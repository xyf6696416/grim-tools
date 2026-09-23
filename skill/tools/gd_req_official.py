# -*- coding: utf-8 -*-
r"""装备属性需求（体格/狡诈/精神）—— **官方公式版**（2026-09-19 重写）

为什么重写
----------
旧版是靠「实测标定 + 部位外推」的线性近似（只有头部/腿部是实测，其余 ±10%），
因为它假设「需求值不在任何可读数据源里」。**这个假设是错的**：

  · 游戏 `.dbr` 里的 `strengthRequirement` / `dexterityRequirement` /
    `intelligenceRequirement` 确实**恒为 0**（引擎在运行时按公式算）；
  · 但 **Grim Tools 桌面版把官方公式原样带在了包里** ——
    `app.asar` 里 `itemCostFormulae = { cf1: {…}, …, cf13: {…} }`，
    每个公式集含 `headStrengthEquation` / `chestIntelligenceEquation` /
    `swordDexterityEquation` … 共 31 类方程。
  · 装备用哪个公式集由物品字段 **`m`（itemCostName）** 决定；
    哪个方程由**部位**决定（GT 的 `Ob(d)` switch）。
  · 需求 = `Math.round(evaluate(equation, {itemLevel, totalAttCount, …}))`。

抽取脚本：`tools/build_cost_formulae.py` → `data/cost_formulae.json`。
旧估算版归档在 `tools/legacy/gd_req_estimate.py`。

交叉验证（旧版「实测标定」来自公开数据表，这里逐条对上）
--------------------------------------------------------
| 断言 | 轻甲盔 94 | 重甲盔 94 | 法系盔 84 | 轻甲腿 94 | 重甲腿 94 |
|---|---|---|---|---|---|
| 实测 | 538 | 915 | 320 | 662 | 1035 |
| 公式 | 538.0 | 915.4 | 320.2 | 662.4 | 1035.1 |

`ilvl1`：轻甲盔 14.8 / 重甲盔 33.3 / 法系盔 19.0 —— 与实测 15 / 33 / 19 一致。

口径
----
* 需求按 **`itemLevel`**（不是 `k`=levelRequirement）算，与 GT 的 `P(d)` 一致。
* 饰品（项链/戒指）的方程含 `totalAttCount`（= 提示框属性行数，含前缀/后缀），
  这是个**估算量**，见 `total_att_estimate()`；`confidence` 标 `'official*'`。
* 勋章（medal）与圣物**没有属性需求**（GT 的 switch 里 `medal` 直接跳过、
  `ItemArtifact` 落到武器分支但模板不匹配）—— 与游戏一致。
* 这是**面板值**要满足的阈值：面板 = 存档值 + 精通 + 装备平值，再 ×(1+装备%值/100)。

用法
----
```python
from . import req as R
R.req('it12230', slot='靴子')          # {'physique': 554, 'cunning': 0, 'spirit': 0, …}
R.panel_needed(['it1351','it12230'])    # 全套需要的面板属性
R.check_panel(gids, {'physique':533,'cunning':967,'spirit':270})
```
"""
from __future__ import annotations

import ast
import json
from typing import Optional

from . import paths

__all__ = ['req', 'need_of_rows', 'panel_needed', 'check_panel', 'total_att_estimate',
           'weapon_kind', 'SLOT_CAT_BY_CN', 'CAT_EQ', 'WEAPON_STEM_EQ']

# ---------------------------------------------------------------- 公式表
_FORM = None


def _form():
    global _FORM
    if _FORM is None:
        p = paths.DATA_DIR / 'cost_formulae.json'
        try:
            _FORM = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            _FORM = {'default': 'cf1', 'sets': {}}
    return _FORM


# ---------------------------------------------------------------- 表达式求值
def _ev(expr: str, vars: dict) -> float:
    """求值 GT 的方程串。`^` 是幂（不是 XOR），变量取自 vars，缺省 0。"""
    tree = ast.parse(expr.replace('^', '**'), mode='eval')

    def rec(n):
        if isinstance(n, ast.Expression):
            return rec(n.body)
        if isinstance(n, ast.BinOp):
            a, b = rec(n.left), rec(n.right)
            o = n.op
            if isinstance(o, ast.Add):
                return a + b
            if isinstance(o, ast.Sub):
                return a - b
            if isinstance(o, ast.Mult):
                return a * b
            if isinstance(o, ast.Div):
                return a / b
            if isinstance(o, ast.Pow):
                return a ** b
            raise ValueError(o)
        if isinstance(n, ast.UnaryOp):
            v = rec(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name):
            return float(vars.get(n.id) or 0.0)
        raise ValueError(n)
    return float(rec(tree))


