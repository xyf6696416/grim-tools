# -*- coding: utf-8 -*-
r"""恐怖黎明 背包（袋子）物品增删复制 —— 变长写入

  python gd_inv.py list Sam                       # 列出背包物品
  python gd_inv.py dup Sam 0 --sack 0             # 预演：复制 袋子0 第0件
  python gd_inv.py dup Sam 0 --sack 0 --apply     # 写入
  python gd_inv.py add Sam it1725                 # 预演：按 GT id 添加
  python gd_inv.py add Sam it1725 --apply
  python gd_inv.py add Sam --record records/items/geartorso/b002b_torso.dbr --apply
  python gd_inv.py add Sam --record records/items/geartorso/b002b_torso.dbr \
        --component records/items/materia/compa_silkswatch.dbr --apply
  python gd_inv.py del Sam 3 --sack 0 --apply     # 删除 袋子0 第3件
  （⚠ 位置参数只有 `char` 和 `index`；`--sack` 是选项，默认 0）

★ 写装备一律用 **`--record` 显式指定普通版路径**（`records/items/gearweapons/...`），
  不要依赖 GT id 自动映射 —— 同一模板常有 `upgraded/`(+32 等级) / `awakened/` 等多档，会挑错。
★ 新物品的格子坐标默认**自动挑第一个空闲格**（`--gx/--gy` 可显式指定）。

实现要点（与 gd_skill.py 同一套变长写入链路）：
  袋子结构 = [len 字段][bool unused][i32 件数][N 件物品][chk 4B]
  * `chk` 段由 `gd_save.encode` 按**当前滚动 state** 自动重写（不必自己算）
  * `len` 段与 state 整块异或、**不推进 state**
  所以只要用 Writer 重新产出袋子内容、并让 rebuild 替换 [len 字段, 校验和) 这段即可。

安全：写盘前强制备份；先写临时文件 → 重新解析六重校验 → 才覆盖正式文件。
"""
import argparse
import os
import random
import struct
import sys

from . import core as S
from . import write as W
from .. import _legacyenv as ENV
from . import backup as B

SEED_LO, SEED_HI = 9_296_907, 2_143_745_881     # 实测合法区间（越界会导致镶嵌不生效）


def live_path(char):
    sd, _ = ENV.find_save_dir()
    p = os.path.join(sd, 'main', '_' + char, 'player.gdc')
    if not os.path.exists(p):
        raise SystemExit('✗ 找不到角色档: %s' % p)
    return p


def short(rec):
    return (rec or '').replace('records/items/', '')


def game_running():
    import subprocess
    try:
        p = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Grim Dawn.exe'],
                           capture_output=True, timeout=15)
        out = p.stdout.decode('utf-8', errors='ignore') + p.stdout.decode('gbk', errors='ignore')
        return 'Grim Dawn.exe' in out
    except Exception:
        return False


def sack_segs(unused, items, v11):
    """重建一个袋子的内容段（不含开头的 len 字段与结尾的 chk）"""
    w = W.Writer()
    w.bool(unused)
    w.i32(len(items))
    for it in items:
        W.w_item(w, it, v11, 'sack')
    return w.segs()


def blank_item(rec, seed):
    return {'basename': rec, 'prefix': '', 'suffix': '', 'modifier': '', 'transmute': '',
            'seed': seed & S.MASK, 'relic_name': '', 'relic_bonus': '', 'relic_seed': 0,
            'augment_name': '', 'unk': 0, 'augment_seed': 0, 'f_relic_comp': 0,
            'f_a': 0, 'f_b': 0, 'qty': 1, 'f_c': 0, 'f_d': 0, 'gx': 0, 'gy': 0}


def first_free(items, cols=12, rows=8):
    """袋子网格里第一个空闲格（GD 主背包 12×8）"""
    used = {(x.get('gx', 0), x.get('gy', 0)) for x in items}
    for y in range(rows):
        for x in range(cols):
            if (x, y) not in used:
                return x, y
    raise SystemExit('✗ 袋子已满（无空闲格）')


