# -*- coding: utf-8 -*-
r"""恐怖黎明存档 技能加点写入器

  python gd_skill.py show  [Sam]           # 列出技能（按点数排序 + 收支统计）
  python gd_skill.py check plans/x.json    # 预演：打印改动与点数收支，不写盘
  python gd_skill.py apply plans/x.json    # 真正写入（自动备份 + 六重校验）

方案文件：
  {"char": "_Sam", "note": "…",
   "set": [{"skill": "records/skills/playerclass10/werewolf3.dbr",
            "level": 0, "why": "撤点：穿刺转混乱"}]}

语义：
  * level = 0  → 撤点（条目保留、enabled 置 0，与存档里 0 级药水条目同构）
  * 存档里没有该记录 → 自动新增一条，插在该精通技能区末尾
  * **只改等级，不动加点顺序**（游戏自己会在升级时重排）

为什么统一走变长写入：新增/删除条目会改变技能区长度，定长补丁做不到；
改等级本身虽是定长，但为保持单一代码路径，这里一律重建技能区（已实测可靠）。
"""
import argparse
import json
import os
import sys

from .. import _legacyenv as ENV
from . import core as S
from . import write as W

NON_SKILL = ('/default/', '/devotion/', '/itemskills')


def live_path(char):
    sd, _ = ENV.find_save_dir()
    if not sd:
        raise SystemExit('✗ 找不到存档目录')
    return os.path.join(sd, 'main', '_' + char.lstrip('_'), 'player.gdc')


def spent(skills):
    """已花技能点：玩家技能 + 精通条（不含 default / devotion / 药水）"""
    return sum(s['level'] for s in skills
               if not any(k in s['skill'] for k in NON_SKILL))


def short(rec):
    return rec.replace('records/skills/', '')


def cmd_show(args):
    p = live_path(args.char)
    d = S.parse(p)
    b8 = d['block_map'][8]
    rows = [s for s in b8['skills'] if s['level'] > 0
            and not any(k in s['skill'] for k in NON_SKILL)]
    print('=' * 96)
    print('%s  等级 %s  职业 %s' % (d['name'], d['level'], ' + '.join(d['classes'])))
    print('=' * 96)
    for s in sorted(rows, key=lambda x: -x['level']):
        print('  %4d  %-46s%s' % (s['level'], short(s['skill']),
                                  '  [已激活]' if s['active'] else ''))
    print('-' * 96)
    print('  已花技能点 %d    未分配 %d' % (spent(b8['skills']),
                                          d['block_map'][2]['skill_points']))


def load_plan(path):
    with open(path, 'r', encoding='utf-8') as f:
        plan = json.load(f)
    if 'char' not in plan or 'set' not in plan:
        raise SystemExit('✗ 方案缺少 char / set 字段')
    for e in plan['set']:
        if 'skill' not in e or 'level' not in e:
            raise SystemExit('✗ set 条目缺少 skill / level')
        if not e['skill'].startswith('records/skills/'):
            raise SystemExit('✗ skill 必须是完整记录路径: %s' % e['skill'])
    return plan


def apply_plan(b8, plan):
    """返回 (新技能列表, 改动日志)"""
    skills = [dict(s) for s in b8['skills']]
    idx = {s['skill']: i for i, s in enumerate(skills)}
    log, fresh = [], []

    for e in plan['set']:
        rec, lv = e['skill'], int(e['level'])
        if rec in idx:
            s = skills[idx[rec]]
            old = s['level']
            s['level'] = lv
            s['enabled'] = lv > 0
            if lv <= 0:
                # ★★ 星座必须**同时清 `devotion_level`**（2026-09-18 实测踩过）：
                #   游戏判定一个星点是否点亮看的是 `devotion_level`，**不是 `level`**。
                #   只把 `level` 置 0 的话，存档里看着撤了，游戏里**星星照样亮着**
                #   —— 实测换星座时旧的 17 点狼人星座全留着，档内虔诚 41 点（预算只有 24）。
                for kk in ('devotion_level', 'devotion_exp'):
                    if kk in s:
                        s[kk] = 0
            elif W.is_star(rec):
                # ★ 反向也要修（2026-09-21）：**重新点亮**一个曾撤过的星点，
                #   同样得把 `devotion_level` 顶回 `lv`，否则档里有了、游戏里不亮。
                if 'devotion_level' in s:
                    s['devotion_level'] = lv
            log.append((rec, old, lv, '调整'))
        else:
            fresh.append((rec, lv, e.get('why', '')))

    if fresh:
        last = max(i for i, s in enumerate(skills)
                   if s['skill'].startswith('records/skills/playerclass'))
        ins = [W.blank_skill(rec, lv) for rec, lv, _ in fresh]
        skills[last + 1:last + 1] = ins
        for rec, lv, _ in fresh:
            log.append((rec, 0, lv, '新增'))

    return skills, log


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)

    s1 = sub.add_parser('show', help='列出技能')
    s1.add_argument('char', nargs='?', default='Sam')
    s1.set_defaults(fn=cmd_show)

    for name in ('check', 'apply'):
        s2 = sub.add_parser(name, help='预演' if name == 'check' else '写入')
        s2.add_argument('plan')
        s2.set_defaults(fn=(cmd_check if name == 'check' else cmd_apply))

    args = ap.parse_args()
    args.fn(args)


