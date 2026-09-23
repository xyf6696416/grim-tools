# -*- coding: utf-8 -*-
"""affix_fit.py —— 给方案里的**绿装槽**补上合法词缀，按真实 DPS 择优。

为什么存在（2026-09-20）
------------------------
`gd/opt.py` 早就有完整的词缀建模（`_AFFIX_DB` 注入 `_IT` ⇒ `contrib()` 能直接算词缀；
`ev()` 会折叠方案元组的第 4/5 位），但：
  · `_parse_affixes()` 只找 `data/itemdb.js`，而真实文件在 `data/cache/itemdb.js`
    ⇒ `_AFFIX_DB` 恒 0 条 ⇒ `POOL_PRE/POOL_SUF` 全空 ⇒ **词缀维度一直是死的**（已修）；
  · autobuild / tune_dps 这条链**从不调用** `compute_best_affixes()`，
    也从不填元组的第 4/5 位 ⇒ 实测 16 份方案全是 0 前缀 0 后缀。

本工具的定位：**不动搜索器**，在既有方案上做一层「槽位级词缀最优」——
词缀与底材可加且槽位间独立，所以这一层是严格正确的分解（不引入近似）。

合法性的三条硬规则（缺一条就会产出「游戏里静默不生效」的方案）
------------------------------------------------------------
1. **类别兼容**：词缀的 `cls` 必须包含底材的类别码（底材的 `l` 字段，如 `c24` 施法匕首）。
   ★ 只校验 `affix_ok`（适用位）是不够的 —— 它不比对底材类别，
     实测会把 c28~c32（近战单手）的前缀配到 c24 匕首上。
2. **词缀条数上限**：底材 `maxAffixes`（绿装多为 2，**也有 1**；紫/蓝为 0）。
   ★ 实测 `it2063`（施法匕首）`maxAffixes=1` ⇒ 只能带前缀**或**后缀，不能两条。
3. **等级段**：词缀 `k` ≤ 目标 ilvl（由 `POOL_PRE/POOL_SUF` 在 `GD_MAX_ILVL` 下预先过滤）。

用法
----
    python tools/affix_fit.py <方案.json> --char Sam --arch raven_nightblade \
        --alloc <加点.json> [--ilvl 71] [--topn 12] [--out 新方案.json]
"""
from __future__ import annotations

import argparse
import io
import itertools
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'tools'))

_CLS_RE = re.compile(r'^c\d+$')


def base_cls(O, gid):
    """底材类别码（GT `l` 字段）。★ 不是需求等级 —— `_req_lvl()` 把它读成等级是**误读**。"""
    l = (O._IT.get(gid) or {}).get('l')
    return l if isinstance(l, str) and _CLS_RE.match(l.strip()) else None


def max_affixes(O, gid):
    o = O._IT.get(gid) or {}
    if (o.get('f') or '') != 'Rare':
        return 0
    ma = o.get('maxAffixes')
    return 2 if ma is None else int(ma)


def legal_pool(O, base, slot, kind):
    """该(底材,槽位,种类)下**合法**的词缀列表。"""
    cls = base_cls(O, base)
    pool = (O.POOL_PRE if kind == 'pre' else O.POOL_SUF).get(slot) or []
    out = []
    for gid in pool:
        d = O._AFFIX_DB.get(gid) or {}
        acls = d.get('cls') or []
        if cls and acls and cls not in acls:
            continue
        if not O.affix_ok(gid, slot):
            continue
        out.append(gid)
    return out


