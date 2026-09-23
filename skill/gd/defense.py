# -*- coding: utf-8 -*-
r"""防御减伤模型 —— **我方挨打**（官方 Order of Defense 九层）。

与 `gd/combat.py` 的分工
------------------------
`gd/combat.py` 是**攻击侧**（我方伤害 → 敌方护甲/抗性）；本模块是**防御侧**
（敌方打击 → 我方减伤链）。两者共用同一批官方公式（DLEP/DGP、吸收率、PTH），
所以**一律复用 `combat` 的实现**，绝不在这里写第二套 —— 那正是陷阱 #52 的成因。

口径（真源逐字）
----------------
1. **抗性**（`data/cache/calc.js::f63k`）：
   `抗性 = defensive<X> + (火/冰/雷再加 defensiveElementalResistance)
           + (控制类再加 defensiveCrowdControl)`，再
   `min(., engine.playerDefenseCap + defensive<X>MaxResist + defensiveAllMaxResist)`。
   ★ **难度惩罚**（本项目既有口径，`gd/opt.py::PEN`）：终极 **上排 −50 / 下排 −25**
   ⇒ 「封顶 80」等价于 **raw ≥ 130（上排）/ 105（下排）**（`gd/opt.py::NEED`）。
   ⚠ 不 import `gd/opt`（它有模块级副作用）⇒ 常量在此冗余定义，`selftest` 里比对一次。
2. **部位护甲**（`calc.js::f62k`）：
   `部位护甲 = (本槽 defensiveProtection + 本槽 defensiveBonusProtection
                + 腰带项 + 全局项) × (1 + defensiveProtectionModifier/100)`；
   **腰带护甲对 6 个部位各计一次**；部位↔槽：head/shoulders/**chest→torso**/hands→arms/legs/feet。
   ★★ **吸收率的前提是「该部位自身护甲 ≠ 0」**（`0 == e ? 0 : ...`）
   —— 与 `combat.absorption_of` 的 docstring 一致，但那条此前没有调用方。
3. **减伤链顺序**（官方 Advanced Mechanics）：
   ① fumble/dodge/偏移 ② OA vs DA ③ 盾 ④ 种族% ⑤ 抗性 ⑥ 护甲 ⑦ 种族点数 ⑧ %吸收 ⑨ 点数吸收
   ⇒ **⑧ 乘算、⑨ 加算且排最后**（`data/absorption_sources.md`）。

设计约束
--------
* `chain()` / `armor_panel()` / `marginal()` **纯函数**（不读存档、不读离线库）⇒ 可单独断言。
* **不碰 `rep['dps']`**（攻击侧面板）⇒ 接入后锚点零漂移。
* 数据缺口**如实标注**：敌方打击值缺席时报告写「未计入绝对量」，不静默。
"""
import collections

from . import combat as CB

# ---------------------------------------------------------------- 常量（真源冗余）
# ★ 与 `gd/opt.py::TYPES/TOP/PHYS/NEED/PEN/RMAP` **必须一致**（那里有模块级副作用，
#   不能直接 import；`selftest` 会跑一次逐一比对，漂了就红）。
TOP = ('火', '冰', '电', '毒酸', '穿刺')
TYPES = ['火', '冰', '电', '毒酸', '穿刺', '流血', '活力', '虚化', '混乱']
PHYS = '物理'
ALL_TYPES = TYPES + [PHYS]
NEED = {t: (130 if t in TOP else 105) for t in TYPES}
NEED[PHYS] = 0
PEN = {t: (50 if t in TOP else 25) for t in TYPES}
PEN[PHYS] = 0
RMAP = {'火': [('defensiveElementalResistance', 1), ('defensiveFire', 1)],
        '冰': [('defensiveElementalResistance', 1), ('defensiveCold', 1)],
        '电': [('defensiveElementalResistance', 1), ('defensiveLightning', 1)],
        '毒酸': [('defensivePoison', 1)], '穿刺': [('defensivePierce', 1)],
        '流血': [('defensiveBleeding', 1)], '活力': [('defensiveLife', 1)],
        '虚化': [('defensiveAether', 1)], '混乱': [('defensiveChaos', 1)],
        PHYS: [('defensivePhysical', 1)]}

# 抗性桶 → 伤害类型名（`gd/rr.py` 的桶名），供减伤链查表用
RES_FIELD_OF = {
    'physical': '物理', 'pierce': '穿刺', 'bleeding': '流血', 'poison': '毒酸',
    'fire': '火', 'cold': '冰', 'lightning': '电', 'vitality': '活力',
    'aether': '虚化', 'chaos': '混乱',
}
# DoT → 基础桶（与 `gd/rr.py` 的映射同义：DoT 共用其基础类型的抗性）
DOT_OF = {'burn': 'fire', 'frostburn': 'cold', 'electrocute': 'lightning',
          'decay': 'vitality', 'trauma': 'physical', 'poisondot': 'poison'}

# 受击部位 → 装备槽（`calc.js::f6bl`，第二个元素才是「部位名」）
REGION_SLOT = {'head': '头部', 'shoulders': '肩甲', 'torso': '胸甲',
               'arms': '手套', 'legs': '腿甲', 'feet': '靴子'}
