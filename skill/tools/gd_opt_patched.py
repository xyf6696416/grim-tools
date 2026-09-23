#!/usr/bin/env python3
"""gd_opt.py —— 装备组合优化器（抗性覆盖率 + 输出下限）

用途：给定「每槽位的候选装备/镶嵌/附魔」，用束搜索找一组最优搭配。
     默认目标是「终极难度抗性覆盖率最大化，同时不跌破输出下限」。

用法：
    python gd_opt.py [--max-leg N]   # N=允许的传奇（紫装）件数上限，默认 2；0=纯绿蓝
                                     # 结果写到 _tmp_opt_bal.json（工作目录）

改法：
    · 候选池 POOL_BASE / POOL_WPN / POOL_COMP / POOL_CWPN / JA / WA 直接改
    · 输出下限与权重改 FLOOR / W_PEN
    · 抗性加权改 W_COVER（默认给缺口大的系加权，逼优化器去补）

关键约束（已在代码里）：
    1) 抗性需求按难度算 —— 终极：上排(火/冰/电/毒酸/穿刺) >= 130，下排(流血/活力/虚化/混乱) >= 105
    2) 元素抗与火/冰/电是叠加关系，必须分开累加（见 SKILL.md §30.2）
    3) 每个候选都会做槽位校验（位图目录）与镶嵌限用位置校验，不合法的直接丢弃
    4) 腰带与圣物没有镶嵌位；附魔（augment）整体为 70 级解锁功能，70 级前不注入任何玩家附魔

实测结论（2026-09-15，50 级）：
    9 项全封顶需要 1070 点，而 50 级全部可用槽位上限约 1000~1030 → 不可能全封顶；
    束搜索 700 宽的最好结果约 95.5% 覆盖率（5 项封顶）。
"""
import re, sys, os, json
from collections import defaultdict
from operator import add

from . import savemap as M
from . import _timing as TM

from . import paths as _PATHS

from . import gear as gd_gear


# 旧脚本用 HERE 拼数据文件路径；新架构下数据在技能的 data/ 里
HERE = str(_PATHS.DATA_DIR)
_CACHE_ROOT = str(_PATHS.CACHE_DIR)
_PLANS = str(_PATHS.CACHE_DIR / 'plans')



try:
    import numpy as _np            # 可选：向量化加速，缺失时自动退回纯 Python
except ImportError:
    _np = None

# ★ 必须**拷贝**一份：下面会把解析出的词缀条目注入这个字典，
#   直接改 gd_map 的原表会让 `c`（tag 家族）等字符串字段被覆盖掉，
#   于是 M.resolve_affix() 全部报「该词缀无 c 标签」——方案里的词缀全丢。
_IT = dict(M.gt_items())
TAGS = gd_gear.load_tags()

# 目标等级（= 角色当前/目标等级；低于此等级的件无法装备）
_MAX_ILVL = int(os.environ.get('GD_MAX_ILVL', '52'))

# ★ 目标（goal）必须在候选池构建之前确定 —— auto_pool() 的排序口径随 goal 变化
#   （dmg 目标要的是「输出件」，而不是「抗性件」）。原先 GOAL 在文件末尾才解析，
#   导致 --goal dmg 时池子仍是按抗性筛的，池里根本没有输出装。
GOAL = sys.argv[sys.argv.index('--goal') + 1] if '--goal' in sys.argv else 'worst'
if GOAL not in ('worst', 'cover', 'dmg', 'maxdmg'):
    raise SystemExit('✗ --goal 只能取 worst | cover | dmg | maxdmg')

# ★ 硬覆盖下限（仅 goal=maxdmg 生效）：总覆盖点数不得低于该值。
#   用途：先跑一遍抗性最优拿到「可达最大覆盖」，再用 maxdmg + 这个下限
#   在**不牺牲覆盖**的前提下把输出拉到最高 —— 即「尽可能高抗性 + 保证伤害」。
COVER_MIN = float(os.environ.get('GD_COVER_MIN', '0'))

# ★ 伤害并列项：抗性口径（worst/cover）下追加一个**很小的**输出加权项。
#   作用：在「抗性成绩不变或几乎不变」的众多解里，挑输出最高的那个
#   → 正好对应「尽可能高抗性 + 保证伤害」这个诉求，否则同等抗性下会随机挑到纯坦克解。
DMG_TIE = float(os.environ.get('GD_DMG_TIE', '0'))

# ★ 支配剪枝开关（默认开）。关掉可做对照：GD_PRUNE=0
_PRUNE = os.environ.get('GD_PRUNE', '1') != '0'

# ★ 完全不写任何附魔（含首饰/勋章附魔）：GD_NO_AUG=1
#   用途：用户对「附魔等级门」有疑虑、或不想让装备被 Soulbound 时，出一版零争议方案。
_NO_AUG = os.environ.get('GD_NO_AUG', '0') != '0'

# ★ **溢出惩罚**：抗性超过 NEED（= 在终极难度显示 80% 所需值）的部分**纯属浪费** ——
#   游戏里 80% 是硬上限，超出既不加减伤、也换不来别的属性。
#   权重调大 → 解会更贴 80%（把省的预算换回输出）；0 = 旧行为（不管溢出）。
#   ★ 注意：这一项是**减分项**，所以只会在「覆盖度已经打满」的同一档解之间起作用 ——
#     worst*1000 会把任何跌破 NEED 的方案压下去，不会为了少溢出而牺牲封顶。
OVER_PEN = float(os.environ.get('GD_OVER_PEN', '0'))

# ★ 只优化部分槽位（逗号分隔，取自 SLOTS）。例：GD_SLOTS=头部,项链,…（不动武器槽）
_SLOTS_ENV = os.environ.get('GD_SLOTS', '')

# 附魔（augment）的等级墙 —— **只卡护甲槽**，不是一刀切 70 级。
#
#   实测 GT 全库 376 条附魔按「适用位 × 需求等级 k」分布（2026-09-16）：
#     戒指/项链            k = 40 / 50 / 65 / 90
#     双手武器              k = 40 / 50 / 65 / 90
#     单手武器·盾·法器副手    k = 40 / 50 / 65 / 90
#     勋章                 k = 1 / 33 / 50 / 70 / 90
#     **所有护甲**          k = 70 / 90          ← 70 级墙只在这里
#   即 SKILL §23.9 / 陷阱 #31：50 级角色能用「单手武器 / 双手武器 / 戒指 / 项链 / 勋章」
#   这五类附魔；12 格防具的附魔要等 70 级。
#
#   ⚠ 旧代码写的是「70 级前禁止所有玩家附魔 → augs=[None]」，与它自己下一行的注释矛盾，
#     把 58 级本来可用的首饰/勋章附魔全禁掉了 —— 白白少一大块抗性来源
#     （存档里那枚勋章符文 `b201_rune` 就是「勋章附魔在 70 级前可附着」的实证）。
AUG_UNLOCK_LVL = 70                     # 仅用于「护甲槽」的闸门
AUG_SLOTS_UNDER_70 = ('项链', '戒指1', '戒指2', '勋章', '主手', '副手')




# ================================================================ 词缀（前缀/后缀）建模
# GT 物品库里 preXXXX / sufXXXX 条目自带完整数值，字段名与基础装备一致
# （offensivePierceModifier=%穿刺, offensivePierceMin/Max=平戳穿刺,
#  characterAttackSpeedModifier=攻速, defensivePierce=穿刺抗…），
# 因此可直接复用 contrib()/res_of()/off_of() 计算贡献向量。
# 词缀按 cls（GT 物品类代码，见 calc.js 的 itemClassification 图例）决定可镶嵌槽位，
# 按 k(itemLevel) 决定该等级段能否出现 —— 与基础装备的等级过滤口径一致。
# 注：词缀≠附魔(augment)。附魔 70 级解锁（见 AUG_UNLOCK_LVL）；词缀是装备掉落/铁匠词缀，
#     任何等级都可用，故不受 70 级闸门限制。
_CLS_SLOT = {
    'c10': ['头部'], 'c11': ['肩甲'], 'c12': ['胸甲'], 'c13': ['手套'], 'c14': ['腰带'],
    'c15': ['腿甲'], 'c16': ['靴子'], 'c40': ['戒指1', '戒指2'], 'c41': ['项链'], 'c42': ['勋章'],
    'c45': ['圣物'], 'c43': ['圣物'], 'c26': ['副手'], 'c27': ['副手'],
    'c20': ['主手'], 'c21': ['主手'], 'c22': ['主手'], 'c23': ['主手'], 'c24': ['主手'], 'c25': ['主手'],
    'c28': ['主手'], 'c29': ['主手'], 'c30': ['主手'], 'c31': ['主手'], 'c32': ['主手'], 'c33': ['主手'], 'c34': ['主手'],
}
for _c in ('c20', 'c21', 'c22', 'c23', 'c24', 'c25', 'c28', 'c29', 'c30', 'c31', 'c32', 'c33', 'c34'):
    _CLS_SLOT[_c] = ['主手', '副手']   # 武器词缀双持均可
_AFFIX_CACHE = os.path.join(HERE, 'affix_cache.json')


def _parse_affixes():
    """从 itemdb.js 解析所有 pre/suf 词缀 → {gid: {stat..., 'cls':[...], 'k':int}}，按 mtime 落盘缓存。

    ★★ 2026-09-20 修：原实现只找 `data/itemdb.js` —— 而**真实文件在
       `data/cache/itemdb.js`**（8.7 MB，含 4105 条 pre/suf，全部带完整数值 +
       `cls` 槽位表 + `k` 等级）。路径错了 ⇒ `_AFFIX_DB` 恒为 **0 条** ⇒
       `POOL_PRE/POOL_SUF` 全空 ⇒ **整条链路的词缀维度一直是死的**：
         · `auto_pool` 特意补进来的绿装被当成「裸底材」评估（注释还写着
           「绿装能带 2 条词缀，折叠词缀后战力远高于裸分」——但折叠从未发生）；
         · `compute_best_affixes()` 拿空池算，`BEST_PRE/BEST_SUF` 恒空；
         · 产出的每个方案第 4/5 位都是 `None`（实测 16 份方案全为 0 前缀 0 后缀，
           而存档真实装备是 2~3 前置 + 4 后缀）。
       现在按**候选路径列表**探测，任何一处命中即可 —— 目录再搬家也不会静默失效。
    """
    src = ''
    for _rel in ('itemdb.js', os.path.join('cache', 'itemdb.js'),
                 os.path.join('gt_data', 'itemdb.js')):
        _p = os.path.join(HERE, _rel)
        if os.path.exists(_p):
            src = _p
            break
    if not src:
        return {}
    try:
        mt = os.path.getmtime(src)
        if os.path.exists(_AFFIX_CACHE):
            try:
                _c = json.load(open(_AFFIX_CACHE, encoding='utf-8'))
                if _c.get('_mtime') == mt:
                    return {k: v for k, v in _c.items() if k != '_mtime'}
            except Exception:
                pass
    except Exception:
        pass
    txt = open(src, encoding='utf-8', errors='ignore').read()
    out = {}
    _num = re.compile(r'^[+-]?(\d+\.?\d*|\.\d+)$')
    for m in re.finditer(r'(pre\d+|suf\d+):\{', txt):
        gid = m.group(1)
        i = m.end() - 1            # '{' 的位置
        depth = 0
        j = i
        while j < len(txt):
            ch = txt[j]
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    break
            j += 1
        body = txt[i + 1:j]
        d = {'cls': [], 'k': 0}
        # ★★ 2026-09-20 修 `cls` 解析（两条独立缺陷，都会让词缀**静默丢出池子**）：
        #   ① `cls` 在 itemdb.js 里有**两种写法**：
        #        · JSON 数组      `cls:["c12","c24"]`
        #        · 字符串 + split `cls:"c20 c21 c24".split(" ")`
        #      旧实现只认第一种；第二种被当作普通字符串存下 ⇒ `_affix_slots()` 对字符串
        #      逐**字符**遍历（`'c'`/`'2'`/`' '`… 都不是 `_CLS_SLOT` 的键）⇒ 该词缀
        #      **一条槽位都映射不到**。实测后果：`POOL_SUF['主手']` 里含 `c24`（施法匕首）
        #      的后缀 **0 条**，而库里实际有 314 条 ⇒ **施法武器的后缀选择权根本不存在**。
        #   ② `if vv.startswith('[')` 原来**对任何数组字段**都往 `d['cls']` 写
        #      （如 `augmentSkillName:[…]` 会把 cls 覆盖成它里面出现的 c 码）。
        #      现在只在 `kk == 'cls'` 时写，其它数组字段按原样保留。
        for kk, vv in re.findall(r'([a-zA-Z][a-zA-Z0-9_]*)\s*:\s*("[^"]*"|\[[^\]]*\]|[^,}]+)', body):
            vv = vv.strip()
            if vv.startswith('['):
                if kk == 'cls':
                    d['cls'] = vv
                else:
                    d.setdefault(kk, vv)
                continue
            if vv.startswith('"'):
                vv = vv[1:-1]
            if _num.match(vv):
                try:
                    d[kk] = float(vv)
                except ValueError:
                    pass
            else:
                d[kk] = vv          # 字符串字段（含 `c` = tag 家族，resolve_affix 必需）
        # 归一化：无论上面拿到的是 `[...]` 串、`"c20 c21".split(" ")` 串还是列表，
        # 一律压成 `['c20','c21',…]` —— 下游 `_affix_slots()` 只认列表。
        _cv = d.get('cls')
        if isinstance(_cv, str):
            d['cls'] = re.findall(r'c\d+', _cv)
        elif isinstance(_cv, list):
            d['cls'] = re.findall(r'c\d+', ' '.join(str(x) for x in _cv))
        else:
            d['cls'] = []
        km = re.search(r'\bk\s*:\s*(\d+)', body)
        d['k'] = int(km.group(1)) if km else 0
        out[gid] = d
    try:
        json.dump(dict(_mtime=mt, **out), open(_AFFIX_CACHE, 'w', encoding='utf-8'))
    except Exception:
        pass
    return out


_AFFIX_DB = _parse_affixes()
for _g, _d in _AFFIX_DB.items():     # 注入 _IT，使 contrib() 可直接处理词缀
    _IT[_g] = _d


def _affix_slots(gid):
    d = _AFFIX_DB.get(gid) or {}
    s = set()
    for c in d.get('cls', []):
        s.update(_CLS_SLOT.get(c, []))
    return s


# 候选词缀池：按槽位分类，并过滤掉「目标等级段无法出现」的高 tier 词缀（k>_MAX_ILVL）
POOL_PRE, POOL_SUF = {}, {}
for _g, _d in _AFFIX_DB.items():
    _kind = 'pre' if _g.startswith('pre') else ('suf' if _g.startswith('suf') else None)
    if not _kind or _d.get('k', 0) > _MAX_ILVL:
        continue
    for _sl in _affix_slots(_g):
        if _kind == 'pre':
            POOL_PRE.setdefault(_sl, []).append(_g)
        else:
            POOL_SUF.setdefault(_sl, []).append(_g)


# 每个槽位在当前目标下「最优前缀/后缀」。词缀贡献与 (b,c,a) 可加且相互独立，
# 故对线性可加目标，槽位级最优词缀与具体装备无关，可直接折叠进候选向量（×1，不爆炸）。
BEST_PRE, BEST_SUF = {}, {}


def _affix_score(gid):
    _rt, _ot, _sk = contrib(gid)
    if GOAL == 'dmg':
        return sum(_ot[i] * W_DMG.get(k, 0.0) for i, k in enumerate(FKEYS))
    return float(sum(_rt))     # 抗性目标：总抗性点


_AFF_OK = {}


def affix_ok(gid, slot):
    """该词缀能否真的写到这个槽位的记录上（带槽位提示解析成功才算）
       ★ 不校验的话，方案里会写进一条「柜里存在但适用位不对」的词缀——
         游戏不会报错，只是**完全不生效**。"""
    k = (gid, slot)
    v = _AFF_OK.get(k)
    if v is None:
        kind = 'pre' if gid.startswith('pre') else 'suf'
        try:
            v = bool(M.resolve_affix(kind, gid, slot)[0])
        except Exception:
            v = False
        _AFF_OK[k] = v
    return v


def _best_resolvable(pool, slot):
    """按分数降序排，返回第一条「能真正落地」的词缀。

    为什么必须早停：`affix_ok()` 每次都要扫整个词缀文件池（5000+ 条名字做前缀匹配），
    旧写法对每槽约 2500 条**全查一遍**再取 max，12 槽 x 2 种 x 2500 约等于 6 万次池扫描，
    实测 2.57 s（占单次运行的 29.6%）—— 而结果只是取一条最大值的词缀。
    改成「先按分数排序、再顺序找第一条可解析的」后，命中数通常是个位数。
    """
    if not pool:
        return None
    scored = sorted(((_affix_score(g), g) for g in pool), key=lambda x: -x[0])
    for _sc, g in scored:
        if affix_ok(g, slot):
            return g
    return None


def compute_best_affixes():
    for _s in SLOTS:
        BEST_PRE[_s] = _best_resolvable(POOL_PRE.get(_s), _s)
        BEST_SUF[_s] = _best_resolvable(POOL_SUF.get(_s), _s)


def _req_lvl(gid):
    """装备需求等级 = GT 库的 `k`（= `levelRequirement`）；缺失则回退 `itemLevel`。

    用于剔除「等级不够戴不上、游戏里无法生效」的候选件（尤其阵营装备）。

    ★★ 2026-09-22 修正（陷阱 #86）：旧实现对 `l` 做 `c(\\d+)` 解析**并当等级用**，
      注释还写着「`l` 形如 `c20` 表示需 20 级」—— 这是**误读**。
      GT 自己的键字典（`data/cache/itemdb.js`）明写：

          levelRequirement: "k"       ← 等级要求
          Class:            "l"       ← **槽位类别码**
          WeaponMelee_Sword:"c20"     ← `c20` 是「剑」这个类别

      ⇒ `c20` 是**剑**、`c10` 是头、`c40` 是戒指、`c45` 是镶嵌物。
      旧写法让本函数恒返回 10~50 的「槽位码数字」，对 `_MAX_ILVL` 的过滤
      **永远放行** ⇒ 等级闸门形同虚设（实测 `it729`：旧 20 ／ 真实 `k`=50）。
      同时它给镶嵌物返回 45（`l='c45'`），与真实等级要求无关。
    """
    o = _IT.get(gid) or {}
    return int(o.get('k') or o.get('itemLevel') or 0)

TOP = ('火', '冰', '电', '毒酸', '穿刺')
TYPES = ['火', '冰', '电', '毒酸', '穿刺', '流血', '活力', '虚化', '混乱']
PHYS = '物理'
NEED = {t: (130 if t in TOP else 105) for t in TYPES}
NEED[PHYS] = 0
PEN = {t: (50 if t in TOP else 25) for t in TYPES}
PEN[PHYS] = 0
RMAP = {'火': [('defensiveElementalResistance', 1), ('defensiveFire', 1)],
        '冰': [('defensiveElementalResistance', 1), ('defensiveCold', 1)],
        '电': [('defensiveElementalResistance', 1), ('defensiveLightning', 1)],
        '毒酸': [('defensivePoison', 1)], '穿刺': [('defensivePierce', 1)],
        '流血': [('defensiveBleeding', 1)], '活力': [('defensiveLife', 1)],
        '虚化': [('defensiveAether', 1)], '混乱': [('defensiveChaos', 1)],
        PHYS: [('defensivePhysical', 1)]}

