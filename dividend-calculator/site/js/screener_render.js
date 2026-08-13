/* 选股结果页渲染纯函数 — site/screener.html 共用
 * 纯函数无 DOM/网络依赖，浏览器与 Node(测试) 共用。
 * esc/fmtNum 是 XSS 与 NaN 防护的核心，必须有单测兜底。 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.ScreenerRender = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  // HTML 转义：代码/名称/行业来自外部数据源，禁止未经转义拼进 innerHTML（XSS）
  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // 数字格式化：空值/非数字（含缺数占位符如 "—"）显示为空，不渲染 NaN 或 0.00
  function fmtNum(v, digits) {
    if (v == null || v === '') return '';
    var n = Number(v);
    return isFinite(n) ? n.toFixed(digits) : '';
  }

  // 估值/可持续性徽章（zone 已由调用方 esc，此处不再转义）
  function zoneBadge(zone) {
    var cls = zone === '低估' || zone === '合理偏低' ? 'badge-green' :
              (zone === '高估' ? '' : 'badge-gray');
    if (zone === '高估') return '<span class="badge" style="background:var(--red-soft);color:var(--red);border:1px solid var(--red-line);">' + zone + '</span>';
    return cls ? '<span class="badge ' + cls + '">' + zone + '</span>' : zone || '';
  }
  function susBadge(sus) {
    var cls = sus === '可持续' ? 'badge-green' : (sus === '偏弱' ? 'badge-amber' : 'badge-gray');
    return '<span class="badge ' + cls + '">' + sus + '</span>';
  }

  // 提示徽标：灰底虚线边框的注释，title 提供 hover 说明（低调，不喧宾夺主）
  function noteBadge(text, tip) {
    return ' <span class="badge badge-note" title="' + esc(tip) + '">' + esc(text) + '</span>';
  }

  // 小盘股提示：PR 阈值回测样本为沪深300，总市值 <100 亿的行加注释
  function prNote(r) {
    var cap = Number(r['总市值(亿)']);
    if (isFinite(cap) && cap < 100) {
      return noteBadge('小盘未验证', '市赚率阈值回测样本为沪深300，小盘股低 PR 可能是价值陷阱，请人工核实');
    }
    return '';
  }

  // 偏弱可持续性提示：红旗只降档不否决，偏弱可能因特别分红/突击分红或周期拐头
  function susNote(r) {
    if (r['可持续性'] === '偏弱') {
      return noteBadge('核实分红性质', '偏弱可能因特别分红/突击分红或周期拐头，请人工核实');
    }
    return '';
  }

  // 单行渲染（行字段拼接，返回值拼进 innerHTML；字符串列已 esc）
  // 列序须与 site/screener.html 的 <th> 表头一致
  function rowHtml(r) {
    return '<td>' + esc(r['代码']) + '</td>' +
      '<td>' + esc(r['名称']) + '</td>' +
      '<td class="num yield">' + fmtNum(r['真实股息率%'], 2) + '</td>' +
      '<td>' + susBadge(esc(r['可持续性'])) + susNote(r) + '</td>' +
      '<td>' + zoneBadge(esc(r['估值区间'])) + '</td>' +
      '<td class="num">' + fmtNum(r['市赚率PR'], 2) + prNote(r) + '</td>' +
      '<td class="num">' + fmtNum(r['ROE%'], 2) + '</td>' +
      '<td class="num">' + fmtNum(r['总市值(亿)'], 1) + '</td>' +
      '<td class="num">' + fmtNum(r['TTM股息率%'], 2) + '</td>' +
      '<td>' + esc(r['行业']) + '</td>' +
      '<td>' + esc(r['数据来源']) + '</td>';
  }

  return { esc: esc, fmtNum: fmtNum, zoneBadge: zoneBadge, susBadge: susBadge, noteBadge: noteBadge, prNote: prNote, susNote: susNote, rowHtml: rowHtml };
}));
