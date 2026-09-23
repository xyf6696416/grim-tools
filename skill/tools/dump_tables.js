// 从离线版 calc.js 导出 GT 官方静态表 -> JSON（通用版）
// 用法: node dump_tables.js <calc.js> [out.json]
const fs = require('fs');
const s = fs.readFileSync(process.argv[2], 'utf8');

const NAMES = ['ye', 'we', 'Bd', 'xe', 've', 'Mf', 'Bf', 'sd', 'dg', 'Sf', 'Zf', 'se', 'te', 'ue',
  'Ed', 'De', 'Qc', 'ze', 'xc', 'Yb', 'Wc', 'sc', 'ad', 'oe', 'pe', 'Ge', 'Ac', 'xc', 'Ee', 'Fe',
  'ne', 'Ae', 'Be', 'Ce', 'cg'];

const RE_STR = /^[A-Za-z_$][\w$]*$/;

function grab(name) {
  const re = new RegExp('(?:^|[;,])\\s*' + name + '\\s*=(?!=)', 'g');
  let m;
  while ((m = re.exec(s))) {
    const start = m.index + m[0].length;
    // 平衡扫描 RHS，直到深度为 0 的 , 或 ;
    let d = 0, k, inStr = null;
    for (k = start; k < s.length; k++) {
      const c = s[k];
      if (inStr) { if (c === '\\') { k++; continue; } if (c === inStr) inStr = null; continue; }
      if (c === '"' || c === "'") { inStr = c; continue; }
      if (c === '{' || c === '[' || c === '(') d++;
      else if (c === '}' || c === ']' || c === ')') { d--; if (d < 0) { k--; break; } }
      else if ((c === ';' || c === ',') && d === 0) break;
    }
    const raw = s.slice(start, k).trim();
    if (!raw || raw.length > 200000) continue;
    try {
      const v = eval('(' + raw
        .replace(/([{\[,])\s*([A-Za-z_$][\w$]*)\s*:/g, '$1"$2":')
        .replace(/,\s*([}\]])/g, '$1') + ')');
      if (v !== undefined) return { v, raw };
    } catch (e) { /* 继续找下一处同名赋值 */ }
  }
  return undefined;
}

const out = {};
for (const n of NAMES) {
  const r = grab(n);
  out[n] = r === undefined ? null : r.v;
}
process.stdout.write(JSON.stringify(out, null, 1));
