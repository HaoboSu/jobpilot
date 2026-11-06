// Pure helper regression tests; no browser or third-party packages required.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('web/app.js', 'utf8');
const start = source.indexOf('  const safeJobURL =');
const end = source.indexOf('  // ---------------------------------------------------------- i18n layer', start);
assert.ok(start >= 0 && end > start);
const safeJobURL = vm.runInNewContext(`${source.slice(start, end)}\nsafeJobURL`, { URL });
for (const value of ['javascript:alert(1)', 'data:text/html,test', 'file:///tmp/example', '//example.com', 'https://user:secret@example.com', '']) {
  assert.equal(safeJobURL(value), '', value);
}
assert.equal(safeJobURL('https://example.com/jobs/1'), 'https://example.com/jobs/1');
assert.equal(safeJobURL('http://example.com/jobs/2'), 'http://example.com/jobs/2');
const context = { window: {} };
vm.runInNewContext(fs.readFileSync('web/i18n.js', 'utf8'), context);
for (const label of ['打开职位原文', '本地存储 · 外部服务可选', '✓ 规则检查通过 · 请人工复核']) {
  assert.ok(context.window.JP_I18N.en[label], label);
}
console.log('Frontend URL and translation checks passed.');
