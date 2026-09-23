"""敌方模型：**真实怪真值** + 五档手填（向后兼容）+ 等级池。

Q3/Q4 口径（`docs/plan_dmg_model_v2.md`）：
  · **真实怪 + 难度缩放为主** —— 数据来自 `data/monster_stats.json`，
    它逐字来自 `app.asar` 的 `/dist/monsterdb/js/monsterdb.js`（此前从未启用）。
  · **五档手填保留为显式简化档** —— `gd/rr.py::ENEMY_PROFILES` 原样不动，
    `of_profile()` 就是它的薄封装（单一真源在 rr.py）。
  · **优化器默认 Ultimate + Champion/Hero 中位怪**（而非拍一个 33%），
    可用环境变量 `GD_ENEMY_PROFILE` 覆盖（值 = 档位名 或 `m<id>` 或 `pool:<档位>`）。

★ 12 元调整数组的索引（从 `mdb_db.js` 逐字读出的语义，不是猜的）：
    `$db.buildSkillData(monsterAdjustments, 4*((4==Ph?3:Ph)-1)+Dh)`，取 `arr[rank-1]`
  ⇒ **索引 = 4*(难度-1) + 玩家数-1**，难度 1/2/3/4 = Normal/Elite/Ultimate/Ascendant，
    玩家数 1..4。自检：`offensiveTotalDamageModifier` = Normal −25% / Elite +25% /
    Ultimate +40%；`characterLifeMultModifier` 每难度 0/90/180/270（= 玩家数）。

★★ **护甲**：怪物记录里**没有** `defensiveProtection`，但**被动技能里有**
  （如 `sk1260.defensiveProtection[99] = 1607` = 游戏面板「护甲等级」）⇒ `armor_of()`
  从被动技能取，与面板逐位一致（自检 [24]）。物理直伤**只在「该怪没有护甲被动」时**才是下界。
  · `of_monster()` / `panel_of()` / `dummy()` 都给真值；
  · `pool()` 是**分位数聚合**、`of_profile()` 五档手填 ⇒ `armor=None`（没有单一护甲值）。

★★ **抗性是 10 型、分两排**（`monsterdb_page.js` 的 `ml` 逐字）：
  第一排 `Fire Cold Lightning Poison Pierce` ｜ 第二排 `Bleeding Life Aether Chaos Physical`。
  逐怪真值 = ①记录字段 ⊕ ②`monsterAdjustments[4*(难度−1)+玩家数−1]` ⊕ ③**被动技能数组**。
  第三层此前整块漏掉（第二排抗性/护甲/生命能量的真正来源），见 `gd/enemy.py::merged_stats`。
"""
import ast
import copy
import json
import math
import os
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_PATH = ROOT / "data" / "monster_stats.json"

_S = None

DIFFICULTIES = {"normal": 1, "elite": 2, "ultimate": 3, "ascendant": 4}
DIFF_ZH = {1: "普通", 2: "精英", 3: "终极", 4: "飞升"}

# 伤害抗性桶 → 怪物 dbr 字段名（与 gd/rr.py::RES_BUCKETS 对齐）
RES_FIELD = {
    "physical": "defensivePhysical", "pierce": "defensivePierce",
    "fire": "defensiveFire", "cold": "defensiveCold",
    "lightning": "defensiveLightning", "poison": "defensivePoison",
    "vitality": "defensiveLife", "aether": "defensiveAether",
    "chaos": "defensiveChaos", "bleeding": "defensiveBleeding",
}
RES_CAMEL = {b: f[len("defensive"):] for b, f in RES_FIELD.items()}

# ★ 官方页面逐字的**抗性显示顺序**（`ml`）：
#   ml="Fire Cold Lightning Poison Pierce Bleeding Life Aether Chaos Physical".split(" ")
#   前 5 个 = 面板第一排；后 5 个 = 面板第二排（正是截图没拍全的那排）。
RES_ORDER = ("fire", "cold", "lightning", "poison", "pierce",
             "bleeding", "vitality", "aether", "chaos", "physical")

DUMMY_KEYS = ("m1294", "m4139")     # 经典假人 / GDX3 假人（extract 时已识别）