REGION_ZH = {'head': '头部', 'shoulders': '肩部', 'torso': '躯干',
             'arms': '手臂', 'legs': '腿部', 'feet': '足部'}
WAIST_SLOT = '腰带'

# 本模块要聚合的字段（★ 比 `gd/compare.py::SURV_FIELDS` 多三个「Modifier」——
#   没有它们，护甲 %加成与吸收修正就丢了，而 `f62k` 两者都要）。
DEF_FIELDS = tuple(
    {f for pairs in RMAP.values() for f, _w in pairs}
    | {'defensiveBonusProtection', 'defensiveProtection', 'defensiveProtectionModifier',
       'defensiveAbsorptionModifier', 'defensiveAllMaxResist', 'defensiveCrowdControl',
       'characterDodgePercent', 'characterDeflectProjectile',
       'defensiveBlockChance', 'defensiveBlockAmount', 'defensiveBlockModifier',
       'defensiveBlock', 'characterDefensiveBlockRecoveryReduction',
       'racialBonusPercentDefense', 'racialBonusAbsoluteDefense',
       'characterDamageAbsorptionPercent', 'damageAbsorptionPercent', 'damageAbsorption',
       'characterDefensiveAbility', 'characterLife', 'characterLifeModifier',
       'characterOffensiveAbility',
       'defensiveStun', 'defensiveFreeze', 'defensiveTrap', 'defensivePetrify',
       'defensiveKnockdown', 'defensiveDisruption'}
)
MAX_RESIST = 80.0


# ---------------------------------------------------------------- 聚合（装备 / 技能+星座）
_TPL_CACHE = None


def _templates():
    """`记录路径 → 模板名`（`data/skills.json` + `devotion_skills.json`，模块级缓存）。"""
    global _TPL_CACHE
    if _TPL_CACHE is None:
        _TPL_CACHE = {}
        from . import paths as _P
        for fn in ('skills.json', 'devotion_skills.json'):
            try:
                d = _P.load_json(fn) or {}
            except Exception:                                  # noqa: BLE001
                continue
            for rec, o in d.items():
                if not isinstance(o, dict):
                    continue
                t = o.get('template') or o.get('l')
                if t:
                    _TPL_CACHE.setdefault(rec, t)
    return _TPL_CACHE


def applies_to_self(rec):
    """★★ 该技能记录里的 `defensive*` 字段是**给我方**的吗？

    ★ 这是本模块最容易出错的一处：**同一个字段名 `defensivePierce`，在不同模板上作用对象相反** ——
      装备/被动上 = **我方**穿刺抗性；`Skill_AttackBuff*`（减益）上 = **目标的**穿刺抗性。
    实测（Sam，2026-09-21）两个反例：
      · `devotion/tier1_08e_skill`（刺客的标记，proc）→ `defensivePierce −8 / defensivePhysical −4`
      · `playerclass10/bonechillingcry1`（刺骨战吼）→ `defensivePierce −30 / defensiveBleeding −40 …`
    无脑累加会把「降敌抗」算成**我方掉抗**（合计 ≈ −38 穿刺 / −25 混乱）⇒ 面板整块失真
    （`gd/compare.py::survival()` 就有这个缺陷 —— 复用时必须绕开）。

    判据：模板以 `Skill_Attack` / `Skill_Debuff` 开头 ⇒ **排除**。
    ⚠ 不能简单地按 `*_skill.dbr` 后缀排除：星座的「自我增益型」proc（如伊师塔克的自然守护者
      给 % 吸收）**也要收** —— 只能靠模板区分。
    拿不到模板时**保守排除**星座 proc、其余保留（宁可少算，不可把降敌抗算成我方）。
    """
    t = _templates().get(rec)
    if not t:
        return not str(rec).endswith('_skill.dbr')
    t = str(t)
    return not (t.startswith('Skill_Attack') or t.startswith('Skill_Debuff'))


def split_fields(c, db=None, fields=None, self_only=True):
    """把角色的防御字段拆成 **逐槽** 与 **全局**（技能 + 星座 + 被动）两池。

    为什么要拆：`calc.js::f62k` 里「本槽护甲」与「全局护甲」是**两个不同的项**
    （前者算吸收率的判据、后者不然），合并成一个 Counter 就再也分不开了。
    `gd/compare.py::survival()` 只给合并值 ⇒ 这里必须自己拆一层。

    ★ `self_only=True`（默认）会剔掉 **`Skill_Attack*` 减益技能** —— 否则它们的
      「降低目标抗性」会被算成我方掉抗（见 `applies_to_self`）。

    返回 `(gear_by_slot, glob)`；`glob` 含技能/星座/被动的累加。
    """
    from . import rotation as R
    flds = tuple(fields or DEF_FIELDS)
    gear = {}
    for slot, f in (c.get('folded') or {}).items():
        d = {k: float(v) for k, v in f.items()
             if k in flds and isinstance(v, (int, float))}
        if d:
            gear[slot] = d
    glob = collections.Counter()
    cond = collections.Counter()
    dropped = []
    for rec, lv in (c.get('skills') or {}).items():
        if not lv:
            continue
        if self_only and not applies_to_self(rec):
            f = R.fields_with_buff(db, rec) or {}
            hit = [k for k in flds if k.startswith('defensive')
                   and R._at(f.get(k), lv)]
            if hit:
                dropped.append((rec, hit))
            continue
        f = R.fields_with_buff(db, rec) or {}
        # ★ 条件性来源：`lifeMonitorPercent`（如「不羁狂怒」= 50 ⇒ **HP 低于 50% 才触发**）
        #   ⇒ **整条记录**的贡献都归入 `cond`，不能混进常驻面板（否则 EHP 虚高）。
        lm = R._at(f.get('lifeMonitorPercent'), lv) if f.get('lifeMonitorPercent') else 0.0
        tgt = cond if lm else glob
        for k in flds:
            v = R._at(f.get(k), lv)
            if v:
                tgt[k] += v
    return gear, glob, cond, dropped


