# Python API（§4）

> 由 SKILL.md §4 拆出。

## 4. Python API

```python
import sys; sys.path.insert(0, r"C:\Users\Administrator\.workbuddy\skills\grim-dawn")
from gd import DB
from gd.render import Renderer

db = DB.load()                 # 默认中文 + 英文回退；缓存命中 ~0.1s
r  = Renderer(db)

db.name("it2116")              # '天之裂片咒刃'      任意 id 的中文名
db.en_name("it2116")           # 'Skyshard Spellblade'
db.name("is190")               # '世界守护者的花园'
db.find("利维坦", "item", 5)     # [(gid, 中文名), …]
db.filter_items(**{"offensiveFireModifier__gte": 100, "f": "Legendary"})
db.mastery_skill("sk1235")     # 专精技能（含逐级数值）
db.level_table()               # 升级给点表
db.stats()                     # 各库条目数
db.l10n.get("DamageFire")      # '{%t0} {^E}火焰伤害'   （模板，未渲染）
db.field_tag("offensiveFireModifier")   # -> 'DamageModifierFire'

print(r.item_card("it2116", show_range=True))
print(r.affix_card("pre4264"))
print(r.set_card("is190"))
print(r.skill_card("sk1122"))

# ---- 属性需求闸门（见 §3.8）----
from gd import req as RQ
RQ.req("it15810", "c031_necklace.dbr", "项链")            # {'physique','cunning','spirit',…}
RQ.req(gid, rec, slot, att_safety=6, extra_gids=[...])     # 饰品行数加安全余量
RQ.panel_needed(gids, records, slots, att_safety=6, extra=ex)   # 全套逐维最大值
RQ.total_att_estimate(gid, extra_gids=())                  # 提示框行数近似（饰品才有用）
RQ.need_of_rows(rows, att_safety=6)                        # ★ 推荐入口：直接吃「行」清单
RQ.weapon_kind("records/items/gearweapons/swords/c012_sword.dbr")   # -> ('sword','cunning',方程)

# ---- 属性自动拟合（★ 写档链路共用，见 §3.8）----
from gd import reqfit as RF
r = RF.solve('Sam')                        # 当前装备 → Fit
r = RF.solve('Sam', override=ov,           # 换成 ov 这套装备之后（ov 见 dps.load_char）
             buffer=1, att_safety=6, prefer=None, spread=False,
             save_dir=None, rows=None)     # save_dir：从副本读（配合 paths.save_dir_override）
r.feasible          # False = 预算不够（看 r.short）
r.targets           # {'physique':242.0,'cunning':418.0,'spirit':130.0}  ← 要写进 block2 的值
r.changed           # 存档值是否需要改
r.need              # 原始需求 {'physique':554,'cunning':421,'spirit':309}
r.unevaluated       # 需求算不出来的件（confidence='unknown'）
print(RF.describe(r))
RF.gear_from_save('Sam')                   # 存档里的 12 槽（含全部武器套）
RF.gear_from_items(eq, wsets)              # 从 plan/eq 结构取行（↔ gd/build.py 用）
RF.fit('Sam') / RF.feasible('Sam')         # 一步到位

from gd import rotation as R
mast = {'class04': 35, 'class10': 50}                      # 有效精通等级（存档 + 装备）
R.mastery_attr_of('class10', 50, 'physique')               # -> 200.0  精通逐级给点
R.panel_attrs({'physique':242, 'cunning':418, 'spirit':130}, mast, gf, gp)
R.attr_damage_pct(panel)                                   # 属性→伤害加成%（按伤害类型）

import gd.paths as P
with P.save_dir_override(r'<副本目录>'):      # 临时把「存档目录」指到别处
    RF.solve('Sam')                          # 读写都在副本里，不碰真档
```

**模块**

| 模块 | 职责 |
|---|---|
| `gd/asar.py` | Electron asar 读取（纯 Python） |
| `gd/jsobj.py` | JS 对象字面量解析器（剥离赋值前缀后当数据读，**绝不 eval**） |
| `gd/db.py` | 离线库加载/索引/缓存（`DB.load()`） |
| `gd/text.py` | 多语言查表 + **GT 官方格式引擎**（`f()`/`l()`/颜色/区间） |
| `gd/scale.py` | **官方等级缩放判据**（`va()`/`Xc()`：只有 `offensive*` 缩放 + 三张黑名单） |
| `gd/req.py` | ★ **属性需求闸门**：官方 `itemCostFormulae` 求值 + 面板阈值比较（见 §3.8） |
| `gd/reqfit.py` | ★★ **属性自动拟合**：唯一求解实现，`solve()` 算「该投多少点」（见 §3.8） |
| `gd/render.py` | 物品/词缀/套装/技能卡片 |
| `gd/paths.py` | 全技能唯一的路径来源（含 `save_dir_override()`） |
| `gd/cli.py` | `python -m gd` |

---

