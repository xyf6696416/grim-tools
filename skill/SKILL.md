---
name: grim-dawn
description: 恐怖黎明（Grim Dawn）离线数据工具链。地基是 Grim Tools 桌面版 app.asar 里的**离线数据库**（8612 物品 —— 站点「总览」口径是 **8301 件装备**，差额 311 条是游戏内书籍；4105 词缀 / 199 套装 / 4268 物品技能记录（被装备引用的去重 3297）/ 1433 怪物 / 13 语言含简体中文 / 未混淆的官方公式），全部本地、零网络。可查物品与词缀的**中文名称与全部词条**（含官方等级缩放后的真值与区间）、套装逐档加成、技能逐级数值、星座（虔诚）、怪物与掉落、地图与箱子、升级给点表；也解析/回写游戏存档 .gdc/.gst。当用户提到"恐怖黎明""Grim Dawn""查一下这件装备""XX 物品的中文名/属性""配装""BD""改档""存档""player.gdc""GrimTools""离线库""离线数据库"时使用。**不需要 .arz、不需要 lz4、不需要抓网页、不需要 CDP。**
agent_created: true
---

# 恐怖黎明 · 离线数据工具链

> **本文件是 skill 的「导航层」** —— 只放速览、命令速查、关键口径、陷阱索引。
> 细节按功能沉到四个文件夹，**层层下钻、按需再读**，别把整本一次读完。

## 目录地图（功能分层）

```
grim-dawn/
├── SKILL.md          本文件（导航层：速览 / 速查 / 口径 / 陷阱索引）
├── README_PORTING.md 移植/测试包说明（随 `tools/pack_skill.py` 打进 zip）
├── gd/               核心 Python 包（唯一路径来源：gd/paths.py）
│   └── save/         存档读写（core / write / patch / inv / backup / verify / diff / skill）
├── tools/            命令行工具（详解见 docs/commands.md；保命副本 gd_*_patched.py 勿删）
├── data/             数据
│   ├── *.json        抽取产物（skills / monster_stats / item_skills / archetypes / …）
│   ├── cache/        离线库原始 .js（重建前提：itemdb/calc/l10n/map/monsterdb）+ pickle 索引
│   ├── plans/        方案与报告（final/ = 交付物）
│   └── regress/      回归基线（golden / anchor / sheet）
└── docs/             原理与设计文档（原 refs/，勿改名回来）
    ├── offline_db.md        离线库结构 / asar 布局 / 溯源
    ├── formulas.md          官方公式逐条对应（rc/Af/Xc/f/l）
    ├── perf_research.md     性能调研（瓶颈定位 / A~H 方案 / GPU 结论）
    ├── plan_dmg_model_v2.md 伤害模型 v2（Step 4/5/6）
    ├── plan_rr_conversion.md 减抗 / 伤害转化缺口 + 四期方案
    ├── status.md            迁移状态 / 旧脚本对照 / 路线图
    ├── commands.md          ★ 命令行详解（原 §3，全部子命令）
    ├── pitfalls.md          ★ 陷阱全集（原 §7，编号已到 #98）
    ├── api.md               Python API（原 §4）
    └── reference.md         公式 / 字段 / 迁移 / 基线 / 映射表 / 自检（原 §5/§6/§8/§10/§11/§13）
```

```bash
PY="C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
cd C:/Users/Administrator/.workbuddy/skills/grim-dawn     # ← 技能根目录，gd/ tools/ data/ docs/ 全在这里
$PY -m gd <子命令>
```

> ⚠ **别把 `E:\Grim Tools\` 当根目录**。那只是 GT 桌面版安装目录（`app.asar` 在那儿），
> 里面**没有** `gd/` `tools/` `data/` `docs/`。在它下面跑 `tools/selftest.py` 会报
> `can't open file 'E:\Grim Tools\tools\selftest.py'`。

---

## 0. 速览剧本

```
① 探环境            python -m gd env                      ← GT 安装 / 存档 / 游戏 / 角色
② 查数据            python -m gd item  <中文名|itXXXX>
                     python -m gd find / affix / set / skill / monster / devotion
③ 读存档（只读）     python -m gd chars                    ← 列出全部角色
                     python -m gd save  Sam [--full] [--md] [--out x.md]
④ 存档安全           python -m gd verify --preflight        ← 写盘前检查（游戏必须退出）
                     python -m gd backup backup / verify / diff / restore
                     ★ python tools/gd_restore.py [-l] [-n N] [-y]   ← 一键换档
                       （列表含等级/三围/指纹/备注；**默认只恢复目标角色**，不碰其他角色）
⑤ 配装 / DPS        ★ python -m gd auto Sam [--apply]      ← 一条命令跑完全链路
                     python -m gd dps Sam [--enemy-res boss]
                     python -m gd rr  Sam                    ← 敌方减抗汇总 + 5 档换算表
                     python -m gd opt --goal dmg --beam 6000 --restart 6

④b ★ 落档一整套 BD  （**先备份** → 装 → 技/星 → 锚点）
                     python -m gd backup backup                       ← ① 先备份
                     python -m gd build apply <装备方案.json>          ← ② 装备
                       方案格式 = `{char, note, equipment[], weapons[]}`（GT id 可直传）；
                       ★ 会按**新装备需求自动拟合三围** + 复检可穿性
                     ★ $PY tools/plan_to_save_skill.py Sam --alloc <加点.json> \
                          --dev <星座方案.json> --out <落档.json>      ← ③a 生成「技能+星座」落档方案
                       （差量语义：不在清单里的技能会被置 0；`--alloc` **不能含星座**；
                         差量**按并集一次算** —— 陷阱 #74）
                     python -m gd.save.skill apply <落档.json>        ← ③b 技能 + 星座
                       格式 = `{char, note, set:[{skill, level, why}]}`；六重校验 + 自动备份；
                       ★ 星点写的是 **`devotion_level`**（不是 `level`）—— `docs/pitfalls.md` #67
                     python -m gd.save.patch --char Sam --devotion-points N --no-fit-gear --apply
                       ← ③c 星座点数**要跟着扣**（`gd.save.skill` 拒绝改 block2）
                     ★ ④ **落档后必须重设锚点**：`data/regress/model_v2_anchor.json` 加 `vN`
                       + 把 `tools/selftest.py` 里**两处**版本指针指向 `vN`
                       （否则自检一直「跳过：存档已变」，看着像全绿其实没验）
⑤b 伤害循环文档 ★★  $PY tools/plan_cycle.py Sam data/plans/X.json --arch <形态> \
                        --enemy m3955 --out data/plans/final/CYCLE_<形态>.md
                     ← **每个新 BD 必带**：逐技能 · 逐伤害类型 · **每条伤害的来源**
                       （武器/光环被动/装备/套装/星座/技能本体）· 乘区叠加 · 单次与每秒 ·
                       可触发的装备/镶嵌物技能。见 docs/commands.md §伤害循环文档
                     ★★ 模型改完后**一键重跑全部交付物**：
                        $PY tools/rerun_final_docs.py      ← 8 形态 CYCLE + REPORT
⑥ 写代码            from gd import DB; db = DB.load()       ← 见 docs/api.md
⑦ 装备穿不上？       ★ $PY tools/reqcheck.py Sam               ← 逐件属性需求 + 缺口
                     ★ python -m gd.save.patch --char Sam --apply  ← 自动拟合并写入三围
⑧ 真源校验/抢修      $PY tools/sync_live.py [--apply|--capture]   ← 22 对，见 docs/pitfalls.md #23
⑨ 回归对拍           $PY tools/gt_regress.py ZyDo860V [--golden]  ← 改了 gd/ 就跑
⑩ 库/池对拍          ★ $PY tools/db_census.py [--pool]          ← 物品库「总览」9 行 + 候选池自洽
```

