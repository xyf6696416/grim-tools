"""物品技能「授予者」反查 —— 形态技能来源合法性（A 方案 · 2026-09-20）。

为什么要它
----------
`data/archetypes.json` 里 **`fangs`（完美姿态）** 的 `root_skills` / `core_skills`
全部指向 `records/skills/itemskillsgdx3/relics/*`。物品技能在游戏里
**必须先装备对应物品才有**，并且受那个物品的**等级需求 `k`** 约束。
`gd/rotation.py::analyze()` 却把它们当角色自带技能**无条件计入** ⇒
算出 lv71 根本搭不出来的 DPS（实测：`fangs` 以 104,966 排第一，
而它依赖的遗物 `it15928` 是 **lv90** 的 GDX3 传奇遗物）。

这条判据此前只写在 SKILL §7 陷阱里（「已发现、未修」）；本模块把它落地成代码。

判据链（全部走离线库，不猜）
----------------------------
    item(it*).itemSkillName = "skXXXX"           ← `gd/gear.load_items()`
      → data/item_skills.json["skXXXX"]
          · skillDisplayName tag                 → 技能组**本体**记录
          · grantedSkills[].skillDisplayName tag → 被授予的子技能记录
      → data/skills.json 的 `tag` 索引           → 记录路径

用法
----
    from gd import skillprov as SP
    SP.is_item_skill(rec)                  # 记录是不是物品技能
    SP.providers(rec)                      # [(物品 gid, 等级需求 k), …]
    SP.min_level(rec)                      # 能拿到它的最低等级（None = 未知）
    SP.sk_of(rec)                          # 授予它的技能组 id（skXXXX）
    kept, dropped = SP.filter_legal(recs, equipped_sk={'sk4527'})
    SP.explain(rec, level=71)              # 一行中文判语（报告/日志用）

★ 设计口径（与「宁可不认，也不认错」一致）
  · **非物品技能 → 永远合法**（专精树 / 星座，一律放行）。
  · 物品技能：
      ① 给了 `equipped_sk`（角色当前装备授予的技能组集合）⇒ **严格判**：
         必须由已装备物品授予，否则剔除（这就是 A 方案）。
      ② 没给 `equipped_sk` 但给了 `level` ⇒ **退化为等级闸门**：
         授予者最低等级 ≤ 目标等级才放行。
      ③ 两者都没给 ⇒ 不判（`keep`），但在 `explain()` 里标注「未校验」。
  · 反查不出授予者的记录（全库实测约 85% 的 `itemskills*` 记录属于
    「词缀 / 套装 / 怪物技能」等非物品授予路径）⇒ **放行**，标注 `unknown`。
    宁可不认，也不认错。
"""
from __future__ import annotations

import collections
import os

ITEM_SKILL_PREFIX = 'records/skills/itemskills'

_SK2REC: dict | None = None
_REC2SK: dict | None = None
_SK2ITEMS: dict | None = None


def _norm(rec) -> str:
    return str(rec or '').replace('\\', '/').lower()


def is_item_skill(rec) -> bool:
    """记录路径是不是「物品技能」（`records/skills/itemskills*`）。

    `itemskillsgdx1/2/3` 都是（GDX3 不是玩家自装 mod —— 离线库
    `modNameMap` 写明 `gdx3: "Fangs of Asterkarn"`，官方第三个资料片）。
    """
    return _norm(rec).startswith(ITEM_SKILL_PREFIX)


