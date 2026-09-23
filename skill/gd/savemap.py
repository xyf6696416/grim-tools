# -*- coding: utf-8 -*-
"""★ 统一映射库 —— GT 物品/组件/词缀 → 游戏记录路径

这是**唯一权威实现**。此前映射规则散落在 gd_resolve2 / gd_plan2 / gd_plan3 /
gd_final2 / gd_mixed_pick / gd_wpn_comp / gd_tag_rule 等 7 个脚本里，
正则与目录表有三套不一致的版本 —— 这是历史上"改完装备数值不对"的根源。

规则（实测校准，含决定性验证）：
  护甲/饰品  tag[GDX<n>]<Type><A-F><num>
             → records/items/<目录>/<小写字母><num补3>_<槽位小写>.dbr
  武器      tag[GDX<n>]Weapon<Sword|Axe|Blunt|Dagger|...><A|B|C><num>
             → records/items/gearweapons/<目录>/<小写字母><num补3>_<类型小写>.dbr
  组件      component_<a|b><num>_<name>_<x>.png → records/items/materia/comp<a|b>_<name>.dbr
  词缀      tag<Prefix|Suffix><家族>  → 同 tag 家族内 GT id 升序 ↔ 文件在池中顺序

多档定位（关键）：
  同一位图/标签在 GT 里可能有 N 个等级档，库里对应 M 条记录。
  **N == M 时**：GT 条目按 k 升序 ↔ 记录名按字典序一一对应。

用法：
    python gd_map.py selftest        # 用本机存档做回归自证
    python gd_map.py resolve it1725  # 解析单个 GT 物品
"""
import json
import os
import re
import sys

from . import _legacyenv as ENV
from . import paths as _PATHS
from . import _timing as TM

ARMDIR = {'Head': 'gearhead', 'Torso': 'geartorso', 'Legs': 'gearlegs',
          'Feet': 'gearfeet', 'Hands': 'gearhands', 'Shoulder': 'gearshoulders',
          'Shoulders': 'gearshoulders', 'Necklace': 'gearaccessories/necklaces',
          'Ring': 'gearaccessories/rings', 'Waist': 'gearaccessories/waist',
          'Medal': 'gearaccessories/medals', 'Relic': 'gearrelic'}
# GT 标签里的武器类型 → 库里真实目录（注意：库内只有 9 个目录，
# 双手近战统一在 melee2h；匕首与法杖共用 caster）
WPDIR = {'Sword': 'swords1h', 'Sword2h': 'melee2h', 'Axe': 'axes1h', 'Axe2h': 'melee2h',
         'Blunt': 'hammers1h', 'Blunt2h': 'melee2h', 'Mace': 'hammers1h',
         'Dagger': 'caster', 'Caster1h': 'caster', 'Scepter': 'caster',
         'Staff2h': 'caster', 'Caster2h': 'caster',
         'Gun1h': 'guns1h', 'Pistol': 'guns1h', 'Gun2h': 'guns2h', 'Rifle': 'guns2h',
         'Crossbow2h': 'guns2h', 'Crossbow': 'guns2h',
         'Shield': 'shields', 'Offhand': 'focus', 'Focus': 'focus'}
ARMTAG = re.compile(r'^tag(?:GDX\d)?([A-Za-z]+?)([ABCDEF])(\d+)$')
WPTAG = re.compile(r'^tag(?:GDX\d)?Weapon([A-Za-z0-9]+?)([ABCD])(\d+)$')
COMPBM = re.compile(r'component_([ab])(\d+)_([a-z0-9]+)_([ab])')
# 记录名：<字母><3位数字><可选变体字母>_<槽位>   或  <字母><2~3位数字>_<槽位><2位序号>
RECFMT = 'records/items/%s/%s%s_%s.dbr'
RECFMT2 = 'records/items/%s/%s%s%s_%s.dbr'          # 带变体字母

_POOL = None            # set(全部 records/*.dbr)
_BY_STEM = None         # 记录基名 -> [完整路径]
_ARZ = None
# ★ 性能索引（见 §性能）：把原先 O(N) 的全表扫描换成 O(1) 查表。
#   selftest 单次 178.8s → 0.10s（1789×），结果逐位不变。
_FAMILY = None          # '<家族基名>' -> [完整记录名]
_TAG_IDX = None         # tag      -> [(gt_id, 条目)]
_BMP_IDX = None         # 位图      -> [(gt_id, 条目)]


# ------------------------------------------------------------------ 加载
def arz_objects():
    """按需加载全部 .arz（惰性 + 缓存）

    ⚠ 新架构**不再解析 .arz**（gd_arz 已随旧技能封存）。
    记录池改由随技能发布的 data/arz_*.pkl 提供（见 _disk_cache）。
    """
    global _ARZ
    if _ARZ is None:
        raise RuntimeError(
            "记录池缓存缺失，且新架构不含 .arz 解析器。\n"
            "请从归档（skill_archive/grim-dawn-save/scripts/gt_data/）取回\n"
            "  arz_pool.pkl / arz_by_stem.pkl / arz_family.pkl\n"
            "放进本技能 data/ 目录。"
        )
    return _ARZ


def pool():
    global _POOL
    if _POOL is None:
        if _NOCACHE:
            with TM.span('map:pool()'):
                _POOL = _build_pool()
        else:
            with TM.span('map:pool(落盘缓存)'):
                _POOL = _disk_cache('pool', _build_pool)
    return _POOL


