/* screener_render.js 单测：XSS 转义 + NaN 防护 + 徽章分类（code-review 修复回归） */
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const R = require('./screener_render.js');

test('esc 转义 HTML 特殊字符（XSS 防护）', () => {
  assert.strictEqual(R.esc('格力电器'), '格力电器');
  assert.strictEqual(R.esc('<script>alert(1)</script>'), '&lt;script&gt;alert(1)&lt;/script&gt;');
  assert.strictEqual(R.esc('a&b"c\'d'), 'a&amp;b&quot;c&#39;d');
  assert.strictEqual(R.esc(null), '');
  assert.strictEqual(R.esc(undefined), '');
});

test('fmtNum 非数字不渲染 NaN（含缺数占位符 "—"）', () => {
  assert.strictEqual(R.fmtNum(9.68, 2), '9.68');
  assert.strictEqual(R.fmtNum('7.48', 2), '7.48');
  assert.strictEqual(R.fmtNum('—', 2), '');       // 缺数占位符
  assert.strictEqual(R.fmtNum('', 2), '');
  assert.strictEqual(R.fmtNum(null, 2), '');
  assert.strictEqual(R.fmtNum(undefined, 2), '');
  assert.strictEqual(R.fmtNum('abc', 2), '');
  assert.strictEqual(R.fmtNum(2246, 1), '2246.0');
});

test('zoneBadge 分类着色', () => {
  assert.match(R.zoneBadge('低估'), /badge-green/);
  assert.match(R.zoneBadge('合理偏低'), /badge-green/);
  assert.match(R.zoneBadge('高估'), /var\(--red/);
  assert.match(R.zoneBadge('合理'), /badge-gray/); // 无绿色/红色匹配 → 灰
  assert.match(R.zoneBadge(''), /badge-gray/);     // 空值 → 灰色空徽章（兜底）
});

test('susBadge 分类着色', () => {
  assert.match(R.susBadge('可持续'), /badge-green/);
  assert.match(R.susBadge('偏弱'), /badge-amber/);
  assert.match(R.susBadge('不可持续'), /badge-gray/);
});

test('rowHtml 完整 11 列 + 转义 + 数字格式化', () => {
  const r = {
    '代码': '600900', '名称': '长<江>电力', 'TTM股息率%': 3.80, '真实股息率%': 3.80,
    '估值区间': '低估', '市赚率PR': 0.52, '行业': '公用事业-电力',
    '可持续性': '可持续', 'ROE%': '—', '总市值(亿)': 6800.5, '数据来源': 'mootdx',
  };
  const html = R.rowHtml(r);
  assert.ok(html.includes('<td>600900</td>'));
  assert.ok(html.includes('长&lt;江&gt;电力'));     // 名称被转义
  // 列序：真实股息率(3) → ROE(7) → TTM(9)；ROE% 占位符 → 空
  assert.ok(html.indexOf('class="num yield">3.80</td>') < html.indexOf('<td>mootdx</td>'));
  assert.ok(html.includes('badge-green'));
  assert.ok(html.includes('<td class="num"></td>'), 'ROE% 占位符 → 空，不渲染 NaN');
  assert.ok(html.includes('6800.5'));
  assert.ok(html.includes('<td>mootdx</td>'), '新增数据来源列');
  // 关键：HTML 中不得出现原始 < 字符（XSS 断言）
  assert.ok(!html.includes('长<江'));
});
