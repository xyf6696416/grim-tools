# -*- coding: utf-8 -*-
"""恐怖黎明 database.arz 只读解析器（已实测校准）

格式（database.arz v2/v3，GD 1.3.0.0）：
    偏移 0   : int16 主版本(2) + int16 次版本(3)
    偏移 4   : int32 记录表偏移       (53233776)
    偏移 8   : int32 记录表长度       (1342639)
    偏移 12  : int32 记录条数         (34182)
    偏移 16  : int32 字符串池偏移     (54576415)
    偏移 20  : int32 字符串池长度     (3970335)

记录表每条（**实测校准，勿用旧布局**）：
    [D:int32 该记录名字在字符串池中的下标][名字长度:int32][名字][20 字节尾]
    20 字节尾 = [数据偏移:int32][B:int32][C:int32][filetime:8]
    循环步长 = 28 + 名字长度，从记录表偏移一路走到字符串池偏移，恰好精确走完。
    （base 27837 条 / GDX3 21113 条；头部声明的 34182 是旧口径，别拿来循环）
    ⚠ 旧文档里写的 `[A][B][C][filetime][D][tmplLen][tmpl]` 是错的，会丢 ~18% 条目。

字符串池： 偏移+4 起 [int32 长度][字节] 平铺，共 82843 条，走完恰好到文件尾。
    第 0 条是总条数（int32 本身）。

关键推论：
- 尾部的「数据偏移」在整张表上严格单调递增、范围落在 [0, 记录表偏移)，
  所以第 i 条记录的二进制数据 = raw[off_i : off_{i+1}]。
- 字符串池是按记录顺序【追加且去重】写入的，因此某记录的字符串块不连续：
  它 = 自己的名字 + 它【首次引入】的字符串。除了"第一个使用该字符串的记录"，
  其余无法靠邻近关系反推宿主（位图被技能/特效记录大量共用，反查会得到无关记录）。
"""
import re
import struct

DEFAULT_ARZ = r'E:\SteamLibrary\steamapps\common\Grim Dawn\database\database.arz'


