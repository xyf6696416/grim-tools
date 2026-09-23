# -*- coding: utf-8 -*-
"""eval_build_variants.py —— lv73 终评：把各条优化轴的收益放在**同一个 harness** 里量。

为什么必须同一个 harness
------------------------
不同入口的口径**不可混比**（见 `docs/pitfalls.md` #52）：
  · **CLI 默认**（`gd dps` / `plan_dps`）：星座当「技能 %」算 ⇒ 少算平伤/攻速/独立倍率。
  · **显式星座通道**（本工具）：`levels` 去掉 `/devotion/` + 传 `devotion_levels`，更完整。
所以「+4.9%」「+2.3%」这类结论**只能在同一通道内相减**，跨通道相减会得到假增益。

口径
----
  · `面板` = `rep['dps']`；`含减抗` = `rep['vs']['dps_vs']`（与 `plan_dps.dps` 同源）。
  · `GD_PROJ_HITS=1`（保守口径，与历次普查一致）。
  · 技能集来自 `data/scratch/skill_swap.json`（`tools/tune_skills.py --mode swap`）；
    星座集来自 `data/scratch/devotion_opt.json`（`tools/tune_devotion.py`）。
    两者默认吃 scratch 的调优产物；没有就退回「不动」并明确提示。

用法
----
    python tools/eval_build_variants.py            # 现状 / 各轴 / 全上
    ARCH=werewolf python tools/eval_build_variants.py
"""
import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)
sys.path.insert(0, SKILL)
sys.path.insert(0, HERE)
os.environ.setdefault('GD_PROJ_HITS', '1')
# ★★ 2026-09-22 性能：优化器路径**关掉归因账本**（`slot_pct` / `slot_flat` ——
#   那是「伤害循环文档」的报告产物，却在每次评估里都记一遍）。
#   报告子进程会显式改回 `GD_ATTRIB=1`。
os.environ.setdefault('GD_ATTRIB', '0')

from gd import dps as D, rotation as R, procs as PR, skillprov as SP   # noqa: E402
from gd import rr as RR, enemy as ENM                                # noqa: E402
import objfunc as _OBJ                                                # noqa: E402

OBJ = _OBJ.mode_default()
# 目标不同 ⇒ 默认吃**不同**的方案文件（免得把总目标的方案套到穿刺目标上）
_SFX = '' if OBJ == 'total' else '_' + OBJ.rstrip('+')

EN = importlib.import_module('gd.enemy')
ARCH = os.environ.get('ARCH', 'wolf_nightblade_fast')
CHAR = os.environ.get('CHAR', 'Sam')
PLAN = os.environ.get('PLAN', 'data/plans/Sam_lv73_current.json')


def _gear_override():
    """`PLAN`（**显式设了环境变量才生效**）指定的方案作为**装备覆盖**。

    ★ 为什么要它：旧版 `tune_skills` / `eval_build_variants` **完全忽略 PLAN**，
      永远在**存档原装备**上算技能 —— 换流（例如「主穿刺」要换 5 件装备）时，
      装备按穿刺权重选、技能却在旧装备上评，两套假设互相矛盾，结论不可用。
    ⚠ 未设 `PLAN` 时**不覆盖** ⇒ 旧行为逐位不变（避免默认方案与存档路径产生微差）。
    """
    if not os.environ.get('PLAN'):
        return None
    p = os.path.join(SKILL, PLAN)
    if not os.path.isfile(p):
        return None
    try:
        import plan_dps as _PD
        return _PD.plan_to_override(json.load(open(p, encoding='utf-8'))) or None
    except Exception:                                            # noqa: BLE001
        return None


_C = D.load_char(CHAR, '', True, gear_override=_gear_override())
EFF_ALL = dict(_C['skills'])
for _k, _e in (_C.get('skill_plus') or {}).items():
    if _k in EFF_ALL:
        EFF_ALL[_k] += _e

