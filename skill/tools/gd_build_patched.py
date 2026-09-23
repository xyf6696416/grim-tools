# -*- coding: utf-8 -*-
"""★ 方案构建器 —— 方案定义外置 + 记录名全自动解析 + 验证 + 写入

设计目标（消除三类脆弱点）：
  1. 记录名不再人工填写 —— 方案里只写 GT id（itXXXX），由 gd_map 自动解析
  2. 方案可版本化 —— 存成 plans/*.json，可审查、可复现、可 diff
  3. 验证与写入分离 —— plan 子命令只出清单，apply 才写盘（自动备份 + 复检）

方案文件格式（plans/*.json）：
    {
      "char": "_Sam",
      "note": "装备升级 v3",
      "equipment": [
        {"slot": "头部", "item": "it558",
         "prefix": "aa004a_cunmod_01", "suffix": "b_ar005_ar_b", "component": "it2865"}
      ],
      "weapons": [
        {"slot": "主手", "item": "it2754", "component": "it2878"}
      ]
    }
  · item / component 用 GT id（itXXXX），自动解析成记录路径
  · prefix / suffix 可用 GT id（preXXXX / sufXXXX）或直接写记录名
    （推荐直接写记录名 —— 从该角色原存档采集，天然合法）

用法：
    python gd_build.py new <角色> [--out plans/xxx.json]   # 从当前存档导出草稿方案
    python gd_build.py plan plans/xxx.json                 # 解析 + 验证 + 出清单
    python gd_build.py apply plans/xxx.json                # 写入（自动备份 + 复检）
"""
import argparse
import json
import os
import random
import shutil
import sys

from . import _legacyenv as ENV
from . import savemap as MAP
from .save import core as S
from .save import write as W

from . import paths as _PATHS

# 旧脚本用 HERE 拼数据文件路径；新架构下数据在技能的 data/ 里
HERE = str(_PATHS.DATA_DIR)
_CACHE_ROOT = str(_PATHS.CACHE_DIR)
_PLANS = str(_PATHS.CACHE_DIR / 'plans')


SEED_LO, SEED_HI = 9_296_907, 2_143_745_881
PLACEHOLDER = {0, 1, 803563914, 1234567}
SLOTS = ['头部', '项链', '胸甲', '腿甲', '靴子', '手套', '戒指1', '戒指2',
         '腰带', '肩甲', '勋章', '圣物']
SLOT_IDX = {s: i for i, s in enumerate(SLOTS)}
# 武器组内索引（主手/副手）—— 与装备槽是两套编号，别混用
WIDX = {'主手': 0, '副手': 1}


# ------------------------------------------------------------------ 解析
def resolve_item(spec_gt):
    """GT id → (记录名, 依据)；找不到返回 (None, 原因)"""
    o = MAP.gt_items().get(spec_gt)
    if not o:
        return None, 'GT 里没有 %s' % spec_gt
    return MAP.resolve(o)


def resolve_component(gt):
    if not gt:
        return '', '无镶嵌'
    o = MAP.gt_items().get(gt)
    if not o:
        return None, '组件 %s 不在 GT 库' % gt
    return MAP.resolve_comp(o)


def resolve_augment(gt):
    """附魔（augment）：GT id 或记录名。规则见 gd_map.resolve_enchant（含符文）"""
    if not gt:
        return '', '无附魔'
    if gt.startswith('records/'):
        return (gt, '记录名') if MAP.exists(gt) else (None, '记录不存在 %s' % gt)
    o = MAP.gt_items().get(gt)
    if not o:
        return None, '附魔 %s 不在 GT 库' % gt
    return MAP.resolve_enchant(o)


def resolve_affix(v):
    """词缀字段可以直接写记录名，也可以写 GT id（preXXXX / sufXXXX）"""
    if not v:
        return '', '无'
    if v.startswith('records/'):
        return (v, '记录名') if MAP.exists(v) else (None, '记录不存在 %s' % v)
    kind = 'prefix' if v.startswith('pre') else 'suffix'
    rec, why = MAP.resolve_affix(kind, v)
    return rec or '', why


