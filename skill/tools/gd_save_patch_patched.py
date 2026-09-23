# -*- coding: utf-8 -*-
"""
恐怖黎明存档安全改档工具

  # 预演(默认, 不写盘): 看会改什么
  python gd_edit.py --char Sam --iron 999999 --attribute-points 10

  # 实际写入(自动先备份 + 写后自动校验)
  python gd_edit.py --char Sam --iron 999999 --apply

支持字段:
  --iron N                铁币           (块1)
  --tributes N            贡品           (块1)
  --attribute-points N    未分配属性点    (块2)
  --skill-points N        未分配技能点    (块2)
  --devotion-points N     未分配虔诚点    (块2)
  --experience N          经验           (块2)
  --level-in-bio N        角色面板等级    (块2)
  --physique F --cunning F --spirit F --health F --energy F   (块2, 浮点)

★ 自动属性拟合（默认开启）
  --fit-gear / --no-fit-gear   按当前装备的需求**自动算出** physique/cunning/spirit
  --fit-buffer N               体格/精神各多留 N 点余量          (默认 1)
  --att-safety N               饰品提示框行数的安全行数           (默认 6)
  --fit-prefer physique|cunning|spirit   余额投给哪个属性(默认按形态倾向)
  --fit-allow-short            即使预算不够也照写「最接近」的解(默认拒绝写)

  · 未显式给 --physique/--cunning/--spirit 时**自动开启**；
    显式给了就以你给的为准（但会提示是否满足装备需求）。
  · 严格等级预算：可用点数 = 已花 + 未分配，**不凭空加总点数**。
  · 拟合成功会把 attribute_points 置 0（点数已经花掉了）。

安全机制:
  1. 检测游戏是否在运行, 在运行则直接拒绝
  2. 写盘前强制完整备份(含 MANIFEST)
  3. 先写临时文件 → 重新解析校验(15 块全过 + 目标字段已生效 + 其它字段未变) → 才覆盖正式文件
  4. ★ 写后**再验一遍装备可穿性**（三围面板 ≥ 全套需求），不过就报错
  5. 任一步失败即中止, 不动正式文件
"""
import argparse
import os
import shutil
import struct
import subprocess
import sys

from . import core as S
from . import backup as B

SAVE_DIR = os.path.join(B.LIVE_SAVE, 'remote', 'save')
BLOCK2_FIELDS = {'attribute_points', 'skill_points', 'devotion_points',
                 'experience', 'level_in_bio'}
BLOCK2_FLOATS = {'physique', 'cunning', 'spirit', 'health', 'energy'}


def game_running():
    """检测游戏是否在运行（中文 Windows 控制台编码不定，这里按字节解码并忽略错误）"""
    try:
        p = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Grim Dawn.exe'],
                           capture_output=True, timeout=15)
        out = p.stdout.decode('utf-8', errors='ignore') + \
            p.stdout.decode('gbk', errors='ignore')
        return 'Grim Dawn.exe' in out
    except Exception:
        return False


