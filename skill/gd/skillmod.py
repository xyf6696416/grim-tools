#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""装备 / 套装对技能的「改造」（item & set skill modifiers）+ 套装加成。

GD 里这两块以前完全没建模，是 DPS 偏低的最大原因（实测 Korba 套装给猛袭
**100% 物理→冰冷 + 100% 穿刺→冰冷 + 660 流血**，不算是算不出一半伤害的）。

数据来源
--------
1. **装备自身的改造** → 直接读 dbr：
       modifiedSkillName1..N  = 被改造的技能记录
       modifierSkillName1..N  = 装着改造数据的记录（模板 `Skill_Modifier`）
2. **套装的加成与改造** → dbr 里没有 lootset 记录，只能取 GT 的：
       gt_data/item_sets.json    （`window.itemSets`，199 套）
       gt_data/item_skills.json  （`window.itemSkills`，5156 条，含套装引用的 skNNNN）
3. **星座节点等** → gt_data/e_skills.json（`window.eSkills`）

套装的数组语义（实测 Korba）：**数组下标 i ↔ 集齐 (i+1) 件时生效**，
所以装了 N 件就把下标 0..N-1 全部累加。
"""
import io
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
GT = os.path.join(HERE, 'gt_data')

# ★★★ 武器基础攻速 —— 用 **GT `calc.js` 里的官方公式**（2026-09-17 抠出并实测 8/8 命中）
#
# ```js
# function db(d){ return (!d || cb(d)) ? 1.973684311 : 1.7857143 }   // 类别基准
# cb(d) = Xf.indexOf(category(d)) != -1
# Xf = ["WeaponMelee_Sword","WeaponMelee_Axe","WeaponMelee_Mace",
#       "WeaponMelee_Dagger","WeaponMelee_Scepter"]                  // 单手近战
# function zb(d){ ... return (1 + characterBaseAttackSpeed) * db(d) }
# ```
# 即：**APS = (1 + characterBaseAttackSpeed) × 类别基准**
#   单手近战（剑/斧/锤/匕首/权杖） = 1.973684311 = 75/38
#   其余（双手 / 远程 / 长矛）        = 1.7857143  = 25/14
#
# 实测校验（GT 物品提示框的 `X Attacks per Second`，显示 2 位小数）：
#   单手斧 -0.06 → 1.9737×0.94 = 1.8553 → 1.86 ✓
#   匕首  -0.02 → 1.9737×0.98 = 1.9342 → 1.93 ✓
#   权杖  -0.10 → 1.9737×0.90 = 1.7763 → 1.78 ✓
#   单手枪 -0.05 → 1.7857×0.95 = 1.6964 → 1.70 ✓
#   双手锤 -0.20 → 1.7857×0.80 = 1.4286 → 1.43 ✓
#   双手锤 -0.15 → 1.7857×0.85 = 1.5179 → 1.52 ✓
#
# ⚠ 旧版写死 `TAG_BASE_APS[tag] + delta`（1.25/1.50/1.75/1.90/2.00），
#   **表本身是错的**（tag 只是 delta 的分档显示，不决定基准）。
BASE_APS_1H_MELEE = 1.973684311        # 75/38
BASE_APS_OTHER = 1.7857143             # 25/14
# 属于「单手近战」的武器模板（`templateName` 去掉 `weapon_` 前缀与 `.tpl` 后缀）
_TPL_1H_MELEE = ('sword', 'axe', 'mace', 'dagger', 'scepter')

# 兜底（记录里没有 characterBaseAttackSpeed 字段时，只能按标签粗估）
TAG_BASE_APS = {
    'tagAttackSpeedVerySlow': 1.50,
    'tagAttackSpeedSlow': 1.65,
    'tagAttackSpeedAverage': 1.85,
    'tagAttackSpeedFast': 1.92,
    'tagAttackSpeedVeryFast': 1.96,
}


def weapon_template(db, weapon_rec):
    """武器记录的模板名（去掉 `weapon_` 前缀与 `.tpl`），如 `sword2h` / `dagger`"""
    f = (db.fields(weapon_rec) or {}) if db else {}
    t = (f.get('templateName') or [''])[0]
    t = t.split('/')[-1].replace('.tpl', '')
    return t[7:] if t.startswith('weapon_') else t


def base_aps_of(db, weapon_rec):
    """武器基础攻速（无 speed% 加成时）—— 见文首公式"""
    f = (db.fields(weapon_rec) or {}) if db else {}
    delta = (f.get('characterBaseAttackSpeed') or [None])[0]
    tpl = weapon_template(db, weapon_rec)
    base = BASE_APS_1H_MELEE if tpl in _TPL_1H_MELEE else BASE_APS_OTHER
    if isinstance(delta, (int, float)):
        return base * (1.0 + float(delta))
    tag = (f.get('characterBaseAttackSpeedTag') or [''])[0]
    return TAG_BASE_APS.get(tag, base)

# Skill_Modifier 记录里这些字段是「覆盖」语义，不是累加
_OVERRIDE = ('weaponDamagePct',)


def _load(name, default=None):
    """★★ 2026-09-21 修：**数据文件已迁到 `data/`**（重构时 `gt_data/` 被移走，本文件没跟上）。

    旧实现直接拼 `gt_data/<name>` ⇒ 文件不存在 ⇒ **静默返回 0 条**：
    `item_sets.json`（199 套）与 `item_skills.json`（5156 条）**整整一类加成从模型里消失**
    —— 套装加成（`folded['套装加成']` 根本不生成）与套装技能改造全部漏算，
    而**不报错、不崩、任何自检都看不出来**（陷阱 **#72**）。
    现在走 `gd.paths.load_json`（与全项目同源），`gt_data/` 只作兜底。
    """
    try:
        from . import paths as _P
        d = _P.load_json(name)
        if d:
            return d
    except Exception:                                          # noqa: BLE001
        pass
    p = os.path.join(GT, name)
    if not os.path.isfile(p):
        return default if default is not None else {}
    with io.open(p, encoding='utf-8') as fh:
        return json.load(fh)


def load_sets():
    return _load('item_sets.json')


def load_item_skills():
    return _load('item_skills.json')


def load_e_skills():
    return _load('e_skills.json')


def _num(v):
    """标量 / 单元素数组 → float；其他 → 0"""
    if isinstance(v, bool):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, list) and v and isinstance(v[0], (int, float)):
        return float(v[0])
    return 0.0


# ---------------------------------------------------------------- 套装

def sets_of_items(item_ids, sets=None):
    """{套装 id: 已装备件数}"""
    sets = sets if sets is not None else load_sets()
    have = set(item_ids or [])
    out = {}
    for sid, s in sets.items():
        mem = s.get('setMembers') or []
        n = len([m for m in mem if m in have])
        if n >= 2:
            out[sid] = n
    return out


def set_fields(set_obj, count):
    """把套装里「按件数分档」的**数值字段**累加到 count 件：返回 {字段: 值}

    跳过名字/描述/成员表/技能引用这类非数值键。
    """
    skip = ('setName', 'setDescription', 'setMembers', 'id', 'l', 'f', 'o',
            'k', 'itemLevel', 'mods')
    out = {}
    for k, v in (set_obj or {}).items():
        if k in skip or k.startswith(('modifiedSkillName', 'modifierSkillName',
                                      'augmentSkillName')):
            continue
        if not isinstance(v, list) or not v:
            continue
        tot = 0.0
        for i in range(min(count, len(v))):
            tot += _num(v[i])
        if tot:
            out[k] = out.get(k, 0.0) + tot
    # augmentSkillName{i} + Level{i}：给技能加等级（标签名，调用方自行映射）
    for i in range(1, 6):
        nm = set_obj.get('augmentSkillName%d' % i)
        lv = set_obj.get('augmentSkillLevel%d' % i)
        if isinstance(nm, str) and isinstance(lv, list) and lv:
            tot = 0
            for j in range(min(count, len(lv))):
                tot += int(_num(lv[j]))
            if tot:
                out['__augment__%s' % nm] = tot
    return out


def set_skill_mods(set_obj, count, item_skills=None):
    """套装给技能的改造：返回 {技能名标签: [改造字段 dict, ...]}

    受 `itemSkillModifierControl` 分档开关控制（Korba 是 4 件套才开）。
    """
    item_skills = item_skills if item_skills is not None else load_item_skills()
    ctrl = set_obj.get('itemSkillModifierControl')
    if not ctrl:
        return {}
    if not any(_num(ctrl[i]) for i in range(min(count, len(ctrl)))):
        return {}
    out = {}
    for i in range(1, 9):
        tag = set_obj.get('modifiedSkillName%d' % i)
        sid = set_obj.get('modifierSkillName%d' % i)
        if not tag or not sid:
            continue
        mod = item_skills.get(sid)
        if isinstance(mod, dict):
            out.setdefault(tag, []).append(mod)
    return out


# ---------------------------------------------------------------- 装备

def item_skill_mods(db, records):
    """装备自身的技能改造：返回 {技能记录名: [改造字段 dict, ...]}"""
    out = {}
    for rec in (records or []):
        if not rec:
            continue
        f = db.fields(rec) or {}
        for i in range(1, 6):
            tgt = (f.get('modifiedSkillName%d' % i) or [None])[0]
            src = (f.get('modifierSkillName%d' % i) or [None])[0]
            if not tgt or not src:
                continue
            if not (isinstance(tgt, str) and tgt.startswith('records/')):
                continue
            mf = db.fields(src)
            if mf:
                out.setdefault(tgt, []).append(mf)
    return out


# ---------------------------------------------------------------- 合并

def merge_modifiers(fields, mods, level=1):
    """把若干改造字段合并进技能字段，返回**新的字段副本**。

    规则：
      * `offensiveXxxMin/Max`（含 Slow 变体）→ **累加**
      * `offensiveXxxModifier` → 累加
      * `skillCooldownTime` → 累加（负值 = 减冷却）
      * `weaponDamagePct` → 取大（覆盖语义）
      * `conversionInType/OutType/Percentage` → 收集（调用方拼接）
    """
    out = dict(fields or {})
    for m in (mods or []):
        for k, v in m.items():
            if not isinstance(v, list) or not v or not any(v):
                continue
            if k in _OVERRIDE:
                cur = _num(out.get(k))
                out[k] = [max(cur, _num(v))]
            elif k in ('skillCooldownTime', 'skillActiveDuration',
                       'offensiveSlowBleedingDurationMin'):
                cur = _num(out.get(k))
                out[k] = [cur + _num(v)]
            elif k.startswith('offensive') or k.startswith('character'):
                cur = _num(out.get(k))
                out[k] = [cur + _num(v)]
    return out


def _str(v):
    """dbr 字段是长度 1 的列表、GT 数据是裸字符串 —— 都取出来"""
    if isinstance(v, str):
        return v or None
    if isinstance(v, list) and v and isinstance(v[0], str):
        return v[0] or None
    return None


def mod_conversions(mods):
    """改造里带的**专属转化**（只对该技能生效）→ [(in, out, pct)]

    ⚠ dbr 读出来的字段是**列表**（`['Physical']`），GT 的 item_skills 是裸字符串 ——
    两种都要吃得下（踩过：只判 str 会让装备自带的转化整批失效）。
    """
    out = []
    for m in (mods or []):
        i = _str(m.get('conversionInType'))
        o = _str(m.get('conversionOutType'))
        p = _num(m.get('conversionPercentage'))
        if i and o and p:
            out.append((i.lower(), o.lower(), float(p)))
    return out


def mod_pct(mods):
    """改造里带的 **% 加成**（只对该技能生效）→ {类型: 值}"""
    out = {}
    for m in (mods or []):
        for k, v in m.items():
            if k.endswith('Modifier') and k.startswith('offensive'):
                x = _num(v)
                if x:
                    t = k[len('offensive'):-len('Modifier')]
                    t = t[0].lower() + t[1:]
                    out[t] = out.get(t, 0.0) + x
    return out


# ---------------------------------------------------------------- 其他

def weapon_base_aps(db, weapon_rec, tooltip_aps=None):
    """武器**基础攻速**：优先用提示框实测值，否则按官方公式（`base_aps_of`）。

    旧版写死 1.25（或 `TAG_BASE_APS[tag] + delta`），实测单手斧应为 1.8553、
    双手锤 1.4286 —— 旧表**整体偏低**，DPS 直接差 20%+。
    """
    if tooltip_aps:
        return float(tooltip_aps)
    return base_aps_of(db, weapon_rec)


def parse_tooltip_aps(text):
    """从 GT 物品提示框里抠出 `X Attacks per Second`"""
    m = re.search(r'([0-9.]+)\s*Attacks per Second', text or '')
    return float(m.group(1)) if m else None


def extra_skill_mods(direct, tag_mods, tag_to_rec):
    """把「套装给的、以**名标签**为键的改造」映射到记录名后并进 `direct`

    tag_mods    : {技能名标签: [mod, ...]}（来自 `set_skill_mods`）
    tag_to_rec  : {技能名标签: 记录名}（即 `gt_build_dps.tag_to_record()` 的输出）
    """
    out = {k: list(v) for k, v in (direct or {}).items()}
    for tag, mods in (tag_mods or {}).items():
        rec = (tag_to_rec or {}).get(tag)
        if rec:
            out.setdefault(rec, []).extend(mods)
    return out


def tag_to_record(skills=None):
    """{技能名标签: 记录名} —— 同名 tag 优先取 `playerclass` 下的（敌人变体很多）"""
    skills = skills if skills is not None else _load('skills.json', {})
    out = {}
    for rec, d in (skills or {}).items():
        t = (d.get('tag') or '').strip()
        if not t:
            continue
        cur = out.get(t)
        if cur is None or ('/playerclass' in rec and '/playerclass' not in cur):
            out[t] = rec
    return out


def plan_extras(plan, db, items, skills=None):
    """方案 JSON（GT id）→ **套装加成字段 + 技能改造 + 套装给的技能等级**

    返回 (set_bucket, skill_mods, found_sets, augment_levels)
      set_bucket      : {字段: 值} —— 调用方塞进 `folded['套装加成']`
      skill_mods      : {技能记录: [改造字段]} —— 传给 `gd_rotation.final_report`
      found_sets      : {套装 id: 件数}
      augment_levels  : {技能记录: +等级}（来自套装，叠加到技能等级上）
    """
    from . import savemap as M
    sets, itk = load_sets(), load_item_skills()
    t2r = tag_to_record(skills)
    gids = [g for row in (plan or {}).values() for g in (row or []) if g]
    base = [row[0] for row in (plan or {}).values() if row and row[0]]
    recs = []
    for g in gids:
        try:
            rec, _w = M.resolve((items or {}).get(g) or {})
        except Exception:
            rec = None
        if rec:
            recs.append(rec)

    bucket, tagmods, found, aug = {}, {}, {}, {}
    for sid, cnt in sets_of_items(base, sets).items():
        so = sets[sid]
        found[sid] = cnt
        for k, v in set_fields(so, cnt).items():
            if k.startswith('__augment__'):
                rec = t2r.get(k[len('__augment__'):])
                if rec:
                    aug[rec] = aug.get(rec, 0) + int(v)
                continue
            if k == 'itemSkillModifierControl':
                continue
            bucket[k] = bucket.get(k, 0.0) + float(v)
        for tag, ms in set_skill_mods(so, cnt, itk).items():
            tagmods.setdefault(tag, []).extend(ms)

    mods = item_skill_mods(db, recs) if db else {}
    mods = extra_skill_mods(mods, tagmods, t2r)
    return bucket, mods, found, aug
