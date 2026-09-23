"""把归档里的旧脚本搬进 `gd/` 包（可复跑、可审计）。

旧技能已封存在 `~/.workbuddy/skill_archive/grim-dawn-save/`。
本脚本做的是**机械搬运 + 只补接缝**：保留已实测校准的算法，
只改「模块路径 / 环境探测 / 写死的路径」这三类接缝。

    python tools/migrate_from_archive.py [归档目录]

映射：
    gd_save.py    -> gd/save/core.py      （存档解析核心，含加密/解密）
    gd_write.py   -> gd/save/write.py     （变长写入）
    gd_edit.py    -> gd/save/patch.py     （定长补丁）
    gd_inv.py     -> gd/save/inv.py       （背包增删）
    gd_backup.py  -> gd/save/backup.py    （备份/校验/恢复）
    gd_verify.py  -> gd/save/verify.py    （体检）
    gd_diff.py    -> gd/save/diff.py      （逐字段 diff）
    gd_map.py     -> gd/savemap.py        （GT id <-> 记录路径）
    gt_data/arz_{pool,by_stem,family}.pkl, record_hash.json -> data/
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
import sys

SKILL = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_ARCHIVE = pathlib.Path(
    r"C:\Users\Administrator\.workbuddy\skill_archive\grim-dawn-save")

# (源文件, 目标, 是否为 save 子包)
FILES = [
    # ---- 存档层 ----
    ("gd_save.py", "gd/save/core.py", True),
    ("gd_write.py", "gd/save/write.py", True),
    ("gd_edit.py", "gd/save/patch.py", True),
    ("gd_inv.py", "gd/save/inv.py", True),
    ("gd_backup.py", "gd/save/backup.py", True),
    ("gd_verify.py", "gd/save/verify.py", True),
    ("gd_diff.py", "gd/save/diff.py", True),
    ("gd_skill.py", "gd/save/skill.py", True),
    # ---- 记录桥 ----
    ("gd_map.py", "gd/savemap.py", False),
    # ---- 数据 / 计算层 ----
    ("gd_gear.py", "gd/gear.py", False),
    ("gd_req.py", "gd/req.py", False),
    ("gd_skillmod.py", "gd/skillmod.py", False),
    ("gd_alloc.py", "gd/alloc.py", False),
    ("gd_devotion.py", "gd/devotion.py", False),
    ("gd_rotation.py", "gd/rotation.py", False),
    ("gd_dps_check.py", "gd/dps.py", False),
    ("gd_opt.py", "gd/opt.py", False),
    ("bd_recipe.py", "gd/recipe.py", False),
    ("bd_gen.py", "gd/gen.py", False),
    ("gd_loop.py", "gd/loop.py", False),
    ("gd_explain.py", "gd/explain.py", False),
    ("gd_mana.py", "gd/mana.py", False),
    ("gd_char_compare.py", "gd/compare.py", False),
    ("gd_plan_report.py", "gd/planreport.py", False),
    # ---- 落档（方案 -> 存档）----
    ("gd_build.py", "gd/build.py", False),
]

DATA = ["gt_data/arz_pool.pkl", "gt_data/arz_by_stem.pkl", "gt_data/arz_family.pkl",
        "record_hash.json",
        # 计算层用到的数据（旧技能已从 .arz 抽出，这里随技能发布）
        "gt_data/skills.json", "gt_data/e_skills.json", "gt_data/archetypes.json",
        "gt_data/devotions.json", "gt_data/devotion_tree.json",
        "gt_data/level_table.json", "gt_data/item_sets.json",
        "gt_data/item_skills.json", "gt_data/scale_index.json", "gt_data/f64_gt.json"]

# gd_timing 的 try/except 前导 -> 一个空 TM
RE_TIMING_BLOCK = re.compile(
    r"\ntry:\n\s+import gd_timing as TM\n\s+TM\.auto\(\)\nexcept ImportError:"
    r"[^\n]*\n\s+TM = None\n", re.M)
RE_TIMING_PLAIN = re.compile(r"^import gd_timing as TM[^\n]*\n", re.M)
RE_HERE_BLOCK = re.compile(
    r"^HERE = os\.path\.dirname\(os\.path\.abspath\(__file__\)\)\n"
    r"sys\.path\.insert\(0, HERE\)\n", re.M)
RE_SYSPATH = re.compile(r"^sys\.path\.insert\(0, os\.path\.dirname"
                        r"\(os\.path\.abspath\(__file__\)\)\)\n", re.M)

# 旧模块名 -> 新相对导入
REL = {
    "gd_save": "core", "gd_write": "write", "gd_backup": "backup",
    "gd_edit": "patch", "gd_inv": "inv", "gd_verify": "verify", "gd_diff": "diff",
    "gd_map": "savemap", "gd_gear": "gear",
    "gd_opt": "opt", "gd_req": "req", "gd_devotion": "devotion",
    # gd_dbr 是已废弃的 .arz 字段读取层 -> gd/dbr.py 兼容 shim
    "gd_dbr": "dbr",
    "gd_skillmod": "skillmod", "gd_rotation": "rotation",
    "gd_build": "build",
}
# 这些旧模块在新架构里搬进了 `gd/save/` 子包 —— gd/ 顶层的文件引用它们时
# 必须写成 `from .save import X`，写成 `from . import X` 会找不到。
SAVE_MODULES = {"gd_save", "gd_write", "gd_backup", "gd_edit", "gd_inv",
                "gd_verify", "gd_diff", "gd_skill"}
# 这些旧模块在新架构里**不存在**，引用处直接去掉该行（调用点另有兜底）
REL_DROP = ("gd_arz", "gd_timing", "gd_env")


def patch(text: str, in_save: bool) -> str:
    # ★ 两种前缀要分清：
    #   up_root    —— 引用 `gd/` 下的模块（_legacyenv / _timing / paths / gear…）
    #   up_sibling —— 引用**同目录**的模块（save 子包内部互相引用、gd/ 顶层互相引用）
    up_root = "from .. import" if in_save else "from . import"
    up = "from . import"
    # ★ 归档里的源码是 CRLF 行尾：先归一化，否则下面带 `$` 锚点的正则全部失效
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # 1) 埋点：旧 `gd_timing` -> `gd/_timing.py`（no-op shim）
    text = RE_TIMING_BLOCK.sub(f"\n{up_root} _timing as TM\n", text)
    text = RE_TIMING_PLAIN.sub(f"{up_root} _timing as TM\n", text)
    text = re.sub(r"^TM\.auto\(\)\n", "", text, flags=re.M)

    # 2) sys.path 前导
    text = RE_HERE_BLOCK.sub("", text)
    text = RE_SYSPATH.sub("", text)

    # 3) 旧模块 -> 新相对导入（注意行尾可能有 `# noqa` 之类的注释；
    #     ★ 也要处理**函数体内的局部 import**，所以正则带前导空白）
    text = re.sub(r"^([ \t]*)import gd_env as ([A-Za-z_]+)\b.*$",
                  rf"\1{up_root} _legacyenv as \2", text, flags=re.M)
    for old, new in REL.items():
        tgt = f"{up} {new} as \\2"
        if not in_save and old in SAVE_MODULES:
            tgt = f"from .save import {new} as \\2"
        tgt_plain = tgt.replace(" as \\2", "")
        text = re.sub(rf"^([ \t]*)import {old} as ([A-Za-z_]+)\b.*$",
                      rf"\1{tgt}", text, flags=re.M)
        text = re.sub(rf"^([ \t]*)import {old}\b[^\n]*$",
                      rf"\1{tgt_plain}", text, flags=re.M)
    # ★ 已不存在的模块（gd_arz / gd_lint）整体删行
    for old in REL_DROP:
        text = re.sub(rf"^[ \t]*import {old}\b[^\n]*$", "", text, flags=re.M)
    text = re.sub(r"^[ \t]*from (gd_arz|gd_lint)\b[^\n]*$", "", text, flags=re.M)

    # 4) gd_map 的 gt_items() 改为接离线库
    if "def gt_items()" in text:
        text = re.sub(r"def gt_items\(\):.*?\n    return _ITEMS\n",
                      _GT_ITEMS_NEW, text, flags=re.S)

    # 5) gd_backup 里写死的绝对路径 -> 由 paths 探测
    text = text.replace(
        "LIVE_SAVE = r'C:\\Program Files (x86)\\Steam\\userdata\\1016762644\\219990'\n"
        "LIVE_DOC = r'C:\\Users\\Administrator\\Documents\\My Games\\Grim Dawn'\n"
        "ARCHIVES = r'E:\\xz\\Archives'\n"
        "PAIRS = ((LIVE_SAVE, 'Steam云存档_219990'), (LIVE_DOC, '我的文档_MyGames_GrimDawn'))\n",
        _BACKUP_PATHS)

    # 6) gd_map 的缓存/加载：改为「随技能发布的静态数据」，运行时不再碰 .arz
    if "def by_stem()" in text:
        text = text.replace("from . import _legacyenv as ENV\n",
                            "from . import _legacyenv as ENV\nfrom . import paths as _PATHS\n", 1)
        text = text.replace("_CACHE_DIR = os.path.join(HERE, 'gt_data')\n",
                            "_CACHE_DIR = str(_PATHS.DATA_DIR)\n")
        text = text.replace("_NOCACHE = bool(os.environ.get('GD_NOCACHE'))\n",
                            "_NOCACHE = bool(os.environ.get('GD_NOCACHE'))\n"
                            "_VERBOSE = bool(os.environ.get('GD_VERBOSE'))\n"
                            "_POOL_ORDER = None      # 记录名（按库内顺序）\n")
        # ★ 用 lambda 做替换：re.sub 会把替换串里的反斜杠当转义处理，
        #   直接把含 `\n` 的代码块当替换串会把它变成真换行、语法崩掉。
        text = re.sub(r"def _disk_cache\(name, build\):.*?\n    return data\n",
                      lambda m: _DISK_CACHE_NEW, text, flags=re.S)
        text = re.sub(r"def arz_objects\(\):.*?\n    return _ARZ\n",
                      lambda m: _ARZ_OBJECTS_NEW, text, flags=re.S)
        text = re.sub(r"def _affix_files\(kind\):.*?\n    return out\n",
                      lambda m: _AFFIX_FILES_NEW, text, flags=re.S)
        if "\nimport json\n" not in text:
            text = text.replace("\nimport os\n", "\nimport json\nimport os\n", 1)

    # 7) gd_gear：物品库与语言包改为直接读离线库
    if "def load_items(path=ITEMDB):" in text:
        text = re.sub(r"def load_items\(path=ITEMDB\):.*?\n    return out\n",
                      lambda m: _GEAR_ITEMS_NEW, text, flags=re.S)
        text = re.sub(r"def load_tags\(\):.*?\n    return tab\n",
                      lambda m: _GEAR_TAGS_NEW, text, flags=re.S)
        if "_ITEMS_CACHE = None" not in text:
            text = re.sub(r"^_CACHE_VER = ", _ITEMS_CACHE_INIT + "_CACHE_VER = ",
                          text, count=1, flags=re.M)
            text = re.sub(r"^_CACHE_VER = ", _TAGS_CACHE_INIT + "_CACHE_VER = ",
                          text, count=1, flags=re.M)
    # 8) 兜底：旧代码里的 `HERE`（数据目录）与无别名的 `gd_gear`
    if re.search(r"\bHERE\b", text) and not re.search(r"^HERE\s*=", text, re.M):
        text = text.replace("os.path.join(HERE, 'gt_data', ", "os.path.join(HERE, ")
        text = text.replace('os.path.join(HERE, "gt_data", ', "os.path.join(HERE, ")
        text = text.replace("os.path.join(HERE, 'gt_data')", "HERE")
        text = re.sub(r"os\.path\.join\(HERE,\s*'_cache'\)", "_CACHE_ROOT", text)
        text = re.sub(r"os\.path\.join\(HERE,\s*'plans'\)", "_PLANS", text)
        text = _inject_after_imports(text, _here_defs(up_root))
    if re.search(r"\bgd_gear\.", text) and "gear as gd_gear" not in text:
        text = _inject_after_imports(text, f"{up} gear as gd_gear")

    # 9) 性能：`resolve()` / `resolve_affix()` 先查权威桥表（O(1)）
    #    否则每次调用都走旧规则兜底 `_family_by_prefix` ——
    #    实测「为建反查表遍历 8612 件物品」要烧 7.5 s。
    if "def resolve(gt_obj, verbose=False):" in text:
        text = text.replace("def resolve(gt_obj, verbose=False):", _RESOLVE_NEW, 1)
    if "def resolve_affix(kind, gt_id, slot=None):" in text:
        text = text.replace("def resolve_affix(kind, gt_id, slot=None):",
                            _RESOLVE_AFFIX_NEW, 1)

    # 10) 性能：dps 建「记录 -> GT id」反查表时别遍历全库跑规则
    #     （原来是 for 8612 件物品逐个 resolve()，实测 7.5 s）
    if "for _gid, _o in GG.load_items().items():" in text:
        text = text.replace(_REV_OLD, _REV_NEW, 1)

    # 11) gd_opt：把写死的「当前穿着」表改成可由 `GD_CUR_JSON` 覆盖
    #     （旧表是 52 级时代的快照；「读取现在的存档出方案」必须用真实现状件兜底）
    if "CUR = {\n" in text and "GD_CUR_JSON" not in text:
        text = text.replace(_CUR_ANCHOR, _CUR_ENV_BLOCK + _CUR_ANCHOR, 1)

    # 12) gd_build：`_picksrc()` 里写死的归档路径 -> 由 paths/backup 探测
    if "E:\\xz\\Archives" in text:
        text = text.replace(_PICKSRC_OLD, _PICKSRC_NEW, 1)

    # 13) 性能：两个「每次 DPS 评估都 json.load 大文件」的入口 -> paths.load_json
    #     （进程内 memo + 磁盘 pickle 缓存）。实测单次评估 0.33 s → ~0.13 s。
    if "def load_skills_zh():" in text and "_P.load_json('skills.json')" not in text:
        text = re.sub(r"def load_skills_zh\(\):.*?\n    return json\.load"
                      r"\(open\(p, encoding='utf-8'\)\)\n",
                      lambda m: _SKILLS_ZH_NEW, text, flags=re.S)
    if "def _load(name, default):" in text and "def _load(name, default):\n    from . import paths" not in text:
        text = re.sub(r"def _load\(name, default\):.*?\n    return json\.load"
                      r"\(open\(p, encoding='utf-8'\)\)\n",
                      lambda m: _ROT_LOAD_NEW, text, flags=re.S)

    # 14) 静音：dps 的「[武器套]」自检打印在批量评估里会刷屏（GD_QUIET=1 关闭）
    if "[武器套]" in text and "GD_QUIET" not in text:
        text = text.replace(
            "    try:\n        for _t in ('alt1', 'alt2'):",
            "    try:\n        for _t in (() if os.environ.get('GD_QUIET') else ('alt1', 'alt2')):",
            1)

    # 15) gd_opt.auto_pool：`GD_POOL_DMG_TOPN=N` 额外并入「按输出代理排序的前 N 件」，
    #     并放开 `sc<=0` 那道**零抗性直接丢弃**的闸门（否则高伤害件根本进不了池）
    if "for s, lst in got.items():" in text and "GD_POOL_DMG_TOPN" not in text:
        text = text.replace(_AUTOPOOL_HEAD_OLD, _AUTOPOOL_HEAD_NEW, 1)
        text = text.replace(_AUTOPOOL_FILT_OLD, _AUTOPOOL_FILT_NEW, 1)
        text = text.replace(_AUTOPOOL_OLD, _AUTOPOOL_NEW, 1)

    # 16) gd_dps.load_char：`gear_override` 支持**武器槽**（主手/副手）
    #     默认实现只换 12 个装备槽、武器不动；极限模式要连武器一起what-if。
    if "for tag in ([_set_tag] if _set_tag else []):" in text and "武器也被 override" not in text:
        text = text.replace(_WPN_OV_OLD, _WPN_OV_NEW, 1)

    # 17) 性能：dps.load_char 里每次评估都 `json.load(skills.json)`（11 MB）
    #     实测这是单次 DPS 评估 0.15 s 里的 **0.09 s（60%）** → 改成缓存 + 进程内 memo。
    if "skills.json')," in text and "_tag2rec_map" not in text:
        text = text.replace(_TAG2REC_OLD, _TAG2REC_NEW, 1)
        text = text.replace("def load_char(name, weapon_set='', local=True, gear_override=None):",
                            _TAG2REC_FN + "def load_char(name, weapon_set='', local=True, "
                                          "gear_override=None):", 1)

    # 18) gd_plan_report 的 `zh()`：名称字段要**先 `a` 再 `d`**
    #     （`d` 常常是**描述**标签：`tagWeaponSwordC016Desc` —— 先读 `d` 会把
    #      副手装备名显示成描述文本「"刀刃怎么砍也不会钝。"」）
    if "for key in ('d', 'n'):" in text:
        text = text.replace("for key in ('d', 'n'):", "for key in ('a', 'd', 'n'):", 1)

    # 19) ★ 确定性修正 + 性能：gd_rotation
    #     ① `skill_children` 记忆化（父子表只依赖静态 skills.json）
    #     ② `apply_conversions` 改**两阶段**（修「既是转化目标又是转化源」的顺序依赖）
    #     ③ `base100` 的 set 迭代定序  ④ `dmg_bonus` 的 set 迭代定序
    if "def skill_group(rec, levels, skills, db, children, _seen=None):" in text \
            and "_CHILDREN_MEMO" not in text:
        text = text.replace(
            "def skill_group(rec, levels, skills, db, children, _seen=None):",
            _CHILDREN_WRAP + "def skill_group(rec, levels, skills, db, children, _seen=None):",
            1)
    if "def apply_conversions(base, convs):" in text and "两阶段" not in text:
        text = re.sub(r"def apply_conversions\(base, convs\):.*?\n    return base\n",
                      lambda m: _APPLY_CONV_NEW, text, flags=re.S)
    if "for t in set(list(wflat) + list(wdot) + list(aura_fl) + list(aura_dt)" in text:
        text = re.sub(
            r"    for t in set\(list\(wflat\) \+ list\(wdot\) \+ list\(aura_fl\) "
            r"\+ list\(aura_dt\)\n\s*\+ list\(add_fl\) \+ list\(add_dt\)\):",
            lambda m: _B100_NEW, text)
    if "for k in mods:" in text and "★ 定序：set 迭代序随 PYTHONHASHSEED" not in text:
        text = text.replace("        for k in mods:\n", _DMGBONUS_NEW, 1)

    # 20) 性能：dps.load_char 每次评估都 `S.parse(player.gdc)`（实测占评估 24 %）
    #     → 只读缓存（按 路径+mtime+大小）；带参调用（record=True 等写盘用途）不受影响。
    #     ★ 只对**含 load_char 的那个文件**生效（否则会误改 planreport 等同名调用点，
    #       那边没有 _parse_cached 定义 → NameError）。
    if "def load_char(name, weapon_set='', local=True, gear_override=None):" in text \
            and "_parse_cached" not in text:
        text = text.replace("    d = S.parse(p)\n", "    d = _parse_cached(p)\n", 1)
        text = text.replace("def load_char(name, weapon_set='', local=True, gear_override=None):",
                            _PARSE_CACHE_FN + "def load_char(name, weapon_set='', local=True, "
                                              "gear_override=None):", 1)

    # 21) gd_opt：模块级「启动搜索 + 打印 + 落盘」整段挪进 `if __name__ == '__main__':`
    #     （`import gd.opt` 以前会顺带跑一整轮搜索，实测 **6.9 s**；
    #      对 tune_dps / autobuild / explain / planreport 都是纯浪费，
    #      更是多进程每个 worker 的固定开销）
    if "print('=== 约束：紫装" in text and "只有**直接运行**" not in text:
        _i = text.index("print('=== 约束：紫装")
        _head, _tail = text[:_i], text[_i:]
        _tail = "\n".join(("    " + _l) if _l.strip() else _l
                          for _l in _tail.split("\n"))
        text = _head + _OPT_MAIN_GUARD + _tail

    # 22) 性能：`prune_cands` 记忆化 + 落盘缓存
    #     单槽 30 470 条候选跑一次 skyline 要 **6.1 s**，而结果只取决于
    #     (每候选的 抗性/输出/技能 向量 + gid, NEED, TYPES) —— 一次搜索里完全不变。
    if "def score(rt, ot, sk, gr, ep, lg):" in text and "_PRUNE_MEMO" not in text:
        text = text.replace("def score(rt, ot, sk, gr, ep, lg):",
                            _PRUNE_WRAP + "def score(rt, ot, sk, gr, ep, lg):", 1)

    # 23) gd_opt：双持副手候选池修复 + 空槽防御（事故说明见上方常量区）
    #     症状：`gd auto <角色> --with-weapon` / `--extreme` 抛
    #       ValueError: zero-size array to reduction operation maximum which has no identity
    #       （栈顶在 gd/opt.py 的 beam_search_np → SUFM[_si] = ... + _cR.max(axis=0)）
    #     `gd/opt.py` 只有它才含 beam_search_np，用它做文件判别。
    if "def beam_search_np(" in text and "★ 双持：武器必须" not in text:
        if _WPN_DUAL_OLD in text:
            text = text.replace(_WPN_DUAL_OLD, _WPN_DUAL_NEW, 1)
        if _CHOICES_BASES_OLD in text:
            text = text.replace(_CHOICES_BASES_OLD, _CHOICES_BASES_NEW, 1)
        if _SUFM_OLD in text:
            text = text.replace(_SUFM_OLD, _SUFM_NEW, 1)
        if _RUNSEARCH_OLD in text:
            text = text.replace(_RUNSEARCH_OLD, _RUNSEARCH_NEW, 1)
    return text


_TAG2REC_OLD = """    try:
        _skills_all = json.load(io.open(os.path.join(HERE, 'skills.json'),
                                        encoding='utf-8'))
    except Exception:
        _skills_all = {}
    _tag2rec = {}
    for _rec, _d in _skills_all.items():
        _t = (_d.get('tag') or '').strip()
        if _t and (_t not in _tag2rec or ('/playerclass' in _rec
                                          and '/playerclass' not in _tag2rec[_t])):
            _tag2rec[_t] = _rec"""

_TAG2REC_NEW = """    _tag2rec = _tag2rec_map()"""

_TAG2REC_FN = '''_TAG2REC = None


def _tag2rec_map():
    """技能 tag → 记录名（进程内只建一次）。

    ★ 为什么必须缓存：旧实现**每次 `load_char` 都 `json.load(skills.json)`（11 MB）** ——
      实测这是单次 DPS 评估 0.15 s 里的 0.09 s（60%）。局部搜索要评估上千次，
      不堵这个洞，「极限模式」就是几十分钟起步。
    """
    global _TAG2REC
    if _TAG2REC is None:
        from . import paths as _P
        _all = _P.load_json('skills.json') or {}
        out = {}
        for _rec, _d in _all.items():
            _t = ((_d or {}).get('tag') or '').strip()
            if _t and (_t not in out or ('/playerclass' in _rec
                                         and '/playerclass' not in out[_t])):
                out[_t] = _rec
        _TAG2REC = out
    return _TAG2REC


'''


_WPN_OV_OLD = """    for tag in ([_set_tag] if _set_tag else []):
        # ★ 不再看 `attached`（实测它与「哪套在用」不一致）：有 basename 就是那把武器"""

