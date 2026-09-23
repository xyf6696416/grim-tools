#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gt_regress.py —— grimtools 社区构建 → 本引擎重算 → 数值回归对拍（阶段 4）。

## 为什么值得做
社区成熟构建是**现成的回归语料**：同一份 `buildInfo` 既能读出「人家怎么配的」，
也能喂给我们的引擎算一遍。有了它，`gd/` 里任何一处改动只要动了数值，
立刻能在 N 个真实构建上看到偏差 —— 这是唯一能证明「我们的引擎 == 游戏」的手段。

## 离线约束（**重要**）
面板数字是 grimtools 页面里 `calc.js` **在浏览器里现算**的，HTML 里根本没有
（已实证：`gt_ZyDo860V.html` 只有标签文本，没有数值）。
本项目约定**不抓网页、不用 CDP**，所以参考值只能**录入一次**：

    python tools/gt_regress.py ZyDo860V --emit-ref data/regress/ZyDo860V.ref.json
    #  → 出一份模板（我们算出来的值已填好，留空的键请照 grimtools 面板抄）
    #  → 抄完再跑：
    python tools/gt_regress.py ZyDo860V --ref data/regress/ZyDo860V.ref.json

另外两条不需要人工录入、纯离线的回归：
  · `--golden` 把本次算出的全部指标冻结到 `data/regress/<ID>.golden.json`；
    之后每次跑都跟它对拍 —— 抓的是**我们自己的数值漂移**（改代码回归）。
  · `--json` 出一行 JSON，便于批量/CI。

## 链路（全部离线，除首次抓页）
    gt_<ID>.html ──buildInfo──▶ 归一层
        ├ 装备 14 槽（英文槽位 → 中文槽位）  → {槽位: [gid…]}
        ├ 技能 `sk####` → 记录名 + 等级
        │     ① `itemdb.js` 的 `name` / `calc.js` 的 `skillDisplayName` → tag
        │     ② 职业/精通：`data/mastery_skills.json[tag].record`
        │     ③ 星座星点：`data/devotions.json` 同 tag 候选中按**字段签名**唯一匹配
        │        （由 `calc.js` 的 `sk####` 字段做指纹，见 `resolve_skills`）
        └ `bio` / `masteries` → 等级 / 属性 / 职业
    ──▶ `gd.dps.load_char(name='', …overrides)`（**不读存档**）
    ──▶ `gd.rotation.final_report` + `gd.rr` → 指标字典
    ──▶ 对比 ref / golden → 表格 + 退出码

## 已知口径差异（对拍时会显示为偏差，不是 bug）
  · **随机词缀**：`pre####`/`suf####`/`aa####` 是 `LootRandomizer`，骰出的具体数值
    只存在于 GT 自己那份 `itemdb.js` 里（`aa####` 连记录都没有）。我们按
    **dbr 模板值**算 ⇒ 这些槽会偏。报告里会单独列出「受随机词缀影响的槽」。
  · **难度抗性惩罚**：GD 终极难度所有抗性 -50%（`--res-penalty 50` 对齐面板）。
  · ★ **OA / DA / 暴击 / 命中自 2026-09-19（伤害模型 v2）起**已进引擎：
    `gd/combat.py` 实现官方 PTH 方程与暴击窗口，`gd/rotation.py` 把它们接进
    主流水线（`rep['oa'] / ['da'] / ['pth'] / ['hit']`）。所以它们现在**能**对拍，
    但**上面这份 grimtools 参考值的键表还没扩**（`TOL` 里没有这些键）——
    要扩请直接往 `TOL` 加，别再写「引擎不建模」。
  · `skills[].level` 实测是**已分配点数**（本例 305 = 满级角色技能点预算，
    且无一技能超过 `max_level`）⇒ 装备 `+技能` 仍由引擎按 `skill_plus` 叠加。

## 第三条通道：`--sheet`（**游戏内实测**对拍，Q9）
前两条通道都对不上游戏本体（一条是社区构建、一条是我们自己）。
`--sheet <角色名>` 读 `data/regress/<角色名>.sheet.json` —— 那是**手工誊录的游戏
面板真值**（角色二/三页：OA/DA/攻速/暴伤/护甲；角色一页：10 项抗性）。
它是唯一能证伪「引擎 == 游戏」的证据来源。

    python tools/gt_regress.py --sheet Sam --emit-sheet   # 出模板（我方值已填）
    #   → 打开 data/regress/Sam.sheet.json，把 in_game 段的 null 照面板抄上
    python tools/gt_regress.py --sheet Sam                # 对拍

**没填的键会明确报「未填写」而不是静默跳过** —— 覆盖率是这套方法唯一会骗人的地方。

用法：
    python tools/gt_regress.py ZyDo860V                       # 出报告
    python tools/gt_regress.py ZyDo860V --json                # 一行 JSON
    python tools/gt_regress.py ZyDo860V --emit-ref R.json     # 出对拍模板
    python tools/gt_regress.py ZyDo860V --ref R.json          # 对拍
    python tools/gt_regress.py ZyDo860V --golden              # 冻结/比对自身口径
    python tools/gt_regress.py ZyDo860V --golden --write      # 覆盖冻结值
    python tools/gt_regress.py ZyDo860V --enemy-res boss      # 减抗按 Boss 档换算
    python tools/gt_regress.py --ids A,B,C --golden           # 批量
    python tools/gt_regress.py --sheet Sam                    # 游戏内面板真值对拍
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import gt_build as GB                     # noqa: E402
import plan_dps as PD                     # noqa: E402

