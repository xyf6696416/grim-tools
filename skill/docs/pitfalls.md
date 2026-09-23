# 陷阱全集（§7）

> 由 SKILL.md §7 拆出。**67 条编号陷阱（1–67）+ 文末专题**：
> · ★★ **装备授予的武器池技能（WPS）从未进循环**（2026-09-20 **已修**，含全形态前后对照）
> · ★ **改完「文档渲染器」必须重跑所有已落盘的产物**（「部分陈旧」最难发现）
> 另有散在正文里的性能/并发/注入/技能来源/减抗三族/加点覆盖/路径坑/敌方档难度字段小节。

## 7. 陷阱

1. **必须用带 numpy 的托管 venv**（见 §2）。
2. **`Scaler` 不能绕过**：直接读原始字段会得到未缩放的"基础值"，与游戏提示框对不上。
3. **`Mf`/`sd` 黑名单里的字段以 `offensive` 开头但不缩放**（暴击伤害/吸血/嘲讽/燃魔）。
4. **值为 0 不缩放** —— 别对 0 调 `rc()`。
5. **`+{%t0}` 是 GT 自己的模板写法**（39 个标签如此，中英文一致）。武器平伤会渲染成
   `+126-862 物理伤害`，这是 GT 的原样，不是 bug。
6. **套装数组是「该件数时的累计值」，下标 = 件数 − 1**（下标 0 恒为 0）。
   例：`[0,0,150,150]` ⇒ 3 件起 +150%。
7. **离线库不含职业技能的逐级数值** —— `dbMasteryData` 只有 `{name: tag}`。
   已通过 `data/mastery_skills.json`（从 .arz 抽出后按 tag 对齐）补齐 356 条，
   见 `tools/migrate_mastery_skills.py`；新增职业/版本时需重新迁移。
8. **`itemdb_diff.js` 有 14 MB，按需懒加载**（不要放进 `DB.load()` 主路径）。
9. 缓存文件名必须用**稳定哈希**（`hashlib`）—— Python 内置 `hash()` 每进程随机化，
   会生成无穷多个 27 MB 的缓存文件（已踩过，已修）。
10. ★★ **记录名 ≠ 名称标签名**：`records/items/gearweapons/melee2h/c204_sword2h.dbr`
    的 `itemNameTag` 是 **`tagGDX2WeaponMelee2hC202`**（记录写 c204、标签写 C202）。
    **任何「从标签推记录名」的规则都必然漏掉它** —— 所以映射表必须从游戏 `.dbr` 直接读，
    见 §11。历史上这就是「武器基础伤害算成 4」的根因。
11. ★ **别对全库逐个调 `savemap.resolve()`**：旧规则兜底 `_family_by_prefix` 是 O(全库)，
    8612 次调用要烧 7.5 s。权威桥表就绪后应直接查表（`resolve()` 已内置 O(1) 快速路径）。
12. 跑长输出命令时别用管道截断 Python 进程（SIGPIPE 会打断写盘），先重定向到文件。
13. ★★ **`resolve()` 的权威快速路径只在传「字符串 gid」时生效** ——
    传 `gt_items().get(gid)` 取出的**对象**会退回规则推导。实测 `gd.build` 因此把
    `it15810`（仲冬之忆）解析成 `c303_necklace.dbr`（**它的权威记录是
    `awakened/…/c308_necklace.dbr`**）→ **静默写错装备**。现已按对象身份反查 gid
    （`_gid_of_obj`），两种入参都命中权威表；改 `resolve()` 时**必须**保留这条。
14. **`resolve()` 传对象**与**传 gid** 必须给出同一结果 —— 自检里有这条守卫。
15. `gd/save/backup.py` 的 `PAIRS` 必须是 `_pairs()` 的**调用结果**（曾被迁移写成
    `PAIRS = ()` → 备份静默空跑、`MANIFEST.json` 写不出来直接报错）。
16. ★ CLI 的**透传型子命令**（`opt` / `recipe` / `rotation` / `build` / `auto`）不能靠
    `parse_known_args` 拼 `rest` —— 未知选项会进 `extra`，拼回去**顺序被打乱**
    （`--compare a.json` → `a.json --compare`，下游解析直接崩）。`gd/cli.py` 的 `main()`
    对这几条**手工切分 argv**（子命令词之后的原始参数原样转发）。
17. **别拿「伤害代理」当伤害**（同 §3.4 注）：它是线性加权和，对本流派会选错。
    要排「真实伤害」必须走 `gd.dps` 的 `final_report`（`tools/plan_dps.py`）。
18. ★★ **性能陷阱（全链路慢的元凶）**：`gd/dps.py` 的 `load_char()` 里有一段
    **每次调用都 `json.load(skills.json)`（11 MB）** 建 `tag→record` 映射 ——
    占单次 DPS 评估的 60%（0.09 s / 0.15 s）。评估上千次就是十几分钟。
    已改为 `_tag2rec_map()`（进程内只建一次）。**改 `load_char` 时别把缓存去掉。**
19. **双手武器时副手不生效**这条游戏规则要在**评估层**执行（`plan_dps.plan_to_override`
    丢弃副手），否则优化器会被「双手+副手」的虚高收益吸引，搜出游戏里无效的组合。
20. **管道会缓冲输出**：`python -m gd auto ... | grep ...` 在退出前看不到任何进度
    （块缓冲）。要看实时进度用 `python -u`，或直接写日志文件。
21. `save_plan --weapon` 里，双手武器角色的 `alt1[1]` 是**只剩镶嵌的空壳** ——
    别把它当「副手装了件」收进方案，否则 override 会把那件镶嵌重复计一次（实测虚高 1.7%）。
22. ★★ **`save_dir()` 返回的是「存档目录」，但备份/落档要的是「app 目录」**。
    `_pairs()` 的根必须是 `.../userdata/<id>/219990`（**不含 `remote`**），因为
    `gd/save/patch.py` 用 `join(LIVE_SAVE,'remote','save')`、`restore()` 用
    `join(LIVE_SAVE, rel)`。曾被改成 `sd.parent`（多一层 `remote`）→ 拼出
    `remote/remote/save/...`，**落档直接报「找不到角色档」**。
    同时 `LIVE_SAVE` / `LIVE_DOC` 曾被写死成空字符串 → 备份能跑、**恢复和补丁全废**
    （它们拼出的是相对路径 `remote\save\...`）。两者均已修，原始意图见
    `tools/migrate_from_archive.py:146`。**改 `_pairs()` 后必跑 patch 预演验证。**
23. ★★★ **`gd/*.py` 会被「迁移脚本自己」打回旧版** —— 不是后台同步、不是别的进程，
    就是 `tools/migrate_from_archive.py`：它的 `FILES` 表从旧归档搬 `gd_req.py` 等
    **基座文件**，重跑一次就把成熟版覆盖。实测一次**静默丢 881 行**：
    `gd/req.py` 507→253（官方方程版整体退回估算版）、`gd/build.py` 652→501、
    `gd/save/patch.py` 313→197、`gd/rotation.py` 1260→1230、`gd/save/backup.py` 218→190、
    `gd/alloc.py` 579→561、`gd/save/verify.py` 327→318。
    **对策（三层，缺一不可）**：
    - ① **真源 = `tools/` 下的保命副本**，清单是 `tools/sync_live.py` 的 `PAIRS`（**16 对**）。
      它同时是 `migrate_from_archive.py` **步骤 24「权威覆盖」**的输入 —— 迁移末尾无条件
      用副本回填活文件，所以**重跑迁移已不再丢东西**（2026-09-19 验证：临时副本重跑 → 零差异）。
      ★ 2026-09-19 **`gd/dps.py` / `gd/opt.py` 的归属从 patch 步骤迁到本表**：
      它们的积累改动到了 **131 / 458 行**（减抗两维、伤害代理边际标定、
      `load_char` 的构建覆盖），已不是「几处字符串替换」能表达的规模。
      迁移里针对它们的步骤 10/11/15/16/17/20/21 仍会跑（作用于旧基座、不报错），
      产物随即被覆盖 ⇒ **那些步骤是过时的**，新增改动一律改
      `tools/gd_{dps,opt,rr}_patched.py` 并 `--capture`，别再往那些步骤上叠。
    - ② 改完任一受管文件，**立刻** `$PY tools/sync_live.py --capture` 刷新副本。
      漏了这步，副本变旧，下一次迁移/抢修就会把新代码打回去。
    - ③ 迁移**开始前**若活文件已与副本不同，旧版留档到 `tools/_reverted/`（不静默丢手改）；
      若是迁移自己刚打回的则不留档，避免淹没证据。
    - 新增「重写级」改动（不是小补丁）时，**加进 `sync_live.py` 的 `PAIRS`**，
      否则它不在真源里，重跑迁移就丢。
    症状特征：`AttributeError: module 'gd.rotation' has no attribute 'mastery_attr_of'` /
    `TypeError: panel_needed() got an unexpected keyword argument` 这种「刚写好的函数凭空消失」
    —— 先跑 `$PY tools/sync_live.py`（只报告不改）即可确认。
24. ★★ **武器需求曾是「死分支 + 谎报 official」**：`gd/req.py` 的 `CAT_EQ` 里有
    `'weapon': (None, None)`，`if cat in CAT_EQ` 先命中直接返回 →
    `elif cat == 'weapon' and tmpl` 是**永不执行的死代码** → 所有武器需求算成 **0**，
    而 `confidence` 仍报 `official`（静默错误）。**修法**：武器分支**提到 `CAT_EQ` 之前**；
    `gd/dbr.template()` 运行时返回 `None`（`.arz` 不在运行时依赖），改按**记录文件名词干**
    （`WEAPON_STEM_EQ` + `weapon_kind()`，**长的先匹配**）判类型；认不出就 `confidence='unknown'`。
    **改 `CAT_EQ` 时必须保证武器分支仍在它前面。**
25. ★ **基线测试的三围断言必须跟「拟合器」同源**：`gd/build.py` 里换了装备后，
    要按**新装备**重新拟合（`_auto_fit_attrs` 传 `override`），不能沿用旧档三围 ——
    否则写进去的是「老装备的需求」，新件照样穿不上（`reqfit` 的 `override` 就是干这个的）。
    同理，`reqfit.solve()` 的 `save_dir=` 必须与「待写档」同一目录，见下条。
26. ★★ **测试副本时「读档」与「写档」必须同源**：`reqfit` 经 `dps.load_char` **默认读真档** ——
    只给 `--save-dir <副本>` 而不同步 `GD_SAVE`，就会出现「拟合真档、写副本」，
    结果副本明明穿不上、拟合却报「已满足」。**修法**：`gd/paths.py` 新增
    `save_dir_override()` 上下文管理器（设/还原 `GD_SAVE`），`patch` / `build` 都把
    `save_dir` 传下去。
27. ★ **`gd build` 只在正式存档目录才备份**：写测试副本时若照样 `do_backup`，
    会把一堆垃圾备份塞进 `E:\xz\Archives`。判据 = 「当前 `save` 是否等于
    `ENV.find_save_dir()`（先临时摘掉 `GD_SAVE` 再问）」，不等就打印「跳过备份」。
28. **`_auto_fit_attrs` 的 `wsets` 必须是 `[(槽位, 部件字典)]`**，不能传「记录名清单」——
    曾把 list 当 dict 用 → `AttributeError: 'list' object has no attribute 'get'`，
    且 `gd build apply` 被 `cli._run_module` 的 `subprocess.call` **吞掉 traceback**，
    只报 `exit=1`。排查时**直接跑 `python -m gd.build`** 才看得到真因。
    （`reqfit._row_of()` 已兼容两种入参。）
29. **`--no-fit-gear` 时写后复检不该拦盘**：`wear_blocking = bool(fit_on)` ——
    显式关拟合就只**强烈警告**，不拒绝写。开了拟合（默认）则**必须过**，否则拒绝落盘。
30. ★★★ **`auto_pool` 用无条件 `break` 分配槽位 ⇒ 「副手」候选池恒为空** 的连锁事故：
    旧实现 `for s in SLOTS: if slot_ok(s, gid): got[s].append(...); break` ——
    一件装备只进**第一个**匹配的槽，而 `SLOTS` 里「主手」排在「副手」之前，
    于是 `got['副手']` **永远是空的**，副手平时靠 `POOL_BASE.get(slot) or POOL_WPN`
    的 or-兜底（6 件）活着。
    **触发链**（`gd auto <角色> --with-weapon` / `--extreme` 必走）：
    `save_plan` 注入「当前穿着」→ `POOL_BASE['副手']` 有值（**1 件**，truthy）
    ⇒ `or POOL_WPN` 不再回退 ⇒ 那 1 件再被 `ALLOW_NORMAL` 滤掉（普通品质）
    ⇒ **副手 0 候选** ⇒ `beam_search_np` 的
    `SUFM[_si] = SUFM[_si+1] + _cR.max(axis=0)` 对零尺寸数组归约
    ⇒ `ValueError: zero-size array to reduction operation maximum which has no identity`。
    **本次触发条件**：存档「当前使用武器套1」里装的是普通品质单手剑（`it2737` 阔剑）。
    修法（`migrate_from_archive.py` 步骤 23，三处，都写进迁移）：
    ① `auto_pool` 的双持槽用 `continue` 而非 `break`；
    ② `choices()` 的武器槽把 `POOL_WPN` **并进** bases，不再当 `or` 兜底；
    ③ `beam_search_np` 空槽不归约 + `run_search` 提前报出「哪些槽无候选」。
    回归守卫：`tools/selftest.py` 的 **[14]**（子进程复现「注入当前穿着」）。
    > 教训：**`x or DEFAULT` 只有在 `x` 可能为「空」时才安全**；
    > 一旦上游会注入「只有 1 个元素」的 `x`，兜底就静默失效。

31. ★★★ **`--compare` 是 `gd/opt.py` 的保留开关，会静默清空候选池**。
    `gd/opt.py` 里有 `_CMP_ONLY = '--compare' in sys.argv`，命中就把
    `AUTO_POOL, AUTO_COMP = {}, {}` —— 候选池只剩硬编码的几十件，
    **问题凭空小 5.5 倍**（项链 478 vs 真实的 2351），此时测出来的任何「快」
    都是假的（拿小问题骗自己）。`tools/ilp_res.py` 因此把对照参数改名 `--bench`，
    并在 import 前**防御性剥离** `--compare`；`tools/autobuild.py` 同理。
    > 教训：**给探针起参数名之前，先 grep 一遍目标模块有没有读 `sys.argv`。**

32. ★★ **HiGHS 本机构建只支持单线程 —— 别在「让求解器并行」上浪费时间**。
    实测 `threads=4/16/32` 或 `parallel='on'` 会把模型**直接跑成 `Not Set`**
    （0.00 s / `gap inf` / 目标 `-0.00`），不是变慢而是**失败**。
    所以 `tools/ilp_res.py` 固定 `threads=1`；多核只能靠「**N 条独立链并行**」吃，
    不能靠「把一个 ILP 拆开」。实测反例：按「传奇装数量 = L」拆成 15 个子问题
    用 16 进程并行要 **27.7 s**，**比单实例 9.0 s 还慢**（子问题加了等式约束后
    形状更难，L=0 那一个就要 26.5 s）。分解实验唯一的收获是交叉验证了正确性：
    L=4 得 2746.70，与完整 MILP 逐位一致。

33. ★ **`--ilp-gap` 别贪快**：`1e-2` 约 6 s，但**实测偶尔停在次优**
    （代理 2741.1 vs 全局最优 2746.7，差 0.2%）；`1e-3` 约 9 s 稳定拿全局最优。
    另外两个「看起来能加速、实测没用」的选项：`mip_heuristic_effort 0.5`（16.3 s，更慢）、
    **MIP start**（把 LP 舍入解喂进去，9.98 s vs 9.02 s，无收益）。
    想知道离最优还有多远：`tools/ilp_res.py:lp_bound()` —— LP 松弛 0.03 s 给出上界
    2808.19（8982 变量里只有 11 个分数分量、5 个槽位被拆开）。
    > 别指望「LP 舍入 + 枚举分数槽位」能秒解：实测那 5 个槽位各取 top-3 枚举 243
    > 个组合**全部不可行** —— 抗性约束卡在边界，任何整数舍入都会让某项跌破需求。

34. ★ **只铺物理核 = machine 只用了一半**：16C/32T 上 `--chains 16` 时整机 CPU
    均值只有 **39.5%**（峰值 60%）；`--chains 32` 后峰值 **98.6%**、均值 65.4%，
    而**墙钟几乎不变**（77 → 80 s），DPS 85,741 → **86,169（+0.5%）**。
    所以并行度的默认值就是 `_logical_cores()`。复现：`$PY tools/cpu_probe.py 45`。
    - ★ **2026-09-20 更新**：普通模式**也是默认逻辑核**（旧版普通/`--quick` 默认 1 条链，
      作者的「进程启动开销 > 收益」权衡在带 `--with-weapon` 时早已不成立 ——
      单链一轮实测十几分钟）。条数由 `_cap_chains_by_mem` 两道闸兜底：
      **① 内存（每链估值已从 1.4 GB 修正为 0.5 GB —— 实测每链只要 ~0.19 GB）
      ② 逻辑核**。旧估值高 7 倍 ⇒ 32 核机器只铺得出 22 条，白空 10 个核。
      显式 `--chains N` 永远优先，`--jobs N` 会自动转成 N 条链。详见坑 36。

35. ★★ **「装备池子」有两层，先分清再排查**（2026-09-20 用户口径「检查一下装备池子是否
    与数据库一致」）。用户拿的是 grimtools 物品库**总览面板**的 9 个数字，那是
    **数据库层**；而「优化器拿什么去搜」是**候选池层**。两层都要查，且查法不同：
    - **库层** → `tools/db_census.py`（9 行逐行复算，见 §3.13）。⚠ 两个坑：
      `物品总数` **不是** `allItems` 长度（要去掉 311 条 `ItemNote` 游戏内书籍）；
      `物品技能修正` **不是** `itemSkills` 长度（是 5 张表 `modifierSkillName*` 值的
      **去重并集** 3297，表里另有 971 条孤儿记录）。**别用「表长」当口径**。
    - **池层** → `tools/db_census.py --pool`。**硬编码池会绕过所有校验器**
      （`slot_ok` / `_is_quest` / `comp_ok` 只在 `auto_pool` / `auto_comp_pool` 里跑），
      所以「池里有任务物品 / 类别与槽位不符」这类错误**只在硬编码名单里出现**。
      已抓到的四类（全部已修，见 §3.13 表）。
    - ★ **成对/可互换槽位**（`主手`/`副手`、`戒指1`/`戒指2`）在 `auto_pool` 的
      `for s in SLOTS: … break` 里**必须用 `continue` 而不是 `break`** ——
      漏一个就得到「后一槽恒空、只剩硬编码名单」的隐形瘫痪。
      **判据**：两槽的 `SLOTDIR` 与 `slot_ok` 是否同一判据。是 ⇒ 池必须同源。
    - 池层普查的必过项：池内每一项 **① 在库里 ② 过 `slot_ok`/`comp_ok`
      ③ 非任务物品 ④ 成对槽位同源**。这四条已固化成 `selftest [25]`。

36. ★★★ **`--jobs` 不是并行开关 —— 传了也不加速**（2026-09-20 实测：32 核机器
    **整机 CPU 只有 6%**、跑了 4 分半没出结果，用户直接问「优化没用上」）。三层原因：
    - `--jobs N` 只作用于**束搜索重启**的 `ThreadPoolExecutor`，被 GIL 锁成近似串行
      （作者在 `run_cap_search` 的 docstring 里早写明「74 s ÷ 6 = 12 s/次，正是单次
      耗时」）⇒ 对总墙钟**几乎无影响**。更误导的是它的默认值是物理核（16），
      **看起来像「已开 16 并行」，实际一点没并行**。
    - 真正吃满多核的是 **`--chains N`**（`mp.spawn` 多进程、每链独立单线程）。
      普通模式默认 `chains=1`，只有 `--extreme` 才自动铺 `_logical_cores()`。
    - 局部搜索本身才是瓶颈：实测**每槽约 31 s**（`--topk 60` 串行实算）× 14 槽
      × 2 轮 ≈ **15 分钟单链**。所以「**快**」要靠 `--quick` / 调小 `--topk`；
      「**强**」才靠 `--chains`（墙钟 ≈ 单链，搜索量 ×N —— 它是买搜索量，不是变快）。
    **已修**：① 显式 `--jobs N` 自动转成 N 条并行链；② 单链时主动打印
    「本机 N 逻辑核，搜索只占用 1 核」；③ `selftest [26]` 守住「并行意图优先于
    `--quick`/`--extreme` 预设」这个**判定顺序**（顺序反了会被 quick 分支先置成
    1 条链 —— 这个坑当场踩过一次）。修后实测：**整机 CPU 6.1% → 70.9%**、20 核满载。
    - ★ 伴生教训：`… | tail -60` 会把输出**缓冲到进程结束**才吐 ⇒ 全程看不到进度
      （坑 20 的变体，也会让人误判「卡死了」）。长跑一律 `python -u … | tee 日志`。

37. ★★★ **「优化器怎么跑这么久」的真答案：敌方档没缓存，占单次评估的 99.5%**
    （2026-09-20 用户追问「到底有多少组合」）。**先别去数组合** —— 组合从来不是瓶颈：
    - 14 槽的**原始**组合数确实是 **1.30 × 10³⁰**（约 30 个数量级：头部 155 ｜ 项链 650 ｜
      胸甲 230 ｜ 腿甲 116 ｜ 靴子 68 ｜ 手套 126 ｜ 戒指1 440 ｜ 戒指2 440 ｜ 腰带 24 ｜
      肩甲 172 ｜ 勋章 84 ｜ 圣物 9 ｜ 主手 306 ｜ 副手 306），**但它从未被枚举** ——
      满抗走 ILP（7863 个 0/1 变量、23 条约束）**3.0 s 拿全局最优**。
    - 贵的是后面「在满抗解空间里追伤害」的**局部搜索**：一轮 ≈ **1204 次真实伤害模拟**
      （单槽 14×`--topk 60` + 双槽 C(14,2)=91 × `pair_k²`=4），`passes=2` ⇒
      **2408 次/链**；20 条并行链合计 **≈4.8 万次**。
    - 而**每次**模拟 `plan_dps.dps_of()` 都要调 `gd.enemy.get_profile(None, level)`，
      它解析等级池要 **2.007 s**，且**没有任何缓存** ⇒ 单次评估 **2.168 s**，
      `get_profile` 占 **99.5%**（`load_char` 只有 2 ms、`final_report` 3 ms）。
      ⇒ 一条链 ≈ **87 分钟**。`--chains` 救不了：**链内是串行的**，
      加并行度只是让更多进程一起等同一个 2 秒调用。
    - **已修**：`get_profile` 按 `(spec, level, players, difficulty)` 记忆化，
      原逻辑抽成 `_get_profile_uncached`；返回 **`copy.deepcopy`**
      （调用方会就地改 `nested['res']`，吐同一对象会污染缓存，实测过）；
      新增 `clear_profile_cache()`。

      | | 修前 | 修后 |
      |---|---|---|
      | `get_profile`（热） | 2.007 s | **0.00002 s** |
      | `dps_of` 单次 | 2.168 s | **0.0030 s**（**725×**） |
      | 一条链 | ≈87 分钟 | **≈7 s** |
      | 整轮 `autobuild --goal super --with-weapon` | 小时级 | **19.1 s** |

      零漂移（Sam 锚点面板 52,737.6 逐位不变），`selftest` 新增 5 条缓存断言。
    - ★ **方法论**：遇到「慢」，先 **profile 单次评估**再谈并行 ——
      `cProfile` 一跑就是 `get_profile 2.007 s`，比任何推理都快定位。

38. ★★ **`autobuild` 必须单实例**（2026-09-20 实测事故：用户报「后台爆了，新的旧的都在跑」）。
    两个 `autobuild` 同时在跑的三个后果：① 两个进程往**同一个 `--out` JSON** 写
    ⇒ 后写的覆盖先写的，谁的结果都不可信；② 各自 spawn 20~30 个 worker
    ⇒ 一次 **53 个 `python.exe`**；③ 日志交错，分不清 DPS 出自哪条。
    **已修**：`data/scratch/.autobuild.lock` 存 PID，持有者活着就拒绝启动（退出码 **4**）、
    已死**自动回收**（不需要人工清锁）；`--force` 抢占逃生口；`selftest [26]` 守四情形。
    - 排查手法：PowerShell 工具在本会话可能返回空，改用托管 venv 的 **`psutil`**
      列 `(pid, ppid, cmdline, create_time)` 即可看清进程树；
      杀树用 `taskkill /F /T /PID <根>`（Git Bash 里要 `MSYS_NO_PATHCONV=1`）。

39. ★★ **星座（虔诚）的「防御向」贡献此前完全不可见**（2026-09-20 用户提醒
    「星座是有效果的 可以绑定技能」时顺带查到的）。两个独立缺陷：
    - `data/devotion_skills.json`（716 条）是一份**只有名字、0 条带 `stats`** 的索引，
      它先占住记录槽位 ⇒ `gd/dbr.py` 第 4 步整批跳过 ⇒ **701 条星座记录的真实数值
      永远进不了模型**（Sam 的 32 个星座节点曾全部算出 `pct={} flat={} spd=0`）。
      修法：第 4 步从「只判不存在」改为「**能补字段**」（`setdefault`）。
    - `gd/rotation.devotion_contrib` **只统计进攻向** ⇒ 星座给的抗性**读不出来**。
      修法：新增 `res` / `raw` 两个键（`res` 用 `gd/opt.py::RMAP` **同口径**，
      可直接与 `NEED` 130/105 对拍）。实测 150 个星节点里 **54 个带 `defensive*`**，
      且 Sam 现状星座带 **-8 穿刺 / -4 物理 的代价**（负值**不许 clamp 成 0**，
      那是真信息）；`select_coherent` 的方案则给 火/冰/电 +28、混乱 +10、虚化 +10、活力 +8。
    - ★ **口径边界（报告里必须写清）**：优化器的「满抗」只累加**装备槽**的抗性
      （`gd/opt.py::ev` 只做 gear 的 `res_of`）⇒ 报告的满抗 = **仅靠装备也满**，
      是**保守**口径。**别把它读成**「星座给抗性 ⇒ 装备可以少堆」。
    - ★ `db.records_like(prefix)` 收的是**前缀**不是子串 —— 传 `'devotion'` 得 0 条，
      要传 `'records/skills/devotion'`。

