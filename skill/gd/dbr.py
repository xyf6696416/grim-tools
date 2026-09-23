"""`gd_dbr` 的兼容层 —— 字段级记录读取，数据源换成离线库。

旧实现要解压 `.arz` 的 RD 段（LZ4），所以**必须装 lz4**，缺了会静默算错。
新架构下同一份数据本来就在离线库里（8612 物品 + 4105 词缀 + 4268 物品技能 +
356 专精技能 + 716 星座节点），所以这里做成语义一致的 shim，
让上层的 DPS / 优化器 / 需求建模**一行都不用改**。

接口（与旧实现一致）：
    DB.open_all()                    -> Db
    db.field(record, name, default, idx)   -> 值（列表则取 idx）
    db.fields(record)                -> {字段: 值}
    db.template(record)              -> 模板名
    db.has(record)                   -> bool
    db.resolve(record)               -> 记录条目

数据来源优先级：
    1) `.dbr` 记录路径 -> GT id（data/record_map.json，79.1% 高置信）
    2) 专精技能（data/mastery_skills.json，按 tag 对齐）
    3) 星座节点（data/devotion_skills.json）
    4) 技能全库（data/skills.json，12372 条，含逐级数值）
    5) **补洞**（data/calc_mastery_skills.json，从 asar 的 `calc.js` 回填上面缺的字段，
       如 `skillChanceWeight` / `conversion*`；`setdefault` 只增不改）
"""

from __future__ import annotations

import json
from typing import Any, Optional

from . import paths

_CACHE: Optional["Db"] = None
_NONE = object()          # `Db.fields()` 缓存的「未命中」哨兵（区别于「算出来是 None」）


class _Entry:
    """一条记录：字段字典 + 模板名。"""

    __slots__ = ("record", "fields", "template")

    def __init__(self, record: str, fields: dict, template: Optional[str] = None):
        self.record = record
        self.fields = fields
        self.template = template


