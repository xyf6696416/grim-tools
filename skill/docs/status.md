# 迁移状态 · 旧代码对照 · 路线图

## 1. 起点与终点

**起点**：旧技能 `grim-dawn-save` —— 6106 行 SKILL.md（374 KB）、60+ 脚本、69 MB 缓存，
功能重叠、文档膨胀，地基是「解析 .arz + 抓 grimtools 网页 + CDP」。

**终点**：`grim-dawn` —— 地基换成离线数据库，数据本地直读；文档索引化；
脚本收敛为一个可 import 的包 + 一个 CLI。**旧功能已全部迁移完毕**。

## 2. 旧技能的归档

```
C:\Users\Administrator\.workbuddy\skill_archive\
    grim-dawn-save_2026-09-19.zip    1598 文件 / 65.4 MB → 12.1 MB（testzip 通过）
    grim-dawn-save\                  原目录整体移出，未删除，可随时取回
```

## 3. 迁移方式

`tools/migrate_from_archive.py`：**机械搬运 + 只补三类接缝**，可复跑、可审计。

```
FILES  24 个模块    →  gd/ 与 gd/save/
DATA   14 个数据文件 →  data/（记录池 pkl、skills.json、e_skills.json…）
```

补的接缝只有三类：

| # | 接缝 | 做法 |
|---|---|---|
| 1 | **模块路径** | `import gd_save as S` → `from .save import core as S`；带缩进的**函数体局部 import** 也一并处理 |
| 2 | **环境探测** | `gd_env` / `gd_timing` → `gd/_legacyenv.py`（接 `gd.paths`）/ `gd/_timing.py`（no-op 埋点） |
| 3 | **写死的绝对路径** | `gd_backup` 里写死的 `userdata\1016762644`、用户名 → 由 `paths` 探测（换机器不再失效） |

再补 4 个**语义适配**（让上层一行都不用改）：

| 适配 | 说明 |
|---|---|
| `gd.savemap` 的池缓存 | 改为「直接采用随技能发布的 `data/arz_*.pkl`」，**不再用 .arz 签名卡失效**（运行时不依赖 .arz） |
| `gd.savemap.gt_items()` | 接 `gd.DB`，且**必须合并物品+词缀**（只给 allItems 会让词缀解析 100% 失败） |
| `gd.gear.load_items/load_tags` | 接 `gd.DB`（旧实现自己写正则解 itemdb.js，只取字符串/数值） |
| `gd.dbr`（新写的兼容层） | 旧 `gd_dbr` 要 lz4 解 `.arz` 的 RD 段；新 shim 从离线库取字段，并**把标量包成单元素列表**以符合 `.dbr` 的「字段即数组」语义 |

另加一个 `gd/__init__.py` 里的**惰性别名查找器**，兜住旧脚本的**字符串动态导入**
（`__import__('gd_rotation')`）。已废弃模块（`gd_local_tip` 等）返回 no-op 空模块。

## 4. 模块对照表

| 新位置 | 旧脚本 | 说明 |
|---|---|---|
| `gd/save/core.py` | `gd_save.py` | 解密读器 / 编码器 / 15 块解析 |
| `gd/save/write.py` | `gd_write.py` | 变长写入（换装、增删条目） |
| `gd/save/patch.py` | `gd_edit.py` | 定长补丁 |
| `gd/save/inv.py` | `gd_inv.py` | 背包增删复制 |
| `gd/save/backup.py` | `gd_backup.py` | 备份 / 校验 / 恢复 |
| `gd/save/verify.py` | `gd_verify.py` | 统一体检 + 写盘前安全检查 |
| `gd/save/diff.py` | `gd_diff.py` | 逐字段 diff |
| `gd/save/skill.py` | `gd_skill.py` | 技能落档 |
| `gd/save/report.py` | （新） | 中文角色报告 |
| `gd/save/items.py` | （新） | 记录路径 ↔ GT id 桥 |
| `gd/savemap.py` | `gd_map.py` | GT id ↔ 记录路径（规则：名标签家族 + 多档按等级 `k` 对齐） |
| `gd/dbr.py` | `gd_dbr.py` | 字段级读取（shim，免 lz4） |
| `gd/gear.py` | `gd_gear.py` | 物品库 / 语言包加载 |
| `gd/opt.py` | `gd_opt.py` | 装备优化器（束搜索，numpy 向量化） |
| `gd/rotation.py` | `gd_rotation.py` | 输出循环 / 最终伤害模型 |
| `gd/dps.py` | `gd_dps_check.py` | 存档实测 DPS 校验 |
| `gd/recipe.py` / `gd/gen.py` | `bd_recipe.py` / `bd_gen.py` | BD 配方与生成 |
| `gd/alloc.py` / `gd/req.py` / `gd/skillmod.py` / `gd/devotion.py` | 同名 | 加点 / 需求建模 / 装备改造 / 星座 |
| `gd/mana.py` / `gd/compare.py` / `gd/planreport.py` / `gd/explain.py` / `gd/loop.py` | 同名 | 续航 / 存档对比 / 计划报告 / 计划解释 / 收敛循环 |
| `gd/asar.py` `gd/jsobj.py` `gd/db.py` `gd/text.py` `gd/scale.py` `gd/render.py` `gd/paths.py` `gd/cli.py` | （新） | 离线库地基 |

