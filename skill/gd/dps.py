# -*- coding: utf-8 -*-
r"""★ 存档实测 DPS 校验（gd_dps_check）—— 中文白话版

拿**真实角色**（存档里的实际配装 + 实际加点）复算「最终伤害」，
对着游戏面板逐项对账。输出**全中文**（技能名查 `skills.json` 的中文名），
并附一段「怎么看这些数字」的白话讲解 + 一个逐步手算的例子。

```bash
python gd_dps_check.py Sam                  # 默认带逐技能明细
python gd_dps_check.py Sam --brief          # 只看汇总
python gd_dps_check.py Sam --arch werewolf  # 指定流派
python gd_dps_check.py Sam --aps 1.30       # 覆盖基础攻速假设
# → 落盘 plans/<角色>_dps_check.md
```

⚠ 已知偏差（对账时注意）：
  1. **词缀/紫装的随机 roll**：存档只存 roll 种子，这里读**记录默认值**
     ⇒ 与游戏内面板可能差几个百分点；看**量级与占比**比看绝对值可靠
  2. **星座只算到「记录本身带加成」的星节点**，经 `_buff`/二次计算派生的部分没算
  3. 常驻开关（血源苏醒/阿玛托克契约）已计入；**限时 buff 与减抗类不计**
     （集结战吼 = 团队 buff；刺骨战吼 = 削抗降 DA 的 debuff，本身无伤害）
  4. 不含怪物抗性/护甲、暴击期望、命中率；基础攻速是**假设值**（可用 --aps 覆盖）
"""
import argparse
import io
import json
import os
import sys

from . import paths as _PATHS

# 旧脚本用 HERE 拼数据文件路径；新架构下数据在技能的 data/ 里
HERE = str(_PATHS.DATA_DIR)
_CACHE_ROOT = str(_PATHS.CACHE_DIR)
_PLANS = str(_PATHS.CACHE_DIR / 'plans')



SLOTS = ['头部', '项链', '胸甲', '腿甲', '靴子', '手套', '戒指1', '戒指2',
         '腰带', '肩甲', '勋章', '圣物']
PARTS = ('basename', 'prefix', 'suffix', 'relic_name', 'augment_name')
FORM_ARCH = {'werewolf1': 'werewolf', 'wereraven1': 'wereraven'}
FORM_ZH = {'werewolf': '狼人形态', 'wereraven': '鸦人形态', 'human': '人形态（不变身）'}
# 记录名不友好时的兜底中文名（`skills.json` 里查不到的老记录）
EXTRA_ZH = {'defaultweaponattack': '默认武器攻击', 'defaultkickattack': '默认踢击',
            'defaultwpbasicattack': '默认武器普攻', 'defaultmoveto': '移动',
            'defaultpetattack': '宠物攻击', 'defaulthealthpotion': '生命药水',
            'defaultmanapotion': '魔力药水', 'defaultevade': '闪避',
            'healthpotion_healovertime': '药水持续治疗'}
# 常见「没有伤害数字」的技能是干什么的
NOTE_ZH = {
    'rallyingcry1': '团队增益（+OA / 移速）',
    'bonechillingcry1': '★ 削敌人 30% 穿刺抗性 + 降敌 DA（**本身无伤害**）',
    'werewolf1': '形态本体（解锁授予技能）',
    'werewolf3': '削敌 DA + 暴击刷新冲锋冷却',
    'passive04': '野兽形态：伤害倍率 + 攻速上限',
    'amatokpact2': '阿玛托克契约的强化（加成已计入第三节）',
    'amatokpact1': '常驻开关',
    'passive03': '被动',
    'passive02': '暴击触发的被动',
}


def load_skills_zh():
    """技能记录 -> 中文名。走 `paths.load_json`（进程内 memo + pickle 缓存）。

    ★ 旧实现每次评估都 `json.load(skills.json)`（11 MB）→ 单次 DPS 评估白花 ~0.1 s；
      局部搜索要评估上千次，这个洞不堵就快不起来。
    """
    from . import paths as _P
    return _P.load_json('skills.json') or {}


def dedup(pairs):
    """同一份清单里中文名重复时，补上记录名以便区分（星座常同名）"""
    from collections import Counter
    cnt = Counter(n for _r, n in pairs)
    out = []
    for r, n in pairs:
        if cnt[n] > 1:
            n = '%s（%s）' % (n, os.path.basename(r).replace('.dbr', ''))
        out.append((r, n))
    return out


def zh_name(rec, skills):
    """记录名 → 中文名（查不到就退回兜底表，再退回记录名）"""
    d = skills.get(rec) or {}
    base = os.path.basename(rec).replace('.dbr', '')
    if base.startswith('_classtraining'):          # 精通条优先（职业名容易撞）
        return '%s精通条' % (d.get('name') or base.replace('_classtraining_', ''))
    if d.get('name'):
        return d['name']
    if base in EXTRA_ZH:
        return EXTRA_ZH[base]
    if base.startswith('tier') or 'devotion' in rec.lower():
        return '星座·%s' % base
    return base


_TAG2REC = None


def _tag2rec_map():
    """技能 tag → 记录名（进程内只建一次）。

    ★ 为什么必须缓存：旧实现**每次 `load_char` 都 `json.load(skills.json)`（11 MB）** ——
      实测这是单次 DPS 评估 0.15 s 里的 0.09 s（60%）。局部搜索要评估上千次，
      不堵这个洞，「极限模式」就是几十分钟起步。
    """
    global _TAG2REC
    if _TAG2REC is None:
        from . import paths as _P
        _all = _P.load_json('skills.json') or {}
        out = {}
        for _rec, _d in _all.items():
            _t = ((_d or {}).get('tag') or '').strip()
            if _t and (_t not in out or ('/playerclass' in _rec
                                         and '/playerclass' not in out[_t])):
                out[_t] = _rec
        _TAG2REC = out
    return _TAG2REC


_PARSE_MEMO = {}

# ★ `load_char` 里 `fold(parts, no_scale)` 的槽级缓存（2026-09-20）。
#   键 = `(部件元组, no_scale)` —— 同一槽的同一组部件永远是同一个折合结果。
#   LNS/local_search 每轮只放开少数槽，其余槽**逐轮重复折叠** ⇒ 命中率极高。
_FOLD_MEMO: dict = {}
_FOLD_MEMO_MAX = 60000


def _parse_cached(path):
    """★ 只读解析缓存。

    `load_char` 每次评估都会 `S.parse(player.gdc)` —— 实测占单次 DPS 评估的 **24 %**
    （0.0053 s / 0.0225 s）。一次搜索里存档不会变，故按 `(路径, mtime, 大小)` 缓存。
    ★ 带参调用（`record=True` 等**写盘**用途）不走缓存，行为与原来完全一致。
    ★ `load_char` 只读解析结果（不写回 `d`），共享安全。
    """
    from .save import core as _S
    try:
        key = (str(path), os.path.getmtime(path), os.path.getsize(path))
    except OSError:
        return _S.parse(path)
    v = _PARSE_MEMO.get(key)
    if v is None:
        v = _PARSE_MEMO[key] = _S.parse(path)
    return v


