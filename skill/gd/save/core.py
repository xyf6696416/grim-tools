# -*- coding: utf-8 -*-
"""
恐怖黎明 (Grim Dawn) 存档完整解析器  —— 最终版
覆盖: 头部 / 块1,2,3(背包+装备),4(仓库),5,6,7,8(技能),10,12,13,15,16,17

加密算法来源: gd-edit (github.com/Odie/gd-edit) src/gd_edit/io/gdc.clj
物品结构为 GD 1.3 (块版本 >= 11) 实测校准结果。

核心要点:
  * 4 字节数值(int32/float): 整块与同一 state 异或, 然后按文件字节顺序推进 4 次
  * 单字节/字符串: 逐字节链式
  * 块 length 字段: 只异或, 不推进 state
  * 块 3/4 内有嵌套块
  * 状态推进与字段分组无关 -> 可用于校验任意偏移
  * 每个块尾部 4 字节明文 == 当前 state (游戏自带的校验和)
"""
import struct
import os
import glob

from .. import _timing as TM

MASK = 0xFFFFFFFF
MULT = 39916801
SEED_MASK = 0x55555555
MAGIC = 0x58434447

CLASS_TAG = {
    '01': ('士兵', 'Soldier'), '02': ('爆破专家', 'Demolitionist'),
    '03': ('神秘学者', 'Occultist'), '04': ('夜刃', 'Nightblade'),
    '05': ('奥术师', 'Arcanist'), '06': ('萨满', 'Shaman'),
    '07': ('审判官', 'Inquisitor'), '08': ('死灵法师', 'Necromancer'),
    '09': ('誓约者', 'Oathkeeper'), '10': ('狂战士', 'Berserker'),
    '11': ('符文大师', 'Runemaster'),
}
DIFF = {0: '普通', 1: '精英', 2: '终极'}
SLOTS = ['头部', '项链', '胸甲', '腿甲', '靴子', '手套', '戒指1', '戒指2',
         '腰带', '肩甲', '勋章', '圣物']
CATE = [
    ('records/items/gearhead/', '头部'), ('records/items/geartorso/', '胸甲'),
    ('records/items/gearlegs/', '腿甲'), ('records/items/gearfeet/', '靴子'),
    ('records/items/gearhands/', '手套'), ('records/items/gearshoulders/', '肩甲'),
    ('records/items/gearaccessories/necklaces/', '项链'),
    ('records/items/gearaccessories/rings/', '戒指'),
    ('records/items/gearaccessories/waist/', '腰带'),
    ('records/items/gearaccessories/medals/', '勋章'),
    ('records/items/gearweapons/', '武器'),
    ('records/items/materia/', '材料'),
    ('records/items/crafting/', '打造材料'),
    ('records/items/questitems/', '任务物品'),
    ('records/items/lootaffixes/prefix/', '前缀'),
    ('records/items/lootaffixes/suffix/', '后缀'),
    ('records/items/lootaffixes/completion/', '词缀'),
    ('records/items/enemy/', '怪物'),
]


def pretty(path):
    if not path:
        return ''
    for pre, cn in CATE:
        if path.startswith(pre):
            return '%s:%s' % (cn, path[len(pre):].replace('.dbr', ''))
    return path.replace('.dbr', '')


def gen_table(seed):
    t, v = [], seed & MASK
    for _ in range(256):
        v = ((v >> 1) | ((v & 1) << 31)) & MASK
        v = (v * MULT) & MASK
        t.append(v)
    return t


