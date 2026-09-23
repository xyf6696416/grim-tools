# 命令行详解（§3）

> 本文件由 SKILL.md §3 拆出，随 skill 维护。目录：数据查询 / 存档 / 计算 / 方案 / 落档 / 自动链路 / 极限模式 / 需求闸门 / RR / 回归对拍 / 伤害模型 v2 / 普查 / 词缀 / LNS / 并行 / 报告。

## 3. 命令行

### 3.1 数据查询（主用途）

| 命令 | 说明 |
|---|---|
| `gd item <名字\|itXXXX> [--range] [--json]` | 物品卡片。`--range` 附官方区间 `[lo-hi]`，`--json` 附原始字段 |
| `gd find <关键字> [--kind …] [--quality …] [--max-level N] [--limit N]` | 按名字模糊搜索（kind: item/prefix/suffix/set/itemSkill/monster） |
| `gd affix <名字\|preXXXX>` | 词缀卡片（含可附部位） |
| `gd set <套装名\|isXXXX>` | 套装卡片（成员 + **逐档加成**，件数已自动对齐） |
| `gd skill <名字\|skXXXX>` | 技能卡片；**专精技能带逐级数值表** |
| `gd monster <名字>` | 怪物类型/难度/专属掉落 |
| `gd devotion [名字]` | 星座与亲和力需求 |
| `gd filter <f=V> [k__gte=N] …` | 按**任意原始字段**过滤物品，支持 `__gte/__lte/__gt/__lt/__in/__contains/__exists` |
| `gd grep <关键字>` | 在语言包里全文搜索（找 tag） |
| `gd text <tag>…` | 多语言文本直查 |
| `gd label <字段>…` | 字段 → 中文标签（排查渲染问题用） |
| `gd level` | 升级给点表（官方 `playerBio`） |
| `gd db` | 离线库概览 |

### 3.2 存档（只读优先）

| 命令 | 说明 |
|---|---|
| `gd chars` | 列出所有角色（名称 / 等级 / 职业） |
| `gd save <角色> [--full] [--md] [--out F]` | **中文角色报告**：属性 / 装备（含前后缀/镶嵌/附魔中文名）/ 技能加点 / 战绩 / 派系声望 / 进度。`--full` 带背包逐件 |
| `gd verify --preflight` | ★ **写盘前安全检查**（游戏进程、环境、备份） |
| `gd verify <角色>` | 单角色体检（装备槽 / 技能 / 精通） |
| `gd verify --diff <备份目录>` | 与备份逐字段 diff |
| `gd backup backup / verify <目录> / diff <目录> / restore <目录> --yes` | 备份、校验、对比、恢复（**restore 不加 `--yes` 只预演**） |
| **`tools/gd_restore.py`** ★ | **一键换档**：列出全部备份（等级/三围/未分配/指纹/备注）→ 回车恢复最新，或输序号。**默认只恢复目标角色**，不碰其他角色。双击用 `tools/gd_restore.cmd` |

### 3.3 计算层（配装 / DPS）

| 命令 | 说明 |
|---|---|
| `gd dps <角色>` | ★ **存档实测 DPS 校验**：读真实配装+加点复算最终伤害，按 装备/技能/星座 拆 % 加成 |
| `gd rr <角色>` | ★ **敌方减抗（RR）汇总**：技能/装备分列，5 档敌方抗性各给「减抗后抗性 / 伤害倍数」 |
| `gd opt [参数]` | 装备优化器（`--goal dmg\|worst`、`--beam`、`--restart`、`--compare`…） |
| `gd recipe <流派> <等级> [--goal]` | BD 配方：三要素 → 完整 BD + 中文报告 |
| `gd rotation [参数]` | 输出循环 / 最终伤害模型 |
| `gd tool <模块> [参数]` | 通用转发到任意 `gd.<模块>`（如 `gd tool mana Sam`） |

> `gd opt` / `gd recipe` 是**脚本式模块**（有模块级副作用，import 就会跑一整轮搜索），
> 所以 CLI 走独立子进程转发。别在代码里 `import gd.opt`。

> ★ **实战伤害（敌方减抗乘区）**：`gd dps` 与 `gd auto` 都接受
> `--enemy-res none|elite|boss|high|max`（默认 `elite` = 33%）。
> **面板数字口径不变**（仍不含敌方抗性），减抗另开一行「实战」——
> 这样既保持可对拍，又让优化器看得见乘区。详见 §3.10。

**实例**

```bash
$PY -m gd item 天之裂片咒刃 --range
$PY -m gd find 利维坦 --quality Legendary
$PY -m gd filter f=Legendary k=94 offensiveFireModifier__gte=100 --limit 10
$PY -m gd save Sam --md --out Sam.md
$PY -m gd verify --preflight
$PY -m gd dps Sam
```

### 3.4 存档 → 装备方案（满抗 · 追高伤害）

```bash
# ① 现状 → 方案 JSON（12 槽，默认**不含武器**）
$PY tools/save_plan.py Sam --out data/plans/Sam_current.json
# ② 满抗搜索：GD_FULL=1 是**逐维硬约束**（9 项全封顶）；GD_CUR_JSON 把现状件注入池子（不劣于现状）
GD_MAX_ILVL=69 GD_ARCHETYPE=wolf_nightblade GD_FULL=1 GD_NO_FACTION=1 GD_CUR_JSON='{"头部":["…"]}' \
  $PY -m gd.opt --goal worst --beam 8000 --restart 4 --out plans/Sam_cap.json
# ③ 真实 DPS（同模型 what-if，武器不动）
$PY tools/plan_dps.py Sam data/plans/Sam_cap.json
# ④ 满抗硬约束下，用**真实模型**做单槽 + 双槽局部搜索
$PY tools/tune_dps.py Sam --arch wolf_nightblade --topk 0 --pair-k 3
# ⑤ 中文报告
$PY -m gd.planreport data/plans/Sam_best.json --char Sam --level 69 --out Sam.md
```

| 工具 | 作用 |
|---|---|
| `tools/save_plan.py <角色>` | 存档正在穿的装备 → `gd.opt` 方案 JSON（`--weapon` 才含主手/副手） |
| `tools/plan_dps.py <角色> <方案.json>` | 方案 → **真实每秒伤害**（`gear_override` what-if，武器不动） |
| `tools/tune_dps.py <角色>` | 满抗硬约束下用真实模型做局部搜索，最大化 DPS |
| `tools/db_census.py [--pool]` | ★ 物品库「总览」9 行对拍 grimtools + 候选池与数据库一致性普查（§3.13） |
| **`tools/res_audit.py`** ★ | **抗性溢出审计**：逐槽 → 来源（底材/镶嵌/附魔/前缀/后缀）→ 字段，并标出每条抗性自带哪些输出向字段（判断「能不能换成伤害」）。**报告里已自动带这一段**（逻辑在 `gd/resaudit.py`，`planreport` §二 与 `plan_audit` 共用） |
| **`tools/absorb_audit.py`** ★ | **伤害吸收来源全库审计**（星座 / 专精 / 装备三层 × % 吸收 / 点数吸收）：`--percent` **只看乘算层（% 吸收）全清单**、`--write` 落盘 `data/absorption_sources.json` + `.md`、`--check` 与离线库比对（**含行内容**，漂移则退出码 1）、`--show-dropped` 看「被判为专精/星座技能副本」而剔除的行。**技能类字段一律四表并集扫**，别单扫一张（陷阱 **#66**） |
| `gd.planreport <方案.json>` | 方案 → 中文 BD 报告，**七节覆盖三要素**：装备表（含/不含武器**自动判定**）/ 抗性表 **+ ★「抗性来源分解」**（逐槽 → 底材/镶嵌/附魔/词缀 → 字段，标出「卡在线上」的维度与**搭便车**带来的 OA/伤害）/ 输出表 / 可穿性 / **⑤技能点**（预算·已投入·超预算告警·精通条·前 15 技能）/ **⑥星座**（现状 + 建议，⚠ `select_coherent` 的加成%加和与真实 DPS **不同向**，见 §3.22）/ 落档命令 |

> ★ **别拿「伤害代理」当伤害**：它是线性加权和，对本流派（攻速 × 流血平伤）权重偏低 ——
> 实测代理 951 的方案真实 DPS 只有 26k，代理 1106 的现状却有 38.5k。
> 要「拉高伤害」必须走 `tools/plan_dps.py` / `tools/tune_dps.py` 的**真实 final_report 模型**。

### 3.5 落档（把方案写进存档）

```bash
$PY tools/plan_to_build.py Sam data/plans/Sam_best.json --out data/plans/Sam_build.json
$PY -m gd build plan  data/plans/Sam_build.json    # 解析 + 静态校验（不写盘）
$PY -m gd build check data/plans/Sam_build.json    # 方案 ↔ 存档 逐槽比对（不写盘）
$PY -m gd build apply data/plans/Sam_build.json    # ★ 写入：自动备份 + 往返校验 + 复检
```

★★ **一整套 BD 要「装 + 技/星」两步**（`gd build` 只管装备，**不碰技能区**）：

```bash
# ① 技能 + 星座：先生成落档方案（差量语义：不在清单里的技能会被**置 0**）
$PY tools/plan_to_save_skill.py Sam \
    --alloc data/plans/pivot/alloc_legal.json \      # 形态加点（**只能含技能/精通**）
    --dev   data/plans/pivot/devotion_star_plan.json \  # 星座 records（全按 1 级）
    --out   data/plans/pivot/save_skill_final.json --note "…"
$PY -m gd.save.skill check data/plans/pivot/save_skill_final.json   # 预演（不写盘）
$PY -m gd.save.skill apply data/plans/pivot/save_skill_final.json   # ★ 六重校验 + 自动备份
# ② 星座点数要跟着扣（`gd.save.skill` **拒绝改 block2**）
$PY -m gd.save.patch --char Sam --devotion-points 0 --no-fit-gear --apply
# ③ 落档后**必须重设锚点**（见 §3.5.1）
```

> ⚠ `--alloc` **不能含星座记录**（含了是「并集叠加」而非替换，陷阱 **#69-B**）——
> 工具会直接拒绝。`--dev` 的 `records` 必须**含 proc 记录**（`*_skill.dbr`），
> 否则退款/差量阶段会把它们判成「该退」（陷阱 **#70-C**）。
> ⚠ 落档差量**按并集一次算**（分两趟会把「升级」误判成「撤点」，陷阱 **#74**）。

`gd build` 的安全设计：写 `.new` → **块校验 / 往返逐字节一致 / 字段逐项吻合 / seed 合法性**
四道复检全过才 `do_backup` + 覆盖，且**游戏进程在跑就拒绝写盘**。

> ★★ **换完装备会自动重算三围**：`apply` 在写盘前按**新装备**跑 `gd.reqfit.solve()`
> （`_auto_fit_attrs`），有变化就就地写入 block2 并清零 `attribute_points`，
> 静态校验后**复核「写入值 vs 目标 / 可穿性」**，不过就拒绝落盘。
> 参数见 §3.8（`--no-fit-gear` / `--fit-buffer` / `--att-safety` / `--fit-prefer` / `--fit-spread` /
> `--fit-allow-short`）；调测试副本时加 `--allow-running` 跳过游戏进程拦截。

> ⚠ 这是**改个人存档**的动作。写前务必：① `gd verify --preflight`；
> ② 游戏内 **设置 → Gameplay 关掉 Cloud Saving**（否则 Steam 用云档覆盖）；
> ③ 确认备份目录（`E:\xz\Archives`，可用 `GD_BACKUP_DIR` 覆盖）里那份是好的。
> ⓘ 备份**只在正式存档目录**触发；改副本调试时自动跳过（不污染 `Archives`）。

### 3.6 ★★ 自动化：一条命令跑完全链路（`gd auto`）

```bash
python -m gd auto Sam                 # 满抗 + 追高伤害（不含武器）→ 方案 + 报告
python -m gd auto Sam --apply         # 再落档：preflight → 备份 → 写入 → 复检
python -m gd auto Sam --with-weapon   # 连主手/副手一起优化
python -m gd auto Sam --goal fullres  # 只求满抗（不追伤害）
python -m gd auto Sam --goal dmg      # 只要伤害（不保抗性）
python -m gd auto --list              # 列角色 + 自动判定形态
python -m gd auto Sam --quick         # 速跑（束宽 1500 / 重启 1 / 局部 1 轮）
python -m gd auto Sam --chains 4      # ★ 4 条独立搜索链并行，取最优（墙钟≈单链、搜索量 ×4）
python -m gd auto Sam --anneal-iters 4000   # ★ 退火按迭代数停 → 完全可复现
python -m gd auto Sam --cap-solver beam     # 满抗搜索退回旧束搜索（约 74 s，仅供对照）
python -m gd auto Sam --no-dom             # 关掉支配剪枝做对照（默认是开的）
```

> **满抗搜索默认走 ILP（阶段 3）**：`gd auto` 的满抗起点由 `tools/ilp_res.py` 的
> **整数规划**求全局最优（Sam lv73 / 14 槽实测 **3.7 s**，含支配剪枝），
> 取代旧的束搜索（**74 s**，且 `GD_FULL=1` 下它的目标退化成「技能分 − 紫装惩罚」，
> **伤害项整个消失**）。
> ★ **(抗性,目标) 支配剪枝**（默认开）：变量 38,267 → 13,034、求解 −62%，
> **解与目标逐位不变**（陷阱 #93）。`--no-dom` 或 `GD_ILP_DOM=0` 可关掉做对照；
> 剪枝结果按**内容指纹**落盘到 `data/cache/prune/`，重跑 **0.00 s**。
> `--cap-solver auto`（默认）先试 ILP，不可行或异常时**自动回退束搜索**，可用性不受影响。
> ⚠ **HiGHS 本机构建只支持单线程**：`threads>1` 或 `parallel='on'` 会把模型跑成
> `Not Set`（0.00 s / gap inf）。加速来自「换了算法」，不是「求解器并行」。
> ⚠ **别指望 `--ilp-gap` 提速**：实测 1e-3 / 5e-3 / 1e-2 / 3e-2 / 1e-1 **耗时与解完全相同**
> （HiGHS 是证完最优性才停，MIP gap 归零）。想调参数请用归因台架
> `$PY tools/bench_ilp_opts.py Sam --extreme`（同进程矩阵，不再靠猜）。

> **`--chains N`（阶段 2）**：N 条链各跑完整一轮（贪心 + 退火），进程间零共享、取最优 ——
> 绕开 Amdahl。`--extreme` 默认铺**逻辑核数**（32；内存封顶后实测 29 条），`--quick` /
> 普通默认 1 条；显式传值永远优先。
> 实测（16C/32T + 45 GB）：只铺物理核时整机 CPU 均值仅 **39.5%**（峰值 60%）；
> 铺满逻辑核后 CPU 峰值 **98.6%**、墙钟几乎不变（77 → 80 s），DPS 85,741 → **86,169（+0.5%）**。
> ⚠ 退火默认按**墙钟**停（`--anneal-seconds`），机器负载会让结果轻微抖动；
> 要**逐位可复现**请用 `--anneal-iters N`。
> 想看 CPU 到底跑没跑满：`$PY tools/cpu_probe.py 45`（对比 16 链 / 32 链）。

