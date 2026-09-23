# -*- coding: utf-8 -*-
r"""输出循环 / DPS 模型（gd_rotation）—— 2026-09-17 新增（v3 P1）

为什么需要它
------------
`gd_opt` 的「伤害代理」是**按形态加权的分数**（狼人口径读物理/穿刺字段、
鸦人口径读冰冷字段），所以**跨形态不可比**：三套方案都是 2544/2469/2547，
看起来一样强，但那只是权重算出来的，不是实战伤害。

本模块给出**绝对尺度**的 DPS 估算，把三形态拉到同一把尺子上。

模型（三层）
------------
```
技能单次伤害 = 固定段(min/max 均值, 按技能等级取逐级数组)
             + 武器伤害% × 武器基础伤害均值
装备加成     = Σ(该技能伤害类型对应的 offensive*Modifier)
攻击频率     = 攻速（近战）/ 冷却（法术）
主动 DPS     = Σ 技能单次伤害 × (1 + 加成) × 频率

限时 buff    = 增益 × min(1, skillActiveDuration / skillCooldownTime)   ← 临时加成
开关 buff    = 增益 × 1.0                                              ← 常驻
proc         = 触发概率 × 攻击频率 × 单次效果                           ← 期望值
```

用法
----
```python
from . import rotation as R
rep = R.analyze('werewolf', levels={'records/skills/playerclass10/werewolf1.dbr': 16, ...},
                gear={'records/items/gearweapons/swords1h/d302_sword.dbr': None})
print(rep['dps'], rep['breakdown'])
```
"""
import json
import os
import re
import sys

from . import paths as _PATHS

# 旧脚本用 HERE 拼数据文件路径；新架构下数据在技能的 data/ 里
HERE = str(_PATHS.DATA_DIR)
_CACHE_ROOT = str(_PATHS.CACHE_DIR)
_PLANS = str(_PATHS.CACHE_DIR / 'plans')



# 装备/套装对技能的改造（`Skill_Modifier` 合并）—— 见 gd_skillmod
try:
    from gd_skillmod import mod_conversions, mod_pct          # noqa: E402
except ImportError:                                          # 允许独立运行
    def mod_conversions(_m):
        return []

    def mod_pct(_m):
        return {}

# 「元素」不是独立伤害类型：GD 里等于 火 + 冰 + 电（均分）
ELEMENTAL = ('fire', 'cold', 'lightning')

ATTACK_KINDS = ('attack', 'attack_weapon', 'attack_radius', 'attack_wave',
                'attack_projectile', 'attack_buff', 'attack_buff_radius')
# ★ 合成记录名：**普通武器攻击** —— 角色没点任何默认攻击(DAR)技能时左键的那一击。
#   它不是一个真实 `.dbr` 记录（库里查不到），所以任何「按记录名查库」的地方
#   都必须先判 `== BASIC_REC`（见 `wps_blocked`）。用双下划线包住是为了
#   在报告/日志里一眼看出「这是模型合成的，不是数据库里的技能」。
BASIC_REC = '__basic_weapon_attack__'
BUFF_DURATION_KINDS = ('buff_timed', 'buff_self')
BUFF_TOGGLE_KINDS = ('buff_toggle', 'buff_radius_toggle', 'buff_self', 'buff_radius')

# 伤害类型字段 → 加成字段名（用于把装备加成对上技能伤害）
DMG_TO_MOD = {
    'physical': 'offensivePhysicalModifier',
    'pierce': 'offensivePierceModifier',
    'fire': 'offensiveFireModifier',
    'cold': 'offensiveColdModifier',
    'lightning': 'offensiveLightningModifier',
    'poison': 'offensivePoisonModifier',
    'acid': 'offensivePoisonModifier',
    'vitality': 'offensiveVitalityModifier',
    'aether': 'offensiveAetherModifier',
    'chaos': 'offensiveChaosModifier',
    'bleeding': 'offensiveSlowBleedingModifier',
    'burn': 'offensiveSlowFireModifier',
    'frostburn': 'offensiveSlowColdModifier',
    'electrocute': 'offensiveSlowLightningModifier',
    'decay': 'offensiveSlowVitalityModifier',
}


def _load(name, default):
    """读 `data/<name>`。走 `paths.load_json`（进程内 memo + pickle 缓存）。"""
    from . import paths as _P
    v = _P.load_json(name)
    return default if v is None else v


def _at(vals, level):
    """按技能等级取逐级数组的值（等级从 1 起；超界取末位）"""
    if not vals:
        return 0.0
    i = max(0, min(int(level) - 1, len(vals) - 1))
    v = vals[i]
    return float(v) if isinstance(v, (int, float)) else 0.0


def parse_damage(fields, level):
    """拆出「固定伤害」与「DoT 伤害」以及涉及的伤害类型"""
    flat, dot, types = {}, {}, set()
    for k, vals in fields.items():
        m = re.match(r'^offensive(Slow)?(?:Base)?([A-Z][a-zA-Z]*?)(Min|Max)$', k)
        if not m:
            continue
        slow, tname = bool(m.group(1)), m.group(2)
        t = tname[0].lower() + tname[1:]
        v = _at(vals, level)
        if not v:
            continue
        d = dot if slow else flat
        d[t] = d.get(t, 0.0) + v / 2.0          # Min/Max 各取一半 = 均值
        types.add(t)
    return flat, dot, types


# ============================================================ 最终伤害（折叠装备）
# 游戏里那条「武器攻击 → 物理 394-706 / 穿刺 171 / 冰冷 10439-14272 …」的明细，
# 才是玩家要看的东西。旧版只读 dbr **底材**，绿装的词缀（加成主力）全丢了，
# 所以「装备加成」一栏常是 +0.0%。这里改成：读**方案 JSON**，把
# 底材 + 镶嵌 + 附魔 + 前缀 + 后缀**折叠求和**（与 gd_opt 同源的 GT 物品库）。
TYPE_ZH = {
    'physical': '物理', 'pierce': '穿刺', 'fire': '火焰', 'cold': '冰冷',
    'lightning': '闪电', 'poison': '毒素', 'acid': '酸液', 'vitality': '活力',
    'aether': '以太', 'chaos': '混乱', 'bleeding': '流血', 'burn': '燃烧',
    'frostburn': '霜燃', 'electrocute': '电击', 'decay': '活力衰减',
    'trauma': '内部创伤', 'poisondot': '毒素持续',
}
TYPE_ORDER = tuple(TYPE_ZH)
DOT_TYPES = ('bleeding', 'burn', 'frostburn', 'electrocute', 'decay',
             'trauma', 'poisondot')
WPN_SLOTS = ('主手', '副手')
# 归因账本里的**虚拟槽** —— 它们不是真装备，但 `slot_flat['套装加成']` 要参与
# `base_parts` 的拆分（从「装备」里减掉套装），所以即使关掉账本也必须照记。
_VIRT_SLOTS = ('套装加成', '星座节点')

# ================================================================ ★ 形态门控（2026-09-20）
# 游戏规则：**变身替换整个技能栏**。
#   · 狼人形态（`werewolf1`）只授予「野性利爪 / 狂乱撕扯」两个技能，
#     基础主动攻击技能（猛袭 / 血牙 / 雪崩 / 跃击 / 阿斯特堪之风…）**点不出来也用不了**；
#   · 反过来，人形态下用不了任何变形授予技能。
# 这条规则 `gd/alloc.py` 一直知道（加点时按 `_FORM_LOCKED` 排除），
# 但 `final_report` **从来没执行** —— 于是拿存档加点算伤害时，
# 人形态方案里会混进狼人的野性利爪，把真正的主输出（雪崩）**整个挤掉**
# （实测：雪崩 lv20 每击 6782~8671，而野性利爪 16 级 avg 更高 ⇒ 雪崩贡献恒为 0）。
#
# ★★ 触发方式是**形态表显式声明** `"form": "human" | "werewolf" | "wereraven"`：
#     没写 `form` 的形态**完全不做门控** ⇒ 历史形态与全部基准**零漂移**。
#     这是刻意的：老形态（如 `wolf_nightblade`）的语义是「狼人形态下夜刃 WPS 是否触发」
#     这个**还没被游戏实测标定**的问题，不在这里顺手改。
FORM_LOCKED = ('attack', 'attack_weapon', 'attack_wave', 'attack_projectile',
               'attack_radius', 'attack_weapon_charge', 'attack_weapon_blink',
               'attack_projectile_burst', 'attack_weapon_radius',
               'attack_spellcone', 'attack_projectile_debuf')
_FORM_OWNED_RE = re.compile(r'^([a-z]+?)\d+(?:_|$)')
# 全部变身分支（`fangs` = 完美姿态，记录名是 `fangs_triplejab` —— 字母后没有紧跟
# 数字，正则抓不到，见 `form_of_record` 里的特判）。
_FORM_OWNERS = ('werewolf', 'wereraven', 'fangs')


def form_of_record(rec):
    """记录属于哪个变形分支（`werewolf1_skill01_claws.dbr` → `werewolf`）；非变形技能 None。"""
    base = os.path.basename(rec or '')
    if base.startswith('fangs_') or base == 'fangs.dbr':
        return 'fangs'
    m = _FORM_OWNED_RE.match(base)
    if not m:
        return None
    head = m.group(1)
    return head if head in _FORM_OWNERS else None


def is_wps_rec(rec, d=None):
    """是不是武器池技能（WPS）—— 按**模板**判，不按记录路径。

    装备授予的 WPS 走 `gd/procs.wps_pool()`，专精自带的走 `core_skills`，
    两条路的记录路径完全不同，只有模板是共通的。
    """
    t = (d or {}).get('template') or ''
    return 'WPAttack' in t or 'WeaponPool' in t


def form_granted(archetype_arch, skills):
    """该形态**变身之后的技能栏** —— `root_skills[0]` 的 `granted` 列表。

    ★ 这是形态门控的**权威真源**（离线库直读，不是猜的）：
      · `werewolf1.granted  = [werewolf1_skill01_claws, werewolf1_skill02_charge]`
      · `wereraven1.granted = [wereraven1_skill01_icicles, wereraven1_skill02_icering]`
      · `fangs.granted      = [fangs_triplejab, fangs_screech]`
    变身会**整体替换技能栏** ⇒ 不在这张表里的主动攻击技能，游戏里点不出来。
    """
    r0 = ((archetype_arch or {}).get('root_skills') or [None])[0]
    if not r0 or not skills:
        return set()
    return set((skills.get(r0) or {}).get('granted') or [])


def form_blocks(rec, form, granted=(), skills=None):
    """变形之后 `rec` 是否**点不出来 / 用不了** —— 形态门控的唯一判据。

    ★★ 2026-09-21 重写（原判据按 `kind ∈ FORM_LOCKED` 一刀切，有两处错）：

      ① **漏了**：`form_gate` 只在形态**显式声明 `form`** 时才跑，而 13 个形态里
         只有 `avalanche` 声明了 ⇒ 狼人形态的 Sam 全程零门控，`跃击`
         （`playerclass10/leap1.dbr`，`werewolf1.granted` 里根本没有）
         被按 `1/冷却 = 0.33 次/秒` 算进面板 —— **虚增 27,307（19.8%）**。
      ② **误杀**：`FORM_LOCKED` 把**星座 proc** 一起干掉了。星座 proc 是
         「攻击时触发」的，变身之后照样在打（默认攻击换成野性利爪而已），
         不该跟着技能栏一起消失（实测「刀锋之怒」`devotion/tier2_06g_skill`
         无辜掉了 2,031）。

    现在按**技能的真实来源**分三类判：

      | 条件 | 裁决 | 依据 |
      |---|---|---|
      | `rec ∈ granted` 或 `form_of_record(rec) == form` | **保留** | 形态自己授予的（野性利爪 / 狂乱撕扯） |
      | 路径含 `/devotion/` | **保留** | 星座 proc，被攻击行为触发，不占技能栏 |
      | WPS 模板 / `kind ∈ FORM_LOCKED` | **剔除** | 技能栏主动技能 + 武器池技能 |
      | 其余（被动 / 光环 / 加成 / 开关） | **保留** | 不占技能栏，变形后照常生效 |

    ★ 为什么 WPS 也剔（2026-09-21 用户拍板）：变身后默认攻击由野性利爪接管，
      WPS 是「替代默认攻击」的武器池技能 ⇒ 默认攻击被替换了，WPS 一次都不触发。
      实测 Sam：「击倒」13.8% +「恐狼之爪」9.9% 一并归零。
    """
    if not form:
        return False
    own = form_of_record(rec)
    if form == 'human':
        return own is not None                  # 人形态：变形授予技能一律排除
    if rec in granted or own == form:
        return False                            # 形态自己授予的
    if '/devotion/' in (rec or '').replace('\\', '/'):
        return False                            # 星座 proc
    d = (skills or {}).get(rec) or {}
    return is_wps_rec(rec, d) or (d.get('kind') or '') in FORM_LOCKED


def form_gate(records, archetype_arch, skills=None):
    """按形态声明过滤技能清单（见 `form_blocks` 的裁决表）。

    `archetype_arch` 没写 `form` ⇒ **原样返回**（不做任何过滤）。
    """
    form = (archetype_arch or {}).get('form')
    if not form:
        return list(records)
    skills = skills or {}
    granted = form_granted(archetype_arch, skills)
    return [r for r in records if not form_blocks(r, form, granted, skills)]


# 装备的 % 加成字段 → 它影响哪些伤害类型（比 DMG_TO_MOD 更全：含元素/物理持续）
GEAR_MOD_TYPES = {
    'offensivePhysicalModifier': ('physical',),
    'offensivePierceModifier': ('pierce',),
    'offensiveFireModifier': ('fire',),
    'offensiveColdModifier': ('cold',),
    'offensiveLightningModifier': ('lightning',),
    'offensivePoisonModifier': ('poison', 'acid'),
    'offensiveVitalityModifier': ('vitality',),
    'offensiveAetherModifier': ('aether',),
    'offensiveChaosModifier': ('chaos',),
    'offensiveElementalModifier': ('fire', 'cold', 'lightning'),
    'offensiveSlowBleedingModifier': ('bleeding',),
    'offensiveSlowFireModifier': ('burn',),
    'offensiveSlowColdModifier': ('frostburn',),
    'offensiveSlowLightningModifier': ('electrocute',),
    'offensiveSlowVitalityModifier': ('decay',),
    'offensiveSlowPoisonModifier': ('poisondot',),
    'offensiveSlowPhysicalModifier': ('trauma',),
    # ★★ GD 的 dbr 里**活力叫 Life 不叫 Vitality**（`offensiveLifeMin/Max/Modifier`）。
    #    只写 `offensiveVitalityModifier` 等于这条映射**永远不命中** —— 实测批量验证
    #    10/10 个秘术系 build 的活力修正低 800 个百分点（+756% GT vs +79% 我们）。
    'offensiveLifeModifier': ('vitality',),
    'offensiveSlowLifeModifier': ('decay',),
}


# ★ 只有**真正的伤害类型**才进明细。dbr 里还有一堆同样形如 `offensiveXxxMin/Max`
#   的**非伤害**字段，不白名单过滤就会算进去（实测混进过 stun 0.7-0.7、
#   knockdown 0.5-0.5、bleedingDuration 3-3、lifeLeech 28-28、PierceRatio 200-200）。
DAMAGE_TYPES = frozenset(TYPE_ZH)
# `offensiveSlowXxxMin` 是 **DoT**，类型名和直接伤害不同名（SlowCold = 霜燃，不是冰冷）
SLOW_TYPE = {'bleeding': 'bleeding', 'fire': 'burn', 'cold': 'frostburn',
             'lightning': 'electrocute', 'poison': 'poisondot',
             'vitality': 'decay', 'physical': 'trauma',
             'aether': 'aether', 'chaos': 'chaos'}

# ★★ **字段名 → 我们的类型名**：GD 的 dbr 里活力写作 `Life`
#    （`offensiveLifeMin` / `offensiveSlowLifeModifier`），不归一的话
#    `parse_mm` 推导出 `life`，而 `DAMAGE_TYPES`/`SLOW_TYPE` 里叫 `vitality`
#    → 活力与活力衰减的**平伤整块被丢弃**。
FIELD_TYPE = {'life': 'vitality'}


def _num(v):
    """逐级数组取首值 / 标量原样 / 其他 → 0"""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, list) and v and isinstance(v[0], (int, float)):
        return float(v[0])
    return 0.0