def pick_relic_seed(comp):
    """从全部存档采集该组件「已镶嵌实例」的真实 seed（比随机生成更稳）"""
    import glob
    sd, _ = ENV.find_save_dir()
    for p in glob.glob(os.path.join(sd or '', 'main', '*', 'player.gdc')):
        try:
            d = S.parse(p)
        except Exception:
            continue
        b3 = d.get('block_map', {}).get(3)
        if not b3:
            continue
        pool = [i for s in b3.get('sacks', []) for i in s['items']]
        pool += (b3.get('equipment') or []) + (b3.get('alt1') or []) + (b3.get('alt2') or [])
        for it in pool:
            if it.get('relic_name') == comp and SEED_LO <= (it.get('relic_seed') or 0) <= SEED_HI:
                return it['relic_seed']
    return random.randint(SEED_LO, SEED_HI)


def resolve_record(gid):
    """GT id -> 游戏记录路径"""
    from . import savemap
    rec, basis = gd_map.resolve(gid)
    return rec, basis


def cmd_list(a):
    live = live_path(a.char)
    d = S.parse(live, record=True)
    b3 = d['block_map'][3]
    print('角色 %s   %d 级   袋子数 %d' % (d['name'], d['level'], len(b3['sacks'])))
    for si, sk in enumerate(b3['sacks']):
        items = sk['items']
        print('-' * 100)
        print('袋子 %d：%d 件   unused=%s' % (si, len(items), sk.get('unused')))
        for i, it in enumerate(items):
            ex = []
            if it.get('prefix'):
                ex.append('前=%s' % short(it['prefix']))
            if it.get('suffix'):
                ex.append('后=%s' % short(it['suffix']))
            if it.get('relic_name'):
                ex.append('镶=%s' % short(it['relic_name']))
            if it.get('augment_name'):
                ex.append('附=%s' % short(it['augment_name']))
            if (it.get('qty') or 1) > 1:
                ex.append('x%d' % it['qty'])
            print('  %3d  %-46s seed=%-11s %s'
                  % (i, short(it.get('basename')), it.get('seed'), ' '.join(ex)))