def _eqs(m_set: Optional[str]) -> dict:
    """default(cf1) 打底 + 该件自己的公式集覆盖 —— 与 GT 的 `b.extend` 同口径。"""
    f = _form()
    out = dict(f['sets'].get(f.get('default') or 'cf1') or {})
    out.update(f['sets'].get(m_set or '') or {})
    return out


# ---------------------------------------------------------------- 部位
SLOT_CAT_BY_CN = {
    '头部': 'head', '胸甲': 'chest', '腿甲': 'legs', '靴子': 'feet',
    '手套': 'hands', '肩甲': 'shoulders', '腰带': 'waist',
    '项链': 'amulet', '戒指1': 'ring', '戒指2': 'ring', '勋章': 'medal',
    '圣物': 'relic', '主手': 'weapon', '副手': 'weapon',
}

# 模板名 → 部位（与 GT 的 `Ob(d)` switch 一致）
TMPL_CAT = {
    'ArmorJewelry_Amulet': 'amulet', 'ArmorJewelry_Ring': 'ring',
    'ArmorJewelry_Medal': 'medal', 'ItemRelic': 'component',
    'ItemArtifact': 'relic', 'ArmorProtective_Waist': 'waist',
    'ArmorProtective_Head': 'head', 'ArmorProtective_Shoulders': 'shoulders',
    'ArmorProtective_Hands': 'hands', 'ArmorProtective_Feet': 'feet',
    'ArmorProtective_Legs': 'legs', 'ArmorProtective_Chest': 'chest',
}
for _t in ('WeaponArmor_Offhand', 'WeaponArmor_Shield', 'WeaponMelee_Sword',
           'WeaponMelee_Axe', 'WeaponMelee_Mace', 'WeaponHunting_Ranged1h',
           'WeaponMelee_Dagger', 'WeaponMelee_Scepter', 'WeaponMelee_Sword2h',
           'WeaponMelee_Axe2h', 'WeaponMelee_Mace2h', 'WeaponMelee_Spear2h',
           'WeaponHunting_Ranged2h'):
    TMPL_CAT[_t] = 'weapon'

# 部位 → （体格方程, 精神方程）—— 缺省 None 表示该部位不吃这个属性
CAT_EQ = {
    'head': ('headStrengthEquation', 'headIntelligenceEquation'),
    'chest': ('chestStrengthEquation', 'chestIntelligenceEquation'),
    'legs': ('legsStrengthEquation', None),
    'feet': ('feetStrengthEquation', None),
    'hands': ('handsStrengthEquation', None),
    'shoulders': ('shouldersStrengthEquation', None),
    'waist': ('waistStrengthEquation', None),
    'amulet': (None, 'amuletIntelligenceEquation'),
    'ring': (None, 'ringIntelligenceEquation'),
    'medal': (None, None),
    'relic': (None, None), 'component': (None, None), 'weapon': (None, None),
}

# 武器：模板 → （属性, 方程）
WEAPON_EQ = {
    'WeaponMelee_Sword': ('cunning', 'swordDexterityEquation'),
    'WeaponHunting_Ranged1h': ('cunning', 'ranged1hDexterityEquation'),
    'WeaponHunting_Ranged2h': ('cunning', 'ranged2hDexterityEquation'),
    'WeaponMelee_Dagger': ('cunning', 'daggerDexterityEquation'),
    'WeaponMelee_Axe': ('physique', 'axeStrengthEquation'),
    'WeaponMelee_Mace': ('physique', 'maceStrengthEquation'),
    'WeaponMelee_Axe2h': ('physique', 'melee2hStrengthEquation'),
    'WeaponMelee_Mace2h': ('physique', 'melee2hStrengthEquation'),
    'WeaponMelee_Sword2h': ('physique', 'melee2hStrengthEquation'),
    'WeaponMelee_Spear2h': ('physique', 'melee2hStrengthEquation'),
    'WeaponArmor_Shield': ('physique', 'shieldStrengthEquation'),
}
# 施法器（权杖/副手）走 精神 方程
CASTER_EQ = {'WeaponMelee_Scepter': ('spirit', 'scepterIntelligenceEquation'),
             'WeaponArmor_Offhand': ('spirit', 'offhandIntelligenceEquation')}

