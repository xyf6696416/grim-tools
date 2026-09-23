#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""一键换档 —— 列出存档备份并恢复「最新的一份」（或指定某一份）。

为什么需要它
------------
手动换档时 `python -m gd backup restore <目录> --yes` 有两个不便：
  1. 要手打 `GrimDawn_存档备份_2026-09-20_203941` 这种目录名，69 份备份根本认不过来；
  2. `do_restore` 是**整包覆盖** —— 存档里有 5 个角色，整包恢复会把**别的角色一起退回旧进度**。
本脚本补上这两点：**列表一眼可读**（等级 / 三围 / 未分配 / 指纹 / 备注，能认出「落档前·后」）
+ **默认只恢复目标角色**（`main/_<char>`，别的角色一根手指都不碰）。

用法
----
    python tools/gd_restore.py                  # 交互：列备份 -> 回车 = 恢复最新，或输序号
    python tools/gd_restore.py -l               # 只列不恢复
    python tools/gd_restore.py -y               # 一键恢复**最新**备份（只恢复 Sam 的角色档）
    python tools/gd_restore.py -n 3 -y          # 恢复列表里的第 3 份
    python tools/gd_restore.py --dir "<备份目录>" -y
    python tools/gd_restore.py -y --all         # 整包恢复（含其他角色 / 设置 / 共享仓库）
    python tools/gd_restore.py -y --char xyf    # 换另一个角色的档
    python tools/gd_restore.py -y --dry-run     # 预演（永不写盘）

安全设计（与 `gd/save/backup.py` 一致，另加三道门）
--------------------------------------------------
  * **不加 `-y` 只预演**；`--dry-run` 永不写盘；
  * **游戏运行中直接拒绝** —— Grim Dawn 退出时会把内存态写回盘，恢复了也白恢复
    （`--force` 可越过，但你要自己承担）；
  * 恢复**前**用 MANIFEST 逐文件 md5 校验备份完好性，不符则拒绝（`--force` 可越过）；
  * 恢复**前**自动把当前存档再备份一份（`do_backup`）⇒ 永远可回退；
  * 默认**只覆盖、不删除**；`--mirror` 才会清掉目标角色目录里「备份中没有」的旧文件
    （执行前会把待删清单打出来）。
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import shutil
import subprocess
import sys
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

PREFIX = 'GrimDawn_存档备份_'
GAME_MARK = b'Grim Dawn'
NO_WINDOW = 0x08000000 if os.name == 'nt' else 0


# ---------------------------------------------------------------- 输出小工具

def say(*a, **k):
    """按控制台编码安全打印。

    cp936 控制台里 `⇒`(U+21D2) 这类字符会直接抛 UnicodeEncodeError ——
    与其在 `chcp` 上赌运气，不如在这里统一降级成 `?`。
    """
    enc = getattr(sys.stdout, 'encoding', None) or 'utf-8'
    out = []
    for x in a:
        s = str(x)
        try:
            s.encode(enc)
        except (UnicodeEncodeError, LookupError):
            s = s.encode(enc, 'replace').decode(enc, 'replace')
        out.append(s)
    print(*out, **k)


def _dw(s):
    """字符串显示宽度（CJK 算 2 格，否则 ljust 出来的表会歪）。"""
    return sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in str(s))


def _pad(s, n):
    s = str(s)
    return s + ' ' * max(0, n - _dw(s))


def _clip(s, n):
    s = str(s or '').replace('\n', ' ').strip()
    while _dw(s) > n:
        s = s[:-1]
    return s


def ask(prompt):
    """交互输入；非交互环境 / 管道 / Ctrl-C -> None（调用方按「预演结束」处理）。

    ⚠ Git Bash 里 `sys.stdin.isatty()` 可能仍为 True 而实际读到 EOF，
      所以这里必须自己兜 EOFError，不能只靠 isatty 判断。
    """
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        say('')
        return None


# ---------------------------------------------------------------- 备份扫描

def _B():
    """延迟导入 `gd.save.backup`（`--live` 要先设 GD_SAVE 再导入）。"""
    from gd.save import backup as B
    return B


def find_backups(archives=None):
    """[(时间戳名, 目录)]，按时间**倒序**（最新在前）。"""
    root = archives or _B().ARCHIVES
    out = []
    for d in glob.glob(os.path.join(root, PREFIX + '*')):
        if os.path.isdir(d):
            out.append((os.path.basename(d)[len(PREFIX):], d))
    # 目录名即时间戳 —— 按名字排比按 mtime 稳（拷贝/搬移会改 mtime）
    out.sort(key=lambda x: x[0], reverse=True)
    return out


