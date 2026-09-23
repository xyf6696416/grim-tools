"""伤害来源与转化链 —— 官方 Step 2（拆解）与 Step 3（转化 / 加成）。

★ 为什么必须换成「来源列表」而不是「按类型合并的 dict」：
  官方规则是 **每个伤害源只能被转化一次**（"it is only applied once"），
  而「按类型合并」之后结构上无法表达「这一半已经被技能转化过、那一半还没有」。
  实测后果：护甲穿透（第④步）会把**已被技能转走的物理**再转一次（双转）。

官方转化顺序（逐字）：
    Base Skill > Skill Modifiers > Conversion on Modifiers or Transmuter
              > Global Conversion on Equipment and Buffs > % damage on Equipment, Auras and Passives
以及两条容易漏的规则：
  · **目标类型没有对应 DoT 时不转**（例：火 → 混乱，燃烧保持不变）
  · **护甲穿透排最后**，作用于**整击残余物理**（不限武器），
    门槛是「该技能有武器伤害%」（`weaponDamagePct > 0`）
"""
import re
from dataclasses import dataclass

# ---------------------------------------------------------------- 类型表

# DoT 类型 ↔ 它的直接伤害类型
DOT_OF = {
    'fire': 'burn', 'cold': 'frostburn', 'lightning': 'electrocute',
    'poison': 'poisondot', 'acid': 'poisondot', 'physical': 'trauma',
    'bleeding': 'bleeding', 'vitality': 'decay',
}
DOT_TYPES = frozenset(set(DOT_OF.values()) | {'bleeding'})
_DIRECT_OF = {}
for _d, _s in DOT_OF.items():
    _DIRECT_OF.setdefault(_s, _d)

ELEMENTAL = ('fire', 'cold', 'lightning')

# dbr 字段名 → 我们的类型名（GD 里活力写作 `Life`）
FIELD_TYPE = {'life': 'vitality', 'acid': 'acid'}
# `offensiveSlowXxx` 的类型名与直接伤害不同名（SlowCold = 霜燃，不是冰冷）
SLOW_TYPE = {'bleeding': 'bleeding', 'fire': 'burn', 'cold': 'frostburn',
             'lightning': 'electrocute', 'poison': 'poisondot',
             'vitality': 'decay', 'physical': 'trauma',
             'aether': 'aether', 'chaos': 'chaos'}
ALL_TYPES = frozenset({
    'physical', 'pierce', 'fire', 'cold', 'lightning', 'poison', 'acid',
    'vitality', 'aether', 'chaos', 'bleeding',
    'burn', 'frostburn', 'electrocute', 'decay', 'trauma', 'poisondot',
})


def direct_of(t):
    """DoT 类型 → 它的直接类型（非 DoT 原样返回）"""
    return _DIRECT_OF.get(t, t)


def is_dot(t):
    return t in DOT_TYPES


# ---------------------------------------------------------------- 来源

@dataclass
class Source:
    """一个**独立结算的伤害源**（官方 Step 2 的「类型 × 来源」单元）。

    `origin`：`weapon` / `skill` / `aura` / `gear` / `devotion` / `mod`
    `dur`：DoT 持续时间（秒）；0 = 直接伤害
    `locked`：是否**已被转化过一次**（官方「只转一次」）
    """
    type: str
    lo: float
    hi: float
    origin: str = 'skill'
    dur: float = 0.0
    locked: bool = False

    @property
    def is_dot(self):
        return self.type in DOT_TYPES

    @property
    def avg(self):
        return (self.lo + self.hi) / 2.0

    def scaled(self, k):
        return Source(self.type, self.lo * k, self.hi * k, self.origin, self.dur, self.locked)

    def copy(self):
        return Source(self.type, self.lo, self.hi, self.origin, self.dur, self.locked)


def from_map(m, origin, is_dot=False, dur=0.0):
    """`{type: [lo, hi]}` → `[Source…]`（`{type: v}` 单值也接受）"""
    out = []
    for t, v in (m or {}).items():
        lo, hi = (v, v) if isinstance(v, (int, float)) else (v[0], v[1])
        if not lo and not hi:
            continue
        d = float(dur) if is_dot else 0.0
        if is_dot and t in DOT_TYPES:
            d = float(dur)
        out.append(Source(t, float(lo), float(hi), origin, d))
    return out