def load_char(name, weapon_set='', local=True, gear_override=None,
              skill_override=None, bio_override=None, level_override=None,
              classes_override=None):
    """解析角色存档 → 实际加点 / 实际配装（字段已逐条求和折叠）

    `gear_override`（2026-09-18 新增）：`{槽位: [记录名…]}`，**替换 12 个装备槽**
    （武器不动）。用于「换一套装备后 DPS 会变成多少」的**同模型 what-if** ——
    下游的 skill_plus / mastery_plus / 套装 / 转化 / skill_mods / folded / gear_flat
    全部按新部件重算，因此结果与「真落档后跑一次」同口径。
    ⚠ 给了 override 时，`gt_data/local_items.json`（游戏提示框真值，绑的是**旧装备**）
      只对武器生效，不会污染被替换的 12 槽。

    `skill_override` / `bio_override` 等（2026-09-19 新增，为 `tools/gt_regress.py`）：
    让本函数能评估**一份与存档无关的构建**（如 grimtools 上的社区配装）：
      · `skill_override`  `{记录名: 等级}` —— **整体替换**存档加点（含 `_classtraining_`）
      · `bio_override`    `{'physique'|'cunning'|'spirit': 值}` —— 替换基础属性
      · `level_override` / `classes_override` —— 替换等级 / 职业名
      · `name=''`（假值）→ **完全不用存档**，一切以 override 为准
    ⚠ `skill_override` 给的是**总等级**（已含装备加成），故 `skill_plus` 不再叠加；
      实现上仍会在 `plan_dps` 层叠加，所以调用方要按需处理（`gt_regress` 用的是
      `gt_build` 解析出的**含装备后的**等级，故传 `skill_bonus_already=True` 语义
      由调用方自行扣减 —— 见 `tools/gt_regress.py` 的 `_dedupe_skill_bonus`）。
    """
    # ★★ `GD_SKILL_JSON`：把「按形态算出来的加点」注入**所有**调用方。
    #   为什么需要：`skill_override` 只是个**函数参数**，而实际跑伤害的链路
    #   （`gd/opt.py` 的局部搜索 → `plan_dps` → `load_char`）拿不到它 ——
    #   于是「换形态 = 换一套加点」这件事**走不通**：优化器只会按存档里的旧加点
    #   评估，把一个新形态的主输出技能整个漏掉（实测：`avalanche` 形态下若不注入，
    #   雪崩根本不在循环里，局部搜索就是在给别人的 BD 挑装备）。
    #   值 = `tools/make_alloc.py` 产出的 `{记录名: 等级}` JSON 路径。
    if skill_override is None:
        _ovp = os.environ.get('GD_SKILL_JSON')
        if _ovp and os.path.isfile(_ovp):
            try:
                with io.open(_ovp, encoding='utf-8') as _fh:
                    _ov = json.load(_fh)
                if isinstance(_ov, dict) and _ov:
                    skill_override = _ov
            except Exception as _e:
                print('⚠ GD_SKILL_JSON 读取失败：%s（已忽略）' % _e)
    from . import _legacyenv as E
    from .save import core as S
    from . import dbr as DB
    if name:
        sd, _ = E.find_save_dir()
        key = name if name.startswith('_') else '_' + name
        p = os.path.join(sd, 'main', key, 'player.gdc')
        if not os.path.isfile(p):
            raise SystemExit('✗ 找不到存档：%s' % p)
        d = _parse_cached(p)
    else:
        # ★ 无存档模式：造一个空壳，全部靠 override
        p = ''
        d = {'level': int(level_override or 100), 'classes': list(classes_override or []),
             'block_map': {2: dict(bio_override or {}), 3: {}, 8: {}}}
    db = DB.open_all()

    skills = {s['skill']: int(s['level'])
              for s in (d['block_map'].get(8) or {}).get('skills', []) if s['level'] > 0}
    if skill_override is not None:
        # ★★ 星座必须**保留存档值**（2026-09-20 修）：
        #   形态加点 JSON（`GD_SKILL_JSON`）只描述**技能 / 精通条**，不含星座。
        #   旧实现是整体替换 `skills` ⇒ 存档的星座被**静默清空**：
        #   实测同一角色 `Sam` 设了 GD_SKILL_JSON 后技能条目 63 → 12、
        #   星座 32 点 → 0。后果是整条搜索链（autobuild / tune_dps / plan_dps）
        #   都在「星座全空」的口径下评估 —— 装备方案与真实存档不匹配，
        #   且报告 §六 会谎报「已点 0 / 55」。
        #   星座是**可洗**的独立维度，不属于「形态加点」，因此这里保留存档值。
        _dev = {r: int(lv) for r, lv in skills.items()
                if '/devotion/' in r and lv}
        # ★★ 2026-09-21：注入文件里**不该有星座** —— 带进来是**求并集**、不是替换
        #   （下面的 `update(_dev)` 只覆盖**同名**键），所以注入文件里一件存档没有的
        #   星座会被**静默叠加**在存档星座上。实测：`alloc_opt.json` 带了 4 星「乌龟」
        #   ⇒ 报告 DA **1,406 → 1,470**，而乌龟是纯防御、合计 DPS 一位不变 ⇒
        #   **这个错不会在任何 DPS 数字上暴露**，只有 OA/DA 行会骗人。
        #   这里只发警告、**不动数值**（改语义会让所有历史批次漂移）。
        _stray = sorted(r for r, lv in (skill_override or {}).items()
                        if lv and '/devotion/' in r and r not in _dev)
        if _stray:
            print('⚠ GD_SKILL_JSON 含 %d 个「存档没有的星座」记录 ⇒ 会**叠加**在存档星座上'
                  '（不是替换）：%s%s'
                  % (len(_stray),
                     '、'.join(os.path.basename(x).replace('.dbr', '') for x in _stray[:6]),
                     ' …' if len(_stray) > 6 else ''))
        skills = {r: int(lv) for r, lv in skill_override.items() if lv}
        skills.update(_dev)
    if local and skill_override is not None:
        local = False          # 外来构建没有「游戏内提示框真值」，别串味
    if local and not name:
        local = False
    # 无存档时武器套选择无意义，且 `b3` 是空壳 —— 直接跳过整套武器逻辑
    _no_save = not name

    # ★ 哪些槽已经有「游戏内提示框真值」（`gt_data/local_items.json`）——
    #   那些槽**不做 dbr 缩放**，否则会和提示框真值重复计算。
    _local_slots = set()
    if local:
        try:
            import gd_local_tip as _LT
            _local_slots = set(_LT.load_local() or {})
        except Exception:
            _local_slots = set()

    def fold(parts, no_scale=False):
        """折合一个槽的部件（**求和**），并做 `attributeScalePercent` 缩放。

        ★★ 缩放公式（2026-09-17 逆向 GT 实证，见 SKILL §67）：
            `offensive*` **伤害属性** × `(1 + attributeScalePercent/100)`
        而 `character*` / `defensive*` / `conversionPercentage` /
        `skillCooldownReduction` / `offensive*Ratio*` **不缩放**。实测：
            c204_sword2h（h=60）`offensivePierceMin` 27 → 43.2
                ⇒ 提示框 `38 穿刺伤害 [35-51]`（43.2×[0.8,1.2]）✓
            c204_sword2h `characterLife` 212 ⇒ 提示框 `+180 生命 [170-254]`
                （212×[0.8,1.2]，**没乘 1.6**）✓

        缩放系数取**物品主体**的 `attributeScalePercent`（ARZ 记录里有）；
        **词缀（路径含 `/lootaffixes/`）没有它，借用物品的**；
        **镶嵌 / 附魔不缩放** —— 实测「恶毒尖刺」提示框显示
        `4 穿刺伤害 / +18% 穿刺伤害`，就是记录里的原值。

        `no_scale=True`：该槽已有权威真值（游戏提示框），不要叠。

        ★★ 槽级缓存（2026-09-20）：`fold` 是**纯函数**（同槽同部件 ⇒ 同结果），
          而搜索器每轮只放开 k 个槽、其余 14−k 个槽**逐轮重复折叠** ⇒ 命中率极高。
          返回**浅拷贝**（外层 dict 新建），调用方可随意改而不污染缓存。
        """
        _key = (tuple(parts), bool(no_scale))
        _c = _FOLD_MEMO.get(_key)
        if _c is not None:
            return dict(_c)
        _f = _fold_raw(parts, no_scale)
        if len(_FOLD_MEMO) < _FOLD_MEMO_MAX:
            _FOLD_MEMO[_key] = _f
        return dict(_f)

    def _fold_raw(parts, no_scale=False):
        """`fold` 的实算体（无缓存）。见 `fold` 的 docstring。"""
        from . import rotation as _R
        h_main = 0.0
        for rec in parts:
            if not rec or '/lootaffixes/' in rec:
                continue
            _v = (db.fields(rec) or {}).get('attributeScalePercent') or [0]
            if _v and _v[0]:
                h_main = float(_v[0])
                break
        f = {}
        for rec in parts:
            if not rec:
                continue
            fld = db.fields(rec) or {}
            if no_scale:
                use_h = 0.0
            elif '/lootaffixes/' in rec:       # 词缀借用物品的 h
                use_h = h_main
            else:                              # 物品主体用自己的；镶嵌/附魔没有 → 0
                _v = fld.get('attributeScalePercent') or [0]
                use_h = float(_v[0]) if (_v and _v[0]) else 0.0
            flat = {}
            for k, v in fld.items():
                if isinstance(v, (int, float)):
                    val = float(v)
                elif isinstance(v, list) and v and isinstance(v[0], (int, float)):
                    val = float(v[0])
                else:
                    continue
                flat[k] = flat.get(k, 0.0) + val
            if use_h:
                # ★ 用 GT 官方公式（`gd_scale.value` = `Af()` = 区间中点），取整与 GT 一致
                try:
                    import gd_scale as _GS
                    for k in flat:
                        if flat[k] and _R.needs_scale(k, flat):
                            flat[k] = _GS.value(flat[k], use_h)
                except Exception:
                    for k in flat:
                        if _R.needs_scale(k, flat):
                            flat[k] *= (1.0 + use_h / 100.0)
            for k, v in flat.items():
                f[k] = f.get(k, 0.0) + v
        return f

    b3 = d['block_map'].get(3) or {}
    if gear_override is None and _no_save:
        raise SystemExit('✗ 无存档模式必须给 gear_override（否则没有装备可算）')
    folded, all_parts = {}, []
    if gear_override is not None:
        # ★ 12 槽**整槽替换**：只取 override 给的部件，不再读存档里那 12 件。
        for _slot in SLOTS:
            parts = [x for x in (gear_override.get(_slot) or []) if x]
            all_parts += parts
            folded[_slot] = fold(parts, no_scale=False)
    else:
        for i, it in enumerate(b3.get('equipment') or []):
            parts = [it.get(k) for k in PARTS]
            all_parts += [x for x in parts if x]
            _nm = SLOTS[i] if i < len(SLOTS) else '槽%d' % i
            folded[_nm] = fold(parts, no_scale=(_nm in _local_slots))
    wpn_gids = []
    # ★★ 武器套选择：权威标志是 `alt1_unused` / `alt2_unused`（哪个 True 就是哪套没用）。
    #    实测（2026-09-17 23:5x，存档 + 游戏提示框**双向验证**）：
    #      `use_alt_weaponset=False` ｜ `alt1_unused=False` ｜ `alt2_unused=True`
    #      `alt1[0] = c204_sword2h`（attached=True，带 relic `compa_viciousspikes` 恶毒尖刺）
    #      `alt2[*] = a09_sword001`（单手剑 ×2，带 prefix/suffix）
    #    ⇒ **生效的是 alt1** —— 与游戏提示框「170-228 点物理伤害 / 每秒攻击 1.54 次 / 恶毒尖刺」
    #      **完全一致** ✓
    #    ⚠ 旧代码写反了（`alt1 if use_alt_weaponset else alt2`），会选 alt2 的双持单手剑
    #      ⇒ 「武器基础伤害」整块错（102~168 vs 370~428），是主输出偏低的一大来源。
    #    ⚠ `attached` 也不能单独用（alt1[1] 是空条目 attached=False，alt2 两条也 False）。
    _cands = [t for t in ('alt1', 'alt2')
              if any(it.get('basename') for it in (b3.get(t) or []))]
    if b3.get('alt2_unused') and not b3.get('alt1_unused'):
        _pref = 'alt1'
    elif b3.get('alt1_unused') and not b3.get('alt2_unused'):
        _pref = 'alt2'
    else:
        _pref = 'alt2' if b3.get('use_alt_weaponset') else 'alt1'
    _set_tag = weapon_set or (_pref if _pref in _cands else (_cands[0] if _cands else ''))
    # ★ 自检：打印两套武器，方便人工确认选了哪套（`--weapon-set` 可强制）
    try:
        for _t in (() if os.environ.get('GD_QUIET') else ('alt1', 'alt2')):
            _ws = [it.get('basename') for it in (b3.get(_t) or [])
                   if it.get('basename')]
            if _ws:
                print('  [武器套] %s%s → %s'
                      % (_t, '（选中）' if _t == _set_tag else '',
                         '、'.join(os.path.basename(x) for x in _ws)))
    except Exception:
        pass
    # ★ 武器也被 override（极限模式 `--with-weapon`）：默认只换 12 个装备槽、武器不动，
    #   这里补上武器替换 —— 否则优化器会「选武器但按旧武器算伤害」，结果全是错的。
    _ov_w = None
    if gear_override is not None and (gear_override.get('主手') or gear_override.get('副手')):
        _ov_w = [gear_override.get('主手') or [], gear_override.get('副手') or []]
    if _ov_w is not None:
        _ws_parts = []
        for _j, _parts in enumerate(_ov_w):
            _parts = [x for x in (_parts or []) if x]
            if not _parts:
                continue
            all_parts += _parts
            folded[('主手', '副手')[_j]] = fold(_parts, no_scale=False)
            _ws_parts.append(_parts)
        if _ws_parts:
            wpn_gids = [p[0] for p in _ws_parts]
            wf = db.fields(_ws_parts[0][0]) or {}
            wpn_speed = ((wf.get('characterBaseAttackSpeedTag') or [''])[0],
                         (wf.get('characterBaseAttackSpeed') or [None])[0])
            try:
                from . import skillmod as _SM
                wpn_tpl = _SM.weapon_template(db, _ws_parts[0][0])
            except Exception:
                wpn_tpl = ''
    for tag in ([_set_tag] if _set_tag else []):
        if _ov_w is not None:
            break
        # ★ 不再看 `attached`（实测它与「哪套在用」不一致）：有 basename 就是那把武器
        ws = [it for it in (b3.get(tag) or []) if it.get('basename')]
        if ws:
            wpn_gids = [x.get('basename') for x in ws if x.get('basename')]
            for j, it in enumerate(ws):
                parts = [it.get(k) for k in PARTS]
                all_parts += [x for x in parts if x]
                _wn = ('主手', '副手')[j] if j < 2 else '武器%d' % j
                folded[_wn] = fold(parts, no_scale=(_wn in _local_slots))
            wf = db.fields(ws[0]['basename']) or {}
            wpn_speed = ((wf.get('characterBaseAttackSpeedTag') or [''])[0],
                         (wf.get('characterBaseAttackSpeed') or [None])[0])
            try:
                from . import skillmod as _SM
                wpn_tpl = _SM.weapon_template(db, ws[0]['basename'])
            except Exception:
                wpn_tpl = ''
            break

    # ★ 装备**给技能/精通加等级**（`augmentSkillName1..N` + `augmentSkillLevel1..N`）
    skill_plus, mastery_plus, item_skills = {}, {}, []
    for rec in all_parts:
        f = db.fields(rec) or {}
        for i in range(1, 6):
            n, lv = f.get('augmentSkillName%d' % i), f.get('augmentSkillLevel%d' % i)
            if n and lv and n[0]:
                skill_plus[n[0]] = skill_plus.get(n[0], 0) + int(lv[0])
            n, lv = f.get('augmentMasteryName%d' % i), f.get('augmentMasteryLevel%d' % i)
            if n and lv and n[0]:
                cls = os.path.basename(n[0]).replace('_classtraining_', '')                     .replace('.dbr', '')
                mastery_plus[cls] = mastery_plus.get(cls, 0) + int(lv[0])
        n = f.get('itemSkillName')
        if n and n[0]:
            ctrl = (f.get('itemSkillAutoController') or [''])[0]
            item_skills.append((rec, n[0], ctrl))

    # ★★ 装备授予的**常驻类**技能必须进技能表（2026-09-21 修，陷阱 #71）
    #   组件/装备的「授予技能」分三类：
    #     ① **WPS**（`Skill_WPAttack*`）—— 已由 `item_wps` 入池，不在这里；
    #     ② **常驻类** —— `Skill_BuffSelfToggled`（开关光环）/ `Skill_BuffRadius*` /
    #        `Skill_Passive*` / `Skill_Give*`：**一直是开着的**，属性该全程生效；
    #     ③ **触发类** —— 带 `itemSkillAutoController`（ctXX）或 `Skill_Attack*`：
    #        触发率/指派模型没有 ⇒ **不收**（同 proc 的口径，见陷阱 #69-E）。
    #   旧实现只把 ② 列进报告表格（「七、装备自带的触发技能」）而**从来没折进伤害**
    #   ⇒ 整类漏算。实测：Sam 的 10 条授予技能里有 3 条常驻类，
    #     光「恶毒尖刺」的 `comp_bladeaura_02`（+75% 穿刺 / +10 平穿）单独注入就 **+2.79%**。
    #   `db.fields()` 能解析这些记录（它们**不在** `db.skills` 表里）⇒ 进技能表即可被折进。
    _perm_grants = {}
    if item_skills:
        _META = ('templateName', 'characterBaseAttackSpeedTag', 'skillDisplayName',
                 'skillDownBitmapName', 'skillUpBitmapName', 'skillMaxLevel',
                 'skillUltimateLevel', 'petBonusName', 'id')
        _EXACT = ('Skill_BuffSelfToggled', 'Skill_BuffRadiusToggled', 'Skill_BuffSelf',
                  'Skill_BuffRadius', 'Skill_BuffOther')
        try:
            from . import skillprov as _SPg
            from . import skillmod as _SMg
            _iskg = _SMg.load_item_skills() or {}
            for _src, _skid, _ctrl in item_skills:
                if _ctrl:                                  # 有自动控制器 ⇒ 触发型
                    continue
                _t = str((_iskg.get(_skid) or {}).get('l') or '')
                if not (_t in _EXACT or _t.startswith(('Skill_Passive', 'Skill_Give'))):
                    continue
                # 同一 `sk` 组可能给出多条记录（本体 + `granted` 子技能）⇒
                # **只取数值字段最多的那一条**，否则会重复计算同一份加成。
                _best, _bn = None, -1
                for _r in _SPg.sk_to_records(_skid):
                    _ff = db.fields(_r) or {}
                    _n = sum(1 for _k, _v in _ff.items()
                             if _k not in _META and isinstance(_v, list) and _v
                             and isinstance(_v[0], (int, float)))
                    if _n > _bn:
                        _best, _bn = _r, _n
                if _best is None or _bn <= 0:
                    continue
                _v = (db.fields(_src) or {}).get('itemSkillLevelEq')
                if isinstance(_v, list):
                    _v = _v[0] if _v else 1
                _lv = int(_v or 1)
                _perm_grants[_best] = max(_perm_grants.get(_best, 0), _lv)
        except Exception as _e:                            # noqa: BLE001
            print('  ⚠ 装备常驻技能解析失败：%s' % _e)
    if _perm_grants:
        _add = {r: lv for r, lv in _perm_grants.items() if lv > skills.get(r, 0)}
        if _add:
            skills.update(_add)
            print('  ★ 装备授予的**常驻类**技能 %d 条已计入伤害：%s'
                  % (len(_add), '、'.join(sorted(os.path.basename(r)[:-4] for r in _add))))

    # ★ 伤害转化（`conversionInType/OutType/Percentage`，含 `…Type2` 第 2 条）
    #   ⚠ 旧实现只读**第 1 条** ⇒ 带 `conversionInType2` 的 **122 件**物品
    #     少转一条（官方页面渲染器 `itemdb/db.js` 逐字是 `for(a=1;10>a;a++)`，
    #     最多 9 组；这里对齐同一口径）。顺手去重，避免提示框覆盖时重复叠加。
    convs = []
    for rec in all_parts:
        f = db.fields(rec) or {}
        for _i in range(1, 10):
            _sfx = '' if _i == 1 else str(_i)
            i = (f.get('conversionInType' + _sfx) or [None])[0]
            o = (f.get('conversionOutType' + _sfx) or [None])[0]
            pv = (f.get('conversionPercentage' + _sfx) or [None])[0]
            if isinstance(i, str) and isinstance(o, str) and pv:
                _cv = (i.lower(), o.lower(), float(pv))
                if _cv not in convs:
                    convs.append(_cv)

    # ★ 装备/套装对技能的改造（`Skill_Modifier`）—— 以前完全没算，是伤害偏低的主因
    from . import skillmod as SM
    from . import gear as GG
    from . import savemap as MM
    skill_mods = SM.item_skill_mods(db, all_parts)

    # ★ 套装：存档存的是记录名，套装成员表用 GT 物品 id → 建「记录 → GT id」反查表
    # ★ 直接用权威桥表（O(1)），别再遍历全库跑规则推导：
    #   实测「for 8612 件物品逐个 resolve()」要烧 7.5 s，而桥表读盘只要 0.1 s。
    from .save import items as _SI
    _br = _SI.bridge()
    _rev = dict(_br.rev)
    for _r, _g in _br.affix_rev.items():
        _rev.setdefault(_r, _g)
    base_gids = []
    if gear_override is not None:
        for _slot in SLOTS:
            _recs = [x for x in (gear_override.get(_slot) or []) if x]
            _g = _rev.get(_recs[0]) if _recs else None
            if _g:
                base_gids.append(_g)
    else:
        for _it in (b3.get('equipment') or []):
            _g = _rev.get(_it.get('basename'))
            if _g:
                base_gids.append(_g)
    for _b in wpn_gids:
        _g = _rev.get(_b)
        if _g:
            base_gids.append(_g)

    set_bucket, found_sets, tag_mods = {}, {}, {}
    sets = SM.load_sets()
    itk = SM.load_item_skills()
    _tag2rec = _tag2rec_map()

    for _sid, _cnt in SM.sets_of_items(base_gids, sets).items():
        _so = sets[_sid]
        found_sets[_sid] = _cnt
        for _k, _v in SM.set_fields(_so, _cnt).items():
            if _k.startswith('__augment__'):
                _r2 = _tag2rec.get(_k[len('__augment__'):])
                if _r2:
                    skill_plus[_r2] = skill_plus.get(_r2, 0) + int(_v)
                continue
            if _k == 'itemSkillModifierControl':
                continue
            set_bucket[_k] = set_bucket.get(_k, 0.0) + float(_v)
        for _tag, _ms in SM.set_skill_mods(_so, _cnt, itk).items():
            tag_mods.setdefault(_tag, []).extend(_ms)

    skill_mods = SM.extra_skill_mods(skill_mods, tag_mods, _tag2rec)
    if set_bucket:
        folded['套装加成'] = set_bucket

    # ★★ **本地提示框覆盖**（`gt_data/local_items.json`，键 = 中文槽位名）
    #    `database.arz` 里没有 LootRandomizer 表、存档只存 roll 种子 ⇒ 词缀的真实数值
    #    只能从**游戏内提示框**读。实测差异巨大：前缀穿刺 6 → **38**、攻击速度 9 → **16**、
    #    还多出「48% 冰冷→穿刺」转换和「强化穿甲利器 158 穿刺」常驻增益（见 §63）。
    local_used, local_skill_lv, loc_aps = [], {}, None
    if local:
        try:
            import gd_local_tip as LT
            for _slot, _txt in (LT.load_local() or {}).items():
                if gear_override is not None and _slot in SLOTS:
                    continue          # ★ 提示框真值绑的是旧装备，别盖到新部件上
                _p = LT.parse_cn_tip(_txt)
                if LT.apply_to_folded(folded, _slot, _p, verbose=False):
                    local_used.append('%s(%d 项)' % (_slot, len(LT.to_slot_fields(_p))))
                for _c in (_p.get('conv') or []):
                    _cv = (str(_c[0]), str(_c[1]), float(_c[2]))
                    if _cv not in convs:
                        convs.append(_cv)
                if _p.get('aps') and _slot in ('主手', '副手') and not loc_aps:
                    loc_aps = float(_p['aps'])
                for _k, _v in (_p.get('skill_lv') or {}).items():
                    local_skill_lv[_k] = local_skill_lv.get(_k, 0) + int(_v)
        except Exception as e:
            print('⚠ 本地提示框解析失败：%s' % e)

    # ★ 武器真实基础攻速（官方公式；有本地提示框时优先用提示框实测值）
    from . import rotation as _R
    base_aps = None
    for _b in wpn_gids:
        if _b and '/gearweapons/' in _b:
            base_aps = SM.weapon_base_aps(db, _b)
            break
    if loc_aps:
        base_aps = loc_aps

    # ★ 装备给的属性平值 / 百分比（用于合成面板属性）
    gf, gp = {}, {}
    for name, key in (('physique', 'Strength'), ('cunning', 'Dexterity'),
                      ('spirit', 'Intelligence')):
        gf[name] = sum(v.get('character' + key) or 0.0 for v in folded.values())
        gp[name] = sum(v.get('character' + key + 'Modifier') or 0.0
                       for v in folded.values())

    _bio = {k: (d['block_map'].get(2) or {}).get(k) or 0.0
            for k in ('physique', 'cunning', 'spirit')}
    if bio_override:
        for _k, _v in bio_override.items():
            if _v is not None:
                _bio[_k] = float(_v)

    # ★★ 武器构成（判武器类型门控）—— 在源头算一次，往下游 `final_report(weapon_st=…)`
    #   传。见 `gd/procs.weapon_state_of()` 的说明：此前只有 `plan_dps` 一条路接了
    #   门控，`gd dps` CLI 等路径全在「零门控」下出数。
    from . import procs as _PRW
    _weapon_st = _PRW.weapon_state(base_gids)

    return {'path': p, 'level': int(level_override or d['level']),
            'classes': list(classes_override or d.get('classes') or []),
            'skills': skills, 'folded': folded, 'db': db,
            'wpn_speed': locals().get('wpn_speed') or ('', None),
            'wpn_tpl': locals().get('wpn_tpl') or '',
            'bio': _bio,
            'skill_plus': skill_plus, 'mastery_plus': mastery_plus,
            'item_skills': item_skills, 'gear_flat': gf, 'gear_pct': gp,
            # ★ 当前配装的 **GT 物品 id 列表**（存档路径与 `gear_override` 路径都覆盖）
            #   —— `gd.procs.wps_pool()` 靠它把「装备授予的武器池技能」查齐后
            #   喂给 `final_report(item_wps=…)`。以前只内部用，没对外暴露。
            'base_gids': list(base_gids),
            # ★★ **武器构成**（2026-09-22）：`final_report(weapon_st=…)` 的输入，
            #   用于「武器类型白名单」硬前提门控（`Axe`/`Spear2h`/`Shield`…，
            #   如狂战士星座要斧或矛）。在源头算一次存下 ⇒ 所有调用点直接
            #   `c['weapon_st']` 取用，不必各自重复推断、也不会口径不一。
            #   看不到武器 ⇒ `None`（= 不门控，零漂移）。
            'weapon_st': _weapon_st,
            'conversions': convs, 'skill_mods': skill_mods,
            'local_used': local_used, 'local_skill_lv': local_skill_lv,
            'set_bucket': set_bucket, 'found_sets': found_sets,
            'base_aps': base_aps}


