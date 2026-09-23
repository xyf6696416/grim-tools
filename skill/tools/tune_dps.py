#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tune_dps.py —— 在「满抗性」硬约束下，用**真实 DPS 模型**做局部搜索，最大化实际每秒伤害。

为什么要它
----------
`gd.opt` 的「伤害代理」是**线性加权和**，只用于排序；它对本 build 的权重
（穿刺 / 流血 / 攻速）与**真实模型**有明显偏差 —— 实测「代理更高」的方案真实 DPS 反而更低。

所以本工具：
  · 用 `gd.opt` 的候选池（`slot_cands`/`prune_cands`，含词缀折叠）当搜索空间
  · 用 `gd.opt.ev` 的向量（快）**硬筛**「9 项抗性全部封顶」的候选
  · 用 `gd.dps` 的 `final_report`（真）**实算**每秒伤害，取真正更高的解
  · 从给定起点做「单槽 + 双槽」贪心局部搜索（从现状出发 ⇒ 结果不劣于现状）

用法：
    python tools/tune_dps.py Sam [--arch wolf_nightblade] [--passes 2] [--topk 0]
                                 [--pair-k 3] [--start data/plans/Sam_current.json]
                                 [--out data/plans/Sam_tuned.json]

也可当库用：`from tune_dps import load_opt, vecs_of, local_search`（autobuild 就是这么做的）。
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from gd import paths                                   # noqa: E402
from gd import DB                                      # noqa: E402


# ==================================================================== 基础件
def load_opt(filter_archetype="wolf_nightblade_fast"):
    """以「最小化启动」导入 gd.opt（它模块级会跑一轮小搜索）。

    ★ 启动搜索必须能出解，否则 `run_search` 会 `SystemExit(1)` ——
      所以临时关掉 GD_FULL 硬剪枝（小束宽 + 硬剪枝容易无解）。
      真正的搜索由 `local_search` / `autobuild` 自己跑。
    """
    os.environ.setdefault("GD_DMG_TIE", "1.2")
    os.environ["GD_FULL"] = "0"
    os.environ["GD_COVER_MIN"] = "0"
    os.environ.setdefault("GD_OVER_PEN", "0")
    os.environ.setdefault("GD_AUTO_TOPN", "70")
    os.environ.setdefault("GD_COMP_TOPN", "20")
    os.environ.setdefault("GD_AUG_TOPN", "12")
    os.environ.setdefault("GD_ARCHETYPE", filter_archetype)
    saved = sys.argv
    sys.argv = ["gd_opt.py", "--goal", "worst", "--beam", "200", "--restart", "1",
                "--min-leg", "0", "--max-leg", "14", "--min-epic", "0", "--max-epic", "14",
                "--out", "_tmp_tune_boot.json", "--quiet"]
    import importlib
    O = importlib.import_module("gd.opt")
    sys.argv = saved
    return O


def vecs_of(O, tri):
    """一个槽位元组 (b,c,a,p,s) 的抗性/输出/技能向量。"""
    rt = [0.0] * len(O.TYPES)
    ot = [0.0] * len(O.FKEYS)
    sk = 0.0
    for gid in tri:
        if not gid:
            continue
        r1, o1, s1 = O.contrib(gid)
        for i in range(len(rt)):
            rt[i] += r1[i]
        for i in range(len(ot)):
            ot[i] += o1[i]
        sk += s1
    return tuple(rt), tuple(ot), sk


def coverage(O, sol):
    r, _ = O.ev(sol)
    return sum(min(r.get(t, 0.0), O.NEED[t]) for t in O.TYPES), r


def need_total(O):
    return sum(O.NEED[t] for t in O.TYPES)


def is_full(O, r):
    return all(r.get(t, 0.0) >= O.NEED[t] - 1e-9 for t in O.TYPES)


