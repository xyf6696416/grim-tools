# -*- coding: utf-8 -*-
r"""点数预算与技能点分配（gd_alloc）—— 2026-09-17 新增（v3 P1/P3）

用户拍板的点数是 **a：严格按等级合法预算**（不做 free 模式）。
所以「方案能不能落地」必须先过这一关：

```
技能点预算 = min(等级-1, 49) × 3 + max(0, 等级-50) × 2 + 13（任务）
             65 级 → 49×3 + 15×2 + 13 = 190
属性点预算 = (等级-1) + 10（任务）      65 级 → 74
虔诚点     = 神殿解锁数（保守按等级上限估）
```

> 公式来源：Grim Dawn 官方 wiki（1-85 级每级 1 属性点）+ GT `calc.js` 的任务奖励表
> （`questAttributePoints:10` / `questSkillPoints:13`，22 条任务）。
> ⚠ 现有存档里 `_Sam`(58 级) 花了 213 点 > 该公式的 176 —— 说明**该角色被改过档**，
> 所以本模块只用它做"合法性上限"，不用来推断现状。

分配策略（贪心 **合表排序**，够用且可解释）
------------------------------------------
两池（`core_skills` 与「同职业可点技能」）**合成一张表**再按下面这个键排序，
然后从头贪心点满（每技能上限 20 级，精通条按形态声明）：

```
(_priority, 是否 core, tier, 名称)
_priority:  0 精通条 > 1 形态本体 > 2 形态子技能 > 3 WPS > 4 攻击
             > 5 加成/开关 > 6 被动 > 7 其他
```

★ **两池必须合表排**（2026-09-20 修）：旧写法 `core + rest` 是「先吃满 core
  再轮 rest」，点数不足时 rest 池被**整池饿死**（狼系实测偏低 **35%**）。
  合表后「加成类」能排在**优先级更低的 core 攻击技能**之前。
★ 精通条**不在这张表里** —— 它在循环前先按 `masteries` 声明扣掉（保证技能树解锁），
  且要按 `%02d` 补零拼记录名（`class01`~`class09` 漏补零会让精通条整体消失）。
"""
import json
import re
import os
import sys

from . import paths as _PATHS

# 旧脚本用 HERE 拼数据文件路径；新架构下数据在技能的 data/ 里
HERE = str(_PATHS.DATA_DIR)
_CACHE_ROOT = str(_PATHS.CACHE_DIR)
_PLANS = str(_PATHS.CACHE_DIR / 'plans')



# ★ 职业号必须补零：真实记录是 `playerclass01/…_class01.dbr`。
#   旧版用 `%d` 生成 `playerclass1`，只有 class10（狂战士）恰好两位而正常，
#   **class01~09 的精通条全部被静默丢弃**——表现为「BD 里一个精通条都没有」，
#   且点数被全部塞给技能（看似预算用满，实际落不了地：不点精通条技能树是锁的）。
MASTERY_REC = 'records/skills/playerclass%02d/_classtraining_class%02d.dbr'
TASK_SKILL_POINTS = 13
TASK_ATTR_POINTS = 10

# ------------------------------------------------------------------ 加点优先级
# 数字越小越先点。
# ★ WPS（武器池技能）必须排在普通攻击技能之前：模板 `Skill_WPAttack_*` /
#   `Skill_WeaponPool_*`（节奏打击、马尔科夫优势、处决…）是双持流的**乘法级**收益
#   —— 每次普攻按概率额外触发一次。旧版按 tier 升序贪心，能把「处决」（tier9）
#   一路挤到点数耗尽，等于丢掉了双持流的核心输出。
def is_wps(d):
    tmpl = d.get('template') or ''
    return 'WeaponPool' in tmpl or 'WPAttack' in tmpl


_FORMS = None