_WPN_OV_NEW = """    # ★ 武器也被 override（极限模式 `--with-weapon`）：默认只换 12 个装备槽、武器不动，
    #   这里补上武器替换 —— 否则优化器会「选武器但按旧武器算伤害」，结果全是错的。
    _ov_w = None
    if gear_override is not None and (gear_override.get('主手') or gear_override.get('副手')):
        _ov_w = [gear_override.get('主手') or [], gear_override.get('副手') or []]
    if _ov_w is not None:
        _ws_parts = []
        for _j, _parts in enumerate(_ov_w):
            _parts = [x for x in (_parts or []) if x]
            if not _parts:
                continue
            all_parts += _parts
            folded[('主手', '副手')[_j]] = fold(_parts, no_scale=False)
            _ws_parts.append(_parts)
        if _ws_parts:
            wpn_gids = [p[0] for p in _ws_parts]
            wf = db.fields(_ws_parts[0][0]) or {}
            wpn_speed = ((wf.get('characterBaseAttackSpeedTag') or [''])[0],
                         (wf.get('characterBaseAttackSpeed') or [None])[0])
            try:
                from . import skillmod as _SM
                wpn_tpl = _SM.weapon_template(db, _ws_parts[0][0])
            except Exception:
                wpn_tpl = ''
    for tag in ([_set_tag] if _set_tag else []):
        if _ov_w is not None:
            break
        # ★ 不再看 `attached`（实测它与「哪套在用」不一致）：有 basename 就是那把武器"""


