/* datasources.test.js — 一致预期响应三分类（对齐 tests/test_eastmoney_forecast.py）
 * 东财对「无机构覆盖」与「服务端错误」都回 HTTP 200 + success=false，
 * 只能靠 code 区分（9201 = 返回数据为空）。误判会让页面把故障显示成"无预测"。 */
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const path = require('path');

const DS = require(path.join(__dirname, 'datasources.js'));
const classify = DS.forecastRowsFromPayload;

const ROW = { YEAR1: 2025, YEAR_MARK1: 'A', EPS1: 40.0, RATING_ORG_NUM: 20 };

test('forecastRowsFromPayload success + data → 行数组', () => {
  assert.deepEqual(classify({ success: true, code: 0, result: { data: [ROW] } }), [ROW]);
});

test('forecastRowsFromPayload success 但无 data → 无覆盖', () => {
  assert.deepEqual(classify({ success: true, code: 0, result: null }), []);
});

test('forecastRowsFromPayload code 9201 → 无覆盖（300441 实测形状）', () => {
  assert.deepEqual(classify({ success: false, code: 9201, message: '返回数据为空', result: null }), []);
});

test('forecastRowsFromPayload 其他 code → 取数失败', () => {
  assert.equal(classify({ success: false, code: 9501, message: '参数预处理错误', result: null }), null);
});

test('forecastRowsFromPayload 空 payload → 取数失败', () => {
  assert.equal(classify(null), null);
  assert.equal(classify(undefined), null);
});

test('forecastRowsFromPayload 缺 success 但带数据 → 按有数据处理', () => {
  assert.deepEqual(classify({ result: { data: [ROW] } }), [ROW]);
});
