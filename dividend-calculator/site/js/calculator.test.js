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

// ---- computeTsr（估值不变下的股东总回报率，对齐 test_pr_calculator.py TestComputeTSR）----
test('computeTsr 正常', () => assert.equal(Calc.computeTsr(15.9, null, false, 5.0, 2.0), round2((15.9 - 10.0) + 5.0)));
test('computeTsr 周期股取5年中位数', () => assert.equal(Calc.computeTsr(20.0, 12.0, true, 5.0, 2.0), 7.0));
test('computeTsr 周期股中位数缺失回退最新', () => assert.equal(Calc.computeTsr(20.0, null, true, 5.0, 2.0), 15.0));
test('computeTsr 非周期忽略中位数', () => assert.equal(Calc.computeTsr(15.0, 12.0, false, 5.0, 2.0), 10.0));
test('computeTsr 无分红退化为ROE', () => assert.equal(Calc.computeTsr(12.0, null, false, 0.0, 3.0), 12.0));
test('computeTsr 亏损股照算', () => assert.equal(Calc.computeTsr(-8.0, null, false, 2.0, 1.5), -9.0));
test('computeTsr 任一输入null', () => {
  assert.equal(Calc.computeTsr(null, null, false, 5.0, 2.0), null);
  assert.equal(Calc.computeTsr(10.0, null, false, null, 2.0), null);
  assert.equal(Calc.computeTsr(10.0, null, false, 5.0, null), null);
});
test('computeTsr PB非正视为异常', () => {
  assert.equal(Calc.computeTsr(10.0, null, false, 5.0, 0.0), null);
  assert.equal(Calc.computeTsr(10.0, null, false, 5.0, -1.0), null);
});