> ★ **写档动作本身就是属性求解器**：`gd.save.patch` / `gd build apply` 会按**当前/新装备**
> 自动算出三围并写入，写完复检可穿性 —— 不过就拒绝落盘。不用再手跑 `fix_wearability.py`。

> ★ **要「出一套装备」就直接 `python -m gd auto <角色> [--apply]`** ——
> 读档 → 满抗搜索 → 真实 DPS 微调 → 中文报告（`--apply` 再落档）。

**四条铁律**

0. ★★ **每出一版新 BD，必须同时交一份「伤害循环文档」**（`tools/plan_cycle.py`，
   落地在 `data/plans/final/CYCLE_<形态>.md`）。它回答四件事：**每条伤害的来源**
   （武器 / 光环被动 / 装备 / 套装 / 星座 / 技能本体）、**乘区怎么叠**（各来源给了
   多少 %、独立乘区、转化、穿甲、抗性、护甲）、**单次与每秒**（四层口径）、
   **该技能能触发哪些装备/镶嵌物技能**。口径与 `plan_audit` 同源（都吃
   `plan_dps.dps_of`），数值不可能分叉。**没有这份文档的 BD 不算交付。**
1. **先 `gd env` 确认环境**，不要硬编码路径 —— 所有路径由 `gd/paths.py` 唯一提供。
2. 第一次加载要 ~2 秒（解析 8.7 MB 的 itemdb.js），之后走缓存 **0.1 秒**。
3. **数值一律经过 `Scaler`**（官方等级缩放）+ `GTFormat`（官方格式引擎），否则数字是错的。
4. 改档前必须：**游戏完全退出** + **先备份**（`paths.game_running()` 会拦住你）。

---

## 1. 为什么离线库能取代之前那一大堆东西

| 旧做法 | 现在 | 砍掉的东西 |
|---|---|---|
| 解析游戏 `database.arz`（RD 段 LZ4） | 离线库直读 | `gd_arz.py` / `gd_arc.py` / `gd_dbr.py`（已归档 `tools/legacy/`）、**lz4 依赖** |
| Chrome CDP 抓 grimtools 网页 | 同一份数据，本地完整版 | `gt_fetch.py` / `gt_page.py` / `cdp_eval.py` |
| 抓网页提示框反推词缀真值 | `rc()/Af()` 官方公式直接算 | `gt_tooltip.py` / `gd_local_tip.py` |
| 逆向缩放规则（猜） | `calc.js` **未混淆**，逐字读 | 全部逆向笔记 |
| 手抄技能名/中文名 | 13 语言语言包，16563 条中文 | 手工维护的映射表 |

**离线库 = `app.asar` 内的 `itemdb.js`（8.7 MB）**，与 GT 网页版数据同源，
外加 **`calc.js` 未混淆**（官方计算引擎）与 **`l10n/zh.js`**（简体中文）。

---

## 2. 环境与路径

```bash
$PY -m gd env
```

| 项 | 位置 |
|---|---|
| GT 桌面版 | `E:\Grim Tools`（可用 `GD_GT_DIR` 覆盖） |
| 离线库 | `E:\Grim Tools\resources\app.asar`（465 MB） |
| 存档 | `C:\Program Files (x86)\Steam\userdata\<id>\219990\remote\save`（steam-cloud） |
| 游戏 | `E:\SteamLibrary\steamapps\common\Grim Dawn` |
| 本技能缓存 | `skills/grim-dawn/data/cache/`（抽取的 js + pickle 索引） |

Python：**必须用托管 venv**（`…/envs/default/Scripts/python.exe`，带 numpy）。
`versions/3.13.12/python.exe` 缺 numpy，做优化器类计算会静默退回纯 Python（慢 7 倍）。

---

## 3. 命令速查（详解见 `docs/commands.md`）

