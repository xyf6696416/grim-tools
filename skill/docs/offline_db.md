# 离线数据库（Grim Tools 桌面版）结构说明

## 1. 为什么是它

`E:\Grim Tools` 是 Grim Tools（grimtools.com）的 **Electron 桌面版**。
它的 `resources/app.asar`（109 MB）里是**和网页版同源、但完整得多**的数据库 +
**未混淆的官方计算引擎**。整个工具链不再需要网络、不需要 .arz、不需要 CDP。

实测：asar 里的 `itemdb.js` 与网页抓的版本 **MD5 相同** ⇒ 数据没有"网页没有的东西"，
价值在于**完整性**、**13 种语言**、**官方公式可读**。

## 2. asar 格式（读取实现见 `gd/asar.py`）

```
[0:4]    = 4                      固定
[4:8]    = headerSize             pickle 总长
[8:12]   = headerSize + 4
[12:16]  = jsonLen                目录树 JSON 字节数
[16 : 16+jsonLen]  = JSON 目录树
文件数据区起点 = 8 + headerSize
```

asar 内共 **491 个文件**。

## 3. 关键文件

| 路径 | 大小 | 用途 |
|---|---|---|
| `/dist/db/itemdb/itemdb.js` | 8.7 MB | ★ **主数据库**（54 个顶层 `window.X=` 赋值） |
| `/dist/db/itemdb/itemdb_diff.js` | 14.8 MB | 版本更新明细（`versionUpdateDiffs.B27…B31` + Hotfix），**按需懒加载** |
| `/dist/db/itemdb/l10n/zh.js` | 1.0 MB | ★ 简体中文语言包（16563 条） |
| `/dist/db/itemdb/l10n/{en,cs,de,…}.js` | 各 ~1 MB | 另外 12 种语言 |
| `/dist/calc/calc.js` | 1.5 MB | ★★ **未混淆的官方计算引擎**（3035 行，有函数名） |
| `/dist/db/itemdb/db.js` | 216 KB | GT 前端的字段→标签映射表来源（用于生成 `data/gt_tables.json`） |
| `/dist/monsterdb/js/monsterdb.js` | 9.3 MB | 怪物详细数据 |
| `/dist/map/js/data.js` | 1.9 MB | 地图/区域/箱子/传送点 |
| `/dist/db/itemdb/skill_sprites/skills.json` | 102 KB | 技能图标精灵表 |
| `/dist/static.bundle` | 68 MB | 地图瓦片（GTTILES/RIFF+WebP），**不是数据** |
| `resources/bin/read_gd_save.exe` | 12 MB | GT 自己的存档读取器（未使用） |

## 4. 顶层键与条目数（`itemdb.js`，实测 1.3.0.8）

| 键 | 条目 | 说明 |
|---|---|---|
| `allItems` | 8612 | ★ 全部物品（含全部词条数值） |
| `prefixes` | 1745 | ★ 前缀 |
| `suffixes` | 2360 | ★ 后缀 |
| `itemSets` | 199 | ★ 套装（成员 + 逐档加成数组） |
| `itemSkills` | 4268 | ★ 物品授予的技能（**含数值**） |
| `dbMasteryData` | 326 | 专精技能注册表（**只有 `{name: tag}`**，数值见下） |
| `monsters` | 1433 | 怪物（type/tag/所在难度） |
| `MIRefs` | 2516 | 怪物 → 专属掉落 |
| `buffSkills` | 159 | buff 技能 |
| `player` | 31 键 | `skillTree1..10`（技能树布局）、各基础属性公式 |
| `playerBio` | 14 键 | **升级给点表**（`skillPointsIncrement`） |
| `engine` | 9 键 | 里程碑、各上限、自动施法公式 |
| `combatformulas` | 38 | ★ 属性 → 伤害的官方公式 |
| `itemSkillControllers` | 70 | 物品技能触发控制器 |
| `containers` / `containerRefs` | 139 / 261 | 箱子与引用（掉落来源） |
| `roguelikeChests(+Items)` | 55 / 376 | 罗格类宝箱 |
| `shatteredRealmChests(+Items)` | 11 / 22 | 破碎界宝箱 |
| `crafters` / `crafterRecipes` | 19 / 128 | 铁匠与图纸 |
| `merchants` / `merchantItems` | 44 / 1612 | 商人与在售物品 |
| `factions` | 15 | 派系 |
| `questItems` | 92 | 任务奖励物品 |
| `ascensionAffixData` | 989 | 飞升（GDX3）词缀 |
| `versionDiffs` / `versionChangeTotals` | 84 / 83 | 版本差异索引 |
| `shortNameMapping` | 52 | ★ **短键 → 全名**（`a`/`b`/`f`/`h`/`i`/`k`/`l`/`o`/`p`…） |
| `db_l10n` | 13 | 语言列表 |
| `maps`（来自 `/dist/map/js/data.js`） | 186 | 地图 |

## 5. 语言包

`l10n/<lang>.js` 的形式是 `db_l10n_texts['<lang>'] = { tag: "文本", … }`。

文本**不是纯字符串**，而是带 GT 格式指令的模板：
`tagCharAttackSpeed` → `"{%+.0f0}% {^E}攻击速度"`。
渲染引擎见 `gd/text.py`，语法见 `refs/formulas.md`。

## 6. 短键（`shortNameMapping` 全表）