内部六步（**全在一个进程**里跑，`gd.opt` 只 import 一次）：

| 步 | 动作 | 要点 |
|---|---|---|
| ① | 读档 | 等级 / 职业 → **自动选形态**（`archetypes.json` 的 `masteries` 比对）；导出 `<Char>_current.json` |
| ② | 满抗搜索 | **ILP 全局最优**（`tools/ilp_res.py` + 支配剪枝，**3.7 s**，原 9.7 s）；失败自动回退束搜索（`GD_FULL=1` + `--goal worst`，约 74 s）；**现状已满抗就跳过** |
| ③ | 真实 DPS 微调 | 单槽 + 双槽贪心；目标函数是 `gd.dps` 的**真实 `final_report`**，不是线性「伤害代理」 |
| ④ | 报告 | `<Char>_auto.md`（装备 / 抗性 / 输出 / 可穿性） |
| ⑤ | 落档（`--apply`） | preflight → 显式备份 → `plan_to_build` → `plan` → `check` → `apply` → `verify` |

**性能账**（Sam lv69→71）：

| | 前 | 后 |
|---|---|---|
| 单次 DPS 评估 | 0.33 s → 0.15 s | **0.0025 s** |
| **满抗搜索** | 束搜索 **74 s**（串行，占 `--extreme` 全程 71%） | **ILP 9 s**（阶段 3，全局最优）；⚠ `gd auto` 进程内实测 17.8~23.9 s |
| 出一套装备 | 手跑 ~30 min | **~8 s** |
| 出装备 + 落档 | ~60 min | **~15 s** |

优化手段（按收益排序）：
1. ★★ **`gd/dps.py` 里 `skills.json`（11 MB）每评估一次就 `json.load` 一遍** ——
   实测占单次评估的 **60%（0.09 s）**。改成 `_tag2rec_map()` 进程内只建一次后，
   **0.150 s → 0.023 s（6.5×）**。这是「全链路慢」的真正瓶颈。
2. ★★ **满抗搜索换 ILP**（阶段 3）：束搜索的 6 次重启用 `ThreadPoolExecutor`，
   而它的主循环是「Python 逐槽展开 + 大量**小数组** numpy 运算」——小数组几乎不释放
   GIL，所以 6 次「并行」实际串行（74 s ÷ 6 = 12 s/次，正是单次耗时）。
   换成整数规划后 **74 s → 9 s（8.2×）**，且是全局最优。
3. 大 JSON（`record_map.json` / `rotation._load` / `skills.json`）全走 `paths.load_json`
   的进程内 memo + pickle 缓存。
4. 现状满抗就不跑满抗搜索（最慢一步）。
5. 候选按（抗性,输出,技能）向量**去重**；候选向量 + DPS 评估全 memo。
6. **并行度 = 逻辑核数**（不是物理核）：16 链时整机 CPU 均值仅 39.5%，
   32 链峰值 98.6%，墙钟不变、DPS +0.5%。

### 3.7 ★ 极限模式（测试算法上限）

```bash
python -m gd auto Sam --extreme            # 放开全部限制，跑一套「极限配装」
```

`--extreme` = ① 候选池开到底（基础 top-400 ∪ **输出排序 top-600**、
镶嵌 32 / 附魔 16）② **允许派系件** ③ **含武器**（主手/副手）
④ 稀有度不限 ⑤ **满抗搜索走 ILP**（全局最优，约 9 s；不用旧的 74 s 束搜索）
⑥ 局部搜索 4 轮 × 双槽 4 × 每槽上限 200 ⑦ **LNS 400 轮**（`--algo lns`；
2026-09-20 晚由「退火 420 s」切换 —— 退火有「预算越大越差」的病灶，见 §3.15 / 陷阱 46）
⑧ **并行**：链级 = 逻辑核数（内存封顶后实测 29 条）；加 `--procs 16` 则改走**邻域并行**（§3.17）。

> `GD_POOL_DMG_TOPN=N` 是极限模式的关键开关：默认候选池按**抗性**排序、且会
> **直接丢弃零抗性装备**，高伤害件根本进不去。并进「输出排序前 N 件」才算真·扩大范围。
>
> 武器合法性：**双手武器时副手不生效**这条游戏规则**在评估层执行**
> （`plan_dps.plan_to_override` 直接丢掉副手）→ 「双手+副手」拿不到收益，
> 搜索自然收敛到合法解。

> ⚠ `gd auto` 默认口径：束宽 3000 / 重启 2 / 局部 2 轮 / 双槽 2 / 每槽实算上限 120。
> 想更快加 `--quick`（束宽 1500 / 局部 1 轮 / 上限 30，Sam ~10 s）；
> 想更狠加 `--thorough`（束宽 8000 / 重启 6 / 局部 3 轮 / 上限 300）。
> 候选会先按**（抗性, 输出, 技能）向量去重**——换名不换数值的候选不重复算 DPS。
> 落档前**仍然**要「游戏退出 + 关 Cloud Saving」——`gd auto` 会替你跑 `preflight`。

### 3.8 ★★ 属性需求闸门与「装备穿不上」修复

装备的需求（体格 / 狡诈 / 精神）**不在 `.dbr` 里** ——
`strengthRequirement` / `dexterityRequirement` / `intelligenceRequirement`
**恒为 0**（实测 12 件全 0）。引擎按**官方公式在运行时算**。

| 诉求 | 一条命令 |
|---|---|
| 只想看差多少（只读） | `$PY tools/reqcheck.py Sam` |
| **只调三围** | `$PY -m gd.save.patch --char Sam --apply` ← **自动拟合** |
| **换整套装备** | `$PY -m gd build apply data/plans/Sam_build.json` ← **自动拟合** |
| 单独跑一次拟合（调试） | `$PY tools/fix_wearability.py Sam [--apply]`（薄封装，走同一 `gd.reqfit`） |

> ★ 旧版要**手跑 `fix_wearability.py --apply`** 才知道该投多少点；
> 现在**写档动作本身就是求解器** —— 三围由 `gd/reqfit.py` 按当前/新装备自动算好并写入，
> 写完再复检可穿性，不过就拒绝落盘。详见下面「自动拟合」一节。

**需求公式（★ 已逐条验证）**

- 公式集由物品字段 **`m`（itemCostName，如 `cf5`/`cf11`）** 决定，方程由**部位**决定。
  数据在 `data/cost_formulae.json`（`cf1..cf13`，31 类方程），由
  `tools/build_cost_formulae.py` 从 `app.asar` 的 `itemCostFormulae` 配对花括号抽出。
- 取值：`Math.round(evaluate(equation, {itemLevel, totalAttCount, …}))`。
  `^` 是幂运算 → 求值前 `.replace('^','**')`。
- **`totalAttCount` = 提示框行数**（GT 用 `W.length`，`S` 已合并前缀/后缀 stats）。
  我们只能近似：`gd.req.total_att_estimate()` 数「非零数值字段」。
  → 所以 `att_safety`（默认 6）是必要的：**饰品方程才有这一项**，装甲/武器不受影响。
- ★★ **武器需求按「记录名文件名词干」判类型**（`gd/req.py: WEAPON_STEM_EQ`），
  因为 `gd/dbr.template()` **运行时返回 `None`**（`.arz` 不在运行时依赖）。
  词干顺序**长的先匹配**（`sword2h` 必须先于 `sword`）；映射：

  | 词干 | 属性 | 方程 |
  |---|---|---|
  | `sword` / `gun1h` / `gun2h` | 狡诈 | `swordDexterityEquation` / `ranged*DexterityEquation` |
  | `dagger` | 狡诈 | `daggerDexterityEquation` |
  | `scepter` / `focus` | 精神 | `scepterIntelligenceEquation` / `offhandIntelligenceEquation` |
  | `axe` / `mace` / `hammer` / `shield` | 体格 | `axeStrengthEquation` / `maceStrengthEquation` / `shieldStrengthEquation` |
  | `sword2h` / `blunt2h` / `axe2h` / `mace2h` / `spear2h` | 体格 | `melee2hStrengthEquation` |

  认不出类型时 `confidence='unknown'`（**不谎报 `official`**）。
  `req()` 返回里新增 `weapon_kind` 字段可查。
  > ⚠ 历史 bug：`CAT_EQ` 里 `'weapon': (None, None)` 让武器分支变成**死代码** →
  > **所有武器需求都被算成 0，且 `confidence` 仍报 `official`**（静默错误，武器拿不起来看不出来）。
  > 现在武器分支**提到 `CAT_EQ` 之前**。实测：剑→狡诈 421、2 手剑→体格 484、权杖→精神 384、盾→体格 581。

- 验证锚点（公式 vs 实测，全部吻合，**按护甲档位分组**）：轻甲盔 `538`、轻甲腿 `662.4`、
  重甲盔 `915.4`、重甲腿 `1035.1`、法系盔(ilvl84) `320.2`；`ilvl1` 两端 轻 `14.8` / 重 `33.3`。

**面板属性口径**（决定阈值比较对不对）

```
面板 = (50 + 8×加点 + 精通逐级 + 装备平值) × (1 + 装备%值/100)
```

- 存档 block2 的 `physique/cunning/spirit` 存的是 **`50 + 8×已投点数`**，
  **不含精通、不含装备** —— 别拿它当面板（`gd verify` 旧版就标错了，已修）。
- 精通曲线是**半值取整**，必须走 `data/mastery_attr.json` +
  `gd.rotation.mastery_attr_of()`。旧的「每级常数」线性表 `MASTERY_ATTR` 会算错
  （Sam 用线性表得 550/967/287，真值 **533/967/270**）。

**求解规则**（唯一实现在 `gd/reqfit.py`）

1. **严格等级预算**：可用点数 = 已花 + 未分配，**不凭空加总点数**。
2. 先同时满足三阈值（取全套最大值），剩余点数全投 `out_key`（= 输出属性）。
3. `--buffer N`（默认 1）：**体格 / 精神各多留 N 点**（⚠ 2026-09-20 修过一个 bug：
   旧实现只在「该属性原本不够、必须投点」时才加余量 ⇒ `pmin=0` 的属性
   **完全拿不到 buffer**，实测 `--fit-buffer 40` 都不动体格。详见 `docs/pitfalls.md` #48）。
   `--att-safety N`（默认 6）：见上。
4. **落档链路里写前自动拟合 + 写后复检**，不过三阈值就拒绝写盘。
5. `spread=True`（`--fit-spread` / `--spread`）时余额按形态 `attribute_bias` 拆分，
   默认 `False`（全给 `out_key`，对输出最省）。

**out_key 选取优先级**：`--fit-prefer`（显式指定）
→ 形态 bias 最大的属性（`data/archetypes.json`，`werewolf` = `{physique:0.35, cunning:0.65, spirit:0.0}` → 狡诈）
→ 当前投得最多的属性。

> **Sam 实例（2026-09-19）**：体质 20 / 狡诈 55 / 精神 4（79 已花 + 1 未分配 = 80）。
> 穿不上 3 件 → 项链「先祖护卫」精神 288、靴子「读石者」体格 **554**、戒指1「黑暗女王之戒」精神 299。
> 重排为 **体格 24 / 狡诈 46 / 精神 10**（存档 242/418/130）→ 14 槽全 ✓。
> 代价：穿刺加成 394.68% → 364.70%，总 DPS 95408 → 94126（**−1.34%**）。
> 依据：体格需求与 `att` 无关（硬值 554），精神在 288–320 间随行数浮动，
> 面板 318 覆盖到行数上沿。

> ⚠ 想**保狡诈不掉伤害**，唯一办法是加点（总点数 80 → 87），那就不再是「重排」而是改总量 ——
> 与「严格等级预算」原则冲突，需用户明确点头。

**★★ 自动拟合已经合进落档链路**（不用再手跑 `fix_wearability`）

```bash
# 只改三围（其余不动）：默认就开拟合，自动按当前装备算出并写入
$PY -m gd.save.patch --char Sam --apply                # 自动 → 242/418/130

# 换整套装备：按「新装备」拟合，同一事务写 block2 + 复检
$PY -m gd build apply data/plans/Sam_build.json        # 自动按新装备算三围

# 可调参数（`gd.save.patch` 与 `gd build` 基本一致）
--fit-gear / --no-fit-gear    # 显式开/关（默认：没显式给三围就自动开；build 只有 --no-fit-gear）
--fit-buffer N                # 体格/精神各留余量，默认 1
--att-safety N                # 饰品提示框行数安全余量，默认 6
--fit-prefer physique|cunning|spirit   # 强制余额投向（fix_wearability 里叫 --prefer）
--fit-spread                  # 余额按形态 bias 拆分（默认全给 out_key；fix_wearability 里叫 --spread）
--fit-allow-short             # 允许「点数不够、穿不上」也照写（默认拒绝）
--save-dir <目录>             # 指定存档目录（patch / reqcheck）；build 用 GD_SAVE / --allow-running
```

行为要点：

- **`gd/save/patch.py`**：写盘前自动 `reqfit.solve()`，有变化就追加
  `physique/cunning/spirit` 三条 edits + 把 `attribute_points` 清零。
- **`gd/build.py`**：换装备后在 `do_apply` 里按**新装备**重新拟合，
  `struct` 就地打进 block2；静态校验后新增「三围 vs `rf.targets` / 点数清零 / 可穿性」复核。
- **写后复检可穿性**：开了拟合就**必须过**，否则拒绝写盘；`--no-fit-gear` 时只强烈警告不拦。
- 三围没变化（本来就能穿）时，`patch` 报「✓ 当前三围已满足全套装备需求」直接返回。
- 两条链路都**只在正式存档目录才备份**（改副本调试时跳过，不污染 `E:\xz\Archives`）。
- `gd/reqfit.py` 支持 `save_dir=` / `paths.save_dir_override()`，
  测试副本时「读档」与「写档」指向同一目录（否则会拟合真档、写副本 → 结果对不上）。

**可用性检查（只读）**

```bash
$PY tools/reqcheck.py Sam                 # 按记录名逐件列需求 + 缺口（含全部武器套）
$PY tools/reqcheck.py Sam --att-safety 6  # 给饰品行数加安全余量
$PY tools/reqcheck.py Sam --json out.json
$PY tools/reqcheck.py Sam --save-dir <副本目录>   # 从副本读（配合测试）
```

---

### 3.9 ★ 读社区构建（grimtools 配装链接 → 本地清单）

