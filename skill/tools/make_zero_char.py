# -*- coding: utf-8 -*-
r"""make_zero_char.py —— 造一个**从 0 的角色**到临时存档目录（「不参考存档」的测试底子）。

为什么需要它
------------
`gd auto` 是**存档锚定**的：等级、职业、技能点/属性点/虔诚点预算都从存档读。
想做「75 级 死灵+狂战士、不参考存档、从 0 构建」，就得先有一个
「lv75 / 职业 {8,10} / 什么都没点 / 什么装备都没有」的合法存档。

做法：拿一个**已有角色当壳**（默认 `_Cx666` —— 游戏里唯一 class{8,10} 的角色），
复制到临时目录后按 block2 的**明文字段偏移表**做定长改写：

    level_in_bio  → 目标等级          experience    → 保留
    attribute_points → 该等级预算      physique/cunning/spirit → 50.0（= 未投点的基础）
    skill_points  → 该等级预算         devotion_points / total_devotion_points → 虔诚上限

★ 预算全部来自 `gd/alloc.py` + `data/level_table.json`（官方表），不是拍脑袋。
★ **只写临时目录**（`--out`，默认 `%TEMP%/gd_zero_save`），真存档一个字节都不动；
  要跑工具时用 `GD_SAVE=<临时目录>` 指过去（`gd/paths.save_dir()` 认这个环境变量）。

⚠ 没做（也不打算做）：清空装备/清空星座树。理由：`GD_NO_CUR=1` 已经让**搜索**
  完全不看存档装备（「不参考存档」就是它），而清装备要动 block3 的变长区域，
  风险远大于收益。所以这个角色仍然「穿着壳角色的旧装备」——只当**起点**用。

用法::

    python tools/make_zero_char.py --level 75 --classes 8,10 --name _ZERO75
    GD_SAVE=<out>/save python -m gd auto _ZERO75 --extreme --with-weapon
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(HERE))

from gd import paths as _P                        # noqa: E402
from gd.save import core as C                     # noqa: E402
from gd.save import write as W                    # noqa: E402


def _classids(skills):
    out = set()
    for rec in skills:
        b = os.path.basename(rec).replace('.dbr', '')
        if b.startswith('_classtraining_class'):
            try:
                out.add(int(b.replace('_classtraining_class', '')))
            except ValueError:
                pass
    return out


@contextlib.contextmanager
def _unoverridden():
    """临时摘掉 `GD_SAVE`（并清 memo）。

    ★★ 为什么必须：`recipe` 驱动时会带着 `GD_SAVE=<临时目录>` 调本工具，
      于是 `paths.save_dir()` 指向**临时目录** ⇒
        ① 安全闸拿它当「真存档」⇒ 写临时目录反被判成「写真存档」而拒绝（实测踩到）；
        ② 挑壳会从**临时目录**里挑（挑到上一个测试角色 `_CHK10_1`，而它正是要造的目标）。
      ⇒ 壳的候选与安全闸**都必须看未被重定向的真实存档目录**。
    """
    from gd import paths as _PP
    _old = os.environ.pop('GD_SAVE', None)
    _PP._SAVE_DIR_MEMO.clear()
    try:
        yield
    finally:
        if _old is not None:
            os.environ['GD_SAVE'] = _old
        _PP._SAVE_DIR_MEMO.clear()


def pick_shell(classes, prefer=10):
    """挑壳：**优先包含全部目标职业**的；没有就取**重叠最多**的（缺的职业在写档时合成）。

    ★ 两点必须自动化：
      ① 壳的职业对不上时，`--classes` 只是**新增**精通条，壳原有职业会留着
         ⇒ classids 变并集（实测：拿 (8,10) 的壳造 {10,4}，读出来还是 [8,10]）；
      ② 全库只有 5 个角色（{4,10}/{8,10}/{3,8}/{1,4}/{4}），而「狂战士 + 任一职业」
         有 9 个组合 ⇒ **必须允许合成**缺失的精通条，否则一半组合造不出来。
    """
    import autobuild as _AB
    want = {int(x) for x in classes}
    best = None          # (完全覆盖?, 重叠数, 是否含 prefer, 等级)
    with _unoverridden():                        # ★ 只看**真实**存档目录里的角色
        for name in _P.characters():
            try:
                lv, cls, _sk = _AB.read_meta(name)
            except Exception:                                  # noqa: BLE001
                continue
            hit = len(want & set(cls))
            key = (1 if want <= set(cls) else 0, hit, 1 if prefer in cls else 0, lv)
            if best is None or key > best[1]:
                best = (name, key)
    return best[0] if best else ''


def build(shell, level, classes, name, out_root, keep_gear=True, learn=()):
    """返回 (out_save_dir, 校验信息 dict)"""
    import gd_alloc as A
    if not shell or shell not in _P.characters():
        shell = pick_shell(classes)
        if not shell:
            raise SystemExit('✗ 找不到任何**包含职业 %s** 的现有角色当壳；'
                             '请先有一个含该职业组合的角色。' % sorted(int(x) for x in classes))
        print('  （未指定 --shell，自动挑壳：%s）' % shell)
    with _unoverridden():          # ★ 壳的来源也在**真实**存档目录（GD_SAVE 可能指向临时目录）
        src_dir = pathlib.Path(_P.characters()[shell])
    tgt_save = pathlib.Path(out_root) / 'save'
    tgt_dir = tgt_save / 'main' / name
    # ★ 安全闸：绝不写到真实存档目录**或其内部任何位置**。
    #   ⚠ 第一版只比了「tgt_save == 真实目录」，于是 `--out <真实目录>` 会让目标变成
    #     `<真实目录>/save`（**在它里面**）⇒ 闸门形同虚设，自检的功能性验证当场抓到。
    with _unoverridden():
        real = pathlib.Path(_P.save_dir()[0]).resolve()
    _t = tgt_save.resolve()
    if _t == real or real in _t.parents or _t in real.parents:
        raise SystemExit('✗ 拒绝写入真实存档目录（或其内部）：%s' % real)
    tgt_dir.mkdir(parents=True, exist_ok=True)
    for fn in ('player.gdc', 'levels_world001.map'):
        p = src_dir / fn
        if p.exists():
            try:
                shutil.copy2(p, tgt_dir / fn)
            except OSError as e:
                # ★ `levels_world001.map` 在 Program Files 下有时读不出来（ACL/占用）。
                #   它只是地图解锁记录，**我们所有工具都不读它** ⇒ 缺了就缺了，别中断。
                print('  ⚠ 跳过 %s（%s）' % (fn, type(e).__name__))

    live = str(tgt_dir / 'player.gdc')
    raw = (tgt_dir / 'player.gdc').read_bytes()
    d = C.parse(live, record=True)
    b2 = d['block_map'][2]
    off = b2['_off']
    image = bytearray(d['record']['image'])
    plan_ = list(d['record']['plan'])

    want = {
        'level_in_bio': int(level),
        'attribute_points': int(A.attr_budget(level)),
        'skill_points': int(A.skill_budget(level)),
        'devotion_points': int(A.devotion_budget(level)),
        'total_devotion_points': int(A.devotion_budget(level)),
    }
    for k, v in want.items():
        W.patch_i32(image, off[k], v)
    for k in ('physique', 'cunning', 'spirit'):        # 未投点的基础三围 = 50.0
        W.patch_f32(image, off[k], 50.0)

    # ★★ **文件头**还有一个等级字段 —— `doc['level']` 读的是它，不是 block2 的
    #   `level_in_bio`（改了 block2 那个，`read_meta` 依然看到旧等级，实测踩到）。
    #   头部是**变长**的（name/tag 都是带长度前缀的字符串），所以偏移只能按解析顺序推。
    _r = C.Reader(raw, record=False)
    _r.pos = 4
    _r.u32()                    # magic
    _r.i32()                    # version
    _r.string('utf-16-le')      # name
    _r.bool()                   # male
    _r.string()                 # class_tag
    W.patch_i32(image, _r.pos, int(level))
    level_off = _r.pos

    # ★★ **文件头的职业标签也必须改**：`doc['classes']` 是从 `tagSkillClassName<XXYY>`
    #   解出来的，只改 block8 的精通条会让「头说 A、block8 说 B」不一致
    #   （`_classids` 走 block8、`doc['classes']` 走头部，两边都可能被下游读）。
    #   2 职业的标签**等长**（各 2 位数字）⇒ 可以原地覆写；不等长就拒绝。
    _tag = d.get('class_tag') or ''
    _new_tag = 'tagSkillClassName' + ''.join('%02d' % c for c in
                                             sorted({int(x) for x in classes}))
    # 精确：重新按解析顺序走一遍拿 tag 的明文偏移（name 是变长 utf-16，只能这么算）
    _r2 = C.Reader(raw, record=False)
    _r2.pos = 4
    _r2.u32(); _r2.i32(); _r2.string('utf-16-le'); _r2.bool()
    _taglen_off = _r2.pos
    _tag_off = _taglen_off + 4
    if _tag and len(_new_tag) == len(_tag):
        image[_tag_off:_tag_off + len(_new_tag)] = _new_tag.encode('ascii')
        tag_patched = _new_tag
    else:
        tag_patched = ''                                 # 等长才改；否则只留 block8

    # 职业：把精通条重置为「只解锁」（1 级），其余技能清空
    b8 = d['block_map'][8]
    old = list(b8['skills'])
    new_skills = [s for s in old if '/default/' in s['skill']]     # 保留默认攻击等系统技能
    _want_cls = sorted({int(x) for x in classes})
    # ★ 必须**先清掉壳里不属于目标组合的精通条**，否则 `read_meta` 读到的 classids
    #   是「壳 ∪ 目标」的并集（实测：拿 (8,10) 的壳去造 {10,4}，classids 还是 [8,10]）。
    new_skills = [s for s in new_skills
                  if not (s['skill'].split('/')[-1].startswith('_classtraining_class')
                          and not any(('_classtraining_class%02d.dbr' % c) ==
                                      s['skill'].split('/')[-1] for c in _want_cls))]
    for cid in _want_cls:
        base = '_classtraining_class%02d.dbr' % cid
        hit = [s for s in old if s['skill'].split('/')[-1] == base]
        if hit:
            e = dict(hit[0])
        else:
            e = dict(skill='records/skills/playerclass%02d/%s' % (cid, base),
                     enabled=True, unk=False, devotion_level=0, devotion_exp=0,
                     sublevel=0, active=False, transition=False,
                     autocast='', autocast_controller='')
        e['level'] = 1
        new_skills.append(e)
    # ★ `--learn`：把指定技能也学上（lv1）。为什么需要：**变身形态**在「从 0」角色上
    #   会算出 **DPS 0** —— 形态门控（陷阱 #78）按 `root_skills[0].granted` 裁技能栏，
    #   而没学过 `werewolf1` / `wereraven1` 的角色没有任何形态攻击可用
    #   ⇒ 变身形态恒为 0、比不出优劣。要比较形态就得先把形态及其授予的攻击技学上。
    _have = {s['skill'] for s in new_skills}
    for short in learn:
        short = short.strip()
        if not short:
            continue
        cand = [r for r in (short,
                            'records/skills/playerclass%02d/%s' % (int(classes[0]), short),
                            'records/skills/playerclass%02d/%s' % (int(classes[-1]), short))]
        rec = None
        for c in cand:
            hit = [s for s in old if s['skill'].split('/')[-1] == c.split('/')[-1]]
            if hit:
                rec = dict(hit[0])
                break
            if c.startswith('records/skills/'):
                rec = dict(skill=c, enabled=True, unk=False, devotion_level=0,
                           devotion_exp=0, sublevel=0, active=False, transition=False,
                           autocast='', autocast_controller='')
                break
        if rec and rec['skill'] not in _have:
            rec['level'] = 1
            new_skills.append(rec)
            _have.add(rec['skill'])
    new_segs = W.w_skills_region(new_skills, b8['version'] >= 7)
    blocks = W.block_ranges(raw)
    repl = {}
    if 8 in [b['id'] for b in blocks]:
        repl[8] = (b8['_off_skills'], b8['_off_skills_end'], new_segs)
    segs = W.rebuild(image, plan_, blocks, repl)
    img, plan2 = W.flatten(segs)
    out_bytes = C.encode(img, plan2, d['seed'])
    (tgt_dir / 'player.gdc').write_bytes(out_bytes)

    # ---------------- 回读校验
    v = C.parse(live, record=True)
    vb2, vb8 = v['block_map'][2], v['block_map'][8]
    info = {
        'name': v['name'], 'classes': v['classes'], 'class_tag': v.get('class_tag'),
        'tag_patched': bool(tag_patched),
        'level': v['level'], 'level_in_bio': vb2['level_in_bio'],
        'blocks_ok': v['all_blocks_ok'],
        'attribute_points': vb2['attribute_points'],
        'skill_points': vb2['skill_points'],
        'devotion_points': vb2['devotion_points'],
        'total_devotion_points': vb2['total_devotion_points'],
        'physique': vb2['physique'], 'cunning': vb2['cunning'], 'spirit': vb2['spirit'],
        'skills': len(vb8['skills']),
        'classids': sorted(_classids([s['skill'] for s in vb8['skills']])),
        'roundtrip_ok': C.encode(v['record']['image'], v['record']['plan'],
                                 v['seed']) == out_bytes,
    }
    errs = []
    if not info['blocks_ok']:
        errs.append('块校验失败')
    if not info['roundtrip_ok']:
        errs.append('加密往返不一致')
    if info['level'] != int(level):
        errs.append('等级未生效 %s' % info['level'])
    if set(info['classids']) != set(int(x) for x in classes):
        errs.append('职业未生效 %s' % info['classids'])
    info['errors'] = errs
    return str(tgt_save), info


def main():
    ap = argparse.ArgumentParser(description='造一个「从 0」的角色到临时存档目录')
    ap.add_argument('--level', type=int, default=75)
    ap.add_argument('--classes', default='8,10', help='classNN 编号，逗号分隔（8=死灵,10=狂战士）')
    ap.add_argument('--shell', default='',
                    help='当壳的角色；不给则**自动挑**一个包含目标职业的（取等级最高者）')
    ap.add_argument('--name', default='_ZERO75')
    ap.add_argument('--learn', default='',
                    help='额外学上的技能（逗号分隔的短名，会依次尝试 playerclass08/10 前缀）。'
                         '★ 比较**变身形态**时必须给：不学 werewolf1 / wereraven1 的角色，'
                         '形态门控会把攻击技能全剔掉 ⇒ DPS 恒为 0，比不出优劣')
    ap.add_argument('--out', default=os.path.join(
        os.environ.get('TEMP') or '/tmp', 'gd_zero_save'))
    a = ap.parse_args()
    cls = [x.strip() for x in a.classes.split(',') if x.strip()]
    learn = [x.strip() for x in a.learn.split(',') if x.strip()]
    out, info = build(a.shell, a.level, cls, a.name, a.out, learn=learn)
    print('=' * 74)
    print('已写出临时存档目录：%s' % out)
    print('  跑：GD_SAVE=%s python -m gd auto %s --extreme --with-weapon' % (out, a.name))
    print('=' * 74)
    print(json.dumps(info, ensure_ascii=False, indent=1))
    if info['errors']:
        print('✗ 校验未通过：%s' % info['errors'])
        return 1
    print('✓ 校验通过')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