def _form_of(rec):
    """技能属于哪个**变身形态**（按记录名首段字母判定）；非形态技能返回 None。

    ★ 形态是**互斥**的：狼人（`werewolf1`）与鸦人（`wereraven1`）是两条独立分支，
    游戏里不可能同时点。但 rest 池按「同职业补充技能」会把另一条分支也拉进来 ——
    实测狼人方案里混进了「鸦人形态 16 级」。这里按记录名前缀识别归属：
    `wereraven1_skill01_icicles` / `wereraven2` / `wereraven3` → 都归 `wereraven`。
    """
    global _FORMS
    if _FORMS is None:
        skills = _load('skills.json', {}) or {}
        _FORMS = set()
        for r, d in skills.items():
            if d.get('kind') == 'shapeshift':
                _FORMS.add(_alpha(r))
        _FORMS.discard('')
    f = _alpha(rec)
    return f if f in _FORMS else None


def _alpha(rec):
    """记录基名的首段小写字母（`wereraven1_skill01_icicles.dbr` → `wereraven`）"""
    m = re.match(r'^([a-z]+)', os.path.basename(rec))
    return m.group(1) if m else ''


def _tier_key(d):
    """排序键里的 tier：**缺失的视为 0（最高优先）**，而不是垫底。

    ★ 为什么：变形授予的技能常读不到 tier（例鸦人形态的「寒冰之爪」`icicles` /
    「霜暴」`icering` —— 记录挂在形态本体下、`skillTier` 为空）。它们是**该形态的
    主输出技能**，却被 `tier or 9` 压到所有普通攻击之后，点数一紧就轮不到
    （实测 60 级狂战士+夜刃：预算 180 点不够，两个核心技能直接没点）。
    这类技能只会出现在 archetype 精挑的 `core_skills` 里 —— rest 池强制要求
    `tier is not None`，所以放行不会引进垃圾。
    """
    t = d.get('tier')
    return 0 if t is None else t


# 变形后**点不出来**的技能 kind：技能栏被 `grantedSkills` 整体替换，
# 基础主动攻击技能（猛袭/血牙/雪崩/跃击/阿斯特堪之风…）在狼人形态下无法使用。
# 实测依据：`werewolf1.grantedSkills` 只有 [claws, charge] 两个。
# 注意**不含** `attack_buff_radius`（战吼类，变形后仍可用）。
_FORM_LOCKED = ('attack', 'attack_weapon', 'attack_wave', 'attack_projectile',
                'attack_radius', 'attack_weapon_charge', 'attack_weapon_blink',
                'attack_projectile_burst', 'attack_weapon_radius',
                'attack_spellcone', 'attack_projectile_debuf')


def _priority(d, form_roots=()):
    kind = d.get('kind')
    if kind == 'mastery':
        return 0
    if kind == 'shapeshift':
        return 1
    # ★ **形态子技能**（贪噬「暴击回血」/ 血莽 / 冰川之爪 / 永冬 / 野兽形态…）：
    #   `deps` 指向形态本体，直接强化形态的核心输出，必须排在普通攻击之前。
    #   实测：旧版按 tier 排，这些会被猛袭等**变形后用不了**的技能挤掉。
    deps = d.get('deps') or ([d['depend']] if d.get('depend') else [])
    if form_roots and any(x in form_roots for x in deps):
        return 2
    if is_wps(d):
        return 3
    if kind and kind.startswith('attack'):
        return 4
    if kind in ('modifier', 'buff_timed', 'buff_toggle', 'buff_self',
                'buff_radius', 'buff_radius_toggle'):
        return 5
    if kind in ('passive', 'passive_proc'):
        return 6
    return 7


def _level_table():
    """官方升级给点表（gt_data/level_table.json，由 gt_extract.py levels 生成）"""
    return _load('level_table.json', {}) or {}


