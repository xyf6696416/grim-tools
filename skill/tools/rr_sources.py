#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""rr_sources.py —— **当前这套 BD 的减抗来源明细**（逐装备部件 / 逐技能 / 逐星座）。

回答的问题
----------
「我这套 BD 里，具体**哪一件装备的哪个部件**、**哪条技能**、**哪个星座 proc**
 在降低怪物抗性？」

与 `python -m gd rr <角色>` 的区别：那个给的是**聚合值**
（「技能/星座：穿刺 −38%＋最多 −20」），看得出总量、看不出**是谁供的**。
本工具把它拆到**来源级**：

| 层 | 拆法 |
|---|---|
| 装备 | 逐槽 → **逐部件**（底材 / 镶嵌 / 附魔 / 前缀 / 后缀），走 `save_plan.build` |
| 技能 | 逐记录 → 分「专精技能 / 装备授予技能 / 星座 proc」三类 |

判据与 `gd/rr.py::rr_of` **逐字同源**：B 族（技能上 `defensive<类型>` 负值）精确匹配、
C·A 族（`offensive*ResistanceReduction…`）剥一层后缀。

用法
----
    python tools/rr_sources.py                 # Sam 存档
    python tools/rr_sources.py Sam --no-weapon # 不含主手/副手
    python tools/rr_sources.py Sam --write     # 落 data/rr_sources_Sam.md