### 直接淘汰（被离线库取代）

`gd_arz.py` / `gd_arc.py`（.arz/.arc 解析，**顺带甩掉 lz4 依赖**）、
`gt_fetch.py` / `gt_page.py` / `cdp_eval.py` / `cdp_js/`（网页 + CDP）、
`gt_extract.py`（.arz → 技能库，改为一次性迁移）、`gt_offline.py`（→ `gd/asar.py`）、
`gt_scale_index.py` / `gt_tooltip.py` / `gd_local_tip.py`（→ 官方公式）、
`gd_text/`（手抄中文字典 → 语言包 16563 条）、`ce_auto.py` / `gd_mem.py`（内存改档实验）。

## 5. 已验证（`tools/selftest.py` 全绿）

```
✅ 环境探测 / 离线库加载（缓存 0.1s）
✅ 官方公式：rc(27,60)==[35,51]、0 值不缩放、黑名单、character* 不缩放
✅ 格式引擎：+14% 攻击速度 / 38 火焰伤害 / +182% 穿刺伤害
✅ 中文名：物品 / 英文名 / 套装 / 专精技能 / 怪物 / 类别反查
✅ 渲染：物品卡片（含区间）/ 套装逐档 / 词缀可附部位 / 技能逐级数值
✅ 搜索与过滤 / 派生数据（给点表、356 条专精技能、110 星座）
✅ 存档层：5 个角色解析、15 块全 OK、记录桥 6816/5703、词缀中文名、角色报告、gd_dbr 兼容层
```

```
✅  python -m gd chars / save / verify --preflight / verify Sam
✅  python -m gd dps Sam          （武器基础 穿刺 201~259；野性利爪 30746/s；合计 38255/s）
✅  python -m gd opt --goal dmg    （numpy 引擎，覆盖率 100% = 1070/1070）
✅  gd.explain / gd.planreport     （跑通并输出中文方案）
✅  tools/selftest.py              （51 项断言全绿）
```

★ **武器基础伤害修复**：桥表换成权威版后，`gd dps Sam` 的武器基础从
「穿刺 4」变成「穿刺 201~259」，合计每秒伤害 21966 → **38255**。
根因是 `c204_sword2h.dbr` 的记录名与它的 `itemNameTag`（C202）不一致，
旧规则永远找不到它。

## 6. 已知缺口

1. **仍分不清的记录**：物品 26 条 / 词缀 236 条 —— 只写正向（供写盘），
   **不写反向**（读取宁可不认）。原则不变：认错会让 DPS 与改档全盘偏。
2. **GT 里没有名称标签的条目**：103 物品（多是蓝图）+ 177 词缀（职业专属词缀），
   清单在 `data/record_map.unmatched.json`。这些在存档里出现时会退回显示记录基名。
3. **`gd.opt` / `gd.recipe` 是脚本式模块**（模块级有副作用，import 就会跑一整轮搜索）。
   CLI 因此走独立子进程转发；**不要在代码里 import 它们**。
   旧 `bd_recipe` 里靠 `subprocess` 调脚本的编排尚未改成进程内调用。
4. **`comp_bladeaura_02` 的 156 仍未解**（本轮补充了证据）：
   记录是 `skill_buffselftoggled`、`skillMaxLevel: 1`、`offensivePierceMin: 10`、
   `offensivePierceModifier: 75`，用官方公式只能得到 10；
   而游戏提示框显示 156。已确认该记录唯一、无 `_buff` 变体、无同族第二版本。
   ⇒ 推定为**运行时按物品等级放大**（组件 aura 的 itemLevel 缩放），机制未查明。
   **以游戏内提示框为准。**
5. 职业专精技能数值来自**一次性 .arz 迁移**（356 条）；游戏更新或新增职业后需重跑
   `tools/migrate_mastery_skills.py`。
6. 星座只有「亲和力 + 节点技能」，**完整星图连线未破译**；
   但**星位中文名已解决**（`tier1_08e_skill` → `tagDevotionEffectA08` → 「暗杀者的标记」）。
7. 物品只有**官方区间**，没有运行时 roll 真值 —— 单件装备的精确数值仍需游戏提示框。

## 7. 重建脚本

```bash
PY="C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe"

$PY tools/migrate_from_archive.py     # 从归档重搬脚本 + 数据（幂等，可反复跑）
$PY tools/build_exact_map.py          # ★ 从游戏 .dbr 重建权威映射（需 .arz，约 35 s）
$PY tools/migrate_mastery_skills.py <旧 skills.json>   # 重建专精技能表
$PY tools/selftest.py                 # 51 项断言
$PY tools/bench.py                    # 性能基线
```

