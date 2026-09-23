#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 grimtools 配装（`/calc/<短ID>` 或页内 `buildInfo` JSON）解码成**本地可读**的构建清单。

★ 全程离线（除首次抓页面）：
  · 页面 → 只抓一次，缓存到 `data/cache/gt_<ID>.html`
  · ID → 原始字段：`data/cache/itemdb.js`（走 `tools/dump_ids.js`，node 平衡扫描）
  · tag → 中文：`gd.gear.load_tags()`（16563 条，覆盖物品/技能/星座/词缀）
  · it/pre/suf → 记录路径：`data/record_map.json`
  · sk → 星座归属：`data/devotion_tree.json`

为什么值得做：社区成熟构建是**现成的回归语料**——
同一份 `buildInfo` 既可以喂给我们的引擎算一遍，跟 grimtools 面板数字对拍，
也可以直接读出「人家点了哪些星座、怎么分配技能点、靠什么减抗」，
反推我们算法缺的机制（见 docs/`gap_analysis`）。

用法：
  python tools/gt_build.py ZyDo860V                          # 短 ID
  python tools/gt_build.py https://www.grimtools.com/calc/ZyDo860V
  python tools/gt_build.py --html data/cache/gt_ZyDo860V.html
  python tools/gt_build.py ZyDo860V --json out.json          # 归一化构建
  python tools/gt_build.py ZyDo860V --dump it13922           # 看单个 id 的原始字段
  python tools/gt_build.py ZyDo860V --rr                     # 只列抗性削减来源
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ITEMDB = os.path.join(ROOT, "data", "cache", "itemdb.js")
CACHE = os.path.join(ROOT, "data", "cache")
PLANS = os.path.join(ROOT, "data", "plans")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

SLOT_ZH = {"head": "头部", "amulet": "项链", "chest": "胸甲", "legs": "腿甲",
           "feet": "靴子", "hands": "手套", "ring1": "戒指1", "ring2": "戒指2",
           "waist": "腰带", "shoulders": "肩甲", "medal": "勋章", "relic": "圣物",
           "weapon1": "主手", "weapon2": "副手"}
SLOT_ORDER = list(SLOT_ZH)

CLASS_ZH = {"01": "士兵", "02": "爆破者", "03": "秘术师", "04": "夜刃", "05": "奥术师",
            "06": "萨满", "07": "审判官", "08": "死灵法师", "09": "守誓者", "10": "狂战士",
            "11": "邪术师"}

# 抗性削减字段：GT 里命名不统一，用「含 Resistance + 含 Reduct/绝对量」宽口径捞
RR_RX = re.compile(r"esistance.*(educt|Minus|Absolute|Percent)|(educt|Minus).*esistance")
CLEAN = re.compile(r"\^[a-z]")


# ---------------------------------------------------------------- 载入辅助
def _load_json(name):
    with open(os.path.join(ROOT, "data", name), encoding="utf-8") as f:
        return json.load(f)


_TAGS = None
_DEV = None


def tags():
    global _TAGS
    if _TAGS is None:
        sys.path.insert(0, ROOT)
        try:
            from gd import gear as G
            _TAGS = G.load_tags() or {}
        except Exception:
            _TAGS = {}
    return _TAGS


def dev_owner():
    """sk#### -> (星座中文名, 星序号)"""
    global _DEV
    if _DEV is None:
        _DEV = {}
        for cid, c in (_load_json("devotion_tree.json") or {}).items():
            nm = clean(tags().get(c.get("tag") or "", "") or ("星座" + str(cid)))
            for i, sk in enumerate(c.get("stars") or []):
                _DEV[sk] = (nm, i + 1)
    return _DEV


def clean(s):
    return CLEAN.sub("", s) if isinstance(s, str) else s


def zh_tag(t):
    return clean(tags().get(t) or "") if isinstance(t, str) else ""


# ---------------------------------------------------------------- 页面 → buildInfo
def fetch_html(target):
    """target 可以是短 ID 或完整 URL；命中缓存则不联网。"""
    if target.startswith("http"):
        url, key = target, target.rstrip("/").split("/")[-1]
    else:
        key = re.sub(r"[^0-9A-Za-z_-]", "", target)
        url = "https://www.grimtools.com/calc/" + key
    os.makedirs(CACHE, exist_ok=True)
    path = os.path.join(CACHE, "gt_%s.html" % key)
    if os.path.exists(path) and os.path.getsize(path) > 4096:
        return open(path, encoding="utf-8", errors="replace").read(), path
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        html = r.read().decode("utf-8", "replace")
    if "buildInfo" not in html:
        raise SystemExit("页面里没有 buildInfo（ID 可能已失效）：%s" % url)
    open(path, "w", encoding="utf-8").write(html)
    return html, path


