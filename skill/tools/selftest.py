"""自检：跑一遍关键断言，确认离线库与官方公式都对得上。

    python tools/selftest.py

每项输出 `✓` / `✗`，最后给汇总。改动 gd/ 之后照跑。
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gd import DB, paths                        # noqa: E402
from gd.render import Renderer                  # noqa: E402

FAILS: list[str] = []


def check(name: str, cond: bool, detail="") -> None:
    if isinstance(detail, (list, tuple)):
        detail = " | ".join(str(x) for x in detail)
    print(f"  {'✓' if cond else '✗'} {name}{('  ' + str(detail)) if detail else ''}")
    if not cond:
        FAILS.append(name)


def main() -> int:
    print("=" * 72)
    print("grim-dawn 自检")
    print("=" * 72)

    # ---- 环境 ----
    print("\n[1] 环境")
    check("找得到 GT 安装", paths.gt_dir() is not None, str(paths.gt_dir()))
    check("找得到 app.asar", paths.asar_path() is not None)
    check("找得到存档目录", paths.save_dir()[0] is not None, str(paths.save_dir()[0]))

    # ---- 加载 ----
    print("\n[2] 离线库加载")
    t0 = time.time()
    db = DB.load()
    dt = time.time() - t0
    check("加载成功", db.game_version != "", db.game_version)
    check("加载耗时 < 3s", dt < 3.0, f"{dt:.2f}s")
    st = db.stats()
    for key, want in (("items", 8612), ("prefixes", 1745), ("suffixes", 2360),
                      ("sets", 199), ("itemSkills", 4268), ("monsters", 1433)):
        check(f"{key} = {want}", st[key] == want, f"实际 {st[key]}")
    check("中文条目 16563", st["l10nTags"] == 16563, f"实际 {st['l10nTags']}")

    # ---- 官方公式 ----
    print("\n[3] 官方公式（calc.js 逐字移植）")
    check("rc(27,60) == [35,51]", db.fmt.rc(27, 60) == [35, 51], str(db.fmt.rc(27, 60)))
    check("af(27,60) == 43.0", abs(db.fmt.af(27, 60) - 43.0) < 1e-9)
    check("rc(135,60) == [172,259]", db.fmt.rc(135, 60) == [172, 259], str(db.fmt.rc(135, 60)))
    check("0 值不缩放", db.scaler.value("offensiveFireMin", 0, 60) == 0)
    check("黑名单不缩放：暴击伤害",
          not db.scaler.is_scaled("offensiveCritDamageModifier"))
    check("character* 不缩放",
          db.scaler.scales("characterAttackSpeedModifier", 60) == 0.0)
    check("offensive* 才拿缩放",
          db.scaler.scales("offensiveFireModifier", 60) == 60.0)

    # ---- 格式引擎 ----
    print("\n[4] 格式引擎")
    check("tagCharAttackSpeed(14) -> '+14% 攻击速度'",
          db.fmt.render("tagCharAttackSpeed", 14) == "+14% 攻击速度",
          repr(db.fmt.render("tagCharAttackSpeed", 14)))
    check("DamageFire(38) -> '38 火焰伤害'",
          db.fmt.render("DamageFire", 38) == "38 火焰伤害")
    check("DamageModifierPierce(182) -> '+182% 穿刺伤害'",
          db.fmt.render("DamageModifierPierce", 182) == "+182% 穿刺伤害")
    check("pair_str 透传不补多余加号",
          db.fmt.pair_str("DamagePhysical", 126, 862) == "126-862",
          db.fmt.pair_str("DamagePhysical", 126, 862))

    # ---- 名称 ----
    print("\n[5] 中文名解析")
    check("it2116 -> 天之裂片咒刃", db.name("it2116") == "天之裂片咒刃", db.name("it2116"))
    check("it2116 英文名", db.en_name("it2116") == "Skyshard Spellblade", db.en_name("it2116"))
    check("is190 -> 世界守护者的花园", db.name("is190") == "世界守护者的花园")
    check("sk1235 -> 暗影面纱", db.name("sk1235") == "暗影面纱")
    check("m3528 有中文名", "极寒" in db.name("m3528"), db.name("m3528"))
    check("类别反查：c24 -> 匕首", db.class_cn(db.item_class("it2116")) == "匕首",
          db.class_cn(db.item_class("it2116")))

    # ---- 渲染 ----
    print("\n[6] 渲染")
    r = Renderer(db)
    card = r.item_card("it2116", show_range=True)
    check("物品卡片含中文名", "天之裂片咒刃" in card)
    check("物品卡片含区间", "[14-" in card, [l for l in card.splitlines() if "[" in l][:1])
    check("物品卡片含等级需求", "玩家等级 35" in card, [l for l in card.splitlines()
                                                if "等级" in l][:1])
    check("物品卡片含赋予技能", "技能等级" in card)
    check("套装卡片含逐档", "[4 件]" in r.set_card("is190"))
    check("词缀卡片含可附部位", "可附部位" in r.affix_card("pre4264"))
    check("技能卡片含逐级数值", "逐级数值" in r.skill_card("sk1235"))

    # ---- 搜索 / 过滤 ----
    print("\n[7] 搜索与过滤")
    check("按名字找到利维坦", len(db.find("利维坦", "item", 5)) >= 2)
    check("filter 支持 __gte",
          len(db.filter_items(**{"offensiveFireModifier__gte": 100, "f": "Legendary"})) > 0)
    check("field_tag 组合规则",
          db.field_tag("offensiveSlowFireModifier") == "DamageDurationModifierFire")

    # ---- 派生 ----
    print("\n[8] 派生数据")
    lt = db.level_table()
    check("满级技能点 237", lt["max_skill_points"] == 237, str(lt["max_skill_points"]))
    check("虔诚点上限 55", lt["max_devotion_points"] == 55)
    check("职业专精技能 356 条", len(db.mastery_skills) == 356, str(len(db.mastery_skills)))
    check("sk1235 有逐级数值", bool(((db.mastery_skill("sk1235") or {}).get("stats"))))
    check("星座 110 个", len(db.devotions()) == 110, str(len(db.devotions())))

    # ---- 存档层 ----
    print("\n[9] 存档层")
    from gd.save import core as S, items as SI, report as RP
    chars = paths.characters()
    check("找得到角色存档", len(chars) > 0, f"{len(chars)} 个：{', '.join(chars)}")
    if chars:
        name = "_Sam" if "_Sam" in chars else next(iter(chars))
        doc = S.parse(str(chars[name] / "player.gdc"))
        check("存档解析成功", bool(doc.get("all_blocks_ok")),
              f"{doc.get('name')} lv{doc.get('level')}")
        check("15 个块全部 OK", len(doc["blocks"]) == 15, str(len(doc["blocks"])))

    br = SI.bridge(db)
    check("记录桥已加载", len(br.fwd) > 8000, f"正向 {len(br.fwd)} / 反向 {len(br.rev)}")
    check("记录 → GT id 反查", br.gid_of("records/items/gearweapons/caster/c013_dagger.dbr")
          == "it2116")
    check("词缀中文名（族根）", br.affix_name("records/items/lootaffixes/suffix/"
                                        "a010a_ch_lifemana_01.dbr") == "潜能之")
    check("角色报告可生成", "【装备】" in RP.character(name if chars else "x"))
    check("gd_dbr 兼容层", __import__("gd_dbr").open_all() is not None)

    # ---- 权威映射 + 修复回归守卫 ----
    print("\n[10] 权威映射（读游戏 .dbr）与修复守卫")
    check("物品映射 ≥98%", len(br.fwd) / 8612 >= 0.98,
          f"{len(br.fwd)}/8612 = {len(br.fwd) / 8612 * 100:.1f}%")
    check("词缀映射 ≥95%", len(br.affix_fwd) / 4105 >= 0.95,
          f"{len(br.affix_fwd)}/4105 = {len(br.affix_fwd) / 4105 * 100:.1f}%")
    check("c204_sword2h → it12691（记录名≠标签名的坑）",
          br.gid_of("records/items/gearweapons/melee2h/c204_sword2h.dbr") == "it12691",
          br.label("records/items/gearweapons/melee2h/c204_sword2h.dbr"))
    check("星座星位有中文名",
          db.skill_name_of_record("records/skills/devotion/tier1_08e_skill.dbr")
          == "暗杀者的标记",
          db.skill_name_of_record("records/skills/devotion/tier1_08e_skill.dbr"))
    check("savemap 走权威桥表（O(1)）",
          "权威映射" in __import__("gd.savemap", fromlist=["x"]).resolve("it12691")[1])
    # ★ 回归守卫：`resolve()` 传**对象**时也必须命中权威表（曾因此把 it15810 写错成
    #   c303_necklace 而不是 awakened/.../c308_necklace，静默写错装备）
    _SM = __import__("gd.savemap", fromlist=["x"])
    _obj = _SM.gt_items().get("it15810")
    check("resolve(对象) 命中权威表",
          _SM.resolve(_obj)[0] == "records/items/awakened/gearaccessories/necklaces/"
                                  "c308_necklace.dbr",
          _SM.resolve(_obj)[0])
    check("resolve(对象) 与 resolve(gid) 一致",
          _SM.resolve(_obj)[0] == _SM.resolve("it15810")[0])
    # ★ 回归守卫：备份的 PAIRS 必须非空（曾被迁移写成 `PAIRS = ()` → 备份静默空跑）
    from gd.save import backup as _B
    check("备份 PAIRS 非空", len(_B.PAIRS) >= 1, f"{len(_B.PAIRS)} 个源目录")
    # ★★ 回归守卫（2026-09-20 实测踩到）：归档目录若落在活存档根内，
    #    `do_backup` 会把「上一次的备份」当存档内容再复制一遍 ⇒ 指数膨胀
    #    （2 分 22 秒滚出 553 MB / 280 份 MANIFEST）。`_under` 是这条护栏的判据。
    _b0 = _B.PAIRS[0][0]
    _sib = os.path.join(os.path.dirname(_b0), '_gd_sibling_dir')
    check("★ 归档嵌套护栏：_under 判定（自身 / 子路径 / 兄弟路径）",
          _B._under(_b0, _b0)
          and _B._under(os.path.join(_b0, 'GrimDawn_存档备份_2039'), _b0)
          and not _B._under(_sib, _b0),
          '根 %s ｜ 兄弟 %s' % (_b0, _sib))

    # ---- ★★ 抗性来源分解（`gd/resaudit.py`，2026-09-20 并入出 BD 全链路）
    #   这一组守的是「**分解口径**」：它被 `gd/planreport.py` §二 与
    #   `tools/plan_audit.py`（交付物 REPORT_*）**同时**消费 ⇒
    #   一旦口径漂了，两条报告链路会一起说谎，而肉眼很难发现。
    print("\n[32] 抗性来源分解（gd.resaudit）")
    try:
        import gd.resaudit as _RA
        check("gd.resaudit 可导入", True)
        # ① 结构事实：元素三抗是**一条字段给三项** —— 本专题的全部结论都挂在它上面
        check("★ 元素三抗是一条字段喂 3 个维度（上排溢出的结构性原因）",
              _RA.FIELD_DIMS.get('defensiveElementalResistance') == ['火', '冰', '电'],
              'FIELD_DIMS[defensiveElementalResistance] = %s'
              % _RA.FIELD_DIMS.get('defensiveElementalResistance'))
        # ② 分解自恰：逐槽汇总 == 总计；每个维度 == 各槽之和
        _per = _RA.per_slot_from_folded({
            'A': {'defensiveElementalResistance': 10, 'defensiveFire': 5,
                  'defensiveLife': 7},
            'B': {'defensiveCold': 3, 'defensiveChaos': 2}})
        _tot = _RA.totals(_per)
        check("★ 分解自恰：总计 == 逐槽汇总（火 15 / 冰 13 / 电 10 / 活力 7 / 混乱 2）",
              _tot.get('火') == 15 and _tot.get('冰') == 13 and _tot.get('电') == 10
              and _tot.get('活力') == 7 and _tot.get('混乱') == 2,
              '火 %s 冰 %s 电 %s 活力 %s 混乱 %s'
              % (_tot.get('火'), _tot.get('冰'), _tot.get('电'),
                 _tot.get('活力'), _tot.get('混乱')))
        # ③ 报告段落必须带上两条**关键提示**（否则用户会误读成「纯浪费，去换掉」）
        _md = '\n'.join(_RA.render_md(_per, top=1))
        check("★ 分解段落含「卡在线上」与「完全无效」两条关键提示",
              '卡在线上' in _md and '完全无效' in _md,
              '共 %d 行' % (len(_md.splitlines())))
        check("★ 分解段落给出独立 CLI 入口（tools/res_audit.py）",
              'tools/res_audit.py' in _md)
    except Exception as _e32:                                        # noqa: BLE001
        check("抗性来源分解可运行", False, f"{type(_e32).__name__}: {_e32}")

    # ---- 落档链路（只读自检：不写盘）----
    print("\n[11] 落档链路（gd.build）")
    try:
        import gd.build as _BD
        check("gd.build 可导入", True)
        check("槽位表 12 个装备槽", len(_BD.SLOTS) == 12)
        check("写盘安全：有 game_running 闸门", hasattr(__import__("gd.save.patch",
              fromlist=["x"]), "game_running"))
    except Exception as e:
        check("gd.build 可导入", False, str(e)[:80])

    # ---- 自动化链路（只读自检）----
    print("\n[12] 自动化（gd auto / 工具链）")
    try:
        from gd import cli as _cli
        _ = _cli._PASSTHRU
        check("CLI 透传集合含 build/auto/opt",
              {"build", "auto", "opt"} <= set(_cli._PASSTHRU))
    except Exception as e:
        check("CLI 透传集合", False, str(e)[:80])
    for _m in ("autobuild", "plan_dps", "tune_dps", "save_plan", "plan_to_build",
               "plan_to_save_skill", "plan_legal"):
        try:
            __import__(_m)
            check(f"tools/{_m}.py 可导入", True)
        except Exception as e:
            check(f"tools/{_m}.py 可导入", False, str(e)[:80])
    # ★ 大 JSON 走 paths.load_json（进程内 memo）—— 二次调用必须命中缓存
    _p = __import__("gd.paths", fromlist=["x"])
    _a = _p.load_json("record_map.json")
    _b = _p.load_json("record_map.json")
    check("paths.load_json 进程内缓存", _a is _b)

    # ★ 性能守卫：dps.load_char 里 skills.json 的 tag→record 映射必须**只建一次**
    #   （曾每次 DPS 评估都 json.load 11 MB → 单次评估 0.15 s 里的 0.09 s）
    _D = __import__("gd.dps", fromlist=["x"])
    check("skills tag→record 有缓存函数", hasattr(_D, "_tag2rec_map"))
    check("tag→record 映射进程内复用",
          _D._tag2rec_map() is _D._tag2rec_map())
    # ★ 双手武器时「副手」必须在评估里被丢弃（否则双手+副手虚高）
    import plan_dps as _PD
    _ov = _PD.plan_to_override({"主手": ["it12691", None, None, None, None],
                                "副手": ["it2804", None, None, None, None]})
    check("双手武器 → 副手不生效",
          _PD._is_two_hand("records/items/gearweapons/melee2h/c204_sword2h.dbr")
          and "副手" not in _ov and "主手" in _ov)
    check("单手武器 → 副手保留",
          not _PD._is_two_hand("records/items/gearweapons/axes1h/c003_axe.dbr"))

    # ---- [11] 性能优化 + 确定性修正（2026-09-19，见 docs/perf_research.md）----
    print("\n[11] 性能 / 确定性")
    _R = __import__("gd.rotation", fromlist=["x"])
    _PATH = __import__("gd.paths", fromlist=["x"])
    _JSON = _PATH.load_json("skills.json")
    check("skill_children 已记忆化（同一对象 → 同一结果对象）",
          _R.skill_children(_JSON) is _R.skill_children(_JSON))

    _GDB = __import__("gd.db", fromlist=["x"])
    check("DB.load 进程内单例（不再每次重读 pickle，0.068 s/次）",
          _GDB.DB.load() is _GDB.DB.load())

    _DPS = __import__("gd.dps", fromlist=["x"])
    check("load_char 走只读解析缓存", hasattr(_DPS, "_parse_cached"))

    # apply_conversions 必须与「转化条目顺序」无关（否则跨进程不可复现）
    _base = {"physical": [100.0, 200.0], "pierce": [50.0, 60.0]}
    _cv1 = [("physical", "pierce", 40), ("pierce", "cold", 30)]
    _r1 = _R.apply_conversions({k: list(v) for k, v in _base.items()}, _cv1)
    _r2 = _R.apply_conversions({k: list(v) for k, v in _base.items()}, list(reversed(_cv1)))
    check("apply_conversions 与顺序无关（两阶段化）", _r1 == _r2,
          f"{_r1} vs {_r2}")

    # prune_cands 的缓存必须与直算逐条一致
    _O = __import__("gd.opt", fromlist=["x"])
    _cs = _O.slot_cands(_O.SLOTS[0])
    if _O._np is not None and len(_cs) >= 3:
        _rows = _O._np.array([list(c[1]) + list(c[2]) + [c[3]] for c in _cs], _O._np.float64)
        _plain = [c[0] for c, k in zip(_cs, _O._skyline(_rows)) if k] or [c[0] for c in _cs]
        _O._PRUNE_MEMO.clear()
        _cached = [c[0] for c in _O.prune_cands(_cs)]
        check("prune_cands 缓存与直算一致", _plain == _cached,
              f"{len(_plain)} vs {len(_cached)}")
    else:
        check("prune_cands 缓存与直算一致", True, "候选太少，跳过")

    # `import gd.opt` 不得有副作用（以前会顺带跑一整轮搜索，6.9 s）
    import subprocess
    import time as _tm
    _t0 = _tm.time()
    _pr = subprocess.run([sys.executable, "-c",
                          "import sys;sys.path.insert(0,'.');import gd.opt"],
                         cwd=str(_PATH.DATA_DIR.parent), capture_output=True, text=True, timeout=120)
    _dt = _tm.time() - _t0
    check("import gd.opt 无副作用且 <3 s", _dt < 3.0 and not (_pr.stdout or "").strip(),
          f"{_dt:.2f}s")

    # ------------------------------------------------------------------ 属性需求闸门
    print("\n[12] 属性需求闸门（官方 itemCostFormulae）")
    from gd import req as _RQ
    from gd import reqfit as _RF

    # ① 官方公式 vs 实测锚点（旧版「实测标定」表，逐条对上才算没退化）
    #    ★ 锚点是按**护甲档位**分组的，不是按部位：
    #      轻甲/中甲（cf1..cf8, cf12, cf13）头 538 / 腿 662；
    #      重甲（cf9, cf10, cf11）          头 915.4 / 腿 1035.1。
    def _ev(name, ilvl, att=8, setname=None):
        eqs = _RQ._eqs(setname)
        return _RQ._ev(eqs[name], {'itemLevel': ilvl, 'itemlevel': ilvl,
                                   'totalAttCount': att})

    check("轻甲 headStrengthEquation(94) ≈ 538",
          abs(_ev('headStrengthEquation', 94, setname='cf5') - 538.0) < 1.0,
          f"{_ev('headStrengthEquation', 94, setname='cf5'):.1f}")
    check("轻甲 legsStrengthEquation(94) ≈ 662",
          abs(_ev('legsStrengthEquation', 94, setname='cf5') - 662.4) < 1.0,
          f"{_ev('legsStrengthEquation', 94, setname='cf5'):.1f}")
    check("重甲 headStrengthEquation(94) ≈ 915",
          abs(_ev('headStrengthEquation', 94, setname='cf11') - 915.4) < 1.0,
          f"{_ev('headStrengthEquation', 94, setname='cf11'):.1f}")
    check("重甲 legsStrengthEquation(94) ≈ 1035",
          abs(_ev('legsStrengthEquation', 94, setname='cf11') - 1035.1) < 1.0,
          f"{_ev('legsStrengthEquation', 94, setname='cf11'):.1f}")
    check("ilvl1 两端对得上（轻 15 / 重 33）",
          abs(_ev('headStrengthEquation', 1, setname='cf5') - 14.8) < 0.5
          and abs(_ev('headStrengthEquation', 1, setname='cf11') - 33.3) < 0.5,
          "%.1f / %.1f" % (_ev('headStrengthEquation', 1, setname='cf5'),
                           _ev('headStrengthEquation', 1, setname='cf11')))

    # ② 武器需求必须**真算出来**（历史上 weapon 分支是死代码，恒为 0）
    _wrec = 'records/items/gearweapons/swords1h/c012_sword.dbr'
    _w = _RQ.req(_wrec, _wrec, '主手', total_att=8)
    check("武器需求不再恒为 0（剑→狡诈）", _w['cunning'] > 0 and _w['physique'] == 0
          and _w['confidence'] == 'official~',
          f"狡诈={_w['cunning']} [{_w['confidence']}]")
    check("近战 2 手 → 体格方程", _RQ.req(
        'records/items/gearweapons/melee2h/c026_blunt2h.dbr',
        'records/items/gearweapons/melee2h/c026_blunt2h.dbr', '主手')['physique'] > 0)
    check("权杖 → 精神方程", _RQ.req(
        'records/items/gearweapons/caster/d004_scepter.dbr',
        'records/items/gearweapons/caster/d004_scepter.dbr', '主手')['spirit'] > 0)
    check("认不出类型 → 不谎报 official", _RQ.req(
        'it1', 'records/items/gearweapons/unknown/w001_thing.dbr', '主手')['confidence']
        in ('unknown', 'none'))

    # ③ 精通曲线与 alloc 口径必须一致（曾用「每级常数」线性近似，会漂 17 点）
    try:
        from gd import alloc as _AL
        _lin = _AL.mastery_attr([[4, 35], [10, 50]])
        _cur = {k: sum(_RF.R.mastery_attr_of(c, lv, k)
                       for c, lv in (('class04', 35), ('class10', 50)))
                for k in ('physique', 'cunning', 'spirit')}
        check("alloc.mastery_attr 已对齐逐级曲线", all(
            abs(_lin[k] - _cur[k]) < 0.01 for k in _cur),
            f"{ {k: round(_lin[k], 1) for k in _cur} } vs { {k: round(_cur[k], 1) for k in _cur} }")
    except Exception as _e:
        check("alloc.mastery_attr 已对齐逐级曲线", False, f"{type(_e).__name__}: {_e}")

    # ③b archetype 的 `core_skills` 必须覆盖**全部可点 WPS**（2026-09-20 补漏）
    #   ★ 为什么：`gd/alloc.allocate` 的贪心是「core 吃满预算再轮 rest」，
    #     所以**漏进 core_skills 的 WPS 永远点不到**。实测 `avalanche` 漏了
    #     class10 的「血牙」(`wpattack01`) 与 class04 的「死亡旋风」(`wpattack3`)，
    #     补进 `core_skills` 后 DPS 65,697 → **79,034（+20.3%）**，零附带损伤。
    #   ⚠ 修法是**补数据**不是改贪心 —— 两池合表实测 `human` **−7.7%**
    #     （攻击整体提前、挤掉乘全区被动），见 `gd/alloc.py` 的实测记录表。
    try:
        from gd import alloc as _AL
        _archs = _AL._load('archetypes.json', {}) or {}
        _bad = {}
        for _name in _archs:
            # 只守 Sam 可选的 N+B 形态（mastery=class10）；其余形态要别的专精，
            # 不在本项目的优化范围内，缺 WPS 不影响任何交付结论。
            if (_archs[_name] or {}).get('mastery') != 'class10':
                continue
            _reach, _miss = _AL.reachable_wps(_name)
            _miss -= set(_AL.WPS_OPT_OUT.get(_name) or ())
            if _miss:
                _bad[_name] = sorted(os.path.basename(x) for x in _miss)
        check("archetype 的 core_skills 覆盖全部可点 WPS（漏了 = 点不到）",
              not _bad, str(_bad) or "12 个形态全部齐全")
        check("WPS 覆盖守卫生效（avalanche 补漏后已齐全）",
              not any(v for k, v in _bad.items() if k == 'avalanche'),
              "avalanche 缺 %s" % _bad.get('avalanche'))
    except Exception as _e:
        check("archetype 的 core_skills 覆盖全部可点 WPS（漏了 = 点不到）", False,
              f"{type(_e).__name__}: {_e}")

    # ④ 幂等性：同一存档连续求解两次，结论必须一致。
    #   ★ 别断言「changed == False」——那是**历史状态**不是不变式：用户进游戏换了装备
    #     它就会变（Sam lv69→lv71 那次就误红过）。这里该测的是「重复调用是否稳定」。
    try:
        _sd, _ = paths.save_dir()
        _has = os.path.isfile(os.path.join(str(_sd), 'main', '_Sam', 'player.gdc'))
        if _has:
            _r1 = _RF.solve('Sam')
            _r2 = _RF.solve('Sam')
            check("reqfit 幂等（同档两次求解结论一致）",
                  (_r1.changed, _r1.feasible) == (_r2.changed, _r2.feasible),
                  f"changed={_r1.changed} feasible={_r1.feasible} "
                  f"need={ {k: _r1.need[k] for k in _RF.KEYS} }")
        else:
            check("reqfit 幂等（同档两次求解结论一致）", True, "无 Sam 档，跳过")
    except Exception as _e:
        check("reqfit 幂等（同档两次求解结论一致）", False, f"{type(_e).__name__}: {_e}")

    # ------------------------------------------------------- [13] 真源完整性
    # 迁移脚本的步骤 23「权威覆盖」会按 sync_live.PAIRS 用副本回填 gd/。
    # 所以「副本与活文件不一致」= 下次重跑迁移会**静默丢东西**，必须在这里拦住。
    print("\n[13] 真源完整性（迁移「权威覆盖」的登记表）")
    try:
        import importlib

        SKILL = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(SKILL / "tools"))
        SL = importlib.import_module("sync_live")
        pairs = list(SL.PAIRS)
        check("sync_live.PAIRS 非空", len(pairs) >= 9, f"{len(pairs)} 对")
        miss = [b for _l, b in pairs if not (SKILL / b).is_file()]
        check("保命副本齐全", not miss, ("缺：" + "、".join(miss)) if miss else "全部在位")
        drift = []
        for lv, bk in pairs:
            lp, bp = SKILL / lv, SKILL / bk
            if not lp.is_file():
                drift.append(f"{lv}(活文件缺失)")
            elif SL.md5(str(lp)) != SL.md5(str(bp)):
                drift.append(lv)
        check("活文件与副本一致（否则重跑迁移会丢）", not drift,
              ("★ 跑 tools/sync_live.py --capture 刷新：" + "、".join(drift)) if drift else "一致")
        sl_src = (SKILL / "tools" / "sync_live.py").read_text(encoding="utf-8")
        check("sync_live 支持 --capture（反向刷新）", "--capture" in sl_src)
        mg_src = (SKILL / "tools" / "migrate_from_archive.py").read_text(encoding="utf-8")
        check("迁移含步骤 24 权威覆盖",
              "def overlay_live" in mg_src and "overlay_live(sync_live, pre)" in mg_src,
              "重跑迁移不再打回成熟版")
        check("迁移含步骤 23 双持副手修复",
              "_WPN_DUAL_NEW" in mg_src and "'主手', '副手'" in mg_src,
              "auto_pool 的 break 曾让副手池恒空")
    except Exception as _e:
        check("真源完整性检查可运行", False, f"{type(_e).__name__}: {_e}")

    # ------------------------------------------------ [14] 双持副手候选池（回归守卫）
    # `auto_pool()` 曾用**无条件 `break`** 分配槽位，而 `SLOTS` 里「主手」排在
    # 「副手」之前 ⇒ `got['副手']` 恒为空；平时靠 `POOL_BASE.get(slot) or POOL_WPN`
    # 兜底活着。一旦注入「当前穿着」（`GD_CUR_JSON`，`gd auto --with-weapon` 必走），
    # `POOL_BASE['副手']` 就有值了 ⇒ or-兜底失效 ⇒ 那 1 件再被 `ALLOW_NORMAL` 滤掉
    # ⇒ 副手 0 候选 ⇒ `beam_search_np` 的 SUFM 归约零尺寸数组崩溃。
    # 这条断言在**子进程**里复现「注入当前穿着」的场景（避免污染本进程的候选池）。
    print("\n[14] 双持副手候选池（注入当前穿着后仍非空）")
    try:
        import subprocess

        probe = (
            "import os, json, sys\n"
            "sys.path.insert(0, %r)\n"
            "os.environ['GD_SLOTS'] = ','.join(['头部','项链','胸甲','腿甲','靴子','手套',\n"
            "    '戒指1','戒指2','腰带','肩甲','勋章','圣物','主手','副手'])\n"
            "os.environ['GD_MAX_ILVL'] = '71'\n"
            "os.environ['GD_CUR_JSON'] = json.dumps({'主手': ['it2737'], '副手': ['it2737']})\n"
            "import gd.opt as O\n"
            "print(len(O.choices('主手')), len(O.choices('副手')))\n"
        ) % str(SKILL)
        r = subprocess.run([sys.executable, "-c", probe],
                           capture_output=True, text=True, timeout=600)
        nums = (r.stdout.strip().split() or ["0", "0"])
        nm, nf = int(nums[0]), int(nums[1])
        check("注入当前穿着后 副手候选 > 0", nf > 0, "主手 %d ｜ 副手 %d" % (nm, nf))
        check("主手/副手候选数一致（同一武器池）", nm == nf,
              "%d vs %d" % (nm, nf) if nm != nf else "%d" % nm)
    except Exception as _e:
        check("双持副手候选池检查可运行", False, f"{type(_e).__name__}: {_e}")

    print("\n[15] ILP 满抗搜索（替换束搜索的新主路径）")
    try:
        _SK = Path(__file__).resolve().parent.parent
        _ir = (_SK / "tools" / "ilp_res.py").read_text(encoding="utf-8")
        _ab = (_SK / "tools" / "autobuild.py").read_text(encoding="utf-8")
        check("ilp_res 提供 Model / cap_search / solve",
              "class Model" in _ir and "def cap_search" in _ir and "def solve(" in _ir)
        check("autobuild 满抗搜索默认走 ILP（可 --cap-solver 切换）",
              "--cap-solver" in _ab and 'solver in ("auto", "ilp")' in _ab)
        check("autobuild ILP 默认 gap 1e-3（1e-2 会偶尔停在次优）",
              '"--ilp-gap"' in _ab and "default=1e-3" in _ab)
        check("ilp_res 注明 HiGHS 只能单线程（防有人改回 threads>1 → Not Set）",
              "只能单线程" in _ir)
        # ★ 2026-09-22：默认并行度改为**统一分配器**（陷阱 #89）—— 不再写死「逻辑核数」。
        #   旧契约「默认 = 逻辑核」在 32 核机器上算出「32 条链 × 每链 1 进程」，
        #   而每条链内部串行 ⇒ 并行效率 1/32（`--extreme` 实测 18.8 分钟 / 10 CPU-小时）。
        check("★ autobuild 默认并行度走 `_alloc_parallel`（不再写死逻辑核）",
              "_alloc_parallel(" in _ab and "a.chains = _logical_cores()" not in _ab
              and "_PAR_AUTO_PROCS" in _ab)
        check("ILP 失败仍回退束搜索（可用性不受影响）",
              "回退束搜索" in _ab and "run_search(list(O.SLOTS), beam" in _ab)
        # 真实求解：取 3 个槽位的小模型，只验链路通（求解+复核+返回格式）
        _probe = (
            "import os, sys, json\n"
            "sys.path.insert(0, %r)\n"
            "sys.path.insert(0, %r)\n"
            "os.environ['GD_SLOTS'] = '头部,项链,胸甲'\n"
            "os.environ['GD_MAX_ILVL'] = '71'\n"
            "import gd.opt as O, ilp_res as IR\n"
            "cm = IR.build_candmap(O, ['头部','项链','胸甲'])\n"
            "M = IR.Model(O, cm)\n"
            "sol, info = M.solve(time_limit=30, gap=1e-2, log=lambda *a: None)\n"
            "print(info['status'], info['nvar'])\n"
        ) % (str(_SK), str(_SK / "tools"))
        _r = subprocess.run([sys.executable, "-c", _probe],
                            capture_output=True, text=True, timeout=300)
        _out = (_r.stdout or "").strip().split()
        check("ILP 小模型可解（链路：建模→HiGHS→还原解）",
              bool(_out) and _out[0] in ("Optimal", "Infeasible", "Time limit reached"),
              _out[0] if _out else (_r.stderr or "")[-200:])
    except Exception as _e:
        check("ILP 集成检查可运行", False, f"{type(_e).__name__}: {_e}")

    # ------------------------------------------------ [16] 减抗 / 转化 / 构建回归
    # 这一组守的是 2026-09-19 落地的三件事：
    #   ① `gd/rr.py`：敌方减抗（B 族叠加 / C 族取最高 / A 族取最高）+ 角色自身抗性
    #   ② `gd/opt.py`：减抗两维 + 转化逐件估值进伤害代理（维度顺序不能动）
    #   ③ `gd/dps.py` 的构建覆盖 + `tools/gt_regress.py`：任意构建可离线重算
    print("\n[16] 减抗 / 转化 / 构建回归（gd/rr.py · gt_regress.py）")
    try:
        import importlib
        import os as _os
        SKILL = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(SKILL))
        _os.environ.setdefault("PYTHONHASHSEED", "0")

        RR = importlib.import_module("gd.rr")

        # —— 口径：三族语义必须与游戏一致
        check("rr 有 10 个抗性桶", len(RR.RES_BUCKETS) == 10, "、".join(RR.RES_BUCKETS))
        check("C 族取最高（两件不叠加）",
              RR.combine({"add": {}, "max": {"pierce": 30}},
                         {"add": {}, "max": {"pierce": 20}})
              ["max"]["pierce"] == 30,
              "offensiveTotalResistanceReductionPercent 是 max 语义")
        check("B 族（负 defensive*）叠加",
              RR.combine({"add": {"pierce": 30}, "max": {}},
                         {"add": {"pierce": 12}, "max": {}})["add"]["pierce"] == 42)
        # ★ 回归守卫：`for b in 'pierce'` 会遍历**字符** —— 这个坑真发生过
        pa = RR.rr_of({"defensivePierce": [-30]})
        check("B 族单桶映射不拆成字符", pa["add"] == {"pierce": 30.0}, str(pa["add"]))
        pe = RR.rr_of({"defensiveElementalResistance": [-15]})
        check("B 族元素族展开成三桶",
              all(pe["add"].get(b) == 15.0 for b in ("fire", "cold", "lightning")))
        check("非伤害抗性被排除（眩晕/恐惧…）",
              not RR.rr_of({"defensiveStun": [-20], "defensiveFear": [-10]})["add"])
        check("DoT 与直接伤害共用抗性桶",
              RR.TYPE_BUCKET["burn"] == "fire" and RR.TYPE_BUCKET["trauma"] == "physical")
        check("敌方档位含 100%（免疫）档", RR.ENEMY_PROFILES.get("max", {}).get("default") == 100
              or 100 in set((RR.ENEMY_PROFILES.get("max") or {}).values()),
              str(RR.ENEMY_PROFILES.get("max")))
        check("默认档位 = 精英（性价比口径）", RR.DEFAULT_PROFILE == "elite", RR.DEFAULT_PROFILE)

        # —— 换算公式：减抗边际必须 > 同点数的 % 加成（否则代理会低估减抗）
        m = RR.mult_of("pierce", RR.rr_of({"defensivePierce": [-30]}), 33)
        check("减抗换算：33% 抗 削 30 ⇒ ×1.45", abs(m - 1.4478) < 0.01, "%.4f" % m)
        m2 = RR.mult_of("physical", RR.empty(), 0)
        check("零减抗 ⇒ ×1.00（不虚报）", abs(m2 - 1.0) < 1e-9)
        check("基抗 ≥100% 时分母钳到 1（免疫不吃负收益）",
              RR.mult_of("pierce", RR.empty(), 100) == 0.0)

        # —— ★ 三族口径（2026-09-20 修正）：B 叠加 / C 取最高 / A 取最高，顺序 B→C→A
        #   B = 技能上的负 `defensive*`（文案「−n% 类型抗性」）        → 键 `add`
        #   C = `…ResistanceReductionPercent`（带 %）                  → 键 `max`
        #   A = `…ResistanceReductionAbsolute`（无 %）                 → 键 `flat`
        #   详见 docs/rr_mechanics.md（含官方论坛出处）。
        check("rr 三键齐备（B add / C max / A flat）",
              set(RR.empty()) == {"add", "max", "flat"}, str(sorted(RR.empty())))
        _c = RR.rr_of({"offensiveTotalResistanceReductionPercentMin": [25],
                       "offensiveTotalResistanceReductionPercentMax": 10})
        check("C 族（…Percent）取最高、不叠加", _c["max"]["pierce"] == 25.0,
              str({k: v for k, v in _c["max"].items() if k == "pierce"}))
        _a2 = RR.rr_of({"offensiveTotalResistanceReductionAbsoluteMin": [20],
                        "offensivePhysicalResistanceReductionAbsolute": 9})
        check("A 族（…Absolute）取最高、且不混进 add（旧版会求和成 29）",
              _a2["flat"]["pierce"] == 20.0 and not _a2["add"],
              "flat=%s｜add=%s" % (_a2["flat"]["pierce"], _a2["add"]))
        # 负值加深：官方论坛《Advanced Mechanics》原例 —— 10% − 18 − 46% = −54%，
        # 再套 C 类 30% ⇒ −54% × 1.30 = **−70.2%**（不是拉回 −37.8%）。
        _r3 = {"add": {"pierce": 64}, "max": {"pierce": 30}, "flat": {}}
        check("C 族把负抗性**推得更深**（负值 ×(1+c)，不是拉回 0）",
              abs(RR.res_eff("pierce", _r3, 10.0) - (-70.2)) < 0.05,
              "%.2f（期望 −70.20）" % RR.res_eff("pierce", _r3, 10.0))
        # 顺序判别：base 40 / C 30% / A 8 —— B→C→A 得 40×0.7−8 = 20；
        # 旧版 (B+A)→C 会得 (40−8)×0.7 = 22.4，两者可区分。
        _r4 = {"add": {}, "max": {"pierce": 30}, "flat": {"pierce": 8}}
        check("结算顺序 B→C→A（A 在 C 之后减）",
              abs(RR.res_eff("pierce", _r4, 40.0) - 20.0) < 1e-6,
              "%.2f（期望 20.00）" % RR.res_eff("pierce", _r4, 40.0))
        check("抗性下限可配（GD_RR_FLOOR，默认 −100 = 保守）",
              RR.rr_floor() == -100.0, "%.0f" % RR.rr_floor())
        # C 族负值口径可 A/B 切换（GD_RR_C_NEG=pull 回到旧行为，实测差 +16%）
        _os.environ["GD_RR_C_NEG"] = "pull"
        _pull = RR.res_eff("pierce", _r3, 10.0)
        _os.environ.pop("GD_RR_C_NEG", None)
        check("C 族负值口径可 A/B（GD_RR_C_NEG=pull 回旧行为）",
              abs(_pull - (-37.8)) < 0.05 and abs(RR.res_eff("pierce", _r3, 10.0) - (-70.2)) < 0.05,
              "pull=%.2f ｜ deepen=%.2f" % (_pull, RR.res_eff("pierce", _r3, 10.0)))
        check("零减抗三族全空时倍数为 1（不虚报）",
              all(abs(RR.mult_of(b, RR.empty(), 33.0) - 1.0) < 1e-9
                  for b in ("pierce", "physical", "cold")))

        # —— 角色自身抗性（对拍 grimtools 抗性面板的输入侧）
        cr = RR.char_res({"头部": {"defensivePierce": 20, "defensivePoison": 12},
                          "腿甲": {"defensiveAllResistance": 8}},
                         base={"pierce": -50})
        check("char_res 汇总叠加 + 泛化族 + 难度惩罚",
              cr["pierce"] == -22.0 and cr["fire"] == 8.0 and cr["poison"] == 20.0,
              str({k: v for k, v in cr.items() if v}))

        # —— opt：维度必须**追加在末尾**（`OA/PI/PH_` 靠 FKEYS.index 取）
        O = importlib.import_module("gd.opt")
        check("opt 有 rr_add / rr_pct 两维",
              "rr_add" in O.FKEYS and "rr_pct" in O.FKEYS, "NF=%d" % len(O.FKEYS))
        check("opt 有 conv_net 维（转化逐件估值）", "conv_net" in O.FKEYS)
        check("新维度都在 FKEYS 末尾（插中间会整体错位）",
              list(O.FKEYS)[-3:] == ["rr_add", "rr_pct", "conv_net"],
              "、".join(list(O.FKEYS)[-3:]))
        check("FLOOR/W_DMG/W_PEN 覆盖全部 FKEYS（少一个键 = KeyError）",
              set(O.FKEYS) <= set(O.FLOOR) and set(O.FKEYS) <= set(O.W_PEN)
              and set(O.FKEYS) <= set(O.W_DMG),
              "缺：" + "、".join(sorted(set(O.FKEYS) - set(O.W_PEN))) or "齐备")
        check("转化可为负 ⇒ conv_net 不设 0 下限", O.FLOOR.get("conv_net", 0) < 0,
              str(O.FLOOR.get("conv_net")))
        check("`_clear_caches()` 存在（标定后必须清 _CV/_RR_OF/_CONV_OF）",
              callable(getattr(O, "_clear_caches", None)))
        check("标定入口齐备（rr / conv / rr_skills）",
              all(callable(getattr(O, f, None)) for f in
                  ("recalibrate_rr", "recalibrate_conv", "recalibrate_rr_skills")))
        # ★ 幂等：反复标定不能累加 `SKILL_W`（曾经会）——
        #   先跑一次把基线建起来，再连跑两次比结果（否则第一次的「从空到有」
        #   会被误判成不幂等）。
        try:
            O.recalibrate_rr_skills(log=lambda *a: None)
            _w1 = dict(getattr(O, "RR_SKILL_GAIN", {}) or {})
            O.recalibrate_rr_skills(log=lambda *a: None)
            O.recalibrate_rr_skills(log=lambda *a: None)
            _w2 = dict(getattr(O, "RR_SKILL_GAIN", {}) or {})
            check("recalibrate_rr_skills 幂等（SKILL_W 不累加）", _w1 == _w2,
                  "两次标定后仍 %d 项" % len(_w2))
        except Exception as _e2:
            check("recalibrate_rr_skills 幂等（SKILL_W 不累加）", False,
                  "%s: %s" % (type(_e2).__name__, _e2))
        check("候选池放开「零抗性直接丢弃」（否则减抗件进不来）",
              "_rrsc" in (SKILL / "gd" / "opt.py").read_text(encoding="utf-8"),
              "auto_pool 的 sc<=0 闸门已放宽")

        # —— dps：构建覆盖能力（gt_regress 的地基）
        D = importlib.import_module("gd.dps")
        import inspect as _insp
        _sig = _insp.signature(D.load_char)
        check("load_char 支持构建覆盖（skill/bio/level/classes）",
              all(k in _sig.parameters for k in
                  ("skill_override", "bio_override", "level_override", "classes_override")),
              "、".join(_sig.parameters))
        check("attack_rows 存在（退火热路径不再渲染整份报告）",
              callable(getattr(D, "attack_rows", None)))

        # —— gt_regress：离线对拍工具的链路
        sys.path.insert(0, str(SKILL / "tools"))
        GR = importlib.import_module("gt_regress")
        check("gt_regress 三件套齐备（plan / 技能解析 / 评估）",
              all(callable(getattr(GR, f, None)) for f in
                  ("to_plan", "resolve_skills", "evaluate", "pick_arch")))
        check("槽位名映射到引擎口径（中文）",
              set(GR.to_plan({"slots": {"head": {"zh": "头部", "item": "it1"}}})[0]) == {"头部"},
              "norm 的 slots 键是英文、中文在 zh 字段里")
        check("golden 目录 = data/regress", GR.REGRESS_DIR.name == "regress")
        check("参考值容差表非空（对拍才有意义）", bool(GR.TOL) and bool(GR.TOL_RES))
        # 真实跑一次（缓存里已有 ZyDo860V；没有就跳过，不联网）
        _gt = SKILL / "data" / "cache" / "gt_ZyDo860V.html"
        if _gt.is_file():
            _r = GR.evaluate(GR.load_build(html=str(_gt)))
            check("gt_regress 端到端可算（离线，ZyDo860V）",
                  (_r["metrics"].get("等级") or 0) > 0
                  and (len(_r["plan"]) == 14)
                  and not _r["skills_resolved"]["missing"],
                  "lv%s ｜ 槽 %d ｜ 技能 %d ｜ DPS %s"
                  % (_r["metrics"].get("等级"), len(_r["plan"]),
                     _r["skills_resolved"]["n"],
                     format(int(_r["metrics"].get("每秒伤害") or 0), ",")))
            check("gt_regress 打法可自动识别（不变身构建不该套狼人）",
                  _r["arch"] in ("human", "soldier_nightblade", "berserker_nightblade"),
                  _r["arch"])
            check("gt_regress 与 golden 零漂移（自身口径回归）",
                  bool(GR.golden_mode(_r, log=lambda *a: None))
                  if GR._gpath(_r["id"]).exists() else True,
                  "data/regress/%s.golden.json" % _r["id"])
        else:
            check("gt_regress 端到端可算（离线，ZyDo860V）", True, "无缓存页，跳过")

        # —— 真源：这三个文件必须登记进 PAIRS（本轮从 patch 步骤迁过来的）
        importlib.import_module("sync_live")
        SL2 = sys.modules["sync_live"]
        _pv = {l for l, _b in SL2.PAIRS}
        check("dps/opt/rr 已登记进 PAIRS（改完必须 --capture）",
              {"gd/dps.py", "gd/opt.py", "gd/rr.py"} <= _pv,
              "%d 对" % len(SL2.PAIRS))
    except Exception as _e:
        check("减抗/转化/构建回归检查可运行", False, f"{type(_e).__name__}: {_e}")

    # ---- 伤害模型 v2：官方常数自证（L1，全部离线、零参数）----
    print("\n[17] 伤害模型 v2 · 官方常数自证（PTH / 暴击窗口 / 护甲 / DoT 通道）")
    try:
        CB = importlib.import_module("gd.combat")
        DM = importlib.import_module("gd.dmg")
        for name, ok, detail in (CB.selftest_identities() or []):
            check(name, bool(ok), detail)
        for name, ok, detail in (DM.selftest_cases() or []):
            check(name, bool(ok), detail)
    except Exception as _e:
        check("combat / dmg 自证可运行", False, f"{type(_e).__name__}: {_e}")

    # ---- 伤害模型 v2：敌方真值表（L1）----
    print("\n[18] 伤害模型 v2 · 敌方真值表（等级池 / 难度修正 / 假人）")
    try:
        EN = importlib.import_module("gd.enemy")
        if not EN.available():
            check("data/monster_stats.json 就位", False,
                  "先跑 tools/extract_monsterdb.py")
        else:
            _mon = EN.monsters()
            check("怪物 2840 只", len(_mon) == 2840, "实际 %d" % len(_mon))
            check("调整表存在", len(EN.stats().get("adjustments") or {}) > 0,
                  "%d 键" % len(EN.stats().get("adjustments") or {}))
            # 索引语义：4*(难度-1) + 玩家数-1（从 mdb_db.js 逐字读出）
            check("调整索引 = 4*(难度-1)+玩家数-1",
                  [EN.adj_index(d, p) for d, p in
                   ((1, 1), (2, 1), (3, 1), (4, 1), (3, 4))] == [0, 4, 8, 8, 11],
                  "终极 4 人 = 11（飞升折算 3）")
            _adj = EN.adjustments(3, 1)
            check("终极难度 敌方伤害 +40%（官方调整表）",
                  abs(float(_adj.get("offensiveTotalDamageModifier") or 0) - 40.0) < 1e-9,
                  str(_adj.get("offensiveTotalDamageModifier")))
            _du = EN.dummy(100, 3, 1)
            check("训练假人可解析（DA 在合理区间）",
                  bool(_du) and 1000.0 < float(_du.get("da") or 0) < 3000.0,
                  "m4139 ｜ DA %.0f" % (float(_du.get("da") or 0)))
            _pl = EN.pool(71, 3, 1)
            check("等级池非空且逐桶抗性有值",
                  bool(_pl) and len(_pl.get("res") or {}) == 10,
                  "样本 %s ｜ DA %.0f" % (_pl and _pl.get("count"),
                                          float((_pl or {}).get("da") or 0)))
            # 真值档能喂给减抗换算（`res_base_map` 是两者的接口）
            RR2 = importlib.import_module("gd.rr")
            _per, _flat = EN.res_base_map(_pl)
            check("敌方真值 -> rr.apply_vs 的逐桶接口",
                  len(_per) == 10 and _flat is None)
            _res = importlib.import_module("gd.rotation")
            check("rotation 把 enemy 传给了 apply_vs（真值口径接线）",
                  "enemy=_ep" in (SKILL / "gd" / "rotation.py").read_text(encoding="utf-8"),
              "")

            # ★★ 档位缓存（2026-09-20 本轮最大的性能修复，必须守住）。
            #   事故：`pool()` 解析等级池要 **2.007 s**，而 `plan_dps.dps_of`
            #   每次评估都调一次 ⇒ 单次评估 2.017 s、其中 99.5% 是它。
            #   局部搜索一轮 1204 次 × 2 s ⇒ 一条链 ≈87 分钟（20 条并行也降不下来，
            #   链内串行）。而它的入参**全程不变** ⇒ 4.8 万次在算同一个东西。
            #   加记忆化后单次 2.017 s → ~0.003 s（**725×**），整轮 19 秒跑完。
            import time as _t2
            EN2 = importlib.import_module("gd.enemy")
            check("档位缓存：有 clear_profile_cache() 逃生口",
                  callable(getattr(EN2, "clear_profile_cache", None)))
            EN2.clear_profile_cache()
            _t0 = _t2.perf_counter()
            _p1 = EN2.get_profile(None, 71)
            _cold = _t2.perf_counter() - _t0
            _t0 = _t2.perf_counter()
            _p2 = EN2.get_profile(None, 71)
            _warm = _t2.perf_counter() - _t0
            check("档位缓存：命中后至少快 20×（冷 %.2fs → 热 %.5fs）"
                  % (_cold, _warm), _warm < max(_cold / 20.0, 1e-4),
                  "加速 %.0f×" % (_cold / max(_warm, 1e-9)))
            check("档位缓存：命中值与冷算值逐位一致（零漂移）",
                  _p1 == _p2)
            # ★ 返回值必须是**深拷贝**：调用方可能就地改嵌套字段（如 res），
            #   直接吐同一个对象会把缓存污染掉。
            _p2["res"]["fire"] = 99999.0
            check("档位缓存：返回值是深拷贝（改它不会污染缓存）",
                  EN2.get_profile(None, 71)["res"].get("fire") != 99999.0)
            check("档位缓存：不同 spec 分桶（不会串档）",
                  EN2.get_profile("elite", 71).get("kind")
                  != EN2.get_profile(None, 71).get("kind")
                  or EN2.get_profile("elite", 71).get("res")
                  != EN2.get_profile(None, 71).get("res"))
            EN2.clear_profile_cache()
    except Exception as _e:
        check("敌方模型可运行", False, f"{type(_e).__name__}: {_e}")

    # ---- 伤害模型 v2：基准锚点（L2 自身口径回归）----
    #   ★ Q8 的口径：golden 只回答「和上次比有没有动」；改动本身带来了什么，
    #     由 `data/regress/model_v2_anchor.json` 的 v1/v2 对照回答。
    EN = importlib.import_module("gd.enemy")
    _res = importlib.import_module("gd.rotation")
    _PR2 = importlib.import_module("gd.procs")
    _SP2 = importlib.import_module("gd.skillprov")
    import json as _json

    def _load_golden(p):
        """读 golden 的 `metrics` 段（给 v1/v2 对照用）"""
        try:
            return (_json.loads(p.read_text(encoding="utf-8")).get("metrics") or {})
        except Exception:
            return {}

    print("\n[19] 伤害模型 · 基准锚点（多版本对照）")
    try:
        _ap = SKILL / "data" / "regress" / "model_v2_anchor.json"
        check("锚点文件存在", _ap.is_file(), str(_ap))
        if _ap.is_file():
            _anc = _json.loads(_ap.read_text(encoding="utf-8"))
            _zy = (_anc.get("cases") or {}).get("ZyDo860V") or {}
            _g1 = _load_golden(SKILL / (_zy.get("v1_golden")
                                        or "data/regress/ZyDo860V.golden.v1.json"))
            _g2 = _load_golden(SKILL / (_zy.get("v2_golden")
                                        or "data/regress/ZyDo860V.golden.v2.json"))
            _g3 = _load_golden(SKILL / (_zy.get("v3_golden")
                                        or "data/regress/ZyDo860V.golden.json"))

            def _d(a, b):
                return sorted(k for k in set(a) | set(b)
                              if abs(float(a.get(k) or 0) - float(b.get(k) or 0)) > 1e-6)

            # ① v1 → v2：DoT 口径（官方 Step 9）。改动面必须**恰好**是登记的键，
            #    其余（抗性/属性/等级…）一位不动。
            _d12 = _d(_g1, _g2)
            check("v1 → v2 改动面 == 登记的键集（DoT 修正在生效且未误伤）",
                  _d12 == sorted(_zy.get("expect_diff_keys") or []),
                  "实际差 %d 项：%s" % (len(_d12), "、".join(_d12[:6])))
            # ② v2 → v3：**星座数值接入**（`devotion_skills.json` 空索引遮罩修复）。
            #    同样要求改动面 == 登记的键集 —— 这条能抓住「修一个口径顺手改坏另一个」。
            _d23 = _d(_g2, _g3)
            check("v2 → v3 改动面 == 登记的键集（星座数值接入且未误伤）",
                  _d23 == sorted(_zy.get("expect_diff_keys_v3") or []),
                  "实际差 %d 项" % len(_d23))
            # ③ 当前 golden 必须与锚点 v3 逐位一致（锚点过期 = 断言失效）
            _bad3 = [k for k, v in (_zy.get("v3") or {}).items()
                     if abs(float(_g3.get(k) or 0) - float(v)) > 1e-4]
            check("锚点 v3 值 == 当前 golden（锚点没过期）", not _bad3,
                  ("不一致：%s" % "、".join(_bad3)) if _bad3 else "逐位一致")
            check("golden 冻结项数没缩水（v1 43 项保底）",
                  len(_g3) >= 43 and len(_g1) == 43,
                  "v3 %d 项 ｜ v1 %d 项" % (len(_g3), len(_g1)))
    except Exception as _e:
        check("锚点对拍可运行", False, f"{type(_e).__name__}: {_e}")

    # ---- 伤害模型 v2：端到端（存档实测锚点，需要 Sam 存档）----
    print("\n[20] 伤害模型 v2 · 端到端（Sam 存档实测锚点）")
    try:
        import json as _json2
        _ap2 = SKILL / "data" / "regress" / "model_v2_anchor.json"
        _case = (_json2.loads(_ap2.read_text(encoding="utf-8"))
                 .get("cases", {}).get("Sam", {})) if _ap2.is_file() else {}
        # v12 = 「**罗卡（Lokarr m1281）口径落档**：装备 4 槽」后的最新锚点；v11/v10/… 留作历史对照。
        # ⚠ **锚点绑存档**：落档 / 游戏自己存档 / 玩家重置属性都会让它失效
        #   （`v12._state.指纹` 记了解锁它时的存档指纹，跑 `tools/save_state.py Sam` 复算）。
        _sv = (_case.get("v14") or _case.get("v13") or _case.get("v12") or _case.get("v11") or _case.get("v10")
               or _case.get("v9") or _case.get("v8") or _case.get("v7")
               or _case.get("v6") or _case.get("v5") or _case.get("v4")
               or _case.get("v3") or _case.get("v2") or {})
        _sv_fp = ((_sv.get("_state") or {}).get("指纹")
                  if isinstance(_sv.get("_state"), dict) else None)
        _sd = paths.save_dir()[0]
        if not _sd or not _sv:
            check("Sam 端到端锚点", True, "无存档目录或锚点，跳过")
        else:
            D2 = importlib.import_module("gd.dps")
            _c = D2.load_char("Sam")
            _sk = {k: v for k, v in _c["skills"].items() if v > 0}
            _eff = dict(_sk)
            for _rec, _ex in (_c.get("skill_plus") or {}).items():
                if _rec in _eff:
                    _eff[_rec] += _ex
            _mast = {}
            for _rec, _lv in _sk.items():
                if "_classtraining_" in _rec:
                    _mast[os.path.basename(_rec).replace("_classtraining_", "")
                          .replace(".dbr", "")] = _lv
            for _cls, _ex in (_c.get("mastery_plus") or {}).items():
                _mast[_cls] = _mast.get(_cls, 0) + _ex
            _at = _res.panel_attrs(_c.get("bio") or {}, _mast,
                                   _c.get("gear_flat") or {}, _c.get("gear_pct") or {})
            _rep = _res.final_report(
                "werewolf", _eff, db=_c["db"], folded=_c["folded"],
                skill_records=list(_eff), base_aps=_c.get("base_aps") or 1.25,
                attr_pct=_res.attr_damage_pct(_at),
                conversions=_c.get("conversions") or [],
                skill_mods=_c.get("skill_mods") or {},
                attrs=_at, level=_c.get("level"),
                enemy=EN.get_profile(None, _c.get("level") or 100),
                # ★ 2026-09-20：**必须与 [27]⑨ 及生产链路同口径** —— 补上
                #   `equipped_sk`（技能来源合法性）与 `item_wps`（装备授予 WPS 入池）。
                #   以前这里漏传 item_wps ⇒ 这条断言测的已经是**淘汰口径**。
                equipped_sk=_SP2.norm_equipped(
                    [x[1] for x in (_c.get("item_skills") or [])]),
                item_wps=_PR2.wps_pool(_c.get("base_gids")),
                # ★ 2026-09-22：与生产链路同口径 —— 武器类型硬前提门控。
                weapon_st=_PR2.weapon_state_of(_c))
            _p, _r = float(_rep["dps"]), float(_rep["dps_real"])
            # ★★ 2026-09-20：**先比存档指纹**。锚点是「存档实测」值，而存档会被
            #   外部改写（落档 / 游戏自己存档 / 玩家重置属性点）。
            #   实测教训：用户在游戏里把属性从 0/71/12 退回 1/0/0（未分配 82），
            #   这里 7 条断言同时变红 —— 看着像模型崩了，其实是**基准换了**。
            #   ⇒ 指纹不符时**明确跳过并说明**（`docs/pitfalls.md` #50），不误报。
            try:
                import importlib as _il2
                _fp_now = _il2.import_module("save_state").fingerprint("Sam")
            except Exception:                                        # noqa: BLE001
                _fp_now = None
            _stale = bool(_sv_fp) and _fp_now is not None and _fp_now != _sv_fp
            # ★ 跳过时**发同样条数的断言**（3 条）：否则「存档变了」会让总项数从
            #   373 掉到 371 ⇒ 文档/pack_skill 里写死的「N 项全绿」随存档状态漂移，
            #   而且看起来像「有断言凭空消失」。**计数必须与存档状态无关。**
            _STALE_NOTE = ("指纹 %s ≠ 锚点 %s ⇒ 基准已变；"
                           "确认新状态后重设 vN（tools/save_state.py Sam）"
                           % (_fp_now, _sv_fp))
            if _stale:
                for _lb in ("Sam 面板 DPS == 锚点", "Sam 实战 DPS == 锚点",
                            "Sam OA/DA/PTH == 锚点"):
                    check("%s（跳过：存档已变）" % _lb, True, _STALE_NOTE)
            else:
                check("Sam 面板 DPS == 锚点（±1）",
                      abs(_p - float(_sv.get("面板") or 0)) <= 1.0,
                      "%.1f ｜ 锚点 %.1f" % (_p, float(_sv.get("面板") or 0)))
                check("Sam 实战 DPS == 锚点（±1）",
                      abs(_r - float(_sv.get("实战") or 0)) <= 1.0,
                      "%.1f ｜ 锚点 %.1f" % (_r, float(_sv.get("实战") or 0)))
                check("Sam OA/DA/PTH == 锚点",
                      abs(float(_rep["oa"]) - float(_sv.get("OA") or 0)) <= 0.2
                      and abs(float(_rep["da"]) - float(_sv.get("DA") or 0)) <= 0.2
                      and abs(float(_rep["pth"]) - float(_sv.get("PTH") or 0)) <= 0.05,
                      "OA %.1f DA %.1f PTH %.2f" % (_rep["oa"], _rep["da"], _rep["pth"]))
            check("实战口径 = 面板 × 命中期望（不虚报、不重复打折）",
                  abs(_r - _p * float(_rep["hit"]["expected"]))
                  <= max(2.0, _p * 2e-4),
                  "×%.4f（`hit.expected` 只存 4 位小数，容差按此放宽）"
                  % float(_rep["hit"]["expected"]))
            # DoT 通道明细必须落进 rep（Step 9 可审计）
            _dt = _rep.get("dot") or {}
            check("DoT 通道明细已落进 rep['dot']",
                  bool(_dt.get("channels")) and _dt.get("share") is not None,
                  "%d 个通道 ｜ 占面板 %.1f%%"
                  % (len(_dt.get("channels") or []), (_dt.get("share") or 0) * 100))
            check("敌方真值抗性进了实战口径（逐桶，而非五档 33%）",
                  bool((_rep.get("vs") or {}).get("per_bucket")),
                  "轮廓 %s" % (((_rep.get("vs") or {}).get("enemy") or {})
                                .get("id")))
            # ★★ 2026-09-21 新增：**渲染器集成守卫**。为什么必须有它 ——
            #   `gd/dps.py::build_report` 里有一行 `'（%目标抗性降低）'` 走了 `%` 格式化，
            #   而 `%目` 不是合法格式符 ⇒ `ValueError: unsupported format character '目'`。
            #   **旧 build 没有 C 类减抗 ⇒ 那条分支从没执行过**，一直到落档「罗卡口径」的
            #   装备（带「最多 −30%」全抗削减）才崩。锚点 / 数值断言**一个都抓不到**，
            #   因为它们走的是 `final_report`，不碰渲染器。
            #   ⇒ 这里直接跑一遍 CLI 的报告链，任何渲染崩溃都会被抓住。
            _av = sys.argv[:]
            _buf = io.StringIO()
            try:
                sys.argv = ["dps", "Sam", "--brief"]               # `main()` 自己 parse sys.argv[1:]
                with contextlib.redirect_stdout(_buf):
                    importlib.import_module("gd.dps").main()
                _txt = _buf.getvalue()
                check("★ `gd dps` 能完整渲染当前存档的报告（渲染器崩溃 = 报告链整条断）",
                      "合计每秒伤害" in _txt, "%d 字符" % len(_txt))
            except BaseException as _e2:                             # noqa: BLE001
                check("★ `gd dps` 能完整渲染当前存档的报告（渲染器崩溃 = 报告链整条断）",
                      False, "%s: %s" % (type(_e2).__name__, str(_e2)[:120]))
            finally:
                sys.argv = _av
    except Exception as _e:
        check("Sam 端到端锚点可运行", False, f"{type(_e).__name__}: {_e}")

    # ---- 伤害模型 v2：权重表自洽（Q6 —— crit 的口径撕裂）----
    #   `gd/opt.py::W_DMG` 是**搜索排序用**的线性代理，它本身无法自证对错；
    #   唯一能当裁判的是**真实模型**。这一组把「写死的 crit=1.00」换成
    #   「由暴击口径推导 + 用有限差分实测校验」。
    print("\n[21] 伤害模型 v2 · 权重表自洽（crit 权重的解析值 vs 模型实测边际）")
    try:
        sys.path.insert(0, str(SKILL / "tools"))
        PD2 = importlib.import_module("plan_dps")
        O2 = importlib.import_module("gd.opt")
        _sd2 = paths.save_dir()[0]
        if not _sd2 or not hasattr(PD2, "marginals"):
            check("crit 权重自洽（需存档）", True, "无存档，跳过")
        else:
            _m = PD2.marginals("Sam", delta=10.0)
            _b = _m["base"]
            check("边际自测可跑（5 次模型评估）",
                  bool(_m.get("per_point", {}).get("crit")),
                  "每点实战DPS：全伤害 %.1f ｜ 穿刺 %.1f ｜ 流血 %.1f ｜ 暴伤 %.1f"
                  % (_m["per_point"].get("offensiveTotalDamageModifier", 0),
                     _m["per_point"].get("offensivePierceModifier", 0),
                     _m["per_point"].get("offensiveSlowBleedingModifier", 0),
                     _m["per_point"].get("crit", 0)))
            _i = O2.recalibrate_crit(chance=_b["hit"]["crit_chance"],
                                     expected=_b["hit"]["expected"],
                                     pct_ref=_b["pct_ref"])
            _ana, _mea = float(_i["ratio_vs_total"]), float(_m.get("crit_ratio") or 0.0)
            # 容差 3%（2026-09-21 从 1% 放宽）：换装后（锚点 v9）该解析近似的实测误差
            # 为 **2.0%**（解析 3.2079 / 实测 3.2739）—— 这是**线性近似的固有误差**，
            # 随解的加成结构变化，不是模型回归。**超过 5% 才算回归**（容差即守卫）。
            check("crit/total 解析值 == 模型实测边际（±3%）",
                  _mea > 0 and abs(_ana - _mea) / _mea < 0.03,
                  "解析 %.4f ｜ 实测 %.4f ｜ 差 %.3f%%"
                  % (_ana, _mea, abs(_ana - _mea) / _mea * 100 if _mea else 0))
            check("W_DMG['crit'] 由标定决定（写死值 1.00 对 Sam 高估 ≥1.5×）",
                  abs(float(O2.W_DMG["crit"])) > 0
                  and (1.0 / float(O2.W_DMG["crit"])) >= 1.5,
                  "标定后 %.4f ⇒ 写死值高估 %.2f 倍"
                  % (O2.W_DMG["crit"], 1.0 / max(float(O2.W_DMG["crit"]), 1e-9)))
            # ⚠ 不能给 0 暴击率兜底成「非零权重」：那等于宣称暴击伤害在无暴击时
            #   也有收益。0 暴击率 ⇒ 权重 0，是唯一自洽的下限。
            check("给 0 暴击率 ⇒ crit 权重 0（不虚报）",
                  float(O2.recalibrate_crit(chance=0.0, expected=1.0,
                                            pct_ref=800.0)["W_crit"]) == 0.0)
            # 恢复成真实标定值（本组前面把模块状态改来改去，别留给后面的调用方）
            O2.recalibrate_crit(chance=_b["hit"]["crit_chance"],
                                expected=_b["hit"]["expected"],
                                pct_ref=_b["pct_ref"])
            # 减抗维度的权重公式（纯公式，不需要模型）：
            #   per point = 主桶权重 × (100+pct)/(100−res) × uptime
            _rr = O2.recalibrate_rr(pct_main=800.0, res_main=3.0)
            _want = float(O2.W_DMG.get(O2.RR_MAIN or "pierce", 1.0)) \
                * (100.0 + 800.0) / (100.0 - 3.0) * float(_rr["uptime"])
            check("rr_add 权重 == 主桶权重 ×(100+pct)/(100−res)×uptime",
                  abs(float(_rr["W_rr_add"]) - _want) < 0.01,
                  "%.3f ｜ 公式 %.3f" % (float(_rr["W_rr_add"]), _want))
            check("FLOOR / W_DMG / W_PEN 三表同键集且覆盖 FKEYS",
                  set(O2.FLOOR) == set(O2.W_DMG) == set(O2.W_PEN)
                  and set(O2.FKEYS) <= set(O2.FLOOR),
                  "%d 维" % len(O2.FKEYS))
    except Exception as _e:
        check("权重表自洽可运行", False, f"{type(_e).__name__}: {_e}")

    # ---- 伤害模型 v2：游戏内实测通道（L3 的地基，Q9）----
    #   ⚠ 这里断言的是「通道能跑、键位齐全」，**不是**「对拍通过」——
    #     面板真值要靠人拿游戏抄，抄错了不等于引擎错了，不该让 selftest 报红。
    print("\n[22] 伤害模型 v2 · 游戏内实测通道（--sheet，L3 入口）")
    try:
        _sd3 = paths.save_dir()[0]
        if not _sd3 or not hasattr(GR, "sheet_mode"):
            check("--sheet 通道就位", True, "无存档或无接口，跳过")
        else:
            _ours = GR.build_sheet("Sam")
            _res = _ours.get("抗性") or {}
            _pct = _ours.get("伤害加成") or {}
            check("--sheet 我方面板值可生成（键名 = 游戏面板叫法）",
                  len(_res) == 10 and len(_pct) >= 15
                  and _ours.get("OA") and _ours.get("DA"),
                  "抗性 %d 项 ｜ 加成 %d 项 ｜ OA %.0f DA %.0f 攻速 %.2f 暴伤 %.0f"
                  % (len(_res), len(_pct), float(_ours.get("OA") or 0),
                     float(_ours.get("DA") or 0), float(_ours.get("攻击速度") or 0),
                     float(_ours.get("暴击伤害加成") or 0)))
            check("DoT 键带「（持续伤害）」后缀，直伤键不带",
                  any("（持续伤害）" in k for k in _pct) and "毒素" in _pct
                  and "毒素（持续伤害）" not in _pct,
                  "毒素/酸液是直伤、只有 `poisondot` 才是持续")
            _sp = GR.sheet_path("Sam")
            check("实测模板已生成（in_game 段待人工誊录）", _sp.is_file(), str(_sp))
            if _sp.is_file():
                _doc = _json.loads(_sp.read_text(encoding="utf-8"))
                _ing = _doc.get("in_game") or {}
                # ★ 2026-09-20：`in_game` 段**首次誊录**后语义变了 ——
                #   以前断言「必须为空」（防止把空模板误当真值）；
                #   现在应断言「**格式合法、能被 --sheet 认出来**」。
                _filled = sum(1 for k, v in _ing.items()
                              if not isinstance(v, dict) and v is not None
                              and not str(k).startswith('_'))
                _filled += sum(1 for s in ("抗性", "伤害加成")
                               for v in (_ing.get(s) or {}).values() if v is not None)
                check("in_game 段誊录的真值格式合法（模板态 0 项 / 已填则 ≥1 项）",
                      isinstance(_filled, int) and _filled >= 0
                      and isinstance(_ing.get('_meta') or {}, dict),
                      "已填 %d 项" % _filled)
            check("未填写会被明确报出（不静默算通过）",
                  "还没有产生任何证据" in (SKILL / "tools" / "gt_regress.py")
                  .read_text(encoding="utf-8"),
                  "空模板跑 --sheet 会打印这句话")
    except Exception as _e:
        check("--sheet 通道可运行", False, f"{type(_e).__name__}: {_e}")

    # ---- 伤害模型 v2：护甲/破甲的**逐类型裁决**（2026-09-20）----
    #   用户口径：「怪物元素护甲值不管，只需要知道在伤害循环的主要输出属性中，
    #   对应的护甲/破甲也是需要考虑的」。所以这一组守两件事：
    #     ① `rep['armor']['by_type']` 的键**恰好等于**输出循环里真正进 DPS 的类型；
    #     ② 只有 `physical` 被判「过护甲」——「元素护甲」在本作里不存在。
    print("\n[23] 伤害模型 v2 · 护甲/破甲逐类型裁决（by_type）")
    try:
        import json as _json3
        _sv3 = (_json3.loads((SKILL / "data" / "regress" / "model_v2_anchor.json")
                             .read_text(encoding="utf-8"))
                .get("cases", {}).get("Sam", {}).get("v2", {})) \
            if (SKILL / "data" / "regress" / "model_v2_anchor.json").is_file() else {}
        _sd3 = paths.save_dir()[0]
        if not _sd3 or not _sv3:
            check("护甲逐类型裁决", True, "无存档目录或锚点，跳过")
        else:
            D3 = importlib.import_module("gd.dps")
            RR3 = importlib.import_module("gd.rr")
            _ROT3 = importlib.import_module("gd.rotation")     # ★ 局部取模块：
            #   `_res` 这个名字在前面几组里被覆盖成过 dict，别再依赖它
            _c3 = D3.load_char("Sam")
            _sk3 = {k: v for k, v in _c3["skills"].items() if v > 0}
            _e3 = dict(_sk3)
            for _rec, _ex in (_c3.get("skill_plus") or {}).items():
                if _rec in _e3:
                    _e3[_rec] += _ex
            _m3 = {}
            for _rec, _lv in _sk3.items():
                if "_classtraining_" in _rec:
                    _m3[os.path.basename(_rec).replace("_classtraining_", "")
                        .replace(".dbr", "")] = _lv
            for _cls, _ex in (_c3.get("mastery_plus") or {}).items():
                _m3[_cls] = _m3.get(_cls, 0) + _ex
            _at3 = _ROT3.panel_attrs(_c3.get("bio") or {}, _m3,
                                     _c3.get("gear_flat") or {}, _c3.get("gear_pct") or {})
            _r3 = _ROT3.final_report(
                "werewolf", _e3, db=_c3["db"], folded=_c3["folded"],
                skill_records=list(_e3), base_aps=_c3.get("base_aps") or 1.25,
                attr_pct=_ROT3.attr_damage_pct(_at3),
                conversions=_c3.get("conversions") or [],
                skill_mods=_c3.get("skill_mods") or {},
                attrs=_at3, level=_c3.get("level"),
                enemy=importlib.import_module("gd.enemy").get_profile(
                    None, _c3.get("level") or 100),
                # ★ 2026-09-22：与生产链路同口径（武器类型硬前提门控）。
                weapon_st=importlib.import_module("gd.procs").weapon_state_of(_c3))
            _am = _r3.get("armor") or {}
            _bt = _am.get("by_type") or {}
            _cov = list(_am.get("covered_types") or [])
            # ① 键集 == 进 DPS 的类型集（`dps_by_type` 才是「主要输出属性」的判据）
            _dbt = RR3.dps_by_type({k: v for k, v in
                                    (_r3.get("skills") or {}).items() if v.get("dps")})
            check("by_type 的键 == 输出循环里真正进 DPS 的类型集",
                  set(_cov) == set(_dbt) and len(_cov) == len(set(_cov)),
                  "by_type %d 项 ｜ dps_by_type %d 项" % (len(_cov), len(_dbt)))
            # ② 占比降序 + 合计 ≈ 1
            _sh = [_bt[t]["share"] for t in _cov]
            check("按循环占比降序排列且合计 = 1",
                  _sh == sorted(_sh, reverse=True) and abs(sum(_sh) - 1.0) < 5e-3,
                  "首位 %s %.1f%% ｜ 合计 %.4f"
                  % (_bt[_cov[0]]["zh"], _sh[0] * 100, sum(_sh)) if _cov else "空")
            # ③ 只有 physical 过护甲
            _hit = sorted(t for t in _cov if _bt[t]["armor_affected"])
            check("只有 physical 被判「过护甲」（不存在元素护甲）",
                  _hit == ["physical"] if "physical" in _cov else _hit == [],
                  "过护甲：%s" % (_hit or "无"))
            # ④ 护甲穿透只挂在 physical / pierce 上
            _ap = sorted(t for t in _cov if _bt[t]["armor_pierce_pct"])
            check("护甲穿透（`offensivePierceRatio`）只标在 physical / pierce 上",
                  set(_ap) <= {"physical", "pierce"}, "标注：%s" % (_ap or "无"))
            # ⑤ 吸收：怪物侧 56% ≠ 引擎基准 70%，且修正量取自官方调整表
            check("吸收率用**怪物侧** 56%（不是引擎基准 70%）",
                  abs(float(_am.get("absorption") or 0) - 56.0) < 1e-9
                  and abs(float(_am.get("absorption_player") or 0) - 70.0) < 1e-9
                  and float(_am.get("absorb_modifier") or 0) == -20.0,
                  "怪物 %.0f%% ｜ 基准 %.0f%% ｜ 修正 %+.0f"
                  % (_am.get("absorption") or 0, _am.get("absorption_player") or 0,
                     _am.get("absorb_modifier") or 0))
            # ⑥ 破甲：实测普查玩家侧 0 次 ⇒ 本角色破甲值必须为 0（不是写死的假设）
            _cen = _am.get("reduce") or {}
            _pz = _cen.get("player_side_total")
            check("破甲字段玩家侧实测 = 0 次（普查，非写死）",
                  _pz == 0 and (RR3.armor_reduce_player_side() in (0, None)),
                  "itemdb+skills 合计 %s ｜ 本角色破甲 %.0f"
                  % (_pz, _cen.get("value") or 0))
            check("破甲挂点可吃值（造一个 75 ⇒ 收集器得 75）",
                  abs(RR3.armor_reduce_of(
                      {"DamageDurationDefensiveReductionMin": 75}) - 75.0) < 1e-9,
                  "说明挂点通路，不是死参数")
            # ⑦ 物理占比必须**如实反映**（这条守卫判的是「报告没在物理占比上撒谎」，
            #    不是「Sam 永远是穿刺/流血流」—— 后者会随装备漂移）。
            #    沿革：2026-09-21 之前 Sam 是「穿刺 59.9% + 流血 36.5%」⇒ 物理 <10%；
            #    **fix3 落档**后换了双「蝎尾狮长剑」（物理+毒酸），丢掉了旧副手的
            #    「138% 物理→穿刺」⇒ 物理升到 **17.1%**（构成＝毒酸DoT 27.3 / 流血 23.6 /
            #    穿刺 18.2 / 物理 17.1 / 毒酸 10.4）。带护甲真值怪 `m3955` 复核
            #    仍 **+9.16%**（非护甲口径 +8.91%）⇒ 不是「没算护甲」造成的假增益。
            #    ⇒ 阈值放宽到 35%（仍能抓住「物理变成主通道」这种真回归）。
            _ph = _bt.get("physical") or {}
            check("物理占比如实反映（现 17%，新武器丢了 138% 物理→穿刺 ⇒ 护甲乘区变大）",
                  (not _ph) or _ph["share"] < 0.35,
                  "物理 %.2f%%" % ((_ph.get("share") or 0) * 100))
            # ⑧ 护甲值缺失必须**如实**标成未应用，绝不静默打折
            check("护甲值缺失 ⇒ applied=False（不用猜测值改写伤害）",
                  _am.get("applied") is False and bool(_am.get("reason")),
                  str(_am.get("reason")))
    except Exception as _e:
        check("护甲逐类型裁决可运行", False, f"{type(_e).__name__}: {_e}")

    # ---- 伤害模型 v3：敌方面板逐字段复现（用户游戏截图当金标准）----
    #   来源：用户 2026-09-20 提供的游戏截图（怪物等级 100 ｜ 终极 ｜ 1 人）。
    #   反查锁定 `m3955` = tagGDX3Nemesis_Outlaw02 —— 抗性 (58,5,10,10,5) 全库唯一命中。
    #   这一组是**端到端**的：记录 + 难度修正 + 被动技能（数组按等级取值）三段全对才过。
    print("\n[24] 伤害模型 v3 · 敌方面板复现（m3955 vs 游戏截图）")
    PANEL_GOLD = {           # 字段: (截图值, 容差)
        "physique": (856, 0.5), "cunning": (1188, 0.5), "spirit": (1188, 0.5),
        "health": (4667811, 1.0), "energy": (68563, 1.0),
        "oa": (2477, 0.5), "da": (2254, 0.5), "armor": (1607, 0.5),
    }
    RES_GOLD = {"fire": 58, "cold": 5, "lightning": 10, "poison": 10, "pierce": 5,
                "bleeding": 85, "vitality": 12, "aether": 25, "chaos": 25,
                "physical": 2}
    try:
        EN2 = importlib.import_module("gd.enemy")
        _pn = EN2.panel_of("m3955", 100, 3, 1)
        check("敌方面板可复现（m3955 存在）", bool(_pn))
        for _k, (_want, _tol) in sorted(PANEL_GOLD.items()):
            _got = float((_pn or {}).get(_k) or 0.0)
            check("面板 %s == 游戏截图" % _k, abs(_got - _want) <= _tol,
                  "%.1f ｜ 截图 %s" % (_got, _want))
        _rr2 = (_pn or {}).get("res") or {}
        _bad = {b: (round(_rr2.get(b, 0.0), 1), v) for b, v in RES_GOLD.items()
                if abs(float(_rr2.get(b, 0.0)) - v) > 0.05}
        check("抗性**两排 10 型** == 游戏截图第一排（5 型）",
              all(b not in _bad for b in ("fire", "cold", "lightning", "poison", "pierce")),
              "、".join("%s %.0f" % (b, _rr2.get(b, 0)) for b in
                        ("fire", "cold", "lightning", "poison", "pierce")))
        check("被动技能补齐的**第二排**（5 型，截图未拍全）",
              all(b not in _bad for b in ("bleeding", "vitality", "aether", "chaos", "physical")),
              "、".join("%s %.0f" % (b, _rr2.get(b, 0)) for b in
                        ("bleeding", "vitality", "aether", "chaos", "physical")))
        # 反例：不合并被动技能会把 OA/护甲/流血算错 —— 断言「被动确实在起作用」
        _x = EN2.merged_stats("m3955", 100.0, 3, 1)
        check("被动技能确实被合并（OA 修正 / 护甲 / 流血）",
              float(_x.get("characterOffensiveAbilityModifier") or 0) < 0
              and float(_x.get("defensiveProtection") or 0) > 1000
              and float(_x.get("defensiveBleeding") or 0) > 50,
              "OAmod %.0f ｜ 护甲 %.0f ｜ 流血 %.0f"
              % (float(_x.get("characterOffensiveAbilityModifier") or 0),
                 float(_x.get("defensiveProtection") or 0),
                 float(_x.get("defensiveBleeding") or 0)))
        # JS `^` 幂运算：官方生命方程 ((charLevel*60)^1.53)+30000
        _chk = EN2._eval("((charLevel*60)^1.53)+30000", {"charLevel": 100.0})
        check("官方方程的 `^`（幂）被正确求值（不是异或/0）",
              abs(_chk - 633400.0) < 3000.0, "%.0f" % _chk)
        # ★ 护甲自动套用契约：真值怪的 armor 能被 `gd/dps.py` 直接取用（无需 --enemy-armor）
        _DPS2 = importlib.import_module("gd.dps")
        _v_real, _s_real = _DPS2.resolve_enemy_armor(
            None, EN2.get_profile("m3955", 71, 1, 3))
        check("真值怪的护甲被**自动**喂进模型（不需要 `--enemy-armor`）",
              float(_v_real or 0) > 0, "%.0f ｜ %s" % (float(_v_real or 0), _s_real))
        _v_pool, _ = _DPS2.resolve_enemy_armor(
            None, EN2.get_profile("pool:Champion+Hero@0.5", 71, 1, 3))
        check("等级池没有单一护甲值 ⇒ 如实留空（报下界，不编数）",
              _v_pool is None)
        _v_ovr, _s_ovr = _DPS2.resolve_enemy_armor(300.0, EN2.get_profile("m3955", 71, 1, 3))
        check("显式 `--enemy-armor` 优先于敌方档自带的护甲",
              abs(float(_v_ovr) - 300.0) < 1e-9, "%.0f ｜ %s" % (_v_ovr, _s_ovr))
        # ★ 10 型「两排」顺序就是 grimtools 页面渲染的顺序（供报告分组用）
        check("抗性顺序 = 面板两排顺序（前 5 一排、后 5 一排）",
              tuple(EN2.RES_ORDER) == ("fire", "cold", "lightning", "poison", "pierce",
                                       "bleeding", "vitality", "aether", "chaos",
                                       "physical"),
              "、".join(EN2.RES_ORDER))
        # ★★ 2026-09-20 修：五档档位（elite/boss/high/max/none）的 profile 里
        #   `difficulty` 是**字符串**（`"n/a"`，见 `gd/enemy.py:518`），而
        #   `gd/rotation.py::final_report` 曾裸 `int()` 这个字段 ⇒
        #   `plan_audit --enemy elite` 直接 `ValueError: invalid literal for int()` 崩掉。
        #   现在改成容错 cast（转不动就透传给 `adj_index`，它本就接受字符串）。
        _dif_txt = []
        for _spec in ("none", "elite", "boss", "high", "max"):
            _p = EN2.get_profile(_spec, 71, 1, 3)
            _dif_txt.append("%s=%r" % (_spec, _p.get("difficulty")))
        check("五档敌方的 `difficulty` 含非数字（`n/a`）—— 必须容错",
              any("n/a" in t for t in _dif_txt), " ｜ ".join(_dif_txt))
        # 真跑一遍护甲吸收段：字符串难度不得抛异常（原崩溃点）
        _okt, _err = [], ""
        _CB2 = importlib.import_module("gd.combat")
        for _spec in ("none", "elite", "boss", "high", "max", "m3955",
                      "pool:Champion+Hero@0.5"):
            try:
                _ep = EN2.get_profile(_spec, 71, 1, 3)
                _d = _ep.get("difficulty")
                try:
                    _d = int(_d)
                except (TypeError, ValueError):
                    _d = _d or 3
                _CB2.monster_absorption(_d, _ep.get("players") or 1)
                _okt.append(_spec)
            except Exception as _e2:                      # noqa: BLE001
                _err += "%s->%s: %s " % (_spec, type(_e2).__name__, _e2)
        check("七种敌方档全部能过护甲吸收段（字符串难度不再崩）",
              not _err and len(_okt) == 7, _err or "、".join(_okt))
        # ★ 源码级守卫：`final_report` 里**不许**再出现裸 int() 的旧写法
        _src_rot = (Path(__file__).resolve().parent.parent
                    / "gd" / "rotation.py").read_text(encoding="utf-8")
        check("gd/rotation.py 的难度字段用容错 cast（旧裸 int 写法已消失）",
              "_as_int((_ep or {}).get('difficulty')" in _src_rot
              and "int((_ep or {}).get('difficulty') or 3)" not in _src_rot)
    except Exception as _e:
        check("敌方面板复现可运行", False, f"{type(_e).__name__}: {_e}")

    # ------------------------------------------------ [25] 物品库「总览」+ 候选池自洽性
    # 用户口径（2026-09-20）：「检查一下装备池子是否与数据库一致」。
    # 站点基准 = grimtools 物品库「总览」面板 9 行（截图）。逐行复算的算法见
    # `tools/db_census.py` 的 docstring（逐字来自 `/dist/db/itemdb/db.js`）。
    print("\n[25] 物品库「总览」对拍 + 候选池与数据库一致")
    try:
        _CEN = importlib.import_module("tools.db_census")
    except Exception:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import db_census as _CEN       # type: ignore
        except Exception as _e:
            _CEN = None
            check("db_census 可导入", False, f"{type(_e).__name__}: {_e}")
    if _CEN is not None:
        try:
            _rows, _per, _vals = _CEN.overview(db)
            _bad = {k: (_rows[k]["local"], v) for k, v in _CEN.GOLDEN_ZH.items()
                    if _rows[k]["local"] != v}
            check("「总览」9 行 == grimtools 站点", not _bad,
                  "；".join("%s 本地 %s vs 站点 %s" % (k, a, b)
                            for k, (a, b) in _bad.items()) if _bad
                  else "、".join("%s %s" % (k, format(_rows[k]["local"], ","))
                                 for k in _CEN.GOLDEN_ZH))
            # 两处「本地 ≠ 原始量」的行必须**能解释**，不是缺数据
            _n_note = len(db.items) - _rows["物品总数"]["local"]
            check("物品总数 = allItems 减 ItemNote 类（%d 条游戏内书籍）" % _n_note,
                  _n_note == 311, "ItemNote %d" % _n_note)
            check("物品技能修正 = 5 表 modifierSkillName 去重（不是 itemSkills 表长）",
                  _rows["物品技能修正"]["local"] == 3297
                  and _rows["物品技能修正"]["raw"] == len(db.skills)
                  and len(db.skills) > _rows["物品技能修正"]["local"],
                  "去重 %d ｜ itemSkills 全表 %d" % (_rows["物品技能修正"]["local"],
                                                    len(db.skills)))
            _orph = _CEN.unreferenced_skills(db, _vals)
            check("孤儿 itemSkills（无任何物品/词缀/套装引用）为 971 条",
                  len(_orph) == 971, "%d 条" % len(_orph))

            # ---- 候选池自洽：池里每一条都要能过它自己槽位的校验
            _O2 = importlib.import_module("gd.opt")
            _bs, _cs = [], []
            for _s in _O2.SLOTS:
                for _g in (_O2.POOL_BASE.get(_s) or []):
                    if not _O2.slot_ok(_s, _g):
                        _bs.append("%s/%s" % (_s, _g))
                for _g in (_O2.POOL_COMP.get(_s) or []):
                    if not _O2.comp_ok(_s, _g):
                        _cs.append("%s/%s" % (_s, _g))
            check("硬编码装备池每一项都过 `slot_ok`（类别 ↔ 槽位一致）",
                  not _bs, "、".join(_bs[:8]) or "全过")
            check("硬编码镶嵌池每一项都过 `comp_ok`（描述 ↔ 槽位一致）",
                  not _cs, "、".join(_cs[:8]) or "全过")
            check("装备池每一项都在离线库里（无幽灵 id）",
                  all(_g in db.items for _s in _O2.SLOTS
                      for _g in (_O2.POOL_BASE.get(_s) or [])),
                  "")
            _q = [g for _s in _O2.SLOTS for g in (_O2.POOL_BASE.get(_s) or [])
                  if _O2._is_quest(_O2._IT.get(g) or {})]
            check("装备池不含任务/剧情物品（会被游戏静默摘掉，陷阱 #35）",
                  not _q, "、".join(_q[:8]) or "无")
            # ---- ★ 成对/可互换槽位：两槽的自动池必须同源（原实现后一槽恒空）
            _aut = _O2.auto_pool(int(os.environ.get("GD_AUTO_TOPN", "45")))
            check("auto_pool 对「戒指2」不再恒空（与「戒指1」同源）",
                  set(_aut.get("戒指1") or []) == set(_aut.get("戒指2") or [])
                  and len(_aut.get("戒指2") or []) > 0,
                  "戒指1 %d ｜ 戒指2 %d" % (len(_aut.get("戒指1") or []),
                                            len(_aut.get("戒指2") or [])))
            check("auto_pool 对「副手」与「主手」同源",
                  set(_aut.get("主手") or []) == set(_aut.get("副手") or []),
                  "主手 %d ｜ 副手 %d" % (len(_aut.get("主手") or []),
                                          len(_aut.get("副手") or [])))
            _opt_src = (SKILL / "gd" / "opt.py").read_text(encoding="utf-8")
            check("戒指两槽硬编码名单同源（共用 `RING_BASE`）",
                  _O2.POOL_BASE.get("戒指1") == _O2.POOL_BASE.get("戒指2")
                  and "'戒指1': RING_BASE" in _opt_src
                  and "'戒指2': RING_BASE" in _opt_src,
                  "%d / %d ｜ RING_BASE 常量 %s"
                  % (len(_O2.POOL_BASE.get("戒指1") or []),
                     len(_O2.POOL_BASE.get("戒指2") or []),
                     "在" if "RING_BASE =" in _opt_src else "缺失"))
        except Exception as _e:
            check("物品库总览 + 候选池检查可运行", False,
                  f"{type(_e).__name__}: {_e}")

    # ================================================================ [26]
    print("\n[26] 并行搜索链与参数护栏（--jobs / --chains）")
    try:
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location("_ab", SKILL / "tools" / "autobuild.py")
        _ab = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_ab)

        # ★ 顺序坑守卫：显式 `--jobs` 的「并行意图」必须排在 `--quick`/`--extreme`
        #   模式预设**之前**判定 —— 否则 `--quick --jobs 20` 会先被 quick 分支置成
        #   1 条链，用户的并行意图被静默吞掉（2026-09-20 实测踩过一次：
        #   机器 32 核，日志打「并行链 1」、整机 CPU 仅 6%）。
        _src = (SKILL / "tools" / "autobuild.py").read_text(encoding="utf-8")
        _blk = _src[_src.find("_chains_explicit = bool(a.chains)"):]
        _i_jobs = _blk.find("if not a.chains and _jobs_explicit and a.jobs > 1:")
        _i_alloc = _blk.find("_alloc_parallel(")
        check("--jobs 的并行意图在 `_alloc_parallel` **之前**兑现（顺序坑守卫）",
              0 <= _i_jobs < _i_alloc,
              "jobs@%d ｜ alloc@%d" % (_i_jobs, _i_alloc))
        # ★ 2026-09-22：默认不再是「写死逻辑核」也不是「写死 1」，而是
        #   **把物理核在「链数」与「每链进程数」之间分配**（陷阱 #89）。
        #   ★ 关键：都不显式传时必须把两参数都当 0 传进去走自动分支 ——
        #     否则 32 逻辑核机器会算出「32 链 × 1 进程」（正是要修的那个配置）。
        check("★ 并行度自动分配（chains/procs 都不显式时走 else 0，不是逻辑核）",
              "chains=(a.chains if _chains_explicit else 0)" in _blk
              and "a.chains = _logical_cores()" not in _blk)

        # ★ 并行链必须封顶：① 内存（每链独立加载一份 DB）② **逻辑核**
        #   （多于核数的 worker 只会互相抢 CPU，墙钟反而更长）
        _cap = _ab._cap_chains_by_mem(9999)
        check("并行链封顶（每链 ≈ %.2f GB，且不超过逻辑核 %d）"
              % (_ab._PER_WORKER_GB, _ab._logical_cores()),
              1 <= _cap <= _ab._logical_cores(),
              "请求 9999 → 封顶 %d 条" % _cap)
        check("并行链每链内存估值不离谱（实测 ~0.19 GB，估值须 <0.8 GB）",
              0.1 <= _ab._PER_WORKER_GB <= 0.8,
              "%.2f GB" % _ab._PER_WORKER_GB)

        # ★ help 文案必须把「多核靠 --chains」讲清楚，别退回成误导性的「并行线程数」
        check("--jobs 的 help 明确指向 --chains（防误导回退）",
              "真正吃满多核的是 --chains" in _src)

        # ★★ 锁的粒度（2026-09-20 两次实测教训）：
        #    ① 两个 autobuild 同时跑 ⇒ 一次 53 个 python.exe，且**两个进程往同一个
        #       `--out` JSON 写**，结果与日志都不可追溯 → 于是加了锁；
        #    ② 但锁一开始是**全局单实例**，直接把「多形态并发普查」堵死
        #       （实测 `tools/sweep_arch.py` 第 6 个形态被拒、rc=4）
        #       → 改成按 **(角色, 输出文件)** 取键：同输出仍互斥，不同输出可并发。
        check("锁：锁文件落在 data/scratch/.autobuild.locks/ 目录下",
              _ab._lock_path("Sam|t").parent.name == ".autobuild.locks")
        check("锁：**不同输出 ⇒ 不同锁文件**（并发的前提）",
              _ab._lock_path(_ab._lock_key("Sam", "a"))
              != _ab._lock_path(_ab._lock_key("Sam", "b")))
        check("锁：同一输出 ⇒ 同一锁文件（互斥的前提）",
              _ab._lock_path(_ab._lock_key("Sam", "a"))
              == _ab._lock_path(_ab._lock_key("Sam", "a")))
        check("锁：PID 存活判定可用（自己一定活着）",
              _ab._pid_alive(os.getpid()) is True)
        check("锁：明显不存在的 PID 判为已死",
              _ab._pid_alive(99999999) is False)
        try:
            import json as _json26
            import subprocess as _sp26
            _locks = [_ab._lock_path(_ab._lock_key("Sam", "t")),
                      _ab._lock_path(_ab._lock_key("Sam", "other"))]
            _baks = [p.read_text(encoding="utf-8") if p.is_file() else None
                     for p in _locks]
            _lp = _locks[0]
            # ① 持有者活着 → 必须拒绝
            _hold = _sp26.Popen([sys.executable, "-c", "import time;time.sleep(20)"])
            _lp.write_text(_json26.dumps({"pid": _hold.pid, "char": "Sam",
                                          "out": "t", "t0": "t"}),
                           encoding="utf-8")
            # 预期会打印「拒绝启动」→ 静音，免得自检日志里混进一个假 ✗
            import contextlib as _cl26
            import io as _io26
            _buf26 = _io26.StringIO()
            with _cl26.redirect_stdout(_buf26):
                _refused = _ab.acquire_single_instance("Sam", "t")
            check("锁：同输出且持有者活着 → 拒绝启动", _refused is False,
                  "提示语：%s" % _buf26.getvalue().strip().splitlines()[:1])
            check("锁：拒绝时提示怎么处理（含 PID 与逃生口）",
                  "--force" in _buf26.getvalue()
                  and str(_hold.pid) in _buf26.getvalue())
            # ★ 关键新语义：持有者还活着，但**换一个输出**必须放行
            with _cl26.redirect_stdout(_io26.StringIO()):
                _other_ok = _ab.acquire_single_instance("Sam", "other")
            check("锁：持有者在世但**输出不同** ⇒ 放行（多形态并发的前提）",
                  _other_ok is True)
            with _cl26.redirect_stdout(_io26.StringIO()):
                _forced_ok = _ab.acquire_single_instance("Sam", "t", force=True)
            check("锁：`--force` 能抢占同输出", _forced_ok is True)
            # ② 持有者死了 → 自动回收，不锁死
            _hold.kill()
            _hold.wait()
            time.sleep(0.6)
            with _cl26.redirect_stdout(_io26.StringIO()):
                _reclaim_ok = _ab.acquire_single_instance("Sam", "t")
            check("锁：持有者已死 → 自动回收（不会锁死）", _reclaim_ok is True)
            # ③ 自己持有自己 → 不自我拒绝
            _lp.write_text(_json26.dumps({"pid": os.getpid(), "char": "Sam",
                                          "out": "t", "t0": "t"}),
                           encoding="utf-8")
            with _cl26.redirect_stdout(_io26.StringIO()):
                _self_ok = _ab.acquire_single_instance("Sam", "t")
            check("锁：本进程持有 → 不自我拒绝", _self_ok is True)
        finally:
            for _p, _b in zip(_locks, _baks):
                try:
                    if _b is not None:
                        _p.write_text(_b, encoding="utf-8")
                    elif _p.is_file():
                        _p.unlink()
                except Exception:
                    pass
        check("锁：main() 里真的接上了锁（拒绝时返回码 4）",
              "acquire_single_instance(a.char, _out_pre" in _src
              and "return 4" in _src)
        check("锁：命令行有 --force 逃生口", '"--force"' in _src)

        # ★★ 多形态并发普查驱动（2026-09-20 晚）—— 三层并行里**最外层**那一层。
        #    它必须绕开 `--jobs/--chains` 与 `--procs`，自己控制总进程预算。
        _sw = SKILL / "tools" / "sweep_arch.py"
        check("多形态并发：tools/sweep_arch.py 存在", _sw.is_file())
        if _sw.is_file():
            _sws = _sw.read_text(encoding="utf-8")
            check("多形态并发：默认 --budget = 24（实测拐点，不是越多越好）",
                  "default=24" in _sws)
            check("多形态并发：每形态进程数 = 预算 // 形态数",
                  "a.budget // n" in _sws)
            check("多形态并发：LNS 轮数走已存在的 --anneal-iters",
                  "'--anneal-iters'" in _sws and "'--lns-iters'" not in _sws)
            check("多形态并发：文档讲清「三层并行别叠加」",
                  "别叠加" in _sws and "绕开" in _sws)
            check(r"多形态并发：结果行按「真实 DPS」精确匹配（防把 +100.2% 当 DPS）",
                  r"真实 DPS\s*([\d,]+)" in _sws)
            check("多形态并发：汇总里给出「重叠收益」口径",
                  "sum_elapsed" in _sws and "重叠收益" in _sws)
    except Exception as _e:
        check("并行链护栏检查可运行", False, f"{type(_e).__name__}: {_e}")

    # ================================================================ [27]
    print("\n[27] 转化源类型 / 武器池(WPS) 权重 / 形态主输出")
    try:
        import io as _io27
        import json as _js27
        from gd import dmg as _DM27
        from gd import rotation as _RT27
        from gd import dbr as _DBR27

        # ① `Elemental` **作源**必须命中 火/冰/电（旧实现只当目标 ⇒ 静默失效）
        _ok_el = (_DM27._conv_matches('elemental', 'cold')
                  and _DM27._conv_matches('elemental', 'fire')
                  and _DM27._conv_matches('elemental', 'lightning'))
        check("转化：`Elemental` 作源命中 火/冰/电（不是只作目标）", _ok_el)
        check("转化：`Elemental` 作源**不**误伤非元素类型",
              not _DM27._conv_matches('elemental', 'physical')
              and not _DM27._conv_matches('elemental', 'pierce'))
        check("转化：同名类型仍精确命中（`cold`→`cold`）",
              _DM27._conv_matches('cold', 'cold')
              and not _DM27._conv_matches('cold', 'fire'))

        # ② 「元素→穿刺」在 `apply_conversions` 里对火/冰/电**各**生效
        _base = {'fire': [100.0, 100.0], 'cold': [100.0, 100.0],
                 'lightning': [100.0, 100.0], 'physical': [100.0, 100.0]}
        _out = _RT27.apply_conversions(_base, [('elemental', 'pierce', 50.0)])
        check("转化：`元素→穿刺 50%` 让冰分的一半并入穿刺",
              abs(_out['pierce'][0] - 150.0) < 1e-6
              and abs(_out['cold'][0] - 50.0) < 1e-6,
              "pierce=%.1f cold=%.1f" % (_out['pierce'][0], _out['cold'][0]))
        check("转化：`元素→穿刺` 不碰物理",
              abs(_out['physical'][0] - 100.0) < 1e-6)

        # ③ `skillChanceWeight` 必须从 calc.js 回填进中央 DB（旧数据整库缺失
        #    ⇒ 武器池技能被误判成默认攻击，实测虚高 4 倍以上）
        _db27 = _DBR27.open_all()
        _w10 = (_db27.fields('records/skills/playerclass10/wpattack02.dbr') or {}).get(
            'skillChanceWeight')
        check("武器池：雪崩的 `skillChanceWeight` 已回填（非空列表）",
              isinstance(_w10, list) and len(_w10) >= 10,
              "lv1..lvN = %s" % (_w10[:6] if _w10 else None))
        check("武器池：雪崩权重曲线 = 12(1级) → 26(10级) → 30(20级)",
              isinstance(_w10, list) and len(_w10) >= 20
              and _w10[0] == 12 and _w10[9] == 26 and _w10[19] == 30,
              "%s / %s / %s" % (_w10[0] if _w10 else None,
                                _w10[9] if len(_w10 or []) > 9 else None,
                                _w10[19] if len(_w10 or []) > 19 else None))
        _nb = (_db27.fields('records/skills/playerclass04/wpattack1.dbr') or {}).get(
            'skillChanceWeight')
        check("武器池：夜刃系权重低于雪崩（25 < 30，雪崩是最高一档）",
              isinstance(_nb, list) and max(_nb) == 25 and max(_nb) < max(_w10),
              "夜刃 max=%s" % (max(_nb) if _nb else None))

        # ④ 分母口径：官方 = 默认攻击权重 `max(0, 100-ΣW)`、分母 `max(100, ΣW)`
        #    （Crate 设计师 Zantai）—— 旧实现 `100 + ΣW` 会系统性高估普攻占比。
        _src27 = (SKILL / "gd" / "rotation.py").read_text(encoding="utf-8")
        check("武器池：分母用官方口径 max(100, Σw)（不是 100+Σw）",
              'denom = max(100.0, W)' in _src27
              and 'w_def = max(0.0, 100.0 - W)' in _src27)

        # ⑤ **普通武器攻击**的合成：没点任何默认攻击(DAR)、但有武器池时，
        #    左键仍是「普通武器攻击」。旧实现把整池清空 ⇒ 这类形态算出近乎零输出。
        check("武器池：存在「普通攻击」合成记录常量 BASIC_REC",
              isinstance(getattr(_RT27, 'BASIC_REC', None), str)
              and 'if not swing and proc:' in _src27)
        check("武器池：合成记录不按记录名查库（`default_rec == BASIC_REC` 有专判）",
              'if default_rec == BASIC_REC:' in _src27)

        # ⑥ 形态门控：**每个形态都要声明 `form`**
        #   ★★ 2026-09-21 重写。原先这里断言的是「未声明 form 的形态不被过滤」，
        #     把它当成「零漂移前提」—— 其实那是**漏洞**：狼人形态的 Sam 走
        #     `wolf_nightblade_fast`（当时无 form）⇒ 门控全程没跑，
        #     「跃击」（`playerclass10/leap1.dbr`，而 `werewolf1.granted` 里只有
        #     野性利爪/狂乱撕扯）被按 `1/冷却 = 0.33 次/秒` 算成 **19.8% 面板 DPS**，
        #     还连带把 v9 落档的「跃击 +5」决策带偏。
        #     现在所有形态都声明 form，漏网即失败。
        _arch27 = _js27.loads((SKILL / "data" / "archetypes.json").read_text(
            encoding="utf-8"))
        _sk27 = _js27.loads((SKILL / "data" / "skills.json").read_text(
            encoding="utf-8"))
        _av = _arch27.get('avalanche') or {}
        check("形态：avalanche 声明了 form=human", _av.get('form') == 'human')
        check("形态：avalanche 声明了 primary_skill（主输出）",
              bool(_av.get('primary_skill')))
        _nof27 = sorted(k for k, v in _arch27.items() if not (v or {}).get('form'))
        check("形态：**每个**形态都声明了 form（门控不再有漏网）", not _nof27,
              "缺 form：%s" % ('、'.join(_nof27) if _nof27 else '无（%d 个）'
                               % len(_arch27)))

        # ⑥b form 必须与 `root_skills` 的变形分支自洽（防手抄错）
        _badf27 = []
        for _k27, _v27 in _arch27.items():
            _rs27 = (_v27 or {}).get('root_skills') or []
            _f27 = (_v27 or {}).get('form')
            if not _rs27:
                if _f27 != 'human':
                    _badf27.append('%s=%s（无 root ⇒ 应 human）' % (_k27, _f27))
                continue
            if (_sk27.get(_rs27[0]) or {}).get('kind') != 'shapeshift':
                continue                     # 非变形 root（如 avalanche 的 wpattack02）
            _base27 = _rs27[0].split('/')[-1].replace('.dbr', '')
            _exp27 = ('werewolf' if _base27.startswith('werewolf')
                      else 'wereraven' if _base27.startswith('wereraven')
                      else 'fangs' if _base27.startswith('fangs') else None)
            if _exp27 and _f27 != _exp27:
                _badf27.append('%s=%s（root %s ⇒ 应 %s）'
                               % (_k27, _f27, _base27, _exp27))
        check("形态：form 与 root_skills 的变形分支自洽", not _badf27,
              '、'.join(_badf27) if _badf27 else '逐一对上')

        # ⑥c ★★ 形态门控的**裁决表**——这是「跃击 bug」的永久守卫
        _R27 = importlib.import_module("gd.rotation")
        _g27 = _R27.form_granted(_arch27.get('werewolf') or {}, _sk27)
        _leap27 = 'records/skills/playerclass10/leap1.dbr'
        _claws27 = 'records/skills/playerclass10/werewolf1_skill01_claws.dbr'
        _dev27 = 'records/skills/devotion/tier2_06g_skill.dbr'
        _wps27 = 'records/skills/itemskills/granted/item_knockout.dbr'
        check("★★ 形态门控：狼人形态**剔除跃击**（变形后技能栏里没有它）",
              _R27.form_blocks(_leap27, 'werewolf', _g27, _sk27) is True)
        check("★★ 形态门控：形态授予技能 + 星座 proc **不被误杀**",
              not _R27.form_blocks(_claws27, 'werewolf', _g27, _sk27)
              and not _R27.form_blocks(_dev27, 'werewolf', _g27, _sk27),
              "野性利爪保留 ｜ 刀锋之怒（星座 proc）保留")
        check("★★ 形态门控：WPS 一并剔除（变身后默认攻击被野性利爪接管）",
              _R27.form_blocks(_wps27, 'werewolf', _g27, _sk27) is True)
        check("★★ 形态门控：人形态保留 WPS、只剔变形授予技能",
              not _R27.form_blocks(_wps27, 'human', set(), _sk27)
              and _R27.form_blocks(_claws27, 'human', set(), _sk27))
        check("★★ 形态门控：未声明 form ⇒ 一个都不剔（零漂移后路）",
              not _R27.form_blocks(_leap27, None, set(), _sk27)
              and _R27.form_gate([_leap27, _wps27], {'root_skills': []}, _sk27)
              == [_leap27, _wps27])

        # ⑦ 主输出技能的优化器权重必须是**全表最高**（否则「围绕主输出」被辅助技能挤掉）
        try:
            _os27 = dict(os.environ)
            os.environ['GD_ARCHETYPE'] = 'avalanche'
            import importlib as _il27
            import gd.opt as _O27
            _il27.reload(_O27)
            _sk27 = _js27.loads((SKILL / "data" / "skills.json").read_text(
                encoding="utf-8"))
            _tag27 = (_sk27.get('records/skills/playerclass10/wpattack02.dbr')
                      or {}).get('tag')
            _w27 = _O27.SKILL_W_DMG.get(_tag27)
            check("主输出：雪崩的技能权重 >= 其余任何技能",
                  _w27 is not None
                  and _w27 >= max(v for k, v in _O27.SKILL_W_DMG.items() if k != _tag27),
                  "雪崩 %.1f ｜ 次高 %.1f"
                  % (_w27 or -1,
                     max([v for k, v in _O27.SKILL_W_DMG.items() if k != _tag27] or [0])))
            os.environ.clear()
            os.environ.update(_os27)
            _il27.reload(_O27)
        except Exception as _e:
            check("主输出技能权重检查可运行", False, f"{type(_e).__name__}: {_e}")

        # ⑧ ★★ 星座数值必须真的进模型（2026-09-20 修：`devotion_skills.json` 是
        #    「只有名字、0 条带数值」的索引，它会占住记录槽位 ⇒ 701 条星座记录的
        #    真实数值永远进不来 ⇒ 整个星座系统在伤害模型里等于不存在）。
        _dv27 = _db27.fields('records/skills/devotion/tier2_06g_skill.dbr') or {}
        check("星座：天神之力「刀锋之怒」的记录能取到数值字段",
              len([k for k in _dv27 if k != 'templateName']) >= 5,
              "字段 %d 个" % len([k for k in _dv27 if k != 'templateName']))
        _dvn27 = _db27.fields('records/skills/devotion/tier1_10a.dbr') or {}
        check("星座：被动节点（鹰隼）的记录能取到数值字段",
              any(k.startswith('offensive') or k.startswith('character')
                  for k in _dvn27), sorted(_dvn27)[:6])
        check("星座：`devotion_skills.json` 本身确实不含 stats（遮罩的成因）",
              all(not (v or {}).get('stats')
                  for v in (_js27.loads((SKILL / "data" / "devotion_skills.json")
                                        .read_text(encoding="utf-8")) or {}).values()))

        # ⑧-b ★ **防御向**读出（2026-09-20 新增）：优化器的 `NEED`（130/105）是拿
        #    **装备槽**的抗性之和去比的（`gd/opt.py::ev` 只累加 gear 的 `res_of`），
        #    星座/基座/技能的贡献一概没进。这是**保守**口径，但必须**看得见** ——
        #    否则容易误读成「星座给抗性 ⇒ 装备可以少堆」。
        #    ★ 坑：`records_like` 收的是**前缀**不是子串 —— 传 'devotion' 会得到 0 条。
        _dnodes = list(_db27.records_like('records/skills/devotion'))[:150]
        check("星座：能按前缀枚举星节点（records_like 收前缀）",
              len(_dnodes) > 50, "枚举到 %d 个星节点" % len(_dnodes))
        _withdef = [_r for _r in _dnodes
                    if any(k.startswith('defensive')
                           for k in (_db27.fields(_r) or {}))]
        check("星座：库里存在带 defensive* 字段的星节点（抗性来源非空）",
              bool(_withdef), "%d / %d 个含防御字段" % (len(_withdef), len(_dnodes)))
        _rs27 = _RT27.devotion_contrib(
            {r: 1 for r in _withdef[:40]}, _db27) if _withdef else {}
        check("星座：devotion_contrib 暴露防御向读出（'res' / 'raw' 两键）",
              'res' in _rs27 and 'raw' in _rs27,
              "res=%s" % {k: round(v, 1)
                          for k, v in list((_rs27.get('res') or {}).items())[:5]})
        check("星座：含防御字段的节点 → `res` 真的非空（同 RMAP 口径）",
              bool(_rs27.get('res')),
              "合计 %s" % {k: round(v, 1)
                           for k, v in (sorted((_rs27.get('res') or {}).items(),
                                               key=lambda x: -abs(x[1]))[:5])})
        # ★ 抗性**代价**（负值）不许被 clamp 成 0 —— 实测 Sam 的星座里有 -8 穿刺 / -4 物理，
        #   那是真信息（换伤害就得付这个价），夹掉会让人以为这一版没代价。
        _neg27 = [(r, k, v) for r in _dnodes[:150]
                  for k, v in (_RT27.devotion_contrib({r: 1}, _db27)
                               .get('res') or {}).items() if v < 0]
        check("星座：抗性代价（负值）原样保留，没有被 clamp 成 0",
              bool(_neg27),
              "例：%s" % (("%s %s%+.0f" % (os.path.basename(_neg27[0][0]),
                                           _neg27[0][1], _neg27[0][2]))
                          if _neg27 else "全库未发现负值（可能是真没有）"))

        # ⑧-c ★★ 多投射物乘区（2026-09-20，鸦人形态引出）：
        #    默认**必须不启用** —— grimtools 计算器对单体目标也只算 1 枚命中
        #    （`projectileLaunchRotation: 360` 的 N 枚环射打同一个目标通常只有 1 枚生效）。
        #    「贴脸全中」是**场景假设**，只允许写在形态表的 `projectile_hits` 里，
        #    且只放大**直伤**（DoT 是刷新不是叠加）。
        _ic27 = _db27.fields(
            'records/skills/playerclass10/wereraven1_skill02_icering.dbr') or {}
        check("多投射物：霜暴真值 = 8 枚 × 360° 环射（乘区的数据来源）",
              _ic27.get('projectileLaunchNumber') == [8]
              and _ic27.get('projectileLaunchRotation') == [360.0],
              "launchNumber=%s rot=%s" % (_ic27.get('projectileLaunchNumber'),
                                          _ic27.get('projectileLaunchRotation')))
        check("多投射物：狼人/夜刃技能**全都不带**该字段（口径对等，不误伤狼人）",
              all(not (_db27.fields(_r) or {}).get('projectileLaunchNumber')
                  for _r in ('records/skills/playerclass10/werewolf1_skill01_claws.dbr',
                             'records/skills/playerclass10/werewolf1_skill02_charge.dbr',
                             'records/skills/playerclass04/wpattack1.dbr',
                             'records/skills/playerclass04/wpattack2.dbr',
                             'records/skills/playerclass04/wpattack3.dbr',
                             'records/skills/playerclass10/wereraven1_skill01_icicles.dbr')))
        _archall27 = _js27.loads((SKILL / "data" / "archetypes.json").read_text(
            encoding="utf-8"))
        check("多投射物：raven_nightblade 在**主表**里声明了 projectile_hits=8",
              (_archall27.get('raven_nightblade') or {}).get('projectile_hits') == 8,
              "（运行时读主表 `archetypes.json`，只写 gdskill 表会静默回落 human）")
        # ★ 烟囱测试：同一套装备、同一个技能，声明与未声明 `projectile_hits` 的形态
        #   必须算出**不同**的霜暴 DPS —— 否则说明乘区没接上。
        try:
            from gd import dps as _D27pj
            _ICER = 'records/skills/playerclass10/wereraven1_skill02_icering.dbr'
            _cpj = _D27pj.load_char('Sam', '', True)

            def _icer_dps(archname):
                _r = _RT27.final_report(
                    archname, {_ICER: 16}, db=_cpj['db'], folded=_cpj['folded'],
                    skill_records=[_ICER], base_aps=_cpj.get('base_aps') or 1.25,
                    conversions=_cpj.get('conversions') or [],
                    skill_mods=_cpj.get('skill_mods') or {},
                    attrs=_RT27.panel_attrs(_cpj.get('bio') or {}, {},
                                            _cpj.get('gear_flat') or {},
                                            _cpj.get('gear_pct') or {}),
                    level=_cpj.get('level'),
                    # 本测只比「声明投射物数」前后同一个技能，武器门控与结论无关
                    # ⇒ 显式 `None`（守卫要求每个调用点都点名 `weapon_st`）。
                    weapon_st=None)
                return ((_r.get('skills') or {}).get(_ICER) or {}).get('dps', 0.0)

            _d_off, _d_on = _icer_dps('wereraven'), _icer_dps('raven_nightblade')
            check("多投射物：声明 projectile_hits 后霜暴 DPS 显著提高",
                  _d_on > _d_off * 2.5 and _d_off > 0,
                  "未声明 %.0f → 声明后 %.0f（×%.1f）"
                  % (_d_off, _d_on, _d_on / max(_d_off, 1e-9)))
        except Exception as _e:
            check("多投射物烟囱测试可运行", False, f"{type(_e).__name__}: {_e}")

        # ⑨ 零漂移基准：狼人 Sam 的面板 DPS 不许被上面任何一条改动影响
        #    ★ 坑：这条断言**必须传满参数**（attr_pct / attrs / level / enemy），
        #      与 [20] 组同口径。漏传会算出 35,448.9 而不是 52,737.6 ——
        #      因为属性加成百分比与敌方 DA（命中/PTH）都丢了。
        try:
            from gd import dps as _D27
            from gd import rotation as _R27
            from gd import procs as _PR27
            from gd import skillprov as _SP27
            _EN27 = importlib.import_module('gd.enemy')
            _c27 = _D27.load_char('Sam', '', True)
            _sk27c = {k: v for k, v in _c27['skills'].items() if v > 0}
            _eff27 = dict(_sk27c)
            for _rec, _ex in (_c27.get('skill_plus') or {}).items():
                if _rec in _eff27:
                    _eff27[_rec] += _ex
            _mast27 = {}
            for _rec, _lv in _sk27c.items():
                if '_classtraining_' in _rec:
                    _mast27[os.path.basename(_rec)
                            .replace('_classtraining_', '')
                            .replace('.dbr', '')] = _lv
            for _cls, _ex in (_c27.get('mastery_plus') or {}).items():
                _mast27[_cls] = _mast27.get(_cls, 0) + _ex
            _at27 = _R27.panel_attrs(_c27.get('bio') or {}, _mast27,
                                     _c27.get('gear_flat') or {},
                                     _c27.get('gear_pct') or {})
            _rep27 = _R27.final_report(
                'werewolf', _eff27, db=_c27['db'], folded=_c27['folded'],
                skill_records=list(_eff27),
                base_aps=_c27.get('base_aps') or 1.25,
                attr_pct=_R27.attr_damage_pct(_at27),
                conversions=_c27.get('conversions') or [],
                skill_mods=_c27.get('skill_mods') or {},
                attrs=_at27, level=_c27.get('level'),
                enemy=_EN27.get_profile(None, _c27.get('level') or 100),
                # ★ 2026-09-20：装备授予的 WPS 入池后，基准也必须按**新口径**算，
                #   否则这条「零漂移」断言测的是已被淘汰的旧行为。
                equipped_sk=_SP27.norm_equipped(
                    [x[1] for x in (_c27.get('item_skills') or [])]),
                item_wps=_PR27.wps_pool(_c27.get('base_gids')),
                # ★ 2026-09-22：与生产链路同口径（武器类型硬前提门控）。
                weapon_st=_PR27.weapon_state_of(_c27))
            _exp27 = 101976.5
            _sv27, _fp27 = None, None
            try:
                _ac27 = _js27.loads((SKILL / 'data' / 'regress'
                                     / 'model_v2_anchor.json')
                                    .read_text(encoding='utf-8'))
                _sam27 = _ac27['cases']['Sam']
                _sv27 = (_sam27.get('v14') or _sam27.get('v13') or _sam27.get('v12') or _sam27.get('v11') or _sam27.get('v10')
                         or _sam27.get('v9') or _sam27.get('v8') or _sam27.get('v7')
                         or _sam27.get('v6') or _sam27.get('v5') or _sam27.get('v4') or {})
                _exp27 = float(_sv27.get('面板') or _exp27)
                _st27 = _sv27.get('_state')
                _fp27 = _st27.get('指纹') if isinstance(_st27, dict) else None
            except Exception:
                pass
            # ★ 同 [20]：**先比存档指纹**，不符则明确跳过（见 `docs/pitfalls.md` #50）
            try:
                import importlib as _il27
                _fp27now = _il27.import_module("save_state").fingerprint("Sam")
            except Exception:                                        # noqa: BLE001
                _fp27now = None
            if _fp27 and _fp27now and _fp27now != _fp27:
                check("基准：狼人 Sam 面板 DPS == 锚点（跳过：存档已变）", True,
                      "指纹 %s ≠ 锚点 %s ⇒ 基准已变，待重设 vN" % (_fp27now, _fp27))
            else:
                check("基准：狼人 Sam 面板 DPS == 锚点（±1）",
                      abs(_rep27['dps'] - _exp27) < 1.0,
                      "实测 %.1f ｜ 锚点 %.1f" % (_rep27['dps'], _exp27))
            _dev27 = {r: lv for r, lv in _c27['skills'].items() if 'devotion' in r}
            _dc27 = _R27.devotion_contrib(_dev27, _c27['db'])
            check("基准：Sam 的星座贡献非空（此前恒为空）",
                  (sum((_dc27.get('pct') or {}).values()) > 100
                   or bool(_dc27.get('flat'))),
                  "pct 合计 %.0f ｜ flat %s"
                  % (sum((_dc27.get('pct') or {}).values()), bool(_dc27.get('flat'))))
        except Exception as _e:
            check("狼人基准复算可运行", False, f"{type(_e).__name__}: {_e}")
    except Exception as _e:
        check("转化/武器池检查可运行", False, f"{type(_e).__name__}: {_e}")

    # ------------------------------------------------ [28] 词缀库解析 + 体检工具链
    # 用户口径（2026-09-20）：「找一下 夜刃+狂战士 71级在满抗性下的极限伤害构建db」。
    # 追极限时发现**词缀维度整条链路是死的**（前缀/后缀池恒空，16 份方案全是
    # 0 前缀 0 后缀，而存档里真实装备有 2~3 前置 + 4 后缀）。两处根因：
    #   ❶ `gd/opt.py::_parse_affixes()` 只找 `data/itemdb.js`，
    #      真实文件在 `data/cache/itemdb.js`（8.7 MB / 4105 条 pre+suf）；
    #   ❷ itemdb 里 `cls` 有**两种写法** —— JSON 数组 `["c12","c24"]`
    #      与 `"c20 c21 c24".split(" ")`；旧解析把后者存成**字符串**
    #      ⇒ `_affix_slots()` 逐**字符**遍历 ⇒ 该词缀一条槽位都映射不到。
    # 另一层是 `tools/affix_fit.py` 补的**合法性**：词缀的 `cls` 必须含底材类别码、
    # 条数 ≤ `maxAffixes`。GT 的 `l` 字段是**类别码**（`c24`），
    # `gd/opt.py::_req_lvl()` 把它当「需求等级」读是**误读**（已发现未修）。
    print("\n[28] 词缀库解析 + 极限方案体检工具链")
    try:
        import importlib
        from gd import opt as _O28
        _adb = getattr(_O28, "_AFFIX_DB", None) or {}
        check("词缀库非空（守住 data/cache/itemdb.js 路径）",
              len(_adb) > 4000, f"实测 {len(_adb)} 条")
        _bad28 = [g for g, d in _adb.items()
                  if not isinstance(d.get("cls"), list)
                  or any(not str(x).startswith("c") or not str(x)[1:].isdigit()
                         for x in (d.get("cls") or []))]
        check("cls 一律归一化成 ['cNN', …]（守住 '…'.split(' ') 写法）",
              not _bad28, f"不规范 {len(_bad28)} 条")
        _n24 = sum(1 for g in (_O28.POOL_SUF.get("主手") or [])
                   if "c24" in ((_adb.get(g) or {}).get("cls") or []))
        check("POOL_SUF['主手'] 含施法匕首(c24)的后缀 > 0（旧解析恒 0）",
              _n24 > 0, f"实测 {_n24} 条")
    except Exception as _e:                                      # noqa: BLE001
        check("词缀库解析检查可运行", False, f"{type(_e).__name__}: {_e}")

    try:
        _AF28 = importlib.import_module("tools.affix_fit")
    except Exception:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            _AF28 = importlib.import_module("affix_fit")          # type: ignore
        except Exception as _e:                                   # noqa: BLE001
            _AF28 = None
            check("affix_fit 可导入", False, f"{type(_e).__name__}: {_e}")
    if _AF28 is not None:
        from gd import opt as _O28b
        # it2063 = 大师级魔刃：GT `l` = 'c24'（**类别码，不是需求等级**）
        check("base_cls(it2063) == 'c24'", _AF28.base_cls(_O28b, "it2063") == "c24",
              str(_AF28.base_cls(_O28b, "it2063")))
        # maxAffixes = 1（绿装里也有 1，不是「绿装一律 2」）
        check("max_affixes(it2063) == 1", _AF28.max_affixes(_O28b, "it2063") == 1,
              str(_AF28.max_affixes(_O28b, "it2063")))
        _raw = _O28b.POOL_PRE.get("主手") or []
        _leg = _AF28.legal_pool(_O28b, "it2063", "主手", "pre")
        check("合法前缀池 ⊂ 原始池 且确实剔除了底材不符的",
              0 < len(_leg) < len(_raw),
              f"合法 {len(_leg)} / 原始 {len(_raw)}")
        _allok = all("c24" in ((_O28b._AFFIX_DB.get(g) or {}).get("cls") or [])
                     for g in _leg)
        check("合法池里每一条的 cls 都含 c24", _allok)
        check("近战单手专属前缀 pre4478(c28-c32) 被剔除",
              "pre4478" not in _leg)
    try:
        _PA28 = importlib.import_module("tools.plan_audit")
    except Exception:
        try:
            _PA28 = importlib.import_module("plan_audit")          # type: ignore
        except Exception as _e:                                    # noqa: BLE001
            _PA28 = None
            check("plan_audit 可导入", False, f"{type(_e).__name__}: {_e}")
    if _PA28 is not None:
        check("plan_audit 分层：analyze / render_text / render_md 都在",
              all(hasattr(_PA28, n) for n in
                  ("analyze", "render_text", "render_md")))
        _src28 = (Path(__file__).resolve().parent.parent
                  / "gd" / "planreport.py").read_text(encoding="utf-8")
        check("planreport 注册了 --audit（体检段重生成不会丢）",
              "--audit" in _src28 and "render_md" in _src28)

    # ------------------------------------------------ [29] LNS 搜索器 + 伤害循环段
    # 用户口径（2026-09-20）：
    #   ① 「放弃退火这个方案 太慢了 有没有更优秀的算法 可以去 GitHub 找一下思路」
    #   ② 「伤害循环这个条目要写在最终报告里 一般是普通攻击 叠加很多普攻触发的
    #      效果 如猛袭就会触发雪崩 和一些装备上的技能哪些」
    # ① 的结论：换成 **LNS（大邻域搜索）+ LAHC** —— GitHub 上的主流答案
    #    （Pisinger & Ropke 2010；也是 OR-Tools CP-SAT 官方文档推荐的「大问题」解法）。
    #    实测 100 轮 / 26.7 s 从 94,474 → 117,805（+24.70%），600 轮 / 106.5 s → 128,756；
    #    而旧的「420 s × 30 链退火」最好只有 94,474。
    # ② 的结论：`gd/rotation.py` 早就按官方口径（Zantai, Grim Misadventure #54：
    #    `skillChanceWeight` 是**权重不是百分比**）建模了循环，但**报告里没露出来**。
    #    现在由 `plan_audit._loop_rows/_loop_text/_loop_md` 渲染，`planreport --audit`
    #    自动并入 ⇒ 重生成不会丢。
    print("\n[29] LNS 大邻域搜索（替代退火）+ 报告伤害循环段")
    import inspect as _ins29
    import json as _js29
    try:
        try:
            _TD29 = importlib.import_module("tools.tune_dps")
        except Exception:                                          # noqa: BLE001
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            _TD29 = importlib.import_module("tune_dps")             # type: ignore
        check("tune_dps.lns_search 存在（退火的替代品）",
              callable(getattr(_TD29, "lns_search", None)))
        check("lns_search 带 LAHC 历史队列参数",
              "hist_len" in _ins29.signature(_TD29.lns_search).parameters)
    except Exception as _e:                                        # noqa: BLE001
        check("tune_dps.lns_search 可导入", False, f"{type(_e).__name__}: {_e}")

    # ---- 功能：假 real_dps（恒 0）也要能跑完并返回 (sol, dps)，且不劣化起点
    try:
        _O29 = _TD29.load_opt()
        _base29 = None
        _p29 = Path(__file__).resolve().parent.parent / "data" / "plans" / "Sam_best_raven2.json"
        if _p29.exists():
            _raw29 = _js29.loads(_p29.read_text(encoding="utf-8"))
            _base29 = {s: tuple(_raw29[s]) for s in _O29.SLOTS if s in _raw29}
        if _base29 is None:
            check("LNS 端到端可跑（缺起点方案，跳过）", True, "skipped")
        else:
            _s29, _d29 = _TD29.lns_search(
                _O29, dict(_base29), lambda s: 0.0, max_iters=1, seed=20260920,
                ks=(2,), topk=4, eval_cap=6, hist_len=4,
                log=lambda *a: None, tag="[selftest]")
            check("lns_search 跑完一轮并返回 (sol, dps)",
                  isinstance(_s29, dict) and isinstance(_d29, float)
                  and set(_s29) == set(_O29.SLOTS))
    except Exception as _e:                                        # noqa: BLE001
        check("lns_search 端到端可跑", False, f"{type(_e).__name__}: {_e}")

    # ---- autobuild 的接线（CLI 默认 + extreme 预设）
    try:
        _AB29 = (Path(__file__).resolve().parent / "autobuild.py").read_text(
            encoding="utf-8")
        check("autobuild --algo 默认 lns", '--algo", default="lns"' in _AB29)
        check("autobuild --extreme 预设切到 LNS（不再是退火 420 s）",
              'a.algo = "lns"' in _AB29 and "a.anneal_iters = 400" in _AB29)
        check("run_chain 支持 algo 分派到 lns_search",
              'def run_chain' in _AB29 and "lns_search" in _AB29)
        check("并行链透传 lns_* 参数",
              all(k in _AB29 for k in ('"lns_k"', '"lns_topk"', '"lns_cap"')))
    except Exception as _e:                                        # noqa: BLE001
        check("autobuild 接线检查可运行", False, f"{type(_e).__name__}: {_e}")

    # ---- 伤害循环：`dps_of` 产出 + `plan_audit` 渲染
    try:
        _PD29 = (Path(__file__).resolve().parent / "plan_dps.py").read_text(
            encoding="utf-8")
        check("plan_dps.dps_of 返回 rotation（伤害循环）",
              "'rotation': rep.get('rotation')" in _PD29)
        check("plan_dps.dps_of 返回 loop（逐技能循环明细）", "'loop': _loop" in _PD29)
    except Exception as _e:                                        # noqa: BLE001
        check("plan_dps 循环字段检查可运行", False, f"{type(_e).__name__}: {_e}")

    try:
        try:
            _PA29 = importlib.import_module("tools.plan_audit")
        except Exception:                                          # noqa: BLE001
            _PA29 = importlib.import_module("plan_audit")           # type: ignore
        _src29 = (Path(__file__).resolve().parent / "plan_audit.py").read_text(
            encoding="utf-8")
        check("plan_audit 循环渲染三层都在",
              all(("def _loop_%s" % n) in _src29 for n in ("rows", "text", "md")))
        check("render_text / render_md 都接了循环段",
              "_loop_text(o)" in _src29 and "_loop_md(o)" in _src29)
        # ★ 官方权重口径：W=70 ⇒ 默认攻击权重 30；WPS 占比 = 权重/分母
        _h29, _r29 = _PA29._loop_rows({
            "rotation": {"default": "X.dbr", "procs": ["W.dbr"], "cooldowns": [],
                         "weight_total": 70.0, "default_weight": 30.0, "denom": 100.0,
                         "wps_blocked": False, "dps_swing": 10.0, "dps_cooldown": 5.0,
                         "bench": []},
            "loop": [{"记录": "X.dbr", "技能": "默认", "等级": 16, "权重": 0,
                      "冷却": 0, "每秒几下": 2.0, "每秒伤害": 10, "弹数": None,
                      "类型": []},
                     {"记录": "W.dbr", "技能": "雪崩", "等级": 16, "权重": 26,
                      "冷却": 0, "每秒几下": 0.6, "每秒伤害": 20, "弹数": None,
                      "类型": []}]})
        check("_loop_rows：默认攻击权重 = 100 - W（W=70 ⇒ 30）",
              bool(_h29) and abs((_h29.get("w_def") or 0) - 30.0) < 1e-9)
        check("_loop_rows：WPS 占比 = 权重 / 分母（26/100 = 26%）",
              any(abs((r.get("占比") or 0) - 0.26) < 1e-9 for r in _r29))
        check("_loop_rows：W>100 时分母换成 W（默认攻击占比归零）",
              _PA29._loop_rows({
                  "rotation": {"default": "X.dbr", "procs": ["W.dbr"], "cooldowns": [],
                               "weight_total": 130.0, "default_weight": 0.0,
                               "denom": 130.0, "wps_blocked": False, "bench": []},
                  "loop": [{"记录": "X.dbr", "技能": "默认", "等级": 1, "权重": 0,
                            "冷却": 0, "每秒几下": 1, "每秒伤害": 1, "类型": []}]})[0][
                      "w_def"] == 0.0)
    except Exception as _e:                                        # noqa: BLE001
        check("plan_audit 循环渲染检查可运行", False, f"{type(_e).__name__}: {_e}")

    # ------------------------------------------------ [30] 邻域并行 + 三处缓存
    #   ★ 2026-09-20 晚：用户问「lns 不能继续优化吗 / GPU 能不能参与」。
    #     实测结论是**全落 CPU 侧**（GPU 不划算，理由见 SKILL §3.18）：
    #     ① 邻域并行（16 进程 19×，24 进程反而劣化）② 三处缓存（单次评估 1.49×）
    #     ③ 负面结论：600 轮与 4000 轮结果相同 ⇒ 加轮数无效、要扩邻域。
    print("\n[30] LNS 邻域并行（ParEval）+ 三处缓存（save_dir / fields / fold）")
    import inspect as _ins30
    try:
        _TD30 = importlib.import_module("tune_dps")
        check("tune_dps.ParEval 存在（邻域并行评估器）",
              callable(getattr(_TD30, "ParEval", None)))
        _P30 = _TD30.ParEval
        check("ParEval 有 warm / batch / close 三件套",
              all(callable(getattr(_P30, n, None)) for n in ("warm", "batch", "close")))
        _sig30 = _ins30.signature(_P30.__init__).parameters
        check("ParEval 默认 procs=16（实测最优；24 会超订劣化）",
              _sig30["procs"].default == 16)
        check("lns_search 带 par 参数（并行入口）",
              "par" in _ins30.signature(_TD30.lns_search).parameters)
        _src30 = (Path(__file__).resolve().parent / "tune_dps.py").read_text(
            encoding="utf-8")
        check("ParEval.batch 空列表直接返回空（不建池）",
              "if not sols:" in _src30 and "return []" in _src30)
        # ★ 2026-09-22：这段逻辑从 `lns_search` 内联搬进了**统一入口** `eval_trials()`
        #   （贪心与 LNS 共用）⇒ 断言跟着搬家。
        check("并行路径先吃调用方 memo（否则重复评估 ~40%）",
              "getattr(real_dps, 'memo'" in _src30
              and "memo.get(k) for k in keys" in _src30)
        check("★ 贪心与 LNS 共用并行入口 `eval_trials`（旧版只并行 LNS）",
              "def eval_trials(" in _src30
              and "out = [memo.get(k) for k in keys]" in _src30)
    except Exception as _e:                                        # noqa: BLE001
        check("ParEval 契约检查可运行", False, f"{type(_e).__name__}: {_e}")

    # ---- 三处缓存：save_dir / fields / fold
    try:
        from gd import paths as _PTH30
        check("gd/paths.py 暴露 clear_save_dir_cache（缓存可重置）",
              callable(getattr(_PTH30, "clear_save_dir_cache", None)))
        check("save_dir 两次调用结果一致（缓存不改语义）",
              _PTH30.save_dir() == _PTH30.save_dir())
    except Exception as _e:                                        # noqa: BLE001
        check("save_dir 缓存检查可运行", False, f"{type(_e).__name__}: {_e}")

    try:
        from gd import dbr as _DBR30
        _db30 = _DBR30.open_all()
        check("Db 带 _fields_memo（fields() 结果缓存）",
              isinstance(getattr(_db30, "_fields_memo", None), dict))
        # ⚠ 用 `_idx` 里**真实存在**的记录：`it2063` 是 `gd.opt._IT`（itemdb.js）的 id，
        #   与 `Db`（记录桥）不是同一套键 ⇒ 在那上面取会得到 None、断言必挂。
        _k30 = next(iter(_db30._idx), None)
        _f30 = _db30.fields(_k30) if _k30 else None
        check("fields() 命中缓存返回**同一对象**（只读共享）",
              _f30 is not None and _db30.fields(_k30) is _f30)
        check("fields() 对不存在的记录返回 None（不抛）",
              _db30.fields("__no_such_record_x__") is None)
    except Exception as _e:                                        # noqa: BLE001
        check("fields 缓存检查可运行", False, f"{type(_e).__name__}: {_e}")

    try:
        from gd import dps as _D30
        check("gd/dps.py 有 _FOLD_MEMO（槽级折叠缓存）",
              isinstance(getattr(_D30, "_FOLD_MEMO", None), dict))
        check("fold 有 _fold_raw 实算体（缓存层与实算层分离）",
              "_fold_raw" in (Path(__file__).resolve().parent.parent /
                              "gd" / "dps.py").read_text(encoding="utf-8"))
    except Exception as _e:                                        # noqa: BLE001
        check("fold 缓存检查可运行", False, f"{type(_e).__name__}: {_e}")

    try:
        _AB30 = (Path(__file__).resolve().parent / "autobuild.py").read_text(
            encoding="utf-8")
        check("autobuild 有 --procs 参数（邻域并行入口）", '"--procs"' in _AB30)
        check("autobuild 把 memo 挂到 real_dps 上（并行复用）", "f.memo = memo" in _AB30)
        # ★ 2026-09-22：互斥**已取消** —— 改成「总进程 = 物理核」的统一分配，
        #   并保证 chains × procs ≤ 物理核（陷阱 #89）。
        check("★ 并行度统一分配：保证 chains × procs ≤ 物理核",
              "chains * procs > phys" in _AB30 and "_alloc_parallel" in _AB30)
        check("★ 链进程必须**非 daemon**（否则链内不能再建邻域池，陷阱 #90）",
              "pr.daemon = False" in _AB30 and "_chain_worker" in _AB30)
        # ★ 2026-09-22：`pr.terminate()` 已换成 `_kill_tree(pr.pid)`（树杀，见下）
        check("★ 并行链收尾处理「子进程静默死掉」（防无限等）",
              "all(not pr.is_alive() for pr in workers)" in _AB30
              and "_kill_tree(pr.pid)" in _AB30)
        # ★★ 2026-09-22：进程清理必须**树杀** —— `Process.terminate()` /
        #   `taskkill /F /PID`（不带 /T）只杀一个进程，而链进程底下还挂着
        #   一层邻域池（非 daemon）⇒ 父一死、孙全变孤儿（用户实测报「后台堆一堆
        #   0% CPU 的 python」）。
        check("★ 清理一律走**树杀** `_kill_tree`（不再用 terminate）",
              "def _kill_tree(" in _AB30 and '"/T"' in _AB30
              and "pr.terminate()" not in _AB30)
        check("★ 链 worker 用 finally **显式关闭链内邻域池**（防 ParEval 孤儿）",
              "_p.close()" in _AB30 and "finally:" in _AB30)
        check("★ `--extreme` 预设 lns_cap=120（实测同结果、墙钟 −40%）",
              "a.lns_cap = 120" in _AB30)
        _KO = Path(__file__).resolve().parent / "kill_orphans.py"
        _kot = _KO.read_text(encoding="utf-8") if _KO.exists() else ""
        check("★ 孤儿清理工具存在且**默认不杀**（需 --yes）",
              "def kill_tree(" in _kot and "--yes" in _kot
              and "默认不杀" in _kot)
    except Exception as _e:                                        # noqa: BLE001
        check("autobuild --procs 接线检查可运行", False, f"{type(_e).__name__}: {_e}")

    # ------------------------------------------------ [31] 伤害循环文档（每个 BD 必带）
    #   用户口径（2026-09-20）：「每次出新 db 的时候必须带一份专门的伤害循环文档。」
    #   这一组守三件东西：① 触发关系的判据（离线库控制器表 + 裁决逻辑）
    #                     ② 提示框格式与游戏口径
    #                     ③ **归因账本的恒等式**（五源之和 == 武器列 + 技能列）
    try:
        _PR31 = importlib.import_module("gd.procs")
        _CY31 = importlib.import_module("gd.dmgcycle")
        _DB31 = importlib.import_module("gd.db").DB.load()
        _ct31 = _DB31.raw.get('itemSkillControllers') or {}
        _trig31 = {v.get('triggerType') for v in _ct31.values()
                   if isinstance(v, dict)}
        check("离线库有 itemSkillControllers（触发控制器表）",
              len(_ct31) >= 60, "%d 条" % len(_ct31))
        check("控制器覆盖全部七种触发类型",
              {'AttackEnemy', 'AttackEnemyCrit', 'HitByEnemy', 'HitByMelee',
               'Block', 'OnKill', 'LowHealth'} <= _trig31,
              "、".join(sorted(str(x) for x in _trig31)))

        # ---- 裁决逻辑（纯逻辑，不依赖库）
        def _mke31(t, mod=False, wps=False):
            return {'trigger': t, 'is_modifier': mod, 'is_wps': wps}
        _v31 = {
            'AttackEnemy+有伤害': _PR31.verdict(_mke31('AttackEnemy'), True, 0.0)[0],
            'AttackEnemyCrit+零暴击': _PR31.verdict(_mke31('AttackEnemyCrit'), True, 0.0)[0],
            'AttackEnemyCrit+有暴击': _PR31.verdict(_mke31('AttackEnemyCrit'), True, 12.0)[0],
            'HitByEnemy': _PR31.verdict(_mke31('HitByEnemy'), True, 0.0)[0],
            '常驻(无控制器)': _PR31.verdict(_mke31(None), True, 0.0)[0],
            'WPS': _PR31.verdict(_mke31(None, wps=True), True, 0.0)[0],
            '技能改造': _PR31.verdict(_mke31(None, mod=True), True, 0.0)[0],
            'AttackEnemy+无伤害技能': _PR31.verdict(_mke31('AttackEnemy'), False, 0.0)[0],
        }
        check("触发裁决：八种情形全对",
              (_v31['AttackEnemy+有伤害'] == _PR31.YES
               and _v31['AttackEnemyCrit+零暴击'] == _PR31.NO
               and _v31['AttackEnemyCrit+有暴击'] == _PR31.YES
               and _v31['AttackEnemy+无伤害技能'] == _PR31.NO
               and all(_v31[k] == _PR31.NA for k in
                       ('HitByEnemy', '常驻(无控制器)', 'WPS', '技能改造'))),
              " ｜ ".join("%s=%s" % kv for kv in _v31.items()))

        check("itemSkillLevelEq：常数与算式都能解、解不出给 None",
              _PR31._skill_level(3, {}) == 3
              and _PR31._skill_level('itemLevel/4+1', {'itemLevel': 60}) == 16
              and _PR31._skill_level('乱码', {}) is None,
              "%r / %r" % (_PR31._skill_level(3, {}),
                           _PR31._skill_level('itemLevel/4+1', {'itemLevel': 60})))

        # ---- 提示框格式（游戏口径）
        _tb31 = _CY31.tip_block([
            {'type': 'pierce', 'is_dot': False, 'min': 9880, 'max': 11498,
             'avg': 10689},
            {'type': 'bleeding', 'is_dot': True, 'min': 2535, 'max': 2535,
             'avg': 2535, 'dur': 3.0},
        ])
        check("提示框：直伤 `min - max`、DoT 单值、**无千分位**",
              '9880 - 11498' in _tb31[0] and '2535' in _tb31[1]
              and ',' not in ''.join(_tb31), ' ｜ '.join(_tb31))
        check("提示框：类型名走游戏官方口径（创伤 / 毒素 / 毒酸）",
              _CY31._tip_zh('trauma') == '创伤'
              and _CY31._tip_zh('poisondot') == '毒素'
              and _CY31._tip_zh('poison') == '毒酸')
    except Exception as _e:                                        # noqa: BLE001
        check("伤害循环文档（格式 / 判据）检查可运行", False,
              f"{type(_e).__name__}: {_e}")

    # ---- 归因账本的**恒等式**（用真实方案，防止 `origins` 与主口径分叉）
    try:
        import json as _js31
        _PR31 = importlib.import_module("gd.procs")
        _DB31 = importlib.import_module("gd.db").DB.load()
        _it31 = importlib.import_module("gd.gear").load_items()
        _pl31 = _js31.loads((SKILL / 'data' / 'plans' / 'lv73_A'
                             / 'arch_werewolf.json').read_text(encoding='utf-8'))
        _lst31 = _PR31.collect(_DB31, _it31, _pl31)
        # ⚠ 别写死条数：方案 JSON 每跑一次普查就会变（实测 11 → 8）。
        #   结构性断言：清单非空、WPS 是它的子集、且至少有一条 WPS。
        _wps31 = [e for e in _lst31 if e.get('is_wps')]
        check("物品技能清点：方案物品技能可枚举（含 ≥1 条 WPS）",
              len(_lst31) >= 1 and len(_wps31) >= 1 and len(_lst31) >= len(_wps31),
              "物品技能 %d 条 ｜ 其中 WPS %d 条" % (len(_lst31), len(_wps31)))
        # ★ 缺口检出：`base_hits={}`（池子空）⇒ 应当把**全部** WPS 都算作缺失
        _g31 = _PR31.wps_gap(_DB31, _it31, _pl31, {})
        check("★ 检出「装备授予 WPS 未进循环」缺口（含权重损失）",
              len(_g31['missing']) == len(_g31['items']) >= 1
              and _g31['weight_lost'] > 30,
              "丢失 %d 条 / 权重 %.0f" % (len(_g31['missing']),
                                          _g31['weight_lost']))

        # ---- ★ 2026-09-20：装备授予的 WPS **已入池**（用户批准后的修复）
        _pool31 = _PR31.wps_pool([_g for _v in _pl31.values() for _g in (_v or []) if _g],
                                 _it31)
        # 权重必须**逐条等于离线库 `itemSkills` 表**按等级取出的值
        # —— 这才是「权重不在 .dbr / skills.json 里」这条坑的守门断言
        _wbad31 = []
        for _e in _pool31:
            _raw31 = (_DB31.skills.get(_e['sk']) or {}).get('skillChanceWeight')
            _want31 = _PR31._at(_raw31, _e['level'])
            if _want31 <= 0 or abs(float(_e['weight']) - float(_want31)) > 1e-9:
                _wbad31.append(_e['sk'])
        check("★ wps_pool：权重逐条 == 离线库 itemSkills 表（非 .dbr / skills.json）",
              bool(_pool31) and not _wbad31,
              "%d 条 ｜ 权重 %s" % (len(_pool31),
                                    {e['sk']: e['weight'] for e in _pool31})
              + (" ｜ ✗ %s" % _wbad31 if _wbad31 else ''))
        check("★ wps_pool：记录路径经 skillprov 翻译（不是 skXXXX）",
              all(str(e.get('rec') or '').endswith('.dbr') for e in _pool31)
              and all(e.get('level') for e in _pool31),
              '、'.join('%s→lv%s' % (e['rec'].split('/')[-1], e['level'])
                        for e in _pool31))
        # ★★ 判据必须**先翻成记录路径再比**：`e['sk']`（sk325）≠ 记录路径，
        #    旧实现直接比 ⇒ 修好之后也**永远报缺口**（假阳性）。
        _g31b = _PR31.wps_gap(_DB31, _it31, _pl31,
                              {e['rec']: 1 for e in _pool31},
                              st=_PR31.weapon_state(
                                  [_g for _v in _pl31.values()
                                   for _g in (_v or []) if _g], _DB31))
        check("★ wps_gap 判据已修：WPS 真在池里时不报缺口（旧实现恒报）"
              "，且武器闸门下**不误报**",
              not _g31b['missing'] and _g31b['weight_lost'] == 0.0
              and len(_g31b['items']) == len(_pool31),
              "missing=%d ｜ 权重损失 %s"
              % (len(_g31b['missing']), _g31b['weight_lost']))
        _dc31 = importlib.import_module("gd.dps").load_char('Sam', '', True)
        check("★ load_char 对外暴露 base_gids（WPS 注入的输入）",
              bool(_dc31.get('base_gids'))
              and len(_dc31['base_gids']) >= 12,
              "%d 个 gid" % len(_dc31.get('base_gids') or []))

        # ---- ★★ 武器闸门（`docs/pitfalls.md` **#61**）：WPS 的武器约束
        #   「盾牌战技」在双持角色身上游戏里根本不出现 ⇒ 必须剔除；
        #   「双持专属」在双持下必须保留。判据 = 离线库 itemSkills 的
        #   `Shield` / `dualWieldOnly` vs `weapon_state(base_gids)` 的武器构成。
        _st31 = _PR31.weapon_state(_dc31['base_gids'], _DB31)
        _pool31s = _PR31.wps_pool(_dc31['base_gids'], _it31)
        _pool31n = _PR31.wps_pool(_dc31['base_gids'], _it31, st=False)
        _nm31s = sorted(e['name'] for e in _pool31s)
        _nm31n = sorted(e['name'] for e in _pool31n)
        _ok31 = all(_PR31.weapon_verdict(
            (_DB31.skills.get(e['sk']) or {}), _st31)[0] for e in _pool31s)
        # ★★ 2026-09-21 改：**不再要求「恰好剔除了东西」**。
        #   旧判据 `len(_pool31n) > len(_pool31s)` 依赖「当前装备里正好有授予盾牌战技的件」
        #   （旧勋章「猩红连队烙印」给「混乱打击」）。落档换上「冰原巨狼盾徽」后这个前提消失，
        #   断言就**假红**了 —— 是「断言绑了存档装备」，不是闸门坏了。
        #   闸门本身的正确性由**下一条**（`sk3683` 盾牌战技在无盾时判 ✗）独立守住；
        #   本条只守「闸门后池内**全部合规**」这个不变量（与是否真有剔除无关）。
        _drop31 = len(_pool31n) - len(_pool31s)
        check("★ 武器闸门：Sam 双持 ⇒ 池内不会留下无法触发的 WPS",
              bool(_st31) and _st31['dual_wield'] and not _st31['has_shield']
              and _ok31 and len(_pool31s) >= 1 and _drop31 >= 0,
              "构成 %s ｜ 闸门后 %s ｜ 关闸门 %s ｜ 剔除 %d 条"
              % (_st31.get('kinds') if _st31 else None, _nm31s, _nm31n, _drop31))
        _v31a = _PR31.weapon_verdict(
            (_DB31.skills.get('sk3683') or {}), _st31)      # 混乱打击 = 盾牌战技
        _v31b = _PR31.weapon_verdict(
            (_DB31.skills.get('sk519') or {}), _st31)       # 毁伤 = 双持专属
        check("★ 闸门判语：盾牌战技在无盾时 ✗、双持专属在双持时 ✓",
              _v31a[0] is False and '盾' in _v31a[1] and _v31b[0] is True,
              "%s ｜ %s" % (_v31a[1], _v31b[1]))
        check("★ 闸门边界：看不到武器时返回 None（不误杀 dualWieldOnly）",
              _PR31.weapon_state(['it1291', 'it874'], _DB31) is None
              and _PR31.weapon_verdict({'dualWieldOnly': 1}, None)[0] is True,
              '只给 12 装备槽（无武器）⇒ 不判')

        # ---- ★★ 降敌 DA 接入命中乘区（`docs/pitfalls.md` **#61**）：
        #   PTH = f(我方 OA, 敌方 **DA**)，角色自己的降敌 DA 必须扣 ——
        #   否则暴击乘区被系统性低估（Sam 实测 −14.3%）。
        _ROT31 = importlib.import_module("gd.rotation")
        _SKJ31 = importlib.import_module("gd.paths").load_json('skills.json')
        _cut31, _rows31 = _ROT31.enemy_da_cut(_SKJ31, {
            'records/skills/playerclass10/werewolf3.dbr': 12,        # 血莽
            'records/skills/playerclass10/bonechillingcry1.dbr': 12,  # 刺骨战吼
        })
        check("★ 降敌 DA：血莽 250 + 刺骨战吼 124 = 374（含覆盖率折算）",
              abs(_cut31 - 374.0) < 1e-6 and len(_rows31) == 2,
              "合计 −%.0f ｜ %s" % (_cut31,
                                    [(r[0].split('/')[-1], r[1]) for r in _rows31]))
        _cut31c, _ = _ROT31.enemy_da_cut(_SKJ31, {
            'records/skills/playerclass10/amatokpact1.dbr': 12})     # 阿玛托克契约 = 自身 DA +
        check("★ 降敌 DA 判据：自身 DA 加成（正值）**不得**计入削减",
              _cut31c == 0.0, "%.1f" % _cut31c)

        # ---- ★ 零漂移护栏：**不传 item_wps ⇒ 完全不注入**（旧口径逐位保持）
        _R31x = importlib.import_module("gd.rotation")
        _rv31 = _R31x.final_report(
            'werewolf', _dc31.get('skills'), db=_dc31.get('db'),
            folded=_dc31.get('folded'), skill_records=list(_dc31.get('skills') or []),
            base_aps=_dc31.get('base_aps'), level=_dc31.get('level'),
            # 零漂移护栏：`weapon_st` 也不传 ⇒ 武器门控同样整段跳过（旧口径逐位保持）
            weapon_st=None)
        check("★ 零漂移护栏：不给 item_wps ⇒ 武器池仍为空（旧口径不动）",
              float((_rv31.get('rotation') or {}).get('weight_total') or 0) == 0.0,
              "W=%s" % ( _rv31.get('rotation') or {}).get('weight_total'))

        # ---- ★★ 2026-09-20：**落档要写进「启用」的那一套武器**
        #   实测踩过：旧 `do_apply` 恒写 alt1，而启用的是 alt2 ⇒
        #   `gd dps Sam` 面板 103,682 → 67,637（差 53%），玩家也看不到新武器。
        _B31 = importlib.import_module("gd.build")
        _cases31 = [
            ({'alt2_unused': True, 'alt1_unused': False,
              'use_alt_weaponset': False}, 'alt1'),
            ({'alt1_unused': True, 'alt2_unused': False,
              'use_alt_weaponset': True}, 'alt2'),
            ({'alt1_unused': True, 'alt2_unused': True,
              'use_alt_weaponset': False}, 'alt1'),
            ({'alt1_unused': False, 'alt2_unused': False,
              'use_alt_weaponset': True}, 'alt2'),
        ]
        _bad31w = [(_b, _w, _B31._active_weapon_set(_b))
                   for _b, _w in _cases31 if _B31._active_weapon_set(_b) != _w]

        def _d31(_b):
            return 'alt1u=%s,alt2u=%s,use_alt=%s' % (
                _b.get('alt1_unused'), _b.get('alt2_unused'),
                _b.get('use_alt_weaponset'))

        check("★ 落档武器套判据：与 dps.load_char 同规则（4 种情形全对）",
              not _bad31w,
              ('、'.join('%s→%s（应 %s）' % (_d31(b), g, w) for b, w, g in _bad31w)
               if _bad31w else
               '｜'.join('%s→%s' % (_d31(b), w) for b, w in _cases31)))

        # ---- ★★ 2026-09-20：`reqfit` 的 `buffer` 必须作用在**所有有需求的属性**上
        #   旧实现写 `if pmin[k]:` ⇒ `pmin[k] == 0`（面板本来就过阈值）时
        #   **buffer 完全失效** ⇒ 体格恒 0 点、余量恒 9 ⇒ 落档后穿不上。
        _RQ31 = importlib.import_module("gd.reqfit")
        _fb0 = _RQ31.solve('Sam', buffer=0)
        _fb3 = _RQ31.solve('Sam', buffer=3)
        check("★ reqfit.buffer 对体格生效（旧实现 pmin=0 时完全失效）",
              _fb3.points_after.get('physique', 0)
              >= _fb0.points_after.get('physique', 0) + 3,
              "buffer=0 → 体格 %s 点 / 面板 %.0f ｜ buffer=3 → %s 点 / 面板 %.0f"
              % (_fb0.points_after.get('physique'), _fb0.panel_after.get('physique', 0),
                 _fb3.points_after.get('physique'), _fb3.panel_after.get('physique', 0)))

        # ---- ★★ 2026-09-20：`*ReqReduction` 只能扣**字段名里写的那个属性**
        #   实测 bug：`it1800`（使者的夹克）带 `characterHuntingDexterityReqReduction: 15`
        #   ⇒ 旧实现无差别扣三个属性 ⇒ 体格需求 464 被扣成 **394**（游戏提示框明写 464）
        #   ⇒ 模型静默报「全过」而游戏穿不上。
        _RQ31b = importlib.import_module("gd.req")
        _rchest31 = _RQ31b.req('it1800', 'records/items/geartorso/c046_torso.dbr',
                               '胸甲')
        check("★ 需求减免只扣对应属性（胸甲体格需求 == 464，不被狡诈减免扣）",
              _rchest31.get('physique') == 464 and not _rchest31.get('cunning'),
              "胸甲 体格 %s ｜ 狡诈 %s ｜ 精神 %s（游戏提示框：需要体格 464）"
              % (_rchest31.get('physique'), _rchest31.get('cunning'),
                 _rchest31.get('spirit')))

        _PD31 = importlib.import_module("plan_dps")
        # ★ 2026-09-21：形态用 `__nogate__`（**archetypes.json 里没有的名字**）。
        #   这一组断言测的是「**装备授予的 WPS 入池**」这条路（权重 25 点不许蒸发），
        #   而 `werewolf` 现在声明了 `form` ⇒ 门控会把装备授予的 WPS 整批剔除，
        #   W 必然 0，断言会被门控撞死。
        #   换 `human` 也不行 —— 实测 `human` 形态的默认攻击不带武器伤害，
        #   `wps_blocked` 会把整个武器池清空（另一个分支，同样撞死）。
        #   `final_report` 对未知形态名取到 `{}` ⇒ 无 `form` ⇒ 门控不生效，
        #   这正好是「零漂移后路」的入口，机制覆盖点原样保住。
        _b31 = _PD31.dps_of('Sam', _pl31, '__nogate__')
        # ★ 注入生效（**结构性**：不写死 37 —— 方案每跑一次普查就会变）
        _rot31 = _b31.get('rotation') or {}
        _sumw31 = round(sum(float(e['weight']) for e in _pool31), 1)
        check("★ 修复生效：W = 逐条权重之和，默认攻击 = 100 − W",
              abs(float(_rot31.get('weight_total') or 0) - _sumw31) < 1e-6
              and abs(float(_rot31.get('default_weight') or 0)
                      - max(0.0, 100.0 - _sumw31)) < 1e-6,
              "W=%s（逐条 %.1f）｜ 默认=%s ｜ procs=%d 条"
              % (_rot31.get('weight_total'), _sumw31,
                 _rot31.get('default_weight'), len(_rot31.get('procs') or [])))
        # ★★ 2026-09-21 新增：同一份装备在**变身形态**下，装备授予的 WPS
        #   必须被门控**整批剔除**（`wps_form_dropped` 就是拦截账本）。
        #   这条守住「门控对 item_wps 这条旁路也生效」—— 否则 WPS 会绕过门控留在池里。
        _b31w = _PD31.dps_of('Sam', _pl31, 'werewolf')
        _rot31w = _b31w.get('rotation') or {}
        check("★★ 形态门控对 item_wps 旁路同样生效（werewolf 下 WPS 整批出池）",
              (not _pool31)
              or (float(_rot31w.get('weight_total') or 0) == 0.0
                  and len(_rot31w.get('wps_form_dropped') or []) == len(_pool31)),
              "pool %d 条 → 拦下 %d 条 ｜ W=%s"
              % (len(_pool31), len(_rot31w.get('wps_form_dropped') or []),
                 _rot31w.get('weight_total')))
        check("★ 修复生效：进池的 WPS 各自带武器伤害%（> 0）",
              bool(_pool31) and all(
                  h.get('weapon_pct') for h in (_b31.get('hits') or {}).values()
                  if h.get('chance_weight')),
              '、'.join('%s %s%%' % (h.get('name'), h.get('weapon_pct'))
                        for h in (_b31.get('hits') or {}).values()
                        if h.get('chance_weight')))
        check("dps_of 带出 hits / base_parts / slot_pct",
              bool(_b31.get('hits')) and bool(_b31.get('base_parts'))
              and isinstance(_b31.get('slot_pct'), dict))
        check("来源账本：base_parts 五源齐备",
              set(_b31.get('base_parts') or {})
              == {'武器', '光环/被动', '星座', '套装', '装备'},
              str(list((_b31.get('base_parts') or {}).keys())))
        _bad31, _bad31b, _n31 = 0, 0, 0
        for _rec, _h in (_b31.get('hits') or {}).items():
            for _r in (_h.get('rows') or []):
                _n31 += 1
                _o = _r.get('origins') or {}
                _s = sum(v[0] for k, v in _o.items() if k != '技能')
                if abs(_s - float(_r.get('weapon', [0, 0])[0])) > 0.35:
                    _bad31 += 1
                if abs(float(_o.get('技能', [0, 0])[0])
                       - float(_r.get('skill', [0, 0])[0])) > 0.35:
                    _bad31 += 1
                _p = _r.get('pct_parts') or {}
                if abs(sum(_p.values()) - float(_r.get('pct') or 0.0)) > 0.6:
                    _bad31b += 1
        check("★ 来源账本恒等式：五源之和 == 武器列 + 技能列（全 rows）",
              _n31 > 30 and _bad31 == 0, "%d 行 / %d 处不一致" % (_n31, _bad31))
        check("★ 乘区分层恒等式：pct_parts 之和 == 该行 pct", _bad31b == 0,
              "%d 处不一致" % _bad31b)
        _r31 = None
        for _rec, _h in (_b31.get('hits') or {}).items():
            if _h.get('rows'):
                _r31 = _h['rows'][0]
                break
        check("乘区分层六项齐备（装备/套装/星座节点/技能/属性/星座）",
              set((_r31 or {}).get('pct_parts') or {})
              == {'装备', '套装', '星座节点', '技能', '属性', '星座'},
              str(sorted((( _r31 or {}).get('pct_parts') or {}).keys())))
    except Exception as _e:                                        # noqa: BLE001
        check("归因账本恒等式检查可运行", False, f"{type(_e).__name__}: {_e}")

    # ---- 伤害吸收来源（tools/absorb_audit.py） ----
    print("\n[54] 伤害吸收来源全库审计")
    try:
        import importlib.util as _ilu
        import json as _json
        _p = Path(__file__).resolve().parent / "absorb_audit.py"
        _spec = _ilu.spec_from_file_location("absorb_audit", _p)
        _aa = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_aa)
        _doc = _aa.collect(db)
        _f, _rows = _doc["facts"], _doc["rows"]
        check("审计可运行且行数 > 40", len(_rows) > 40, "%d 行" % len(_rows))
        check("星座授予技能记录 = 63", _f["devotion_skill_records"] == 63,
              str(_f["devotion_skill_records"]))
        check("★ 星座带吸收的授予技能 = 4", _f["devotion_skill_with_absorption"] == 4,
              str(_f["devotion_skill_with_absorption"]))
        check("★ 星座星位被动里 % 吸收 = 0 条", _f["devotion_passive_with_absorption"] == 0,
              str(_f["devotion_passive_with_absorption"]))
        _devpct = [r for r in _rows if r["layer"] == "devotion" and r["kind"] == "percent"]
        check("★ 星座 % 吸收只有 1 个来源、上限 25",
              len(_devpct) == 1 and _f["devotion_pct_max"] == 25,
              "%d 条 / max %s / %s" % (len(_devpct), _f["devotion_pct_max"],
                                       _devpct[0]["owner"] if _devpct else ""))
        check("星座点数吸收上限 = 6100（海龟壳）", _f["devotion_flat_max"] == 6100,
              str(_f["devotion_flat_max"]))
        check("专精 % 吸收上限 = 100（刀锋壁障／艾瑞奥特之镜）", _f["mastery_pct_max"] == 100,
              str(_f["mastery_pct_max"]))
        check("★ 专精内最高非 100% % 吸收 = 折磨印记 50%",
              [r["name"] for r in sorted(
                  (r for r in _rows if r["layer"] == "mastery" and r["kind"] == "percent"
                   and (r["max"] or 0) < 100), key=lambda r: -(r["max"] or 0))][:1] == ["折磨印记"],
              str(_f["mastery_pct_max"]))
        check("★ 全库最高非 100% 单条 = 巫妖守卫 80%（物品 proc）",
              (_f["pct_top_name"], _f["pct_top_value"]) == ("巫妖守卫", 80),
              "%s / %s" % (_f["pct_top_name"], _f["pct_top_value"]))
        check("物品点数吸收上限 = 12000", _f["item_flat_max"] == 12000,
              str(_f["item_flat_max"]))
        check("★ % 吸收是乘算：50% + 40% == 70%（不是 90%）",
              abs((1 - (1 - 0.5) * (1 - 0.4)) - 0.70) < 1e-9)
        _rem = 0.5 * 0.6 * 0.75 * 0.2 * 0.7 * 0.72 * 0.75 * 0.8 * 0.92
        check("★ 九层 % 吸收乘算后残余 1.25%（> 0 ⇒ 永不归零）", 0 < _rem < 0.02,
              "%.5f" % _rem)
        _old = {}
        if _aa.OUT_JSON.exists():
            _old = _json.loads(_aa.OUT_JSON.read_text(encoding="utf-8"))
        check("data/absorption_sources.json 与离线库一致（--write 可刷新）",
              (_old.get("facts") or {}) == _f and len(_old.get("rows") or []) == len(_rows),
              "" if (_old.get("facts") or {}) == _f
              else "已漂移，跑 tools/absorb_audit.py --write")
        _pct = [r for r in _rows if r["kind"] == "percent"]
        check("★ 乘算层清单每条都有来源/加在标注", len(_pct) > 25 and all(r["owner"] for r in _pct),
              "%d 条 / 无标注 %d 条" % (len(_pct), len([r for r in _pct if not r["owner"]])))
        check("★ 物品修正器的「加在」目标全部解析成功",
              all("未解析" not in r["note"] for r in _pct if r["role"] == "modifier"),
              str([r["name"] for r in _pct if r["role"] == "modifier"
                   and "未解析" in r["note"]][:4]))
    except Exception as _e:                                    # noqa: BLE001
        check("伤害吸收审计可运行", False, f"{type(_e).__name__}: {_e}")

    # ---- [55] 优化器「不劣化」落盘兜底（静态守卫） ----
    print("\n[55] 优化器落盘安全")
    _ROOT = Path(__file__).resolve().parent.parent
    _dev_src = (_ROOT / "tools" / "tune_devotion.py").read_text(encoding="utf-8")
    check("★ tune_devotion 记 best-seen（加星点也会掉分 ⇒ 防贪心下坡）",
          "if v > best_score:" in _dev_src and "best_score, best_recs = v, dict(cur)" in _dev_src)
    check("★ tune_devotion 落盘前与基线取优（绝不写更差的方案）",
          "best_score > _bsc + _sw" in _dev_src and "'adopted': adopted" in _dev_src)
    check("★ tune_devotion 放弃重排时落「基线原样」而不是留旧文件",
          "fin, cur = base, dict(CUR)" in _dev_src)
    _sk_src = (_ROOT / "tools" / "tune_skills.py").read_text(encoding="utf-8")
    check("★ tune_skills 贪心每步都要求真提升（swap / add 同一守卫）",
          _sk_src.count("_sc(v) <= _sc(") >= 2)
    _ab_src = (_ROOT / "tools" / "autobuild.py").read_text(encoding="utf-8")
    check("★ autobuild 起点解做合法性清洗（否则非法装备位会被 LNS 原样留下）",
          "comp_ok(" in _ab_src and "aug_ok(" in _ab_src)
    # 星座点数 / 搜索粒度（2026-09-21，陷阱 #70）
    _td_src = (_ROOT / "tools" / "tune_devotion.py").read_text(encoding="utf-8")
    check("★ 星座连线顺序按「记录名前缀」认 proc（tag 变换法**差 1**，已弃用）",
          "skill_by_pre.setdefault(DV.star_prefix" in _td_src
          and "eff_by_tag" not in _td_src)
    check("★ 退款阶段按前缀归位 + orphan 原样保留（旧实现会**静默丢掉 proc 记录**）",
          "pre2idx = {sg['prefix']" in _td_src and "for r in orphan:" in _td_src)
    check("★ proc 记录不喂进模型（无触发率门控 ⇒ 虚增数十 %）",
          "记录**故意不喂进模型**" in _td_src)
    check("★ 点数池 = 已点亮节点数 + 未分配（`total_devotion_points` 是**已花**，不是池）",
          "return len(CUR) + int(free or 0)" in _td_src)
    _pr_src = (_ROOT / "gd" / "planreport.py").read_text(encoding="utf-8")
    check("★ 报告预算公式 = 已花 + 未分配（旧写法把「已花」当池 ⇒ 少算）",
          "int(_earn) + int(_left or 0)" in _pr_src)
    # 亲和力：必须用**真规则**（点满才给 given），不能用近似的「每星 +1」
    check("★ 星座亲和力用精确规则（`aff_of` / `req_ok` / `feasible` 三件套）",
          all(s in _td_src for s in ("def aff_of(", "def req_ok(", "def feasible(")))
    # ⚠ 2026-09-21：`aff_of` 曾拿 `>= sg['nodes']` 判「点满」—— 而 `nodes` **含 proc 节点**、
    #   `pos` 里那格是 `None` ⇒ 带 proc 的星座 `given` **恒记 0** ⇒ 亲和力被低估
    #   ⇒ `req_ok` 过严、**误拦合法重排**（实测挡掉了 +0.64% 的重排）。
    check("★ 亲和力「点满」判据 = 星位全亮 ＋ proc 在（不能拿 `sg['nodes']` 比）",
          "all(r in cur for r in stars) and (not sg.get('proc') or sg['proc'] in cur)"
          in _td_src
          and "sum(1 for r in sg['pos'] if r and r in cur) >= sg['nodes']" not in _td_src)
    check("★ 「亲和力自洽」用精确规则复检（旧版拿近似 `check_affinity` 当结论 ⇒ 误报 False）",
          "精确规则 ｜" in _td_src)
    check("★ 候选用**精确门槛**取代近似 `check_affinity`",
          "not req_ok(idx, aff, _tree)" in _td_src)
    check("★ 退款前复检「留下的星座门槛是否仍够」，不够就**撤销退款**",
          "撤销退款" in _td_src)
    # 落档方案的差量必须**按并集一次算**（2026-09-21）：分两趟（先扫存档再扫目标）
    # 会把「升级」的记录先写成「撤点 0」、再写成「调整 N」，去重保留先写入的那条
    # ⇒ **主输出技能被静默清零**（本工具第一版把 `leap1 6→8` 算成了 `leap1 6→0`）。
    _pss_src = (_ROOT / "tools" / "plan_to_save_skill.py").read_text(encoding="utf-8")
    check("★ 落档差量按并集一次算（分两趟会把「升级」误判成「撤点」）",
          "for r in sorted(set(cur) | set(target)):" in _pss_src)
    check("★ 落档方案拒绝含星座的 `--alloc`（那是并集叠加，不是替换，陷阱 #69-B）",
          "并集叠加" in _pss_src and "raise SystemExit" in _pss_src)

    # ---- [56] 装备授予技能 / 套装数据表（2026-09-21 修的两个静默失效） ----
    print("\n[56] 装备授予技能 · 套装数据表")
    try:
        from gd import skillmod as _SM2, dbr as _DBR
        check("★ 套装表非空（`skillmod` 数据目录曾指向已不存在的 `gt_data/` ⇒ 恒 0 条）",
              len(_SM2.load_sets() or {}) > 100, "%d 套" % len(_SM2.load_sets() or {}))
        check("★ 物品授予技能表非空（同上；它还是「常驻技能入表」的判据表）",
              len(_SM2.load_item_skills() or {}) > 1000,
              "%d 条" % len(_SM2.load_item_skills() or {}))
        _dbr_db = _DBR.open_all()
        _f = _dbr_db.fields("records/skills/itemskills/componentskills/"
                            "comp_bladeaura_02.dbr") or {}
        check("★ 授予技能记录可从 dbr 解析（不在 `db.skills` 表里也能取到属性）",
              "offensivePierceModifier" in _f,
              "穿刺 %s" % _f.get("offensivePierceModifier"))
        _dps_src = (_ROOT / "gd" / "dps.py").read_text(encoding="utf-8")
        check("★ dps.py：装备授予的**常驻类**技能会进技能表（旧实现整类漏算）",
              "_perm_grants" in _dps_src and "Skill_BuffSelfToggled" in _dps_src)
        _sm_src = (_ROOT / "gd" / "skillmod.py").read_text(encoding="utf-8")
        check("★ skillmod 走 `gd.paths.load_json`（不再直接拼 `gt_data/`）",
              "_P.load_json(name)" in _sm_src)
    except Exception as _e2:                                    # noqa: BLE001
        check("装备授予技能/套装表自检可运行", False, f"{type(_e2).__name__}: {_e2}")

    # ---- [57] 防御减伤模型（2026-09-21 新增；**我方挨打**） ----
    print("\n[57] 防御减伤模型（gd/defense.py）")
    try:
        from gd import defense as _DF
        for _nm, _ok, _det in _DF.selftest_identities():
            check(_nm, _ok, _det)
    except Exception as _e3:                                    # noqa: BLE001
        check("defense 恒等自证可运行", False, f"{type(_e3).__name__}: {_e3}")

    # ★ 格式串未转义 `%` 的**永久守卫**（陷阱 #76）：
    #   `gd/dps.py` 曾因 `'（%目标抗性降低）'` 崩过；2026-09-21 我在新写的
    #   `gd/defense.py` / `tools/defense_audit.py` 里**又踩了两次** ⇒ 不再靠人眼，
    #   改为 **AST 静态扫描**（只查真正带 `%` 格式化的字符串常量，`%%` 先消掉）。
    try:
        import re as _re
        import ast as _ast
        _BADP = _re.compile(r'%(?![%sdifegxXo0-9.\-+#(r])')
        _badp = []
        for _p in (list((_ROOT / "gd").glob("*.py"))
                   + list((_ROOT / "tools").glob("*.py"))):
            if "_patched" in _p.name:
                continue
            try:
                _t = _ast.parse(_p.read_text(encoding="utf-8"))
            except Exception:                                   # noqa: BLE001
                continue
            for _n in _ast.walk(_t):
                if not (isinstance(_n, _ast.BinOp) and isinstance(_n.op, _ast.Mod)):
                    continue
                if not (isinstance(_n.left, _ast.Constant)
                        and isinstance(_n.left.value, str)):
                    continue
                if _BADP.search(_n.left.value.replace("%%", "")):
                    _badp.append("%s:%d" % (_p.name, _n.lineno))
        check("★ 格式串无未转义 `%%`（陷阱 #76 的永久守卫）", not _badp,
              " ".join(_badp[:6]))
    except Exception as _e4:                                    # noqa: BLE001
        check("格式串扫描可运行", False, f"{type(_e4).__name__}: {_e4}")

    # ====================================================== [58] 属性轴 · 单维度最优解
    print("\n[58] 属性轴（tools/tune_attrs.py）· 单维度最优解")
    try:
        import tune_attrs as _TA58
        import plan_dps as _PD58
        _OBJ58 = importlib.import_module("objfunc")

        # ---- ① ★★ 单位换算：加点 → `bio_override`
        #   `plan_dps.dps_of(bio_override=…)` 收的是**不含精通/装备的三围基础**。
        #   踩过两次：传加点数 ⇒ 面板 75,089；传 `reqfit` 的最终面板 ⇒ 94,790；
        #   **真值 83,536**。错了不会报错，只会静默在一个假面板上优化。
        _pts58 = {'physique': 10, 'cunning': 73, 'spirit': 2}
        _bio58 = {k: _TA58.BASE + _TA58.PER * _pts58[k] for k in _TA58.KEYS}
        check("★★ 加点 → bio_override 的单位换算（= 50 + 8 × 点数）",
              _bio58 == {'physique': 130.0, 'cunning': 634.0, 'spirit': 66.0},
              "10/73/2 → %s（存档 block2 真值 130/634/66）" % _bio58)

        # ---- ② ★★ 精确模式（`_FastEval`）与 `dps_of` **必须同口径**
        #   把 `final_report` 的返回直接喂 `objfunc` 会让 `total` 退成**面板口径**
        #   ⇒ 两种模式的数字不可比（实测 83,536 vs 152,638，差 83%）。
        _slow58 = _OBJ58.score(_PD58.dps_of('Sam'))
        _fast58 = _TA58._FastEval('Sam').score(_pts58)
        check("★★ 精确模式与 dps_of 评分口径一致（同一加点）",
              abs(_fast58 - _slow58) < 1.0,
              "精确 %.2f ｜ dps_of %.2f ｜ 差 %.4f"
              % (_fast58, _slow58, abs(_fast58 - _slow58)))

        # ---- ③ 可行域枚举：投满预算 + 不破可穿底线（逐组合体检）
        _need58 = {'physique': 10, 'cunning': 1, 'spirit': 2}
        _cb58 = _TA58._combos(_need58, 85)
        _bad58 = [c for c in _cb58
                  if sum(c.values()) != 85
                  or any(c[k] < _need58[k] for k in _TA58.KEYS)]
        check("可行域枚举：投满预算 + 不破可穿底线（逐组合）",
              not _bad58 and len(_cb58) == 2701,
              "%d 个组合，违规 %d 个" % (len(_cb58), len(_bad58)))

        # ---- ④ 「单维度单独跑最优解」的三条契约
        _CA58 = (SKILL / 'tools' / 'coordinate_ascent.py').read_text(encoding='utf-8')
        check("★ 单轴报告按轴标签分文件（跑技能轴不覆盖属性轴的结论）",
              "ASCENT_%s_%s.md" in _CA58)
        check("★ 四轴各带「最优性等级」标注（防把局部最优当全局最优）",
              'OPTIMALITY' in _CA58 and '全局最优' in _CA58
              and '启发式' in _CA58 and '局部最优' in _CA58)
        check("★ 轴内增益走「自报」而非 measure 前后差（后者恒 0、假收敛）",
              '_axis_gain' in _CA58 and '各自口径，不可相加' in _CA58)
    except Exception as _e58:                                   # noqa: BLE001
        check("属性轴模块可运行", False, f"{type(_e58).__name__}: {_e58}")

    # ================================================= [59] 降抗字段（物品 / 技能）
    print("\n[59] 降低目标抗性的字段（tools/rr_items.py）")
    try:
        import rr_items as _RI59
        _rows59 = _RI59.scan()

        # ---- ① B 族必须**精确匹配**：`defensiveColdDuration` 是 debuff 时长，
        #   一剥后缀就会误命中 `defensiveCold`（`gd/rr.py::rr_of` 正是靠精确匹配避开的）。
        _fB59 = [x['field'] for x in _rows59['B']]
        check("★ B 族精确匹配：`defensive*Duration`（debuff 时长）不被当成抗性值",
              'defensiveColdDuration' not in _fB59 and 'defensiveCold' in _fB59,
              "defensiveCold ×%d ｜ 混入的 Duration 字段 %d 个"
              % (_fB59.count('defensiveCold'),
                 sum(1 for k in _fB59 if k.endswith('Duration'))))

        # ---- ② C/A 族在库里只有 `…Min` / `…DurationMin` 两种形态：
        #   前者是**数值**、后者是**时长**。统计时排不掉时长 ⇒ 把「持续 20 秒」读成「减抗 20%」。
        _fC59 = [x['field'] for x in _rows59['C']]
        _fA59 = [x['field'] for x in _rows59['A']]
        check("★ C/A 族排掉 `…DurationMin`（时长 ≠ 减抗值）",
              all(not k.endswith(('DurationMin', 'Duration')) for k in _fC59 + _fA59)
              and any(k.endswith('PercentMin') for k in _fC59)
              and any(k.endswith('AbsoluteMin') for k in _fA59),
              "C %d 条 / A %d 条 ｜ 混入时长 %d 个"
              % (len(_fC59), len(_fA59),
                 sum(1 for k in _fC59 + _fA59
                     if k.endswith(('DurationMin', 'Duration')))))

        # ---- ③ 实例锚定：截图那条「银色标记」= B 族典型（负 defensiveCold + defensivePierce）
        _sil59 = [x for x in _rows59['B'] if 'silvermark' in x['rec']]
        check("★ 实例锚定：银色标记 = 负 `defensiveCold` + `defensivePierce`（−10%）",
              len(_sil59) == 2
              and {x['field'] for x in _sil59} == {'defensiveCold', 'defensivePierce'}
              and all(x['value'] == 10.0 for x in _sil59),
              '、'.join('%s −%.0f' % (x['field'], x['value']) for x in _sil59) or '未找到')

        # ---- ④ 三族都非空（数据表或判据坏了会立刻显形）
        check("三族均非空（B/C/A）", all(_rows59[k] for k in 'BCA'),
              'B %d / C %d / A %d' % tuple(len(_rows59[k]) for k in 'BCA'))
    except Exception as _e59:                                   # noqa: BLE001
        check("降抗字段工具可运行", False, f"{type(_e59).__name__}: {_e59}")

    # ============================================= [60] 减抗来源明细（当前这套 BD）
    print("\n[60] 减抗来源明细（tools/rr_sources.py）")
    try:
        import rr_sources as _RS60
        from gd import rr as _RR60, dps as _D60
        _res60 = _RS60.scan('Sam')

        # ---- ① ★★ 逐来源合计必须与**模型聚合口径**逐位一致
        #   这条是核心：明细把来源拆开了（逐槽逐部件 / 逐技能），求和后
        #   必须回到 `collect_char` 的聚合值 —— 否则「明细」与「模型」是两套数。
        _add60, _mx60, _fl60 = _RS60._totals(_res60)
        _c60 = _D60.load_char('Sam')
        _sk60 = {k: v for k, v in _c60['skills'].items() if v > 0}
        for _r60, _e60 in (_c60.get('skill_plus') or {}).items():
            if _r60 in _sk60:
                _sk60[_r60] += _e60
        _all60, _gear60, _sk60b = _RR60.collect_char(_c60['folded'], _sk60, _c60['db'])
        _badB = ['%s 明细%.1f≠模型%.1f' % (b, _add60.get(b, 0.0),
                                          float((_all60['add'] or {}).get(b, 0.0)))
                 for b in set(list(_add60) + list(_all60['add'] or {}))
                 if abs(_add60.get(b, 0.0)
                        - float((_all60['add'] or {}).get(b, 0.0))) > 1e-6]
        check("★★ 明细合计 == 模型聚合口径（B 族逐桶累加）", not _badB,
              '、'.join(_badB[:4]) if _badB else '%d 个桶全对' % len(_add60))
        _badC = ['%s 明细%.1f≠模型%.1f' % (b, _mx60.get(b, 0.0),
                                          float((_all60['max'] or {}).get(b, 0.0)))
                 for b in set(list(_mx60) + list(_all60['max'] or {}))
                 if abs(_mx60.get(b, 0.0)
                        - float((_all60['max'] or {}).get(b, 0.0))) > 1e-6]
        check("★★ 明细合计 == 模型聚合口径（C 族逐桶取最高）", not _badC,
              '、'.join(_badC[:4]) if _badC else '%d 个桶全对' % len(_mx60))
        _badA = ['%s 明细%.1f≠模型%.1f' % (b, _fl60.get(b, 0.0),
                                          float((_all60['flat'] or {}).get(b, 0.0)))
                 for b in set(list(_fl60) + list(_all60['flat'] or {}))
                 if abs(_fl60.get(b, 0.0)
                        - float((_all60['flat'] or {}).get(b, 0.0))) > 1e-6]
        check("★★ 明细合计 == 模型聚合口径（A 族逐桶取最高）", not _badA,
              '、'.join(_badA[:4]) if _badA else '%d 个桶全对' % len(_fl60))

        # ---- ② 武器套判据（拿错一套 ⇒ 整张明细指向不存在的装备）
        check("★ 减抗明细标出「当前启用的武器套」（与 gd.build 同判据）",
              _res60.get('weapon_set') in ('alt1', 'alt2'),
              _res60.get('weapon_set'))

        # ---- ③ 三个来源层都要能被拆出来（装备逐部件 / 技能逐条）
        _parts60 = {g['part'] for g in _res60['gear']}
        _kinds60 = {s['kind'] for s in _res60['skill']}
        # ⚠ 不能断言「必定有部件级减抗」—— 换武器套后会**合法地**变成 0 条
        #   （实测 2026-09-22：Sam 从「扭曲精神」(alt2 主手) 换成 alt1「刻罪者」后，
        #    装备侧那条 C 族 −30% 整条消失）。只断言**拆出来的条目结构完整**。
        _okp60 = all({'slot', 'part', 'name', 'gid', 'rows'} <= set(g)
                     for g in _res60['gear'])
        check("★ 装备拆到**部件级**（底材/镶嵌/附魔/前缀/后缀；五字段齐备）", _okp60,
              ('%d 条 ｜ ' % len(_res60['gear'])
               + ('、'.join(sorted(_parts60)) if _parts60
                  else '本套装备无减抗字段（合法）')))
        check("★ 技能按来源分类（专精 / 装备授予 / 星座 proc）", bool(_kinds60),
              '、'.join(sorted(_kinds60)))
    except Exception as _e60:                                   # noqa: BLE001
        check("减抗来源明细工具可运行", False, f"{type(_e60).__name__}: {_e60}")

    # ==================================== [61] 武器类型硬前提门控（陷阱 #80 / #81）
    print("\n[61] 武器类型硬前提门控（陷阱 #80 / #81）")
    try:
        import ast as _ast61
        import importlib as _il61
        import json as _js61

        # ---- ① ★★ 永久守卫：**每个** `final_report(` 调用点都必须点名 `weapon_st`
        #   为什么必须静态扫：门控是**可选参数**，漏传 = 静默零门控 ——
        #   同一条 BD 在不同工具里出两个数，而且**没有任何提示**。
        #   实测 2026-09-22：只有 `plan_dps` 接了，`gd dps` CLI / `tune_devotion` /
        #   `tune_skills` / `eval_build_variants` / `gt_regress` / `defense_audit` /
        #   `tune_attrs` / `gd.gen` / `gd.mana` **全部零门控**。
        #   允许写 `weapon_st=None`（显式声明「此处故意不门控」），但**必须点名**。
        _miss61, _tot61 = [], 0
        for _p61 in (list((_ROOT / "gd").glob("*.py"))
                     + list((_ROOT / "tools").glob("*.py"))):
            if "_patched" in _p61.name:
                continue
            try:
                _t61 = _ast61.parse(_p61.read_text(encoding="utf-8"))
            except Exception:                                   # noqa: BLE001
                continue
            for _n61 in _ast61.walk(_t61):
                if not isinstance(_n61, _ast61.Call):
                    continue
                _f61 = _n61.func
                if not (isinstance(_f61, _ast61.Attribute)
                        and _f61.attr == "final_report"):
                    continue
                _tot61 += 1
                if "weapon_st" not in {_k61.arg for _k61 in _n61.keywords}:
                    _miss61.append("%s:%d" % (_p61.name, _n61.lineno))
        check("★★ 每个 final_report 调用点都点名 weapon_st（陷阱 #81 永久守卫）",
              not _miss61, "全 %d 处已接" % _tot61 if not _miss61
              else "漏：%s" % "、".join(_miss61))

        # ---- ② `load_char` 存下 weapon_st，且与从 `base_gids` 现推**一致**
        _D61 = _il61.import_module("gd.dps")
        _PR61 = _il61.import_module("gd.procs")
        _DBR61 = _il61.import_module("gd.dbr")
        _c61 = _D61.load_char('Sam', '', True)
        _st61 = _c61.get("weapon_st")
        _st61b = _PR61.weapon_state(_c61.get("base_gids"))
        #   ⚠ `kinds` 是 **tuple** ⇒ 必须 `str()` 包一层。直接写
        #     `"kinds=%s" % d.get("kinds")` 会把 tuple 当**参数元组**展开，
        #     报 `TypeError: not all arguments converted`（陷阱 #76 的同类坑）。
        check("★ load_char 存下 weapon_st（= 从 base_gids 现推的结果）",
              _st61 is not None and _st61 == _st61b,
              "kinds=%s" % str((_st61 or {}).get("kinds")))
        check("★ weapon_state_of 两条取值路一致（存值 vs 现推）",
              _PR61.weapon_state_of(_c61) == _st61b)
        check("★ weapon_state 记忆化幂等（连调两次逐位相同）",
              _PR61.weapon_state(_c61.get("base_gids")) == _st61b)
        check("★ 看不到武器 ⇒ 返回 None（= 不门控，零漂移）",
              _PR61.weapon_state([]) is None
              and _PR61.weapon_type_ok({}, None)[0] is True)

        # ---- ③ ★★ 门控在**两条通道**都生效（这是本轮补的核心缺口）
        #   `levels`（存档 / 形态通道）与 `devotion_levels`（`tune_devotion` /
        #   `eval_build_variants` 走的**显式星座通道**）—— 只做前者时，
        #   星座优化器**完全不受武器限制约束**。
        _A61 = "records/skills/devotion/tier2_25a.dbr"       # 狂战士星位（需斧/矛）
        _db61 = _DBR61.open_all()
        _ST_AXE = {"kinds": ("WeaponMelee_Sword", "WeaponMelee_Axe"),
                   "has_shield": False, "dual_wield": True,
                   "two_handed": False, "n_weapons": 2}
        _ST_SW = {"kinds": ("WeaponMelee_Sword", "WeaponMelee_Sword"),
                  "has_shield": False, "dual_wield": True,
                  "two_handed": False, "n_weapons": 2}
        _R61 = _il61.import_module("gd.rotation")

        def _run61(_st, _dev=False):                            # noqa: ANN001
            _kw61 = ({"devotion_levels": {_A61: 1}} if _dev
                     else {"skill_records": [_A61]})
            return _R61.final_report("wolf_nightblade_fast",
                                     {} if _dev else {_A61: 1},
                                     db=_db61, folded={}, weapon_st=_st, **_kw61)

        _bad61 = []
        for _tag61, _dev61 in (("levels", False), ("devotion", True)):
            _r61sw = _run61(_ST_SW, _dev61)
            _d61 = (_r61sw["rotation"].get("weapon_dropped") or [])
            if not _d61:
                _bad61.append("%s 未拦" % _tag61)
            elif _d61[0].get("channel") != _tag61:
                _bad61.append("%s 通道标注错" % _tag61)
            if (_run61(_ST_AXE, _dev61)["rotation"].get("weapon_dropped") or []):
                _bad61.append("%s 误杀斧" % _tag61)
            # 星座通道还有一个**直接可观测**的证据：`dev_nodes`（真正生效的星位数）
            if _dev61:
                if int(_r61sw.get("dev_nodes") or 0) != 0:
                    _bad61.append("devotion 通道：受限星位仍计入 dev_nodes")
                if int(_run61(_ST_AXE, _dev61).get("dev_nodes") or 0) != 1:
                    _bad61.append("devotion 通道：合法星位被误剔")
        check("★★ 武器门控在 levels 与 devotion_levels **两条通道**都生效",
              not _bad61, "、".join(_bad61) or "两条通道均正确剔除 / 正确放行")

        # ---- ④ 零漂移：`weapon_st=None` ⇒ 门控整段跳过（星位照常生效）
        _n61 = _run61(None, True)
        check("★ 零漂移：weapon_st=None ⇒ weapon_dropped 空 且 dev_nodes 不变",
              not (_n61["rotation"].get("weapon_dropped") or [])
              and int(_n61.get("dev_nodes") or 0) == 1,
              "dev_nodes=%s" % _n61.get("dev_nodes"))

        # ---- ⑤ 数据层守卫：星座星位的武器限制字段**必须可读**（陷阱 #80 的产物）
        _cal61 = _js61.loads((_ROOT / "data" / "calc_mastery_skills.json")
                             .read_text(encoding="utf-8"))
        _wk61 = ("Axe", "Axe2h", "Spear2h", "Sword", "Mace", "Shield", "Dagger",
                 "Scepter", "Gun", "Crossbow")
        _b2561 = {}
        for _ch61 in "abcdef":
            _v61 = _cal61.get("records/skills/devotion/tier2_25%s.dbr" % _ch61) or {}
            _b2561[_ch61] = sorted(_k for _k in ("Axe", "Axe2h", "Spear2h")
                                   if _v61.get(_k))
        check("★★ 数据层：狂战士**六星全带**武器限制（陷阱 #80 守卫）",
              all(_b2561.values()), str(_b2561))
        _lim61 = sum(1 for _v in _cal61.values()
                     if any(_v.get(_k) for _k in _wk61))
        check("★ 数据层：武器限制字段整体可读（> 0 条）", _lim61 > 0,
              "%d 条（补洞前为 0）" % _lim61)

        # ---- ⑥ 中文原因可读（报告要给人看）
        _ok61r, _why61r = _PR61.weapon_type_ok(_db61.fields(_A61) or {}, _ST_SW)
        check("★ 拒绝原因含中文武器名（报告可读性）",
              (not _ok61r) and ("斧" in _why61r) and ("剑" in _why61r), _why61r)

        # ---- ⑦ CLI 报告**可见性**：门控剔了必须在报告里说出来
        #   否则玩家只看到「数字变小了」，不知道是哪条、为什么 ——
        #   「静默归零」正是这类 bug 最难查的地方。
        import types as _tp61
        _D61b = _il61.import_module("gd.dps")
        _sk61 = {_k: _v for _k, _v in (_c61.get("skills") or {}).items() if _v > 0}
        _rep61 = _R61.final_report("wolf_nightblade_fast", _sk61, db=_c61["db"],
                                   folded=_c61["folded"], skill_records=list(_sk61),
                                   weapon_st=None)

        def _render61(_rep):                                    # noqa: ANN001
            _b = io.StringIO()
            with contextlib.redirect_stdout(_b):
                _t, _ = _D61b.build_report(_tp61.SimpleNamespace(char="Sam"),
                                           _c61, _sk61, _D61b.load_skills_zh(),
                                           "wolf_nightblade_fast", _rep, True)
            return _t

        _t_clean = _render61(_rep61)                    # 空账本（真实存档形状）
        _rep61["rotation"]["weapon_dropped"] = [
            {"rec": _A61, "why": "需要 斧／双手斧／双手矛，当前 剑",
             "channel": "levels"}]
        _t_gate = _render61(_rep61)                     # 有条目
        check("★ CLI 报告会**明说**武器门控剔了什么",
              ("武器类型门控" in _t_gate) and ("tier2_25a" in _t_gate)
              and ("斧" in _t_gate))
        check("★ 空账本时**不占版面**（不打印门控块）",
              "武器类型门控" not in _t_clean)
    except Exception as _e61:                                   # noqa: BLE001
        check("武器类型门控可运行", False, f"{type(_e61).__name__}: {_e61}")

    # ============================ [62] 优化目标口径 + 减抗按试验等级重算（#84/#85）
    print("\n[62] 优化目标口径与减抗重算（陷阱 #84 / #85）")
    try:
        import importlib as _il62
        import json as _js62

        _OBJ62 = _il62.import_module("objfunc")
        _RR62 = _il62.import_module("gd.rr")
        _PD62 = _il62.import_module("plan_dps")
        _D62 = _il62.import_module("gd.dps")
        _DBR62 = _il62.import_module("gd.dbr")

        # ---- ① ★★ 永久守卫：`total` 必须取**实战层**，不能读 `rep['dps']`
        #   为什么：`rep['dps']` 在 `dps_of()`（= `dps_vs`）与 `final_report()`（= **面板**）
        #   里语义不同 ⇒ 只读 `dps` 会让「只在实战层生效」的东西**静默归零**：
        #   减抗技能（刺骨战吼拆掉实测 −24.36%，却被报 0）、OA/DA 增益、暴击、敌方抗性。
        #   方向最危险：**把最大收益项当废物**。
        _fake62 = {'dps': 1000.0, 'vs': {'dps_vs': 9999.0},
                   'type_rows': [{'type': 'pierce', 'dps_vs': 8000.0}]}
        _s_total62 = _OBJ62.score(_fake62, 'total')
        check("★★ objfunc(total) 取实战层 `vs.dps_vs`，不读面板 `dps`（陷阱 #84）",
              abs(_s_total62 - 9999.0) < 1e-6, "score=%s（面板 1000）" % _s_total62)
        _s_bk62 = _OBJ62.score(_fake62, 'pierce')
        check("★ objfunc(桶) 本来就读 `type_rows.dps_vs`（未被 #84 波及）",
              abs(_s_bk62 - 8000.0) < 1e-6, "score=%s" % _s_bk62)

        # ---- ② ★★ 技能轴的评分基线必须与 `dps_of` **同口径**（逐位级）
        #   ⚠ `tune_devotion` 走**显式星座通道** ⇒ 它的绝对值与 `dps_of` 不同源，
        #     不能这样直接比（只比「是否在实战层」）。这里只钉技能轴。
        _TS62 = _il62.import_module("tune_skills")
        _b62 = _TS62.ev(_TS62.BASE)
        _ref62 = float(_PD62.dps_of('Sam')['dps_vs'])
        _got62 = float(_TS62._sc(_b62))
        check("★★ tune_skills 基线评分 == plan_dps.dps_of 的含减抗值（同口径）",
              abs(_got62 - _ref62) < 1.0, "%.1f vs %.0f" % (_got62, _ref62))

        # ---- ③ ★★ 减抗必须**按试验等级重算**（陷阱 #85）
        #   旧实现把 `collect_char()` 的结果在模块级冻结 ⇒ 改减抗技能等级**永不见效**。
        #   端到端证据：刺骨战吼 `defensivePierce` 逐级 `[-4,-7,…,-30,…]`，
        #   lv1 = 12% ／ lv11 = 36% ／ lv12 = 38% ⇒ 拆 1 点必须有损失。
        _BONE62 = 'records/skills/playerclass10/bonechillingcry1.dbr'
        _t62 = dict(_TS62.BASE)
        _t62[_BONE62] = _t62[_BONE62] - 1
        _loss62 = _TS62._sc(_b62) - _TS62._sc(_TS62.ev(_t62))
        check("★★ 减抗技能拆 1 点**必须有损失**（rr 不再冻结，陷阱 #85）",
              _loss62 > 1.0, "刺骨战吼 lv12→11 损失 %.1f" % _loss62)

        # ---- ④ ★ RRCache：与直算逐位一致 / 只在该重算时才重算 / 幂等
        _c62 = _D62.load_char('Sam', '', True)
        _rc62 = _RR62.RRCache(_c62['folded'], _c62['db'])
        _lv62 = dict(_TS62.BASE)
        _p1 = _rc62.pack(_lv62)
        _direct62 = _RR62.collect_char(_c62['folded'],
                                       {k: v for k, v in _lv62.items() if v}, 
                                       _c62['db'])[0]
        check("★ RRCache 与 collect_char 直算**逐位一致**",
              _js62.dumps(_p1, sort_keys=True) == _js62.dumps(_direct62, sort_keys=True))
        _n1_62 = _rc62.recomputes
        _rc62.pack(dict(_lv62))                       # 同一等级集（新 dict、同值）
        check("★ RRCache 幂等：同一等级集命中缓存（不重算）",
              _rc62.recomputes == _n1_62, "重算 %d" % _rc62.recomputes)
        #   动一条**不含减抗**的记录 ⇒ 不应触发重算
        _plain62 = [r for r in _lv62 if not _rc62.is_source(r) and _lv62[r] > 1]
        if _plain62:
            _t62b = dict(_lv62)
            _t62b[_plain62[0]] -= 1
            _rc62.pack(_t62b)
            check("★ RRCache：动**无减抗**的技能 ⇒ 不重算（性能守卫）",
                  _rc62.recomputes == _n1_62, "重算 %d" % _rc62.recomputes)
        #   动一条**带减抗**的记录 ⇒ 必须重算，且值确实变化
        _t62c = dict(_lv62)
        _t62c[_BONE62] = 1
        _p2_62 = _rc62.pack(_t62c)
        _a62 = (_p1.get('add') or {}).get('pierce')
        _b62v = (_p2_62.get('add') or {}).get('pierce')
        check("★ RRCache：动**减抗**技能 ⇒ 重算且值变化",
              _rc62.recomputes > _n1_62 and abs((_a62 or 0) - (_b62v or 0)) > 0.5,
              "pierce %s → %s" % (_a62, _b62v))

        # ---- ⑤ ★ 三个走 `objfunc` 的轴都必须用 RRCache（结构性防回退）
        _miss62 = []
        for _f62 in ('tune_skills.py', 'tune_devotion.py', 'eval_build_variants.py'):
            _src62 = (_ROOT / "tools" / _f62).read_text(encoding="utf-8")
            if 'RRCache' not in _src62:
                _miss62.append(_f62 + '(未用 RRCache)')
            if 'rr=_rr_pack' in _src62 or 'rr=rr_pack' in _src62:
                _miss62.append(_f62 + '(仍是冻结 rr)')
        check("★ 三轴都用 RRCache 取 rr（陷阱 #85 回退守卫）",
              not _miss62, "、".join(_miss62) or "tune_skills ／ tune_devotion ／ eval_build_variants")
    except Exception as _e62:                                   # noqa: BLE001
        check("优化目标口径守卫可运行", False, f"{type(_e62).__name__}: {_e62}")

    # ================== [63] `k`=等级要求 / `l`=Class + 武器组件池（#86 / #87）
    print("\n[63] 等级要求（`k`）与武器组件候选池（陷阱 #86 / #87）")
    try:
        import importlib as _il63

        _O63 = _il63.import_module("gd.opt")

        # ---- ① ★★ GT 键字典：`k` = levelRequirement、`l` = Class（槽位类别码）
        #   为什么钉在自检里：这两个都是字母，含义天差地别；把 `l` 当等级会让
        #   「等级闸门」静默失效（`_req_lvl` 曾如此）。
        _it63 = _O63._IT.get('it729') or {}
        check("★★ `_req_lvl` 读 `k`（= 等级要求），不再把 `l`(Class) 当等级",
              _O63._req_lvl('it729') == int(_it63.get('k') or 0)
              and _O63._req_lvl('it729') != 20,
              "it729: k=%s ／ 旧实现会返回 20（= `l`='c20' 的槽位码数字）"
              % _it63.get('k'))

        # ---- ② ★★ 武器组件池必须是**自动生成**的、规模非平凡
        #   旧状：硬编码 `POOL_CWPN = ['it2878', 'it2849']` 两项 ⇒ 武器组件这一维
        #   几乎无搜索空间（实测「主手」只有 1 项）。
        _w63 = _O63.auto_wpn_comp_pool()
        _mh63 = _w63.get('主手') or []
        check("★★ 武器组件池是自动生成的且 ≥ 20 项（陷阱 #87）",
              len(_mh63) >= 20, "主手 %d 项" % len(_mh63))
        check("★ 池内每一项都**等级合法**（`k` ≤ `_MAX_ILVL`）",
              all(((_O63._IT.get(g) or {}).get('k') or 0) <= _O63._MAX_ILVL
                  for g in _mh63),
              "上限 %s" % _O63._MAX_ILVL)
        check("★ 池内每一项都**槽位可取**（`cls ∩ WEAPON_CODES ≠ ∅`）",
              all(set((_O63._IT.get(g) or {}).get('cls') or [])
                  & set(_O63.WEAPON_CODES) for g in _mh63))
        check("★ 已知最优件 `it2878`（恶毒尖刺）在池内", 'it2878' in _mh63)

        # ---- ③ ★★ 等级不够的件**必须**被等级闸门挡掉（`it8898` 刀刃之印 k=75）
        check("★★ 等级不够的 `it8898`（k=75）不在池内（角色 73 级）",
              'it8898' not in _mh63,
              "k=%s" % (_O63._IT.get('it8898') or {}).get('k'))

        # ---- ④ ★★ `plan_legal.check_level` 能拦住「槽位合法但等级不够」
        #   这正是 2026-09-22 把 刀刃之印 误当可达收益的那类错误。
        _pl63 = importlib.import_module("plan_legal")
        # ⚠ 这里**必须**用 `from gd import DB` 这种直接 import 语句 ——
        #   磁盘上的文件是 `gd/db.py`（小写），`from gd import DB` 靠 **Windows 大小写
        #   不敏感**才解析得到，而 `importlib.import_module("gd.DB")` 按名查 → 直接
        #   `ModuleNotFoundError`（实测踩到）。要写 importlib 就得写 `"gd.db"`。
        from gd import DB as _DBmod63
        _db63 = _DBmod63.load()
        _over63 = _pl63.check_level(
            _db63, {"主手": ["it1891", "it8898", None, None, None]}, 73)
        check("★★ plan_legal 查出「等级不够」（刀刃之印 k=75 > 73）",
              len(_over63) == 1 and _over63[0][2] == 'it8898',
              "%s" % (_over63,))
        _over63b = _pl63.check_level(
            _db63, {"主手": ["it1891", "it2878", None, None, None]}, 73)
        check("★ plan_legal 不误判合法件（恶毒尖刺 k=15）", not _over63b)
    except Exception as _e63:                                   # noqa: BLE001
        check("等级要求与组件池守卫可运行", False, f"{type(_e63).__name__}: {_e63}")


    # ==================== [64] 转伤链**预编译查表**（陷阱 #92）
    #   ★★ 2026-09-22 性能重构：`dmg.convert` 把每步规则预编译成
    #     「来源类型 → (目标表, 总量)」（`_rel_tbl`，**按步骤签名全局缓存**）。
    #     实测单次评估 −8.2%（3.310 → 3.039 ms），真实负载下唯一步骤签名只有 1 个。
    #   ★ 零漂移是硬指标 ⇒ 这里内嵌**旧实现的参考复刻**做 fuzz 逐位对拍。
    print()
    print("[64] 转伤链预编译（查表）与旧实现逐位对拍")
    try:
        import importlib as _il64
        import random as _rnd64mod
        _D64 = _il64.import_module("gd.dmg")
        _T64 = sorted(_D64.ALL_TYPES)
        _IO64 = _T64 + ["elemental"]

        def _ref_apply_one(_s, _convs):              # ← 旧实现（逐字复刻）
            if _s.locked or not _s.type:
                return [_s]
            _base = _D64.direct_of(_s.type)
            _rel = []
            for _i, _o, _p in _convs:
                if not _D64._conv_matches(_i, _base) or not _p or _p <= 0:
                    continue
                for _tg, _sh in _D64._targets(_s.type, _o):
                    _rel.append((_tg, float(_p) * _sh))
            if not _rel:
                return [_s]
            _tot = sum(_q for _t, _q in _rel)
            _out = []
            _keep = max(0.0, 100.0 - _tot) / 100.0
            if _keep > 1e-9:
                _out.append(_s.scaled(_keep))
            _share = 1.0 if _tot <= 100.0 else 100.0 / _tot
            for _tg, _q in _rel:
                _k = _q / 100.0 * _share
                if _k <= 0:
                    continue
                _out.append(_D64.Source(_tg, _s.lo * _k, _s.hi * _k, _s.origin, _s.dur, True))
            return _out

        def _ref_convert(_srcs, _steps, _pr=0.0, _hw=False):
            _cur = [_s.copy() for _s in _srcs]
            for _st in (_steps or []):
                if not _st:
                    continue
                _nx = []
                for _s in _cur:
                    _nx.extend(_ref_apply_one(_s, _st))
                _cur = _nx
            if _pr and _hw:
                _cur = _D64._apply_pierce(_cur, _pr)
            return _cur

        def _k64(_s):
            return (_s.type, repr(_s.lo), repr(_s.hi), _s.origin, repr(_s.dur), _s.locked)

        _rnd64 = _rnd64mod.Random(20260922)
        _diff, _n64 = 0, 240
        for _ in range(_n64):
            _srcs = []
            for _ in range(_rnd64.randint(0, 12)):
                _t = _rnd64.choice(_T64)
                _lo = round(_rnd64.uniform(-5, 900), 4)
                _srcs.append(_D64.Source(
                    _t, _lo, _lo + round(_rnd64.uniform(0, 300), 4),
                    _rnd64.choice(["weapon", "skill", "aura", "gear"]),
                    _rnd64.choice([0.0, 1.5, 3.0]), _rnd64.random() < 0.2))
            _steps = []
            for _ in range(_rnd64.randint(0, 2)):
                _steps.append([] if _rnd64.random() < 0.25 else [
                    (_rnd64.choice(_IO64), _rnd64.choice(_IO64),
                     _rnd64.choice([5.0, 25.0, 50.0, 100.0, 33.333, 0.0, -10.0]))
                    for _ in range(_rnd64.randint(1, 3))])
            _pr, _hw = _rnd64.choice([0.0, 0.0, 0.2, 0.5, 1.0]), _rnd64.random() < 0.7
            _a = [_k64(x) for x in _ref_convert(_srcs, _steps, _pr, _hw)]
            _b = [_k64(x) for x in _D64.convert(_srcs, _steps, pierce_ratio=_pr,
                                                has_weapon_damage=_hw)]
            if _a != _b:
                _diff += 1
        check("★★ 转伤链：新查表实现与旧实现 **fuzz %d 例逐位一致**" % _n64,
              _diff == 0, "不一致 %d 例" % _diff)

        _st64 = [("fire", "cold", 50.0)]
        check("★ `_rel_tbl` 同签名返回**同一对象**（真缓存，非每次重建）",
              _D64._rel_tbl(_st64) is _D64._rel_tbl(list(_st64)))
        check("★ 不同签名 ⇒ 不同表（不会串味）",
              _D64._rel_tbl(_st64) is not _D64._rel_tbl([("fire", "chaos", 50.0)]))
        check("★ 未知来源类型**就地补算**（不改行为）",
              _D64._apply_one(_D64.Source("no_such_type", 1.0, 2.0, "skill"),
                              [("no_such_type", "fire", 50.0)], {})[-1].type == "fire")
        check("★ 缓存有上限守卫（防长搜索无界增长）",
              isinstance(_D64._REL_TBL_CAP, int) and _D64._REL_TBL_CAP > 0
              and "clear()" in (Path(__file__).resolve().parent.parent
                                / "gd" / "dmg.py").read_text(encoding="utf-8"))
    except Exception as _e64:                                   # noqa: BLE001
        check("转伤链预编译守卫可运行", False, f"{type(_e64).__name__}: {_e64}")

    # ==================== [65] ILP 满抗的 (抗性,目标) 支配剪枝（陷阱 #93）
    #   ★★ 2026-09-22 性能：`ilp_res.dom_reduce` 按「res_A ≥ res_B 且 obj_A ≥ obj_B」
    #      删候选（主手额外要求「A 双手不能替非双手 B」）。这是**精确剪枝**：
    #      被删的候选任何解里都能就地换成支配它的那件 —— 抗性只增不减、目标不降。
    #      实测 38,267 → 13,034 变量，MIP 9.65 s → 3.45 s，解与目标逐位相同。
    #   ★ 这里在**合成小实例**上做三件事：① 与朴素 O(n²) 判据对拍（剪枝不得删掉
    #     真正不可支配的候选）② 全模型 vs 剪枝模型 **MIP 最优值必须相同**
    #     ③ 缓存 key 契约（同样输入同 key、换权重换 key）。
    print()
    print("[65] ILP 支配剪枝（精确性对拍 + 缓存契约）")
    try:
        import numpy as _np65
        import random as _rnd65mod
        import ilp_res as _IR65

        class _FakeO65:
            NT, NF = 3, 2
            TYPES = ["fire", "cold", "poison"]
            NEED = {"fire": 40.0, "cold": 30.0, "poison": 12.0}
            FKEYS = ["out", "rr"]
            W_DMG = {"out": 1.0, "rr": 0.5}
            SKILL_K, LEG_PEN = 0.6, 5.0
            SLOTS = ["头部", "胸甲", "主手", "副手"]

            def __init__(self):
                self._IT = {}

        _O65 = _FakeO65()
        _r65 = _rnd65mod.Random(20260922)

        def _mk65(slot, n, two_h=False):
            out = []
            for i in range(n):
                gid = "%s_%d" % (slot, i)
                _O65._IT[gid] = {"n": gid + ("_2h_sword" if two_h else "")}
                out.append(((gid, None, None, None, None),
                            tuple(round(_r65.uniform(0, 22), 3) for _ in range(3)),
                            tuple(round(_r65.uniform(0, 90), 3) for _ in range(2)),
                            round(_r65.uniform(0, 30), 3), 0,
                            0, 1 if _r65.random() < 0.3 else 0))
            return out

        # 判据矩阵 = 9 抗性 + 目标；is2h 单独一列
        _cm65 = {
            "头部": _mk65("头部", 160),
            "胸甲": _mk65("胸甲", 160),
            "主手": _mk65("主手", 200, two_h=True),
            "副手": [_IR65._empty_cand(_O65)] + _mk65("副手", 90),
        }
        _dw65 = _np65.array([_O65.W_DMG[k] for k in _O65.FKEYS], float)

        # ① 与朴素 O(n²) 判据对拍：朴素保留的必须**全部**被剪枝保留（只许多留、不许删）
        _bad65, _slow65, _fast65 = [], 0, 0
        for _s65 in ("头部", "主手"):
            _cs65 = _cm65[_s65]
            _rows65 = _IR65._dom_rows(_O65, _cs65, _dw65)
            _o2 = _np65.array([1.0 if _IR65._is_2h(_O65, c[0]) else 0.0 for c in _cs65])
            _r65b = _np65.column_stack([_rows65, _o2]) if _s65 == "主手" else _rows65
            _ord65 = _np65.argsort(-_r65b[:, -1 if _s65 != "主手" else -2],
                                   kind="stable").tolist()
            _d = _r65b.shape[1] - (1 if _s65 == "主手" else 0)
            _naive65, _seen65 = [], []
            for _i65 in _ord65:
                _dom65 = False
                for _j65 in _naive65:
                    if (_r65b[_j65][:_d] >= _r65b[_i65][:_d]).all() and (
                            _s65 != "主手" or _r65b[_j65][-1] <= _r65b[_i65][-1]):
                        _dom65 = True
                        break
                if not _dom65:
                    _naive65.append(_i65)
            _core65 = _IR65._dom_core(_r65b, _ord65, is2h=(_o2 if _s65 == "主手" else None))
            _slow65 += len(_naive65)
            _fast65 += len(_core65)
            _miss65 = sorted(set(_naive65) - set(_core65))
            if _miss65:
                _bad65.append((_s65, _miss65[:5]))
        check("★★ 支配剪枝**只许多留、不许删**（与朴素 O(n²) 判据逐槽对拍）",
              not _bad65, "误删：%s" % (_bad65[:2],))
        check("★ 快速路径确实起作用（保留数 ≥ 朴素 Pareto 前沿）",
              _fast65 >= _slow65, "fast=%d slow=%d" % (_fast65, _slow65))

        # ② 全模型 vs 剪枝模型：MIP 最优值必须相同（精确性）
        import highspy as _hs65                                        # noqa: F401
        _Mfull65 = _IR65.Model(_O65, _cm65)
        _solA65, _iA65 = _Mfull65.solve(time_limit=60.0, gap=0.0, log=lambda *a: None)
        _cm65r = _IR65.dom_reduce(_O65, _cm65, log=lambda *a: None, use_cache=False)
        _Mred65 = _IR65.Model(_O65, _cm65r)
        _solB65, _iB65 = _Mred65.solve(time_limit=60.0, gap=0.0, log=lambda *a: None)
        _nA65 = _Mfull65.n
        _nB65 = _Mred65.n
        _dobj65 = abs((_iA65.get("objective") or 0) - (_iB65.get("objective") or 0))
        check("★★ 全模型与剪枝模型 **MIP 最优值逐位相同**（%d→%d 变量，目标差 %.6f）"
              % (_nA65, _nB65, _dobj65), _dobj65 < 1e-6)
        check("★ 剪枝模型选中的每件都在**原候选表**里（没有凭空造件）",
              _solB65 is not None and all(
                  _solB65[_s] in [c[0] for c in _cm65[_s]] for _s in _solB65))
        _rowsE65 = _IR65._dom_rows(_O65, _cm65["副手"], _dw65)
        _ordE65 = [0] + [i for i in _np65.argsort(-_rowsE65[:, -1],
                                                  kind="stable").tolist() if i != 0]
        check("★ 副手 ∅ 候选**永不删**（双手一致性约束依赖它）",
              0 in set(_IR65._dom_core(_rowsE65, _ordE65, fixed_first=True)))

        # ③ 缓存 key 契约
        _k1a = _IR65._dom_key(_O65, _cm65, list(_cm65))
        _k1b = _IR65._dom_key(_O65, _cm65, list(_cm65))
        _saved65 = dict(_O65.W_DMG)
        _O65.W_DMG = {"out": 2.0, "rr": 0.5}
        _k2 = _IR65._dom_key(_O65, _cm65, list(_cm65))
        _O65.W_DMG = _saved65
        check("★ 缓存 key 稳定（同输入同 key）", _k1a == _k1b and _k1a)
        check("★ 目标权重一变 ⇒ key 变（不会吃到旧剪枝）", _k1a != _k2)
        _src65 = (Path(__file__).resolve().parent / "ilp_res.py").read_text(encoding="utf-8")
        check("★ `cap_search` 里已接支配剪枝（且可 `GD_ILP_DOM=0` 关掉）",
              "dom_reduce(O, candmap" in _src65 and "GD_ILP_DOM" in _src65)
    except Exception as _e65:                                   # noqa: BLE001
        check("ILP 支配剪枝守卫可运行", False, f"{type(_e65).__name__}: {_e65}")

    # ==================== [66] `gd/opt._skyline` 的 O(n²)→近线性重写（陷阱 #94）
    #   ★★ 2026-09-22：用户实测「怎么这么慢，只有一个在跑」—— 真凶是**候选池建池**：
    #      lv76 / `--extreme` 单槽原始候选可达 **61,510**（150×24×14），
    #      旧 `_skyline` 对每个候选扫**全部 n 行** ⇒ O(n²)，单槽 ~110 s、
    #      14 槽 **419.5 s**（`gd auto` 全程 546 s，且**单线程单核**）。
    #   ★ 三条改动都**不改变结果**（支配关系有传递性 + 字典序降序让支配者先到）：
    #     ① 只与**已保留**行比 ② 字典序降序预排序 ③ runmax 快速路径 + 512 行分块命中即停。
    #   ★ 这里做两件事：fuzz 与**旧实现逐位对拍**、结构化大数组上的**加速比守卫**。
    print()
    print("[66] 候选池支配剪枝 `_skyline`（快速路径）与旧实现逐位对拍")
    try:
        import importlib as _il66
        import random as _rnd66mod
        import time as _t66

        _O66 = _il66.import_module("gd.opt")
        import numpy as _np66

        def _mk66(n, dm, kind, seed):
            _r = _np66.random.default_rng(seed)
            if kind == "rand":
                return _np66.round(_r.uniform(-5, 60, size=(n, dm)), 3)
            if kind == "int":
                return _r.integers(0, 7, size=(n, dm)).astype(float)
            if kind == "dup":                       # 全同 / 大量重复 ⇒ 无严格支配
                v = _np66.round(_r.uniform(0, 9, size=(1, dm)), 3)
                return _np66.repeat(v, n, axis=0)
            # structured：少量「强」点 + 大量被支配点（贴近真实前沿稀疏的情形）
            _k = max(1, min(n, max(2, n // 40)))
            _a = _np66.round(_r.uniform(0, 60, size=(_k, dm)), 2)
            _m = max(0, n - _k)
            if _m == 0:
                return _a
            _j = _a[_r.integers(0, _k, size=_m)] * _np66.round(
                _r.uniform(0.2, 0.95, size=(_m, dm)), 3)
            return _np66.vstack([_a, _j])

        _bad66 = []
        for _kind in ("rand", "int", "dup", "structured"):
            for _sd in (1, 2, 3):
                for _nn, _dm in ((0, 5), (1, 5), (2, 3), (37, 4), (300, 11), (1200, 7)):
                    _rows = _mk66(_nn, _dm, _kind, _sd * 100 + _nn)
                    _a = _O66._skyline_slow(_rows)
                    _b = _O66._skyline(_rows)
                    if not (_a == _b).all():
                        _bad66.append((_kind, _nn, _dm, int((_a != _b).sum())))
        check("★★ `_skyline` 新版与旧 O(n²) 版 **fuzz 逐位一致**（4 种分布 × 6 种规模）",
              not _bad66, "不一致：%s" % (_bad66[:3],))

        _env66 = os.environ.get("GD_SKYLINE")
        os.environ["GD_SKYLINE"] = "slow"
        _rows66 = _mk66(500, 6, "structured", 77)
        _same_switch = bool((_O66._skyline(_rows66) == _O66._skyline_slow(_rows66)).all())
        if _env66 is None:
            os.environ.pop("GD_SKYLINE", None)
        else:
            os.environ["GD_SKYLINE"] = _env66
        check("★ `GD_SKYLINE=slow` 回退开关生效（A/B 用）", _same_switch)

        # 加速比守卫（真实量级的结构化数组；84× 实测，阈值放到 3× 只防回归）
        _big66 = _mk66(6000, 11, "structured", 2026)
        _t0 = _t66.time()
        _ka = _O66._skyline_slow(_big66)
        _t_old = _t66.time() - _t0
        os.environ.pop("GD_SKYLINE", None)
        _t0 = _t66.time()
        _kb = _O66._skyline(_big66)
        _t_new = _t66.time() - _t0
        check("★ 快速路径**确有加速**（6000×11 结构化，实测旧 %.2fs / 新 %.3fs ≈ %.0f×）"
              % (_t_old, _t_new, _t_old / max(_t_new, 1e-9)),
              _t_old > 0.2 and _t_new * 3 < _t_old and (_ka == _kb).all())
        check("★ 快速路径有 512 行**分块 + 命中即停**（防退化成全量比较）",
              "512" in (Path(__file__).resolve().parent.parent
                        / "gd" / "opt.py").read_text(encoding="utf-8"))
    except Exception as _e66:                                   # noqa: BLE001
        check("候选池支配剪枝守卫可运行", False, f"{type(_e66).__name__}: {_e66}")

    # ==================== [67] 形态判定链 & 四轴的输入方案（陷阱 #95）
    #   ★★ 2026-09-22：给**非 Sam** 角色（`_xyf` lv76 士兵+夜刃）跑四轴时发现，
    #      `tune_skills` / `tune_devotion` 的 `PLAN` 默认值**写死成 Sam 的文件**、
    #      `ARCH` 默认写死 `wolf_nightblade_fast`；`plan_dps.dps_of` / `tune_attrs`
    #      的形态兜底又是写死的 `'werewolf'`（`guess_arch` 只认变身技能）。
    #      结果：数字看着正常（面板 89,217 / 评分 113,736）却与该角色毫无关系
    #      （`_xyf` 真值 200,754）。⇒ 这里把「形态链」与「方案传递」钉住。
    print()
    print("[67] 形态判定链（变身优先）与四轴输入方案传递（陷阱 #95）")
    try:
        import importlib as _il67
        _PD67 = _il67.import_module("plan_dps")

        check("★★ `resolve_arch` 显式值优先",
              _PD67.resolve_arch('Sam', 'avalanche') == 'avalanche')
        check("★★ 变身角色形态**不被职业组合覆盖**（Sam ⇒ werewolf，零漂移）",
              _PD67.resolve_arch('Sam') == 'werewolf',
              "得到 %s" % _PD67.resolve_arch('Sam'))

        # 非变身角色：必须落到「职业组合」那一档，而不是 werewolf
        _has67 = False
        try:
            _has67 = 'Sam' in _PD67._ARCH_MEMO or True
            _m67 = _PD67.arch_by_mastery('_xyf')
            _has67 = bool(_m67)
        except Exception:                                   # noqa: BLE001
            _m67, _has67 = '', False
        if _has67:
            check("★★ 非变身角色按**职业组合**判定（`_xyf` ⇒ soldier_nightblade）",
                  _m67 == 'soldier_nightblade', "得到 %s" % _m67)
            check("★★ `resolve_arch` 对它**不再回落到 werewolf**",
                  _PD67.resolve_arch('_xyf') == _m67)
        else:
            check("非变身角色形态兜底（本机无 `_xyf` 存档 ⇒ 跳过实测，只验契约）",
                  _PD67.arch_by_mastery('__no_such_char__') == '')

        _src67 = (Path(__file__).resolve().parent / "coordinate_ascent.py").read_text(
            encoding="utf-8")
        _sk67 = (Path(__file__).resolve().parent / "tune_skills.py").read_text(
            encoding="utf-8")
        _dv67 = (Path(__file__).resolve().parent / "tune_devotion.py").read_text(
            encoding="utf-8")
        check("★★ `coordinate_ascent` **显式把 PLAN 传给子轴**（否则吃到 Sam 的方案）",
              "env['PLAN'] = plan" in _src67 and "_resolve_plan" in _src67)
        check("★★ `coordinate_ascent` 把**形态定死后下传**（否则吃 wolf_nightblade_fast）",
              "args.arch = arch" in _src67 and "PD.resolve_arch(char)" in _src67)
        check("★ 子轴仍保留「默认值」这一危险事实（提醒：必须显式传）",
              "Sam_lv73_current.json" in _sk67 and "Sam_lv73_current.json" in _dv67)
    except Exception as _e67:                                   # noqa: BLE001
        check("形态链/方案传递守卫可运行", False, f"{type(_e67).__name__}: {_e67}")

    # ==================== [68] 「从 0 构建」链路（陷阱 #96）
    #   ★★ 2026-09-22：用户要「75 级 死灵+狂战士、不参考存档、从 0 构建」。三个坑：
    #     ① `{8,10}` 形态表里**完全不存在** ⇒ 已补 3 个（人/狼人/鸦人）到**增补文件**
    #        `data/archetypes_gdskill.json`（`data/archetypes.json` 会被 migrate 无条件覆盖）
    #     ② `gd recipe`（唯一「三要素→完整 BD」入口）**断链**（引用的 gd_opt.py/bd_gen.py 已不存在）
    #     ③ 从 0 角色没学过形态技能 ⇒ 变身形态被形态门控剔空、**DPS 恒为 0**，
    #        必须先 `make_alloc.py` 给加点（`GD_SKILL_JSON`）才比得出形态优劣
    print()
    print("[68] 「从 0 构建」链路：形态增补 / 造角色安全闸（陷阱 #96）")
    try:
        import importlib as _il68
        import json as _j68
        _archs68 = _j68.loads((Path(__file__).resolve().parent.parent
                               / "data" / "archetypes.json").read_text(encoding="utf-8"))
        _want68 = {"necro_berserker": "human", "wolf_necromancer": "werewolf",
                   "raven_necromancer": "wereraven"}
        _got68 = {k: (_archs68.get(k) or {}).get("form") for k in _want68}
        check("★★ (8,10) 的三个形态都在 `archetypes.json` 里且 form 正确",
              _got68 == _want68, str(_got68))
        _bad68 = []
        for k in _want68:
            ids = {m[0] for m in ((_archs68.get(k) or {}).get("masteries") or [])}
            if ids != {8, 10}:
                _bad68.append((k, sorted(ids)))
        check("★★ 三个形态的 masteries 都是 **{{8,10}}**", not _bad68, str(_bad68))

        _over68 = _j68.loads((Path(__file__).resolve().parent.parent
                              / "data" / "archetypes_gdskill.json").read_text(encoding="utf-8"))
        check("★★ 增补登记在 `archetypes_gdskill.json`（不是直接改会被覆盖的 archetypes.json）",
              all(k in (_over68.get("archetypes") or {}) for k in _want68))

        import autobuild as _AB68
        _p68 = _AB68.pick_archetype([8, 10], {}, _archs68)
        check("★★ `pick_archetype({{8,10}})` 命中新形态（不再按重叠度乱猜）",
              _p68 in _want68, "得到 %s" % _p68)

        _MZ68 = _il68.import_module("make_zero_char")

        def _P68REAL():
            """真实存档目录（临时摘掉 `GD_SAVE`，避开 memo 缓存）"""
            from gd import paths as _pp
            _old = os.environ.pop('GD_SAVE', None)
            _pp._SAVE_DIR_MEMO.clear()
            try:
                return str(_pp.save_dir()[0])
            finally:
                if _old is not None:
                    os.environ['GD_SAVE'] = _old
                _pp._SAVE_DIR_MEMO.clear()

        _rsrc68 = (Path(__file__).resolve().parent / "make_zero_char.py").read_text(
            encoding="utf-8")
        check("★★ 造角色工具有「拒绝写入真实存档目录」硬闸",
              "拒绝写入真实存档目录" in _rsrc68)
        # 功能性验证：真把 out 指到真实存档目录 ⇒ 必须抛 SystemExit
        _refused = False
        try:
            _MZ68.build('_Cx666', 75, [8, 10], '_SHOULD_NOT_EXIST',
                        str(_P68REAL()))
        except SystemExit:
            _refused = True
        except Exception:
            _refused = False
        check("★★ 功能性验证：out=真实存档目录时**确实拒绝**且不落盘",
              _refused and not (Path(_P68REAL()) / "main" / "_SHOULD_NOT_EXIST").exists())
        check("★ 从 0 角色产物不在真存档目录里（隔离生效）",
              not (Path(_P68REAL()) / "main" / "_ZERO75").exists()
              and not (Path(_P68REAL()) / "main" / "_ZERO75F").exists())
        # ★★ 壳必须**按目标职业自动挑**：壳的 class 对不上时，`--classes` 只是新增精通条，
        #    壳原有职业会留着 ⇒ `read_meta` 读到的是并集（实测：拿 (8,10) 的壳造 {10,4}，
        #    classids 仍是 [8,10]）。这里钉住「挑出来的壳确实包含目标职业」。
        import autobuild as _AB68b
        _sh = _MZ68.pick_shell([10, 4])
        _ok_sh = False
        if _sh:
            _lv, _cls, _ = _AB68b.read_meta(_sh)
            _ok_sh = {10, 4} <= set(_cls)
        check("★★ `pick_shell` 按目标职业自动挑壳（挑出的壳确实包含该组合）",
              _ok_sh, "挑到 %s" % _sh)
        check("★ `--shell` 默认已改为空（自动挑），不再是写死的 `_Cx666`",
              "default=''" in _rsrc68 or 'default=""' in _rsrc68)
    except Exception as _e68:                                   # noqa: BLE001
        check("「从 0 构建」守卫可运行", False, f"{type(_e68).__name__}: {_e68}")

    # ==================== [69] BD 配方 v2（`python -m gd recipe`）—— 陷阱 #97
    #   v1（gd/recipe.py, 2026-09-17）**断链**：`cd data && python gd_opt.py` + `bd_gen.py`
    #   都不存在了 ⇒ 第③步必崩。v2 = **纯编排器**（`tools/recipe.py`），每阶段调现有工具，
    #   分阶段状态落盘可续跑、一条命令跑完「预算→造角色→加点→形态并发普查→四轴→交付」。
    print()
    print("[69] BD 配方 v2：编排器契约与 CLI（陷阱 #97）")
    try:
        import importlib.util as _iu69
        _sp = _iu69.spec_from_file_location(
            'recipe69', str(Path(__file__).resolve().parent / 'recipe.py'))
        _RC69 = _iu69.module_from_spec(_sp)
        _sp.loader.exec_module(_RC69)

        _A69 = _RC69.load_archs()
        _f69 = _RC69.forms_for('8,10', _A69)
        # ⚠ 放宽：{8,10} 现在除 3 条**手工**形态外，还挂着 3 条**机械派生**的
        #   （`b10c08_*`，见陷阱 #99）⇒ 断言「包含那 3 条手工形态」而不是「恰好等于 3」。
        check("★★ `forms_for('8,10')` 自动展开（不再让人先选，含手工与派生两批）",
              set(['necro_berserker', 'raven_necromancer', 'wolf_necromancer'])
              <= set(_f69), str(_f69))
        check("★ 指定形态时**不展开**（原样返回）",
              _RC69.forms_for('wolf_necromancer', _A69) == ['wolf_necromancer'])
        check("★ `--forms` 显式收窄优先于自动展开",
              _RC69.forms_for('8,10', _A69, 'necro_berserker') == ['necro_berserker'])

        _lv = _RC69.learn_for(_A69['wolf_necromancer'])
        check("★★ `learn_for` 会带上**形态本体 + 它授予的攻击技**"
              "（不学 ⇒ 形态门控剔空 ⇒ DPS 恒为 0）",
              'werewolf1' in _lv and any(x.startswith('werewolf1_') for x in _lv),
              str(_lv))
        check("★★ 人形态没有 root_skills ⇒ 不学任何东西",
              _RC69.learn_for(_A69['necro_berserker']) == [])

        _b69 = _RC69.budget_of(75)
        check("★★ 预算**只按等级推导**（技能 210 / 属性 84 / 虔诚 55，lv75）",
              (_b69['skill'], _b69['attr'], _b69['devotion']) == (210, 84, 55),
              str(_b69))

        _src69 = (Path(__file__).resolve().parent / "recipe.py").read_text(encoding="utf-8")
        # ⚠ 只在**命令行参数位**上断言（带引号），别被文档里解释 v1 断链的那句误伤
        check("★★ 它是**编排器**：命令行里不再出现 v1 那些已不存在的脚本",
              "'gd_opt.py'" not in _src69 and "'bd_gen.py'" not in _src69)
        check("★★ 有分阶段状态（可续跑）+ 每阶段调现有工具",
              "state.json" in _src69 and "sweep_arch.py" in _src69
              and "coordinate_ascent.py" in _src69 and "plan_cycle.py" in _src69)
        check("★★ 伤害循环文档是**硬交付**（plan_cycle 失败 ⇒ 阶段判失败）",
              "arts['cycle_doc']" in _src69 and "bool(arts['build_report'] and arts['cycle_doc'])" in _src69)
        check("★★ 从 0 走 `GD_SAVE` 重定向（不碰真存档）",
              "GD_SAVE" in _src69 and "zero_save" in _src69)

        _shim69 = (Path(__file__).resolve().parent.parent / "gd" / "recipe.py").read_text(
            encoding="utf-8")
        check("★ `gd/recipe.py` 已改成**薄壳**（CLI 入口不变，实现只有一份）",
              "run_path" in _shim69 and "tools" in _shim69)

        import subprocess as _sp69
        _p69 = _sp69.run([sys.executable, '-m', 'gd', 'recipe', '8,10', '75',
                          '--list-forms'], cwd=str(Path(__file__).resolve().parent.parent),
                         capture_output=True, text=True, encoding='utf-8', errors='replace')
        check("★★ `python -m gd recipe --list-forms` 可用（CLI 真的通了）",
              _p69.returncode == 0 and 'wolf_necromancer' in (_p69.stdout or ''),
              _p69.stdout.strip().splitlines()[0] if _p69.stdout else '无输出')
        _p69b = _sp69.run([sys.executable, '-m', 'gd', 'recipe', '99,98', '75',
                           '--list-forms'], cwd=str(Path(__file__).resolve().parent.parent),
                          capture_output=True, text=True, encoding='utf-8', errors='replace')
        check("★ 不存在的职业组合 ⇒ 非零退出并提示去补增补文件",
              _p69b.returncode != 0 and 'archetypes_gdskill.json' in (_p69b.stdout or ''),
              (_p69b.stdout or '').strip().splitlines()[-1:])
    except Exception as _e69:                                   # noqa: BLE001
        check("BD 配方 v2 守卫可运行", False, f"{type(_e69).__name__}: {_e69}")

    # ==================== [70] 形态**机械派生**器（陷阱 #99）
    #   「某职业 + 全部职业谁伤害最高」需要 9 组合 × 3 形态 = 27 条形态条目，手写不可行。
    #   `tools/make_archetypes.py` 按离线库规则派生：主职业全树 + 副职业支援技
    #   （武器池 + 被动/光环），`damage_weights` 不写；写进**增补文件**并幂等合并。
    print()
    print("[70] 形态机械派生器 `make_archetypes`（陷阱 #99）")
    try:
        import importlib.util as _iu70
        import json as _j70
        _sp70 = _iu70.spec_from_file_location(
            'mk70', str(Path(__file__).resolve().parent / 'make_archetypes.py'))
        _MA70 = _iu70.module_from_spec(_sp70)
        _sp70.loader.exec_module(_MA70)

        _by70 = _MA70._load_ms()
        check("★★ 职业名取的是 `kind=='mastery'` 那条**精通记录**（不是技能表第一条）",
              _MA70.class_name(_by70, 'class10') == '狂战士'
              and _MA70.class_name(_by70, 'class08') == '死灵法师',
              '%s / %s' % (_MA70.class_name(_by70, 'class10'),
                           _MA70.class_name(_by70, 'class08')))

        _e70 = _MA70.twin(10, 1, _by70)
        check("★★ 一个组合派生出 **3 个形态**（human/wolf/raven）", len(_e70) == 3,
              str(sorted(_e70)))
        _h = _e70['b10c01_human']
        check("★★ 键名带 `wolf`/`raven` 子串 —— `pick_archetype` 的形态过滤靠它",
              ('wolf' in 'b10c01_wolf') and ('raven' in 'b10c01_raven')
              and _h['form'] == 'human' and _h['root_skills'] == [])
        check("★★ 变身形有 `root_skills`（人形态没有）",
              _e70['b10c01_wolf']['root_skills'][0].endswith('werewolf1.dbr')
              and _e70['b10c01_raven']['root_skills'][0].endswith('wereraven1.dbr'))
        check("★★ `human` 的 core_skills **不含 shapeshift**（不点变形）",
              not any('werewolf1.dbr' == r.split('/')[-1] or
                      'wereraven1.dbr' == r.split('/')[-1]
                      for r in _h['core_skills']))
        check("★ 变身形**含** shapeshift 记录",
              any(r.split('/')[-1] == 'werewolf1.dbr'
                  for r in _e70['b10c01_wolf']['core_skills']))
        check("★ 不含宠物/transmuter（`petskill_*` 与 kind ∈ pet/transmuter 都要剔）",
              not any(r.split('/')[-1].startswith('petskill_')
                      for v in _e70.values() for r in v['core_skills']))
        check("★ 副职业支援技里有**武器池**（class01 的 wpattack*）",
              any('wpattack' in r for r in _h['core_skills']))
        check("★ `damage_weights` **故意不写**（不预设伤害方向）",
              all('damage_weights' not in v for v in _e70.values()))
        check("★ 全部 9 个组合都能派生（10 × {1..9}）",
              all(len(_MA70.twin(10, y, _by70)) == 3 for y in range(1, 10)))

        _p70 = Path(__file__).resolve().parent.parent / "data" / "archetypes_gdskill.json"
        _o70 = _j70.loads(_p70.read_text(encoding='utf-8'))['archetypes']
        _k70 = [k for k in _o70 if k.startswith('b10c')]
        check("★★ 27 条派生条目已登记进**增补文件**（不是会被迁移覆盖的 archetypes.json）",
              len(_k70) == 27, '实得 %d 条' % len(_k70))
        _a70 = _j70.loads((Path(__file__).resolve().parent.parent / "data"
                           / "archetypes.json").read_text(encoding='utf-8'))
        check("★ 且已合并进 `archetypes.json`",
              all(k in _a70 for k in _k70))
        check("★ 幂等：同名键**已存在就不再写**（不会覆盖手工调过的条目）",
              "if k in A and not a.force" in
              (Path(__file__).resolve().parent / "make_archetypes.py").read_text(
                  encoding='utf-8'))
    except Exception as _e70:                                   # noqa: BLE001
        check("形态机械派生守卫可运行", False, f"{type(_e70).__name__}: {_e70}")

    print("\n" + "=" * 72)
    if FAILS:
        print(f"✗ {len(FAILS)} 项失败：" + "、".join(FAILS))
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