REGRESS_DIR = ROOT / 'data' / 'regress'

# 中文槽位（沿用 gt_build 的命名，两边必须一致，否则 override 落空）
SLOT_ZH = GB.SLOT_ZH
SLOTS = list(GB.SLOT_ORDER)

# grimtools 面板能对上的指标 → 容差（相对 | 绝对，取**宽**的那个）
TOL = {
    '等级': (0.0, 0.5),
    '攻击速度': (0.01, 0.02),
    '每秒伤害': (0.02, 1.0),
    '体质': (0.02, 2.0), '狡诈': (0.02, 2.0), '精神': (0.02, 2.0),
}
TOL_RES = (0.02, 1.0)          # 10 项抗性
TOL_RR = (0.02, 1.0)           # 减抗
TOL_SKILL = (0.05, 2.0)        # 逐技能 DPS（对拍时更松，因为覆盖率模型是近似）
GOLDEN_TOL = 1e-4              # golden 是自身口径，只容浮点噪声


# ==================================================================== 归一层
def load_build(target=None, html=None):
    """短 ID / URL / 本地 html → `gt_build.norm()` 的归一层。"""
    if html:
        raw = open(html, encoding='utf-8', errors='replace').read()
        src = html
    elif target:
        raw, src = GB.fetch_html(target)
    else:
        raise SystemExit('要给一个 grimtools 短 ID / URL，或 --html')
    n = GB.norm(GB.extract_buildinfo(raw))
    n['src'] = src
    n['id'] = _id_of(target or html or '')
    return n


def _id_of(s):
    import re
    m = re.search(r'calc/([0-9A-Za-z_-]+)', s) or re.search(r'gt_([0-9A-Za-z_-]+)\.html', s)
    return m.group(1) if m else os.path.basename(s)


GT_SLOT_TO_ZH = {v: k for k, v in SLOT_ZH.items()}   # 中文 → 英文（norm 的 slots 是中文键）


def _zh_slot(key, s):
    """`gt_build.norm()` 的 slots 以**英文键**存放，中文名在 `zh` 字段里。
    ★ 一开始按字典键当槽位名用 → 14 槽全部落空（`load_char` 的 `SLOTS` 是中文），
      表现为「抗性只剩技能给的、DPS 少一大截」，见本工具首版实测。"""
    return (s or {}).get('zh') or SLOT_ZH.get(key) or key


def to_plan(n, keep_ascended=False):
    """归一层 → `{槽位: [gid…]}`（引擎 plan 口径）。

    槽内顺序 = `[主体, 镶嵌, 附魔, 前缀, 后缀, 飞升]`：
    `gd.dps.fold()` 靠**第一个非词缀部件**取 `attributeScalePercent`，
    主体必须排第一，否则整槽的数值缩放会跑偏。

    `keep_ascended=False`（默认）跳过 `aa####`：飞升词缀是 `LootRandomizer`，
    `gd.save.items` 桥里没有它的记录名（实测 `any_record_of('aa16063') is None`）。
    """
    plan, skipped = {}, []
    for key, s in (n['slots'] or {}).items():
        zh = _zh_slot(key, s)
        row = []
        for k in ('item', 'component', 'augment', 'prefix', 'suffix',
                  'ascendedAffix' if keep_ascended else None):
            if not k:
                continue
            v = s.get(k)
            if v:
                row.append(v)
        if row:
            plan[zh] = row
        if s.get('ascendedAffix'):
            skipped.append((zh, s['ascendedAffix']))
    return plan, skipped


def randomizer_slots(n):
    """受随机骰词缀影响的槽（这些槽的数值**必然**跟 grimtools 对不上）。"""
    out = []
    for key, s in (n['slots'] or {}).items():
        tags = [k for k in ('prefix', 'suffix', 'ascendedAffix') if s.get(k)]
        if tags:
            out.append((_zh_slot(key, s), '/'.join(tags)))
    return out


# ==================================================================== 技能解析
_MASTERY_BY_TAG = None
_DEV_BY_TAG = None


def _mastery_by_tag():
    global _MASTERY_BY_TAG
    if _MASTERY_BY_TAG is None:
        ms = json.load(open(ROOT / 'data' / 'mastery_skills.json', encoding='utf-8')) or {}
        _MASTERY_BY_TAG = {v.get('tag'): v.get('record') for v in ms.values() if v.get('record')}
    return _MASTERY_BY_TAG


