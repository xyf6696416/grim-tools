"""离线数据库（Grim Tools 桌面版 app.asar）加载层。

**这是整个技能的地基。** 离线库 = GT 网页版同款数据，但：
  * 完整（8612 物品、1745 前缀、2360 后缀、199 套装、4268 物品技能、1433 怪物、
    326 专精技能、地图/箱子/商人/图纸……）
  * 带 **13 种语言**（含完整简体中文，16563 条）
  * 带 **未混淆的 calc.js**（官方公式可直接读，不再是逆向猜测）

所以：**不再需要** .arz/lz4 解析、不再需要抓 grimtools 网页、不再需要 CDP。

典型用法：
    from gd import DB
    db = DB.load()                      # 默认中文
    db.items["it2116"]                  # 原始字段
    db.name("it2116")                   # 中文名
    db.find("天之裂片")                  # 按名字搜物品
    db.affix("pre4264")                 # 词缀
    db.set("is190")                     # 套装
"""

from __future__ import annotations

import hashlib
import json
import pickle
import re
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from . import jsobj, paths
from .asar import Asar
from .scale import Scaler
from .text import GTFormat, LANGS, Localizer

__all__ = ["DB", "OfflineDB"]

# asar 内需要抽取的文件
ASAR_FILES = {
    "itemdb": "/dist/db/itemdb/itemdb.js",
    "itemdb_diff": "/dist/db/itemdb/itemdb_diff.js",
    "l10n_zh": "/dist/db/itemdb/l10n/zh.js",
    "l10n_en": "/dist/db/itemdb/l10n/en.js",
    "calc": "/dist/calc/calc.js",
    "map": "/dist/map/js/data.js",
    "skills_json": "/dist/db/itemdb/skill_sprites/skills.json",
}
_L10N_TPL = "/dist/db/itemdb/l10n/{lang}.js"

_CJK = re.compile(r"[\u3000-\u9fff\uff00-\uffef]")
# 星座星位记录：records/skills/devotion/tier<档>_<编号><星>.dbr
_DEVOTION_REC = re.compile(r"devotion/tier(\d)_(\d+)")


def _has_cjk(s) -> bool:
    """是否含中日韩字符（用来判断文本是否已本地化）。"""
    return bool(isinstance(s, str) and _CJK.search(s))

# 物品类别 -> 中文标签（GT 的 ItemDB_tagCategory* 系列，实测自 zh 语言包）
_CLASS_TAG = {
    "WeaponMelee_Dagger": "ItemDB_tagCategoryDaggers",
    "WeaponMelee_Sword": "ItemDB_tagCategorySwords",
    "WeaponMelee_Axe": "ItemDB_tagCategoryAxes",
    "WeaponMelee_Mace": "ItemDB_tagCategoryMaces",
    "WeaponMelee_Scepter": "ItemDB_tagCategoryScepters",
    "WeaponMelee_Sword2h": "ItemDB_tagCategoryTwoHandedSwords",
    "WeaponMelee_Axe2h": "ItemDB_tagCategoryTwoHandedAxes",
    "WeaponMelee_Mace2h": "ItemDB_tagCategoryTwoHandedMaces",
    "WeaponArmor_Shield": "ItemDB_tagCategoryShields",
    "WeaponArmor_Offhand": "ItemDB_tagCategoryOffHands",
    "ArmorProtective_Head": "ItemDB_tagCategoryHelms",
    "ArmorProtective_Chest": "ItemDB_tagCategoryChestArmor",
    "ArmorProtective_Hands": "ItemDB_tagCategoryGloves",
    "ArmorProtective_Waist": "ItemDB_tagCategoryBelts",
    "ArmorJewelry_Ring": "ItemDB_tagCategoryRings",
    "ArmorJewelry_Amulet": "ItemDB_tagCategoryAmulets",
    "ArmorJewelry_Medal": "ItemDB_tagCategoryMedals",
    "ItemRelic": "ItemDB_tagCategoryComponents",
    "ItemArtifact": "ItemDB_tagCategoryRelics",
    "ItemEnchantment": "ItemDB_tagCategoryAugments",
}

CACHE_VERSION = 4


