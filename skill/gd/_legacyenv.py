"""旧 `gd_env` 的兼容层 —— 把旧脚本里的 `ENV.*` 调用接到 `gd.paths`。

旧脚本（`gd_verify` / `gd_inv` / `gd_map` / `gd_build`）会调：
    ENV.find_save_dir()   -> (路径, kind)
    ENV.data_file(*parts) -> 数据文件路径
    ENV.find_arz()        -> [(标签, 路径)]
    ENV.summary()         -> dict

新架构下数据全部来自离线库，`.arz` 不再需要；
但 `find_arz()` 仍保留（返回真实路径），供需要重新生成记录桥表时探测用。
"""

from __future__ import annotations

from . import paths

GAME_NAME = paths.GAME_NAME
STEAM_APPID = paths.STEAM_APPID


def find_save_dir():
    return paths.save_dir()


def find_config_dir():
    return paths.config_dir()


def find_game_dir():
    return paths.game_dir()


def find_arz():
    """按加载优先级返回全部 .arz 文件 [(标签, 路径)]（仅探测，不再解析）。"""
    gd = paths.game_dir()
    if gd is None:
        return []
    out = []
    for tag, rel in (("base", "database/database.arz"),
                     ("gdx1", "gdx1/database/GDX1.arz"),
                     ("gdx2", "gdx2/database/GDX2.arz"),
                     ("gdx3", "gdx3/database/GDX3.arz")):
        p = gd / rel
        if p.exists():
            out.append((tag, str(p)))
    return out


def find_resource(name):
    gd = paths.game_dir()
    if gd is None:
        return None
    p = gd / "resources" / name
    return p if p.exists() else None


def find_archive_tool():
    gd = paths.game_dir()
    if gd is None:
        return None
    p = gd / "ArchiveTool.exe"
    return p if p.exists() else None


def data_file(*parts):
    """数据文件路径（data/ 或 data/cache/）。"""
    if not parts:
        return None
    name = "/".join(parts)
    p = paths.data_file(name)
    return str(p) if p else None


def find_data_dir():
    return str(paths.DATA_DIR)


def python_exe():
    return paths.python_exe()


def summary():
    s = paths.summary()
    return {
        "save_dir": str(s["save_dir"]) if s["save_dir"] else None,
        "save_kind": s["save_kind"],
        "config_dir": str(s["config_dir"]) if s["config_dir"] else None,
        "game_dir": str(s["game_dir"]) if s["game_dir"] else None,
        "arz_files": find_arz(),
        "gt_dir": str(s["gt_dir"]) if s["gt_dir"] else None,
        # 旧脚本会读这两个键（数据缓存 / 中文字典）；新架构下数据在技能的 data/
        "data_dir": str(paths.DATA_DIR),
        "text_dir": str(paths.DATA_DIR),
        "archive_tool": str(find_archive_tool()) if find_archive_tool() else None,
        "python": paths.python_exe(),
    }


def main():
    s = summary()
    print("存档目录:", s["save_dir"], "[%s]" % s["save_kind"])
    print("游戏目录:", s["game_dir"])
    print("GT 安装 :", s["gt_dir"])
    print("数据库  :", s["arz_files"] or "（未找到，新架构不依赖）")
    return 0