def to_map(sources):
    """`[Source…]` → `{type: [lo, hi]}`（兼容旧接口）"""
    out = {}
    for s in sources:
        e = out.setdefault(s.type, [0.0, 0.0])
        e[0] += s.lo
        e[1] += s.hi
    return out


# ---------------------------------------------------------------- Step 3.1 转化

def _targets(src_type, out_type):
    """这条转化规则对该来源是否有效；返回目标类型元组（可能空）。

    · `elemental` → 均分给 火/冰/电
    · **DoT 目标没有对应 DoT 类型 ⇒ 不转**（官方：火→混乱，燃烧保持不变）
    """
    outs = ELEMENTAL if out_type == 'elemental' else (out_type,)
    if src_type in DOT_TYPES:
        got = tuple(DOT_OF[o] for o in outs if o in DOT_OF)
    else:
        got = tuple(outs)
    if not got:
        return ()
    # 每个目标均分该规则的百分比
    k = len(outs)
    return tuple((t, 1.0 / k) for t in got)


def convert(sources, steps, pierce_ratio=0.0, has_weapon_damage=False):
    """按**官方顺序**依次应用转化步骤，最后做护甲穿透。

    `steps`：按顺序的转化列表，每步是 `[(in_type, out_type, pct), …]`
      · 第 1 步 = 技能自带 + modifier/transmuter 转化（**技能专属**）
      · 第 2 步 = 装备 / buff / 星座的全局转化
    `pierce_ratio`：武器的护甲穿透比（0..1）
    `has_weapon_damage`：该技能是否有武器伤害%（官方门槛）

    返回新的来源列表（不改原列表）。

    ★★ 2026-09-22 性能：每一步的规则先编译成「**来源类型 → (目标表, 总量)**」的
      查表（`_rel_tbl`，**按步骤签名全局缓存**），再逐来源查表应用。

      为什么这一步值得做（实测，`tools/_bench_conv.py`）：
        单次评估 `convert()` 被调 **76 次**、含 **152 个步骤实例**，
        但**唯一步骤签名只有 1 个**（同一次 `_make_hit` 里主路径 + 5 组分来源账本
        用的是**同一份**转化配置；装备搜索期间全局转化更是不变）
        ⇒ 理论复用 **456×**。
        旧实现每调一次都要对**每个来源 × 每条规则**重算 `_conv_matches` + `_targets`
        （实测 1,684 + 316 次/评估）；现在是**每种来源类型每步只算一次**。

      ⚠ 算术逐字保留（`keep` / `share` / `scaled` / `Source(...)` 的参数与顺序都不动），
        只是为了消除重复计算 ⇒ **数值零漂移**（自检 `[64]` 用 fuzz 对拍旧实现）。
    """
    cur = [s.copy() for s in sources]
    for step in (steps or []):
        if not step:
            continue
        tbl = _rel_tbl(step)
        nxt = []
        for s in cur:
            nxt.extend(_apply_one(s, step, tbl))
        cur = nxt
    if pierce_ratio and has_weapon_damage:
        cur = _apply_pierce(cur, pierce_ratio)
    return cur


# ------------------------------------------------ 转化规则 → 来源类型查表
_REL_TBL_CAP = 4096
_REL_TBL = {}                      # 步骤签名 → {来源类型: (rel 元组, 总量)}，`()` = 不转


def _build_rel_one(src_type, convs):
    """**一种**来源类型在给定规则下的 (目标表, 总量)；无转化时返回 `()`。

    这里就是旧 `_apply_one` 里那段「逐规则匹配 + 展开目标」的循环 —— 原样搬出来，
    因为它只取决于 `(src_type, convs)`，与来源的 `lo/hi/origin` 无关。
    """
    base = _DIRECT_OF.get(src_type, src_type)
    rel = []
    for in_t, out_t, pct in convs:
        if not _conv_matches(in_t, base) or not pct or pct <= 0:
            continue
        for tgt, share in _targets(src_type, out_t):
            rel.append((tgt, float(pct) * share))
    if not rel:
        return ()
    return (tuple(rel), sum(p for _t, p in rel))


