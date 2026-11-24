const { test } = require('node:test');
const assert = require('node:assert');
const path = require('path');
const P = require(path.join(__dirname, 'portfolio.js'));

test('parseHoldings 支持冒号/空格/换行与中文逗号', () => {
  assert.deepStrictEqual(P.parseHoldings('600900:1000，601398 5000\n600887:200'),
    [{ code: '600900', shares: 1000 }, { code: '601398', shares: 5000 }, { code: '600887', shares: 200 }]);
});

test('parseHoldings 非法输入抛错（不静默丢弃）', () => {
  ['600900', '600900:0', ':1000', '600900:abc', '', ' , '].forEach(function (bad) {
    assert.throws(function () { P.parseHoldings(bad); }, /股数|格式|为空/);
  });
});

function analysis(over) {
  const a = {
    stock_info: { stock_name: '测试股', current_price: 10, total_shares: 1e9 },
    dividend: { total_dividend: 5e8 },
    pr: { industry: '制造业' },
    sustainability: { verdict: '偏弱' },
  };
  return Object.assign(a, over || {});
}

test('buildPosition 折算每股分红/年收入/股息率', () => {
  const p = P.buildPosition('600000', 2000, analysis());
  assert.equal(p.dividend_per_share, 0.5);
  assert.equal(p.annual_income, 1000);
  assert.equal(p.market_value, 20000);
  assert.equal(p.dividend_yield, 5);
  assert.equal(p.error, null);
});

test('buildPosition 无分红：标记错误但市值仍保留', () => {
  const p = P.buildPosition('600001', 100, analysis({ dividend: { total_dividend: 0 } }));
  assert.match(p.error, /无现金分红/);
  assert.equal(p.annual_income, null);
  assert.equal(p.market_value, 1000);
});

test('buildPosition 分析失败', () => {
  const p = P.buildPosition('600002', 100, null);
  assert.match(p.error, /分析失败/);
});

test('summarize 加权股息率与税后分支', () => {
  const s = P.summarize([
    P.buildPosition('A', 2000, analysis()),                                   // 5%
    P.buildPosition('B', 3000, analysis({ stock_info: { stock_name: 'B', current_price: 10, total_shares: 1e9 }, dividend: { total_dividend: 1e8 } })), // 0.1 元/股
  ]);
  assert.equal(s.total_market_value, 50000);
  assert.equal(s.total_annual_income, 1300);
  assert.equal(s.weighted_yield, 2.6);
  assert.equal(s.after_tax_income_10, 1170);
  assert.equal(s.after_tax_income_20, 1040);
});

test('summarize 失败持仓不进分母（与 Python 同契约）', () => {
  const rows = [P.buildPosition('A', 2000, analysis()), P.buildPosition('FAIL', 1, null)];
  const s = P.summarize(rows);
  assert.equal(s.weighted_yield, 5);
  assert.deepStrictEqual(s.errors, ['FAIL: 分析失败（取数失败或代码不存在）']);
});

test('summarize 行业占比排序与未知行业/未评估兜底', () => {
  const s = P.summarize([
    P.buildPosition('A', 2000, analysis()),
    P.buildPosition('B', 1000, analysis({ pr: { industry: null }, sustainability: null,
      dividend: { total_dividend: 2e8 } })),
  ]);
  assert.deepStrictEqual(s.industry_breakdown.map(r => r[0]), ['制造业', '未知行业']);
  assert.equal(s.verdict_summary['偏弱'], 1);
  assert.equal(s.verdict_summary['未评估'], 1);
});

test('summarize 无分红持仓进市值分母（稀释加权股息率）', () => {
  const s = P.summarize([
    { code: 'A', market_value: 20000, annual_income: 1000, industry: '银行', verdict: '可持续', error: null },
    { code: 'Z', market_value: 20000, annual_income: null, industry: '电力', verdict: '未评估',
      error: '最新完整财年无现金分红' },
  ]);
  assert.equal(s.total_market_value, 40000);
  assert.equal(s.weighted_yield, 2.5);
  assert.deepStrictEqual(s.industry_breakdown.map(r => r[0]), ['银行']);
  assert.deepStrictEqual(s.errors, ['Z: 最新完整财年无现金分红']);
});

test('parseHoldings 支持空格分隔（与 Python 一致）', () => {
  assert.deepStrictEqual(P.parseHoldings('600900 1000'), [{ code: '600900', shares: 1000 }]);
});