```
itemNameTag                 a      itemText                    b
lootRandomizerName          c      description                 d
itemSetName                 e      itemClassification          f
armorClassification         g      attributeScalePercent       h   ← 等级缩放%
lootRandomizerJitter        i      levelRequirement            k   ← 等级需求
Class                       l      itemCostName                m
bitmap                      n      characterBaseAttackSpeedTag o
characterBaseAttackSpeed    p
WeaponMelee_Dagger  c24    WeaponMelee_Scepter c25   WeaponArmor_Offhand  c26
WeaponArmor_Shield  c27    WeaponMelee_Sword2h c28   WeaponMelee_Axe2h    c29
WeaponMelee_Mace2h  c30    WeaponHunting_Ranged2h c31 WeaponMelee_Spear2h c32
WeaponMagical_Staff c33    WeaponMelee_Sword   c20   WeaponMelee_Axe      c21
WeaponMelee_Mace    c22    WeaponHunting_Ranged1h c23
ArmorProtective_Head c10   Shoulders c11   Chest c12   Hands c13   Waist c14
Legs c15   Feet c16   ArmorJewelry_Ring c40   Amulet c41   Medal c42
ItemArtifact c43   ItemEnchantment c44   ItemRelic c45   OneShot_Scroll c46
QuestItem c47   ItemArtifactFormula c48   OneShot_SkillUnlock c49
tagAttackSpeedVerySlow d1  Slow d2  Average d3  Fast d4  VeryFast d5
```

## 7. 离线库**没有**的东西（重要）

* **职业/专精技能的逐级数值** —— `dbMasteryData[skXXXX]` 只有 `{name: tag}`，
  技能树布局在 `player.skillTree1..10` 里也只有 id 引用。
  真正的数值在游戏的 `database.arz`。
  ⇒ 已用旧技能从 .arz 抽出的 `skills.json` 裁剪成 `data/mastery_skills.json`（356 条，
  按 **tag** 与离线库对齐：`dbMasteryData['sk1235'].name` = `tagClass04SkillName07A`）。
  重生成：`python tools/migrate_mastery_skills.py <旧skills.json>`
* **星座（虔诚）的完整星图** —— 在 `calc.js` 的 `devotionConstellationNN` 里（字段被混淆成
  `Ga`/`Ha`/`affinityRequiredName1`…）。本技能暂用迁移来的 `data/devotion_tree.json`
  （110 个星座：tag/亲和力/节点技能/连线）。完整星图待破译。
* 物品的**运行时 roll 值** —— 只有区间，真实 roll 要游戏提示框。

## 8. 溯源

`data/gt_tables.json` 由 `tools/dump_tables.js` + `tools/scan_label_pairs.js`
从 `/dist/calc/calc.js` 与 `/dist/db/itemdb/db.js` **逐字导出**，
字段→标签对 (62 条) 经中文语言包（16563 tag）校验后保留。

```bash
node tools/dump_tables.js     <calc.js>                    > data/gtc_tables.json
node tools/scan_label_pairs.js <db.js> <calc.js>            > label_pairs.jsonl
# 再用 l10n 校验 label_pairs 里的 tag 是否存在，合并成 field_labels
```

## 9. ★ 记录路径 ↔ GT id：必须读游戏 `.dbr`，不能靠规则推

离线库**只按 `itXXXX` 索引，不带 `.dbr` 记录路径**；而存档里存的是记录路径。
两边要互通就必须有一张映射表。**这张表不能用命名规则推**，实测反例：

```
records/items/gearweapons/melee2h/c204_sword2h.dbr
    itemNameTag = tagGDX2WeaponMelee2hC202      ← 记录写 c204、标签写 C202！
```

⇒ 规则推导覆盖率只有 79.1%，且**会把武器基础伤害算成 4**（真实 201~259）。

正确做法（`tools/build_exact_map.py`，一次性，约 35 s）：

```
① 逆向游戏 .arz：解码每条记录，收集它引用的**全部 `tag*` 字符串字段**
   （普通物品 itemNameTag、组件/圣物/附魔走别的字段、学识笔记又不同 →
    因此不能用字段白名单，必须全扫。共 27853 条记录 / ~45k tag 引用）
② 与离线库按 **标签 + 等级** 精确对齐（物品用 itemLevel，词缀用 levelRequirement）
③ 仍分不清时依次消歧：
     · 词缀 tag 尾部字母 = 适用位变体（tagPrefixB020_Class_A → b_class020_a03）
     · 内容指纹 hash = 识别「纯重复副本」（如 b_class021_shaman01..27 共 27 条
       622 字段全同 → 互相可替换 → 反向表全部认）
     · 数值字段逐个比对（offensive*/character*/defensive* …）
     · 图纸靠 artifactName、唯一词缀靠 onlyForItems / 技能改造对象 反查
```

产出 `data/record_map.json`：

| 表 | 条目 | 用途 |
|---|---|---|
| `fwd` | 8509 | GT id → 记录（**写盘用**） |
| `rev` | 8494 | 记录 → GT id（**读档用，只要唯一确定的**） |
| `affix_fwd` / `affix_rev` | 3928 / 4390 | 词缀 |
| `low` / `affix_low` | 26 / 236 | 仍分不清：**只写正向、不写反向** |

未匹配（GT 无名称标签）：103 物品 + 177 词缀 → `data/record_map.unmatched.json`。

`tools/legacy/` 里保留 `gd_arz.py` / `gd_dbr.py`（要 lz4 解 `.arz` 的 RD 段），
**只在重建映射时用，不进运行时依赖**。