class Reader:
    """解密读取器。record=True 时同时产出「字段计划 plan」与「明文字节镜像 image」，
    二者可用于把镜像重新加密回原文件（已用 5 个存档做过逐字节往返验证）。"""

    def __init__(self, data, record=False):
        self.data = data
        self.seed = struct.unpack('<I', data[:4])[0] ^ SEED_MASK
        self.table = gen_table(self.seed)
        self.state = self.seed
        self.pos = 4
        self.record = record
        self.plan = []                      # [(kind, start, end)]
        self.image = bytearray(data) if record else None
        self.block_end = len(data)

    # ---- 记录辅助 ----
    def _log(self, kind, start, n, plain):
        if self.record:
            self.plan.append((kind, start, start + n))
            self.image[start:start + n] = plain

    def raw(self, n):
        b = self.data[self.pos:self.pos + n]
        if len(b) != n:
            raise EOFError('越界 @%d' % self.pos)
        self.pos += n
        return b

    def u32(self):
        p0 = self.pos
        raw = self.raw(4)
        v = struct.unpack('<I', raw)[0] ^ self.state
        for b in raw:
            self.state = (self.state ^ self.table[b]) & MASK
        self._log('i', p0, 4, struct.pack('<I', v))
        return v

    def i32(self):
        v = self.u32()
        return v - (1 << 32) if v & 0x80000000 else v

    def byte(self):
        p0 = self.pos
        c = self.raw(1)[0]
        v = c ^ (self.state & 0xFF)
        self.state = (self.state ^ self.table[c]) & MASK
        self._log('b', p0, 1, bytes([v]))
        return v

    def bool(self):
        return self.byte() == 1

    def bytes_(self, n):
        p0 = self.pos
        out = bytearray()
        for _ in range(n):
            c = self.raw(1)[0]
            v = c ^ (self.state & 0xFF)
            self.state = (self.state ^ self.table[c]) & MASK
            out.append(v)
        self._log('b', p0, n, bytes(out))
        return bytes(out)

    def f32(self):
        return struct.unpack('<f', struct.pack('<I', self.u32()))[0]

    def string(self, enc='ascii', static=-1):
        n = static if static >= 0 else self.i32()
        if n < 0 or n > 200000:
            raise ValueError('字符串长度异常 %d @%d' % (n, self.pos))
        raw = self.bytes_(n * (2 if enc == 'utf-16-le' else 1))
        return raw.hex() if enc == 'bytes' else raw.decode(enc, errors='replace')

    def len_field(self):
        """块/嵌套块的长度字段：与 state 整块异或，但**不推进 state**"""
        p0 = self.pos
        raw = self.raw(4)
        v = struct.unpack('<I', raw)[0] ^ self.state
        self._log('len', p0, 4, struct.pack('<I', v))
        return v

    def advance(self, n):
        """按字节推进 state。
        记为 'x' 段：往返安全（能原样重建密文），但镜像里存的不是真实明文，
        因此**该段不能作为改档落点**，且它上游被修改时内容会变。"""
        p0 = self.pos
        out = bytearray()
        for _ in range(n):
            c = self.raw(1)[0]
            out.append(c ^ (self.state & 0xFF))
            self.state = (self.state ^ self.table[c]) & MASK
        self._log('x', p0, n, bytes(out))

    def checksum(self):
        """块尾 4 字节明文 state（不异或、不推进）。改档后需按新 state 重写，故单独标记为 'chk'"""
        p0 = self.pos
        raw = self.raw(4)
        self._log('chk', p0, 4, raw)
        return struct.unpack('<I', raw)[0]


def encode(image, plan, seed):
    """按字段计划把明文字节镜像重新加密为存档字节流"""
    table = gen_table(seed)
    out = bytearray(len(image))
    state = seed
    for kind, start, end in plan:
        chunk = image[start:end]
        if kind == 'plain':
            out[start:end] = chunk
        elif kind == 'chk':
            # 块尾状态校验和：始终写入「当前 state」。未改档时与原值相同，
            # 改档后自动变成新值 —— 这是让修改后文件仍能通过游戏校验的关键
            out[start:end] = struct.pack('<I', state)
        elif kind == 'len':
            v = struct.unpack('<I', chunk)[0] ^ state
            out[start:end] = struct.pack('<I', v & MASK)
        elif kind == 'i':
            v = struct.unpack('<I', chunk)[0] ^ state
            out[start:end] = struct.pack('<I', v & MASK)
            for b in out[start:end]:
                state = (state ^ table[b]) & MASK
        else:                                # 'b'/'x'：逐字节链式
            for j, p in enumerate(chunk):
                c = p ^ (state & 0xFF)
                out[start + j] = c
                state = (state ^ table[c]) & MASK
    return bytes(out)