# ---------------------------------------------------------------- 面板
def resist_panel(glob, gear=None, difficulty=3):
    """9 + 1 个抗性桶 → `{raw, pen, after_pen, value, capped}`。

    `raw` = 装备 + 技能/星座（`f63k` 的组成规则）；
    `after_pen` = raw − 难度惩罚；`value` = `min(after_pen, 80 + MaxResist)`。
    ★ `capped` 判据用 **raw ≥ NEED**（= 惩罚后 ≥ 80），与 `gd/opt.py` 的满抗判据同源。
    """
    tot = collections.Counter(glob)
    for d in (gear or {}).values():
        for k, v in d.items():
            tot[k] += v
    allmax = float(tot.get('defensiveAllMaxResist') or 0.0)
    out = {}
    for t in ALL_TYPES:
        raw = sum(float(tot.get(f) or 0.0) * w for f, w in RMAP[t])
        mx = float(tot.get('defensive' + {'火': 'Fire', '冰': 'Cold', '电': 'Lightning',
                                          '毒酸': 'Poison', '穿刺': 'Pierce',
                                          '流血': 'Bleeding', '活力': 'Life',
                                          '虚化': 'Aether', '混乱': 'Chaos',
                                          '物理': 'Physical'}[t] + 'MaxResist') or 0.0)
        cap = MAX_RESIST + mx + allmax
        pen = float(PEN.get(t) or 0.0)
        after = raw - pen
        out[t] = {'raw': round(raw, 1), 'pen': pen, 'after_pen': round(after, 1),
                  'cap': round(cap, 1), 'value': round(min(after, cap), 1),
                  # ★ `capped` = **真的顶到上限**（`after_pen ≥ cap`）；物理桶 `NEED=0`
                  #   只表示「没有满抗要求」，不等于「已封顶」—— 两者必须分开。
                  'capped': after >= cap,
                  'need': NEED.get(t, 0),
                  'meets': raw >= NEED.get(t, 0)}
    return out


def armor_panel(gear, glob):
    """逐部位护甲（`calc.js::f62k`）+ 逐部位吸收率。

    返回 `{'regions': {region: {'own','armor','absorb'}}, 'total','waist','global','mod'}`。
    ★ `absorb` 的前提是**该部位自身护甲 `own` ≠ 0**（官方 `0 == e ? 0 : …`）。
    """
    def _prot(d):
        return float(d.get('defensiveProtection') or 0.0) \
            + float(d.get('defensiveBonusProtection') or 0.0)

    waist = _prot(gear.get(WAIST_SLOT) or {})
    gprot = float(glob.get('defensiveProtection') or 0.0) \
        + float(glob.get('defensiveBonusProtection') or 0.0)
    mod = float(glob.get('defensiveProtectionModifier') or 0.0)
    amod = float(glob.get('defensiveAbsorptionModifier') or 0.0)

    regions, tot = {}, 0.0
    for region, slot in REGION_SLOT.items():
        d = gear.get(slot) or {}
        own = _prot(d)
        raw = (own + waist + gprot) * (1.0 + mod / 100.0)
        regions[region] = {'own': round(own, 1), 'raw': round(raw, 1),
                           'absorb': CB.absorption_of(amod) if own > 0 else 0.0}
        tot += own
    return {'regions': regions, 'regions_total': round(tot, 1),
            'total': round(tot + waist, 1),
            'waist': round(waist, 1), 'global': round(gprot, 1),
            'prot_modifier': mod, 'absorb_modifier': amod}