def cmd_check(args):
    run(args, write=False)


def cmd_apply(args):
    run(args, write=True)


def run(args, write):
    plan = load_plan(args.plan)
    live = live_path(plan['char'])
    if write:
        from . import patch as E
        if E.game_running():
            raise SystemExit('✗ 检测到 Grim Dawn.exe 正在运行，拒绝写盘')

    raw = open(live, 'rb').read()
    d = S.parse(live, record=True)
    b8 = d['block_map'][8]
    before_spent = spent(b8['skills'])

    skills, log = apply_plan(b8, plan)
    after_spent = spent(skills)

    print('=' * 96)
    print('技能方案：%s' % plan.get('note', ''))
    print('角色 %s   块8 version=%d   技能条数 %d -> %d' %
          (d['name'], b8['version'], len(b8['skills']), len(skills)))
    print('=' * 96)
    for rec, old, new, kind in log:
        print('  %-6s %-46s %3d -> %-3d' % (kind, short(rec), old, new))
    print('-' * 96)
    print('  已花技能点 %d -> %d  (净 %+d)   未分配 %d' %
          (before_spent, after_spent, after_spent - before_spent,
           d['block_map'][2]['skill_points']))

    image = bytearray(d['record']['image'])
    plan_ = list(d['record']['plan'])
    blocks = W.block_ranges(raw)
    b8blk = [b for b in blocks if b['id'] == 8][0]

    new_segs = W.w_skills_region(skills, b8['version'] >= 7)
    segs = W.rebuild(image, plan_, blocks,
                     {8: (b8['_off_skills'], b8['_off_skills_end'], new_segs)})
    img, plan2 = W.flatten(segs)
    out = S.encode(img, plan2, d['seed'])

    tmp = live + '.new'
    open(tmp, 'wb').write(out)
    v = S.parse(tmp, record=True)

    errs = []
    if not v['all_blocks_ok']:
        errs.append('块校验失败: %s' % [b['id'] for b in v['blocks'] if not b['ok']])
    if S.encode(v['record']['image'], v['record']['plan'], v['seed']) != out:
        errs.append('加密往返不一致')
    if v['level'] != d['level'] or v['name'] != d['name']:
        errs.append('角色等级/姓名被改动')
    if v['block_map'][2] != d['block_map'][2]:
        errs.append('块2(点数)被意外改动')

    vb8 = v['block_map'][8]
    vs = {s['skill']: s['level'] for s in vb8['skills']}
    for e in plan['set']:
        if vs.get(e['skill']) != int(e['level']):
            errs.append('%s 写入不符(实际 %s)' % (short(e['skill']), vs.get(e['skill'])))
    if len(vb8['skills']) != len(skills):
        errs.append('技能条数不符')
    if spent(vb8['skills']) != after_spent:
        errs.append('点数统计不符')
    for s in vb8['skills']:
        if s['level'] < 0 or s['level'] > 100:
            errs.append('%s 等级越界 %d' % (short(s['skill']), s['level']))

    print()
    if errs:
        print('✗ 校验未通过：')
        for e in errs:
            print('   - %s' % e)
        print('   临时文件保留在 %s' % tmp)
        return 1
    print('✓ 六重校验全过：块校验 / 加密往返 / 等级不变 / 点数区不变 / 逐项吻合 / 条数一致')

    if write:
        from . import backup as B
        bdir = B.do_backup(note='改技能前自动备份')
        os.replace(tmp, live)
        print('✓ 已写入 %s（%d 字节）' % (live, len(out)))
        print('  回滚点：%s' % bdir)
    else:
        os.remove(tmp)
        print('（预演模式，未写盘）')
    return 0


if __name__ == '__main__':
    sys.exit(main() or 0)
