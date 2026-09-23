"""迁移：把旧技能从游戏 .arz 抽出的技能库裁剪成「职业技能表」。

背景：Grim Tools 的离线库（app.asar）**不含职业技能的逐级数值** ——
`dbMasteryData` 里每个技能只有 `{name: tag}`，真正的数值在游戏的 `database.arz` 里。

旧技能 `gt_extract.py` 已从 .arz 抽好 `gt_data/skills.json`（12372 条，含逐级数值）。
本脚本把它裁剪为 `data/mastery_skills.json`：
  * 只保留 `records/skills/playerclass*`（玩家职业技能树）
  * 以 `tag` 建索引 —— 因为离线库里的 `dbMasteryData[skXXXX].name` 就是那个 tag，
    两边可以直接对上（实测 `tagClass04SkillName07A` → veilofshadows1.dbf ✅）

用法：
    python tools/migrate_mastery_skills.py <旧skills.json> [输出路径]
"""

from __future__ import annotations

import json
import pathlib
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = pathlib.Path(sys.argv[1])
    dst = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else \
        pathlib.Path(__file__).resolve().parent.parent / "data" / "mastery_skills.json"

    raw = json.loads(src.read_text(encoding="utf-8"))
    out, dropped = {}, 0
    for rec, v in raw.items():
        if not isinstance(v, dict) or not v.get("tag"):
            dropped += 1
            continue
        if "/playerclass" not in rec:
            dropped += 1
            continue
        tag = v["tag"]
        prev = out.get(tag)
        # 同一 tag 可能有多个记录（怪物借用玩家技能）；优先带 tier 的玩家版本
        if prev is None or (v.get("tier") is not None and prev.get("tier") is None):
            out[tag] = {"record": rec, **v}

    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"输入 {len(raw)} 条  ->  保留 {len(out)} 条（丢弃 {dropped}）")
    print(f"写入 {dst}  {dst.stat().st_size / 1048576:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