def _build_rel_tbl(convs):
    """对**全部**已知伤害类型预编译（17 种 × 规则数）。"""
    return {st: _build_rel_one(st, convs) for st in ALL_TYPES}


def _step_sig(convs):
    """步骤签名（可哈希）；规则里混进不可哈希的东西时返回 `None` ⇒ 调用方不缓存。"""
    try:
        return tuple((a, b, float(c)) for a, b, c in convs)
    except Exception:                                        # noqa: BLE001
        return None


def _rel_tbl(convs):
    """取该步骤的查表（**全局缓存**，按签名）。命中率极高（同一次评估里只有 1 个签名）。"""
    sig = _step_sig(convs)
    if sig is None:
        return _build_rel_tbl(convs)                   # 零风险回退：不缓存、照旧算
    t = _REL_TBL.get(sig)
    if t is None:
        t = _REL_TBL[sig] = _build_rel_tbl(convs)
        if len(_REL_TBL) > _REL_TBL_CAP:               # 上限守卫（每项 ~17 条）
            _REL_TBL.clear()
    return t


def _conv_matches(in_t, base):
    """规则里的**源类型** `in_t` 是否作用于该来源（`base` = 来源的直接类型）。

    ★★ `Elemental` 是**元素三合一**，既能当目标也能当**源**：
       「元素伤害转化为穿刺 50%」= 火/冰/电**各**转 50%。
       旧实现只处理了它作**目标**（`out_type == 'elemental'` → 均分给火冰电），
       作**源**时 `in_t != base`（'Elemental' 永远不等于 'cold'）⇒ 规则**静默失效**。
       实测影响面：**165 件物品 / 36 条技能记录**带 `Elemental -> X` 转化
       （元素→活力 50、元素→物理 43、元素→穿刺 25…）全部白丢。
       注：DoT 来源（霜燃/燃烧/电击）的 `direct_of()` 已回落到火/冰/电，
       所以它们也会被「元素→」正确命中；而「→穿刺」这类无对应 DoT 的目标
       仍由 `_targets()` 挡住（官方：目标没有对应 DoT 时不转）。
    """
    if in_t == base:
        return True
    return in_t == 'elemental' and base in ELEMENTAL


def _apply_one(s, convs, tbl):
    """把**一条**来源按一组规则转化（同一输入类型有多条时按比例分摊）。

    ★★ 2026-09-22：`rel` / `tot` 改从预编译查表 `tbl` 取（由 `_rel_tbl(step)` 产出），
      **算术一行没动** —— 只是把「每来源 × 每规则」的匹配展开换成一次查表。
      `tbl` 里没有的类型（理论上不该有）**就地补算并写回表**，绝不改变行为。
    """
    if s.locked or not s.type:
        return [s]
    e = tbl.get(s.type)
    if e is None:                                  # 未知类型：补算 + 写回（与旧行为一致）
        e = tbl[s.type] = _build_rel_one(s.type, convs)
    if not e:
        return [s]
    rel, tot = e
    out = []
    # ★ 阈值 1e-9 而不是 `> 0`：元素均分 100% → 33.333…×3 的浮点和可能是
    #   99.99999999999999，`keep` 会算成 1e-17 并留下一颗**零值来源**，
    #   进而在报告里冒出一个 `physical 0.0` 的空行。
    keep = max(0.0, 100.0 - tot) / 100.0
    if keep > 1e-9:
        out.append(s.scaled(keep))                 # 未被转走的部分（仍可被后续步骤转）
    share = 1.0 if tot <= 100.0 else 100.0 / tot
    for tgt, p in rel:
        k = p / 100.0 * share
        if k <= 0:
            continue
        out.append(Source(tgt, s.lo * k, s.hi * k, s.origin, s.dur, True))
    return out