def _dev_by_tag():
    """星座 tag → [(记录名, stats)]。

    ★ 必须把**星座技能（celestial power）**也算进来：`devotion_tree.json` 里
      「刺客的利刃」有 5 个星，而 `devotions.json` 按 `tagDevotion_A08` 只找得到 4 个
      （`tier1_08a..d`）；第 5 个是 proc，记录叫 `tier1_08e_skill.dbr`，tag 是
      `tagDevotionEffectA08`（另一套前缀）。不并进来的话那个星点会静默丢失 ——
      实测丢的正好是「暗杀者的标记」这类**带减抗的 proc**，代价不该由回归来承担。
    """
    global _DEV_BY_TAG
    if _DEV_BY_TAG is None:
        dv = json.load(open(ROOT / 'data' / 'devotions.json', encoding='utf-8')) or {}
        m = collections.defaultdict(list)
        for rec, v in dv.items():
            t = (v or {}).get('tag')
            if not t:
                continue
            m[t].append((rec, (v or {}).get('stats') or {}))
            if t.startswith('tagDevotionEffect'):       # proc → 并到所属星座的 tag 下
                alias = 'tagDevotion_' + t[len('tagDevotionEffect'):]
                m[alias].append((rec, (v or {}).get('stats') or {}))
        _DEV_BY_TAG = m
    return _DEV_BY_TAG


def _sig(d):
    """把字段字典压成「标量指纹」，用于星座星点的唯一性匹配。

    只保留**单值数值**字段：`calc.js` 里是标量、`devotions.json` 里是单元素列表。
    逐级数组（技能）会自然被排除 —— 星座星点的 `max_level` 恒为 1，正合适。
    """
    out = {}
    for k, v in (d or {}).items():
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out[k] = float(v)
        elif isinstance(v, list) and len(v) == 1 and isinstance(v[0], (int, float)):
            out[k] = float(v[0])
    return out


def _assign(cands):
    """同一星座内做**一对一**分配：贪心取最高命中数，平手按记录名字母序。

    `cands` = {sk: {记录名: 命中数}}；返回 {sk: 记录名}。
    这样 3 个「签名并列」的星点也能被拆开（实测 B23 星座上出现过），
    没有指纹的 proc 星点则由「剩下的那条记录」补位。
    """
    order = sorted(cands, key=lambda s: (-max(cands[s].values() or [0]), s))
    taken, out = set(), {}
    for sk in order:
        pool = sorted((r for r in cands[sk] if r not in taken),
                      key=lambda r: (-cands[sk][r], r))
        if pool:
            out[sk] = pool[0]
            taken.add(pool[0])
    # 剩下的（全被抢走 / 命中数全 0）按字母序补位
    rest = sorted(set().union(*[set(v) for v in cands.values()]) - taken)
    for sk in (s for s in order if s not in out):
        if rest:
            out[sk] = rest.pop(0)
    return out


_TREE_TAG = None


def _tree_tag_of(sk):
    """`sk####` → 所属星座的 tag（`devotion_tree.json` 的 `stars` 列表）。

    兜底用：有些星座技能在 `itemdb.js` 里是 `null`、在 `calc.js` 里也没有
    `skillDisplayName`（实测 sk713 = 刺客的利刃的 proc）—— 只能靠星座归属定位。
    """
    global _TREE_TAG
    if _TREE_TAG is None:
        tree = json.load(open(ROOT / 'data' / 'devotion_tree.json', encoding='utf-8')) or {}
        _TREE_TAG = {}
        for cid, c in tree.items():
            for s in (c.get('stars') or []):
                _TREE_TAG[s] = c.get('tag')
    return _TREE_TAG.get(sk)


def resolve_skills(n, log=None):
    """`buildInfo.data.skills` → `({记录名: 等级}, 统计)`。

    统计里带每个 sk 的解析方式（`职业/精通` / `签名唯一` / `签名并列` / `未解析`），
    未解析清单必须能看见 —— 静默丢技能是最危险的失败模式。
    """
    sks = [s for s in (n['skills'] or []) if s.get('name')]
    ids = [s['name'] for s in sks]
    tagmap = GB.dump_ids(ids)                 # itemdb.js：职业技能的 tag 在这里
    calc = GB.sk_fields(ids)                  # calc.js：星座星点的 displayName + 字段指纹
    mbt, dbt = _mastery_by_tag(), _dev_by_tag()

    # ① 先把能按 tag 直接定的（职业/精通）分掉
    out, how, miss = {}, collections.Counter(), []
    pending = collections.defaultdict(dict)   # tag → {sk: {记录名: 命中数}}
    sk_level = {}
    for s in sks:
        sk, lv = s['name'], int(s.get('level') or 0)
        if lv <= 0:
            continue
        sk_level[sk] = lv
        c = calc.get(sk) or {}
        tag = (tagmap.get(sk) or {}).get('name') or c.get('skillDisplayName')
        if not tag:
            tag = _tree_tag_of(sk)
            if tag:
                how['星座归属兜底'] = how['星座归属兜底'] + 1
        if tag and tag in mbt:
            out[mbt[tag]] = lv
            how['职业/精通'] += 1
            continue
        cands = dbt.get(tag) or []
        if not cands:
            how['未解析'] += 1
            miss.append((sk, lv, tag, '既非职业技能，也没有同 tag 的星座星点'))
            continue
        cs = _sig(c)
        if cs:
            sc = {}
            for rec, stats in cands:
                st = _sig(stats)
                sc[rec] = sum(1 for k, v in cs.items() if k in st and abs(st[k] - v) < 1e-6)
        else:
            # 没有指纹（典型 = 星座 proc，字段全是 `buffSkillName`/`templateName`）：
            # 全 0 分进池，让同星座的**一对一分配**把它落到剩下的那条记录上。
            how['无指纹→补位'] += 1
            sc = {rec: 0 for rec, _ in cands}
        pending[tag][sk] = sc

    # ② 同星座内一对一分配
    for tag, cands in pending.items():
        got = _assign(cands)
        for sk, rec in got.items():
            top = max(cands[sk].values() or [0])
            if top <= 0:
                how['补位(无签名)'] += 1
            elif sum(1 for r in cands[sk] if cands[sk][r] == top) > 1:
                how['签名并列→拆分'] += 1
            else:
                how['签名唯一'] += 1
            out[rec] = sk_level[sk]

    if log:
        for k, v in sorted(how.items(), key=lambda x: -x[1]):
            log('  %-14s %d' % (k, v))
    return out, {'how': dict(how), 'missing': miss, 'n': len(sk_level)}


