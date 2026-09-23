# -*- coding: utf-8 -*-
r"""伤害代理分解器（gd_explain）—— 把「伤害代理」这个数字拆开讲清楚

```bash
python gd_explain.py plans/necro_inquisitor_100_dmg.json
python gd_explain.py plans/a.json plans/b.json        # 多份并排对比
```

## 「伤害代理」到底是什么

**它不是 DPS。** 它是**装备输出类属性的加权总分**：

```
伤害代理 = Σ (每个输出维度的装备数值 × 该维度的权重)
```

权重来自 `gd_opt.W_DMG`，含义是「这一项每 1 点值多少分」。权重不是拍脑袋定的，
而是按**该属性对实际 DPS 的作用方式**排的梯度：

| 档 | 权重 | 维度 | 为什么 |
|---|---|---|---|
| 乘区 | **2.00** | 攻速 +% | +5% 攻速 ≈ +5% DPS（直接乘） |
| 全局 | **1.30** | 总伤害 +% | 对所有伤害类型生效 |
| 主伤害 | **1.00** | 物理% / 穿刺% / 冰冷% / 活力%…、暴伤% | 直接加在主力伤害上 |
| DoT | **0.55** | 流血% / 活力衰减% | 有覆盖率折扣（不是每一击都吃满） |
| 平伤 | **0.50** | 固定伤害（fpie） | 不吃装备的 % 加成，后期占比低 |
| 辅助 | **0.30** | OA | 只影响命中与暴击概率，间接 |
| 不计 | **0.00** | 生命 / 护甲 | 纯输出目标下不参与评分 |

> 维度名是**固定的 10 个**（`FKEYS`），但**含义可被流派覆盖**：
> 例如走以太/活力的死灵审判流，`phys` 槽实际读的是 `offensiveLifeModifier`（活力 +%），
> `pierce` 槽读的是 `offensiveAetherModifier`（以太 +%）。见 `archetypes.json` 的 `field_aliases`。

## 为什么不用真实 DPS

真实 DPS 取决于攻速、命中率、暴击、怪物抗性、技能循环、宠物……**无法只从装备字段算出来**。
所以用「装备输出属性加权和」当**代理（proxy）**：**用途是排序，不是预测伤害绝对值。**

## 怎么读

- 只能跟**同流派 + 同等级**的方案横比（不同流派字段映射不同，数字不可比）
- 数字越大越好，但**绝对值没有物理意义**
- 跟「等效技能」是两个独立项：装备技能加成单独折算，最后在 `s0` 里合成
"""
import json
import os
import sys

USER_ARGV = [a for a in sys.argv[1:]]
GOAL = next((a.split('=')[1] for a in USER_ARGV if a.startswith('--goal=')), 'dmg')
FILES = [a for a in USER_ARGV if not a.startswith('--')]

# 与 eval_plan 同款「最小化 import」：gd_opt 的模块级代码会跑一轮搜索，这里压到最小
os.environ.setdefault('GD_MAX_ILVL', '100')
os.environ.setdefault('GD_AUTO_TOPN', '70')
os.environ.setdefault('GD_COMP_TOPN', '16')
os.environ.setdefault('GD_AUG_TOPN', '10')
os.environ.setdefault('GD_DMG_TIE', '1.2')
sys.argv = ['gd_opt.py', '--goal', GOAL, '--beam', '30', '--restart', '1',
            '--min-leg', '0', '--max-leg', '14', '--min-epic', '0', '--max-epic', '14',
            '--out', '_explain_boot.json', '--quiet']

from . import opt as O