# ★★ 敌方减抗（RR）与敌方档 —— **必须与装备搜索（`tools/plan_dps.py::dps_of`）同口径**。
#   旧版 `tune_skills` / `eval_build_variants` **没传 `rr=`** ⇒ 算的是「无减抗」口径，
#   而装备搜索走的是**含减抗**口径 ⇒ 两个阶段的目标函数不同源，结论会互相矛盾
#   （实测同一套装备：带 RR 穿刺桶 112,233 ／ 不带 RR 76,090，差 **47%**）。
#   ★★★ 2026-09-22（陷阱 #85）：rr **必须按每次试验的技能/星座等级现取** ——
#     模块级冻结会让「改减抗技能或减抗星位的等级」在评分里恒为 0。
#     `RRCache` 只在该变体真的动了减抗来源时重算，其余命中缓存（不拖慢）。
_RRC = RR.RRCache(_C['folded'], _C['db'])
_eprof = ENM.get_profile(os.environ.get('GD_ENEMY_PROFILE') or None,
                         _C.get('level') or 100)
_e_arm, _e_src = D.resolve_enemy_armor(os.environ.get('GD_ENEMY_ARMOR') or None, _eprof)
# ⚠ 这里**不含 rr** —— 由 `ev()` 按本次变体现取
RRKW = dict(enemy=_eprof, enemy_armor=_e_arm, enemy_armor_src=_e_src)


SK = {k: v for k, v in _C['skills'].items() if v > 0}
EFF_SAVE = dict(SK)
for _r, _e in (_C.get('skill_plus') or {}).items():
    if _r in EFF_SAVE:
        EFF_SAVE[_r] += _e
EFF_SAVE_NODEV = {k: v for k, v in EFF_SAVE.items() if '/devotion/' not in k}
CUR_DEV = {k: 1 for k in SK if '/devotion/' in k}

MAST = {}
for _r, _lv in SK.items():
    if '_classtraining_' in _r:
        MAST[os.path.basename(_r).replace('_classtraining_', '').replace('.dbr', '')] = _lv
for _cl, _ex in (_C.get('mastery_plus') or {}).items():
    MAST[_cl] = MAST.get(_cl, 0) + _ex
ATTRS = R.panel_attrs(_C.get('bio') or {}, MAST, _C.get('gear_flat') or {}, _C.get('gear_pct') or {})
APCT = R.attr_damage_pct(ATTRS)
EQ_SK = SP.norm_equipped([x[1] for x in (_C.get('item_skills') or [])])
WPS = PR.wps_pool(_C.get('base_gids'))
# ★★ 2026-09-22：武器构成（武器类型硬前提门控，见 `gd/procs.weapon_type_ok`）。
#   ⚠ 这里的 `_C` 可能带 `gear_override`（`--gear`）⇒ 武器构成按**override 后**算，
#     正是「换了装之后这个星座还能不能点」的正确答案。
WEAPON_ST = PR.weapon_state_of(_C)


def _load(rel):
    p = os.path.join(SKILL, rel)
    if not os.path.isfile(p):
        return None
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:                                            # noqa: BLE001
        return None


# ---- 技能重排（`skills` 里含星座条目，用的时候要剥掉）
_SKOPT = (_load(os.environ.get('SKILL_SRC') or 'data/scratch/skill_swap%s.json' % _SFX)
          or _load('data/scratch/skill_swap.json')
          or _load('data/scratch/skill_opt.json'))
SK_NEW = dict(_SKOPT['skills']) if (_SKOPT and _SKOPT.get('skills')) else dict(SK)
EFF_NEW_NODEV = {k: v for k, v in SK_NEW.items() if v > 0 and '/devotion/' not in k}
for _r, _e in (_C.get('skill_plus') or {}).items():       # 装备 +技能 照旧叠加
    if _r in EFF_NEW_NODEV:
        EFF_NEW_NODEV[_r] += _e

# ---- 星座重排
_DOPT = (_load(os.environ.get('DEV_SRC') or 'data/scratch/devotion_opt%s.json' % _SFX)
         or _load('data/scratch/devotion_opt.json'))
DEV_NEW = ({r: 1 for r in _DOPT['records']} if (_DOPT and _DOPT.get('records'))
           else dict(CUR_DEV))

# ---- 未分配属性点（投狡诈：给 OA + 穿刺/流血 %，比体格更增伤）
# ⚠ 别从 `C['bio']` 读：`load_char` 的 `bio` **不暴露** `attribute_points`
#   （实测取到 None ⇒ 整条属性轴恒为 0，看着「没收益」）。真源在存档 block2。
def _attr_points():
    try:
        from gd import _legacyenv as _E
        from gd.save import core as _S
        _sd, _ = _E.find_save_dir()
        _p = os.path.join(_sd, 'main', '_%s' % CHAR.lstrip('_'), 'player.gdc')
        return int(_S.parse(_p, record=False)['block_map'][2].get('attribute_points') or 0)
    except Exception:                                            # noqa: BLE001
        return 0