| 域 | 子命令 / 工具 | 一句话 |
|---|---|---|
| 数据查询 | `gd item / find / affix / set / skill / monster / devotion / map` | 物品/词缀/套装/技能/怪物/星座的中文名与全部词条 |
| 存档 | `gd chars / save / backup / verify / diff / restore` | 只读优先；写档前必须游戏退出 |
| 计算 | `gd dps / rr / opt / recipe` | 面板 DPS、敌方减抗、ILP 配装、社区构建解码 |
| **装备授予技能** ★★ | （`gd/dps.py::load_char`） | 按模板分三类：**WPS** 入池 ／ **常驻类**（`Skill_BuffSelfToggled`·`Passive*`·`Give*`）**进技能表 ⇒ 属性全程生效** ／ **触发型**（带 `ctXX`）不收。实测「恶毒尖刺」的 +75% 穿刺此前**整类漏算**（陷阱 #71）。⚠ `skillmod` 数据目录曾失效 ⇒ 套装表恒空（#72） |
| 自动链路 | `gd auto <角色> [--apply] [--extreme]` | 读档→满抗搜索→DPS 微调→中文报告（→落档） |
| 伤害循环 | `$PY tools/plan_cycle.py` | ★ **每个 BD 必带**：逐技能来源拆解 + 乘区链 + 触发关系（`gd/dmgcycle.py` + `gd/procs.py`） |
| 交付物重生成 | `$PY tools/rerun_final_docs.py` | ★ **模型改完必跑**：一键重算 `final/` 全部 `CYCLE_*` + `REPORT_*`（`--check` 只 diff） |
| 落档 | `gd save.patch` / `gd build apply` | 按装备自动拟合三围并写入 + 复检可穿性 |
| **换档 / 回退** | **`$PY tools/gd_restore.py [-l] [-n N] [-y]`** ★ | **一键恢复最新备份**（列表含等级/三围/指纹/备注）；**默认只恢复目标角色**，不碰其他角色。三道门：游戏在跑 / 备份校验 / 归档不得嵌套。双击用 `tools/gd_restore.cmd` |
| 存档指纹 | `$PY tools/save_state.py Sam` | 锚点绑存档 ⇒ **先看指纹再比数值**（`_state.指纹`） |
| **星座优化** | **`$PY tools/tune_devotion.py [--rank\|--marginal\|--realloc]`** ★ | **真实 DPS** 做星座贪心 / 单星座增量排行 / **现有星座拆点损失** / 整池重排。★ `--realloc` 有**不劣化兜底**（best-seen × 基线取优，落盘永不更差，`"adopted": false` = 已退回基线）；**支持单星粒度**（含根的连通子集，游戏里不能跳着点星）。★★ **点数池 = 已点亮节点数 + 未分配**（存档 `total_devotion_points` 是**已花**）；**proc 节点也花 1 点**但不喂模型（无触发率门控）。⚠ `devotion.select_coherent` 的「加成 % 加和」与真实伤害**不同向**（实测建议方案 −10.1%） |
| **技能点优化** | **`$PY tools/tune_skills.py --mode {add,marginal,swap}`** ★ | 真实 DPS：只用未分配点 / **边际值表** / **1-1 换位到收敛**（带依赖护栏）。**候选池排除 `/itemskills`**（投 WPS 会把武器池撑爆 ⇒ 默认攻击归零） |
| **属性点优化** ★ | **`$PY tools/tune_attrs.py [--exact] [--plan X.json]`** | **四轴里唯一从没被优化过的一轴**。默认坐标上升挪移；**`--exact` = 全枚举求全局最优**（可行域 `~预算²/2`，Sam 2,701 个组合 / 16 进程 **3~5 秒**）⇒ 这一轴给的是**可证明的全局最优**，不是局部最优。★★ 单位坑：`bio_override` 收的是「**不含精通/装备的三围基础**」（= `50 + 8 × 点数`，Sam 即 `130/634/66`）—— 传加点数或最终面板都会**静默**算出一个假面板 |
| **单维度最优 / 四轴编排** ★★ | **`$PY tools/coordinate_ascent.py Sam --axes attr`** | ★ **单维度单独跑最优解**就写单轴；`--axes attr,skill,dev,gear` 则是坐标上升外循环。每轴在**上一个轴的最优解**上继续，迭代到收敛。★ 报告按轴分文件（`ASCENT_<角色>_<轴>.md`，互不覆盖）；表里带 **「最优性等级」列** —— 属性 = **全局最优**、技能 = 局部最优、星座/装备 = 启发式。⚠ 四轴 harness 各不相同（星座走显式星座通道，差 +3.7%）⇒ **轴内增益是「自报」的，绝不可相加**；要跨轴可比数字用 `eval_build_variants` |
| 变体对比 | `$PY tools/eval_build_variants.py` | 同 harness 下对「技能 / 星座 / 属性」各轴做 A/B（数字不可跨 harness 混比） |
| **评分目标** | **`GD_OBJ=<伤害桶>`** ★ | **优化目标的单一开关**（`tools/objfunc.py`）。`total`（默认）/ **任意伤害桶**（`physical` `pierce` `fire` `cold` `lightning` `poison` `acid` `vitality` `aether` `chaos` `bleeding` `burn` `frostburn` `electrocute` `decay` `trauma` `poisondot`，2026-09-21 从「只支持穿刺」扩展而来）／桶名加 `+` = 桶 + 20% 其他。做**单一主轴**形态**必须**设，且要让**装备搜索 / 星座 / 技能 / 终评四处一致** —— 只改 `damage_weights` 只管满抗求解、管不到 LNS 搜索（`docs/pitfalls.md` #60）。⚠ **桶为空时静默退化成「合计」**（#63），跑完必须与 `total` 对一次 |
| 需求闸门 | `tools/reqcheck.py` / `tools/fix_wearability.py` | 逐件属性需求核查 / 单独跑一次拟合 |
| **抗性审计** | **`$PY tools/res_audit.py Sam [--plan X.json]`** ★ | 满抗**为什么溢出**：逐槽 → 底材/镶嵌/附魔/词缀 → 字段，并列出每条抗性**搭便车**带来的 OA / 伤害字段 |
| **减抗来源明细** | **`$PY tools/rr_sources.py [角色] [--no-weapon] [--write]`** ★ | **当前这套 BD 的减抗是「谁供的」**：装备拆到**逐槽逐部件**（底材/镶嵌/附魔/前缀/后缀，走 `save_plan.build`）、技能按来源分三类（专精 / 装备授予 / 星座 proc），末尾给 B 累加·C·A 取最高的**合计**。★ 合计与 `collect_char` 聚合口径**逐桶一致**（自检 `[60]` 锁死）—— 调 `resaudit.SLOT_FIELDS` 的部件顺序，标出当前启用武器套 |
| **降抗字段清单** | **`$PY tools/rr_items.py [--family B\|C\|A] [--top N] [--write]`** ★ | **「降低目标抗性」的字段全库清单 + 实例排行** → `data/rr_item_fields.md`。三族：**B** = 技能上 `defensive<类型>` **负值**（叠加，物品技能 491 条）／**C** = `offensive*ResistanceReductionPercentMin`（取最高，16 条）／**A** = `…AbsoluteMin`（取最高，76 条）。★ 两个形态坑：**B 族必须精确匹配**（`defensiveColdDuration` 是时长，剥后缀会误命中）；**C/A 族要排掉 `…DurationMin`**（那是时长不是数值）。判据与 `gd/rr.py::rr_of` 逐字同源 |
| **吸收审计** | **`$PY tools/absorb_audit.py [--percent\|--write\|--check\|--show-dropped]`** ★ | **伤害吸收来源全库审计**（星座/专精/装备 × %吸收/点数吸收）→ `data/absorption_sources.md`；**`--percent` = 只看乘算层（% 吸收）全清单**。★ 表里已锁死：**% 吸收是乘算层、永远 <100%**；**星座的 % 吸收只有伊师塔克 25% 一个来源**。技能类字段**必须四表并集扫**（`docs/pitfalls.md` #66） |
| **防御审计** | **`$PY tools/defense_audit.py Sam [--dmg N --dtype X] [--enemy m1281] [--out F]`** ★ | **我方挨打**的官方 Order of Defense **九层减伤链**：防御面板（抗性含难度惩罚与封顶／护甲**逐部位**／DA／闪避／种族／吸收）+ **逐层剩余** + **等效生命 EHP** + **每层边际收益**。★ 设计见 `docs/plan_defense_model.md`。★★ 减益技能里的 `defensive*` 是**目标的**抗性 ⇒ 必须按模板剔除（陷阱 **#77**） |
| 搜索器 | `tools/autobuild.py` / `tools/tune_dps.py` | LNS 大邻域搜索（**非**模拟退火）+ 邻域并行。★★ 并行度由 `_alloc_parallel(物理核, chains, procs)` **统一分配**（`chains × procs ≤ 物理核`；`--procs` 与 `--chains` **不再互斥**）；**贪心与 LNS 共用并行入口 `eval_trials()`**。收敛开关（默认全关 ⇒ 零漂移）：`--lns-patience N` / `--lns-proxy-tol` / `--time-budget S`。见陷阱 **#89/#90** |
| **孤儿进程清理** ★ | **`$PY tools/kill_orphans.py [--yes] [--any-idle]`** | **「后台堆一堆 0% CPU 的 python」时用它**（陷阱 **#91**）。优化器进程树有**两层**（`gd auto` → 链进程 → 邻域池 worker），被强杀会留孤儿。工具用 **ctypes** 查进程树（本机 `wmic` 与 Windows 图形壳命令都不可用），判据只有「**父进程已死**」一条；**默认只列不杀**，加 `--yes` 才动手（树杀 `/T`） |
| 形态普查 | `tools/sweep_arch.py Sam --all` | 多形态并发（默认 `--budget 24` = 拐点） |
| 词缀补全 | `tools/affix_fit.py` | 给方案里的绿装槽补**合法**词缀（三条硬规则） |
| 加点 / 形态 | `tools/make_alloc.py` · `gd/alloc.py` · `tools/sweep_arch.py Sam --all` | 形态贪心加点 JSON（喂 `GD_SKILL_JSON`）／覆盖率护栏 `alloc.reachable_wps()`／多形态并发普查 |
| 方案体检 | `tools/plan_audit.py` | 三层 DPS + 命中 + 主输出 + **逐技能伤害构成（抗性/穿甲）** |
| 可落地清单 | `tools/build_sheet.py <arch.json> <out.md>` | 装备（含等级需求校验）/属性/技能/星座落地表 |
| 回归对拍 | `tools/gt_regress.py` / `tools/rr_skills.py` | 引擎数值回归 / 减抗技能分·级排行 |
| 库池对拍 | `tools/db_census.py` | 物品库「总览」9 行 + 候选池自洽 |
| 真源运维 | `tools/sync_live.py` / `tools/migrate_from_archive.py` | 保命副本一致性 / 唯一可复跑迁移真源 |
| 打包分发 | `tools/pack_skill.py [--verify] [--only full\|slim]` | 打成可移植 zip（默认 `E:\xz\Archives`）；`--verify` 自动解压跑自检+基准 |

