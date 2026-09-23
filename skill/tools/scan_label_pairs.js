// 从 GT 前端(db.js) 与引擎(calc.js) 中扫描所有 `someField:"someTag"` 形式的映射对。
// 输出 JSONL: {"field":..., "tag":..., "src":"db|calc"}
const fs = require('fs');

const files = process.argv.slice(2);
const seen = new Set();
const out = [];

const RE = /([{,]\s*)([a-z][A-Za-z0-9_]*)\s*:\s*"([A-Za-z][A-Za-z0-9_]*)"\s*(?=[,}])/g;

for (const f of files) {
  const s = fs.readFileSync(f, 'utf8');
  let m;
  while ((m = RE.exec(s))) {
    const field = m[2], tag = m[3];
    const k = field + '\u0000' + tag;
    if (seen.has(k)) continue;
    seen.add(k);
    out.push({ field, tag, src: f.includes('calc.js') ? 'calc' : 'db' });
  }
}

process.stdout.write(out.map(o => JSON.stringify(o)).join('\n') + '\n');
