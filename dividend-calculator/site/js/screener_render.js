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

  // 高回报提示：估值不变下的股东总回报率 > 15%（用户约定阈值，非回测结论）
  function tsrNote(r) {
    var t = Number(r['股东总回报率%']);
    if (isFinite(t) && t > 15) {
      return noteBadge('高回报', '估值不变下的股东总回报率 >15%（约定阈值，非回测结论；假设 PB 一年不变）');
    }
    return '';
  }

  /* #30 分红趋势列：增长/萎缩/平稳 + 年化 CAGR（仅展示，不参与漏斗判据）。
   * 数据来自 CSV 的「分红趋势」「分红CAGR%」两列。2026-11 前写入历史 JSON 的旧列名
   * 「股息趋势」「股息CAGR%」仍然读取（旧数据兼容）；更早的历史 JSON 无这两列 → 显示 — */
  function divTrendCell(r) {
    var trendRaw = r['分红趋势'] != null ? r['分红趋势'] : r['股息趋势'];
    var trend = trendRaw ? String(trendRaw) : '';
    var cagr = Number(r['分红CAGR%'] != null ? r['分红CAGR%'] : r['股息CAGR%']);
    if (!trend) return '<td class="num">—</td>';
    var color = trend === '萎缩' ? '#e5534b' : (trend === '增长' ? '#3fb950' : 'inherit');
    var pct = isFinite(cagr) ? ' ' + (cagr > 0 ? '+' : '') + cagr.toFixed(1) + '%' : '';
    return '<td class="num"><span style="color:' + color + '">' + esc(trend) + pct + '</span></td>';
  }

  /* #158 不可估原因文案（九状态全覆盖，市场事实 vs 数据缺口分开）：
   * 市场事实 = 公司/市场层面真的没有预测或没有分红；数据缺口 = 我们这边缺数据，
   * 不能读成「没有机构看好」。未知状态返回空串（宁可不解释，也不泄漏英文枚举）。 */
  var FORWARD_REASONS = {
    no_coverage: '市场：无机构盈利预测',
    no_positive_eps: '市场：机构预测亏损',
    no_target_year: '市场：无目标财年预测',
    no_complete_year: '市场：近年无完整财年分红',
    stale: '市场：近年无分红',
    fetch_error: '数据缺口：前瞻取数失败',
    missing_inputs: '数据缺口：缺少分红/财务快照',
    no_payout_history: '数据缺口：无可用分红历史',
  };

  function forwardReason(status) {
    return FORWARD_REASONS[status] || '';
  }

  /* #157 前瞻股息率列：数字 + 目标财年/口径提示（仅展示，不参与漏斗判据）。
   * 口径 = 下一财年一致预期 EPS × 近 3 年支付率中位数 ÷ 现价（税前，模型估算）。
   * 不可估与历史 JSON 无该列 → —，不填 0（数据铁律）。 */
  function forwardCell(r) {
    var y = fmtNum(r['前瞻股息率%'], 2);
    var year = r['前瞻财年'];
    var reason = forwardReason(r['前瞻状态']);
    var basis = '模型估算，税前，非公司公告';
    if (!y) {
      if (year == null || year === '') return '<td class="num">—</td>';
      if (!reason) return '<td class="num">—</td>';
      return '<td class="num" title="' +
        esc('前瞻财年 ' + year + '；' + reason + '；' + basis) + '">—</td>';
    }
    var tip = (year ? '前瞻财年 ' + year + '；' : '') + basis;
    return '<td class="num" title="' + esc(tip) + '">' + y + '</td>';
  }

  // 单行渲染（行字段拼接，返回值拼进 innerHTML；字符串列已 esc）
  // 列序须与 site/screener.html 的 <th> 表头一致
  function rowHtml(r) {
    return '<td>' + esc(r['代码']) + '</td>' +
      '<td>' + esc(r['名称']) + '</td>' +
      '<td class="num yield">' + fmtNum(r['真实股息率%'], 2) + '</td>' +
      forwardCell(r) +
      '<td>' + susBadge(esc(r['可持续性'])) + susNote(r) + '</td>' +
      divTrendCell(r) +
      '<td>' + zoneBadge(esc(r['估值区间'])) + '</td>' +
      '<td class="num">' + fmtNum(r['市赚率PR'], 2) + prNote(r) + '</td>' +
      '<td class="num">' + fmtNum(r['ROE%'], 2) + '</td>' +
      '<td class="num">' + fmtNum(r['股东总回报率%'], 2) + tsrNote(r) + '</td>' +
      '<td class="num">' + fmtNum(r['总市值(亿)'], 1) + '</td>' +
      '<td class="num">' + fmtNum(r['TTM股息率%'], 2) + '</td>' +
      '<td>' + esc(r['行业']) + '</td>' +
      '<td>' + esc(r['数据来源']) + '</td>';
  }

  return { esc: esc, fmtNum: fmtNum, zoneBadge: zoneBadge, susBadge: susBadge, noteBadge: noteBadge, prNote: prNote, susNote: susNote, tsrNote: tsrNote, divTrendCell: divTrendCell, forwardReason: forwardReason, forwardCell: forwardCell, rowHtml: rowHtml };
}));
