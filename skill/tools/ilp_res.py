"""用 ILP（HiGHS）**精确求解**「满抗可行域内伤害代理最高」的配装。

为什么要换掉束搜索
------------------
`gd auto` 的满抗起点来自 `gd/opt.py:run_search`（束搜索 × 6 次重启），实测 **74 秒**，
占 `--extreme` 总耗时的大头，而且它**用的是线程池**（`ThreadPoolExecutor`）——
束搜索主循环是「Python 逐槽展开 + 大量小数组 numpy 运算」，小数组几乎不释放 GIL，
所以 6 次重启实际是串行（74s ÷ 6 = 12s/次，正是单次耗时）。

更严重的是**目标函数错了**。`_obj()` 在 `goal='worst'` 下优化的是
    worst*1000 + cover*0.05 - pen + SKILL_K*sk - LEG_PEN*lg
而 `GD_FULL=1` 把每条抗性都做成硬约束后，`worst ≡ 1.0`、`cover ≡ 1070` 恒定，
于是这个表达式退化成「技能分 − 紫装惩罚」，**完全不含伤害代理项**。
结果：束搜索交出来的「满抗起点」在伤害维度上是随机的。

ILP 的建模
----------
决策变量  x[s][c] ∈ {0,1}      槽 s 选候选 c
  每槽恰选一件   Σ_c x[s][c] = 1
  抗性硬约束     Σ_{s,c} res[s][c][t] · x[s][c] ≥ NEED[t]          （9 条）
  紫/蓝名额      MIN_LEG ≤ Σ leg·x ≤ MAX_LEG ；MIN_EPIC ≤ Σ epic·x ≤ MAX_EPIC
  双手武器一致   x[主手=双手] − x[副手=空] ≤ 0                     （见下）
目标            max Σ_{s,c} [ out·W_DMG + SKILL_K·skill − LEG_PEN·leg ] · x[s][c]

规模实测（Sam lv71 / --extreme / 14 槽）：**8982 个二元变量，23 条约束**。

为什么这是**精确最优**（不是近似）
----------------------------------
`gd/opt.py:prune_cands` 的剪枝是 `_skyline` 支配剪枝：只丢弃「同槽另一件各维全面 ≥」
的候选。本目标对 (抗性, 输出, 技能分) 每一维单调不减，所以被支配的候选**永不出现在
最优解里** —— 在剪枝后的集合上求最优 = 在全量候选上求最优。

为什么加「副手为空」选项
------------------------
游戏规则：双手武器占满两只手 ⇒ 副手必须空。原模型 `choices('副手')` **永远不含空选项**，
只能在事后由 `autobuild._weapon_ok` 兜底（发现非法就把武器槽整体退回存档现状，等于白优化）。
这里给副手补一个 ∅ 候选，再把约束写成 `x[主手2H] ≤ x[副手∅]`，从根上消除这种非法组合。

★ 两个必须知道的坑
------------------
1. **不能用 `--compare` 当自己的参数名**。`gd/opt.py:720` 有
   `_CMP_ONLY = '--compare' in sys.argv`，一旦命中就会把 `AUTO_POOL/AUTO_COMP` 全设成 `{}`
   —— 候选池只剩硬编码的几十件，问题凭空小 5.5 倍，测出来的"快"全是假的。
   本模块用 `--bench`，并在 import 前防御性剥离 `--compare`。
2. **环境变量必须在 `import gd.opt` 之前铺好**（候选池在模块级构建），见 `prepare_env`。

用法
----
    python tools/ilp_res.py Sam --with-weapon                 # 独立跑，打印结果与自检
    python tools/ilp_res.py Sam --with-weapon --extreme       # 全池（同 gd auto --extreme）
    python tools/ilp_res.py Sam --with-weapon --extreme --bench   # 同时跑束搜索对照
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(HERE))

# 空候选（副手「不装东西」）的三元组，形状必须和 slot_cands 的输出一致
_EMPTY_TRI = (None, None, None, None, None)


# ==================================================================== 环境
def prepare_env(char, with_weapon, extreme=False):
    """铺好 gd.opt 需要的**模块级**环境变量，并返回 (char, level, arch, slots)。

    ★ 顺序铁律：`gd/opt.py` 在 **import 时**就读 GD_MAX_ILVL / GD_ARCHETYPE /
      GD_SLOTS / 各种 TOPN 来建候选池。所以本函数必须在 `import gd.opt` **之前**调用。
    """
    import autobuild as AB
    from gd import paths
    from gd.save import core

    char = AB._char_key(char)
    level, classids, skills = AB.read_meta(char)
    archs = paths.load_json("archetypes.json") or {}
    arch = AB.pick_archetype(classids, skills, archs)

    slots = list(core.SLOTS)
    if with_weapon:
        slots += ["主手", "副手"]

    cur = AB.current_plan(char, with_weapon)

    os.environ["GD_MAX_ILVL"] = str(level)
    os.environ["GD_ARCHETYPE"] = arch
    os.environ["GD_SLOTS"] = ",".join(slots)
    os.environ["GD_CUR_JSON"] = json.dumps(
        {k: [v[0]] for k, v in cur.items() if v and v[0]}, ensure_ascii=False)
    os.environ.setdefault("GD_QUIET", "1")
    if extreme:
        os.environ["GD_AUTO_TOPN"] = "150"
        os.environ["GD_COMP_TOPN"] = "24"
        os.environ["GD_AUG_TOPN"] = "14"
        os.environ["GD_DEDUP"] = "6"
        os.environ["GD_POOL_DMG_TOPN"] = "400"
        os.environ.setdefault("GD_OVER_PEN", "0")
    return char, level, arch, slots


def import_opt():
    """导入 gd.opt —— 自动剥掉会篡改候选池的 `--compare`（见模块开头「坑 1」）。"""
    saved = list(sys.argv)
    stripped = [x for x in sys.argv if x != "--compare"]
    if len(stripped) != len(saved):
        print("  ⚠ 已从 sys.argv 剥掉 --compare（gd.opt 的保留开关，会禁用候选池）")
    sys.argv = stripped
    try:
        import gd.opt as O
    finally:
        sys.argv = saved
    return O


# ==================================================================== 建模
def _empty_cand(O):
    """副手 ∅ 候选：七元组 (tri, rvec, ovec, skill, green, epic, leg)"""
    return (_EMPTY_TRI, (0.0,) * O.NT, (0.0,) * O.NF, 0.0, 0, 0, 0)


def build_candmap(O, slots=None, allow_empty_offhand=True):
    """槽 -> 剪枝后候选表（与束搜索用**完全相同**的候选集，保证可比）"""
    slots = slots or list(O.SLOTS)
    cm = {}
    for s in sorted(set(slots)):
        cm[s] = list(O.prune_cands(O.slot_cands(s)))
    if allow_empty_offhand and "副手" in cm and "主手" in cm:
        cm["副手"] = [_empty_cand(O)] + cm["副手"]
    return cm


def _is_2h(O, tri):
    gid = tri[0] if tri else None
    bm = (((O._IT.get(gid) or {}).get("n") or "") if gid else "").lower()
    return ("2h" in bm) or ("twohand" in bm)


# ================================================================ 支配剪枝
# ★ 为什么可以剪掉「被支配」的候选（**精确**，不是近似）
#   候选 A 支配候选 B ⇔ ① res_A ≥ res_B（9 条抗性逐维）② obj_A ≥ obj_B
#   ③ 不允许「A 是双手而 B 不是」（否则换过去会凭空多出「副手必须为空」的约束）。
#   此时任何用到 B 的解都能把 B 就地换成 A：抗性只增不减 ⇒ 可行性保持；
#   目标不降 ⇒ 最优值不变。故 B 可删，**最优解与最优值逐位不变**。
#
#   ★ 与 `gd/opt.py:_skyline` 的区别：skyline 要求**输出向量的每一维**都不劣，
#     本判据只要求**目标（输出各维的线性组合）**不劣 ⇒ 严格更强。
#     实测 Sam lv73/--extreme/14 槽：38,267 → 13,034 变量（34.1%），
#     MIP 求解 9.65 s → 3.45 s，目标与解**逐位相同**（陷阱 #93）。
#   ★ 快速路径只影响「剪得多不多」，不影响正确性：误判为「保留」只会多留候选。
_DOM_MEMO = {}


def _dom_rows(O, cs, dmg_w):
    """候选 → 判据矩阵 [9 抗性 …, 目标]（目标 = 输出线性组合 + 技能分 − 紫装惩罚）"""
    import numpy as np
    NF = O.NF
    if not cs:
        return np.zeros((0, O.NT + 1))
    Rv = np.array([list(c[1]) for c in cs], float)
    obj = np.array([sum(c[2][k] * dmg_w[k] for k in range(NF))
                    + O.SKILL_K * c[3] - O.LEG_PEN * c[6] for c in cs])
    return np.column_stack([Rv, obj])


def _dom_core(rows, order, is2h=None, fixed_first=False):
    """返回应保留的下标（升序）。`order` = 按目标降序访问的顺序。"""
    import numpy as np
    n, d = rows.shape
    ndom = d - 1 if is2h is not None else d        # is2h 那一维不参与快速路径
    K = np.empty_like(rows)
    runmax = np.full(ndom, -np.inf)
    m, kept = 0, []
    for i in order:
        ri = rows[i]
        if m and not (ri[:ndom] > runmax).any():
            ok = (K[:m] >= ri).all(axis=1)          # 慢路径：逐行比
            if is2h is not None:
                # A(is2h=1) 不能替 B(is2h=0)
                ok &= (K[:m, d - 1] <= ri[d - 1])
            if ok.any():
                continue
        K[m] = ri
        m += 1
        kept.append(int(i))
        np.maximum(runmax, ri[:ndom], out=runmax)
    return kept


def _dom_key(O, candmap, slots):
    """内容指纹：任何影响判据的输入变了就换 key（含 gid 顺序、抗性需求、权重）。"""
    try:
        import hashlib
        import numpy as np
        h = hashlib.sha1()
        dmg_w = np.array([O.W_DMG.get(k, 0.0) for k in O.FKEYS], float)
        h.update(("|".join(O.TYPES) + "|" + repr(sorted(O.NEED.items())) + "|"
                  + "|".join(O.FKEYS) + "|%r|%r|%r"
                  % (tuple(dmg_w.tolist()), O.SKILL_K, O.LEG_PEN)).encode("utf-8"))
        for s in slots:
            cs = candmap[s]
            h.update(("##%s|" % s).encode("utf-8"))
            h.update(_dom_rows(O, cs, dmg_w).tobytes())
            for c in cs:
                h.update(("%s|" % (c[0],)).encode("utf-8"))
        return h.hexdigest()[:20]
    except Exception:
        return None


def _dom_cache_path(key):
    from gd import paths as _P
    return _P.CACHE_DIR / "prune" / ("dom_%s.pkl" % key)


def dom_reduce(O, candmap, log=print, use_cache=True):
    """对每个槽做 (9 抗性, 目标[, 双手]) 支配剪枝，返回新 candmap。**精确。**"""
    import time as _t
    import numpy as np
    env = os.environ.get("GD_ILP_DOM", "1").strip().lower()
    if env in ("0", "off", "no", "false"):
        return candmap
    slots = [s for s in O.SLOTS if s in candmap]
    dmg_w = np.array([O.W_DMG.get(k, 0.0) for k in O.FKEYS], float)

    key = _dom_key(O, candmap, slots) if use_cache else None
    masks = None
    if key is not None:
        masks = _DOM_MEMO.get(key)
        if masks is None:
            try:
                import pickle
                p = _dom_cache_path(key)
                if p.exists():
                    masks = pickle.loads(p.read_bytes())
            except Exception:
                masks = None
        if masks is not None and set(masks) != set(slots):
            masks = None

    t0 = _t.time()
    if masks is None:
        masks = {}
        for s in slots:
            cs = candmap[s]
            if len(cs) < 3:
                masks[s] = list(range(len(cs)))
                continue
            is2h = None
            if s == "主手":
                is2h = np.array([1.0 if _is_2h(O, c[0]) else 0.0 for c in cs])
            rows = _dom_rows(O, cs, dmg_w)
            if is2h is not None:
                rows = np.column_stack([rows, is2h])
                order = np.argsort(-rows[:, -2], kind="stable").tolist()
            else:
                order = np.argsort(-rows[:, -1], kind="stable").tolist()
            if s == "副手":                      # ∅ 候选必须留（双手约束要用它）
                order = [0] + [i for i in order if i != 0]
            masks[s] = _dom_core(rows, order, is2h=is2h, fixed_first=(s == "副手"))
        if key is not None:
            _DOM_MEMO[key] = masks
            try:
                import pickle
                p = _dom_cache_path(key)
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_suffix(".tmp")
                tmp.write_bytes(pickle.dumps(masks, protocol=4))
                tmp.replace(p)
            except Exception:
                pass
        hit = "未命中（已落盘）"
    else:
        hit = "缓存命中"

    out = {}
    for s in slots:
        ks = set(masks[s])
        out[s] = [c for i, c in enumerate(candmap[s]) if i in ks]
    n0 = sum(len(candmap[s]) for s in slots)
    n1 = sum(len(out[s]) for s in slots)
    log("  [ILP 支配剪枝] %s ｜ %.2f s ｜ 变量 %d → %d（%.1f%%）"
        % (hit, _t.time() - t0, n0, n1, 100.0 * n1 / max(1, n0)))
    log("    逐槽 " + " ｜ ".join("%s %d→%d" % (s, len(candmap[s]), len(out[s]))
                                 for s in slots))
    return out


# ==================================================================== 建模
class Model:
    """多选择背包 MILP：每槽选 1 件 + 9 条抗性硬约束 + 紫/蓝名额 + 双手一致。

        决策变量  x[s][c] ∈ {0,1}      槽 s 选候选 c
        目标      max Σ [ 输出·W_DMG + SKILL_K·技能分 − LEG_PEN·传奇 ]

    ★ 为什么它能替掉束搜索（`gd/opt.py:run_search`）
      以 Sam lv71 / `--extreme` / 14 槽 / 8982 变量实测：

        束搜索 宽12000×6重启    74.4 s  启发式。且 GD_FULL=1 下 worst≡1、cover≡1070
                                        恒定，目标退化成「技能分 − 紫装惩罚」，
                                        **完全不含伤害项** → 满抗起点在伤害维度上是随机的
        LP 松弛                  0.03 s  全局上界 2808.19（8982 变量里只有 11 个分数分量）
        本模型 gap 1e-3            9.0 s  全局最优 2746.70  ← 生产默认
        本模型 gap 1e-2            5.9 s  **偶尔**停在次优（2741.10，差 0.2%）

      ⚠ 在 `gd auto` 进程内同一模型实测要 **17.8~20.0 s**（独立跑 9~12 s）。
      已验证**不是**环境/候选池差异（两种环境指纹逐一相同，10.2 vs 9.0 s），
      也**不是**能靠子进程隔离解决的（隔离后反而 23.8 s）。原因未完全归因；
      但即便按 20 s 算，仍比束搜索快 3.7 倍。

    ★ HiGHS 只能单线程 —— 别在这上面浪费时间
      实测 `threads=4/16/32` 与 `parallel='on'` 会把模型直接跑成 `Not Set`
      （0.00 s / gap inf）：本机 highspy 构建没编并行 MIP。因此这里固定 `threads=1`，
      多核只能靠「**多条独立链并行**」来吃，不是「把一个 ILP 拆开」——
      后者实测更慢（按 Σleg=L 拆成 15 个子问题并行要 27.7 s，因为子问题形状更难）。
    """

    def __init__(self, O, candmap, min_leg=0, max_leg=None, min_epic=0,
                 max_epic=None, order_by_obj=False):
        import numpy as np

        self.O = O
        self.candmap = candmap
        self.slots = [s for s in O.SLOTS if s in candmap]
        NT, NF = O.NT, O.NF
        dmg_w = np.array([O.W_DMG.get(k, 0.0) for k in O.FKEYS], float)

        # ★ 变量顺序确实能左右 B&B，但「按目标降序」实测**更慢**（9.0 → 12.8 s），
        #   所以默认保持候选池原序。留着开关是为了以后试别的序（抗性序 / 比值序）。
        #   ⚠ 无论如何排序都**必须在这里**做完 —— A 矩阵（`add_row`）的列索引就是
        #     此刻的下标；建完矩阵再改下标会让约束指向错变量，得到**超过 LP 界**的
        #     「假快」解（实测目标 2891.45 > 上界 2808.19，耗时 1.5 s）。
        if order_by_obj:
            candmap = {s: [c for _, c in sorted(
                enumerate(cs),
                key=lambda ic: -(sum(ic[1][2][k] * dmg_w[k] for k in range(NF))
                                 + O.SKILL_K * ic[1][3] - O.LEG_PEN * ic[1][6]))]
                for s, cs in candmap.items()}
            self.candmap = candmap

        off, n = {}, 0
        for s in self.slots:
            off[s] = n
            n += len(candmap[s])
        self.off, self.n = off, n

        self.R = np.zeros((n, NT))          # 抗性（行 = 变量，列 = 抗性类型）
        self.leg = np.zeros(n)
        self.epi = np.zeros(n)
        self.obj = np.zeros(n)              # 目标贡献（越大越好）
        self.cost = np.zeros(n)             # HiGHS 是最小化 → 取负
        self.is2h = np.zeros(n, bool)
        for s in self.slots:
            o0 = off[s]
            for i, c in enumerate(candmap[s]):
                j = o0 + i
                self.R[j, :] = c[1]
                self.leg[j] = c[6]
                self.epi[j] = c[5]
                v = (sum(c[2][k] * dmg_w[k] for k in range(NF))
                     + O.SKILL_K * c[3] - O.LEG_PEN * c[6])
                self.obj[j] = v
                self.cost[j] = -v
                tri = c[0]
                if tri and tri[0]:
                    self.is2h[j] = _is_2h(O, tri)

        # 行：用 None 表示「这一侧无界」，序列化时再换 kHighsInf
        self.row_cols, self.row_vals, self.row_lb, self.row_ub = [], [], [], []

        for s in self.slots:                                   # 每槽恰选一件
            cols = list(range(off[s], off[s] + len(candmap[s])))
            self.add_row(cols, [1.0] * len(cols), 1.0, 1.0)

        self.need_rows = []
        for t in range(NT):                                    # 抗性 ≥ 需求
            nd = O.NEED[O.TYPES[t]]
            if nd <= 0:
                continue
            cols = np.flatnonzero(self.R[:, t]).tolist()
            self.add_row(cols, self.R[cols, t].tolist(), float(nd), None)
            self.need_rows.append(t)

        nsl = len(self.slots)                                  # 紫 / 蓝名额
        if min_leg > 0 or (max_leg is not None and max_leg < nsl):
            cols = np.flatnonzero(self.leg).tolist()
            self.add_row(cols, self.leg[cols].tolist(),
                         float(min_leg) if min_leg > 0 else None,
                         float(max_leg) if (max_leg is not None and max_leg < nsl) else None)
        if min_epic > 0 or (max_epic is not None and max_epic < nsl):
            cols = np.flatnonzero(self.epi).tolist()
            self.add_row(cols, self.epi[cols].tolist(),
                         float(min_epic) if min_epic > 0 else None,
                         float(max_epic) if (max_epic is not None and max_epic < nsl) else None)

        self.n2h = 0                                           # 双手 ⇒ 副手必空
        if "主手" in self.slots and "副手" in self.slots:
            e = off["副手"]                      # ∅ 恒在索引 0（见 build_candmap）
            for i, c in enumerate(candmap["主手"]):
                j = off["主手"] + i
                if self.is2h[j]:
                    self.add_row([j, e], [1.0, -1.0], None, 0.0)
                    self.n2h += 1

    def add_row(self, cols, vals, lo, hi):
        self.row_cols.append(cols)
        self.row_vals.append(vals)
        self.row_lb.append(lo)
        self.row_ub.append(hi)

    # ---------------------------------------------------------------- 求解
    def highs_lp(self, integrality):
        import numpy as np
        import highspy
        INF = highspy.kHighsInf

        ri, ci, vv = [], [], []
        for r, (cols, vals) in enumerate(zip(self.row_cols, self.row_vals)):
            ri.extend([r] * len(cols))
            ci.extend(cols)
            vv.extend(vals)
        ri = np.asarray(ri, np.int32)
        ci = np.asarray(ci, np.int32)
        vv = np.asarray(vv, np.float64)
        order = np.lexsort((ri, ci))
        ci_s, ri_s, vv_s = ci[order], ri[order], vv[order]
        start = np.zeros(self.n + 1, np.int32)
        np.add.at(start, ci_s + 1, 1)
        np.cumsum(start, out=start)

        lp = highspy.HighsLp()
        lp.num_col_ = self.n
        lp.num_row_ = len(self.row_lb)
        lp.col_cost_ = self.cost.tolist()
        lp.col_lower_ = np.zeros(self.n).tolist()
        lp.col_upper_ = np.ones(self.n).tolist()
        lp.row_lower_ = [(-INF if v is None else float(v)) for v in self.row_lb]
        lp.row_upper_ = [(INF if v is None else float(v)) for v in self.row_ub]
        lp.a_matrix_.format_ = highspy.MatrixFormat.kColwise
        lp.a_matrix_.start_ = start
        lp.a_matrix_.index_ = ri_s
        lp.a_matrix_.value_ = vv_s
        lp.integrality_ = [integrality] * self.n
        return lp

    def solve(self, time_limit=60.0, gap=1e-2, msg=0, log=print, mip_start=None,
              threads=1, extra_opts=None):
        """返回 (sol, info)。sol=None 表示没拿到可行解（调用方应回退束搜索）。"""
        import numpy as np
        import highspy

        lp = self.highs_lp(highspy.HighsVarType.kInteger)
        h = highspy.Highs()
        h.setOptionValue("output_flag", bool(msg))
        h.setOptionValue("time_limit", float(time_limit))
        h.setOptionValue("mip_rel_gap", float(gap))
        h.setOptionValue("presolve", "on")
        h.setOptionValue("threads", int(threads))      # ★ 只能 1，见类文档
        for k, v in (extra_opts or {}).items():
            try:
                h.setOptionValue(k, v)
            except Exception as e:
                log("    （HiGHS 选项 %s 不被支持：%s）" % (k, e))
        h.passModel(lp)
        if mip_start is not None:
            s = highspy.HighsSolution()
            s.col_value = np.asarray(mip_start, float).tolist()
            try:
                h.setSolution(s)
            except Exception as e:
                log("    （MIP start 被拒：%s）" % e)

        log("  [ILP] HiGHS ｜ 变量 %d ｜ 约束 %d ｜ 上限 %.0fs ｜ gap %.0e"
            % (self.n, len(self.row_lb), time_limit, gap))
        t0 = time.time()
        h.run()
        dt = time.time() - t0
        status = h.modelStatusToString(h.getModelStatus())
        obj = h.getObjectiveValue()
        try:
            mg = h.getInfo().mip_gap
        except Exception:
            mg = float("nan")

        info = {"solver": "highs", "status": status, "objective": -obj,
                "nvar": self.n, "ncon": len(self.row_lb), "seconds": dt,
                "mip_gap": mg, "two_h_constraints": self.n2h}
        log("  [ILP] %s ｜ 目标 %.2f ｜ %.2f s ｜ MIP gap %.2e"
            % (status, info["objective"], dt, mg))

        if status not in ("Optimal", "Time limit reached"):
            info["sol"] = None
            return None, info

        x = np.asarray(h.getSolution().col_value, float)
        sol = self.solution_of(x)
        if sol is None:
            log("  [ILP] ⚠ 有槽位未选出候选（状态 %s），交回调用方回退" % status)
        return sol, info

    def solution_of(self, x):
        """把 LP/MIP 解向量还原成 {slot: 5元组}；任一槽缺失就返回 None。"""
        import numpy as np
        sol = {}
        for s in self.slots:
            seg = x[self.off[s]: self.off[s] + len(self.candmap[s])]
            i = int(np.argmax(seg))
            if seg[i] < 0.5:
                return None
            sol[s] = self.candmap[s][i][0]
        if "副手" in self.slots and x[self.off["副手"]] > 0.5:
            sol["副手"] = _EMPTY_TRI            # ∅ 就是空三元组
        return sol


def solve(O, candmap, time_limit=60.0, min_leg=0, max_leg=None, min_epic=0,
          max_epic=None, msg=0, log=print, gap=1e-2):
    """HiGHS 求解（薄封装，保留旧签名）。gap 默认 1e-2：与 1e-4 同解但快 1.7 倍。

    返回 (sol, info)；sol 与 `gd.opt.run_search` 同格式：{slot: (b,c,a,p,s)}
    """
    M = Model(O, candmap, min_leg=min_leg, max_leg=max_leg,
              min_epic=min_epic, max_epic=max_epic)
    return M.solve(time_limit=time_limit, gap=gap, msg=msg, log=log)


def cap_search(O, candmap, gap=1e-2, time_limit=60.0, log=print):
    """满抗搜索高层入口。返回 (sol, info)；sol=None 时调用方应回退束搜索。

    `gd/opt.py` 的 GD_FULL / GOAL / MIN_LEG 等全局开关**与本入口无关**——
    抗性在模型里是硬约束，紫蓝名额放开到 0..14，与 `run_cap_search` 原口径一致。
    """
    t0 = time.time()
    candmap = dom_reduce(O, candmap, log=log)
    M = Model(O, candmap)
    sol, info = M.solve(time_limit=time_limit, gap=gap, log=log)
    info["seconds_total"] = time.time() - t0
    if sol is not None:
        rows, ok_all, lg, ep, weap_ok = verify(O, sol)
        info.update({"res_ok": ok_all, "legendary": lg, "epic": ep,
                     "weapon_ok": weap_ok})
        log("  [ILP] 复核：抗性全绿 %s ｜ 紫 %d 蓝 %d ｜ 双手 %s"
            % ("是" if ok_all else "否", lg, ep, "合法" if weap_ok else "非法"))
        if not (ok_all and weap_ok):
            log("  [ILP] ⚠ 解未通过复核，交回调用方回退")
            return None, info
    return sol, info


def lp_bound(O, candmap, min_leg=0, max_leg=None, min_epic=0, max_epic=None):
    """LP 松弛（去掉整数约束）的全局上界 —— 比 MIP 快两个数量级。

    用途：判断 MIP 解离最优还有多远。本问题实测（Sam lv71 / --extreme / 8982 变量）：
      LP 界 2808.19（0.03 s，只有 11 个分数分量、5 个槽位被拆开）
      MIP 最优 2746.70（9.0 s）→ 整数间隙 2.2%

    ★ 别指望「LP 舍入 + 枚举分数槽位」能秒解：实测把 5 个分数槽位各取 top-3 枚举
      243 个组合，**全部不可行**——抗性约束卡在边界，任何整数舍入都会让某项跌破需求。
      这正是 B&B 必须跑的原因，也是它慢的原因。
    """
    import numpy as np
    import highspy

    M = Model(O, candmap, min_leg=min_leg, max_leg=max_leg,
              min_epic=min_epic, max_epic=max_epic)
    lp = M.highs_lp(highspy.HighsVarType.kContinuous)
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("presolve", "on")
    h.passModel(lp)
    t0 = time.time()
    h.run()
    dt = time.time() - t0
    x = np.asarray(h.getSolution().col_value, float)
    frac = int(np.count_nonzero((x > 1e-6) & (x < 1 - 1e-6)))
    split = sum(1 for s in M.slots
                if np.count_nonzero(x[M.off[s]: M.off[s] + len(candmap[s])] > 1e-6) > 1)
    return {"upper_bound": -h.getObjectiveValue(), "seconds": dt,
            "status": h.modelStatusToString(h.getModelStatus()),
            "frac_vars": frac, "split_slots": split, "nvar": M.n}


# ==================================================================== 自检
def solve_pulp(O, candmap, time_limit=60.0, min_leg=0, max_leg=14,
               min_epic=0, max_epic=14, msg=0, log=print):
    """同模型的 PuLP + CBC 版本（对照用，验证两个求解器给出同一个最优值）"""
    import pulp

    slots = [s for s in O.SLOTS if s in candmap]
    NT, NF = O.NT, O.NF
    dmg_w = [O.W_DMG.get(k, 0.0) for k in O.FKEYS]

    prob = pulp.LpProblem("fullres_maxdmg", pulp.LpMaximize)
    X = {s: [pulp.LpVariable("x_%s_%d" % (s, i), cat="Binary")
             for i in range(len(candmap[s]))] for s in slots}
    for s in slots:
        prob += pulp.lpSum(X[s]) == 1, "pick_%s" % s
    for ti, t in enumerate(O.TYPES):
        nd = O.NEED[t]
        if nd <= 0:
            continue
        prob += pulp.lpSum([candmap[s][i][1][ti] * X[s][i]
                            for s in slots for i in range(len(candmap[s]))]) >= nd - 1e-9, "res_%d" % ti
    lt = [candmap[s][i][6] * X[s][i] for s in slots for i in range(len(candmap[s]))]
    et = [candmap[s][i][5] * X[s][i] for s in slots for i in range(len(candmap[s]))]
    if min_leg > 0:
        prob += pulp.lpSum(lt) >= min_leg
    if max_leg < len(slots):
        prob += pulp.lpSum(lt) <= max_leg
    if min_epic > 0:
        prob += pulp.lpSum(et) >= min_epic
    if max_epic < len(slots):
        prob += pulp.lpSum(et) <= max_epic
    if "主手" in slots and "副手" in slots:
        e = X["副手"][0]
        for i, c in enumerate(candmap["主手"]):
            if _is_2h(O, c[0]):
                prob += X["主手"][i] - e <= 0
    obj = []
    for s in slots:
        for i, c in enumerate(candmap[s]):
            w = sum(c[2][k] * dmg_w[k] for k in range(NF)) + O.SKILL_K * c[3] - O.LEG_PEN * c[6]
            obj.append(w * X[s][i])
    prob += pulp.lpSum(obj)

    t0 = time.time()
    st = prob.solve(pulp.PULP_CBC_CMD(msg=msg, timeLimit=max(1, int(time_limit))))
    dt = time.time() - t0
    sol = {}
    for s in slots:
        for i, v in enumerate(X[s]):
            if v.value() is not None and v.value() > 0.5:
                sol[s] = candmap[s][i][0]
                break
    if "副手" in sol and sol["副手"] == _EMPTY_TRI[0]:
        sol["副手"] = _EMPTY_TRI
    log("  [CBC] %s ｜ 目标 %.2f ｜ %.2f s" % (pulp.LpStatus[st], pulp.value(prob.objective), dt))
    return sol, {"status": pulp.LpStatus[st], "objective": pulp.value(prob.objective),
                 "seconds": dt, "nvar": sum(len(candmap[s]) for s in slots)}


def proxy_of(O, sol):
    """按 ILP 的同一口径独立算一遍目标值（复核用）"""
    NF = O.NF
    dmg_w = [O.W_DMG.get(k, 0.0) for k in O.FKEYS]
    tot = 0.0
    for s in O.SLOTS:
        if s not in sol:
            continue
        tri = sol[s]
        b, c, a, p, sf = tri
        rt = [0.0] * O.NT
        ot = [0.0] * O.NF
        sk = 0.0
        # ★ 必须含 p / sf：`slot_cands` 会把该槽位的最优词缀折进 (rvec, ovec, skill)，
        #   只算底材+组件会让复核值系统性偏低（旧版漏了 → 与求解器目标差 ~10）。
        for gid in (b, c, a, p, sf):
            if not gid:
                continue
            r1, o1, s1 = O.contrib(gid)
            for i in range(O.NT):
                rt[i] += r1[i]
            for i in range(O.NF):
                ot[i] += o1[i]
            sk += s1
        lg = 1 if O._rar(b) == "Legendary" else 0
        tot += sum(ot[k] * dmg_w[k] for k in range(NF)) + O.SKILL_K * sk - O.LEG_PEN * lg
    return tot


def verify(O, sol):
    """独立复核：抗性逐维、稀有度、双手一致性"""
    r, _o = O.ev({s: v for s, v in sol.items()})
    rows = []
    ok_all = True
    for t in O.TYPES:
        nd = O.NEED[t]
        v = r.get(t, 0.0)
        ok = v >= nd - 1e-6
        ok_all &= ok
        rows.append((t, v, nd, ok))
    lg = sum(1 for s in sol if sol[s][0] and O._rar(sol[s][0]) == "Legendary")
    ep = sum(1 for s in sol if sol[s][0] and O._rar(sol[s][0]) == "Epic")
    m = (sol.get("主手") or (None,))[0]
    o_ = (sol.get("副手") or (None,))[0]
    two_h = bool(m) and _is_2h(O, sol.get("主手"))
    weapon_ok = not (two_h and o_)
    return rows, ok_all, lg, ep, weapon_ok


def dump(O, sol, slots):
    print()
    print("  ── 选中装备 ──")
    for s in slots:
        b, c, ag, p, sf = sol[s]
        print("    %-6s %s" % (s, O.name_of(b) if b else "（空）"))
        if c:
            print("           └ 组件 %s" % O.name_of(c))
        if ag:
            print("           └ 附魔 %s" % O.name_of(ag))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("char", nargs="?", default="Sam")
    ap.add_argument("--with-weapon", action="store_true")
    ap.add_argument("--time-limit", type=float, default=60.0)
    ap.add_argument("--gap", type=float, default=1e-2,
                    help="MIP 相对间隙。1e-2 与 1e-4 实测**同解**但快 1.7 倍（默认）")
    ap.add_argument("--extreme", action="store_true", help="候选池开到底（同 gd auto --extreme）")
    ap.add_argument("--bench", action="store_true", help="同时跑束搜索做对照（慢）")
    ap.add_argument("--bench-beam", type=int, default=12000)
    ap.add_argument("--bench-jobs", type=int, default=16)
    ap.add_argument("--cbc", action="store_true", help="再用 CBC 解一遍，验证最优值一致")
    ap.add_argument("--no-dom", action="store_true",
                    help="关闭 (抗性,目标) 支配剪枝（对照用；默认开启）")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if a.no_dom:
        os.environ["GD_ILP_DOM"] = "0"

    print("=" * 72)
    print("ILP 满抗搜索 ｜ 角色 %s ｜ %s" % (a.char, "含武器" if a.with_weapon else "不含武器"))
    print("=" * 72)

    t_all = time.time()
    char, level, arch, slots = prepare_env(a.char, a.with_weapon, a.extreme)
    print("  角色 %s lv%d ｜ 形态 %s ｜ 槽位 %d" % (char, level, arch, len(slots)))

    O = import_opt()                                    # ★ 必须在 prepare_env 之后

    t0 = time.time()
    cm = build_candmap(O, slots, allow_empty_offhand=True)
    print("  候选 " + " ｜ ".join("%s %d" % (s, len(cm[s])) for s in slots))
    print("  建池 %.1f s" % (time.time() - t0))
    cm = dom_reduce(O, cm)          # ★ 精确支配剪枝（--no-dom 可关）

    sol, info = solve(O, cm, time_limit=a.time_limit, gap=a.gap)
    rows, ok_all, lg, ep, weapon_ok = verify(O, sol)

    print()
    print("  ── 抗性复核 ──")
    for t, v, nd, ok in rows:
        print("    %-4s %6.1f / %3d  %s" % (t, v, nd, "✓" if ok else "✗ 不足"))
    print("  稀有度：紫 %d ｜ 蓝 %d" % (lg, ep))
    print("  双手一致性：%s" % ("✓" if weapon_ok else "✗ 非法"))
    print("  代理复核 %.2f（与求解器目标应一致）" % proxy_of(O, sol))
    print("  综合：%s" % ("✓ 全部通过" if (ok_all and weapon_ok) else "✗ 有问题"))
    dump(O, sol, slots)

    if a.cbc:
        print()
        print("  ── CBC 交叉验证（同一模型，另一种求解器）──")
        _, i2 = solve_pulp(O, cm, time_limit=a.time_limit)
        d = abs(i2["objective"] - info["objective"])
        print("    目标差 %.4f → %s" % (d, "✓ 一致（互为佐证）" if d < 0.05 else "⚠ 不一致，需排查"))

    if a.bench:
        print()
        print("  ── 束搜索对照（原方案，计时）──")
        O.GOAL = "worst"
        O.MIN_LEG, O.MAX_LEG = 0, 14
        O.MIN_EPIC, O.MAX_EPIC = 0, 14
        O.COVER_MIN = 0.0
        os.environ["GD_FULL"] = "1"
        t2 = time.time()
        old, cnt = O.run_search(list(O.SLOTS), a.bench_beam, verbose=False,
                                restart=6, jobs=a.bench_jobs)
        dt_old = time.time() - t2
        old = {s: tuple(v) for s, v in old.items()}
        _r2, ok2, lg2, ep2, wok2 = verify(O, old)
        print("    束搜索(宽%d×6重启) %6.1f s ｜ 抗性全绿 %s ｜ 紫%d 蓝%d ｜ 代理 %.2f"
              % (a.bench_beam, dt_old, "是" if ok2 else "否", lg2, ep2, proxy_of(O, old)))
        print("    ILP                 %6.1f s ｜ 抗性全绿 %s ｜ 紫%d 蓝%d ｜ 代理 %.2f"
              % (info["seconds"], "是" if ok_all else "否", lg, ep, info["objective"]))
        gain = info["objective"] - proxy_of(O, old)
        print("    → 代理增益 %+.2f（ILP 是全局最优，束搜索是启发式）" % gain)
        if dt_old > 0:
            print("    → 加速 %.1f×" % (dt_old / max(1e-9, info["seconds"])))

    print()
    print("  总耗时 %.1f s" % (time.time() - t_all))

    if a.out:
        json.dump({k: list(v) for k, v in sol.items()},
                  open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("  已写出 %s" % a.out)

    return 0 if (ok_all and weapon_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