_AUTOPOOL_HEAD_OLD = """    got = {s: [] for s in SLOTS}
    for gid, o in _IT.items():"""

_AUTOPOOL_HEAD_NEW = """    got = {s: [] for s in SLOTS}
    # ★ `GD_POOL_DMG_TOPN=N`：把「按**输出代理**排的前 N 件」也纳入候选池。
    #   默认池按**抗性**排序，且下面 `sc<=0` 会**直接丢弃零抗性**装备 ——
    #   高伤害件全被挡在门外，等于人为封死「满抗前提下拉满伤害」的搜索空间。
    try:
        _dtop = int(_os.environ.get('GD_POOL_DMG_TOPN', '0') or 0)
    except Exception:
        _dtop = 0

    def _dmg_score(_g):
        _o = off_of(_g)
        return sum(_o.get(_k, 0.0) * W_DMG.get(_k, 0.0) for _k in FKEYS)

    for gid, o in _IT.items():"""

_AUTOPOOL_FILT_OLD = """        sc = _pool_score(gid)
        if sc <= 0:
            continue"""

_AUTOPOOL_FILT_NEW = """        sc = _pool_score(gid)
        if sc <= 0 and (_dtop <= 0 or _dmg_score(gid) <= 0):
            continue"""

_AUTOPOOL_OLD = """    out = {}
    for s, lst in got.items():
        lst.sort(key=lambda x: (-x[0], x[1]))
        pick = [g for _, g in lst[:topn]]
        rares = [g for _, g in lst if (_IT.get(g) or {}).get('f') == 'Rare']
        for g in rares[:max(4, topn // 2)]:
            if g not in pick:
                pick.append(g)
        out[s] = pick
    return out"""