# ★★ 武器类型 → （属性, 方程），**按记录文件名词干判定**。
#
# 为什么不用模板名：`gd/dbr.open_all().template(record)` 在运行时**返回 None**
# （`database.arz` 不在运行时依赖里），于是 `WEAPON_EQ` 这条分支历史上**从未生效过**
# —— 见下面 `req()` 里那条被 `CAT_EQ['weapon']` 抢掉的死分支。
# 而 `.dbr` 的**目录 + 文件名**就是游戏自己的类型依据（`caster/_scepter.dbr` 必然是
# 权杖、`melee2h/_blunt2h.dbr` 必然走 2 手力量方程），比任何启发式都硬。
# 实测词干（全库枚举）：_dagger / _scepter / _sword / _sword2h / _axe / _axe2h /
#                      _blunt2h / _hammer / _mace / _gun1h / _gun2h / _focus / _shield
#
# ⚠ 顺序敏感：**长的先匹配**（`sword2h` 必须在 `sword` 之前，`axe2h` 在 `axe` 之前）。
WEAPON_STEM_EQ = (
    ('scepter', ('spirit', 'scepterIntelligenceEquation')),
    ('dagger', ('cunning', 'daggerDexterityEquation')),
    ('sword2h', ('physique', 'melee2hStrengthEquation')),
    ('blunt2h', ('physique', 'melee2hStrengthEquation')),
    ('axe2h', ('physique', 'melee2hStrengthEquation')),
    ('spear2h', ('physique', 'melee2hStrengthEquation')),
    ('mace2h', ('physique', 'melee2hStrengthEquation')),
    ('sword', ('cunning', 'swordDexterityEquation')),
    ('axe', ('physique', 'axeStrengthEquation')),
    ('mace', ('physique', 'maceStrengthEquation')),
    ('hammer', ('physique', 'maceStrengthEquation')),
    ('gun2h', ('cunning', 'ranged2hDexterityEquation')),
    ('gun1h', ('cunning', 'ranged1hDexterityEquation')),
    ('rifle', ('cunning', 'ranged2hDexterityEquation')),
    ('focus', ('spirit', 'offhandIntelligenceEquation')),
    ('shield', ('physique', 'shieldStrengthEquation')),
)


def weapon_kind(record: Optional[str]):
    """记录名 → (类型词干, 属性, 方程)。认不出返回 (None, None, None)。

    `records/items/gearweapons/caster/d004_scepter.dbr` → ('scepter', 'spirit',
    'scepterIntelligenceEquation')
    """
    if not record:
        return None, None, None
    stem = record.replace('\\', '/').rsplit('/', 1)[-1]
    if stem.lower().endswith('.dbr'):
        stem = stem[:-4]
    low = stem.lower()
    for key, (attr, eq) in WEAPON_STEM_EQ:
        if key in low:
            return key, attr, eq
    return None, None, None

# 装备上的「减需求」字段（极少见，按百分比扣）——
# ★★ 字段名里**写着作用属性与限定条件**，必须按属性分别扣。
#
#   实测代价（2026-09-20 用户报「好多装备穿不上」）：
#   `it1800`（使者的夹克，胸甲）带 `characterHuntingDexterityReqReduction: 15`
#   —— 名字里是 **Dexterity（狡诈）**，却被旧实现当成**全局减免**扣到**体格**上：
#   体格需求 **464 → 394**（×0.85），而**游戏提示框明写「需要体格: 464」**。
#   ⇒ 需求被系统性低估 ⇒ 模型报「全部可穿 ✓」而游戏里穿不上（静默放行）。
#
#   ⚠ 限定条件（`Armor*` 只对护甲 / `Melee*` 只对近战武器 / `Hunting*` 只对远程）
#     目前**未判**（缺游戏内验证，已登记待查）；**只按属性匹配**就已经修掉本 bug。
REQ_REDUCE_RULES = (
    ('characterArmorStrengthReqReduction',    'physique'),
    ('characterMeleeStrengthReqReduction',    'physique'),
    ('characterArmorDexterityReqReduction',   'cunning'),
    ('characterMeleeDexterityReqReduction',   'cunning'),
    ('characterHuntingDexterityReqReduction', 'cunning'),
    ('characterMeleeIntelligenceReqReduction', 'spirit'),
)
# 兼容旧名（其它模块可能引用这张字段表）
REQ_REDUCE_FIELDS = tuple(k for k, _ in REQ_REDUCE_RULES)

