# 官方公式（逐字取自未混淆的 `/dist/calc/calc.js`）

> 这些公式**不再是逆向猜测**。桌面版的 calc.js 有函数名、有正则、有表，
> 可以直接读。本文件记录已移植的部分与实测对照。

## 1. 值缩放：`rc()` / `Af()`

```js
Hg = 20;                                     // 默认 jitter = 20%
function rc(d, g, h){                        // d=基础值 g=attributeScalePercent h=jitter%
    "undefined" == typeof h && (h = Hg);
    return [
        Math.floor( Math.min(Math.ceil(d*(1-h/100)), d-1) * (1+g/100) ),
        Math.floor( Math.max(Math.floor(d*(1+h/100)), d+1) * (1+g/100) )
    ];
}
function Af(d, g, h){ var x = rc(d,g,h); return (x[0]+x[1])/2; }   // 显示值 = 区间中点
```

Python：`db.fmt.rc(base, scale, jitter)` / `db.fmt.af(...)`

* 数学上中点 = `base × (1 + g/100)`，**但 `rc()` 额外给出 `[lo, hi]`**，
  可以直接和游戏提示框的 `[35-51]` 对照。
* 注意 JS 的舍入：`Math.round` 对 `.5` 一律向 `+∞`（Python 的 `round()` 是银行家舍入）。
  `gd/text.py::_jsround` 已按 JS 语义实现。

**实测（§78 记录的 c204_sword2h，`attributeScalePercent=60`，jitter 20）**

| 字段 | 基础 | `rc()` | 游戏提示框 | |
|---|---|---|---|---|
| `offensivePierceMin` | 27 | **[35, 51]** | `38 穿刺伤害 [35-51]` | ✓ |
| `offensivePhysicalModifier` | 135 | **[172, 259]** | `+182% 物理 [172-259]` | ✓ |
| `offensivePierceModifier` | 115 | **[147, 220]** | `+182% 穿刺 [147-220]` | ✓ |
| `offensiveSlowPhysicalModifier` | 135 | **[172, 259]** | `+220% 创伤 [172-259]` | ✓ |

4/4 命中。`python -m gd` 里没有直接暴露 `rc`，用 `db.fmt.rc(27, 60)` 自检。

## 2. 哪些字段参与缩放：`va()` / `Xc()`

```js
B = dg[x] || (x.match(eg) && !x.match(fg));
D = x.indexOf("offensive") == 0 ? attributeScalePercent : 0;
// Xc():
if (undefined == h || 0 == h || sd[d]) return g;    // 不缩放
Bf[d] && (h = 0);
return Af(g, h, p);
```

```
eg = /^(offensive|defensive|retaliation|character).+/
fg = /.+(ReqReduction|DurationMin|DurationMax|Chance|MaxResist|Global|XOR)$/
gg = /(.+)(Min|Max)$/
```

⇒ **只有 `offensive*` 拿得到物品的 `attributeScalePercent`（字段 `h`）；其余拿到 0。**

| 表 | 项数 | 含义 |
|---|---|---|
| `Mf`(=`Bf`) | 23 | 「以 `offensive` 开头但**明确不缩放**」的陷阱名单：`offensiveCritDamageModifier`、`offensiveLifeLeechMin`、`offensiveTauntMin`、`offensiveManaBurn*` … |
| `sd` | 12 | 另一张不缩放名单：`characterIncreasedExperience`、`characterLightRadius`、`characterIncreasedGold`、`characterManaRegen`、`characterTotalSpeed` … |
| `dg` | 11 | 「按技能等级取值」的名单（`skillCooldownReduction`、`skillManaCostReduction`、`skillProjectileSpeedModifier`…） |

**两条实测坑**

1. **值为 0 时不调缩放公式**（GT 源码是 `0 != S ? D = Xc(…,S,…) : D = S`）。
   不修会让大量空字段变成 `-0.5`，总伤害偏低约 **3.5%**。
   `gd/scale.py::Scaler.value()` 与 `range()` 都已加 0 值保护。