```bash
$PY tools/gt_build.py ZyDo860V                    # 短 ID：自动抓一次并缓存页面
$PY tools/gt_build.py https://www.grimtools.com/calc/ZyDo860V
$PY tools/gt_build.py --html data/cache/gt_ZyDo860V.html   # 用已缓存页面（零联网）
$PY tools/gt_build.py ZyDo860V --json out.json    # 归一化构建 → 喂给引擎对拍
$PY tools/gt_build.py ZyDo860V --dump it13922     # 看单个 id 的原始字段
```

输出：装备 14 槽（含词缀/组件/附魔）、职业技能加点、星座（按星座聚合星数）、
**伤害转化汇总**、**抗性削减汇总**。

实现要点：
- grimtools 的 `/calc/<短ID>` 页面把**完整构建 JSON** 嵌在 `<script>window['buildInfo']=…</script>`，
  ID（`it####`/`sk####`/`aa####`）**就是本地离线库的同一套命名** ⇒ 不需要抓渲染后的 DOM。
- `sk####` 的字段取自 `data/cache/itemdb.js`（走 `tools/dump_ids.js`，node 平衡扫描 + 键加引号 + eval）。
- `tag → 中文` 走 `gd.gear.load_tags()`（16563 条）；`sk → 星座` 走 `data/devotion_tree.json`。

> ⚠ 字段口径：**减抗不是 `ResistanceReduction` 字段，而是技能上的负值 `defensive<类型>`**
> （逐级数组，如刺骨战吼 lv12 `defensivePierce:-30`）。完整口径见 §3.10 与
> `refs/plan_rr_conversion.md`。

---

### 3.10 ★★ 敌方减抗（RR）与实战伤害 —— `gd/rr.py`

**为什么要它**：伤害代理是**线性加权和**，看不见「敌人抗性」乘区。实测 Sam lv71 的
刺骨战吼给 −30% 穿刺抗，对 33% 抗性的精英 = **1.45 倍**伤害 —— 旧代理里这类词条
**一分都拿不到**，优化器会拿它去换「看起来更值钱」的 +100% 线性伤害。

```bash
$PY -m gd rr Sam                 # 减抗汇总 + 5 档敌方抗性的换算表
$PY -m gd rr Sam --scan          # 全库减抗字段普查
$PY -m gd dps Sam --enemy-res boss
$PY tools/autobuild.py Sam --quick --enemy-res high --real-goal vs
```

**三族口径**（依据 = `data/` 里 16563 条本地化格式串，不是猜的）：

| 族 | 字段 | 游戏显示 | 合并方式 |
|---|---|---|---|
| **A** | 技能上的**负值** `defensive<类型>`（逐级数组） | `−X 目标抗性` | **按类型叠加** |
| **B-%** | `offensive{Total,Elemental,Physical}ResistanceReductionPercent` | `−X% 目标抗性降低` | **取最强**，最后**乘算** |
| **B-abs** | `…ResistanceReductionAbsolute` | `−X 目标抗性`（无 %） | **叠加** |

```
r = base_res − Σadd
r ×= (1 − max_pct/100)
r  = max(r, −100)
mult = (100 − r_eff) / (100 − base_res)        # base_res ≥ 100%（免疫）时分母钳到 1
```

**敌方档位 profile**：`none 0 / elite 33 / boss 50 / high 80 / max 100`，默认 **精英档**。
用户口径是「标称最高 100% 不是硬指标，逼近即可、性价比合适就行」⇒
减抗**不作为优化硬目标**，靠两个折扣旋钮调「合适」：
`GD_RR_UPTIME`（debuff 覆盖率，默认 0.7）、`GD_RR_PCT_FACTOR`（B-% 族打折，默认 0.5）。

**迭代线性化**：减抗维度的权重不是常数，由真实口径动态标定 ——
边际比 `(100 + pct_main) / (100 − res_main)`（Sam：+851% / 剩 3% ⇒ **9.8×**）。
`autobuild` 在搜索前用现状真实评估重标一次，再清 `_CV`/`_RR_OF`/`_CONV_OF` 缓存。

**伤害转化**（不再用白名单 hack）：`损失比例 = X% × 源伤害占比 × (m_in − m_out)/m_in`，
折成「主桶等效 %」。源伤害占比来自**真实评估**的 `dps_by_type`，而非假设纯物理。
⚠ 实测转化**不只在武器槽**：全库 **2458 件**物品带转化、分布在整个 14 槽、
294 种 `(in,out,pct)` 签名 ⇒ 逐件估值 + 迭代线性化（原计划的「武器桶枚举」前提不成立）。

**技能点侧的边际分析**（`gd/opt.py` 注释里许诺的 `tools/rr_skills.py`）：

```bash
$PY tools/rr_skills.py                 # 减抗技能「分/级」排行（含来源折扣）
$PY tools/rr_skills.py --enemy-res boss --top 30
```

来源折扣：职业树 1.0 / 星座 0.7 / **物品触发技能 0.25** / 其他 0.5。
不给折扣的话 `item_sacredbalance`（神圣平衡）这类触发技能会算出 **+286 分/级**，
高出常驻技能一个数量级 —— 就是靠这个折扣压回 44.6 分/级。

---

### 3.11 ★★ 社区构建回归对拍 —— `tools/gt_regress.py`

`tools/gt_build.py` 只做「读懂」，`gt_regress.py` 做「**重算 + 对拍**」：
把任意 grimtools 社区的 `buildInfo` 还原成引擎口径的构建 → 用**同一套 `final_report`** 算一遍 →
与面板/冻结值逐项比。

```bash
$PY tools/gt_regress.py ZyDo860V                       # 报告（离线，读缓存页面）
$PY tools/gt_regress.py ZyDo860V --json                # 一行 JSON（批量/CI）
$PY tools/gt_regress.py ZyDo860V --emit-ref R.json     # 出对拍模板（measures 全空）
$PY tools/gt_regress.py ZyDo860V --ref R.json          # 与人工录入的面板值对拍
$PY tools/gt_regress.py ZyDo860V --golden              # 与冻结值对拍（抓自身数值漂移）
$PY tools/gt_regress.py ZyDo860V --golden --write       # 覆盖冻结值
$PY tools/gt_regress.py --ids A,B,C --golden            # 批量
```

**离线约束（重要）**：面板数字是页面里 `calc.js` **在浏览器里现算**的，HTML 里没有
（实证：`gt_ZyDo860V.html` 只有标签文本）。项目约定不抓网页/不用 CDP ⇒
参考值**录入一次**存 `data/regress/<ID>.ref.json`（`measures` 留 `null` 的键会被跳过）。
另有一条**不需要人工录入**的回归：`--golden` 冻结本引擎的 43 项指标，
之后任何代码改动只要动了数值就会亮红。

**技能解析**（这条链路是本工具最容易静默失败的地方，实测踩了三坑）：

| sk 来源 | 路由 |
|---|---|
| 职业树 / 精通 | `itemdb.js` 的 `name` → tag → `data/mastery_skills.json[tag].record` |
| 星座星点 | `calc.js` 的 `skillDisplayName` → tag → `data/devotions.json` 同 tag 候选中按**字段签名**唯一匹配 |
| 星座 proc（celestial power） | 上面拿不到 tag/tag 无候选时：`devotion_tree.json` 归属兜底 + `tagDevotionEffect*` 别名池补位 |

**已知口径差异**（报告里会单独列出，不是 bug）：
* **随机骰词缀** `pre####`/`suf####`/`aa####` 是 `LootRandomizer`，骰出的具体数值
  只在 GT 自己的 `itemdb.js` 里；`aa####` 连记录都没有 ⇒ 我们按 **dbr 模板值**算，这些槽必然偏。
* ★ **OA/DA/暴击/命中自伤害模型 v2 起已进引擎**（见 §3.12），所以它们**能**对拍；
  但这份 grimtools 参考值的键表还没扩（`TOL` 里没有这些键）—— 要扩直接往 `TOL` 加。
* `skills[].level` 实测是**已分配点数**（ZyDo860V 实测 305 = 满级角色技能点预算，
  且无一技能超过 `max_level`）⇒ 装备 `+技能` 仍由引擎按 `skill_plus` 叠加。
* 抗性面板有**难度惩罚**（终极 −50%）：`--res-penalty 50` 对齐。

**不变身构建**：`gd.dps.guess_arch` 只认 `werewolf1`/`wereraven1`，而社区大量构建是
人形态。本工具按 `archetypes.json` 的 `core_skills` 重合度挑打法，
并加两道硬门槛（`root_skills` 必须在构建里、专精必须匹配）——
否则 `wolf_nightblade`（重合度 8，最高）会把「没点形态技能」的人形态构建算成狼人。

#### 3.11.1 第三条通道 `--sheet`：**游戏内面板真值**（唯一能证伪「引擎 == 游戏」）

前两条通道都对不上游戏本体（`--ref` 是社区构建、`--golden` 是我们自己）。
`--sheet <角色名>` 读 `data/regress/<角色名>.sheet.json` —— **手工誊录的游戏面板**
（角色二/三页：OA/DA/攻速/暴伤/面板 DPS；角色一页：10 项抗性；三页：各伤害类型 +%）。

```bash
$PY tools/gt_regress.py --sheet Sam --emit-sheet   # 出模板（engine_values 已算好）
#   → 打开 data/regress/Sam.sheet.json，把 in_game 段的 null 照面板改成真实数值
$PY tools/gt_regress.py --sheet Sam                # 对拍
```

* 键名是**游戏面板的中文叫法**（不是引擎内部字段名）—— 誊录的人照着面板抄，
  不该先做一次「类型键翻译」。
* **留 `null` 的键会被明确打印为「未填写」，不静默跳过**；一个都没填时打印
  「这套对拍还没有产生任何证据」并**不当作通过** —— 覆盖率是这套方法唯一会骗人的地方。
* 超差 ⇒ 非零退出码（可直接进 CI / 定时任务）。

---

### 3.12 ★★ 伤害模型 v2/v3：官方 Step 2–9 全流水线（`gd/dmg.py` · `gd/combat.py` · `gd/enemy.py`）

2026-09-19 重建。地基是**离线库里一直躺着、从未启用的两层官方数据**：

| 产物 | 来源 | 内容 |
|---|---|---|
| `data/combatformulas.json` | `itemdb.js` 内嵌的 `window.combatformulas` | PTH 阈值/倍率/下限、护甲吸收 70%、6 部位概率、DoT 语义原文 |
| `data/monster_stats.json` | asar 里 `/dist/monsterdb/js/monsterdb.js`（9.3 MB） | `window.allMonsters` **2840 只怪**的 10 型抗性真值 + DA/OA 逐级方程 + 难度/玩家数修正表 + ★ **247 个被动技能**（抗性/护甲/生命的真正来源，见下） |

```bash
$PY tools/extract_combatformulas.py     # → data/combatformulas.json
$PY tools/extract_monsterdb.py          # → data/monster_stats.json（+ 缓存 9.3 MB）
$PY -m gd enemy --dummy --level 100     # 训练假人真值
$PY -m gd enemy --pool 'Champion+Hero@0.5' --level 71
$PY -m gd enemy --panel m3955           # ★ 复现「游戏面板」逐字段（两排 10 型抗性 + 护甲）
$PY -m gd enemy --panel m3955 --difficulty elite --players 3
$PY -m gd enemy --find Kymon            # 按 tag 子串搜怪 → 拿 mid
$PY -m gd dps Sam --enemy-profile pool:Champion+Hero@0.5 --enemy-difficulty ultimate
$PY -m gd dps Sam --enemy-profile m3955 # 直接对某只真值怪（tag 或 id 都行）
$PY -m gd dps Sam --enemy-armor 300 --enemy-armor-reduce 120   # 覆盖护甲/破甲（默认从被动技能自动取）
$PY -m gd rr --scan                     # 减抗字段普查 + **破甲字段普查**（实测，非写死）
```

**四个模块**（全部**纯函数式**，各自带离线自证）：

| 模块 | 职责 | 自证 |
|---|---|---|
| `gd/combat.py` | Step 4/5/6：PTH 方程、暴击窗口（含 **PTH>100 时总面数 = PTH**）、护甲 DLEP/DGP + **吸收按官方 `f62k` 推导**（`absorption_of` / `monster_absorption` = 56%）、**破甲算式**（`armor_after_reduce`）、`armor_applies` 判据单点、DoT 通道 | `selftest_identities()` **25 项** |
| `gd/dmg.py` | Step 2/3：来源拆解、三步转化链（技能专属 → 全局 → 护甲穿透**排最后**）、% 加成按最终类型 | `selftest_cases()` 9 项 |
| `gd/enemy.py` | 敌方真值：`m<id>` / `tag` / `pool:Champion+Hero@0.5` / 五档手填；★ **`panel_of()` 逐字段复现游戏面板**（含被动技能层） | selftest [18] + **[24] 组 17 项** |
| `gd/rr.py` | 敌方减抗（10 桶 × 3 族）+ **破甲收集/普查**（`armor_reduce_of*` / `armor_reduce_census`，实测而非写死） | selftest [16] + [23] 组 |

**每击流水线**（`gd/rotation.py::final_report`，①→⑩）：
来源拆解 → 技能专属转化 → 全局转化 → 护甲穿透（门槛 `weaponDamagePct>0`，作用整击残余物理）
→ 按最终类型取 % → **命中/暴击期望** → **护甲 + 破甲**（逐类型裁决，只有物理过甲）
→ **敌方抗性（逐桶真值）** → **DoT 时间轴**（同源刷新 / 异源全额叠加，`min(1, 时长×频率）`）→ 汇总。

**三个口径**（都在 `rep` 里，互不覆盖）：

| 键 | 含义 | 用途 |
|---|---|---|
| `rep['dps']` | **面板**（含 DoT 覆盖率修正，不含命中/暴击/敌方抗性） | 与 grimtools 面板逐项对拍 |
| `rep['dps_real']` | 面板 × 命中暴击期望 | 真实输出 |
| `rep['vs']['dps_vs']` | 实战口径再叠敌方减抗（**逐桶真值**，不再是五档 33%） | 优化目标（`GD_REAL_GOAL=vs`） |

**★★ 敌方抗性：**面板是 10 型、分两排**，且有一层「被动技能」此前整块没接**

用户 2026-09-20 指出：**每只怪都有自己的元素抗性，面板上「下面还有一排」**。查证后
确认——官方页面 `/dist/monsterdb/js/db.js` 逐字的类型表是：

```js
ml   = "Fire Cold Lightning Poison Pierce Bleeding Life Aether Chaos Physical".split(" ")
//     ↑ 前 5 型 = 面板第一排            ↑ 后 5 型 = 面板第二排（截图没拍全的那排）
Dl(a,c,b){ var d = Y(a["defensive"+c]);
           if("Fire"==c||"Cold"==c||"Lightning"==c) d += Y(a.defensiveElementalResistance);
           return b ? Math.min(d, 500 + Y(a["defensive"+c+"MaxResist"]) + Y(a.defensiveAllMaxResist)) : d }
```

