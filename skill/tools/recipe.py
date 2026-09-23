# -*- coding: utf-8 -*-
r"""recipe.py —— BD 配方 **v2**：一条命令从三要素跑到完整 BD（高自动化 CLI）。

v1（`gd/recipe.py`，2026-09-17）已**断链** —— 它 `cd data && python gd_opt.py`、
还要 `bd_gen.py`，这些在现有工具链里都不存在了（实跑第③步即崩）。

v2 的定位：**只做编排，不重写任何已有逻辑**。每个阶段都是对现有工具的一次子进程调用，
因此「改了底层工具，recipe 自动跟着变」，不需要在两处维护同一份实现。

    S0 预算+配方   gd/alloc.*_budget(lv) ← data/level_table.json（官方表，不读存档）
    S1 底子        --from-zero ⇒ tools/make_zero_char.py（临时存档目录，不碰真存档）
    S2 加点        每形态 ⇒ tools/make_alloc.py → data/scratch/alloc<lv>_<arch>.json
    S3 形态普查    并发跑 `gd auto --extreme --with-weapon` ⇒ 取最优形态（模型自己选，不问人）
    S4 四轴        tools/coordinate_ascent.py --axes attr,skill,dev[,gear]
    S5 交付        planreport（BD 报告）+ plan_cycle（**伤害循环文档，铁律**）+ plan_legal（合法自检）
    S6 落档        --apply 才跑（真存档角色专用）

★ **分阶段可续跑**：状态落 `data/plans/recipe/<key>/state.json`，成功的阶段默认跳过
  （`--force` 重跑）。这才是「减少反复编写原始代码」的关键 —— 改中间一步不用从头再来。

用法::

    PY=".../envs/default/Scripts/python.exe"

    # ① 职业组合（自动展开该组合的**全部**形态，并发跑、取最优）
    $PY -m gd recipe 8,10 75 --from-zero

    # ② 指定形态（跳过形态普查，省一半时间）
    $PY -m gd recipe wolf_necromancer 75 --from-zero

    # ③ 已有角色（不造从 0 角色）
    $PY -m gd recipe soldier_nightblade 75 --char _xyf

    # ④ 只看会跑什么 / 只看形态展开
    $PY -m gd recipe 8,10 75 --from-zero --dry-run
    $PY -m gd recipe 8,10 75 --list-forms

    # ⑤ 断点续跑 / 只补某段
    $PY -m gd recipe wolf_necromancer 75 --stages 4,5

产出：`data/plans/final/RECIPE_<key>.md`（总报告）+ `data/plans/recipe/<key>/`（中间产物与日志）
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(HERE))

from gd import paths as _P                       # noqa: E402

PLANS = SKILL / 'data' / 'plans'
FINAL = PLANS / 'final'
SCRATCH = SKILL / 'data' / 'scratch'
ZERO_SAVE = SCRATCH / 'zero_save' / 'save'       # GD_SAVE 目标（临时，绝不等于真存档）
GOALS = {'balanced': ('worst', {'GD_FULL': '1', 'GD_OVER_PEN': '1.0',
                                'GD_DMG_TIE': '1.2', 'GD_DEDUP': '4'}),
         'tank': ('worst', {'GD_FULL': '1', 'GD_OVER_PEN': '2.0',
                            'GD_DMG_TIE': '1.2', 'GD_DEDUP': '4'}),
         'dmg': ('dmg', {})}


# ------------------------------------------------------------------ 基础
def _py() -> str:
    """统一用托管 venv（默认 env 才装了 numpy；用 versions/ 里的会静默退纯 Python）"""
    c = (r'C:\Users\Administrator\.workbuddy\binaries\python\envs\default'
         r'\Scripts\python.exe')
    return c if os.path.exists(c) else sys.executable


_CREATIONFLAGS = 0


def _set_low_priority(on: bool):
    """把之后 spawn 的子进程设为低于正常优先级（优先级会被孙进程继承）。"""
    global _CREATIONFLAGS
    _CREATIONFLAGS = 0
    if on and os.name == 'nt':
        _CREATIONFLAGS = getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0x00004000)
    return _CREATIONFLAGS


def _phys() -> int:
    try:
        import psutil                                   # noqa: F401
        return psutil.cpu_count(logical=False) or 16
    except Exception:                                   # noqa: BLE001
        return max(1, (os.cpu_count() or 16) // 2)


def load_archs() -> dict:
    return json.loads((SKILL / 'data' / 'archetypes.json').read_text(encoding='utf-8'))


def slug(s: str) -> str:
    return re.sub(r'[^0-9A-Za-z_]+', '', str(s).replace(',', '_'))


def is_classes(spec: str) -> bool:
    return bool(re.fullmatch(r'\d+(,\d+)*', spec.strip()))


def forms_for(spec: str, archs: dict, only: str = '') -> list:
    """spec = 形态键 或 职业组合 `8,10` ⇒ 展开成候选形态列表。

    ★ 「模型自己出最优形态」= 展开**同职业组合的全部形态**并发跑、按真实 DPS 取最优，
      而不是让人先选。`--forms` 可显式收窄。
    """
    if only:
        return [x.strip() for x in only.split(',') if x.strip()]
    if not is_classes(spec):
        return [spec.strip()]
    want = {int(x) for x in spec.split(',') if x.strip()}
    out = []
    for k, v in archs.items():
        ids = {m[0] for m in (v.get('masteries') or [])}
        if ids == want and not k.endswith('_fast'):      # `_fast` 是变体，不参与普查
            out.append(k)
    return sorted(out)


def budget_of(level: int) -> dict:
    """点数预算 —— **只按等级算，不读存档**（`data/level_table.json` 是官方表）"""
    import gd_alloc as A
    return {'level': int(level), 'skill': int(A.skill_budget(level)),
            'attr': int(A.attr_budget(level)), 'devotion': int(A.devotion_budget(level)),
            'attr_per_point': A.attr_per_point()}


def learn_for(arch: dict) -> list:
    """该形态要在从 0 角色上「先学出来」的技能（形态本体 + 它授予的攻击技）。

    ★★ 不学的话：形态门控（陷阱 #78）按 `root_skills[0].granted` 裁技能栏，
      而没学过 `werewolf1`/`wereraven1` 的角色**一个攻击都不剩 ⇒ DPS 恒为 0**，
      变身形态之间就比不出优劣（实测踩到）。
    """
    roots = arch.get('root_skills') or []
    if not roots:
        return []
    base = roots[0].split('/')[-1].replace('.dbr', '')
    out = {base}
    for r in (arch.get('core_skills') or []):
        b = r.split('/')[-1].replace('.dbr', '')
        if b == base or b.startswith(base + '_'):
            out.add(b)
    return sorted(out)


# ------------------------------------------------------------------ 状态（可续跑）
class State:
    def __init__(self, key: str, force: bool = False):
        self.key = key
        self.dir = PLANS / 'recipe' / key
        self.dir.mkdir(parents=True, exist_ok=True)
        self.f = self.dir / 'state.json'
        self.d = json.loads(self.f.read_text(encoding='utf-8')) if self.f.exists() else {}
        self.d.setdefault('key', key)
        self.d.setdefault('stages', {})
        self.force = force

    def done(self, stage: str) -> bool:
        s = self.d['stages'].get(stage) or {}
        return bool(s.get('ok')) and not self.force

    def put(self, stage: str, **kw):
        s = self.d['stages'].setdefault(stage, {})
        s.update(kw)
        self.save()

    def save(self):
        self.f.write_text(json.dumps(self.d, ensure_ascii=False, indent=1),
                          encoding='utf-8')

    def get(self, stage: str, k, default=None):
        return (self.d['stages'].get(stage) or {}).get(k, default)


# ------------------------------------------------------------------ 子进程
def run(cmd, log_path=None, env_extra=None, quiet=False):
    """跑一个子进程；日志落盘 + 回显尾部。返回 (rc, 完整输出)。"""
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'      # ★ 免得子进程按 GBK 输出、遇非 ASCII 就崩
    env.update(env_extra or {})
    t0 = time.time()
    p = subprocess.run(cmd, cwd=str(SKILL), env=env, capture_output=True,
                       text=True, encoding='utf-8', errors='replace',
                       creationflags=_CREATIONFLAGS)
    out = (p.stdout or '') + (p.stderr or '')
    el = time.time() - t0
    if log_path:
        pathlib.Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write('$ %s\n\n%s' % (' '.join(cmd), out))
    if not quiet:
        print('    ⏱ %.1f s ｜ rc=%d ｜ %s' % (el, p.returncode,
                                              ' '.join(cmd[:4]) + ' …'))
    return p.returncode, out, el


def _tail(text, n=3):
    ls = [x for x in (text or '').splitlines() if x.strip()]
    return ' ／ '.join(ls[-n:])


# ------------------------------------------------------------------ 各阶段
def s1_base(char, cfg, st: State, log):
    """S1 底子：从 0 造角色（临时存档目录）或复用现有角色"""
    if not cfg['from_zero']:
        log('S1 底子    复用现有角色 `%s`（未指定 --from-zero）' % char)
        return True, {'char': char, 'from_zero': False}
    learn = sorted({x for f in cfg['forms'] for x in learn_for(cfg['archs'][f])})
    cmd = [_py(), 'tools/make_zero_char.py', '--level', str(cfg['level']),
           '--classes', ','.join(str(c) for c in cfg['classes']),
           '--name', char, '--out', str(ZERO_SAVE.parent)]
    if learn:
        cmd += ['--learn', ','.join(learn)]
    rc, out, el = run(cmd, st.dir / 'logs' / 's1_make_zero.log')
    if rc != 0:
        return False, {'error': _tail(out)}
    log('S1 底子    从 0 角色 `%s` lv%d class%s ｜ 形态技能学入 %d 条'
        % (char, cfg['level'], cfg['classes'], len(learn)))
    return True, {'char': char, 'from_zero': True, 'zero_save': str(ZERO_SAVE),
                  'learned': learn, 'seconds': round(el, 1)}


def s2_alloc(cfg, st: State, log):
    """S2 加点：每个候选形态一份加点 JSON（供 `GD_SKILL_JSON` 注入）"""
    import time as _t2
    t0 = _t2.time()
    outs = {}
    for f in cfg['forms']:
        o = SCRATCH / ('alloc%d_%s.json' % (cfg['level'], f))
        if o.exists() and not st.force:
            outs[f] = str(o.relative_to(SKILL))
            continue
        rc, out, el = run([_py(), 'tools/make_alloc.py', f, str(cfg['level']),
                           '--out', str(o)], st.dir / 'logs' / ('s2_alloc_%s.log' % f))
        if rc != 0:
            log('    ✗ %s 加点失败：%s' % (f, _tail(out, 2)))
            continue
        outs[f] = str(o.relative_to(SKILL))
    log('S2 加点    %d/%d 个形态有加点' % (len(outs), len(cfg['forms'])))
    return bool(outs), {'plans': outs, 'seconds': round(_t2.time() - t0, 1)}


def s3_sweep(char, cfg, st: State, log, env_extra):
    """S3 形态普查：并发跑 `gd auto --extreme --with-weapon`，按真实 DPS 取最优"""
    if len(cfg['forms']) == 1:
        f = cfg['forms'][0]
        plan = PLANS / ('arch_%s.json' % f)
        if plan.exists() and not st.force:
            log('S3 形态普查 只有 1 个形态，复用已有方案 %s' % plan.name)
            return True, {'winner': f, 'rows': [{'arch': f, 'plan': str(plan)}],
                          'cached': True}
        cmd = [_py(), '-m', 'gd', 'auto', char, '--extreme', '--with-weapon',
               '--archetype', f, '--out', str(plan)]
        cmd += ['--procs', str(max(1, cfg['budget'] - 1))] if cfg['budget'] else []
        rc, out, el = run(cmd, st.dir / 'logs' / ('s3_%s.log' % f), env_extra)
        ok = rc == 0
        return ok, ({'winner': f, 'rows': [{'arch': f, 'plan': str(plan),
                                            'seconds': round(el, 1)}]}
                    if ok else {'error': _tail(out)})
    cmd = [_py(), 'tools/sweep_arch.py', char, '--archs', ','.join(cfg['forms']),
           '--budget', str(cfg['budget']), '--iters', str(cfg['iters']),
           '--alloc-tpl', 'data/scratch/alloc%d_{arch}.json' % cfg['level'],
           '--proj', '1', '--extra=--with-weapon',
           '--out-dir', str(st.dir / 'plans'), '--log-dir', str(st.dir / 'logs')]
    rc, out, el = run(cmd, st.dir / 'logs' / 's3_sweep.log', env_extra)
    if rc != 0:
        return False, {'error': _tail(out)}
    sw = json.loads((st.dir / 'plans' / '_sweep.json').read_text(encoding='utf-8'))
    rows = [r for r in sw['rows'] if (r.get('dps') or 0) > 0]
    if not rows:
        # 全 0 ⇒ 典型原因是「从 0 角色没学形态技能」，报清楚而不是给个 0 分最优
        return False, {'error': '所有形态真实 DPS 均为 0 —— 检查 S1 的 --learn / S2 加点',
                       'rows': sw['rows']}
    rows.sort(key=lambda r: -r['dps'])
    log('S3 形态普查 %d 形态并发 ｜ 墙钟 %.1f s ｜ 最优 **%s** %.0f DPS'
        % (len(cfg['forms']), sw['wall'], rows[0]['arch'], rows[0]['dps']))
    for r in rows:
        log('              %-22s %10s DPS ｜ %5.1f s' % (r['arch'],
                                                         '{:,.0f}'.format(r['dps']),
                                                         r.get('elapsed') or 0))
    return True, {'winner': rows[0]['arch'], 'rows': rows, 'wall': round(sw['wall'], 1),
                  'sum_elapsed': sw.get('sum_elapsed')}


def s4_axes(char, cfg, st: State, log, env_extra):
    """S4 四轴坐标上升（赢家形态）"""
    axes = str(cfg['axes'] or '').strip()
    if axes in ('', 'none', 'skip', '-'):
        log('S4 四轴    跳过（--axes none）')
        return True, {'skipped': True}
    win = st.get('S3', 'winner', cfg['forms'][0])
    plan = st.dir / 'plans' / ('arch_%s.json' % win)
    if not plan.exists():
        plan = PLANS / ('arch_%s.json' % win)
    cmd = [_py(), 'tools/coordinate_ascent.py', char, '--axes', axes, '--arch', win,
           '--plan', str(plan)]
    if 'gear' in axes:
        cmd += ['--gear-extreme']
    rc, out, el = run(cmd, st.dir / 'logs' / 's4_axes.log', env_extra)
    gains = re.findall(r'自报增益\s*(.*)', out)
    log('S4 四轴    %s ｜ %.0f s ｜ %s' % (axes, el, (gains[-1] if gains else '').strip()))
    return rc == 0, {'axes': axes, 'seconds': round(el, 1),
                     'summary': (gains[-1].strip() if gains else ''), 'rc': rc}


def s5_deliver(char, cfg, st: State, log, env_extra):
    """S5 交付：BD 报告 + **伤害循环文档**（铁律）+ 合法自检"""
    win = st.get('S3', 'winner', cfg['forms'][0])
    plan = st.dir / 'plans' / ('arch_%s.json' % win)
    if not plan.exists():
        plan = PLANS / ('arch_%s.json' % win)
    alloc = (st.get('S2', 'plans') or {}).get(win) or ''
    key = st.key
    arts = {}

    bd = FINAL / ('BUILD_%s.md' % key)
    rc1, o1, _ = run([_py(), '-m', 'gd', 'tool', 'planreport', str(plan),
                      '--char', char, '--archetype', win, '--level', str(cfg['level']),
                      '--out', str(bd)], st.dir / 'logs' / 's5_report.log', env_extra)
    arts['build_report'] = str(bd.relative_to(SKILL)) if rc1 == 0 else ''

    cyc = FINAL / ('CYCLE_%s.md' % key)          # ★ 每出新 BD 必带
    ccmd = [_py(), 'tools/plan_cycle.py', char, str(plan), '--arch', win,
            '--out', str(cyc)]
    if alloc:
        ccmd += ['--alloc', alloc]
    rc2, o2, _ = run(ccmd, st.dir / 'logs' / 's5_cycle.log', env_extra)
    arts['cycle_doc'] = str(cyc.relative_to(SKILL)) if rc2 == 0 else ''

    rc3, o3, _ = run([_py(), 'tools/plan_legal.py', '--char', char,
                      '--level', str(cfg['level']), str(plan)],
                     st.dir / 'logs' / 's5_legal.log', env_extra)
    legal_bad = [x for x in o3.splitlines() if '✗' in x or '等级不够' in x]
    arts['legal_ok'] = (rc3 == 0 and not legal_bad)
    if legal_bad:
        arts['legal_issues'] = legal_bad[:6]

    log('S5 交付    报告 %s ｜ 循环文档 %s ｜ 合法自检 %s'
        % ('✓' if arts['build_report'] else '✗',
           '✓' if arts['cycle_doc'] else '✗',
           '✓' if arts['legal_ok'] else '✗'))
    return bool(arts['build_report'] and arts['cycle_doc']), arts


STAGES = {'1': s1_base, '2': s2_alloc, '3': s3_sweep, '4': s4_axes, '5': s5_deliver}


# ------------------------------------------------------------------ 横向普查
def mastery_names() -> dict:
    ms = json.loads((SKILL / 'data' / 'mastery_skills.json').read_text(encoding='utf-8'))
    out = {}
    for v in ms.values():
        if v.get('kind') == 'mastery':
            out[int(v['class'][5:])] = v.get('name') or v['class']
    return out


def all_with(a, archs, level, log):
    """固定职业 × 其余全部职业 → **一个个组合都造角色、并发普查** → 排名。

    ★ 为什么不能让 `sweep_arch` 一把跑完 27 个形态：`sweep_arch` 只吃**一个角色**，
      而每个职业组合都需要自己的 from-zero 角色（精通条不同）⇒
      必须「每组合一个角色」，再把 9 个 sweep **并发**起起来。
    """
    names = mastery_names()
    fixed = int(a.all_with)
    pairs = [(fixed, x) for x in sorted(names) if x != fixed]
    if a.dry_run:
        print('横向普查计划 ｜ %s × 以下 %d 个职业 ｜ lv%d ｜ 底子 %s'
              % (names[fixed], len(pairs), level,
                 '从 0 造' if a.from_zero else '现有角色（⚠ 需要 9 个不同职业组合的角色，'
                                              '建议加 --from-zero）'))
        for (x, y) in pairs:
            fs = [k for k in forms_for('%d,%d' % (x, y), archs)
                  if k.startswith('b%dc%02d_' % (fixed, y))]
            print('  %-22s 形态 %-2d ｜ %s' % ('%s+%s' % (names[x], names[y]),
                                               len(fs), ', '.join(fs) or '（无条目）'))
        print('  ⇒ 并发起 %d 个 sweep，每形态 1 进程 ｜ LNS %d 轮' % (len(pairs), a.iters))
        print('（--dry-run：未执行）')
        return 0
    key = 'allwith%d_lv%d%s' % (fixed, level, '_zero' if a.from_zero else '')
    st = State(key, force=a.force)
    st.dir.joinpath('plans').mkdir(parents=True, exist_ok=True)
    st.dir.joinpath('logs').mkdir(parents=True, exist_ok=True)
    # ★★ 必须**从 os.environ 派生**：第一版只放了 GD_* 几个变量 ⇒ 丢掉
    #   `PYTHONIOENCODING` / PATH 等 ⇒ 子进程按 Windows 本地编码（GBK）输出，
    #   一遇到 `⇒` 这种字符直接 UnicodeEncodeError 崩掉（9 个 sweep 全 rc=1，3 秒就退）。
    env = os.environ.copy()
    env['PYTHONIOENCODING'] = 'utf-8'
    env.update(GOALS[a.goal][1])
    if a.from_zero:
        env['GD_SAVE'] = str(ZERO_SAVE)

    log('=' * 86)
    log('横向普查 ｜ %s × 其余 %d 个职业 ｜ lv%d ｜ 目标 %s ｜ 底子 %s'
        % (names[fixed], len(pairs), level, a.goal,
           '从 0 造' if a.from_zero else '现有角色'))
    log('=' * 86)

    def _res_path(x, y):
        return st.dir / ('p%d_%d' % (x, y)) / 'plans' / '_sweep.json'

    def _res_rows(x, y, char):
        try:
            sw = json.loads(_res_path(x, y).read_text(encoding='utf-8'))
        except Exception:                                      # noqa: BLE001
            return []
        return [{**r, 'a': x, 'b': y, 'pair': '%s+%s' % (names[x], names[y]),
                 'char': char} for r in (sw.get('rows') or []) if (r.get('dps') or 0) > 0]

    jobs = []
    done = []                    # ★ 已跑过的组合：直接复用结果（**逐组合续跑**）
    done_pairs = set()
    if not a.force:
        for (x, y) in pairs:
            _c = '_R%d_%d_L%d' % (x, y, level)
            _rs = _res_rows(x, y, _c)
            if _rs:
                log('  = %-16s %s ｜ 复用已有结果（%d 形态）；`--force` 可重跑'
                    % ('%s+%s' % (names[x], names[y]), _c, len(_rs)))
                done += _rs
                done_pairs.add((x, y))
    _todo = [(x, y) for (x, y) in pairs if (x, y) not in done_pairs]
    if a.batch and a.batch > 0:
        _todo = _todo[:int(a.batch)]
    if jobs or _todo:
        pass
    for (x, y) in pairs:
        if (x, y) not in _todo:
            continue
        # ★ 只取 **机械派生**那批（`b<固定>c<副>_*`）：{10,4}/{10,8} 还挂着历史**手工调过**的
        #   条目（avalanche 等），混进来会让这两个组合凭空多几倍候选 ⇒ 排名不公平。
        forms = [k for k in forms_for('%d,%d' % (x, y), archs)
                 if k.startswith('b%dc%02d_' % (fixed, y))]
        if not forms:
            log('  ✗ %s+%s 无形态条目，跳过（先跑 tools/make_archetypes.py --all-with %d）'
                % (names[x], names[y], fixed))
            continue
        char = '_R%d_%d_L%d' % (x, y, level)
        learn = sorted({t for f in forms for t in learn_for(archs[f])})
        if a.from_zero:
            cmd = [_py(), 'tools/make_zero_char.py', '--level', str(level),
                   '--classes', '%d,%d' % (x, y), '--name', char,
                   '--out', str(ZERO_SAVE.parent)]
            if learn:
                cmd += ['--learn', ','.join(learn)]
            rc, out, _ = run(cmd, st.dir / 'logs' / ('s1_%d_%d.log' % (x, y)), env,
                             quiet=True)
            if rc != 0:
                log('  ✗ %s+%s 造角色失败' % (names[x], names[y]))
                continue
        allocs = {}
        for f in forms:
            o = SCRATCH / ('alloc%d_%s.json' % (level, f))
            if not o.exists() or a.force:
                rc, out, _ = run([_py(), 'tools/make_alloc.py', f, str(level),
                                  '--out', str(o)],
                                 st.dir / 'logs' / ('s2_%s.log' % f), env, quiet=True)
            if o.exists():
                allocs[f] = str(o.relative_to(SKILL))
        log('  ✓ %-16s %s ｜ 形态 %d ｜ 加点 %d'
            % ('%s+%s' % (names[x], names[y]), char, len(forms), len(allocs)))
        jobs.append((x, y, char, forms))

    if not jobs and not done:
        log('✗ 没有任何可跑的组合')
        return 1
    # ★ 每形态 1 进程：`sweep_arch` 内部 `per = budget // len(forms)`
    # ★ 资源配额：`--budget` 是**总进程上限**（默认物理核）。原来硬编码「每形态 1 进程」
    #   ⇒ 9 组 × 3 形态 = 27 进程，在 16 核机上把机器打满（用户实测反馈「占用太夸张」）。
    _per_form = 1
    if a.budget and jobs:
        _per_form = max(1, int(a.budget) // max(1, len(jobs) * len(jobs[0][3])))
    per_budget = max(1, len(jobs[0][3]) * _per_form) if jobs else 3
    log('S3 并发起 %d 个 sweep（每形态 %d 进程 ⇒ 合计 %d 进程，LNS %d 轮，上限 %d）'
        % (len(jobs), _per_form, len(jobs) * per_budget, a.iters, a.budget or _phys()))
    t0 = time.time()
    procs = []
    for (x, y, char, forms) in jobs:
        d = st.dir / ('p%d_%d' % (x, y))
        d.joinpath('plans').mkdir(parents=True, exist_ok=True)
        d.joinpath('logs').mkdir(parents=True, exist_ok=True)
        cmd = [_py(), 'tools/sweep_arch.py', char, '--archs', ','.join(forms),
               '--budget', str(per_budget), '--iters', str(a.iters),
               '--alloc-tpl', 'data/scratch/alloc%d_{arch}.json' % level,
               '--proj', '1', '--extra=--with-weapon',
               '--out-dir', str(d / 'plans'), '--log-dir', str(d / 'logs')]
        f = open(st.dir / 'logs' / ('s3_%d_%d.log' % (x, y)), 'w', encoding='utf-8')
        procs.append((x, y, char, forms, f,
                      subprocess.Popen(cmd, cwd=str(SKILL), env=env, stdout=f,
                                       stderr=subprocess.STDOUT,
                                       creationflags=_CREATIONFLAGS)))
    while any(p.poll() is None for *_, p in procs):
        time.sleep(1.0)
    wall = time.time() - t0
    for *_, f, p in procs:
        f.close()

    rows = list(done)
    for (x, y, char, forms, _f, p) in procs:
        sp = st.dir / ('p%d_%d' % (x, y)) / 'plans' / '_sweep.json'
        if not sp.exists():
            log('  ✗ %s+%s 没有产物（rc=%d）' % (names[x], names[y], p.returncode))
            continue
        sw = json.loads(sp.read_text(encoding='utf-8'))
        for r in sw.get('rows') or []:
            if (r.get('dps') or 0) > 0:
                rows.append({**r, 'a': x, 'b': y, 'pair': '%s+%s' % (names[x], names[y]),
                             'char': char})
    if not rows:
        log('✗ 全部组合 DPS 为 0（检查加点/形态技能）')
        return 1
    rows.sort(key=lambda r: -r['dps'])
    best_by_pair = {}
    for r in rows:
        best_by_pair.setdefault(r['pair'], r)
    log('')
    log('排名（每组合取它的最优形态）｜ 并发墙钟 **%.1f s**' % wall)
    log('%-4s %-28s %-24s %10s' % ('#', '组合', '最优形态', '真实 DPS'))
    for i, r in enumerate(sorted(best_by_pair.values(), key=lambda z: -z['dps']), 1):
        log('%-4d %-28s %-24s %10s' % (i, r['pair'], r['arch'],
                                       '{:,.0f}'.format(r['dps'])))
    st.put('S3', ok=True, rows=rows, wall=round(wall, 1), pairs=len(jobs),
           best=rows[0]['arch'], winner=rows[0]['arch'], seconds=round(wall, 1))
    st.d['char'] = rows[0]['char']
    st.d['all_with'] = fixed
    st.save()
    rep = write_compare(st, rows, names, level, a, wall)
    log('-' * 86)
    log('报告 %s' % rep)
    return 0


def write_compare(st, rows, names, level, a, wall):
    best = {}
    for r in rows:
        best.setdefault(r['pair'], r)
    L = ['# 横向普查 —— %s + 其余全部职业（lv%d 从 0）' % (names[a.all_with], level), '',
         '> 生成 %s ｜ **同一套口径**：从 0 造角色 / 同预算 / `--extreme --with-weapon` /'
         ' LNS %d 轮 / proj=1 / **每形态 1 进程** ｜ 并发墙钟 **%.1f s**'
         % (time.strftime('%Y-%m-%d %H:%M'), a.iters, wall), '',
         '> ⚠ 形态条目是 `tools/make_archetypes.py` **机械派生**的（未人工校准）；'
         '每形态只给 1 个进程 ⇒ 是**粗筛排名**，不是各组合的上限。', '',
         '## 排名（每组合取其最优形态）', '',
         '| # | 组合 | 最优形态 | 真实 DPS | 相对第一 |', '|---|---|---|---|---|']
    top = max(r['dps'] for r in best.values())
    for i, r in enumerate(sorted(best.values(), key=lambda z: -z['dps']), 1):
        L += ['| %d | **%s** | `%s` | **%s** | %.1f%% |'
              % (i, r['pair'], r['arch'], '{:,.0f}'.format(r['dps']),
                 100.0 * r['dps'] / top)]
    L += ['', '## 全部形态明细（按 DPS 降序）', '',
          '| 组合 | 形态 | 真实 DPS | 耗时 |', '|---|---|---|---|']
    for r in rows:
        L += ['| %s | `%s` | %s | %s |'
              % (r['pair'], r['arch'], '{:,.0f}'.format(r['dps']),
                 ('%.1f s' % r['elapsed']) if r.get('elapsed') else '—')]
    L += ['', '## 口径与局限', '',
          '- 形态条目机械派生：主职业全树 + 副职业支援技（武器池 + 被动/光环），'
          '`damage_weights` 不写（默认权重）。**未人工校准** ⇒ 排名反映的是'
          '「这套自动流程下的相对强弱」，不排除某些组合靠手工校准能再上一个台阶。',
          '- 每形态 1 进程（为了 27 个形态能并发）⇒ 单形态数字**低于**'
          '「每形态 5~8 进程」的正式跑。**组合之间的相对排名**仍然可用，'
          '但别把这里的绝对值当成上限。',
          '- 只测了 3 个形态（人/狼人/鸦人）；`_fast`/`_pierce` 这类调参变体未参与。']
    out = FINAL / ('ALLWITH_%d_lv%d.md' % (a.all_with, level))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(L) + '\n', encoding='utf-8')
    return str(out.relative_to(SKILL))


# ------------------------------------------------------------------ 总报告
def write_report(cfg, char, st: State, log):
    key = st.key
    P = ['# BD 配方 —— %s' % key, '']
    P += ['> 生成 %s ｜ 形态 %s ｜ 等级 **%d** ｜ 目标 **%s** ｜ 底子 %s'
          % (time.strftime('%Y-%m-%d %H:%M'),
             ('形态 `%s`' % cfg['forms'][0]) if len(cfg['forms']) == 1
             else '**%d 个候选形态**（自动展开）' % len(cfg['forms']),
             cfg['level'], cfg['goal'],
             '从 0 造角色（不参考存档）' if cfg['from_zero'] else '现有角色 `%s`' % char),
          '']
    b = cfg['budget_info']
    P += ['## ① 点数预算（按等级推导，不读存档）', '',
          '| 项 | 值 | 依据 |', '|---|---|---|',
          '| 等级 | %d | — |' % b['level'],
          '| 技能点 | **%d** | `data/level_table.json` |' % b['skill'],
          '| 属性点 | **%d** | 同上（1 点 = +%s 面板） |' % (b['attr'], b['attr_per_point']),
          '| 虔诚上限 | **%d** | 同上 |' % b['devotion'], '']

    s3 = st.d.get('stages', {}).get('S3') or {}
    if s3.get('rows'):
        P += ['## ② 形态普查（模型自己选，不问人）', '',
              '| 形态 | 真实 DPS | 单形态耗时 |', '|---|---|---|']
        for r in s3['rows']:
            mark = ' ← **最优**' if r.get('arch') == s3.get('winner') else ''
            P += ['| `%s`%s | %s | %s |'
                  % (r.get('arch'), mark,
                     '{:,.0f}'.format(r['dps']) if r.get('dps') else '—',
                     ('%.1f s' % r['elapsed']) if r.get('elapsed') else '—')]
        P += ['', '并发墙钟 **%.1f s**（串行和 %.1f s ⇒ 重叠收益 **%.2f×**）'
              % (s3.get('wall') or 0, s3.get('sum_elapsed') or 0,
                 ((s3.get('sum_elapsed') or 1) / max(s3.get('wall') or 1, 1e-9))), '']

    s4 = st.d.get('stages', {}).get('S4') or {}
    P += ['## ③ 四轴', '']
    if s4.get('skipped'):
        P += ['（已跳过）', '']
    else:
        P += ['轴 = `%s` ｜ 用时 %.0f s' % (s4.get('axes'), s4.get('seconds') or 0), '',
              '```', (s4.get('summary') or '').strip(), '```', '',
              '⚠ 四轴 harness 不同（星座走显式星座通道），**只报逐轴增益、不可相加**。', '']

    s5 = st.d.get('stages', {}).get('S5') or {}
    P += ['## ④ 交付物', '',
          '| 项 | 路径 |', '|---|---|',
          '| BD 报告 | `%s` |' % (s5.get('build_report') or '—'),
          '| **伤害循环文档** | `%s` |' % (s5.get('cycle_doc') or '—'),
          '| 可落档方案 | `%s` |' % str((st.dir / 'plans' / ('arch_%s.json'
                                        % (s3.get('winner') or ''))).relative_to(SKILL)),
          '| 合法自检 | %s |' % ('✓ 通过' if s5.get('legal_ok') else '✗ 见日志'), '']

    P += ['## ⑤ 各阶段耗时', '', '| 阶段 | 秒 |', '|---|---|']
    tot = 0.0
    for k in ('S1', 'S2', 'S3', 'S4', 'S5'):
        sec = (st.d['stages'].get(k) or {}).get('seconds')
        if sec:
            tot += float(sec)
        P += ['| %s | %s |' % (k, ('%.1f' % sec) if sec else '—')]
    P += ['| **S3 墙钟（真实）** | **%s** |' % ('%.1f' % s3['wall'] if s3.get('wall') else '—'),
          '', '> S3 是并发跑的，**不能用各形态耗时相加**；合计请以 S3 墙钟为准。', '']

    out = FINAL / ('RECIPE_%s.md' % key)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(P) + '\n', encoding='utf-8')
    return str(out)


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(
        prog='python -m gd recipe',
        description='BD 配方 v2：一条命令从「职业 + 等级 + 目标」跑到完整 BD')
    ap.add_argument('spec', help='形态键（wolf_necromancer）或职业组合（8,10）')
    ap.add_argument('level', nargs='?', type=int, default=0,
                    help='等级（缺省 = 取 --char 的存档等级）')
    ap.add_argument('--char', default='', help='现有角色名（不给且 --from-zero 时自动造）')
    ap.add_argument('--from-zero', action='store_true',
                    help='从 0 造角色（临时存档目录，不碰真存档）')
    ap.add_argument('--forms', default='', help='只跑指定形态（逗号分隔），覆盖自动展开')
    ap.add_argument('--goal', default='balanced', choices=sorted(GOALS))
    ap.add_argument('--budget', type=int, default=0, help='并发进程上限（默认物理核）')
    ap.add_argument('--iters', type=int, default=400, help='LNS 轮数（默认 400）')
    ap.add_argument('--axes', default='attr,skill,dev',
                    help='S4 跑哪些轴（attr,skill,dev,gear ｜ none=跳过）')
    ap.add_argument('--stages', default='1,2,3,4,5', help='只跑这些阶段')
    ap.add_argument('--force', action='store_true', help='无视状态缓存，全部重跑')
    ap.add_argument('--dry-run', action='store_true', help='只打印计划')
    ap.add_argument('--list-forms', action='store_true', help='只列形态展开结果')
    ap.add_argument('--low-priority', action='store_true',
                    help='★ 子进程设为「低于正常」优先级（Windows `BELOW_NORMAL_PRIORITY_CLASS`）'
                         '—— 普查不再抢前台，机器仍能正常用')
    ap.add_argument('--batch', type=int, default=0,
                    help='★ 每批最多跑几个组合（0=全跑）。配合「逐组合续跑」，'
                         '可以分批累积 —— 长任务被打断也不会丢进度')
    ap.add_argument('--all-with', type=int, default=0, metavar='CLASS',
                    help='★ 固定这个职业（如 10=狂战士），与**其余全部职业**逐一组合并发普查，'
                         '输出「哪个组合伤害最高」排名（spec 位置可随便填占位）')
    a = ap.parse_args()

    archs = load_archs()
    if a.low_priority:
        _set_low_priority(True)
    if a.all_with:
        lvl = int(a.level or 0)
        if not lvl:
            print('✗ --all-with 需要等级（第二个位置参数）')
            return 1
        return all_with(a, archs, lvl, print)
    forms = forms_for(a.spec, archs, a.forms)
    if a.list_forms:
        print('spec=%s ⇒ %d 个形态：%s' % (a.spec, len(forms), ', '.join(forms) or '（无）'))
        if is_classes(a.spec) and not forms:
            print('  ⚠ 该职业组合在 archetypes.json 里没有形态 —— 先补进 '
                  'data/archetypes_gdskill.json（**不是** archetypes.json，会被迁移覆盖）')
        return 0 if forms else 1
    if not forms:
        print('✗ 没有可用形态（spec=%s）。用 --list-forms 看展开结果。' % a.spec)
        return 1

    if a.level:
        level = int(a.level)
    elif a.char:
        import autobuild as AB
        level = int(AB.read_meta(a.char)[0])
    else:
        print('✗ 需要等级（第二个位置参数），或给 --char 让它按存档等级推。')
        return 1

    cls = ([int(x) for x in a.spec.split(',')] if is_classes(a.spec)
           else sorted({m[0] for m in (archs[forms[0]].get('masteries') or [])}))
    char = a.char or ('_R' + slug(a.spec) + '_L%d' % level)
    key = '%s_lv%d%s' % (slug(a.spec), level, '_zero' if a.from_zero else '')
    goal, genv = GOALS[a.goal]
    budget = a.budget or _phys()

    cfg = {'spec': a.spec, 'level': level, 'classes': cls, 'forms': forms,
           'char': char, 'from_zero': a.from_zero, 'goal': a.goal, 'goal_opt': goal,
           'budget': budget, 'iters': a.iters, 'axes': a.axes, 'archs': archs,
           'budget_info': budget_of(level)}

    print('=' * 86)
    print('BD 配方 ｜ %s ｜ lv%d ｜ 目标 %s ｜ 形态 %d 个 ｜ 底子 %s'
          % (a.spec, level, a.goal, len(forms), '从 0 造' if a.from_zero else '`%s`' % char))
    print('=' * 86)
    b = cfg['budget_info']
    print('  S0 预算    技能 %d ｜ 属性 %d ｜ 虔诚 %d ｜ 搜索口味 goal=%s %s'
          % (b['skill'], b['attr'], b['devotion'], goal, genv or '{}'))
    if a.from_zero:
        print('  S1 底子    临时存档目录 %s（真存档不受影响）' % ZERO_SAVE)
    print('  S2 加点    %s' % ', '.join('alloc%d_%s.json' % (level, f) for f in forms))
    print('  S3 形态普查 %s ｜ 并发 %d 进程 ｜ LNS %d 轮 ｜ --extreme --with-weapon'
          % (','.join(forms), budget, a.iters))
    print('  S4 四轴    %s ｜  S5 交付 planreport + plan_cycle + plan_legal' % a.axes)
    if a.dry_run:
        print('\n（--dry-run：只打印计划）')
        return 0

    st = State(key, force=a.force)
    env = dict(genv)
    if a.from_zero:
        env['GD_SAVE'] = str(ZERO_SAVE)
    # ★ 赢家形态与它的加点：**从状态里读**，不能只在循环里设 ——
    #   否则 `--stages 4` 续跑时（S2/S3 被跳过）会拿不到加点 ⇒ S4 在「无技能」状态下评。
    win0 = st.get('S3', 'winner') or forms[0]
    env['GD_ARCHETYPE'] = win0
    _al0 = (st.get('S2', 'plans') or {}).get(win0) or (
        'data/scratch/alloc%d_%s.json' % (level, win0))
    if pathlib.Path(SKILL / _al0).exists():
        env['GD_SKILL_JSON'] = _al0

    want = {x.strip() for x in a.stages.split(',') if x.strip()}
    fails = []
    t_all = time.time()
    for n in ('1', '2', '3', '4', '5'):
        if n not in want:
            continue
        label = {'1': 'S1 底子    ', '2': 'S2 加点    ', '3': 'S3 形态普查',
                 '4': 'S4 四轴    ', '5': 'S5 交付    '}[n]
        if st.done('S' + n):
            print('%s 跳过（状态里已完成；`--force` 可重跑）' % label)
            continue
        t0 = time.time()
        if n == '1':
            ok, res = s1_base(char, cfg, st, print)
        elif n == '2':
            ok, res = s2_alloc(cfg, st, print)
        elif n == '3':
            ok, res = s3_sweep(char, cfg, st, print, env)
            if res.get('winner'):
                env['GD_ARCHETYPE'] = res['winner']
                al = (st.get('S2', 'plans') or {}).get(res['winner'])
                if al:
                    env['GD_SKILL_JSON'] = al
        elif n == '4':
            ok, res = s4_axes(char, cfg, st, print, env)
        else:
            ok, res = s5_deliver(char, cfg, st, print, env)
        res['seconds'] = res.get('seconds', round(time.time() - t0, 1))
        res['ok'] = bool(ok)
        st.put('S' + n, **res)
        if not ok:
            fails.append('S' + n)
            print('  ✗ %s 失败：%s' % (label.strip(), res.get('error') or res))
            break
    st.d['char'] = char
    st.d['config'] = {k: cfg[k] for k in ('spec', 'level', 'classes', 'forms', 'goal',
                                          'from_zero', 'budget', 'iters', 'axes')}
    st.save()
    rep = write_report(cfg, char, st, print)
    print('-' * 86)
    print('总用时 %.1f s ｜ 报告 %s' % (time.time() - t_all,
                                       pathlib.Path(rep).relative_to(SKILL)))
    print('中间产物与日志：%s' % (st.dir.relative_to(SKILL)))
    if fails:
        print('✗ 失败阶段：%s' % ', '.join(fails))
        return 1
    print('✓ 全阶段完成')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
