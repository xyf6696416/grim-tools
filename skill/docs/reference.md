# 参考：公式 / 字段 / 迁移 / 基线 / 映射表 / 自检

> 由 SKILL.md §5/§6/§8/§10/§11/§13 拆出。

## 5. 官方公式（已经可以直接读源码，不用再猜）

```python
db.fmt.rc(27, 60)          # -> [35, 51]   官方区间
db.fmt.af(27, 60)          # -> 43.0       提示框显示值 = 区间中点
db.scaler.value("offensivePierceMin", 27, item["h"], item.get("i"))
```

* `rc(base, attributeScalePercent, jitter)` —— 区间 `[lo, hi]`，可直接和游戏提示框 `[35-51]` 对照
* `Af = (lo+hi)/2` —— 提示框的"显示值"
* **只有 `offensive*` 开头**才拿得到物品的 `attributeScalePercent`（字段 `h`）；其余拿到 0 ⇒ 不缩放
* **两张黑名单**（`Mf`/`Bf` 23 项 + `sd` 12 项）：`offensiveCritDamageModifier`、吸血、嘲讽、燃魔、经验、光照、总速度……
* **值为 0 时压根不调缩放**（否则 `rc(0,g)` 会算出 `-0.5`，总伤害偏低约 3.5%）

公式细节与实测对照见 **`refs/formulas.md`**。

---

## 6. 字段与标签

离线库的字段名里有一批**短键**（见 `shortNameMapping`），必须知道：

| 短键 | 含义 | 短键 | 含义 |
|---|---|---|---|
| `a` | 名称标签 | `h` | **attributeScalePercent**（等级缩放%） |
| `b` | 描述标签 | `i` | lootRandomizerJitter（默认 20） |
| `c` | 词缀随机名标签 | `k` | **等级需求** |
| `f` | 品质（Common/Magical/Rare/Epic/Legendary/Quest） | `l` | 类别代码（`c24`=匕首，反查 `short_names`） |
| `n` | 图标路径 | `o` | 攻速档代码（`d5`=非常快） |
| `p` | 基础攻速系数 | `h`+`i` | 算真值必须的两个参数 |

**字段 → 中文标签**是组合式生成的（`db.field_tag()`）：

```
offensiveBase<Type>Min/Max  -> tagDamageBase<Type>      (Life→Vitality)
offensive<Type>Min/Max      -> Damage<Type>
offensive<Type>Modifier     -> DamageModifier<Type>
offensiveSlow<Type>Min/Max  -> DamageDuration<Type>
offensiveSlow<Type>Modifier -> DamageDurationModifier<Type>
character* / defensive*     -> 显式字典（62 条，经中文语言包校验）
```

渲染时数值走 `value_label()`：模板缺 `%` 占位符的（如 `DamageDurationPoison` = "点毒素伤害"、
`LevelRequirement` = "玩家等级"）会自动补位，**保证数值不丢**。

---


## 8. 迁移状态

| 能力 | 状态 |
|---|---|
| 离线库加载 / 物品 / 词缀 / 套装 / 技能 / 怪物 / 星座 / 地图 / 给点表 | ✅ |
| 中文名 + 官方缩放真值 + 官方区间 | ✅ |
| 专精技能逐级数值（tag 桥接，356 条） | ✅ |
| **存档只读解析**（.gdc/.gst，15 块，加密/校验） | ✅ 实测 5 个角色全通过 |
| **中文角色报告**（装备/技能/战绩/声望/进度） | ✅ `gd save Sam` |
| **备份 / 校验 / 恢复 / 逐字段 diff** | ✅ `gd backup` / `gd verify` |
| **一键换档（列备份 → 恢复最新）** | ✅ ★ `tools/gd_restore.py`（默认**只恢复目标角色**；`tools/gd_restore.cmd` 双击即用） |
| **写盘前安全检查 + 统一体检** | ✅ `gd verify --preflight` |
| **定长补丁 / 变长写入 / 背包增删** | ✅ 已迁移（`gd/save/patch.py`、`write.py`、`inv.py`） |
| **DPS 模型 / 输出循环** | ✅ `gd dps Sam` 出数 |
| **属性需求闸门（官方公式）** | ✅ `tools/reqcheck.py`：按记录名逐件需求 + 缺口；**含武器需求** |
| **属性自动拟合（写档即求解）** | ✅ ★★ `gd/reqfit.py` —— `gd.save.patch` / `gd build apply` **自动按装备调三围** |
| **「穿不上」自动重排属性点** | ✅ 三条链路共用 `gd.reqfit`（`fix_wearability.py` 为薄封装） |
| **精通逐级属性曲线** | ✅ `data/mastery_attr.json` + `gd.rotation.mastery_attr_of()` |
| **装备优化器 / BD 配方 / 计划解释** | ✅ `gd.opt` / `gd.recipe` / `gd.explain` 跑通 |
| 记录桥 `记录路径 ↔ itXXXX` | **98.8%**（8509/8612），其中 8494 唯一确定；低置信不写反向表 |
| 词缀映射 `记录 ↔ pre/sufXXXX` | **95.7%**（3928/4105），4390 唯一确定 |
| 桥表来源 | ★ **直接读游戏 .dbr 的 `itemNameTag`**（不再靠命名规则推导） |

