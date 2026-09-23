#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plan_dps.py —— 用**真实 DPS 模型**评估一份装备方案（gd.opt 的 --out JSON）。

为什么要它：`gd.opt` 的「伤害代理」是**线性加权和**，只用于排序；
真实伤害还取决于攻速、技能倍率、伤害转化、属性加成、DoT 覆盖率……
本工具把方案里的 gid 还原成记录名，喂给 `gd.dps.load_char(gear_override=…)`
走**同一套 final_report 模型**，得到可与存档实测对齐的每秒伤害。

用法：
    python tools/plan_dps.py Sam data/plans/xxx.json [--arch wolf_nightblade] [--json]

    --json   只输出一行 JSON（便于脚本批量对比）
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gd import DB                                   # noqa: E402
from gd import defense as DF                        # noqa: E402  ★ 防御轴（仅 GD_DEF_WEIGHT 时用）
from gd.save import items as SI                     # noqa: E402


_TWOH: dict = {}


def _is_two_hand(rec: str) -> bool:
    """该武器记录是不是**双手**（游戏里双手武器 + 副手 不可能同时生效）。"""
    if rec in _TWOH:
        return _TWOH[rec]
    v = False
    try:
        from gd import DB
        g = SI.bridge().gid_of(rec)
        o = DB.load().items.get(g) or {}
        v = "2h" in (o.get("n") or "").lower()
    except Exception:
        v = False
    _TWOH[rec] = v
    return v


def plan_to_override(plan: dict) -> dict:
    """{槽位:[base,comp,aug,pre,suf]} → {槽位:[记录名…]}（喂给 gear_override）。

    `plan` 为 `None` / 空 ⇒ 返回 `{}`（= 不覆盖，用存档自己的装备）。
    """
    br = SI.bridge()
    out = {}
    for slot, tup in (plan or {}).items():
        recs = []
        for gid in (tup or []):
            if not gid:
                continue
            rec = br.any_record_of(gid)
            if rec:
                recs.append(rec)
        # ★ 主体为空（如双手武器下「副手」只剩镶嵌的空壳）→ **整槽跳过**，
        #   否则 override 会把那件镶嵌当成独立的副手件重复计入。
        if recs:
            out[slot] = recs
    # ★ 游戏规则：**双手武器时副手不生效**。评估里就把副手丢掉 ——
    #   这样「双手 + 副手」这种非法组合**拿不到任何收益**，优化器自然不会选它，
    #   省掉「搜出非法解再回退」的浪费。
    m = out.get("主手")
    if m and _is_two_hand(m[0]):
        out.pop("副手", None)
    return out


def bump_field(folded: dict, field: str, delta: float) -> dict:
    """`folded` 的**浅拷贝**，把任意一个槽位的 `field` 加上 `delta`。

    用途是**模型边际自测**：把「+10 点 `offensiveTotalDamageModifier`」塞进装备字段，
    看真实 DPS 动了多少 —— 这是唯一能验证 `gd/opt.py` 权重表的方法
    （权重是「每点值多少分」的代理，只有对着真实模型测才知道标得对不对）。

    槽位按**名字排序**取第一个，保证多次调用可复现（不依赖 dict 顺序）。
    """
    out = {k: dict(v) for k, v in (folded or {}).items()}
    if not out:
        return out
    k0 = sorted(out)[0]
    f = out[k0]
    cur = f.get(field)
    if isinstance(cur, bool):
        f[field] = float(delta)
    elif isinstance(cur, (int, float)):
        f[field] = float(cur) + delta
    elif isinstance(cur, list) and cur and isinstance(cur[0], (int, float)):
        f[field] = [float(cur[0]) + delta] + list(cur[1:])
    else:
        f[field] = float(delta)
    return out


_ARCH_MEMO = {}


