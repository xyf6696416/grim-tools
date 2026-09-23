#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""清掉**孤儿 python 进程** —— 父进程已死、自己还挂着的那些。

★ 为什么会有孤儿（2026-09-22 实测）
  优化器的进程树是**两层**：`gd auto`（父）→ 链进程（非 daemon，因为链内还要建邻域池）
  → `ParEval` worker（每链 `--procs` 个）。
  · Windows 上**子进程不会随父进程退出而结束**；
  · `Process.terminate()` / `taskkill /F /PID`（不带 `/T`）**只杀一个进程**；
  ⇒ 一旦父进程被强杀（`timeout` 到点、手动 `taskkill`、任务管理器结束进程），
    整棵子树就**变成孤儿**：CPU 0%、内存占着、`tasklist` 里越堆越多。

★ 现在代码里已做三件事（本工具是**第四道保险**，用于清理历史遗留）：
  1. `autobuild._chain_worker` 用 `try/finally` **显式关闭链内邻域池**；
  2. `autobuild._run_chains` 收尾一律走 **`_kill_tree()`（`taskkill /T`）**，
     不再用只杀自己的 `terminate()`；
  3. 主进程对「静默死掉的链」有超时跳出 + 树杀收尾。

用法
----
```bash
PY=".../envs/default/Scripts/python.exe"
$PY tools/kill_orphans.py                 # 只列出（**默认不杀**）
$PY tools/kill_orphans.py --any-idle      # 连「父还活着但完全空闲」的也列出来
$PY tools/kill_orphans.py --yes           # 真正杀掉「孤儿」
$PY tools/kill_orphans.py --yes --any-idle
```
⚠ 默认只杀 **父进程已死** 的；`--any-idle` 会扩大到「完全空闲」——
  那可能误伤别的东西（例如某些常驻服务会在空闲时 0% CPU），**自己看清楚再决定**。
