# 伤害模型 v2 —— 按官方机制重建（待审批）

> 起因：用户给出「恐怖黎明伤害建模：详细实施步骤」（Step 0–8），要求**考虑该建议、重建伤害模型**。
> 本方案做三件事：① 把建议逐条对照**官方真值**做审计；② 报告离线库里**此前未启用的两层官方数据**；
> ③ 给出重建架构与待确认决策（Q1–Q9）。
>
> 审计用到的证据全部来自本地：`data/cache/itemdb.js`、`/dist/monsterdb/js/monsterdb.js`、
> `data/cache/l10n_zh.js`、`gd/*.py` 实跑输出。**没有一条来自记忆或猜测。**

---

## 0. 结论先行

**你的框架是对的**：Step 4/5 里的 PTH 公式、暴击层级 1.1/1.2/1.3/1.4/1.5、
护甲吸收 70%、6 个受击部位概率 15/15/26/12/20/12 —— **逐字命中官方常数表**。
你写的 `315/(DA/OA+3.5) + (OA−DA)/43.956 + 20` 与官方方程**代数等价**（验算见 §2.1）。

**但有 3 处与当前官方口径不符**（§1.2），且现有模型有 **4 个整块缺失**（§1.1），
其中最严重的是 DoT —— 它占 Sam 面板 DPS 的 **29.5%**，而算法是错的。

**同时**：离线库里躺着两层从未被启用的官方数据 —— **公式常数表**与**敌方真值表**
（每只怪的抗性/护甲/DA/逐级方程，含训练假人）。这意味着 Step 4/5 的「防方参数」
不必再用社区经验或假设档位，可以直接用官方数据（§2.2）。

---

## 1. 审计：现有模型 vs 你的 8 步

### 1.1 逐条对照

| 步 | 内容 | 现状 | 证据 |
|---|---|---|---|
| 0 | 范围 / 精度 | ⚠️ 单点估计，无误差口径 | 无「预期误差」概念，只有绝对值 |
| 1 | 输入层 | ✅ 攻方齐全 | 转化/属性/装备%都已收集 |
| — | 防方 | ❌ | 见 §1.1 末行 |
| 2 | **来源拆解** | ❌ **无来源概念** | `merged` 只有 `{类型: [min,max]}`，转化在**合并后**做 ⇒ 结构上无法表达「只转一次」与「哪部分被谁转过」 |
| 3.1 | **转化顺序** | ❌ 三步合成一步 | `sk_conv = conversions + mod_conversions(mods)` 合成**一个列表一次应用**；且**护甲穿透在第①步最先做**（官方要求**最后**） |
| 3.2 | 加成 | ✅ | 转化后按**最终类型**取 `%`，与官方一致 |
| 4 | **PTH / 暴击** | ❌ **完全缺失** | `offensiveCritDamageModifier` 被优化器当维度、`W_DMG['crit']=1.00`，但伤害模型里**零影响** ⇒ **口径撕裂**（优化器认为值钱、DPS 不认） |
| 5 | **护甲** | ❌ **完全缺失** | 物理直伤无任何护甲减免；6 部位概率/吸收 70% 全无 |
| 6 | **DoT** | ❌ **方向性错误** | 把 DoT 当平伤 × 攻频。读**真实持续时间**（claws `215/s × 2s`、charge `270/s × 3s`、leap `26/s × 3s`，均在 dbr 里）重算：爪子 `31,669 → 25,259`、冲锋 `4,812 → 8,219`、跳跃 `1,768 → 2,576` ⇒ **合计 38,249 → ≈36,054（−5.7%）**。高频技能高估（×攻频）、低频技能低估（覆盖率被吞）——**两头都错，方向不定** |
| 7 | 汇总 | ⚠️ 部分 | 有类型明细，但无「DoT 单列」、无「各类型占比 × 实战」 |
| 8 | 验证 | ⚠️ 起步 | `tools/gt_regress.py` 已能对 grimtools 面板 + `--golden` 冻结；**无游戏内实测通道** |
| — | 防方参数 | ⚠️ **拍的** | `gd/rr.py::ENEMY_PROFILES = {none:0, elite:33, boss:50, high:80, max:100}` —— 五个手填数字 |

现有模型的数据流（`gd/rotation.py::final_report`）：

