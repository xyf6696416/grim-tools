# -*- coding: utf-8 -*-
"""物品技能与**触发关系** —— 「这个技能能不能触发那件衣服 / 镶嵌物上的技能」。

用户口径（2026-09-20 原文）：
  「…以及该技能**能不能触发其他技能** —— 一些衣服上的技能或者是镶嵌物的。
   尽可能与游戏保持一致。」

数据链（全部来自离线库，`E:\\Grim Tools\\resources\\app.asar`）：

    allItems[gid].itemSkillName        → "skXXXX"    物品/镶嵌/附魔**授予的技能**
    allItems[gid].itemSkillAutoController → "ctNN"   **触发控制器**（无此字段 = 常驻）
    allItems[gid].itemSkillLevelEq     → 等级算式   2 / "itemLevel/4+1"
    itemSkillControllers["ctNN"]       → {chanceToRun, targetType, triggerType, triggerParam}
    itemSkills["skXXXX"]               → {l: 模板类型, skillDisplayName, skillChanceWeight…}

★ 判据来自 **`triggerType` 全集**（离线库 70 条控制器里出现的全部取值）：

    AttackEnemy      攻击敌人（命中即 roll）      ← **本循环的伤害技能能触发它**
    AttackEnemyCrit  攻击**暴击**时               ← 只有暴击才 roll（暴击率 0% ⇒ 永不触发）
    HitByEnemy       被敌人击中时                 ← 挨打；与用哪个技能**无关**
    HitByMelee       被**近战**击中时             ← 同上
    Block            格挡成功时                   ← 需盾牌
    OnKill           击杀敌人时                   ← 与技能无关（DoT 跳死也算）
    LowHealth        生命低于 `triggerParam`% 时   ← 与技能无关
    （无 controller） 常驻 —— 不需要触发，直接生效

★ `Skill_WPAttack_*` 是**武器池技能（WPS）**：它不靠 `chanceToRun`，而是按
  `skillChanceWeight`（**权重**）从默认攻击的 100 里扣 —— 见 `gd/rotation.py`
  的 WPS 段与 `docs/pitfalls.md`。**装备授予的 WPS 也属于武器池**（本期发现
  它们此前从未进池，见 `wps_gap()`）。
"""
from __future__ import annotations

import os

__all__ = ['TRIGGER_ZH', 'ATTACK_TRIGGERS', 'RESULT_ZH', 'controller_of',
           'skill_of', 'item_procs', 'collect', 'wps_gap', 'KIND_ZH',
           'wps_of_item', 'wps_pool', 'weapon_state', 'weapon_state_of',
           'weapon_type_ok', 'weapon_verdict',
           'WEAPON_CLASS_1H', 'WEAPON_CLASS_2H', 'SHIELD_CLASS']


# ---------------------------------------------------------------- 口径表

TRIGGER_ZH = {
    'AttackEnemy': '攻击敌人时',
    'AttackEnemyCrit': '攻击**暴击**时',
    'HitByEnemy': '被敌人击中时',
    'HitByMelee': '被近战击中时',
    'Block': '格挡成功时',
    'OnKill': '击杀敌人时',
    'LowHealth': '生命过低时',
}

# 由**我方攻击**触发 —— 「本循环的伤害技能能不能触发它」才有意义
ATTACK_TRIGGERS = ('AttackEnemy', 'AttackEnemyCrit')
# 由挨打 / 格挡 / 低血 / 击杀触发 —— **与用哪个技能无关**（问「能不能触发」是伪问题）
DEFENSIVE_TRIGGERS = ('HitByEnemy', 'HitByMelee', 'Block', 'OnKill', 'LowHealth')

# 物品技能的模板类别（`itemSkills[sk].l`）→ 中文（精确命中）
KIND_ZH = {
    'Skill_WPAttack_BasicAttack': '武器池技能（WPS）',
    'Skill_Modifier': '技能改造（不触发，直接改某个技能）',
    'Skill_Passive': '常驻被动',
    'Skill_PassiveOnLifeBuffSelf': '常驻被动（生命触发增益）',
    'Skill_BuffSelfToggled': '常驻开关光环',
    'Skill_BuffRadiusToggled': '常驻开关光环（半径）',
    'Skill_BuffRadius': '常驻光环',
    'Skill_GiveBonus': '常驻属性加成',
}