def snapshot_cmp(a, b, ignore_paths):
    """比较两棵解析结果, 返回差异列表"""
    diffs = []

    def walk(pa, pb, path):
        if isinstance(pa, dict) and isinstance(pb, dict):
            for k in set(pa) | set(pb):
                walk(pa.get(k), pb.get(k), path + '/' + str(k))
        elif isinstance(pa, list) and isinstance(pb, list):
            if len(pa) != len(pb):
                diffs.append('%s 长度 %d->%d' % (path, len(pa), len(pb)))
            else:
                for i, (x, y) in enumerate(zip(pa, pb)):
                    walk(x, y, '%s[%d]' % (path, i))
        elif pa != pb:
            if path not in ignore_paths:
                diffs.append('%s %r -> %r' % (path, pa, pb))

    walk(a, b, '')
    return diffs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--char', required=True, help='角色目录名(不含下划线), 如 Sam')
    ap.add_argument('--iron', type=int)
    ap.add_argument('--tributes', type=int)
    ap.add_argument('--attribute-points', type=int, dest='attribute_points')
    ap.add_argument('--skill-points', type=int, dest='skill_points')
    ap.add_argument('--devotion-points', type=int, dest='devotion_points')
    ap.add_argument('--experience', type=int)
    ap.add_argument('--level-in-bio', type=int, dest='level_in_bio')
    for f in sorted(BLOCK2_FLOATS):
        ap.add_argument('--' + f, type=float)
    ap.add_argument('--fit-gear', dest='fit_gear', action='store_true', default=None,
                    help='按装备需求自动拟合三围（默认：未显式给三围时开启）')
    ap.add_argument('--no-fit-gear', dest='fit_gear', action='store_false',
                    help='关闭自动拟合，严格按命令行给的值写')
    ap.add_argument('--fit-buffer', type=int, default=1, dest='fit_buffer',
                    help='体格/精神各多留几点余量（默认 1）')
    ap.add_argument('--att-safety', type=int, default=6, dest='att_safety',
                    help='饰品提示框行数的安全行数（默认 6）')
    ap.add_argument('--fit-prefer', default=None, dest='fit_prefer',
                    choices=['physique', 'cunning', 'spirit'],
                    help='余额投给哪个属性（默认按形态 attribute_bias）')
    ap.add_argument('--fit-allow-short', action='store_true', dest='fit_allow_short',
                    help='预算不够也照写（默认拒绝写盘）')
    ap.add_argument('--fit-spread', action='store_true', dest='fit_spread',
                    help='余额按形态倾向分摊（默认全给输出属性）')
    ap.add_argument('--apply', action='store_true', help='真正写入(默认只预演)')
    ap.add_argument('--allow-running', action='store_true', dest='allow_running',
                    help='危险：游戏运行中也允许写入。仅用于「实时生效」实验；'
                         '游戏的下一次存档会覆盖本次写入')
    ap.add_argument('--save-dir', default=SAVE_DIR, help='存档目录(调试用)')
    a = ap.parse_args()

    target = os.path.join(a.save_dir, 'main', '_' + a.char, 'player.gdc')
    if not os.path.exists(target):
        raise SystemExit('找不到角色档: %s' % target)

    running = game_running()
    if a.apply and running and not a.allow_running:
        raise SystemExit('检测到 Grim Dawn.exe 正在运行 —— 请先完全退出游戏再改档！\n'
                         '（游戏在运行时会覆盖你的修改）\n'
                         '若确实要做「实时生效」实验，加 --allow-running（有被覆盖的风险）。')
    if a.apply and running and a.allow_running:
        print('⚠ --allow-running：游戏正在运行。写入后通常不会实时生效，'
              '且会被游戏的下一次存档覆盖 —— 本开关仅用于验证该行为。')
    if a.apply and not os.path.normcase(a.save_dir) == os.path.normcase(SAVE_DIR):
        print('注意：正在改的是自定义目录 %s' % a.save_dir)

    d = S.parse(target, record=True)
    if not d['all_blocks_ok']:
        raise SystemExit('原档解析未全部通过，先别改。')
    rec = d['record']
    b1, b2 = d['block_map'][1], d['block_map'][2]

    edits = []          # (区块, 偏移, 类型, 旧值, 新值, 描述)
    if a.iron is not None:
        edits.append((1, b1['_off_iron'], 'u32', b1['iron'], a.iron, '铁币'))
    if a.tributes is not None:
        edits.append((1, b1['_off_tributes'], 'u32', b1['tributes'], a.tributes, '贡品'))
    for name, key in (('attribute_points', 'attribute_points'), ('skill_points', 'skill_points'),
                      ('devotion_points', 'devotion_points'), ('experience', 'experience'),
                      ('level_in_bio', 'level_in_bio')):
        v = getattr(a, name)
        if v is not None:
            edits.append((2, b2['_off'][key], 'u32', b2[key], v, name))
    for f in sorted(BLOCK2_FLOATS):
        v = getattr(a, f)
        if v is not None:
            edits.append((2, b2['_off'][f], 'f32', b2[f], v, f))

    # ---- ★ 自动属性拟合（按装备需求反推三围）
    explicit_attrs = any(getattr(a, f) is not None for f in BLOCK2_FLOATS)
    fit_on = a.fit_gear if a.fit_gear is not None else (not explicit_attrs)
    rf = None
    if fit_on:
        from .. import reqfit
        print('=' * 78)
        print('按装备需求自动拟合属性点 …')
        rf = reqfit.solve(a.char, buffer=a.fit_buffer, att_safety=a.att_safety,
                          prefer=a.fit_prefer, spread=a.fit_spread,
                          save_dir=a.save_dir)
        print(reqfit.describe(rf))
        print('=' * 78)
        if not rf.feasible and not a.fit_allow_short:
            raise SystemExit(
                '✗ 属性点预算不够，装备穿不上 —— 拒绝写盘。\n'
                '  可用 %d 点，至少需要 %d 点，还差：%s\n'
                '  （要么换件，要么加 --fit-allow-short 写「最接近」的解）'
                % (rf.budget, sum(rf.points_after.values()),
                   ', '.join('%s %d 点' % (reqfit.ZH[k], v)
                             for k, v in rf.short.items())))
        if rf.changed:
            for f in ('physique', 'cunning', 'spirit'):
                old = float(b2.get(f) or 0.0)
                new = float(rf.targets[f])
                if abs(old - new) > 1e-9:
                    edits.append((2, b2['_off'][f], 'f32', old, new, f))
            # 点数已经花在属性上了 → 未分配清零
            if a.attribute_points is None and int(b2.get('attribute_points') or 0):
                edits.append((2, b2['_off']['attribute_points'], 'u32',
                              b2['attribute_points'], 0, 'attribute_points'))
        else:
            print('（当前三围已经满足全套需求，无需改动属性）')
    elif explicit_attrs:
        # 显式给了三围 → 尊重，但提示是否满足装备需求
        from .. import reqfit
        try:
            rf = reqfit.solve(a.char, buffer=a.fit_buffer,
                              att_safety=a.att_safety, prefer=a.fit_prefer,
                              spread=a.fit_spread, save_dir=a.save_dir)
            _forced = {k: round((getattr(a, k) - 50.0) / 8.0)
                       for k in ('physique', 'cunning', 'spirit')
                       if getattr(a, k) is not None}
            _panel = {k: rf.A[k] + rf.B[k] * _forced.get(k, rf.points_before[k])
                      for k in reqfit.KEYS}
            _bad = [k for k in reqfit.KEYS
                    if rf.need[k] and _panel[k] + 1e-6 < rf.need[k]]
            if _bad:
                print('⚠ 你显式指定的三围**不满足**装备需求：%s'
                      % ', '.join('%s 需 %d 只有 %.0f'
                                  % (reqfit.ZH[k], rf.need[k], _panel[k])
                                  for k in _bad))
                print('  （要自动修就加 --fit-gear；想只看需求就 --no-fit-gear）')
            else:
                print('✓ 你显式指定的三围满足装备需求')
        except SystemExit:
            raise
        except Exception as e:
            print('⚠ 需求自检跳过（%s: %s）' % (type(e).__name__, e))

    if not edits:
        if fit_on and rf is not None:
            # 只跑拟合、且已经满足 → 不是错误，是「无需改动」
            print('✓ 当前三围已满足全套装备需求，没有需要修改的字段。')
            return
        raise SystemExit('没有指定要改的字段，看看 --help')

    print('目标: %s  (%s, %d 级)' % (target, d['name'], d['level']))
    print('将要修改:')
    for _, _, k, ov, nv, desc in edits:
        print('   %-18s %s -> %s' % (desc, ov, nv))
    if not a.apply:
        print('\n[预演] 未写盘。确认无误后加 --apply')
        return

    # 1) 备份（只对真实存档目录做；自定义目录视为测试）
    if os.path.normcase(a.save_dir) == os.path.normcase(SAVE_DIR):
        bk = B.do_backup(note='改档前: %s' % a.char)
        B.do_verify(bk)
        print('备份完成 -> %s' % bk)
    else:
        bk = None
        print('（自定义存档目录，跳过备份）')

    # 2) 打补丁
    DESC2PATH = {'铁币': '/1/iron', '贡品': '/1/tributes'}
    ignore = set()
    for bid, off, kind, ov, nv, desc in edits:
        if kind == 'u32':
            rec['image'][off:off + 4] = struct.pack('<I', nv & S.MASK)
        else:
            rec['image'][off:off + 4] = struct.pack('<I', struct.unpack(
                '<I', struct.pack('<f', nv))[0])
        ignore.add(DESC2PATH.get(desc, '/2/' + desc))

    new_bytes = S.encode(rec['image'], rec['plan'], d['seed'])

    # 3) 先写临时文件并校验
    tmp = target + '.new'
    with open(tmp, 'wb') as f:
        f.write(new_bytes)
    v = S.parse(tmp)
    ok = v['all_blocks_ok'] and v['header_ok']
    v1, v2 = v['block_map'].get(1, {}), v['block_map'].get(2, {})
    applied = True
    for bid, off, kind, ov, nv, desc in edits:
        got = (v1 if bid == 1 else v2)[
            {'铁币': 'iron', '贡品': 'tributes'}.get(desc, desc)]
        same = abs(got - nv) < 1e-3
        applied &= same
        print('   校验 %-18s 现值 %s %s' % (desc, got, 'OK' if same else '不符'))
    diffs = snapshot_cmp(d['block_map'], v['block_map'], ignore)
    print('   校验 15 块全过=%s ｜ 目标字段生效=%s ｜ 其它字段变化=%d %s'
          % (ok, applied, len(diffs), (diffs[:5] if diffs else '无 ★')))

    # ★ 写后复检：装备可穿性（三围面板 ≥ 全套需求）
    #   规则：**开了自动拟合就必须过**（否则拒绝写盘）；
    #         显式 `--no-fit-gear` 时只强烈警告、不拦（用户已明确选择自己负责）。
    wear_ok, wear_blocking = True, bool(fit_on)
    if rf is not None and rf.need and any(rf.need.values()):
        vb2 = v['block_map'].get(2) or {}
        pv = {}
        for f in ('physique', 'cunning', 'spirit'):
            pts = (float(vb2.get(f) or 0.0) - 50.0) / 8.0
            pv[f] = rf.A[f] + rf.B[f] * pts
        bad = [f for f in ('physique', 'cunning', 'spirit')
               if rf.need[f] and pv[f] + 1e-6 < rf.need[f]]
        wear_ok = not bad
        print('   校验 装备可穿性 体格 %.0f/%d ｜ 狡诈 %.0f/%d ｜ 精神 %.0f/%d  %s'
              % (pv['physique'], rf.need['physique'], pv['cunning'],
                 rf.need['cunning'], pv['spirit'], rf.need['spirit'],
                 '✓ 全过' if wear_ok else '✗ 仍缺 %s' % bad))
        if rf.unevaluated:
            print('   ⚠ %d 件装备需求**未评估**（认不出类型，看 tools/reqcheck.py）'
                  % len(rf.unevaluated))
        if not wear_ok and not wear_blocking:
            print('   ⚠ 你用了 --no-fit-gear，这条不拦你 —— 但这套装备在游戏里穿不上。'
                  '\n     想自动修就加 --fit-gear。')

    if not (ok and applied and not diffs and (wear_ok or not wear_blocking)):
        os.remove(tmp)
        raise SystemExit('校验未通过，已丢弃临时文件，正式存档未改动。')

    # 4) 落盘
    shutil.move(tmp, target)
    print('\n已写入 %s' % target)
    if bk:
        print('备份在 %s' % bk)
        print('如需回退: python gd_backup.py restore "%s" --yes' % bk)


if __name__ == '__main__':
    main()
