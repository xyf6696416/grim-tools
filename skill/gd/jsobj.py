"""极简 JS 对象字面量解析器（纯 Python，零依赖）。

Grim Tools 的离线库（itemdb.js / itemdb_diff.js / l10n/*.js / map/data.js）都是
由脚本生成的「机器产物」，语法是 JSON 的子集 + 三样东西：

  1. 无引号的键        `{milestones:[...], maxLevel:100}`
  2. 单引号字符串      `db_l10n_texts['zh']={...}`
  3. 顶层带前缀的赋值  `window.playerBio={...};` / `versionUpdateDiffs.B27={...}`

所以不需要 JS 引擎：剥离赋值前缀后，剩下的就是可解析的字面量。
本模块只做这件事，不执行任何代码（安全：绝不 eval）。

用法：
    parse_assignments(text) -> list[(path, value)]
        path 形如 ["window", "playerBio"] 或 ["versionUpdateDiffs", "B27"]
"""

from __future__ import annotations

import re
from typing import Any, Iterable

__all__ = ["parse_literal", "parse_assignments", "JsLiteralError"]


class JsLiteralError(ValueError):
    pass


_IDENT_START = set("$_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
_IDENT_BODY = _IDENT_START | set("0123456789")
_NUM_BODY = set("0123456789.eE+-xXabcdefABCDEF")
_WS = " \t\r\n\f\v\u00a0\u2028\u2029\ufeff"

_ESCAPES = {
    "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v",
    "0": "\0", "'": "'", '"': '"', "\\": "\\", "/": "/",
}


class _Parser:
    __slots__ = ("s", "i", "n")

    def __init__(self, text: str):
        self.s = text
        self.i = 0
        self.n = len(text)

    # ---- 基础 ----
    def err(self, msg: str):
        lo = max(0, self.i - 40)
        raise JsLiteralError(f"{msg} @ {self.i}: ...{self.s[lo:self.i + 40]!r}")

    def skip(self):
        s, n = self.s, self.n
        i = self.i
        while i < n:
            c = s[i]
            if c in _WS:
                i += 1
            elif c == "/" and i + 1 < n and s[i + 1] == "/":
                j = s.find("\n", i)
                i = n if j < 0 else j + 1
            elif c == "/" and i + 1 < n and s[i + 1] == "*":
                j = s.find("*/", i + 2)
                i = n if j < 0 else j + 2
            else:
                break
        self.i = i

    # ---- 值 ----
    def value(self) -> Any:
        v = self.primary()
        return self.postfix(v)

    def primary(self) -> Any:
        self.skip()
        if self.i >= self.n:
            self.err("输入意外结束")
        c = self.s[self.i]
        if c == "{":
            return self.obj()
        if c == "[":
            return self.arr()
        if c in "'\"":
            return self.string()
        if c in "-+.0123456789":
            return self.number()
        if c == "!":
            # JS 布尔简写：!0 -> true, !1 -> false；多个 ! 取奇偶
            k = self.i
            while k < self.n and self.s[k] == "!":
                k += 1
            bangs = k - self.i
            self.i = k
            inner = self.primary()
            truth = bool(inner)
            return (not truth) if bangs % 2 else truth
        if c.isalpha() or c in "$_":
            return self.keyword()
        self.err(f"意外的字符 {c!r}")

    def postfix(self, v: Any) -> Any:
        """处理字面量上的少量方法调用（本库只用到 .split）。"""
        while True:
            self.skip()
            if self.i >= self.n or self.s[self.i] != ".":
                return v
            save = self.i
            self.i += 1
            self.skip()
            if self.i >= self.n or self.s[self.i] not in _IDENT_START:
                self.i = save
                return v
            j = self.i
            while j < self.n and self.s[j] in _IDENT_BODY:
                j += 1
            name = self.s[self.i:j]
            self.i = j
            self.skip()
            if self.i >= self.n or self.s[self.i] != "(":
                self.i = save
                return v
            self.i += 1
            args: list[Any] = []
            self.skip()
            if self.i < self.n and self.s[self.i] != ")":
                while True:
                    args.append(self.value())
                    self.skip()
                    if self.i < self.n and self.s[self.i] == ",":
                        self.i += 1
                        continue
                    break
            self.skip()
            if self.i < self.n and self.s[self.i] == ")":
                self.i += 1
            if name == "split" and isinstance(v, str) and args and isinstance(args[0], str):
                v = list(v) if args[0] == "" else v.split(args[0])
            elif name == "join" and isinstance(v, list) and args:
                v = str(args[0]).join(str(x) for x in v)
            else:
                return None

    def obj(self) -> dict:
        s = self.s
        self.i += 1  # {
        out: dict = {}
        while True:
            self.skip()
            if self.i >= self.n:
                self.err("对象未闭合")
            if s[self.i] == "}":
                self.i += 1
                return out
            k = self.member_key()
            self.skip()
            if self.i < self.n and s[self.i] == ":":
                self.i += 1
                out[k] = self.value()
            else:
                # ES5 简写 {foo} —— 本库不出现，容错处理为 true
                out[k] = True
            self.skip()
            if self.i < self.n and s[self.i] == ",":
                self.i += 1
            elif self.i < self.n and s[self.i] == "}":
                self.i += 1
                return out
            else:
                self.err("对象成员后缺少 , 或 }")

    def member_key(self) -> str:
        c = self.s[self.i]
        if c in "'\"":
            return self.string()
        if c in "-+.0123456789":
            v = self.number()
            return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)
        if c in _IDENT_START:
            j = self.i
            while j < self.n and self.s[j] in _IDENT_BODY:
                j += 1
            k = self.s[self.i:j]
            self.i = j
            return k
        self.err(f"非法的键 {c!r}")

    def arr(self) -> list:
        self.i += 1  # [
        out: list = []
        while True:
            self.skip()
            if self.i >= self.n:
                self.err("数组未闭合")
            if self.s[self.i] == "]":
                self.i += 1
                return out
            out.append(self.value())
            self.skip()
            if self.i < self.n and self.s[self.i] == ",":
                self.i += 1
            elif self.i < self.n and self.s[self.i] == "]":
                self.i += 1
                return out
            else:
                self.err("数组元素后缺少 , 或 ]")

    def string(self) -> str:
        s = self.s
        quote = s[self.i]
        i = self.i + 1
        n = self.n
        out: list[str] = []
        while i < n:
            c = s[i]
            if c == "\\":
                i += 1
                if i >= n:
                    break
                e = s[i]
                if e == "u":
                    out.append(chr(int(s[i + 1:i + 5], 16)))
                    i += 5
                    continue
                if e == "x":
                    out.append(chr(int(s[i + 1:i + 3], 16)))
                    i += 3
                    continue
                if e in "\r\n":
                    i += 2 if e == "\r" and i + 1 < n and s[i + 1] == "\n" else 1
                    continue
                out.append(_ESCAPES.get(e, e))
                i += 1
                continue
            if c == quote:
                self.i = i + 1
                return "".join(out)
            # 快路径：下一处特殊字符前整段拷贝
            j = i
            while j < n and s[j] != quote and s[j] != "\\":
                j += 1
            out.append(s[i:j])
            i = j
        self.err("字符串未闭合")

    def number(self) -> Any:
        s = self.s
        j = self.i
        if s[j] in "+-":
            j += 1
        while j < self.n and s[j] in _NUM_BODY:
            j += 1
        raw = s[self.i:j]
        self.i = j
        if raw[:2].lower() in ("0x", "+0", "-0") and raw.lstrip("+-")[:2].lower() == "0x":
            return int(raw, 16)
        try:
            if any(ch in raw for ch in ".eE"):
                return float(raw)
            return int(raw)
        except ValueError:
            # 形如 1.2.3 的脏数据
            m = re.match(r"[-+]?[\d.]+", raw)
            if not m:
                self.err(f"非法数字 {raw!r}")
            return float(m.group(0))

    def keyword(self) -> Any:
        s = self.s
        j = self.i
        while j < self.n and s[j] in _IDENT_BODY:
            j += 1
        word = s[self.i:j]
        if word in ("true", "false"):
            self.i = j
            return word == "true"
        if word == "null":
            self.i = j
            return None
        if word == "undefined":
            self.i = j
            return None
        if word in ("NaN", "Infinity"):
            self.i = j
            return float("nan") if word == "NaN" else float("inf")
        if word == "new":
            # `new Array(3)` / `new Date(...)` 之类：跳过，返回 None
            self.i = j
            self.skip()
            self.resync()
            return None
        self.err(f"未定义标识符 {word!r}")

    def resync(self) -> None:
        """失败后前进到下一个「顶层」分号（跳过字符串与括号内的分号）。"""
        s, n = self.s, self.n
        i = self.i
        depth = 0
        while i < n:
            c = s[i]
            if c in "'\"":
                self.i = i
                try:
                    self.string()
                    i = self.i
                except JsLiteralError:
                    return
                continue
            if c in "{[(":
                depth += 1
            elif c in "}])":
                depth -= 1
            elif c == ";" and depth <= 0:
                self.i = i + 1
                return
            i += 1
        self.i = n