def kind_zh_of(l):
    """`l`（模板类别）→ 中文（**兜底按前缀归类**，不认识的类别原样返回）。

    为什么必须兜底：`itemSkills` 全表 4268 条、模板类别 40+ 种，
    逐个列中文是维护地狱；但报告里直接印 `Skill_BuffSelfDuration`
    对用户毫无意义。⇒ 按前缀归到「常驻 / 攻击 / 召唤 / 改造」四类，
    精确名（如 WPS）走 `KIND_ZH`。
    """
    if not l:
        return '—'
    if l in KIND_ZH:
        return KIND_ZH[l]
    s = str(l)
    if s.startswith(('Skill_Buff', 'Skill_Passive', 'Skill_Give')):
        return '常驻类（Buff / 被动）'
    if s.startswith(('Skill_Attack', 'Skill_WPAttack')):
        return '攻击类'
    if s.startswith('Skill_SpawnPet'):
        return '召唤物'
    if s.startswith('Skill_Modifier'):
        return '技能改造'
    return s

# 「本技能能否触发它」的三种裁决
YES = '可以'
NO = '不可以'
NA = '与技能无关'


def _is_wps(l):
    return str(l or '').startswith('Skill_WPAttack')


def _is_modifier(l):
    return str(l or '') == 'Skill_Modifier'


def _skill_level(eq, item):
    """`itemSkillLevelEq` → 技能等级。

    `2` / `[2]` 直接就是等级；`"itemLevel/4+1"` 之类的算式用物品的 `itemLevel`
    代入（GT 的 `itemLevel` 字段 = `allItems[gid]['itemLevel']`）。算不出来给 `None`
    —— **宁可不认，不认错**。
    """
    if eq is None:
        return None
    if isinstance(eq, (list, tuple)):
        eq = eq[0] if eq else None
    if isinstance(eq, (int, float)):
        return int(eq)
    s = str(eq).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        pass
    lvl = item.get('itemLevel')
    if not isinstance(lvl, (int, float)):
        return None
    try:
        expr = s.replace('itemLevel', str(int(lvl)))
        if not all(c in '0123456789+-*/() .' for c in expr):
            return None
        return int(eval(expr, {'__builtins__': {}}, {}))       # noqa: S307
    except Exception:                                          # noqa: BLE001
        return None


def _at(vals, lv):
    """逐级取值（标量 / 单元素列表 / 逐级数组），与 `gd.rotation._at` 同口径。"""
    if vals is None:
        return 0.0
    if isinstance(vals, (int, float)):
        return float(vals)
    if isinstance(vals, (list, tuple)):
        if not vals:
            return 0.0
        if len(vals) == 1:
            v = vals[0]
            return float(v) if isinstance(v, (int, float)) else 0.0
        i = max(0, min(int(lv or 1) - 1, len(vals) - 1))
        v = vals[i]
        return float(v) if isinstance(v, (int, float)) else 0.0
    return 0.0


# ---------------------------------------------------------------- 单件 → proc

def controller_of(db, item):
    """物品 → `(ctNN, 控制器 dict 或 None)`。"""
    ct = item.get('itemSkillAutoController')
    if isinstance(ct, (list, tuple)):
        ct = ct[0] if ct else None
    if not ct:
        return None, None
    return ct, (db.raw.get('itemSkillControllers') or {}).get(ct)


def skill_of(db, item):
    """物品 → `(skXXXX, 技能记录 或 {})`。"""
    sk = item.get('itemSkillName')
    if isinstance(sk, (list, tuple)):
        sk = sk[0] if sk else None
    if not sk:
        return None, {}
    return sk, (db.skills.get(sk) or {})