# ======================================================== 统一「批量评估」入口
def eval_trials(trials, real_dps, par=None, memo=None, slots=None, min_batch=None):
    """评估一批方案，返回 `[dps, …]`（**顺序与入参一致**）。

    ★★ 为什么要有这个函数（2026-09-22 性能重构）：贪心（`local_search`）与
      LNS（`lns_search`）本质上都是「**在一个邻域里对一批互相独立的方案打分**」。
      旧实现只有 LNS 走了进程池，贪心全程串行 —— 而贪心的评估量一点不小
      （单槽 14×200 + 双槽 91×16，每轮 ~17,000 次，占单链三分之二的时间）。
      抽成一个入口后，两条路都能吃满多核，且**口径不可能分叉**。

    路径选择：
      · `par` 存在 **且** 本批够大（≥ `par.min_batch`）⇒ 进程池整批并行（实测 8 进程 6.8×）
      · 否则 ⇒ 串行 `real_dps`（**带 memo**，小批量时反而更快）

    ⚠ 并行路径**先吃掉调用方的 memo** —— 进程池看不到 memo，不去重的话同一解
      会被反复送去重算（实测浪费 ~40% 真实评估）。
    """
    if not trials:
        return []
    if par is not None and len(trials) >= (min_batch or par.min_batch):
        if memo is None or slots is None:
            return list(par.batch(trials))
        keys = [tuple(t[s] for s in slots) for t in trials]
        out = [memo.get(k) for k in keys]
        todo = [i for i, v in enumerate(out) if v is None]
        if todo:
            for _i, _v in zip(todo, par.batch([trials[i] for i in todo])):
                memo[keys[_i]] = _v
                out[_i] = _v
        return out
    return [real_dps(t) for t in trials]