def char_savedir(backup_dir, char):
    """备份里该角色的存档根（`.../remote/save`）；没有则 None。"""
    c = char.lstrip('_')
    pat = os.path.join(glob.escape(backup_dir), '**', 'main', '_' + c, 'player.gdc')
    hits = glob.glob(pat, recursive=True)
    if not hits:
        return None
    return hits[0].replace(os.sep, '/').split('/main/')[0].replace('/', os.sep)


def _short_time(name):
    """`2026-09-20_203941` -> `09-20 20:39`（列表用，带日期才分得清哪天的档）。"""
    try:
        d, t = name.split('_', 1)
        return '%s %s:%s' % ('-'.join(d.split('-')[1:]), t[:2], t[2:4])
    except Exception:                                        # noqa: BLE001
        return name[:11]


def backup_info(d, char='Sam'):
    """读一份备份的摘要（清单信息 + 目标角色的状态/指纹）。"""
    info = {'dir': d, 'name': os.path.basename(d)[len(PREFIX):], 'time': '',
            'full': '', 'note': '', 'files': 0, 'bad': '', 'chars': [],
            'state': {}, 'fp': ''}
    info['time'] = _short_time(info['name'])
    mp = os.path.join(d, 'MANIFEST.json')
    if not os.path.isfile(mp):
        info['bad'] = '缺 MANIFEST'
        return info
    try:
        man = json.load(open(mp, encoding='utf-8'))
        files = man.get('files') or {}
        info['full'] = man.get('备份时间') or info['name']
        info['note'] = man.get('备注') or ''
        info['files'] = len(files)
        info['chars'] = sorted({k.split('/main/_', 1)[1].split('/')[0]
                                for k in files if '/main/_' in k})
    except Exception as e:                                   # noqa: BLE001
        info['bad'] = 'MANIFEST 读不出'
        say('  ! %s: %s' % (info['name'], e))
        return info
    sd = char_savedir(d, char)
    if not sd:
        info['bad'] = info['bad'] or '无 %s 的角色档' % char
        return info
    try:
        from save_state import fingerprint_of, state_of
        st = state_of(char, sd)
        info['state'] = st
        info['fp'] = fingerprint_of(st)
    except Exception:                                        # noqa: BLE001
        info['bad'] = info['bad'] or '角色档解析失败'
    return info


def live_state(char='Sam'):
    """当前存档状态（用于列表里标「← 当前」）。"""
    try:
        from save_state import fingerprint_of, state_of
        st = state_of(char)
        return st, fingerprint_of(st)
    except Exception:                                        # noqa: BLE001
        return {}, ''


def game_running():
    """Grim Dawn 是否在跑（按进程名做字节匹配，绕开控制台编码）。"""
    if os.name != 'nt':
        return False
    try:
        out = subprocess.run(['tasklist'], capture_output=True,
                             creationflags=NO_WINDOW).stdout or b''
    except Exception:                                        # noqa: BLE001
        return False
    return GAME_MARK in out


# ---------------------------------------------------------------- 列表

HDR = ('#', '时间', '等级', '体格/狡诈/精神', '未分', '文件', '指纹', '备注')


def print_list(infos, cur_fp='', title='存档备份'):
    say('%s（最新在前）' % title)
    say('  ' + ' '.join([_pad(HDR[0], 3), _pad(HDR[1], 11), _pad(HDR[2], 5),
                         _pad(HDR[3], 17), _pad(HDR[4], 4), _pad(HDR[5], 4),
                         _pad(HDR[6], 12), HDR[7]]))
    say('  ' + '-' * 100)
    for i, f in enumerate(infos, 1):
        st = f['state'] or {}
        pcs = ('%s/%s/%s' % (st.get('physique'), st.get('cunning'), st.get('spirit'))
               if st else '-')
        mark = ''
        if f['bad']:
            mark = '  [%s]' % f['bad']
        elif f['fp'] and f['fp'] == cur_fp:
            mark = '  [= 当前]'
        say('  ' + ' '.join([
            _pad(i, 3), _pad(f['time'] or '?', 11),
            _pad('lv%s' % st.get('level') if st.get('level') else '-', 5),
            _pad(pcs, 17), _pad(st.get('attribute_points') if st else '-', 4),
            _pad(f['files'] or '-', 4), _pad(f['fp'] or '-', 12),
            _clip(f['note'], 38)]) + mark)


# ---------------------------------------------------------------- 恢复

def _live_base(key):
    for base, k in _B().PAIRS:
        if k == key:
            return base
    return None


