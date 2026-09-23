# -*- coding: utf-8 -*-
r"""gd_char_compare.py —— **两个存档的 DPS + 生存对比**（2026-09-18 新增）

为什么需要它：`gd_gear_compare.py` 比的是「一套装备的现状 vs 候选」，而这里比的是
**两个真实存档**（例如换形态前后的备份档），并且**同时给出生存面**——
伤害模型只看输出，但「换个形态是不是更容易死」是同一个问题的另一半。

用法（两个档要在同一个存档目录下，用 `GD_SAVE` 指过去即可，不动主存档）
------------------------------------------------------------------
```bash
mkdir -p /e/gzq_deepseek/_gd_cmp/main
cp -r "<当前档>/main/_Sam"     "…/_gd_cmp/main/_Sam"
cp -r "<旧档备份>/…/main/_Sam" "…/_gd_cmp/main/_SamWolf"

GD_SAVE="E:/gzq_deepseek/_gd_cmp" python gd_char_compare.py SamWolf:狼人 Sam:鸦人
```

口径
----
* 两边都走 `gd_dps_check.load_char(local=False)` —— **关闭「游戏提示框真值」**，
  否则只覆盖某一侧的手套/腰带/主手，凭空给那边加成，对比不成立（SKILL §69.4）。
* 抗性在**终极难度**有惩罚：原始值需 **上排 130 / 下排 105** 才算真正封顶 80%
  （即惩罚 上排 −50 / 下排 −25）。本表按此判定 ★。
* 生命 = 存档块2 的 `health`（基础）+ 装备/技能/星座的 `characterLife`（估算值，
  模型没有官方生命公式；相对变化是可靠的，绝对值仅供参考）。
"""
import argparse
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# ★ 与 `gd_opt.TYPES/NEED/PEN/RMAP` 保持一致（不 import gd_opt：它有模块级初始化副作用）
TYPES_ZH = ['火', '冰', '电', '毒酸', '穿刺', '流血', '活力', '虚化', '混乱']
TOP = {'火', '冰', '电', '毒酸', '穿刺'}          # 上排：需 130 / 惩罚 −50
NEED = {t: (130 if t in TOP else 105) for t in TYPES_ZH}
RMAP = {
    '火': [('defensiveElementalResistance', 1), ('defensiveFire', 1)],
    '冰': [('defensiveElementalResistance', 1), ('defensiveCold', 1)],
    '电': [('defensiveElementalResistance', 1), ('defensiveLightning', 1)],
    '毒酸': [('defensivePoison', 1)],
    '穿刺': [('defensivePierce', 1)],
    '流血': [('defensiveBleeding', 1)],
    '活力': [('defensiveLife', 1)],
    '虚化': [('defensiveAether', 1)],
    '混乱': [('defensiveChaos', 1)],
}
_FIELDS = {k for pairs in RMAP.values() for k, _w in pairs}
SURV_FIELDS = tuple(_FIELDS) + (
    'defensiveProtection', 'defensiveBonusProtection',
    'characterOffensiveAbility', 'characterDefensiveAbility',
    'characterLife', 'characterLifeModifier', 'characterLifeRegen',
    'characterMana', 'characterManaModifier', 'characterManaRegen', 'characterManaRegenModifier',
    'characterEnergyAbsorptionPercent', 'characterDamageAbsorptionPercent',
    'defensiveStun', 'defensiveFreeze', 'defensiveTrap', 'defensivePetrify',
    'defensiveKnockdown', 'defensiveDisruption',
)


def survival(c, db):
    """把一个角色的装备 + 技能 + 星座，汇总成生存字段字典。"""
    from . import rotation as R
    agg = Counter()
    for _slot, f in (c.get('folded') or {}).items():
        for k, v in f.items():
            if k in SURV_FIELDS and isinstance(v, (int, float)):
                agg[k] += v
    for rec, lv in (c.get('skills') or {}).items():
        if not lv:
            continue
        f = R.fields_with_buff(db, rec) or {}
        for k in SURV_FIELDS:
            v = R._at(f.get(k), lv)
            if v:
                agg[k] += v
    return agg


def res_of(agg, name):
    return sum(agg.get(f, 0.0) * w for f, w in RMAP[name])


