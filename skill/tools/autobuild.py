#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""autobuild.py —— 一条命令跑完「读存档 → 满抗搜索 → 真实 DPS 微调 → 报告（可选落档）」

为什么要有它
------------
以前是「手动一条条跑」：save_plan → gd.opt → plan_dps → tune_dps → planreport → gd build …
串起来半小时，还要人盯着中间结果微调。这个脚本把整条链**进程内**跑完，
默认参数就是最优口味，不需要人干预。

用法
----
    python tools/autobuild.py Sam                      # 满抗 + 追高伤害（不含武器），只出方案
    python tools/autobuild.py Sam --apply              # 出方案并落档（自动备份 + 复检）
    python tools/autobuild.py Sam --with-weapon        # 连武器一起优化
    python tools/autobuild.py Sam --goal fullres       # 只求满抗（不追伤害）
    python tools/autobuild.py Sam --goal dmg           # 只要伤害（不保抗性）
    python tools/autobuild.py --list                   # 列出角色 + 自动判定的形态

产出
----
    data/plans/<Char>_current.json   现状基线
    data/plans/<Char>_auto.json      最优方案
    data/plans/<Char>_auto.md        中文报告（装备/抗性/输出/可穿性）
    （--apply 时还会生成 <Char>_build.json 并写入存档）

设计要点
--------
· 全程**一个进程**：gd.opt 只 import 一次；候选池 / 桥表 / JSON 缓存都复用。
· **现状已满抗就不跑束搜索**（那是最慢的一步，~40 s）——直接做局部搜索。
· 局部搜索目标函数是**真实 DPS 模型**，不是 gd.opt 的线性「伤害代理」
  （代理对本流派会选错，见 docs/status.md §9）。
