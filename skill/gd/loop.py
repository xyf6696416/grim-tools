# -*- coding: utf-8 -*-
r"""属性 ↔ 装备 **收敛循环**（gd_loop）—— 2026-09-17 新增（v3 P2 收官）

解决的问题
----------
装备要属性才穿得上，属性又由「加点 + 精通 + **装备自带属性**」决定 —— 是**闭环**：

```
装备选择 ──决定能否穿上──> 属性需求
    ↑                          │
    └──装备自带属性加成────────┘
```

单向流程（先搜装备、再算加点）会选出「上一轮装备凑出来的属性」才够穿的件，
换一件就可能掉链子。本脚本把两头迭代到稳定：

```
① 无约束跑一次装备搜索
② 读方案 → gd_alloc.attribute_plan 算「属性点分配 + 面板」
③ 把面板当 GD_ATTR_BUDGET 闸门，重跑搜索（穿不上的件被剔除）
④ 面板不再变化 → 收敛，输出方案 + 加点 + 星座
```

用法
----
```bash
python gd_loop.py wereraven 65
python gd_loop.py werewolf 65 --max-iter 4 --beam 6000 --restart 3
```
"""
import argparse
import json
import os
import subprocess
import sys

from . import paths as _PATHS

# 旧脚本用 HERE 拼数据文件路径；新架构下数据在技能的 data/ 里
HERE = str(_PATHS.DATA_DIR)
_CACHE_ROOT = str(_PATHS.CACHE_DIR)
_PLANS = str(_PATHS.CACHE_DIR / 'plans')




def _py():
    cand = r'C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
    return cand if os.path.exists(cand) else sys.executable


def run_opt(arch, level, out, goal='worst', beam=3000, restart=2, budget=None, quiet=True):
    env = os.environ.copy()
    env.update({
        'GD_MAX_ILVL': str(level), 'GD_ARCHETYPE': arch, 'GD_NO_CUR': '1',
        'GD_NO_FACTION': '1', 'GD_SKILL_K': '2.0', 'GD_DEDUP': '4',
        'GD_FULL': '1', 'GD_OVER_PEN': '2.0', 'GD_DMG_TIE': '1.2',
        'GD_AUTO_TOPN': '70', 'GD_COMP_TOPN': '16', 'GD_AUG_TOPN': '10',
    })
    if budget:
        env['GD_ATTR_BUDGET'] = json.dumps(budget)
    cmd = [_py(), 'gd_opt.py', '--goal', goal, '--beam', str(beam),
           '--restart', str(restart), '--out', out]
    p = subprocess.run(cmd, cwd=HERE, env=env, capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    log = (p.stdout or '') + (p.stderr or '')
    return p.returncode == 0, log


def _run_opt(arch, level, out, **kw):
    ok, log = run_opt(arch, level, out, **kw)
    gate = ''
    for ln in log.splitlines():
        if '属性闸门' in ln:
            gate = ln.strip()
            break
    cover = ''
    for ln in log.splitlines():
        if '覆盖率' in ln:
            cover = ln.strip()
            break
    return ok, gate, cover, log


def gear_records(plan_path):
    from . import savemap as M
    from . import gear as G
    IT = G.load_items()
    plan = json.load(open(plan_path, encoding='utf-8'))
    out = {}
    for slot, row in plan.items():
        gid = row[0]
        rec, _ = M.resolve(IT.get(gid) or {})
        out[gid] = rec
    return out


def main():
    ap = argparse.ArgumentParser(description='属性↔装备 收敛循环')
    ap.add_argument('archetype')
    ap.add_argument('level', nargs='?', type=int, default=65)
    ap.add_argument('--max-iter', type=int, default=3)
    ap.add_argument('--beam', type=int, default=3000)
    ap.add_argument('--restart', type=int, default=2)
    ap.add_argument('--goal', default='worst')
    args = ap.parse_args()

    import gd_alloc as A
    out = os.path.join(HERE, 'plans', 'v3loop_%s.json' % args.archetype)
    panel_prev = None
    budget = None
    for i in range(1, args.max_iter + 1):
        print('=' * 78)
        print('第 %d 轮 ｜ 形态 %s ｜ 等级 %d ｜ 属性闸门 %s'
              % (i, args.archetype, args.level, budget or '（无，首轮）'))
        print('=' * 78)
        ok, gate, cover, log = _run_opt(args.archetype, args.level, out,
                                        goal=args.goal, beam=args.beam,
                                        restart=args.restart, budget=budget)
        if not ok:
            print('✗ 第 %d 轮搜索失败：' % i)
            print('\n'.join(log.strip().splitlines()[-5:]))
            return 1
        if gate:
            print('  ' + gate)
        if cover:
            print('  ' + cover)

        recs = gear_records(out)
        plan = A.attribute_plan(recs, args.level, 50, args.archetype)
        panel = {k: int(v) for k, v in plan['panel'].items()}
        print('  属性需求 %s ｜ 加点 %s ｜ 面板 %s'
              % ({k: v for k, v in plan['req'].items() if v}, plan['points'], panel))

        if panel == panel_prev:
            print()
            print('✓ 第 %d 轮面板与上一轮一致 → **收敛**' % i)
            break
        panel_prev = panel
        budget = panel
    else:
        print()
        print('⚠ 达到迭代上限 %d 轮仍未完全稳定（面板仍在微调）' % args.max_iter)

    print()
    print('=' * 78)
    print('最终产物')
    print('  装备方案 : %s' % out)
    print('  属性加点 : %s（预算 %d 点）' % (plan['points'], plan['budget']))
    print('  面板属性 : %s' % panel)
    print('  装备需求 : %s' % {k: v for k, v in plan['req'].items() if v})
    sk = A.allocate(args.archetype, args.level)
    print('  技能加点 : %d 条，用 %d/%d 点' % (len(sk['skills']), sk['used'], sk['budget']))
    try:
        from . import devotion as D
        dv = D.select_coherent(args.archetype, budget=A.devotion_budget(args.level))
        print('  星座     : %d 个星座 / %d 点 ｜ %s'
              % (len(dv['picks']), dv['used'], '亲和力自洽 ✓' if dv['coherent'] else '亲和力待复核'))
    except Exception as e:
        print('  星座     : 跳过（%s）' % e)
    print('=' * 78)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
