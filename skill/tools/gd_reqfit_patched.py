# -*- coding: utf-8 -*-
r"""装备需求拟合 —— **自动把属性点调到「整套装备都能穿上」**。

这是「属性需求闸门」的**唯一求解实现**。三条写档链路都调它，不要各写一份：

```
gd/save/patch.py   属性/点数补丁写盘前   → fit() / solve()
gd/build.py        换完装备写盘前（按**新装备**）→ solve(override=…)
tools/fix_wearability.py  独立的「只调属性」入口
```

口径（与 SKILL §3.8 一致）
--------------------------
```
面板 = (50 + 8×加点 + 精通逐级 + 装备平值) × (1 + 装备%值 / 100)
需求 = 官方 itemCostFormulae 求值（gd.req，含武器）
```
* **精通逐级**必须走 `data/mastery_attr.json`（半值取整曲线），
  不能用「每级常数」线性外推 —— 那会把 Sam 算成 550/967/287（真值 533/967/270）。
* **面板 ≠ 存档值**：存档 block2 存的是 `50 + 8×已投点数`。

求解规则
--------
1. **严格等级预算**：可用点数 = 已花 + 未分配。**不凭空加总点数。**
2. 先让 体格 / 狡诈 / 精神 **同时**过阈值（阈值取全套最大值）。
3. 余额全投**输出属性**：优先 `prefer`，否则取形态 `attribute_bias` 里权重最大的，
   再退回「当前投得最多的那个」。
4. `buffer`：体格/精神各多留几点余量；`att_safety`：饰品提示框行数的安全行数。

用起来
------
```python
from gd import reqfit
r = reqfit.solve('Sam')                  # 当前装备
r = reqfit.solve('Sam', override=ov)     # 换成 ov 这套装备之后（ov 见 dps.load_char）
r.feasible        # False 表示预算不够（看 r.short）
r.targets         # {'physique': 242.0, 'cunning': 418.0, 'spirit': 130.0}  存档值
r.changed         # 存档值是否需要改
print(reqfit.describe(r))
```
"""
from __future__ import annotations

import math
import os
from typing import Optional

from . import paths
from . import req as RQ
from . import rotation as R

__all__ = ['KEYS', 'ZH', 'solve', 'fit', 'feasible', 'describe', 'gear_from_save',
           'gear_from_items', 'gear_from_override', 'mastery_levels', 'mastery_attrs',
           'panel_coeffs', 'Fit']

KEYS = ('physique', 'cunning', 'spirit')
ZH = {'physique': '体格', 'cunning': '狡诈', 'spirit': '精神'}
BASE = 50.0      # 1 级基础三围
PER = 8.0        # 每点属性点加的面板值

# 装备槽（与 gd.save.core.SLOTS 一致：12 个装备槽，**不含武器**）
EQUIP_SLOTS = ('头部', '项链', '胸甲', '腿甲', '靴子', '手套',
               '戒指1', '戒指2', '腰带', '肩甲', '勋章', '圣物')


# ---------------------------------------------------------------- 装备采集
def gear_from_save(char: str) -> list:
    """当前存档的装备 → `[{'slot','record','extras','group'}]`。

    含 12 个装备槽 + **全部武器套**（alt1 / alt2，以及选中的那套）。
    武器也一起纳入：换武器同样会「穿不上」。
    """
    from .save import core as S
    sd, _ = paths.save_dir()
    key = char if char.startswith('_') else '_' + char
    p = os.path.join(str(sd), 'main', key, 'player.gdc')
    if not os.path.isfile(p):
        raise SystemExit('✗ 找不到角色档：%s' % p)
    b3 = S.parse(p)['block_map'].get(3) or {}
    rows = []
    for i, it in enumerate(b3.get('equipment') or []):
        rec = it.get('basename') or ''
        if not rec:
            continue
        rows.append({'slot': EQUIP_SLOTS[i] if i < len(EQUIP_SLOTS) else '槽%d' % i,
                     'record': rec, 'extras': _extras(it),
                     'component': it.get('relic_name') or '',
                     'augment': it.get('augment_name') or '', 'group': 'equip'})
    for setname in ('alt1', 'alt2'):
        for j, it in enumerate(b3.get(setname) or []):
            rec = it.get('basename') or ''
            if not rec:
                continue
            rows.append({'slot': '主手' if j == 0 else '副手',
                         'record': rec, 'extras': _extras(it),
                         'component': it.get('relic_name') or '',
                         'augment': it.get('augment_name') or '',
                         'group': setname})
    return rows