# ---------------------------------------------------------------- 加载

def stats(reload=False):
    global _S
    if _S is None or reload:
        if _PATH.exists():
            try:
                _S = json.loads(_PATH.read_text(encoding="utf-8"))
            except Exception:
                _S = {}
        else:
            _S = {}
    return _S or {}


def available():
    return bool(stats().get("monsters"))


def monsters():
    return stats().get("monsters") or {}


def equations():
    return stats().get("equations") or {}


# ---------------------------------------------------------------- 表达式求值

_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
            ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd,
            ast.Name, ast.Load, ast.Mod, ast.FloorDiv, ast.Call)


def _eval(expr, env, default=0.0):
    """安全求值 `charLevel*1+2` / `((charLevel*60)^1.53)+30000` 这类表达式。

    只允许算术运算与 `env` 里的变量名 —— 用 `ast` 白名单，不用 `eval`。
    不认识的变量/语法 → 返回 `default`（**不抛异常**：敌方数据缺失不该中断配装搜索）。

    ★ JS 的 `^` 是**幂**不是异或（官方方程逐字用 `^`，如
      `characterLife = ((charLevel*60)^1.53)+30000`）。此前没做这层翻译，
      所有含 `^` 的方程都静默退化成 0 —— 生命/法力一直算不出来。
    """
    if isinstance(expr, (int, float)):
        return float(expr)
    if not isinstance(expr, str) or not expr.strip():
        return float(default)
    src = expr.strip().replace("^", "**")
    try:
        tree = ast.parse(src, mode="eval")
    except SyntaxError:
        return float(default)
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            return float(default)
        if isinstance(node, ast.Name) and node.id not in env:
            return float(default)
    try:
        v = eval(compile(tree, "<enemy>", "eval"), {"__builtins__": {}}, dict(env))
        return float(v)
    except Exception:
        return float(default)


def Y(v):
    """页面脚本里的 `Y(a)`：数组取**前两项均值**（区间），其余取原值（0 兜底）。"""
    if isinstance(v, list):
        nums = [x for x in v[:2] if isinstance(x, (int, float))]
        return (sum(nums) / len(nums)) if nums else 0.0
    return float(v) if isinstance(v, (int, float)) else 0.0


def level_value(v, level):
    """`buildSkillData(skill, level)` 的数组取值语义：**`arr[level-1]`**（越界钳到末项）。

    自证：`sk1260.defensiveProtection[99] = 1607` 且该技能等级 = `charLevel*1` = 100
          ⇒ 与用户游戏截图「护甲等级 1607」逐字一致。
    """
    if not isinstance(v, list):
        return v if isinstance(v, (int, float)) else 0.0
    if not v:
        return 0.0
    i = max(0, min(len(v) - 1, int(level) - 1))
    x = v[i]
    return x if isinstance(x, (int, float)) else 0.0


def skill_level(expr, char_level):
    """`$db.charEquationValue(expr)` = `floor(evaluate(expr, {charLevel}))`。"""
    return max(0, int(math.floor(_eval(
        expr, {"charLevel": float(char_level), "charlevel": float(char_level)}))))


def passive_block(mid, char_level):
    """Σ 该怪所有**被动技能**的防御/属性贡献（`I.v = mergeSkillData(I.v, pl.v)`）。

    逐字依据见 `tools/extract_monsterdb.py` 里 `PASSIVE_CLASSES` 上方的注释。
    这一步是「第二排抗性」（流血/活力/以太/混乱/物理）与**护甲**的真正来源。
    """
    m = monsters().get(mid) or {}
    Ps = stats().get("passive_skills") or {}
    out = {}
    for sid, expr in (m.get("passives") or []):
        sk = Ps.get(sid) or {}
        lv = skill_level(expr, char_level)
        for k, v in (sk.get("s") or {}).items():
            out[k] = out.get(k, 0.0) + level_value(v, lv)
    return out


