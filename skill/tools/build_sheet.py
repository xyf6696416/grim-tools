#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_sheet.py —— 把「形态方案 JSON（装备）+ 角色存档（属性/技能/星座）」渲染成
可照着做的 BD 落地清单（Markdown）。

用法：
    python tools/build_sheet.py <arch方案.json> <out.md> [--char Sam] [--archetype 形态名]

说明：
  · 装备来自 arch 方案的 14 个槽位（[base, comp, aug, pre, suf] 全是 gid），
    逐件解析中文名 + 等级需求 k，并与角色等级做「可落地」校验（k>等级 标 ✗）。
  · 属性点 / 专精技能 / 星座来自真实存档（load_char），天然符合等级预算，口径与
    plan_audit 的伤害报告一致（都是「真实存档 + arch 装备」路径）。
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gd import DB, dps as D  # noqa: E402


def lvl_req(db, gid):
    """物品等级需求：优先 k（GDX3），回退 levelRequirement / itemLevel。"""
    if not gid:
        return None
    o = db.get(gid)
    if not o:
        return None
    for f in ("k", "levelRequirement", "itemLevel"):
        if f in o and o[f] is not None:
            return o[f]
    return None


SLOTS = ["头部", "项链", "胸甲", "腿甲", "靴子", "手套",
         "戒指1", "戒指2", "腰带", "肩甲", "勋章", "圣物", "主手", "副手"]


def render(char, arch_path, arch_label):
    db = DB.load()
    arch = json.load(open(arch_path, encoding="utf-8"))
    c = D.load_char(char, "", True)
    L = c.get("level")
    out = []

    # ── 头 ──────────────────────────────────────────────
    out.append("# 可落地 BD 清单（%s · %s）" % (char, arch_label or arch_path))
    out.append("")
    out.append("> 角色等级 **%d** ｜ 专精 **%s** ｜ 全部数值来自真实存档 + 本形态优化装备，"
               "口径与 `plan_audit` 伤害报告一致。" % (L, " / ".join(c.get("classes", []))))
    out.append("")

    # ── 装备 ────────────────────────────────────────────
    out.append("## 一、装备（14 槽 · 含等级需求校验）")
    out.append("")
    out.append("| 槽位 | 物品 | 需求等级 | 组件 | 强化 | 前缀 | 后缀 | 可落地 |")
    out.append("|---|---|---|---|---|---|---|---|")
    bad = []
    for slot in SLOTS:
        tup = arch.get(slot)
        if not tup or not tup[0]:
            continue
        base, comp, aug, pre, suf = (list(tup) + [None] * 5)[:5]
        nm = db.name(base) if base else "—"
        k = lvl_req(db, base)
        comp_nm = db.name(comp) if comp else "—"
        aug_nm = db.name(aug) if aug else "—"
        pre_nm = db.name(pre) if pre else "—"
        suf_nm = db.name(suf) if suf else "—"
        if k is None or k <= L:
            ok = "✓"
        else:
            ok = "✗ 超 %d 级" % (k - L)
            bad.append((slot, nm, k))
        out.append("| %s | %s | %s | %s | %s | %s | %s | %s |"
                   % (slot, "%s `%s`" % (nm, base), (k if k is not None else "—"),
                      comp_nm, aug_nm, pre_nm, suf_nm, ok))
    if bad:
        out.append("")
        out.append("⚠ **不可落地槽位**：" + "；".join("%s %s(需求%d)" % (s, n, k) for s, n, k in bad))
    else:
        out.append("")
        out.append("✓ 全部装备等级需求 ≤ %d，可照装。" % L)

    # ── 属性点 ──────────────────────────────────────────
    out.append("")
    out.append("## 二、属性点（Attribute）")
    bio = c.get("bio", {})
    out.append("")
    out.append("- 力量 **Physique**：**%d**" % int(bio.get("physique", 0)))
    out.append("- 灵巧 **Cunning**：**%d**" % int(bio.get("cunning", 0)))
    out.append("- 精神 **Spirit**：**%d**" % int(bio.get("spirit", 0)))

    # ── 技能树 ──────────────────────────────────────────
    out.append("")
    out.append("## 三、技能加点（专精树）")
    sk = c.get("skills", {})
    groups = {"夜刃 (class04)": [], "狂战士 (class10)": [], "其他": []}
    for kk, lv in sk.items():
        low = kk.lower()
        if "devotion" in low or "default" in low or "potionmodifiers" in low:
            continue
        rec = kk if kk.startswith("records/") else "records/skills/" + kk
        nm = db.skill_name_of_record(rec) or kk
        if "class04" in kk:
            groups["夜刃 (class04)"].append((nm, lv))
        elif "class10" in kk:
            groups["狂战士 (class10)"].append((nm, lv))
        else:
            groups["其他"].append((nm, lv))
    for g, items in groups.items():
        if not items:
            continue
        out.append("")
        out.append("### %s" % g)
        out.append("")
        out.append("| 技能 | 等级 |")
        out.append("|---|---|")
        for nm, lv in sorted(items, key=lambda x: -x[1]):
            out.append("| %s | %d |" % (nm, int(lv)))

    # ── 星座 ────────────────────────────────────────────
    out.append("")
    out.append("## 四、星座（Devotion）")
    dev = [k for k in sk if "devotion" in k]
    out.append("")
    out.append("已点亮 **%d** 个星座节点：" % len(dev))
    out.append("")
    out.append("| 星座 / 星位 |")
    out.append("|---|")
    seen = set()
    for kk in dev:
        rec = kk if kk.startswith("records/") else "records/skills/" + kk
        nm = db.skill_name_of_record(rec) or kk
        if nm in seen:
            continue
        seen.add(nm)
        out.append("| %s |" % nm)

    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("arch")
    ap.add_argument("out")
    ap.add_argument("--char", default="Sam")
    ap.add_argument("--archetype", default="")
    a = ap.parse_args()
    if not a.archetype:
        a.archetype = Path(a.arch).stem.replace("arch_", "")
    txt = render(a.char, a.arch, a.archetype)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    open(a.out, "w", encoding="utf-8").write(txt + "\n")
    print("→ %s  (%d 行)" % (a.out, txt.count("\n") + 1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
