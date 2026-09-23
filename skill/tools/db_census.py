#!/usr/bin/env python3
"""物品库「总览」普查 —— 逐行复现 grimtools 物品库面板的总览数字，与离线库对拍。

## 为什么要这个工具

grimtools 物品库左栏第一块是「总览」（ItemDB_tagOverview），共 9 行：
物品总数 / 前缀 / 后缀 / Ascended Affixes / 套装 / 专属掉落 / 独特稀有 /
Awakened Items / 物品技能修正。

这 9 行是**判断「本地装备池与数据库是否一致」的唯一公开口径** —— 用户拿游戏内/
网页屏幕上的这 9 个数字来对，我们得能逐行复算，而不是靠「看起来差不多」。

## 数字怎么来的（**不是猜的**，逐字来自 `/dist/db/itemdb/db.js`）

渲染函数 `Ei(a)`：

```js
'...'+$db.getText("ItemDB_tagTotalNumberOfItems")+'</span><span class="filler"></span><span>'+Bj+"</span>"
'...'+$db.getText("ItemDB_tagItemPrefixes")       +'...'+Cj(prefixes)+"..."
'...'+$db.getText("ItemDB_tagItemSuffixes")       +'...'+Cj(suffixes)+"..."
'...'+$db.getText("ItemDB_tagItemAscendedAffixes")+'...'+Cj(ascendedAffixes)+"..."
'...'+$db.getText("ItemDB_tagItemSets")           +'...'+Cj(itemSets)+"..."
'...'+$db.getText("ItemDB_tagMonsterInfrequents") +'...'+MIs.length+"..."
'...'+$db.getText("ItemDB_tagUniqueRares")        +'...'+uniqueRares.length+"..."
'...'+$db.getText("ItemDB_tagAwakenedItems")      +'...'+window.awakenedItems.length+"..."
'...'+$db.getText("ItemDB_tagItemSkillModifiers") +'...'+Ej+"..."
```

⇒ 7 行是**直接取某个顶层表的键数 / 数组长度**，只有两行是**变量**，而这两个变量
正是唯一两处「本地≠站点」的地方 —— 所以必须按页面算法复算：

### ① 物品总数 = `Bj`（**不是** `allItems` 的长度）

```js
var Bj=0;
function bh(){ ... Bj=0;
  for(var a in allItems) if(allItems.hasOwnProperty(a)){
    var c=allItems[a];
    if("ItemNote"!=$db.getItemClass(c)){ U[a]=...; Bj++ } } ... }
```

⇒ **`allItems` 长度 减去 `l:"ItemNote"`（游戏内书籍/日志）的数量**。
8612 − 311 = **8301**（≡ 站点）。

### ② 物品技能修正 = `Ej`（**不是** `itemSkills` 表的长度）

```js
function Ci(){
  var a={}, c={}, b;
  for(b in dbMasteryData){ var d=dbMasteryData[b]; d.wa={};
    d.isMastery||d.isTransmuter||(c[d.name]=b) }        // c: 技能记录名 → 技能 id
  Xj(allItems,"items",a,c); Xj(prefixes,"prefixes",a,c); Xj(suffixes,"suffixes",a,c);
  Xj(ascendedAffixes,"ascendedAffixes",a,c); Xj(itemSets,"sets",a,c);
  Ej=Cj(a) }                                            // ← a 是**集合**，Ej = 它的势
```

`Xj` 的末尾（在 `for(h in f)` 里，与上面的 `modifiedSkillName` 判定平级）只做一件事：

```js
0==h.indexOf("modifierSkillName") && (b[k]=!0)          // b = 累加器 a，k = 字段值
```

⇒ `Ej` = **五张表（allItems / prefixes / suffixes / ascendedAffixes / itemSets）里
出现过的 `modifierSkillName*` 值的去重个数**。
2008 + 132 + 0 + 923 + 234（各自去重后并集）= **3297**（≡ 站点）。

`itemSkills` 全表 4268 是**记录总数**，其中 **971 条没有任何物品/词缀/套装引用它**
（GDX3 等版本遗留、被 LootRandomizer 反查过的中间态），所以站点不把 4268 当口径。

⇒ **两句话记住**：我们「多」出来的 311 是游戏内书籍（不是装备），
「少」的 971 是从没被任何装备引用的孤儿技能记录。**装备本体一件不少。**

## 用法

    python tools/db_census.py               # 9 行对拍表（默认拿截图作 golden）
    python tools/db_census.py --json        # 机器可读
    python tools/db_census.py --pool        # 追加：优化器候选池普查（14 槽）
    python tools/db_census.py --pool --level 71
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ★ 站点「总览」面板的 9 行 —— 2026-09-20 用户截图（grimtools 物品库，游戏版本 1.3.0.8）。
#   键 = 页面 l10n 标签，值 = 站点显示的数字。
GOLDEN_ZH = {
    "物品总数": 8301,
    "前缀": 1745,
    "后缀": 2360,
    "Ascended Affixes": 989,
    "套装": 199,
    "专属掉落": 2489,
    "独特稀有": 66,
    "Awakened Items": 92,
    "物品技能修正": 3297,
}

# 页面 l10n 标签 → 中文（与 `db_census` 输出用）
PAGE_TAG = {
    "物品总数": "ItemDB_tagTotalNumberOfItems",
    "前缀": "ItemDB_tagItemPrefixes",
    "后缀": "ItemDB_tagItemSuffixes",
    "Ascended Affixes": "ItemDB_tagItemAscendedAffixes",
    "套装": "ItemDB_tagItemSets",
    "专属掉落": "ItemDB_tagMonsterInfrequents",
    "独特稀有": "ItemDB_tagUniqueRares",
    "Awakened Items": "ItemDB_tagAwakenedItems",
    "物品技能修正": "ItemDB_tagItemSkillModifiers",
}

# `Ci()` / `hh()` 里扫的那 5 张表（顺序逐字照抄，虽然求势与顺序无关）
XJ_TABLES = (
    ("allItems", "allItems"),
    ("prefixes", "prefixes"),
    ("suffixes", "suffixes"),
    ("ascendedAffixes", "ascensionAffixData"),   # ← 页面变量名 ≠ window 键名
    ("itemSets", "itemSets"),
)


# ------------------------------------------------------------------ 逐行复算

def overview(db) -> dict:
    """复现「总览」9 行。返回 `{行名: {'local': n, 'raw': 原始量, 'how': 算法说明}}`。"""
    raw = db.raw

    # ① 物品总数 Bj：allItems 去掉 ItemNote 类
    n_note = sum(1 for gid in db.items if db.item_class(gid) == "ItemNote")

    # ② 物品技能修正 Ej：5 表 modifierSkillName* 值去重后的并集
    per_table, vals = {}, set()
    for name, key in XJ_TABLES:
        s = set()
        for rec in (raw.get(key) or {}).values():
            if not isinstance(rec, dict):
                continue
            for k, v in rec.items():
                if k.startswith("modifierSkillName") and v is not None:
                    s.add(v)
        per_table[name] = len(s)
        vals |= s

    rows = {
        "物品总数": {
            "local": len(db.items) - n_note,
            "raw": len(db.items),
            "how": "allItems 减 l:\"ItemNote\"（游戏内书籍/日志）%d 条 —— 页面的 `Bj`" % n_note,
        },
        "前缀": {"local": len(db.prefixes), "raw": len(db.prefixes),
                 "how": "Cj(prefixes) = prefixes 键数"},
        "后缀": {"local": len(db.suffixes), "raw": len(db.suffixes),
                 "how": "Cj(suffixes) = suffixes 键数"},
        "Ascended Affixes": {
            "local": len(raw.get("ascensionAffixData") or {}),
            "raw": len(raw.get("ascensionAffixData") or {}),
            "how": "Cj(ascendedAffixes) —— 页面变量 ascendedAffixes 绑的是 "
                   "window.ascensionAffixData（**不是** 4 键的 ascensionAffixes 配置表）",
        },
        "套装": {"local": len(db.sets), "raw": len(db.sets),
                 "how": "Cj(itemSets) = itemSets 键数"},
        "专属掉落": {"local": len(db.mis), "raw": len(db.mis),
                     "how": "MIs.length（= 怪物专属掉落表 MIRefs 的键集）"},
        "独特稀有": {"local": len(raw.get("uniqueRares") or []),
                     "raw": len(raw.get("uniqueRares") or []),
                     "how": "uniqueRares.length"},
        "Awakened Items": {"local": len(raw.get("awakenedItems") or []),
                           "raw": len(raw.get("awakenedItems") or []),
                           "how": "window.awakenedItems.length"},
        "物品技能修正": {
            "local": len(vals),
            "raw": len(db.skills),
            "how": "5 表 modifierSkillName* 值去重并集 %s ⇒ %d —— 页面的 `Ej`"
                   "；`itemSkills` 全表 %d 是**记录总数**（含未被引用的孤儿）"
                   % ("+".join(str(per_table[k]) for k, _ in XJ_TABLES), len(vals),
                      len(db.skills)),
        },
    }
    return rows, per_table, vals


def unreferenced_skills(db, vals: set) -> list:
    """`itemSkills` 里**没有任何物品/词缀/套装引用**的记录（本地比站点多的那批）。"""
    return sorted(set(db.skills) - {v for v in vals if isinstance(v, str)})


# ------------------------------------------------------------------ 候选池普查

def pool_census(level=71, topn=None, verbose=False) -> dict:
    """优化器**真正拿去搜索**的候选池（`gd/opt.py` 的那几张 POOL_*），逐槽清点。

    ⚠ `gd/opt.py` 在 **import 时**读 `GD_MAX_ILVL` 等环境变量 ⇒ 必须在 import 前设好。
    """
    os.environ["GD_MAX_ILVL"] = str(int(level))
    if topn:
        os.environ["GD_AUTO_TOPN"] = str(int(topn))
    from gd import opt as O
    from gd import DB

    db = DB.load()
    known = set(db.items)
    comp_all = set(db.items)

    out = {}
    for slot in O.SLOTS:
        gids = list(dict.fromkeys(O.POOL_BASE.get(slot) or []))
        bad = [g for g in gids if g not in known]
        offslot = [g for g in gids if g in known and not O.slot_ok(slot, g)]
        comps = list(dict.fromkeys(O.POOL_COMP.get(slot) or []))
        cbad = [g for g in comps if g not in comp_all]
        coff = [g for g in comps if g in comp_all and not O.comp_ok(slot, g)]
        out[slot] = {
            "装备": len(gids), "装备_库外": bad, "装备_槽位校验失败": offslot,
            "镶嵌": len(comps), "镶嵌_库外": cbad, "镶嵌_槽位校验失败": coff,
        }
    aug = list(dict.fromkeys(getattr(O, "POOL_AUG", {}) and
                             [g for v in O.POOL_AUG.values() for g in v] or []))
    return {
        "level": int(level),
        "topn": os.environ.get("GD_AUTO_TOPN"),
        "max_ilvl": os.environ.get("GD_MAX_ILVL"),
        "slots": out,
        "装备合计": sum(v["装备"] for v in out.values()),
        "镶嵌合计": sum(v["镶嵌"] for v in out.values()),
        "库外合计": sum(len(v["装备_库外"]) + len(v["镶嵌_库外"]) for v in out.values()),
        "槽位校验失败合计": sum(len(v["装备_槽位校验失败"]) + len(v["镶嵌_槽位校验失败"])
                               for v in out.values()),
        "附魔样本": len(aug),
    }


# ------------------------------------------------------------------ CLI

def _main(argv=None):
    ap = argparse.ArgumentParser(description="物品库「总览」普查（对拍 grimtools）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--pool", action="store_true", help="追加优化器候选池普查")
    ap.add_argument("--level", type=int, default=71, help="候选池等级上限（默认 71）")
    ap.add_argument("--topn", type=int, default=0, help="每槽候选上限（默认读环境/45）")
    ap.add_argument("--orphans", type=int, default=0,
                    help="打印前 N 条「未被任何物品引用的 itemSkills」")
    a = ap.parse_args(argv)

    from gd import DB
    db = DB.load()
    rows, per_table, vals = overview(db)

    if a.json:
        print(json.dumps({
            "gameVersion": db.game_version,
            "rows": {k: {"local": v["local"], "site": GOLDEN_ZH.get(k),
                         "raw": v["raw"], "how": v["how"]} for k, v in rows.items()},
            "perTableModifierRefs": per_table,
            "orphanSkillCount": len(unreferenced_skills(db, vals)),
        }, ensure_ascii=False, indent=1))
        return 0

    print("物品库「总览」对拍 —— 本地离线库 vs grimtools 站点")
    print("  游戏版本 %s ｜ 站点基准 = 2026-09-20 用户截图" % (db.game_version or "?"))
    print()
    print("| 行 | 站点 | 本地 | 判定 | 基准/来源量 |")
    print("|---|---|---|---|---|")
    bad = 0
    for k in GOLDEN_ZH:
        site = GOLDEN_ZH[k]
        loc = rows[k]["local"]
        raw = rows[k]["raw"]
        ok = loc == site
        bad += 0 if ok else 1
        extra = ("原始量 %s" % format(raw, ",")) if raw != loc else "—"
        print("| %s | %s | **%s** | %s | %s |"
              % (k, format(site, ","), format(loc, ","),
                 "✓ 一致" if ok else "✗ 差 %+d" % (loc - site), extra))
    print()
    print("### 两处「本地 ≠ 原始量」的行 —— 这是**预期**，不是缺数据")
    for k in ("物品总数", "物品技能修正"):
        print("- **%s**：%s" % (k, rows[k]["how"]))
    print()
    orph = unreferenced_skills(db, vals)
    print("> `itemSkills` 全表 **%s** 条，其中 **%d** 条没有任何物品/词缀/套装引用"
          "（孤儿记录，站点不计入「物品技能修正」）。"
          % (format(len(db.skills), ","), len(orph)))
    if a.orphans:
        print("> 样例：%s" % "、".join(orph[:a.orphans]))
    print()

    if a.pool:
        pc = pool_census(a.level, a.topn or None)
        print("### 优化器候选池普查（等级上限 %s ｜ 每槽 topn=%s）"
              % (pc["max_ilvl"], pc["topn"]))
        print()
        print("| 槽位 | 装备候选 | 镶嵌候选 | 库外 id | 槽位校验失败 |")
        print("|---|---|---|---|---|")
        for s, v in pc["slots"].items():
            print("| %s | %d | %d | %d | %d |"
                  % (s, v["装备"], v["镶嵌"],
                     len(v["装备_库外"]) + len(v["镶嵌_库外"]),
                     len(v["装备_槽位校验失败"]) + len(v["镶嵌_槽位校验失败"])))
        print("| **合计** | **%d** | **%d** | **%d** | **%d** |"
              % (pc["装备合计"], pc["镶嵌合计"], pc["库外合计"],
                 pc["槽位校验失败合计"]))
        print()
        for s, v in pc["slots"].items():
            for tag in ("装备_库外", "镶嵌_库外", "装备_槽位校验失败", "镶嵌_槽位校验失败"):
                if v[tag]:
                    print("  ⚠ %s / %s：%s" % (s, tag, "、".join(v[tag][:12])))
        print()

    print("=" * 72)
    print("✓ 9 行全部与站点一致 —— 装备本体一件不少、一件不多。" if not bad
          else "✗ %d 行不一致" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_main())
