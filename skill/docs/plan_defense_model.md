# 防御减伤模型 —— 设计方案（**已实现**）

> ★ **实现状态（2026-09-21 17:40）**：Q1–Q5 已批复 —— **1A**（相对传递函数）／**2**（要典型档）／
> **3**（**接优化器**）／抗性**直接复用现有处理**（`gd/opt.py::NEED/PEN/RMAP` + `compare.survival` 的分层聚合思路）／
> 目标 = 与**星座·技能点·装备综合考虑**。**已全部落地**：
>
> | 交付 | 内容 |
> |---|---|
> | `gd/defense.py` | `split_fields` 分层聚合 · `resist_panel`（f63k + 难度惩罚）· `armor_panel`（f62k 逐部位）· `chain_remain`（九层）· `ehp` · `marginal` · `quick_score`（优化器用，轻量）· **16 项恒等自证** |
> | `tools/defense_audit.py` | CLI：面板 + 九层明细 + 典型档 + EHP + 边际 + **如实标注段** |
> | `tools/selftest.py [57]` | **17 项守卫**（含 ★**格式串裸 `%` 的 AST 永久守卫** —— 我在本轮又踩了两次 #76） |
> | 优化器接入 | `GD_DEF_WEIGHT`（未设 ⇒ **零漂移**）；链路 `plan_dps` 出 `def_score` → `objfunc._with_defense` 乘到评分 ⇒ **装备/星座/技能三处一起考虑** |
>
> **`selftest` 441 项全绿**（424 → 441）｜ 锚点**零漂移**。
>
> ★ 实现中挖出**新陷阱 #77**（本轮最有价值的发现）：**减益技能（`Skill_Attack*`）里的 `defensive*` 字段
> 表示「**目标的**抗性」**（如「刺客的标记」「刺骨战吼」），无脑累加会把「我给敌人的降抗」记成
> 「我自己掉抗」（Sam 面板凭空少 **38 穿刺抗 / 25 混乱抗**）。判据 = 模板前缀，见 `applies_to_self`。
> ⚠ **`gd/compare.py::survival()` 有同一缺陷**（它只用于「两个存档对比」，相对值部分抵消 ⇒ 从未暴露）；
> **本次未改它**（属既有行为，改了会让历史对比口径变化），新代码一律走 `gd/defense.py::split_fields`。
>
> ⚠ 仍待办：① 格挡吸收率口径**待游戏内实测核准**；② 若要「敌人打你多少」的绝对量，
> 需抽取 `monsterdb.js` 的攻击字段（已确认库里有：`offensivePhysical` 1782 / `attackSkill` 1152）。


> 起因：用户要求「构建防御减伤模型」。
> 本方案先做三件事：① 盘点**已有材料**（含此前散落的减伤整理）；② 用**离线库真源**审计
> 「我方挨打」这条链每一层的数据可得性；③ 给出模型架构与待决策项（Q1–Q5）。
> **全部结论来自本地实跑**（`gd/*.py`、`data/combatformulas.json`、`data/cache/calc.js`），没有一条来自记忆。

---

## 0. 结论先行

**能做的（且这正是最有用的形态）**：把「我方挨打」建成一条**纯函数减伤链**，回答
**「给定一次原始打击 D（类型 + 数值），过完 9 层后剩多少」**，并输出
**逐层剩余 / 有效减伤率 / 等效生命 EHP / 每层边际收益**。

**当前拿不到的（但库里有，只是没抽）**：**敌方打你的绝对数值**。
`data/monster_stats.json` 的 `derived` 明写 **`has_armor: false`**，字段只有怪的
`OA / DA / 抗性 / 护甲 / 攻击速度修正`，**没有攻击伤害**。

★ **但 `monsterdb.js`（9.3 MB，`/dist/monsterdb/js/db.js`）里有**（实跑确认）：
`offensivePhysical` **1782** 处、`offensiveFire` **572** 处、`attackSkill` **1152** 处、
`skillName` **33726** 处 ⇒ **「怪物用什么技能、打多少」是可抽的**，
只是这条线**从未被启用到 `data/`**（`docs/plan_dmg_model_v2.md` §2.2 已记录该缺口）。

