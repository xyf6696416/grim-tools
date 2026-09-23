"""性能基线：跑一遍主要命令，记录耗时。

    python tools/bench.py            # 全跑
    python tools/bench.py save dps   # 只跑指定项
"""
import os
import subprocess
import sys
import time
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
PY = sys.executable

CASES = [
    ("env",       ["-m", "gd", "env"]),
    ("db(冷启动)", ["-c", "import sys;sys.path.insert(0,'.');import gd;"
                    "print(gd.DB.load(verbose=False).stats()['items'])"], True),
    ("item",      ["-m", "gd", "item", "天之裂片咒刃", "--range"]),
    ("find",      ["-m", "gd", "find", "世界守护者", "--limit", "8"]),
    ("chars",     ["-m", "gd", "chars"]),
    ("save Sam",  ["-m", "gd", "save", "Sam"]),
    ("verify Sam", ["-m", "gd", "verify", "Sam"]),
    ("dps Sam",   ["-m", "gd", "dps", "Sam"]),
]


def run(args, cold=False):
    env = dict(os.environ)
    if cold:
        # 清掉 pickle 缓存，测真实冷启动成本
        for p in (SKILL / "data" / "cache").glob("db_*.pkl"):
            p.unlink()
    t0 = time.time()
    r = subprocess.run([PY] + args, cwd=str(SKILL), capture_output=True)
    return time.time() - t0, r.returncode, (r.stderr or b"")[:120].decode("utf-8", "replace")


def main():
    want = set(sys.argv[1:])
    print(f"{'用例':<16}{'耗时':>9}   结果")
    print("-" * 46)
    tot = 0.0
    for name, args, *rest in [(c[0], c[1], *(c[2:])) for c in CASES]:
        if want and name not in want:
            continue
        cold = bool(rest and rest[0])
        dt, rc, err = run(args, cold)
        tot += dt
        mark = "OK" if rc == 0 else f"rc={rc}"
        print(f"{name:<16}{dt:>8.2f}s   {mark} {err if rc else ''}")
    print("-" * 46)
    print(f"{'合计':<16}{tot:>8.2f}s")


if __name__ == "__main__":
    main()