def build_edit(a, d):
    """返回 (袋子序号, 新物品列表, 说明行列表)"""
    b3 = d['block_map'][3]
    sacks = b3['sacks']
    if a.cmd == 'dup':
        si, idx = a.sack, a.index
        if si >= len(sacks):
            raise SystemExit('✗ 袋子序号越界: %d' % si)
        items = sacks[si]['items']
        if not (0 <= idx < len(items)):
            raise SystemExit('✗ 物品序号越界: %d（该袋 %d 件）' % (idx, len(items)))
        new = dict(items[idx])
        if 'seed' in new and new['seed']:
            new['seed'] = random.randint(SEED_LO, SEED_HI)
        out = [dict(x) for x in items] + [new]
        desc = ['复制 袋子%d 第%d件 → 追加为第%d件' % (si, idx, len(items)),
                '本体 %s' % short(new.get('basename')),
                '新 seed %s' % new.get('seed')]
        return si, out, desc

    if a.cmd == 'add':
        rec = a.record
        basis = '命令行 --record'
        if not rec:
            if not a.gtid:
                raise SystemExit('✗ 需要 GT id 或 --record')
            rec, basis = resolve_record(a.gtid)
        if not rec:
            raise SystemExit('✗ 无法解析成记录路径')
        if not rec.startswith('records/'):
            raise SystemExit('✗ 记录路径必须以 records/ 开头: %s' % rec)
        seed = a.seed if a.seed else random.randint(SEED_LO, SEED_HI)
        it = blank_item(rec, seed)
        si = a.sack
        if si >= len(sacks):
            raise SystemExit('✗ 袋子序号越界: %d' % si)
        # ★ 镶嵌组件（可选）：用存档里「已镶嵌实例」的真实 seed
        comp = getattr(a, 'component', None)
        if comp:
            it['relic_name'] = comp
            it['relic_seed'] = pick_relic_seed(comp)
        # ★ 词缀（可选）：GT id（preXXXX/sufXXXX，走 gd_map 家族对齐）或直接给记录名
        for kind, val in (('prefix', getattr(a, 'prefix', None)),
                          ('suffix', getattr(a, 'suffix', None))):
            if not val:
                continue
            if val.startswith('records/'):
                reca = val
            else:
                from . import savemap as MAP
                reca, why = MAP.resolve_affix(kind, val)
                if not reca:
                    raise SystemExit('✗ %s %s 解析失败: %s' % (kind, val, why))
            it[kind] = reca
        # ★ 格子坐标：显式指定，否则自动挑第一个空闲格（否则会和 (0,0) 上的物品重叠）
        gx, gy = getattr(a, 'gx', None), getattr(a, 'gy', None)
        if gx is None or gy is None:
            gx, gy = first_free(sacks[si]['items'])
        it['gx'], it['gy'] = gx, gy
        out = [dict(x) for x in sacks[si]['items']] + [it]
        base = 'GT %s（%s）' % (a.gtid, basis) if a.gtid else basis
        desc = ['添加 %s → 袋子%d 第%d件' % (base, si, len(out) - 1),
                '记录 %s' % rec, 'seed %s' % seed,
                '格子 (%d,%d)%s' % (gx, gy, '' if not comp else '  镶 %s seed=%d' % (short(comp), it['relic_seed']))]
        if it['prefix']:
            desc.append('前缀 %s' % short(it['prefix']))
        if it['suffix']:
            desc.append('后缀 %s' % short(it['suffix']))
        return si, out, desc

    if a.cmd == 'del':
        si, idx = a.sack, a.index
        if si >= len(sacks):
            raise SystemExit('✗ 袋子序号越界: %d' % si)
        items = sacks[si]['items']
        if not (0 <= idx < len(items)):
            raise SystemExit('✗ 物品序号越界: %d（该袋 %d 件）' % (idx, len(items)))
        victim = items[idx]
        out = [dict(x) for x in items if x is not victim]
        desc = ['删除 袋子%d 第%d件' % (si, idx), '本体 %s' % short(victim.get('basename'))]
        return si, out, desc

    raise SystemExit('✗ 未知操作')


def strip_offsets(o):
    """去掉解析器记录的 `_off_*` 偏移字段再比较。

    块3 变长后，**其后所有块**的偏移都会平移（如块8 的 `_off_skills`），
    但数据本身没变 —— 直接整块比较会误报"被意外改动"。
    """
    if isinstance(o, dict):
        return {k: strip_offsets(v) for k, v in o.items() if not k.startswith('_')}
    if isinstance(o, list):
        return [strip_offsets(x) for x in o]
    return o