def _active_weapon_set(b3):
    """当前**启用**的是哪一套武器（`'alt1'` / `'alt2'`）。

    ★★ 2026-09-20 新增。为什么必须有：一个角色有**两套武器**（alt1/alt2），
    旧的 `do_apply` **恒写 alt1**、并把 `alt1_unused`/`alt2_unused` 原样保留 ⇒
    若启用的其实是 alt2，**落档后武器完全不生效**：
    实测（Sam 落档 wolf_nightblade_fast）`gd dps Sam` 面板 103,682 → 67,637
    （差 **53%**），且玩家进游戏看到的手上还是旧武器。

    判据**逐字对齐** `gd/dps.py::load_char` 的武器套选择段（两处必须同步改）：
      · `alt2_unused=True` 且 `alt1_unused=False` ⇒ alt1 在用
      · `alt1_unused=True` 且 `alt2_unused=False` ⇒ alt2 在用
      · 两者相同 ⇒ 退回 `use_alt_weaponset`（True ⇒ alt2）
    """
    if b3.get('alt2_unused') and not b3.get('alt1_unused'):
        return 'alt1'
    if b3.get('alt1_unused') and not b3.get('alt2_unused'):
        return 'alt2'
    return 'alt2' if b3.get('use_alt_weaponset') else 'alt1'


def build_plan(spec, char_d=None):
    """方案 JSON → 逐条解析结果 [{...}]

    sp['record'] 可显式指定记录名，覆盖自动解析（用于低置信档位，需写明依据）
    """
    rows = []
    b3 = (char_d or {}).get('block_map', {}).get(3)
    _wset = _active_weapon_set(b3) if b3 else 'alt1'
    for group, key in (('equip', 'equipment'), ('weapon', 'weapons')):
        for i, sp in enumerate(spec.get(key) or []):
            if sp.get('record'):
                rec = sp['record']
                why = '显式指定（%s）' % sp.get('basis', '无依据说明')
                if not MAP.exists(rec):
                    why = '显式指定但记录不存在'
            else:
                rec, why = resolve_item(sp.get('item', ''))
            pre, pw = resolve_affix(sp.get('prefix', ''))
            suf, sw = resolve_affix(sp.get('suffix', ''))
            comp, cw = resolve_component(sp.get('component', ''))
            aug, aw = resolve_augment(sp.get('augment', ''))
            old = None
            if b3:
                if group == 'equip':
                    idx = SLOT_IDX.get(sp.get('slot', ''), -1)
                    if 0 <= idx < len(b3['equipment']):
                        old = b3['equipment'][idx]
                # ★ 对拍要比**启用**的那一套（否则「写完却报不一致」）
                elif i < len(b3.get(_wset) or []):
                    old = b3[_wset][i]
            rows.append({
                'group': group, 'slot': sp.get('slot', ''), 'gt': sp.get('item'),
                'rec': rec, 'why': why,
                'prefix': pre, 'pw': pw, 'suffix': suf, 'sw': sw,
                'component': comp or '', 'cw': cw,
                'augment': aug or '', 'aw': aw,
                'old': old, 'raw': sp,
                'ok': bool(rec and (pre is not False) and (suf is not False)
                           and comp is not None and aug is not None),
            })
    return rows