_AUTOPOOL_NEW = """    out = {}
    for s, lst in got.items():
        lst.sort(key=lambda x: (-x[0], x[1]))
        pick = [g for _, g in lst[:topn]]
        rares = [g for _, g in lst if (_IT.get(g) or {}).get('f') == 'Rare']
        for g in rares[:max(4, topn // 2)]:
            if g not in pick:
                pick.append(g)
        if _dtop:
            _dl = sorted(lst, key=lambda x: (-_dmg_score(x[1]), x[1]))
            for g in [g for _, g in _dl[:_dtop]]:
                if g not in pick:
                    pick.append(g)
        out[s] = pick
    return out"""


_SKILLS_ZH_NEW = '''def load_skills_zh():
    """技能记录 -> 中文名。走 `paths.load_json`（进程内 memo + pickle 缓存）。

    ★ 旧实现每次评估都 `json.load(skills.json)`（11 MB）→ 单次 DPS 评估白花 ~0.1 s；
      局部搜索要评估上千次，这个洞不堵就快不起来。
    """
    from . import paths as _P
    return _P.load_json('skills.json') or {}
'''

_ROT_LOAD_NEW = '''def _load(name, default):
    """读 `data/<name>`。走 `paths.load_json`（进程内 memo + pickle 缓存）。"""
    from . import paths as _P
    v = _P.load_json(name)
    return default if v is None else v
'''


_PICKSRC_OLD = """    paths += glob.glob(os.path.join(r'E:\\xz\\Archives', '*', 'Steam云存档_219990',
                                    'remote', 'save', 'main', '*', 'player.gdc'))"""
_PICKSRC_NEW = """    try:
        from .save import backup as _B
        for _sub, _leaf in (('Steam云存档_%s' % _PATHS.STEAM_APPID,
                             ('remote', 'save', 'main', '*', 'player.gdc')),
                            ('我的文档_MyGames_GrimDawn',
                             ('save', 'main', '*', 'player.gdc'))):
            paths += glob.glob(os.path.join(_B.ARCHIVES, '*', _sub, *_leaf))
    except Exception:
        pass"""


_CUR_ANCHOR = ("# ★ v3 解绑：`GD_NO_CUR=1` → **不把「当前穿着」注入候选池**。")
_CUR_ENV_BLOCK = '''# ★ 存档覆盖：`GD_CUR_JSON='{"头部":["it17247"],...}'` 用**当前存档**的装备
#   替换上面写死的表（由 tools/save_plan.py 生成）。
_CUR_ENV = os.environ.get('GD_CUR_JSON', '')
if _CUR_ENV:
    try:
        CUR = {k: [g for g in v if g] for k, v in json.loads(_CUR_ENV).items()}
    except Exception as _e:
        print('  ⚠ GD_CUR_JSON 解析失败: %s' % _e)

'''


_REV_OLD = '''    _rev = {}
    for _gid, _o in GG.load_items().items():
        try:
            _r, _w = MM.resolve(_o)
        except Exception:
            _r = None
        if _r and _r not in _rev:
            _rev[_r] = _gid'''

_REV_NEW = '''    # ★ 直接用权威桥表（O(1)），别再遍历全库跑规则推导：
    #   实测「for 8612 件物品逐个 resolve()」要烧 7.5 s，而桥表读盘只要 0.1 s。
    from .save import items as _SI
    _br = _SI.bridge()
    _rev = dict(_br.rev)
    for _r, _g in _br.affix_rev.items():
        _rev.setdefault(_r, _g)'''



_RESOLVE_NEW = '''_BRIDGE = None


def _bridge():
    """权威桥表（`data/record_map.json`，由 tools/build_exact_map.py 从游戏 .dbr 生成）。

    ★ 有它就别再走规则推导 —— 规则曾漏掉 `c204_sword2h.dbr`
      （记录名 c204，itemNameTag 却是 tagGDX2WeaponMelee2hC202）。
    """
    global _BRIDGE
    if _BRIDGE is None:
        try:
            with open(os.path.join(_CACHE_DIR, 'record_map.json'), encoding='utf-8') as f:
                _BRIDGE = json.load(f)
        except Exception:
            _BRIDGE = {}
    return _BRIDGE


_RESOLVE_MEMO = {}

_GID_BY_OBJ = {}


def _gid_of_obj(o):
    """对象 → gid（按身份索引 `gt_items()`）。

    ★ 为什么必须补：权威快速路径原本只在传入**字符串 gid** 时生效，
      而 `gd_build` 传的是 `gt_items().get(gid)` 取出的**对象** →
      快速路径被跳过、退回规则推导，把 `it15810`（仲冬之忆）解析成
      `c303_necklace.dbr`（其权威记录是 `awakened/.../c308_necklace.dbr`），
      **静默写错了装备**。这里按对象身份反查 gid，让快速路径对两种入参都生效。
    """
    if not _GID_BY_OBJ:
        try:
            for _g, _o in gt_items().items():
                _GID_BY_OBJ[id(_o)] = _g
        except Exception:
            pass
    return _GID_BY_OBJ.get(id(o))


def resolve(gt_obj, verbose=False):
    """GT 物品对象/ID -> (记录名, 依据)。

    顺序：① 权威桥表（O(1)，来自游戏 .dbr） ② 旧规则推导（兜底）
    """
    gid = gt_obj if isinstance(gt_obj, str) else _gid_of_obj(gt_obj)
    if gid is not None and gid in _RESOLVE_MEMO:
        return _RESOLVE_MEMO[gid]
    if gid is not None:
        rec = (_bridge().get("fwd") or {}).get(gid)
        if rec:
            out = (rec, "权威映射（读取游戏 .dbr 的 itemNameTag）")
            _RESOLVE_MEMO[gid] = out
            return out
    out = _resolve_by_rules(gt_obj, verbose)
    if gid is not None:
        _RESOLVE_MEMO[gid] = out
    return out


def _resolve_by_rules(gt_obj, verbose=False):
'''