# ================================================================ ★ 形态流派（v3，2026-09-17 新增）
# 目的：把「伤害类型权重」从写死的物理向，改成**按形态参数化**。
#   · 数据源 `gt_data/archetypes.json`（由 gt_extract.py 从游戏数据库生成）
#   · `GD_ARCHETYPE=wereraven` 时：
#       ① `damage_weights` 覆盖 W_DMG（维度名不变，只是权重不同）
#       ② `field_aliases` 把维度指向该形态的真实伤害字段
#          （鸦人流：phys → offensiveColdModifier、pierce → offensiveSlowColdModifier）
#   · **不设 GD_ARCHETYPE 时行为与旧版逐字节一致**（安全默认）
#
# 为什么必须做别名：`off_of()` 的维度是物理向的，鸦人流的冰冷加成在旧版里
# **一分都拿不到**（"phys" 槽读的是 offensivePhysicalModifier）。
# 之前只能靠 extra 的手工权重表绕过，无法真正换流派。
_ARCH_NAME = os.environ.get('GD_ARCHETYPE', '')
ARCH = None
if _ARCH_NAME:
    _ap = os.path.join(HERE, 'archetypes.json')
    if os.path.exists(_ap):
        try:
            ARCH = json.load(open(_ap, encoding='utf-8')).get(_ARCH_NAME)
        except Exception:
            ARCH = None
    if not ARCH:
        raise SystemExit('✗ 未知形态 %r（gt_data/archetypes.json 里没有；'
                         '可选项见该文件的键）' % _ARCH_NAME)

# 输出维度（固定顺序）：攻/防两用。
#   伤害代理新增 phys(物理%) / bleed(流血%) / total(总伤害%) / fpie(平戳) / fphy(平物理)
#   —— 狼人形态的「野性利爪 / 狂乱撕扯」= 武器伤害×150~295% + 固定穿刺 + 流血 DoT，
#      其中武器伤害是**物理**（2H 剑无转换），因此只按「穿刺%」评分会漏掉一大块乘区。
FKEYS = ['OA', 'pierce', 'phys', 'bleed', 'total', 'crit', 'spd', 'fpie', 'fbleed',
         'life', 'armor']
# ★ 2026-09-19 新增 **减抗两个维度**（见 `gd/rr.py`）：
#   `rr_add` = 可叠加的敌方减抗（A 族 `defensive*` 负值 + B-abs），**对本流伤害加权**；
#   `rr_pct` = 「% 目标抗性降低」（B-% 族，**取最强不叠加**），单独一维好单独打折。
#   为什么必须加：伤害代理是线性加权和，看不见「敌人抗性」这个乘区。实测 Sam lv71
#   的刺骨战吼给 −30% 穿刺抗，对 33% 抗性的精英 = **1.45 倍**伤害 —— 而旧代理里
#   这类词条一分都拿不到 ⇒ 优化器会拿它去换「看起来更值钱」的 +100% 线性伤害。
#   ⚠ 维度追加在**末尾**：`OA/PI/PH_/…` 这些下标常量都靠 `FKEYS.index()` 取，
#     插在中间会让它们整体错位。
_RR_ON = os.environ.get('GD_RR', '1') != '0'
if _RR_ON:
    FKEYS = FKEYS + ['rr_add', 'rr_pct']
# ★ 2026-09-18 新增 `fbleed` = 平伤 DoT（`offensiveSlowBleedingMin`）。
#   为什么必须补这一个维度：旧 10 维里**没有任何一维能表达「装备附加的流血平伤」**
#   （`fpie` 只管 `offensivePierceMin`）。实测 63 级狼人夜刃：现状装备带 66 点流血平伤，
#   在 957% 流血加成下每击 ≈ +630、约占总 DPS 的 4%；旧维度表对此**完全无感**，
#   于是优化器会拿它去换「看起来更值钱」的穿刺%（线性加权，实际是乘法）。
#   维度数变了没关系：`NT/NF` 与所有 numpy 数组形状都从 len(FKEYS) 推导。
OA, PI, PH_, BL, TO = FKEYS.index('OA'), FKEYS.index('pierce'), FKEYS.index('phys'), FKEYS.index('bleed'), FKEYS.index('total')
CR, SP, FP = FKEYS.index('crit'), FKEYS.index('spd'), FKEYS.index('fpie')
LIFE, ARM = FKEYS.index('life'), FKEYS.index('armor')

# 输出下限（默认 = 52 级存档现状；可用 GD_FLOOR='{"OA":…}' 覆盖）
FLOOR = {'OA': 285, 'pierce': 196, 'phys': 0, 'bleed': 0, 'total': 0,
         'crit': 16, 'spd': 0, 'fpie': 0, 'fbleed': 0, 'life': 980, 'armor': 2092,
         'rr_add': 0, 'rr_pct': 0}      # ★ 减抗不设下限（宁缺毋滥，不罚）
_GDF = os.environ.get('GD_FLOOR')
if _GDF:
    FLOOR.update({k: float(v) for k, v in json.loads(_GDF).items()})
# 权重（= 「每点属性对实战的边际价值」）。伤害/词缀/惩罚**共用同一套**权重，
# 这样「掉 1 点输出」与「得 1 点输出」在评分里严格对称。
#   攻速/暴伤 > 1：它们是**乘区**（+5% 攻速 ≈ +5% DPS），而 +40% 物理只是叠加项
#   物理% 与 穿刺% 同权：狼爪武器段是物理、固定段是穿刺，两条腿都要喂
W_DMG = {'OA': 0.30, 'pierce': 1.00, 'phys': 1.00, 'bleed': 0.55, 'total': 1.30,
         'crit': 1.00, 'spd': 2.00, 'fpie': 0.50, 'fbleed': 0.50, 'life': 0.0, 'armor': 0.0,
         'rr_add': 0.0, 'rr_pct': 0.0}
W_PEN = {'OA': 0.30, 'pierce': 1.00, 'phys': 1.00, 'bleed': 0.55, 'total': 1.30,
         'crit': 1.00, 'spd': 2.00, 'fpie': 0.50, 'fbleed': 0.50, 'life': 0.35, 'armor': 0.25,
         'rr_add': 0.0, 'rr_pct': 0.0,
         # ★ 必须与 `FLOOR` **同键集**：`key()` / 退火的惩罚项都是
         #   `for k in FLOOR: W_PEN[k] * max(0, FLOOR[k] - o[k])` ——
         #   少一个键就是 `KeyError`（本轮 `conv_net` 真的炸过一次）。
         #   权重给 0：`FLOOR['conv_net'] = -1e9` 已是「不设下限」，此项恒为 0。
         'conv_net': 0.0}
# 惩罚倍率：调大 = 把「输出下限」从"软约束"逼近"硬约束"（用户要「保证伤害」时用）
_PEN_SCALE = float(os.environ.get('GD_PEN_SCALE', '1'))
if _PEN_SCALE != 1:
    W_PEN = {k: v * _PEN_SCALE for k, v in W_PEN.items()}

# ★ 形态权重覆盖（v3）：只覆盖权重值，不动维度定义，保证束搜索向量维度不变
if ARCH and ARCH.get('damage_weights'):
    for _k, _v in ARCH['damage_weights'].items():
        if _k in W_DMG:
            W_DMG[_k] = float(_v)
        if _k in W_PEN:
            W_PEN[_k] = float(_v)


def _get(gid, k):
    v = (_IT.get(gid) or {}).get(k)
    return v if isinstance(v, (int, float)) else 0.0


# ★ 维度 → 装备字段名（可被形态的 field_aliases 覆盖）
FLD = {
    'OA': 'characterOffensiveAbility',
    'pierce': 'offensivePierceModifier',
    'phys': 'offensivePhysicalModifier',
    'bleed': 'offensiveSlowBleedingModifier',
    'total': 'offensiveTotalDamageModifier',
    'spd': 'characterAttackSpeedModifier',
    'fpie': 'offensivePierceMin',
    'life': 'characterLife',
    'armor': 'defensiveProtection',
    'crit': 'offensiveCritDamageModifier',
    'DA': 'characterDefensiveAbility',
    'flat': 'offensivePierceMin',
    'fbleed': 'offensiveSlowBleedingMin',
}
if ARCH and ARCH.get('field_aliases'):
    FLD.update(ARCH['field_aliases'])


# ================================================================ ★ 减抗维度（见 gd/rr.py）
# 目标：让「敌方减抗」进伤害代理。三个设计决定：
#
# ① **按本流伤害占比加权**。物品上的减抗多半写成「全类型 −27%」，但本流只吃其中几个
#    桶（狼人穿刺流 = 穿刺 / 物理 / 流血），火焰抗削得再多也一分不值。所以先把
#    多桶减抗折成**一个标量**（=「等效主桶减抗点」：「全类型 −27」对穿刺流就是 27）。
#    占比从 `damage_weights` + `field_aliases` 推导 —— 鸦人流的 `phys` 别名指向
#    `offensiveColdModifier`，桶权重就自动落到冰冷，不用维护第二张手工表。
#
# ② **权重按边际价值动态标定**（`recalibrate_rr`）。同一个「1 点」，
#    +1% 伤害加成的边际是 `D/(100+pct)`，而 −1 敌方抗性的边际是 `D/(100−res)` ——
#    后者通常大一个数量级（Sam：pct 1500 / res 33 ⇒ **23.9 倍**）。这个比值随当前
#    加成与所选敌方档位变化，所以由调用方（autobuild / tune_dps）在搜索前用真实
#    数值重标一次，而不是写死常量。
#
# ③ **打折**。`GD_RR_UPTIME`（默认 0.7）—— debuff 不是 100% 覆盖，且多数敌人
#    的实际抗性低于标称档位；`rr_pct` 再乘 `GD_RR_PCT_FACTOR`（默认 0.5）——
#    因为 B-% 族「取最强」，第二件起边际陡降。用户口径是「性价比合适就行」，
#    这两个旋钮就是「合适」的调节点。
RR_FIELD_BUCKET = {
    'offensivePhysicalModifier': ('physical',), 'offensivePierceModifier': ('pierce',),
    'offensiveFireModifier': ('fire',), 'offensiveColdModifier': ('cold',),
    'offensiveLightningModifier': ('lightning',), 'offensivePoisonModifier': ('poison',),
    'offensiveVitalityModifier': ('vitality',), 'offensiveLifeModifier': ('vitality',),
    'offensiveAetherModifier': ('aether',), 'offensiveChaosModifier': ('chaos',),
    'offensiveSlowBleedingModifier': ('bleeding',), 'offensiveSlowFireModifier': ('fire',),
    'offensiveSlowColdModifier': ('cold',), 'offensiveSlowLightningModifier': ('lightning',),
    'offensiveSlowVitalityModifier': ('vitality',), 'offensiveSlowLifeModifier': ('vitality',),
    'offensiveSlowPoisonModifier': ('poison',), 'offensiveSlowPhysicalModifier': ('physical',),
    'offensiveElementalModifier': ('fire', 'cold', 'lightning'),
}
RR_DIM_W = {}       # 抗性桶 → 本流伤害占比（和为 1）
RR_MAIN = ''
if _RR_ON:
    for _d in ('pierce', 'phys', 'bleed', 'fpie', 'fbleed'):
        _w = float(W_DMG.get(_d, 0.0) or 0.0)
        if _w <= 0:
            continue
        for _b in RR_FIELD_BUCKET.get(FLD.get(_d) or '', ()):
            RR_DIM_W[_b] = RR_DIM_W.get(_b, 0.0) + _w
    if not RR_DIM_W:                    # 兜底：形态没给伤害权重时，按穿刺流估
        from . import rr as _RRM
        RR_DIM_W = {b: 1.0 for b in _RRM.RES_BUCKETS}
    _s = sum(RR_DIM_W.values()) or 1.0
    RR_DIM_W = {k: v / _s for k, v in RR_DIM_W.items()}
    RR_MAIN = max(RR_DIM_W, key=RR_DIM_W.get)

_RR_OF = {}


def rr_dims(gid):
    """gid → (rr_add, rr_pct)：按本流伤害占比折算后的「等效主桶减抗点」。

    「−27% 全部抗性」对纯穿刺流 = **27 点**；对 50% 打火/50% 打冰的流 = 27 点
    （两桶都覆盖）；而「−20% 物理」对穿刺流只有 `20 × 占比(物理)` 点。
    """
    v = _RR_OF.get(gid)
    if v is None:
        from . import rr as _RR
        p = _RR.rr_of(_IT.get(gid) or {})
        a = sum(RR_DIM_W.get(b, 0.0) * x for b, x in (p.get('add') or {}).items())
        # ★ A 族（`…ReductionAbsolute`，绝对值）折进 `rr_add` 维：它是**绝对减抗**，
        #   与 B 族同量纲（都是「减多少点抗性」），只是结算得晚一步（B→C→A）。
        #   不折进来这些装备在代理里就是 0 分，会被搜索器整类丢掉。
        a += sum(RR_DIM_W.get(b, 0.0) * x for b, x in (p.get('flat') or {}).items())
        m = sum(RR_DIM_W.get(b, 0.0) * x for b, x in (p.get('max') or {}).items())
        v = (round(a, 3), round(m, 3))
        _RR_OF[gid] = v
    return v


# 标定基线（调用方没给真实数据时用的保守值；autobuild 会用实测量覆盖）
_RR_INIT_PCT = float(os.environ.get('GD_RR_BASE_PCT', '600'))
_RR_INIT_RES = float(os.environ.get('GD_RR_BASE_RES', '33'))


def _clear_caches():
    """标定/参数变更后必须清空的派生缓存。

    ★ `_CV`（`contrib` 的 per-gid 缓存）里存的是**已经算好的 ot 向量**，
      而 ot 里含 rr / conv 两维 —— 标定后不清它，搜索就还在用旧权重算出来的值。
      （`_CV` 定义在本函数之后，模块级首次自动标定时它还不存在 → 用 NameError 兜住。）
    """
    for _c in ('_CV', '_RR_OF', '_CONV_OF'):
        try:
            globals()[_c].clear()
        except (KeyError, NameError):
            pass


def recalibrate_rr(pct_main=None, res_main=None, log=None):
    """按当前解的真实口径重标 `rr_add` / `rr_pct` 权重（迭代线性化的一步）。

    `pct_main` = 主桶当前的伤害加成（%，例如穿刺 +1500）
    `res_main` = 所选敌方档位下、**已计入技能减抗**的主桶有效抗性（%）
    """
    if not _RR_ON:
        return {}
    pct = float(_RR_INIT_PCT if pct_main is None else pct_main)
    res = float(_RR_INIT_RES if res_main is None else res_main)
    wd = float(W_DMG.get(RR_MAIN or 'pierce', 1.0) or 1.0)
    scale = (100.0 + pct) / max(100.0 - res, 1.0)
    up = float(os.environ.get('GD_RR_UPTIME', '0.7'))
    pf = float(os.environ.get('GD_RR_PCT_FACTOR', '0.5'))
    W_DMG['rr_add'] = round(wd * scale * up, 4)
    W_DMG['rr_pct'] = round(wd * scale * up * pf, 4)
    W_PEN['rr_add'] = W_DMG['rr_add']
    W_PEN['rr_pct'] = W_DMG['rr_pct']
    _clear_caches()
    out = {'pct_main': pct, 'res_main': res, 'rr_main': RR_MAIN,
           'scale': round(scale, 2), 'uptime': up, 'pct_factor': pf,
           'W_rr_add': W_DMG['rr_add'], 'W_rr_pct': W_DMG['rr_pct']}
    if log:
        log('  [减抗] 主桶 %s ｜ 加成 +%.0f%% ｜ 敌方剩 %.0f%% ⇒ 边际比 %.1f× '
            '⇒ 权重 rr_add=%.2f / rr_pct=%.2f（uptime %.2f, pct 因子 %.2f）'
            % (RR_MAIN, pct, res, scale, W_DMG['rr_add'], W_DMG['rr_pct'], up, pf))
    return out


recalibrate_rr()          # 模块级先按保守基线标一次（无副作用，只是算权重）


# ================================================================ ★ 暴击伤害（crit 维度）
#
# Q6 的「口径撕裂」：`crit` 维给的是**写死的 1.00**，而 v2 之前的伤害模型
# **对暴击零响应**（`hit_mult` 还没接进 `final_report`）—— 于是优化器会拿
# 一件「+40% 暴击伤害」去换真实的线性 % 伤害，代价无人察觉。
#
# v2 起 `gd/rotation.py` 把 OA/DA → PTH → 暴击窗口 → 期望倍率接进了主流水线
# （`gd/combat.py::hit_mult`），`crit` 终于有真实边际。于是权重也能**推导**出来：
#
#   +1 点 `offensiveCritDamageModifier`（= +1% 暴伤）
#      ⇒ 期望倍率增加 `暴击率 / 100`（只在暴击面上加 1 点基础伤害）
#      ⇒ 相对增益 = `(暴击率/100) / 期望倍率`
#   +1 点 `offensiveTotalDamageModifier`（= +1% 全伤害）
#      ⇒ 相对增益 = `1 / (100 + pct_ref)`，其中 `pct_ref` 是**调和等效**基准
#        （`Σ_t share_t/(100+pct_t)` 的倒数 − 100）。★ 不能用算术加权平均：
#        `1/(100+pct)` 是凸函数，算术口径实测偏 2.2%（Sam 856.5 vs 837.3）；
#        调和口径下解析值与有限差分**逐位吻合**。
#
#   两个「相对增益」的比值 × `W_DMG['total']` = `W_DMG['crit']`
#
# 实测校验（Sam，lv71，终极 Champion/Hero 池）：
#   +10 暴伤 → 实战 +156.8 ｜ +10 全伤害 → 实战 +384.6 ｜ 比值 **0.4077**
#   推导值：0.0423/100/0.9711 × (100+837.3) = 0.4077 ✓（selftest [21] 断言这条）
#   ⇒ `W_DMG['crit'] = 1.30 × 0.4077 = 0.53`，而写死值是 1.00 ⇒ **高估 1.85 倍**
#
# ⚠ 上限：`GD_CRIT_CAP`（默认 0.6）—— 暴击流（高暴击率 + 高暴伤）的边际会显著
#   高于此值，但本表是**搜索排序用的代理**，给太高会让优化器堆暴伤而牺牲基础伤害。
#   要跑真正的暴击流，把上限调上去或直接用 `GD_REAL_GOAL=real` 走真实模型复评。
_CRIT_ON = os.environ.get('GD_CRIT', '1').strip() not in ('0', 'off', 'no', 'false')
_CRIT_CAP = float(os.environ.get('GD_CRIT_CAP', '0.6'))
# 未标定时的暴击率假设。★ 为什么给 10% 而不是 0：给 0 等于「crit 维永远不选」，
#   那是另一种口径撕裂（静默把暴击伤害当垃圾）。10% 是「有点 OA 但没堆」的中位假设，
#   代进公式（pct 基准 600）得 `W_crit ≈ 0.91` —— 与写死的 1.00 同量级，不会突变；
#   真实标定则由 `autobuild` 用 `rep['hit']['crit_chance']` 覆盖。
_CRIT_DEFAULT_CHANCE = float(os.environ.get('GD_CRIT_CHANCE', '0.10'))


