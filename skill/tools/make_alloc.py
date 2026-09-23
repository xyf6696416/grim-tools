"""按形态生成一份**加点 JSON**，供 `GD_SKILL_JSON` 注入整条伤害/配装链路。

为什么需要这个工具（2026-09-20）
--------------------------------
`gd/alloc.py` 早就按形态算加点，`gd/dps.load_char` 也早就支持 `skill_override`
（为 `tools/gt_regress.py` 加的），但**两者之间没有管道**：

    gd/alloc  →（缺失）→  gd/dps / gd/opt 的局部搜索

后果：`GD_ARCHETYPE=<新形态> gd auto` 时，选装备用的是**存档里的旧加点**，
新形态的主输出技能根本不在循环里 —— 搜索等于在给别人的 BD 挑装备。
本工具把这个缺口补上：形态 → 加点 JSON → `GD_SKILL_JSON=<该文件>`。

用法
----
```bash
PY=".../envs/default/Scripts/python.exe"
# ① 纯贪心（形态 core_skills 的默认答案）
$PY tools/make_alloc.py avalanche 71 --out data/plans/Sam_alloc_avalanche.json
# ② 手工指定优先技能（中文名或记录名，同 gd/alloc 的 GD_ALLOC 口径）
$PY tools/make_alloc.py avalanche 71 --set "雪崩:10,双刃:16,气爆:12" --out out.json
# ③ 跑整条链路
GD_SKILL_JSON=data/plans/Sam_alloc_avalanche.json GD_ARCHETYPE=avalanche \
  $PY -m gd dps Sam
```

字段口径（★ 与 `gd/alloc.py::allocate` 一致，勿另起一套）
* `skills` = `{记录名: 等级}`，**含 `_classtraining_*`（精通条）** ——
  这正是 `skill_override` 要求的形态（整体替换存档加点）。
* 等级是**基础加点**（不含装备 +技能）。装备加成由 `load_char` 的
  `skill_plus` 自动叠加，所以这里**不能**预先加进去。
* `--set` 走 `gd/alloc` 自己的 `GD_ALLOC` 机制（会做 tier 门槛 / 形态归属 /
  预算校验），不要在这里另写一套解析。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('archetype', help='形态键（data/archetypes.json 的键）')
    ap.add_argument('level', type=int, nargs='?', default=0,
                    help='角色等级（默认从 --char 读，或 71）')
    ap.add_argument('--char', default='', help='按该角色的存档等级取等级')
    ap.add_argument('--set', default='',
                    help='强制加点，如 "雪崩:10,双刃:16,气爆:12"（等价 gd/alloc 的 GD_ALLOC）')
    ap.add_argument('--out', default='',
                    help='输出 JSON 路径（默认打印到 stdout）')
    a = ap.parse_args()

    level = a.level
    if not level and a.char:
        from gd import dps as _D
        level = _D.load_char(a.char, '', True).get('level') or 0
    if not level:
        level = 71

    if a.set:
        os.environ['GD_ALLOC'] = a.set
    # ★ 必须在 import gd.alloc **之前**设好 GD_ALLOC（模块级副作用）
    from gd import alloc as AL

    r = AL.allocate(a.archetype, level)
    ov = {s['skill']: int(s['level']) for s in (r.get('skills') or []) if s['level']}
    if not ov:
        print('✗ 形态 %s 没算出任何加点（形态键写错？）' % a.archetype, file=sys.stderr)
        return 2

    used, budget = r.get('used'), r.get('budget')
    # ⚠ 旧版这里印的是 `r.get('attr_used') or r.get('used')` —— `allocate()` 根本
    #   不返回 `attr_used`，于是**属性点那一栏印的是技能点**（2026-09-20 修）。
    #   属性点分配要有装备才能反推（`alloc.attribute_plan`），这里只报预算。
    print('形态 %-14s ｜ 等级 %d ｜ 技能点 %s/%s ｜ 属性点预算 %d ｜ 虔诚点上限 %d'
          % (a.archetype, level, used, budget,
             AL.attr_budget(level), AL.devotion_budget(level)),
          file=sys.stderr)
    print('  ⚠ 本文件是**基础加点**（不含装备 +技能）；装备加成由 load_char 叠加',
          file=sys.stderr)
    for s in sorted((r.get('skills') or []), key=lambda x: -x['level']):
        print('   %-46s %3d  %s'
              % (s['skill'].split('/')[-1], s['level'], s.get('why', '')), file=sys.stderr)

    txt = json.dumps(ov, ensure_ascii=False, indent=1)
    if a.out:
        p = pathlib.Path(a.out)
        if not p.is_absolute():
            p = SKILL / p
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(txt, encoding='utf-8')
        print('已写入 %s（%d 条加点）' % (p, len(ov)), file=sys.stderr)
    else:
        print(txt)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