· 每个候选的 DPS 评估都 memo，且单次评估已从 0.33 s 优化到 ~0.12 s。
"""
import argparse
import atexit
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from gd import paths, DB                              # noqa: E402
from gd.save import core                              # noqa: E402

PY = sys.executable
GOAL_ZH = {"super": "满抗 + 追高伤害", "fullres": "只求满抗", "dmg": "只要伤害"}


# ==================================================================== 存档侧
def _char_key(name):
    chars = paths.characters()
    for k in chars:
        if k == name or k.lstrip("_").lower() == name.lstrip("_").lower():
            return k
    raise SystemExit("✗ 角色 %r 不存在；可选：%s" % (name, ", ".join(chars)))


def read_meta(char):
    """读存档 → (等级, 职业编号, 加点)。"""
    doc = core.parse(str(paths.characters()[_char_key(char)] / "player.gdc"))
    b8 = doc["block_map"].get(8) or {}
    skills = {s["skill"]: int(s["level"]) for s in b8.get("skills", []) if s["level"] > 0}
    classids = set()
    for rec in skills:
        base = os.path.basename(rec).replace(".dbr", "")
        if base.startswith("_classtraining_class"):
            try:
                classids.add(int(base.replace("_classtraining_class", "")))
            except ValueError:
                pass
    return doc.get("level") or 1, sorted(classids), skills


def pick_archetype(classids, skills, archs):
    """按「职业组合 + 变身形态」自动选形态键（archetypes.json 的键）。"""
    form = None
    for rec in skills:
        b = os.path.basename(rec).replace(".dbr", "")
        if b == "werewolf1":
            form = "wolf"
        elif b == "wereraven1":
            form = "raven"
    cs = set(classids)

    def ids(kv):
        return {m[0] for m in (kv[1].get("masteries") or [])}

    exact = [kv for kv in archs.items() if ids(kv) == cs]
    if exact:
        pool = [kv for kv in exact if not kv[0].endswith("_fast")] or exact
    else:
        ranked = sorted(archs.items(), key=lambda kv: -len(ids(kv) & cs))
        pool = [kv for kv in ranked[:3] if not kv[0].endswith("_fast")] or ranked[:1]
    if form:
        fp = [kv for kv in pool if form in kv[0]]
        if fp:
            pool = fp
    return pool[0][0]


def _phys_cores():
    """物理核数 —— 计算密集任务的并行度上限（SMT 线程会互抢执行单元）。

    psutil 优先；没装则退化成「逻辑核 // 2」（对 9950X3D 这类 16C/32T 正好对）。
    """
    try:
        import psutil
        n = psutil.cpu_count(logical=False)
        if n:
            return int(n)
    except Exception:
        pass
    return max(1, (os.cpu_count() or 4) // 2)


def _logical_cores():
    return int(os.cpu_count() or 4)


def _mem_available_gb():
    """可用物理内存（GB）；拿不到就返回 0.0（调用方按「不限制」处理）。"""
    try:
        import psutil
        return psutil.virtual_memory().available / 1073741824.0
    except Exception:
        return 0.0


# 单个链进程的内存占用。
# ★ 2026-09-20 实测修正：24 个 python（1 父 + 20 链 + 3 杂）合计 RSS **4.5 GB**
#   ⇒ **每链 ≈ 0.19 GB**。旧值 1.4 GB 是把「搜索后期的候选池峰值」当成了常驻，
#   高了 7 倍多 ⇒ 40 GB 可用内存只肯铺 22 条链，32 逻辑核里白白空着 10 个。
#   取 0.5 GB（≈2.6 倍余量）兼顾候选池膨胀，同时**再按逻辑核封一道**。
_PER_WORKER_GB = 0.5
# 留给系统的余量：低于这个数就不再铺链，避免换页把整机拖垮
_MEM_RESERVE_GB = 4.0


def _cap_chains_by_mem(n, log=None):
    """按可用内存给并行链数封顶（每个 worker 独立加载一份数据）。

    ★ 两道闸：① 内存（防换页）② **逻辑核**（超过核数的 worker 只会互相抢 CPU）。
    """
    avail = _mem_available_gb()
    cap = None
    if avail > 0:
        cap = max(1, int((avail - _MEM_RESERVE_GB) / _PER_WORKER_GB))
    # 多于逻辑核的 worker 拿不到核，只会拉长墙钟
    cores = _logical_cores()
    cap = cores if cap is None else min(cap, cores)
    if n > cap:
        if log:
            log("  ⚠ 并行链封顶 %d 条（请求 %d）｜ 可用内存 %.1f GB / 每链 ≈%.2f GB"
                " / 逻辑核 %d" % (cap, n, avail, _PER_WORKER_GB, cores))
        return cap
    return n


# ---------------------------------------------------------------- 并行度分配
# ★★ 2026-09-22 性能重构的核心：**把「链级并行」与「链内并行」统一成一个分配问题**。
#
#   旧设计把两者设成**互斥**（`--procs` 存在就把 `--chains` 压成 1），理由是
#   「N×procs 会超过物理核」。代价是 `--extreme` 在 32 核机器上跑出
#   **10.02 CPU-小时 / 单链 18.8 分钟**：32 条链**各自内部完全串行**，
#   实测并行效率只有 1/32。
#
#   新设计：**总进程数 = 物理核**，把这份预算在「链数」与「每链进程数」之间分配。
#   两种极端等价于旧行为，但默认落在中间：
#     · `--chains 1 --procs 16` ⇒ 单链、最省墙钟（但只有 1 个盆地）
#     · `--chains 16 --procs 1` ⇒ 16 个盆地、最费墙钟（旧默认）
#     · 默认（都不传）：`procs = min(物理核, _PAR_AUTO_PROCS)`，`chains = 物理核 // procs`
#   ⚠ 为什么 `procs` 默认封在 **4**：实测（`--extreme` 全链路，Sam，16 物理核）
#     · `--chains 2 --procs 8` ⇒ 墙钟 **72 s** ｜ 结果 **174,893（+6.7%）**
#     · `--chains 4 --procs 4` ⇒ 墙钟 **113 s** ｜ 结果 **175,726（+7.2%）**  ← 默认取这档
#     ·（旧架构 32 链 × 1 进程 ⇒ 墙钟 **1128 s** ｜ 176,235（+7.5%））
#     即：把核从「每链 8 个」挪到「多两条链」，多花 41 s 换回 **+0.5% DPS**，
#     而相对旧架构仍是 **10× 提速**。加速比本身也是递减的
#     （2 进程 1.9×｜4 进程 3.3×｜8 进程 6.8×｜16 进程 10.2×）⇒ 堆进程数不划算。
_PAR_AUTO_PROCS = 4


def _alloc_parallel(phys, chains=0, procs=0, want_chains_explicit=False):
    """把 `phys` 个物理核分配给 `chains` 条链 × 每条链 `procs` 个邻域进程。

    返回 `(chains, procs, note)`。保证 `chains × procs ≤ phys`（下界各 1）。
    """
    phys = max(1, int(phys))
    chains = max(0, int(chains or 0))
    procs = max(0, int(procs or 0))
    note = ""
    if procs and chains:
        if chains * procs > phys:
            new_p = max(1, phys // chains)
            note = ("⚠ 链 × 进程 = %d×%d = %d > 物理核 %d ⇒ 每链进程数压到 %d"
                    % (chains, procs, chains * procs, phys, new_p))
            procs = new_p
    elif procs:                                   # 只给 procs ⇒ 链数按物理核分
        chains = max(1, phys // procs)
        note = "由 --procs %d 推出 %d 条链（物理核 %d）" % (procs, chains, phys)
    elif chains:                                  # 只给 chains ⇒ 每链分到剩余核
        procs = max(1, phys // chains) if chains < phys else 1
        if procs > 1:
            note = "由 --chains %d 自动给每链 %d 个邻域进程（物理核 %d）" % (
                chains, procs, phys)
    else:                                         # 都不给 ⇒ 默认折中
        procs = min(phys, _PAR_AUTO_PROCS)
        chains = max(1, phys // procs)
        note = ("自动分配：%d 条链 × 每链 %d 进程（物理核 %d）" % (chains, procs, phys))
    if chains * procs > phys:                     # 最后兜一道
        procs = max(1, phys // max(1, chains))
    return chains, procs, note


def _pid_alive(pid):
    """进程是否还活着（跨平台；拿不到信息时保守返回 False）。"""
    if not pid or int(pid) <= 0:
        return False
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:
        pass
    if os.name == 'nt':
        try:
            import ctypes
            _P = ctypes.windll.kernel32.OpenProcess(0x00100000, False, int(pid))
            if not _P:
                return False
            _code = ctypes.c_ulong(0)
            ctypes.windll.kernel32.GetExitCodeProcess(_P, ctypes.byref(_code))
            ctypes.windll.kernel32.CloseHandle(_P)
            return _code.value == 259          # STILL_ACTIVE
        except Exception:
            return False
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _lock_dir():
    _d = paths.DATA_DIR / 'scratch' / '.autobuild.locks'
    try:
        _d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return _d


def _lock_key(char, out):
    """锁键 = (角色, **绝对化后的**输出文件)。自检与本模块共用，避免两处各写一份。"""
    return '%s|%s' % (char, os.path.abspath(str(out)))


def _lock_path(key):
    """锁文件按 **(角色, 输出文件)** 取键 —— 不是全局一把锁。

    ★ 2026-09-20 改造：原来是**全局单实例锁**（一台机器只许一个 autobuild）。
    那条规则直接堵死了「多形态并发普查」（`tools/sweep_arch.py`）——
    实测 6 形态并发时第 6 个（raven_nightblade）被拒（rc=4），前 5 个已跑完。
    重新审视原注释列的三条危害：①③ 只发生在**同一输出文件**上；
    ②（进程数爆炸）由调用方的**总预算**负责，不该由一刀切的锁来管。
    ⇒ 改成按输出文件互斥：同输出仍拒绝第二个，不同输出**允许并发**。
    锁目录里可能堆多个文件，每个都自带 PID，死进程的锁会被自动回收。
    """
    h = hashlib.md5(str(key).encode('utf-8')).hexdigest()[:12]
    return _lock_dir() / (h + '.lock')


def acquire_single_instance(char, out, force=False):
    """**按输出文件的互斥锁**：同一个 `--out` 只允许一个 autobuild 写。

    为什么需要（2026-09-20 实测踩坑）：当时有**两个** autobuild 同时在跑
    （旧的没退、新的又起），后果是

      ① 两个进程往**同一个** `--out` JSON 写 ⇒ 后写的覆盖先写的，谁的结果都不可信；
      ② 各自 spawn 20~30 个 worker ⇒ 一次 **53 个 python.exe**，内存与句柄被吃光；
      ③ 两条日志交错，报出来的 DPS 分不清出自哪一条链。

    其中 ①③ 是**同输出**才能触发的 ⇒ 按输出文件加锁即可根除；
    ② 属于**并发度预算**问题，交给调用方（`sweep_arch.py --budget`）显式控制，
    不该用「全局只许一个」来一刀切（那会连「不同形态各写各的文件」也禁掉）。

    规则：锁文件里存 PID。**同输出的**持有者还活着就拒绝启动；持有者已死
    （上次被 kill / 崩溃）则**自动回收**，无需人工清锁。
    """
    lp = _lock_path(_lock_key(char, out))
    try:
        info = json.loads(lp.read_text(encoding='utf-8'))
    except Exception:
        info = None

    if info and not force:
        old = info.get('pid')
        if _pid_alive(old) and int(old) != os.getpid():
            print('✗ 已有另一个 autobuild 在写同一个输出文件，拒绝启动。')
            print('    PID %s ｜ 角色 %s ｜ 输出 %s ｜ 启动 %s'
                  % (old, info.get('char'), info.get('out'), info.get('t0')))
            print('    要等它跑完；确认它已卡死就 `taskkill /F /T /PID %s`，'
                  '或改用 `--force` 抢占（**会与它抢同一个输出文件**）。' % old)
            print('    ℹ 想跑**不同**形态/输出请直接换 `--out`：'
                  '不同输出之间不互斥（见 `tools/sweep_arch.py`）。')
            return False
        if old and not _pid_alive(old):
            print('  ℹ 发现上次残留的锁（PID %s 已不在）→ 自动回收' % old)

    rec = {'pid': os.getpid(), 'char': char, 'out': str(out),
           't0': time.strftime('%Y-%m-%d %H:%M:%S'), 'argv': list(sys.argv)}
    try:
        lp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding='utf-8')
    except Exception as e:                                        # noqa: BLE001
        print('  ⚠ 单实例锁写不进去（%s）：本次不设防' % e)
        return True

    def _release():
        try:
            cur = json.loads(lp.read_text(encoding='utf-8'))
            if int(cur.get('pid') or 0) == os.getpid():
                lp.unlink()
        except Exception:
            pass

    atexit.register(_release)
    return True


def current_plan(char, with_weapon):
    import save_plan
    return save_plan.build(char, with_weapon)


# ==================================================================== 搜索
def run_cap_search(O, beam, restart, jobs, log, solver="auto", gap=1e-3,
                   ilp_time_limit=60.0, char=""):
    """满抗搜索。默认走 **ILP**（全局最优，实测 5.9 s vs 束搜索 74.4 s），失败回退束搜索。

    为什么换：束搜索不但在 6 次重启用线程池时被 GIL 锁成串行（74 s ÷ 6 = 12 s/次，
    正是单次耗时），更要命的是 `GD_FULL=1` 下它的目标退化成「技能分 − 紫装惩罚」——
    `worst≡1`、`cover≡1070` 恒定，**伤害项整个消失**，交出来的「满抗起点」在伤害维度
    上是随机的。ILP 直接把「9 条抗性硬约束下最大化伤害代理」建成多选择背包整数规划，
    5.9 s 拿全局最优。详见 `tools/ilp_res.py:Model` 的类文档。

    ★ HiGHS 只支持单线程（threads>1 / parallel='on' → Not Set），所以这里的加速
      来自「算法换了」而不是「求解器并行」；多核交给后面的 16 条独立搜索链。
    """
    t0 = time.time()
    _cm = {s: O.prune_cands(O.slot_cands(s)) for s in sorted(set(O.SLOTS))}
    log("  [满抗搜索] 候选 " + " ｜ ".join("%s %d" % (s, len(_cm[s])) for s in O.SLOTS))
    log("  [满抗搜索] 建池 %.1f s ｜ DEDUP=%s ｜ CHUNK=%s ｜ 求解器 %s"
        % (time.time() - t0, os.environ.get("GD_DEDUP", "-"),
           os.environ.get("GD_CHUNK", "-"), solver))

    if solver in ("auto", "ilp"):
        try:
            import ilp_res as IR
            cm = IR.build_candmap(O)          # 内部补「副手 ∅」候选
            sol, info = IR.cap_search(O, cm, gap=gap, time_limit=ilp_time_limit,
                                      log=log)
            if sol is not None:
                log("  [满抗搜索] ILP 全局最优 ｜ 建模+求解+复核 %.1f s"
                    "（束搜索约 74 s）｜ 紫%d 蓝%d"
                    % (time.time() - t0, info.get("legendary", -1),
                       info.get("epic", -1)))
                return {s: tuple(v) for s, v in sol.items()}
            log("  [满抗搜索] ILP 未给出可行解 → 回退束搜索")
        except Exception as e:
            log("  [满抗搜索] ILP 异常（%s: %s）→ 回退束搜索" % (type(e).__name__, e))
        if solver == "ilp":
            raise SystemExit("✗ 指定 --cap-solver ilp，但 ILP 没能给出解（见上方日志）")

    os.environ["GD_FULL"] = "1"
    os.environ["GD_COVER_MIN"] = "0"
    O.GOAL = "worst"
    O.MIN_LEG, O.MAX_LEG = 0, 14
    O.MIN_EPIC, O.MAX_EPIC = 0, 14
    O.COVER_MIN = 0.0
    t1 = time.time()
    try:
        sol, cnt = O.run_search(list(O.SLOTS), beam, verbose=False, restart=restart,
                                jobs=jobs, csmap=_cm)
    except Exception:
        # 诊断：束搜索崩了，先把每槽候选数打出来（空槽 => SUFM 归约零尺寸）
        _diag = {s: O.prune_cands(O.slot_cands(s)) for s in sorted(set(O.SLOTS))}
        for _s in O.SLOTS:
            log("  [诊断] %-6s 候选 %d" % (_s, len(_diag[_s])))
        raise
    log("  [满抗搜索] 束搜索 束宽 %d × 重启 %d ｜ %.0f s ｜ 绿%d 蓝%d 紫%d"
        % (beam, restart, time.time() - t1, cnt[0], cnt[1], cnt[2]))
    os.environ["GD_FULL"] = "0"
    return {s: tuple(v) for s, v in sol.items()}


def _weapon_ok(O, sol):
    """武器槽一致性：**双手武器时副手必须为空**；副手有货时主手不能是双手。

    为什么要有这道闸门：`gd.opt.slot_ok` 只按位图目录判「这件能不能放这个槽」，
    **不做跨槽一致性**。放开武器槽后它可能给出「双手剑 + 副手匕首」这种游戏里无效的
    组合（写进去游戏会忽略其中一件，白搭）。这里在交付前兜一道。
    """
    if "主手" not in O.SLOTS:
        return True
    m = (sol.get("主手") or [None])[0]
    o = (sol.get("副手") or [None])[0]
    bm = (((O._IT.get(m) or {}).get("n") or "") if m else "").lower()
    two_h = ("2h" in bm) or ("twohand" in bm)
    if two_h and o:
        return False
    if o and not m:
        return False
    return True


def _RR_DIM_ON(O):
    """`gd/opt` 里有没有减抗维度（`GD_RR=0` / `--no-rr` 时会没有）"""
    return "rr_add" in (getattr(O, "FKEYS", None) or ())


def make_real_dps(O, char, arch):
    import plan_dps as PD
    import objfunc as _OB
    # ★★ 搜索目标可换（2026-09-20，为「主穿刺」形态加的）：
    #   `--goal super` 下 LNS/局部搜索原本一律用 `dps_of()["dps"]`（**合计**含减抗）。
    #   于是形态声明的 `damage_weights`（只作用于满抗求解阶段）与搜索目标**互相矛盾** ——
    #   实测主穿刺形态（pierce 3.0 / bleed 0.05）装备搜索仍然是 0 改进，
    #   因为搜索根本没看穿刺。用 `GD_OBJ=pierce` 把目标换成**穿刺桶**才真正生效。
    _obj = _OB.mode_default()
    memo = {}

    def f(sol):
        key = tuple(sol[x] for x in O.SLOTS)
        if key not in memo:
            r = PD.dps_of(char, {k: list(v) for k, v in sol.items()}, arch)
            # ★ 一律走 `objfunc.score`：`total` 时它的返回值与 `r["dps"]` **逐位相同**
            #   （都是 `plan_dps` 的 `dps` 字段），但**多了一层防御乘子**
            #   （`GD_DEF_WEIGHT`，未设 ⇒ 原样返回）。之前这里写 `r["dps"] if total else …`，
            #   会让「合计口径 + 防御权重」静默失效（防御只对非 total 的桶生效）。
            memo[key] = _OB.score(r, _obj)
        return memo[key]
    # ★ 把 memo 挂到函数上：`lns_search` 的**并行**路径要复用它，否则会把
    #   「本轮邻域里已经算过的解」再送去 worker 重算一遍 —— 实测 600 轮里
    #   30,068 次真实评估被重复成了 60,478 次（浪费 ~40% 墙钟）。
    f.memo = memo
    return f, memo


# ============================================================ 搜索链（可单跑 / 可并行）
def run_chain(O, real_dps, base, *, seed=20260919, perturb=0, passes=2, topk=60,
              pair_k=2, max_per_slot=120, anneal_seconds=0.0, anneal_iters=0, goal="super",
              algo="lns", lns_k=(2, 3, 4), lns_topk=18, lns_cap=240, lns_hist=50,
              par=None, log=print, tag="", patience=0, proxy_tol=0.0, deadline=0.0):
    """跑**一条完整搜索链**：可选扰动 → 单槽/双槽贪心 → **LNS**（默认）/ 退火。返回 `(sol, dps)`。

    ★ 为什么要把它抽出来：并行加速的正解不是「把一次搜索拆开并行」（那受 Amdahl 限制），
      而是**同时跑 N 条独立链、取最优** —— 每条链自己走完「贪心 + 大邻域搜索」，
      进程间零共享，收益近线性。`perturb` 与 `seed` 用来让各链落到不同盆地。

    ★ `algo`（2026-09-20 新增）：
      · `"lns"`    —— **默认**。LNS + LAHC，每轮对随机 k 个槽做**精确枚举**。
                     实测 10 轮 / 1.2 s 从 94,474 → 107,376（+13.66%），
                     而退火 420 s 的 30 链最优只有 94,474。详见 `tune_dps.lns_search`。
      · `"anneal"` —— 旧的模拟退火（保留以支持历史对比 / 复现旧结果）。
      · `"none"`   —— 只跑单槽/双槽贪心。
    """
    import random
    from tune_dps import local_search, vecs_of

    sol = {k: tuple(v) for k, v in base.items()}
    # ---- 扰动：随机跳几个「仍满抗」的槽，把这条链推到不同起点
    if perturb > 0 and O.SLOTS:
        rnd = random.Random(seed)
        pools = {s: [c[0] for c in O.prune_cands(O.slot_cands(s))]
                 for s in O.SLOTS if s in sol}
        need = O.NEED
        cur_r = dict(O.ev(sol)[0])
        done = 0
        for _ in range(perturb * 12):
            if done >= perturb:
                break
            s = rnd.choice([x for x in O.SLOTS if x in sol])
            cands = pools.get(s) or []
            if not cands:
                continue
            c = rnd.choice(cands)
            if c == sol[s]:
                continue
            r1, _, _ = vecs_of(O, sol[s])
            r2, _, _ = vecs_of(O, c)
            nr = {t: cur_r.get(t, 0.0) - r1[i] + r2[i] for i, t in enumerate(O.TYPES)}
            if all(nr[t] >= need[t] - 1e-9 for t in O.TYPES):
                sol[s] = c
                cur_r = nr
                done += 1
        if done:
            log("  %s扰动 %d 槽（起点 DPS %s）" % (tag, done, format(real_dps(sol), ",")))

    sol, best = local_search(O, dict(sol), real_dps, passes=passes, topk=topk,
                             pair_k=pair_k, log=log, tag=tag,
                             require_full=(goal == "super"),
                             max_per_slot=max_per_slot, par=par)
    if algo == "lns":
        from tune_dps import lns_search
        # ★ `deadline > 0` ⇒ 用**剩余墙钟**当预算（`--time-budget`），并让轮数上限失效
        #   —— 否则「按轮数停」会把预算顶穿。
        _bud, _mi = anneal_seconds, anneal_iters
        if deadline and deadline > 0:
            import time as _t9
            _bud = max(0.5, float(deadline) - _t9.time())
            _mi = 0
            log("  %s剩余墙钟预算 %.0f s（--time-budget）" % (tag, _bud))
        sol, best = lns_search(O, sol, real_dps, budget_s=_bud,
                               max_iters=_mi, seed=seed, ks=tuple(lns_k),
                               topk=lns_topk, eval_cap=lns_cap, hist_len=lns_hist,
                               log=log, tag=tag, require_full=(goal == "super"),
                               par=par, patience=patience, proxy_tol=proxy_tol)
    elif anneal_seconds > 0 or anneal_iters > 0:
        from tune_dps import anneal_search
        sol, best = anneal_search(O, sol, real_dps, budget_s=anneal_seconds,
                                  log=log, tag=tag, seed=seed, max_iters=anneal_iters,
                                  moves=(1, 2, 3, 4) if anneal_seconds >= 180 else (1, 2, 3))
    return sol, best


# ---------------------------------------------------------------- 并行链 worker
_CH = {}


def _chain_init(char, arch, procs=0, seed_sol=None):
    """worker 初始化（每进程一次）：加载 gd.opt + 真实 DPS 评估器（+ 可选的链内并行器）。

    ★★ 2026-09-22：`procs > 0` 时**在 worker 内**再建一个邻域评估进程池 ——
      即「链级并行 × 链内并行」的嵌套。旧实现把两者设成**互斥**，理由是
      「N×procs 会超过物理核」；现在由 `_alloc_parallel()` 在**分配阶段**保证
      `chains × procs ≤ 物理核`，所以嵌套是安全的，而且墙钟大幅下降
      （每条链内部不再串行 ⇒ 同样轮数下墙钟 ÷ procs）。
    """
    os.environ.setdefault("GD_QUIET", "1")
    from tune_dps import load_opt
    from gd import DB as _DB
    with _silence():
        _CH["O"] = load_opt()
    _DB.load()
    _CH["dps"], _CH["memo"] = make_real_dps(_CH["O"], char, arch)
    _CH["par"] = None
    if int(procs or 0) > 0 and seed_sol:
        from tune_dps import ParEval
        _CH["par"] = ParEval(char, arch, procs=int(procs)).warm(seed_sol)


def _chain_run(payload):
    """worker：跑一条链 → `(sol, dps)`（可 pickle：槽位元组会转成 list）。"""
    seed, perturb, params = payload
    O = _CH["O"]
    base = {k: tuple(v) for k, v in params.pop("base").items()}
    sol, dps = run_chain(O, _CH["dps"], base, seed=seed, perturb=perturb,
                         log=lambda *a: None, par=_CH.get("par"), **params)
    return ({k: list(v) for k, v in sol.items()}, dps)


def _kill_tree(pid):
    """**连子孙一起杀**（Windows：`Process.terminate()` 只杀自己）。

    ★★ 为什么必须树杀：链进程底下还挂着一层**邻域评估池**（`ParEval`，4 个 worker）。
      只杀链进程 ⇒ 那 4 个 worker 立刻变成**孤儿**（Windows 上子进程不随父退出），
      实测「后台堆一堆 0% CPU 的 python」就是这么来的。
      用 `taskkill /T`（tree）或 psutil 的 `children(recursive=True)`。
    """
    pid = int(pid or 0)
    if pid <= 0:
        return False
    try:
        import psutil
        pr = psutil.Process(pid)
        for ch in pr.children(recursive=True):
            try:
                ch.kill()
            except Exception:                    # noqa: BLE001
                pass
        pr.kill()
        return True
    except Exception:                            # noqa: BLE001
        pass
    try:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                       capture_output=True)
        return True
    except Exception:                            # noqa: BLE001
        return False


def _chain_worker(payload, q, char, arch, procs, seed_sol):
    """链进程入口。★★ 必须是**非 daemon** 进程 —— 见 `_run_chains` 的说明。

    daemon 进程不允许有子进程；而本函数里 `_chain_init(procs=…)` 要再建一个
    邻域评估进程池 ⇒ 用 `mp.Pool` 承载链会直接抛
    `AssertionError: daemonic processes are not allowed to have children`。
    """
    try:
        _chain_init(char, arch, procs, seed_sol)
        q.put(("ok", _chain_run(payload)))
    except BaseException as e:                      # noqa: BLE001 兜住一切并回传
        import traceback
        q.put(("err", "%s: %s\n%s" % (type(e).__name__, e,
                                      traceback.format_exc()[-500:])))
    finally:
        # ★★ 显式关掉**链内**的邻域池 —— 不让它留到解释器退出才回收。
        #    （靠 `concurrent.futures` 的 atexit 兜底也行，但一旦本进程被**强杀**，
        #      atexit 不会跑 ⇒ 4 个 worker 全变孤儿。显式关闭把这个窗口关掉。）
        try:
            _p = _CH.get("par")
            if _p is not None:
                _p.close()
                _CH["par"] = None
        except Exception:                            # noqa: BLE001
            pass


def _run_chains(O, real_dps, base, args, an_s, n, char, arch, log, procs=0):
    """并行跑 N 条独立链（每条链内再用 `procs` 个进程做邻域并行），取最优。

    ★ 这就是「绕开 Amdahl」的姿势：**每条链都跑完整一轮**（贪心 + LNS），
      墙钟 ≈ 单链墙钟（链间并行），而**总搜索量 ×N**。
    ★ chain 0 用 `perturb=0` **完全复刻单链行为** ⇒ 并行结果**必定不劣于**单链。

    ★★ 2026-09-22：**链这一层不能用 `mp.Pool`**。`Pool` 的 worker 是 daemon 进程，
      而 Python 明令 daemon 进程**不许有子进程**；链内要建邻域进程池（`ParEval`）
      就会在 worker 里抛
      `AssertionError: daemonic processes are not allowed to have children`
      —— 实测表现是**整轮搜索静默挂死**（33 个进程全在 0% CPU 空转，5 分钟无输出）。
      改用显式的 `ctx.Process(daemon=False)` + `Queue` 收结果。
    """
    import multiprocessing as mp
    import queue as _q
    p_common = {"passes": args.passes, "topk": args.topk, "pair_k": args.pair_k,
                "max_per_slot": args.max_per_slot, "anneal_seconds": an_s,
                "anneal_iters": int(getattr(args, "anneal_iters", 0) or 0),
                "goal": args.goal,
                "algo": str(getattr(args, "algo", "lns") or "lns"),
                "lns_k": tuple(int(x) for x in
                               str(getattr(args, "lns_k", "2,3,4") or "2,3,4").split(",")),
                "lns_topk": int(getattr(args, "lns_topk", 18) or 18),
                "lns_cap": int(getattr(args, "lns_cap", 240) or 240),
                "lns_hist": int(getattr(args, "lns_hist", 50) or 50),
                "patience": int(getattr(args, "lns_patience", 0) or 0),
                "proxy_tol": float(getattr(args, "lns_proxy_tol", 0.0) or 0.0),
                "deadline": float(getattr(args, "_deadline", 0.0) or 0.0)}
    log("  ★ 并行 %d 条独立搜索链%s（每条跑完整一轮，取最优）"
        % (n, "，链内邻域并行 %d 进程" % procs if procs > 0 else ""))

    def _fallback(why):
        log("  ⚠ %s → 回退单链" % why)
        return run_chain(O, real_dps, base, passes=args.passes, topk=args.topk,
                         pair_k=args.pair_k, max_per_slot=args.max_per_slot,
                         anneal_seconds=an_s, goal=args.goal, log=log, tag="")

    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    seed_l = {kk: list(vv) for kk, vv in base.items()}
    payloads = [(20260919 + k * 100003, 0 if k == 0 else 1 + (k % 3),
                 dict(p_common, base={kk: list(vv) for kk, vv in base.items()}))
                for k in range(n)]
    workers = []
    try:
        for pl in payloads:
            pr = ctx.Process(target=_chain_worker,
                             args=(pl, q, char, arch, int(procs), seed_l))
            pr.daemon = False            # ★ 关键：允许它再建子进程（邻域池）
            pr.start()
            workers.append(pr)
    except Exception as e:                       # noqa: BLE001
        for pr in workers:                       # ★ 树杀：别留下邻域池孤儿
            _kill_tree(pr.pid)
        return _fallback("并行链启动失败（%s: %s）" % (type(e).__name__, str(e)[:80]))

    best_sol, best_dps, got, errs = None, -1.0, 0, []
    while got < len(payloads):
        try:
            kind, val = q.get(timeout=5.0)
        except _q.Empty:
            # 所有链进程都没了却还没收齐 ⇒ 有链是**静默死掉**的（不 put 任何东西）
            if all(not pr.is_alive() for pr in workers):
                break
            continue
        got += 1
        if kind == "err":
            errs.append(val)
            continue
        sol_l, dps = val
        log("    链完成 → DPS %s%s"
            % (format(dps, ","), "（当前最优）" if dps > best_dps else ""))
        if dps > best_dps:
            best_sol, best_dps = sol_l, dps
    for pr in workers:
        try:
            pr.join(timeout=10)
        except Exception:                        # noqa: BLE001
            pass
        if pr.is_alive():
            _kill_tree(pr.pid)                   # ★ 连它底下的邻域池一起收
    try:
        q.close()
    except Exception:                            # noqa: BLE001
        pass

    if errs:
        log("  ⚠ %d 条链报错（前一条）：%s" % (len(errs), errs[0].splitlines()[0][:160]))
    if best_sol is None:
        return _fallback("并行链无结果（%d/%d 条完成）" % (got, len(payloads)))
    sol = {k: tuple(v) for k, v in best_sol.items()}
    base_dps = real_dps({k: tuple(v) for k, v in base.items()})
    if best_dps < base_dps:                      # 兜底：绝不劣于起点
        log("  ⚠ 并行结果 %s 低于起点 %s → 采用起点"
            % (format(best_dps, ","), format(base_dps, ",")))
        return {k: tuple(v) for k, v in base.items()}, base_dps
    return sol, best_dps


# ==================================================================== 落档
def _run(cmd, log, quiet=True, env=None):
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    if not quiet and r.stdout:
        log(r.stdout)
    return r


def _report_env():
    """给**报告子进程**用的环境：强制把归因账本打开。

    ★ 搜索路径会设 `GD_ATTRIB=0`（每次评估都记一份报告用的账本纯属浪费），
      但 `gd.planreport` 生成的报告**需要** `slot_pct` / `slot_flat`（归因表）。
      子进程继承父进程环境 ⇒ 必须在这里显式改回来，否则报告里的归因整块空掉。
    """
    e = dict(os.environ)
    e['GD_ATTRIB'] = '1'
    return e


def apply_plan(char, plan_path, note, log):
    from gd.save import backup as B
    log("\n【落档】")
    # ① 写盘前安全检查
    r = _run([PY, "-m", "gd", "verify", "--preflight"], log)
    log("  ① preflight:", "OK" if r.returncode == 0 else "✗")
    # ② 显式备份（apply 自己也会备份一次）
    bk = B.do_backup("落档前：%s" % note)
    log("  ② 备份:", bk)
    # ③ 转换 + plan + check
    build_json = str(paths.DATA_DIR / "plans" / ("%s_build.json" % char.lstrip("_")))
    r = _run([PY, str(ROOT / "tools" / "plan_to_build.py"), char, plan_path,
              "--out", build_json, "--note", note], log)
    if r.returncode != 0:
        log(r.stdout or r.stderr)
        return False
    for sub in ("plan", "check"):
        r = _run([PY, "-m", "gd", "build", sub, build_json], log)
        if r.returncode != 0:
            log("  ✗ gd build %s 失败：\n%s" % (sub, (r.stdout or "")[-1500:]))
            return False
    log("  ③ plan / check 通过")
    # ④ 写入
    r = _run([PY, "-m", "gd", "build", "apply", build_json], log)
    tail = (r.stdout or "").strip().splitlines()[-3:]
    log("  ④ apply:", "OK" if r.returncode == 0 else "✗")
    for line in tail:
        log("     " + line)
    if r.returncode != 0:
        return False
    # ⑤ 落档后复检
    r = _run([PY, "-m", "gd", "verify", char], log)
    log("  ⑤ 体检:", "通过 ✓" if r.returncode == 0 else "✗")
    return True


# ==================================================================== 主流程
def run(args):
    t_all = time.time()
    log = print
    os.environ.setdefault("GD_QUIET", "1")     # 关掉 dps 的 [武器套] 刷屏
    # ★★ 2026-09-22 性能：搜索期间**关掉归因账本**（`gd/rotation.py` 里 `slot_pct` /
    #   `slot_flat`）。它是「伤害循环文档」用的报告产物，却在**每次**评估里都算一遍
    #   —— 搜索路径一次评估调一次 ⇒ 一份报告用的账本被算了 ~10 万遍。
    #   报告子进程由 `_report_env()` 显式改回 `GD_ATTRIB=1`（不能让它跟着继承）。
    os.environ.setdefault("GD_ATTRIB", "0")
    # ★ `--time-budget`：整轮搜索的**墙钟硬预算**。注意起算点在**这里**而不是
    #   搜索开始时 —— 建池（23 s）+ 满抗 ILP（32 s）都算在预算内，才是用户要的
    #   「N 分钟内出结果」。
    _tb = float(getattr(args, "time_budget", 0.0) or 0.0)
    args._deadline = (t_all + _tb) if _tb > 0 else 0.0
    char = _char_key(args.char)
    level, classids, skills = read_meta(char)
    archs = paths.load_json("archetypes.json") or {}
    arch = args.archetype or pick_archetype(classids, skills, archs)
    logs = []

    # ★ 槽位表：默认 12 件护甲/首饰；`--with-weapon` 再并入主手/副手。
    #   副手照常放开 —— 「双手 + 副手」的**非法组合在评估层就被丢弃**
    #   （见 `plan_dps.plan_to_override` 的双手规则），所以搜索自然会收敛到合法解。
    _cur0 = current_plan(char, args.with_weapon)
    slots = list(core.SLOTS)
    if args.with_weapon:
        slots += ["主手", "副手"]

    def _log(*a):
        msg = " ".join(str(x) for x in a)
        logs.append(msg)
        print(msg, flush=True)

    log("=" * 78)
    log("机器 ｜ %d 物理核 / %d 逻辑核 ｜ 可用内存 %.1f GB ｜ 并行 %d 链 × 每链 %d 进程"
        "（= %d 进程）｜ 满抗求解 %s(gap %g)"
        % (_phys_cores(), _logical_cores(), _mem_available_gb(),
           int(getattr(args, "chains", 1) or 1),
           int(getattr(args, "procs", 0) or 0),
           int(getattr(args, "chains", 1) or 1) * max(1, int(getattr(args, "procs", 0) or 0)),
           getattr(args, "cap_solver", "auto").upper(),
           float(getattr(args, "ilp_gap", 1e-3) or 1e-3)))
    if _tb > 0:
        log("★ 墙钟硬预算 `--time-budget` = %.0f s（含建池与满抗搜索；到点即收尾落盘）" % _tb)
    log("自动配装 ｜ 角色 %s lv%d ｜ 形态 %s ｜ 目标 %s ｜ 槽位 %d（%s）"
        % (char, level, arch, GOAL_ZH[args.goal], len(slots),
           "含武器" if args.with_weapon else "不含武器"))
    # ★ 搜索的**评分目标**（`GD_OBJ` / 形态主轴）：`total` = 合计（默认，旧行为）；
    #   `pierce` = 穿刺桶 ⇒ 「主穿刺」形态必须显式用，否则搜索会继续堆合计。
    try:
        import objfunc as _OB0
        if _OB0.mode_default() != 'total':
            log("★ 评分目标 = **%s**（`GD_OBJ=%s`）—— 搜索按该桶的含减抗 DPS 排序"
                % (_OB0.label(), _OB0.mode_default()))
    except Exception:                                            # noqa: BLE001
        pass
    # ★ 防御轴提示（2026-09-21）：`GD_DEF_WEIGHT` 生效时，**下面那两行
    #   「真实 DPS」打印的其实是「评分」**（= 伤害 × (1 + w × 防御分)），
    #   不是纯 DPS。不提示的话，日志会显得「DPS 凭空涨了 26%」（实测）。
    try:
        _dw = float(os.environ.get('GD_DEF_WEIGHT') or 0.0)
        if _dw:
            log("★ **防御轴已启用** `GD_DEF_WEIGHT=%.2f` ⇒ 下文「真实 DPS」列实为"
                "**评分** = 伤害 × (1 + %.2f × 防御分)，**不是纯 DPS**。" % (_dw, _dw))
    except Exception:                                            # noqa: BLE001
        pass
    log("=" * 78)
    # ★ 多核闲置提示：普通模式默认只开 1 条链（作者权衡是「进程启动开销 > 收益」），
    #   但带 --with-weapon 时单链一轮实测可达十几分钟，启动开销早已可忽略。
    #   静默单链跑过一次（32 核机器整机 CPU 仅 6%，被问「优化没用上」）⇒ 主动提示。
    if (int(getattr(args, "chains", 1) or 1) <= 1
            and int(getattr(args, "procs", 0) or 0) <= 1
            and _logical_cores() >= 8):
        log("  ⚠ 全串行运行：本机 %d 逻辑核，搜索只占用 1 核。"
            "要并行请加 `--procs N`（链内邻域并行，推荐）或 `--chains N`（多条独立链）"
            "—— 两者现在可以叠加，总进程数按物理核分配。" % _logical_cores())

    EMPTY = tuple([None] * 5)
    # ---- 现状（`_cur0` 已在上面取过，这里只负责落盘）
    cur = _cur0
    cur_path = str(paths.DATA_DIR / "plans" / ("%s_current.json" % char.lstrip("_")))
    json.dump({k: list(v) for k, v in cur.items()}, open(cur_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # ---- 环境（必须在 import gd.opt 之前设好：候选池在模块级构建）
    os.environ["GD_MAX_ILVL"] = str(level)
    os.environ["GD_ARCHETYPE"] = arch
    os.environ["GD_SLOTS"] = ",".join(slots)
    os.environ["GD_CUR_JSON"] = json.dumps(
        {k: [v[0]] for k, v in cur.items() if v and v[0]}, ensure_ascii=False)
    if not args.with_weapon:
        os.environ.pop("GD_NO_CUR", None)
    if args.no_faction:
        os.environ["GD_NO_FACTION"] = "1"
    else:
        os.environ.pop("GD_NO_FACTION", None)
    if getattr(args, "extreme", False):
        # 极限模式：把候选池开到底（这几项在 gd.opt 模块级生效，必须在 import 前设好）
        os.environ["GD_AUTO_TOPN"] = str(getattr(args, "pool_topn", 0) or 150)
        os.environ["GD_COMP_TOPN"] = str(getattr(args, "comp_topn", 0) or 24)
        os.environ["GD_AUG_TOPN"] = str(getattr(args, "aug_topn", 0) or 14)
        os.environ["GD_DEDUP"] = str(getattr(args, "dedup", 0) or 6)
        # ★ 关键：额外并入「按输出排序的前 N 件」——默认池只按抗性排，高伤害件进不来
        os.environ["GD_POOL_DMG_TOPN"] = str(getattr(args, "pool_dmg_topn", 0) or 400)
        os.environ.setdefault("GD_OVER_PEN", "0")
    # ★ 满抗 ILP 的 (抗性,目标) 支配剪枝（陷阱 #93）。默认开；`--no-dom` 只用于对照。
    #   读 env 发生在 `ilp_res.dom_reduce()` 调用时，所以这里设不算「import 前铁律」，
    #   但仍与其它开关一起放在环境段，便于一眼看全。
    if getattr(args, "no_dom", False):
        os.environ["GD_ILP_DOM"] = "0"
    if args.attr_gate:
        from gd import alloc
        try:
            ap = alloc.attribute_plan(char)
            if ap:
                os.environ["GD_ATTR_BUDGET"] = json.dumps(ap)
        except Exception:
            pass

    # ★ 减抗（RR）开关，**必须在 `load_opt()` 之前**设：
    #   `GD_RR` 决定 `gd/opt.FKEYS` 里有没有 rr_add / rr_pct（维度在模块级定死）。
    os.environ["GD_ENEMY_RES_PROFILE"] = args.enemy_res
    os.environ["GD_REAL_GOAL"] = args.real_goal
    if not getattr(args, "rr", True):
        os.environ["GD_RR"] = "0"
    if getattr(args, "rr_uptime", 0.0):
        os.environ["GD_RR_UPTIME"] = str(args.rr_uptime)

    from tune_dps import load_opt, local_search, vecs_of
    with _silence():
        O = load_opt()
    DB.load()
    real_dps, memo = make_real_dps(O, char, arch)
    from tune_dps import coverage, need_total, is_full
    _, cur_r = coverage(O, {k: tuple(v) for k, v in cur.items()})
    cur_sol = {s: (tuple(cur[s]) if cur.get(s) else EMPTY) for s in O.SLOTS}
    # ★★ 起点**必须过装备位合法性** —— 否则 LNS/退火「只在能提分时才改槽位」，
    #    非法项会被**原样留在结果里**。实测（2026-09-21）：起点带着
    #    「武器上的弹性铠甲片」（护甲组件）与「勋章上的炼狱粉尘」（戒指/项链附魔），
    #    重跑后这两处**仍然存在**，尽管 `comp_ok`/`aug_ok` 已经判它们非法、
    #    候选集 `choices()` 里也一个都没有 —— 因为搜索根本没去动那两个槽位。
    #    修法：非法项**置空**，让候选池去补（不猜、不替换成"看起来像"的东西）。
    from gd import opt as _OPT
    _illegal = []
    for _s in list(cur_sol):
        _v = list(cur_sol[_s])
        if not _v:
            continue
        _b = _v[0] if _v else None
        if len(_v) > 1 and _v[1] and not _OPT.comp_ok(_s, _v[1], _b):
            _illegal.append((_s, "组件", _v[1]))
            _v[1] = None
        if len(_v) > 2 and _v[2] and not _OPT.aug_ok(_s, _v[2], _b):
            _illegal.append((_s, "附魔", _v[2]))
            _v[2] = None
        cur_sol[_s] = tuple(_v)
    if _illegal:
        log("  ⚠ 起点含 %d 处**非法**装备位，已置空（候选池会补）：%s"
            % (len(_illegal),
               "、".join("%s%s %s" % (s, k, g) for s, k, g in _illegal)))
    cur_dps = real_dps(cur_sol)
    full = is_full(O, cur_r)
    log("现状：覆盖 %.0f/%d ｜ 真实 DPS %s"
        % (sum(min(cur_r.get(t, 0.0), O.NEED[t]) for t in O.TYPES), need_total(O),
           format(cur_dps, ",")))

    # ★★ 减抗维度的**边际权重标定**（迭代线性化的第 0 轮）——
    #    同一个「1 点」，+1% 伤害加成的边际是 `D/(100+pct)`，而 −1 敌方抗性是
    #    `D/(100−res)`，两者通常差十几到几十倍（Sam：+1500% / 剩 3% ⇒ 30×）。
    #    这个比值随「当前加成」和「所选敌方档位」变化，必须用**真实数值**标定，
    #    写死常量会让减抗件要么白给、要么吃掉全部预算。
    if _RR_DIM_ON(O):
        try:
            import plan_dps as _PD
            from gd import rr as _RRM
            _ev = _PD.dps_of(char, {k: list(v) for k, v in cur.items()}, arch)
            _rrb = _ev.get("rr") or {}
            _main = getattr(O, "RR_MAIN", "") or "pierce"
            _res = _RRM.res_eff(_main, _rrb, _RRM.profile_of()["base"])
            _pctm = _ev.get("pct") or {}
            _pct = _pctm.get(_main)
            if _pct is None:
                _pct = max(_pctm.values()) if _pctm else 0.0
            _info = O.recalibrate_rr(pct_main=_pct, res_main=_res, log=_log)
            log("  [减抗] 现状减抗：叠加 %s ｜ 取最强 %s ｜ 主桶 %s 剩余抗性 %.0f%%"
                % (_rrb.get("add") or "—", _rrb.get("max") or "—", _main, _res))
            # ★ 转化维度的同一套标定（用真实加成口径算「转出去亏多少」）
            if "conv_net" in (getattr(O, "FKEYS", None) or ()):
                O.recalibrate_conv(pct=_pctm, share=_ev.get("share") or None,
                                   main=_main, log=_log)
            # ★ 暴击维度（crit）的同一套标定（Q6）——
            #   之前 `W_DMG['crit']` 是**写死的 1.00**，而 v2 之前的模型对暴击
            #   零响应 ⇒ 优化器把「+40% 暴击伤害」按线性 % 伤害估价，代价无人察觉。
            #   现在用**真实暴击口径**（暴击率 / 期望倍率 / 加成基准）推导权重。
            _hit = _ev.get("hit") or {}
            if _hit.get("crit_chance") is not None:
                O.recalibrate_crit(chance=_hit.get("crit_chance"),
                                   expected=_hit.get("expected"),
                                   pct_ref=_ev.get("pct_ref"), log=_log)
            # ★ 减抗技能每级价值 → 折进技能分（装备给「刺骨战吼 +N」才被正确估值）
            O.recalibrate_rr_skills(log=_log)
        except Exception as _e:
            log("  [减抗] 标定失败（沿用保守基线）：%s: %s" % (type(_e).__name__, _e))

    # ---- 起点：不满抗就先跑满抗搜索
    base = cur_sol
    if args.goal in ("super", "fullres") and not full:
        # ★ 常见情形：**双持/靠武器补抗性**的 build，砍掉武器后 12 槽必然不满抗
        #   → 束搜索会走到「无可行组合」死路。此时自动带上武器重跑一次
        #   （用环境变量防重入；`sys.orig_argv` 保留原始命令行）。
        if not args.with_weapon and not os.environ.get("GD_AUTO_REEXEC"):
            _w = current_plan(char, True)
            if _w.get("主手") or _w.get("副手"):
                log("  ⚠ 12 槽下不满抗（这套 build 可能靠武器补抗性）→ 自动带武器重跑")
                argv = list(getattr(sys, "orig_argv", None)
                            or ([sys.executable] + sys.argv))
                env = dict(os.environ, GD_AUTO_REEXEC="1")
                try:
                    return subprocess.call(argv + ["--with-weapon"], env=env)
                except Exception as e:                       # noqa: BLE001
                    log("  ⚠ 自动重跑失败（%s），继续按 12 槽处理" % type(e).__name__)
        base = run_cap_search(O, args.beam, args.restart, args.jobs, _log,
                              solver=getattr(args, "cap_solver", "auto"),
                              gap=getattr(args, "ilp_gap", 1e-3),
                              ilp_time_limit=getattr(args, "ilp_time_limit", 60.0),
                              char=char)
        _, br = coverage(O, base)
        log("  满抗搜索后覆盖 %.0f/%d"
            % (sum(min(br.get(t, 0.0), O.NEED[t]) for t in O.TYPES), need_total(O)))

    # ---- 局部搜索 + 微调算法（LNS 默认 / 退火可选）（N>1 时改成并行跑 N 条独立链取最优）
    an_s = getattr(args, "anneal_seconds", 0) or 0
    n_chain = max(1, int(getattr(args, "chains", 1) or 1))
    _np = int(getattr(args, "procs", 0) or 0)
    # ★★ 并行度分配说明（`_alloc_parallel` 算的）—— 必须打出来，否则用户不知道
    #    「32 核机器为什么只跑 16 个进程」「链内进程数从哪来」。
    if getattr(args, "_alloc_note", ""):
        _log("  ⚙ 并行分配：%s" % args._alloc_note)
    # ★ 邻域并行器（`--procs N`）：
    #   · **单链** ⇒ 在主进程内建池（池建一次、全程复用，建池+预热 ~2 s）；
    #   · **多链** ⇒ 交给每个 worker 自己建（`_chain_init(procs=…)`）——
    #     主进程不再建池，避免「父池 + 子池」两层池叠加。
    _par = None
    if _np > 0 and args.goal != "fullres" and n_chain == 1:
        from tune_dps import ParEval
        _par = ParEval(char, arch, procs=_np).warm(cur_sol)
        _log("  ⚡ 邻域并行 %d 进程（建池+预热 %.1f s）" % (_np, _par.t_warm))
    try:
        if args.goal == "fullres":
            sol, best = base, real_dps(base)
        elif n_chain > 1:
            sol, best = _run_chains(O, real_dps, base, args, an_s, n_chain, char, arch,
                                    _log, procs=_np)
        else:
            sol, best = run_chain(O, real_dps, base, seed=20260919, perturb=0,
                                  passes=args.passes, topk=args.topk, pair_k=args.pair_k,
                                  max_per_slot=args.max_per_slot, anneal_seconds=an_s,
                                  anneal_iters=int(getattr(args, "anneal_iters", 0) or 0),
                                  goal=args.goal, log=_log, tag="",
                                  algo=str(getattr(args, "algo", "lns") or "lns"),
                                  lns_k=tuple(int(x) for x in
                                              str(getattr(args, "lns_k", "2,3,4")).split(",")),
                                  lns_topk=int(getattr(args, "lns_topk", 18) or 18),
                                  lns_cap=int(getattr(args, "lns_cap", 240) or 240),
                                  lns_hist=int(getattr(args, "lns_hist", 50) or 50),
                                  patience=int(getattr(args, "lns_patience", 0) or 0),
                                  proxy_tol=float(getattr(args, "lns_proxy_tol", 0.0) or 0.0),
                                  par=_par)
    finally:
        if _par is not None:
            _par.close()
            _log("  ⚡ 邻域并行统计：%d 次评估 / %.1f s（%.3f ms/次）"
                 % (_par.n_eval, _par.t_eval,
                    _par.t_eval / max(1, _par.n_eval) * 1000))

    # ---- 武器一致性守卫（放开武器槽后必须兜一道）
    if not _weapon_ok(O, sol):
        _log("  ⚠ 武器组合不合法（双手武器与副手并存）→ 武器槽回退为现状")
        for _s in ("主手", "副手"):
            if _s in base:
                sol[_s] = base[_s]
        best = real_dps(sol)

    # ---- 输出
    _, fr = coverage(O, sol)
    out = args.out or str(paths.DATA_DIR / "plans" / ("%s_auto.json" % char.lstrip("_")))
    json.dump({k: list(v) for k, v in sol.items()}, open(out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    log("")
    log("=" * 78)
    log("结果 ｜ 覆盖 %.0f/%d ｜ 真实 DPS %s（现状 %s，%+.1f%%）"
        % (sum(min(fr.get(t, 0.0), O.NEED[t]) for t in O.TYPES), need_total(O),
           format(best, ","), format(cur_dps, ","),
           (best / cur_dps - 1) * 100 if cur_dps else 0))
    for t in O.TYPES:
        v = fr.get(t, 0.0)
        nd = O.NEED[t]
        log("    %-4s %6.0f / 需 %3d  %s" % (t, v, nd,
            "★封顶" if v >= nd - 1e-9 else "差 %.0f ✗" % (nd - v)))
    log("  评估次数（含缓存命中）：%d" % len(memo))
    log("  方案 → %s" % out)
    log("  现状 → %s" % cur_path)

    # ---- 中文报告
    md = str(paths.DATA_DIR / "plans" / ("%s_auto.md" % char.lstrip("_")))
    r = _run([PY, "-m", "gd.planreport", out, "--char", char, "--archetype", arch,
              "--level", str(level), "--out", md], _log, env=_report_env())
    if r.returncode == 0:
        log("  报告 → %s" % md)

    # ---- 落档
    ok = True
    if args.apply:
        note = "%s 自动配装（%s）" % (char, GOAL_ZH[args.goal])
        ok = apply_plan(char, out, note, _log)

    log("")
    log("总耗时 %.1f s" % (time.time() - t_all))
    log("=" * 78)
    return 0 if ok else 1


class _silence:
    """吞掉 gd.opt 模块级的那一堆打印（它的启动搜索会打印整张表）。"""

    def __enter__(self):
        import io
        self._o, self._e = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = io.StringIO()
        return self

    def __exit__(self, *a):
        sys.stdout, sys.stderr = self._o, self._e
        return False


def main():
    ap = argparse.ArgumentParser(description="一条命令跑完 读档 → 满抗搜索 → 真实 DPS 微调 → 报告/落档")
    ap.add_argument("char", nargs="?", default="")
    ap.add_argument("--list", action="store_true", help="列出角色 + 自动判定形态")
    ap.add_argument("--goal", default="super", choices=["super", "fullres", "dmg"])
    ap.add_argument("--with-weapon", action="store_true", help="连主手/副手一起优化")
    ap.add_argument("--apply", action="store_true", help="出方案后直接落档（自动备份+复检）")
    ap.add_argument("--archetype", default="", help="手动指定形态键（默认自动判定）")
    ap.add_argument("--beam", type=int, default=3000, help="满抗搜索束宽（默认 3000）")
    ap.add_argument("--restart", type=int, default=2, help="满抗搜索重启次数（默认 2）")
    ap.add_argument("--cap-solver", default="auto", choices=["auto", "ilp", "beam"],
                    help="满抗搜索求解器：ilp=整数规划（全局最优，约 4 s，含支配剪枝）｜"
                         "beam=束搜索（旧路径，约 74 s，GD_FULL 下目标不含伤害项）｜"
                         "auto=先 ilp，不可行/异常时回退 beam（默认）")
    ap.add_argument("--ilp-gap", type=float, default=1e-3,
                    help="ILP 的 MIP 相对间隙。⚠ 实测 1e-3 ~ 1e-1 **耗时与解完全相同**"
                         "（HiGHS 是证完最优性才停，MIP gap 归零）—— 别指望它提速，"
                         "详见 docs/pitfalls.md #93")
    ap.add_argument("--ilp-time-limit", type=float, default=60.0,
                    help="ILP 单次求解上限秒数（默认 60）")
    ap.add_argument("--no-dom", action="store_true",
                    help="关闭满抗 ILP 的 (抗性,目标) 支配剪枝（陷阱 #93）。默认**开启**；"
                         "剪枝是精确的（变量 −66%%、求解 −62%%、解与目标逐位不变），"
                         "本开关只用于做对照/排查")
    ap.add_argument("--jobs", type=int, default=0,
                    help="★ 束搜索**线程**数（0=自动=物理核）。只作用于满抗束搜索的"
                         "重启并行，而那条路被 GIL 锁成近似串行 ⇒ 对总墙钟几乎无影响。"
                         "**真正吃满多核的是 --chains**。为免误用，显式传 --jobs N(N>1) "
                         "且未指定 --chains 时会自动转成 N 条并行搜索链。")
    ap.add_argument("--chains", type=int, default=0,
                    help="★ 并行**独立搜索链**数（0=自动：**逻辑核数**，再按可用内存"
                         "与逻辑核两道闸封顶）。每条链跑完整一轮，墙钟≈单链、"
                         "总搜索量 ×N，取最优。这是唯一真正吃满多核的开关。")
    ap.add_argument("--passes", type=int, default=2, help="局部搜索轮数")
    ap.add_argument("--topk", type=int, default=60,
                    help="每槽实算 DPS 的候选数（0=全部可行，但受 --max-per-slot 硬顶）")
    ap.add_argument("--pair-k", type=int, default=2, help="双槽联动候选数（0=关）")
    ap.add_argument("--max-per-slot", type=int, default=120,
                    help="每槽实算 DPS 的硬上限（防候选爆炸）")
    ap.add_argument("--allow-faction", action="store_true",
                    help="允许派系件（默认排除：声望不够会被游戏整件清空）")
    ap.add_argument("--attr-gate", action="store_true", help="用属性点预算闸门（默认关）")
    ap.add_argument("--out", default="")
    ap.add_argument("--force", action="store_true",
                    help="无视**同输出**互斥锁强行启动（**会与已在跑的那个抢同一个"
                         "输出文件**，仅在确认对方已卡死时用）。"
                         "注意：不同 `--out` 之间本来就允许并发（见 tools/sweep_arch.py）")
    ap.add_argument("--quick", action="store_true", help="速跑：束宽 1500 / 重启 1 / 局部 1 轮")
    ap.add_argument("--thorough", action="store_true", help="狠跑：束宽 8000 / 重启 6 / 局部 3 轮")
    ap.add_argument("--extreme", action="store_true",
                    help="★ 极限模式：候选池开到底 + 允许派系 + 含武器 + 双槽6 + "
                         "**LNS 400 轮**（2026-09-20 前是「退火 420 s」）")
    ap.add_argument("--anneal-seconds", type=float, default=0.0,
                    help="退火预算（秒）；0=关闭")
    ap.add_argument("--anneal-iters", type=int, default=0,
                    help="★ 退火**按迭代次数**停（>0 时忽略 --anneal-seconds）。"
                         "配合固定 --chains 可做到**完全可复现**（墙钟预算模式下"
                         "机器负载会影响迭代数，结果会抖）。")
    # ★★ 2026-09-20：微调算法从「模拟退火」换成 **LNS + LAHC**（见 tune_dps.lns_search）
    ap.add_argument("--algo", default="lns", choices=["lns", "anneal", "none"],
                    help="微调算法：lns=**大邻域搜索 + LAHC**（默认；每轮随机放开 k 个槽做"
                         "精确枚举，实测 100 轮/26.7 s 从 94,474 → 117,805 = +24.70%%，"
                         "600 轮/106.5 s → 128,756；而 420 s×30 链退火只有 94,474）"
                         "｜anneal=旧的模拟退火（保留，用于复现历史结果 / 对照）"
                         "｜none=只跑单槽/双槽贪心")
    ap.add_argument("--lns-k", default="2,3,4",
                    help="LNS 每轮**放开的槽数**（逗号分隔，默认 2,3,4）。"
                         "k 越大邻域越大、越能跳出局部最优，但每轮组合数按 topk^k 增长")
    ap.add_argument("--lns-topk", type=int, default=18,
                    help="LNS 每槽候选数（按线性代理降序取，默认 18）")
    ap.add_argument("--lns-cap", type=int, default=240,
                    help="LNS 每个邻域**实算真实 DPS**的组合数上限（默认 240）。"
                         "代理只用来排序，真实 DPS 才是判据 —— 这是主要成本项")
    ap.add_argument("--lns-hist", type=int, default=50,
                    help="LAHC 历史队列长度（默认 50）。越大越保守")
    ap.add_argument("--procs", type=int, default=0,
                    help="★ 每条链**内部**邻域评估的进程数（0=自动）。与 `--chains` **不再互斥** ——"
                         "总进程数按 `chains × procs ≤ 物理核` 分配（见 `_alloc_parallel`）。"
                         "实测加速比：2 进程 1.9×｜4 进程 3.3×｜8 进程 6.8×｜16 进程 10.2×。"
                         "**只想单链最省墙钟**：`--chains 1 --procs 16`")
    ap.add_argument("--lns-patience", type=int, default=0,
                    help="★ LNS 连续 N 轮未刷新最优即提前结束（0=关，默认关 ⇒ 逐位零漂移）。"
                         "LNS 后半段大量轮次在做无效探索，早停可省 30~50%% 墙钟")
    ap.add_argument("--lns-proxy-tol", type=float, default=0.0,
                    help="★ LNS 邻域**代理窗口**：只对「线性代理 ≥ 最优代理 × (1−tol)」的组合"
                         "做真实评估（0=关）。代理与真实 DPS 秩相关很高 ⇒ 几乎不丢解")
    ap.add_argument("--time-budget", type=float, default=0.0,
                    help="★ 整轮搜索的**墙钟硬预算**（秒，0=不限）。到点即收尾并落盘 ——"
                         "用于「必须在 N 分钟内出结果」的场景")
    ap.add_argument("--pool-topn", type=int, default=0, help="极限模式的每槽基础候选数（默认 150）")
    ap.add_argument("--pool-dmg-topn", type=int, default=0,
                    help="极限模式额外并入的「输出排序前 N 件」（默认 400，0=不并）")
    ap.add_argument("--comp-topn", type=int, default=0, help="极限模式的镶嵌候选数（默认 24）")
    ap.add_argument("--aug-topn", type=int, default=0, help="极限模式的附魔候选数（默认 14）")
    ap.add_argument("--dedup", type=int, default=0, help="束层去重超采样倍数（默认 6）")
    # ★ 减抗（RR，见 `gd/rr.py`）两个开关
    ap.add_argument("--enemy-res", dest="enemy_res", default="elite",
                    choices=["none", "elite", "boss", "high", "max"],
                    help="敌方抗性档位（默认 elite=33%%）。影响减抗件的估值与「实战 DPS」；"
                         "用户口径是「不是硬指标，逼近即可」，所以默认取中间档而不是 max")
    ap.add_argument("--real-goal", dest="real_goal", default="vs", choices=["vs", "panel"],
                    help="局部搜索/退火用什么做目标：vs=实战 DPS（含敌方减抗乘区，默认）｜"
                         "panel=纯面板 DPS（旧口径，便于对照）")
    ap.add_argument("--no-rr", dest="rr", action="store_false", default=True,
                    help="完全关掉减抗维度（退回只有线性伤害代理的旧行为）")
    ap.add_argument("--rr-uptime", type=float, default=0.0,
                    help="减抗覆盖率折扣（默认 0.7）；调小=对减抗更保守")
    a = ap.parse_args()
    a.no_faction = not a.allow_faction
    if a.extreme and a.no_faction:
        a.no_faction = False       # 极限模式默认放开派系件

    # ★ 用户是否**显式**传过 --jobs（决定「并行意图」是否要转成并行链，见下方 chains 判定）
    _jobs_explicit = any(t == "--jobs" or t.startswith("--jobs=") for t in sys.argv[1:])

    if a.jobs == 0:
        # 束搜索在主进程内用**线程**并行（numpy 释放 GIL），上限 = 物理核。
        a.jobs = max(1, _phys_cores())

    if a.list:
        archs = paths.load_json("archetypes.json") or {}
        print("%-10s %-5s %-16s %s" % ("角色", "等级", "职业编号", "自动形态"))
        for k in paths.characters():
            lv, cids, sk = read_meta(k)
            print("%-10s %-5d %-16s %s" % (k, lv, ",".join(map(str, cids)),
                                           pick_archetype(cids, sk, archs)))
        return 0

    if not a.char:
        print("✗ 需要角色名（或 --list）")
        return 2

    # ★ 锁是**按输出文件**加的（同一 `--out` 才互斥），不是全局单实例 ——
    #   否则「多形态并发普查」会被自己挡住（实测 sweep_arch 第 6 个形态被拒）。
    _out_pre = a.out or str(paths.DATA_DIR / "plans"
                            / ("%s_auto.json" % a.char.lstrip("_")))
    if not acquire_single_instance(a.char, _out_pre, force=a.force):
        return 4

    if a.thorough:
        a.beam, a.restart, a.passes, a.pair_k = 8000, 6, 3, 4
        a.max_per_slot = 300
    if a.extreme:
        a.with_weapon = True
        a.beam, a.restart = max(a.beam, 12000), max(a.restart, 6)
        a.passes, a.pair_k = max(a.passes, 4), max(a.pair_k, 4)
        a.topk = 0
        a.max_per_slot = max(a.max_per_slot, 200)   # 每槽实算上限（防候选爆炸吃光时间）
        if not a.pool_topn:
            a.pool_topn = 400
        if not a.pool_dmg_topn:
            a.pool_dmg_topn = 600
        if not a.comp_topn:
            a.comp_topn = 32
        if not a.aug_topn:
            a.aug_topn = 16
        if not a.dedup:
            a.dedup = 6
        # ★★ 2026-09-22 实测：`--lns-cap` 240 → **120** 时墙钟 **113 s → 67.8 s（−40%）**，
        #   而结果**逐位相同**（175,726 / +7.2%）。原因：邻域里按线性代理降序排列后，
        #   前 120 个已经覆盖了全部真正竞争者，后 120 个只是陪跑。
        #   （显式传 `--lns-cap N` 仍然优先 —— 只想改这一项时不必绕开 --extreme。）
        _cap_given = any(t == "--lns-cap" or t.startswith("--lns-cap=")
                         for t in sys.argv[1:])
        if not _cap_given:
            a.lns_cap = 120
        if not a.anneal_seconds and not a.anneal_iters:
            # ★★ 2026-09-20：默认从「退火 420 s」改为「**LNS 400 轮**」。
            #   动因（实测）：420 s×30 链退火最好只有 94,474，而 LNS 单链 100 轮
            #   26.7 s 就到 117,805（+24.70%）。退火的两个结构性缺陷（温度挂墙钟、
            #   moves 随预算加四槽后绝大多数迭代被满抗预检 continue）见 lns_search 文档。
            #   显式 `--algo anneal --anneal-seconds N` 仍可走回旧路径。
            a.algo = "lns"
            a.anneal_iters = 400
    # ★ 并行链默认：**逻辑核**（见下方长注释），不再默认 1 条。
    #   **显式传 `--chains N` 永远优先**（不被 --quick / --extreme 覆盖）。
    if a.quick:
        a.beam, a.restart, a.passes, a.pair_k = 1500, 1, 1, 1
        a.topk, a.max_per_slot = 25, 30
        a.anneal_seconds = 0
    # ★ 用户是否**显式**指定了并行度（决定 `_alloc_parallel` 走显式还是自动分支）
    _chains_explicit = bool(a.chains)
    if not a.chains and _jobs_explicit and a.jobs > 1:
        # ★ 显式 `--jobs N` 是**用户的并行意图**，优先级**高于** --quick/--extreme
        #   的模式预设 —— 否则 `--quick --jobs 20` 会先被 quick 分支置成 1 条链，
        #   并行意图被静默吞掉（这个顺序坑实测踩过一次）。
        #   --jobs 走的是束搜索的**线程池**，被 GIL 锁成近似串行 ⇒ 在这里兑现成
        #   N 条独立搜索链（真·多进程，每条链独立单线程 ⇒ 吃满多核）。
        a.chains = min(a.jobs, _logical_cores())
        _chains_explicit = True
        print("  ℹ 检测到 --jobs %d：束搜索线程池受 GIL 限制几乎不加速，"
              "已自动转为 %d 条并行搜索链（只想单链请显式传 --chains 1）"
              % (a.jobs, a.chains), flush=True)
    # ★★ 2026-09-22：`--procs` 与 `--chains` **统一分配**（不再互斥）。
    #   总进程预算 = **物理核**；`_alloc_parallel` 负责在两者间划分。
    #   ⚠ 都不显式传 ⇒ 两个都传 0 进去走**自动折中**（不是把 chains 默认成逻辑核，
    #     否则 32 逻辑核机器会算出「32 条链 × 每链 1 进程」——正是要修的那个配置）。
    _pc = _phys_cores()
    _n_ch, _n_pr, _note = _alloc_parallel(
        _pc, chains=(a.chains if _chains_explicit else 0),
        procs=int(getattr(a, "procs", 0) or 0))
    a.chains, a.procs = _n_ch, _n_pr
    a._alloc_note = _note
    if getattr(a, "algo", "lns") == "anneal" and _n_pr > 0:
        print("  ⚠ 链内并行器只对 LNS 生效；当前 --algo anneal，已把 --procs 置 0", flush=True)
        a.procs = 0
        a.chains = max(1, _pc)
    a.chains = _cap_chains_by_mem(a.chains)
    return run(a)


if __name__ == "__main__":
    raise SystemExit(main())