_RESOLVE_AFFIX_NEW = '''def resolve_affix(kind, gt_id, slot=None):
    """GT 词缀 ID -> (记录名, 依据)。先查权威桥表，再走规则。"""
    rec = (_bridge().get("affix_fwd") or {}).get(gt_id)
    if rec:
        return rec, "权威映射（读取游戏 .dbr）"
    return _resolve_affix_by_rules(kind, gt_id, slot)


def _resolve_affix_by_rules(kind, gt_id, slot=None):
'''


def _here_defs(up: str) -> str:
    return f'''{up} paths as _PATHS

# 旧脚本用 HERE 拼数据文件路径；新架构下数据在技能的 data/ 里
HERE = str(_PATHS.DATA_DIR)
_CACHE_ROOT = str(_PATHS.CACHE_DIR)
_PLANS = str(_PATHS.CACHE_DIR / 'plans')'''


def _inject_after_imports(text: str, block: str) -> str:
    """把一段代码插到「最后一行顶层 import/from 之后」。

    ⚠ 不能插在「第一个 def 之前」—— 旧代码里有模块级语句（如 `TAGS = gd_gear.load_tags()`）
      出现在第一个 def 之前，那样插会太晚。
    """
    lines = text.split("\n")
    idx = 0
    for i, ln in enumerate(lines[:120]):
        if re.match(r"^(import|from) \S", ln):
            idx = i + 1
    if idx == 0:
        return block + "\n" + text
    return "\n".join(lines[:idx] + ["", *block.split("\n"), ""] + lines[idx:])


_DISK_CACHE_NEW = '''def _disk_cache(name, build):
    """读/写落盘索引。

    ★ 新架构：`data/arz_<name>.pkl` 是**随技能发布的静态数据**，直接采用，
      **不再用 .arz 签名卡失效**（运行时不该依赖 .arz）。
      只有缓存整个缺失时才走 `build()`（那需要 .arz 工具链，已随旧技能封存）。
    """
    import pickle
    p = os.path.join(_CACHE_DIR, 'arz_%s.pkl' % name)
    try:
        with open(p, 'rb') as f:
            blob = pickle.load(f)
        data = blob.get('data')
        if data is not None:
            if _VERBOSE:
                cur = _arz_signature()
                if cur and cur != blob.get('sig'):
                    print('  ⚠ %s 的池缓存是按另一份 .arz 建的（游戏更新过？）'
                          '—— 结果可能对不上，必要时重建。' % os.path.basename(p))
            return data
    except Exception:
        pass
    try:
        data = build()
    except Exception as e:
        raise RuntimeError(
            "记录池索引 data/arz_%s.pkl 缺失，且无法从 .arz 重建。\\n"
            "请从归档取回该文件（skill_archive/grim-dawn-save/scripts/gt_data/）。\\n"
            "原始错误：%s" % (name, e)
        ) from e
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(p, 'wb') as f:
            pickle.dump({'sig': _arz_signature(), 'data': data}, f, protocol=4)
    except Exception:
        pass
    return data
'''

_AFFIX_FILES_NEW = '''def _pool_order():
    """全部记录名，**按原始库内顺序**（list）。

    新架构不含 .arz，改用随技能发布的 record_hash.json ——
    它的插入顺序就是 .arz 字符串池顺序，而词缀两字母族依赖这个顺序定档。
    拿不到时退回 `sorted(pool())`（字典序对零填充的文件名通常等价）。
    """
    global _POOL_ORDER
    if _POOL_ORDER is None:
        p = os.path.join(_CACHE_DIR, 'record_hash.json')
        try:
            with open(p, encoding='utf-8') as f:
                _POOL_ORDER = list(json.load(f).values())
        except Exception:
            _POOL_ORDER = sorted(pool())
    return _POOL_ORDER


def _affix_files(kind):
    """按库内顺序取出某类词缀的全部记录名"""
    if kind in _AFFIX:
        return _AFFIX[kind]
    sub = 'prefix' if kind == 'prefix' else 'suffix'
    pat = '/lootaffixes/%s/' % sub
    seen, out = set(), []
    for t in _pool_order():
        if pat in t and t.endswith('.dbr') and t not in seen:
            seen.add(t)
            out.append(t)
    _AFFIX[kind] = out
    return out
'''

_GEAR_ITEMS_NEW = '''def load_items(path=None):
    """物品库 {gt_id: {字段: 值}} —— 直接读离线数据库。

    ★ 取代旧的 itemdb.js 正则解析：离线库的解析器能拿到**全部**字段
      （含嵌套对象/数组），而旧正则只取字符串与数值。
      返回值形状保持一致（调用方只按字段名取）。
      path 参数保留仅为兼容旧签名，已忽略。
    """
    global _ITEMS_CACHE
    if _ITEMS_CACHE is not None:
        return _ITEMS_CACHE
    from . import DB
    db = DB.load()
    _ITEMS_CACHE = {}
    _ITEMS_CACHE.update(db.items)
    _ITEMS_CACHE.update(db.prefixes)
    _ITEMS_CACHE.update(db.suffixes)
    return _ITEMS_CACHE
'''
_ITEMS_CACHE_INIT = "_ITEMS_CACHE = None\n\n\n"

_GEAR_TAGS_NEW = '''def load_tags():
    """语言包 {tag: 中文} —— 直接取离线库的 l10n（13 语言，中文 16563 条）。

    ★ 取代旧实现：旧的要先解包游戏 Text_ZH.arc 再逐文件解码，
      新架构下这份数据本来就在离线库里，且更全。
    """
    global _TAGS_CACHE
    if _TAGS_CACHE is not None:
        return _TAGS_CACHE
    from . import DB
    _TAGS_CACHE = dict(DB.load().l10n.texts.get("zh") or {})
    return _TAGS_CACHE
'''
_TAGS_CACHE_INIT = "_TAGS_CACHE = None\n\n\n"

_ARZ_OBJECTS_NEW = '''def arz_objects():
    """按需加载全部 .arz（惰性 + 缓存）

    ⚠ 新架构**不再解析 .arz**（gd_arz 已随旧技能封存）。
    记录池改由随技能发布的 data/arz_*.pkl 提供（见 _disk_cache）。
    """
    global _ARZ
    if _ARZ is None:
        raise RuntimeError(
            "记录池缓存缺失，且新架构不含 .arz 解析器。\\n"
            "请从归档（skill_archive/grim-dawn-save/scripts/gt_data/）取回\\n"
            "  arz_pool.pkl / arz_by_stem.pkl / arz_family.pkl\\n"
            "放进本技能 data/ 目录。"
        )
    return _ARZ
'''

_GT_ITEMS_NEW = '''def gt_items():
    """GT 物品 + 词缀的合并视图 —— 直接读离线数据库（取代旧的 itemdb.js 解析）

    ★ 必须合并：`resolve_affix()` 会拿 `preXXXX`/`sufXXXX` 到这里查 `c` 标签，
      只给 `allItems` 会让词缀解析 100% 失败。
    """
    global _ITEMS
    if _ITEMS is None:
        try:
            from . import DB
            db = DB.load()
            _ITEMS = {}
            _ITEMS.update(db.items)
            _ITEMS.update(db.prefixes)
            _ITEMS.update(db.suffixes)
        except Exception:
            _ITEMS = {}
    return _ITEMS
'''

_BACKUP_PATHS = '''import os as _os
from .. import paths as _P

# 归档目录沿用用户约定 E:\\xz\\Archives（可用 GD_BACKUP_DIR 覆盖）。
# 旧实现同时写死了 userdata id（1016762644）与用户名，换机器就失效 —— 这里改为探测。
ARCHIVES = _os.environ.get('GD_BACKUP_DIR') or r'E:\\xz\\Archives'


def _pairs():
    """[(要备份的目录, 归档内的子目录名)]"""
    out = []
    sd, _kind = _P.save_dir()
    if sd is not None:
        out.append((str(sd.parent), 'Steam云存档_%s' % _P.STEAM_APPID))
    cd = _P.config_dir()
    if cd is not None and str(cd) not in [x[0] for x in out]:
        out.append((str(cd), '我的文档_MyGames_GrimDawn'))
    return out


PAIRS = _pairs()          # ★ 必须在这里求值：do_backup/snapshot 都直接遍历 PAIRS
LIVE_SAVE = ''
LIVE_DOC = ''
'''