// ---- selectRoe（ROE 取值规则：周期股中位数，缺失回退最新；计算与展示共用）----
test('selectRoe 周期股取中位数', () => assert.equal(Calc.selectRoe(20.0, 12.0, true), 12.0));
test('selectRoe 周期股中位数缺失回退最新', () => assert.equal(Calc.selectRoe(20.0, null, true), 20.0));
test('selectRoe 非周期忽略中位数', () => assert.equal(Calc.selectRoe(15.0, 12.0, false), 15.0));

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
test('classifyIndustry 成长-太阳能真实标签', () => {
  const r = Calc.classifyIndustry('电气设备-电源设备-太阳能');
  assert.equal(r.isGrowth, true);
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

// ---- isImplemented / toChartDividendRecords（走势图映射单一实现）----
test('isImplemented T5 判定（对齐 Python _is_implemented）', () => {
  assert.equal(Calc.isImplemented('实施分配'), true);
  assert.equal(Calc.isImplemented('董事会决议通过'), false);
  assert.equal(Calc.isImplemented('未实施'), false);
  assert.equal(Calc.isImplemented(''), false);
  assert.equal(Calc.isImplemented(null), false);
});

test('toChartDividendRecords 现金行映射 + 实施过滤 + 预披露排除', () => {
  const rows = [
    { REPORT_DATE: '2024-12-31 00:00:00', PRETAX_BONUS_RMB: 7.9, IT_RATIO: null,
      ASSIGN_PROGRESS: '实施分配', EX_DIVIDEND_DATE: '2025-07-10 00:00:00',
      PLAN_NOTICE_DATE: '2025-04-25 00:00:00', TOTAL_SHARES: 2.4468e10 },
    { REPORT_DATE: '2025-12-31 00:00:00', PRETAX_BONUS_RMB: 8,
      ASSIGN_PROGRESS: '预披露', EX_DIVIDEND_DATE: '2026-07-10 00:00:00' },
    { REPORT_DATE: '2025-06-30 00:00:00', PRETAX_BONUS_RMB: 2.1,
      ASSIGN_PROGRESS: '实施分配', EX_DIVIDEND_DATE: '2026-02-12 00:00:00' },
  ];
  const out = Calc.toChartDividendRecords(rows);
  assert.equal(out.length, 2);  // 预披露排除
  const annual = out.find(r => r.report_time === '2024年报');
  assert.equal(annual.dividend_per_10, 7.9);
  assert.equal(annual.plan_notice_date, '2025-04-25');
  assert.equal(annual.total_shares, 2.4468e10);
  const interim = out.find(r => r.report_time === '2025中期分配');
  assert.equal(interim.ex_dividend_date, '2026-02-12');
  assert.equal(interim.total_shares, null);
});

test('toChartDividendRecords 纯送转行保留（per10=0，仅锚点）', () => {
  const rows = [
    { REPORT_DATE: '2021-12-31 00:00:00', PRETAX_BONUS_RMB: null, IT_RATIO: 10,
      ASSIGN_PROGRESS: '实施分配', EX_DIVIDEND_DATE: '2021-12-20 00:00:00',
      TOTAL_SHARES: 1.0e8 },
    { REPORT_DATE: '2021-12-31 00:00:00', PRETAX_BONUS_RMB: 0, IT_RATIO: 0,
      ASSIGN_PROGRESS: '实施分配', EX_DIVIDEND_DATE: '2022-01-20 00:00:00' },
  ];
  const out = Calc.toChartDividendRecords(rows);
  assert.equal(out.length, 1);  // 无现金无送转 → 丢弃
  assert.equal(out[0].dividend_per_10, 0);
  assert.equal(out[0].transfer_per_10, 10);
  assert.equal(out[0].total_shares, 1.0e8);
});

// ---- computeDividendYields（走势图总额法）----
/* 回归案例（嘉友国际 603871，2026-08 实测东财/腾讯数据）：
 * 高送转公司，旧每股法（每股分红÷前复权价）曾把 2021/01 显示为 45.77%。
 * 真实数字：2019年度 10派10（除权 2020-06-15，登记股本 1.568 亿股 = 除权前），
 * 2021-01-29 名义收盘价 19.17 元（前复权价仅 2.185，口径错配 ~8.8 倍）；
 * 2021-01 时点股本 2.195 亿股（2020-06 的 10转4 已生效）→ 真实股息率 3.73%。 */
function jyRecords() {
  return [
    { ex_dividend_date: '2019-06-05', dividend_per_10: 5, report_time: '2018年报',
      plan_notice_date: '2019-04-16', total_shares: 1.120e8, transfer_per_10: 4 },
    { ex_dividend_date: '2020-06-15', dividend_per_10: 10, report_time: '2019年报',
      plan_notice_date: '2020-04-28', total_shares: 1.568e8, transfer_per_10: 4 },
    { ex_dividend_date: '2021-06-15', dividend_per_10: 10, report_time: '2020年报',
      plan_notice_date: '2021-04-23', total_shares: 2.195e8, transfer_per_10: 3 },
  ];
}

test('computeDividendYields 高送转公司总额法（嘉友 603871 回归）', () => {
  const prices = [
    { date: '2021-01-29', close: 2.185, close_nominal: 19.17 },
  ];
  const out = Calc.computeDividendYields(prices, jyRecords());
  // 分子 1元/股×1.568亿（2019年度除权前登记股本）
  // 分母 19.17 × 2.195亿（2021-01 时点股本，2020-06 送转已生效）
  assert.equal(out[0].yield, round2(1.568e8 / (19.17 * 2.195e8) * 100));
  assert.equal(out[0].fiscalYear, 2019);
  assert.equal(out[0].price, 2.185);
});

test('computeDividendYields 财年归因：2021/05 切换到 2020 年度', () => {
  const prices = [
    { date: '2021-05-31', close: 3.06, close_nominal: 26.85 },  // 2020年报预案 2021-04-23 已公告
    { date: '2021-01-29', close: 2.185, close_nominal: 19.17 },
  ];
  const out = Calc.computeDividendYields(prices, jyRecords());
  // 2021-05：分子 2020 年度 1元×2.195亿（其登记股本），分母 26.85×2.195亿（送转未发生）
  assert.equal(out[0].fiscalYear, 2020);
  assert.equal(out[0].yield, round2(2.195e8 / (26.85 * 2.195e8) * 100));
  assert.equal(out[1].fiscalYear, 2019);
});

test('computeDividendYields 晚于全部除权：优先当前总股本（与主卡自洽）', () => {
  const prices = [{ date: '2022-01-28', close: 2.8, close_nominal: 24.5 }];
  const out = Calc.computeDividendYields(prices, jyRecords(), 3.170e8);
  // 2022-01：分子 2020 年度 2.195亿，分母 24.5 × 3.17亿（当前总股本）
  assert.equal(out[0].yield, round2(2.195e8 / (24.5 * 3.170e8) * 100));
});

test('computeDividendYields 晚于全部除权且无当前股本：末条送转因子回退', () => {
  const prices = [{ date: '2022-01-28', close: 2.8, close_nominal: 24.5 }];
  const out = Calc.computeDividendYields(prices, jyRecords());
  // 末条 2021-06-15 登记 2.195亿 × (10+3)/10（10转3）
  assert.equal(out[0].yield, round2(2.195e8 / (24.5 * 2.195e8 * 1.3) * 100));
});

test('computeDividendYields 无送转时与每股法一致（等价性）', () => {
  // 股本恒定 S：总额法 = Σdps×S ÷ (P×S) = Σdps/P
  const S = 5.0e9;
  const recs = [
    { ex_dividend_date: '2025-07-10', dividend_per_10: 8, report_time: '2024年报',
      plan_notice_date: '2025-04-30', total_shares: S },
    { ex_dividend_date: '2024-07-10', dividend_per_10: 7.5, report_time: '2023年报',
      plan_notice_date: '2024-04-30', total_shares: S },
  ];
  const prices = [{ date: '2025-08-29', close: 25.0, close_nominal: 25.0 }];
  const out = Calc.computeDividendYields(prices, recs, S);
  assert.equal(out[0].yield, round2(0.8 / 25.0 * 100));
});

test('computeDividendYields 股本缺失 → null（宁缺毋假）', () => {
  const recs = [
    { ex_dividend_date: '2024-07-10', dividend_per_10: 5, report_time: '2023年报',
      plan_notice_date: '2024-04-30', total_shares: null },  // mootdx 兜底无股本
  ];
  const prices = [{ date: '2024-08-30', close: 10.0, close_nominal: 10.0 }];
  const out = Calc.computeDividendYields(prices, recs);
  assert.equal(out[0].yield, null);
  assert.equal(out[0].fiscalYear, 2023);
});

test('computeDividendYields 名义价缺失 → null', () => {
  const prices = [{ date: '2021-01-29', close: 2.185, close_nominal: null }];
  const out = Calc.computeDividendYields(prices, jyRecords());
  assert.equal(out[0].yield, null);
});

test('computeDividendYields 早于首笔分红 → 无归因财年 yield=null', () => {
  const prices = [{ date: '2017-12-29', close: 15.0, close_nominal: 30.0 }];
  const out = Calc.computeDividendYields(prices, jyRecords());
  assert.equal(out[0].fiscalYear, null);
  assert.equal(out[0].yield, null);
});

test('computeDividendYields 纯送转行进股本锚点链，不进分子（H1 回归）', () => {
  /* 1 亿股：2020 年度 10派5（登记 1 亿）；2021-12 纯 10转10（无现金，登记仍 1 亿）；
   * 2021 年度 10派5（登记 2 亿，除权 2022-06）。若纯送转行被金额过滤丢弃，
   * 2021-09 的锚点会错到送转后的 2 亿 → 股息率 2.50%（错一倍且无提示）。 */
  const recs = [
    { ex_dividend_date: '2021-06-10', dividend_per_10: 5, report_time: '2020年报',
      plan_notice_date: '2021-04-20', total_shares: 1.0e8 },
    { ex_dividend_date: '2021-12-20', dividend_per_10: 0, report_time: '2021年报',
      plan_notice_date: '', total_shares: 1.0e8, transfer_per_10: 10 },
    { ex_dividend_date: '2022-06-10', dividend_per_10: 5, report_time: '2021年报',
      plan_notice_date: '2022-04-20', total_shares: 2.0e8 },
  ];
  const prices = [
    { date: '2021-09-30', close: 10.0, close_nominal: 10.0 },  // 送转前：股本 1 亿
    { date: '2022-01-31', close: 5.0, close_nominal: 5.0 },    // 送转后：股本 2 亿
  ];
  const out = Calc.computeDividendYields(prices, recs);
  // 2021-09：锚定纯送转行登记股本 1 亿；归因 2020 年度（2021 年报未公告）
  assert.equal(out[0].yield, round2(0.5e8 / (10.0 * 1.0e8) * 100));  // 5.00，非 2.50
  // 2022-01：送转后锚定下一分红登记股本 2 亿；仍归因 2020 年度
  assert.equal(out[1].yield, round2(0.5e8 / (5.0 * 2.0e8) * 100));
});

test('parseDividendRecords 仅中期分配无年报 → 无完整财年，不回退 (#99)', () => {
  // 对齐 Python dividend_records：latest_year=None → total=0、year=null（项目原则：
  // 最新完整财年 > TTM，仅12月=年报；无年报公司 dividend_year 应为 None 而非退回最新有数据年份）
  const rows = [
    { REPORT_DATE: '2025-06-30 00:00:00', PRETAX_BONUS_RMB: 2, ASSIGN_PROGRESS: '实施分配' },
    { REPORT_DATE: '2024-09-30 00:00:00', PRETAX_BONUS_RMB: 1.5, ASSIGN_PROGRESS: '实施分配' },
  ];
  const r = Calc.parseDividendRecords(rows, 1000);
  assert.equal(r.totalDividend, 0);
  assert.equal(r.year, null);
  assert.equal(r.details.length, 0);
  assert.equal(r.explanation, '无有效分红数据');
});
test('parseDividendRecords 明细按除权日升序 (#99)', () => {
  // 对齐 Python dividend_records.summary.records（ex_dividend_date 升序，空串在前）
  const rows = [
    { REPORT_DATE: '2025-12-31 00:00:00', PRETAX_BONUS_RMB: 4, ASSIGN_PROGRESS: '实施分配', EX_DIVIDEND_DATE: '2026-07-15 00:00:00' },
    { REPORT_DATE: '2025-06-30 00:00:00', PRETAX_BONUS_RMB: 2, ASSIGN_PROGRESS: '实施分配', EX_DIVIDEND_DATE: '2025-12-01 00:00:00' },
  ];
  const r = Calc.parseDividendRecords(rows, 1000);
  assert.deepEqual(r.details.map(d => d.report_time), ['2025中期分配', '2025年报']);
});
test('parseDividendRecords 历史聚合各年用行股本 (P1-1)', () => {
  // 东财 RPT_SHAREBONUS_DET 行自带 TOTAL_SHARES（历史股本）：600900 2021=22741859230、2025=24468217716
  const rows = [
    { REPORT_DATE: '2025-12-31 00:00:00', PRETAX_BONUS_RMB: 10, ASSIGN_PROGRESS: '实施分配', TOTAL_SHARES: 24468217716 },
    { REPORT_DATE: '2021-12-31 00:00:00', PRETAX_BONUS_RMB: 10, ASSIGN_PROGRESS: '实施分配', TOTAL_SHARES: 22741859230 },
  ];
  const r = Calc.parseDividendRecords(rows, 1e9); // 参数股本 10 亿，应被行股本覆盖
  // 最新财年股息率仍用参数股本（DPS/股价自洽，P1-1 不改）
  assert.equal(r.totalDividend, (10 / 10) * 1e9);
  const h = r.sustainabilityHistory;
  assert.equal(h.latest_year_amount, (10 / 10) * 24468217716);  // 2025 行股本
  assert.equal(h.history_mean_amount, (10 / 10) * 22741859230); // 2021 行股本
  assert.equal(h.consecutive_years, 1); // 2025→2024 断档（无 2022-2024 数据）
});
test('parseDividendRecords 行股本缺失回退参数股本 (P1-1)', () => {
  const rows = [
    { REPORT_DATE: '2025-12-31 00:00:00', PRETAX_BONUS_RMB: 10, ASSIGN_PROGRESS: '实施分配' },
    { REPORT_DATE: '2024-12-31 00:00:00', PRETAX_BONUS_RMB: 10, ASSIGN_PROGRESS: '实施分配' },
  ];
  const r = Calc.parseDividendRecords(rows, 5e9);
  const h = r.sustainabilityHistory;
  assert.equal(h.latest_year_amount, (10 / 10) * 5e9);
  assert.equal(h.history_mean_amount, (10 / 10) * 5e9);
});
test('parseDividendRecords 行股本为0/NaN视为无效回退参数 (P1-1)', () => {
  const rows = [
    { REPORT_DATE: '2025-12-31 00:00:00', PRETAX_BONUS_RMB: 10, ASSIGN_PROGRESS: '实施分配', TOTAL_SHARES: 0 },
    { REPORT_DATE: '2024-12-31 00:00:00', PRETAX_BONUS_RMB: 10, ASSIGN_PROGRESS: '实施分配', TOTAL_SHARES: 'abc' },
  ];
  const r = Calc.parseDividendRecords(rows, 5e9);
  const h = r.sustainabilityHistory;
  assert.equal(h.latest_year_amount, (10 / 10) * 5e9);
  assert.equal(h.history_mean_amount, (10 / 10) * 5e9);
});
test('parseDividendRecords 同年多笔分红各用各行股本 (P1-1)', () => {
  // 同一财年中期分配与年报实施时股本不同（增发/转送）→ 逐笔折算，与 Python 逐记录一致
  const rows = [
    { REPORT_DATE: '2025-12-31 00:00:00', PRETAX_BONUS_RMB: 80, ASSIGN_PROGRESS: '实施分配', TOTAL_SHARES: 24468217716 },
    { REPORT_DATE: '2025-09-30 00:00:00', PRETAX_BONUS_RMB: 30, ASSIGN_PROGRESS: '实施分配', TOTAL_SHARES: 22741859230 },
  ];
  const r = Calc.parseDividendRecords(rows, 1e9);
  const h = r.sustainabilityHistory;
  const perRecord = (30 / 10) * 22741859230 + (80 / 10) * 24468217716;
  assert.equal(h.latest_year_amount, perRecord);
  assert.equal(h.history_mean_amount, null); // 仅一年数据，无历史年
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

test('computePr 判定档位用基础 PR，支付率低不抬档（2026-11 银行回归）', () => {
  // PE 5.59 / ROE 9.15 → 基础 PR 0.61 合理偏低；若按修正 PR（N=2）会变 1.22 合理被剔除
  const r = Calc.computePr({ pe_ttm: 5.59, pb: 0.6, roe_latest: 9.15, roe_5y_median: 10.64,
                             is_cyclical: false, net_profit_annual: 100, dividend_total: 12.9 });
  assert.equal(r.pr_basic, 0.61);
  assert.equal(r.pr_judge, 0.61);
  assert.equal(r.roe_judge, 9.15);
  assert.equal(r.valuation_zone, '合理偏低');
  assert.ok(r.pr_corrected > 1.0);  // 修正 PR 仍是 1.22，但只展示
});

test('judgePR 周期股用 5 年 ROE 中位数判档（页面与选股器同口径）', () => {
  // 平安: PE 9.88, roe_latest 14.0, 5年中位数 13.2 → 0.75 合理偏低（非 0.71）
  const j = Calc.judgePR(9.88, 14.0, 13.2, true);
  assert.equal(j.roe, 13.2);
  assert.equal(j.pr, 0.75);
  assert.equal(j.zone, '合理偏低');
  // 非周期股用最新 ROE
  const k = Calc.judgePR(9.88, 14.0, 13.2, false);
  assert.equal(k.roe, 14.0);
  assert.equal(k.pr, 0.71);
});

test('judgePR 缺 PE / ROE ≤ 0 → 无法判定', () => {
  assert.equal(Calc.judgePR(null, 14.0, 13.2, false).zone, '无法判定');
  assert.equal(Calc.judgePR(10, 0, null, false).zone, '无法判定');
  assert.equal(Calc.judgePR(10, null, null, false).zone, '无法判定');
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

// ---- buildExDivMarkers（走势图除权标记）----
/* 2026-11 审计：旧内联实现在 index.html 里用 ex_dividend_date 作 coord.x，而 x 轴
 * 是月末类目 → 该类目不在轴上，标记无法定位；窗口外的历史除权事件也被钉在图表首月
 * 的价格上产生越界标记。下面钉住新契约。 */
function monthPrices() {
  return [
    { date: '2026-01-30', close: 10.0, close_nominal: 12.0 },
    { date: '2026-02-27', close: 11.0, close_nominal: 13.0 },
    { date: '2026-04-30', close: 9.5, close_nominal: 11.5 },
  ];
}

test('buildExDivMarkers x 落在轴上的月末日期，而非月中除权日', () => {
  const recs = [
    { ex_dividend_date: '2026-02-16', dividend_per_10: 5, report_time: '2025年报',
      transfer_per_10: 0, total_shares: 1e9 },
  ];
  const out = Calc.buildExDivMarkers(monthPrices(), recs);
  assert.equal(out.length, 1);
  assert.deepEqual(out[0].coord, ['2026-02-27', 11.0]);  // 除权日之后第一个月末
  assert.equal(out[0].name, '2025年报: 10派5元');
});

test('buildExDivMarkers 窗口外的除权事件不产生标记', () => {
  const recs = [
    { ex_dividend_date: '2015-07-01', dividend_per_10: 3, report_time: '2014年报', transfer_per_10: 0 },
    { ex_dividend_date: '2030-07-01', dividend_per_10: 3, report_time: '2029年报', transfer_per_10: 0 },
  ];
  assert.equal(Calc.buildExDivMarkers(monthPrices(), recs).length, 0);
});

test('buildExDivMarkers 纯送转不写「10派0元」，派+送转同时呈现', () => {
  const recs = [
    { ex_dividend_date: '2026-04-10', dividend_per_10: 0, report_time: '2025年报', transfer_per_10: 10 },
    { ex_dividend_date: '2026-04-20', dividend_per_10: 5, report_time: '2025中期分配', transfer_per_10: 4 },
    { ex_dividend_date: '2026-04-25', dividend_per_10: 0, report_time: '空行', transfer_per_10: 0 },
  ];
  const out = Calc.buildExDivMarkers(monthPrices(), recs);
  assert.equal(out.length, 2);
  assert.equal(out[0].name, '2025年报: 10送转10');
  assert.equal(out[1].name, '2025中期分配: 10派5元10送转4');
});

test('buildExDivMarkers 空输入安全', () => {
  assert.deepEqual(Calc.buildExDivMarkers([], [{ ex_dividend_date: '2026-02-16', dividend_per_10: 5 }]), []);
  assert.deepEqual(Calc.buildExDivMarkers(monthPrices(), null), []);
});

// ---- 走势图股息率口径：末月与主卡（总额法）同口径 ----
test('computeDividendYields 末月 = 分红总额 ÷ (月末价 × 当前总股本)（与主卡同口径）', () => {
  /* 主卡 = 财年现金分红总额 ÷ 当前总市值。图表末月必须与之一致，
   * 否则用户会看到「图 3.9%、卡片 3.6%」两个数。非送转股本变动（增发/回购注销）
   * 会让历史月份偏离每股口径（约等于股本变动比例），但末月不偏离。 */
  const S_AT_PAY = 2.0e8;   // 派息时股本
  const S_NOW = 2.4e8;      // 当前股本（增发 20%，非送转）
  const recs = [
    { ex_dividend_date: '2025-07-10', dividend_per_10: 8, report_time: '2024年报',
      plan_notice_date: '2025-04-30', total_shares: S_AT_PAY, transfer_per_10: 0 },
  ];
  const prices = [{ date: '2026-06-30', close: 20.0, close_nominal: 25.0 }];
  const out = Calc.computeDividendYields(prices, recs, S_NOW);
  assert.equal(out[0].yield, round2(0.8 * S_AT_PAY / (25.0 * S_NOW) * 100));
  assert.notEqual(out[0].yield, round2(0.8 / 25.0 * 100));  // 与每股口径不同（增发导致）
});

// ---- 月度价格多源降级（腾讯 → 东财 push2his）----
/* 2026-11-16 实测：腾讯 fqkline 限频时返回 WAF HTML，fetch 拿到非 JSON →
 * 走势图取不到数据。东财 klt=103 作退路，双口径与腾讯一致。 */
function stubFetch(handler) {
  const orig = global.fetch;
  const urls = [];
  global.fetch = function (url) {
    urls.push(String(url));
    return handler(String(url));
  };
  return { urls, restore: () => { global.fetch = orig; } };
}

test('fetchMonthlyPrices 腾讯成功时不打东财', async () => {
  const st = stubFetch(u => Promise.resolve({
    ok: true,
    json: () => Promise.resolve({
      data: { sh601318: { qfqmonth: [['2026-10-30', '0', '53.80']], month: [['2026-10-30', '0', '62.40']] } },
    }),
  }));
  try {
    const rows = await DS.fetchMonthlyPrices('601318', 120);
    assert.equal(rows.length, 1);
    assert.equal(rows[0].close, 53.8);
    assert.equal(rows[0].close_nominal, 62.4);
    assert.ok(!st.urls.some(u => u.includes('push2his')));
  } finally { st.restore(); }
});

test('fetchMonthlyPrices 腾讯抛错（WAF HTML→JSON 解析失败）→ 东财双口径', async () => {
  const st = stubFetch(u => {
    if (u.includes('push2his')) {
      const qfq = u.endsWith('fqt=1');
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({
          data: { klines: qfq ? ['2026-10-30,53.80', '2026-11-28,55.10'] : ['2026-10-30,62.40', '2026-11-28,55.10'] },
        }),
      });
    }
    return Promise.resolve({ ok: true, json: () => Promise.reject(new SyntaxError('Unexpected token <')) });
  });
  try {
    const rows = await DS.fetchMonthlyPrices('601318', 120);
    assert.deepEqual(rows.map(r => r.date), ['2026-10-30', '2026-11-28']);
    assert.deepEqual(rows.map(r => r.close), [53.8, 55.1]);          // 前复权
    assert.deepEqual(rows.map(r => r.close_nominal), [62.4, 55.1]);  // 不复权
  } finally { st.restore(); }
});

test('fetchMonthlyPrices 腾讯返回空数组 → 东财；东财不复权失败仍出价格线', async () => {
  const st = stubFetch(u => {
    if (u.includes('push2his')) {
      if (u.endsWith('fqt=1')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ data: { klines: ['2026-11-28,55.10', '2026-10-30,0'] } }) });
      }
      return Promise.resolve({ ok: false, status: 502, json: () => Promise.resolve({}) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ data: {} }) });
  });
  try {
    const rows = await DS.fetchMonthlyPrices('601318', 120);
    assert.equal(rows.length, 1);            // 非正价被丢弃
    assert.equal(rows[0].close, 55.1);
    assert.equal(rows[0].close_nominal, null);  // 宁缺毋假
  } finally { st.restore(); }
});

