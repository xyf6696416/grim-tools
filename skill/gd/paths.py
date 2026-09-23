"""路径/环境探测 —— 全技能所有路径的唯一来源。

不要再在别处硬编码任何路径。
"""

from __future__ import annotations

import contextlib
import glob
import os
import sys
from pathlib import Path
from typing import Optional

# ----------------------------------------------------------------- 常量
GAME_NAME = "Grim Dawn"
STEAM_APPID = "219990"          # Grim Dawn 的 Steam AppID
HOME = Path(os.path.expanduser("~"))
SKILL_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = SKILL_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"

# 离线版 Grim Tools（Electron）安装位置候选
_GT_CANDIDATES = [
    r"E:\Grim Tools",
    r"D:\Grim Tools",
    r"C:\Grim Tools",
    r"F:\Grim Tools",
    r"E:\xz\Grim Tools",
]

_STEAM_ROOTS = [
    r"C:\Program Files (x86)\Steam", r"C:\Program Files\Steam",
    r"D:\Steam", r"E:\Steam", r"F:\Steam",
    r"D:\SteamLibrary", r"E:\SteamLibrary", r"F:\SteamLibrary",
    r"D:\Games\Steam", r"E:\Games\Steam",
]
_GAME_ROOTS = [r"D:\Games", r"E:\Games", r"F:\Games",
               r"D:\Program Files", r"E:\Program Files", r"C:\Games"]


# ----------------------------------------------------------------- 离线库（Grim Tools 桌面版）
def gt_dir() -> Optional[Path]:
    """Grim Tools 桌面版安装目录（其下应有 resources/app.asar）。"""
    env = os.environ.get("GD_GT_DIR")
    if env and (Path(env) / "resources" / "app.asar").exists():
        return Path(env)
    for c in _GT_CANDIDATES:
        p = Path(c)
        if (p / "resources" / "app.asar").exists():
            return p
    for drive in "CDEFG":
        for p in glob.glob(f"{drive}:/**/resources/app.asar"):
            cand = Path(p).parent.parent
            if (cand / "Grim Tools.exe").exists():
                return cand
        # 只扫一层，避免全盘遍历
        for p in glob.glob(f"{drive}:/*/resources/app.asar"):
            cand = Path(p).parent.parent
            if (cand / "Grim Tools.exe").exists():
                return cand
    return None


def asar_path() -> Optional[Path]:
    d = gt_dir()
    if d is None:
        return None
    p = d / "resources" / "app.asar"
    return p if p.exists() else None


# ----------------------------------------------------------------- 存档
_SAVE_DIR_MEMO: dict = {}


def save_dir():
    """角色存档目录的【父目录】（其下应有 main/<角色>/player.gdc）

    返回 (Path, kind)；kind ∈ {'env','steam-cloud','local',None}

    ★ 缓存（2026-09-20）：本函数含 `glob` 扫描（Steam 根目录 + userdata 通配），
      实测占单次 DPS 评估的 **7.4%**（`glob` / `stat` / `isfile` 合计）——
      而优化器每次评估都会调它一次（`load_char` → `find_save_dir`）。
      键 = `GD_SAVE` 的当前值：`save_dir_override` 正是靠改这个环境变量实现，
      带上它才不会把 override 的结果缓存错。
      **只缓存成功结果** —— 目录尚未创建时继续每次重探，避免把「暂时找不到」钉死。
    """
    _key = os.environ.get("GD_SAVE")
    _hit = _SAVE_DIR_MEMO.get(_key)
    if _hit is not None:
        return _hit
    _res = None
    if _key and (Path(_key) / "main").is_dir():
        _res = (Path(_key), "env")
    if _res is None:
        for root in _STEAM_ROOTS:
            for pat in ("userdata/*/%s/remote/save" % STEAM_APPID,
                        "steamapps/userdata/*/%s/remote/save" % STEAM_APPID):
                for p in glob.glob(os.path.join(root, *pat.split("/"))):
                    if (Path(p) / "main").is_dir():
                        _res = (Path(p), "steam-cloud")
                        break
                if _res:
                    break
            if _res:
                break
    if _res is None:
        for base in (HOME / "Documents", HOME / "OneDrive" / "Documents", HOME / "文档"):
            p = base / "My Games" / GAME_NAME / "save"
            if (p / "main").is_dir():
                _res = (p, "local")
                break
    if _res is None:
        return None, None
    _SAVE_DIR_MEMO[_key] = _res
    return _res


def clear_save_dir_cache():
    """清 `save_dir()` 的成功缓存（测试 / 切存档目录后手工重置用）。"""
    _SAVE_DIR_MEMO.clear()


