# -*- coding: utf-8 -*-
r"""
恐怖黎明存档 备份 / 校验 / 恢复 工具

  python gd_backup.py backup              # 备份到 E:\xz\Archives\GrimDawn_存档备份_<时间戳>
  python gd_backup.py verify <备份目录>    # 用 MANIFEST.json 校验备份是否完好
  python gd_backup.py diff <备份目录>      # 对比当前存档与备份的差异
  python gd_backup.py restore <备份目录> --yes   # 恢复(会先自动备份当前存档)

安全设计:
  * restore 必须显式加 --yes, 否则只打印将要做什么
  * restore 前自动把当前存档再备份一份, 保证可回退
  * 不做任何删除操作, 只覆盖写入
"""
import datetime
import hashlib
import json
import os
import shutil
import sys

from .. import _timing as TM

import os as _os
from .. import paths as _P

# 归档目录沿用用户约定 E:\xz\Archives（可用 GD_BACKUP_DIR 覆盖）。
# 旧实现同时写死了 userdata id（1016762644）与用户名，换机器就失效 —— 这里改为探测。
ARCHIVES = _os.environ.get('GD_BACKUP_DIR') or r'E:\xz\Archives'


def _pairs():
    """[(要备份的目录, 归档内的子目录名)]

    ★ 根目录必须是 **Steam app 目录**（`.../userdata/<id>/219990`），**不含 `remote`**。
      原因：`gd/save/patch.py` 用 `join(LIVE_SAVE, 'remote', 'save')` 定位存档、
      `restore()` 用 `join(LIVE_SAVE, rest)`，两者都以 app 目录为基。
      历史回归：这里一度改成 `sd.parent`（`.../219990/remote`），导致补丁拼出
      `remote/remote/save/...` 而报「找不到角色档」。见 tools/migrate_from_archive.py
      里归档的原始定义（LIVE_SAVE = app 目录）。
    """
    out = []
    sd, _kind = _P.save_dir()
    if sd is not None:
        app_root = sd.parent
        if app_root.name.lower() == 'remote':      # `.../219990/remote/save`
            app_root = app_root.parent
        out.append((str(app_root), 'Steam云存档_%s' % _P.STEAM_APPID))
    cd = _P.config_dir()
    if cd is not None and str(cd) not in [x[0] for x in out]:
        out.append((str(cd), '我的文档_MyGames_GrimDawn'))
    return out


PAIRS = _pairs()          # ★ 必须在这里求值：do_backup/snapshot 都直接遍历 PAIRS


def _live_roots():
    """从 PAIRS 反推两个「活」根目录。

    ★ 旧版把 `LIVE_SAVE` / `LIVE_DOC` 写死成空字符串 —— `snapshot()` 不受影响
      （它只遍历 PAIRS），但 `restore()` 与 `gd.save.patch`（`SAVE_DIR` 由
      `LIVE_SAVE` 拼出）会指向相对路径 `remote\\save\\...`，必然报「找不到角色档」。
    """
    save_root, doc_root = '', ''
    for base, key in PAIRS:
        if key.startswith('Steam云存档'):
            save_root = base
        elif key.startswith('我的文档') or 'GrimDawn' in key:
            doc_root = base
    return save_root, doc_root


LIVE_SAVE, LIVE_DOC = _live_roots()


def md5(p):
    h = hashlib.md5()
    with open(p, 'rb') as f:
        for c in iter(lambda: f.read(1 << 20), b''):
            h.update(c)
    return h.hexdigest()


def snapshot():
    """返回 {相对路径: (绝对路径, 大小, md5)}；跳过被占用/读不到的文件"""
    out = {}
    for base, key in PAIRS:
        for root, _, files in os.walk(base):
            for fn in files:
                a = os.path.join(root, fn)
                rel = (key + '/' + os.path.relpath(a, base)).replace('\\', '/')
                try:
                    out[rel] = (a, os.path.getsize(a), md5(a))
                except OSError:
                    continue        # 游戏运行中 log.html/log.xml 会被独占锁定
    return out


def copy_tree_tolerant(src, dst):
    """复制目录树，跳过被其他进程独占的文件（游戏运行时会锁 log.html / log.xml）

    原实现用 shutil.copytree，遇到任一锁定文件会整体抛错、备份直接失败。
    """
    skipped = []
    for root, _, files in os.walk(src):
        rel = os.path.relpath(root, src)
        out = dst if rel == '.' else os.path.join(dst, rel)
        os.makedirs(out, exist_ok=True)
        for fn in files:
            s, dd = os.path.join(root, fn), os.path.join(out, fn)
            try:
                shutil.copy2(s, dd)
            except OSError:
                skipped.append(s)
    return skipped