def _extras(it: dict) -> list:
    """该件的**前缀/后缀**记录名。

    只有前缀/后缀会并进 GT 的提示框行数（`S = wa(S, va(affix))`），
    镶嵌 / 附魔**不并** —— 所以这里只收 prefix / suffix。
    """
    return [x for x in (it.get('prefix'), it.get('suffix')) if x]


def _row_of(slot, obj):
    """部件字典 **或** 记录名清单 → (主体记录, 前缀/后缀清单)。

    两种入参都要吃：写档链路手里是部件字典（`basename`/`prefix`/`suffix` 显式字段），
    而 `gear_override` 风格是「主体+镶嵌+附魔+前缀+后缀」混在一起的记录名清单。
    """
    if isinstance(obj, dict):
        rec = obj.get('basename') or ''
        return rec, _extras(obj)
    rec, extras = '', []
    for r in (obj or ()):
        low = str(r).replace('\\', '/').lower()
        if '/lootaffixes/' in low:
            extras.append(r)
        elif '/components/' in low or '/augments/' in low or '/enchant' in low:
            continue
        elif not rec:
            rec = r
    return rec, extras


def gear_from_items(eq_items, weapon_sets=None) -> list:
    """**写档链路首选**：直接吃「将要写进存档的部件」。

    `eq_items`：12 个装备槽的部件 dict（`gd.build.mk_item` 的产出，
                 字段 `basename` / `prefix` / `suffix`）；也接受记录名清单。
    `weapon_sets`：`{'主手': it, '副手': it}` 或 `[('主手', it), ('副手', it), …]`。

    比从 `override` 反推更准 —— 前缀/后缀在这里是**显式字段**，不用靠路径猜。
    """
    rows = []
    for i, it in enumerate(eq_items or ()):
        rec, extras = _row_of(None, it)
        if not rec:
            continue
        rows.append({'slot': EQUIP_SLOTS[i] if i < len(EQUIP_SLOTS) else '槽%d' % i,
                     'record': rec, 'extras': extras, 'group': 'equip'})
    pairs = []
    if isinstance(weapon_sets, dict):
        pairs = list(weapon_sets.items())
    elif weapon_sets:
        pairs = list(weapon_sets)
    for slot, it in pairs:
        rec, extras = _row_of(slot, it)
        if not rec:
            continue
        rows.append({'slot': slot, 'record': rec, 'extras': extras,
                     'group': 'weapon'})
    return rows


def gear_from_override(override: dict) -> list:
    """`{槽位: [记录名…]}`（`dps.load_char(gear_override=…)` 的格式）→ 装备行。

    清单里混着 主体 / 前缀 / 后缀 / 镶嵌 / 附魔，按记录路径分类：
    只有 `…/lootaffixes/…` 算「前缀/后缀」（进提示框行数），其余镶嵌附魔忽略。
    主体取第一个非词缀/非镶嵌/非附魔的记录。
    """
    rows = []
    for slot, recs in (override or {}).items():
        rec, extras = _row_of(slot, recs)
        if rec:
            rows.append({'slot': slot, 'record': rec, 'extras': extras,
                         'group': 'override'})
    return rows


# ---------------------------------------------------------------- 面板系数
def mastery_levels(load: dict) -> dict:
    """{职业: 有效精通等级} = 存档加点 + 装备给的 +等级（`mastery_plus`）。"""
    mlev = {}
    for rec, lv in (load.get('skills') or {}).items():
        if '_classtraining_' in rec:
            cls = os.path.basename(rec).replace('_classtraining_', '').replace('.dbr', '')
            mlev[cls] = mlev.get(cls, 0) + int(lv)
    for cls, extra in (load.get('mastery_plus') or {}).items():
        mlev[cls] = mlev.get(cls, 0) + int(extra)
    return mlev


