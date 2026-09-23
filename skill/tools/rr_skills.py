#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rr_skills.py —— 减抗技能的**每级边际价值**分析（技能点预算用）。

`gd/opt.py` 的 `recalibrate_rr_skills()` 已经把「减抗技能每级增益」按来源折扣折进
技能分，好让优化器知道「装备上那条 `+2 刺骨战吼` 值多少钱」。但那只解决**配装**问题 ——
「**技能点**该往哪儿投」是另一件事（技能点是等级预算，不属于装备方案）。
这个工具把同一张表摊开给人看。

三件事它做对了（都是踩过的坑）：
  · 权重用**真实口径标定**：`rr_add` 的边际是 `(100+pct)/(100−res)` 倍（Sam 实测 9.8×），
    不是拍脑袋的常数 —— 所以先用现状跑一次真实评估（`plan_dps.dps_of`）。
  · **来源折扣**：职业树 1.0 / 星座 0.7 / 物品触发技能 0.25 / 其他 0.5。
    触发型物品技能（如 `item_sacredbalance`）按常驻估值会高出几倍。
  · **本流占比归一**：物品上写的「全类型 −27%」对本流只值其中几个桶，
    占比从 `damage_weights` + `field_aliases` 推导，不是写死。

用法：
    python tools/rr_skills.py                      # 全库排行（默认按 Sam 的现状标定）
    python tools/rr_skills.py --char Sam
    python tools/rr_skills.py --archetype wolf_nightblade
    python tools/rr_skills.py --top 30             # 只看前 N
    python tools/rr_skills.py --enemy-res boss     # 按 Boss 档标定（减抗边际更大）
    python tools/rr_skills.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

KIND_ZH = {"playerclass": "职业树", "devotion": "星座", "itemskill": "物品技能",
           "item": "物品技能", "mastery": "精通", "other": "其他"}