# ------------------------------------------------------------------ 19/20 常量
_CHILDREN_WRAP = '''_CHILDREN_MEMO = {}
_SKILL_CHILDREN_RAW = skill_children


def skill_children(skills):                       # noqa: F811
    """★ 记忆化包装：父子表只依赖**静态** `skills.json`，不必每次评估重算。

    原始实现要遍历 12 372 条技能、做 3 339 次 `re.sub` + 3 339 次路径处理，
    占 `final_report` 的 **89 %**（0.025 s / 0.028 s）。
    `_load('skills.json')` 返回同一对象 → 按 `id` 缓存必然命中。
    实测：单次评估 0.0255 → 0.0078 s（3.3×），DPS 逐位不变。
    """
    _k = id(skills)
    _v = _CHILDREN_MEMO.get(_k)
    if _v is None:
        _v = _CHILDREN_MEMO[_k] = _SKILL_CHILDREN_RAW(skills)
    return _v


'''


_APPLY_CONV_NEW = '''def apply_conversions(base, convs):
    """把 base（{类型: [min, max]}）按转化搬迁（同一次、并发应用，不链式）。

    同一输入类型有多条时，总比例 >100% 的部分按比例分摊。
    ★ `Elemental`（元素）不是独立伤害类型 —— 按 GD 规则**均分给 火/冰/电**。

    ★★ 必须是**两阶段**：先把所有「转出」按**原始值**削掉，再统一累加「转入」。
       旧实现把 `base[t] = 原值 × (1 - tot)`（**覆盖**）与 `e[k] += lo * k`（**累加**）
       放在**同一趟**循环里 —— 当某类型**既是转化目标又是转化源**时，
       处理先后决定了「别人转进来的量」会不会被那一下覆盖式赋值**连带缩放**。
       实测后果：同一份装备在不同 `PYTHONHASHSEED` 下算出 **差 2 %** 的 DPS
       （随机抽样 6 % 的装备组合会命中）→ 并行搜索不可用、结果不可复现。
       两阶段化后结果与顺序无关，且符合「同一次、并发、不链式」的语义。
    """
    if not convs:
        return base
    src = {t: list(v) for t, v in base.items()}
    rel_of = {}
    for t in src:
        rel = [(o, p) for i, o, p in convs if i == t]
        if rel:
            rel_of[t] = rel
    # ① 转出：一律基于**原始值**削减（不受他人转入影响）
    for t, (lo, hi) in src.items():
        rel = rel_of.get(t)
        if not rel:
            continue
        tot = sum(p for _o, p in rel)
        base[t] = [lo * max(0.0, 1 - tot / 100.0), hi * max(0.0, 1 - tot / 100.0)]
    # ② 转入：同样基于**原始值**分摊；按 `t` 定序，保证浮点累加顺序确定
    for t in sorted(src):
        rel = rel_of.get(t)
        if not rel:
            continue
        lo, hi = src[t]
        tot = sum(p for _o, p in rel)
        share = 1.0 if tot <= 100 else 100.0 / tot
        for o, p in rel:
            outs = ELEMENTAL if o == 'elemental' else (o,)
            k = p / 100.0 * share / len(outs)
            for oo in outs:
                e = base.setdefault(oo, [0.0, 0.0])
                e[0] += lo * k
                e[1] += hi * k
    return base
'''


_B100_NEW = ("    for t in sorted(set(list(wflat) + list(wdot) + list(aura_fl)\n"
             "                        + list(aura_dt) + list(add_fl) + list(add_dt))):")


_DMGBONUS_NEW = ("        # ★ 定序：set 迭代序随 PYTHONHASHSEED 变，不可复现\n"
                 "        for k in sorted(mods):\n")


_PARSE_CACHE_FN = '''_PARSE_MEMO = {}


def _parse_cached(path):
    """★ 只读解析缓存。

    `load_char` 每次评估都会 `S.parse(player.gdc)` —— 实测占单次 DPS 评估的 **24 %**
    （0.0053 s / 0.0225 s）。一次搜索里存档不会变，故按 `(路径, mtime, 大小)` 缓存。
    ★ 带参调用（`record=True` 等**写盘**用途）不走缓存，行为与原来完全一致。
    ★ `load_char` 只读解析结果（不写回 `d`），共享安全。
    """
    from .save import core as _S
    try:
        key = (str(path), os.path.getmtime(path), os.path.getsize(path))
    except OSError:
        return _S.parse(path)
    v = _PARSE_MEMO.get(key)
    if v is None:
        v = _PARSE_MEMO[key] = _S.parse(path)
    return v


'''


_PRUNE_WRAP = '''_PRUNE_MEMO = {}


def _prune_key(rows, cs):
    """内容指纹：向量 + gid + 抗性需求 + 类型序。物品库/等级/名额一变就自动换 key。"""
    try:
        import hashlib
        h = hashlib.sha1()
        h.update(_np.ascontiguousarray(rows, dtype=_np.float64).tobytes())
        h.update(repr(sorted(NEED.items())).encode('utf-8'))
        h.update(','.join(TYPES).encode('utf-8'))
        for _c in cs:                       # ★ 必须含 gid：同一组向量配不同 gid 会串味
            h.update(('%s|' % (_c[0],)).encode('utf-8'))
        return h.hexdigest()[:20]
    except Exception:
        return None


def _prune_cache_path(key):
    from . import paths as _P
    return _P.CACHE_DIR / 'prune' / ('%s.pkl' % key)


def _prune_disk_get(key):
    import pickle
    try:
        p = _prune_cache_path(key)
        if p.exists():
            return pickle.loads(p.read_bytes())
    except Exception:
        pass
    return None


def _prune_disk_put(key, keep):
    import pickle
    try:
        p = _prune_cache_path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix('.tmp')
        tmp.write_bytes(pickle.dumps(keep, protocol=4))
        tmp.replace(p)
    except Exception:
        pass


def prune_cands(cs):                              # noqa: F811
    """★ 记忆化 + 落盘缓存包装（支配剪枝本身不变）。

    单槽 30 470 条候选跑一次 `_skyline` 要 **6.1 s**，而结果只取决于
    (每候选的 抗性/输出/技能 向量 + gid, NEED, TYPES) —— 一次搜索里完全不变，
    换等级/名额也只是换个 key。实测「候选池构建」6.1 s → 0.05 s。
    """
    if not _PRUNE or _np is None or len(cs) < 3:
        return cs
    rows = _np.array([list(c[1]) + list(c[2]) + [c[3]] for c in cs], _np.float64)
    key = _prune_key(rows, cs)
    if key is not None:
        keep = _PRUNE_MEMO.get(key)
        if keep is None:
            keep = _prune_disk_get(key)
        if keep is not None and len(keep) == len(cs):
            _PRUNE_MEMO[key] = keep
            return [c for c, k in zip(cs, keep) if k] or cs
    keep = _skyline(rows)
    if key is not None:
        _PRUNE_MEMO[key] = keep
        _prune_disk_put(key, keep)
    return [c for c, k in zip(cs, keep) if k] or cs


'''


_OPT_MAIN_GUARD = '''# ★ 只有**直接运行**（`python -m gd.opt` / `python gd/opt.py`）才跑搜索与输出。
#   `import gd.opt` 以前会**顺带跑一整轮搜索**（实测 6.9 s）；
#   对 `tools/tune_dps.py` / `tools/autobuild.py` / `gd/explain.py` / `gd/planreport.py`
#   这类「只要候选池与向量」的调用方是纯浪费，更是多进程每个 worker 的固定开销。
if __name__ == '__main__':
'''


# ------------------------------------------------- 23 常量：双持副手 + 空槽防御
# 旧归档里 `auto_pool` 的槽位归属用 `break`：一件装备只进**第一个**匹配的槽。
# 而 SLOTS 里「主手」排在「副手」之前 ⇒ got['副手'] **恒为空**，
# 副手只能靠 `POOL_WPN`（6 件）的 or-兜底活着。一旦 CUR / `GD_CUR_JSON`
# 注入「当前穿着」（哪怕只有 1 件普通品质武器），`POOL_BASE['副手']` 就有值了，
# `POOL_BASE.get(slot) or POOL_WPN` 不再回退 ⇒ 那 1 件再被 `ALLOW_NORMAL` 滤掉
# ⇒ **副手 0 候选** ⇒ `beam_search_np` 的 SUFM 归约零尺寸数组崩溃：
#   ValueError: zero-size array to reduction operation maximum which has no identity
# 实测触发条件：存档「当前使用武器套」里装的是普通品质单手剑（Sam 的套装 1）。
_WPN_DUAL_OLD = """        for s in SLOTS:
            if slot_ok(s, gid):
                got[s].append((sc, gid))
                break"""