def arch_by_mastery(char):
    """按**职业组合**匹配形态键（`data/archetypes.json` 的 `masteries`）—— 带记忆化。

    ★★ 为什么要有它（2026-09-22 踩到）：`dps_of` 原来的兜底链是
      `arch or D.guess_arch(sk) or 'werewolf'`，而 `guess_arch` **只认变身技能**
      （`werewolf1` / `wereraven1`）。非变身角色（如 `_xyf` 士兵+夜刃）拿到 None
      ⇒ **一律被当成「狼人形态」**去算。`gd auto` 走的是 `autobuild.pick_archetype`
      （职业组合匹配）⇒ 判成 `soldier_nightblade`，两边对不上：
      同一次 `gd auto` 报 **200,754**，而 `dps_of` 报 **12,736（差 15.8 倍）**。
    ⇒ 与 `autobuild.pick_archetype` 同源补齐这一档；**变身角色行为不变**
      （`guess_arch` 先返回），所以对 Sam 是零漂移。
    """
    if char in _ARCH_MEMO:
        return _ARCH_MEMO[char]
    got = ''
    try:
        import autobuild as _AB            # 延迟导入：autobuild 会 import 本模块
        from gd import paths as _P
        _lv, _cls, _sk = _AB.read_meta(char)
        got = _AB.pick_archetype(_cls, _sk, _P.load_json('archetypes.json') or {}) or ''
    except Exception:                                          # noqa: BLE001
        got = ''
    _ARCH_MEMO[char] = got
    return got


def resolve_arch(char, arch=''):
    """完整形态判定链（**对外统一入口**）：

        显式 `arch`  >  **变身技能**（`gd.dps.guess_arch`，狼人/鸦人）
                     >  **职业组合**（`arch_by_mastery`）
                     >  `'werewolf'`（历史兜底）

    ★ 顺序不能反：`guess_arch` 在前 ⇒ **变身角色的行为与改动前逐位一致**
      （Sam 仍得 `werewolf`），非变身角色才落到职业组合那一档。
    """
    if arch:
        return arch
    try:
        from gd import dps as _D
        got = _D.guess_arch((_D.load_char(char).get('skills') or {})) if char else None
        if got:
            return got
    except Exception:                                          # noqa: BLE001
        pass
    return arch_by_mastery(char) or 'werewolf'