def build(c, db=None, rep=None, difficulty=3):
    """完整防御面板。`rep` 给了就用它的面板 DA/OA（**含等级与三围项**）。"""
    gear, glob, cond, dropped = split_fields(c, db)
    tot = collections.Counter(glob)
    for d in gear.values():
        for k, v in d.items():
            tot[k] += v
    da = oa = None
    if isinstance(rep, dict):
        da, oa = rep.get('da'), rep.get('oa')
    if da is None:
        da = float(tot.get('characterDefensiveAbility') or 0.0)
    if oa is None:
        oa = float(tot.get('characterOffensiveAbility') or 0.0)
    return {
        'gear': gear, 'global': dict(glob), 'totals': dict(tot),
        'resist': resist_panel(glob, gear, difficulty),
        'armor': armor_panel(gear, glob),
        'da': round(float(da), 1), 'oa': round(float(oa), 1),
        'da_has_level': bool(isinstance(rep, dict) and rep.get('da')),
        'dodge': round(float(tot.get('characterDodgePercent') or 0.0), 1),
        'deflect': round(float(tot.get('characterDeflectProjectile') or 0.0), 1),
        'block_chance': round(float(tot.get('defensiveBlockChance') or 0.0), 1),
        'block_amount': round(float(tot.get('defensiveBlockAmount') or 0.0), 1),
        'racial_pct': round(float(tot.get('racialBonusPercentDefense') or 0.0), 1),
        'racial_flat': round(float(tot.get('racialBonusAbsoluteDefense') or 0.0), 1),
        'pct_absorb': round(float(tot.get('damageAbsorptionPercent')
                                  or tot.get('characterDamageAbsorptionPercent') or 0.0), 1),
        'flat_absorb': round(float(tot.get('damageAbsorption') or 0.0), 1),
        # ★ 条件性来源（如「不羁狂怒」残血触发）—— **单列**，绝不并进上面的常驻面板
        'pct_absorb_cond': round(float(cond.get('damageAbsorptionPercent')
                                       or cond.get('characterDamageAbsorptionPercent') or 0.0), 1),
        'flat_absorb_cond': round(float(cond.get('damageAbsorption') or 0.0), 1),
        'cond_fields': {k: round(v, 1) for k, v in cond.items()},
        'life_bonus': round(float(tot.get('characterLife') or 0.0), 1),
        'difficulty': difficulty,
        # ★ 被剔除的「降敌抗」减益记录（如实上报，不静默）
        'dropped_debuffs': [r for r, _h in dropped],
    }


# ---------------------------------------------------------------- 减伤链（纯函数）
def chain_remain(dmg, dtype, panel, enemy_oa=None, flat_pool=None):
    """**九层减伤链**：返回 `(剩余值, [逐层明细])`。全程期望值口径。

    `dtype`：`gd/rr.py` 的桶名（`physical` / `fire` / `bleeding` / `poisondot` …）。
    `enemy_oa`：敌方进攻能力（给了才走第 ② 层命中判定；不给 = 视为必中）。
    `flat_pool`：可用点数吸收池（第 ⑨ 层）。**它是加算且排最后** ⇒ 能真正减到 0。
    """
    cur = float(dmg)
    rows = []

    def _step(idx, zh, fn):
        nonlocal cur
        before = cur
        cur = max(0.0, fn(cur))
        rows.append({'no': idx, 'name': zh, 'before': round(before, 2),
                     'after': round(cur, 2),
                     'cut_pct': (round((1 - cur / before) * 100, 2) if before else 0.0)})

    # ① 躲避（闪避 + 投射偏移）—— 期望口径：完全免伤的部分按概率摊薄
    dodge = float(panel.get('dodge') or 0.0)
    deflect = float(panel.get('deflect') or 0.0)
    d = min(99.0, max(0.0, dodge + deflect))
    if d:
        _step('①', '闪避/偏移 %.1f%%' % d, lambda x: x * (1.0 - d / 100.0))

    # ② 敌方命中 / 暴击判定（敌 OA vs 我 DA）—— **反向**
    #    ⚠ 这一层**可能让伤害变大**：它是「敌方命中掷骰 + 暴击」的期望倍率，不是纯减伤层。
    #      PTH ≥ 100 ⇒ 敌方没有未命中面，只剩暴击加成 ⇒ 期望 > 1。如实呈现，别硬掰成 ≤1。
    if enemy_oa:
        p = CB.pth(float(enemy_oa), float(panel.get('da') or 1.0))
        hm = CB.hit_mult(p)
        _step('②', '敌方命中/暴击（PTH %.1f，期望 ×%.3f）' % (p, hm['expected']),
              lambda x, e=float(hm['expected']): x * e)

    # ③ 盾牌（官方 DGB/DLEB：格挡成功则按**格挡值**抵消）
    #    DGB `dmg − shieldDefense*(abs/100)` / DLEB `dmg*((100−abs)/100)`
    #    ⚠ **待核准**：GD 的「格挡吸收率」口径尚无实测（Sam 双持无盾 ⇒ 本层恒跳过）。
    bc = float(panel.get('block_chance') or 0.0)
    ba = float(panel.get('block_amount') or 0.0)
    if bc > 0 and ba > 0:
        def _blk(x, c=bc / 100.0, v=ba):
            return x * (1.0 - c) + max(0.0, x - min(x, v)) * c
        _step('③', '格挡 %.0f%% / 盾值 %.0f' % (bc, ba), _blk)
    else:
        rows.append({'no': '③', 'name': '格挡（未持盾）', 'before': round(cur, 2),
                     'after': round(cur, 2), 'cut_pct': 0.0})

    # ④ 种族 % 减伤
    rp = float(panel.get('racial_pct') or 0.0)
    if rp:
        _step('④', '种族减伤 %.0f%%' % rp, lambda x: x * (1.0 - rp / 100.0))

    # ⑤ 抗性（★ DoT 共用其基础类型的抗性；物理桶也在其中）
    bucket = DOT_OF.get(str(dtype), str(dtype))
    res = (panel.get('resist') or {}).get(RES_FIELD_OF.get(bucket, ''), {})
    rv = float(res.get('value') or 0.0)
    _step('⑤', '%s抗性 %.1f%%' % (RES_FIELD_OF.get(bucket, bucket), rv),
          lambda x: x * (1.0 - rv / 100.0))

    # ⑥ 护甲（**仅物理直伤**；创伤/穿刺/元素/流血不过甲）
    is_dot = str(dtype) in DOT_OF
    if CB.armor_applies(bucket, is_dot=is_dot):
        regs = (panel.get('armor') or {}).get('regions') or {}
        if regs:
            # 逐部位各自用自己的护甲与吸收率，再按官方命中概率加权
            tot_w, acc = 0.0, 0.0
            for region, r in regs.items():
                w = CB.armor_constants()['regions'].get(region, 0.0)
                acc += CB.armor_mitigate(cur, r['raw'], r['absorb']) * w
                tot_w += w
            _step('⑥', '护甲（逐部位，仅物理直伤）',
                  lambda x, a=acc / (tot_w or 1.0), c=cur: a * (x / c if c else 1.0))
    else:
        rows.append({'no': '⑥', 'name': '护甲（本类型不过甲）', 'before': round(cur, 2),
                     'after': round(cur, 2), 'cut_pct': 0.0})

    # ⑦ 种族点数减伤
    rf = float(panel.get('racial_flat') or 0.0)
    if rf:
        _step('⑦', '种族点数减伤 −%.0f' % rf, lambda x: x - rf)
    else:
        rows.append({'no': '⑦', 'name': '种族点数减伤（无）', 'before': round(cur, 2),
                     'after': round(cur, 2), 'cut_pct': 0.0})

    # ⑧ % 伤害吸收 —— **乘算**（只算**常驻**层；条件层单列，绝不并进来）
    pa = float(panel.get('pct_absorb') or 0.0)
    if pa:
        _step('⑧', '%% 伤害吸收 %.0f%%（乘算）' % pa, lambda x: x * (1.0 - pa / 100.0))
    else:
        rows.append({'no': '⑧', 'name': '% 伤害吸收（常驻 0）', 'before': round(cur, 2),
                     'after': round(cur, 2), 'cut_pct': 0.0})

    # ⑨ 点数伤害吸收 —— **加算且排最后**（唯一能真正减到 0 的层）
    pool = float(flat_pool if flat_pool is not None
                 else (panel.get('flat_absorb') or 0.0))
    _step('⑨', '点数伤害吸收 −%.0f（链末·加算）' % pool, lambda x: x - pool)

    return round(cur, 2), rows