_MM_RE = re.compile(r'^offensive(Slow)?(?:Base)?([A-Z][a-zA-Z]*?)(Min|Max)$')
# ★★ 2026-09-22 性能：（字段名 → 分类）**永久缓存**。
#   `parse_mm` 在单次 `final_report` 里被调 ~150 次、扫 ~200 个字段 ⇒ 每次评估
#   ~1,300 次 `re.match`，而**字段名集合是全库固定的**（就那几百个）⇒ 分类结果
#   可以一次算好、永久复用。实测单次评估里 `re.match` 是第 3 大热点。
#   分类值：`('flat'|'dot', 伤害类型)`；`None` = 不是伤害字段。
_MM_CLS = {}


def _mm_cls(k):
    v = _MM_CLS.get(k)
    if v is None:
        m = _MM_RE.match(k)
        if not m:
            v = False
        else:
            slow, tname, bound = bool(m.group(1)), m.group(2), m.group(3)
            key = tname[0].lower() + tname[1:]
            key = FIELD_TYPE.get(key, key)          # Life → vitality
            t = SLOW_TYPE.get(key) if slow else (key if key in DAMAGE_TYPES else None)
            v = (slow, t, bound) if t is not None else False
        _MM_CLS[k] = v
    return v


def parse_mm(fields, level=None):
    """拆出**各类型**的 min/max（保留上下限，不像 parse_damage 那样取均值）。

    level 给定时按技能逐级数组取值；level 为 None 时取首值（装备用）。
    单边字段（只有 Min）→ max = min。
    返回 (直接伤害, 持续伤害)，各是 {类型: [min, max]}

    ★ 字段名分类走 `_mm_cls()` 的**永久缓存**（见上方 `_MM_CLS`）—— 语义逐字不变，
      只是把每次都做的正则匹配换成一次字典查表。
    """
    flat, dot = {}, {}
    _cls = _mm_cls
    for k, vals in (fields or {}).items():
        c = _cls(k)
        if not c:
            continue
        slow, t, bound = c
        v = _at(vals, level) if level is not None else _num(vals)
        if not v:
            continue
        e = (dot if slow else flat).setdefault(t, [0.0, None])
        if bound == 'Min':
            e[0] += v
        else:
            e[1] = (e[1] or 0.0) + v
    for d in (flat, dot):
        for t, e in d.items():
            if e[1] is None or e[1] < e[0]:
                e[1] = e[0]
    return flat, dot


# ★★ 游戏**属性 → 伤害**的官方公式（来自 `itemdb.js` 的 `window.combatformulas`）：
#   physicalDamageEquation        = physicalDamageDV * ((dexterityDV/245) + 1)
#   pierceDamageEquation          = pierceDamageDV * ((dexterityDV/245) + 1)
#   magicalDamageEquation         = magicalDamageDV  * ((intelligenceDV/215) + 1)
#   physicalDurationDamageEquation= physicalDamageDV * ((dexterityDV/215) + 1)
#   magicalDurationDamageEquation = magicalDamageDV  * ((intelligenceDV/200) + 1)
#   ⇒ 换算成「%加成」= 属性值 / 分母（例：狡诈 700 → +285% 穿刺/物理）
#   注意：**物理和穿刺都用狡诈（dexterity）**，不是体格。
ATTR_EQ = {
    'physical': ('cunning', 245), 'pierce': ('cunning', 245),
    'trauma': ('cunning', 215), 'bleeding': ('cunning', 215),
    'fire': ('spirit', 215), 'cold': ('spirit', 215), 'lightning': ('spirit', 215),
    'poison': ('spirit', 215), 'acid': ('spirit', 215), 'vitality': ('spirit', 215),
    'aether': ('spirit', 215), 'chaos': ('spirit', 215),
    'burn': ('spirit', 200), 'frostburn': ('spirit', 200),
    'electrocute': ('spirit', 200), 'decay': ('spirit', 200),
    'poisondot': ('spirit', 200),
}
# 精通条每级给的属性（力/敏/智）—— 「每级常数」兜底近似。
# ⚠ 仅当拿不到逐级曲线时才用：真实曲线是**半值取整**的（class01 是 5/3.5/1.5、
#   class04 精神是 2.5），这个线性表对 class01/class02/class04… 都会算错。
#   权威数据见 `data/mastery_attr.json`（`tools/build_mastery_attr.py`）。
MASTERY_ATTR = {
    'class01': (5, 3, 2), 'class02': (4, 4, 3), 'class03': (3, 3, 5),
    'class04': (4, 4, 3), 'class05': (2, 3, 5), 'class06': (4, 3, 3),
    'class07': (3, 4, 4), 'class08': (4, 2, 4), 'class09': (5, 3, 3),
    'class10': (4, 4, 2),
}

# ★ 逐级曲线（100 级）来自游戏 `records/skills/playerclassNN/_classtraining_classNN.dbr`
_MASTERY_CURVE = None


def mastery_curve():
    global _MASTERY_CURVE
    if _MASTERY_CURVE is None:
        try:
            from . import paths
            _MASTERY_CURVE = paths.load_json('mastery_attr.json') or {}
        except Exception:
            _MASTERY_CURVE = {}
    return _MASTERY_CURVE


def mastery_attr_of(cls, level, key):
    """某个专精条在 level 级时给 `key`(physique/cunning/spirit) 的累计值。"""
    curve = mastery_curve().get(cls)
    if curve and curve.get(key):
        arr = curve[key]
        i = max(0, min(int(level), len(arr)) - 1)
        return float(arr[i])
    per = MASTERY_ATTR.get(cls)
    if per:
        return float(per[{'physique': 0, 'cunning': 1, 'spirit': 2}[key]] * level)
    return 0.0


ATTR_BASE = 50.0            # 1 级时的基础属性值
ATTR_PER_POINT = 8.0        # 每投 1 点属性 = +8 面板值（playerBio.*Increment = 8）


def attr_damage_pct(attrs):
    """面板属性 → 各伤害类型的**属性加成 %**（GD 官方公式，见 ATTR_EQ）

    公式是 `伤害 × (属性/分母 + 1)` ⇒ 加成 = 属性值 / 分母（不减基础 50）。
    实测：狡诈 747 → +305% 穿刺。
    """
    out = {}
    for t, (who, denom) in ATTR_EQ.items():
        v = float(attrs.get(who) or 0.0)
        if v > 0:
            out[t] = v / denom * 100.0
    return out


def panel_attrs(bio, masteries, gear_flat, gear_pct):
    """把「存档里的属性值 + 精通 + 装备」合成**面板属性**。

    ★ 关键：存档 block2 存的是 `50 + 8 × 投入点`（**不含精通与装备**）——
    由 `playerBio.cunningIncrement = 8` 与「(410-50)/8 = 45 点 ≈ 58 级点数」双重验证。
    所以面板值 = (存档值 + 精通给点 + 装备平值) × (1 + 装备%值/100)
    """
    out = {}
    for name, key in (('physique', 'physique'), ('cunning', 'cunning'),
                      ('spirit', 'spirit')):
        mast = 0.0
        for cls, lv in (masteries or {}).items():
            mast += mastery_attr_of(cls, lv, key)
        base = float(bio.get(name) or 0.0) + mast + float(gear_flat.get(name) or 0.0)
        out[name] = base * (1.0 + float(gear_pct.get(name) or 0.0) / 100.0)
    return out


_FWB_CACHE = {}


def fields_with_buff(db, rec):
    """取技能的有效字段：**若是「壳技能」则合并 `buffSkillName` 指向的记录**。

    ★ 实测：`bonechillingcry1.dbr`（刺骨战吼）本体**没有任何伤害字段**，
    伤害全在 `bonechillingcry1_buff.dbr` 里；`wordofpain1.dbr` 同理。
    不合并的话这些技能在伤害明细里会**整条消失**（不是显示为 0，是压根不出现）。

    ★★ 2026-09-22 性能：加**模块级缓存**。
      单次 `final_report` 会调它 ~230 次（技能加成循环 + 光环循环 + 命中构造 +
      `rr_of_skills`），而**同一个 `rec` 的结果恒定**（只取决于 db）⇒ 重复率约 4×。
      缓存的是**合并后的主副本**，返回 `dict(主副本)` **浅拷贝** ——
      因为 `_make_hit` 的调用点会往返回的 dict 里注入 `skillChanceWeight`
      （见 `final_report` 里「装备授予的 WPS」那段），直接返回主副本会被污染。
      浅拷贝 ~200 个键只花几微秒，远比 `dict(db.fields()) + 合并循环` 便宜。
    """
    _key = (id(db), rec)
    hit = _FWB_CACHE.get(_key)
    if hit is not None:
        return dict(hit)
    f = dict(db.fields(rec) or {})
    if not f:
        return f
    for k in ('buffSkillName', 'buffSkillNames'):
        v = f.get(k)
        for sub in (v if isinstance(v, list) else [v]):
            if not isinstance(sub, str) or not sub or sub == rec:
                continue
            for kk, vv in (db.fields(sub) or {}).items():
                if kk in ('templateName', 'Class', 'buffSkillName'):
                    continue
                # ★ 主记录里常有一堆**全 0 占位**（`offensiveLifeModifier: [0.0]`），
                #   直接 setdefault 会被这些 0 挡住，buff 的真实值进不来。
                cur = f.get(kk)
                if cur is None or not _nonzero(cur):
                    f[kk] = vv
    if len(_FWB_CACHE) < 40000:          # 上限守卫（全库技能记录约 4k，留 10× 余量）
        _FWB_CACHE[_key] = f
    return dict(f)


def _nonzero(v):
    if isinstance(v, (list, tuple)):
        return any((isinstance(x, (int, float)) and x) or
                   (isinstance(x, str) and x) for x in v)
    return bool(v)


def collect_conversions(items):
    """从装备字段收集**伤害转化**：`(输入类型, 输出类型, 百分比)`。

    GT/存档的物品对象里有 `conversionInType` / `conversionOutType` /
    `conversionPercentage`（类型名是 `Physical`/`Cold` 这种首字母大写）。
    实测：`戈尔巴的刽子手` 每把 15% 物理→冰冷，双持合计 30%。
    """
    out = []
    for o in (items or []):
        if not isinstance(o, dict):
            continue
        i = o.get('conversionInType')
        t = o.get('conversionOutType')
        p = o.get('conversionPercentage')
        if isinstance(i, str) and isinstance(t, str) and p:
            out.append((i.lower(), t.lower(), float(p)))
    return out


def apply_conversions(base, convs):
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
        # ★★ `Elemental` 既能作**目标**也能作**源**：作源时对 火/冰/电 **各**生效。
        #   旧实现只判 `i == t` ⇒ 「元素→X」这类规则**静默失效**
        #   （与 `gd/dmg.py::_conv_matches` 同一处口径，两处必须一致，见自检 [26]）。
        rel = [(o, p) for i, o, p in convs
               if i == t or (i == 'elemental' and t in ELEMENTAL)]
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


def skill_children(skills):
    """父技能 → 它的 **modifier 子技能**（`deps` 指向父）。

    ★ GD 的「修饰技能」不是独立攻击，而是**加在父技能上**：
    `猛袭`(onslaught1) ← `开裂创口`/`无尽狂怒`(onslaught2/3) 的平伤与武器伤害%
    必须并进父技能，否则默认攻击的伤害会被严重低估。
    """
    out = {}
    by_deps = set()
    for r, d in (skills or {}).items():
        # ★ **敌人技能也会 `skillDependancy` 指向玩家技能**（例：`ulgrim_wpattack01`、
        #   `outlaw_dualwieldattack_02` 都挂在 `wpattack0` 下）。不过滤的话，
        #   这些敌人技能会被并进玩家伤害里（实测把 wpattack0 的伤害算歪）。
        if '/playerclass' not in r:
            continue
        for p in (d.get('deps') or []):
            out.setdefault(p, []).append(r)
            by_deps.add(r)
    # ★ 多数 modifier **没有依赖字段**（dbr 里查不到），父技能靠**命名约定**：
    #   `onslaught1 → onslaught2/onslaught3`、`ringofsteel → ringofsteel_mod1`、
    #   `phantomblade → phantomblade_mod0/1/2`、`wpattack0 → wpattack4` …
    #   规则：去掉尾部 `(_mod)?数字` 得「词干」，同词干内**kind 为 modifier/transmuter 的
    #   作为子**，其余取名字最短者作父。
    groups = {}
    for r, d in (skills or {}).items():
        if r in by_deps:
            continue
        # ★ 只收**玩家技能**：敌人也有一堆同名同词干的技能
        #   （`ulgrim_wpattack01` / `outlaw_dualwieldattack_02` …），
        #   不加这条会把敌人技能当成玩家的 modifier 并进伤害里。
        if '/playerclass' not in r:
            continue
        base = os.path.basename(r).replace('.dbr', '')
        stem = re.sub(r'(_mod)?\d+$', '', base)
        groups.setdefault((os.path.dirname(r), stem), []).append(r)
    for _k, recs in groups.items():
        mods = [r for r in recs
                if (skills[r].get('kind') in ('modifier', 'transmuter'))]
        parents = [r for r in recs if r not in mods]
        if not mods or not parents:
            continue
        parent = sorted(parents, key=lambda x: (len(os.path.basename(x)), x))[0]
        out.setdefault(parent, []).extend(mods)

    # ★ **只有 modifier / transmuter 才「并入父技能」**。
    #   别的 kind（尤其 `attack`）虽然也 `skillDependancy` 指向父技能，但它们是
    #   **独立攻击**（例：武器池 `wpattack1/2/3/5` 依赖「双刃」但各自独立触发），
    #   并进去会重复计算。
    for k in list(out):
        out[k] = [r for r in out[k]
                  if skills[r].get('kind') in ('modifier', 'transmuter')]
        if not out[k]:
            del out[k]

    # ★ **形态的 modifier 要挂到形态「授予」的攻击上**：
    #   `贪噬/血莽` 依赖 `狼人形态`，但真正吃到它们加成的是
    #   `野性利爪 / 狂乱撕扯`（`grantedSkills`）→ 平移过去。
    for k in list(out):
        d = skills.get(k) or {}
        if d.get('kind') != 'shapeshift':
            continue
        for g in (d.get('granted') or []):
            out.setdefault(g, []).extend(out[k])
        del out[k]
    return out


_CHILDREN_MEMO = {}
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


def skill_group(rec, levels, skills, db, children, _seen=None):
    """把「父技能 + 其 modifier 子链」合成一组：返回 (固定, DoT, 武器伤害%, 参与记录)

    子技能用**子自己的等级**取数组；` weaponDamagePct ` 累加。
    """
    _seen = _seen or set()
    if rec in _seen:
        return {}, {}, 0.0, []
    _seen.add(rec)
    outs = [(rec, levels.get(rec))]
    for c in children.get(rec, []):
        lv = levels.get(c)
        if lv:
            outs.append((c, lv))
    flat, dot, wd, used = {}, {}, 0.0, []
    for r, lv in outs:
        if not lv:
            continue
        f = fields_with_buff(db, r)
        if not f:
            continue
        fl, dt = parse_mm(f, lv)
        for src, tgt in ((fl, flat), (dt, dot)):
            for t, (lo, hi) in src.items():
                e = tgt.setdefault(t, [0.0, 0.0])
                e[0] += lo
                e[1] += hi
        wd += _at(f.get('weaponDamagePct') or [], lv) / 100.0
        used.append(r)
    return flat, dot, wd, used


# 「平伤」字段（Min/Max，含 Slow 变体，以及武器的 `offensiveBaseXxx`）
_FLAT_RE = re.compile(r'^offensive(Slow)?(Base)?[A-Z][a-zA-Z]*(Min|Max)$')


def is_shield_item(o):
    """GT 条目是不是**盾牌**（位图路径含 `gearweapons/shields/`）"""
    return 'shields/' in ((o or {}).get('n') or '').lower()


# ★★★ 「属性等级缩放」：`offensive*` **伤害属性要乘 (1 + attributeScalePercent/100)**
#   逆向自 GT（2026-09-17，全部命中，见 SKILL §67）：
#     c204_sword2h  h=60 → PierceMin 27→43.2 → 提示框 [35-51] = 43.2×[0.8,1.2] ✓
#     it8407        h=40 → 前缀 PhysModifier 36→50.4 → 提示框 +43/+57% = 50.4×[0.85,1.15] ✓
#   而 `character*` / `defensive*` / `conversionPercentage` **不缩放**：
#     it8407 的 characterDefensiveAbility 33 → 提示框 +29/+37 = 33×[0.85,1.15]（没乘 1.4）✓
#     c204 的 characterLife 212 → 提示框 +170-254 = 212×[0.8,1.2]（没乘 1.6）✓
#   ⚠ 旧版**完全没做这个缩放**，导致所有伤害属性系统性低 (1+h/100) 倍
#     （Sam 的穿刺 27 vs 实际 43.2，正好 1.6 倍）—— 这就是「打一下」偏低的根因之一。
_SCALE_MOD_RE = re.compile(r'^offensive[A-Z][a-zA-Z]*Modifier$')
_SCALE_MIN_RE = re.compile(r'^offensive([A-Z][a-zA-Z]*)Min$')


