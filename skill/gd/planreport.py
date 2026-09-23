# -*- coding: utf-8 -*-
r"""gd_plan_report —— 把 `gd_opt.py` 的方案 JSON 渲染成**中文装备报告**（Markdown）

`gd_opt.py` 自己会打印一份明细，但只到终端、且词缀/镶嵌/附魔只有 id。
本脚本把它固化成可交付的 Markdown：装备表（中文名/稀有度/词缀/镶嵌/附魔/自带技能）
+ 抗性表 + 输出属性表 + 装备需求与「能不能穿上」的结论。

用法：
    python gd_plan_report.py <plan.json> [--char Sam] [--archetype wolf_nightblade]
                             [--level 63] [--title "…"] [--out x.md]

环境变量与 `gd_opt.py` **同口径**（GD_ARCHETYPE / GD_MAX_ILVL / GD_FULL / GD_OVER_PEN /
GD_DMG_TIE / GD_SKILL_K …）—— 因为报告要复算抗性与伤害代理，口径不一致就会对不上号。
"""
import argparse
import datetime
import re
import json
import os
import sys


ARGS = [a for a in sys.argv[1:]]

# 与 eval_plan.py 同样的「最小化启动」：import gd_opt 会跑一整轮搜索，
# 把 argv 压到最小（beam 40 / restart 1）把这一次开销降到 ~0.3 s。
os.environ.setdefault('GD_MAX_ILVL', '63')
os.environ.setdefault('GD_AUTO_TOPN', '70')
os.environ.setdefault('GD_COMP_TOPN', '16')
os.environ.setdefault('GD_AUG_TOPN', '10')
os.environ.setdefault('GD_SLOTS', '头部,项链,胸甲,腿甲,靴子,手套,戒指1,戒指2,腰带,肩甲,勋章,圣物')
os.environ.setdefault('GD_DMG_TIE', '1.2')
os.environ.setdefault('GD_OVER_PEN', '1.0')


def earned_points(char):
    """★ 存档里的点数**实况**（不是等级理论值）。

    为什么要它（2026-09-20 实测）：星座建议一度按 `devotion_budget(level)` = **等级上限**
    （lv73 → 55）生成，而角色实际只解锁了 **40 点**（`total_devotion_points` **36 = 已花** +
    `devotion_points` **4 = 未分配**）⇒ 报告会给出**点不出来**的 51 点方案。技能点同理：等级预算
    206，存档却是 245（243 已投 + 2 未投）⇒ 属**非标准来源**（训练器 / mod），必须如实标出来。

    ⚠ 别把 `total_devotion_points` 当「已解锁」—— 那是**已花**（2026-09-21 更正）。

    返回 `{键: 值}`；读不到就给空 dict（调用方回退到理论值）。
    """
    try:
        from gd import paths as _P
        from gd.save import core as _SC
        sd, _k = _P.save_dir()
        if not sd:
            return {}
        p = os.path.join(str(sd), 'main', '_%s' % str(char).lstrip('_'), 'player.gdc')
        if not os.path.isfile(p):
            return {}
        b2 = (_SC.parse(p, record=False).get('block_map') or {}).get(2) or {}
        return {k: b2.get(k) for k in (
            'devotion_points', 'total_devotion_points',
            'skill_points', 'attribute_points')}
    except Exception:                                        # noqa: BLE001
        return {}


_REAL_ARGV = list(sys.argv)                 # ★ 必须在改写 sys.argv **之前**备份：
                                            #   否则 argparse 会去解析 gd_opt 的参数
sys.argv = ['gd_opt.py', '--goal', 'worst', '--beam', '40', '--restart', '1',
            '--min-leg', '0', '--max-leg', '12', '--min-epic', '0', '--max-epic', '12',
            '--out', '_report_boot.json', '--quiet']