def item_procs(db, items, gid, label='', slot=''):
    """一件物品 → 它带来的技能条目（0 或 1 条，物品只有一个 `itemSkillName`）。

    返回 `[{gid, slot, label, sk, name, kind, ctl, trigger, chance, target,
             level, is_wps, is_modifier}, …]`
    """
    item = items.get(gid) or {}
    sk, rec = skill_of(db, item)
    if not sk:
        return []
    ct, cd = controller_of(db, item)
    l = rec.get('l')
    # 译名回退链：skillDisplayName → name → skillBaseDescription（少数老技能没前两个）
    tag = rec.get('skillDisplayName') or rec.get('name') or ''
    nm = db.l10n.get(tag) or tag or sk
    if isinstance(nm, str) and nm.startswith('tag'):
        _alt = db.l10n.get(rec.get('skillBaseDescription') or '') or ''
        if _alt and not str(_alt).startswith('tag'):
            nm = str(_alt)[:28]
    if not nm or nm == sk:
        nm = '%s（离线库无本地化名）' % sk
    lv = _skill_level(item.get('itemSkillLevelEq'), item)
    trig = (cd or {}).get('triggerType')
    return [{
        'gid': gid, 'slot': slot, 'label': label, 'sk': sk,
        'name': str(nm), 'tag': tag, 'kind': l,
        'kind_zh': kind_zh_of(l),
        'ctl': ct, 'trigger': trig,
        'trigger_zh': TRIGGER_ZH.get(trig, trig or '常驻'),
        'chance': (cd or {}).get('chanceToRun'),
        'target': (cd or {}).get('targetType'),
        'param': (cd or {}).get('triggerParam'),
        'level': lv,
        'weight': _at(rec.get('skillChanceWeight'), lv) if _is_wps(l) else None,
        'weapon_pct': _at(rec.get('weaponDamagePct'), lv) if _is_wps(l) else None,
        'is_wps': _is_wps(l), 'is_modifier': _is_modifier(l),
    }]


def collect(db, items, plan, item_skills=None):
    """整个方案 → 全部物品技能条目（按槽位顺序）。

    `item_skills`：`gd.dps.load_char` 返回的同名结构（**当前装备**的实际授予表），
    传了就与方案 JSON 交叉核对（两者应一致；不一致会登记出来）。
    """
    out = []
    for slot, gids in (plan or {}).items():
        for gid in (gids or []):
            if not gid:
                continue
            it = items.get(gid) or {}
            lbl = db.name(gid) if hasattr(db, 'name') else ''
            out.extend(item_procs(db, items, gid, label=lbl, slot=slot))
    return out


# ---------------------------------------------------------------- 裁决

def verdict(entry, skill_is_attack=True, crit_chance=0.0, has_shield=False):
    """**这个技能能不能触发它** —— 返回 `(裁决, 说明)`。

    `skill_is_attack`：被问的那个技能是否**造成伤害的攻击/法术**（DoT 的后续跳不算）
    `crit_chance`：我方暴击率（%）—— `AttackEnemyCrit` 只有暴击才 roll
    `has_shield`：是否持盾 —— `Block` 类需要
    """
    if entry.get('is_modifier'):
        return NA, '技能改造：直接改某个技能，不存在「触发」'
    if entry.get('is_wps'):
        return NA, '武器池技能（WPS）：由**带武器伤害的默认攻击**按权重掷骰，不靠几率'
    t = entry.get('trigger')
    if not t:
        return NA, '常驻：无需触发，装备上就生效'
    if t in ATTACK_TRIGGERS:
        if not skill_is_attack:
            return NO, '该技能不产生伤害，无法触发「攻击敌人」类'
        if t == 'AttackEnemyCrit' and float(crit_chance or 0.0) <= 0.0:
            return NO, '需要**暴击**才有 roll —— 本循环暴击率 0%'
        return YES, '每次命中都会 roll 一次（DoT 的后续跳不触发）'
    if t == 'Block':
        return (YES if has_shield else NA), ('持盾：格挡成功时 roll'
                                             if has_shield else '未持盾 ⇒ 不会发生')
    return NA, '由**挨打 / 击杀 / 低血**触发 —— 与用哪个技能无关'


