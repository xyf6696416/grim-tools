# -*- coding: utf-8 -*-
"""coordinate_ascent.py —— **四轴**坐标上升编排器（装备 / 技能 / 星座 / 属性）。

为什么要有它
------------
在它之前，四轴是**单向串行**的：`autobuild` 只搜装备，搜完把解**冻死**；
`tune_skills` / `tune_devotion` 是各自独立的手动脚本，跑完也不回喂给装备搜索；
**属性点压根不参与优化**（`gd/alloc.attribute_plan` 只按装备需求反推 + 余额按 bias 投）。
结果就是「模型只跑装备」——换一件装备本该重算技能与星座，实际没人重算。

做法：坐标上升
--------------
```
for 轮 in 1..N:
    for 轴 in axes:              # 默认 属性 → 技能 → 星座 →（可选）装备
        跑该轴的搜索（在**当前解**上）
        度量 → 若提升则接受，否则保持
    若本轮四轴增益都 < tol ⇒ 收敛
```
每轴都在**上一个轴的最优解**上继续，所以「换了装备之后技能/星座会重新收敛」。

⚠⚠ **口径纪律（务必遵守）**
    四轴各自的搜索 harness **不相同**：
      · 属性轴 → `tools/tune_attrs.py` → `plan_dps.dps_of`（星座混在技能里）
      · 装备轴 → `tools/autobuild.py` → 同一个 `plan_dps.dps_of` ✅ 与属性轴同口径
      · 技能轴 → `tools/tune_skills.py` → 走 `GD_SKILL_JSON` 注入
      · 星座轴 → `tools/tune_devotion.py` → **显式星座通道**（`levels` 去掉 `/devotion/`
        + 传 `devotion_levels`），比其余通道多算平伤/攻速/独立倍率
    两条通道同配置实测差 **+3.7%**（见 `docs/pitfalls.md` 专题）。
    ⇒ 本工具**只报逐轴增益**并标注通道，**绝不把不同通道的增益相加**。
      要「跨轴可比」的终评，用 `tools/eval_build_variants.py`（它自己统一了 harness）。

用法
----
```bash
PY=".../envs/default/Scripts/python.exe"
$PY tools/coordinate_ascent.py Sam --dry-run          # 只打印编排计划
$PY tools/coordinate_ascent.py Sam --axes attr        # 单轴（最快，3 秒）
$PY tools/coordinate_ascent.py Sam --axes skill,dev --rounds 2
$PY tools/coordinate_ascent.py Sam --axes attr,skill,dev,gear --rounds 3   # 全量（很慢）
```

★★ **单维度单独跑最优解**：`--axes` 写成单轴即可 —— 各轴互不干扰、各出各的报告
（`data/plans/final/ASCENT_<角色>_<轴>.md`，**不会互相覆盖**）。
各轴的**最优性等级不同，必须跟着数字一起看**：

| 轴 | 命令 | 最优性 | 说明 |
|---|---|---|---|
| 属性点 | `--axes attr` | **全局最优** | 可行域只有 `~预算²/2` 个组合（Sam 2,701）⇒ **全枚举**，16 进程 3~5 秒 |
| 技能点 | `--axes skill` | 局部最优 | 点数预算 × 几十个技能 ⇒ 组合爆炸，只能 1-1 换位到收敛 |
| 星座 | `--axes dev` | 启发式 | 贪心 + 拆点重排；有 best-seen 兜底（落盘永不更差） |
| 装备 | `--axes gear` | 启发式 | LNS 大邻域（`--chains N` 多链并行取优） |

⚠ 「局部最优 / 启发式」不等于「最优」—— 报数字时必须带上这一列（报告表头已内建）。
产出 `data/plans/final/ASCENT_<角色>_<轴标签>.md`。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, SKILL)
sys.path.insert(0, HERE)
os.environ.setdefault('GD_PROJ_HITS', '1')

import objfunc as _OBJ                       # noqa: E402
import plan_dps as PD                        # noqa: E402

PY = sys.executable
ZH = {'attr': '属性点', 'skill': '技能点', 'dev': '星座', 'gear': '装备'}
# 默认顺序：便宜的、确定性的先跑；最贵的装备放最后（且默认不跑）
DEFAULT_AXES = 'attr,skill,dev'
# 每轴的通道（见模块 docstring 的「口径纪律」）—— 报告里必须逐条标出来
CHANNEL = {
    'attr': 'plan_dps（星座混在技能里）',
    'gear': 'plan_dps（星座混在技能里）',
    'skill': 'GD_SKILL_JSON 注入',
    'dev': '显式星座通道（与上面两条**不可直接相减**）',
}
# ★ 每轴的**最优性等级** —— 「单独跑最优解」时这句必须跟着数字一起给，
#   否则「最优」会被当成「全局最优」。（2026-09-21 用户要求单维度可单独跑最优解）
OPTIMALITY = {
    'attr': '**全局最优**（可行域 ~预算²/2 ⇒ 全枚举）',
    'skill': '局部最优（1-1 换位到收敛）',
    'dev': '启发式（贪心 + 拆点重排，best-seen 兜底）',
    'gear': '启发式（LNS 大邻域搜索）',
}


# ------------------------------------------------------------------ 度量
_PCT = re.compile(r'([+-]?\d+\.\d+)\s*%')


def _axis_gain(text):
    """从子进程输出里抓该轴**自报**的增益百分比。

    ★ 为什么只能「自报」：四轴的搜索 harness 不同（星座走显式星座通道，与另两条
      差 +3.7%），**没有任何一个 `measure()` 能同时表达四个轴**。旧实现拿
      `measure()` 前后相减 ⇒ 那列**恒为 0**（`measure` 只读存档，读不到各轴产出的
      方案文件），把星座轴自报的 +4.80% 整个盖掉了。
    ⇒ 改成如实转述「该轴自己算出来的增益」，并把它锁在**该轴的口径**里。
    取输出里**最后**一个百分比 —— 各轴的总结论都印在末尾。
    """
    hits = _PCT.findall(text or '')
    return float(hits[-1]) if hits else None


def measure(char, arch=''):
    """当前**存档**下的一次评分。口径与 objfunc 一致。

    ⚠ 它**只反映存档**（不叠加各轴产出的方案文件）⇒ 用途是「全局起点」，
      不是「跑完某轴之后的状态」。各轴增益请用 `_axis_gain()` 读自报值。
    """
    r = PD.dps_of(char, arch=arch)
    return _OBJ.score(r), r


# ------------------------------------------------------------------ 各轴
def _resolve_plan(char, args):
    """四轴的**输入方案**：`--plan` 优先，否则 `data/plans/<char>_auto.json`
    （`gd auto` 的产物），再退回 `_current.json`（存档现状）。

    ★★ 不传会踩的坑（2026-09-22 实测）：`tune_skills` / `tune_devotion` 的 `PLAN`
      默认值是**写死的 Sam 文件**（`data/plans/Sam_lv73_current.json`）。给别的角色
      跑四轴时若不显式传，它们会**拿 Sam 的方案当装备覆盖**去算 —— 数字看着很正常
      （面板 89,217、评分 113,736）其实与该角色毫无关系（`_xyf` 真值 200,754）。
    """
    if getattr(args, 'plan', ''):
        return args.plan
    base = 'data/plans/%s' % char.lstrip('_')
    for _suf in ('_auto.json', '_current.json'):
        if os.path.isfile(os.path.join(SKILL, base + _suf)):
            return base + _suf
    return ''


def run_axis(ax, char, arch, args, log):
    """跑一个轴。返回 (增益字符串, 是否可能已改进)。"""
    plan = _resolve_plan(char, args)
    if ax == 'attr':
        # 函数级复用：同进程、同 harness，最快也最干净。
        # ★★ 用**全枚举**（`optimize_exact`）而不是局部挪移 —— 属性轴的可行域只有
        #   ~预算²/2 个组合（Sam：2,701），16 进程实测 **5 秒**跑完
        #   （577 次/秒）⇒ 没有理由退而求其次。这轴能给**可证明的全局最优**。
        import tune_attrs as TA
        out = TA.optimize_exact(char, arch=arch, jobs=int(args.jobs or 0),
                                plan_path=(plan or None), log=lambda *a: None)
        return ('枚举 %d 个组合 / %.0fs ｜ 评分 %.1f → %.1f ｜ 加点 %s'
                % (out['combos'], out['seconds'], out['score_start'], out['score'],
                   json.dumps(out['points'], ensure_ascii=False)),
                out['gain_pct'])
    env = dict(os.environ)
    env['CHAR'] = char
    if arch:
        env['ARCH'] = arch
    if plan:
        env['PLAN'] = plan            # ★ 见 `_resolve_plan`：不传就会吃到 Sam 的方案
    if ax == 'skill':
        cmd = [PY, os.path.join(HERE, 'tune_skills.py'),
               '--mode', 'swap', '--rounds', str(int(args.skill_rounds))]
        outs = ['data/scratch/skill_swap.json']
    elif ax == 'dev':
        cmd = [PY, os.path.join(HERE, 'tune_devotion.py'), '--realloc']
        outs = ['data/scratch/devotion_opt.json']
    elif ax == 'gear':
        cmd = [PY, os.path.join(HERE, 'autobuild.py'), char,
               '--chains', str(int(args.gear_chains))]
        # ★ 2026-09-22：装备轴的**搜索强度**默认跟普通 `gd auto` 一致；要给双持/武器向形态
        #   跑到与主线同口径，加 `--gear-extreme`（= `--extreme --with-weapon`）。
        #   不加的话装备轴的数字会**低于**主线那次 `gd auto --extreme --with-weapon`，
        #   看着像「装备轴没用」，其实只是池子小一圈。
        if getattr(args, 'gear_extreme', False):
            cmd += ['--extreme', '--with-weapon']
        outs = ['data/plans/%s_auto.json' % char.lstrip('_')]
    else:
        return ('未知轴', False)
    t0 = time.time()
    p = subprocess.run(cmd, cwd=SKILL, env=env, capture_output=True, text=True)
    tail = [x for x in (p.stdout or '').strip().splitlines() if x.strip()][-3:]
    ok = p.returncode == 0
    produced = [x for x in outs if os.path.isfile(os.path.join(SKILL, x))]
    return ('%s ｜ 用时 %.0fs ｜ 产物 %s ｜ %s'
            % ('✓' if ok else '✗ 退出码 %d' % p.returncode,
               time.time() - t0, produced or '无',
               ' ／ '.join(tail) if tail else ''),
            _axis_gain(p.stdout))


# ------------------------------------------------------------------ 主流程
def run(args):
    char = args.char
    lines = []
    t_all = time.time()

    def log(*a):
        msg = ' '.join(str(x) for x in a)
        lines.append(msg)
        print(msg, flush=True)

    axes = [x.strip() for x in (args.axes or DEFAULT_AXES).split(',') if x.strip()]
    bad = [x for x in axes if x not in ZH]
    if bad:
        raise SystemExit('✗ 未知轴 %s（可选 %s）' % (bad, ' / '.join(ZH)))

    # ★★ 形态必须在这里**定死**再往下传：`tune_skills` / `tune_devotion` 的
    #   `ARCH` 默认值是写死的 `wolf_nightblade_fast`，不显式传就会被它们套到
    #   任意角色头上（与「PLAN 默认是 Sam 的文件」是同一类坑，见 `_resolve_plan`）。
    arch = args.arch or PD.resolve_arch(char)
    if not args.arch and arch:
        print('  （未传 --arch，按职业组合判定为 **%s**）' % arch)
    args.arch = arch

    log('# 四轴坐标上升 —— %s' % char)
    log('')
    log('| 项 | 值 |')
    log('|---|---|')
    log('| 轴（顺序） | %s |' % ' → '.join('%s(%s)' % (ZH[a], a) for a in axes))
    log('| 各轴最优性 | %s |'
        % ' ／ '.join('%s = %s' % (ZH[a], OPTIMALITY.get(a, '—')) for a in axes))
    log('| 轮数上限 | %d |' % int(args.rounds))
    log('| 收敛阈值 | %g |' % float(args.tol))
    log('| 评分目标 | **%s** |' % _OBJ.label())
    log('| 防御权重 | `GD_DEF_WEIGHT=%s` |'
        % (os.environ.get('GD_DEF_WEIGHT') or '未设（关）'))
    log('')
    log('> ⚠ 四轴的搜索 harness 不同（星座那轴走**显式星座通道**，与其余差实测 **+3.7%**，')
    log('> 见 `docs/pitfalls.md` 专题）—— 下表**只报逐轴增益，不可相加**。')
    log('')

    s0, r0 = measure(char, args.arch)
    log('起点：评分 **%.1f** ｜ 面板 %.0f ｜ 含减抗 %.0f ｜ 形态 %s'
        % (s0, r0.get('dps_panel') or 0, r0.get('dps_vs') or 0, r0.get('arch')))
    log('')
    log('| 轮 | 轴 | 最优性 | 通道 | 结果 | 轴内增益（自报） |')
    log('|---|---|---|---|---|---|')

    if args.dry_run:
        log('| — | %s | — | — | `--dry-run`：只列计划，未执行 | — |'
            % ' → '.join(ZH[a] for a in axes))
        _dump(char, lines, args, t_all, s0)
        return

    for it in range(1, int(args.rounds) + 1):
        gains = []
        for ax in axes:
            detail, gain = run_axis(ax, char, args.arch, args, log)
            gains.append((ZH[ax], gain))
            log('| %d | %s | %s | %s | %s | %s |'
                % (it, ZH[ax], OPTIMALITY.get(ax, '—'), CHANNEL.get(ax, '—'), detail,
                   ('**%+.2f%%**' % gain) if gain is not None else '—（见产物）'))
        best_g = max([g for _n, g in gains if g is not None], default=0.0)
        log('')
        # ★ 收敛判据用**轴内自报增益**：若某轴还能提 4.8%，就不该判收敛。
        #   旧实现用 `measure()` 前后差 ⇒ 恒 0 ⇒ 永远第一轮就「收敛」（假收敛）。
        log('轮 %d 小结：各轴自报增益 %s —— **各自口径，不可相加**。'
            % (it, ' ／ '.join('%s %s' % (n, ('%+.2f%%' % g) if g is not None else '—')
                               for n, g in gains)))
        log('')
        if best_g <= float(args.tol) * 100.0:
            log('⇒ 本轮最大轴内增益 ≤ %.2f%%，**收敛**，提前结束。'
                % (float(args.tol) * 100.0))
            break

    _dump(char, lines, args, t_all, s0)


def _dump(char, lines, args, t_all, s0):
    lines.append('')
    lines.append('总用时 %.0f 秒。' % (time.time() - t_all))
    # ★ 报告名带**轴标签**：单轴跑（用户要的「单独跑某维度的最优解」）不该覆盖
    #   别的轴的报告 —— 旧写法恒定 `ASCENT_<char>.md`，跑完技能轴就把属性轴的结论冲掉了。
    axes = [x.strip() for x in (args.axes or DEFAULT_AXES).split(',') if x.strip()]
    tag = ('all' if axes == DEFAULT_AXES.split(',') else '-'.join(axes))
    name = 'ASCENT_%s_%s.md' % (char.lstrip('_'), tag)
    p = os.path.join(SKILL, 'data', 'plans', 'final', name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    print('\n  已落盘：data/plans/final/%s' % name)
    if s0:
        print('  全局起点（存档口径）：%.1f' % s0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('char', nargs='?', default='Sam')
    ap.add_argument('--axes', default=DEFAULT_AXES,
                    help='顺序敏感，逗号分隔（attr/skill/dev/gear）')
    ap.add_argument('--arch', default='')
    ap.add_argument('--plan', default='',
                    help='四轴的**输入方案**（默认 data/plans/<char>_auto.json，'
                         '没有则 <char>_current.json）。⚠ 不传会踩坑：tune_skills / '
                         'tune_devotion 的 PLAN 默认是**写死的 Sam 文件**')
    ap.add_argument('--rounds', type=int, default=2)
    ap.add_argument('--tol', type=float, default=1e-4)
    ap.add_argument('--attr-rounds', type=int, default=3)
    ap.add_argument('--jobs', type=int, default=0,
                    help='属性轴全枚举的并行进程数（默认物理核数）')
    ap.add_argument('--skill-rounds', type=int, default=2)
    ap.add_argument('--gear-chains', type=int, default=4)
    ap.add_argument('--gear-extreme', action='store_true',
                    help='装备轴用 `--extreme --with-weapon`（与主线 gd auto 同口径；'
                         '双持/武器向形态应当开）')
    ap.add_argument('--dry-run', action='store_true')
    run(ap.parse_args())


if __name__ == '__main__':
    main()
