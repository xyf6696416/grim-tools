"""装备位合法性检查：组件 / 附魔是否真的能装在那个槽位上。

    python tools/plan_legal.py <方案.json> [<方案2.json> ...]   # 逐槽判定，有非法项退出码 1
    python tools/plan_legal.py --selftest                        # 用内置样例验证判据本身

方案 JSON 的格式是 `{槽位: [底材, 组件, 附魔, 前缀, 后缀]}`（`gd.opt` 的落盘格式）。

★ 判据只有一条（`gd/opt.py::comp_ok` / `aug_ok` 同源）：
      **底材自己的 `l` 就是它的槽位码；组件/附魔的 `cls` 就是它能装的底材码集**
      ⇒ 合法 ⟺ `base.l ∈ item.cls`
  码表：护甲 `c10=头盔 c11=护肩 c12=胸甲 c13=手套 c14=腰带 c15=腿甲 c16=足具`、
  `c40=戒指 c41=项链 c42=勋章 c43=圣物`、`c26=法器副手 c27=盾牌`、武器 `c20–c25/c28–c32`。

★ 为什么要有这个工具（2026-09-21）：游戏会**静默摘掉**非法装备位，而模型曾经
  「按描述文本 + 子串匹配」判合法性 ⇒ 两把斧头被镶上护甲组件「弹性铠甲片」、
  勋章被镶上戒指/项链附魔「炼狱粉尘」，**跑了几轮优化都没人发现**。
  详见 `docs/pitfalls.md` **#68**。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gd import DB, opt                                  # noqa: E402

LAYER = {"组件": 1, "附魔": 2}
# 槽位几层的名字（报告用）——
# `k` = GT 库的 `levelRequirement`（见 `docs/pitfalls.md` #86）
ALL_LAYER = ("底材", 0), ("组件", 1), ("附魔", 2), ("前缀", 3), ("后缀", 4)


def check_level(db, plan: dict, level: int):
    """等级要求检查：**每一层**（底材/组件/附魔/前缀/后缀）的 `k` 都必须 ≤ `level`。

    ★★ 为什么必须单独查（2026-09-22 我本人栽在这）：`comp_ok` 只判**槽位**，
      **不判等级**。于是 `题合法` 的方案里可以塞进「需要 75 级」的镶嵌物，
      而角色只有 73 级 —— `plan_legal` 照样报「全部合法」。
      实测：我据此把「刀刃之印」（`k=75`）当成可达收益推荐出去（+3.65%），
      实际**游戏里根本装不上**。GT 键字典：`levelRequirement: "k"`。

    返回 `[(槽位, 层名, gid, 名称, k), ...]`（空 = 全部满足）。
    """
    bad = []
    for slot, arr in plan.items():
        if not isinstance(arr, (list, tuple)):
            continue
        for name, idx in ALL_LAYER:
            gid = arr[idx] if len(arr) > idx else None
            if not gid:
                continue
            k = int((db.items.get(gid) or {}).get("k")
                    or (db.items.get(gid) or {}).get("itemLevel") or 0)
            if k > level:
                bad.append((slot, name, gid, db.name(gid) or gid, k))
    return bad


def check_plan(db, plan: dict) -> list[tuple[str, str, str, str, str]]:
    """返回 [(槽位, 类型, gid, 物品名, 底材码), ...]，空列表 = 全部合法。"""
    bad = []
    for slot, arr in plan.items():
        if not isinstance(arr, (list, tuple)) or not arr:
            continue
        base = arr[0]
        bc = (db.items.get(base) or {}).get("l") or "?"
        for kind, idx in LAYER.items():
            gid = arr[idx] if len(arr) > idx else None
            if not gid:
                continue
            ok = (opt.comp_ok if idx == 1 else opt.aug_ok)(slot, gid, base)
            if not ok:
                bad.append((slot, kind, gid, db.name(gid) or gid, bc))
    return bad


# ------------------------------------------------------------------ 自检样例
_GOOD = {"主手": ["it1891", "it2878", None, None, None],      # 斧 + 恶毒尖刺（武器组件）
         "胸甲": ["it1774", "it2860", None, None, None],      # 胸甲 + 弹性铠甲片
         "肩甲": ["it12354", "it2860", "it460", None, None],  # 肩甲 + 铠甲片 + 护甲附魔(k=70)
         "勋章": ["it798", None, None, None, None]}

_BAD = {"主手": ["it1891", "it2860", None, None, None],      # ★ 斧 + 护甲组件
        "副手": ["it15956", "it2860", None, None, None],     # ★ 斧 + 护甲组件
        "勋章": ["it798", None, "it453", None, None]}        # ★ 勋章 + 戒指/项链附魔


def _selftest(db) -> int:
    bad_ok = check_plan(db, _GOOD)
    bad_bad = check_plan(db, _BAD)
    fails = []
    print("合法样例 → 期望 0 处非法，实际 %d 处 %s" % (len(bad_ok), bad_ok))
    if bad_ok:
        fails.append("合法样例被误判")
    print("非法样例 → 期望 3 处非法，实际 %d 处" % len(bad_bad))
    for b in bad_bad:
        print("    %-5s %s %-9s %-14s 底材码=%s" % b)
    if len(bad_bad) != 3:
        fails.append("非法样例漏判")
    got = {(s, k) for s, k, _, _, _ in bad_bad}
    if got != {("主手", "组件"), ("副手", "组件"), ("勋章", "附魔")}:
        fails.append("非法样例命中位置不对：%s" % sorted(got))

    # ★★ 等级要求（`k`）自检：`it8898`「刀刃之印」`k=75` —— 73 级**装不上**，
    #   而它的槽位是合法的（cls 含武器码）⇒ **只有等级检查能拦住它**。
    #   这正是 2026-09-22 我把它误当可达收益的原因。
    lv = check_level(db, {"主手": ["it1891", "it8898", None, None, None]}, 73)
    print("等级样例（73 级 + 刀刃之印 k=75）→ 期望 1 处超等级，实际 %d 处" % len(lv))
    for b in lv:
        print("    %-5s %s %-9s %-14s k=%s" % b)
    if len(lv) != 1:
        fails.append("等级检查漏判刀刃之印")
    lv2 = check_level(db, {"主手": ["it1891", "it2878", None, None, None]}, 73)
    if lv2:
        fails.append("等级检查误判恶毒尖刺（k=15）")
    print("✓ 判据自检通过" if not fails else "✗ " + "；".join(fails))
    return 1 if fails else 0


def main() -> int:
    ap = argparse.ArgumentParser(description="装备位合法性检查（槽位 + 等级要求）")
    ap.add_argument("plans", nargs="*", help="方案 JSON（可多个）")
    ap.add_argument("--selftest", action="store_true", help="只用内置样例验证判据")
    ap.add_argument("--level", type=int, default=0,
                    help="角色等级，用于查 `k`（= 等级要求）。不传 ⇒ **不做等级检查**（会醒目警告）")
    ap.add_argument("--char", default="",
                    help="角色名：从存档读等级（与 `--level` 二选一，优先 `--level`）")
    a = ap.parse_args()

    db = DB.load()
    if a.selftest:
        return _selftest(db)
    if not a.plans:
        ap.print_help()
        return 2

    # ★ 判等级：`--level` 优先；否则从 `--char` 的存档读；都没有 ⇒ 显式警告。
    #   ⚠ 绝不静默跳过 —— 「可选参数不传就失效」是陷阱 #82 的形态。
    level = int(a.level or 0)
    if not level and a.char:
        try:
            from gd.save import core as _SC
            from gd import paths as _P
            k = [x for x in _P.characters()
                 if x.lstrip('_').lower() == a.char.lstrip('_').lower()][0]
            level = int(_SC.parse(str(_P.characters()[k] / "player.gdc"))
                        .get("level") or 0)
        except Exception as e:                                  # noqa: BLE001
            print("⚠ 读 `--char %s` 的等级失败：%s" % (a.char, e))
    if not level:
        print("⚠⚠ **未做等级要求检查**（没给 `--level` 或 `--char`）——")
        print("    `k`（= `levelRequirement`）超标的件游戏里**装不上**，"
              "只查槽位会漏掉这类（陷阱 #86）。建议：`--char <角色名>`")

    rc = 0
    for p in a.plans:
        if not Path(p).exists():
            print("=== %s ｜ 文件不存在，跳过" % Path(p).name)
            continue
        plan = json.loads(Path(p).read_text(encoding="utf-8"))
        bad = check_plan(db, plan)
        over = check_level(db, plan, level) if level else []
        n_slot = len([1 for v in plan.values() if v])
        print("=== %s ｜ %d 槽 ｜ 非法 %d 处 ｜ 超等级 %d 处%s"
              % (Path(p).name, n_slot, len(bad), len(over),
                 "（等级上限 %d）" % level if level else ""))
        for slot, kind, gid, nm, bc in bad:
            print("    ✗ %-5s %s %-9s %-16s 底材码=%s ← 游戏会静默摘掉"
                  % (slot, kind, gid, nm, bc))
        for slot, name, gid, nm, k in over:
            print("    ✗ %-5s %s %-9s %-16s k=%s > %d ← **等级不够，装不上**"
                  % (slot, name, gid, nm, k, level))
        if bad or over:
            rc = 1
        else:
            print("    ✓ 全部合法")
    return rc


if __name__ == "__main__":
    sys.exit(main())
