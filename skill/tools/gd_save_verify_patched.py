# -*- coding: utf-8 -*-
"""★ 统一体检 —— 一条命令跑完端到端校验

此前检查逻辑散落在 4 个写入脚本里各写一遍，容易漏项。这里收敛为唯一实现。

用法：
    python gd_verify.py                   # 全角色体检（解析/块校验/往返/未解析段）
    python gd_verify.py Sam               # 单角色详查（装备槽 / seed / 镶嵌 / 点数）
    python gd_verify.py --diff <备份目录>  # 与备份逐字节比对
    python gd_verify.py --preflight       # 写盘前安全检查

退出码：0 = 通过，1 = 有问题
"""
import argparse
import glob
import hashlib
import os
import sys

from .. import _legacyenv as ENV
from .. import _timing as TM

from . import core as S

from .. import paths as _PATHS

# 旧脚本用 HERE 拼数据文件路径；新架构下数据在技能的 data/ 里
HERE = str(_PATHS.DATA_DIR)
_CACHE_ROOT = str(_PATHS.CACHE_DIR)
_PLANS = str(_PATHS.CACHE_DIR / 'plans')


# 实测真实 seed 的取值区间（620 组件 + 1550 物品样本）
SEED_LO, SEED_HI = 9_296_907, 2_143_745_881
SLOTS = ['头部', '项链', '胸甲', '腿甲', '靴子', '手套', '戒指1', '戒指2',
         '腰带', '肩甲', '勋章', '圣物']


def char_paths():
    save, _ = ENV.find_save_dir()
    if not save:
        return {}
    out = {}
    for p in sorted(glob.glob(os.path.join(save, 'main', '*', 'player.gdc'))):
        out[os.path.basename(os.path.dirname(p))] = p
    return out


def norm(name):
    return name if name.startswith('_') else '_' + name


# ------------------------------------------------------------------ 全角色
def verify_all(verbose=True):
    paths = char_paths()
    if not paths:
        print('✗ 未找到存档目录（设置 GD_SAVE 或检查游戏是否安装）')
        return 1
    print('=' * 92)
    print('全角色体检 —— %d 个角色' % len(paths))
    print('=' * 92)
    allok = True
    for name, p in paths.items():
        try:
            raw = open(p, 'rb').read()
            d = S.parse(p, record=True)
        except Exception as e:
            print('  ✗ %-8s 解析失败: %s: %s' % (name, type(e).__name__, e))
            allok = False
            continue
        blocks_ok = d['all_blocks_ok']
        roundtrip = S.encode(d['record']['image'], d['record']['plan'], d['seed']) == raw
        unresolved = sum(1 for k, _a, _b in d['record']['plan'] if k == 'x')
        ok = blocks_ok and roundtrip and unresolved == 0
        allok &= ok
        print('  %s %-8s lv%-3s %-22s 块校验=%-5s 往返=%-5s 未解析段=%d  %d 字节' % (
            '✓' if ok else '✗', name, d['level'], '+'.join(d['classes']),
            blocks_ok, roundtrip, unresolved, len(raw)))
        if not ok:
            for b in d['blocks']:
                if b['error']:
                    print('        块%d 异常: %s' % (b['id'], b['error']))
    print()
    print('  结论：%s' % ('全部通过 ★' if allok else '存在问题，见上'))
    return 0 if allok else 1