## 8. 性能基线（2026-09-19）

| 命令 | 优化前 | **优化后** |
|---|---|---|
| `gd dps Sam` | 6.06 s | **1.2 s**（5×） |
| `gd save Sam` | 0.51 s | **0.38 s** |
| `gd opt`（默认） | — | 0.84 s |
| `gd opt --beam 2000 --restart 2` | — | 3.15 s |
| `gd item` / `gd find` | 0.29 / 0.33 s | 0.30 / 0.34 s |
| `DB.load()` 缓存命中 | 0.11 s | 0.07 s |
| 全部用例合计 | 9.7 s | **5.0 s** |

主要手段（结果逐位不变）：
1. `dps` 建反查表不再遍历全库跑规则 → 用权威桥表；
2. `savemap.resolve()` 加桥表 O(1) 快速路径 + 记忆化；
3. 大 JSON 统一走 `paths.load_json()`（pickle 缓存，快 3~5 倍）；
4. 14 MB 的 `itemdb_diff.js` 与派生表全部懒加载。

## 9. 存档 → 装备方案（2026-09-19 新增）

需求：读当前存档，出「满抗性 + 尽可能拉高伤害」的装备（不含武器）。

新增工具：
| 文件 | 作用 |
|---|---|
| `tools/save_plan.py` | 存档正在穿的装备 → `gd.opt` 方案 JSON（`--weapon` 才含武器） |
| `tools/plan_dps.py` | 方案 → **真实每秒伤害**（`gd.dps.load_char(gear_override=…)` what-if，武器不动） |
| `tools/tune_dps.py` | 满抗**硬约束**下用真实模型做单槽 + 双槽局部搜索 |
| `gd/planreport.py`（改造） | 方案 → 中文装备报告；§5 改成「真实伤害与落档」（原来引用的旧脚本名已失效） |
| `gd/opt.py`（新增 `GD_CUR_JSON`） | 用**当前存档**装备替换写死的「现状」表 |

### ★ 两个必须记住的坑

1. **「伤害代理」不是伤害。** 它是线性加权和，对本流派（狼人：攻速 × 流血/穿刺平伤）
   权重偏低 → **会选错**。实测：
   | 方案 | 代理(默认权重) | 代理(真实边际权重) | 真实 DPS |
   |---|---|---|---|
   | 现状 | 836 | 1106 | **38,518** |
   | 代理最优解 E | 951 | 980 | 26,318 |
   | 代理最优解 G | 988 | 983 | 26,588 |
   → 要「拉高伤害」必须用 `tools/plan_dps.py` 走真实模型。
2. **`--goal maxdmg` + `GD_COVER_MIN` 到不了满抗。** 总覆盖是「各槽单独 min(抗性,需求)求和」的
   **乐观上界**；且它的逐槽 `sufcap` 剪枝**不含稀有度预算**，会因乐观而留下无法收尾的节点。
   要满抗请用 **`GD_FULL=1` + `--goal worst`**（逐维硬剪枝，实测 1070/1070）。

### 本轮实测（Sam lv69，12 槽不含怒刃）

| | 覆盖 | 真实 DPS | 稀有度 |
|---|---|---|---|
| 现状 | 1070/1070 | 38,518 | 5绿/4蓝/3紫 |
| **方案** | 1070/1070 | **40,804（+5.9%）** | 5绿/3蓝/4紫 |

换了 4 件 + 1 颗宝石：项链 → 仲冬之忆、胸甲 → 血羁护甲、戒指1 → 死亡领主之戒、
戒指2 → 屠杀之右手戒、戒指1 镶嵌 → 灵魂碎片。攻速 2.84 → 3.05。
方案与报告落在 `data/plans/`。

## 10. 落档（方案 → 存档）2026-09-19

补上了原缺的**写盘链路**：`gd_build.py` → `gd/build.py`（新增迁移）
+ `tools/plan_to_build.py`（转换器）+ CLI `gd build`（`new | plan | check | apply`）。

流程：`plan_to_build` → `gd build plan`（解析校验）→ `gd build check`（方案↔存档逐槽比对）
→ `gd build apply`（写 `.new` → **块校验 / 往返逐字节一致 / 字段逐项吻合 / seed 合法**
四道复检 → 自动备份 → 覆盖；游戏进程在跑则拒绝写盘）。

### ★★ 本轮踩到并修掉的两个真 bug

1. **`savemap.resolve()` 传「对象」时绕过权威表** → 退回规则推导。
   实测把 `it15810`（仲冬之忆）解析成 `c303_necklace.dbr`，而它的权威记录是
   `records/items/awakened/gearaccessories/necklaces/c308_necklace.dbr`
   → **第一次落档静默写错了项链**。修法：`resolve()` 里按对象身份反查 gid
   （`_gid_of_obj`）。已加自检守卫（`resolve(对象) == resolve(gid)`）。