def wps_gap(db, items, plan, base_hits, st=None):
    """**历史缺口检测**：装备授予的武器池技能（WPS）有没有进循环。

    ⚠ 2026-09-20 已**修复**（用户批准）——修复方式见 `wps_pool()` 与
    `gd/rotation.py::final_report(item_wps=…)`。本函数保留为**体检器**：
    传入的 `base_hits` 仍未包含这些 WPS 时返回非空（用于自检 / 回归对照）。

    `st`（2026-09-20 **新增**，配合武器闸门）：武器构成。传了就对每条候选先跑
    `weapon_verdict()`，**跳过游戏里不会出现的 WPS**（如双持角色身上的盾牌战技
    `sk3683`）—— 否则闸门把非法项挡在池外后，本函数会**误报成缺口**（假阳性）。
    不传 ⇒ 旧语义（不判武器），**零漂移**。

    背景（当初的实测）：`gd/rotation.final_report` 的武器池只从 `levels`
    （存档技能表 / 形态 `core_skills`）收集，而**装备授予的 WPS 不在那张表里**
    —— 实测 `it798`（勋章）的「恐狼之爪」权重 12、
    `it1516`（圣物）的「毁伤」权重 25，两者合计 37 却算出 `weight_total = 0`。
    """
    got = []
    for e in collect(db, items, plan):
        if not e.get('is_wps'):
            continue
        if st is not None:
            _ok, _ = weapon_verdict((db.skills.get(e.get('sk')) or {}), st)
            if not _ok:
                continue
        got.append(e)
    if not got:
        return {'items': [], 'missing': [], 'weight_lost': 0.0}
    from . import skillprov as _SP
    # ⚠ 判据必须**先把 `skXXXX` 翻成记录路径**再比 —— `e['sk']` 是技能组 id
    #   （`sk325`），而 `base_hits` 的键是记录路径（`records/skills/itemskills/…dbr`），
    #   拿两者直接比会**永远判「缺失」**（旧实现的漏洞：修好之后它还是报缺口）。
    inside = {str(k) for k in (base_hits or {})}
    inside |= {os.path.basename(str(k)) for k in (base_hits or {})}

    def _in(e):
        for _r in (_SP.sk_to_records(e['sk']) or ()):
            if _r in inside or os.path.basename(_r) in inside:
                return True
        return False

    missing = [e for e in got if not _in(e)]
    return {'items': got, 'missing': missing,
            'weight_lost': round(sum(float(e.get('weight') or 0.0) for e in missing), 1)}


# ------------------------------------------- 武器构成 → WPS 武器约束闸门（#61）

# 物品库里的武器类别码（`gd.db.OfflineDB.item_class()`，全库实测 13 类，见 `docs/pitfalls.md` #61）：
#   单手近战 Axe / Dagger / Mace / Scepter / Sword ｜ 单手远程 Ranged1h
#   双手 Melee *2h / Ranged2h ｜ 盾 WeaponArmor_Shield ｜ 副手法器 WeaponArmor_Offhand
SHIELD_CLASS = 'WeaponArmor_Shield'
WEAPON_CLASS_1H = ('WeaponMelee_Axe', 'WeaponMelee_Dagger', 'WeaponMelee_Mace',
                   'WeaponMelee_Scepter', 'WeaponMelee_Sword',
                   'WeaponHunting_Ranged1h')
WEAPON_CLASS_2H = ('WeaponMelee_Axe2h', 'WeaponMelee_Mace2h', 'WeaponMelee_Sword2h',
                   'WeaponMelee_Spear2h', 'WeaponHunting_Ranged2h')


_WEAPON_ST_MEMO: dict = {}


def weapon_state(gids, db=None):
    """装备 gid 列表 → 角色的**武器构成**（判 WPS 武器约束的输入）。

    返回 `{'kinds', 'has_shield', 'dual_wield', 'two_handed', 'n_weapons'}`。

    ★ 为什么从 `gids` 推而不是让调用方传：`gd.dps.load_char()` 的 `base_gids`
      已覆盖「存档」与「override（优化器换装 / `--with-weapon`）」**两条路**
      ⇒ 在源头推断可让全部调用点零改动地拿到正确口径。

    ★ 记忆化（2026-09-22）：本函数现在被**每条 DPS 路径**调用（`load_char` 与六个
      调用点），而它内部要 `DB.load()` + 每 gid 一次 `item_class()`。
      按 `tuple(gids)` 记忆并**返回副本**（调用方只读，但共享可变 dict 是隐患）。
    """
    key = tuple(g for g in (gids or ()) if g)
    hit = _WEAPON_ST_MEMO.get(key, 0)
    if hit != 0:
        return dict(hit) if hit else None
    out = _weapon_state_calc(key, db)
    _WEAPON_ST_MEMO[key] = out
    return dict(out) if out else None