def print_plan(spec, rows):
    print('=' * 104)
    print('方案：%s   （角色 %s）' % (spec.get('note', '(未命名)'), spec.get('char', '?')))
    print('=' * 104)
    bad = 0
    for r in rows:
        tag = '✓' if r['ok'] else '✗'
        print('  %s %-6s %-9s → %s' % (tag, r['slot'], r['gt'], r['rec'] or '【未解析】'))
        print('        依据: %s' % r['why'])
        extra = []
        if r['prefix']:
            extra.append('前:' + r['prefix'].split('/')[-1])
        if r['suffix']:
            extra.append('后:' + r['suffix'].split('/')[-1])
        if r['component']:
            extra.append('镶:' + r['component'].split('/')[-1])
        if r.get('augment'):
            extra.append('附魔:' + r['augment'].split('/')[-1])
        if extra:
            print('        词缀/镶嵌: %s' % '  '.join(extra))
        if not r['ok']:
            bad += 1
    print()
    print('  共 %d 条，其中 %d 条解析失败' % (len(rows), bad))
    return bad


def verify_rows(rows):
    """写入前的静态校验"""
    errs = []
    for r in rows:
        if r['rec'] and not MAP.exists(r['rec']):
            errs.append('%s 记录不存在: %s' % (r['slot'], r['rec']))
        for k, nm in (('prefix', '前缀'), ('suffix', '后缀'), ('component', '镶嵌'),
                      ('augment', '附魔')):
            v = r.get(k)
            if v and not MAP.exists(v):
                errs.append('%s %s 不存在: %s' % (r['slot'], nm, v))
        if r['why'].startswith('⚠') and not r['raw'].get('record'):
            errs.append('%s 映射低置信（%s）—— 请在方案里用 record 字段显式指定'
                        % (r['slot'], r['why'][:60]))
    return errs


# ------------------------------------------------------------------ 构造物品
def _picksrc():
    """从全部存档采集「组件/附魔记录 → 真实 seed」"""
    import glob
    save, _ = ENV.find_save_dir()
    paths = glob.glob(os.path.join(save or '', 'main', '*', 'player.gdc'))
    try:
        from .save import backup as _B
        for _sub, _leaf in (('Steam云存档_%s' % _PATHS.STEAM_APPID,
                             ('remote', 'save', 'main', '*', 'player.gdc')),
                            ('我的文档_MyGames_GrimDawn',
                             ('save', 'main', '*', 'player.gdc'))):
            paths += glob.glob(os.path.join(_B.ARCHIVES, '*', _sub, *_leaf))
    except Exception:
        pass
    src = {}
    for p in paths:
        try:
            d = S.parse(p)
        except Exception:
            continue
        b3 = d['block_map'].get(3)
        if not b3:
            continue
        items = [i for s in b3.get('sacks', []) for i in s['items']]
        items += b3.get('equipment', []) + b3.get('alt1', []) + b3.get('alt2', [])
        for it in items:
            base = it.get('basename') or ''
            if '/materia/' in base and it.get('seed') not in PLACEHOLDER:
                src.setdefault(base, ('背包组件物品', it['seed']))
            rel = it.get('relic_name') or ''
            if rel and it.get('relic_seed') not in PLACEHOLDER:
                src.setdefault(rel, ('已镶嵌实例', it['relic_seed']))
    return src


_USED = set()


def pick_seed(rec, src, old=0, salt=''):
    if old and SEED_LO <= old <= SEED_HI:
        return old, '沿用原值'
    if rec in src and rec not in _USED:
        _USED.add(rec)
        return src[rec][1], src[rec][0]
    r = random.Random('gd-seed:%s@%s' % (rec, salt))
    return r.randint(SEED_LO, SEED_HI), '按实测分布生成'


def mk_item(r, src):
    old = r['old'] or {}
    seed, w1 = pick_seed(r['rec'], src, old.get('seed', 0), r['slot'] + '/main')
    if r['component']:
        rseed, w2 = pick_seed(r['component'], src, 0, r['slot'] + '/comp')
    else:
        rseed, w2 = 0, '无镶嵌'
    if r.get('augment'):
        # ⚠ augment_seed 也必须落在合法区间（写 0/1 → 附魔不生效，见 SKILL §6.1）
        aseed, w3 = pick_seed(r['augment'], src, 0, r['slot'] + '/aug')
    else:
        aseed, w3 = 0, '无附魔'
    return {
        'basename': r['rec'], 'prefix': r['prefix'], 'suffix': r['suffix'],
        'modifier': '', 'transmute': '', 'seed': seed,
        'relic_name': r['component'], 'relic_bonus': '', 'relic_seed': rseed,
        'augment_name': r.get('augment') or '', 'unk': 0, 'augment_seed': aseed,
        'f_relic_comp': 0, 'f_a': 0, 'f_b': 0, 'qty': 1,
        'f_c': 0, 'f_d': 0, 'attached': True,
    }, (w1, w2, w3)


