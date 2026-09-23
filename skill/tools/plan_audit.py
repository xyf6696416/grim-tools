# -*- coding: utf-8 -*-
"""plan_audit.py —— 方案体检：真实 DPS + 属性重排 + 口径敏感性。

为什么需要它（2026-09-20）
--------------------------
`gd/planreport.py` 出的是**装备方案报告**（装备表 / 抗性表 / 伤害代理 / 技能点 / 星座），
它的「伤害」是**搜索排序用的线性代理**，不是面板/实战 DPS。
「这个方案到底打多少」和「属性点还能不能再榨」这两件事一直没有单一入口 ——
本工具补上，并且**只做已有模块的编排**，不复制任何公式。

分层（同一个 analyze 喂三种消费方）
    analyze(...)   -> dict      纯数据，不打印（`gd/planreport.py --audit` 也吃它）
    render_text()  -> str       CLI 终端版
    render_md()    -> str       Markdown 版（并进方案报告，重生成不丢）

用法
----
    python tools/plan_audit.py Sam data/plans/X.json --arch raven_nightblade \
        --alloc data/scratch/alloc71_raven_nightblade.json

    --alloc  ：形态加点 JSON（等价于 `GD_SKILL_JSON`）。不传 ⇒ 用存档加点。
    --attr   ：`auto`（默认）做属性重排并给出最优；`none` 跳过。
    --proj   ：口径敏感性对照，如 `--proj 1,4,8`（未声明的形态自动跳过）。
    --md     ：输出 Markdown（与 `--json` 互斥，优先 `--json`）。
    --json   ：机器可读输出。

口径提醒（见 SKILL §3 与 `gd/rotation.py` 的注释）
    · `dps`      面板口径（无命中/减抗）
    · `dps_real` 再乘命中/暴击期望倍率（敌方 DA 参与）
    · `dps_vs`   再乘敌方减抗乘区 —— **优化器的目标就是它**
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'tools'))


def _f(v):
    return format(v, ',.0f') if isinstance(v, (int, float)) else str(v)


# ---------------------------------------------------------------------------
# 数据层：不打印、不改全局（除 GD_SKILL_JSON / GD_PROJ_HITS 这两个显式入参开关）
# ---------------------------------------------------------------------------
def analyze(char, plan, arch='', alloc='', attr='auto', proj='', enemy=''):
    """跑完三层口径 + 属性重排 + 口径敏感性，返回可序列化的 dict。

    `enemy`：敌方档（`gd/enemy.py` 口径）—— 五档 `none/elite/boss/high/max`、
    真值怪 `m<id>`、等级池 `pool:Champion+Hero@0.5`。**默认读 `GD_ENEMY_PROFILE`**。
    ★ 只有**真值怪**（`m<id>` / 假人）带护甲值 ⇒ 想要「穿甲」那一列有数字，
      必须传真值怪；等级池/五档没有单一护甲值，报告会如实标注「物理直伤为下界」。
    """
    if alloc:
        os.environ['GD_SKILL_JSON'] = alloc
    if enemy:
        os.environ['GD_ENEMY_PROFILE'] = enemy
    import plan_dps as PD                                    # noqa: E402
    from gd import reqfit as RF                              # noqa: E402

    if isinstance(plan, str):
        plan = json.load(open(plan, encoding='utf-8'))
    arch = arch or os.environ.get('GD_ARCHETYPE', '')
    base = PD.dps_of(char, plan, arch)

    out = {'plan': '', 'arch': base['arch'], 'level': base.get('level'),
           'dps_panel': base['dps_panel'], 'dps_real': base['dps_real'],
           'dps_vs': base['dps_vs'], 'dps_final': base.get('dps_final'),
           'mult_vs': base.get('mult_vs'),
           'aps': base.get('aps'), 'hit': base['hit'],
           'oa': base['oa'], 'da': base['da'], 'pth': base['pth'],
           'top': base['top'], 'share': {k: round(v) for k, v in
                                         (base.get('share') or {}).items() if v},
           # ★ 伤害循环：默认攻击 / 武器池(WPS)权重 / 冷却技能 / `wps_blocked`
           'rotation': base.get('rotation') or {}, 'loop': base.get('loop') or [],
           # ★ 逐类型构成 / 敌方抗性表 / 护甲（含穿甲）/ 合法性
           'type_rows': base.get('type_rows') or [],
           'vs_rows': base.get('vs_rows') or [],
           'armor': base.get('armor') or {},
           'rr': base.get('rr') or {},
           'enemy': base.get('enemy') or {},
           'legality': base.get('legality') or {},
           'char': char}

    # ---- 属性重排（装备定下来之后，加点三围还能不能再榨）
    if attr == 'auto':
        ov = PD.plan_to_override(plan)
        best = None
        for prefer in ('cunning', 'spirit', 'physique', None):
            try:
                fit = RF.solve(char, override=ov, prefer=prefer, spread=False)
            except Exception:
                continue
            if not fit.feasible:
                continue
            bio = {k: float(v) for k, v in (fit.targets or {}).items()}
            d = PD.dps_of(char, plan, arch, bio_override=bio)
            gain = (d['dps'] / base['dps'] - 1.0) * 100.0
            if best is None or gain > best['gain']:
                best = {'prefer': prefer or '(默认)', 'gain': round(gain, 2),
                        'points': {k: int(v) for k, v in fit.points_after.items()},
                        'panel': {k: int(v) for k, v in fit.panel_after.items()},
                        'block2': {k: int(v) for k, v in fit.targets.items()},
                        'dps': int(d['dps']), 'warnings': list(fit.warnings or [])}
        out['attr_refit'] = best

    # ---- 口径敏感性（`projectile_hits` 是场景假设，不是数据库事实）
    if proj:
        rows = []
        old = os.environ.get('GD_PROJ_HITS')
        for n in [x for x in proj.split(',') if x.strip()]:
            os.environ['GD_PROJ_HITS'] = n.strip()
            d = PD.dps_of(char, plan, arch)
            rows.append({'hits': float(n), 'dps': int(d['dps'])})
        if old is None:
            os.environ.pop('GD_PROJ_HITS', None)
        else:
            os.environ['GD_PROJ_HITS'] = old
        if len({r['dps'] for r in rows}) > 1:
            out['proj_sensitivity'] = rows

    # ---- ★★ 抗性来源分解（2026-09-20 并入全链路）
    #   逻辑在 `gd/resaudit.py`（单一真源，`gd/planreport.py` §二 与独立 CLI 也调它）。
    #   在这里**预渲染成行**存进 `out['_res_md']`，`render_md` 直接 extend ——
    #   这样不用给 render_md 多加参数（它现在只吃 `o`）。
    try:
        from gd import resaudit as RA
        _per = RA.per_slot_from_plan(plan)
        out['_res_md'] = RA.render_md(_per, plan=plan, top=3, detail=2)
        out['res_overflow'] = {
            t: RA.totals(_per).get(t, 0) - RA.NEED[t] for t in RA.TYPES}
        out['res_overflow_sum'] = sum(v for v in out['res_overflow'].values() if v > 0)
    except Exception as _e:                                          # noqa: BLE001
        out['_res_md'] = ['### 抗性来源分解（为什么会溢出）', '',
                          '> ⚠ 生成失败：%s: %s' % (type(_e).__name__, _e), '']
    return out


# ---------------------------------------------------------------------------
# 渲染层
# ---------------------------------------------------------------------------
def render_text(o):
    hit = o.get('hit') or {}
    L = []
    L.append('=' * 78)
    L.append('%s ｜ 流派 %s ｜ lv%s' % (o.get('char', '?'), o['arch'], o.get('level')))
    L.append('=' * 78)
    lg = o.get('legality') or {}
    if lg.get('checked'):
        dr = lg.get('dropped') or []
        L.append('技能来源合法性：%s'
                 % ('✓ 全部合法' if not dr else
                    '✗ 剔除 %d 条 %s' % (len(dr), [x.get('zh') for x in dr])))
    L.append('真实 DPS')
    L.append('   面板            %12s' % _f(o['dps_panel']))
    L.append('   实战（含命中）    %12s' % _f(o['dps_real']))
    L.append('   实战（含减抗）★   %12s   ← 优化器目标' % _f(o['dps_vs']))
    if o.get('dps_final') is not None:
        L.append('   实战（再叠过甲）  %12s' % _f(o['dps_final']))
    L.append('   减抗乘区 %.3f× ｜ 攻速 %.2f 下/秒' % (o.get('mult_vs') or 1.0,
                                                      o.get('aps') or 0.0))
    L.append('')
    L.append('命中 / 暴击')
    L.append('   OA %s ｜ DA %s ｜ PTH %.2f' % (_f(o['oa']), _f(o['da']),
                                                 o['pth'] or 0.0))
    if hit:
        L.append('   命中率 %.1f%% ｜ 暴击率 %.2f%% ｜ 期望倍率 %.4f'
                 % ((hit.get('hit_chance') or 0) * 100, (hit.get('crit_chance') or 0) * 100,
                    hit.get('expected') or 0.0))
    L.append('')
    L.extend(_loop_text(o))
    L.append('')
    L.append('主输出')
    for t in o['top']:
        L.append('   %-14s %2d 级 ｜ 打一下 %s~%s ｜ %.2f 下/秒 → %s'
                 % (t['技能'], t['等级'], _f(t['打一下'][0]), _f(t['打一下'][1]),
                    t['每秒几下'], _f(t['每秒伤害'])))
    if o.get('share'):
        L.append('   ' + _share_line(o['share']))
    r = o.get('attr_refit')
    if r:
        L.append('')
        L.append('属性重排（硬约束：全套装备都穿得上）')
        L.append('   最优 余额投 %-10s ｜ 加点 %s' % (r['prefer'], r['points']))
        L.append('   面板三围 %s' % r['panel'])
        L.append('   DPS %s  %+.2f%% ｜ 存档 block2 目标 %s'
                 % (_f(r['dps']), r['gain'], r['block2']))
        if r['warnings']:
            L.append('   ⚠ ' + '; '.join(r['warnings']))
    for r in (o.get('proj_sensitivity') or []):
        pass
    if o.get('proj_sensitivity'):
        L.append('')
        L.append('口径敏感性（投射物命中数）')
        for r in o['proj_sensitivity']:
            L.append('   ×%-4g → %s' % (r['hits'], _f(r['dps'])))
    return '\n'.join(L)


# 伤害桶名（`gd/dmg.py` 的英文键）→ 中文，报告里别露英文
_DMG_ZH = {'physical': '物理', 'pierce': '穿刺', 'fire': '火', 'cold': '冰',
           'lightning': '电', 'poison': '毒酸', 'bleeding': '流血',
           'vitality': '活力', 'aether': '虚化', 'chaos': '混乱',
           'frostburn': '霜燃', 'burn': '燃烧', 'electrocute': '电刑',
           'poisonDot': '毒DoT', 'internalTrauma': '内伤',
           'vitalityDecay': '活力衰减', 'decay': '衰减'}


def _pt(lo, hi):
    """每击区间 → 紧凑字符串（1 万以下取整、以上用 k 记法，避免表格被数字撑爆）。"""
    def one(v):
        v = float(v or 0.0)
        return ('%.1fk' % (v / 1000.0)) if v >= 10000 else ('%d' % round(v))
    if lo is None and hi is None:
        return '—'
    return '%s~%s' % (one(lo), one(hi))


def _mix_str(rows, top=4):
    """一个技能的**伤害构成**一句话：`冰 1.2k~1.6k（62%）｜穿刺 456~789（31%）`。

    ★ 用户口径（2026-09-20）：「我要看到**具体的技能伤害构成** —— 多少物理、
      多少穿刺、多少冰冷这种」。数值是**面板口径的一击**（含转化与 % 加成之后），
      括号里是该类型在这个技能 DPS 里的**占比**（DoT 行已按覆盖率折算）。
    """
    if not rows:
        return '—'
    parts = []
    for r in rows[:top]:
        nom = _DMG_ZH.get(r.get('type'), r.get('type'))
        seg = '%s %s（%.0f%%）' % (nom, _pt(*(r.get('per_hit') or [None, None])),
                                  (r.get('share') or 0.0) * 100)
        if r.get('is_dot'):
            d = r.get('dur')
            seg = seg[:-1] + ('，DoT\uff1a%gs）' % d if d else '，DoT）')
        parts.append(seg)
    if len(rows) > top:
        rest = sum((r.get('share') or 0.0) for r in rows[top:]) * 100
        parts.append('其余 %.0f%%' % rest)
    return ' ｜ '.join(parts)


def _loop_rows(o):
    """把 `rotation` + `loop` 合成「伤害循环」的结构化行（text / md 共用）。

    官方口径（Crate 设计师 Zantai，Grim Misadventure #54）：
      `skillChanceWeight` 是**权重不是百分比** —— 武器池技能先按权重从默认攻击的
      100 里扣，扣完还剩下多少就是**默认攻击（普攻）出现的权重**；总权重超过 100
      时，默认攻击权重归零 ⇒ **普攻彻底不再出现**，此时分母改成总权重。
    """
    rot = o.get('rotation') or {}
    loop = o.get('loop') or []
    if not rot:
        return None, []
    by_rec = {x.get('记录'): x for x in loop}

    def nm(rec, cache={}):
        if not rec:
            return '—'
        if rec in cache:
            return cache[rec]
        x = by_rec.get(rec)
        if x and x.get('技能'):
            n = x['技能']
        elif rec == '__basic_weapon_attack__':
            n = '普通攻击'
        else:
            n = os.path.basename(str(rec)).replace('.dbr', '')
        cache[rec] = n
        return n

    W = float(rot.get('weight_total') or 0.0)
    wd = float(rot.get('default_weight') or 0.0)
    den = float(rot.get('denom') or 100.0) or 100.0
    dft = rot.get('default')
    procs = set(rot.get('procs') or [])
    cds = set(rot.get('cooldowns') or [])
    rows = []
    for x in loop:
        rec = x.get('记录')
        if rec not in procs and rec not in cds and rec != dft:
            continue
        w, cd = x.get('权重'), x.get('冷却')
        if rec == dft:
            role, share = '默认攻击', (wd / den if W <= 100 else 0.0)
        elif w and w > 0:
            role, share = '武器池(WPS)', w / den
        elif cd and cd > 0:
            role, share = '冷却技能', None
        else:
            continue
        rows.append({'技能': x.get('技能'), '角色': role, '等级': x.get('等级'),
                     '权重': w, '冷却': cd, '占比': share,
                     '频率': x.get('每秒几下'), 'DPS': x.get('每秒伤害'),
                     'DPS_vs': x.get('每秒伤害_实战'), 'DPS_final': x.get('每秒伤害_过甲'),
                     # ★ 逐伤害类型构成（面板口径的一击 + 该类型的 DPS 占比）
                     '构成': x.get('构成') or [],
                     '弹数': x.get('弹数'), '类型': x.get('类型') or []})
    rows.sort(key=lambda r: -(r['DPS'] or 0))
    head = {'default': nm(dft), 'default_rec': dft,
            'declared': rot.get('primary_attack'), 'honored': rot.get('primary_honored'),
            'W': W, 'w_def': wd, 'den': den, 'blocked': bool(rot.get('wps_blocked')),
            'dps_swing': rot.get('dps_swing'), 'dps_cd': rot.get('dps_cooldown'),
            'bench': [(b.get('tag'), b.get('avg')) for b in (rot.get('bench') or [])]}
    return head, rows


def _enemy_md(o):
    """「敌方参数」段：抗性（逐桶真值）· 减抗 · 护甲 · 穿甲。

    ★ 用户口径（2026-09-20）：「把**抗性、穿甲**也加入计算」。这一段把参与
      伤害循环的每一项敌方参数**摆出来**，让上面的 DPS 不是黑箱。
    """
    vs_rows = o.get('vs_rows') or []
    armor = o.get('armor') or {}
    rr = o.get('rr') or {}
    if not vs_rows:
        return []
    try:
        from gd.rr import BUCKET_ZH
    except Exception:                                        # noqa: BLE001
        BUCKET_ZH = {}
    # 抗性桶名与伤害类型同名（`gd/rr.py::RES_BUCKETS`），直接复用中文表
    nm = lambda b: _DMG_ZH.get(b, BUCKET_ZH.get(b, b))       # noqa: E731
    n = o.get('enemy') or {}
    L = ['**敌方参数（进入上面每一行的乘区）**', '']
    L.append('- 敌方档：%s' % (n.get('zh') or n.get('id') or '（未知）'))
    L.append('')
    L.append('| 抗性桶 | 敌方基础抗性 | 减抗（绝对值） | 减抗（%） | **减抗后抗性** '
             '| 伤害倍数 | 该桶面板 DPS | 该桶实战 DPS |')
    L.append('|---|---|---|---|---|---|---|---|')
    for r in vs_rows:
        L.append('| %s | %s%% | %s | %s%% | **%s%%** | **%.3f×** | %s | %s |'
                 % (nm(r.get('bucket')),
                    _f(r.get('base')),
                    ('−%g' % r['rr_add']) if r.get('rr_add') else '—',
                    ('−%g' % r['rr_pct']) if r.get('rr_pct') else '—',
                    _f(r.get('res')), r.get('mult') or 1.0,
                    _f(r.get('dps')), _f(r.get('dps_vs'))))
    L.append('')
    if armor:
        red = armor.get('reduce') or {}
        if red.get('armor_before'):
            L.append('- **护甲**：敌方护甲 **%s** ｜ 吸收率 **%.0f%%**（%s）'
                     ' ｜ 破甲 −%s ⇒ 有效护甲 **%s**'
                     % (_f(red.get('armor_before')), armor.get('absorption') or 0,
                        armor.get('absorption_basis') or '—',
                        _f(red.get('value')), _f(red.get('armor_after'))))
        else:
            L.append('- **护甲**：%s' % (armor.get('reason') or '该敌方档无单一护甲值'))
        L.append('- **护甲穿透**：武器「%% 护甲穿透」= **%s%%** —— 把**残余物理**'
                 '转成穿刺（穿刺**绕过护甲**）；它已体现在上表的类型拆分里，'
                 '**不是**「降低目标护甲」（那是怪物侧机制，玩家侧恒为 0，'
                 '`gd rr --scan` 实测全库玩家侧 0 次）'
                 % _f(_pierce_of(o)))
        bt = armor.get('by_type') or {}
        if bt:
            L.append('- **逐类型裁决**：%s'
                     % ' ｜ '.join('%s %s' % (v.get('zh'), v.get('verdict'))
                                   for v in bt.values()))
    rr_add = (rr.get('add') or {})
    rr_max = (rr.get('max') or {})
    if rr_add or rr_max:
        L.append('- **减抗来源合计**：绝对值 %s ｜ 百分比 %s'
                 % ('、'.join('−%g %s' % (v, nm(k)) for k, v in sorted(rr_add.items()))
                    or '无',
                    '、'.join('−%g%% %s' % (v, nm(k)) for k, v in sorted(rr_max.items()))
                    or '无'))
    L.append('')
    return L


def _pierce_of(o):
    for x in (o.get('loop') or []):
        for r in (x.get('构成') or []):
            if r.get('armor_pierce_pct'):
                return r['armor_pierce_pct']
    return 0.0


def _type_total_md(o):
    """全循环「按伤害类型」汇总 —— 一张表回答「物理/穿刺/冰冷各占多少」。"""
    rows = o.get('type_rows') or []
    if not rows:
        return []
    tot_f = sum(r['dps_final'] for r in rows) or 1.0
    L = ['**总伤害构成（实战口径：含命中 · 减抗 · 过甲）**', '']
    L.append('| 伤害类型 | 每秒伤害（面板） | 每秒伤害（实战） | **占比** |')
    L.append('|---|---|---|---|')
    for r in rows:
        L.append('| %s%s | %s | %s | **%.1f%%** |'
                 % (r.get('zh'), '（DoT）' if r.get('is_dot') else '',
                    _f(r.get('dps_panel')), _f(r.get('dps_final')),
                    r['dps_final'] / tot_f * 100))
    L.append('')
    return L


def _loop_text(o):
    """终端版的伤害循环段。"""
    head, rows = _loop_rows(o)
    if not head:
        return []
    L = []
    L.append('伤害循环（官方武器池权重口径）')
    L.append('   默认攻击（左键）  %s' % head['default'])
    L.append('   武器池总权重 W = %-6g ｜ 默认攻击权重 = %-6g ｜ 分母 = %g'
             % (head['W'], head['w_def'], head['den']))
    if head['blocked']:
        L.append('   ⚠ WPS 被阻断：默认攻击无武器伤害（纯法术）⇒ 整池武器池不触发')
    for r in rows:
        sh = '%.1f%%' % (r['占比'] * 100) if r['占比'] is not None else '冷却'
        L.append('   %-6s %-14s %2s 级 ｜ 权重 %-5s ｜ 占比 %-7s ｜ %.2f 下/秒 → %s'
                 % (r['角色'], (r['技能'] or '?')[:14], r['等级'] or 0,
                    r['权重'] if r['权重'] else '—', sh, r['频率'] or 0.0, _f(r['DPS'])))
        if r['构成']:
            for t in r['构成']:
                L.append('        · %-6s %-12s 占比 %5.1f%% ｜ 抗性后 %-8s ｜ 倍数 %.3f×'
                         ' ｜ 护甲 %s ｜ 穿透 %s'
                         % (_DMG_ZH.get(t.get('type'), t.get('type')),
                            _pt(*(t.get('per_hit') or [None, None])),
                            (t.get('share') or 0) * 100,
                            ('%s%%' % _f(t.get('res'))) if t.get('res') is not None else '—',
                            t.get('combat_mult') or 1.0,
                            ('−%.1f%%' % t['armor_pct']) if t.get('armor_pct') is not None else '不过甲',
                            ('%.1f%%' % t['armor_pierce_pct']) if t.get('armor_pierce_pct') else '—'))
    if head['dps_swing'] is not None:
        L.append('   挥击 %s ｜ 冷却 %s' % (_f(head['dps_swing']), _f(head['dps_cd'])))
    return L


def _loop_md(o):
    """Markdown 版的伤害循环段（含**逐技能伤害构成**与**抗性/穿甲**）。"""
    head, rows = _loop_rows(o)
    if not head:
        return []
    L = []
    L.append('## ★ 伤害循环（官方武器池权重口径）')
    L.append('')
    L.append('> **官方规则**（Crate 设计师 Zantai，Grim Misadventure #54）：'
             '`skillChanceWeight` 是**权重，不是百分比**。武器池技能（WPS）先按权重'
             '从默认攻击的 100 里扣，剩下的才是**默认攻击（普攻）**的权重；'
             '总权重超过 100 时默认攻击权重归零 —— **普攻彻底不再出现**。')
    L.append('')
    L.append('- **默认攻击（左键）**：`%s`' % head['default'])
    L.append('- **武器池总权重 W = %g** ｜ 默认攻击权重 = **%g** ｜ 掷骰分母 = %g'
             % (head['W'], head['w_def'], head['den']))
    if head['blocked']:
        L.append('- ⚠️ **武器池（WPS）被阻断**：默认攻击 `%s` **不带武器伤害**'
                 '（`weaponDamagePct = 0` 的纯法术型攻击）⇒ 整套武器池技能'
                 '**一次都不会触发**。本 BD 的伤害全部来自默认攻击 + 冷却技能。'
                 % head['default'])
    if head['bench']:
        L.append('- 落选的基础攻击（争同一个左键槽）：%s'
                 % '、'.join('%s（单下 %s）' % (t, _f(a)) for t, a in head['bench'][:4]))
    L.append('')
    L.append('### 循环总表')
    L.append('')
    L.append('| 角色 | 技能 | 等级 | 权重 | 实际占比 | 频率 | DPS（面板） '
             '| DPS（实战·含命中减抗） | **伤害构成（每击 · 面板口径）** |')
    L.append('|---|---|---|---|---|---|---|---|---|')
    for r in rows:
        sh = '**%.1f%%**' % (r['占比'] * 100) if r['占比'] is not None else '按冷却'
        L.append('| %s | %s | %d | %s | %s | %.2f 下/秒 | %s | %s | %s |'
                 % (r['角色'], r['技能'], r['等级'] or 0,
                    ('%g' % r['权重']) if r['权重'] else ('冷却 %.1fs' % r['冷却']
                                                        if r['冷却'] else '—'),
                    sh, r['频率'] or 0.0,
                    _f(r['DPS']),
                    _f(r['DPS_vs']),
                    _mix_str(r['构成'])))
    L.append('')
    if head['dps_swing'] is not None:
        L.append('> 挥击合计 **%s** ｜ 冷却技能合计 **%s**（均为面板口径；'
                 '实战口径见下表与「敌方参数」段）'
                 % (_f(head['dps_swing']), _f(head['dps_cd'])))
        L.append('')
    # ---- 逐技能 × 逐伤害类型：面板 → 减抗 → 过甲 ----
    det = [r for r in rows if r['构成']]
    if det:
        L.append('### 逐技能伤害构成（含抗性 · 穿甲）')
        L.append('')
        L.append('> 每一行 = **一个技能的一个伤害类型**。`每击` 是面板口径的一击'
                 '（已含伤害转化与 % 加成）；`敌方抗性` 是该类型的**基础抗性**，'
                 '`减抗后` 是叠加我方减抗之后的值；`抗性倍数` = `(100−减抗后)/(100−基础)`；'
                 '`护甲减免` **只作用于物理直伤**（穿刺/元素/流血不过甲）；'
                 '`护甲穿透` 是武器 `% 护甲穿透` 把残余物理转成穿刺的比例'
                 '（已体现在类型拆分里）。')
        L.append('')
        L.append('| 技能 | 伤害类型 | 每击（面板） | 该技能内占比 | 敌方抗性 '
                 '| 减抗后 | 抗性倍数 | 护甲减免 | 护甲穿透 | 综合倍数 '
                 '| 每秒（面板） | 每秒（实战） | 每秒（过甲后） |')
        L.append('|---|---|---|---|---|---|---|---|---|---|---|---|---|')
        for r in det:
            for t in r['构成']:
                L.append('| %s | %s%s | %s | %.1f%% | %s | %s | %.3f× | %s | %s | %.3f× '
                         '| %s | %s | %s |'
                         % (r['技能'], _DMG_ZH.get(t.get('type'), t.get('type')),
                            '（DoT %gs）' % t['dur'] if t.get('is_dot') and t.get('dur')
                            else ('（DoT）' if t.get('is_dot') else ''),
                            _pt(*(t.get('per_hit') or [None, None])),
                            (t.get('share') or 0.0) * 100,
                            ('%s%%' % _f(t.get('res_base'))) if t.get('res_base') is not None
                            else '—',
                            ('%s%%' % _f(t.get('res'))) if t.get('res') is not None else '—',
                            t.get('rr_mult') or 1.0,
                            ('−%.2f%%' % t['armor_pct']) if t.get('armor_pct') is not None
                            else '**不过甲**',
                            ('%.1f%%' % t['armor_pierce_pct']) if t.get('armor_pierce_pct')
                            else '—',
                            t.get('combat_mult') or 1.0,
                            _f(t.get('dps_panel')), _f(t.get('dps_vs')),
                            _f(t.get('dps_final'))))
        L.append('')
    L.extend(_type_total_md(o))
    L.extend(_enemy_md(o))
    return L


def _share_line(share):
    tot = sum(share.values()) or 1.0
    return '伤害构成：' + ' ｜ '.join(
        '%s %.0f%%' % (_DMG_ZH.get(k, k), v / tot * 100) for k, v in
        sorted(share.items(), key=lambda x: -x[1])[:6])


def _legality_md(o):
    """技能来源合法性段（A 方案）：被剔除的形态技能 + 原因。

    ★ 静默剔除是这个 bug 的第一特征 —— 所以**必须显式打印**，哪怕结果是空。
    """
    lg = o.get('legality') or {}
    if not lg.get('checked'):
        return []
    dr = lg.get('dropped') or []
    L = ['**技能来源合法性**（`records/skills/itemskills*` 必须由**已装备物品**授予）',
         '']
    if not dr:
        L.append('- ✓ 本形态的全部技能都合法（专精树技能，或已由装备授予）')
        L.append('')
        return L
    L.append('- ✗ **被剔除 %d 条**（这些技能的授予物品没穿在身上 / 等级不够）：' % len(dr))
    L.append('')
    L.append('| 技能 | 记录 | 判定 | 授予者（物品 · 等级需求） |')
    L.append('|---|---|---|---|')
    for x in dr:
        prov = '、'.join('%s（k=%s）' % (p.get('gid'), p.get('k'))
                        for p in (x.get('providers') or [])) or '—'
        L.append('| %s | `%s` | %s | %s |'
                 % (x.get('zh'), os.path.basename(str(x.get('rec'))),
                    x.get('reason'), prov))
    L.append('')
    return L


def render_md(o):
    """Markdown 版 —— 由 `gd/planreport.py --audit` 追加到方案报告。"""
    hit = o.get('hit') or {}
    L = []
    L.extend(_legality_md(o))
    L.append('## ★ 极限数字（三层口径 · `tools/plan_audit.py`）')
    L.append('')
    exp = hit.get('expected')
    L.append('| 口径 | 数值 | 含义 |')
    L.append('|---|---|---|')
    L.append('| 面板 DPS | **%s** | 只含装备/技能/星座属性，**无命中、无减抗** |'
             % _f(o['dps_panel']))
    L.append('| 实战（含命中） | **%s** | × 期望倍率 %s |'
             % (_f(o['dps_real']), ('%.4f' % exp) if exp else '—'))
    L.append('| **实战（含减抗）★** | **%s** | × 减抗乘区 **%.3f×** —— 优化器目标 |'
             % (_f(o['dps_vs']), o.get('mult_vs') or 1.0))
    if o.get('dps_final') is not None:
        _km = ((o.get('dps_final') or 0) / (o.get('dps_vs') or 1)) if o.get('dps_vs') else 1.0
        L.append('| 实战（再叠过甲） | **%s** | 物理直伤 × 护甲减免（×%.4f，'
                 '见「敌方参数」段）|' % (_f(o['dps_final']), _km))
    r = o.get('attr_refit')
    if r:
        L.append('| 并案属性重排 | **%s（%+.2f%%）** | 加点 %s |'
                 % (_f(r['dps']), r['gain'], r['points']))
    L.append('')
    if hit:
        L.append('**命中 / 暴击**：OA **%s** ｜ DA **%s** ｜ PTH **%.2f** ｜ 命中率 **%.1f%%** '
                 '｜ 暴击率 **%.2f%%** ｜ 期望倍率 **%.4f**'
                 % (_f(o['oa']), _f(o['da']), o['pth'] or 0.0,
                    (hit.get('hit_chance') or 0) * 100,
                    (hit.get('crit_chance') or 0) * 100, exp or 0.0))
        L.append('')
        L.append('> PTH `%.2f` ⇒ 对该敌方档约有 **%.0f%% 的挥空**。%s'
                 % (o['pth'] or 0.0, (1 - (hit.get('hit_chance') or 0)) * 100,
                    'OA 已过暴击门槛。' if (hit.get('crit_chance') or 0) > 0
                    else '暴击率 0% 是因为 OA 尚未过暴击门槛 —— 本套属「稳命中、零暴击」型；'
                         '想换暴击须继续堆 OA，代价是牺牲别的维度。'))
    L.append('')
    L.extend(_loop_md(o))
    # ★★ 抗性来源分解（为什么满抗了还溢出这么多）—— 逻辑在 `gd/resaudit.py`
    if o.get('_res_md'):
        L.extend(o['_res_md'])
    L.append('**主输出**')
    L.append('')
    L.append('| 技能 | 等级 | 单下 | 频率 | DPS |')
    L.append('|---|---|---|---|---|')
    for t in o['top']:
        L.append('| %s | %d | %s~%s | %.2f 下/秒 | %s |'
                 % (t['技能'], t['等级'], _f(t['打一下'][0]), _f(t['打一下'][1]),
                    t['每秒几下'], _f(t['每秒伤害'])))
    L.append('')
    if o.get('share'):
        L.append(_share_line(o['share']).replace('伤害构成：', '伤害构成：**') + '**')
        L.append('')
    if r:
        L.append('**属性重排**（硬约束：重排后全套装备仍全部穿得上）')
        L.append('')
        L.append('- 加点 `%s`' % r['points'])
        L.append('- 面板三围：%s' % ' ｜ '.join(
            '%s **%s**' % (_ATTR_ZH.get(k, k), _f(v)) for k, v in r['panel'].items()))
        L.append('- 存档 block2 目标 `%s`（= 50 + 8×点数）' % r['block2'])
        if r['warnings']:
            L.append('- ⚠ ' + '; '.join(r['warnings']))
        L.append('')
    if o.get('proj_sensitivity'):
        L.append('**口径敏感性**（投射物命中数 —— 场景假设，非数据库事实）')
        L.append('')
        for x in o['proj_sensitivity']:
            L.append('- ×%-4g → %s' % (x['hits'], _f(x['dps'])))
        L.append('')
    return '\n'.join(L).rstrip() + '\n'


_ATTR_ZH = {'physique': '体格', 'cunning': '狡诈', 'spirit': '精神'}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('char')
    ap.add_argument('plan')
    ap.add_argument('--arch', default='')
    ap.add_argument('--alloc', default='', help='形态加点 JSON（= GD_SKILL_JSON）')
    ap.add_argument('--attr', default='auto', choices=['auto', 'none'])
    ap.add_argument('--proj', default='', help='口径对照，如 1,4,8')
    ap.add_argument('--enemy', default='',
                    help='敌方档（gd/enemy.py）：五档 none/elite/boss/high/max、'
                         '真值怪 m<id>（如 m3955）、等级池 pool:Champion+Hero@0.5。'
                         '★ 只有真值怪带护甲值 ⇒ 想让「穿甲」有数字就用它。'
                         '默认读 GD_ENEMY_PROFILE')
    ap.add_argument('--md', action='store_true', help='输出 Markdown')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    out = analyze(a.char, a.plan, arch=a.arch, alloc=a.alloc,
                  attr=a.attr, proj=a.proj, enemy=a.enemy)
    out['plan'] = os.path.basename(a.plan)

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    elif a.md:
        sys.stdout.write(render_md(out))
    else:
        print(render_text(out))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