class OfflineDB:
    """解析后的离线库。"""

    def __init__(self, raw: dict, l10n_texts, lang: str = "zh",
                 tables: Optional[dict] = None, meta: Optional[dict] = None):
        self.raw = raw
        self._index = {}
        self.lang = lang
        self.meta = meta or {}
        self.tables = tables if tables is not None else _load_tables()
        self.l10n = Localizer(l10n_texts or {}, lang)
        self.fmt = GTFormat(self.tables, self.l10n)
        self.scaler = Scaler(self.tables, self.fmt)

        # ---- 常用别名 ----
        g = self.raw.get
        self.items: dict = g("allItems") or {}
        self.prefixes: dict = g("prefixes") or {}
        self.suffixes: dict = g("suffixes") or {}
        self.sets: dict = g("itemSets") or {}
        self.monsters: dict = g("monsters") or {}
        self.skills: dict = g("itemSkills") or {}
        self.buff_skills: dict = g("buffSkills") or {}
        self.mastery: dict = g("dbMasteryData") or {}
        self.player: dict = g("player") or {}
        self.player_bio: dict = g("playerBio") or {}
        self.engine: dict = g("engine") or {}
        self.factions: dict = g("factions") or {}
        self.containers: dict = g("containers") or {}
        self.container_refs: dict = g("containerRefs") or {}
        self.crafters: dict = g("crafters") or {}
        self.recipes: dict = g("crafterRecipes") or {}
        self.merchants: dict = g("merchants") or {}
        self.merchant_items: dict = g("merchantItems") or {}
        self.interaction_items: dict = g("interactionItems") or {}
        self.interaction_npcs: dict = g("interactionNpcs") or {}
        self.mis: list = g("MIs") or []
        self.mi_refs: dict = g("MIRefs") or {}
        self.quest_items: dict = g("questItems") or {}
        self.combat_formulas: dict = g("combatformulas") or {}
        self.skill_icons: dict = g("skillIcons") or {}
        self.short_names: dict = g("shortNameMapping") or {}
        self.version_diffs: dict = g("versionDiffs") or {}
        self.game_version: str = g("gameVersion") or ""
        self.tool_mod_name: str = g("toolModName") or ""
        self.map: dict = g("maps") or {}
        self._cache_dir = None
        self._update_diffs = g("versionUpdateDiffs") or None
        self._mastery_skills = None
        self._devotion_skills = None

    # ------------------------------------------------------ 懒加载（14 MB）
    @property
    def version_update_diffs(self) -> dict:
        """版本更新明细（itemdb_diff.js，14 MB，按需解析，约 2.7 s）。"""
        if self._update_diffs is None:
            self._update_diffs = self.raw.get("versionUpdateDiffs") or {}
            if not self._update_diffs and self._cache_dir:
                f = Path(self._cache_dir) / "itemdb_diff.js"
                if f.exists():
                    tmp: dict = {}
                    for path, val in jsobj.parse_assignments(f.read_text(encoding="utf-8")):
                        # 形如 versionUpdateDiffs.B27["Hotfix 1"] —— 剥掉统一前缀
                        keys = path[1:] if path and path[0] == "versionUpdateDiffs" else path
                        jsobj.assign_into(tmp, keys, val)
                    self._update_diffs = tmp
        return self._update_diffs

    # ================================================================ 名称
    def text(self, tag: Any, default: Any = None) -> Any:
        return self.l10n.get(tag, default=default)

    def name(self, gid: str, lang: Optional[str] = None) -> str:
        """任意对象的中文名（物品/词缀/套装/技能/怪物/派系/星座……）

        ⚠ 名称标签不止一个字段：普通物品用 `a`（itemNameTag），
          而**组件 / 圣物 / 附魔把名称放在 `d`**（如 `it2866` 的 `d = tagCompA003Name`）。
          先 `a` 后 `d`，否则这些条目会退化成 `itXXXX`。
        """
        o = self.get(gid)
        if o is None:
            return gid
        if gid in self.sets:
            tag = o.get("setName")
        elif gid in self.monsters:
            tag = o.get("tag")
        elif gid in self.prefixes or gid in self.suffixes:
            # 词缀的名称在 `c`（lootRandomizerName），如 tagPrefixB020_Class_A
            tag = o.get("c") or o.get("a")
        else:
            tag = (o.get("a") or o.get("d") or o.get("name")
                   or o.get("skillDisplayName") or o.get("tag"))
        if not tag:
            return gid
        # ★ 名字里会带 GT 的颜色指令（如 `^k基尔利安的碎裂之魂`），
        #   走格式引擎渲染成纯文本，避免颜色码泄漏到报告里。
        #   注意：`fmt.render(tag)` 固定用「当前语言」，所以要先按 lang 取文本，
        #   再把这段**已本地化的文本**当模板渲染（它已不是 tag，会原样透传）。
        text = str(self.l10n.get(tag, lang, default=gid))
        return str(self.fmt.render(text, mode="plain")) or gid

    def en_name(self, gid: str) -> str:
        return self.name(gid, lang="en")

    def label(self, field: str) -> str:
        """字段名 -> 中文标签（不含数值）。"""
        tag = self.field_tag(field)
        return self.fmt.render(tag) if tag else field

    # ================================================================ 查询
    def get(self, gid: str) -> Optional[dict]:
        for table in (self.items, self.prefixes, self.suffixes, self.sets,
                      self.skills, self.monsters, self.mastery, self.factions,
                      self.merchants, self.interaction_npcs, self.crafters,
                      self.containers):
            if gid in table:
                return table[gid]
        return None

    def kind(self, gid: str) -> str:
        for tbl, k in ((self.items, "item"), (self.prefixes, "prefix"), (self.suffixes, "suffix"),
                       (self.sets, "set"), (self.skills, "itemSkill"), (self.monsters, "monster"),
                       (self.mastery, "skill")):
            if gid in tbl:
                return k
        return "?"

    def affix(self, gid: str) -> Optional[dict]:
        return self.prefixes.get(gid) or self.suffixes.get(gid)

    def item_quality(self, gid: str) -> str:
        o = self.items.get(gid) or {}
        return o.get("f") or ""      # Common/Magical/Rare/Epic/Legendary/Quest

    def item_class(self, gid: str) -> str:
        """物品类别（WeaponMelee_Dagger / ArmorProtective_Chest …）。

        `shortNames` 是 全名 -> 短码 的映射，物品里存的是短码，所以要反查。
        """
        o = self.items.get(gid) or {}
        code = o.get("l") or ""
        rev = self._index.get("_class_rev")
        if rev is None:
            rev = {v: k for k, v in self.short_names.items()}
            self._index["_class_rev"] = rev
        return rev.get(code, code)

    def class_cn(self, cls: str) -> str:
        """类别 -> 中文（部位/武器种类）。标签取自 GT 的 `ItemDB_tagCategory*`。"""
        tag = _CLASS_TAG.get(cls)
        if tag:
            t = self.l10n.get(tag)
            if t != tag:
                return str(t)
        t = self.tables.get("se", {}).get(cls) or self.tables.get("te", {}).get(cls) \
            or self.tables.get("ue", {}).get(cls)
        if t:
            v = self.l10n.get(t)
            if v != t:
                return str(v)
        return cls

    # ---------------------------------------------------------------- 搜索
    def _name_index(self, lang: Optional[str] = None):
        key = f"_names_{lang or self.lang}"
        idx = self._index.get(key)
        if idx is None:
            idx = {}
            for gid in self.items:
                n = self.name(gid, lang)
                idx.setdefault(n.lower(), []).append(gid)
            self._index[key] = idx
        return idx

    def find(self, keyword: str, kind: str = "item", limit: int = 30,
             quality: Optional[str] = None, max_level: Optional[int] = None,
             lang: Optional[str] = None) -> list[tuple[str, str]]:
        """按名字模糊搜索 -> [(gid, 中文名)]。"""
        kw = keyword.lower()
        out = []
        tables = {"item": self.items, "prefix": self.prefixes, "suffix": self.suffixes,
                  "set": self.sets, "itemSkill": self.skills, "monster": self.monsters}
        table = tables.get(kind, self.items)
        for gid in table:
            nm = self.name(gid, lang)
            if kw not in nm.lower() and kw not in gid.lower():
                continue
            if kind == "item":
                if quality and (self.items[gid].get("f") or "") != quality:
                    continue
                if max_level is not None and (self.items[gid].get("itemLevel") or 0) > max_level:
                    continue
            out.append((gid, nm))
            if len(out) >= limit:
                break
        return out

    def filter_items(self, **crit) -> list[str]:
        """按任意原始字段过滤物品。

        例：db.filter_items(**{"offensiveFireModifier__gte": 50, "f": "Legendary"})
        后缀 __gte / __lte / __gt / __lt / __in / __contains 可用。
        """
        def match(o: dict) -> bool:
            for k, v in crit.items():
                op = None
                if "__" in k:
                    k, op = k.rsplit("__", 1)
                cur = o.get(k)
                if op == "gte":
                    if cur is None or not cur >= v:
                        return False
                elif op == "lte":
                    if cur is None or not cur <= v:
                        return False
                elif op == "gt":
                    if cur is None or not cur > v:
                        return False
                elif op == "lt":
                    if cur is None or not cur < v:
                        return False
                elif op == "in":
                    if cur not in v:
                        return False
                elif op == "contains":
                    if not cur or v not in cur:
                        return False
                elif op == "exists":
                    if (cur is not None) != bool(v):
                        return False
                else:
                    if cur != v:
                        return False
            return True

        return [gid for gid, o in self.items.items() if match(o)]

    # ---------------------------------------------------------------- 字段标签
    def field_tag(self, field: str) -> Optional[str]:
        """字段名 -> 多语言标签 tag。

        顺序：显式字典（GT 前端校验过的）-> 组合规则 -> None
        """
        fmap = self.tables.get("field_labels") or {}
        if field in fmap:
            return fmap[field]
        if field in self._compositional_tags():
            return self._compositional_tags()[field]
        return None

    def _compositional_tags(self) -> dict:
        """GT 用类型表拼标签，这里把常用族一次展开并用 l10n 校验后缓存。

        命名规律（实测自 l10n 16563 条）：
            基础平伤     offensiveBase<Type>Min/Max  -> tagDamageBase<Type>   (Life->Vitality)
            平伤         offensive<Type>Min/Max      -> Damage<Type>
            % 加成       offensive<Type>Modifier     -> DamageModifier<Type>
            DoT 平伤     offensiveSlow<Type>Min/Max  -> DamageDuration<Type>
            DoT %        offensiveSlow<Type>Modifier -> DamageDurationModifier<Type>
            反击         retaliation<Type>*          -> DamageRetaliation<Type> / …Modifier
        """
        cached = self._index.get("_ctags")
        if cached is not None:
            return cached
        zh = self.l10n.texts.get(self.lang) or {}
        types = ["Physical", "Pierce", "Bleeding", "Fire", "Cold", "Lightning",
                 "Poison", "Life", "Aether", "Chaos", "Elemental"]
        base_alias = {"Life": "Vitality"}
        rules = [
            ("offensiveBase{}Min", lambda t: "tagDamageBase" + base_alias.get(t, t)),
            ("offensiveBase{}Max", lambda t: "tagDamageBase" + base_alias.get(t, t)),
            ("offensive{}Modifier", lambda t: "DamageModifier" + t),
            ("offensive{}Min", lambda t: "Damage" + t),
            ("offensive{}Max", lambda t: "Damage" + t),
            ("offensiveSlow{}Modifier", lambda t: "DamageDurationModifier" + t),
            ("offensiveSlow{}Min", lambda t: "DamageDuration" + t),
            ("offensiveSlow{}Max", lambda t: "DamageDuration" + t),
            ("retaliation{}Min", lambda t: "DamageRetaliation" + t),
            ("retaliation{}Max", lambda t: "DamageRetaliation" + t),
            ("retaliation{}Modifier", lambda t: "DamageRetaliationModifier" + t),
        ]
        out = {}
        for pat, tpl in rules:
            for t in types:
                field, tag = pat.format(t), tpl(t)
                if tag in zh:
                    out.setdefault(field, tag)
        # 单点/全局
        for field, tag in (
            ("offensiveTotalDamageModifier", "tagDamageModifierTotalDamage"),
            ("offensiveCritDamageModifier", "tagDamageModifierCritDamage"),
            ("offensivePierceRatioMin", "DamageBasePierceRatio"),
            ("offensivePierceRatioMax", "DamageBasePierceRatio"),
            ("offensiveBasePhysicalMin", "DamageBasePhysical"),
            ("offensiveBasePhysicalMax", "DamageBasePhysical"),
        ):
            if tag in zh:
                out.setdefault(field, tag)
        self._index["_ctags"] = out
        return out

    # ---------------------------------------------------------------- 派生数据
    @property
    def mastery_skills(self) -> dict:
        """职业技能表（按 tag 索引）。

        ★ 离线库**不含**职业技能的逐级数值（`dbMasteryData` 只有 `{name: tag}`），
          这张表由旧技能从游戏 `database.arz` 抽出后裁剪而来，
          见 tools/migrate_mastery_skills.py。按 tag 与离线库对齐。
        """
        if self._mastery_skills is None:
            self._mastery_skills = paths.load_json("mastery_skills.json") or {}
        return self._mastery_skills

    def mastery_skill(self, gid: str) -> Optional[dict]:
        """`skXXXX`（离线库的专精技能 id）-> 带数值的技能记录。"""
        tag = (self.mastery.get(gid) or {}).get("name")
        if not tag:
            return None
        rec = self.mastery_skills.get(tag)
        if rec:
            return dict(rec, id=gid, tag=tag)
        return None

    @property
    def devotion_skills(self) -> dict:
        """星座节点技能：`records/skills/devotion/*.dbr` -> {tag, name, …}。

        ★ 离线库不含这份数据（星座只有亲和力），由旧技能从 .arz 抽出后迁移，
          见 data/devotion_skills.json。
        """
        if self._devotion_skills is None:
            self._devotion_skills = paths.load_json("devotion_skills.json") or {}
        return self._devotion_skills

    def skill_name_of_record(self, record: str) -> Optional[str]:
        """任意技能记录路径 -> 中文名（专精技能 / 星座节点 / 默认技能）。"""
        if not record:
            return None
        for tag, rec in self.mastery_skills.items():
            if rec.get("record") == record:
                return str(self.l10n.get(rec.get("name") or tag))
        name = self._devotion_star_name(record)
        if name:
            return name
        dev = self.devotion_skills.get(record)
        if dev:
            # 兜底：迁移来的 devotions.json 里少数条目 `name` 是英文
            tag = dev.get("tag")
            if tag:
                v = self.l10n.get(tag)
                if v and v != tag and _has_cjk(v):
                    return str(v)
            nm = dev.get("name")
            if nm and _has_cjk(nm):
                return str(nm)
            return str(tag or nm) if (tag or nm) else None
        return None

    def _devotion_star_name(self, record: str) -> Optional[str]:
        """星座星位的中文名。

        记录形如 `records/skills/devotion/tier<档>_<编号><星>.dbr`，
        档位 1/2/3 对应标签前缀 A/B/C：

            tier1_08e_skill  ->  tagDevotionEffectA08   「暗杀者的标记」
                                 tagDevotion_A08        「刺客的利刃」(所属星座)

        ★ 离线库与迁移来的 devotions.json 都没有这份名字，但**语言包里有**，
          所以按命名规律直接拼标签去查。星位的效果名优先，取不到再退到星座名。
        """
        m = _DEVOTION_REC.search(record)
        if not m:
            return None
        tier, num = m.group(1), m.group(2)
        letter = {"1": "A", "2": "B", "3": "C"}.get(tier)
        if not letter:
            return None
        for tag in (f"tagDevotionEffect{letter}{num}", f"tagDevotion_{letter}{num}"):
            v = self.l10n.get(tag)
            if v and v != tag:
                return str(v)
        return None

    def level_table(self) -> dict:
        """升级给点表（官方 window.playerBio / window.engine）。"""
        bio, eng = self.player_bio, self.engine
        inc = bio.get("skillPointsIncrement") or []
        out, total = [], 0
        for lv in range(1, int(bio.get("maxLevel") or 100) + 1):
            total += inc[lv - 1] if lv - 1 < len(inc) else 0
            out.append(total)
        return {
            "level_cap": bio.get("maxLevel"),
            "skill_points_at_level": out,
            "max_skill_points": bio.get("maxSkillPoints"),
            "attribute_points_increment": bio.get("attributePointsIncrement"),
            "max_attribute_points": bio.get("maxAttributePoints"),
            "max_devotion_points": bio.get("maxDevotionPoints"),
            "quest_skill_points": eng.get("questSkillPoints"),
            "quest_attribute_points": eng.get("questAttributePoints"),
            "milestones": eng.get("milestones"),
            "mastery_increment_level": self.player.get("masteryIncrementLevel"),
            "player": self.player,
        }

    def devotions(self) -> list[dict]:
        """星座树（数据来自 data/devotion_tree.json，见 docs/offline_db.md 的溯源说明）。"""
        data = paths.load_json("devotion_tree.json")
        if not data:
            return []
        out = []
        for cid, c in data.items():
            c = dict(c)
            c["id"] = cid
            c["name"] = self.l10n.get(c.get("tag") or "", default=cid)
            out.append(c)
        return out

    def stats(self) -> dict:
        return {
            "gameVersion": self.game_version,
            "lang": self.lang,
            "items": len(self.items),
            "prefixes": len(self.prefixes),
            "suffixes": len(self.suffixes),
            "sets": len(self.sets),
            "itemSkills": len(self.skills),
            "masteries": len(self.mastery),
            "monsters": len(self.monsters),
            "l10nTags": len(self.l10n),
            "maps": len(self.map),
        }

    def __repr__(self):
        s = self.stats()
        return (f"<OfflineDB {s['gameVersion']} 物品{s['items']} 词缀"
                f"{s['prefixes'] + s['suffixes']} 套装{s['sets']} 技能{s['itemSkills']} "
                f"怪物{s['monsters']} 中文{s['l10nTags']}>")


