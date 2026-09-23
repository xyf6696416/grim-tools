# -*- coding: utf-8 -*-
"""按「装备的官方属性需求」重排属性点 —— 让全套装备都能穿上。

    python tools/fix_wearability.py Sam                # 只出方案（预演）
    python tools/fix_wearability.py Sam --apply        # 备份后写盘
    python tools/fix_wearability.py Sam --spread       # 余额按形态倾向分摊

★ 求解与写盘都**复用库内的唯一实现**，本脚本不再自己做数学：
    求解 → `gd.reqfit.solve()`
    写盘 → `gd.save.patch`（`--fit-gear`，自带备份 + 往返校验 + 可穿性复检）

规则（详见 `gd/reqfit.py` 与 SKILL §3.8）
----------------------------------------
* **严格等级预算**：可用点数 = 已花 + 未分配（不改总点数，不凭空加点）。
* 面板 = `(50 + 8×加点 + 精通逐级 + 装备平值) × (1 + 装备%值/100)`。
* 先满足 体格/狡诈/精神 三阈值，余额投输出属性（`--spread` 则按形态倾向分摊）。
* `--buffer N`：体格/精神各多留 N 点余量（默认 1）。
* `--att-safety N`：饰品提示框行数的安全余量（默认 6）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from gd import reqfit as RF                   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('char')
    ap.add_argument('--apply', action='store_true', help='备份后写入')
    ap.add_argument('--buffer', type=int, default=1, help='体格/精神各留几点余量')
    ap.add_argument('--att-safety', type=int, default=6, dest='att_safety',
                    help='饰品提示框行数的安全余量（默认 6）')
    ap.add_argument('--prefer', default=None, dest='prefer',
                    choices=['physique', 'cunning', 'spirit'],
                    help='余额投给哪个属性（默认按形态 attribute_bias）')
    ap.add_argument('--spread', action='store_true',
                    help='余额按形态倾向分摊（默认全给输出属性）')
    a = ap.parse_args()

    r = RF.solve(a.char, buffer=a.buffer, att_safety=a.att_safety,
                 prefer=a.prefer, spread=a.spread)
    print('=' * 78)
    print('%s  属性重排方案' % a.char)
    print('=' * 78)
    print(RF.describe(r, '  '))
    print()

    if not r.feasible:
        print('✗ 预算不够（%s）。强写只能得到「最接近」的解；'
              '要真穿得上得换件或加点。' % ', '.join(
                  '%s 还差 %d 点' % (RF.ZH[k], v) for k, v in r.short.items()))
    if not r.changed:
        print('✓ 当前三围已满足全套需求，无需改动。')
        return 0
    if not a.apply:
        print('（预演，未写盘。加 --apply 写入）')
        return 0

    cmd = [sys.executable, '-m', 'gd.save.patch', '--char', a.char,
           '--fit-buffer', str(a.buffer), '--att-safety', str(a.att_safety),
           '--apply']
    if a.prefer:
        cmd += ['--fit-prefer', a.prefer]
    if a.spread:
        cmd += ['--fit-spread']
    if not r.feasible:
        cmd += ['--fit-allow-short']
    print('执行：%s' % ' '.join(cmd))
    return subprocess.call(cmd, cwd=HERE)


if __name__ == '__main__':
    sys.exit(main())