def score(O, gid):
    _r, o, _sk = O.contrib(gid)
    return sum(v * O.W_DMG.get(k, 0.0) for v, k in zip(o, O.FKEYS))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('plan')
    ap.add_argument('--char', default='Sam')
    ap.add_argument('--arch', default=os.environ.get('GD_ARCHETYPE', ''))
    ap.add_argument('--alloc', default='')
    ap.add_argument('--ilvl', type=int, default=71)
    ap.add_argument('--topn', type=int, default=12, help='每槽每类保留的候选数')
    ap.add_argument('--out', default='')
    a = ap.parse_args()

    if a.alloc:
        os.environ['GD_SKILL_JSON'] = a.alloc
    os.environ['GD_ARCHETYPE'] = a.arch
    os.environ['GD_MAX_ILVL'] = str(a.ilvl)
    os.environ.setdefault('GD_QUIET', '1')

    from tune_dps import load_opt                    # noqa: E402
    import plan_dps as PD                            # noqa: E402
    O = load_opt(a.arch)

    sol = {k: list(v) for k, v in
           json.load(io.open(a.plan, encoding='utf-8')).items()}
    for k in sol:
        sol[k] = sol[k] + [None] * (5 - len(sol[k]))

    base = PD.dps_of(a.char, sol, a.arch)
    print('词缀库 %d 条 ｜ 原始方案 DPS %s'
          % (len(O._AFFIX_DB), format(base['dps'], ',.0f')))
    r0, _o = O.ev({k: tuple(v) for k, v in sol.items()})
    print('当前抗性：' + ' ｜ '.join('%s %.0f/%.0f' % (t, r0.get(t, 0), O.NEED[t])
                                     for t in O.TYPES if O.NEED[t]))
    print()

    def fullres(s):
        r, _ = O.ev({k: tuple(v) for k, v in s.items()})
        return all(r.get(t, 0.0) >= O.NEED[t] - 1e-9 for t in O.TYPES), r

    total_gain = 0.0
    for slot in O.SLOTS:
        b = sol[slot][0]
        ma = max_affixes(O, b)
        if ma <= 0:
            continue
        cands = {}
        for kind in ('pre', 'suf'):
            lst = legal_pool(O, b, slot, kind)
            lst.sort(key=lambda g: -score(O, g))
            cands[kind] = lst[:a.topn]
            print('  %-5s %-3s 合法 %4d 条（取前 %d）｜ 榜首 %s 分 %+.1f'
                  % (slot, kind, len(legal_pool(O, b, slot, kind)), len(cands[kind]),
                     cands[kind][0] if cands[kind] else '（无）',
                     score(O, cands[kind][0]) if cands[kind] else 0.0))
        combos = [()] + [(g,) for kind in ('pre', 'suf') for g in cands[kind]]
        if ma >= 2:
            combos += list(itertools.product(cands['pre'], cands['suf']))
        best, bi = base['dps'], None
        for combo in combos:
            t = list(sol[slot])
            for g in combo:
                t[3 if g in cands['pre'] else 4] = g
            trial = dict(sol, **{slot: t})
            ok, _r = fullres(trial)
            if not ok:
                continue
            d = PD.dps_of(a.char, trial, a.arch)['dps']
            if d > best:
                best, bi = d, (t, combo)
        if bi:
            sol[slot] = bi[0]
            gain = (best / base['dps'] - 1.0) * 100.0
            total_gain = gain
            print('    → 选中 %s ｜ 底材 %s（maxAffixes=%d）｜ DPS %s（%+.2f%%）'
                  % (list(bi[1]), O.name_of(b), ma, format(best, ',.0f'), gain))
    print()
    final = PD.dps_of(a.char, sol, a.arch)
    ok, r = fullres(sol)
    print('最终 DPS %s（原 %s，%+.2f%%）｜ 满抗 %s'
          % (format(final['dps'], ',.0f'), format(base['dps'], ',.0f'),
             (final['dps'] / base['dps'] - 1.0) * 100.0, '✓' if ok else '✗'))
    for t in O.TYPES:
        if O.NEED[t]:
            print('   %-4s %.0f/%.0f %s' % (t, r.get(t, 0), O.NEED[t],
                                            '★封顶' if r.get(t, 0) >= O.NEED[t] - 1e-9 else '✗缺口'))
    out = a.out or a.plan.replace('.json', '_afx.json')
    json.dump({k: [x for x in v] for k, v in sol.items()},
              io.open(out, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('→ %s' % out)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
