# -*- coding: utf-8 -*-
r"""敌方抗性削减（Resistance Reduction, 下称 RR）—— 收集 / 合计 / 换算实战伤害。

## 为什么需要它

`gd/dps.py` 的伤害模型**不含敌方抗性**（面板口径，与 grimtools 对拍用）。
但实战里 RR 是 Grim Dawn **最大的伤害乘区**：对 80% 抗性的 Boss，
−57% 穿刺抗性 = **3.85 倍**伤害。旧模型对此完全无感 ⇒ 优化器会拿一件
`−30% 穿刺抗性` 的东西去换「看起来更值钱」的线性 % 伤害。本模块补上这一维。

## 三条机制（口径来自离线库的官方显示文本 + 官方论坛实测，不是猜的）

三族**按减抗文案的措辞**区分（不是按数值大小），合计规则完全不同。
社区用 **B / C / A** 命名，与字段的对应关系如下（详见 `docs/rr_mechanics.md`）：

| 族 | 字段 | 位置 | 游戏显示 | 合计口径 | 本模块键名 |
|---|---|---|---|---|---|
| **B** | `defensive<类型>` **负值**（逐级数组） | 技能 / 星座 / 物品技能 | `−X% 穿刺抗性` | **叠加** | `add` |
| **C** | `offensive{Total,Elemental,Physical}ResistanceReductionPercent` | 物品 / 技能 | `X% 目标抗性降低`（带 %） | **只取最高**（乘算） | `max` |
| **A** | `offensive{Total,Elemental,Physical}ResistanceReductionAbsolute` | 物品 / 技能 | `X 目标抗性降低`（无 %） | **只取最高**（最后减） | `flat` |

证据链：`data/` 那 16563 条本地化文本里
`DamageTotalResistanceReductionPercent` = 「% 目标抗性降低」、
`DamageTotalResistanceReductionAbsolute` = 「目标抗性降低」（无 %）、
`DefensePierce` = `{%.0f0}% 穿刺抗性` —— 字段族与显示文本一一对应。

★ 注意字段名在**物品库（itemdb.js）**里统一带 `Min` 后缀
（`…AbsoluteMin`），技能表里同样带 `Min` 后缀但值是**逐级数组**。
另有 `…DurationMin`（debuff 持续秒数）与 `…Chance`（触发概率）后缀，
本模块解析时剥掉，按「debuff 常驻」计（打久了就是常驻，不折算覆盖率）。

## 换算公式（结算顺序 **B → C → A**）

```
r = base_res − Σ(B 族)                # 叠加类，直接相减
r = r × (1 − c/100)   (r ≥ 0)         # C 族：只取最高，乘算
r = r × (1 + c/100)   (r < 0)         #       ★ 负抗性时**加深**（不是拉回 0）
r = r − max(A 族绝对值)                # A 族：只取最高，最后减
r = max(r, RR_FLOOR)                  # 下限，默认 −100%（GD_RR_FLOOR 可放开）
mult = (100 − r) / (100 − base_res)   # 相对「不减抗」的伤害倍数
```

★ **敌抗可以减成负数，负值 1:1 放大伤害**（社区实测 −118% ⇒ 2.18×），
**不锁 0**。下限 −100% 是**保守**取值（官方无引擎级硬下限的权威证据），
极端减抗流会被**低估**。

⚠ 2026-09-20 修正三处旧偏差（旧版）：① A 族被并进 B 族**求和**（应独立取最高）；
② C 族对**负抗性**无条件用 `×(1−c/100)`，把负数**拉回 0**（应 `×(1+c/100)` 加深）；
③ 结算顺序写成 `(B+A) → C`（应 `B → C → A`）。
全库仅**装备侧**有 A/C 族（`itemdb.js`：C 90 处 / A 189 处，技能表 0 处）
⇒ Sam 的减抗**全是 B 族**，历史数字不受影响。

## 敌方抗性档位（profile）

用户口径：「最高有 100%，**不是硬指标，逼近即可，性价比合适就行**」。
所以这里是**多档并列展示**，优化器只用其中一档（`GD_ENEMY_RES_PROFILE`，默认 elite）。
`max=100%` 时基准伤害为 0（免疫），倍数分母钳到 1，仅用于展示「减抗能把免疫怪打到多少」。
"""

from __future__ import annotations

import os
import re
from collections import defaultdict

# ---------------------------------------------------------------- 抗性桶
# 10 个**抗性桶**（敌人抗性的粒度）。DoT 与直接伤害共用同一个桶
# （燃烧吃火抗、霜燃吃冰抗、内部创伤吃物理抗）。
RES_BUCKETS = ('physical', 'pierce', 'fire', 'cold', 'lightning',
               'poison', 'bleeding', 'vitality', 'aether', 'chaos')

BUCKET_ZH = {
    'physical': '物理', 'pierce': '穿刺', 'fire': '火焰', 'cold': '冰冷',
    'lightning': '闪电', 'poison': '毒素/酸液', 'bleeding': '流血',
    'vitality': '活力', 'aether': '虚化', 'chaos': '混乱',
}