```
① 武器平伤（含 pierce ratio 提前做）→ wflat/wdot
② 全槽 % 加成 → pct（含属性/星座/技能自身）
③ 每技能：merged = 技能平伤 + wd × base100
      → apply_conversions(merged, 技能转化 + 全局转化)   ← 合成一步、无顺序
      → × (1 + pct[最终类型])
      → avg = Σ各类型
④ 频率：WPS 权重 / 冷却
⑤ dps = avg × freq                                    ← DoT 与直伤不加区分
```

### 1.2 三处与当前官方口径不符

**(a) DoT 数值的语义是「每秒」，不是「每击总值」；持续时间可从 dbr 逐条读出**

本地化原文（`data/cache/l10n_zh.js`）：

```
tagCharStatsBleedAbsDmgInfo      = 每次武器攻击所造成的 3 秒内每秒流血伤害值，含加成。
tagCharStatsPoisonAbsDmgInfo     = 使用武器攻击每一击所造成的 5 秒内每秒毒素伤害值，含加成。
tagCharStatsElectrocuteAbsDmgInfo= 使用武器攻击每一击所造成的 3 秒内每秒电击伤害值，含加成。
```

且 `offensiveSlow*Min` 与 `offensiveSlow*DurationMin` **永远成对出现**（实测覆盖）：

| DoT | `Min` 条数 | `DurationMin` 条数 |
|---|---|---|
| Bleeding | 517 | 519 |
| Fire（燃烧） | 268 | 267 |
| Cold（霜燃） | 176 | 176 |
| Lightning（电击） | 141 | 142 |
| Poison | 268 | 270 |
| Life（活力衰减） | 90 | 90 |
| Physical（创伤） | 143 | 143 |

⇒ 持续时间**不需要假设**，逐条读。另有 `offensiveSlow*DurationModifier`（如 `+100%` 持续）。

**(b) DoT 叠加：官方是「异源全额叠加、同源刷新」** —— 你的描述只覆盖了后半句

官方指南原文：

> Damage over time (DoT) effects are different. Unlike debuff effects, DoTs stack from
> different sources and always do full damage. For example, if you apply Poison with your
> weapon, and then another Poison effect with a spell, both will deal full damage.

⇒ 同源（同一技能/同一武器池）重复施加 = **刷新，不叠加**；不同源 = **全额叠加**。

**(c) PTH 不是「概率层叠」，是 roll 1..100 的窗口；threshold1 是「伤害折减悬崖」**

官方指南给的例子（逐字）：

```
PTH = 65 : 1-65 命中但只造成 92.86% 伤害（= 65/70）, 66-100 未命中
PTH = 97 : 1-89 普通命中, 90-97 暴击 1.1×, 98-100 未命中
PTH = 107: 1-89 普通命中, 90-104 暴击 1.1×, 105-107 暴击 1.2×
PTH = 124: 1-89, 90-104(1.1×), 105-119(1.2×), 120-124(1.3×)
```

并明确：**PTH 不得低于 55**（你写的「不低于 60」是旧版 wiki：旧版 threshold1=75、floor=60；
当前版是 **threshold1=70、floor=55**，与 `combatformulas` 一致）。

⇒ 正确模型是「roll 均匀取 1..100，按阈值分段」的**期望倍率**，而不是「先判命中再判暴击」的层叠。

**(d) 护甲穿透的优先级与作用域**

官方指南 + 论坛机制贴一致：

> Armour piercing is a conversion from physical damage to piercing damage. It is a **global
> conversion that happens after all other conversions** from physical damage has been done.
> In order to use this conversion, your skill must have **weapon damage component**.

> Armor piercing converts all phys to pierce **regardless of source** as long as it has weapon
> damage. So flat from other sources is fine.

⇒ ① 排最后；② 作用于**整击残余物理**（不只武器物理）；③ 门槛是「该技能 `weaponDamagePct > 0`」；
④ 已被别的转化转走的物理**不能再被穿透转**。

**转化整体顺序（官方原文）**：

```
Base Skill > Skill Modifiers > Conversion on Modifiers or Transmuter
           > Global Conversion on Equipment and Buffs > % damage on Equipment, Auras and Passives
```

并且 **"it is only applied once"** —— 一次转化过的伤害不能再被转。
另有例外：**目标类型没有对应 DoT 时不转**（例：火→混乱，燃烧保持不变）。

---

## 2. 离线库里躺着两层未启用的官方数据

### 2.1 `window.combatformulas` —— 官方公式常数表（逐字）

`data/cache/itemdb.js` 内嵌，来源标注为 `records/game/combatformulas.dbr`：

