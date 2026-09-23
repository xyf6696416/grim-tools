# -*- coding: utf-8 -*-
"""临时：用**真实 DPS** 做星座贪心搜索（`select_coherent` 的分数与真实伤害不同向）。

口径：只加载一次存档；`final_report` 走**显式星座通道**
（`levels` 去掉 `/devotion/` 条目 + 传 `devotion_levels`），
这样比「星座混在技能里」更完整（多算平伤/攻速/独立倍率），且改星座能直接出数。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, SKILL)
sys.path.insert(0, HERE)
os.environ['GD_PROJ_HITS'] = '1'
# ★★ 2026-09-22 性能：优化器路径**关掉归因账本**（`slot_pct` / `slot_flat` ——
#   那是「伤害循环文档」的报告产物，却在每次评估里都记一遍）。
#   报告子进程会显式改回 `GD_ATTRIB=1`。
os.environ.setdefault('GD_ATTRIB', '0')

from gd import DB, dps as D, rotation as R, rr as RR, enemy as ENM  # noqa: E402
from gd import skillprov as SP, procs as PR, devotion as DV          # noqa: E402
import objfunc as _OBJ                                                # noqa: E402

# ★ 评分目标（`--objective` / `GD_OBJ`）：`total` = `rep['dps']`（旧行为）；
#   `pierce` = 穿刺桶含减抗 DPS —— 「主穿刺」形态用。
OBJ = _OBJ.mode_default()
# 输出路径可换（免得覆盖别的目标算出来的方案）
DEV_OUT = os.environ.get('DEV_OUT', 'data/scratch/devotion_opt.json')

ARCH = os.environ.get('ARCH', 'wolf_nightblade_fast')
CHAR = os.environ.get('CHAR', 'Sam')
PLAN = os.environ.get('PLAN', 'data/plans/Sam_lv73_current.json')
# ★ 点数池不再是写死的 36 —— **要么**外部给 `BUDGET`，**要么**从存档自己算（见下方 `_pool()`）。
_ENV_BUDGET = int(os.environ.get('BUDGET') or 0)

import plan_dps as PD  # noqa: E402

ov = PD.plan_to_override(json.load(open(PLAN, encoding='utf-8')))
C = D.load_char(CHAR, '', True, gear_override=(ov or None))

sk = C['skills']
eff_all = dict(sk)
for rec, extra in (C.get('skill_plus') or {}).items():
    if rec in eff_all:
        eff_all[rec] += extra
eff_nodev = {k: v for k, v in eff_all.items() if '/devotion/' not in k}

mast = {}
for rec, lv in sk.items():
    if '_classtraining_' in rec:
        mast[os.path.basename(rec).replace('_classtraining_', '').replace('.dbr', '')] = lv
for cls, extra in (C.get('mastery_plus') or {}).items():
    mast[cls] = mast.get(cls, 0) + extra
attrs = R.panel_attrs(C.get('bio') or {}, mast, C.get('gear_flat') or {}, C.get('gear_pct') or {})
apct = R.attr_damage_pct(attrs)
# ★★★ 2026-09-22（陷阱 #85）：rr **不能冻结** —— 星座星位里就有 B 族减抗
#   （如「暗杀者的标记」`defensivePierce −8`，且逐级数组会变）。
#   旧写法在这里算一次 `rr_pack` 就复用，于是「退掉/补入一个减抗星位」在评分里
#   恒为 0 ⇒ 星座优化器对**自家减抗星位**完全失明。
#   `RRCache` 只在星座集合真的变了减抗来源时才重算。
_rrc = RR.RRCache(C['folded'], C['db'])
eprof = ENM.get_profile(os.environ.get('GD_ENEMY_PROFILE') or None, C.get('level') or 100)
e_arm, e_src = D.resolve_enemy_armor(os.environ.get('GD_ENEMY_ARMOR') or None, eprof)
eq_sk = SP.norm_equipped([x[1] for x in (C.get('item_skills') or [])])
item_wps = PR.wps_pool(C.get('base_gids'))
# ★★ 2026-09-22：**武器类型门控** —— 星座里有一批星位带硬前提
#   （狂战士六星全要 `Axe`/`Axe2h`/`Spear2h`，l10n「需要斧或矛。」）。
#   不传 `weapon_st` ⇒ 这些星位在**星座优化器**里被当成有效收益，搜索方向被带偏。
#   ★ 星座走的是 `devotion_levels=` 这条**显式通道**，`final_report` 里已一并过门。
weapon_st = PR.weapon_state_of(C)
BASE_APS = C.get('base_aps') or 1.25


def ev(dev_recs):
    """dev_recs: {记录: 1} → (面板 dps, 实战 dps_real)。"""
    rep = R.final_report(
        ARCH, eff_nodev, db=C['db'], folded=C['folded'],
        skill_records=list(eff_nodev), base_aps=BASE_APS, attr_pct=apct,
        conversions=C.get('conversions') or [], skill_mods=C.get('skill_mods') or {},
        rr=_rrc.pack(eff_nodev, dev_recs), attrs=attrs, level=C.get('level'), enemy=eprof,
        enemy_armor=e_arm, enemy_armor_src=e_src, equipped_sk=eq_sk, item_wps=item_wps,
        devotion_levels=dev_recs, weapon_st=weapon_st)
    return float(rep['dps']), float(rep.get('dps_real') or 0), rep


CUR = {k: 1 for k, v in sk.items() if '/devotion/' in k and v > 0}


def _pool():
    """虔诚点池 = **已点亮节点数**（`len(CUR)`，**含 proc 节点**）+ 存档「未分配」。

    ★★ 2026-09-21 更正两处（旧代码两处都错）：

      ① **proc / Celestial Power 节点也要花 1 点**。官方指南：*"Some Stars glow brighter than
         others. These will grant Celestial Powers."* —— **亮星本身就是一颗要花点买的星**。
         实测三重对账完全一致：存档点亮节点 **36** == 存档 `total_devotion_points` **36**
         == 这 8 颗星座的节点数合计 **36**。而 `groups()['stars']` 只数**非 proc** 记录
         ⇒ 旧口径的「已投 34」其实是 **36** 点，`choices`/预算检查对**带 proc 的星座**恒少算 1 点。
      ② 池**不是** `total_devotion_points` —— 那字段是「**已花**」。旧代码（以及
         `gd/save/report.py` 的「虔诚点已用 = 总 − 未分配」）把它当池/当已用，都读错了。
    """
    free = None
    try:
        from gd import paths as P
        p = os.path.join(P.save_dir()[0] or '', 'main', '_' + CHAR, 'player.gdc')
        if os.path.isfile(p):
            b2 = (D._parse_cached(p).get('block_map') or {}).get(2) or {}
            free = int(b2.get('devotion_points') or 0)
            print('  点数对账：点亮节点 %d ｜ 存档「已花」%s ｜ 未分配 %s'
                  % (len(CUR), b2.get('total_devotion_points'), free))
    except Exception as e:                                     # noqa: BLE001
        print('  ⚠ 读存档点数失败：%s（退回 len(CUR)）' % e)
    return len(CUR) + int(free or 0)


BUDGET = _ENV_BUDGET or _pool()


_SG = None
_SG_IGN = ('l', 'o', 'templateName', 'skillUpBitmapName', 'skillMaxLevel',
           'skillDisplayName', 'characterBaseAttackSpeedTag')


_CALC_G = None


def calc_graph():
    """GT 桌面版 `calc.js` 里的**星位图**：`{tag: {'buttons': {序号: sk}, 'children': {序号: [序号]}}}`

    ⚠⚠ 为什么**不能**用 `devotion_tree.json` 的 `stars` + `links` 直接配对：
      `links` 是**按星位序号**排的（键 2..N，第 K 项 = 第 K 个星位的父星序号），
      而 `stars` 数组**不是**按序号排的 —— 实测 tier3_21：
      `calc.js` 里 `button1..6 = sk2602…sk2607`（升序），树里却是
      `sk2603, sk2604, sk2602, sk2607, sk2605, sk2606`（乱序）⇒ 两者**错位**，
      照它推出的「根 = `stars[0]`」「`stars[k]` 的父 = `stars[links[k-1]-1]`」**会取到错的星**。
      正解：回 `calc.js` 按 `devotionButton<序号>` + `devotionLinks<序号>` 取（**根 = 唯一没有父星的 1 号**）。
    """
    global _CALC_G
    if _CALC_G is not None:
        return _CALC_G
    out = {}
    try:
        p = os.path.join('data', 'cache', 'calc.js')
        txt = open(p, encoding='utf-8', errors='replace').read()
    except Exception as e:                                     # noqa: BLE001
        print('  ⚠ 读不到 calc.js：%s（星座连线顺序不可解）' % e)
        _CALC_G = out
        return out

    def _back(pos):                       # 往左找**包含该属性**的那个对象（星座对象）的 `{`
        d = 0
        for i in range(pos, -1, -1):
            c = txt[i]
            if c == '}':
                d += 1
            elif c == '{':
                if d == 0:
                    return i
                d -= 1
        return -1

    def _fwd(pos):                        # 从 `{` 找到配对的 `}`
        d = 0
        for i in range(pos, len(txt)):
            c = txt[i]
            if c == '{':
                d += 1
            elif c == '}':
                d -= 1
                if d == 0:
                    return i
        return -1

    # ★ 按钮/连线在星座对象里是**交错排列**的（实测：button2, button3, links3, button4, …），
    #   所以必须**按大括号配对切块**，不能简单按「下一个 button1」切。
    for m in re.finditer(r'devotionButton1:\{X:"sk\d+"', txt):
        a = _back(m.start())
        b = _fwd(a) if a >= 0 else -1
        if a < 0 or b < 0:
            continue
        seg = txt[a:b + 1]
        btns = dict(re.findall(r'devotionButton(\d+):\{X:"(sk\d+)"', seg))
        links = dict(re.findall(r'devotionLinks(\d+):(\d+)', seg))
        if len(btns) < 2 or len(links) != len(btns) - 1:
            continue
        ch = {}
        for k, par in links.items():
            ch.setdefault(int(par), []).append(int(k))
        key = frozenset(btns.values())
        out.setdefault(key, {'buttons': {int(k): v for k, v in btns.items()},
                             'children': ch, 'n': len(btns)})
    _CALC_G = out
    return out


_AFF_TPL = None


def aff_of(cur, tree, SG):
    """★ **精确亲和力**：`Σ` 「**完全点亮**」星座的 `given`（2026-09-21 修，陷阱 #73）。

    游戏规则（官方指南）：*"Affinity is earned by **fully unlocking a Constellation**,
    or through each star in the Crossroads Constellation."* ⇒

      · **只有点满一整颗星座**才拿到它的 `given`；**点一半 = 0**；
      · 十字路口在数据里是 **6 个「抉择之地」条目**（各 1 星、需求为空、`given` = 各 1 点）

    ⚠ 旧实现 `DV.check_affinity` 是「**每星 +1 主亲和力**」的粗筛 —— 与真规则**不同向**：
    它会给「点了一半」的星座白送亲和力，也会漏掉 `given` 的第二种亲和力，
    实测把**完全达不到**的星座放行（北海巨妖要 Primordial 5，Sam 是 **0**）。
    """
    from collections import Counter
    aff = Counter()
    for idx, sg in SG.items():
        # ⚠⚠ **不能用 `>= sg['nodes']` 判「点满」**（2026-09-21 修）：`nodes` 是
        #   `calc_graph()` 数出来的**按钮总数**，**含 proc（Celestial Power）节点**；
        #   而 `pos` 里 proc 那一格是 `None` ⇒ 「点亮记录数」**永远比 `nodes` 少 1**
        #   ⇒ **凡是带 proc 的星座，`given` 恒记 0**。
        #   实测后果：Sam 的亲和力被低估 **Asc −4（铁锤）/ Order −3**，
        #   `req_ok` 因此**过严** ⇒ 误判「退款后门槛不够」、把合法重排挡在外面
        #   （本工具输出里那句「亲和力自洽：False」也是同一处漏算的副作用）。
        #   正解：**非 None 的星位全亮 ＋（若有）proc 记录也在** = 游戏里「完全解锁」。
        stars = [r for r in sg['pos'] if r]
        if all(r in cur for r in stars) and (not sg.get('proc') or sg['proc'] in cur):
            for k, v in (tree.get(idx, {}).get('given') or []):
                aff[k] += v
    return aff


def req_ok(idx, aff, tree):
    """该星座的需求是否已被现有亲和力满足（点它的**第一颗星**前的门槛）"""
    return all(aff.get(k, 0) >= v for k, v in (tree.get(idx, {}).get('affinity') or []))


def feasible(order_pool, aff0, tree, SG):
    """**能不能按某个顺序真的点出来**（亲和力只增 ⇒ 反复找「当下就够门槛」的星座）。

    为什么必须查顺序：最终状态的 `aff` 满足所有需求**不等于**可达 ——
    X 要 Prim 5、只由 Y 提供，而 Y 要 Asc 15、只由 X 提供 ⇒ 阿喀琉斯死锁。
    返回 `(可行?, 顺序)`。
    """
    aff, pend, out = dict(aff0), list(order_pool), []
    while pend:
        nxt = [i for i in pend if req_ok(i, aff, tree)]
        if not nxt:
            return False, out
        for i in nxt:
            for k, v in (tree.get(i, {}).get('given') or []):
                aff[k] = aff.get(k, 0) + v
            out.append(i)
            pend.remove(i)
    return True, out


def star_graph():
    """★ 星座内部**连线顺序** → `{idx: {'pos': [记录或 None], 'children': {pos: [pos…]}, 'name': str}}`

    为什么需要：游戏里**不能跳着点星** —— 一颗星只有在**父星已点亮**时才能买
    （根星另需满足星座亲和力）。所以「把剩下的 1~2 点花掉」时，合法的买法只有
    「含根的**连通**子集」。旧的 `--realloc` 只会整颗买（`len(stars) > 4` 还直接跳过）
    ⇒ 预算剩 2 点时**一个候选都出不来**，看起来像「没收益」，其实是**搜索粒度**的缺口。

    数据来源（**全在离线库/GT 缓存里，不碰 `.arz`**）：
      · `devotion_tree.json` 的 `links` 就是父子关系 —— `len(links) == 星数-1`，
        `stars[k]` 的父 = `stars[links[k-1]-1]`，**根恒为 `stars[0]`**（构造上必然）；
        但 `stars` 给的是 **sk 号**，不是记录路径；
      · 记录侧（`devotions.json` 的 `stats`）与 sk 侧（`item_skills.json` 的数值）
        用**同星座内「最小距离配对」**join（注意记录侧的值是**单元素列表**，
        且多一个 `characterBaseAttackSpeedTag`，必须归一化）。
        实测 **109/109 个星座的真实星点 100% 配上**，每个星座只差 1 个
        = **非星点的「授予技能」节点**（记为 `None`，不计点数）。

    ⚠ 某星座只要**拿不到根**（`pos[0] is None`）就**整颗跳过** —— 宁可不出候选，也不瞎猜顺序。

    ⚠ `None` 位 = 该星座的 **proc / 授予技能节点**（`<前缀>g_skill`，按**记录名前缀**认，
    **不要**用 tag 变换法：实测差 1）。它**要花 1 点**但不给收益（见下方 `realloc` 里的说明）。
    """
    global _SG
    if _SG is not None:
        return _SG
    devs = DV._load('devotions.json', {}) or {}
    isk = DV._load('item_skills.json', {}) or {}
    tree = DV._tree()

    def _sig(d):
        out = {}
        for k, v in (d or {}).items():
            if k in _SG_IGN:
                continue
            if isinstance(v, list):
                v = v[0] if v else None
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[k] = round(float(v), 4)
        return out

    def _dist(a, b):
        return sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in set(a) | set(b))

    recs_by_tag, sk_by_tag, skill_by_pre = {}, {}, {}
    for rec, o in devs.items():
        if o.get('is_skill'):
            # 授予技能/proc 记录的归属**只能按记录名前缀**（`tier3_05g_skill` → `tier3_05`）。
            # ⚠ 别用 tag 变换法：实测**差 1**（`tier3_04g_skill` 是 C04 的，tag 却写
            #   `tagDevotionEffectC05`）—— 这也是陷阱 #66 里那条「tag 变换」的**适用边界**。
            skill_by_pre.setdefault(DV.star_prefix(rec.replace('_skill.dbr', '.dbr')), rec)
            continue
        recs_by_tag.setdefault(o.get('tag'), []).append((rec, _sig(o.get('stats'))))
    for skid, o in isk.items():
        sk_by_tag.setdefault(o.get('skillDisplayName'), []).append((skid, _sig(o)))

    out = {}
    cg = calc_graph()
    for idx, con in tree.items():
        stars = con.get('stars') or []
        recs = recs_by_tag.get(con.get('tag')) or []
        cands = sk_by_tag.get(con.get('tag')) or []
        blk = cg.get(frozenset(stars))                      # 按**星位 sk 集合**认块（不靠 tag）
        if not stars or not recs or not cands or not blk:
            continue                                        # 星位图拿不到 ⇒ 整颗跳过
        if set(blk['buttons'].values()) != set(stars):
            continue                                        # 两套数据对不上 ⇒ 跳过（保守）
        pairs = sorted((_dist(rs, ss), ri, si)
                       for ri, (_r, rs) in enumerate(recs)
                       for si, (_s, ss) in enumerate(cands))
        used_r, used_s, sk2rec = set(), set(), {}
        for _d, ri, si in pairs:                     # 贪心最小距离配对
            if ri in used_r or si in used_s:
                continue
            used_r.add(ri)
            used_s.add(si)
            sk2rec[cands[si][0]] = recs[ri][0]
        # ★ 按**星位序号**排（`button 1` = 根：唯一没有父星的）
        pos = [sk2rec.get(blk['buttons'][k]) for k in sorted(blk['buttons'])]
        if pos[0] is None:
            continue
        ch = {int(p) - 1: [int(c) - 1 for c in cs] for p, cs in blk['children'].items()}
        pre = next((DV.star_prefix(r) for r in pos if r), None)
        out[idx] = {'pos': pos, 'children': ch, 'nodes': len(stars), 'prefix': pre,
                    'proc': skill_by_pre.get(pre),
                    'name': DV._tags().get(con.get('tag') or '') or str(idx)}
    _SG = out
    return out


def rooted_subsets(sg, maxn=2):
    """树上**含根且连通**、节点数 ≤ `maxn` 的子集（= 游戏里合法的买法；**每个节点 1 点**）"""
    out = []

    def rec(incl, frontier):
        out.append(list(incl))
        if len(incl) >= maxn:
            return
        for i, f in enumerate(frontier):
            rec(incl + [f],
                frontier[i + 1:] + [c for c in sg['children'].get(f, []) if c not in incl])

    rec([0], list(sg['children'].get(0, [])))
    return out


def _sc(v):
    """评分：`v` = `ev()` 的返回值 (面板, 实战, rep)。"""
    return _OBJ.score(v[2], OBJ)



def groups():
    """全部可选星座：{idx: (名字, [星点记录], 需亲和力)}"""
    devs = DV._load('devotions.json', {}) or {}
    tree = DV._tree()
    g = {}
    for rec, d in devs.items():
        if d.get('is_skill'):
            continue
        idx, con = DV.constellation_of(rec, tree)
        if con is None:
            continue
        e = g.setdefault(idx, {'con': con, 'stars': [], 'name': DV._tags().get(con.get('tag') or '')})
        e['stars'].append(rec)
    return g


def rank():
    """单个星座增量排行榜：对每个「未选中的星座」单独加进现状，按真实 DPS 排序。

    为什么需要它：`select_coherent` 按「加成 % 加和」评分，与真实伤害**不同向**
    （实测它的整套建议 −10.1%）。排行榜用真实 `final_report` 排序，
    并且能顺手看到**需亲和力**（判断游戏里点不点得开）。
    """
    base = ev(CUR)
    G = groups()
    have = set()
    for r in CUR:
        idx, _c = DV.constellation_of(r)
        if idx is not None:
            have.add(idx)
    rows = []
    for idx, g in G.items():
        if idx in have or not g['name'] or len(g['stars']) > 4:
            continue
        cand = dict(CUR)
        cand.update({r: 1 for r in g['stars']})
        v = ev(cand)
        rows.append((_sc(v), g['name'], len(g['stars']), (g['con'] or {}).get('affinity')))
    rows.sort(reverse=True)
    print('基线（现有 %d 星点）＝ 面板 %s ｜ 实战 %s ｜ **评分目标 %s**'
          % (len(CUR), format(base[0], ',.0f'), format(base[1], ',.0f'), _OBJ.label(OBJ)))
    print()
    print('%-16s %4s %-30s %12s %8s' % ('星座', '星点', '需亲和力', '评分', '增益'))
    print('-' * 78)
    for v, nm, n, aff in rows[:15]:
        print('%-16s %4d %-30s %12s %7.1f%%'
              % (nm, n, str(aff), format(v, ',.0f'), v / base[0] * 100 - 100))
    json.dump({'base': list(base[:2]), 'rows': [[v, nm, n, aff] for v, nm, n, aff in rows]},
              open(os.environ.get('DEV_TOP', 'data/scratch/devotion_top.json'),
                   'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('\n已落 %s' % os.environ.get('DEV_TOP', 'data/scratch/devotion_top.json'))


def marginal():
    """**现有星座的拆点损失**：整颗拆掉，看掉多少。

    为什么需要它：`--rank` 只回答「再加一颗选哪颗」，回答不了
    「**现有 32 点里有没有白投的**」。GD 里虔诚点可以在灵魂向导处退，
    所以「换掉一颗低价值的」是合法操作。损失 ≈ 0 的星座就是候选弃子。
    """
    base = ev(CUR)
    G = groups()
    by_idx = {}
    for r in CUR:
        idx, _c = DV.constellation_of(r)
        if idx is not None:
            by_idx.setdefault(idx, []).append(r)
    rows = []
    for idx, recs in by_idx.items():
        cand = {r: 1 for r in CUR if r not in recs}
        v = ev(cand)
        nmz = (G.get(idx) or {}).get('name') or str(idx)
        rows.append((_sc(base) - _sc(v), nmz, len(recs)))
    rows.sort()
    print('基线（现有 %d 星点 / %d 颗星座）＝ 面板 %s ｜ 实战 %s'
          % (len(CUR), len(by_idx), format(base[0], ',.0f'), format(base[1], ',.0f')))
    print()
    print('%-18s %4s %12s %9s' % ('星座', '星点', '拆掉后损失(评分)', '占基线'))
    print('-' * 48)
    for loss, nmz, n in rows:
        print('%-18s %4d %12s %8.2f%%' % (nmz, n, format(loss, ',.0f'),
                                          -loss / _sc(base) * 100 if _sc(base) else 0))
    json.dump({'base': list(base[:2]),
               'rows': [[l, nmz, n] for l, nmz, n in rows]},
              open('data/scratch/devotion_marginal.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('\n已落 data/scratch/devotion_marginal.json')


def _affinity_ok(recs):
    """亲和力自洽检查（游戏里点不出来 = 方案无效）。"""
    try:
        ok, _msg, _got, _ = DV.check_affinity([{'record': r} for r in recs])
        return bool(ok)
    except Exception:                                            # noqa: BLE001
        return True


def realloc(threshold=0.25):
    """**整池重排**：先退掉「拆掉损失 < threshold% 基线」的星座，再贪心补回最优的。

    ⚠ 判读要点（别把「模型盲区」当成「白投的点」）：
      `乌龟`（护甲/吸收）`海鳗`（DA）`抉择之地`（三围）这类**纯防御向**星座，
      在本模型里恒为 0 —— 因为 DPS 模型**不算防御**。它们的 0 是**模型边界**，
      不是「没价值」。`--realloc` 给出的方案 = **用防御换伤害**，
      要按这个口径读（同族的模型盲区还有技能 `血莽`：只降敌方 DA）。

    ★ 输出契约（2026-09-21 加）：**落盘结果永不劣于基线**。
      `ev()` 不单调（加星点可能挤占 proc 池而掉 DPS），退款又是「单颗独立判损失」，
      所以旧实现会落盘更差的方案（实测 133,396 → 128,139）。现在全程记 **best-seen**
      并于收尾与基线取优；没超过基线就写**基线原样**并在 JSON 里
      `"adopted": false` —— 消费方（`DEV_SRC`）拿到的永远是「不比现状差」的配点。
    """
    base = ev(CUR)
    G = groups()
    SG = star_graph()
    # ★★ 按星座分组时**必须连 proc 记录一起归位**（2026-09-21 修）：
    #   `DV.constellation_of()` 认不出 `<前缀>g_skill` 这类**授予技能/proc**记录
    #   ⇒ 旧实现把它们排除在 `keep` 之外，退款阶段一跑就被**静默丢掉**
    #   （实测方案相对存档少了 `tier1_08e_skill`「刺客的标记」= **−33% 穿刺抗**的 debuff proc！）。
    #   这里改用**记录名前缀**归位；连前缀都认不出的记录一律进 `orphan` 并**永远保留**。
    pre2idx = {sg['prefix']: i for i, sg in SG.items() if sg.get('prefix')}
    by_idx, orphan = {}, []
    for r in CUR:
        idx, _c = DV.constellation_of(r)
        if idx is None:
            idx = pre2idx.get(DV.star_prefix(r.replace('_skill.dbr', '.dbr')))
        if idx is not None:
            by_idx.setdefault(idx, []).append(r)
        else:
            orphan.append(r)
    if orphan:
        print('  ⚠ %d 条记录认不出星座（已**原样保留**，不参与退款）：%s'
              % (len(orphan), '、'.join(x.split('/')[-1] for x in orphan[:4])))
    keep, refunded = {}, []
    for idx, recs in by_idx.items():
        cand = {r: 1 for r in CUR if r not in recs}
        loss = _sc(base) - _sc(ev(cand))
        nmz = (G.get(idx) or {}).get('name') or str(idx)
        if loss / _sc(base) * 100 < threshold if _sc(base) else 0:
            refunded.append((nmz, len(recs), loss))
        else:
            keep[idx] = recs
    print('基线 %s ｜ 退掉低价值星座 %d 颗（共 %d 星点）'
          % (format(base[0], ',.0f'), len(refunded), sum(n for _, n, _ in refunded)))
    for nmz, n, loss in sorted(refunded, key=lambda x: x[2]):
        print('   − %-16s %d 星 ｜ 拆掉损失 %s' % (nmz, n, format(loss, ',.0f')))
    cur = {r: 1 for recs in keep.values() for r in recs}
    for r in orphan:                      # ★ 认不出星座的记录**永不丢**（见上方说明）
        cur[r] = 1
    # ★★ 精确亲和力（2026-09-21）：退款会**丢 `given`** ⇒ 必须复检「留下的星座还够不够门槛」
    _tree = DV._tree()
    aff = aff_of(cur, _tree, SG)
    _bad_keep = [SG[i]['name'] for i in keep
                 if not req_ok(i, aff, _tree) and i in SG]
    print('  亲和力（点满的星座所给）: %s' % dict(aff))
    if _bad_keep:
        print('  ⚠⚠ **退款后这些星座的门槛已不够**（游戏里点不出来）：%s'
              % '、'.join(_bad_keep))
        print('     ⇒ **撤销退款**，保留原样（宁可不动，也不交付「点不出来」的配点）')
        keep = dict(by_idx)              # 全部保留
        refunded = []
        cur = dict(CUR)
        aff = aff_of(cur, _tree, SG)
    print('  剩余 %d 节点，预算 %d ⇒ 可再补 %d 点'
          % (len(cur), BUDGET, BUDGET - len(cur)))
    print()
    print('%-26s %4s %12s %8s' % ('补入星座 / 连通子集', '节点', '面板', '增益'))
    print('-' * 72)
    # ★★ 兜底①②（2026-09-21）：重排**只允许变好**。两个原因：
    #   ① `ev()` 不是单调的 —— 加星点**也可能让 DPS 变低**（授技 proc 会挤占
    #      proc/WPS 池，并改变减抗结算顺序）。旧实现每步只挑「候选之间最好」的那颗，
    #      **从不与当前分比较** ⇒ 会**一路下坡**；
    #   ② 退款阶段按「单颗损失 < threshold%」**独立**判定，多颗叠加后可能远超阈值，
    #      而贪心补点未必补得回来 ⇒ 最终结果可能低于基线（实测 133,396 → 128,139）。
    #   所以全程记 **best-seen**，收尾时与基线取优 ⇒ 落盘结果**永不劣于基线**。
    best_score = _sc(base)
    best_recs = dict(CUR)
    step = 0
    SG = star_graph()
    print('  连线顺序可解星座：%d / %d' % (len(SG), len([i for i in G if G[i]['name']])))
    while len(cur) < BUDGET:
        free = BUDGET - len(cur)
        aff = aff_of(cur, _tree, SG)                # ★ 每轮重算**精确亲和力**
        have = set()
        for r in cur:
            i2, _c2 = DV.constellation_of(r)
            if i2 is None:
                i2 = pre2idx.get(DV.star_prefix(r.replace('_skill.dbr', '.dbr')))
            if i2 is not None:
                have.add(i2)
        cands = []                                  # [(记录集, 标签, 成本)]
        # ⚠⚠ proc（Celestial Power）记录**故意不喂进模型**（2026-09-21 实测）：
        #   模型**没有 proc 触发率 / 指派门控**，把攻击型 Celestial Power 当**常驻攻击技**
        #   ⇒ 单条就能把面板抬几十个百分点（实测「雷霆暴怒」+44.6%、「激流漩涡」+18.7%）
        #   —— 是**伪影**，不是收益。所以：proc **节点照样算成本**（游戏里要点亮它就得花 1 点），
        #   但不给它记任何收益 ⇒ 搜索自然会把「为拿 proc 而花的那一点」判成亏，从而避开。
        #   （要真正用上 proc，得先补「触发率 × 指派技能」的门控，属模型级改动，待批准。）
        for idx, sg in SG.items():
            if idx in have or not req_ok(idx, aff, _tree):
                continue        # ★★ **精确亲和力门槛**（旧代码用近似的 `check_affinity`）
            # ① 整颗星座：成本 = **节点数**（含 proc）
            if sg['nodes'] <= min(free, 5):
                rs = [r for r in sg['pos'] if r]
                if rs:
                    cands.append((rs, '%s（整颗）' % sg['name'], sg['nodes'], idx))
            # ② ★ 单星粒度：含根的连通子集 —— 「剩下几点花掉」只有这条路
            for sub in rooted_subsets(sg, maxn=min(free, 3)):
                rs = [sg['pos'][p] for p in sub if sg['pos'][p]]
                if rs:
                    cands.append((rs, '%s[补 %d 节点]' % (sg['name'], len(sub)), len(sub),
                                  idx))
        # ③ 兜底：连线顺序不可解的星座，仍按旧口径整颗试（只数非 proc 星点）
        for idx, g in G.items():
            if idx in SG or idx in have or not g['name'] or len(g['stars']) > 4:
                continue
            if len(g['stars']) > free or not req_ok(idx, aff, _tree):
                continue
            cands.append((g['stars'], g['name'], len(g['stars']), idx))
        best = None
        for rs, label, cost, idx in cands:
            cand = dict(cur)
            cand.update({r: 1 for r in rs})
            if len(cand) > BUDGET:
                continue
            v = _sc(ev(cand))
            if best is None or v > best[0]:
                best = (v, label, rs, cost, idx)
        if best is None:
            print('  （没有「亲和力达标 + 装得下」的候选了）')
            break
        v, label, rs, cost, _bidx = best
        cur.update({r: 1 for r in rs})
        step += 1
        print('%-26s %4d %12s %7.2f%%'
              % (label, cost, format(v, ',.0f'),
                 v / best_score * 100 - 100 if best_score else 0))
        if v > best_score:                      # ★ best-seen：只有**真变好**才记
            best_score, best_recs = v, dict(cur)
    # ---- ★★ 精确亲和力 + **可达性（拓扑顺序）** ----------------------------
    #   最终亲和力满足所有需求 **≠** 点得出来：X 要 Prim 5（只由 Y 给）、Y 要 Asc 15
    #   （只由 X 给）⇒ 死锁。亲和力只增 ⇒ 反复取「当下就够门槛」的星座即可判定。
    _new_idx = set()
    for _r in best_recs:
        _i3, _c3 = DV.constellation_of(_r)
        if _i3 is None:
            _i3 = pre2idx.get(DV.star_prefix(_r.replace('_skill.dbr', '.dbr')))
        if _i3 is not None:
            _new_idx.add(_i3)
    _aff0 = aff_of({r: 1 for i in keep if i in SG for r in SG[i]['pos'] if r}, _tree, SG)
    _pend = [i for i in _new_idx if i not in keep]
    _okf, _ord = feasible(_pend, _aff0, _tree, SG)
    _fin_aff = aff_of(best_recs, _tree, SG)
    print()
    print('  亲和力：退完包袱 %s → 方案点满 %s' % (dict(_aff0), dict(_fin_aff)))
    if _okf:
        print('  可达性：✓ 可按顺序逐颗解锁（%d 颗新星座）'
              % len(_pend))
    else:
        print('  ⚠⚠ 可达性：**✗ 死锁** —— 这些星座按任何顺序都点不出来：%s'
              % '、'.join(SG[i]['name'] for i in _pend if i not in _ord))

    # ---- ★ best-seen × 基线 取优：绝不落盘比基线更差的方案 ------------------
    _bsc = _sc(base)
    _sw = 0.05                              # 数值噪声容差（<0.05 点视为「没有变好」）
    if best_score > _bsc + _sw and _okf:
        fin, cur = ev(best_recs), dict(best_recs)
        adopted = True
        print()
        print('  ✓ 采纳重排（best-seen）：评分 %s → %s（%+.2f%%）'
              % (format(_bsc, ',.0f'), format(best_score, ',.0f'),
                 best_score / _bsc * 100 - 100 if _bsc else 0))
    else:
        fin, cur = base, dict(CUR)
        adopted = False
        print()
        if not _okf:
            print('  ⚠ **放弃重排**：方案在游戏里**点不出来**（亲和力死锁）⇒ 落「基线原样」')
        else:
            print('  ⚠ **放弃重排**：最佳尝试（%s）也没超过基线（%s）⇒ 落「基线原样」'
                  % (format(best_score, ',.0f'), format(_bsc, ',.0f')))
            print('    （这是不劣化兜底：宁可什么都不换，也不交付更差的星座配点。')
            print('      退掉的那些星座在本模型里恒为低分 ⇒ 大概率是**防御向模型盲区**，')
            print('      不是「白投的点」；要换得先看 `--marginal` 的真实损失。）')
    print()
    print('=== 重排结果 ===')
    print('  星点 %d / %d ｜ 面板 %s → %s（%+.2f%%）｜ 实战 %s → %s ｜ 评分 %s → %s（%+.2f%%）'
          % (len(cur), BUDGET, format(base[0], ',.0f'), format(fin[0], ',.0f'),
             fin[0] / base[0] * 100 - 100, format(base[1], ',.0f'), format(fin[1], ',.0f'),
             format(_sc(base), ',.0f'), format(_sc(fin), ',.0f'),
             _sc(fin) / _sc(base) * 100 - 100 if _sc(base) else 0))
    # 亲和力自洽：**用精确规则复检**（`aff_of` × 每颗星座在方案里的门槛），
    # 不再拿 `DV.check_affinity` 那个「每星 +1」的近似粗筛当结论 —— 它会给点了一半的
    # 星座白送亲和力，并**漏掉 `given` 的第二种亲和力** ⇒ 本来就会误报 False。
    _aff = aff_of(cur, _tree, SG)
    _idxs = set()
    for _r in cur:
        _i = DV.constellation_of(_r)[0]
        if _i is None:                       # proc 记录认不出星座 ⇒ 按记录名前缀反查
            _pre = DV.star_prefix(_r.replace('_skill.dbr', '.dbr'))
            for _i2, _sg2 in SG.items():
                if _sg2.get('prefix') == _pre:
                    _i = _i2
                    break
        if _i is not None:
            _idxs.add(_i)
    _myl = []
    for _i in sorted(_idxs):
        _need = (_tree.get(_i, {}).get('affinity') or [])
        _miss = [(a, v, _aff.get(a, 0)) for a, v in _need if _aff.get(a, 0) < v]
        if _miss:
            _myl.append('%s 缺 %s' % ((SG.get(_i) or {}).get('name') or _i, _miss))
    print('  亲和力自洽：%s（精确规则 ｜ %s）'
          % ('是' if not _myl else '**否** —— ' + '；'.join(_myl),
             '、'.join('%s %d' % (k, v) for k, v in sorted(_aff.items()))))
    json.dump({'base': list(base[:2]), 'opt': list(fin[:2]),
               'adopted': adopted,                      # ★ false = 已退回基线（不劣化）
               'base_score': _bsc, 'best_attempt_score': best_score,
               'refunded': [[n, k, l] for n, k, l in refunded],
               'keep': {str(i): [DV._tags().get((G[i]['con'] or {}).get('tag') or '') or str(i),
                                 len(v)] for i, v in keep.items()},
               'records': sorted(cur)},
              open('data/scratch/devotion_realloc.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('  已落 data/scratch/devotion_realloc.json（adopted=%s）' % adopted)


def main():
    global OBJ
    if '--objective' in sys.argv:
        i = sys.argv.index('--objective')
        OBJ = sys.argv[i + 1] if len(sys.argv) > i + 1 else OBJ
    if '--rank' in sys.argv:
        rank()
        return
    if '--marginal' in sys.argv:
        marginal()
        return
    if '--realloc' in sys.argv:
        i = sys.argv.index('--realloc')
        th = float(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 0.25
        realloc(th)
        return
    print('=== 基线 ===')
    b = ev(CUR)
    print('  现有 %d 星点 → 面板 %s ｜ 实战 %s ｜ **评分目标 %s**'
          % (len(CUR), format(b[0], ',.0f'), format(b[1], ',.0f'), _OBJ.label(OBJ)))
    print('  目标：再加 %d 点（池 %d）' % (BUDGET - len(CUR), BUDGET))
    print()

    G = groups()
    picked = set()
    for rec in CUR:
        idx, _ = DV.constellation_of(rec)
        if idx is not None:
            picked.add(idx)
    cur_recs = dict(CUR)
    cur = b
    step = 0
    while len(cur_recs) < BUDGET:
        left = BUDGET - len(cur_recs)
        best = None
        for idx, g in G.items():
            if idx in picked:
                continue
            need = len(g['stars'])
            if need > left or not g['name']:
                continue
            cand = dict(cur_recs)
            cand.update({r: 1 for r in g['stars']})
            ok, msg, got, _ = DV.check_affinity([{'record': r} for r in cand])
            if not ok:
                continue
            v = ev(cand)
            if best is None or _sc(v) > _sc(best[0]):
                best = (v, idx, g, need, msg)
        if best is None:
            print('  （没有能再加的星座了）')
            break
        step += 1
        v, idx, g, need, msg = best
        picked.add(idx)
        cur_recs.update({r: 1 for r in g['stars']})
        d = _sc(v) - _sc(cur)
        cur = v
        print('  %d) +%-14s %2d 星 → 面板 %s（%+.1f%%）｜ 实战 %s'
              % (step, g['name'], need, format(v[0], ',.0f'),
                 v[0] / b[0] * 100 - 100, format(v[1], ',.0f')))
    print()
    print('=== 贪心结果 ===')
    print('  星点 %d / %d ｜ 面板 %s（基线 %s，%+.1f%%）｜ 实战 %s'
          % (len(cur_recs), BUDGET, format(cur[0], ',.0f'), format(b[0], ',.0f'),
             cur[0] / b[0] * 100 - 100, format(cur[1], ',.0f')))
    json.dump({'base': b[:2], 'opt': cur[:2], 'records': sorted(cur_recs),
               'arch': ARCH, 'budget': BUDGET},
              open(DEV_OUT, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print('  已落 %s' % DEV_OUT)


if __name__ == '__main__':
    main()
