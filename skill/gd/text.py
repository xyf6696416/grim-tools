"""多语言文本 + GT 官方格式引擎（calc.js 的 f()/l()/H() 逐字移植）。

GT 的文本不是简单字符串，而是**带格式指令的模板**，例如：

    tagCharAttackSpeed   ->  "{%+.0f0}% {^E}攻击速度"
    DamageFire           ->  "{%t0} {^E}火焰伤害"
    DamageModifierPierce ->  "{%+.0f0}% {^E}穿刺伤害"

格式指令 `%` 后面的语法（对应 calc.js 的 ke 正则）：

    %  [+?]  [.精度]  <类型>  <参数序号>

类型：s S f g d a A t z
    f/g/d  —— 数值，按精度四舍五入；值 > 0 且带 `+` 时补正号
    t/z/a/s/S/A —— 原样输出（t 用于平伤，z 强制精度 1）
参数序号 n 表示取第 n 个实参（0 起）。

颜色指令：`{^X}` / `^X`，X ∈ Sf 映射表（见 data/gt_tables.json）。
换行：`^n`。

本模块的 render() 输出与 GT 页面/游戏提示框逐字一致（去色后）。
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Optional

from . import paths

__all__ = ["Localizer", "GTFormat", "LANGS"]

LANGS = "en cs de es fr it ja ko pl pt ru vi zh".split()

# ---- calc.js 里的原正则（逐字照搬）----
RE_BRACE = re.compile(r"\{([^\{\}]+)\}")
RE_FMT = re.compile(r"%(\+?)(\d*\.?\d*)([sSfgdaAtz])(\d*)")
RE_NUMTYPE = re.compile(r"[dgf]")
RE_COLOR = re.compile(r"(\{\^([abcdefghiklmopqrstywz])\}|\^([abcdefghiklmopqrstywz]))", re.I)
RE_NL = re.compile(r"\^n")
RE_SPECIAL = re.compile(r"\{%_(s|3a)(\d+)\}")
# r() 里的三连
RE_MULTISPACE = re.compile(r" +")
RE_COMMENT = re.compile(r"/\*.+?\*/", re.S)
RE_DOLLAR = re.compile(r"\$")
RE_ALLBRACE = re.compile(r"[\{\}]")

ANSI = {
    "aqua": "\033[96m", "blue": "\033[94m", "cyan": "\033[96m", "dark grey": "\033[90m",
    "brown": "\033[33m", "fuschia": "\033[95m", "green": "\033[92m", "light-gold": "\033[93m",
    "indigo": "\033[35m", "khaki": "\033[93m", "olive": "\033[33m", "maroon": "\033[31m",
    "orange": "\033[33m", "purple": "\033[35m", "lilac": "\033[95m", "red": "\033[91m",
    "silver": "\033[37m", "teal": "\033[36m", "white": "\033[97m", "yellow": "\033[93m",
    "light-blue": "\033[94m", "gold": "\033[93m", "gray": "\033[90m", "grey": "\033[90m",
    "dark-gold": "\033[33m", "light-green": "\033[92m",
}


def _jsnum(x: Any) -> str:
    """模拟 JS 的 Number->String（整数不带 .0）。"""
    if isinstance(x, bool):
        return "true" if x else "false"
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        if x != x:
            return "NaN"
        if x == float("inf"):
            return "Infinity"
        if x == float("-inf"):
            return "-Infinity"
        if x.is_integer() and abs(x) < 1e21:
            return str(int(x))
        return repr(x)
    return str(x)


def _jsround(x: float) -> float:
    """JS Math.round：.5 一律向 +∞ 取整（Python 的 round 是银行家舍入）。"""
    return math.floor(x + 0.5)


class GTFormat:
    """GT 格式引擎。tables 来自 data/gt_tables.json 的 tables。"""

    def __init__(self, tables: Optional[dict] = None, l10n: Optional["Localizer"] = None,
                 jitter_default: float = 20.0):
        self.tables = tables or {}
        self.l10n = l10n
        self.Hg = jitter_default
        self.colors = {k.lower(): v for k, v in (self.tables.get("Sf") or {}).items()}

    # ---------------------------------------------------------- 格式化
    def _lenum(self, v: Any, typ: str, plus: str, prec: Any, mode: int = 0) -> Any:
        """calc.js l() 的移植。"""
        if isinstance(v, (list, tuple)):
            a = self._lenum(v[0], typ, plus, prec, 0)
            b = self._lenum(v[1], typ, plus, prec, 0)
            return f"{a}/{b}"
        if typ and RE_NUMTYPE.match(typ):
            if v == "?" or v == "[X]":
                return f"{plus}{v}"
            try:
                num = float(v)
            except (TypeError, ValueError):
                # 已经是渲染好的字符串（如区间 "12-18"）——原样透传，别再补正号
                return v
            p = 10 ** int(prec or 0)
            if mode == 2:
                num = math.ceil(num * p) / p
            elif mode == 1:
                num = math.floor(num * p) / p
            else:
                num = _jsround(num * p) / p
            if num > 0:
                return plus + _jsnum(num)
            return _jsnum(num)
        return v

    def _expand(self, inner: str, args: tuple) -> str:
        """替换一段 `{...}` 内部的所有 % 指令。"""
        def sub(m: re.Match) -> str:
            plus = m.group(1) or ""
            raw_prec = m.group(2)
            # 对应 JS: x = w[2] ? (w[2].substr(1) || 0) : 0
            prec = (raw_prec[1:] or "0") if raw_prec else "0"
            typ = m.group(3)
            if typ == "z":
                prec = "1"
            idx = int(m.group(4) or 0)
            val = args[idx] if idx < len(args) else ""
            out = self._lenum(val, typ, plus, prec, 0)
            return out if isinstance(out, str) else _jsnum(out)

        # {%_s0} / {%_3a0} 特例（calc.js Qf）
        inner = RE_SPECIAL.sub(lambda m: "%" + m.group(1) + m.group(2), inner)
        return RE_FMT.sub(sub, inner)

    # ---------------------------------------------------------- 颜色
    def colorize(self, s: str, mode: str = "plain") -> str:
        def sub(m: re.Match) -> str:
            code = m.group(2) or m.group(3)
            name = self.colors.get((code or "").lower())
            if mode == "html":
                if not name:
                    return ""
                return f'<span class="text-{name.replace(" ", "-")}">'
            if mode == "ansi":
                if not name:
                    return ""
                return ANSI.get(name.lower(), "")
            return ""
        s = RE_COLOR.sub(sub, s)
        if mode == "html":
            return RE_NL.sub("<br>", s)
        if mode == "ansi":
            return RE_NL.sub("\n", s)
        return RE_NL.sub("\n", s)

    # ---------------------------------------------------------- 主入口
    def render(self, template: Any, *args, mode: str = "plain") -> str:
        """把模板（tag 或多语言原文）渲染成最终文本。

        mode: plain（纯文本，默认）/ ansi（终端带色）/ html（GT 页面同款）
        """
        text = self.localize(template)
        if not isinstance(text, str):
            text = _jsnum(text)
        s = self.colorize(text, mode)
        out, pos = [], 0
        for m in RE_BRACE.finditer(s):
            out.append(s[pos:m.start()])
            out.append(self._expand(m.group(1), args))
            pos = m.end()
        out.append(s[pos:])
        h = "".join(out)
        # r() 三连 + Of
        h = RE_MULTISPACE.sub(" ", h)
        h = RE_COMMENT.sub("", h)
        h = RE_DOLLAR.sub("", h)
        return RE_ALLBRACE.sub("", h)

    def localize(self, template: Any) -> Any:
        if not isinstance(template, str):
            return template
        if self.l10n is not None:
            return self.l10n.get(template)
        return template

    # ---------------------------------------------------------- 区间 / 模板探测
    def has_placeholder(self, tag: str) -> bool:
        """模板里是否含有取值占位符（`%t0` / `%.0f0` …）。"""
        tpl = self.localize(tag)
        return isinstance(tpl, str) and bool(RE_FMT.search(tpl))

    def pair_str(self, tag: str, lo: Any, hi: Any) -> str:
        """把 Min/Max 两个值按**该标签自己的精度**拼成 "lo-hi"。

        精度取模板里的第一个 `%` 指令（如 `DamageRangeFormat` 的 `{%.0f0}-{%.0f1}`、
        `tagDamageModifierCritDamageR` 的 `{%+.0f0}-{%.0f1}%`）。
        `+` 只加在低位上（与 GT 的 R 系列模板一致）。
        """
        tpl = self.localize(tag)
        m = RE_FMT.search(tpl) if isinstance(tpl, str) else None
        if m is None:
            return f"{lo}-{hi}" if lo != hi else f"{lo}"
        plus, raw_prec, typ, _ = m.groups()
        if not RE_NUMTYPE.match(typ):
            # GT 的平伤/百分比行本身用 `%t0` 透传，真正的取整发生在 Za() 里：
            # 它先用 `DamageRangeFormat` = `{%.0f0}-{%.0f1}` 把区间取整成字符串。
            typ, prec = "f", "0"
        else:
            prec = (raw_prec[1:] or "0") if raw_prec else "0"
            if typ == "z":
                prec = "1"
        a = self._lenum(lo, typ, plus or "", prec, 0)
        b = self._lenum(hi, typ, "", prec, 0)
        a = a if isinstance(a, str) else _jsnum(a)
        b = b if isinstance(b, str) else _jsnum(b)
        return a if a == b else f"{a}-{b}"

    def value_label(self, tag: str, value: Any) -> str:
        """渲染一行「标签+数值」。模板缺占位符时按语义兜底拼接。

        GT 里少数标签（如 `DamageDurationPoison` = "点毒素伤害"、`LevelRequirement`
        = "玩家等级"）本身不带 `%` 占位符，是给调用方自己拼的，
        这里按「伤害类 → 数值在前，其余 → 标签在前」兜底，保证数值不丢。
        """
        tpl = self.localize(tag)
        if isinstance(tpl, str) and RE_FMT.search(tpl):
            return self.render(tag, value)
        txt = self.render(tag).strip()
        head = tag.split("_")[-1] if tag.startswith("ItemDB_") else tag
        if head.startswith("Damage") or head.startswith("tagDamage") or "点" in txt:
            return f"{value} {txt}".strip()
        return f"{txt} {value}".strip()

    # ---------------------------------------------------------- 数值
    def rc(self, base: float, scale_pct: float = 0.0, jitter: Optional[float] = None):
        """calc.js rc()：返回官方区间的 [lo, hi]。"""
        h = self.Hg if jitter is None else jitter
        d, g = float(base), float(scale_pct or 0)
        lo = math.floor(min(math.ceil(d * (1 - h / 100.0)), d - 1) * (1 + g / 100.0))
        hi = math.floor(max(math.floor(d * (1 + h / 100.0)), d + 1) * (1 + g / 100.0))
        return [lo, hi]

    def af(self, base: float, scale_pct: float = 0.0, jitter: Optional[float] = None) -> float:
        """calc.js Af()：区间中点 = 提示框显示值。"""
        lo, hi = self.rc(base, scale_pct, jitter)
        return (lo + hi) / 2.0


class Localizer:
    """多语言文本表。tag -> 当地语言。"""

    def __init__(self, texts: Optional[dict] = None, lang: str = "zh"):
        self.texts: dict[str, dict[str, str]] = texts or {}
        self.lang = lang if lang in self.texts else (next(iter(self.texts), "en"))

    @classmethod
    def load(cls, texts: dict, lang: str = "zh") -> "Localizer":
        return cls(texts, lang)

    def set_lang(self, lang: str):
        if lang in self.texts:
            self.lang = lang
        return self

    def get(self, tag: Any, lang: Optional[str] = None, default: Any = None) -> Any:
        """查表；查不到原样返回（GT 的行为）。"""
        if not isinstance(tag, str) or not tag:
            return default if default is not None else tag
        table = self.texts.get(lang or self.lang) or {}
        if tag in table:
            return table[tag]
        # 回退：先去 en，再原样
        en = self.texts.get("en") or {}
        if tag in en:
            return en[tag]
        return default if default is not None else tag

    def has(self, tag: str) -> bool:
        return any(tag in (v or {}) for v in self.texts.values())

    def search(self, keyword: str, lang: Optional[str] = None, limit: int = 40):
        """按文本内容模糊搜索 tag。"""
        table = self.texts.get(lang or self.lang) or {}
        kw = keyword.lower()
        hits = [(k, v) for k, v in table.items() if kw in v.lower()]
        return hits[:limit]

    def __len__(self):
        return len(self.texts.get(self.lang) or {})