2. **`gd/save/backup.py` 的 `PAIRS` 是空的**（迁移时写成 `PAIRS = ()`，
   而 `do_backup()` / `snapshot()` 直接遍历 `PAIRS`）→ 备份静默空跑、
   `MANIFEST.json` 写不出来直接报错。修法：`PAIRS = _pairs()`（导入时求值）；
   `_pairs()` 里 `_P.find_save_dir()` → `_P.save_dir()`。

### 本次落档结果（Sam）

- 备份 #1 `GrimDawn_存档备份_2026-09-19_165742`（落档前，65 文件，已校验「备份完好 ★」）；
  写盘时又自动备份 #2 `…_165850`、#3 `…_170330`
- 换 4 件 + 1 颗宝石，武器不动；写盘文件 31331 → 31224 字节
- 落档后：`gd build check` = **方案与存档完全一致 ★**；`gd verify Sam` = **体检通过**；
  `gd dps Sam` = **40,804 / 秒**（落档前现状 38,518）
- 回滚：`gd backup restore <备份目录> --yes`

## 11. 自动化：一条命令跑完全链路（2026-09-19）

痛点：以前一条条手跑（save_plan → gd.opt → plan_dps → tune_dps → planreport → gd build），
出一套装备约 30 min、含落档约 60 min，中间还要人盯结果微调。
现在：`python -m gd auto <角色> [--apply]`，**~25 s / ~29 s**。

新件：
| 文件 | 作用 |
|---|---|
| `tools/autobuild.py` | 总编排：读档 → 满抗搜索 → 真实 DPS 局部搜索 → 报告（→ 落档） |
| `gd/auto.py` | `gd auto` 的薄转发入口 |
| `gd/cli.py`（改） | 透传型子命令（opt/recipe/rotation/build/auto）**手工切分 argv** |
| `tools/tune_dps.py`（重构） | 抽出 `load_opt` / `vecs_of` / `local_search`，可被 autobuild 复用 |
| `gd/dps.py`·`gd/rotation.py`（改） | 大 JSON 改走 `paths.load_json`（进程内 memo + pickle） |
| `gd/dps.py`（改） | `GD_QUIET=1` 关掉「[武器套]」刷屏 |

### 性能

| | 前 | 后 |
|---|---|---|
| 单次 DPS 评估 | 0.33 s | **0.12 s** |
| 出一套装备 | ~30 min | **~25 s** |
| 出装备 + 落档 | ~60 min | **~29 s** |

三刀：① 大 JSON 全走 `paths.load_json`（进程内 memo）；② **现状已满抗就跳过束搜索**
（最慢一步，~40 s）；③ 候选向量 + DPS 评估全 memo。

### 又修了一个 CLI 坑

透传型子命令原来用 `parse_known_args` 把 `extra` 拼回 `rest` → **顺序被打乱**
（`gd opt --compare a.json` 变成 `a.json --compare` → `max() arg is empty` 崩）。
改法：`main()` 里对这几条**手工切分 argv**，子命令词之后的原始参数原样转发。

### 用法

```bash
python -m gd auto Sam                 # 满抗 + 追高伤害（不含武器）
python -m gd auto Sam --apply         # 并落档（preflight → 备份 → 写入 → 复检）
python -m gd auto Sam --with-weapon / --goal fullres / --goal dmg / --quick
python -m gd auto --list              # 列角色 + 自动判定形态
```

形态自动判定：按 `archetypes.json` 的 `masteries`（职业技能组合）比对存档的职业编号，
再按变身形态（狼人 / 鸦人）挑具体变体。

## 12. 瓶颈定位 + 极限模式（2026-09-19 晚）

用户问「为什么跑这么慢、瓶颈在哪」。**实测定位到元凶并修掉。**

### ★★ 真正的瓶颈：每次 DPS 评估都重解析 `skills.json`（11 MB）

`gd/dps.py` 的 `load_char()` 里有一段直接
`_skills_all = json.load(io.open(HERE/'skills.json'))` 用来建 `tag → record` 映射 ——
**每次评估都跑一遍**。cProfile 显示它占单次评估的 **60%**（0.09 s / 0.15 s）。

修法：抽成 `_tag2rec_map()`，走 `paths.load_json`（进程内 memo）只建一次。

| | 修前 | 修后 |
|---|---|---|
| 单次 DPS 评估（热） | 0.150 s | **0.023 s（6.5×）** |
| `gd auto Sam` 全程 | 24.2 s | **8.2 s** |
| 极限模式局部搜索 | 361 s | **82.6 s（4.4×）** |

### 第二层原因：极限模式的搜索量本来就是 13 分钟级

候选池 5491 条（14 槽）× 每槽实算上限 200 × 单槽 4 轮 + 双槽 91 对 + 退火 420 s
= **约 6700~20000 次真实 DPS 评估**。评估单价从 0.15 s 降到 0.023 s 后，
整轮 795 s → **520 s**。