def _weapon_state_calc(gids, db=None):
    """`weapon_state` 的**无记忆化**实现（只返回 `None` 或 dict）。"""
    from . import DB
    _db = db if db is not None else DB.load()
    kinds = []
    for g in (gids or ()):
        if not g:
            continue
        try:
            k = _db.item_class(g) or ''
        except Exception:                                        # noqa: BLE001
            k = ''
        if str(k).startswith('Weapon'):
            kinds.append(str(k))
    has_shield = any(k == SHIELD_CLASS for k in kinds)
    two_handed = any(k in WEAPON_CLASS_2H for k in kinds)
    n_1h = sum(1 for k in kinds if k in WEAPON_CLASS_1H)
    # ★ **看不到武器 ⇒ 返回 `None`（未知，不判）**：调用方常常只给 12 个装备槽的
    #   方案（`plan` 里没有武器）⇒ 此时若按「无双持」处理，会把 `dualWieldOnly`
    #   的 WPS（毁伤 / 恐狼之爪 / 夜刃武器池）**整批误杀**。
    #   「宁可不认，也不认错」—— 不知道就不门控。
    if not kinds:
        return None
    # 双持 = **两把单手武器**（近战或远程），且不是剑盾 / 双手
    dual = (n_1h >= 2) and not has_shield and not two_handed
    return {'kinds': tuple(kinds), 'has_shield': has_shield,
            'dual_wield': dual, 'two_handed': two_handed, 'n_weapons': len(kinds)}


def weapon_verdict(entry, st):
    """`itemSkills[sk]` 的武器约束 vs 角色武器构成 → `(ok, 中文原因)`。

    判据只取**离线库 `itemSkills` 表里能明确解释**的两族（宁可不认，也不认错）：

      · `Shield: 1`        ⇒ **必须持盾**。全库 28 条，其中模板为
        `Skill_WPAttack_BasicAttack` 的只有 2 条：`sk3699` 碎岩猛击（士兵盾击）、
        `sk3683` **混乱打击** —— 两个都是盾击类，双持 / 双手下游戏里不出现。
      · `dualWieldOnly: 1` ⇒ **必须双持近战**。全库 17 条（`sk519` 毁伤、
        `sk325` 恐狼之爪、夜刃 `sk1237~1240` 等）。**例外**：同时带
        `dualRangedOnly` / `dualRangedOrAllRangedOnly` / `dualRangedOrRanged2hOnly`
        的是 **OR** 语义（如 `sk3625` 战争兵器 = 双持远近皆可）⇒ 放行，不门控。

    其余武器类型白名单（`Sword` / `Axe2h` / … 顶层键）**登记不门控** ——
    规则细、误判风险高，且 Sam 当前不受影响。
    `st` 为空 ⇒ 不判（`ok=True`），保持「不传 = 零漂移」。
    """
    if not st:
        return True, '未提供武器构成 ⇒ 未校验'
    kinds = '、'.join(st.get('kinds') or ()) or '（无武器）'
    if (entry or {}).get('Shield') and not st.get('has_shield'):
        return False, '盾牌战技，但当前未持盾（%s）' % kinds
    if (entry or {}).get('dualWieldOnly'):
        ranged_alt = ((entry or {}).get('dualRangedOnly')
                      or (entry or {}).get('dualRangedOrAllRangedOnly')
                      or (entry or {}).get('dualRangedOrRanged2hOnly'))
        if not ranged_alt and not st.get('dual_wield'):
            return False, '双持专属战技，但当前不是双持（%s）' % kinds
    return True, '武器构成匹配（%s）' % kinds


