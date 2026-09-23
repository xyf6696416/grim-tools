#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把本 skill 打包成可分发的 zip（移植到其他 agent / 其他机器测试用）。

用法：
    python tools/pack_skill.py                    # 出 full + slim 两个包到 E:\\xz\\Archives
    python tools/pack_skill.py --out D:/tmp       # 换输出目录
    python tools/pack_skill.py --only full        # 只出一个
    python tools/pack_skill.py --verify           # 打包后自动解压到临时目录跑 selftest

排除项（可再生，不入包）：
    · `__pycache__/`、`*.pyc`
    · `data/cache/*.pkl`、`data/cache/prune/`   ← load_json 的 (mtime,size) pickle 缓存
    · `data/scratch/`                            ← 运行期临时物（含 .autobuild.locks）
    · `*.tmp`
    · slim 包额外排除 `data/cache/*.js`（38 MB 离线库原始解包；装了 Grim Tools 后
      跑 `tools/migrate_from_archive.py` 可从 app.asar 重落）

★ 为什么默认出 full：`data/cache/*.js` 压缩比极高（38 MB → ~1.6 MB），
  保留它只多 1.6 MB 却换来「目标机无需装 Grim Tools 也能重建」的能力。
  只有目标机磁盘/带宽极紧时才用 slim。

校验口径：包内自带 `README_PORTING.md`；装完跑 `tools/selftest.py`（期望 379 全绿）
与 `python -m gd dps Sam`（期望 合计 101976 ｜ 实战 152476）即零漂移。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = Path(r"E:\xz\Archives")
ARC_ROOT = "grim-dawn"          # zip 内的顶层目录名（解压即可直接放进 skills/）


def _drop(rel: str, slim: bool) -> bool:
    rel = rel.replace("\\", "/")
    parts = rel.split("/")
    if "__pycache__" in parts:
        return True
    if rel.endswith(".pyc") or rel.endswith(".tmp"):
        return True
    if rel.startswith("data/cache/prune"):
        return True
    if rel.startswith("data/scratch"):
        return True
    if rel.startswith("data/cache/") and rel.endswith(".pkl"):
        return True
    # ★ 防呆：zip 内的顶层目录就叫 `grim-dawn`。若 skill 根下**又**出现一个
    #   `grim-dawn/`（打包验证时手工解压留下的快照），必须排除 —— 否则整个包体
    #   翻倍，目标 agent 还会看到两份 skill。2026-09-20 实测踩过（82 MB 重复）。
    if rel == "grim-dawn" or rel.startswith("grim-dawn/"):
        return True
    if slim and rel.startswith("data/cache/") and rel.endswith(".js"):
        return True
    return False


def collect(slim: bool):
    """产出 [(绝对路径, zip 内相对名)]。"""
    out = []
    for dp, dns, fns in os.walk(SKILL_ROOT):
        dns[:] = [d for d in dns if d != "__pycache__"]
        for f in fns:
            p = Path(dp) / f
            rel = os.path.relpath(p, SKILL_ROOT).replace("\\", "/")
            if _drop(rel, slim):
                continue
            out.append((p, rel))
    out.sort(key=lambda x: x[1])
    return out


def build(slim: bool, out_dir: Path, stamp: str, readme: Path | None):
    tag = "slim" if slim else "full"
    zip_path = out_dir / f"grim-dawn_skill_{tag}_{stamp}.zip"
    files = collect(slim)
    raw = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p, rel in files:
            z.write(p, f"{ARC_ROOT}/{rel}")
            try:
                raw += p.stat().st_size
            except OSError:
                pass
        if readme and readme.exists() and "README_PORTING.md" not in {r for _, r in files}:
            z.write(readme, f"{ARC_ROOT}/README_PORTING.md")
    size = zip_path.stat().st_size
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    return {
        "tag": tag, "path": zip_path, "zip_name": zip_path.name,
        "n": len(files), "raw_mb": raw / 1048576, "zip_mb": size / 1048576,
        "sha256": digest,
    }


def verify(zip_path: Path, python_exe: str) -> tuple[bool, str]:
    """解压到临时目录跑 selftest + 基准，证明 zip 本体可用。"""
    tmp = Path(tempfile.mkdtemp(prefix="gd_pack_verify_"))
    try:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        root = tmp / ARC_ROOT
        st = subprocess.run([python_exe, "tools/selftest.py"], cwd=root,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        tail = (st.stdout or "").strip().splitlines()[-4:]
        ok_self = st.returncode == 0 and "全部通过" in (st.stdout or "")
        ds = subprocess.run([python_exe, "-m", "gd", "dps", "Sam"], cwd=root,
                            capture_output=True, text=True, encoding="utf-8", errors="replace")
        ok_dps = "101976" in (ds.stdout or "")
        msg = " | ".join(x.strip() for x in tail if x.strip())
        return (ok_self and ok_dps), msg + ("  ｜ dps Sam 101976 ✓" if ok_dps else "  ｜ dps Sam ✗")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="打包 grim-dawn skill 为可分发 zip")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录（默认 E:\\xz\\Archives）")
    ap.add_argument("--only", choices=["full", "slim"], help="只出一个包")
    ap.add_argument("--stamp", help="日期戳（默认取 data/*.json 最新修改日期）")
    ap.add_argument("--readme", default=None, help="额外塞入的 README 路径")
    ap.add_argument("--verify", action="store_true", help="打包后解压到临时目录跑 selftest+dps")
    a = ap.parse_args()

    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    stamp = a.stamp
    if not stamp:
        import datetime
        newest = 0.0
        for p, _ in collect(False):
            try:
                newest = max(newest, p.stat().st_mtime)
            except OSError:
                pass
        stamp = datetime.date.fromtimestamp(newest or os.path.getmtime(SKILL_ROOT)).strftime("%Y%m%d")

    readme = Path(a.readme) if a.readme else None
    wants = [a.only] if a.only else ["full", "slim"]
    results = [build(w == "slim", out_dir, stamp, readme) for w in wants]

    print("skill root : %s" % SKILL_ROOT)
    print("output dir : %s" % out_dir)
    print("-" * 78)
    for r in results:
        print("%-38s %3d files  zip %6.1f MB  (raw %.1f MB)" %
              (r["zip_name"], r["n"], r["zip_mb"], r["raw_mb"]))
        print("    sha256 %s" % r["sha256"])
    print("-" * 78)
    print("校验口径：解压后 `python tools/selftest.py` 应 379 全绿；"
          "`python -m gd dps Sam` 应 合计 101976 ｜ 实战 152476")

    if a.verify:
        py = sys.executable
        all_ok = True
        for r in results:
            ok, msg = verify(r["path"], py)
            all_ok = all_ok and ok
            print("[%s] %s  %s" % ("VERIFY OK" if ok else "VERIFY FAIL", r["zip_name"], msg))
        if not all_ok:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