def guess_arch(skills):
    for rec, lv in skills.items():
        base = os.path.basename(rec).replace('.dbr', '')
        if lv > 0 and base in FORM_ARCH:
            return FORM_ARCH[base]
    return None


def mm(lo, hi):
    return '%.0f' % lo if abs(hi - lo) < 0.05 else '%.0f ~ %.0f' % (lo, hi)


def attack_rows(rep):
    """「有 DPS 的技能」按 DPS 降序 —— 与 `build_report` 第四节同一口径。

    ★ 单独抽出来是因为它跑在**退火热路径**上（每次评估都要拿它做排序）：
      旧写法为了取这个列表先去 `build_report(...)` 渲染一整份中文 Markdown，
      退火跑几万次就是在白烧 CPU。
    """
    hits = rep.get('skills') or {}
    atk = {r: h for r, h in hits.items() if (h.get('dps') or 0) > 0}
    return sorted(atk.items(), key=lambda x: -x[1]['dps'])


def build_report(a, c, sk, skills_zh, arch, rep, brief=False):
    """生成中文 Markdown 报告"""
    L = []
    A = L.append
    hits = rep.get('skills') or {}
    atk = {r: h for r, h in hits.items() if (h.get('dps') or 0) > 0}
    atk_sorted = attack_rows(rep)
    top = atk_sorted[0] if atk_sorted else None
    pct = rep.get('pct') or {}
    gp = rep.get('gear_pct') or {}
    sp = rep.get('skill_pct') or {}
    ap = rep.get('attr_pct') or {}
    dev = __import__('gd_rotation').devotion_pct(arch, sk, c['db'])
    TZ = __import__('gd_rotation').TYPE_ZH

    A('# 存档实测 DPS 校验 —— %s' % a.char)
    A('')
    A('> **等级 %d** ｜ 职业 **%s** ｜ 打法 **%s** ｜ 技能 %d 条 ｜ 装备 %d 件（含武器）'
      % (c['level'], '+'.join(c['classes']) or '?', FORM_ZH.get(arch, arch),
         len(sk), len(c['folded'])))
    A('')

    # ★★ 武器类型硬前提门控账本（2026-09-22）：**非空才打印**。
    #   为什么必须在这里可见：门控会让技能/星座星位**静默归零** ——
    #   玩家只看到「数字变小了」，却不知道是哪条、为什么。空 ⇒ 不占版面。
    _rot_g = rep.get('rotation') or {}
    _lost_g = _rot_g.get('weapon_dropped') or []
    if _lost_g:
        A('> ⚠ **武器类型门控**：%d 条技能/星位因**武器不符**完全不生效'
          % len(_lost_g))
        for _e in _lost_g:
            A('> · `%s`（%s 通道）—— %s'
              % (_e.get('rec', '').rsplit('/', 1)[-1],
                 _e.get('channel') or 'levels', _e.get('why') or ''))
        A('')

    # ---- 结论
    A('## 一句话结论')
    A('')
    if top:
        A('- 主输出是 **%s**：打一下 **%s**（平均 %.0f），每秒 %.2f 下 → '
          '**每秒伤害 %.0f（占 %.0f%%）**'
          % (zh_name(top[0], skills_zh), mm(top[1]['min'], top[1]['max']),
             top[1]['avg'], top[1]['freq'], top[1]['dps'],
             top[1]['dps'] / rep['dps'] * 100 if rep['dps'] else 0))
    A('- 合计 **每秒伤害 %.0f**' % rep['dps'])
    if pct:
        mt = max(pct, key=lambda t: pct[t])
        A('- 主力伤害类型是 **%s**，加成合计 **+%.0f%%**'
          '（装备 +%.0f%% ／ 技能 +%.0f%% ／ 星座 +%.0f%% ／ **属性 +%.0f%%**）'
          % (TZ.get(mt, mt), pct[mt], gp.get(mt, 0),
             sp.get(mt, 0) - dev.get(mt, 0), dev.get(mt, 0), ap.get(mt, 0)))
    A('')

    # ---- 白话解释
    A('## 一、先看懂这些数字（白话版）')
    A('')
    A('| 名词 | 什么意思 | 在下面哪一栏 |')
    A('|---|---|---|')
    A('| 技能自带伤害 | 技能本身写死的固定伤害（技能提示框上半部分那些数） | 「技能自带」列 |')
    A('| 武器带出的伤害 | 技能按「武器伤害%」把**武器基础伤害**带进来的部分 | 「武器带出」列 |')
    A('| 加成倍率 | 该伤害类型的全部 % 加成之和；+640% 就是 **7.4 倍** | 「加成倍率」列 |')
    A('| **最终伤害（打一下）** | 前两项相加 → 乘加成倍率 → 再乘独立倍率（如野兽形态） | 「打一下」列 |')
    A('| 触发率 | 这一击出现在普攻里的机会（武器池技能是**概率触发**） | 「触发率」列 |')
    A('| 每秒打几下 | 有冷却的技能 = 1 ÷ 冷却；普攻 = 攻速 × 触发率 | 「每秒几下」列 |')
    A('| **每秒伤害（DPS）** | 打一下的伤害 × 每秒打几下 | 「每秒伤害」列 |')
    A('')
    A('```')
    A('最终伤害(打一下) = ( 技能自带 + 武器伤害% × 武器基础伤害 ) × (1 + 所有%加成) × (1 + 独立倍率)')
    A('```')
    A('')
    if top:
        rec, h = top
        r0 = h['rows'][0]
        tn = TZ.get(r0['type'], r0['type'])
        A('### 拿「%s」当例子，一步一步算' % zh_name(rec, skills_zh))
        A('')
        A('1. **技能自带** %s **%.0f**（%d 级）' % (tn, r0['skill'][0], h['level']))
        wb = (rep['weapon'] or {}).get(r0['type']) or (0.0, 0.0)
        A('2. **武器带出**：武器基础 %s %s，技能按 **%.0f%% 武器伤害** 取'
          ' → **%.0f ~ %.0f**'
          % (tn, mm(wb[0], wb[1]), h['weapon_pct'],
             r0['weapon'][0], r0['weapon'][1]))
        A('3. **相加** → %.0f ~ %.0f' % (r0['skill'][0] + r0['weapon'][0],
                                        r0['skill'][1] + r0['weapon'][1]))
        A('4. **乘加成倍率**（1 + %.0f%% = **%.2f 倍**）→ %.0f ~ %.0f'
          % (r0['pct'], 1 + r0['pct'] / 100,
             (r0['skill'][0] + r0['weapon'][0]) * (1 + r0['pct'] / 100),
             (r0['skill'][1] + r0['weapon'][1]) * (1 + r0['pct'] / 100)))
        if rep.get('dmg_mult'):
            A('5. **乘独立倍率**（野兽形态 +%.0f%%）→ **%s** ← 你一刀打出来的数'
              % (rep['dmg_mult'], mm(h['min'], h['max'])))
        else:
            A('5. 没有独立倍率 → **%s** ← 你一刀打出来的数' % mm(h['min'], h['max']))
        A('6. 再 × 每秒打 %.2f 下 = **每秒伤害 %.0f**' % (h['freq'], h['dps']))
        A('')

    # ---- 武器
    A('## 二、武器基础伤害（所有加成之前）')
    A('')
    A('| 伤害类型 | 数值 |')
    A('|---|---|')
    for t, v in sorted(dict(list(rep['weapon'].items())
                            + list((rep.get('weapon_dot') or {}).items())).items(),
                       key=lambda x: -x[1][1]):
        A('| %s | %s |' % (TZ.get(t, t), mm(v[0], v[1])))
    A('')
    if rep.get('pierce_ratio'):
        A('- 已做 **物理 → 穿刺转换 %.0f%%**（武器上的「%%穿刺」词条）' % rep['pierce_ratio'])
    A('- 武器速度标签 **%s** ｜ 武器模板 **%s** ｜ `characterBaseAttackSpeed` = %s'
      % ((c.get('wpn_speed') or ('', None))[0] or '未标注',
         c.get('wpn_tpl') or '?',
         (c.get('wpn_speed') or ('', None))[1]))
    A('- 攻击速度 **%.2f 次/秒**（基础 **%.4f** × (1 + %.0f%%)）'
      % (rep['aps'], rep['base_aps'], rep['speed_pct']))
    A('  > 基础攻速按 GT `calc.js` 官方公式：`(1 + characterBaseAttackSpeed) × 类别基准`；'
      '单手近战基准 **1.973684311**（75/38）、双手与远程 **1.7857143**（25/14）。'
      '已用 8 个 GT 提示框实测值 **20/20 命中**（斧 1.86 / 匕首 1.93 / 权杖 1.78 / '
      '单手枪 1.70 / 双手锤 1.43、1.52）')
    A('- **游戏面板核对**：角色面板「Attacks per Second」应 = **%.2f**；'
      '若不一致，用 `--aps %.4f` 覆盖后重跑' % (rep['aps'], rep['aps'] / (1 + rep['speed_pct'] / 100.0)))
    if rep.get('dmg_mult'):
        A('- 独立伤害倍率 **+%.0f%%**（野兽形态；乘在所有加成**之后**）' % rep['dmg_mult'])
    A('')

    # ---- 加成来源（**与 GT 面板口径一致**：修正% 不含属性，属性单列）
    A('## 三、% 加成都是哪来的（装备 / 套装 / 技能 / 星座 / 属性）')
    A('')
    A('| 伤害类型 | 装备 | 套装 | 技能 | 星座 | **属性** | 合计 |')
    A('|---|---|---|---|---|---|---|')
    _bp = rep.get('bucket_pct') or {}
    _setp = _bp.get('套装加成') or {}
    for t, v in sorted(pct.items(), key=lambda x: -x[1]):
        if not v:
            continue
        A('| %s | +%.0f%% | %s | %s | %s | %s | **+%.0f%%** |'
          % (TZ.get(t, t), gp.get(t, 0) - _setp.get(t, 0),
             ('+%.0f%%' % _setp[t]) if _setp.get(t) else '—',
             ('+%.0f%%' % (sp[t] - dev.get(t, 0))) if sp.get(t) else '—',
             ('+%.0f%%' % dev[t]) if dev.get(t) else '—',
             ('+%.0f%%' % ap[t]) if ap.get(t) else '—', v))
    A('')
    A('> ⚠ **属性** 是独立乘区（物理/穿刺吃狡诈 ÷245、元素与魔法吃精神 ÷215 等）。'
      '**GT 面板的「修正%」不含属性**，对账时请用 装备+套装+技能+星座 四列相加。')
    A('')
    A('### 属性（存档值 → 面板值 → 伤害加成）')
    A('')
    AV = __import__('gd_rotation')
    A('| 属性 | 存档值 | 精通 | 装备平值 | 装备%% | **面板值** | 影响 |')
    A('|---|---|---|---|---|---|---|')
    _b = c.get('bio') or {}
    _gf = c.get('gear_flat') or {}
    _gp = c.get('gear_pct') or {}
    mast = c.get('mastery_eff') or {}
    attrs = c.get('attrs') or {}
    _mast_txt = '、'.join('%s %d 级' % (v, lv) for v, lv in sorted(mast.items()))
    for name, zh, aff in (('physique', '体格', '—'), ('cunning', '狡诈', '物理 / 穿刺 / 流血'),
                          ('spirit', '精神', '元素 / 魔法')):
        A('| %s | %.0f | %s | %+.0f | %+.0f%% | **%.0f** | %s |'
          % (zh, _b.get(name, 0), _mast_txt if name == 'physique' else '',
             _gf.get(name, 0), _gp.get(name, 0), attrs.get(name, 0), aff))
    A('')
    A('> 面板值 = (存档值 + 精通给点 + 装备平值) × (1 + 装备%%/100)；存档存的是 '
      '`50 + 8 × 投入点`（`playerBio.*Increment = 8`）。')
    A('> 属性伤害加成按游戏公式：**物理/穿刺 = 狡诈 ÷ 245**、魔法 = 精神 ÷ 215、'
      '物理持续(流血/内部创伤) = 狡诈 ÷ 215、魔法持续 = 精神 ÷ 200。')
    A('')
    R = __import__('gd_rotation')
    src = []
    for rec, lv in sk.items():
        v = R._at((c['db'].fields(rec) or {}).get('offensivePierceModifier') or [], lv)
        if v:
            src.append((zh_name(rec, skills_zh), v))
    if src:
        A('> 「技能 + 星座」侧的穿刺加成逐条：%s'
          % '、'.join('%s +%.0f%%' % (n, v) for n, v in sorted(src, key=lambda x: -x[1])))
        A('')

    # ---- 汇总
    A('## 四、逐技能最终伤害（按每秒伤害排序）')
    A('')
    A('| 技能 | 等级 | 触发率 | 打一下（最低~最高） | 平均 | 每秒几下 | 每秒伤害 | 占比 |')
    A('|---|---|---|---|---|---|---|---|')
    for rec, h in atk_sorted:
        A('| **%s** | %d | %s | **%s** | %.0f | %.2f | **%.0f** | %.0f%% |'
          % (zh_name(rec, skills_zh), h['level'],
             '—' if h.get('chance') is None else '%.1f%%' % (h['chance'] * 100),
             mm(h['min'], h['max']), h['avg'], h.get('freq') or 0, h['dps'],
             h['dps'] / rep['dps'] * 100 if rep['dps'] else 0))
    A('| **合计** | | | | | | **%.0f** | 100%% |' % rep['dps'])
    A('')
    A('> 触发率不是 100% 的是**武器池技能（WPS）**：普攻时按概率替换成这一击，'
      '所以按概率折算频率。')
    A('')

    # ---- ★ 实战伤害（敌方减抗乘区）
    vs = rep.get('vs')
    if vs and vs.get('rows'):
        A('## 四点五、实战伤害（敌方减抗乘区）')
        A('')
        A('> 上面所有数字都是**面板口径**（不含敌方抗性），所以能与 grimtools 逐项对拍。'
          '实战里还要乘一层 `(1−敌方剩余抗性) ÷ (1−敌方标称抗性)` —— 这层由**减抗**决定，'
          '是 GD 最大的伤害乘区。当前目标：**%s**。'
          % (vs.get('profile_zh') or vs.get('profile')))
        if not vs.get('per_bucket'):
            A('> ')
            A('> ⚠ 敌方抗性用的是**五档手填的全体同值档**（标称 %.0f%%）；'
              '要切到真值请加 `--enemy-profile pool:Champion+Hero@0.5`。'
              % (vs.get('enemy_res') or 0))
        A('')
        # ★ 敌方抗性**全表 10 型**：顺序与游戏面板一致（前 5 = 第一排，后 5 = 第二排）。
        #   面板第二排（流血/活力/以太/混乱/物理）在旧模型里是**整块缺失**的 ——
        #   它们不在怪物记录里，而在**被动技能**里（见 `gd/enemy.py::passive_block`）。
        if vs.get('per_bucket'):
            from . import rr as _RRb
            from . import enemy as _ENb
            _pb = vs['per_bucket']
            _rrs = rep.get('rr') or {'add': {}, 'max': {}}
            _usedb = set()
            for _t, _v in (_RRb.dps_by_type(rep.get('skills') or {}) or {}).items():
                if _v > 0:
                    _bb = _RRb.TYPE_BUCKET.get(_t)
                    if _bb:
                        _usedb.add(_bb)
            A('**敌方抗性全表（10 型）** —— 顺序与游戏面板相同，'
              '**前 5 型 = 面板第一排、后 5 型 = 第二排**：')
            A('')
            A('| 排 | 类型 | 敌方抗性 | 循环里用到 | 我方减抗 | 剩余抗性 |')
            A('|---|---|---|---|---|---|')
            for _i, _b in enumerate(_ENb.RES_ORDER):
                _br = _pb.get(_b)
                if _br is None:
                    continue
                _ad = float((_rrs.get('add') or {}).get(_b, 0.0))
                _mx = float((_rrs.get('max') or {}).get(_b, 0.0))
                _txt = ('−%.0f%%' % _ad) if _ad else ''
                if _mx:
                    _txt += ('　最多 −%.0f%%' % _mx)
                A('| %s | %s | %.0f%% | %s | %s | %+.0f%% |'
                  % ('第一排' if _i < 5 else '**第二排**', _RRb.BUCKET_ZH.get(_b, _b),
                     _br, '**✓**' if _b in _usedb else '—', _txt or '—',
                     _RRb.res_eff(_b, _rrs, _br)))
            A('')
            _arm = (vs.get('enemy') or {}).get('armor')
            if isinstance(_arm, (int, float)) and _arm > 0:
                A('> 该怪**护甲值 %.0f**（来自被动技能 `defensiveProtection`，'
                  '见「四点六」；`gd/enemy.py::armor_of`）。' % _arm)
                A('')
        A('| 抗性桶 | 敌方标称 | 我方减抗（B 叠加 / C 最多 / A 最多） | 剩余抗性 | 伤害倍数 | '
          '面板 DPS | **实战 DPS** |')
        A('|---|---|---|---|---|---|---|')
        for r in vs['rows']:
            rrt = ('−%.0f%%' % r['rr_add']) if r['rr_add'] else ''
            if r['rr_pct']:
                rrt += ('　最多 −%.0f%%' % r['rr_pct'])
            if r.get('rr_flat'):
                rrt += ('　最多 −%.0f（绝对）' % r['rr_flat'])
            A('| %s | %.0f%% | %s | **%+.0f%%** | **%.2f×** | %.0f | **%.0f** |'
              % (r['zh'], r['base'], rrt or '—', r['res'], r['mult'], r['dps'], r['dps_vs']))
        A('| **合计** | | | | **%.2f×** | **%.0f** | **%.0f** |'
          % (vs['mult_overall'], vs['dps'], vs['dps_vs']))
        A('')
        _rrs = rep.get('rr') or {}
        if _rrs.get('add') or _rrs.get('max') or _rrs.get('flat'):
            A('> 减抗来源明细（`gd/rr.py` 收集）：叠加类合计 %s'
              % '、'.join('%s −%.0f%%' % (__import__('gd.rr', fromlist=['x']).BUCKET_ZH.get(b, b), v)
                          for b, v in sorted((_rrs.get('add') or {}).items()))
              if _rrs.get('add') else '> （无叠加类减抗）')
            if _rrs.get('max'):
                # ⚠ `%` 必须写成 `%%` —— 这个字符串走 `%` 格式化，而「%目标抗性降低」
                #   里的 `%目` 会被当成格式说明符 ⇒ `ValueError: unsupported format
                #   character '目'`。**旧 build 没有 C 类减抗 ⇒ 这条分支从没执行过**，
                #   直到 2026-09-21 落档「罗卡口径」的装备才暴露。
                A('> 取最强类（%%目标抗性降低）：%s'
                  % '、'.join('%s −%.0f%%' % (__import__('gd.rr', fromlist=['x']).BUCKET_ZH.get(b, b), v)
                              for b, v in sorted(_rrs['max'].items())))
            if _rrs.get('flat'):
                A('> 取最强类（绝对值｜目标抗性降低，无 %%）：%s'
                  % '、'.join('%s −%.0f' % (__import__('gd.rr', fromlist=['x']).BUCKET_ZH.get(b, b), v)
                              for b, v in sorted(_rrs['flat'].items())))
            A('')
        A('> 口径（结算顺序 **B → C → A**）：**B 类**（技能上的 `−X%% 抗性`）**叠加**相减；'
          '**C 类**（`X%% 目标抗性降低`）**只取最高**、**乘算**，且对已是负抗性的目标是'
          '**加深**（`r×(1+c%%)`，不是「负收益」）；**A 类**（`X 目标抗性降低`，无 %% ）'
          '**只取最高**、最后相减。敌抗**可减成负数**、按 1:1 放大，下限 **%.0f%%**'
          '（环境变量 `GD_RR_FLOOR` 可调）。' % __import__('gd.rr', fromlist=['x']).rr_floor())
        A('')

    # ---- ★ 护甲 / 破甲（按输出循环的主要类型逐条裁决）
    am = rep.get('armor') or {}
    if am.get('by_type'):
        A('## 四点六、护甲 / 破甲（按输出类型逐条裁决）')
        A('')
        A('> 本作**护甲只吃物理直伤**：穿刺是「护甲穿透」从物理**转出去**的，'
          '转出后归穿刺抗性管，不再回头吃护甲；创伤（物理 DoT）走 DoT 通道。'
          '⇒ **「元素护甲」这个东西不存在**，元素伤害的减伤通道是**抗性**（见上一节）。'
          '判据单点在 `gd/combat.py::armor_applies()`。')
        A('')
        A('| 伤害类型 | 循环占比 | 是否过护甲 | 武器护甲穿透 | 护甲减伤 | 对应抗性桶 |')
        A('|---|---|---|---|---|---|')
        for _t in (am.get('covered_types') or []):
            _r = am['by_type'][_t]
            A('| %s | %.2f%% | %s | %s | %s | %s |'
              % (_r['zh'], _r['share'] * 100,
                 '**是**' if _r['armor_affected'] else '否',
                 ('%.0f%%' % _r['armor_pierce_pct']) if _r['armor_pierce_pct'] else '—',
                 ('%.2f%%' % _r['mitigation_pct']) if _r['mitigation_pct'] is not None else '—',
                 _r['res_bucket']))
        A('')
        _rd = am.get('reduce') or {}
        A('- **吸收率**：怪物侧 **%.0f%%**（%s）｜ 引擎/玩家基准 %.0f%%'
          % (am.get('absorption') or 0, am.get('absorption_basis') or '—',
             am.get('absorption_player') or 0))
        A('- **破甲**（`%s` = 「%s」）：本角色 **%.0f**'
          % (_rd.get('field'), _rd.get('zh'), _rd.get('value') or 0))
        _census = _rd.get('census') or {}
        _cen = '、'.join('%s %s' % (v.get('zh'), ('%d 次' % v['count'])
                                    if v.get('count') is not None else '未读到')
                        for v in _census.values())
        A('  - ★ 玩家侧**没有这个机制**（实测普查：%s）。'
          '挂点已就位（参数 `armor_reduce` / `--enemy-armor-reduce`），'
          '数据侧恒为 0；哪天版本/Mod 带上这个字段，模型不改一行就能吃到。' % (_cen or '—'))
        if am.get('applied'):
            _av = (am.get('reduce') or {}).get('armor_before') or am.get('armor_before')
            A('- ✓ **护甲值已套用**：该敌方档 **%.0f**（%s），上表「护甲减伤」为真值。'
              % (_av or 0, am.get('armor_source') or '来自敌方档'))
        else:
            A('- ⚠ **当前档位没有单一护甲值**：等级池聚的是**分位数**（每只怪护甲各不同，'
              '不做分位聚合），五档手填更无护甲 ⇒ 上表「护甲减伤」留空，'
              '**物理那部分按下界报**。')
            A('  - 想拿真值：`--enemy-profile m<id>` —— 真值怪的护甲由被动技能 '
              '`defensiveProtection` 读出并**自动套用**（如 `m3955` = 1607，见自检 [24]）。'
              '也可 `--enemy-armor 300` 手工指定，`--enemy-armor-reduce 120` 试破甲。')
        A('')
        A('')

    # ---- 明细
    if not brief:
        A('## 五、每个技能的伤害构成')
        A('')
        for rec, h in atk_sorted:
            A('### %s · %d 级 ｜ 每秒伤害 **%.0f**'
              % (zh_name(rec, skills_zh), h['level'], h['dps']))
            A('')
            A('| 伤害类型 | 技能自带 | 武器带出（%.0f%%武器伤害） | 加成倍率 | 打一下 |'
              % h['weapon_pct'])
            A('|---|---|---|---|---|')
            for r in h['rows']:
                A('| %s%s | %.0f | %.0f | +%.0f%% | **%s** |'
                  % (TZ.get(r['type'], r['type']),
                     '（持续伤害）' if r['is_dot'] else '',
                     r['skill'][0], r['weapon'][0], r['pct'], mm(r['min'], r['max'])))
            A('| **合计** | | | | **%s**（平均 %.0f） |' % (mm(h['min'], h['max']), h['avg']))
            A('')

    # ---- 装备给技能加的等级
    sp_plus = c.get('skill_plus') or {}
    if sp_plus:
        A('## 六、装备给的技能等级（★ 以前完全没算）')
        A('')
        A('| 技能 | 装备加成 | 存档加点 | **有效等级** | 装备 |')
        A('|---|---|---|---|---|')
        IR = __import__('gd_rotation')
        for rec, extra in sorted(sp_plus.items(), key=lambda x: -x[1]):
            base = sk.get(rec)
            eff_lv = c.get('eff', {}).get(rec)
            if eff_lv is None:
                continue
            if not (base or eff_lv > extra):
                continue
            A('| %s | +%d | %s | **%d** | — |'
              % (zh_name(rec, skills_zh), extra, base if base else '（未点）', eff_lv))
        A('')
        A('> 装备上的 `augmentSkillName/augmentSkillLevel`（本角色头部给**狼人形态 +3**、'
          '胸甲给**贪噬 +2 / 野兽形态 +2**）。上面只列了本角色**有加点**的那些。')
        A('')
        mp = c.get('mastery_plus') or {}
        if mp:
            A('> 精通也被加成：%s（本报告已按有效精通计算属性）'
              % '、'.join('%s +%d' % (k, v) for k, v in mp.items()))
            A('')

    # ---- 装备触发技能
    its = c.get('item_skills') or []
    if its:
        A('## 七、装备自带的**触发技能**（这些不吃普攻等级，但会额外打伤害）')
        A('')
        A('| 来源装备 | 触发技能 | 触发条件 |')
        A('|---|---|---|')
        for src, n, ctrl in its:
            base = os.path.basename(ctrl).replace('cast_@self', '').replace('.dbr', '')
            zh_ctrl = {'onattack_10%': '攻击时 10%', 'onattack_20%': '攻击时 20%',
                       'onanyhit_10%': '任意命中 10%', 'onattackcrit_33%': '暴击时 33%',
                       '': '常驻/光环'}.get(base, base)
            A('| %s | %s | %s |'
              % (zh_name(src, skills_zh) if src in sk else os.path.basename(src),
                 zh_name(n, skills_zh), zh_ctrl))
        A('')
        A('> 这些是**独立的伤害来源**（例：武器 20% 概率施放钢铁之环），'
          '本报告的单次伤害里**不含**它们，所以实际总输出会略高于第四节。')
        A('')

    # ---- 被动
    others = [r for r in hits if r not in atk]
    if others:
        A('## 八、不带普攻等级的技能（被动 / 开关 / 触发型）')
        A('')
        A('> 它们本身不按普攻节奏输出（所以没有「每秒伤害」），但**加成已算进第三节**。')
        A('')
        A('| 技能 | 等级 |')
        A('|---|---|')
        pairs = dedup([(r, zh_name(r, skills_zh))
                       for r in sorted(others, key=lambda x: -hits[x]['level'])])
        for r, n in pairs:
            A('| %s | %d |' % (n, hits[r]['level']))
        A('')
    un = [r for r in sorted([r for r in sk if r not in hits], key=lambda x: -sk[x])
          if not os.path.basename(r).startswith('default')]
    if un:
        A('## 九、没有伤害数字的技能（**不是漏算**：它们本身没有伤害字段）')
        A('')
        A('| 技能 | 等级 | 作用 |')
        A('|---|---|---|')
        dev_nodes = [r for r in un
                     if os.path.basename(r).startswith('tier')
                     or 'devotion' in r.lower()]
        rest = [r for r in un if r not in dev_nodes]
        for r, n in dedup([(r, zh_name(r, skills_zh)) for r in rest]):
            base = os.path.basename(r).replace('.dbr', '')
            A('| %s | %d | %s |' % (n, sk[r], NOTE_ZH.get(base, '—')))
        if dev_nodes:
            A('| **星座节点（%d 个）** | — | 只提供加成/触发效果，本身没有武器攻击数值；'
              '带 %% 加成的已计入第三节 |' % len(dev_nodes))
        A('')

    # ---- 对账
    A('## 十、和游戏面板怎么对账')
    A('')
    A('| 本报告 | 游戏里看哪里 |')
    A('|---|---|')
    A('| 第二节 武器基础伤害 | 武器提示框顶部的「物理/穿刺/…」基础值（还没乘加成的） |')
    A('| 第三节 % 加成合计 | 角色面板 → 伤害加成（该伤害类型那一行） |')
    A('| 第四节 「打一下」 | 技能提示框里「武器攻击」那一段的数值 |')
    A('')
    A('## 十一、可能对不上的地方（先说清楚）')
    A('')
    A('1. **词缀/紫装的随机 roll**：存档只存 roll 种子，这里读记录默认值 ⇒ 可能差几个百分点')
    A('2. **星座只算「记录本身带加成」的星节点**，经 _buff / 二次计算派生的部分没算')
    A('3. **基础攻速**按 GT `calc.js` 官方公式算（**已 20/20 实测命中**，不再是假设值）；'
      '若游戏面板的「Attacks per Second」和第二节对不上，用 `--aps %.4f` 覆盖'
      '（面板值 ÷ (1+攻击速度%%) = 基础攻速）' % (rep['aps'] / (1 + rep['speed_pct'] / 100.0)))
    A('4. **第一节的「合计」是面板口径**（不含敌方抗性/护甲，用来跟 grimtools 对拍）；'
      '加了敌方档之后，**实战**与**对怪**两个口径才含命中/暴击期望与逐桶减抗')
    A('5. **怪物护甲值**取自**被动技能**的 `defensiveProtection`（等级索引数组），'
      '与游戏面板「护甲等级」逐位一致（见自检 [24]）；'
      '若某只怪没有护甲被动 ⇒ 该行留空，物理直伤即**下界**。'
      '也可显式给 `--enemy-armor` 覆盖。')
    A('')
    return '\n'.join(L) + '\n', atk_sorted