**迁移方式**：`tools/migrate_from_archive.py` —— 机械搬运 + 只补三类接缝
（模块路径 / 环境探测 / 写死路径），可复跑、可审计。详见 `refs/status.md`。

**已知缺口**（交付时要说清楚）：
* 少数词缀家族的**多档对齐**档数不吻合（GT 18 档 vs 库 20 文件）→ 不写入反向表。
  原则：**宁可不认，也不认错**（认错会让 DPS/改档全盘偏）。
* 旧脚本里少数**字符串形式的动态导入**由 `gd/__init__.py` 的别名查找器兜住；
  已废弃模块（`gd_local_tip` 等）返回 no-op 空模块。
* 存档报告里极少数无中文 tag 的技能会显示英文原名。


## 10. 自检与性能基线

```bash
$PY tools/selftest.py     # 379 项断言：离线库 + 官方公式 + 渲染 + 存档层 + 权威映射 +
                          #   属性需求闸门 + 真源完整性 + ILP 满抗搜索 + 减抗/转化/构建回归 +
                          #   [17-22] 伤害模型 v2（官方常数 / 敌方真值 / 基准锚点 / 端到端 /
                          #          权重表自洽 / 游戏内实测通道）
                          #   [24] 敌方面板复现 / [25] 物品库总览+候选池 /
                          #   [26] 并行搜索护栏 / [27] 转化·WPS·形态主输出 /
                          #   [28] 词缀库解析 + 体检工具链 /
                          #   [29] LNS 搜索器（替代退火）+ 报告伤害循环段 /
                          #   [30] 邻域并行（ParEval）+ 三处缓存（save_dir/fields/fold）
$PY tools/bench.py        # 主要命令耗时
```

> ⚠ **性能基线里的 DPS 数字是 v1 时代的**（`94,126` / `38,249` 等），
> 伤害模型 v2 已修正 DoT 口径 ⇒ 时间指标仍有效，**DPS 数字请以 §13 的当前基准表为准**。

**性能基线**（2026-09-19，托管 venv，冷/热缓存）

| 命令 | 耗时 | 说明 |
|---|---|---|
| 裸解释器 | 0.11 s | 基线，无法再压 |
| `import gd` | +0.04 s | 包导入 |
| `DB.load()`（缓存命中） | +0.07 s | 11 MB pickle |
| `gd item` / `find` | 0.30 / 0.34 s | 大头是解释器启动 |
| `gd chars` / `verify Sam` | 0.28 / 0.23 s | |
| `gd save Sam` | 0.38 s | |
| `gd dps Sam` | **1.2 s** | 优化前 6.1 s（**5×**） |
| `gd opt`（默认束宽 700） | 0.84 s | |
| `gd opt --beam 2000 --restart 2` | 3.15 s | |

**做过的优化**（结果逐位不变，已用 `selftest` + DPS 数字对照验证）

1. ★ `dps` 建「记录 → GT id」反查表原本要遍历 8612 件物品跑 `resolve()` 规则推导
   → **改用权威桥表**：6.1 s → 1.2 s。