# ==================================================================== 局部搜索
def local_search(O, sol, real_dps, passes=2, topk=0, pair_k=3, log=print, tag="",
                 require_full=True, max_per_slot=120, par=None):
    """用 real_dps 做单槽 + 双槽贪心局部搜索。

    `require_full=True`（默认）: 硬约束「9 项抗性全部封顶」——任一槽换完必须仍满抗。
    `require_full=False`        : 不保抗性，只追伤害（`--goal dmg` 用）。
    `topk<=0`                   : 该槽**全部**可行候选（但仍受 `max_per_slot` 硬顶）。

    返回 (sol, dps)。real_dps(sol) 应自带缓存（autobuild 会包一层 memo）。

    ★★ 2026-09-22：新增 `par`（`tune_dps.ParEval` 进程池）—— 单槽与双槽两个阶段的
      候选评估全部改走 `eval_trials()` **整批并行**。口径与旧实现一致（同样的候选集、
      同样的满抗约束、同样的 `best + 0.5` 门槛），差别只有两点：
        · 单槽内改成「取本槽**最优**的可行候选」而不是「最后一个变好的」
          （旧写法会被候选顺序左右，等价于随机取一个提升，现在严格取最优）；
        · 双槽内**每个槽对**的候选整批判完再落一步，不再在双重循环里边走边改 `sol`
          （旧写法 `c1s`/`c2s` 是按旧 `sol` 算的，中途换 `sol` 会让后续候选失配）。
      两者都只会**加强**搜索，不会削弱。
    """
    import time as _t
    t0 = _t.time()
    _slots_l = list(O.SLOTS)
    _memo = getattr(real_dps, 'memo', None)
    SLOTS = O.SLOTS
    NEED, TYPES, W = O.NEED, O.TYPES, O.W_DMG
    NT = len(TYPES)
    csmap = {s: O.prune_cands(O.slot_cands(s)) for s in SLOTS}

    cv_cache = {}

    def cv(tri):
        v = cv_cache.get(tri)
        if v is None:
            v = cv_cache[tri] = vecs_of(O, tri)
        return v

    # 把起点元组也塞进候选集（否则无法与它比较 / 无法保留它）
    for s in SLOTS:
        if all(c[0] != sol[s] for c in csmap[s]):
            rt, ot, sk = cv(sol[s])
            f = O._rar(sol[s][0])
            csmap[s].append((sol[s], rt, ot, sk, int(f == "Rare"),
                             int(f == "Epic"), int(f == "Legendary")))
        csmap[s].sort(key=lambda c: -sum(c[2][i] * W.get(k, 0.0)
                                         for i, k in enumerate(O.FKEYS)))
        # ★ 按（抗性, 输出, 技能）向量去重：换名不换数值的候选完全等价，
        #   全库里有大量这种（不同记录、同数值），不去重会白算几十次 DPS。
        seen, uniq = set(), []
        for c in csmap[s]:
            sig = (c[1], c[2], c[3])
            if sig in seen:
                continue
            seen.add(sig)
            uniq.append(c)
        csmap[s] = uniq
    log("  %s候选池（去重后）：%s ｜ %.1f s"
        % (tag, " ".join("%s=%d" % (s, len(csmap[s])) for s in SLOTS), _t.time() - t0))

    tot = [0.0] * NT
    for s in SLOTS:
        r, _, _ = cv(sol[s])
        for i in range(NT):
            tot[i] += r[i]

    def ok(r):
        if not require_full:
            return True
        return all(r[i] >= NEED[TYPES[i]] - 1e-9 for i in range(NT))

    def feas(s, base_tot, k):
        """在 base_tot（不含 s 自身贡献）之上，返回 s 的可行候选（按代理降序）。"""
        out = []
        for c in csmap[s]:
            if c[0] == sol[s]:
                continue
            nr = [base_tot[i] + c[1][i] for i in range(NT)]
            if ok(nr):
                sc = sum(c[2][i] * W.get(kk, 0.0) for i, kk in enumerate(O.FKEYS))
                out.append((sc, c))
        out.sort(key=lambda x: -x[0])
        lim = k if (k and k > 0) else max_per_slot
        return out[:lim] if lim and lim > 0 else out

    best = real_dps(sol)
    full = all(tot[i] >= NEED[TYPES[i]] - 1e-9 for i in range(NT))
    log("  %s起点 DPS = %s（覆盖 %s）" % (tag, format(best, ","), "满" if full else "有缺口"))
    if require_full and not full:
        log("  %s起点未满抗 —— 需要先跑满抗搜索，局部搜索跳过" % tag)
        return sol, best

    def apply_swap(s, newtri):
        nonlocal best
        nr, _, _ = cv(newtri)
        cr, _, _ = cv(sol[s])
        for i in range(NT):
            tot[i] += nr[i] - cr[i]
        sol[s] = newtri

    for ps in range(passes):
        improved = False
        for s in SLOTS:
            base = [tot[i] - cv(sol[s])[0][i] for i in range(NT)]
            cands = feas(s, base, topk)
            if not cands:
                continue
            # ★ 整批并行评估（旧写法逐个 `real_dps`，单槽最多 200 次串行）
            trials = []
            for _sc, c in cands:
                trial = dict(sol)
                trial[s] = c[0]
                trials.append(trial)
            ds = eval_trials(trials, real_dps, par, _memo, _slots_l)
            bi = None
            for i, d in enumerate(ds):
                if d > best + 0.5 and (bi is None or d > ds[bi]):
                    bi = i
            if bi is not None:
                pick = cands[bi][1]
                best = ds[bi]
                apply_swap(s, pick[0])
                improved = True
                log("  %s[第%d轮] %-5s → %-16s DPS %s"
                    % (tag, ps + 1, s, str(pick[0][0])[:16], format(best, ",")))
        if pair_k and pair_k > 0:
            for i, s1 in enumerate(SLOTS):
                for s2 in SLOTS[i + 1:]:
                    base1 = [tot[j] - cv(sol[s1])[0][j] for j in range(NT)]
                    c1s = feas(s1, base1, pair_k)
                    if not c1s:
                        continue
                    base2 = [base1[j] - cv(sol[s2])[0][j] for j in range(NT)]
                    c2s = feas(s2, base2, pair_k)
                    if not c2s:
                        continue
                    # ★ 该槽对的 (c1 × c2) 全部候选**整批**评估，再落一步最优
                    trials, pairs = [], []
                    for _a, ca in c1s:
                        for _b, cb in c2s:
                            trial = dict(sol)
                            trial[s1] = ca[0]
                            trial[s2] = cb[0]
                            trials.append(trial)
                            pairs.append((ca, cb))
                    ds = eval_trials(trials, real_dps, par, _memo, _slots_l)
                    bi = None
                    for k2, d in enumerate(ds):
                        if d > best + 0.5 and (bi is None or d > ds[bi]):
                            bi = k2
                    if bi is not None:
                        ca, cb = pairs[bi]
                        tmp = dict(sol)
                        tmp[s1] = ca[0]
                        tmp[s2] = cb[0]
                        best = ds[bi]
                        sol = dict(tmp)
                        tot = [0.0] * NT
                        for ss in SLOTS:
                            rr, _, _ = cv(sol[ss])
                            for j in range(NT):
                                tot[j] += rr[j]
                        improved = True
                        log("  %s[双槽] %s + %s → DPS %s"
                            % (tag, s1, s2, format(best, ",")))
        if not improved:
            break
    log("  %s局部搜索完成：DPS %s ｜ 用时 %.1f s" % (tag, format(best, ","),
                                                    _t.time() - t0))
    return sol, best