def resolve_enemy_armor(explicit, enemy_profile):
    """决定这一跑用哪个敌方护甲值，返回 `(value, 来源说明)`。

    优先级：**显式 `--enemy-armor` > 敌方档自带的 `armor` > 无**。
    敌方档里只有**真值怪**（`m<id>` / `dummy()`）带 `armor`（读自被动技能
    `defensiveProtection`，与游戏面板逐位一致）；`pool:`（分位数聚合）与五档手填
    没有单一护甲值 ⇒ 返回 `(None, …)`，报告里如实标注「物理直伤为下界」。

    抽成函数是为了让 `tools/selftest.py` 能直接断言这条契约（不用跑整条 CLI）。
    """
    if explicit is not None:
        return float(explicit), "显式 `--enemy-armor`"
    if isinstance(enemy_profile, dict) and enemy_profile.get("armor"):
        return (float(enemy_profile["armor"]),
                "敌方档 `%s` 的被动技能 `defensiveProtection`"
                % (enemy_profile.get("id") or "m?"))
    return None, "该敌方档没有单一护甲值（等级池分位数 / 五档手填）"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('char')
    ap.add_argument('--arch', default='')
    ap.add_argument('--brief', action='store_true', help='只看汇总，跳过逐技能明细')
    ap.add_argument('--aps', type=float, default=None,
                    help='武器基础攻速（不给则按武器记录的 模板+characterBaseAttackSpeed 用官方公式算）')
    ap.add_argument('--no-local', dest='local', action='store_false', default=True,
                    help='不用 gt_data/local_items.json 里的游戏内提示框覆盖')
    ap.add_argument('--weapon-set', dest='wset', default='', choices=['', 'alt1', 'alt2'],
                    help='强制用哪套武器（不给则按存档的 use_alt_weaponset / *_unused 标志自动选）')
    ap.add_argument('--enemy-res', dest='enemy_res', default=None,
                    help='敌方抗性档位（none/elite/boss/high/max，默认 elite）；'
                         '给了就会在报告里加「实战伤害（含减抗乘区）」一节')
    ap.add_argument('--no-vs', dest='vs', action='store_false', default=True,
                    help='不算实战伤害（只出面板口径）')
    ap.add_argument('--enemy-profile', dest='enemy_profile', default=None,
                    help='敌方档（`gd/enemy.py`）：五档 none/elite/boss/high/max、'
                         '真值怪 m<id>（如 m4139 训练假人）、'
                         '等级池 pool:Champion+Hero@0.5；默认读 GD_ENEMY_PROFILE')
    ap.add_argument('--enemy-armor', dest='enemy_armor', type=float, default=None,
                    help='敌方护甲值（不给则自动用敌方档的 armor —— 真值怪有，'
                         '等级池/五档手填没有）')
    ap.add_argument('--enemy-armor-reduce', dest='enemy_armor_reduce', type=float,
                    default=None,
                    help='破甲值：`N 点目标护甲降低`。先减甲再算减免。'
                         '⚠ 玩家侧无此字段（itemdb/skills 出现 0 次），仅供压力测试/怪物反向场景')
    ap.add_argument('--enemy-difficulty', dest='enemy_diff', default='ultimate',
                    choices=['normal', 'elite', 'ultimate', 'ascendant'],
                    help='真值口径（m<id> / pool:）的难度档，默认 ultimate')
    ap.add_argument('--enemy-players', dest='enemy_players', type=int, default=1,
                    help='玩家数 1-4（影响敌方生命/伤害修正），默认 1')
    ap.add_argument('--out', default='')
    a = ap.parse_args()

    from . import rotation as R
    c = load_char(a.char, a.wset, a.local)
    sk = c['skills']
    skills_zh = load_skills_zh()
    arch = a.arch or guess_arch(sk) or 'werewolf'

    # ★ 有效技能等级 = 存档加点 + **装备给的 +等级**
    eff = dict(sk)
    for rec, extra in (c.get('skill_plus') or {}).items():
        if rec in eff:
            eff[rec] += extra
    # ★ 有效精通等级（存档 + 装备）
    mast = {}
    for rec, lv in sk.items():
        if '_classtraining_' in rec:
            mast[os.path.basename(rec).replace('_classtraining_', '')
                 .replace('.dbr', '')] = lv
    for cls, extra in (c.get('mastery_plus') or {}).items():
        mast[cls] = mast.get(cls, 0) + extra
    # ★ 面板属性（存档值 + 精通 + 装备），再按官方公式得属性伤害加成
    attrs = R.panel_attrs(c.get('bio') or {}, mast,
                          c.get('gear_flat') or {}, c.get('gear_pct') or {})
    apct = R.attr_damage_pct(attrs)

    c['mastery_eff'], c['attrs'], c['eff'] = mast, attrs, eff
    # ★ 基础攻速优先级：--aps 手动 > 武器记录官方公式反推 > 1.25 兜底
    #   （旧版 `--aps` 默认写死 1.25 → **永远压过反推值**，第 226 行的反推是死代码）
    _base_aps = a.aps if a.aps else (c.get('base_aps') or 1.25)
    c['base_aps_used'] = _base_aps

    # ★ 实战伤害（敌方减抗乘区）：收集「装备折叠 + 技能/星座」两侧的减抗
    _rr, _rr_gear, _rr_skill = ({}, {}, {})
    if a.vs:
        from . import rr as _RRM
        _rr, _rr_gear, _rr_skill = _RRM.collect_char(c['folded'], eff, c['db'])
        c['rr_gear'], c['rr_skill'] = _rr_gear, _rr_skill

    # ★ 破甲（护甲降低）：**先自动收集**，CLI 显式给了就覆盖。
    #   当前数据下自动收集恒为 0（玩家侧无此字段，见 `gd/rr.py::armor_reduce_census`），
    #   但走「收集」而不是「写 0」——这样有了数据它自己就生效。
    from . import rr as _RRA
    _ared = _RRA.armor_reduce_of_folded(c['folded'], with_skills=True,
                                        skills=eff, db=c['db'])
    if getattr(a, 'enemy_armor_reduce', None) is not None:
        _ared = float(a.enemy_armor_reduce)
    c['armor_reduce'] = _ared

    # ★ 敌方档（`gd/enemy.py`）：真值怪 / 五档手填 / 等级池。
    #   命中与暴击乘区需要**敌方 DA**（Step 4），敌方抗性则喂给 `rr.apply_vs`。
    from . import enemy as _ENM
    _ep = _ENM.get_profile(getattr(a, 'enemy_profile', None), c.get('level') or 100,
                           players=getattr(a, 'enemy_players', 1) or 1,
                           difficulty=_ENM.DIFFICULTIES.get(
                               getattr(a, 'enemy_diff', 'ultimate') or 'ultimate', 3))
    c['enemy'] = _ep

    # ★ 敌方护甲：真值怪（`m<id>`）的 `armor` 已由被动技能 `defensiveProtection` 读出
    #   （`gd/enemy.py::armor_of`，与游戏面板逐位一致，见自检 [24]）⇒ 直接喂给模型，
    #   不再需要人工 `--enemy-armor`。显式给参数时以参数为准。
    #   ⚠ 等级池（默认档）聚合的是**分位数**，没有单一护甲值 ⇒ `armor=None`，
    #     此时「四点六」如实标注「该行留空 ⇒ 物理直伤为下界」。
    _e_arm, _e_src = resolve_enemy_armor(getattr(a, 'enemy_armor', None), _ep)
    c['enemy_armor'] = _e_arm
    c['enemy_armor_src'] = _e_src

    # ★★ 技能来源合法性（A 方案 · 2026-09-20）：把「当前装备授予的技能组」交给模型，
    #   让 `records/skills/itemskills*` 的记录**只有真被装备授予时才计入**。
    #   判据与边界见 `gd/skillprov.py`；非物品技能不受影响。
    from . import skillprov as _SP
    _eq_sk = _SP.norm_equipped([x[1] for x in (c.get('item_skills') or [])])
    c['equipped_sk'] = _eq_sk

    # ★★ 装备授予的武器池技能（WPS）—— 2026-09-20 入池（用户批准）。
    #   见 `gd/procs.py::wps_of_item`：记录路径经 `skillprov` 翻译，
    #   权重只存在于离线库 `itemSkills` 表（`.dbr` / `skills.json` 都没有）。
    from . import procs as _PR
    _item_wps = _PR.wps_pool(c.get('base_gids'))

    rep = R.final_report(arch, eff, db=c['db'], folded=c['folded'],
                         skill_records=list(eff),
                         base_aps=_base_aps,
                         attr_pct=apct,
                         conversions=c.get('conversions') or [],
                         skill_mods=c.get('skill_mods') or {},
                         rr=_rr if a.vs else None,
                         enemy_res=(a.enemy_res or None) if a.vs else None,
                         attrs=attrs, level=c.get('level'), enemy=_ep,
                         enemy_armor=_e_arm,
                         enemy_armor_src=_e_src,
                         armor_reduce=_ared,
                         equipped_sk=_eq_sk,
                         item_wps=_item_wps,
                         # ★★ 武器类型门控（2026-09-22）：与 `plan_dps.dps_of` 同口径。
                         #   此前 CLI 这条**没接** ⇒ 「同一个角色，CLI 与优化器两个数」。
                         weapon_st=c.get('weapon_st'))

    text, atk_sorted = build_report(a, c, sk, skills_zh, arch, rep, a.brief)
    out = a.out or os.path.join(HERE, 'plans', '%s_dps_check.md' % a.char)
    try:
        with io.open(out, 'w', encoding='utf-8') as fh:
            fh.write(text)
    except Exception as e:
        print('⚠ 落盘失败：%s' % e)

    print('存档实测 DPS 校验 —— %s（lv%d %s ｜ %s）'
          % (a.char, c['level'], '+'.join(c['classes']) or '?', FORM_ZH.get(arch, arch)))
    print('  武器基础：%s（攻速 %.2f 次/秒，独立倍率 +%.0f%%）'
          % ('、'.join('%s %s' % (R.TYPE_ZH.get(t, t), mm(v[0], v[1]))
                       for t, v in rep['weapon'].items()),
             rep['aps'], rep.get('dmg_mult', 0)))
    for rec, h in atk_sorted:
        print('  %-12s %2d 级   打一下 %-16s 每秒 %.2f 下 → 每秒伤害 %6.0f（%.0f%%）'
              % (zh_name(rec, skills_zh), h['level'], mm(h['min'], h['max']),
                 h['freq'], h['dps'], h['dps'] / rep['dps'] * 100 if rep['dps'] else 0))
    print('  %-12s %s 合计每秒伤害 %.0f' % ('合计', ' ' * 44, rep['dps']))
    _vs = rep.get('vs') or {}
    if _vs.get('rows'):
        from . import rr as _RRM
        _tgt = ((_vs.get('enemy') or {}).get('zh') if _vs.get('per_bucket')
                else '%s 敌方 %.0f%%' % (_vs.get('profile_zh') or _vs.get('profile'),
                                         _vs.get('enemy_res') or 0))
        print('  %-12s %s 实战 %.0f（×%.2f ｜ %s ｜ 减抗 %s）'
              % ('实战', ' ' * 4, _vs['dps_vs'], _vs['mult_overall'], _tgt,
                 _RRM.describe(rep.get('rr') or {})))
    print('  中文报告已落盘：%s' % out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