def dps_of(char: str, plan: dict = None, arch: str = "",
           inject=None, crit_dmg_pct=None, bio_override=None) -> dict:
    """评估一份方案（`plan=None` ⇒ 用存档自己的装备）。

    `inject`       = `(字段名, 增量)` —— 给装备注入一个**合成字段**，用于边际自测。
    `crit_dmg_pct` = 覆盖暴击伤害加成（%），用于暴击维度的边际自测。
    `bio_override` = `{'physique'|'cunning'|'spirit': 存档 block2 原值}` —— 替换**加点三围**，
                     用来回答「装备定下来之后，属性点还能不能榨出伤害」
                     （与 `gd/reqfit.solve()` 的 `Fit.targets` 同口径，可直接对接）。
                     默认 `None` ⇒ 用存档原值，**逐位零漂移**。
    """
    import io
    from gd import dps as D
    from gd import rotation as R

    ov = plan_to_override(plan)
    # ★ `{}` 必须传 `None`：`load_char` 判的是 `is not None` ⇒ 空 dict 会被当成
    #   「把 12 个装备槽全替换成没有」—— 装备加成整块蒸发（实测 pct 基准
    #   838 → 591、crit 边际 15.7 → 3.1）。空方案 = 用存档自己的装备。
    c = D.load_char(char, '', True, gear_override=(ov or None),
                    bio_override=(bio_override or None))
    if inject:
        try:
            c['folded'] = bump_field(c.get('folded') or {}, inject[0], float(inject[1]))
        except Exception:
            pass
    sk = c['skills']
    skills_zh = D.load_skills_zh()
    arch = arch or D.guess_arch(sk) or arch_by_mastery(char) or 'werewolf'

    eff = dict(sk)
    for rec, extra in (c.get('skill_plus') or {}).items():
        if rec in eff:
            eff[rec] += extra
    mast = {}
    for rec, lv in sk.items():
        if '_classtraining_' in rec:
            mast[os.path.basename(rec).replace('_classtraining_', '').replace('.dbr', '')] = lv
    for cls, extra in (c.get('mastery_plus') or {}).items():
        mast[cls] = mast.get(cls, 0) + extra
    attrs = R.panel_attrs(c.get('bio') or {}, mast,
                          c.get('gear_flat') or {}, c.get('gear_pct') or {})
    apct = R.attr_damage_pct(attrs)
    base_aps = c.get('base_aps') or 1.25

    # ★ 敌方减抗乘区（`gd/rr.py`）—— 面板口径不动，另给 `dps_vs`（实战）
    from gd import rr as RR
    from gd import enemy as ENM
    rr_pack, _, _ = RR.collect_char(c['folded'], eff, c['db'])
    # ★ 敌方档（真值怪 / 五档 / 等级池）—— 命中与暴击乘区需要敌方 DA
    eprof = ENM.get_profile(os.environ.get('GD_ENEMY_PROFILE') or None,
                            c.get('level') or 100)
    # ★★ 敌方**护甲**（2026-09-20）：真值怪（`m<id>` / 假人）带 `armor`
    #   （读自被动技能 `defensiveProtection`，与游戏面板逐位一致）⇒ 喂进模型，
    #   让「抗性 + 穿甲」一起进伤害循环。等级池/五档没有单一护甲值 ⇒ None
    #   （报告里如实标注「物理直伤为下界」，绝不编数）。
    #   ⚠ 旧实现**完全没接**这条 —— 方案评估里护甲恒不参与，用户口径要求的
    #     「穿甲加入计算」在方案链路上是空的。
    from gd.dps import resolve_enemy_armor as _rea
    e_arm, e_src = _rea(os.environ.get('GD_ENEMY_ARMOR') or None, eprof)
    # ★★ 技能来源合法性（A 方案 · 2026-09-20）：把「当前装备授予的技能组」交给模型，
    #   物品技能（`records/skills/itemskills*`）只有真被装备授予时才计入。
    from gd import skillprov as SP
    eq_sk = SP.norm_equipped([x[1] for x in (c.get('item_skills') or [])])
    # ★★ 装备授予的武器池技能（WPS）入池（2026-09-20，用户批准）。
    #   见 `gd/procs.py::wps_of_item`：记录路径要经 `skillprov` 翻译，
    #   权重只在离线库 `itemSkills` 表 ⇒ 必须 `wps_pool` 一起取回后注入。
    from gd import procs as PR
    item_wps = PR.wps_pool(c.get('base_gids'))
    # ★★ 2026-09-21：**武器构成** —— 交给 `final_report` 做武器类型门控。
    #   技能记录上的 `Axe` / `Spear2h` / `Shield` 等键是硬前提（如狂战士星座要斧或矛），
    #   武器不对 ⇒ 该星位/技能完全不生效。
    #   ★ 2026-09-22 起走**统一入口** `procs.weapon_state_of(c)`（优先吃 `load_char`
    #     存下的 `c['weapon_st']`，取不到才从 `base_gids` 现推）—— 避免这里一套、
    #     CLI 一套、`tune_*` 另一套。
    weapon_st = PR.weapon_state_of(c)

    rep = R.final_report(arch, eff, db=c['db'], folded=c['folded'],
                         skill_records=list(eff), base_aps=base_aps, attr_pct=apct,
                         conversions=c.get('conversions') or [],
                         skill_mods=c.get('skill_mods') or {},
                         rr=rr_pack, attrs=attrs, level=c.get('level'), enemy=eprof,
                         crit_dmg_pct=crit_dmg_pct,
                         enemy_armor=e_arm, enemy_armor_src=e_src,
                         equipped_sk=eq_sk, item_wps=item_wps,
                         weapon_st=weapon_st)
    # 主要输出技能（按 dps 排序）
    atk = D.attack_rows(rep)
    _by_rec = {rec: h for rec, h in atk}

    def _type_rows(rec):
        """该技能的「逐伤害类型」分解（`gd.rotation.skill_type_breakdown` 产出）。"""
        h = _by_rec.get(rec) or {}
        return h.get('type_rows') or []

    _loop = [{'记录': rec, '技能': D.zh_name(rec, skills_zh), '等级': h['level'],
              '权重': h.get('chance_weight'), '冷却': h.get('cooldown'),
              '每秒几下': round(h.get('freq') or 0, 2),
              '每秒伤害': round(h.get('dps') or 0),
              '每秒伤害_实战': round(h.get('dps_vs') or h.get('dps') or 0),
              '每秒伤害_过甲': round(h.get('dps_final') or h.get('dps_vs')
                                     or h.get('dps') or 0),
              '弹数': h.get('projectiles'), '类型': list(h.get('types') or []),
              # ★ 逐类型构成（含抗性 / 穿甲）—— 报告的「伤害循环」直接消费
              '构成': _type_rows(rec)}
             for rec, h in atk]
    top = [{'技能': D.zh_name(rec, skills_zh), '等级': h['level'],
            '打一下': [round(h['min']), round(h['max'])],
            '每秒几下': round(h['freq'], 2), '每秒伤害': round(h['dps']),
            '每秒伤害_实战': round(h.get('dps_vs') or h['dps'])}
           for rec, h in atk[:4]]

    # ★ 优化目标：`GD_REAL_GOAL=vs`（默认）用实战 DPS（含减抗乘区），
    #   `=real` 用「含命中/暴击、但未减抗」的实战口径，`=panel` 退回旧面板口径。
    vs = rep.get('vs') or {}
    goal = (os.environ.get('GD_REAL_GOAL') or 'vs').strip().lower()
    d_goal = rep['dps']
    if goal == 'real':
        d_goal = rep.get('dps_real') or rep['dps']
    elif goal == 'vs' and vs.get('dps_vs'):
        d_goal = vs['dps_vs']
    # ★ `pct_ref`：把「+1% 全伤害」的边际折算成**单一等效加成基准**。
    #   为什么是**调和**均值而不是算术平均：`offensiveTotalDamageModifier` 对每种
    #   伤害类型都 +1%，而各类型现有加成差别很大（Sam：流血 +923%、穿刺 +851%），
    #   每点的相对增益是 `Σ_t share_t / (100 + pct_t)` —— 而 `1/(100+pct)` 是凸函数，
    #   所以它恒大于 `1/(100 + Σ share_t·pct_t)`。实测（Sam）算术口径偏 2.2%，
    #   换成调和口径后与有限差分**逐位吻合**（selftest [21] 断言 1e-3）。
    #   `gd/opt.py::recalibrate_crit` / `recalibrate_rr` 的标定都吃这个值。
    _pct = {t: float(v) for t, v in (rep.get('pct') or {}).items() if v}
    _sh = RR.dps_by_type(rep.get('skills') or {})
    _tot = sum(_sh.values()) or 1.0
    _inv = sum(_sh.get(t, 0.0) / (100.0 + _pct.get(t, 0.0)) for t in _sh) / _tot
    _pct_ref = (1.0 / _inv - 100.0) if _inv > 0 else 0.0
    _pct_avg = sum(_sh.get(t, 0.0) * _pct.get(t, 0.0) for t in _sh) / _tot
    return {'level': c['level'], 'classes': c['classes'], 'arch': arch,
            'dps': round(d_goal), 'dps_panel': round(rep['dps']),
            'dps_real': round(rep.get('dps_real') or rep['dps']),
            'dps_vs': round(vs.get('dps_vs') or rep['dps']),
            # ★ 第四层口径：再叠**过甲**（只有真值怪/显式护甲档才有值）
            'dps_final': round(rep.get('dps_final') or vs.get('dps_vs') or rep['dps']),
            'hit': rep.get('hit') or {},
            'oa': rep.get('oa'), 'da': rep.get('da'), 'pth': rep.get('pth'),
            'crit_dmg_pct': rep.get('crit_dmg_pct'),
            # 调和等效（标定用）与算术均值（报告用）并列，避免两者被搞混
            'pct_ref': round(_pct_ref, 2), 'pct_avg': round(_pct_avg, 1),
            'armor': rep.get('armor') or {},
            # ★ 防御轴（可选）：**只在 `GD_DEF_WEIGHT` 设了才算** ⇒ 默认零开销、零漂移。
            #   消费方 `tools/objfunc.py::_with_defense`，三处搜索（装备/星座/技能）共用。
            'def_score': (DF.quick_score(c, c.get('db'), da=rep.get('da'))
                          if os.environ.get('GD_DEF_WEIGHT') else None),
            'dot': rep.get('dot') or {},
            # ★ 伤害循环（官方 WPS 权重口径）—— 默认攻击槽 / 武器池 / 冷却技能 /
            #   `wps_blocked`。报告「伤害循环」章节直接读这里，避免二次计算。
            'rotation': rep.get('rotation') or {},
            # 逐技能的循环明细（中文名 + 权重/冷却 + 频率 + DPS + **逐类型构成**）
            'loop': _loop,
            # ★ 全循环「按伤害类型」汇总（实战口径，含减抗与过甲）
            'type_rows': rep.get('type_rows') or [],
            # ★ 技能来源合法性（A 方案）—— 剔除了哪些、为什么
            'legality': rep.get('skill_legality') or {},
            'enemy': rep.get('enemy') or {},
            'mult_vs': vs.get('mult_overall', 1.0),
            'rr': rep.get('rr') or {},
            'vs_rows': vs.get('rows') or [],
            'pct': {t: round(v, 1) for t, v in (rep.get('pct') or {}).items() if v},
            # 各伤害类型的 DPS 占比 —— 转化模型的「源伤害构成」
            'share': _sh,
            'aps': round(rep['aps'], 2),
            'weapon': rep['weapon'], 'top': top,
            # ★ 2026-09-20「伤害循环文档」：**原始 `hits`**（每技能的 `rows`，
            #   含逐类型 `origins` 来源归因 / `pct_parts` 乘区分层 / `mult`）。
            #   `loop` 里的『构成』是它的**渲染后**子集，会丢掉归因字段，
            #   故这里把原表一并带出来。纯新增键，不影响任何既有消费方。
            'hits': rep.get('skills') or {},
            'base_parts': rep.get('base_parts') or {},
            'slot_pct': rep.get('slot_pct') or {},
            'sk_pct_by_rec': rep.get('sk_pct_by_rec') or {}}