def _apply_pierce(sources, ratio):
    """**护甲穿透**：残余物理 → 穿刺。官方要求排在其他转化之后。

    · 只作用 `physical` **直伤**（创伤是 DoT，穿刺没有对应 DoT ⇒ 不受影响）
    · 只作用**还没被转过**的物理（`locked=False`）
    · 作用范围是**整击残余物理**（含技能平伤里的物理），不限武器
    """
    r = min(max(float(ratio or 0.0), 0.0), 1.0)
    if r <= 0:
        return sources
    out = []
    for s in sources:
        if s.type == 'physical' and not s.locked and (s.lo or s.hi):
            if r < 1.0:
                out.append(Source('physical', s.lo * (1 - r), s.hi * (1 - r),
                                  s.origin, s.dur, True))
            out.append(Source('pierce', s.lo * r, s.hi * r, s.origin, s.dur, True))
        else:
            out.append(s)
    return out


# ---------------------------------------------------------------- Step 3.2 加成

def apply_pct(sources, pct, mult=1.0, extra_pct=None):
    """按**最终类型**取 % 加成（官方：转化之后才加成）。

    `pct`：`{类型: 百分比}`；`mult`：独立乘区（`offensiveDamageMultModifier`）
    `extra_pct`：技能专属的额外 %（与 `pct` 相加）
    返回 `[(source, value_lo, value_hi), …]`。
    """
    out = []
    for s in sources:
        p = float((pct or {}).get(s.type, 0.0)) + float((extra_pct or {}).get(s.type, 0.0))
        m = (1.0 + p / 100.0) * float(mult or 1.0)
        out.append((s, s.lo * m, s.hi * m))
    return out


# ---------------------------------------------------------------- DoT 持续时间

_DUR_RE = re.compile(r'^offensiveSlow([A-Z][a-zA-Z]*?)Duration(Min|Max|Modifier)$')


def durations(fields, level=None, _at=None, _num=None):
    """从字段里读 **DoT 持续时间（秒）**：`{dot_type: seconds}`。

    ★ 官方本地化原文（`tagCharStatsBleedAbsDmgInfo`）：
      「每次武器攻击所造成的 **3 秒内每秒**流血伤害值，含加成」
      ⇒ dbr 里的 DoT 值 = **每秒**，时长逐条可读（`offensiveSlow<X>DurationMin`），
        另有 `…DurationModifier`（如 +100% 持续）。

    `_at` / `_num`：由调用方注入 `gd.rotation` 的逐级取值函数（避免循环导入）。
    """
    got = {}
    for k, vals in (fields or {}).items():
        m = _DUR_RE.match(k)
        if not m:
            continue
        name, bound = m.group(1), m.group(2)
        key = name[0].lower() + name[1:]
        key = FIELD_TYPE.get(key, key)
        t = SLOW_TYPE.get(key)
        if not t:
            continue
        v = _at(vals, level) if (level is not None and _at) else (
            _num(vals) if _num else (vals[0] if isinstance(vals, list) and vals else vals))
        try:
            v = float(v or 0.0)
        except Exception:
            v = 0.0
        e = got.setdefault(t, {'min': 0.0, 'max': 0.0, 'mod': 0.0})
        e[bound.lower()] = v
    out = {}
    for t, e in got.items():
        v = e['min'] or e['max']
        if not v:
            continue
        out[t] = v * (1.0 + e['mod'] / 100.0)
    return out


# ---------------------------------------------------------------- 汇总

def dot_channels(sources, per_sec_by_type=None):
    """把 DoT 来源按**来源标识**聚合，供 `gd.combat.DotTimeline` 使用。

    同源刷新 / 异源叠加的「源」= `(类型, origin)`：
      · 武器带来的同类 DoT 无论走哪个技能，共享一个通道（官方：武器池来源）
      · 技能自身的 DoT 以技能为源（不同技能 = 不同源，全额叠加）
      · 装备/光环/星座各自独立
    """
    out = {}
    for s in sources:
        if not s.is_dot:
            continue
        key = (s.type, s.origin)
        out[key] = out.get(key, 0.0) + (s.lo + s.hi) / 2.0
    return out


