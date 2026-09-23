"""探针：测 .arz 记录解码速度，并看关键字段（一次性工具）。

    python tools/probe_arz.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "legacy"))
import gd_dbr as D          # noqa: E402

GD = r"E:\SteamLibrary\steamapps\common\Grim Dawn"
ARZ = [("base", os.path.join(GD, "database", "database.arz")),
       ("gdx1", os.path.join(GD, "gdx1", "database", "GDX1.arz")),
       ("gdx2", os.path.join(GD, "gdx2", "database", "GDX2.arz")),
       ("gdx3", os.path.join(GD, "gdx3", "database", "GDX3.arz"))]


def main():
    total = 0
    for tag, path in ARZ:
        if not os.path.exists(path):
            print("跳过（不存在）", path)
            continue
        t0 = time.time()
        a = D.ArzDb(path, tag)
        print(f"{tag:5} {os.path.basename(path):16} 加载 {time.time() - t0:5.2f}s  "
              f"记录 {len(a.records):7d}  串池 {len(a.strings):6d}")
        total += len(a.records)

        recs = [r for r in a.records if r.startswith("records/items/")
                and r.endswith(".dbr")]
        t0 = time.time()
        n = 0
        for r in recs[:300]:
            if a.fields(r):
                n += 1
        dt = time.time() - t0
        print(f"      items 记录 {len(recs)}；解码 300 条 {dt:.3f}s "
              f"({dt / max(n, 1) * 1000:.2f} ms/条) → 全量约 {dt / 300 * len(recs):.1f}s")

        for want in ("c204_sword2h", "comp_bladeaura_02"):
            hit = [r for r in a.records if want in r]
            for h in hit[:1]:
                f = a.fields(h)
                keys = [k for k in f if "Name" in k or "Level" in k or "Scale" in k
                        or "Protection" in k or "Pierce" in k]
                print(f"     {want}: {h}")
                print(f"        { {k: f[k] for k in keys} }")
    print("记录合计:", total)


if __name__ == "__main__":
    main()
