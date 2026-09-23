"""★ 权威映射表构建：直接读游戏 `.dbr` 记录，不再靠命名规则猜。

为什么必须这样做（实测反例）：
    `records/items/gearweapons/melee2h/c204_sword2h.dbr`
    它的 `itemNameTag` 是 **`tagGDX2WeaponMelee2hC202`** ——
    记录名是 c204，标签却是 C202。任何「从标签族名推记录名」的规则**必然找不到它**。

做法：
    1. 逐个 .arz（base → gdx1 → gdx2 → gdx3，后者覆盖前者）解码每条记录的
       `itemNameTag`（物品/组件/圣物/附魔）与 lootRandomizer 名（词缀）。
    2. 用 **`itemLevel` + 标签** 与离线库的 GT 条目做**精确对齐**（多档不再靠猜）。
    3. 产出 `data/record_map.json`（`fwd` / `rev` / 词缀三张表）。

    python tools/build_exact_map.py [--limit N]
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL))
sys.path.insert(0, str(SKILL / "tools" / "legacy"))

import gd_dbr as D                                        # noqa: E402  (构建期工具)

GD_DIR = Path(os.environ.get("GD_GAME") or r"E:\SteamLibrary\steamapps\common\Grim Dawn")
ARZ_ORDER = [("base", "database/database.arz"),
             ("gdx1", "gdx1/database/GDX1.arz"),
             ("gdx2", "gdx2/database/GDX2.arz"),
             ("gdx3", "gdx3/database/GDX3.arz")]

# 记录里表示「名称标签」的字段（不同类别用不同字段；其余 tag 值字段作兜底）
NAME_FIELDS = ("itemNameTag", "lootRandomizerName", "setName", "skillDisplayName",
               "artifactName", "description")


def collect(verbose=True):
    """★ 全库扫描：解码每条记录，收集它引用的**所有 `tag*` 字符串**。

    为什么不只按已知字段取：不同类别用的字段名不一样 ——
      普通物品 `itemNameTag`、组件/圣物/附魔 走 `d`、图纸走别的字段、
      学识笔记（`records/storyelements/…`）又是另一个。
    只按白名单字段取会让 1981 条（笔记 / 图纸 / 镶嵌 / 附魔）全部漏掉。
    所以：**所有值以 `tag` 开头的字符串字段都收**，并标记哪些来自「主名称字段」。
    """
    out: dict[str, dict] = {}
    t_all = time.time()
    for tag, rel in ARZ_ORDER:
        p = GD_DIR / rel
        if not p.exists():
            if verbose:
                print(f"  跳过（不存在） {rel}")
            continue
        t0 = time.time()
        db = D.ArzDb(str(p), tag)
        nrec = ntag = 0
        for rec in db.records:
            if not rec.endswith(".dbr"):
                continue
            f = db.fields(rec)
            if not f:
                continue
            prim: list[str] = []
            allt: list[str] = []
            paths: list[str] = []
            keyrefs: dict = {}
            for k, v in f.items():
                if not v or not isinstance(v[0], str):
                    continue
                s = v[0]
                if s.startswith("tag"):
                    allt.append(s)
                    if k in NAME_FIELDS:
                        prim.append(s)
                elif s.startswith("records/") and s.endswith(".dbr"):
                    paths.append(s)
                    # 图纸靠 artifactName、唯一词缀靠 onlyForItems —— 二次匹配要用
                    if k in ("artifactName", "onlyForItems") or k.startswith("onlyForItems"):
                        keyrefs.setdefault(k, s)
            if not allt:
                continue
            nrec += 1
            ntag += len(allt)
            lvl = f.get("itemLevel")
            req = f.get("levelRequirement")
            out[rec] = {"prim": prim, "all": allt, "paths": paths, "keyrefs": keyrefs,
                        "level": lvl[0] if lvl else None,
                        "req": req[0] if req else None,
                        "sig": _content_sig(f), "src": tag}
        if verbose:
            print(f"  {tag:5} {p.name:16} {nrec:6d} 条 / {ntag:6d} 个 tag 引用  "
                  f"{time.time() - t0:5.1f}s")
    if verbose:
        print(f"  → 合计 {len(out)} 条含 tag 引用的记录，{time.time() - t_all:.1f}s")

    # 第二遍：每条记录**间接**引用的 tag（顺着它引用的记录再取一层）
    #   —— 词缀的 `augmentSkillName1` 指向技能记录，技能记录的 skillDisplayName
    #      就是 GT 侧 `augmentSkillName1` 用的那个 tag。这是消歧的最后一把钥匙。
    t1 = time.time()
    for info in out.values():
        ref = set()
        for pth in info["paths"]:
            tgt = out.get(pth)
            if tgt:
                ref.update(tgt["all"])
        info["ref"] = ref
    if verbose:
        print(f"  → 间接 tag 引用计算完成 {time.time() - t1:.1f}s")
    return out


_TAIL_LETTER = re.compile(r"_([A-Za-z])$")
_REC_VARIANT = re.compile(r"_([a-z])(\d*)$")


def _content_sig(f: dict) -> str:
    """记录的**内容指纹**：只取数值 / 布尔字段。

    ★ 用来发现「本质上完全相同的重复记录」。
      实测：`b_class021_shaman01_je_c.dbr` … `shaman27_je_c.dbr` 共 27 条，
      **622 个字段逐字段完全相同** —— 是纯重复副本。
      这种情况下它们互相可替换，反向表应当**全部指向同一个 GT 条目**，
      而不是因为「不唯一」就把整族判为无法确定。
    """
    parts = []
    for k in sorted(f):
        v = f[k]
        if not isinstance(v, list) or not v:
            continue
        if isinstance(v[0], str):
            continue
        parts.append(f"{k}={v!r}")
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:16]


def affix_variant(tag: str):
    """词缀 tag 尾部的大写字母 = **适用位变体**（实测破译）。

    base.arz 的 `b_class020` 家族共 9 条记录：

        tagPrefixB020_Class_A            ← 尾部 `_A` 指明变体 a
        b_class020_<x><nn>.dbr            x ∈ {a,b,c} 适用位变体
                                          nn ∈ {,'02','03'} 等级档
        三条 x 的 levelRequirement 都是 42 / 65 / 90

    而 GT 侧同 tag 也有 3 档（k=42/65/90）—— **只按等级对齐会「一挑三」**。
    必须「等级定档 + 尾部字母定变体」才唯一。
    """
    m = _TAIL_LETTER.search(tag or "")
    return m.group(1).lower() if m else None


def _rec_variant(record: str):
    """记录基名尾部的变体字母（`b_class020_a03` → `a`）。"""
    base = record[:-4].split("/")[-1]
    m = _REC_VARIANT.search(base)
    return m.group(1) if m else None


def index_by_tag(recs: dict) -> dict:
    """tag -> [记录]，主名称字段命中的排在前面（更可信）。"""
    primary: dict[str, list] = defaultdict(list)
    secondary: dict[str, list] = defaultdict(list)
    for rec, info in recs.items():
        for t in info["prim"]:
            primary[t].append(rec)
        for t in info["all"]:
            if t not in info["prim"]:
                secondary[t].append(rec)
    out = {}
    for t in set(primary) | set(secondary):
        out[t] = {"primary": sorted(set(primary.get(t, ()))),
                  "secondary": sorted(set(secondary.get(t, ())))}
    return out


def build(verbose=True):
    from gd import DB
    db = DB.load()
    recs = collect(verbose)
    by_tag = index_by_tag(recs)

    # ★ 歧义时按需解码：把 ArzDb 留在手里，只对「分不清」的候选现查现比数值。
    #   全量缓存所有记录的数值字段要几百 MB，没必要。
    t_load = time.time()
    dbs: dict[str, object] = {}
    for tag, rel in ARZ_ORDER:
        p = GD_DIR / rel
        if p.exists():
            dbs[tag] = D.ArzDb(str(p), tag)
    rec_src = {rec: info["src"] for rec, info in recs.items()}

    SKIP_NUM = {"k", "i", "h", "p", "itemLevel", "levelRequirement",
                "lootRandomizerJitter", "itemSkillLevelEq"}

    def numeric_of(obj: dict) -> dict:
        return {k: v for k, v in obj.items()
                if isinstance(v, (int, float)) and not isinstance(v, bool)
                and k not in SKIP_NUM}

    def numeric_hits(gt_nums: dict, rec: str):
        """候选记录与 GT 条目的数值一致度 -> (命中数, 参与比较数)。"""
        db_ = dbs.get(rec_src.get(rec, ""))
        if db_ is None:
            return (0, 0)
        f = db_.fields(rec)
        if not f:
            return (0, 0)
        hit = tot = 0
        for k, gv in gt_nums.items():
            v = f.get(k)
            if not v or isinstance(v[0], str):
                continue
            tot += 1
            try:
                if abs(float(v[0]) - float(gv)) < 1e-6:
                    hit += 1
            except (TypeError, ValueError):
                pass
        return (hit, tot)

    def pick_by_numbers(gt_obj: dict, pool: list):
        """在候选里挑数值最吻合的；返回 (候选, 是否唯一)。"""
        gt_nums = numeric_of(gt_obj)
        if not gt_nums or len(pool) < 2:
            return pool[0], False
        scored = []
        for r in pool:
            hit, tot = numeric_hits(gt_nums, r)
            scored.append((hit, tot, r))
        scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
        best = scored[0]
        if best[0] > 0 and (len(scored) == 1 or scored[1][0] < best[0]):
            return best[2], True
        return best[2], False

    if verbose:
        print(f"  ArzDb 就绪 {time.time() - t_load:.1f}s")

    def candidates(tag):
        e = by_tag.get(tag)
        if not e:
            return []
        return e["primary"] or e["secondary"]

    # ---------- 1) 物品 / 组件 / 圣物 / 附魔 / 图纸 / 笔记 ----------
    def gt_tag(o):
        """GT 条目的名称标签：普通物品在 `a`，组件/圣物/附魔/图纸/笔记在 `d`。"""
        for k in ("a", "d"):
            v = o.get(k)
            if isinstance(v, str) and v.startswith("tag"):
                return v
        return None

    fwd, rev, low, fails, dup, num = {}, {}, {}, [], {}, {}
    for gid, o in db.items.items():
        if not gid.startswith("it"):
            continue
        tag = gt_tag(o)
        if not tag:
            fails.append([gid, "无名称标签(a/d 都不是 tag)"])
            continue
        cands = candidates(tag)
        if not cands:
            fails.append([gid, f"标签 {tag} 在 .dbr 里没有记录"])
            continue
        lvl = o.get("itemLevel")
        # ① 先按 itemLevel 精确对齐（多档不再靠猜）
        exact = [r for r in cands if recs[r]["level"] == lvl] if lvl is not None else []
        # ② 再用 levelRequirement 兜一次（笔记 / 图纸只有这个）
        if not exact and len(cands) > 1:
            req = o.get("k")
            exact = [r for r in cands if recs[r]["req"] == req] if req is not None else []
        if len(exact) == 1:
            fwd[gid] = exact[0]
            rev[exact[0]] = gid                     # 唯一确定 → 双向都可信
        elif len(cands) == 1:
            fwd[gid] = cands[0]
            rev[cands[0]] = gid
        else:
            pool = exact or cands
            # ③ 内容完全一致 = 重复副本，互相可替换 → 全部认，双向都可信
            if len({recs[r]["sig"] for r in pool}) == 1:
                fwd[gid] = pool[0]
                for r in pool:
                    rev.setdefault(r, gid)
                dup[gid] = len(pool)
            else:
                # ④ 按数值字段精确比对
                best, uniq = pick_by_numbers(o, pool)
                fwd[gid] = best
                if uniq:
                    rev.setdefault(best, gid)
                    num[gid] = len(pool)
                else:
                    low[gid] = [best, f"标签 {tag} 下 {len(pool)} 条同档，数值也分不清"]

    # ---------- 1b) 二次匹配：图纸 / 唯一词缀（它们没有名称标签）----------
    #   图纸：GT 用 artifactName 指向产物；记录里 artifactName 是记录路径。
    #   唯一词缀：GT 用 onlyForItems 指向唯一物品；记录里同样是指针。
    #   两边都能顺着引用反解到同一个人，于是可以精确对上。
    rec_gid = {r: g for g, r in fwd.items()}
    for gid, r in list(rev.items()):
        rec_gid.setdefault(r, gid)

    def ref_gids(info: dict):
        out = set()
        for pth in info.get("keyrefs", {}).values():
            tgt = rec_gid.get(pth)
            if tgt:
                out.add(tgt)
        return out

    gids_of_ref = {}
    for rec, info in recs.items():
        for pth in info.get("keyrefs", {}).values():
            g = rec_gid.get(pth)
            if g:
                gids_of_ref.setdefault(g, []).append(rec)

    second = 0
    for gid, o in db.items.items():
        if gid in fwd or not gid.startswith("it"):
            continue
        want = None
        if o.get("artifactName"):
            want = o["artifactName"]
        if want:
            cands2 = gids_of_ref.get(want, [])
            if len(cands2) >= 1:
                fwd[gid] = cands2[0]
                rev.setdefault(cands2[0], gid)
                second += 1
    if verbose and second:
        print(f"  二次匹配（图纸）补上 {second} 件")

    # ---------- 2) 词缀 ----------
    afwd, arev, alow, afails, adup, anum = {}, {}, {}, [], {}, {}
    noc = []                       # 没有 c 标签的（多是「唯一前缀/后缀」）
    for gid, table in (("pre", db.prefixes), ("suf", db.suffixes)):
        for aid, o in table.items():
            tag = o.get("c")
            if not tag:
                noc.append((aid, o))
                continue
            cands = candidates(tag)
            if not cands:
                afails.append([aid, f"标签 {tag} 在 .dbr 里没有记录"])
                continue
            lvl = o.get("k")            # 词缀的 levelRequirement 在 `k`
            # ★ 词缀记录里 `itemLevel` 常为 0，必须用 `levelRequirement` 对齐
            exact = [r for r in cands if recs[r]["req"] == lvl] if lvl is not None else []
            # ★ 再用 tag 尾部字母消「适用位变体」的分歧
            var = affix_variant(tag)
            if var and len(exact) > 1:
                pref = [r for r in exact if _rec_variant(r) == var]
                if pref:
                    exact = pref
            # ★★ 最后用「技能改造对象」消歧：
            #     GT 的 augmentSkillNameN 是 tag，记录的 augmentSkillNameN 是记录路径，
            #     把路径解析成 tag 后直接比对（实测可把 27 条候选收敛到 1 条）。
            if len(exact) > 1:
                want = {o.get(f"augmentSkillName{i}") for i in range(1, 11)}
                want.discard(None)
                if want:
                    scored = [(len(want & recs[r]["ref"]), r) for r in exact]
                    best = max(s for s, _ in scored)
                    if best:
                        exact = [r for s, r in scored if s == best]
            if len(exact) == 1:
                afwd[aid] = exact[0]
                arev[exact[0]] = aid
            elif len(cands) == 1:
                afwd[aid] = cands[0]
                arev[cands[0]] = aid
            else:
                pool = exact or cands
                if len({recs[r]["sig"] for r in pool}) == 1:
                    afwd[aid] = pool[0]
                    for r in pool:
                        arev.setdefault(r, aid)
                    adup[aid] = len(pool)
                else:
                    best, uniq = pick_by_numbers(o, pool)
                    afwd[aid] = best
                    if uniq:
                        arev.setdefault(best, aid)
                        anum[aid] = len(pool)
                    else:
                        alow[aid] = [best, f"标签 {tag} 下 {len(pool)} 条同档，数值也分不清"]

    out = {
        "__provenance__": {
            "built": time.strftime("%Y-%m-%d %H:%M:%S"),
            "source": "★ 直接读游戏 .dbr 的 itemNameTag / lootRandomizerName（权威，非规则推导）",
            "arz": [rel for _, rel in ARZ_ORDER],
            "records_with_name_tag": len(recs),
            "note": "多档用 itemLevel 精确对齐；未能对齐的记入 low，不写入反向表",
        },
        "fwd": fwd, "rev": rev, "low": low,
        "affix_fwd": afwd, "affix_rev": arev, "affix_low": alow,
    }

    # ---------- 2b) 唯一词缀：靠 onlyForItems 指针二次匹配 ----------
    #   有些职业专属词缀（GT 里没有 `c` 标签）在 .dbr 里也没写 onlyForItems，
    #   但它们都会写明「提升哪个技能」（augmentSkillName）——
    #   于是可以用「技能改造对象」反查：哪条词缀记录的 ref tag 命中它。
    by_ref: dict[str, list] = defaultdict(list)
    for rec, info in recs.items():
        if "/lootaffixes/" not in rec:
            continue
        for t in info["ref"]:
            by_ref[t].append(rec)

    n2 = 0
    for aid, o in noc:
        pool = []
        refs = o.get("onlyForItems")
        if refs:
            ref = refs[0] if isinstance(refs, list) else refs
            pool = list(gids_of_ref.get(ref, []))
        if not pool:
            want = {o.get(f"augmentSkillName{i}") for i in range(1, 11)}
            want.discard(None)
            for t in want:
                pool += by_ref.get(t, [])
            pool = sorted(set(pool))
        if not pool:
            afails.append([aid, "无 c 标签，且 onlyForItems / 技能改造对象都反查不到"])
            continue
        best, uniq = pick_by_numbers(o, pool)
        afwd[aid] = best
        if uniq:
            arev.setdefault(best, aid)
            n2 += 1
        else:
            alow[aid] = [best, f"唯一词缀，{len(pool)} 条候选，分不清"]
    if verbose and n2:
        print(f"  二次匹配（唯一词缀）补上 {n2} 个")

    tot = len(fwd) + len(fails)
    atot = len(afwd) + len(afails)
    if verbose:
        print()
        print(f"物品 {tot}：可正向 {len(fwd)} ({len(fwd) / tot * 100:.1f}%)  "
              f"其中唯一确定(双向) {len(rev)}  有歧义 {len(low)}  库里无 {len(fails)}")
        print(f"词缀 {atot}：可正向 {len(afwd)} ({len(afwd) / atot * 100:.1f}%)  "
              f"其中唯一确定(双向) {len(arev)}  有歧义 {len(alow)}  库里无 {len(afails)}")
        # 失败原因归类
        from collections import Counter
        c = Counter(re.sub(r"tag\S+", "tagXXX", reason) for _, reason in fails)
        print("  物品失败原因 top5:", c.most_common(5))
        c2 = Counter(re.sub(r"tag\S+", "tagXXX", reason) for _, reason in afails)
        print("  词缀失败原因 top5:", c2.most_common(5))
    return out, fails, afails


def main() -> int:
    t0 = time.time()
    data, fails, afails = build()
    p = SKILL / "data" / "record_map.json"
    if p.exists():
        bak = SKILL / "data" / "record_map.rules.json.bak"
        if not bak.exists():
            bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"（旧规则版已备份到 {bak.name}）")
    p.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                 encoding="utf-8")
    # 失败清单落盘，便于继续收敛
    f = SKILL / "data" / "record_map.unmatched.json"
    f.write_text(json.dumps({"items": fails, "affixes": afails},
                            ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"写入 {p}  {p.stat().st_size / 1024:.0f} KB   {time.time() - t0:.1f}s")
    print(f"未匹配清单 {f.name}  {f.stat().st_size / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