```js
pthMinimum: 55
pthThreshold1..6  : 70, 90, 105, 120, 130, 135
pthDamageModifier1..6 : 1, 1.1, 1.2, 1.3, 1.4, 1.5
normalPTHEquation : "probabilityToHitDV/70"
probabilityToHitEquation : "((((OA/((DA/3.5)+OA))*300)*0.3)+(((((OA*3.25)+10000)-(DA*3.25))/100)*0.7))-50"

combatRegionHeadChance:15  ShouldersChance:15  TorsoChance:26
combatRegionArmsChance:12  LegsChance:20  FeetChance:12

physicalDamageDefenseEquationDGP : "(sumProtectionDV*(1-sumAbsorptionDV))+(physicalDamageDV-sumProtectionDV)"
physcialDamageDefenseEquationDLEP: "physicalDamageDV*(1-sumAbsorptionDV)"

offensiveAbilityEquation : "(offensiveAbilityDV + (characterLevelDV*12) + ((dexterityDV+bonusDV)*0.5)) * (1+(offensiveAbilityModifierDV/100)) + 53"
defensiveAbilityEquation : "(defensiveAbilityDV + (characterLevelDV*12) + ((strengthDV+bonusDV)*0.5)) * (1+(defensiveAbilityModifierDV/100)) + 53"

physicalDamageEquation        : "physicalDamageDV*((dexterityDV/245)+1)"
pierceDamageEquation          : "pierceDamageDV*((dexterityDV/245)+1)"
physicalDurationDamageEquation: "physicalDamageDV*((dexterityDV/215)+1)"
magicalDamageEquation         : "magicalDamageDV*((intelligenceDV/215)+1)"
magicalDurationDamageEquation : "magicalDamageDV*((intelligenceDV/200)+1)"

window.engine = { armorDefensiveAbsorption: 70, playerAttackSpeedCapMax: 200, ... }
```

**验算：你的 PTH 公式 = 官方方程**（代数恒等）

```
官方项① = 0.3 × 300 × OA / (DA/3.5 + OA) = 90·OA/(DA/3.5+OA)
        = 90·OA·3.5 / (DA + 3.5·OA)      [分子分母同乘 3.5/OA]
        = 315 / (DA/OA + 3.5)            ✓ 与你的第一项一致
官方项② = 0.7 × (3.25·OA + 10000 − 3.25·DA)/100 = 0.02275·(OA−DA) + 70
        = (OA−DA)/43.9556 + 70           ✓ 1/0.02275 = 43.956，与你的第二项一致
常数   = 70 − 50 = 20                      ✓ 与你的 "+20" 一致
```

顺带验证：现有 `gd/rotation.py::ATTR_EQ`（`cunning/245` 物理与穿刺、`cunning/215` 创伤与流血、
`spirit/215` 魔法、`spirit/200` 魔法 DoT）**与官方 5 条属性方程逐项一致** ✓ —— 这块不用改。

### 2.2 `/dist/monsterdb/js/monsterdb.js`（9.3 MB，**从未被抽取**）—— 敌方真值表

| 块 | 内容 | 用途 |
|---|---|---|
| `window.monsterData` | **每只怪**：`defensivePhysical/Pierce/Fire/Cold/Lightning/Poison/Life/Aether/Chaos`、`defensiveProtection`（护甲）、`characterDefensiveAbility(+Modifier)`、`monsterClassification`（Common/Champion/Hero/Boss/SuperBoss）、`charLevel` 表达式（如假人 `charLevel*1+2`）、`characterAttributeEquations` | **Step 1「防方参数」真值** |
| `window.characterAttributeEquations` | `eqNNN` → 逐级方程，例：假人 `eq424` = `characterDefensiveAbility:"(charLevel*5)+25"`、`characterStrength:"(charLevel*6)+50"` | 任意等级的敌方 DA/三围 |
| `window.monsterAdjustments` | 12 值/键 = **3 难度 × 4 档**的全局修正（如 `characterDefensiveAbilityModifier:[-15,×4, -8,×4, -8,×4]`） | **难度缩放**真值 |
| `window.ascendantAdjustments` | 飞升模式全局敌人增益（`offensiveTotalDamageModifier:165` 等） | 可选难度 |
| `window.monsterTier` / `monsterSpawns` / `monsterPool` | 档位与刷新 | 构造「实战怪群分布」 |

规模参照：`defensivePhysical` 出现 **1368** 次、`characterDefensiveAbility` **1000** 次、
`defensiveProtection` **121** 次。

**训练假人就在库里**：