def merged_stats(mid, level=100, difficulty=3, players=1):
    """重建 grimtools 怪物页的 `x = I.v`：

       记录里匹配 `/^(defensive.+|character.+Speed.*)$/` 的字段
       ⊕ `monsterAdjustments[4*(难度-1)+玩家数-1]`
       ⊕ Σ 被动技能（`passive_block`）

    合并语义 = `mergeSkillData` = **数值相加**（数组逐元相加）。
    """
    m = monsters().get(mid) or {}
    x = {}
    for k, v in (m.get("recdef") or {}).items():
        x[k] = Y(v)
    for k, v in adjustments(difficulty, players).items():
        x[k] = x.get(k, 0.0) + float(v)
    for k, v in passive_block(mid, float(level)).items():
        x[k] = x.get(k, 0.0) + float(v)
    return x


def char_level_of(mid, level):
    """怪物在其记录里定义的**实际等级**（`charLevel*1+2` 这类表达式的求值）"""
    m = monsters().get(mid) or {}
    return _eval(m.get("level_expr"), {"charLevel": float(level)}, default=float(level))


def adj_index(difficulty=3, players=1):
    """调整数组下标 = `4*(难度-1) + 玩家数-1`（见模块 docstring 的证据）"""
    d = DIFFICULTIES.get(str(difficulty).lower(), difficulty if isinstance(difficulty, int) else 3)
    d = int(d or 3)
    d = 3 if d == 4 else d                    # 官方：`4==Ph ? 3 : Ph`
    d = max(1, min(3, d))
    p = max(1, min(4, int(players or 1)))
    return 4 * (d - 1) + (p - 1)


def adjustments(difficulty=3, players=1):
    """该难度/玩家数下的全局敌方修正（`{字段: 值}`）"""
    i = adj_index(difficulty, players)
    out = {}
    for k, v in (stats().get("adjustments") or {}).items():
        if isinstance(v, list) and i < len(v) and isinstance(v[i], (int, float)):
            out[k] = float(v[i])
    return out


# ---------------------------------------------------------------- 单只怪

def _official_eq(name, values, default=None):
    """用 `data/combatformulas.json` 里的**官方方程原文**求值（单一真源，不手抄公式）。

    如 `offensiveAbilityEquation = '(offensiveAbilityDV + (characterLevelDV * 12) + '
    '((dexterityDV + bonusDV) *0.5)) * (1 + (offensiveAbilityModifierDV / 100))+53'`
    """
    from . import combat as _CB                # combat.py 无内部依赖，不会成环
    f = (_CB.formulas().get("combatformulas") or {}).get(name)
    if not isinstance(f, str):
        return default
    env = {k: float(v) for k, v in values.items()}
    got = _eval(f, env, default=float("nan"))
    return default if got != got else got      # NaN ⇒ 方程缺变量，退回 default


def _eq_value(mid, key, char_level):
    eq = equations().get((monsters().get(mid) or {}).get("eq") or "") or {}
    return _eval(eq.get(key), {"charLevel": float(char_level),
                               "charlevel": float(char_level)})


def _attrs_of(mid, char_level, difficulty, players):
    """三围（体格/灵巧/精神）的真值 —— 官方页面逐字：
    `(eq值 + x.characterStrength) * (1 + x.characterStrengthModifier/100)`"""
    x = merged_stats(mid, char_level, difficulty, players)
    out = {}
    for key in ("Strength", "Dexterity", "Intelligence"):
        base = _eq_value(mid, "character" + key, char_level) + Y(x.get("character" + key))
        out[key.lower()] = base * (1.0 + Y(x.get("character" + key + "Modifier")) / 100.0)
    return out