test('fetchMonthlyPricesEastmoney 按 months 截取最近 N 根', async () => {
  const klines = [];
  for (let i = 1; i <= 20; i++) klines.push(`20${String(10 + i).padStart(2, '0')}-01-31,${i}.0`);
  const st = stubFetch(() => Promise.resolve({ ok: true, json: () => Promise.resolve({ data: { klines } }) }));
  try {
    const rows = await DS.fetchMonthlyPricesEastmoney('000651', 5);
    assert.equal(rows.length, 5);
    assert.equal(rows[rows.length - 1].close, 20);  // 升序，取最近
  } finally { st.restore(); }
});

// ---- computeForwardYield（对齐 tests/test_forward_yield.py，同输入同期望）----
const FWD_SHARES = 1.0e9;

function fwdForecast(eps2026 = 66.0, coverage = 20, mark = 'E', base = 2025) {
  return [{
    YEAR1: base, YEAR_MARK1: 'A', EPS1: 40.0,  // 40e9 / 1e9 股，与基准年净利一致
    YEAR2: base + 1, YEAR_MARK2: mark, EPS2: eps2026,
    RATING_ORG_NUM: coverage,
  }];
}

function fwdInputs(over) {
  const base = {
    price: 1000.0,
    totalShares: FWD_SHARES,
    latestYear: 2025,
    yearlyDividendTotals: { 2025: 30e9, 2024: 28.5e9, 2023: 27e9 },
    netProfitByYear: { 2025: 40e9, 2024: 38e9, 2023: 36e9, 2026: 44e9 },
    forecast_rows: fwdForecast(),
    isCyclical: false,
    trailingYield: null,
  };
  return Object.assign(base, over || {});
}