# 伤害类型（gd/rotation.TYPE_ZH 的键）→ 抗性桶
TYPE_BUCKET = {
    'physical': 'physical', 'pierce': 'pierce',
    'fire': 'fire', 'cold': 'cold', 'lightning': 'lightning',
    'poison': 'poison', 'acid': 'poison',
    'vitality': 'vitality', 'aether': 'aether', 'chaos': 'chaos',
    'bleeding': 'bleeding',
    # DoT 共用直接伤害的桶
    'burn': 'fire', 'frostburn': 'cold', 'electrocute': 'lightning',
    'decay': 'vitality', 'trauma': 'physical', 'poisondot': 'poison',
}

# ---------------------------------------------------------------- 字段表
_ALL = RES_BUCKETS
_ELEM = ('fire', 'cold', 'lightning')

# 字段（**已剥后缀**）→ (作用桶, 族)；族 'add' = B 叠加 / 'max' = C 取最强 /
# 'flat' = A 取最强（绝对值，最后减）。见模块顶部三族表与 docs/rr_mechanics.md。
FIELD_RR = {
    # C 族：带 % 的「目标抗性降低」
    'offensiveTotalResistanceReductionPercent': (_ALL, 'max'),
    'offensiveElementalResistanceReductionPercent': (_ELEM, 'max'),
    'offensivePhysicalResistanceReductionPercent': (('physical',), 'max'),
    # A 族：不带 % 的「目标抗性降低」（绝对值）
    'offensiveTotalResistanceReductionAbsolute': (_ALL, 'flat'),
    'offensiveElementalResistanceReductionAbsolute': (_ELEM, 'flat'),
    'offensivePhysicalResistanceReductionAbsolute': (('physical',), 'flat'),
}

# A 族：技能上的**负值** `defensive<类型>`。只有伤害抗性进制，其余（眩晕/恐惧/
# 冰冻/击退/石化/反射/陷阱/睡眠/迷惑/护甲修正/元素抗性修正）一律**排除** ——
# 实测全库有 17 个 defensive* 字段会出现负值，其中 8 个与伤害无关。
DEF_BUCKET = {
    'defensivePhysical': 'physical', 'defensivePierce': 'pierce',
    'defensiveFire': 'fire', 'defensiveCold': 'cold',
    'defensiveLightning': 'lightning', 'defensivePoison': 'poison',
    'defensiveBleeding': 'bleeding',
    'defensiveLife': 'vitality',          # ★ GD 里活力叫 Life 不叫 Vitality
    'defensiveAether': 'aether', 'defensiveChaos': 'chaos',
    # 泛化族（负值同样是「对敌减抗」）
    'defensiveElementalResistance': _ELEM,
    'defensiveAllResistance': _ALL,
}

_SUFFIXES = ('DurationMin', 'DurationMax', 'Duration', 'Chance', 'Min', 'Max')


def strip_suffix(k):
    """`offensiveTotalResistanceReductionAbsoluteDurationMin` → `…Absolute`"""
    for s in _SUFFIXES:
        if k.endswith(s):
            return k[: -len(s)]
    return k


def _num(v):
    """标量 / 单元素数组 → float；其余 → 0.0"""
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, list) and v and isinstance(v[0], (int, float)):
        return float(v[0])
    return 0.0


def _at(vals, level):
    """按技能等级取逐级数组（1 起，超界取末位）"""
    if not vals:
        return 0.0
    if isinstance(vals, (int, float)):
        return float(vals)
    if not isinstance(vals, list) or not vals:
        return 0.0
    i = max(0, min(int(level) - 1, len(vals) - 1))
    v = vals[i]
    return float(v) if isinstance(v, (int, float)) else 0.0


# ---------------------------------------------------------------- 单来源收集
def rr_of(fields, level=None):
    """从一个「字段字典」收集 RR。

    三种输入都吃（字段名同构，只在取值方式上不同）：
      · `itemdb.js` 物品条目（值 = 标量或单元素数组）            → level=None
      · `db.fields(rec)` 技能记录（值 = **逐级数组**）            → 传 level
      · `folded[槽位]`（`fold_plan` 折叠后的求和值）              → level=None

    返回 `{'add': {桶: 值}, 'max': {桶: 值}, 'flat': {桶: 值}}`
    （值恒为**正数**，表示「降低多少」；三键 = B 叠加 / C 取最强 / A 取最强）。
    """
    add, mx, fl = defaultdict(float), defaultdict(float), defaultdict(float)
    for k, v in (fields or {}).items():
        if not isinstance(k, str):
            continue
        if k in DEF_BUCKET:                       # B 族：技能上的负值 defensive*
            val = _at(v, level) if level is not None else _num(v)
            if not val or val >= 0:
                continue
            bk = DEF_BUCKET[k]
            if bk is _ALL:
                bk = RES_BUCKETS
            elif not isinstance(bk, tuple):     # ★ 单个桶也要包成元组：
                bk = (bk,)                     #   `for b in 'pierce'` 会遍历**字符**
            for b in bk:
                add[b] += -val
            continue
        base = strip_suffix(k)
        hit = FIELD_RR.get(base)
        if not hit:
            continue
        val = _at(v, level) if level is not None else _num(v)
        if not val:
            continue
        buckets, fam = hit
        tgt = add if fam == 'add' else (fl if fam == 'flat' else mx)
        for b in buckets:
            if fam == 'add':
                tgt[b] += val                     # B 族：叠加
            else:
                tgt[b] = max(tgt[b], val)         # C / A 族：★ 取最强，**不叠加**
    return {'add': dict(add), 'max': dict(mx), 'flat': dict(fl)}


