# -*- coding: utf-8 -*-
"""gd/dmgcycle.py —— **伤害循环文档**（每个 BD 一份，必带）

用户口径（2026-09-20 原文，逐字）：
  「伤害循环中的技能 用这种格式给我展示；每一条的伤害是哪里来的 —— 比如说
    武器多少、套装多少、技能多少、星座多少、乘区怎么叠加这些；出单次伤害与
    每秒伤害；以及该技能能不能触发其他技能（一些衣服上的技能或者是镶嵌物的）。
    尽可能与游戏保持一致。**每次出新 db 的时候必须带一份专门的伤害循环文档**。」

★ 与 `tools/plan_audit.py` 的分工（不要重复造轮子）：
    `plan_audit`  = **体检**（四层口径 / 属性重排 / 口径敏感性）
    `dmgcycle`    = **档案**（逐技能 · 逐伤害类型 · 逐来源 · 逐乘区 · 逐触发关系）
  两者都消费同一个 `plan_dps.dps_of()`，所以数值不可能分叉。

本文件只做三件事：**取数 → 分账 → 渲染**。所有数值都来自既有模块：
    `gd/rotation.py`   `rows[].origins`（逐来源基础值）/ `pct_parts`（乘区分层）
    `gd/procs.py`      物品技能与触发关系
    `gd/rr.py`         敌方抗性乘区（`vs_rows`）
    `gd/combat.py`     命中/暴击期望与护甲减免

口径层（与报告其余部分一致，**绝不混用**）：
    面板  `dps`        每击平均 × 频率（DoT 按覆盖率折算）—— 与游戏提示框同口径
    实战  `dps_real`   再 × 命中/暴击期望倍率
    对怪  `dps_vs`     再 × 敌方减抗乘区（**优化器目标**）
    过甲  `dps_final`  再 × 护甲减免（仅物理直伤；穿刺/元素/流血不过甲）
"""
from __future__ import annotations

import os

from . import procs as _PR

# ------------------------------------------------------------------ 展示表

# 游戏官方简中的伤害类型名（与内部 `TYPE_ZH` 只差三条，提示框按**游戏口径**显示：
#   · trauma     内部 `内部创伤` → 游戏 `创伤`
#   · poison     内部 `毒素`     → 游戏直伤写作 `毒酸`（与 `gd/rr.py` 的抗性桶同名）
#   · poisondot  内部 `毒素持续` → 游戏 `毒素`（截图里的「毒素 100」就是它）
TIP_ZH = {
    'physical': '物理', 'pierce': '穿刺', 'fire': '火焰', 'cold': '冰冷',
    'lightning': '闪电', 'poison': '毒酸', 'acid': '酸液', 'vitality': '活力',
    'aether': '以太', 'chaos': '混乱', 'bleeding': '流血', 'burn': '燃烧',
    'frostburn': '霜燃', 'electrocute': '电击', 'decay': '活力衰减',
    'trauma': '创伤', 'poisondot': '毒素',
}

# 直伤在前、DoT 在后 —— 与游戏提示框的排布一致
_DIRECT_ORDER = ('physical', 'pierce', 'fire', 'cold', 'lightning', 'poison',
                 'acid', 'vitality', 'aether', 'chaos')
_DOT_ORDER = ('bleeding', 'trauma', 'burn', 'frostburn', 'electrocute',
              'decay', 'poisondot')


def _tip_zh(t):
    return TIP_ZH.get(t, t)


def _i(v):
    """游戏提示框的整数渲染（**无千分位**，与游戏逐字一致）。"""
    try:
        return '%d' % round(float(v))
    except (TypeError, ValueError):
        return str(v)


def _fmt_pct(v):
    """抗性百分比：`None` ⇒ `—`（该类型没有敌方数据时）。"""
    if v is None:
        return '—'
    try:
        return '%g' % round(float(v), 1)
    except (TypeError, ValueError):
        return str(v)


def _disp_w(s):
    """字符串的**显示宽度**（东亚宽字符算 2 列）—— 提示框对齐用。"""
    return sum(2 if ord(c) > 0x2E80 else 1 for c in str(s))


