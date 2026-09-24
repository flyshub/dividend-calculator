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

test('prNote 小盘（<100亿）加注释，大盘不加', () => {
  assert.match(R.prNote({ '总市值(亿)': 60.93 }), /小盘未验证/);
  assert.match(R.prNote({ '总市值(亿)': 99.99 }), /小盘未验证/);
  assert.match(R.prNote({ '总市值(亿)': 100 }), /^$/);       // 边界：100 不算小盘
  assert.match(R.prNote({ '总市值(亿)': 6800.5 }), /^$/);
  assert.match(R.prNote({ '总市值(亿)': '—' }), /^$/);       // 缺数占位符
  assert.match(R.prNote({}), /^$/);                          // 字段缺失
});

test('susNote 偏弱加注释，其余不加', () => {
  assert.match(R.susNote({ '可持续性': '偏弱' }), /核实分红性质/);
  assert.match(R.susNote({ '可持续性': '可持续' }), /^$/);
  assert.match(R.susNote({ '可持续性': '不可持续' }), /^$/);
});

test('tsrNote 高回报（TSR>15%）加徽标，其余不加', () => {
  assert.match(R.tsrNote({ '股东总回报率%': 15.01 }), /高回报/);
  assert.match(R.tsrNote({ '股东总回报率%': 20 }), /高回报/);
  assert.strictEqual(R.tsrNote({ '股东总回报率%': 15 }), '', '边界：15 不算（严格大于）');
  assert.strictEqual(R.tsrNote({ '股东总回报率%': 14.9 }), '', '低于阈值不加');
  assert.strictEqual(R.tsrNote({ '股东总回报率%': '—' }), '', '占位符不加');
  assert.strictEqual(R.tsrNote({}), '', '历史行缺 key 不加');
});

test('noteBadge 文案与 title 说明均转义', () => {
  const b = R.noteBadge('小盘未验证', '市赚率阈值回测样本为沪深300，请人工核实');
  assert.ok(b.includes('class="badge badge-note"'));
  assert.ok(b.includes('title="市赚率阈值回测样本为沪深300，请人工核实"'));
  // tip 含引号/尖括号时不得破坏 title 属性（XSS 防护）
  assert.ok(R.noteBadge('x', 'a"b<c').includes('a&quot;b&lt;c'));
});

test('rowHtml 完整 12 列 + 转义 + 数字格式化', () => {
  const r = {
    '代码': '600900', '名称': '长<江>电力', 'TTM股息率%': 3.80, '真实股息率%': 3.80,
    '估值区间': '低估', '市赚率PR': 0.52, '行业': '公用事业-电力',
    '可持续性': '可持续', 'ROE%': '—', '股东总回报率%': 13.32,
    '总市值(亿)': 6800.5, '数据来源': 'mootdx',
  };
  const html = R.rowHtml(r);
  assert.ok(html.includes('<td>600900</td>'));
  assert.ok(html.includes('长&lt;江&gt;电力'));     // 名称被转义
  // 列序：真实股息率 → ROE → TSR → 总市值（Q7-A：TSR 插 ROE% 后）
  const roeIdx = html.indexOf('class="num"></td>');       // ROE% 占位符 → 空
  const tsrIdx = html.indexOf('class="num">13.32</td>');
  const capIdx = html.indexOf('6800.5');
  assert.ok(roeIdx !== -1 && tsrIdx !== -1 && roeIdx < tsrIdx && tsrIdx < capIdx);
  assert.ok(html.includes('badge-green'));
  assert.ok(!html.includes('NaN'), '占位符与缺失值不得渲染 NaN');
  assert.ok(html.includes('<td>mootdx</td>'), '新增数据来源列');
  // 关键：HTML 中不得出现原始 < 字符（XSS 断言）
  assert.ok(!html.includes('长<江'));
});

test('rowHtml 历史 11 列行（缺 TSR）→ 空 td 不崩（ADR-0003）', () => {
  const r = {
    '代码': '600900', '名称': '长江电力', 'TTM股息率%': 3.80, '真实股息率%': 3.80,
    '估值区间': '低估', '市赚率PR': 0.52, '行业': '公用事业-电力',
    '可持续性': '可持续', 'ROE%': 16.0, '总市值(亿)': 6800.5, '数据来源': 'mootdx',
  };
  const html = R.rowHtml(r);   // r 无「股东总回报率%」key（历史按日 JSON）
  assert.ok(html.includes('class="num">16.00</td>'));   // ROE% 正常渲染
  assert.ok(!html.includes('undefined'), '缺列渲染为空而非 undefined');
  assert.ok(!html.includes('NaN'));
});

test('rowHtml 小盘+偏弱行带提示徽标', () => {
  const r = {
    '代码': '000915', '名称': '华特达因', 'TTM股息率%': 6.5, '真实股息率%': 6.5,
    '估值区间': '低估', '市赚率PR': 0.8, '行业': '医药',
    '可持续性': '偏弱', 'ROE%': 15, '总市值(亿)': 60.93, '数据来源': 'mootdx',
  };
  const html = R.rowHtml(r);
  assert.ok(html.includes('小盘未验证'));
  assert.ok(html.includes('核实分红性质'));
  assert.ok(html.includes('badge-note'));
  assert.ok(html.includes('title="市赚率阈值回测样本为沪深300'));
  // 大盘 + 可持续行不带任何提示徽标
  const big = R.rowHtml({ ...r, '总市值(亿)': 6800.5, '可持续性': '可持续' });
  assert.ok(!big.includes('badge-note'));
});
