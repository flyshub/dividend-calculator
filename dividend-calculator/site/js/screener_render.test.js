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

/* #30 分红趋势列（2026-11 由「股息」改名；旧表头仍兼容） */
test('divTrendCell 增长/萎缩/平稳与缺失', () => {
  assert.ok(R.divTrendCell({ '分红趋势': '增长', '分红CAGR%': 8.4 }).includes('+8.4%'));
  assert.ok(R.divTrendCell({ '分红趋势': '增长', '分红CAGR%': 8.4 }).includes('#3fb950'));
  assert.ok(R.divTrendCell({ '分红趋势': '萎缩', '分红CAGR%': -6.2 }).includes('#e5534b'));
  assert.ok(R.divTrendCell({ '分红趋势': '萎缩', '分红CAGR%': -6.2 }).includes('-6.2%'));
  assert.ok(R.divTrendCell({ '分红趋势': '平稳', '分红CAGR%': 0 }).includes('平稳'));
  assert.ok(R.divTrendCell({}).includes('—'), '历史行缺列显示占位符');
  assert.ok(!R.divTrendCell({ '分红趋势': '增长' }).includes('NaN'), 'CAGR 缺失不渲染 NaN');
});

test('rowHtml 股息趋势列在可持续性之后', () => {
  const r = { '代码': '600900', '名称': '长江电力', '真实股息率%': 3.8,
              '可持续性': '可持续', '分红趋势': '增长', '分红CAGR%': 8.4 };
  const html = R.rowHtml(r);
  assert.ok(html.indexOf('badge-green') < html.indexOf('+8.4%'), '趋势列紧随可持续性');
});

test('divTrendCell 兼容改名前的历史 JSON（股息趋势/股息CAGR%）', () => {
  const html = R.divTrendCell({ '股息趋势': '增长', '股息CAGR%': 5.5 });
  assert.ok(html.includes('增长') && html.includes('+5.5%'));
});

/* #157 前瞻股息率列：数字 + 目标财年/口径提示；不可估与历史行显示 — */
test('forwardCell 可估行：两位小数 + 财年与口径提示', () => {
  const html = R.forwardCell({ '前瞻股息率%': 5.4321, '前瞻财年': 2026, '前瞻状态': 'ok' });
  assert.ok(html.includes('>5.43<'));
  assert.ok(html.includes('title="前瞻财年 2026'), '悬停含目标财年');
  assert.ok(html.includes('模型估算'), '悬停标注模型估算');
  assert.ok(html.includes('税前'));
  assert.ok(html.includes('非公司公告'));
  assert.ok(!html.includes('市场：') && !html.includes('数据缺口：'),
    'ok 行有数值，悬停只给财年与口径，不写原因');
});

test('forwardCell 不可估/历史行显示 —，不渲染 NaN/undefined', () => {
  const blank = R.forwardCell({ '前瞻股息率%': '', '前瞻财年': 2026, '前瞻状态': 'no_coverage' });
  assert.ok(blank.includes('—'));
  assert.ok(!blank.includes('NaN') && !blank.includes('undefined'));
  assert.ok(blank.includes('<td class="num"'), '占位符保持数值列样式');
  assert.ok(blank.includes('title="前瞻财年 2026；市场：无机构盈利预测；'), '不可估行给出财年、原因与口径');
  const legacy = R.forwardCell({});   // 历史按日 JSON 无前瞻 key
  assert.ok(legacy.includes('—'));
  assert.ok(!legacy.includes('title='), '无值时不给误导性提示');
});

test('forwardCell 财年与提示经转义（XSS）', () => {
  const html = R.forwardCell({ '前瞻股息率%': 5.0, '前瞻财年': '" onmouseover="alert(1)' });
  assert.ok(!html.includes('onmouseover="alert'));
  assert.ok(html.includes('&quot;'));
});

/* #158 不可估原因：九状态全映射，市场事实与数据缺口可区分，不泄漏英文枚举 */
const FORWARD_REASONS = [
  ['no_coverage', '市场：无机构盈利预测'],
  ['no_positive_eps', '市场：机构预测亏损'],
  ['no_target_year', '市场：无目标财年预测'],
  ['no_complete_year', '市场：近年无完整财年分红'],
  ['stale', '市场：近年无分红'],
  ['fetch_error', '数据缺口：前瞻取数失败'],
  ['missing_inputs', '数据缺口：缺少现价'],
  ['no_payout_history', '数据缺口：无可用分红历史'],
];

for (const [status, text] of FORWARD_REASONS) {
  test('forwardCell 状态 ' + status + ' 的悬停原因', () => {
    const html = R.forwardCell({ '前瞻股息率%': '', '前瞻财年': 2026, '前瞻状态': status });
    assert.ok(html.includes('—'), '数值列显示占位符');
    assert.ok(html.includes(text), '含中文原因：' + text);
    assert.ok(!html.includes(status), '不泄漏英文枚举');
    assert.ok(html.includes('前瞻财年 2026'), '仍给出目标财年');
    if (text.startsWith('市场：')) {
      assert.ok(!html.includes('数据缺口'), '市场事实不写成数据缺口');
    } else {
      assert.ok(!html.includes('市场：'), '数据缺口不写成市场事实');
    }
  });
}

test('forwardCell 无财年的不可估状态仍给原因（契约组合）', () => {
  // §6.1：stale / no_complete_year / missing_inputs 的「前瞻财年」为空，
  // 但原因必须仍然可见，否则用户只看到「—」（#158 AC 的原始意图）
  for (const [status, text] of [
    ['stale', '市场：近年无分红'],
    ['no_complete_year', '市场：近年无完整财年分红'],
    ['missing_inputs', '数据缺口：缺少现价'],
  ]) {
    for (const year of ['', null, undefined]) {
      const html = R.forwardCell({ '前瞻股息率%': '', '前瞻财年': year, '前瞻状态': status });
      assert.ok(html.includes('—'));
      assert.ok(html.includes(text), '无财年也要给原因：' + status);
      assert.ok(!html.includes('前瞻财年'), '无财年时不写年份段');
      assert.ok(html.includes('非公司公告'), '口径说明始终存在');
    }
  }
});

test('forwardCell 未知状态不渲染未映射枚举', () => {
  const html = R.forwardCell({ '前瞻股息率%': '', '前瞻状态': 'some_future_status' });
  assert.ok(html.includes('—'));
  assert.ok(!html.includes('some_future_status'));
  assert.ok(!html.includes('title='), '无法解释时宁可不给提示');
});

test('rowHtml 前瞻列紧跟真实股息率（第 4 列）', () => {
  const r = {
    '代码': '600900', '名称': '长江电力', '真实股息率%': 3.8,
    '前瞻股息率%': 4.25, '前瞻财年': 2026, '前瞻状态': 'ok',
    '估值区间': '低估', '可持续性': '可持续',
  };
  const html = R.rowHtml(r);
  const realIdx = html.indexOf('yield">3.80');
  const fwdIdx = html.indexOf('>4.25<');
  const susIdx = html.indexOf('badge-green');
  assert.ok(realIdx !== -1 && fwdIdx !== -1 && susIdx !== -1);
  assert.ok(realIdx < fwdIdx && fwdIdx < susIdx, '列序：真实股息率 → 前瞻 → 可持续性');
});