40. ★★★ **`autobuild` 只优化装备，技能加点永远是存档的**（2026-09-20 用户问
    「为什么还是走到了狼人」时查出的**结论性**坑）。`--archetype` 只改
    `damage_weights`（装备取向），**不换技能** ⇒
    「换个形态跑一遍」得到的其实是「**新形态的装备 + 存档旧形态的技能**」。
    实测：`--archetype wereraven` 输出的分解里全是 `野性利爪`/`狂乱撕扯`（狼人技能），
    只涨 1.5% —— 因为技能压根没动。
    **正确姿势**（换形态/做形态对比时）：
    ```bash
    GD_ARCHETYPE=<形态> "$PY" tools/make_alloc.py <形态> <等级> --out alloc.json   # ① 形态加点
    GD_SKILL_JSON=alloc.json GD_ARCHETYPE=<形态> "$PY" tools/autobuild.py Sam \
      --archetype <形态> --goal super --with-weapon --out out.json                # ② 形态装备
    ```
    `GD_SKILL_JSON` 的注入点在 `gd/dps.py::load_char` ⇒ **所有**走 load_char 的调用方
    （autobuild / plan_dps / planreport / gd dps）都会吃到。
    `tools/make_alloc.py` 内部用 `gd/alloc.py::allocate`，它**自带形态过滤**
    （基于 `root_skills[0]` 记录名前缀 `werewolf\d+` / `wereraven\d+`）——
    所以 `make_alloc.py werewolf` 出来的表里不会混进鸦人技能。

41. ★★ **新增 archetype 必须写「两张表」，且 `form` 字段是门控开关**。
    - **运行时读主表** `data/archetypes.json`（**扁平**结构，顶层直接是形态名）：
      `gd/alloc.py:242`、`gd/opt.py:314`、`tools/autobuild.py:532` 都读它。
    - `data/archetypes_gdskill.json`（**嵌套** `{"archetypes":{…}}`）**只给迁移
      步骤 23.5 幂等合并用**，不参与运行时。
    - ★ 只写 gdskill ⇒ `make_alloc` **静默回落到 human**（实测：`raven_nightblade`
      生成了 `onslaught/leap/windsofasterkarn + wpattack01/02` 的人形态加点，
      **一个鸦人技能都没有**，且不报错）。
    - ★ `form_gate`（`gd/rotation.py`）的契约是「**没写 `form` 就完全不过滤**」——
      这是为了不改变历史形态的语义（`wolf_nightblade` 的 `form` 语义
      「狼人形态下夜刃 WPS 是否触发」还没被游戏实测标定）。所以
      **`wereraven`/`werewolf` 这类老原型保留存档技能**，做形态对比时
      必须靠 `GD_SKILL_JSON`（见坑 40）而不是靠 `form`。
    - `damage_weights` 是**固定 8 个维度名**（phys/pierce/bleed/total/OA/spd/crit/fpie），
      靠 `field_aliases` 把维度**指向**该形态的真实伤害字段。★ 建鸦人原型时发现
      旧 `wereraven` 的 `pierce` 别名指向 `offensiveSlowColdModifier`（冰 **DoT** 加成），
      但 `寒冰之爪`/`霜暴` 有约 1/3 是**穿刺直伤**（`offensivePierceMin` 16/34 起）⇒
      这 1/3 成分没被优化器覆盖。`raven_nightblade` 已改回
      `offensivePierceModifier`（默认值）。

42. ★★ **多投射物（`projectileLaunchNumber`）默认不乘，只有形态声明 `projectile_hits` 才乘**
    （2026-09-20，用户「理论上乌鸦形态的伤害会更高一点」引出）。
    - 数据：全库只有 `霜暴`（`wereraven1_skill02_icering`）带该字段 =
      `[8]` + `projectileLaunchRotation [360]` + `projectilePiercingChance [100]`。
      狼人的 `野性利爪`/`狂乱撕扯`、夜刃 `wpattack1/2/3/5`、鸦人 `寒冰之爪`
      **全都不带** ⇒ 乘区**不会误伤狼人**（口径对等，已自检）。
    - **不是 bug 而是口径选择**：grimtools 计算器对单体也只算 1 枚
      （环射的 8 枚打同一个目标通常只有 1 枚生效）。所以默认 **1.0**，
      要「贴脸全中」必须在形态表里显式写 `"projectile_hits": 8`。
    - 实现：`gd/rotation.py::_make_hit` 里的 `_n_proj`（默认 1.0）→ 行级 `_k`
      **只作用于非 DoT 行**（同一目标的 DoT 是刷新不是叠加）；
      `projectiles` 键**只在乘区生效时**写进 hit（默认时 rep 结构与历史逐位一致）。
    - 实测影响（鸦人+夜刃，同一套装备/加点）：**35,557 → 67,277（+89%）**，
      相对狼人+夜刃（45,305）由 −21.5% 翻转为 **+48.5%**。
    - **零漂移**：不声明时狼人锚点面板 **52,737.6** 逐位不变（自检 [27] ⑨ 守着）。
    - ★ 判据：看到技能的 `projectileLaunchNumber > 1` **先问「打单目标有几枚生效」**，
      别默认乘满。
    - 追极限时**必须做口径敏感性对照**（`GD_PROJ_HITS` 环境变量 / `plan_audit --proj 1,4,8`）：
      同一套装备在 ×1 / ×4 / ×8 下绝对值能差近一倍。★ 但反过来也测过了 ——
      **装备最优选择几乎不随口径变**（保守口径独立重优化的最优值只比共用装备高 **0.7%**）
      ⇒ 口径只改绝对值，不改结论，所以可以先用 ×8 搜、再用 ×1 报「下界」。

43. ★★ **词缀维度曾经整条链路是死的（前缀/后缀池恒空）** —— 2026-09-20 追极限时抓到。
    症状：**16 份方案的词缀列全是 `— / —`**，而存档里真实装备有 2~3 前缀 + 4 后缀。
    两处根因，**必须一起修**：
    - ❶ 路径错：`gd/opt.py::_parse_affixes()` 只找 `data/itemdb.js`，
      真实文件在 **`data/cache/itemdb.js`**（8.7 MB）。找不到就**静默 `return {}`**
      ⇒ `POOL_PRE` / `POOL_SUF` 全空，而**没有任何报错**。
    - ❷ `cls` 有两种写法：JSON 数组 `cls:["c12","c24"]` 与
      **`cls:"c20 c21 c24".split(" ")`**。旧解析把后者存成**字符串**
      ⇒ `_affix_slots()` 对字符串做迭代 ⇒ 逐**字符**遍历 ⇒ 一条槽位都映射不到。
      修法：一律 `re.findall(r'c\d+', ...)` 归一成 `['c20','c21','c24']`，
      并且**只在键名是 `cls` 时**才按数组处理（别把别的数组字段也吃进来）。
    - 修复后：词缀库 **4105 条**，`POOL_PRE/POOL_SUF` 条目 **3220 / 4019**，
      `POOL_SUF['主手']` 含施法匕首 `c24` 的从 **0 → 142 条**。
    - 实测收益（夜刃+狂战士 lv71 满抗极限，鸦人形态）：**94,474 → 118,334（+25.26%）**。
    - 自检 [28] 用 3 条断言把这两处钉死（条数 / `cls` 归一化 / c24 后缀计数）。

44. ★ **词缀「合法」有三条硬规则，`affix_ok()` 只覆盖第一条**（`tools/affix_fit.py` 补齐）。
    - ① 词缀的 `cls` 必须含**底材类别码**；② 条数 ≤ `maxAffixes`；③ 词缀 `k` ≤ 目标 ilvl。
      `gd.opt.affix_ok()` **只校验槽位适用性**，**不校验底材类别** ——
      第一版探针就是因此把 `pre4478`（`c28-c32` 近战单手专属）配到了 `c24` 施法匕首上，
      算出虚高的 **+51.10%**；按真实规则修正后是 **+25.26%**。
    - ★ **底材类别码 = GT `_IT` 的 `l` 字段**（`it2063` → `l: "c24"`）。
      `gd/opt.py::_req_lvl()` 把这个 `l` 当成「需求等级」在用是**误读**
      （真实需求等级在 GT 数据里是数字型 `l` / `k`）；**已发现，未修**（只影响
      `autobuild.get_gearplan` 的排序，不影响本次结论）。
    - `maxAffixes`：紫/蓝 = 0（只能靠固定数值）、绿**多为 2 但也有 1**
      （实测 `it2063` 大师级魔刃 = 1）；字段缺失时**按 2**。
    - 落地面：`tools/affix_fit.py` 的 `base_cls()` / `max_affixes()` / `legal_pool()`，
      自检 [28] 守着 `base_cls(it2063)=='c24'`、`max_affixes(it2063)==1`、
      「合法池 ⊂ 原始池」且 `pre4478` 被剔除。
    - 注：`gd/opt.py::slot_cands()` **本来就支持词缀折叠**（`BEST_PRE/BEST_SUF`），
      缺的只是这两个表恒空 —— 修好解析后折叠立刻生效
      （`prune_cands` 实测 288/214/28 → 132/97/0）。

45. ★ **「并进报告」的东西必须是生成器的能力，不能手工贴**（2026-09-20）。
    方案报告 `gd/planreport.py` 的「伤害」是**线性伤害代理**（排序用），不是 DPS。
    真实 DPS / 命中暴击 / 属性重排由 `tools/plan_audit.py` 算 —— 早先只能手工往 md 里贴，
    **一重生成就丢**，且**没有任何报错**。现在 `planreport --audit` 直接调用
    `plan_audit.analyze/render_md` 把体检段插在 `## 一、装备` 之前。
    - ★ `tools/plan_audit.py` 分成三层：`analyze()` 出数据、`render_text()` / `render_md()`
      出两种视图。**新消费方用 `analyze()`，别去解析终端文本**。
    - 配套：`gd/planreport.py` **在**迁移 `FILES` 里 ⇒ 必须同时登记进
      `tools/sync_live.py::PAIRS`（现 **17 对**），否则重跑迁移会静默打回旧版。
      自检 [13] 守「活文件 == 副本」、[28] 守「`--audit` 已注册」。

46. ★★★ **模拟退火的「预算越大越差」病灶 → LNS 换代**（2026-09-20，用户口径
    「放弃退火这个方案 太慢了 有没有更优秀的算法」）。两个结构性缺陷：
    ① 温度挂在**墙钟**上（`frac = el / budget_s`）⇒ 420 s 预算下指数降温极慢，
       大半时间在高温随机游走；
    ② `moves` 随预算变大（`≥180 s` 加四槽联动），而随机挑 4 槽几乎不可能同时满抗
       ⇒ 绝大多数迭代 `okp=False` **直接 `continue`，连评估都没做**。
    实测：退火 100 轮只做了 **65 次真实评估**；从 94,474 再退火 47 s 到 117,457，
    而 30 条链各跑满 420 s 只到 94,474 —— **预算成了负资产**。
    换 **LNS + LAHC**（`tune_dps.lns_search`）后：600 轮 / 106.5 s → **128,756**。
    - ★ 关键设计：每轮随机放开 k 个槽做**向量化精确枚举**（numpy 广播一次算完
      「抗性和 + 代理和」，再按剩余抗性需求掩码剪枝），**不做随机游走**；
      接受准则是 LAHC（长度 `hist_len` 的历史队列），**无温度、无降温调度**。
    - ★ 变量名坑：numpy 的抗性累加矩阵**不要叫 `acc`** —— 那是同函数里的接受计数器，
      撞名会 `TypeError: %d format: a real number is required, not numpy.ndarray`。
    - ★ **同一文件的多处改动要串行 Edit**：这次两次 Edit 到 `tools/autobuild.py`
      （加 `--algo` 参数、改 `--extreme` 的 help）**只有后者存活**，`--algo` 参数
      静默消失，直到自检 [29] 才抓到。自检 [29] 就是为此加的护栏。
     - ★ GitHub 调研落点：**LNS**（Pisinger & Ropke 2010）、`N-Wouda/ALNS`（Python 实现）、
       OR-Tools CP-SAT 官方 Primer（明说「LNS 往往优于其他所有方法」）。

   **★ 多形态并发（2026-09-20 晚新增）**
     - ★ **别以为「一台机器只能跑一个 autobuild」** —— 那是旧的全局单实例锁。
       现在锁按 **(角色, 输出文件)** 取键：`--out` 不同就能并发。
       想跑多形态批量，用 `tools/sweep_arch.py`（§3.19），别自己循环。
     - ★ **`--budget / len(archs)` 是整除**：形态多而预算小时 `per` 会掉到 1~2。
       宁可 `--budget 30`（6 形态 ⇒ per=5），别用 `--budget 6`。
     - ★ 并发**不改变结果**，只改墙钟（实测逐位一致）⇒ 可以放心用并发普查替代串行。
     - ★ 三个不同层次的并行，**别叠加**：
       `--jobs/--chains`（单形态内多链）｜`--procs`（单池邻域并行）｜
       `sweep_arch`（多形态多进程）。前两者互斥已由代码保证，第三者是外层驱动。

47. ★★★ **落档必须写进「启用」的那一套武器 —— 否则「落档成功」但武器不生效**
   （2026-09-20 落档 `wolf_nightblade_fast` 时实测抓到）。
   一个角色有**两套武器**（`alt1` / `alt2`），用 `alt1_unused` / `alt2_unused` /
   `use_alt_weaponset` 三个标志决定哪套在用。旧 `gd/build.py::do_apply` **恒写 `alt1`**，
   且写盘时把三个标志**原样保留** ⇒ 若启用的是 `alt2`：
   - `gd dps Sam` 面板 **103,682 → 67,637**（差 **53%**）；
   - 玩家进游戏手上还是旧武器（**静默**，工具全程报「复检通过 ★」）。
   **判据**（`gd/build.py::_active_weapon_set`，与 `gd/dps.py::load_char` **逐字一致**，
   两处必须同步改）：
   ```
   alt2_unused=True  且 alt1_unused=False ⇒ 用 alt1
   alt1_unused=True  且 alt2_unused=False ⇒ 用 alt2
   两者相同 ⇒ 退回 use_alt_weaponset（True ⇒ alt2）
   ```
   修完 `do_apply` **写启用套**、`build_plan` 对拍读启用套、`do_check` 比启用套、
   `do_new` 导出草稿也读启用套 —— 四处都要改，只改写盘会变成「写完报不一致」。
   **自检** `[31]` 有一条 4 情形表守这个判据。
   **落档后的正确验收**：`gd build check <spec>` 应报「方案与存档完全一致 ★」，
   且 **`plan_audit` 的方案评估 == `gd dps Sam` 的存档实测**（逐位）。

48. ★★★ **`--fit-buffer` 曾对「本来就过阈值的属性」完全失效**
   （2026-09-20 用户报「动态调整属性没生效 · 好多装备都穿不上」时抓到）。
   `gd/reqfit.py::solve()` 里原本写：
   ```python
   for k in ('physique', 'spirit'):
       if pmin[k]:                       # ← ✗ 只在「必须投点」时才加余量
           pmin[k] += max(0, int(buffer))
   ```
   `pmin[k]` 是「达到阈值**所需的最低点数**」。**只要面板本来就过阈值，
   `pmin[k] == 0` ⇒ buffer 一点不加** —— 而「本来就过」正是最常见的情形。
   实测（Sam 落档 `wolf_nightblade_fast` 后）：体格 `pmin=0`
   （0 点面板 438 ≥ 需求 429，**余量只有 9**），`--fit-buffer` 调到 **40 都不动体格**；
   同一次调用里 `pmin['spirit']=11 > 0` 却能拿到 buffer ⇒ **两个属性待遇不一致**。
   ⇒ 落档后余量仍是 9 点，而模型口径与游戏存在 **≤21 点**的未知偏差
   （由「旧配置 21 点余量下游戏认可」+「新配置 9 点余量下游戏不认」夹逼得到）
   ⇒ 进游戏就穿不上。
   **修法**：判据改成「**该属性有需求**就留余量」——
   ```python
   for k in ('physique', 'spirit'):
       if need.get(k):                   # ✓ 有需求就留，与「原本够不够」无关
           pmin[k] = pmin.get(k, 0) + max(0, int(buffer))
   ```
   **修后实测**（Sam · 同一个方案）：
   | `--fit-buffer` | 体格点 | 面板体格 | 余量 |
   |---|---|---|---|
   | 0 | 0 | 438 | +9 |
   | **1（默认）** | **1** | **446** | **+17** |
   | **3（推荐）** | **3** | **462** | **+33** |
   | 10 | 10 | 518 | +89 |
   ⚠ 改完 `used` 会变大 ⇒ 预算真不够时会**如实**报 `feasible=False`
   （以前是「悄悄不给余量、看着成功」）。
   **自检** `[31]` 有一条断言守它（`buffer=3` 的体格点数 ≥ `buffer=0` + 3）。
   **配套教训**：凡是「**默认参数看着生效、其实被 if 短路**」的分支，
   都要有一条**跨参数取值的断言**（本例是「buffer=0 vs buffer=3 逐属性比点数」），
   否则单测某个固定 buffer 只会看到「跑通了」。

49. ★★★ **「减需求」字段被无差别扣到**三个属性**上 ⇒ 需求被系统性低估**
   （2026-09-20 修 #48 时顺带抓到，是「好多装备穿不上」的**直接主因**）。
   `gd/req.py::_apply_reduce()` 旧实现：
   ```python
   red = 0.0
   for k in REQ_REDUCE_FIELDS:
       vv = f.get(k)
       if vv and vv[0]:
           red = max(red, abs(float(vv[0])))      # ← 不区分属性
   if red:
       for k in ('physique', 'cunning', 'spirit'):  # ← 三个属性全扣一遍
           if out.get(k):
               out[k] = int(round(out[k] * (1 - red / 100)))
   ```
   而字段名里**写着作用属性与限定条件**：
   `characterArmor/Melee/Hunting**Strength/Dexterity/Intelligence**ReqReduction`
   —— `…ArmorStrength…` 只减**体格**、`…HuntingDexterity…` 只减**狡诈**、
   `Armor*` 只对护甲 / `Melee*` 只对近战武器 / `Hunting*` 只对远程生效。
   **实测代价**：`it1800`（使者的夹克，胸甲）带
   `characterHuntingDexterityReqReduction: 15`（**狡诈**、且限**远程武器**）
   ⇒ 体格需求 **464 被扣成 394**（×0.85），**而游戏提示框明写「需要体格: 464」**
   ⇒ `reqfit` 的体格阈值取成 429（手套）而**漏掉 464（胸甲）**
   ⇒ 模型报「全部可穿 ✓」，**落档后进游戏穿不上**。
   **修法**：按字段名里的属性**分别**扣（`REQ_REDUCE_RULES`；
   同一属性多字段取 max 只扣一次）。限定条件（Armor/Melee/Hunting）**暂未判**，
   已登记待查 —— **只按属性匹配就已经修掉本 bug**。
   **修后实测**：`req('it1800', …, '胸甲')` = 体格 **464**（= 游戏提示框）
   ⇒ `reqfit` 体格阈值 429 → **464**。自检 `[31]` 有断言守它。
   **排查口径**：怀疑「需求算错」时，先
   `python -c "from gd import req as RQ; print(RQ.req('<GT id>','<记录名>','<槽位>'))"`
   ——**注意必须传 `record`**，否则 `_apply_reduce` 不跑、结果会**偏高**
   （本例不传 record 得 464、传了得 394 —— 传了才暴露 bug）。

50. ★★ **「游戏内面板真值」通道此前从未启用 ⇒ 一批面板偏差长期不可见**
   （2026-09-20 用户发游戏截图后首次启用）。
   `data/regress/Sam.sheet.json` 的 `in_game` 段一直是**空的**
   （`tools/gt_regress.py --sheet Sam` 报「未填写」）⇒ **没有一把尺子量过面板**。
   首次誊录（2026-09-20 20:57 截图）后立刻暴露 5 处偏差：

   | 项 | 引擎 | 游戏 | 差 |
   |---|---|---|---|
   | 等级 | 71 | 71 | ✓ |
   | **体格** | 438 | **383** | 引擎 **+55** |
   | **灵巧** | 1119 | **1139** | 引擎 −20 |
   | 精神 | 341 | 341 | ✓ |
   | **OA** | 1842 | **1795** | 引擎 +47 |
   | **DA** | 1396 | **1688** | 引擎 **−292** |

   ⇒ **四类偏差方向/幅度都不同** ⇒ 不是单一缩放，是**多个独立缺口**。
   **已确证的一个**：体格那 +55 恰好等于胸甲 `it1800.characterStrength`
   （`characterStrength` 在**装备**上是「+体格」，见游戏提示框「+65 体格 [44-66]」）
   ⇒ 需要一次**判别实验**才能定归属：
   > **把胸甲卸下，看游戏面板体格**：
   > · 掉到 **328**（−55）⇒ 胸甲确实给体格 ⇒ **精通被高估 55**
   > · 基本不变 ⇒ 装备字段被算错 ⇒ 查 `fold()`
   **用法**（本通道**不需要网络**）：
   ```bash
   $PY tools/gt_regress.py --sheet Sam --emit-sheet   # 出模板（engine_values 自动填）
   # 打开 data/regress/Sam.sheet.json，把 in_game 段的 null 照游戏面板抄上
   $PY tools/gt_regress.py --sheet Sam                # 对拍
   ```
   ⚠ 三围（体格/灵巧/精神）`engine_values` / `in_game` 里**没有对应字段**，
   首次誊录暂记在 `in_game._meta`（要自动对拍得先扩 schema）。

   **★ `GD_SKILL_JSON` 注入（2026-09-20 晚新增）**
     - ★ **注入形态加点会清空星座，除非显式保留**：`gd/dps.py::load_char` 里
       `skill_override` 曾**整体替换** `skills` 字典，而形态 JSON 只描述技能/精通条、
       不含星座 ⇒ 存档 32 点星座被**静默清零**（技能条目 63 → 12）。
       后果：整条搜索链都在「星座全空」的口径下评估，报告 §六 谎报「已点 0 / 55」。
       **已修为「保留存档的 `/devotion/` 条目」**。今后任何"用外部 JSON 覆盖某类
       记录"的写法，都要先问：**这个 JSON 是不是只描述了记录的一个子集？**
     - 自检口径：`planreport` 的「星座已点 N/M」必须 ≥ 存档真实值，
       不是 0；改动 `load_char` 后**必查**这一条。

   **★ 形态技能来源必须合法（2026-09-20 追查「刺击」）**
     - ★★ **`archetypes.json` 里的 `root_skills` / `core_skills` 若指向
       `records/skills/itemskills*`（物品技能），等于白送技能** ——
       物品技能在游戏里**必须先装备对应物品**才有，且受该物品的等级需求约束。
       而 `rotation.analyze()` 是把它们当角色自带技能**无条件计入**的 ⇒
       绕过「装备前提」与「等级过滤」，算出**不可构建**的 DPS。
     - 实例：`fangs`（完美姿态）是 12 个形态里**唯一 `auto: true`** 的，
       `mastery: "other"`，三条技能全来自 `itemskillsgdx3/relics/fangs*`：
       `fangs.dbr`(shapeshift 完美姿态) → 授予 `fangs_triplejab.dbr`(刺击) + `fangs_screech.dbr`(尖啸)。
       刺击的**唯一授予者**是 GDX3(Fangs of Asterkarn) 的 tier3 传奇遗物 **`it15928`**
       （图标 `gearrelics/tier3/tier3_relic_306.png`，`artifactClassification: "Divine"`，
       **`k = 等级需求 90`**、`itemLevel 90`）。lv71 存档**装不上** ⇒ 该形态的
       104,966 / 114,782 **不可构建**。
     - **一般规律**：GD 里 **`items/gearrelics/tier3/` 的遗物 `k` 恒为 90**
       （GDX1/2/3 全批实测都是 90）⇒ tier3 遗物一律 lv90 起步。
       对照：`data/plans/*.json` 里 14 个槽位的装备 `k` **全 ≤ 70**（装备搜索确实按等级过滤了）。
     - **判据（加新形态 / 审老形态时必查）**：
       ① `root_skills`+`core_skills` 的路径前缀是不是 `itemskills*`？
       ② 若是，反查哪个 `it*` 通过 `itemSkillName: "skXXXX"` 授予它
          （在 `data/cache/itemdb.js` 里搜技能组 id），看那个物品的 `k`；
       ③ `k > 角色等级` ⇒ 该形态在目标等级**不可构建**。
     - ★ 报告里的「默认攻击（左键）」是 `rotation.analyze()` 在 `swing` 池里按
       `max(avg)` 兜底选的（形态没声明 `primary_attack` 时）——**不代表这个技能能拿到**。
     - 现状：**已发现，未修**（改它会动到已有数值结论，需先评审）。逃生口是别用
       `--arch fangs`，或先给形态加等级前提再评估。