**另一条正交事实**（官方公式表证明）：**伤害的绝对量不在防御侧** ——
`magicalDamageEquation = magicalDamageDV*((intelligenceDV/215)+1)` 里**根本没有防守方属性**，
即 **元素/魔法伤害没有防御方程 ⇒ 减伤 100% 来自抗性**；物理同理只有护甲。

⇒ 两条路：
- **A（本方案）**：**传递函数** —— 给定「一次打击 (类型, 数值)」→ 剩余 + 减伤率 + EHP + 边际收益。
  **立即可用**，且是 B 的基础设施。
- **B（后续可选）**：抽 `monsterdb` 的攻击字段 → 能算「这怪一拳打我多少」。
  成本高（9.3 MB 表 + `attackSkill` 关联 + 技能等级缩放 + 怪↔技能映射），**建议后置**。

---

## 1. 盘点：已有材料（不重造）

| 资产 | 覆盖 | 与防御模型的关系 |
|---|---|---|
| `gd/combat.py` | PTH/暴击 · 护甲 DLEP/DGP · 吸收率 · 破甲 · DoT 时间轴 | **纯函数，可直接复用**（DLEP/DGP 对「挨打」同样成立，只是把「敌人护甲」换成「我方护甲」） |
| `data/combatformulas.json` | **官方公式常数表（38 条，逐字）** | ★ **真源**：含 `shieldDamageReductionEquationDGB/DLEB`、`playerDefenseCap`、6 部位权重 |
| `data/cache/calc.js::f63k` | **抗性组成 + 上限的真源** | ★ 未落地：抗性 = `defensive<X>` + (火冰雷再加 `defensiveElementalResistance`) + (CC 类再加 `defensiveCrowdControl`)，再 `min(., cap)` |
| `data/absorption_sources.md` + `tools/absorb_audit.py` | **% / 点数吸收全来源（56 条）** | ★ **直接就是减伤链的第 ⑧⑨ 层** |
| `gd/resaudit.py` | 抗性溢出审计（逐槽 → 来源） | 减伤链第 ⑤ 层的输入审计 |
| `gd/rotation.py` 的 `rep['da']` | 我方 DA（已算，自检钉住） | 减伤链第 ② 层 |
| `data/combatformulas.json::engine.playerDefenseCap` | `[80,80,80]` = 普通/精英/终极的**抗性上限** | 未落地 |
| **`gd/defense.py`** | — | ❌ **不存在**（本方案要建的） |

**「之前整理过的文档」找到了两处，但都不是完整模型**：
1. `data/absorption_sources.md` —— 只覆盖减伤链的**最后两层**（吸收），已落文件；
2. 上一轮联网核实的**官方 Order of Defense 9 层**（`grimdawn.com/guide/gameplay/combat/`
   Advanced Mechanics + 各 wiki 交叉验证）—— **只在对话里，没落文件**，本方案把它固化进文档。

---

## 2. 数据可得性审计（逐层，实跑）

官方 Order of Defense（挨打时的结算顺序，**这是本模型的主干**）：

```
① fumble / dodge / 投射偏移  ② OA vs DA 命中  ③ 盾牌格挡  ④ 种族%减伤  ⑤ 抗性
⑥ 护甲(仅物理直伤)  ⑦ 种族点数减伤  ⑧ % 伤害吸收(乘算)  ⑨ 点数伤害吸收(加算·最后)
```