@contextlib.contextmanager
def save_dir_override(path):
    """临时把「存档目录」指到别处（写档测试 / 从副本拟合时用）。

    为什么要它：写档工具支持 `--save-dir`（指向测试副本），但下游
    `dps.load_char` / `reqfit` 都自己走 `save_dir()` 找存档 —— 两边会**不一致**
    （实测：patch 在改副本、拟合却在读真档，于是「已满足」是假结论）。
    `save_dir()` 认 `GD_SAVE` 环境变量，这里就临时设/还原它。
    """
    old = os.environ.get('GD_SAVE')
    if path is None:
        yield
        return
    os.environ['GD_SAVE'] = str(path)
    try:
        yield
    finally:
        if old is None:
            os.environ.pop('GD_SAVE', None)
        else:
            os.environ['GD_SAVE'] = old


def config_dir() -> Optional[Path]:
    """文档里的配置目录（Settings / log）。"""
    for base in (HOME / "Documents", HOME / "OneDrive" / "Documents", HOME / "文档"):
        p = base / "My Games" / GAME_NAME
        if p.is_dir():
            return p
    return None


def game_dir() -> Optional[Path]:
    """游戏安装目录（其下应有 database/）。"""
    env = os.environ.get("GD_GAME")
    if env and (Path(env) / "database").is_dir():
        return Path(env)
    for root in _STEAM_ROOTS:
        p = Path(root) / "steamapps" / "common" / GAME_NAME
        if (p / "database").is_dir():
            return p
    for root in _GAME_ROOTS:
        for pat in (GAME_NAME, GAME_NAME + "*"):
            for p in glob.glob(os.path.join(root, pat)):
                if (Path(p) / "database").is_dir():
                    return Path(p)
    return None


def characters():
    """存档里的角色列表 {角色名: 目录}。"""
    sd, _ = save_dir()
    if sd is None:
        return {}
    main = sd / "main"
    return {d.name: d for d in main.iterdir() if d.is_dir() and (d / "player.gdc").exists()}


def game_running() -> bool:
    """游戏进程是否在跑（改档前必须为 False）。"""
    try:
        import subprocess
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq Grim Dawn.exe"],
                             capture_output=True, timeout=15,
                             creationflags=0x08000000)
        blob = (out.stdout or b"").decode("utf-8", errors="replace") \
            + (out.stderr or b"").decode("utf-8", errors="replace")
        return "Grim Dawn.exe" in blob
    except Exception:
        return False


def data_file(name: str) -> Optional[Path]:
    """skill 自带数据文件（data/ 或 data/cache/）。"""
    for p in (DATA_DIR / name, CACHE_DIR / name):
        if p.exists():
            return p
    return None


# ------------------------------------------------------------------ 数据加载
# data/ 里有几个大 JSON（record_map 1.6 MB / mastery_skills 0.4 MB /
# devotion_skills 0.1 MB / skills 11 MB）。`json.load` 解析大文件很慢，
# pickle 读同样结构要快 3~5 倍，所以按 (mtime, size) 落一份 pickle 缓存。
_JSON_CACHE: dict[str, object] = {}


def load_json(name: str, cache: bool = True):
    """读 `data/<name>`，带进程内缓存 + 磁盘 pickle 缓存。

    name 可带 `.json` 后缀。找不到返回 None。
    """
    key = name
    if key in _JSON_CACHE:
        return _JSON_CACHE[key]
    p = data_file(name)
    if p is None and not name.endswith(".json"):
        p = data_file(name + ".json")
    if p is None:
        return None

    import json
    import pickle

    obj = None
    cpath = None
    if cache:
        try:
            st = p.stat()
            tag = f"{p.name}.{int(st.st_mtime)}.{st.st_size}"
            cpath = CACHE_DIR / f"{tag}.pkl"
            if cpath.exists():
                with cpath.open("rb") as f:
                    obj = pickle.load(f)
        except Exception:
            obj = None
    if obj is None:
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
        if cache and cpath is not None:
            try:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                tmp = cpath.with_suffix(".tmp")
                with tmp.open("wb") as f:
                    pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
                tmp.replace(cpath)
                # 清掉同一数据文件的旧缓存
                for old in CACHE_DIR.glob(f"{p.name}.*.pkl"):
                    if old != cpath:
                        old.unlink(missing_ok=True)
            except Exception:
                pass
    _JSON_CACHE[key] = obj
    return obj


def python_exe() -> str:
    """本技能要求的解释器：必须是带 numpy 的托管 venv。"""
    venv = Path(r"C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe")
    return str(venv) if venv.exists() else sys.executable


def summary() -> dict:
    sd, kind = save_dir()
    return {
        "gt_dir": gt_dir(),
        "asar": asar_path(),
        "save_dir": sd,
        "save_kind": kind,
        "config_dir": config_dir(),
        "game_dir": game_dir(),
        "characters": list(characters()),
        "python": python_exe(),
    }