class Arz:
    def __init__(self, path=DEFAULT_ARZ):
        self.data = open(path, 'rb').read()
        d = self.data
        self.version = struct.unpack_from('<hh', d, 0)
        self.rec_off, self.rec_size, self.rec_count = struct.unpack_from('<iii', d, 4)
        self.str_off, self.str_size = struct.unpack_from('<ii', d, 16)
        self._load_strings()
        self._load_records()

    # ------------------------------------------------------------ 字符串池
    def _load_strings(self):
        d, o, end = self.data, self.str_off + 4, self.str_off + self.str_size
        strs = []
        while o < end - 4:
            ln = struct.unpack_from('<i', d, o)[0]
            if not (0 <= ln <= 65536):
                break
            strs.append(d[o + 4:o + 4 + ln])
            o += 4 + ln
        self.strings = strs
        self._ALL_DBR = None
        self._ALL_DBR_SET = None
        self._str_index = {}
        for i, s in enumerate(strs):
            self._str_index.setdefault(s, i)
        self.str_walk_end = o

    # ------------------------------------------------------------ 记录表
    def _load_records(self):
        """记录表布局（实测）：

            [D:int32 名字在字符串池中的下标][名字长度:int32][名字][20 字节尾]

        20 字节尾 = [A:int32][B:int32][C:int32][filetime:8]。
        从表偏移起按此步长推进，恰好精确走完整个表（database.arz 27837 条 / GDX3 21112 条），
        且 D 递增 —— 即表顺序 == 字符串池顺序 == 数据库顺序。
        """
        d, end = self.data, self.str_off
        o, rows = self.rec_off, []
        nstr = len(self.strings)
        while o + 28 <= end:
            D, ln = struct.unpack_from('<ii', d, o)
            if not (0 < ln <= 200) or not (0 <= D < nstr):
                o += 1
                continue
            nm = d[o + 8:o + 8 + ln]
            if not all(32 <= c < 127 for c in nm):
                o += 1
                continue
            rows.append({'off': o, 'name_index': D,
                         'name': self.strings[D].decode('latin1'),
                         'tmpl': nm.decode('latin1'),
                         'trailer': struct.unpack_from('<5i', d, o + 8 + ln)[:3]})
            o += 28 + ln
        self.records = rows
        self._rec_by_name = {r['name']: r for r in rows}

    # ------------------------------------------------------------ 查询
    def owner_of(self, value):
        """返回包含该字符串的那条记录（记录名）"""
        b = value.encode() if isinstance(value, str) else value
        i = self._str_index.get(b)
        if i is None:
            return None
        lo, hi, best = 0, len(self.records) - 1, None
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.records[mid]['name_index'] <= i:
                best = self.records[mid]
                lo = mid + 1
            else:
                hi = mid - 1
        return best['name'] if best else None

    # ------------------------------------------------------------ 位图 -> 记录
    def _pool_names(self):
        if self._ALL_DBR is None:
            self._ALL_DBR = [s.decode('latin1') for s in self.strings
                             if s.startswith(b'records/') and s.endswith(b'.dbr')]
            self._ALL_DBR_SET = set(self._ALL_DBR)
        return self._ALL_DBR

    def record_for_bitmap(self, bitmap):
        """GT 位图路径 -> 物品记录路径。

        GT 把 .tex 显示成 .png；GD 的物品记录与位图同目录同基名，
        区别只在于记录可能带一个变体字母（b012_sword -> b012a_sword）。
        """
        p = bitmap
        if p.endswith('.png') or p.endswith('.tex'):
            p = p[:-4]
        p = p.replace('/bitmaps/', '/')
        d, base = p.rsplit('/', 1)
        self._pool_names()
        pool = self._ALL_DBR_SET
        cands = ['records/%s/%s.dbr' % (d, base)]
        m = re.fullmatch(r'([a-z]+[0-9]+[a-z]?)_(.+)', base)
        if m:
            for L in ('a', 'b', 'c', 'd', ''):
                cands.append('records/%s/%s%s_%s.dbr' % (d, m.group(1), L, m.group(2)))
        m2 = re.fullmatch(r'([a-z]+[0-9]+)([a-z])', base)
        for name in cands:
            if name in pool:
                return name
        # 兜底：同目录下基名最接近的记录
        pref = 'records/%s/%s' % (d, base.split('_')[0])
        near = [n for n in pool if n.startswith(pref)]
        return near[0] if len(near) == 1 else None

    def strings_of(self, record_name):
        """某记录字符串块内的全部字符串"""
        r = self._rec_by_name.get(record_name)
        if not r:
            return []
        i = r['name_index']
        nxt = None
        for q in self.records:
            if q['name_index'] > i:
                nxt = q['name_index']
                break
        return [s.decode('latin1') for s in self.strings[i + 1: nxt or len(self.strings)]]

    def stats_like(self, record_name):
        """从字符串块里挑出形如数值的项（GD 把 float 也写进字符串池）"""
        out = []
        for s in self.strings_of(record_name):
            try:
                out.append(float(s))
            except ValueError:
                pass
        return out

    def names_under(self, prefix):
        return [n for n in self._pool_names() if n.startswith(prefix)]

    def find(self, name):
        return self._rec_by_name.get(name)

    # ------------------------------------------------------------ 词缀家族
    def affix_family(self, group, family):
        """按字符串池顺序（= 数据库顺序）列出某词缀家族的全部记录"""
        pat = re.compile(rb'records/items/lootaffixes/' + group.encode() + rb'/' +
                         family.encode() + rb'[A-Za-z0-9_%.\-]*\.dbr')
        seen = []
        for s in self.strings:
            m = pat.match(s)
            if m:
                n = m.group().decode('latin1')
                if n not in seen:
                    seen.append(n)
        return seen

    def affix_desc(self, record):
        """词缀记录里的可读描述（GD 里叫 lootRandomizerName 之类）"""
        out = []
        for s in self.strings_of(record):
            if s.startswith('tag'):
                out.append('TAG:' + s)
            elif s and not re.fullmatch(r'-?\d+(\.\d+)?', s):
                out.append(s)
        return out


if __name__ == '__main__':
    a = Arz()
    print('版本 %s  记录数(声明) %d  解析出 %d' % (a.version, a.rec_count, len(a.records)))
    print('字符串 %d 条，走完 %d / %d' % (len(a.strings), a.str_walk_end,
                                         a.str_off + a.str_size))
    print()
    print('位图 -> 记录 测试:')
    for v in ['items/gearweapons/swords1h/bitmaps/b012_sword.tex',
              'items/gearhead/bitmaps/a07_head001.tex',
              'items/gearaccessories/rings/bitmaps/a003_ring03.tex']:
        print("   %-52s -> %s" % (v, a.record_for_bitmap(v)))
    print()
    for fam, grp in (('ao003', 'prefix'), ('a014', 'suffix')):
        names = a.affix_family(grp, fam)
        print('%s 家族 %d 条:' % (fam, len(names)))
        for n in names[:4] + names[-2:]:
            print('    %-64s %s' % (n, a.affix_desc(n)))