_AP = _attr_points()


def ev(eff_nodev=None, dev=None, ap_points=0, ap_to='cunning'):
    """→ (面板, 含减抗)。`ap_points` 加进 `attrs` 后再算 `attr_pct`。"""
    eff = dict(EFF_SAVE_NODEV if eff_nodev is None else eff_nodev)
    at = dict(ATTRS)
    if ap_points:
        at[ap_to] = float(at.get(ap_to) or 0) + 8.0 * ap_points
    rep = R.final_report(
        ARCH, eff, db=_C['db'], folded=_C['folded'], skill_records=list(eff),
        base_aps=_C.get('base_aps') or 1.25, attr_pct=R.attr_damage_pct(at),
        conversions=_C.get('conversions') or [], skill_mods=_C.get('skill_mods') or {},
        attrs=at, level=_C.get('level'),
        equipped_sk=EQ_SK, item_wps=WPS,
        # ★★★ 陷阱 #85：rr 按本变体的**技能 + 星座**等级现取（不是模块级冻结值）。
        rr=_RRC.pack(eff, dev),
        **RRKW,   # RRKW 里已含 enemy / 护甲
        devotion_levels=dev, weapon_st=WEAPON_ST)
    vs = (rep.get('vs') or {}).get('dps_vs') or rep['dps']
    return float(rep['dps']), float(vs), _OBJ.score(rep, OBJ)


def main():
    rows = [
        ('① 现状（存档原样）', None, CUR_DEV, 0),
        ('② + 技能重排', EFF_NEW_NODEV, CUR_DEV, 0),
        ('③ + 星座（狐狸 4 星）', None, DEV_NEW, 0),
        ('④ + 属性 %d 点→狡诈' % _AP, None, CUR_DEV, _AP),
        ('★ ⑤ 全上（推荐）', EFF_NEW_NODEV, DEV_NEW, _AP),
    ]
    print('形态 %s ｜ 角色 %s lv%s' % (ARCH, CHAR, _C.get('level')))
    print('评分目标 %s ｜ 技能源 %s ｜ 星座源 %s ｜ 装备 %s ｜ 未分配属性点 %d'
          % (_OBJ.label(OBJ),
             'skill_swap%s.json' % _SFX if _SKOPT else '存档',
             'devotion_opt%s.json' % _SFX if DEV_NEW != CUR_DEV else '存档',
             'PLAN 覆盖' if os.environ.get('PLAN') else '存档装备', _AP))
    print()
    print('%-26s %12s %12s %12s %8s %9s %8s'
          % ('变体', '面板', '含减抗', _OBJ.label(OBJ), '面板Δ', '含减抗Δ', '目标Δ'))
    print('-' * 96)
    out, base = [], None
    for tag, eff, dev, ap in rows:
        p, v, sc = ev(eff, dev, ap)
        if base is None:
            base = (p, v, sc)
        print('%-26s %12s %12s %12s %9.1f%% %10.1f%% %9.1f%%'
              % (tag, format(p, ',.0f'), format(v, ',.0f'), format(sc, ',.0f'),
                 p / base[0] * 100 - 100, v / base[1] * 100 - 100,
                 sc / base[2] * 100 - 100))
        out.append({'tag': tag, 'panel': p, 'vs': v, 'objective': sc})
    print()
    _diff = {r: SK_NEW.get(r, 0) - SK.get(r, 0) for r in set(SK_NEW) | set(SK)
             if SK_NEW.get(r, 0) != SK.get(r, 0)}
    print('★ 技能改动 %d 处：%s'
          % (len(_diff), '、'.join('%s %+d' % (r.rsplit('/', 1)[-1], d)
                                  for r, d in sorted(_diff.items())) or '（无）'))
    print('★ 星座改动：%d 星点（%s）'
          % (len(DEV_NEW), '与现状相同' if DEV_NEW == CUR_DEV else '已加入新星座'))
    json.dump({'arch': ARCH, 'rows': out, 'skill_diff': sorted(_diff.items()),
               'dev_n': len(DEV_NEW), 'ap_points': _AP},
              open(os.path.join(SKILL, 'data/scratch/lv73_final_eval.json'),
                   'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('已落 data/scratch/lv73_final_eval.json')


if __name__ == '__main__':
    main()
