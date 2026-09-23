# 重构前快照 —— grim-dawn（2026-09-23 更新）

> 大重构「干净开始」前的完整回退点。**两份归档共同覆盖当前版本的全部状态**。

## 归档清单

| 文件 | sha256 | 大小 | 内容 |
|---|---|---|---|
| `grim-dawn_prerefactor_20260923.tgz` | `680930c3…ff2e6` | 8.97 MB | **源码 + 离线库参考**：`gd/` `tools/` `docs/` `data/*.json`（skills/archetypes/record_* 等）`SKILL.md`。**排除**生成产物（data/plans·cache·scratch·regress）。 |
| `grim-dawn_generated_20260923.tgz` | `84f78b92…e7f8` | 15.67 MB | **全部生成产物**：`data/plans`(364M) `data/cache`(99M) `data/scratch` `data/regress`。普查/从0构建/四轴全部中间与最终产物，可完整回退。 |
| `grim-dawn_prerefactor_20260923.tgz.bak` | `06793d16…26bb` | 7.98 MB | 首版快照（15:30，含原始 313 文件基线，未含本次新增的调研 §9）。双保险。 |

## 还原

```bash
cd "/c/Users/Administrator/.workbuddy/skills"
mv grim-dawn grim-dawn.broken
mkdir grim-dawn && tar -xzf "/c/Users/Administrator/.workbuddy/skill_archive/grim-dawn_prerefactor_20260923.tgz" -C grim-dawn
tar -xzf "/c/Users/Administrator/.workbuddy/skill_archive/grim-dawn_generated_20260923.tgz" -C grim-dawn
/c/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe grim-dawn/tools/selftest.py   # 应全绿
```

## 本次快照的状态
- 伤害模型：`gd/dmg.py` + `gd/rotation.py::final_report`（**逐位可复现** 是硬指标）
- 搜索：`tools/tune_dps.py::lns_search`（LNS+LAHC，非退火）
- 满抗：`tools/ilp_res.py`（HiGHS MILP + 支配剪枝）
- 建池：`gd/opt.py::_skyline`（已从 O(n²) 改到近线性，419.5 → 21.9 s）
- 已测瓶颈：单次评估 ~1.45~3.0 ms；**LNS 轮内 98% 逐候选标量 Python，2% numpy 邻域构造**
- 调研文档 `docs/REFACTOR_RESEARCH.md`：**方案 B（张量化/CUDA）已删除**（必破坏逐位可复现）；保留 A 代理驱动 / B 整体 MILP / C DP / D 搜索内核 / E 热路径。
- **非 git 仓库** ⇒ 本归档是唯一的版本回退点。
