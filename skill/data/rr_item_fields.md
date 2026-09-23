# 降低目标抗性的字段清单（物品 / 技能）

> 自动生成：`python tools/rr_items.py --write`。
> 判据与 `gd/rr.py::rr_of` 逐字同源；`GD` 里这三族的**结算顺序是 B → C → A**，
> 且**敌抗可成负、不锁 0**（见 `docs/rr_mechanics.md`）。

## B 族 · 负 `defensive<类型>`（**叠加**） ｜ 命中 **764** 条字段实例

- 来源：物品技能(装备授予) **491** ／ 怪物技能 **159** ／ 专精技能 **104** ／ 星座 proc **10**
- 字段：`defensiveLife`×99、`defensiveAether`×76、`defensivePhysical`×73、`defensiveElementalResistance`×73、`defensiveBleeding`×68、`defensivePoison`×68、`defensiveChaos`×67、`defensiveAllResistance`×67、`defensivePierce`×50、`defensiveFire`×48、`defensiveCold`×47、`defensiveLightning`×28

| 值 | 名称 | 字段 | 抗性桶 | 来源 |
|---|---|---|---|---|
| 33 | 自然之怒 | `defensiveElementalResistance` | 火/冰/电 | 怪物技能 |
| 33 | 悲痛 | `defensiveElementalResistance` | 火/冰/电 | 物品技能(装备授予) |
| 33 | dermapteran_infestor_a | `defensiveFire` | 火焰 | 怪物技能 |
| 33 | reddan_curse1.dbr | `defensiveLife` | 活力 | 怪物技能 |
| 33 | 悲痛 | `defensiveElementalResistance` | 火/冰/电 | 物品技能(装备授予) |
| 30 | 凝血注射 | `defensiveBleeding` | 流血 | 怪物技能 |

## C 族 · `…ResistanceReductionPercentMin`（**取最高**） ｜ 命中 **16** 条字段实例

- 来源：物品技能(装备授予) **14** ／ 星座 proc **2**
- 字段：`offensiveTotalResistanceReductionPercentMin`×10、`offensiveElementalResistanceReductionPercentMin`×6

| 值 | 名称 | 字段 | 抗性桶 | 来源 |
|---|---|---|---|---|
| 26 | petskill_livingplant_p | `off.TotalResistanceReductionPercentMin` | 全部 10 种 | 物品技能(装备授予) |
| 25 | 寇瓦克的复仇 | `off.ElementalResistanceReductionPercentMin` | 火/冰/电 | 物品技能(装备授予) |
| 25 | set_d309_markovianadva | `off.TotalResistanceReductionPercentMin` | 全部 10 种 | 物品技能(装备授予) |
| 24 | set1_soldier5.dbr | `off.ElementalResistanceReductionPercentMin` | 火/冰/电 | 物品技能(装备授予) |
| 24 | set3_soldier1.dbr | `off.ElementalResistanceReductionPercentMin` | 火/冰/电 | 物品技能(装备授予) |
| 20 | 时光转移 | `off.ElementalResistanceReductionPercentMin` | 火/冰/电 | 物品技能(装备授予) |

## A 族 · `…ResistanceReductionAbsoluteMin`（**取最高**） ｜ 命中 **76** 条字段实例

- 来源：物品技能(装备授予) **57** ／ 专精技能 **9** ／ 星座 proc **8** ／ 怪物技能 **1** ／ 其他 **1**
- 字段：`offensiveTotalResistanceReductionAbsoluteMin`×69、`offensiveElementalResistanceReductionAbsoluteMin`×4、`offensivePhysicalResistanceReductionAbsoluteMin`×3

| 值 | 名称 | 字段 | 抗性桶 | 来源 |
|---|---|---|---|---|
| 80 | mogdrogen_lightningdis | `off.ElementalResistanceReductionAbsoluteMin` | 火/冰/电 | 怪物技能 |
| 40 | necklace_d314_cadence. | `off.ElementalResistanceReductionAbsoluteMin` | 火/冰/电 | 物品技能(装备授予) |
| 38 | mace2h_d208_cadence.db | `off.TotalResistanceReductionAbsoluteMin` | 全部 10 种 | 物品技能(装备授予) |
| 35 | gun2h_d104_grenado.dbr | `off.TotalResistanceReductionAbsoluteMin` | 全部 10 种 | 物品技能(装备授予) |
| 35 | gun2h_d104_viremight.d | `off.TotalResistanceReductionAbsoluteMin` | 全部 10 种 | 物品技能(装备授予) |
| 35 | head_b315_fieldcommand | `off.TotalResistanceReductionAbsoluteMin` | 全部 10 种 | 物品技能(装备授予) |
