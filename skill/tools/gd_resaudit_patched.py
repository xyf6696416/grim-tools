# -*- coding: utf-8 -*-
"""抗性溢出审计（库）—— 「满抗了，为什么还溢出这么多？」

为什么要有它（2026-09-20）
--------------------------
`满抗` 在本项目里是**逐维硬约束**（上排 130 / 下排 105 = 终极难度显示 80% 所需），
而优化器的目标函数**对超出部分没有任何惩罚** ⇒ 报告只给一个「溢出 255 点」的总数，
既看不出**是谁供的**，也判断不了「能不能换成伤害」。

拆开之后两类事实才浮出来：

1. **溢出集中在元素三抗**，因为 `defensiveElementalResistance` 是
   **一条字段同时给火/冰/电**（三合一）⇒ 只把**最弱那一项**顶到 130，另两项必然同高，
   **物理上无法精调**。下排四维都是单项字段 ⇒ 能卡到 ±1（实测 活力 0 / 混乱 +1）。
2. **溢出的抗性往往是「搭便车」来的**：带元素三抗的词缀（「雷击的」「奥术平衡之」）
   同时给 OA 与元素伤害 ⇒ 它们本来就是**伤害最优解**，换掉只会掉输出。

⇒ 所以正确读法是：溢出确实是纯浪费（80% 是硬上限、不转化），但**边际成本为零**。

本模块是**单一真源**：`gd/planreport.py`（出 BD 报告 §二）、`tools/plan_audit.py`
（交付物 `REPORT_*.md`）、`tools/res_audit.py`（独立 CLI）都调它 —— 别各写一套。
"""
from __future__ import annotations

import collections

# 抗性维度 → 组成它的字段（与 `gd/opt.py::RMAP` 同源，改一处要同步另一处）
DIMMAP = collections.OrderedDict([
    ('火', ['defensiveElementalResistance', 'defensiveFire']),
    ('冰', ['defensiveElementalResistance', 'defensiveCold']),
    ('电', ['defensiveElementalResistance', 'defensiveLightning']),
    ('毒酸', ['defensivePoison']), ('穿刺', ['defensivePierce']),
    ('流血', ['defensiveBleeding']), ('活力', ['defensiveLife']),
    ('虚化', ['defensiveAether']), ('混乱', ['defensiveChaos']),
])
# ★ 反向表：一条字段喂给**哪几个维度**。`defensiveElementalResistance` 一次喂 3 个 ——
#   这正是「上排溢出远大于下排」的结构性原因。
FIELD_DIMS = collections.OrderedDict()
for _dim, _flds in DIMMAP.items():
    for _f in _flds:
        FIELD_DIMS.setdefault(_f, []).append(_dim)

# 字段 → 中文（展示用；`元素三抗` 是**一条字段**的名字，不是维度）
RESF = {
    'defensiveElementalResistance': '元素三抗', 'defensiveFire': '火',
    'defensiveCold': '冰', 'defensiveLightning': '电', 'defensivePoison': '毒酸',
    'defensivePierce': '穿刺', 'defensiveBleeding': '流血', 'defensiveLife': '活力',
    'defensiveAether': '虚化', 'defensiveChaos': '混乱', 'defensivePhysical': '物理',
}
# 输出向字段（用来回答「这条抗性换来/搭来了什么」）
GOODF = collections.OrderedDict([
    ('characterOffensiveAbility', 'OA'), ('characterDefensiveAbility', 'DA'),
    ('characterStrength', '体格'), ('characterDexterity', '狡诈'),
    ('characterIntelligence', '精神'),
    ('offensiveElementalModifier', '元素伤害%'), ('offensiveFireModifier', '火伤%'),
    ('offensiveColdModifier', '冰伤%'), ('offensiveLightningModifier', '电伤%'),
    ('offensivePierceModifier', '穿刺伤害%'),
    ('offensiveBleedingModifier', '流血伤害%'), ('offensiveSlowBleedingModifier', '流血伤害%'),
    ('offensiveTotalDamageModifier', '总伤害%'),
    ('characterAttackSpeedModifier', '攻速%'), ('characterTotalSpeedModifier', '全速%'),
])
SLOT_FIELDS = ('base', 'comp', 'aug', 'pre', 'suf')
SLOT_ZH = {'base': '底材', 'comp': '镶嵌', 'aug': '附魔', 'pre': '前缀', 'suf': '后缀'}

