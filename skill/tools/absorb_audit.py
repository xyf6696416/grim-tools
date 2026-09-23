"""伤害吸收来源全库审计（% 吸收 + 点数吸收）。

    python tools/absorb_audit.py                 # 打到屏幕
    python tools/absorb_audit.py --write         # 写 data/absorption_sources.json + .md
    python tools/absorb_audit.py --check         # 与已存表比对（自检用；漂移则退出码 1）
    python tools/absorb_audit.py --show-dropped  # 打印被判为「非物品技能」而剔除的行

★ 为什么必须单独有个工具：吸收字段散在**四个表**里，而且**没有一张是全的** ——

| 表 | 有什么 | 缺什么 |
|---|---|---|
| `data/skills.json`（12372 条全技能） | 记录路径 / class / name / tag / template | `stats` **被裁剪过**，不含 `damageAbsorptionPercent` |
| `data/calc_mastery_skills.json` | 专精 + **51/63** 个星座授予技能的实际数值 | 缺 12 个星座技能；无物品技能 |
| `data/devotions.json`（716 星位） | 星位**被动**属性（有 `stats`） | 不含授予技能记录 |
| `data/item_skills.json`（5156 条） | 物品授予技能的实际数值 | 键是 `skXXXX`，且**混装了专精/星座技能的副本** |

⇒ **必须四表并集 + 按来源去重**。实测教训：只扫 `calc_mastery_skills.json` 会把星座来源判成
「只有 1 个」并漏掉全部物品来源（第一版就是这么错的）。

三个容易搞错的口径：
* **百分比吸收是乘算层**（`1 - Π(1-aᵢ)`，永远 < 100%）；**点数吸收是加算层**，排在减伤链**最后**。
* `Skill_Modifier`（template `t88`）记录**自己不写目标技能** —— 目标在**物品**上，
  靠 `modifierSkillName<N>` ↔ `modifiedSkillName<N>` 成对出现（后者是 tag）。
* `data/item_skills.json` 里同一批 `sk` 也包含**专精技能**（如 `sk2144` = 折磨印记）和
  **星座授予技能**（如 `sk2072` = 自然的守护者）的副本 ⇒ 必须用 `db.mastery`（专精 ID 全集）
  与「记录路径含 `/itemskills`」两条判据剔除，否则会把专精/星座算成装备。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gd import DB, paths                              # noqa: E402

PCT = "damageAbsorptionPercent"
FLAT = "damageAbsorption"
OUT_JSON = paths.DATA_DIR / "absorption_sources.json"
OUT_MD = paths.DATA_DIR / "absorption_sources.md"

LAYER_CN = {"devotion": "星座", "mastery": "专精", "item": "装备"}
CLASS_TAG = re.compile(r"tag(?:GDX\d)?Class(\d+)Skill")


# ------------------------------------------------------------------ 小工具
def _nums(v):
    if isinstance(v, list):
        return [x for x in v if isinstance(x, (int, float))]
    return [v] if isinstance(v, (int, float)) else []


def _min(v):
    n = _nums(v)
    return min(n) if n else None


def _max(v):
    n = _nums(v)
    return max(n) if n else None


def _row(layer, kind, name, owner, ident, values, template="", role="skill", note=""):
    return {
        "layer": layer,
        "layer_cn": LAYER_CN.get(layer, layer),
        "kind": kind,
        "role": role,
        "name": name or ident,
        "owner": owner or "",
        "id": ident,
        "template": template,
        "min": _min(values),
        "max": _max(values),
        "values": values if isinstance(values, (int, float)) else list(values or []),
        "note": note,
    }


# ------------------------------------------------------------------ 索引
def _load(name):
    return json.loads((paths.DATA_DIR / name).read_text(encoding="utf-8"))


def _devotion_owner_names(db) -> dict:
    """star_prefix(tier3_15) -> 星座中文名。"""
    out = {}
    try:
        t = _load("devotion_tree.json")
    except Exception:                                          # noqa: BLE001
        return out
    for o in t.values():
        sp, tag = o.get("star_prefix"), o.get("tag")
        if not sp:
            continue
        try:
            out[sp] = db.text(tag) or tag
        except Exception:                                      # noqa: BLE001
            out[sp] = tag or ""
    return out


def _class_names(db) -> dict:
    """class04 -> 夜刃（从 isMastery 节点的 tag 反查中文名）。"""
    out = {}
    for sk, o in (db.mastery or {}).items():
        if not isinstance(o, dict) or not o.get("isMastery"):
            continue
        tag = o.get("name") or ""
        m = CLASS_TAG.match(tag)
        if not m:
            continue
        try:
            nm = db.text(tag) or ""
        except Exception:                                      # noqa: BLE001
            nm = ""
        if nm:
            out["class%02d" % int(m.group(1))] = nm
    return out


def _sk_tag(db, sk) -> str:
    """物品技能的 tag（`skillDisplayName` / `skillBaseDescription`）。"""
    o = db.skills.get(sk) or {}
    for k in ("skillDisplayName", "skillBaseDescription"):
        v = o.get(k)
        if isinstance(v, str) and v:
            return v[:-4] if v.endswith("Desc") else v
    return ""


def _item_owner_map(db) -> dict:
    """skXXXX -> {"granted_by": {物品名}, "modifies": {目标技能名}}。

    ★ 必须**深度遍历**（有些物品把技能引用放在嵌套结构里），
      且 `modifiedSkillName<N>` 要回到**物品根记录**上取。
    """
    own = {}

    def note(sk, field, val):
        if not val:
            return
        own.setdefault(sk, {"granted_by": set(), "modifies": set()})[field].add(val)

    def walk(node, root, iname):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and v.startswith("sk"):
                    if k == "itemSkillName":
                        note(v, "granted_by", iname)
                    elif k.startswith("modifierSkillName"):
                        idx = k[len("modifierSkillName"):]
                        tg = root.get("modifiedSkillName" + idx) if isinstance(root, dict) else None
                        if tg:
                            try:
                                note(v, "modifies", db.text(tg) or tg)
                            except Exception:                      # noqa: BLE001
                                note(v, "modifies", tg)
                walk(v, root, iname)
        elif isinstance(node, list):
            for v in node:
                walk(v, root, iname)

    for k, v in _owner_sources(db):
        if not isinstance(v, dict):
            continue
        try:
            nm = db.name(k) or k
        except Exception:                                      # noqa: BLE001
            nm = k
        walk(v, v, nm)
    return own


def _owner_sources(db):
    """归属索引要扫的集合 —— 只扫 `items` 会漏掉词缀/套装授予的技能。"""
    for name in ("items", "prefixes", "suffixes", "sets", "mis",
                 "quest_items", "interaction_items", "container_refs"):
        g = getattr(db, name, None)
        if isinstance(g, dict):
            for k, v in g.items():
                yield k, v


def _resolve_modifier_targets_raw(db, sk_ids) -> dict:
    """兜底：直接扫**原始 itemdb 文本**补 `modifiedSkillName<N>` 配对。

    ★ 为什么需要：有一部分 `Skill_Modifier` 挂在 **`aa*` 记录（附魔/词缀）** 上，
      而 `aa*` **不在 `DB` 暴露的任何集合里**（`db.items`/`prefixes`/`suffixes`/`sets` 都查不到）
      ⇒ 只靠 DB 反查会留下一堆「目标未解析」。原始 JS 里字段是成对相邻的：
      `modifiedSkillName1:"tagClass01SkillName08A",modifierSkillName1:"sk4656"`
    """
    out = {}
    p = paths.CACHE_DIR / "itemdb.js"
    if not p.exists() or not sk_ids:
        return out
    s = p.read_text(encoding="utf-8", errors="replace")
    for sk in sk_ids:
        m = re.search(r'modifierSkillName(\d+):"%s"[,\}]' % re.escape(sk), s)
        if not m:
            continue
        n = m.group(1)
        back = s[max(0, m.start() - 900):m.start()]
        tgt, tg = "", ""
        # ★ 必须取**最近一处**（findall[-1]）—— 用 re.search 会抓到窗口里更早的**别的记录的**配对。
        ts = re.findall(r'modifiedSkillName%s:"([^"]+)"' % n, back)
        if ts:
            tg = ts[-1]
            try:
                tgt = db.text(tg) or tg
            except Exception:                                  # noqa: BLE001
                tgt = tg
        keys = re.findall(r'([A-Za-z0-9_]{3,}):\{', back)
        rawo = keys[-1] if keys else ""
        own = ""
        if rawo:
            rs = s.rfind(rawo + ":{", 0, m.start())
            seg = s[rs:rs + 900] if rs >= 0 else ""
            if "LootRandomizer" in seg:
                own = "随机词缀池"          # `aa*` = LootRandomizer ⇒ 绿装/掉落的随机词缀
            else:
                nm = re.search(r'\ba:"([^"]+)"', seg) or re.search(r'\bj:"([^"]+)"', seg)
                if nm:
                    try:
                        own = db.text(nm.group(1)) or nm.group(1)
                    except Exception:                          # noqa: BLE001
                        own = nm.group(1)
        out[sk] = {"target": tgt, "target_tag": tg, "raw_owner": own or rawo}
    return out


# ------------------------------------------------------------------ 采集
def collect(db) -> dict:
    calc = _load("calc_mastery_skills.json")
    skills = _load("skills.json")
    devs = _load("devotions.json")
    iskills = _load("item_skills.json")
    mskills = _load("mastery_skills.json")

    deco = _devotion_owner_names(db)
    clsn = _class_names(db)
    iown = _item_owner_map(db)
    mastery_ids = set((db.mastery or {}).keys())

    tag2rec = {}
    for rec, o in skills.items():
        t = o.get("tag")
        if t:
            tag2rec.setdefault(t, []).append(rec)

    def recs_of(sk):
        t = _sk_tag(db, sk)
        return tag2rec.get(t, []) if t else []

    rows, dropped = [], []
    counts = {}

    # --- 1. 星座授予技能 -------------------------------------------------
    dev_recs = sorted(r for r in skills
                      if r.startswith("records/skills/devotion/") and r.endswith("_skill.dbr"))
    counts["devotion_skill_records"] = len(dev_recs)
    n_hit = 0
    for rec in dev_recs:
        st = calc.get(rec) or {}
        p, f = st.get(PCT), st.get(FLAT)
        if p is None and f is None:
            continue
        n_hit += 1
        stem = rec.rsplit("/", 1)[-1].replace("_skill.dbr", "")
        mm = re.match(r"(tier\d_\d+)", stem)
        owner = deco.get(stem) or (deco.get(mm.group(1)) if mm else "") or ""
        if not owner:
            # ★ 兜底：授予技能的 tag 是 `tag…DevotionEffect<C102>`，
            #   把 `Effect` 换成 `_` 就是**星座自己的 tag** ⇒ 可反查星座名。
            tg = skills[rec].get("tag") or ""
            if "DevotionEffect" in tg:
                try:
                    owner = db.text(tg.replace("DevotionEffect", "Devotion_")) or ""
                except Exception:                              # noqa: BLE001
                    owner = ""
        name = skills[rec].get("name") or stem
        tmpl = skills[rec].get("template") or ""
        if p is not None:
            rows.append(_row("devotion", "percent", name, owner, rec, p, tmpl))
        if f is not None:
            rows.append(_row("devotion", "flat", name, owner, rec, f, tmpl))
    counts["devotion_skill_with_absorption"] = n_hit

    # --- 2. 星座被动星位（没有授予技能的节点） ----------------------------
    n_pass = 0
    for rec, o in devs.items():
        st = (o or {}).get("stats") or {}
        for key, kind in ((PCT, "percent"), (FLAT, "flat")):
            v = st.get(key)
            if v is None:
                continue
            n_pass += 1
            rows.append(_row("devotion", kind, o.get("name") or rec, "", rec, v, "Skill_Passive",
                             note="星位被动"))
    counts["devotion_passive_with_absorption"] = n_pass

    # --- 3. 专精技能 -----------------------------------------------------
    for _tag, o in mskills.items():
        rec = o.get("record")
        if not rec:
            continue
        st = calc.get(rec) or {}
        cid = o.get("class") or ""
        for key, kind in ((PCT, "percent"), (FLAT, "flat")):
            v = st.get(key)
            if v is None:
                continue
            rows.append(_row("mastery", kind, o.get("name") or rec,
                             clsn.get(cid, cid), rec, v, o.get("template") or ""))

    # --- 4. 物品技能（含 Skill_Modifier），按来源去重 --------------------
    mod_ids = [sk for sk, o in iskills.items()
               if isinstance(o, dict) and o.get("l") == "Skill_Modifier"
               and (o.get(PCT) is not None or o.get(FLAT) is not None)]
    rawmod = _resolve_modifier_targets_raw(db, mod_ids)
    for sk, o in iskills.items():
        if not isinstance(o, dict):
            continue
        p, f = o.get(PCT), o.get(FLAT)
        if p is None and f is None:
            continue
        role = "modifier" if o.get("l") == "Skill_Modifier" else "skill"
        ow = iown.get(sk) or {}
        recs = recs_of(sk)
        is_item_rec = any("/itemskills" in r for r in recs)
        # 专精技能副本 / 星座技能副本 ⇒ 归到专精/星座层，不进物品层
        if role != "modifier" and (sk in mastery_ids or not is_item_rec):
            dropped.append({"id": sk, "pct": p, "flat": f,
                            "why": "专精技能副本" if sk in mastery_ids else "非物品技能记录",
                            "recs": recs[:2]})
            continue
        granted = sorted(x for x in ow.get("granted_by", set()) if x)
        owner = "、".join(granted[:4])
        if not owner and role != "modifier":
            # 消耗品/特殊物品的技能记录不在 DB 暴露的任何集合里（如「虚化集簇」）
            # ⇒ 技能名本身就是物品名，标成「物品本体」而不是留空。
            owner = "（物品本体）"
        kindvals = [("percent", p), ("flat", f)]
        for kind, v in kindvals:
            if v is None:
                continue
            note = ""
            tmpl = o.get("templateName") or ""
            if role == "modifier":
                tgt = "、".join(sorted(x for x in ow.get("modifies", set()) if x))
                rm = rawmod.get(sk) or {}
                if not tgt and rm.get("target"):
                    tgt = rm["target"]
                if not owner and rm.get("raw_owner"):
                    owner = rm["raw_owner"]
                nm = "+%s%% → %s" % (_max(v), tgt or "（目标未解析）")
                note = "加在：%s" % (tgt or "（目标未解析）")
            else:
                try:
                    nm = db.name(sk) or sk
                except Exception:                              # noqa: BLE001
                    nm = sk
            rows.append(_row("item", kind, nm, owner, sk, v, tmpl, role=role, note=note))

    # --- 事实块（自检只认这几个数） --------------------------------------
    def _mx(sel):
        vals = [r["max"] for r in rows if sel(r) and r["max"] is not None]
        return max(vals) if vals else None

    def is_l(lyr, knd):
        return lambda r: r["layer"] == lyr and r["kind"] == knd

    facts = {
        "devotion_skill_records": counts["devotion_skill_records"],
        "devotion_skill_with_absorption": counts["devotion_skill_with_absorption"],
        "devotion_passive_with_absorption": counts["devotion_passive_with_absorption"],
        "devotion_pct_max": _mx(is_l("devotion", "percent")),
        "devotion_flat_max": _mx(is_l("devotion", "flat")),
        "mastery_pct_max": _mx(is_l("mastery", "percent")),
        "mastery_flat_max": _mx(is_l("mastery", "flat")),
        "item_pct_max": _mx(is_l("item", "percent")),
        "item_flat_max": _mx(is_l("item", "flat")),
        "pct_sources_lt100": len([r for r in rows if r["kind"] == "percent"
                                  and r["max"] is not None and 0 < r["max"] < 100]),
        "pct_sources_eq100": len([r for r in rows if r["kind"] == "percent" and r["max"] == 100]),
        "dropped_non_item": len(dropped),
    }
    sus = sorted((r for r in rows if r["kind"] == "percent" and (r["max"] or 0) < 100),
                 key=lambda r: -(r["max"] or 0))
    facts["pct_top_name"] = sus[0]["name"] if sus else ""
    facts["pct_top_value"] = sus[0]["max"] if sus else None
    facts["pct_top_owner"] = sus[0]["owner"] if sus else ""

    rows.sort(key=lambda r: (r["layer"], r["kind"], -(r["max"] or 0), r["name"]))
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "game_version": db.game_version,
        "counts": counts,
        "facts": facts,
        "rows": rows,
        "dropped": sorted(dropped, key=lambda d: -(_max(d["pct"]) or _max(d["flat"]) or 0)),
    }


# ------------------------------------------------------------------ 输出
def render_text(doc, show_dropped=False) -> str:
    out = ["=" * 78, "伤害吸收来源全库审计", "=" * 78,
           "游戏版本 %s   ｜   %s" % (doc["game_version"], doc["generated_at"]), ""]
    for layer, cn in (("devotion", "星座"), ("mastery", "专精"), ("item", "装备")):
        for kind, kcn in (("percent", "% 吸收（乘算层）"), ("flat", "点数吸收（加算层）")):
            sel = [r for r in doc["rows"] if r["layer"] == layer and r["kind"] == kind]
            if not sel:
                continue
            out.append("--- %s / %s（%d 条）---" % (cn, kcn, len(sel)))
            for r in sel:
                rng = "%s" % r["min"] if r["min"] == r["max"] else "%s -> %s" % (r["min"], r["max"])
                own = ("@ " + r["owner"]) if r["owner"] else ""
                extra = ("   ｜ " + r["note"]) if r["note"] else ""
                out.append("  %-24s %-13s %-18s%s" % (str(r["name"])[:24], rng, own, extra))
            out.append("")
    if show_dropped:
        out.append("--- 被剔除（判为非物品技能）%d 条 ---" % len(doc["dropped"]))
        for d in doc["dropped"]:
            out.append("  %-9s pct=%-5s flat=%-7s %s" % (d["id"], d["pct"], d["flat"], d["why"]))
        out.append("")
    out.append("--- 关键事实 ---")
    for k, v in doc["facts"].items():
        out.append("  %-32s %s" % (k, v))
    return "\n".join(out)


def render_md(doc) -> str:
    f = doc["facts"]
    L = ["# 伤害吸收来源核查表", "",
         "> 由 `tools/absorb_audit.py --write` 生成，**不要手改**。",
         "> 游戏版本 `%s` ｜ 生成于 %s" % (doc["game_version"], doc["generated_at"]), "",
         "## 口径（先看这三条）", "",
         "1. **% 吸收 = 乘算层**：`总吸收 = 1 - (1-a₁)(1-a₂)…` ⇒ **永远 < 100%**，",
         "   除非单一来源本身就是 100%（那种都是短时无敌窗）。",
         "2. **点数吸收 = 加算层**，且排在减伤链**最后**结算 ⇒ 「伤害被减到 0」靠它，不靠 % 吸收。",
         "3. 星座的「点数池」（`Skill_BuffSelfShield`）就是点数吸收；物品的 `Skill_Modifier`",
         "   是**加到某个已有技能上**的，不是独立来源。", ""]

    lay = {"devotion": "星座", "mastery": "专精", "item": "装备"}
    pct = sorted([r for r in doc["rows"] if r["kind"] == "percent"],
                 key=lambda r: -(r["max"] or 0))
    L += ["## ★ 乘算层一览（% 伤害吸收）—— 全部 {} 条，按最高值排序".format(len(pct)), "",
          "| 最高 | 1 级 → 满级 | 来源 | 层 · 形态 | 归属 / 加在 | 备注 |",
          "|---|---|---|---|---|---|"]
    for r in pct:
        rng = "%s" % r["min"] if r["min"] == r["max"] else "%s → %s" % (r["min"], r["max"])
        role = "修正器" if r["role"] == "modifier" else "独立"
        L.append("| **%s** | %s | %s | %s · %s | %s | %s |"
                 % (r["max"], rng, r["name"], lay.get(r["layer"], r["layer"]), role,
                    r["owner"] or "—", r["note"] or "—"))
    L += ["", "> 乘算示例：死灵「折磨印记」50% + 狂战士「不羁狂怒」40% = **70%**（不是 90%）。",
          "> 100% 的三条（刀锋壁障 / 艾瑞奥特之镜 / 圣光）都是**短时无敌窗**。", ""]
    for layer, cn in (("devotion", "星座"), ("mastery", "专精"), ("item", "装备")):
        for kind, kcn in (("percent", "% 伤害吸收"), ("flat", "点数伤害吸收")):
            sel = [r for r in doc["rows"] if r["layer"] == layer and r["kind"] == kind]
            if not sel:
                continue
            L.append("### %s · %s（%d 条）" % (cn, kcn, len(sel)))
            L.append("")
            L.append("| 来源 | 归属 | 1 级 → 满级 | 最高 | 形态 | 备注 |")
            L.append("|---|---|---|---|---|---|")
            for r in sel:
                rng = "%s" % r["min"] if r["min"] == r["max"] else "%s → %s" % (r["min"], r["max"])
                role = {"modifier": "修正器", "skill": "独立技能"}.get(r["role"], r["role"])
                L.append("| %s | %s | %s | **%s** | %s | %s |"
                         % (r["name"], r["owner"] or "—", rng, r["max"], role, r["note"] or "—"))
            L.append("")
    L += ["## 关键事实", "", "| 键 | 值 |", "|---|---|"]
    for k, v in f.items():
        L.append("| `%s` | %s |" % (k, v))
    L.append("")
    if doc["dropped"]:
        L += ["## 被剔除的行（判为专精/星座技能副本，不计入装备层）", "",
              "| ID | % 吸收 | 点数吸收 | 判据 |", "|---|---|---|---|"]
        for d in doc["dropped"]:
            L.append("| `%s` | %s | %s | %s |" % (d["id"], d["pct"], d["flat"], d["why"]))
        L.append("")
    return "\n".join(L)


def render_percent(doc) -> str:
    """只列**乘算层**（% 伤害吸收）—— 用户最常问的那一张。"""
    pct = [r for r in doc["rows"] if r["kind"] == "percent"]
    out = ["=" * 78, "乘算的伤害吸收（% Damage Absorption）全清单", "=" * 78,
           "游戏版本 %s   ｜   %s" % (doc["game_version"], doc["generated_at"]), "",
           "★ 叠加规则：总吸收 = 1 - (1-a1)(1-a2)…  ⇒ 乘算，**永远 < 100%**",
           "★ 只要不是单一来源给 100%，加到再多也到不了 100%；",
           "   真正「把伤害减到 0」靠**点数吸收**（加算、且在减伤链最后）。", ""]
    groups = [
        ("星座 proc（全库只有这 1 个 % 吸收来源）", lambda r: r["layer"] == "devotion"),
        ("专精技能", lambda r: r["layer"] == "mastery"),
        ("装备授予的独立技能 / 触发 proc",
         lambda r: r["layer"] == "item" and r["role"] == "skill"),
        ("物品修正器（★ 加到已有技能上，不是独立来源）",
         lambda r: r["layer"] == "item" and r["role"] == "modifier"),
    ]
    for title, sel in groups:
        rs = sorted([r for r in pct if sel(r)], key=lambda r: -(r["max"] or 0))
        if not rs:
            continue
        out.append("--- %s（%d 条）---" % (title, len(rs)))
        for r in rs:
            v = "%s" % r["max"] if r["min"] == r["max"] else "%s -> %s" % (r["min"], r["max"])
            tgt = r["owner"] or "—"
            extra = ("  " + r["note"]) if r["note"] and r["role"] == "modifier" else ""
            out.append("  %-24s %-12s %-16s%s" % (str(r["name"])[:24], v, tgt, extra))
        out.append("")
    sus = sorted((r for r in pct if (r["max"] or 0) < 100), key=lambda r: -(r["max"] or 0))
    out.append("--- 口径提醒 ---")
    out.append("  · 100% 的 3 条（刀锋壁障 / 艾瑞奥特之镜 / 圣光）都是**短时无敌窗**，不是常驻。")
    out.append("  · 非 100% 里，单条最高是 **巫妖守卫 80%**（装备 proc，低血触发）。")
    if sus:
        out.append("  · 乘算示例：折磨印记 50% + 不羁狂怒 40% = {:.1f}%（不是 90%）"
                   .format((1 - (1 - 0.5) * (1 - 0.4)) * 100))
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="伤害吸收来源全库审计")
    ap.add_argument("--write", action="store_true", help="写 data/absorption_sources.json + .md")
    ap.add_argument("--check", action="store_true", help="与已存表比对，漂移则退出码 1")
    ap.add_argument("--show-dropped", action="store_true", help="打印被剔除的行")
    ap.add_argument("--percent", action="store_true", help="只列乘算层（% 伤害吸收）全清单")
    a = ap.parse_args()

    db = DB.load()
    doc = collect(db)

    if a.percent:
        print(render_percent(doc))
        return 0

    if a.write:
        OUT_JSON.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        OUT_MD.write_text(render_md(doc), encoding="utf-8")
        print("已写 %s（%d 行）" % (OUT_JSON, len(doc["rows"])))
        print("已写 %s" % OUT_MD)
        return 0

    if a.check:
        if not OUT_JSON.exists():
            print("✗ 缺 %s（先跑 --write）" % OUT_JSON)
            return 1
        old = json.loads(OUT_JSON.read_text(encoding="utf-8"))
        bad = []
        for k in sorted(set(old.get("facts", {})) | set(doc["facts"])):
            if old.get("facts", {}).get(k) != doc["facts"].get(k):
                bad.append("%s: %s -> %s" % (k, old.get("facts", {}).get(k), doc["facts"].get(k)))

        def sig(d):
            return [(r["layer"], r["kind"], r["role"], r["name"], r["owner"],
                     r["min"], r["max"], r["note"]) for r in d["rows"]]

        if sig(old) != sig(doc):
            a, b = sig(old), sig(doc)
            only_old = [x[3] for x in a if x not in b][:6]
            only_new = [x[3] for x in b if x not in a][:6]
            bad.append("行内容不一致：旧有 %s ／ 新有 %s" % (only_old or "—", only_new or "—"))
        ok = not bad
        print(("✓ " if ok else "✗ ") + "吸收来源表与离线库一致（%d 行）" % len(doc["rows"]))
        for b in bad:
            print("   " + b)
        return 0 if ok else 1

    print(render_text(doc, a.show_dropped))
    return 0


if __name__ == "__main__":
    sys.exit(main())
