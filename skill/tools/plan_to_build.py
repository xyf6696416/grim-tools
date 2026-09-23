#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan_to_build.py —— 把 `gd.opt` 方案 JSON 转成 `gd.build` 的可落档方案。

两种 JSON 的差别：
  · gd.opt 方案   {槽位: [base, comp, aug, pre, suf]}            —— 全是 gid
  · gd.build 方案 {char, equipment:[{slot,item,prefix,suffix,component,augment}]}
    item / component / augment = GT id；prefix / suffix 用**记录名**（从桥表反查，
    天然是游戏里存在的合法记录）

用法：
    python tools/plan_to_build.py <角色> <opt方案.json> [--out build.json] [--note "…"]
                                    [--keep-weapon]   # 默认不动武器
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gd import paths                                    # noqa: E402
from gd.save import core, items as SI                   # noqa: E402

SLOTS = core.SLOTS          # 12 个装备槽（不含主手/副手）
WEAPON_SLOTS = ["主手", "副手"]


def _row(slot: str, tup, br) -> dict:
    base, comp, aug, pre, suf = (list(tup) + [None] * 5)[:5]
    row = {"slot": slot, "item": base or ""}
    if comp:
        row["component"] = comp
    if aug:
        row["augment"] = aug
    if pre:
        row["prefix"] = br.any_record_of(pre) or pre
    if suf:
        row["suffix"] = br.any_record_of(suf) or suf
    return row


def convert(char: str, plan: dict, note: str = "", with_weapon: bool = True) -> dict:
    br = SI.bridge()
    key = char if char.startswith("_") else "_" + char
    eq = []
    for slot in SLOTS:
        tup = plan.get(slot)
        if not tup or not tup[0]:
            continue
        eq.append(_row(slot, tup, br))
    # ★ 武器：opt 方案里的「主手/副手」同样要落档 —— 极限配装常靠换武器拉伤害
    #   （比如 双手 → 双持）。`--no-weapon` 可显式只改护甲/首饰。
    weapons = []
    if with_weapon:
        for slot in WEAPON_SLOTS:
            tup = plan.get(slot)
            if not tup or not tup[0]:
                continue
            weapons.append(_row(slot, tup, br))
    return {"char": key, "note": note or "%s 装备方案" % key, "equipment": eq,
            "weapons": weapons}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("char")
    ap.add_argument("plan")
    ap.add_argument("--out", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--no-weapon", action="store_true",
                    help="只改 12 件护甲/首饰，武器保持不动")
    a = ap.parse_args()
    plan = json.load(open(a.plan, encoding="utf-8"))
    spec = convert(a.char, plan, a.note, with_weapon=not a.no_weapon)
    out = a.out or str(paths.DATA_DIR / "plans" / ("%s_build.json" % a.char.lstrip("_")))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(spec, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("角色 %s ｜ 装备 %d 槽 ｜ 武器 %d 件"
          % (spec["char"], len(spec["equipment"]), len(spec["weapons"])))
    for r in spec["equipment"] + spec["weapons"]:
        bits = ["%s=%s" % (k, str(v).split("/")[-1]) for k, v in r.items() if k != "slot"]
        print("  %-6s %s" % (r["slot"], "  ".join(bits)))
    print("→ %s" % out)
    _ = os
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
