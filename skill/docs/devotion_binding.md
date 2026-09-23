# 星座绑定技能（Devotion Binding）机制

> **本文件回答**：星座授予的「天界之力」（celestial power）**绑定到玩家技能上**有哪些关系、
> 触发条件怎么分类、绑定关系存在哪里、模型怎么裁决。
> 数据源：离线库 `itemSkillControllers` + 存档 `/devotion/*_skill` 条目。

---

## 1. 绑定关系存在哪里

**不在离线库，在存档里**。存档块 8 的技能条目有两个字段就是绑定关系：

```python
# gd/save/core.py::_skill()
{'skill': …, 'level': …, 'devotion_level': …, 'sublevel': …,
 'autocast': …,                 # ★ 宿主技能记录路径（绑到谁身上）
 'autocast_controller': …}      # ★ 触发控制器记录路径（什么时候触发）
```

**读法**（`devotion_skills` 里 `is_skill: true` 的星点 = 授予技能的星点）：

```python
for s in d['block_map'][8]['skills']:
    if s.get('autocast'):
        print(s['skill'], '→', s['autocast'], s['autocast_controller'])
```

控制器路径的**命名规则自带语义**：

```
records/controllers/itemskills/cast_@<目标><触发条件>[_参数]_<几率>%.dbr
                                 enemyonattackcrit_100%   → Enemy + 攻击暴击 + 100%
                                 selfonattackcrit_100%    → Self  + 攻击暴击 + 100%
                                 selfat50%health_100%     → Self  + 生命 50%  + 100%
```

---

## 2. 触发条件全集（7 类 × 作用目标）

来自离线库 `db.raw['itemSkillControllers']` 的 **70 条控制器**（物品与星座**共用同一批控制器**）：

| 触发类型 `triggerType` | 中文 | 作用目标 | 出现过的几率 |
|---|---|---|---|
| `AttackEnemy` | 攻击敌人时 | Enemy / Self | 5,8,10,15,20,25,30,35,50,100 / 5,8,10,15,20,25,33,100 |
| `AttackEnemyCrit` | 攻击**暴击**时 | Enemy / Self | 15,20,25,30,33,50,100 / 25,30,33,50,100 |
| `HitByEnemy` | 被敌人击中时 | Enemy / Self | 15,20,25,30,33,50,100 / 10,15,20,25,30,50,100 |
| `HitByMelee` | 被**近战**击中时 | Enemy / Self | 15,20,30,33 / 10,15,25 |
| `Block` | 格挡成功时 | Enemy / Self | 15,20,25,30,50 / 10,15,20,25,30,33 |
| `OnKill` | 击杀敌人时 | EnemyLocation / Self | 50,100 / 15,30,50,100 |
| `LowHealth` | 生命低于 `triggerParam`% | **仅 Self** | **恒 100** |

**作用目标**三种：`Enemy`（对敌施放）/ `Self`（自身增益）/ `EnemyLocation`（击杀点）。

---

## 3. ★ 两类触发，性质完全不同

| 类别 | 成员 | 含义 |
|---|---|---|
| **攻击类**（`ATTACK_TRIGGERS`） | `AttackEnemy`、`AttackEnemyCrit` | **本循环的哪个伤害技能能触发它** —— 这个问题**有意义** |
| **防御类**（`DEFENSIVE_TRIGGERS`） | `HitByEnemy`、`HitByMelee`、`Block`、`OnKill`、`LowHealth` | 由**挨打 / 格挡 / 击杀 / 低血**触发 ⇒ **与用哪个技能无关**，「能不能触发」是**伪问题** |

### 攻击类的三条硬规则（`gd/procs.py::verdict()`）

1. **宿主必须是「造成伤害的攻击/法术」** —— 不产生伤害的技能（纯光环、位移、召唤）**无法**触发；
   **DoT 的后续跳不算**（只有起手命中 roll）。
2. **`AttackEnemyCrit` 需要暴击率 > 0** —— 0% 暴击率 ⇒ 永不 roll。
3. **`Block` 需要持盾** —— 没盾 ⇒ 该类不会发生。

### 两类**不适用**触发语境的（返回 `NA` 而非是/否）