抗性 = **三层相加**，缺任何一层都算错：

| 层 | 内容 | 落在哪 |
|---|---|---|
| ① 怪物记录 | `defensiveFire` / `defensiveBleeding` …（10 个伤害型 + 护甲等） | `monster_stats.json::monsters[mid].recdef` |
| ② 难度修正 | `defensiveFire: [0,0,0,0, 4,6,8,11, 8,10,13,16]`（索引 = `4*(难度−1)+玩家数−1`） | `…adjustments` |
| ③ **被动技能** | 2721/2840 只怪有；**第二排抗性与护甲几乎全来自这里** | `…passive_skills` + `monsters[mid].passives` |

第三层的三条语义（逐字来自官方 `charEquationValue` / `buildSkillData` / `mergeSkillData`）：

* **技能等级 = `floor(evaluate(skillLevelN 表达式, {charLevel}))`** —— 如 `charLevel/4+1`。
* **等级索引数组取值 = `arr[level-1]`** —— 自证：`sk1260.defensiveProtection[99] = 1607`
  且该技能等级 = `charLevel*1` = 100 ⇒ 与游戏面板「护甲等级 1607」逐位一致。
* **合并 = 数值相加**（`mergeSkillData`）。
* 另有一条通道：**`defensiveElementalResistance` 一个字段同时加给火/冰/电**。

**★ 逐字段复现游戏面板**（`gd/enemy.py::panel_of()`）—— 这是本层的验收面：

| 字段 | 游戏截图 | 复算 | 来源 |
|---|---|---|---|
| 体格 / 灵巧 / 精神 | 856 / 1188 / 1188 | 855.8 / 1188.0 / 1188.0 | 方程 `eq145` × (1+难度修正) |
| 攻击能力 | 2477 | **2477.2** | 难度 −8% ＋ 被动 `sk4685` **−1%** |
| 防御能力 | 2254 | **2254.4** | 难度 −8% |
| 生命 | 4,667,811 | **4,667,811.0** | 难度 +580% ＋ 被动 `sk1260` `characterLifeModifier[99]` **+57%** |
| 能量 | 68,563 | **68,563.2** | 同上（`characterMana`） |
| **护甲等级** | **1607** | **1607.0** | 被动 `sk1260.defensiveProtection[99]` |
| 抗性（5 型） | 火58 冰5 电10 毒10 穿5 | **全中** | 记录 ⊕ 难度修正 |
| 抗性（第二排 5 型） | *截图未拍全* | 流血 **85** / 活力 12 / 以太 25 / 混乱 25 / 物理 2 | ①+②+③（流血来自 `sk4685.defensiveBleeding`） |

目标怪是 **`m3955` = `tagGDX3Nemesis_Outlaw02`**（终极 / 100 级 / 1 人），
反查依据是全库**抗性序列 (58,5,10,10,5) 唯一命中**。这 8+10 项已固化成 **selftest [24]**。

### ★★ 换靶子会换最优解 —— 搜索链路必须显式给 `GD_ENEMY_PROFILE`（2026-09-21）

`gd auto` / `tune_devotion` / `tune_skills` 的评分都走 `plan_dps`，
而它读 **`GD_ENEMY_PROFILE`**（不传 ⇒ **默认「等级池」档，该档没有护甲值**）。
⇒ 想「对某只真值怪最优」，必须把变量**带到搜索那一步**：

```bash
GD_ENEMY_PROFILE=m1281 GD_OBJ=total $PY -m gd auto Sam --extreme --archetype wolf_nightblade_fast
PLAN=… GD_ENEMY_PROFILE=m1281 $PY tools/tune_devotion.py --realloc
PLAN=… GD_ENEMY_PROFILE=m1281 $PY tools/tune_skills.py --mode swap --rounds 20
```

**实测差异（同一个角色，只换靶子）**：默认等级池档（无护甲）下最优的装备，
换成**罗卡**后只有 **−9.37%**；而按罗卡重搜出来的装备，打罗卡 **+24.0%**、打等级池 **−9.37%**。
⇒ **「最优」永远是相对某个靶子的**，报告必须写清靶子（`docs/pitfalls.md` #52 同族）。

| 靶子 | 特点 |
|---|---|
| **等级池**（默认 `pool:<n>:…`） | 聚合分位数；**没有单一护甲值** ⇒ 物理直伤是「下界、不打折」 |
| **`m3955`** = Nemesis Outlaw | 护甲 1184，抗性偏低（穿刺 5 / 流血 80） |
| **`m1281` / `m3699`** = **罗卡（Lokarr）** | SuperBoss、**护甲 1029**、抗性 **82–92%**（社区 DPS 基准王）；两只只差 OA/DA |
| `m4139` / `m1294` | 训练假人（**无护甲**，`--dummy`） |


⚠ **两个易踩的坑**（都已修，别再犯）：
* 官方方程里的 `^` 是 **JS 幂运算**，不是异或 —— 用 Python `ast` 直接解析会静默退化成 0
  （`characterLife = ((charLevel*60)^1.53)+30000` 一直算不出来）。`_eval()` 现在会先 `^`→`**`。
* 怪物记录里的 `defensiveProtection` **确实不存在**，但**不代表没有护甲** —— 它在被动技能里。
  「离线库无怪物护甲」这个结论是**错的**，已从文档与代码注释中撤掉。

**★ 护甲 / 破甲：按输出循环的主要类型逐条裁决**（`rep['armor']['by_type']`）

用户口径：「怪物元素护甲值不用管，只要保证**输出循环里每个主要输出类型对应的护甲/破甲
都被考虑到了**」。落地成一张按类型逐条的裁决表，四条事实都在里面：

| 事实 | 值 | 依据 |
|---|---|---|
| 护甲只吃**物理直伤** | 穿刺/创伤/元素/流血 一律 `armor_affected=False` | 官方两条护甲公式的输入变量逐字是 `physicalDamageDV`；判据单点 `gd/combat.py::armor_applies()` |
| **「元素护甲」不存在** | 元素的减伤通道是**抗性**（10 个桶），不是护甲 | 同上。用户口径「怪物元素护甲值不用管」与官方机制一致 —— 该管的是**抗性**（上一节） |
| 怪物护甲**吸收率 = 56%** | `70 × (1 + (−20)/100)`；引擎/玩家基准 70% | `engine.armorDefensiveAbsorption` × 调整表 `defensiveAbsorptionModifier = −20`（三难度同值）。**是推导值**，函数返回依据串 |
| 玩家侧**没有破甲机制** | 本角色破甲恒为 0 | 实测普查（`gd rr --scan`）：`DamageDurationDefensiveReduction`「点目标护甲降低」在 `itemdb.js`/`skills_json.js` **0 次**，只在 `monsterdb.js` **80 次** |

★ **玩家的「破甲」其实是护甲穿透**：`offensivePierceRatio`（武器「%护甲穿透」）把
**残余物理转成穿刺**，穿刺**绕过护甲** —— 它在转化链最后一步（`gd/dmg.py::_apply_pierce`），
不是「降低目标护甲」。别把两者混为一谈。
吸收修正是**乘法缩放**（`base×(1+mod/100)` ⇒ 70→56），不是加减百分点（70−20=50 是错的）。
破甲值走挂点 `armor_reduce` / `--enemy-armor-reduce`（先减甲、下限 0、再算减免）；
当前数据下恒为 0 —— 挂点而非死参数（selftest [23] 用造值 75 证明通路）。

**★ 已知近似与缺口**（如实标注，不猜）：
* **护甲值现在拿得到了，而且会自动套用** —— 来自被动技能的 `defensiveProtection`
  （等级索引数组），与游戏面板「护甲等级」逐位一致（`gd/enemy.py::armor_of`，自检 [24]）。
  `--enemy-profile m<id>` 时 `gd/dps.py::resolve_enemy_armor()` 会**直接把档位里的护甲喂进模型**，
  不再需要人工 `--enemy-armor`（显式参数仍然优先，自检 [24] 有 3 项守着这条契约）。
  ⚠ **等级池（默认档）与五档手填没有单一护甲值**（池聚的是分位数）⇒ 该行留空、
  物理直伤报**下界**，报告里如实标注来源，不编数。
* 吸收率 56% 是从官方常数推出来的（见上表）。
* 敌方方程在 lv1 量级与用户引用的游戏值仍有差异（假人 lv1 我们算 223.2）⇒
  精确形式待 §3.11.1 的 `--sheet` 用游戏实测标定。

**`crit` 维度的口径撕裂（已修）**：`gd/opt.py` 过去给 `crit` 写死权重 `1.00`，
而 v2 之前模型对暴击**零响应** ⇒ 优化器把「+40% 暴击伤害」按线性 % 伤害估价。
现在 `recalibrate_crit()` 由**真实暴击口径推导**：

```
W_crit = W_total × [(暴击率/100) / 期望倍率] ÷ [1/(100 + pct_ref)]
```

`pct_ref` 是**调和等效**加成基准（`Σ_t share_t/(100+pct_t)` 的倒数 − 100）——
用算术均值实测偏 1.7%，调和口径下与有限差分**吻合到 0.2%**（selftest [21] 断言）。
Sam 实测：每点全伤害 38.5 / 暴伤 15.7 / 穿刺 26.8 / 流血 8.1 实战 DPS
⇒ `W_crit = 0.53`（写死值 1.00 **高估 1.88 倍**）。
边际测量入口：`tools/plan_dps.py::marginals(char)`。

---

### 3.13 ★★ 物品库「总览」对拍 + 候选池与数据库一致 —— `tools/db_census.py`

**为什么要它**：用户拿 grimtools 物品库左栏那块「总览」（9 行数字）来问
「装备池子是否与数据库一致」。**这是唯一公开口径**，必须能逐行复算 ——
不能靠「看起来差不多」。

```bash
$PY tools/db_census.py                     # 9 行对拍表（站点值 = 2026-09-20 用户截图）
$PY tools/db_census.py --pool --level 71   # 追加：优化器 14 槽候选池普查
$PY tools/db_census.py --json
```

**权威算法**在 `data/cache/itemdb_page.js`（= asar `/dist/db/itemdb/db.js`，221 KB，
grimtools 物品库页面渲染器）。渲染函数 `Ei(a)` 里 7 行是**直接取某个顶层表的键数 /
数组长度**，只有 2 行是**变量** —— 而这两个变量正是唯一两处「本地 ≠ 站点」：

| 行 | 站点 | 本地算法（逐字来自 `Ei` 的变量） |
|---|---|---|
| 物品总数 | 8301 | `Bj` = `allItems` 减 `l:"ItemNote"`（游戏内书籍/日志）**311** 条 ⇒ 8612 − 311 |
| 前缀 / 后缀 | 1745 / 2360 | `Cj(prefixes)` / `Cj(suffixes)` = 键数 |
| Ascended Affixes | 989 | `Cj(ascendedAffixes)` —— 页面变量绑的是 **`window.ascensionAffixData`**，**不是**只有 4 键的 `ascensionAffixes` 配置表 |
| 套装 | 199 | `Cj(itemSets)` |
| 专属掉落 | 2489 | `MIs.length`（`MIRefs` 的键集） |
| 独特稀有 | 66 | `uniqueRares.length` |
| Awakened Items | 92 | `window.awakenedItems.length` |
| 物品技能修正 | 3297 | `Ej` —— **不是** `itemSkills` 表长（4268） |

★ **`Ej` 的语义**（`Ci()` → `Xj()` 的末尾那句 `0==h.indexOf("modifierSkillName") && (b[k]=!0)`）：
**5 张表（allItems / prefixes / suffixes / ascendedAffixes / itemSets）里出现过的
`modifierSkillName*` 值的去重并集** = 2008 + 132 + 0 + 923 + 234 ⇒ **3297**。
`itemSkills` 全表 4268 是**记录总数**，其中 **971 条没有任何物品/词缀/套装引用**
（GDX3 等版本遗留 / 被 `LootRandomizer` 反查过的中间态）⇒ 站点不计入。

⇒ **两句话结论**：多出来的 311 是**游戏内书籍**（不是装备），
少掉的 971 是**从未被任何装备引用的孤儿技能记录**。**装备本体一件不少。**

**★ 候选池普查**（`--pool`）暴露的三类不一致，2026-09-20 已全部修掉：

| 症状 | 根因 | 修法 |
|---|---|---|
| `戒指2` 自动池**恒为 0**（戒指1=130） | `auto_pool` 用无条件 `break` 分槽，`SLOTS` 里「戒指1」在前 ⇒ **与已修过的「副手恒空」同源**，只覆盖了 `('主手','副手')` | `continue` 名单扩成 `('主手','副手','戒指1','戒指2')` |
| 池里有 4 件**类别 ↔ 槽位不符** | 50 级时代手工名单，硬编码池绕过 `slot_ok` / `_is_quest` | `it823`（**任务项链**）整件剔除；`it486`/`it483`（`Amulet`）归位项链；`it14477`（`Medal`）归位勋章 |
| 4 件「(用于所有护甲)」组件过不了 `comp_ok('头部')` | `COMP_WORDS['头部']` 只有 `('头盔','帽')`，缺 `'护甲'` ⇒ **池与校验器自相矛盾** | 补 `'护甲'` |
| 戒指两槽手工名单不同源 | 两槽各写一份（戒指1 独有 3 件、戒指2 独有 2 件） | 抽 `RING_BASE` 常量，**两槽共用**（结构性杜绝再分叉） |

**实测收益**（Sam 满抗 ILP，`--with-weapon`）：抗性合计 **1120 → 1149（+29.0）**，
最优戒指从 `it972`（皇冠印玺，仅在旧手工名单里）换成 **`it1009`（黑卫士印戒）**
—— 它**不在任何一张旧手工名单上**，是修复后才被 `auto_pool` 放进池的。
`gt_regress ZyDo860V --golden` **43 项 0 漂移**（池改动不碰引擎回归）。

**回归守卫**：`selftest [25]`（9 行对拍 + `ItemNote` 311 + 孤儿技能 971 +
池内每一项过 `slot_ok`/`comp_ok` + 无幽灵 id + 无任务物品 + 成对槽位同源）。

---

### 3.14 ★★ 追极限的两把工具：补合法词缀 + 方案体检（2026-09-20）

用户口径：「找一下 **夜刃+狂战士 71 级在满抗性下的极限伤害构建 db**」——
要「在满抗硬约束下真的最高」，光靠装备前缀/后缀维度是死的（见坑 43/44），
且报告里的「伤害」是排序代理。这两个工具补上缺口，都只做编排、不复制公式。

