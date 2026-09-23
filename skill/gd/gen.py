# -*- coding: utf-8 -*-
r"""完整 BD 生成器（bd_gen）—— 2026-09-17 新增

把「装备方案（gd_opt 输出）」补全成一套**完整可落档 BD**：

```
装备方案 ─┬─ gd_alloc.attribute_plan   → 属性点分配 + 面板
          ├─ gd_alloc.allocate         → 技能加点（严格点数预算）
          ├─ gd_devotion.select_coherent → 星座（亲和力自洽）
          ├─ gd_rotation.analyze       → 输出循环 DPS 分解
          └─ gd_plan_export            → gd_build 可写方案
```

产出两份文件：
  · `<out>_profile.json`  —— 交给 `gd_profile.py` 落档
  · `<out>.md`            —— 可读 BD 报告

用法
----
```bash
python bd_gen.py plans/sn75_dmg.json --arch soldier_nightblade --level 75 --name "士兵+夜刃 双持物理"
```
"""
import argparse
import json
import os
import re
import sys

from . import paths as _PATHS

# 旧脚本用 HERE 拼数据文件路径；新架构下数据在技能的 data/ 里
HERE = str(_PATHS.DATA_DIR)
_CACHE_ROOT = str(_PATHS.CACHE_DIR)
_PLANS = str(_PATHS.CACHE_DIR / 'plans')



SLOT_ORDER = ['头部', '项链', '胸甲', '腿甲', '靴子', '手套', '戒指1', '戒指2',
              '腰带', '肩甲', '勋章', '圣物', '主手', '副手']

RARITY_ZH = {'Rare': '绿', 'Epic': '蓝', 'Legendary': '紫'}


_SKLIB = None


def _skills_lib():
    """技能名库：tag / 记录名 → 中文名"""
    global _SKLIB
    if _SKLIB is None:
        p = os.path.join(HERE, 'skills.json')
        _SKLIB = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else {}
    return _SKLIB


def zh_name(gid, tags=None, items=None):
    """装备显示名：优先 GT 的**名 tag**（`a`），其次位图基名，最后 gid。

    ⚠ 别用 `b` 字段 —— 它的内容在 GT 里是**描述**（实测会显示成
    "著名的战斗大师佐尔汉穿过的战甲。" 这种句子）；中文名缺失时 `a` 也常是
    `tagGDX3HeadB302` 这类未翻译 tag，此时退回**位图基名**更可读。
    """
    from . import gear as G
    items = items if items is not None else G.load_items()
    tags = tags if tags is not None else G.load_tags()
    o = items.get(gid) or {}
    # ★ 名 tag 不只在 `a`：**圣物**把名放在 `d`（`a` 为空），例 it1489 →
    #   `d=tagRelicC012`（译名「热诚」）、`b=tagRelicC012Desc`（描述）。
    #   判据：t 值不含 desc、译文短且不以句号结尾 ⇒ 是名而非描述。
    #
    # ★ 还要剥掉 GD 的**颜色代码**：组件/附魔的译文里常带 `^k` `^r` 这类前缀
    #   （实测「镶嵌」列显示成 `^k活体护甲`），`^` + 单字符即为一段颜色标记。
    for key in ('a', 'd'):
        t = o.get(key)
        if not (isinstance(t, str) and t.startswith('tag')):
            continue
        if 'desc' in t.lower():
            continue
        v = tags.get(t)
        if v:
            v = re.sub(r'\^[A-Za-z_]', '', v).strip()
        if v and len(v) <= 24 and not v.endswith('。'):
            return v
    bmp = o.get('n') or ''
    base = os.path.basename(bmp).replace('.png', '').replace('.tex', '')
    if base:
        return '%s（%s）' % (gid, base)
    return gid


