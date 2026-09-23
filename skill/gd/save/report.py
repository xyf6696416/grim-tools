"""角色报告：把存档 → 可读的中文报告（存档只读，绝不写盘）。

数据来源：`gd.save.core.parse()`（存档）+ 离线库（名称/分类）+ `items.RecordBridge`
（记录路径 ↔ GT id）。

    from gd.save import report
    print(report.character("Sam"))
    md = report.character_md("Sam")     # Markdown
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .. import paths
from . import core, items

__all__ = ["character", "character_md", "list_chars"]

SLOTS = core.SLOTS
DIFF = core.DIFF


# ==================================================================== 工具
def _hm(seconds: Any) -> str:
    try:
        s = int(seconds or 0)
    except (TypeError, ValueError):
        return "—"
    return f"{s // 3600} 小时 {(s % 3600) // 60} 分"


def _pct(a: float, b: float) -> str:
    return f"{a / b * 100:.1f}%" if b else "—"


class _Ctx:
    """一次报告用到的共享对象（避免重复加载）。"""

    def __init__(self):
        from .. import DB
        from ..render import Renderer
        self.db = DB.load()
        self.br = items.bridge(self.db)
        self.r = Renderer(self.db)
        self._rec_skill = None
        self._factions = None

    # 记录路径 -> 专精技能（来自 data/mastery_skills.json 的 record 字段）
    def skill_cn(self, record: str) -> str:
        """技能记录路径 -> 中文名（专精技能 / 星座节点 / 默认技能）。"""
        v = self.db.skill_name_of_record(record)
        if v:
            return v
        if record.endswith(".dbr"):
            return record.split("/")[-1][:-4]
        return record

    def faction_list(self):
        """按派系编号排序：存档里 faction_values 是**按编号顺序**存的。"""
        if self._factions is None:
            def num(g):
                m = re.search(r"\d+", g)
                return int(m.group(0)) if m else 0
            self._factions = [(g, self.db.name(g))
                              for g in sorted(self.db.factions, key=num)]
        return self._factions


# ==================================================================== 物品
def item_line(ctx: _Ctx, it: dict, indent: str = "  ") -> str:
    """一行中文物品描述：名称 ＋ 前缀 ＋ 后缀 ＋ 镶嵌 ＋ 附魔。"""
    br = ctx.br
    base = br.label(it.get("basename") or "")
    parts = [base]
    for key, label in (("prefix", "前缀"), ("suffix", "后缀"),
                       ("relic_name", "镶嵌"), ("augment_name", "附魔")):
        rec = it.get(key) or ""
        if not rec:
            continue
        nm = br.affix_name(rec) or br.label(rec)
        parts.append(f"{label} {nm}")
    line = f"{indent}{base}"
    if len(parts) > 1:
        line += "  ｜ " + " ｜ ".join(parts[1:])
    extra = []
    if it.get("attached") is False:
        extra.append("⚠ 未生效(attached=False)")
    if it.get("qty", 1) != 1:
        extra.append(f"x{it['qty']}")
    if extra:
        line += "   " + " ".join(extra)
    return line


# ==================================================================== 主体
def _summarize(doc: dict, ctx: _Ctx) -> dict:
    bm = doc["block_map"]
    b2 = bm.get(2, {})
    b16 = bm.get(16, {})
    classes = doc.get("classes") or []
    return {
        "名字": doc.get("name"),
        "等级": doc.get("level"),
        "职业": " + ".join(classes) if isinstance(classes, list) else classes,
        "硬核": "是" if doc.get("hardcore") else "否",
        "难度": DIFF.get(bm.get(1, {}).get("last_difficulty"), "?"),
        "最高通关难度": DIFF.get(bm.get(1, {}).get("greatest_difficulty_completed"), "?"),
        "铁币": bm.get(1, {}).get("iron"),
        "体格": b2.get("physique"),
        "狡诈": b2.get("cunning"),
        "精神": b2.get("spirit"),
        "生命": b2.get("health"),
        "能量": b2.get("energy"),
        "未用属性点": b2.get("attribute_points"),
        "未用技能点": b2.get("skill_points"),
        "技能点已用": None,
        "虔诚点已用": (b2.get("total_devotion_points") or 0) - (b2.get("devotion_points") or 0),
        "虔诚点可用": b2.get("devotion_points"),
        "游戏时长": _hm(b16.get("playtime_seconds")),
        "击杀": b16.get("kill_count"),
        "死亡": b16.get("death_count"),
        "锻造物品": b16.get("items_crafted"),
        "最高单次伤害": b16.get("greatest_damage_done"),
        "版本": doc.get("version"),
    }


def character(name_or_path: str, full: bool = False) -> str:
    """生成一份中文角色报告（纯文本）。"""
    doc, ctx = _load(name_or_path)
    out: list[str] = []
    s = _summarize(doc, ctx)
    bm = doc["block_map"]

    out.append("=" * 68)
    out.append(f"{s['名字']}   lv{s['等级']}   {s['职业']}"
               f"{'   [硬核]' if s['硬核'] == '是' else ''}")
    out.append("=" * 68)
    out.append(f"  难度 {s['难度']}（最高通关 {s['最高通关难度']}）    "
               f"铁币 {s['铁币']:,}    时长 {s['游戏时长']}")
    out.append(f"  体格 {s['体格']:.0f}  狡诈 {s['狡诈']:.0f}  精神 {s['精神']:.0f}"
               f"    生命 {s['生命']:.0f}  能量 {s['能量']:.0f}")
    free = []
    if s["未用属性点"]:
        free.append(f"属性点 {s['未用属性点']}")
    if s["未用技能点"]:
        free.append(f"技能点 {s['未用技能点']}")
    if s["虔诚点可用"]:
        free.append(f"虔诚点 {s['虔诚点可用']}")
    if free:
        out.append("  ⚠ 未分配：" + "、".join(free))
    out.append(f"  虔诚已点 {s['虔诚点已用']} 点")

    # ---------------- 装备 ----------------
    b3 = bm.get(3, {})
    eq = b3.get("equipment") or []
    out.append("")
    out.append("【装备】")
    for i, it in enumerate(eq):
        slot = SLOTS[i] if i < len(SLOTS) else f"槽{i}"
        if not it.get("basename"):
            out.append(f"  {slot:<5} —")
            continue
        line = item_line(ctx, it, indent="")
        out.append(f"  {slot:<5} {line}")
    if b3.get("alt1"):
        alt = [it for it in b3["alt1"] if it.get("basename")]
        if alt:
            out.append(f"  武器套2 {' ｜ '.join(ctx.br.label(x['basename']) for x in alt)}")
    if b3.get("use_alt_weaponset"):
        out.append(f"  当前使用：武器套{2 if b3.get('use_alt_weaponset') == 2 else '1'}")

    # ---------------- 背包 ----------------
    sacks = b3.get("sacks") or []
    total_bag = sum(len(sk.get("items") or []) for sk in sacks)
    out.append("")
    out.append(f"【背包】{len(sacks)} 个袋子，共 {total_bag} 件")
    if full:
        for si, sk in enumerate(sacks):
            for it in sk.get("items") or []:
                if it.get("basename"):
                    out.append(f"  袋{si + 1} " + item_line(ctx, it, indent=""))

    # ---------------- 仓库 ----------------
    b4 = bm.get(4, {})
    stashes = b4.get("stashes") or []
    total_st = sum(len(th.get("items") or []) for th in stashes)
    out.append("")
    out.append(f"【仓库】{len(stashes)} 页，共 {total_st} 件")

    # ---------------- 技能 ----------------
    b8 = bm.get(8, {})
    skills = b8.get("skills") or []
    named = [x for x in skills if (x.get("level") or 0) > 0
             and "/default/" not in (x.get("skill") or "")]
    out.append("")
    out.append(f"【技能】{len(named)} 条加点记录（未用技能点 {s['未用技能点']}）")
    # 同一技能名会有多条（星座的多个星、形态子技能…）→ 按名字合并
    agg: dict[str, list] = {}
    for x in named:
        agg.setdefault(ctx.skill_cn(x["skill"]), []).append(x.get("level") or 0)
    rows = [(nm, max(lv), len(lv), sum(lv)) for nm, lv in agg.items()]
    for nm, top, cnt, tot in sorted(rows, key=lambda r: (-r[1], r[0])):
        tag = f"  ×{cnt}" if cnt > 1 else ""
        out.append(f"  {nm:<18} {top}{tag}")
    if b8.get("masteries_allowed") is not None:
        out.append(f"  可用专精：{b8['masteries_allowed']}")

    # ---------------- 战绩 ----------------
    out.append("")
    out.append("【战绩】")
    out.append(f"  击杀 {s['击杀']:,}   死亡 {s['死亡']:,}   锻造 {s['锻造物品']:,}")
    out.append(f"  最高单次伤害 {s['最高单次伤害']:,.0f}")

    # ---------------- 派系 ----------------
    fv = (bm.get(13) or {}).get("faction_values") or []
    flist = ctx.faction_list()
    if fv and flist:
        out.append("")
        out.append("【派系声望】")
        for i, f in enumerate(fv):
            if i >= len(flist):
                break
            gid, nm = flist[i]
            if not f.get("unlocked"):
                continue
            out.append(f"  {nm:<16} {f.get('value'):>10,.0f}")

    # ---------------- 进度 ----------------
    b6 = bm.get(6, {})
    b17 = bm.get(17, {})
    out.append("")
    out.append(f"【进度】传送点 {len(b6.get('teleporter_points') or [])}    "
               f"神殿 {len(b17.get('shrines') or [])}    "
               f"学识笔记 {len((bm.get(12) or {}).get('lore_item_names') or [])}")

    out.append("")
    out.append("=" * 68)
    out.append(f"存档：{doc.get('path')}")
    out.append(f"所有块解析 OK：{doc.get('all_blocks_ok')}")
    return "\n".join(out)


def character_md(name_or_path: str, full: bool = False) -> str:
    """Markdown 版（结构与纯文本一致，标题转 #）。"""
    txt = character(name_or_path, full=full)
    lines = txt.splitlines()
    out = []
    for ln in lines:
        if ln.startswith("===="):
            continue
        if ln.startswith("【") and ln.endswith("】"):
            out.append(f"### {ln[1:-1]}")
        elif ln.startswith("  "):
            out.append("- " + ln.strip())
        else:
            out.append(ln)
    return "\n".join(out)