def _under(child, parent):
    """child 是否落在 parent 里面（大小写不敏感）。"""
    c = os.path.abspath(child).lower()
    p = os.path.abspath(parent).lower()
    return c == p or c.startswith(p + os.sep)


def nested_in_live(path):
    """path 落在某个「活存档根」里则返回那个根，否则 None。

    ★ 实测踩过：归档目录放进存档根 ⇒ `do_backup` 把**上一次的备份**当存档内容
      再复制一遍，**2 分半滚出 553 MB / 280 份清单**（指数膨胀，界面毫无提示）。
    """
    for base, _ in _B().PAIRS:
        if _under(path, base):
            return base
    return None


def entries_of(backup_dir, char=None, whole=False):
    """备份 -> {相对路径: 清单信息}。

    `whole=False` 且给了 char 时：只取 `<...>/main/_<char>/...` 那些文件。
    ⇒ **别的角色、共享仓库(transfer.gst)、设置一概不动。**
    """
    man = json.load(open(os.path.join(backup_dir, 'MANIFEST.json'),
                         encoding='utf-8'))['files']
    if whole or not char:
        return dict(man)
    tag = '/main/_%s/' % char.lstrip('_')
    return {k: v for k, v in man.items() if tag in k}


def _rel_of(abs_path):
    """活存档绝对路径 -> 备份清单里的相对键；不在任何活根下则 None。"""
    ap = abs_path.lower()
    for base, key in _B().PAIRS:
        b = base.lower()
        if ap == b or ap.startswith(b + os.sep):
            return (key + '/' + os.path.relpath(abs_path, base)).replace(os.sep, '/')
    return None


def mirror_extras(backup_dir, char):
    """`--mirror` 用：目标角色目录里「备份中没有」的活文件（待删）。

    只看**该角色目录**（备份里出现过的那些文件的父目录），所以不会碰到别的角色。
    """
    rels = entries_of(backup_dir, char)
    have = {r.lower() for r in rels}
    dirs = set()
    for rel in rels:
        key, rest = rel.split('/', 1)
        base = _live_base(key)
        if base:
            dirs.add(os.path.dirname(os.path.join(base, rest.replace('/', os.sep))))
    out, seen = [], set()
    for d in sorted(dirs):
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for fn in files:
                a = os.path.join(root, fn)
                if a.lower() in seen:
                    continue
                seen.add(a.lower())
                rel = _rel_of(a)
                if rel and rel.lower() not in have:
                    out.append(a)
    return out