# archetype 可以**故意**不带某些「可点 WPS」—— 只有**实测加了更低**的才写进来，
# 并注明依据。`tools/selftest.py` 的 WPS 覆盖率断言会拿这张表做豁免。
WPS_OPT_OUT = {
    # human（人形态 · 猛袭流）不带「雪崩」(`playerclass10/wpattack02`)：
    # 塞进 core 会用 10 点换掉 `passive01/02/03`（乘全区），实测
    # 77,920 → 75,038（**−3.7%**）。human 的 WPS 由「猛袭」+「血牙」承担。
    'human': ('records/skills/playerclass10/wpattack02.dbr',),
}


def reachable_wps(archetype):
    """该形态**可点**的 WPS（武器池技能）记录，以及 `core_skills` 漏掉的那些。

    与 `_priority` / `allocate` 同口径的三条筛选：
      ① `class` ∈ 该形态的 `masteries`（缺省按 `mastery` 字段，再缺省 class10）
      ② tier 门槛 ≤ 对应精通等级（`tier_milestone`）
      ③ 变身形态（狼人/鸦人/完美姿态）下变形后技能栏被替换 ⇒ `_FORM_LOCKED` 的点不出来

    ★ 为什么需要它：`gd/alloc.allocate` 的贪心是「core 吃满预算再轮 rest」，
      所以**漏进 core_skills 的 WPS 永远点不到**。实测 `avalanche` 漏了
      class10 的「血牙」与 class04 的「死亡旋风」，补上后 65,697 → **79,034**
      （+20.3%）。这条不变量由 `tools/selftest.py` 守住。
    """
    skills = _load('skills.json', {}) or {}
    arch = (_load('archetypes.json', {}) or {}).get(archetype) or {}
    roots = arch.get('root_skills') or []
    my_form = _form_of(roots[0]) if roots else None
    ms = arch.get('masteries')
    if ms is None:
        m = str(arch.get('mastery') or '')
        num = int(m[5:]) if (m.startswith('class') and m[5:].isdigit()) else 10
        ms = [[num, 50]]
    cls = {'class%02d' % int(x[0]): int(x[1]) for x in ms}
    core = set(arch.get('core_skills') or [])
    reach, miss = set(), set()
    for r, d in skills.items():
        if not is_wps(d):
            continue
        c = d.get('class')
        if c not in cls:
            continue
        t = d.get('tier')
        if t is not None and cls[c] < tier_milestone(t):
            continue
        if os.path.basename(r).startswith('_'):
            continue
        if my_form and (d.get('kind') or '') in _FORM_LOCKED:
            continue
        reach.add(r)
        if r not in core:
            miss.add(r)
    return reach, miss


# 手写公式的兜底（表缺失时用），与官方表在 **≤90 级** 完全一致：
#   2..50 级每级 3 点 / 51..90 级每级 2 点 / 91..100 级每级 1 点
_PIECEWISE = ((1, 50, 3), (51, 90, 2), (91, 100, 1))


def _resolve_skill(token, skills, cls_set=()):
    """把「中文名」/「记录名」/「basename」解析成 skills.json 里的记录路径。

    供 `GD_ALLOC`（显式加点）使用 —— 让人用**中文名**写加点表，不必记记录名。

    ★ 中文名会重名：`双刃` 同时也是一件双持手枪（`item_dualpistols.dbr`），
      `气爆` 同时是 7 条敌人技能的译名。所以候选要**按可信度排序**：
        ① 记录属于本流派的职业（`cls_set`）优先
        ② 排除宠物/其它（kind 为 pet / other）的记录
        ③ 路径短者优先
    """
    t = (token or '').strip()
    if t in skills:
        return t
    base = t if t.endswith('.dbr') else t + '.dbr'
    low = base.lower()

    def rank(r):
        d = skills.get(r) or {}
        kind = d.get('kind') or ''
        return (0 if d.get('class') in cls_set else 1,
                1 if ('pet' in kind or kind == 'other') else 0,
                len(r))

    hits = [r for r in skills if os.path.basename(r).lower() == low]
    if not hits:
        hits = [r for r, d in skills.items() if (d.get('name') or '') == t]
    if not hits:
        return None
    hits.sort(key=rank)
    if len(hits) > 1:
        print('   · GD_ALLOC：「%s」有 %d 条同名/同基名记录，取 %s'
              % (t, len(hits), os.path.basename(hits[0])))
    return hits[0]


