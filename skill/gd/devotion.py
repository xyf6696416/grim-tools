# -*- coding: utf-8 -*-
r"""星座（虔诚）筛选与落档（gd_devotion）—— 2026-09-17 新增（v3 P2 第一步）

范围说明（**诚实标注**）
------------------------
本模块实现的是**按目标伤害类型筛选星座节点**，并产出可直接落档的星座清单。
**尚未实现**：亲和力预算约束、连线可达性校验（这两项数据在 GT `calc.js` 的
`devotionConstellation*` 结构里，`gt_extract` 目前只从 `.dbr` 提取了节点数值）。

所以当前产出的星座方案**可能包含点不出来的组合**，落档前需要用亲和力数据复核。
下面的 `check_affinity()` 留了接口，等数据补齐后接上。

可用的部分（已验证）
--------------------
  · 星座节点数据：`gt_data/devotions.json`（716 条，含被动加成数值）
  · **落档链路已通**：星座在存档里就是技能条目，走 `gd_skill` 即可写入
    （`gd_profile.py` 的 `devotions` 字段已接上，并做**全量重建**）

用法
----
```python
from . import devotion as D
sel = D.select('wereraven', budget=55, topn=20)
print(sel['picks'])
```
"""
import json
import os
import sys

from . import paths as _PATHS

# 旧脚本用 HERE 拼数据文件路径；新架构下数据在技能的 data/ 里
HERE = str(_PATHS.DATA_DIR)
_CACHE_ROOT = str(_PATHS.CACHE_DIR)
_PLANS = str(_PATHS.CACHE_DIR / 'plans')



# 通用优质字段（任何流派都吃）
DEFAULT_GOOD = (
    'offensiveTotalDamageModifier',
    'characterOffensiveAbility',
    'characterAttackSpeedModifier',
    'characterDefensiveAbility',
    'characterLife',
)

# 无形态别名时的默认（物理向）
FALLBACK_OFFENSE = (
    'offensivePhysicalModifier', 'offensivePierceModifier',
    'offensiveSlowBleedingModifier',
)


def _load(name, default):
    p = os.path.join(HERE, name)
    return json.load(open(p, encoding='utf-8')) if os.path.exists(p) else default


def offense_fields(archetype):
    """该流派「专属伤害」字段（权重更高）"""
    arch = (_load('archetypes.json', {}) or {}).get(archetype) or {}
    out = set()
    for _dim, fld in (arch.get('field_aliases') or {}).items():
        if isinstance(fld, str) and fld.startswith('offensive'):
            out.add(fld)
    return out or set(FALLBACK_OFFENSE)


def score_node(d, fields, granted_weight=1.0, offense=None):
    """单个星座节点的得分。

    ★ 专属伤害字段按 **3 倍** 计权：否则 `characterOffensiveAbility` /
    `characterLife` 这类通用字段会盖过伤害类型，导致狼人与鸦人选出**同一批节点**
    （实测两形态 top-12 完全重合）。
    """
    offense = offense if offense is not None else ()
    sc = 0.0
    stats = d.get('stats') or {}
    for k, vals in stats.items():
        if not vals or k not in fields:
            continue
        v = vals[0]
        if isinstance(v, (int, float)):
            sc += float(v) * (3.0 if k in offense else 1.0)
    if d.get('granted'):
        sc += 40.0 * granted_weight          # 带授予技能的星座更值钱
    return sc