def combine(*parts):
    """合并多个来源：B 族 `add` 累加、C 族 `max` 与 A 族 `flat` 各取最大。"""
    add, mx, fl = defaultdict(float), defaultdict(float), defaultdict(float)
    for p in parts:
        if not p:
            continue
        for b, v in (p.get('add') or {}).items():
            add[b] += v
        for b, v in (p.get('max') or {}).items():
            mx[b] = max(mx[b], v)
        for b, v in (p.get('flat') or {}).items():
            fl[b] = max(fl[b], v)
    return {'add': dict(add), 'max': dict(mx), 'flat': dict(fl)}


# ---------------------------------------------------------------- 破甲（护甲降低）
#
# ★★ 与「减抗」并列的第二个敌方乘区，但**只在有护甲值时才起作用** ——
#    因为护甲只吃物理直伤，破甲的价值 = 物理伤害 × 护甲减伤的边际。
#
# ★ 机制真相（别照着直觉写代码）：
#   · 本作**没有「元素护甲」**。元素的减伤通道是抗性（上面那 10 个桶）。
#   · 玩家的「破甲」不是「降低目标护甲」，而是 **`offensivePierceRatio`
#     （武器上的「%护甲穿透」）**：把**残余物理转成穿刺**，穿刺**绕过护甲**。
#     它在 `gd/dmg.py::_apply_pierce` 里，是转化链的**最后一步**。
#   · `DamageDurationDefensiveReduction`（「点目标护甲降低」）是**另一回事**，
#     且在玩家侧**根本不存在**（实测见下面的普查）。
#
# ⚠ 所以本函数在**当前数据下恒为 0** —— 它不是死代码，是**挂点**：
#   哪天版本更新 / 装 Mod 让装备带上这个字段，模型不改一行就能吃到。

_ARC = None
_ARC_FILES = (('itemdb.js', '物品库'), ('skills_json.js', '技能表'),
              ('monsterdb.js', '怪物表'))


def armor_reduce_census(reload=False):
    """破甲字段的全库普查（**实测**，不写死计数）。

    返回 `{文件名: {'zh':…, 'count': n 或 None}}`；`None` = 该 js 不在缓存里。
    为什么必须实测：写死计数会随版本/Mod 静默过期 —— 本项目已因此踩过一次坑
    （「离线库无怪物护甲值」这条结论一度被当成永久事实）。
    """
    global _ARC
    if _ARC is None or reload:
        from .paths import CACHE_DIR       # 复用路径单一真源
        key = 'DefensiveReduction'
        out = {}
        for fn, zh in _ARC_FILES:
            p = os.path.join(str(CACHE_DIR), fn)
            n = None
            if os.path.exists(p):
                try:
                    with open(p, encoding='utf-8', errors='replace') as fh:
                        n = fh.read().count(key)
                except Exception:
                    n = None
            out[fn] = {'zh': zh, 'count': n}
        _ARC = out
    return _ARC


def armor_reduce_player_side():
    """玩家侧（物品库 + 技能表）破甲字段的实测出现次数合计。

    实测 = 0 ⇒ 玩家**没有**破甲机制（与「护甲只吃物理」互为交叉验证）。
    """
    c = armor_reduce_census()
    vals = [c[k]['count'] for k in ('itemdb.js', 'skills_json.js') if c[k]['count'] is not None]
    return sum(vals) if vals else None


def armor_reduce_of(fields, level=None):
    """从字段字典收集破甲值（「点目标护甲降低」，**绝对值**）。

    与 `rr_of` 同构：吃 itemdb 物品条目 / `db.fields` 技能记录（值 = 逐级数组）/
    `fold_plan` 折叠值三种输入。**没有该字段时返回 0.0**（当前全库皆如此）。
    """
    from . import combat as _CB          # 局部 import：combat 不依赖 rr，无环
    want = set(_CB.armor_reduce_constants()['fields'])
    tot = 0.0
    for k, v in (fields or {}).items():
        if not isinstance(k, str) or strip_suffix(k) not in want:
            continue
        val = _at(v, level) if level is not None else _num(v)
        if val:
            tot += abs(float(val))      # 负值也当「降低」（与 A 族减抗同一个约定）
    return tot


def armor_reduce_of_folded(folded, with_skills=True, skills=None, db=None):
    """角色侧统一入口：装备折叠字段（+ 可选技能表）→ 破甲合计。"""
    tot = 0.0
    for _slot, f in (folded or {}).items():
        tot += armor_reduce_of(f)
    if with_skills and skills and db is not None:
        for rec, lv in (skills or {}).items():
            try:
                f = db.fields(rec) if hasattr(db, 'fields') else None
            except Exception:
                f = None
            if f:
                tot += armor_reduce_of(f, level=lv)
    return tot


