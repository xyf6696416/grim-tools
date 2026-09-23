# -*- coding: utf-8 -*-
"""GrimTools 物品库 (itemdb.js) 解析 + 中文名映射 + 槽位分类

数据源：
  gt_data/itemdb.js   —— GrimTools 官方 DB 导出，含全部 8612 件物品的完整数值
  gd_text/text_zh/    —— 游戏自带 Text_ZH.arc 解包出的中文标签

用法：
    python gd_gear.py                  # 概览
    python gd_gear.py 30 38            # 列出等级要求 30..38 的各槽位候选
"""
import hashlib
import json
import marshal
import os
import re
import sys

from . import _timing as TM

from . import paths as _PATHS

from . import gear as gd_gear


# 旧脚本用 HERE 拼数据文件路径；新架构下数据在技能的 data/ 里
HERE = str(_PATHS.DATA_DIR)
_CACHE_ROOT = str(_PATHS.CACHE_DIR)
_PLANS = str(_PATHS.CACHE_DIR / 'plans')

try:
    from . import _legacyenv as _ENV
    ITEMDB = _ENV.data_file('itemdb.js') or os.path.join(HERE, 'itemdb.js')
    TEXTDIR = _ENV.text_dir() or os.path.join(HERE, 'gd_text', 'text_zh')
except Exception:                       # 环境层不可用时退回脚本目录
    ITEMDB = os.path.join(HERE, 'itemdb.js')
    TEXTDIR = os.path.join(HERE, 'gd_text', 'text_zh')


# ------------------------------------------------------------------ 解析缓存
# ★ 性能：itemdb.js 8.7 MB 每次进程都要重新正则解析（实测 2.53 s）。
#   解析结果用 marshal 落盘，以「源文件路径 + 大小 + mtime」做失效键。
#   实测 2.53 s → 0.10 s，反序列化结果与原对象 == 相等。
_ITEMS_CACHE = None


_TAGS_CACHE = None


_CACHE_VER = 1                          # 解析器有改动时 +1，强制全局失效
_CACHE_DIR = _CACHE_ROOT


def _cache_key(paths):
    """失效键 = 解析器版本 + 每个源文件的 (规范化绝对路径, 大小, mtime_ns)

    ⚠ 路径必须先 normcase(abspath())：
      Windows 上同一个文件可能被写成 `D:\\...` 或 `d:\\...`（取决于从 cwd 还是
      从 sys.path 里的字符串推导），不规范化就会算出两个不同的键 ——
      缓存静默失效，且极难察觉（实测：cwd=scripts 与 cwd=项目根 得到两个不同的键）。
    """
    h = hashlib.sha1()
    h.update(b'v%d' % _CACHE_VER)
    for p in paths:
        try:
            q = os.path.normcase(os.path.abspath(p))
            st = os.stat(q)
            h.update(('%s|%d|%d;' % (q, st.st_size, st.st_mtime_ns)).encode('utf-8', 'replace'))
        except OSError:
            h.update(('missing:%s;' % os.path.normcase(os.path.abspath(p))).encode('utf-8', 'replace'))
    return h.hexdigest()[:16]


def _cache_load(name, key):
    try:
        with open(os.path.join(_CACHE_DIR, '%s.%s.marshal' % (name, key)), 'rb') as f:
            return marshal.load(f)
    except Exception:
        return None


def _cache_save(name, key, obj):
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
        p = os.path.join(_CACHE_DIR, '%s.%s.marshal' % (name, key))
        tmp = '%s.tmp%d' % (p, os.getpid())
        with open(tmp, 'wb') as f:
            marshal.dump(obj, f)
        os.replace(tmp, p)
    except Exception:
        pass                            # 缓存写失败不影响正常流程


# ------------------------------------------------------------------ 中文标签
def _text_files():
    """gd_text/text_zh 下全部 .txt —— 保持 os.walk 原顺序

    顺序不能变：原实现用「先到先得」（`if k not in tab`）处理重复 tag，
    排序会改变谁赢，进而改变中文名结果。
    """
    out = []
    for root, _dirs, files in os.walk(TEXTDIR):
        for fn in files:
            if fn.endswith('.txt'):
                out.append(os.path.join(root, fn))
    return out


def ensure_text():
    """中文字典缺失时自动补齐 —— 用纯 Python 读 Text_ZH.arc，不需要 ArchiveTool.exe

    以前这一步是手工执行 `ArchiveTool.exe Text_ZH.arc -extract <工作目录>/gd_text`，
    换机器就得重来一遍。gd_arc 已把 .arc 格式完整破译（见 gd_arc.py 顶部注释），
    这里做一次性自动解包（之后存在 tags_items.txt 就直接跳过）。
    """
    if not TEXTDIR or os.path.exists(os.path.join(TEXTDIR, 'tags_items.txt')):
        return False
    try:
        import gd_arc
    except ImportError:
        return False
    arc = gd_arc.find_text_arc('ZH')
    if not arc:
        return False
    with TM.span('gear:解包中文文本(纯Python)'):
        gd_arc.ensure_text(arc, TEXTDIR)
    return True