def select(archetype, budget=55, topn=None, exclude_skills=True):
    """按流派筛星座。

    budget : 虔诚点预算（每节点按 1 点估，见 gd_alloc.devotion_budget）
    topn   : 取前 N 个节点（默认 = budget）
    """
    devs = _load('devotions.json', {}) or {}
    off = offense_fields(archetype)
    fields = set(DEFAULT_GOOD) | off
    rows = []
    for rec, d in devs.items():
        if exclude_skills and d.get('is_skill'):
            continue                             # `_skill.dbr` 是节点授予的技能，不是节点
        sc = score_node(d, fields, offense=off)
        if sc <= 0:
            continue
        rows.append((sc, rec, d))
    rows.sort(key=lambda x: -x[0])
    n = topn or budget
    picks = rows[:n]
    return {
        'archetype': archetype,
        'budget': budget,
        'fields': sorted(fields),
        'offense_fields': sorted(off),
        'picks': [{'record': rec, 'level': 1, 'score': round(sc, 1),
                   'name': d.get('name'), 'granted': d.get('granted') or []}
                  for sc, rec, d in picks],
        'considered': len(rows),
        'note': '未做亲和力/连线校验（数据待补），落档前请复核',
    }


def _tree():
    return _load('devotion_tree.json', {}) or {}


def star_prefix(record):
    """`records/skills/devotion/tier3_21b.dbr` → `tier3_21`（星座的 star 前缀）"""
    base = os.path.basename(record)
    if base.endswith('.dbr'):
        base = base[:-4]
    return base[:-1] if base and base[-1].isalpha() else base


_TAGS = None


def _tags():
    global _TAGS
    if _TAGS is None:
        try:
            from . import gear as G
            _TAGS = G.load_tags()
        except Exception:
            _TAGS = {}
    return _TAGS


def constellation_of(record, tree=None):
    """star 记录 → (星座索引, 星座数据)

    ★ 关联方式（按可靠性排序）：
      ① **星座名匹配**：star 的中文名里含星座名
         （`tier3_21b` = 「无尽贪夜 ~ 尤戈尔」，而星座 110 的 tag 译名是「尤戈尔」）
      ② star 前缀匹配：图标名 `tier3-021` → 记录前缀 `tier3_21`
    原先只用 ②，实测 110 个星座里只有 23 个能关联（多数星座的图标字段结构不同），
    覆盖率太低 → 改用 ① 优先。
    """
    tree = tree or _tree()
    devs = _load('devotions.json', {}) or {}
    d = devs.get(record) or {}
    nm = d.get('name') or ''
    if nm:
        for idx, con in tree.items():
            zh = _tags().get(con.get('tag') or '')
            if zh and zh in nm:
                return idx, con
    pre = star_prefix(record)
    for idx, con in tree.items():
        if con.get('star_prefix') == pre:
            return idx, con
    return None, None


def check_affinity(picks, tree=None):
    """亲和力可行性校验（**近似模型**）。

    规则（对游戏规则的近似）：
      · 每个选中的 star 为其所属星座的**主亲和力类型**贡献 1 点；
      · 星座的 `affinityRequired*` 必须被总亲和力满足。

    ⚠ 与游戏真实规则仍有差距（真实规则里 star 需按连线顺序点、星座有前置），
    所以本函数是**粗筛**：报「不满足」基本就一定有问题，报「满足」仍需游戏内确认。
    """
    tree = tree or _tree()
    from collections import Counter
    got = Counter()
    grouped = {}
    unknown = []
    for p in picks:
        rec = p.get('record') or p.get('skill') or ''
        idx, con = constellation_of(rec, tree)
        if con is None:
            unknown.append(rec)
            continue
        pre = con.get('star_prefix')
        grouped.setdefault(pre, {'idx': idx, 'con': con, 'n': 0})
        grouped[pre]['n'] += 1
        if con.get('affinity'):
            got[con['affinity'][0][0]] += 1

    bad = []
    for pre, g in grouped.items():
        con = g['con']
        for name, val in con.get('affinity') or []:
            if got[name] < val:
                bad.append('%s 需 %s %d（现有 %d）'
                           % (con.get('tag') or pre, name, val, got[name]))
    ok = not bad
    msg = ('涉及 %d 个可识别星座 ｜ 无法关联的 star %d 个' % (len(grouped), len(unknown)))
    if bad:
        msg += ' ｜ ✗ 亲和力不足: ' + '; '.join(bad[:3])
    else:
        msg += ' ｜ ✓ 亲和力近似自洽'
    return ok, msg, dict(got), grouped


