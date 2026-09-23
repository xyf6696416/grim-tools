#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""plan_to_save_skill.py —— 把「形态加点 + 星座方案」转成 `gd.save.skill` 的可落档方案。

为什么需要这个工具：落档流程（`SKILL.md` ④b 第 ③ 步）写着
    `python -m gd.save.skill apply <加点方案.json>`，格式 = `{char, note, set:[...]}`
但**没有任何工具生成它** —— 过去是手写的，容易漏项（漏掉的记录等级不会被改，
看着成功、实际没落上）。这里按**差量**精确生成。

两个输入的语义**不同**，别混：

  · `--alloc`（形态加点，如 `data/plans/pivot/alloc_legal.json`）
    对应 `GD_SKILL_JSON` 的**替换**语义（`gd/dps.py`：`skills = {注入的}; skills.update(存档星座)`）
    ⇒ **存档里 level>0 而它没有的技能会被置 0**。所以它必须是**完整的技能/精通清单**。
    ⚠ 它**不能含星座**（含了就是「并集叠加」而不是替换，陷阱 #69-B）。

  · `--dev`（星座方案，如 `data/plans/pivot/devotion_star_plan.json`）
    取它的 `records` 字段，**全部按 level 1** 写入；存档里有、它没有的星座记录 → 置 0。
    星点写的是 `devotion_level`（由 `gd/save/skill.py` 负责），陷阱 #67。

用法：
    python tools/plan_to_save_skill.py Sam \
        --alloc data/plans/pivot/alloc_legal.json \
        --dev   data/plans/pivot/devotion_star_plan.json \
        --out   data/plans/pivot/save_skill_final.json --note "opt_lv73_legal 落档"
    # 先看 diff，再 python -m gd.save.skill apply <out>
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gd import paths                                    # noqa: E402
from gd.save import core as S                           # noqa: E402

DEV = '/devotion/'
# 装备 / 镶嵌 / 附魔 / 圣物 / 套装授予的技能 —— **不属于「加点」**，不该写进存档技能表。
# ⚠ 只匹配 `records/skills/itemskills/`；`itemskillsgdx3/potionmodifiers/…`（药水改造）
#   是**真正的存档条目**，必须保留 —— 所以正则里带上结尾斜杠。
GRANTED_RE = re.compile(r'/itemskills/')


def live_skills(char):
    sd, _ = paths.save_dir()
    key = char if char.startswith('_') else '_' + char
    p = os.path.join(sd, 'main', key, 'player.gdc')
    if not os.path.isfile(p):
        raise SystemExit('✗ 找不到存档：%s' % p)
    d = S.parse(p)
    return {s['skill']: int(s['level'])
            for s in d['block_map'][8]['skills'] if s['level'] > 0}