- **技能改造（`Skill_Modifier`）**：直接改某个技能，根本不存在「触发」。
- **武器池技能（WPS）**：按 `skillChanceWeight` **权重**掷骰，不靠 `chanceToRun`。
- **常驻（无 controller）**：装上就生效，不需要触发。

---

## 4. 其他绑定规则

| 规则 | 说明 |
|---|---|
| **一个宿主可绑多个星座技能** | 实测 Sam 的「野性利爪」同时绑了 **2** 个（刺客 + 刺客的利刃） |
| **每个星座技能只占一个槽** | 一个 celestial power 只能绑一处 |
| **星座技能不绑定 ⇒ 不生效** | 只是点亮星座拿属性，技能槽空着就没有触发 |
| **宠物绑定是另一套** | 可绑到召唤物上（`Skill_TargetedSpawnPet` / `Skill_SpawnPet` 模板的星座） |

---

## 5. 全库有多少星座带技能

`data/devotion_skills.json` 里 **`is_skill: true` 的星点 = 63 个**（对应 63 个星座有授予技能；
其余是纯属性小星座）。按模板分布前几名：

| 模板 `template` | 数量 | 是什么 |
|---|---|---|
| `Skill_TargetedSpawnPet` | 6 | 召唤指定宠物 |
| `Skill_AttackBuff` | 5 | 攻击型增益 |
| `Skill_AttackRadius` / `Skill_BuffRadius` | 4 / 4 | 范围攻击 / 范围光环 |
| `Skill_AttackProjectileOrbiting` / `ProjectileRing` / `AttackWave` | 3 / 3 / 3 | 环绕弹 / 弹幕环 / 冲击波 |

> 这些是**技能形态**（怎么打出去），**不是触发条件**。触发条件只存在于绑定时写入的 controller。

---

## 6. 本 BD（Sam lv73）的实际绑定

| 星座技能 | 宿主技能 | 触发条件 |
|---|---|---|
| **乌龟**（`tier1_29e_skill`） | 野兽形态 `werewolf1` | **自身生命 50% 时**（100%） |
| **刺客**（`tier2_06g_skill`） | 野性利爪 `werewolf1_skill01_claws` | **自身攻击暴击时**（100%） |
| **刺客的利刃**（`tier1_08e_skill`） | 野性利爪 `…_claws` | **对敌攻击暴击时**（100%） |

★ 三条**全是暴击 / 低血触发**，与「主输出是野性利爪」一致 —— 绑得是对的。
★ **新加的 狐狸 / 猫头鹰 / 豺狼 是纯属性星座，没有技能**，不需要绑定。

---

## 7. 数据缺口（★ 未导出）

**离线库没有导出「每个星座技能自带哪个触发条件」** —— `devotion_skills.json` 只有
`{template, tag, name, en_name, max_level, is_skill}`，**没有 controller 字段**。
触发条件只在**玩家绑定后写入存档**时可见。

⇒ 现状能回答的是：**触发条件全集**（7 类）+ **存档里的实际绑定**；
**不能**回答「还没绑的某个星座技能会用什么触发条件」（需要游戏内查看或补数据）。

**模型边界**：星座绑定的主动技能**不计入 DPS**（`gd/rotation.py` 只吃属性/被动加成）。

---

## 8. 快速查询命令

```bash
PY="C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
cd C:/Users/Administrator/.workbuddy/skills/grim-dawn

# 当前角色的星座绑定关系
$PY -c "
import sys,os; sys.path.insert(0,'.')
from gd import _legacyenv as ENV, DB
from gd.save import core as S
db=DB.load(); sd,_=ENV.find_save_dir()
d=S.parse(os.path.join(sd,'main','_Sam','player.gdc'))
for s in d['block_map'][8]['skills']:
    if s.get('autocast'):
        nm=(db.devotion_skills.get(s['skill']) or {}).get('name') or s['skill']
        print(nm, '→', s['autocast'].split('/')[-1], '|', s['autocast_controller'].split('/')[-1])
"

# 控制器全集
$PY -c "
import sys; sys.path.insert(0,'.'); from gd import DB
ic=DB.load().raw['itemSkillControllers']
for ct,v in ic.items(): print(ct, v['targetType'], v['triggerType'], v['chanceToRun'])
"
```