# ------------------------------------------------------------------ 落盘缓存
# 4 个 .arz 每次进程启动都要解压 + 扫字符串池（83,762 条记录），
# 而 `pool()` / `by_stem()` / `family_index()` 的结果只跟 .arz 文件本身有关。
# 这三个索引在 gd_verify / gd_lint / gd_build / gd_plan_export / gd_opt 里都会被建，
# 每个进程白花 0.5~0.9 s。落盘缓存后首次 ~0.3 s、之后 ~0.05 s。
_CACHE_DIR = str(_PATHS.DATA_DIR)
_NOCACHE = bool(os.environ.get('GD_NOCACHE'))
_VERBOSE = bool(os.environ.get('GD_VERBOSE'))
_POOL_ORDER = None      # 记录名（按库内顺序）


def _arz_signature():
    """全部 .arz 的 (标签, 路径, mtime, 大小) —— 任一变化即失效"""
    sig = []
    for tag, path in ENV.find_arz():
        try:
            st = os.stat(path)
            sig.append((tag, path, int(st.st_mtime), st.st_size))
        except OSError:
            sig.append((tag, path, 0, 0))
    return sig


def _disk_cache(name, build):
    """读/写落盘索引。

    ★ 新架构：`data/arz_<name>.pkl` 是**随技能发布的静态数据**，直接采用，
      **不再用 .arz 签名卡失效**（运行时不该依赖 .arz）。
      只有缓存整个缺失时才走 `build()`（那需要 .arz 工具链，已随旧技能封存）。
    """
    import pickle
    p = os.path.join(_CACHE_DIR, 'arz_%s.pkl' % name)
    try:
        with open(p, 'rb') as f:
            blob = pickle.load(f)
        data = blob.get('data')
        if data is not None:
            if _VERBOSE:
                cur = _arz_signature()
                if cur and cur != blob.get('sig'):
                    print('  ⚠ %s 的池缓存是按另一份 .arz 建的（游戏更新过？）'
                          '—— 结果可能对不上，必要时重建。' % os.path.basename(p))
            return data
    except Exception:
        pass
    try:
        data = build()
    except Exception as e:
        raise RuntimeError(
            "记录池索引 data/arz_%s.pkl 缺失，且无法从 .arz 重建。\n"
            "请从归档取回该文件（skill_archive/grim-dawn-save/scripts/gt_data/）。\n"
            "原始错误：%s" % (name, e)
        ) from e
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        with open(p, 'wb') as f:
            pickle.dump({'sig': _arz_signature(), 'data': data}, f, protocol=4)
    except Exception:
        pass
    return data


def _build_pool():
    s = set()
    for a in arz_objects().values():
        s |= set(a._pool_names())
    return s


def by_stem():
    """记录基名 -> [完整记录名]（全局索引，不分目录）"""
    global _BY_STEM
    if _BY_STEM is None:
        def _build():
            out = {}
            for n in pool():
                stem = n.split('/')[-1][:-4] if n.endswith('.dbr') else n.split('/')[-1]
                out.setdefault(stem, []).append(n)
            return out
        if _NOCACHE:
            with TM.span('map:by_stem()'):
                _BY_STEM = _build()
        else:
            with TM.span('map:by_stem(落盘缓存)'):
                _BY_STEM = _disk_cache('by_stem', _build)
    return _BY_STEM


def exists(rec):
    return rec in pool()


# ------------------------------------------------------------------ GT 物品库
_ITEMS = None
_TAGS = None
_TAGCOUNT = None


def gt_items():
    """GT 物品 + 词缀的合并视图 —— 直接读离线数据库（取代旧的 itemdb.js 解析）

    ★ 必须合并：`resolve_affix()` 会拿 `preXXXX`/`sufXXXX` 到这里查 `c` 标签，
      只给 `allItems` 会让词缀解析 100% 失败。
    """
    global _ITEMS
    if _ITEMS is None:
        try:
            from . import DB
            db = DB.load()
            _ITEMS = {}
            _ITEMS.update(db.items)
            _ITEMS.update(db.prefixes)
            _ITEMS.update(db.suffixes)
        except Exception:
            _ITEMS = {}
    return _ITEMS


def tag_count():
    """GT 里每个名标签被几个条目使用（用于多档判定）"""
    global _TAGCOUNT
    if _TAGCOUNT is None:
        _TAGCOUNT = {}
        for g, o in gt_items().items():
            if not g.startswith('it'):
                continue
            t = o.get('a')
            if isinstance(t, str) and t.startswith('tag'):
                _TAGCOUNT[t] = _TAGCOUNT.get(t, 0) + 1
    return _TAGCOUNT


def _entry_index():
    """建 tag / 位图 两个反向索引（惰性 + 缓存）

    ★ 性能：原实现每次调用都遍历全部 17,468 件 GT 物品
      （selftest 里 tag_entries 被调用 6,555 次 → 1.14 亿次迭代、87.5 s）。
      建索引后单次查询 O(1)。
      排序键与顺序与原实现完全一致（稳定排序 + 同一 dict 迭代序）。
    """
    global _TAG_IDX, _BMP_IDX
    if _TAG_IDX is None:
        with TM.span('map:建 tag/位图 索引'):
            ti, bi = {}, {}
            for g, o in gt_items().items():
                if not g.startswith('it'):
                    continue
                ti.setdefault(o.get('a'), []).append((g, o))
                bi.setdefault(o.get('n'), []).append((g, o))
            kk = lambda x: (x[1].get('k') is None, x[1].get('k') or 0)   # noqa: E731
            for d in (ti, bi):
                for k in d:
                    d[k].sort(key=kk)
            _TAG_IDX, _BMP_IDX = ti, bi
    return _TAG_IDX, _BMP_IDX


