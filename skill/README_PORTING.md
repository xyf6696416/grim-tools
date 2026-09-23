# grim-dawn skill · 移植/测试包

恐怖黎明（Grim Dawn）离线数据工具链。数据地基 = **Grim Tools 桌面版离线库**
（`<GT安装目录>/resources/app.asar`），不解析 `.arz`、不抓网页、不用 CDP。

打包时间：2026-09-20 ｜ 打包源：`C:\Users\Administrator\.workbuddy\skills\grim-dawn`

---

## 一、包内容

| 目录 | 说明 |
|---|---|
| `SKILL.md` | **导航层**（228 行）：目录地图 + 按域命令速查 + 关键口径 + 陷阱索引 |
| `gd/` | 核心库（39 模块 + `save/` 子包）。路径全相对（`SKILL_ROOT = __file__/../..`）|
| `tools/` | 生产工具 + 18 个 `gd_*_patched.py` **保命副本**（`sync_live.py` 机制，勿删）|
| `docs/` | 细则：`commands.md`(命令行 1035 行) / `pitfalls.md`(46 条陷阱) / `api.md` / `reference.md` |
| `data/` | 26 个抽取产物 + `plans/final/` 交付报告 + `regress/` golden 基线 |
| `data/cache/` | 见「四、数据自愈」 |

---

## 二、运行前提

1. **Python ≥ 3.11 且装有 numpy** —— 优化器/LNS 必需。缺 numpy 会**静默**退回纯 Python
   （结果一致但慢一个量级）。本机托管 venv：
   `C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe`
2. （可选）**Grim Tools 桌面版** —— 仅「重建数据 / 跑迁移」时需要。
3. （可选）**Grim Dawn 存档** —— 仅涉及角色 `Sam` 的命令（dps / auto / plan_audit）需要。

---

## 三、三条命令验证（装完必跑）

```bash
# 用你环境里带 numpy 的 python
PY="C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe"

$PY tools/selftest.py      # 期望：✓ 全部通过（379 项）
$PY -m gd dps Sam          # 期望：合计 101976 ｜ 实战 152476
$PY -m gd --help           # 命令清单
```

> `selftest` 全绿 + `dps Sam = 101976` 是**零漂移指纹**。若对不上，先查 python 是否带 numpy、
> 以及 `data/` 是否完整。

---

## 四、数据自愈

- `data/cache/*.pkl` **未打包**（22 MB，是 `data/*.json` 的 pickle 加速缓存）——
  首次运行会按 `(mtime, size)` 自动重建，无需干预。
- 若你拿到的是 **slim 包**：不含 `data/cache/*.js`（38 MB，离线库原始解包）。
  装了 Grim Tools 后跑一次
  ```bash
  $PY tools/migrate_from_archive.py
  ```
  会从 `app.asar` 把 `itemdb.js` / `monsterdb.js` / `calc.js` / `l10n_*.js` 等重新落下来。
  **运行 dps / 报告不需要这些 .js**，只有重建抽取产物时才需要。

---

## 五、环境变量（路径探测全在 `gd/paths.py`）

| 变量 | 作用 |
|---|---|
| `GD_GT_DIR` | Grim Tools 安装目录（其下须有 `resources/app.asar`）|
| `GD_SAVE` | 存档**父目录**（其下须有 `main/<角色>/player.gdc`）|
| `GD_GAME` | 游戏安装目录（其下有 `database/`）|
| `GD_PROJ_HITS` | 投射物命中数**场景假设**（默认 `1.0`，不是数据库事实）|
| `GD_SKILL_JSON` | ⚠ **不宜使用**：会**整体替换**技能集，套不完整加点会抹掉高伤技能 |
| `GD_NOCACHE` | 关磁盘缓存 |

`paths.py` 会在 `E:/D:/C:/F:/Grim Tools`、Steam 库、`Documents/My Games` 等处自动探测；
找不到时用上面变量覆盖。

---

## 六、安装到其他 agent

把整个 `grim-dawn/` 目录放到目标机器的 skill 目录即可（默认 user 级）：

```
~/.workbuddy/skills/grim-dawn/
```

路径全相对，**可以放任意位置**；无需改任何代码。装完跑第三节的三条命令。

---

## 七、已知边界（别踩）

- 极限 DPS **随敌方抗性档变化**，没有单一「极限」——报告须注明敌方档
  （现统一 `m3955` 真值怪：护甲 1129，穿刺 5% / 流血 80%）。
- `fangs`（完美姿态）**不可构建**：核心技能来自 `itemskillsgdx3/relics/*`，
  授予者 `it15928` 需求等级 90 > 71 ⇒ 已被合法性闸门剔除（`gd/skillprov.py`）。
- 护甲**只吃物理直伤**；「护甲穿透」是**物理→穿刺**转化，不是降目标护甲（玩家侧恒 0）。
- 装备自带触发技能、星座绑定的主动技能**不在** DPS 里。

---

## 八、重新打包

本包由 skill 自带的打包器生成，改完代码后再出包只需一条命令：

```bash
$PY tools/pack_skill.py --verify
# 产出（默认 E:\xz\Archives）：
#   grim-dawn_skill_full_<日期戳>.zip   ← 含 cache/*.js，目标机无需装 Grim Tools
#   grim-dawn_skill_slim_<日期戳>.zip   ← 不含 cache/*.js（省约 5.6 MB）
# --verify 会自动解压到临时目录跑 selftest + dps 基准，确保包本体可用
```

排除项：`__pycache__` / `*.pyc` / `data/cache/*.pkl` / `data/cache/prune/` /
`data/scratch/` / `*.tmp`（slim 另排 `data/cache/*.js`）——全部可自动重建。