_SKILL_MEMO = {}


def rr_of_skills(levels, db, skills=None):
    """从「技能记录 → 等级」收集 A 族减抗（含壳技能的 `_buff`）。

    ★ 必须走 `fields_with_buff`：像秘术的「附身」这类加成写在 `<rec>_buff.dbr` 里，
      只用 `db.fields` 会把它的减抗**整块丢掉**（与 `%加成` 那条同一个坑）。
    ★ 带 `(记录, 等级)` 记忆化 —— 本函数在**退火热路径**上（每次评估都跑），
      `fields_with_buff` 每次都会重建一个几百键的 dict，不缓存就是白烧 CPU。
    """
    out = []
    for rec, lv in (levels or {}).items():
        if not lv or lv <= 0 or not db:
            continue
        key = (rec, int(lv))
        p = _SKILL_MEMO.get(key)
        if p is None:
            from . import rotation as R
            try:
                f = R.fields_with_buff(db, rec)
            except Exception:
                f = db.fields(rec) or {}
            p = rr_of(f, int(lv))
            _SKILL_MEMO[key] = p
        if p['add'] or p['max'] or p['flat']:
            out.append(p)
    return combine(*out)


def _spread(out, field, val):
    """把一个 `defensive*` 字段值摊到它影响的抗性桶上（`out` 就地累加）。"""
    hit = DEF_BUCKET.get(field)
    if not hit or not val:
        return
    bk = RES_BUCKETS if hit is _ALL else ((hit,) if not isinstance(hit, tuple) else hit)
    for b in bk:
        out[b] = out.get(b, 0.0) + val


def char_res(folded=None, levels=None, db=None, with_skills=True, base=None):
    """**角色自己**的 10 项抗性（%，面板口径）。

    ★ 与 `rr_of` 共用同一张字段表（`DEF_BUCKET`），方向相反：
      物品/技能上 `defensive<类型>` **正**值是「我的抗性」，
      技能上**负**值才是「对敌减抗」（由 `rr_of` 收走）。
    ★ 用途：`tools/gt_regress.py` 拿它跟 grimtools 的抗性面板对拍 ——
      抗性算错会直接污染 RR 的输入（`base_res` 之外的 `Σadd` 那一侧），
      所以这一项必须能独立验证。

    `base` 可选：`{桶: 基础值}`（如终极难度 -50% 的惩罚），直接加在结果上。
    """
    out = {b: 0.0 for b in RES_BUCKETS}
    for name, f in (folded or {}).items():
        if not isinstance(f, dict):
            continue
        for k, v in f.items():
            if isinstance(k, str) and k in DEF_BUCKET:
                _spread(out, k, _num(v))
    if with_skills and levels and db:
        from . import rotation as R
        for rec, lv in levels.items():
            if not lv or lv <= 0:
                continue
            try:
                f = R.fields_with_buff(db, rec)
            except Exception:
                f = db.fields(rec) or {}
            for k, v in (f or {}).items():
                if isinstance(k, str) and k in DEF_BUCKET:
                    _spread(out, k, _at(v, int(lv)))
    if base:
        for b, v in base.items():
            if b in out:
                out[b] += float(v)
    return out


def add_item(out, gid, items=None, mul=1.0):
    """把一件物品（GT id）的 RR 并进 `out`（就地修改）。"""
    if not gid:
        return out
    if items is None:
        from . import savemap as M
        items = M.gt_items()
    o = items.get(gid)
    if not isinstance(o, dict):
        return out
    p = rr_of(o)
    for b, v in p['add'].items():
        out['add'][b] = out['add'].get(b, 0.0) + v * mul
    for b, v in p['max'].items():
        out['max'][b] = max(out['max'].get(b, 0.0), v * mul)
    for b, v in p['flat'].items():
        out['flat'][b] = max(out['flat'].get(b, 0.0), v * mul)
    return out


def empty():
    """空减抗包（三族齐备；`apply_vs` / `combine` 的零元）"""
    return {'add': {}, 'max': {}, 'flat': {}}


# ---------------------------------------------------------------- 敌方 profile
ENEMY_PROFILES = {
    'none':  {'base': 0.0, 'zh': '无额外抗性（普通小怪）'},
    'elite': {'base': 33.0, 'zh': '精英档'},
    'boss':  {'base': 50.0, 'zh': 'Boss 档'},
    'high':  {'base': 80.0, 'zh': '高抗 Boss'},
    'max':   {'base': 100.0, 'zh': '免疫级（100%）'},
}

# 优化器默认档位。用户口径：不是硬指标，逼近即可 → 取中间档（精英），
# 避免为了「打得动 100% 免疫怪」把预算全砸进减抗。
DEFAULT_PROFILE = os.environ.get('GD_ENEMY_RES_PROFILE', 'elite')


