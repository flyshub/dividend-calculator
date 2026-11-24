#!/usr/bin/env node
/* JS fixture runner — 从 fixture JSON 读取原始数据，用 JS 纯计算逻辑出结果。
 * fixture 由 scripts/verify_js_vs_python.py 生成（与 Python 走相同 HTTP 接口）。
 * 用法: node site/js/verify_raw.js <fixture.json> */
'use strict';
var path = require('path');
var App = require(path.join(__dirname, 'app.js'));
var formatResult = require(path.join(__dirname, 'formatResult.js'));

var fixturePath = process.argv[2];
if (!fixturePath) {
  console.error('用法: node site/js/verify_raw.js <fixture.json>');
  process.exit(1);
}

var fixture = JSON.parse(require('fs').readFileSync(fixturePath, 'utf8'));
var out = {};

Object.keys(fixture.stocks || {}).forEach(function (code) {
  var raw = fixture.stocks[code];
  try {
    out[code] = formatResult(App.computeFromRaw(raw));
  } catch (e) {
    out[code] = { stock_code: code, error: String(e.message || e) };
  }
});

/* 组合股息（#31）双端对账：用同一批 fixture 原始数据跑 Portfolio.buildPosition+summarize。
 * 每只固定 1000 股，Python 侧用同一股数，只比口径算术，不比用户输入。 */
if (fixture.portfolio) {
  var Portfolio = require(path.join(__dirname, 'portfolio.js'));
  var rows = Object.keys(fixture.portfolio).map(function (code) {
    var raw = fixture.stocks[code];
    return Portfolio.buildPosition(code, fixture.portfolio[code], raw ? App.computeFromRaw(raw) : null);
  });
  /* 边界行（合成、两端完全相同）：真实样本里默认 6 只都有分红，
   * 覆盖不到「无分红仍计入市值」与「取数失败整体退出」两条口径分支。
   * 只验证算术两端一致，不代表任何真实公司。 */
  rows.push({ code: '__no_div__', shares: 1000, name: null, price: null,
              market_value: 30000, dividend_per_share: null, annual_income: null,
              dividend_yield: null, industry: '未知行业', verdict: null,
              error: '最新完整财年无现金分红' });
  rows.push({ code: '__failed__', shares: 1000, name: null, price: null,
              market_value: null, dividend_per_share: null, annual_income: null,
              dividend_yield: null, industry: null, verdict: null,
              error: '分析失败（取数失败或代码不存在）' });
  var sum = Portfolio.summarize(rows);
  out.__portfolio__ = {
    total_market_value: sum.total_market_value,
    total_annual_income: sum.total_annual_income,
    weighted_yield: sum.weighted_yield,
    after_tax_income_10: sum.after_tax_income_10,
    after_tax_income_20: sum.after_tax_income_20,
    industry: sum.industry_breakdown.map(function (r) { return [r[0], r[1]]; }),
    verdicts: sum.verdict_summary,
    errors: sum.errors,
  };
}

console.log(JSON.stringify(out, null, 2));