def da_of(mid, level=100, difficulty=3, players=1, exact=False):
    """敌方 DA 真值（官方 `defensiveAbilityEquation`，含**被动技能**贡献）。

    `exact=True` 时直接用 `level`；否则先把 `level` 过一遍怪物自己的
    `charLevel` 表达式（如 `(charLevel*1.1)+2`）—— 这才是它在游戏里的实际等级。
    """
    lv = float(level) if exact else char_level_of(mid, level)
    x = merged_stats(mid, lv, difficulty, players)
    a = _attrs_of(mid, lv, difficulty, players)
    got = _official_eq("defensiveAbilityEquation", {
        "defensiveAbilityDV": _eq_value(mid, "characterDefensiveAbility", lv)
                              + Y(x.get("characterDefensiveAbility")),
        "defensiveAbilityModifierDV": Y(x.get("characterDefensiveAbilityModifier")),
        "strengthDV": a["strength"], "bonusDV": 0.0, "characterLevelDV": lv})
    if got is not None:
        return got
    return (_eq_value(mid, "characterDefensiveAbility", lv) + Y(x.get("characterDefensiveAbility"))
            + lv * 12.0 + a["strength"] * 0.5) \
        * (1.0 + Y(x.get("characterDefensiveAbilityModifier")) / 100.0) + 53.0


def oa_of(mid, level=100, difficulty=3, players=1, exact=False):
    """敌方 OA 真值（同 DA 的构造，用于算敌方打你的 PTH）。

    ★ 被动技能会改这一项 —— 例：`m3955` 的 `sk4685.characterOffensiveAbilityModifier = -1`，
      不合并就会把 OA 算高 26.9（2503.9 vs 截图 2477）。
    """
    lv = float(level) if exact else char_level_of(mid, level)
    x = merged_stats(mid, lv, difficulty, players)
    a = _attrs_of(mid, lv, difficulty, players)
    got = _official_eq("offensiveAbilityEquation", {
        "offensiveAbilityDV": _eq_value(mid, "characterOffensiveAbility", lv)
                              + Y(x.get("characterOffensiveAbility")),
        "offensiveAbilityModifierDV": Y(x.get("characterOffensiveAbilityModifier")),
        "dexterityDV": a["dexterity"], "bonusDV": 0.0, "characterLevelDV": lv})
    if got is not None:
        return got
    return (_eq_value(mid, "characterOffensiveAbility", lv) + Y(x.get("characterOffensiveAbility"))
            + lv * 12.0 + a["dexterity"] * 0.5) \
        * (1.0 + Y(x.get("characterOffensiveAbilityModifier")) / 100.0) + 53.0


def res_of(mid, level=100, difficulty=3, players=1, buckets=None, capped=False,
           exact=False):
    """敌方抗性真值（**全 10 型**）：`记录基础值 + 难度修正 + Σ 被动技能`（逐桶）。

    ★ 这就是「第二排抗性」：`gd/rr.py` 的动画里只看到 5 个，实际是 10 个 ——
      逐字来自官方页面 `ml="Fire Cold Lightning Poison Pierce Bleeding Life
      Aether Chaos Physical".split(" ")`。

    ★ `defensiveElementalResistance`：一个字段**同时**加给火/冰/电（官方 `Dl()` 逐字）。
    ★ `capped=True` 走显示口径 `min(d, 500 + 该类型MaxResist + AllMaxResist)`；
      伤害结算要用 `capped=False`（真实值）。
    """
    lv = float(level) if exact else char_level_of(mid, level)
    x = merged_stats(mid, lv, difficulty, players)
    keys = tuple(buckets) if buckets else RES_ORDER
    out = {}
    for b in keys:
        camel = RES_CAMEL.get(b)
        if camel is None:
            continue
        d = Y(x.get("defensive" + camel))
        if camel in ("Fire", "Cold", "Lightning"):
            d += Y(x.get("defensiveElementalResistance"))
        if capped:
            d = min(d, 500.0 + Y(x.get("defensive" + camel + "MaxResist"))
                    + Y(x.get("defensiveAllMaxResist")))
        out[b] = d
    return out


def armor_of(mid, level=100, difficulty=3, players=1, exact=False):
    """敌方**护甲值** —— 此前记为「离线库没有」，实际在**被动技能**里。

    官方页面逐字：`Cl()` 里 `e = (部位护甲 + Y(a.defensiveProtection))
    * (1 + Y(a.defensiveProtectionModifier)/100)`，再按 6 部位概率加权
    （怪物 `defensiveProtection` 与部位无关 ⇒ 加权和恰等于该值）。
    自证：`m3955` ⇒ `sk1260.defensiveProtection[99] = 1607` = 游戏面板「护甲等级 1607」。
    """
    lv = float(level) if exact else char_level_of(mid, level)
    x = merged_stats(mid, lv, difficulty, players)
    return Y(x.get("defensiveProtection")) \
        * (1.0 + Y(x.get("defensiveProtectionModifier")) / 100.0)