def skill_budget(level):
    """升到 level 级可用的总技能点 = 等级累计 + 任务奖励。

    ★ 权威来源是 `gt_data/level_table.json`（GT `calc.js` 的 `window.playerBio`，
      内含 `skillPointsIncrement` 逐级数组）。手写分段式只作兜底：
      实测 91 级起每级只给 **1** 点，而旧版 `max(0, lv-50)*2` 会一直按 2 点算，
      到 100 级多算 10 点（260 vs 官方 250）。
    """
    lv = int(level)
    cum = (_level_table().get('skill_cumulative') or {})
    if str(lv) in cum:
        return int(cum[str(lv)]) + TASK_SKILL_POINTS
    run = 0
    for lo, hi, per in _PIECEWISE:
        if lv >= lo:
            run += (min(lv, hi) - lo + 1) * per
    return run + TASK_SKILL_POINTS


def attr_budget(level):
    """总属性点 = (等级-1) × 每级增量 + 任务奖励。

    ★ **每级只给 1 点**（官方 `attributePointsIncrement:1`，`maxAttributePoints:99`
      = 100 级 99 点，自洽）。这是 Grim Dawn 最容易被记错的一条：
      属性点极稀缺，主要靠装备补。
    """
    lt = _level_table()
    per = lt.get('attribute_points_increment')
    return (int(level) - 1) * (per if per else 1) + TASK_ATTR_POINTS


def devotion_budget(level):
    """虔诚点 = 神殿解锁数；官方上限 `maxDevotionPoints:55`"""
    lt = _level_table()
    cap = lt.get('max_devotion_points') or 55
    return min(int(level), cap)


def attr_per_point():
    """1 点属性 = +N 面板（官方 `strengthIncrement:8`）"""
    return _level_table().get('strength_increment') or 8


def _load(name, default):
    p = os.path.join(HERE, name)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else default