def _pad(s, w):
    s = str(s)
    return s + ' ' * max(1, w - _disp_w(s))


def tip_block(rows):
    """**游戏格式**的伤害明细块（用户截图的那个格式）。

    规则（对齐游戏提示框）：
      · 直伤显示 `min - max`；DoT 显示**每秒**单值（dbr 里 DoT 本来就是单值）
      · 整数、**无千分位**
      · 直伤在前、DoT 在后
      · 顺序 = 伤害类型表顺序（物理 → 穿刺 → 元素 → … → 持续伤害）
      · 类型名左对齐（按显示宽度补空格，中文算 2 列）
    """
    idx = {t: i for i, t in enumerate(_DIRECT_ORDER + _DOT_ORDER)}
    rs = sorted(rows, key=lambda r: idx.get(r['type'], 99))
    out = []
    for r in rs:
        nm = _pad(_tip_zh(r['type']), 10)
        if r.get('is_dot'):
            _d = (' /秒（持续 %ss）' % r['dur']) if r.get('dur') else ' /秒'
            out.append('%s%s%s' % (nm, _i(r['avg']), _d))
        else:
            out.append('%s%s - %s' % (nm, _i(r['min']), _i(r['max'])))
    return out


# ------------------------------------------------------------------ 分账

_SRC_ORDER = ('技能', '武器', '光环/被动', '装备', '套装', '星座')


def src_rows(rows):
    """**每一条伤害从哪来** —— 逐伤害类型 × 逐来源（**加成前**的基础值）。

    ★ 为什么可信：`gd.rotation.final_report` 在算这一击时，对每个来源
      **各自**跑了一遍同参数的转化链（官方 Step 3.1），并断言
      「五源之和 == 100%武器攻击基础 + 技能本体」（实测 43 行 0 不一致）。
    """
    idx = {t: i for i, t in enumerate(_DIRECT_ORDER + _DOT_ORDER)}
    out = []
    for r in sorted(rows, key=lambda x: idx.get(x['type'], 99)):
        o = r.get('origins') or {}
        row = {'type': r['type'], 'zh': _tip_zh(r['type']),
               'is_dot': bool(r.get('is_dot')), 'total': r.get('weapon', [0, 0]),
               'srcs': {}}
        tot = 0.0
        for k in _SRC_ORDER:
            v = o.get(k)
            if v and (v[0] or v[1]):
                a = (float(v[0]) + float(v[1])) / 2.0
                row['srcs'][k] = a
                tot += a
        row['sum'] = tot
        out.append(row)
    return out


def pct_rows(rows):
    """**乘区怎么叠加** —— 逐伤害类型的 % 加成**分层**（谁给了多少）。"""
    idx = {t: i for i, t in enumerate(_DIRECT_ORDER + _DOT_ORDER)}
    out = []
    for r in sorted(rows, key=lambda x: idx.get(x['type'], 99)):
        pp = r.get('pct_parts') or {}
        base = (float(r.get('weapon', [0, 0])[0]) + float(r.get('weapon', [0, 0])[1])) / 2.0
        base += (float(r.get('skill', [0, 0])[0]) + float(r.get('skill', [0, 0])[1])) / 2.0
        out.append({'type': r['type'], 'zh': _tip_zh(r['type']),
                    'base': base, 'pct': float(r.get('pct') or 0.0),
                    'mult': float(r.get('mult') or 1.0), 'parts': pp,
                    'per_hit': (float(r['min']) + float(r['max'])) / 2.0})
    return out


# ------------------------------------------------------------------ 渲染