def panel_of(mid, level=100, difficulty=3, players=1):
    """★ **复现游戏/grimtools 怪物面板**（逐字段，用于对拍与自检）。

    与 `da_of`/`res_of` 的区别：这里**直接用 `level`**，不过怪物自己的 `charLevel`
    表达式 —— 官方页面是 `U.level = X = globals.charLevel`（非宠物分支）。
    """
    if mid not in monsters():
        return None
    x = merged_stats(mid, float(level), difficulty, players)
    a = _attrs_of(mid, float(level), difficulty, players)
    life = (_eq_value(mid, "characterLife", level) + Y(x.get("characterLife"))) \
        * (1.0 + Y(x.get("characterLifeModifier")) / 100.0)
    mana = (_eq_value(mid, "characterMana", level) + Y(x.get("characterMana"))) \
        * (1.0 + Y(x.get("characterManaModifier")) / 100.0)
    m = monsters()[mid]
    return {
        "id": mid,
        "tag": m.get("tag"),
        "classification": m.get("classification"),
        "level": float(level),
        "difficulty": difficulty,
        "players": players,
        "physique": a["strength"],
        "cunning": a["dexterity"],
        "spirit": a["intelligence"],
        "health": life,
        "energy": mana,
        "oa": oa_of(mid, level, difficulty, players, exact=True),
        "da": da_of(mid, level, difficulty, players, exact=True),
        "armor": armor_of(mid, level, difficulty, players, exact=True),
        "res": res_of(mid, level, difficulty, players, capped=True, exact=True),
        "res_raw": res_of(mid, level, difficulty, players, capped=False, exact=True),
    }


def of_monster(mid, level=100, difficulty=3, players=1):
    """一只怪的完整敌方档（可直接喂 `gd/rr.py::apply_vs(per_bucket=…)`）。"""
    m = monsters().get(mid) or {}
    return {
        "kind": "monster",
        "id": mid,
        "tag": m.get("tag"),
        "name": m.get("tag"),
        "classification": m.get("classification"),
        "tier": m.get("tier"),
        "level": char_level_of(mid, level),
        "difficulty": difficulty,
        "players": players,
        "da": da_of(mid, level, difficulty, players),
        "oa": oa_of(mid, level, difficulty, players),
        "res": res_of(mid, level, difficulty, players),
        "armor": armor_of(mid, level, difficulty, players),
        "source": "monster_stats.json",
    }


def dummy(level=100, difficulty=3, players=1, gdx3=True):
    """训练假人（`m4139` = GDX3 假人 / `m1294` = 经典假人）"""
    mid = "m4139" if gdx3 else "m1294"
    if mid not in monsters():
        mid = next(iter(monsters()), None)
    return of_monster(mid, level, difficulty, players) if mid else None


# ---------------------------------------------------------------- 等级池（典型怪）

def pool(level=100, difficulty=3, players=1,
         tiers=("Champion", "Hero"), rank=0.5, match_difficulty=True):
    """该等级的「典型怪」—— 取指定档位里 **DA / 各抗性的分位数**。

    Q4 的口径：优化器不该对着「拍一个 33%」调，而应对着**真实怪群的中位数**调。
    `rank` = 分位（0.5 = 中位；1.0 = 最抗打的那只，可作压力测试）。

    返回与 `of_monster` 同构的 dict（`kind='pool'`）。
    """
    want = {str(t).lower() for t in tiers} if tiers else None
    rows = []
    for mid, m in monsters().items():
        if want and str(m.get("classification") or "").lower() not in want:
            continue
        if match_difficulty:
            ds = m.get("difficulty")
            if ds and int(difficulty) not in [int(x) for x in ds]:
                continue
        if not (m.get("eq") or "") or not (m.get("res")):
            continue
        rows.append(mid)
    if not rows:
        return None
    da = [da_of(mid, level, difficulty, players) for mid in rows]
    buckets = sorted(RES_FIELD)
    res_by = {b: sorted(res_of(mid, level, difficulty, players).get(b, 0.0)
                        for mid in rows) for b in buckets}

    def q(vs):
        if not vs:
            return 0.0
        i = max(0, min(len(vs) - 1, int(round((len(vs) - 1) * float(rank)))))
        return vs[i]

    return {
        "kind": "pool",
        "id": "pool:%d:%s" % (int(difficulty), "+".join(sorted(want or []))),
        "classification": "+".join(sorted(want or [])) or "all",
        "level": float(level),
        "difficulty": difficulty,
        "players": players,
        "count": len(rows),
        "rank": rank,
        "da": q(sorted(da)),
        "oa": None,
        "res": {b: q(res_by[b]) for b in buckets},
        "armor": None,
        "source": "monster_stats.json::pool",
    }


