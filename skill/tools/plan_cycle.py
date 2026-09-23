# -*- coding: utf-8 -*-
"""plan_cycle.py —— 生成 **伤害循环文档**（每个 BD 必带）

用户口径（2026-09-20）：
  「每次出新 db 的时候**必须**带一份专门的伤害循环文档。」

与 `tools/plan_audit.py` 的分工
------------------------------
    `plan_audit.py`  = **体检**：四层口径 / 属性重排 / 口径敏感性（数字对不对）
    `plan_cycle.py`  = **档案**：逐技能 · 逐伤害类型 · 逐来源 · 逐乘区 · 逐触发关系
                       （数字**怎么来的**）

两者消费同一个 `plan_dps.dps_of()` ⇒ 数值不可能分叉。

用法
----
    # ① 直接生成到 stdout
    python tools/plan_cycle.py Sam data/plans/lv73_A/arch_werewolf.json \
        --arch werewolf --enemy m3955

    # ② 落到文件（推荐：与方案报告同目录）
    python tools/plan_cycle.py Sam data/plans/X.json --arch werewolf \
        --enemy m3955 --out data/plans/final/CYCLE_werewolf.md

    # ③ 注入加点口径（B 口径）
    python tools/plan_cycle.py Sam data/plans/X.json --arch wolf_nightblade \
        --alloc data/scratch/alloc71_wolf_nightblade.json --out ...

参数
----
    --arch   流派（默认读 `GD_ARCHETYPE`，再退回 `plan_dps` 的自动猜测）
    --alloc  形态加点 JSON（= `GD_SKILL_JSON`）。**不传 ⇒ 用真实存档加点**。
    --enemy  敌方档（`gd/enemy.py` 口径：五档 / `m<id>` 真值怪 / 等级池）。
    --out    输出文件；不传则打 stdout。
    --no-procs  跳过触发关系章节（方案里没有装备 id 时用）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'tools'))


def main() -> int:
    ap = argparse.ArgumentParser(description='生成伤害循环文档（每个 BD 必带）')
    ap.add_argument('char')
    ap.add_argument('plan')
    ap.add_argument('--arch', default='')
    ap.add_argument('--alloc', default='', help='形态加点 JSON（= GD_SKILL_JSON）')
    ap.add_argument('--enemy', default='', help='敌方档（gd/enemy.py 口径）')
    ap.add_argument('--out', default='', help='输出文件（不传打 stdout）')
    ap.add_argument('--title', default='', help='标题前缀（默认取形态 label）')
    ap.add_argument('--no-procs', action='store_true',
                    help='跳过「触发关系」章节')
    a = ap.parse_args()

    if a.alloc:
        os.environ['GD_SKILL_JSON'] = a.alloc
    if a.enemy:
        os.environ['GD_ENEMY_PROFILE'] = a.enemy

    import plan_dps as PD                                    # noqa: E402
    from gd import db as D                                   # noqa: E402
    from gd import gear as G                                 # noqa: E402
    from gd import dmgcycle as CY                            # noqa: E402
    from gd import enemy as ENM                              # noqa: E402

    with open(a.plan, encoding='utf-8') as fh:
        plan = json.load(fh)

    base = PD.dps_of(a.char, plan, a.arch)
    arch = a.arch or base.get('arch') or ''

    db = items = None
    if not a.no_procs:
        try:
            db = D.DB.load()
            items = G.load_items()
        except Exception as _e:                              # noqa: BLE001
            print('⚠ 触发关系数据不可用：%s' % _e, file=sys.stderr)

    # 是否有盾（Block 类触发需要）
    has_shield = any(((items or {}).get(g) or {}).get('l') == 'c27'
                     for g in (plan.get('副手') or []) if g) if items else False

    ep = ENM.get_profile(a.enemy or None, base.get('level') or 100)
    enemy_label = (a.enemy or os.environ.get('GD_ENEMY_PROFILE')
                   or (ep or {}).get('spec') or '(默认)')

    md = CY.render(base, char=a.char, arch=arch, plan_path=a.plan,
                   db=db, items=items, plan=plan, enemy_label=enemy_label,
                   has_shield=has_shield,
                   argv='python tools/plan_cycle.py %s %s%s%s%s'
                        % (a.char, a.plan,
                           ' --arch %s' % a.arch if a.arch else '',
                           ' --alloc %s' % a.alloc if a.alloc else '',
                           ' --enemy %s' % a.enemy if a.enemy else ''))
    if a.title:
        md = md.replace('# 伤害循环文档 ·', '# %s ·' % a.title, 1)
    # 落盘时补一行生成时间（stdout 不补，保持可 diff）
    if a.out:
        stamp = time.strftime('%Y-%m-%d %H:%M')
        md = md.rstrip() + '\n\n---\n\n*生成于 %s ｜ 口径：面板 → 实战(含命中) ' \
             '→ 对怪(含减抗) → 过甲*\n' % stamp
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, 'w', encoding='utf-8') as fh:
            fh.write(md)
        print('伤害循环文档已落盘：%s（%d 行）' % (a.out, md.count('\n') + 1),
              file=sys.stderr)
        print('  面板 %s ｜ 实战 %s ｜ 对怪 %s ｜ 过甲 %s'
              % (base.get('dps_panel'), base.get('dps_real'),
                 base.get('dps_vs'), base.get('dps_final')), file=sys.stderr)
    else:
        sys.stdout.write(md)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