def profile_of(name=None):
    n = name or DEFAULT_PROFILE
    return ENEMY_PROFILES.get(n) or ENEMY_PROFILES['elite']


def base_res_for(bucket, base=None, per_bucket=None):
    """该抗性桶的敌方标称抗性（%）。`per_bucket` 可给逐桶覆盖。"""
    if per_bucket and bucket in per_bucket:
        return float(per_bucket[bucket])
    return float(base if base is not None else profile_of()['base'])


def rr_floor():
    """抗性下限（%）。默认 **−100**，可用 `GD_RR_FLOOR` 覆盖。

    ★ 官方**没有**引擎级硬下限的权威证据 —— 社区实测出现过 **−118%**（⇒ 2.18× 伤害，
      官方论坛《Negative enemy resistance question》）。−100% 是**保守**取值：
      极端减抗流会被**低估**（`docs/rr_mechanics.md` 第五节）。
    """
    try:
        return float(os.environ.get('GD_RR_FLOOR') or -100.0)
    except (TypeError, ValueError):
        return -100.0


def rr_c_neg_mode():
    """C 族（`…ReductionPercent`）打在**负抗性**目标上时的口径。

    | 值 | 公式 | 含义 |
    |---|---|---|
    | `deepen`（默认） | `r × (1 + c/100)` | 把负抗性**推得更深**（官方论坛《Advanced Mechanics》口径） |
    | `pull` | `r × (1 − c/100)` | 把负值**拉回 0**（2019 年一派说法，**旧版实现**） |

    环境变量 `GD_RR_C_NEG` 可切换，用于 A/B 对照（差异实测可达 **+16%**）。
    """
    return (os.environ.get('GD_RR_C_NEG') or 'deepen').strip().lower()


def res_eff(bucket, rr, base_res):
    """减抗后的敌人实际抗性（%）。结算顺序 **B → C → A**。

    ```
    r = base − Σ(B 族 add)            # 叠加类（技能上的负 defensive*）
    r = r × (1 − c/100)   (r ≥ 0)     # C 族（…ReductionPercent）：只取最高，乘算
    r = r × (1 + c/100)   (r < 0)     #   ★ 负抗性时**加深**，不是拉回 0（见 rr_c_neg_mode）
    r = r − max(A 族 flat)            # A 族（…ReductionAbsolute）：只取最高，最后减
    r = max(r, rr_floor())            # 下限（默认 −100%；GD_RR_FLOOR 可放开）
    ```

    ⚠ 2026-09-20 修正：旧版把 A 族并进 B 族**求和**、且对负抗性用 `×(1−c/100)`。
      触发条件是 build 里出现**装备**侧的 A/C 族减抗（全库 57 件：
      C 族 28 / A 族 31，两族都有 2）⇒ 技能表两族皆 0 处，Sam 的历史数字不受影响，
      但**优化器目标函数会变** ⇒ 搜索轨迹重排（同一方案实测新/旧口径差 **+16.2%**）。
    """
    r = float(base_res) - float((rr or {}).get('add', {}).get(bucket, 0.0))
    pct = float((rr or {}).get('max', {}).get(bucket, 0.0))
    if pct:
        if r >= 0 or rr_c_neg_mode() == 'pull':
            k = 1.0 - pct / 100.0
        else:
            k = 1.0 + pct / 100.0
        r *= max(0.0, k)
    flat = float((rr or {}).get('flat', {}).get(bucket, 0.0))
    if flat:
        r -= flat
    return max(r, rr_floor())


def mult_of(bucket, rr, base_res):
    """相对「不减抗」的伤害倍数。

    ⚠ `base_res >= 100` 时基准伤害为 0（免疫），分母钳到 1：
      倍数会很大，那是数学上的必然（从 0 放大），只用于展示，不作优化目标。
    """
    denom = max(100.0 - float(base_res), 1.0)
    return (100.0 - res_eff(bucket, rr, base_res)) / denom


# ---------------------------------------------------------------- 实战 DPS

def row_w(h, r):
    """一行在**该技能的 DPS** 里占的相对权重。

    直伤 = 每击中点；**DoT = 每击中点 × 覆盖率**。

    ★ 为什么要乘覆盖率：一个技能的 DPS = Σ行(每击中点 × 频率 × 覆盖率)，
      而频率对同一技能的所有行是公共因子 ⇒ 行的相对权重就是
      `每击中点 × 覆盖率`。不乘覆盖率会把 DoT 行的占比**系统性高估**
      （Sam：DoT 占面板约 25%，但按每击中点算占比会明显更大），
      于是「逐桶 DPS 拆分」和「+1% 全伤害的等效加成基准」都会偏。
      （实测：修正前后，crit 标定的自洽残差 0.34% → 0.1% 量级。）
    """
    a = float(r.get('avg') or 0.0)
    if a <= 0:
        # rows 里没有 `avg` 键 ⇒ 用**中点**而不是 min，否则权重被系统性压低
        a = (float(r.get('min') or 0.0) + float(r.get('max') or 0.0)) / 2.0
    if a <= 0:
        return 0.0
    if r.get('is_dot'):
        d = float(r.get('dur') or 0.0)
        f = float(h.get('freq') or 0.0)
        a *= min(1.0, d * f) if d > 0 else 1.0
    return a