def explain(path):
    d = json.load(open(path, encoding='utf-8'))
    sol = {k: tuple(v) for k, v in d.items()}
    _r, o = O.ev(sol)
    ot = [o[k] for k in O.FKEYS]
    # ★ 合并「指向同一字段的多个维度」：流派可以利用 `field_aliases` 让两个维度
    #   指向同一个字段、给不同的权重档（例 wereraven 的 `pierce` 和 `bleed` 都指向
    #   `offensiveSlowColdModifier`）。分开显示会让人以为系统算重了，
    #   所以按中文标签归并，把权重相加（= 该字段的实际等效权重）。
    from collections import OrderedDict
    merged = OrderedDict()
    for i, k in enumerate(O.FKEYS):
        w = O.W_DMG.get(k, 0.0)
        if w == 0:
            continue
        lbl = dim_label(k)
        e = merged.setdefault(lbl, {'v': ot[i], 'w': 0.0, 'dims': []})
        e['v'] = ot[i]
        e['w'] += w
        e['dims'].append(k)
    rows = [(lbl, e['w'], e['v'], e['v'] * e['w'], e['dims'])
            for lbl, e in merged.items()]
    total = sum(r[3] for r in rows)
    sk_raw = sum(O.contrib(g)[2] for s in sol for g in sol[s] if g)
    skill_k = O.SKILL_K
    return {'rows': rows, 'total': total, 'sk': sk_raw, 'skill_k': skill_k,
            's0': total + skill_k * sk_raw, 'path': path}


# 维度 → 中文。★ 优先按**真实字段名**取（因为流派会用 `field_aliases` 把维度
#   指向别的伤害类型：以太/活力流的 `phys` 槽实际读 `offensiveLifeModifier`）。
#   只写死维度名会显示成「主伤害% / 副伤害%」，用户根本不知道那是什么伤害。
DIM_ZH = {'OA': '进攻能力(OA)', 'pierce': '穿刺伤害%', 'phys': '物理伤害%',
          'bleed': '流血伤害%', 'total': '总伤害%', 'crit': '暴击伤害%',
          'spd': '攻击速度%', 'fpie': '固定伤害', 'life': '生命', 'armor': '护甲'}

FIELD_ZH = {
    'offensivePhysicalModifier': '物理伤害%',
    'offensivePierceModifier': '穿刺伤害%',
    'offensiveColdModifier': '冰冷伤害%',
    'offensiveFireModifier': '火焰伤害%',
    'offensiveLightningModifier': '闪电伤害%',
    'offensivePoisonModifier': '毒素伤害%',
    'offensiveLifeModifier': '活力伤害%',
    'offensiveChaosModifier': '混乱伤害%',
    'offensiveAetherModifier': '以太伤害%',
    'offensiveElementalModifier': '元素伤害%',
    'offensiveSlowBleedingModifier': '流血伤害%',
    'offensiveSlowLifeModifier': '活力衰减%',
    'offensiveSlowColdModifier': '霜燃伤害%',
    'offensiveSlowFireModifier': '燃烧伤害%',
    'offensiveSlowLightningModifier': '电击伤害%',
    'offensiveSlowPoisonModifier': '毒伤衰减%',
    'offensiveSlowPhysicalModifier': '内伤%',
    'offensiveTotalDamageModifier': '总伤害%',
    'offensiveCritDamageModifier': '暴击伤害%',
    'characterAttackSpeedModifier': '攻击速度%',
    'characterOffensiveAbility': '进攻能力(OA)',
    'offensivePierceMin': '固定穿刺',
    'offensiveLifeMin': '固定活力',
    'offensivePhysicalMin': '固定物理',
    'offensiveColdMin': '固定冰冷',
    'characterLife': '生命',
    'defensiveProtection': '护甲',
}


def dim_label(k):
    """维度 → 中文标签：优先用该流派真实字段名（经 field_aliases 覆盖后的 FLD）"""
    fld = O.FLD.get(k)
    if fld and fld in FIELD_ZH:
        return FIELD_ZH[fld]
    return DIM_ZH.get(k, k)


