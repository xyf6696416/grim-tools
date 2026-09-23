# -*- coding: utf-8 -*-
"""tune_attrs.py —— **属性点**调优（真实 DPS 目标）。

为什么需要它
------------
四轴里只有属性点**从来没有被优化过**：`gd/alloc.attribute_plan()` 只做「按装备需求
反推 + 把剩余点按形态 `attribute_bias` 投」。那是个**够用就行**的分配，不是最优分配
—— 余额投给谁（体格给生命/穿装备，狡诈给物理穿刺%，精神给元素）只有拿真实 DPS
称过才知道。

做法
----
1. `gd.reqfit.solve()` 给出**可穿底线** `need` 与预算 `budget`（装备需求是硬约束）；
2. 从 `points_after`（含 bias 余额）出发做**坐标上升**：对每对 (搬出, 搬入) 试挪
   `step` 点，`need` 不许破、总数不许超预算，取真实评分最高者，迭代到不再提升；
3. 评分走 `tools/objfunc.py::score` —— 与装备 / 星座 / 技能三轴**同一个口径**，
   所以 `GD_OBJ` / `GD_DEF_WEIGHT` 在这里同样生效（跨轴结论才可比）。

★ 单次评估 ≈ 2 s，一轮 18 次 ≈ 40 s；轮数默认 4。

用法
----
    PY tools/tune_attrs.py                       # 存档原装备
    PY tools/tune_attrs.py --plan data/plans/x.json
    PY tools/tune_attrs.py --steps 1,5,10 --rounds 6
    GD_OBJ=pierce PY tools/tune_attrs.py         # 换主轴
"""
from __future__ import annotations

import argparse
import json
import math
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

import plan_dps as PD                       # noqa: E402
import objfunc as _OBJ                      # noqa: E402
from gd import reqfit as _RF                # noqa: E402
from gd import alloc as _AL                 # noqa: E402

KEYS = ('physique', 'cunning', 'spirit')
ZH = {'physique': '体格', 'cunning': '狡诈', 'spirit': '精神'}
# ★★ 加点数 → `bio_override` 的换算基数（**踩过两次的坑**）：
#   `plan_dps.dps_of(bio_override=…)` 收的是**不含精通/装备的三围基础**，
#   也就是存档 `block_map[2]` 里那个值 —— 实测 Sam = `130/634/66`，
#   恰好等于 `50 + 8 × (10/73/2)`。
#   ⚠ 千万别拿 `reqfit` 的 `A + B × 点数` 去传：那是**最终面板**（477/1249.5/331），
#     `panel_attrs` 会再叠一遍精通与装备 ⇒ 面板虚高（实测 94,790 vs 正常 83,536）。
#   ⚠ 也别拿 `B` 反推基础 —— 狡诈的 `B = 8.16`（含装备 % 修正），不等于官方每点 8。
BASE = float(getattr(_AL, 'BASE_ATTR', 50) or 50)
PER = float(_AL.attr_per_point() or 8)
OUT = os.environ.get('ATTR_OUT', 'data/scratch/attr_opt.json')


def _plan_override(plan_path):
    if not plan_path:
        return None
    p = plan_path if os.path.isabs(plan_path) else os.path.join(SKILL, plan_path)
    ov = PD.plan_to_override(json.load(open(p, encoding='utf-8')))
    return ov or None


def evaluate(char, plan, arch, pts, A=None, B=None):
    """给定**加点数** → (评分, 报告)。

    ★★ 单位坑（**踩了两次**）：`plan_dps.dps_of(bio_override=…)` 收的是
      「**不含精通/装备的三围基础**」，不是加点数、也不是最终面板。
      实测口径：
        · 加点数 `10/73/2` → 基础 `130/634/66`（= `50 + 8 × 点数`）→ 面板 `477/1249.5/331`
        · 传错单位会**静默**算出一个假面板（实测 75,089 与 94,790，真值 83,536）
      `A` / `B` 只是留着做报告展示，**不参与换算**（`A` 是最终面板、`B` 含装备 % 修正）。
    """
    bio = {k: BASE + PER * int(pts[k]) for k in KEYS}
    r = PD.dps_of(char, plan, arch, bio_override=bio)
    return _OBJ.score(r), r


