# -*- coding: utf-8 -*-
"""`res_audit.py` —— 抗性溢出审计（CLI）。

逻辑全在 `gd/resaudit.py`（**单一真源**，`gd/planreport.py` 与 `tools/plan_audit.py`
的报告节也调它）—— 本文件只做参数解析与打印，别在这里另写一套分解。

回答的问题是：**「满抗了，为什么还溢出这么多？」**
  · 溢出集中在**元素三抗**，因为 `defensiveElementalResistance` 是**一条字段给火/冰/电**
    ⇒ 只把最弱那项顶到 130，另两项必然同高，**无法精调**；
  · 溢出的抗性常常是**搭伤害/OA 便车**来的（「雷击的」「奥术平衡之」同时给 OA 与元素伤害）
    ⇒ 换掉只会掉输出 ⇒ **边际成本为零**。

用法
----
    $PY tools/res_audit.py [角色]                     # 读存档现穿装备
    $PY tools/res_audit.py Sam --plan data/plans/X.json
    $PY tools/res_audit.py Sam --plan X.json --slot 肩甲      # 只看某槽的来源明细
    $PY tools/res_audit.py Sam --plan X.json --detail 3       # 前三槽的来源明细
    $PY tools/res_audit.py Sam --json                          # 机读输出
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, SKILL)


def main() -> int:
    ap = argparse.ArgumentParser(description='抗性溢出审计（来源分解）',
                                 epilog=__doc__.split('用法\n----\n')[-1]
                                 if '用法' in (__doc__ or '') else None,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('char', nargs='?', default='Sam')
    ap.add_argument('--plan', default='', help='方案 JSON（默认读存档现穿的装备）')
    ap.add_argument('--slot', default='', help='只看某个槽位的来源明细')
    ap.add_argument('--top', type=int, default=0, help='供给排行只列前 N 个槽（0=全部）')
    ap.add_argument('--detail', type=int, default=0, help='逐来源明细：列前 N 个槽')
    ap.add_argument('--json', action='store_true', help='只输出 JSON')
    a = ap.parse_args()

    from gd import resaudit as RA

    if a.plan:
        plan = json.load(open(a.plan, encoding='utf-8'))
        per = RA.per_slot_from_plan(plan)
    else:
        from gd import dps as D
        c = D.load_char(a.char)
        plan = None
        per = RA.per_slot_from_folded(c.get('folded') or {})

    tot = RA.totals(per)
    ov = {t: tot.get(t, 0) - RA.NEED[t] for t in RA.TYPES}

    if a.json:
        print(json.dumps({
            'char': a.char, 'plan': a.plan or '(存档现穿)',
            'per_slot': {k: dict(v) for k, v in per.items()},
            'total': dict(tot), 'need': RA.NEED, 'overflow': ov,
            'overflow_sum': sum(v for v in ov.values() if v > 0),
        }, ensure_ascii=False, indent=1))
        return 0

    ranked = sorted(per.items(), key=lambda x: -sum(x[1].values()))
    print('=== 逐槽抗性（%s）===' % (a.plan or '存档现穿'))
    order = list(RA.DIMMAP)
    hdr = '%-8s' % '槽位' + ''.join('%7s' % t for t in order)
    print(hdr)
    print('-' * len(hdr))
    for slot in per:
        print('%-8s' % slot + ''.join('%7.0f' % per[slot].get(t, 0) for t in order))
    print('-' * len(hdr))
    print('%-8s' % '合计' + ''.join('%7.0f' % tot.get(t, 0) for t in order))
    print('%-8s' % '需求' + ''.join('%7d' % RA.NEED[t] for t in order))
    print('%-8s' % '溢出' + ''.join('%7.0f' % ov[t] for t in order))
    print()
    print('溢出合计 **%.0f** 点 ｜ 上排 %.0f ｜ 下排 %.0f'
          % (sum(v for v in ov.values() if v > 0),
             sum(v for t, v in ov.items() if t in RA.TOP and v > 0),
             sum(v for t, v in ov.items() if t not in RA.TOP and v > 0)))
    tight = sorted(RA.TYPES, key=lambda t: tot.get(t, 0) - RA.NEED[t])[:4]
    print('★ 真正「卡在线上」的维度：%s'
          % '、'.join('%s %+.0f' % (t, tot.get(t, 0) - RA.NEED[t]) for t in tight))
    print('★ 溢出的抗性在游戏里**完全无效**（80% 是硬上限，不转化）。')
    print()

    print('=== 各槽抗性供给排行 ===')
    for slot, d in (ranked[:a.top] if a.top else ranked):
        print('  %-8s 合计 %5.0f ｜ %s' % (slot, sum(d.values()),
              '、'.join('%s +%.0f' % (k, v) for k, v in d.most_common(5)) or '—'))
    print()

    if a.plan:
        n = a.detail or (1 if a.slot else 0)
        if n:
            print('=== 来源明细（抗性 ★ 与它搭便车的输出向字段）===')
            for slot, _d in ([('', None)] if a.slot else ranked[:n]):
                s = a.slot or slot
                print('── %s' % s)
                for tag, label, res, good in RA.sources(plan, s):
                    print('   %-4s %-24s' % (tag, label[:24]))
                    if res:
                        print('        抗性 : %s' % '、'.join('%s +%g' % (k, v)
                                                                 for k, v in res.items()))
                    if good:
                        print('        附带 : %s' % '、'.join('%s +%g' % (k, v)
                                                                 for k, v in good.items()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