```bash
PY=C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe

# ① 给方案里的绿装槽补**合法**词缀，按真实 DPS 择优
#    （槽位级分解：词缀与底材可加、槽位间独立 ⇒ 严格正确，不引入近似）
$PY tools/affix_fit.py data/plans/X.json --char Sam \
    --arch raven_nightblade --alloc data/scratch/alloc71_raven_nightblade.json \
    --ilvl 71 --topn 16 --out data/plans/X_词缀.json

# ② 方案体检：真实 DPS 三层口径 + 命中/暴击 + 主输出 + 属性重排 + 口径敏感性
$PY tools/plan_audit.py Sam data/plans/X_词缀.json --arch raven_nightblade \
    --alloc data/scratch/alloc71_raven_nightblade.json --attr auto --proj 1,4,8
$PY tools/plan_audit.py Sam data/plans/X_词缀.json --json      # 机器读

# ③ 出方案报告时**直接带体检段**（推荐 —— 手工贴的段一重生成就丢）
$PY -m gd.planreport data/plans/X_词缀.json --char Sam \
    --archetype raven_nightblade --level 71 --audit --audit-proj 1,4,8 \
    --out data/plans/X.md
```

- `affix_fit` 的合法性 = 底材类别码（GT `l`，如 `c24`）+ `maxAffixes`（紫/蓝 0、绿 1 或 2）
  + `affix_ok()` 槽位适用性；三条**全部**满足才进候选。
- `plan_audit` 的 `--attr auto` 会跑 `gd.reqfit.solve(override=…, prefer=…)` 四轮
  （狡诈/精神/体格/默认）取最优，**硬约束是「重排后全套装备仍穿得上」**。
- `--proj 1,4,8` 是**口径敏感性**：对多投射物形态（`projectile_hits`）报
  「假设不成立时会掉到多少」，避免拿一个场景假设当既成事实交付。

**实测链路**（夜刃 `class04` + 狂战士 `class10`，lv71，终极满抗）：

| 步骤 | DPS | 增量 |
|---|---|---|
| 极限搜索基线（`--chains 30 --anneal-seconds 420`） | 94,474 | +178.4% vs 现状 33,930 |
| ＋ 合法词缀（主/副手各 1 条 `pre4474`） | **118,334** | +25.26% |
| ＋ 属性重排（狡诈 72 / 体格 11 / 精神 0） | **121,985** | +3.09% |

口径分层：面板 **67,479** ｜ 含命中 **55,220**（×0.8183）｜ 含减抗 **118,334**（×2.143）。

> ⚠ **这一节的数字已被下节的 LNS 搜索器刷新**（2026-09-20 当晚）：同一条链路
> 现在到 **167,474**。上表保留的是「退火 420 s × 30 链」时代的结果，作为**算法换代的对照基线**。

---

### 3.15 ★★ 搜索器换代：LNS 大邻域搜索替代模拟退火（2026-09-20）

用户口径：「放弃退火这个方案 太慢了 有没有更优秀的算法 可以去 GitHub 找一下思路」。

**GitHub 上的答案 = LNS（Large Neighborhood Search）**
- Pisinger & Ropke 2010《Large Neighborhood Search》（Handbook of Metaheuristics）
  是教科书级做法；`N-Wouda/ALNS` 是 Python 参考实现（含 ILS/VNS/GRASP 作特例）。
- **OR-Tools CP-SAT 官方 Primer 直接写：LNS 往往优于其他所有方法** ——
  把问题变成「小邻域子问题」交给求解器做 destroy & repair，而不是逐个造邻居。
  CP-SAT 自己的日志里就挂着 8 个 LNS 子求解器。

**退火为什么慢**（实测归因 —— `tune_dps.anneal_search` 的两个结构性缺陷）
1. 温度挂在**墙钟**上 ⇒ 420 s 预算下指数降温极慢，大半时间在高温随机游走；
2. `moves` 随预算变大（`≥180 s` 改用四槽联动），而随机挑 4 槽几乎不可能同时满足
   满抗 ⇒ 绝大多数迭代 `okp=False` **直接 `continue`，连评估都没做**。
   实测：**100 轮里只有 65 次真实评估**。
⇒ **预算成了负资产**：从 30 链最优解（94,474）再退火 47 s 就到 117,457，
  而 30 条链各跑满 420 s 只到 94,474。

**新实现 `tune_dps.lns_search`** —— 每轮 = 一次 destroy & repair
- **destroy**：随机挑 k 个槽（`ks=(2,3,4)`）放开，其余槽**硬固定**
- **repair**：邻域内**精确枚举** —— 每槽按线性代理取 `topk` 个候选，k 槽笛卡尔积用
  **numpy 广播**一次算完（抗性和 + 代理和），再按「剩余抗性需求」掩码剪枝
  ⇒ 邻域内**所有满抗组合都被看到**；只对代理分最高的 `eval_cap` 个算真实 DPS
  （真实评估 2.4 ms 是瓶颈，代理**只用于排序**）
- **accept**：**LAHC**（Late Acceptance Hill Climbing, Burke & Bykov 2017）——
  维护 `hist_len` 长度的历史队列，新解不差于 `hist[i]` 就接受。
  **无温度参数、无降温调度** ⇒ 不会重蹈退火的覆辙。

实测（同起点 `Sam_best_raven2.json` = 94,474，同种子）：

| 配置 | 时间 | 真实评估 | DPS | 提升 |
|---|---|---|---|---|
| 退火 SA 100 轮 | 0.2 s | **65** | 101,406 | +7.34% |
| LNS 10 轮 | 1.2 s | 499 | 107,376 | +13.66% |
| LNS 100 轮 | 26.7 s | 10,597 | 117,805 | +24.70% |
| **LNS 600 轮** | **106.5 s** | 41,282 | **128,756** | **+36.29%** |
| 退火 420 s × 30 链（**旧生产默认**） | ~470 s | — | **94,474** | +178.4% vs 现状 |

完整链路（鸦人 `raven_nightblade`）：
`94,474` --LNS 600 轮--> **128,756** --合法词缀 +26.16%--> **162,436**
--属性重排 +3.10%--> **167,474**
对比旧链路「退火 420 s×30 链 + 词缀 + 属性重排」= 121,985 ⇒ **+37.3%**。

**CLI**
```bash
$PY -m gd auto Sam --extreme                    # 默认已是 LNS 400 轮（不再是退火 420 s）
$PY -m gd auto Sam --extreme --algo lns --anneal-iters 600 --chains 4
$PY -m gd auto Sam --algo anneal --anneal-seconds 420   # 走回旧路径复现历史结果
# 单链复现（含 --out 落盘）：probe28_lns.py <起点.json> lns 600 --k 2,3,4 --topk 18 --cap 240
```
⚠ `--anneal-seconds` 在 `--algo lns` 下是**墙钟预算**（机器负载会影响轮数 ⇒ 不可复现）；
要**可复现**用 `--anneal-iters N`（随机流由 `--seed` 定死）。
⚠ `--lns-cap` 是主要成本项：每轮实算 DPS 的组合数上限。调大更准更慢。

---

### 3.17 ★★ LNS 的第二轮加速：**邻域并行 + 三处缓存**（2026-09-20 晚）

用户口径：「lns 不能继续优化吗？有没有可以让 GPU 参与计算的方案｜GPU 天然擅长大数据
处理和模型运行」。

**结论先说**：这一轮的加速**全部落在 CPU 侧**，GPU **不划算**（理由与量化见 §3.18）。
实测两条腿：

#### ① 邻域并行（`tune_dps.ParEval`）—— 主收益

LNS 每轮的 `eval_cap` 个候选**互相独立** ⇒ 天然可并行。实测（480 个候选）：

| 进程数 | 每次评估 | 相对串行 |
|---|---|---|
| 串行 | 5.198 ms | 1.0× |
| 8 | 0.375 ms | **13.9×** |
| **16** | **0.274 ms** | **19.0×** ← 最优 |
| 24 | 3.254 ms | 1.6× ← **超订劣化** |

★ **不是进程越多越好**：16 物理核 / 32 线程，24 进程时超线程 + 内存带宽双重饱和，
反而比 8 进程慢 8 倍。默认 `procs=16`。

★ **池要建一次、全程复用**（`ParEval.warm()`）：建池 + 让每个 worker 各加载一次 DB
约 **2.2 s** 固定开销。把池创建放在计时内会得出「并行没用」的**错误结论**
（第一次实测就踩了这个坑：24 进程显示 0.7×）。

★ **worker 用 `(char, arch)` 重建评估**：`real_dps` 是**闭包**（含 memo），跨进程不可 pickle；
`plan_dps.dps_of(char, sol, arch)` 是函数式入口 ⇒ worker 里重建**逐位相同**的口径。

★ **并行路径必须先吃调用方 memo**：进程池看不到主进程的 memo，不去重会把同一解反复送算
（实测 600 轮里 36,045 次评估被重复成 60,478 次，**浪费 40% 墙钟**）。
`make_real_dps` 把 memo 挂在函数上（`f.memo`），`lns_search` 靠 `getattr(real_dps,'memo')` 复用。

★ **与链级并行互斥**：`--procs N` 会把 `--chains` 压成 1。链级并行是 N 个进程、
邻域并行是 procs 个进程，叠加就是 N×procs 个进程 ⇒ 必爆。

端到端实测（600 轮，起点 94,474）：**串行 61.8 s → 并行 8.0 s（LNS 本体 7.7×，
含建池 6.0×），结果逐位一致 ✓**。

#### ② 三处缓存 —— 单次评估 **2.440 → 1.633 ms（1.49×）**

| 缓存 | 位置 | 收益 | 为什么安全 |
|---|---|---|---|
| `save_dir()` | `gd/paths.py` | **7.4%** | 每次评估都在 `glob` 扫 Steam 目录；键 = `GD_SAVE` 值（`save_dir_override` 靠它），**只缓存成功结果** |
| `Db.fields()` | `gd/dbr.py` | **19%** | 每评估调 **236 次**；审计过 69 个调用点**无一修改返回值** ⇒ 可共享同一 dict |
| `fold(parts)` | `gd/dps.py` | ~10% | 纯函数（同槽同部件⇒同结果）；LNS 每轮只换 k 槽、其余槽**逐轮重复折叠**；返回**浅拷贝**⇒调用方可随意改 |

#### ③ 「加轮数」是无效方向（重要负面结论）

同一起点、同一种子：**600 轮与 4000 轮结果完全相同（122,337）** ⇒ LNS 早已收敛。
4000 轮只是把墙钟从 61.8 s 拉到 234 s（串行）。
⇒ **真正的杠杆是「扩大邻域」（k）与「邻域构造」，不是轮数**。

---

### 3.18 ★★ 为什么这一轮**没有**用 GPU（量化判据）

用户直觉是「GPU 擅长大数据处理与模型运行」。实测后**不成立**，四条硬理由：

1. **数据量差 3 个数量级**。一个解 = 14 槽 × ~5 部件 = 70 个记录，折叠后 ~2000 维属性、
   **~700 个非零**。GPU 的 SpMV/SpMM 要 `nnz ≥ 10⁵` 才摊得开 kernel launch（~10 μs）
   与访存延迟 ⇒ 单解级别**差 ~143 倍**。
2. **瓶颈根本不是数学运算**。优化后 profile：`final_report`（技能伤害的"算术"）
   `tottime` 只占 **11%**；其余是 `load_char` 里的 **字典/字符串/分支**
   （`dict.get` 4,384 次/评估、`dict.setdefault` 4,524 次、`re.match` 895 次）。
   GPU 对「数据相关的控制流」是最差的场景（分支发散）。
3. **批量被满抗约束卡死**。实测满抗可行率仅 **0.4%**（k=3/topk=20：8000 组合只剩 31 个）。
   每轮可评估候选只有几十~几百个，摊不开 GPU 的固定开销。
4. **口径风险**。把伤害链路向量化必然引入近似 ⇒ 触碰本项目的核心资产
   「面板逐位可复现」。而 CPU 16 进程已拿到 **19×（评估）/ 7.7×（LNS）**，成本是半天。

**GPU 唯一真正有价值的场景 —— 把邻域放大到 CPU 吃不住时**：

| k | topk | 组合数 | 平均满抗可行 | P(可行 > eval_cap 240) |
|---|---|---|---|---|
| 3 | 18 | 5,832 | 206 | 29% |
| 4 | 18 | 104,976 | 2,472 | 62% |
| **5** | **18** | **1,889,568** | **30,225** | **87%** |
| 5 | 30 | 24,300,000 | 248,133 | 99% |

⇒ 默认参数（k≤4）下代理**只影响 29~62% 的轮次**；一旦 k=5，每轮 3 万个可行解 =
eval_cap 的 **126 倍**，**「从 10⁶ 个组合里快速筛出真正好的 240 个」就成了新瓶颈** ——
这正是 GPU（或更好的代理模型）该上的位置。届时需要：
`fold` 重构成「候选×属性」稀疏矩阵（可行：`needs_scale` 只依赖**单记录**字段 ⇒
每条记录贡献可独立预计算）→ GPU 批量 SpMM 折叠 + 打分 → 只对 top-N 用 CPU 精算。
⚠ 另需处理 `lns_search` 里 `total > 4_000_000: continue` 的**安全阀**（k=5/topk=30 会被直接跳过）
⇒ 大邻域要改**分块枚举**（k=5/topk=18 的 `rac` 已是 151 MB/k=5/topk=30 达 1.9 GB）。

---

### 3.19 ★★ 多形态并发普查（`tools/sweep_arch.py`）—— 比「串行 + 大池」快 **2.7×**（2026-09-20 晚）

用户口径：「六个形态不能 6 个并发跑吗？」

**能，而且应该 —— 但要先把两道闸拆掉。**

#### ① 拦路虎：`autobuild` 原本是**全局单实例锁**

真跑 6 并发时第 6 个形态（`raven_nightblade`）直接 `rc=4`：
「✗ 已有一个 autobuild 在跑，拒绝启动第二个」。前 5 个已跑完，只有它被拒。

那把锁的历史动因（2026-09-20 早）是「两个 autobuild 同时跑 ⇒ 一次 53 个
python.exe，且**两个进程往同一个 `--out` JSON 写**」。重看它列的三条危害：

| 危害 | 触发条件 | 该由谁管 |
|---|---|---|
| ① 往同一 `--out` 写、结果不可信 | **仅同输出** | 锁按输出取键即可 |
| ② 50+ 个 worker 吃光内存/句柄 | 任何多实例 | **并发度预算**（调用方） |
| ③ 日志交错、DPS 分不清出处 | **仅同输出** | 同 ① |

⇒ 改成按 **(角色, 输出文件)** 取键（`autobuild._lock_key` / `_lock_path`）：
同输出仍互斥，**不同输出允许并发**。锁文件落在
`data/scratch/.autobuild.locks/<md5前12>.lock`，每个自带 PID，死进程自动回收。
`--force` 的语义也随之收窄为「抢占**同一个输出**」。