| 层 | 需要的字段 | 数据源 | 可得 | Sam 实测值 |
|---|---|---|---|---|
| ① 闪避 | `characterDodgePercent` | 装备/被动 | ✅ | **8%** |
| ① 投射偏移 | `characterDeflectProjectile` | 装备/被动 | ✅（Sam 无） | — |
| ② 命中 | 我 DA ↔ 敌 OA | `rep['da']` ↔ `enemy.oa_of()` | ✅ | DA **1,390.5** |
| ③ 格挡 | `defensiveBlockChance/Amount` · `characterDefensiveBlockRecoveryReduction` | 盾牌 | ✅（Sam 双持剑 ⇒ 无） | — |
| ④ 种族%减伤 | `racialBonusPercentDefense` (+ `racialBonusRace`) | 装备/被动 | ✅ | **12%**（种族待查） |
| ⑤ 抗性 | `defensive<X>` + `defensiveElementalResistance` + `defensiveCrowdControl`，`min(., playerDefenseCap+MaxResist)` | 装备/星座/被动 | ✅ | 见 §3.2（**全桶溢出到 80**） |
| ⑥ 护甲 | `defensiveProtection`（逐槽）+ `defensiveBonusProtection` | 装备/被动 | ✅ | **4,052 + 48 = 4,100** |
| ⑥ 吸收率 | `engine.armorDefensiveAbsorption=70` × `(1+mod/100)` | 官方常数 | ✅（`combat.absorption_of`） | 玩家 **70%**／怪 **56%** |
| ⑦ 种族点数减伤 | `racialBonusAbsoluteDefense` | 装备/被动 | ✅（Sam 无） | — |
| ⑧ % 吸收 | `damageAbsorptionPercent` | 技能/星座/装备 | ✅（走技能，非装备槽） | 0（装备槽） |
| ⑨ 点数吸收 | `damageAbsorption` | 技能/星座/装备 | ✅（同上） | 0（装备槽） |
| **敌方打击值** | 怪的技能伤害 | `monsterdb.js`（**未抽取**） | ⚠ **库里有、`data/` 没有** | — |

★ **唯一缺口**是最后一行（敌方打击的绝对量）—— 所以 A 方案的输入是
**「一次打击」=(类型, 数值)**，「数值」由调用方给（可给典型档，见 Q2）；

---

## 3. 官方真源（逐字，本模型的公式依据）

### 3.1 护甲（已有实现，`gd/combat.py`）

```
DLEP (dmg ≤ armor) : dmg * (1 − absorb)
DGP  (dmg >  armor) : armor*(1 − absorb) + (dmg − armor)
absorb = engine.armorDefensiveAbsorption(70) * (1 + defensiveAbsorptionModifier/100)
部位权重：头 15 / 肩 15 / 躯干 26 / 臂 12 / 腿 20 / 足 12（合计 100）
★ 护甲只吃**物理直伤**（判据单点 `armor_applies`）；穿刺 / 创伤 / 元素 / 流血不过甲。
```

### 3.2 抗性（新，来自 `calc.js::f63k` —— **已实测吻合**）

```js
f63k(a, b, capped) {
  e = a["defensive"+b]                                     // 基础抗性
  if (b ∈ {Fire,Cold,Lightning}) e += a.defensiveElementalResistance   // 元素抗性并入火冰雷
  if (b ∈ CC集合)                e += a.defensiveCrowdControl          // 控制类抗性并入
  if (capped) { cap = playerDefenseCap[难度] + a["defensive"+b+"MaxResist"]
                + a.defensiveAllMaxResist
                return min(e, cap) }
  return e
}
```

- `playerDefenseCap = [80, 80, 80]`（普通/精英/终极，官方 `engine`）。
- **实跑验证（Sam 装备层）**：火/冰/雷 **153**、毒 **156**、穿刺 **134**、流血 **119**、
  活力 **170**、虚化 **108**、混乱 **107**、物理 **0** ⇒ **全部 clamp 到 80**。
  ⇒ 与既有结论「**满抗 = 仅装备槽；抗性溢出不是可捡收益**」**完全一致**（多出来的 27~90 点是沉的）。
- ⚠ 真实面板抗性 = 装备 + **星座 / 专精被动**（模型里算「额外余量」）；上限仍是 80 + `MaxResist`。

### 3.3 盾牌（新，官方公式**未落地**）

```
DGB  (dmg >  block) : dmg − shieldDefense * (shieldAbsorption/100)
DLEB (dmg ≤  block) : dmg * ((100 − shieldAbsorption)/100)
命中判定：meleeBlockEquation = blockChance + blockChanceModifier
```