def recalibrate_crit(chance=None, expected=None, pct_ref=None, log=None):
    """按**当前解的真实暴击口径**重标 `crit` 权重（Q6）。

    `chance`   = 暴击率（0..1，来自 `rep['hit']['crit_chance']`）
    `expected` = 命中/暴击的期望倍率（`rep['hit']['expected']`，含未命中折扣）
    `pct_ref`  = 与 `W_DMG['total']` 对应的**加成基准**（按 DPS 占比加权的 % 加成）。
                缺省时退回 `GD_RR_BASE_PCT`（与减抗维度同一保守基线）。
    """
    if not _CRIT_ON or 'crit' not in W_DMG:
        return {}
    cc = _CRIT_DEFAULT_CHANCE if chance is None else float(chance)
    ex = 1.0 if expected is None else float(expected)
    pct = float(pct_ref if pct_ref is not None else _RR_INIT_PCT)
    # 相对增益比 = [(暴击率/100) / 期望] ÷ [1 / (100+pct)]
    ratio = (cc / 100.0) / max(ex, 1e-9) * (100.0 + pct)
    w = float(W_DMG.get('total', 1.0) or 1.0) * ratio
    w = max(0.0, min(w, _CRIT_CAP)) if _CRIT_CAP > 0 else max(0.0, w)
    W_DMG['crit'] = round(w, 4)
    W_PEN['crit'] = W_DMG['crit']
    _clear_caches()
    out = {'crit_chance': cc, 'expected': ex, 'pct_ref': pct,
           'ratio_vs_total': round(ratio, 4), 'W_crit': W_DMG['crit']}
    if log:
        log('  [暴击] 暴击率 %.2f%% ｜ 期望倍率 %.4f ｜ 加成基准 +%.0f%% '
            '⇒ 相对全伤害 %.3f× ⇒ 权重 crit=%.3f（写死值曾是 1.00）'
            % (cc * 100.0, ex, pct, ratio, W_DMG['crit']))
    return out


recalibrate_crit()        # 模块级先按保守基线标一次（cc 缺省 0 ⇒ 权重 0，安全下限）


# ================================================================ ★ 伤害转化（conv 维度）
# 目标：让优化器**看得见**「这件装备会把伤害转成别的类型」。
#
# 旧对策是 `GD_CONV_KEEP` 白名单（整体剔除转化件）—— 只能删候选、给不出估值，
# 还会误杀「转出去但转得更划算」的件。本版换成逐件真估值：
#
#     损失比例 = X% × (源伤害占比) × (mA − mB)/mA
#     主桶等效 % = 损失比例 × (100 + 主桶加成)
#
# 其中 `mA`/`mB` 是源/目标伤害类型**当前的加成倍率**。
#
# ★ 实测修正（2026-09-19）：plan 里写的「转化来源只来自武器槽」**不成立** ——
#   全库 **2458 件**物品带 `conversionInType/OutType`，分布在 melee2h / shields /
#   caster / guns2h / swords1h / waist / focus / rings / necklaces / components
#   等**所有**槽位（294 种 (in,out,pct) 签名）。因此「按武器对枚举转化桶、
#   桶内恢复线性」的方案根本不适用（没有那个桶）；改用「逐件估值 + 迭代线性化」——
#   与减抗完全同一套思路，而且不需要枚举，规模上也不挑槽位。
_CONV_ON = os.environ.get('GD_CONV', '1') != '0'
if _CONV_ON:
    FKEYS = FKEYS + ['conv_net']
    FLOOR['conv_net'] = -1e9          # 转化可以是负的 ⇒ 永不触发下限惩罚
    W_DMG['conv_net'] = 0.0           # 权重由 recalibrate_conv 按主桶权重填

# 转化字段里的类型名（GD 写的是 `Life` / `Elemental` 这类）→ 我们的伤害类型
_CONV_TYPES = {
    'physical': ('physical',), 'pierce': ('pierce',),
    'fire': ('fire',), 'cold': ('cold',), 'lightning': ('lightning',),
    'poison': ('poison', 'acid'), 'acid': ('poison', 'acid'),
    'vitality': ('vitality',), 'life': ('vitality',),
    'aether': ('aether',), 'chaos': ('chaos',), 'bleeding': ('bleeding',),
    'elemental': ('fire', 'cold', 'lightning'),
}
# 「被转化的源伤害」的类型构成。默认纯物理 —— GD 的武器基础伤害主体就是物理，
# 而转化规则作用在武器基础伤害上（`offensivePierceRatioMin` 那条除外，它另有建模）。
CONV_SHARE = {'physical': 1.0}
CONV_MULT = {}            # 伤害类型 → 当前加成倍率 (1 + pct/100)
CONV_PCT_MAIN = 0.0       # 主桶当前加成（%）
_CONV_OF = {}


def _conv_types(name):
    return _CONV_TYPES.get(str(name or '').strip().lower(), ())


def conv_delta(gid):
    """该物品的转化在**当前加成口径**下对主桶的净影响（主桶等效 %，正=收益）。

    例（Sam 穿刺流 +1300% 穿刺 / +50% 火焰）：一件「物理 → 火焰 25%」的武器
    ⇒ 25% 的物理伤害从 ×14.0 掉到 ×1.5 ⇒ 损失比例 ≈ 25% × (14−1.5)/14 = 22.3%
    ⇒ 主桶等效 −22.3% × 1400 ≈ **−312「% 穿刺」** —— 与「+312% 穿刺」同量级，
    优化器这时才真的看得见它。
    """
    v = _CONV_OF.get(gid)
    if v is None:
        v = 0.0
        o = _IT.get(gid) or {}
        i, t, p = (o.get('conversionInType'), o.get('conversionOutType'),
                   o.get('conversionPercentage'))
        if i and t and p and CONV_MULT:
            try:
                frac = abs(float(p)) / 100.0
            except Exception:
                frac = 0.0
            ins, outs = _conv_types(i), _conv_types(t)
            share = sum(CONV_SHARE.get(x, 0.0) for x in ins)
            if frac and share > 0 and outs:
                mi = sum(CONV_MULT.get(x, 1.0) for x in ins) / max(len(ins), 1)
                mo = sum(CONV_MULT.get(x, 1.0) for x in outs) / max(len(outs), 1)
                if mi > 1e-9:
                    v = (min(frac * min(share, 1.0), 1.0) * (mo - mi) / mi
                         * (100.0 + CONV_PCT_MAIN))
        _CONV_OF[gid] = v
    return v


def recalibrate_conv(pct=None, share=None, main=None, log=None):
    """按当前解的**真实加成口径 + 真实伤害构成**重标转化维度。

    `pct`   = {伤害类型: 当前加成 %}（来自 `final_report` 的 `rep['pct']`）
    `share` = {伤害类型: 该类型在总 DPS 里的占比}（来自真实评估）
              —— 不传就退回「武器基础=纯物理」的旧假设（对纯物理武器成立，
                 但对带大量元素/活力平伤的装备会把转化影响估偏）。
    `main`  = 主伤害类型（与 `RR_MAIN` 同一个桶对应的类型名）
    """
    if not _CONV_ON:
        return {}
    global CONV_PCT_MAIN
    pct = pct or {}
    from . import rotation as _RT
    for t in _RT.TYPE_ORDER:
        CONV_MULT[t] = 1.0 + float(pct.get(t, 0.0) or 0.0) / 100.0
    if share:
        tot = sum(float(v or 0.0) for v in share.values())
        if tot > 0:
            CONV_SHARE.clear()
            CONV_SHARE.update({k: float(v) / tot for k, v in share.items() if v})
    main = main or RR_MAIN or 'pierce'
    CONV_PCT_MAIN = float(pct.get(main, 0.0) or 0.0)
    W_DMG['conv_net'] = float(W_DMG.get(main, 1.0) or 1.0)
    _clear_caches()
    out = {'main': main, 'pct_main': CONV_PCT_MAIN,
           'W_conv_net': W_DMG['conv_net'],
           'share': {k: round(v, 3) for k, v in sorted(
               CONV_SHARE.items(), key=lambda x: -x[1])[:4]},
           'mult': {k: round(v, 2) for k, v in sorted(
               CONV_MULT.items(), key=lambda x: -x[1])[:4]}}
    if log:
        log('  [转化] 主类型 %s（+%.0f%%）×%s ｜ 源伤害构成 %s ⇒ 权重 conv_net=%.2f'
            % (main, CONV_PCT_MAIN,
               '、'.join('%s×%.1f' % (k, v) for k, v in out['mult'].items()),
               '、'.join('%s %.0f%%' % (k, v * 100) for k, v in out['share'].items()),
               W_DMG['conv_net']))
    return out


# ------------------------------------------------ ★ 减抗技能等级的价值（阶段 3）
# 装备上的 `augmentSkillName/augmentSkillLevel`（给技能 +N 级）本来就进技能分，
# 但那套权重是**手工分段表**（狼人形态 90 分/级、贪噬 40 …）—— 里面**完全没有**
# 「这个技能每级减多少抗」的概念。后果：一件给「刺骨战吼 +3」的装备，实际等于
# **−6% 穿刺抗性**（对 33% 抗性的敌人 ≈ +9% 实战伤害），却只按手工分估值。
#
# 本函数扫全库找出「每升一级都会加强减抗」的技能，把它们**按真实减抗价值**折进
# `SKILL_W` —— 于是装备选择会自动偏向「给减抗技能加等级」的那几件。
#
# ⚠ 这是「装备侧」的修正。至于**技能点本身**该点几级（用角色的技能点预算去换），
#   属于角色构建而不只是配装，交给 `tools/rr_skills.py` 做边际分析。
RR_SKILL_GAIN = {}      # tag → (每级 add 加权点, 每级 max 加权点, record)
# 减抗技能的**生效折扣**（按来源分档）—— 不减会系统性高估：
#   · 专精技能（`/playerclass/`）：玩家自己按 CD 放，打的久了基本常驻 → 1.0；
#   · 星座（`/devotion/`）：多半绑在攻击触发上、有内置 CD → 0.7；
#   · **物品技能（`/itemskills/`）：装备上的概率触发**（`itemSkillAutoController`
#     常见 `onattack_10%` / `onattackcrit_33%`），按常驻估值会高出好几倍
#     —— 实测未打折时「圣物平衡 +1 级 = 286 分」，会直接碾压所有真实属性 → 0.25；
#   · 其他（圣物/怪物技能等）→ 0.5。
RR_SKILL_KIND_K = {
    'playerclass': 1.0, 'devotion': 0.7, 'itemskill': 0.25, '': 0.5,
}


def _rr_skill_kind_k(rec):
    rl = str(rec or '').lower()
    for k, v in RR_SKILL_KIND_K.items():
        if k and k in rl:
            return v
    return RR_SKILL_KIND_K['']


def find_rr_skills(db=None):
    """扫全库 → `{tag: (每级叠加减抗, 每级最强减抗, record)}`（按本流伤害占比归一）。"""
    if db is None:
        from . import dbr as _DBM
        db = _DBM.open_all()
    from . import dps as _D
    from . import rr as _RR
    t2r = _D._tag2rec_map()
    out = {}
    for tag, rec in t2r.items():
        try:
            f = db.fields(rec) or {}
        except Exception:
            continue
        # ★ 预筛：不走这一步要对 1715 个技能各做两次 rr_of（每次遍历几百个字段）
        if not any(('ResistanceReduction' in _k) or (_k in _RR.DEF_BUCKET) for _k in f):
            continue
        p1, p2 = _RR.rr_of(f, 1), _RR.rr_of(f, 2)
        a = sum(RR_DIM_W.get(b, 0.0) * (p2['add'].get(b, 0.0) - p1['add'].get(b, 0.0))
                for b in set(p1['add']) | set(p2['add']))
        a += sum(RR_DIM_W.get(b, 0.0) * (p2['flat'].get(b, 0.0) - p1['flat'].get(b, 0.0))
                 for b in set(p1['flat']) | set(p2['flat']))
        m = sum(RR_DIM_W.get(b, 0.0) * (p2['max'].get(b, 0.0) - p1['max'].get(b, 0.0))
                for b in set(p1['max']) | set(p2['max']))
        if a > 1e-9 or m > 1e-9:
            out[tag] = (round(a, 4), round(m, 4), rec)
    return out


_SKILL_W_BASE = None


def recalibrate_rr_skills(db=None, log=None):
    """把「减抗技能的每级增益」按**来源折扣**折进 `SKILL_W` / `SKILL_W_DMG`。

    ★ 幂等：第一次调用时记下基线，之后每次都从基线重建 —— 否则反复调用会累加。
    """
    if not _RR_ON:
        return {}
    global RR_SKILL_GAIN, _SKILL_W_BASE
    try:
        gain = find_rr_skills(db)
    except Exception as e:
        if log:
            log('  [减抗技能] 扫描失败：%s: %s' % (type(e).__name__, e))
        return {}
    w_add = float(W_DMG.get('rr_add', 0.0) or 0.0)
    w_pct = float(W_DMG.get('rr_pct', 0.0) or 0.0)
    _same = SKILL_W is SKILL_W_DMG          # goal=dmg 时两者是**同一个对象**
    if _SKILL_W_BASE is None:
        _SKILL_W_BASE = (dict(SKILL_W), dict(SKILL_W_DMG), _same)
    else:
        SKILL_W.clear()
        SKILL_W.update(_SKILL_W_BASE[0])
        if not _same:
            SKILL_W_DMG.clear()
            SKILL_W_DMG.update(_SKILL_W_BASE[1])
    n = 0
    top = []
    for tag, (a, m, rec) in gain.items():
        v = (a * w_add + m * w_pct) * _rr_skill_kind_k(rec)
        if v <= 0:
            continue
        if _same:
            SKILL_W_DMG[tag] = SKILL_W_DMG.get(tag, 0.0) + v
        else:
            SKILL_W[tag] = SKILL_W.get(tag, 0.0) + v
            SKILL_W_DMG[tag] = SKILL_W_DMG.get(tag, 0.0) + v
        n += 1
        top.append((v, tag, rec))
    RR_SKILL_GAIN = dict(gain)
    _clear_caches()
    top.sort(reverse=True)
    if log:
        log('  [减抗技能] %d 个技能带减抗，已按「来源折扣」折进技能分 ｜ 前 5：%s'
            % (n, '、'.join('%s(+%.0f 分/级)' % (t.replace('tag', '').replace('Name', '')
                                                .replace('Skill', ''), v)
                            for v, t, _r in top[:5]) or '—'))
    return dict(gain)


def res_of(gid):
    d = defaultdict(float)
    for t, ks in RMAP.items():
        for k, w in ks:
            d[t] += _get(gid, k) * w
    return d


def off_of(gid):
    """★ v3：字段名来自 FLD（可被形态 field_aliases 覆盖），维度与旧版完全一致。

    旧版把字段名写死在这里 → 鸦人流的冰冷加成一分都拿不到。
    现在 `GD_ARCHETYPE=wereraven` 会把 phys/pierce/fpie 指到冰冷字段上，
    维度数不变，因此束搜索的向量形状、剪枝逻辑、下游全部不用改。

    ★ 减抗两维（`rr_add` / `rr_pct`）不走 FLD —— 它们是**跨抗性桶的加权和**，
      单个字段名表达不了，所以在这里单独填（`rr_dims`）。
    """
    o = {k: _get(gid, f) for k, f in FLD.items()}
    if _RR_ON:
        _a, _m = rr_dims(gid)
        o['rr_add'] = _a
        o['rr_pct'] = _m
    if _CONV_ON:
        o['conv_net'] = conv_delta(gid)
    return o


def name_of(gid):
    if not gid:
        return '——'
    o = _IT.get(gid) or {}
    t = o.get('a') or o.get('d') or ''
    nm = (TAGS.get(t, '') if isinstance(t, str) else '') or gid
    return re.sub(r'\^.', '', nm)   # 去掉 GrimTools 颜色码 ^k ^w 等


# ---------------------------------------------------------------- 贡献向量缓存
# ★ 性能关键：原版 ev()/key() 每次评分都把整个解重算一遍，res_of/off_of 也无缓存，
#   同一个 gid 一次运行被重复解析上万次。这里改为「每个 gid 只算一次」。
_CV = {}


def contrib(gid):
    """gid -> (抗性向量, 输出向量, 技能分)"""
    c = _CV.get(gid)
    if c is None:
        r0, o0 = res_of(gid), off_of(gid)
        rt = tuple(r0.get(t, 0.0) for t in TYPES)
        ot = tuple(o0[k] for k in FKEYS)
        sk = 0.0
        o = _IT.get(gid) or {}
        for n in (1, 2, 3, 4):
            t = o.get('augmentSkillName%d' % n)
            lv = o.get('augmentSkillLevel%d' % n)
            if isinstance(t, str) and t in SKILL_W and isinstance(lv, (int, float)):
                sk += SKILL_W[t] * lv
        c = (rt, ot, sk)
        _CV[gid] = c
    return c


# ★ 戒指两槽**可互换**：同一类别 `c40`、同一 `SLOTDIR=['rings']`、同一 `slot_ok` 判据
#   ⇒ 手工名单必须**同源**（共用一份常量，结构上杜绝再次分叉）。
#   原实现两槽各写一份略有出入的名单（戒指1 独有 it14570/it14552/it957、
#   戒指2 独有 it14558/it7882）⇒ 同一件戒指放左放右候选不同，搜索空间被人为卡成不对称。
RING_BASE = ['it14570', 'it987', 'it14552', 'it957', 'it14534', 'it7893', 'it972',
             'it976', 'it7896', 'it14558', 'it7882', 'it973']

POOL_BASE = {
    '头部': ['it1308', 'it15408', 'it8077'],
    # ⚠ it823（腐臭项链）已移除：它是**任务物品**（位图 `misc/bitmaps/quest_necklace_slith01.png`，
    #    `_is_quest` 判 True），硬编码池绕过了 `_is_quest` ⇒ 任务装塞进装备槽会被游戏静默摘掉（陷阱 #35）。
    # ⚠ it486（收割者宝石）、it483（罗瓦里的怨恨）都是 `ArmorJewelry_Amulet`（**项链**），
    #    原本被放在「戒指1」池里 —— 按 `slot_ok` 反查只有「项链」能过。现归位到项链
    #    （it483 本来就在项链列表里，只是从戒指1 里删重复的错位项）。
    '项链': ['it14500', 'it14514', 'it14005', 'it15733', 'it14499', 'it848', 'it840',
             'it483', 'it489', 'it486'],
    '胸甲': ['it1729', 'it614', 'it606', 'it602'],
    '腿甲': ['it8168', 'it1420', 'it8183', 'it15827', 'it15822', 'it11193', 'it8158'],
    '靴子': ['it525', 'it527', 'it1131', 'it521', 'it529'],
    '手套': ['it537', 'it533', 'it535', 'it531'],
    '戒指1': RING_BASE,
    '戒指2': RING_BASE,
    # ⚠ it504（恶魔的护腰）已移除：标签族 f002_waist 档数不吻合（GT 1 vs 库 3），
    #    记录名解析为低置信，gd_build 会拒绝该方案。其余 7 件均为高置信映射。
    '腰带': ['it1047', 'it1048', 'it7182', 'it1070', 'it1071', 'it1073', 'it12207'],
    '肩甲': ['it8266', 'it1582', 'it1594', 'it1566', 'it12364'],
    # ⚠ it14477（炼狱梦境勋章）是 `ArmorJewelry_Medal`（**勋章**），原本被放在「戒指2」池里
    #    —— 按 `slot_ok` 反查只有「勋章」能过。现已归位到这里。
    '勋章': ['it13960', 'it749', 'it7760', 'it12120', 'it14176', 'it13965', 'it13955',
             'it7788', 'it12118', 'it14477'],
    '圣物': ['it1487', 'it1478', 'it14691', 'it1480', 'it8215', 'it1489'],
}
# ---- 传奇（紫装）候选：51 级可用、已核记录名 ----
# 注意：传奇物品**没有词缀**（GD 的 Legendary 不参与词缀系统），换成紫装 = 丢该槽词缀；
#       但传奇自带 itemSkillName（特效）+ augmentSkillName（+精通技能等级）+ 部分 MaxResist。
POOL_LEG = {
    '头部': ['it8124', 'it17247', 'it12321', 'it8122', 'it1370'],
    '胸甲': ['it1803', 'it8368', 'it8366', 'it12441', 'it14762', 'it1802'],
    '肩甲': ['it1648', 'it8284', 'it14725', 'it1647', 'it8286'],
    '腿甲': ['it14681', 'it1449', 'it8199', 'it1448', 'it12338', 'it8201'],
    '靴子': ['it1156', 'it1154', 'it1155', 'it8011', 'it14637', 'it12229', 'it8013'],
    '手套': ['it12246', 'it8038', 'it1222', 'it14646', 'it1220', 'it8036'],
    '项链': ['it15810'],
    # 戒指/腰带/圣物：51 级无可用紫装（GT 全库核对）
}
for _s, _lst in POOL_LEG.items():
    POOL_BASE.setdefault(_s, [])
    POOL_BASE[_s] = list(POOL_BASE[_s]) + [g for g in _lst if g not in POOL_BASE[_s]]

