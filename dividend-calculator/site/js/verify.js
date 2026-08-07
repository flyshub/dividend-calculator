#!/usr/bin/env node
/* JS 实现验证脚本 — 对指定股票跑完整 JS 管线（实时数据），输出 JSON 结果。
 * 用法: node site/js/verify.js <代码1> <代码2> ...
 * 输出字段与 scripts/verify_js_vs_python.py 对齐。 */
'use strict';
var path = require('path');
var App = require(path.join(__dirname, 'app.js'));
var formatResult = require(path.join(__dirname, 'formatResult.js'));

var codes = process.argv.slice(2);
if (!codes.length) {
  console.error('用法: node site/js/verify.js <股票代码>...');
  process.exit(1);
}

Promise.all(codes.map(function (code) {
  return App.analyzeStock(code).then(function (r) {
    var out = formatResult(r);
    out.total_dividend = Math.round(out.total_dividend * 100) / 100;
    out.net_profit_latest_period = out.net_profit_latest_period == null ? null : Math.round(out.net_profit_latest_period * 100) / 100;
    out.net_profit_annual = out.net_profit_annual == null ? null : Math.round(out.net_profit_annual * 100) / 100;
    return out;
  }).catch(function (err) {
    return { stock_code: code, error: String(err.message || err) };
  });
})).then(function (results) {
  console.log(JSON.stringify(results, null, 2));
}).catch(function (err) {
  console.error('FATAL:', err);
  process.exit(1);
});
