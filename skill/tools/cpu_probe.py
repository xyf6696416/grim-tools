"""实测 `gd auto` 各阶段的整机 CPU 利用率 —— 回答「到底有没有跑满机器」。

对比 16 链（=物理核数）与 32 链（=逻辑核数，走 SMT）：
  · 若 16 链时 CPU 只有 ~50%，说明 SMT 那 16 个逻辑核白放着
  · 32 链若 CPU 上到 90%+ 且 DPS 不降，就该把默认并行度提上去
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import time

SKILL = pathlib.Path(__file__).resolve().parent.parent
PY = r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"


def one(chains, anneal):
    import psutil
    import tempfile
    log = pathlib.Path(tempfile.gettempdir()) / ("gd_cpu_%d.log" % chains)
    env = dict(os.environ, PYTHONHASHSEED="0")
    env.pop("GD_AUTO_REEXEC", None)
    t0 = time.time()
    with open(log, "w", encoding="utf-8") as fh:
        p = subprocess.Popen([PY, "-m", "gd", "auto", "Sam", "--extreme",
                              "--chains", str(chains),
                              "--anneal-seconds", str(anneal)],
                             stdout=fh, stderr=subprocess.STDOUT, env=env,
                             cwd=str(SKILL))
    psutil.cpu_percent(interval=None)
    peak = tot = 0.0
    n = 0
    ncpu = psutil.cpu_count()
    per_peak = [0.0] * ncpu
    while p.poll() is None:
        v = psutil.cpu_percent(interval=1.0, percpu=False)
        peak = max(peak, v)
        tot += v
        n += 1
    wall = time.time() - t0
    txt = log.read_text(encoding="utf-8", errors="replace")
    dps = re.findall(r"真实 DPS ([\d,]+)（现状", txt)
    cap = re.findall(r"满抗搜索\] (ILP 全局最优|束搜索)", txt)
    cl = re.findall(r"并行链 (\d+)", txt)
    warn = re.findall(r"⚠ 可用内存.*", txt)
    print("  %2d 链 ｜ 墙钟 %5.0f s ｜ CPU 峰值 %5.1f%% ｜ 均值 %5.1f%% ｜ "
          "结果 DPS %s ｜ 满抗 %s ｜ 实际链 %s"
          % (chains, wall, peak, tot / max(1, n),
             dps[0] if dps else "?", cap[0] if cap else "?",
             cl[0] if cl else "?"))
    for w in warn:
        print("      %s" % w)
    return {"chains": chains, "wall": wall, "peak": peak, "avg": tot / max(1, n),
            "dps": dps[0] if dps else None}


def main():
    anneal = float(sys.argv[1]) if len(sys.argv) > 1 else 45.0
    print("退火预算各 %.0f s ｜ 逻辑核 %d" % (anneal, os.cpu_count()))
    rows = []
    for c in (16, 32):
        rows.append(one(c, anneal))
    print()
    print("=== 汇总 ===")
    base = rows[0]
    for r in rows:
        gain = ""
        if r["dps"] and base["dps"]:
            a = float(base["dps"].replace(",", ""))
            b = float(r["dps"].replace(",", ""))
            gain = " ｜ DPS %+.1f%%" % (100 * (b - a) / a)
        print("  %2d 链 ｜ %5.0f s ｜ CPU 均值 %5.1f%%%s"
              % (r["chains"], r["wall"], r["avg"], gain))


if __name__ == "__main__":
    main()