def optimize(char='Sam', plan=None, arch='', steps=(1, 5, 10), rounds=4,
             log=print, plan_path=None):
    """属性点坐标上升。返回 dict（含 points / 分数 / 轨迹 / 边际表）。

    `plan` 是**已转好的装备覆盖**（`plan_to_override` 的输出）；也可用
    `plan_path` 直接给方案 JSON 路径。
    """
    if plan is None and plan_path:
        plan = _plan_override(plan_path)
    from gd import dps as D
    gch = D.load_char(char)
    arch = arch or D.guess_arch(gch['skills']) or PD.arch_by_mastery(char) or 'werewolf'

    fit = _RF.solve(char, override=plan)
    # ★ 单位坑：`fit.need` 是**面板值**（体格 464…），不是点数；预算却是点数（85）。
    #   点数下限要按 `面板 = A + B × 点数` 反算（这正是 `reqfit` 内部 `pmin` 的算法，
    #   但它没透出来）。**不换算的话约束恒不成立 ⇒ 一次挪移都做不了**（实测踩到）。
    need_panel = {k: float((fit.need or {}).get(k) or 0) for k in KEYS}
    A = {k: float((fit.A or {}).get(k) or 0) for k in KEYS}
    B = {k: float((fit.B or {}).get(k) or 0) for k in KEYS}
    need = {}
    for k in KEYS:
        need[k] = (max(0, int(math.ceil((need_panel[k] - A[k]) / B[k]))) + 1
                   if (need_panel[k] and B[k] > 0) else 0)
    budget = int(fit.budget or 0)
    cur = {k: int((fit.points_after or {}).get(k) or 0) for k in KEYS}
    base = dict(cur)
    base_s, base_r = evaluate(char, plan, arch, cur, A, B)
    log('属性轴 ｜ %s lv%s ｜ 形态 %s ｜ 评分口径 %s'
        % (char, base_r.get('level'), arch, _OBJ.label()))
    log('  可穿底线（点数）need = %s ｜ 预算 %d ｜ 起点 %s'
        % ({ZH[k]: need[k] for k in KEYS}, budget,
           {ZH[k]: cur[k] for k in KEYS}))
    log('  起点评分 = %.1f（面板 %.0f ｜ 含减抗 %.0f）'
        % (base_s, base_r.get('dps_panel') or 0, base_r.get('dps_vs') or 0))

    trace = []
    best_s = base_s
    for it in range(1, int(rounds) + 1):
        moved = None
        for src in KEYS:
            for dst in KEYS:
                if src == dst:
                    continue
                for st in steps:
                    if cur[src] - st < need[src]:
                        continue
                    trial = dict(cur)
                    trial[src] -= st
                    trial[dst] += st
                    if sum(trial.values()) > budget:
                        continue
                    s, r = evaluate(char, plan, arch, trial, A, B)
                    if s > best_s * (1.0 + 1e-9):
                        moved = (src, dst, st, trial, s, r)
                        break
                if moved:
                    break
            if moved:
                break
        if not moved:
            log('  第 %d 轮：无可提升的挪移 ⇒ 收敛' % it)
            break
        src, dst, st, trial, s, r = moved
        log('  第 %d 轮：%s −%d → %s ⇒ %.1f（%+.2f%%）'
            % (it, ZH[src], st, ZH[dst], s, 100.0 * (s / base_s - 1.0)))
        trace.append({'round': it, 'from': src, 'to': dst, 'step': st,
                      'score': round(s, 2), 'gain_pct': round(100.0 * (s / base_s - 1), 3)})
        cur, best_s = trial, s

    # 逐一属性的边际（每 +5 点 / −5 点的评分变化）—— 给你看「谁值钱」
    marg = {}
    for k in KEYS:
        row = {}
        for d in (-5, 5, 10):
            t = dict(cur)
            t[k] += d
            if t[k] < need[k] or sum(t.values()) > budget:
                row['%+d' % d] = None
                continue
            s, _r = evaluate(char, plan, arch, t, A, B)
            row['%+d' % d] = round(s - best_s, 1)
        marg[ZH[k]] = row

    out = {'char': char, 'arch': arch, 'objective': _OBJ.mode_default(),
           'budget': budget, 'need': need, 'points_start': base,
           'points': cur, 'score_start': round(base_s, 2),
           'score': round(best_s, 2),
           'gain_pct': round(100.0 * (best_s / base_s - 1.0), 3),
           'trace': trace, 'marginal_5': marg,
           'need_points': need,
           'panel': {ZH[k]: round(A[k] + B[k] * cur[k], 1) for k in KEYS}}
    return out