# ==================================================================== 加载
def _load_tables() -> dict:
    d = paths.load_json("gt_tables.json")
    if not d:
        return {}
    tables = dict(d.get("tables") or {})
    tables["field_labels"] = d.get("field_labels") or {}
    return tables


def _extract(asar: Asar, langs: Iterable[str], verbose=False) -> Path:
    """把需要的文件抽到 data/cache/，返回 cache 根目录。"""
    root = paths.CACHE_DIR
    root.mkdir(parents=True, exist_ok=True)
    names = dict(ASAR_FILES)
    for lang in langs:
        names[f"l10n_{lang}"] = _L10N_TPL.format(lang=lang)
    for key, apath in names.items():
        dest = root / f"{key}.js"
        try:
            entry = asar.stat(apath)
        except Exception:
            continue
        if entry is None:
            continue
        if dest.exists() and dest.stat().st_size == int(entry["size"]):
            continue
        dest.write_bytes(asar.read(apath))
        if verbose:
            print(f"  抽取 {apath} -> {dest.name}")
    return root


def _parse_all(cache: Path, langs: Iterable[str], verbose=False) -> tuple[dict, dict, dict]:
    """返回 (raw, l10n_texts, extra)

    ★ 只解析 itemdb.js（必需）。itemdb_diff.js 有 14 MB，绝大多数查询用不到，
      改为按需懒加载（见 OfflineDB.version_update_diffs）。
    """
    t0 = time.time()
    raw: dict = {}
    f = cache / "itemdb.js"
    if f.exists():
        for path, val in jsobj.parse_assignments(f.read_text(encoding="utf-8")):
            jsobj.assign_into(raw, path, val)
    if verbose:
        print(f"  itemdb 解析完成 {time.time() - t0:.2f}s  顶层键 {len(raw)}")

    l10n: dict = {}
    for lang in langs:
        f = cache / f"l10n_{lang}.js"
        if not f.exists():
            continue
        for path, val in jsobj.parse_assignments(f.read_text(encoding="utf-8")):
            # 路径形如 ['db_l10n_texts', lang]
            if path and path[-1] == lang and isinstance(val, dict):
                l10n.setdefault(lang, {}).update(val)
    if verbose:
        print(f"  语言包：{ {k: len(v) for k, v in l10n.items()} }  {time.time() - t0:.2f}s")

    mf = cache / "map.js"
    if mf.exists():
        for path, val in jsobj.parse_assignments(mf.read_text(encoding="utf-8")):
            if path and path[-1] == "maps":
                raw["maps"] = val
    return raw, l10n, {}


