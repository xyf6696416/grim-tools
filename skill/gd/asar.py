"""asar 读取器（纯 Python，零依赖）。

Electron 的 app.asar 格式：
    [0:4]   = 4                        （pickle 头部长度字段，固定）
    [4:8]   = headerSize               （pickle 总长 = 4 + jsonLen 的 8 字节对齐值）
    [8:12]  = headerSize + 4
    [12:16] = jsonLen                  （目录树 JSON 的字节数）
    [16:16+jsonLen] = JSON 目录树
    文件数据区起点 = 8 + headerSize

用法：
    a = Asar(path)
    a.list("itemdb")
    a.read("/dist/calc/calc.js")           # -> bytes
    a.extract("/dist/calc/calc.js", dest)
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Iterator, Optional

__all__ = ["Asar"]


class Asar:
    def __init__(self, path):
        self.path = Path(path)
        with self.path.open("rb") as f:
            head = f.read(16)
            if len(head) < 16:
                raise ValueError(f"不是合法的 asar：{self.path}")
            self.header_size = struct.unpack("<I", head[4:8])[0]
            json_len = struct.unpack("<I", head[12:16])[0]
            blob = f.read(json_len)
            if len(blob) != json_len:
                raise ValueError(f"asar 头被截断：{self.path}")
        self.tree = json.loads(blob.decode("utf-8"))
        self.data_start = 8 + self.header_size

    # ----------------------------------------------------------- 目录
    def _walk(self, node=None, prefix="") -> Iterator[tuple[str, dict]]:
        node = self.tree if node is None else node
        for name, entry in (node.get("files") or {}).items():
            full = f"{prefix}/{name}"
            if "files" in entry:
                yield from self._walk(entry, full)
            else:
                yield full, entry

    def list(self, keyword: str = "") -> list[str]:
        out = [p for p, _ in self._walk() if not keyword or keyword in p]
        return sorted(out)

    def entries(self) -> dict[str, dict]:
        return {p: e for p, e in self._walk()}

    def stat(self, name: str) -> Optional[dict]:
        return self.entries().get(name)

    # ----------------------------------------------------------- 读取
    def read(self, name: str) -> bytes:
        """读取 asar 内某个文件（name 形如 /dist/calc/calc.js）。"""
        entry = self.stat(name)
        if entry is None:
            raise KeyError(f"asar 内不存在：{name}")
        with self.path.open("rb") as f:
            f.seek(self.data_start + int(entry["offset"]))
            return f.read(int(entry["size"]))

    def extract(self, name: str, dest) -> Path:
        dest = Path(dest)
        if dest.is_dir() or dest.suffix == "":
            dest.mkdir(parents=True, exist_ok=True)
            dest = dest / Path(name).name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.read(name))
        return dest

    def fingerprint(self, names) -> str:
        """一组文件的 (size, offset) 指纹，用于缓存失效判定。"""
        ent = self.entries()
        parts = []
        for n in sorted(names):
            e = ent.get(n)
            parts.append(f"{n}:{e['size'] if e else '-'}")
        return "|".join(parts)

    def __repr__(self):
        return f"<Asar {self.path} files={len(self.list())}>"