# ------------------------------------------------------------------ 单角色
def verify_char(name, verbose=True):
    paths = char_paths()
    key = None
    for k in paths:
        if k.lstrip('_').lower() == name.lstrip('_').lower():
            key = k
            break
    if key is None:
        print('✗ 找不到角色 %r；可选: %s' % (name, ', '.join(paths) or '（无）'))
        return 1
    p = paths[key]
    raw = open(p, 'rb').read()
    d = S.parse(p, record=True)
    b3 = d['block_map'].get(3)
    errs, warns = [], []

    if not d['all_blocks_ok']:
        errs.append('块校验失败: %s' % [b['id'] for b in d['blocks'] if not b['ok']])
    if S.encode(d['record']['image'], d['record']['plan'], d['seed']) != raw:
        errs.append('往返不一致')
    if any(k == 'x' for k, _a, _b in d['record']['plan']):
        errs.append('存在未解析段（改档会波及）')

    print('=' * 92)
    print('%s 详查 —— lv%s %s ｜ %d 字节 ｜ md5 %s' % (
        key, d['level'], '+'.join(d['classes']), len(raw), hashlib.md5(raw).hexdigest()[:16]))
    print('=' * 92)

    b2 = d['block_map'].get(2) or {}
    # ★ 这里读的是**存档原始值** = 50 + 8×已投点数，**不含精通与装备**。
    #   旧标签写成「面板」会误导（面板还要加精通逐级曲线 + 装备平值再乘装备%值）。
    #   真面板请看 `gd dps` / `tools/reqcheck.py` 的「★ 面板」行。
    _ph = float(b2.get('physique') or 0)
    _cu = float(b2.get('cunning') or 0)
    _sp = float(b2.get('spirit') or 0)
    print('  属性 : 体格 %.0f ｜ 狡诈 %.0f ｜ 精神 %.0f ｜ 生命 %.0f'
          '   ← 存档值(50+8×加点)，非面板' % (
              _ph, _cu, _sp, float(b2.get('health') or 0)))
    print('        折算已投点: 体格 %.0f ｜ 狡诈 %.0f ｜ 精神 %.0f ｜ 合计 %.0f' % (
        (_ph - 50) / 8, (_cu - 50) / 8, (_sp - 50) / 8, (_ph + _cu + _sp - 150) / 8))
    print('  点数 : 属性 %s ｜ 技能 %s ｜ 虔诚 %s ｜ 铁币 %s' % (
        b2.get('attribute_points'), b2.get('skill_points'), b2.get('devotion_points'),
        (d['block_map'].get(1) or {}).get('iron')))

    if b3:
        print()
        print('  装备栏（attached=False 表示该格为空）：')
        seed_bad = []
        for i, it in enumerate(b3['equipment']):
            base = (it['basename'] or '').split('/')[-1]
            ex = []
            if it['prefix']:
                ex.append('前:' + it['prefix'].split('/')[-1])
            if it['suffix']:
                ex.append('后:' + it['suffix'].split('/')[-1])
            if it['relic_name']:
                ex.append('镶:' + it['relic_name'].split('/')[-1])
                if not (SEED_LO <= it['relic_seed'] <= SEED_HI):
                    seed_bad.append('%s.%s' % (SLOTS[i] if i < 12 else i,
                                               it['relic_name'].split('/')[-1]))
            if it['augment_name']:
                ex.append('附:' + it['augment_name'].split('/')[-1])
                if not (SEED_LO <= it['augment_seed'] <= SEED_HI):
                    seed_bad.append('%s.附魔' % (SLOTS[i] if i < 12 else i))
            print('     %-6s %-5s %-32s %s' % (
                SLOTS[i] if i < 12 else i, it['attached'],
                base or '(空)', ' '.join(ex)))
        for tag in ('alt1', 'alt2'):
            for j, it in enumerate(b3.get(tag) or []):
                if not it.get('basename'):
                    continue
                ex = []
                if it['relic_name']:
                    ex.append('镶:' + it['relic_name'].split('/')[-1])
                    if not (SEED_LO <= it['relic_seed'] <= SEED_HI):
                        seed_bad.append('%s[%d]' % (tag, j))
                print('     %-6s %-5s %-32s %s' % (tag, it['attached'],
                                                    it['basename'].split('/')[-1], ' '.join(ex)))
        # 记录存在性核对（用统一映射库的池子做存在性判断）
        try:
            from . import savemap as MAP
            missing = set()
            for lst in (b3['equipment'], b3.get('alt1') or [], b3.get('alt2') or []):
                for it in lst:
                    for k in ('basename', 'prefix', 'suffix', 'relic_name', 'augment_name'):
                        v = it.get(k)
                        if v and not MAP.exists(v):
                            missing.add(v)
            if missing:
                errs.append('以下记录在游戏库中不存在（会被游戏拒装）: %s'
                            % sorted(x.split('/')[-1] for x in missing))
        except Exception:
            pass
        if seed_bad:
            errs.append('镶嵌/附魔 seed 越界（游戏里会表现为无效）: %s' % seed_bad)
        if not any(it['relic_name'] for it in b3['equipment'] + (b3.get('alt1') or [])):
            warns.append('没有任何镶嵌/附魔')

    b8 = d['block_map'].get(8)
    if b8:
        nz = [(s['skill'].split('/')[-1], s['level']) for s in b8['skills'] if s['level'] > 0]
        print()
        print('  技能 : %d 条非零 ｜ 精通 %s' % (len(nz), [
            '%s=%d' % (a, b) for a, b in nz if 'classtraining' in a] or '—'))

    print()
    for w in warns:
        print('  ⚠ %s' % w)
    for e in errs:
        print('  ✗ %s' % e)
    if not errs:
        print('  ✓ 体检通过')
    return 0 if not errs else 1