# ==================================================================== 退火 / 迭代局部搜索
def anneal_search(O, sol, real_dps, budget_s=240.0, moves=(1, 2, 3), log=print, tag="",
                  seed=20260919, full_start=True, t_hot=0.06, t_cold=0.002, max_per_slot=0,
                  max_iters=0):
    """在「9 项抗性全封顶」空间里做**模拟退火 + 迭代局部搜索**，专治贪心的局部最优。

    与 `local_search` 的区别：这个会**主动接受变差的解**（温度内），因此能跳出
    「单槽/双槽换不动、但三槽联动能大幅提升」的陷阱。用于「测试算法极限」。

    ★ 停止条件二选一：
      · `max_iters > 0` —— **按迭代次数**停（+ 由 `seed` 定死随机流 ⇒ **完全可复现**）
      · `max_iters = 0` —— 按**墙钟预算** `budget_s` 停（机器负载会影响迭代数 ⇒ 不可复现）

    返回 (best_sol, best_dps)。`real_dps(sol)` 应自带缓存。
    """
    import math
    import random
    import time as _t
    t0 = _t.time()
    SLOTS = list(O.SLOTS)
    NEED, TYPES = O.NEED, O.TYPES
    NT = len(TYPES)

    C = {}
    for s in SLOTS:
        lst = O.prune_cands(O.slot_cands(s))
        lst.sort(key=lambda c: (c[0][0] if isinstance(c[0], tuple) else c[0],))
        seen, uniq = set(), []
        for c in lst:
            sig = (c[1], c[2], c[3])
            if sig in seen:
                continue
            seen.add(sig)
            uniq.append(c)
        C[s] = uniq
    log("  %s退火候选池：%s" % (tag, " ".join("%s=%d" % (s, len(C[s])) for s in SLOTS)))

    def vres(tri):
        r, _, _ = vecs_of(O, tri)
        return r

    cur = {s: tuple(sol[s]) for s in SLOTS}
    cur_r = [0.0] * NT
    for s in SLOTS:
        r = vres(cur[s])
        for i in range(NT):
            cur_r[i] += r[i]

    def feasible(r):
        return all(r[i] >= NEED[TYPES[i]] - 1e-9 for i in range(NT))

    cur_dps = real_dps(cur)
    best_sol, best_dps = dict(cur), cur_dps
    log("  %s退火起点 DPS = %s（预算 %.0f s）" % (tag, format(cur_dps, ","), budget_s))

    rnd = random.Random(seed)
    it, acc, up = 0, 0, 0
    while True:
        el = _t.time() - t0
        if max_iters > 0:                      # ★ 按迭代数（可复现）
            if it >= max_iters:
                break
            frac = it / float(max_iters)
        else:                                  # 按墙钟预算
            if el >= budget_s:
                break
            frac = el / budget_s
        # 温度：指数降温
        temp = t_hot * (t_cold / t_hot) ** min(1.0, frac)
        it += 1
        k = rnd.choice(moves)
        picks = rnd.sample(SLOTS, k)
        base = list(cur_r)
        for s in picks:
            rr = vres(cur[s])
            for i in range(NT):
                base[i] -= rr[i]
        cand = dict(cur)
        okp = True
        for s in picks:
            lst = C[s]
            if not lst:
                okp = False
                break
            # 选一个「加入后仍可能满抗」的候选（随机，不用代理排序 —— 否则退化成贪心）
            idxs = []
            for j in range(len(lst)):
                rj = lst[j][1]
                good = True
                for i in range(NT):
                    if base[i] + rj[i] < NEED[TYPES[i]] - 1e-9:
                        good = False
                        break
                if good:
                    idxs.append(j)
            if not idxs:
                okp = False
                break
            j = idxs[rnd.randrange(len(idxs))]
            cand[s] = lst[j][0]
            rj = lst[j][1]
            for i in range(NT):
                base[i] += rj[i]
        if not okp:
            continue
        d = real_dps(cand)
        if d >= cur_dps or rnd.random() < math.exp((d - cur_dps) / (temp * max(cur_dps, 1.0))):
            cur, cur_dps = cand, d
            cur_r = base
            acc += 1
            if d > best_dps + 0.5:
                best_dps, best_sol = d, dict(cand)
                up += 1
                log("  %s[退火 %.0fs] DPS %s（第 %d 次改善）"
                    % (tag, el, format(best_dps, ","), up))
    log("  %s退火结束：%d 次试探 / %d 次接受 / %d 次改善 ｜ %.0f s ｜ 最终 %s"
        % (tag, it, acc, up, _t.time() - t0, format(best_dps, ",")))
    return best_sol, best_dps


# ============================================================ LNS（大邻域搜索）
# ============================================================ 并行评估器（CPU 多核）
_PAR_CH = ''
_PAR_AR = ''


def _par_init(char, arch):
    """worker 初始化：记下 (char, arch) 并**把 worker 的 stdout 静音**。

    ★ 为什么必须在 worker 里重建评估：`real_dps` 是**闭包**（含 memo），
      跨进程无法 pickle。而 `plan_dps.dps_of(char, sol, arch)` 是纯函数式入口，
      用两个 str 就能在 worker 里重建**逐位相同**的口径。
    """
    global _PAR_CH, _PAR_AR
    _PAR_CH, _PAR_AR = char, arch
    os.environ['GD_QUIET'] = '1'