def extract_buildinfo(html):
    """从 `window['buildInfo'] = {...}` 里平衡扫描出 JSON 对象。"""
    i = html.find("buildInfo")
    if i < 0:
        raise SystemExit("找不到 buildInfo")
    j = html.index("{", i)
    depth, instr, k = 0, None, j
    while k < len(html):
        c = html[k]
        if instr:
            if c == "\\":
                k += 2
                continue
            if c == instr:
                instr = None
        elif c in "\"'":
            instr = c
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                break
        k += 1
    return json.loads(html[j:k + 1])


# ---------------------------------------------------------------- ID → 原始字段
def node_path():
    exe = os.environ.get("NODE") or ""
    if exe and os.path.exists(exe):
        return exe
    import shutil
    w = shutil.which("node")
    if w:
        return w
    for base in (os.path.expanduser("~/.workbuddy/binaries/node/versions"),):
        if os.path.isdir(base):
            for d in sorted(os.listdir(base), reverse=True):
                p = os.path.join(base, d, "node.exe")
                if os.path.exists(p):
                    return p
    raise SystemExit("找不到 node（设 NODE=<path> 或装 node）")


def dump_ids(ids, src=None):
    """按 id 从 JS 对象字面量里导出条目。

    `src` 默认 `data/cache/itemdb.js`；也可指向 `data/cache/calc.js`
    —— 后者带**全部 `sk####` 的完整字段**（含 `skillDisplayName`），
    星座星点在那里才有（itemdb.js 里是空 `{}`），见 `tools/gt_regress.py`。
    """
    src = src or ITEMDB
    if not os.path.exists(src):
        raise SystemExit("缺 %s —— 先跑迁移把 itemdb.js 落到 data/cache/" % src)
    ids = [i for i in dict.fromkeys(ids) if i]
    out = subprocess.run([node_path(), os.path.join(HERE, "dump_ids.js"), src] + ids,
                         capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise SystemExit("dump_ids.js 失败：%s" % (out.stderr or "")[:400])
    return json.loads(out.stdout)


CALC_JS = os.path.join(ROOT, "data", "cache", "calc.js")


def sk_fields(ids, src=None):
    """`sk####` → calc.js 里的原始字段（`{}` 表示没有）。"""
    return dump_ids(ids, src=src or CALC_JS)


# ---------------------------------------------------------------- 归一化
def norm(build):
    """buildInfo -> 便于下游消费的扁平结构。"""
    d = (build or {}).get("data") or {}
    bio = d.get("bio") or {}
    eq = d.get("equipment") or {}
    ids = []
    for o in eq.values():
        for v in o.values():
            if isinstance(v, str) and re.fullmatch(r"(it|aa|pre|suf|bta|is)\d+", v):
                ids.append(v)
    for o in (d.get("potions") or {}).values():
        for v in o.values():
            if isinstance(v, str):
                ids.append(v)
    skills = [s for s in (d.get("skills") or []) if s.get("name")]
    ids += [s["name"] for s in skills]
    db = dump_ids(ids)

    slots = {}
    for key in SLOT_ORDER:
        o = eq.get(key)
        if not o:
            continue
        slots[key] = {"zh": SLOT_ZH[key], **{k: v for k, v in o.items()}}
    return {"raw": build, "db": db, "slots": slots, "skills": skills, "bio": bio,
            "masteries": (build or {}).get("masteries") or {},
            "game": (build or {}).get("created_for_build") or ""}


def item_name(gid, o):
    for k in ("a", "d", "c"):
        v = (o or {}).get(k)
        nm = zh_tag(v)
        if nm:
            return nm
    return gid


def affix_name(gid, o):
    nm = zh_tag((o or {}).get("a"))
    if nm:
        return nm
    # pre/suf 在本地 record_map 里能查到记录路径，但 GT 词缀名要另走标签
    try:
        rm = _load_json("record_map.json")
        rec = (rm.get("affix_fwd") or {}).get(gid)
        if rec:
            return os.path.basename(rec).replace(".dbr", "")
    except Exception:
        pass
    return gid


def fmt(rows, head):
    out = ["", "── %s " % head + "─" * max(0, 56 - len(head) * 2)]
    for a, b in rows:
        out.append("   %-8s %s" % (a, b))
    return "\n".join(out)


def report(n, show_rr_only=False):
    db, L = n["db"], []
    L.append("构建：%s" % os.path.basename(n.get("src") or ""))
    lvl = n["bio"].get("level")
    classes = " + ".join(CLASS_ZH.get(v, "class" + str(v))
                        for v in (n["masteries"].values() if n["masteries"] else []))
    L.append("  等级 %s ｜ 职业 %s ｜ 游戏版本 %s" % (lvl, classes or "?", n["game"] or "?"))
    bp = n["bio"]
    if bp:
        L.append("  基础属性  力量 %s / 敏捷 %s / 智力 %s ｜ 剩余点数 属性 %s 技能 %s 星座 %s"
                 % (bp.get("physique"), bp.get("cunning"), bp.get("spirit"),
                    bp.get("attributePoints"), bp.get("skillPoints"),
                    bp.get("devotionPoints")))

    rows = []
    for key in SLOT_ORDER:
        s = n["slots"].get(key)
        if not s:
            continue
        it = s.get("item")
        nm = item_name(it, db.get(it))
        bits = []
        for f, lab in (("prefix", "前缀"), ("suffix", "后缀"), ("ascendedAffix", "飞升")):
            if s.get(f):
                bits.append("%s %s" % (lab, affix_name(s[f], db.get(s[f]))))
        for f, lab in (("component", "组件"), ("augment", "附魔")):
            if s.get(f):
                bits.append("%s %s" % (lab, item_name(s[f], db.get(s[f]))))
        if s.get("relicBonus"):
            bits.append("遗物奖励 %s" % item_name(s["relicBonus"], db.get(s["relicBonus"])))
        rows.append((s["zh"], "%s%s" % (nm, ("  ← " + " / ".join(bits)) if bits else "")))
    L.append(fmt(rows, "装备"))

    # 技能：分开「职业加点」与「星座星点」
    devil = dev_owner()
    cls_rows, dev_pick = [], {}
    for s in n["skills"]:
        sk, lv = s["name"], s.get("level") or 0
        o = db.get(sk) or {}
        nm = zh_tag(o.get("name")) or sk
        if sk in devil:
            cname, idx = devil[sk]
            dev_pick.setdefault(cname, []).append((idx, lv))
        else:
            cls_rows.append((lv, nm, o))
    cls_rows.sort(key=lambda r: (-r[0], r[1]))
    L.append(fmt([(str(lv), nm + ("  ★精通" if o.get("isMastery") else ""))
                  for lv, nm, o in cls_rows if lv > 0], "职业技能加点"))
    L.append(fmt([(str(len(v)), nm) for nm, v in sorted(dev_pick.items(),
                                                         key=lambda x: -len(x[1]))], "星座"))

    # 伤害转化汇总（同 (in,out) 相加 —— 与游戏内「同来源类型可叠加」一致）
    conv, src = {}, {}
    for gid, o in db.items():
        if not isinstance(o, dict):
            continue
        i, t = o.get("conversionInType"), o.get("conversionOutType")
        if i and t:
            conv[(i, t)] = conv.get((i, t), 0) + (o.get("conversionPercentage") or 0)
            src.setdefault((i, t), []).append(item_name(gid, o))
    rows = [("%s→%s" % k, "%.0f%%   （%s）" % (v, "、".join(src[k])))
            for k, v in sorted(conv.items(), key=lambda x: -x[1])]
    L.append(fmt(rows, "伤害转化"))

    # 抗性削减
    rows = []
    for gid, o in db.items():
        if not isinstance(o, dict):
            continue
        hits = {f: v for f, v in o.items() if RR_RX.search(f) and v}
        if hits:
            rows.append((item_name(gid, o), json.dumps(hits, ensure_ascii=False)[:150]))
    L.append(fmt(rows, "抗性削减（-RR）来源"))

    if show_rr_only:
        return "\n".join(L[L.index("") if False else 0:])
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", help="grimtools 短 ID 或完整 URL")
    ap.add_argument("--html", help="已保存的页面 html")
    ap.add_argument("--json", help="把归一化构建写到该路径")
    ap.add_argument("--dump", help="只打印这个 id 的原始字段")
    ap.add_argument("--rr", action="store_true", help="只列抗性削减字段（宽口径扫全部 id）")
    a = ap.parse_args()

    if a.dump:
        print(json.dumps(dump_ids([a.dump]), ensure_ascii=False, indent=1))
        return 0

    if a.html:
        html = open(a.html, encoding="utf-8", errors="replace").read()
    elif a.target:
        html, _ = fetch_html(a.target)
    else:
        ap.error("给一个短 ID / URL，或 --html")

    n = norm(extract_buildinfo(html))
    n["src"] = a.target or a.html
    print(report(n, show_rr_only=a.rr))

    if a.json:
        os.makedirs(os.path.dirname(os.path.abspath(a.json)), exist_ok=True)
        json.dump({"bio": n["bio"], "masteries": n["masteries"], "game": n["game"],
                   "slots": n["slots"], "skills": n["skills"], "db": n["db"]},
                  open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n归一化构建 → %s" % a.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