def load_tags():
    """语言包 {tag: 中文} —— 直接取离线库的 l10n（13 语言，中文 16563 条）。

    ★ 取代旧实现：旧的要先解包游戏 Text_ZH.arc 再逐文件解码，
      新架构下这份数据本来就在离线库里，且更全。
    """
    global _TAGS_CACHE
    if _TAGS_CACHE is not None:
        return _TAGS_CACHE
    from . import DB
    _TAGS_CACHE = dict(DB.load().l10n.texts.get("zh") or {})
    return _TAGS_CACHE


# ------------------------------------------------------------------ itemdb
def _obj_at(js, i):
    """从 js[i] == '{' 起按花括号配平取整个对象文本"""
    d, ins, esc, k = 0, False, False, i
    while k < len(js):
        c = js[k]
        if ins:
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                ins = False
        else:
            if c == '"':
                ins = True
            elif c == '{':
                d += 1
            elif c == '}':
                d -= 1
                if d == 0:
                    return js[i:k + 1]
        k += 1
    return js[i:]


def obj_of(js, key):
    """精确取出 "key":{...} 对象文本（key 可带/不带引号）"""
    for pat in ('"%s":{' % key, '%s:{' % key):
        m = re.search(re.escape(pat), js)
        if m:
            return _obj_at(js, m.end() - 1)
    return ''


# 注意 `\s*` 不可省：GT 的 JS 里存在 `,\n f:"Legendary"` 这类**换行缩进**的字段分隔，
# 旧正则（`[,{]` 后直接跟键名）会把这类字段整段丢弃 —— 实测 it8036 的 f/多个数值字段全丢，
# 导致稀有度判断、抗性数值统计统统偏低。见 SKILL.md 陷阱 #39。
_STR = re.compile(r'(?:^|[,{])\s*([A-Za-z_][A-Za-z0-9_]*):"((?:[^"\\]|\\.)*)"')
_NUM = re.compile(r'(?:^|[,{])\s*([A-Za-z_][A-Za-z0-9_]*):(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)')


def parse_obj(seg):
    """对象文本 -> {字段: 值}；字符串字段 str，数值字段 float/int"""
    d = {}
    for m in _STR.finditer(seg):
        d[m.group(1)] = m.group(2).encode().decode('unicode_escape', 'replace')
    for m in _NUM.finditer(seg):
        v = m.group(2)
        try:
            d[m.group(1)] = int(v) if re.fullmatch(r'-?\d+', v) else float(v)
        except ValueError:
            pass
    return d


def _parse_items(path):
    js = open(path, encoding='utf-8', errors='replace').read()
    out = {}
    for m in re.finditer(r'(?<![\w$])((?:it|pre|suf|sk|bs)\d+):\{', js):
        key = m.group(1)
        if key in out:
            continue
        out[key] = parse_obj(_obj_at(js, m.end() - 1))
    return out


def load_items(path=None):
    """物品库 {gt_id: {字段: 值}} —— 直接读离线数据库。

    ★ 取代旧的 itemdb.js 正则解析：离线库的解析器能拿到**全部**字段
      （含嵌套对象/数组），而旧正则只取字符串与数值。
      返回值形状保持一致（调用方只按字段名取）。
      path 参数保留仅为兼容旧签名，已忽略。
    """
    global _ITEMS_CACHE
    if _ITEMS_CACHE is not None:
        return _ITEMS_CACHE
    from . import DB
    db = DB.load()
    _ITEMS_CACHE = {}
    _ITEMS_CACHE.update(db.items)
    _ITEMS_CACHE.update(db.prefixes)
    _ITEMS_CACHE.update(db.suffixes)
    return _ITEMS_CACHE


# ------------------------------------------------------------------ 槽位分类
SLOT_RULES = [
    ('头部',     r'^items/gearhead/'),
    ('项链',     r'^items/gearaccessories/necklaces/'),
    ('胸甲',     r'^items/geartorso/'),
    ('腿甲',     r'^items/gearlegs/'),
    ('靴子',     r'^items/gearfeet/'),
    ('手套',     r'^items/gearhands/'),
    ('戒指',     r'^items/gearaccessories/rings/'),
    ('腰带',     r'^items/gearaccessories/waist/'),
    ('肩甲',     r'^items/gearshoulders/'),
    ('勋章',     r'^items/gearaccessories/medals/'),
    ('圣物',     r'^items/gearrelics?/'),
    ('剑',       r'^items/gearweapons/swords1h/'),
    ('斧',       r'^items/gearweapons/axe1h/'),
    ('锤',       r'^items/gearweapons/blunt1h/'),
    ('巨大武器', r'^items/gearweapons/swords2h/'),
    ('盾',       r'^items/gearweapons/shields/'),
    ('组件',     r'^items/materia/'),
    ('附魔',     r'^items/enchants/'),
]