⚠ 与护甲**同一形状**（DLEP/DLEB 对应 DGP/DGB），所以实现可**共用一段代码**，只是参数换成盾。

### 3.4 吸收（已有 `absorb_audit`）

```
⑧ % 吸收：**乘算**——总 = 1 − Π(1−aᵢ) ⇒ **永远 < 100%**（除非单源 100%，那都是短时无敌窗）
⑨ 点数吸收：**加算**且**排在最后** ⇒ 「减到 0」靠它
```

---

## 4. 模型架构

```
gd/defense.py                      ← 新增（纯函数，不碰存档）
  ├─ panel(char_ctx, folded, ...)   ① 防御面板：逐部位护甲 / 10 桶抗性(含 f63k 上限) /
  │                                    DA / 闪避 / 偏移 / 格挡 / 种族减伤 / 吸收 / 生命
  ├─ chain(dmg, dtype, ctx)         ② 9 层减伤链 → {layers:[(层, 进, 出, 减伤%)], remain}
  ├─ ehp(panel, profile)            ③ 等效生命：按**打击构成**加权（默认给一组典型档）
  ├─ marginal(panel)                ④ 边际收益：+1% 抗性 / +100 护甲 / +1% 吸收 / +X 点数吸收
  └─ selftest_identities()          ⑤ 恒等自证（供 tools/selftest.py 直接跑）

tools/defense_audit.py             ← 新增 CLI（对标 res_audit / absorb_audit）
  $PY tools/defense_audit.py Sam [--dmg 5000 --dtype physical] [--enemy m1281] [--md]
```

**关键设计约束**（沿用既有铁律）：

- **纯函数**：`chain()` 不读存档、不读离线库 ⇒ 可单独断言（与 `gd/combat.py` 同风格）。
- **判据单点**：护甲是否生效只能走 `combat.armor_applies()`；抗性上限只能走一个 `resist_cap()`。
- **口径不混**：模型**不碰** `rep['dps']`（攻击侧面板），两者互不影响 ⇒ **锚点零漂移**。
- **数据缺口如实标注**：敌方打击值缺席时，报告要写「本层未计入绝对量」，不静默。

---

## 5. 输出示例（Sam 现状，实跑数据）

```
$PY tools/defense_audit.py Sam --dmg 5000 --dtype physical --enemy m1281

防御面板   护甲 4,100（部位 15/15/26/12/20/12）｜ DA 1,390.5 ｜ 闪避 8%
           抗性（终极，上限 80）：物理 0 ｜ 火/冰/雷 80* ｜ 毒 80* ｜ 穿刺 80* ｜ 流血 80* ｜ 活力 80* ｜ 虚化 80* ｜ 混乱 80*
           （* = 装备层溢出被 clamp；星座/被动另算余量）
           % 吸收 0 ｜ 点数吸收 0 ｜ 格挡 — ｜ 种族减伤 12%

减伤链（5,000 物理，终极罗卡）
  ① 闪避 8%      → 4,600      −8%      （期望值口径）
  ② 命中 PTH 55  → 4,600      —        （罗卡 OA 高 ⇒ 压到下限）
  ③ 格挡         → 跳过（未持盾）
  ④ 种族减伤 12% → 4,048      −12%
  ⑤ 物理抗性 0   → 4,048      —
  ⑥ 护甲 4,100   → 2,563.6    −36.7%   （DGP：4100×0.3 + 900）
  ⑦ 种族点数     → 4,048 ...（无）
  ⑧ % 吸收 0     → 2,563.6    —
  ⑨ 点数吸收 0   → 2,563.6    —
  ────────────────────────────────────────
  有效减伤 48.7% ｜ 等效生命 ×1.95 ｜ 最大可用点数吸收池 12,000（装备上限）
```

（数值为方案示意；实现后以实跑为准。）

---

## 6. 验证计划