class DB:
    """加载入口（磁盘缓存 + 进程内单例）。"""

    _instance: Optional[OfflineDB] = None
    _memo: dict = {}

    @staticmethod
    def load(lang: str = "zh", langs: Iterable[str] = ("zh", "en"), cache: bool = True,
             verbose: bool = False, gt_dir=None) -> OfflineDB:
        """加载离线库（默认中文 + 英文回退）。

        ★ 必须有**进程内记忆化**：磁盘缓存命中也要 0.068 s（反序列化 8 k 物品 +
          16 k 语言包）。旧实现每次都重读 —— 实测 `plan_dps._is_two_hand()` 每遇到
          一个新的武器记录就调一次，占了「变方案」单次评估的 **58 %**。
        """
        key = (lang, tuple(langs), str(gt_dir) if gt_dir else None)
        db = DB._memo.get(key)
        if db is None:
            db = DB._load_impl(lang, langs, cache, verbose, gt_dir)
            DB._memo[key] = db
        return db

    @staticmethod
    def _load_impl(lang: str = "zh", langs: Iterable[str] = ("zh", "en"), cache: bool = True,
                   verbose: bool = False, gt_dir=None) -> OfflineDB:
        """真正加载（只应被 `load()` 调用一次/进程）。"""
        langs = list(dict.fromkeys([lang] + list(langs)))
        apath = Path(gt_dir) / "resources" / "app.asar" if gt_dir else paths.asar_path()
        if apath is None or not Path(apath).exists():
            raise FileNotFoundError(
                "找不到 Grim Tools 离线库（resources/app.asar）。\n"
                "请确认已安装 Grim Tools 桌面版，或设置环境变量 GD_GT_DIR 指向其安装目录。"
            )
        asar = Asar(apath)
        root = _extract(asar, langs, verbose)
        fp = asar.fingerprint([ASAR_FILES["itemdb"], ASAR_FILES["itemdb_diff"]] +
                              [_L10N_TPL.format(lang=l) for l in langs])
        # ★ 必须用稳定哈希：Python 内置 hash() 每进程随机化，会生成无穷多个缓存文件
        digest = hashlib.sha1(fp.encode("utf-8")).hexdigest()[:12]
        cache_file = root / f"db_{digest}_{CACHE_VERSION}.pkl"

        t0 = time.time()
        if cache and cache_file.exists():
            try:
                with cache_file.open("rb") as f:
                    raw, l10n, meta = pickle.load(f)
                if verbose:
                    print(f"  命中缓存 {cache_file.name}  {time.time() - t0:.2f}s")
                db = OfflineDB(raw, l10n, lang, meta=meta)
                db._cache_dir = root
                return db
            except Exception as e:
                if verbose:
                    print(f"  缓存失效（{e}），重新解析")
        raw, l10n, _ = _parse_all(root, langs, verbose)
        meta = {"fingerprint": fp, "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "asar": str(apath)}
        if cache:
            try:
                tmp = cache_file.with_suffix(".tmp")
                with tmp.open("wb") as f:
                    pickle.dump((raw, l10n, meta), f, protocol=pickle.HIGHEST_PROTOCOL)
                tmp.replace(cache_file)
                for old in root.glob(f"db_*_{CACHE_VERSION}.pkl"):
                    if old != cache_file:
                        old.unlink(missing_ok=True)
                if verbose:
                    print(f"  缓存写入 {cache_file.name} "
                          f"({cache_file.stat().st_size / 1048576:.1f} MB)")
            except Exception as e:
                if verbose:
                    print(f"  缓存写入失败（不影响使用）：{e}")
        db = OfflineDB(raw, l10n, lang, meta=meta)
        db._cache_dir = root
        return db