def allocate(archetype, level=65, mastery_level=50, mastery_class=10, masteries=None):
    """在预算内产出一份技能加点方案。

    masteries : [[职业号, 精通等级], …]，默认读 archetype 的 `masteries` 字段
                （双职业流派如「士兵+夜刃」= [[1, 50], [4, 32]]）

    返回 {'skills': [...], 'used': n, 'budget': n, 'left': n, 'mastery': {...}}
    """
    skills = _load('skills.json', {}) or {}
    arch = (_load('archetypes.json', {}) or {}).get(archetype) or {}
    budget = skill_budget(level)

    # ★ 空列表 `[]` = **明确表示「无精通条」**（如圣物形态 fangs —— 它不属于任何职业）。
    #   旧写法 `or` 会把空列表当成"未指定"→ 回退成 [[10,50]]，白点 50 点精通。
    ms = masteries
    if ms is None:
        ms = arch.get('masteries')
        if ms is None:
            ms = [[mastery_class, mastery_level]]
    used = 0
    plan = []
    cls_set = set()
    mast_lv = {}                     # 'class01' → 该职业的精通等级（用于 tier 门槛）
    for item in ms:
        cnum, mlv = int(item[0]), int(item[1])
        cls_set.add('class%02d' % cnum)
        m_rec = MASTERY_REC % (cnum, cnum)
        if m_rec in skills:
            lv = min(mlv, (skills[m_rec].get('max_level') or 50))
            plan.append({'skill': m_rec, 'level': lv,
                         'why': '%s精通条' % (skills[m_rec].get('name') or '职业')})
            used += lv
            mast_lv['class%02d' % cnum] = lv

    # ★ tier 门槛：技能树第 N 层需要本职业精通等级 ≥ milestones[N-1]。
    #   不校验会产出「点了 tier9 但精通只有 32」的非法方案（游戏里根本点不出来）。
    blocked = []

    def _tier_ok(rec):
        d = skills.get(rec) or {}
        t = d.get('tier')
        if t is None:
            # ★ tier 缺失 ≠ 不能点。有些技能的 dbr 只是个「壳」——真正的数据在
            #   `_buff.dbr` 里（例：审判官 `wordofpain1.dbr` 只有 4 个字段：
            #   templateName / Class / buffSkillName / pointBlank）。这类记录
            #   读不到 skillTier，**当初拒绝会误杀核心技能**，所以放行；
            #   真正的垃圾（skeleton_*/wraith_* 等）靠 kind 与 rest 池的
            #   `tier is not None` 条件挡住。
            return True
        got = mast_lv.get(d.get('class'), 0)
        if got >= tier_milestone(t):
            return True
        blocked.append((rec, t, tier_milestone(t), got))
        return False

    # ★ 形态互斥：变身分支（狼人 / 鸦人 / 完美姿态）不能共存。
    #   本流派的形态由 `root_skills` 判定；`root_skills` 为空 = 不变身流派（如 human、
    #   纯职业组合），此时**排除全部形态技能**。
    _roots = arch.get('root_skills') or []
    my_form = _form_of(_roots[0]) if _roots else None

    def _form_ok(rec):
        f = _form_of(rec)
        return f is None or f == my_form

    core = [r for r in (arch.get('core_skills') or [])
            if r in skills and _tier_ok(r) and _form_ok(r)]
    core.sort(key=lambda r: (_priority(skills[r], _roots), _tier_key(skills[r]), r))

    left = budget - used
    core_set = set(core)
    # 二级池：流派核心之外，**同（多）职业**里真正能点的技能
    #   （剔除 modifier / transmuter —— 它们多是 1 级开关或技能改造，不是加点项）
    rest = [r for r, d in skills.items()
            if d.get('class') in cls_set
            and r not in core_set
            and d.get('tier') is not None
            and d.get('kind') not in ('modifier', 'transmuter', 'other', None)
            and not os.path.basename(r).startswith('_')
            and not os.path.basename(r).startswith('petskill')   # 宠物技能，玩家点不了
            # ★ 形态流派：变形后技能栏被替换，基础主动攻击技能点不出来
            #   （实测狼人变身后只有 grantedSkills 的野性利爪/狂乱撕扯）
            and not (my_form and (skills[r].get('kind') or '') in _FORM_LOCKED)
            and _tier_ok(r) and _form_ok(r)]
    rest.sort(key=lambda r: (_priority(skills[r], _roots), _tier_key(skills[r]), r))

    # ★★ 2026-09-20 实测记录（**别再把两池合表**）
    #    `core_skills` 是 archetype 的**手写精选清单**，`rest` 只是「core 吃不满
    #    预算时」的填充（唯一真正吃到 rest 的是 `fangs`：core+rest 合计 149 < 202）。
    #    曾提议改成两池合表 `(优先级, 是否 core, tier, 名称)`，实测**净劣化**：
    #
    #    | 形态 | 旧（core+rest） | 全合表 | 差 |
    #    |---|---|---|---|
    #    | avalanche | 65,697 | **79,034** | +20.3% |
    #    | human | **77,920** | 71,925 | **−7.7%** |
    #    | 其余 6 形态 | — | 逐位一致 | 0 |
    #
    #    原因：合表会把**优先级 4/5 的攻击技能**整体提到**优先级 6 的被动**之前，
    #    而被动在模型里是乘全区（human 掉的 7.7% 全来自被动被挤掉）。
    #    ⇒ 正确修法是**补数据**：archetype 的 `core_skills` 漏了 WPS 就补上
    #      （见 `data/archetypes.json` 的 `avalanche`）。补数据后 avalanche
    #      同样拿到 79,034（+20.3%），且**零附带损伤**。

    # ★ 显式加点覆盖（环境变量 `GD_ALLOC`）：自动贪心只是**默认答案**，不是唯一答案。
    #   有些流派的取舍靠"点数不够"是排不出来的（例：穿刺流里「双刃」这个 +%穿刺被动
    #   与「武器池 proc」谁是第一优先，取决于玩家的打法），所以留一个手工入口。
    #   用法：GD_ALLOC="狼人形态:16,野性利爪:16,双刃:16"（名称可用中文名或记录名；
    #         不写等级 = 该技能上限）
    spec = os.environ.get('GD_ALLOC')
    if spec:
        for part in spec.split(','):
            name, _, lv = part.partition(':')
            name = name.strip()
            if not name:
                continue
            rec = _resolve_skill(name, skills, cls_set)
            if not rec:
                print('   ⚠ GD_ALLOC：找不到技能「%s」，已跳过' % name)
                continue
            d = skills[rec]
            if not _tier_ok(rec):
                print('   ⚠ GD_ALLOC：%s 的 tier 门槛未满足，已跳过' % name)
                continue
            if not _form_ok(rec):
                print('   ⚠ GD_ALLOC：%s 属其他形态分支，已跳过' % name)
                continue
            mx = int(d.get('max_level') or 1)
            try:
                lv_i = int(lv.strip()) if lv.strip() else mx
            except ValueError:
                print('   ⚠ GD_ALLOC：%s 的等级「%s」不是数字，已跳过' % (name, lv))
                continue
            if lv_i > mx:
                print('   ⚠ GD_ALLOC：%s 等级 %d 超上限 %d，已截到 %d'
                      % (name, lv_i, mx, mx))
                lv_i = mx
            if lv_i <= 0:
                continue
            plan.append({'skill': rec, 'level': lv_i, 'why': d.get('name') or name})
            used += lv_i
        left = budget - used
        if left < 0:
            print('   ✗ GD_ALLOC：合计 %d 点 > 预算 %d（超 %d 点）'
                  % (used, budget, -left))
        return {'skills': plan, 'used': used, 'budget': budget, 'left': left,
                'mastery': {'masteries': ms}, 'archetype': archetype,
                'blocked_count': len(blocked), 'blocked_by_mastery': blocked,
                'explicit': True}

    for rec in core + rest:
        if left <= 0:
            break
        d = skills.get(rec)
        if not d:
            continue
        mx = int(min(d.get('max_level') or 1, 20))
        take = min(mx, left)
        if take <= 0:
            continue
        plan.append({'skill': rec, 'level': take,
                     'why': d.get('name') or d.get('kind')})
        used += take
        left -= take

    held = sorted({(os.path.basename(r), t, need, got) for r, t, need, got in blocked})
    return {'skills': plan, 'used': used, 'budget': budget, 'left': left,
            'mastery': {'masteries': ms, 'levels': mast_lv},
            'blocked_by_mastery': held[:20], 'blocked_count': len(held),
            'archetype': archetype}


