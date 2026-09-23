# -*- coding: utf-8 -*-
r"""恐怖黎明 .arz 数据库 —— **记录字段级**读取器（2026-09-17 新增）

为什么需要它
-------------
现有 `gd_arz.Arz` 只能读到「记录名 / 模板名」，**读不到字段值**。
而配装算法要用的东西全在字段里：
  · 装备的真实数值（`itemLevel` / `defensiveProtection` / `strengthRequirement` …）
  · 技能逐级数值（`skillCooldownTime` / `skillActiveDuration` / `offensivePhysicalMin` …）
  · 星座节点的亲和力与授予技能
之前为了绕开这一点，只能从 GrimTools 的 `itemdb.js` 拿"部分字段"，
而 GT 恰好**不收录装备需求值**（实测 7133 件里只有 5 件带 `strengthRequirement`），
导致「属性点分配后能不能穿上装备」长期无法验证。

格式（2026-09-17 实测跑通，参考社区规范 + 本机逐字段验证）
--------------------------------------------------------
```
.arz 布局:  [HDR 24B][Record Data(LZ4)][Record Table][String Table][Footer 16B]
HDR      :  int16 version, int32 RT_off, int32 RT_size, int32 RT_count,
            int32 ST_off, int32 ST_size
RT 条目   :  u32 nameSTidx | CString 模板名 | u32 RD偏移 | u32 RD压缩大小
             | u32 RD解压大小 | u32 | u32        （CString = [u32 len][bytes]）
ST        :  u32 字符串数, 之后 CString 序列（**不以 \0 结尾**）
RD(解压后):  重复  [u16 type][u16 count][u32 字段名STidx][count 个 u32]
type      :  0=Int32  1=Float32  2=String(值是 STidx)  3=Bool
```
关键点（踩过的坑）：
  · **RD 是 LZ4 压缩的** —— 直接扫字节永远找不到裸数值，必须先解压
  · RD 偏移是**相对数据区起点（=24）**的，不是绝对文件偏移
  · 字段名是 ST **索引**，不是字节偏移

实测样例：`records/items/gearlegs/d301_legs.dbr` → 663 字段，`itemLevel=50`、
`levelRequirement=50`、`itemClassification=Legendary`（与 GrimTools 页面一致）。

用法
----
```python
import gd_dbr as DB
db = DB.open_all()                      # 打开 base/gdx1/gdx2/gdx3 并合并索引
f  = db.fields('records/items/gearlegs/d301_legs.dbr')   # {字段名: [值...]}
print(f['itemLevel'], f['itemClassification'])
print(db.field('records/skills/playerclass10/werewolf1.dbr', 'grantedSkills'))
```

依赖：`lz4`（托管 venv 已装）。缺失时本模块会给出明确提示而不是静默失败。
"""
import os
import struct

__all__ = ['ArzDb', 'open_all', 'DBError']

try:
    import lz4.block as _lz4
except ImportError:                                          # pragma: no cover
    _lz4 = None

HDR = 24
DATA0 = 24                     # Record Data 区起点

TYPE_INT, TYPE_FLOAT, TYPE_STRING, TYPE_BOOL = 0, 1, 2, 3


class DBError(Exception):
    pass