# ================================================================ 精确模式（全枚举）
# 属性轴有个别的轴没有的优势：**可行域极小**。三个属性、点数总和固定 = 预算，
# 各自只需 ≥ 可穿底线 ⇒ 组合数 ≈ 预算²/2（Sam：85 ⇒ 约 2,700 个）。
# 于是「局部最优」这个借口不成立 —— 可以**全枚举拿真正的全局最优**。
#
# 唯一障碍是速度：`plan_dps.dps_of()` 每次都要 `load_char`（读存档 + 折叠装备 + 建库），
# 单次 ≈ 2 s ⇒ 2,700 次要 1.5 小时。但属性只影响 `panel_attrs → attr_damage_pct`
# 这一小段，其余全是常量 ⇒ 把前置预计算一次，之后单次 ≈ 0.2 s，再开满物理核并行。
class _FastEval(object):
    """预计算 `dps_of` 里**与属性无关**的全部前置，之后只换 `bio` 重跑 `final_report`。"""

    def __init__(self, char, plan=None, arch=''):
        from gd import dps as D, rotation as R, rr as RR, enemy as ENM
        from gd import skillprov as SP, procs as PR
        from gd.dps import resolve_enemy_armor as _rea
        self.R = R
        self.c = D.load_char(char, '', True, gear_override=(plan or None))
        c = self.c
        sk = c['skills']
        eff = dict(sk)
        for rec, extra in (c.get('skill_plus') or {}).items():
            if rec in eff:
                eff[rec] += extra
        self.eff = eff
        mast = {}
        for rec, lv in sk.items():
            if '_classtraining_' in rec:
                mast[os.path.basename(rec).replace('_classtraining_', '')
                     .replace('.dbr', '')] = lv
        for cls, extra in (c.get('mastery_plus') or {}).items():
            mast[cls] = mast.get(cls, 0) + extra
        self.mast = mast
        self.arch = arch or D.guess_arch(sk) or PD.arch_by_mastery(char) or 'werewolf'
        self.rr_pack, _, _ = RR.collect_char(c['folded'], eff, c['db'])
        self.eprof = ENM.get_profile(os.environ.get('GD_ENEMY_PROFILE') or None,
                                     c.get('level') or 100)
        self.e_arm, self.e_src = _rea(os.environ.get('GD_ENEMY_ARMOR') or None,
                                      self.eprof)
        self.eq_sk = SP.norm_equipped([x[1] for x in (c.get('item_skills') or [])])
        self.item_wps = PR.wps_pool(c.get('base_gids'))
        # ★★ 2026-09-22：武器构成 —— 属性轴也要过同一道门控（见 `gd/procs`）。
        self.weapon_st = PR.weapon_state_of(c)
        self.base_aps = c.get('base_aps') or 1.25

    def report(self, pts):
        R, c = self.R, self.c
        bio = {k: BASE + PER * int(pts[k]) for k in KEYS}
        attrs = R.panel_attrs(bio, self.mast, c.get('gear_flat') or {},
                              c.get('gear_pct') or {})
        apct = R.attr_damage_pct(attrs)
        return R.final_report(
            self.arch, self.eff, db=c['db'], folded=c['folded'],
            skill_records=list(self.eff), base_aps=self.base_aps, attr_pct=apct,
            conversions=c.get('conversions') or [],
            skill_mods=c.get('skill_mods') or {}, rr=self.rr_pack, attrs=attrs,
            level=c.get('level'), enemy=self.eprof, enemy_armor=self.e_arm,
            enemy_armor_src=self.e_src, equipped_sk=self.eq_sk,
            item_wps=self.item_wps, weapon_st=self.weapon_st)

    def score(self, pts):
        rep = self.report(pts)
        # ★★ 口径必须与 `plan_dps.dps_of` **逐位一致**：`objfunc` 的 `total` 读
        #   `rep['dps']`，而 `dps_of` 返回的 `dps` 是**含减抗的 `dps_vs`**（不是面板）。
        #   直接把 `final_report` 的返回喂进 `objfunc` 会让 `total` 退成**面板口径**
        #   ⇒ 两种模式的数字不可比（实测 83,536 vs 152,638）。这里补一层同形 shim。
        shim = {'dps': (rep.get('vs') or {}).get('dps_vs') or rep['dps'],
                'type_rows': rep.get('type_rows') or []}
        if os.environ.get('GD_DEF_WEIGHT'):
            try:
                from gd import defense as _DF
                shim['def_score'] = _DF.quick_score(self.c, self.c.get('db'),
                                                    da=rep.get('da'))
            except Exception:                                    # noqa: BLE001
                pass
        return _OBJ.score(shim)