def _under(child, parent) -> bool:
    """child 是否就是 parent 或落在 parent 里面（大小写不敏感，Windows 用）。"""
    c, p = os.path.abspath(child).lower(), os.path.abspath(parent).lower()
    return c == p or c.startswith(p + os.sep)


def do_backup(note=''):
    ts = datetime.datetime.now().strftime('%Y-%m-%d_%H%M%S')
    dst = os.path.join(ARCHIVES, 'GrimDawn_存档备份_%s' % ts)
    # ★ 护栏（2026-09-20 实测踩到）：归档目录若落在**活存档根**里面，
    #   下一次备份会把「上一次的备份」当存档内容再复制一遍 ⇒
    #   2 分半钟滚出 **553 MB / 280 份清单**（指数膨胀，且神不知鬼不觉）。
    #   正常配置（`E:\xz\Archives`）永远碰不到这条；一旦碰到就是配置错了。
    for _base, _ in PAIRS:
        if _under(dst, _base):
            raise SystemExit(
                '✗ 备份目录落在活存档根内：%s\n'
                '  备份会把「备份」再当存档复制 ⇒ 指数膨胀。\n'
                '  请把归档目录（GD_BACKUP_DIR）换到存档目录之外。' % _base)
    n = 0
    for base, key in PAIRS:
        target = os.path.join(dst, key)
        if os.path.exists(target):
            target = target + '_1'
        skipped = copy_tree_tolerant(base, target)
        if skipped:
            print('  跳过被占用文件 %d 个: %s' % (len(skipped),
                  '、'.join(sorted(os.path.basename(x) for x in skipped))))
        n += sum(len(f) for _, _, f in os.walk(target))
    snap = snapshot()
    json.dump({'备份时间': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
               '备注': note,
               'files': {k: {'size': v[1], 'md5': v[2]} for k, v in snap.items()}},
              open(os.path.join(dst, 'MANIFEST.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('已备份 %d 个文件 -> %s' % (n, dst))
    return dst


def load_manifest(d):
    p = os.path.join(d, 'MANIFEST.json')
    if not os.path.exists(p):
        raise SystemExit('缺少 MANIFEST.json：%s' % d)
    return json.load(open(p, encoding='utf-8'))


def do_verify(d):
    man = load_manifest(d)['files']
    bad, miss = [], []
    for rel, info in man.items():
        p = os.path.join(d, rel.replace('/', os.sep))
        if not os.path.exists(p):
            miss.append(rel)
        elif md5(p) != info['md5']:
            bad.append(rel)
    print('清单 %d 个文件 ｜ 缺失 %d ｜ 内容不符 %d' % (len(man), len(miss), len(bad)))
    for x in miss[:10]:
        print('   缺失', x)
    for x in bad[:10]:
        print('   不符', x)
    if not miss and not bad:
        print('备份完好 ★')
    return not miss and not bad


def do_diff(d):
    man = load_manifest(d)['files']
    cur = snapshot()
    changed = [k for k in man if k in cur and cur[k][2] != man[k]['md5']]
    added = [k for k in cur if k not in man]
    removed = [k for k in man if k not in cur]
    print('与备份相比：变更 %d ｜ 新增 %d ｜ 丢失 %d' % (len(changed), len(added), len(removed)))
    for k in changed:
        print('   变更 %s' % k)
    for k in added:
        print('   新增 %s' % k)
    for k in removed:
        print('   丢失 %s' % k)
    return changed


def do_restore(d, yes=False):
    man = load_manifest(d)['files']
    if not yes:
        print('[预演] 将从备份恢复 %d 个文件到:' % len(man))
        for base, _ in PAIRS:
            print('   ', base)
        print('确认请加 --yes（会先把当前存档备份一份）')
        return False
    safe = do_backup(note='恢复前自动备份')
    print('恢复前快照已保存 -> %s' % safe)
    n = 0
    for rel in man:
        src = os.path.join(d, rel.replace('/', os.sep))
        key, rest = rel.split('/', 1)
        base = LIVE_SAVE if key == 'Steam云存档_219990' else LIVE_DOC
        dst = os.path.join(base, rest.replace('/', os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    print('已恢复 %d 个文件。请用 verify 与 diff 复核。' % n)
    return True


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'help'
    if cmd == 'backup':
        do_backup(' '.join(sys.argv[2:]))
    elif cmd == 'verify':
        do_verify(sys.argv[2])
    elif cmd == 'diff':
        do_diff(sys.argv[2])
    elif cmd == 'restore':
        do_restore(sys.argv[2], '--yes' in sys.argv)
    else:
        print(__doc__)