```
m1294  tagMiscTargetDummy      eq424   charLevel:"charLevel*1+2"   defensiveStun:500
m4139  tagGDX3TargetDummy      eq424   charLevel:"charLevel*1+2"   defensiveKnockdown:500
```

⚠ 你引用的「1 级 DA ≈166 / 固定 1803」我**没能从本地数据复现**：按 eq424 + 官方
`defensiveAbilityEquation`，取 Normal 难度 `-15%`，lvl100 得 ≈1796（与 1803 差 0.4%）、
lvl1 得 ≈113（与你说的 166 差得多）。⇒ **敌方方程的精确形式（等级映射 + 难度系数取哪一档）
需要一个标定点才能定稿**，已列为 Q3/Q9。

**重要**：怪物防御数据**只在 monsterdb 里**，`itemdb` 的 `window.monsters` 只有
`{type, tag, onMap, diff}`（无任何数值）—— 这就是为什么此前一直以为「敌方数据没有」。

### 2.3 grimtools 的 calc 不做敌方侧结算

`calc.js` 里 `combatformulas` 只被用了 2 次，都是 `armorDefensiveAbsorption` 与
`combatRegion*Chance`（角色面板的护甲区显示）；`pth`/`pthThreshold` **零出现**。
⇒ PTH/暴击/对怪护甲这套**必须我们自己建**，但常数是官方的。

---

## 3. 重建架构

### 3.1 数据层（新增，可复跑）

| 产物 | 生成器 | 内容 |
|---|---|---|
| `data/combatformulas.json` | `tools/extract_combatformulas.py`（新） | §2.1 全部常数 + 每条带 `source` 原文留档 |
| `data/monster_stats.json` | `tools/extract_monsterdb.py`（新） | 裁剪后的敌方表：`{id: {tag, tier, res{}, armor, da, eq, charLevel}}` + `equations` + 两张 adjustments + **假人白名单** |

两者都要注册进 `tools/migrate_from_archive.py`（与 `build_mastery_attr.py` 同规格的可复跑步骤），
并在 `tools/selftest.py` 加「常数与原文一致」断言。

### 3.2 计算层（拆分 `gd/rotation.py`，现 1272 行）

```
gd/dmg.py      伤害来源与转化链
                 Source(type, lo, hi, origin, is_dot, dot_dur_s, scope)
                 decompose(...)                                  # Step 2
                 convert(sources, skill_conv, global_conv, pierce_ratio, has_wd)
                                                                 # Step 3.1：三步 + 只转一次 + DoT 无对应型不转
                 apply_pct(sources, pct, dmg_mult)               # Step 3.2

gd/combat.py   命中 / 暴击 / 护甲 / DoT 时间轴
                 pth(OA, DA)                                     # 官方方程 + floor 55
                 hit_mult(pth, crit_dmg)                         # roll 1..100 窗口 → 期望倍率
                 armor_mitigate(phys, armor_regions, absorb)      # Step 5：6 部位期望
                 DotTimeline()                                   # Step 6：同源刷新 / 异源叠加 / 暴击锁定

gd/enemy.py    敌方模型
                 of_profile(name)                                # 五档手填（保留，向后兼容）
                 of_monster(mid, level, difficulty)              # 真实怪 + 难度缩放
                 of_level_pool(level, difficulty, tier)          # 该等级的「典型怪」分布

gd/rr.py       保留：减抗收集/合计/换算（职责不变）
gd/rotation.py 收缩为「输出循环频率」：谁在何时打几下（WPS 权重 / 冷却 / 覆盖率）
gd/dps.py      报告：新增「实战伤害明细」节（直伤 / DoT / 暴击 / 护甲 分列）
```

### 3.3 每击流水线（Step 2 → Step 7）

```
① 来源拆解        武器平伤(逐把) · 技能平伤 · 光环/被动平伤 · 非武器槽平伤 · 星座平伤
                  （元素平伤均分 1/3 火冰电）
② 技能转化        技能自带 + 装备/星座的 skill modifier 转化（**技能专属**）
③ 全局转化        装备 / buff / 星座（**只作用于②没转过的来源**）
④ 护甲穿透        仅当 weaponDamagePct > 0；作用于整击残余物理；**排最后**
⑤ % 加成          按**最终类型**取（装备 + 技能 + 属性 + 星座 + 技能专属 mod）× 独立乘区
⑥ 命中 / 暴击     期望倍率（PTH 窗口 + %暴伤平摊到各层）；DoT 用**施放瞬间**锁定的暴击状态
⑦ 护甲            仅物理**直伤**；6 部位按概率加权；吸收 70%
⑧ 敌方抗性        接 `rr.py`（A 族叠加 / B-% 取最强 / B-abs 叠加）
⑨ DoT 时间轴      同源刷新、异源叠加；活跃期 vs 冷却算覆盖率
⑩ 汇总            DPS = Σ直伤×频率 + Σ DoT 通道稳态；按类型出占比
```