# 伤害/效果类型 → 中文（报告里别暴露 `coldDuration` 这种内部字段名）
DMG_ZH = {
    'physical': '物理', 'pierce': '穿刺', 'cold': '冰冷', 'fire': '火焰',
    'lightning': '闪电', 'poison': '毒素', 'acid': '酸蚀', 'vitality': '活力',
    'life': '活力', 'aether': '以太', 'chaos': '混乱', 'bleeding': '流血',
    'elemental': '元素', 'frostburn': '霜燃', 'burn': '燃烧', 'electrocute': '电击',
    'decay': '腐朽', 'trauma': '内伤',
    # DoT 变体（内部是 xxxDuration）
    'coldDuration': '霜燃', 'bleedingDuration': '流血持续', 'lifeDuration': '活力衰减',
    'fireDuration': '燃烧', 'lightningDuration': '电击', 'poisonDuration': '毒伤持续',
    'aetherDuration': '以太持续', 'physicalDuration': '内伤', 'burnDuration': '燃烧',
    # 控制/减益效果
    'freeze': '冻结', 'stun': '眩晕', 'knockdown': '击倒', 'slow': '减速',
    'confusion': '混乱', 'petrify': '石化', 'fumble': '失误', 'lifeLeech': '吸取生命',
    'percentCurrentLife': '按比例削血', 'totalDamageReductionPercent': '降伤',
    'runSpeed': '减速', 'attackSpeed': '降攻速', 'defensiveAbility': '降 DA',
    'offensiveAbility': '降 OA', 'resistReduction': '降抗', 'reduceResistance': '降抗',
}


def skill_label(name_or_record, tags=None, items=None):
    """技能显示名：记录名 → skills.json 的 name；tag → 字典；否则原样。"""
    if not name_or_record:
        return ''
    if name_or_record.startswith('records/'):
        d = _skills_lib().get(name_or_record) or {}
        return d.get('name') or os.path.basename(name_or_record).replace('.dbr', '')
    from . import gear as G
    tags = tags if tags is not None else G.load_tags()
    v = tags.get(name_or_record)
    return v if v else name_or_record.replace('tag', '').replace('Class01SkillName', '技能')


def resolve_all(plan):
    """gd_opt 方案 → {slot: {gid, record, comp, aug, pre, suf}}"""
    from . import savemap as M
    from . import gear as G
    IT = G.load_items()
    out = {}
    for slot, row in plan.items():
        gid = row[0]
        rec, _ = M.resolve(IT.get(gid) or {})
        out[slot] = {'gid': gid, 'record': rec,
                     'comp': row[1], 'aug': row[2], 'pre': row[3], 'suf': row[4]}
    return out