def bitmap_entries(bmp):
    """GT 里使用该位图的全部条目，按 k 升序（用于无名标签的条目定档）

    ⚠ 返回的是缓存的共享列表，**调用方只读，不要原地修改**。
    """
    return _entry_index()[1].get(bmp, [])


def tag_entries(tag):
    """GT 里使用该标签的全部条目，按 k 升序

    ⚠ 返回的是缓存的共享列表，**调用方只读，不要原地修改**。
    """
    return _entry_index()[0].get(tag, [])


def zh(gt_id_or_obj):
    """取中文名"""
    global _TAGS
    if _TAGS is None:
        try:
            from . import gear as G
            _TAGS = G.load_tags() or {}
        except Exception:
            _TAGS = {}
    o = gt_id_or_obj if isinstance(gt_id_or_obj, dict) else gt_items().get(gt_id_or_obj, {})
    nm = (o.get('d') or '').replace('^k', '').replace('^w', '').replace('^n', '')
    if nm:
        return nm
    for k in ('a', 'c'):
        v = o.get(k)
        if isinstance(v, str) and _TAGS.get(v):
            return _TAGS[v]
    return ''


# ------------------------------------------------------------------ 家族
_STEM_RX = re.compile(r'^([a-z]+\d+)_(.+)$')
_LN_RX = re.compile(r'^[a-z]+\d+$')                    # 家族基名本体，如 b002
_LNL_RX = re.compile(r'^[a-z]+\d+[a-g]$')              # 家族基名 + 变体字母，如 b002c


def _family_keys_of(stem):
    """枚举「家族正则 ^ln[a-g]?_slot$ 会命中该基名」的全部家族键

    ⚠ 必须同时覆盖带变体字母的基名：`b002a_torso` / `b002c_torso` 都要归到 `b002_torso`。
      早期实现图省事，直接用 `_STEM_RX` 对基名分组 —— 变体字母会让 `_STEM_RX`
      整体失配（`[a-z]+\\d+` 后面紧跟的不是 `_`），于是**所有变体全部漏掉**：
      selftest 成功数 6636 → 3829、未映射 239 → 3046。
      正确做法是枚举基名里每个 `_` 分割点，看左半段是 `ln` 还是 `ln+[a-g]`。
    """
    for i, ch in enumerate(stem):
        if ch != '_':
            continue
        left, right = stem[:i], stem[i + 1:]
        if _LN_RX.match(left):
            yield left + '_' + right
        elif _LNL_RX.match(left):
            yield left[:-1] + '_' + right


def family_index():
    """家族键 -> [完整记录名]（惰性 + 缓存）

    ★ 性能：原实现每次调用都遍历全部 73,557 个记录基名做一次正则匹配，
      selftest 里被调用 7,449 次 → **5.4 亿次正则匹配、195 s**。建索引后 O(1)。
    """
    global _FAMILY
    if _FAMILY is None:
        def _build():
            fam = {}
            for s, paths in by_stem().items():
                for k in _family_keys_of(s):
                    fam.setdefault(k, []).extend(paths)
            return fam
        if _NOCACHE:
            with TM.span('map:建家族索引'):
                _FAMILY = _build()
        else:
            with TM.span('map:建家族索引(落盘缓存)'):
                _FAMILY = _disk_cache('family', _build)
    return _FAMILY


def family_by_stem(stem):
    """由记录基名（或位图基名）取整个家族记录，按字典序

    名字形如 `<字母><数字><可选变体字母>_<槽位>`：
        b002_torso / b002b_torso / b002c_torso …      （变体字母插在数字后）
        b012_sword / b012a_sword …                    （位图基名同形）
        f010_head  / f010a_head, f010c_head           （派系装备走同名规则）
    """
    m = _STEM_RX.match(stem or '')
    if not m:
        return []
    ln, slot = m.groups()
    return sorted(set(family_index().get('%s_%s' % (ln, slot), ())))


def arm_prefix(tag):
    """tag → (目录, 家族基名)；失败返回 (None, None)"""
    m = ARMTAG.match(tag or '')
    if not m:
        return None, None
    typ, L, num = m.groups()
    d = ARMDIR.get(typ)
    if not d:
        return None, None
    return d, '%s%s_%s' % (L.lower(), num.rjust(3, '0'), typ.lower())


