# -*- coding: utf-8 -*-
"""tune_skills.py —— 技能点调优（真实 DPS 目标，单次加载存档）。

三种模式
--------
    --mode add       只用**未分配**的点做贪心（最保守，不动任何现有加点）
    --mode marginal  ★ 边际值表：每个技能的「拆 1 点损失」与「加 1 点收益」
    --mode swap      ★ 在 marginal 的基础上做 1-1 换位，迭代到收敛

为什么不用「全量组合搜索」
--------------------------
单次 `final_report` 实测 **2.16 s**（21 个技能 × 22 个候选 = 462 次 ≈ 17 分钟/轮）。
改用**边际值**：对每个已有技能算「−1 点」的损失、对每个可加技能算「+1 点」的收益，
一轮只有 ~30 次评估（≈ 65 s），而且直接给出「该拆谁、该补谁」的排序。

★★ 本工具存在的根本原因（2026-09-20 定位）：**加点不能只看技能等级**
---------------------------------------------------------------
注入加点若**多投武器池（WPS）**，Σ权重会 ≥ 100 ⇒ `默认攻击权重 = max(0, 100−Σw) = 0`
⇒ **主输出（野性利爪）一次都不出现**，DPS 直接腰斩（实测 −50%）。
所以每次评估都会报 `ΣW` 与 `默认权重`，并在 `默认权重 == 0` 时**打红灯**。

安全护栏（不是可选项）
----------------------
1. **绝不动精通条**（`_classtraining_*`）—— 它按 tier 门槛解锁技能树，拆了整片失效。
2. **绝不动「有下游依赖」的技能** —— `wpattack1/2/3` 依赖 `wpattack0`、
   `werewolf2/3` 与 `passive04` 依赖 `werewolf1`（离线库 `deps` 字段，全库 44 条）。
   拆掉父技能会让子技能**非法**，而伤害模型**不会报错**（静默照算）
   ⇒ 会给出一个游戏里点不出来的方案。
3. 只考虑「当前等级 > 0」的技能做拆点、「未到 `max_level`」的做加点。

用法
----
```bash
PY=".../envs/default/Scripts/python.exe"
$PY tools/tune_skills.py --mode marginal            # 边际值表
$PY tools/tune_skills.py --mode swap --rounds 3     # 换位搜索
$PY tools/tune_skills.py --mode add                 # 只花未分配的点
ARCH=werewolf $PY tools/tune_skills.py --mode swap  # 换形态
```
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, SKILL)
sys.path.insert(0, HERE)
os.environ.setdefault('GD_PROJ_HITS', '1')
# ★★ 2026-09-22 性能：优化器路径**关掉归因账本**（`slot_pct` / `slot_flat` ——
#   那是「伤害循环文档」的报告产物，却在每次评估里都记一遍）。
#   报告子进程会显式改回 `GD_ATTRIB=1`。
os.environ.setdefault('GD_ATTRIB', '0')

from gd import dps as D, rotation as R, procs as PR, skillprov as SP, paths  # noqa: E402
from gd import rr as RR, enemy as ENM                                # noqa: E402
import objfunc as _OBJ                                                # noqa: E402

# ★ 评分目标（`--objective` / `GD_OBJ`）：默认 `total` = `rep['dps']`（旧行为逐位不变）；
#   `pierce` = **穿刺桶的含减抗 DPS** —— 「主穿刺」形态必须用它，否则优化器会继续堆流血，
#   与形态声明的 `damage_weights`（装备搜索用穿刺权重）自相矛盾。
OBJ = _OBJ.mode_default()

EN = importlib.import_module('gd.enemy')

ARCH = os.environ.get('ARCH', 'wolf_nightblade_fast')
CHAR = os.environ.get('CHAR', 'Sam')
PLAN = os.environ.get('PLAN', 'data/plans/Sam_lv73_current.json')


def _gear_override():
    """`PLAN`（**显式设了环境变量才生效**）指定的方案作为**装备覆盖**。

    ★ 为什么要它：旧版 `tune_skills` / `eval_build_variants` **完全忽略 PLAN**，
      永远在**存档原装备**上算技能 —— 换流（例如「主穿刺」要换 5 件装备）时，
      装备按穿刺权重选、技能却在旧装备上评，两套假设互相矛盾，结论不可用。
    ⚠ 未设 `PLAN` 时**不覆盖** ⇒ 旧行为逐位不变（避免默认方案与存档路径产生微差）。
    """
    if not os.environ.get('PLAN'):
        return None
    p = os.path.join(SKILL, PLAN)
    if not os.path.isfile(p):
        return None
    try:
        import plan_dps as _PD
        return _PD.plan_to_override(json.load(open(p, encoding='utf-8'))) or None
    except Exception:                                            # noqa: BLE001
        return None


_C = D.load_char(CHAR, '', True, gear_override=_gear_override())
EFF_ALL = dict(_C['skills'])
for _k, _e in (_C.get('skill_plus') or {}).items():
    if _k in EFF_ALL:
        EFF_ALL[_k] += _e

# ★★ 敌方减抗（RR）与敌方档 —— **必须与装备搜索（`tools/plan_dps.py::dps_of`）同口径**。
#   旧版 `tune_skills` / `eval_build_variants` **没传 `rr=`** ⇒ 算的是「无减抗」口径，
#   而装备搜索走的是**含减抗**口径 ⇒ 两个阶段的目标函数不同源，结论会互相矛盾
#   （实测同一套装备：带 RR 穿刺桶 112,233 ／ 不带 RR 76,090，差 **47%**）。
#   ★★★ 2026-09-22（陷阱 #85）：**光传 rr 还不够** —— `collect_char` 的结果**按试验等级
#     重算**才有意义。旧写法在这里算一次就冻结，于是「改减抗技能的等级」永远不体现在
#     评分里（刺骨战吼 lv11→12 的穿刺减抗 36% → 38%，拆点损失却报 0）。
#     现改为 `RRCache`：只有**带减抗字段的那些记录**的等级变了才重算，其余命中缓存。
_RRC = RR.RRCache(_C['folded'], _C['db'])
_eprof = ENM.get_profile(os.environ.get('GD_ENEMY_PROFILE') or None,
                         _C.get('level') or 100)
_e_arm, _e_src = D.resolve_enemy_armor(os.environ.get('GD_ENEMY_ARMOR') or None, _eprof)
# ⚠ 这里**不含 rr** —— rr 由每次试验按自身等级现取（见 `ev()`）
RRKW = dict(enemy=_eprof, enemy_armor=_e_arm, enemy_armor_src=_e_src)


SKILLS = paths.load_json('skills.json') or {}
BASE = {k: v for k, v in _C['skills'].items() if v > 0}
BIO = _C.get('bio') or {}
MAST = {}
for _r, _lv in BASE.items():
    if '_classtraining_' in _r:
        MAST[os.path.basename(_r).replace('_classtraining_', '').replace('.dbr', '')] = _lv
for _cl, _ex in (_C.get('mastery_plus') or {}).items():
    MAST[_cl] = MAST.get(_cl, 0) + _ex
ATTRS = R.panel_attrs(BIO, MAST, _C.get('gear_flat') or {}, _C.get('gear_pct') or {})
APCT = R.attr_damage_pct(ATTRS)
EQ_SK = SP.norm_equipped([x[1] for x in (_C.get('item_skills') or [])])
WPS = PR.wps_pool(_C.get('base_gids'))
# ★★ 2026-09-22：武器构成（判武器类型硬前提，见 `gd/procs.weapon_type_ok`）。
WEAPON_ST = PR.weapon_state_of(_C)

# 下游依赖：谁依赖了谁（拆点护栏）。全库 `deps` 只有 44 条，构表很便宜。
_DEPENDENTS: dict = {}
for _r, _d in SKILLS.items():
    for _p in (_d.get('deps') or []):
        _DEPENDENTS.setdefault(_p, set()).add(_r)


def nm(rec):
    n = _NAMES.get(rec)
    if n:
        return n
    if '_classtraining_' in rec:
        return '精通条 ' + os.path.basename(rec).replace('_classtraining_', '').replace('.dbr', '')
    return (SKILLS.get(rec) or {}).get('name') or rec.rsplit('/', 1)[-1]


_NAMES = {r: ((SKILLS.get(r) or {}).get('name') or r.rsplit('/', 1)[-1]) for r in BASE}


def ev(sk):
    """sk: {记录: 等级} → (面板, 实战, ΣW, 默认权重, rep)。"""
    eff = dict(sk)
    for r, e in (_C.get('skill_plus') or {}).items():
        if r in eff:
            eff[r] += e
    rep = R.final_report(
        ARCH, eff, db=_C['db'], folded=_C['folded'], skill_records=list(eff),
        base_aps=_C.get('base_aps') or 1.25, attr_pct=APCT,
        conversions=_C.get('conversions') or [], skill_mods=_C.get('skill_mods') or {},
        attrs=ATTRS, level=_C.get('level'),
        equipped_sk=EQ_SK, item_wps=WPS,
        # ★★★ 陷阱 #85：rr **按本试验的等级现取**（不是模块级冻结值）——
        #   否则「改减抗技能等级」在评分里恒为 0（RRCache 会把它做得足够快）。
        rr=_RRC.pack(eff),
        **RRKW,      # RRKW 里含 enemy / 护甲
        # ★★ 2026-09-22：武器类型门控（与 `plan_dps` / CLI 同口径）。技能轴也要 ——
        #   基础主动技能里就有限武器类型的（如「猛袭」类要双手），不接会出现
        #   「优化器以为可用、游戏里点不出来」。
        weapon_st=WEAPON_ST)
    rot = rep.get('rotation') or {}
    return (float(rep['dps']), float(rep.get('dps_real') or 0),
            rot.get('weight_total'), rot.get('default_weight'), rep)


def _sc(v):
    """评分：`v` = `ev()` 的返回值；按 `OBJ` 取「合计」或「穿刺桶」。"""
    return _OBJ.score(v[4], OBJ)


def _skippable(rec):
    """不进调优池：动作条 / 星座 / 物品技能 / 精通条。"""
    return ('/default/' in rec or '/devotion/' in rec or '/itemskills' in rec
            or '_classtraining_' in rec)


def unspent():
    """存档里未分配的技能点（`block2.skill_points`）。"""
    try:
        from gd import _legacyenv as _E
        from gd.save import core as _S
        _sd, _ = _E.find_save_dir()
        _p = os.path.join(_sd, 'main', '_%s' % CHAR.lstrip('_'), 'player.gdc')
        _b2 = _S.parse(_p, record=False)['block_map'][2]
        return int(_b2.get('skill_points') or 0)
    except Exception:                                            # noqa: BLE001
        return 0


def removable(cur):
    """可拆点的技能：现有 >0、非跳过类、**没有下游依赖**。"""
    return sorted(r for r, lv in cur.items()
                  if lv > 0 and not _skippable(r) and not _DEPENDENTS.get(r))


def addable(cur):
    """可加点的技能：现有记录且未到 `max_level`。"""
    out = []
    for rec, lv in cur.items():
        if _skippable(rec):
            continue
        mx = (SKILLS.get(rec) or {}).get('max_level')
        if mx and lv < mx:
            out.append(rec)
    return sorted(out)


def main() -> int:
    # ⚠ `global` 必须在函数里**首次使用 OBJ 之前**声明（否则 SyntaxError：
    #   "name 'OBJ' is used prior to global declaration"）。
    global OBJ
    ap = argparse.ArgumentParser(description='技能点调优（真实 DPS 目标）')
    ap.add_argument('--mode', default='marginal', choices=('add', 'marginal', 'swap'))
    ap.add_argument('--rounds', type=int, default=3, help='swap 迭代轮数')
    ap.add_argument('--top', type=int, default=8, help='每张表打印多少行')
    ap.add_argument('--objective', default=OBJ, choices=_OBJ.VALID,
                    help='评分目标：total=合计（默认）｜pierce=穿刺桶｜pierce+=穿刺+20%%其他')
    ap.add_argument('--json', default='data/scratch/skill_opt.json')
    a = ap.parse_args()
    OBJ = a.objective

    cur = {k: v for k, v in BASE.items() if v > 0}
    b = ev(cur)
    free = unspent()
    print('形态 %-20s ｜ 角色 %s lv%s ｜ **评分目标 %s**'
          % (ARCH, CHAR, _C.get('level'), _OBJ.label(OBJ)))
    print('=== 基线（存档现状）===')
    print('  面板 %s ｜ 实战 %s ｜ ΣW=%s ｜ 默认攻击权重=%s ｜ 未分配技能点 %d'
          % (format(b[0], ',.0f'), format(b[1], ',.0f'), b[2], b[3], free))
    if not b[3]:
        print('  ⚠⚠ **默认攻击权重 = 0 ⇒ 主输出不出现（武器池被撑爆）**')
    print()

    if a.mode == 'add':
        gained, best = [], b
        for step in range(free):
            cand = None
            for rec in addable(cur):
                t = dict(cur)
                t[rec] += 1
                v = ev(t)
                if cand is None or _sc(v) > _sc(cand[0]):
                    cand = (v, rec)
            if cand is None:
                break
            v, rec = cand
            if _sc(v) <= _sc(best) + 0.5:
                print('  （没有能提高的加点了）')
                break
            prev = cur[rec]
            cur[rec] += 1
            best = v
            gained.append(rec)
            print('  %d) +%-22s %2d → %-2d ｜ 评分 %10s（%+.2f%%）｜ 面板 %s ｜ ΣW=%s 默认=%s'
                  % (step + 1, nm(rec), prev, cur[rec], format(_sc(v), ',.1f'),
                     _sc(v) / _sc(b) * 100 - 100, format(v[0], ',.0f'), v[2], v[3]))
        print()
        print('=== 结果 ===')
        print('  面板 %s → %s（%+.2f%%）｜ 用了 %d / %d 个未分配点'
              % (format(b[0], ',.0f'), format(best[0], ',.0f'),
                 best[0] / b[0] * 100 - 100, len(gained), free))
        json.dump({'mode': 'add', 'arch': ARCH, 'base': list(b[:4]),
                   'opt': list(best[:4]), 'added': [[r, nm(r)] for r in gained],
                   'skills': sorted(cur.items())},
                  open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('  已落 %s' % a.json)
        return 0

    def marginal(cc, base_v):
        rem, add = [], []
        for rec in removable(cc):
            if cc[rec] - 1 <= 0:
                continue
            t = dict(cc)
            t[rec] -= 1
            v = ev(t)
            rem.append((_sc(base_v) - _sc(v), rec))
        for rec in addable(cc):
            t = dict(cc)
            t[rec] += 1
            v = ev(t)
            add.append((_sc(v) - _sc(base_v), rec))
        rem.sort(reverse=True)
        add.sort(reverse=True)
        return rem, add

    cur_v = b
    swaps = []
    rounds = a.rounds if a.mode == 'swap' else 1
    for rnd in range(rounds):
        print('── 第 %d 轮 ｜ 评分 %s ｜ 面板 %s（ΣW=%s 默认=%s）'
              % (rnd + 1, format(_sc(cur_v), ',.1f'), format(cur_v[0], ',.0f'),
                 cur_v[2], cur_v[3]))
        rem, add = marginal(cur, cur_v)
        print('  ▸ 拆点损失**最大**（最不该拆；越靠下越便宜）')
        for loss, rec in rem[:a.top]:
            print('     −%-24s %2d 级 ｜ 损失 %9s（%+.2f%%）'
                  % (nm(rec), cur[rec], format(loss, ',.0f'),
                     -loss / _sc(cur_v) * 100 if _sc(cur_v) else 0))
        print('  ▸ 加点收益最大（补谁最值）')
        for gain, rec in add[:a.top]:
            print('     +%-24s %2d 级 ｜ 收益 %9s（%+.2f%%）'
                  % (nm(rec), cur[rec], format(gain, ',.0f'),
                     gain / _sc(cur_v) * 100 if _sc(cur_v) else 0))
        if a.mode == 'marginal':
            print()
            json.dump({'mode': 'marginal', 'arch': ARCH, 'base': list(b[:4]),
                       'remove': [[l, nm(r), cur[r]] for l, r in rem],
                       'add': [[g, nm(r), cur[r]] for g, r in add]},
                      open(a.json, 'w', encoding='utf-8'),
                      ensure_ascii=False, indent=1)
            print('  已落 %s' % a.json)
            return 0

        if not rem or not add:
            print('  （无可拆 / 可加技能）')
            break
        best_pair = None
        for gain, ra in add[:6]:
            for loss, rr in rem[:6]:
                if ra == rr:          # ⚠ 必须排除：同一技能既在 add 也在 rem
                    continue          #   （未到 max_level 且可拆点时会同时出现，
                                      #    旧版会把「−A/+A」当成一次换位 ⇒ 假收敛）
                net = gain - loss
                if best_pair is None or net > best_pair[0]:
                    best_pair = (net, ra, rr, loss, gain)
        if best_pair is None:
            print('  （无可配对）')
            break
        net, ra, rr, loss, gain = best_pair
        if net <= 0.5:
            print('  （最优 1-1 换位净收益 %s ≤ 0.5 ⇒ 收敛）' % format(net, ',.0f'))
            break
        cur[ra] += 1
        cur[rr] -= 1
        cur_v = ev(cur)
        swaps.append({'add': [ra, nm(ra), cur[ra]], 'remove': [rr, nm(rr), cur[rr]],
                      'net': net})
        print('  ★ 换位：−1 %s ／ +1 %s ｜ 净 %s ｜ 评分 %s（**%+.2f%%**）｜ 面板 %s'
              % (nm(rr), nm(ra), format(net, ',.0f'),
                 format(_sc(cur_v), ',.1f'), _sc(cur_v) / _sc(b) * 100 - 100,
                 format(cur_v[0], ',.0f')))
        print()

    # ---- 收敛后把**未分配的点**花掉（swap 只做 1-1 换位，不会用掉免费点）
    if free and a.mode == 'swap':
        print('── 花掉 %d 个未分配点' % free)
        for step in range(free):
            cand = None
            for rec in addable(cur):
                t = dict(cur)
                t[rec] += 1
                v = ev(t)
                if cand is None or _sc(v) > _sc(cand[0]):
                    cand = (v, rec)
            if cand is None:
                break
            v, rec = cand
            if _sc(v) <= _sc(cur_v) + 0.5:
                print('  （没有能提高的加点了）')
                break
            prev = cur[rec]
            gain = _sc(v) - _sc(cur_v)
            cur[rec] += 1
            cur_v = v
            swaps.append({'add': [rec, nm(rec), cur[rec]], 'remove': None,
                          'net': gain})
            print('  %d) +%-22s %2d → %-2d ｜ 面板 %10s（%+.2f%%）'
                  % (step + 1, nm(rec), prev, cur[rec], format(v[0], ',.0f'),
                     v[0] / b[0] * 100 - 100))
        print()

    print()
    print('=== 结果 ===')
    print('  面板 %s → %s（%+.2f%%）｜ 实战 %s → %s'
          % (format(b[0], ',.0f'), format(cur_v[0], ',.0f'),
             cur_v[0] / b[0] * 100 - 100, format(b[1], ',.0f'), format(cur_v[1], ',.0f')))
    # ★ 2026-09-22：**优化器真正用的目标是 `_sc()`（实战/含减抗层），不是面板** ——
    #   只印面板百分比会让人以为「收益很小」（实测同一轮：面板 +0.16% ／ 评分 +0.43%）。
    print('  ★ 评分（%s）%s → %s（**%+.2f%%**）← 优化器实际目标'
          % (_OBJ.label(OBJ), format(_sc(b), ',.1f'), format(_sc(cur_v), ',.1f'),
             _sc(cur_v) / _sc(b) * 100 - 100))
    print('  ΣW %s → %s ｜ 默认攻击权重 %s → %s' % (b[2], cur_v[2], b[3], cur_v[3]))
    if not cur_v[3]:
        print('  ⚠⚠ **默认攻击权重 = 0 ⇒ 主输出消失，方案不可用**')
    diff = {r: cur.get(r, 0) - BASE.get(r, 0) for r in set(cur) | set(BASE)
            if cur.get(r, 0) != BASE.get(r, 0)}
    print('  相对存档的改动：%s'
          % ('、'.join('%s %+d' % (nm(r), d) for r, d in sorted(diff.items()))
             or '（无）'))
    json.dump({'mode': a.mode, 'arch': ARCH, 'objective': OBJ,
               'base': list(b[:4]),
               'opt': list(cur_v[:4]), 'swaps': swaps,
               'skills': sorted(cur.items()), 'diff': sorted(diff.items())},
              open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('  已落 %s' % a.json)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
