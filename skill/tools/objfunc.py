# -*- coding: utf-8 -*-
"""objfunc.py —— 优化目标的**统一评分**（供 `tune_skills` / `tune_devotion` /
`eval_build_variants` 共用）。

为什么必须能换目标
------------------
`final_report()` 返回的 `rep['dps']` 是**全类型合计**。用它当目标，优化器一定会去堆
**合计最高**的伤害类型 —— 对「穿刺 + 流血」混合流就是两者一起堆。
一旦用户要求**单一主轴**（例如「主穿刺」），就必须把目标换成**该桶的 DPS**，
否则结果与形态声明的 `damage_weights`（装备搜索用的）**互相矛盾**：
装备按穿刺权重选、技能却按合计选，最后出来一个「装备穿刺、技能流血」的四不像。

口径
----
`rep['type_rows']` 是**按伤害桶**的拆分（含减抗后的 `dps_vs`）：

    {'type': 'pierce', 'zh': '穿刺', 'is_dot': False,
     'dps_panel': 70468.0, 'dps_vs': 72384.5, 'dps_final': 72384.5,
     'share_panel': 0.6884, 'share_final': ...}

★ 用 **`dps_vs`（含减抗）** 而不是 `dps_panel`：优化的目的就是打怪，
  而不同桶的敌方抗性差异巨大（穿刺 20% / 流血 9%），用面板值会系统性高估高抗桶。

模式
----
* `total`（默认）—— `rep['dps']`，与历史结论一致（零漂移：不传就是旧行为）。
* `pierce`（`--objective pierce`）—— **穿刺桶的含减抗 DPS**，用于「主穿刺」形态。
* `pierce+`—— 穿刺桶 + 20% 的其他桶（给主穿刺留一点附带伤害的余地，避免
  优化器为了 1 点穿刺% 把全部合计伤害砍掉）。

⚠ `GD_OBJ` 环境变量或调用方参数指定；**不指定 = `total`**，保证旧脚本行为逐位不变。
"""
from __future__ import annotations

import os

VALID = ('total', 'pierce', 'pierce+')

# ★ 2026-09-21：把「单一主轴」从**只支持穿刺**扩成**任意伤害桶**。
#   动机：用户问「这条线堆什么伤害属性最高」⇒ 必须能让搜索器以 `cold` / `bleeding` /
#   `physical` … 为目标各跑一次，否则永远只能拿到「合计最优」或「穿刺最优」两个点。
#   桶名与 `gd.rotation.TYPE_ZH` 同源（`bleeding` 不是 `bleed`、`physical` 不是 `phys`）。
try:                                            # 正常路径：与引擎同源，永不漂移
    from gd.rotation import TYPE_ZH as _TYPE_ZH
    BUCKETS = tuple(_TYPE_ZH)
except Exception:                               # 兜底：独立使用时也能工作
    _TYPE_ZH = {'physical': '物理', 'pierce': '穿刺', 'fire': '火焰', 'cold': '冰冷',
                'lightning': '闪电', 'poison': '毒素', 'acid': '酸液', 'vitality': '活力',
                'aether': '以太', 'chaos': '混乱', 'bleeding': '流血', 'burn': '燃烧',
                'frostburn': '霜燃', 'electrocute': '电击', 'decay': '活力衰减',
                'trauma': '内部创伤', 'poisondot': '毒素持续'}
    BUCKETS = tuple(_TYPE_ZH)

# `--objective` 的 argparse `choices`：`total` + 全部伤害桶 + 各桶的 `+` 变体。
VALID = VALID + tuple(b for b in BUCKETS if b not in VALID) \
              + tuple(b + '+' for b in BUCKETS if b + '+' not in VALID)


def zh(name: str) -> str:
    return _TYPE_ZH.get(name, name)


def _split(mode: str):
    """`'pierce+'` → `('pierce', True)`；`'cold'` → `('cold', False)`。"""
    return (mode[:-1], True) if mode.endswith('+') else (mode, False)


def mode_default() -> str:
    return os.environ.get('GD_OBJ', 'total')


def bucket(rep, name):
    """某个伤害桶的含减抗 DPS（取不到就返回 None）。"""
    for r in (rep.get('type_rows') or []):
        if r.get('type') == name:
            v = r.get('dps_vs')
            return float(v) if v is not None else None
    return None