def needs_scale(k, norm):
    """该字段要不要乘 `(1 + attributeScalePercent/100)`。

    ★★ **唯一权威来源现在是 `gd_scale.needs_scale()`** —— 它逐字对齐了
    GT 离线版 `calc.js` 的 `va()` / `Xc()` 逻辑（2026-09-19 逆向，见 SKILL §67）。
    本函数保留为薄封装，方便老代码不改调用点。

    - `offensive*Modifier`（伤害 %）→ **要**
    - `offensive*Min` 且同记录**没有非零的**配对 `Max` → **要**（单值平伤）
      ⚠ `offensivePhysicalMin/Max` 是成对的「武器基础伤害」，**不缩放**
      ⚠ `...RatioMin`（穿刺转换比）/ `...DurationMin`（DoT 时长）不缩放
    - `Mf` / `sd` 名单里的**不缩放** —— 注意三个陷阱字段：
      `offensiveCritDamageModifier`（暴击伤害）、`offensiveLifeLeechMin`、
      `offensiveTauntMin` —— **以 `offensive` 开头但 GT 明确不缩放**
    - 其余（`character*` / `defensive*` / `conversionPercentage` / `skillCooldownReduction`）→ **不要**

    ★★ **坑（2026-09-19 实测）**：GD 的 dbr 记录是**完整字段集** ——
    未使用的字段**存在但值为 0**。所以判「有没有配对的 Max」**不能用 `in`**，
    必须用**真值**判断：c204 同时有 `offensivePierceMin:27` 和 `offensivePierceMax:0`，
    用 `in` 会误判成「成对 ⇒ 不缩放」，结果穿刺平伤**少算 1.6 倍**。
    """
    try:
        import gd_scale as S
        return S.needs_scale(k, norm)
    except Exception:
        pass
    if 'Ratio' in k or 'Duration' in k:
        return False
    if _SCALE_MOD_RE.match(k):
        return True
    m = _SCALE_MIN_RE.match(k)
    if m and not norm.get(k[:-3] + 'Max'):     # 无配对 Max，或配对 Max 为 0/None
        return True
    return False


def fold_plan(plan, items=None, no_scale=()):
    """方案 JSON → {槽位: {字段: 值}}（底材+镶嵌+附魔+前缀+后缀 **求和**）。

    这是「最终伤害」能算对的前提：绿装的加成几乎全在词缀里，底材常常是 0。

    ★ **盾牌的基础伤害要剔除**：盾牌记录里的 `offensivePhysicalMin/Max`（如 175 物理）
    只在「盾牌攻击」类技能（盾击/盾牌冲击）生效，**普通攻击吃不到**；
    而它的词缀 / 镶嵌 / 附魔（含平伤）照常生效。只剔盾牌记录自身的 flat。
    实测（Thane 剑盾流）：不剔则物理基础从 108~141 变 283~316（多算盾的 175）。

    ★★ **`attributeScalePercent` 缩放**（见上方 `needs_scale`）：
    缩放系数取**物品主体**记录的 `h`；**词缀记录没有 `h`，借用物品的**
    （GT 源码 `ea(D)||ia(D,ea(d))` 就是这个语义）。
    **镶嵌物 / 附魔不缩放** —— 实测「恶毒尖刺」提示框显示 `4 穿刺伤害 / +18% 穿刺伤害`，
    就是它记录里的原值（`it2878` 的 `offensivePierceMin:4` / `offensivePierceModifier:18`）。

    ★★ **`no_scale`：这些槽不做上面的缩放。**
    语义：**该槽已经有「已缩放的权威真值」（GT 物品提示框 / 游戏内提示框）**，
    再叠一次 dbr 缩放就会**重复计算**（实测会让残差从 −9% 掉到 +57%）。
    调用方约定：
    - `gt_build_dps.py`：有 GT 页面提示框文本的槽
    - `gd_dps_check.py`：`gt_data/local_items.json` 里录了提示框的槽
    """
    from . import gear as G
    items = items if items is not None else G.load_items()
    no_scale = set(no_scale or ())
    try:
        import gt_scale_index as SI
        scale_idx = SI.load()
    except Exception:
        scale_idx = {}
    out = {}
    for slot, row in (plan or {}).items():
        gids = [g for g in (row or []) if g]
        # ① 该槽的 attributeScalePercent：取**物品主体**（词缀借它）
        h = 0.0
        for gid in gids:
            if gid[:3] in ('pre', 'suf'):       # 词缀自身没有 h
                continue
            hv = (scale_idx.get(gid) or {}).get('h')
            if hv:
                h = float(hv)
                break
        f = {}
        for gid in gids:
            o = items.get(gid) or {}
            drop_flat = is_shield_item(o)
            # ② 每个部件用哪个缩放系数
            hv = (scale_idx.get(gid) or {}).get('h')
            if hv:                               # 物品主体：用自己的 h
                use_h = float(hv)
            elif gid[:3] in ('pre', 'suf'):      # 词缀：借用物品的 h
                use_h = h
            else:                                # 镶嵌 / 附魔：不缩放
                use_h = 0.0
            if slot in no_scale:                 # ★ 该槽已有权威真值（提示框）→ 不缩放
                use_h = 0.0
            norm = {}
            for k, v in o.items():
                if not isinstance(v, (int, float)):
                    continue
                if drop_flat and _FLAT_RE.match(k):
                    continue
                # ★ 武器的「基础伤害」写作 `offensiveBaseChaosMin/Max`
                #   （如 `b102d_axe` 的 `offensiveBaseChaosMin: 54`）——
                #   归一成 `offensiveChaosMin` 才能被 parse_mm 识别，否则**整块丢弃**。
                if k.startswith('offensiveBase'):
                    k = 'offensive' + k[len('offensiveBase'):]
                norm[k] = norm.get(k, 0.0) + float(v)
            if use_h:
                # ★ 用 GT 官方公式 `Af()` = `(rc()[0]+rc()[1])/2`（`gd_scale.value`），
                #   与 `base × (1+h/100)` 数学等价，但**取整方式与 GT 一致**（差 ≤0.5）。
                try:
                    import gd_scale as _GS
                    for k in norm:
                        if norm[k] and needs_scale(k, norm):
                            norm[k] = _GS.value(norm[k], use_h)
                except Exception:
                    for k in norm:
                        if needs_scale(k, norm):
                            norm[k] *= (1.0 + use_h / 100.0)
            for k, v in norm.items():
                f[k] = f.get(k, 0.0) + v
        out[slot] = f
    return out


def is_devotion(rec):
    """是否星座（虔诚）节点记录。"""
    low = (rec or '').lower()
    return 'devotion' in low or os.path.basename(low).startswith('tier')


def devotion_contrib(levels, db):
    """★ **星座（星节点）**对伤害的全部贡献。

    返回 `{'pct': {类型: %}, 'flat': {类型: [min,max]}, 'spd': %, 'mult': %,
           'nodes': n, 'res': {抗性类型: 值}, 'raw': {字段: 值}}`。

    为什么必须单独算（2026-09-18 补）：`final_report` 原先**完全不读星座** ——
    旧的 `devotion_pct()` 定义了却没有任何调用点，`levels` 里也只传玩家技能树。
    实测 Sam 现状星座给了 **+118% 穿刺 + 大量 Oa/生命**，模型里一点没算，
    所以「换星座」这件事此前根本无法用真实 DPS 评价。

    ★ 口径边界（诚实标注）：
      · 只统计**节点自身**的字段（`Skill_Passive` 的加成、平伤、DoT 平伤、攻速、独立倍率）；
      · **星座授予的技能（proc，如暴风雪/元素风暴）没有算** —— 那是独立伤害来源，
        需要按触发条件建模，属已知缺口（用 `star 数` 判取舍时要人工补看）；
      · `res` / `raw` 是 2026-09-20 新增的**防御向**读出（见下方长注释），
        只做**展示与审计**，**没有**回灌进 `gd/opt.py` 的满抗约束。
    """
    pct = {t: 0.0 for t in TYPE_ORDER}
    flat = {}
    dot = {}
    spd = 0.0
    mult = 0.0
    nodes = 0
    # ★★ 2026-09-20 新增：**防御向**贡献（抗性 / OA / DA / 生命 …）。
    #   背景：用户把「抗性满」定成硬指标。但优化器的 `NEED`（上排 130 / 下排 105）
    #   是拿**装备槽的抗性之和**去比的 —— `gd/opt.py::ev()` 只累加 gear 的 `res_of()`，
    #   基座/精通/技能/星座的贡献一概没进。所以「满抗」在规划器里 = **仅靠装备也满**，
    #   这是**保守**口径（实战只会更高），不是错。
    #   但它必须**看得见**：否则容易误读成「星座给抗性 ⇒ 装备可以少堆」，
    #   进而在真实配装时把抗性堆漏。这里把字段原样吐出来供审计。
    res = {}
    raw = {}
    _RMAP = None
    for rec, lv in (levels or {}).items():
        if not lv or lv <= 0 or not is_devotion(rec):
            continue
        nodes += 1
        f = fields_with_buff(db, rec) if db else {}
        for k, vals in f.items():
            v = _at(vals, lv)
            if not v:
                continue
            if k in GEAR_MOD_TYPES:
                for t in GEAR_MOD_TYPES[k]:
                    pct[t] += v
            elif k == 'offensiveTotalDamageModifier':
                for t in TYPE_ORDER:
                    pct[t] += v
            elif k == 'offensiveDamageMultModifier':
                mult += v
            elif k in ('characterAttackSpeedModifier', 'characterTotalSpeedModifier'):
                spd += v
            # 防御/角色向字段：只登记，不影响伤害
            if k.startswith('defensive') or k.startswith('character'):
                raw[k] = raw.get(k, 0.0) + v
        fl, dt = parse_mm(f)
        for src, tgt in ((fl, flat), (dt, dot)):
            for t, (lo, hi) in src.items():
                e = tgt.setdefault(t, [0.0, 0.0])
                e[0] += lo
                e[1] += hi
    # 抗性：与 `gd/opt.py::RMAP` **同口径**（元素抗性同时计入 火/冰/电），
    # 这样这里的数字可以直接和 `NEED`（130/105）对比，不用人工换算。
    if raw:
        try:
            from .opt import RMAP as _RMAP
        except Exception:                       # noqa: BLE001
            _RMAP = None
        if _RMAP:
            for _t, _ks in _RMAP.items():
                for _k, _w in _ks:
                    if _k in raw:
                        res[_t] = res.get(_t, 0.0) + raw[_k] * _w
    return {'pct': pct, 'flat': flat, 'dot': dot, 'spd': spd, 'mult': mult,
            'nodes': nodes, 'res': res, 'raw': raw}


def devotion_pct(archetype, levels, db):
    """**星座（星节点）**贡献的 % 加成（薄包装，向后兼容）。"""
    return devotion_contrib(levels, db)['pct']


def skill_type_breakdown(rep):
    """把每个技能的**每一击**拆成「逐伤害类型 × 实战口径」，写回 `rep`。

    ★ 用户口径（2026-09-20）：「伤害循环我要看到**具体的技能伤害构成** ——
      多少物理、多少穿刺、多少冰冷；**把抗性、穿甲也加入计算**」。

    输出：
        `rep['skills'][rec]['type_rows']` —— 每条 = 一个伤害类型在该技能里的一行：
            `type` / `zh` / `is_dot` / `dur` / `per_hit[lo,hi]`（面板口径的**一击**）
            `share`         该行在技能 DPS 里的占比（DoT 行已含覆盖率，同 `rr.row_w`）
            `bucket` / `res` / `rr_add` / `rr_pct`   敌方抗性（**减抗后**的 `res`）
            `rr_mult`       抗性乘区 `(100−res_eff)/(100−base)`
            `armor_pct`     护甲减免 %（**只对物理直伤**，其余为 `None`）
            `armor_pierce_pct` 武器「护甲穿透」%（物理→穿刺；**已含在行里**，此处仅供阅读）
            `combat_mult`   综合乘区 = `rr_mult × (1 − armor_pct/100)`
            `dps_panel` / `dps_vs` / `dps_final`
        `rep['type_rows']`       —— 所有技能按类型汇总（实战口径），给「总构成」用
        `rep['dps_final']`       —— 含命中·减抗·**过甲**的第四层口径

    ★ 口径链：`dps_panel` →（× 命中/暴击期望，**技能级**）→ `dps_vs`（已含减抗）
              →（再 × (1 − 护甲减免)）→ `dps_final`。
      护甲减免**逐行**用该行自己的中点算（与 `rep['armor']['by_type']` 同一算式
      `gd.combat.armor_expect`）；拿聚合 DPS 当「这一击」是错的 ——
      护甲减免对打击大小**非线性**，大击被吸收的比例更小。
    ★ 本函数**只加键、不改任何既有数值**（`dps` / `dps_real` / `dps_vs` 逐位不变），
      所以对拍（`gt_regress --golden`）零漂移。
    """
    from . import rr as _RR
    try:
        from . import combat as _CB
    except Exception:                                    # noqa: BLE001
        _CB = None

    _armor = rep.get('armor') or {}
    _red = _armor.get('reduce') or {}
    armor_eff = float(_red.get('armor_after') or 0.0)
    absorb = float(_armor.get('absorption') or 0.0)
    pierce_pct = float(rep.get('pierce_ratio') or 0.0)
    bmap = {}
    for _r in ((rep.get('vs') or {}).get('rows') or []):
        bmap[_r.get('bucket')] = _r
    hit_exp = float((rep.get('hit') or {}).get('expected') or 1.0)

    tot = {}
    for rec, h in (rep.get('skills') or {}).items():
        dp = float(h.get('dps') or 0.0)
        if dp <= 0:
            continue
        ws = []
        for r in (h.get('rows') or []):
            w = _RR.row_w(h, r)
            if w > 0:
                ws.append((r, w))
        totw = sum(w for _, w in ws) or 1.0
        out = []
        for r, w in ws:
            t = r.get('type')
            if not t:
                continue
            sh = w / totw
            b = _RR.TYPE_BUCKET.get(t)
            br = bmap.get(b) or {}
            rr_mult = float(br.get('mult') or 1.0)
            armor_pct = None
            if (armor_eff > 0 and _CB is not None
                    and _CB.armor_applies(t, r.get('is_dot'))):
                mid = (float(r.get('min') or 0.0) + float(r.get('max') or 0.0)) / 2.0
                if mid > 0:
                    armor_pct = round(
                        float(_CB.armor_expect(mid, armor_eff, absorb)['reduction_pct']), 2)
            k_arm = (1.0 - (armor_pct or 0.0) / 100.0)
            d_panel = dp * sh
            d_vs = d_panel * hit_exp * rr_mult
            out.append({
                'type': t, 'zh': TYPE_ZH.get(t, t),
                'is_dot': bool(r.get('is_dot')), 'dur': r.get('dur'),
                'per_hit': [r.get('min'), r.get('max')],
                'weapon_part': [r.get('w_min'), r.get('w_max')],
                'share': round(sh, 4),
                'bucket': b, 'res': br.get('res'), 'res_base': br.get('base'),
                'rr_add': br.get('rr_add'), 'rr_pct': br.get('rr_pct'),
                'rr_mult': round(rr_mult, 3),
                'armor_pct': armor_pct,
                'armor_pierce_pct': (round(pierce_pct, 1)
                                     if t in ('physical', 'pierce') else 0.0),
                'combat_mult': round(rr_mult * k_arm, 3),
                'dps_panel': round(d_panel, 1),
                'dps_vs': round(d_vs, 1),
                'dps_final': round(d_vs * k_arm, 1),
            })
        out.sort(key=lambda x: -x['dps_final'])
        h['type_rows'] = out
        h['dps_final'] = round(sum(x['dps_final'] for x in out), 1)
        for x in out:
            e = tot.setdefault(x['type'], {'type': x['type'], 'zh': x['zh'],
                                           'is_dot': x['is_dot'],
                                           'dps_panel': 0.0, 'dps_vs': 0.0,
                                           'dps_final': 0.0})
            e['dps_panel'] += x['dps_panel']
            e['dps_vs'] += x['dps_vs']
            e['dps_final'] += x['dps_final']

    rows = []
    s_panel = sum(v['dps_panel'] for v in tot.values()) or 1.0
    s_final = sum(v['dps_final'] for v in tot.values()) or 1.0
    for v in tot.values():
        rows.append({'type': v['type'], 'zh': v['zh'], 'is_dot': v['is_dot'],
                     'dps_panel': round(v['dps_panel'], 1),
                     'dps_vs': round(v['dps_vs'], 1),
                     'dps_final': round(v['dps_final'], 1),
                     'share_panel': round(v['dps_panel'] / s_panel, 4),
                     'share_final': round(v['dps_final'] / s_final, 4)})
    rows.sort(key=lambda x: -x['dps_final'])
    rep['type_rows'] = rows
    rep['dps_final'] = round(s_final, 1)
    return rep