def build(plan_path, arch, level, name):
    import gd_alloc as A
    from . import devotion as D
    from . import rotation as R
    from . import gear as G
    from . import savemap as M
    from . import dbr as DB

    plan = json.load(open(plan_path, encoding='utf-8'))
    gear = resolve_all(plan)
    tags = G.load_tags()
    IT = G.load_items()
    db = DB.open_all()

    # ---- 属性 ----
    recs = {v['gid']: v['record'] for v in gear.values() if v['record']}
    ap = A.attribute_plan(recs, level, archetype=arch)

    # ---- 技能 ----
    sk = A.allocate(arch, level)

    # ---- 星座 ----
    dv = D.select_coherent(arch, budget=A.devotion_budget(level))

    # ---- DPS ----
    gear_items = []
    for v in gear.values():
        f = db.fields(v['record']) if v['record'] else None
        gear_items.append((v['gid'], f))
    # ★ 必须把**实际加点**传进去：`analyze` 对没给等级的技能会默认取 `max_level`，
    #   于是报告里会混进一堆"根本没点"的技能（实测 60 级方案里 `暗影袭改造` 显示
    #   12 级、占了 DPS 榜前列，而它并未加点）—— 严重高估。
    _levels = {r['skill']: int(r['level']) for r in sk['skills']}
    # ★ v4：改用 final_report —— 它读**方案 JSON 并折叠词缀/镶嵌/附魔**，
    #   按伤害类型算「所有加成后的最终伤害」（游戏「武器攻击」面板的口径），
    #   并按 `skillChanceWeight` 建输出循环（普攻占 100 权重 + 武器池 proc）。
    #   旧版 analyze 只读 dbr 底材，绿装词缀全丢，装备加成恒为 +0.0%。
    # ★ 武器基础攻速要用**武器自己的**（旧版 final_report 写死 1.25，实测单手斧 1.82）
    _base_aps = None
    _folded, _mods = None, {}
    try:
        from . import skillmod as _SM
        for _slot in ('主手', '副手'):
            for _g in (plan.get(_slot) or []):
                _rec2, _w2 = M.resolve(IT.get(_g) or {})
                if _rec2 and '/gearweapons/' in _rec2:
                    _base_aps = _SM.weapon_base_aps(db, _rec2)
                    break
            if _base_aps:
                break
        # ★ 套装加成 + 装备/套装对技能的改造（以前完全没建模）
        _bucket, _mods, _found, _aug = _SM.plan_extras(plan, db, IT)
        if _bucket or _mods or _aug:
            _folded = R.fold_plan(plan)
        if _bucket:
            _folded['套装加成'] = _bucket
        for _r3, _n3 in (_aug or {}).items():
            if _r3 in _levels:
                _levels[_r3] += _n3
    except Exception as _e:
        _base_aps, _folded, _mods = _base_aps, None, {}
    # ★ 属性伤害加成（GD 官方公式：物理/穿刺吃狡诈÷245、元素与魔法吃精神÷215…）
    #   以前**完全没传给 final_report** → 自有 BD 的伤害被低估一大截。
    _apct = {}
    try:
        _panel = ((ap or {}).get('panel') or (profile.get('_meta') or {}).get('panel')
                  or {})
        if _panel:
            _apct = R.attr_damage_pct(_panel)
    except Exception:
        _apct = {}
    # ★ 装备授予的武器池技能（WPS）入池（2026-09-20）—— `plan` 给的是 GT id，
    #   直接喂 `procs.wps_pool` 即可（它内部选 OfflineDB 取 `itemSkills` 权重）。
    try:
        from . import procs as _PR
        _gids = [g for _v in (plan or {}).values() for g in (_v or []) if g]
        _iw = _PR.wps_pool(_gids)
        # ★★ 2026-09-22：武器构成 → 武器类型硬前提门控（`plan` 里没有武器槽时
        #   返回 `None` ⇒ 不门控，零漂移）。
        _wst = _PR.weapon_state(_gids)
    except Exception:                                            # noqa: BLE001
        _iw, _wst = None, None
    rot = R.final_report(arch, _levels, plan, db, only_listed=True,
                         base_aps=_base_aps, folded=_folded, skill_mods=_mods,
                         attr_pct=_apct, item_wps=_iw, weapon_st=_wst)
    # 兼容旧渲染路径（三形态横向对比仍用 analyze）
    rot.setdefault('weapon_base', 0.0)

    # ---- 可落档 profile ----
    build_plan = {}
    for slot, v in gear.items():
        if slot in ('主手', '副手'):
            build_plan.setdefault('weapons', []).append(
                {'slot': slot, 'item': v['gid'], 'component': v['comp'], 'augment': v['aug']})
        else:
            build_plan.setdefault('equipment', []).append(
                {'slot': slot, 'item': v['gid'], 'component': v['comp'],
                 'augment': v['aug'], 'prefix': v['pre'], 'suffix': v['suf']})
    profile = {
        'char': None, 'target_level': level, 'archetype': arch,
        'note': name,
        'attributes': {k: ap['points'][k] for k in ('physique', 'cunning', 'spirit')},
        'skills': sk['skills'],
        'devotions': [{'record': r, 'level': 1} for r in dv['records']],
        'equipment': build_plan.get('equipment', []),
        'weapons': build_plan.get('weapons', []),
        '_meta': {'attr_used': ap['used'], 'attr_budget': ap['budget'],
                  'skill_used': sk['used'], 'skill_budget': sk['budget'],
                  'devotion_used': dv['used'], 'panel': ap['panel'],
                  'req': ap['req'], 'dps': rot.get('dps'),
                  'devotion_coherent': dv['coherent']},
    }
    return profile, gear, ap, sk, dv, rot, tags, IT, db