POOL_WPN = ['it729', 'it15954', 'it8399', 'it15006', 'it2804', 'it2798']
ALLARM = ['it2879', 'it2863', 'it2853', 'it2839']
POOL_COMP = {
    '头部': ALLARM + ['it2865', 'it2866'],
    '胸甲': ALLARM + ['it2866', 'it2872', 'it2894', 'it2896', 'it2860'],
    '腿甲': ALLARM + ['it2872'],
    '靴子': ALLARM,
    '手套': ALLARM + ['it2905', 'it2835'],
    '肩甲': ALLARM + ['it2872', 'it2860'],
    '项链': ['it2881', 'it2823', 'it2829', 'it2836', 'it2873'],
    '勋章': ['it2881', 'it2823', 'it2829', 'it2836', 'it2873'],
    '戒指1': ['it2836', 'it2873', 'it2845'],
    '戒指2': ['it2836', 'it2873', 'it2845'],
}
POOL_CWPN = ['it2878', 'it2849']
JA = ['it415', 'it364', 'it413', 'it406', 'it411', 'it425', 'it376', 'it392',
      'it424', 'it453', 'it404', 'it390', 'it377', 'it414']
WA = ['it393', 'it428', 'it444', 'it399', 'it397', 'it401', 'it432']

# ★ 武器槽默认纳入（用户要求「武器带装备」）—— 主手/副手走 POOL_WPN + 武器组件 + 武器附魔
SLOTS = ['头部', '项链', '胸甲', '腿甲', '靴子', '手套', '戒指1', '戒指2', '腰带', '肩甲', '勋章', '圣物', '主手', '副手']
if _SLOTS_ENV:
    _want = [s.strip() for s in _SLOTS_ENV.split(',') if s.strip()]
    _bad = [s for s in _want if s not in SLOTS]
    if _bad:
        raise SystemExit('✗ GD_SLOTS 含未知槽位: %s' % _bad)
    SLOTS = [s for s in SLOTS if s in _want]        # 保持原顺序

SLOTDIR = {'头部': ['gearhead', 'faction/head'], '胸甲': ['geartorso', 'faction/torso'],
           '腿甲': ['gearlegs', 'faction/legs'], '靴子': ['gearfeet', 'faction/feet'],
           '手套': ['gearhands', 'faction/hands'], '肩甲': ['gearshoulders', 'faction/shoulders'],
           '项链': ['necklaces'], '戒指1': ['rings'], '戒指2': ['rings'],
           '腰带': ['waist'], '勋章': ['medals'], '圣物': ['gearrelic'],
           '主手': ['swords1h', 'axes1h', 'blunt1h', 'dagger'], '副手': ['swords1h', 'axes1h', 'blunt1h', 'dagger']}
COMP_SLOT = {'头部': ['头盔'], '胸甲': ['胸甲', '护肩', '胸甲和头盔', '所有护甲', '护肩，胸甲和护腿'],
             '腿甲': ['所有护甲', '护肩，胸甲和护腿'], '靴子': ['所有护甲'],
             '手套': ['所有护甲', '手套'], '肩甲': ['所有护甲', '护肩，胸甲和护腿'],
             '项链': ['项链和勋章', '戒指，项链以及勋章', '戒指，项链和勋章'],
             '勋章': ['项链和勋章', '戒指，项链以及勋章', '戒指，项链和勋章'],
             '戒指1': ['戒指，项链以及勋章', '戒指，项链和勋章', '戒指'],
             '戒指2': ['戒指，项链以及勋章', '戒指，项链和勋章', '戒指'],
             '主手': ['所有武器，盾牌和法器副手'], '副手': ['所有武器，盾牌和法器副手']}


def slot_ok(slot, gid):
    o = _IT.get(gid) or {}
    bm = (o.get('n') or '').lower()
    if not bm:
        return False
    if '/bitmaps/' in bm or bm.endswith('.png'):
        for d in SLOTDIR.get(slot, []):
            if d in bm:
                return True
        return False
    return False


# 组件「可用于哪些槽位」的词表。★ 词表 + 子串匹配（`comp_ok`），无括号一律拒绝。
#   必须用**词表 + 子串匹配**，不能用「精确等于 COMP_SLOT 里的整句」：
#   全库 107 个组件的括号文本有 25 种不同写法（"用于所有护甲" / "用于胸甲和头盔" /
#   "用于戒指，项链以及勋章" / "适用于斧、矛、剑、枪、弩" …），精确匹配必然大面积漏判。
#   ⚠ `头部` 必须含 **'护甲'**：4 件「(用于所有护甲)」的护甲组件（`ALLARM`：
#     it2879 抗毒血清 / it2863 坚硬外壳 / it2853 熔岩皮肤 / it2839 茂密毛皮）
#     原本被 `comp_ok('头部')` 判 False —— 而它们又在硬编码 `POOL_COMP['头部']` 里，
#     **池与校验器自相矛盾**。游戏里「所有护甲」当然包含头盔，故补上 '护甲'。
# ★★ 槽位 → 底材类别码（2026-09-21 从 GT 库实测推出，见 docs/pitfalls.md #67）
#
#   底材自己的 `l` **就是它的槽位码**（斧 c21 / 胸甲 c12 / 勋章 c42 …），
#   而组件与附魔的 `cls` **就是「它能装的底材码集」**
#   ⇒ 判据 = `base.l ∈ item.cls`。这条规则**对组件与附魔通用**，且无需维护任何词表。
#
#   实测：`弹性铠甲片 it2860` cls=[c11,c12,c26,c27] —— 与武器码集**完全不相交**，
#   而它的描述明写「用于盾牌，法器副手，胸甲和护肩」，两条证据一致。
WEAPON_CODES = ('c20', 'c21', 'c22', 'c23', 'c24', 'c25',
                'c28', 'c29', 'c30', 'c31', 'c32')
SLOT_CODES = {
    '头部': ('c10',), '肩甲': ('c11',), '胸甲': ('c12',), '手套': ('c13',),
    '腰带': ('c14',), '腿甲': ('c15',), '靴子': ('c16',),
    '戒指1': ('c40',), '戒指2': ('c40',), '项链': ('c41',), '勋章': ('c42',),
    '圣物': ('c43',),
    # 武器槽：单手/双手各类型；**副手额外允许盾牌(c27)与法器副手(c26)**
    '主手': WEAPON_CODES,
    '副手': WEAPON_CODES + ('c26', 'c27'),
}

# 能打附魔的**护甲/腰带**槽（圣物不能打附魔 ⇒ 不在内）。
# ★ 旧实现从未给这些槽位生成过附魔池（见 `auto_aug_pool` / `aug_ok` 的注释）。
AUG_SLOTS_ARMOR = ('头部', '肩甲', '胸甲', '手套', '腰带', '腿甲', '靴子')

# ⚠ 下面这张词表**只作兜底**：仅当记录**没有 `cls`** 时才用（实测 c45 组件全都有 cls）。
#   保留它是因为极少数记录可能缺字段 —— 但**主判据永远是类别码**。
COMP_WORDS = {
    '头部': ('头盔', '帽', '护甲'),
    '项链': ('项链',),
    '勋章': ('勋章', '徽章'),
    '戒指1': ('戒指',), '戒指2': ('戒指',),
    '胸甲': ('胸甲', '护甲', '铠甲', '躯干'),
    '腿甲': ('腿甲', '护腿', '护甲'),
    '靴子': ('靴子', '鞋', '足具', '护甲'),
    '手套': ('手套', '护手', '护甲'),
    '肩甲': ('肩甲', '护肩', '肩', '护甲'),
    '腰带': ('腰带', '护甲'),
    '主手': ('武器', '剑', '斧', '锤', '矛', '枪', '弩', '匕首', '权杖'),
    '副手': ('武器', '法器副手', '盾牌', '剑', '斧', '锤', '匕首'),
}


def comp_ok(slot, gid, base_gid=None):
    """装备位合法性判定（**组件与附魔通用**）。

    ★ 2026-09-21 第三次重写 —— 前两次都栽在「按描述文本判」上：
      ① 正则写死 `[（(]用于` ⇒ 命中不到「（**适用于**斧、矛、剑、枪、弩）」⇒ 直接放行；
      ② 改成词表**子串**匹配后又冒第三个洞：`COMP_WORDS['主手']` 里的
         **单字 `'盾'`** 会被「用于**盾牌**，法器副手，胸甲和护肩」命中
         ⇒ **弹性铠甲片（护甲组件）被放行到武器**（实测 `comp_ok('主手','it2860')`
         曾返回 True，两把斧头都镶了它，把武器组件池里的恶毒尖刺挤掉）。

    正解：**不看文本，看类别码** —— `base.l ∈ item.cls`。
      底材的 `l` 是它自己的槽位码；组件/附魔的 `cls` 是它能装的底材码集。
      没有 `base_gid` 时（候选池生成阶段）退化为 `cls ∩ SLOT_CODES[slot]`。
      仅当记录**缺 `cls`** 才回退到文本词表（且**整词**比对，不再用子串）。
    """
    o = _IT.get(gid) or {}
    cls = o.get('cls')
    if cls:
        cs = set(cls)
        if base_gid:
            bc = (_IT.get(base_gid) or {}).get('l')
            if bc:
                return bc in cs
        return bool(cs & set(SLOT_CODES.get(slot, ())))
    # ---------------- 兜底：记录没有 cls ----------------
    b = o.get('b')
    desc = TAGS.get(b, '') if isinstance(b, str) else ''
    m = re.search(r'[（(]([^）)]*)[）)]', desc)
    if not m:
        return False
    pos = m.group(1)
    for w in ('用于', '适用于', '适用'):
        pos = pos.replace(w, '')
    toks = [t.replace('所有', '').replace('近战', '')
            for t in re.split(r'[、，,和以及]+', pos) if t]
    return any(t == w for w in COMP_WORDS.get(slot, ()) for t in toks)


# 固定槽位：--pin 头部=it17247  （可多次；固定后只在该槽位放这一件）
PIN = {}
for _i, _a in enumerate(sys.argv):
    if _a == '--pin' and _i + 1 < len(sys.argv):
        _s, _g = sys.argv[_i + 1].split('=', 1)
        PIN[_s] = _g


def _is_quest(o):
    """任务/剧情/测试物品会伪装成绿装或紫装 —— 塞进装备槽会被游戏静默摘掉（陷阱 #35）。

    实测三个识别面（都要查）：
      1) 标签含 Quest / **Test** —— 「人形凝视者」it13729/it2941 的标签是
         `tagGDX2ItemTest` / `tagItemTest`，只查 Quest 会漏；
      2) 位图路径含 `quest_` / `storyelements` / 或基名形如 `q001_torso`（q + 3 位数字）；
      3) 带 `hideLegs` / `hideShoulders` / `hideFeet` 等字段 —— 那是**剧情换装**专用，
         普通装备不会有。
    """
    bm = (o.get('n') or '').lower()
    if 'quest_' in bm or '/quest' in bm or 'storyelements' in bm or 'endlessdungeon' in bm:
        return True
    if re.match(r'^q\d{3}_', os.path.basename(bm)):
        return True
    for key in ('a', 'b', 'd'):
        t = o.get(key)
        if isinstance(t, str) and ('Quest' in t or 'Test' in t):
            return True
    return any(k.startswith('hide') for k in o)


def rr_score(gid):
    """减抗的**代理分**（候选池排序 / 保底入池用，不是最终评分）。

    为什么单独要有它：候选池是按「抗性总点」截断的，而减抗件恰恰是**抗性点很低、
    实战价值极高**的那一类 —— 实测默认池 14 个槽里只有戒指槽收到 42 件，
    其余 13 槽**一件都没有**。加维度而不修池子，等于白加。
    """
    if not _RR_ON:
        return 0.0
    a, m = rr_dims(gid)
    return (a * max(W_DMG.get('rr_add', 0.0), 1e-9)
            + m * max(W_DMG.get('rr_pct', 0.0), 1e-9))


def _pool_score(gid):
    """候选池排序分：随目标切换口径。
       抗性目标 → 抗性总点；伤害目标 → 输出代理（否则池里根本没有输出装）。
       注意：这里只依赖 res_of/off_of（不依赖 contrib，避免 SKILL_W 尚未定义）。"""
    if GOAL == 'dmg':
        o = off_of(gid)
        return sum(o.get(k, 0.0) * W_DMG.get(k, 0.0) for k in FKEYS)
    return sum(res_of(gid).values())