> 完整参数、示例、口径说明：**`docs/commands.md`**（原 §3，含 §3.1–§3.20 全部小节）。

---

## 4. 关键口径（最容易记错的几条）

- **三层 / 四层 DPS**：`dps_panel`（面板）→ ×命中期望（技能级）→ `dps_real`
  → ×减抗乘区 → `dps_vs`（**优化器目标**）→ ×护甲减免（**仅物理直伤**）→ `dps_final`。
- **满抗 = 仅装备槽**（`gd/opt.py::ev` 只做 gear 的 `res_of`）⇒ 保守口径；
  星座 / 被动给的抗性算**额外余量**。抗性 **10 型两排**（前 5 火/冰/电/毒酸/穿刺，
  后 5 流血/活力/虚化/混乱/物理）+ 被动技能一层。
- **护甲只吃物理直伤**（判据单点 `gd/combat.py::armor_applies`）；穿刺/元素/流血**不过甲**。
  武器「% 护甲穿透」是**物理→穿刺**的转化（穿刺绕过护甲），**不是**降低目标护甲（玩家侧恒 0）。
  敌方护甲只有**真值怪**（`m<id>`/假人）有值；等级池/五档无单一护甲值 ⇒ `armor=None`，报告如实标注。
- **伤害循环官方口径**：`skillChanceWeight` 是**权重不是百分比**（Crate 设计师 Zantai，
  Grim Misadventure #54）；WPS 先从默认攻击的 100 里扣，**W > 100 ⇒ 普攻彻底不出现**。
  ★ WPS 只在「默认攻击带武器伤害」时触发 ⇒ 鸦人形态（寒冰之爪 `weaponDamagePct=0`）的夜刃武器池**一次都不触发**。
- **词缀合法性三条齐备才算**：底材类别码（GT `l`，如 `c24`）+ `maxAffixes` + 槽位适用性
  （`affix_ok()` 只覆盖第一条，靠 `tools/affix_fit.py` 补齐）。
- **技能来源合法性**（`gd/skillprov.py`）：`records/skills/itemskills*` 必须由**当前已装备物品**授予，
  且等级需求 ≤ 角色等级；反查不到授予者则放行并标 `unknown`。**反例：`fangs`（完美姿态）**
  三条核心技能只来自 `itemskillsgdx3/relics/*`，唯一授予者 `it15928` 需求 **k=90**，lv71 拿不到 ⇒ 已门控剔除。
- **两种技能口径不能混比（A / B）**：**A** = 真实存档技能+星座（`gd dps Sam` 默认，「只换
  装备」⇒ **可落地**）；**B** = `GD_SKILL_JSON` **整体替换**核心加点（各形态各用各的技能 ⇒
  **跨形态比较只能用它**）。A 口径下 12 个形态**除 `avalanche`(15.7k) 外全部收敛到 110–132k**
  （默认池档；因为都在算同一套狼技能，是假象）。
  ★ **两个口径的冠军不是一个形态**（RR 修正后）：**A ⇒ `wolf_nightblade_fast` 145,818**；
  **B ⇒ `werewolf` 131,893**（m3955）。两个都指向**狼系三形态**这一族。
  ⚠ 注入会**保留存档 `/devotion/`**（旧版曾整体替换 ⇒ 32 点星座被静默清零）。
- **`archetypes.json::core_skills` 是手写清单、会漏**：`gd/alloc` 的贪心是「core 吃满再轮
  rest」⇒ **漏掉的 WPS 永远点不到**。护栏 = `alloc.reachable_wps()` + 自检断言；漏了就
  **补数据**，**别改贪心**（两池合表实测 `human` −7.7%、`avalanche` +20.3%，不能一刀切）。
- ★★ **WPS 武器闸门**（2026-09-20 起默认开启，`docs/pitfalls.md` **#61**）：
  `gd/procs.py::weapon_state()` 从 `base_gids` 的 `item_class` 推武器构成，
  `weapon_verdict()` 按技能的 `Shield` / `dualWieldOnly` 判定 —— 不合则**不进武器池**。
  带 `dualRangedOnly` 等变体的是 **OR** 语义 ⇒ 放行；**看不到武器 ⇒ 不判**（宁可不认）。
  实例：双持 Sam 身上的「**混乱打击**」（勋章 `it14485` 授予的**盾牌战技**）被剔除 ⇒ 面板 −11.3%。
- ★★ **降敌 DA 已接进命中乘区**（2026-09-20，同 #61）：`gd/rotation.py::enemy_da_cut()`
  在算 PTH 之前扣掉角色自己的削减 —— `offensiveSlowDefensiveAbilityMin`（血莽 250）
  与 `characterDefensiveAbility` **负值**（刺骨战吼 −124；**正值是自身 DA 加成、不计**）。
  Sam：敌 DA 1646.7 → **1272.7**、PTH 95.96 → **107.95**、暴击 6.96% → **17.56%**。
  `rep` 透出 `enemy_da_cut` / `enemy_da_effective` / `enemy_da_cut_rows`。
- ★★ **形态门控（陷阱 #78）**：变身**整体替换技能栏** ⇒ 只有 `root_skills[0].granted`
  里的技能能用（`werewolf1.granted` 只有野性利爪/狂乱扯撕）。形态**没声明 `form` ⇒ 零门控**
  （历史漏洞，2026-09-21 已给全部 13 个形态补上）。裁决按**技能的真实来源**：
  形态本人授予 + 星座 proc（`/devotion/` 路径）**保留**；WPS 与技能栏主动技能**剔除**；
  被动/光环/加成保留。⚠ 装备授予的 WPS 走 `gd/procs.wps_pool()` 是**另一条路**，
  在 `form_gate` **之后**才注入 ⇒ 必须在注入处**再过一次同一道门控**（否则「击倒」照样留池里）；
  拦截账本 `rep['rotation']['wps_form_dropped']`。
- ★★ **武器类型门控**（陷阱 #80/#81/#82/#83）：技能记录顶层的 `Axe`/`Axe2h`/`Spear2h`/`Sword`/
  `Mace`/`Shield`/`Dagger` 是**硬前提**（OR 语义），武器不符 ⇒ 该技能/星位**完全不生效**
  （连被动加成也没有）。数据靠 `tools/extract_calc_skills.py` 的**数值指纹消歧**补全
  （2026-09-22：344 → 749 条，**46 条带武器限制**）。★ **门控必须在 `final_report`
  的加成收集之前**跑，两边（`levels` + `_recs`）都要剔；`weapon_st=None` ⇒ 零漂移。
  ★★ **两条输入通道都要过门**：`levels`（存档/形态）与 **`devotion_levels`**（`tune_devotion`
  / `eval_build_variants` 走的**显式星座通道**）—— 只做前者时**星座优化器完全不受约束**。
  账本 `rep['rotation']['weapon_dropped']`（带 `channel` 标注）。
  ★★ **调用点全覆盖**：`weapon_st` 由 `gd.dps.load_char()` 在源头算好存进 `c['weapon_st']`，
  统一用 `gd.procs.weapon_state_of(char)` 取（= `base_gids`，覆盖存档与 override 两条路）。
  **selftest `[61]` 用 AST 扫描强制每个 `final_report(` 调用点点名 `weapon_st`**（19 处）——
  可选参数门控最危险的就是「只接了一条路、其余静默零门控」。
  ⚠ **武器套**判据必须与 `gd.build._active_weapon_set` / `gd dps` 打印一致 ——
  遇到「三处判据互相矛盾」，**先复算指纹确认存档没被游戏改过**再报 bug（2026-09-22 踩过）。