def dps_by_bucket(hits, weapon_pct=None, key=None):
    """把 `final_report` 的逐技能结果按**抗性桶**汇总成「不减抗时的 DPS」。

    只看有 DPS 的技能（`dps > 0`），每行按 `type` 归桶。

    `key`：读哪个字段。默认**优先 `dps_real`**（含命中/暴击的实战口径），
    没有才退回 `dps`（面板口径）—— 敌方减抗那个乘区本来就该作用在实战值上。
    """
    out = defaultdict(float)
    for _rec, h in (hits or {}).items():
        k = key or ('dps_real' if h.get('dps_real') is not None else 'dps')
        d = h.get(k) or 0.0
        if not d:
            continue
        buckets = defaultdict(float)
        tot = 0.0
        for r in (h.get('rows') or []):
            t = r.get('type')
            b = TYPE_BUCKET.get(t)
            if not b:
                continue
            w = row_w(h, r)
            if w <= 0:
                continue
            buckets[b] += w
            tot += w
        if tot <= 0:
            continue
        for b, w in buckets.items():
            out[b] += d * (w / tot)
    return dict(out)


def dps_by_type(hits):
    """按**伤害类型**汇总 DPS —— 给「伤害转化」模型当「源伤害构成」用。

    为什么要它：转化模型要回答「把 X% 的 A 类伤害转成 B 类，亏多少」，
    而 A 类在总伤害里占多大是**决定性**的。假设「武器基础=纯物理」在
    纯物理武器上没问题（Sam 的剑就是），但一旦装备带出大量元素/活力平伤，
    这个假设会把转化的影响**高估或低估一个数量级**。
    """
    out = defaultdict(float)
    for _rec, h in (hits or {}).items():
        d = h.get('dps') or 0.0
        if not d:
            continue
        acc = defaultdict(float)
        tot = 0.0
        for r in (h.get('rows') or []):
            t = r.get('type')
            if not t:
                continue
            w = row_w(h, r)          # ★ 与 `dps_by_bucket` 同一口径（含 DoT 覆盖率）
            if w <= 0:
                continue
            acc[t] += w
            tot += w
        if tot <= 0:
            continue
        for t, w in acc.items():
            out[t] += d * (w / tot)
    return dict(out)


def apply_vs(rep, rr, profile=None, per_bucket=None, enemy=None):
    """给 `final_report` 的返回值补上「实战伤害」字段（**面板口径不动**）。

    写入：
      `rep['rr']`        —— 本次收集到的减抗（add / max）
      `rep['vs']`        —— {'rows': [{bucket, base, rr_add, rr_pct, res, mult, dps, dps_vs}],
                             'profile', 'dps', 'dps_vs', 'mult_overall'}
      `rep['skills'][rec]['dps_vs'] / ['mult_vs']` —— 每个技能的实战口径

    ★ `enemy`（`gd/enemy.py::get_profile` 的返回值，可选）：**真值口径** ——
      用该怪**逐桶**的抗性（含难度/玩家数修正）替代五档的「全体同值 33%」。
      优先级：`enemy['res']` > `per_bucket` > `profile` 档位。
      这就是「假设档位 → 真值」那一刀：五档仍在，但只在没给 enemy 时兜底。
    """
    _eflat = None
    if enemy:
        from . import enemy as _ENM          # 局部 import：避免与 enemy→rr 的引用成环
        eres, _eflat = _ENM.res_base_map(enemy)
        if eres:
            per_bucket = dict(eres)
            profile = None
    prof = profile_of(profile)
    base = _eflat if _eflat is not None else prof['base']
    rows = []
    tot, tot_vs = 0.0, 0.0
    by_b = dps_by_bucket(rep.get('skills') or {})
    for b in RES_BUCKETS:
        d = by_b.get(b, 0.0)
        if d <= 0:
            continue
        br = base_res_for(b, base, per_bucket)
        m = mult_of(b, rr, br)
        rows.append({
            'bucket': b, 'zh': BUCKET_ZH.get(b, b),
            'base': round(br, 1),
            'rr_add': round(float((rr or {}).get('add', {}).get(b, 0.0)), 1),
            'rr_pct': round(float((rr or {}).get('max', {}).get(b, 0.0)), 1),
            'rr_flat': round(float((rr or {}).get('flat', {}).get(b, 0.0)), 1),
            'res': round(res_eff(b, rr, br), 1),
            'mult': round(m, 3),
            'dps': round(d, 1),
            'dps_vs': round(d * m, 1),
        })
        tot += d
        tot_vs += d * m
    rows.sort(key=lambda x: -x['dps_vs'])

    # 逐技能：按它自己的伤害构成加权出倍数
    # ★ 口径与 `dps_by_bucket` 一致 —— 优先 `dps_real`（含命中/暴击），
    #   否则同一个技能在总表用实战值、在逐技能行用面板值，两边对不上。
    for _rec, h in (rep.get('skills') or {}).items():
        dv = h.get('dps_real')
        if dv is None:
            dv = h.get('dps') or 0.0
        if not dv:
            continue
        acc, wsum = 0.0, 0.0
        for r in (h.get('rows') or []):
            b = TYPE_BUCKET.get(r.get('type'))
            if not b:
                continue
            w = row_w(h, r)      # ★ 与总表同口径（DoT 行含覆盖率）
            if w <= 0:
                continue
            acc += w * mult_of(b, rr, base_res_for(b, base, per_bucket))
            wsum += w
        if wsum > 0:
            h['mult_vs'] = round(acc / wsum, 3)
            h['dps_vs'] = round(dv * acc / wsum, 1)

    rep['rr'] = {k: dict(v) for k, v in (rr or empty()).items()}
    _einfo = None
    if enemy:
        from . import enemy as _ENM
        _einfo = {
            'id': enemy.get('id'), 'kind': enemy.get('kind'),
            'classification': enemy.get('classification'),
            'difficulty': enemy.get('difficulty'), 'players': enemy.get('players'),
            'level': enemy.get('level'), 'da': enemy.get('da'),
            'count': enemy.get('count'), 'rank': enemy.get('rank'),
            'armor': enemy.get('armor'), 'source': enemy.get('source'),
            'zh': _ENM.describe(enemy),
        }
    rep['vs'] = {
        'rows': rows,
        'profile': (enemy.get('id') if enemy else None) or profile or DEFAULT_PROFILE,
        'profile_zh': (_einfo or {}).get('zh') or prof['zh'],
        'enemy_res': base,
        'per_bucket': (bool(per_bucket) and dict(per_bucket)) or None,
        'enemy': _einfo,
        'dps': round(tot, 1), 'dps_vs': round(tot_vs, 1),
        'mult_overall': round(tot_vs / tot, 3) if tot else 1.0,
    }
    return rep


