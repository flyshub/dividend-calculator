/* 综合分析编排 — 对齐 src/analysis.py run_stock_analysis
 * 浏览器与 Node(verify) 共用。 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory(require('./calculator.js'), require('./datasources.js'));
  } else {
    root.DividendApp = factory(root.Calculator, root.DataSources);
  }
}(typeof self !== 'undefined' ? self : this, function (Calculator, DS) {
  'use strict';

  /* 股票代码归一化: 600900 / sh600900 / SH600900 → 600900 */
  function normalizeStockCode(input) {
    var s = String(input).trim().toLowerCase();
    s = s.replace(/^(sh|sz)/, '');
    if (!/^\d{6}$/.test(s)) return null;
    return s;
  }

  /* 主查询: 代码 → 股息率 + 市赚率综合分析结果
   * 返回 { stock_info, dividend, pr } */
  function analyzeStock(input) {
    return DS.fetchTencentQuote(input).then(function (quote) {
      if (!quote.price) throw new Error('无法获取行情数据，请检查股票代码');
      return Promise.all([
        Promise.resolve(quote),
        DS.fetchDividendRecords(input),
        DS.fetchFinancials(input),
        DS.fetchCashflow(input),
        DS.fetchIndustry(input).catch(function () { return null; }),
        /* #40: 情境红旗数据（被动高股息/输血式分红），失败传 null 降级，不阻塞主展示 */
        DS.fetchPriceChange1y(input),
        DS.fetchTop10Holding(input),
      ]);
    }).then(function (results) {
      return computeFromRaw({
        quote: results[0],
        dividend_rows: results[1],
        financial_rows: results[2],
        cashflow_rows: results[3],
        industry: results[4] || '未知行业',
        price_change_1y: results[5],
        top10_holding: results[6],
      });
    });
  }

  /* 纯计算: 对给定原始数据计算股息率 + 市赚率（与数据获取解耦，便于验证） */
  function computeFromRaw(raw) {
    var quote = raw.quote;
    var dividendRows = raw.dividend_rows || [];
    var financeRows = raw.financial_rows || [];
    var cashflowRows = raw.cashflow_rows || [];
    var industry = raw.industry || '未知行业';

    var totalShares = quote.total_shares || quote.a_shares || 0;

    var div = Calculator.parseDividendRecords(dividendRows, totalShares);
    var ttm = Calculator.computeTtmDividend(dividendRows, totalShares);
    var totalMarketCap = quote.price * totalShares;
    var yields = Calculator.calculateDividendYield(div.totalDividend, totalMarketCap);

    var fin = Calculator.parseFinancials(financeRows);

    var pr = Calculator.computePr({
      pe_ttm: quote.pe_ttm,
      pb: quote.pb,
      roe_latest: fin.roeLatest,
      net_profit_annual: fin.netProfitAnnual,
      dividend_total: div.totalDividend > 0 ? div.totalDividend : null,
    });

    var indClass = Calculator.classifyIndustry(industry);

    /* 亏损股提示：与 pr.py 一致，追加"该股为亏损股，市赚率不适用" */
    var prWarning = indClass.warning;
    if (pr.is_loss_stock) {
      prWarning = prWarning
        ? prWarning + '；该股为亏损股，市赚率不适用'
        : '该股为亏损股，市赚率不适用';
    }

    /* 股息可持续性：仅税前股息率 > 4% 时评估（对齐 Python analysis.py） */
    var sustainability = null;
    if (yields[0] > 4) {
      sustainability = Calculator.assessSustainability({
        dividend_yield_before_tax: yields[0],
        dividend_total: div.totalDividend > 0 ? div.totalDividend : null,
        latest: fin.sustainabilityFin,
        cashflow_rows: cashflowRows,
        history: div.sustainabilityHistory,
        industry: industry,
        price_change_1y: raw.price_change_1y != null ? raw.price_change_1y : null,
        top10_holding: raw.top10_holding != null ? raw.top10_holding : null,
      });
      sustainability.explanation = Calculator.explainSustainability(sustainability);
    }

    return {
      stock_info: {
        stock_code: quote.stock_code,
        stock_name: quote.name,
        current_price: quote.price,
        pe_ttm: quote.pe_ttm,
        pb: quote.pb,
        total_shares: totalShares,
      },
      dividend: {
        has_dividend: div.totalDividend > 0,
        total_dividend: div.totalDividend,
        dividend_year: div.year,
        dividend_details: div.details,
        explanation: div.explanation,
        dividend_yield_before_tax: yields[0],
        dividend_yield_after_tax_10: yields[1],
        dividend_yield_after_tax_20: yields[2],
        total_market_cap: totalMarketCap,
        dividend_source: '东财 datacenter',
        /* TTM 口径（#19）：近 12 个月实际派发，与主口径并行 */
        ttm_dividend: ttm.ttm_dividend,
        dividend_yield_ttm_before_tax: ttm.ttm_dividend != null && totalMarketCap > 0
          ? ttm.ttm_dividend / totalMarketCap * 100 : null,
        ttm_period: ttm.period,
        ttm_source: ttm.source,
      },
      pr: {
        pr_basic: pr.pr_basic,
        pr_corrected: pr.pr_corrected,
        pr_pb: pr.pr_pb,
        valuation_zone: pr.valuation_zone,
        pr_warning: prWarning,
        pe_ttm: quote.pe_ttm,
        pb: quote.pb,
        payout_ratio: pr.payout_ratio,
        n_factor: pr.n_factor,
        is_loss_stock: pr.is_loss_stock,
        roe_latest: fin.roeLatest,
        roe_5y_median: fin.roe5yMedian,
        net_profit_latest_period: fin.netProfitTtm,
        net_profit_annual: fin.netProfitAnnual,
        industry: industry,
      },
      sustainability: sustainability,
    };
  }

  return {
    normalizeStockCode: normalizeStockCode,
    analyzeStock: analyzeStock,
    computeFromRaw: computeFromRaw,
  };
}));