def _par_eval(sol):
    import io as _io
    from contextlib import redirect_stdout as _ro
    import plan_dps as _PD
    with _ro(_io.StringIO()):
        return _PD.dps_of(_PAR_CH, sol, _PAR_AR)['dps']


class ParEval:
    """邻域评估的**进程池并行器**（LNS 每轮的候选互相独立 ⇒ 天然可并行）。

    ★ 实测（2026-09-20，RTX 5090 机器 / 32 逻辑核，480 个候选）：
        串行 5.198 ms/次
        8  进程 0.375 ms/次  ⇒ **13.9×**
        16 进程 0.274 ms/次  ⇒ **19.0×**  ← 最优
        24 进程 3.254 ms/次  ⇒ 1.6×（超订：16 物理核 + 内存带宽饱和）
      ⇒ 默认 `procs=16`。**不是进程越多越好。**

    ★ 用法（池建一次、全程复用 —— 建池 + 预热约 3 s，必须摊到整轮搜索上）：
        pe = ParEval(char, arch, procs=16).warm(sol)
        ds = pe.batch(list_of_sols)
        pe.close()

    ⚠ **不要与「链级并行」（autobuild `--chains N`）叠加** —— 那会变成
      `N × procs` 个进程，远超物理核（实测 24 进程已开始劣化）。
    """

    def __init__(self, char, arch, procs=16, min_batch=6):
        self.char, self.arch = char, arch
        self.procs = max(1, int(procs))
        self.min_batch = max(1, int(min_batch))
        self.pool = None
        self.n_eval = 0
        self.t_eval = 0.0

    def warm(self, sol):
        """建池 + 让**每个** worker 都加载一次 DB（否则首个 batch 要等它们冷启动）。"""
        import time as _t
        from concurrent.futures import ProcessPoolExecutor
        os.environ.setdefault('GD_QUIET', '1')
        w = [{k: list(v) for k, v in sol.items()}] * self.procs
        t0 = _t.perf_counter()
        self.pool = ProcessPoolExecutor(max_workers=self.procs,
                                        initializer=_par_init,
                                        initargs=(self.char, self.arch))
        list(self.pool.map(_par_eval, w, chunksize=1))
        self.t_warm = _t.perf_counter() - t0
        return self

    def batch(self, sols):
        import time as _t
        if not sols:
            return []
        payload = [{k: list(v) for k, v in s.items()} for s in sols]
        n = self.procs
        t0 = _t.perf_counter()
        r = list(self.pool.map(_par_eval, payload,
                               chunksize=max(1, len(payload) // (n * 3))))
        self.t_eval += _t.perf_counter() - t0
        self.n_eval += len(payload)
        return r

    def close(self):
        if self.pool is not None:
            self.pool.shutdown()
            self.pool = None


def lns_search(O, sol, real_dps, budget_s=0.0, max_iters=0, seed=20260920,
               ks=(2, 3, 4), topk=20, eval_cap=260, hist_len=50,
               log=print, tag="", require_full=True, par=None,
               patience=0, proxy_tol=0.0):
    """**LNS（Large Neighborhood Search）+ LAHC 接受准则** —— 模拟退火的替代品。

    ★ 为什么要换掉退火（2026-09-20 实测归因）
      `anneal_search` 有两个结构性缺陷，导致**预算越大结果越差**：
      ① 温度挂在**墙钟**上 ⇒ 420 s 预算下指数降温极慢，大半时间在高温随机游走；
      ② `moves` 随预算变大（≥180 s 改用四槽联动），而随机挑 4 槽几乎不可能同时
         满足满抗 ⇒ 绝大多数迭代 `okp=False` **直接 continue，连评估都没做**。
      实测：从 30 链最优解（94,474）再退火 47 s 就到 117,457（+24.3%），
      而 30 条链各跑满 420 s 只到 94,474 —— 预算成了负资产。

    ★ 本实现（对应 GitHub 上的主流答案：Pisinger & Ropke 2010 的 LNS，
      也是 OR-Tools CP-SAT 官方文档推荐的「大问题」解法）
      每一轮 = 一次 **destroy & repair**：
        destroy —— 随机挑 `k` 个槽（k ∈ `ks`）放开，其余槽**硬固定**
        repair  —— 在这 k 个槽内做**精确枚举**（不是随机游走！）：
                     · 每槽按线性代理降序取 `topk` 个候选
                     · `k` 槽的笛卡尔积用 **numpy 广播**一次算完（抗性 + 代理），
                       再按「剩余抗性需求」掩码剪枝 ⇒ 邻域内**所有满抗组合都被看到**
                     · 只对代理分最高的 `eval_cap` 个算真实 DPS（真实评估是 2.4 ms 的
                       瓶颈，代理只是**排序**用）
        accept  —— **LAHC**（Late Acceptance Hill Climbing, Burke & Bykov 2017）：
                    维护长度 `hist_len` 的历史队列，新解只要不差于 `hist[i]`
                    就接受。**无温度参数、无降温调度** ⇒ 不会重蹈退火的覆辙，
                    且实测比 SA 更稳（见 SKILL §3.15）。

    ★ 相比 `local_search` 的关键差别
      `local_search` 的双槽联动默认 `pair_k=2` —— 每槽**只取 2 个候选**，
      等于没搜索；且槽对是**固定顺序**遍历。这里每槽取 `topk=20`，
      槽组合是**随机**的，并且能一次吃 2/3/4 个槽的联动。

    停止条件二选一（与退火一致，保证可复现性口径相同）：
      · `max_iters > 0` —— 按轮数停（随机流由 `seed` 定死 ⇒ **完全可复现**）
      · `budget_s  > 0` —— 按墙钟预算停

    ★ 2026-09-22 新增两个**收敛加速**开关（默认关闭 ⇒ 逐位零漂移）：
      · `patience > 0` —— **连续 N 轮没有刷新最优就提前结束**。LNS 后期大量轮次
        在做无效探索（实测 Sam 400 轮里最后 ~40% 从未刷新最优），早停把这段砍掉。
      · `proxy_tol > 0` —— **代理窗口截断**：邻域内按线性代理降序，只保留
        代理分 ≥ 最优代理 × (1 − tol) 的组合再送真实评估。代理与真实 DPS 的
        秩相关实测极高（见 `docs/pitfalls.md` #89），所以这一步几乎不丢解。

    返回 (best_sol, best_dps)。
    """
    import random
    import time as _t
    t0 = _t.time()
    _par = par                       # 进程池并行器（None = 纯串行）
    _memo = getattr(real_dps, 'memo', None)   # 调用方的评估缓存（并行路径要复用）
    SLOTS = list(O.SLOTS)
    NEED, TYPES = O.NEED, O.TYPES
    NT = len(TYPES)
    KEYS = list(O.FKEYS)
    W = O.W_DMG

    def proxy(c):
        return sum(c[2][i] * W.get(KEYS[i], 0.0) for i in range(len(KEYS)))

    # ---- 候选池：每槽按代理降序取 topk（与 local_search 的 feas() 同口径）
    POOL = {}
    for s in SLOTS:
        lst = O.prune_cands(O.slot_cands(s))
        lst = list(lst)
        lst.sort(key=lambda c: -proxy(c))
        seen, uniq = set(), []
        for c in lst:
            sig = (c[1], c[2], c[3])
            if sig in seen:
                continue
            seen.add(sig)
            uniq.append(c)
        keep = uniq[:topk] if (topk and topk > 0) else uniq
        # 「当前选择」必须留在池里 —— 否则邻域里连原地都走不回去
        if not any(c[0] == sol[s] for c in keep):
            for c in uniq:
                if c[0] == sol[s]:
                    keep.append(c)
                    break
        POOL[s] = keep
    log("  %sLNS 候选池：%s" % (tag, " ".join("%s=%d" % (s, len(POOL[s])) for s in SLOTS)))

    def vres(tri):
        r, _, _ = vecs_of(O, tri)
        return r

    cur = {s: tuple(sol[s]) for s in SLOTS}
    cur_r = [0.0] * NT
    for s in SLOTS:
        r = vres(cur[s])
        for i in range(NT):
            cur_r[i] += r[i]

    def feasible(r):
        return all(r[i] >= NEED[TYPES[i]] - 1e-9 for i in range(NT))

    if not feasible(cur_r):
        log("  %sLNS 起点未满抗 —— 需先跑满抗搜索，跳过" % tag)
        return cur, real_dps(cur)

    cur_dps = real_dps(cur)
    best_sol, best_dps = dict(cur), cur_dps
    log("  %sLNS 起点 DPS = %s（k∈%s ｜ topk=%d ｜ eval_cap=%d ｜ LAHC L=%d）"
        % (tag, format(cur_dps, ","), tuple(ks), topk, eval_cap, hist_len))

    import numpy as np
    rnd = random.Random(seed)
    hist = [cur_dps] * max(1, hist_len)
    it, acc, up, evals, empty, cut = 0, 0, 0, 0, 0, 0
    _last_up = 0                       # 最近一次刷新最优的轮号（patience 用）
    _rt = []                           # ★ 逐轮墙钟（回答「一轮大概多久」—— 原先完全看不见）
    # ★ 分段计时（`GD_LNS_SPLIT=1`）：回答「**哪一段能吃 GPU**」。
    #   邻域构造 = numpy 广播（可上 GPU）；真实评估 = 逐候选的**标量 Python**（上不了）。
    _SPLIT = os.environ.get('GD_LNS_SPLIT', '') not in ('', '0')
    _t_neb = _t_ev = 0.0
    _ARR = {}                       # 槽 → (抗性矩阵, 代理数组) 缓存

    def _arr(s):
        v = _ARR.get(s)
        if v is None:
            v = (np.asarray([c[1] for c in POOL[s]], float),
                 np.asarray([proxy(c) for c in POOL[s]], float))
            _ARR[s] = v
        return v

    budget = float(budget_s or 0.0)
    while True:
        if max_iters > 0:
            if it >= max_iters:
                break
        else:
            if budget <= 0 or (_t.time() - t0) >= budget:
                break
        it += 1
        _tr = _t.time()                # ★ 单轮计时起点（destroy & repair 一整轮）
        k = rnd.choice(ks)
        k = min(k, len(SLOTS))
        picks = rnd.sample(SLOTS, k)

        # ---- destroy：把 picks 的抗性贡献从总量里摘掉
        fixed_r = list(cur_r)
        for s in picks:
            rr = vres(cur[s])
            for i in range(NT):
                fixed_r[i] -= rr[i]
        need2 = np.asarray([NEED[TYPES[i]] - fixed_r[i] for i in range(NT)], float)

        Rs = [_arr(s)[0] for s in picks]
        Ps = [_arr(s)[1] for s in picks]
        shape = [len(p) for p in Ps]
        total = 1
        for n in shape:
            total *= n
        if total > 4_000_000:               # 安全阀（正常 topk=20×k=4 = 160k）
            continue

        # ---- repair：笛卡尔积的「抗性和」与「代理和」，numpy 广播一次算完
        #   ⚠ 变量名用 `rac` 而不是 `acc` —— `acc` 是接受计数器，早先版本被覆盖过。
        rac = np.zeros((1, NT))
        for Rm in Rs:
            rac = (rac[:, None, :] + Rm[None, :, :]).reshape(-1, NT)
        psc = np.zeros(1)
        for Pm in Ps:
            psc = (psc[:, None] + Pm[None, :]).reshape(-1)

        if require_full:
            mask = np.all(rac >= need2 - 1e-9, axis=1)
        else:
            mask = np.ones(len(psc), bool)
        idx = np.flatnonzero(mask)
        if idx.size == 0:
            empty += 1
            continue
        order = idx[np.argsort(-psc[idx])]
        # ★ 代理窗口截断（`proxy_tol > 0` 才生效）：线性代理与真实 DPS 的秩相关
        #   很高 ⇒ 距最优代理太远的组合几乎不可能赢。保留窗口内前 `eval_cap` 个。
        if proxy_tol and proxy_tol > 0 and order.size > eval_cap:
            _pmax = float(psc[order[0]])
            if _pmax > 0:
                _keep = order[psc[order] >= _pmax * (1.0 - float(proxy_tol))]
                if _keep.size:
                    order = _keep
        order = order[:eval_cap]
        cut += max(0, idx.size - int(order.size))

        # ---- 构造本轮全部 trial（先造齐，才能整批并行；避免逐个 submit 的往返）
        trials = []
        for flat in order:
            rem = int(flat)
            ci = []
            for n in reversed(shape):
                ci.append(rem % n)
                rem //= n
            ci.reverse()
            trial = dict(cur)
            for s, j in zip(picks, ci):
                trial[s] = POOL[s][j][0]
            trials.append(trial)
        if not trials:
            continue
        # ---- 评估：统一走 `eval_trials`（有并行器且本批够大 ⇒ 进程池整批并行）
        if _SPLIT:
            _t_neb += _t.time() - _tr
        _te0 = _t.time()
        ds = eval_trials(trials, real_dps, _par, _memo, SLOTS)
        if _SPLIT:
            _t_ev += _t.time() - _te0
        evals += len(trials)
        cand_best, cand_sol = None, None
        for d, trial in zip(ds, trials):
            if cand_best is None or d > cand_best:
                cand_best, cand_sol = d, trial
        if cand_best is None:
            _rt.append(_t.time() - _tr)
            continue

        # ---- LAHC 接受：不差于「上 L 轮里同一位置的记录」就接受
        if cand_best >= hist[it % len(hist)] or cand_best >= cur_dps:
            cur, cur_dps = cand_sol, cand_best
            cur_r = list(fixed_r)
            for s in picks:
                rr = vres(cur[s])
                for i in range(NT):
                    cur_r[i] += rr[i]
            acc += 1
            if cand_best > best_dps + 0.5:
                best_dps, best_sol = cand_best, dict(cand_sol)
                up += 1
                _last_up = it
                log("  %s[LNS k=%d %.0fs] DPS %s（第 %d 次改善 ｜ 第 %d 轮 ｜ 真实评估 %d）"
                    % (tag, k, _t.time() - t0, format(best_dps, ","), up, it, evals))
        hist[it % len(hist)] = cur_dps
        _rt.append(_t.time() - _tr)

        # ★ 收敛早停：连续 `patience` 轮没刷新最优 ⇒ 后面基本是白跑
        if patience and patience > 0 and (it - _last_up) >= int(patience):
            _rt.append(_t.time() - _tr)
            log("  %sLNS 早停：连续 %d 轮无改善（第 %d 轮停）" % (tag, int(patience), it))
            break

    log("  %sLNS 结束：%d 轮 / %d 次真实评估 / %d 次接受 / %d 次改善 / %d 次空邻域"
        " / %d 个组合被代理窗口裁掉 ｜ %.1f s ｜ 最终 %s"
        % (tag, it, evals, acc, up, empty, cut, _t.time() - t0, format(best_dps, ",")))
    if _SPLIT:
        _tot = _t_neb + _t_ev
        log('  %sLNS 分段：邻域构造(numpy，**可上 GPU**) %.1f s (%.0f%%) ｜ '
            '真实评估(标量 Python，上不了 GPU) %.1f s (%.0f%%)'
            % (tag, _t_neb, 100.0 * _t_neb / max(_tot, 1e-9), _t_ev,
               100.0 * _t_ev / max(_tot, 1e-9)))
    if _rt:
        # ★ 逐轮耗时分布（原先完全不可见）。口径：**一轮 = 一次 destroy & repair**
        #   （随机放开 k 个槽 → 枚举 topk^k 组合 → 抗性掩码 → 代理降序取 `eval_cap`
        #     个算**真实** DPS）。`均评估/轮` 只数**真实**评估，不含被代理裁掉的。
        _rs = sorted(_rt)
        _pk = lambda q: _rs[min(len(_rs) - 1, int(q * len(_rs)))]          # noqa: E731
        log("  %sLNS 单轮耗时：均值 %.0f ms ｜ 中位 %.0f ms ｜ p90 %.0f ms ｜"
            " 均 %.1f 次真实评估/轮 ｜ %d 轮 / %.1f 轮/秒"
            % (tag, 1000.0 * sum(_rt) / len(_rt), 1000.0 * _pk(0.5), 1000.0 * _pk(0.9),
               float(evals) / max(1, it), len(_rt),
               len(_rt) / max(1e-9, _t.time() - t0)))
    return best_sol, best_dps


# ==================================================================== CLI
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("char")
    ap.add_argument("--arch", default="wolf_nightblade")
    ap.add_argument("--passes", type=int, default=2)
    ap.add_argument("--topk", type=int, default=0,
                    help="每槽按代理预选后实算 DPS 的候选数（<=0 = 全部可行）")
    ap.add_argument("--pair-k", type=int, default=3, help="双槽联动的每槽候选数（0=关）")
    ap.add_argument("--max-per-slot", type=int, default=120,
                    help="每槽实算 DPS 的硬上限（topk<=0 时生效）")
    ap.add_argument("--start", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    O = load_opt()
    DB.load()

    start = a.start or str(paths.DATA_DIR / "plans" / ("%s_current.json" % a.char.lstrip("_")))
    sol = {k: tuple(v) for k, v in json.load(open(start, encoding="utf-8")).items()}
    sol = {s: sol[s] for s in O.SLOTS}

    import plan_dps as PD
    memo = {}

    def real_dps(s):
        key = tuple(s[x] for x in O.SLOTS)
        if key not in memo:
            memo[key] = PD.dps_of(a.char, {k: list(v) for k, v in s.items()},
                                  a.arch)["dps"]
        return memo[key]

    sol, best = local_search(O, sol, real_dps, a.passes, a.topk, a.pair_k,
                             max_per_slot=a.max_per_slot)
    out = a.out or str(paths.DATA_DIR / "plans" / ("%s_tuned.json" % a.char.lstrip("_")))
    json.dump({k: list(v) for k, v in sol.items()}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("最终 DPS = %s" % format(best, ","))
    print("→ %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
