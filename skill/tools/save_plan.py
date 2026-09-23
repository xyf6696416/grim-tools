#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""save_plan.py —— 把存档里「正在穿的装备」导出成 gd.opt 的方案 JSON。

用途：
    · `gd.opt --compare 现状.json 候选.json` —— 让优化结果与现状**同口径对账**
    · `gd.explain 候选.json` —— 拆开看伤害代理的每一项来源

输出格式与 gd_opt 的 `--out` 完全一致：{槽位: [base, comp, aug, pre, suf]}

用法：
    python tools/save_plan.py Sam [--out data/plans/Sam_current.json] [--weapon]
    （默认只导 12 件护甲/首饰；`--weapon` 追加主手/副手两槽）
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gd import paths  # noqa: E402
from gd.save import core, items as SI  # noqa: E402


def build(name: str, with_weapon: bool = False) -> dict:
    br = SI.bridge()
    chars = paths.characters()
    key = next((k for k in chars if k == name or k.lstrip("_") == name), None)
    if key is None:
        raise SystemExit("✗ 角色 %r 不存在；可选：%s" % (name, ", ".join(chars)))
    doc = core.parse(str(chars[key] / "player.gdc"))
    b3 = doc["block_map"][3]
    eq = b3.get("equipment") or []

    def gid(rec):
        return br.gid_of(rec) if rec else None

    plan = {}
    for i, it in enumerate(eq):
        slot = core.SLOTS[i] if i < len(core.SLOTS) else None
        if slot is None:
            continue
        # 武器套（主手/副手）在 equipment 之后；用 alt1 判断更稳
        plan[slot] = [gid(it.get("basename")), gid(it.get("relic_name")),
                      gid(it.get("augment_name")), gid(it.get("prefix")),
                      gid(it.get("suffix"))]
    # 武器（主手/副手）**不在 `equipment` 里** —— 它们在 `alt1` / `alt2`（两套武器）
    # `alt1_unused` / `alt2_unused` 指示「哪套没用」，据此选**当前在用**的那套。
    if with_weapon:
        use2 = bool(b3.get("alt1_unused")) and not bool(b3.get("alt2_unused"))
        alt = b3.get("alt2" if use2 else "alt1") or []
        for j, slot in enumerate(["主手", "副手"]):
            if j >= len(alt):
                break
            it = alt[j]
            # ★ 只收「有主体」的武器条目。双手武器的 alt1[1] 是个**只剩镶嵌的空壳**
            #   （basename=''，只有 relic_name）—— 收进来会被当成「副手也装了一件」，
            #   跟 override 一起用时把主手的镶嵌**重复计一次**（实测 DPS 虚高 ~1.7%）。
            if not it.get("basename"):
                continue
            plan[slot] = [gid(it.get("basename")), gid(it.get("relic_name")),
                          gid(it.get("augment_name")), gid(it.get("prefix")),
                          gid(it.get("suffix"))]
    return plan


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("char")
    ap.add_argument("--out", default="")
    ap.add_argument("--weapon", action="store_true", help="追加主手/副手")
    a = ap.parse_args()
    plan = build(a.char, a.weapon)
    out = a.out or str(paths.DATA_DIR / "plans" / ("%s_current.json" % a.char.lstrip("_")))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({k: list(v) for k, v in plan.items()},
              open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    from gd import DB
    db = DB.load()
    for slot, tup in plan.items():
        print("  %-6s %s" % (slot, " | ".join(
            "%s(%s)" % (db.name(g) if g else "——", g or "") for g in tup)))
    print("→ %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