2. ★ `savemap.resolve()` 加**权威桥表 O(1) 快速路径** + 记忆化，
   避免每次都落到 O(全库) 的 `_family_by_prefix`。
3. ★ 大 JSON 统一走 `paths.load_json()`：按 (mtime,size) 落 **pickle 缓存**（快 3~5 倍）
   —— 覆盖 `record_map` / `skills` / `mastery_skills` / `devotion_*` / `gt_tables`。
4. 离线库 pickle 缓存 11 MB，命中 **0.07 s**（首次 1.6 s）。
5. `itemdb_diff.js`（14 MB）与 `mastery_skills` / `devotion_skills` 全部**按需懒加载**。
6. 缓存文件名用 `hashlib` 稳定哈希（内置 `hash()` 每进程随机化，曾生成 15 个 27 MB 文件）。


## 11. 权威映射表（★ 本轮重做的关键件）

**问题**：靠命名规则从 GT 标签推记录名，覆盖率只有 79.1%，而且**会算错**：
`c204_sword2h.dbr` 的记录名是 `c204`，但它的 `itemNameTag` 是 `tagGDX2WeaponMelee2hC202`。
规则永远找不到它 → 武器基础伤害算成 4（真实 201~259）。

**做法**（`tools/build_exact_map.py`，一次性构建，约 35 s）：

```
1. 逆向游戏 .arz：解码每条记录的 itemNameTag / lootRandomizerName / skillDisplayName …
   （全库扫描所有 `tag*` 字符串字段，共 27853 条记录 / 45k 个 tag 引用）
2. 与离线库按 **标签 + 等级** 精确对齐（itemLevel / levelRequirement）
3. 仍分不清时依次用：
     ① 词缀 tag 尾部字母 → 适用位变体（tagPrefixB020_Class_A → b_class020_a03）
     ② 内容指纹 hash → 识别「纯重复副本」（27 条完全相同的记录视为可互换，全部认）
     ③ 数值字段逐个比对 → 收敛到唯一
     ④ 图纸靠 artifactName、唯一词缀靠 onlyForItems / 技能改造对象 反查
```

**产出** `data/record_map.json`（1.6 MB）：

| 表 | 条目 | 含义 |
|---|---|---|
| `fwd` / `rev` | 8509 / 8494 | 物品 GT id ↔ 记录路径（**读取用 rev，写盘用 fwd**） |
| `affix_fwd` / `affix_rev` | 3928 / 4390 | 词缀 |
| `low` / `affix_low` | 26 / 236 | 仍分不清的，**只写正向、不写反向**（宁可不认，也不认错） |

未匹配 103 物品 + 177 词缀（约 2.2%）是 GT 里没有名称标签的条目，
清单落在 `data/record_map.unmatched.json`。

重建：
```bash
$PY tools/build_exact_map.py        # 需要游戏 .arz（只读，不进运行时依赖）
```


## 13. 自检

```bash
$PY tools/selftest.py        # 379 项断言，全绿才算好
                             #   [13] 真源完整性 / [15] ILP / [16] 减抗·转化·构建回归
                             #   [17] 官方常数自证（PTH/护甲/DoT）/ [18] 敌方真值
                             #   [19] 基准锚点 v1↔v2 / [20] Sam 端到端 / [21] 权重表自洽
                             #   [22] 游戏内实测通道 / [25] 物品库总览+候选池自洽
                             #   [26] 并行搜索护栏 / [28] 词缀库解析 + 体检工具链
                             #   [29] LNS 搜索器 + 伤害循环段（官方权重口径三项断言）
                             #   [30] 邻域并行 ParEval + 三处缓存（16 项）
                             #   [23] 护甲/破甲逐类型裁决
                             #   [24] 敌方面板复现（m3955 vs 游戏截图，13 项）+ 护甲自动套用契约 4 项
                             #   [25] 物品库「总览」对拍（9 行 == grimtools 站点）+ 候选池自洽 8 项
$PY tools/cpu_probe.py 45    # 实测 CPU 到底跑没跑满（对比 16 链 / 32 链）
$PY tools/sync_live.py       # gd/*.py 是否与保命副本一致（只报告）
$PY tools/sync_live.py --apply    # 不一致 -> 用副本抢修活文件（见 §7-23）
$PY tools/sync_live.py --capture  # 反向：改完代码后刷新副本（★ 义务，别忘）
$PY tools/gt_regress.py --ids ZyDo860V --golden   # 引擎数值回归（改了 gd/ 就跑）
$PY tools/gt_regress.py --sheet Sam               # 游戏内面板真值对拍（见 §3.11.1）
```

