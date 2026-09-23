# -*- coding: utf-8 -*-
"""多形态**并发**普查驱动 —— 把 N 个形态的完整配装链同时跑起来。

为什么值得做
------------
单个形态的 LNS 里有**不可并行的串行段**（邻域构造、LAHC 记账、numpy 修复、
eval_cap 截断）。实测单形态 16 进程端到端只有 **7.7×**（并行效率 48%）。

串行跑 6 个形态时，这 6 份串行段是**首尾相接**的；并发跑时它们**互相重叠**。
实测（6 形态 × 400 轮 LNS，同口径）：

| 布局 | 每实例 procs | 总进程 | 总墙钟 |
|---|---|---|---|
| 串行 | 16 | 16 | 244.6 s |
| 并发 | 2 | 16 | 154.1 s |
| 并发 | 3 | 18 | 108.1 s |
| **并发** | **4** | **24** | **90.0 s ← 拐点** |
| 并发 | 5 | 30 | 98.1 s（已过拐点） |

★ 结果**逐位一致**：并发下 6 个形态的 DPS 与串行完全相同 ⇒ 并发只改墙钟。
★ 内存不是约束：ParEval 单 worker 仅 **143 MB**（线性）⇒ 30 个才 ~3.6 GB。
  真正的约束是**核**：16 物理 / 32 逻辑，所以拐点在 24 而不是 32。

用法
----
  # 默认 budget=24（拐点）
  python tools/sweep_arch.py Sam --archs fangs,raven_nightblade

  # 全形态（读 data/archetypes.json）
  python tools/sweep_arch.py Sam --all

  # 自定义 LNS 规模 / 额外透传参数
  python tools/sweep_arch.py Sam --all --budget 30 --iters 1200 --extra "--pool-topn 200"

⚠ 它绕开 `gd auto` 的两条内层并行（`--jobs/--chains` 与 `--procs`）——
  那是**不同层次**的并行，别叠加（§3.19）。

产出
----
  --out-dir/arch.json        每个形态一份方案
  --log-dir/sweep_<arch>.log 每个形态的完整日志
  --out-dir/_sweep.json      汇总（含墙钟与 DPS）
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def _env_for(arch: str, char: str, alloc_tpl: str, proj: str) -> dict:
    e = dict(os.environ)
    e['GD_ARCHETYPE'] = arch
    e['GD_PROJ_HITS'] = str(proj)
    e['PYTHONHASHSEED'] = '0'
    e.setdefault('GD_QUIET', '1')
    ap = alloc_tpl.format(arch=arch, char=char)
    if os.path.isfile(os.path.join(ROOT, ap)):
        e['GD_SKILL_JSON'] = ap
    return e


def _read_last_res(log_path: str):
    """从日志里抽 (总耗时秒, DPS, 现状DPS, 原文)

    ⚠ 别用「取行内最后一个数字」的写法 —— 结果行末尾是 `+100.2%）`，
      那样会解析出 100.2 这个**百分比**当成 DPS。
    """
    import re
    try:
        txt = open(log_path, encoding='utf-8', errors='replace').read()
    except OSError:
        return None
    el, dps, cur, line = None, None, None, ''
    for ln in txt.splitlines():
        if ln.startswith('总耗时'):
            try:
                el = float(ln.split()[-2])
            except (ValueError, IndexError):
                pass
        if ln.startswith('结果'):
            line = ln
            m = re.search(r'真实 DPS\s*([\d,]+)', ln)
            if m:
                dps = float(m.group(1).replace(',', ''))
            m2 = re.search(r'现状\s*([\d,]+)', ln)
            if m2:
                cur = float(m2.group(1).replace(',', ''))
    return {'elapsed': el, 'dps': dps, 'cur': cur, 'line': line}


def main():
    ap = argparse.ArgumentParser(description='多形态并发普查')
    ap.add_argument('char', nargs='?', default='Sam')
    ap.add_argument('--archs', default='', help='逗号分隔的形态键')
    ap.add_argument('--all', action='store_true', help='data/archetypes.json 里的全部形态')
    ap.add_argument('--budget', type=int, default=24,
                    help='总评估进程预算（默认 24 = 本机实测拐点）。'
                         '实测 6 形态 × 400 轮：串行 244.6 s ｜ 16 进程 154.1 s ｜ '
                         '18 进程 108.1 s ｜ **24 进程 90.0 s（最优）** ｜ 30 进程 98.1 s（过拐点）')
    ap.add_argument('--iters', type=int, default=400, help='LNS 轮数（默认 400）')
    ap.add_argument('--lns-k', default='', help='邻域规模，如 2,3,4')
    ap.add_argument('--lns-topk', type=int, default=0)
    ap.add_argument('--lns-cap', type=int, default=0)
    ap.add_argument('--alloc-tpl', default='data/scratch/alloc71_{arch}.json',
                    help='加点文件模板（{arch} 占位）')
    ap.add_argument('--proj', default='1', help='projectile_hits（默认 1，保守口径）')
    ap.add_argument('--out-dir', default='data/plans')
    ap.add_argument('--log-dir', default='data/scratch')
    ap.add_argument('--extra', default='', help='透传给 gd auto 的额外参数串')
    a = ap.parse_args()

    if a.all:
        archs = list(json.load(open(os.path.join(ROOT, 'data/archetypes.json'),
                                    encoding='utf-8')).keys())
    else:
        archs = [x.strip() for x in a.archs.split(',') if x.strip()]
    if not archs:
        ap.error('需要 --archs 或 --all')

    n = len(archs)
    per = max(1, a.budget // n)
    total = per * n

    print('形态 %d 个 ｜ 总预算 %d 进程 ⇒ 每形态 %d 进程 ｜ LNS %d 轮 ｜ proj=%s'
          % (n, a.budget, per, a.iters, a.proj))
    print('=' * 78)

    os.makedirs(os.path.join(ROOT, a.out_dir), exist_ok=True)
    os.makedirs(os.path.join(ROOT, a.log_dir), exist_ok=True)

    procs = []
    t0 = time.perf_counter()
    for arch in archs:
        out = os.path.join(a.out_dir, 'arch_%s.json' % arch)
        log = os.path.join(a.log_dir, 'sweep_%s.log' % arch)
        cmd = [sys.executable, '-u', '-m', 'gd', 'auto', a.char, '--extreme',
               '--procs', str(per), '--archetype', arch, '--out', out]
        if a.iters:
            cmd += ['--anneal-iters', str(a.iters)]   # 该参数现在也驱动 LNS 轮数
        if a.lns_k:
            cmd += ['--lns-k', a.lns_k]
        if a.lns_topk:
            cmd += ['--lns-topk', str(a.lns_topk)]
        if a.lns_cap:
            cmd += ['--lns-cap', str(a.lns_cap)]
        if a.extra:
            cmd += a.extra.split()
        f = open(os.path.join(ROOT, log), 'w', encoding='utf-8')
        print('  ▶ %-24s %s' % (arch, log))
        procs.append((arch, subprocess.Popen(cmd, cwd=ROOT, stdout=f,
                                             stderr=subprocess.STDOUT,
                                             env=_env_for(arch, a.char, a.alloc_tpl, a.proj))))

    while any(p.poll() is None for _, p in procs):
        time.sleep(1.0)
    el = time.perf_counter() - t0

    # ---- 汇总
    print('=' * 78)
    rows = []
    for arch, p in procs:
        r = _read_last_res(os.path.join(ROOT, a.log_dir, 'sweep_%s.log' % arch)) or {}
        r['arch'] = arch
        r['rc'] = p.returncode
        rows.append(r)
    rows.sort(key=lambda r: -(r.get('dps') or 0))
    print('%-24s %10s %8s %10s  %s' % ('形态', '真实DPS', '提升', '单形态耗时', '状态'))
    for r in rows:
        gain = ('+%.1f%%' % ((r['dps'] / r['cur'] - 1) * 100)
                if r.get('dps') and r.get('cur') else '—')
        print('%-24s %10s %8s %10s  %s'
              % (r['arch'],
                 format(int(r['dps']), ',') if r.get('dps') else '—',
                 gain,
                 ('%.1f s' % r['elapsed']) if r.get('elapsed') else '—',
                 '✓' if r['rc'] == 0 else '✗ rc=%d' % r['rc']))
    print('-' * 78)
    wall = sum(r.get('elapsed') or 0 for r in rows)
    print('并发总墙钟 %.1f s ｜ 各形态耗时之和 %.1f s ｜ 重叠收益 %.2f×'
          % (el, wall, wall / el if el else 1.0))

    summary = {'char': a.char, 'n': n, 'per_proc': per, 'iters': a.iters,
               'proj': a.proj, 'wall': el, 'sum_elapsed': wall, 'rows': rows}
    sp = os.path.join(ROOT, a.out_dir, '_sweep.json')
    json.dump(summary, open(sp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('→', sp)
    return 0


if __name__ == '__main__':
    sys.exit(main())
