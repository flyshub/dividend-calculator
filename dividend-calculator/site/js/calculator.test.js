#!/usr/bin/env node
/* calculator.js 纯函数单元测试 — 对齐 tests/test_pr_calculator.py + test_fiscal_year.py
 * 运行: node --test site/js/calculator.test.js  （Node 18+ 内置 test runner） */
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const path = require('path');
const Calc = require(path.join(__dirname, 'calculator.js'));

function round2(v) { return Math.round(v * 100) / 100; }

// ---- computeBasicPR ----
test('computeBasicPR 正常', () => assert.equal(Calc.computeBasicPR(10, 15.0), round2(10 / 15.0)));
test('computeBasicPR pe为null', () => assert.equal(Calc.computeBasicPR(null, 15.0), null));
test('computeBasicPR roe为null', () => assert.equal(Calc.computeBasicPR(10, null), null));
test('computeBasicPR roe为0', () => assert.equal(Calc.computeBasicPR(10, 0.0), null));
test('computeBasicPR roe为负', () => assert.equal(Calc.computeBasicPR(10, -5.0), null));

// ---- computeCorrectedPR ----
test('computeCorrectedPR 正常', () => assert.equal(Calc.computeCorrectedPR(10, 15.0, 1.5), round2(1.5 * 10 / 15.0)));
test('computeCorrectedPR nFactor为null', () => assert.equal(Calc.computeCorrectedPR(10, 15.0, null), null));
test('computeCorrectedPR pe为null', () => assert.equal(Calc.computeCorrectedPR(null, 15.0, 1.0), null));
test('computeCorrectedPR roe为0', () => assert.equal(Calc.computeCorrectedPR(10, 0.0, 1.0), null));

// ---- computePbPR ----
test('computePbPR 正常', () => assert.equal(Calc.computePbPR(2.0, 15.0), round2(2 / (0.15 ** 2) / 100)));
test('computePbPR pb为null', () => assert.equal(Calc.computePbPR(null, 15.0), null));
test('computePbPR roe为null', () => assert.equal(Calc.computePbPR(2.0, null), null));
test('computePbPR roe为0', () => assert.equal(Calc.computePbPR(2.0, 0.0), null));

// ---- computeNFactor ----
test('computeNFactor null', () => assert.equal(Calc.computeNFactor(null), null));
test('computeNFactor 0 → 2.0', () => assert.equal(Calc.computeNFactor(0.0), 2.0));
test('computeNFactor 负 → 2.0', () => assert.equal(Calc.computeNFactor(-0.1), 2.0));
test('computeNFactor 高支付率→1.0', () => assert.equal(Calc.computeNFactor(0.60), 1.0));
test('computeNFactor 低支付率→2.0', () => assert.equal(Calc.computeNFactor(0.20), 2.0));
test('computeNFactor 中支付率 0.40→1.25', () => assert.equal(Calc.computeNFactor(0.40), 1.25));
test('computeNFactor 边界0.50→1.0', () => assert.equal(Calc.computeNFactor(0.50), 1.0));
test('computeNFactor 边界0.25→2.0', () => assert.equal(Calc.computeNFactor(0.25), 2.0));

// ---- classifyValuation ----
test('classifyValuation 低估', () => assert.equal(Calc.classifyValuation(0.3), '低估'));
test('classifyValuation 合理偏低', () => assert.equal(Calc.classifyValuation(0.6), '合理偏低'));
test('classifyValuation 合理', () => assert.equal(Calc.classifyValuation(0.85), '合理'));
test('classifyValuation 高估', () => assert.equal(Calc.classifyValuation(1.5), '高估'));
test('classifyValuation null', () => assert.equal(Calc.classifyValuation(null), '无法判定'));
test('classifyValuation 边界0.5', () => assert.equal(Calc.classifyValuation(0.5), '低估'));
test('classifyValuation 边界0.7', () => assert.equal(Calc.classifyValuation(0.7), '合理偏低'));
test('classifyValuation 边界1.0', () => assert.equal(Calc.classifyValuation(1.0), '合理'));