def auto_pool(topn=None):
    """★ 从 GT 库自动生成候选池：≤ 目标等级 / 非派系 / 非任务 / 有位图 / 槽位合法，
       按 _pool_score() 降序取前 topn 个。
       另外**额外补 topn//2 件绿装**（绿装能带 2 条词缀，折叠词缀后战力远高于裸分，
       只用裸分排序会把它们全挤出去）。
    """
    import os as _os
    topn = topn or int(_os.environ.get('GD_AUTO_TOPN', '45'))
    _max_ilvl = int(_os.environ.get('GD_MAX_ILVL', '52'))
    got = {s: [] for s in SLOTS}
    # ★ `GD_POOL_DMG_TOPN=N`：把「按**输出代理**排的前 N 件」也纳入候选池。
    #   默认池按**抗性**排序，且下面 `sc<=0` 会**直接丢弃零抗性**装备 ——
    #   高伤害件全被挡在门外，等于人为封死「满抗前提下拉满伤害」的搜索空间。
    try:
        _dtop = int(_os.environ.get('GD_POOL_DMG_TOPN', '0') or 0)
    except Exception:
        _dtop = 0

    def _dmg_score(_g):
        _o = off_of(_g)
        return sum(_o.get(_k, 0.0) * W_DMG.get(_k, 0.0) for _k in FKEYS)

    for gid, o in _IT.items():
        k = o.get('k') or o.get('itemLevel') or 0
        if not k or k > _max_ilvl:
            continue
        if o.get('factions') or o.get('repTier'):
            continue      # 派系装备：声望不够时游戏会在载入存档时**整件清空**（见 SKILL §33）
        if _is_quest(o):
            continue
        if o.get('f') not in ('Rare', 'Epic', 'Legendary'):
            continue
        # ★ 「有位图」判据（2026-09-17 放宽）：
        #   原实现只认 `/bitmaps/`，但**圣物**的图标路径是
        #   `items/gearrelics/tier1/tier1_relic_03.png` —— 全库 92 件圣物**一件都不含**
        #   `/bitmaps/`。后果：auto_pool 把圣物整体排除，池里仅存的 6 件蓝圣物
        #   全靠 `POOL_BASE` 硬编码，**18 件绿圣物无处可进** → 「全绿」约束必然失败。
        #   放宽为「路径里含 bitmaps，**或**本身就是 .png/.tex 图标」。
        _n = (o.get('n') or '').lower()
        if '/bitmaps/' not in _n and not _n.endswith(('.png', '.tex')):
            continue
        sc = _pool_score(gid)
        _rrsc = rr_score(gid) if _RR_ON else 0.0
        # ★ 减抗件保底：它的「抗性总点」经常是 0（减抗字段不在 RMAP 里），
        #   会被下面这道 `sc<=0` 整批丢掉 —— 实测全库 57 件减抗物品只有 42 件
        #   挤进戒指池，其余 13 个槽一件都没有。
        if sc <= 0 and _rrsc <= 0 and (_dtop <= 0 or _dmg_score(gid) <= 0):
            continue
        for s in SLOTS:
            if slot_ok(s, gid):
                got[s].append((sc, gid))
                # ★ **成对槽位 / 可互换槽位**必须同时入池：原实现无条件 break，而 SLOTS 里
                #   前一个排在前面 ⇒ 后一个恒为空。已踩两次，症状一样：
                #   ① 武器：「主手」在「副手」前 ⇒ `got['副手']` 恒空，副手只能靠
                #      `POOL_WPN` 那 6 件兜底；一旦 CUR/GD_CUR_JSON 注入副手（哪怕只有 1 件
                #      普通装），`POOL_BASE.get('副手') or POOL_WPN` 的 or-兜底即失效
                #      ⇒ 副手 0 候选 ⇒ `beam_search_np` 的 SUFM 归约零尺寸数组崩溃。
                #   ② 戒指（2026-09-20 修）：「戒指1」在「戒指2」前 ⇒ `got['戒指2']` 恒空
                #      （实测 auto_pool 产出 戒指1=130 / 戒指2=**0**），戒指2 只剩
                #      `POOL_BASE['戒指2']` 那 9 件 50 级时代手工名单，**双戒指优化被冻结**。
                #      两槽 `SLOTDIR` 都是 `['rings']`、`slot_ok` 同判据 ⇒ 池必须同源。
                if s in ('主手', '副手', '戒指1', '戒指2'):
                    continue
                break
    out = {}
    for s, lst in got.items():
        lst.sort(key=lambda x: (-x[0], x[1]))
        pick = [g for _, g in lst[:topn]]
        rares = [g for _, g in lst if (_IT.get(g) or {}).get('f') == 'Rare']
        for g in rares[:max(4, topn // 2)]:
            if g not in pick:
                pick.append(g)
        if _dtop:
            _dl = sorted(lst, key=lambda x: (-_dmg_score(x[1]), x[1]))
            for g in [g for _, g in _dl[:_dtop]]:
                if g not in pick:
                    pick.append(g)
        # ★ 减抗件保底入池（数量少但价值高，且抗性排序永远排在末尾会被 topn 截断）
        if _RR_ON:
            _rl = sorted((rr_score(x[1]), x[1]) for x in lst)
            for _rs, g in reversed(_rl):
                if _rs > 0 and g not in pick:
                    pick.append(g)
        out[s] = pick
    return out


def auto_wpn_comp_pool(topn=None):
    """★ 自动生成**武器槽**的镶嵌物候选池（按**输出代理**排序，与 goal 无关）。

    为什么必须有（陷阱 #87）
    ----------------------
    武器槽的候选原先只有**硬编码两项** `POOL_CWPN = ['it2878', 'it2849']`；
    而通用的 `auto_comp_pool()` 在 `--goal super` 下按**抗性总点**排序、并且
    `if sc > 0` **把零抗性的纯输出件全丢** ⇒ 逐槽只剩 1~3 项、**主手只剩 1 项**（实测）。
    合起来：搜索在「武器镶嵌物」这一维上**几乎没有搜索空间**。

    实测代价（Sam lv73）：武器组件池只有 2 项，而**真正合法可用**（`cls` 含武器码
    且等级要求 `k` ≤ 73）的有 **25 项**。这一次硬编码的 2 项里恰好有最优解
    （`it2878` 恶毒尖刺），属于**运气**，不是设计。

    判据（与 `comp_ok` 同源）：`cls ∩ WEAPON_CODES ≠ ∅` 且 `k ≤ GD_MAX_ILVL` 且
    记录可解析（否则方案里会写进不存在的路径）。
    """
    import os as _os
    topn = topn or int(_os.environ.get('GD_COMP_WPN_TOPN', '40'))
    _W = set(WEAPON_CODES)
    cands = []
    for gid, o in _IT.items():
        if 'craftingparts/components' not in (o.get('n') or ''):
            continue
        k = o.get('k') or o.get('itemLevel') or 0
        if not k or k > _MAX_ILVL:
            continue
        if not (set(o.get('cls') or []) & _W):
            continue
        if not M.resolve_comp(o)[0]:
            continue
        cands.append((_comp_off_score(gid), gid))
    cands.sort(key=lambda x: (-x[0], x[1]))
    pick = [g for _, g in cands[:topn]]
    return {'主手': list(pick), '副手': list(pick)}


def _comp_off_score(gid):
    """镶嵌物的**输出代理**分（与 `goal` 无关）。

    `_pool_score()` 在 `--goal super` 下退成「抗性总点」，会把纯输出组件判 0 分 ——
    那正是陷阱 #87 的成因。这里固定用**输出代理**，并给「授予技能」的组件一点底分
    （很多武器组件的价值主要在 `itemSkillName` 那条 WPS 上）。
    """
    o = off_of(gid)
    s = sum(o.get(k, 0.0) * W_DMG.get(k, 0.0) for k in FKEYS)
    it = _IT.get(gid) or {}
    if it.get('itemSkillName'):
        s += 1.0
    return s


def auto_comp_pool(topn=None):
    """★ 从 GT 库自动生成镶嵌（组件）候选池：位置合法 + 等级够 + 按目标口径排序。
       覆盖 GDX3 新增组件（硬编码 POOL_COMP 是 50 级时代手工挑的，只有十几个）。"""
    import os as _os
    topn = topn or int(_os.environ.get('GD_COMP_TOPN', '12'))
    # ★ 先按一次全库扫描筛出「合格组件」（等级够 + 槽位 + 记录可解析 + 有分），
    #   再对每个槽位只做一次位置过滤。
    #   旧写法把 `for gid,o in _IT.items()` 套在 `for slot` 里面 → 13 槽 × 1.7 万条
    #   = 22 万次循环，其中每次还带 4 次 `exists()` 记录查找（实测 0.35 s/进程）。
    cands = []
    for gid, o in _IT.items():
        if 'craftingparts/components' not in (o.get('n') or ''):
            continue
        k = o.get('k') or o.get('itemLevel') or 0
        if not k or k > _MAX_ILVL:
            continue
        # 记录名解析不出来的组件一律剔除 —— 否则方案里会写进一个不存在的
        # records/items/materia/... 路径，游戏里等于「没镶嵌」
        # （全库 107 个组件里有 26 个位图名与记录名不一致，见 gd_map.COMP_ALIAS）
        if not M.resolve_comp(o)[0]:
            continue
        sc = _pool_score(gid)
        if sc > 0:
            cands.append((sc, gid))
    out = {}
    for slot in list(COMP_SLOT) + ['腿甲', '靴子']:
        lst = [(sc, gid) for sc, gid in cands if comp_ok(slot, gid)]
        lst.sort(key=lambda x: (-x[0], x[1]))
        out[slot] = [g for _, g in lst[:topn]]
    return out


# ★ `--compare` 是**纯对比模式**：只对已有方案做同口径打分，既不做搜索、
#   也不需要任何候选池。旧实现照样把三个自动池全建一遍 → 一次 --compare 白花 3.3 秒。
_CMP_ONLY = '--compare' in sys.argv
if _CMP_ONLY:
    AUTO_POOL, AUTO_COMP = {}, {}
else:
    with TM.span('opt:auto_pool'):
        AUTO_POOL = auto_pool()
    with TM.span('opt:auto_comp'):
        AUTO_COMP = auto_comp_pool()
for _s, _lst in AUTO_POOL.items():
    POOL_BASE.setdefault(_s, [])
    POOL_BASE[_s] = list(dict.fromkeys(list(POOL_BASE[_s]) + _lst))
for _s, _lst in AUTO_COMP.items():
    if not _lst:
        continue
    POOL_COMP.setdefault(_s, [])
    POOL_COMP[_s] = list(dict.fromkeys(list(POOL_COMP[_s]) + _lst))
# ★★ 2026-09-22（陷阱 #87）：**武器槽**另外吃一份「输出代理排名」的自动池 ——
#   否则武器组件只有硬编码那 2 项（`auto_comp_pool` 在 `--goal super` 下按抗性排、
#   且把零抗性的纯输出件丢光）。
for _s, _lst in auto_wpn_comp_pool().items():
    if not _lst:
        continue
    POOL_COMP.setdefault(_s, [])
    POOL_COMP[_s] = list(dict.fromkeys(list(POOL_COMP[_s]) + _lst))

# ★ 把「当前存档里正在穿的 12 件」纳入池子 —— 保证优化结果**不劣于现状**
CUR = {
    '头部': ['it17247'], '项链': ['it848'], '胸甲': ['it614'], '腿甲': ['it11188'],
    '靴子': ['it1156'], '手套': ['it535'], '戒指1': ['it972'], '戒指2': ['it987'],
    '腰带': ['it1048'], '肩甲': ['it8266'], '勋章': ['it14176'], '圣物': ['it14691'],
}
# ★ 存档覆盖：`GD_CUR_JSON='{"头部":["it17247"],...}'` 用**当前存档**的装备
#   替换上面写死的表（由 tools/save_plan.py 生成）。
_CUR_ENV = os.environ.get('GD_CUR_JSON', '')
if _CUR_ENV:
    try:
        CUR = {k: [g for g in v if g] for k, v in json.loads(_CUR_ENV).items()}
    except Exception as _e:
        print('  ⚠ GD_CUR_JSON 解析失败: %s' % _e)

# ★ v3 解绑：`GD_NO_CUR=1` → **不把「当前穿着」注入候选池**。
#   旧行为隐含「结果不劣于现状」，会限制换流派（比如从物理向换鸦人冰冷向时，
#   旧装备一直挂在池子里挤占名额）。做「最优方案」时就该关掉。
if os.environ.get('GD_NO_CUR', '0') != '0':
    CUR = {}
for _s, _lst in CUR.items():
    POOL_BASE.setdefault(_s, [])
    POOL_BASE[_s] = list(dict.fromkeys(list(POOL_BASE[_s]) + _lst))


# ★ 装备需求等级闸门：剔除「目标等级不足以装备」的候选件。
#   手工池（POOL_WPN 里的阵营武器 it729 军团切刀、POOL_LEG/POOL_BASE 里的阵营件）
#   只按 itemLevel 过滤、从不检查 l（需求等级），会选进游戏里戴不上、无法生效的件。
#   这里统一按 l(cNN) 解析出的需求等级把关，保证推荐的全部可装备。
for _s in list(POOL_BASE.keys()):
    POOL_BASE[_s] = [g for g in POOL_BASE[_s] if _req_lvl(g) <= _MAX_ILVL]
POOL_WPN = [g for g in POOL_WPN if _req_lvl(g) <= _MAX_ILVL]

# ★ v3 属性闸门（2026-09-17）：`GD_ATTR_BUDGET='{"physique":818,"cunning":312}'`
#   给了面板属性上限后，剔除**穿不上**的候选件。
#
#   为什么需要：以前优化器只按「需求等级 ≤ 目标等级」筛件，但装备还有**属性需求**
#   （体格/狡诈/精神），而属性来自「加点 + 精通 + 装备本身」。
#   不筛的话会选出纸面很强、实际穿不上的件（游戏载入时把它们标 attached=False，
#   不报错、只是不生效）。
#   配合 gd_alloc.attribute_plan() 构成收敛循环：
#     跑一次搜索 → 算属性分配 → 用该分配做闸门重跑 → 直到稳定。
_AB = os.environ.get('GD_ATTR_BUDGET', '')
if _AB:
    try:
        _budget = {k: float(v) for k, v in json.loads(_AB).items()}
        from . import req as _R

        # ★ 与 `gd_alloc.attribute_plan()` **同口径**：estimated 件的需求乘 1.10 安全余量。
        #   旧版这里用原始需求，而加点侧用含余量需求 → 两侧判据不一致，
        #   会出现「闸门放行、加点却超预算」的假可行（实测 100 级方案超 7 点）。
        def _req_ok(_g):
            _r = _R.req(_g)
            _safe = 1.10 if _r.get('confidence') == 'estimated' else 1.0
            for _k, _cap in _budget.items():
                if _r.get(_k, 0) * _safe > _cap:
                    return False
            return True

        _before = sum(len(v) for v in POOL_BASE.values()) + len(POOL_WPN)
        for _s in list(POOL_BASE.keys()):
            POOL_BASE[_s] = [g for g in POOL_BASE[_s] if _req_ok(g)]
        POOL_WPN = [g for g in POOL_WPN if _req_ok(g)]
        _after = sum(len(v) for v in POOL_BASE.values()) + len(POOL_WPN)
        print('  [属性闸门] 面板上限 %s → 候选 %d → %d（剔除 %d 件穿不上的）'
              % (_budget, _before, _after, _before - _after))
    except Exception as _e:
        print('  ⚠ 属性闸门未生效: %s' % _e)

# ★ 派系闸门（2026-09-17 新增）：`GD_NO_FACTION=1` 时把所有候选池里的派系件剔除。
#   为什么需要：`auto_pool()` 内部**已经**过滤 `factions` / `repTier`，但**手写池**
#   （POOL_BASE / POOL_LEG / POOL_WPN）没有走那条路 —— 65 级满抗方案实测混进
#   3 件 `repTier=tagFactionStateFriend2`（尊重）的派系件：it521 罗瓦里裹足 /
#   it535 收割者护手 / it729 军团切刀。声望不够时游戏会在载入存档时**整件清空**
#   （不报错、只是该格变空，见 SKILL §33）。
#   默认关（保持既有行为）；要一份"声望无关、闭眼可穿"的方案就开它。
if os.environ.get('GD_NO_FACTION', '0') != '0':
    def _fac_bad(_g):
        _o = _IT.get(_g) or {}
        return bool(_o.get('factions') or _o.get('repTier'))
    for _s in list(POOL_BASE.keys()):
        POOL_BASE[_s] = [g for g in POOL_BASE[_s] if not _fac_bad(g)]
    POOL_WPN = [g for g in POOL_WPN if not _fac_bad(g)]


# ★ 伤害转化闸门（`GD_CONV_KEEP` 白名单）—— **2026-09-19 已废弃并移除**
#
#   旧实现：`conversionOutType` 不在白名单里的候选件**整体剔除**。
#   为什么当初需要它：伤害代理是线性加权和，看不见转化 —— 一件把主伤害转出去的装备
#   在代理里既不加分也不扣分（它的 `offensivePierceModifier` 可能还很高），
#   实际却把主伤害丢进了加成只有零头的类型。实测（Sam lv63）：掺一件
#   光之誓 `d202_ring`（额外 Pierce→Fire 25%）→ 普攻 32,804 → 25,040/秒，
#   而代理判它更好（823 → 1025）。
#
#   为什么现在能删：换成**逐件真估值**（见下 `conv_delta`）——
#   白名单只能删候选，给不出「这件转化到底亏多少」的数字，还会误杀
#   「转出去但转得很划算」的件。废除后 `GD_CONV_KEEP` 环境变量不再有任何作用。
_CONV_KEEP = ''            # 保留名字，避免老脚本 `os.environ` 检查报错


def _lim_of(gid):
    """物品描述里括号内的「用在哪里」文本（去掉「用于/适用于」前缀）"""
    o = _IT.get(gid) or {}
    b = o.get('b')
    desc = TAGS.get(b, '') if isinstance(b, str) else ''
    m = re.search(r'[（(]([^）)]*)[）)]', desc)
    if not m:
        return ''
    return m.group(1).replace('用于', '').replace('适用于', '').replace('适用', '')


def aug_ok(slot, gid, base_gid=None):
    """附魔位置合法性 —— ★ 2026-09-21 改为与组件**同一套类别码判据**。

    旧实现只认 `用于` 文本里的 5 个词（武器/项链/戒指/勋章），**其余一律 `return False`**
    ⇒ **护甲槽永远拿不到任何附魔**。而护甲附魔实测 38 条、**全是防御向（零输出字段，
    穿刺/活力/虚化抗为主）** ⇒ 搜索器想补抗性时**没有合法手段**，
    只好把防御组件塞进武器（并借 `comp_ok` 里单字 `'盾'` 的漏洞放行）——
    这正是「两把斧头都镶弹性铠甲片」的完整成因。

    现在：`base.l ∈ 附魔.cls`，与组件完全一致；无 `base_gid` 时用 `SLOT_CODES`。
    """
    return comp_ok(slot, gid, base_gid)


def auto_aug_pool(topn=None):
    """★ 自动附魔候选池：/enchants/ 下、k ≤ 目标等级、位置合法、按目标口径排序。

    ★ 2026-09-21 **补上护甲槽**。原实现有两个叠加的洞：
      ① 循环只有 `('项链','戒指1','戒指2','勋章','主手','副手')` —— **护甲槽压根没进来**；
      ② 再叠 `aug_ok()` 对护甲槽恒 `False`。
      两个洞合起来 ⇒ 护甲附魔从未进入搜索空间。
      新增的护甲槽**不按分数过滤**（护甲附魔全是防御向，`--goal dmg` 下得分为 0，
      照旧过滤会把它们全筛掉）；原有 6 个槽位的口径**保持不变**。
    """
    import os as _os
    topn = topn or int(_os.environ.get('GD_AUG_TOPN', '8'))
    # ★ 护甲槽**单独用一个更大的上限**：护甲附魔全是**单条抗性**（总分很低），
    #   照 `GD_AUG_TOPN=8` 截断会把「拜斯迈的沙语（穿刺抗+15）」这类**专补某一维**的
    #   直接挤出去 —— 而那正是补满抗唯一需要的那条。Sam lv73 全库只有 20 条（k=70）⇒ 不截断。
    armor_topn = int(_os.environ.get('GD_AUG_TOPN_ARMOR', '32'))
    cands = []
    for gid, o in _IT.items():                 # 同样：全库只扫一次，别套在 slot 循环里
        if '/enchants/' not in (o.get('n') or ''):
            continue
        k = o.get('k') or o.get('itemLevel') or 0
        if not k or k > _MAX_ILVL:
            continue
        cands.append((_pool_score(gid), gid))
    out = {}
    _old = ('项链', '戒指1', '戒指2', '勋章', '主手', '副手')
    for slot in _old + AUG_SLOTS_ARMOR:
        lst = [(sc, gid) for sc, gid in cands
               if aug_ok(slot, gid) and (sc > 0 or slot in AUG_SLOTS_ARMOR)]
        lst.sort(key=lambda x: (-x[0], x[1]))
        out[slot] = [g for _, g in lst[:armor_topn if slot in AUG_SLOTS_ARMOR else topn]]
    return out


if _CMP_ONLY:
    AUTO_AUG = {}
else:
    with TM.span('opt:auto_aug'):
        AUTO_AUG = auto_aug_pool()


def choices(slot):
    out = []
    if slot in PIN:
        bases = [PIN[slot]]
    elif slot in ('主手', '副手'):
        # ★ 武器槽：POOL_WPN 是**默认武器池**，必须并进来，而不是当 `or` 兜底。
        #   否则「POOL_BASE 里恰好有 1 件」（CUR / GD_CUR_JSON 注入的当前武器）
        #   是 truthy ⇒ `or POOL_WPN` 不触发 ⇒ 6 件默认池被整体屏蔽，
        #   候选可能只剩 0（再被 ALLOW_NORMAL 滤掉普通装）→ 束搜索崩。
        bases = list(dict.fromkeys(list(POOL_BASE.get(slot) or []) + list(POOL_WPN)))
    else:
        bases = POOL_BASE.get(slot) or POOL_WPN
    if slot in POOL_COMP:
        comps = POOL_COMP[slot]
    elif slot in ('主手', '副手'):
        comps = POOL_CWPN
    else:
        comps = [None]
    # ★ 武器槽：自动池（`AUTO_COMP`）会给 `POOL_COMP` 加 `主手/副手` 键 ⇒ 上面那个
    #   `if slot in POOL_COMP` 会**遮住** `POOL_CWPN` 分支 ⇒ 硬编码武器组件池（含恶毒尖刺）
    #   被整体挤掉。这里显式并回来，任何情况都保证武器池在候选里。
    if slot in ('主手', '副手'):
        comps = list(dict.fromkeys(list(comps) + list(POOL_CWPN)))
    # 附魔（augment）：护甲槽附魔 70 级才解锁；武器/首饰/勋章附魔 50 级起可用
    if _NO_AUG:
        augs = [None]
    elif _MAX_ILVL < AUG_UNLOCK_LVL:
        augs = [None] + (list(AUTO_AUG.get(slot) or [])
                         if slot in AUG_SLOTS_UNDER_70 else [])
    else:
        # ★ 旧实现直接 `JA`/`WA`/`[None]` 三分 —— 有两处硬伤：
        #   ① 护甲槽 `[None]` ⇒ 护甲附魔（38 条，**全是防御向**）从未被搜索；
        #   ② `JA` 被当作「首饰通用池」也喂给**勋章** —— 但 JA 的 `cls=['c40','c41']`
        #      **只覆盖 戒指+项链，不含 c42（勋章）** ⇒ 计划里勋章上的附魔是**非法**的
        #      （`auto_aug_pool` 实测：勋章有 **63 条专属附魔** `cls=['c42']`）。
        #   现在统一以**自动池**为主、旧硬编码池并进来兜底；
        #   非法项由下面 `aug_ok(slot, a, b)` 的**按底材**判定逐条剔除，不会漏网。
        _legacy = JA if slot in ('项链', '戒指1', '戒指2', '勋章') else (
            WA if slot in ('主手', '副手') else [])
        # ★ 必须保留 `None`（不打附魔也是合法状态）：否则一旦池里每条都被
        #   `aug_ok(slot, a, b)` 按底材否掉，该槽位会**一个候选都没有** ⇒ 搜索崩。
        augs = list(dict.fromkeys([None] + list(AUTO_AUG.get(slot) or []) + list(_legacy)))
    for b in bases:
        if slot not in PIN and _req_lvl(b) > _MAX_ILVL:
            continue
        if not slot_ok(slot, b):
            continue
        for c in comps:
            if c and not comp_ok(slot, c, b):
                continue
            for a in augs:
                # ★ 附魔也必须按**实际底材**判（多码槽位如 主手/副手 才分得清）
                if a and not aug_ok(slot, a, b):
                    continue
                out.append((b, c, a))
    return out


def ev(sol):
    r, o = defaultdict(float), defaultdict(float)
    for slot, (b, c, a, _p, _s) in sol.items():
        for gid in (b, c, a):
            if not gid:
                continue
            for t, v in res_of(gid).items():
                r[t] += v
            for k, v in off_of(gid).items():
                o[k] += v
        for _ag in (_p, _s):   # 折叠该槽位「已存储」的最优前缀/后缀（来自候选向量，重载时自洽）
            if _ag:
                for t, v in res_of(_ag).items():
                    r[t] += v
                for k, v in off_of(_ag).items():
                    o[k] += v
    return r, o


# ★ 本轮目标 = 纯覆盖率最大化（用户要求「尽可能所有抗性拉满」）→ 取消抗性加权偏置
W_COVER = {t: 1.0 for t in TYPES}


def rar_of(sol):
    """(绿装数, 紫装数)"""
    g = sum(1 for s, (b, c, a, _p, _s) in sol.items() if (_IT.get(b) or {}).get('f') == 'Rare')
    l = sum(1 for s, (b, c, a, _p, _s) in sol.items() if (_IT.get(b) or {}).get('f') == 'Legendary')
    return g, l


# 紫装自带的 augmentSkillName/Level —— 对「狼人穿刺流」直接强化，必须计入评分。
# 数值是「等效抗性点」的主观折算：狼人形态 1 级 ≈ 12 点。
SKILL_W = {
    'tagGDX3Class10SkillName01A': 12.0,   # 狼人形态（主形态）
    'tagGDX3Class10SkillName01B': 8.0,    # 贪噬（ADCtH 吸血）
    'tagGDX3Class10SkillName01C': 6.0,    # 血莽（暴击刷 CD）
    'tagGDX3Class10SkillName12A': 7.0,    # 野兽形态
    'tagGDX3Class10SkillName10A': 5.0,    # 战意澎湃（暴击回血）
    'tagGDX3Class10SkillName11A': 5.0,    # 不羁狂怒（半血吸收）
    'tagGDX3Class10SkillName13A': 4.0,    # 战争兵器
    'tagClass04SkillName00': 6.0,         # 双刃（夜刃双持解锁/穿刺）
}
SKILL_K = float(os.environ.get('GD_SKILL_K', '0.6'))   # 技能分折算系数（可用 GD_SKILL_K 覆盖）
# ★ 为什么需要这个开关：装备自带的 +精通技能等级（如「野兽形态 +2」「贪噬 +2」）对本 build
#   是核心收益，但按 0.6 折算时 18 点技能分只值 10.8 分，而「省下 25 点抗性溢出」值 25 分
#   ⇒ 优化器会拿技能换溢出。实测就是这样丢掉了一件给 3 个狼人系技能的胸甲。
# ★ 伤害目标下 +技能 的价值远大于「等效抗性点」——狼人形态每级直接抬爪伤
#   （16→19 级：武器伤害% 150→166、固定穿刺 237→309），用同一张表的 12 分/级
#   会被 1400 分的伤害主分淹没，等于白给。故单独一套量纲（1 级 ≈ 60~90 分）。
SKILL_W_DMG = {
    'tagGDX3Class10SkillName01A': 90.0,   # 狼人形态
    'tagGDX3Class10SkillName01B': 40.0,   # 贪噬
    'tagGDX3Class10SkillName01C': 30.0,   # 血莽
    'tagGDX3Class10SkillName12A': 50.0,   # 野兽形态
    'tagGDX3Class10SkillName10A': 20.0,   # 战意澎湃
    'tagGDX3Class10SkillName11A': 20.0,   # 不羁狂怒
    'tagGDX3Class10SkillName13A': 25.0,   # 战争兵器
    'tagGDX3Class10SkillName09A': 60.0,   # 血源苏醒（专属）
    'tagClass04SkillName00': 30.0,        # 双刃
}
if GOAL == 'dmg':
    SKILL_W = SKILL_W_DMG
    SKILL_K = float(os.environ.get('GD_SKILL_K', '1.0'))

# ★ v3：形态流派的技能权重 —— 从该形态的**核心技能树**（含 grantedSkills 与
#   skillDependancy 递归）生成，取代上面那张写死狼人系的手工表。
#   量纲换算：SKILL_W_DMG ≈ 20 × SKILL_W（前者"1 级 ≈ 60~90 分"、后者"1 级 ≈ 3~5 点抗性值"）。
if ARCH and ARCH.get('core_skills'):
    try:
        _SKLIB = json.load(open(os.path.join(HERE, 'skills.json'), encoding='utf-8'))
    except Exception:
        _SKLIB = {}
    _sw = {}
    for _rec in ARCH['core_skills']:
        _d = _SKLIB.get(_rec) or {}
        _tag = _d.get('tag')
        if not _tag:
            continue
        if _d.get('kind') == 'shapeshift':
            _w = 90.0                     # 形态本体：最贵
        else:
            _tier = _d.get('tier') or 9
            _w = max(20.0, 55.0 - 5.0 * _tier)
        _sw[_tag] = max(_sw.get(_tag, 0.0), _w)
    # ★★ 2026-09-20：**主输出技能必须是权重最高的**。
    #   上面那条 `55 - 5×tier` 对**辅助**技能成立（越低阶越通用、越该点满），
    #   但它对**主输出**是**反的** —— 越核心的输出技能往往越靠后（tier 高）⇒
    #   分值越低。实测（avalanche）：主输出「雪崩」(tier 5) 只有 30 分，
    #   而被动的「双刃」「无情终章」「气爆」各 50 分 ⇒ 优化器会为了辅助技能
    #   的 +等级 放弃主输出的 +等级，与「围绕雪崩」的意图完全相反。
    #   故：形态可用 `primary_skill`（tag / 记录名 / 文件名 / 中文名）**显式**声明
    #   主输出，它拿全表最高权重；**不声明 ⇒ 完全走旧公式（零漂移）**。
    _ps = ARCH.get('primary_skill')
    if _ps:
        for _rec in ARCH['core_skills']:
            _d = _SKLIB.get(_rec) or {}
            _t, _n = _d.get('tag'), _d.get('name')
            if _ps in (_t, _n, _rec, os.path.basename(_rec),
                       os.path.basename(_rec)[:-4]):
                _sw[_t] = 120.0           # 主输出：全表最高（压过形态本体的 90）
                break
    if _sw:
        SKILL_W_DMG = _sw
        SKILL_W = {k: v / 20.0 for k, v in _sw.items()}
        if GOAL == 'dmg':
            SKILL_W = SKILL_W_DMG
# 紫装使用惩罚：束搜索是贪心的，早期槽位（还没攒到抗性时）会倾向于选数值漂亮的紫装，
# 把名额浪费掉。给每件紫装扣一点分，让名额留给真正划算的槽位。
LEG_PEN = 6.0
if '--leg-pen' in sys.argv:
    LEG_PEN = float(sys.argv[sys.argv.index('--leg-pen') + 1])


def skill_score(sol):
    sc = 0.0
    for s, (b, c, a, _p, _s) in sol.items():
        for gid in (b, c, a):
            if not gid:
                continue
            o = _IT.get(gid) or {}
            for n in (1, 2, 3, 4):
                t = o.get('augmentSkillName%d' % n)
                lv = o.get('augmentSkillLevel%d' % n)
                if isinstance(t, str) and t in SKILL_W and isinstance(lv, (int, float)):
                    sc += SKILL_W[t] * lv
    return sc


def key(sol):
    r, o = ev(sol)
    cover = sum(min(r[t], NEED[t]) * W_COVER[t] for t in TYPES)
    # ★ 用户目标：「尽可能所有抗性拉满」⇒ 主序 = **最短板**（各项覆盖率的最小值），次序 = 总覆盖
    worst = min(min(r[t] / NEED[t], 1.0) for t in TYPES if NEED[t])
    pen = sum(W_PEN[k] * max(0.0, FLOOR[k] - o[k]) for k in FLOOR)
    sk = skill_score(sol)
    green, leg = rar_of(sol)
    # 同分时：优先紫装少（更贴近自然获取），其次绿装多
    return (round(worst * 1000 + cover * 0.05 - pen + SKILL_K * sk - LEG_PEN * leg, 2),
            cover, round(sk, 1), -leg, green, o['OA'])


# ---------------------------------------------------------------- 稀有度约束
# 目标：主绿 + 副蓝 2~3 + 少量紫 1~2（基准 12 件基装；镶嵌/附魔不计入稀有度）
MIN_LEG, MAX_LEG = 1, 2
MIN_EPIC, MAX_EPIC = 2, 3
ALLOW_NORMAL = False          # 黄/白严格劣于绿，不入池


def _arg(name, dflt):
    """取命令行数值参数（缺省用 dflt 的类型）"""
    return type(dflt)(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else dflt


MIN_LEG = _arg('--min-leg', MIN_LEG)
MAX_LEG = _arg('--max-leg', MAX_LEG)
MIN_EPIC = _arg('--min-epic', MIN_EPIC)
MAX_EPIC = _arg('--max-epic', MAX_EPIC)
K = _arg('--beam', 700)
RESTART = _arg('--restart', 1)
JOBS = _arg('--jobs', 0)          # 0 = 自动（按 CPU 与内存预算）
# GOAL 已在文件头部解析（候选池构建需要它），此处不重复解析
if not _CMP_ONLY:
    with TM.span('opt:best_affixes'):
        compute_best_affixes()  # 当前目标下每槽最优词缀（前缀+后缀）；--compare 用不到
OUT = sys.argv[sys.argv.index('--out') + 1] if '--out' in sys.argv else '_tmp_opt_bal.json'
if not os.path.isabs(OUT):
    OUT = os.path.join(HERE, OUT)

NT, NF = len(TYPES), len(FKEYS)
RAR_CN = {'Rare': '绿', 'Legendary': '紫', 'Epic': '蓝', 'Magical': '黄', 'Common': '白'}


def _rar(gid):
    return (_IT.get(gid) or {}).get('f')


# ---------------------------------------------------------------- 候选向量化
def _can_affix(gid):
    """该底材能否带词缀 —— GD 规则：`maxAffixes`
       紫(Legendary) 全库 1532 件 maxAffixes=0、蓝(Epic) 1647 件为 0 ⇒ **只能靠固定数值**；
       绿(Rare) 绝大多数是 2（2336 件），只有 410 件固定 roll 为 0。
       ★ 旧代码对**所有**候选都折叠了「该槽最优前缀+后缀」，等于给紫/蓝装凭空加了词缀，
         既高估它们的分，又会让落档方案给紫装写词缀（游戏可能拒收整件，见陷阱 #40）。
    """
    o = _IT.get(gid) or {}
    f = o.get('f')
    if f not in ('Rare',):
        return False
    ma = o.get('maxAffixes')
    return ma != 0          # None = GT 未记录，按可带词缀处理


def slot_cands(slot):
    """槽位候选 -> [(tri, rvec, ovec, skill, green, epic, leg)]（每个候选只算一次）"""
    out = []
    for b, c, a in choices(slot):
        rt = [0.0] * NT
        ot = [0.0] * NF
        sk = 0.0
        for gid in (b, c, a):
            if not gid:
                continue
            r1, o1, s1 = contrib(gid)
            for i in range(NT):
                rt[i] += r1[i]
            for i in range(NF):
                ot[i] += o1[i]
            sk += s1
        _p = BEST_PRE.get(slot) if _can_affix(b) else None
        _s = BEST_SUF.get(slot) if _can_affix(b) else None
        for _ag in (_p, _s):        # 折叠该槽位最优词缀（仅绿装；见 _can_affix）
            if _ag:
                _ar, _ao, _as = contrib(_ag)
                for i in range(NT):
                    rt[i] += _ar[i]
                for i in range(NF):
                    ot[i] += _ao[i]
                sk += _as
        f = _rar(b)
        gr, ep, lg = (f == 'Rare'), (f == 'Epic'), (f == 'Legendary')
        if not (gr or ep or lg) and not ALLOW_NORMAL:
            continue
        out.append(((b, c, a, _p, _s), tuple(rt), tuple(ot), sk, int(gr), int(ep), int(lg)))
    return out


def _skyline_slow(rows):
    """**参考实现**（旧的 O(n²) 版）—— 只用于 A/B 与自检对拍，生产走 `_skyline`。

    语义：丢弃「∃ 另一行 j≠i 各维全面 ≥ 且至少一维严格 >」的行 i（含 j 自身已被丢弃的情形）。
    """
    n = rows.shape[0]
    if n < 2:
        return _np.ones(n, bool)
    keep = _np.ones(n, bool)
    for i in range(n):
        if not keep[i]:
            continue
        ge = (rows >= rows[i]).all(axis=1)
        gt = (rows > rows[i]).any(axis=1)
        ge[i] = False
        if (gt & ge).any():
            keep[i] = False
    return keep


def _skyline(rows):
    """逐槽**支配剪枝**：丢弃「被同槽另一件各维全面 ≥ 且至少一维严格 >」的候选。

    安全性：本优化器的目标在每个维度上单调不减（抗性总和 / 最短板比例 / 输出代理 / 技能分），
    多出来的抗性最坏也只是被 NEED 截掉、不会变差 ⇒ 被支配的候选**永不出现在最优解里**。
    所以这是**精确剪枝**，不是近似。

    收益：束搜索每步 = 束宽 × 候选数 的笛卡尔积，候选数直接决定耗时
    （12 槽原本 3077 件，剪枝后通常只剩几百件）。

    ★★ 2026-09-22 性能（陷阱 #94）：旧实现是 **O(n²)** —— 对每个候选扫**全部 n 行**。
      实测 lv76 / `--extreme` 的原始候选可达 **61,510/槽**（= AUTO_TOPN 150 × COMP_TOPN 24
      × AUG_TOPN 14），单槽 `_skyline` 要 **~100 s**，14 槽把 `gd auto` 拖到 **15 分钟**，
      而且**只有 1 个进程 1 个核**在跑（用户实测截图报「怎么这么慢」）。
      改法三步（**结果逐位不变**，见下）：
        ① 只与**已保留**的行比 —— 被丢弃的行 j 若能支配 i，则支配 j 的那行也支配 i
           （传递性），所以「只比已保留」与「比全部」等价；
        ② **字典序降序**预排序 ⇒ 支配者一定排在被支配者之前，① 的等价性才成立；
        ③ 快速路径 `(ri > runmax).any()`：某维刷新了已保留集的最大值 ⇒ 不可能被支配 ⇒ 直接留；
           慢路径按 512 行分块比较并**命中即停**（多数候选被最前面几行就否掉）。
      `GD_SKYLINE=slow` 可强制走旧实现做 A/B（未设 ⇒ 零漂移）。
    """
    n = rows.shape[0]
    if n < 2:
        return _np.ones(n, bool)
    if os.environ.get('GD_SKYLINE', '').strip().lower() in ('slow', 'old', '0'):
        return _skyline_slow(rows)
    dm = rows.shape[1]
    order = _np.lexsort(tuple(-rows[:, d] for d in range(dm - 1, -1, -1)))
    keep = _np.zeros(n, bool)
    K = _np.empty_like(rows)
    runmax = _np.full(dm, -_np.inf)
    cnt = 0
    for idx in order:
        ri = rows[idx]
        if cnt and not (ri > runmax).any():
            hit = False
            for a0 in range(0, cnt, 512):
                # ★ 必须夹到 cnt —— `K` 是 `empty_like`，越界切片会读到**未初始化的行**，
                #   那些垃圾值可能凑巧 ≥/> ri（负值数据上尤其容易），造成**误删**。
                blk = K[a0:min(a0 + 512, cnt)]
                if (((blk >= ri).all(axis=1)) & ((blk > ri).any(axis=1))).any():
                    hit = True
                    break
            if hit:
                continue
        K[cnt] = ri
        cnt += 1
        keep[idx] = True
        _np.maximum(runmax, ri, out=runmax)
    return keep


def prune_cands(cs):
    """对单槽候选表做支配剪枝；向量 = (9 条抗性, 输出各维…, 技能分)"""
    if not _PRUNE or _np is None or len(cs) < 3:
        return cs
    rows = _np.array([list(c[1]) + list(c[2]) + [c[3]] for c in cs], _np.float64)
    keep = _skyline(rows)
    out = [c for c, k in zip(cs, keep) if k]
    return out or cs


_PRUNE_MEMO = {}


def _prune_key(rows, cs):
    """内容指纹：向量 + gid + 抗性需求 + 类型序。物品库/等级/名额一变就自动换 key。"""
    try:
        import hashlib
        h = hashlib.sha1()
        h.update(_np.ascontiguousarray(rows, dtype=_np.float64).tobytes())
        h.update(repr(sorted(NEED.items())).encode('utf-8'))
        h.update(','.join(TYPES).encode('utf-8'))
        for _c in cs:                       # ★ 必须含 gid：同一组向量配不同 gid 会串味
            h.update(('%s|' % (_c[0],)).encode('utf-8'))
        return h.hexdigest()[:20]
    except Exception:
        return None


def _prune_cache_path(key):
    from . import paths as _P
    return _P.CACHE_DIR / 'prune' / ('%s.pkl' % key)


def _prune_disk_get(key):
    import pickle
    try:
        p = _prune_cache_path(key)
        if p.exists():
            return pickle.loads(p.read_bytes())
    except Exception:
        pass
    return None


def _prune_disk_put(key, keep):
    import pickle
    try:
        p = _prune_cache_path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix('.tmp')
        tmp.write_bytes(pickle.dumps(keep, protocol=4))
        tmp.replace(p)
    except Exception:
        pass


def prune_cands(cs):                              # noqa: F811
    """★ 记忆化 + 落盘缓存包装（支配剪枝本身不变）。

    单槽 30 470 条候选跑一次 `_skyline` 要 **6.1 s**，而结果只取决于
    (每候选的 抗性/输出/技能 向量 + gid, NEED, TYPES) —— 一次搜索里完全不变，
    换等级/名额也只是换个 key。实测「候选池构建」6.1 s → 0.05 s。
    """
    if not _PRUNE or _np is None or len(cs) < 3:
        return cs
    rows = _np.array([list(c[1]) + list(c[2]) + [c[3]] for c in cs], _np.float64)
    key = _prune_key(rows, cs)
    if key is not None:
        keep = _PRUNE_MEMO.get(key)
        if keep is None:
            keep = _prune_disk_get(key)
        if keep is not None and len(keep) == len(cs):
            _PRUNE_MEMO[key] = keep
            return [c for c, k in zip(cs, keep) if k] or cs
    keep = _skyline(rows)
    if key is not None:
        _PRUNE_MEMO[key] = keep
        _prune_disk_put(key, keep)
    return [c for c, k in zip(cs, keep) if k] or cs


def score(rt, ot, sk, gr, ep, lg):
    """与 legacy 的 key() 逐字段等价（数学同一式，仅改成读累加值）"""
    cover = 0.0
    worst = 1.0
    for i in range(NT):
        nd = NEED[TYPES[i]]
        v = rt[i]
        if v > nd:
            v = nd
        cover += v
        if nd:
            rr = rt[i] / nd
            if rr > 1.0:
                rr = 1.0
            if rr < worst:
                worst = rr
    pen = 0.0
    for i, k in enumerate(FKEYS):
        d = FLOOR[k] - ot[i]
        if d > 0:
            pen += W_PEN[k] * d
    return (round(worst * 1000 + cover * 0.05 - pen + SKILL_K * sk - LEG_PEN * lg, 2),
            cover, round(sk, 1), -lg, gr, ot[OA])


def _feasible(rem_avail, lg, ep):
    """在「剩余槽位可用稀有度集合」rem_avail 下，(lg 紫, ep 蓝) 还能否补足到合法区间"""
    if lg > MAX_LEG or ep > MAX_EPIC:
        return False
    LEG, EPI = (0, 0, 1), (0, 1, 0)
    forced_l = sum(1 for a in rem_avail if a == {LEG})
    forced_e = sum(1 for a in rem_avail if a == {EPI})
    max_l = sum(1 for a in rem_avail if LEG in a)
    max_e = sum(1 for a in rem_avail if EPI in a)
    if lg + forced_l > MAX_LEG or ep + forced_e > MAX_EPIC:
        return False
    if lg + max_l < MIN_LEG or ep + max_e < MIN_EPIC:
        return False
    # 紫与蓝占的是不同槽位，最低需求之和不得超过剩余槽位数
    return max(0, MIN_LEG - lg) + max(0, MIN_EPIC - ep) <= len(rem_avail)


def _obj(rt, ot, sk, gr, ep, lg):
    """实际搜索用的目标函数，两种口味：

    goal=worst（默认）—— 先最大化「最短板比例」再加权总覆盖。避免出现某项抗性特别低
                         被杀穿，是原版 gd_opt 的口径。
    goal=cover        —— 直接最大化总覆盖率（各系 min(抗性, 需求) 之和），短板让位于总量。

    两者会选出**完全不同的方案**（实测同一约束下：最短板解覆盖 900，纯总量解覆盖 993），
    因此必须显式选择，不能混用。
    """
    cover = 0.0
    worst = 1.0
    over = 0.0
    for i in range(NT):
        nd = NEED[TYPES[i]]
        v = rt[i]
        if v > nd:
            cover += nd
            over += v - nd          # 超过 80% 显示上限的部分 = 纯浪费
        else:
            cover += v
        if nd:
            rr = rt[i] / nd
            if rr > 1.0:
                rr = 1.0
            if rr < worst:
                worst = rr
    if GOAL == 'dmg':
        # 纯输出：抗性全忽略，只按伤害代理加权进攻 + 技能加成（不罚紫装）
        base = sum(ot[i] * W_DMG.get(k, 0.0) for i, k in enumerate(FKEYS))
        return (round(base + SKILL_K * sk, 2),
                base, round(sk, 1), -lg, gr, ot[OA])
    if GOAL == 'maxdmg':
        # 覆盖只作为约束（在束搜索里硬剪枝），排序完全按输出。
        # ⚠ 必须与 numpy 路径的 s0 完全同口径：那里是
        #   base + SKILL_K*sk - LEG_PEN*lg - OVER_PEN*OVER
        # 之前这里漏了 `- OVER_PEN*over`，导致束搜索内部排序与 score_of
        # （用于多顺序重启之间比优劣）用的不是同一个函数。
        base = sum(ot[i] * W_DMG.get(k, 0.0) for i, k in enumerate(FKEYS))
        return (round(base + SKILL_K * sk - LEG_PEN * lg - OVER_PEN * over, 2),
                cover, round(sk, 1), -lg, gr, ot[OA])
    pen = 0.0
    for i, k in enumerate(FKEYS):
        d = FLOOR[k] - ot[i]
        if d > 0:
            pen += W_PEN[k] * d
    tie = DMG_TIE * sum(ot[i] * W_DMG.get(k, 0.0) for i, k in enumerate(FKEYS))
    base = (cover - pen) if GOAL == 'cover' else (worst * 1000 + cover * 0.05 - pen)
    return (round(base + SKILL_K * sk - LEG_PEN * lg + tie - OVER_PEN * over, 2),
            cover, round(sk, 1), -lg, gr, ot[OA])


def build_feas(order, CSMAP):
    """稀有度可行性前视表：每个深度只与 (已用紫, 已用蓝) 有关，与具体束节点无关，
    因此预计算成小表 —— 避免前面的槽位把名额用光后才发现无解
    （例：圣物槽只有蓝装候选，必须给它留一个蓝装名额）。
    """
    NSLOT = len(order)
    AVAIL = {s: {(c[4], c[5], c[6]) for c in CSMAP[s]} for s in order}
    FEAS = []
    for si in range(NSLOT):
        rem = [AVAIL[s] for s in order[si + 1:]]
        FEAS.append({(lg, ep): _feasible(rem, lg, ep)
                     for lg in range(MAX_LEG + 1) for ep in range(MAX_EPIC + 1)})
    return FEAS


def _sufcap(order, CSMAP):
    """每个深度「剩余槽位最多还能贡献多少覆盖点」的上界。
       ⚠ 单槽贡献按 `sum(min(rt, NEED))` 单独算再加总 —— 这是**上界**（跨槽会重叠截断），
        因此可用它判断「某节点是否**还有可能**达到 COVER_MIN」：不可能则重罚。"""
    n = len(order)
    suf = [0.0] * (n + 1)
    for si in range(n - 1, -1, -1):
        mx = 0.0
        for c in CSMAP[order[si]]:
            v = sum(min(c[1][i], NEED[TYPES[i]]) for i in range(NT))
            if v > mx:
                mx = v
        suf[si] = suf[si + 1] + mx
    return suf


_INFEAS = 1e6      # 覆盖不足的惩罚系数：大到让「可行」严格优于「不可行」


def _mkey(rt, ot, sk, gr, ep, lg, suf_left):
    """goal=maxdmg 的排序键：**先保证覆盖可行，再最大化输出**。

    覆盖不足量 = COVER_MIN − (当前覆盖 + 剩余槽位覆盖上界)。
    由于上界是乐观的，shortfall > 0 ⇒ 该节点**无论如何**都到不了 COVER_MIN ⇒ 重罚。
    可行节点之间才按输出代理排序 —— 这正是「抗性拉满 + 其余槽位全堆输出」。"""
    cov = sum(min(rt[i], NEED[TYPES[i]]) for i in range(NT))
    over = sum(max(0.0, rt[i] - NEED[TYPES[i]]) for i in range(NT))
    dmg = sum(ot[i] * W_DMG.get(k, 0.0) for i, k in enumerate(FKEYS)) + SKILL_K * sk - LEG_PEN * lg
    short = COVER_MIN - (cov + suf_left)
    if short > 0:
        dmg -= _INFEAS * short
    return (round(dmg - OVER_PEN * over, 2), cov, round(sk, 1), -lg, gr, ot[OA])


def beam_search(order, K, CSMAP, FEAS, verbose=True):
    """纯 Python 增量束搜索：束节点携带累计向量，扩展时只做向量相加（原版是每节点重算整个解）"""
    sufcap = _sufcap(order, CSMAP) if COVER_MIN > 0 else None
    beam = [((0.0,) * NT, (0.0,) * NF, 0.0, 0, 0, 0, ())]
    for si, slot in enumerate(order):
        cs = CSMAP[slot]
        feas = FEAS[si]
        new = []
        ap = new.append
        for rt, ot, sk, gr, ep, lg, sol in beam:
            for ci, (tri, rt1, ot1, sk1, gr1, ep1, lg1) in enumerate(cs):
                nlg = lg + lg1
                nep = ep + ep1
                if not feas.get((nlg, nep)):
                    continue
                nrt = tuple(map(add, rt, rt1))
                ap((nrt, tuple(map(add, ot, ot1)),
                    sk + sk1, gr + gr1, nep, nlg, sol + (ci,)))
        if not new:
            return None
        if sufcap is not None:
            _sf = sufcap[si + 1]
            new.sort(key=lambda x: _mkey(x[0], x[1], x[2], x[3], x[4], x[5], _sf),
                     reverse=True)
        else:
            new.sort(key=lambda x: _obj(x[0], x[1], x[2], x[3], x[4], x[5]), reverse=True)
        beam = new[:K]
        b0 = beam[0]
        sc = _obj(b0[0], b0[1], b0[2], b0[3], b0[4], b0[5])
        if verbose:
            print('  %-5s 候选%-4d 生成%-7d 最优分%.0f 覆盖%.0f OA%.0f 绿%d 蓝%d 紫%d'
                  % (slot, len(cs), len(new), sc[0], sc[1], sc[5],
                     b0[3], b0[4], b0[5]), flush=True)
    b0 = beam[0]
    sol = {s: CSMAP[s][b0[6][i]][0] for i, s in enumerate(order)}
    return sol, (b0[3], b0[4], b0[5])


def beam_search_np(order, K, CSMAP, FEAS, verbose=True):
    """numpy 向量化束搜索（与纯 Python 版逐位等价，快 ~15 倍）

    做法：把「束 × 候选」的笛卡尔积展开成 (B,C,·) 数组做向量相加，评分同样向量化，
    用 lexsort 稳定排序取前 K —— 排序键与 Python 元组比较完全同序，
    因此结果与纯 Python 版一致。

    ★ **分块展开**：父束按块处理，每块内先取 top-K 再与累积 top-K 归并。
      这不是近似 —— 若某节点不在自己那一块的 top-K 里，说明同一块里已有 K 个比它好，
      它必然也不在全局 top-K 里。所以「分块取 top-K 再归并」与「整体取 top-K」严格等价。
      好处：峰值内存从 O(B×C) 降到 O(CHUNK)，K=16000 时从 ~3 GB 降到 ~30 MB，
      从而让 --restart 真正能并行起来。
    """
    np = _np
    ns = len(order)
    # ★ 束层去重：每层先保留 K×X 个节点，再按「有效状态」合并，最后取前 K。
    #   为什么有效：目标的 min 截断会把大量不同装备组合映射成同一状态
    #   （实测 K=2000 时后半段唯一状态仅 370~403，即 80% 坑位被重复状态占满），
    #   于是「名义束宽」是虚的，加宽 K 毫无收益（实测 3000 与 8000 结果完全相同）。
    #   GD_DEDUP=0/1 关闭，>1 为超采样倍数。
    _DEDUP = int(os.environ.get('GD_DEDUP', '0') or 0)
    KX = K * _DEDUP if _DEDUP > 1 else K
    # ★ 逐维硬约束：GD_FULL=1 时，要求最终解每一条抗性都 ≥ 需求（即 9 项全部封顶）。
    #   为什么不能只靠 COVER_MIN：总覆盖是「各槽单独 min(抗性,需求) 求和」的**乐观上界**
    #   （跨槽会重叠截断），实测设 COVER_MIN=1070 仍只能做到 998 —— 软约束不保达成。
    #   逐维 sufmax 剪枝则是安全的：若「已达成 + 剩余槽位该维最大值之和」仍 < 需求，
    #   该节点在本维永远无法达标，可直接删除。
    _FULL = os.environ.get('GD_FULL', '0') != '0'
    SUFM = None
    if _FULL:
        SUFM = np.zeros((ns + 1, NT))
        for _si in range(ns - 1, -1, -1):
            _rows = CSMAP[order[_si]]
            if not _rows:
                # 空槽（该槽 0 候选）：它对各维的贡献上界就是 0，不做归约。
                # 过去这里直接 `_cR.max(axis=0)`，遇到空槽会以
                # 「ValueError: zero-size array to reduction」的晦涩方式崩掉。
                # 空槽本身是上游错误，由 run_search 提前报出明确原因。
                SUFM[_si] = SUFM[_si + 1]
                continue
            _cR = np.array([c[1] for c in _rows], np.float64)
            SUFM[_si] = SUFM[_si + 1] + _cR.max(axis=0)
    sufcap = _sufcap(order, CSMAP) if COVER_MIN > 0 else None
    CARR = {}
    for s in order:
        cs = CSMAP[s]
        CARR[s] = (np.array([c[1] for c in cs], np.float64),
                   np.array([c[2] for c in cs], np.float64),
                   np.array([c[3] for c in cs], np.float64),
                   np.array([c[4] for c in cs], np.int64),
                   np.array([c[5] for c in cs], np.int64),
                   np.array([c[6] for c in cs], np.int64))
    NEED_A = np.array([NEED[t] for t in TYPES], np.float64)
    FLR_A = np.array([FLOOR[k] for k in FKEYS], np.float64)
    WP_A = np.array([W_PEN[k] for k in FKEYS], np.float64)
    W_DMG_A = np.array([W_DMG.get(k, 0.0) for k in FKEYS], np.float64)
    OA_I = FKEYS.index('OA')
    chunk_nodes = max(1024, int(os.environ.get('GD_CHUNK', '262144')))

    bR = np.zeros((1, NT)); bO = np.zeros((1, NF)); bSK = np.zeros(1)
    bGR = np.zeros(1, np.int64); bEP = np.zeros(1, np.int64); bLG = np.zeros(1, np.int64)
    hist = []
    for si, s in enumerate(order):
        cR, cO, cSK, cGR, cEP, cLG = CARR[s]
        C = cR.shape[0]
        tb = np.array([[FEAS[si][(lg, ep)] for ep in range(MAX_EPIC + 1)]
                       for lg in range(MAX_LEG + 1)], dtype=bool)
        step = max(1, chunk_nodes // max(1, C))
        nb = bR.shape[0]
        best = None
        gen = 0
        for st in range(0, nb, step):
            en = min(nb, st + step)
            B = en - st
            sR, sO, sSK = bR[st:en], bO[st:en], bSK[st:en]
            sGR, sEP, sLG = bGR[st:en], bEP[st:en], bLG[st:en]
            nR = (sR[:, None, :] + cR[None, :, :]).reshape(B * C, NT)
            nO = (sO[:, None, :] + cO[None, :, :]).reshape(B * C, NF)
            nSK = (sSK[:, None] + cSK[None, :]).ravel()
            nGR = (sGR[:, None] + cGR[None, :]).ravel()
            nEP = (sEP[:, None] + cEP[None, :]).ravel()
            nLG = (sLG[:, None] + cLG[None, :]).ravel()
            par = np.repeat(np.arange(st, en), C)
            cidx = np.tile(np.arange(C), B)
            gen += nR.shape[0]

            inr = (nLG <= MAX_LEG) & (nEP <= MAX_EPIC)
            ok = np.zeros(nLG.shape[0], dtype=bool)
            if inr.any():
                ok[inr] = tb[nLG[inr], nEP[inr]]
            if _FULL:
                # 逐维硬剪枝：某维「已达成 + 剩余槽位该维最大值之和」仍不达标 ⇒ 永久出局
                ok &= (nR + SUFM[si + 1][None, :] >= NEED_A - 1e-9).all(axis=1)
            idx = np.flatnonzero(ok)
            if idx.size == 0:
                continue
            nR, nO, nSK = nR[idx], nO[idx], nSK[idx]
            nGR, nEP, nLG = nGR[idx], nEP[idx], nLG[idx]
            par, cidx = par[idx], cidx[idx]
            # 超出 80% 显示上限的抗性点 = 纯浪费（惩罚项见 OVER_PEN）
            OVER_A = np.maximum(0.0, nR - NEED_A).sum(axis=1)

            if GOAL in ('dmg', 'maxdmg'):
                # 纯输出：抗性全忽略，按伤害代理加权进攻（+技能加成）
                base = (nO * W_DMG_A).sum(axis=1)
                cover = np.minimum(nR, NEED_A).sum(axis=1)
                if GOAL == 'dmg':
                    s0 = np.round(base + SKILL_K * nSK, 2)
                    cover = base
                elif sufcap is not None:
                    # 覆盖不足 → 重罚；只有「还有可能达标」的节点才按输出排序
                    short = COVER_MIN - (cover + sufcap[si + 1])
                    s0 = np.round(base + SKILL_K * nSK - LEG_PEN * nLG
                                  - _INFEAS * np.maximum(0.0, short)
                                  - OVER_PEN * OVER_A, 2)
                else:
                    s0 = np.round(base + SKILL_K * nSK - LEG_PEN * nLG
                                  - OVER_PEN * OVER_A, 2)
            else:
                cover = np.minimum(nR, NEED_A).sum(axis=1)
                pen = (np.maximum(0.0, FLR_A - nO) * WP_A).sum(axis=1)
                if GOAL == 'cover':
                    base = cover - pen
                else:
                    worst = np.minimum(nR / NEED_A, 1.0).min(axis=1)
                    base = worst * 1000 + cover * 0.05 - pen
                s0 = np.round(base + SKILL_K * nSK - LEG_PEN * nLG
                              + DMG_TIE * (nO * W_DMG_A).sum(axis=1)
                              - OVER_PEN * OVER_A, 2)
            # seq = 全局插入序（父下标*C + 候选下标），用作最后一级 tie-break，
            # 等价于原实现的「稳定排序」，保证与未分块时逐位一致
            seq = par * C + cidx
            # ★ 先 argpartition 再 lexsort（严格等价，快得多）
            #   主排序键是 s0（降序）。任何 s0 **严格小于第 K 大值**的行，
            #   无论后面几级 tie-break 怎么排，都不可能进 top-K ⇒ 先筛掉。
            #   旧实现直接对「整块 K×C 行」做 7 键 lexsort（链表式稳定排序，7 趟），
            #   而这里 n 通常是几十万行、K 只有几千 ⇒ 白排了 99% 的行。
            nrow = seq.size
            if nrow > KX:
                _neg = -s0
                thr = np.partition(_neg, KX - 1)[KX - 1]    # 第 KX 大的 s0 值
                sel = np.flatnonzero(_neg <= thr)
            else:
                sel = np.arange(nrow)
            kk = sel[np.lexsort((seq[sel], -nO[sel, OA_I], -nGR[sel], nLG[sel],
                                 -np.round(nSK[sel], 1), -cover[sel],
                                 -s0[sel]))[:KX]]
            part = {'R': nR[kk], 'O': nO[kk], 'SK': nSK[kk], 'GR': nGR[kk],
                    'EP': nEP[kk], 'LG': nLG[kk], 'par': par[kk], 'cidx': cidx[kk],
                    's0': s0[kk], 'cover': cover[kk], 'skr': np.round(nSK[kk], 1),
                    'oa': nO[kk, OA_I], 'seq': seq[kk]}
            if best is None:
                best = part
            else:
                mg = {k: np.concatenate((best[k], part[k])) for k in part}
                sl = np.lexsort((mg['seq'], -mg['oa'], -mg['GR'], mg['LG'],
                                 -mg['skr'], -mg['cover'], -mg['s0']))[:KX]
                best = {k: mg[k][sl] for k in mg}

        if best is None:
            return None
        if _DEDUP > 1 and best['R'].shape[0] > K:
            # 「有效状态」= min(抗性, 需求) 向量 + 溢出量。
            # 两者相同 ⇒ 本节点之后无论怎么加装备，cover / worst / 溢出惩罚都完全一致，
            # 只剩输出向量有区别 —— 因此同 key 只保留排序最靠前（s0 最高）的那个。
            _cap = np.minimum(best['R'], NEED_A)
            _ovr = np.maximum(0.0, best['R'] - NEED_A).sum(axis=1)
            _ky = np.concatenate([np.round(_cap, 3), np.round(_ovr, 3)[:, None]], axis=1)
            _, _first = np.unique(_ky, axis=0, return_index=True)
            _keep = np.sort(_first)[:K]
            best = {k: v[_keep] for k, v in best.items()}
        hist.append((best['par'], best['cidx']))
        bR, bO = best['R'], best['O']
        bSK, bGR = best['SK'], best['GR']
        bEP, bLG = best['EP'], best['LG']
        if verbose:
            print('  %-5s 候选%-4d 生成%-7d 留存%-5d 最优分%.0f 覆盖%.0f OA%.0f 绿%d 蓝%d 紫%d'
                  % (s, C, gen, bR.shape[0], best['s0'][0], best['cover'][0],
                     best['oa'][0], best['GR'][0], best['EP'][0], best['LG'][0]),
                  flush=True)

    k = 0
    sol = {}
    for si in range(ns - 1, -1, -1):
        par, cidx = hist[si]
        sol[order[si]] = CSMAP[order[si]][int(cidx[k])][0]
        k = int(par[k])
    return sol, (int(bGR[0]), int(bEP[0]), int(bLG[0]))


def score_of(sol):
    """整解的 score tuple（用于多顺序重启之间比优劣）"""
    r, o = ev(sol)
    rt = tuple(r.get(t, 0.0) for t in TYPES)
    ot = tuple(o[k] for k in FKEYS)
    sk = sum(contrib(g)[2] for s in sol for g in sol[s] if g)
    gr = sum(1 for s in sol if _rar(sol[s][0]) == 'Rare')
    ep = sum(1 for s in sol if _rar(sol[s][0]) == 'Epic')
    lg = sum(1 for s in sol if _rar(sol[s][0]) == 'Legendary')
    if GOAL == 'maxdmg':
        return _mkey(rt, ot, sk, gr, ep, lg, 0.0)     # 完整解：剩余上界 = 0
    return _obj(rt, ot, sk, gr, ep, lg)


def _orders_for(order, restart):
    """自然顺序 + (restart-1) 个固定种子打乱顺序"""
    orders = [list(order)]
    if restart > 1:
        import random
        rnd = random.Random(20260916)
        for _ in range(restart - 1):
            o = list(order)
            rnd.shuffle(o)
            orders.append(o)
    return orders


def _safe_jobs(jobs, K, csmap):
    """按内存预算限制并行度

    因为 beam_search_np 改成了**分块展开**，单路峰值不再是 K×max(C)×(NT+NF)，
    而是「一块」的大小 + 累积 top-K：约 CHUNK×(NT+NF)×8×4 + K×(NT+NF)×8×2。
    CHUNK 默认 262144（可用 GD_CHUNK 覆盖）。
    """
    import os as _os
    n = _os.cpu_count() or 1
    want = min(jobs, n) if (jobs and jobs > 0) else n
    if want <= 1:
        return 1
    chunk = max(1024, int(_os.environ.get('GD_CHUNK', '262144')))
    per = chunk * (NT + NF) * 8 * 4 + K * (NT + NF) * 8 * 2
    budget = 6 * 1024 ** 3                      # 保守按 6 GB 可用算
    return max(1, min(want, int(budget // max(1, per)) or 1))


def _search_once(eng, od, K, csmap, verbose):
    return eng(od, K, csmap, build_feas(od, csmap), verbose)


def run_search(order, K, verbose=True, restart=1, jobs=1, csmap=None):
    """束搜索入口

    ★ 束搜索对「槽位处理顺序」敏感且**在束宽上不单调**（实测 K=2000 得 589 分，
      K=4000 反而只有 588 分）—— 这是贪心启发式的固有特性，不是 bug。
      因此提供 --restart N：用自然顺序 + N-1 个固定种子的打乱顺序各跑一遍，取最优。
      实测同一约束下不同顺序的成绩在 78.3%~88.2% 之间浮动，取最优能稳定拿到上界。

    ★ 各次重启彼此独立，用**线程池并行**（numpy 的数组运算会释放 GIL，
      所以线程比多进程更合适 —— 免去 Windows spawn 下重跑模块级代码的坑）。
    """
    eng = beam_search_np if (_np is not None and '--no-numpy' not in sys.argv) else beam_search
    if csmap is None:
        with TM.span('opt:建候选池'):
            csmap = {s: prune_cands(slot_cands(s)) for s in sorted(set(order))}
    # ★ 空槽提前报错：某槽 0 候选时束搜索给不出有意义的解，
    #   而 numpy 版过去会一路撑到 SUFM 才以「零尺寸数组归约」崩掉（难排查）。
    _empty = sorted(s for s in set(order) if not csmap.get(s))
    if _empty:
        raise SystemExit(
            '✗ 这些槽位没有任何候选：%s\n'
            '  排查：① 品质过滤（普通装默认被剔）② 装备需求等级 > GD_MAX_ILVL\n'
            '        ③ 派系 / 属性闸门过紧 ④ GD_SLOTS 槽位名不对'
            % '、'.join(_empty))
    orders = _orders_for(order, restart)
    jn = _safe_jobs(jobs, K, csmap)

    if jn > 1 and len(orders) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with TM.span('opt:束搜索(并行x%d)' % jn):
            with ThreadPoolExecutor(max_workers=jn) as ex:
                futs = [ex.submit(_search_once, eng, od, K, csmap, verbose and i == 0)
                        for i, od in enumerate(orders)]
                results = [f.result() for f in futs]
        par = '，并行 x%d' % jn
    else:
        with TM.span('opt:束搜索'):
            results = [_search_once(eng, od, K, csmap, verbose and i == 0)
                       for i, od in enumerate(orders)]
        par = ''

    best = None
    for i, res in enumerate(results):
        if res is None:
            continue
        sol, cnt = res
        sol = {s: sol[s] for s in SLOTS}
        sc = score_of(sol)
        if verbose:
            print('  [重启 %d/%d]%s 分%.0f 覆盖%.0f 绿%d 蓝%d 紫%d'
                  % (i + 1, len(orders), '（首次，逐步明细见上）' if i == 0 else '        ',
                     sc[0], sc[1], cnt[0], cnt[1], cnt[2]), flush=True)
        if best is None or sc > best[1]:
            best = (sol, sc, cnt)
    if best is None:
        print('  ✗ 无可行组合（稀有度约束 %d~%d 紫 / %d~%d 蓝 是否与 --pin 冲突？）'
              % (MIN_LEG, MAX_LEG, MIN_EPIC, MAX_EPIC))
        raise SystemExit(1)
    if verbose:
        print('  （引擎：%s ｜ 重启 %d 次%s，取最优）'
              % ('numpy 向量化' if eng is beam_search_np else '纯 Python', len(orders), par))
    return best[0], best[2]


# ---------------------------------------------------------------- 参数扫描
def _parse_sweep(specs):
    """--sweep beam=700,4000 --sweep restart=1,6 --sweep max-epic=2,3 → 笛卡尔积"""
    dims = []
    for sp in specs:
        if '=' not in sp:
            continue
        k, v = sp.split('=', 1)
        k = k.strip().lstrip('-')
        vals = []
        for x in v.split(','):
            x = x.strip()
            if not x:
                continue
            try:
                vals.append(int(x))
            except ValueError:
                vals.append(x)
        if vals:
            dims.append((k, vals))
    combos = [{}]
    for k, vals in dims:
        combos = [dict(c, **{k: v}) for c in combos for v in vals]
    return combos


def apply_params(c):
    """把一组扫描参数写回模块级变量（_obj/_feasible 读的就是这些）"""
    global K, RESTART, MIN_LEG, MAX_LEG, MIN_EPIC, MAX_EPIC, GOAL
    m = {'beam': 'K', 'restart': 'RESTART', 'min-leg': 'MIN_LEG', 'max-leg': 'MAX_LEG',
         'min-epic': 'MIN_EPIC', 'max-epic': 'MAX_EPIC', 'goal': 'GOAL'}
    for k, v in c.items():
        if k not in m:
            continue
        n = m[k]
        if n == 'GOAL':
            if str(v) in ('worst', 'cover'):
                GOAL = str(v)
        else:
            globals()[n] = int(v)


def sweep(specs, order, verbose=False, jobs=1):
    """参数矩阵一次跑完 —— 候选池只建一次，N 组参数各跑一次搜索

    ★ 这是针对「调参要跑 N 次、每次都付一遍进程启动 + 建池」的正解：
      实测 10 组参数从「10 次调用、每次约 1.5 s」降到「1 次调用、约 5 s」。
    """
    combos = _parse_sweep(specs)
    if not combos:
        print('✗ --sweep 格式：--sweep beam=700,4000 --sweep restart=1,6')
        raise SystemExit(2)
    with TM.span('opt:建候选池(sweep)'):
        csmap = {s: prune_cands(slot_cands(s)) for s in order}
    rows = []
    for i, c in enumerate(combos):
        apply_params(c)
        with TM.span('opt:sweep#%d' % i):
            try:
                sol, cnt = run_search(order, K, verbose=verbose, restart=RESTART,
                                      jobs=jobs, csmap=csmap)
            except SystemExit:
                print('  [%d/%d] %s → 无可行解，跳过' % (i + 1, len(combos), c))
                continue
        rows.append((c, score_of(sol), cnt, sol))
    if not rows:
        print('✗ 所有组合都无可行解')
        raise SystemExit(1)
    print()
    print('=' * 96)
    print('参数扫描结果（%d 组，候选池只建了一次）' % len(rows))
    print('=' * 96)
    print('%-42s %8s %8s %5s %5s %5s' % ('参数', '分', '覆盖', '绿', '蓝', '紫'))
    print('-' * 96)
    for c, sc, cnt, _s in rows:
        desc = ' '.join('%s=%s' % (k, v) for k, v in c.items())
        print('%-42s %8.0f %8.0f %5d %5d %5d'
              % (desc[:42], sc[0], sc[1], cnt[0], cnt[1], cnt[2]))
    print('-' * 96)
    best = max(rows, key=lambda x: x[1])
    print('最优：%s  →  分 %.0f  覆盖 %.0f/%d (%.1f%%)'
          % (' '.join('%s=%s' % (k, v) for k, v in best[0].items()), best[1][0], best[1][1],
             sum(NEED.values()), 100.0 * best[1][1] / sum(NEED.values())))
    apply_params(best[0])
    return best[3], best[2]


# ★ 只有**直接运行**（`python -m gd.opt` / `python gd/opt.py`）才跑搜索与输出。
#   `import gd.opt` 以前会**顺带跑一整轮搜索**（实测 6.9 s）；
#   对 `tools/tune_dps.py` / `tools/autobuild.py` / `gd/explain.py` / `gd/planreport.py`
#   这类「只要候选池与向量」的调用方是纯浪费，更是多进程每个 worker 的固定开销。
if __name__ == '__main__':
    print('=== 约束：紫装 %d~%d 件 ｜ 蓝装 %d~%d 件 ｜ 其余绿装 ｜ 束宽 %d ｜ 重启 %d ｜ 目标 %s ==='
          % (MIN_LEG, MAX_LEG, MIN_EPIC, MAX_EPIC, K, RESTART,
             '纯输出·伤害优先（抗性忽略）' if GOAL == 'dmg'
             else ('高抗性+保伤害（覆盖硬约束 %.0f）' % COVER_MIN if GOAL == 'maxdmg'
                   else ('总覆盖率最大' if GOAL == 'cover' else '最短板优先'))))

    if '--selfcheck' in sys.argv:
        # 回归自证：新 score() 必须与 legacy key() 逐字段一致
        import random
        random.seed(20260916)
        bad = n = 0
        for _ in range(300):
            s = {}
            for sl in SLOTS:
                cc = choices(sl)
                if cc:
                    s[sl] = cc[random.randrange(len(cc))] + (None, None)
            if len(s) != len(SLOTS):
                continue
            n += 1
            rr, oo = ev(s)
            rt = tuple(rr.get(t, 0.0) for t in TYPES)
            ot = tuple(oo[k] for k in FKEYS)
            sk = sum(contrib(g)[2] for sl in s for g in s[sl] if g)
            gr = sum(1 for sl in s if _rar(s[sl][0]) == 'Rare')
            ep = sum(1 for sl in s if _rar(s[sl][0]) == 'Epic')
            lg = sum(1 for sl in s if _rar(s[sl][0]) == 'Legendary')
            if key(s) != score(rt, ot, sk, gr, ep, lg):
                bad += 1
                if bad <= 3:
                    print('  ✗ 不一致 %r vs %r' % (key(s), score(rt, ot, sk, gr, ep, lg)))
        print('  [自检] score() vs legacy key()：%d 组随机方案，不一致 %d 组 → %s'
              % (n, bad, '✓ 完全一致' if bad == 0 else '✗ 有偏差'))
        raise SystemExit(0 if bad == 0 else 1)

    # ⚠ 束搜索是按槽位顺序贪心的：pin 的槽位必须最先处理，否则名额会被前面的槽位用光
    # ---------------------------------------------------------------- 方案对比模式
    # `--compare a.json b.json [c.json …]`：不做搜索，只**同口径**评估若干已有方案
    # （用同一套 contrib/res_of/off_of，避免报告里出现"两套算法各说各话"）
    if '--compare' in sys.argv:
        _i = sys.argv.index('--compare')
        _files = [a for a in sys.argv[_i + 1:] if a.endswith('.json')]
        _rows = []
        for _f in _files:
            _sol = {k: tuple(v) for k, v in json.load(open(_f, encoding='utf-8')).items()}
            _r, _o = ev(_sol)
            _cov = sum(min(_r.get(t, 0.0), NEED[t]) for t in TYPES)
            _lg = sum(1 for s in _sol if _rar(_sol[s][0]) == 'Legendary')
            _ep = sum(1 for s in _sol if _rar(_sol[s][0]) == 'Epic')
            _gr = sum(1 for s in _sol if _rar(_sol[s][0]) == 'Rare')
            _sk = sum(contrib(g)[2] for s in _sol for g in _sol[s] if g)
            _dmg = sum(_o.get(k, 0.0) * W_DMG.get(k, 0.0) for k in FKEYS)
            _rows.append((os.path.basename(_f), _r, _o, _cov, _gr, _ep, _lg, _sk, _dmg))
        _w = max(len(r[0]) for r in _rows)
        print('%-*s %8s %8s %5s %5s %5s %8s %8s' %
              (_w, '方案', '覆盖', '覆盖%', '绿', '蓝', '紫', '等效技能', '伤害代理'))
        for r in _rows:
            print('%-*s %8.0f %7.1f%% %5d %5d %5d %8.0f %8.0f' %
                  (_w, r[0], r[3], 100 * r[3] / sum(NEED.values()), r[4], r[5], r[6], r[7], r[8]))
        print()
        print('%-12s %s' % ('抗性', ' '.join('%18s' % r[0][:18] for r in _rows)))
        for t in TYPES:
            line = []
            for r in _rows:
                v = r[1].get(t, 0.0)
                eff = max(0, min(80, v - PEN[t]))
                line.append('%7.0f→%3.0f%%%s' % (v, eff, '★' if NEED[t] and v >= NEED[t] else ' '))
            print('%-12s %s' % (t, ' '.join('%18s' % x for x in line)))
        print()
        print('%-12s %s' % ('输出', ' '.join('%18s' % r[0][:18] for r in _rows)))
        for k in FKEYS:
            print('%-12s %s' % (k, ' '.join('%18.0f' % r[2].get(k, 0.0) for r in _rows)))
        raise SystemExit(0)

    ORDER = [s for s in PIN if s in SLOTS] + [s for s in SLOTS if s not in PIN]
    SWEEP = [sys.argv[i + 1] for i, a in enumerate(sys.argv)
             if a == '--sweep' and i + 1 < len(sys.argv)]
    if SWEEP:
        sol, (_gr, _ep, _lg) = sweep(SWEEP, ORDER, verbose='--quiet' not in sys.argv, jobs=JOBS)
    else:
        sol, (_gr, _ep, _lg) = run_search(ORDER, K, restart=RESTART, jobs=JOBS)

    r, o = ev(sol)
    print()
    if GOAL == 'dmg':
        print('=== 纯输出方案（伤害优先，抗性忽略；武器槽已纳入）===')
    elif GOAL == 'maxdmg':
        print('=== 高抗性 + 保伤害方案（覆盖硬约束 %.0f 点，其余槽位全堆输出）==='
              % COVER_MIN)
    else:
        print('=== 平衡方案（主绿 + 副蓝 %d~%d + 少量紫 %d~%d，抗性优先）==='
              % (MIN_EPIC, MAX_EPIC, MIN_LEG, MAX_LEG))
    cov = sum(min(r[t], NEED[t]) for t in TYPES)
    print('覆盖率 %.0f / %d = %.1f%%   绿装 %d  蓝装 %d  紫装 %d  /%d' %
          (cov, sum(NEED.values()), cov / sum(NEED.values()) * 100, _gr, _ep, _lg,
           len(SLOTS)))          # 原来是写死的 /12，但 SLOTS 有 14 个（含主手/副手），会误导
    for t in TYPES + [PHYS]:
        eff = max(0, min(80, r[t] - PEN[t]))
        print('  %-4s 原值%6.0f 终极后%4.0f%%  需%3d  %s' %
              (t, r[t], eff, NEED[t], '★封顶' if (eff >= 80 and NEED[t]) else ('差%.0f' % (NEED[t] - r[t]) if NEED[t] and r[t] < NEED[t] else '')))
    print('  --- 输出（下限 %s 低于则扣分）---' % FLOOR)
    for k in FKEYS:
        print('  %-7s %6.0f  %s' % (k, o[k], '✔' if o[k] >= FLOOR[k] else '✘ 低于下限 %.0f' % (FLOOR[k] - o[k])))
    print()
    for slot in SLOTS:
        b, c, a, p, s = sol[slot]
        print('  %-5s [%s] %-9s %-16s | 镶 %-9s %-12s | 附 %-9s %-12s | 词缀 %s / %s' %
              (slot, RAR_CN.get(_rar(b), '?'), b, name_of(b)[:16],
               c or '——', name_of(c)[:12], a or '——', name_of(a)[:12],
               p or '——', s or '——'))

    print()
    print('  --- 装备自带的技能加成（对本 build 有效）---')
    _nsk = 0
    for slot in SLOTS:
        b, c, a, _p, _s = sol[slot]
        for gid in (b, c, a):
            if not gid:
                continue
            oo = _IT.get(gid) or {}
            for nn in (1, 2, 3, 4):
                t = oo.get('augmentSkillName%d' % nn)
                lv = oo.get('augmentSkillLevel%d' % nn)
                if isinstance(t, str) and t in SKILL_W and isinstance(lv, (int, float)):
                    print('    %-5s %-18s %s +%s' % (slot, name_of(gid)[:18], TAGS.get(t, t), lv))
                    _nsk += 1
    if not _nsk:
        print('    （无）')
    json.dump({k: [x for x in v] for k, v in sol.items()},
              open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    # ★ 侧车文件：该目标下每槽选中的最优前缀/后缀。
    #   gd_plan_export.py 要用它把词缀还原成 gd_build 方案 —— 而 gd_opt 的模块级代码
    #   一 import 就会跑整轮搜索，所以不能让导出脚本去 import 它。
    json.dump({'goal': GOAL, 'max_ilvl': _MAX_ILVL, 'cover_min': COVER_MIN,
               'best_pre': {k: v for k, v in BEST_PRE.items() if v},
               'best_suf': {k: v for k, v in BEST_SUF.items() if v}},
              open(OUT + '.affix.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('→ %s' % OUT)