- ★ **冷却技能「施放占位」（陷阱 #79）**：冷却技能按 `freq = 1/冷却` 且**不占任何时间**
  ⇒ 无摩擦叠加、系统性高估。现扣 `occ = Σ(1/冷却 × 占位秒数)`，平 A 可用率
  `swing_scale = 1 − occ`。**只对玩家主动施放的技能**计（星座 / 装备 proc 与平 A 并行）。
  `GD_CD_CAST`（默认 0.4s）／`GD_CD_FRICTION`（默认 1.0，设 0 逐位退回旧口径）。
- **模型边界**：装备自带触发技能、星座绑定的主动技能**都不在** DPS 里（报告如实说明）。
- **极限随敌方抗性变化**：没有单一极限；报告须固定一个敌方档（现统一 `m3955`）并注明口径。

---

## 5. 陷阱索引

**全部 92 条 → `docs/pitfalls.md`**。最高优先（★★★）的先看这几条：

- **#80** ★★★ **星座星位整类拿不到补洞字段**（`extract_calc_skills.py` 一个 tag 对多条记录
  一律判歧义跳过 ⇒ `tagDevotion_B25` → `tier2_25a~f` 六条全丢）⇒ **武器类型白名单
  （`Axe`/`Spear2h`/`Sword`/`Mace`/`Shield`…）全库为 0 条**，模型不知道「需要斧或矛」这种硬前提。
  修法：**数值指纹消歧**（共同字段 ≥2 逐值相等 + 字段集对称差排序）⇒ 产物 344 → **749 条**。
- **#81** ★★ **武器类型门控必须放在「加成收集之前」** —— 放晚（只在 `_recs` 上过滤）
  **面板一位不变**。`gd/procs.weapon_type_ok()` + `final_report(weapon_st=…)`，
  记录顶层武器键是 **OR** 语义。`weapon_st=None` ⇒ 零漂移。
- **#95** ★★★ **给非 Sam 角色跑四轴，三个默认值会把「Sam 的方案 + 狼人形态」套上去**：
  ① `plan_dps`/`tune_attrs` 的形态兜底写死 `'werewolf'`（`guess_arch` **只认变身技能**
  ⇒ 非变身角色一律当狼人，而 `gd auto` 走 `pick_archetype` 职业组合匹配）；
  ② `tune_skills`/`tune_devotion` 的 `PLAN` 默认是**写死的 Sam 文件**、`ARCH` 默认
  `wolf_nightblade_fast`；③ `coordinate_ascent` **没把 PLAN 传下去**。
  ⇒ 数字看着合理（面板 89,217／评分 113,736）却与该角色无关（`_xyf` 真值 **200,754**）。
  修法：`plan_dps.resolve_arch()`（**guess_arch 在前** ⇒ 变身角色零漂移）＋
  `arch_by_mastery()`（与 `autobuild.pick_archetype` 同源）；`coordinate_ascent._resolve_plan()`
  ＋ 显式 `env['PLAN']` ＋ 把 arch 定死后下传；新增 `--plan` / `--gear-extreme`。自检 `[67]`。
- **#94** ★★★ **候选池支配剪枝 `_skyline` 是 O(n²)**（用户实测「怎么这么慢　只有一个在跑」）：
  lv76 `--extreme` 单槽原始候选 **61,510**（150×24×14），单槽 ~110 s、14 槽 **419.5 s**、
  `gd auto` 全程 **546 s**，而且**单线程单核**（16 核一点忙帮不上）。ILP 本身只占 16.9 s。
  改法（**结果逐位不变**）：① 只与**已保留**行比（支配有传递性）② **字典序降序**预排序
  （保证支配者先到）③ `runmax` 快速路径 + 512 行分块命中即停。
  ⇒ 建池 **419.5 → 21.9 s（19.2×）**、全程 **546 → 147.5 s（3.70×）**、候选数与 DPS 逐位不变。
  ★ 第一版 `K[a0:a0+512]` **越界读到 `empty_like` 的未初始化行** ⇒ 负值数据上误删候选
  （fuzz `('rand',1200,7)` 抓出 441 行不一致）⇒ 必须 `min(a0+512, cnt)`。
  `GD_SKYLINE=slow` 可回退 A/B；自检 `[66]`。
- **#93** ★★★ **满抗 ILP 的 (抗性, 目标) 支配剪枝**（`tools/ilp_res.py::dom_reduce`）：
  判据 = 「`res_A ≥ res_B` 逐维 且 `obj_A ≥ obj_B`，且**不许『A 双手替非双手 B』**」
  ⇒ 任何用到 B 的解都能就地换 A（抗性只增不减、目标不降）⇒ **精确剪枝**。
  比 `gd/opt._skyline`（要求输出**每一维**都不劣）**严格更强**。
  实测 Sam lv73 `--extreme`：变量 **38,267 → 13,034（34.1%）**，
  MIP **9.65 s → 3.70 s**，`gd auto` 全程 **77.6 s → 68.0 s（−12.4%）**，
  **DPS 175,726 与解逐位不变**。`GD_ILP_DOM=0` / `--no-dom` 可关。
  ★ **`gap` 不是瓶颈**（1e-3 ~ 1e-1 实测无差，HiGHS 是证完最优性才停）；
  `presolve=off` 变快但给**另一个最优解** ⇒ 弃用。
  ★ **副手 ∅ 候选必须强制保留**（双手约束 `x[主手2H] ≤ x[副手∅]` 靠它）。
  归因台架：`tools/bench_ilp_opts.py Sam --extreme`（同进程参数矩阵）。
- **#92** ★★ **转伤链 `convert()` 预编译查表（单次评估 −8.2%）**：实测
  `convert` 被调 **76 次/评估、152 个步骤实例，但唯一步骤签名只有 1 个**
  （同一次 `_make_hit` 里主路径 + 5 组分来源账本共用同一份配置）⇒ 理论复用 **456×**。
  改成 `_rel_tbl(step)`（按签名全局缓存「来源类型 → 目标表」）后
  **3.310 → 3.039 ms**，结果逐位一致（fuzz 4000 例 + 同进程 A/B）。
  ★★★ **方法论（我连错三次的总结）**：**「包装计时」只能找「谁被调得最多」，
  **不能当耗时占比**（包装本身 ~0.4 µs/次，几千次就把小函数吹成大热点）；
  准确占比只能靠**同进程开关 A/B**（把新旧实现挂回去各跑一遍）。
- **#91** ★★★ **非 daemon 子进程 + 只杀自己 = 孤儿进程越堆越多**（用户实测报「后台卡
  一大堆没占用的 py 进程」）：`Process.terminate()` / `taskkill /F /PID`（**不带 `/T`**）
  只杀一个进程，而链进程底下还挂着一层 `ParEval`（非 daemon）⇒ 父一死、**孙全变孤儿**。
  三道修法：① `_chain_worker` 用 `try/finally` **显式关邻域池**；② 收尾一律
  **`_kill_tree()`（`taskkill /T`）**；③ 新增 **`$PY tools/kill_orphans.py`**
  （默认只列、`--yes` 才杀，判据是「父进程已死」）。验证：跑完 `--extreme` 后孤儿 **0 个**。