def to_bio(n):
    b = n.get('bio') or {}
    return {k: b[k] for k in ('physique', 'cunning', 'spirit') if b.get(k) is not None}


def to_classes(n):
    return [GB.CLASS_ZH.get(v, 'class' + str(v)) for v in (n.get('masteries') or {}).values()]


# ==================================================================== 评估
def pick_arch(skills, classes=None, class_codes=None):
    """没形态技能时的打法兜底：挑 `core_skills` 与构建加点**重合最多**的那个。

    ★ 为什么需要：`gd.dps.guess_arch` 只认 `werewolf1`/`wereraven1` 两个形态技能，
      而社区构建大量是**不变身**的（本例 狂战士+夜刃 人形态）。硬套 `werewolf`
      会让 `devotion_pct` 按狼人的星座去算 —— 数值对不上，而且错得查不出来。
    ★ 两道**硬门槛**（否则单纯按重合度会选错）：
      ① `root_skills` 非空时，至少要有一个在构建里
         （`wolf_nightblade` 重合度 8 最高，但要求 `werewolf1.dbr`，本构建没点）；
      ② 声明了 `mastery`/`masteries` 时，至少一个职业要在构建的专精里
         （挡掉 `soldier_nightblade`）。
    """
    import json as _json
    try:
        arch = _json.load(open(ROOT / 'data' / 'archetypes.json', encoding='utf-8')) or {}
    except Exception:
        return ''
    have = {r for r, lv in (skills or {}).items() if lv}
    codes = {c if str(c).startswith('class') else 'class%s' % c for c in (class_codes or [])}
    best, bs = '', 0
    for name, a in sorted(arch.items()):
        core = set(a.get('core_skills') or [])
        if not core:
            continue
        root = set(a.get('root_skills') or [])
        if root and not (root & have):
            continue
        ms = a.get('masteries') or ([a['mastery']] if a.get('mastery') else [])
        need = set()
        for m in ms:
            need.add(m if isinstance(m, str) else 'class%02d' % m[0] if isinstance(m, (list, tuple)) else 'class%s' % m)
        if need and codes and not (need & codes):
            continue
        sc = len(core & have)
        if sc > bs:
            best, bs = name, sc
    return best