function approx(a, b, eps) {
  assert.ok(Math.abs(a - b) <= (eps || 1e-9), `${a} != ${b}`);
}

test('computeForwardYield 正常', () => {
  const r = Calc.computeForwardYield(fwdInputs());
  assert.equal(r.forward_year, 2026);
  assert.equal(r.forward_eps_kind, 'E');
  approx(r.forward_eps, 66.0);
  approx(r.forward_payout_assumed, 0.75);
  approx(r.forward_dps, 49.5);
  approx(r.forward_yield_before_tax, 4.95);
  assert.equal(r.forward_coverage, 20);
  assert.equal(r.forward_status, 'ok');
  assert.deepEqual(r.forward_warnings, []);
  assert.equal(r.forward_note, '2026E EPS 66.00 × 支付率 75.0% ÷ 现价 1000.00');
});

test('computeForwardYield 两年样本取中位数均值', () => {
  const r = Calc.computeForwardYield(fwdInputs({
    yearlyDividendTotals: { 2025: 20e9, 2024: 40e9 },
    netProfitByYear: { 2025: 40e9, 2024: 40e9 },
  }));
  approx(r.forward_payout_assumed, 0.75);
  assert.equal(r.forward_status, 'ok');
  assert.ok(r.forward_warnings.some(w => w.includes('样本不连续')));
});