def _is_two_hand(rec: str) -> bool:
    """该武器记录是不是**双手**（游戏里双手武器 + 副手 不能同时生效）。"""
    try:
        from .save import items as _SI
        g = _SI.bridge().gid_of(rec)
        o = (g and _SI.bridge().item(rec)) or {}
        return '2h' in (o.get('n') or '').lower()
    except Exception:
        return False


def _auto_fit_attrs(char, save, eq, alt1, alt2, opts, image, b2):
    """★ 按**将要写入的新装备**自动拟合三围，并就地打进 image。

    返回 (rf, err) —— `err` 非空表示应当中止写盘。
    """
    from . import reqfit

    # 1) 喂给 load_char 的 override（12 槽 + 武器；record 清单 = 主体/镶嵌/附魔/前缀/后缀）
    ov = {}
    for i, it in enumerate(eq):
        parts = [it.get(k) for k in ('basename', 'relic_name', 'augment_name',
                                     'prefix', 'suffix') if it.get(k)]
        if parts:
            ov[SLOTS[i]] = parts
    wsets = []          # [(槽位, 部件字典)] —— 给需求行用（前缀/后缀是显式字段）
    for j, it in enumerate(alt1 or []):
        slot = ('主手', '副手')[j] if j < 2 else '主手'
        if it.get('basename'):
            wsets.append((slot, it))
    # 双手主手 → 副手不生效，整槽丢掉（与 tools/plan_dps.plan_to_override 同规则）
    if wsets and _is_two_hand(wsets[0][1]['basename']):
        wsets = [x for x in wsets if x[0] != '副手']
    for slot, it in wsets:
        parts = [it.get(k) for k in ('basename', 'relic_name', 'augment_name',
                                     'prefix', 'suffix') if it.get(k)]
        if parts:
            ov[slot] = parts

    # 2) 需求行：前缀/后缀是**显式字段**，比从 override 反推更准
    rows = reqfit.gear_from_items(eq, wsets)

    rf = reqfit.solve(char, override=ov, rows=rows, save_dir=save,
                      buffer=opts.get('buffer', 1),
                      att_safety=opts.get('att_safety', 6),
                      prefer=opts.get('prefer'),
                      spread=bool(opts.get('spread')))
    print()
    print('=' * 104)
    print('★ 按新装备自动拟合属性点')
    print(reqfit.describe(rf, '   '))
    if not rf.feasible and not opts.get('allow_short'):
        return rf, ('属性点预算不够，新装备穿不上 —— 拒绝写盘。'
                    '（还差：%s；要硬写加 --fit-allow-short）'
                    % ', '.join('%s %d 点' % (reqfit.ZH[k], v)
                                for k, v in rf.short.items()))
    if not rf.changed and not opts.get('force'):
        print('   当前三围已满足，属性不变')
        return rf, None

    # 3) 就地打进 block2（定长字段，与块 3 的变长重写互不干扰）
    import struct as _st
    for f in ('physique', 'cunning', 'spirit'):
        off = b2['_off'][f]
        image[off:off + 4] = _st.pack('<I', _st.unpack('<I', _st.pack('<f', float(rf.targets[f])))[0])
    if int(b2.get('attribute_points') or 0):
        off = b2['_off']['attribute_points']
        image[off:off + 4] = _st.pack('<I', 0)
    print('   写入 physique=%.0f cunning=%.0f spirit=%.0f attribute_points=0'
          % (rf.targets['physique'], rf.targets['cunning'], rf.targets['spirit']))
    return rf, None