def wp_prefix(tag):
    """tag → (目录, 家族基名)。GT 类型名归一化后再查表"""
    m = WPTAG.match(tag or '')
    if not m:
        return None, None
    t, L, num = m.groups()
    d = WPDIR.get(t)
    if not d:
        # 归一化：去掉大小写差异后模糊匹配
        low = t.lower()
        for k, v in WPDIR.items():
            if k.lower() == low:
                d = v
                t = k
                break
    if not d:
        return None, None
    # 记录名里的"槽位词"与 GT 类型名未必相同（如 Caster1h → dagger）
    slot = {'Caster1h': 'dagger', 'Scepter': 'scepter', 'Staff2h': 'staff',
            'Caster2h': 'staff', 'Focus': 'focus', 'Offhand': 'focus',
            'Gun1h': 'gun1h', 'Pistol': 'gun1h', 'Gun2h': 'gun2h',
            'Rifle': 'gun2h', 'Crossbow': 'gun2h', 'Crossbow2h': 'gun2h',
            'Shield': 'shield', 'Sword2h': 'sword2h', 'Axe2h': 'axe2h',
            'Blunt2h': 'blunt2h'}.get(t, t.lower())
    return d, '%s%s_%s' % (L.lower(), num.rjust(3, '0'), slot)


# ------------------------------------------------------------------ 核心解析
# ★ 「名标签族名 ≠ 记录基名」的 MI 显式别名（2026-09-17）。
#
# 背景：GT 的 `tag<Type>B<num>` 家族推导出的 stem（如 `b002_ring`）在库里
# **一条记录都匹配不到** —— 因为游戏的记录名叫 `b002_ring_outlawleader.dbr`
# （位图基名 `b03_ring_cronleyring` 也帮不上忙）。不处理的话该槽位判「未映射」，
# 后果是**整件贡献算 0**：实测评估 GT 配装 NOYg1OoV 时，两枚克隆利的印玺
# 全部丢失（抗性/OA/输出都少算一大块），只好人工发现。
#
# 解法：① 显式别名（人工核对过，优先）；② `_family_by_prefix()` 自动兜底。
_ARM_ALIAS = {
    'b002_ring': 'b002_ring_outlawleader',   # 克隆利的印玺（tagRingB002）
}
_PREFIX_FAM_CACHE = {}


def _family_by_prefix(stem):
    """名标签族没命中时的兜底：在全库记录里找以 `<stem>_` 开头的基名。

    唯一候选 → 返回该基名的家族（多候选一律放弃，宁可判未映射也不猜）。
    实测：`b002_ring` → 唯一前缀 `b002_ring_outlawleader`
    → 家族 6 档（b002/b002b…b002f），正好与 GT 的 6 个档位对齐（k 升序取第 2 档 =
    it943，与 §36.4 里 `_Sam` 52 级实穿的 `b002b_ring_outlawleader` 一致）。
    """
    key = stem.lower()
    if key in _PREFIX_FAM_CACHE:
        return _PREFIX_FAM_CACHE[key]
    pre = key + '_'
    cands = set()
    for n in pool():
        b = n.rsplit('/', 1)[-1][:-4]
        if b.startswith(pre):
            cands.add(b)
    out = family_by_stem(next(iter(cands))) if len(cands) == 1 else []
    _PREFIX_FAM_CACHE[key] = out
    return out


_BRIDGE = None


def _bridge():
    """权威桥表（`data/record_map.json`，由 tools/build_exact_map.py 从游戏 .dbr 生成）。

    ★ 有它就别再走规则推导 —— 规则曾漏掉 `c204_sword2h.dbr`
      （记录名 c204，itemNameTag 却是 tagGDX2WeaponMelee2hC202）。
    """
    global _BRIDGE
    if _BRIDGE is None:
        try:
            with open(os.path.join(_CACHE_DIR, 'record_map.json'), encoding='utf-8') as f:
                _BRIDGE = json.load(f)
        except Exception:
            _BRIDGE = {}
    return _BRIDGE


_RESOLVE_MEMO = {}

_GID_BY_OBJ = {}


def _gid_of_obj(o):
    """对象 → gid（按身份索引 `gt_items()`）。

    ★ 为什么必须补：权威快速路径原本只在传入**字符串 gid** 时生效，
      而 `gd_build` 传的是 `gt_items().get(gid)` 取出的**对象** →
      快速路径被跳过、退回规则推导，把 `it15810`（仲冬之忆）解析成
      `c303_necklace.dbr`（其权威记录是 `awakened/.../c308_necklace.dbr`），
      **静默写错了装备**。这里按对象身份反查 gid，让快速路径对两种入参都生效。
    """
    if not _GID_BY_OBJ:
        try:
            for _g, _o in gt_items().items():
                _GID_BY_OBJ[id(_o)] = _g
        except Exception:
            pass
    return _GID_BY_OBJ.get(id(o))


def resolve(gt_obj, verbose=False):
    """GT 物品对象/ID -> (记录名, 依据)。

    顺序：① 权威桥表（O(1)，来自游戏 .dbr） ② 旧规则推导（兜底）
    """
    gid = gt_obj if isinstance(gt_obj, str) else _gid_of_obj(gt_obj)
    if gid is not None and gid in _RESOLVE_MEMO:
        return _RESOLVE_MEMO[gid]
    if gid is not None:
        rec = (_bridge().get("fwd") or {}).get(gid)
        if rec:
            out = (rec, "权威映射（读取游戏 .dbr 的 itemNameTag）")
            _RESOLVE_MEMO[gid] = out
            return out
    out = _resolve_by_rules(gt_obj, verbose)
    if gid is not None:
        _RESOLVE_MEMO[gid] = out
    return out