def base_health(name):
    """从存档块2 读基础生命。"""
    try:
        from . import _legacyenv as E
        from .save import core as S
        sd, _ = E.find_save_dir()
        p = os.path.join(sd, 'main', '_' + name.lstrip('_'), 'player.gdc')
        if not os.path.exists(p):
            return 0.0
        return float((S.parse(p)['block_map'][2] or {}).get('health') or 0)
    except Exception:
        return 0.0


def main():
    ap = argparse.ArgumentParser(description='两个存档的 DPS + 生存对比')
    ap.add_argument('pairs', nargs='+', help='形如 SamWolf:狼人 Sam:鸦人')
    ap.add_argument('--arch', action='append', default=[],
                    help='强制形态，形如 SamWolf=werewolf（可多次）')
    ap.add_argument('--weapon-set', default='')
    ap.add_argument('--out', default='')
    a = ap.parse_args()

    import gd_dps_check as C
    import gd_gear_compare as GC

    forced = dict(x.split('=') for x in a.arch)

    rows = []
    for spec in a.pairs:
        name, label = spec.split(':', 1) if ':' in spec else (spec, spec)
        c = C.load_char(name, a.weapon_set, False)      # ★ local=False：两边同口径
        arch = forced.get(name) or C.guess_arch(c['skills']) or ''
        rep, eff, attrs = GC._report(c, arch)
        sv = survival(c, c['db'])
        rows.append({'name': name, 'label': label, 'arch': arch, 'c': c,
                     'rep': rep, 'attrs': attrs, 'sv': sv, 'hp0': base_health(name)})
        print('[读档] %-9s %-6s 形态=%-22s DPS=%9.0f' % (name, label, arch, rep['dps']))

    def delta(vals, fmt='%+.1f%%'):
        if len(vals) != 2 or not vals[0]:
            return '—'
        return fmt % ((vals[1] / vals[0] - 1) * 100)

    L = []
    L.append('# 两个存档对比 · DPS 与生存')
    L.append('')
    L.append('> 口径：两边都关掉「游戏提示框真值」（`local=False`），其余完全同模型。')
    L.append('> 形态由技能树自动识别：' + ' ｜ '.join('%s = `%s`' % (r['label'], r['arch']) for r in rows))
    L.append('')

    # ---- 一、输出
    L.append('## 一、输出（真实伤害模型）')
    L.append('')
    dps = [r['rep']['dps'] for r in rows]
    L.append('| 项 | ' + ' | '.join(r['label'] for r in rows) + ' | 变化 |')
    L.append('|---|' + '---|' * (len(rows) + 1))
    L.append('| **合计每秒伤害** | ' + ' | '.join('**%.0f**' % v for v in dps) + ' | ' + delta(dps) + ' |')
    L.append('| 攻击速度 | ' + ' | '.join('%.2f 次/秒' % r['rep']['aps'] for r in rows) + ' | — |')
    L.append('| 独立伤害倍率 | ' + ' | '.join('×%.2f' % r['rep']['dmg_mult'] for r in rows) + ' | — |')
    L.append('| 攻速加成 | ' + ' | '.join('%+.0f%%' % r['rep']['speed_pct'] for r in rows) + ' | — |')
    L.append('')
    L.append('### 逐技能')
    L.append('')
    names = []
    for r in rows:
        for rec, h in (r['rep'].get('skills') or {}).items():
            nm = rec.split('/')[-1]
            if h.get('dps') and nm not in names:
                names.append(nm)
    L.append('| 技能 | 等级 | ' + ' | '.join(r['label'] for r in rows) + ' | 占比 |')
    L.append('|---|---|' + '---|' * len(rows) + '---|')
    for nm in names:
        cells, lv, share = [], '', []
        for r in rows:
            v = None
            for rec, h in (r['rep'].get('skills') or {}).items():
                if rec.split('/')[-1] == nm and h.get('dps'):
                    v, lv = h['dps'], h.get('level')
            cells.append('%.0f' % v if v else '—')
            share.append('%.0f%%' % (v / r['rep']['dps'] * 100) if v else '—')
        L.append('| %s | %s | %s | %s |' % (nm, lv or '—', ' | '.join(cells), ' / '.join(share)))
    L.append('')

    # ---- 二、生存
    L.append('## 二、生存')
    L.append('')
    L.append('### 抗性（终极难度：原始值需 **上排 130 / 下排 105** 才算真正封顶 80%）')
    L.append('')
    L.append('| 抗性 | 需求 | ' + ' | '.join(r['label'] for r in rows) + ' | 变化 |')
    L.append('|---|---|' + '---|' * (len(rows) + 1))
    for name in TYPES_ZH:
        vals = [res_of(r['sv'], name) for r in rows]
        cell = ['%.0f%s' % (v, ' ★' if v >= NEED[name] else ' ✗') for v in vals]
        L.append('| %s抗 | %d | %s | %s |'
                 % (name, NEED[name], ' | '.join(cell),
                    ('%+.0f' % (vals[1] - vals[0])) if len(vals) == 2 else '—'))
    L.append('')
    L.append('> ★ = 已达难度上限；✗ = **没封顶**（实机里该抗性会显示 < 80%）。')
    L.append('')
    L.append('### 生命 / 护甲 / 防御')
    L.append('')
    hp = [r['hp0'] + r['sv'].get('characterLife', 0.0) for r in rows]
    L.append('| 项 | ' + ' | '.join(r['label'] for r in rows) + ' | 变化 |')
    L.append('|---|' + '---|' * (len(rows) + 1))
    L.append('| **生命（基础+加成）** | ' + ' | '.join('**%.0f**' % v for v in hp) + ' | ' + delta(hp) + ' |')
    L.append('| 　存档基础生命 | ' + ' | '.join('%.0f' % r['hp0'] for r in rows) + ' | — |')
    L.append('| 　装备+技能+星座 | ' + ' | '.join('%+.0f' % r['sv'].get('characterLife', 0) for r in rows) + ' | — |')
    L.append('| 生命回复/秒 | ' + ' | '.join('%.1f' % r['sv'].get('characterLifeRegen', 0) for r in rows) + ' | — |')
    prot = [r['sv'].get('defensiveProtection', 0) + r['sv'].get('defensiveBonusProtection', 0) for r in rows]
    L.append('| **护甲/防护** | ' + ' | '.join('%.0f' % v for v in prot) + ' | ' + delta(prot) + ' |')
    da = [r['sv'].get('characterDefensiveAbility', 0) for r in rows]
    oa = [r['sv'].get('characterOffensiveAbility', 0) for r in rows]
    L.append('| **防御能力 Da** | ' + ' | '.join('%+.0f' % v for v in da) + ' | ' + delta(da) + ' |')
    L.append('| **攻击能力 Oa** | ' + ' | '.join('%+.0f' % v for v in oa) + ' | ' + delta(oa) + ' |')
    L.append('| 控制抗（晕/冻/定/石） | ' +
             ' | '.join('%.0f / %.0f / %.0f / %.0f' % (
                 r['sv'].get('defensiveStun', 0), r['sv'].get('defensiveFreeze', 0),
                 r['sv'].get('defensiveTrap', 0), r['sv'].get('defensivePetrify', 0)) for r in rows) + ' | — |')
    L.append('')
    L.append('### 续航（能量）')
    L.append('')
    L.append('| 项 | ' + ' | '.join(r['label'] for r in rows) + ' |')
    L.append('|---|' + '---|' * len(rows))
    for k, nm, f in (('characterMana', '能量上限 +', '%+.0f'),
                     ('characterManaModifier', '能量上限 %', '%+.0f%%'),
                     ('characterManaRegen', '每秒回蓝', '%+.1f'),
                     ('characterManaRegenModifier', '回蓝 %', '%+.0f%%'),
                     ('characterEnergyAbsorptionPercent', '击杀能量吸收', '%+.0f%%')):
        L.append('| %s | ' % nm + ' | '.join(f % r['sv'].get(k, 0) for r in rows) + ' |')
    L.append('')
    L.append('> 每秒蓝耗要用 `gd_mana.py` 单独算（伤害模型不算蓝）。')
    L.append('')

    txt = '\n'.join(L)
    print()
    print(txt)
    if a.out:
        open(a.out, 'w', encoding='utf-8').write(txt)
        print('\n→ %s' % a.out)


if __name__ == '__main__':
    main()
