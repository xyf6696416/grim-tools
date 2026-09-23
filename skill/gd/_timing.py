"""时序埋点 —— 兼容旧脚本里的 `TM.auto()/TM.mark()/TM.span()` 调用。

旧实现会把 spans 落到 `logs/flow_timing.jsonl`。新架构下加载只要 0.1 s，
埋点收益不大，这里做成 no-op；把 `GD_TIMING=1` 置上才真的记录。
"""

from __future__ import annotations

import contextlib
import json
import os
import time

enabled = bool(os.environ.get("GD_TIMING"))
_LOG = None


def _log_path():
    global _LOG
    if _LOG is None:
        from . import paths
        paths.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _LOG = paths.CACHE_DIR / "timing.jsonl"
    return _LOG


def auto(*_, **__):
    """旧 API：自动开启埋点。这里只在 GD_TIMING=1 时生效。"""
    return enabled


def mark(name, **extra):
    if not enabled:
        return
    try:
        with _log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "kind": "mark", "name": name,
                                **extra}, ensure_ascii=False) + "\n")
    except OSError:
        pass


@contextlib.contextmanager
def span(name, **extra):
    if not enabled:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        try:
            with _log_path().open("a", encoding="utf-8") as f:
                f.write(json.dumps({"t": time.time(), "kind": "span", "name": name,
                                    "ms": round((time.perf_counter() - t0) * 1000, 1),
                                    **extra}, ensure_ascii=False) + "\n")
        except OSError:
            pass