test('computeForwardYield 最新年支付率>100% 仅警示不改变中位数', () => {
  const r = Calc.computeForwardYield(fwdInputs({
    yearlyDividendTotals: { 2025: 60e9, 2024: 28e9, 2023: 24e9 },
    netProfitByYear: { 2025: 26.8e9, 2024: 40e9, 2023: 38e9 },
  }));
  approx(r.forward_payout_assumed, 0.70);
  assert.ok(r.forward_warnings.some(w => w.includes('最新财年支付率 223.9% > 100%')));
  assert.ok(r.forward_warnings.some(w => w.includes('偏离超 30 个百分点')));
});

test('computeForwardYield 基线 EPS×股本偏差在不估时仍警示', () => {
  const r = Calc.computeForwardYield(fwdInputs({
    yearlyDividendTotals: {},
    netProfitByYear: { 2025: 40e9, 2024: 38e9, 2023: 36e9 },
    totalShares: 1.5e9,
  }));
  assert.equal(r.forward_status, 'no_payout_history');
  assert.ok(r.forward_warnings.some(w => w.includes('股本口径可能不一致')));
});

test('computeForwardYield 支付率趋势偏离>30pct 警示', () => {
  const r = Calc.computeForwardYield(fwdInputs({
    yearlyDividendTotals: { 2025: 16e9, 2024: 30e9, 2023: 30e9 },
    netProfitByYear: { 2025: 40e9, 2024: 40e9, 2023: 40e9 },
  }));
  approx(r.forward_payout_assumed, 0.75);
  assert.ok(r.forward_warnings.some(w => w.includes('分红政策可能变化')));
  assert.equal(r.forward_status, 'ok');
});

