"""抽取**敌方真值表** → `data/monster_stats.json`

数据来源：`E:\\Grim Tools\\resources\\app.asar` → `/dist/monsterdb/js/monsterdb.js`（9.3 MB）。
这份文件此前**从未被抽取过**；`itemdb.js` 里那个 `window.monsters` 只有
`{type, tag, onMap, diff}`（一个数值都没有），所以一直以为「敌方数据没有」。

实际内容（`window.allMonsters`，2840 条）：
  - 9 个伤害抗性真值：`defensivePhysical/Pierce/Fire/Cold/Lightning/Poison/Life/Aether/Chaos`
  - `monsterClassification`（Common / Champion / Hero / Boss / SuperBoss / Quest）
  - `charLevel` 表达式（如假人 `charLevel*1+2`）
  - `characterAttributeEquations` → 指向 `eqNNN`，给出 DA/OA/生命/三围的**逐级方程**

以及两张全局修正表：
  - `window.monsterAdjustments`：65 个键 × 12 元数组
  - `window.ascendantAdjustments`：飞升模式增益

★ 12 元数组的索引语义（从 `mdb_db.js` 逐字读出，不是猜的）：
    `$db.buildSkillData(monsterAdjustments, 4*((4==Ph?3:Ph)-1)+Dh)`
  其中 `Ph` = 难度（1=Normal / 2=Elite / 3=Ultimate / 4=Ascendant，默认 3），
  `Dh` = 玩家数（1..4，默认 1）。`buildSkillData(obj, rank)` 取 `arr[rank-1]` ⇒
  **索引 = 4*(难度-1) + 玩家数-1**。
  自检：`offensiveTotalDamageModifier` = Normal −25% / Elite +25% / Ultimate +40%（合理）；
  `characterLifeMultModifier` 每难度内 0/90/180/270 = 玩家数 1..4 的 +0/+90/+180/+270% 生命。

★ **被动技能（本表的第二块地基）**：怪物记录里**没有** `defensiveProtection`
（全库 121 次 `defensiveProtection` 里属于怪物记录的 0 次，全在装备/技能上下文），
但**被动技能里全都有** —— 逐字依据见 `PASSIVE_CLASSES` 上方注释。于是：
  - 抗性：`怪物记录.defensiveX` ⊕ `难度修正` ⊕ `Σ 被动技能.defensiveX[等级-1]`
  - 护甲：`Σ 被动技能.defensiveProtection[等级-1]`（× (1+modifier/100)）
  - 生命：`eq.characterLife` × (1 + (难度 +580) + Σ 被动.characterLifeModifier)/100)

自证（`m3955` = tagGDX3Nemesis_Outlaw02，终极/100 级/1 人，与用户游戏截图逐字段一致）：
    体格 855.8 ｜ OA 2477.2 ｜ DA 2254.4 ｜ 生命 4,668,000 ｜ **护甲 1607**（= sk1260[99]）
    抗性 火58 冰5 电10 毒10 穿5 ｜ 流血 85（= sk4685.defensiveBleeding[25] + 9）

用法：`python tools/extract_monsterdb.py [--force]`
"""
import argparse
import collections
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CACHE = ROOT / "data" / "cache"
ASAR_MEMBER = "/dist/monsterdb/js/monsterdb.js"
CACHED = CACHE / "monsterdb.js"
OUT = ROOT / "data" / "monster_stats.json"

# 只收「伤害抗性」——眩晕/冰冻/恐惧/击退/石化/陷阱/睡眠不是伤害类型，
# 进模型反而会污染敌人档位（与 gd/rr.py::DEF_BUCKET 的取舍一致）。
RES_FIELDS = ("defensivePhysical", "defensivePierce", "defensiveFire", "defensiveCold",
              "defensiveLightning", "defensivePoison", "defensiveLife",
              "defensiveAether", "defensiveChaos", "defensiveBleeding")
