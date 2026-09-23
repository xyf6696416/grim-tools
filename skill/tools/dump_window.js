// 从离线 js 里按名导出 `window.<name>` —— 通用版（配合 dump_ids.js）
// 用法: node dump_window.js <file.js> <name> [<name> ...]
//       → stdout 一个 JSON: { "<name>": {...} | null }
//
// 为什么与 dump_ids.js 分开：那里的目标是 `id:{...}`（键无 window. 前缀，
// 靠左边界判词），这里是 `window.X = {...}`（要跳过 `=` 与空白，且允许 `[` 开头）。
// 两者共用「平衡扫描 + 键加引号 + eval」手法 —— 这是唯一能拿到嵌套子对象
// 且不受 `!0`/`!1`/无引号键影响的做法。
const fs = require('fs');
const s = fs.readFileSync(process.argv[2], 'utf8');
const names = process.argv.slice(3);

function grab(name) {
  let from = 0;
  const needle = 'window.' + name;
  while (true) {
    const i = s.indexOf(needle, from);
    if (i < 0) return null;
    // `window.monsters` 不能命中 `window.monsterData` / `window.monsterTier`
    const after = s[i + needle.length];
    if (after && /[\w$]/.test(after)) { from = i + 1; continue; }
    let k = i + needle.length;
    while (k < s.length && /\s/.test(s[k])) k++;
    if (s[k] !== '=') { from = i + 1; continue; }
    k++;
    while (k < s.length && /\s/.test(s[k])) k++;
    if (s[k] !== '{' && s[k] !== '[') { from = i + 1; continue; }
    let d = 0, q, inStr = null;
    for (q = k; q < s.length; q++) {
      const c = s[q];
      if (inStr) {
        if (c === '\\') { q++; continue; }
        if (c === inStr) inStr = null;
        continue;
      }
      if (c === '"' || c === "'") { inStr = c; continue; }
      if (c === '{' || c === '[' || c === '(') d++;
      else if (c === '}' || c === ']' || c === ')') { d--; if (d === 0) break; }
    }
    const raw = s.slice(k, q + 1);
    try {
      const v = eval('(' + raw
        .replace(/([{\[,])\s*([A-Za-z_$][\w$]*)\s*:/g, '$1"$2":')
        .replace(/,\s*([}\]])/g, '$1') + ')');
      if (v && typeof v === 'object') return v;
    } catch (e) { /* 继续找下一处 */ }
    from = q;
  }
}

const out = {};
for (const n of names) out[n] = grab(n);
process.stdout.write(JSON.stringify(out));