def slot_of(bitmap):
    b = bitmap or ''
    for name, pat in SLOT_RULES:
        if re.search(pat, b):
            return name
    return None


def item_to_record(bitmap):
    """GT 位图 -> 记录路径候选基名（不含变体字母消歧）"""
    p = bitmap[:-4] if bitmap.endswith('.png') else bitmap
    p = p.replace('/bitmaps/', '/')
    return 'records/' + p + '.dbr'


# ------------------------------------------------------------------ 统计字段
OFF_KEYS = [k for k in (
    'offensivePhysicalMin', 'offensivePhysicalMax', 'offensivePierceMin',
    'offensivePierceMax', 'offensiveFireMin', 'offensiveColdMin',
    'offensiveLightningMin', 'offensivePoisonMin', 'offensiveVitalityMin',
    'offensiveAetherMin', 'offensiveChaosMin', 'offensiveBleedingMin',
    'offensiveSlowPhysicalMin', 'characterOffensiveAbility',
    'offensivePierceRatioMin', 'offensiveTotalDamageModifier',
    'offensiveCritDamageModifier', 'characterAttackSpeed',
    'characterTotalSpeedModifier',
)]
DEF_KEYS = [k for k in (
    'defensiveProtection', 'characterLife', 'characterDefensiveAbility',
    'defensiveFire', 'defensiveCold', 'defensiveLightning', 'defensivePoison',
    'defensivePierce', 'defensiveBleeding', 'defensiveAether',
    'defensiveChaos', 'defensiveVitality', 'defensiveElemental',
    'defensiveStun', 'defensiveFreeze', 'defensiveSlow', 'defensivePetrify',
    'defensiveTrap', 'defensiveKnockdown', 'defensiveSleep',
    'characterLifeRegen', 'characterManaRegen', 'characterLifeModifier',
    'defensiveAbsorptionModifier', 'defensiveBlockAmountModifier',
)]

RESIST_KEYS = ['defensiveFire', 'defensiveCold', 'defensiveLightning',
               'defensivePoison', 'defensivePierce', 'defensiveBleeding',
               'defensiveAether', 'defensiveChaos', 'defensiveVitality']

RESIST_ZH = {'defensiveFire': '火抗', 'defensiveCold': '冰抗',
             'defensiveLightning': '电抗', 'defensivePoison': '毒酸抗',
             'defensivePierce': '穿刺抗', 'defensiveBleeding': '流血抗',
             'defensiveAether': '以太抗', 'defensiveChaos': '混乱抗',
             'defensiveVitality': '活力抗'}


def main():
    items = load_items()
    tags = load_tags()
    its = {k: v for k, v in items.items() if k.startswith('it')}
    print('物品 %d 个, 词缀 pre %d / suf %d, 技能 sk %d'
          % (len(its), sum(1 for k in items if k.startswith('pre')),
             sum(1 for k in items if k.startswith('suf')),
             sum(1 for k in items if k.startswith('sk'))))
    print('中文标签 %d 条' % len(tags))
    print()

    import collections
    rar = collections.Counter()
    sl = collections.Counter()
    for gid, o in its.items():
        rar[o.get('f', '-')] += 1
        s = slot_of(o.get('n', ''))
        sl[s or '(其他)'] += 1
    print('稀有度:', rar.most_common())
    print()
    print('槽位分布:')
    for k, v in sl.most_common(30):
        print('   %-10s %d' % (k, v))
    print()

    # 等级要求分布（只看防具槽）
    ARMOR = ('头部', '项链', '胸甲', '腿甲', '靴子', '手套', '戒指', '腰带', '肩甲', '勋章', '圣物')
    lv = collections.Counter()
    for gid, o in its.items():
        if slot_of(o.get('n', '')) in ARMOR:
            lv[o.get('k')] += 1
    print('防具槽的等级要求分布:', sorted(lv.items(), key=lambda x: (x[0] is None, x[0])))

    # 字段字典
    keys = collections.Counter()
    for gid, o in its.items():
        for k in o:
            keys[k] += 1
    print()
    print('字段频次 Top45:', keys.most_common(45))

    if len(sys.argv) >= 3:
        lo, hi = int(sys.argv[1]), int(sys.argv[2])
        print()
        print('=' * 78)
        print('等级要求 %d..%d 的防具候选' % (lo, hi))
        for name in ARMOR:
            rows = []
            for gid, o in its.items():
                if slot_of(o.get('n', '')) != name:
                    continue
                k = o.get('k')
                if k is None or not (lo <= k <= hi):
                    continue
                rows.append((gid, o))
            rows.sort(key=lambda x: (-(x[1].get('k') or 0), x[1].get('a') or ''))
            print()
            print('--- %s : %d 件' % (name, len(rows)))
            for gid, o in rows[:40]:
                tag = o.get('a') or ''
                zh = tags.get(tag, '')
                print('   %-8s lv%-3s %-9s %-22s %s' % (
                    gid, o.get('k'), o.get('f', ''), zh[:22], o.get('n', '').split('/')[-1]))


if __name__ == '__main__':
    main()