### 顺带修掉的坑

1. **双手武器 + 副手虚高**：`save_plan --weapon` 把「只剩镶嵌的空壳副手」也收了进来，
   override 时那件镶嵌被重复计一次 → DPS 虚高 ~1.7%。改为只收有主体的武器条目。
2. **双手规则下沉到评估层**：`plan_dps.plan_to_override` 里，主手是双手武器时
   **直接丢掉副手** —— 非法组合拿不到收益，搜索自然收敛到合法解（不会再「搜出非法解再回退」）。
3. **输出缓冲**：`| grep` 管道块缓冲，跑 5 分钟一个字符都不出。用 `python -u`。

### 极限模式（`--extreme`）实测：Sam lv69

放开全部限制后的极限配装（14 槽，含武器）：

| | 覆盖 | 真实 DPS | 说明 |
|---|---|---|---|
| 现状（12 槽方案） | 1070/1070 | 40,949 | 双手怒刃 |
| **极限配装** | **1070/1070** | **95,433（+133.1%）** | 双持单手剑（脊柱切割者 + 威利的剃刀） |

- 单槽/双槽贪心到 92,655（82.6 s）后**卡住**；**退火再挖出 +2.9%**（92,655 → 95,433）
  —— 6 次改善全部出现在退火的后 120 s，说明贪心确实有局部最优、退火能跳出来。
- 结论：**「满抗」并不妨碍高伤害**；真正的瓶颈一直是**评估速度**，不是搜索策略。

### 新增开关

| 开关 | 作用 |
|---|---|
| `--extreme` | 池开到底 + 允派系 + 含武器 + 不限稀有度 + 束宽 12000 + 退火 420 s |
| `--anneal-seconds N` | 退火预算（秒） |
| `GD_POOL_DMG_TOPN=N` | ★ 候选池额外并入「按输出排序前 N 件」（默认池按抗性排且丢弃零抗性件） |
| `--pool-topn/--pool-dmg-topn/--comp-topn/--aug-topn/--dedup` | 细调池规模 |

## 13. 极限配装落档（2026-09-19 18:4x）

`Sam_extreme.json`（14 槽，满抗 + 双持）已写入存档。

### ★ 落档链路上补的一个缺口：`plan_to_build` 把武器丢了

`tools/plan_to_build.py` 原来写死 `"weapons": []`，只转 12 件护甲/首饰 ——
极限配装的核心恰恰是**换武器**（双手怒刃 → 双持单手剑）。
若照旧落档，会得到「护甲按双持方案、武器还是双手」的**混合态**，DPS 与方案对不上。

修法：`convert()` 支持 `主手/副手`（默认带上，`--no-weapon` 可关），
复用与装备槽相同的解析路径。`gd/build.py` 本来就支持 `weapons`（还带逐项写后校验）。

### 完成情况

| 步骤 | 结果 |
|---|---|
| preflight | 游戏未运行 ✓、环境就绪 ✓ |
| 显式备份 | `GrimDawn_存档备份_2026-09-19_184300`（65 文件，校验**备份完好 ★**） |
| 转换 + plan | 14 条全部解析成功（0 失败） |
| apply | 四道复检全过 ｜ 28 465 → 28 165 字节 ｜ 自动再备份一份 |
| `gd build check` | **方案与存档完全一致 ★** |
| `gd verify Sam` | **体检通过** |
| `gd dps Sam` | **合计 95 433 / 秒**（与方案一致：野性利爪 78 520、狂乱撕扯 12 071、跃击 4 842） |

- 武器：**双持单手剑**（脊柱切割者 c012_sword + 威利的剃刀 c023_sword），
  前提是角色已学「双刃」（`wpattack0.dbr`，实测 lv1 ✓）。
- 回滚：`python -m gd backup restore "E:\\xz\\Archives\\GrimDawn_存档备份_2026-09-19_184300" --yes`

## 14. 性能调研（2026-09-19 19:0x）—— 详见 `refs/perf_research.md`

一句话：**瓶颈是纯 Python 伤害模型里的重复计算，不是算力。**

### 三个已定位的瓶颈（全部实测）

1. ★★ `gd/rotation.py:807` 的 `skill_children(skills)` ——
   **每次评估都把 12 372 条技能库重算一遍父子关系**（3 339 次正则 + 3 339 次路径处理），
   占 `final_report` 的 **89 %**。而它只依赖**静态** skills.json。
   → 记忆化后单次评估 **0.0255 → 0.0078 s（3.3×）**，DPS 逐位不变。
2. ★ `gd/dps.py:143` `S.parse(p)` —— 每次评估重复解析存档。
   → 叠加 1 后 **0.0225 → 0.0025 s（10.4×）**，DPS 逐位不变。
3. 串行开销 20.4 s / 33.6 s：`import gd.opt` 6.9 s（模块级启动搜索）+
   候选池 `prune_cands` 6.0 s。