def _build():
    """一次性建三张表（进程内 memo）。

    · `_SK2REC`  skXXXX → 它授予的记录（本体 + `grantedSkills` 子技能）
    · `_REC2SK`  记录 → 授予它的 skXXXX 集合
    · `_SK2ITEMS` skXXXX → [(物品 gid, 等级需求 k)]（只含 **`itemSkillName`**
      指向它的物品；词缀/套装的授予路径不在这里）
    """
    global _SK2REC, _REC2SK, _SK2ITEMS
    if _SK2REC is not None:
        return
    from . import paths as P
    from . import gear

    skills = P.load_json('skills.json') or {}
    isk = P.load_json('item_skills.json') or {}
    items = gear.load_items() or {}

    # ① tag → 记录（只认 records/skills/ 下的；重名 tag 全部保留）
    tag2rec = collections.defaultdict(list)
    for rec, d in skills.items():
        if not _norm(rec).startswith('records/skills/'):
            continue
        t = (d or {}).get('tag')
        if t:
            tag2rec[t].append(rec)

    # ② sk → 记录集合
    sk2rec = {}
    rec2sk = collections.defaultdict(set)
    for sk, g in isk.items():
        if not isinstance(g, dict):
            continue
        tags = [g.get('skillDisplayName'), g.get('skillBaseDescription')]
        for ch in (g.get('grantedSkills') or []):
            if isinstance(ch, dict):
                tags.append(ch.get('skillDisplayName'))
                tags.append(ch.get('skillBaseDescription'))
        got = []
        for t in tags:
            for r in (tag2rec.get(t) or []):
                if r not in got:
                    got.append(r)
        sk2rec[sk] = tuple(got)
        for r in got:
            rec2sk[r].add(sk)

    # ③ sk → 授予它的物品
    sk2items = collections.defaultdict(list)
    for gid, o in items.items():
        if not gid.startswith('it') or not isinstance(o, dict):
            continue
        sk = o.get('itemSkillName')
        if not sk:
            continue
        sk2items[sk].append((gid, o.get('k')))

    _SK2REC = sk2rec
    _REC2SK = {r: tuple(sorted(s)) for r, s in rec2sk.items()}
    _SK2ITEMS = {k: tuple(v) for k, v in sk2items.items()}


def sk_to_records(sk_id):
    """技能组 `skXXXX` 授予的记录（本体 + `grantedSkills` 子技能）。"""
    _build()
    return _SK2REC.get(sk_id) or ()


def sk_of(rec):
    """授予该记录的技能组 id 元组（`('sk4527',)`）；非物品技能 / 查不到 = `()`。"""
    _build()
    return _REC2SK.get(rec) or ()


def sk_items(sk_id):
    """技能组 → 授予它的物品 `[(gid, k), …]`（`k` = 该物品的等级需求）。"""
    _build()
    return _SK2ITEMS.get(sk_id) or ()


def providers(rec):
    """记录 → `[(物品 gid, 等级需求 k), …]`（按等级排序；查不到 = `()`）。

    ★ 只看 **`itemSkillName`** 这条路径：词缀 / 套装 / 怪物技能授予的
      物品技能在这里查不到，调用方按「未知 ⇒ 放行」处理（见模块 docstring）。
    """
    out = []
    for sk in sk_of(rec):
        out.extend(sk_items(sk))
    return tuple(sorted(set(out), key=lambda x: (x[1] is None, x[1] or 0, x[0])))


def min_level(rec):
    """能拿到该记录的最低等级需求（`None` = 未知 / 无授予者）。"""
    ks = [k for _, k in providers(rec) if isinstance(k, (int, float))]
    return int(min(ks)) if ks else None


def verdict(rec, level=None, equipped_sk=None):
    """单条记录的合法性判语。

    返回 `{'legal': bool, 'reason': str, 'kind': …}`，`kind` 取值：
      `class`   非物品技能（专精树 / 星座）—— 恒合法
      `unknown` 物品技能，但反查不到物品授予者 —— 恒合法（宁可不认，不认错）
      `equipped` / `level` / `unchecked` —— 物品技能，按装备 / 等级判定
    """
    if not is_item_skill(rec):
        return {'legal': True, 'reason': '专精树 / 星座技能', 'kind': 'class',
                'providers': (), 'min_level': None}
    prov = providers(rec)
    if not prov:
        return {'legal': True, 'reason': '物品技能，但反查不到物品授予者（未校验）',
                'kind': 'unknown', 'providers': (), 'min_level': None}
    ks = [k for _, k in prov if isinstance(k, (int, float))]
    mn = int(min(ks)) if ks else None
    if equipped_sk is not None:
        sks = [s for s in sk_of(rec) if s in (equipped_sk or ())]
        ok = bool(sks)
        why = ('由已装备物品授予（%s）' % '、'.join(sks)) if ok else (
            '需要的物品未装备（需 %s%s）'
            % ('、'.join(g for g, _ in prov[:3]),
               ('，其等级需求 %d' % mn) if mn else ''))
        return {'legal': ok, 'reason': why, 'kind': 'equipped',
                'providers': prov, 'min_level': mn}
    if level is not None and mn is not None:
        ok = mn <= int(level)
        return {'legal': ok,
                'reason': ('等级闸门：需求 %d ≤ 角色 %d' % (mn, int(level))) if ok
                          else ('等级闸门：需求 **%d** > 角色 %d ⇒ 拿不到'
                                % (mn, int(level))),
                'kind': 'level', 'providers': prov, 'min_level': mn}
    return {'legal': True, 'reason': '未提供装备/等级信息 ⇒ 未校验',
            'kind': 'unchecked', 'providers': prov, 'min_level': mn}