_WPN_DUAL_NEW = """        for s in SLOTS:
            if slot_ok(s, gid):
                got[s].append((sc, gid))
                # ★ **成对/可互换槽位**必须同时入池：原实现无条件 break，而 SLOTS 里前一个
                #   排在前面 ⇒ 后一个恒为空。踩过两次（症状一样，都是「后一槽只剩硬编码名单」）：
                #   ① 武器：「主手」在「副手」前 ⇒ got['副手'] 恒空 ⇒ 一旦 CUR/GD_CUR_JSON
                #      注入副手，`POOL_BASE.get('副手') or POOL_WPN` 的 or-兜底即失效
                #      ⇒ 副手 0 候选 ⇒ SUFM 归约零尺寸数组崩溃。
                #   ② 戒指（2026-09-20）：「戒指1」在「戒指2」前 ⇒ got['戒指2'] 恒空
                #      （实测 auto_pool 产出 戒指1=130 / 戒指2=0），戒指2 只剩 9 件手工名单。
                #      两槽 SLOTDIR 都是 ['rings']、slot_ok 同判据 ⇒ 池必须同源。
                if s in ('主手', '副手', '戒指1', '戒指2'):
                    continue
                break"""

_CHOICES_BASES_OLD = """def choices(slot):
    out = []
    bases = [PIN[slot]] if slot in PIN else (POOL_BASE.get(slot) or POOL_WPN)"""

_CHOICES_BASES_NEW = """def choices(slot):
    out = []
    if slot in PIN:
        bases = [PIN[slot]]
    elif slot in ('主手', '副手'):
        # ★ 武器槽：POOL_WPN 是**默认武器池**，必须并进来，而不是当 `or` 兜底。
        #   否则「POOL_BASE 里恰好有 1 件」（CUR / GD_CUR_JSON 注入的当前武器）
        #   是 truthy ⇒ `or POOL_WPN` 不触发 ⇒ 6 件默认池被整体屏蔽，
        #   候选可能只剩 0（再被 ALLOW_NORMAL 滤掉普通装）⇒ 束搜索崩。
        bases = list(dict.fromkeys(list(POOL_BASE.get(slot) or []) + list(POOL_WPN)))
    else:
        bases = POOL_BASE.get(slot) or POOL_WPN"""

_SUFM_OLD = """    SUFM = None
    if _FULL:
        SUFM = np.zeros((ns + 1, NT))
        for _si in range(ns - 1, -1, -1):
            _cR = np.array([c[1] for c in CSMAP[order[_si]]], np.float64)
            SUFM[_si] = SUFM[_si + 1] + _cR.max(axis=0)"""

_SUFM_NEW = """    SUFM = None
    if _FULL:
        SUFM = np.zeros((ns + 1, NT))
        for _si in range(ns - 1, -1, -1):
            _rows = CSMAP[order[_si]]
            if not _rows:
                # 空槽（该槽 0 候选）：它对各维的贡献上界就是 0，不做归约。
                # 过去这里直接 `_cR.max(axis=0)`，遇空槽会以
                # 「ValueError: zero-size array to reduction」的晦涩方式崩掉。
                # 空槽本身是上游错误，由 run_search 提前报出明确原因。
                SUFM[_si] = SUFM[_si + 1]
                continue
            _cR = np.array([c[1] for c in _rows], np.float64)
            SUFM[_si] = SUFM[_si + 1] + _cR.max(axis=0)"""

_RUNSEARCH_OLD = """            csmap = {s: prune_cands(slot_cands(s)) for s in sorted(set(order))}
    orders = _orders_for(order, restart)"""

_RUNSEARCH_NEW = """            csmap = {s: prune_cands(slot_cands(s)) for s in sorted(set(order))}
    # ★ 空槽提前报错：某槽 0 候选时束搜索给不出有意义的解，
    #   而 numpy 版过去会一路撑到 SUFM 才以「零尺寸数组归约」崩掉（难排查）。
    _empty = sorted(s for s in set(order) if not csmap.get(s))
    if _empty:
        raise SystemExit(
            '✗ 这些槽位没有任何候选：%s\\n'
            '  排查：① 品质过滤（普通装默认被剔）② 装备需求等级 > GD_MAX_ILVL\\n'
            '        ③ 派系 / 属性闸门过紧 ④ GD_SLOTS 槽位名不对'
            % '、'.join(_empty))
    orders = _orders_for(order, restart)"""


# ---------------------------------------------------------------- 24 权威覆盖
# `FILES` 里的旧脚本只提供「基座」。本 skill 成熟后，有一批模块的语义**已远超旧版**：
#
#   gd/req.py          253 行「估算版」 -> 507 行官方 itemCostFormulae 求值版
#   gd/build.py        501 -> 652       （接入 reqfit：写盘前按新装备重算三围）
#   gd/save/patch.py   197 -> 313       （写档动作本身即需求求解器）
#   gd/rotation.py    1230 -> 1260
#   gd/save/backup.py  190 -> 218
#   gd/alloc.py        561 -> 579
#   gd/save/verify.py  318 -> 327
#   gd/dps.py          +131 行（`load_char` 的构建覆盖 / 无存档模式 / `attack_rows`）
#   gd/opt.py          +458 行（减抗两维、伤害代理的边际标定、转化逐件估值）
#   gd/rr.py           新增模块（敌方减抗：收集/合计/换算实战伤害）
#
# 实测：不加这一步，重跑迁移会**静默丢掉 881 行**（`gd/req.py` 整体退回估算版）。
# 因为 FILES 只做「搬运 + 字符串打补丁」，而这些改动是**重写级**的，
# 写成几百行细粒度 patch 既脆弱又不可读 —— 改为「迁移末尾整体覆盖」。
#
# ★★ `gd/dps.py` / `gd/opt.py` 的**归属已从 patch 步骤迁到本表**（2026-09-19）：
#    它们的积累改动到了 131 / 458 行，已经不是「几处字符串替换」能表达的规模。
#    下面针对它们的步骤 10/11/15/16/17/20/21 现在**仍然会跑**（作用于旧基座，
#    并且不会报错），但产物随即被本表覆盖 —— 所以那些步骤是**过时**的，
#    新增改动一律改 `tools/gd_*_patched.py` 并跑 `sync_live.py --capture`，
#    **不要**再往那些步骤上叠。`tools/selftest.py` 的 [13] 组会守住这条不变量。
#
# 真源 = `tools/` 下的保命副本（同时是 `tools/sync_live.py` 的抢修源，共用 PAIRS）。
# 副本缺失 -> 直接失败退出，**绝不**产出「看起来成功、其实缺功能」的 skill。
# 留档只在**迁移前**活文件就与副本不同时才做 —— 因为 FILES 步骤刚打回的旧版
# 是「本脚本自己的产物」，没有留档价值，每次都存只会淹没真正的证据。
# ---------------------------------------------------------------- 25 抽取产物
# 伤害模型 v2 依赖两份**自己抽出来的**数据，归档里没有，`FILES` / `DATA` 都管不到：
#
#   data/combatformulas.json   ← tools/extract_combatformulas.py
#       `window.combatformulas`（官方 PTH 阈值/倍率/下限、护甲吸收 70%、6 部位概率）
#   data/monster_stats.json    ← tools/extract_monsterdb.py
#       `window.allMonsters` 2840 只（9 个抗性真值 + DA/OA 方程）+ 难度修正表
#
# 缺了它们 `gd/combat.py` 会退化、`gd/enemy.py` 会返回空 —— 都是**静默降级**，
# 不报错但数字不对。所以这里硬性检查：缺了就现场重建；重建不了就**返回非零退出码**，
# 而不是产出一个「看起来成功、其实没数据」的 skill。
EXTRACTS = [
    ("data/combatformulas.json", "extract_combatformulas.py",
     "官方公式常数表（PTH / 护甲 / DoT 语义）"),
    ("data/monster_stats.json", "extract_monsterdb.py",
     "敌方真值表（2840 只怪 + 难度修正）"),
    # ★ 2026-09-20 新增。为什么需要：`data/skills.json` 是旧 `.arz` 抽取的产物，
    #   它的 `KEEP_PREFIX` 名字白名单把 `skillChanceWeight` 整库丢掉了
    #   ⇒ 武器池技能（WPS）在伤害模型里被当成默认攻击。真源是 asar 的 `calc.js`。
    #   缺了就重建 —— **不重建会让 WPS 静默退回「默认攻击」这种错分类**，
    #   而且错得不报错（正是最难查的那类）。
    ("data/calc_mastery_skills.json", "extract_calc_skills.py",
     "职业技能补洞表（skillChanceWeight / conversion*，源 = asar calc.js）"),
]