### 反直觉点：退火是「固定墙钟预算」

`--anneal-seconds` 是**时间预算**，评估变快**不缩短墙钟**，而是同时间跑更多提案。
实测 `anneal=90`：评估 0.0191 → 0.0028 s，评估次数 **4 993 → 26 712（×5.3）**，
墙钟 123.8 → 116.7 s（几乎不变）⇒ **收益是"结果更好"，不是"跑得更快"**。

### 多进程（32 核）

评估吞吐实测：**P=4 → 2.0×｜P=8 → 2.9×｜P=16 → 3.5×**（含 pickle 开销，Windows `spawn`）。
受 Amdahl 限制——优化评估后串行占比反而上升（39.2 % → 16.1 % 是评估，其余是串行）。
**更优解是「并行多条独立搜索链取最优」**（每进程跑完整一轮，无共享状态，近线性）。

### ★ 必须先修的 bug：模型跨进程不确定

不同 `PYTHONHASHSEED` 会算出**不同 DPS**（随机抽样 18/300 = 6 % 不一致，偏差可达数百）。
根因是 `set` 直接迭代（`rotation.py:838`、`parse_damage` 返回的 `types` 等），
迭代序随字符串哈希变。**不修就不能安全上多进程，也无法复现结果。**
修法：这几处 `set(...)` 改 `sorted(...)`，并加「多种子结果一致」回归。

### GPU：明确否决

热路径是**字符串/路径处理 + 正则**，修完层 1 后真正的算术量极小；
要上 GPU 只能把伤害模型整体重写成张量式 —— 大重写 + 偏离已验证口径的高风险，
且环境无 torch/cupy/numba。**投入产出远不如 A/B/D 三项。**

### 待审批路线

- 阶段 1（低风险，greedy+pair 段 33.6 s → ~11.5 s）：修不确定性 → A 缓存 skill_children →
  B 缓存 S.parse → F 拆模块级启动搜索 → E 落盘缓存 prune_cands。
- 阶段 2：D 并行独立搜索链（`gd auto --jobs N`）。
- 不做：G（numpy 重写模型）、H（GPU）。

## 15. 性能优化阶段 1 已实施（2026-09-19 19:0x）

详见 **`refs/perf_research.md`** 的「阶段 1 实施结果」。

一句话：**单次评估 8.2×、import gd.opt 15×、候选池 28×、greedy+pair 段 11.6×**，
全部写进 `tools/migrate_from_archive.py` 步骤 19~22（可复跑、幂等、已验证）。

★ 顺带修掉一个**隐藏 0.068 s/次**的洞：`DB.load()` 声明了 `_instance` 却没用，
每次重读 pickle；而 `plan_dps._is_two_hand()` 每个新武器记录调一次 → 占「变方案评估」58 %。

⚠️ **同一目录检测到并行写入的外部进程**（`tools/_probe_*.py`、`build_mastery_attr.py`、
`gd_rotation_patched.py` 等非本轮产物），它新建的 `data/mastery_attr.json` 直接改变 DPS：

| 时间 | `gd dps Sam` |
|---|---|
| 落档后 / 优化中段 | 95 433 |
| 外部进程生成 `mastery_attr.json`（19:08:38）之后 | **94 126**（当前基准，多种子确定） |

两者都会互相覆盖（我们重写 `gd/*.py`，对方重写 `data/*.json`）——
**建议确认没有第二个会话在写这个 skill 目录**。

## 16. 性能优化阶段 2：并行独立搜索链（2026-09-19 19:4x）

`gd auto --chains N` —— N 条链各跑完整一轮（贪心 + 退火），取最优。

| | 墙钟 | 结果 |
|---|---|---|
| `--chains 1 --anneal-seconds 30` | 33.5 s | 94,126（0 次改善） |
| `--chains 4 --anneal-seconds 30` | 35.6 s | **95,369（+1.3 %）** |

- `--extreme` 默认自动开 `min(核数,8)` 条链；`--quick`/普通默认 1 条。显式 `--chains N` 永远优先。
- ★ **`--anneal-iters N`**：按迭代数停（温度表也按进度），**完全可复现**；
  默认的 `--anneal-seconds` 按墙钟停，机器负载会让结果轻微抖动。
- chain 0 `perturb=0` 复刻单链 ⇒ 并行结果**必定不劣于**单链。

顺带修：**双持 build 靠武器补抗性**，默认 12 槽会看到 1011/1070 → 束搜索「无可行组合」。
现在检测到这种情况会**自动带 `--with-weapon` 重跑**（`GD_AUTO_REEXEC` 防重入）。

⚠️ **协调告警**：本轮实施期间检测到**另一个进程在同一目录实时写入**
（不仅 `data/*.json`，还包括 `gd/paths.py` —— 19:46:49 抓到一次**写一半的语法错误**状态）。
**我的迁移脚本会重写 `gd/rotation.py`、`gd/dps.py`、`gd/opt.py`、`gd/req.py` 等生成文件；
若对方也改了这些，双方会互相覆盖。** 详见 §15 与 `refs/perf_research.md`。

