# -*- coding: utf-8 -*-
r"""装备属性需求建模（gd_req）—— 2026-09-17 新增

背景（为什么需要它）
--------------------
「属性点分配后能不能穿上装备」是配装算法的硬约束，但需求值**不在任何可读的数据源里**：
  · `.dbr` 里 `strengthRequirement` / `dexterityRequirement` / `intelligenceRequirement`
    实测**普遍是 0**（游戏在运行时按公式算）；
  · GrimTools 的 `itemdb.js` 只有 5/7133 件带该字段（都是 NPC 道具）；
  · GT 物品页/分类页都是 JS 渲染，curl 抓不到。

本模块的做法
------------
需求对 `itemLevel` 是**严格线性**的：`需求 = a × ilvl + b`，系数取决于「部位 × 类别」。
下面的 `COEF` 表分两档：

  · **measured（实测标定）**：头部 3 类、腿部 2 类，来自公开数据表拟合，误差 <2%
  · **estimated（外推估算）**：其余部位按「部位比例」外推，精度约 ±10%

外推的置信度低，所以 `req()` 会返回 `confidence` 字段；调用方应据此**留安全余量**
（建议 estimated 件按 ×1.10 计入）。

用法
----
```python
from . import req as R
r = R.req('it14681')          # {'physique': 566, 'cunning': 0, 'spirit': 0, 'confidence': 'measured'}
R.panel_needed(['it14681','it15451'])   # 一套装备需要的面板属性
```
"""
import os
import sys


# ------------------------------------------------------------------ 系数表
# 需求 = a * itemLevel + b
#
# 标定依据（2026-09-17 校准）：
#  · **头部 / 腿部 = measured**：来自公开数据表的完整 ilvl→需求序列，直接线性拟合
#      - 普通盔：ilvl1→15 … ilvl94→538   → 5.62/9.4（ilvl50 验算 290.4 vs 实际 290 ✓）
#      - 重甲盔：ilvl1→33 … ilvl94→915   → 9.48/23.5（ilvl84 验算 820.7 vs 实际 820 ✓）
#      - 法系盔：ilvl1→19 … ilvl84→320   → 3.63/15.4（ilvl50 验算 196.9 vs 实际 197 ✓）
#      - 轻甲腿：ilvl1→22 … ilvl94→662   → 6.88/15.1（ilvl58 验算 414.1 vs 实际 415 ✓）
#      - 重甲腿：ilvl1→43 … ilvl94→1035  → 10.67/32.3（ilvl84 验算 928.6 vs 实际 927 ✓）
#  · **其余部位 = estimated**：按「部位比例」外推。比例取自社区汇总的 75 级上限
#      （重甲 970 体格 / 轻甲 618 体格）与腿部模型比对：
#      胸甲 = 腿 × 1.166、肩甲 = 头 × 1.20、手/脚 = 头 × 0.703、腰带 = 头 × 0.795
#    estimated 件的调用方**应当留安全余量**（`req()` 已返回 confidence，默认 ×1.10）
COEF = {
    # 部位       类别         a        b      来源
    '头部': {'caster': (3.63, 15.4, 'measured'),
             'light': (5.62, 9.4, 'measured'),
             'heavy': (9.48, 23.5, 'measured')},
    '腿甲': {'caster': (4.23, 17.9, 'estimated'),
             'light': (6.88, 15.1, 'measured'),
             'heavy': (10.67, 32.3, 'measured')},
    '胸甲': {'caster': (4.23, 17.9, 'estimated'),
             'light': (8.01, 17.6, 'estimated'),
             'heavy': (12.44, 37.7, 'estimated')},
    '肩甲': {'caster': (4.36, 18.5, 'estimated'),
             'light': (6.74, 11.3, 'estimated'),
             'heavy': (11.38, 28.2, 'estimated')},
    '手套': {'caster': (2.55, 12.0, 'estimated'),
             'light': (3.94, 9.5, 'estimated'),
             'heavy': (6.64, 17.0, 'estimated')},
    '靴子': {'caster': (2.56, 12.5, 'estimated'),
             'light': (3.96, 10.0, 'estimated'),
             'heavy': (6.68, 18.0, 'estimated')},
    '腰带': {'caster': (2.90, 10.0, 'estimated'),
             'light': (4.47, 9.0, 'estimated'),
             'heavy': (7.53, 15.0, 'estimated')},
}