def reencode_and_check(path):
    """往返验证：解密→再加密 必须与原文件逐字节一致"""
    data = open(path, 'rb').read()
    r = Reader(data, record=True)
    # 前 4 字节种子头为明文
    r.plan.append(('plain', 0, 4))
    parse_into(r)
    rebuilt = encode(r.image, r.plan, r.seed)
    return rebuilt == data


def parse_into(r):
    """在给定 Reader 上跑完整解析流程（parse() 的内部实现）"""
    out = {}
    out['magic'] = r.u32()
    out['version'] = r.i32()
    out['name'] = r.string('utf-16-le')
    out['male'] = r.bool()
    tag = r.string()
    out['class_tag'] = tag
    codes = tag[len('tagSkillClassName'):] if tag.startswith('tagSkillClassName') else ''
    out['classes'] = [CLASS_TAG.get(codes[i:i + 2], (codes[i:i + 2],))[0]
                      for i in range(0, len(codes), 2)]
    out['level'] = r.i32()
    out['hardcore'] = r.bool()
    out['expansion'] = r.byte()
    out['header_ok'] = (r.checksum() == r.state)
    out['data_version'] = r.i32()
    out['mystery'] = r.bytes_(16)
    blocks = []
    while r.pos + 8 <= len(r.data):
        bid = r.i32()
        blen = r.len_field()
        pstart, state0 = r.pos, r.state
        r.block_end = pstart + blen
        plan0 = len(r.plan)
        snap = bytes(r.image[pstart:min(pstart + blen, len(r.image))]) if r.record else b''
        val, err = None, None
        if bid in SPEC:
            try:
                val = SPEC[bid](r)
                if r.pos != pstart + blen:
                    err = '字段规格短 %d 字节' % (pstart + blen - r.pos)
                    raise ValueError(err)
            except Exception as e:
                err = '%s: %s' % (type(e).__name__, e)
                r.pos, r.state = pstart, state0
                if r.record:
                    del r.plan[plan0:]
                    r.image[pstart:pstart + len(snap)] = snap
                r.advance(blen)
                val = None
        else:
            r.advance(blen)
            val = {'_unknown_block': bid}
        ok = (r.checksum() == r.state)
        blocks.append({'id': bid, 'length': blen, 'ok': ok, 'data': val, 'error': err})
        if not ok:
            break
    out['blocks'] = blocks
    out['block_map'] = {b['id']: b['data'] for b in blocks if b['data']}
    out['all_blocks_ok'] = all(b['ok'] for b in blocks)
    out['_reader'] = r
    return out


# ---------------- 物品 ----------------
def read_item(r, v11, kind='inv'):
    d = {'basename': r.string(), 'prefix': r.string(), 'suffix': r.string(),
         'modifier': r.string(), 'transmute': r.string()}
    d['seed'] = r.i32()
    d['relic_name'] = r.string()
    d['relic_bonus'] = r.string()
    d['relic_seed'] = r.i32()
    d['augment_name'] = r.string()
    d['unk'] = r.i32()
    d['augment_seed'] = r.i32()
    d['f_relic_comp'] = r.i32()
    if v11:
        d['f_a'] = r.i32()
        d['f_b'] = r.i32()
        d['qty'] = r.i32()
        d['f_c'] = r.i32()
        d['f_d'] = r.i32()
    else:
        d['qty'] = r.i32()
    if kind == 'equip':
        d['attached'] = r.bool()
    else:
        d['gx'] = r.i32()
        d['gy'] = r.i32()
    return d


def item_label(it):
    parts = [pretty(it['basename'])]
    if it['prefix']:
        parts.append('前缀 ' + pretty(it['prefix']).replace('前缀:', ''))
    if it['suffix']:
        parts.append('后缀 ' + pretty(it['suffix']).replace('后缀:', ''))
    if it.get('relic_name'):
        parts.append('镶嵌 ' + pretty(it['relic_name']).replace('材料:', ''))
    if it.get('augment_name'):
        parts.append('附魔 ' + pretty(it['augment_name']).replace('材料:', ''))
    return ' | '.join(x for x in parts if x)


