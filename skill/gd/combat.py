"""官方战斗乘区：**命中(PTH) / 暴击 / 护甲 / DoT 时间轴**。

本模块是 `docs/plan_dmg_model_v2.md` 的 Step 4/5/6 落地。

★ 为什么单独成模块：`gd/rotation.py` 原先 1272 行，再加这四个乘区会到 1800+ 行
  且无法单独测。这几个函数都**纯函数式**（不碰存档、不碰装备），可以独立断言。

★★ 常数全部来自 `data/combatformulas.json`，而它逐字来自
   `records/game/combatformulas.dbr`（游戏引擎实际读的那张表）。
   社区流传的简式与官方式**代数等价**（见 `pth()` 的 docstring），
   但阈值/下限有版本差异：**当前版 threshold1=70、floor=55**；
   旧版 wiki 是 75/60。本模块只用官方值（引入第二套只会制造混淆）。

官方指南原文（`grimdawn.com/guide/gameplay/combat/`）：
  - "PTH Threshold 1: 70 (1.0x Damage) … The damage reduction multiplier is
     equal to your PTH / 70"  ⇒ PTH<70 时命中伤害整体打折
  - "PTH cannot go below 55"
  - 示例（逐字）：PTH 97 → 1-89 普通命中 / 90-97 暴击 1.1× / 98-100 未命中；
    PTH 124 → 1-89 / 90-104(1.1×) / 105-119(1.2×) / 120-124(1.3×)
  - 部位概率 Head 15% / Shoulders 15% / Torso 26% / Arms 12% / Legs 20% / Feet 12%，
    默认护甲吸收 70%（**怪物侧被调整表 −20 缩放成 56%**，见 `monster_absorption()`）
  - "DoTs stack from different sources and always do full damage"

★★ **护甲只吃物理直伤**（判据统一走 `armor_applies()`）。「元素护甲」在本作里不存在：
  元素的减伤通道是**抗性**（`gd/rr.py` 的 10 个抗性桶），不是护甲。所以
  「怪物元素护甲值」这个量既拿不到（离线库无 `defensiveProtection`）**也不需要** ——
  要关心的只是「输出循环里哪些类型真的过护甲这一关」，那是 `rep['armor']['by_type']`。
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_CF_PATH = ROOT / "data" / "combatformulas.json"

_CF = None

# 六个受击部位 → 概率（官方常数，缺文件时用兜底值）
REGION_FALLBACK = {"head": 15.0, "shoulders": 15.0, "torso": 26.0,
                   "arms": 12.0, "legs": 20.0, "feet": 12.0}
ABSORB_FALLBACK = 70.0


# ---------------------------------------------------------------- 常数加载

def formulas(reload=False):
    """`data/combatformulas.json`（带缓存）。缺失时返回最小兜底档，**不抛异常** ——
    常数表是「精度提升」，缺了应该退化成旧行为，而不是让整条链路崩掉。"""
    global _CF
    if _CF is None or reload:
        if _CF_PATH.exists():
            try:
                _CF = json.loads(_CF_PATH.read_text(encoding="utf-8"))
            except Exception:
                _CF = {}
        else:
            _CF = {}
    return _CF or {}


def pth_constants():
    f = formulas()
    p = f.get("pth") or {}
    thr = p.get("thresholds") or [70, 90, 105, 120, 130, 135]
    mod = p.get("modifiers") or [1.0, 1.1, 1.2, 1.3, 1.4, 1.5]
    return {"minimum": float(p.get("minimum") or 55.0),
            "thresholds": [float(x) for x in thr],
            "modifiers": [float(x) for x in mod]}


def armor_constants():
    f = formulas()
    a = f.get("armor") or {}
    regions = {k: float(v) for k, v in (a.get("regions") or {}).items()} \
        or dict(REGION_FALLBACK)
    return {"regions": regions, "absorption": float(a.get("absorption") or ABSORB_FALLBACK)}


# ---------------------------------------------------------------- Step 4 命中 / 暴击

def pth(oa, da, minimum=None):
    """官方 PTH 方程 + 下限。

    官方式（逐字）：
        ((((OA/((DA/3.5)+OA))*300)*0.3) + (((((OA*3.25)+10000)-(DA*3.25))/100)*0.7)) - 50

    与社区简式**代数等价**（这是 `selftest` 的断言之一）：
        项① 0.3*300*OA/(DA/3.5+OA) = 90*OA*3.5/(DA+3.5*OA) = 315/(DA/OA+3.5)
        项② 0.7*(3.25*OA+10000-3.25*DA)/100 = (OA-DA)/43.9556 + 70
        常数 70-50 = 20   ⇒ 315/(DA/OA+3.5) + (OA-DA)/43.9556 + 20
    """
    if oa <= 0 or da <= 0:
        return float(minimum if minimum is not None else pth_constants()["minimum"])
    lo = pth_constants()["minimum"] if minimum is None else float(minimum)
    v = ((((oa / ((da / 3.5) + oa)) * 300) * 0.3)
         + (((((oa * 3.25) + 10000) - (da * 3.25)) / 100) * 0.7)) - 50
    return max(lo, v)


def crit_windows(pth_value):
    """PTH → 掷骰窗口切分（官方分段语义）。

    返回 `{'windows': [(lo, hi, mult, kind), …], 'faces': n}`，
    `kind ∈ {'hit','crit','miss'}`，区间是 **1-based 闭区间**（`hi` 可为小数）。

    ★★ 归一化规则（`faces`）：**总面数 = max(100, PTH)**。这是官方四个示例
       唯一自洽的解释 —— PTH=107 时官方给出 `1-89 / 90-104 / 105-107`，
       共 107 面且**没有未命中面**；若按固定 100 面归一，这个例子无法成立。
       直观理解：PTH 超过 100 后，多出来的部分**直接转成暴击面**，
       同时未命中概率归零。

    规则（与官方示例逐条对齐）：
      · 普通命中 = `1 .. min(PTH, 89)`（89 = threshold2 − 1，即第一档暴击线之前）
      · 第 i 层暴击 = `[threshold_i, min(PTH, threshold_{i+1}−1)]`，**从 threshold2=90 起**
        （threshold1=70 是「满伤线」不是暴击线）
      · 未命中 = `(PTH, 100]`（PTH ≥ 100 时为空）
      · PTH ≤ 70：命中区间 `1..PTH`，且命中伤害整体 ×(PTH/70)（见 `hit_mult`）

    自检（官方示例逐字）：
        PTH 97  → 89 / 8 / 3       PTH 107 → 89 / 15+3 / 0
        PTH 124 → 89 / 15+15+5 / 0 PTH 100 → 89 / 11 / 0
    """
    c = pth_constants()
    thr, mod = c["thresholds"], c["modifiers"]
    p = float(pth_value)
    faces = max(100.0, p)
    out = []
    if p <= 0:
        return {"windows": [(1.0, 100.0, 0.0, "miss")], "faces": faces}
    if p <= thr[0]:
        out.append((1.0, p, 1.0, "hit"))
    else:
        out.append((1.0, min(p, thr[1] - 1.0), 1.0, "hit"))
        for i in range(1, len(thr)):
            lo = float(thr[i])
            if lo > p:
                break
            hi = min(p, float(thr[i + 1]) - 1.0) if i + 1 < len(thr) else p
            if hi >= lo:
                out.append((lo, hi, float(mod[i]), "crit"))
    if p < 100.0:
        # 未命中区间 `(PTH, 100]` ⇒ 长度正好 100 − PTH（lo=p+1、hi=100）
        out.append((p + 1.0, 100.0, 0.0, "miss"))
    return {"windows": out, "faces": faces}


def hit_mult(pth_value, crit_dmg_pct=0.0, dot=False):
    """期望命中/暴击倍率（对整个 roll 1..100 求期望）。

    `crit_dmg_pct`：角色面板的「+% 暴击伤害」—— 官方规则是**平坦加到每一层的倍率**上
    （+50% ⇒ 1.1× 变 1.6×）。这里按面数加权摊进期望。

    `dot=True`：DoT 的暴击在**施放瞬间**判定一次，之后所有跳字都用同一状态
    （官方："DoT 的暴击是在施放瞬间判定的"）。对期望而言**等价于整段乘一次期望倍率**，
    所以调用方拿到同一个数即可 —— 参数只是把语义显式化，便于报告里说明。

    返回 `{'expected','hit_chance','crit_chance','miss_chance','discount','mult_by_kind'}`
      · `discount` = min(1, PTH/70)：PTH<70 时命中伤害的整体折减（官方 normalPTHEquation）
    """
    c = pth_constants()
    p = max(0.0, float(pth_value))
    discount = 1.0
    if p <= c["thresholds"][0]:
        discount = p / c["thresholds"][0]
    cd = float(crit_dmg_pct or 0.0) / 100.0
    w = crit_windows(p)
    tot = w["faces"] or 100.0
    exp = n_hit = n_crit = n_miss = 0.0
    for lo, hi, m, kind in w["windows"]:
        n = hi - lo + 1.0
        if kind == "hit":
            n_hit += n
            exp += n * discount
        elif kind == "crit":
            n_crit += n
            exp += n * (m + cd)
        else:
            n_miss += n
    return {
        "expected": exp / tot,
        "hit_chance": n_hit / tot,
        "crit_chance": n_crit / tot,
        "miss_chance": n_miss / tot,
        "discount": discount,
        "faces": tot,
        "crit_dmg_pct": float(crit_dmg_pct or 0.0),
    }


# ---------------------------------------------------------------- Step 5 护甲

def armor_mitigate(dmg, armor, absorption=None):
    """**单部位**护甲减免（官方两条公式）。

    官方（`combatformulas`）：
      DGP（damage > protection）: (prot*(1-absorb)) + (dmg - prot)
      DLEP（damage ≤ protection）: dmg * (1-absorb)
    ⇒ 即使伤害低于护甲，也仍有一部分穿透（默认吸收 70% ⇒ 穿透 30%）。

    只对**物理直伤**调用（判据统一走 `armor_applies()`）；穿刺与创伤（物理 DoT）
    **无视护甲**。`absorption` 缺省用引擎基准 70%，**打怪时应传 `monster_absorption()`**
    （推导值 56%，见该函数）—— 两个口径差 20% 的穿透量，不能混用。
    """
    a = (armor_constants()["absorption"] if absorption is None else float(absorption)) / 100.0
    d, p = float(dmg), float(armor or 0.0)
    if d <= 0:
        return 0.0
    if p <= 0:
        return d
    if d <= p:
        return d * (1.0 - a)
    return (p * (1.0 - a)) + (d - p)


def armor_expect(dmg, armor_by_region, absorption=None, weights=None):
    """**多部位期望**护甲减免。

    `armor_by_region`：`{'head': 120, 'torso': 200, …}`；缺的部位用 `armor` 键或该值兜底。
    也接受单个数字（所有部位同护甲）。

    返回 `{'expected', 'by_region', 'reduction_pct'}`。
    """
    ac = armor_constants()
    w = weights or ac["regions"]
    if isinstance(armor_by_region, (int, float)):
        vals = {r: float(armor_by_region) for r in w}
    else:
        vals = {r: float((armor_by_region or {}).get(r) or 0.0) for r in w}
    tot_w = sum(w.values()) or 1.0
    by_region, exp = {}, 0.0
    for r, ww in w.items():
        v = armor_mitigate(dmg, vals[r], absorption)
        by_region[r] = v
        exp += v * ww
    exp /= tot_w
    d = float(dmg)
    return {"expected": exp,
            "by_region": by_region,
            "reduction_pct": (0.0 if d <= 0 else (1.0 - exp / d) * 100.0)}


# ---------------------------------------------------------------- Step 5b 护甲吸收 / 破甲

ABSORB_BASE_KEY = "armorDefensiveAbsorption"
ABSORB_MAX = 100.0

# ★ 护甲只吃**物理直伤**。其余类型一律不经过护甲乘区 —— 这不是简化，是官方口径：
#     ① 两条护甲公式（DLEP/DGP）的输入变量逐字就叫 `physicalDamageDV`；
#     ② 穿刺是「护甲穿透」（`offensivePierceRatio`）从物理**转出去**的，
#        转出之后由穿刺抗性管，不再回头吃护甲；
#     ③ 创伤（internal trauma，物理 DoT）走 DoT 通道，同样不吃护甲。
#   ⇒ 所以「元素护甲」这个概念在本作里不存在：元素伤害从来就不经过护甲。
ARMOR_TYPES = frozenset({"physical"})


def armor_applies(dtype, is_dot=False):
    """该伤害类型是否经过护甲乘区（**唯一入口**，别在别处手写 `== 'physical'`）。"""
    return (not is_dot) and str(dtype) in ARMOR_TYPES


def absorb_base():
    """引擎基础护甲吸收（官方 `engine.armorDefensiveAbsorption`，= 70）。"""
    e = formulas().get("engine") or {}
    return float(e.get(ABSORB_BASE_KEY) or ABSORB_FALLBACK)


def absorption_of(absorb_modifier=None, base=None):
    """官方护甲吸收率（%），逐字对应 `calc.js::f62k`：

        absorption = 0 == slotArmor ? 0
                   : min(engine.armorDefensiveAbsorption
                         * (1 + defensiveAbsorptionModifier / 100), 100)

    两个容易写错的地方：
      ① 修正是**乘法缩放**（`base*(1+mod/100)`），不是加减百分点 ——
         `-20` 得到的是 `70*0.8 = 56`，不是 `50`；
      ② 上限 100；且**该部位护甲为 0 时吸收为 0**（不是「吸收率照旧成立」）。
         第二点在 `armor_mitigate` 里已经天然满足（`armor<=0` 直接原样返回）。
    """
    b = absorb_base() if base is None else float(base)
    m = 0.0 if absorb_modifier is None else float(absorb_modifier)
    return max(0.0, min(b * (1.0 + m / 100.0), ABSORB_MAX))


def monster_absorb_modifier(difficulty=3, players=1):
    """怪物侧 `defensiveAbsorptionModifier`（来自 `monsterAdjustments` 调整表）。

    实测：**三个难度 × 四个玩家数共 12 格全是 −20**（不随难度递增）。
    取不到（数据缺失）时返回 `None`，调用方应退回引擎基准吸收。
    """
    try:
        from . import enemy as _ENM       # 局部 import：避免 enemy→rr→combat 的环
        return _ENM.adjustments(difficulty, players).get("defensiveAbsorptionModifier")
    except Exception:
        return None


def monster_absorption(difficulty=3, players=1, override=None):
    """怪物护甲吸收率（%），返回 `(吸收率, 依据)`。

    ★ 这是**推导值**，不是逐怪真值：**怪物记录里没有吸收率字段**，
      但它的**修正量**在官方调整表里，而吸收的换算公式在官方引擎常数旁边。
      推导链：`engine.armorDefensiveAbsorption(70)`
              × `(1 + monsterAdjustments.defensiveAbsorptionModifier(−20)/100)`
              = 56（终极难度，实测三档同为 −20 ⇒ 三档都是 56）。
    ※ 护甲**值**不在此列 —— 它已经从被动技能的 `defensiveProtection` 拿到了真值
      （`gd/enemy.py::armor_of`，与游戏面板「护甲等级」逐位一致，自检 [24]）；
      这里只是**吸收率**（护甲值 → 实际减免的比例）仍属推导。
      真假人对照可走 `tools/gt_regress.py --sheet`（游戏内实测通道）。
    """
    if override is not None:
        return float(override), "显式指定"
    m = monster_absorb_modifier(difficulty, players)
    if m is None:
        return absorb_base(), "退回引擎基准（调整表缺失）"
    return absorption_of(m), ("推导：%g%% × (1 + (%g)/100)" % (absorb_base(), m))


def armor_after_reduce(armor, reduction):
    """**破甲**：`N 点目标护甲降低` ⇒ 护甲按**绝对值**下调，下限 0。

    官方字段 `DamageDurationDefensiveReduction`（显示文本逐字：`点目标护甲降低`）。
    ⚠ 全库普查（`gd rr --scan`）：该字段在 **itemdb.js / skills_json.js 里出现 0 次**，
      只在 `monsterdb.js` 出现 80 次 —— 即**只有怪物能「破」玩家的甲，
      玩家侧没有任何装备/技能能降低怪物护甲**。
      所以这个函数是**挂点**（机制已就位、参数化），数据侧目前恒为 0；
      真要生效得靠 `--enemy-armor-reduce` 显式给值或用怪物技能做压力测试。
    """
    a = max(0.0, float(armor or 0.0))
    r = max(0.0, float(reduction or 0.0))
    return max(0.0, a - r)


def armor_reduce_constants():
    """破甲字段的**单一真源**（字段名 + 显示文本 + 结论）。

    ⚠ 出现次数**不写死在这里** —— 那是「数据普查」的结论，会随版本/Mod 变，
      必须**实测**：见 `gd/rr.py::armor_reduce_census()`（`gd rr --scan` 也会打它）。
      写死计数的后果是「论文级结论悄悄过期」，本项目已踩过一次（护甲值那条）。
    """
    return {"fields": ("DamageDurationDefensiveReduction",),
            "tag": "DamageDurationDefensiveReduction",
            "zh": "点目标护甲降低",
            "note": "玩家侧无破甲机制（实测见 gd/rr.py::armor_reduce_census）；"
                    "挂点保留，参数名 armor_reduce / --enemy-armor-reduce"}


# ---------------------------------------------------------------- Step 6 DoT 时间轴

class DotTimeline:
    """DoT 通道：**异源全额叠加、同源刷新**。

    官方："DoTs stack from different sources and always do full damage.
    For example, if you apply Poison with your weapon, and then another Poison
    effect with a spell, both will deal full damage."

    ⇒ 不同 `key`（来源）= 各自独立通道，**全额相加**；
      同一 `key` 重复施加 = 刷新（取更强的每秒值 + 重置时长），**不叠加**。

    ★ 与旧实现的关键差别：旧代码把 DoT 当平伤 **× 攻频**，
      高频技能会高估（同一通道被算成多份），低频技能会低估（持续时间被吞）。
      正确口径是「稳态覆盖率」：`每秒值 × min(1, 时长 × 施加频率)`。
    """

    def __init__(self):
        self.channels = {}

    def apply(self, key, per_sec, dur_s, crit=False):
        """施加一次 DoT。同源已存在时只在**更强**或**暴击**时替换（并按新时长续期）。"""
        cur = self.channels.get(key)
        if cur is None:
            self.channels[key] = {"per_sec": float(per_sec), "dur": float(dur_s),
                                  "crit": bool(crit)}
            return
        # 官方：暴击的 DoT 覆盖同源非暴击的（暴击状态在施放瞬间锁定）
        if crit and not cur["crit"]:
            self.channels[key] = {"per_sec": float(per_sec), "dur": float(dur_s),
                                  "crit": True}
        elif crit == cur["crit"] and float(per_sec) > cur["per_sec"]:
            cur["per_sec"] = float(per_sec)
            cur["dur"] = float(dur_s)

    def steady_dps(self, freqs=None):
        """稳态总 DPS。

        `freqs`：`{key: 施加频率(次/秒)}`。给了就按覆盖率打折
        （`min(1, 时长×频率)`）；没给的通道按**满覆盖**算（= 一直在跳）。
        """
        tot = 0.0
        for k, c in self.channels.items():
            f = (freqs or {}).get(k)
            cov = 1.0 if f is None else min(1.0, c["dur"] * float(f))
            tot += c["per_sec"] * cov
        return tot

    def coverage(self, key, freq):
        c = self.channels.get(key)
        if not c:
            return 0.0
        return min(1.0, c["dur"] * float(freq))


def dot_channel_dps(per_sec, dur_s, freq):
    """单通道稳态 DPS —— 同源刷新 ⇒ 覆盖率上限 1.0（不会因为打得多而翻倍）。"""
    return float(per_sec) * min(1.0, max(0.0, float(dur_s)) * max(0.0, float(freq)))


# ---------------------------------------------------------------- 自证

def selftest_identities():
    """常数自证（L1）：返回 `[(名称, 是否通过, 细节), …]`。

    不依赖任何存档/装备 —— 纯公式恒等，可在 `tools/selftest.py` 里直接跑。
    """
    out = []
    c = pth_constants()

    # ① 官方式 ⇔ 社区简式（同一组 (OA,DA) 上逐点比较）
    worst = 0.0
    for oa, da in ((2000, 1800), (1500, 2000), (3000, 2500), (800, 900), (5000, 4000)):
        official = pth(oa, da, minimum=0)
        simple = 315.0 / (da / oa + 3.5) + (oa - da) / (1.0 / (0.7 * 3.25 / 100.0)) + 20.0
        worst = max(worst, abs(official - simple))
    out.append(("PTH 官方式 ⇔ 简式 `315/(DA/OA+3.5)+(OA-DA)/43.9556+20`", worst < 1e-6,
                "最大偏差 %.2e" % worst))

    # ② 阈值/倍率与官方表逐字一致
    out.append(("PTH 阈值 = [70,90,105,120,130,135]", c["thresholds"] == [70, 90, 105, 120, 130, 135],
                str(c["thresholds"])))
    out.append(("PTH 倍率 = [1,1.1,1.2,1.3,1.4,1.5]", c["modifiers"] == [1, 1.1, 1.2, 1.3, 1.4, 1.5],
                str(c["modifiers"])))
    out.append(("PTH 下限 = 55（当前官方；旧 wiki 75/60 已过时）", c["minimum"] == 55,
                str(c["minimum"])))

    # ③ 官方给出的四个示例，逐面数校验（含 PTH>100 的归一化）
    cases = {97: (89, 8, 3), 107: (89, 18, 0), 124: (89, 35, 0), 100: (89, 11, 0)}
    ok, det = True, []
    for p, (nh, nc, nm) in cases.items():
        w = crit_windows(p)
        acc = {"hit": 0.0, "crit": 0.0, "miss": 0.0}
        for lo, hi, _m, k in w["windows"]:
            acc[k] += hi - lo + 1.0
        got = (round(acc["hit"]), round(acc["crit"]), round(acc["miss"]))
        det.append("PTH%d=%d/%d/%d" % (p, got[0], got[1], got[2]))
        ok = ok and got == (nh, nc, nm)
    out.append(("官方 PTH 示例窗口逐条吻合（97/107/124/100）", ok, " ".join(det)))
    out.append(("PTH>100 时总面数 = PTH（未命中归零）",
                crit_windows(124)["faces"] == 124.0 and crit_windows(107)["faces"] == 107.0,
                "107→%g 124→%g" % (crit_windows(107)["faces"], crit_windows(124)["faces"])))

    # ④ 部位概率合计 = 100、吸收 = 70
    ac = armor_constants()
    out.append(("部位概率合计 = 100", abs(sum(ac["regions"].values()) - 100.0) < 1e-9,
                str(ac["regions"])))
    out.append(("护甲吸收 = 70%", abs(ac["absorption"] - 70.0) < 1e-9, str(ac["absorption"])))

    # ⑤ 护甲两条公式的边界行为
    a70 = armor_mitigate(50, 100)                 # 伤害 < 护甲 → DLEP
    b = armor_mitigate(200, 100)                  # 伤害 > 护甲 → DGP
    out.append(("护甲 DLEP：50 打在 100 护甲上 = 50*0.3 = 15", abs(a70 - 15.0) < 1e-9, "%.1f" % a70))
    out.append(("护甲 DGP：200 打在 100 护甲上 = 30+100 = 130", abs(b - 130.0) < 1e-9, "%.1f" % b))
    out.append(("护甲 0 时不做减免", abs(armor_mitigate(123, 0) - 123.0) < 1e-9, ""))

    # ⑥ DoT：同源刷新、异源叠加
    tl = DotTimeline()
    tl.apply("claws", 100, 2.0)
    tl.apply("claws", 100, 2.0)                   # 同源重复 → 不翻倍
    same = tl.steady_dps()
    tl.apply("spell", 100, 2.0)                   # 异源 → 全额相加
    both = tl.steady_dps()
    out.append(("DoT 同源刷新（施加两次仍是 100）", abs(same - 100.0) < 1e-9, "%.1f" % same))
    out.append(("DoT 异源叠加（两个来源 = 200）", abs(both - 200.0) < 1e-9, "%.1f" % both))
    # 覆盖率：0.25 次/秒 × 3 秒 = 75%（低频技能不再被吞）
    cov = dot_channel_dps(100, 3.0, 0.25)
    out.append(("DoT 覆盖率 0.25Hz×3s = 75%", abs(cov - 75.0) < 1e-9, "%.1f" % cov))
    # 高频技能：3.3 次/秒 × 2 秒 ⇒ 上限 100%（旧实现会算成 330）
    hi = dot_channel_dps(100, 2.0, 3.3)
    out.append(("DoT 高频覆盖率上限 = 100%（不会翻倍）", abs(hi - 100.0) < 1e-9, "%.1f" % hi))

    # ⑦ 护甲吸收：官方是**乘法缩放**、上限 100；怪物侧 = 56%
    ab = absorption_of(-20.0)
    out.append(("吸收修正是乘法缩放：70×(1−20/100)=56（不是 70−20=50）",
                abs(ab - 56.0) < 1e-9, "%.1f" % ab))
    out.append(("吸收率上限 100（+100 ⇒ 钳到 100）",
                abs(absorption_of(100.0) - 100.0) < 1e-9, "%.1f" % absorption_of(100.0)))
    out.append(("吸收率不为负（−100 ⇒ 0）",
                abs(absorption_of(-100.0) - 0.0) < 1e-9, "%.1f" % absorption_of(-100.0)))
    mm = monster_absorb_modifier(3, 1)
    out.append(("怪物调整表 defensiveAbsorptionModifier = −20（三难度同值）",
                mm == -20.0 and monster_absorb_modifier(1, 4) == -20.0,
                "%s / %s" % (mm, monster_absorb_modifier(1, 4))))
    ma, why = monster_absorption(3, 1)
    out.append(("怪物护甲吸收（推导）= 56%", abs(ma - 56.0) < 1e-9, why))

    # ⑧ 破甲：绝对值下调、下限 0
    out.append(("破甲：300 护甲 −120 = 180", abs(armor_after_reduce(300, 120) - 180.0) < 1e-9, ""))
    out.append(("破甲下限：50 护甲 −120 = 0（不会变负护甲）",
                abs(armor_after_reduce(50, 120) - 0.0) < 1e-9, ""))
    # 破甲后减免必须**单调不增**（甲越少 ⇒ 减伤越少 ⇒ 打击值越大）
    #   取 400 点打击（> 护甲，走 DGP）避免落在 DLEP 边界上变得不敏感
    a0 = armor_mitigate(400, 300, 56.0)
    a1 = armor_mitigate(400, armor_after_reduce(300, 100), 56.0)
    out.append(("破甲单调性：甲 300→200 时 400 点打击的剩余值上升",
                a1 > a0, "%.1f → %.1f" % (a0, a1)))

    # ⑨ 护甲只吃物理直伤（`armor_applies` 是唯一判据）
    verdict = {t: armor_applies(t) for t in
               ("physical", "pierce", "trauma", "fire", "burn", "bleeding", "poison")}
    out.append(("护甲只吃 physical（穿刺/创伤/元素/流血一律不过护甲）",
                verdict["physical"] and not any(v for k, v in verdict.items() if k != "physical"),
                str(verdict)))
    out.append(("创伤（物理 DoT）也不吃护甲（is_dot 优先）",
                not armor_applies("physical", is_dot=True), ""))
    return out