def _resolve_by_rules(gt_obj, verbose=False):

    """GT 物品对象 → (记录名, 依据说明)；无法确定返回 (None, 原因)

    决策顺序：
      1) 名标签家族 + 多档对齐（N 个 GT 档 == M 条库记录）
      1b) 名标签族名 ≠ 记录基名时的别名 / 前缀兜底（见下方 `_ARM_ALIAS`）
      2) 名标签家族唯一记录
      3) 位图家族 + 多档对齐（**GT 条目无名标签时**，如 b012_sword）
      4) 位图基名自带变体字母 → 直接对应
    """
    if isinstance(gt_obj, str):
        gt_obj = gt_items().get(gt_obj, {})
    o = gt_obj or {}
    tag = o.get('a') or ''
    if not tag:
        dv = o.get('d') or ''
        if isinstance(dv, str) and dv.startswith('tag'):
            tag = dv                       # 部分物品（如圣物）把标签放在 d 字段
    bmp = o.get('n') or ''
    base = bmp.split('/')[-1][:-4] if bmp else ''

    # ---- 附魔走专属规则（位图被同一派系大量复用，家族规则全失效）
    if '/enchants/' in bmp or tag.startswith('tagEnchant'):
        return resolve_enchant(o)

    # ---- 组件走专属规则
    if 'craftingparts/components' in bmp:
        return resolve_comp(o)

    def _pick(recs, why_prefix, bmp_key=None):
        """在家族记录里按 k 升序定位自己"""
        entries = tag_entries(tag) if tag else []
        if not entries:
            entries = bitmap_entries(bmp)
        k = o.get('k')
        idx = 0
        if entries:
            keys = [e[1].get('k') for e in entries]
            if k in keys:
                idx = keys.index(k)
        if len(entries) == len(recs):
            return (recs[min(idx, len(recs) - 1)],
                    '%s：GT %d 档 == 库 %d 条记录，按 k 升序取第 %d 档'
                    % (why_prefix, len(entries), len(recs), idx + 1))
        if len(recs) == 1:
            return recs[0], '%s：库内仅 1 条记录' % why_prefix
        if idx < len(recs):
            return (recs[idx], '⚠ 低置信：%s 档数不吻合（GT %d vs 库 %d），'
                    '暂按 k 升序取第 %d 档' % (why_prefix, len(entries), len(recs), idx + 1))
        return None, None

    # ---- 1) 名标签家族（武器优先，避免 Sword/Blunt 被护甲规则误吃）
    d, stem = wp_prefix(tag)
    kind = '武器'
    if not stem:
        d, stem = arm_prefix(tag)
        kind = '护甲/饰品'
    if stem:
        recs = family_by_stem(stem)
        if not recs:
            alt = _ARM_ALIAS.get(stem.lower())
            if alt:
                recs = family_by_stem(alt)
                if recs:
                    rec, why = _pick(recs, '%s 标签族别名 %s→%s' % (kind, stem, alt))
                    if rec:
                        return rec, why
            if not recs:
                recs = _family_by_prefix(stem)
                if recs:
                    rec, why = _pick(recs, '%s 标签族前缀兜底 %s' % (kind, stem))
                    if rec:
                        return rec, why
        if recs:
            rec, why = _pick(recs, '%s 标签族 %s' % (kind, stem))
            if rec:
                return rec, why

    # ---- 3) 位图家族（GT 条目无名标签时）
    if base:
        recs = family_by_stem(base)
        if not recs:
            m2 = re.match(r'^(.*?)\d+$', base)      # c013_dagger001 → c013_dagger
            if m2:
                recs = family_by_stem(m2.group(1))
        if recs:
            rec, why = _pick(recs, '位图族 %s' % base)
            if rec:
                return rec, why
        # ---- 4) 位图基名自带变体字母 → 直接对应
        if re.search(r'[a-g]$', base) and base in by_stem():
            return by_stem()[base][0], '位图基名自带变体字母（%s）' % base
        # ---- 位图基名无字母、且无家族 → 弱提示
        if base in by_stem():
            return (by_stem()[base][0],
                    '⚠ 低置信：仅按位图基名匹配（%s）。位图会被多件装备复用，请人工复核' % base)
    return None, '未映射：tag=%r 位图=%r' % (tag, base)


def resolve_enchant(gt_obj):
    """附魔（augment）→ 记录名

    GT 标签：`tagEnchant<A|B|C><索引 2~3 位><变体字母>`
    游戏记录：`records/items/enchants/<小写前缀><索引><小写变体>_enchant.dbr`

    - 常规集索引**补零到 2 位**：`tagEnchantA017A` → `a17a_enchant.dbr`
    - GDX1 另有 3~5 位零填充的变体集（`a0000a_enchant` 等），故逐个宽度试
    - **不能**用位图家族规则：位图（如 `enchantf_blue`）被同一派系的几十条附魔复用
    实测 109/109 命中（2026-09-15）
    """
    if isinstance(gt_obj, str):
        gt_obj = gt_items().get(gt_obj, {})
    o = gt_obj or {}
    tag = o.get('d') or o.get('a') or ''

    # ---- 符文（GDX2+，贴在勋章上）：tagGDX2RuneB201 -> enchants/runes/b201_rune.dbr
    m = re.match(r'^tag(?:GDX\d)?Rune([A-Za-z])(\d+)$', str(tag))
    if m:
        p = 'records/items/enchants/runes/%s%d_rune.dbr' % (m.group(1).lower(), int(m.group(2)))
        if exists(p):
            return p, '符文标签规则（%s）' % p.split('/')[-1]
        return None, '符文记录不存在（%s）' % p

    # ★ 资料片标签带前缀：`tagGDX2EnchantA218A` / `tagGDX3EnchantA309A`（2026-09-17 实测）。
    #   旧正则 `^tagEnchant…` 只认基础版，GDX2/GDX3 附魔一律判「格式不符」→
    #   方案导出时这些附魔被整条丢弃（65 级方案里 项链/戒指 的附魔就是这么没的）。
    m = re.match(r'^tag(?:GDX\d)?Enchant([A-Z])(\d{2,3})([A-Z])$', str(tag))
    if not m:
        return None, '附魔标签格式不符 %r' % tag
    pref, idx, var = m.group(1).lower(), int(m.group(2)), m.group(3).lower()
    for w in (2, 3, 4, 5, 6):
        c = 'records/items/enchants/%s%0*d%s_enchant.dbr' % (pref, w, idx, var)
        if exists(c):
            return c, '附魔标签规则（%s）' % c.split('/')[-1]
    return None, '附魔记录不存在（%s%d%s 各零填充宽度均未命中）' % (pref, idx, var)


