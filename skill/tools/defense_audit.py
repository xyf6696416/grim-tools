#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""防御减伤审计 —— **我方挨打**（官方 Order of Defense 九层）。

用法
----
```bash
PY=/c/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe
$PY tools/defense_audit.py Sam                            # 面板 + 典型档 + 边际
$PY tools/defense_audit.py Sam --dmg 8000 --dtype physical --enemy m1281
$PY tools/defense_audit.py Sam --enemy m1281 --md --out data/defense_snapshot.md
```

口径
----
* **抗性**：`raw = 装备 + 技能/星座`（`calc.js::f63k` 的组成规则），
  再减**难度惩罚**（终极 上排 −50 / 下排 −25）并钳到 80（`gd/opt.py::NEED/PEN` 同源）。
* **护甲**：逐部位（`calc.js::f62k`；腰带护甲对 6 部位各计一次；吸收率的前提是该部位**自身**护甲 ≠ 0）。
* **减伤链**：① 躲避 ② 敌方命中/暴击 ③ 格挡 ④ 种族% ⑤ 抗性 ⑥ 护甲 ⑦ 种族点数 ⑧ %吸收 ⑨ 点数吸收。
* ⛔ **不给「敌人打你多少」的绝对量** —— 那需要 `monsterdb` 的攻击数据（未抽取）；
  本工具给的是**传递函数**（给定打击 → 剩余）与**等效生命**。
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gd import defense as DF                                       # noqa: E402
from gd import dps as D                                            # noqa: E402
from gd import rotation as R                                       # noqa: E402
from gd import enemy as EN                                         # noqa: E402


def _rep(c, arch='werewolf'):
    """算一个 `rep` —— **只为拿面板 DA/OA**（与锚点同一套参数，含等级/三围项）。"""
    from gd import skillprov as SP, procs as PR
    sk = {k: v for k, v in c['skills'].items() if v > 0}
    eff = dict(sk)
    for rec, ex in (c.get('skill_plus') or {}).items():
        if rec in eff:
            eff[rec] += ex
    mast = {}
    for rec, lv in sk.items():
        if '_classtraining_' in rec:
            mast[os.path.basename(rec).replace('_classtraining_', '')
                 .replace('.dbr', '')] = lv
    for cl, ex in (c.get('mastery_plus') or {}).items():
        mast[cl] = mast.get(cl, 0) + ex
    at = R.panel_attrs(c.get('bio') or {}, mast,
                       c.get('gear_flat') or {}, c.get('gear_pct') or {})
    return R.final_report(
        arch, eff, db=c['db'], folded=c['folded'], skill_records=list(eff),
        base_aps=c.get('base_aps') or 1.25, attr_pct=R.attr_damage_pct(at),
        conversions=c.get('conversions') or [], skill_mods=c.get('skill_mods') or {},
        attrs=at, level=c.get('level'),
        enemy=EN.get_profile(None, c.get('level') or 100),
        equipped_sk=SP.norm_equipped([x[1] for x in (c.get('item_skills') or [])]),
        item_wps=PR.wps_pool(c.get('base_gids')),
        # ★★ 2026-09-22：武器类型门控 —— 防御链里的「技能/星座被动」同样受限
        #   （点数吸收、DA 之类可能挂在要求斧/盾的星位上）。
        weapon_st=PR.weapon_state_of(c))


def _hp(name, panel):
    """生命 = 存档块2 的 `health`（基础）+ 装备/技能/星座的 `characterLife`。"""
    from gd import paths
    from gd.save import core as S
    base = 0.0
    try:
        sd, _ = paths.save_dir()
        p = os.path.join(sd, 'main', '_' + str(name).lstrip('_'), 'player.gdc')
        if os.path.isfile(p):
            base = float((S.parse(p)['block_map'][2] or {}).get('health') or 0)
    except Exception:                                              # noqa: BLE001
        base = 0.0
    return base, base + float(panel.get('life_bonus') or 0.0)


