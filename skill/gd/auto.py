# -*- coding: utf-8 -*-
"""`gd auto` 的入口 —— 转发到 `tools/autobuild.py`（自动化编排脚本）。

为什么放这层：`autobuild.py` 在 `tools/` 下（属于「构建/运维脚本」，不属于库），
但用户希望在统一 CLI 里用到它。这里做一层薄转发，不改 `autobuild.py` 的独立可用性。
"""
import runpy
import sys
from pathlib import Path


def main() -> int:
    p = Path(__file__).resolve().parent.parent / "tools" / "autobuild.py"
    if not p.exists():
        print("✗ 找不到 %s" % p)
        return 2
    sys.argv = [str(p)] + list(sys.argv[1:])
    runpy.run_path(str(p), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