def check(plan_skills, level):
    """校验一份加点是否超预算；返回 (ok, used, budget, msg)"""
    used = sum(int(s.get('level') or 0) for s in plan_skills)
    b = skill_budget(level)
    ok = used <= b
    msg = '技能点 %d / 预算 %d（%s）' % (used, b, '✓ 合法' if ok else '✗ 超出 %d' % (used - b))
    return ok, used, b, msg


# ------------------------------------------------------------------ 属性点分配
_DB = None


def _db():
    global _DB
    if _DB is None:
        from . import dbr as DB
        _DB = DB.open_all()
    return _DB


ATTR_FIELD = {'physique': 'characterStrength',
              'cunning': 'characterDexterity',
              'spirit': 'characterIntelligence'}
MASTERY_ATTR = {'physique': 4, 'cunning': 4, 'spirit': 2}      # 兜底（= class10 实测值）
BASE_ATTR = 50                                                  # 1 级基础
PER_POINT = attr_per_point()                                    # 1 点 = +8（官方 strengthIncrement）

_MASTERY_COEF = None


def mastery_coef():
    """各职业精通条「每级」给的属性 —— 实测自 `_classtraining_classNN.dbr`。

    以前只知道 class10（狂战士）是 4/4/2（SKILL §36.1），现在 10 个职业全测出来了：

      | 职业 | 力 | 敏 | 智 | 生命 | 能量 |
      |---|---|---|---|---|---|
      | class01 士兵 | **5** | 3 | 2 | 28 | 10 |
      | class02 爆破 | 4 | 4 | 3 | 24 | 14 |
      | class03 秘术 | 3 | 3 | 5 | 20 | 18 |
      | class04 夜刃 | **4** | **4** | 3 | 25 | 13 |
      | class05 奥术 | 2 | 3 | 5 | 18 | 20 |
      | class06 萨满 | 4 | 3 | 3 | 26 | 12 |
      | class07 审判 | 3 | 4 | 4 | 23 | 15 |
      | class08 死灵 | 4 | 2 | 4 | 19 | 19 |
      | class09 誓约 | 5 | 3 | 3 | 25 | 13 |
      | class10 狂战 | 4 | 4 | 2 | 27 | 11 |
    """
    global _MASTERY_COEF
    if _MASTERY_COEF is None:
        db = _db()
        out = {}
        for c in range(1, 11):
            rec = 'records/skills/playerclass%02d/_classtraining_class%02d.dbr' % (c, c)
            f = db.fields(rec) or {}
            def g(k):
                v = f.get(k)
                return float(v[0]) if v and isinstance(v[0], (int, float)) else 0.0
            out['class%02d' % c] = {'physique': g('characterStrength'),
                                    'cunning': g('characterDexterity'),
                                    'spirit': g('characterIntelligence'),
                                    'life': g('characterLife')}
        _MASTERY_COEF = out
    return _MASTERY_COEF