# 组件位图名 ≠ 记录名 的已知例外（GT 用美术名，游戏用另一套命名）。
# 只收录**能通过中文名对上号**的确定项；拿不准的一律不猜 —— 猜错 = 镶错组件。
# （gd_opt.auto_comp_pool 会把剩余无法解析的组件直接剔出候选池。）
COMP_ALIAS = {
    'taintedsoul': 'compb_kilriansoul',          # 基尔利安的碎裂之魂
    'hellsbaneammo': 'compa_hellbaneammo',        # 拼写差一个 s
    'spellwoventhread': 'compb_spellwoventhreads',  # 单复数
    'oleronsblood': 'compb_oleronblood',          # 所有格
    'bindingsofbysmeil': 'compb_bindingsofbysmiel',  # bysmeil/bysmiel
    'ancientarmor': 'compb_ancientarmorplate',    # 古代铠甲片
    'markofmyrmidon': 'compb_markofthemyrmidon',  # 蚁族标记
    'enchantedflint': 'compa_flint',              # 附魔燧石
    'attunedlodestone': 'compb_lodestone',        # 调谐磁石
    'shardofberonath': 'compb_beronath',          # 贝罗纳斯碎片
    'vitriolicgallstone': 'compa_gallstone',      # 硫酸胆石
}


def resolve_comp(gt_obj):
    """组件 → 记录名"""
    if isinstance(gt_obj, str):
        gt_obj = gt_items().get(gt_obj, {})
    o = gt_obj or {}
    b = (o.get('n') or '').split('/')[-1][:-4]
    m = COMPBM.fullmatch(b)
    if not m:
        return None, '位图格式不符 %r' % b
    letter, _num, name, post = m.groups()
    cands = []
    al = COMP_ALIAS.get(name)
    if al:
        cands.append('records/items/materia/%s.dbr' % al)
    for L in (letter, post, 'a', 'b'):          # GDX1 组件的字母不严格对应
        c = 'records/items/materia/comp%s_%s.dbr' % (L, name)
        if c not in cands:
            cands.append(c)
    for c in cands:
        if exists(c):
            return c, '组件位图规则（字母 %s）%s' % (c.split('/')[-1][4],
                                                    '＋别名表' if al else '')
    return None, '记录不存在（试过 %s）' % ' / '.join(x.split('/')[-1] for x in cands)


def _affix_family_name(kind, tag):
    """（保留给归档脚本用的旧接口，已被 _affix_stem 取代）"""
    s = re.sub(r'^tag', '', tag or '', flags=re.I)
    s = re.sub(r'^GDX\d', '', s, flags=re.I)
    m = re.match(r'(?i)(?:Prefix|Suffix)(.+)$', s)
    if not m:
        return None, None
    return ('prefix' if kind == 'prefix' else 'suffix'), m.group(1).lower()


def _num(x):
    """从 'pre3413' / 'suf4931' / 'it1725' 里取出数字部分"""
    d = re.sub(r'\D', '', x)
    return int(d) if d else 0


_AFFIX = {}


def _affix_stem(tag):
    """词缀 tag → 库内文件名族根（**2026-09-16 实测破译**）

    两套命名并存：

    | tag 形态 | 族根 | 库内文件 | 档位怎么排 |
    |---|---|---|---|
    | `tagSuffixA018` / `tagPrefixAO003` / `tagGDX1PrefixB012` | `a018` / `ao003` / `b012` | `a018a_ch_oa_06.dbr` / `ao003a_pierce_01.dbr` | **按文件名升序**就是 k 升序 |
    | `tagPrefixB024_Ar_A` / `tagGDX3SuffixB308_Ar` | `b_ar024` / `b_ar308` | `b_ar024_ar.dbr, _b, _c…` | 末尾单字母 = 档位（无字母 < a < b < c…） |

    ⚠ 旧实现只认 `^<字母><数字>` 这一种，于是**所有 `B0xx_类别_档位` 形态的词缀全军覆没**
      （实测 6 个候选槽位里 4 个报「家族档数不吻合 GT 6 vs 各库 0」）。
      注意 `B` 族的文件名是 `b_<类别><编号>_<适用位>[档位]`，类别段在**编号前面**。
    """
    s = re.sub(r'^tag', '', tag or '', flags=re.I)
    s = re.sub(r'^GDX\d', '', s, flags=re.I)
    m = re.match(r'(?i)(Prefix|Suffix)(.+)$', s)
    if not m:
        return None
    rest = m.group(2)
    if re.fullmatch(r'[A-Za-z]{1,2}\d+', rest):        # A018 / AO003 / AD014 / AA004
        return rest.lower()
    m2 = re.match(r'^([A-Z])(\d+)_([A-Za-z0-9]+?)(?:_[A-Z])?$', rest)
    if m2:
        return 'b_%s%03d' % (m2.group(3).lower(), int(m2.group(2)))
    return None