# ------------------------------------------- 降敌 DA → 命中/暴击乘区（#61）

def enemy_da_cut(skills, levels):
    """角色技能对**敌方防御能力（DA）**的削减 → `(合计, [(记录, 值, 字段, 覆盖率), …])`。

    ★★ 为什么需要（2026-09-20 接入，见 `docs/pitfalls.md` **#61**）：
    PTH = f(我方 OA, **敌方 DA**) 决定命中窗与暴击窗（`gd/combat.py`）。
    角色给自己带的**降敌 DA** 会直接把 PTH 推高、把未命中面变成暴击面 ——
    旧实现直接拿敌方档的**原始** DA 算 PTH ⇒ **暴击乘区被系统性低估**。
    实测（Sam，`pool:3:champion+hero` lv73）：面板 102,361 那一版，
    PTH 95.96 / 暴击 6.96% / 期望 1.0272；扣掉自身降敌 DA 后
    1272.7 / 107.95 / 17.56% / **1.1739（+14.3%）**。

    **两族判据**（都来自技能 `stats`，逐级取值走 `_at`）：
      · `offensiveSlowDefensiveAbilityMin` —— 明确的「缓慢降低目标防御能力」
        （例：血莽 `werewolf3`，12 级 = **250**，持续 3s）。
      · `characterDefensiveAbility` **负值** —— debuff 技能对**目标**施加的 DA 削减
        （例：刺骨战吼 `bonechillingcry1`，12 级 = **−124**；正值是**自身** DA 加成，
        如阿玛托克契约 +20…260，**不计**）。

    **覆盖率**：有冷却的按 `min(1, 持续/冷却)` 折算（刺骨战吼 12s/6s ⇒ 100%）；
    无冷却（攻击触发的 debuff，如血莽 3s，aps 3.43 稳刷）⇒ 100%。
    """
    rows = []
    for rec, lv in (levels or {}).items():
        try:
            lv = int(lv or 0)
        except (TypeError, ValueError):
            continue
        if lv <= 0:
            continue
        st = (skills.get(rec) or {}).get('stats') or {}

        def _cov(stats, dur_key):
            dur = abs(_at(stats.get(dur_key) or [], lv) or 0.0)
            cd = abs(_at(stats.get('skillCooldownTime') or [], lv) or 0.0)
            return min(1.0, dur / cd) if (cd > 0 and dur > 0) else 1.0

        slow = _at(st.get('offensiveSlowDefensiveAbilityMin') or [], lv)
        if slow > 0:
            cov = _cov(st, 'offensiveSlowDefensiveAbilityDurationMin')
            rows.append((rec, float(slow) * cov, 'offensiveSlowDefensiveAbility', cov))
            continue
        own = _at(st.get('characterDefensiveAbility') or [], lv)
        if own < 0:
            cov = _cov(st, 'skillActiveDuration')
            rows.append((rec, float(-own) * cov, 'characterDefensiveAbility(负)', cov))
    rows.sort(key=lambda x: -x[1])
    return round(sum(r[1] for r in rows), 1), rows