def tier_milestone(tier):
    """技能 tier → 所需精通等级（官方 `engine.milestones`）。

    `[1,5,10,15,20,25,32,40,50]`：tier1 需精通 1、tier5 需 20、tier7 需 **32**、
    tier8 需 **40**、tier9 需 **50**。tier0 = 精通条本身，无门槛。
    兜底用同一张表（表缺失时）。
    """
    t = int(tier or 0)
    if t <= 0:
        return 0
    ms = _level_table().get('mastery_milestones') or [1, 5, 10, 15, 20, 25, 32, 40, 50]
    return int(ms[min(t, len(ms)) - 1])


def mastery_attr(masteries):
    """多职业精通条合计的属性加成。masteries = [[职业号, 精通等级], …]

    ★ 走**官方逐级曲线**（`data/mastery_attr.json` + `gd.rotation.mastery_attr_of`），
      不再用「每级常数 × 等级」的线性近似 —— 实测两者会漂：
      Sam（class04 lv35 + class10 lv50）线性得 **体格 340**，曲线真值是 **323**
      （差 17 点，足以把「差 20 点体格」误判成「差 3 点」）。
      `mastery_coef()` 的表只作为曲线缺失时的兜底。
    """
    from . import rotation as _R
    coef = mastery_coef()
    out = {'physique': 0.0, 'cunning': 0.0, 'spirit': 0.0}
    for item in masteries or []:
        cls = 'class%02d' % int(item[0])
        lv = int(item[1])
        curve = None
        try:
            curve = {k: float(_R.mastery_attr_of(cls, lv, k) or 0.0) for k in out}
        except Exception:
            curve = None
        if curve and any(curve.values()):
            for k in out:
                out[k] += curve[k]
            continue
        c = coef.get(cls)                      # 兜底：线性
        if c:
            for k in out:
                out[k] += float(c[k]) * lv
    return out