def evaluate(n, arch='', enemy_res=None, res_penalty=0.0, log=None):
    """归一层 → 指标字典（**不读存档**）。"""
    import os as _os
    from gd import dps as D
    from gd import rotation as R
    from gd import rr as RR

    plan, skipped = to_plan(n)
    ov = PD.plan_to_override(plan)
    skills, sinfo = resolve_skills(n, log=log)
    bio = to_bio(n)
    level = (n.get('bio') or {}).get('level') or 100
    classes = to_classes(n)

    c = D.load_char('', '', False, gear_override=ov, skill_override=skills,
                    bio_override=bio, level_override=level, classes_override=classes)
    sk = c['skills']
    skills_zh = D.load_skills_zh()
    arch_how = ''
    if not arch:
        arch = D.guess_arch(sk) or ''
        if arch:
            arch_how = '形态技能'
        else:
            arch = pick_arch(sk, classes, class_codes=list((n.get('masteries') or {}).values()))
            arch_how = ('core_skills 重合度' if arch else '无匹配→默认 werewolf')
            arch = arch or 'werewolf'
    if log and arch_how:
        log('  打法 %s（来源：%s）' % (arch, arch_how))

    # 装备 `+技能`：GT 的 level 是**已分配**点数，故这里照常叠加（见模块 docstring）
    eff = dict(sk)
    for rec, extra in (c.get('skill_plus') or {}).items():
        if rec in eff:
            eff[rec] += extra
    mast = {}
    for rec, lv in sk.items():
        if '_classtraining_' in rec:
            mast[_os.path.basename(rec).replace('_classtraining_', '').replace('.dbr', '')] = lv
    for cls, extra in (c.get('mastery_plus') or {}).items():
        mast[cls] = mast.get(cls, 0) + extra
    attrs = R.panel_attrs(c.get('bio') or {}, mast,
                          c.get('gear_flat') or {}, c.get('gear_pct') or {})
    apct = R.attr_damage_pct(attrs)

    rr_pack, _, _ = RR.collect_char(c['folded'], eff, c['db'])
    from gd import procs as _PR
    rep = R.final_report(arch, eff, db=c['db'], folded=c['folded'], skill_records=list(eff),
                         base_aps=c.get('base_aps'), attr_pct=apct,
                         conversions=c.get('conversions') or [],
                         skill_mods=c.get('skill_mods') or {}, rr=rr_pack,
                         item_wps=_PR.wps_pool(c.get('base_gids')),
                         weapon_st=_PR.weapon_state_of(c))

    m = {}
    m['等级'] = int(c['level'])
    m['攻击速度'] = round(rep.get('aps') or 0.0, 3)
    m['每秒伤害'] = round(rep.get('dps') or 0.0, 1)
    for k in ('physique', 'cunning', 'spirit'):
        zh = {'physique': '体质', 'cunning': '狡诈', 'spirit': '精神'}[k]
        m[zh] = round(float(attrs.get(k) or 0.0), 1)

    # 抗性（面板口径；`--res-penalty 50` 对齐终极难度）
    res = RR.char_res(c['folded'], eff, c['db'],
                      base={b: -float(res_penalty) for b in RR.RES_BUCKETS})
    for b, v in res.items():
        m['抗性_' + RR.BUCKET_ZH[b]] = round(v, 1)

    # 减抗（A 族叠加 + B-abs 叠加；B-% 单列，因为它是乘算的「最强」）
    add, mx = rr_pack.get('add') or {}, rr_pack.get('max') or {}
    for b in RR.RES_BUCKETS:
        if add.get(b):
            m['减抗_' + RR.BUCKET_ZH[b]] = round(add[b], 1)
        if mx.get(b):
            m['减抗%_' + RR.BUCKET_ZH[b]] = round(mx[b], 1)

    # 各伤害类型的 % 加成（诊断用：对拍「XX% 增加」那类条目）
    for t, v in (rep.get('pct') or {}).items():
        if v:
            m['加成_' + (R.TYPE_ZH.get(t) or t)] = round(float(v), 1)

    # 逐技能 DPS
    for rec, h in D.attack_rows(rep):
        nm = D.zh_name(rec, skills_zh)
        m['技能_' + nm] = round(float(h.get('dps') or 0.0), 1)
    m['_空转技能数'] = len([1 for r, lv in eff.items() if lv and r not in rep.get('skills', {})])

    # 实战（敌方减抗乘区）
    prov = None
    if enemy_res:
        RR.apply_vs(rep, rr_pack, profile=enemy_res)
        prov = rep.get('vs') or {}
        m['_敌方档位'] = enemy_res
        m['每秒伤害_实战'] = round(float(prov.get('dps_vs') or 0.0), 1)
        m['_实战倍率'] = round(float(prov.get('mult_overall') or 1.0), 4)

    return {'id': n.get('id'), 'arch': arch, 'arch_how': arch_how, 'classes': classes,
            'level': m['等级'], 'metrics': m, 'plan': plan,
            'skipped_ascended': skipped, 'randomizer': randomizer_slots(n),
            'skills_resolved': sinfo, 'rr': rr_pack, 'attrs': attrs,
            'skill_plus_n': len(c.get('skill_plus') or {}),
            'item_skills_n': len(c.get('item_skills') or []),
            'conversions': c.get('conversions') or [],
            'vs': prov}


# ==================================================================== 对拍
def _tol(key):
    if key in TOL:
        return TOL[key]
    if key.startswith('抗性_'):
        return TOL_RES
    if key.startswith('减抗'):
        return TOL_RR
    if key.startswith('技能_'):
        return TOL_SKILL
    if key.startswith('加成_'):
        return (0.05, 2.0)
    return (0.05, 2.0)


def diff(computed, ref, tol=None):
    """`{指标: 值}` 之间的差异表。ref 里没有的键**跳过**（允许部分录入）。"""
    rows = []
    for k in sorted(set(computed) | set(ref)):
        if k.startswith('_'):
            continue
        cv, rv = computed.get(k), ref.get(k)
        if rv is None:
            rows.append((k, cv, None, False))
            continue
        if cv is None:
            rows.append((k, None, rv, False))
            continue
        try:
            cv, rv = float(cv), float(rv)
        except (TypeError, ValueError):
            rows.append((k, cv, rv, str(cv) == str(rv)))
            continue
        rt, at = tol if tol else _tol(k)
        lim = max(abs(rv) * rt, at)
        rows.append((k, cv, rv, abs(cv - rv) <= lim))
    return rows


def fmt_diff(rows):
    L = ['| 指标 | 本引擎 | 参考 | 差 | ', '|---|---|---|---|']
    for k, cv, rv, ok in rows:
        if cv is None or rv is None:
            L.append('| %s | %s | %s | — |' % (k, cv, rv))
            continue
        try:
            d = float(cv) - float(rv)
        except (TypeError, ValueError):
            L.append('| %s | %s | %s | %s |' % (k, cv, rv, '✓' if ok else '✗'))
            continue
        L.append('| %s | %s | %s | %+.1f %s |'
                 % (k, _n(cv), _n(rv), d, '' if ok else '**✗**'))
    return L