2. **`character*Modifier` 不能缩放**：用 `endswith('Modifier')` 判据会把
   `characterAttackSpeedModifier` 也算进去（攻速 2.50 → 2.72）。
   **必须先判 `startswith('offensive')`**。

## 3. 文本格式化引擎：`f()` / `l()` / `H()` / `r()`

```js
je = /\{([^\{\}]+)\}/g
ke = /%(\+?)(\d*\.?\d*)([sSfgdaAtz])(\d*)/g
Pf = /[dgf]/                      // 数值类型
Rf = /(\{\^[abcdefghiklmopqrstywz]\}|\^[abcdefghiklmopqrstywz])/ig   // 颜色
Tf = /\^n/g                       // 换行
Uf = / +/g   le = /\/\*.+?\*\//g   Vf = /\$/g   Of = /[\{\}]/g

function f(d){                       // d = 模板 tag，其余为实参
    var g = G(d), h = g, p;
    for (je.lastIndex=0; p=je.exec(g);) {          // 逐个 {...}
        var m = p[0]; p = p[1];
        var w;
        for (ke.lastIndex=0; w=ke.exec(m);) {      // {...} 内的每个 %指令
            var y = w[1],                          // '+' 号
                x = w[2] ? w[2].substr(1) || 0 : 0,// 精度
                B = w[3],                          // 类型
                D = +w[4];                         // 实参序号
            "z" == B && (x = 1);
            D = arguments[D + 1];
            p = p.replace(w[0], l(D, B, y, x));
        }
        h = h.replace(m, p);
    }
    h = r(h);                                      // 收空格 / 去注释 / 去 $
    return h.replace(Of, "");                      // 去掉残余花括号
}
function l(d, g, h, p, m){                         // 值, 类型, 加号, 精度, 舍入模式
    if (b.isArray(d)) return l(d[0],…) + "/" + l(d[1],…);
    if (Pf.test(g)) {
        if ("?" == d || "[X]" == d) d = h + d;
        else {
            g = m || sc.Gg;                        // sc = {Gg:0, floor:1, ceil:2}
            p = Math.pow(10, p);
            d = g == sc.ceil ? Math.ceil(d*p)/p : g == sc.floor ? Math.floor(d*p)/p : Math.round(d*p)/p;
            0 < d && (d = h + d);                  // 只有正值才补 '+'
        }
    }
    return d;
}
```

Python 移植：`gd/text.py::GTFormat.render()` / `._lenum()` / `pair_str()` / `value_label()`

**格式指令语义**

```
%  [+]?  [.精度]  <类型>  <实参序号>
类型: f/g/d = 数值（按精度四舍五入，正值且带 + 时补正号）
      t/z/a/s/S/A = 原样输出（t 用于平伤；z 强制精度 1）
实参序号 n → args[n]（0 起）
```

例：`{%+.0f0}% {^E}攻击速度` + `14` ⇒ `+14% 攻击速度`

**颜色表** `Sf`（41 项）：`a`aqua `b`blue `c`cyan `d`dark grey `e`brown `f`fuschia
`g`green `h`light-gold `i`indigo `k`khaki `l`olive `m`maroon `o`orange `p`purple
`q`lilac `r`red `s`silver `t`teal `w`white `y`yellow `z`light-blue …
（注意 `n` **不在**颜色表里，它是换行 `^n`）

**区间渲染**：GT 在 `Za(min,max)` 里先用 `DamageRangeFormat` = `{%.0f0}-{%.0f1}` 把区间
取整成字符串，再把这个字符串当普通值传给真正的标签模板（`{%t0}` 直接透传）。
所以 `pair_str()` 的做法是：模板类型不是数值时，按 `DamageRangeFormat` 的精度（0 位）取整，
`+` 只加在低位上（与 `tagDamageModifierCritDamageR` = `{%+.0f0}-{%.0f1}%` 一致）。