def mastery_attrs(load: dict) -> dict:
    """精通条给的三围**累计值**（官方逐级半值取整曲线）。"""
    mlev = mastery_levels(load)
    return {k: sum(R.mastery_attr_of(cls, lv, k) for cls, lv in mlev.items())
            for k in KEYS}


def panel_coeffs(load: dict):
    """`面板 = A + B × 点数` 的系数。

    A 取「0 点加点时」的面板：`(50 + 精通 + 装备平值) × (1 + 装备%值/100)`
    B = `8 × (1 + 装备%值/100)`
    """
    mast = mastery_attrs(load)
    gf = load.get('gear_flat') or {}
    gp = load.get('gear_pct') or {}
    A, B = {}, {}
    for k in KEYS:
        pct = 1.0 + float(gp.get(k) or 0.0) / 100.0
        A[k] = (BASE + mast[k] + float(gf.get(k) or 0.0)) * pct
        B[k] = PER * pct
    return A, B, mast


# ---------------------------------------------------------------- 求解
class Fit(object):
    """求解结果。字段都是纯数据，方便写档链路直接取用。"""

    __slots__ = ('char', 'rows', 'need', 'unevaluated', 'A', 'B', 'mast',
                 'points_before', 'points_after', 'budget', 'free',
                 'panel_before', 'panel_after', 'targets', 'feasible',
                 'short', 'out_key', 'att_safety', 'buffer', 'arch', 'fail',
                 'warnings')

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def changed(self) -> bool:
        """存档值是否需要改（点数是整数，比点数即可）。"""
        return self.points_before != self.points_after

    def as_dict(self) -> dict:
        d = {k: getattr(self, k) for k in self.__slots__}
        d['changed'] = self.changed
        return d


def _arch_of(load: dict) -> Optional[str]:
    try:
        from . import dps as D
        return D.guess_arch(load.get('skills') or {})
    except Exception:
        return None


def _bias_of(arch: Optional[str]) -> dict:
    if not arch:
        return {}
    try:
        import json
        p = paths.DATA_DIR / 'archetypes.json'
        spec = (json.loads(p.read_text(encoding='utf-8')) or {}).get(arch) or {}
        return spec.get('attribute_bias') or {}
    except Exception:
        return {}


def _pick_out_key(arch, spent, prefer):
    """余额投给谁：显式 prefer > 形态 bias 最大 > 当前投得最多。"""
    if prefer in KEYS:
        return prefer
    bias = _bias_of(arch)
    if bias:
        top = max(bias.items(), key=lambda kv: kv[1])[0]
        if top in KEYS:
            return top
    return max(KEYS, key=lambda k: spent.get(k, 0)) if spent else 'physique'


def _split_left(left, bias, out_key):
    """余额怎么分：`bias` 非空且给了分摊 → 按权重分配；否则**全给 out_key**。

    默认「全给 out_key」是为了可预测（`gd/opt.py` 的严格等级预算也是这个口径）；
    想兼顾生存（体格给生命/防御）就传 `spread=True`，按形态 `attribute_bias` 分摊。
    """
    if not bias:
        return {out_key: left}
    keys = [k for k in KEYS if float(bias.get(k) or 0.0) > 0]
    if not keys:
        return {out_key: left}
    tot = sum(float(bias[k]) for k in keys) or 1.0
    alloc = {k: int(left * float(bias[k]) / tot) for k in keys}
    alloc[max(keys, key=lambda k: float(bias[k]))] += left - sum(alloc.values())
    return alloc