#### ② 内存**不是**约束（实测）

`ParEval` 单 worker（含离线库加载）恒为 **143 MB**，procs=1/4/8/16 完全线性：

| procs | 1 | 4 | 8 | 16 |
|---|---|---|---|---|
| 树内合计 RSS | 294 MB | 723 MB | 1,294 MB | 2,437 MB |
| 均摊 / worker | 142.8 | 142.9 | 142.9 | 142.9 MB |

⇒ 6×5 = 30 个 worker 也只有 ~3.6 GB（64 GB 机器上无关痛痒）。
**真正的约束是核**：16 物理 / 32 逻辑（这正是 `procs=16` 最优、24 已劣化的原因，见 §3.17）。

#### ③ 并发布局 vs「串行 + 大池」：拐点在 **24 进程 → 2.7×**

6 个形态 × 400 轮 LNS，同批形态、同口径（`GD_PROJ_HITS=1`、星座保留）：

| 布局 | 每实例 procs | **总进程** | 总墙钟 | 相对串行 | 各形态耗时之和 |
|---|---|---|---|---|---|
| 串行（原地做法） | 16 | 16 | 244.6 s | 1.00× | 244.6 s |
| 并发 | 2 | 16 | 154.1 s | 1.59× | 628.5 s |
| 并发 | 3 | 18 | 108.1 s | 2.26× | 482.0 s |
| **并发** | **4** | **24** | **90.0 s** | **2.72×** | 385.5 s |
| 并发 | 5 | 30 | 98.1 s | 2.49× | 422.4 s |

★ **不是越多越好**：24 → 30 反而变慢（90.0 → 98.1 s），拐点在物理核 16 与逻辑核
32 之间的 **24**；`sweep_arch.py` 的默认 `--budget` 就取 24。

**机制**：单个实例的 LNS 端到端并行效率只有 **48%**（16 进程 7.7×，
因为串行段 = 邻域构造 / LAHC 记账 / numpy 修复 / `eval_cap` 截断）。
串行布局下这 6 份串行段**首尾相接、白白叠墙钟**；并发布局下它们**互相重叠**。
⇒ **总进程数可以超过单池最优值**，因为并发的是**独立进程的串行段**，
不是同一个池在抢核 —— 这与 §3.17「同一池 procs 超 16 就劣化」并不矛盾。

★ **结果逐位一致**：并发（每实例仅 4~5 进程）与串行（16 进程）的 DPS 完全相同
（104,900 / 83,139 / 81,007 / 79,529 / 77,114 / 68,149）⇒ 并发只改墙钟，**不改答案**。

#### 用法

```bash
PY=/c/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe

# 全部形态（读 data/archetypes.json），总预算 24 进程（默认 = 拐点）
$PY tools/sweep_arch.py Sam --all

# 指定形态 + 自定义 LNS 规模 + 总预算
$PY tools/sweep_arch.py Sam --archs fangs,raven_nightblade --budget 30 --iters 1200 \
        --lns-k 2,3,4 --lns-cap 240
```

`--budget` 是**总**进程预算，脚本自动 `per = budget // len(archs)`（下限 1）。
产出：`data/plans/arch_<形态>.json` ｜ `data/scratch/sweep_<形态>.log` ｜
`data/plans/_sweep.json`（含墙钟与 DPS 汇总）。

★ 它绕开了 `--jobs`/`--chains`（那两个是**单形态内**的并行），
也绕开了 `--procs`（单池并行）—— 三者是**三个不同层次**，别叠加。

---

### 3.16 ★★ 报告的「伤害循环」章节（2026-09-20）

用户口径：「伤害循环这个条目要写在最终报告里 一般是普通攻击 叠加很多普攻触发的效果
如猛袭就会触发雪崩 和一些装备上的技能哪些 调研一下」。

**官方机制**（`gd/rotation.py` 早已建模，但报告里一直没露出来）
- 一个角色只有**一个左键默认攻击**（default attack）；`attack*` 族里所有
  「无冷却、无 `skillChanceWeight`」的技能都在争这一个槽，**归属是玩家选择**，
  形态可用 `primary_attack` 显式声明（否则退回 `max(avg)`）。
- **WPS（武器池技能）**由 `skillChanceWeight` 驱动。官方口径（Zantai，
  Grim Misadventure #54）：**「这些数字是权重，不是百分比」** ——
  WPS 先按权重从默认攻击的 100 里扣，剩下的才是默认攻击权重；
  **总权重 W > 100 ⇒ 默认攻击权重归零、普攻彻底不再出现**，此时分母改成 W。
- ★ WPS **只在「默认攻击带武器伤害」时才触发**（`weaponDamagePct > 0`）。
  鸦人形态的默认攻击「寒冰之爪」是 `attack_wave`、`weaponDamagePct = 0`
  ⇒ `wps_blocked = True`，夜刃整套武器池**一次都不触发**（即使加了点）。

**输出位置**（★ 走代码，不手工贴 —— 手工段一重生成就丢且无报错）
- `plan_dps.dps_of` 返回 `rotation`（默认/权重/分母/`wps_blocked`）与
  `loop`（逐技能中文名 + 权重/冷却/频率/DPS）
- `plan_audit._loop_rows` / `_loop_text` / `_loop_md` 渲染
- `planreport --audit` 自动并入报告的 `## ★ 伤害循环（官方武器池权重口径）` 段

**实测对照（同角色同等级，两种形态）**

| | 鸦人 `raven_nightblade`（本 BD） | 人形态 `avalanche`（用户举的例子） |
|---|---|---|
| 默认攻击 | 寒冰之爪（`attack_wave`，**无武器伤害**） | **猛袭** `onslaught1`（带武器伤害） |
| 武器池总权重 W | **0**（全阻断） | **70** |
| 默认攻击权重 | 100（每下都出现） | 30 |
| WPS | ⚠ **一次都不触发** | **雪崩** `wpattack02` 权重 26 → 占比 26%；夜刃 wpattack1/2 各 22 |
| 冷却技能 | 霜暴 | 跃击、幻影刃 |
| DoT 占比 | 13.4% | **55.0%** |
| 实战 DPS（含减抗） | **162,436** | 29,059 |

⇒ 「猛袭触发雪崩」是**人形态**的机制；鸦人形态**没有这条普攻触发链**，
但它的绝对 DPS 高 **5.6 倍**。

**★★ WPS 可能是「负收益」—— `fangs` 实测（2026-09-20）**

默认攻击「刺击」单击 **26,040~30,124**，而狂战士两条 WPS（`wpattack01` 雪崩 /
`wpattack02` 血牙）满级只有 **12,149~15,416**（约一半）。WPS 触发是**替换**一次
默认攻击 ⇒ 每次触发等于**用弱击换强击**。手工各投 10 级（149→169/202，仍在预算内）实测：

| 配置 | W | 循环结构 | 实战（含减抗） |
|---|---|---|---|
| 不带 WPS | 0 | 刺击 100% + 刀锋之怒 | **114,782** |
| 带 WPS 10+10 | 52 | 刺击 48% / 雪崩 26% / 血牙 26% + 刀锋之怒 | 105,443（**−8.1%**） |

⇒ **`W = 0` 不等于「漏项」**：当默认攻击单击远强于 WPS 时，
单目标极限配装**故意不该带 WPS**（要清怪 AoE 再按需加，那是另一个目标函数）。
报告里必须讲清是哪一种，否则会被读成缺陷。

**★★ 缺陷：`gd/alloc.py::_FORM_LOCKED` 把 WPS 一起误杀（**尚未修**）**

`_FORM_LOCKED` 含 `'attack'`，而 `rest` 池按 `kind` **整类剔除** ⇒
变身形态里所有 `kind='attack'` 的技能都被排除。但 WPS 的 `kind` **也是** `'attack'`
（判 WPS 看的是 `template` 里的 `Skill_WPAttack_*` / `Skill_WeaponPool_*`）。
WPS 是**被动触发、不占技能栏**，与「变形后技能栏被整体替换」是两回事。
后果：**6 个形态全是变身形态 ⇒ 所有形态的 WPS 候选从未进过加点池**，
且单专精形态可投技能不足 ⇒ `fangs` 闲置 **53 / 202** 点。

⚠ **但别急着放开**：`allocate()` 是**纯优先级贪心**（`for rec in core + rest`，
不看分数），WPS 优先级是 3（高于普通攻击的 4）⇒ 放开后会被**无条件照点**，
`fangs` 反而掉 8.1%。要改必须**同时**让 WPS 决策看分数
（或在 `tools/make_alloc.py` 层出「带 / 不带 WPS」两版再评估）。
当前逃生口：`GD_ALLOC="雪崩:10,血牙:10"` 手工指定。

**模型边界（报告里要如实说）**
- 已覆盖：默认攻击 + WPS（权重制）+ 冷却技能 + DoT 通道 + 逐通道覆盖率
- **未覆盖**：装备自带的**触发技能**（`gd/dps.py` 明确写「本报告的单次伤害里不含它们」）、
  星座**绑定的主动技能**（星座只进了被动加成 `devotion_pct`）
- ★★ **星座没有注入通道**（不存在 `GD_DEVOTION` 之类的开关，2026-09-20 确认）⇒
  **所有配装搜索的 DPS 都是在「存档现存星座」下算的**，星座重排从未参与优化。
  实测量级：Sam 存档 **32 / 55** 点、进攻加成 **565%**；`select_coherent` 建议
  51 / 55 点、**1429%**（2.5×）。⇒ 这是**最大的一块未压缩余量**。
  注意 55 是**游戏上限**（需开满神龛），不是玩家当前手上的点数；
  `planreport` §6.2 用的是 `devotion_budget(level)`，会高于存档实际点数 —— 读报告时要分清。

---


### 3.20 ★ 打包分发：`tools/pack_skill.py`（2026-09-20）

把本 skill 打成可移植 zip，供**其他 agent / 其他机器**测试。

```bash
$PY tools/pack_skill.py                       # 出 full + slim 到 E:\xz\Archives
$PY tools/pack_skill.py --verify              # 打包后自动解压到临时目录跑 selftest + dps 基准
$PY tools/pack_skill.py --only slim --out D:/tmp
```

- **排除项**（可再生，不入包）：`__pycache__` / `*.pyc`、
  `data/cache/*.pkl`（`load_json` 的 `(mtime,size)` 缓存）、`data/cache/prune/`、
  `data/scratch/`、`*.tmp`。slim 额外排除 `data/cache/*.js`。
- **为什么默认出 full**：`cache/*.js` 压缩比极高（38 MB → ~5.6 MB），
  保留它换来「目标机不装 Grim Tools 也能重建」；slim 仅省 ~5.6 MB。
- **zero-drift 校验口径**：解压后 `tools/selftest.py` 应 **379 全绿**，
  `python -m gd dps Sam` 应 **合计 101976 ｜ 实战 152476**。
- zip 内顶层目录固定为 `grim-dawn/`，解压即可直接放进 `~/.workbuddy/skills/`。
- `README_PORTING.md`（skill 根）随包分发，说明前提、环境变量、自愈机制与已知边界。

⚠ 打包**不排除** `data/regress/`（golden 基线）与 `data/plans/final/`（交付报告）——
它们是校验与交付物的一部分。

---

### 3.21 ★★ 一键换档：`tools/gd_restore.py`（2026-09-20）

手动换档有两处不便：**备份目录名认不过来**（68 份 `GrimDawn_存档备份_2026-09-20_203941`），
以及 `gd backup restore` 是**整包覆盖** —— 存档里有 5 个角色（`Sam` / `xyf` / `1234` / `236` /
`Cx666`），整包恢复会把**别的角色一起退回旧进度**。本工具就是补这两点。

```bash
$PY tools/gd_restore.py                  # 交互：列表 -> 回车 = 最新，或输序号
$PY tools/gd_restore.py -l               # 只列不恢复
$PY tools/gd_restore.py -y               # 一键恢复最新（只恢复 Sam 的角色档）
$PY tools/gd_restore.py -n 3 -y          # 恢复列表里的第 3 份
$PY tools/gd_restore.py --dir "<备份目录>" -y
$PY tools/gd_restore.py -y --all         # 整包（含其他角色 / 共享仓库 / 设置）
$PY tools/gd_restore.py -y --char xyf    # 换另一个角色的档
$PY tools/gd_restore.py -y --dry-run     # 预演（永不写盘）
$PY tools/gd_restore.py --verify -n 3 -y # 恢复前用 MANIFEST 逐文件校验
$PY tools/gd_restore.py -y --mirror      # 额外清掉目标角色目录里「备份中没有」的旧文件
```

双击用 **`tools/gd_restore.cmd`**（自动找托管 venv 的 python，跑完 `pause` 不闪退）。

**列表长这样**（一眼认出「落档前 / 落档后」，`[= 当前]` = 内容与现状相同）：

```
备份目录 E:\xz\Archives ｜ 共 68 份
当前存档 Sam  lv73  138.0/546.0/130.0  未分配 2  指纹 c58437cd9006

  #   时间        等级  体格/狡诈/精神    未分 文件 指纹         备注
  1   09-20 20:39 lv71  50.0/618.0/146.0  0    67   569d5ba43ed6 _Sam 自动配装（满抗 + 追高伤害）
```

**三道门**（都在写盘之前）：

| 门 | 判据 | 越过 |
|---|---|---|
| 游戏在跑 | `tasklist` 里出现 `Grim Dawn` ⇒ 拒绝（游戏退出会把内存态写回盘，白恢复） | `--force` |
| 备份完好 | `--verify` 逐文件 md5 比对 MANIFEST | `--force` |
| 归档目录位置 | 归档目录**不得**落在活存档根内（否则备份指数膨胀，见 `docs/pitfalls.md` #51） | 不可越过 |
| `--live` 有效性 | 指向的目录**必须真有 `main/`** —— 否则 `save_dir()` 会**静默回落到真实存档** | 不可越过 |

**恢复前**一定自动 `do_backup`（笔记写「换档前自动备份（恢复 <时间戳>）」）⇒ 永远可回退，
脚本会把回退命令直接打出来。**默认只覆盖、不删除**；要删旧文件显式加 `--mirror`。

⚠ **Steam 云同步**：存档正本在 `.../219990/remote/save`（就是云同步目录）。
恢复后进游戏前，让 Steam 先同步完（或离线启动），否则云端旧档可能被推回来。

---

### 3.22 ★★ 星座 / 技能点优化（`tools/tune_devotion.py` · `tools/tune_skills.py`）（2026-09-20）

在此之前，**星座重排从未参与过任何优化**（报告里只有「加成 % 加和」的近似建议）。
本轮把「星座 / 技能点」也接进**真实 DPS 模型**：