# 元数据字段（不算「提示框属性行」）
_META = frozenset((
    'a', 'b', 'c', 'd', 'f', 'g', 'h', 'i', 'k', 'l', 'm', 'n', 'o', 'p',
    'itemLevel', 'levelRequirement', 'droppable', 'maxAffixes', 'MI',
    'uniqueRare', 'itemStyleTag', 'bitmap', 'itemCostName',
    'augmentSkillName1', 'augmentSkillName2', 'augmentSkillName3',
    'itemSkillName', 'itemSkillAutoController', 'itemSkillLevelEq',
    'itemClassification', 'artifactClassification', 'bonusTableName',
))

_DB = None


def _items():
    global _DB
    if _DB is None:
        from . import DB
        _DB = DB.load()
    return _DB


def _obj(gid: str) -> dict:
    """取离线库物品对象。**传记录名也行** —— 过桥表反查 gid 后再取。

    这样一来 `req()` / `panel_needed()` / `total_att_estimate()` 都能直接吃记录名，
    调用方（写档链路）不必再自己维护 gid ↔ 记录名的两张表。
    """
    try:
        o = _items().get(gid)
        if o:
            return o
    except Exception:
        pass
    if gid and ('.dbr' in gid or '/' in gid or '\\' in gid):
        try:
            from .save import items as _SI
            g = _SI.bridge().gid_of(str(gid).replace('\\', '/'))
            if g:
                return _items().get(g) or {}
        except Exception:
            pass
    return {}


def total_att_estimate(gid: str, extra_gids=()) -> int:
    """估算提示框「属性行数」`totalAttCount`（只影响饰品方程）。

    GT 自己用的是 `W.length` —— `gb(S)` 渲染出的行数，而 `S` 已经把
    **前缀/后缀**的 stats 合并进来了（`S=va(d,1); g&&(S=wa(S,va(g,1)))`），
    所以要把词缀记录也一起数：`extra_gids`。

    这里用「非零数值字段数」近似；两边都是「行数量级」，误差 ±几行。
    上层要更保守可加安全行数（`req(..., att_safety=…)`）。
    """
    n = 0
    for g in (gid,) + tuple(extra_gids or ()):
        o = _obj(g)
        for k, v in o.items():
            if k in _META or k.startswith('augment') or k.startswith('conversion'):
                continue
            if isinstance(v, (int, float)) and v:
                n += 1
    return max(1, n)


