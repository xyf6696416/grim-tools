"""满抗 ILP 的**同进程参数矩阵**诊断台架（归因工具，不参与生产）。

为什么要它
----------
满抗 MIP 的耗时来自 HiGHS 的 B&B，**不能靠猜**（`gap` / 启发式 / 对称性这些旋钮
在别的模型上有效、在这个模型上完全无效）。本工具在**真实 `gd auto` 环境**里
拿到 candmap 后，**建一次池、串行跑一组配置**，因此差异可归因。

⚠ 别用 `python tools/ilp_res.py ... ` 手工复现这套对比 —— CLI 的 env 与 `gd auto`
  不同（少了 `GD_ENEMY_RES_PROFILE` / `GD_REAL_GOAL` / `GD_ATTR_BUDGET` 等），
  候选池会小一圈（35,664 vs 38,267 变量），测出来的数不可比。

用法
----
    python tools/bench_ilp_opts.py Sam --extreme          # 完整矩阵
    python tools/bench_ilp_opts.py Sam --extreme --quick  # 只跑基准 + 支配剪枝

结论（2026-09-22，Sam lv73 / 38,267 变量）见 `docs/pitfalls.md` #93：
  * `gap` 1e-3 ~ 1e-1 **完全无差**（HiGHS 是证明完最优性才停，gap 归零）；
  * `mip_start` / `mip_heuristic_effort` / `mip_detect_symmetry` 基本无功；
  * `presolve=off` 变快但**给出另一个最优解** ⇒ 有下游风险，不用；
  * ★ **(抗性, 目标) 支配剪枝**：变量 −66%、求解 −62%、**解与目标逐位不变**。
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(HERE))

import ilp_res as IR            # noqa: E402
import numpy as np              # noqa: E402

_solve_orig = IR.Model.solve
_RAN = []


def vec_of(M, sol):
    """{slot: 5元组} -> 0/1 向量（供 `mip_start` 用）"""
    x = np.zeros(M.n)
    for s in M.slots:
        for i, c in enumerate(M.candmap[s]):
            if c[0] == sol.get(s):
                x[M.off[s] + i] = 1.0
                break
    return x


def run_matrix(self, quick=False):
    print("\n============ 满抗 ILP 参数矩阵（同进程 / 同 candmap）============", flush=True)
    base = {}

    def go(M, label, key="full", gap=1e-3, mip_start=None, extra=None):
        t = time.time()
        sol, info = _solve_orig(M, time_limit=60.0, gap=gap, log=lambda *a: None,
                                mip_start=mip_start, extra_opts=extra)
        dt = time.time() - t
        if sol is not None and key not in base:
            base[key] = sol
        same = "-" if (sol is None or key not in base) else (
            "是" if sol == base[key] else "**否**")
        print("  %-38s %6.2f s ｜ %-9s ｜ 目标 %-10.4f ｜ n=%-6d ｜ 同基准 %s"
              % (label, dt, info.get("status"), info.get("objective") or -1,
                 M.n, same), flush=True)
        return sol

    go(self, "① 基准（gap 1e-3）")
    go(self, "①' 基准复跑（可复现性）")
    if not quick:
        for g in (1e-2, 1e-1):
            go(self, "② gap=%g（验证 gap 是否为瓶颈）" % g, gap=g)
        go(self, "③ gap=1e-3 + mip_heuristic_effort=0.2",
           extra={"mip_heuristic_effort": 0.2})
        go(self, "④ gap=1e-3 + presolve=off", extra={"presolve": "off"})
        go(self, "⑤ gap=1e-3 + mip_detect_symmetry=True",
           extra={"mip_detect_symmetry": True})

    O = self.O
    t = time.time()
    cm2 = IR.dom_reduce(O, self.candmap, log=lambda *a: None, use_cache=False)
    print("  [支配剪枝] 用时 %.2f s ｜ 变量 %d → %d（%.1f%%）"
          % (time.time() - t, self.n, sum(len(v) for v in cm2.values()),
             100.0 * sum(len(v) for v in cm2.values()) / self.n), flush=True)
    print("    逐槽 " + " ｜ ".join("%s %d→%d" % (s, len(self.candmap[s]), len(cm2[s]))
                                   for s in self.slots), flush=True)

    t = time.time()
    M2 = IR.Model(O, cm2)
    s2 = go(M2, "★ ⑥ 支配剪枝（gap 1e-3）")
    print("    （建模 %.2f s）" % (time.time() - t), flush=True)
    if s2 is not None:
        go(M2, "⑥' 剪枝 + mip_start(⑥ 的解)", mip_start=vec_of(M2, s2))
    print("=============================================================\n", flush=True)


def _hook(self, *a, **k):
    if not _RAN:
        _RAN.append(1)
        try:
            run_matrix(self, quick=bool(getattr(_ARGS, "quick", False)))
        except Exception:                                       # noqa: BLE001
            import traceback
            traceback.print_exc()
    return _solve_orig(self, *a, **k)


IR.Model.solve = _hook

_ARGS = None
if __name__ == "__main__":          # ★ 必须守卫：spawn 会把本文件当 __mp_main__ 再导入
    ap = argparse.ArgumentParser(description="满抗 ILP 参数矩阵（同进程归因）")
    ap.add_argument("char", nargs="?", default="Sam")
    ap.add_argument("--with-weapon", action="store_true")
    ap.add_argument("--extreme", action="store_true")
    ap.add_argument("--quick", action="store_true", help="只跑基准 + 支配剪枝")
    _ARGS, _rest = ap.parse_known_args()

    import autobuild as AB
    sys.argv = ["autobuild", _ARGS.char] + (
        ["--with-weapon"] if _ARGS.with_weapon else []) + (
        ["--extreme"] if _ARGS.extreme else []) + _rest
    AB.main()