51. ★★★ **归档目录一旦落在「活存档根」里，备份会指数膨胀**（2026-09-20 实测踩到）。
   做「一键换档」的写盘测试时把 `GD_BACKUP_DIR` 指到了存档副本里面，结果：

   ```
   第一次 backup  66 个文件  →  第二次把「上一次的备份」也当存档内容复制
   2 分 22 秒后：553 MB ／ 280 份 MANIFEST（还在涨）
   ```

   **两个放大因子**：
   ① `copy_tree_tolerant(base, target)` 是**整棵树照抄**，`base` = 活存档根
      （`.../219990`，见 `_pairs()`），归档若在其下 ⇒ 上一次的备份被当成存档内容；
   ② 新备份又落在同一个归档里 ⇒ 下一轮再翻一倍。**没有任何提示，界面看起来只是在「慢」。**

   **判据**：`_under(dst, base)` —— 归档目标目录是否落在任一 `PAIRS` 根内。
   **已修**：`gd/save/backup.py::do_backup` 与 `tools/gd_restore.py`（开工前先拦）
   都会直接报错并说明原因（默认 `E:\xz\Archives` 永远碰不到这条 —— 一旦碰到就是配置错了）。

   **通用教训**：凡是「**把目录整棵复制到 X**」的工具，都要先检查 **X 是否在源树里面**。
   这不是备份特有的 —— 递归输出目录、日志目录、渲染产物目录同理。

   **排查手法**（此坑的症状是「只是很慢」，极易误判成磁盘/杀软问题）：
   `find <归档根> -name MANIFEST.json | wc -l` 与 `du -sh` —— 数量/体积异常增长即中招。

   **★ 同族（同一轮抓到的）：`--live` 指向无效目录会「静默回落到真实存档」。**
   `gd/paths.py::save_dir()` 的次序是「先看 `GD_SAVE`，不成立再自动探测」——
   于是 `--live` 打错一个字母时，工具**不报错**，而是拿着**真档**继续跑
   （实测：`--live E:/xz/tmp/nope/remote/save` 下 `gd_restore.py` 照样打印出真实存档的指纹）。
   若非当时加的是 `--dry-run`，就写到真档上了。
   ⇒ **凡是「外部传入的路径」，都要先确认它真的存在且形态正确，再继续**；
   `gd_restore.py` 现在硬校验 `--live/main` 是否存在，不存在直接拒。
   **通用规律**：**回落（fallback）只有在「回落是安全的」时候才可以静默。**

52. ★★★ **星座（虔诚）必须用「真实 DPS」评价 —— 加成 % 加和会给出反号结论**
   （2026-09-20 lv73 重出版实测）。

   `gd/devotion.py::select_coherent()` 的评分是 **各伤害类型加成 % 的加权和**，
   而真实伤害只吃**你这套 build 实际打出去的伤害类型**，还受平伤 / 攻速 / 独立倍率影响。

   实测（Sam lv73，狼人+夜刃穿刺流血）：

   | 方案 | 加成 % 加和 | **真实 DPS（含减抗）** |
   |---|---|---|
   | 现有 32 星点 | 565% | **155,394** |
   | `select_coherent` 建议（33 点） | **1429%** | **139,701（−10.1%）** |
   | 现有 + 「狐狸」4 星 | 655% | **163,004（+4.9%）** |

   ⇒ **建议方案加成 % 是现状的 2.5 倍，真实伤害却低 10%**（加成摊到了不用的类型上）。
   **别照报告 §6.2 的「星座建议」重排。**

   **正确做法**：用 `tools/tune_devotion.py`（真实 `final_report` 目标）：
   ```bash
   $PY tools/tune_devotion.py          # 从现状出发贪心加点
   $PY tools/tune_devotion.py --rank   # 单星座增量排行榜（含「需亲和力」）
   ```

   **★ 同源问题：星座的「通道不一致」。**
   `final_report(devotion_levels=…)` 是**完整**通道（分别进 pct / 平伤 / DoT / 攻速 / 独立倍率），
   但**只有 `gd/mana.py` 在传**；`gd/dps.py`（存档实测）与 `tools/plan_dps.py`（方案评估）
   都**不传**，而星座记录因为躺在 `levels` 里，被当成「技能 %」算了 ——
   结果是**少算 1.9%**（漏掉平伤/攻速/独立倍率）。两者都自洽，但**改星座只能在完整通道下比**。
   ⚠ **同时传 `levels`(含星座) + `devotion_levels` 会重复计入**（实测 ③>①），
   完整通道必须先**把 `/devotion/` 从 `levels` 里摘掉**。

53. ★★★ **注入加点会把武器池「撑爆」⇒ 主输出直接归零（自动加点器会产出自毁方案）**
   （2026-09-20 lv73 实测）。

   `tools/make_alloc.py` 生成的方案（合法 206 点 / 按池 245 点）在真实模型下：

   | 加点 | 含减抗 DPS | 相对存档现状 |
   |---|---|---|
   | **存档现状 243 点** | **152,438** | — |
   | 生成·合法 206 点 | 75,818 | **−50.3%** |
   | 生成·按 245 点池 | 99,505 | −34.7% |

   **根因**：生成器把 8+8+8 = **24 点投进夜刃武器池 `playerclass04/wpattack1/2/3`**，
   于是武器池由「2 条装备授予的 WPS，Σ权重 **45**」变成「5 条，Σ权重 **111**」：

   ```
   默认攻击权重 = max(0, 100 − ΣW) = 0    ⇒ 左键默认攻击（野性利爪）一次都不出现
   ```

   ⇒ `skillChanceWeight` 是**权重不是百分比**、且**从默认攻击的 100 里扣** ——
   **WPS 投多了会把主输出挤没**。生成器只看技能优先级，不知道这件事。

   **判据（加任何加点方案前必查）**：看 `rep['rotation']` 的
   `weight_total` 与 `default_weight` —— **`default_weight == 0` 就意味着主输出没了**。
   `tools/tune_skills.py` 的候选池**排除** `/itemskills` 且逐次报告这两个值。

54. ★★ **`planreport` 的星座建议用「等级上限」而非「存档实况」**（2026-09-20 已修）。
   `_bud2 = devotion_budget(level)` ⇒ lv73 → **55**，而角色实际只解锁 **36** 点
   （`total_devotion_points` = 已投 32 + 未投 4）⇒ 报告给出**点不出来**的 51 点方案。
   同理 §五 技能点只报「预算 206 / 已投 243」，看不到**未分配 2** 与**池子 245**。
   **修法**：`earned_points(char)` 直接读存档 block2 的
   `devotion_points / total_devotion_points / skill_points / attribute_points`，
   预算取 `min(等级上限, 已解锁)`，并如实标注「差的 N 点要去打神龛 / 非标准来源」。
   见 `gd/planreport.py::earned_points`。

55. ★★ **「抗性溢出」不是 bug，也不是可捡的收益 —— 别朝这个方向优化**（2026-09-20）。
   满抗是**逐维硬约束**（上排 130 / 下排 105），优化器的目标函数**对超出部分没有惩罚**，
   于是报告里会看到一大坨溢出（Sam lv73 实测 **255 点**）。但拆开来源后**两条硬事实**：

   ① **溢出的抗性常常是「搭便车」来的**。Sam 的 255 点里，元素三抗 +188 中的 **+108**
      来自四条词缀 —— 前缀「**雷击的**」×2（元素三抗 +30 / 虚化 +22，**附带 OA +29、电伤% +30**）
      与后缀「**奥术平衡之**」×2（元素三抗 +24 / 流血 +24，**附带 元素伤害% +32**）。
      它们本来就是**伤害最优解** ⇒ 换掉只会掉输出。
   ② **字段粒度决定溢出下限**。`defensiveElementalResistance` 是**一条字段同时给火/冰/电**，
      ⇒ 只把**最弱那项**顶到 130，另两项必然同高，**物理上无法精调**。
      下排四维都是单项字段（`defensiveLife` / `defensiveChaos` …）⇒ 实测能卡到 **活力 105/105、混乱 106/105**。

   **代价量化**（同口径 `--extreme --procs 24 --anneal-iters 400`，只换 `--goal`）：

   | 目标 | 真实 DPS | 抗性 |
   |---|---|---|
   | 满抗 + 追高伤害 | **152,438** | 9 项全满 |
   | 只要伤害（`--goal dmg`） | **185,647（+21.8%）** | 全破（虚化 **0/105**、穿刺 35/130、混乱 54/105）|

   ⇒ **满抗值 21.8% 伤害**，而溢出是这 21.8% 的「找零」。
   **溢出在游戏里 100% 无效**（80% 是硬上限、不转化），但**消灭它的边际收益为负**。

   **要看的其实是**：哪些维度**卡在线上**（余量 ≈0）—— 那才是脆点。查法：
   ```bash
   $PY tools/res_audit.py Sam [--plan X.json]     # 或看报告里的「抗性来源分解」段
   ```
   口径真源 = `gd/resaudit.py`（`planreport` §二 与 `plan_audit` 共用；自检 `[32]` 守它）。

56. ★★★ **边际值 = 0 的技能/星座，多半是「模型盲区」，不是「白投的点」**（2026-09-20 实测）。

   `tools/tune_skills.py --mode marginal` / `tune_devotion.py --marginal` 会给出
   「拆掉损失 0」的项，**逐条核对字段**再决定，否则会拆掉游戏里真正有用的东西。

   | 项 | 模型边际 | 真实字段 | 判读 |
   |---|---|---|---|
   | 技能 **血莽**（`werewolf3`，12 级） | **0** | 只有 `offensiveSlowDefensiveAbilityMin 30→110` + 时长 3s | **降敌方 DA** ⇒ 模型不算敌方 DA 削减 ⇒ **盲区，不能拆** |
   | 星座 **乌龟**（4 星） | **0** | 护甲 / 伤害吸收 | DEF 向 ⇒ 盲区 |
   | 星座 **海鳗**（1 星） | **0** | DA | 同上 |
   | 星座 **抉择之地**（1 星） | **0** | 三围 | 同上 |
   | 星座 **铁锤**（3 星） | 177（−0.16%） | 防御向 | 同上 |

   ⇒ **两条口径**：
   - 「**不花钱**」的结论（加什么）可以直接用边际表；
   - 「**拆点**」的结论必须过一遍字段 —— **盲区项要标成「用防御换伤害」**，
     由用户决定，而不是报告成「白投的点」。

   同族：模型还看不见**护甲 / DA / 吸收 / 生命**，所以任何「防御换伤害」方案都要显式声明代价
   （本轮星座 +9.6% 就是退掉 4 颗防御向星座换来的）。

57. ★★ **1-1 换位搜索必须排除「同一个技能既拆又加」**（2026-09-20 实测假收敛）。

   `tools/tune_skills.py --mode swap` 的两个候选表**会重叠**：
   未到 `max_level` 且可拆点的技能**同时**出现在 `add` 与 `rem` 里。
   旧版没排除 `ra == rr`，于是出现过
   `★ 换位：−1 血源苏醒 ／ +1 血源苏醒 ｜ 净 4` —— **自我换位**，
   每轮报「净 4」永不收敛，收敛判据被绕过、项数也会虚增。

   **修法**：配对时 `if ra == rr: continue`。**断言口径**：
   换位搜索的收敛条件是「最优 1-1 换位净收益 ≤ 0.5」，
   而**净收益必须来自两个不同技能**（`net = gain(B) − loss(A)`，`A ≠ B`）。

58. ★★★ **优化器的目标函数必须与搜索阶段「同源」—— 差一个 `rr=` 就偏 47%**（2026-09-20）。

   `tune_skills` / `eval_build_variants` 早期版本调 `final_report()` 时**没传 `rr=`**
   （减抗包）与 `enemy_armor=` ⇒ 算的是「**无减抗**」口径，而装备搜索
   （`tools/plan_dps.py::dps_of`）走的是**含减抗**口径。
   实测同一套主穿刺装备的**穿刺桶**：

   | 路径 | 穿刺桶 |
   |---|---|
   | `dps_of`（管线，带 RR） | **112,233** |
   | `final_report` 不带 `rr=` | **76,090** |

   ⇒ 差 **47%**。后果不是「数字不准」而是**决策错**：技能/星座是在错误的目标上挑的。

   **修法**（已落在三个工具里）：setup 阶段统一算好 `RRKW = dict(rr=…, enemy=…,
   enemy_armor=…, enemy_armor_src=…)`，所有 `final_report()` 调用 `**RRKW`。
   ⚠ 传的时候**别再显式传 `enemy=`**（会 `got multiple values for keyword argument`）。

   **通用判据**：写任何「优化 / 评分 / 对比」脚本前，先确认它和**对手方**用的是
   同一套口径（敌方档、减抗、护甲、星座通道、proj 假设）——不然比较的是两个不同的量。

59. ★★ **`gd auto --goal fullres` 的语义是「只求满抗、完全不优化伤害」**（2026-09-20 实测）。

   跑一次 12 形态 / 单形态搜索时误传 `--goal fullres`，日志一路正常、还打出了
   「结果 ｜ 覆盖 1070/1070」，但 **`评估次数` 只有 1、总耗时 5 s** ⇒ 其实什么都没搜。

   * `--goal` **默认 `super`**（= 满抗 + 真实 DPS 微调），**想优化伤害就别传**。
   * `fullres` = 只解「9 项抗性封顶」这个约束，出解即止。
   * `dmg` = 不保抗性只追伤害（做「抗性税」对照用）。

   **判据**：日志里 `目标 %s` 那行（`GOAL_ZH[args.goal]`）+ `评估次数`。
   **`评估次数` 是个位数 ⇒ 大概率是目标选错了**，不是「已经最优」。

60. ★★ **形态的 `damage_weights` 只管「满抗求解」阶段，管不到 LNS 搜索**（2026-09-20）。

   想做「主穿刺」时，只在 `data/archetypes.json` 里把 `pierce` 拉到 3.0 / `bleed` 压到 0.05
   **是不够的**：`damage_weights` 覆盖的是 `gd/opt.py` 的 `W_DMG`（**约束求解**的评分），
   而 `--goal super` 的局部搜索 / LNS 用的是 `tools/autobuild.py::make_real_dps` 的
   `plan_dps.dps_of(...)["dps"]` —— **合计 DPS**。结果就是：
   形态声明「主穿刺」、搜索却按合计优化 ⇒ 实测两次都是 **0 改进**。

   **修法**：`tools/objfunc.py` + `GD_OBJ=pierce` —— 把 LNS 的目标换成**穿刺桶**
   （`rep['type_rows']` 里 `type=='pierce'` 的 `dps_vs`）。
   换成桶目标后同一套装备立刻找到 **+27.2%** 的解（隐式通道）。

   **同理**：星座 / 技能也要用 `--objective pierce`，否则会出现
   「装备按穿刺选、技能按合计选」的四不像。

   **★ 通道效应**：`dps_of`（星座混在 `levels`）与「去 `levels` + 传 `devotion_levels`」
   两条通道的**差值不是常数**（同一套装备上测到 4.7%，换装后变成 15%）。
   ⇒ **收益（Δ）不能跨通道比**；报告里必须写明用的是哪条通道。

   **★ 减抗三族口径（2026-09-20 修正 —— 旧版三处偏差）**
     - ★ 三族**按文案措辞**分，不按数值大小：**B** = 技能上的负 `defensive*`
       （`−n% 类型抗性`）⇒ **叠加**；**C** = `…ResistanceReductionPercent`（带 %）
       ⇒ **取最高**；**A** = `…ResistanceReductionAbsolute`（无 %）⇒ **取最高**。
       **结算顺序 B → C → A**。详见 `docs/rr_mechanics.md`（含官方论坛出处）。
     - ★★ **敌抗可以减成负数、按 1:1 放大，不锁 0**。别信第三方计算器
       「最低 −100%」那句 —— 那是它的自设假设，不是官方规则（社区实测见到 −118%
       ⇒ 2.18× 伤害）。本库 `rr_floor()` 默认 **−100%（保守）**，`GD_RR_FLOOR` 可放开。
     - ★ **C 类对负抗性是「加深」不是「负收益」**：`r < 0` 用 `r×(1+c/100)`。
       旧版无条件 `×(1−c/100)` 会把负值**拉回 0**。官方论坛原例可对拍：
       `10 − 18 − 46% = −54%`，再套 C 类 30% ⇒ `−54 × 1.30 = −70.2%`。
     - ★ **A 族不能并进 B 族求和**：A 是「取最高」，求和会高估。旧版把
       `…ResistanceReductionAbsolute` 当 `add` 族，两件 A 类装备会算成叠加。
     - ★ 影响面：**A/C 族只在装备侧出现**（`skills_json.js` 里两族皆 **0** 处）⇒
       角色**基座**（存档技能+星座）给的减抗全是 B 族，所以锚点
       `gd dps Sam` **不漂移**（101,976 / 152,476）。**但优化器挑出来的方案会吃 A/C 装备**
       —— 逐件普查：12,717 件里 **C 族 28 件 / A 族 31 件**（两族都有 2 件）
       ⇒ 已实测 B 口径整段重排（`werewolf` 之外 −13.6% ~ +12.1%）。
       ⚠ 别把 `itemdb.js` 里的**原始串计数**（C 90 / A 189）当件数（见 `rr_mechanics.md §三`）。
     - 每处偏差在 `tools/selftest.py` [16] 组各有断言（含官方论坛例的逐位对拍）。

   **★ 技能点分配：core_skills 是手写清单、会漏（2026-09-20）**
     - ★★ **`gd/alloc.allocate` 的贪心是「core 吃满预算再轮 rest」**，所以
       **漏进 `archetypes.json::core_skills` 的技能永远点不到**（`rest` 只是
       「core 吃不满预算时」的填充；实测 12 个形态里只有 `fangs` 真吃到 rest）。
     - ★ 实例：`avalanche` 的 `core_skills` 漏了 class10 的「血牙」(`wpattack01`)
       与 class04 的「死亡旋风」(`wpattack3`) ⇒ 补上后 **65,697 → 79,034（+20.3%）**，
       且**零附带损伤**（其余 11 个形态的加点逐位不变）。
     - ★★ **别用「两池合表」去修「rest 饿死」** —— 实测**净劣化**：
       合表会把优先级 4/5 的**攻击技能**整体提到优先级 6 的**被动**之前，
       而被动是乘全区 ⇒ `human` **77,920 → 71,925（−7.7%）**。
       同一改动在 `avalanche` 上是 +20.3%（它挤掉的是低收益 buff），
       在 `human` 上是 −7.7%（它挤掉的是被动）⇒ **不能一刀切，要按形态补数据**。
       完整实测表记在 `gd/alloc.py` 的注释里，`WPS_OPT_OUT` 记「故意不带」的形态。
     - ★ 护栏：`gd/alloc.reachable_wps()` + 自检断言「core_skills 覆盖全部可点 WPS」
       （变形形态自动跳过 `_FORM_LOCKED` 的技能；豁免表在 `alloc.WPS_OPT_OUT`）。

   **★ 路径坑：`/tmp/...` 在 Windows Python 眼里不是那个 `/tmp`（2026-09-20 实测）**
     - ★★★ 在 Git Bash 里 `GD_SKILL_JSON=/tmp/alloc_old/alloc71_werewolf.json` 传下去，
       Windows 的 Python 会解析成 **`C:\tmp\...`**（或直接不存在）⇒ `os.path.exists()` 为
       False ⇒ 下游**静默回落真实存档**，你看到的是 A 口径数字却以为在看 B 口径。
       实测症状：**同一个文件、同一命令，只因路径前缀不同，DPS 差 20–45%**，
       而且连续三次跑结果完全一致（不是随机性），极具欺骗性。
     - ⇒ 对照实验的文件**一律放在仓库内的相对路径**（如 `data/scratch/_alloc_prev/`），
       或传 `C:/...` 形式的绝对路径。**先 `os.path.exists()` 验一遍再比数字。**
     - 同类：`make_alloc.py --out /tmp/x.json` 会被当成相对路径落到
       `SKILL/_tmp...`，因为 `pathlib.Path('/tmp/x.json').is_absolute()` 在 Windows 上是 **False**。

   **★ 敌方档的 `difficulty` 可能是字符串 —— 裸 `int()` 会让整条链路崩（2026-09-20 修）**
     - ★★★ 五档档位（`none` / `elite` / `boss` / `high` / `max`）的 profile 里
       `difficulty` 写的是 **`"n/a"`**（`gd/enemy.py:518` —— 这些是「一类怪」，
       本来就没有单一难度），而 `gd/rotation.py::final_report` 曾写
       `int((_ep or {}).get('difficulty') or 3)` ⇒ `"n/a"` 是真值、`or 3` 不生效 ⇒
       **`ValueError: invalid literal for int() with base 10: 'n/a'`**。
       症状：`plan_audit --enemy elite/boss/high/max` 直接崩（只有 `m<id>` 和默认池能跑）。
     - ⇒ 修法是**容错 cast**：能 `int()` 就转，转不动就**原样透传**给
       `gd/enemy.py::adj_index` —— 它本就接受字符串难度（自己查 `DIFFICULTIES` 表，
       查不到退回 3）。**别在调用点重新实现难度映射**，那是 `enemy.py` 的职责。
     - 护栏：自检 `[24]` 三条 —— ①五档 `difficulty` 含 `n/a`；②七种敌方档
       （五档 + `m3955` + `pool:`）都能过护甲吸收段；③**源码级**断言
       `gd/rotation.py` 里不许再出现那句裸 `int(...)`。

61. ★★★ **武器池技能（WPS）的「武器约束」模型完全没判 —— Sam 的「混乱打击」22% 是虚的**（2026-09-20 **已修**）。

   `gd/procs.py::wps_of_item()` 只判「是不是 `Skill_WPAttack_*` + `skillChanceWeight > 0`」，
   **不判这个 WPS 在游戏里要求的武器** ⇒ 不合法的 WPS 照样进池、照样占权重。

   **实测（Sam，lv73，双持 `d302_sword` + `c018_axe`）**：池里排第二的
   **混乱打击**（面板 22,303 ／ 22%）是**盾牌战技** ——
   * `data/item_skills.json['sk3683']['Shield'] == 1`；
   * 官方中文描述：「以防守姿态向敌人发动突击，令敌人晕头转向。**盾牌战技**，由默认武器攻击触发。」；
   * 授予者 = 勋章 `it14485`（**猩红连队烙印**，k=65）→
     `records/skills/itemskillsgdx3/legendary/disorientingstrike.dbr`。

   双持**没有盾** ⇒ 游戏里它根本不会出现在武器池。

   **判据可交叉验证**：全库 `Shield: 1` 且模板为 `Skill_WPAttack_BasicAttack` 的**只有 2 条** ——
   `sk3699` **碎岩猛击**（士兵盾击）与 `sk3683` **混乱打击**；带同一字段的还有
   力场波 `sk1135`、曼海尔神盾 `sk2609`、女武神 `sk998–1003`、曼海尔的壁垒 `sk1139`
   ⇒ 该字段就是「**需要盾牌**」。

   **量化（只读实验：monkey patch `procs.wps_pool` 剔除该条）**：

   | 口径 | 面板合计 | 野性利爪 |
   |---|---|---|
   | 现状（含混乱打击，w=20） | **102,361** | 36,791（36%） |
   | 剔除后（w=20 回到**默认攻击**） | **90,844**（−11.3%） | 47,577（52%） |

   **相邻的 `dualWieldOnly`（全库 17 条）同样没判**：`sk519` **毁伤**
   （圣物 `it1516` 刀剑大师护符授予，w=25）在 Sam 双持下**合法**（剔除只 −1.3%）；
   但**双手 build 的 WPS 占比会偏高**（`sk325` 恐狼之爪、夜刃 `sk1237/1238/1239/1240`
   全是 `dualWieldOnly = 1`）。

   **修法（2026-09-20 已实施，用户批准）** —— `gd/procs.py` 新增三个件：
   · **`weapon_state(gids)`**：从 `base_gids` 的 `item_class` 推武器构成
     （盾 = `WeaponArmor_Shield`；单手 = `WeaponMelee_*` / `Ranged1h`；双手 = `*2h`）。
     ★ **看不到武器 ⇒ 返回 `None`（不判）** —— 调用方常常只给 12 个装备槽的方案，
     此时若按「无双持」处理，会把 `dualWieldOnly` 的 WPS **整批误杀**。
   · **`weapon_verdict(entry, st)`**：`Shield:1` ⇒ 必须持盾；`dualWieldOnly:1` ⇒ 必须双持，
     **但带 `dualRangedOnly` / `dualRangedOrAllRangedOnly` / `dualRangedOrRanged2hOnly`
     的是 OR 语义 ⇒ 放行**（实例：`sk3625` 战争兵器 = 双持远近皆可）。
   · **`wps_pool(gids, …, st=None)`** 默认自动推断并**过滤**；`st=False` 退回旧口径（零漂移）。
     **`wps_gap(…, st=None)`** 同步过闸门 —— 否则闸门把非法项挡在池外后，
     **体检器会把它们误报成「缺口」**（实测 missing=1 / 权重损失 20.0）。
   自检新增 **3 条**（闸门生效＋判语＋无武器时不误杀）。
   ⚠ **仍未做**（已登记）：武器类型白名单（`Sword` / `Axe2h` / … 顶层键）与
   **专精自身** WPS（`levels` 通道，如夜刃 `sk1237~1240`）的门控 ——
   Sam 双持不受影响，换成双手 build 时才需要。

   **排查口径**：`$PY -m gd dps Sam` 输出里「每秒几次 ÷ 攻速」= 该技能在池里的权重；
   再逐件跑 `gd.procs.wps_of_item(db, gear.load_items(), <gid>)` 看 `weight` / `rec` / `name`。

   **★ 同期量化（关联 #56 的「降敌 DA 盲区」）**：模型算 PTH 时用的是**未削弱的敌方 DA**
   ⇒ **暴击乘区被系统性低估**。Sam 实测（`pool:3:champion+hero` lv73，敌 DA **1646.7**）：
   血莽 12 级 `offensiveSlowDefensiveAbilityMin` = **−250**（3 s，靠攻击刷新，aps 3.43 ⇒ 常驻）、
   刺骨战吼 12 级 `characterDefensiveAbility` = **−124**（持续 12 s ／ 冷却 6 s ⇒ **常驻**）：

   | 口径 | 敌方 DA | PTH | 暴击率 | 命中期望倍率 |
   |---|---|---|---|---|
   | 模型现状（不削 DA） | 1646.7 | 95.96 | 6.96% | 1.0272 |
   | 扣 血莽 | 1396.7 | 103.94 | 14.37% | 1.1394（**+10.9%**） |
   | 扣 两者 | 1272.7 | 107.95 | 17.56% | **1.1739（+14.3%）** |

   ⇒ 想让 PTH 从 95.96 到 107.95，**「削敌 DA」零成本**（技能已点满），
   而靠自身 OA 需要 **≈ 2240（+22%）**。**这是「暴击为什么值得上」的正解**。
   **2026-09-20 已接入**：`gd/rotation.py::enemy_da_cut(skills, levels)`，判两族 ——
   `offensiveSlowDefensiveAbilityMin`（血莽 250）与 `characterDefensiveAbility` **负值**
   （刺骨战吼 −124；**正值是自身 DA 加成，不计** —— 如阿玛托克契约 +20…260）；
   有冷却的按 `min(1, 持续/冷却)` 折覆盖率（刺骨战吼 12 s ／ 6 s ⇒ 100%）。
   `rep` 新增 `enemy_da_cut` / `enemy_da_effective` / `enemy_da_cut_rows`；
   自检 2 条守卫（374 的取值 + 正值不计）。**实测面板 90,844 / 实战 106,646.8**，
   与上表手算逐位吻合。