# ⚠ import gd_opt 会跑一整轮**启动搜索**。GD_FULL / GD_FLOOR 这类**硬约束**在小束宽下
#   很容易「剪到无解」而 SystemExit —— 报告只需要 ev()/contrib() 的打分函数，
#   与搜索无关，所以把硬约束摘掉再 import（报告口径不受影响：抗性用 NEED，伤害用 W_DMG）。
for _k in ('GD_FULL', 'GD_FLOOR'):
    os.environ.pop(_k, None)

_ARGV = _REAL_ARGV                          # import 完必须还原，否则下面的 argparse 会炸
from . import opt as O
sys.argv = _ARGV
from . import gear as G
from . import savemap as M
from . import req as R

RAR_CN = {'Rare': '绿', 'Epic': '蓝', 'Legendary': '紫', 'Common': '白', None: '—'}
TAGS = G.load_tags()
IT = G.load_items()
FKEYS_CN = {'OA': '进攻能力(OA)', 'pierce': '穿刺伤害%', 'phys': '物理/主伤害%',
            'bleed': '流血伤害%', 'total': '总伤害%', 'crit': '暴击伤害%',
            'spd': '攻击速度%', 'fpie': '穿刺转化/附加', 'life': '生命', 'armor': '护甲'}


def zh(gid):
    if not gid:
        return '—'
    o = IT.get(gid) or {}
    for key in ('a', 'd', 'n'):
        t = o.get(key)
        if isinstance(t, str) and TAGS.get(t):
            return clean(TAGS[t])
    try:
        return clean(M.zh(gid) or gid)
    except Exception:
        return gid


def tag_of(t):
    return TAGS.get(t, t) if isinstance(t, str) else t


def clean(s):
    """去掉 GD 本地化标签里的颜色前缀（`^k邪恶铭文` → `邪恶铭文`）。"""
    return re.sub(r'\^[a-z]', '', s) if isinstance(s, str) else s


# ---------------------------------------------------------------- 存档侧
def load_char(name):
    """读存档拿「存档属性 + 精通等级」，用于算可穿性。"""
    from . import _legacyenv as ENV
    from .save import core as S
    save, _ = ENV.find_save_dir()
    if not save:
        return None
    import glob
    for p in glob.glob(os.path.join(save, 'main', '*', 'player.gdc')):
        if os.path.basename(os.path.dirname(p)).lstrip('_').lower() != name.lstrip('_').lower():
            continue
        d = S.parse(p)
        b2 = d['block_map'].get(2) or {}
        b8 = d['block_map'].get(8) or {}
        mast = {}
        for s in b8.get('skills', []):
            if 'classtraining' in s['skill'] and s['level'] > 0:
                # _classtraining_classNN → NN
                stem = s['skill'].split('/')[-1]
                try:
                    num = int(stem.replace('_classtraining_class', '').replace('.dbr', ''))
                except ValueError:
                    continue
                mast[num] = s['level']
        return {'attrs': {k: b2.get(k, 0) for k in ('physique', 'cunning', 'spirit')},
                'mastery': mast, 'level': d['level']}
    return None