"""
import argparse
import ctypes
import ctypes.wintypes as w
import os
import subprocess
import sys
import time

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
TH32CS_SNAPPROCESS = 0x2


class PE32(ctypes.Structure):
    _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ProcessID', w.DWORD),
                ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)),
                ('th32ModuleID', w.DWORD), ('cntThreads', w.DWORD),
                ('th32ParentProcessID', w.DWORD), ('pcPriClassBase', ctypes.c_long),
                ('dwFlags', w.DWORD), ('szExeFile', ctypes.c_char * 260)]


class FT(ctypes.Structure):
    _fields_ = [('lo', w.DWORD), ('hi', w.DWORD)]


def _snapshot():
    """→ [(pid, ppid, exe), …]（ctypes Toolhelp32；不依赖 wmic/tasklist）。"""
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    e = PE32()
    e.dwSize = ctypes.sizeof(PE32)
    out = []
    ok = k32.Process32First(snap, ctypes.byref(e))
    while ok:
        out.append((int(e.th32ProcessID), int(e.th32ParentProcessID),
                    e.szExeFile.decode('gbk', 'ignore')))
        ok = k32.Process32Next(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return out


def cpu_seconds(pid):
    h = k32.OpenProcess(0x1000, False, int(pid))
    if not h:
        return None
    c, e, k, u = FT(), FT(), FT(), FT()
    ok = k32.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                             ctypes.byref(k), ctypes.byref(u))
    k32.CloseHandle(h)
    if not ok:
        return None
    g = lambda t: (t.hi << 32) | t.lo
    return (g(k) + g(u)) / 1e7


def mem_mb(pid):
    """工作集大小（MB）；拿不到返回 0。"""
    class PMC(ctypes.Structure):
        _fields_ = [('cb', w.DWORD), ('PageFaultCount', w.DWORD),
                    ('PeakWorkingSetSize', ctypes.c_size_t),
                    ('WorkingSetSize', ctypes.c_size_t),
                    ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                    ('PagefileUsage', ctypes.c_size_t),
                    ('PeakPagefileUsage', ctypes.c_size_t)]
    h = k32.OpenProcess(0x0400, False, int(pid))          # QUERY_INFORMATION
    if not h:
        return 0
    m = PMC()
    m.cb = ctypes.sizeof(PMC)
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(m), m.cb)
    k32.CloseHandle(h)
    return (m.WorkingSetSize // 1048576) if ok else 0


def ancestors(pid):
    """本进程的全部祖先进程（防止把自己/Agent 运行时杀掉）。"""
    par = {p: pp for p, pp, _ in _snapshot()}
    out, cur, seen = {os.getpid()}, par.get(int(pid), 0), 0
    while cur and cur not in out and seen < 40:
        out.add(cur)
        cur = par.get(cur, 0)
        seen += 1
    return out


def kill_tree(pid):
    """连子孙一起杀（`taskkill /T`）。"""
    try:
        r = subprocess.run(['taskkill', '/T', '/F', '/PID', str(int(pid))],
                           capture_output=True)
        return r.returncode == 0
    except Exception as e:                                # noqa: BLE001
        print('   × %d 杀失败：%s' % (pid, e))
        return False


def main():
    ap = argparse.ArgumentParser(description='清理孤儿 python 进程（前两层优化器进程树）')
    ap.add_argument('--yes', action='store_true', help='真正执行；不带则只列')
    ap.add_argument('--any-idle', action='store_true',
                    help='扩大到「父进程还活着但完全空闲」的进程（可能误伤，慎用）')
    ap.add_argument('--sample', type=float, default=3.0, help='CPU 采样秒数（默认 3）')
    ap.add_argument('--exe', default='python', help='目标可执行名（默认 python）')
    a = ap.parse_args()

    snap = _snapshot()
    alive = {p for p, _, _ in snap}
    mine = ancestors(os.getpid())
    cand = [(p, pp) for p, pp, nm in snap
            if a.exe in nm.lower() and p not in mine and p != os.getpid()]

    c0 = {p: cpu_seconds(p) for p, _ in cand}
    time.sleep(max(0.5, a.sample))
    c1 = {p: cpu_seconds(p) for p, _ in cand}

    rows = []
    for p, pp in cand:
        busy = (c1.get(p) or 0) - (c0.get(p) or 0)
        orphan = pp not in alive
        rows.append((p, pp, busy, orphan, mem_mb(p)))
    rows.sort(key=lambda r: -r[4])

    orph = [r for r in rows if r[3]]
    idle = [r for r in rows if (not r[3]) and r[2] < 0.05]
    work = [r for r in rows if r[2] >= 0.05]

    print('=' * 84)
    print('%s 进程 %d 个 ｜ 采样 %.0f s ｜ 本进程 PID %d'
          % (a.exe, len(rows), a.sample, os.getpid()))
    print('=' * 84)
    print('%-9s %-9s %-10s %-8s %-8s %s' % ('PID', 'PPID', 'CPU占用', '孤儿', '内存MB', '判定'))
    for p, pp, busy, orphan, mb in rows:
        tag = ('★孤儿（父已死）' if orphan
               else ('在算' if busy >= 0.05 else '空闲（父还在）'))
        print('%-9d %-9d %-10.2f %-8s %-8d %s'
              % (p, pp, busy, '是' if orphan else '否', mb, tag))

    print()
    print('汇总：孤儿 %d ｜ 在算 %d ｜ 空闲但父活着 %d'
          % (len(orph), len(work), len(idle)))
    target = list(orph) + (list(idle) if a.any_idle else [])
    if not target:
        print('✓ 没有需要清理的进程')
        return 0
    if not a.yes:
        print('\n（**默认不杀**）确认无误后加 `--yes` 执行。待清理 %d 个：%s'
              % (len(target), sorted(r[0] for r in target)))
        return 0
    print()
    n = 0
    for p, pp, busy, orphan, mb in target:
        if kill_tree(p):
            n += 1
            print('   ✓ 已杀 %d（树杀，连带子孙）' % p)
    print('\n共清理 %d 个进程' % n)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