- **#89/#90 实操**：`gd auto --extreme` 的墙钟已从 **18.8 min → 113 s（10×）**，
  新预设 **`lns_cap=120`**（实测同结果、墙钟 −40%）⇒ **80.1 s / 175,726（+7.2%）**，
  相对旧架构累计 **≈14×**。要更快：`--chains 2 --procs 8`；单链最省墙钟：`--chains 1 --procs 16`。
  固定开销（与并行度无关）≈ 26 s：建池 2.3 s + 满抗 ILP 18.6 s（`--ilp-gap 1e-2` 可省 ~4 s）+ 报告约 5 s。
  单次评估还剩 ~30% 在**转伤链**（`dmg.convert`，1,684 次 `_conv_matches`/评估）—— 见 #91 附。

- **#90** ★★ **进程池不能嵌套在 `mp.Pool` 里** —— `Pool` 的 worker 是 daemon，
  Python **禁止 daemon 进程创建子进程** ⇒ 「链内再建邻域池」抛
  `AssertionError: daemonic processes are not allowed to have children`，
  表现为**整轮搜索静默挂死**（全进程 0% CPU）。链这层必须用
  `ctx.Process(daemon=False)` + `Queue`，并处理「子进程静默死掉」的收尾。
- **#89** ★★★ **优化器性能瓶颈是「并行效率」而非单次计算**（陷阱维度归因）：
  旧架构 `--extreme` = **10.02 CPU-小时 / 单链 18.8 分钟**，根因是
  **`--procs` 与 `--chains` 被设成互斥** ⇒ 32 条链**各自内部完全串行**（并行效率 1/32）。
  已重构：`_alloc_parallel()` 统一分配（总进程 = 物理核）+ **贪心也并行**
  （新 `eval_trials()` 统一入口）。微基准：**贪心 6.2× / LNS 4.3×，结果 Δ0**。
  ★ 方法论：**别用整条链路做性能对照**（55 s 固定开销会淹没差异），只测改动的函数。
- **#88** ★★★ **`gd auto --extreme` 的性能归因**（实测 10.02 CPU-小时 / 单链 18.8 min）：
  单次 `dps_of` = **3.68 ms**（`final_report` 70% + `load_char` 21%）；单链固定开销 ~32 s
  （建池 23 s + 满抗 ILP 32 s）；LNS **126 次评估/轮** × `max_iters`；贪心 ~17,000 次/链。
  ★ **`--procs`（迭代内并行）与 `--chains`（链级并行）互斥** ⇒ `--extreme` 下每轮
  那 126 次**全部串行**。实测 `--procs 16` 让同样 30 轮 **13.7 s → 1.8 s（7.6×）**，结果一致。
  ★ **扩大候选池必须同时收紧每轮评估上限**（本例武器组件池 2→25 使墙钟 7→18 min）。
- **#86** ★★★ **GT 库 `k` = 等级要求 ／ `l` = Class（槽位类别码）** ——
  GT 键字典：`levelRequirement:"k"`、`Class:"l"`、`WeaponMelee_Sword:"c20"`。
  ⇒ `c20` 是**剑**不是「需 20 级」。`_req_lvl()` 曾把 `l` 当等级 ⇒ 等级闸门形同虚设；
  `plan_legal` 曾**只判槽位不判等级** ⇒ 会给「装不上」的方案报「全部合法」（已修，
  新增 `--level` / `--char`，不传会**醒目警告**）。
- **#87** ★★ **武器镶嵌物候选池只有硬编码 2 项** + `_pool_score` 在 `--goal super` 下
  按抗性排序并**丢掉零抗性的纯输出件** ⇒ 主手池只剩 1 项，武器组件这一维几乎无搜索空间。
  已修：新增 `auto_wpn_comp_pool()`（等级+槽位合法 → 输出代理排序，默认取前 40）。
  ★ **反面教材**：我用「物品等级」当门槛扫出「刀刃之印 +3.65%」并写成方案，
  实际 `k=75` ⇒ 73 级**装不上**。**推荐任何装备前先跑 `plan_legal.py --char <角色>`。**
- **#85** ★★ **优化器把 `collect_char()` 冻结了 ⇒ 减抗不随等级变化** ——
  `tune_skills`/`tune_devotion`/`eval_build_variants` 三处都在**模块级**算一次 rr 就复用，
  于是改减抗技能/星位的等级**永不见效**（刺骨战吼 lv11→12 = 36%→38%，拆点损失却报 0）。
  ★ 它与 #84 是**同一对**：RR 只作用于 `vs` 层，**#84 没修之前根本看不见它**。
  修法 = `gd/rr.py::RRCache`（只按减抗来源等级变化重算，其余命中缓存）+
  三处 `rr=_RRC.pack(eff[, dev])`。`tune_attrs` 无需改（属性点不影响减抗）。
- **#84** ★★★ **`objfunc.score` 的口径取决于喂进去的 `rep` 来自哪个函数** ——
  `dps_of()` 的 `dps` = 含减抗，`final_report()` 的 `dps` = **面板**（不含命中/暴击/
  敌方抗性/护甲）⇒ 只看面板的工具有一批东西**静默归零**：**减抗技能**
  （刺骨战吼拆掉实测 **−24.36%**，工具报 0）、OA/DA 增益（集结战吼 −2.99%、
  不羁狂怒 −2.97%）、血莽 −4.72%。**修法 = `objfunc.score` 的 `total` 分支改用
  现成的 `_total_vs(rep)`（2026-09-22 已修，一处修全部）** —— 对 `dps_of` 的返回
  零漂移（装备轴 + 锚点 v14 不动），对 `final_report` 自动取实战层。
  ⚠ `gt_regress` **不经过 objfunc**（刻意用面板对拍 GT 网站），不在名单里。
  ★ 面板口径的系统性低估 ≈ **8×**（技能轴同一轮：面板 +0.16% ／ 评分 +1.26%）。
- **#82** ★★★ **可选参数门控最危险的形态：只接了一条路** —— `weapon_st` 初版只有
  `plan_dps` 接了，CLI 与 6 个 `tune_*`/`eval_*`/`gt_regress`/`defense_audit` 全在
  **静默零门控**下出数。修法 = 单一真源（`load_char` 存 `weapon_st`）
  + 统一入口（`procs.weapon_state_of`）+ **AST 静态守卫**（`[61]`，19 个调用点全覆盖）。
  **通用规则**：任何「默认关闭」的开关都要配调用点全覆盖的静态检查。
- **#83** ★★ **星座的显式通道 `devotion_levels` 漏过门控** —— 门控只遍历 `levels`，
  而 `tune_devotion` / `eval_build_variants` 走的是 `devotion_levels` ⇒
  星座优化器完全不受武器限制约束。**一个东西有几条输入通道，门控就要有几处**；
  验证看 `rep['dev_nodes']`（比数字干净）。

- **#78** ★★★ **形态门控只在形态「显式声明 `form`」时生效** —— 13 个形态里只声明了 1 个
  ⇒ 狼人/鸦人全系**全程零门控**，「跃击」（`werewolf1.granted` 里没有）被算成 **19.8% 面板 DPS**。
  修法：按技能**真实来源**四类裁决 + 全部形态补 `form` + 装备授予的 WPS 也要过门控。