→ **本节的「另一个会话在写」是误判，真相见 §17。** 回滚器就是迁移脚本自己。

## 17. 真源修复：迁移脚本「权威覆盖」终步（2026-09-19 21:0x）

### 起因：上一轮的归因错了

阶段 1/2 期间连续观察到 `gd/*.py` 被打回旧版（19:46:49 抓到 `gd/paths.py` 写一半的
`IndentationError`；DPS 从 95 433 漂到 94 126），当时归因为「另一个会话/进程在写同一目录」，
还建议用户去关掉并行会话。

**并行会话收工后，用隔离实验证伪**：把整个 skill 复制到 `/tmp/mtest`，在副本上重跑
`tools/migrate_from_archive.py`，再与迁移前逐文件 `diff -rq` —— 差异恰好落在 **7 个文件**：

| 文件 | 迁移前 | 迁移后 | 丢 |
|---|---|---|---|
| `gd/req.py` | 507 | 253 | **−254**（官方 `itemCostFormulae` 求值版 → 估算版）|
| `gd/build.py` | 652 | 501 | −151 |
| `gd/save/patch.py` | 313 | 197 | −116 |
| `gd/rotation.py` | 1260 | 1230 | −30 |
| `gd/save/backup.py` | 218 | 190 | −28 |
| `gd/alloc.py` | 579 | 561 | −18 |
| `gd/save/verify.py` | 327 | 318 | −9 |

合计 **881 行**。`FILES` 表把这些文件当「基座」从旧归档搬运，而成熟版的**重写级改动
从未写进迁移脚本**，所以每次重跑都被打回。
物证：`tools/_reverted/req.py.1d6924c3`（10 017 B）大小 = `tools/legacy/gd_req_estimate.py`，
正是 `FILES` 会产出的那个估算版。

### 修法：迁移末尾加步骤 23「权威覆盖」

- **`overlay_live(sync_live, pre)`**：按 `sync_live.PAIRS` 用 `tools/` 下的保命副本
  **整体回填** `gd/`。选整体覆盖而非细粒度 patch —— 881 行是重写级改动，
  写成字符串 patch 既脆弱又不可读。
- **`sync_live.py` 新增 `--capture`**（活文件 → 副本），补齐双向同步；
  改完代码后刷新副本从「可选」变成**义务**。
- **留档收紧**：只在**迁移开始前**活文件就与副本不同时才留档（迁移自己打回的不留，
  否则每次跑都往 `_reverted/` 塞同样的垃圾）。为此 `main()` 在 `FILES` 循环前
  做一次 `snapshot_live()`。
- **副本缺失 → `return 3` 直接失败**，绝不产出「看起来成功、其实缺功能」的 skill。
- `PAIRS` 由 `sync_live.py` **单点定义**，迁移 `import` 复用，避免两份清单漂移。

### 验证（2026-09-19 21:0x）

| 项 | 结果 |
|---|---|
| 临时副本重跑迁移 | 与迁移前 **零差异**（`diff -rq` 无输出）|
| 活目录重跑迁移 | `gd/req.py` / `gd/build.py` / `gd/save/patch.py` **md5 不变** |
| `tools/_reverted/` | 无新增垃圾留档 |
| `tools/selftest.py` | **全部通过**（12 组）|
| `PYTHONHASHSEED=0 gd dps Sam` | **94 126**（与阶段 2 基准逐位一致）|

### 遗留约定（★ 必须遵守）

> **凡是「重写级」改动（不是小补丁），必须登记进 `tools/sync_live.py` 的 `PAIRS`。**
> 登记 = 进入真源 = 迁移重跑不丢 + 可被 `--apply` 抢修。
> 只改活文件不登记，任何人重跑一次迁移就丢，且**不会报错**。

`gd/dps.py`、`gd/opt.py` 的改动（阶段 1 的缓存/守卫）**不在** `PAIRS` 里，
因为它们已作为细粒度 patch 写进迁移步骤 19~22，迁移能自行重建 ——
这是「patch 步骤」与「PAIRS 覆盖」的分工：**能写成小补丁的走 patch，重写级的走 PAIRS。**

---

## 18. 性能优化阶段 3：满抗搜索换 ILP + 并行度改逻辑核（2026-09-19 23:0x）

### 起因

用户：「太慢了 完全没发挥电脑的性能 尽可能跑满电脑性能」。
实测确认属实：`--extreme` 全程里**满抗搜索 74 s 是纯串行**（占 71%），
而它用的 `ThreadPoolExecutor` 被 GIL 锁死（74 s ÷ 6 次重启 = 12 s/次 ≈ 单次耗时）。

### 调研结论（★ 都带实测数字，别再重复踩）