def short(rec):
    return rec.replace('records/skills/', '')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('char')
    ap.add_argument('--alloc', default='', help='形态加点 JSON（技能/精通，**不含星座**）')
    ap.add_argument('--dev', default='', help='星座方案 JSON（取 records，全按 1 级）')
    ap.add_argument('--keep-dev', action='store_true',
                    help='不动星座（只落技能）')
    ap.add_argument('--keep-granted', action='store_true',
                    help='不过滤「装备授予的技能」（默认过滤；见下方说明）')
    ap.add_argument('--out', default='')
    ap.add_argument('--note', default='')
    a = ap.parse_args()

    cur = live_skills(a.char)
    target = {}

    # ⚠⚠ **装备授予的技能不属于「加点」**（2026-09-21）：`gd/dps.py::load_char` 会把
    #   「装备/镶嵌/圣物授予的**常驻类**技能」折进 `skills`（陷阱 #71）—— 于是
    #   `tools/tune_skills.py` 读到的 `skills` 里**混着它们**，写回方案就等于把
    #   「装备给的」当成「玩家点的」。
    #   后果：① 摘掉那件装备后技能仍在（游戏里不该有）；② 存档技能表被污染。
    #   折叠用的是 `max()` ⇒ 不写档也**不影响伤害数值**，所以这是个**只在落档时才暴露**的错。
    #   ⇒ 一律剔除 `records/skills/itemskills/`；`itemskillsgdx3/potionmodifiers/` 是
    #     真正的存档条目（药水改造），**保留**。
    if a.alloc:
        alloc = json.load(open(a.alloc, encoding='utf-8'))
        granted = sorted(r for r, lv in alloc.items() if lv and GRANTED_RE.search(r))
        if granted and not a.keep_granted:
            print('  ⚠ 剔除 %d 条**装备授予**的技能（不属于加点，写档会「摘装备后技能还在」）：%s'
                  % (len(granted),
                     '、'.join(os.path.basename(r)[:-4] for r in granted)))
            alloc = {r: lv for r, lv in alloc.items() if r not in set(granted)}
            print()
        stray = [r for r, lv in alloc.items() if lv and DEV in r]
        if stray:
            raise SystemExit('✗ --alloc 里含 %d 条星座记录（%s…）—— 星座请走 --dev，'
                             '否则是「并集叠加」而不是替换（陷阱 #69-B）'
                             % (len(stray), short(stray[0])))
        target.update({r: int(lv) for r, lv in alloc.items() if int(lv) > 0})

    if a.keep_dev:
        target.update({r: lv for r, lv in cur.items() if DEV in r})
    elif a.dev:
        dev = json.load(open(a.dev, encoding='utf-8'))
        recs = dev.get('records') or []
        if not recs:
            raise SystemExit('✗ --dev 文件没有 records 字段（%s）' % a.dev)
        for r in recs:
            target[r] = 1
        # 星座的 proc 记录（`*_skill.dbr`）也必须在方案里，否则会被下面判成「该退」
        for r, lv in cur.items():
            if DEV in r and r.endswith('_skill.dbr'):
                target.setdefault(r, lv)

    # ---- 差量 ----------------------------------------------------------
    # ⚠ 必须**按并集一次算**：分两趟（先扫存档再扫目标）会让「升级」的记录
    #   先被写成「撤点 0」，再被写成「调整 N」—— 去重时若保留先写入的那条，
    #   主输出技能就被**静默清零**了（本工具第一版就是这么错的：`leap1 6→8`
    #   被算成 `leap1 6→0`）。
    entries = []
    for r in sorted(set(cur) | set(target)):
        before, after = cur.get(r, 0), target.get(r, 0)
        if before == after:
            continue
        why = '撤点（不在目标清单里）' if not after else ('新增' if not before else '调整')
        entries.append((r, after, why))

    set_list = [{'skill': r, 'level': lv, 'why': why} for r, lv, why in entries]

    print('=' * 92)
    print('角色 %s ｜ 存档非零技能 %d ｜ 目标 %d ｜ 差量 %d 条'
          % (a.char, len(cur), len(target), len(set_list)))
    print('=' * 92)
    upsn = [e for e in set_list if DEV not in e['skill']]
    devn = [e for e in set_list if DEV in e['skill']]
    print('--- 技能/精通 %d 条 ---' % len(upsn))
    for e in upsn:
        print('  %-6s %-52s %s -> %s'
              % (e['why'], short(e['skill']), cur.get(e['skill'], 0), e['level']))
    print('--- 星座 %d 条（新增 %d / 退出 %d）---'
          % (len(devn), sum(1 for e in devn if e['why'] == '新增'),
             sum(1 for e in devn if e['why'] == '撤点（不在目标清单里）')))
    for e in devn:
        print('  %-6s %-52s %s -> %s'
              % (e['why'], short(e['skill']), cur.get(e['skill'], 0), e['level']))
    print('-' * 92)
    print('  技能点净变化 %+d ｜ 虔诚点净变化 %+d'
          % (sum(e['level'] - cur.get(e['skill'], 0) for e in upsn),
             sum(e['level'] - cur.get(e['skill'], 0) for e in devn)))

    out = a.out or str(paths.DATA_DIR / 'plans' / ('%s_skill_final.json' % a.char.lstrip('_')))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump({'char': a.char, 'note': a.note or '%s 加点落档' % a.char,
               'set': set_list},
              open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('→ %s' % out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