RES_KEY = {"defensivePhysical": "physical", "defensivePierce": "pierce",
           "defensiveFire": "fire", "defensiveCold": "cold",
           "defensiveLightning": "lightning", "defensivePoison": "poison",
           "defensiveLife": "vitality", "defensiveAether": "aether",
           "defensiveChaos": "chaos", "defensiveBleeding": "bleeding"}

DIFFICULTY = {1: "normal", 2: "elite", 3: "ultimate", 4: "ascendant"}
DUMMY_TAGS = ("tagMiscTargetDummy", "tagGDX3TargetDummy", "tagTargetDummy")

# ★ 被动技能：怪物抗性/护甲/生命的一大块来自这里。
#   权威依据（grimtools 怪物页 `/dist/monsterdb/js/db.js`，逐字）：
#     for(h=1; h<40; h++){ var g=D["skillName"+h], l=D["skillLevel"+h];
#       var n=$db.getSkill(allSkills[g]), q=$db.charEquationValue(l, 1);
#       var E=$db.getItemClass(n);
#       if(("Skill_Passive"==E||"Skill_PassiveDualWieldWeapon"==E||"Skill_Mastery"==E) && rl(n))
#         { var w=$db.buildSkillData(n,q); G.v=$db.mergeSkillData(G.v,w.stats) } }
#     I.v = $db.mergeSkillData(I.v, pl.v)      ← 合并语义 = 数值相加（数组逐元相加）
#     charEquationValue(expr) = floor(evaluate(expr, {charLevel}))
#     buildSkillData(skill, level) 的数组取值 = arr[level-1]
PASSIVE_CLASSES = ("Skill_Passive", "Skill_PassiveDualWieldWeapon", "Skill_Mastery")
SKILL_SCAN = 40          # 页面脚本是 `for(h=1; h<40; h++)`

# 被动技能里**保留**的键（其余是动画/音效/描述，与伤害无关）。
# 覆盖：10 个伤害抗性 + 三元素合并通道 + 上限 + 护甲 + 三围/生命/法力/OA/DA。
PASSIVE_KEEP = (
    "defensivePhysical", "defensivePierce", "defensiveFire", "defensiveCold",
    "defensiveLightning", "defensivePoison", "defensiveLife", "defensiveAether",
    "defensiveChaos", "defensiveBleeding",
    "defensiveElementalResistance",
    "defensivePhysicalMaxResist", "defensivePierceMaxResist", "defensiveFireMaxResist",
    "defensiveColdMaxResist", "defensiveLightningMaxResist", "defensivePoisonMaxResist",
    "defensiveLifeMaxResist", "defensiveAetherMaxResist", "defensiveChaosMaxResist",
    "defensiveBleedingMaxResist", "defensiveAllMaxResist",
    "defensiveProtection", "defensiveProtectionModifier", "defensiveAbsorptionModifier",
    "characterStrength", "characterDexterity", "characterIntelligence",
    "characterStrengthModifier", "characterDexterityModifier", "characterIntelligenceModifier",
    "characterLife", "characterLifeModifier", "characterMana", "characterManaModifier",
    "characterOffensiveAbility", "characterOffensiveAbilityModifier",
    "characterDefensiveAbility", "characterDefensiveAbilityModifier",
)


def node_path():
    exe = os.environ.get("NODE") or ""
    if exe and os.path.isfile(exe):
        return exe
    cands = sorted(Path(os.path.expanduser("~")).glob(
        ".workbuddy/binaries/node/versions/*/node.exe"))
    if cands:
        return str(cands[-1])
    import shutil
    for n in ("node", "node.exe"):
        p = shutil.which(n)
        if p:
            return p
    raise SystemExit("找不到 node（设 NODE=<path>）")