# ★ 兜底常量：与 `gd/opt.py::TYPES / NEED` 同源。**报告链路会传入权威值**，
#   这里只在「不想 import 重模块」的场合（独立 CLI）用 —— `import gd.opt` 会构造
#   整个候选池（秒级），不值得为两个常量付这个钱。
TYPES = ['火', '冰', '电', '毒酸', '穿刺', '流血', '活力', '虚化', '混乱']
TOP = ('火', '冰', '电', '毒酸', '穿刺')
NEED = {t: (130 if t in TOP else 105) for t in TYPES}


def _default_get():
    from . import DB
    return DB.load().get


def _default_name():
    from . import DB
    return DB.load().name


def _pick(get=None, items=None, db=None):
    if get is not None:
        return get
    if items is not None:
        return items.get
    if db is not None:
        return db.get
    return _default_get()


def per_slot_from_plan(plan, get=None, items=None, db=None):
    """方案（{槽位: [gid × 5]}）→ {槽位: Counter(维度 → 值)}。"""
    get = _pick(get, items, db)
    out = collections.OrderedDict()
    for slot, ids in (plan or {}).items():
        d = collections.Counter()
        for i, gid in enumerate(ids or []):
            if not gid or i >= len(SLOT_FIELDS):
                continue
            f = get(gid) or {}
            for fld, dims in FIELD_DIMS.items():
                v = f.get(fld)
                if isinstance(v, (int, float)) and v:
                    for t in dims:
                        d[t] += v
        out[slot] = d
    return out


def per_slot_from_folded(folded):
    """存档 `folded`（{槽位: 字段 dict}）→ 同结构。

    ⚠ `folded` 里的抗性字段**已经含词缀/镶嵌/附魔**（是「身上那件」的合成值），
      所以不能直接按字段名当维度累加 —— 必须按 `FIELD_DIMS` 展开。
    """
    out = collections.OrderedDict()
    for slot, f in (folded or {}).items():
        d = collections.Counter()
        for fld, dims in FIELD_DIMS.items():
            v = (f or {}).get(fld)
            if isinstance(v, (int, float)) and v:
                for t in dims:
                    d[t] += v
        out[slot] = d
    return out


def totals(per):
    t = collections.Counter()
    for d in (per or {}).values():
        t.update(d)
    return t


def sources(plan, slot, get=None, items=None, db=None, name_of=None):
    """某槽的逐来源明细：[(来源中文, 名称, {抗性字段: 值}, {输出向字段: 值})]"""
    get = _pick(get, items, db)
    if name_of is None:
        name_of = lambda g: _db().name(g)                        # noqa: E731
    out = []
    ids = (plan or {}).get(slot) or []
    for i, gid in enumerate(ids):
        if not gid or i >= len(SLOT_FIELDS):
            continue
        f = get(gid) or {}
        res = {RESF[k]: v for k, v in f.items()
               if k in RESF and isinstance(v, (int, float)) and v}
        good = {GOODF[k]: v for k, v in f.items()
                if k in GOODF and isinstance(v, (int, float)) and v}
        try:
            label = name_of(gid)
        except Exception:                                        # noqa: BLE001
            label = gid
        out.append((SLOT_ZH[SLOT_FIELDS[i]], str(label or gid), res, good))
    return out


def _db():
    from . import DB
    return DB.load()