def select_coherent(archetype, budget=55, verbose=False):
    """★ **按星座整体 + 亲和力自洽**的贪心选择（推荐用这个，不是 select）。

    为什么必须按星座整体选：实测按 star 散点选 55 个 → 亲和力必然不足
    （`Ascendant` 需 12 只有 5、`Eldritch` 需 20 只有 15），因为亲和力来自
    **同一个星座里已点的 star**。散点跨星座乱选等于点不出来。

    规则（对游戏的近似）：
      · 每个星座的 star 要么整组选、要么不选；
      · 亲和力 = 已选 star 按其所属星座**主亲和类型**累加；
      · 候选星座的 `affinityRequired*` 必须被当前亲和力满足（首个星座放宽，
        因为 tier1 的 star 在游戏里没有亲和力门槛）。
    """
    devs = _load('devotions.json', {}) or {}
    tree = _tree()
    off = offense_fields(archetype)
    fields = set(DEFAULT_GOOD) | off

    groups = {}
    for rec, d in devs.items():
        if d.get('is_skill'):
            continue
        idx, con = constellation_of(rec, tree)
        if con is None:
            continue
        g = groups.setdefault(idx, {'con': con, 'stars': [], 'score': 0.0})
        g['stars'].append(rec)
        g['score'] += score_node(d, fields, offense=off)

    order = sorted(groups.items(), key=lambda x: -x[1]['score'])
    from collections import Counter
    aff = Counter()
    picked, used = [], 0
    used_idx = set()
    for round_no in range(3):                     # 多趟：后续趟吃新积累的亲和力
        for idx, g in order:
            if idx in used_idx:
                continue
            con = g['con']
            n = len(g['stars'])
            if used + n > budget:
                continue
            req = con.get('affinity') or []
            need_ok = all(aff[nm] >= v for nm, v in req) if req else True
            if not need_ok and not picked and req:
                # 首个星座只放宽**主亲和**（tier1 的 star 在游戏里没有门槛），
                # 副亲和仍须满足 —— 否则会选进"主亲和够、副亲和缺"的点不出来的星座
                need_ok = all(aff[nm] >= v for nm, v in req[1:])
            if not need_ok:
                continue
            picked.append({'idx': idx, 'con': con, 'score': round(g['score'], 1),
                           'stars': sorted(g['stars']), 'name': _tags().get(con.get('tag') or ''),
                           'affinity': req})
            used_idx.add(idx)
            used += n
            if req:
                aff[req[0][0]] += n

    ok, msg, got, _ = check_affinity(
        [{'record': s} for p in picked for s in p['stars']], tree)
    out = {
        'archetype': archetype, 'budget': budget, 'used': used,
        'picks': picked, 'affinity': dict(got), 'coherent': ok,
        'msg': msg,
        'records': [s for p in picked for s in p['stars']],
    }
    if verbose:
        print(msg)
    return out


if __name__ == '__main__':
    arch = sys.argv[1] if len(sys.argv) > 1 else 'wereraven'
    import gd_alloc as A
    budget = A.devotion_budget(65)
    print('=== 散点模式（旧）===')
    r = select(arch, budget=budget)
    print('  %s' % check_affinity(r['picks'])[1])
    print()
    print('=== 星座整体 + 亲和力自洽（推荐）===')
    c = select_coherent(arch, budget=budget)
    print('  用 %d/%d 点 ｜ 选中 %d 个星座 ｜ %s' % (c['used'], budget, len(c['picks']), c['msg']))
    print('  亲和力：%s' % c['affinity'])
    for p in c['picks'][:10]:
        print('     %-22s 分=%-7.1f star%2d 需 %s'
              % ((p['name'] or str(p['idx']))[:22], p['score'], len(p['stars']), p['affinity']))