# ---------------- 块 ----------------
def b1(r):
    b = {'version': r.i32()}
    v = b['version']
    b['in_main_quest'] = r.bool()
    b['has_been_in_game'] = r.bool()
    b['last_difficulty'] = r.byte()
    b['greatest_difficulty_completed'] = r.byte()
    b['_off_iron'] = r.pos                 # 可直接改档的字段偏移
    b['iron'] = r.i32()
    b['greatest_survival_difficulty'] = r.byte()
    b['_off_tributes'] = r.pos
    b['tributes'] = r.i32()
    b['ui_compass_state'] = r.byte()
    if 2 <= v <= 4:
        b['always_show_loot'] = r.i32()
    b['show_skill_help'] = r.bool()
    b['alt_weapon_set'] = r.bool()
    b['alt_weapon_set_enabled'] = r.bool()
    b['player_texture'] = r.string()
    if v >= 5:
        b['loot_filters'] = [r.byte() for _ in range(r.i32())]
    return b


def b2(r):
    b = {'version': r.i32()}
    b['_off'] = {}
    for k, kt in (('level_in_bio', 'i32'), ('experience', 'i32'),
                  ('attribute_points', 'i32'), ('skill_points', 'i32'),
                  ('devotion_points', 'i32'), ('total_devotion_points', 'i32'),
                  ('physique', 'f32'), ('cunning', 'f32'), ('spirit', 'f32'),
                  ('health', 'f32'), ('energy', 'f32')):
        b['_off'][k] = r.pos
        b[k] = r.i32() if kt == 'i32' else r.f32()
    return b


def b3(r):
    b = {'version': r.i32(), 'has_data': r.bool()}
    v11 = b['version'] >= 11
    if not b['has_data']:
        return b
    b['sack_count'] = r.i32()
    b['focused_sack'] = r.i32()
    b['selected_sack'] = r.i32()
    sacks = []
    sack_offs = []
    for _ in range(b['sack_count']):
        nid = r.i32()
        off_len = r.pos                    # 袋子长度字段位置（变长写入的锚点）
        nlen = r.len_field()
        ps = r.pos
        unused = r.bool()
        cnt = r.i32()
        items = [read_item(r, v11) for _ in range(cnt)]
        if r.pos - ps != nlen:
            raise ValueError('袋子长度不符 %d/%d' % (r.pos - ps, nlen))
        off_chk = r.pos                    # 袋子校验和位置（'chk' 段，编码时按新 state 重写）
        if r.checksum() != r.state:
            raise ValueError('袋子校验失败')
        sacks.append({'items': items, 'unused': unused})
        sack_offs.append({'nid': nid, 'off_len': off_len, 'off_chk': off_chk,
                          'nlen': nlen, 'ps': ps})
    b['sacks'] = sacks
    b['_sack_offs'] = sack_offs
    b['_off_equip'] = r.pos            # 装备区起点（变长写入的拼接锚点）
    b['use_alt_weaponset'] = r.bool()
    b['equipment'] = [read_item(r, v11, 'equip') for _ in range(12)]
    b['alt1_unused'] = r.bool()
    b['alt1'] = [read_item(r, v11, 'equip') for _ in range(2)]
    b['alt2_unused'] = r.bool()
    b['alt2'] = [read_item(r, v11, 'equip') for _ in range(2)]
    return b


def b4(r):
    b = {'version': r.i32(), 'stash_count': r.i32()}
    v11 = b['version'] >= 11
    st = []
    for _ in range(b['stash_count']):
        nid = r.i32()
        nlen = r.len_field()
        ps = r.pos
        w = r.i32(); h = r.i32(); cnt = r.i32()
        items = [read_item(r, v11) for _ in range(cnt)]
        footer = []
        left = nlen - (r.pos - ps)
        while left >= 4:                     # 尾部附加字段(GD 1.3 为 20 字节)
            footer.append(r.i32())
            left -= 4
        if left:
            r.advance(left)
        if r.pos - ps != nlen:
            raise ValueError('仓库长度不符 %d/%d' % (r.pos - ps, nlen))
        if r.checksum() != r.state:
            raise ValueError('仓库校验失败')
        st.append({'width': w, 'height': h, 'items': items, 'footer': footer})
    b['stashes'] = st
    return b