```bash
# —— 星座（真实 DPS，三条路）
$PY tools/tune_devotion.py               # 从存档现状出发贪心加点（只加不拆）
$PY tools/tune_devotion.py --rank        # 单星座增量排行榜（含「需亲和力」）
$PY tools/tune_devotion.py --marginal    # ★ 现有星座的**拆点损失**（找可退的）
$PY tools/tune_devotion.py --realloc .25 # 退掉损失 <0.25% 的星座再贪心补回

# —— 技能点（三种模式）
$PY tools/tune_skills.py --mode add       # 只用未分配的点（最保守）
$PY tools/tune_skills.py --mode marginal  # ★ 边际值表：每个技能「拆 1 点损失 / 加 1 点收益」
$PY tools/tune_skills.py --mode swap --rounds 20   # ★ 1-1 换位，迭代到收敛

# —— 终评 / 交付物
$PY tools/eval_build_variants.py         # 同 harness 下各优化轴的收益（技能 × 星座 × 属性）
# ⚠⚠ **必须显式传 `SKILL_SRC=` 与 `DEV_SRC=`**：不传会**静默回退**到
#     `data/scratch/skill_swap.json` / `devotion_opt.json`（上次跑留下的旧文件）
#     ⇒ 输出一整片**负增益的假结论**（陷阱 #75-C）。
$PY tools/rerun_final_docs.py            # 重算 data/plans/final/ 全部交付物
# 环境变量：ARCH / CHAR / PLAN / BUDGET（星座池）/ UNSPENT（技能未分配数）
```

★ **`tune_devotion --realloc` 的输出契约（2026-09-21 加）**：**落盘结果永不劣于基线**。
`ev()` 不单调（加星点可能挤占 proc 池而掉 DPS），退款又是「单颗独立判损失」⇒
旧实现会写盘更差的方案（实测 133,396 → 128,139）。现在全程记 **best-seen** 并于收尾与基线取优，
没超过基线就写**基线原样** + `"adopted": false`（陷阱 **#69-A**）。

★★ **星座点数与搜索粒度（2026-09-21 更正，陷阱 #70）**：

- **池 = 已点亮节点数 + 存档「未分配」**。存档 `total_devotion_points` 是**已花**、不是池
  （旧代码两处都读错）。Sam 实测：节点 **36** ｜ 已花 36 ｜ 未分配 **4** ⇒ **池 40**。
- **proc / Celestial Power 节点也要花 1 点**（官方指南：亮星本身就是要花点买的星）。
  ⇒ 旧口径「已投 34」实为 **36**，且带 proc 的星座成本恒少算 1 点。
- 旧的 `len(g['stars']) > 4 → continue` **把全部 Tier-3 星座排除在搜索之外**（T3 都是 6~7 节点）。
- 现在支持**单星粒度**：游戏里不能跳着点星 ⇒ 合法买法 = **含根的连通子集**
  （`star_graph()` + `rooted_subsets()`，星位图取自 `data/cache/calc.js` 的
  `devotionButton<序号>`/`devotionLinks<序号>`，**不能用 `devotion_tree.json` 的 `stars` 配对 —— 会错位**）。
- ⚠⚠ **proc 记录不喂进模型**：模型没有触发率/指派门控，把攻击型 Celestial Power 当常驻攻击技
  （实测「雷霆暴怒」+44.6%、「激流漩涡」+18.7% = 纯伪影）⇒ proc 节点**算成本、不给收益**。

★★ **注入加点时注意（陷阱 #69-B）**：`GD_SKILL_JSON`（= `--alloc` / `SKILL_SRC` / `DEV_SRC`）
**只替换技能/精通，星座是与存档求「并集」**（不是替换）⇒ 注入文件里带星座会被**静默叠加**。
注入文件必须**只含技能/精通**（如 `data/plans/pivot/alloc_legal.json`）；带星座时 `gd/dps.py`
会打 `⚠ GD_SKILL_JSON 含 N 个「存档没有的星座」记录`。

### 三条只能靠实测得到的口径（2026-09-20）

**(1) 为什么不能信 `devotion.select_coherent()`**：它按「各伤害类型加成 % 的加权和」
打分，与真实伤害**不同向**。Sam lv73 实测：

| 方案 | 加成 % 加和 | **真实 DPS（含减抗）** |
|---|---|---|
| 现有 32 星点 | 565% | **155,394** |
| `select_coherent` 建议（33 点） | **1429%** | 139,701（**−10.1%**） |
| 现有 +「狐狸」4 星 | 655% | **163,004（+4.9%）** |

**(2) 为什么 `tune_skills` 要排除 `/itemskills`**：`skillChanceWeight` 是**权重不是百分比**，
WPS 从默认攻击的 100 里扣 ⇒ **投多了会把主输出挤没**。实测自动生成的加点
（把 24 点投进夜刃武器池 `wpattack1/2/3`）让 ΣW 从 45 涨到 **111**
⇒ 默认攻击权重 **0** ⇒ 野性利爪 0 下/秒 ⇒ **DPS 掉一半**。
**判据**：看 `rep['rotation']['default_weight']`，**等于 0 就是主输出没了**。

**(3) ★★ 边际值 0 的项先查字段，再决定拆不拆**（`docs/pitfalls.md` #56）。
边际表是「不花钱」的最优答案，但**「拆点」建议必须过字段关**：

| 项 | 模型边际 | 真实字段 | 判读 |
|---|---|---|---|
| 技能 `血莽`（12 级） | **0** | 只降**敌方 DA**（30→110） | 模型不算敌 DA ⇒ **盲区，别拆** |
| 星座 `乌龟`/`海鳗`/`铁锤`/`抉择之地` | **≈0** | 护甲 / DA / 三围 | 防御向 ⇒ 盲区，退了是**用防御换伤害** |

⇒ 报告里要把这类写成「**用防御换伤害**」的主动取舍，不能写成「白投的点」。

> ⚠ **跨 harness 的数字不能混比**：`gd dps` / `plan_dps` 把星座混在「技能 %」里算
> （漏平伤/攻速/独立倍率，少 1.9%）；上面这些工具走**显式星座通道**
> （`levels` 摘掉 `/devotion/` + 传 `devotion_levels=`）。报告里必须标明用的是哪套。

> ⚠ **B 口径（`GD_SKILL_JSON` 注入加点）已退役**（2026-09-20）：见 `docs/pitfalls.md` #53。
> 恢复方式写在 `tools/rerun_final_docs.py::JOBS` 的注释里。

### 3.23 ★★ 单一主轴形态（「主穿刺」）—— `GD_OBJ` 四处一致才成立（2026-09-20）

想做**单一主轴**（如「主穿刺」）时，**只改形态的 `damage_weights` 是不够的**：

| 阶段 | 用哪个目标 | 怎么设 |
|---|---|---|
| 满抗求解 | `archetypes.json` 的 `damage_weights` | 新形态里把 `pierce` 拉高、`bleed` 压到 ≈0 |
| **局部 / LNS 搜索** | `tools/autobuild.py::make_real_dps` | **`GD_OBJ=pierce`** ⚠ 不设 = 按**合计**优化 |
| 星座调优 | `tools/tune_devotion.py` | `GD_OBJ=pierce`（`--rank` / `--marginal`） |
| 技能调优 | `tools/tune_skills.py` | `GD_OBJ=pierce` 或 `--objective pierce` |
| 终评 | `tools/eval_build_variants.py` | `GD_OBJ=pierce`（自动吃 `*_pierce.json` 方案） |

目标定义在 **`tools/objfunc.py`**（`total` = 旧行为、`pierce` = 穿刺桶含减抗 DPS、
`pierce+` = 穿刺桶 + 20% 其他桶）。**换目标后结论会明显不同**：

| 项 | 合计目标 | 穿刺目标 |
|---|---|---|
| 收益最高的加点 | 战争兵器（流血 +36%）= **+1,250** | 双刃（穿刺被动）= **+1,232**，战争兵器掉到 **+271** |
| 收益最高的星座 | **狐狸**（流血 +90%）= +4.9% | **螳螂** = +6.6%，狐狸掉到 +4.2%（第 5） |

**三条硬约束**：
1. **四处目标必须一致**，否则出现「装备按穿刺选、技能按合计选」的四不像（#60）。
2. **优化器与搜索必须同源**：调 `final_report` 时**必须传 `rr=` / `enemy_armor=`**，
   否则与 `plan_dps.dps_of` 差 **47%**（#58）。
3. **收益（Δ）不能跨通道比**：显式星座通道 vs 管线通道的差值**不是常数**（同装备 4.7%、换装后 15%）。
   ⇒ 报告里写明用的是哪条通道。

复跑示例（主穿刺）：

```bash
PY=".../envs/default/Scripts/python.exe"; A=wolf_nightblade_pierce
GD_ARCHETYPE=$A GD_OBJ=pierce $PY -u -m gd auto Sam --extreme --procs 24     --archetype $A --anneal-iters 800 --lns-k 2,3,4,5,6,7 --out data/plans/lv73_pierce/arch_$A.json
GD_OBJ=pierce PLAN=data/plans/lv73_pierce/arch_$A.json $PY tools/tune_devotion.py --rank
GD_OBJ=pierce PLAN=data/plans/lv73_pierce/arch_$A.json $PY tools/tune_skills.py --mode swap --rounds 12
GD_OBJ=pierce PLAN=data/plans/lv73_pierce/arch_$A.json $PY tools/eval_build_variants.py
```

⚠ **`--goal` 不要乱传**：默认是 `super`（满抗 + 真实 DPS 微调）；
`fullres` = **只求满抗、完全不优化伤害**（误传会 5 秒 / 1 次评估就「完成」，见 #59）。

---

### 3.24 ★★ 装备「授予技能」的建模口径（2026-09-21 **已修**，陷阱 #71 / #72）

装备（组件 / 词缀 / 套装 / 专精无关）会在提示框里给一块「**授予技能**」。模型必须**按模板分三类**：

| 模板（`data/item_skills.json[sk]['l']`） | 类别 | 处理 |
|---|---|---|
| `Skill_WPAttack*` | WPS | 由 `item_wps` 入池（有武器闸门） |
| `Skill_BuffSelfToggled` / `Skill_BuffRadiusToggled` | **常驻开关光环** | ★ **要收**（进技能表 ⇒ 属性全程生效） |
| `Skill_Passive*` / `Skill_Give*` | **常驻被动 / 属性加成** | ★ **要收** |
| `Skill_BuffSelf*` / `Skill_BuffRadius*`（**无**控制器） | 常驻 buff | ★ 要收 |
| 带 `itemSkillAutoController`（`ctXX`）/ `Skill_Attack*` | **触发型** | ⛔ **不收**（触发率/指派没建模） |

- 收的时候：**同一 `sk` 组只取「数值字段最多」的那条记录**（本体 + `granted/` 子技能会重复），
  等级取 `itemSkillLevelEq`，写进 `c['skills']`。
- ★ 这些记录**不在 `db.skills`（4268 条）里**，但 `db.fields()` 能解析 ⇒ 进技能表即可生效。
- 实测（Sam / `x_total_fix2`）：**219,964 → 234,122（+6.44%）**，计入 2 条
  （`comp_bladeaura_02` = 恶毒尖刺的 +75% 穿刺 / `item_gunslinger`）。
- ⚠ **`characterManaLimitReserve` 没建模**（两把恶毒尖刺 = 300 预留能量上限）。
- ⚠ **套装表路径**：`gd/skillmod.py` 曾指向已不存在的 `gt_data/` ⇒ `load_sets()` /
  `load_item_skills()` **恒 0 条**（套装加成整套静默丢失）⇒ 已改走 `gd.paths.load_json`。

---

### 3.25 ★★ 防御减伤模型（`gd/defense.py` · `tools/defense_audit.py`）（2026-09-21 新增）

**这是「我方挨打」的镜像模块** —— `gd/combat.py` 管攻击侧（我方伤害 → 敌方护甲/抗性），
本节管防御侧（敌方打击 → 我方减伤链）。两者**共用**同一批官方公式（复用 `combat`，不写第二套）。

```bash
$PY tools/defense_audit.py Sam                                   # 面板 + 典型档 + 边际
$PY tools/defense_audit.py Sam --dmg 8000 --dtype physical --enemy m1281 --out data/defense_snapshot.md
```

**九层**（官方 Order of Defense）：① 躲避 ② 敌方命中/暴击 ③ 格挡 ④ 种族% ⑤ 抗性 ⑥ 护甲 ⑦ 种族点数 ⑧ %吸收 ⑨ 点数吸收。

**口径（真源）**：

| 项 | 真源 | 要点 |
|---|---|---|
| 抗性组成 + 上限 | `data/cache/calc.js::f63k` | `defensive<X>` **+ 火冰雷再加 `defensiveElementalResistance`** + CC 再加 `defensiveCrowdControl`；上限 = `engine.playerDefenseCap(80) + MaxResist` |
| 难度惩罚 | `gd/opt.py::NEED/PEN` | 终极 **上排 −50 / 下排 −25** ⇒ 封顶需 **raw ≥ 130 / 105**（与满抗约束求解**同源**） |
| 逐部位护甲 | `data/cache/calc.js::f62k` | `(本槽 prot+bonusProt + **腰带项** + 全局项) × (1+protModifier/100)`；**腰带护甲对 6 部位各计一次**；★ **吸收率的前提是该部位自身护甲 ≠ 0** |
| 盾牌 | `data/combatformulas.json` | `shieldDamageReductionEquationDGB/DLEB`（⚠ 格挡吸收率口径**待实测核准**） |

**⛔ 数据缺口（如实标注）**：**敌方打击的绝对量拿不到** —— `data/monster_stats.json` 只有怪的
OA/DA/抗性/护甲（`derived.has_armor=false`）。★ 但 **`monsterdb.js`（9.3 MB）里其实有**
（`offensivePhysical` 1782 / `attackSkill` 1152 / `skillName` 33726），只是**从未抽取**。
⇒ 本模块给的是**传递函数**（给定打击 → 剩余/EHP/边际），不是「模拟一场战斗」。

**接进优化器**（可选，**默认零漂移**）：`GD_DEF_WEIGHT`（未设 = 不启用）。

```bash
GD_DEF_WEIGHT=0.2 GD_OBJ=total $PY -m gd auto Sam --extreme …   # 防御分满 1.0 等效 +20% 伤害
```

链路：`tools/plan_dps.py` 输出 `def_score`（`gd/defense.py::quick_score`，**轻量**：抗性覆盖／护甲／
满抗缺口／吸收／生命五项加权）→ `tools/objfunc.py::_with_defense` 乘到评分上 ⇒
**装备搜索 / 星座重排 / 技能换位**三处一起把「抗打」纳入目标。