# ---------------------------------------------------------------- 主接口
def req(gid: str, record: Optional[str] = None, slot: Optional[str] = None,
        item_level: Optional[float] = None, att_safety: int = 0,
        extra_gids=(), total_att: Optional[int] = None) -> dict:
    """装备的属性需求。

    slot  : 中文槽位名（'靴子'）或 GT 部位码（'feet'），优先用中文槽位
    record: `.dbr` 记录名（用于取模板判部位 / 读减需求字段）
    item_level: 覆盖物品等级（默认取离线库 `itemLevel`）
    att_safety: 给饰品的 totalAttCount 加的安全行数（默认 0）
    extra_gids: 该件的**前缀/后缀** GT id（计入提示框行数）
    total_att : 直接指定提示框行数（覆盖估算）

    返回 {'physique','cunning','spirit','confidence','ilvl','cat','set','att'}
    """
    o = _obj(gid)
    ilvl = item_level if item_level is not None else (o.get('itemLevel') or o.get('k') or 0)
    m_set = o.get('m') or o.get('itemCostName')
    out = {'physique': 0, 'cunning': 0, 'spirit': 0, 'confidence': 'official',
           'ilvl': ilvl, 'cat': None, 'set': m_set, 'att': 0, 'weapon_kind': None}

    if not ilvl:
        out['confidence'] = 'none'
        return out

    # 部位判定：中文槽位 > 模板
    tmpl = None
    if record:
        try:
            from . import dbr as _D
            tmpl = _D.open_all().template(record)
        except Exception:
            tmpl = None
    cat = None
    if slot:
        cat = SLOT_CAT_BY_CN.get(slot) or (slot if slot in CAT_EQ else None)
    if cat is None and tmpl:
        cat = TMPL_CAT.get(tmpl)
    if cat is None:
        bmp = (o.get('n') or '').lower()
        for d, s in (('gearweapons', 'weapon'),
                     ('gearhead', 'head'), ('geartorso', 'chest'),
                     ('gearlegs', 'legs'), ('gearshoulders', 'shoulders'),
                     ('gearhands', 'hands'), ('gearfeet', 'feet'),
                     ('waist', 'waist'), ('necklace', 'amulet'),
                     ('rings', 'ring'), ('medal', 'medal')):
            if d in bmp:
                cat = s
                break
    out['cat'] = cat

    eqs = _eqs(m_set)
    att = (int(total_att) if total_att is not None
           else total_att_estimate(gid, extra_gids)) + max(0, int(att_safety))
    out['att'] = att
    v = {'itemLevel': ilvl, 'itemlevel': ilvl, 'totalAttCount': att}

    # ★★ 武器必须在 CAT_EQ 之前处理。
    #    历史 Bug：`CAT_EQ` 里有 `'weapon': (None, None)`，于是 `cat == 'weapon'` 时
    #    `if cat in CAT_EQ` 先命中、一个字段都不设就走到返回 —— 底下那条
    #    `elif cat == 'weapon' and tmpl:` **永远执行不到**（死代码），
    #    结果**任何武器的需求都被算成 0，而且 confidence 还是 'official'**
    #    （静默错误：角色拿不起武器也看不出来）。
    if cat == 'weapon':
        # 模板名优先（能拿到就用，最权威）；拿不到就退回**记录文件名词干**
        rule = WEAPON_EQ.get(tmpl) or CASTER_EQ.get(tmpl) if tmpl else None
        kind = conf_kind = None
        if rule:
            kind, conf_kind = tmpl, 'official'
        else:
            kind, attr0, eq0 = weapon_kind(record)
            if eq0:
                rule = (attr0, eq0)
                conf_kind = 'official~'      # 方程官方，类型由文件名词干判定
        out['weapon_kind'] = kind
        if rule and rule[1] in eqs:
            attr, key = rule
            out[attr] = round(_ev(eqs[key], v))
            out['confidence'] = conf_kind or 'official~'
        else:
            # 认不出类型 → **不谎报 official**，上层要能看出「这件没评过」
            out['confidence'] = 'unknown'
        return _apply_reduce(out, record)

    if cat and cat in CAT_EQ:
        stk, iek = CAT_EQ[cat]
        if stk and stk in eqs:
            out['physique'] = round(_ev(eqs[stk], v))
        if iek and iek in eqs:
            out['spirit'] = round(_ev(eqs[iek], v))
            out['confidence'] = 'official*'      # 含 totalAttCount 估算
        if not (stk or iek):
            # 该部位本就没有需求（勋章 / 圣物），这是**游戏事实**，保持 official
            out['confidence'] = 'official'
    else:
        out['confidence'] = 'unknown' if cat is None else 'official'
    return _apply_reduce(out, record)


def _apply_reduce(out: dict, record: Optional[str]) -> dict:
    """装备上的「减需求」字段（极少见，按百分比扣**对应属性**）。

    ★ 2026-09-20 修 bug：旧实现把**所有** REQ_REDUCE_FIELDS 取 max 后
    **无差别**地扣 physique / cunning / spirit 三个属性 ⇒ 只要装备带任意一个
    减需求字段，三个属性需求**全部**被扣一遍。
    实测：胸甲 `it1800` 带 `characterHuntingDexterityReqReduction: 15`（狡诈）
    ⇒ 体格需求被扣成 **394**（实际 **464**，游戏提示框为准）。
    现在按**字段名里的属性**分别扣；同一属性多个字段取 max，只扣一次。
    """
    if not record:
        return out
    try:
        from . import dbr as _D
        f = _D.open_all().fields(record) or {}
        red_by_attr: dict = {}
        for key, attr in REQ_REDUCE_RULES:
            vv = f.get(key)
            if vv and vv[0]:
                red_by_attr[attr] = max(red_by_attr.get(attr, 0.0),
                                        abs(float(vv[0])))
        for attr, red in red_by_attr.items():
            if red and out.get(attr):
                out[attr] = int(round(out[attr] * (1.0 - red / 100.0)))
    except Exception:
        pass
    return out