| 路线 | 结果 | 结论 |
|---|---|---|
| LP 松弛（去掉整数约束） | 0.03 s，界 2808.19，仅 11 个分数分量 / 5 槽被拆开 | 界很紧，但**不可达** |
| LP 舍入 + 枚举分数槽位 top-3 | 243 个组合**全部不可行** | 抗性卡边界，整数间隙必须靠 B&B |
| 完整 MILP `gap 1e-4` | 9.8 s，**2746.70**（全局最优） | 生产用 `gap 1e-3`（9.0 s，同解） |
| 完整 MILP `gap 1e-2` | 5.85 s，有时 2746.70、有时 **2741.10** | 会停在次优，**不作默认** |
| `mip_heuristic_effort 0.5` | 16.3 s | 更慢，无用 |
| MIP start（LP 舍入解） | 9.98 s vs 基线 9.02 s | 无收益 |
| **HiGHS `threads>1` / `parallel='on'`** | **`Not Set`（0.00 s / gap inf）** | **本机构建不支持并行 MIP** |
| 分解并行：Σleg = L，15 子问题 × 16 进程 | 墙钟 **27.7 s**（CPU 合计 188 s） | **比单实例 9.0 s 更慢**；但 L=4 得 2746.70 交叉验证了正确性 |

**所以「并发」在这里的正确形态是**：ILP 单线程不可压缩（9 s），
多核靠「**N 条独立搜索链**」吃，不是「把一个 ILP 拆开」。

### 实施

| 文件 | 改动 |
|---|---|
| `tools/ilp_res.py` | 新增 `class Model`（多选择背包 MILP），`solve()` 重写为薄封装，新增 `cap_search()` 高层入口与 `lp_bound()` 诊断；删掉旧的一次性 `solve()` 内联实现 |
| `tools/autobuild.py` | `run_cap_search()` 默认走 ILP（`--cap-solver auto/ilp/beam`），**失败自动回退束搜索**；新增 `--ilp-gap`（默认 **1e-3**）、`--ilp-time-limit`；**默认并行度 `_phys_cores()` → `_logical_cores()`** |
| `tools/selftest.py` | 新增 **[15] ILP 满抗搜索** 7 项断言（96 → **103 项**）；同时修掉一条**设计缺陷断言**（见下） |
| `tools/cpu_probe.py` | 新增：实测 CPU 到底跑没跑满（16 链 vs 32 链） |

### 数字

| 指标 | 前 | 后 |
|---|---|---|
| 满抗搜索 | 束搜索 **74 s**（串行，且目标不含伤害项） | ILP **9.0 s** 全局最优（⚠ 在 `gd auto` 进程内实测 17.8~23.9 s，原因未完全归因，见「遗留 1」） |
| 全流程（`--extreme --chains 1 --anneal-seconds 2`） | 100.9 s | **16.0 s** |
| 整机 CPU（45 s 退火预算） | 16 链：峰值 60.3% / 均值 **39.5%** | 32 链：峰值 **98.6%** / 均值 65.4% |
| 同预算 DPS | 16 链 85,741 | 32 链 **86,169（+0.5%）**，墙钟 77 → 80 s |

### 顺带修掉的自检缺陷（★ 值得记住）

原断言 `reqfit 幂等（Sam 已修好 → 无需改动）` 把**历史状态**当成了不变式 ——
用户进游戏换装备后（lv69 → lv71）必然变红，属**误报**。
已改成真正的幂等性检查：**同档连续求解两次，结论必须一致**。
> 教训：断言要测「系统的性质」，不要测「某个时刻的快照」。

### 遗留（下一步可做）

1. **ILP 的 9~24 s 与 worker 启动（约 15 s）串行叠加**：独立跑 9.0 s，
   但在 `gd auto` 进程内实测 **17.8~23.9 s**。已排除两个猜测：
   - **不是**环境/候选池差异：把 `gd auto` 的环境完整复刻出来单独跑，候选指纹
     逐一相同、耗时 10.2 s（对照 9.0 s，只是噪声）。
   - **不是**能靠进程隔离解决的：放子进程反而 **23.8 s**（多出 spawn + 重新加载 DB）。
   剩下最可能的解释是主进程已占 ~1.5 GB 造成的系统级内存/分配压力，尚未证实。
   把 ILP 与 worker 预热**重叠**（而非隔离）预计能拿回这 10~15 s。
2. **退火阶段（420 s）才是 `--extreme` 的真正大头**，且已满核 ——
   要再快只能提高**单次评估吞吐**（任务 12/13：向量化批量评估，目标 100~1000×）。
3. 满抗搜索的**目标函数仍是线性「伤害代理」**（`gd/opt.py:824` 的注释自己承认
   光之誓戒指代理判 +25%、真实模型判 −19%）。ILP 是在「代理」口径下全局最优，
   换成真实 DPS 口径需要把模型改成非线性 —— 目前靠后面的退火阶段兜。
