"""gd —— 恐怖黎明（Grim Dawn）离线数据工具包。

地基是 Grim Tools 桌面版的离线库（app.asar），**不做网络请求、不解析 .arz、不需要 CDP**。

    from gd import DB
    db = DB.load()
    print(db.name("it2116"))          # 天之裂片咒刃
    print(db.stats())

命令行：python -m gd <子命令>    （见 python -m gd help）
"""

from .db import DB, OfflineDB                                          # noqa: F401
from .text import GTFormat, Localizer, LANGS                           # noqa: F401
from .scale import Scaler                                              # noqa: F401
from . import paths, jsobj, asar                                       # noqa: F401

__version__ = "1.1.0"

# ==================================================================== 兼容层
# 旧脚本里有**字符串形式的动态导入**（`__import__('gd_rotation')`、
# `__import__('gd_local_tip')`），正则改不到。这里装一个惰性别名查找器，
# 把旧模块名映射到新模块名，导入时才解析，零启动开销。
#
# 已废弃的（如 gd_local_tip：本地提示框覆盖 —— 官方公式已能算出真值）返回空模块，
# 调用方本来就有 try/except 兜底。
_ALIASES = {
    "gd_rotation": "gd.rotation", "gd_gear": "gd.gear", "gd_map": "gd.savemap",
    "gd_save": "gd.save.core", "gd_write": "gd.save.write", "gd_backup": "gd.save.backup",
    "gd_edit": "gd.save.patch", "gd_inv": "gd.save.inv", "gd_verify": "gd.save.verify",
    "gd_diff": "gd.save.diff", "gd_skill": "gd.save.skill",
    "gd_opt": "gd.opt", "gd_req": "gd.req", "gd_devotion": "gd.devotion",
    "gd_skillmod": "gd.skillmod", "gd_alloc": "gd.alloc", "gd_dps_check": "gd.dps",
    "gd_mana": "gd.mana", "gd_timing": "gd._timing", "gd_env": "gd._legacyenv",
    "bd_recipe": "gd.recipe", "bd_gen": "gd.gen", "gd_loop": "gd.loop",
    "gd_explain": "gd.explain", "gd_char_compare": "gd.compare",
    "gd_plan_report": "gd.planreport", "gd_dbr": "gd.dbr",
}
_OBSOLETE = {"gd_local_tip", "gd_arz", "gd_arc", "gt_fetch", "gt_page",
             "gt_extract", "gt_tooltip", "gt_scale_index", "cdp_eval", "ce_auto",
             "gd_mem"}


def _install_aliases():
    import importlib
    import importlib.abc
    import importlib.util
    import sys
    import types

    class _AliasFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
        def find_spec(self, fullname, path=None, target=None):
            if fullname in _ALIASES or fullname in _OBSOLETE:
                return importlib.util.spec_from_loader(fullname, self)
            return None

        def create_module(self, spec):
            name = spec.name
            if name in _ALIASES and _ALIASES[name]:
                mod = importlib.import_module(_ALIASES[name])
                sys.modules[name] = mod
                return mod
            m = types.ModuleType(name)          # 已废弃：返回空模块
            m.__doc__ = ("已废弃（新架构不再需要本地提示框覆盖，官方公式已能算出真值），"
                         "仅为兼容旧脚本的字符串导入而存在。")

            def _noop(*_a, **_k):
                return None

            def _any(_attr):
                # 任何属性都返回一个 no-op 可调用对象，让旧调用点安全降级
                return _noop

            m.__getattr__ = _any
            return m

        def exec_module(self, module):
            return None

    if not any(isinstance(f, _AliasFinder) for f in sys.meta_path):
        sys.meta_path.append(_AliasFinder())


_install_aliases()

__all__ = ["DB", "OfflineDB", "GTFormat", "Localizer", "Scaler",
           "paths", "jsobj", "asar", "LANGS", "__version__"]
