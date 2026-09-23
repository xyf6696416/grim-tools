"""渲染层：把离线库的原始字段渲染成可读的中文条目。

所有数值都走 `Scaler`（官方缩放口径）+ `GTFormat`（官方格式引擎），
所以输出与游戏提示框 **逐字一致**（去色后）。
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

QUALITY_CN = {"Common": "普通", "Magical": "魔法", "Rare": "稀有", "Epic": "史诗",
              "Legendary": "传说", "Quest": "任务", "Unknown": "未知"}

# 不参与展示的结构性字段（shortNameMapping 里的短键 + 元数据）
SKIP = {
    "a", "b", "c", "d", "e", "f", "g", "h", "i", "k", "l", "m", "n", "o", "p",
    "id", "mods", "cls", "MI", "uniqueRare", "base", "itemLevel", "itemQualityTag",
    "itemStyleTag", "maxStackSize", "forcedRelicCompletion", "fromBlacksmith",
    "onlyForItems", "petBonusName", "skillName", "skillDisplayName", "skillDownBitmapName",
    "skillUpBitmapName", "skillBaseDescription", "templateName", "droppable", "maxAffixes",
    "onMap", "type", "tag", "name", "diff", "setMembers", "blacklisted",
}
PREFIX_SKIP = ("reagent", "crafting", "conversionInType", "conversionOutType",
               "conversionPercentage", "augmentSkillName", "augmentSkillLevel",
               "modifiedSkillName", "modifierSkillName", "augmentMasteryName",
               "augmentMasteryLevel", "itemSkill", "racialBonus")

_GROUP = [
    (re.compile(r"^(offensiveBase|offensivePierceRatio|defensiveProtection|"
                r"defensiveBlock|characterBaseAttackSpeed)"), 1),          # 基础
    (re.compile(r"^offensive(?!Slow|Base)[A-Za-z]+(Min|Max)$"), 2),         # 平伤
    (re.compile(r"^retaliation[A-Za-z]+(Min|Max)$"), 2),
    (re.compile(r"^offensiveSlow[A-Za-z]+(Min|Max)$"), 3),                  # DoT 平伤
    (re.compile(r"^(offensive[A-Za-z]+Modifier|offensiveTotalDamageModifier|"
                r"offensiveCritDamageModifier)$"), 4),                      # % 加成
    (re.compile(r"^offensiveSlow[A-Za-z]+Modifier$"), 5),                   # DoT %
    (re.compile(r"^conversion"), 6),
    (re.compile(r"^character"), 7),                                         # 属性
    (re.compile(r"^defensive"), 8),                                         # 抗性/防御
    (re.compile(r"^(skill|refresh|onHit|lifeMonitor|chance|retaliation)"), 9),
]


def _group(field: str) -> int:
    for rx, g in _GROUP:
        if rx.match(field):
            return g
    return 10


class Renderer:
    def __init__(self, db):
        self.db = db
        self.fmt = db.fmt
        self.scaler = db.scaler
        self.zh = db.l10n.texts.get(db.lang) or {}

    # ---------------------------------------------------------------- 单值
    def label(self, field: str) -> Optional[str]:
        return self.db.field_tag(field)

    def _scale_ctx(self, obj: dict):
        return float(obj.get("h") or 0), (obj.get("i") if obj.get("i") is not None else None)

    def line(self, field: str, value: Any, obj: dict, show_range: bool = False) -> Optional[str]:
        """单个字段 -> 一行中文（含官方缩放）。"""
        tag = self.label(field)
        if tag is None:
            return None
        scale, jitter = self._scale_ctx(obj)
        # Min/Max 成对：由 Min 侧统一输出一条 "lo-hi"
        m = re.match(r"^(.+)(Min|Max)$", field)
        if m and isinstance(value, (int, float)):
            base, side = m.group(1), m.group(2)
            other = obj.get(base + ("Max" if side == "Min" else "Min"))
            if isinstance(other, (int, float)):
                if side == "Max":
                    return None
                lo = self.scaler.value(field, value, scale, jitter)
                hi = self.scaler.value(base + "Max", other, scale, jitter)
                txt = self.fmt.value_label(tag, self.fmt.pair_str(tag, lo, hi))
                if show_range:
                    # 区间取「整个伤害的最低可能 ~ 最高可能」
                    r_lo = self.scaler.range(field, value, scale, jitter)
                    r_hi = self.scaler.range(base + "Max", other, scale, jitter)
                    if r_lo and r_hi:
                        txt += f"  [{r_lo[0]}-{r_hi[1]}]"
                return txt
        val = self.scaler.value(field, value, scale, jitter)
        txt = self.fmt.value_label(tag, val)
        if show_range:
            rng = self.scaler.range(field, value, scale, jitter)
            if rng and rng[0] != rng[1]:
                txt += f"  [{rng[0]}-{rng[1]}]"
        return txt

    @staticmethod
    def _pair(lo: Any, hi: Any) -> str:
        if isinstance(lo, float) and lo.is_integer():
            lo = int(lo)
        if isinstance(hi, float) and hi.is_integer():
            hi = int(hi)
        return str(lo) if lo == hi else f"{lo}-{hi}"

    # ---------------------------------------------------------------- 物品
    def item_lines(self, gid: str, show_range: bool = False) -> list[str]:
        obj = self.db.items.get(gid)
        if obj is None:
            return []
        out: list[tuple[int, str]] = []
        scale, jitter = self._scale_ctx(obj)
        for field, value in obj.items():
            if field in SKIP or field.startswith(PREFIX_SKIP):
                continue
            if value is None or value == 0 or value == []:
                continue
            if isinstance(value, (dict, list)) and not isinstance(value, (int, float)):
                continue
            txt = self.line(field, value, obj, show_range)
            if txt:
                out.append((_group(field), txt))
        out.sort(key=lambda x: x[0])

        lines = [t for _, t in out]
        # 等级需求（GT 的 LevelRequirement 标签不带占位符，单独拼）
        lvl = obj.get("k")
        if lvl:
            lines.insert(0, self.fmt.value_label("LevelRequirement", lvl))
        return lines

    def item_card(self, gid: str, show_range: bool = False, desc: bool = True) -> str:
        obj = self.db.items.get(gid)
        if obj is None:
            return f"✗ 找不到物品 {gid}"
        q = QUALITY_CN.get(obj.get("f") or "", obj.get("f") or "")
        cls = self.db.class_cn(self.db.item_class(gid))
        head = f"{self.db.name(gid)}"
        sub = " · ".join(x for x in (q, cls) if x)
        atk = self.attack_speed_label(obj)
        if atk:
            sub += f" · {atk}"
        bars = "─" * 46
        body = self.item_lines(gid, show_range)
        out = [head, sub or "", bars]
        out += body
        # 授予 / 改造技能
        granted = self.granted_skills(obj)
        if granted:
            out.append(bars)
            out += granted
        if desc:
            d = obj.get("b")
            if d:
                t = self.fmt.render(d)
                if t and t != d and len(t) > 2:
                    out.append(bars)
                    out.append(t)
        out.append(bars)
        out.append(f"{self.db.en_name(gid)}  [{gid}]")
        return "\n".join(out)

    def attack_speed_label(self, obj: dict) -> Optional[str]:
        """武器/护甲的基础攻速档（`o` = characterBaseAttackSpeedTag 的短码）。"""
        code = obj.get("o")
        if not code:
            return None
        rev = {v: k for k, v in self.db.short_names.items()}
        tag = rev.get(code)
        if not tag:
            return f"攻速档 {code}"
        t = self.db.l10n.get(tag)
        return str(t) if t != tag else f"攻速档 {code}"

    def granted_skills(self, obj: dict) -> list[str]:
        out = []
        # 装备自带技能
        sk = obj.get("itemSkillName")
        if sk:
            nm = self.db.skills.get(sk, {}).get("skillDisplayName") or sk
            out.append(f"装备技能：{self.db.l10n.get(nm, default=nm)}")
        # 赋予技能 / 精通加成（紫装核心价值）
        for i in range(1, 11):
            nm = obj.get(f"augmentSkillName{i}")
            lv = obj.get(f"augmentSkillLevel{i}")
            if nm and lv:
                out.append(f"+{lv} 技能等级：{self.db.l10n.get(nm, default=nm)}")
        for i in range(1, 11):
            nm = obj.get(f"augmentMasteryName{i}")
            lv = obj.get(f"augmentMasteryLevel{i}")
            if nm and lv:
                out.append(f"+{lv} 精通等级：{self.db.l10n.get(nm, default=nm)}")
        for i in range(1, 11):
            mod = obj.get(f"modalSkillName{i}") or obj.get(f"modifierSkillName{i}")
            tgt = obj.get(f"modifiedSkillName{i}")
            if mod and tgt:
                out.append(f"改造技能：{self.db.l10n.get(tgt, default=tgt)}")
        return out

    # ---------------------------------------------------------------- 词缀
    def affix_card(self, gid: str) -> str:
        obj = self.db.affix(gid)
        if obj is None:
            return f"✗ 找不到词缀 {gid}"
        kind = "前缀" if gid in self.db.prefixes else "后缀"
        q = QUALITY_CN.get(obj.get("f") or "", obj.get("f") or "")
        cls = obj.get("cls") or []
        rev = {v: k for k, v in self.db.short_names.items()}
        allowed = "、".join(
            self.db.class_cn(rev.get(c, c)) for c in cls[:14]) or "（任意）"
        lines = [f"{self.db.name(gid)}", f"{kind} · {q} · 等级需求 {obj.get('k') or 0}",
                 "─" * 46]
        scale, jitter = self._scale_ctx(obj)
        rows = []
        for field, value in obj.items():
            if field in SKIP or field.startswith(PREFIX_SKIP) or field == "cls":
                continue
            if value is None or value == 0:
                continue
            if isinstance(value, (dict, list)):
                continue
            txt = self.line(field, value, obj)
            if txt:
                rows.append((_group(field), txt))
        rows.sort(key=lambda x: x[0])
        lines += [t for _, t in rows]
        lines.append("─" * 46)
        lines.append(f"可附部位：{allowed}")
        lines.append(f"[{gid}]")
        return "\n".join(lines)

    # ---------------------------------------------------------------- 套装
    def set_card(self, gid: str) -> str:
        obj = self.db.sets.get(gid)
        if obj is None:
            return f"✗ 找不到套装 {gid}"
        members = obj.get("setMembers") or []
        if not isinstance(members, list):
            members = [members]
        lines = [f"【套装】{self.db.name(gid)}", f"共 {len(members)} 件", "─" * 46, "成员："]
        for mid in members:
            lines.append(f"  · {self.db.name(mid)}   ({mid})")
        lines.append("─" * 46)
        # 逐档加成（数组是「该件数时的累计值」，index = 件数 - 1）
        tiers: dict[int, list[str]] = {}
        for field, value in obj.items():
            if not isinstance(value, list) or field == "setMembers":
                continue
            for idx, v in enumerate(value):
                if idx == 0 or v in (None, 0):
                    continue
                pieces = idx + 1
                txt = self.line(field, v, obj)
                if txt:
                    tiers.setdefault(pieces, []).append(txt)
        for pieces in sorted(tiers):
            lines.append(f"  [{pieces} 件]")
            for t in tiers[pieces]:
                lines.append(f"    {t}")
        if obj.get("itemSkillLevelEq") is not None:
            lines.append(f"  （套装还会强化其绑定技能）")
        lines.append("─" * 46)
        lines.append(f"[{gid}]")
        return "\n".join(lines)

    # ---------------------------------------------------------------- 技能
    def skill_card(self, gid: str, levels: int = 6) -> str:
        """技能卡片。专精技能（skXXXX）会带逐级数值表。"""
        rec = self.db.mastery_skill(gid) if gid in self.db.mastery else None
        obj = rec or self.db.skills.get(gid) or self.db.mastery.get(gid)
        if obj is None:
            return f"✗ 找不到技能 {gid}"
        nm = obj.get("skillDisplayName") or obj.get("name") or gid
        if isinstance(obj.get("name"), str) and obj["name"] and not obj.get("skillDisplayName"):
            nm = obj["name"]
        title = str(self.db.l10n.get(nm, default=nm))
        lines = [title, "─" * 46]
        meta = []
        if obj.get("class"):
            meta.append(f"职业 {obj['class']}")
        if obj.get("tier") is not None:
            meta.append(f"tier {obj['tier']}")
        if obj.get("kind"):
            meta.append(f"类型 {obj['kind']}")
        mx = obj.get("max_level") or obj.get("skillMaxLevel")
        if mx:
            meta.append(f"上限 {mx}")
        if obj.get("ultimate_level"):
            meta.append(f"终极 +{obj['ultimate_level']}")
        if meta:
            lines.append(" · ".join(meta))
        if obj.get("templateName") or obj.get("template"):
            lines.append(f"模板：{obj.get('templateName') or obj.get('template')}")
        if obj.get("record"):
            lines.append(f"记录：{obj['record']}")
        if obj.get("damage"):
            lines.append(f"伤害类型：{obj['damage']}")

        stats = obj.get("stats") or {}
        if stats:
            lines.append("─" * 46)
            lines.append(f"逐级数值（前 {levels} 级）：")
            for field, val in stats.items():
                if not isinstance(val, list) or not val:
                    continue
                tag = self.db.field_tag(field)
                label = self.db.l10n.get(tag) if tag else None
                if not label:
                    continue
                head = self.fmt.render(tag).strip().lstrip("+-").strip()
                vals = " / ".join(
                    self._num(v) for v in val[:levels])
                lines.append(f"  {head:<22} {vals}")
            # 非逐级（单值）字段
            single = [(f, v) for f, v in stats.items() if not isinstance(v, list)]
            for field, val in single:
                tag = self.db.field_tag(field)
                if tag and not self.db.l10n.get(tag) == tag:
                    lines.append(f"  {self.fmt.value_label(tag, val)}")
        elif gid in self.db.skills:
            rows = []
            for field, value in obj.items():
                if field.startswith(("skillDisplayName", "skillBaseDescription", "templateName",
                                     "skillDownBitmapName", "skillUpBitmapName")):
                    continue
                if value in (None, 0, [], "") or isinstance(value, (dict, list)):
                    continue
                txt = self.line(field, value, obj)
                if txt:
                    rows.append((_group(field), txt))
            rows.sort(key=lambda x: x[0])
            lines += [t for _, t in rows]
        lines.append("─" * 46)
        lines.append(f"[{gid}]")
        return "\n".join(lines)

    @staticmethod
    def _num(v):
        if isinstance(v, float) and v.is_integer():
            return str(int(v))
        return str(v)