# 模板 → 部位
TMPL_SLOT = {
    'ArmorProtective_Head': '头部',
    'ArmorProtective_Chest': '胸甲',
    'ArmorProtective_Legs': '腿甲',
    'ArmorProtective_Shoulders': '肩甲',
    'ArmorProtective_Hands': '手套',
    'ArmorProtective_Feet': '靴子',
    'ArmorProtective_Waist': '腰带',
}

# 饰品：需求走 Spirit（少量走 Cunning）；公式较弱，取保守线性式
ACCESSORY_SLOT = {'项链', '戒指1', '戒指2', '勋章'}
ACCESSORY_COEF = (1.90, 12.0, 'estimated')       # spirit ≈ 1.9×ilvl + 12

# 武器需求（属性分配时也吃紧）
WEAPON_RULES = {
    'WeaponMelee_Sword':      ('cunning', (3.30, 8.0, 'estimated')),   # 单手剑 → 狡诈
    'WeaponHunting_Ranged1h': ('cunning', (3.30, 8.0, 'estimated')),
    'WeaponHunting_Ranged2h': ('cunning', (4.20, 10.0, 'estimated')),
    'WeaponMelee_Dagger':     ('cunning', (2.60, 7.0, 'estimated')),
    'WeaponMelee_Axe':        ('physique', (3.40, 8.0, 'estimated')),
    'WeaponMelee_Blunt':      ('physique', (3.40, 8.0, 'estimated')),
    'WeaponMelee_Axe2h':      ('physique', (4.60, 12.0, 'estimated')),
    'WeaponMelee_Blunt2h':    ('physique', (4.60, 12.0, 'estimated')),
    'WeaponMelee_Sword2h':    ('physique', (4.30, 11.0, 'estimated')),
    'WeaponArmor_Shield':     ('physique', (4.80, 12.0, 'estimated')),
}

# GT itemdb 的 g 字段 → 内部类别名
CLASS_MAP = {'Caster': 'caster', 'Light': 'light', 'Heavy': 'heavy'}

# 减需求字段（在读到的 .dbr 里搜这些名字）
REQ_REDUCE_FIELDS = (
    'characterArmorStrengthReqReduction',
    'characterMeleeStrengthReqReduction',
    'characterMeleeDexterityReqReduction',
    'characterMeleeIntelligenceReqReduction',
    'characterHuntingDexterityReqReduction',
    'characterArmorDexterityReqReduction',
)

_DB = None
_IT = None


def _db():
    global _DB
    if _DB is None:
        from . import dbr as DB
        _DB = DB.open_all()
    return _DB


def _items():
    global _IT
    if _IT is None:
        try:
            from . import gear as G
            _IT = G.load_items()
        except Exception:
            _IT = {}
    return _IT


def _slot_of(gt_id, tmpl):
    """部位判定：优先模板，其次 GT 位图路径"""
    if tmpl in TMPL_SLOT:
        return TMPL_SLOT[tmpl]
    o = _items().get(gt_id) or {}
    bmp = (o.get('n') or '').lower()
    for d, s in (('gearhead', '头部'), ('geartorso', '胸甲'), ('gearlegs', '腿甲'),
                 ('gearshoulders', '肩甲'), ('gearhands', '手套'), ('gearfeet', '靴子'),
                 ('waist', '腰带'), ('necklaces', '项链'), ('rings', '戒指1'),
                 ('medals', '勋章')):
        if d in bmp:
            return s
    return None


