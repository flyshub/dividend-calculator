/* 组合股息分析纯函数（#31）— 镜像 src/portfolio.py 的 parse_holdings / summarize。
 * 页面与 Node 测试共用；不做网络请求，输入为单股分析结果折算后的持仓行。 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.Portfolio = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  /* "600900:1000, 601398:5000" 或 "600900 1000" → [{code, shares}]
   * 空项/非法股数抛 Error（与 Python parse_holdings 一样，不静默丢弃）。 */
  function parseHoldings(text) {
    var items = String(text || '').replace(/，/g, ',').split(/[,;\n]/);
    var out = [];
    items.forEach(function (raw) {
      var item = raw.trim();
      if (!item) return;
      var m = item.match(/^([0-9A-Za-z]+)\s*[:：\s]\s*([0-9.]+)$/);
      if (!m) throw new Error('持仓格式应为「代码:股数」，收到 ' + item);
      var shares = Number(m[2]);
      if (!isFinite(shares) || shares <= 0) throw new Error('股数必须为正：' + item);
      out.push({ code: m[1], shares: shares });
    });
    if (!out.length) throw new Error('持仓为空');
    return out;
  }

  /* 单股分析结果 → 持仓行（对齐 Python build_position） */
  function buildPosition(code, shares, analysis) {
    var pos = { code: code, shares: shares, name: null, price: null,
                market_value: null, dividend_per_share: null, annual_income: null,
                dividend_yield: null, industry: null, verdict: null, error: null };
    if (!analysis) { pos.error = '分析失败（取数失败或代码不存在）'; return pos; }

    var si = analysis.stock_info || {};
    var di = analysis.dividend || {};
    var pr = analysis.pr || {};
    var sus = analysis.sustainability || null;

    pos.name = si.stock_name || null;
    pos.price = si.current_price != null ? Number(si.current_price) : null;
    if (pos.price > 0) pos.market_value = pos.price * shares;

    var totalShares = Number(si.total_shares) || 0;
    var dividendTotal = Number(di.total_dividend) || 0;
    if (totalShares > 0 && dividendTotal > 0) {
      pos.dividend_per_share = dividendTotal / totalShares;
      pos.annual_income = pos.dividend_per_share * shares;
      if (pos.market_value > 0) pos.dividend_yield = pos.annual_income / pos.market_value * 100;
    } else {
      pos.error = '最新完整财年无现金分红';
    }

    pos.industry = pr.industry || null;
    if (sus) { pos.verdict = sus.verdict || null; }
    return pos;
  }

  /* 聚合（对齐 Python summarize）：失败持仓不进分子也不进分母 */
  function summarize(positions) {
    var usable = positions.filter(function (p) { return !p.error; });
    var mv = 0, income = 0;
    usable.forEach(function (p) {
      if (p.market_value) mv += p.market_value;
      if (p.annual_income) income += p.annual_income;
    });

    var byIndustry = {};
    usable.forEach(function (p) {
      if (!p.annual_income) return;
      var key = p.industry || '未知行业';
      byIndustry[key] = (byIndustry[key] || 0) + p.annual_income;
    });
    var industry = Object.keys(byIndustry).map(function (k) {
      return [k, byIndustry[k], income > 0 ? byIndustry[k] / income * 100 : 0];
    }).sort(function (a, b) { return b[1] - a[1]; });

    var verdicts = {};
    usable.forEach(function (p) {
      var key = p.verdict || '未评估';
      verdicts[key] = (verdicts[key] || 0) + 1;
    });

    return {
      positions: positions,
      total_market_value: mv,
      total_annual_income: income,
      weighted_yield: mv > 0 ? income / mv * 100 : null,
      after_tax_income_10: income * 0.9,
      after_tax_income_20: income * 0.8,
      industry_breakdown: industry,
      verdict_summary: verdicts,
      errors: positions.filter(function (p) { return p.error; })
        .map(function (p) { return p.code + ': ' + p.error; }),
    };
  }

  return { parseHoldings: parseHoldings, buildPosition: buildPosition, summarize: summarize };
}));