def run(a, write):
    live = live_path(a.char)
    if write and game_running():
        raise SystemExit('✗ 检测到 Grim Dawn.exe 正在运行 —— 请先完全退出游戏再写盘')

    raw = open(live, 'rb').read()
    d = S.parse(live, record=True)
    if not d['all_blocks_ok']:
        raise SystemExit('✗ 原档解析未全部通过，先别改')
    b3 = d['block_map'][3]
    v11 = b3['version'] >= 11

    si, items, desc = build_edit(a, d)
    offs = b3['_sack_offs'][si]
    old_cnt = len(b3['sacks'][si]['items'])

    print('=' * 96)
    print('背包物品改动（%s）  角色 %s  %d 级' % (a.cmd, d['name'], d['level']))
    print('=' * 96)
    for line in desc:
        print('  ' + line)
    print('-' * 96)
    print('  袋子%d 件数 %d -> %d' % (si, old_cnt, len(items)))

    image = bytearray(d['record']['image'])
    plan_ = list(d['record']['plan'])
    blocks = W.block_ranges(raw)

    segs = sack_segs(b3['sacks'][si].get('unused', False), items, v11)
    nlen = sum(len(b) for _, b in segs)
    new_segs = [('len', struct.pack('<I', nlen))] + segs

    all_segs = W.rebuild(image, plan_, blocks,
                         {3: (offs['off_len'], offs['off_chk'], new_segs)})
    img, plan2 = W.flatten(all_segs)
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
    for bid in (1, 2, 8):
        if strip_offsets(v['block_map'].get(bid)) != strip_offsets(d['block_map'].get(bid)):
            errs.append('块%d 被意外改动' % bid)

    v3 = v['block_map'][3]
    if len(v3['sacks']) != len(b3['sacks']):
        errs.append('袋子数被改动')
    else:
        for j, (os_, ns) in enumerate(zip(b3['sacks'], v3['sacks'])):
            if j == si:
                continue
            if len(os_['items']) != len(ns['items']):
                errs.append('袋子%d 件数被意外改动' % j)
    if len(v3['sacks'][si]['items']) != len(items):
        errs.append('目标袋件数不符: %d != %d' % (len(v3['sacks'][si]['items']), len(items)))
    else:
        for x, y in zip(v3['sacks'][si]['items'], items):
            if x.get('basename') != y.get('basename'):
                errs.append('物品记录不符: %s != %s' % (x.get('basename'), y.get('basename')))
                break
        if a.cmd in ('dup', 'add'):
            last = v3['sacks'][si]['items'][-1]
            want = items[-1]
            if last.get('basename') != want.get('basename') or last.get('seed') != want.get('seed'):
                errs.append('追加物品不符')
    for j, (o, n) in enumerate(zip(b3['equipment'], v3['equipment'])):
        if o.get('seed') != n.get('seed'):
            errs.append('装备槽%d 被意外改动' % j)

    print()
    if errs:
        print('✗ 校验未通过：')
        for e in errs:
            print('   - %s' % e)
        print('   临时文件保留在 %s（正式存档未动）' % tmp)
        return 1
    print('✓ 六重校验全过：块校验 / 加密往返 / 等级姓名不变 / 块1·2·8 不变 / 袋子与装备未串改 / 目标吻合')

    if write:
        bdir = B.do_backup(note='改背包前自动备份（%s）' % a.cmd)
        os.replace(tmp, live)
        print('✓ 已写入 %s（%d 字节）' % (live, len(out)))
        print('   回滚点：%s' % bdir)
    else:
        os.remove(tmp)
        print('（预演模式，未写盘；确认无误后加 --apply）')
    return 0


def cmd_run(a):
    return run(a, write=a.apply)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)

    s = sub.add_parser('list')
    s.add_argument('char', nargs='?', default='Sam')
    s.set_defaults(fn=cmd_list)

    for name in ('dup', 'add', 'del'):
        p = sub.add_parser(name)
        p.add_argument('char')
        p.add_argument('--sack', type=int, default=0)
        p.add_argument('--apply', action='store_true')
        p.set_defaults(fn=cmd_run)
        if name == 'dup':
            p.add_argument('index', type=int)
        elif name == 'del':
            p.add_argument('index', type=int)
        else:
            p.add_argument('gtid', nargs='?')
            p.add_argument('--record')
            p.add_argument('--seed', type=int)
            p.add_argument('--component', help='镶嵌组件记录名（records/...）')
            p.add_argument('--prefix', help='前缀：GT id（preXXXX）或 records/... 记录名')
            p.add_argument('--suffix', help='后缀：GT id（sufXXXX）或 records/... 记录名')
            p.add_argument('--gx', type=int, help='格子 x（缺省自动找空格）')
            p.add_argument('--gy', type=int, help='格子 y')
    a = ap.parse_args()
    return a.fn(a) or 0


if __name__ == '__main__':
    sys.exit(main())