def ehp(panel, hp, weights=None, enemy_oa=None, flat_pool=None):
    """**等效生命**：按打击构成加权求平均剩余率，再 `HP / 剩余率`。

    `weights`：`{桶名: 占比}`（不传用**等权**，并在报告里标注）。
    ⚠ 不做「敌人打你多少」的绝对量 —— 那需要 `monsterdb` 的攻击数据（未抽取）。
    """
    ws = weights or {b: 1.0 for b in RES_FIELD_OF}
    tot_w = sum(ws.values()) or 1.0
    acc = 0.0
    detail = {}
    for b, w in ws.items():
        rem, _rows = chain_remain(1.0, b, panel, enemy_oa=enemy_oa, flat_pool=flat_pool)
        detail[b] = round(rem, 4)
        acc += rem * w
    avg = acc / tot_w
    return {'hp': float(hp), 'avg_remain': round(avg, 4),
            'reduction_pct': round((1 - avg) * 100, 1),
            'ehp': round(float(hp) / avg, 1) if avg > 0 else None,
            'by_type': detail, 'weights': dict(ws)}


def marginal(panel, dtype='physical', enemy_oa=None, base_dmg=5000.0):
    """**每层边际收益**：给该项加一点，剩余值少多少（越大越值）。

    ⚠ `base_dmg` 必须选在**该类型能落进 DGP 区间（伤害 > 护甲）**的量级。
      落在 **DLEP**（伤害 ≤ 护甲）时**护甲值本身不影响结果**，边际会显示 0 ——
      那是**真实的物理行为**（两条官方公式 DLEP/DGP 的形状决定的），不是 bug。
    """
    def rem(p):
        return chain_remain(base_dmg, dtype, p, enemy_oa=enemy_oa, flat_pool=0.0)[0]

    base = rem(panel)
    out = []
    # 抗性 +10（该类型所属桶）
    bucket = DOT_OF.get(dtype, dtype)
    tzh = RES_FIELD_OF.get(bucket)
    if tzh:
        p2 = _copy_panel(panel)
        cur = p2['resist'][tzh]['value']
        p2['resist'][tzh]['value'] = min(cur + 10.0, p2['resist'][tzh]['cap'])
        out.append(('+10 %s抗性' % tzh, base - rem(p2)))
    # 护甲 +300（每部位）
    p3 = _copy_panel(panel)
    for r in (p3.get('armor') or {}).get('regions', {}).values():
        r['raw'] += 300.0
    out.append(('+300 护甲（每部位）', base - rem(p3)))
    # 点数吸收 +1000
    out.append(('+1000 点数吸收池',
                base - chain_remain(base_dmg, dtype, panel, enemy_oa=enemy_oa,
                                    flat_pool=1000.0)[0]))
    return {'dtype': dtype, 'base_remain': base, 'items': out}


def _copy_panel(panel):
    import copy as _copy
    return _copy.deepcopy(panel)