- **#79** ★★ **冷却技能是「无摩擦叠加」** —— `freq = 1/冷却` 且不占任何时间。
  修法：加**施放占位**（`GD_CD_CAST` 0.4s），**只对玩家主动施放的技能**计
  （星座/装备 proc 与平 A 并行，不抢动作）。`GD_CD_FRICTION=0` 可逐位退回旧口径。

- **#23** `gd/*.py` 会被**迁移脚本自己**打回旧版 → 重写级改动必须登记 `tools/sync_live.py::PAIRS`（现 22 对）并 `--capture`。
- **#24** 武器需求曾是「死分支 + 谎报 official」（`gd/req.py` 的 `CAT_EQ`）。
- **#13** `savemap.resolve()` 的权威快速路径只在传**字符串 gid** 时生效 → 传对象会静默解析错装备。
- **#18** `gd/dps.py::load_char()` 有段性能陷阱（全链路慢的元凶）。
- **#30** `auto_pool` 无条件 `break` ⇒ 「副手」候选池恒为空。
- **#31** `--compare` 是 `gd/opt.py` 保留开关，会**静默清空候选池**。
- **#36** `--jobs` **不是**并行开关（传了也不加速）。
- **#37** 「优化器跑很久」的真答案：**敌方档没缓存**，占单次评估 99.5%。
- **#40** `autobuild` **只优化装备**，技能加点永远是存档的。
  ★ 2026-09-21 补：四轴（属性/技能/星座/装备）编排见 **`tools/coordinate_ascent.py`**，
  属性轴另立 **`tools/tune_attrs.py`**（此前**从来没有**被优化过）。
- **#42** 多投射物默认不乘，只有形态声明 `projectile_hits` 才乘。
- **#46** ★★★ 模拟退火的「预算越大越差」病灶 → **LNS 换代**（不再用退火）。
- **★ 专题**（`docs/pitfalls.md` 文末）：**减抗三族 B→C→A + 负抗性不锁 0** ／
  **`core_skills` 漏 WPS 就永远点不到**（护栏 `alloc.reachable_wps()`）／
  **`/tmp/...` 在 Windows Python 里静默失效**（对照实验必须放仓库内相对路径）／
  **敌方档的 `difficulty` 是字符串 `n/a`，裸 `int()` 会崩**（五档只能靠容错 cast）／
  ★★ **装备授予的武器池技能（WPS）从未进循环（2026-09-20 已修）** ——
  装备 / 镶嵌 / 圣物授予的 `Skill_WPAttack_*` 走 `itemSkillName` 这条路，
  与专精技能表是**两条**收集路径 ⇒ 实测 werewolf 方案：恐狼之爪 权重 12、
  毁伤 权重 25，**37 点权重凭空蒸发**、默认攻击占比虚高到 100%。
  **两层坑**：① `skXXXX` 必须经 `gd/skillprov.sk_to_records()` 翻成记录路径；
  ② `skillChanceWeight` **不在 `.dbr`、也不在 `skills.json`**，只在离线库
  **`itemSkills` 表** ⇒ 只塞记录不塞权重，会变成「又一个默认攻击候选」。
  修法 `gd/procs.wps_pool()` + `final_report(item_wps=…)`（不传 = 零漂移）。
   ⚠ **相邻缺口（已登记，未修）**：**WPS 的「武器约束」整块没判**（陷阱 **#61**）——
   `dualWieldOnly` 会让双手 build 的 WPS 占比偏高；**`Shield` 更严重、且 Sam 本人就是受害者**：
   双持角色算了勋章授予的**盾牌战技「混乱打击」**（面板占 22%，剔除后合计 **−11.3%**）。
   详见 `docs/pitfalls.md` **#61** 与同名专题。

---

## 6. 自检与性能基线

```bash
$PY tools/selftest.py             # 379 项断言，全绿才算好
$PY tools/sync_live.py            # gd/*.py 是否与保命副本一致（只报告）；改完必 --capture
$PY tools/gt_regress.py --ids ZyDo860V --golden   # 引擎数值回归（改了 gd/ 就跑）
$PY tools/gt_regress.py --sheet Sam               # 游戏内面板真值对拍
$PY tools/cpu_probe.py 45         # 实测 CPU 跑没跑满
```

**当前基准（Sam lv73 夜刃+狂战士 · 狼人形态 · 双持单手剑+单手斧）**

| 基准 | 值 | 说明 |
|---|---|---|
| 合计每秒伤害 | **83,536** | `python -m gd dps Sam`（锚点口径 **v13**，见下） |
| 实战（×命中期望） | **96,948** | ×**1.1606**（暴击 **22.26%**）—— ★ 这是自检锚点里的「实战」 |
| 实战（含减抗） | **152,638** | 池 champion+hero（减抗：穿刺 −38% / 流血 −40% / 冰 −25% / 混乱 −25%）—— ★ 这是 `gd dps` CLI 里印的「实战」 |
| 主输出 | **野性利爪（= 默认攻击）54,587（65%）**｜狂乱撕扯 26,918（32%）｜刀锋之怒 2,031（2%） | 官方 WPS 权重口径；★★ 狼人形态下 WPS 全部被**形态门控**剔除（见陷阱 **#78**），默认攻击独占 |

> ★★ **2026-09-21：这两个数字从 137,719 / 159,831 掉到 83,536 / 96,948（−39.3%）**。
> 掉的不是实力，是**虚增** —— 旧数字里「跃击」占 19.8%（27,307），而它在狼人变身后
> **点不出来也用不了**（`werewolf1.granted` 只有野性利爪/狂乱撕扯）。见陷阱 **#78/#79**。

> ⚠ **「实战」有两个同名不同义的口径**：自检锚点里的「实战」= `dps_real`（×命中期望）；
> `gd dps` CLI 印的「实战」= `dps_vs`（**再** ×减抗）。两者差距可达 40%+，引用时必须带口径。