# ---------------------------------------------------------------- 档位解析（统一入口）

# ★★ 2026-09-20：**档位记忆化**（本轮最大的性能修复）。
#
# 事故现场：`tools/autobuild.py` 的局部搜索每轮要跑约 **1204 次**「真实伤害模拟」，
# 20 条并行链合计 **≈4.8 万次**。每次 `plan_dps.dps_of()` 都要调
# `get_profile(None, level)` —— 而 `pool()` 从头解析等级池要 **2.007 s**，
# 单次评估总计 2.017 s ⇒ **`get_profile` 占 99.5%**。
# 一条链于是要 2408 × 2.17 s ≈ **87 分钟**，20 条并行也降不下来（链内串行）。
#
# 而它的**入参全程不变**（同一个等级、同一个 spec、同一个难度）——
# 也就是说 4.8 万次里有 4.8 万次在算**一模一样的东西**。
#
# 加缓存后单次评估 2.017 s → ~0.005 s（**约 400×**），整轮优化从「小时级」回到「分钟级」。
#   注：返回**深拷贝**，因为调用方（`gd/rotation.final_report` 等）有可能就地改
#   这份 dict 的嵌套字段；直接吐同一个对象会让缓存被污染。
_PROFILE_CACHE: dict = {}


def clear_profile_cache():
    """清空敌方档缓存（改过等级池数据 / 切换测试语境后调）。"""
    _PROFILE_CACHE.clear()


def get_profile(spec=None, level=100, players=1, difficulty=3):
    """把「档位描述」解析成敌方档 dict。`spec` 支持四种写法：

      · `None` / `''`  → `GD_ENEMY_PROFILE` 环境变量，默认 `pool:Champion+Hero@0.5`
      · `none`/`elite`/`boss`/`high`/`max` → **五档手填**（`gd/rr.py::ENEMY_PROFILES`）
      · `m<id>`        → 指定的真实怪（如 `m4139` = 训练假人）
      · `pool:Champion+Hero@0.5` → 等级池（分位可省，默认 Champion+Hero 中位）
        可写作 `pool:Champion+Hero@0.5#elite` 强制该池的难度（覆盖 `difficulty`）

    `difficulty` 只对 **真值口径**（`m<id>` / `pool:`）有意义：五档手填是「全体同值」的
    简化档，本身就把难度折进去了（`elite` = 33%）。

    ★ 结果按 `(spec, level, players, difficulty)` **记忆化**（见上方长注释）——
    热路径上这是 400× 的差别，别把缓存去掉。
    """
    _spec = (spec if spec is not None
             else os.environ.get("GD_ENEMY_PROFILE", "")).strip() or DEFAULT_SPEC
    key = (_spec, int(level), int(players), int(difficulty))
    hit = _PROFILE_CACHE.get(key)
    if hit is not None:
        return copy.deepcopy(hit)
    out = _get_profile_uncached(spec, level=level, players=players,
                                difficulty=difficulty)
    _PROFILE_CACHE[key] = copy.deepcopy(out)
    return out