---

62. ★★ **`gd auto` 的「自动带武器重跑」会被父进程自己的锁挡住 —— 报一个不存在的 PID**（2026-09-21）。

**症状**：不带 `--with-weapon` 跑 `gd auto`，当 12 个装备槽搜不出满抗时，程序打印
`⚠ 12 槽下不满抗 → 自动带武器重跑`，紧接着**拒绝启动自己**：

```
✗ 已有另一个 autobuild 在写同一个输出文件，拒绝启动。
    PID 40564 ｜ 角色 Sam ｜ 输出 data/plans/pivot/q_pierce.json ｜ 启动 2026-09-21 10:40:09
```

**但那个 PID 是假的** —— `tasklist /FI "IMAGENAME eq python.exe"` 查无此进程，
`--out` 目录里也**没有** `.lock` 文件。

**原因**：重跑走的是「重新 spawn 自己」，而**父进程的互斥锁尚未释放**；
子进程按 `--out` 判冲突 ⇒ 判到了自己人。

**规避**：直接加 `--with-weapon`（「一次到位」），或直接用 `--extreme`（自带含武器）
⇒ 不触发重跑路径。**别**照提示去 `taskkill` 那个 PID，也别用 `--force` 抢
（真抢的是自己的输出文件）。

**判据**：`tasklist` 查无此 PID **且** 目录无 `.lock` ⇒ **自锁**，不是真并发。

63. ★★ **`GD_OBJ` 指到「空桶」会静默退化成「合计」—— 目标落空看起来像成功**（2026-09-21）。

**症状**：用 `GD_OBJ=physical` / `GD_OBJ=vitality` 跑搜索，横幅**明明**写着
`★ 评分目标 = **物理桶 DPS**`，但最终「真实 DPS」与 `GD_OBJ=total` **逐位相同**
（178,003 = 178,003）。

**原因**：`objfunc.score()` 在 `bucket(rep, base)` 返回 `None`
（该伤害桶在本 build 里**没有任何来源**）时**回退到 `rep['dps']`**。
这是为「零漂移」刻意保留的旧行为，但副作用是：**目标选错**会伪装成**结果一致**。

**规避**：① 先确认桶存在 —— 看日志的「**源伤害构成**」或 `plan_dps --json` 的 `type_rows`；
② 跑完与 `GD_OBJ=total` 对一次，**完全相同 ⇒ 目标落空**，别当结论用。

**实例**：Sam 狼人形态下 `physical`（被武器「138% 物理→穿刺」搬空）与 `vitality`
两桶为空 ⇒ 这两类**在这条线上无法作主轴**（这本身就是有效结论）。

★ 同批改动：`GD_OBJ` 从「只支持穿刺」扩成 **任意伤害桶**
（`tools/objfunc.py`：`total` + 17 个桶 + 各桶的 `+` 变体，共 35 个合法值），
`total` / `pierce` / `pierce+` 的输出**逐位不变**。

64. ★★ **「默认攻击替换型」形态的 ΣW 陷阱 —— `make_alloc` 默认贪心必炸，修法是手工加点**（2026-09-21）。

**症状**：`avalanche`（人形态，猛袭 + 雪崩）用 `make_alloc.py` 的默认贪心生成加点后，
面板只剩 **61,096**，主输出「猛袭」**一次都不出现**。

**根因**：`skillChanceWeight` 是**权重**，从默认攻击的 100 里扣，`ΣW ≥ 100 ⇒ 默认攻击权重 0`。
贪心给双持形态点了 3 条夜刃 WPS（8 级 = 22 ×3）+ 雪崩（10 级 = 26）+ 血牙（10 级 = 26）
⇒ **ΣW = 118 > 100**。猛袭（170% 武器伤害的默认攻击）被自己挤没。

**判据**：`rep['rotation']['default_weight'] == 0` ⇒ 默认攻击已消失，任何结论都不可用。

**修法（★ 唯一可行）**：**手工指定加点**，别用默认贪心 ——
`$PY tools/make_alloc.py <arch> <lv> --set "猛袭:16,雪崩:10"` 只加指定项（**不贪心填充**），
或直接写 alloc JSON 交给 `GD_SKILL_JSON`。
实测人形态 ΣW=63（雪崩 26 + 毁伤 25 + 恐狼之爪 12）⇒ 默认攻击权重 37，循环正常。

⚠ **与 #53 的关系**：#53 记的是「B 口径自动加点器」把点塞进 `wpattack*`；
本条是**同一个根因的第二个入口**（默认贪心模板）。**两条都要防**。

**参考值**：权重表在 `data/calc_mastery_skills.json`（逐级列表，取「已点等级」那一项）——
`class10/wpattack01|02` 10 级 = 26；`class04/wpattack1|2|3|5` 8 级 = 22。

65. ★★ **产物名 ≠ 消费方默认路径 ⇒ 静默吃旧文件；且调优产物不能直接当 `--alloc`**（2026-09-21）。

这是**一对姊妹坑**，都在「调优产物 → 下游消费」这条链上。

**坑 A：`tune_skills` / `tune_devotion` 的输出不是纯加点，喂 `--alloc` 会崩**

它们的产物是**元数据 + 结果**的混合体，顶部有 `mode` / `arch` / `objective` / `base` / `opt` 等键
（`tune_skills` 的最终加点在 **`skills`** 键里，`tune_devotion` 在 **`records`** 键里）。

直接把它当 `--alloc` 传给 `plan_cycle.py` ⇒ `gd/dps.py::load_char` 会把 `'swap'` 当技能等级解析：

```
ValueError: invalid literal for int() with base 10: 'swap'
```

**修法**：抽成**纯 `{record: level}`** 再喂 ——

```python
d = json.load(open('data/scratch/skill_opt.json'))
alloc = {r: int(lv) for r, lv in d['skills'] if int(lv)}   # 只留非 0
```

**坑 B：`eval_build_variants` 的默认路径与实际落盘名不一致 ⇒ 静默吃到旧文件**

工具头注释与代码默认读的是 `data/scratch/skill_swap.json` / `devotion_opt.json`，
而 `tune_skills --mode swap` 实际落在 **`skill_opt.json`**、`tune_devotion --realloc` 落在
**`devotion_realloc.json`**。后果：**不报错**，但用的是**上次的旧方案**；
更坑的是输出标题**硬编码**成 `技能源 skill_swap.json` ⇒ 连"用了哪个文件"都看不出来。

**修法**：显式传环境变量 ——

```bash
PLAN=… SKILL_SRC=data/scratch/skill_opt.json DEV_SRC=data/scratch/devotion_realloc.json \
  $PY tools/eval_build_variants.py
```

**判据**：跑完比一下 `base` / `opt` 数字是否随新方案变化；**没变就是吃到了旧文件**。

> **同类风险**：任何「工具 A 落盘 → 工具 B 按固定名读取」的链路（`rerun_final_docs` 的 `JOBS` 是
> **显式登记表**，所以没有这个问题；**没登记的临时链路就有**）。

66. ★★ **技能字段散在四个表里 —— 单扫一张既会漏来源，又会把专精/星座误判成装备**（2026-09-21）。

问「某类技能字段（吸收/减抗/召唤…）全库有多少来源」时，**没有一张表是全的**：

| 表 | 有 | 缺 |
|---|---|---|
| `data/skills.json`（12372 条） | 记录路径 / class / **name** / **tag** / template | `stats` **被裁剪过** —— 不含 `damageAbsorptionPercent` 这类修正字段 |
| `data/calc_mastery_skills.json` | 专精 + **51/63** 个星座授予技能的实际数值 | 缺 12 个星座技能；**无物品技能** |
| `data/devotions.json`（716 星位） | 星位**被动**属性（有 `stats`） | **不含授予技能记录**（键是 `tier2_10a`，不是 `..._skill`） |
| `data/item_skills.json`（5156 条） | 物品技能的实际数值 | 键是 `skXXXX`；**混装了专精/星座技能的同 ID 副本** |

**实测后果（第一版就是这么错的）**：只扫 `calc_mastery_skills.json` ⇒
① 星座来源只数到 **1** 个（实际 4 个）；② **全部物品来源漏掉**；
反过来只扫 `item_skills.json` ⇒ 把 `sk2144`（折磨印记）、`sk3627`（不羁狂怒）当成**装备**。

**四条修法（`tools/absorb_audit.py` 已实现）**：

1. **并集扫**四张表，按 `layer` 分开（星座 / 专精 / 装备）。
2. **专精去重**：`db.mastery` 就是**专精技能 ID 全集**（dict，键 `skXXXX`，值含 `isMastery`/`name` 的 tag）
   ⇒ `sk in db.mastery` 的直接剔出装备层。
3. **星座技能去重**：`db.mastery` 管不到星座 —— 用 **tag→记录 索引**（`data/skills.json` 的 `tag` 字段）
   反查记录路径，**只有路径含 `/itemskills` 的才算物品来源**。
4. **中文名解析**：专精名从 `isMastery` 节点的 tag（`tag…Class04SkillName00`）经 `db.text()` 反查
   （⇒ 夜刃/狂战士…）；**星座名**由授予技能的 tag 把 `DevotionEffect` 换成 `Devotion_` 反查
   （`tier1_29e_skill` 的 `tagDevotionEffectA29` → `tagDevotion_A29` = 乌龟）。

**另外三条口径**（写代码前必须先定清）：
* `Skill_Modifier`（template `t88`）**自己不写目标技能** —— 目标在**物品**上，
  靠 `modifierSkillName<N>` ↔ `modifiedSkillName<N>` **成对**出现（后者是 tag）；
  且这两个字段可能在**嵌套结构**里 ⇒ 归属索引必须**深度遍历**，`modifiedSkillName<N>` 回**根记录**取。
* 归属索引**不能只扫 `db.items`** —— 词缀 / 套装 / 消耗品也可能授予技能，要一并扫
  （`items` / `prefixes` / `suffixes` / `sets` / `mis` / `quest_items` / `interaction_items`）。
* `itemdb_diff.js`（`data/cache/`）是**版本 diff**（`added`/`deleted`/`updated` 结构），
  **不是完整记录** ⇒ 查当前值别读它，会被 `added:{...}` 里的"新值"骗到。

**入口**：`tools/absorb_audit.py`（`--write` 落盘 / `--check` 比对 / `--percent` 只看乘算层 /
`--show-dropped` 看剔除明细），产物 `data/absorption_sources.json` + `.md`，
自检 `tools/selftest.py` `[54]` **15 项**守卫。

**★ 追加两条（同一次踩到，写工具时才发现）**：

**追加 A：有一批 `Skill_Modifier` 根本不在 `DB` 暴露的任何集合里 ⇒ 必须回原始文本兜底。**
实测 15 条「+X% 吸收 → 某技能」里有 **7 条目标解析不出来**。原因是它们挂在 **`aa*` 记录**上，
而 `aa*` 在 `db.items` / `prefixes` / `suffixes` / `sets` / `mis` / `quest_items` / `interaction_items`
里**全都查不到**。只能在 `data/cache/itemdb.js` 里按字段名成对抓：

```
modifiedSkillName1:"tagClass01SkillName08A",modifierSkillName1:"sk4656"   ← 目标在 modifier 前一格
```

★ 而这些 `aa*` 记录的 `l` 是 **`LootRandomizer`** ⇒ 它们不是"某件装备"给的，
而是**随机词缀池**给的（绿装/掉落的词缀）。归属标注成「随机词缀池」才对。

**追加 B：回溯窗口取配对字段必须用 `findall(...)[-1]`，不能用 `re.search`。**
`re.search` 返回窗口里**第一处**匹配，而 900 字符的窗口里往往裹着**前一条记录**的同名字段
⇒ 目标是**张冠李戴**的：`sk4656` 实际加在「曼海尔的意志」，用 `re.search` 会解成「节奏打击」。
**这种错不报错、也过自检**（自检只验"有值"，不验"值对不对"）——
所以自检里加了一条**「修正器目标全部解析成功」**（凡出现「未解析」即失败），
外加 `--check` 现在**也比对行内容**（原先只比对 `facts`，行级错误会漏过）。

---

67. ★★ **星点 `devotion_level` 的**两处**写入都缺 —— 「档里点了、游戏里不亮」**（2026-09-21 **已修**）。

**症状**：用 `gd.save.skill apply` 落一份星座重排，存档里新星点写成 `level=1` 但
**`devotion_level=0`** ⇒ 游戏读档后**星星不亮**（等于没点）。

**根因**：`gd/save/write.py::blank_skill()` 恒写 `devotion_level: 0`
（这对**星座授予技能** `*_skill.dbr` 是对的 —— 它的 `level` 才是技能等级）；
但**星点**（`tierN_XXy.dbr`）的 `devotion_level` 必须是 1 ——
**游戏判定星点是否点亮看的就是它**。这与 `skill.py` 撤点分支
（只清 `level` 不够、必须同时清 `devotion_level`）**是同一个字段的两面**。

**修法**（三处一起改）：
1. 新增 `gd/save/write.py::is_star(rec)`：`/devotion/` 且**不以 `_skill.dbr` 结尾**；
2. `blank_skill()` → 星点写 `devotion_level = level`；
3. `gd/save/skill.py::apply_plan()` 的**重新点亮**分支 → 星点把 `devotion_level` 顶回 `lv`。

**★ 必须同时登记 `sync_live.py::PAIRS`**：`gd/save/write.py`（源 `gd_write.py`）与
`gd/save/skill.py`（源 `gd_skill.py`）**都在迁移的 `FILES` 里** ⇒ 不登记的话，
**重跑迁移会把修复静默打回**（陷阱 #23）。本轮已补 2 对（现共 **24 对**）。

**验证判据**：落档后 `Σ(点亮的星点)` == 目标点数，且
`devotion_level != level` 的星点条目数 == **0**（本次 34 点 / 0 条 ✓）。

---

**附 67.1 · 断言绑了「存档装备状态」⇒ 落档后假红**（同类问题，同日修）

- `★ 武器闸门：Sam 双持 ⇒ 剔除无法触发的 WPS` 的旧判据要求
  `len(关闸门池) > len(开闸门池)` ⇒ **依赖「当前装备里正好有授予盾牌战技的件」**
  （旧勋章「猩红连队烙印」给「混乱打击」）。落档换上「冰原巨狼盾徽」后该前提消失，
  断言**假红** —— 不是闸门坏了。
  **修法**：改成守「闸门后池内**全部合规**」这个**不变量**（与是否真有剔除无关）；
  剔除能力由**相邻断言**独立守住（`sk3683` 盾牌战技在无盾时判 ✗）。
- `crit/total 解析值 == 模型实测边际` 容差 **1% → 3%**：换装后该**线性近似**的实测误差
  为 **2.0%**（解析 3.2079 / 实测 3.2739），是近似固有误差、**不是模型回归**。

> **教训**：凡是**依赖具体存档状态**的断言，都要么先比指纹再比数值（锚点哲学，见 #50），
> 要么改写成**不变量**，要么用**不依赖存档的合成场景**。

**附 67.2 · ⚠「实战」两个同名不同义的口径**

| 出处 | 「实战」= | 倍率链 |
|---|---|---|
| 自检锚点 / `plan_dps` 的 `dps_real` | **×命中期望** | 面板 × `hit.expected` |
| `gd dps` CLI 印的「实战」/ `dps_vs` | **再 ×减抗** | 面板 × 期望 × 减抗 |

两者差距可达 **40%+**（本次 145,575 vs 210,248）。**引用时必须写清是哪一个**，
否则「实战 210,248」与「锚点实战 145,575」看起来像互相矛盾。

---

68. ★★★ **装备位合法性：判据曾三次栽在「按描述文本判」—— 正解是「底材 `l` ∈ 物品 `cls`」**（2026-09-21 **已修**）。

**症状**：`data/plans/pivot/x_total.json` 里**两把斧头都镶了「弹性铠甲片」**（护甲组件，
**零伤害字段**）⇒ 把武器组件池里的**恶毒尖刺**挤掉了；**勋章上镶了「炼狱粉尘」**（戒指/项链附魔）。
三处**全是非法配置**，游戏里会被静默摘掉。

**根因不是一处，是五个洞叠加**：

| # | 位置 | 洞 |
|---|---|---|
| A | `comp_ok()` | `COMP_WORDS['主手']` 里的**单字 `'盾'`** + `w in pos` **子串匹配** ⇒ 「用于**盾牌**，法器副手，胸甲和护肩」被判可进武器（实测 `comp_ok('主手','it2860')` 曾返回 **True**） |
| A′ | `choices()` | `auto_comp_pool()` 按槽位（**含 `主手`/`副手`**）生成自动池 ⇒ 给 `POOL_COMP` 加了 `主手` 键 ⇒ `if slot in POOL_COMP` **遮住** `POOL_CWPN` 分支 ⇒ 硬编码武器组件池（含恶毒尖刺）被整体挤掉 |
| B | `aug_ok()` | 只认文本里 5 个词，**其余一律 `return False`** ⇒ **护甲槽永远拿不到附魔** |
| B′ | `auto_aug_pool()` | 槽位循环**只有 6 个**（首饰+武器），**护甲槽压根没进循环**；且 `_pool_score > 0` 门槛会把零分的全筛掉 |
| C | `choices()` | `JA` 被当「首饰通用池」也喂给**勋章** —— 而 `JA` 的 `cls=['c40','c41']` **只覆盖 戒指+项链** ⇒ 勋章附魔非法 |
| D | `tools/autobuild.py` | **起点解没做合法性清洗**（`cur_sol = {s: tuple(cur[s]) …}`）⇒ LNS/退火「只在能提分时才改槽位」，**非法项被原样留在结果里**。实测：只修 A~C 后重跑，`x_total_fix.json` 里那 2 处非法**仍然存在**（`choices()` 里已经 0 条非法候选，但搜索根本没去动那两个槽位） |

**★ 正解（一条规则通吃组件与附魔）**：

```
底材自己的 `l` 就是它的槽位码；组件/附魔的 `cls` 就是它能装的底材码集
⇒ 合法性 = base.l ∈ item.cls
```

**槽位码表**（实测推出）：`c10=头盔 c11=护肩 c12=胸甲 c13=手套 c14=腰带 c15=腿甲 c16=足具`、
`c40=戒指 c41=项链 c42=勋章 c43=圣物`、**`c26=法器副手 c27=盾牌`**、武器 `c20–c25 / c28–c32`。
附魔全库只有 **5 种** `cls` 组合：`[c40,c41]`×113 ／ `[c42]`×63 ／ `[c28–c32]`×81 ／
`[c20–c27]`×81 ／ `[c10–c16]`×38。

**改法**：`comp_ok(slot, gid, base_gid=None)` 与 `aug_ok(...)` 统一走 `base.l ∈ cls`；
无 `base_gid`（建池阶段）时退化成 `cls ∩ SLOT_CODES[slot]`；搜索循环里**逐条按实际底材**过滤
（`comp_ok(slot, c, b)` / `aug_ok(slot, a, b)`），并且 `augs` **必须保留 `None`** ——
否则池里每条都被否掉 ⇒ 该槽**零候选** ⇒ 搜索崩。
**外加**：`tools/autobuild.py` 建起点解时**清洗非法项（置空，不猜）** —— 否则前面全修对了，
结果里照样留着旧方案带过来的非法配置（洞 D）。

**★ 新增检查工具**：`tools/plan_legal.py <方案.json>…`（`--selftest` 验证判据本身）。
逐槽报「组件/附魔 + 底材码」，有非法项即退出码 1。**以后任何方案落档前都跑一次。**

**同批纠正的三个事实**：

- **护甲附魔 38 条全是防御向（零输出字段）** ⇒ 它们的价值是**合法地补抗性**。
  这也正是上面那些非法配置的**动机**：搜索器想补穿刺抗，但护甲附魔不在搜索空间 ⇒ 只剩武器组件一条路。
- ★ **护甲附魔有硬等级墙**：`k=70`（**17 条**，lv73 可用）／`k=90`（21 条，**lv73 用不了**）。
  ⚠ 本轮曾手工拿「拜斯迈的沙语（穿刺抗 +15）」补抗 —— **它是 `k=90`，lv73 不合法**！
  lv73 合法的是 **「刀锋护卫魔药」穿刺抗 +12**（`k=70`）与
  暗夜之影粉末／火焰编织魔尘／皇家护卫粉末 **+9**。
- **勋章的合法附魔是 `c42` 的「符文」**（鬼影刺客纹章、拉恩之力符文…），
  它们**只授予技能、没有任何抗性/OA/伤害字段** ⇒ `_pool_score = 0`，被老门槛全筛掉。
  ⇒ 摘掉非法的炼狱粉尘后勋章会**失去 OA+25 / 元素抗+12**，必须在别处补回 —— 这正是护甲附魔池该干的活。
  （`c42` 符文模型暂无法估价 ⇒ 池留空、`augs=[None]`，属**保守且合法**。）

**验证判据（8 条全过）**：斧+弹性铠甲片 → **False**；斧+恶毒尖刺 → **True**；
胸甲/肩甲+弹性铠甲片 → True；头盔+弹性铠甲片 → False；
`aug_ok('肩甲', 护甲附魔)` → True、`aug_ok('主手', 护甲附魔)` → False；
**硬编码候选池（`POOL_COMP`/`POOL_CWPN`）全部仍合法**。
自检 **399 项全绿**；镜像（`gd/opt.py` ↔ `tools/gd_opt_patched.py`）已 `sync_live --capture`。

---

69. ★★★ **优化器「落盘更差」与「星座静默叠加」——两个不报错、也不掉 DPS 的错**（2026-09-21 **已修**）

### 69-A `tune_devotion --realloc` 会把**更差的方案**写盘（已修）

**症状**：`PLAN=… GD_PROJ_HITS=1 $PY tools/tune_devotion.py --realloc` 打印
`基线 133,396 → 128,139（−3.94%）`（退「铁锤」3 星、补「老鼠」4 星），**却照样写了盘**。
下游 `eval_build_variants`（`DEV_SRC=data/scratch/devotion_realloc.json`）拿它当「星座方案」⇒
多轴终评的 **③ / ⑤ 被污染成负数**（−5.5% / −3.8%），而问题其实**不在技能侧**。

**根因两层**：

| # | 位置 | 洞 |
|---|---|---|
| ① | 贪心补点循环 | 每步只挑「**候选之间**最好」的那颗（`if best is None or v > best[0]`），**从不与当前分比较**。而 `ev()` **不单调** —— 加星点可能让 DPS **变低**（授技 proc 挤占 proc/WPS 池、并改变减抗结算顺序）⇒ 循环**可以一路下坡** |
| ② | 退款阶段 | 按「**单颗**损失 < threshold%」**独立**判定 ⇒ 多颗叠加后远超阈值，而贪心补点未必补得回来 ⇒ 最终低于基线 |

**改法**：全程记 **best-seen**（`best_score` / `best_recs`，初值 = 基线），
收尾 `best_score > 基线 + 0.05` 才采纳；否则**落「基线原样」**（`cur = dict(CUR)`），
JSON 里写 `"adopted": false`。
⇒ **输出契约：`devotion_realloc.json` 永不劣于基线**（消费方最坏拿到「什么都不换」）。

**验证**：修后同参数重跑 ⇒ `⚠ 放弃重排：最佳尝试（133,396）也没超过基线 ⇒ 落基线原样`，
`adopted=false` ✓；终评 ③ **−5.5% → 0.0%**、⑤ **−3.8% → +1.8%**（= ② 技能侧）。
**同族检查**：`tune_skills.py` **没有**这个问题（swap / add 两步都是 `if _sc(v) <= _sc(cur) + 0.5: break`，
即**每步都要求真提升**）⇒ 不动。自检加静态守卫 **`[55] 优化器落盘安全`**。

### 69-B `GD_SKILL_JSON` 注入的**星座是「并集」不是「替换」**（已加警告）

`gd/dps.py` 的注入语义是：`skills = {注入的技能}` → `skills.update(存档的星座)`
⇒ 只覆盖**同名**键。**注入文件里存档没有的星座会被静默叠加**在存档星座上。

**实测**：`data/plans/pivot/alloc_opt.json` 里带着 **4 星「乌龟」**（`tier1_29a~d`），而存档没有
⇒ 报告 **DA `1,406` → `1,470`**（+64）。而乌龟是**纯防御**、**合计 DPS 一位不变**
⇒ **这个错不会在任何 DPS 数字上暴露**，只有 OA/DA 行会骗人（`plan_dps` 的 216,114 两次完全一致）。