def ensure_cached(force=False):
    """从 asar 把 monsterdb.js 落到 data/cache/（9.3 MB，只做一次）"""
    if CACHED.exists() and not force:
        return CACHED
    from gd import paths
    from gd import asar as A
    p = paths.asar_path()
    if p is None:
        raise SystemExit("找不到 app.asar —— 检查 E:\\Grim Tools\\resources\\")
    a = A.Asar(p)
    data = a.read(ASAR_MEMBER)
    if not data:
        raise SystemExit("asar 里没有 %s" % ASAR_MEMBER)
    CACHED.parent.mkdir(parents=True, exist_ok=True)
    CACHED.write_bytes(data)
    print("  已从 asar 落盘 %s（%.1f MB）" % (CACHED.relative_to(ROOT), len(data) / 1e6))
    return CACHED


def dump_window(names):
    script = ROOT / "tools" / "dump_window.js"
    out = subprocess.run([node_path(), str(script), str(CACHED)] + list(names),
                         capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise SystemExit("dump_window.js 失败：%s" % (out.stderr or "")[:400])
    return json.loads(out.stdout)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="extract_monsterdb")
    ap.add_argument("--force", action="store_true", help="重新从 asar 落盘 monsterdb.js")
    a = ap.parse_args(argv)

    ensure_cached(a.force)
    got = dump_window(["allMonsters", "characterAttributeEquations",
                       "monsterAdjustments", "ascendantAdjustments",
                       "monsterTier", "monsterDifficulty", "allSkills"])
    am = got.get("allMonsters") or {}
    all_skills = got.get("allSkills") or {}
    if not am:
        raise SystemExit("monsterdb.js 里没有 window.allMonsters（结构变了？）")

    monsters = {}
    res_cov = collections.Counter()
    cls = collections.Counter()
    passive_refs = {}                       # {skId: 原始技能记录}（只收真正被怪物被动引用的）
    n_passive_mon = 0
    for mid, rec in am.items():
        if not isinstance(rec, dict):
            continue
        res = {}
        for f, k in RES_KEY.items():
            v = rec.get(f)
            if isinstance(v, (int, float)):
                res[k] = float(v)
                res_cov[k] += 1
        tier = (got.get("monsterTier") or {}).get(mid)
        c = rec.get("monsterClassification")
        cls[c or "(none)"] += 1
        row = {
            "tag": rec.get("d"),
            "type": rec.get("l"),
            "classification": c,
            "res": res,
            # ★ 记录里**其余** defensive* 字段（护甲/吸收/上限/三元素合并通道）。
            #   页面脚本 `I.v` 先收记录里匹配 /^(defensive.+|character.+Speed.*)$/ 的字段。
            "recdef": {k: rec[k] for k in PASSIVE_KEEP
                       if k.startswith("defensive") and isinstance(rec.get(k), (int, float))},
            "armor": None,                              # ★ 记录本身无护甲；护甲来自被动技能
            "level_expr": rec.get("charLevel"),
            "eq": rec.get("characterAttributeEquations"),
        }
        # ★ 被动技能引用（grimtools 怪物页逐字扫 skillName1..39）
        pl = []
        for h in range(1, SKILL_SCAN):
            g = rec.get("skillName%d" % h)
            lv = rec.get("skillLevel%d" % h)
            if not (g and lv):
                continue
            s = all_skills.get(g)
            if isinstance(s, dict) and s.get("l") in PASSIVE_CLASSES:
                pl.append([g, lv])
                passive_refs.setdefault(g, s)
        if pl:
            row["passives"] = pl
            n_passive_mon += 1
        if tier is not None:
            row["tier"] = tier
        d = (got.get("monsterDifficulty") or {}).get(mid)
        if d:
            row["difficulty"] = d
        monsters[mid] = row

    eq_raw = got.get("characterAttributeEquations") or {}
    # 只留会进伤害结算的项，去掉 templateName 之类的噪音
    eq_keys = ("characterDefensiveAbility", "characterOffensiveAbility",
               "characterLife", "characterMana", "characterStrength",
               "characterDexterity", "characterIntelligence",
               "characterLifeRegen", "characterManaRegen")
    equations = {}
    for k, v in eq_raw.items():
        if not isinstance(v, dict):
            continue
        equations[k] = {kk: v[kk] for kk in eq_keys if kk in v}

    def only_arrays(d):
        """调整表里混着 o/l/templateName 这类标量，只留 12 元数组（真正的数值修正）"""
        return {k: [float(x) if isinstance(x, (int, float)) else x for x in v]
                for k, v in (d or {}).items()
                if isinstance(v, list) and len(v) == 12}

    dummies = {mid: r["tag"] for mid, r in monsters.items()
               if r.get("tag") in DUMMY_TAGS}

    # ★ 紧凑被动技能表：只保留与伤害/面板有关的键
    passives = {}
    for sid, s in passive_refs.items():
        keep = {k: s[k] for k in PASSIVE_KEEP if k in s}
        if keep:
            passives[sid] = {"cls": s.get("l"), "s": keep}
    n_passive_res = sum(1 for s in passives.values()
                        if any(k.startswith("defensive") and k[9:] in
                               ("Fire", "Cold", "Lightning", "Poison", "Pierce",
                                "Physical", "Life", "Aether", "Chaos", "Bleeding")
                               for k in s["s"]))
    n_passive_armor = sum(1 for s in passives.values() if "defensiveProtection" in s["s"])

    doc = {
        "source": {
            "asar_member": ASAR_MEMBER,
            "cached_as": "data/cache/monsterdb.js",
            "generator": "tools/extract_monsterdb.py",
            "note": "grimtools 怪物库的底层数据；此前从未启用（itemdb.js 的 window.monsters 无任何数值）",
        },
        "index_semantics": {
            "formula": "4*(difficulty-1) + player_count - 1",
            "evidence": "mdb_db.js: $db.buildSkillData(monsterAdjustments, "
                        "4*((4==Ph?3:Ph)-1)+Dh)，Ph=难度(默认3)，Dh=玩家数(默认1)；"
                        "buildSkillData 取 arr[rank-1]",
            "difficulty": {str(k): v for k, v in DIFFICULTY.items()},
            "player_count": [1, 2, 3, 4],
            "sanity": "offensiveTotalDamageModifier = -25/+25/+40（Normal/Elite/Ultimate）；"
                      "characterLifeMultModifier 每难度 0/90/180/270 = 玩家数 1..4",
        },
        "monsters": monsters,
        "equations": equations,
        "passive_skills": passives,
        "adjustments": only_arrays(got.get("monsterAdjustments")),
        "ascendant": only_arrays(got.get("ascendantAdjustments")),
        "dummies": dummies,
        "derived": {
            "monster_count": len(monsters),
            "equations_count": len(equations),
            "classifications": dict(sorted(cls.items())),
            "res_field_coverage": dict(sorted(res_cov.items())),
            "has_armor": False,                 # 记录本身无护甲字段（护甲在被动技能里）
            "passive_monsters": n_passive_mon,
            "passive_skills": len(passives),
            "passive_with_damage_res": n_passive_res,
            "passive_with_armor": n_passive_armor,
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    print("✓ %s（%.1f MB）" % (OUT.relative_to(ROOT), OUT.stat().st_size / 1e6))
    print("  怪物 %d 只 ｜ 方程 %d 条 ｜ 分类 %s"
          % (len(monsters), len(equations),
             "、".join("%s%d" % kv for kv in sorted(cls.items(), key=lambda x: -x[1])[:5])))
    print("  抗性覆盖 %s" % res_cov.most_common())
    print("  训练假人 %s" % (dummies or "（未找到）"))
    print("  被动技能：%d 只怪引用 ｜ 去重 %d 个技能 ｜ 其中 %d 个给伤害抗性、%d 个给护甲"
          % (n_passive_mon, len(passives), n_passive_res, n_passive_armor))
    return 0


if __name__ == "__main__":
    sys.exit(main())