# ------------------------------------------------------------------ 与备份比对
def verify_diff(bkdir):
    """与备份目录逐字节比对（备份目录下应是 Steam云存档_*/ 与 我的文档_*/）"""
    if not os.path.isdir(bkdir):
        print('✗ 备份目录不存在: %s' % bkdir)
        return 1
    sub = None
    for c in os.listdir(bkdir):
        if os.path.isdir(os.path.join(bkdir, c, 'remote')) or '云存档' in c:
            sub = os.path.join(bkdir, c)
            break
    if sub is None:
        sub = bkdir
    live = ENV.find_save_dir()[0]
    if not live:
        print('✗ 未找到现存档目录')
        return 1

    def md5(p):
        h = hashlib.md5()
        with open(p, 'rb') as f:
            for c in iter(lambda: f.read(1 << 20), b''):
                h.update(c)
        return h.hexdigest()

    changed, same, missing = [], 0, []
    for root, _d, fs in os.walk(live):
        for fn in fs:
            a = os.path.join(root, fn)
            rel = os.path.relpath(a, live)
            b = os.path.join(sub, rel)
            if not os.path.exists(b):
                missing.append(rel)
            elif md5(a) == md5(b):
                same += 1
            else:
                changed.append(rel)
    print('=' * 92)
    print('与备份比对：%s' % bkdir)
    print('=' * 92)
    print('  一致 %d 个 ｜ 变更 %d 个 ｜ 备份中缺失 %d 个' % (same, len(changed), len(missing)))
    for c in changed:
        print('     变更 %s' % c)
    for c in missing:
        print('     缺失 %s' % c)
    return 0 if not changed and not missing else 1


# ------------------------------------------------------------------ 写盘前安全检查
def verify_preflight():
    print('=' * 92)
    print('写盘前安全检查')
    print('=' * 92)
    problems = []
    s = ENV.summary()
    print('  存档目录 : %s' % (s['save_dir'] or '✗ 未找到'))
    if not s['save_dir']:
        problems.append('未找到存档目录')
    print('  游戏目录 : %s' % (s['game_dir'] or '✗ 未找到'))
    if not s['game_dir']:
        problems.append('未找到游戏目录（映射与验证会失效）')
    print('  数据库   : %d 个 .arz' % len(s['arz_files']))
    if not s['arz_files']:
        problems.append('未找到 .arz 数据库')
    print('  数据缓存 : %s' % (s['data_dir'] or '✗（GT 相关功能不可用）'))

    running = False
    try:
        sys.path.insert(0, HERE)
        from . import patch as E
        running = E.game_running()
    except Exception:
        import subprocess
        try:
            out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq Grim Dawn.exe'],
                                 capture_output=True, text=True, timeout=15).stdout
            running = 'Grim Dawn.exe' in out
        except Exception:
            pass
    print('  游戏进程 : %s' % ('⚠ 正在运行' if running else '未运行 ✓'))
    if running:
        problems.append('游戏正在运行 —— 必须先完全退出，否则写入会被覆盖')

    paths = char_paths()
    print('  角色数   : %d（%s）' % (len(paths), ', '.join(paths)))
    if not paths:
        problems.append('没有找到任何角色存档')

    bk = sorted(glob.glob(os.path.join(r'E:\xz\Archives', 'GrimDawn_存档备份_*')))
    print('  可用备份 : %d 个' % len(bk))
    if bk:
        print('     最近: %s' % os.path.basename(bk[-1]))
    else:
        problems.append('没有可用备份 —— 强烈建议先跑 gd_backup.py backup')

    print()
    print('  ⚠ 提醒：进游戏前务必在 设置 → Gameplay 关闭 Cloud Saving，')
    print('     否则 Steam 会用云端旧档覆盖本地修改。')
    print()
    if problems:
        for p in problems:
            print('  ✗ %s' % p)
        return 1
    print('  ✓ 环境就绪，可以写入')
    return 0


def main():
    ap = argparse.ArgumentParser(description='恐怖黎明存档 · 统一体检')
    ap.add_argument('char', nargs='?', help='角色名（不含下划线也可）')
    ap.add_argument('--diff', metavar='备份目录', help='与备份逐字节比对')
    ap.add_argument('--preflight', action='store_true', help='写盘前安全检查')
    a = ap.parse_args()
    if a.preflight:
        return verify_preflight()
    if a.diff:
        return verify_diff(a.diff)
    if a.char:
        return verify_char(a.char)
    return verify_all()


if __name__ == '__main__':
    sys.exit(main())