def need_of_rows(rows, att_safety: int = 0) -> dict:
    """★ **推荐入口**：按「装备行」算全套需求。

    `rows` = `[{'slot': '靴子', 'record': '…/d202_feet.dbr', 'extras': ['…/suf…dbr']}]`
    （`slot` 中文槽位名，`extras` = 前缀/后缀记录名，用于饰品的提示框行数）。

    **用记录名而不是 gid 做键** —— 同名的两件（两个一样的戒指）不会互相覆盖。

    返回 `{'physique','cunning','spirit','items':[…],'unevaluated':[…]}`，
    `items` 逐件带 `slot` / `record` / 需求 / `confidence`。
    """
    tot = {'physique': 0, 'cunning': 0, 'spirit': 0}
    items, unevaluated = [], []
    for row in rows:
        rec = row.get('record') or ''
        if not rec:
            continue
        r = req(rec, rec, row.get('slot'), att_safety=att_safety,
                extra_gids=tuple(row.get('extras') or ()))
        item = dict(r)
        item['slot'] = row.get('slot')
        item['record'] = rec
        item['extras'] = list(row.get('extras') or ())
        items.append(item)
        if r.get('confidence') == 'unknown':
            unevaluated.append(item)
        for a in ('physique', 'cunning', 'spirit'):
            tot[a] = max(tot[a], r[a])
    return {'physique': int(tot['physique']), 'cunning': int(tot['cunning']),
            'spirit': int(tot['spirit']), 'items': items,
            'unevaluated': unevaluated}


def panel_needed(gids, records=None, slots=None, att_safety: int = 0,
                 extra=None) -> dict:
    """按 GT id 算全套需求（历史接口，保留兼容）。

    新代码建议用 `need_of_rows()` —— 它能区分「两件一模一样的装备」，
    也接受记录名（`_obj()` 内部会过桥）。
    额外返回 `unevaluated`：**认不出类型、需求没评估过的件**
    （`confidence=='unknown'`）。上层必须把这个列表报给用户 ——
    否则「校验通过」是假通过。
    """
    records = records or {}
    slots = slots or {}
    extra = extra or {}
    tot = {'physique': 0, 'cunning': 0, 'spirit': 0}
    detail, unevaluated = {}, []
    for gid in gids:
        r = req(gid, records.get(gid), slots.get(gid), att_safety=att_safety,
                extra_gids=extra.get(gid) or ())
        detail[gid] = r
        if r.get('confidence') == 'unknown':
            unevaluated.append((gid, records.get(gid), slots.get(gid)))
        for a in ('physique', 'cunning', 'spirit'):
            tot[a] = max(tot[a], r[a])
    return {'physique': int(tot['physique']), 'cunning': int(tot['cunning']),
            'spirit': int(tot['spirit']), 'items': detail,
            'unevaluated': unevaluated}


def check_panel(gids, panel, records=None, slots=None, att_safety: int = 0,
                extra=None) -> dict:
    """逐件对照面板属性，列出「穿不上」的件。

    面板是**最终值**（存档 + 精通 + 装备平值，再 ×(1+装备%值)）。
    返回 {'ok': [...], 'fail': [(gid, 缺什么, 需求), ...], 'worst': {...}}
    """
    need = panel_needed(gids, records, slots, att_safety=att_safety, extra=extra)
    ok, fail = [], []
    for gid, r in (need['items'] or {}).items():
        short = {}
        for k, cn in (('physique', '体格'), ('cunning', '狡诈'), ('spirit', '精神')):
            if r[k] and r[k] > (panel.get(k) or 0):
                short[cn] = r[k] - (panel.get(k) or 0)
        if short:
            fail.append((gid, short, r))
        else:
            ok.append((gid, r))
    return {'ok': ok, 'fail': fail, 'need': need,
            'unevaluated': need.get('unevaluated') or [],
            'worst': {k: need[k] for k in ('physique', 'cunning', 'spirit')}}


if __name__ == '__main__':
    import sys
    ids = sys.argv[1:] or ['it12230', 'it1351']
    for gid in ids:
        r = req(gid)
        print('%-9s ilvl=%-3s %-9s set=%-5s 体格=%-5s 狡诈=%-4s 精神=%-5s [%s]'
              % (gid, r['ilvl'], r['cat'], r['set'], r['physique'] or '-',
                 r['cunning'] or '-', r['spirit'] or '-', r['confidence']))