# 词缀记录文件名里的「适用位」代码 → 我方槽位
AFFIX_SLOT_CODE = {
    'ar': ('头部', '胸甲', '腿甲', '靴子', '手套', '肩甲', '腰带'),
    'he': ('头部',),
    'je': ('项链', '戒指1', '戒指2', '勋章'),
    'ne': ('项链', '勋章'),
    'ri': ('戒指1', '戒指2'),
    'we': ('主手', '副手'), 'we2h': ('主手', '副手'),
    'sh': ('主手', '副手'), 'fo': ('副手',),
    'wpn': ('主手', '副手'),
}


def _tier_key(name):
    """文件名的档位排序键：末尾单字母（a..f）或附录表后缀（_01/_02）→ 无档位在最前"""
    stem = name.split('/')[-1][:-4]
    parts = stem.split('_')
    tail = parts[-1]
    if len(tail) == 1 and tail.isalpha() and tail.islower():
        return (0, ord(tail) - 97, stem)
    if re.fullmatch(r'\d{1,2}', tail):
        return (0, int(tail), stem)
    return (0, -1, stem)


def _affix_family_files(group, stem):
    """库里属于某族根的全部文件。
       · 族根后必须跟 '_'（b_ar024_ar）或**字母**（ad014a_res_...）——
         用「非数字」而不是「必须是下划线」，否则 aa/ad/ao 两字母族会被整族漏掉
         （实测 `tagPrefixAD014` → 族根 ad014，文件是 `ad014a_res_coldpoison_03.dbr`）。
       · 只对 `b_` 族按档位字母重排；两字母族保持字符串池原有顺序（= 游戏的库内顺序）。
    """
    pat = 'records/items/lootaffixes/%s/%s' % (group, stem)
    out = [t for t in _affix_files(group)
           if t.startswith(pat) and not t[len(pat)].isdigit()]
    if stem.startswith('b_'):
        out.sort(key=_tier_key)
    return out


def resolve_affix(kind, gt_id, slot=None):
    """GT 词缀 ID -> (记录名, 依据)。先查权威桥表，再走规则。"""
    rec = (_bridge().get("affix_fwd") or {}).get(gt_id)
    if rec:
        return rec, "权威映射（读取游戏 .dbr）"
    return _resolve_affix_by_rules(kind, gt_id, slot)


def _resolve_affix_by_rules(kind, gt_id, slot=None):

    """词缀（前缀/后缀）→ 记录名

    规则：**同一 tag 家族内，GT id 升序 ↔ 该族文件按档位升序**。
    两个必须处理的坑：
      ① 资料片变体（条目带 `mods`）挂在同一 tag 下但对应另一批文件 → 先剔除；
      ② 族根可能带多个「适用位」变体（`b_ar024_ar` / `b_ar024_je` …），
         文件数与 GT 档数应当**按适用位分组后**才相等 → 优先取与目标槽位匹配的那组。
    """
    o = gt_items().get(gt_id) or {}
    tag = o.get('c')
    if not tag:
        return None, '该词缀无 c 标签'
    group = 'prefix' if kind in ('pre', 'prefix') else 'suffix'
    stem = _affix_stem(tag)
    if not stem:
        return None, 'tag 形态未识别: %s' % tag

    plain = sorted([g for g, oo in gt_items().items()
                    if oo.get('c') == tag and not oo.get('mods')], key=_num)
    if gt_id not in plain:
        return None, '该 id 属资料片变体（非变体 %d 个）' % len(plain)
    idx = plain.index(gt_id)

    files = _affix_family_files(group, stem)
    if not files:
        return None, '库里没有族根 %s 的文件' % stem
    if not stem.startswith('b_'):
        if len(files) == len(plain):
            return files[idx], '词缀族根 %s（%d 档 · 池内顺序，GT 第 %d 位）' % (
                stem, len(files), idx + 1)
        return None, '族根 %s 有 %d 个文件，GT %d 档，不吻合' % (stem, len(files), len(plain))
    # b_ 族：文件名形如 b_<类别><编号>_<适用位>[档位] → 按适用位分组，取档数吻合的那组
    groups = {}
    for f in files:
        code = f[:-4].split('_')[-1]
        if len(code) == 1 and code.isalpha():
            code = f[:-4].split('_')[-2]
        groups.setdefault(code, []).append(f)
    cand = None
    if slot:
        # ★ 给了槽位就**只认适用位匹配的那组**：宁可解析失败，也不能把武器词缀写到护甲上
        #   （写错的后果是游戏静默不生效，而存档层面完全看不出来）
        for code, fl in groups.items():
            if slot in AFFIX_SLOT_CODE.get(code, ()) and len(fl) == len(plain):
                cand = fl
                break
        if cand is None:
            return None, '族根 %s 无「%s」可用且档数吻合的适用位（各适用位 %s / GT %d）' % (
                stem, slot, {k: len(v) for k, v in groups.items()}, len(plain))
    else:
        for code, fl in groups.items():
            if len(fl) == len(plain):
                cand = fl
                break
    if cand is None:
        return None, '族根 %s 各适用位档数 %s，均不等于 GT %d' % (
            stem, {k: len(v) for k, v in groups.items()}, len(plain))
    return cand[idx], '词缀族根 %s（%d 档，GT 第 %d 位）' % (stem, len(cand), idx + 1)