> ★★ **锚点是「绑存档」的，而存档会被外部改** ⇒ **先比指纹，再比数值**。
> 锚点里记了 `_state.指纹`（`tools/save_state.py Sam` 复算）；指纹不符 ⇒ 自检**明确跳过**
> 并提示重设，**不误报成「模型回归」**（见 `docs/pitfalls.md` **#50**）。
> 触发锚点重设的三件事：**落档换装备 / 游戏自己存档 / 玩家在游戏里重置属性点**。
>
> 历史锚点全部保留在 `data/regress/model_v2_anchor.json`：
> · **v14（当前）** = 2026-09-22「**武器类型门控 + 星座数据补洞**」（陷阱 **#80/#81**）后：
>   面板 **81,363** ｜ 实战 98,873 ｜ 含减抗 **163,959** ｜ OA 2,092.2 ｜ DA 1,404.5 ｜
>   PTH 115.32 ｜ 暴击 22.82% ｜ 期望 **1.2152** ｜ 指纹 **`ddb54cc16610`**。
>   ★ **玩家把武器套从 alt2（双剑）换成了 alt1（剑 + 斧）** ⇒ 减抗 C 族 −30% 整条消失
>   （源自 alt2 主手「扭曲精神」底材）。**狂战士（需 Axe/Spear2h）现在限制满足、保留**。
> · **v13** = 2026-09-21「**形态门控补全 + 冷却技能施放占位**」（陷阱 **#78/#79**）后：
>   面板 **83,536.1** ｜ 实战 `dps_real` **96,948.3** ｜ 含减抗 **152,638**；
>   OA 2,062.9 ｜ DA 1,390.5 ｜ PTH 114.49 ｜ 暴击 22.26%（期望倍率 **1.1606**）；
>   指纹 **`2e8dc006a055`**（**未变** —— 这次只改模型，存档一个字都没动）。
>   ★ 137,719 → 83,536 是**扣虚增**：跃击 27,307（19.8%，变形后技能栏里根本没有它）
>   + 装备授予 WPS 32,688（变身后默认攻击被野性利爪接管）+ 施放占位（88,380 → 83,536）。
> · **v12** = 2026-09-21「罗卡（m1281）口径落档」后：面板 137,719.3 ｜ 实战 159,831.0 ｜
>   含减抗 248,083（指纹 `2e8dc006a055`）。⚠ 该锚点**建立在虚增之上**，已被 v13 取代。
> · **v9** = 2026-09-21「**opt_lv73 落档**」后：装备 **9/14 槽**换装（副手→林恩·瓦尔戈斯的切肉斧
>   138% 穿甲 / 勋章→冰原巨狼盾徽 / 主手→血肉盛宴 / 两枚奥利西亚的印戒 等）+ 星座整池重排 **34 点**
>   （退 乌龟/海鳗/抉择之地 → 加 狐狸/猫头鹰/豺狼）+ 技能 5 处（战争兵器 +11 / 跃击 +5 / 血性狂热 −6 /
>   血源苏醒 −7 / 贪噬 −1）。面板 **125,381** ｜ 实战 `dps_real` **145,575** ｜ 含减抗 **210,248**；
>   指纹 `c58437cd9006` → **`d973b27ab594`**；三围 162/546/122（已投 14/62/9）。
>   ★ 属性由 `build apply` 按**新装备需求**自动重拟（不是手工点狡诈）。
>   ⚠ 落档同时**修了星点 `devotion_level` 的两处写入**（新增/重新点亮），见 `docs/pitfalls.md` #66。
> · v8 = 2026-09-20「**WPS 武器闸门 + 降敌 DA 接入 PTH**（`docs/pitfalls.md` #61）」后：
>   面板 **90,844** ｜ 实战 **106,646.8**（命中期望 1.0313 → **1.1740**；指纹 `c58437cd9006`）。
>   ★ **面板降、实战升**：剔除双持触发不了的**盾牌战技**（−11.3%），
>   同时敌方 DA 扣掉自身削的 **374**（血莽 250 + 刺骨战吼 124）⇒ 暴击 6.96% → **17.56%**。
> · v7 = 「属性重分为 11/62/10（存档 `138/546/130`）」后：面板 **101,976** ｜ 实战 **152,476**
>   （指纹 `ad4a74d328fe`）
> · v6 = 「落档 `wolf_nightblade_fast`」后：103,682.4 ｜ 109,509.5
> · v5 = 「装备授予的 WPS 入池」后：52,598.7 ｜ 52,954.0
> · v4 = 「星座数值接入」后：52,737.6 ｜ 53,093.9 ｜ v3/v2/v1 更早
>
> ⚠ **落档已知坑**：一个角色有**两套武器**，必须写进**启用**的那一套
> （`gd/build.py::_active_weapon_set`，判据与 `gd/dps.py` 同步）。
> 旧实现恒写 `alt1` ⇒ 启用 `alt2` 的存档会「落档成功但武器不生效」（实测面板差 **53%**）。
> ⚠ **属性需求已知坑**：`*ReqReduction` 只能扣**字段名里写的那个属性**
> （`_apply_reduce`，见 `docs/pitfalls.md` **#49**）—— 修前胸甲真实需求 464 被误算成 394。

`sync_live.py` 现管 **22 对**（含伤害模型 v2 的 `combat/dmg/enemy`、`cli/dbr/planreport/skillprov`，
以及「伤害循环」新增的 `procs/dmgcycle`）——详表见 `docs/reference.md`。
改完任一文件**立刻** `--capture`；长操作前后各跑一次 `sync_live.py`。

---

## 7. 参考文档（`docs/`，按需读）

| 文件 | 内容 |
|---|---|
| `docs/commands.md` | ★ 命令行详解（原 §3）：数据查询 / 存档 / 计算 / 方案 / 落档 / 自动链路 / 极限模式 / 需求闸门 / RR / 回归 / 伤害模型 v2 / 普查 / 词缀 / LNS / 并行 / 报告 |
| `docs/pitfalls.md` | ★ 陷阱全集（原 §7，编号已到 #98） |
| `docs/api.md` | Python API（`DB.load()` 等） |
| `docs/reference.md` | 官方公式 / 字段与标签 / 迁移状态 / 基线 / 权威映射表 / 自检清单 |
| `docs/offline_db.md` | 离线库结构、asar 布局、顶层键与条目数、语言包、溯源 |
| `docs/formulas.md` | 官方公式逐条对应、黑名单、实测对照 |
| `docs/perf_research.md` | 性能调研：瓶颈定位、A~H 方案对比、并行/GPU 结论 |
| `docs/plan_rr_conversion.md` | 减抗 / 伤害转化缺口分析 + 四期方案 + 落地状态 |
| `docs/rr_mechanics.md` | ★ 减抗三族（B 叠加 / C 取最高 / A 取最高）、结算顺序 B→C→A、**抗性可成负不锁 0**、`gd/rr.py` 对拍与**已修的三处偏差** |
| `docs/devotion_binding.md` | ★ **星座绑定技能机制**：绑定关系存在**存档**的 `autocast` / `autocast_controller`（→ 宿主技能 + 触发条件）；**触发条件全集 = 7 类 × 3 作用目标 / 70 条控制器**；★ **攻击类（`AttackEnemy`/`AttackEnemyCrit`）与宿主技能有关，防御类（被击中/格挡/击杀/低血）无关**；「一个宿主可绑多个、星座技能不绑则不生效」；**63 个星座带技能**；⚠ **触发条件未从离线库导出**（数据缺口） |
| `docs/plan_dmg_model_v2.md` | 伤害模型 v2（`docs/pitfalls.md` #39 星座防御 / `gd/combat.py` 落地） |
| `docs/status.md` | 迁移状态、旧脚本对照表、路线图、归档位置 |
| `gd/dmgcycle.py` · `gd/procs.py` | ★ **伤害循环文档**生成器 + 物品技能**触发关系**（离线库 `itemSkillControllers` 70 条的 `triggerType` 裁决：攻击/暴击/受击/格挡/击杀/低血/常驻/WPS）｜ `procs.wps_pool()` = 装备授予的 WPS 入池 |
| `data/plans/final/CYCLE_*.md` | ★ **伤害循环文档**（**每个 BD 必带**，8 个 Sam 可选形态各一份 + B 口径一份） |
| `data/plans/final/SUMMARY_极限BD.md` · `REGRESS_极限BD_20260920.md` | ★ 速览（口径变更 / 冠军 / 形态排名）+ 全形态回归对拍 |
| `tools/rerun_final_docs.py` | ★ **一键重生成 `final/` 全部交付物**（模型改完必跑；`--check` 只 diff） |
| `data/cost_formulae.json` | 官方**属性需求**公式集 `cf1..cf13`（31 类方程） |
| `data/mastery_attr.json` | 10 职业 ×100 级 ×(体格/狡诈/精神) 精通逐级累计曲线 |
| `data/regress/*.golden.json` | grimtools 构建的**引擎冻结值**（`gt_regress --golden` 基准） |
| `data/regress/model_v2_anchor.json` | 伤害模型**基准锚点**（v1/v2/v3 对照 + Sam 实测锚点） |
| `data/regress/*.sheet.json` | ★ **游戏内面板真值**（手工誊录），`gt_regress --sheet` 基准 |