def norm_equipped(values):
    """把「装备授予的技能组 id / 记录名」归一成 **sk id 集合**。

    调用方拿到的 `itemSkillName` 在离线库里是 `skXXXX`，但在真实 `.dbr` /
    `gt_data/local_items.json`（游戏内提示框真值）里可能是**记录路径**
    ⇒ 两种都要吃得下，否则「已装备」判定会静默失效（A 方案白做）。
    """
    _build()
    out = set()
    for v in (values or ()):
        if not isinstance(v, str) or not v:
            continue
        if v.startswith('sk') and v[2:].isdigit():
            out.add(v)
            continue
        for s in (_REC2SK.get(v) or ()):
            out.add(s)
    return out


def _sk_in(rec, equipped_sk) -> bool:
    return any(s in (equipped_sk or ()) for s in sk_of(rec))


def filter_legal(records, level=None, equipped_sk=None):
    """按来源合法性过滤技能清单。

    返回 `(kept, dropped)`；`dropped` 是 `[(rec, verdict_dict), …]`，
    调用方应把它透出到报告 / 日志 —— **静默剔除是这个 bug 的第一特征**。
    """
    kept, dropped = [], []
    for rec in (records or []):
        v = verdict(rec, level=level, equipped_sk=equipped_sk)
        (kept if v['legal'] else dropped).append(rec if v['legal'] else (rec, v))
    return kept, dropped


def explain(rec, level=None, equipped_sk=None) -> str:
    """一行中文判语（日志 / 报告脚注用）。"""
    b = os.path.basename(str(rec))
    v = verdict(rec, level=level, equipped_sk=equipped_sk)
    mark = '✓' if v['legal'] else '✗'
    extra = ''
    if v['providers']:
        extra = ' ｜ 授予者 %s' % '、'.join(
            '%s(k=%s)' % (g, k if k is not None else '?') for g, k in v['providers'][:3])
    return '%s %-28s %s%s' % (mark, b, v['reason'], extra)


def arch_report(archetype, level=None, equipped_sk=None):
    """一个形态的合法性体检：返回 `{'ok', 'rows', 'blockers'}`。

    `blockers` = 会让**整个形态不可构建**的记录（空 = 该形态在该等级合法）。
    """
    from . import paths as P
    arch = (P.load_json('archetypes.json') or {}).get(archetype) or {}
    recs = list(arch.get('root_skills') or []) + list(arch.get('core_skills') or [])
    seen, rows, blockers = set(), [], []
    for rec in recs:
        if rec in seen:
            continue
        seen.add(rec)
        v = verdict(rec, level=level, equipped_sk=equipped_sk)
        rows.append({'rec': rec, 'zh': os.path.basename(rec).replace('.dbr', ''),
                     **{k: v[k] for k in ('legal', 'reason', 'kind',
                                          'min_level', 'providers')}})
        if not v['legal']:
            blockers.append(rows[-1])
    return {'arch': archetype, 'label': arch.get('label'), 'level': level,
            'ok': not blockers, 'rows': rows, 'blockers': blockers,
            'is_item_skill_arch': any(is_item_skill(r) for r in recs)}