def describe(rr, buckets=None):
    """人类可读的一行摘要（给 CLI / 报告用）。三族按 B→C→A 顺序列出。"""
    parts = []
    for b in (buckets or RES_BUCKETS):
        a = float((rr or {}).get('add', {}).get(b, 0.0))
        p = float((rr or {}).get('max', {}).get(b, 0.0))
        f = float((rr or {}).get('flat', {}).get(b, 0.0))
        if a or p or f:
            seg = []
            if a:
                seg.append('−%.0f%%' % a)                 # B 族：叠加
            if p:
                seg.append('最多 −%.0f%%' % p)              # C 族：取最强
            if f:
                seg.append('最多 −%.0f（绝对值）' % f)       # A 族：取最强
            parts.append('%s %s' % (BUCKET_ZH.get(b, b), '＋'.join(seg)))
    return '；'.join(parts) if parts else '（无）'


def collect_char(folded, skills, db, items=None, with_skills=True):
    """角色侧统一入口：装备（`folded` = 已折叠字段）+ 技能等级 → 合并减抗包。

    返回 `(rr_all, rr_gear, rr_skill)`，三个都是 `{'add':…, 'max':…}`。
    """
    gear = empty()
    for _slot, f in (folded or {}).items():
        gear = combine(gear, rr_of(f))     # ★ 三族语义统一由 combine 兜住
    sk = rr_of_skills(skills, db) if with_skills else empty()
    return combine(gear, sk), gear, sk


class RRCache:
    """按「**与减抗相关的那部分等级**」缓存 `collect_char()`（给优化器用）。

    为什么必须有（陷阱 #85）
    ----------------------
    `tune_skills` / `tune_devotion` / `eval_build_variants` 在一次搜索里会调用
    `final_report(rr=…)` 几十~几万次。旧实现把 `collect_char()` 的结果**在模块级算一次
    就冻结**，于是 **改减抗技能的等级完全不会体现在评分里**：

        刺骨战吼 `defensivePierce` 逐级 `[-4, -7, …, -30, …]`（22 级）
        ⇒ lv1 穿刺减抗 **12%** ／ lv11 **36%** ／ lv12 **38%**
        ⇒ 冻结后「拆 1 点」的损失恒为 **0**

    （注：这类影响**只在实战层** —— `rep['dps']` 面板不含 RR。所以这个 bug 与陷阱 #84
      是**同一对**：先把目标改到 `vs` 层，冻结的 RR 才会暴露出来。）

    做法（既要正确又要快）
    ----------------------
    只把 `levels` 里**带减抗字段的那些记录**的等级拼成 key，key 变了才重算。
    绝大多数试验（动的是没减抗的技能/星位）直接命中缓存 ⇒ 与旧实现同速。
    `extra` 用于第二条输入通道（如 `devotion_levels`）。

    ⚠ `folded`（装备）在一次工具运行内不变 ⇒ 不进 key。
    """

    def __init__(self, folded, db):
        self.folded = folded
        self.db = db
        self._ismemo = {}
        self._cache = {}
        self.recomputes = 0

    def is_source(self, rec):
        """该记录是否带减抗字段（自可叠加 / 取最强 / 绝对三族任一）。"""
        hit = self._ismemo.get(rec)
        if hit is None:
            try:
                f = self.db.fields(rec) or {}
            except Exception:                                    # noqa: BLE001
                f = {}
            hit = any((k in DEF_BUCKET) or (strip_suffix(k) in FIELD_RR) for k in f)
            self._ismemo[rec] = hit
        return hit

    def pack(self, levels, extra=None):
        """→ 减抗包（`{'add':…, 'max':…}`）。只有**减抗相关等级**变了才重算。"""
        sel = {}
        for d in (levels, extra):
            for rec, lv in (d or {}).items():
                if lv and self.is_source(rec):
                    sel[rec] = int(lv)
        key = tuple(sorted(sel.items()))
        hit = self._cache.get(key)
        if hit is None:
            # ★ 只把**原始 levels/extra** 喂给 `collect_char`（不喂筛选后的 sel）——
            #   否则会漏掉「外壳技能 / `_buff` 记录」那条取数路。
            merged = {}
            for d in (levels, extra):
                for rec, lv in (d or {}).items():
                    if lv:
                        merged[rec] = int(lv)
            hit = collect_char(self.folded, merged, self.db)[0]
            self._cache[key] = hit
            self.recomputes += 1
        return hit