# ------------------------------------------------------------------ 写入
def do_apply(spec, rows, opts=None):
    opts = opts or {}
    char = spec.get('char')
    save, _ = ENV.find_save_dir()
    live = os.path.join(save, 'main', char, 'player.gdc')
    if not os.path.exists(live):
        print('✗ 找不到角色存档: %s' % live)
        return 1
    from .save import backup as B
    from .save import patch as E
    if E.game_running() and not opts.get('allow_running'):
        print('✗ 检测到 Grim Dawn.exe 正在运行，拒绝写盘')
        print('  （游戏的下一次存档会覆盖本次写入。确实要写加 --allow-running；'
              '改测试副本也该加）')
        return 2
    if E.game_running():
        print('⚠ --allow-running：游戏正在运行，写入通常不会实时生效。')

    raw = open(live, 'rb').read()
    d = S.parse(live, record=True)
    image = bytearray(d['record']['image'])
    plan = list(d['record']['plan'])
    blocks = W.block_ranges(raw)
    b3 = d['block_map'][3]
    v11 = b3['version'] >= 11
    src = _picksrc()

    eq = [dict(x) for x in b3['equipment']]
    # ★★ 2026-09-20：武器写进**当前启用**的那一套（见 `_active_weapon_set`）。
    #   旧实现恒写 alt1 ⇒ 启用 alt2 的存档「落档后武器不生效」（实测面板差 53%）。
    _wset = _active_weapon_set(b3)
    wset = [dict(x) for x in (b3.get(_wset) or [])]
    print('  [武器套] 写盘目标 %s（%s 在用）' % (_wset, _wset))
    log = []
    for r in rows:
        it, why = mk_item(r, src)
        if r['group'] == 'equip':
            idx = SLOT_IDX.get(r['slot'], -1)
            if idx < 0:
                print('✗ 未知槽位: %s' % r['slot'])
                return 1
            eq[idx] = it
        else:
            # ⚠ SLOTS 只含 12 个装备槽，**不含主手/副手**。
            #   旧代码写 `list(SLOTS).index(r['slot'])` → 两个武器槽都恒得到 0，
            #   于是副手覆盖主手、而真正的副手位（wset[1]）从未被写入（静默漏写）。
            j = WIDX.get(r['slot'], 0)
            if not wset:
                wset = [it]
                while len(wset) <= j:
                    wset.append(dict(it))
            else:
                while len(wset) <= j:
                    wset.append(dict(wset[0]))
                wset[j] = it
        log.append((r, it, why))

    b3blk = [b for b in blocks if b['id'] == 3][0]

    # ★ 换完装备 → 按**新装备的需求**自动调整三围（同一个事务里写进去）
    rf = None
    if opts.get('fit_gear', True):
        b2 = d['block_map'].get(2) or {}
        rf, err = _auto_fit_attrs(char, save, eq, wset, b3.get('alt2'), opts,
                                  image, b2)
        if err:
            print('\n✗ %s' % err)
            return 1

    # 另一套武器原样保留（写盘时按位置放回）
    _a1 = wset if _wset == 'alt1' else [dict(x) for x in (b3.get('alt1') or [])]
    _a2 = wset if _wset == 'alt2' else [dict(x) for x in (b3.get('alt2') or [])]
    new_segs = W.w_equip_region(b3['use_alt_weaponset'], eq, b3['alt1_unused'], _a1,
                                b3['alt2_unused'], _a2, v11)
    segs = W.rebuild(image, plan, blocks,
                     {3: (b3['_off_equip'], b3blk['payload'] + b3blk['plen'], new_segs)})
    img, plan2 = W.flatten(segs)
    out = S.encode(img, plan2, d['seed'])

    tmp = live + '.new'
    open(tmp, 'wb').write(out)
    v = S.parse(tmp, record=True)

    errs = []
    if not v['all_blocks_ok']:
        errs.append('块校验失败: %s' % [b['id'] for b in v['blocks'] if not b['ok']])
    if S.encode(v['record']['image'], v['record']['plan'], v['seed']) != out:
        errs.append('往返不一致')
    if v['level'] != d['level']:
        errs.append('等级变化 %s -> %s' % (d['level'], v['level']))

    # ★ 复核：三围是否按拟合目标写进去了 + 装备可穿性
    if rf is not None:
        vb2 = v['block_map'].get(2) or {}
        for f in ('physique', 'cunning', 'spirit'):
            got = float(vb2.get(f) or 0.0)
            want = float(rf.targets[f])
            if abs(got - want) > 1e-3:
                errs.append('%s 写入不符 %.1f -> %.1f' % (f, want, got))
        if int(vb2.get('attribute_points') or 0) != 0 and rf.free:
            errs.append('attribute_points 未清零（还是 %s）'
                        % vb2.get('attribute_points'))
        pv = {k: rf.A[k] + rf.B[k] * ((float(vb2.get(k) or 0.0) - 50.0) / 8.0)
              for k in ('physique', 'cunning', 'spirit')}
        _bad = [k for k in ('physique', 'cunning', 'spirit')
                if rf.need[k] and pv[k] + 1e-6 < rf.need[k]]
        print('   复检 装备可穿性 体格 %.0f/%d ｜ 狡诈 %.0f/%d ｜ 精神 %.0f/%d  %s'
              % (pv['physique'], rf.need['physique'], pv['cunning'],
                 rf.need['cunning'], pv['spirit'], rf.need['spirit'],
                 '✓ 全过' if not _bad else '✗ 仍缺 %s' % _bad))
        if _bad:
            errs.append('新装备仍穿不上：%s' % _bad)
        if rf.unevaluated:
            print('   ⚠ %d 件装备需求未评估（认不出类型）' % len(rf.unevaluated))
    ve = v['block_map'][3]['equipment']
    # ★ 校验也要看**写盘目标那一套**（`_active_weapon_set`），否则会把「写对但不在 alt1」
    #   误报成「未写入」。
    va = v['block_map'][3].get(_wset) or []
    for r in rows:
        if r['group'] == 'equip':
            i = SLOT_IDX[r['slot']]
            if ve[i]['basename'] != r['rec']:
                errs.append('%s 记录写入不符' % r['slot'])
            if (ve[i]['relic_name'] or '') != r['component']:
                errs.append('%s 镶嵌写入不符' % r['slot'])
            if (ve[i].get('augment_name') or '') != (r.get('augment') or ''):
                errs.append('%s 附魔写入不符' % r['slot'])
        else:
            # 武器槽必须逐项校验（历史上这里漏检过一次「副手没写入」）
            j = WIDX.get(r['slot'], 0)
            if j >= len(va):
                errs.append('%s 未写入（武器组只有 %d 项）' % (r['slot'], len(va)))
                continue
            if va[j]['basename'] != r['rec']:
                errs.append('%s 记录写入不符' % r['slot'])
            if (va[j]['relic_name'] or '') != r['component']:
                errs.append('%s 镶嵌写入不符' % r['slot'])
            if (va[j].get('augment_name') or '') != (r.get('augment') or ''):
                errs.append('%s 附魔写入不符' % r['slot'])
        if r.get('augment'):
            if r['group'] == 'equip':
                as_ = ve[i]['augment_seed']
            else:
                _j2 = WIDX.get(r['slot'], 0)
                as_ = va[_j2]['augment_seed'] if _j2 < len(va) else 0
            if not (SEED_LO <= as_ <= SEED_HI):
                errs.append('%s augment_seed 越界' % r['slot'])
        if r['component']:
            # 武器项要走 va[j]，不能用装备的 i（旧代码在这里误读、并对武器漏校验）
            if r['group'] == 'equip':
                rs = ve[i]['relic_seed']
            else:
                _j = WIDX.get(r['slot'], 0)
                rs = va[_j]['relic_seed'] if _j < len(va) else 0
            if not (SEED_LO <= rs <= SEED_HI):
                errs.append('%s relic_seed 越界' % r['slot'])

    print()
    print('=' * 104)
    if errs:
        print('✗ 复检失败:')
        for e in errs:
            print('    -', e)
        try:
            os.remove(tmp)
        except OSError:
            pass
        return 1
    print('✓ 复检通过：块校验全过 · 往返逐字节一致 · 字段逐项吻合 · seed 全合法')
    print('   文件 %d -> %d 字节' % (len(raw), len(out)))

    # ★ 只在**正式存档目录**上做备份 —— 改测试副本（GD_SAVE / 临时目录）时
    #   备份会把副本树也塞进 E:\xz\Archives，制造一堆看不懂的垃圾备份。
    #   （与 gd/save/patch.py 的行为一致）
    _env_backup = os.environ.pop('GD_SAVE', None)
    try:
        _true_save, _ = ENV.find_save_dir()
    finally:
        if _env_backup is not None:
            os.environ['GD_SAVE'] = _env_backup
    _is_live = (bool(_true_save)
                and os.path.normcase(str(_true_save)) == os.path.normcase(str(save)))
    if _is_live:
        bk = B.do_backup(note='%s 写入前' % spec.get('note', '方案'))
    else:
        bk = None
        print('\n（正在改非正式存档目录 %s —— 跳过备份）' % save)
    shutil.copyfile(tmp, live)
    os.remove(tmp)
    print('\n已写入 %s' % live)
    if bk:
        print('备份在 %s' % bk)
        print('回滚：python gd_backup.py restore "%s" --yes' % bk)
    return 0