def render_md(per, get=None, items=None, db=None, types=None, need=None,
              plan=None, top=3, detail=0, folded=False, name_of=None):
    """抗性分解 → markdown 行列表（报告直接 `L.extend`）。

    `types` / `need` 可由调用方传入（`gd/opt.py` 的 TYPES / NEED 是权威定义）；
    不传就用本模块的兜底常量，**绝不为了两个常量去 import 重模块**。
    """
    types = list(types or TYPES)
    need = dict(need or NEED)
    tot = totals(per)
    L = []

    ov = {t: tot.get(t, 0) - need.get(t, 0) for t in types if need.get(t)}
    ov_sum = sum(v for v in ov.values() if v > 0)
    tight = sorted((t for t in types if need.get(t)), key=lambda t: tot.get(t, 0) - need[t])[:4]

    L.append('### 抗性来源分解（为什么会溢出）')
    L.append('')
    L.append('| 抗性 | 合计 | 需求 | 溢出 |')
    L.append('|---|---|---|---|')
    for t in types:
        nd = need.get(t)
        if not nd:
            continue
        o = tot.get(t, 0) - nd
        L.append('| %s | %.0f | %d | %s |'
                 % (t, tot.get(t, 0), nd,
                    ('**+%.0f**' % o) if o > 0 else ('%+.0f' % o)))
    L.append('')
    L.append('- 溢出合计 **%.0f** 点 ｜ 上排（元素/毒酸/穿刺）%.0f ｜ 下排 %.0f'
             % (ov_sum,
                sum(v for t, v in ov.items()
                    if t in ('火', '冰', '电', '毒酸', '穿刺') and v > 0),
                sum(v for t, v in ov.items()
                    if t in ('流血', '活力', '虚化', '混乱') and v > 0)))
    L.append('- ★ **真正卡在线上**的维度：%s'
             % '、'.join('%s %+.0f' % (t, tot.get(t, 0) - need[t]) for t in tight))
    L.append('- ★ **溢出的抗性在游戏里完全无效**（80% 是硬上限，不转化成别的）。'
             '但注意下面两条：**① 元素三抗是一条字段给三项**（`defensiveElementalResistance`）'
             '⇒ 只把最弱那项顶到 130，另两项必然同高，**无法精调**；'
             '**② 溢出的抗性常常是「搭便车」来的**（带元素三抗的词缀同时给 OA / 元素伤害）'
             '⇒ 换掉它只会掉输出。优化器实测**找不到更好的套装**就是这个原因。')
    L.append('')
    ranked = sorted((per or {}).items(), key=lambda x: -sum(x[1].values()))
    if top:
        ranked = ranked[:top]
    L.append('**各槽抗性供给（前 %d）**' % len(ranked))
    L.append('')
    L.append('| 槽位 | 合计 | 主要来源 |')
    L.append('|---|---|---|')
    for slot, d in ranked:
        L.append('| %s | %.0f | %s |'
                 % (slot, sum(d.values()),
                    '、'.join('%s +%.0f' % (k, v) for k, v in d.most_common(4)) or '—'))
    L.append('')

    if detail and plan:
        L.append('**逐来源明细（抗性 ★ 与它搭便车的输出向字段）**')
        L.append('')
        for slot, _d in ranked[:detail]:
            L.append('- **%s**' % slot)
            for tag, label, res, good in sources(plan, slot, get, items, db, name_of):
                L.append('    - %s `%s`：%s%s'
                         % (tag, label,
                            '、'.join('%s +%g' % (k, v) for k, v in res.items()) or '无抗性',
                            ('　（附带 %s）' % '、'.join('%s +%g' % (k, v)
                                                     for k, v in good.items())) if good else ''))
        L.append('')
    L.append('> 独立查：`$PY tools/res_audit.py <角色> --plan <方案.json> [--slot 肩甲] [--top N]`'
             '（读存档现穿装备时省略 `--plan`）。')
    L.append('')
    return L