**改法（只报警、不改语义）**：`load_char` 发现 `skill_override` 含「存档没有的星座记录」时打印
`⚠ GD_SKILL_JSON 含 N 个「存档没有的星座」记录 ⇒ 会叠加在存档星座上`。
**没有改成自动剔除**，因为改语义会让所有历史批次漂移（锚点零漂移优先）；
**数据侧的正解**是让注入文件**只含技能/精通** —— 见 `data/plans/pivot/alloc_legal.json`
（63 条 → 31 条，丢掉 32 条星座）。实测带上它：合计 **216,114 → 219,964（+1.78%）**、
报告 DA 回到 **1,406** ✓。

---

70. ★★★ **星座点数的三个口径错误 + 一个搜索盲区**（2026-09-21，A~C/D 已修，E 待批准）

### 70-A `total_devotion_points` 是「**已花**」不是「池」

存档 block2 两个字段必须分开读：

| 字段 | 含义 | Sam 实测 |
|---|---|---|
| `devotion_points` | **未分配** | **4** |
| `total_devotion_points` | **已花** | **36** |
| ⇒ **池** = 已花 + 未分配 | | **40** |

- 旧代码把 `total_devotion_points` 当「已解锁/池」用：`gd/planreport.py` 的
  `_bud2 = min(cap, total)`（**少算未分配的点**）与「虔诚点已用 = 总 − 未分配」（应直接用 `total`）；
  `tools/tune_devotion.py` 的 `BUDGET` 默认写死 `36`。⇒ 方案**少花 4 点**、报告数字自相矛盾
  （会打印「已点 36 / 36」却又写「未分配 4 点」）。
- ★★ **proc / Celestial Power 节点也花 1 点**。官方指南：*"Some Stars glow brighter than others.
  These will grant Celestial Powers."* —— 亮星本身就是**要花点买的星**。三重对账完全一致：
  **存档点亮节点 36 == `total_devotion_points` 36 == 这 8 颗星座的节点数合计 36**。
  而 `groups()['stars']` 只数**非 proc** 记录 ⇒ 旧口径的「已投 34」实为 **36**，
  且对**带 proc 的星座**成本恒**少算 1 点**。
- 修法：`tools/tune_devotion.py::_pool()` 直接读存档两字段（`BUDGET` 可外部覆盖）；
  `gd/planreport.py::earned_points` 的注释与 `_bud2` 同步改。

### 70-B 搜索盲区：`len(g['stars']) > 4 → continue` ⇒ **所有 Tier-3 星座从未进过搜索**

T3 星座全是 **6~7 个节点**（5 星 + proc）⇒ 被这一行**整体排除**。代价实测很大：
单补 3 个节点就能拿到 **+13% 攻速**（北海巨妖）／**+70% 总伤**／**+80% 流血 + 18 平伤**（无名士兵）
这类星点 —— 逐条与记录字段对得上，**不是伪影**。

### 70-C 单星粒度：「剩下 1~2 点花掉」原来**一个候选都出不来**

`--realloc` 只在「整颗星座装得下」时才补（`len(cur)+len(g['stars']) > BUDGET → skip`）
⇒ 剩 1~2 点时没有合法候选，看起来像「没收益」，其实是**搜索粒度**的缺口（陷阱 **#69-A** 的续集）。

### 70-D 星座内部**连线顺序**怎么还原（游戏里**不能跳着点星**）

- 一颗星只有**父星已点亮**才能买，根星另需满足星座亲和力 ⇒ 合法买法 = **含根的连通子集**。
- ★★ **别用 `devotion_tree.json` 的 `stars` + `links` 直接配对 —— 会错位**：
  - `links` 是**按星位序号**排的（键 `2..N`，第 K 项 = 第 K 个星位的父星序号）；
  - 而 `stars` 数组**不是**按序号排的。实测 tier3_21：`calc.js` 里
    `button1..6 = sk2602…sk2607`（升序），树里却是 `sk2603, sk2604, sk2602, sk2607, sk2605, sk2606`。
  - 后果：照它对出来的「根 = `stars[0]`」「`stars[k]` 的父 = `stars[links[k-1]-1]`」**取到错的星**
    （实测「刺客」的非星点节点位置从 **4** 变成 **6**，说明确实错位）。
- **正解**：回原始 `data/cache/calc.js`（GT 桌面版的 calc 模块）取 `devotionButton<序号>:{X:"sk…"}`
  与 `devotionLinks<序号>:<父序号>`。⚠ 两者在星座对象里是**交错排列**的
  （实测 `button2, button3, links3, button4, …`）⇒ 必须**按大括号配对切块**
  （从 `devotionButton1` 往左找未闭合的 `{`、再往右找配对 `}`），不能按「下一个 button1」切。
  块与星座的对应**按星位 sk 集合**认（不靠 tag）。**根 = 唯一没有父星的 1 号**。
- sk 号 → 记录路径：`item_skills.json`（sk→数值）与 `devotions.json`（记录→数值）做
  **同星座内「最小距离配对」**。⚠ 归一化必须做：记录侧的值是**单元素列表**、
  且多一个 `characterBaseAttackSpeedTag`（否则配对率只有 52~57%）。实测 **104/104 星座的
  真实星点 100% 配上**，每个星座只差 1 个 = **proc 节点**（记为 `None`）。
  两套数据对不上（sk 集合不等）或拿不到根的星座**整颗跳过**（宁可不给候选，也不瞎猜）。
- 实现：`tools/tune_devotion.py::calc_graph()` + `star_graph()` + `rooted_subsets()`（自检 `[55]` 有守卫）。

### 70-E ⚠ proc 记录**不能**喂进模型（**待批准**补门控）

模型**没有 proc 触发率 / 指派门控** ⇒ 把攻击型 Celestial Power 当**常驻攻击技**：
实测**单独加一条**就能把面板抬 **雷霆暴怒 +44.6%**、**激流漩涡 +18.7%** —— **纯伪影**。
⇒ 现行策略：proc 节点**照样算成本（1 点）、但不给任何收益** ⇒ 搜索自然把「为拿 proc 花的那点」判成亏。
★ 归属认法也别踩坑：**按记录名前缀**（`tier3_05g_skill` → `tier3_05`），
**不要**用 tag 变换法 —— 实测**差 1**（`tier3_04g_skill` 是 C04 的，`tag` 却写 `tagDevotionEffectC05`；
`tier3_13g_skill`(C13) 写 C14）。这是陷阱 **#66** 那条「tag 变换」的**适用边界**。

### 70-F 退款阶段**静默丢掉 proc 记录**（已修）

`--realloc` 的「保留 / 退款」分组用 `DV.constellation_of()` 归位，而它**认不出**
`<前缀>g_skill` 这类授予技能记录 ⇒ 这些记录不在 `keep` 里，一跑退款就被**从方案里抹掉**。
实测：方案相对存档少了 `tier1_08e_skill`（**刺客的标记**，−33% 穿刺抗的 debuff proc）
与 `tier2_06g_skill` —— 症状是日志「剩余 **31** 节点」对不上 `36 − 3`。
**修法**：归位先走 `constellation_of`，认不出就按**记录名前缀**
（`tier1_08e_skill` → `tier1_08`）；再认不出的一律进 `orphan` 并**永远保留**。自检 `[55]` 有守卫。

---

71. ★★★ **装备授予的「常驻类」技能**从来没进过伤害评估（2026-09-21 **已修**）

**症状**：`恶毒尖刺`（组件）提示框里那块「**授予技能 → 强化穿甲利器**」——
**+75% 穿刺伤害 / +10 穿刺平伤 / 150 预留能量** —— 面板与实战里**完全没算**。
这类技能在游戏里是**一直开着的**（`Skill_BuffSelfToggled` = 常驻开关光环）。

**根因**：`gd/dps.py::load_char` 收集了 `item_skills`（`(来源, skXXXX, 控制器)`），
但它**只有两个消费方**：报告 §七 的展示表格、`skillprov.norm_equipped` 的合法性判定
⇒ **属性从来没折进伤害**。实测：把 `comp_bladeaura_02.dbr` 注入技能表，**+2.79%**。

**分类（必须按模板分，不能一刀切）**：`data/item_skills.json[sk]['l']`

| 模板 | 类别 | 处理 |
|---|---|---|
| `Skill_WPAttack*` | WPS | 已由 `item_wps` 入池（陷阱见「WPS 从未进循环」专题） |
| `Skill_BuffSelfToggled` / `Skill_BuffRadiusToggled` | **常驻开关光环** | ★ **要收** |
| `Skill_Passive*` / `Skill_Give*` | **常驻被动 / 属性加成** | ★ **要收** |
| `Skill_BuffSelf*` / `Skill_BuffRadius*`（无控制器） | 常驻 buff | ★ 要收 |
| 带 `itemSkillAutoController`（`ctXX`）/ `Skill_Attack*` | **触发型** | ⛔ **不收**（触发率/指派没建模，同 proc 口径 #70-E） |

**改法**：`load_char` 里按上表挑出常驻类，**同一 `sk` 组只取「数值字段最多」的那条记录**
（`sk82` 会给出本体 + `granted/` 子技能两条，全收会重复计算），
用 `itemSkillLevelEq` 当等级，`skills[rec] = max(已有, 等级)` 折进技能表。
★ `db.fields()` **能解析**这些记录（它们**不在** `db.skills` 4268 条表里）⇒ 进技能表即可生效。

**实测（Sam / `x_total_fix2`）**：`219,964 → **234,122**（+6.44%）`，
计入 **2 条**：`comp_bladeaura_02`（恶毒尖刺 ×2 把武器）与 `item_gunslinger`（胸甲被动）。
⚠ 存档自身那套装备**没有**常驻类授予技能 ⇒ **端到端锚点（#20）零漂移**。

**待办**：`characterManaLimitReserve`（150×2 = 300 预留能量）**没有建模** ——
两把「恶毒尖刺」会吃掉 300 上限能量，`gd/mana.py` 目前不看这个字段（能量够不够另说）。

---

72. ★★★ **`skillmod` 的数据目录指向已不存在的 `gt_data/`** ⇒ 套装/物品技能表**恒空**（2026-09-21 **已修**）

**症状**：`SM.load_sets()` 与 `SM.load_item_skills()` **永远返回 0 条**。
后果：`folded['套装加成']` **根本不会生成**（套装加成整套丢失）、
`set_skill_mods`（套装技能改造）恒空 —— 而**不报错、不崩、任何断言都看不出来**。

**根因**：迁移把数据从 `gt_data/` 搬到了 `data/`，`gd/skillmod.py` 的
`GT = os.path.join(HERE, 'gt_data')` **没跟上** ⇒ `_load()` 直接拼路径 ⇒ 文件不存在 ⇒ 静默 `{}`。

**改法**：`_load()` 改走 `gd.paths.load_json(name)`（与全项目同源），`gt_data/` 只作兜底。
★ 顺带发现：这条失效**挡住了 #71 的修复** —— 常驻技能的判据表就是 `item_skills.json`，
表是空的 ⇒ 折叠逻辑成死代码。**两个 bug 是叠着的**。

**量化（Sam）**：他的 13 件装备**不属于任何套装** ⇒ 对当前结论 0 影响；
但任何用套装件的 BD，套装加成过去**一直是 0**（`setMembers` 数据本身是好的，199 套齐备）。

---

73. ★★★ **亲和力的真规则是「点满一颗星座才给 `given`」—— 近似的「每星 +1」会放行点不出来的星座**（2026-09-21 **已修**）

**症状**：`--realloc` 给出「退铁锤 ＋ 补『北海巨妖』(5 节点) ＋『无名士兵』(2 节点)」，
模型算出 **+20.20%** —— 而这三步**没有一步能在游戏里做出来**：

| 动作 | 为什么不行 |
|---|---|
| 补「北海巨妖」 | 需 **Primordial 5** + Eldritch 5 ｜ Sam **Primordial = 0**（他把十字路口的 Primordial 星退过） |
| 补「无名士兵」 | 需 Ascendant 15 + **Order 8** ｜ Sam 是 Asc 21 / **Order 5** |
| **退「铁锤」** | 铁锤给 Ascendant 4；退掉后 **aff Order 只剩 2 < 「刺客」需要的 4** ⇒ 连「不动」都不如 |

**真规则**（官方指南）：*"Affinity is earned by **fully unlocking a Constellation**,
or through each star in the Crossroads Constellation."* ⇒ 数据里

- **`given`** = **完成奖励**（**点一半 = 0**）；**`affinity`** = 门槛；**T3 一律 `given: []`**（不给）。
- **十字路口 = 6 个「抉择之地」条目**（`idx 15/80/81/82/83/87`，各 1 星、需求空、各 +1 亲和力）。

**根因**：`DV.check_affinity` 是「**每星 +1 → 主亲和力**」的粗筛（它自己的 docstring 就写着
「报『满足』仍需游戏内确认」），却被 `--realloc` 当成了**硬门槛** ⇒ 产出不可实现的方案。

**改法**（`tools/tune_devotion.py`）：

| 函数 | 作用 |
|---|---|
| `aff_of(cur, tree, SG)` | 亲和力 = `Σ`「**点满**」星座的 `given` |
| `req_ok(idx, aff, tree)` | **候选门槛**（替掉近似 `check_affinity`） |
| `feasible(pend, aff0, tree, SG)` | **拓扑可达性**：最终够门槛 ≠ 点得出来（X/Y 互锁会死锁）；亲和力只增 ⇒ 反复取「当下够门槛」的 |
| 退款前复检 | 退掉一颗会让**别的**星座门槛不够 ⇒ **撤销退款**（实测「退铁锤」就是这样被挡下的） |

**量化**：合法方案 = 「**狂战士** 3 节点 ＋ **螳螂** 1 节点」 ⇒ `138,634 → 146,694（**+5.81%**）`；
对存档 **+12.47%（面板）/ +15.72%（含减抗）**，全上 **+14.44% / +17.76%**（把那个 +20% 的幻影挤掉）。

**教训**：凡是「**游戏会不会放行**」的判据，都必须回到**数据里的真规则**；
粗筛模型可以留着做提示，**绝不能当硬门槛**。

---

74. ★★ **落档方案的差量必须「按并集一次算」—— 分两趟会把「升级」静默算成「撤点」**（2026-09-21 **已修**）

**症状**：`tools/plan_to_save_skill.py`（本轮新增的落档方案生成器）第一版把
`playerclass10/leap1.dbr 6 → 8` 算成了 **`6 → 0`**。`gd.save.skill check` 照样**六重校验全过**
（它只校验「写进去的是不是方案说的值」，方案本身错了它看不出来）。

**根因**：差量分两趟算 ——
① 先扫存档：`target.get(r,0) != lv` ⇒ 写成 `(r, 0, '撤点')`；
② 再扫目标：`cur.get(r,0) != lv` ⇒ 写成 `(r, 8, '调整')`。
同一条记录两趟都命中，去重时**保留了先写入的那条**（= 撤点）。

**改法**：`for r in sorted(set(cur) | set(target))` 一次算完，
`after == 0` 才是撤点。自检 `[55]` 加了静态守卫。

**教训**：**「六重校验」只证明「写进去的 == 方案里写的」，不证明「方案是对的」**。
生成器与校验器之间必须有一个独立的、可读的 diff 打印（本工具会逐条打印
`before -> after`，我自己就是看那行发现 `leap1 6 -> 0` 不对劲的）。

---

75. ★★ **亲和力 / 装备授予技能 / 终评数据源 —— 三个「不报错、只在特定路径暴露」的静默错**（2026-09-21 **已修 A、B**）

### 75-A 亲和力「点满」判据拿了含 proc 的节点数 ⇒ 带 proc 的星座 `given` 恒 0

`tools/tune_devotion.py::aff_of` 原判据：

```python
if sum(1 for r in sg['pos'] if r and r in cur) >= sg['nodes']:   # ✗
```

`sg['nodes']` 是 `calc_graph()` 数出来的**按钮总数**，**含 proc（Celestial Power）节点**；
而 `sg['pos']` 里 proc 那一格是 **`None`** ⇒ 「点亮记录数」**永远比 `nodes` 少 1**。
**症状**：Sam 的亲和力被低估 **Ascendant −4（铁锤）／ Order −3**，
`req_ok` 因此**过严** ⇒ 误判「退款后门槛不够」（走「撤销退款」分支），
**把一次 +0.64% 的合法重排挡在外面**；顺带输出里那句「亲和力自洽：False」也是同一处漏算的副作用
（它当时用的是近似粗筛 `DV.check_affinity`）。

**正解**：`all(星位在 cur) and (无 proc or proc 在 cur)` = 游戏里的「完全解锁」。
修后 `aff_of` = `{Ascendant 21, Order 5, Eldritch 5, Chaos 3}`（与手算一致）。

> **教训**：**「计数」型判据必须确认计数的**分母**和**集合**是同一套**。
> `nodes`（含 proc）与 `pos`（proc 位是 `None`）本就不是同一个集合，拿来比大小必然差 1。

### 75-B 装备授予的技能混进了「加点方案」 ⇒ 落档会把「装备给的」写成「玩家点的」

`gd/dps.py::load_char` 会把**装备/镶嵌/圣物授予的常驻类技能**折进 `skills`（陷阱 #71），
而 `tools/tune_skills.py` 是把（折叠后的）`skills` 整体写回方案文件的 ⇒
**`data/scratch/skill_opt.json` 里混着 `comp_bladeaura_02` / `item_gunslinger` /
`relic_malediction`**。落档时它们会被当成「新增技能」写进存档技能表。

**为什么以前没发现**：折叠用的是 `max()`（`{r: lv for r, lv in … if lv > skills.get(r, 0)}`）
⇒ **不写档也完全不影响伤害数值** ⇒ 所有 DPS 断言、锚点、终评**一个都不会红**。
**只在落档这条路上暴露**，而且症状要等玩家摘掉那件装备才看得出来（技能还在）。

**改法**：`tools/plan_to_save_skill.py` 默认剔除 `records/skills/itemskills/`
（`--keep-granted` 可关）。⚠ 正则要带结尾斜杠 —— `itemskillsgdx3/potionmodifiers/…`
（药水改造）是**真正的存档条目**，必须保留。

### 75-C `eval_build_variants` 不显式传两个源 ⇒ **静默吃旧文件**

`SKILL_SRC` / `DEV_SRC` 不传时回退到 `data/scratch/skill_swap.json` / `devotion_opt.json`
（**上次跑留下的旧文件**）⇒ 会输出**全是负增益的假结论**
（实测跑出 ② −5.7% / ③ −10.0% / ⑤ −15.3%，还附一份 26 处「技能改动」）。
只有 `① 现状` 一行是可信的。**永远显式传 `SKILL_SRC=` 与 `DEV_SRC=`。**
（与陷阱 **#65 坑 B** 同族：产物名 ≠ 消费方默认路径。）

---

76. ★★ **`%` 格式串里的中文字面 `%` —— 一条「旧 build 走不到、所以从没崩过」的渲染器死路**（2026-09-21 **已修**）

**症状**：`gd dps Sam` 直接抛

```
ValueError: unsupported format character '目' (0x76ee) at index 8
  在 gd/dps.py::build_report → A('> 取最强类（%目标抗性降低）：%s' % …)
```

**根因**：那个字符串走的是 `%` 格式化，而 `'（%目标抗性降低）'` 里的 **`%目` 被当成格式说明符**。

**为什么一直没暴露**：它包在 `if _rrs.get('max'):` 里 —— 只有 build 带 **C 类减抗**
（`n% Reduced target's Resistances`）时才会执行。**之前所有 build 都没有 C 类**，
⇒ 这条分支**一次都没跑过**。2026-09-21 落档「罗卡口径」的装备（带全 10 型「最多 −30%」）
才第一次触发。

**为什么所有断言都没抓到**：锚点 / selftest 的数值断言走的都是 `final_report`（**数据层**），
**不碰渲染器**（`build_report`）。⇒ 数据全对、报告链整条断。

**改法**：`'%%目标抗性降低'`（同文件 922 行早就写对了 `无 %%`）。

**加固（本轮新增，selftest [20]）**：**跑一遍 CLI 的报告链**并断言不抛 ——

```python
sys.argv = ["dps", "Sam", "--brief"]      # 注意：`main()` 自己 parse sys.argv[1:]
with contextlib.redirect_stdout(buf):
    importlib.import_module("gd.dps").main()
check("★ `gd dps` 能完整渲染当前存档的报告", "合计每秒伤害" in buf.getvalue())
```

**教训**：**「条件分支从没被执行过」= 等价于没有这段代码**。凡是报告 / 渲染路径，
都要有一条**跑真实存档**的集成断言，否则它只会在某个新 build 上第一次炸。

---

### 77. ★★★ **`defensive*` 字段的「作用对象」取决于模板** —— 减益技能的「降敌抗」会被算成我方掉抗（2026-09-21 **已修**）

**症状**：`gd/defense.py` 一接入，Sam 的抗性面板立刻少了 **穿刺 −38 / 混乱 −25 / 冰冷 −25 / 物理 −4**
—— 而存档明明是**满抗 1070/1070**。

**根因**：**同一个字段名 `defensivePierce`，在不同模板上作用对象相反**：

| 所在记录 | 模板 | `defensivePierce` 的含义 |
|---|---|---|
| 装备 / 星座星点 / 自 buff | `Skill_Passive` · `Skill_BuffSelfToggled` · `Skill_BuffRadiusToggled` · `Skill_Modifier` | **我方**穿刺抗性 |
| ★ **减益技能** | **`Skill_Attack*`**（`Skill_AttackBuff` / `Skill_AttackBuffRadius`） | **目标的**穿刺抗性（= 降敌抗！） |

实测两个反例（Sam 身上就有）：
- `devotion/tier1_08e_skill`（**刺客的标记**，proc）→ `defensivePierce −8 / defensivePhysical −4`
- `playerclass10/bonechillingcry1`（**刺骨战吼**）→ `defensivePierce −30 / defensiveBleeding −40 / defensiveChaos −25 / defensiveCold −25`

⇒ 无脑累加 = 把「我给敌人的减益」记成「我自己掉抗」。

**判据**：模板以 `Skill_Attack` / `Skill_Debuff` 开头 ⇒ **排除**（实现 `gd/defense.py::applies_to_self`）。
⚠ **不能**简单地按 `*_skill.dbr` 后缀排除 —— 星座的「**自我增益型** proc」
（如伊师塔克的自然守护者给 % 吸收）**也要收**，只能靠模板区分。

**为什么以前没暴露**：`gd/compare.py::survival()` **也有这个缺陷**（无脑累加 `defensive*`），
但它只用于「两个存档对比」⇒ **相对值部分抵消**，绝对值错也看不出来。

**教训**：**「同名 = 同义」是个危险的假设**。凡跨模板复用的字段，先问「它作用在**谁**身上」。
（同族的还有：`Skill_Modifier` 的字段是**加到别人技能上**的、不是独立来源 —— 陷阱 #66。）

---

**症状**：`gd/rotation.py::final_report` 的武器池只从 `levels`（存档技能表 /
形态 `core_skills`）收集那些**带 `skillChanceWeight` 的攻击技能**；而
**装备、镶嵌物、附魔、圣物授予的技能不在那张表里** —— 它们走的是
`allItems[gid].itemSkillName` 这条路。于是装备授予的 `Skill_WPAttack_*`
**一次都不参与掷骰**。

实测（werewolf 方案，`data/plans/lv73_A/arch_werewolf.json`）：

| 槽位 | 物品 | 技能 | 记录 | 等级 | `skillChanceWeight` | 武器伤害% |
|---|---|---|---|---|---|---|
| 勋章 | 冰原巨狼盾徽 | 恐狼之爪 | `sk325` | 1 | **12** | 135% |
| 圣物 | 刀剑大师护符 | 毁伤 | `sk519` | 3 | **25** | 110% |

⇒ 修复前 `rotation.procs = []`、`weight_total = 0`，**37 点权重凭空蒸发**。

**★ 两层坑（只修一层不算修）**
1. **记录路径翻译**：`itemSkillName` 是 `skXXXX`，而技能表用的是
   `records/skills/…dbr` ⇒ 必须经 `gd/skillprov.py::sk_to_records()`（tag 索引）翻译。
2. **权重不在记录里**：`skillChanceWeight` **既不在 `.dbr` 记录、也不在
   `skills.json`**（`stats` 段里没有），只存在于离线库的 **`itemSkills` 表**（键 `skXXXX`）。
   ⇒ 只把记录塞进技能表，权重仍算成 `0`，记录会被判成「**又一个默认攻击候选**」
   而不是武器池技能 —— 数值照样错，还错得更隐蔽。

**修法（2026-09-20，用户批准）**
- `gd/procs.py::wps_of_item()` —— 一件物品 → WPS 注入项（记录路径 + 等级 + 权重）。
- `gd/procs.py::wps_pool(gids)` —— 按 gid memo，喂给模型。
- `gd/dps.py::load_char()` 新增对外暴露 **`base_gids`**（当前配装的 GT id 列表，
  存档路径与 `gear_override` 路径都覆盖）—— 这是注入的输入。
- `gd/rotation.py::final_report(item_wps=…)` —— 注入 `_recs` + `levels`，
  并把权重补进该记录的字段（`f['skillChanceWeight']`）。
  现已在 `gd/dps.py` / `tools/plan_dps.py` / `tools/gt_regress.py` / `gd/gen.py` 全部接线。
- **`item_wps=None` ⇒ 整段跳过，零漂移**（自检 `[31]` 有一条护栏守它）。

**前后对照（Sam · werewolf）**

| 口径 | 修复前 | 修复后 |
|---|---|---|
| 武器池权重 W | 0 | **25**（存档：遗物「毁伤」） |
| 默认攻击权重 | 100 | **75** |
| 野性利爪频率 | 3.45 下/秒 | **2.59 下/秒** |
| 面板 / 实战 | 52,737.6 / 53,093.9 | **52,598.7 / 52,954.0** |
| OA / DA / PTH / 暴击率 | 1760.8 / 1464.5 / 94.93 / 5.93% | **一位未动** |

werewolf **方案**（含 `it798` 勋章）：W = 12 + 25 = **37**，默认攻击 63，
面板 90,068 → **93,996**、对怪 143,584 → **136,201**（面板涨、对怪跌 —— WPS 的
伤害类型与敌方抗性/护甲的匹配比野性利爪差）。