def final_report(archetype, levels, plan=None, db=None, only_listed=True,
                 base_aps=None, folded=None, skill_records=None, attr_pct=None,
                 conversions=None, skill_mods=None, devotion_levels=None,
                 rr=None, enemy_res=None, attrs=None, level=None, enemy=None,
                 crit_dmg_pct=None, enemy_armor=None, armor_reduce=None,
                 enemy_armor_src=None, equipped_sk=None, item_wps=None,
                 weapon_st=None):
    """★ 最终伤害明细（对应游戏里「武器攻击」那条面板）。

    公式（每一伤害类型独立计算）：
        最终伤害 = ( 技能固定值 + 武器伤害% × 武器基础伤害 ) × (1 + %加成) + 装备附加平伤
    - 武器基础伤害**已含「物理→穿刺」转换**（`offensivePierceRatioMin`）
    - %加成 = Σ装备各槽(该类型的 Modifier + 总伤害 Modifier) + Σ技能自身(同字段)
               + **属性加成**（`attr_pct`：狡诈→物理/穿刺、精神→元素与魔法，GD 官方公式）
    - 攻击频率：武器池按 `skillChanceWeight` 加权（默认攻击权重 = `max(0, 100-Σw)`）；其余有冷却用 1/冷却
    - **★ 装备/套装对技能的「改造」**（`skill_mods`，见 `gd_skillmod`）：
      平伤累加、专属转化（如猛袭 100% 物理→冰冷）、专属 % 加成、冷却增减、武器伤害% 取大
    - 不含：怪物抗性、暴击、命中率、DoT 的持续时间折算

    `base_aps=None` 时按 GD 常识取 1.75（调用方应传武器真实基础攻速，见 `gd_skillmod.weapon_base_aps`）。

    两种调用方式：
      ① 传 `plan`（方案 JSON，GT id）→ 内部 `fold_plan` 折叠词缀/镶嵌/附魔
      ② 传 `folded`（已折叠好的 {槽位: 字段}）→ 用于**存档实测校验**
         （`gd_dps_check.py` 从真实角色读出记录名后自行折叠）
    `skill_records` 可显式指定要算的技能（默认用流派 `core_skills`）。

    `item_wps`（2026-09-20 新增）：**装备授予的武器池技能（WPS）**注入表，
    由 `gd.procs.wps_pool(c['base_gids'])` 产出。不传 ⇒ 完全不注入，**零漂移**。
    为什么需要：`_src_recs` 只来自形态 / 存档技能表，而装备授予的 WPS 走
    `itemSkillName` 另一条路 ⇒ 它们的 `skillChanceWeight`（武器池权重）从来
    没进过池子（实测 werewolf 方案蒸发 37 点权重）。详见 `gd/procs.py` 的
    `wps_of_item()`：记录路径要经 `skillprov` 翻译，权重只存在于 `itemSkills` 表。
    """
    # ★★ 2026-09-21：**武器类型门控**必须在**加成收集之前**（本函数下面 ①~③ 步会把
    #   `levels` 里每条记录的 % 加成折进 `pct` / `sk_pct`）。
    #   ⚠ 放晚了**完全无效** —— 实测只过滤 `_recs` 时面板**一位不变**（83,536 → 83,536），
    #     因为星位的被动加成早就收完了。
    #   规则：技能记录顶层的 `Axe` / `Axe2h` / `Spear2h` / `Shield`… 是**硬前提**，
    #   武器不对 ⇒ 该技能/星位**完全不生效**（连被动加成也没有）。
    #   实例：**狂战士**六颗星位全要斧/矛，Sam 双持**剑** ⇒ 已点 3 点全废。
    #   `weapon_st=None` ⇒ 整段跳过、**零漂移**。
    #   ★★ **两条输入都要过门**（2026-09-22 补）：`levels` 与 `devotion_levels`
    #      —— 后者是 `tune_devotion` / `eval_build_variants` 走的**显式星座通道**，
    #      只在 `levels` 上做门控的话，那两条路上的受限星位**一点都拦不住**
    #      （实测：`tune_devotion` 的 `ev()` 恒为「零门控」，狂战士照样计收益）。
    _weapon_dropped, _gone0 = [], set()
    if weapon_st and db is not None:
        try:
            from . import procs as _PCS0
        except Exception:                                        # noqa: BLE001
            _PCS0 = None
        # ★ 字段读取器：生产链路传的是 `gd.DB.open_all()`（有 `.fields`）；
        #   `gd.DB.load()`（离线库 `OfflineDB`）没有 —— 回落到 `dbr.open_all()`
        #   （同一单例、有缓存），而不是**静默跳过**门控（那会让数字悄悄偏掉）。
        _fld = getattr(db, 'fields', None)
        if _fld is None:
            try:
                from . import dbr as _DBR
                _fld = _DBR.open_all().fields
            except Exception:                                    # noqa: BLE001
                _fld = None
        if _PCS0 is not None and _fld is not None:
            def _wgate(_d):                                      # noqa: ANN001
                """{记录: 等级} → (保留的, [{'rec','why'}…])。"""
                _keep, _gone = {}, []
                for _r0, _v0 in (_d or {}).items():
                    _ok0, _why0 = _PCS0.weapon_type_ok(_fld(_r0) or {}, weapon_st)
                    if _ok0:
                        _keep[_r0] = _v0
                    else:
                        _gone.append({'rec': _r0, 'why': _why0})
                return _keep, _gone
            if isinstance(levels, dict):
                levels, _g0 = _wgate(levels)
                for _e0 in _g0:
                    _e0['channel'] = 'levels'
                _weapon_dropped += _g0
            if isinstance(devotion_levels, dict):
                devotion_levels, _g1 = _wgate(devotion_levels)
                for _e1 in _g1:
                    _e1['channel'] = 'devotion'
                _weapon_dropped += _g1
            _gone0 = {_e2['rec'] for _e2 in _weapon_dropped}

    base_aps = 1.75 if base_aps is None else base_aps
    arch = (_load('archetypes.json', {}) or {}).get(archetype) or {}
    skills = _load('skills.json', {}) or {}
    folded = folded if folded is not None else fold_plan(plan or {})
    from . import dmg as _DMG          # 来源拆解 / 转化链 / DoT 时长（官方 Step 2–3）

    # ① 武器基础伤害（**原样收集**；护甲穿透不在这里做，见下方 ④）
    wflat, wdot, phys_left = {}, {}, [0.0, 0.0]
    ratio_used = 0.0
    for slot in WPN_SLOTS:
        f = folded.get(slot) or {}
        fl, dt = parse_mm(f)
        ratio_used = max(ratio_used, min(max((f.get('offensivePierceRatioMin') or 0.0) / 100.0,
                                             0.0), 1.0))
        # ★★ 官方规则：**护甲穿透是所有转化里最后一步**，作用于**整击残余物理**
        #    （不只武器物理），且门槛是「该技能有武器伤害%」。
        #    旧实现在这里就把武器物理转掉了 —— 位置与作用域都是错的（Q5）。
        #    现在只记录比例，真正转换在 `gd/dmg.py::_apply_pierce` 里做。
        for d, tgt in ((fl, wflat), (dt, wdot)):
            for t, (lo, hi) in d.items():
                e = tgt.setdefault(t, [0.0, 0.0])
                e[0] += lo
                e[1] += hi

    # ② 全槽 % 加成（含武器）＋ 非武器槽的附加平伤 ＋ **技能自身的 % 加成**
    pct = {t: 0.0 for t in TYPE_ORDER}
    # ★★ OA / DA / 暴伤：官方方程（`gd/combat.py`）需要它们。
    #    Q6 之前这三者在伤害模型里**完全不存在** —— 优化器给 `crit` 权重 1.00，
    #    而 DPS 对它零响应（口径撕裂）。这里开始收集，后面在 ⑥ 步真正接进模型。
    oa_flat = oa_mod = da_flat = da_mod = crit_dmg = 0.0
    gear_durs = {}          # 装备/光环带来的 DoT 时长（`offensiveSlow*DurationMin`）
    add_flat = {}
    add_fl, add_dt = {}, {}
    spd = 0.0
    # ★★ 2026-09-20「伤害循环文档」：**归因账本**
    #    用户要「这一条伤害里，武器给多少 / 装备给多少 / 套装给多少 / 星座给多少」
    #    —— 而 `pct` / `add_fl` 都是**全场求和**，结构上答不出来。
    #    这里与 `pct` / `add_fl` **同步**记一份「按槽位分账」的副本：
    #      `slot_pct['套装加成']` → 套装给该类型的 %；`slot_pct['主手']` → 主手给……
    #      `slot_flat['胸甲']` → 胸甲给的平伤（**加成前**原始值）
    #    ⚠ 只记不改 —— `pct` / `add_fl` 的累加式一个字节都不动（零漂移）。
    slot_pct = {}
    slot_flat = {}
    # ★★ 2026-09-22 性能：归因账本（`slot_pct` / `slot_flat`）**默认只在需要时记**。
    #   它是给「伤害循环文档」看的**报告产物**，却在**每一次** `final_report` 里都算
    #   —— 搜索时一次评估调一次 ⇒ 一份报告用的账本被算了 ~10 万遍。
    #   实测它是单次评估里最大的可省项（`dict.setdefault` 5,300 次/评估）。
    #   ⚠ 只跳过**真实装备槽**的记账：`slot_flat['套装加成']` 是 `base_parts` 的输入
    #     （`_s_all` 要从「装备」里减掉套装），两个**虚拟槽**必须照记，否则归因会串。
    #   `GD_ATTRIB=0` 时 `slot_pct` 里只剩虚拟槽 —— 搜索路径用（不生成报告），
    #   报告路径（`gd dps` / `plan_cycle` / `planreport`）不设这个变量 ⇒ **逐位零漂移**。
    _attrib = os.environ.get('GD_ATTRIB', '1') != '0'
    for slot, f in folded.items():
        _led = _attrib or (slot in _VIRT_SLOTS)
        _sp = slot_pct.setdefault(slot, {}) if _led else None
        for k, v in f.items():
            if k in GEAR_MOD_TYPES:
                for t in GEAR_MOD_TYPES[k]:
                    pct[t] = pct.get(t, 0.0) + v
                    if _led:
                        _sp[t] = _sp.get(t, 0.0) + v
        tot = f.get('offensiveTotalDamageModifier') or 0.0
        if tot:
            for t in TYPE_ORDER:
                pct[t] = pct.get(t, 0.0) + tot
                if _led:
                    _sp[t] = _sp.get(t, 0.0) + tot
        spd += (f.get('characterAttackSpeedModifier') or 0.0)
        spd += (f.get('characterTotalSpeedModifier') or 0.0)
        if slot not in WPN_SLOTS:
            fl, dt = parse_mm(f)
            # ★ DoT 时长：装备上的「X 流血 / 3 秒」也必须能读到（否则覆盖率无从算）
            for _t, _s in _DMG.durations(f).items():
                gear_durs[_t] = max(gear_durs.get(_t, 0.0), _s)
            # ★ DoT 平伤也要收：装备上的「X 流血 / 3 秒」（戒指、圣物、手套…）
            #   走的是 `offensiveSlowBleedingMin`，旧代码只取 `fl` 把它们全丢了。
            _sf = slot_flat.setdefault(slot, {}) if _led else None
            for src, tgt in ((fl, add_fl), (dt, add_dt)):
                for t, (lo, hi) in src.items():
                    e = add_flat.setdefault(t, [0.0, 0.0])
                    e[0] += lo
                    e[1] += hi
                    g = tgt.setdefault(t, [0.0, 0.0])
                    g[0] += lo
                    g[1] += hi
                    if _led:
                        e2 = _sf.setdefault(t, [0.0, 0.0])
                        e2[0] += lo
                        e2[1] += hi

    # ★ 技能自身的加成也要算（用户看的「最终伤害」是**所有**加成后的）：
    #   双刃 +穿刺%、气爆 +穿刺%、野兽形态 +攻速/伤害倍率 …
    gear_pct = dict(pct)
    sk_pct = {t: 0.0 for t in TYPE_ORDER}
    # ★★ 2026-09-20：归因账本 —— 技能 % 加成**按技能记录**分账
    #    （用户要「这一条伤害里哪个技能给了多少 %」；`sk_pct` 只有总和）。
    #    值与 `sk_pct` 逐位同源，只是多一层分组，**不改累加式**（零漂移）。
    sk_pct_by_rec = {}
    sk_mult_by_rec = {}
    dmg_mult = 0.0                      # offensiveDamageMultModifier（独立乘区）
    for rec, lv in (levels or {}).items():
        if not db or not lv or lv <= 0:
            continue
        # ★★ 必须用 fields_with_buff：秘术的「附身」「索拉尔的巫火」这类是**壳技能**，
        #    加成写在 `_buff.dbr` 里；只用 db.fields 会把它们的 % 加成**整块丢掉**
        #    —— 批量验证实测：活力 −827%、活力衰减 −753%、混乱 −242%（10/10 个 build 全中）
        f = fields_with_buff(db, rec)
        _sr = sk_pct_by_rec.setdefault(rec, {})
        for k, vals in f.items():
            v = _at(vals, lv)
            if not v:
                continue
            if k in GEAR_MOD_TYPES:
                for t in GEAR_MOD_TYPES[k]:
                    sk_pct[t] += v
                    _sr[t] = _sr.get(t, 0.0) + v
            elif k == 'offensiveTotalDamageModifier':
                for t in TYPE_ORDER:
                    sk_pct[t] += v
                    _sr[t] = _sr.get(t, 0.0) + v
            elif k == 'offensiveDamageMultModifier':
                dmg_mult += v
                sk_mult_by_rec[rec] = sk_mult_by_rec.get(rec, 0.0) + v
            elif k == 'characterAttackSpeedModifier':
                spd += v
            elif k == 'characterTotalSpeedModifier':
                spd += v
            elif k == 'characterOffensiveAbility':
                oa_flat += v
            elif k == 'characterOffensiveAbilityModifier':
                oa_mod += v
            elif k == 'characterDefensiveAbility':
                da_flat += v
            elif k == 'characterDefensiveAbilityModifier':
                da_mod += v
            elif k == 'offensiveCritDamageModifier':
                crit_dmg += v
    for t in TYPE_ORDER:
        pct[t] += sk_pct[t]
    at_pct = {t: float(v) for t, v in (attr_pct or {}).items()}
    for t in TYPE_ORDER:
        pct[t] += at_pct.get(t, 0.0)

    # ★★ 星座（虔诚）加成 —— 与装备/技能**同层**并入「加成总池」。
    #    传 `devotion_levels={'records/skills/devotion/tier1_08a.dbr': 1, ...}` 即可。
    dev = devotion_contrib(devotion_levels, db) if devotion_levels else {
        'pct': {}, 'flat': {}, 'dot': {}, 'spd': 0.0, 'mult': 0.0, 'nodes': 0}
    for t, v in (dev['pct'] or {}).items():
        pct[t] = pct.get(t, 0.0) + v
    for src, tgt in ((dev['flat'] or {}, add_fl), (dev['dot'] or {}, add_dt)):
        for t, (lo, hi) in src.items():
            e = add_flat.setdefault(t, [0.0, 0.0])
            e[0] += lo
            e[1] += hi
            g = tgt.setdefault(t, [0.0, 0.0])
            g[0] += lo
            g[1] += hi
    spd += dev['spd'] or 0.0
    dmg_mult += dev['mult'] or 0.0

    aps = base_aps * (1.0 + spd / 100.0)
    mult = 1.0 + dmg_mult / 100.0

    # ★★★ ③-b **OA / DA / 命中乘区**（官方 Step 4）—— 之前整块缺失。
    #   官方面板方程（`window.combatformulas`，逐字）：
    #     OA = (oaFlat + level*12 + (cunning+bonus)*0.5) * (1 + oaMod/100) + 53
    #     DA = (daFlat + level*12 + (physique+bonus)*0.5) * (1 + daMod/100) + 53
    #   命中判定用**我方 OA vs 敌方 DA**（敌方 DA 取 `gd/enemy.py` 的真值），
    #   再按 roll 1..100 的窗口切出「命中 / 暴击 / 未命中」期望倍率。
    #   `crit` 从此真正影响 DPS —— 修掉 Q6 那条「优化器认为值钱、模型不认」的撕裂。
    from . import combat as _CB
    _lv = float(level or 0.0)
    _at3 = attrs if isinstance(attrs, dict) else {}
    oa = (oa_flat + _lv * 12.0 + float(_at3.get('cunning') or 0.0) * 0.5) \
        * (1.0 + oa_mod / 100.0) + 53.0
    da_self = (da_flat + _lv * 12.0 + float(_at3.get('physique') or 0.0) * 0.5) \
        * (1.0 + da_mod / 100.0) + 53.0
    if crit_dmg_pct is not None:
        crit_dmg = float(crit_dmg_pct)
    _ep = enemy if isinstance(enemy, dict) else None
    _eda = (_ep or {}).get('da')
    # ★★ 降敌 DA（2026-09-20 接入，见 `docs/pitfalls.md` #61）：判定命中/暴击用的敌 DA
    #   必须**先扣掉角色自己给的削减**，否则暴击乘区被系统性低估（Sam 实测 −14.3%）。
    _da_cut, _da_cut_rows, _eda_eff = 0.0, [], None
    if _eda:
        _da_cut, _da_cut_rows = enemy_da_cut(skills, levels)
        _eda_eff = max(0.0, float(_eda) - _da_cut)
        pth_v = _CB.pth(oa, _eda_eff)
        hm = _CB.hit_mult(pth_v, crit_dmg)
    else:
        pth_v, hm = None, {'expected': 1.0, 'hit_chance': None, 'crit_chance': None,
                           'miss_chance': None, 'discount': 1.0, 'faces': None,
                           'crit_dmg_pct': crit_dmg}

    children_pre = skill_children(skills)

    # ★ **光环/被动/开关提供的「平伤」要加到每次攻击上**
    #   （阿斯特堪之心、阿玛托克契约、夜之寒气、双刃 这类都在这里给平伤）
    aura_flat, aura_fl, aura_dt = {}, {}, {}
    _kids = {c for v in children_pre.values() for c in v}   # 已并进父技能的，别再当光环加一遍
    for r, lv in (levels or {}).items():
        d = skills.get(r) or {}
        if d.get('kind') in ATTACK_KINDS or not lv or lv <= 0 or r in _kids:
            continue
        f = fields_with_buff(db, r) if db else None
        if not f:
            continue
        fl, dt = parse_mm(f, lv)
        for src, tgt in ((fl, aura_fl), (dt, aura_dt)):
            for t, (lo, hi) in src.items():
                e = aura_flat.setdefault(t, [0.0, 0.0])
                e[0] += lo
                e[1] += hi
                g = tgt.setdefault(t, [0.0, 0.0])
                g[0] += lo
                g[1] += hi

    # ★★★ **「100% 武器攻击」的统一基础**（= 武器平伤 + 光环/被动平伤 + 非武器槽平伤）
    #   这三者是**同一次攻击**的三个来源，在 GD 里**都会被技能的「武器伤害%」一起缩放**。
    #   实测（Sam 野性利爪，150% 武器伤害）：
    #     游戏 冰伤 页3 = 58~83（100% 武器攻击）→ 技能提示框 = 99~141 ≈ 1.5×58 + 小额技能自带
    #   ⇒ 光环平伤和装备平伤**必须乘 wd**，不能只加一次裸值。
    #   旧实现把 `aura_flat` 直接并进每个技能的 merged（不乘 wd）→ 而且 `weapon_attack` 里
    #   已经算过一遍 ⇒ **重复计算**（实测「多出物理 14~18」「冰冷偏高」的根因）。
    base100 = {}
    for t in sorted(set(list(wflat) + list(wdot) + list(aura_fl)
                        + list(aura_dt) + list(add_fl) + list(add_dt))):
        _dot = t in DOT_TYPES
        _w = (wdot if _dot else wflat).get(t, [0.0, 0.0])
        _a = (aura_dt if _dot else aura_fl).get(t, [0.0, 0.0])
        _g = (add_dt if _dot else add_fl).get(t, [0.0, 0.0])
        base100[t] = [_w[0] + _a[0] + _g[0], _w[1] + _a[1] + _g[1]]

    # ★★ 2026-09-20「伤害循环文档」：把 `base100` 的四个来源**分开记一份账**。
    #    为什么必须分：`base100` 已经把「武器 + 光环/被动 + 非武器槽装备 + 星座」
    #    求和了，结构上答不出「这一条伤害里武器占多少」。
    #    ⇒ 这里按来源各建一份 `{类型: [lo, hi]}`，供 `_make_hit` 打 `origins` 标签。
    #    ⚠ **`base100` 的构造式一个字节都没动** ⇒ 主路径与历史逐位一致（零漂移）。
    #    ⚠ `add_fl/add_dt` 里已经混进星座平伤（见上方 dev 那段），
    #      故「装备平伤」= `add_*` − 对应的 `dev['flat'] / dev['dot']`。
    def _merge(*dicts):
        """把若干 `{类型: [lo,hi]}` 逐键**相加**（不能直接用 `|` —— 同键会覆盖）。"""
        out = {}
        for _d in dicts:
            for _t, _v in (_d or {}).items():
                e = out.setdefault(_t, [0.0, 0.0])
                e[0] += float(_v[0])
                e[1] += float(_v[1])
        return out

    _w_all = _merge(wflat, wdot)
    _a_all = _merge(aura_fl, aura_dt)
    _d_all = _merge(dev['flat'] or {}, dev['dot'] or {})
    _g_all = _merge(add_fl, add_dt)          # 含「星座平伤」与「套装平伤」
    for _t, _v in _d_all.items():            # 减掉星座 ⇒ 剩下的才是装备本体
        if _t in _g_all:
            _g_all[_t] = [_g_all[_t][0] - _v[0], _g_all[_t][1] - _v[1]]
    _s_all = _merge(slot_flat.get('套装加成') or {})
    for _t, _v in _s_all.items():            # 再减掉套装 ⇒ 单列
        if _t in _g_all:
            _g_all[_t] = [_g_all[_t][0] - _v[0], _g_all[_t][1] - _v[1]]
    _pos = lambda d: {t: v for t, v in d.items() if v[0] or v[1]}      # noqa: E731
    base_parts = {'武器': _pos(_w_all), '光环/被动': _pos(_a_all),
                  '星座': _pos(_d_all), '套装': _pos(_s_all),
                  '装备': _pos(_g_all)}
    # 自证：五源之和必须逐位等于 `base100`（否则归因账本与主口径分叉了）
    try:
        _sum5 = _merge(*base_parts.values())
        _keys = set(_sum5) | set(base100)
        _bad = [t for t in _keys
                if abs(_sum5.get(t, [0.0, 0.0])[0] - base100.get(t, [0.0, 0.0])[0]) > 1e-6
                or abs(_sum5.get(t, [0.0, 0.0])[1] - base100.get(t, [0.0, 0.0])[1]) > 1e-6]
        if _bad:
            import warnings
            warnings.warn('base_parts 与 base100 不一致：%s' % _bad)
    except Exception:
        pass

    # ★ 把「套装加成 / 星座节点」这两个虚拟槽从「装备」里拆出来（对齐 GT 的口径）
    bucket_pct = {}
    for _name in ('套装加成', '星座节点'):
        _f = (folded or {}).get(_name)
        if not _f:
            continue
        _d = {}
        for _k, _v in _f.items():
            _ts = GEAR_MOD_TYPES.get(_k)
            if _ts:
                for _t in _ts:
                    _d[_t] = _d.get(_t, 0.0) + _v
        bucket_pct[_name] = _d

    # ③ 逐技能：先把每一击的最终伤害算出来
    children = children_pre
    rep = {'archetype': archetype, 'label': arch.get('label'),
           'weapon': wflat, 'weapon_dot': wdot, 'pct': pct,
           'gear_pct': gear_pct, 'skill_pct': sk_pct, 'attr_pct': at_pct,
           'dev_pct': dict(dev['pct'] or {}), 'dev_flat': dict(dev['flat'] or {}),
           'dev_nodes': dev['nodes'],
           'bucket_pct': bucket_pct,
           'dmg_mult': round(dmg_mult, 1),
           'add_flat': add_flat, 'speed_pct': round(spd, 1), 'aps': round(aps, 3),
           'base_aps': base_aps, 'pierce_ratio': round(ratio_used * 100, 1),
           # ★ 2026-09-20「伤害循环文档」：归因账本（纯新增键，消费方自己取）
           'base_parts': base_parts, 'slot_pct': slot_pct,
           'slot_flat': slot_flat, 'sk_pct_by_rec': sk_pct_by_rec,
           'sk_mult_by_rec': sk_mult_by_rec,
           'skills': {}, 'buff': {}, 'rotation': {}, 'dps': 0.0}
    hits = {}
    # ★ 形态门控：只在该形态**显式声明** `form` 时生效（见 `form_blocks`）。
    _src_recs = skill_records or arch.get('core_skills') or []
    _recs = form_gate(_src_recs, arch, skills)
    rep['form'] = arch.get('form')
    rep['form_dropped'] = sorted(set(_src_recs) - set(_recs))

    # ★★ 2026-09-21：**武器类型门控**（星座星位 / 专精技能 / 物品技能通用）。
    #
    #   游戏规则：技能记录顶层的武器类型键（`Axe` / `Axe2h` / `Spear2h` / `Shield`…）
    #   是**硬前提** —— 武器不对，这个技能/星位**完全不生效**，连它的被动加成也没有。
    #   最典型：**狂战士**星座六颗星位全带 `Axe/Axe2h/Spear2h`（l10n
    #   `tagDevotion_RequiresAxeSpear` = 「需要斧或矛。」）。
    #
    #   实测后果（Sam 双持**剑**）：已点的 `tier2_25a/d/e` 三颗星位一分钱不产生，
    #   而模型给它们算了 **+4.28%**（面板 83,536 → 80,532）⇒ 搜索方向被带偏。
    #
    #   `weapon_st=None`（旧调用方 / 拿不到装备信息的路径）⇒ **整段跳过、零漂移**。
    # ★ 武器门控的**第二步**：`_recs` 也剔掉（第一步在函数开头处理了 `levels`，
    #   那是加成来源；这里管的是**技能循环**）。两边都剔才彻底。
    if _gone0:
        _recs = [r for r in _recs if r not in _gone0]
    # ★★ 2026-09-21：**装备授予的 WPS 也必须过形态门控**。
    #   门控本来只作用于 `_src_recs`，而下面那段 `item_wps` 注入走的是**另一条路**
    #   ⇒ 即使形态声明了 `form`，装备给的 WPS（Sam：「击倒」「恐狼之爪」）也会
    #   绕过门控留在武器池里。变身后默认攻击由野性利爪接管 ⇒ WPS 一次都不触发。
    _form_now = arch.get('form')
    _granted_now = form_granted(arch, skills) if _form_now else set()
    _wps_dropped = []

    # ★★ 2026-09-20：**装备授予的武器池技能（WPS）入池**（用户批准）。
    #
    #   背景：`_src_recs` 只来自形态 `core_skills` / 存档技能表，而**装备授予的
    #   WPS** 走 `allItems[gid].itemSkillName` 另一条路 ⇒ 它们从来没进过武器池。
    #   实测 werewolf 方案：`sk325`（恐狼之爪，勋章）权重 12 +
    #   `sk519`（毁伤，圣物）权重 25 ⇒ `procs=[]`、`weight_total=0`，
    #   **37 点权重凭空蒸发**、默认攻击占比虚高到 100%（正确应为 63%）。
    #
    #   ⚠ 两层坑（见 `gd/procs.py::wps_of_item`）：① `skXXXX` 要经 `skillprov`
    #     翻成记录路径才能进技能表；② `skillChanceWeight` **不在 `.dbr` 记录、
    #     也不在 `skills.json`**，只在离线库 `itemSkills` 表 ⇒ 必须连权重一起注入
    #     （`_wps_weight`），否则记录进了表但权重仍是 0，会被判成
    #     「又一个默认攻击候选」而不是武器池技能。
    #
    #   `item_wps=None`（旧调用方 / 无装备信息的路径）⇒ 整段跳过，**零漂移**。
    _wps_weight, _wps_levels = {}, {}
    for _e in (item_wps or ()):
        _r = _e.get('rec')
        if not _r or _r in _recs:
            continue
        if _form_now and form_blocks(_r, _form_now, _granted_now, skills):
            _wps_dropped.append(_r)
            continue
        _recs.append(_r)
        _wps_levels[_r] = int(_e.get('level') or 1)
        try:
            _w = float(_e.get('weight') or 0.0)
        except (TypeError, ValueError):
            _w = 0.0
        if _w > 0:
            _wps_weight[_r] = _w
    if _wps_levels:
        # 本地副本：**不改调用方传进来的 dict**；已有点过的技能保留角色自己的等级
        levels = dict(levels or {})
        for _r, _lv in _wps_levels.items():
            levels.setdefault(_r, _lv)

    # ★★ 来源合法性（A 方案 · 2026-09-20）：物品技能必须**由已装备物品授予**
    #   （`equipped_sk`）或**等级够得到**（`level`），否则不计入。
    #
    #   为什么必须做：`data/archetypes.json` 的 `fangs`（完美姿态）三条技能全部来自
    #   `records/skills/itemskillsgdx3/relics/*`，而授予它的遗物 `it15928` 是
    #   **lv90** 的 GDX3 传奇遗物 ⇒ lv71 存档根本搭不出来。旧实现把它们当角色自带
    #   技能无条件计入，于是 `fangs` 以 104,966（补词缀 114,782）排在六形态第一 ——
    #   一个**不可构建**的 DPS。
    #   口径与边界见 `gd/skillprov.py` 的模块 docstring（非物品技能恒合法；
    #   反查不到授予者的恒合法）。**两个参数都不传 ⇒ 完全不校验（零漂移）**。
    _illegal = []
    if equipped_sk is not None or level is not None:
        from . import skillprov as _SP
        _kept, _illegal = _SP.filter_legal(_recs, level=level, equipped_sk=equipped_sk)
        _recs = _kept
    rep['skill_legality'] = {
        'checked': bool(equipped_sk is not None or level is not None),
        'level': level,
        'equipped_sk': (sorted(equipped_sk) if equipped_sk is not None else None),
        'dropped': [{'rec': _r, 'zh': os.path.basename(_r).replace('.dbr', ''),
                     'kind': _v.get('kind'), 'min_level': _v.get('min_level'),
                     'reason': _v.get('reason'),
                     'providers': [{'gid': g, 'k': k} for g, k in
                                   (_v.get('providers') or ())]}
                    for _r, _v in _illegal],
    }
    if _illegal:
        rep['skill_legality']['line'] = '；'.join(
            '%s（%s）' % (os.path.basename(_r).replace('.dbr', ''), _v.get('reason'))
            for _r, _v in _illegal)

    def _pct_parts(t):
        """某伤害类型的 **% 加成逐来源分层**（归因账本；与 `pct` 同源）。

        `装备` 已经扣掉「套装加成 / 星座节点」两个**虚拟槽** —— 它们在
        `folded` 里以槽的形式存在，所以先被并进了 `gear_pct`（见上方 `bucket_pct`）。
        """
        _b = bucket_pct or {}
        _set = (_b.get('套装加成') or {}).get(t, 0.0)
        _dvn = (_b.get('星座节点') or {}).get(t, 0.0)
        return {'装备': round(gear_pct.get(t, 0.0) - _set - _dvn, 1),
                '套装': round(_set, 1), '星座节点': round(_dvn, 1),
                '技能': round(sk_pct.get(t, 0.0), 1),
                '属性': round(at_pct.get(t, 0.0), 1),
                '星座': round((dev['pct'] or {}).get(t, 0.0), 1)}

    def _make_hit(fl, dt, wd, sk_conv, sp_extra, lv, kind=None, rec=None,
                  f=None, mods=None, tag=None, name=None, group=None,
                  base_parts=None):
        """算「一个攻击的**一击**」，返回 `hits` 条目（`None` = 这一击没有任何伤害）。

        ★★ 为什么必须抽成函数（2026-09-20）：**当角色没有点任何默认攻击(DAR)技能时**，
          左键在那个角色上仍然是**普通武器攻击**（100% 武器伤害、无技能平伤）。
          GD 的武器池技能(WPS)正是被「**带武器伤害的攻击**」触发的，所以普通攻击
          一样触发它们。旧实现在「没有 DAR」时会把整池 WPS 清空
          （见下方 `wps_blocked`，它本意是挡「纯法术默认攻击」，却顺带把
          「根本没点 DAR」也当成武器伤害=0）⇒ 任何「只靠武器池、不点 DAR」的构建
          被算成**几乎零输出**。实测：以「雪崩」为主输出的加点（雪崩 + 血牙 + 夜刃
          三个武器池，不含猛袭）算出 `default=None ｜ W=0 ｜ procs=[]`，
          总 DPS 只剩一个冷却技能 6,925（正确值应在 3 万量级）。
          抽成函数后，普通攻击与真实技能走**同一段代码**，口径不可能分叉。
        """
        # ★★ 多投射物乘区（2026-09-20，鸦人形态引出）：
        #   **默认 1.0** —— 与 grimtools 计算器同口径：对**单体目标**，环形齐射
        #   （`projectileLaunchRotation: 360`）的 N 枚投射物通常只有 1 枚能命中。
        #   只有形态**显式声明** `projectile_hits`（= 假设的有效命中数上限）时才放大 ——
        #   因为「贴脸全中」是**场景假设**，不是数据库事实，不该偷偷写进默认口径。
        #   DoT 分量**不乘**：同一目标上的 DoT 是刷新而不是叠加（见下方 `_k`）。
        #   实测（鸦人 霜暴 16 级）：`projectileLaunchNumber: 8`，直伤占约 69%。
        _n_proj = 1.0
        _pj = (arch or {}).get('projectile_hits')
        # ★ `GD_PROJ_HITS`：**口径敏感性**开关（2026-09-20 加）。
        #   为什么不写死在形态表：`projectile_hits` 是**场景假设**（贴脸几枚），
        #   不是数据库事实。形态表给一个默认（鸦人 8），但要能一键换成保守口径
        #   （1 = 与 grimtools 计算器一致）做「口径决定结论」的对照。
        #   未设环境变量时**逐位不变**（零漂移）。
        _env_pj = os.environ.get('GD_PROJ_HITS')
        if _env_pj not in (None, ''):
            try:
                _pj = float(_env_pj)
            except ValueError:
                pass
        if _pj and f is not None:
            _pv = f.get('projectileLaunchNumber')
            if isinstance(_pv, (list, tuple)):
                _pn = _at(_pv, lv) or 0.0
            elif isinstance(_pv, (int, float)):
                _pn = float(_pv)
            else:
                _pn = 0.0
            if _pn > 1.0:
                _n_proj = min(_pn, float(_pj))
        _durs = (_DMG.durations(f, lv, _at=_at, _num=_num)
                 if (f is not None and dt) else {})
        srcs = []
        for _src in (fl, dt):
            for _t, (_lo, _hi) in _src.items():
                srcs.append(_DMG.Source(
                    _t, _lo, _hi, 'skill',
                    dur=(_durs.get(_t, 0.0) if _t in DOT_TYPES else 0.0)))
        for _t, (_lo, _hi) in base100.items():
            _is_dot = _t in DOT_TYPES
            _dur = (_durs.get(_t) or gear_durs.get(_t) or 0.0) if _is_dot else 0.0
            srcs.append(_DMG.Source(_t, wd * _lo, wd * _hi, 'weapon', dur=_dur))
        # 转化顺序（官方）：① 技能专属（自带 + modifier/transmuter）
        #                    ② 全局（装备 / buff / 星座）
        #                    ③ 护甲穿透（**排最后**；仅当 `weaponDamagePct > 0`；
        #                       作用于整击残余物理，含技能平伤里的物理）
        srcs = _DMG.convert(srcs, [sk_conv, list(conversions or [])],
                            pierce_ratio=ratio_used, has_weapon_damage=(wd > 0))
        # ★★ 2026-09-20「伤害循环文档」：**分来源账本**（不参与主路径，纯记录）
        #    对 `base_parts` 的每个来源**各自**跑一次**同参数**的转化链。
        #    为什么可以分开跑：`convert()` 是**逐 Source 独立**的
        #    （`_apply_one` 只吃单个 source，`_apply_pierce` 也只按来源切分），
        #    所以「分来源各自转后求和」与「合并后一起转」在数学上等价，
        #    分开跑只是为了**看得出归属**。
        #    成本：Source 数 ~20 × 步骤 2 ⇒ 单次 < 0.1 ms，可忽略。
        #    ⚠ 主路径（`srcs` / `conv`）一个字节都没动 ⇒ 历史数值零漂移。
        _orig_srcs = {}
        for _o, _dct in (base_parts or {}).items():
            _lst = []
            for _ot, (_olo, _ohi) in _dct.items():
                _odd = _ot in DOT_TYPES
                _lst.append(_DMG.Source(
                    _ot, wd * _olo, wd * _ohi, 'base',
                    dur=((_durs.get(_ot) or gear_durs.get(_ot) or 0.0) if _odd else 0.0)))
            if _lst:
                _orig_srcs[_o] = _DMG.to_map(_DMG.convert(
                    _lst, [sk_conv, list(conversions or [])],
                    pierce_ratio=ratio_used, has_weapon_damage=(wd > 0)))
        conv = _DMG.to_map(srcs)
        rows = []
        for _t, (_blo, _bhi) in conv.items():
            if _blo <= 0 and _bhi <= 0:
                continue
            _is_dot = _t in DOT_TYPES
            _t_pct = pct.get(_t, 0.0) + sp_extra.get(_t, 0.0)   # 含该技能专属的 %
            _m = (1.0 + _t_pct / 100.0) * mult
            # ★ 多投射物只放大**直伤**；DoT 是刷新不是叠加 ⇒ 不乘（见上方 `_n_proj`）
            _k = 1.0 if _is_dot else _n_proj
            # 报告里的「武器」列 = 100% 武器攻击基础 × 武器伤害%（转化后仍落在该类型的部分）
            _w_lo = sum(s.lo for s in srcs if s.type == _t and s.origin == 'weapon')
            _w_hi = sum(s.hi for s in srcs if s.type == _t and s.origin == 'weapon')
            rows.append({'type': _t, 'is_dot': _is_dot, 'pct': round(_t_pct, 1),
                         'dur': round((_durs.get(_t) or gear_durs.get(_t) or 0.0)
                                      if _is_dot else 0.0, 2),
                         'skill': [round(max(0.0, _blo - _w_lo), 1),
                                   round(max(0.0, _bhi - _w_hi), 1)],
                         'weapon': [round(_w_lo, 1), round(_w_hi, 1)],
                         # ★ 武器来源那部分的**加成后**数值：DoT 通道要按来源分账
                         #   （见 Step 9），`weapon` 那两列是加成前的原始基础值。
                         'w_min': round(_w_lo * _m * _k, 1), 'w_max': round(_w_hi * _m * _k, 1),
                         'min': round(_blo * _m * _k, 1),
                         'max': round(_bhi * _m * _k, 1),
                         # ★ `avg` = 中点。**必须显式给**：下游按「行占比」加权的
                         #   两处（`rr.dps_by_bucket` / `rr.dps_by_type`）过去没有它，
                         #   退化成了用 `min` 当权重 ⇒ 占比被系统性压低
                         #   （min/max 差距大的技能尤其严重）。
                         'avg': round((_blo + _bhi) * _m / 2.0 * _k, 1),
                         # ★★ 2026-09-20「伤害循环文档」：**这一条伤害从哪来**
                         #   `origins` = **加成前**（转化后）的基础值，逐来源；
                         #   `skill` = 技能本体平伤（= 总 − 100%武器攻击基础，与旧两列同源）
                         #   ⚠ 键在 `origins` 里是中文来源名（武器 / 光环/被动 / 星座 /
                         #     套装 / 装备 / 技能）；五源之和 == `weapon + skill` 两列之和。
                         'origins': dict(
                             {'技能': [round(max(0.0, _blo - _w_lo), 1),
                                       round(max(0.0, _bhi - _w_hi), 1)]},
                             **{_o: [round(_v[0], 1), round(_v[1], 1)]
                                for _o, _v in ((_o, (_orig_srcs.get(_o) or {}).get(_t))
                                               for _o in (_orig_srcs or {}))
                                if _v and (_v[0] or _v[1])}),
                         # ★ 乘区的**分层**：谁给了多少 %（`mult` 是独立乘区）
                         'pct_parts': _pct_parts(_t),
                         'mult': round(mult, 4)})
        if not rows:
            return None
        rows.sort(key=lambda x: -x['max'])
        lo = sum(r['min'] for r in rows)
        hi = sum(r['max'] for r in rows)
        cd = _at(f.get('skillCooldownTime') or [], lv) if f is not None else 0.0
        for m in (mods or []):
            cd += _num(m.get('skillCooldownTime'))           # 负数 = 减冷却
        cd = max(0.0, cd)
        _h = {'level': lv, 'kind': kind, 'cooldown': round(cd, 2),
              # ★ `tag` 进 hits：默认攻击槽的归属可按中文名声明（见下方 `primary_attack`）
              'tag': tag, 'name': name, 'group': list(group or []),
              'weapon_pct': round(wd * 100, 1), 'rows': rows,
              'min': round(lo, 1), 'max': round(hi, 1),
              'avg': round((lo + hi) / 2.0, 1),
              'chance_weight': (_at(f.get('skillChanceWeight') or [], lv)
                                if f is not None else 0.0),
              'types': [r['type'] for r in rows]}
        # ★ 只在真正启用乘区时加这个键 —— 默认（1 枚）时 rep 结构与历史逐位一致
        if _n_proj > 1.0:
            _h['projectiles'] = round(_n_proj, 1)
        return _h

    for rec in _recs:
        d = skills.get(rec) or {}
        lv = (levels or {}).get(rec)
        if lv is None:
            if only_listed:
                continue
            lv = min(d.get('max_level') or 1, 16)
        if lv <= 0 or not db:
            continue
        # ★ 壳技能合并 `_buff.dbr`；★ modifier 子链并进父技能（见 skill_group）
        fl, dt, wd, used = skill_group(rec, levels, skills, db, children)
        # ★ 装备/套装对该技能的改造（`gd_skillmod`）—— 平伤、专属转化、% 加成、冷却、武器伤害%
        mods = (skill_mods or {}).get(rec) or []
        sk_conv = list(conversions or []) + mod_conversions(mods)
        sp_extra = mod_pct(mods)          # ★ 该技能**专属**的 % 加成（不要覆盖外层的 sk_pct）
        if mods:
            for m in mods:
                mfl, mdt = parse_mm(m)      # 改造是 rank-1：标量或 1 元素列表
                for src, tgt in ((mfl, fl), (mdt, dt)):
                    for t, (a, b) in src.items():
                        e = tgt.setdefault(t, [0.0, 0.0])
                        e[0] += a
                        e[1] += b
                wdv = _num(m.get('weaponDamagePct')) / 100.0
                if wdv > wd:
                    wd = wdv
                used = list(used) + ['mod:%s' % (m.get('id') or 'mod')]
        if not fl and not dt:
            continue
        f = fields_with_buff(db, rec)
        # ★ 装备授予的 WPS：权重只存在于离线库 `itemSkills` 表 ⇒ 注入进字段
        #   （`fields_with_buff` 返回的是**新 dict**，改它不会污染 `db.fields` 缓存）
        if rec in _wps_weight:
            f['skillChanceWeight'] = [_wps_weight[rec]]
        rows = []
        # ★ 合成规则（GD 实际机制）：
        #     （技能平伤 ×1） + （100% 武器攻击基础 × 武器伤害%）
        #   其中「100% 武器攻击基础」= 武器 + 光环/被动 + 非武器槽平伤（见 `base100`）。
        #   合成后再做**伤害转化**，最后统一乘 % 加成。
        # ★★ 来源拆解（官方 Step 2）→ 三步转化链（Step 3.1）→ 按最终类型加成（Step 3.2）
        #    为什么不能再用「按类型合并的 dict」：官方规定**每个来源只转一次**，
        #    合并之后结构上无法表达「这一半已被技能转过、那一半还没」——
        #    旧实现会把「已被技能转走的物理」再用护甲穿透转一次（双重转化）。
        #    每个来源还带 `origin`（skill / weapon），报告里的「技能 / 武器」两列由此而来。
        _h = _make_hit(fl, dt, wd, sk_conv, sp_extra,
                       lv, kind=d.get('kind'), rec=rec, f=f, mods=mods,
                       tag=d.get('tag'), name=d.get('name'),
                       group=[r for r in used if r != rec],
                       base_parts=base_parts)
        if _h is None:
            continue
        hits[rec] = _h

    # ④ ★ 输出循环：普攻（默认攻击）+ 武器池 proc + 冷却技能
    #    武器池按 `skillChanceWeight` **权重**分配（**不是**百分比）—— 默认攻击的
    #    权重是 `max(0, 100 - Σ权重)`，详见下方 `w_def` / `denom` 处的官方引文。
    #    不算权重就会把 proc 当成每秒打满（高估数倍）。
    proc = {r: h for r, h in hits.items()
            if h['chance_weight'] > 0 and h['kind'] in ATTACK_KINDS}
    cd_sk = {r: h for r, h in hits.items()
             if r not in proc and h['cooldown'] > 0 and h['kind'] in ATTACK_KINDS}
    swing = {r: h for r, h in hits.items()
             if r not in proc and r not in cd_sk and h['kind'] in ATTACK_KINDS}

    # ★★ 2026-09-20：**没有点任何默认攻击(DAR)技能**时，左键是**普通武器攻击**。
    #    它在游戏里一定存在（100% 武器伤害、无技能平伤），并且和 DAR 一样能触发
    #    武器池技能(WPS)。旧实现在这里推不出默认攻击 ⇒ 下方 `wps_blocked` 把整池
    #    清空 ⇒ 「只靠武器池、不点 DAR」（含 Σ权重 ≥ 100 的形态）被算成几乎零输出。
    #    ⚠ 只在「**有武器池但没有 DAR**」时补 —— 没点任何武器池的纯施法构建、
    #      以及已经点了猛袭/野性利爪的构建**完全不受影响**（零漂移）。
    if not swing and proc:
        _basic = _make_hit({}, {}, 1.0, list(conversions or []), {},
                           1, kind='attack', tag='__basic_weapon_attack__',
                           name='普通攻击')
        if _basic:
            hits[BASIC_REC] = _basic
            swing = {BASIC_REC: _basic}

    # ★★ 2026-09-20：**默认攻击槽的归属不能只按「平均每击最大」猜**。
    #    GD 里一个角色只有**一个**左键默认攻击（default attack），`attack*` 族里
    #    所有「无冷却、无 `skillChanceWeight`」的技能都在**争这一个槽**，谁当默认
    #    攻击是**玩家选择**，不是数值决定的。旧实现 `max(avg)` 会让「猛袭」这类
    #    单纯每击更高的技能**顶掉**形态真正的主输出（实测：以「雪崩」为主输出的
    #    人形态 BD 被猛袭抢走 83% 占比，雪崩 0%）—— 与用户意图直接冲突。
    #    故：形态可在 `archetypes*.json` 里**显式声明** `primary_attack`
    #    （记录名 / 文件名 / 中文 tag 皆可），声明了就按声明走；
    #    **没声明 ⇒ 完全保持旧的 `max(avg)` 行为（零漂移）**。
    _prim = (arch or {}).get('primary_attack')
    default_rec = None
    prim_hit = None
    if _prim:
        for _r in sorted(swing):
            if (swing[_r].get('tag') == _prim or _r == _prim
                    or os.path.basename(_r) == _prim
                    or os.path.basename(_r)[:-4] == _prim):
                default_rec = _r
                prim_hit = _prim
                break
    if default_rec is None:
        default_rec = max(swing, key=lambda r: swing[r]['avg']) if swing else None
    rep['primary_attack'] = _prim
    rep['primary_honored'] = bool(prim_hit)
    # 落选的基础攻击（争同一个左键槽）—— 记进报告，避免「点了却没打」说不清
    rep['rotation_bench'] = sorted(
        ({'skill': r, 'tag': swing[r].get('tag'), 'avg': swing[r]['avg']}
         for r in swing if r != default_rec),
        key=lambda x: -x['avg'])

    # ★★ 2026-09-18 修正：**武器池技能（WPS）只在「默认攻击带武器伤害」时才会触发**。
    #    GD 规则：`skillChanceWeight` 技能（WPS）由**带武器伤害的攻击**触发；
    #    默认攻击若 `weaponDamagePct = 0`（纯法术型攻击，如鸦人形态的「寒冰之爪」
    #    是 `attack_wave` 且无该字段），WPS 一次都不会触发。
    #    实测（Sam lv63 鸦人）：默认攻击 寒冰之爪 weapon_pct=0%，却把夜刃的
    #    wpattack1/2/3（143%/110%/135% 武器伤害）按 22 权重 ×3 算成 proc，
    #    虚增 **5,116 DPS（占 13.8%）**，并且把装备优化方向带偏到「穿刺」上。
    #    对照：狼人形态的「野性利爪」是 `attack_weapon`、weaponDamagePct 70~100% ⇒ WPS 正常触发。
    wps_blocked = False
    if proc:
        _dw = 0.0
        if default_rec and db:
            if default_rec == BASIC_REC:
                # 普通武器攻击 = 100% 武器伤害 ⇒ 永远满足「带武器伤害」这个门槛
                _dw = 100.0
            else:
                _df = fields_with_buff(db, default_rec) or {}
                _dw = _at(_df.get('weaponDamagePct') or [], levels.get(default_rec, 1))
        if _dw <= 0.0:
            wps_blocked = True
            proc = {}

    # W = 武器池总权重；`w_def` = **默认攻击自己的权重**。
    #
    # ★★ 2026-09-20 修正分母（旧实现 `denom = 100 + W` 是错的）
    #   官方（Crate 设计师 Zantai，官网论坛 Grim Misadventure #54）：
    #     "The numbers are actually **weights, not percentages**. Weapon pool
    #      skills first **subtract their weight from the default attack up to
    #      100%**, then it becomes additive so that they start competing with
    #      one another. … Once you pass 100, the default single right or left
    #      attacks won't happen anymore."
    #     例（他给的）：三个各 16 ⇒ 槽位 1-16 / 17-32 / 33-48 是三个技能，
    #                    **49-100 是普攻**，掷 1-100。
    #   ⇒ 正确口径：
    #       W ≤ 100：默认攻击权重 = 100 - W（普攻仍会出现），分母 = 100
    #       W >  100：默认攻击权重 = 0（**普攻彻底不再出现**），分母 = W
    #   另有 stackexchange / allthings.how 两处独立复述同一条规则（含
    #   「超过 100 就按比例缩放」）。
    #   ⚠ 旧口径把默认攻击权重当成恒 100 ⇒ 分母偏大、普攻占比被系统性高估；
    #     这一条**只有在权重真的拿到之后才有意义** —— 旧数据里
    #     `skillChanceWeight` 整库缺失（见 `gd/dbr.py` 第 5 步），
    #     当时的 `100 + W` 只是个「防高估」的兜底。
    W = sum(h['chance_weight'] for h in proc.values())
    w_def = max(0.0, 100.0 - W)
    denom = max(100.0, W)

    # ---------------------------------------------------------------- DPS 汇总
    # ★ 官方 Step 9「DoT 时间轴」：DoT **不能当平伤 × 攻频**。
    #   官方："DoTs stack from different sources and always do full damage"
    #   ⇒ 同一来源（同一个技能）反复施加 = **刷新**，不翻倍 ⇒ 稳态覆盖率上限 100%；
    #     不同技能 = 不同来源 ⇒ **全额叠加**（社区共识，也是游戏内实测行为）。
    #   本函数按「每技能一个通道」实现，通道内 `min(1, 时长×频率)` 打折；
    #   时长缺失按理应按满覆盖，但会**显式登记**到 `rep['dot_missing_dur']` 而不是装作知道。
    #   ⚠ 反例（本模型**不**采用）：若把「武器来源 DoT」跨技能合并成单通道取最高，
    #     高频技能会高估（旧实现的错）；若把武器 DoT 与技能 DoT 当同一来源刷新，
    #     又会低估。这里选与游戏内一致的「按技能分源、全额叠加」。
    try:
        from . import combat as _CB9
    except Exception:
        _CB9 = None
    _dot_rows = []
    _missing = []

    def _skill_dps(h, freq, rec=None):
        """一个技能的**实际 DPS**（官方口径）。

        · **直伤**：每击伤害 × 频率
        · **DoT**：`每秒值 × min(1, 时长 × 频率)`（走 `gd/combat.py::dot_channel_dps`）
        · **命中/暴击**：整段乘一次期望倍率。DoT 的暴击在施放瞬间锁定，
          对期望而言等价于乘同一个倍率（`gd/combat.py::hit_mult`）。
        """
        direct = dot = 0.0
        for r in (h.get('rows') or []):
            a = (r['min'] + r['max']) / 2.0
            if r.get('is_dot'):
                d = r.get('dur') or 0.0
                if d > 0:
                    cov = min(1.0, d * freq)
                    v = (_CB9.dot_channel_dps(a, d, freq) if _CB9 else a * cov)
                else:
                    # 时长缺失（技能表里没写 `offensiveSlow<X>Duration`）⇒ 满覆盖，
                    # 并登记出来让报告能一眼看到哪些通道是「默认常驻」的假设。
                    cov, v = 1.0, a
                    _missing.append({'skill': rec, 'type': r.get('type')})
                dot += v
                _dot_rows.append({
                    'skill': rec, 'type': r.get('type'),
                    'per_sec': round(a, 1), 'dur': round(d, 2), 'freq': round(freq, 3),
                    'coverage': round(cov, 4), 'dps': round(v, 1),
                    'weapon_part': round((float(r.get('w_min') or 0.0)
                                          + float(r.get('w_max') or 0.0)) / 2.0, 1),
                })
            else:
                direct += a
        panel = direct * freq + dot
        return panel, panel * float(hm.get('expected') or 1.0)

    # ★ 两个口径分开记：
    #   `dps`      = **面板口径**（平均伤害 × 频率；含 DoT 覆盖率修正，**不含**命中/暴击）
    #                —— grimtools 面板与游戏角色页都是这个口径，`gt_regress` 靠它对拍
    #   `dps_real` = **实战口径**（再乘命中/暴击期望倍率）
    dps_swing = dps_swing_real = 0.0
    # ★★ 2026-09-21：冷却技能的**施放占位**（用户口径：主动技能收益远低于「卡着 CD 打满」）
    #
    #   旧模型对冷却技能取 `freq = 1 / cooldown`，隐含两个过乐观假设：
    #     ① 每 N 秒**必定**放一次，覆盖率恒 100%；
    #     ② 施放**不占用任何时间** ⇒ 冷却技能是**无摩擦叠加**在平 A 循环之上的。
    #   现实里施放要吃攻击动画时间，这段时间平 A 与 WPS 都出不来 ——
    #   这正是「主动攻击技能收益很低」在模型里的缺口。
    #   这里把「每次施放的占位秒数 × 施放频率」求成总占用率，从平 A 可用时间里扣掉。
    #
    #   `GD_CD_CAST`     每次施放占用的秒数（默认 **0.4**，近战攻击技能的动画量级）
    #   `GD_CD_FRICTION` 折扣强度（默认 **1.0** = 全额扣；
    #                    设 `GD_CD_FRICTION=0` 退回旧口径 ⇒ 逐位零漂移）
    _cast_s = float(os.environ.get('GD_CD_CAST') or 0.4)
    _fric = float(os.environ.get('GD_CD_FRICTION') or 1.0)
    _occ_raw = 0.0
    for _r0, _h0 in (cd_sk or {}).items():
        # ★ 只有**玩家主动施放**的技能才占攻击时间。
        #   星座 proc（`/devotion/`）与装备授予的自动触发技能（`/itemskills`）
        #   是「攻击时自动触发」的 —— 它们与平 A **并行**发生，不抢动作。
        #   实测：不排除的话「刀锋之怒」（星座，1/1.79s）会白白吃掉 22% 的平 A 时间。
        _p0 = (_r0 or '').replace('\\', '/')
        if '/devotion/' in _p0 or '/itemskills' in _p0:
            continue
        _cd0 = float(_h0.get('cooldown') or 0.0)
        if _cd0 > 0:
            _occ_raw += min(1.0, 1.0 / _cd0) * _cast_s
    occ = min(1.0, _occ_raw * _fric)
    swing_scale = 1.0 - occ
    if default_rec:
        p_def = w_def / denom
        _p, _r = _skill_dps(hits[default_rec], aps * swing_scale * p_def, default_rec)
        hits[default_rec]['chance'] = round(p_def, 4)
        hits[default_rec]['freq'] = round(aps * swing_scale * p_def, 2)
        hits[default_rec]['dps'] = round(_p, 1)
        hits[default_rec]['dps_real'] = round(_r, 1)
        dps_swing += _p
        dps_swing_real += _r
    for r, h in proc.items():
        p = h['chance_weight'] / denom
        _p, _r = _skill_dps(h, aps * swing_scale * p, r)
        h['chance'] = round(p, 4)
        h['freq'] = round(aps * swing_scale * p, 2)
        h['dps'] = round(_p, 1)
        h['dps_real'] = round(_r, 1)
        dps_swing += _p
        dps_swing_real += _r
    dps_cd = dps_cd_real = 0.0
    for r, h in cd_sk.items():
        _p, _r = _skill_dps(h, 1.0 / h['cooldown'], r)
        h['chance'] = 1.0
        h['freq'] = round(1.0 / h['cooldown'], 2)
        h['dps'] = round(_p, 1)
        h['dps_real'] = round(_r, 1)
        dps_cd += _p
        dps_cd_real += _r
    # 其余（被动 / 增益）不产生直接 DPS，但要给出它们的加成值
    for r, h in hits.items():
        if 'dps' not in h:
            h['chance'] = None
            h['freq'] = 0.0
            h['dps'] = 0.0
    for rec, h in hits.items():
        d = skills.get(rec) or {}
        if d.get('kind') in BUFF_DURATION_KINDS + BUFF_TOGGLE_KINDS:
            lv, f = h['level'], (db.fields(rec) or {})
            cd = _at(f.get('skillCooldownTime') or [], lv)
            dur = _at(f.get('skillActiveDuration') or [], lv)
            cov = min(1.0, dur / cd) if cd > 0 else 1.0
            rep['buff'][rec] = {'level': lv, 'kind': d.get('kind'),
                                'coverage': round(cov, 2), 'value': h['avg']}
    rep['skills'] = hits
    rep['rotation'] = {'default': default_rec, 'procs': list(proc),
                       'cooldowns': list(cd_sk),
                       # ★ 武器池口径（官方权重制）—— 报告要能一眼看出「普攻会不会出现」
                       'weight_total': round(W, 1), 'default_weight': round(w_def, 1),
                       'denom': round(denom, 1),
                       'wps_blocked': wps_blocked,
                       # ★ 被**形态门控**剔掉的装备授予 WPS（变形后默认攻击被替换）
                       'wps_form_dropped': sorted(_wps_dropped),
                       # ★ 被**武器类型门控**剔掉的技能/星位（`{rec, why}`）
                       'weapon_dropped': list(_weapon_dropped),
                       # ★ 冷却技能的**施放占位**：平 A 时间被吃掉的份额（1 − 这个 = 平 A 可用）
                       'cd_cast_occupancy': round(occ, 4),
                       'swing_scale': round(swing_scale, 4),
                       # ★ 默认攻击槽的归属（`primary_attack` 声明是否被兑现）+ 落选者
                       'primary_attack': _prim, 'primary_honored': bool(prim_hit),
                       'bench': rep.get('rotation_bench') or [],
                       'dps_swing': round(dps_swing, 1), 'dps_cooldown': round(dps_cd, 1),
                       'dps_swing_real': round(dps_swing_real, 1),
                       'dps_cooldown_real': round(dps_cd_real, 1)}
    rep['dps'] = round(dps_swing + dps_cd, 1)
    rep['dps_real'] = round(dps_swing_real + dps_cd_real, 1)

    # ★ 官方 Step 9 的可审计输出：逐 DoT 通道（技能 = 来源）的每秒值 / 时长 / 频率 /
    #   覆盖率 / 折算后 DPS。`dot_dps` 是它们在面板 DPS 里占的份额。
    _dot_rows.sort(key=lambda x: -x['dps'])
    _dot_tot = sum(x['dps'] for x in _dot_rows)
    rep['dot'] = {
        'channels': _dot_rows,
        'dot_dps': round(_dot_tot, 1),
        'share': round(_dot_tot / (dps_swing + dps_cd), 4) if (dps_swing + dps_cd) else 0.0,
        'missing_dur': sorted({(m.get('skill'), m.get('type')) for m in _missing},
                              key=lambda x: (str(x[0]), str(x[1]))),
    }

    # ★ 官方 Step 4/5 的口径与中间量：面板 OA/DA、PTH、命中/暴击期望、敌方档。
    #   写进 rep 便于报告与对拍（`tools/gt_regress.py --sheet` 要读这几个）。
    rep['oa'] = round(oa, 1)
    rep['da'] = round(da_self, 1)
    rep['crit_dmg_pct'] = round(crit_dmg, 1)
    rep['pth'] = (round(pth_v, 2) if pth_v is not None else None)
    # ★ 降敌 DA 的透明化：原始敌方 DA ｜ 扣减明细 ｜ 生效值（见 `enemy_da_cut`）
    rep['enemy_da_cut'] = round(_da_cut, 1)
    rep['enemy_da_effective'] = (round(_eda_eff, 1) if _eda_eff is not None else None)
    rep['enemy_da_cut_rows'] = [
        {'rec': r[0], 'value': round(r[1], 1), 'field': r[2],
         'coverage': round(r[3], 3)} for r in (_da_cut_rows or ())]
    rep['hit'] = {'expected': round(float(hm['expected']), 4),
                  'hit_chance': (round(hm['hit_chance'], 4)
                                 if hm.get('hit_chance') is not None else None),
                  'crit_chance': (round(hm['crit_chance'], 4)
                                  if hm.get('crit_chance') is not None else None),
                  'miss_chance': (round(hm['miss_chance'], 4)
                                  if hm.get('miss_chance') is not None else None),
                  'discount': round(float(hm.get('discount') or 1.0), 4),
                  'enemy_da': (round(float(_eda), 1) if _eda else None)}
    if _ep:
        rep['enemy'] = {'id': _ep.get('id'), 'kind': _ep.get('kind'),
                        'classification': _ep.get('classification'),
                        'level': _ep.get('level'), 'da': _eda,
                        'armor': _ep.get('armor'), 'source': _ep.get('source')}

    # ★ 官方 Step 5 **护甲减免**（只对物理直伤；先护甲、后抗性）。
    #   ⚠ 离线库**不含怪物护甲值**（见 `gd/enemy.py` 的说明），所以只有调用方
    #     显式给出 `enemy_armor` 时才真的改写伤害；否则如实记录「数值缺失」，
    #     绝不用猜测值静默改写伤害。
    #
    #   ★★ 2026-09-20 改版：不再是「一个 bool」，而是**按输出循环的主要类型逐条裁决**
    #     （用户口径：怪物元素护甲值不必去追，但循环里每个主要输出类型**对应的
    #      护甲/破甲有没有被考虑**必须看得见）。四条关键事实都进 `by_type`：
    #       ① 护甲只吃 `physical` 直伤 —— 穿刺/创伤/元素/流血全都不过这一关
    #          （判据单点在 `gd/combat.py::armor_applies`，别处不许手写比较）；
    #       ② 吸收率用**怪物侧 56%**（推导），不是引擎基准 70% —— 差 20% 穿透量；
    #       ③ 破甲值从 `armor_reduce` 参数/字段收（**玩家侧数据为空**，见
    #          `gd/combat.py::armor_reduce_constants`），先减甲再算减免；
    #       ④ 每类型给出该类型的 `armor_pierce`（武器「%护甲穿透」= 物理→穿刺）。
    from . import rr as _RR
    _ac = _CB.armor_constants()
    # ⚠ 别裸 `int()`：敌方档里的 `difficulty` 可能是**字符串**（五档档位是 `"n/a"`，
    #   见 `gd/enemy.py:518`），裸 cast 会在 `plan_audit --enemy elite/boss/...` 时
    #   抛 `ValueError: invalid literal for int()` 整条链路崩掉。
    #   这里改成**容错 cast**：能转就转，转不动就原样透传给 `adj_index`
    #   （`enemy.adj_index` 本就接受字符串难度并自己映射，映射不到就退回 3）。
    def _as_int(v, dflt):
        try:
            return int(v)
        except (TypeError, ValueError):
            return v or dflt
    _dif = _as_int((_ep or {}).get('difficulty'), 3)
    _ply = _as_int((_ep or {}).get('players'), 1)
    _absorb, _absorb_why = _CB.monster_absorption(_dif, _ply)
    _red = max(0.0, float(armor_reduce or 0.0))
    _arm = float(enemy_armor) if enemy_armor else 0.0
    _arm_eff = _CB.armor_after_reduce(_arm, _red)

    _share = _RR.dps_by_type(hits)
    _share_tot = sum(_share.values()) or 1.0
    # 逐 **行**（每击）算减免再按贡献加权 —— 减免对打击大小非线性，
    # 拿聚合 DPS 当「这一击」是错的（旧实现还每行覆盖一次，只剩最后一行）。
    _agg = {}
    for _h in hits.values():
        for _r in (_h.get('rows') or []):
            _t = _r.get('type')
            if not _t:
                continue
            _e = _agg.setdefault(_t, {'w': 0.0, 'mit': 0.0, 'w_mit': 0.0, 'rows': 0})
            _w = _RR.row_w(_h, _r)
            _e['rows'] += 1
            _e['w'] += _w
            if _w <= 0:
                continue
            if not _CB.armor_applies(_t, _r.get('is_dot')):
                continue
            _e['w_mit'] += _w
            if _arm_eff > 0:
                _mid = (_r['min'] + _r['max']) / 2.0
                _e['mit'] += _CB.armor_expect(
                    _mid, _arm_eff, _absorb)['reduction_pct'] * _w

    _by_type, _covered = {}, []
    # ★ 只列**真正进 DPS 的类型**（`dps_by_type` 的口径）——
    #   这才叫「输出循环的主要输出属性」。有行但权重为 0 的类型不算。
    for _t in sorted(_share, key=lambda x: -_share.get(x, 0.0)):
        _e = _agg.get(_t)
        if not _e or _e['w'] <= 0:
            continue
        _hit = _e['w_mit'] > 0
        _mp = (round(_e['mit'] / _e['w_mit'], 2) if (_hit and _arm_eff > 0) else None)
        # 护甲穿透是**武器属性**，只跟「物理 / 穿刺」这一对有关系：
        #   `offensivePierceRatio` 把**残余物理**转成穿刺；穿刺转出后由穿刺抗性管。
        _ap = round(ratio_used * 100.0, 1) if _t in ('physical', 'pierce') else 0.0
        if _hit:
            _v = '过护甲：吸收 %.0f%%%s' % (
                _absorb, ('，减伤 %.2f%%' % _mp) if _mp is not None else '（护甲值缺失）')
            if ratio_used > 0:
                _v += '；武器护甲穿透 %.0f%%（残余物理来自无武器伤害%%的技能）' % (
                    ratio_used * 100.0)
        else:
            _v = '不过护甲（本作护甲只吃物理直伤）'
        _by_type[_t] = {
            'type': _t,
            'zh': TYPE_ZH.get(_t, _t),
            'armor_affected': bool(_hit),
            'share': round(_share.get(_t, 0.0) / _share_tot, 4),
            'mitigation_pct': _mp,
            'armor_pierce_pct': _ap,
            'res_bucket': _RR.TYPE_BUCKET.get(_t, _t),
            'verdict': _v,
        }
        _covered.append(_t)

    _applied = bool(_arm_eff > 0 and any(v['armor_affected'] for v in _by_type.values()))
    _a_red = _CB.armor_reduce_constants()
    rep['armor'] = {
        'applied': _applied,
        'armor_source': (enemy_armor_src if _arm > 0 else None),
        'reason': (None if _applied else
                   ('该敌方档没有单一护甲值（等级池是分位数聚合 / 五档手填无护甲）'
                    '⇒ 该行留空、物理直伤为下界；换 `--enemy-profile m<id>` 可读到真值'
                    if _arm_eff <= 0
                    else '循环里没有物理直伤 ⇒ 护甲本就不参与')),
        # 吸收：**怪物侧**（推导）优先，引擎基准另存参照
        'absorption': round(_absorb, 2),
        'absorption_basis': _absorb_why,
        'absorption_player': round(_CB.absorb_base(), 2),
        'absorb_modifier': _CB.monster_absorb_modifier(_dif, _ply),
        # 破甲
        'reduce': {'value': round(_red, 2),
                   'armor_before': round(_arm, 2), 'armor_after': round(_arm_eff, 2),
                   'field': _a_red['fields'][0], 'tag': _a_red['tag'], 'zh': _a_red['zh'],
                   # ★ 出现次数**实测**（`gd/rr.py::armor_reduce_census`），不写死 ——
                   #   写死的结论会随版本/Mod 静默过期。
                   'census': _RR.armor_reduce_census(),
                   'player_side_total': _RR.armor_reduce_player_side(),
                   'note': _a_red['note']},
        'regions': {k: round(v, 1) for k, v in _ac['regions'].items()},
        # ★ 按输出循环主要类型逐条裁决（键 = 循环里**实际出现**的类型）
        'by_type': _by_type,
        'covered_types': _covered,
        'line': ' · '.join('%s %s' % (v['zh'], v['verdict']) for v in _by_type.values()),
    }

    # ★★ **「100% 武器攻击」**：GT 角色面板的 `Weapon Damage` 行就是这个口径 ——
    #    只含「武器基础（直接 + DoT） + 全局平伤 + 光环/被动平伤」（= `base100`），
    #    经转化与加成后的结果，**不含技能的固定值与武器伤害%**。
    conv_w = {}
    for _t, (_v) in (base100 or {}).items():
        _e = conv_w.setdefault(_t, [0.0, 0.0])
        _e[0] += _v[0]
        _e[1] += _v[1]
    apply_conversions(conv_w, conversions or [])
    wa_rows = []
    for _t, (_lo, _hi) in conv_w.items():
        if _lo <= 0 and _hi <= 0:
            continue
        _m = (1.0 + pct.get(_t, 0.0) / 100.0) * mult
        wa_rows.append({'type': _t, 'pct': round(pct.get(_t, 0.0), 1),
                        'base': [round(_lo, 1), round(_hi, 1)],
                        'min': round(_lo * _m, 1), 'max': round(_hi * _m, 1)})
    wa_rows.sort(key=lambda x: -x['max'])
    rep['weapon_attack'] = {
        'rows': wa_rows,
        'min': round(sum(r['min'] for r in wa_rows), 1),
        'max': round(sum(r['max'] for r in wa_rows), 1)}

    # ★★ **实战伤害**（敌方减抗乘区，见 `gd/rr.py`）—— 面板口径 `rep['dps']` **不动**。
    #    为什么分开：`rep['dps']` 要能与 grimtools 面板逐项对拍（那也是不含敌方抗性的），
    #    而「实战」这一行才是玩家真正打出来的数。两者差一个 `(1−res_eff)/(1−res)`。
    #    传 `rr`（减抗包）或 `enemy_res`（档位名 / 数字）任一即可触发。
    if rr is not None or enemy_res is not None or _ep is not None:
        try:
            from . import rr as _RR
            # ★ 传 `enemy`（真值怪/等级池）时用它的**逐桶抗性**，五档只在缺 enemy 时兜底。
            _RR.apply_vs(rep, rr or _RR.empty(), profile=enemy_res, enemy=_ep)
        except Exception as _e:                       # 不允许它影响面板数值
            rep['vs_error'] = '%s: %s' % (type(_e).__name__, _e)

    # ★★ 逐技能 × 逐伤害类型的**实战分解**（用户口径 2026-09-20）——
    #    放在最后：要吃 `rep['vs']['rows']`（逐桶减抗后抗性与倍数）与
    #    `rep['armor']`（护甲值/吸收率）。**只加键，不改任何既有数值**。
    try:
        skill_type_breakdown(rep)
    except Exception as _e:                           # 不允许它影响既有口径
        rep['type_rows_error'] = '%s: %s' % (type(_e).__name__, _e)
    return rep




