#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rr_items.py —— **「降低目标抗性」字段**全库清单与实例排行。

回答的问题
----------
「本 DB 里针对**怪物抗性**的字段到底有哪些？各自出现在谁身上、最强的是哪条？」

三族（口径与 `gd/rr.py` **逐字同源**，不另立标准）：

| 族 | 字段形态 | 游戏文案 | 结算 |
|---|---|---|---|
| **B** | 技能上 `defensive<类型>` 的**负值** | `−X% 穿刺抗性` | **叠加**（多条相加） |
| **C** | `offensive*ResistanceReductionPercentMin` | `X% Reduced target's Resistance` | **取最高** |
| **A** | `offensive*ResistanceReductionAbsoluteMin` | `X Reduced target's Resistance` | **取最高** |

★ 两个字段形态坑（都踩过）
--------------------------
① **B 族必须用精确匹配**（`k in DEF_BUCKET`）：`defensiveColdDuration` 是 debuff **时长**，
   一剥后缀就会误命中 `defensiveCold`。`gd/rr.py::rr_of` 正是这么分的（B 精确 / C·A 剥一层）。
② **C/A 族在库里只有 `…Min` 与 `…DurationMin` 两种形态**，基础名靠 `strip_suffix` 剥出来。
   统计时**必须排掉 `…DurationMin``** —— 那是时长，不排就会把「持续 20 秒」读成「减抗 20%」。

用法
----
    python tools/rr_items.py                 # 控制台报表
    python tools/rr_items.py --top 15        # 每族多看几条
    python tools/rr_items.py --write         # 落 data/rr_item_fields.md
    python tools/rr_items.py --family B      # 只看某一族
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, SKILL)

from gd import rr as RR                                    # noqa: E402

TYPES = {'physical': '物理', 'pierce': '穿刺', 'fire': '火焰', 'cold': '冰冷',
         'lightning': '闪电', 'poison': '毒素/酸液', 'bleeding': '流血',
         'vitality': '活力', 'aether': '虚化', 'chaos': '混乱'}
FAM_ZH = {'B': 'B 族 · 负 `defensive<类型>`（**叠加**）',
          'C': 'C 族 · `…ResistanceReductionPercentMin`（**取最高**）',
          'A': 'A 族 · `…ResistanceReductionAbsoluteMin`（**取最高**）'}


def source_of(rec):
    if '/itemskills' in rec:
        return '物品技能(装备授予)'
    if '/devotion/' in rec:
        return '星座 proc'
    if '/playerclass' in rec:
        return '专精技能'
    if '/nonplayerskills' in rec:
        return '怪物技能'
    return '其他'


def buckets_of(b):
    if b is RR._ALL:
        return '全部 10 种'
    if b is RR._ELEM:
        return '火/冰/电'
    if isinstance(b, str):
        return TYPES.get(b, b)
    return '/'.join(TYPES.get(x, x) for x in b)


def _is_duration(key):
    return key.endswith(('DurationMin', 'DurationMax', 'Duration'))


def scan():
    """扫全库 → {族: [记录, …]}，每条 = dict(值/名字/字段/来源/桶)。

    ★ 判据与 `gd/rr.py::rr_of` **逐字相同**：B 族精确匹配，C/A 族剥一层后缀；
      再额外排掉 `…DurationMin`（时长），否则数值与时长会混在一起。
    """
    p = os.path.join(SKILL, 'data', 'skills.json')
    sk = json.load(open(p, encoding='utf-8'))
    out = {'B': [], 'C': [], 'A': []}
    for rec, v in sk.items():
        stats = (v or {}).get('stats') or {}
        name = (v or {}).get('name') or rec.split('/')[-1]
        for key, raw in stats.items():
            if not isinstance(key, str):
                continue
            if key in RR.DEF_BUCKET:                    # B 族：精确匹配
                fam, buckets, field = 'B', RR.DEF_BUCKET[key], key
            else:
                if _is_duration(key):                   # 时长不是数值
                    continue
                hit = RR.FIELD_RR.get(RR.strip_suffix(key))
                if not hit or hit[1] == 'add':
                    continue
                fam = 'C' if hit[1] == 'max' else 'A'
                buckets, field = hit[0], key
            val = raw[0] if isinstance(raw, list) and raw else raw
            if not isinstance(val, (int, float)) or not val:
                continue
            if fam == 'B' and val >= 0:                 # 正值 = 自己的抗性
                continue
            out[fam].append({'value': abs(float(val)), 'name': name, 'field': field,
                             'rec': rec, 'source': source_of(rec),
                             'buckets': buckets_of(buckets)})
    return out


def report(rows, top=8, families=('B', 'C', 'A')):
    lines = []

    def w(s=''):
        lines.append(s)

    for fam in families:
        rs = rows[fam]
        w('')
        w('## %s ｜ 命中 **%d** 条字段实例' % (FAM_ZH[fam], len(rs)))
        w('')
        src = collections.Counter(x['source'] for x in rs)
        w('- 来源：' + ' ／ '.join('%s **%d**' % kv for kv in src.most_common()))
        fld = collections.Counter(x['field'] for x in rs)
        w('- 字段：%s' % '、'.join('`%s`×%d' % kv for kv in fld.most_common()))
        w('')
        w('| 值 | 名称 | 字段 | 抗性桶 | 来源 |')
        w('|---|---|---|---|---|')
        for x in sorted(rs, key=lambda y: -y['value'])[:int(top)]:
            w('| %.0f | %s | `%s` | %s | %s |'
              % (x['value'], x['name'][:22], x['field'].replace('offensive', 'off.'),
                 x['buckets'], x['source']))
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--top', type=int, default=8)
    ap.add_argument('--family', default='BCA')
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--out', default='data/rr_item_fields.md')
    a = ap.parse_args()
    fams = tuple(c for c in a.family.upper() if c in 'BCA') or ('B', 'C', 'A')
    rows = scan()
    head = ['# 降低目标抗性的字段清单（物品 / 技能）', '',
            '> 自动生成：`python tools/rr_items.py --write`。',
            '> 判据与 `gd/rr.py::rr_of` 逐字同源；`GD` 里这三族的**结算顺序是 B → C → A**，',
            '> 且**敌抗可成负、不锁 0**（见 `docs/rr_mechanics.md`）。']
    lines = head + report(rows, top=a.top, families=fams)
    txt = '\n'.join(lines) + '\n'
    print(txt)
    if a.write:
        p = os.path.join(SKILL, a.out)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'w', encoding='utf-8').write(txt)
        print('已落盘：%s' % a.out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