def _class_of(gt_id):
    o = _items().get(gt_id) or {}
    return CLASS_MAP.get(o.get('g'), 'light')


def _reduce_of(record, base):
    """从 .dbr 读「减需求」字段，返回扣减后的值"""
    try:
        f = _db().fields(record)
    except Exception:
        return base, 0.0
    if not f:
        return base, 0.0
    best = 0.0
    for k in REQ_REDUCE_FIELDS:
        v = f.get(k)
        if v and v[0]:
            best = max(best, abs(float(v[0])))
    if best:
        return base * (1.0 - best / 100.0), best
    return base, 0.0


def req(gt_id, record=None):
    """返回该件装备的属性需求。

    {physique, cunning, spirit, confidence, ilvl, slot, cls, reduce}
    confidence: 'measured'（实测标定）/ 'estimated'（外推，建议 ×1.10 计）
    """
    o = _items().get(gt_id) or {}
    ilvl = o.get('k') or o.get('itemLevel') or 0
    out = {'physique': 0.0, 'cunning': 0.0, 'spirit': 0.0,
           'confidence': 'none', 'ilvl': ilvl, 'slot': None, 'cls': None, 'reduce': 0.0}
    if not ilvl:
        return out

    tmpl = None
    if record:
        try:
            tmpl = _db().template(record)
        except Exception:
            tmpl = None
    slot = _slot_of(gt_id, tmpl)
    out['slot'] = slot

    if slot in ACCESSORY_SLOT or slot is None and (o.get('n') or '').find('accessor') >= 0:
        a, b, conf = ACCESSORY_COEF
        out['spirit'] = a * ilvl + b
        out['confidence'] = conf
    elif slot and slot in COEF:
        cls = _class_of(gt_id)
        out['cls'] = cls
        a, b, conf = COEF[slot][cls]
        out['physique'] = a * ilvl + b
        out['confidence'] = conf
    elif tmpl and tmpl in WEAPON_RULES:
        attr, (a, b, conf) = WEAPON_RULES[tmpl]
        out[attr] = a * ilvl + b
        out['confidence'] = conf
    else:
        return out

    if record:
        for k in ('physique', 'cunning', 'spirit'):
            if out[k]:
                v, red = _reduce_of(record, out[k])
                out[k] = v
                out['reduce'] = max(out['reduce'], red)
    for k in ('physique', 'cunning', 'spirit'):
        out[k] = int(round(out[k]))
    return out


def panel_needed(gt_ids, records=None, safety=1.10):
    """一套装备需要的面板属性（estimated 件乘安全余量）。

    返回 {'physique':…, 'cunning':…, 'spirit':…, 'items': {gid: req}}
    """
    records = records or {}
    tot = {'physique': 0.0, 'cunning': 0.0, 'spirit': 0.0}
    detail = {}
    for gid in gt_ids:
        r = req(gid, records.get(gid))
        detail[gid] = r
        k = safety if r['confidence'] == 'estimated' else 1.0
        for a in ('physique', 'cunning', 'spirit'):
            tot[a] = max(tot[a], r[a] * k)          # 逐件取最大值：需求是「须同时满足」
    return {'physique': int(round(tot['physique'])),
            'cunning': int(round(tot['cunning'])),
            'spirit': int(round(tot['spirit'])),
            'items': detail}


if __name__ == '__main__':
    ids = sys.argv[1:] or ['it14681', 'it15451', 'it1421', 'it525']
    for gid in ids:
        r = req(gid)
        print('%-9s ilvl=%-3s %-5s %-6s 体格=%-5s 狡诈=%-4s 精神=%-5s [%s]'
              % (gid, r['ilvl'], r['slot'] or '-', r['cls'] or '-',
                 r['physique'] or '-', r['cunning'] or '-', r['spirit'] or '-', r['confidence']))