def _pool_order():
    """全部记录名，**按原始库内顺序**（list）。

    新架构不含 .arz，改用随技能发布的 record_hash.json ——
    它的插入顺序就是 .arz 字符串池顺序，而词缀两字母族依赖这个顺序定档。
    拿不到时退回 `sorted(pool())`（字典序对零填充的文件名通常等价）。
    """
    global _POOL_ORDER
    if _POOL_ORDER is None:
        p = os.path.join(_CACHE_DIR, 'record_hash.json')
        try:
            with open(p, encoding='utf-8') as f:
                _POOL_ORDER = list(json.load(f).values())
        except Exception:
            _POOL_ORDER = sorted(pool())
    return _POOL_ORDER


def _affix_files(kind):
    """按库内顺序取出某类词缀的全部记录名"""
    if kind in _AFFIX:
        return _AFFIX[kind]
    sub = 'prefix' if kind == 'prefix' else 'suffix'
    pat = '/lootaffixes/%s/' % sub
    seen, out = set(), []
    for t in _pool_order():
        if pat in t and t.endswith('.dbr') and t not in seen:
            seen.add(t)
            out.append(t)
    _AFFIX[kind] = out
    return out


# ------------------------------------------------------------------ 自证
def selftest(verbose=True, sample=15):
    """回归自证：对 GT 全库物品跑一遍 resolve()，统计成功率与置信度分布

    这是**最有价值的自检** —— 一次跑完 8000+ 件物品，
    任何规则退化都会立刻体现在成功率上。
    """
    import collections
    it = gt_items()
    if not it:
        print('✗ 未找到 GT 物品库（先跑 gt_fetch.py，或设置 GD_DATA）')
        return 1
    stat = collections.Counter()
    why_stat = collections.Counter()
    fails, lows = [], []
    for g, o in it.items():
        if not g.startswith('it'):
            continue
        n = o.get('n') or ''
        if not n:
            continue
        if any(k in n for k in ('items/misc/', '/bitmaps/misc', 'blueprint_', 'consumable',
                                'rune_', 'enchant', 'itemicons', 'questitem', '/ui/')):
            stat['非装备'] += 1
            continue
        rec, why = resolve(o)
        if not rec:
            stat['未映射'] += 1
            if len(fails) < sample:
                fails.append((g, n.split('/')[-1], o.get('a') or ''))
            continue
        if not exists(rec):
            stat['映射但记录不存在'] += 1
            if len(fails) < sample:
                fails.append((g, n.split('/')[-1], rec))
            continue
        stat['成功'] += 1
        if why.startswith('⚠'):
            stat['其中低置信'] += 1
            if len(lows) < sample:
                lows.append((g, zh(o)[:14], rec.split('/')[-1], why[:64]))
        why_stat[why.split('：')[0].split('（')[0][:30]] += 1

    total = sum(v for k, v in stat.items() if k != '非装备')
    print('=' * 88)
    print('映射库回归自证 —— 对 GT 全库物品跑一遍 resolve()')
    print('=' * 88)
    print('  统计：')
    for k in ('成功', '其中低置信', '未映射', '映射但记录不存在', '非装备'):
        if stat[k]:
            extra = '  (%.1f%%)' % (100.0 * stat[k] / total) if (k == '成功' and total) else ''
            print('     %-20s %6d%s' % (k, stat[k], extra))
    print()
    print('  命中规则分布：')
    for k, v in why_stat.most_common(8):
        print('     %-36s %6d' % (k, v))
    if lows:
        print()
        print('  低置信样例（档数不吻合的家族，需人工复核）:')
        for g, nm, rec, why in lows:
            print('     %-9s %-14s → %-30s %s' % (g, nm, rec, why))
    if fails:
        print()
        print('  失败样例:')
        for g, bmp, extra in fails:
            print('     %-9s 位图=%-34s %s' % (g, bmp, extra))
    print()
    bad = stat['未映射'] + stat['映射但记录不存在']
    print('  结论：高置信 %d / 低置信 %d / 失败 %d'
          % (stat['成功'] - stat['其中低置信'], stat['其中低置信'], bad))
    return 0 if bad == 0 else 1



def main():
    if len(sys.argv) < 2:
        return selftest()
    cmd = sys.argv[1]
    if cmd == 'selftest':
        return selftest()
    if cmd == 'resolve':
        for gid in sys.argv[2:]:
            o = gt_items().get(gid, {})
            rec, why = resolve(o)
            print('%s  %-16s k=%-4s %s' % (gid, zh(o)[:16], o.get('k'), rec or '【未映射】'))
            print('      依据: %s' % why)
        return 0
    if cmd == 'env':
        return ENV.main()
    print('用法: python gd_map.py [selftest|resolve <itXXXX>|env]')
    return 2


if __name__ == '__main__':
    sys.exit(main())