def _n(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return '%d' % round(f) if abs(f) >= 100 or f == int(f) else '%.2f' % f


# ==================================================================== 输出
def report(r, ref=None, golden=None, log=print):
    n = r
    log('')
    log('═' * 68)
    log('grimtools 构建 %s ｜ lv%d ｜ %s ｜ 打法 %s'
        % (n['id'], n['level'], '+'.join(n['classes']) or '?', n['arch']))
    log('═' * 68)

    si = n['skills_resolved']
    log('  装备槽 %d ｜ 技能 %d（解析 %d）｜ 装备 +技能 %d 条 ｜ 转化的物品 %d 件'
        % (len(n['plan']), si['n'], si['n'] - len(si['missing']),
           n.get('skill_plus_n') or 0, len(set(n.get('conversions') or []))))
    if n['randomizer']:
        log('  ⚠ 随机骰词缀 %d 槽（数值按 dbr 模板算，与 grimtools 必有偏差）：%s'
            % (len(n['randomizer']), '、'.join('%s(%s)' % t for t in n['randomizer'])))
    if n['skipped_ascended']:
        log('  ⚠ 跳过飞升词缀 %d 个（LootRandomizer，无记录名）：%s'
            % (len(n['skipped_ascended']), '、'.join('%s/%s' % t for t in n['skipped_ascended'])))
    if si['missing']:
        log('  ✗ 未解析技能 %d 个：' % len(si['missing']))
        for sk, lv, tag, why in si['missing'][:8]:
            log('      %-8s lv%-3d %-30s %s' % (sk, lv, tag or '-', why))

    m = n['metrics']
    core = ['等级', '攻击速度', '每秒伤害', '体质', '狡诈', '精神']
    log('')
    log('── 核心指标 ' + '─' * 55)
    log('   ' + '  ｜  '.join('%s %s' % (k, _n(m[k])) for k in core if k in m))
    res = [(k, v) for k, v in m.items() if k.startswith('抗性_')]
    log('   抗性：' + ' / '.join('%s %s' % (k[3:], _n(v)) for k, v in res))
    rrv = [(k, v) for k, v in m.items() if k.startswith('减抗')]
    log('   减抗：' + (' / '.join('%s %s' % (k.replace('减抗_', '').replace('减抗%_', '% '), _n(v))
                                 for k, v in rrv) or '（无）'))
    sk = [(k, v) for k, v in m.items() if k.startswith('技能_')]
    sk.sort(key=lambda x: -x[1])
    log('   技能 DPS（前 6）：' + ' / '.join('%s %s' % (k[3:], _n(v)) for k, v in sk[:6]))
    if n.get('vs'):
        log('   实战（%s 档）：%s ｜ 倍率 ×%.3f'
            % (m.get('_敌方档位'), _n(m.get('每秒伤害_实战') or 0), m.get('_实战倍率') or 1.0))

    if ref is not None:
        log('')
        log('── 对拍 grimtools 面板 ' + '─' * 43)
        for line in fmt_diff(diff(m, ref)):
            log('   ' + line)
    if golden is not None:
        log('')
        log('── 对拍 golden（自身口径冻结值）' + '─' * 32)
        rows = diff(m, golden, tol=(GOLDEN_TOL, 1e-4))
        bad = [x for x in rows if not x[3]]
        log('   指标 %d 项 ｜ 漂移 %d 项 %s'
            % (len(rows), len(bad), '✓' if not bad else '✗'))
        for k, cv, rv, ok in bad[:15]:
            log('      %-22s 本引擎 %s ｜ golden %s' % (k, _n(cv), _n(rv)))
    log('')
    return r


def _plus_count(n):
    return len(n.get('_skill_plus_n') or [])


# ==================================================================== golden
def _gpath(gid):
    return REGRESS_DIR / ('%s.golden.json' % gid)


def golden_mode(r, write=False, log=print):
    REGRESS_DIR.mkdir(parents=True, exist_ok=True)
    p = _gpath(r['id'])
    m = {k: v for k, v in r['metrics'].items() if not k.startswith('_')}
    if write or not p.exists():
        json.dump({'id': r['id'], 'arch': r['arch'], 'level': r['level'],
                   'metrics': m}, open(p, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1, sort_keys=True)
        log('  golden %s → %s' % ('已写入' if write else '首次建立', p))
        return True
    old = json.load(open(p, encoding='utf-8'))
    rows = diff(m, old.get('metrics') or {}, tol=(GOLDEN_TOL, 1e-4))
    bad = [x for x in rows if not x[3]]
    log('  golden 对拍 %d 项 ｜ 漂移 %d 项 %s' % (len(rows), len(bad), '✓' if not bad else '✗'))
    for k, cv, rv, ok in bad[:15]:
        log('      %-22s %s → %s' % (k, _n(rv), _n(cv)))
    return not bad


# ==================================================================== 游戏内实测（--sheet）
# 前两条通道都对不上游戏本体：`--ref` 是**社区构建**（grimtools 面板），
# `--golden` 是**我们自己**。只有游戏内面板是真值，所以单独开一条通道。
#
# 键名刻意用**中文面板名**（角色一/二/三页的叫法），因为誊录的人看着面板抄；
# 用引擎内部字段名会逼着他做一次翻译，那是最容易抄错的地方。
SHEET_TOL = {
    '等级': (0.0, 0.5),
    'OA': (0.02, 5.0), 'DA': (0.02, 5.0),
    '攻击速度': (0.02, 0.05),
    '暴击伤害加成': (0.02, 2.0),
    '每秒伤害（面板）': (0.05, 50.0),
}
SHEET_TOL_RES = (0.02, 1.0)          # 10 项抗性（百分比点）
SHEET_TOL_PCT = (0.02, 2.0)          # 角色三页的各伤害类型 +%


def sheet_path(char):
    return REGRESS_DIR / ('%s.sheet.json' % char)


def build_sheet(char):
    """算我方一侧的「面板值」——键名与游戏面板一致。"""
    import gd.dps as D
    import gd.rotation as R
    import gd.rr as RR
    import gd.enemy as EN

    c = D.load_char(char)
    sk = {k: v for k, v in c['skills'].items() if v > 0}
    eff = dict(sk)
    for rec, extra in (c.get('skill_plus') or {}).items():
        if rec in eff:
            eff[rec] += extra
    mast = {}
    for rec, lv in sk.items():
        if '_classtraining_' in rec:
            mast[os.path.basename(rec).replace('_classtraining_', '')
                 .replace('.dbr', '')] = lv
    for cls, extra in (c.get('mastery_plus') or {}).items():
        mast[cls] = mast.get(cls, 0) + extra
    attrs = R.panel_attrs(c.get('bio') or {}, mast,
                          c.get('gear_flat') or {}, c.get('gear_pct') or {})
    from gd import procs as _PR2
    rep = R.final_report(D.guess_arch(sk) or 'werewolf', eff, db=c['db'],
                         folded=c['folded'], skill_records=list(eff),
                         base_aps=c.get('base_aps') or 1.25,
                         attr_pct=R.attr_damage_pct(attrs),
                         conversions=c.get('conversions') or [],
                         skill_mods=c.get('skill_mods') or {},
                         attrs=attrs, level=c.get('level'),
                         enemy=EN.get_profile(None, c.get('level') or 100),
                         item_wps=_PR2.wps_pool(c.get('base_gids')),
                         weapon_st=_PR2.weapon_state_of(c))
    out = {
        '等级': c['level'],
        'OA': rep.get('oa'), 'DA': rep.get('da'),
        '攻击速度': round(rep['aps'], 3),
        '暴击伤害加成': rep.get('crit_dmg_pct'),
        'PTH': rep.get('pth'),
        '每秒伤害（面板）': rep.get('dps'),
        '每秒伤害（实战）': rep.get('dps_real'),
        '抗性': {}, '伤害加成': {},
    }
    for b, v in (RR.char_res(c['folded'], eff, c['db']) or {}).items():
        out['抗性'][RR.BUCKET_ZH.get(b, b)] = round(v, 1)
    # ★ 键名用**游戏面板的叫法**（中文），DoT 类型带「（持续伤害）」后缀 —— Panel 三页
    #   就是这么列的。誊录的人照着面板抄，不该先做一次「类型键翻译」。
    #   DoT 集合取 `gd/dmg.py::DOT_TYPES` 单一真源（手写清单曾把 `poison`（直接伤害）
    #   也标成持续伤害 —— 毒素/酸液是直伤，只有 `poisondot` 才是持续）。
    from gd import dmg as DMG
    for t, v in (rep.get('pct') or {}).items():
        zh = R.TYPE_ZH.get(t, t)
        if t in DMG.DOT_TYPES:
            zh = '%s（持续伤害）' % zh
        out['伤害加成'][zh] = round(v, 1)
    out['_meta'] = {'武器基础': rep.get('weapon'), '武器攻击范围':
                    (rep.get('weapon_attack') or {}).get('min'),
                    '_武器攻击范围上限': (rep.get('weapon_attack') or {}).get('max')}
    return out


def sheet_mode(char, write=False, log=print):
    """对拍游戏内面板真值。返回 `True` 表示**没有失败项**（含「还没填」）。"""
    REGRESS_DIR.mkdir(parents=True, exist_ok=True)
    p = sheet_path(char)
    ours = build_sheet(char)
    if write or not p.exists():
        tpl = {'char': char,
               'source': '游戏内 角色一/二/三 页手工誊录',
               'note': ('把 in_game 里的 null 照游戏面板改成真实数值；'
                        '留 null 的键会被**明确报为「未填写」**（不静默跳过）。'
                        'engine_values 是本引擎当前算出的值，供对照，对拍时不读。'),
               'in_game': {'等级': None, 'OA': None, 'DA': None, '攻击速度': None,
                           '暴击伤害加成': None, '每秒伤害（面板）': None,
                           '抗性': {k: None for k in sorted(ours.get('抗性') or {})},
                           '伤害加成': {k: None for k in sorted(ours.get('伤害加成') or {})}},
               'engine_values': ours}
        json.dump(tpl, open(p, 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=1, sort_keys=True)
        log('  实测模板 → %s（照游戏面板填 in_game 段）' % p)
        return True

    doc = json.load(open(p, encoding='utf-8'))
    ing = doc.get('in_game') or {}
    log('── 游戏内实测对拍（%s）' % char)

    rows = []          # (键, 引擎值, 游戏值 or None, 容差)
    for k in ('等级', 'OA', 'DA', '攻击速度', '暴击伤害加成', '每秒伤害（面板）'):
        if k in ing:
            rows.append((k, ours.get(k), ing.get(k), SHEET_TOL.get(k, (0.02, 1.0))))
    for sect, tol in (('抗性', SHEET_TOL_RES), ('伤害加成', SHEET_TOL_PCT)):
        for k, v in sorted((ing.get(sect) or {}).items()):
            rows.append((k, (ours.get(sect) or {}).get(k), v, tol))

    filled = bad = 0
    for k, cv, rv, tol in rows:
        if rv is None:
            log('      · %-16s 未填写（引擎值 %s）' % (k, _n(cv) if cv is not None else '—'))
            continue
        filled += 1
        if cv is None:
            log('      ✗ %-16s 引擎算不出 ｜ 游戏 %s' % (k, _n(rv)))
            bad += 1
            continue
        ok = abs(float(cv) - float(rv)) <= max(tol[1], abs(float(rv)) * tol[0])
        log('      %s %-16s 引擎 %s ｜ 游戏 %s'
            % ('✓' if ok else '✗', k, _n(cv), _n(rv)))
        if not ok:
            bad += 1
    log('  键位 %d ｜ 已填 %d ｜ 超差 %d %s'
        % (len(rows), filled, bad, '✓' if not bad else '✗'))
    if not filled:
        log('  ⚠ 一个键都没填 —— 这套对拍**还没有产生任何证据**'
            '（如实报告；不因为「没填」就当作通过）')
    return not bad


# ==================================================================== main
def main(argv=None):
    ap = argparse.ArgumentParser(description='grimtools 社区构建回归对拍')
    ap.add_argument('target', nargs='?', help='grimtools 短 ID / URL')
    ap.add_argument('--ids', default='', help='逗号分隔的多个短 ID（批量）')
    ap.add_argument('--html', help='本地已保存的页面')
    ap.add_argument('--arch', default='', help='强制打法（默认自动识别）')
    ap.add_argument('--enemy-res', default=None,
                    choices=['none', 'elite', 'boss', 'high', 'max'],
                    help='实战伤害的敌方抗性档位')
    ap.add_argument('--res-penalty', type=float, default=0.0,
                    help='抗性难度惩罚（终极难度 = 50）')
    ap.add_argument('--ref', help='参考值 JSON（grimtools 面板，人工录入）')
    ap.add_argument('--emit-ref', help='按参考模板写出一个 JSON（含本引擎的值）')
    ap.add_argument('--golden', action='store_true', help='与冻结值对拍')
    ap.add_argument('--write', action='store_true', help='--golden 时覆盖冻结值')
    ap.add_argument('--json', action='store_true', help='只输出一行 JSON')
    ap.add_argument('--sheet', default='', metavar='角色名',
                    help='与**游戏内面板真值**对拍（读 data/regress/<角色>.sheet.json）')
    ap.add_argument('--emit-sheet', action='store_true',
                    help='--sheet 时写模板（engine_values 已填好，in_game 段照面板抄）')
    a = ap.parse_args(argv)

    # `--sheet` 是**独立通道**（对的是游戏本体，不是 grimtools 构建），所以先处理。
    if a.sheet:
        return 0 if sheet_mode(a.sheet, write=a.emit_sheet) else 1

    ids = [x for x in (a.ids.split(',') if a.ids else []) if x]
    if a.target:
        ids.insert(0, a.target)
    if not ids and not a.html:
        ap.error('给一个短 ID / URL，或 --ids A,B,C，或 --html')

    targets = ids or [None]
    ref = json.load(open(a.ref, encoding='utf-8')) if a.ref else None
    refm = ((ref or {}).get('measures') or ref or {}) if ref else None

    ok_all, payload = True, []
    for i, t in enumerate(targets):
        n = load_build(t, a.html if (a.html and len(targets) == 1) else None)
        r = evaluate(n, arch=a.arch, enemy_res=a.enemy_res,
                     res_penalty=a.res_penalty,
                     log=(None if a.json else (lambda s: print(s))))
        if a.json:
            payload.append({'id': r['id'], 'level': r['level'], 'arch': r['arch'],
                            'classes': r['classes'], 'metrics': r['metrics']})
            continue
        ref_one = refm
        if isinstance(refm, dict) and r['id'] in refm:
            ref_one = refm[r['id']]
        report(r, ref=ref_one)
        if a.emit_ref:
            # ★ `measures` 一律**留 null**：把引擎值填在那儿会被误当成参考值，
            #   对拍就变成「自己跟自己比」—— 引擎值另放 `engine_values` 供对照。
            vals = {k: v for k, v in r['metrics'].items() if not k.startswith('_')}
            out = {'id': r['id'], 'source': 'grimtools',
                   'note': ('照 grimtools 面板把 measures 里的 null 改成真实数值；'
                            'engine_values 是本引擎当前算出的值，供对照，对拍时不读。'
                            '留着 null 的键会被跳过（允许部分录入）。'),
                   'measures': {k: None for k in sorted(vals)},
                   'engine_values': dict(sorted(vals.items()))}
            p = Path(a.emit_ref)
            if len(targets) > 1:
                p = p.with_name('%s.%s' % (p.stem, r['id']))
            p.parent.mkdir(parents=True, exist_ok=True)
            json.dump(out, open(p, 'w', encoding='utf-8'),
                      ensure_ascii=False, indent=1, sort_keys=True)
            print('  对拍模板 → %s（%d 项待录入）' % (p, len(vals)))
        if a.golden:
            ok_all &= bool(golden_mode(r, write=a.write))

    if a.json:
        print(json.dumps(payload if len(payload) > 1 else payload[0], ensure_ascii=False))
        return 0
    return 0 if ok_all else 1


if __name__ == '__main__':
    raise SystemExit(main())