def b5(r):
    return {'version': r.i32(),
            'spawn_points': [[r.string('bytes', 16) for _ in range(r.i32())] for _ in range(3)],
            'current_respawn': [r.string('bytes', 16) for _ in range(3)]}


def b6(r):
    return {'version': r.i32(),
            'teleporter_points': [[r.string('bytes', 16) for _ in range(r.i32())] for _ in range(3)]}


def b7(r):
    return {'version': r.i32(),
            'markers': [[r.string('bytes', 16) for _ in range(r.i32())] for _ in range(3)]}


def _skill(r, new_layout):
    d = {'skill': r.string()}
    d['_off_level'] = r.pos
    d.update({'level': r.i32(), 'enabled': r.bool()})
    if new_layout:
        d['unk'] = r.bool()          # 块版本 >= 7 新增
    d.update({'devotion_level': r.i32(), 'devotion_exp': r.i32(), 'sublevel': r.i32(),
              'active': r.bool(), 'transition': r.bool(),
              'autocast': r.string(), 'autocast_controller': r.string()})
    return d


def b8(r):
    b = {'version': r.i32()}
    v = b['version']
    new_layout = v >= 7
    b['_off_skills'] = r.pos                 # 技能区起点（count 字段），变长写入用
    b['skills'] = [_skill(r, new_layout) for _ in range(r.i32())]
    b['_off_skills_end'] = r.pos             # 技能区终点（= masteries_allowed 起点）
    b['masteries_allowed'] = r.i32()
    b['skill_points_reclaimed'] = r.i32()
    b['devotion_points_reclaimed'] = r.i32()
    b['item_skills'] = [{'skill': r.string(), 'autocast': r.string(),
                         'autocast_controller': r.string(),
                         'unk_bytes': r.string('bytes', 4), 'unk': r.string()}
                        for _ in range(r.i32())]
    if v >= 6:
        b['unk1'] = r.i32()
    # 尾部：变长的「技能/控制器记录」字符串数组（一直占满到块尾）。
    # 实测（2026-09-15，48 级 _Sam）：狼人形态的两个内置攻击 + 绑定的虔诚技 +
    # 触发控制器（records/controllers/itemskills/cast_@enemyonattackcrit_100%.dbr）。
    # 老存档此处为空 → 循环不执行，行为不变。
    b['tail_records'] = []
    _guard = 0
    while getattr(r, 'block_end', None) and r.pos < r.block_end and _guard < 1000:
        b['tail_records'].append(r.string())
        _guard += 1
    return b


def b10(r):
    return {'version': r.i32(),
            'tokens_per_difficulty': [[r.string() for _ in range(r.i32())] for _ in range(3)]}


def b12(r):
    return {'version': r.i32(), 'lore_item_names': [r.string() for _ in range(r.i32())]}


def b13(r):
    return {'version': r.i32(), 'my_faction': r.i32(),
            'faction_values': [{'changed': r.bool(), 'unlocked': r.bool(),
                                'value': r.f32(), 'pos': r.f32(), 'neg': r.f32()}
                               for _ in range(r.i32())]}


def _hotslot(r):
    t = r.i32()
    if t == 0:
        return {'type': t, 'skill': r.string(), 'is_item_skill': r.bool(),
                'item': r.string(), 'equip_loc': r.i32()}
    if t == 4:
        return {'type': t, 'item': r.string(), 'up': r.string(),
                'down': r.string(), 'text': r.string('utf-16-le')}
    return {'type': t}


