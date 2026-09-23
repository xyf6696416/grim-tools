"""存档 ↔ 离线库桥接层。

存档里的每件物品存的是**记录路径字符串**（`basename` / `prefix` / `suffix` /
`relic_name` / `augment_name`），而离线库按 `itXXXX` 索引。这一层负责两边互查：

    record -> gid     走随技能发布的 data/record_map.json（高置信结果，79.1%）
    gid -> record     同一张表的正查
    词缀中文名         记录基名 -> 族根 -> `tagPrefix/Suffix<族根>` -> 语言包（62.7%）

置信度是要紧的：低置信（多档数不吻合）的映射**不放进反向表**，
宁可显示记录名，也不能把物品认错 —— 认错会导致 DPS/改档全盘偏。
"""

from __future__ import annotations

import json
import re
from typing import Optional

from .. import paths

__all__ = ["RecordBridge", "bridge"]

_NUM_RE = re.compile(r"(\d+)")


class RecordBridge:
    """记录路径 ↔ GT id 的双向桥。惰性加载 record_map.json。"""

    def __init__(self, db=None, table: Optional[dict] = None):
        self._db = db
        raw = table if table is not None else (paths.load_json("record_map.json") or {})
        self._raw = raw
        self.fwd: dict[str, str] = raw.get("fwd") or {}       # gid -> record
        self.rev: dict[str, str] = raw.get("rev") or {}       # record -> gid
        self.low: dict[str, list] = raw.get("low") or {}
        self.affix_fwd: dict[str, str] = raw.get("affix_fwd") or {}
        self.affix_rev: dict[str, str] = raw.get("affix_rev") or {}
        self._stem_tag = None
        self._name_cache = {}

    # ------------------------------------------------------------ DB
    @property
    def db(self):
        if self._db is None:
            from .. import DB
            self._db = DB.load()
        return self._db

    # ------------------------------------------------------------ 正反查
    def record_of(self, gid: str) -> Optional[str]:
        return self.fwd.get(gid)

    def gid_of(self, record: str) -> Optional[str]:
        """记录路径 -> GT id。物品表优先，词缀表兜底。"""
        if not record:
            return None
        if record in self.rev:
            return self.rev[record]
        if record in self.affix_rev:
            return self.affix_rev[record]
        # 运行时可能有斜杠方向差异，归一化后再试一次
        r = record.replace("\\", "/").lower()
        for tbl in (self.rev, self.affix_rev):
            for k, v in tbl.items():
                if k.lower() == r:
                    return v
        return None

    def any_record_of(self, gid: str) -> Optional[str]:
        """GT id -> 记录路径（物品表或词缀表）。"""
        return self.fwd.get(gid) or self.affix_fwd.get(gid)

    def item(self, record: str) -> Optional[dict]:
        gid = self.gid_of(record)
        return self.db.items.get(gid) if gid else None

    # ------------------------------------------------------------ 名称
    def _stem_tags(self) -> dict:
        """{(组, 族根): tag} —— 从离线库的 `c` 字段反推，用于给词缀记录找中文名。"""
        if self._stem_tag is None:
            from .. import savemap as M
            db = self.db
            out = {"prefix": {}, "suffix": {}}
            for grp, table in (("prefix", db.prefixes), ("suffix", db.suffixes)):
                for o in table.values():
                    tag = o.get("c")
                    if not isinstance(tag, str):
                        continue
                    stem = M._affix_stem(tag)
                    if stem:
                        out[grp].setdefault(stem, tag)
            self._stem_tag = out
        return self._stem_tag

    def affix_tag(self, record: str) -> Optional[str]:
        """词缀记录 -> 名称 tag（按最长可用族根前缀匹配）。"""
        if not record:
            return None
        grp = "suffix" if "/suffix/" in record else ("prefix" if "/prefix/" in record else None)
        if grp is None:
            return None
        base = record.split("/")[-1]
        base = base[:-4] if base.endswith(".dbr") else base
        tbl = self._stem_tags()[grp]
        for i in range(len(base), 0, -1):
            if base[:i] in tbl:
                return tbl[base[:i]]
        return None

    def label(self, record: str, lang: Optional[str] = None) -> str:
        """任意记录路径 -> 尽量可读的中文标签。

        顺序：离线库物品名 -> 词缀族根名 -> 记录基名（兜底，绝不丢东西）
        """
        if not record:
            return ""
        key = (record, lang)
        if key in self._name_cache:
            return self._name_cache[key]
        out = None
        gid = self.gid_of(record)
        if gid:
            sp = self.db.get(gid)
            if gid in self.db.sets:
                out = self.db.name(gid, lang)
            elif sp is not None:
                tag = sp.get("a") or sp.get("name") or sp.get("skillDisplayName")
                out = self.db.l10n.get(tag, lang, default=gid) if tag else self.db.name(gid, lang)
        if not out:
            tag = self.affix_tag(record)
            if tag:
                v = self.db.l10n.get(tag, lang, default=None)
                if v and v != tag:
                    out = v
        if not out:
            base = record.split("/")[-1]
            out = base[:-4] if base.endswith(".dbr") else base
        self._name_cache[key] = out
        return out

    def affix_name(self, record: str, lang: Optional[str] = None) -> Optional[str]:
        """只取词缀名（拿不到返回 None，调用方决定怎么兜底）。"""
        tag = self.affix_tag(record)
        if not tag:
            return None
        v = self.db.l10n.get(tag, lang, default=None)
        return v if v and v != tag else None

    # ------------------------------------------------------------ 统计
    def stats(self) -> dict:
        return {
            "正向（物品）": len(self.fwd),
            "反向（记录）": len(self.rev),
            "低置信（未收录）": len(self.low),
            "词缀正向": len(self.affix_fwd),
            "词缀反向": len(self.affix_rev),
            "构建时间": (self._raw.get("__provenance__") or {}).get("built", "?"),
        }


_BRIDGE: Optional[RecordBridge] = None


def bridge(db=None, reload: bool = False) -> RecordBridge:
    """进程内单例。"""
    global _BRIDGE
    if _BRIDGE is None or reload:
        _BRIDGE = RecordBridge(db)
    return _BRIDGE
