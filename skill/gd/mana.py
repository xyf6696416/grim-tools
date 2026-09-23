# -*- coding: utf-8 -*-
r"""gd_mana.py —— **蓝耗 / 续航分析器**（2026-09-18 新增）

为什么需要它：伤害模型 `final_report` **完全不算蓝**，而法系变身（鸦人）的真实瓶颈
常常是**续航而不是 DPS**。实测 Sam 63 级鸦人：寒冰之爪 26 级 53 蓝/次 × **2.85 次/秒**
（星座给了 +31% 攻速，耗蓝也同比例上涨）= **160.9 蓝/秒**。

⚠ 一个曾经犯过的错（务必别再犯）：把「单次蓝耗」当「每秒蓝耗」报给用户。
寒冰之爪 53 + 霜暴 41 是**单次**，不是每秒 —— 每秒要乘频率（本例 ×2.85）。

输出四块
--------
1. **逐技能蓝耗**：单次 / 频率 / 蓝每秒（频率取自真实模型的输出循环）
2. **回蓝来源汇总**：装备 + 星座的 `characterManaRegen` / `%` / 能量吸收 / 上限
3. **技能等级 → DPS / 蓝耗 曲线**（含 `DPS per 蓝` 效率）—— 找「省蓝最划算」的等级
4. **可替换的回蓝 / 降耗候选**（镶嵌 `materia` + 附魔 `enchants`）

用法
----
```bash
$PY gd_mana.py berserker_nightblade --plan plans/rc_4.json \
    --profile plans/raven_dev_profile.json --char Sam
```
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

MANA_FIELDS = (
    'characterManaRegen',            # 每秒回蓝（绝对）
    'characterManaRegenModifier',    # 回蓝 %
    'characterMana',                 # 能量上限 +
    'characterManaModifier',         # 能量上限 %
    'skillManaCostReduction',        # 技能耗蓝 % 减免
    'characterEnergyAbsorptionPercent',   # 击杀时能量吸收 %
)


def mana_cost(db, rec, level):
    """技能在指定等级的单次蓝耗（取 `skillManaCost` 逐级数组）。"""
    from . import rotation as R
    f = R.fields_with_buff(db, rec) or {}
    v = f.get('skillManaCost')
    return float(R._at(v, level)) if v else 0.0


def grab(fields):
    """从字段表里挑出与蓝/能量有关的非零项。"""
    out = {}
    for k in MANA_FIELDS:
        v = (fields or {}).get(k)
        if not v:
            continue
        x = v[0] if isinstance(v, (list, tuple)) and v else v
        if isinstance(x, (int, float)) and x:
            out[k] = float(x)
    return out


def norm_plan_row(v):
    seq = v if isinstance(v, (list, tuple)) else [v]
    out = [x if isinstance(x, str) else None for x in seq]
    while len(out) < 5:
        out.append(None)
    return out[:5]


def main():
    ap = argparse.ArgumentParser(description='蓝耗 / 续航分析器')
    ap.add_argument('archetype')
    ap.add_argument('--plan', required=True)
    ap.add_argument('--profile', required=True, help='含 skills/devotions 的 profile')
    ap.add_argument('--levels', default='', help='额外考察的技能等级范围，如 icicles=14:26')
    ap.add_argument('--char', default='',
                    help='角色名（给了就从**存档实际**读回蓝/能量字段——方案 JSON 与存档在 ilvl 档位上可能不同）')
    ap.add_argument('--top', type=int, default=8, help='候选替换件显示条数')
    ap.add_argument('--out', default='')
    a = ap.parse_args()

    from . import rotation as R
    from . import dbr as DB

    db = DB.open_all()
    plan = {k: norm_plan_row(v) for k, v in
            json.load(open(a.plan, encoding='utf-8')).items()}
    prof = json.load(open(a.profile, encoding='utf-8'))
    levels = {s['skill']: s['level'] for s in prof['skills']}
    dev = {d['record']: 1 for d in (prof.get('devotions') or [])}
    folded = R.fold_plan(plan)
    # ★★ 2026-09-22：武器构成（判武器类型硬前提门控）。`plan` 里的 gid 已含武器槽
    #   ⇒ 能推就推；推不出来（plan 无武器）⇒ `None` ⇒ 不门控（零漂移）。
    from . import procs as _PCS
    _wst = _PCS.weapon_state([_g for _row in plan.values() for _g in (_row or []) if _g])

    rep = R.final_report(a.archetype, levels, folded=folded, db=db,
                         devotion_levels=dev or None, weapon_st=_wst)

    L = []
    L.append('# 蓝耗 / 续航分析')
    L.append('')
    L.append('> 真实伤害模型 **不算蓝** —— 这份是把「每秒蓝耗」单独算出来的结果。')
    L.append('> ⚠ 注意区分**单次蓝耗**与**每秒蓝耗**：单次要乘输出频率。')
    L.append('')

    # ---- ① 逐技能蓝耗
    L.append('## 一、逐技能蓝耗（频率取自真实输出循环）')
    L.append('')
    L.append('| 技能 | 等级 | 单次蓝 | 频率/秒 | **蓝每秒** | 占比 |')
    L.append('|---|---|---|---|---|---|')
    rows = []
    for rec, h in (rep.get('skills') or {}).items():
        lv = h.get('level') or levels.get(rec) or 0
        freq = h.get('freq') or 0
        c = mana_cost(db, rec, lv)
        if freq <= 0 or c <= 0:
            continue
        rows.append((c * freq, rec, lv, c, freq))
    rows.sort(reverse=True)
    tot_mps = sum(r[0] for r in rows) or 1.0
    for mps, rec, lv, c, freq in rows:
        L.append('| %s | %d | %.0f | %.2f | **%.1f** | %.0f%% |'
                 % (rec.split('/')[-1], lv, c, freq, mps, mps / tot_mps * 100))
    L.append('| **合计** | | | | **%.1f** | 100%% |' % tot_mps)
    L.append('')
    L.append('> 变身技（`skill_shapeshift.tpl`）的蓝耗是**一次性**（激活时扣一次），不计入每秒。')
    L.append('')

    # ---- ② 回蓝来源
    L.append('## 二、回蓝来源（现状装备 + 星座）')
    L.append('')
    tot = {}
    # ★ 优先从**存档实际**读（方案 JSON 与存档记录在 ilvl 档位上可能不同，
    #   实测同一套装备 `characterMana` 方案口径 0 / 存档口径 +1070，会直接把能量池算小一半）
    if a.char:
        try:
            import gd_dps_check as _C
            import gd_char_compare as _CC
            _c = _C.load_char(a.char, '', False)
            _sv = _CC.survival(_c, _c['db'])
            for _k in MANA_FIELDS:
                if _sv.get(_k):
                    tot[_k] = float(_sv[_k])
            print('（回蓝/能量来源：读存档实际）')
        except Exception as _ex:
            print('（读存档失败，退回方案口径：%s）' % _ex)
    if not tot:
        import gd_gear_compare as GC
        for slot, recs in GC.plan_records(a.plan).items():
            for r in recs:
                for k, v in grab(R.fields_with_buff(db, r) or {}).items():
                    tot[k] = tot.get(k, 0) + v
        for rec in dev:
            for k, v in grab(R.fields_with_buff(db, rec) or {}).items():
                tot[k] = tot.get(k, 0) + v
    L.append('| 字段 | 合计 | 含义 |')
    L.append('|---|---|---|')
    NICE = {
        'characterManaRegen': '每秒回蓝（绝对）',
        'characterManaRegenModifier': '回蓝 %',
        'characterMana': '能量上限 +',
        'characterManaModifier': '能量上限 %',
        'skillManaCostReduction': '技能耗蓝减免 %',
        'characterEnergyAbsorptionPercent': '击杀能量吸收 %',
    }
    for k in MANA_FIELDS:
        if k in tot:
            L.append('| `%s` | %+.1f | %s |' % (k, tot[k], NICE[k]))
    L.append('')
    L.append('> **判据**：把这几个数抄到游戏面板上对一下（尤其「能量回复/秒」）。')
    L.append('> 净收支 = 回蓝 − **%.1f**；为负时，能量池 ÷ 缺口 = 你能连续输出的秒数。' % tot_mps)
    L.append('')

    # ---- ③ 等级 → DPS / 蓝耗 曲线
    L.append('## 三、技能等级 → DPS vs 蓝耗（找省蓝最划算的等级）')
    L.append('')
    for rec in sorted({r for _m, r, _l, _c, _f in rows}, reverse=True):
        f = R.fields_with_buff(db, rec) or {}
        arr = f.get('skillManaCost') or []
        if not isinstance(arr, list) or len(arr) < 3:
            continue
        top = levels.get(rec) or len(arr)
        lo = max(1, top - 12)
        L.append('### %s（当前 %d 级）' % (rec.split('/')[-1], top))
        L.append('')
        L.append('| 等级 | 单次蓝 | **总蓝每秒** | DPS | **DPS/蓝** | DPS 损失 |')
        L.append('|---|---|---|---|---|---|')
        _lvtop = dict(levels)
        _lvtop[rec] = int(top)
        ref = R.final_report(a.archetype, _lvtop, folded=folded, db=db,
                             devotion_levels=dev or None, weapon_st=_wst)['dps']
        for lv in range(lo, int(top) + 1):
            lvv = dict(levels)
            lvv[rec] = lv
            r2 = R.final_report(a.archetype, lvv, folded=folded, db=db,
                                devotion_levels=dev or None, weapon_st=_wst)
            h = (r2.get('skills') or {}).get(rec) or {}
            freq = h.get('freq') or 0
            c = mana_cost(db, rec, lv)
            mps = c * freq
            others = sum(x[0] for x in rows if x[1] != rec)
            t = r2['dps']
            L.append('| %d | %.0f | %.1f | %.0f | %.1f | %s |'
                     % (lv, c, mps + others, t, t / max(mps + others, 0.1),
                        '—' if lv == int(top) else '%+.1f%%' % ((t / ref - 1) * 100)))
        L.append('')
        L.append('> 「DPS/蓝」随等级**单调下降** ⇒ 高级技能是「用蓝换 DPS」；'
                 '要续航就往低等级收，要上限就顶满。**拐点看 DPS 损失与省蓝的比例**。')
        L.append('')

    # ---- ④ 可替换候选
    L.append('## 四、可替换的回蓝 / 降耗件')
    L.append('')
    for label, pref in (('镶嵌（materia）', 'records/items/materia/'),
                        ('附魔（enchants）', 'records/items/enchants/')):
        cand = []
        for r in db.records_like(pref):
            g = grab(R.fields_with_buff(db, r) or {})
            if g:
                score = (g.get('skillManaCostReduction', 0) * 3
                         + g.get('characterManaRegen', 0) * 4
                         + g.get('characterManaRegenModifier', 0) * 0.5
                         + g.get('characterEnergyAbsorptionPercent', 0) * 0.3)
                cand.append((score, r, g))
        cand.sort(reverse=True)
        L.append('### %s（按回蓝/降耗价值排序）' % label)
        L.append('')
        L.append('| 记录 | 效果 |')
        L.append('|---|---|')
        for _s, r, g in cand[:a.top]:
            L.append('| `%s` | %s |'
                     % (r.split('/')[-1],
                        '、'.join('%s %+.1f' % (NICE[k], v) for k, v in g.items())))
        L.append('')

    txt = '\n'.join(L)
    print(txt)

    # ---- ⑤ 续航判据（能量池 + 净收支）--------------------------------------
    import gd_alloc as _A
    lt = _A._level_table()
    inc = float(lt.get('energy_increment') or 16)
    lvl = 63
    try:
        from . import _legacyenv as _E
        from .save import core as _S
        _sd, _ = _E.find_save_dir()
        _p = os.path.join(_sd, 'main', '_' + (a.char or 'Sam').lstrip('_'), 'player.gdc')
        if os.path.exists(_p):
            _b2 = _S.parse(_p)['block_map'][2]
            lvl = int(_b2.get('level_in_bio') or lvl)
    except Exception:
        pass
    pool = (lvl * inc + tot.get('characterMana', 0.0)) * (1 + tot.get('characterManaModifier', 0.0) / 100.0)

    M = []
    M.append('')
    M.append('## 五、续航判据（这是最终答案要看的地方）')
    M.append('')
    M.append('**能量池估算** = 等级 %d × 每级 %d + 装备 `characterMana` %.0f，再 ×(1+上限%% %.0f) = **≈ %.0f**'
             % (lvl, inc, tot.get('characterMana', 0), tot.get('characterManaModifier', 0), pool))
    M.append('')
    M.append('**每秒回蓝** = (基础回蓝 + 装备/星座 flat %.1f) × (1 + 回蓝%% %.0f)'
             % (tot.get('characterManaRegen', 0), tot.get('characterManaRegenModifier', 0)))
    M.append('')
    M.append('> ⚠ **基础回蓝（等级 + 精神带来的那部分）模型里没有** —— 它是唯一缺失项。')
    M.append('> 但它**不影响结论方向**：下表把基础回蓝从 10 扫到 60，看净收支就明白了。')
    M.append('')
    M.append('| 假设的基础回蓝 | 实际每秒回蓝 | 消耗 | **净收支** | 满池连续输出 |')
    M.append('|---|---|---|---|---|')
    f, m_ = tot.get('characterManaRegen', 0.0), tot.get('characterManaRegenModifier', 0.0)
    for base in (10, 20, 30, 40, 60):
        regen = (base + f) * (1 + m_ / 100.0)
        net = regen - tot_mps
        dur = (pool / -net) if net < 0 else float('inf')
        M.append('| %d | %.1f | %.1f | **%+.1f** | %s |'
                 % (base, regen, tot_mps, net,
                    ('**%.0f 秒**' % dur) if dur != float('inf') else '可持续 ✓'))
    M.append('')
    M.append('**收支平衡需要面板回蓝 = %.1f /秒**（即消耗本身）。63 级角色典型回蓝 30~80/秒，'
             '堆到 100+ 已属极限 ⇒ **自然回蓝几乎必然跟不上**。' % tot_mps)
    M.append('')
    M.append('所以真正的续航手段只有四个：')
    M.append('')
    M.append('1. **降技能等级**（§三 的表，线性交换：省蓝%% ≈ 掉 DPS%%）')
    M.append('2. **`skillManaCostReduction`**（−10%% 镶嵌 ≈ 省 %.1f 蓝/秒，唯一近似纯赚）' % (tot_mps * 0.1))
    M.append('3. **击杀能量吸收**（本 build `characterEnergyAbsorptionPercent` = %.0f%%）——'
             '**清怪时很有效，打 Boss 完全无用**' % tot.get('characterEnergyAbsorptionPercent', 0))
    M.append('4. **蓝药**（游戏内，模型不算）')
    M.append('')
    M.append('> 实战判断法则：**清怪（短战斗 + 吸收）能撑住；长时间单体 Boss 必然打空**。')
    txt2 = '\n'.join(M)
    print(txt2)

    if a.out:
        open(a.out, 'w', encoding='utf-8').write(txt + txt2)
        print('\n→ %s' % a.out)


if __name__ == '__main__':
    main()
