/* 档位阈值与行业关键词的页面侧契约（issues #149 / #151） */
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const Calc = require('./calculator.js');
const policySrc = fs.readFileSync(path.join(__dirname, 'policy.js'), 'utf8');
const PRPolicy = (() => {
  const sandbox = { self: {} };
  new Function('self', `${policySrc}\n;return self.PRPolicy;`)(sandbox.self);
  return sandbox.self.PRPolicy;
})();

test('切点与页面历史文案一致', () => {
  assert.deepStrictEqual(PRPolicy.cutoffs, { undervalued: 0.5, fair_low: 1.0, fair_max: 3.0 });
});

test('zoneLegend 文本格式不变', () => {
  assert.strictEqual(PRPolicy.zoneLegend(),
    '市赚率≤0.5低估，0.5-1.0合理偏低，1.0-3.0合理，>3.0高估');
});

test('fillSpans 按 data-format 填充阈值', () => {
  const nodes = [
    { getAttribute: (k) => ({ 'data-policy': 'fair_max', 'data-format': 'int' })[k], textContent: '' },
    { getAttribute: (k) => ({ 'data-policy': 'fair_low', 'data-format': 'int' })[k], textContent: '' },
    { getAttribute: (k) => ({ 'data-policy': 'fair_low', 'data-format': 'one-decimal' })[k], textContent: '' },
    { getAttribute: (k) => ({ 'data-policy': 'unknown' })[k], textContent: '保持原样' },
  ];
  PRPolicy.fillSpans({ querySelectorAll: () => nodes });
  assert.deepStrictEqual(nodes.map((n) => n.textContent), ['3', '1', '1.0', '保持原样']);
});

test('防御性关键词用专名，避免「电力设备」被误判', () => {
  assert.ok(Calc.DEFENSIVE_INDUSTRIES.includes('电力行业'));
  assert.ok(!Calc.DEFENSIVE_INDUSTRIES.includes('电力'));
  const isDef = (tag) => Calc.DEFENSIVE_INDUSTRIES.some((k) => tag.includes(k));
  assert.strictEqual(isDef('公用事业-电力-水电'), true);
  assert.strictEqual(isDef('电力设备-电源设备-太阳能'), false);
});