def _get_profile_uncached(spec=None, level=100, players=1, difficulty=3):
    """`get_profile` 的**无缓存**实现（逻辑一字未改，只是被包了一层记忆化）。"""
    spec = (spec if spec is not None else os.environ.get("GD_ENEMY_PROFILE", "")).strip()
    if not spec:
        spec = DEFAULT_SPEC
    # 五档手填（单一真源在 gd/rr.py）
    from . import rr as _RR
    if spec in _RR.ENEMY_PROFILES:
        p = _RR.ENEMY_PROFILES[spec]
        return {"kind": "flat", "id": spec, "classification": spec,
                "level": float(level), "difficulty": "n/a", "players": players,
                "da": None, "oa": None,
                "res": {b: float(p["base"]) for b in RES_FIELD},
                "flat_base": float(p["base"]), "zh": p.get("zh"),
                "armor": None, "source": "gd/rr.py::ENEMY_PROFILES"}
    if spec.startswith("pool"):
        body = spec.split(":", 1)[1] if ":" in spec else "Champion+Hero"
        if "#" in body:                      # `pool:…@0.5#elite` → 强制难度
            body, d = body.split("#", 1)
            difficulty = DIFFICULTIES.get(d.strip().lower(), difficulty)
        rank = 0.5
        if "@" in body:
            body, r = body.split("@", 1)
            try:
                rank = float(r)
            except ValueError:
                rank = 0.5
        tiers = tuple(t for t in body.split("+") if t.strip()) or ("Champion", "Hero")
        got = pool(level=level, difficulty=difficulty, players=players,
                   tiers=tiers, rank=rank)
        if got:
            return got
        return {"kind": "flat", "id": "elite", "res": {}, "flat_base": 33.0,
                "armor": None, "source": "fallback"}
    if spec.startswith("m") and spec in monsters():
        return of_monster(spec, level=level, difficulty=difficulty, players=players)
    # 未知写法 → 落回默认
    return get_profile(DEFAULT_SPEC, level=level, players=players,
                       difficulty=difficulty)


# Q4：默认对着 **Ultimate 难度的 Champion/Hero 中位怪** 调（而不是拍一个 33%）
DEFAULT_SPEC = "pool:Champion+Hero@0.5"


def res_base_map(prof):
    """从档 dict 取「逐桶抗性」—— 五档手填时退化成全体同值。"""
    if not prof:
        return {}, 33.0
    res = prof.get("res") or {}
    if prof.get("kind") == "flat" and not res:
        return {}, float(prof.get("flat_base") or 0.0)
    return {k: float(v) for k, v in res.items()}, None


def describe(prof):
    if not prof:
        return "（无）"
    res = prof.get("res") or {}
    top = sorted(res.items(), key=lambda x: -x[1])[:5]
    da = prof.get("da")
    return "%s%s ｜ %s ｜ 等级 %s ｜ DA %s ｜ 抗性 %s" % (
        prof.get("id") or "?", ("（%s）" % prof["zh"]) if prof.get("zh") else "",
        prof.get("classification") or "-",
        ("%g" % prof["level"]) if prof.get("level") is not None else "-",
        ("%.0f" % da) if isinstance(da, (int, float)) else "-",
        "、".join("%s%.0f%%" % (k, v) for k, v in top) or "-")


# ---------------------------------------------------------------- CLI

PANEL_ZH = {"physique": "体格", "cunning": "灵巧", "spirit": "精神",
            "health": "生命", "energy": "能量", "oa": "攻击能力", "da": "防御能力",
            "armor": "护甲等级"}
RR_ZH = {"fire": "火焰", "cold": "冰冷", "lightning": "闪电", "poison": "毒素",
         "pierce": "穿刺", "bleeding": "流血", "vitality": "活力",
         "aether": "以太", "chaos": "混乱", "physical": "物理"}


def tag_index():
    """`tag → mid` 索引（同 tag 多只时取 id 最小，保证可复现）。"""
    out = {}
    for mid, m in sorted(monsters().items()):
        t = m.get("tag")
        if t and t not in out:
            out[t] = mid
    return out


