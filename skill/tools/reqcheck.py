# -*- coding: utf-8 -*-
"""可穿性体检：角色的**面板属性** vs 装备的**官方属性需求**。

    python tools/reqcheck.py <角色> [--att-safety N] [--json 输出.json]

输出：逐槽需求、缺口、以及「面板至少要多少才能全套穿上」。

口径由 `gd.reqfit` 唯一提供（面板 = 存档 + 精通逐级曲线 + 装备平值，再 ×(1+装备%值/100)；
需求 = 官方 `itemCostFormulae`，含武器）。本脚本只负责**打印**。
"""
from __future__ import annotations
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from gd import DB, paths                       # noqa: E402
from gd import req as RQ                       # noqa: E402
from gd import reqfit as RF                    # noqa: E402
from gd import rotation as R                   # noqa: E402
from gd import dps as D                        # noqa: E402
from gd.save import core                       # noqa: E402

ZH = RF.ZH


def gear_rows(char):
    """装备槽 → 需求行（**含全部武器套**，槽位名带套别，不再一律叫「武器套2」）。"""
    return RF.gear_from_save(char)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('char')
    ap.add_argument('--att-safety', type=int, default=0,
                    help='饰品属性行数的安全余量（保守估计用）')
    ap.add_argument('--json', default='')
    ap.add_argument('--save-dir', default=None,
                    help='存档目录（默认按 paths.save_dir() 探测）')
    a = ap.parse_args()

    db = DB.load()
    with paths.save_dir_override(a.save_dir):
        c = D.load_char(a.char)
        rows = gear_rows(a.char)
        need = RQ.need_of_rows(rows, att_safety=a.att_safety)
        A, B, mast = RF.panel_coeffs(c)
        bio = c.get('bio') or {}
        pts = {k: (float(bio.get(k) or 0.0) - RF.BASE) / RF.PER for k in RF.KEYS}
        panel = {k: A[k] + B[k] * pts[k] for k in RF.KEYS}

    print('=' * 78)
    print('%s  lv%s   %s' % (a.char, c['level'], '+'.join(c['classes'] or [])))
    print('=' * 78)
    print('  存档属性 : 体格 %.0f  狡诈 %.0f  精神 %.0f   （已投点 %.0f/%.0f/%.0f）'
          % (bio.get('physique', 0), bio.get('cunning', 0), bio.get('spirit', 0),
             pts['physique'], pts['cunning'], pts['spirit']))
    print('  专精     : %s' % ', '.join('%s lv%d' % (k, v)
          for k, v in sorted(RF.mastery_levels(c).items())))
    print('  ★ 面板   : 体格 %.0f  狡诈 %.0f  精神 %.0f'
          % (panel['physique'], panel['cunning'], panel['spirit']))
    print()
    print('%-7s %-16s %-7s %-6s %-8s %-8s %-8s %s'
          % ('槽', '装备', 'set', 'ilvl', '需体格', '需狡诈', '需精神', '判定'))
    worst = {'physique': 0, 'cunning': 0, 'spirit': 0}
    fails, unknown = [], []
    for r in need['items']:
        for k in worst:
            worst[k] = max(worst[k], r[k])
        label = r['record'].rsplit('/', 1)[-1].replace('.dbr', '')
        short = {ZH[k]: r[k] - panel[k] for k in RF.KEYS
                 if r[k] and r[k] > panel[k]}
        if short:
            fails.append((r['slot'], label, short))
        if r.get('confidence') == 'unknown':
            unknown.append(r['slot'])
            mark = '? 未评估'
        else:
            mark = ('✗ ' + ' '.join('%s缺%.0f' % (k, v) for k, v in short.items())
                    ) if short else '✓'
        print('%-7s %-16s %-7s %-6s %-8s %-8s %-8s %s'
              % (r['slot'], label[:15], r.get('set') or '-', r['ilvl'],
                 r['physique'] or '-', r['cunning'] or '-', r['spirit'] or '-', mark))
    print()
    print('全套最高需求 : 体格 %d  狡诈 %d  精神 %d'
          % (worst['physique'], worst['cunning'], worst['spirit']))
    if unknown:
        print('⚠ %d 件需求**未评估**（认不出类型）：%s'
              % (len(unknown), ', '.join(unknown)))
    if fails:
        print('★ 穿不上 %d 件：%s' % (len(fails),
              '、'.join('%s(%s)' % (s, n) for s, n, _ in fails)))
        for k in RF.KEYS:
            if worst[k] > panel[k]:
                print('   → %s 还差 %.0f（需 %d，现有 %.0f）＝还需 %.0f 点属性点'
                      % (ZH[k], worst[k] - panel[k], worst[k], panel[k],
                         -(-(worst[k] - panel[k]) // 8)))
        print('   自动修： python -m gd.save.patch --char %s --fit-gear --apply'
              % a.char.lstrip('_'))
    else:
        print('★ 全部可穿 ✓' if not unknown else '★ 已评估的全部可穿（但有未评估件）')
    if a.json:
        json.dump({'panel': panel, 'worst': worst, 'points': pts,
                   'att_safety': a.att_safety,
                   'rows': [{'slot': r['slot'], 'record': r['record'],
                             'ilvl': r['ilvl'], 'set': r.get('set'),
                             'confidence': r.get('confidence'),
                             'physique': r['physique'], 'cunning': r['cunning'],
                             'spirit': r['spirit']} for r in need['items']]},
                  open(a.json, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        print('已写入', a.json)


if __name__ == '__main__':
    main()
