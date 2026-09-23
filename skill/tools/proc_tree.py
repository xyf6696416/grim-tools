"""proc_tree.py —— 看 `gd auto` 跑起来那**一堆 python 进程**分别是谁、在干什么。

回答的问题（用户 2026-09-22 两次问过）
--------------------------------------
「怎么这么慢　只有一个在跑」（= **建池阶段**，只有父进程 1 个核）
「跑的时候这么多进程是在干嘛」（= **搜索阶段**，1 + chains + chains×procs 全铺开）

`gd auto` 的进程树是**两层**（见 `docs/pitfalls.md` #89 / #90 / #91）：

    gd auto 父进程          1 个   读档 → 建池 → 满抗 ILP → 收链结果 → 出报告
    　└─ 链进程 ×chains     4 个   `ctx.Process(daemon=False)`，各自**独立跑完整一轮**
    　    └─ ParEval ×procs 16 个  链内**邻域评估并行**：每个 worker = 一个独立打分进程

默认 `--chains 4 --procs 4` ⇒ **1 + 4 + 16 = 21 个真进程**，总进程数 = **物理核数**（见
`autobuild._alloc_parallel`）。Windows 用 **spawn** 不是 fork ⇒ 每进程各自重载一份离线库，
所以内存不共享（实测：裸 python 15 MB → `import gd.opt` 111 MB → 建完池 ~147 MB，
21 × 147 MB ≈ 3 GB 是**固有代价**，不是泄漏）。

★ 角色判定不靠绝对深度（外面还套着 bash / 启动壳），而是靠**树形**：
  「python 后代最多的那个 python」= 父；它的 python 子进程 = 链；再下一层 = worker。

用法::

    python tools/proc_tree.py            # 采样 4 s，打印角色 / 个数 / 核数 / 内存
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time
from collections import defaultdict

TH32CS_SNAPPROCESS = 0x00000002


class _PE32(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_char * 260)]


class _PMC(ctypes.Structure):
    _fields_ = [("cb", wt.DWORD), ("PageFaultCount", wt.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


def _snapshot():
    """ctypes Toolhelp32 —— 本机 `wmic` 不存在、图形壳命令工具静默失败，
    bash 调 PowerShell 又被安全策略挡 ⇒ ctypes 是唯一可行路径（同 kill_orphans.py）。"""
    k32 = ctypes.windll.kernel32
    h = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    e = _PE32()
    e.dwSize = ctypes.sizeof(_PE32)
    out = {}
    if k32.Process32First(h, ctypes.byref(e)):
        while True:
            out[e.th32ProcessID] = (e.th32ParentProcessID,
                                    e.szExeFile.decode("mbcs", "replace"))
            if not k32.Process32Next(h, ctypes.byref(e)):
                break
    k32.CloseHandle(h)
    return out


def _cpu_and_mem(pids):
    k32 = ctypes.windll.kernel32
    gp = ctypes.windll.psapi.GetProcessMemoryInfo
    gp.argtypes = [wt.HANDLE, ctypes.POINTER(_PMC), wt.DWORD]
    gp.restype = wt.BOOL
    res = {}
    for p in pids:
        h = k32.OpenProcess(0x0400 | 0x1000, False, p)
        if not h:
            continue
        c, x, k, u = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
        cpu = 0.0
        if k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(x),
                               ctypes.byref(k), ctypes.byref(u)):
            def q(ft):
                return (ft.dwHighDateTime << 32) | ft.dwLowDateTime
            cpu = (q(k) + q(u)) / 1e7
        pm = _PMC()
        pm.cb = ctypes.sizeof(_PMC)
        mb = 0.0
        if gp(h, ctypes.byref(pm), pm.cb):
            mb = pm.WorkingSetSize / 1048576.0
        res[p] = (cpu, mb)
        k32.CloseHandle(h)
    return res


def main(seconds=4.0):
    snap = _snapshot()
    py = {p for p, v in snap.items() if v[1].lower().startswith("python")}
    kids = defaultdict(list)
    for p in py:
        pp = snap[p][0]
        if pp in py:
            kids[pp].append(p)

    if not py:
        print("当前没有 python 进程在跑。")
        print("（`gd auto` 正常结束后不该留任何进程；若有残留用 tools/kill_orphans.py 查看）")
        return

    def desc(p):
        n = 0
        for c in kids[p]:
            n += 1 + desc(c)
        return n

    # ★ 角色判定（实测踩过两次）：
    #   外面还套着 **3 层启动壳**（bash → 沙箱包装 → python，每个只有 ~5 MB），
    #   所以「绝对深度」和「纯后代数」都会把根判到壳上（壳的 desc 反而最大）。
    #   真判据：**有 python 子进程 且 常驻内存 ≥ 80 MB**（壳都 < 25 MB，
    #   真进程因为要读离线物品库一定 ≥ 110 MB）⇒ 在其中取后代最多者为父。
    _m0 = {p: v[1] for p, v in _cpu_and_mem(list(py)).items()}
    cands = [p for p in py if kids[p] and _m0.get(p, 0.0) >= 80.0]
    root = max(cands, key=lambda p: (desc(p), _m0.get(p, 0.0))) if cands else None
    roles = {}
    if root is not None:
        roles[root] = "父 gd auto"
        for c in kids[root]:
            roles[c] = "链进程"
            for w in kids[c]:
                roles[w] = "ParEval worker"
                for x in kids[w]:
                    roles[x] = "worker 的子进程?"
    for p in py:
        if p not in roles:
            roles[p] = ("启动壳（bash/沙箱包装）" if _m0.get(p, 0.0) < 80.0
                        else "未归类")

    t1 = _cpu_and_mem(list(py))
    time.sleep(float(seconds))
    t2 = _cpu_and_mem(list(py))

    groups = defaultdict(list)
    for p in py:
        c1, c2 = t1.get(p, (0, 0)), t2.get(p, (0, 0))
        groups[roles.get(p, "启动壳 / 其它")].append(
            (p, snap[p][0], max(0.0, (c2[0] - c1[0]) / float(seconds)), c2[1]))

    print("python 进程 %d 个 ｜ CPU 采样窗口 %.0f s（本机 16 物理核 / 32 逻辑核）"
          % (len(py), seconds))
    print("%-18s %5s %8s %7s %9s %9s" % ("角色", "个数", "小计核", "均核", "均内存MB", "总内存GB"))
    tot_cores = tot_mb = 0.0
    for name in ("父 gd auto", "链进程", "ParEval worker", "worker 的子进程?",
                 "启动壳（bash/沙箱包装）", "未归类"):
        rows = groups.get(name)
        if not rows:
            continue
        cores = sum(r[2] for r in rows)
        mb = sum(r[3] for r in rows)
        tot_cores += cores
        tot_mb += mb
        print("%-18s %5d %8.2f %7.2f %9.0f %9.2f"
              % (name, len(rows), cores, cores / len(rows), mb / len(rows), mb / 1024))
    print("-" * 64)
    print("%-18s %5d %8.2f %7s %9s %9.2f"
          % ("合计", len(py), tot_cores, "—", "—", tot_mb / 1024))
    print()
    print("★ 小计核 ≈ 物理核数（默认分配）。父进程在**建池**阶段是单核的，")
    print("  只有走到链搜索才会看到这一堆同时满跑（见 docs/pitfalls.md #94）。")


if __name__ == "__main__":
    import sys
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 4.0)