**★ A 口径全形态普查（12 形态各 400 轮 LNS，`data/plans/lv73_A/_sweep.json`）**

| 形态 | 修复后 | 修复前 | 变化 |
|---|---|---|---|
| `fangs` ⚠ | 148,765 | 129,660 | +14.7% |
| `raven_nightblade` | 146,467 | 128,676 | +13.8% |
| `werewolf` | 143,573 | 131,419 | +9.2% |
| `wolf_nightblade` | 143,573 | 130,990 | +9.6% |
| `wolf_nightblade_fast` | 142,894 | 131,976 | +8.3% |
| `soldier_nightblade` ⛔ | 142,500 | 130,105 | +9.5% |
| `human` | 142,390 | 131,838 | +8.0% |
| `oathkeeper_nightblade` ⛔ | 140,583 | 128,930 | +9.0% |
| `wereraven` | 138,965 | 110,222 | **+26.1%** |
| `berserker_nightblade` | 124,569 | 116,947 | +6.5% |
| `necro_inquisitor` ⛔ | 124,501 | 115,865 | +7.5% |
| `avalanche` | 15,714 | 15,714 | **0.0%** |

⛔ = 需要第三个专精，Sam 换不了（数字仅作对照）。`avalanche` 不变是因为它的
优化装备里没有授予 WPS 的部件 ⇒ **这条修复只影响「身上真有 WPS 装备」的形态**。
⚠ `fangs` 在 **A 口径（真实存档）** 里本来就排前 —— 那是**既有**现象（A 口径下
`skill_records` 用的是存档技能，形态门控不生效），**与本次修复无关**；
`fangs` 的**构建合法性**只在 B 口径（`GD_SKILL_JSON` 注入 + `equipped_sk` 门控）下被剔除。

**★ 相伴的判据漏洞**：`wps_gap()` 原来拿 `e['sk']`（`sk325`）直接和
`base_hits` 的**记录路径**比 ⇒ **修好之后它还是报缺口**（假阳性）。
已改为经 `skprov.sk_to_records()` 翻成记录路径再比，自检 `[31]` 加断言守这件事。

**★ 新暴露的相邻缺口（已登记，未修）**：`dualWieldOnly` **模型里完全没判**。
`sk325` / `sk519` 与夜刃武器池（切割/瞬影/死亡旋风/处决）**全部 `dualWieldOnly = 1`**，
换成双手武器时它们在游戏里根本不出现，而模型照算。Sam 当前是**双持两把单手剑**
（`it2737` ×2，`c20`）⇒ 对 Sam 无影响；但任何双手 build 的 WPS 占比都会偏高。
修它同样会改全部数值 ⇒ **需单独批准**。

**排查口径**：怀疑「装备上的武器池技能没生效」时，跑
`python tools/plan_cycle.py ... --out x.md` 看**循环总览的 `W` 与占比**；
`itemSkillName` / `itemSkillAutoController` 全在 `allItems` 里，
控制器语义在 `itemSkillControllers`（70 条，键 `ctNN`）。

引用的自检：`tools/selftest.py` `[31]`（控制器表 / 八种裁决 / 提示框格式 /
WPS 缺口检出 / **wps_pool 权重与翻译** / **注入生效 W=37** / **零漂移护栏** /
归因恒等式）与 `[27]⑨`（面板锚点 = v5 52,598.7）。

### ★ 改完「文档渲染器」必须重跑**所有已落盘的**产物

**症状**：`gd/dmgcycle.py` 的译名回退链已修好（`name` 优先于 `tag`、tag 串回退文件名），
自检也过了，但 `CYCLE_werewolf.md` 里**仍然是** `tagGDX3Class10SkillName04B`。

**原因**：产物是**落盘快照**，渲染器改了它不会自己变。自检只验渲染器**当前**行为，
不验历史产物 —— 两者是两回事。（2026-09-20 实测踩过：三份 `CYCLE_*` 里只有
werewolf 那份是修复前生成的，另两份恰好是修复后生成的。「部分陈旧」最难发现。）

**规避**：① 改渲染器后，**逐个**重跑全部已交付产物（别只跑一份就以为都好了）；
② 重跑前先 `diff` 新旧——**数值必须逐位一致**、只允许时间戳与「被修的那一处」不同
（这同时验证了「渲染器改动不动数值」）；③ 对交付物做一次 `grep -h <旧症状>` 全目录扫。

**同类**：任何「从代码生成落盘文件」的环节（`plan_cycle` → `CYCLE_*`、
`plan_audit` → `REPORT_*`、`build_sheet` → `SHEET_*`）都适用。

---

## #78 形态门控只在形态「显式声明 `form`」时生效 —— 13 个形态里只声明了 1 个

**症状**：狼人形态的 Sam，`gd dps` 面板里赫然写着「**跃击 16 级 27,307（19.8%）**」，
而跃击（`records/skills/playerclass10/leap1.dbr`，`Skill_AttackRadiusLeap`）
在狼人变身后**根本点不出来** —— `werewolf1.granted` 只有
`[werewolf1_skill01_claws, werewolf1_skill02_charge]`（离线库直读，铁证）。

**根因**：`gd/rotation.py::form_gate` 的契约是「形态没写 `form` ⇒ 原样返回、零过滤」。
这条契约本意是「不动历史形态、保零漂移」，实际效果是**门控形同虚设** ——
`data/archetypes.json` 的 13 个形态里**只有 `avalanche`** 写了 `form`，
于是狼人/鸦人全系（**8 个变身形态**）**全程零门控**。

**两层错**：

① **漏**：该剔的主动技能没剔（跃击 19.8%）；
② **误杀**：判据按 `kind ∈ FORM_LOCKED` 一刀切，把**星座 proc** 也一起干掉
（「刀锋之怒」`devotion/tier2_06g_skill` 无辜掉 2,031）——
星座 proc 是「**攻击时触发**」的，变身之后照样在打（默认攻击换成野性利爪而已），
不该跟着技能栏一起消失。

**修法**（2026-09-21）：

① 判据改按**技能的真实来源**分四类（`gd/rotation.py::form_blocks`）：

| 条件 | 裁决 | 依据 |
|---|---|---|
| `rec ∈ 形态 granted` 或 `form_of_record(rec) == form` | **保留** | 形态自己授予的（野性利爪/狂乱撕扯） |
| 路径含 `/devotion/` | **保留** | 星座 proc，被攻击行为触发，不占技能栏 |
| WPS 模板 / `kind ∈ FORM_LOCKED` | **剔除** | 技能栏主动技能 + 武器池技能 |
| 其余（被动/光环/加成/开关） | **保留** | 不占技能栏，变形后照常生效 |

② **13 个形态全部补上 `form`**（`werewolf` / `wereraven` / `fangs` / `human`），
由 `root_skills[0]` 的 `kind == 'shapeshift'` 自动推导，`fangs`（完美姿态）因记录名
字母后无数字、正则抓不到而单独特判。

③ 装备授予的 WPS 走 `gd/procs.wps_pool()` 是**另一条路**，在 `form_gate`
**之后**才注入 ⇒ 必须在注入处**再过一次同一道门控**。不补的话
「击倒」（13.8%）+「恐狼之爪」（9.9%）照样留在池里。
拦截账本：`rep['rotation']['wps_form_dropped']`。

**为什么 WPS 也剔**（2026-09-21 用户拍板）：WPS 是「**替代默认攻击**」的武器池技能，
而变身后默认攻击由野性利爪接管 ⇒ 默认攻击被替换了，WPS 一次都不触发。

**量化**：Sam 面板 **137,719.3 → 83,536.1**（−39.3%）。其中跃击 −27,307、WPS −32,688。
★ 这不是「变弱了」，是把**虚增**扣掉 —— 旧数字里 19.8% 属于一个点不出来也用不了的技能，
而且 v9 落档时那笔「跃击 +5」正是被这个 bug 驱动出来的错误决策。

**自检**：`tools/selftest.py [22]⑥/⑥b/⑥c` —— 覆盖「每个形态都声明 form」、
「form 与 root_skills 分支自洽」、「跃击必剔 / 形态技能与星座 proc **不误杀** /
WPS 必剔 / 人形态只剔变形技能 / 未声明 form 零过滤」。

---

## #79 冷却技能是「无摩擦叠加」—— `freq = 1/冷却` 且不占任何时间

**症状**：主动攻击技能在模型里收益虚高，与手感严重不符（用户原话：「主动攻击技能
收益是很低的」）。

**根因**：`gd/rotation.py` 对冷却技能取 `freq = 1 / h['cooldown']`，隐含两个过乐观假设：
① 每 N 秒**必定**放一次（覆盖率恒 100%）；② 施放**不占任何时间**。
现实里放技能要吃攻击动画时间，这段时间平 A 与 WPS 都出不来。

**修法**（2026-09-21）：新增**施放占位** `occ = Σ(1/冷却 × 每次占位秒数)`，
从平 A 可用时间里扣掉（`swing_scale = 1 − occ`，作用于默认攻击与 WPS 的频率）。

* `GD_CD_CAST`（默认 **0.4 s**）—— 每次施放的占位时长；
* `GD_CD_FRICTION`（默认 **1.0**）—— 折扣强度；设 `0` ⇒ **逐位退回旧口径**
  （零漂移后路，实测 83,536 → 88,380）。

★★ **只有玩家主动施放的技能才占时间**。星座 proc（`/devotion/`）与装备授予的
自动触发技能（`/itemskills`）是「攻击时自动触发」的，与平 A **并行**发生、不抢动作。
实测不排除的话，「刀锋之怒」（1/1.79 s）会白白吃掉 **22%** 的平 A 时间
（占用率 0.324 vs 正确值 0.1）。

**量化**：Sam 88,380 → **83,536**（占用率 0.1，平 A 可用 0.9）。

**同源缺口（仍未做）**：**覆盖率**（uptime）本身仍写死 100% —— 本次只扣了
「施放占位」这个**时间成本**，没有引入「实战打不满冷却」的折扣。


---

## #80 星座星位**整类**拿不到补洞字段（一个 tag 对多条记录）

**症状**：库里 `Axe` / `Axe2h` / `Spear2h` / `Sword` / `Mace` / `Shield` / `Dagger` 这些
**武器类型白名单**字段**全库为 0 条** —— 模型完全不知道「狂战士星座需要斧或矛」这种**硬前提**。

**根因**：`tools/extract_calc_skills.py` 靠 `skillDisplayName` **tag 一对一**映射
（`calc.js` 的 `skillDisplayName` ↔ `skills.json` 的 `tag`），规则是「候选唯一才采用」。
而**星座星位天然是一个 tag 对多条记录**：

```
tagDevotion_B25（狂战士） → tier2_25a.dbr / b / c / d / e / f    ← 六条，全被跳过
```

⇒ **星座星位整类没有补洞字段**，武器限制就是这么丢的。

**修法**：加**数值指纹消歧** `_match_by_stats()`：

1. 候选与 `calc.js` 条目的**共同数值字段 ≥ 2 个**且**逐值相等**；
2. 再按**字段集对称差**排序取最小者（**并列 ⇒ 不认**）。

第 2 步不可省 —— `tier2_25b`（`defensiveFreeze`）与 `tier2_25d`（`defensiveStun`）的
公共字段完全一样（+50% 物理 / +50% 流血），只靠第 1 步会**并列**，
已点的 `tier2_25d` 就拿不到武器限制（实测踩到）。

**结果**：产物 **344 → 749 条**（+405 条靠指纹救回）、+2218 字段值
（**纯增量**：405 条新记录、**0 条已有记录被改值**，已 A/B 实测对 DPS 无影响）、
**46 条带武器限制**（按 `gd.procs.WEAPON_TYPE_CLASS` 的规范键集；逐键：
`Axe2h` 26 ／ `Mace2h` 26 ／ `Spear2h` 26 ／ `Sword2h` 22 ／ `Shield` 13 ／
`Mace` 11 ／ `Axe` 11 ／ `Sword` 7 ／ `Dagger` 7 ／ `Scepter` 7）。

**同类**：任何「按 tag/名字做一对一映射」的抽取——只要目标实体**成组出现**
（星座星位、套装部件、词缀族），都会踩同一坑。

---

## #81 武器类型门控必须放在**加成收集之前**

**症状**：门控代码明明把该剔的技能都剔了（拦截账本非空），**面板却一位不变**。

**根因**：`final_report()` 里**加成收集在前、技能循环在后**：

```
for rec, lv in (levels or {}).items():   ← 约 1181 行：把 % 加成折进 pct / sk_pct
...
_recs = form_gate(...)                   ← 约 1427 行：技能循环清单
```

把门控挂在 `_recs` 上只影响**技能循环**，星位的**被动加成**（狂战士的 +300 生命 /
+50% 物理 / +50% 流血）早就收完了 ⇒ 门控形同虚设。

**修法**：门控挪到**函数开头、加成收集之前**，且**两边都剔**（`levels` 复制后删 +
`_recs` 过滤）。`weapon_st=None` ⇒ 整段跳过、**零漂移**。

**规则**：技能记录顶层的武器键是 **OR** 语义（`Axe`+`Axe2h`+`Spear2h` = 「斧或矛」，
对应 l10n `tagDevotion_RequiresAxeSpear` = 「需要斧或矛。」）。

**推论（重要）**：**任何「某来源完全不生效」的门控，都必须落在它被消费之前** ——
先问「这个字段是在哪一步被读走的」，再决定插在哪。

---

## ★ 附：判据互相矛盾时，**先复算指纹**

2026-09-22 实测踩到：发现 `gd/build._active_weapon_set`、`gd dps` 打印、`base_gids`
**三处武器套判据互相矛盾**，正准备报 bug —— 复算指纹才发现**玩家在游戏里换了武器套**
（`2e8dc006a055` → `ddb54cc16610`），三处判据**其实一直自洽**。

**顺序**：`tools/save_state.py <角色>` 复算指纹 → 与锚点 `_state.指纹` 比对 →
不符则**先重设锚点**，再谈模型 bug。

---

## #82 「可选参数门控」最危险的形态：**只接了一条路，其余全部静默零门控**

**症状**：同一个角色、同一份存档，`gd dps` CLI 与优化器（`plan_dps`）给出**两个不同的数字**，
而且**没有任何提示**说明差异从哪来。

**根因**：武器类型门控（`final_report(weapon_st=…)`）是**可选参数** ——
`weapon_st=None` 时整段跳过。2026-09-22 实测只有 `plan_dps.dps_of` 一处接了，
其余全部漏接：

| 路径 | 接门控前 |
|---|---|
| `tools/plan_dps.py`（优化器主口径） | ✅ 已接 |
| `gd/dps.py`（`gd dps` CLI） | ❌ 零门控 |
| `tools/tune_devotion.py`（星座轴） | ❌ 零门控 |
| `tools/tune_skills.py`（技能轴） | ❌ 零门控 |
| `tools/tune_attrs.py`（属性轴） | ❌ 零门控 |
| `tools/eval_build_variants.py`（变体终评） | ❌ 零门控 |
| `tools/gt_regress.py`（回归基准 ×2 处） | ❌ 零门控 |
| `tools/defense_audit.py`（防御链） | ❌ 零门控 |
| `gd/gen.py` / `gd/mana.py` | ❌ 零门控 |

**修法**（三层，缺一不可）：

1. **单一真源**：`gd.dps.load_char()` 在源头算一次 `weapon_st` 存进返回字典
   （`base_gids` 已同时覆盖「存档」与「override 优化器换装」两条路）⇒
   调用点直接 `c['weapon_st']` 取用，不必各自推断。
2. **统一入口**：`gd.procs.weapon_state_of(char)` —— 优先吃 `char['weapon_st']`，
   取不到才从 `base_gids` 现推（兼容旧字典 / 局部构造的 char）。
3. **★★ 静态守卫**：selftest `[61]` 用 **AST 扫描** `gd/` + `tools/` 里**每一个**
   `final_report(` 调用点，要求**必须点名 `weapon_st`**（写 `None` 也行，但要有名字）。
   这条防的是「**以后新加的调用点又忘了接**」—— 靠人眼检查 19 个调用点必然漏。

**推论（通用）**：任何**默认关闭**的门控/开关（`None` ⇒ 不生效）都必须配一条
「调用点全覆盖」的静态检查，否则它会**静默**地在部分路径上失效。宁可写
`weapon_st=None` 显式声明「此处故意不门控」，也不要让它默默缺省。

**同类**：`item_wps`（WPS 注入）、`equipped_sk`（技能来源合法性）、`GD_OBJ`（评分口径）、
`SKILL_SRC` / `DEV_SRC`（`eval_build_variants` 的源文件）——**全是同一个模式**。

---

## #83 星座的**显式通道**（`devotion_levels`）漏过门控

**症状**：武器门控在 `levels` 通道生效，但 `tune_devotion` / `eval_build_variants`
里受限星位**照样计收益**。

**根因**：`final_report()` 的加成有两个入口 ——

```
levels            ← 存档 / 形态通道：星座记录混在「技能」里
devotion_levels   ← **显式星座通道**：tune_devotion / eval_build_variants 走这条
```

门控第一版只遍历了 `levels`，而 `devotion_levels` 随后被 `devotion_contrib()` 直接消费
（`dev = devotion_contrib(devotion_levels, db) if devotion_levels else {...}`）⇒
**显式通道上完全没有门控**。而星座优化器走的正是这条。

**修法**：门控抽成一个内部函数 `_wgate(d)`，**两条都过**，并分别记 `channel`
（`levels` / `devotion`）便于定位。`_gone0` 取并集后再剔 `_recs`（技能循环）。

**验证方式（推荐）**：星座通道有一个**直接可观测**的证据 —— `rep['dev_nodes']`
（真正生效的星位数）。双剑 ⇒ `dev_nodes=0`；剑+斧 ⇒ `1`；不传 ⇒ `1`。
比"比面板数字"干净得多（合成用例下面板恒为 0，比不出差异）。

**推论（重要）**：**同一个东西有几条输入通道，门控就要有几处** ——
先 grep 出「这个字段被谁读走」，再决定插在哪几处。
只改「看起来最像主路」的那一条，是这类 bug 的标准踩法。

---

## #84 ★★★ 同一个 `objfunc.score`，喂进去的 `rep` 来自**哪个函数**决定了口径

**症状**：技能轴的边际值表说「刺骨战吼拆 1 点损失 **0**」，而实测拆掉它 **−24.36%**。
一整批技能（刺骨战吼 / 血莽 / 集结战吼 / 不羁狂怒）全被标成「零收益」。

**根因**：`tools/objfunc.py::score(rep, 'total')` 读的是 `rep['dps']` ——
而这个键在**两种返回里根本不是同一个东西**：

| 来源 | `rep['dps']` = | 含减抗在哪 |
|---|---|---|
| `tools/plan_dps.py::dps_of()` | **`dps_vs`（含减抗）** | 就是它自己 |
| `gd/rotation.py::final_report()` | **面板（每击伤害）** | `rep['vs']['dps_vs']` |

`final_report` 的 `rep['dps']` 是**面板**：**不含**命中率、暴击、敌方抗性、敌方护甲。
于是所有「只在实战层生效」的东西全部**静默归零**：

- **减抗技能**（刺骨战吼 B 族 −30 穿刺/−40 流血）—— 只影响 `vs`；
- **OA / DA 增益**（集结战吼、不羁狂怒、阿玛托克契约）—— 只影响命中/暴击（在 `vs`）；
- 任何敌方侧的乘区。

**实测（Sam 存档，`tier` = 正确口径）**：

| 拆掉的技能 | 面板口径（工具在用） | **含减抗口径（真实）** |
|---|---|---|
| 刺骨战吼 `bonechillingcry1` | **0.00%** | **−24.36%** |
| 血莽 `werewolf3` | **0.00%** | **−4.72%** |
| 集结战吼 `rallyingcry1` | **0.00%** | **−2.99%** |
| 不羁狂怒 `passive03` | **0.00%** | **−2.97%** |
| 阿玛托克契约 `amatokpact1` | −3.84% | −3.63% |
| 刀灵 `summon_bladespirit` | 0.00% | 0.00%（宠物本就不在模型内，**只有这条是真的 0**） |

⇒ 技能轴会**主动建议退掉全角色最值钱的减抗技能**。这是「模型错误 ⇒ 错误决策」
的典型形态（与 #78 跃击、#82 门控漏接同族），而且**方向最危险**：
它不是多算收益，而是**把最大收益项当废物**。

**当前修复状态（2026-09-22 盘点）**：

| 工具 | 口径 | 状态 |
|---|---|---|
| `tools/plan_dps.py`（装备轴主口径） | 含减抗 ✅ | 正确（`dps_of` 的 `dps` 就是 `dps_vs`） |
| `tools/tune_attrs.py`（属性轴） | 含减抗 ✅ | **已修**（加了同形 shim，自检 `[58]` 守着） |
| `tools/tune_skills.py`（技能轴） | 面板 ❌ | **未修** ⇒ 边际值表整张不可信 |
| `tools/tune_devotion.py`（星座轴） | 面板 ❌ | **未修** |
| `tools/eval_build_variants.py`（变体终评） | 面板 ❌ | **已修** |

⚠ **`tools/gt_regress.py` 不在名单里**（初版我写错了）：它**不经过 `objfunc`**，
而是刻意取 `rep['dps']` / `rep['dps_real']` 去**对拍 GT 网站显示的面板值**
（容差 2%）—— 那里用面板是**设计意图**，改成 `vs` 反而会破坏它与 GT 的对齐。

**修法（2026-09-22 已实施）**：改在**唯一的那一处** —— `objfunc.score` 的 `total` 分支
改用**文件里本来就有的** `_total_vs(rep)`（它兼容两种入参）：

```python
if m == 'total':
    s = _total_vs(rep)      # ← 原来是 float(rep['dps'])
```

一处改动修掉**全部**走 `objfunc` 的工具，而且：

- 对 `dps_of` 的返回**逐位不变**（它的 `dps` 本来就 == `dps_vs`，实测 163,959 ↔ 163,959）
  ⇒ **装备轴零漂移**，锚点 v14 不动；
- 对 `final_report` 的返回自动取实战层；
- 非 `total` 目标（`bucket()`）**本来就对**（读 `type_rows.dps_vs`）—— 这个 bug
  只在**默认目标**上生效过，所以覆盖面最大。

★ 教训：**同一个键名在两种返回里语义不同**，就一定会有人踩。
修的时候要问「**有没有一个已存在的、两种入参都兼容的取值器**」——
本例 `_total_vs()` 早就写好了（给 `+` 变体用的），却没人拿它当 `total` 的取值器。

**★★ 推论（通用）**：「同一个键名在不同函数里语义不同」是最贵的一类口径坑。
判断方法：**问「这个数字是在哪一层被消费的」** ——
OA/DA/减抗/护甲/命中/暴击全部在 `vs` 层，只在 `dps` 层做搜索的工具**看不到它们**。
`selftest [58]` 已有「精确模式 vs `dps_of` 逐位一致」的断言模式，可直接复制到另三处。

---

## #85 优化器把 `collect_char()` 的结果**冻结**了 ⇒ 减抗永远不随等级变化

**症状**：把目标口径修对（#84）之后，`tune_skills` 里「刺骨战吼 拆 1 点」仍然接近 0。

**根因**：`tune_skills` / `tune_devotion` / `eval_build_variants` 三处都在**模块级**
算一次 `RR.collect_char(...)` 然后塞进固定的 kwargs：

```python
_rr_pack, _, _ = RR.collect_char(_C['folded'], EFF_ALL, _C['db'])
RRKW = dict(rr=_rr_pack, enemy=…)        # ← 之后每次试验都用同一个 rr
```

于是**改减抗技能 / 减抗星位的等级，永远不体现在评分里**：

```
刺骨战吼 `defensivePierce` 逐级 = [-4, -7, -10, -13, -16, …, -30, …]（22 级）
⇒ lv1 穿刺减抗 12% ／ lv11 36% ／ lv12 38%
⇒ 冻结后「拆 1 点」损失恒为 **0**（实测修前 0，修后 **−1.78%**）
```

★ **为什么这个 bug 会「藏」起来**：RR 只作用于 `vs` 层，**面板（`rep['dps']`）不含
RR** —— 所以 #84 没修之前，你怎么看面板都发现不了它。#84 与 #85 是**同一对**：
**先把目标挪到 `vs` 层，冻结的 RR 才会暴露。**

**修法**：`gd/rr.py` 新增 `RRCache`（`pack(levels, extra=None)`）：

- 只把 `levels` / `extra` 里**带减抗字段的那些记录**的等级拼成 key；
- key 变了才重算 `collect_char`，否则命中缓存 ⇒ 与旧实现同速；
- `extra` 用来接**第二条输入通道**（`devotion_levels`）—— 星座星位里也有 B 族减抗
  （「暗杀者的标记」`defensivePierce −8`），不接就等于星座优化器对自家减抗星位失明。

三处都把 `rr=` 从固定 kwargs 里拿出来，改成 `rr=_RRC.pack(eff[, dev])`。
（`tune_attrs` 不用改 —— 属性点不影响减抗，冻结在那里是**正确的**。）

**验证要点（自检 `[62]` 已锁）**：`RRCache.pack` 与 `collect_char` 直算**逐位一致**；
同一等级集**幂等**（不重算）；动**无减抗**的技能**不触发重算**（性能守卫）；
动**减抗**技能必重算且值变化（实测 pierce 38 → 12）。

---

## ★ 附：#84 + #85 修好之后的**新结论**（2026-09-22）