**本移植相对 GT 的两处刻意改进**

1. **非数值字符串透传时不补 `+`**。GT 在这里会输出 `NaN`；
   我们的 `value_label()` 会把已格式化的区间串直接填进模板。
2. **模板缺 `%` 占位符时按语义兜底拼接**。GT 里 `DamageDurationPoison` = `" {^E}点毒素伤害"`、
   `LevelRequirement` = `"玩家等级"` 都不带占位符（由调用方自己拼），
   `value_label()` 会按「伤害类 → 数值在前，其余 → 标签在前」补上，**保证数值不丢**。

## 4. 套装加成数组的语义

`itemSets[isXXX]` 里属性值是**数组**，长度 = 件数上限。实测（`is190`，4 件）：

```
offensivePoisonModifier      : [0, 120, 120, 120]
augmentSkillLevel1           : [0,   0,   3,   3]
itemSkillLevel               : [0,   0,   0,   1]
offensiveSlowPoisonMin       : [0,   0,   0, 100]
conversionPercentage         : [0,   0,   0,  50]
```

⇒ **下标 = 件数 − 1，值是「达到该件数时的累计总量」**（下标 0 恒为 0，因为 1 件不触发）。
GT 的 `Ub()` 也是从 `g = 2` 起循环。`Renderer.set_card()` 按此渲染成 `[N 件]` 分段。

## 5. 其它已确认的官方常量

| 名 | 值 | 用途 |
|---|---|---|
| `Hg` | 20 | 默认 jitter% |
| `Zb` | 2 | 区间合并模式（`Fd`/`Gd` 分支） |
| `sc` | `{Gg:0, floor:1, ceil:2}` | 舍入模式；0 = `Math.round` |
| `ne` | `{Common:1, Magical:2, Rare:3, Epic:4, Legendary:5, Quest:6}` | 品质排序 |
| `ze` | `{Common:1, Champion:2, Hero:3, Quest:4, Boss:5, SuperBoss:6}` | 怪物强度排序 |
| `Wc` | 怪物类型 → 颜色 | `Common`white `Champion`yellow `Hero`orange `Quest`purple `Boss`red … |
| `Ac` | 9 项 | 角色速度/能力类字段名 |
| `xc` | 16 项 | 攻击类伤害类型（`Physical BonusPhysical Pierce PierceRatio Elemental Fire …`） |
| `Yb` | 9 项 | DoT 类型（`Physical Bleeding Fire Cold Lightning Poison Life LifeLeach ManaLeach`） |
| `ye` | 7 项 | 基础平伤类型（`BaseFire BaseCold BaseLightning BasePoison BaseLife BaseAether BaseChaos`） |

## 6. 标签命名规律（实测自 16563 条中文）

```
基础平伤   offensiveBase<Type>Min/Max  -> tagDamageBase<Type>     (Life → Vitality)
平伤       offensive<Type>Min/Max      -> Damage<Type>
% 加成     offensive<Type>Modifier     -> DamageModifier<Type>
DoT 平伤   offensiveSlow<Type>Min/Max  -> DamageDuration<Type>
DoT %      offensiveSlow<Type>Modifier -> DamageDurationModifier<Type>
```

`<Type>` ∈ `Physical Pierce Bleeding Fire Cold Lightning Poison Life Aether Chaos Elemental`。
组合结果会用中文语言包**逐条校验**，命中才写入 `db._compositional_tags()`。

## 7. 仍有缺口

* `comp_bladeaura_02.dbr` 的 `offensivePierceMin: 10` 用官方公式（g=0）只能得到
  `[9, 11]` 中点 10，**游戏提示框却显示 156**。全库确认该记录唯一、无第二版本。
  ⇒ 结论：**以游戏内提示框为准**；机制可能是 item skill 的运行时放大，**仍未查清**。
* `devotionConstellation*` 的星图字段仍被混淆（`Ga`/`Ha`/`za`/`Ba`…），完整星图未破译。