// ---- classifyIndustry ----
test('classifyIndustry 周期行业', () => {
  const r = Calc.classifyIndustry('煤炭开采');
  assert.equal(r.isCyclical, true);
  assert.equal(r.isTech, false);
  assert.ok(r.warning.includes('周期行业'));
});
test('classifyIndustry 科技行业', () => {
  const r = Calc.classifyIndustry('半导体设备');
  assert.equal(r.isCyclical, false);
  assert.equal(r.isTech, true);
  assert.ok(r.warning.includes('科技行业'));
});
test('classifyIndustry 普通行业', () => {
  const r = Calc.classifyIndustry('食品饮料');
  assert.equal(r.isCyclical, false);
  assert.equal(r.isTech, false);
  assert.equal(r.warning, '');
});
test('classifyIndustry 空字符串', () => {
  const r = Calc.classifyIndustry('');
  assert.equal(r.isCyclical, false);
  assert.equal(r.isTech, false);
});

// ---- inferFiscalYear（对齐 tests/test_fiscal_year.py）----
test('inferFiscalYear 3-8月除权→上年度年报', () => {
  assert.deepEqual(Calc.inferFiscalYear(2024, 3), { year: 2023, isAnnual: true });
  assert.deepEqual(Calc.inferFiscalYear(2024, 8), { year: 2023, isAnnual: true });
});
test('inferFiscalYear 9-12月除权→当年度中报', () => {
  assert.deepEqual(Calc.inferFiscalYear(2024, 9), { year: 2024, isAnnual: false });
  assert.deepEqual(Calc.inferFiscalYear(2024, 12), { year: 2024, isAnnual: false });
});
test('inferFiscalYear 1-2月除权→上年度中报', () => {
  assert.deepEqual(Calc.inferFiscalYear(2024, 1), { year: 2023, isAnnual: false });
  assert.deepEqual(Calc.inferFiscalYear(2024, 2), { year: 2023, isAnnual: false });
});

// ---- calculateDividendYield ----
test('calculateDividendYield 三档税率', () => {
  const [a, b, c] = Calc.calculateDividendYield(100, 1000);
  assert.equal(a, 10);
  assert.equal(b, 9);
  assert.equal(c, 8);
});
test('calculateDividendYield 零市值', () => {
  assert.deepEqual(Calc.calculateDividendYield(100, 0), [0, 0, 0]);
});

// ---- parseDividendRecords ----
test('parseDividendRecords 半年报+年报合并同财年', () => {
  const rows = [
    { REPORT_DATE: '2025-12-31 00:00:00', PRETAX_BONUS_RMB: 7.9, ASSIGN_PROGRESS: '实施分配' },
    { REPORT_DATE: '2025-06-30 00:00:00', PRETAX_BONUS_RMB: 2.1, ASSIGN_PROGRESS: '实施分配' },
    { REPORT_DATE: '2024-12-31 00:00:00', PRETAX_BONUS_RMB: 8.2, ASSIGN_PROGRESS: '实施分配' },
  ];
  const r = Calc.parseDividendRecords(rows, 1000);
  assert.equal(r.year, '2025');
  assert.equal(r.totalDividend, (7.9 + 2.1) / 10 * 1000);
  assert.equal(r.details.length, 2);
});
test('parseDividendRecords 排除预披露', () => {
  const rows = [
    { REPORT_DATE: '2025-12-31 00:00:00', PRETAX_BONUS_RMB: 5, ASSIGN_PROGRESS: '预披露' },
    { REPORT_DATE: '2024-12-31 00:00:00', PRETAX_BONUS_RMB: 3, ASSIGN_PROGRESS: '实施分配' },
  ];
  const r = Calc.parseDividendRecords(rows, 1000);
  assert.equal(r.year, '2024');
  assert.equal(r.totalDividend, 3 / 10 * 1000);
});
test('parseDividendRecords 非标月份(5/7/8/10/11)按年报', () => {
  // 对齐 _parse_fhps_detail: 6/9月为半年报，其余月份均为年报
  const rows = [
    { REPORT_DATE: '2025-07-31 00:00:00', PRETAX_BONUS_RMB: 4, ASSIGN_PROGRESS: '实施分配' },
    { REPORT_DATE: '2025-06-30 00:00:00', PRETAX_BONUS_RMB: 2, ASSIGN_PROGRESS: '实施分配' },
    { REPORT_DATE: '2025-09-30 00:00:00', PRETAX_BONUS_RMB: 1, ASSIGN_PROGRESS: '实施分配' },
  ];
  const r = Calc.parseDividendRecords(rows, 1000);
  assert.equal(r.year, '2025');
  // 07月(年报) + 06月(半年报) + 09月(半年报) → 财年 2025 有年报
  assert.equal(r.details.filter(d => d.report_time === '2025年报').length, 1);
  assert.equal(r.totalDividend, (4 + 2 + 1) / 10 * 1000);
});
test('parseDividendRecords 无分红', () => {
  const r = Calc.parseDividendRecords([], 1000);
  assert.equal(r.totalDividend, 0);
  assert.equal(r.year, null);
});