def parse_literal(text: str, start: int = 0) -> tuple[Any, int]:
    """解析从 start 开始的一个字面量，返回 (值, 结束位置)。"""
    p = _Parser(text)
    p.i = start
    v = p.value()
    return v, p.i


def parse_assignments(text: str) -> list[tuple[list[str], Any]]:
    """解析形如 `a.b['c'] = <literal>;` 的顶层赋值，返回 [(路径, 值), ...]。

    路径以 `window` 开头时会被剥掉（本库的 `window.X=` 等同于全局 X）。
    """
    out: list[tuple[list[str], Any]] = []
    p = _Parser(text)
    s, n = text, len(text)
    while p.i < n:
        p.skip()
        if p.i >= n:
            break
        start = p.i
        # 1) 读路径
        path: list[str] = []
        c = s[p.i]
        if c in _IDENT_START or c in "'\"":
            path.append(p.member_key() if c not in "'\"" else p.string())
        else:
            # 无法识别的顶层语句：跳到下一个分号
            p.resync()
            continue
        while True:
            p.skip()
            if p.i < n and s[p.i] == ".":
                p.i += 1
                p.skip()
                path.append(p.member_key())
            elif p.i < n and s[p.i] == "[":
                p.i += 1
                p.skip()
                path.append(p.member_key())
                p.skip()
                if p.i < n and s[p.i] == "]":
                    p.i += 1
                else:
                    p.i = start
                    break
            else:
                break
        if p.i == start:
            p.resync()
            continue
        # 2) 赋值号
        p.skip()
        if p.i >= n or s[p.i] != "=":
            p.resync()
            continue
        p.i += 1
        # 3) 值
        try:
            val = p.value()
        except JsLiteralError:
            # 值不是纯字面量（含函数/运算）：跳到分号
            p.resync()
            continue
        if path and path[0] == "window":
            path = path[1:]
        if path:
            out.append((path, val))
        # 4) 分号
        p.skip()
        if p.i < n and s[p.i] == ";":
            p.i += 1
    return out


def assign_into(root: dict, path: Iterable[str], value: Any) -> None:
    """把 (path, value) 合并进 root（支持 `a.b.c` 与动态键）。"""
    keys = list(path)
    cur = root
    for k in keys[:-1]:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[k] = nxt
        cur = nxt
    if keys:
        cur[keys[-1]] = value