def render(base, char='Sam', arch='', plan_path='', db=None, items=None,
           plan=None, enemy_label='', argv='',
           crit_chance=None, has_shield=False):
    """产出**伤害循环文档**（markdown 字符串）。"""
    hits = base.get('hits') or {}
    loop = base.get('loop') or []
    name_of = {r['记录']: r['技能'] for r in loop}

    def _nm(r):
        """技能记录 → 中文名（回退链：`loop` 的中文名 → 记录里的 `name`/`tag` → 文件名）。

        ⚠ `tag` 是本地化键（`tagGDX3Class10SkillName04B`），`name` 才是中文名 ——
        顺序不能反，否则报告里会冒出 tag 串。
        """
        h = hits.get(r) or {}
        _c = name_of.get(r) or h.get('name') or h.get('tag')
        if _c and not str(_c).startswith('tag'):
            return str(_c)
        return os.path.basename(str(r)).replace('.dbr', '') or str(r)
    rot = base.get('rotation') or {}
    default_rec = rot.get('default')
    procs_recs = list(rot.get('procs') or [])
    cd_recs = list(rot.get('cooldowns') or [])
    wps_blocked = bool(rot.get('wps_blocked'))

    hit = base.get('hit') or {}
    if crit_chance is None:
        crit_chance = (hit.get('crit_chance') or 0.0) * 100.0
    vs_rows = {r.get('bucket'): r for r in (base.get('vs_rows') or [])}

    L = []
    A = L.append

    # ---------------------------------------------------------- 抬头
    _lab = ''
    try:
        from .rotation import _load as _ld
        _lab = ((_ld('archetypes.json', {}) or {}).get(arch) or {}).get('label') or ''
    except Exception:                                        # noqa: BLE001
        _lab = ''
    label = _lab or arch or '—'
    A('# 伤害循环文档 · %s' % (label,))
    A('')
    A('> **每个 BD 必带**（用户口径 2026-09-20）。本文档回答四件事：')
    A('> ① 每一条伤害**从哪来**（武器 / 光环被动 / 装备 / 套装 / 星座 / 技能本体）；')
    A('> ② **乘区怎么叠**（各来源给了多少 %、独立乘区、转化、穿甲、抗性、护甲）；')
    A('> ③ **单次伤害与每秒伤害**（面板 / 实战 / 对怪过甲四层口径）；')
    A('> ④ 该技能**能触发哪些装备 / 镶嵌物上的技能**。')
    A('')
    A('- 角色 **%s** ｜ 流派 `%s` ｜ 方案 `%s`' % (char, label, plan_path or '(存档装备)'))
    A('- 敌方档 **%s** ｜ 面板攻速 **%s 下/秒**（已含装备/技能/星座的攻速加成）'
      % (enemy_label or (base.get('enemy') or {}).get('spec') or '(默认)',
         base.get('aps')))
    A('- 命中/暴击：OA **%s** ｜ 敌方 DA **%s** ｜ PTH **%s** ｜ 命中 **%s%%** ｜ '
      '暴击 **%s%%** ｜ 期望倍率 **%s**'
      % (base.get('oa'), base.get('da'), base.get('pth'),
         round((hit.get('hit_chance') or 0) * 100, 1),
         round((hit.get('crit_chance') or 0) * 100, 2),
         round(float(hit.get('expected') or 1.0), 4)))
    A('- 四层口径：**面板 %s** → 实战（含命中）**%s** → 对怪（含减抗）**%s** → '
      '过甲 **%s**'
      % (_i(base.get('dps_panel')), _i(base.get('dps_real')),
         _i(base.get('dps_vs')), _i(base.get('dps_final'))))
    if argv:
        A('- 生成命令 `%s`' % argv)
    A('')

    # ---------------------------------------------------------- 0 总览
    A('## 0. 循环总览')
    A('')
    A('| 角色 | 技能 | 等级 | 权重/冷却 | 占比 | 频率 | **单次（面板）** | '
      '每秒（面板） | 每秒（实战） | 每秒（过甲） |')
    A('|---|---|---|---|---|---|---|---|---|---|')
    _order = ([default_rec] if default_rec else []) + procs_recs + cd_recs
    _seen = set()
    for rec in _order:
        if rec in _seen:
            continue
        _seen.add(rec)
        h = hits.get(rec)
        if not h:
            continue
        role = ('**默认攻击**（左键）' if rec == default_rec
                else ('武器池（WPS）' if rec in procs_recs
                      else ('冷却技能（%ss）' % h.get('cooldown') if h.get('cooldown')
                            else '攻击技能')))
        wt = (h.get('chance_weight') or 0)
        if wt:
            wcol = '权重 %s' % wt
        elif rec in cd_recs:
            wcol = '冷却 %ss' % h.get('cooldown')
        else:
            wcol = '—'
        _share = ('%.1f%%' % ((h.get('chance') or 0) * 100)
                  if (wt or rec == default_rec) else '—')
        A('| %s | %s | %s | %s | %s | %s 下/秒 | **%s - %s** | %s | %s | %s |'
          % (role, _nm(rec),
             h.get('level'), wcol, _share,
             h.get('freq') or 0, _i(h.get('min')), _i(h.get('max')),
             _i(h.get('dps')), _i(h.get('dps_real')), _i(h.get('dps_final'))))
    A('')
    A('- 挥击合计 **%s** ｜ 冷却技能合计 **%s**（面板口径）'
      % (_i(rot.get('dps_swing')), _i(rot.get('dps_cooldown'))))
    A('- 武器池总权重 **W = %s** ｜ 默认攻击权重 **%s** ｜ 掷骰分母 **%s**%s'
      % (rot.get('weight_total'), rot.get('default_weight'), rot.get('denom'),
         ' ｜ ⚠ **`wps_blocked`**：默认攻击无武器伤害 ⇒ 整池 WPS 不触发'
         if wps_blocked else ''))
    bench = rot.get('bench') or []
    if bench:
        A('- 落选的基础攻击（争同一个左键槽）：%s'
          % '、'.join('%s（单下 %s）' % (_nm(b.get('skill')), _i(b.get('avg')))
                      for b in bench[:5]))
    A('')

    # ---------------------------------------------------------- 逐技能
    A('## 1. 逐技能明细')
    A('')
    A('> 每一节的结构：**① 游戏格式明细 → ② 每条伤害的来源 → ③ 乘区叠加 → '
      '④ 单次/每秒 → ⑤ 可触发的其他技能**。')
    A('')
    _n = 0
    for rec in _order:
        h = hits.get(rec)
        if not h or not h.get('rows'):
            continue
        _n += 1
        nm = _nm(rec)
        role = ('默认攻击（左键）' if rec == default_rec
                else ('武器池（WPS）' if rec in procs_recs
                      else ('冷却技能' if h.get('cooldown') else '攻击技能')))
        A('### 1.%d %s' % (_n, nm))
        A('')
        A('`%s` ｜ 等级 **%s** ｜ %s ｜ 武器伤害 **%s%%** ｜ 频率 **%s 下/秒**'
          % (rec, h.get('level'), role, h.get('weapon_pct') or 0, h.get('freq') or 0))
        A('')

        # ① 游戏格式
        A('**① 游戏格式明细**（每击 · 面板口径 —— 与游戏提示框同口径，**不含敌方防御**）')
        A('')
        A('```')
        A(nm)
        for line in tip_block(h['rows']):
            A(line)
        A('```')
        A('')
        A('> 类型名按**游戏官方简中**：`毒酸` = 直伤毒（内部 `poison`）｜'
          '`毒素` = 持续毒（内部 `poisondot`）｜`创伤` = 内部 `内部创伤`。')
        A('> **DoT 行显示「每秒」值** —— 官方 l10n 原文：*「使用武器攻击每一击'
          '所造成的 3 秒内每秒流血伤害值，含加成」*（`tagCharStatsBleedAbsDmgInfo`）。'
          '持续时长逐条读自 `offensiveSlow<X>Duration*`。')
        A('')

        # ② 来源
        A('**② 每条伤害从哪来**（**加成前**的基础值 · 已含伤害转化与护甲穿透）')
        A('')
        A('| 伤害类型 | 合计 | %s |' % ' | '.join(_SRC_ORDER))
        A('|---|' + '---|' * (len(_SRC_ORDER) + 1))
        for r in src_rows(h['rows']):
            cells = []
            for k in _SRC_ORDER:
                v = r['srcs'].get(k)
                cells.append(_i(v) if v else '—')
            A('| %s%s | %s | %s |'
              % (r['zh'], '（DoT）' if r['is_dot'] else '', _i(r['sum']),
                 ' | '.join(cells)))
        A('')
        A('> `技能` = 技能本体平伤 ｜ `武器` = 100% 武器攻击基础 × 武器伤害% ｜ '
          '`光环/被动` = 增益技能给的平伤 ｜ `装备` = 非武器槽的全身平伤 ｜ '
          '`套装` = 套装加成给的平伤 ｜ `星座` = 虔诚节点给的平伤。')
        A('> ⚠ 上表是**加成前**；乘上 ③ 里的加成池之后才是 ① 的每击值。')
        A('')

        # ③ 乘区
        A('**③ 乘区叠加**（逐伤害类型）')
        A('')
        A('| 伤害类型 | 加成前基础 | **装备** | **技能** | **属性** | **星座** | '
          '套装 | 星座节点 | 合计 % | 独立乘区 | **每击（面板）** |')
        A('|---|---|---|---|---|---|---|---|---|---|---|')
        for r in pct_rows(h['rows']):
            p = r['parts']
            A('| %s | %s | %s | %s | %s | %s | %s | %s | **+%s%%** | ×%s | **%s** |'
              % (r['zh'], _i(r['base']), _i(p.get('装备')), _i(p.get('技能')),
                 _i(p.get('属性')), _i(p.get('星座')), _i(p.get('套装')),
                 _i(p.get('星座节点')), round(r['pct'], 1), r['mult'],
                 _i(r['per_hit'])))
        A('')
        A('> **叠加规则（官方顺序）**：')
        A('> ① **来源拆解** → ② **伤害转化**（技能专属 → modifier/transmuter → '
          '全局装备/buff/星座；**每个来源只转一次**）→ ③ **护甲穿透**（排最后，'
          '只作用于残余物理、且仅当该技能有武器伤害%）→ ④ **按最终类型取 % 加成**'
          '（加成池 = 装备 + 技能 + 属性 + 星座，**直接相加**）→ ⑤ **独立乘区**'
          '（`offensiveDamageMultModifier`，**相乘**）。')
        A('> 表中「每击（面板）」= 加成前基础 × (1 + 合计%/100) × 独立乘区。')
        A('')

        # ④ 单次 / 每秒（含抗性 / 护甲）
        tr = {r.get('type'): r for r in (h.get('type_rows') or [])}
        if tr:
            _he = float(hit.get('expected') or 1.0)
            A('**④ 单次与每秒**（含敌方抗性 · 减抗 · 护甲）')
            A('')
            A('| 伤害类型 | 每击（面板） | 技能内占比 | 敌方基础抗性 | 减抗后 | '
              '抗性倍数 | 护甲减免 | 综合倍数 | **单次（实战）** | 每秒（面板） | '
              '**每秒（实战）** | 每秒（过甲） |')
            A('|---|---|---|---|---|---|---|---|---|---|---|---|')
            _rows_sorted = sorted(
                h['rows'],
                key=lambda x: -float((tr.get(x['type']) or {}).get('dps_vs') or 0))
            for r in _rows_sorted:
                t = tr.get(r['type']) or {}
                ap = t.get('armor_pct')
                per_panel = float(r.get('avg') or 0)
                per_vs = (per_panel * _he * float(t.get('combat_mult') or 1.0))
                A('| %s | %s | %.1f%% | %s%% | %s%% | %s× | %s | %s× | **%s** | '
                  '%s | **%s** | %s |'
                  % (_tip_zh(r['type']), _i(per_panel),
                     (float(t.get('share') or 0) * 100),
                     _fmt_pct(t.get('res_base')), _fmt_pct(t.get('res')),
                     round(float(t.get('rr_mult') or 1.0), 3),
                     ('**不过甲**' if (ap is None or not ap) else '%.2f%%' % ap),
                     round(float(t.get('combat_mult') or 1.0), 3),
                     _i(per_vs),
                     _i(round(float(t.get('dps_panel') or 0.0))),
                     _i(round(float(t.get('dps_vs') or 0.0))),
                     _i(round(float(t.get('dps_final') or 0.0)))))
            A('')
            A('> 「单次（实战）」= 每击（面板）× 命中/暴击期望 **%s** × 抗性倍数 × '
              '护甲减免 ｜「每秒」= 该技能频率 **%s 下/秒** 摊分（DoT 已按覆盖率折算）。'
              % (round(_he, 4), h.get('freq') or 0))
            A('')

        # ⑤ 触发
        A('**⑤ 该技能能触发哪些装备 / 镶嵌物技能**')
        A('')
        _proc_section(A, base, db, items, plan, rec, h, crit_chance, has_shield)
        A('')

    # ---------------------------------------------------------- 全循环触发
    A('## 2. 全循环触发关系')
    A('')
    _all_procs(A, base, db, items, plan, _order, hits, _nm,
               crit_chance, has_shield)

    # ---------------------------------------------------------- 边界
    A('')
    A('## 3. 模型边界（如实登记，不装懂）')
    A('')
    gap = _PR.wps_gap(db, items, plan, hits) if (db is not None and items and plan) else {}
    if gap.get('items') and not gap.get('missing'):
        # ★ 2026-09-20 修复后的**正面登记**（以前这里只有缺口警告）
        A('- ★ **装备授予的武器池技能（WPS）已进循环** —— **%s** 条，'
          '权重合计 **%s**（这就是 §0 里的 `W`）：'
          % (len(gap['items']),
             round(sum(float(e.get('weight') or 0) for e in gap['items']), 1)))
        A('')
        A('  | 槽位 | 技能 | 记录 | 等级 | skillChanceWeight | 武器伤害% |')
        A('  |---|---|---|---|---|---|')
        for e in gap['items']:
            A('  | %s | %s（%s） | `%s` | %s | **%s** | %s%% |'
              % (e.get('slot'), e.get('name'), e.get('label') or '', e.get('sk'),
                 e.get('level'), e.get('weight'), e.get('weapon_pct')))
        A('')
        A('  > 机制：装备授予的 `Skill_WPAttack_*` 与专精里的武器池技能**同属一个池**，'
          '按 `skillChanceWeight` 权重从默认攻击的 100 里扣。')
        A('  > ⚠ **未判 `dualWieldOnly`**：带该字段的 WPS（如「恐狼之爪」「毁伤」与'
          '夜刃的切割/瞬影/死亡旋风/处决）在游戏里**需要双持**才出现；'
          '本模型不检查武器形态 ⇒ 双手 build 的 WPS 占比会偏高。**已登记，未修。**')
        A('')
    elif gap.get('missing'):
        A('- ★★ **装备授予的武器池技能（WPS）未进循环** —— 影响 '
          '**%s** 条，丢失权重 **%s**。'
          % (len(gap['missing']), gap.get('weight_lost')))
        A('')
        A('  | 槽位 | 技能 | 记录 | 等级 | skillChanceWeight | 武器伤害% |')
        A('  |---|---|---|---|---|---|')
        for e in gap['missing']:
            A('  | %s | %s（%s） | `%s` | %s | **%s** | %s%% |'
              % (e.get('slot'), e.get('name'), e.get('label') or '', e.get('sk'),
                 e.get('level'), e.get('weight'), e.get('weapon_pct')))
        A('')
        A('  > 机制：装备授予的 `Skill_WPAttack_*` 与专精里的武器池技能**同属一个池**，'
          '按 `skillChanceWeight` 权重从默认攻击的 100 里扣。**这条没进池** ⇒'
          '默认攻击占比被高估。修法见 `gd/procs.py::wps_pool()`。')
        A('')
    A('- **装备自带的触发技能、星座绑定的主动技能不进 DPS** —— 本模型只算'
      '「技能本体 + 装备属性」。上方 ⑤/② 的表是**关系说明**，不是已计入的伤害。')
    A('- **DoT 时长缺失**时按满覆盖处理（时长读自 `offensiveSlow<X>Duration*`）。')
    A('- **`projectile_hits` 是场景假设**（默认 1 = 与 grimtools 计算器同口径），'
      '不是数据库事实。')
    A('- 提示框为**面板口径**（不含敌方防御），与游戏内提示框一致 —— '
      '游戏原文：*「左键技能对单体目标的伤害明细，受到所有来自装备及其技能冷却'
      '时间和加成的影响。敌人的防御未计算在内。」*')
    A('')
    return '\n'.join(L)