// ---- parseFinancials（TTM = 最新累计 + 上年全年 - 上年同期）----
test('parseFinancials 空字符串不污染中位数', () => {
  const rows = [
    { REPORT_DATE: '2025-12-31 00:00:00', ROEJQ: '15.9', PARENTNETPROFIT: '345.03' },
    { REPORT_DATE: '2024-12-31 00:00:00', ROEJQ: '', PARENTNETPROFIT: '324.96' },
    { REPORT_DATE: '2023-12-31 00:00:00', ROEJQ: '13.0', PARENTNETPROFIT: '272.39' },
  ];
  const r = Calc.parseFinancials(rows);
  assert.equal(r.roeLatest, 15.9);
  assert.equal(r.roe5yMedian, 15.9);  // 有效年报 [13.0, 15.9]，len//2=1 → 15.9（空字符串被排除）
  assert.equal(r.netProfitAnnual, 345.03);
});

test('computePr 亏损股 pr_warning 由 app 层拼接（verify 覆盖）', () => {
  const r = Calc.computePr({ pe_ttm: 50, pb: 3, roe_latest: 5, net_profit_annual: -100, dividend_total: null });
  assert.equal(r.is_loss_stock, true);
  assert.equal(r.pr_basic, null);
  assert.equal(r.valuation_zone, '无法判定');
});

test('parseFinancials ROE中位数与TTM', () => {
  const rows = [
    { REPORT_DATE: '2026-03-31 00:00:00', ROEJQ: '9.0', PARENTNETPROFIT: '67.61' },
    { REPORT_DATE: '2025-12-31 00:00:00', ROEJQ: '15.9', PARENTNETPROFIT: '345.03' },
    { REPORT_DATE: '2025-03-31 00:00:00', ROEJQ: '4.0', PARENTNETPROFIT: '51.81' },
    { REPORT_DATE: '2024-12-31 00:00:00', ROEJQ: '13.5', PARENTNETPROFIT: '324.96' },
    { REPORT_DATE: '2023-12-31 00:00:00', ROEJQ: '13.0', PARENTNETPROFIT: '272.39' },
    { REPORT_DATE: '2022-12-31 00:00:00', ROEJQ: '12.0', PARENTNETPROFIT: '213.59' },
  ];
  const r = Calc.parseFinancials(rows);
  assert.equal(r.roeLatest, 15.9);
  assert.equal(r.roe5yMedian, 13.5);  // 4个年报 [12,13,13.5,15.9] 取中间位 → 13.5
  assert.ok(Math.abs(r.netProfitTtm - (67.61 + 345.03 - 51.81)) < 1e-6);
});

// ---- assessSustainability（对齐 tests/test_sustainability_calculator.py）----
function healthyFin() {
  return {
    year: 2025, net_profit: 345e8, net_profit_yoy: 7.0,
    operating_cf: 605e8, investing_cf: -312e8,
    total_assets: 5620e8, total_liabilities: 2918e8,
    debt_ratio: 52.0, interest_debt_ratio: 51.5, interest_coverage: 6.37, roe: 16.0,
    capital_adequacy_ratio: null, net_interest_margin: null, npl_ratio: null, provision_coverage: null,
  };
}
function healthyHistory() {
  return { consecutive_years: 15, ever_cut: false, latest_year_amount: 214e8, history_mean_amount: 200e8 };
}