def weapon_base(gear_fields):
    """从主手武器的字段估算基础伤害均值"""
    lo = hi = 0.0
    for k, v in gear_fields.items():
        if not v or not isinstance(v, list) or not isinstance(v[0], (int, float)):
            continue
        if k.startswith('offensiveBase') or k.startswith('offensivePhysical'):
            if k.endswith('Min'):
                lo += float(v[0])
            elif k.endswith('Max'):
                hi += float(v[0])
    if lo and hi:
        return (lo + hi) / 2.0
    return 0.0


def dmg_bonus(gear_items, types, db=None):
    """装备对指定伤害类型的百分比加成合计。

    gear_items: [(gt_id, fields_dict_or_None), ...]
    """
    mods = {DMG_TO_MOD[t] for t in types if t in DMG_TO_MOD}
    mods.add('offensiveTotalDamageModifier')            # 总伤害加成对所有类型生效
    total = 0.0
    for _gid, f in gear_items:
        if not f:
            continue
        # ★ 定序：set 迭代序随 PYTHONHASHSEED 变，不可复现
        for k in sorted(mods):
            v = f.get(k)
            if isinstance(v, (int, float)):
                total += float(v)
    return total


def analyze(archetype, levels=None, gear_items=None, db=None, verbose=False,
            only_listed=False):
    """输出循环分析。

    archetype    : 流派键（werewolf / wereraven / human / soldier_nightblade …）
    levels       : {技能记录名: 等级}（含装备加成后的最终等级）
    gear_items   : [(gt_id, dbr_fields)]，用于算装备加成
    only_listed  : ★ **只算 `levels` 里明确列出的技能**（缺 = 未加点 = 跳过）。

                   默认 False 时，未列出的技能会取 `max_level` 当作"合理默认"，
                   便于三形态横向对比；但**算具体方案的 DPS 时必须开 True** ——
                   否则会混进一堆根本没加点的技能，把 DPS 榜算歪
                   （实测 60 级狂战士+夜刃方案：`暗影袭改造` 未加点却以 12 级
                   排在 DPS 榜前列）。
    """
    arch = (_load('archetypes.json', {}) or {}).get(archetype)
    skills = _load('skills.json', {}) or {}
    if not arch:
        return {'error': '未知形态 %s' % archetype}
    levels = levels or {}
    gear_items = gear_items or []

    # 形态核心技能若没给等级，用「基础等级 = max_level」的合理默认（便于横向对比）
    core = arch.get('core_skills') or []
    rep = {'archetype': archetype, 'label': arch.get('label'), 'skills': {}, 'buff': {}, 'dps': 0.0}

    wbase = 0.0
    for _gid, f in gear_items:
        if f:
            w = weapon_base(f)
            wbase = max(wbase, w)

    total_dps = 0.0
    for rec in core:
        d = skills.get(rec) or {}
        kind = d.get('kind')
        lv = levels.get(rec)
        if lv is None:
            if only_listed:
                continue                      # 未列出 = 未加点 → 不计入
            lv = min(d.get('max_level') or 1, 16)
        if lv <= 0:
            continue                          # 显式 0 级同样不计入
        if not db:
            continue
        f = db.fields(rec)
        if not f:
            continue
        flat, dot, types = parse_damage(f, lv)
        if not flat and not dot:
            continue
        fixed = sum(flat.values()) + sum(dot.values())
        wd = _at(f.get('weaponDamagePct') or [], lv) / 100.0
        wpn = wd * wbase
        bonus = dmg_bonus(gear_items, types or {'physical'}) / 100.0
        one_hit = (fixed + wpn) * (1.0 + bonus)

        cd = _at(f.get('skillCooldownTime') or [], lv)
        dur = _at(f.get('skillActiveDuration') or [], lv)
        freq = (1.0 / cd) if cd > 0 else 1.0

        if kind in ATTACK_KINDS:
            dps = one_hit * freq
            total_dps += dps
            rep['skills'][rec] = {'kind': kind, 'level': lv, 'fixed': round(fixed, 1),
                                  'weapon_part': round(wpn, 1), 'bonus_pct': round(bonus * 100, 1),
                                  'one_hit': round(one_hit, 1), 'dps': round(dps, 1),
                                  'types': sorted(types)}
        elif kind in BUFF_DURATION_KINDS:
            cov = min(1.0, (dur / cd)) if cd > 0 else 1.0
            val = (sum(flat.values()) + sum(dot.values()))
            rep['buff'][rec] = {'kind': kind, 'coverage': round(cov, 3),
                                'value': round(val, 1), 'weighted': round(val * cov, 1)}
            total_dps += val * cov * 0.05            # buff 的等效 DPS 贡献（保守系数）
        elif kind in BUFF_TOGGLE_KINDS:
            val = (sum(flat.values()) + sum(dot.values()))
            rep['buff'][rec] = {'kind': kind, 'coverage': 1.0,
                                'value': round(val, 1), 'weighted': round(val, 1)}
            total_dps += val * 0.05

    rep['weapon_base'] = round(wbase, 1)
    rep['dps'] = round(total_dps, 1)
    return rep


if __name__ == '__main__':
    from . import dbr as DB
    db = DB.open_all()
    arch_name = sys.argv[1] if len(sys.argv) > 1 else 'werewolf'
    # 演示：不带装备加成，纯技能面板，便于三形态横向看
    for a in ('human', 'werewolf', 'wereraven'):
        if arch_name != 'all' and a != arch_name:
            continue
        rep = analyze(a, db=db)
        print('=== %s（%s） 面板 DPS 代理 %.1f' % (a, rep['label'], rep['dps']))
        for rec, d in sorted(rep['skills'].items(), key=lambda x: -x[1]['dps'])[:5]:
            print('    %-30s Lv%-3d 固定%-8.1f 武器%-8.1f 单次%-9.1f DPS %-8.1f %s'
                  % (rec.split('/')[-1][:30], d['level'], d['fixed'], d['weapon_part'],
                     d['one_hit'], d['dps'], ','.join(d['types'])))
        for rec, d in list(rep['buff'].items())[:3]:
            print('    [buff] %-26s 覆盖 %.2f 权值 %.1f' % (rec.split('/')[-1][:26],
                                                          d['coverage'], d['weighted']))