def _summ(sources):
    """自证用：按类型**累加**（不能直接用字典推导 —— 同类型多条会互相覆盖）"""
    out = {}
    for s in sources:
        out[s.type] = round(out.get(s.type, 0.0) + s.avg, 4)
    return out


def selftest_cases():
    """纯逻辑自证（不依赖存档）：返回 `[(名称, 是否通过, 细节), …]`"""
    out = []

    # ① 只转一次：技能已转走的物理不会被全局转化再转
    src = [Source('physical', 100, 100, 'skill')]
    got = convert(src, [[('physical', 'cold', 100)], [('physical', 'fire', 100)]])
    tot = _summ(got)
    out.append(("只转一次（技能 100% 物理→冰冷后，全局 物理→火焰 不再生效）",
                tot == {'cold': 100.0}, str(tot)))

    # ② 部分转化：50% 物理 → 穿刺，另一半留物理
    got = convert([Source('physical', 100, 100)], [], pierce_ratio=0.5,
                  has_weapon_damage=True)
    tot = _summ(got)
    out.append(("护甲穿透 50% ⇒ 50 物理 + 50 穿刺", tot == {'physical': 50.0, 'pierce': 50.0},
                str(tot)))

    # ③ 门槛：没有武器伤害% ⇒ 穿透不生效
    got = convert([Source('physical', 100, 100)], [], pierce_ratio=0.5,
                  has_weapon_damage=False)
    out.append(("无武器伤害% ⇒ 护甲穿透不生效（官方门槛）",
                _summ(got) == {'physical': 100.0}, str(_summ(got))))

    # ④ 穿透作用于整击残余物理（不限武器来源）
    got = convert([Source('physical', 40, 40, 'skill'), Source('physical', 60, 60, 'weapon')],
                  [], pierce_ratio=1.0, has_weapon_damage=True)
    tot = _summ(got)
    out.append(("穿透作用整击残余物理（技能 40 + 武器 60 → 全部穿刺 100）",
                tot == {'pierce': 100.0}, str(tot)))

    # ⑤ DoT 目标没有对应类型 ⇒ 不转（火 → 混乱，燃烧保持不变）
    got = convert([Source('burn', 100, 100, 'skill', dur=3.0)], [[('fire', 'chaos', 100)]])
    out.append(("火→混乱时燃烧不转（目标无对应 DoT）",
                _summ(got) == {'burn': 100.0}, str(_summ(got))))

    # ⑥ 火 → 冰冷：燃烧跟着变成霜燃
    got = convert([Source('burn', 100, 100, 'skill', dur=3.0)], [[('fire', 'cold', 100)]])
    out.append(("火→冰冷时燃烧变霜燃",
                _summ(got) == {'frostburn': 100.0}, str(_summ(got))))

    # ⑦ 元素转化均分给火冰电
    got = convert([Source('physical', 90, 90, 'skill')], [[('physical', 'elemental', 100)]])
    tot = _summ(got)
    out.append(("物理→元素 均分 30/30/30",
                tot == {'fire': 30.0, 'cold': 30.0, 'lightning': 30.0}, str(tot)))

    # ⑧ 创伤（物理 DoT）不受护甲穿透影响
    got = convert([Source('trauma', 100, 100, 'skill', dur=3.0)], [], pierce_ratio=1.0,
                  has_weapon_damage=True)
    out.append(("创伤（物理 DoT）不受护甲穿透影响",
                _summ(got) == {'trauma': 100.0}, str(_summ(got))))

    # ⑨ 加成按**最终类型**取（转化后取新类型的 %）
    got = convert([Source('physical', 100, 100, 'skill')], [[('physical', 'cold', 100)]])
    rows = apply_pct(got, {'physical': 500.0, 'cold': 50.0})
    v = round(rows[0][1], 4)
    out.append(("加成按最终类型取（转到冰冷后吃 +50% 冰冷，而非 +500% 物理）",
                abs(v - 150.0) < 1e-9, str(v)))
    return out