def solve(char: str, override: Optional[dict] = None, buffer: int = 1,
          att_safety: int = 6, prefer: Optional[str] = None,
          load: Optional[dict] = None, save_dir=None, rows=None,
          spread: bool = False, log=None) -> Fit:
    """算出「让整套装备都能穿上」的属性点分配。

    `override`：换成这套装备**之后**再拟合（`{槽位: [记录名…]}`，
                与 `dps.load_char(gear_override=…)` 同格式）；
                不给就按当前存档里的装备算。
    `rows`    ：直接给装备行（优先级最高）。写档链路已经把「将要写进去的部件」
                拿在手里，用它比从 override 反推更准。
    `save_dir`：存档目录（默认按 `paths.save_dir()` 探测）。**写档工具必须传**，
                否则会出现「patch 在改副本、拟合在读真档」的不一致。
    `buffer`  ：体格/精神各多留几点余量（默认 1）。
    `att_safety`：饰品提示框行数的安全行数（默认 6，见 `gd.req`）。
    `prefer`  ：余额投给哪个属性（默认按形态 `attribute_bias` 里权重最大的）。
    `spread`  ：True = 余额按 `attribute_bias` **分摊**（兼顾体格给的生命/防御）；
                False（默认）= 余额**全给** `prefer`（可预测、最大化输出属性）。
    """
    from . import dps as D
    from . import paths as _P

    say = log or (lambda *a: None)
    with _P.save_dir_override(save_dir):
        load = load if load is not None else D.load_char(char, gear_override=override)
        if rows is None:
            rows = (gear_from_override(override) if override is not None
                    else gear_from_save(char))
        free = _free_points(char)

    need = RQ.need_of_rows(rows, att_safety=att_safety)
    A, B, mast = panel_coeffs(load)

    # ---- 预算：严格等级预算 = 已花 + 未分配
    bio = load.get('bio') or {}
    spent = {k: int(round((float(bio.get(k) or 0.0) - BASE) / PER)) for k in KEYS}
    budget = sum(spent.values()) + free
    panel_before = {k: A[k] + B[k] * spent[k] for k in KEYS}

    # ---- 最低点数：逐属性单独算，再各留余量
    pmin = {}
    for k in KEYS:
        if need[k] and B[k]:
            pmin[k] = max(0, int(math.ceil((need[k] - A[k]) / B[k])))
        else:
            pmin[k] = 0
    # ★★★ 2026-09-20 修 bug：这里原来写的是 `if pmin[k]:`
    #   ⇒ 只有「原本就不够、必须投点」的属性才留余量。
    #   而**常见情形恰恰是 `pmin[k] == 0`**（面板本来就过阈值）⇒ `buffer` 对该属性
    #   **完全失效**。实测代价（用户 2026-09-20 报「落档后好多装备穿不上」）：
    #   Sam new BD 体格 0 点刚好过阈值（面板 438 vs 需求 429，**余量只有 9**），
    #   `--fit-buffer` 调到 40 都不动体格（`pmin['spirit']=11>0` 能拿 buffer、
    #   `pmin['physique']=0` 拿不到）⇒ 落档后余量仍是 9 点，而模型口径与游戏存在
    #   ≤21 点的未知偏差 ⇒ 进游戏就穿不上。
    #   正确语义（本函数 docstring 与 `--fit-buffer` 的 help 都写「体格/精神**各**多留
    #   N 点」）：**只要该属性有需求就留 buffer，与「原本够不够」无关**。
    #   ⚠ 改完 `used` 会变大 ⇒ 预算真不够时会如实报 `feasible=False`（这是期望行为，
    #     以前是「悄悄不给余量、看着成功」）。
    for k in ('physique', 'spirit'):
        if need.get(k):
            pmin[k] = pmin.get(k, 0) + max(0, int(buffer))

    used = sum(pmin.values())
    short = {k: max(0, need[k] - int(A[k] + B[k] * pmin[k])) for k in KEYS
             if need[k]}
    feasible = used <= budget
    if not feasible:
        # 预算不够 → 把缺口按「还需要几点」报出来，别硬凑
        short = {}
        for k in KEYS:
            if need[k] and B[k] > 0:
                need_pts = max(0, int(math.ceil((need[k] - A[k]) / B[k])))
                have_pts = spent.get(k, 0)
                if need_pts > have_pts:
                    short[k] = need_pts - have_pts
        out = dict(pmin)
        out_key = _pick_out_key(_arch_of(load), spent, prefer)
    else:
        arch = _arch_of(load)
        out_key = _pick_out_key(arch, spent, prefer)
        out = dict(pmin)
        _left = budget - used
        _alloc = _split_left(_left, _bias_of(arch) if spread else {}, out_key)
        for k, add in _alloc.items():
            out[k] = out.get(k, 0) + add

    panel_after = {k: A[k] + B[k] * out[k] for k in KEYS}
    targets = {k: BASE + PER * out[k] for k in KEYS}

    warnings = []
    if need.get('unevaluated'):
        warnings.append('%d 件装备需求**未评估**（认不出类型）：%s'
                        % (len(need['unevaluated']),
                           ', '.join((x.get('record') or '').rsplit('/', 1)[-1]
                                     for x in need['unevaluated'][:4])))
    fail = [x for x in (need.get('items') or [])
            if any(x[k] and x[k] > panel_after[k] + 1e-6
                   for k in KEYS)]
    if fail:
        warnings.append('有 %d 件即使重排后仍不达标（装备本身要求过高，需要换件）'
                        % len(fail))

    fit = Fit(char=char, rows=rows, need=need, unevaluated=need.get('unevaluated') or [],
              A=A, B=B, mast=mast, points_before=spent, points_after=out,
              budget=budget, free=free, panel_before=panel_before,
              panel_after=panel_after, targets=targets, feasible=feasible,
              short=short, out_key=out_key, att_safety=att_safety, buffer=buffer,
              arch=_arch_of(load), fail=fail, warnings=warnings)
    say(fit)
    return fit