**当前基准（2026-09-19，伤害模型 v2）**

| 基准 | 值 | 说明 |
|---|---|---|
| `Sam` 面板 DPS | **37,122.7** | `python -m gd dps Sam`（v1 为 38,249，−2.9% = DoT 覆盖率修正） |
| `Sam` 实战 DPS | **36,051.0** | 面板 × 命中期望 `0.9711`（PTH 93.23 < 100 ⇒ 6.8% 未命中） |
| `Sam` 对怪（终极池）| **49,010** | 叠敌方**逐桶真值**抗性（×1.36） |
| `Sam` OA / DA / PTH | 1694.8 / 1308.5 / 93.23 | 暴击率 4.23%、暴伤 82% |
| `Sam` DoT 通道 | 9 个（占面板 ~24.6%）| `rep['dot']['channels']` |
| `ZyDo860V`（grimtools）| **119,741.7** | `gt_regress --golden`（v1 为 116,103） |
| 权重表 `crit` | **0.53** | 由暴击口径推导；写死值 1.00 高估 1.88 倍 |

`sync_live.py` 现在管 **22 对**（详表见 `tools/sync_live.py::PAIRS`）：

| 保命副本 | 正本 |
|---|---|
| `tools/gd_req_official.py` | `gd/req.py` |
| `tools/gd_rotation_patched.py` | `gd/rotation.py` |
| `tools/gd_reqfit_patched.py` | `gd/reqfit.py` |
| `tools/gd_paths_patched.py` | `gd/paths.py` |
| `tools/gd_alloc_patched.py` | `gd/alloc.py` |
| `tools/gd_build_patched.py` | `gd/build.py` |
| `tools/gd_dps_patched.py` | `gd/dps.py` |
| `tools/gd_opt_patched.py` | `gd/opt.py` |
| `tools/gd_rr_patched.py` | `gd/rr.py` |
| `tools/gd_save_backup_patched.py` | `gd/save/backup.py` |
| `tools/gd_save_verify_patched.py` | `gd/save/verify.py` |
| `tools/gd_save_patch_patched.py` | `gd/save/patch.py` |
| `tools/gd_combat_patched.py` | `gd/combat.py` |
| `tools/gd_dmg_patched.py` | `gd/dmg.py` |
| `tools/gd_enemy_patched.py` | `gd/enemy.py` |
| `tools/gd_cli_patched.py` | `gd/cli.py` |
| `tools/gd_dbr_patched.py` | `gd/dbr.py` |
| `tools/gd_planreport_patched.py` | `gd/planreport.py` |
| `tools/gd_skillprov_patched.py` | `gd/skillprov.py` |
| `tools/gd_procs_patched.py` | `gd/procs.py` |
| `tools/gd_dmgcycle_patched.py` | `gd/dmgcycle.py` |
| `tools/gd_resaudit_patched.py` | `gd/resaudit.py` |

改完任一文件**立刻** `--capture` 刷新副本；长操作前后各跑一次 `sync_live.py`。

> ★★ **这一层现在被迁移脚本承认了**：`migrate_from_archive.py` 末尾的
> **步骤 24「权威覆盖」**会按同一份 `PAIRS` 用副本回填活文件 ——
> 所以「重跑迁移」不再丢功能（2026-09-19 实测：临时副本重跑 → 与迁移前**零差异**）。
> 副作用：`PAIRS` 就是「重写级改动」的登记表，新增大改必须加进去。
> 若副本缺失，迁移**直接失败退出**（不会产出「看起来成功、其实缺功能」的 skill）。
> **判断该进表还是该写 patch 步骤**：判据不是文件大小，而是「改动能不能用
> **稳定锚点的字符串替换**表达」—— 能就写 patch 步骤，不能就进 `PAIRS`。