"""
from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, SKILL)
sys.path.insert(0, HERE)

from gd import rr as RR                                      # noqa: E402
from gd import resaudit as RA                                # noqa: E402
from gd import savemap as SM                                 # noqa: E402

FAM = {'add': 'B 叠加', 'max': 'C 取最高', 'flat': 'A 取最高'}


def _parts(pack):
    """把 `rr_of` 的包摊成 [(族, 桶, 值), …]（只留非零）。"""
    out = []
    for fam in ('add', 'max', 'flat'):
        for b, v in (pack.get(fam) or {}).items():
            if v:
                out.append((FAM[fam], b, float(v)))
    return out


def scan(char='Sam', with_weapon=True):
    """返回 {'gear': [...], 'skill': [...]}，每条 = 一个来源。"""
    from gd import dps as D
    import save_plan as SP

    items = SM.gt_items()
    db = D.DB.load() if hasattr(D, 'DB') else None
    try:
        from gd import DB as _DBM
        db = _DBM.load()
    except Exception:                                        # noqa: BLE001
        pass

    # ---------------- ① 装备：逐槽 → 逐部件
    plan = SP.build(char, with_weapon=with_weapon)
    gear = []
    for slot, ids in (plan or {}).items():
        for i, gid in enumerate(ids or []):
            if not gid or i >= len(RA.SLOT_FIELDS):
                continue
            f = items.get(gid)
            if not isinstance(f, dict):
                continue
            rows = _parts(RR.rr_of(f))
            if not rows:
                continue
            try:
                label = db.name(gid) if db else gid
            except Exception:                                # noqa: BLE001
                label = gid
            gear.append({'slot': slot, 'part': RA.SLOT_ZH[RA.SLOT_FIELDS[i]],
                         'name': str(label or gid), 'gid': gid, 'rows': rows})

    # ---------------- ② 技能：逐记录
    c = D.load_char(char)
    sk = {k: v for k, v in (c['skills'] or {}).items() if v > 0}
    for rec, extra in (c.get('skill_plus') or {}).items():
        if rec in sk:
            sk[rec] += extra
    zh = D.load_skills_zh()
    skill = []
    for rec, lv in sk.items():
        if '/devotion/' in rec:
            kind = '星座 proc'
        elif '/itemskills' in rec:
            kind = '装备授予技能'
        elif '/playerclass' in rec:
            kind = '专精技能'
        else:
            kind = '其他'
        rows = _parts(RR.rr_of(c['db'].fields(rec) or {}, int(lv)))
        if not rows:
            continue
        skill.append({'kind': kind, 'name': D.zh_name(rec, zh) or rec.split('/')[-1],
                      'level': int(lv), 'rec': rec, 'rows': rows})
    order = {'专精技能': 0, '装备授予技能': 1, '星座 proc': 2, '其他': 3}
    skill.sort(key=lambda x: (order.get(x['kind'], 9), -max(r[2] for r in x['rows'])))

    # 标出**当前启用**的武器套（与 `gd/build._active_weapon_set` 同判据）——
    # 一个角色有两套武器，拿错一套会让整张明细指向不存在的装备。
    ws = 'alt1'
    try:
        from gd import paths as _P
        from gd.build import _active_weapon_set
        from gd.save import core as _core
        _k = next((x for x in _P.characters()
                   if x.lstrip('_').lower() == char.lstrip('_').lower()), None)
        if _k:
            _b3 = _core.parse(str(_P.characters()[_k] / 'player.gdc'))['block_map'][3]
            ws = _active_weapon_set(_b3)
    except Exception:                                        # noqa: BLE001
        pass
    return {'char': char, 'gear': gear, 'skill': skill, 'weapon_set': ws}


def _totals(res):
    """按模型口径合计：**B 累加、C / A 各取最大**（与 `gd/rr.combine` 一致）。"""
    add, mx, fl = {}, {}, {}
    for s in list(res['gear']) + list(res['skill']):
        for fam, b, v in s['rows']:
            if fam.startswith('B'):
                add[b] = add.get(b, 0.0) + v
            elif fam.startswith('C'):
                mx[b] = max(mx.get(b, 0.0), v)
            else:
                fl[b] = max(fl.get(b, 0.0), v)
    return add, mx, fl


def render(res):
    L = []
    w = L.append
    w('## %s ｜ 减抗来源明细' % res['char'])
    w('')
    w('> 武器套：**%s**（与 `gd.build._active_weapon_set` 同判据）｜ '
      '装备名称是**底材**中文名' % res.get('weapon_set', 'alt1'))
    w('')
    w('### ① 装备（逐槽 → 逐部件）')
    w('')
    if not res['gear']:
        w('_（本套装备的底材/镶嵌/附魔/词缀上没有减抗字段）_')
    else:
        w('| 槽位 | 部件 | 名称 | 族 | 抗性桶 | 值 |')
        w('|---|---|---|---|---|---|')
        for g in res['gear']:
            for fam, b, v in g['rows']:
                w('| %s | %s | %s | %s | %s | %s |'
                  % (g['slot'], g['part'], g['name'][:26], fam,
                     RR.BUCKET_ZH.get(b, b),
                     ('−%.0f%%' % v) if fam.startswith('B') else '%.0f' % v))
    w('')
    w('### ② 技能 / 星座')
    w('')
    if not res['skill']:
        w('_（无）_')
    else:
        w('| 类别 | 技能 | 等级 | 族 | 抗性桶 | 值 |')
        w('|---|---|---|---|---|---|')
        for s in res['skill']:
            for i, (fam, b, v) in enumerate(s['rows']):
                w('| %s | %s | %s | %s | %s | %s |'
                  % (s['kind'] if i == 0 else '', s['name'][:22] if i == 0 else '',
                     ('%d' % s['level']) if i == 0 else '',
                     fam, RR.BUCKET_ZH.get(b, b),
                     ('−%.0f%%' % v) if fam.startswith('B') else '%.0f' % v))
    add, mx, fl = _totals(res)
    w('')
    w('### ③ 合计（**B 累加 / C·A 取最高** —— 与模型口径一致）')
    w('')
    w('| 抗性桶 | B（叠加） | C（最高 %） | A（最高绝对值） |')
    w('|---|---|---|---|')
    for b in sorted(set(add) | set(mx) | set(fl)):
        w('| %s | %s | %s | %s |'
          % (RR.BUCKET_ZH.get(b, b),
             ('−%.0f%%' % add[b]) if add.get(b) else '—',
             ('−%.0f%%' % mx[b]) if mx.get(b) else '—',
             ('−%.0f' % fl[b]) if fl.get(b) else '—'))
    return L


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('char', nargs='?', default='Sam')
    ap.add_argument('--no-weapon', action='store_true')
    ap.add_argument('--write', action='store_true')
    ap.add_argument('--out', default='')
    a = ap.parse_args()
    res = scan(a.char, with_weapon=not a.no_weapon)
    lines = render(res)
    txt = '\n'.join(lines) + '\n'
    print(txt)
    if a.write:
        out = a.out or ('data/rr_sources_%s.md' % a.char.lstrip('_'))
        p = os.path.join(SKILL, out)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, 'w', encoding='utf-8').write(txt)
        print('已落盘：%s' % out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
