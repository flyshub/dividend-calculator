#!/usr/bin/env node
/* calculator.js 纯函数单元测试 — 对齐 tests/test_pr_calculator.py + test_fiscal_year.py
 * 运行: node --test site/js/calculator.test.js  （Node 18+ 内置 test runner） */
'use strict';
const { test } = require('node:test');
const assert = require('node:assert');
const path = require('path');
const Calc = require(path.join(__dirname, 'calculator.js'));
const DS = require(path.join(__dirname, 'datasources.js'));

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
test('classifyValuation 合理', () => assert.equal(Calc.classifyValuation(1.5), '合理'));
test('classifyValuation 高估', () => assert.equal(Calc.classifyValuation(4.0), '高估'));
test('classifyValuation null', () => assert.equal(Calc.classifyValuation(null), '无法判定'));
test('classifyValuation 边界0.5', () => assert.equal(Calc.classifyValuation(0.5), '低估'));
test('classifyValuation 边界1.0', () => assert.equal(Calc.classifyValuation(1.0), '合理偏低'));
test('classifyValuation 边界3.0', () => assert.equal(Calc.classifyValuation(3.0), '合理'));

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
test('classifyIndustry 成长行业', () => {
  const r = Calc.classifyIndustry('光伏设备');
  assert.equal(r.isCyclical, false);
  assert.equal(r.isTech, false);
  assert.equal(r.isGrowth, true);
  assert.ok(r.warning.includes('成长行业'));
});
test('classifyIndustry 成长AI算力', () => {
  const r = Calc.classifyIndustry('数据中心');
  assert.equal(r.isGrowth, true);
});
test('classifyIndustry 优先级周期>成长', () => {
  const r = Calc.classifyIndustry('化工新材料');
  assert.equal(r.isCyclical, true);
  assert.equal(r.isGrowth, true);
  assert.ok(r.warning.includes('周期行业'));
  assert.ok(!r.warning.includes('成长行业'));
});
test('classifyIndustry 优先级科技>成长', () => {
  const r = Calc.classifyIndustry('半导体新材料');
  assert.equal(r.isTech, true);
  assert.equal(r.isGrowth, true);
  assert.ok(r.warning.includes('科技行业'));
  assert.ok(!r.warning.includes('成长行业'));
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
test('parseDividendRecords 仅12月为年报，3/6/9月为中期分配 (#37)', () => {
  // 对齐 Python: 仅 REPORT_DATE 12月为年报（完整财年），3/6/9月等为中期分配
  const rows = [
    { REPORT_DATE: '2025-12-31 00:00:00', PRETAX_BONUS_RMB: 4, ASSIGN_PROGRESS: '实施分配' },
    { REPORT_DATE: '2025-06-30 00:00:00', PRETAX_BONUS_RMB: 2, ASSIGN_PROGRESS: '实施分配' },
    { REPORT_DATE: '2025-09-30 00:00:00', PRETAX_BONUS_RMB: 1, ASSIGN_PROGRESS: '实施分配' },
    { REPORT_DATE: '2025-03-31 00:00:00', PRETAX_BONUS_RMB: 3, ASSIGN_PROGRESS: '实施分配' },
  ];
  const r = Calc.parseDividendRecords(rows, 1000);
  assert.equal(r.year, '2025');
  // 仅 12月(年报)，6/9/3月均为中期分配 → 财年 2025 有年报
  assert.equal(r.details.filter(d => d.report_time === '2025年报').length, 1);
  assert.equal(r.details.filter(d => d.report_time === '2025中期分配').length, 3);
  assert.equal(r.totalDividend, (4 + 2 + 1 + 3) / 10 * 1000);
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
  assert.equal(r.roePeriod, 2025);
  assert.equal(r.netProfitAnnual, 345.03);
});

test('computePr 亏损股 pr_warning 由 app 层拼接（verify 覆盖）', () => {
  const r = Calc.computePr({ pe_ttm: 50, pb: 3, roe_latest: 5, net_profit_annual: -100, dividend_total: null });
  assert.equal(r.is_loss_stock, true);
  assert.equal(r.pr_basic, null);
  assert.equal(r.valuation_zone, '无法判定');
});

test('computePr roe_period 口径标注', () => {
  const r = Calc.computePr({ pe_ttm: 10, pb: 2, roe_latest: 20, roe_period: 2025, net_profit_annual: 100, dividend_total: 50 });
  assert.equal(r.roe_period, '2025年报');
});

test('computePr 周期股 PB-市赚率用 5 年 ROE 中位数', () => {
  // 周期股: roe_latest=5, 5年中位数=10 → PB-PR 用 10
  const cyc = Calc.computePr({ pe_ttm: 10, pb: 4, roe_latest: 5, roe_5y_median: 10, is_cyclical: true, net_profit_annual: 100, dividend_total: 50 });
  const nonCyc = Calc.computePr({ pe_ttm: 10, pb: 4, roe_latest: 5, roe_5y_median: 10, is_cyclical: false, net_profit_annual: 100, dividend_total: 50 });
  assert.notEqual(cyc.pr_pb, nonCyc.pr_pb);
  // 周期股: PB=4, ROE=10% → 4/(0.10²)/100 = 4/0.01/100 = 4.00
  assert.equal(cyc.pr_pb, 4.00);
  // 非周期股: PB=4, ROE=5% → 4/(0.05²)/100 = 4/0.0025/100 = 16.00
  assert.equal(nonCyc.pr_pb, 16.00);
});

test('computePr 周期股但无 5 年中位数时回退最新 ROE', () => {
  const r = Calc.computePr({ pe_ttm: 10, pb: 4, roe_latest: 5, roe_5y_median: null, is_cyclical: true, net_profit_annual: 100, dividend_total: 50 });
  assert.equal(r.pr_pb, 16.00);  // 回退 roe_latest=5
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

test('assessSustainability score_100 基于已舍入 score 计算 (#36)', () => {
  // 银行分支 3 项专项 (2,2,1) → 原始 score=5/3≈1.6667
  // 旧实现用未舍入 score: Math.round(1.6667*500)/10 = 83.3；
  // 新实现基于 round(score,3)=1.667: Math.round(1.667*500)/10 = 83.4（对齐 Python _score_to_100）
  const fin = {
    year: 2025, net_profit: 1500e8, net_profit_yoy: 1.5,
    operating_cf: 4514e8, investing_cf: null,
    total_assets: 12e12, total_liabilities: 11e12, debt_ratio: 87.8,
    interest_debt_ratio: null, interest_coverage: null, roe: 14.0,
    capital_adequacy_ratio: 16.5, net_interest_margin: 1.87, npl_ratio: 1.5, provision_coverage: null,
  };
  const r = Calc.assessSustainability({
    dividend_yield_before_tax: 5.0, dividend_total: 350e8,
    latest: fin, history: { consecutive_years: 12, ever_cut: false, latest_year_amount: 350e8, history_mean_amount: 340e8 },
    industry: '银行',
  });
  assert.equal(r.score, 1.667);    // round(5/3, 3)
  assert.equal(r.score_100, 83.4); // 由已舍入 score 映射，非原始 score
});

test('assessSustainability 财务缺失→不可持续', () => {
  const r = Calc.assessSustainability({
    dividend_yield_before_tax: 5.0, dividend_total: 214e8,
    latest: null, history: null, industry: '公用事业',
  });
  assert.equal(r.verdict, '不可持续');
  assert.ok(r.fatal_flags.some(f => f.includes('缺少财务数据')));
});

/* ── 结论说明 explainSustainability（对齐 Python explain_sustainability，逐字一致） ── */

function susBase() {
  return {
    triggered: true, verdict: '偏弱', score: 1.2, branch: 'general',
    fatal_flags: [], warning_flags: [], notes: [],
    dimension_scores: { cf_coverage: 2, payout: 2, profitability: 0, balance_sheet: 2, dividend_history: 1, industry: 0 },
    metrics: { cf_coverage: 2.983062851644384, payout_ratio: 0.4946238774464123, roe_latest: 13.17, net_profit_yoy: -37.132715923631, debt_ratio: 0.41415746932614467, consecutive_dividend_years: 5 },
  };
}

test('explainSustainability 偏弱→弱维度+优势项', () => {
  const lines = Calc.explainSustainability(susBase());
  assert.deepStrictEqual(lines, [
    '结论：偏弱 — 分红有一定基础，但存在隐忧，长期分红能力可能打折扣',
    '1. 盈利稳定性：ROE 13.17%、净利润同比 -37.1%，盈利在下滑，分红难持续',
    '2. 属强周期行业，盈利随景气波动大，高分红难年年保证',
    '3. 连续分红 5 年，尚不算长期稳定',
    '4. 优势项：现金流覆盖 2.98 倍（充裕）、支付率 49.5%（健康）',
  ]);
});

test('explainSustainability 可持续→优势项+缺失注记', () => {
  const sus = susBase();
  sus.verdict = '可持续'; sus.score = 1.8;
  sus.dimension_scores = { cf_coverage: 2, payout: 2, profitability: 2, balance_sheet: 2, dividend_history: 2, industry: 2 };
  sus.metrics = { cf_coverage: 3.2, payout_ratio: 0.45, roe_latest: 15.2, net_profit_yoy: 8.7, debt_ratio: 0.35, consecutive_dividend_years: 8 };
  sus.notes = ['财务数据部分缺失，结论仅供参考'];
  const lines = Calc.explainSustainability(sus);
  assert.deepStrictEqual(lines, [
    '结论：可持续 — 盈利与现金流足以支撑当前分红',
    '1. 优势项：现金流覆盖 3.20 倍（充裕）、支付率 45.0%（健康）',
    '注：财务数据部分缺失，结论仅供参考',
  ]);
});

test('explainSustainability 不可持续→致命红旗优先', () => {
  const sus = susBase();
  sus.verdict = '不可持续'; sus.score = null;
  sus.fatal_flags = ['净利润为负（亏损）却仍派发现金分红', '自由现金流覆盖 0.60x < 1.0x，分红金额超过自由现金流'];
  const lines = Calc.explainSustainability(sus);
  assert.deepStrictEqual(lines, [
    '结论：不可持续 — 存在致命问题，当前分红水平大概率维持不下去',
    '1. 净利润为负（亏损）却仍派发现金分红',
    '2. 自由现金流覆盖 0.60x < 1.0x，分红金额超过自由现金流',
  ]);
});

test('explainSustainability 未触发→空数组', () => {
  assert.deepStrictEqual(Calc.explainSustainability(null), []);
  assert.deepStrictEqual(Calc.explainSustainability({ triggered: false, verdict: '未评估' }), []);
});

/* ── aggregateDividendHistory（对齐 Python aggregate_dividend_history，近10年窗口） ── */

function yearlyFromYearAmount(obj) {
  /* {year: dp10值} → _aggregateDividendHistory 期望的 yearly 结构（默认视为年报记录） */
  const yearly = {};
  Object.keys(obj).forEach(function (y) {
    yearly[y] = {
      total: obj[y] * 10.0, /* total 是每10股，内部 /10 * shares */
      details: [{ report_time: y + '年报', dividend_per_10: obj[y] * 10.0 }],
    };
  });
  return yearly;
}

test('aggregateDividendHistory 窗口外削减不计入 ever_cut', () => {
  const amt = {};
  for (let y = 2012; y <= 2025; y++) amt[y] = (y === 2014 ? 2.0 : 5.0); /* 2014 削减，窗口外 */
  const h = Calc.aggregateDividendHistory(yearlyFromYearAmount(amt), '2025', 1e9);
  assert.equal(h.consecutive_years, 14);
  assert.equal(h.ever_cut, false);
});

test('aggregateDividendHistory 窗口内削减计入 ever_cut', () => {
  const amt = {};
  for (let y = 2015; y <= 2025; y++) amt[y] = (y === 2023 ? 2.0 : 5.0); /* 2023 削减，窗口内 */
  const h = Calc.aggregateDividendHistory(yearlyFromYearAmount(amt), '2025', 1e9);
  assert.equal(h.ever_cut, true);
});

test('aggregateDividendHistory 削减落在窗口首年计入 ever_cut', () => {
  const amt = {};
  for (let y = 2014; y <= 2025; y++) amt[y] = (y === 2016 ? 2.0 : 5.0); /* 2016 削减，跨立窗口起点 2016 */
  const h = Calc.aggregateDividendHistory(yearlyFromYearAmount(amt), '2025', 1e9);
  assert.equal(h.ever_cut, true);
});

test('aggregateDividendHistory latest_year_amount 用 target_year 而非最大年份 (#35)', () => {
  // 2026 仅半年报、2025 有年报 → target_year=2025，latest_year_amount 取 2025 年报额而非 2026 半年报额
  const yearly = {
    2026: { total: 30, details: [{ report_time: '2026中期分配', dividend_per_10: 30 }] },
    2025: { total: 80, details: [{ report_time: '2025年报', dividend_per_10: 80 }] },
    2024: { total: 100, details: [{ report_time: '2024年报', dividend_per_10: 100 }] },
  };
  const h = Calc.aggregateDividendHistory(yearly, '2025', 1e9);
  assert.equal(h.latest_year_amount, (80 / 10) * 1e9);
});

test('aggregateDividendHistory ever_cut 仅年报参与，半年报混入不误报 (#39)', () => {
  // 2024年报10元 + 2025年报8元 + 2025半年报3元：年报降幅 8/10=0.8 ≥ 0.7 → 无削减
  const yearly = {
    2025: { total: 110, details: [
      { report_time: '2025年报', dividend_per_10: 80 },
      { report_time: '2025中期分配', dividend_per_10: 30 },
    ] },
    2024: { total: 100, details: [{ report_time: '2024年报', dividend_per_10: 100 }] },
  };
  const h = Calc.aggregateDividendHistory(yearly, '2025', 1e9);
  assert.equal(h.ever_cut, false);
});

test('aggregateDividendHistory ever_cut 年报降幅>30% 计入 (#39)', () => {
  // 2024年报10元 + 2025年报6元：6 < 10×0.7 → 削减
  const yearly = {
    2025: { total: 60, details: [{ report_time: '2025年报', dividend_per_10: 60 }] },
    2024: { total: 100, details: [{ report_time: '2024年报', dividend_per_10: 100 }] },
  };
  const h = Calc.aggregateDividendHistory(yearly, '2025', 1e9);
  assert.equal(h.ever_cut, true);
});

test('aggregateDividendHistory ever_cut 半年报不得掩盖真实削减 (#39)', () => {
  // 2024年报10元 + 2025年报6元 + 2025半年报5元：含半年报看 2025=11元 似无削减，
  // 仅年报口径 6 < 10×0.7 → 仍应判削减
  const yearly = {
    2025: { total: 110, details: [
      { report_time: '2025年报', dividend_per_10: 60 },
      { report_time: '2025半年报', dividend_per_10: 50 },
    ] },
    2024: { total: 100, details: [{ report_time: '2024年报', dividend_per_10: 100 }] },
  };
  const h = Calc.aggregateDividendHistory(yearly, '2025', 1e9);
  assert.equal(h.ever_cut, true);
});

/* ── datasources.js 数据源函数（#40，mock 全局 fetch 直测）── */

function mockFetch(jsonResult) {
  const orig = global.fetch;
  global.fetch = function () {
    return Promise.resolve({ ok: true, json: () => Promise.resolve(jsonResult) });
  };
  return orig;
}

test('fetchPriceChange1y 用窗口首尾收盘价计算一年涨跌 (#40)', async () => {
  // 模拟实测：请求 250 根返回 251 根，rows[0] 约 1 年前，末行最新
  const rows = [];
  for (let i = 0; i < 250; i++) rows.push(['2025-07-28', '42.424', '42.424', '42.424', '42.424']);
  rows[0][2] = '42.424';              // 窗口起点（约 1 年前）
  rows[rows.length - 1][2] = '38.80'; // 最新收盘
  const orig = mockFetch({ data: { sh600000: { qfqday: rows } } });
  try {
    const v = await DS.fetchPriceChange1y('600000');
    assert.ok(Math.abs(v - (38.80 - 42.424) / 42.424) < 1e-9, `实际 ${v}`);
    assert.ok(v < 0); // 下跌 → 被动高股息红旗可达（-30% 阈值非死代码）
  } finally {
    global.fetch = orig;
  }
});

test('fetchPriceChange1y 数据不足/非法返回 null (#40)', async () => {
  const cases = [
    { data: { sh600000: { qfqday: [] } } },                              // 空数组
    { data: { sh600000: { qfqday: [['2026-08-07', '38', '38']] } } },    // 单根
    { data: { sh600000: { qfqday: [['2025-07-28', '0', '0'], ['2026-08-07', '38', '38']] } } }, // past<=0
    { data: {} },                                                        // 无节点
    null,                                                                // 响应异常 → catch → null
  ];
  for (const c of cases) {
    const orig = mockFetch(c);
    try {
      assert.equal(await DS.fetchPriceChange1y('600000'), null);
    } finally {
      global.fetch = orig;
    }
  }
});

test('fetchPriceChange1y 前缀 sh/bj/sz (#40)', async () => {
  const urls = [];
  const orig = global.fetch;
  global.fetch = function (url) {
    urls.push(url);
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ data: {} }) });
  };
  try {
    await DS.fetchPriceChange1y('600000');
    await DS.fetchPriceChange1y('920001');
    await DS.fetchPriceChange1y('000001');
    assert.ok(urls[0].includes('sh600000'));
    assert.ok(urls[1].includes('bj920001'));
    assert.ok(urls[2].includes('sz000001'));
  } finally {
    global.fetch = orig;
  }
});

