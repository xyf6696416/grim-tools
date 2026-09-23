// 从离线 itemdb.js 里按 id 导出条目 —— 通用版
// 用法: node dump_ids.js <itemdb.js> <id> [<id> ...]
//       → stdout 一个 JSON: { "<id>": {...} | null }
//
// ★ 为什么不用正则解析整个文件：itemdb.js 是 JS 对象字面量（键无引号、含 !0/!1），
//   「平衡扫描 + 键加引号 + eval」是唯一能拿到**嵌套子对象**（如 playerSkillName）
//   的稳妥做法。tools/dump_tables.js 用的是同一套手法。
const fs = require('fs');
const s = fs.readFileSync(process.argv[2], 'utf8');
const ids = process.argv.slice(3);

const WORD = /[\w$]/;

function grab(id) {
  let from = 0;
  while (true) {
    const i = s.indexOf(id + ':{', from);
    if (i < 0) return null;
    // 左边界必须是分隔符，否则 "sk70" 会命中 "sk701:{"
    if (i > 0 && WORD.test(s[i - 1])) { from = i + 1; continue; }
    let d = 0, k, inStr = null;
    const start = i + id.length + 1;
    for (k = start; k < s.length; k++) {
      const c = s[k];
      if (inStr) {
        if (c === '\\') { k++; continue; }
        if (c === inStr) inStr = null;
        continue;
      }
      if (c === '"' || c === "'") { inStr = c; continue; }
      if (c === '{' || c === '[' || c === '(') d++;
      else if (c === '}' || c === ']' || c === ')') { d--; if (d === 0) break; }
    }
    const raw = s.slice(start, k + 1);
    try {
      const v = eval('(' + raw
        .replace(/([{\[,])\s*([A-Za-z_$][\w$]*)\s*:/g, '$1"$2":')
        .replace(/,\s*([}\]])/g, '$1') + ')');
      if (v && typeof v === 'object') return v;
    } catch (e) { /* 继续找下一处 */ }
    from = k;
  }
}

const out = {};
for (const id of ids) out[id] = grab(id);
process.stdout.write(JSON.stringify(out));