def sam_panel(char_info, recs):
    """面板（无新装备时）= 存档值 + 精通 + 新装备自带属性（平值/百分比）。"""
    import gd_alloc as A
    base = char_info['attrs']
    mast = A.mastery_attr(sorted(char_info['mastery'].items()))
    add = {'physique': 0.0, 'cunning': 0.0, 'spirit': 0.0}
    pct = {'physique': 0.0, 'cunning': 0.0, 'spirit': 0.0}
    flat_key = {'physique': 'characterPhysique', 'cunning': 'characterDexterity',
                'spirit': 'characterIntelligence'}
    pct_key = {'physique': 'characterPhysiqueModifier', 'cunning': 'characterDexterityModifier',
               'spirit': 'characterIntelligenceModifier'}
    try:
        db = A._db()
    except Exception:
        db = None
    for rec in recs.values():
        if not rec or not db:
            continue
        f = db.fields(rec) or {}
        for k, fk in flat_key.items():
            v = f.get(fk)
            if v and isinstance(v[0], (int, float)):
                add[k] += float(v[0])
        for k, fk in pct_key.items():
            v = f.get(fk)
            if v and isinstance(v[0], (int, float)):
                pct[k] += float(v[0])
    return {k: round((base[k] + mast[k] + add[k]) * (1 + pct[k] / 100.0)) for k in base}, mast


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('plan')
    ap.add_argument('--char', default='Sam')
    ap.add_argument('--archetype', default=os.environ.get('GD_ARCHETYPE', ''))
    ap.add_argument('--level', type=int, default=int(os.environ.get('GD_MAX_ILVL', '63')))
    ap.add_argument('--title', default='')
    ap.add_argument('--out', default='')
    # ★ 2026-09-20 新：把 `tools/plan_audit.py` 的体检段（真实 DPS 三层口径 /
    #   命中暴击 / 主输出 / 属性重排）**并进报告**。
    #   在这之前它只能事后手工贴 —— 那份 md 一重生成就丢，是**静默**的信息损失。
    #   口径敏感性用 `--audit-proj 1,4,8` 打开（`projectile_hits` 是场景假设）。
    ap.add_argument('--audit', action='store_true',
                    help='追加真实 DPS / 命中 / 属性重排体检段')
    ap.add_argument('--audit-proj', default='', help='体检的口径对照，如 1,4,8')
    a = ap.parse_args()

    sol = {k: tuple(v) for k, v in json.load(open(a.plan, encoding='utf-8')).items()}
    r, o = O.ev(sol)
    rt = [r.get(t, 0.0) for t in O.TYPES]
    ot = {k: o[k] for k in O.FKEYS}
    sk = sum(O.contrib(g)[2] for s in sol for g in sol[s] if g)
    rar = [O._rar(sol[s][0]) for s in sol]
    gr, ep, lg = rar.count('Rare'), rar.count('Epic'), rar.count('Legendary')
    cov = sum(min(rt[i], O.NEED[O.TYPES[i]]) for i in range(len(O.TYPES)))
    need_tot = sum(O.NEED[t] for t in O.TYPES)
    over = sum(max(0.0, rt[i] - O.NEED[O.TYPES[i]]) for i in range(len(O.TYPES)))
    dmg = sum(ot[k] * O.W_DMG.get(k, 0.0) for k in O.FKEYS)
    n_slot = len(sol)

    recs, req_items = {}, {}
    for slot, row in sol.items():
        recs[row[0]] = M.resolve(IT.get(row[0]) or {})[0]
    pn = R.panel_needed(list(recs.keys()), recs)
    for gid, rr in (pn.get('items') or {}).items():
        req_items[gid] = rr

    L = []
    # ★ 2026-09-20 修：原来把「不含武器」**写死**在标题/表头里。
    #   实际 `autobuild --with-weapon` 会把主手/副手一起优化，报告却仍然说
    #   「不含武器、槽位 12」—— 用户看到的就是一份**自相矛盾**的报告。
    #   改为按方案里**真的有**主手/副手来判断。
    #   注：`slot_ok` 的空槽用 `(None,None,None,None,None)` 占位，b 为空 ⇒ 视为没换武器。
    has_wpn = any(s in ('主手', '副手') and (sol[s][0] if sol.get(s) else None)
                  for s in sol)
    wpn_note = '含主手/副手' if has_wpn else '不含武器'
    title = a.title or ('%s · %d 级 装备方案（满抗 · 追高伤害 · %s）'
                        % (a.char, a.level, wpn_note))
    L.append('# %s' % title)
    L.append('')
    L.append('> 生成 %s ｜ 角色 **%s** lv%d ｜ 流派 `%s` ｜ 槽位 **%d**（%s）'
             % (datetime.datetime.now().strftime('%Y-%m-%d %H:%M'), a.char, a.level,
                a.archetype or '(默认)', n_slot, wpn_note))
    L.append('>')
    L.append('> **伤害代理** 只含装备的输出属性加权和%s，用于横向排序，不等于面板 DPS。'
             % ('' if has_wpn else '（不含武器）'))
    L.append('')
    L.append('## 一句话结论')
    L.append('')
    # ★ 2026-09-20 修：这两行原来是**写死**的 —— 一份覆盖率只有 75.3% 的方案
    #   也会打印「9 项全部封顶（无缺口）」。必须按实测覆盖率算。
    _full = [t for i, t in enumerate(O.TYPES)
             if O.NEED[t] and rt[i] >= O.NEED[t] - 1e-9]
    _gap = [(t, O.NEED[t] - rt[i]) for i, t in enumerate(O.TYPES)
            if O.NEED[t] and rt[i] < O.NEED[t] - 1e-9]
    if not _gap:
        L.append('- 抗性覆盖 **%d / %d = %.1f%%**，终极难度下 **%d 项全部封顶**（无缺口）。'
                 % (cov, need_tot, 100.0 * cov / need_tot, len(_full)))
    else:
        L.append('- 抗性覆盖 **%d / %d = %.1f%%**（**未满**）：封顶 %d 项，'
                 '缺口 %d 项 —— %s。'
                 % (cov, need_tot, 100.0 * cov / need_tot, len(_full), len(_gap),
                    '、'.join('%s 差 %.0f' % (t, d) for t, d in _gap)))
    if over > 0:
        L.append('- 溢出 **%.0f 点**（80%% 是硬上限，溢出即浪费）。' % over)
    else:
        L.append('- 无多余溢出（80% 是硬上限，超出即浪费）。')
    L.append('- 同口径 **伤害代理 %.1f** ｜ 装备自带技能折算 **%.1f** ｜ 稀有度 绿%d / 蓝%d / 紫%d。'
             % (dmg, sk, gr, ep, lg))
    L.append('')

    # ---- 装备表
    L.append('## 一、装备（%d 槽，%s）' % (n_slot, wpn_note))
    L.append('')
    L.append('| 槽位 | 装备 | 稀有 | 词缀(前/后) | 镶嵌 | 附魔 | 自带技能 |')
    L.append('|---|---|---|---|---|---|---|')
    skills_rows = []
    for slot in sol:
        b, c, aug, pre, suf = (list(sol[slot]) + [None] * 5)[:5]
        sname = []
        for gid in (b, c, aug):
            oo = IT.get(gid) or {}
            for n in (1, 2, 3, 4):
                t = oo.get('augmentSkillName%d' % n)
                lv = oo.get('augmentSkillLevel%d' % n)
                if isinstance(t, str) and t in O.SKILL_W and isinstance(lv, (int, float)):
                    sname.append('%s +%s' % (clean(tag_of(t)), lv))
                    skills_rows.append('| %s | %s | %s | +%s |' % (slot, zh(gid), clean(tag_of(t)), lv))
        L.append('| %s | %s | %s | %s / %s | %s | %s | %s |' % (
            slot, zh(b), RAR_CN.get(O._rar(b), '?'),
            zh(pre) if pre else '—', zh(suf) if suf else '—',
            zh(c), zh(aug), '、'.join(sname) or '—'))
    L.append('')

    # ---- 抗性表
    L.append('## 二、抗性（终极难度：上排需 130、下排需 105 才能显示 80%）')
    L.append('')
    L.append('| 抗性 | 装备原值 | 终极显示 | 需求 | 状态 |')
    L.append('|---|---|---|---|---|')
    for i, t in enumerate(O.TYPES):
        nd = O.NEED[t]
        v = rt[i]
        disp = 80 if (nd and v >= nd) else (round(v * 80.0 / nd) if nd else '—')
        L.append('| %s | %.0f | %s | %s | %s |' % (
            t, v, ('%s%%' % disp), (nd or '—'),
            '★封顶' if nd and v >= nd else ('—' if not nd else '差%.0f' % (nd - v))))
    L.append('')

    # ---- ★★ 抗性来源分解（2026-09-20 并入全链路）
    #   用户口径：「抗性溢出这么多」要能一眼看出**谁供的、能不能换成伤害**。
    #   逻辑在 `gd/resaudit.py`（单一真源，`tools/plan_audit.py` 与独立 CLI 也调它）；
    #   标题由 `render_md` 自己发，这里不要再加一行（会重复）。
    try:
        from . import resaudit as _RA
        L.extend(_RA.render_md(_RA.per_slot_from_plan(sol, items=IT), items=IT,
                               types=O.TYPES, need=O.NEED, plan=sol,
                               top=3, detail=3, name_of=zh))
    except Exception as _e:                                      # noqa: BLE001
        L.append('### 抗性来源分解（为什么会溢出）')
        L.append('')
        L.append('> ⚠ 生成失败：%s: %s' % (type(_e).__name__, _e))
        L.append('')

    # ---- 输出属性
    L.append('## 三、输出属性（装备贡献）')
    L.append('')
    L.append('| 维度 | 数值 | 权重 |')
    L.append('|---|---|---|')
    for k in O.FKEYS:
        L.append('| %s | %.0f | %.2f |' % (FKEYS_CN.get(k, k), ot[k], O.W_DMG.get(k, 0.0)))
    L.append('')
    L.append('> 加权和 = **伤害代理 %.1f**；再加 装备技能折算（SKILL_K=%.1f × %.1f）得综合分 **%.1f**。'
             % (dmg, float(os.environ.get('GD_SKILL_K', '2.0')), sk, dmg + sk))
    if skills_rows:
        L.append('')
        L.append('**装备自带的技能加成（对本流派有效）**')
        L.append('')
        L.append('| 槽位 | 来源 | 技能 | 等级 |')
        L.append('|---|---|---|---|')
        L.extend(skills_rows)
    L.append('')

    # ---- 可穿性
    info = load_char(a.char)
    L.append('## 四、装备需求与可穿性')
    L.append('')
    if info:
        panel, mast = sam_panel(info, recs)
        L.append('| 槽位 | 装备 | 需体格 | 需狡诈 | 需精神 | 置信度 |')
        L.append('|---|---|---|---|---|---|')
        for slot in sol:
            gid = sol[slot][0]
            rr = req_items.get(gid) or {}
            L.append('| %s | %s | %s | %s | %s | %s |' % (
                slot, zh(gid), rr.get('physique') or 0, rr.get('cunning') or 0,
                rr.get('spirit') or 0, rr.get('confidence') or '—'))
        L.append('')
        L.append('- 需求上限：体格 **%s** ｜ 狡诈 **%s** ｜ 精神 **%s**（含 estimated 件 ±10%% 风险）'
                 % (pn.get('physique'), pn.get('cunning'), pn.get('spirit')))
        L.append('- %s 面板（存档 %s + 精通 %s + 本套装备自带）≈ **体格 %d / 狡诈 %d / 精神 %d**'
                 % (a.char,
                    '/'.join(str(info['attrs'][k]) for k in ('physique', 'cunning', 'spirit')),
                    '/'.join(str(int(mast[k])) for k in ('physique', 'cunning', 'spirit')),
                    panel['physique'], panel['cunning'], panel['spirit']))
        okp = panel['physique'] >= (pn.get('physique') or 0)
        okc = panel['cunning'] >= (pn.get('cunning') or 0)
        oks = panel['spirit'] >= (pn.get('spirit') or 0)
        L.append('- 结论：**%s**' % ('全部可穿 ✓' if (okp and okc and oks)
                                     else '有缺口 ✗（力%s 敏%s 智%s）'
                                     % ('✓' if okp else '✗', '✓' if okc else '✗', '✓' if oks else '✗')))
    else:
        L.append('（未能读取角色存档，跳过可穿性核算）')
    L.append('')

    # ---- 技能点（三要素之二）
    #   ★ 与伤害模型**同源**：读 `gd.dps.load_char` 实际使用的加点
    #     （存档加点，或 `GD_SKILL_JSON` 注入的形态加点）。
    L.append('## 五、技能点（%d 级）' % a.level)
    L.append('')
    try:
        from gd import alloc as _A
        from gd import dps as _D
        _c = _D.load_char(a.char, '', True)
        _lv = _c.get('level') or a.level
        _bud = _A.skill_budget(_lv)
        _sk = {k: v for k, v in (_c.get('skills') or {}).items() if v > 0}
        # `records/skills/default/*` 是**免费动作条**（左键/移动/喝药），不花技能点
        _paid = {k: v for k, v in _sk.items()
                 if not k.startswith('records/skills/default/')
                 and 'devotion' not in k}
        _mast = {k: v for k, v in _paid.items() if '_classtraining_' in k}
        _oth = {k: v for k, v in _paid.items() if '_classtraining_' not in k}
        _used = sum(_paid.values())
        _aw = earned_points(a.char)          # ★ 存档点数实况（不是等级理论值）
        L.append('- 预算 **%d** 点（等级 %d 累计 %d + 任务奖励 %d）'
                 % (_bud, _lv, _bud - _A.TASK_SKILL_POINTS, _A.TASK_SKILL_POINTS))
        L.append('- 已投入 **%d** 点 = 精通条 %d + 技能 %d'
                 % (_used, sum(_mast.values()), sum(_oth.values())))
        if _aw:
            _tot_sk = _used + int(_aw.get('skill_points') or 0)
            L.append('- 存档实况：**未分配 %s** ｜ 池子合计 **%d**（已投 %d）'
                     % (_aw.get('skill_points'), _tot_sk, _used))
        _d = _used - _bud
        if _d > 0:
            _tot = _used + int(_aw.get('skill_points') or 0)
            L.append('- ⚠ **超预算 %d 点**：存档池子 **%d 点** > 等级 %d 的合法预算 %d。'
                     '成因是等级被调过 / 训练器 / mod 改过给点表。'
                     % (_d, _tot, _lv, _bud))
            L.append('  ⇒ **本报告按存档现状加点算**（数字与你的角色一致）。'
                     '要一份**等级合法**的加点：`tools/make_alloc.py <形态> %d`。' % _lv)
        elif _d < 0:
            L.append('- 余量 **%d** 点**未分配**（可以再点满某个技能）。' % (-_d))
        else:
            L.append('- 点数**恰好用完**。')
        L.append('')
        L.append('**精通条**')
        L.append('')
        L.append('| 职业 | 精通条 | 等级 |')
        L.append('|---|---|---|')
        for k, v in sorted(_mast.items()):
            L.append('| %s | `%s` | %d |'
                     % (os.path.basename(k).replace('_classtraining_', '')
                        .replace('.dbr', ''), os.path.basename(k), v))
        L.append('')
        L.append('**投入最多的技能（前 15）**')
        L.append('')
        L.append('| 技能 | 记录名 | 等级 |')
        L.append('|---|---|---|')
        for k, v in sorted(_oth.items(), key=lambda x: -x[1])[:15]:
            L.append('| %s | `%s` | %d |'
                     % (os.path.basename(k).replace('.dbr', ''),
                        k.split('records/skills/')[-1], v))
        L.append('')
        if _c.get('skill_plus'):
            _sp = sorted(((_v, _k) for _k, _v in _c['skill_plus'].items()), reverse=True)
            L.append('- 上面是**裸投入**；装备还会额外给 %d 个技能加等级，'
                     '其中最高的是 %s（+%d）。'
                     % (len(_sp), os.path.basename(_sp[0][1]).replace('.dbr', ''), _sp[0][0]))
            L.append('')
    except Exception as _e:                                     # noqa: BLE001
        L.append('（技能点核算失败：%s: %s）' % (type(_e).__name__, _e))
        L.append('')

    # ---- 星座 / 虔诚（三要素之三）
    L.append('## 六、星座（虔诚）')
    L.append('')
    L.append('> ★ **口径的边界，必须先看**：优化器判定「满抗」用的是 `NEED`'
             '（上排 130 / 下排 105），而它只累加**装备槽**的抗性'
             '（`gd/opt.py::ev` 只做 gear 的 `res_of`）。'
             '也就是说本报告的 **满抗 = 仅靠装备也满**，是**保守**口径；'
             '星座 / 被动技能给的那部分抗性属于**额外余量**，'
             '不要理解成「星座给抗性所以装备可以少堆」。')
    L.append('')
    try:
        from gd import alloc as _A
        from gd import devotion as _DEV
        from gd import rotation as _R
        from gd import dps as _D2
        _c2 = _D2.load_char(a.char, '', True)
        _lv2 = _c2.get('level') or a.level
        _cap2 = _A.devotion_budget(_lv2)                 # 等级上限（lv73 → 55）
        _aw2 = earned_points(a.char)                     # ★ 存档实况
        _earn = _aw2.get('total_devotion_points')
        _left = _aw2.get('devotion_points')
        # ★★ 预算 = min(等级上限, 已解锁)。**只按等级上限给建议会给出点不出来的方案**：
        #    实测 lv73 上限 55，但角色只解锁 36（= 已投 32 + 未投 4）⇒ 51 点方案不可实现。
        # ★★ 预算 = min(等级上限, **池**)。**池 = 已点亮节点数 + 未分配**
        #    （= `total_devotion_points` + `devotion_points`：前者是**已花**、后者是**未分配**）。
        #    旧写法 `min(cap, total_devotion_points)` 把「已花」当池 ⇒ **少算未分配的点**
        #    （Sam 实测 36 vs 真值 **40**），给出的方案会白白少花几点。
        #    ⚠ 口径更正（2026-09-21）：**proc / Celestial Power 节点也要花 1 点**
        #    （官方指南：「有些星更亮，授予 Celestial Power」——亮星本身就是要花点买的星）。
        #    三重对账一致：点亮节点 36 == 存档已花 36 == 该 8 颗星座节点数合计 36。
        _bud2 = min(_cap2, int(_earn) + int(_left or 0)) if _earn else _cap2
        _cur = {r: lv for r, lv in (_c2.get('skills') or {}).items()
                if 'devotion' in r and lv > 0}
        _dc = _R.devotion_contrib(_cur, _c2['db'])
        L.append('### 6.1 现状（存档）')
        L.append('')
        L.append('- 已点 **%d / %d** 点（%d 个星节点）%s'
                 % (sum(_cur.values()), _bud2, len(_cur),
                    '｜ 未分配 **%s** 点' % _left if _left is not None else ''))
        if _earn:
            L.append('- 本方案按**已解锁 %d 点**规划（等级 %d 的理论上限是 %d 点，'
                     '差的 %d 点要去打神龛）。'
                     % (_bud2, _lv2, _cap2, _cap2 - _bud2))
        L.append('- 进攻向加成合计 **%.0f%%**（pct 全类型求和）｜ 攻速 %+.0f%% ｜ 独立倍率 %+.0f%%'
                 % (sum((_dc.get('pct') or {}).values()),
                    _dc.get('spd') or 0.0, _dc.get('mult') or 0.0))
        _r0 = _dc.get('res') or {}
        L.append('- 抗性贡献：%s'
                 % ('、'.join('%s %+.0f' % (k, v)
                              for k, v in sorted(_r0.items(), key=lambda x: -abs(x[1])))
                    or '无'))
        L.append('')
        _top = sorted(((k, v) for k, v in (_dc.get('pct') or {}).items() if v),
                      key=lambda x: -abs(x[1]))[:8]
        if _top:
            L.append('- 主要增伤项：%s' % '、'.join('%s +%.0f' % (k, v) for k, v in _top))
            L.append('')

        L.append('### 6.2 建议（按星座整体 + 亲和力自洽重新分配）')
        L.append('')
        _prop = _DEV.select_coherent(a.archetype or 'wolf_nightblade', budget=_bud2)
        _dp = _R.devotion_contrib({r: 1 for r in _prop['records']}, _c2['db'])
        L.append('- 用 **%d / %d** 点 ｜ %d 个星座 ｜ 亲和力自洽：**%s**'
                 % (_prop['used'], _bud2, len(_prop['picks']),
                    '✓' if _prop['coherent'] else '✗'))
        L.append('- 进攻向加成合计 **%.0f%%**（现状 %.0f%%）｜ 攻速 %+.0f%% ｜ 独立倍率 %+.0f%%'
                 % (sum((_dp.get('pct') or {}).values()),
                    sum((_dc.get('pct') or {}).values()),
                    _dp.get('spd') or 0.0, _dp.get('mult') or 0.0))
        _r1 = _dp.get('res') or {}
        _dk = sorted(set(_r0) | set(_r1))
        if _dk:
            L.append('- 抗性贡献变化：%s'
                     % '、'.join('%s %+.0f→%+.0f' % (k, _r0.get(k, 0.0), _r1.get(k, 0.0))
                                 for k in _dk if abs(_r1.get(k, 0) - _r0.get(k, 0)) > 0.5))
        L.append('')
        L.append('| 星座 | 分 | 星节点数 | 所需亲和力 |')
        L.append('|---|---|---|---|')
        for _p in _prop['picks']:
            L.append('| %s | %.0f | %d | %s |'
                     % (_p['name'] or str(_p['idx']), _p['score'], len(_p['stars']),
                        _p['affinity'] or '—'))
        L.append('')
        L.append('- 亲和力明细：`%s`' % _prop['affinity'])
        L.append('')
        L.append('> ⚠ `select_coherent` 是**近似模型**（星座整体选择 + 亲和力累加），'
                 '与游戏真实的连线顺序/前置规则仍有差距 —— 报「不满足」基本一定有问题，'
                 '报「满足」仍需在游戏里点一次确认。')
        L.append('')
    except Exception as _e:                                     # noqa: BLE001
        L.append('（星座核算失败：%s: %s）' % (type(_e).__name__, _e))
        L.append('')

    # ---- 落档
    L.append('## 七、落档（需要时再做）')
    L.append('')
    L.append('```bash')
    L.append('# 1) 导出成 gd_build 方案（紫/蓝不写词缀，附记录名反查校验）')
    L.append('python gd_plan_export.py %s %s plans/%s_build.json' % (a.char, a.plan, a.char))
    L.append('# 2) 写入前：游戏内关 Cloud Saving + 完全退出游戏')
    L.append('python gd_verify.py --preflight')
    L.append('# 3) 应用')
    L.append('python gd_build.py plan  plans/%s_build.json' % a.char)
    L.append('python gd_build.py apply plans/%s_build.json' % a.char)
    L.append('# 4) 复检')
    L.append('python gd_verify.py %s ／ python gd_lint.py %s' % (a.char, a.char))
    L.append('```')
    L.append('')

    text = '\n'.join(L)

    # ---- 体检段（真实 DPS / 命中 / 主输出 / 属性重排）并进报告
    if a.audit:
        try:
            _tools = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), 'tools')
            if _tools not in sys.path:
                sys.path.insert(0, _tools)
            import plan_audit as PA                              # noqa: E402
            _blk = PA.render_md(PA.analyze(a.char, a.plan, arch=a.archetype,
                                           attr='auto', proj=a.audit_proj))
            _anchor = '## 一、装备'
            text = (text.replace(_anchor, _blk + '\n' + _anchor, 1)
                    if _anchor in text else text.rstrip() + '\n\n' + _blk)
        except Exception as _e:                                  # noqa: BLE001
            text = text.rstrip() + '\n\n> ⚠ 体检段生成失败：%s: %s\n' % (
                type(_e).__name__, _e)

    out = a.out or os.path.splitext(a.plan)[0] + '_装备报告.md'
    open(out, 'w', encoding='utf-8').write(text)
    print(text)
    print('\n→ %s' % out)


if __name__ == '__main__':
    raise SystemExit(main())