test('fetchTop10Holding 百分数求和转小数 (#40)', async () => {
  const orig = mockFetch({ result: { data: [
    { HOLD_NUM_RATIO: 12.5 }, { HOLD_NUM_RATIO: 8.2 }, { HOLD_NUM_RATIO: '3.30' },
  ] } });
  try {
    const v = await DS.fetchTop10Holding('600000');
    assert.equal(v, (12.5 + 8.2 + 3.3) / 100);
  } finally {
    global.fetch = orig;
  }
});

test('fetchTop10Holding 空数据/全空值返回 null (#40)', async () => {
  const cases = [
    { result: { data: [] } },                                            // 空数据
    { result: { data: [{ HOLD_NUM_RATIO: null }, { HOLD_NUM_RATIO: '--' }] } }, // 全空值 → sum=0
    {},                                                                    // 无 result
  ];
  for (const c of cases) {
    const orig = mockFetch(c);
    try {
      assert.equal(await DS.fetchTop10Holding('600000'), null);
    } finally {
      global.fetch = orig;
    }
  }
});

test('fetchTop10Holding 后缀 SH/BJ/SZ (#40)', async () => {
  const urls = [];
  const orig = global.fetch;
  global.fetch = function (url) {
    urls.push(url);
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ result: { data: [] } }) });
  };
  try {
    await DS.fetchTop10Holding('600000');
    await DS.fetchTop10Holding('920001');
    await DS.fetchTop10Holding('000001');
    assert.ok(urls[0].includes('600000.SH'));
    assert.ok(urls[1].includes('920001.BJ'));
    assert.ok(urls[2].includes('000001.SZ'));
  } finally {
    global.fetch = orig;
  }
});