# ------------------------------------------------- 武器类型白名单（星座 / 专精 / 物品技能通用）
# ★★ 2026-09-21 新增。技能的**武器类型白名单**在记录顶层是**多个布尔键的 OR**：
#   · 狂战士（`tier2_25a~f`）= `Axe:1, Axe2h:1, Spear2h:1` ⇒「需要斧或矛」
#     （对应 l10n `tagDevotion_RequiresAxeSpear` = 「需要斧或矛。」）
#   · 纳丹的刀刃（`tier2_23a/f`）= `Sword:1, Sword2h:1` ｜ 狐狸（`tier2_24a~f`）= `Mace/Mace2h`
#   · 盾墙一类 = `Shield:1`
#
#   旧实现（`weapon_verdict`）**只判 `Shield` 与 `dualWieldOnly`**，注释里写着
#   「其余武器类型白名单**登记不门控**……且 **Sam 当前不受影响**」—— 那句话是错的：
#   Sam 双持**剑**，而狂战士三颗已点星位要**斧/矛** ⇒ 那 3 点一分钱不产生，
#   模型却算成 **+4.28%** 虚增。
#
#   数据来源：`tools/extract_calc_skills.py` 从 `calc.js` 抽（2026-09-21 起，
#   修复了「一个 tag 对多条记录 ⇒ 判歧义跳过」⇒ 星座星位整类拿不到这些字段）。
WEAPON_TYPE_CLASS = {
    'Axe': ('WeaponMelee_Axe',),
    'Axe2h': ('WeaponMelee_Axe2h',),
    'Sword': ('WeaponMelee_Sword',),
    'Sword2h': ('WeaponMelee_Sword2h',),
    'Mace': ('WeaponMelee_Mace',),
    'Mace2h': ('WeaponMelee_Mace2h',),
    'Dagger': ('WeaponMelee_Dagger',),
    'Scepter': ('WeaponMelee_Scepter',),
    'Spear2h': ('WeaponMelee_Spear2h',),
    'Shield': ('WeaponArmor_Shield',),
    'Gun': ('WeaponHunting_Ranged1h',),
    'Crossbow': ('WeaponHunting_Ranged2h',),
}

# 每个武器限制键 → 中文（报告用）
WEAPON_TYPE_ZH = {
    'Axe': '斧', 'Axe2h': '双手斧', 'Sword': '剑', 'Sword2h': '双手剑',
    'Mace': '锤', 'Mace2h': '双手锤', 'Dagger': '匕首', 'Scepter': '权杖',
    'Spear2h': '双手矛', 'Shield': '盾', 'Gun': '手枪', 'Crossbow': '弩',
}
# 物品 class 字符串 → 中文（`WeaponMelee_Axe` → `斧`）
WEAPON_CLASS_ZH = {_c: WEAPON_TYPE_ZH[_k]
                   for _k, _cs in WEAPON_TYPE_CLASS.items() for _c in _cs}


def weapon_state_of(char, db=None):
    """**统一入口**：角色字典 → 武器构成（判武器类型门控用）。

    ★★ 为什么要有一个统一入口（2026-09-22）：武器门控此前只在 `plan_dps.dps_of`
      一条路上接了，其余（`gd dps` CLI / `tune_devotion` / `tune_skills` /
      `eval_build_variants` / `gt_regress` / `defense_audit`）**全部零门控** ——
      同一个角色在不同工具里会得到不同的数字，且没有任何提示。

    取值顺序：
      ① `char['weapon_st']`（`gd.dps.load_char()` 已经算好并存下 —— 首选，零额外开销）
      ② 从 `char['base_gids']` 现推（兼容旧字典 / 局部构造的 char）

    `char` 为空 / 看不到武器 ⇒ `None`（= 不门控，零漂移）。
    """
    if not char:
        return None
    st = char.get('weapon_st')
    if st:
        return st
    return weapon_state(char.get('base_gids'), db or char.get('db'))


def weapon_type_ok(entry, st):
    """技能记录的**武器类型白名单** vs 角色武器构成 → `(ok, 中文原因)`。

    判据：记录上任何白名单键为真 ⇒ 当前武器**命中其中一个即可**
    （游戏里这些键是 **OR** 语义：「斧或矛」＝有斧 **或** 有矛）。

    `st` 为空 / 记录无白名单键 ⇒ `ok=True`，保持「不传 = 零漂移」。
    """
    if not st:
        return True, '未提供武器构成 ⇒ 未校验'
    want = [k for k in WEAPON_TYPE_CLASS if (entry or {}).get(k)]
    if not want:
        return True, '无武器类型限制'
    have = set(st.get('kinds') or ())
    allow = set()
    for k in want:
        allow |= set(WEAPON_TYPE_CLASS[k])
    if have & allow:
        return True, '武器匹配（%s）' % '、'.join(sorted(
            WEAPON_TYPE_ZH.get(k, k) for k in (have & allow)))
    return False, '需要 %s，当前 %s' % (
        '／'.join(WEAPON_TYPE_ZH.get(k, k) for k in want),
        '、'.join(WEAPON_CLASS_ZH.get(k, k) for k in sorted(have)) or '无武器')


# ------------------------------------------------- 装备授予的 WPS → 武器池注入

_WPS_GID_MEMO: dict = {}