# ---------------------------------------------------------------- 典型打击档（Q2）
# ★ 这些是**示意档**，不是真值 —— 用来让 chain / ehp 有默认输入。数值取「终极难度
#   杂兵一次命中」的常见量级；真值需要 `monsterdb` 的攻击数据（未抽取）。
TYPICAL = collections.OrderedDict([
    ('physical',  {'zh': '物理重击',   'dmg': 8000.0, 'note': 'Boss 近战，过护甲'}),
    ('fire',      {'zh': '元素法术',   'dmg': 5000.0, 'note': '元素，只过抗性'}),
    ('pierce',    {'zh': '穿刺射击',   'dmg': 4500.0, 'note': '穿刺，不过护甲'}),
    ('bleeding',  {'zh': '流血 DoT',   'dmg': 1500.0, 'note': 'DoT，按每秒值'}),
    ('poisondot', {'zh': '毒酸 DoT',   'dmg': 1500.0, 'note': 'DoT，按每秒值'}),
    ('aether',    {'zh': '虚化法术',   'dmg': 5000.0, 'note': '虚化，只过抗性'}),
])


def typical_suite(panel, hp=10000.0, enemy_oa=None, weights=None, flat_pool=None):
    """对典型档跑一遍链，返回逐档明细 + 汇总 EHP。"""
    rows = []
    for b, spec in TYPICAL.items():
        rem, layers = chain_remain(spec['dmg'], b, panel, enemy_oa=enemy_oa,
                                   flat_pool=flat_pool)
        rows.append({'bucket': b, 'zh': spec['zh'], 'dmg': spec['dmg'],
                     'remain': rem, 'cut_pct': round((1 - rem / spec['dmg']) * 100, 1),
                     'layers': layers, 'note': spec['note']})
    agg = ehp(panel, hp, weights=weights or {b: 1.0 for b in TYPICAL},
              enemy_oa=enemy_oa, flat_pool=flat_pool)
    return {'rows': rows, 'ehp': agg}


# ---------------------------------------------------------------- 优化器接入
# ★ 为什么放在这里而不是 `tools/objfunc.py`：它需要「装备 + 技能/星座」的**分层聚合**，
#   而那正是 `split_fields` 的职责；且结果可按 `c` 缓存，避免搜索热路径每次评估重算。
#
# ⚠ **默认完全不启用**（`GD_DEF_WEIGHT` 未设 ⇒ `objfunc.score` 原样返回）⇒ 零漂移。
QUICK_WEIGHTS = {
    'cover': 0.30,        # 已封顶的抗性桶数 / 9（物理不计）
    'armor': 0.15,        # 护甲（4000 归一）
    'short': 0.15,        # 满抗缺口（越小越好；300 点缺口视为全失）
    'pct_absorb': 0.10,   # 常驻 % 吸收（50% 归一）
    'flat_absorb': 0.10,  # ★ 点数吸收**池**（6000 归一）—— 星座「乌龟壳 6100」是这里
    'da': 0.10,           # ★ 防御能力 DA（2000 归一）—— 属性点投体格在这儿体现
    'life': 0.10,         # 生命加成（3000 归一）
}
QUICK_ARMOR_REF = 4000.0
QUICK_SHORT_REF = 300.0
QUICK_PCT_ABSORB_REF = 50.0
QUICK_FLAT_ABSORB_REF = 6000.0
QUICK_DA_REF = 2000.0
QUICK_LIFE_REF = 3000.0


def quick_score(c, db=None, difficulty=3, da=None):
    """搜索热路径用的**轻量防御分**（0~1，越大越抗打）。

    ⚠ 刻意**不算九层链**（要建逐部位护甲 + 走 9 步，几万次评估下太贵）：
      只用「抗性覆盖 + 护甲 + 满抗缺口 + **点数吸收池** + **DA** + 生命」六项加权，给**方向**。
      精确评估请走 `build()` + `chain_remain()`（`tools/defense_audit.py`）。

    ★ `flat_absorb`（点数吸收池）与 `da` 是 2026-09-21 补的：没有它们，**星座的防御向选项
      （乌龟壳 6100 点吸收）与属性点（体格 → DA）在搜索里权重为 0** ⇒ 优化器会当作没价值退掉。
    """
    hit = c.get('_def_split')
    if hit is None:
        gear, glob, _cond, _drop = split_fields(c, db)
        hit = (gear, glob)
        try:
            c['_def_split'] = hit                    # 同一 `load_char` 结果内复用
        except Exception:                            # noqa: BLE001
            pass
    gear, glob = hit
    if da is None:
        tot = collections.Counter(glob)
        for d in gear.values():
            tot.update(d)
        da = float(tot.get('characterDefensiveAbility') or 0.0)
    rp = resist_panel(glob, gear, difficulty)
    cover = sum(1 for t, r in rp.items() if t != PHYS and r['capped']) / 9.0
    short = sum(max(0.0, r['need'] - r['raw']) for t, r in rp.items())
    ap = armor_panel(gear, glob)
    pct = float(glob.get('damageAbsorptionPercent')
                or glob.get('characterDamageAbsorptionPercent') or 0.0)
    flat = float(glob.get('damageAbsorption') or 0.0)
    w = QUICK_WEIGHTS
    s = (w['cover'] * cover
         + w['armor'] * min(1.0, ap['total'] / QUICK_ARMOR_REF)
         + w['short'] * max(0.0, 1.0 - short / QUICK_SHORT_REF)
         + w['pct_absorb'] * min(1.0, pct / QUICK_PCT_ABSORB_REF)
         + w['flat_absorb'] * min(1.0, flat / QUICK_FLAT_ABSORB_REF)
         + w['da'] * min(1.0, float(da) / QUICK_DA_REF)
         + w['life'] * min(1.0, float(glob.get('characterLife') or 0.0) / QUICK_LIFE_REF))
    return {'score': round(max(0.0, min(1.0, s)), 4),
            'cover': round(cover, 3), 'armor': round(ap['total'], 1),
            'shortfall': round(short, 1), 'pct_absorb': round(pct, 1),
            'flat_absorb': round(flat, 1), 'da': round(float(da), 1),
            'life_bonus': round(float(glob.get('characterLife') or 0.0), 1),
            'weights': dict(w)}