---

## 4. 编号决策（待 go/no-go）

| # | 决策 | 我的建议 |
|---|---|---|
| **Q1** | DoT 口径：按官方「异源叠加、同源刷新」+「值是每秒」+「时长逐条读」实现？ | **建议照做**。会改写占面板 **29.5%** 的 DoT 部分；实测 Sam 三个技能按真实时长重算 ⇒ **净 −5.7%**（38,249 → ≈36,054），但**单个技能方向不定**（爪子 −6,410 / 冲锋 **+3,407** / 跳跃 **+807**）⇒ **构成变化远大于总量变化**，这才是指向性问题 |
| **Q2** | PTH：用官方窗口模型（threshold1=70 / floor=55）？旧 wiki 口径（75/60）是否作为可选对照保留？ | 建议**只用官方**，旧口径不保留（引入第二套只会制造混淆） |
| **Q3** | 敌方模型：真实怪物 + 难度缩放（`monster_data`）为主，还是「五档手填」为主？ | 建议**真实怪为主、五档保留为显式简化档**；但需一个标定点定稿（见 Q9） |
| **Q4** | 优化器默认对谁调？ | 建议 **Ultimate 难度、Champion/Hero 档的中位怪**（而非拍一个 33%）。可选 `GD_ENEMY_PROFILE` 覆盖 |
| **Q5** | 护甲穿透：重排到 ④、门槛 `weaponDamagePct>0`、作用于整击残余物理？ | **建议照做**（现在是反的，且只作用武器物理） |
| **Q6** | `crit` 维度口径撕裂：模型里零影响、优化器给权重 1.00 | 建议 **⑥ 步接进模型**（推荐）；若你想先止血，可先 `W_DMG['crit']=0` |
| **Q7** | 拆模块（`dmg/combat/enemy`）还是就地扩 `rotation.py`？ | 建议**拆**。`rotation.py` 已 1272 行，再加 PTH/护甲/DoT 会到 1800+ 且无法单独测 |
| **Q8** | 锚点：`gd dps Sam` 面板会从 **38,249** 变（DoT 修正）；`94,126` 等旧基准怎么办？ | 建议**重设锚点**并在 `selftest` 里同时保留「DoT 修正前」的对照值，便于回溯 |
| **Q9** | 验证通道：是否加「游戏内实测」入口（你在游戏里报数 → 落 `data/regress/<char>.sheet.json` → 一键对拍）？ | 建议**加**。这是唯一能定稿取整/护甲/PTH 的手段（grimtools 页面不含这些） |

---

## 5. 影响面与风险

| 项 | 影响 | 对策 |
|---|---|---|
| `tools/selftest.py` | DPS 基准断言、golden 会漂 | 按 Q8 重设 + 保留对照 |
| `tools/gt_regress.py` | 43 项 golden 漂移 | 重新 `--golden`；新增 `--sheet` 通道 |
| `gd/opt.py` | `FLOOR` / `W_DMG` / `W_PEN` 三表要按新模型重标定（`crit`/Armor 相关权重语义变了） | 沿用现有 `recalibrate_*` 标定机制；**注意三表同键集**（`conv_net` 那次 `KeyError` 的教训） |
| 性能（退火热路径） | 来源拆解比「按类型合并」贵 | ① 保留 `dps.py::attack_rows`「别渲染报告」纪律；② PTH/护甲/敌方侧**不随装备变**的部分每次评估只算一次（标量缓存）；③ 单次评估成本要盯住（现在 `plan_dps.dps_of` 0.0016 s） |
| 迁移/真源 | 新增 4 个文件要登记 | `PAIRS`（若改 `gd/rotation.py` 需 `--capture`）+ 数据层注册进迁移步骤 |
| 向后兼容 | `rr.py::apply_vs` 契约 | **不动**（仍只补 `rep['vs']`，面板口径 `rep['dps']` 不变） |

---

## 6. 验证计划（Step 8）

三级递进 —— **由易到难，每级都能独立给出「模型错在哪」**：