| 轴 | 修前工具报 | **修后（正确口径）** |
|---|---|---|
| 技能点（换位收敛） | 面板 +0.16% | **评分 +1.26%**（163,959 → 166,032；血源苏醒 −3 / 贪噬 +3） |
| 技能点：刺骨战吼 拆 1 点 | **0** | **−1.78%**（2,917 分） |
| 技能点：不羁狂怒 拆 1 点 | **0** | −0.57% |
| 星座（重排） | 面板口径 | 评分口径：**148,519 → 155,308（+4.57%）**（推荐不变） |
| 星座：苦难 / 铁锤 | 0 | **仍然 0**（在实战层也确实是零收益，结论稳） |
| 装备轴 | 含减抗 | **不变**（零漂移，锚点 v14 不动） |

★ **面板口径的系统性低估倍数 ≈ 8×**（技能轴同一轮：面板 +0.16% ／ 评分 +1.26%）。
⇒ 以后看这三轴的输出，**只认「评分」列**；`tune_skills` 的输出已改成评分优先。

---

## #86 ★★★ GT 库的 `k` = **等级要求**、`l` = **Class（槽位类别码）** —— 别把 `l` 当等级

**真源**：`data/cache/itemdb.js` 里 GT 自己的键字典：

```js
{ levelRequirement: "k",           // ← 等级要求
  Class:            "l",           // ← **槽位类别码**
  WeaponMelee_Sword: "c20",        // ← 所以 c20 = 剑
  armorClassification: "g", … }
```

⇒ `c20` 是**剑**、`c10` 是头、`c40` 是戒指、`c45` 是镶嵌物；
**组件的 `cls` = 「它能装的底材码集」**（判据 `base.l ∈ item.cls`，见 #68）。

**踩到的坑（两处）**：

1. `gd/opt.py::_req_lvl()` 旧实现把 `l` 用 `c(\d+)` 解析**当等级**（注释还写着
   「`l` 形如 `c20` 表示需 20 级」）⇒ 恒返回 10~50 的「槽位码数字」，
   对 `_MAX_ILVL` 的过滤**永远放行**、等级闸门形同虚设（实测 `it729`：旧 20 ／ 真实 `k`=50）。
   已修：直接读 `k`（回退 `itemLevel`）。修后 `POOL_BASE`/`POOL_WPN` 规模不变（778/6）。
2. `tools/plan_legal.py` 只判**槽位**、**不判等级** ⇒ 它会给「角色装不上」的方案
   报「全部合法」。已补 `check_level()` + `--level` / `--char`（不传会**醒目警告**，
   不静默跳过 —— 这是 #82 的形态）。

**★ 我本人踩了这个坑（记下来当反面教材）**：见 #87 末尾——
把「刀刃之印」(`k=75`) 当成 73 级的可达收益推荐出去，实测**游戏里装不上**。

**推论（通用）**：**「这个数字是等级还是类别」必须去源头确认**，别信拟合出来的注释。
`k` 与 `l` 都是字母，含义天差地别。

---

## #87 ★★ 武器镶嵌物的候选池**只有硬编码 2 项** + 抗性排序把输出件全丢

**症状**：`gd auto --extreme` 在「武器镶嵌物」这一维上**几乎没有搜索空间**，
搜索结果里武器组件像是**没被优化过**。

**根因（两条叠加）**：

1. 武器槽的候选池是**硬编码两项**：`gd/opt.py::POOL_CWPN = ['it2878', 'it2849']`
   （恶毒尖刺 + 银白灌注）。
2. 通用的 `auto_comp_pool()`（13 槽）在 **`--goal super`** 下按 **`_pool_score()`**
   排序，而 `_pool_score` 在非 `dmg` 目标下退成「**抗性总点**」，并且
   `if sc > 0` ⇒ **零抗性的纯输出件被整批丢掉**。
   实测 `auto_comp_pool(32)`：**主手只剩 1 项**（`it2849` 银白灌注 +15 抗）。

⇒ 池里既没有那 25 项「等级+槽位都合法」的武器组件，也没有任何可比较的输出件。

**实测规模（Sam lv73，剑 c20 + 斧 c21）**：合法可用（`cls` 含两个武器码 且 `k` ≤ 73）
的镶嵌物 = **25 项**；其中 `it2878` 恶毒尖刺确实是最优 —— 也就是说
**硬编码那 2 项里恰好有最优解，属于运气，不是设计**。

**修法（2026-09-22）**：新增 `gd/opt.py::auto_wpn_comp_pool()` ——
按「**等级合法（`k` ≤ `GD_MAX_ILVL`）+ 槽位合法（`cls ∩ WEAPON_CODES ≠ ∅`）+ 记录可解析**」
筛出候选，再用**与 goal 无关的输出代理** `_comp_off_score()` 降序取前
`GD_COMP_WPN_TOPN`（默认 40），并入 `POOL_COMP['主手'/'副手']`。

**★★ 反面教材（我自己的误判，务必读）**：我在没查 `k` 的情况下扫了「武器合法」的
43 个组件（**误用「物品等级」当门槛**），得出「换成 `it8898` 刀刃之印能 +3.65%」，
还把方案写进 `headroom_best.json`（+11.41%）。**实际 `it8898` 的 `k=75` ⇒ 73 级装不上。**
`--extreme` 搜索选的 `it2878` 才是对的（它在池里，且确实最优）。
⇒ 教训：**推荐任何一件装备/组件前，先过 `plan_legal.py --char <角色>`（含等级检查）。**

---

## #88 ★★★ `gd auto --extreme` 为何要跑十几分钟 —— **性能归因（实测）**

**总账**：一次 `--extreme`（32 链）实测 **10.02 CPU-小时 / 33 进程**，单链约 **18.8 分钟**墙钟。
换算评估次数（单次 3.68 ms）⇒ 约 **1000 万次**赋值。

**证据（仪器化实测，机器空闲时）**

| 项 | 实测值 |
|---|---|
| 单次 `plan_dps.dps_of` | **3.68 ms**（= 273 次/秒） |
| 其中 `final_report` | **~70%**（纯 Python 字典热循环：单次 ~16,000 次 `dict.get`/`setdefault`、153 次 `parse_mm`、1,762 次 `re.match`） |
| 其中 `load_char` | **~21%**（每次换装重折叠 14 槽 + 重算套装/`skill_plus`/转化/`skill_mods`） |
| 单链固定开销 | **~32 s**（建池 23.1 s + 满抗 ILP 31.9 s，有重叠） |
| LNS 每轮真实评估 | **126 次**（`eval_cap=240` 上限之下） |
| LNS 30 轮（串行） | **13.7 s** ／ 3,784 次评估 |
| LNS 30 轮（`--procs 16`） | **1.8 s** —— **7.6×**，结果逐位一致（167,681） |

**归因（按贡献排序）**

| # | 项 | 规模 |
|---|---|---|
| ① | **32 条链各算一遍「候选池 + 满抗解」**（与扰动无关，纯重复） | 32 × ~30 s ≈ **16 CPU-分钟** |
| ② | **贪心（`local_search`）**：单槽 `max_per_slot`(200)×14×`passes`(4) = 11,200 ／ 双槽 C(14,2)×`pair_k²`(16)×4 = 5,824 | ~17,000 次/链 × 32 = **54 万次** |
| ③ | **LNS**：`max_iters`(400) × 126 次 = 50,400/链 | 32 链 = **161 万次** |
| ④ | **迭代内 126 次评估在 `--extreme` 下全部串行** —— `--procs`（迭代内并行）与 `--chains`（链级并行）**互斥** | 白丢 **7.6×** |

**★★ 本次「突然变慢 2.5×」的直接原因（我自己的改动）**
把武器镶嵌物候选池 2 → 25 项后，武器槽的**可行组合**从 2²=4 涨到 25²=625（截断在
`eval_cap=240`）⇒ 原本几乎免费的迭代现在**每轮都吃满上限** ⇒ 每链评估数 ×2.6。
⇒ 教训：**扩大候选池必须同时收紧「每轮评估上限」**，否则是线性/平方级的墙钟代价。

**优化清单（按性价比）**

| # | 方案 | 改动量 | 预期 |
|---|---|---|---|
| **O1** | **`--procs 16`（单链 + 迭代内并行）** | 零改动（已验证） | LNS 段 **7.6×** |
| **O2** | 放开 `chains × procs ≤ 物理核`（如 `--chains 4 --procs 4`） | ~10 行 | 多样性 + 并行兼得 |
| **O3** | 候选池 / 满抗解**只算一次**，链间共享 | ~20 行 | −16 CPU-分钟 |
| **O4** | **自适应 `eval_cap`**：按代理分衰减定 N（不固定 240） | ~5 行 | 池变大时**不再**变慢 |
| **O5** | `parse_mm` / `_at` 按**字段名**缓存正则解析 | ~10 行 | 单次 −10~15% |
| **O6** | `load_char` **增量折叠**（只换 k 槽 ⇒ O(k)） | ~30 行 | 单次 −20% |
| **O7** | 加「连续 N 轮无改善即停」+ 削减 `--extreme` 默认预算 | ~5 行 | 通常 2~5× |

**推论（通用）**：优化器的墙钟 = **评估次数 × 单次成本 ÷ 并行效率**。
三个因子都要看；只调其中一个是常见的误判（本例：并行效率只有 1/32 —— 32 链各串行）。
**先测「每秒多少次评估」与「每轮多少次评估」**，再决定往哪使劲。

---

## #89 ★★★ 优化器性能：瓶颈是**并行效率**，不是单次计算（实测归因 + 重构）

**基线（旧架构，`--extreme`）**：**10.02 CPU-小时 / 33 进程 / 单链墙钟 18.8 分钟**。

**三个因子的实测值**（`墙钟 = 评估次数 × 单次成本 ÷ 并行效率`）：

| 因子 | 实测 | 说明 |
|---|---|---|
| 单次 `plan_dps.dps_of` | **3.81 ms**（262 次/秒） | ⚠ 别在机器被 32 进程打满时测（会测成 28 ms，夸大 7.6×） |
| 单链评估次数 | ~11.6 万 | 贪心 ~1.7 万 + LNS 400×240 = 9.6 万 |
| **并行效率** | **1/32** | ★ 就是这个 |

**根因：`--procs`（链内邻域并行）与 `--chains`（链级并行）被设成互斥。**
旧代码里写死「显式 `--procs` 就把 `--chains` 压成 1」，理由是「N×procs 会超物理核」。
于是 `--extreme` 在 32 核机器上跑出的配置是 **32 条链 × 每条链内部完全串行** ——
32 个进程各自只用 1 核，而**每轮那 126~240 次互相独立的邻域评估一次都没并行过**。

**修复（2026-09-22）**：

1. **统一分配器** `autobuild._alloc_parallel(phys, chains, procs)`：总进程预算 = **物理核**，
   在「链数」与「每链进程数」之间分配，并保证 `chains × procs ≤ 物理核`。
   · 都不传 ⇒ `procs = min(物理核, 4)`、`chains = 物理核 // procs`（16 核 ⇒ **4×4**）
   · `--chains 1` ⇒ 自动给 16 进程（最省墙钟）· `--chains 16` ⇒ 每链 1 进程（旧行为）
2. **贪心也并行**：抽出统一入口 `tune_dps.eval_trials(trials, real_dps, par, memo, slots)`，
   `local_search` 的单槽阶段（每槽最多 200 个候选）与双槽阶段（槽对 × `pair_k²`）
   全部**整批**走进程池。旧实现只有 LNS 并行，贪心全程串行 —— 而贪心占单链 ~1/3 时间。
3. **链这层不能用 `mp.Pool`** —— 见 #90（daemon 不能有子进程）。

**实测（微基准，同一进程内、同参数、同一份存档）**：

| 阶段 | 串行 | 并行 8 进程 | 加速 | 结果一致性 |
|---|---|---|---|---|
| 贪心 `local_search` | 3.0 s | **0.5 s** | **6.2×** | 559,829 ↔ 559,829（**Δ0**） |
| LNS `lns_search` | 1.6 s | **0.4 s** | **4.3×** | 536,038 ↔ 536,038（**Δ0**） |

邻域并行的加速比（批量 64）：`2 进程 1.9× ｜ 4 进程 3.3× ｜ 8 进程 6.8× ｜ 16 进程 10.2×`
—— **边际收益递减**，所以默认 `procs` 封在 8，剩下的核留给「多链多样性」。

**单次评估降本（P2，另 −14%）**：

- `parse_mm` 的**字段名 → 分类**永久缓存 `_MM_CLS`（原来每次评估 ~1,300 次 `re.match`）；
- `fields_with_buff` 记忆化（单次评估调它 ~230 次、重复率约 4×；
  ⚠ 返回**浅拷贝** —— 有调用点会往里注入 `skillChanceWeight`）；
- 归因账本 `slot_pct` / `slot_flat` **改为按需**（`GD_ATTRIB=0`）：
  它是「伤害循环文档」的报告产物，却在**每次评估**里都算一遍。
  ⚠ **但实测只省 1%** —— 我原先按 cProfile 的 `setdefault` 计数（5,321 次/评估）
  推断它是最大项，**归因错了**：那 5,321 次主要来自 `dmg.py` / 累加器的
  `setdefault`，账本本身只占 ~16 次。留着开关（零成本）但不指望它。
  两个**虚拟槽**（`套装加成` / `星座节点`）的账本**必须照记** ——
  `base_parts` 要从中减掉套装，否则归因串味。

**★★ 端到端验收（`gd auto Sam --extreme --goal super`，16 物理核，满抗 1070/1070）**

| 架构 | 链 × 进程 | 墙钟 | 结果（含减抗） | 相对存档 |
|---|---|---|---|---|
| **旧** | 32 × 1（每条链内部串行） | **1128 s（18.8 min）** | 176,235 | +7.5% |
| 新 `--chains 4 --procs 4` | 4 × 4 | **113 s** | **175,726** | **+7.2%** |
| 新 `--chains 2 --procs 8` | 2 × 8 | **72 s** | 174,893 | +6.7% |
| **新默认（自动 4×4）** | 4 × 4 | **113 s** | **175,726** | **+7.2%** |

⇒ **10× 提速、质量少 0.3%**；把每链进程数从 8 降到 4、多开两条链，
多花 41 s 换回 **+0.5% DPS**，所以默认取 **4×4**。

固定开销（与并行度无关）：建池 2.3 s + 满抗 ILP **18.6 s** + 报告子进程约 5 s ≈ 26 s。

**零漂移验证**：`GD_ATTRIB` 未设（报告口径）与 `=0`（搜索口径）下
`dps_of('Sam')` 的面板 / 实战 / 含减抗 / OA / DA **逐位相同**
（81,363 ｜ 98,873 ｜ 163,959 ｜ 2,092.2 ｜ 1,404.5 = 锚点 v14）。
报告子进程由 `autobuild._report_env()` 显式把 `GD_ATTRIB` 改回 `1`。

**★ 方法论（最该记住的一条）**：**别用整条链路做性能对照。**
`gd auto` 有 ~55 s 固定开销（建池 + 满抗 ILP）和一次报告子进程，会把被测对象的差异淹没
—— 实测为此白跑了三轮、耗掉好几分钟。**只测改动的那两个函数**，6.6 秒就拿到结论。

---

## #90 ★★ 进程池**不能嵌套在 `mp.Pool` 里** —— daemon 进程不许有子进程

**症状**：`--chains 2 --procs 8` 启动后**整轮搜索静默挂死** ——
33 个 python 进程全在 **0~2% CPU** 空转，5 分钟零输出、零中间产物。

**根因**：`multiprocessing.Pool` 的 worker 是 **daemon 进程**，而 Python 明令
daemon 进程**不许创建子进程**；链内要建邻域评估池（`ParEval` → `ProcessPoolExecutor`）
就会抛：

```
AssertionError: daemonic processes are not allowed to have children
```

worker 抛异常后直接死掉，而**主进程在 `imap_unordered` 上永远等不到结果** ⇒ 挂死。
（`imap_unordered` 其实会把异常抛回来，但链 worker 的 initializer 阶段就崩，
加上 `except` 兜底路径又去跑单链，现场表现为「不动」而不是「报错」。）

**修法**：链这一层改用**显式非 daemon 进程** + `Queue` 收结果：

```python
pr = ctx.Process(target=_chain_worker, args=(payload, q, char, arch, procs, seed_sol))
pr.daemon = False          # ★ 关键：daemon=True 会连带禁止它的邻域池
pr.start()
```

配套要做对的三件事：
1. `Queue.get(timeout=5)` + 「所有子进程都不存活却没收齐 ⇒ 跳出」——
   否则**静默死掉的链**会让主进程无限等（这正是挂死的直接原因）；
2. 收尾 `join(timeout)` 后对仍存活的 `terminate()`；
3. worker 里 `except BaseException` 把错误**回传**（含 traceback 尾巴），
   而不是让它烂在子进程里。

**推论（通用）**：**「谁的子进程能再开子进程」是硬约束**。
`mp.Pool` 适合**叶子**任务；要让子任务自己再并行，必须用 `Process(daemon=False)`。

---

## #91 ★★★ 非 daemon 子进程 + 只杀自己 = **孤儿进程越堆越多**

**症状**（用户报）：「后台卡一大堆没占用的 py 进程」—— `tasklist` 里 python 越积越多，
每个 **0% CPU**、内存占着不放。

**根因**：优化器的进程树是**两层**：

```
gd auto（父）
  └─ 链进程 × chains          ← daemon=False（#90：链内还要建邻域池）
       └─ ParEval worker × procs   ← 每链一层
```

两个独立缺陷叠加：

1. **`Process.terminate()` / `taskkill /F /PID`（不带 `/T`）只杀一个进程。**
   Windows 上**子进程不随父进程退出而结束**（不像 POSIX 的进程组）⇒
   父被强杀后，整棵子树**变成孤儿**：还活着、但没有任何人再管它。
2. **链进程内的 `ParEval` 从没被显式关闭**。正常结束靠 `concurrent.futures` 的
   `atexit` 兜底能回收，但**一旦被强杀，`atexit` 不会跑** ⇒ 每链 `procs` 个 worker 全留下。

**修法（三道，缺一不可）**：

| # | 位置 | 做法 |
|---|---|---|
| 1 | `_chain_worker` | `try/finally` 里**显式 `par.close()`** —— 把「靠 atexit 兜底」的窗口关掉 |
| 2 | `_run_chains` 收尾 | 一律走 **`_kill_tree(pid)`**（`taskkill /T` 或 psutil `children(recursive=True)`），**绝不用 `terminate()`** |
| 3 | 新增 `tools/kill_orphans.py` | **第四道保险**：列出并（`--yes` 时）清理「父进程已死」的 python 孤儿 |

★ 第 3 条同时是**运维工具**：历史上被强杀留下的孤儿，靠它一次清干净。
默认**只列不杀**（`--yes` 才动手），只用「父进程已死」这一条硬判据，避免误伤。

**验证**：改造后跑完整 `--extreme`，结束后立刻 `kill_orphans` 检查 ⇒
**python 进程 0 个**（此前同类场景会留下 16~20 个）。

**推论（通用）**：**进程树有两层时，「杀」必须是树杀，且每一层都要自己负责清理自己的子进程。**
`terminate()` 的语义是「杀这一个」，在 Windows 上等于「制造孤儿」。

---

## ★ 附：`--extreme` 的三个**零代码**旋钮（实测，2026-09-22）

| 配置 | 墙钟 | 结果（含减抗） | 相对存档 |
|---|---|---|---|
| `--extreme`（旧预设） | 113 s | 175,726 | +7.2% |
| `--ilp-gap 1e-2` | 102.8 s | 175,726 | +7.2%（**省 10 s、结果相同**） |
| `--lns-patience 120` | 78.9 s | 174,893 | +6.7%（早停，略损质量） |
| **`--lns-cap 120`** | **67.8 s** | **175,726** | **+7.2%** ★ 白赚 −40% |
| **新预设（`lns_cap=120`，其它不变）** | **80.1 s** | **175,726** | **+7.2%** |

**`--lns-cap` 从 240 降到 120：墙钟 −40%、结果逐位相同。**
原因：邻域内按线性代理降序排好后，**前 120 个已覆盖全部真正竞争者**，后 120 个纯陪跑。
已把它写进 `--extreme` 预设（显式传 `--lns-cap N` 仍优先）。

⇒ `--extreme` 累计：**1128 s（旧架构）→ 80.1 s ≈ 14×**，结果 175,726（+7.2%）。

### ⚠ 单次评估还剩多少空间（实测，别再靠 cProfile 猜）
| 段 | 耗时 | 占比 |
|---|---|---|
| `load_char` | 0.62 ms | 19% |
| `final_report` | 2.31 ms | 71% |
| 其余（attrs/rr/wps） | 0.32 ms | 10% |

`final_report` 内部的**函数级**实测（包装计时）：
`dmg.convert` **1.24 ms（76 次/评估）** ／ 其内 `_apply_one` **920 次**、
`_conv_matches` **1,684 次**、`_targets` 316 次；`skill_group` 0.22 ／ `parse_mm` 0.18。

⇒ **「转伤链」占单次评估 ~30%**，是下一块可动的肉（预编译「来源类型 → 目标类型」表，
把 1,684 次 `_conv_matches` 换成查表）。**上限约 −15% 单次成本**。
★ 而「只依赖 `levels` 的聚合」（`sk_pct` / 光环 / `skill_children` / `enemy_da_cut`）
实测只有 **0.23 ms = 7%** —— 不值得为它做缓存（我原本以为是最大项，**测了才知道**）。

---

## #92 ★★ 转伤链 `convert()` 预编译查表（**−8.2% 单次评估**）+ 我第三次归因错误

**背景**：单次评估 3.4 ms，装备搜索要跑 ~10 万次 ⇒ 每降 1% 都值钱。

**实测到的结构**（`tools/_bench_conv.py`，包装 `convert` 统计）：

| 项 | 实测 |
|---|---|
| `convert()` 调用 | **76 次/评估** |
| 其中的**步骤实例** | **152 个/评估** |
| **唯一步骤签名** | **1 个**（同一次 `_make_hit` 里主路径 + 5 组分来源账本用**同一份**配置） |
| 源数/`convert` | 5.5 ｜ 规则数/步骤 **2.0** |

⇒ **理论复用 456×**。旧实现每调一次都要对「每个来源 × 每条规则」重算
`_conv_matches` + `_targets`。

**改法**（`gd/dmg.py`）：把每步规则预编译成「**来源类型 → (目标表, 总量)**」：

```python
_rel_tbl(convs)      # 按**步骤签名**全局缓存；同签名只编译一次（实测真实负载只有 1 个签名）
rel, tot = _rel_tbl(step)[s.type]      # 原来：循环规则 + _conv_matches + _targets
```

- 未知来源类型**就地补算并写回表** ⇒ 行为不变（只放弃该类型的缓存优势）；
- 规则里混进不可哈希的东西 ⇒ **不缓存、照旧算**（零风险回退）；
- 缓存有上限（4096，超了 `clear()`）；
- **算术一行没动**（`keep` / `share` / `scaled` / `Source(...)` 参数与顺序都不变）。

**验证（零漂移是硬指标）**：
- **fuzz 4000 例**（随机来源 × 随机规则 × 随机 pierce/武器伤害，含空步骤、
  `elemental` 源/目标、>100% 分摊、DoT）：与旧实现 **逐位一致，0 例差异**；
- **同进程 A/B**（把旧实现临时挂回 `DMG.convert`，同一预热状态）：
  **3.310 ms → 3.039 ms = −8.2%**，含减抗结果 **176,235 ↔ 176,235 逐位一致**；
- 锚点 v14 复核：面板 81,363 ｜ 含减抗 163,959 ✓；
- 自检 `[64]`：内嵌**旧实现的参考复刻**跑 240 例 fuzz + 4 条缓存契约断言。

**端到端效果（诚实说明）**：改造后 `--extreme` 跑出 **82.1 s / 175,726（+7.2%）** ——
落在最近几轮 **67.8 ~ 82.1 s** 的噪声带里（光 ILP 就在 13.4 ~ 18.6 s 之间跳）。
按「搜索段 ≈ 56 s × 8.2% ≈ −4.6 s」估算，收益**确实存在但被固定开销与运行间方差淹没**。
⇒ 只能声称**受控 A/B 的 −8.2%**，**不能**声称端到端快了 8%。
（下一块真正的肉是**固定开销 ~26 s = 32%**，其中满抗 ILP 独占 18.6 s。）

**⚠⚠ 我第三次归因错误（必须记）**：
我先前用「包装计时」测出 `dmg.convert` = 1.24 ms/评估、占单次 **30%**，
据此判断「转伤链是最大的一块」。**那个 1.24 ms 主要是给 2,920 个内部调用
（`_apply_one` 920 + `_conv_matches` 1684 + `_targets` 316）**打包装**的开销，
不是 `convert` 本身。真实收益是 **8.2%（0.27 ms）**，不是 15%。

⇒ **教训统一为一条**：**「包装计时」只能用来找『谁被调用得最多』，不能直接当耗时占比** ——
  包装本身 ~0.4 µs/次 × 几千次 = 毫秒级，会把小函数吹成大热点。
  要拿**准确占比**，只能做**同进程开关 A/B**（把新旧实现挂回去各跑一遍）。
  （前两次：cProfile 的 `setdefault` 计数 → 误判「归因账本」；包装计时 → 误判「转伤链」。）

## #93 ★★★ 满抗 ILP 的 **(抗性, 目标) 支配剪枝**（变量 −66%、求解 −62%，**最优值逐位不变**）

**上一轮留下的坐标**：`--extreme` 77.6 s，其中**固定开销**里满抗 ILP 独占 ~13.7 s
（建池 1.27 + 建矩阵 0.11 + **HiGHS MIP 10.98**）。所以「下一块肉」就是这 10.98 s。

**先做归因（同进程、同 candmap 扫参数矩阵，`tools/bench_ilp_opts.py`）**——别再猜：

