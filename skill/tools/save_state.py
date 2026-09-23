# -*- coding: utf-8 -*-
"""存档**状态指纹** —— 给「绑存档的锚点断言」加一道门。

为什么要它
----------
`data/regress/model_v2_anchor.json` 里的 Sam 锚点是**存档实测**值。
而存档会被**外部**改写：
  · 游戏自己存档（退出 / 自动存）
  · 玩家在游戏里重置/重分属性点
  · 落档（我们的工具）
⇒ 锚点必然失效。**那不是模型回归，是基准本身变了**。

2026-09-20 实测：用户在游戏里把属性从 `0/71/12` 退回 `1/0/0`（未分配 82），
自检里 **7 条锚点/标定断言同时变红** —— 看起来像模型崩了，其实是存档换了。
⇒ 与其让自检误报，不如**先比指纹**：不符就**明确跳过并说明**，而不是硬失败。

口径
----
指纹 = 12 个装备槽（主体/前缀/后缀/镶嵌/附魔）+ 两套武器 + 三围 + 未分配点 + 等级
逐项拼成字符串后取 md5 前 12 位。**只认结构性内容，不含时间戳** ⇒ 稳定可复现。

用法
----
    from save_state import fingerprint, state_of
    fingerprint(char)                 # 'a1b2c3d4e5f6'
    state_of(char)                    # 可读的明细 dict（排障用）
    fingerprint_from(save_dir, char)  # 指定存档目录（对备份用）
"""
from __future__ import annotations

import hashlib
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

_SLOT_FIELDS = ('basename', 'prefix', 'suffix', 'relic_name', 'augment_name')


def state_of(char='Sam', save_dir=None) -> dict:
    """存档的结构化状态（排障 / 造指纹都用它）。"""
    from gd import paths
    from gd.save import core as S

    def _read(sd):
        p = os.path.join(sd, 'main', '_%s' % char.lstrip('_'), 'player.gdc')
        if not os.path.isfile(p):
            return None
        return S.parse(p, record=False)

    if save_dir is not None:
        d = _read(save_dir)
    else:
        sd, _ = paths.save_dir()
        d = _read(sd) if sd else None
    if not d:
        return {}
    b2 = d.get('block_map', {}).get(2) or {}
    b3 = d.get('block_map', {}).get(3) or {}
    eq = []
    for it in (b3.get('equipment') or []):
        eq.append(tuple(str((it or {}).get(k) or '') for k in _SLOT_FIELDS))
    weapons = {}
    for tag in ('alt1', 'alt2'):
        weapons[tag] = [str((it or {}).get('basename') or '')
                        for it in (b3.get(tag) or [])]
    return {
        'level': d.get('level'),
        'physique': b2.get('physique'), 'cunning': b2.get('cunning'),
        'spirit': b2.get('spirit'),
        'attribute_points': b2.get('attribute_points'),
        'equipment': eq, 'weapons': weapons,
        'alt1_unused': b3.get('alt1_unused'),
        'alt2_unused': b3.get('alt2_unused'),
        'use_alt_weaponset': b3.get('use_alt_weaponset'),
    }


def fingerprint_of(state: dict) -> str:
    """结构化状态 → 12 位 md5（只吃结构，不吃时间）。"""
    if not state:
        return ''
    parts = ['lv=%s' % state.get('level'),
             'pcs=%s/%s/%s' % (state.get('physique'), state.get('cunning'),
                               state.get('spirit')),
             'ap=%s' % state.get('attribute_points'),
             'eq=' + '|'.join(','.join(t) for t in (state.get('equipment') or [])),
             'w=%s' % state.get('weapons'),
             'flags=%s/%s/%s' % (state.get('alt1_unused'), state.get('alt2_unused'),
                                 state.get('use_alt_weaponset'))]
    return hashlib.md5('\n'.join(parts).encode('utf-8')).hexdigest()[:12]


def fingerprint(char='Sam', save_dir=None) -> str:
    return fingerprint_of(state_of(char, save_dir))


def main():
    import argparse
    import json
    ap = argparse.ArgumentParser(description='存档状态指纹')
    ap.add_argument('char', nargs='?', default='Sam')
    ap.add_argument('--save-dir', default=None)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()
    st = state_of(a.char, a.save_dir)
    fp = fingerprint_of(st)
    if a.json or not st:
        print(json.dumps({'fingerprint': fp, 'state': st}, ensure_ascii=False, indent=1))
        return 0
    print('角色 %s ｜ 指纹 %s' % (a.char, fp))
    print('  等级 %s ｜ 三围 %s/%s/%s ｜ 未分配 %s'
          % (st['level'], st['physique'], st['cunning'], st['spirit'],
             st['attribute_points']))
    for tag, lst in (st['weapons'] or {}).items():
        print('  %s: %s' % (tag, '、'.join(x.rsplit('/', 1)[-1] for x in lst if x)))
    for i, t in enumerate(st['equipment'] or []):
        if t and t[0]:
            print('  槽%-2d %s' % (i, t[0].rsplit('/', 1)[-1]))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
