# -*- coding: utf-8 -*-
"""从 `calc.js` 抽取**职业技能逐级数据**，补回旧 `.arz` 抽取白名单丢掉的字段。

为什么需要（2026-09-20）
------------------------
`data/skills.json` 是旧技能 `gt_extract.py` 从游戏 `database.arz` 抽的，它的
`KEEP_PREFIX` 白名单只留 `offensive*/defensive*/skillCooldownTime/...`，
**`skillChanceWeight` 不在其中 ⇒ 全库 12372 条技能一条都没这个字段**。

后果（本轮实测踩到）：
* 武器池技能（WPS，`skillChanceWeight > 0`）全部被 `gd/rotation.py` 误判成
  **默认攻击候选**（`swing`），于是「雪崩」（权重 12→30，全场最高）被当成
  普通普攻，与「猛袭」抢同一个左键槽，并因为平均每击更低而被顶掉。
* 夜刃的「贝尔戈斯安的切割 / 阿玛拉斯塔的瞬影 / 处决」（权重 12→25）同样
  掉进 `swing`，把默认攻击槽搅乱。

真源：Grim Tools 离线库 `resources/app.asar` 的 `/dist/calc/calc.js`
（`ASAR_FILES['calc']`，已抽到 `data/cache/calc.js`）。它是 GT 计算器**自己**
渲染技能面板用的表，含完整逐级数组 —— 比 `.arz` 抽取**更全**。
实测 914 条可解析、0 失败；与 `skills.json` 重叠的字段逐级数值一致。

产物：`data/calc_mastery_skills.json`
    `{记录名: {字段: [逐级值]}}`，**只含 `skills.json` 缺失的字段**（补洞口径），
    这样它的用途是「只增不改」，不会覆盖既有权威值 ⇒ 不引入漂移。
    同时它是**幂等**的：重跑内容一致。

用法：
    python tools/extract_calc_skills.py               # 生成/刷新产物
    python tools/extract_calc_skills.py --check       # 只报告差异，不写
"""
from __future__ import annotations

import argparse
import io
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL))

from gd import jsobj  # noqa: E402

CALC = SKILL / 'data' / 'cache' / 'calc.js'
SKILLS = SKILL / 'data' / 'skills.json'
OUT = SKILL / 'data' / 'calc_mastery_skills.json'

_ENTRY = re.compile(r'(sk\d+)\s*:\s*\{')


def _brace_end(text: str, i: int) -> int:
    """从 `text[i] == '{'` 起找配对 `}`（跳过字符串字面量）。失败返回 -1。"""
    depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in '"\'':
            q = c
            i += 1
            while i < n and text[i] != q:
                i += 2 if text[i] == '\\' else 1
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def parse_calc_skills(path: pathlib.Path) -> dict:
    """`skXXXX` → 字段字典。只收「值是真对象且带 `templateName`」的条目。"""
    s = io.open(path, encoding='utf-8', errors='replace').read()
    out, last, bad = {}, 0, 0
    for m in _ENTRY.finditer(s):
        if m.start() < last:
            continue
        b = s.find('{', m.end() - 1)
        e = _brace_end(s, b)
        if e < 0:
            bad += 1
            continue
        try:
            v = jsobj.parse_literal(s[b:e + 1])[0]
        except Exception:
            bad += 1
            last = e + 1
            continue
        if isinstance(v, dict) and v.get('templateName'):
            out[m.group(1)] = v
        last = e + 1
    if bad:
        print('  ⚠ %d 条条目解析失败（已跳过）' % bad, file=sys.stderr)
    return out


def _as_list(v):
    if isinstance(v, list):
        return list(v)
    if isinstance(v, tuple):
        return list(v)
    return [v]


def _match_by_stats(entry: dict, cands: list, skills: dict):
    """**数值指纹消歧**：候选里唯一一个「共同字段数值全等」的记录才是它。

    ★★ 为什么需要（2026-09-21）：一个 `skillDisplayName` tag 常常对应**多条**记录，
      最典型的就是星座星位 —— `tagDevotion_B25`（狂战士）对应 `tier2_25a~f` 六条。
      旧逻辑遇到多条一律判「歧义跳过」⇒ **星座星位整类拿不到补洞字段**，
      连武器限制（`Axe` / `Axe2h` / `Spear2h`）都一起丢了。
      实测后果：狂战士的 3 个星位在**双剑**下本该 0 收益，模型却算成 **+4.28%**。

    判据取保守口径：共同字段（`skills.json` 里**已有**的数值字段）**至少 2 个**，
    且逐值相等才认；命中不唯一 ⇒ 返回 None（宁可不认，也不认错）。
    """
    def _nums(d):
        out = {}
        for k, v in (d or {}).items():
            vals = _as_list(v)
            if vals and all(isinstance(x, (int, float)) for x in vals):
                out[k] = [float(x) for x in vals]
        return out

    ev = _nums(entry)
    ev_keys = set(ev)
    scored = []
    for rec in cands:
        sv = _nums((skills.get(rec) or {}).get('stats') or {})
        common = ev_keys & set(sv)
        if len(common) < 2:
            continue
        if not all(ev[k][:len(sv[k])] == sv[k][:len(ev[k])] for k in common):
            continue
        # ★ 用**字段集的对称差**排序：越接近（差集越小）越可能是同一记录。
        #   `tier2_25b`（defensiveFreeze）与 `tier2_25d`（defensiveStun）的公共字段
        #   完全一样（+50% 物理 / +50% 流血），只靠「共同字段相等」会**并列**
        #   ⇒ 两条都判歧义 ⇒ 已点的 `tier2_25d` 拿不到武器限制（实测踩到）。
        scored.append((len(ev_keys ^ set(sv)), rec))
    if not scored:
        return None
    scored.sort()
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return None                       # 仍然并列 ⇒ 宁可不认
    return scored[0][1]


