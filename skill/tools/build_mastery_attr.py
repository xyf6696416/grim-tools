# -*- coding: utf-8 -*-
"""从游戏 .arz 抽 `_classtraining_classNN.dbr` 的逐级属性奖励 → data/mastery_attr.json

为什么需要：`gd/rotation.py` 原来用的是「每级固定 4/4/3」这类线性近似，实测对
class01/class02 等**是错的**（真实是 3.5/1.5 这类半值取整）。逐级曲线就在
`_classtraining_classNN.dbr` 里，100 级一条，直接抄。

    属性 01 = 体格(Strength)  02 = 狡诈(Dexterity)  03 = 精神(Intelligence)
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools", "legacy"))
import gd_dbr as D  # noqa: E402

GD = r"E:\SteamLibrary\steamapps\common\Grim Dawn"
ARZ = [("base", os.path.join(GD, "database", "database.arz")),
       ("gdx1", os.path.join(GD, "gdx1", "database", "GDX1.arz")),
       ("gdx2", os.path.join(GD, "gdx2", "database", "GDX2.arz")),
       ("gdx3", os.path.join(GD, "gdx3", "database", "GDX3.arz"))]
db = D.open_all(ARZ)

out = {}
for rec in sorted(db.records_like("records/skills/playerclass")):
    if "_classtraining_" not in rec:
        continue
    cls = os.path.basename(rec).replace("_classtraining_", "").replace(".dbr", "")
    f = db.fields(rec) or {}
    if not f:
        continue
    rows = {}
    for src, dst in (("characterStrength", "physique"),
                     ("characterDexterity", "cunning"),
                     ("characterIntelligence", "spirit")):
        v = f.get(src)
        rows[dst] = [int(round(x)) for x in (v or [])]
    n = min(len(v) for v in rows.values())
    out[cls] = {k: v[:n] for k, v in rows.items()}
    print("%-9s 级数=%d  1级=%s  50级=%s" % (
        cls, n,
        {k: v[0] for k, v in out[cls].items()},
        {k: v[49] if n > 49 else '-' for k, v in out[cls].items()}))

dst = os.path.join(HERE, "data", "mastery_attr.json")
json.dump(out, open(dst, "w", encoding="utf-8"), ensure_ascii=False)
print("\n已写入", dst, "共", len(out), "个职业")
