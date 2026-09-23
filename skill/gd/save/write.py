# -*- coding: utf-8 -*-
"""恐怖黎明存档「变长写入」能力

原理
----
解密后可得到两样东西：
  image —— 整个文件的【明文字节镜像】
  plan  —— 字段计划 [(kind, start, end), ...]，kind ∈ {plain,i,len,b,chk,x}
加密时 `gd_save.encode(image, plan, seed)` 按 plan 逐段变换回密文。

`len` 段是长度字段（与 state 整块异或、不推进 state），`chk` 段是块尾状态校验和
（加密时直接写当前 state）。因此：
  * 改定长数值 → 直接改 image 里对应字节即可，plan 不变；
  * 改变长内容 → 把 image+plan 转成「段列表」[(kind, bytes)]，
    替换掉目标区间的段、按新字节数重算块长度，再拼回去重新加密。
"""
import struct

from . import core as S


# ------------------------------------------------------------------ 基础
def block_ranges(data):
    """只记录每个块在文件里的字节区间。

    载荷必须用 SPEC 里的正规解析器消费 —— 因为嵌套长度字段不推进 state、
    嵌套块尾校验和也不推进，用 advance() 逐字节推进会算错 state 导致提前中断。
    """
    r = S.Reader(data)
    r.u32(); r.i32(); r.string('utf-16-le'); r.bool(); r.string()
    r.i32(); r.bool(); r.byte()
    r.checksum(); r.i32(); r.bytes_(16)
    out = []
    while r.pos + 8 <= len(data):
        st = r.pos
        bid = r.i32()
        blen = r.len_field()
        ps, state0 = r.pos, r.state
        if bid in S.SPEC:
            try:
                S.SPEC[bid](r)
                if r.pos != ps + blen:
                    raise ValueError('长度不符')
            except Exception:
                r.pos, r.state = ps, state0
                r.advance(blen)
        else:
            r.advance(blen)
        ck = r.pos
        ok = (r.checksum() == r.state)
        out.append({'id': bid, 'start': st, 'payload': ps, 'plen': blen,
                    'chk': ck, 'end': r.pos})
        if not ok:
            break
    return out


def segs_of(plan, image, lo, hi):
    """取 [lo,hi) 区间内的 (kind, bytes) 段（plan 必须完整覆盖）"""
    out = []
    for kind, s, e in plan:
        if s >= lo and e <= hi:
            if s != lo + sum(len(b) for _, b in out):
                raise ValueError('plan 覆盖有洞: 期望 %d 实际 %d' %
                                 (lo + sum(len(b) for _, b in out), s))
            out.append((kind, bytes(image[s:e])))
    if lo + sum(len(b) for _, b in out) != hi:
        raise ValueError('plan 未覆盖 [%d,%d)' % (lo, hi))
    return out


def flatten(segs):
    img = bytearray()
    plan = []
    for kind, bs in segs:
        plan.append((kind, len(img), len(img) + len(bs)))
        img += bs
    return bytes(img), plan


def rebuild(image, plan, blocks, repl):
    """repl: {block_id: (off_start, off_end, new_segs)}  按字节区间替换块载荷的一部分"""
    segs = segs_of(plan, image, 0, blocks[0]['start'])
    for b in blocks:
        segs += segs_of(plan, image, b['start'], b['start'] + 4)      # 块 id
        if b['id'] in repl:
            off_s, off_e, new = repl[b['id']]
            head = segs_of(plan, image, b['payload'], off_s)
            tail = segs_of(plan, image, off_e, b['chk'])
            plen = (off_s - b['payload']) + sum(len(x[1]) for x in new) + (b['chk'] - off_e)
            segs.append(('len', struct.pack('<I', plen)))
            segs += head + new + tail
        else:
            segs += segs_of(plan, image, b['start'] + 4, b['chk'])    # 长度字段 + 载荷
        segs.append(('chk', bytes(image[b['chk']:b['chk'] + 4])))
    segs += segs_of(plan, image, blocks[-1]['end'], len(image))
    return segs


# ------------------------------------------------------------------ Writer
class Writer:
    """与 gd_save.Reader 对称的写手，边写边产出字段计划"""

    def __init__(self):
        self.buf = bytearray()
        self.entries = []

    def _put(self, kind, bs):
        st = len(self.buf)
        self.buf += bs
        self.entries.append((kind, st, len(self.buf)))

    def i32(self, v):
        self._put('i', struct.pack('<I', v & 0xFFFFFFFF))

    def f32(self, v):
        self._put('i', struct.pack('<f', v))

    def byte(self, v):
        self._put('b', bytes([v & 0xFF]))

    def bool(self, v):
        self.byte(1 if v else 0)

    def string(self, s):
        b = (s or '').encode('ascii')
        self.i32(len(b))
        self._put('b', b)

    def segs(self):
        return [(k, bytes(self.buf[a:b])) for k, a, b in self.entries]