def wps_of_item(db, items, gid):
    """一件物品 → 它授予的**武器池技能（WPS）**注入项（不是 WPS ⇒ `None`）。

    返回 `{'gid','sk','rec','level','weight','weapon_pct','name'}`。

    ★★ 为什么必须在这里做（两层坑，缺一不可）
      ① **记录路径**：`itemSkillName` 是 `skXXXX`，而 `gd/rotation.py` 的技能表
         用的是 `records/skills/…dbr` 路径 ⇒ 必须经 `gd/skillprov.py` 的
         tag→记录索引翻译（`sk_to_records`）。
      ② **权重**：`skillChanceWeight` **不在 `.dbr` 记录里、也不在 `skills.json`**，
         只存在于离线库的 `itemSkills` 表（键 `skXXXX`）⇒ 就算把记录塞进技能表，
         权重仍算成 `0`，会被判成「又一个默认攻击候选」而不是武器池技能。
         这里连同权重一起取出来交给调用方注入。

    `db` 必须是**离线库**（`gd.db.OfflineDB`，有 `.skills`）；`items` = `gear.load_items()`。
    """
    if gid in _WPS_GID_MEMO:
        return _WPS_GID_MEMO[gid]
    out = None
    it = (items or {}).get(gid) or {}
    sk, rec = skill_of(db, it)
    if sk and _is_wps(rec.get('l')):
        from . import skillprov as _SP
        lv = int(_skill_level(it.get('itemSkillLevelEq'), it) or 1)
        cand = list(_SP.sk_to_records(sk))
        # 记录候选里挑「本体」（`template` 是 `Skill_WPAttack_*` 的那条）；
        # 挑不出就用第一条（`sk_to_records` 的顺序是本体在前）。
        pick = ''
        try:
            from . import rotation as _ROT
            _sj = _ROT._load('skills.json', {}) or {}
            for _r in cand:
                if _is_wps((_sj.get(_r) or {}).get('template')):
                    pick = _r
                    break
        except Exception:                                        # noqa: BLE001
            pick = ''
        pick = pick or (cand[0] if cand else '')
        wt = _at(rec.get('skillChanceWeight'), lv)
        if pick and wt > 0:
            _tag = (rec.get('skillDisplayName') or rec.get('name') or '')
            _nm = db.l10n.get(_tag) or _tag or sk
            if not isinstance(_nm, str) or _nm.startswith('tag'):
                _nm = '%s（离线库无本地化名）' % sk
            out = {'gid': gid, 'sk': sk, 'rec': pick, 'level': lv,
                   'weight': float(wt),
                   'weapon_pct': _at(rec.get('weaponDamagePct'), lv),
                   'name': str(_nm)}
    _WPS_GID_MEMO[gid] = out
    return out


def wps_pool(gids, items=None, st=None):
    """★ 装备 gid 列表 → **装备授予的武器池技能（WPS）**注入表。

    直接喂给 `gd/rotation.py::final_report(item_wps=…)`。
    内部按 gid memo（`_WPS_GID_MEMO`）⇒ 优化器一轮几万次调用也只真算一次。

    `gids` 用 `gd.dps.load_char()` 的 `base_gids`（**存档与 override 两条路都覆盖**）。

    ★★ **武器约束闸门（2026-09-20 起默认开启，见 `docs/pitfalls.md` #61）**：
    `st=None` 时由 `weapon_state(gids)` **自动推断**武器构成，并用
    `weapon_verdict()` 剔除游戏里根本不会出现的 WPS（实例：Sam 双持却把勋章
    授予的**盾牌战技「混乱打击」**塞进池子，面板虚高 11.3%）。
    返回项额外带 `weapon_ok` / `weapon_reason`，便于报告与诊断。
    显式传 `st={}` （空 dict）或 `st=False` ⇒ 整段跳过，退回旧口径。
    """
    from . import DB
    from . import gear as _G
    _db = DB.load()
    if items is None:
        items = _G.load_items()
    _st = weapon_state(gids, _db) if st is None else st
    out = []
    for gid in (gids or ()):
        if not gid:
            continue
        e = wps_of_item(_db, items, gid)
        if not e:
            continue
        _ok, _why = weapon_verdict((_db.skills.get(e.get('sk')) or {}), _st)
        if _ok:
            out.append(dict(e, weapon_ok=True, weapon_reason=_why))
    return out