def explain_md(path):
    """把分解结果渲染成 **Markdown 片段**，供 BD 报告内嵌（`bd_gen` 调用）。

    ⚠ 调用前请先设好 `GD_ARCHETYPE`，否则 `field_aliases` 不会生效，
    标签会退化成「物理伤害%」这种通用名（而不是该流派的真实加成项）。
    """
    r = explain(path)
    L = ['| 加成项（该流派的真实字段） | 装备值 | ×权重 | 得分 | 占比 |',
         '|---|---|---|---|---|']
    for lbl, w, v, sc, dims in sorted(r['rows'], key=lambda x: -x[3]):
        share = (sc / r['total'] * 100) if r['total'] else 0
        note = '（%s 合成）' % '/'.join(dims) if len(dims) > 1 else ''
        L.append('| %s%s | %.1f | ×%.2f | **%.1f** | %.1f%% |' % (lbl, note, v, w, sc, share))
    L.append('| **伤害代理合计** | | | **%.1f** | 100%% |' % r['total'])
    L.append('')
    L.append('- **等效技能** %.1f（装备自带技能等级折算，与上表并列计入 s0）' % r['sk'])
    L.append('- **综合评分 s0** = %.1f + %.2f × %.1f = **%.1f**'
             % (r['total'], r['skill_k'], r['sk'], r['s0']))
    L.append('')
    L.append('> ⚠ **口径边界**：本表是 `gd_opt` 搜索装备时用的**代理分**，它只看'
             '「装备属性 × 权重」，')
    L.append('> **不含**套装加成、装备/套装对技能的改造（`Skill_Modifier`）、'
             '属性加成与技能等级带来的收益。')
    L.append('> 想看真实量级的伤害请用**第五节的最终伤害明细**；本表只用于横向排序。')
    return '\n'.join(L)


def main():
    if not FILES:
        print(__doc__)
        return 1
    res = [explain(f) for f in FILES]

    for r in res:
        print('=' * 76)
        print('伤害代理分解 ｜ %s' % os.path.basename(r['path']))
        print('=' * 76)
        print("%-16s %-10s %-6s %-10s %-8s" % ("维度（该流派的真实加成项）", "装备值", "×权重", "得分", "占比"))
        print('-' * 76)
        for lbl, w, v, sc, dims in sorted(r['rows'], key=lambda x: -x[3]):
            share = (sc / r['total'] * 100) if r['total'] else 0
            bar = '█' * max(0, int(round(share / 3.0)))
            note = ('  ← 由 %s 两个维度合成（权重相加）' % '/'.join(dims)) if len(dims) > 1 else ''
            print('%-16s %-10.1f %-6.2f %-10.1f %5.1f%%  %s%s'
                  % (lbl, v, w, sc, share, bar, note))
        print('-' * 76)
        print('%-16s %-10s %-6s %-10.1f %5s' % ('伤害代理', '', '', r['total'], '100%'))
        print()
        print('另有「等效技能」%.1f（装备自带的技能等级 × 每级价值，单独计）' % r['sk'])
        print('综合评分 s0 = 伤害代理 + %.1f × 等效技能 = %.1f + %.1f = **%.1f**'
              % (r['skill_k'], r['total'], r['skill_k'] * r['sk'], r['s0']))
        print()

    if len(res) > 1:
        print('=' * 76)
        print('并排对比')
        print('=' * 76)
        print('%-34s %-10s %-10s %-10s' % ('方案', '伤害代理', '等效技能', 's0'))
        base = res[0]['total'] or 1.0
        for r in res:
            delta = (r['total'] - base) / base * 100
            print('%-34s %-10.1f %-10.1f %-10.1f  (%+.1f%% vs 第一份)'
                  % (os.path.basename(r['path']), r['total'], r['sk'], r['s0'], delta))
        print()

    print('【怎么读】单位不是 DPS，是「装备输出属性的加权总分」，只用于**排序**。')
    print('          只能跟**同流派 + 同等级**的方案比；绝对值没有物理意义。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