| 级别 | 手段 | 判据 |
|---|---|---|
| L1 恒等 | `defense.selftest_identities()` → `tools/selftest.py` | 公式恒等、边界（dmg=0/armor=0）、**单调性**（抗性↑ ⇒ 剩余↓）、上限钳位 |
| L2 一致性 | 与 `gd/combat.py::selftest_identities` 交叉 | 同一条 DLEP/DGP 在两处结果一致（**共用实现**，不该有第二套） |
| L3 游戏内 | `tools/gt_regress.py --sheet` | 面板护甲/抗性/DA/闪避 逐位对拍（**唯一能定稿取整口径的手段**） |
| L4 回归 | `res_audit` / `absorb_audit --check` | 本模型复用它们的输入，三者不该互相矛盾 |

---

## 7. 决策点（Q1–Q5，待 go/no-go）

| # | 决策 | 我的建议 |
|---|---|---|
| **Q1** | 模型定位：**A 相对传递函数**（给定打击 → 剩余）？ 还是 **B 抽 `monsterdb` 攻击字段**做「模拟战斗」？ | **建议先 A**：立即可用、输出「哪层是短板 / 加什么最值 / EHP」；B 的价值只是「绝对量」，且实测 `monsterdb` **确有数据**（`offensivePhysical`/`attackSkill`/`skillName`），**不是不可做，而是先做后端**。★ 两者不冲突：**A 的减伤链就是 B 的结算内核**，B 只是给它喂真实的 D |
| **Q2** | 要不要一组**典型打击档**（如：重击 8000 物理 / 元素法术 5000 / DoT 1500/秒）作为默认输入？ | **建议要**。否则每次都要手填数字；档位可参数化并标注为「示意档，非真值」 |
| **Q3** | 是否接进优化器（作为**生存约束**或第二目标 EHP）？ | **建议先不接**。先把面板 + 链 + 边际收益做出来跑通、对拍过游戏，再决定是否纳入搜索（接早了会污染现有锚点） |
| **Q4** | 输出形态：CLI 一行摘要 / 详细报告段（`planreport` 加一节）/ 可视化？ | **建议 CLI + 报告段**（对齐 `res_audit`/`absorb_audit` 的既有风格）；可视化按需再加 |
| **Q5** | 抗性口径：面板抗性要不要把**星座/专精被动**的余量一起算进来（= 游戏真实面板）？ | **建议算**，但**分列显示**「装备层 / +余量 / 钳位后」，因为「满抗 = 仅装备槽」是既有约定，混在一起会看不清 |

---

## 8. 影响面与风险

| 项 | 影响 | 对策 |
|---|---|---|
| `tools/selftest.py` | 新增断言（不删旧） | 新分区编号（现最大 `[56]`） |
| 锚点 / DPS | **零影响**（不碰 `rep['dps']` 与 `rotation.py` 的产出） | 验收标准：`selftest` 全绿 + 锚点逐位不变 |
| `PAIRS` | 若新增 `gd/defense.py` **无需镜像**（不是既有文件的改写）；若改 `gd/combat.py` 需 `--capture` | 尽量**新增而不改** `combat.py`（复用它，不重写） |
| 数据层 | 不新增数据表（全部来自现有 `combatformulas.json` / 存档 / 离线库） | — |
| 迁移 | 无需改 `migrate_from_archive.py` | — |

---

## 附录：证据位置

| 文件 | 用途 |
|---|---|
| `gd/combat.py` | 护甲 DLEP/DGP、吸收率、PTH 的**既有实现**（复用） |
| `data/combatformulas.json` | `combatformulas` 38 条官方公式（含 shield DGB/DLEB）+ `engine.playerDefenseCap` |
| `data/cache/calc.js` | `f63k`（抗性组成/上限）、`f66.*`（面板行构建） |
| `data/absorption_sources.md` | 第 ⑧⑨ 层的来源表 |
| `data/monster_stats.json` | 敌方 OA/DA/抗性/护甲（`derived.has_armor=false` ⇒ 无攻击伤害） |
| `docs/plan_dmg_model_v2.md` | 攻击侧同族方案（架构风格参照） |