| 配置 | 求解耗时 | 结果 |
|---|---|---|
| gap 1e-3 / 5e-3 / 1e-2 / 3e-2 / 1e-1 | 9.5 ~ 10.2 s | **全部 Optimal、目标 3792.586、解逐位相同** |
| `mip_start`（把最优解当起点喂回去） | 8.43 s | 同解（−14%，但要先花 9 s 拿到它 ⇒ 净亏） |
| `mip_heuristic_effort=0.2` | 9.50 s | 同解，无收益 |
| `mip_detect_symmetry=True` | 3.48 s（剪枝后） | 无收益 |
| `presolve=off` | 6.26 s | 目标同但**解不同**（另一个最优）⇒ 有风险，弃用 |
| ★ **(抗性, 目标) 支配剪枝** | **3.45 s** | **目标与解逐位相同** |

⚠ **`gap` 根本不是瓶颈**：HiGHS 是把**最优性证明**跑完才停（MIP gap 归零），
所以把 1e-3 放到 1e-1 一点没快 —— 上一轮「`--ilp-gap 1e-2` 省 10 s」的说法
落在 67.8~82.1 s 的**运行间噪声带**里，不是真收益。**这种结论只能靠同进程矩阵。**

**判据（为什么是精确的）**：候选 A 支配候选 B ⇔
① `res_A ≥ res_B`（9 条抗性逐维）② `obj_A ≥ obj_B`
③ **不允许「A 是双手而 B 不是」**（否则换过去会凭空多出「副手必须为空」的约束）。
此时任何用到 B 的解都能把 B **就地换成 A**：抗性只增不减 ⇒ 可行性保持；目标不降 ⇒ 最优值不变。

★ 与 `gd/opt.py:_skyline` 的区别：skyline 要求**输出向量的每一维**都不劣，
本判据只要求**目标（输出各维的线性组合）**不劣 ⇒ **严格更强**，且仍然精确。

```python
# tools/ilp_res.py
_dom_core(rows, order, is2h=None, fixed_first=False)   # 预分配 K + runmax 快速路径
dom_reduce(O, candmap) -> 新 candmap                    # 逐槽；内容指纹落盘缓存
# cap_search 里已接；GD_ILP_DOM=0 可关掉；CLI 有 --no-dom
```

**两个必须记住的实现细节**：
- **快速路径只影响「剪得多不多」，不影响正确性**：`(ri[:ndom] > runmax).any()` 命中 ⇒
  该候选在某个维度刷新了纪录 ⇒ 不可能被支配 ⇒ 直接留。误判成「留」只是少剪，
  **永远不会误删**（判据方向是「留 ⊇ 真 Pareto 前沿」，自检 [65] 就在对这个）。
- **副手 ∅ 候选必须强制保留**（`fixed_first`）：双手一致性约束 `x[主手2H] ≤ x[副手∅]`
  全靠它，被剪掉会让双手武器无解。`build_candmap` 把它放在索引 0。

**实测（Sam lv73 / `--extreme` / 14 槽 / 38,267 变量）**：

| 项 | 前 | 后 |
|---|---|---|
| 变量 | 38,267 | **13,034（34.1%）** |
| 逐槽 | 主手 3224→**84** ｜ 副手 3230→**85** ｜ 戒指 1571→483 ｜ 圣物 23→10 | |
| 剪枝自身 | — | 0.63 s（**首跑**）／ **0.00 s（内容指纹落盘后的重跑，实测「缓存命中」）** |
| MIP 求解 | 9.65 s | **3.70 s（−62%）** |
| ILP 小计 | 13.7 s | **7.9 s** |
| **`gd auto --extreme` 全程** | **77.6 s** | **68.0 s（−12.4%）** |
| 结果 | 175,726 ｜ +7.2% ｜ 1070/1070 | **175,726 ｜ +7.2% ｜ 1070/1070（逐位相同）** |

**验证（零漂移是硬指标）**：① 同进程矩阵里剪枝解与全模型解**逐位相同**；
② 端到端重跑 ILP 目标 3792.586（= 改造前 3792.5855）、紫2 蓝8、DPS 175,726 全部不变；
③ 自检 `[65]` 八项：合成实例上「朴素 O(n²) 判据保留的必须全被保留」（只许多留不许删）、
**全模型 vs 剪枝模型 MIP 最优值逐位相同**、∅ 永不删、缓存 key 契约（同输入同 key / 换权重换 key）；
④ `sync_live` 全部一致；存档指纹 `ddb54cc16610` **未变**。

## #94 ★★★ 候选池支配剪枝 `_skyline` 是 **O(n²)** —— 单核 419 s，`gd auto` 全程 546 s

**症状（用户实测）**：「怎么这么慢　只有一个在跑」，任务管理器截图里**只有 1 个 python
吃 3% CPU**（= 32 逻辑核里的 1 核），其余进程全 0%。

**根因**：慢的不是 ILP（那才 16.9 s），而是它**前面的候选池建池**。
`gd auto` 的 `[满抗搜索] 建池` 计时打印：

```
[满抗搜索] 候选 头部 12600 ｜ 项链 6285 ｜ 胸甲 10481 ｜ … ｜ 副手 3740
[满抗搜索] 建池 419.5 s          ← 真凶
[ILP 支配剪枝] 未命中（已落盘） ｜ 1.28 s ｜ 变量 62743 → 20366
[ILP] Optimal ｜ 目标 3656.69 ｜ 16.93 s        ← 只占 4%
```

`gd/opt.py::_skyline` 对**每个候选扫全部 n 行** ⇒ O(n²)。而 lv76 / `--extreme` 的
**单槽原始候选可达 61,510**（= `GD_AUTO_TOPN` 150 × `GD_COMP_TOPN` 24 × `GD_AUG_TOPN` 14），
单槽 `_skyline` 就要 **~110 s**；14 槽合计 **419.5 s**，`gd auto` 全程 **546 s（9 分 06 秒）**。
★ 它是**纯单线程**的 —— 16 核在这个阶段一点忙都帮不上，这就是「只有一个在跑」。
（从文件缓存反推原始规模：`data/cache/prune/*.pkl` 存的 `keep` 长度 = 原始候选数。）

**改法（三条，结果**逐位不变**）**：

| # | 改动 | 为什么等价 |
|---|---|---|
| ① | 只与**已保留**的行比（不再扫全部 n 行） | 被丢弃的 j 若能支配 i，则支配 j 的那行也支配 i（**传递性**） |
| ② | **字典序降序**预排序（`np.lexsort`，主键 col0 降序） | 保证「支配者一定排在**被支配者之前**」，① 的等价性才成立 |
| ③ | `(ri > runmax).any()` 快速路径 + 512 行**分块、命中即停** | 某维刷新了已保留集的最大值 ⇒ 不可能被支配 ⇒ 直接留；分块早退省掉全量比较 |

```python
# gd/opt.py
order = _np.lexsort(tuple(-rows[:, d] for d in range(dm - 1, -1, -1)))   # ②
...
if cnt and not (ri > runmax).any():                                      # ③
    for a0 in range(0, cnt, 512):
        blk = K[a0:min(a0 + 512, cnt)]        # ★ 必须夹到 cnt，见下
        ...
```

`GD_SKYLINE=slow` 可回退旧实现做 A/B（未设 ⇒ 零漂移）；自检 `[66]` 做 fuzz 对拍 + 加速比守卫。

**★★ 差点带着一个 bug 上线 —— fuzz 对拍抓出来的**：
第一版写成 `blk = K[a0:a0 + 512]`，而 `K = np.empty_like(rows)` —— **越界切片会读到
未初始化的行**。那些垃圾值只要凑巧 ≥ 且 > 当前行，就会**误判为被支配而删掉一个真候选**。
在小规模 / 非负数据上完全不显形（零初始化恰好是最小值），**只在含负值的数据上暴露**：
`('rand', 1200, 7)` 三组种子全部 441 行不一致。修法就是 `min(a0 + 512, cnt)`。
⇒ **结论：`empty_like` 当缓冲区用时，任何切片都必须夹到 `cnt`。**
（这也再次说明「自检里必须有一个**与旧实现逐位对拍**的 fuzz」，否则这种 bug 会静默上线。）

**实测（Sam lv73 之外的新角色 `_xyf` lv76 / 士兵+夜刃 / 14 槽含武器）**：

| 项 | 前 | 后 |
|---|---|---|
| 单槽 `_skyline`（61,510 行） | ~110 s | **1.3 s** |
| **建池** | **419.5 s** | **21.9 s（19.2×）** |
| ILP 小计 | 439.9 s | 42.1 s |
| **`gd auto --extreme --with-weapon` 全程** | **546.1 s（9 分 06 秒）** | **147.5 s（2 分 28 秒，3.70×）** |
| 逐槽候选数 | 12600 ｜ 6285 ｜ 10481 ｜ … ｜ 3740 | **完全相同** |
| ILP 目标 ／ 真实 DPS | 3656.69 ／ 200,754（+216.5%） | **3656.69 ／ 200,754（逐位相同）** |

**验证**：① 合成 fuzz（4 种分布：随机 / 小整数 / 全同重复 / 结构化稀疏前沿 × 6 种规模 ×
3 组种子）与旧 O(n²) 版**逐位一致**；② 结构化 61,510×11 实测**旧 109.70 s / 新 1.300 s = 84×**；
③ **真冷缓存**端到端复跑（先 `rm` 掉全部 prune 缓存）：逐槽候选数与 DPS **逐位不变**；
④ `GD_SKYLINE=slow` 回退开关生效；⑤ 自检 517 项全绿。

**下一个还没动的地方**：建池现在剩的 21.9 s 几乎全是 `slot_cands()`（纯 Python 逐个展开
30 万条候选），同样**单线程**。要再快只能**按槽并行**（14 槽天然独立），
预计还能再砍 10 s 上下 —— 但比 419 s 已经不是主要矛盾了。

## #95 ★★★ 给**非 Sam** 角色跑四轴时，三个默认值会把「Sam 的方案 + 狼人形态」套到别人头上

**怎么发现的**：用户要「完整测一次：75 级 士兵+夜刃 的职业最优解」。
`gd auto _xyf --extreme --with-weapon` 报 **真实 DPS 200,754**；
紧接着用 `tools/coordinate_ascent.py _xyf --axes attr,skill,dev,gear` 跑四轴，
报告第一行却是：

```
起点：评分 12736.0 ｜ 面板 9030 ｜ 含减抗 12736 ｜ 形态 werewolf      ← ？？
```

**200,754 ↔ 12,736（差 15.8 倍）**，而且形态是狼人。三个写死的默认值叠加：

| 位置 | 写死的东西 | 后果 |
|---|---|---|
| `tools/plan_dps.py` / `tune_attrs.py` | `arch = arch or guess_arch(sk) or **'werewolf'**` | `guess_arch` **只认变身技能**（`werewolf1`/`wereraven1`）⇒ 非变身角色拿到 None ⇒ **一律当狼人**。而 `gd auto` 走的是 `autobuild.pick_archetype`（职业组合匹配）⇒ 判成 `soldier_nightblade` |
| `tune_skills.py` / `tune_devotion.py` | `PLAN = os.environ.get('PLAN', 'data/plans/**Sam_lv73_current.json**')` | 不显式传 ⇒ **拿 Sam 的装备方案当覆盖**去算别的角色。数字看着很正常（面板 89,217 / 评分 113,736），其实毫无关系 |
| 同上 | `ARCH = os.environ.get('ARCH', '**wolf_nightblade_fast**')` | 同上，形态也被套错 |
| `coordinate_ascent.run_axis` | 只传了 `CHAR`（和有时为空的 `ARCH`），**没传 `PLAN`** | 把上面三个坑全部引爆 |

★ 最坑的是它**不报错**：面板 89,217、评分 113,736、星点 58/58 全都是「看着很合理」的数字。

**修法**：

```python
# tools/plan_dps.py —— 统一形态链，并**保留 guess_arch 在前**（变身角色零漂移）
def resolve_arch(char, arch=''):
    return arch or guess_arch(sk) or arch_by_mastery(char) or 'werewolf'

def arch_by_mastery(char):        # 与 tools/autobuild.pick_archetype 同源；带记忆化
    _lv, _cls, _sk = autobuild.read_meta(char)
    return autobuild.pick_archetype(_cls, _sk, paths.load_json('archetypes.json'))
```

- `tune_attrs` 的 3 处 `or 'werewolf'` 全部改成 `or PD.arch_by_mastery(char) or 'werewolf'`；
- `coordinate_ascent` 新增 `_resolve_plan()`（`--plan` > `<char>_auto.json` >
  `<char>_current.json`）并**显式** `env['PLAN'] = plan`；
- `coordinate_ascent` 在 `run()` 里把 `args.arch = args.arch or PD.resolve_arch(char)`
  **定死再下传**（否则子轴继续吃 `wolf_nightblade_fast`）；
- 顺带补 `--gear-extreme`：装备轴默认强度与普通 `gd auto` 一致，双持/武器向形态
  要手动开 `--extreme --with-weapon`，否则装备轴的数字会**低于**主线那次，看着像「装备轴没用」。

**零漂移**：`resolve_arch('Sam')` 仍是 `werewolf`（`guess_arch` 先返回），
`resolve_arch('_xyf')` 才是 `soldier_nightblade`；`_xyf` 判对后面板 **63,424**，
与 `gd auto` 的「现状」逐位吻合。自检 `[67]` 八项钉住这条链 + 方案传递。

**顺带发现的存档异常（不是模型问题，但必须说）**：`_xyf` 存档里有 **58 个已点亮星座节点**，
而游戏上限是 **55**；星座轴的「亲和力自洽：否 —— 无名士兵 缺 Ascendant 15（实得 14）」
大概率就是因为这个角色是**改过的存档**。给这类存档算「最优解」时，
星座/点数结论要按「存档可能非法」来读，不能当官方上限下的答案。

## #96 ★★★ 「从 0 构建」的三个坑：形态不存在 / recipe 断链 / 变身形态恒为 0

**需求**：「75 级 亡灵(死灵 class08) + 狂战士(class10)，**不参考存档**，从 0 构建，
**模型自己出最优形态**（别问我选哪个）」。三件事全是坑：

### ① `{8,10}` 这个职业组合在形态表里**完全不存在**
带 8 的只有 `necro_inquisitor(8,7)`，带 10 的全是 `(10,4)` ⇒ `pick_archetype` 只能按
重叠度乱猜。补了 3 个：`necro_berserker`(human) / `wolf_necromancer`(werewolf) /
`raven_necromancer`(wereraven)，`core_skills` **按既有形态同一口径机械派生**
（主职业全树 + 副职业武器池/被动，不含宠物与 transmuter），`damage_weights` **故意不写**
（走默认权重，不预设伤害方向）。

★★ **必须写进 `data/archetypes_gdskill.json`（增补文件），不能直接改 `data/archetypes.json`**
—— 后者被 `migrate_from_archive.py` 的 `DATA` 表 `shutil.copy2` **无条件覆盖**。
（本文件开头就写着这条，我没照做，代价见 ③-b。）

### ② `gd recipe` —— 「三要素→完整 BD」的唯一入口 —— **断链**
```
$ python -m gd recipe soldier_nightblade 75
【③ 收敛循环】装备搜索 ⇄ 属性分配
   ✗ 搜索失败：
   ...python.exe: can't open file '...\data\gd_opt.py': [Errno 2] No such file or directory
```
它读 `HERE = data/`、调 `cd data && python gd_opt.py`、还要 `bd_gen.py` —— 全是
2026-09-17 那版的路径，现在的工具链（`python -m gd auto` + `tools/*`）里都不存在。
**六步流水线的「预算 → 配方」两步还有效**（`gd/alloc.*_budget(lv)` ← `data/level_table.json`，
lv75 = 技能 210 / 属性 84 / 虔诚 55），后四步已废。

### ③ 从 0 角色上，**变身形态恒为 DPS 0** —— 比形态必须先有技能分配
拿 `_Cx666`(lv28, class{8,10}) 当壳造了个 lv75 从 0 角色后：
- 人形态 `necro_berserker` → 34,424（全靠**装备授予技能**打）
- 狼人 / 鸦人形态 → **0.0**（形态门控按 `root_skills[0].granted` 裁技能栏，而角色
  **没学过 `werewolf1`/`wereraven1`** ⇒ 连装备 WPS 也被剔除 ⇒ 一个攻击都不剩）

**正解**：`tools/make_alloc.py <形态> <等级>` 先出加点 JSON，再用 `GD_SKILL_JSON`
注入整条链路。补上加点后：

| 形态 | 真实 DPS |
|---|---|
| **狼人 wolf_necromancer** | **51,402** ← 模型自己选出的最优 |
| 人形态 necro_berserker | 33,949 |
| 鸦人 raven_nightmancer | 33,711 |

⇒ **「形态自动决策」不是把 form 交给搜索，而是「先按形态出加点 → 各形态跑一遍 → 取最优」**
（`tools/sweep_arch.py`）。这也说明 `gd recipe` 的「③ 装备 ⇄ ④ 技能」**顺序是错的**，
技能加点必须**先于**形态比较。

### ④ 怎么做到「不参考存档」：`GD_SAVE` 重定向 + 造一个从 0 角色
`gd/paths.save_dir()` 认 `GD_SAVE`。`tools/make_zero_char.py` 拿现成角色当壳复制到临时目录，
按 block2 的**明文字段偏移表**定长改写：

| 字段 | 值 | 来源 |
|---|---|---|
| `level_in_bio` **＋文件头 level** | 75 | ★ **两处都要改**：`doc['level']` 读的是**文件头**那个，只改 block2 的 `level_in_bio` 会「改了但没生效」（实测踩到） |
| `attribute_points` / `skill_points` / `devotion_points` | 84 / 210 / 55 | `gd/alloc.*_budget(75)` ← `data/level_table.json` |
| `physique` / `cunning` / `spirit` | 50.0 | 未投点的基础值（每点 +8，实测 Sam 130−10×8=50 ✓） |
| 技能区 | 只留 `default/*` + 两个精通条（lv1） | `--learn` 可额外学形态技能 |

★★ **安全闸必须覆盖「真存档目录及其内部」**：第一版只比 `tgt == real`，于是
`--out <真存档目录>` 会让目标变成 `<真存档目录>/save`（在它**里面**）⇒ 闸门形同虚设，
自检的功能性验证当场抓到并清理。现在比的是 **祖先/后代关系**（`real in tgt.parents or ...`）。

### ⑤ 实测：从 0 构建一整套要多久（lv75 死灵+狂战士，16 物理核）

| 阶段 | 命令 | 墙钟 |
|---|---|---|
| 造从 0 角色 | `make_zero_char.py --level 75 --classes 8,10` | ~1 s |
| 三形态加点 | `make_alloc.py <arch> 75` ×3 | ~2 s |
| **形态普查（3 形态并发 24 进程 / LNS 400 轮）** | `sweep_arch.py … --budget 24` | **118.1 s** |
| 赢家四轴（属性 + 技能 + 星座，2 轮） | `coordinate_ascent.py … --axes attr,skill,dev` | **22 s** |
| **合计** | | **≈ 143 s（2 分 23 秒）** |

- 单形态（8 进程）97~118 s；并发**重叠收益 2.79×**（串行和 329.8 s）。
- 四轴结论：属性 **全局最优 +0.00%**（1,485 组合全枚举 / 4 s）、技能 **+27.46%**（2 s）、
  星座 **+0.00%**（从 0 角色星点 0/0，得先「建」星座树而不是重排）。

## #97 ★★★ BD 配方 v2：**只做编排，不重写逻辑** + 分阶段状态可续跑

**动因**（用户 2026-09-22）：「重做，尽可能 py 自动化、CLI 程度高一点，
**减少 agent 反复编写原始代码**」。

### v1 为什么必须废
`gd/recipe.py`(2026-09-17) 把六步流水线**写死**在模块里，用
`cd data && python gd_opt.py` 调装备搜索、还要 `bd_gen.py` 出报告 ——
这两个脚本在现有工具链里**都不存在**（搜索已并入 `python -m gd auto`，
报告走 `gd/planreport.py`）⇒ 实跑第③步必崩。**更要命的是它把实现复制了一份**：
底层一演进，recipe 就得跟着手改，改不动就烂在那里（这正是它烂掉的原因）。

### v2 的设计（三条，`tools/recipe.py`）
| 原则 | 做法 | 好处 |
|---|---|---|
| **纯编排** | 每阶段都是对现有工具的**一次子进程调用** | 底层工具演进时 recipe 自动跟着变；**一份实现**，不存在两处漂移 |
| **分阶段状态** | `data/plans/recipe/<key>/state.json` 记每阶段的 ok/耗时/产物 | 成功的阶段默认跳过 ⇒ 改中间一步不必从头再跑（`--force` 重跑，`--stages 4` 只补某段） |
| **形态自动决策** | 给职业组合（`8,10`）就**自动展开全部形态**并发跑、按真实 DPS 取最优 | 回答「模型要直接拿到最优解，而不是询问形态」；`--forms` 仍可显式收窄 |

阶段与调用的工具：

```
S0 预算    gd/alloc.*_budget(lv) ← data/level_table.json     （不读存档）
S1 底子    tools/make_zero_char.py                            （--from-zero 才跑）
S2 加点    tools/make_alloc.py            → GD_SKILL_JSON
S3 形态普查 tools/sweep_arch.py（并发 gd auto --extreme --with-weapon）
S4 四轴    tools/coordinate_ascent.py
S5 交付    gd/planreport.py + tools/plan_cycle.py + tools/plan_legal.py
```

`gd/recipe.py` 改成 **12 行薄壳**（`runpy.run_path` 转发到 `tools/recipe.py`），
CLI 入口 `python -m gd recipe` 不变、**实现只有一份**。★ `gd/cli.py` 在镜像对里，
改它要 `--capture`；走薄壳就完全不用碰它。

### 三个实现坑（都踩过）
1. ★★ **续跑时不能只在循环里注入环境**：`GD_SKILL_JSON` 原先只在 S2/S3 跑完才设，
   于是 `--stages 4` 续跑（S2/S3 被跳过）会让四轴在「**无技能**」状态下评估
   ⇒ 数字全错。修法：**从 state 里读赢家形态与其加点**，进循环前就设好。
2. ★ **`--extra` 传以 `-` 开头的值要写 `--extra="--with-weapon"`**，否则 argparse 把
   `--with-weapon` 当成下一个选项、报 `expected one argument`。
3. ★ **交付必须有硬判据**：`plan_cycle`（伤害循环文档）是铁律级交付物，
   所以 S5 的成败判据是 `bool(build_report and cycle_doc)`，缺一个整段算失败 ——
   不许「报告出来了就算过」。

### 实测（一条命令，16 物理核）
```
$ python -m gd recipe 8,10 75 --from-zero
S1 从 0 造角色（含自动学入 6 条形态技能）   0.1 s
S2 三个形态加点                            0.3 s
S3 形态普查 3 形态并发                     76.1 s  → wolf_necromancer 51,402 ← 最优
S4 四轴 attr,skill,dev                     18.0 s
S5 报告 + 循环文档 + 合法自检                5.0 s
总用时 **99.2 s**   ｜ 产物 data/plans/final/RECIPE_8_10_lv75_zero.md
```
对比手搓同一件事（我上一轮逐条敲命令）：**143 s**，而且每一步都要 agent 现场拼命令行。
续跑实测：`--stages 4 --force` **只重跑 S4，17.1 s**。
自检 `[69]` 13 项钉住：形态展开、`learn_for`、预算、编排器契约、循环文档硬交付、
`--list-forms` CLI 可用、不存在的职业组合要非零退出并提示去补增补文件。

## #98 ★★ 「同口径对比」的两个陷阱：壳的职业不对齐 + 形态条目校准程度不对等

**需求**：「对比 狂战士+夜刃怎么样，是提升还是削弱」—— 即 `{10,4}` 与 `{8,10}` 谁强。

### 坑一：造从 0 角色时**壳的职业会混进来**
`make_zero_char` 原先写死 `--shell _Cx666`（class {8,10}）。拿它去造 `{10,4}` 的角色时，
`--classes 10,4` 只是**新增**两条精通条记录，**壳原有的 8 号职业还留着**
⇒ `read_meta` 读到 `classids=[8,10]`，跟预期不符、对比直接失效。
修法两条：
- `pick_shell(classes)`：**按目标职业自动挑壳**（取等级最高者，要求 `目标 ⊆ 壳的职业`）；
- 写档时**先清掉壳里不属于目标组合的精通条**，再补目标的。

### 坑二：形态条目的「校准程度」不对等 ⇒ 别把条目差距当成职业差距
同一套流程（`python -m gd recipe <组合> 75 --from-zero --forms ...`，同预算 / 同 LNS 400 轮 /
同 proj=1 / 每形态同 5 进程）实测：

| 形态 | 死灵+狂战士 {8,10} | 狂战士+夜刃 {10,4} | 差 |
|---|---|---|---|
| human | `necro_berserker` 35,188 | `avalanche` **78,843** | {10,4} **+124.1%** |
| werewolf | `wolf_necromancer` **51,402** | `wolf_nightblade` 52,135 | {10,4} **+1.4%** |
| wereraven | `raven_necromancer` 33,711 | `berserker_nightblade` 51,397 | {10,4} **+52.5%** |

**最优对最优**：51,402 vs 78,843 ⇒ `{10,4}` **+53.4%**（`{8,10}` 约 −34.8%）。

★★ **但狼人形态下只差 1.4%** —— 说明强度几乎由「形态 + 它授予的技能」决定，
**第二个职业在变身形态里几乎不出力**（形态门控会把第二职业的武器池剔掉，陷阱 #78）。
差距集中在 **人形态** 与 **鸦人形态**。而人形态那个 2.2 倍**很可能主要是条目质量**：
`avalanche` 是历史手工调过的（仅「补 2 个武器池技能」一项就值 **+16.4%**），
`necro_berserker` 是机械派生的。

⇒ **结论**：按当前形态定义 `{8,10}` 是削弱；但「谁强谁弱」的定论**必须先把两边校准到同一水平**
再跑本对比，否则量到的是「谁的条目调得多」。另外两侧四轴余量还不同（`{8,10}` 技能 +27.46%、
`{10,4}` 属性 +12.71%），**通道不同不可相加**，所以这不是「都调优到极致后」的终评。