def ensure_extracts():
    missing = [(p, s, zh) for p, s, zh in EXTRACTS if not (SKILL / p).is_file()]
    print("\n[25] 抽取产物（战斗公式表 / 敌方真值表 / 技能补洞表）")
    if not missing:
        for p, _s, zh in EXTRACTS:
            print(f"  ✓ {p:30} 已就位  {zh}")
        return False
    ok = True
    for p, script, zh in missing:
        print(f"  ↓ {p:30} 缺失 -> 跑 tools/{script}")
        r = subprocess.call([sys.executable, str(SKILL / "tools" / script)],
                            cwd=str(SKILL))
        if r != 0 or not (SKILL / p).is_file():
            ok = False
            print(f"  ✗ 重建失败（exit={r}）：{zh}")
    if not ok:
        print("\n✗ 抽取产物不齐，拒绝产出残缺 skill。手动重建：")
        for p, script, _zh in missing:
            print(f"    {sys.executable} tools/{script}")
        print("  提示：monster_stats 需要能读到 app.asar 与 node（见脚本头 docstring）。")
    return not ok


def _live_pairs():
    sys.path.insert(0, str(SKILL / "tools"))
    import sync_live  # PAIRS / md5 的唯一定义处，避免两份清单漂移
    return sync_live


def snapshot_live(sync_live):
    """迁移开始前记录受管活文件的 md5，用于区分「外部手改」与「FILES 打回」。"""
    pre = {}
    for live, _bak in sync_live.PAIRS:
        lp = SKILL / live
        pre[live] = sync_live.md5(str(lp)) if lp.is_file() else None
    return pre


def overlay_live(sync_live, pre):
    missing, archived = [], []
    for live, bak in sync_live.PAIRS:
        bp, lp = SKILL / bak, SKILL / live
        if not bp.is_file():
            missing.append(bak)
            continue
        if lp.is_file():
            was, now = pre.get(live), sync_live.md5(str(lp))
            if was is not None and was != sync_live.md5(str(bp)):
                # 迁移**前**就不一致 -> 真的有人手改过，值得留档
                rd = SKILL / "tools" / "_reverted"
                rd.mkdir(parents=True, exist_ok=True)
                dst = rd / f"{lp.name}.{was[:8]}"
                if not dst.exists():
                    shutil.copy2(lp, dst)
                archived.append(f"{live} -> tools/_reverted/{dst.name}")
        lp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bp, lp)
        print(f"  ✓ {bak:38} -> {live}")
    for a in archived:
        print(f"     （迁移前已手改，留档 {a}）")
    return missing


def merge_archetypes():
    """把 `data/archetypes_gdskill.json` 的形态增补**幂等合并**进 `data/archetypes.json`。

    见步骤 23.5 的说明：`DATA` 表里的 `gt_data/archetypes.json` 是无条件 copy，
    本 skill 自己加的形态必须走这个入口才不会被下一次迁移打掉。

    合并语义（2026-09-20 修正）：**增补对自己的键有最终话语权**。
    `data/archetypes_gdskill.json` 里出现的形态键**一律以增补为准覆盖**；
    其余键（归档原有的）不动。

    为什么从「只补缺失键」改成「增补优先」：形态定义是要**迭代**的
    （实测：`avalanche` 先是按「雪崩 = 默认攻击」写的，后来查明真源里
    `skillChanceWeight` 12→30 ⇒ 它其实是武器池技能，`root_skills` /
    `primary_attack` 都得改）。旧语义下改了增补文件却不生效，
    而 `archetypes.json` 本身又是归档无条件覆盖的 —— 改它就等于白改。
    「增补优先」同时是幂等的：反复跑结果一致。
    """
    extra_p = SKILL / "data" / "archetypes_gdskill.json"
    tgt_p = SKILL / "data" / "archetypes.json"
    if not extra_p.is_file():
        print("  跳过（没有 archetypes_gdskill.json）")
        return
    if not tgt_p.is_file():
        print("  ⚠ 目标 data/archetypes.json 不存在，跳过合并")
        return
    try:
        extra = json.loads(extra_p.read_text(encoding="utf-8"))
        tgt = json.loads(tgt_p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ✗ 形态增补合并失败：{e}")
        return
    add = (extra or {}).get("archetypes") or {}
    added, changed = [], []
    for k, v in add.items():
        if k not in tgt:
            added.append(k)
        elif tgt[k] != v:
            changed.append(k)
        tgt[k] = v
    if added or changed:
        # ★ 保持原文件的排版风格（indent=1、无尾换行）—— 换风格会让整个文件
        #   在 diff 里变成「全文重写」，掩盖真正的改动。
        tgt_p.write_text(json.dumps(tgt, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print("  增补形态：新增 %s%s"
          % (added or '无', ("｜按增补更新 %s" % changed) if changed else ""))


def main() -> int:
    arc = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ARCHIVE
    src = arc / "scripts"
    if not src.is_dir():
        print(f"✗ 归档脚本目录不存在：{src}")
        return 2

    sync_live = _live_pairs()
    pre = snapshot_live(sync_live)

    (SKILL / "gd" / "save").mkdir(parents=True, exist_ok=True)
    n = 0
    for name, dest, in_save in FILES:
        s = src / name
        if not s.exists():
            print(f"  跳过（源缺失）{name}")
            continue
        text = patch(s.read_text(encoding="utf-8"), in_save)
        (SKILL / dest).write_text(text, encoding="utf-8")
        print(f"  ✓ {name:16} -> {dest}")
        n += 1

    for rel in DATA:
        s = arc / "scripts" / rel if (arc / "scripts" / rel).exists() else arc / rel
        if not s.exists():
            print(f"  跳过（数据缺失）{rel}")
            continue
        d = SKILL / "data" / pathlib.Path(rel).name
        shutil.copy2(s, d)
        print(f"  ✓ {rel:48} -> data/{d.name}  ({d.stat().st_size / 1048576:.1f} MB)")

    # 步骤 23.5：形态增补合并（★ 必须在上面那条无条件 copy 之后）
    #   为什么需要：`DATA` 里的 `gt_data/archetypes.json` 是 `shutil.copy2` **无条件覆盖**，
    #   所以本 skill 自己新增的形态（如 `avalanche`）如果直接写进 `data/archetypes.json`，
    #   会在下一次迁移时**静默丢失**（陷阱 #23 的同构形态）。
    #   故增补一律登记在 `data/archetypes_gdskill.json`，这里做**幂等合并**：
    #   只补缺失键，已存在的键不动（不覆盖手工调整）。
    merge_archetypes()

    # 步骤 24：把 FILES 搬运出来的基座，整体换成成熟版（见 overlay_live 注释）
    print("\n[24] 权威覆盖（tools/ 保命副本 -> gd/）")
    miss = overlay_live(sync_live, pre)
    if miss:
        print("\n✗ 保命副本缺失 %d 个，拒绝产出残缺 skill：" % len(miss))
        for m in miss:
            print(f"    {m}")
        print("  先跑 `python tools/sync_live.py --capture` 生成副本。")
        return 3

    # 步骤 25：伤害模型 v2 的两份**抽取产物**（官方公式常数表 / 敌方真值表）
    #   它们不在归档里（是 skill 自己从 app.asar 抽的），所以 `FILES` / `DATA` 都管不到。
    #   这里只做「缺了就重建」：产物已在则**不重跑**（抽取要起 node + 解 9.3 MB asar，
    #   代价不小，且重跑不会改变结果）。
    bad_data = ensure_extracts()

    print(f"\n完成：{n} 个模块（含权威覆盖）")
    return 3 if bad_data else 0


if __name__ == "__main__":
    sys.exit(main())