def _free_points(char: str) -> int:
    """未分配属性点（block2 的 `attribute_points`）。"""
    from .save import core as S
    sd, _ = paths.save_dir()
    key = char if char.startswith('_') else '_' + char
    p = os.path.join(str(sd), 'main', key, 'player.gdc')
    d = S.parse(p)
    return int((d['block_map'].get(2) or {}).get('attribute_points') or 0)


def fit(char: str, override: Optional[dict] = None, **kw) -> Fit:
    """`solve()` 的别名（写档链路读起来更顺）。"""
    return solve(char, override=override, **kw)


def feasible(char: str, override: Optional[dict] = None, **kw) -> bool:
    """单问一句「这套装备穿得上吗」。"""
    return solve(char, override=override, **kw).feasible


# ---------------------------------------------------------------- 打印
def describe(r: Fit, prefix: str = '') -> str:
    L = []
    if r.arch:
        L.append('%s形态 %s' % (prefix, r.arch))
    L.append('%s装备需求（att_safety=%d）：体格 %d ｜ 狡诈 %d ｜ 精神 %d'
             % (prefix, r.att_safety, r.need['physique'], r.need['cunning'],
                r.need['spirit']))
    L.append('%s属性点预算 %d（已花 %d + 未分配 %d）'
             % (prefix, r.budget, sum(r.points_before.values()), r.free))
    L.append('%s%-4s %-16s %-16s %-8s %s'
             % (prefix, '属性', '改前(点/面板)', '改后(点/面板)', '阈值', ''))
    for k in KEYS:
        p0, p1 = r.points_before[k], r.points_after[k]
        ok = (not r.need[k]) or r.panel_after[k] + 1e-6 >= r.need[k]
        L.append('%s%-4s %-16s %-16s %-8s %s' % (
            prefix, ZH[k], '%d / %.0f' % (p0, r.panel_before[k]),
            '%d / %.0f' % (p1, r.panel_after[k]), r.need[k] or '-',
            '✓' if ok else '✗'))
    if not r.feasible:
        L.append('%s✗ 预算不够：%s' % (prefix, ', '.join(
            '%s 还差 %d 点' % (ZH[k], v) for k, v in r.short.items()) or '未知'))
    for w in (r.warnings or ()):
        L.append('%s⚠ %s' % (prefix, w))
    return '\n'.join(L)