def _proc_section(A, base, db, items, plan, rec, h, crit_chance, has_shield):
    """单个技能 → 它能触发的物品技能表。"""
    if db is None or not plan:
        A('（未提供方案数据，跳过）')
        return
    lst = _PR.collect(db, items, plan)
    if not lst:
        A('本方案没有任何物品技能（衣服 / 镶嵌物 / 附魔 / 圣物上都没有）。')
        return
    freq = float(h.get('freq') or 0.0)
    A('| 来源槽 | 物品 | 技能 | 类别 | 触发条件 | 几率 | 裁决 | 期望次数/秒 |')
    A('|---|---|---|---|---|---|---|---|')
    n_yes = 0
    for e in lst:
        v, why = _PR.verdict(e, skill_is_attack=True, crit_chance=crit_chance,
                             has_shield=has_shield)
        exp = '—'
        if v == _PR.YES and e.get('trigger') == 'AttackEnemy' and freq:
            exp = '%.2f' % (freq * float(e.get('chance') or 0) / 100.0)
            n_yes += 1
        elif v == _PR.YES:
            exp = '与技能频率无关'
        A('| %s | %s | %s | %s | %s | %s | **%s** | %s |'
          % (e.get('slot'), e.get('label') or e.get('gid'), e.get('name'),
             e.get('kind_zh'), e.get('trigger_zh'),
             (('%s%%' % e['chance']) if e.get('chance') else '—'),
             v, exp))
    A('')
    if n_yes:
        A('> 「期望次数/秒」= 本技能频率 × 触发几率 —— **仅作参考**：'
          '这些 proc 的伤害**没有**计入本模型 DPS（模型边界）。')