def find(spec):
    """把 `m3955` / `tagGDX3Nemesis_Outlaw02` / 子串 解析成 mid。"""
    if not spec:
        return None
    if spec in monsters():
        return spec
    ti = tag_index()
    if spec in ti:
        return ti[spec]
    low = spec.lower()
    hit = [mid for t, mid in sorted(ti.items()) if low in t.lower()]
    return hit[0] if hit else None


def format_panel(p):
    """把 `panel_of` 的结果排成「游戏面板」样式（两排抗性）。"""
    if not p:
        return "（无）"
    L = ["%s ｜ %s ｜ 等级 %g ｜ 难度 %s ｜ %d 人"
         % (p["id"], p.get("tag") or "-", p["level"], DIFF_ZH.get(p["difficulty"], p["difficulty"]),
            p["players"]),
         "  属性    " + "  ".join("%s %s" % (PANEL_ZH[k], format(p[k], ",.0f"))
                                  for k in ("physique", "cunning", "spirit")),
         "          " + "  ".join("%s %s" % (PANEL_ZH[k], format(p[k], ",.0f"))
                                  for k in ("health", "energy")),
         "  战斗属性 " + "  ".join("%s %.0f" % (PANEL_ZH[k], p[k]) for k in ("oa", "da", "armor"))]
    r = p.get("res") or {}
    for row, keys in (("第一排", RES_ORDER[:5]), ("第二排", RES_ORDER[5:])):
        L.append("  抗性 %s " % row + "  ".join(
            "%s %.0f%%" % (RR_ZH.get(k, k), r.get(k, 0.0)) for k in keys))
    return "\n".join(L)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(prog="enemy", description="敌方模型：真值怪 / 五档 / 等级池")
    ap.add_argument("--level", type=int, default=100)
    ap.add_argument("--difficulty", default="ultimate",
                    choices=["normal", "elite", "ultimate", "ascendant"])
    ap.add_argument("--players", type=int, default=1)
    ap.add_argument("--dummy", action="store_true", help="训练假人")
    ap.add_argument("--pool", default="", help="等级池，如 'Champion+Hero@0.5'")
    ap.add_argument("--monster", default="", help="指定怪 id，如 m1294")
    ap.add_argument("--panel", default="", metavar="SPEC",
                    help="打印「游戏面板」（逐字段）：m<id> / tag / 子串，如 --panel m3955")
    ap.add_argument("--find", default="", metavar="TEXT", help="按 tag 子串搜怪")
    a = ap.parse_args(argv)

    if not available():
        print("✗ 缺 data/monster_stats.json —— 先跑 tools/extract_monsterdb.py")
        return 1
    d = DIFFICULTIES[a.difficulty]

    if a.find:
        low = a.find.lower()
        rows = [(mid, m) for mid, m in sorted(monsters().items())
                if low in str(m.get("tag") or "").lower()]
        print("匹配 %d 只（tag 含 %r）：" % (len(rows), a.find))
        for mid, m in rows[:40]:
            print("  %-8s %-42s %s" % (mid, m.get("tag"), m.get("classification")))
        return 0

    if a.panel:
        mid = find(a.panel)
        if not mid:
            print("✗ 找不到 %r（用 --find <子串> 搜）" % a.panel)
            return 1
        print(format_panel(panel_of(mid, a.level, d, a.players)))
        return 0

    if a.dummy:
        p = dummy(a.level, d, a.players)
    elif a.monster:
        p = of_monster(a.monster, a.level, d, a.players)
    else:
        spec = a.pool or None
        p = get_profile(("pool:%s" % spec) if spec else None, a.level, a.players)
    print(describe(p))
    print()
    print("  档位索引 = 4*(难度-1)+玩家数-1 = %d ｜ 修正 %d 项"
          % (adj_index(d, a.players), len(adjustments(d, a.players))))
    print("  抗性（面板顺序，前 5 = 第一排 / 后 5 = 第二排）：")
    for i, b in enumerate(RES_ORDER):
        if b in (p.get("res") or {}):
            print("    %s %-8s %6.1f%%" % ("第一排" if i < 5 else "第二排",
                                          RR_ZH.get(b, b), (p["res"] or {}).get(b, 0.0)))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