| 级 | 方法 | 能定稿什么 | 现状 |
|---|---|---|---|
| **L1 常数自证** | 每个官方常数带 `source` 原文入档；`selftest` 断言与 `combatformulas` 逐字一致；PTH 公式做代数恒等断言（官方式 ⇔ `315/(DA/OA+3.5)+(OA−DA)/43.9556+20`） | 公式/阈值/倍率/部位概率/吸收率 | 待建 |
| **L2 面板对拍** | `gt_regress.py` 已有 grimtools 面板对拍（`--ref` / `--golden`）。新增 `--sheet`：角色二/三页数值（抗性、OA/DA、攻速、武器攻击范围） | 转化顺序、%加成归属、DoT 每秒值、攻速 | 已有框架 |
| **L3 假人实测** | 固定场景**逐个开模块**：纯物理直伤无转化无 DoT → 加转化 → 加 DoT → 加暴击。你报游戏内**最小/最大**（不是均值，也不是面板 DPS） | 取整规则、护甲模型细节、PTH 掷骰、敌方 DA 标定 | 待你配合 |

**L3 的场景清单（建议）**：
1. 空手/纯物理武器 + 无转化技能 → 校验**护甲 + 物理抗性 + 取整**
2. 换上一把带「50% 物理→穿刺」的武器 → 校验**护甲穿透位置**（穿刺不吃护甲）
3. 换上一件带 DoT 的装备 → 校验**每秒值 + 持续时间 + 同源刷新**
4. 提高 OA 到 PTH 跨越 90/105 阈值 → 校验**暴击窗口**
5. 换上一件带全局转化的装备 → 校验**只转一次**

---

## 7. 与既有工作的关系

- `refs/plan_rr_conversion.md`（减抗/转化四期，已全部落地）**继续有效**：
  `gd/rr.py` 的三族口径、减抗换算、敌方档位 —— 本方案只把 `ENEMY_PROFILES`
  的**数值来源**从「手填」换成「真值」（Q3/Q4），机制与接口不变。
- `tools/gt_regress.py`、`tools/rr_skills.py`、`gd rr` 子命令都不需要重写。
- 本方案是「**Stage 5**」：在减抗/转化之后，把**命中、暴击、护甲、DoT**四个乘区补齐。

---

## 附录 A：关键证据文件位置

| 证据 | 位置 |
|---|---|
| 官方公式常数表 | `data/cache/itemdb.js` → `window.combatformulas` / `window.engine` |
| 敌方真值表（源） | `E:\Grim Tools\resources\app.asar` → `/dist/monsterdb/js/monsterdb.js`（9,327,580 B） |
| 敌方真值表（已抽取探针） | `data/cache/probe/monsterdb.js` |
| DoT 语义 | `data/cache/l10n_zh.js` → `tagCharStats{Bleed,Poison,Electrocute}AbsDmgInfo` |
| 现有模型主体 | `gd/rotation.py::final_report`（741–1130 行） |
| 现有转化实现 | `gd/rotation.py::apply_conversions`（369–413 行） |
| 现有减抗 | `gd/rr.py`（556 行） |
| 现有对拍 | `tools/gt_regress.py`（701 行） |

## 附录 B：官方指南原文（Step 4/5 依据）

> Probability To Hit (PTH) = ((((Attacker's OA / ((Defender's DA / 3.5) + Attacker's OA)) * 300) * 0.3)
> + (((((Attacker's OA * 3.25) + 10000) – (Defender's DA * 3.25)) / 100) * 0.7)) – 50
> … PTH cannot go below 55 for you or your enemies …
> PTH Threshold 1: 70 (1.0x Damage) … The damage reduction multiplier is equal to your PTH / 70
> PTH Threshold 2: 90 (1.1x Damage) … Threshold 3: 105 (1.2x) … 4: 120 (1.3x) … 5/6: 130 (1.4x) / 135 (1.5x)

> Armor in Grim Dawn is location-based. … Head: 15% / Shoulders: 15% / Torso: 26% / Arms: 12% /
> Legs: 20% / Feet: 12%
> By default, your armor absorption is 70% across all your equipment.

> Damage over time (DoT) effects are different. Unlike debuff effects, DoTs stack from different
> sources and always do full damage.

> *The order of events for Conversion is as follows: Base Skill > Skill Modifiers > Conversion on
> Modifiers or Transmuter > Global Conversion on Equipment and Buffs > % damage on Equipment,
> Auras and Passives* … it is only applied **once**.

来源：`grimdawn.com/guide/gameplay/combat/`；机制贴：`forums.crateentertainment.com/t/mechanics-guide/99479`