class ArzDb:
    """单个 .arz 文件：记录名 → 字段字典。"""

    def __init__(self, path, tag=None):
        if _lz4 is None:
            raise DBError('需要 lz4 库：python -m pip install lz4')
        self.path = path
        self.tag = tag or os.path.splitext(os.path.basename(path))[0]
        with open(path, 'rb') as fh:
            self.data = fh.read()
        d = self.data
        if len(d) < HDR:
            raise DBError('文件过小: %s' % path)
        self.version = struct.unpack_from('<hh', d, 0)
        self.rt_off, self.rt_size, self.rt_count = struct.unpack_from('<iii', d, 4)
        self.st_off, self.st_size = struct.unpack_from('<ii', d, 16)
        self._load_strings()
        self._load_records()

    # ---------------------------------------------------------------- 基础索引
    def _load_strings(self):
        d, strs = self.data, []
        o = self.st_off + 4
        end = self.st_off + self.st_size
        while o < end - 4:
            ln = struct.unpack_from('<i', d, o)[0]
            if not (0 <= ln <= 65536):
                break
            strs.append(d[o + 4:o + 4 + ln].decode('latin1'))
            o += 4 + ln
        self.strings = strs

    def _load_records(self):
        d, rows = self.data, {}
        o = self.rt_off
        while o + 28 <= self.st_off:
            name_idx, ln = struct.unpack_from('<ii', d, o)
            if not (0 < ln <= 200) or not (0 <= name_idx < len(self.strings)):
                o += 1
                continue
            raw = d[o + 8:o + 8 + ln]
            if not all(32 <= c < 127 for c in raw):     # 模板名必须是 ASCII
                o += 1
                continue
            off, csize, usize, _t1, _t2 = struct.unpack_from('<5i', d, o + 8 + ln)
            rows[self.strings[name_idx]] = (raw.decode('latin1'), off, csize, usize)
            o += 28 + ln
        self.records = rows

    # ---------------------------------------------------------------- 字段读取
    def record_data(self, record):
        """解压某条记录的数据段 → bytes"""
        ent = self.records.get(record)
        if not ent:
            return None
        _tmpl, off, csize, usize = ent
        raw = self.data[DATA0 + off: DATA0 + off + csize]
        if usize == 0:
            return b''
        try:
            return _lz4.decompress(raw, uncompressed_size=usize)
        except Exception:
            # 少数记录未压缩（csize == usize）
            if csize == usize:
                return raw
            raise

    def fields(self, record):
        """{字段名: [值...]}；值按类型转为 int/float/str/bool"""
        dec = self.record_data(record)
        if dec is None:
            return None
        strs = self.strings
        out = {}
        p, n = 0, len(dec)
        while p + 8 <= n:
            ftype, count = struct.unpack_from('<HH', dec, p)
            p += 4
            nidx, = struct.unpack_from('<I', dec, p)
            p += 4
            need = 4 * count
            if p + need > n:
                break
            raws = struct.unpack_from('<%dI' % count, dec, p)
            p += need
            name = strs[nidx] if 0 <= nidx < len(strs) else '?%d' % nidx
            if ftype == TYPE_FLOAT:
                vals = list(struct.unpack('<%df' % count, struct.pack('<%dI' % count, *raws)))
            elif ftype == TYPE_STRING:
                vals = [strs[v] if 0 <= v < len(strs) else '?%d' % v for v in raws]
            elif ftype == TYPE_BOOL:
                vals = [bool(v) for v in raws]
            else:
                vals = list(struct.unpack('<%di' % count, struct.pack('<%dI' % count, *raws)))
            out[name] = vals
        return out

    def field(self, record, name, default=None, idx=0):
        f = self.fields(record)
        if not f or name not in f:
            return default
        v = f[name]
        return v[idx] if idx < len(v) else default

    def has(self, record):
        return record in self.records


class Db:
    """多库合并视图（base + gdx1/2/3），后加载的库优先（资料片覆盖基础版）。"""

    def __init__(self, dbs):
        self.dbs = dbs
        self._index = {}
        for db in dbs:
            for rec in db.records:
                self._index[rec] = db                     # 后写覆盖 = 资料片优先

    def resolve(self, record):
        return self._index.get(record)

    def fields(self, record):
        db = self._index.get(record)
        return db.fields(record) if db else None

    def field(self, record, name, default=None, idx=0):
        db = self._index.get(record)
        return db.field(record, name, default, idx) if db else default

    def template(self, record):
        """记录使用的模板名（如 Skill_Shapeshift / ArmorProtective_Legs）"""
        db = self._index.get(record)
        if not db:
            return None
        ent = db.records.get(record)
        return ent[0] if ent else None

    def has(self, record):
        return record in self._index

    def __len__(self):
        return len(self._index)

    def records_like(self, prefix):
        return [r for r in self._index if r.startswith(prefix)]


_CACHE = None


def open_all(arz_paths=None, use_cache=True):
    """打开全部 .arz 并合并。arz_paths 为空则用 gd_env 探测结果。"""
    global _CACHE
    if _CACHE is not None and arz_paths is None:
        return _CACHE
    if arz_paths is None:
        try:
            import gd_env as ENV
            arz_paths = ENV.find_arz()
        except Exception:
            arz_paths = []
    dbs = []
    for tag, path in arz_paths:
        if not os.path.exists(path):
            continue
        try:
            dbs.append(ArzDb(path, tag))
        except Exception as e:
            print('⚠ 读取 %s 失败: %s' % (path, e))
    obj = Db(dbs)
    if arz_paths is None and use_cache:
        _CACHE = obj
    return obj


if __name__ == '__main__':
    import sys
    db = open_all()
    print('已加载 %d 个库，共 %d 条记录' % (len(db.dbs), len(db)))
    for rec in sys.argv[1:]:
        f = db.fields(rec)
        if not f:
            print('✗ 未找到 %s' % rec)
            continue
        print('=== %s（%d 字段）' % (rec, len(f)))
        for k in sorted(f)[:40]:
            v = f[k]
            print('   %-42s %s' % (k, v[:3]))