def _all_procs(A, base, db, items, plan, order, hits, nm_fn, crit_chance,
               has_shield):
    """全循环触发矩阵：物品技能 × 本循环技能。"""
    if db is None or not plan:
        A('（未提供方案数据，跳过）')
        return
    lst = _PR.collect(db, items, plan)
    if not lst:
        A('本方案没有任何物品技能。')
        return
    names = [(r, nm_fn(r)) for r in order if hits.get(r)]
    A('| 来源槽 | 物品技能 | 类别 | 触发条件 | 几率 | %s |'
      % ' | '.join(n for _r, n in names))
    A('|---|' + '---|' * (len(names) + 4))
    for e in lst:
        cells = []
        for r, _n in names:
            h = hits.get(r) or {}
            v, _why = _PR.verdict(e, skill_is_attack=bool(h.get('rows')),
                                  crit_chance=crit_chance, has_shield=has_shield)
            cells.append({'可以': '✓', '不可以': '✗', '与技能无关': '–'}.get(v, v))
        A('| %s | %s | %s | %s | %s | %s |'
          % (e.get('slot'), e.get('name'), e.get('kind_zh'), e.get('trigger_zh'),
             (('%s%%' % e['chance']) if e.get('chance') else '—'),
             ' | '.join(cells)))
    A('')
    A('> 图例：**✓ 可以** = 该技能命中即 roll ｜ **✗ 不可以** = 条件不满足'
      '（如需要暴击而暴击率为 0）｜ **– 与技能无关** = 由挨打 / 格挡 / 击杀 / 低血'
      '触发，或本身是常驻 / 武器池技能。')
    A('>')
    A('> ⚠ WPS 类（武器池技能）的触发权**属于一次「带武器伤害的攻击」**：'
      '默认攻击带武器伤害时它才参与掷骰。')
