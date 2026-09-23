"""统一命令行入口：python -m gd <子命令>

    python -m gd env                       环境探测（GT 安装 / 存档 / 游戏 / 角色）
    python -m gd db                        离线库概览
    python -m gd item 天之裂片咒刃           物品卡片（支持中文名或 itXXXX）
    python -m gd item it2116 --range        附官方区间 [lo-hi]
    python -m gd find 利维坦 --quality Legendary
    python -m gd affix 狂怒的
    python -m gd set 世界守护者的花园
    python -m gd skill 暗影面纱
    python -m gd monster 奇努科斯
    python -m gd devotion 亡魂
    python -m gd text tagCharAttackSpeed    多语言文本直查
    python -m gd label offensiveSlowFireModifier
    python -m gd level                      升级给点表
    python -m gd grep 点数                 在语言包里全文搜索
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from . import paths
from .db import DB
from .render import QUALITY_CN, Renderer

_CACHE = {}


def _db(args) -> "object":
    if "db" not in _CACHE:
        _CACHE["db"] = DB.load(lang=getattr(args, "lang", "zh") or "zh",
                               verbose=getattr(args, "verbose", False))
    return _CACHE["db"]


def _renderer(args):
    if "r" not in _CACHE:
        _CACHE["r"] = Renderer(_db(args))
    return _CACHE["r"]


def _resolve(db, token: str, kind: str = "item") -> Optional[str]:
    """把「gid 或名字」解析成 gid。"""
    tables = {"item": db.items, "set": db.sets, "monster": db.monsters,
              "skill": None, "affix": None}
    if kind == "affix":
        if token in db.prefixes or token in db.suffixes:
            return token
        for t in (db.prefixes, db.suffixes):
            for gid in t:
                if db.name(gid) == token:
                    return gid
        hits = db.find(token, "prefix", 1) + db.find(token, "suffix", 1)
        return hits[0][0] if hits else None
    if kind == "skill":
        # 技能来源有三处：物品技能 / 专精技能 / buff 技能
        for tbl in (db.skills, db.mastery, db.buff_skills):
            if token in tbl:
                return token
            for gid in tbl:
                if db.name(gid) == token:
                    return gid
        for tbl in (db.skills, db.mastery, db.buff_skills):
            for gid in tbl:
                if token.lower() in db.name(gid).lower():
                    return gid
        return None
    table = tables.get(kind) or db.items
    if token in table:
        return token
    for gid in table:
        if db.name(gid) == token:
            return gid
    hits = db.find(token, kind, 1)
    return hits[0][0] if hits else None


# ==================================================================== 子命令
def cmd_env(args):
    s = paths.summary()
    print("=" * 72)
    print("恐怖黎明 · 离线数据工具链 · 环境")
    print("=" * 72)
    print(f"  Grim Tools 桌面版 : {s['gt_dir'] or '✗ 未找到'}")
    print(f"  app.asar         : {s['asar'] or '✗ 未找到'}")
    print(f"  存档目录          : {s['save_dir'] or '✗ 未找到'}  [{s['save_kind']}]")
    print(f"  游戏目录          : {s['game_dir'] or '—'}")
    print(f"  角色              : {', '.join(s['characters']) or '（无）'}")
    print(f"  推荐解释器        : {s['python']}")
    print(f"  游戏进程          : {'⚠ 正在运行（改档前必须退出）' if paths.game_running() else '未运行 ✓'}")
    return 0 if s["asar"] else 1


def cmd_db(args):
    db = _db(args)
    st = db.stats()
    print("=" * 72)
    print(f"离线库  {st['gameVersion']}   语言={st['lang']}")
    print("=" * 72)
    for k, v in st.items():
        if k in ("gameVersion", "lang"):
            continue
        print(f"  {k:<14} {v}")
    if args.json:
        print(json.dumps(st, ensure_ascii=False, indent=1))
    return 0


def cmd_item(args):
    db, r = _db(args), _renderer(args)
    gid = _resolve(db, args.token, "item")
    if not gid:
        print(f"✗ 找不到物品「{args.token}」")
        return 1
    print(r.item_card(gid, show_range=args.range, desc=not args.no_desc))
    if args.json:
        print(json.dumps(db.items[gid], ensure_ascii=False, indent=1))
    return 0


def cmd_find(args):
    db = _db(args)
    kind = "item" if args.kind == "item" else args.kind
    hits = db.find(args.keyword, kind, args.limit, quality=args.quality,
                   max_level=args.max_level)
    if not hits:
        print(f"✗ 没有匹配「{args.keyword}」的结果")
        return 1
    for gid, nm in hits:
        extra = ""
        if kind == "item":
            o = db.items[gid]
            q = QUALITY_CN.get(o.get("f") or "", o.get("f") or "")
            cls = db.class_cn(db.item_class(gid))
            extra = f"  {q} {cls} 等级{o.get('k') or '-'}"
        print(f"  {nm:<28} {gid}{extra}")
    print(f"\n  共 {len(hits)} 条（上限 {args.limit}）")
    return 0


def cmd_affix(args):
    db, r = _db(args), _renderer(args)
    gid = _resolve(db, args.token, "affix")
    if not gid:
        print(f"✗ 找不到词缀「{args.token}」")
        return 1
    print(r.affix_card(gid))
    return 0


def cmd_set(args):
    db, r = _db(args), _renderer(args)
    gid = _resolve(db, args.token, "set")
    if not gid:
        print(f"✗ 找不到套装「{args.token}」")
        return 1
    print(r.set_card(gid))
    return 0


def cmd_skill(args):
    db, r = _db(args), _renderer(args)
    gid = _resolve(db, args.token, "skill")
    if not gid:
        print(f"✗ 找不到技能「{args.token}」")
        return 1
    print(r.skill_card(gid))
    return 0


def cmd_monster(args):
    db = _db(args)
    gid = _resolve(db, args.token, "monster")
    if not gid:
        print(f"✗ 找不到怪物「{args.token}」")
        return 1
    m = db.monsters[gid]
    print(f"{db.name(gid)}   [{gid}]")
    print(f"  类型：{m.get('type')}   难度：{m.get('diff')}")
    refs = db.mi_refs.get(gid)
    if refs:
        print("  掉落专属：")
        for mid in refs[:20]:
            print(f"    · {db.name(mid)}  ({mid})")
    return 0


def cmd_devotion(args):
    db = _db(args)
    devs = db.devotions()
    if not devs:
        print("✗ 没有星座数据（data/devotion_tree.json 缺失）")
        return 1
    if args.token:
        hit = [d for d in devs if args.token in d["name"] or args.token == d["id"]]
        if not hit:
            print(f"✗ 找不到星座「{args.token}」")
            return 1
        for d in hit:
            print(f"{d['name']}")
            print(f"  亲和力需求：{d.get('affinity')}")
            print(f"  星数：{len(d.get('stars') or [])}   节点技能：{d.get('stars')}")
            print(f"  [{d['id']}]")
        return 0
    print(f"共 {len(devs)} 个星座：")
    for d in sorted(devs, key=lambda x: x["name"]):
        print(f"  {d['name']:<14} 亲和 {d.get('affinity')}")
    return 0


def cmd_text(args):
    db = _db(args)
    if args.lang:
        db.l10n.set_lang(args.lang)
    for tok in args.tokens:
        v = db.l10n.get(tok)
        print(f"{tok:<34} {v}")
    return 0


def cmd_label(args):
    db, r = _db(args), _renderer(args)
    for f in args.fields:
        tag = db.field_tag(f)
        print(f"{f:<36} -> {tag or '（无标签）':<32} {db.l10n.get(tag) if tag else ''}")
    return 0


def cmd_level(args):
    db = _db(args)
    lt = db.level_table()
    print(f"等级上限 {lt['level_cap']}   精通里程碑 {lt['milestones']}")
    print(f"满级技能点 {lt['max_skill_points']}   属性点 {lt['max_attribute_points']}   "
          f"虔诚点 {lt['max_devotion_points']}")
    print(f"任务额外：技能点 {lt['quest_skill_points']} / 属性点 {lt['quest_attribute_points']}")
    print()
    print("等级 -> 累计技能点")
    for lv in range(1, int(lt["level_cap"]) + 1):
        if lv in (1, 10, 20, 30, 40, 50, 60, 65, 70, 75, 80, 90, 94, 100):
            print(f"  lv{lv:<4} {lt['skill_points_at_level'][lv - 1]}")
    return 0


def cmd_grep(args):
    db = _db(args)
    hits = db.l10n.search(args.keyword, limit=args.limit)
    if not hits:
        print(f"✗ 语言包里没有含「{args.keyword}」的条目")
        return 1
    for k, v in hits:
        print(f"  {k:<38} {v[:70]}")
    print(f"\n  共显示 {len(hits)} 条（上限 {args.limit}）")
    return 0


def cmd_filter(args):
    db = _db(args)
    crit = {}
    for expr in args.expr:
        if "=" not in expr:
            print(f"✗ 条件必须是 field=value 形式：{expr}")
            return 2
        k, v = expr.split("=", 1)
        try:
            v = json.loads(v)
        except Exception:
            pass
        crit[k] = v
    hits = db.filter_items(**crit)
    print(f"命中 {len(hits)} 件")
    for gid in hits[: args.limit]:
        o = db.items[gid]
        print(f"  {db.name(gid):<28} {gid}  等级{o.get('k')}")
    return 0


def cmd_chars(args):
    from .save import report as R
    rows = R.list_chars()
    if not rows:
        print("✗ 没找到任何角色存档")
        return 1
    print(f"{'角色':<12}{'等级':>5}  职业")
    for name, lv, cls in rows:
        mark = " ✗" if lv < 0 else ""
        print(f"{name:<12}{lv:>5}  {cls}{mark}")
    return 0


def cmd_save(args):
    from .save import report as R
    txt = R.character(args.char, full=args.full)
    print(txt)
    if args.out:
        from pathlib import Path
        Path(args.out).write_text(
            R.character_md(args.char, full=args.full) if args.md else txt,
            encoding="utf-8")
        print(f"\n→ 已写入 {args.out}")
    return 0


def cmd_backup(args):
    from .save import backup as B
    if args.action == "backup":
        B.do_backup(" ".join(args.note or []) or "命令行备份")
    elif args.action == "verify":
        return 0 if B.do_verify(args.dir) else 1
    elif args.action == "diff":
        B.do_diff(args.dir)
    elif args.action == "restore":
        if not args.dir:
            print("✗ restore 需要指定备份目录")
            return 2
        return 0 if B.do_restore(args.dir, args.yes) else 1
    return 0


def cmd_verify(args):
    """统一体检：全角色 / 单角色 / 与备份 diff / 写盘前安全检查。"""
    import sys as _s
    from .save import verify as V
    argv = []
    if args.char:
        argv.append(args.char)
    if args.preflight:
        argv.append("--preflight")
    if args.diff:
        argv += ["--diff", args.diff]
    saved = _s.argv
    try:
        _s.argv = ["gd verify"] + argv
        return V.main() or 0
    finally:
        _s.argv = saved


def _run_module(name: str, argv: list[str]) -> int:
    """把子命令转发给「脚本式」模块（gd.opt 有模块级副作用，必须独立进程跑）。"""
    import subprocess
    import sys as _s
    cmd = [_s.executable, "-m", name] + list(argv)
    return subprocess.call(cmd, cwd=str(paths.SKILL_ROOT))


def cmd_dps(args):
    return _run_module("gd.dps", [args.char] + args.rest)


def cmd_dps_rest(args):
    """`gd dps` 的**透传**入口（见 `_PASSTHRU`）。

    ★ 为什么不能走 `cmd_dps`：`dps` 子解析器只声明了 `char` + `rest`，
      任何 `--enemy-difficulty normal` 这类**带值的未知选项**会被 `parse_known_args`
      拆成 extra(['--enemy-difficulty']) + rest(['normal'])，拼回去顺序就被打乱，
      下游 `gd.dps` 收到 `Sam normal --enemy-difficulty` ⇒ 「expected one argument」。
      透传路径按原始顺序切分，不受影响。
    """
    return _run_module("gd.dps", args.rest)


def cmd_opt(args):
    return _run_module("gd.opt", args.rest)


def cmd_recipe(args):
    return _run_module("gd.recipe", args.rest)


def cmd_rotation(args):
    return _run_module("gd.rotation", args.rest)


def cmd_enemy(args):
    """`gd enemy` 的透传入口 —— 参数里全是**带值选项**（`--level 100`、
    `--panel m3955`），走声明式子解析器会被 `parse_known_args` 拆散顺序。"""
    return _run_module("gd.enemy", args.rest)


def cmd_tool(args):
    """通用转发：把参数原样交给任意 `gd.<模块>`。"""
    return _run_module(f"gd.{args.module}", args.rest)


def cmd_build(args):
    return _run_module("gd.build", args.rest)


def cmd_auto(args):
    return _run_module("gd.auto", args.rest)


def cmd_rr(args):
    """敌方减抗（RR）：汇总 / 全库普查（见 `gd/rr.py`）。"""
    return _run_module("gd.rr", args.rest)


# 透传型子命令 → 处理函数（参数原样转发，见 main()）
_PASSTHRU = {"opt": "cmd_opt", "recipe": "cmd_recipe", "rotation": "cmd_rotation",
             "build": "cmd_build", "auto": "cmd_auto", "rr": "cmd_rr",
             "dps": "cmd_dps_rest", "enemy": "cmd_enemy"}


# ==================================================================== 装配
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gd", description="恐怖黎明 离线数据工具链")
    p.add_argument("--lang", default="zh", help="语言（默认 zh）")
    p.add_argument("--verbose", action="store_true", help="打印加载细节")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        s = sub.add_parser(name, help=help_)
        s.set_defaults(func=fn)
        return s

    s = add("env", cmd_env, "环境探测")
    s = add("db", cmd_db, "离线库概览")
    s.add_argument("--json", action="store_true")

    s = add("item", cmd_item, "物品卡片")
    s.add_argument("token")
    s.add_argument("--range", action="store_true", help="附官方区间 [lo-hi]")
    s.add_argument("--no-desc", action="store_true", help="不显示描述文本")
    s.add_argument("--json", action="store_true", help="追加原始字段 JSON")

    s = add("find", cmd_find, "按名字搜索")
    s.add_argument("keyword")
    s.add_argument("--kind", default="item",
                   choices=["item", "prefix", "suffix", "set", "itemSkill", "monster"])
    s.add_argument("--quality", default=None,
                   choices=list(QUALITY_CN) + [None])
    s.add_argument("--max-level", type=int, default=None)
    s.add_argument("--limit", type=int, default=30)

    s = add("affix", cmd_affix, "词缀卡片")
    s.add_argument("token")

    s = add("set", cmd_set, "套装卡片")
    s.add_argument("token")

    s = add("skill", cmd_skill, "技能卡片")
    s.add_argument("token")

    s = add("monster", cmd_monster, "怪物信息")
    s.add_argument("token")

    s = add("devotion", cmd_devotion, "星座（虔诚）")
    s.add_argument("token", nargs="?", default=None)

    s = add("text", cmd_text, "多语言文本直查")
    s.add_argument("tokens", nargs="+")

    s = add("label", cmd_label, "字段 -> 中文标签")
    s.add_argument("fields", nargs="+")

    s = add("level", cmd_level, "升级给点表")

    s = add("grep", cmd_grep, "语言包全文搜索")
    s.add_argument("keyword")
    s.add_argument("--limit", type=int, default=30)

    s = add("filter", cmd_filter, "按原始字段过滤物品")
    s.add_argument("expr", nargs="+", help="field=value，支持 field__gte=50 等后缀")
    s.add_argument("--limit", type=int, default=30)

    # ---------------- 存档 ----------------
    add("chars", cmd_chars, "列出所有角色")

    s = add("save", cmd_save, "角色报告（只读，不写盘）")
    s.add_argument("char", nargs="?", default="",
                   help="角色名（省略则报错并列出可选）")
    s.add_argument("--full", action="store_true", help="含背包逐件明细")
    s.add_argument("--md", action="store_true", help="输出 Markdown")
    s.add_argument("--out", default=None, help="写入文件")

    s = add("backup", cmd_backup, "备份 / 校验 / 对比 / 恢复存档")
    s.add_argument("action", choices=["backup", "verify", "diff", "restore"])
    s.add_argument("dir", nargs="?", default=None, help="备份目录（verify/diff/restore 用）")
    s.add_argument("note", nargs="*", help="备份备注")
    s.add_argument("--yes", action="store_true", help="restore 必须显式确认")

    s = add("verify", cmd_verify, "存档统一体检（含写盘前安全检查）")
    s.add_argument("char", nargs="?", default=None)
    s.add_argument("--preflight", action="store_true")
    s.add_argument("--diff", default=None, help="与指定备份目录逐字段 diff")

    # ---------------- 计算层 ----------------
    s = add("dps", cmd_dps, "存档实测 DPS 校验")
    s.add_argument("char", nargs="?", default="")
    s.add_argument("rest", nargs="*", help="其余参数原样透传")

    s = add("opt", cmd_opt, "装备优化器（透传参数给 gd.opt）")
    s.add_argument("rest", nargs="*")

    s = add("recipe", cmd_recipe, "BD 配方（透传参数给 gd.recipe）")
    s.add_argument("rest", nargs="*")

    s = add("rotation", cmd_rotation, "输出循环 / 最终伤害模型")
    s.add_argument("rest", nargs="*")

    s = add("tool", cmd_tool, "通用转发：gd tool <模块名> <参数…>")
    s.add_argument("module")
    s.add_argument("rest", nargs="*")

    s = add("build", cmd_build, "改档方案构建器（new / plan / check / apply）")
    s.add_argument("rest", nargs="*")

    s = add("auto", cmd_auto, "★ 一条命令跑完全链路：读档 → 满抗搜索 → 真实 DPS 微调 → 报告/落档")
    s.add_argument("rest", nargs="*")

    s = add("rr", cmd_rr, "敌方减抗（RR）汇总 / 全库字段普查")
    s.add_argument("rest", nargs="*")
    return p


def main(argv=None) -> int:
    # ★ 透传型子命令（opt / recipe / rotation / build / auto）：
    #   把子命令词**之后的原始参数原样**转发，顺序一点不动。
    #   为什么不能靠 argparse：`parse_known_args` 会把未知选项（如 `--goal dmg`）
    #   丢进 extra，拼回 rest 时**顺序被打乱**（`--compare a.json` 变成
    #   `a.json --compare`），下游解析直接崩。所以这几条走手工切分。
    argv = list(sys.argv[1:] if argv is None else argv)
    i, cmd = 0, None
    while i < len(argv):
        t = argv[i]
        if t == "--lang":
            i += 2
            continue
        if t == "--verbose" or t.startswith("-"):
            i += 1
            continue
        if t in _PASSTHRU:
            cmd = t
        break
    if cmd is not None:
        from types import SimpleNamespace
        return globals()[_PASSTHRU[cmd]](SimpleNamespace(rest=argv[i + 1:]))

    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    if extra:
        if hasattr(args, "rest"):
            args.rest = list(args.rest) + list(extra)
        else:
            parser.error("unrecognized arguments: %s" % " ".join(extra))
    try:
        return args.func(args)
    except FileNotFoundError as e:
        print(f"✗ {e}")
        return 3
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
