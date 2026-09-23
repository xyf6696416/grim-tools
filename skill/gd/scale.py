"""属性等级缩放 —— calc.js 的 va()/Xc() 判据移植。

一句话：**不是所有字段都值得按等级缩放**。
GT 的规则（逐字来自未混淆的 calc.js）：

    B = dg[x] || (x.match(eg) && !x.match(fg))
    D = x.startsWith("offensive") ? attributeScalePercent : 0
    Xc(field, base, scale) :
        if scale is None or scale == 0 or field in sd: return base      # 不缩放
        if field in Bf: scale = 0
        return Af(base, scale, jitter)

    eg = /^(offensive|defensive|retaliation|character).+/
    fg = /.+(ReqReduction|DurationMin|DurationMax|Chance|MaxResist|Global|XOR)$/

即：
  * 只有 `offensive*` 开头才拿得到物品的 attributeScalePercent；其余拿到 0（⇒ 不缩放）
  * `Mf`/`Bf`（同一张表，23 项）是「以 offensive 开头但明确不缩放」的陷阱名单
    （暴击伤害、吸血、嘲讽、燃魔……）
  * `sd`（12 项）是另一张不缩放名单（经验、光照、回蓝、总速度……）
  * `dg`（11 项）是「按技能等级取值」的名单，不走缩放
  * 值为 0 时压根不调缩放公式 —— 否则 rc(0,g) 会算出 -0.5，把总伤害压低约 3.5%
"""

from __future__ import annotations

import re
from typing import Any, Optional

__all__ = ["Scaler"]

EG = re.compile(r"^(offensive|defensive|retaliation|character).+")
FG = re.compile(r".+(ReqReduction|DurationMin|DurationMax|Chance|MaxResist|Global|XOR)$")
GG = re.compile(r"(.+)(Min|Max)$")


class Scaler:
    def __init__(self, tables: dict, fmt):
        self.tables = tables or {}
        self.fmt = fmt
        self.Mf = set((self.tables.get("Mf") or {}).keys()) | set((self.tables.get("Bf") or {}).keys())
        self.sd = set((self.tables.get("sd") or {}).keys())
        self.dg = set((self.tables.get("dg") or {}).keys())

    # ------------------------------------------------------------ 判据
    def scales(self, field: str, attribute_scale_percent: float = 0.0) -> Optional[float]:
        """返回该字段应当使用的缩放系数；None 表示「按等级取值」，不参与缩放。"""
        if field in self.dg:
            return None
        if not (EG.match(field) and not FG.match(field)):
            return 0.0
        if field.startswith("offensive"):
            return float(attribute_scale_percent or 0)
        return 0.0

    def is_scaled(self, field: str) -> bool:
        return field.startswith("offensive") and field not in self.Mf and field not in self.sd

    # ------------------------------------------------------------ 取值
    def value(self, field: str, base: Any, attribute_scale_percent: float = 0.0,
              jitter: Optional[float] = None) -> Any:
        """按官方口径把「基础值」变成「提示框显示值」（区间中点）。"""
        if isinstance(base, (list, tuple)):
            return [self.value(field, base[0], attribute_scale_percent, jitter),
                    self.value(field, base[-1], attribute_scale_percent, jitter)]
        if not isinstance(base, (int, float)) or isinstance(base, bool):
            return base
        # ★ 0 值不缩放（否则会变成 -0.5）
        if base == 0:
            return base
        s = self.scales(field, attribute_scale_percent)
        if s is None or s == 0:
            return base
        return self.fmt.af(base, s, jitter)

    def range(self, field: str, base: Any, attribute_scale_percent: float = 0.0,
              jitter: Optional[float] = None):
        """返回官方区间 [lo, hi]（可直接和游戏提示框的 [35-51] 对照）。"""
        if not isinstance(base, (int, float)) or isinstance(base, bool):
            return None
        if base == 0:
            return [0, 0]
        s = self.scales(field, attribute_scale_percent)
        if s is None or s == 0:
            return [base, base]
        return self.fmt.rc(base, s, jitter)

    def pair(self, field_min: str, vmin: Any, vmax: Any, attribute_scale_percent: float = 0.0,
             jitter: Optional[float] = None):
        """处理 `xxxMin` / `xxxMax` 成对字段（GT 会在两者不等时才输出区间）。"""
        a = self.value(field_min, vmin, attribute_scale_percent, jitter)
        m = GG.match(field_min)
        fmax = (m.group(1) + "Max") if m else field_min
        b = self.value(fmax, vmax, attribute_scale_percent, jitter)
        return a, b