# ==================================================================== 载入
def _load(name_or_path: str) -> tuple[dict, _Ctx]:
    ctx = _Ctx()
    p = resolve_path(name_or_path)
    if p is None:
        raise FileNotFoundError(
            f"找不到角色或存档「{name_or_path}」。\n"
            f"现有角色：{', '.join(paths.characters()) or '（无）'}")
    return core.parse(str(p)), ctx


def resolve_path(name_or_path: str):
    """接受：角色名 / player.gdc 路径 / 角色目录 路径。"""
    chars = paths.characters()
    if name_or_path in chars:
        return chars[name_or_path] / "player.gdc"
    from pathlib import Path
    p = Path(name_or_path)
    if p.is_dir():
        return p / "player.gdc" if (p / "player.gdc").exists() else None
    if p.exists():
        return p
    # 宽松匹配（允许 _Sam / Sam 混用）
    key = name_or_path.lstrip("_").lower()
    for name, d in chars.items():
        if name.lstrip("_").lower() == key:
            return d / "player.gdc"
    return None


def list_chars() -> list[tuple[str, int, str]]:
    """[(角色名, 等级, 职业)]，用于列表展示。"""
    from . import core as C
    out = []
    for name, d in paths.characters().items():
        try:
            doc = C.parse(str(d / "player.gdc"))
            cls = doc.get("classes") or []
            out.append((name, doc.get("level") or 0,
                        " + ".join(cls) if isinstance(cls, list) else str(cls)))
        except Exception as e:
            out.append((name, -1, f"解析失败：{type(e).__name__}"))
    return sorted(out, key=lambda x: -x[1])