def attribute_plan(gear_records, level=65, masteries=None, archetype=None,
                   mastery_level=50, mastery_class=10):
    """按「装备需求」反推属性点分配（严格按等级预算）。

    gear_records : {gt_id: dbr 记录名 或 None}
    面板公式：面板 = 50 + 8×加点 + 精通(4L/4L/2L) + 装备自带属性加成

    返回 {'points': {…}, 'used': n, 'budget': n, 'left': n,
          'need': {…}, 'panel': {…}, 'gear_add': {…}, 'detail': [...]}
    """
    import math
    from . import req as R
    if masteries is None:
        _arch = (_load('archetypes.json', {}) or {}).get(archetype or '') or {}
        _ms = _arch.get('masteries')
        masteries = [[mastery_class, mastery_level]] if _ms is None else _ms
    recs = {k: v for k, v in (gear_records or {}).items() if v}
    req = R.panel_needed(list((gear_records or {}).keys()), recs)
    mast = mastery_attr(masteries)

    # 装备自带的属性加成（gd_dbr 能读，这是过去拿不到的一块）
    add = {k: 0.0 for k in ATTR_FIELD}
    db = _db()
    for rec in recs.values():
        f = db.fields(rec) or {}
        for dst, key in ATTR_FIELD.items():
            v = f.get(key)
            if v and isinstance(v[0], (int, float)):
                add[dst] += float(v[0])

    need = {}
    for k in ATTR_FIELD:
        gap = req[k] - BASE_ATTR - mast[k] - add[k]
        need[k] = max(0, int(math.ceil(gap / float(PER_POINT))))
    used = sum(need.values())
    budget = attr_budget(level)
    left = budget - used

    # 剩余点数按形态倾向投（提升面板伤害）
    spend = dict(need)
    if left > 0 and archetype:
        arch = (_load('archetypes.json', {}) or {}).get(archetype) or {}
        bias = arch.get('attribute_bias') or {}
        if bias:
            tot = sum(bias.values()) or 1.0
            for k, w in sorted(bias.items(), key=lambda x: -x[1]):
                if k not in spend:
                    continue
                give = int(left * w / tot)
                if k == sorted(bias.items(), key=lambda x: -x[1])[0][0]:
                    give = left - (sum(int(left * v / tot) for kk, v in bias.items() if kk != k))
                spend[k] += max(0, give)
        else:
            spend['physique'] += left
    elif left > 0:
        spend['physique'] += left

    panel = {k: BASE_ATTR + PER_POINT * spend[k] + mast[k] + add[k] for k in ATTR_FIELD}
    detail = []
    for gid, r in (req.get('items') or {}).items():
        if r['confidence'] == 'none':
            continue
        detail.append('%s %s 需力%s/敏%s/智%s [%s]' % (
            gid, r['slot'] or '-', r['physique'] or 0, r['cunning'] or 0,
            r['spirit'] or 0, r['confidence']))
    return {'points': spend, 'used': sum(spend.values()), 'budget': budget,
            'left': budget - sum(spend.values()), 'need': need, 'panel': panel,
            'gear_add': add, 'req': {'physique': req['physique'], 'cunning': req['cunning'],
                                     'spirit': req['spirit']},
            'detail': detail}


if __name__ == '__main__':
    arch = sys.argv[1] if len(sys.argv) > 1 else 'wereraven'
    level = int(sys.argv[2]) if len(sys.argv) > 2 else 65
    print('等级 %d ｜ 技能点预算 %d ｜ 属性点预算 %d ｜ 虔诚点上限 %d'
          % (level, skill_budget(level), attr_budget(level), devotion_budget(level)))
    r = allocate(arch, level)
    print('形态 %s：已分配 %d，剩余 %d' % (arch, r['used'], r['left']))
    for s in r['skills']:
        print('   %-46s -> %-3d %s' % (s['skill'].split('/')[-1], s['level'], s.get('why', '')))
