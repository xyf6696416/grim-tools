# -*- coding: utf-8 -*-
"""从 app.asar 抽取 itemCostFormulae（嵌套：cfN -> {equation}）→ data/cost_formulae.json"""
import os, re, json
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
s = open(r"E:\Grim Tools\resources\app.asar", "rb").read().decode("latin1")
start = s.find("itemCostFormulae={")
i = start + len("itemCostFormulae=")


def match_brace(s, i):
    depth, j = 0, i
    while j < len(s):
        ch = s[j]
        if ch == '"':
            j += 1
            while j < len(s) and s[j] != '"':
                if s[j] == "\\":
                    j += 1
                j += 1
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


end = match_brace(s, i)
body = s[i:end + 1]

# 顶层解析：cfN:{...}
out = {}
pos = 1
while pos < len(body):
    m = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*:\s*\{').search(body, pos)
    if not m:
        break
    key = m.group(1)
    ob = body.index("{", m.start())
    oe = match_brace(body, ob)
    inner = body[ob:oe + 1]
    d = dict(re.findall(r'([A-Za-z_][A-Za-z0-9_]*)\s*:\s*"([^"]*)"', inner))
    out[key] = d
    pos = oe + 1

print("公式集:", sorted(out.keys()))
for k in sorted(out, key=lambda x: (len(x), x)):
    print("  %-6s %d 条" % (k, len(out[k])))

# defaultItemCostFormula
dm = re.search(r'defaultItemCostFormula\s*=\s*"([^"]+)"', s)
default = dm.group(1) if dm else "cf1"
print("defaultItemCostFormula =", default)

dst = os.path.join(HERE, "data", "cost_formulae.json")
json.dump({"default": default, "sets": out}, open(dst, "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
print("已写入", dst)
