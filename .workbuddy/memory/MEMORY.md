# Grim Tools 项目长期记忆

> 细节全在 skill：`~/.workbuddy/skills/grim-dawn/`（`SKILL.md` 导航 / `docs/pitfalls.md` 陷阱 #66–#99 / `commands.md` / 模型文档）。
> 本文件 = 定位 + 铁律 + 当前结论。**历史脉络看当日 `memory/2026-09-2X.md` 日志。**

## 地基
- 真源 = GT 离线库 `E:\Grim Tools\resources\app.asar`（不解析 .arz/不抓网页/不用 CDP）。
- 代码根 = `~/.workbuddy/skills/grim-dawn/`（**非** `E:\Grim Tools`）。
- Python = 托管 venv `…/python/envs/default/Scripts/python.exe`（3.13.12 缺 numpy ⇒ 优化器静默退纯 Python）。

## 铁律
- 重写级改动登记 `tools/sync_live.py::PAIRS`（24 对）并 `--capture`；`migrate_from_archive.py` 是唯一可复跑真源；自研形态只写 `data/archetypes_gdskill.json`（`archetypes.json` 被迁移无条件覆盖）。
- 改完必跑 `tools/selftest.py`（全绿）+ 锚点零漂移；同一文件多处改动串行 Edit。
- 模型/渲染器改完 → `tools/rerun_final_docs.py` 重算 `final/`。
- 每出新 BD 必带「伤害循环文档」（`final/CYCLE_<形态>.md`），缺即不交付。
- 落档三步 + 落档后必须重设锚点（`model_v2_anchor.json` 加 vN + selftest 两处版本指针）；`save.patch`/`build` 不加 `--apply` 绝不写盘。
- **资源铁律**：`--extreme` 多进程会爆内存 ⇒ 用 `--batch`/`--budget`/`--low-priority` 限资源（用户实测 400+ py 进程卡爆）。

## 算法瓶颈与重构约束（关键结论）
- 现状：4 链×4 进程=16 核已吃满；**每并行单元内部是纯串行标量 Python**。
- 单次真实评估 ~1.45–3.04 ms（`load_char` 19% + `final_report` 71%，其中 `dmg.convert` 转伤链 ~30%）。
- **CUDA 结论**：LNS 轮内 98% 是逐候选标量评估（分支发散，GPU 死穴）；仅 2% 邻域构造可上 GPU ⇒ 不重排数据布局 ≈ +2%。张量化（方案 B）破坏逐位可复现，需你批准重建锚点。
- 硬约束：①面板逐位可复现（与 GT 对拍零漂移，须有 `GD_*` 回退开关 A/B）②selftest 全绿 ③镜像对改一处 capture ④非 git 仓库，只有归档能回退。
- 重构候选优先级（详情 `docs/REFACTOR_RESEARCH.md`）：**A 代理驱动**（降真实评估 10×、不破坏契约，先测秩相关）→ **F 热路径工程**（共享只读解析/线程池/numba，可逐位对拍）→ **C 整体 MILP**（先 3 槽可行性）→ 最后才 B 张量化/CUDA。

## 当前状态（2026-09-23）
- **BD 配方 v2**：`python -m gd recipe <形态|职业组合> <等级>` 一条命令跑完整 BD（S0 预算→S1 造从0角色→S2 加点→S3 形态并发普查取最优→S4 四轴→S5 交付+循环文档）。`8,10 75 --from-zero` = 99.2 s。实现 `tools/recipe.py` + `gd/recipe.py` 薄壳。
- 从 0 构建链路打通（`tools/make_zero_char.py` + `make_alloc.py` + `sweep_arch.py`）；变身形态必先加点再普查。
- **狂战士×全职业普查**（lv75/从0/`--extreme`）：有效最高 = 爆破者 155,042 → 萨满 109,004 → 士兵 99,974（前 3 因 WPS 判据漏职业默认攻击已作废待重跑）→ 夜刃 91,260 → 神秘 87,108 → 奥术 83,795。续跑：`gd recipe x 75 --all-with 10 --from-zero --iters 150 --batch 3 --budget 9 --low-priority`。
- Sam：lv73 夜刃+狂战士，锚点 v14（面板 81,363 / 实战 98,873 / 含减抗 163,959，指纹 `ddb54cc16610`）。
- 备份：`~/.workbuddy/skill_archive/grim-dawn_prerefactor_20260923.tgz`（7.98MB/313文件）。

## 常用命令（节选）
```bash
PY=/c/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe
$PY tools/selftest.py                 # 全绿基线
$PY tools/save_state.py Sam           # 存档指纹（改后先比）
$PY -m gd recipe 8,10 75 --from-zero  # 整 BD 一条命令
$PY tools/coordinate_ascent.py Sam --axes attr,skill,dev --rounds 2  # 四轴
$PY tools/plan_legal.py <方案.json>   # 逐槽非法位
```
