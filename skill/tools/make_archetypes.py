# -*- coding: utf-8 -*-
r"""make_archetypes.py —— **机械派生**职业组合的形态条目，登记进增补文件。

为什么要有它
------------
`data/archetypes.json` 是**手工维护**的：过去每要一个新职业组合，都得有人手写
`core_skills` / `damage_weights` / `root_skills`。做「狂战士 + 全部 9 个职业谁伤害最高」
这种普查时，这根本不可行（9 组合 × 3 形态 = 27 条）。

本工具按**既有形态的同一口径**机械派生，规则全部来自离线库（`data/mastery_skills.json`），
不猜、不手写：

| 项 | 取值 |
|---|---|
| 形态 | `human`（不点变形）／`wolf`（`werewolf1`）／`raven`（`wereraven1`） |
| `mastery` / `masteries` | 固定职业当主（默认 class10 狂战士）⇒ `[[主,50],[副,32]]` |
| `root_skills` | `human` 空；变身形 = 该变身技能记录 |
| `core_skills` | **主职业全树**（去掉 pet / transmuter / mastery；`human` 再去掉 shapeshift）<br>**＋副职业的支援技**（武器池 `wpattack*`/`weaponpool*` ＋ `passive`/`passive_proc`/`buff_toggle`/`buff_radius_toggle`） |
| `damage_weights` | **故意不写** ⇒ 走默认权重，不预设伤害方向 |

★ 写进 `data/archetypes_gdskill.json`（增补文件）—— `data/archetypes.json` 会被
  `migrate_from_archive.py` 的 DATA 表无条件覆盖，直接改它下次迁移就丢。
★ 幂等：键已存在则**跳过**（不覆盖手工调过的条目），要覆盖用 `--force`。

用法::

    PY=".../envs/default/Scripts/python.exe"
    $PY tools/make_archetypes.py --all-with 10            # 10 × {1..9} 全部生成
    $PY tools/make_archetypes.py --pairs 10:1,10:2
    $PY tools/make_archetypes.py --all-with 10 --dry-run  # 只打印会加什么
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(HERE))

FORMS = (('human', '', 'human'),
         ('wolf', 'records/skills/playerclass10/werewolf1.dbr', 'werewolf'),
         ('raven', 'records/skills/playerclass10/wereraven1.dbr', 'wereraven'))
SKIP_KIND = {'pet', 'transmuter', 'mastery'}
SUPPORT_KIND = {'passive', 'passive_proc', 'buff_toggle', 'buff_radius_toggle'}


def _load_ms() -> dict:
    ms = json.loads((SKILL / 'data' / 'mastery_skills.json').read_text(encoding='utf-8'))
    by = {}
    for v in ms.values():
        by.setdefault(v['class'], []).append(v)
    return by


def _base(rec: str) -> str:
    return rec.split('/')[-1].replace('.dbr', '')


def tree_of(by, cls, drop_shapeshift=False):
    """主职业全树（去掉 pet / transmuter / mastery；可选去掉 shapeshift）"""
    out = []
    for v in by.get(cls, []):
        b = _base(v['record'])
        if b.startswith('petskill_') or v.get('kind') in SKIP_KIND:
            continue
        if drop_shapeshift and v.get('kind') == 'shapeshift':
            continue
        out.append(v['record'])
    return out


def support_of(by, cls):
    """副职业的**支援技**：武器池（`wpattack*` / `weaponpool*`）＋ 被动/常驻光环。

    ★ 为什么支援技只要这些：变身形态的门控（陷阱 #78）会把第二职业的**武器池**
      和技能栏主动技剔掉，只有被动/光环还生效 ⇒ 支援技按这个口径取最贴合真实。
      人形态下这两类都能参与，所以也是它。
    """
    from gd import alloc as _AL
    out = []
    for v in by.get(cls, []):
        b = _base(v['record'])
        if b.startswith('petskill_') or v.get('kind') in SKIP_KIND:
            continue
        # ★★ WPS 判据必须**复用 `gd.alloc.is_wps`**（`WeaponPool`/`WPAttack` 模板），
        #   而不是按名字前缀猜：原先只认 `wpattack*`/`weaponpool*`，漏掉了各职业
        #   自己的「默认攻击替换」技能（士兵 `cadence1`、爆破者 `flamestrike1`、
        #   萨满 `savagery1`、守誓者 `righteousfervor1`）⇒ 自检的 WPS 覆盖守卫当场报红，
        #   而且 `alloc` 的贪心「core 吃满预算再轮 rest」会让这些 WPS **永远点不到**。
        if _AL.is_wps(v):
            out.append(v['record'])
        elif v.get('kind') in SUPPORT_KIND:
            out.append(v['record'])
    return out


def class_name(by, cls):
    """职业名 —— 必须取 `kind == 'mastery'` 那条记录。

    ⚠ 别用 `by[cls][0]`：那只是技能表里的**第一条技能**（实测第一次就写成
      「冥煞之疫 + 战争号令」这种技能名当职业名）。
    """
    for v in by.get(cls, []):
        if v.get('kind') == 'mastery':
            return v.get('name') or cls
    return cls


def twin(cls: int, other: int, by: dict) -> dict:
    """一个职业组合 → 3 条形态条目（键 `b<cls>c<other>_<form>`）"""
    prim, sec = ('class%02d' % cls), ('class%02d' % other)
    pname = class_name(by, prim)
    sname = class_name(by, sec)
    sup = support_of(by, sec)
    E = {}
    for tag, root, form in FORMS:
        core = tree_of(by, prim, drop_shapeshift=(tag == 'human')) + sup
        E['b%dc%02d_%s' % (cls, other, tag)] = {
            'label': '%s + %s · %s形态' % (pname, sname,
                                           {'human': '人', 'wolf': '狼人',
                                            'raven': '鸦人'}[tag]),
            'desc': '★ **机械派生**（`tools/make_archetypes.py`）。主职业全树 + 副职业支援技'
                    '（武器池 + 被动/光环），`damage_weights` 不写 ⇒ 默认权重、不预设伤害方向。'
                    '⚠ 未人工校准：只用于同口径横向普查，别当最终结论。',
            'form': form, 'mastery': prim, 'masteries': [[cls, 50], [other, 32]],
            'root_skills': ([root] if root else []),
            'core_skills': core,
        }
    return E


def main() -> int:
    ap = argparse.ArgumentParser(description='机械派生职业组合的形态条目（写增补文件）')
    ap.add_argument('--all-with', type=int, default=0,
                    help='固定这个职业，与其余 9 个职业两两组合')
    ap.add_argument('--pairs', default='', help='显式组合，形如 `10:1,10:2`')
    ap.add_argument('--force', action='store_true', help='已存在也覆盖')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()

    by = _load_ms()
    all_cls = sorted(int(k[5:]) for k in by)
    if a.all_with:
        pairs = [(a.all_with, x) for x in all_cls if x != a.all_with]
    elif a.pairs:
        pairs = []
        for tok in a.pairs.split(','):
            tok = tok.strip()
            if tok:
                x, y = tok.split(':')
                pairs.append((int(x), int(y)))
    else:
        ap.error('需要 --all-with N 或 --pairs a:b,c:d')

    p = SKILL / 'data' / 'archetypes_gdskill.json'
    d = json.loads(p.read_text(encoding='utf-8'))
    A = d['archetypes']
    new = {}
    for x, y in pairs:
        for k, v in twin(x, y, by).items():
            if k in A and not a.force:
                continue
            new[k] = v
    print('组合 %d 个 ｜ 派生条目 %d 条（已存在且未 --force 的会跳过）' % (len(pairs), len(new)))
    for k in sorted(new):
        print('  %-16s %-28s core=%d' % (k, new[k]['label'], len(new[k]['core_skills'])))
    if a.dry_run:
        print('（--dry-run：未写盘）')
        return 0
    if not new:
        print('无新条目，未改文件。')
        return 0
    A.update(new)
    if not any('make_archetypes' in s for s in d['_why']):
        d['_why'].append(
            '★ 2026-09-22 新增 `tools/make_archetypes.py`：**机械派生**同职业组合的 3 个形态'
            '（`b<主>c<副>_human|wolf|raven`），口径 = 主职业全树 + 副职业支援技'
            '（武器池 + 被动/光环），`damage_weights` 不写。用途：'
            '「某职业 + 全部职业谁伤害最高」这类横向普查（手写 27 条不可行）。'
            '⚠ 未人工校准，只用于同口径比较。')
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')

    import importlib.util as _iu
    spec = _iu.spec_from_file_location('mig', str(HERE / 'migrate_from_archive.py'))
    mig = _iu.module_from_spec(spec)
    try:
        spec.loader.exec_module(mig)
    except SystemExit:
        pass
    mig.merge_archetypes()
    got = json.loads((SKILL / 'data' / 'archetypes.json').read_text(encoding='utf-8'))
    ok = [k for k in new if k in got]
    print('已合并进 archetypes.json：%d/%d' % (len(ok), len(new)))
    return 0 if len(ok) == len(new) else 1


if __name__ == '__main__':
    raise SystemExit(main())
