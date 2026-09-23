# -*- coding: utf-8 -*-
"""对比两个存档的解析结果，逐字段打印差异（用于判断游戏自己改了什么）"""
import os
import sys

from . import core as S
from .. import _timing as TM



def walk(a, b, path, out, maxdepth=4, depth=0):
    if depth > maxdepth:
        if a != b:
            out.append((path, a, b))
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if isinstance(k, str) and k.startswith('_off'):
                continue
            if k not in a or k not in b:
                out.append((path + '/' + str(k), a.get(k, '<无>'), b.get(k, '<无>')))
                continue
            walk(a[k], b[k], path + '/' + str(k), out, maxdepth, depth + 1)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append((path + '/长度', len(a), len(b)))
        for i in range(min(len(a), len(b))):
            walk(a[i], b[i], '%s[%d]' % (path, i), out, maxdepth, depth + 1)
    else:
        if isinstance(a, float) and isinstance(b, float):
            if abs(a - b) > 1e-4:
                out.append((path, a, b))
        elif a != b:
            out.append((path, a, b))


def main(pa, pb, label=''):
    da = S.parse(pa)
    db = S.parse(pb)
    print('A =', os.path.basename(os.path.dirname(pa)), os.path.getsize(pa), '字节')
    print('B =', os.path.basename(os.path.dirname(pb)), os.path.getsize(pb), '字节')
    print()
    if da['level'] != db['level'] or da['classes'] != db['classes']:
        print('头部: 等级 %s->%s  职业 %s->%s' % (da['level'], db['level'], da['classes'], db['classes']))
    out = []
    walk(da['block_map'], db['block_map'], '', out, maxdepth=3)
    print('差异 %d 处:' % len(out))
    for p, x, y in out[:120]:
        sx = str(x)[:60]
        sy = str(y)[:60]
        print('  %-42s %-62s -> %s' % (p, sx, sy))
    if len(out) > 120:
        print('  ...（还有 %d 处）' % (len(out) - 120))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