test('computeForwardYield 中位数>100% 截断为 1.0', () => {
  const r = Calc.computeForwardYield(fwdInputs({
    yearlyDividendTotals: { 2025: 50e9, 2024: 45e9, 2023: 40e9 },
    netProfitByYear: { 2025: 10e9, 2024: 10e9, 2023: 10e9 },
  }));
  assert.equal(r.forward_payout_assumed, 1.0);
  assert.ok(r.forward_warnings.some(w => w.includes('截断为 100%')));
  approx(r.forward_yield_before_tax, 6.6);
});

test('computeForwardYield 年报季 L 滞后→目标年 EPS 为 A', () => {
  const r = Calc.computeForwardYield(fwdInputs({
    latestYear: 2024,
    yearlyDividendTotals: { 2024: 30e9, 2023: 28e9, 2022: 26e9 },
    netProfitByYear: { 2024: 40e9, 2023: 38e9, 2022: 36e9, 2025: 44e9 },
    forecast_rows: fwdForecast(66.0, 20, 'E', 2025),
  }));
  assert.equal(r.forward_year, 2025);
  assert.equal(r.forward_eps_kind, 'A');
});

test('computeForwardYield 无完整财年', () => {
  const r = Calc.computeForwardYield(fwdInputs({ latestYear: null }));
  assert.equal(r.forward_year, null);
  assert.equal(r.forward_status, 'no_complete_year');
});

test('computeForwardYield 无一致预期覆盖', () => {
  const noCov = Calc.computeForwardYield(fwdInputs({ forecast_rows: [] }));
  assert.equal(noCov.forward_status, 'no_coverage');
  assert.equal(noCov.forward_year, 2026);  // 目标财年可确定，UI 显示「FY2026E 无预测」
  assert.equal(noCov.forward_note, '无机构盈利预测，未做前瞻估算');
});

test('computeForwardYield 预测取数失败', () => {
  const failed = Calc.computeForwardYield(fwdInputs({ forecast_rows: null }));
  assert.equal(failed.forward_status, 'fetch_error');
  assert.equal(failed.forward_note, '前瞻取数失败（盈利预测），稍后重试');
});

test('computeForwardYield 预测亏损不估', () => {
  const r = Calc.computeForwardYield(fwdInputs({ forecast_rows: fwdForecast(-1.09) }));
  assert.equal(r.forward_year, 2026);
  assert.equal(r.forward_eps, null);
  assert.equal(r.forward_status, 'no_positive_eps');
  assert.equal(r.forward_note, '机构预测 2026 年亏损，不估股息率');
});

test('computeForwardYield 缺目标年预测', () => {
  const rows = fwdForecast();
  rows[0].YEAR2 = 2027;
  const noTarget = Calc.computeForwardYield(fwdInputs({ forecast_rows: rows }));
  assert.equal(noTarget.forward_year, 2026);
  assert.equal(noTarget.forward_status, 'no_target_year');
  assert.equal(noTarget.forward_note, '无 2026 年盈利预测');
});

test('computeForwardYield 近年无分红（基准年领先≥2）', () => {
  const r = Calc.computeForwardYield(fwdInputs({
    latestYear: 2023,
    yearlyDividendTotals: { 2023: 10e9 },
    netProfitByYear: { 2023: 10e9 },
    forecast_rows: fwdForecast(66.0, 20, 'E', 2025),
  }));
  assert.equal(r.forward_status, 'stale');
  assert.equal(r.forward_year, null);
  assert.ok(r.forward_note.includes('近年无分红'));
});