def do_restore(backup_dir, rels, char=None, mirror=False):
    """把 `rels` 从备份覆盖写回活存档。返回 (写入数, 跳过清单, 删除数)。"""
    n, skip, victims = 0, [], []
    if mirror and char:
        victims = [v for v in mirror_extras(backup_dir, char)
                   if not os.path.basename(v).lower().endswith('.log')]
        for v in victims:
            try:
                os.remove(v)
            except OSError as e:                             # noqa: PERF203
                say('  ! 删不掉 %s (%s)' % (v, e))
    for rel in sorted(rels):
        key, rest = rel.split('/', 1)
        base = _live_base(key)
        if not base:
            skip.append(rel)
            continue
        src = os.path.join(backup_dir, rel.replace('/', os.sep))
        dst = os.path.join(base, rest.replace('/', os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            shutil.copy2(src, dst)
            n += 1
        except OSError as e:
            skip.append('%s (%s)' % (rel, e))
    return n, skip, len(victims)


def _diff_line(cur, tgt, char):
    rows = []
    for k, label in (('level', '等级'), ('physique', '体格'), ('cunning', '狡诈'),
                     ('spirit', '精神'), ('attribute_points', '未分配')):
        a, b = cur.get(k), tgt.get(k)
        if a != b:
            rows.append('%s %s -> %s' % (label, a, b))
    cw = [x.rsplit('/', 1)[-1] for x in ((cur.get('weapons') or {}).get('alt2') or []) if x]
    tw = [x.rsplit('/', 1)[-1] for x in ((tgt.get('weapons') or {}).get('alt2') or []) if x]
    if cw != tw:
        rows.append('武器 %s -> %s' % ('、'.join(cw) or '?', '、'.join(tw) or '?'))
    return rows


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(
        prog='gd_restore.py',
        description='一键换档：列出存档备份并恢复最新（或指定一份）')
    ap.add_argument('-l', '--list', action='store_true', help='只列备份，不恢复')
    ap.add_argument('-n', '--index', type=int, help='恢复列表里的第 N 份（1 起，1=最新）')
    ap.add_argument('--dir', help='直接指定备份目录')
    ap.add_argument('-y', '--yes', action='store_true', help='不再询问，直接恢复')
    ap.add_argument('--dry-run', action='store_true', help='预演，永不写盘')
    ap.add_argument('--all', action='store_true',
                    help='整包恢复（含其他角色 / 共享仓库 / 设置）')
    ap.add_argument('--char', default='Sam', help='目标角色（默认 Sam）')
    ap.add_argument('--limit', type=int, default=15, help='列表显示条数（0 = 全部）')
    ap.add_argument('--verify', action='store_true', help='恢复前用 MANIFEST 逐文件校验')
    ap.add_argument('--mirror', action='store_true',
                    help='删除目标角色目录里「备份中没有」的旧文件')
    ap.add_argument('--force', action='store_true',
                    help='越过「游戏运行中 / 备份校验失败」两道门')
    ap.add_argument('--archives', default=None, help='备份根目录（默认 E:\\xz\\Archives）')
    ap.add_argument('--live', default=None,
                    help='活存档目录覆盖（设 GD_SAVE；改副本调试时用）')
    a = ap.parse_args(argv)

    # ★ `--live` 必须在任何 gd 导入之前设进环境（PAIRS 在 import 时就求值了）
    _av = list(sys.argv if argv is None else argv)
    _live = None
    for i, x in enumerate(_av):
        if x == '--live' and i + 1 < len(_av):
            _live = _av[i + 1]
        elif x.startswith('--live='):
            _live = x.split('=', 1)[1]
    if _live:
        os.environ['GD_SAVE'] = _live
        # ★★ `paths.save_dir()` 在 GD_SAVE 指向不存在/无 main 的目录时会**静默回落到真实存档**
        #    ⇒ 打错一个字母就会「以为在改副本、其实在改真档」。这里硬校验，绝不放行。
        if not os.path.isdir(os.path.join(_live, 'main')):
            say('✗ --live 指向的目录里没有 main/ ：%s' % _live)
            say('   （paths.save_dir() 会因此静默回落到**真实存档**，拒绝继续。）')
            return 1

    B = _B()
    # ★ 先拦「归档目录落在存档根里」—— 否则 preflight 的自动备份会指数膨胀
    for _p, _what in ((a.archives or B.ARCHIVES, '备份根目录'),
                      (a.dir, '指定的备份目录')):
        if not _p:
            continue
        _bad = nested_in_live(_p)
        if _bad:
            say('✗ %s 落在活存档根内：%s' % (_what, _p))
            say('  活存档根 = %s' % _bad)
            say('  备份会把「备份」再当存档复制 ⇒ 指数膨胀。请把归档目录换到存档之外。')
            return 1
    backups = find_backups(a.archives)
    if not backups:
        say('✗ 没找到任何备份：%s' % (a.archives or B.ARCHIVES))
        say('  先跑一次：python -m gd backup backup "换档前"')
        return 1

    cur_st, cur_fp = live_state(a.char)
    say('备份目录 %s ｜ 共 %d 份' % (a.archives or B.ARCHIVES, len(backups)))
    say('当前存档 %s  lv%s  %s/%s/%s  未分配 %s  指纹 %s'
        % (a.char, cur_st.get('level'), cur_st.get('physique'), cur_st.get('cunning'),
           cur_st.get('spirit'), cur_st.get('attribute_points'), cur_fp or '?'))
    say('')

    show = backups if a.limit <= 0 else backups[:a.limit]
    infos = [backup_info(d, a.char) for _, d in show]
    print_list(infos, cur_fp)
    if len(backups) > len(show):
        say('  ... 另有 %d 份更早的（--limit 0 列全部）' % (len(backups) - len(show)))
    if a.list:
        return 0

    # ---- 选目标
    if a.dir:
        target = os.path.abspath(a.dir)
        if not os.path.isdir(target):
            say('✗ 目录不存在：%s' % target)
            return 1
    elif a.index:
        if not 1 <= a.index <= len(backups):
            say('✗ 序号 %d 超出范围（1~%d）' % (a.index, len(backups)))
            return 1
        target = backups[a.index - 1][1]
    else:
        if not sys.stdin.isatty():
            say('非交互环境：请加 -n N 或 -y 指定要恢复哪一份。')
            return 1
        raw = ask('恢复哪一份？[回车 = 最新 #1 / 输入序号 / q 退出] ')
        if raw is None:
            say('[预演结束] 未写盘。')
            return 0
        if raw.lower() in ('q', 'quit', 'exit'):
            return 0
        idx = 1
        if raw:
            if not raw.isdigit() or not 1 <= int(raw) <= len(backups):
                say('✗ 无效序号：%s' % raw)
                return 1
            idx = int(raw)
        target = backups[idx - 1][1]

    ti = backup_info(target, a.char)
    rels = entries_of(target, a.char, whole=a.all)
    if not rels:
        say('✗ 这份备份里没有 %s 的文件：%s' % (a.char, target))
        if not a.all:
            say('  （若是别的角色：--char <名字>；想整包：--all）')
        return 1

    # ---- 计划
    say('')
    say('目标备份 %s' % target)
    say('  备份时间 %s ｜ 备注 %s' % (ti['time'] or '?', ti['note'] or '(无)'))
    say('  含角色 %s' % ('、'.join(ti['chars']) or '?'))
    say('  待写回 %d 个文件 ｜ 范围 = %s'
        % (len(rels), '整包（含其他角色/共享仓库/设置）' if a.all
           else '仅角色 %s' % a.char))
    if ti['state']:
        rows = _diff_line(cur_st, ti['state'], a.char)
        say('  与当前相比：%s' % ('；'.join(rows) if rows else '无差异（内容相同）'))
    say('  存档指纹 %s -> %s' % (cur_fp or '?', ti['fp'] or '?'))

    if a.verify:
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            ok = B.do_verify(target)
        finally:
            sys.stdout = old
        detail = [ln.strip() for ln in buf.getvalue().splitlines() if ln.strip()]
        say('  备份校验 %s ｜ %s' % ('完好' if ok else '**异常**',
                                     detail[0] if detail else ''))
        if not ok and not a.force:
            say('✗ 备份校验未通过 —— 换一份，或 --force 强行恢复。')
            return 1

    if a.mirror and not a.all:
        # ★ 删除是不可逆的 ⇒ 把**待删清单**在执行前逐条打出来（默认只覆盖不删）
        _ex = [v for v in mirror_extras(target, a.char)
               if not os.path.basename(v).lower().endswith('.log')]
        say('  --mirror 将删除 %d 个「备份中没有」的旧文件：' % len(_ex))
        for _v in _ex[:12]:
            say('      - %s' % _v)
        if len(_ex) > 12:
            say('      ... 另有 %d 个' % (len(_ex) - 12))

    if not a.all and a.char:
        say('  ⓘ 共享仓库(transfer.gst) / 其他角色 / 设置**不在**本次范围；要整包加 --all。')

    if not a.yes and not a.dry_run:
        if not sys.stdin.isatty():
            say('\n[预演结束] 非交互环境未写盘。确认无误后加 -y 执行。')
            return 0
        say('')
        ans = ask('确认恢复？[y/N] ')
        if ans is None or ans.lower() not in ('y', 'yes'):
            say('已取消，未写盘。')
            return 0
    if a.dry_run:
        say('\n[预演结束] --dry-run 未写盘。')
        return 0

    # ---- 三道门
    if game_running() and not a.force:
        say('\n✗ 检测到 Grim Dawn 正在运行 —— 游戏退出时会把内存态写回盘，恢复了也白恢复。')
        say('  请先退出游戏再跑本脚本（确实要强行写：--force）。')
        return 1

    # ---- 执行
    say('')
    safe = B.do_backup(note='换档前自动备份（恢复 %s）' % ti['name'])
    say('恢复前快照 -> %s' % safe)
    n, skip, ndel = do_restore(target, rels, char=a.char, mirror=a.mirror)
    say('已写回 %d 个文件' % n)
    if ndel:
        say('  按 --mirror 清掉备份中没有的旧文件 %d 个' % ndel)
    if skip:
        say('  ! 跳过 %d 个：%s' % (len(skip), '、'.join(skip[:5])))

    st2, fp2 = live_state(a.char)
    say('')
    say('现在 %s  lv%s  %s/%s/%s  未分配 %s  指纹 %s%s'
        % (a.char, st2.get('level'), st2.get('physique'), st2.get('cunning'),
           st2.get('spirit'), st2.get('attribute_points'), fp2 or '?',
           '  [已回到该备份状态]' if fp2 and fp2 == ti['fp'] else ''))
    say('回退：python tools/gd_restore.py --dir "%s" -y' % safe)
    say('复核：python tools/save_state.py %s ｜ python tools/reqcheck.py %s'
        % (a.char, a.char))
    say('⚠ Steam 云同步：进游戏前建议先让 Steam 同步完（或离线启动），'
        '否则云端旧档可能被推回来。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