def main() -> int:
    ap = argparse.ArgumentParser(description="减抗技能的每级边际价值")
    ap.add_argument("--char", default="Sam")
    ap.add_argument("--archetype", default="")
    ap.add_argument("--enemy-res", default=None,
                    choices=["none", "elite", "boss", "high", "max"])
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.enemy_res:
        os.environ["GD_ENEMY_RES_PROFILE"] = a.enemy_res
    if a.archetype:
        os.environ["GD_ARCHETYPE"] = a.archetype
    os.environ.setdefault("PYTHONHASHSEED", "0")
    os.environ.setdefault("GD_QUIET", "1")

    import plan_dps as PD
    from gd import opt as O
    from gd import gear as G
    from gd import rr as RR
    from gd import dps as D
    from gd import savemap as M

    rm = O.RR_MAIN or "pierce"
    prof = RR.profile_of()
    # ---- 标定：拿现状真实评估的 (主桶加成, 敌方剩抗) 算边际比
    note = []
    try:
        cur = json.load(open(ROOT / "data" / "plans" / ("%s_current.json" % a.char),
                            encoding="utf-8"))
    except Exception:
        cur = {}
    arch = a.archetype or ""
    if cur:
        try:
            ev = PD.dps_of(a.char, {k: list(v) for k, v in cur.items()}, arch)
            arch = ev.get("arch") or arch
            pct_all, rr_all = ev.get("pct") or {}, ev.get("rr") or {}
            pct = pct_all.get(rm)
            if pct is None:
                pct = max(pct_all.values()) if pct_all else 0.0
            res = RR.res_eff(rm, rr_all, prof["base"])
            O.recalibrate_rr(pct_main=pct, res_main=res)
            if "conv_net" in (O.FKEYS or ()):
                O.recalibrate_conv(pct=pct_all, share=ev.get("share") or None, main=rm)
            note.append("标定自 %s 现状：主桶 %s ｜ 加成 +%.0f%% ｜ 敌方剩 %.0f%%（%s）"
                        % (a.char, rm, pct, res, prof.get("zh") or "-"))
        except Exception as e:
            note.append("现状标定失败（用保守基线）：%s: %s" % (type(e).__name__, e))
    else:
        note.append("没找到 data/plans/%s_current.json ⇒ 用保守基线权重" % a.char)

    gain = O.recalibrate_rr_skills(log=lambda s: note.append(s.strip()))
    w_add = float(O.W_DMG.get("rr_add", 0.0) or 0.0)
    w_pct = float(O.W_DMG.get("rr_pct", 0.0) or 0.0)

    # 现状已点的等级（用来标「已点 / 未点」）
    lv = {}
    if cur:
        try:
            c = D.load_char(a.char)
            eff = dict(c["skills"])
            for rec, extra in (c.get("skill_plus") or {}).items():
                if rec in eff:
                    eff[rec] += extra
            tag2rec = D._tag2rec_map()
            for tag, rec in tag2rec.items():
                if rec in eff:
                    lv[tag] = eff[rec]
        except Exception:
            pass

    tags = G.load_tags()
    rows = []
    for tag, (add, mx, rec) in (gain or {}).items():
        v = (add * w_add + mx * w_pct) * O._rr_skill_kind_k(rec)
        if v <= 0:
            continue
        zh = tags.get(tag) or ""
        rows.append({"tag": tag, "record": rec, "add": round(add, 3), "max": round(mx, 3),
                     "score": round(v, 2), "kind": O._rr_skill_kind_k(rec),
                     "level": lv.get(tag, 0),
                     "name": (zh or os.path.basename(rec).replace(".dbr", ""))
                             .replace("^k", "").replace("^w", "")})
    rows.sort(key=lambda r: -r["score"])

    if a.json:
        print(json.dumps({"main": rm, "w_add": w_add, "w_pct": w_pct,
                          "profile": prof.get("zh"), "rows": rows[:a.top] if a.top else rows},
                         ensure_ascii=False))
        return 0

    print("")
    print("═" * 78)
    print("减抗技能每级边际价值 ｜ 主桶 %s ｜ 敌方 %s" % (rm, prof.get("zh") or "-"))
    print("═" * 78)
    for s in note:
        print("  " + s)
    print("  权重：rr_add=%.2f ｜ rr_pct=%.2f（单位 = 等效主桶减抗点 → 代理分）" % (w_add, w_pct))
    print("  含减抗的技能 %d 个；下表按「分/级」降序（含来源折扣）" % len(rows))
    print("")
    print("  %-3s %-20s %-8s %8s %8s %9s %6s" %
          ("#", "技能", "来源", "每级叠加", "每级最强", "分/级", "已点"))
    print("  " + "-" * 72)
    for i, r in enumerate(rows[:a.top], 1):
        print("  %-3d %-20s %-8s %8.2f %8.2f %9.1f %6s"
              % (i, r["name"][:20], KIND_ZH.get(_kind_name(r["record"]), "其他"),
                 r["add"], r["max"], r["score"], r["level"] or "—"))
    print("")
    print("  说明：「分/级」= (每级叠加点 × rr_add + 每级最强点 × rr_pct) × 来源折扣。")
    print("        来源折扣：职业树 1.0 / 星座 0.7 / 物品触发技能 0.25 / 其他 0.5。")
    print("        「每级最强」列对应 B-% 族（取最强、不叠加）⇒ 第二件起边际陡降，")
    print("        所以 `rr_pct` 的权重另乘 `GD_RR_PCT_FACTOR`（默认 0.5）。")
    print("")
    return 0


def _kind_name(rec):
    s = (rec or "").lower()
    if "playerclass" in s or "classtraining" in s:
        return "playerclass"
    if "/devotion/" in s:
        return "devotion"
    if "itemskill" in s or "itemskills" in s:
        return "itemskill"
    return "other"


if __name__ == "__main__":
    raise SystemExit(main())