class Db:
    """记录 -> 字段 的只读视图。"""

    def __init__(self):
        self._idx: dict[str, _Entry] = {}
        self._loaded = False
        # ★ `fields()` 的结果缓存（2026-09-20）。见 `fields()` 的说明：
        #   优化器每次评估要调 236 次，而记录集在搜索期间**完全不变**。
        self._fields_memo: dict = {}

    # ------------------------------------------------------------ 惰性装载
    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        from . import DB
        db = DB.load()

        # 1) 物品 / 词缀：走记录桥
        try:
            from .save import items as _it
            br = _it.bridge(db)
            idx = self._idx
            for gid, rec in br.fwd.items():
                o = db.get(gid)
                if o:
                    idx[rec] = _Entry(rec, dict(o))
            for gid, rec in br.affix_fwd.items():
                o = db.affix(gid)
                if o:
                    idx.setdefault(rec, _Entry(rec, dict(o)))
        except Exception:
            pass

        # 2) 专精技能（按 tag 桥接）
        for tag, rec in db.mastery_skills.items():
            rp = rec.get("record")
            if rp:
                stats = rec.get("stats") or {}
                self._idx[rp] = _Entry(rp, dict(stats), rec.get("template"))

        # 3) 星座节点
        for rp, rec in db.devotion_skills.items():
            self._idx[rp] = _Entry(rp, dict(rec.get("stats") or {}),
                                   rec.get("template"))

        # 4) 技能全库（含逐级数值）
        #
        # ★★ 2026-09-20：这里**必须能"补字段"**，不能只判 `rp not in self._idx`。
        #    第 3 步的 `devotion_skills.json` 是一份**只有名字、没有任何数值**的索引
        #    （实测 716 条里带 `stats` 的 **0 条**），它先占住了槽位 ⇒ 第 4 步整批跳过
        #    ⇒ **701 条星座记录的真实数值永远进不了模型**。
        #    后果（实测 Sam lv71）：32 个星座节点全部算出 `pct={} flat={} spd=0 mult=0`
        #    —— 整个星座系统在伤害模型里等于不存在（% 伤害、OA/DA、攻速、生命、
        #    以及**天神之力**如「刀锋之怒」「暗杀者的标记」全部白丢）。
        #    这是「只补新记录、不补字段」这一类坑的又一处：与 `skillChanceWeight`
        #    被名字白名单丢掉同源（都是"索引占位但数值缺失"）。
        #    补法用 `setdefault`：**只增不改** ⇒ 第 1/2 步已有的权威值一个都不动。
        raw = paths.load_json("skills.json")
        if raw:
            for rp, rec in raw.items():
                if not isinstance(rec, dict):
                    continue
                st = dict(rec.get("stats") or {})
                e = self._idx.get(rp)
                if e is None:
                    self._idx[rp] = _Entry(rp, st, rec.get("template"))
                    continue
                if st:
                    for k, v in st.items():
                        e.fields.setdefault(k, v)
                    if not e.template:
                        e.template = rec.get("template")

        # 5) ★★ 补洞：从 `calc.js` 回填 `skills.json` **缺失**的字段（只增不改）。
        #
        #    `data/skills.json` 是旧 `.arz` 抽取的产物，它的 `KEEP_PREFIX` 名字白名单
        #    只留 `offensive*/defensive*/skillCooldownTime/...` ⇒ **`skillChanceWeight`
        #    被整库丢掉了（12372 条一条不剩）**，`conversionInType/OutType/Percentage`
        #    这类技能级转化也一并丢失。
        #    后果实测（2026-09-20）：武器池技能（WPS）在 `gd/rotation.py` 里被误判成
        #    **默认攻击候选** —— 「雪崩」（权重 12→30，全场最高）与「猛袭」抢左键槽，
        #    并因平均每击更低而被顶掉；夜刃「处决 / 瞬影 / 切割」同样掉进 swing。
        #
        #    真源是 asar 的 `/dist/calc/calc.js`（GT 计算器面板自己用的表），
        #    产物 `data/calc_mastery_skills.json` 由 `tools/extract_calc_skills.py` 生成，
        #    **只含缺失字段**，所以这里用 `setdefault` 不会改掉任何既有权威值。
        gap = paths.load_json("calc_mastery_skills.json")
        if gap:
            for rp, extra in gap.items():
                e = self._idx.get(rp)
                if not e or not isinstance(extra, dict):
                    continue
                for k, v in extra.items():
                    e.fields.setdefault(k, v)

    # ------------------------------------------------------------ 查询
    def resolve(self, record: str) -> Optional[_Entry]:
        self._load()
        return self._idx.get(record) or self._idx.get((record or "").replace("\\", "/"))

    def has(self, record: str) -> bool:
        return self.resolve(record) is not None

    def fields(self, record: str) -> Optional[dict]:
        """★ 返回**全部值都包成 list** 的字段字典 —— 与旧 .dbr 读取器语义一致。

        旧实现读的是 `.dbr` 的 RD 段，每个字段天然是「值数组」，所以上层到处
        写 `lv[0]` / `n[0]`。离线库存的是标量，这里统一包成单元素列表，
        让上层一行都不用改。

        ★★ 缓存（2026-09-20）：本函数的**唯一**成本就是「包 list」这个循环
          （记录多则每字段一次 `isinstance` + 一次 `[v]`）。实测优化器单次 DPS
          评估调它 **236 次**、`isinstance` 85 万次 —— 而搜索期间记录集不变，
          `_load()` 之后再无写入 ⇒ 结果天然可缓存。
          · 审计过 69 个调用点，**无一处修改返回值**（只读）⇒ 可安全共享同一 dict。
          · 2026-09-20 实测：单次评估 2.440 → 1.971 ms（**1.24×**；480 次评估里
            命中 9632 次 / 未命中 44 条唯一记录）。
          ⚠ 调用方**不得修改**返回的 dict（会污染所有后续调用）。
        """
        _v = self._fields_memo.get(record, _NONE)
        if _v is not _NONE:
            return _v
        e = self.resolve(record)
        if not e:
            self._fields_memo[record] = None
            return None
        out = {}
        for k, v in e.fields.items():
            out[k] = list(v) if isinstance(v, (list, tuple)) else [v]
        if e.template:
            out.setdefault("templateName", [e.template])
        self._fields_memo[record] = out
        return out

    def field(self, record: str, name: str, default: Any = None, idx: int = 0) -> Any:
        f = self.fields(record)
        if not f or name not in f:
            return default
        v = f[name]
        return v[idx] if idx < len(v) else default

    def template(self, record: str) -> Optional[str]:
        e = self.resolve(record)
        return e.template if e else None

    def records_like(self, prefix: str) -> list[str]:
        self._load()
        return [r for r in self._idx if r.startswith(prefix)]

    def __len__(self):
        self._load()
        return len(self._idx)


def open_all(arz_paths=None, use_cache=True) -> Db:
    """兼容旧签名。`arz_paths` 已忽略 —— 新架构不需要 .arz。"""
    global _CACHE
    if _CACHE is None:
        _CACHE = Db()
        _CACHE._load()
    return _CACHE


class DBError(Exception):
    pass