# ---------------------------------------------------------------- 自证
def selftest_identities():
    """恒等自证（L1）：`[(名称, 是否通过, 细节)]`，供 `tools/selftest.py` 直接跑。"""
    out = []

    def _panel(**kw):
        p = {'resist': {t: {'value': 0.0, 'cap': 80.0, 'raw': 0.0, 'after_pen': 0.0}
                        for t in ALL_TYPES},
             'armor': {'regions': {r: {'own': 100.0, 'raw': 100.0, 'absorb': 70.0}
                                   for r in REGION_SLOT}},
             'dodge': 0.0, 'deflect': 0.0, 'block_chance': 0.0, 'block_amount': 0.0,
             'racial_pct': 0.0, 'racial_flat': 0.0,
             'pct_absorb': 0.0, 'flat_absorb': 0.0, 'da': 1000.0}
        p.update(kw)
        return p

    # ① 常量与 gd/opt.py 同源（防「论文级口径悄悄漂」）
    try:
        from . import opt as _O
        same = (_O.TYPES == TYPES and tuple(_O.TOP) == tuple(TOP)
                and _O.PHYS == PHYS
                and all(_O.NEED.get(t) == NEED.get(t) for t in TYPES)
                and all(_O.PEN.get(t) == PEN.get(t) for t in TYPES)
                and all(_O.RMAP.get(t) == RMAP.get(t) for t in ALL_TYPES))
        out.append(("常量与 gd/opt.py 逐项一致（TYPES/TOP/PHYS/NEED/PEN/RMAP）", same, ""))
    except Exception as e:                                     # noqa: BLE001
        out.append(("常量与 gd/opt.py 逐项一致", False, "比对失败 %s" % e))

    # ② 全零面板（**连护甲也为 0**）+ 无源 ⇒ 链不改数值
    #    ⚠ `_panel()` 默认带 100 护甲（给 ⑥ 层的「护甲只吃物理」用），这里要显式清零
    _zarm = {'armor': {'regions': {r: {'own': 0.0, 'raw': 0.0, 'absorb': 0.0}
                                   for r in REGION_SLOT}}}
    rem, rows = chain_remain(1000.0, 'physical', _panel(**_zarm), flat_pool=0.0)
    out.append(("零面板：1000 点打击原样通过（不凭空减伤）", abs(rem - 1000.0) < 1e-9,
                "%.1f" % rem))

    # ③ 抗性单调：抗性越高，剩余越少
    lo, _ = chain_remain(1000.0, 'fire', _panel(resist=_r(80, 0)), flat_pool=0.0)
    hi, _ = chain_remain(1000.0, 'fire', _panel(resist=_r(80, 50)), flat_pool=0.0)
    out.append(("抗性单调：50% 抗比 0% 抗剩余更少", hi < lo, "%.1f < %.1f" % (hi, lo)))

    # ④ 抗性 80% ⇒ 剩 20%
    r80, _ = chain_remain(1000.0, 'fire', _panel(resist=_r(100, 80)), flat_pool=0.0)
    out.append(("80% 抗性：1000 → 200", abs(r80 - 200.0) < 1e-6, "%.1f" % r80))

    # ⑤ DoT 与基础桶共用抗性（poisondot 读 poison）
    pd, _ = chain_remain(1000.0, 'poisondot', _panel(resist=_r(100, 80)), flat_pool=0.0)
    out.append(("DoT 共用基础桶抗性（poisondot→poison）", abs(pd - 200.0) < 1e-6,
                "%.1f" % pd))

    # ⑥ 穿刺不过护甲、物理过护甲（判据单点 `combat.armor_applies`）
    p_arm, _ = chain_remain(1000.0, 'physical', _panel(), flat_pool=0.0)
    pi_arm, _ = chain_remain(1000.0, 'pierce', _panel(), flat_pool=0.0)
    out.append(("护甲只吃物理直伤（同样零抗性下物理被减、穿刺不被减）",
                p_arm < 1000.0 and abs(pi_arm - 1000.0) < 1e-9,
                "物理 %.1f / 穿刺 %.1f" % (p_arm, pi_arm)))

    # ⑦ 点数吸收排最后且**加算** ⇒ 能减到 0（% 吸收做不到）
    zero, _ = chain_remain(1000.0, 'fire', _panel(), flat_pool=1500.0)
    pct_only, _ = chain_remain(1000.0, 'fire',
                               _panel(pct_absorb=100.0 * 0.99), flat_pool=0.0)
    out.append(("点数吸收能归零（加算·链末）；% 吸收即便 99% 也 > 0",
                abs(zero) < 1e-9 and pct_only > 0, "点吸 %.1f / %%吸 %.2f" % (zero, pct_only)))

    # ⑧ % 吸收乘算：50% + 40% = 70%（不是 90%）
    both, _ = chain_remain(1000.0, 'fire', _panel(pct_absorb=70.0), flat_pool=0.0)
    out.append(("乘算示例 50%+40% = 70% ⇒ 1000 → 300",
                abs(both - 300.0) < 1e-6, "%.1f" % both))

    # ⑨ 部位护甲：腰带对 6 个部位各计一次（`f62k` 的 `f` 项）
    gear = {WAIST_SLOT: {'defensiveProtection': 100.0},
            '胸甲': {'defensiveProtection': 500.0}}
    ap = armor_panel(gear, {})
    out.append(("腰带护甲对每个部位各计一次（躯干 500+100=600，头部 0+100=100）",
                abs(ap['regions']['torso']['raw'] - 600.0) < 1e-9
                and abs(ap['regions']['head']['raw'] - 100.0) < 1e-9,
                "躯干 %.0f 头部 %.0f" % (ap['regions']['torso']['raw'],
                                       ap['regions']['head']['raw'])))

    # ⑩ ★ 吸收率的前提是该部位**自身**护甲 ≠ 0（官方 `0 == e ? 0 : …`）
    out.append(("吸收率前提：部位自身护甲 0 ⇒ 吸收 0（即使腰带给了护甲）",
                ap['regions']['head']['own'] == 0.0 and ap['regions']['head']['absorb'] == 0.0
                and ap['regions']['torso']['absorb'] > 0.0,
                "头部 %.0f / 躯干 %.0f" % (ap['regions']['head']['absorb'],
                                         ap['regions']['torso']['absorb'])))

    # ⑪ 护甲 %加成是乘法缩放
    ap2 = armor_panel(gear, {'defensiveProtectionModifier': 20.0})
    out.append(("护甲 %加成：躯干 (500+100)×1.2 = 720",
                abs(ap2['regions']['torso']['raw'] - 720.0) < 1e-9,
                "%.0f" % ap2['regions']['torso']['raw']))

    # ⑫ 抗性面板：惩罚 → 封顶（raw 130 → 80）
    rp = resist_panel({'defensivePierce': 150.0})
    out.append(("抗性面板：穿刺 raw 150 − 惩罚 50 = 100 ⇒ 封顶 80",
                abs(rp['穿刺']['value'] - 80.0) < 1e-9 and rp['穿刺']['capped'],
                "raw %.0f → %.0f" % (rp['穿刺']['raw'], rp['穿刺']['value'])))
    rp2 = resist_panel({'defensiveChaos': 80.0})
    out.append(("抗性面板：混乱 raw 80 − 惩罚 25 = 55（未封顶、如实报）",
                abs(rp2['混乱']['value'] - 55.0) < 1e-9 and not rp2['混乱']['capped'],
                "raw %.0f → %.0f" % (rp2['混乱']['raw'], rp2['混乱']['value'])))

    # ⑬ 元素抗性并入火/冰/雷（`f63k`）
    rp3 = resist_panel({'defensiveElementalResistance': 100.0})
    out.append(("元素抗性并入火/冰/雷三个桶",
                all(abs(rp3[t]['raw'] - 100.0) < 1e-9 for t in ('火', '冰', '电'))
                and rp3['毒酸']['raw'] == 0.0, ""))

    # ⑭ ★★ `defensive*` 的**作用对象**判定：减益技能必须剔除
    _drop = ('records/skills/devotion/tier1_08e_skill.dbr',      # 刺客的标记
             'records/skills/playerclass10/bonechillingcry1.dbr')  # 刺骨战吼
    _keep = ('records/skills/devotion/tier1_04a.dbr',              # 星座星点
             'records/skills/playerclass10/amatokpact1.dbr')       # Amatok 契约
    out.append(("★★ 减益技能的抗性字段不并入我方（Skill_Attack* 判定）",
                all(not applies_to_self(r) for r in _drop)
                and all(applies_to_self(r) for r in _keep),
                "剔除 刺客的标记/刺骨战吼 ｜ 保留 星座星点/Amatok 契约"))

    # ⑮ 护甲合计口径：Σ(六部位自身) + 腰带 = total
    _ap3 = armor_panel({WAIST_SLOT: {'defensiveProtection': 100.0},
                        '胸甲': {'defensiveProtection': 500.0}}, {})
    out.append(("护甲合计 = Σ(六部位自身) + 腰带",
                abs(_ap3['total'] - (_ap3['regions_total'] + _ap3['waist'])) < 1e-9,
                "%.0f = %.0f + %.0f" % (_ap3['total'], _ap3['regions_total'],
                                        _ap3['waist'])))
    return out


def _r(cap, val):
    """自证用的小工具：造一个「所有桶同值」的 resist 面板。"""
    return {t: {'value': float(val), 'cap': float(cap), 'raw': float(val),
                'after_pen': float(val)} for t in ALL_TYPES}