_FAST = {}


def _w_init(char, plan, arch):
    _FAST['ev'] = _FastEval(char, plan, arch)


def _w_run(chunk):
    ev = _FAST['ev']
    best_s, best_p = None, None
    for pts in chunk:
        s = ev.score(pts)
        if best_s is None or s > best_s:
            best_s, best_p = s, pts
    return best_s, best_p


def _phys():
    try:
        import psutil
        n = psutil.cpu_count(logical=False)
        if n:
            return int(n)
    except Exception:                                            # noqa: BLE001
        pass
    return max(1, (os.cpu_count() or 4) // 2)


def _need_budget(fit):
    """`Fit` → (点数下限 need, 预算 budget)。★ `fit.need` 是**面板值**，要反算成点数。"""
    need = {}
    for k in KEYS:
        n = float((fit.need or {}).get(k) or 0)
        b = float((fit.B or {}).get(k) or 0)
        a = float((fit.A or {}).get(k) or 0)
        need[k] = (max(0, int(math.ceil((n - a) / b))) + 1) if (n and b > 0) else 0
    return need, int(fit.budget or 0)


def _combos(need, budget):
    """可行域枚举（点数投满 —— 属性只有增益、没有代价，所以最优必在 `sum == budget`）。"""
    out = []
    lo = need['physique']
    hi = budget - need['cunning'] - need['spirit']
    for p in range(lo, hi + 1):
        rest = budget - p
        for c in range(need['cunning'], rest - need['spirit'] + 1):
            s = rest - c
            if s >= need['spirit']:
                out.append({'physique': p, 'cunning': c, 'spirit': s})
    return out


def optimize_exact(char='Sam', plan=None, arch='', jobs=0, log=print,
                   plan_path=None, slice_n=0):
    """**全枚举**求属性轴的全局最优。`slice_n>0` 时只跑 1/slice_n（调试用）。"""
    if plan is None and plan_path:
        plan = _plan_override(plan_path)
    from gd import dps as D
    gch = D.load_char(char)
    arch = arch or D.guess_arch(gch['skills']) or PD.arch_by_mastery(char) or 'werewolf'
    fit = _RF.solve(char, override=plan)
    need, budget = _need_budget(fit)
    combos = _combos(need, budget)
    if slice_n > 1:
        combos = combos[::int(slice_n)]
    n = max(1, int(jobs or _phys()))
    n = min(n, max(1, len(combos)))
    log('精确模式 ｜ %s lv%s ｜ 形态 %s ｜ 口径 %s'
        % (char, gch.get('level'), arch, _OBJ.label()))
    log('  可穿底线（点数）%s ｜ 预算 %d ｜ 可行域 **%d** 个组合 ｜ 并行 %d 进程'
        % ({ZH[k]: need[k] for k in KEYS}, budget, len(combos), n))
    t0 = __import__('time').time()
    chunks = [combos[i::n] for i in range(n)]
    import concurrent.futures as CF
    ev = _FastEval(char, plan, arch)          # 主进程留一个，用于回填起点分
    base = {k: int((fit.points_after or {}).get(k) or 0) for k in KEYS}
    base_s = ev.score(base)
    best = None
    if n == 1:
        best = _w_run(combos)
    else:
        _w_init(char, plan, arch)
        with CF.ProcessPoolExecutor(max_workers=n, initializer=_w_init,
                                    initargs=(char, plan, arch)) as ex:
            for r in ex.map(_w_run, chunks):
                if r[0] is not None and (best is None or r[0] > best[0]):
                    best = r
    dt = __import__('time').time() - t0
    log('  枚举完成：%.0f 秒（%.0f 次/秒）' % (dt, len(combos) / max(dt, 1e-9)))
    log('  起点（bias 分配）%s = **%.1f**'
        % ({ZH[k]: base[k] for k in KEYS}, base_s))
    log('  全局最优 %s = **%.1f**（%+.3f%%）'
        % ({ZH[k]: best[1][k] for k in KEYS}, best[0],
           100.0 * (best[0] / base_s - 1.0) if base_s else 0.0))
    return {'char': char, 'arch': arch, 'objective': _OBJ.mode_default(),
            'mode': 'exact', 'budget': budget, 'need_points': need,
            'combos': len(combos), 'seconds': round(dt, 1),
            'points_start': base, 'score_start': round(base_s, 2),
            'points': best[1], 'score': round(best[0], 2),
            'gain_pct': round(100.0 * (best[0] / base_s - 1.0), 3) if base_s else 0.0,
            'panel': {ZH[k]: round(_fitpanel(fit, best[1], k), 1) for k in KEYS}}


def _fitpanel(fit, pts, k):
    return float((fit.A or {}).get(k) or 0) + float((fit.B or {}).get(k) or 0) * pts[k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('char', nargs='?', default='Sam')
    ap.add_argument('--plan', default='')
    ap.add_argument('--arch', default='')
    ap.add_argument('--steps', default='1,5,10')
    ap.add_argument('--rounds', type=int, default=4)
    ap.add_argument('--exact', action='store_true',
                    help='全枚举求全局最优（可行域 ~预算²/2，多核并行）')
    ap.add_argument('--jobs', type=int, default=0, help='并行进程数（默认物理核数）')
    ap.add_argument('--out', default=OUT)
    a = ap.parse_args()
    if a.exact:
        out = optimize_exact(a.char, arch=a.arch, jobs=a.jobs,
                             plan_path=a.plan or None)
        out['_out'] = a.out
    else:
        steps = tuple(int(x) for x in a.steps.split(',') if x.strip())
        out = optimize(a.char, arch=a.arch, steps=steps, rounds=a.rounds,
                       plan_path=a.plan or None)
        print()
        print('  最优加点 = %s ｜ 评分 %.1f（起点 %.1f，%+.2f%%）'
              % ({ZH[k]: out['points'][k] for k in KEYS}, out['score'],
                 out['score_start'], out['gain_pct']))
        print('  每 5 点的边际（评分变化）：')
        for k, row in (out['marginal_5'] or {}).items():
            print('    %-4s −5: %-9s +5: %-9s +10: %s'
                  % (k, row.get('-5'), row.get('+5'), row.get('+10')))
        out['_out'] = a.out
    p = os.path.join(SKILL, out['_out'])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(out, open(p, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('  已落盘：%s' % out['_out'])


if __name__ == '__main__':
    main()