def main(argv=None):
    """CLI：`python -m gd rr [角色名] [--profile boss]`

    打印该角色的减抗汇总 + 各敌方档位下**减抗后剩多少抗性 / 乘多少倍**。
    """
    import argparse
    ap = argparse.ArgumentParser(description='减抗（RR）收集与实战伤害换算')
    ap.add_argument('char', nargs='?', default='Sam')
    ap.add_argument('--profile', default=DEFAULT_PROFILE,
                    choices=sorted(ENEMY_PROFILES))
    ap.add_argument('--scan', action='store_true', help='打全库减抗字段普查')
    a = ap.parse_args(argv)

    if a.scan:
        from . import savemap as M
        items = M.gt_items()
        n, zero = 0, 0
        for gid, o in items.items():
            if not isinstance(o, dict):
                continue
            p = rr_of(o)
            if p['add'] or p['max'] or p['flat']:
                n += 1
        print('物品库 %d 件中含 RR 字段的：%d 件' % (len(items), n))
        print()
        # ★ 破甲（护甲降低）普查 —— 与减抗并列的第二个敌方乘区，但**实测为空**
        from . import combat as _CB
        _c = _CB.armor_reduce_constants()
        print('破甲字段普查（%s = 「%s」）' % (_c['tag'], _c['zh']))
        for fn, info in armor_reduce_census().items():
            print('  %-16s %-6s 出现 %s 次'
                  % (fn, info['zh'],
                     ('%d' % info['count']) if info['count'] is not None else '（未读到）'))
        _p = armor_reduce_player_side()
        print('  ⇒ 玩家侧合计 %s 次' % ('?' if _p is None else _p))
        print('  ⚠ 玩家**没有**破甲机制：本作玩家侧降低目标护甲的手段是'
              '「护甲穿透 %」（`offensivePierceRatio`，物理→穿刺，穿刺绕过护甲），')
        print('    它在 gd/dmg.py::_apply_pierce 里（转化链最后一步）。'
              '`armor_reduce` 只是**挂点**，当前恒为 0。')
        print('  ⚠ 「元素护甲」在本作里**不存在**：护甲只吃物理直伤，'
              '元素的减伤通道是抗性（上面那 10 个桶）。')
        return 0

    from . import dps as D
    c = D.load_char(a.char)
    sk = {k: v for k, v in c['skills'].items() if v > 0}
    for rec, extra in (c.get('skill_plus') or {}).items():
        if rec in sk:
            sk[rec] += extra
    rr, rr_gear, rr_skill = collect_char(c['folded'], sk, c['db'])

    print('%s（lv%d %s）减抗汇总' % (a.char, c['level'], '+'.join(c['classes'])))
    print('  技能/星座：%s' % describe(rr_skill))
    print('  装备/词缀：%s' % describe(rr_gear))
    print('  合计      ：%s' % describe(rr))
    print()
    order = ['none', 'elite', 'boss', 'high', 'max']
    print('%-8s' % '抗性桶', end='')
    for k in order:
        print('%22s' % (ENEMY_PROFILES[k]['zh'][:10]), end='')
    print('   ← 每格「减抗后抗性 / 伤害倍数」')
    for b in RES_BUCKETS:
        if not (rr['add'].get(b) or rr['max'].get(b) or rr['flat'].get(b)):
            continue
        print('%-8s' % BUCKET_ZH.get(b, b), end='')
        for k in order:
            br = ENEMY_PROFILES[k]['base']
            r = res_eff(b, rr, br)
            if br >= 100:      # 基准伤害为 0（免疫）⇒ 「倍数」无意义，看剩余抗性
                print('%22s' % ('%+.0f%%  →  从 0' % r), end='')
                continue
            print('%22s' % ('%+.0f%%  →  %.2f×' % (r, mult_of(b, rr, br))), end='')
        print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