def h_kind(rec, hits):
    """该技能是不是**直接造成伤害的攻击技能**（被动/增益不计入 DPS 榜）"""
    from gd_rotation import ATTACK_KINDS
    return (hits.get(rec) or {}).get('kind') in ATTACK_KINDS


def report(profile, gear, ap, sk, dv, rot, tags, IT, db, plan_path):
    from . import rotation as R
    L = []
    m = profile['_meta']
    A_ = L.append
    A_('# %s' % profile['note'])
    A_('')
    A_('> 等级 **%d** ｜ 流派 `%s` ｜ 点数预算：技能 %d / 属性 %d'
       % (profile['target_level'], profile['archetype'], m['skill_budget'], m['attr_budget']))
    A_('')
    A_('## 一、装备（14 槽）')
    A_('')
    A_('| 槽位 | 装备 | 稀有 | 词缀(前/后) | 镶嵌 | 附魔 | 自带技能 |')
    A_('|---|---|---|---|---|---|---|')
    from . import dbr as DB
    for slot in SLOT_ORDER:
        v = gear.get(slot)
        if not v:
            continue
        o = IT.get(v['gid']) or {}
        rar = RARITY_ZH.get(o.get('f'), o.get('f') or '')
        f = db.fields(v['record']) if v['record'] else {}
        skills = []
        if f:
            for i in (1, 2, 3):
                nm = (f.get('augmentSkillName%d' % i) or [''])[0]
                lv = (f.get('augmentSkillLevel%d' % i) or [0])[0]
                if nm:
                    skills.append('%s +%s' % (skill_label(nm, tags, IT), lv))

        def _zh(x):
            if not x:
                return '—'
            return zh_name(x, tags, IT)
        A_('| %s | **it%s %s** | %s | %s / %s | %s | %s | %s |'
           % (slot, v['gid'][2:], zh_name(v['gid'], tags, IT), rar,
              _zh(v['pre']), _zh(v['suf']), _zh(v['comp']), _zh(v['aug']),
              '、'.join(skills) if skills else '—'))
    A_('')
    A_('## 二、技能加点（%d / %d 点）' % (m['skill_used'], m['skill_budget']))
    A_('')
    A_('| 技能 | 等级 |')
    A_('|---|---|')
    for s in sk['skills']:
        A_('| %s | %d |' % (s.get('why') or skill_label(s['skill'], tags, IT), s['level']))
    A_('')
    A_('## 三、属性点（%d / %d 点）' % (m['attr_used'], m['attr_budget']))
    A_('')
    A_('| 属性 | 加点 | 面板 |')
    A_('|---|---|---|')
    for k, zh in (('physique', '体格'), ('cunning', '狡诈'), ('spirit', '精神')):
        A_('| %s | %d | **%d** |' % (zh, ap['points'][k], int(ap['panel'][k])))
    A_('')
    A_('装备需求（含 10%% 余量）：体格 %s ／ 狡诈 %s ／ 精神 %s'
       % (ap['req']['physique'], ap['req']['cunning'], ap['req']['spirit']))
    ok = all(ap['panel'][k] >= ap['req'][k] for k in ('physique', 'cunning', 'spirit'))
    A_('')
    A_('> 可穿性：%s' % ('**✓ 面板满足全部装备需求**' if ok else '**✗ 有属性不足**'))
    A_('')
    A_('## 四、星座（%d 个 / %d 点 ｜ 亲和力%s）'
       % (len(dv['picks']), dv['used'], '自洽 ✓' if dv['coherent'] else '待复核'))
    A_('')
    for p in dv['picks'][:14]:
        A_('- %s（%d 个星）' % (p.get('name') or p['idx'], len(p['stars'])))
    A_('')
    rotn = rot.get('rotation') or {}
    A_('## 五、输出循环（最终伤害明细 ｜ 总 DPS %.0f）' % (rot.get('dps') or 0))
    A_('')
    A_('> 📖 **本节口径 = 游戏里「武器攻击」那条面板**：')
    A_('>')
    A_('> ```')
    A_('> 最终伤害 = ( 技能固定值 + 武器伤害% × 武器基础伤害 ) × (1 + 该类型的全部%加成) + 装备附加平伤')
    A_('> ```')
    A_('>')
    A_('> - **%加成已折叠绿装的前后缀词缀、镶嵌与附魔**（旧版只读装备底材，所以一栏恒为 +0.0%），')
    A_('>   并**计入技能自身的加成**（双刃 +穿刺%、气爆 +穿刺%、野兽形态的伤害倍率）')
    A_('> - **★ 已计入「装备/套装对技能的改造」（`Skill_Modifier`）**：平伤、专属转化、'
       '冷却增减、武器伤害%。')
    A_('>   例：Korba 套装给「猛袭」100% 物理→冰冷 + 100% 穿刺→冰冷 + 660 流血/3s —— '
       '不算是算不出一半伤害的。')
    A_('> - **★ 已计入套装加成**（2/3/4 件的数值加成与技能改造）、**星座节点**（含没有名字标签的）')
    A_('> - **★ 武器基础攻速取武器自己的**（速度标签 + 偏移量），不再用 1.25 的假设值')
    A_('> - **武器池技能按触发率折算**：权重 / (100 + Σ权重)，默认攻击占 100 权重')
    A_('> - **不含**：怪物抗性与护甲、暴击、命中率、DoT 持续时间的二次折算')
    A_('> - **%加成列拆成五列**（5.4）：装备 / 套装 / 星座 / 技能 / **属性**。'
       '属性是独立乘区，GT 面板的「修正%」也不含它。')
    A_('>')
    A_('> | 名词 | 是什么 | 用途 |')
    A_('> |---|---|---|')
    A_('> | **DPS**（本节） | 按触发率/冷却算出的每秒伤害 | 看**绝对输出**与技能贡献 |')
    A_('> | **伤害代理**（第六节） | 装备输出属性的**加权总分**（不是 DPS） | 装备搜索排序 |')
    A_('')

    # 5.1 武器基础
    wf = rot.get('weapon') or {}
    if wf or rot.get('weapon_dot'):
        A_('### 5.1 武器基础伤害%s' % ('（含穿刺转换 %.0f%%）' % rot['pierce_ratio']
                                      if rot.get('pierce_ratio') else ''))
        A_('')
        A_('| 类型 | 武器基础 |')
        A_('|---|---|')
        for t, v in sorted(list(wf.items()) + list((rot.get('weapon_dot') or {}).items()),
                           key=lambda x: -x[1][1]):
            A_('| %s | %.0f ~ %.0f |' % (R.TYPE_ZH.get(t, t), v[0], v[1]))
        A_('')
        A_('攻速 **%.2f 次/秒**（基础 %.2f × (1 + %.0f%%)）%s'
           % (rot['aps'], rot['base_aps'], rot['speed_pct'],
              '｜伤害倍率 **+%.0f%%**' % rot['dmg_mult'] if rot.get('dmg_mult') else ''))
        A_('')

    # 5.2 逐技能最终伤害（攻击技能，按 DPS 排）
    hits = rot.get('skills') or {}
    atk = {r: h for r, h in hits.items() if (h.get('dps') or 0) > 0}
    A_('### 5.2 逐技能最终伤害（按 DPS 排序，取前 6）')
    A_('')
    for rec, h in list(sorted(atk.items(), key=lambda x: -x[1]['dps']))[:6]:
        role = ('**默认攻击**' if rec == rotn.get('default')
                else ('武器池 proc' if rec in (rotn.get('procs') or [])
                      else ('冷却技能' if h.get('cooldown') else '主动技能')))
        ch = '触发率 **%.1f%%**' % (h['chance'] * 100) if h.get('chance') is not None else ''
        A_('#### %s · %d 级 ｜ %s ｜ %s ｜ DPS **%.0f**'
           % (skill_label(rec, tags, IT), h['level'], role, ch, h['dps']))
        A_('')
        A_('| 伤害类型 | 技能固定 | 武器段(%.0f%%) | %%加成 | 最终伤害 | 占比 |'
           % h['weapon_pct'])
        A_('|---|---|---|---|---|---|')
        for r in h['rows']:
            share = (r['max'] / h['max'] * 100) if h['max'] else 0
            A_('| %s%s | %.0f ~ %.0f | %.1f ~ %.1f | +%.0f%% | **%.1f ~ %.1f** | %.0f%% |'
               % (R.TYPE_ZH.get(r['type'], r['type']), '（持续）' if r['is_dot'] else '',
                  r['skill'][0], r['skill'][1], r['weapon'][0], r['weapon'][1],
                  r['pct'], r['min'], r['max'], share))
        A_('| **合计** | | | | **%.0f ~ %.0f**（均值 %.0f） | 100%% |'
           % (h['min'], h['max'], h['avg']))
        A_('')

    # 5.3 汇总
    A_('### 5.3 全技能 DPS 汇总')
    A_('')
    A_('| 技能 | 等级 | 触发率 | 频率(/s) | 单次均值 | DPS | 占比 |')
    A_('|---|---|---|---|---|---|---|')
    for rec, h in sorted((kv for kv in hits.items() if h_kind(kv[0], hits)),
                         key=lambda x: -x[1]['dps']):
        ch = '—' if h.get('chance') is None else '%.1f%%' % (h['chance'] * 100)
        share = (h['dps'] / rot['dps'] * 100) if rot.get('dps') else 0
        A_('| %s | %d | %s | %.2f | %.0f | **%.0f** | %.0f%% |'
           % (skill_label(rec, tags, IT), h['level'], ch, h.get('freq') or 0,
              h['avg'], h['dps'], share))
    A_('| **合计** | | | | | **%.0f** | 100%% |' % (rot.get('dps') or 0))
    A_('')
    if rotn:
        A_('- 普攻 + 武器池 proc **%.0f** ＋ 冷却技能 **%.0f**'
           % (rotn.get('dps_swing') or 0, rotn.get('dps_cooldown') or 0))
    others = [r for r in hits if not h_kind(r, hits)]
    if others:
        A_('- 被动 / 增益项（不占普攻等级，其加成一并计入上表）：%s'
           % '、'.join('%s(%d 级)' % (skill_label(r, tags, IT), hits[r]['level'])
                       for r in others))
    if any(v for v in (rot.get('skill_pct') or {}).values()):
        A_('- 技能自身提供的加成：%s'
           % '、'.join('%s +%.0f%%' % (R.TYPE_ZH.get(k, k), v)
                       for k, v in (rot.get('skill_pct') or {}).items() if v))
    A_('')

    # 5.4 加成来源：拆成 装备 / 套装 / 星座 / 技能 / 属性 —— **与 GT 面板的口径一致**
    #     ★ GT 的「修正%」列**不含属性**（属性在伤害数值内部另算，见 SKILL §52.3），
    #       所以这里也把属性单列出来，避免有人拿合计去对 GT 的面板。
    pct = rot.get('pct') or {}
    A_('### 5.4 最终 % 加成来源（已折叠词缀/镶嵌/附魔）')
    A_('')
    A_('| 伤害类型 | 装备 | 套装 | 星座 | 技能 | **属性** | 合计 |')
    A_('|---|---|---|---|---|---|---|')
    gp, sp = rot.get('gear_pct') or {}, rot.get('skill_pct') or {}
    ap = rot.get('attr_pct') or {}
    bp = rot.get('bucket_pct') or {}
    setp, devp = bp.get('套装加成') or {}, bp.get('星座节点') or {}
    for t, v in sorted(pct.items(), key=lambda x: -x[1])[:10]:
        if not v:
            continue
        A_('| %s | +%.0f%% | %s | %s | %s | %s | **+%.0f%%** |'
           % (R.TYPE_ZH.get(t, t),
              gp.get(t, 0) - setp.get(t, 0) - devp.get(t, 0),
              ('+%.0f%%' % setp[t]) if setp.get(t) else '—',
              ('+%.0f%%' % devp[t]) if devp.get(t) else '—',
              ('+%.0f%%' % sp[t]) if sp.get(t) else '—',
              ('+%.0f%%' % ap[t]) if ap.get(t) else '—', v))
    A_('')
    A_('> ⚠ **属性** 那一列是独立乘区：GD 里 物理/穿刺 吃**狡诈**（÷245）、'
       '元素与魔法吃**精神**（÷215）等。')
    A_('> GT 面板的「修正%」列**不含属性**，所以要和 GT 对账时请用 `装备+套装+星座+技能` 四列相加。')
    A_('')

    # ---- 六、伤害代理分解（把 gd_opt 那个"看不懂的数字"拆开） ----
    try:
        # ★ 先钉住流派，否则 gd_opt 的 field_aliases 不生效，标签会退化成通用名
        os.environ['GD_ARCHETYPE'] = profile['archetype']
        os.environ.setdefault('GD_SKILL_K', '2.0')
        import gd_explain as X
        A_('## 六、伤害代理分解（装备输出属性的加权总分）')
        A_('')
        A_('> 这是 `gd_opt` 搜索装备时**实际在优化的数字**。它**不是 DPS**，'
           '而是「每项输出属性 × 权重」的加权和，用途是**排序**。')
        A_('> 权重按对实际 DPS 的作用方式分档：攻速 2.0（乘区）> 总伤害 1.3 > '
           '主伤害 1.0 > DoT 0.55 > 平伤 0.5 > OA 0.3 > 生命/护甲 0（不计）。')
        A_('')
        A_(X.explain_md(plan_path))
        A_('')
        A_('> ⚠ 只可跟**同流派 + 同等级**的方案横比；绝对值没有物理意义。')
        A_('')
    except Exception as _e:                     # 报告不该因为附加信息失败
        A_('## 六、伤害代理分解')
        A_('')
        A_('（跳过：%s）' % _e)
        A_('')
    A_('## 七、落档')
    A_('')
    A_('```bash')
    A_('# 1) 生成可写档方案（gd_build 格式）')
    A_('python gd_plan_export.py _<角色名> %s plans/%s_build.json' % (plan_path, profile['archetype']))
    A_('# 2) 预演 / 写盘')
    A_('python gd_profile.py plan  %s_profile.json' % plan_path)
    A_('python gd_profile.py apply %s_profile.json' % plan_path)
    A_('```')
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser(description='完整 BD 生成器')
    ap.add_argument('plan')
    ap.add_argument('--arch', required=True)
    ap.add_argument('--level', type=int, default=75)
    ap.add_argument('--name', default='')
    ap.add_argument('--char', default='')
    args = ap.parse_args()

    name = args.name or ('%s · %d 级' % (args.arch, args.level))
    profile, gear, ap_, sk, dv, rot, tags, IT, db = build(
        args.plan, args.arch, args.level, name)
    if args.char:
        profile['char'] = args.char

    prof_path = os.path.splitext(args.plan)[0] + '_profile.json'
    json.dump(profile, open(prof_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    md = report(profile, gear, ap_, sk, dv, rot, tags, IT, db, args.plan)
    md_path = os.path.splitext(args.plan)[0] + '_BD.md'
    open(md_path, 'w', encoding='utf-8').write(md)

    print('BD profile → %s' % prof_path)
    print('BD 报告   → %s' % md_path)
    print()
    print('  装备 %d 件 ｜ 技能 %d 条(%d/%d 点) ｜ 属性 %d/%d 点 ｜ 星座 %d 个/%d 点%s'
          % (len(gear), len(sk['skills']), sk['used'], sk['budget'],
             ap_['used'], ap_['budget'], len(dv['picks']), dv['used'],
             '（亲和力自洽）' if dv['coherent'] else '（亲和力待复核）'))
    p = ap_['panel']
    r = ap_['req']
    print('  面板 力%.0f/敏%.0f/智%.0f ｜ 需求 力%s/敏%s/智%s ｜ %s'
          % (p['physique'], p['cunning'], p['spirit'], r['physique'], r['cunning'], r['spirit'],
             '✓ 够穿' if all(p[k] >= r[k] for k in ('physique', 'cunning', 'spirit')) else '✗ 不足'))
    print('  面板 DPS 代理 %.1f ｜ 主攻技能 %d 个'
          % (rot.get('dps') or 0, len(rot.get('skills') or {})))


if __name__ == '__main__':
    main()