def _numeric(v) -> bool:
    """值是否**全是数值**（int/float；bool 视为数值）。

    ★ 排除规则必须是**语义的（非数值）**，不能是**名字白名单** ——
      `KEEP_PREFIX` 那种按名字筛正是当初丢掉 `skillChanceWeight` 的原因。
      非数值的（UI 图名 `skillUpBitmapName`、字符串列表 `skillTemplates`、
      calc 内部键 `o`）一律不进产物，它们对伤害模型无意义。
    """
    vals = v if isinstance(v, (list, tuple)) else [v]
    return bool(vals) and all(isinstance(x, (int, float)) for x in vals)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='只报告，不写文件')
    ap.add_argument('--out', default=str(OUT))
    a = ap.parse_args()

    if not CALC.exists():
        print('✗ 找不到 %s —— 先跑一次 gd.DB.load() 让它抽出来' % CALC, file=sys.stderr)
        return 2

    calc = parse_calc_skills(CALC)
    skills = json.load(io.open(SKILLS, encoding='utf-8'))
    print('calc.js 技能条目 %d 条' % len(calc))

    # tag → 候选记录（只认 skills.json 里已有的记录；优先 playerclass）
    by_tag: dict[str, list[str]] = {}
    for rec, d in skills.items():
        t = d.get('tag')
        if t:
            by_tag.setdefault(t, []).append(rec)

    picked, ambiguous, orphan, bystat = {}, 0, 0, 0
    for sk, entry in calc.items():
        tag = entry.get('skillDisplayName')
        if not tag:
            continue
        cands = by_tag.get(tag) or []
        if not cands:
            orphan += 1
            continue
        pl = [r for r in cands if '/playerclass' in r]
        use = pl if len(pl) == 1 else (cands if len(cands) == 1 else None)
        if use is None:
            # ★★ 2026-09-21：多条候选时必须用**数值指纹**消歧，不能一律判歧义 ——
            #   星座星位（`tagDevotion_B25` → tier2_25a~f 六条）正是被旧逻辑
            #   整类跳过的，武器限制字段就是这么丢的。
            _hit = _match_by_stats(entry, cands, skills)
            if _hit:
                picked[_hit] = entry
                bystat += 1
            else:
                ambiguous += 1
            continue
        rec = use[0]
        # ★ 模板也要对得上（同一 tag 可能被不同模板的记录复用）
        tpl = entry.get('l')
        cur = skills[rec]
        if tpl and cur.get('template') and tpl != cur['template']:
            ambiguous += 1
            continue
        picked[rec] = entry

    print('  → 唯一映射到 skills.json 记录的 %d 条'
          '（其中 ⚠ 多条候选靠**数值指纹**消歧的 %d 条；歧义跳过 %d，无记录 %d）'
          % (len(picked), bystat, ambiguous, orphan))

    # 只保留「skills.json 缺失的字段」
    gap: dict[str, dict] = {}
    for rec, entry in sorted(picked.items()):
        have = set((skills[rec].get('stats') or {}).keys())
        miss = {}
        for k, v in entry.items():
            if k in ('templateName', 'l', 'skillDisplayName', 'skillBaseDescription'):
                continue        # 元信息，不是数值
            if k in have:
                continue        # ★ 只增不改：已有权威值一律保留
            if not _numeric(v):
                continue        # 非数值字段（UI / 字符串列表）不入产物
            vals = _as_list(v)
            if not vals or all(x in (0, 0.0, False, '', None) for x in vals):
                continue
            miss[k] = vals
        if miss:
            gap[rec] = dict(sorted(miss.items()))

    tot = sum(len(v) for v in gap.values())
    print('  → 补洞：%d 条记录 / %d 个字段值' % (len(gap), tot))
    w = [r for r, v in gap.items() if 'skillChanceWeight' in v]
    print('  → 其中补回 skillChanceWeight 的：%d 条（playerclass %d 条）'
          % (len(w), sum(1 for r in w if '/playerclass' in r)))
    for r in sorted(w):
        if '/playerclass' in r:
            d = skills[r]
            print('      %-40s %-16s 权重 %s'
                  % (pathlib.Path(r).name[:-4], d.get('name') or '', gap[r]['skillChanceWeight']))

    if a.check:
        print('（--check：未写文件）')
        return 0

    p = pathlib.Path(a.out)
    old = p.read_text(encoding='utf-8') if p.exists() else None
    txt = json.dumps(gap, ensure_ascii=False, indent=0, sort_keys=True)
    if old == txt:
        print('产物已是最新，无需改动：%s' % p)
        return 0
    p.write_text(txt, encoding='utf-8')
    print('已写入 %s（%.1f KB）' % (p, p.stat().st_size / 1024))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