test('computeForwardYield 无净利历史', () => {
  const noHist = Calc.computeForwardYield(fwdInputs({ netProfitByYear: {} }));
  assert.equal(noHist.forward_status, 'no_payout_history');
  assert.equal(noHist.forward_note, '无可用分红历史，不做前瞻');
});

test('computeForwardYield 缺现价', () => {
  const noPrice = Calc.computeForwardYield(fwdInputs({ price: null }));
  assert.equal(noPrice.forward_status, 'missing_inputs');
  assert.equal(noPrice.forward_note, '缺少现价，不做前瞻');
});

/* 缺现价时 warning 集合必须与 Python 一致：现价门在样本/覆盖/周期警示之后。
 * 对应 tests/test_forward_yield.py::test_missing_price_keeps_context_warnings */
test('computeForwardYield 缺现价仍保留覆盖与周期警示', () => {
  const r = Calc.computeForwardYield(fwdInputs({
    price: null, isCyclical: true, forecast_rows: fwdForecast(66.0, 1),
  }));
  assert.equal(r.forward_status, 'missing_inputs');
  assert.deepEqual(r.forward_warnings, [
    '一致预期仅 1 家机构覆盖（样本少）',
    '周期行业，盈利预测波动大，前瞻仅供参考',
  ]);
  assert.equal(r.forward_yield_before_tax, null);
});

test('computeForwardYield 周期行业警示', () => {
  const r = Calc.computeForwardYield(fwdInputs({ isCyclical: true }));
  assert.ok(r.forward_warnings.some(w => w.includes('周期行业')));
});

test('computeForwardYield 低覆盖警示', () => {
  const r = Calc.computeForwardYield(fwdInputs({ forecast_rows: fwdForecast(66.0, 2) }));
  assert.ok(r.forward_warnings.some(w => w.includes('仅 2 家机构覆盖')));
});

test('computeForwardYield 与主口径偏离>50% 警示', () => {
  const r = Calc.computeForwardYield(fwdInputs({ trailingYield: 10.0 }));
  assert.ok(r.forward_warnings.some(w => w.includes('偏离超 50%')));
});

test('computeForwardYield 合理上限警示', () => {
  const r = Calc.computeForwardYield(fwdInputs({
    price: 100.0,
    yearlyDividendTotals: { 2025: 50e9, 2024: 45e9, 2023: 40e9 },
    netProfitByYear: { 2025: 10e9, 2024: 10e9, 2023: 10e9 },
  }));
  assert.ok(r.forward_warnings.some(w => w.includes('超合理上限')));
});

test('computeForwardYield A 类 EPS×股本与净利偏差>3% 警示', () => {
  const r = Calc.computeForwardYield(fwdInputs({
    latestYear: 2024,
    totalShares: 1.2e9,
    yearlyDividendTotals: { 2024: 30e9, 2023: 28e9, 2022: 26e9 },
    netProfitByYear: { 2024: 40e9, 2023: 38e9, 2022: 36e9, 2025: 44e9 },
    forecast_rows: fwdForecast(66.0, 20, 'E', 2025),
  }));
  assert.ok(r.forward_warnings.some(w => w.includes('股本口径可能不一致')));
});

test('parseFinancials 输出 netProfitByYear（仅年报行）', () => {
  const fin = Calc.parseFinancials([
    { REPORT_DATE: '2025-12-31', ROEJQ: 15.0, PARENTNETPROFIT: 40e9 },
    { REPORT_DATE: '2025-09-30', ROEJQ: 11.0, PARENTNETPROFIT: 30e9 },
    { REPORT_DATE: '2024-12-31', ROEJQ: 14.0, PARENTNETPROFIT: 38e9 },
  ]);
  assert.deepEqual(Object.keys(fin.netProfitByYear).sort(), ['2024', '2025']);
  assert.equal(fin.netProfitByYear[2025], 40e9);
});

test('parseDividendRecords 输出 yearlyDividendTotals（逐笔登记股本）', () => {
  const div = Calc.parseDividendRecords([
    { REPORT_DATE: '2025-12-31', PRETAX_BONUS_RMB: 10.0, ASSIGN_PROGRESS: '实施',
      EX_DIVIDEND_DATE: '2026-07-01', TOTAL_SHARES: 1.5e9 },
    { REPORT_DATE: '2025-06-30', PRETAX_BONUS_RMB: 2.0, ASSIGN_PROGRESS: '实施',
      EX_DIVIDEND_DATE: '2025-12-01', TOTAL_SHARES: null },
  ], 1.0e9);
  approx(div.yearlyDividendTotals[2025], 10.0 / 10 * 1.5e9 + 2.0 / 10 * 1.0e9);
});