⚠ 三处必读：
1. **减益技能的 `defensive*` 是「目标的」抗性** ⇒ 必须按模板剔除（陷阱 **#77**）；
2. **条件性来源要单列**（如「不羁狂怒」`lifeMonitorPercent=50` ⇒ 残血才触发），不能并进常驻面板；
3. **第 ② 层可能让伤害变大**（敌方暴击，PTH ≥ 100 时期望 > 1），不是 bug。

---

## 伤害循环文档（`tools/plan_cycle.py`）★ 每个 BD 必带

用户口径（2026-09-20）：「伤害循环中的技能用游戏提示框那种格式展示；每一条的伤害
是哪里来的 —— 武器多少、套装多少、技能多少、星座多少、乘区怎么叠加；出单次伤害
与每秒伤害；以及该技能能不能触发其他技能（衣服上的 / 镶嵌物的）。**每次出新 db
必须带一份专门的伤害循环文档**。」

```bash
# ① 打 stdout
$PY tools/plan_cycle.py Sam data/plans/lv73_A/arch_werewolf.json     --arch werewolf --enemy m3955

# ② 落盘（与方案报告同目录，文件名约定 CYCLE_<形态>.md）
$PY tools/plan_cycle.py Sam data/plans/X.json --arch werewolf --enemy m3955     --out data/plans/final/CYCLE_werewolf.md

# ③ B 口径（注入加点）
$PY tools/plan_cycle.py Sam data/plans/X.json --arch wolf_nightblade     --alloc data/scratch/alloc71_wolf_nightblade.json --out ...

# ④ ★ 一键重生成 `data/plans/final/` 的**全部**交付物（模型改完必跑）
$PY tools/rerun_final_docs.py                 # 8 形态 CYCLE + REPORT + B 口径
$PY tools/rerun_final_docs.py --only werewolf # 只做一份
$PY tools/rerun_final_docs.py --check         # 只重算并 diff，不覆盖
```

> ★ **为什么必须有 ④**：产物是**落盘快照**，模型改了它不会自己变。实测踩过：
> 三份 `CYCLE_*` 里只有一份是修复前生成的 —— 「部分陈旧」最难发现。
> 详见 `docs/pitfalls.md`「改完文档渲染器必须重跑所有已落盘的产物」。
> 另一条同源要求：`CYCLE_*` 的渲染器（`gd/dmgcycle.py`）与 `gd/procs.py`
> **必须登记进 `tools/sync_live.py::PAIRS`**（现 22 对），否则从零重建会缺模块。

文档结构（**五段固定**）：

| 段 | 内容 |
|---|---|
| **0 循环总览** | 角色（默认攻击 / WPS / 冷却）· 权重占比 · 频率 · 单次 · 三层每秒 |
| **① 游戏格式明细** | 逐伤害类型 `物理 248 - 358` / DoT 单值（**每秒**）—— 与游戏提示框同口径（**不含敌方防御**） |
| **② 每条伤害从哪来** | 技能本体 / 武器 / 光环被动 / 装备 / 套装 / 星座（**加成前**基础值） |
| **③ 乘区叠加** | 逐类型的 % 加成**分层**（装备/技能/属性/星座/套装/星座节点）+ 独立乘区 + 官方叠加顺序 |
| **④ 单次与每秒** | 敌方基础抗性 → 减抗后 → 抗性倍数 → 护甲减免 → 综合倍数 → 单次（实战）→ 每秒三层 |
| **⑤ 可触发的其他技能** | 该技能能否触发每个装备/镶嵌物技能（含几率与期望次数/秒） |
| **2 全循环触发矩阵** | 物品技能 × 本循环技能 的 ✓/✗/– 矩阵 |
| **3 模型边界** | ★ 装备授予的 WPS（**已入池**，正面登记）/ 触发技能不进 DPS / `dualWieldOnly` 未判 / DoT 时长假设 / `projectile_hits` 场景假设 |

**与 `plan_audit.py` 的分工**：`plan_audit` = **体检**（数字对不对）；
`plan_cycle` = **档案**（数字**怎么来的**）。两者都吃 `plan_dps.dps_of()` ⇒ 不可能分叉。

**装备授予的武器池技能（WPS）—— 2026-09-20 已入池**

装备 / 镶嵌物 / 圣物授予的 `Skill_WPAttack_*` 与专精里的武器池技能**同属一个池**，
按 `skillChanceWeight` 从默认攻击的 100 里扣。这条链路以前**整块缺失**：

- `gd/procs.py::wps_of_item()` —— 记录路径经 `gd/skillprov.py::sk_to_records()` 翻译，
  权重取自离线库 **`itemSkills` 表**（`.dbr` 记录与 `skills.json` 里**都没有**该字段）。
- `gd/dps.py::load_char()` 新增 `base_gids`（当前配装的 GT id 列表）→ 注入的输入。
- `gd/rotation.py::final_report(item_wps=…)` → 注入 `_recs` + `levels` + 权重字段。
  已在 `gd/dps.py` / `tools/plan_dps.py` / `tools/gt_regress.py` / `gd/gen.py` 全部接线。
- **不传 `item_wps` ⇒ 零漂移**（自检 `[31]` 有护栏）。

⇒ 本文件 §0「循环总览」的 `W` 与默认攻击权重因此**不会再虚高**。
详见 `docs/pitfalls.md` 的「装备授予的 WPS 从未进循环」专题。

**归因怎么保证可信**：`gd/rotation.py::final_report` 对 `base100` 的每个来源
**各自**跑一遍同参数的转化链（`convert()` 是逐 Source 独立的，故可加），
`rows[].origins` 记五源基础值、`rows[].pct_parts` 记乘区分层。
自检 `[31]` 用恒等式守死：**五源之和 == `weapon` 列 + `skill` 列**、
**`pct_parts` 之和 == 该行 `pct`**（**56 行** 0 不一致；WPS 入池后行数由 43 增到 56）。

**触发判据**（`gd/procs.py`，全部来自离线库）：

| `triggerType` | 含义 | 谁能触发 |
|---|---|---|
| `AttackEnemy` | 攻击敌人（命中即 roll） | **任何造成伤害的技能**（DoT 后续跳不算） |
| `AttackEnemyCrit` | 攻击**暴击**时 | 只有暴击 ⇒ 暴击率 0% 时**永不触发** |
| `HitByEnemy` / `HitByMelee` | 被击中 / 被近战击中 | 挨打 —— **与用哪个技能无关** |
| `Block` | 格挡成功 | 需持盾 |
| `OnKill` | 击杀敌人 | 与技能无关 |
| `LowHealth` | 生命低于 `triggerParam`% | 与技能无关 |
| （无 `itemSkillAutoController`） | 常驻 | 装备上就生效，不需触发 |
| `Skill_WPAttack_*` | **武器池技能** | 按权重从默认攻击 100 里扣（不靠几率） |

---

## 四轴坐标上升（属性 / 技能 / 星座 / 装备）

```bash
$PY tools/coordinate_ascent.py Sam --dry-run                      # 只打印编排计划
$PY tools/coordinate_ascent.py _xyf --axes attr                    # ★ 单轴单独跑最优解（最快）
$PY tools/coordinate_ascent.py _xyf --axes attr,skill,dev          # 三轴（默认；装备最贵放最后）
$PY tools/coordinate_ascent.py _xyf --axes gear --gear-extreme     # ★ 装备轴用 --extreme --with-weapon
$PY tools/coordinate_ascent.py _xyf --plan data/plans/xyf_auto.json --arch soldier_nightblade
```

| 轴 | 最优性 | 通道 |
|---|---|---|
| 属性点 | **全局最优**（可行域 ~预算²/2 ⇒ 全枚举，Sam 2,701 组合 / 16 进程 5 s） | `plan_dps` |
| 技能点 | 局部最优（1-1 换位到收敛） | `GD_SKILL_JSON` 注入 |
| 星座 | 启发式（贪心 + 拆点重排，best-seen 兜底） | **显式星座通道**（与其余差 +3.7%，**不可相减**） |
| 装备 | 启发式（LNS 大邻域） | `plan_dps` |

⚠⚠ **给非 Sam 角色跑四轴时的三个必传项**（陷阱 #95）：
1. `--plan`：不传会退回 `data/plans/<char>_auto.json` → 再退 `<char>_current.json`。
   **绝不能让它留空** —— `tune_skills` / `tune_devotion` 的 `PLAN` 默认值是
   **写死的 `data/plans/Sam_lv73_current.json`**，会拿 Sam 的方案去算别人。
2. `--arch`：不传会按 `plan_dps.resolve_arch()` 判（变身技能 > 职业组合 > `werewolf`）。
   子轴的 `ARCH` 默认值同样写死成 `wolf_nightblade_fast`。
3. `--gear-extreme`：装备轴默认强度 = 普通 `gd auto`；双持/武器向形态要开，
   否则装备轴数字会低于主线那次，看着像「装备轴没用」。

产出 `data/plans/final/ASCENT_<角色>_<轴>.md`（**逐轴单独跑不会互相覆盖**）。

---

## 「一大堆 python 进程」是谁 —— `tools/proc_tree.py`

跑 `gd auto` 时任务管理器会出现 **20+ 个 python**，每个几十到 147 MB。它们不是泄漏，
是**并行搜索的进程树**（两层，见 `docs/pitfalls.md` #89/#90/#91）：

```bash
$PY tools/proc_tree.py 4      # 采样 4 秒，按角色打印 个数 / 核数 / 内存
```

实测（`gd auto _xyf --extreme --with-weapon`，默认 `--chains 4 --procs 4`）：

| 角色 | 个数 | 小计核 | 均内存 | 在干什么 |
|---|---|---|---|---|
| 父 `gd auto` | 1 | 0.00 | 382 MB | 读档 → 建池 → 满抗 ILP → 收链结果 → 出报告 |
| **链进程** | **4** | 0.26 | 359 MB | `ctx.Process(daemon=False)`，各自**独立跑完整一轮**搜索（贪心 + LNS），取最优 |
| **ParEval worker** | **16** | **14.19** | 160 MB | 链内**邻域评估并行**，每个 = 一个独立打分进程 |
| 启动壳（bash/沙箱包装） | 5 | 0.00 | 11 MB | 调用链包装，不干活 |
| **合计** | **26** | **14.44** | — | ≈ 16 物理核跑满 |

- 总进程 = **物理核数**（`autobuild._alloc_parallel`），`--chains 4 --procs 4` = 1+4+16。
- **内存不共享是 Windows 的固有代价**：spawn 不是 fork ⇒ 每进程各自重载离线库
  （实测 裸 python 15 MB → `import gd.opt` **111 MB** → 建完池 ~147 MB），21 × 147 MB ≈ 3 GB。
- ★ **「只有一个在跑」是另一段**：**建池**阶段只有父进程 1 个核（陷阱 #94，已从 419 s 压到 22 s）。
- 正常结束后**不该留任何进程**；有残留用 `$PY tools/kill_orphans.py --yes` 树杀。

### LNS 一轮有多久（2026-09-22 实测）

**一轮 = 一次 destroy & repair**：随机放开 `k ∈ ks`（`--extreme` = 2,3,4）个槽 →
在 k 槽内枚举 `topk^k` 个组合（numpy 广播）→ 按剩余抗性需求掩码 → 代理降序取前
`eval_cap`（`--extreme` = **120**）个算**真实 DPS** → LAHC 接受判定。

`tune_dps.lns_search` 现在每轮都记时，结束时会多打一行
`LNS 单轮耗时：均值 … 中位 … p90 … 均 N 次真实评估/轮`。实测（`_xyf` lv76 / `--extreme` / 12 轮）：

| 配置 | 均值 | 中位 | p90 | 均真实评估/轮 | 轮/秒 |
|---|---|---|---|---|---|
| 1 链 × **1 进程**（串行） | **441 ms** | 696 ms | 750 ms | 81.1 | 1.6 |
| 1 链 × **4 进程**（生产） | **178 ms** | 295 ms | 300 ms | 81.1 | 2.6 |

⇒ **生产配置下一轮约 0.2~0.3 s**。一轮里约 2/3 花在真实 DPS 评估上
（81 次 × ~2.6 ms ≈ 210 ms），其余是 numpy 邻域生成 + 进程池 IPC。

整轮量级：`--extreme` = **400 轮/链 × 4 链 = 1600 轮**（链间并行）⇒ 每链 ~70~120 s；
`_xyf` 全程 **106 s**（含建池 22 s + 满抗 ILP 21 s）。

⚠ 求总量请用**均值**（中位偏高是因为少数轮次的邻域特别大）；想砍时间优先动
`--lns-cap`（白赚 −40%，结果不变）与 `--lns-patience`（早停）。

---

## ★ BD 配方 v2 —— 一条命令从三要素跑到完整 BD

```bash
PY=".../envs/default/Scripts/python.exe"

$PY -m gd recipe 8,10 75 --from-zero          # 职业组合：自动展开全部形态并发跑、取最优
$PY -m gd recipe wolf_necromancer 75 --from-zero   # 指定形态（省掉形态普查）
$PY -m gd recipe soldier_nightblade --char _xyf    # 已有角色（等级缺省取存档等级）
$PY -m gd recipe 8,10 75 --list-forms          # 只列形态展开
$PY -m gd recipe 8,10 75 --dry-run             # 只打印计划
$PY -m gd recipe 8,10 75 --stages 4 --force    # ★ 断点续跑：只重跑某阶段
```

| 阶段 | 干什么 | 调的工具 |
|---|---|---|
| S0 预算 | 技能/属性/虔诚点数（**只按等级**，不读存档） | `gd/alloc` ← `data/level_table.json` |
| S1 底子 | `--from-zero` ⇒ 造从 0 角色（**临时存档目录**，不碰真存档） | `tools/make_zero_char.py` |
| S2 加点 | 每形态一份加点 → `GD_SKILL_JSON` | `tools/make_alloc.py` |
| S3 形态普查 | 并发跑 `gd auto --extreme --with-weapon`，**按真实 DPS 取最优形态** | `tools/sweep_arch.py` |
| S4 四轴 | 属性 / 技能 / 星座（`--axes` 可含 `gear`） | `tools/coordinate_ascent.py` |
| S5 交付 | BD 报告 + **伤害循环文档** + 合法自检（循环文档缺失 ⇒ 本段判失败） | `gd/planreport` + `plan_cycle` + `plan_legal` |

- **可续跑**：状态落 `data/plans/recipe/<key>/state.json`；`--force` 全部重跑，`--stages` 挑阶段。
- **产物**：`data/plans/final/RECIPE_<key>.md`（总报告）、`BUILD_<key>.md`、`CYCLE_<key>.md`。
- 实测 `8,10 75 --from-zero` 全程 **99.2 s**（S3 占 76 s），续跑 S4 只要 17 s。
- ⚠ 见 `docs/pitfalls.md` #96（从 0 构建三坑）与 #97（v2 设计与实现坑）。