def render(name, panel, hp0, hp, args, enemy_oa=None, enemy_label=''):
    L = []
    L.append('# 防御减伤审计 · %s' % name)
    L.append('')
    L.append('> 由 `tools/defense_audit.py` 生成。**我方挨打**的官方 Order of Defense 九层。')
    L.append('> ⛔ 本表**不含「敌人打你多少」的绝对量** —— 那需要 `monsterdb` 的攻击数据（离线库未抽取）。')
    L.append('> 看到的是**传递函数**：给定一次打击 → 过完九层剩多少。')
    L.append('')

    # ---- 一、面板
    L.append('## 一、防御面板')
    L.append('')
    L.append('### 抗性（难度 %d；penalty 上排 −50 / 下排 −25 ⇒ 封顶需 raw ≥ 130 / 105）'
             % args.difficulty)
    L.append('')
    L.append('| 抗性 | raw（装备+技能/星座） | 惩罚 | 惩罚后 | 上限 | 面板值 | 状态 |')
    L.append('|---|---|---|---|---|---|---|')
    for t, r in panel['resist'].items():
        st = '**封顶**' if r['capped'] else ('满足要求' if r['meets'] else '★**未满足**')
        L.append('| %s | %.1f | −%.0f | %.1f | %.0f | **%.1f** | %s |'
                 % (t, r['raw'], r['pen'], r['after_pen'], r['cap'], r['value'], st))
    L.append('')
    L.append('> `raw` 含**元素抗性并入火/冰/雷**（`f63k` 规则）。物理桶 `NEED=0` = 没有满抗要求，'
             '不等于已封顶（看「状态」列的**封顶**与否）。')
    L.append('')

    L.append('### 护甲（逐部位 · `calc.js::f62k`）')
    L.append('')
    a = panel['armor']
    L.append('| 部位 | 本槽护甲 | 计入后 | 吸收率 |')
    L.append('|---|---|---|---|')
    for reg, r in a['regions'].items():
        L.append('| %s | %.0f | **%.0f** | %.0f%% |'
                 % (DF.REGION_ZH.get(reg, reg), r['own'], r['raw'], r['absorb']))
    L.append('')
    L.append('- 合计 **%.0f**（六部位自身 %.0f + 腰带 %.0f）｜ 全局项（技能/星座）%.0f ｜ '
             '护甲 %%加成 %.0f%%'
             % (a['total'], a['regions_total'], a['waist'], a['global'],
                a['prot_modifier']))
    L.append('- ★ **腰带护甲对 6 个部位各计一次**；**吸收率的前提是该部位自身护甲 ≠ 0**'
             '（只有腰带的部位吸收率为 0）。')
    L.append('- ★ 护甲**只吃物理直伤**；穿刺/创伤/元素/流血**不过甲**。')
    L.append('')

    L.append('### 其他')
    L.append('')
    L.append('| 项 | 值 |')
    L.append('|---|---|')
    L.append('| 防御能力 DA | **%.1f**（%s） |'
             % (panel['da'], '含等级/三围项' if panel['da_has_level'] else '⚠ 仅装备+技能，**缺等级项**'))
    L.append('| 攻击能力 OA | %.1f |' % panel['oa'])
    L.append('| 闪避 ／ 投射偏移 | %.1f%% ／ %.1f%% |' % (panel['dodge'], panel['deflect']))
    L.append('| 格挡率 ／ 格挡值 | %s ／ %s |'
             % ('%.0f%%' % panel['block_chance'] if panel['block_chance'] else '—（未持盾）',
                '%.0f' % panel['block_amount'] if panel['block_amount'] else '—'))
    L.append('| 种族 %% 减伤 ／ 种族点数减伤 | %.0f%% ／ %.0f |'
             % (panel['racial_pct'], panel['racial_flat']))
    L.append('| %% 伤害吸收（**常驻**） | %.1f%% |' % panel['pct_absorb'])
    L.append('| %% 伤害吸收（**条件**） | %.1f%% |' % panel['pct_absorb_cond'])
    L.append('| 点数伤害吸收（常驻 ／ 条件） | %.0f ／ %.0f |'
             % (panel['flat_absorb'], panel['flat_absorb_cond']))
    L.append('| 生命（基础 + 加成） | **%.0f**（%.0f + %.0f） |'
             % (hp, hp0, panel['life_bonus']))
    L.append('')
    L.append('> ⚠ 生命口径同 `gd/compare.py::base_health`（存档块2 `health` + `characterLife` 累加）——'
             '模型**没有官方生命公式**，**绝对值仅供参考**，相对变化才可靠。'
             '也**未**应用 `characterLifeModifier`。')
    L.append('')

    # ---- 二、减伤链
    L.append('## 二、减伤链（给定打击的逐层明细）')
    L.append('')
    dmg, dt = args.dmg, args.dtype
    rem, rows = DF.chain_remain(dmg, dt, panel, enemy_oa=enemy_oa,
                                flat_pool=args.flat_pool)
    L.append('口径：**%s 打击 %.0f 点**%s'
             % (dt, dmg, ('；敌方档 `%s`（OA 用于第 ② 层）' % enemy_label) if enemy_oa else
                '；⚠ **未给敌方档 ⇒ 第 ② 层跳过**（用 `--enemy m<id>` 打开）'))
    L.append('')
    L.append('| 层 | 内容 | 进入 | 剩余 | 本层减免 |')
    L.append('|---|---|---|---|---|')
    for r in rows:
        L.append('| %s | %s | %.1f | %.1f | %.2f%% |'
                 % (r['no'], r['name'], r['before'], r['after'], r['cut_pct']))
    L.append('')
    L.append('- **有效减伤 %.2f%%** ｜ 剩余 **%.1f**' % ((1 - rem / dmg) * 100, rem))
    L.append('- ⚠ 第 ② 层**可能让伤害变大**：它是「敌方命中掷骰 + 暴击」的期望倍率。'
             'PTH ≥ 100 时敌方没有未命中面，只剩暴击加成 ⇒ 期望 > 1。')
    L.append('')

    # ---- 三、典型档
    L.append('## 三、典型打击档（**示意档，非真值**）')
    L.append('')
    ts = DF.typical_suite(panel, hp=hp, enemy_oa=enemy_oa,
                          flat_pool=args.flat_pool)
    L.append('| 类型 | 档位 | 打击 | 剩余 | 减伤 | 备注 |')
    L.append('|---|---|---|---|---|---|')
    for r in ts['rows']:
        L.append('| %s | %s | %.0f | **%.1f** | %.1f%% | %s |'
                 % (r['bucket'], r['zh'], r['dmg'], r['remain'], r['cut_pct'], r['note']))
    L.append('')
    e = ts['ehp']
    L.append('- 等权平均剩余 **%.1f%%** ⇒ **等效生命 %.0f**（HP %.0f）'
             % (e['avg_remain'] * 100, e['ehp'] or 0, hp))
    L.append('- ⚠ 这些档的数值是**示意**（「终极杂兵一次命中」的常见量级），'
             '**不是**某只怪的真值；等权也不代表实际打击构成。')
    L.append('')

    # ---- 四、边际
    L.append('## 四、边际收益（基准打击 %.0f %s）' % (args.dmg, dt))
    L.append('')
    mg = DF.marginal(panel, dt, enemy_oa=enemy_oa, base_dmg=args.dmg)
    L.append('基准剩余 **%.1f**（起点 %.0f）：' % (mg['base_remain'], args.dmg))
    L.append('')
    L.append('| 加什么 | 剩余变化 |')
    L.append('|---|---|')
    for nm, v in mg['items']:
        L.append('| %s | **−%.1f** |' % (nm, v))
    L.append('')
    L.append('> ⚠ 若「+护甲」为 **0**，说明该打击档落在 **DLEP 区间**（伤害 ≤ 护甲）—— '
             '此时护甲**值**不影响结果（只看吸收率），这是官方两条公式的真实形状，不是 bug。')
    L.append('')

    # ---- 五、如实标注
    L.append('## 五、如实标注（不静默）')
    L.append('')
    L.append('| 项 | 内容 |')
    L.append('|---|---|')
    L.append('| ⛔ 数据缺口 | **敌方打击的绝对量**缺席（`monsterdb` 未抽取）⇒ 本表只给传递函数 |')
    drop = panel.get('dropped_debuffs') or []
    L.append('| ★ 已剔除（降敌抗减益） | %s |'
             % ('、'.join('`%s`' % os.path.basename(r) for r in drop) if drop else '无'))
    cf = panel.get('cond_fields') or {}
    L.append('| ★ 条件性来源 | %s |'
             % ('、'.join('%s %s' % (k, v) for k, v in cf.items()) if cf else '无'))
    L.append('| 第 ③ 层（格挡） | 口径**待核准**（GD 的格挡吸收率尚无实测）｜ 本档位：%s |'
             % ('走公式' if panel['block_chance'] else '**跳过（未持盾）**'))
    L.append('')
    L.append('> **已剔除项说明**：`Skill_Attack*`（减益技能）里的 `defensive*` 字段表示'
             '**目标的**抗性（如「刺客的标记」「刺骨战吼」），不是我方掉抗 —— '
             '无脑累加会让面板整块失真。判据见 `gd/defense.py::applies_to_self`。')
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser(description='防御减伤审计（我方挨打）')
    ap.add_argument('char', help='角色名（如 Sam）')
    ap.add_argument('--dmg', type=float, default=5000.0, help='单次打击值（默认 5000）')
    ap.add_argument('--dtype', default='physical',
                    help='伤害桶（gd/rr.py 的桶名：physical/fire/pierce/bleeding/poisondot…）')
    ap.add_argument('--enemy', default='', help='敌方真值怪（如 m1281 = 罗卡）；给了才走第 ② 层')
    ap.add_argument('--difficulty', type=int, default=3, help='难度 1/2/3（默认 3 = 终极）')
    ap.add_argument('--flat-pool', type=float, default=None,
                    help='可用点数吸收池（第 ⑨ 层）；默认用面板值')
    ap.add_argument('--arch', default='werewolf', help='形态（仅用于取面板 DA/OA）')
    ap.add_argument('--md', action='store_true', help='输出 Markdown 全文（默认也输出）')
    ap.add_argument('--out', default='', help='落盘路径')
    a = ap.parse_args()

    c = D.load_char(a.char)
    rep = _rep(c, a.arch)
    P = DF.build(c, c['db'], rep, difficulty=a.difficulty)
    hp0, hp = _hp(a.char, P)

    enemy_oa, enemy_label = None, ''
    if a.enemy:
        try:
            enemy_oa = EN.oa_of(a.enemy, c.get('level') or 100)
            enemy_label = a.enemy
        except Exception as e:                                     # noqa: BLE001
            print('⚠ 取不到 %s 的 OA：%s（第 ② 层跳过）' % (a.enemy, e), file=sys.stderr)

    txt = render(a.char, P, hp0, hp, a, enemy_oa=enemy_oa, enemy_label=enemy_label)
    print(txt)
    if a.out:
        Path(a.out).write_text(txt, encoding='utf-8')
        print('\n→ %s' % a.out)


if __name__ == '__main__':
    main()
