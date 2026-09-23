"""抽取**官方战斗公式常数表** → `data/combatformulas.json`

数据来源（全部本地、零网络）：
  `data/cache/itemdb.js` 里嵌的 `window.combatformulas` / `window.engine`，
  它们逐字来自 `records/game/combatformulas.dbr` —— 也就是游戏引擎实际读的那张表。
  grimtools 的 `calc.js` 只用其中 2 个键（护甲吸收、部位概率），
  `pth*` 那几个**从未被用过** ⇒ 本表是唯一把 PTH/暴击阈值变成「官方常数」的地方。

  另附 `data/cache/l10n_zh.js` 的 DoT 语义串（`tagCharStats*AbsDmgInfo`），
  用来把「dbr 里的值 = 每秒」这件事**留档成证据**，而不是靠记忆。

产物结构：
  {
    "source": {...},                  # 出处与生成时间
    "combatformulas": {...},          # 逐键原样
    "engine": {...},
    "pth": {...},                     # 从上面派生：阈值/倍率/下限（方便直接用）
    "armor": {...},                   # 部位概率 + 吸收率
    "dot_semantics": {...},           # DoT 每秒语义的本地化原文
    "derived": {...}                  # 便于断言的派生量（代数恒等等）
  }

幂等：重复跑结果完全一致（不写时间戳进比对区）。
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "cache"
OUT = ROOT / "data" / "combatformulas.json"

GAME_RECORD = "records/game/combatformulas.dbr"


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


def dump_window(js_path, names):
    """调 `tools/dump_window.js` 拿 `window.<name>`（平衡扫描 + eval，唯一稳妥做法）"""
    script = ROOT / "tools" / "dump_window.js"
    out = subprocess.run([node_path(), str(script), str(js_path)] + list(names),
                         capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise SystemExit("dump_window.js 失败：%s" % (out.stderr or "")[:400])
    return json.loads(out.stdout)


def l10n_dot_semantics():
    """DoT「值是每秒」的官方证据 —— 从简体中文本地化串里取原文"""
    p = CACHE / "l10n_zh.js"
    if not p.exists():
        return {}
    s = p.read_text(encoding="utf-8", errors="replace")
    keys = ("tagCharStatsBleedAbsDmgInfo", "tagCharStatsPoisonAbsDmgInfo",
            "tagCharStatsElectrocuteAbsDmgInfo", "tagCharStatsFireAbsDmgInfo",
            "tagCharStatsColdAbsDmgInfo", "tagCharStatsLifeAbsDmgInfo",
            "tagCharStatsPhysicalAbsDmgInfo")
    out = {}
    for k in keys:
        m = re.search(r'"%s"\s*:\s*"([^"]*)"' % re.escape(k), s)
        if m:
            out[k] = m.group(1)
    return out


def dot_duration_pairs():
    """`offensiveSlow<X>Min` 与 `offensiveSlow<X>DurationMin` 的成对覆盖数

    这是「持续时间可以逐条读、不需要假设」的**统计证据**：两者数量应当接近。
    """
    p = CACHE / "itemdb.js"
    if not p.exists():
        return {}
    s = p.read_text(encoding="utf-8", errors="replace")
    out = {}
    for name in ("Bleeding", "Fire", "Cold", "Lightning", "Poison", "Life", "Physical"):
        a = len(re.findall(r'offensiveSlow%sMin\s*:' % name, s))
        b = len(re.findall(r'offensiveSlow%sDurationMin\s*:' % name, s))
        out[name] = {"min_fields": a, "duration_fields": b}
    return out


def main():
    if not (CACHE / "itemdb.js").exists():
        raise SystemExit("缺 data/cache/itemdb.js —— 先跑迁移把它落下来")
    got = dump_window(CACHE / "itemdb.js", ["combatformulas", "engine"])
    cf = got.get("combatformulas") or {}
    eng = got.get("engine") or {}
    if not cf:
        raise SystemExit("itemdb.js 里没有 window.combatformulas（数据结构变了？）")

    # 派生：PTH 阈值/倍率（按 threshold1..6 的编号顺序收集，跳过缺失）
    thr, mod = [], []
    for i in range(1, 7):
        t, m = cf.get("pthThreshold%d" % i), cf.get("pthDamageModifier%d" % i)
        if t is None or m is None:
            continue
        thr.append(int(t))
        mod.append(float(m))
    pth = {
        "minimum": int(cf.get("pthMinimum") or 55),
        "thresholds": thr,
        "modifiers": mod,
        "full_damage_threshold": thr[0] if thr else 70,
        "normal_equation": cf.get("normalPTHEquation"),
        "hit_equation": cf.get("probabilityToHitEquation"),
    }

    regions = {}
    for name in ("Head", "Shoulders", "Torso", "Arms", "Legs", "Feet"):
        v = cf.get("combatRegion%sChance" % name)
        if v is not None:
            regions[name.lower()] = float(v)
    armor = {
        "regions": regions,
        "region_total": sum(regions.values()),
        "absorption": float(eng.get("armorDefensiveAbsorption") or 70),
        "equation_damage_gt_armor": cf.get("physicalDamageDefenseEquationDGP"),
        "equation_damage_le_armor": cf.get("physcialDamageDefenseEquationDLEP"),
        "note": "DLEP 在官方表里拼作 physcialDamageDefenseEquationDLEP（官方拼写如此，勿改）",
    }

    # 属性方程（角色侧，与 gd/rotation.py::ATTR_EQ 对得上）
    attr_eq = {k: cf[k] for k in ("physicalDamageEquation", "pierceDamageEquation",
                                  "physicalDurationDamageEquation", "magicalDamageEquation",
                                  "magicalDurationDamageEquation") if k in cf}

    derived = {
        # ★ 代数恒等：官方式 ⇔ 社区常用简式（selftest 会拿它当断言）
        #   90·OA/(DA/3.5+OA) = 315/(DA/OA+3.5)
        #   0.7·3.25/100 = 1/43.9556 ⇒ (OA−DA)/43.9556 + 70
        #   常数 70 − 50 = 20
        "pth_simple_form": "315/(DA/OA+3.5) + (OA-DA)/43.9556 + 20",
        "pth_simple_coeff": 1.0 / (0.7 * 3.25 / 100.0),
        "pth_region_weights_sum": sum(regions.values()),
        "attr_equations": attr_eq,
    }

    doc = {
        "source": {
            "record": GAME_RECORD,
            "extracted_from": "data/cache/itemdb.js :: window.combatformulas / window.engine",
            "generator": "tools/extract_combatformulas.py",
            "note": "逐字来自游戏数据库；pth* 键 grimtools calc.js 从未使用，本表是本项目唯一来源",
        },
        "combatformulas": cf,
        "engine": eng,
        "pth": pth,
        "armor": armor,
        "attr_equations": attr_eq,
        "dot_semantics": l10n_dot_semantics(),
        "dot_duration_pairs": dot_duration_pairs(),
        "derived": derived,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True),
                   encoding="utf-8")
    print("✓ %s" % OUT.relative_to(ROOT))
    print("  PTH 阈值 %s ｜ 倍率 %s ｜ 下限 %d"
          % (thr, mod, pth["minimum"]))
    print("  部位概率 %s（合计 %g）｜ 吸收 %.0f%%"
          % (regions, armor["region_total"], armor["absorption"]))
    print("  DoT 语义串 %d 条 ｜ 时长成对覆盖 %d 类"
          % (len(doc["dot_semantics"]), len(doc["dot_duration_pairs"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