def _total_vs(rep):
    """含减抗的**合计** DPS —— 兼容两种入参：
    `final_report()` 的返回（`rep['vs']['dps_vs']`）与 `plan_dps.dps_of()` 的返回（`rep['dps_vs']`）。"""
    vs = rep.get('vs')
    if isinstance(vs, dict) and vs.get('dps_vs') is not None:
        return float(vs['dps_vs'])
    if rep.get('dps_vs') is not None:
        return float(rep['dps_vs'])
    return float(rep.get('dps') or 0.0)


def score(rep, mode: str | None = None) -> float:
    """按 `mode` 给 `rep` 打分（越大越好）。

    `rep` 可以是 `gd.rotation.final_report()` 的返回，也可以是
    `tools/plan_dps.py::dps_of()` 的返回（后者带 `type_rows` / `dps` / `dps_vs`）。

    ★ 防御轴（2026-09-21 新增）：`GD_DEF_WEIGHT` 未设/为 0 ⇒ **逐字节原样返回**
      （默认零漂移）。设成 `0.2` 表示「防御分满 1.0 等效 +20% 伤害」，于是
      **装备搜索 / 星座重排 / 技能换位** 三处会一起把「抗打」纳入目标。
    """
    m = mode or mode_default()
    if m == 'total':
        # ★★★ 2026-09-22（陷阱 #84）：**必须用 `_total_vs`，不能直接读 `rep['dps']`**。
        #   `rep['dps']` 这个键名在两种返回里**语义不同**：
        #     · `plan_dps.dps_of()` 的返回 → `dps` == `dps_vs`（含减抗）✔
        #     · `gd.rotation.final_report()` 的返回 → `dps` = **面板**（每击伤害），
        #       含减抗在 `rep['vs']['dps_vs']` ✘
        #   旧写法对后者**静默退成面板口径** ⇒ 一切只在实战层生效的东西全部归零：
        #   **减抗技能**（刺骨战吼拆掉实测 −24.36%，却被报成 0）、**OA/DA 增益**
        #   （集结战吼 −2.99%、不羁狂怒 −2.97%）、暴击、敌方抗性/护甲。
        #   后果是最危险的一类：**把最大收益项当废物**，优化器会主动建议拆掉它。
        #   `_total_vs` 兼容两种入参 ⇒ 对 `dps_of` 的返回**逐位不变**（零漂移，实测
        #   163,959 ↔ 163,959），对 `final_report` 的返回自动取实战层。
        #   ⚠ 非 `total` 的目标（`bucket()`）**本来就对**（读 `type_rows` 的 `dps_vs`）
        #     —— 所以这个 bug 只在**默认目标**上生效过，覆盖面最大。
        s = _total_vs(rep)
    else:
        base, plus = _split(m)
        if base not in BUCKETS:
            raise ValueError('未知目标 %r（可选 total / %s）' % (m, ' / '.join(BUCKETS)))
        v = bucket(rep, base)
        if v is None:                 # 该桶在当前报告里不存在 ⇒ 退回旧行为（合计）
            s = float(rep['dps'])
        elif plus:
            # 给主桶留一点附带伤害的余地：避免优化器为 1 点主桶% 把合计砍光
            s = v + 0.2 * max(0.0, _total_vs(rep) - v)
        else:
            s = v
    return _with_defense(rep, s)


def _with_defense(rep, s):
    """防御轴乘子。`GD_DEF_WEIGHT` 未设 ⇒ 原样返回（**默认零漂移**）。

    注意这是**乘子**而不是加权和：伤害为 0 的方案不该因为「肉」而胜出。
    """
    try:
        w = float(os.environ.get('GD_DEF_WEIGHT') or 0.0)
    except Exception:                                          # noqa: BLE001
        w = 0.0
    if not w:
        return s
    d = rep.get('def_score')
    if isinstance(d, dict):
        d = d.get('score')
    try:
        return s * (1.0 + w * float(d or 0.0))
    except Exception:                                          # noqa: BLE001
        return s


def label(mode: str | None = None) -> str:
    m = mode or mode_default()
    _L = {'total': '合计 DPS', 'pierce': '穿刺桶 DPS',
          'pierce+': '穿刺桶 +20% 其他'}
    if m in _L:
        return _L[m]
    base, plus = _split(m)
    if base not in BUCKETS:
        return m
    return '%s桶 DPS%s' % (zh(base), ' +20% 其他' if plus else '')