def b14(r):
    """快捷栏。实测结构：version, equipment_selection(bool), skill_window_selection(i32),
    skill_setting_valid(bool), 5×技能组, [v>=7: 3×i32], 变长槽位数组(直至末尾 8 字节),
    尾部 i32 + camera_distance(float)。"""
    b = {'version': r.i32()}
    v = b['version']
    b['equipment_selection'] = r.bool()
    b['skill_window_selection'] = r.i32()
    b['skill_setting_valid'] = r.bool()
    b['skill_sets'] = [{'primary': r.string(), 'secondary': r.string(),
                        'active': r.bool()} for _ in range(5)]
    if v >= 7:
        r.i32(); r.i32(); r.i32()
    end = r.block_end
    p0 = r.pos
    try:
        hs = []
        while r.pos < end - 8:
            hs.append(_hotslot(r))
        b['hotslots'] = hs
        b['tail_int'] = r.i32()
        b['camera_distance'] = r.f32()
        if r.pos != end:
            raise ValueError('快捷栏长度不符')
        b['hotslot_skills'] = [h['skill'] for h in hs if h['type'] == 0 and h.get('skill')]
    except Exception as e:
        b['_error'] = str(e)
        r.pos = p0
        r.advance(end - p0)
        b['hotslots'] = []
        b['hotslot_skills'] = []
    return b


def b15(r):
    return {'version': r.i32(), 'tutorials': [r.i32() for _ in range(r.i32())]}


def b16(r):
    b = {'version': r.i32()}
    v = b['version']
    for k in ('playtime_seconds', 'death_count', 'kill_count', 'exp_from_kills',
              'health_potions', 'energy_potions', 'max_level', 'hits_received',
              'hits_inflicted', 'crits_inflicted', 'crits_received'):
        b[k] = r.i32()
    b['greatest_damage_done'] = r.f32()
    b['greatest_monster_killed'] = [
        {'name': r.string(), 'level': r.i32(), 'life_mana': r.i32(),
         'last_hit': r.string(), 'last_hit_by': r.string()} for _ in range(3)]
    b['champion_kills'] = r.i32()
    b['last_hit_DA'] = r.f32()
    b['last_hit_OA'] = r.f32()
    b['greatest_damage_received'] = r.f32()
    for k in ('hero_kills', 'items_crafted', 'relics_crafted', 'tier2_relics',
              'tier3_relics', 'devotion_shrines', 'one_shot_chests', 'lore_notes'):
        b[k] = r.i32()
    b['boss_kills'] = [r.i32() for _ in range(3)]
    for k in ('survival_wave', 'survival_score', 'survival_defense', 'survival_powerup'):
        b[k] = r.i32()
    if v >= 11:
        b['skills_map'] = [{'skill': r.string(), 'level': r.i32()} for _ in range(r.i32())]
        b['endless_souls'] = r.i32()
        b['endless_essence'] = r.i32()
        b['difficulty_skip'] = r.byte()
    b['unique_items_found'] = r.i32()
    b['randomized_items_found'] = r.i32()
    if v >= 12:
        b['extra1'] = r.i32()
        b['extra2'] = r.i32()
    return b


def b17(r):
    return {'version': r.i32(),
            'shrines': [[r.string('bytes', 16) for _ in range(r.i32())] for _ in range(6)]}


SPEC = {1: b1, 2: b2, 3: b3, 4: b4, 5: b5, 6: b6, 7: b7, 8: b8, 10: b10,
        12: b12, 13: b13, 14: b14, 15: b15, 16: b16, 17: b17}


def parse(path, record=False):
    data = open(path, 'rb').read()
    r = Reader(data, record=record)
    if record:
        r.plan.append(('plain', 0, 4))       # 种子头 4 字节明文, 不参与变换
    out = parse_into(r)
    out['path'] = path
    out['seed'] = r.seed
    out['record'] = {'plan': r.plan, 'image': r.image} if record else None
    out.pop('_reader', None)
    return out


def load_blueprints(save_dir):
    """formulas.gst 是明文, 直接抓图纸路径"""
    p = os.path.join(save_dir, 'formulas.gst')
    if not os.path.exists(p):
        return []
    raw = open(p, 'rb').read()
    out, i = [], 0
    key = b'records/items/crafting/blueprints/'
    while True:
        j = raw.find(key, i)
        if j < 0:
            break
        k = j
        while k < len(raw) and 32 <= raw[k] < 127:
            k += 1
        out.append(raw[j:k].decode('ascii', 'replace'))
        i = k
    return out


if __name__ == '__main__':
    import sys
    d = parse(sys.argv[1])
    print(d['name'], d['classes'], d['level'], '块校验', d['all_blocks_ok'])