def marginals(char: str, plan: dict = None, arch: str = "",
              delta: float = 10.0,
              fields=("offensiveTotalDamageModifier", "offensivePierceModifier",
                      "offensiveSlowBleedingModifier")) -> dict:
    """**模型边际自测**：`+delta` 点各维度，实战 DPS 实际动多少。

    返回 `{'base': 基线评估, 'per_point': {字段: 每点实战 DPS}, 'crit_ratio': …}`。

    ★ 为什么这个函数是「权重表的裁判」：`gd/opt.py` 的 `W_DMG` 是**搜索排序用**的
      线性代理，它的正确性没法自证 —— 只能对着真实模型测。`crit_ratio`
      （暴击伤害每点 ÷ 全伤害每点）就是 `recalibrate_crit` 的解析值要对的靶子；
      `tools/selftest.py` 的 [21] 组拿它做断言。
    """
    base = dps_of(char, plan, arch)
    out = {'base': base, 'delta': delta, 'per_point': {}}
    for f in fields:
        b = dps_of(char, plan, arch, inject=(f, delta))
        out['per_point'][f] = (b['dps_real'] - base['dps_real']) / delta
    cd = base.get('crit_dmg_pct')
    if cd is not None:
        c2 = dps_of(char, plan, arch, crit_dmg_pct=float(cd) + delta)
        out['per_point']['crit'] = (c2['dps_real'] - base['dps_real']) / delta
        mt = out['per_point'].get('offensiveTotalDamageModifier') or 0.0
        out['crit_ratio'] = (out['per_point']['crit'] / mt) if mt else 0.0
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('char')
    ap.add_argument('plan')
    ap.add_argument('--arch', default='')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    plan = json.load(open(a.plan, encoding='utf-8'))
    r = dps_of(a.char, plan, a.arch)
    if a.json:
        print(json.dumps({'plan': os.path.basename(a.plan), 'dps': r['dps'],
                          'aps': r['aps'], 'top': r['top']}, ensure_ascii=False))
        return 0
    db = DB.load()
    print('\n=== %s ===' % os.path.basename(a.plan))
    print('  %s lv%d  合计每秒伤害 %s' % (a.char, r['level'], format(r['dps'], ',')))
    for t in r['top']:
        print('    %-12s %2d 级  打一下 %s~%s  每秒 %.2f 下 → %s'
              % (t['技能'], t['等级'], t['打一下'][0], t['打一下'][1],
                 t['每秒几下'], format(t['每秒伤害'], ',')))
    _ = db
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