def w_item(w, it, v11, kind):
    """与 gd_save.read_item 完全对称"""
    for k in ('basename', 'prefix', 'suffix', 'modifier', 'transmute'):
        w.string(it.get(k, ''))
    w.i32(it.get('seed', 0))
    w.string(it.get('relic_name', ''))
    w.string(it.get('relic_bonus', ''))
    w.i32(it.get('relic_seed', 0))
    w.string(it.get('augment_name', ''))
    w.i32(it.get('unk', 0))
    w.i32(it.get('augment_seed', 0))
    w.i32(it.get('f_relic_comp', 0))
    if v11:
        w.i32(it.get('f_a', 0))
        w.i32(it.get('f_b', 0))
        w.i32(it.get('qty', 1))
        w.i32(it.get('f_c', 0))
        w.i32(it.get('f_d', 0))
    else:
        w.i32(it.get('qty', 1))
    if kind == 'equip':
        w.bool(it.get('attached', True))
    else:
        w.i32(it.get('gx', 0))
        w.i32(it.get('gy', 0))


def w_equip_region(use_alt, equipment, alt1_unused, alt1, alt2_unused, alt2, v11):
    """装备区（use_alt_weaponset 起到块尾）的明文段"""
    w = Writer()
    w.bool(use_alt)
    for it in equipment:
        w_item(w, it, v11, 'equip')
    w.bool(alt1_unused)
    for it in alt1:
        w_item(w, it, v11, 'equip')
    w.bool(alt2_unused)
    for it in alt2:
        w_item(w, it, v11, 'equip')
    return w.segs()


# ------------------------------------------------------------------ 技能区
def w_skill_ent(w, s, new_layout):
    """与 gd_save._skill 完全对称（解析用的 _off_level 不写出）"""
    w.string(s['skill'])
    w.i32(s['level'])
    w.bool(s['enabled'])
    if new_layout:
        w.bool(s['unk'])
    w.i32(s['devotion_level'])
    w.i32(s['devotion_exp'])
    w.i32(s['sublevel'])
    w.bool(s['active'])
    w.bool(s['transition'])
    w.string(s['autocast'])
    w.string(s['autocast_controller'])


def w_skills_region(skills, new_layout):
    """块 8 技能区（count:i32 + 全部条目）的明文段"""
    w = Writer()
    w.i32(len(skills))
    for s in skills:
        w_skill_ent(w, s, new_layout)
    return w.segs()


def is_star(rec):
    """是不是**星点**（而不是星座授予的技能）。

    ★★ 为什么必须分清（2026-09-21 实测）：
      游戏判定一个星点「是否点亮」看的是 **`devotion_level`**，**不是 `level`**
      （见 `gd/save/skill.py` 撤点分支与 `docs/pitfalls.md`）。
      而星座**授予的技能**（`*_skill.dbr`）是另一回事 —— 它的 `level` 才是技能等级，
      `devotion_level` 保持 0。

        records/skills/devotion/tier1_08a.dbr        ← 星点（level=1, devotion_level=1）
        records/skills/devotion/tier1_08e_skill.dbr  ← proc（level=20, devotion_level=0）
    """
    return '/devotion/' in (rec or '') and not str(rec).endswith('_skill.dbr')


def blank_skill(rec, level=1):
    """新建条目的默认字段 —— 照抄存档里普通技能的模式

    ★ 星点必须**同时把 `devotion_level` 设成 `level`**（2026-09-21 修）：
      旧实现恒写 0 ⇒ 新增的星点「存档里看着点了、游戏里星星不亮」——
      与 `skill.py` 撤点分支踩过的是**同一个字段的两面**。
    """
    return {'skill': rec, 'level': level, 'enabled': True, 'unk': False,
            'devotion_level': (level if is_star(rec) else 0),
            'devotion_exp': 0, 'sublevel': 0,
            'active': False, 'transition': False,
            'autocast': '', 'autocast_controller': ''}


# ------------------------------------------------------------------ 定长改写
def patch_i32(image, off, v):
    image[off:off + 4] = struct.pack('<I', v & 0xFFFFFFFF)


def patch_f32(image, off, v):
    image[off:off + 4] = struct.pack('<f', v)