/* ── computeDividendCagr（#30，对齐 Python compute_dividend_cagr 全部用例） ── */
test('computeDividendCagr 稳定增长 10%', () => {
  const r = Calc.computeDividendCagr({ 2021: 100, 2022: 110, 2023: 121, 2024: 133.1, 2025: 146.41 });
  assert.equal(r.years, 5);
  assert.ok(Math.abs(r.cagr - 0.10) < 1e-6);
  assert.equal(r.trend, '增长');
});
test('computeDividendCagr 下降 10%', () => {
  const r = Calc.computeDividendCagr({ 2021: 100, 2022: 90, 2023: 81, 2024: 72.9, 2025: 65.61 });
  assert.ok(Math.abs(r.cagr + 0.10) < 1e-6);
  assert.equal(r.trend, '萎缩');
});
test('computeDividendCagr 持平为平稳', () => {
  const r = Calc.computeDividendCagr({ 2021: 100, 2022: 100, 2023: 100, 2024: 100, 2025: 100 });
  assert.equal(r.cagr, 0);
  assert.equal(r.trend, '平稳');
});
test('computeDividendCagr 两年不估', () => {
  const r = Calc.computeDividendCagr({ 2024: 100, 2025: 110 });
  assert.equal(r.cagr, null);
  assert.equal(r.trend, null);
  assert.equal(r.years, 2);
});
test('computeDividendCagr 缺年截断窗口', () => {
  const r = Calc.computeDividendCagr({ 2021: 80, 2022: 90, 2024: 100, 2025: 110 });
  assert.equal(r.cagr, null);
  assert.equal(r.years, 2);
});
test('computeDividendCagr 零分红年截断', () => {
  const r = Calc.computeDividendCagr({ 2021: 80, 2022: 90, 2023: 100, 2024: 0, 2025: 110 });
  assert.equal(r.cagr, null);
});
test('computeDividendCagr 最新年无分红不估', () => {
  assert.equal(Calc.computeDividendCagr({ 2024: 100 }).cagr, null);
});
test('computeDividendCagr 窗口上限 5 年', () => {
  const amts = {};
  for (let y = 2016; y <= 2025; y++) amts[y] = 100 * Math.pow(1.1, y - 2016);
  const r = Calc.computeDividendCagr(amts);
  assert.equal(r.years, 5);
  assert.ok(Math.abs(r.cagr - 0.10) < 1e-6);
});
test('computeDividendCagr 特别分红给出说明', () => {
  const r = Calc.computeDividendCagr({ 2021: 100, 2022: 100, 2023: 100, 2024: 100, 2025: 300 });
  assert.notEqual(r.cagr, null);
  assert.ok(String(r.note).indexOf('特别分红') !== -1);
});
test('computeDividendCagr 稳定增长无特别分红说明', () => {
  const r = Calc.computeDividendCagr({ 2021: 100, 2022: 110, 2023: 121, 2024: 133.1, 2025: 146.41 });
  assert.equal(r.note, null);
});
test('computeDividendCagr 空输入', () => {
  const r = Calc.computeDividendCagr({});
  assert.equal(r.cagr, null);
  assert.equal(r.years, 0);
});

test('computeDividendCagr 跳过只有中期分配的当年（#30 回归）', () => {
  const amounts = { '2026': 6.54e9, '2025': 15.39e9, '2024': 24.25e9,
                    '2023': 11.86e9, '2022': 54.83e9, '2021': 13.93e9 };
  const complete = { '2021': 1, '2022': 1, '2023': 1, '2024': 1, '2025': 1 };
  const r = Calc.computeDividendCagr(amounts, complete);
  assert.strictEqual(r.years, 5);
  assert.ok(Math.abs(r.cagr - (Math.pow(15.39e9 / 13.93e9, 0.25) - 1)) < 1e-12);
  assert.strictEqual(r.trend, '平稳');
  // 缺省时按全部年份（兜底）→ 正是要避免的假暴跌
  assert.ok(Calc.computeDividendCagr(amounts).cagr < -0.4);
});

test('computeDividendCagr 完整财年但零分红 → 链条断开', () => {
  const r = Calc.computeDividendCagr(
    { '2025': 10, '2024': 0, '2023': 9, '2022': 8, '2021': 7 },
    { '2021': 1, '2022': 1, '2023': 1, '2024': 1, '2025': 1 });
  assert.strictEqual(r.years, 1);
  assert.strictEqual(r.cagr, null);
});

/* ── #26 A 股特色风险：ST 与商誉（镜像 src/risk_flags.py）── */
test('isST 识别 ST / *ST，普通名称不误判', () => {
  assert.equal(Calc.isST('*ST宏图'), true);
  assert.equal(Calc.isST('ST 航通'), true);
  assert.equal(Calc.isST('中远海控'), false);
  assert.equal(Calc.isST(''), false);
  assert.equal(Calc.isST(null), false);
});

test('stWarning 只对 ST 返回文案', () => {
  assert.match(Calc.stWarning('*ST宏图'), /退市风险警示/);
  assert.equal(Calc.stWarning('贵州茅台'), null);
  assert.equal(Calc.stWarning(undefined), null);
});

test('goodwillWarning 阈值 50%：以上警示、以下与缺数据不警示', () => {
  // 美的 2026Q3：311.5 亿 / 2364 亿 ≈ 13.2%
  assert.equal(Calc.goodwillWarning(31146557000, 236404503000), null);
  // 刚好 50% 不警示（严格大于）
  assert.equal(Calc.goodwillWarning(500, 1000), null);
  assert.match(Calc.goodwillWarning(600, 1000), /商誉占净资产 60%/);
  // 缺数据 / 非正净资产：不补 0、不推断
  assert.equal(Calc.goodwillWarning(null, 1000), null);
  assert.equal(Calc.goodwillWarning(100, null), null);
  assert.equal(Calc.goodwillWarning(100, 0), null);
  assert.equal(Calc.goodwillWarning(0, 1000), null);
});

test('computeDividendCagr 分红次数变化 → 给年报口径对照（伊利 #30）', () => {
  const ya = { 2021: 9.6, 2022: 10.4, 2023: 12.0, 2024: 12.2, 2025: 13.8 };
  const aa = { 2021: 9.6, 2022: 10.4, 2023: 12.0, 2024: 12.2, 2025: 9.0 };
  const r = Calc.computeDividendCagr(ya, { 2021: 1, 2022: 1, 2023: 1, 2024: 1, 2025: 1 }, aa, ['2025']);
  assert.equal(r.trend, '增长');
  assert.ok(Math.abs(r.cagr - (Math.pow(13.8 / 9.6, 0.25) - 1)) < 1e-12);
  assert.ok(r.cagr_annual < 0);
  assert.match(r.note, /分红次数不一致/);
  assert.match(r.note, /-1\.6%\/年/);
});

test('computeDividendCagr 次数不变时无口径说明，年报口径等于总口径', () => {
  const ya = { 2021: 10, 2022: 11, 2023: 12 };
  const r = Calc.computeDividendCagr(ya, { 2021: 1, 2022: 1, 2023: 1 }, { 2021: 10, 2022: 11, 2023: 12 }, []);
  assert.equal(r.note, null);
  assert.ok(Math.abs(r.cagr_annual - r.cagr) < 1e-12);
});