test('assessSustainability 健康股→可持续', () => {
  const r = Calc.assessSustainability({
    dividend_yield_before_tax: 4.5, dividend_total: 214e8,
    latest: healthyFin(), history: healthyHistory(), industry: '公用事业-电力-水电',
  });
  assert.equal(r.triggered, true);
  assert.equal(r.verdict, '可持续');
  assert.ok(r.score >= 1.5);
  assert.deepEqual(r.fatal_flags, []);
});

test('assessSustainability 未达阈值不触发', () => {
  const r = Calc.assessSustainability({
    dividend_yield_before_tax: 3.5, dividend_total: 214e8,
    latest: healthyFin(), history: healthyHistory(), industry: '公用事业',
  });
  assert.equal(r.triggered, false);
  assert.equal(r.verdict, '未评估');
});

test('assessSustainability 亏损却分红→不可持续', () => {
  const fin = healthyFin(); fin.net_profit = -10e8; fin.net_profit_yoy = -120.0;
  const r = Calc.assessSustainability({
    dividend_yield_before_tax: 5.0, dividend_total: 214e8,
    latest: fin, history: healthyHistory(), industry: '煤炭',
  });
  assert.equal(r.verdict, '不可持续');
  assert.ok(r.fatal_flags.some(f => f.includes('净利润为负')));
  assert.equal(r.score, 0.0);
});

test('assessSustainability 支付率>100%→情境红旗(非致命) (T2)', () => {
  const fin = healthyFin(); fin.net_profit = 100e8;
  const r = Calc.assessSustainability({
    dividend_yield_before_tax: 5.0, dividend_total: 214e8,
    latest: fin, history: healthyHistory(), industry: '煤炭',
  });
  // T2：不再是致命红旗
  assert.ok(!r.fatal_flags.some(f => f.includes('股利支付率')));
  // 应作为情境红旗
  assert.ok(r.warning_flags.some(w => w.includes('股利支付率') && w.includes('> 100%')));
});

test('assessSustainability 周期股顶点→情境红旗降档', () => {
  const fin = healthyFin();
  fin.net_profit = 100e8; fin.net_profit_yoy = -25.0; fin.roe = 18.0; fin.interest_coverage = 8.0;
  const r = Calc.assessSustainability({
    dividend_yield_before_tax: 6.0, dividend_total: 90e8,
    latest: fin, history: healthyHistory(), industry: '煤炭',
  });
  assert.ok(r.warning_flags.some(w => w.includes('周期')));
});

test('assessSustainability 银行走金融分支', () => {
  const bankFin = {
    year: 2025, net_profit: 1500e8, net_profit_yoy: 1.5,
    operating_cf: 4514e8, investing_cf: null,
    total_assets: 12e12, total_liabilities: 11e12, debt_ratio: 87.8,
    interest_debt_ratio: null, interest_coverage: null, roe: 14.0,
    capital_adequacy_ratio: 16.5, net_interest_margin: 1.87, npl_ratio: 0.95, provision_coverage: 200.0,
  };
  const r = Calc.assessSustainability({
    dividend_yield_before_tax: 5.0, dividend_total: 350e8,
    latest: bankFin, history: { consecutive_years: 12, ever_cut: false, latest_year_amount: 350e8, history_mean_amount: 340e8 },
    industry: '银行',
  });
  assert.equal(r.branch, 'finance');
  assert.ok('capital_adequacy' in r.dimension_scores);
  assert.ok(r.score >= 1.5);
});

test('assessSustainability 财务缺失→不可持续', () => {
  const r = Calc.assessSustainability({
    dividend_yield_before_tax: 5.0, dividend_total: 214e8,
    latest: null, history: null, industry: '公用事业',
  });
  assert.equal(r.verdict, '不可持续');
  assert.ok(r.fatal_flags.some(f => f.includes('缺少财务数据')));
});