# ------------------------------------------------------------------ 导出草稿
def do_new(char):
    save, _ = ENV.find_save_dir()
    p = os.path.join(save, 'main', char, 'player.gdc')
    if not os.path.exists(p):
        print('✗ 找不到 %s' % p)
        return 1
    d = S.parse(p)
    b3 = d['block_map'][3]
    spec = {'char': char, 'note': '%s 装备草稿（从当前存档导出）' % char,
            'equipment': [], 'weapons': []}
    for i, it in enumerate(b3['equipment']):
        row = {'slot': SLOTS[i], 'item': '', 'note_current': (it['basename'] or '').split('/')[-1]}
        if it['prefix']:
            row['prefix'] = it['prefix']
        if it['suffix']:
            row['suffix'] = it['suffix']
        if it['relic_name']:
            row['component'] = ''
            row['note_current_component'] = it['relic_name'].split('/')[-1]
        spec['equipment'].append(row)
    # ★ 草稿要反映**实际在用**的那套武器（否则导出的是另一套，白白改错）
    for j, it in enumerate(b3.get(_active_weapon_set(b3)) or []):
        spec['weapons'].append({'slot': '主手' if j == 0 else '副手', 'item': '',
                                'note_current': (it['basename'] or '').split('/')[-1]})
    out = os.path.join(HERE, 'plans', '%s_draft.json' % char.lstrip('_'))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    json.dump(spec, open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('已导出草稿: %s' % out)
    print('把 item / component 填成 GT id（itXXXX）后即可 plan / apply')
    return 0


def do_check(spec, rows):
    """★ 回归比对：方案期望 vs 当前存档实际 —— 证明方案文件能精确描述存档状态"""
    char = spec.get('char')
    save, _ = ENV.find_save_dir()
    live = os.path.join(save, 'main', char, 'player.gdc')
    d = S.parse(live)
    b3 = d['block_map'][3]
    print()
    print('=' * 104)
    print('方案 ↔ 存档 比对（%s）' % char)
    print('=' * 104)
    diff = 0
    for r in rows:
        if r['group'] == 'equip':
            i = SLOT_IDX[r['slot']]
            it = b3['equipment'][i]
        else:
            j = 0 if r['slot'] == '主手' else 1
            # ★ 比**启用**的那一套（与写盘目标一致；见 `_active_weapon_set`）
            _ws = b3.get(_active_weapon_set(b3)) or []
            it = _ws[j] if j < len(_ws) else {}
        checks = [
            ('记录', (it.get('basename') or ''), r['rec']),
            ('前缀', (it.get('prefix') or ''), r['prefix']),
            ('后缀', (it.get('suffix') or ''), r['suffix']),
            ('镶嵌', (it.get('relic_name') or ''), r['component']),
            ('附魔', (it.get('augment_name') or ''), r.get('augment') or ''),
        ]
        bad = [(nm, a, b) for nm, a, b in checks if (a or '') != (b or '')]
        if bad:
            diff += 1
            print('  ✗ %-6s 不一致:' % r['slot'])
            for nm, a, b in bad:
                print('        %s: 存档=%s  方案=%s' % (nm, a.split('/')[-1] or '(空)',
                                                       b.split('/')[-1] or '(空)'))
        else:
            print('  ✓ %-6s %s' % (r['slot'], (it.get('basename') or '').split('/')[-1]))
    print()
    print('  结论：%s' % ('方案与存档完全一致 ★' if diff == 0 else '有 %d 处不一致' % diff))
    return 0 if diff == 0 else 1


def main():
    ap = argparse.ArgumentParser(description='恐怖黎明 改档方案构建器')
    ap.add_argument('cmd', choices=['new', 'plan', 'apply', 'check'])
    ap.add_argument('target', help='new 时为角色名；plan/apply 时为方案 json 路径')
    ap.add_argument('--out', help='new 时指定输出路径')
    # ★ 落档时按新装备的需求自动调三围（默认开）
    ap.add_argument('--no-fit-gear', dest='fit_gear', action='store_false',
                    default=True, help='不自动调三围（默认会按新装备的需求调）')
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
    ap.add_argument('--allow-running', action='store_true', dest='allow_running',
                    help='危险：游戏运行中也写入（只用于「实时生效」实验或改测试副本）。'
                         '游戏的下一次存档会覆盖本次写入')
    a = ap.parse_args()
    opts = {'fit_gear': a.fit_gear, 'buffer': a.fit_buffer,
            'att_safety': a.att_safety, 'prefer': a.fit_prefer,
            'allow_short': a.fit_allow_short, 'allow_running': a.allow_running,
            'spread': a.fit_spread}

    if a.cmd == 'new':
        return do_new(a.target)

    if not os.path.exists(a.target):
        print('✗ 方案文件不存在: %s' % a.target)
        return 1
    spec = json.load(open(a.target, encoding='utf-8'))
    char = spec.get('char')
    save, _ = ENV.find_save_dir()
    cp = os.path.join(save, 'main', char, 'player.gdc') if save else None
    char_d = S.parse(cp) if cp and os.path.exists(cp) else None

    rows = build_plan(spec, char_d)
    bad = print_plan(spec, rows)
    errs = verify_rows(rows)
    if errs:
        print()
        print('  静态校验发现问题 %d 项：' % len(errs))
        for e in errs:
            print('     ✗ %s' % e)
    if bad or errs:
        print()
        print('  → 请先修正方案（把 GT id 换成能解析出高置信记录名的条目）')
        return 1

    if a.cmd in ('plan', 'check'):
        print()
        print('  ✓ 全部记录解析成功且存在于游戏库（预演模式，未写盘）')
        if a.cmd == 'plan':
            return 0
        return do_check(spec, rows) + 0
    return do_apply(spec, rows, opts)


if __name__ == '__main__':
    sys.exit(main())
