/* 计算逻辑 JS 移植 — 对齐 src/pr_calculator.py / src/utils.py / src/dividend.py
 * 纯函数，无网络依赖，浏览器与 Node(verify) 共用。 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.Calculator = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var CYCLICAL_INDUSTRIES = [
    '煤炭', '钢铁', '有色金属', '石油', '化工', '航运', '建材',
    '水泥', '玻璃', '造纸', '养殖', '房地产', '工程机械', '船舶',
    '化肥', '农药', '化纤', '橡胶', '塑料',
    '证券', '券商', '保险',
  ];

  var TECH_INDUSTRIES = [
    '半导体', '软件', '互联网', '计算机', '通信', '电子',
    '芯片', '人工智能', '云计算', '大数据',
  ];

  function inferFiscalYear(year, month) {
    /* 3-8月除权 → 上年度年报；9-12月 → 当年中报；1-2月 → 上年度中报 */
    if (month >= 3 && month <= 8) {
      return { year: year - 1, isAnnual: true };
    } else if (month >= 9) {
      return { year: year, isAnnual: false };
    } else {
      return { year: year - 1, isAnnual: false };
    }
  }

  function reportTime(year, isAnnual) {
    return year + (isAnnual ? '年报' : '中报');
  }

  function calculateDividendYield(totalDividend, totalMarketCap) {
    if (totalMarketCap <= 0) return [0.0, 0.0, 0.0];
    var before = (totalDividend / totalMarketCap) * 100;
    return [before, before * 0.9, before * 0.8];
  }

  /* ── 分红解析（对齐 _parse_fhps_detail，输入为东财 RPT_SHAREBONUS_DET 行）──
   * 行字段: REPORT_DATE (YYYY-MM-DD ...), PRETAX_BONUS_RMB (每10股派息)
   * 返回: { totalDividend, year, details:[{report_time, dividend_per_10}], explanation }
   */
  function parseDividendRecords(rows, totalShares) {
    var yearly = {};
    var reportDate = /^(\d{4})-(\d{2})/;

    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      /* T5：仅保留已实施分红（含'实施'且不含'未实施'，排除预案/预披露/批准等）*/
      var progress = String(row.ASSIGN_PROGRESS || '');
      if (progress.indexOf('实施') === -1 || progress.indexOf('未实施') !== -1) continue;
      var dp10 = Number(row.PRETAX_BONUS_RMB);
      if (!(dp10 > 0)) continue;
      var m = reportDate.exec(row.REPORT_DATE || '');
      if (!m) continue;
      var y = parseInt(m[1], 10);
      var month = parseInt(m[2], 10);
      /* 与 Python 一致: 仅12月为年报（完整财年），3/6/9月为中期分配 */
      var isAnnual = (month === 12);
      var label = isAnnual ? (y + '年报') : (y + '中期分配');

      if (!yearly[y]) yearly[y] = { total: 0, hasAnnual: false, details: [] };
      yearly[y].total += dp10;
      yearly[y].hasAnnual = yearly[y].hasAnnual || isAnnual;
      yearly[y].details.push({ report_time: label, dividend_per_10: dp10 });
    }

    var years = Object.keys(yearly).map(Number).sort(function (a, b) { return b - a; });
    if (!years.length) return { totalDividend: 0, year: null, details: [], explanation: '无有效分红数据' };

    var target = null;
    for (var j = 0; j < years.length; j++) {
      if (yearly[years[j]].hasAnnual) { target = yearly[years[j]]; target.year = years[j]; break; }
    }
    if (!target) { target = yearly[years[0]]; target.year = years[0]; }

    var totalPer10 = target.total;
    var dps = totalPer10 / 10.0;
    var totalDividend = dps * totalShares;

    var list = target.details.map(function (d) {
      return d.report_time + ': 10派' + pyFloat(d.dividend_per_10) + '元';
    });
    var explanation = String(target.year) + '年度 ' + list.join('，') +
      '，合计10派' + totalPer10.toFixed(3) + '元(每股' + dps.toFixed(4) + '元)，' +
      '总股本' + (totalShares / 1e8).toFixed(2) + '亿股，' +
      '总分红' + (totalDividend / 1e8).toFixed(2) + '亿元';

    /* 可持续性用：连续分红年数 / 曾削减 / 历史均值（对齐 sustainability.aggregate_dividend_history） */
    var susHistory = _aggregateDividendHistory(yearly, String(target.year), totalShares);

    return {
      totalDividend: totalDividend, year: String(target.year),
      details: target.details, explanation: explanation,
      sustainabilityHistory: susHistory,
    };
  }

  /* TTM 股息率（#19）：近 12 个月（按除权除息日）实际派发现金分红。
   * 对齐 Python utils.compute_ttm_dividend。返回 {ttm_dividend, period, count, source} */
  function computeTtmDividend(rows, totalShares, asOfDate) {
    var now = asOfDate || new Date();
    var cutoff = new Date(now.getTime() - 365 * 24 * 3600 * 1000);
    var totalPer10 = 0.0, count = 0;
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      /* T5：仅保留已实施分红（对齐 parseDividendRecords），排除预案/预披露 */
      var progress = String(row.ASSIGN_PROGRESS || '');
      if (progress.indexOf('实施') === -1 || progress.indexOf('未实施') !== -1) continue;
      var dp10 = Number(row.PRETAX_BONUS_RMB);
      if (!(dp10 > 0)) continue;
      var ex = row.EX_DIVIDEND_DATE || row.ex_dividend_date;
      if (!ex) continue;
      var d = new Date(String(ex).slice(0, 10) + 'T00:00:00');
      if (isNaN(d.getTime())) continue;
      if (d > cutoff && d <= now) {
        totalPer10 += dp10;
        count++;
      }
    }
    if (!count) return { ttm_dividend: null, period: null, count: 0, source: '无' };
    var fmt = function (x) {
      return x.getFullYear() + '-' + String(x.getMonth() + 1).padStart(2, '0') + '-' + String(x.getDate()).padStart(2, '0');
    };
    return {
      ttm_dividend: totalPer10 / 10.0 * totalShares,
      period: fmt(cutoff) + '~' + fmt(now),
      count: count,
      source: '东财',
    };
  }

  /* 按财年聚合 → {consecutive_years, ever_cut, latest_year_amount, history_mean_amount} */
  function _aggregateDividendHistory(yearly, targetYear, totalShares) {
    var yearAmount = {};
    Object.keys(yearly).forEach(function (y) {
      yearAmount[y] = (yearly[y].total / 10.0) * totalShares;
    });
    /* 仅年报记录参与 ever_cut（#39）：yearly[y].details 含 {report_time, dividend_per_10}，
     * report_time 为 'YYYY年报'/'YYYY半年报'（注意 '半年报' 含 '年报' 子串，需双重判断）。
     * 半年报混入会掩盖相邻年降幅（如 2025 年报 8 元 + 半年报 3 元被当作 11 元）。 */
    var annualAmount = {};
    Object.keys(yearly).forEach(function (y) {
      var sum10 = 0;
      (yearly[y].details || []).forEach(function (d) {
        var t = String(d.report_time || '');
        if (t.indexOf('年报') !== -1 && t.indexOf('半年报') === -1) sum10 += Number(d.dividend_per_10) || 0;
      });
      annualAmount[y] = (sum10 / 10.0) * totalShares;
    });
    var yearsSorted = Object.keys(yearAmount).map(Number).sort(function (a, b) { return b - a; });
    if (!yearsSorted.length) {
      return { consecutive_years: 0, ever_cut: false, latest_year_amount: null, history_mean_amount: null };
    }
    /* 基准财年：优先传入的 targetYear（须在数据中），否则最新有分红年——对齐 Python target_year */
    var baseYear = (targetYear && yearAmount[targetYear] != null) ? parseInt(targetYear, 10) : yearsSorted[0];
    var tgt = baseYear;
    var consecutive = 0;
    while (yearAmount[String(tgt)] != null) { consecutive++; tgt--; }

    var historyYears = yearsSorted.map(String).filter(function (y) { return y !== String(targetYear); });
    var historyMean = null;
    if (historyYears.length) {
      var sum = 0;
      historyYears.forEach(function (y) { sum += yearAmount[y]; });
      historyMean = sum / historyYears.length;
    }
    /* 近3年均值（targetYear之前最近3年）——突击分红判断用，避免早期低基数拉偏（T3）*/
    var recent3 = historyYears.filter(function (y) { return parseInt(y, 10) < baseYear; }).slice(0, 3);
    var history3yMean = null;
    if (recent3.length) {
      var s3 = 0;
      recent3.forEach(function (y) { s3 += yearAmount[y]; });
      history3yMean = s3 / recent3.length;
    }
    /* 曾削减：近 CUT_WINDOW_YEARS 年窗口（含最新财年）内相邻年降幅 > 30%（对齐 Python aggregate_dividend_history）。
     * 仅年报金额参与比较（#39）。注意用 baseYear（最新财年，含 fallback）而非 tgt——tgt 在 consecutive 计数时已被递减 */
    var windowStart = baseYear - (SUS_CUT_WINDOW_YEARS - 1);
    var annualAsc = Object.keys(annualAmount).map(Number).sort(function (a, b) { return a - b; });
    var everCut = false;
    for (var i = 1; i < annualAsc.length; i++) {
      var prevY = annualAsc[i - 1], curY = annualAsc[i];
      if (curY < windowStart) continue; /* 仅检查窗口内相邻年 */
      var prev = annualAmount[String(prevY)], cur = annualAmount[String(curY)];
      if (prev > 0 && cur < prev * 0.7) { everCut = true; break; }
    }
    return {
      consecutive_years: consecutive, ever_cut: everCut,
      /* #35: 用 baseYear（最新有年报年，含 fallback，与 Python target_year 语义一致）而非最大年份 */
      latest_year_amount: yearAmount[String(baseYear)] || null,
      history_mean_amount: historyMean,
      history_3y_mean: history3yMean,
    };
  }

  /* ── 财务数据（对齐 pr.py _get_financial，输入为东财 RPT_F10_FINANCE_MAINFINADATA 行）
   * PARENTNETPROFIT 为累计值(YTD)，TTM 用最近报告期补齐上年同期。
   * 可持续性字段（NETCASH_OPERATE_PK/NETCASH_INVEST_PK/LIABILITY/TOTAL_ASSETS_PK/
   * DEBT_ASSET_RATIO/INTEREST_DEBT_RATIO/INTEREST_COVERAGE_RATIO/银行专项）在 columns=ALL 已含。 */
  function parseFinancials(rows) {
    if (!rows.length) return { roeLatest: null, roe5yMedian: null, roePeriod: null, netProfitTtm: null, netProfitAnnual: null, sustainabilityFin: null };

    /* 严格数值判断：空字符串/空白/null 视为缺失（Number('')===0 会污染中位数） */
    function toNum(v) {
      if (v == null || String(v).trim() === '') return NaN;
      return Number(v);
    }

    var annual = rows
      .filter(function (r) { return (r.REPORT_DATE || '').slice(5, 10) === '12-31'; })
      .filter(function (r) { return isFinite(toNum(r.ROEJQ)); })
      .map(function (r) {
        return { year: parseInt(r.REPORT_DATE.slice(0, 4), 10), roe: Number(r.ROEJQ) };
      })
      .sort(function (a, b) { return b.year - a.year; });

    var roeLatest = annual.length ? annual[0].roe : null;
    var roe5yMedian = null;
    if (annual.length) {
      var last5 = annual.slice(0, Math.min(5, annual.length)).map(function (a) { return a.roe; });
      var sorted = last5.slice().sort(function (a, b) { return a - b; });
      roe5yMedian = sorted[Math.floor(sorted.length / 2)];
    }

    var netProfitAnnual = null;
    if (annual.length) {
      var latestAnnual = annual[0];
      for (var i = 0; i < rows.length; i++) {
        var r = rows[i];
        if ((r.REPORT_DATE || '').slice(0, 10) === latestAnnual.year + '-12-31' && isFinite(toNum(r.PARENTNETPROFIT))) {
          netProfitAnnual = Number(r.PARENTNETPROFIT);
          break;
        }
      }
    }

    var netProfitTtm = null;
    var dated = rows
      .map(function (r) {
        var dt = (r.REPORT_DATE || '').slice(0, 10);
        return { date: dt, np: toNum(r.PARENTNETPROFIT) };
      })
      .filter(function (r) { return r.date.length === 10 && isFinite(r.np); })
      .sort(function (a, b) { return a.date < b.date ? -1 : 1; });

    if (dated.length) {
      var latest = dated[dated.length - 1];
      var latestYear = parseInt(latest.date.slice(0, 4), 10);
      var latestMonthDay = latest.date.slice(5);
      /* 上年完整财年 + 本年至今 − 上年同期 */
      var prevYear = null, prevSamePeriod = null;
      for (var k = dated.length - 1; k >= 0; k--) {
        var d = dated[k];
        if (d.date.slice(5) === '12-31' && d.date.slice(0, 4) !== String(latestYear)) { prevYear = d.np; break; }
      }
      if (latestMonthDay !== '12-31') {
        var targetPrev = (latestYear - 1) + '-' + latestMonthDay;
        for (var k2 = dated.length - 1; k2 >= 0; k2--) {
          if (dated[k2].date === targetPrev) { prevSamePeriod = dated[k2].np; break; }
        }
        if (prevYear != null && prevSamePeriod != null) {
          netProfitTtm = latest.np + prevYear - prevSamePeriod;
        }
      } else {
        netProfitTtm = latest.np;
      }
    }

    /* 可持续性财务：从年报行解析现金流/负债/银行专项（与 sustainability.py parse_financial_rows 同口径） */
    var sustainabilityFin = parseSustainabilityFin(rows, annual.length ? annual[0].year : null);

    return {
      roeLatest: roeLatest, roe5yMedian: roe5yMedian,
      roePeriod: annual.length ? annual[0].year : null,
      netProfitTtm: netProfitTtm, netProfitAnnual: netProfitAnnual,
      sustainabilityFin: sustainabilityFin,
    };
  }

  /* 东财财务行 → 可持续性年报数据（仅 12-31 年报，对齐 Python _FIELD_MAP） */
  function parseSustainabilityFin(rows, targetYear) {
    function num(v) {
      if (v == null || String(v).trim() === '') return null;
      var n = Number(v);
      return isFinite(n) ? n : null;
    }
    /* 年报行降序 */
    var annualRows = rows
      .filter(function (r) { return (r.REPORT_DATE || '').slice(5, 10) === '12-31' })
      .sort(function (a, b) { return (a.REPORT_DATE < b.REPORT_DATE) ? 1 : -1; });

    var latestRow = null;
    if (targetYear != null) {
      for (var i = 0; i < annualRows.length; i++) {
        if (parseInt((annualRows[i].REPORT_DATE || '').slice(0, 4), 10) === targetYear) { latestRow = annualRows[i]; break; }
      }
    }
    if (!latestRow) latestRow = annualRows[0] || null;
    if (!latestRow) return null;

    return {
      year: parseInt((latestRow.REPORT_DATE || '').slice(0, 4), 10),
      net_profit: num(latestRow.PARENTNETPROFIT),
      net_profit_yoy: num(latestRow.PARENTNETPROFITTZ),
      operating_cf: num(latestRow.NETCASH_OPERATE_PK),
      investing_cf: num(latestRow.NETCASH_INVEST_PK),
      capex: null,  // MAINFINADATA 无 CAPEX，由编排层从现金流量表合并（mergeCapex）
      total_assets: num(latestRow.TOTAL_ASSETS_PK),
      total_liabilities: num(latestRow.LIABILITY),
      debt_ratio: num(latestRow.DEBT_ASSET_RATIO),
      interest_debt_ratio: num(latestRow.INTEREST_DEBT_RATIO),
      interest_coverage: num(latestRow.INTEREST_COVERAGE_RATIO),
      roe: num(latestRow.ROEJQ),
      capital_adequacy_ratio: num(latestRow.NEWCAPITALADER),   // 总资本充足率（实地验证，非ADEQUACY_RATIO虚构字段）
      net_interest_margin: num(latestRow.NET_INTEREST_MARGIN),
      npl_ratio: num(latestRow.NONPERLOAN),                    // 不良率%（非NON_PERFORMING_LOAN余额）
      provision_coverage: num(latestRow.LOAN_PROVISION_RATIO), // 拨贷比%（非RISK_COVERAGE恒空）
    };
  }

  /* 把现金流量表（RPT_F10_FINANCE_GCASHFLOW）的资本开支合并进 sustainabilityFin.capex。
   * 对齐 Python sustainability.merge_capex。CONSTRUCT_LONG_ASSET 为正数（购建固定资产支出）。 */
  function mergeCapex(fin, cashflowRows) {
    if (!fin || !cashflowRows || !cashflowRows.length) return fin;
    function num(v) {
      if (v == null || String(v).trim() === '') return null;
      var n = Number(v);
      return isFinite(n) ? n : null;
    }
    var capexByYear = {};
    for (var i = 0; i < cashflowRows.length; i++) {
      var dateStr = String((cashflowRows[i].REPORT_DATE || '')).slice(0, 10);
      if (dateStr.length < 10 || dateStr.slice(5, 10) !== '12-31') continue;
      var year = parseInt(dateStr.slice(0, 4), 10);
      var v = num(cashflowRows[i].CONSTRUCT_LONG_ASSET);
      if (v == null) continue;
      capexByYear[year] = (capexByYear[year] || 0) + v;
    }
    if (capexByYear[fin.year] != null) fin.capex = capexByYear[fin.year];
    return fin;
  }

  /* ── 市赚率（对齐 pr_calculator.py）── */
  function computeBasicPR(peTtm, roe) {
    if (peTtm == null || roe == null || roe <= 0) return null;
    return round2(peTtm / roe);
  }

  function computeCorrectedPR(peTtm, roe, nFactor) {
    if (peTtm == null || roe == null || roe <= 0 || nFactor == null) return null;
    return round2(nFactor * peTtm / roe);
  }

  function computePbPR(pb, roe) {
    if (pb == null || roe == null || roe <= 0) return null;
    var roeDecimal = roe / 100.0;
    return round2(pb / (roeDecimal * roeDecimal) / 100.0);
  }

  function computeNFactor(payoutRatio) {
    if (payoutRatio == null) return null;
    if (payoutRatio <= 0) return 2.0;
    var raw = 0.50 / payoutRatio;
    return Math.max(1.0, Math.min(2.0, raw));
  }

  function classifyValuation(pr) {
    // 阈值基于 PR 历史回测（2016-2024 沪深300，见 docs/BACKTEST_REPORT.md）：
    // 超额集中在 PR 1~3，PR>3 显著跑输，PR<1 无超额。与 Python classify_valuation 逐字一致。
    if (pr == null) return '无法判定';
    if (pr <= 0.5) return '低估';
    if (pr <= 1.0) return '合理偏低';
    if (pr <= 3.0) return '合理';
    return '高估';
  }

  function classifyIndustry(industry) {
    var isCyclical = false, isTech = false;
    for (var i = 0; i < CYCLICAL_INDUSTRIES.length; i++) {
      if (industry.indexOf(CYCLICAL_INDUSTRIES[i]) !== -1) { isCyclical = true; break; }
    }
    for (var j = 0; j < TECH_INDUSTRIES.length; j++) {
      if (industry.indexOf(TECH_INDUSTRIES[j]) !== -1) { isTech = true; break; }
    }
    var warning = '';
    if (isCyclical) {
      warning = '该股属于周期行业，修正市赚率仅供参考；建议优先参考PB-市赚率';
    } else if (isTech) {
      warning = '该股属于科技行业，修正市赚率可能不适用（科技股常以回购代替分红）';
    }
    return { isCyclical: isCyclical, isTech: isTech, warning: warning };
  }

  /* 综合市赚率（对齐 pr.py calculate_pr 的核心计算段） */
  function computePr(input) {
    var pe = input.pe_ttm, pb = input.pb, roe = input.roe_latest;
    var netProfitAnnual = input.net_profit_annual, dividendTotal = input.dividend_total;
    var roePeriod = input.roe_period;

    var isLossStock = netProfitAnnual != null && netProfitAnnual <= 0;

    var payoutRatio = null, nFactor = null;
    if (netProfitAnnual != null && netProfitAnnual > 0 && dividendTotal != null) {
      payoutRatio = dividendTotal / netProfitAnnual;
      nFactor = computeNFactor(payoutRatio);
    }

    var prBasic = null, prCorrected = null, prPb = null;
    var valuationZone = '无法判定';
    if (!isLossStock && pe != null && roe != null && roe > 0) {
      // 周期股 PB-市赚率用 5 年 ROE 中位数（对齐 Python：周期股单年 ROE 失真）
      var roeForPb = (input.is_cyclical && input.roe_5y_median != null) ? input.roe_5y_median : roe;
      prBasic = computeBasicPR(pe, roe);
      prCorrected = computeCorrectedPR(pe, roe, nFactor);
      prPb = computePbPR(pb, roeForPb);
      valuationZone = classifyValuation(prCorrected != null ? prCorrected : prBasic);
    }

    return {
      pr_basic: prBasic,
      pr_corrected: prCorrected,
      pr_pb: prPb,
      valuation_zone: valuationZone,
      roe_period: roePeriod != null ? String(roePeriod) + '年报' : null,
      payout_ratio: payoutRatio,
      n_factor: nFactor,
      is_loss_stock: isLossStock,
    };
  }

  /* ════════════════════════════════════════════════════════════
   * 股息可持续性（对齐 src/sustainability_calculator.py 分层级联模型）
   * Layer 0 行业路由 → Layer 1 致命红旗 → Layer 2 加权评分 → Layer 3 情境红旗
   * ════════════════════════════════════════════════════════════ */

  var SUS_THRESHOLD_YIELD = 4.0;
  var SUS_SCORE_SUSTAINABLE = 1.5;
  var SUS_SCORE_WEAK = 1.0;
  var SUS_FATAL_CF_COV = 1.0;
  var SUS_FATAL_BANK_CAR = 10.5;   // 银行总资本充足率致命红线（监管约束）
  /* 六维阈值与权重（对齐 Python） */
  var SUS_DIM = {
    cf_coverage: [1.0, 1.5], payout: [0.60, 0.80], roe: [10.0, 15.0],
    debt_ratio: [0.50, 0.70], interest_coverage: [3.0, 5.0], consecutive_years: [3, 10],
  };
  /* 曾削减判定窗口（年）：仅考察最新财年往前 SUS_CUT_WINDOW_YEARS 年内的相邻年降幅（对齐 Python CUT_WINDOW_YEARS） */
  var SUS_CUT_WINDOW_YEARS = 10;
  var SUS_WEIGHTS = {
    cf_coverage: 0.25, payout: 0.20, profitability: 0.15, balance_sheet: 0.15,
    dividend_history: 0.15, industry: 0.10,
  };
  var SUS_WARN = { payout_over_100: 1.0, price_drop: -0.30, special_div: 2.0, holding: 0.50, high_payout: 0.80 };
  var FINANCE_INDUSTRIES = ['银行', '保险'];
  var DEFENSIVE_INDUSTRIES = [
    '公用事业', '电力', '水务', '燃气', '高速公路', '铁路', '港口', '机场',
    '食品饮料', '白酒', '乳品', '家电', '医药', '超市', '运营商', '电信',
  ];

  function _susNum(v) {
    if (v == null || String(v).trim() === '') return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }

  function computeFreeCashFlow(operatingCf, investingCf, capex) {
    // FCF = 经营CF − 资本开支(CAPEX)。有 CAPEX（现金流量表 CONSTRUCT_LONG_ASSET）时用它
    // （正确口径）；无则降级 经营CF + investing_cf（含金融投资，低估 FCF，仅兜底）
    if (operatingCf == null) return null;
    if (capex != null) return operatingCf - capex;
    if (investingCf != null) return operatingCf + investingCf;
    return null;
  }
  function computeCfCoverage(operatingCf, dividendTotal) {
    if (operatingCf == null || !dividendTotal || dividendTotal <= 0) return null;
    return operatingCf / dividendTotal;
  }
  function computeFcfCoverage(fcf, dividendTotal) {
    if (fcf == null || !dividendTotal || dividendTotal <= 0) return null;
    return fcf / dividendTotal;
  }
  function computePayoutRatio(dividendTotal, netProfit) {
    if (!dividendTotal || netProfit == null || netProfit <= 0) return null;
    return dividendTotal / netProfit;
  }
  function _debtRatioDecimal(fin) {
    if (fin.debt_ratio != null) return fin.debt_ratio / 100.0;
    if (fin.total_assets && fin.total_liabilities != null && fin.total_assets > 0) {
      return fin.total_liabilities / fin.total_assets;
    }
    return null;
  }

  /* Layer 1：致命红旗。银行/保险（isBank）短路现金流类红旗（语义失真）。 */
  function checkFatalFlags(payoutRatio, fcfCoverage, operatingCf, netProfit, dividendTotal, isBank) {
    var flags = [];
    var hasDiv = !!(dividendTotal && dividendTotal > 0);
    // 支付率>100% 移至 Layer 3 情境红旗（T2）
    if (!isBank) {
      if (fcfCoverage != null && fcfCoverage < SUS_FATAL_CF_COV) {
        flags.push('自由现金流覆盖 ' + fcfCoverage.toFixed(2) + 'x < 1.0x，分红金额超过自由现金流');
      }
      if (hasDiv && operatingCf != null && operatingCf < 0) flags.push('经营现金流为负却仍派发现金分红');
    }
    if (hasDiv && netProfit != null && netProfit < 0) flags.push('净利润为负（亏损）却仍派发现金分红');
    return flags;
  }

  function _scoreBand(v, lo, hi) { return v < lo ? 0 : (v < hi ? 1 : 2); }
  function _scoreInverted(v, lo, hi) { return v > hi ? 0 : (v > lo ? 1 : 2); }

  /* Layer 2：六维评分 */
  function scoreDimensions(fin, history, payoutRatio, cfCoverage, isCyclical, isDefensive) {
    var s = {};
    s.cf_coverage = (cfCoverage != null) ? _scoreBand(cfCoverage, SUS_DIM.cf_coverage[0], SUS_DIM.cf_coverage[1]) : null;
    s.payout = (payoutRatio != null) ? _scoreInverted(payoutRatio, SUS_DIM.payout[0], SUS_DIM.payout[1]) : null;
    /* 盈利稳定性 */
    var profitScore = null;
    if (fin.roe != null) {
      profitScore = fin.roe < SUS_DIM.roe[0] ? 0 : (fin.roe < SUS_DIM.roe[1] ? 1 : 2);
      if (fin.net_profit_yoy != null && fin.net_profit_yoy < 0 && profitScore > 0) profitScore -= 1;
    }
    s.profitability = profitScore;
    /* 资产负债表（负债率 + 利息覆盖取较低） */
    var debtDec = _debtRatioDecimal(fin);
    var debtScore = (debtDec != null) ? _scoreInverted(debtDec, SUS_DIM.debt_ratio[0], SUS_DIM.debt_ratio[1]) : null;
    var intScore = (fin.interest_coverage != null) ? _scoreBand(fin.interest_coverage, SUS_DIM.interest_coverage[0], SUS_DIM.interest_coverage[1]) : null;
    var bsCands = [debtScore, intScore].filter(function (x) { return x != null; });
    s.balance_sheet = bsCands.length ? Math.min.apply(null, bsCands) : null;
    /* 分红历史 */
    if (history.ever_cut) s.dividend_history = 0;
    else if (history.consecutive_years >= SUS_DIM.consecutive_years[1]) s.dividend_history = 2;
    else if (history.consecutive_years >= SUS_DIM.consecutive_years[0]) s.dividend_history = 1;
    else s.dividend_history = 0;
    /* 行业属性 */
    s.industry = isCyclical ? 0 : (isDefensive ? 2 : 1);
    return s;
  }

  /* Layer 2'：金融分支（银行专项） */
  function scoreFinanceBranch(fin) {
    var s = {};
    if (fin.capital_adequacy_ratio != null) {
      var car = fin.capital_adequacy_ratio;
      s.capital_adequacy = car >= 12 ? 2 : (car >= 10.5 ? 1 : 0);
    }
    if (fin.net_interest_margin != null) {
      var nim = fin.net_interest_margin;
      s.net_interest_margin = nim >= 1.8 ? 2 : (nim >= 1.4 ? 1 : 0);
    }
    if (fin.npl_ratio != null) {
      var npl = fin.npl_ratio;
      s.npl = npl < 1.0 ? 2 : (npl < 2.0 ? 1 : 0);
    }
    // 拨贷比（LOAN_PROVISION_RATIO，监管要求1.5-2.5%；东财无拨备覆盖率字段，用拨贷比近似）
    if (fin.provision_coverage != null) {
      var pc = fin.provision_coverage;
      s.provision = pc >= 2.5 ? 2 : (pc >= 2.0 ? 1 : 0);
    }
    return s;
  }

  /* 返回 [score, missingRatio]：缺失维度计 0 分（不归一化分摊，T4）*/
  function _weightedScore(scores, weights) {
    var keys = Object.keys(scores);
    var totalW = 0, weighted = 0, missingW = 0;
    keys.forEach(function (k) {
      totalW += weights[k] || 0;
      if (scores[k] == null) { missingW += weights[k] || 0; }
      else { weighted += scores[k] * (weights[k] || 0); }
    });
    if (totalW <= 0) return [null, 1.0];
    return [weighted / totalW, missingW / totalW];
  }

  /* Layer 3：情境红旗 */
  function checkWarningFlags(fin, history, isCyclical, priceChange1y, top10Holding, payoutRatio, debtDec, cfCoverage, isBank) {
    var flags = [];
    /* 支付率>100%（T2：单年不否决，仅警示；成熟期/高折旧股结构性偏高属健康）*/
    if (payoutRatio != null && payoutRatio > SUS_WARN.payout_over_100) {
      flags.push('股利支付率 ' + (payoutRatio * 100).toFixed(1) + '% > 100%，分红超过当年净利润（成熟期/高折旧股常见，关注是否动用留存收益）');
    }
    if (priceChange1y != null && priceChange1y < SUS_WARN.price_drop) {
      flags.push('近1年股价跌幅 ' + (priceChange1y * 100).toFixed(1) + '%，高股息率可能源于股价下跌（分母效应）');
    }
    /* 特别/突击分红：当年分红远超近3年均值（T3：避免早期低基数拉偏误判稳定增长股）*/
    var historyBaseline = (history.history_3y_mean != null) ? history.history_3y_mean : history.history_mean_amount;
    if (history.latest_year_amount && historyBaseline && historyBaseline > 0
        && history.latest_year_amount > historyBaseline * SUS_WARN.special_div) {
      flags.push('最新财年分红 ' + (history.latest_year_amount / 1e8).toFixed(2) + '亿元 远超近3年均值 '
        + (historyBaseline / 1e8).toFixed(2) + '亿元，疑似特别/突击分红');
    }
    if (top10Holding != null && top10Holding > SUS_WARN.holding
        && payoutRatio != null && payoutRatio > SUS_WARN.high_payout) {
      flags.push('前十大持股 ' + (top10Holding * 100).toFixed(1) + '% 且支付率 ' + (payoutRatio * 100).toFixed(1)
        + '%，疑似向大股东输血式分红');
    }
    if (isCyclical && fin.net_profit_yoy != null && fin.net_profit_yoy < 0
        && payoutRatio != null && payoutRatio > SUS_WARN.high_payout) {
      flags.push('属周期行业且净利润同比 ' + fin.net_profit_yoy.toFixed(1) + '% 已拐头，支付率仍 '
        + (payoutRatio * 100).toFixed(1) + '%，警惕周期顶点高分红陷阱');
    }
    /* 证监会红线画像：高负债+弱现金流+高派息（银行跳过——负债率对银行无意义，T7）*/
    if (!isBank) {
      var weakCf = cfCoverage != null && cfCoverage < SUS_DIM.cf_coverage[1];
      var highDebt = debtDec != null && debtDec > SUS_DIM.debt_ratio[1];
      var highPayout = payoutRatio != null && payoutRatio > SUS_WARN.high_payout;
      if (weakCf && highDebt && highPayout) {
        flags.push("高负债 + 弱现金流覆盖 + 高派息，符合监管重点关注的'透支式分红'画像");
      }
    }
    return flags;
  }

  function _classifySustainabilityIndustry(industry) {
    var isBank = FINANCE_INDUSTRIES.some(function (kw) { return industry.indexOf(kw) !== -1; });
    var isCyclical = CYCLICAL_INDUSTRIES.some(function (kw) { return industry.indexOf(kw) !== -1; });
    var isDefensive = DEFENSIVE_INDUSTRIES.some(function (kw) { return industry.indexOf(kw) !== -1; });
    return { isBank: isBank, isCyclical: isCyclical, isDefensive: isDefensive };
  }

  /* Layer 2 分支评分（对齐 Python _score_by_branch）：返回 {dimScores, score, missingRatio} */
  function _scoreByBranch(fin, history, payoutRatio, cfCoverage, isBank, isCyclical, isDefensive) {
    if (isBank) {
      var dimScores = scoreFinanceBranch(fin);
      if (Object.keys(dimScores).length) {
        var vals = Object.keys(dimScores).map(function (k) { return dimScores[k]; });
        var score = vals.reduce(function (a, b) { return a + b; }, 0) / vals.length;
        return { dimScores: dimScores, score: score, missingRatio: 0.0 };
      }
      // 银行专项全缺失 → 降级通用（note/branch 由调用方处理）
      dimScores = scoreDimensions(fin, history, payoutRatio, cfCoverage, isCyclical, isDefensive);
      var r1 = _weightedScore(dimScores, SUS_WEIGHTS);
      return { dimScores: dimScores, score: r1[0], missingRatio: r1[1] };
    }
    dimScores = scoreDimensions(fin, history, payoutRatio, cfCoverage, isCyclical, isDefensive);
    var r2 = _weightedScore(dimScores, SUS_WEIGHTS);
    return { dimScores: dimScores, score: r2[0], missingRatio: r2[1] };
  }

  /* 三档结论映射 + 情境红旗降档（对齐 Python _verdict_from_score） */
  function _verdictFromScore(score, warningFlags) {
    var verdict = score == null ? '偏弱'
      : (score >= SUS_SCORE_SUSTAINABLE ? '可持续' : (score >= SUS_SCORE_WEAK ? '偏弱' : '不可持续'));
    if (warningFlags.length) {
      if (verdict === '可持续') verdict = '偏弱';
      else if (verdict === '偏弱') verdict = '不可持续';
    }
    return verdict;
  }

  /* 主入口：assessSustainability（对齐 Python assess_sustainability） */
  function assessSustainability(opts) {
    var yld = opts.dividend_yield_before_tax;
    if (yld == null || yld <= SUS_THRESHOLD_YIELD) {
      return { triggered: false, verdict: '未评估', score: null, fatal_flags: [], warning_flags: [],
               dimension_scores: {}, metrics: {}, branch: 'general', notes: ['股息率未超过阈值，未做可持续性评估'] };
    }
    var result = { triggered: true, verdict: '不可持续', score: null,
                   fatal_flags: [], warning_flags: [], dimension_scores: {}, metrics: {}, branch: 'general', notes: [] };

    var cls = _classifySustainabilityIndustry(opts.industry || '');
    result.branch = cls.isBank ? 'finance' : 'general';
    result.metrics.is_bank = cls.isBank ? 1.0 : 0.0;
    result.metrics.is_cyclical = cls.isCyclical ? 1.0 : 0.0;

    var fin = opts.latest;  // sustainabilityFin 结构（来自 parseSustainabilityFin）
    if (!fin) {
      result.notes.push('财务数据缺失，无法评估可持续性');
      result.fatal_flags.push('缺少财务数据，无法判断分红是否可持续');
      return result;
    }

    /* 数据新鲜度判定（#13）：标注而非改判，对齐 Python _staleness_note */
    result.latest_annual_year = fin.year != null ? fin.year : null;
    if (result.latest_annual_year != null) {
      var nowYear = new Date().getFullYear();
      if (result.latest_annual_year < nowYear - 1) {
        result.notes.push('财务数据截至 ' + result.latest_annual_year + ' 年报，已超过 1 年未更新，结论时效性有限');
      }
    }

    var dividendTotal = opts.dividend_total;
    // 合并现金流量表资本开支（修正 FCF 口径）
    if (opts.cashflow_rows) mergeCapex(fin, opts.cashflow_rows);
    var payoutRatio = computePayoutRatio(dividendTotal, fin.net_profit);
    var fcf = computeFreeCashFlow(fin.operating_cf, fin.investing_cf, fin.capex);
    var fcfCoverage = computeFcfCoverage(fcf, dividendTotal);
    var cfCoverage = computeCfCoverage(fin.operating_cf, dividendTotal);
    var debtDec = _debtRatioDecimal(fin);

    result.metrics.payout_ratio = payoutRatio;
    result.metrics.operating_cf = fin.operating_cf;
    result.metrics.capex = fin.capex;
    result.metrics.free_cash_flow = fcf;
    result.metrics.fcf_coverage = fcfCoverage;
    result.metrics.cf_coverage = cfCoverage;
    result.metrics.debt_ratio = _debtRatioDecimal(fin);
    result.metrics.interest_coverage = fin.interest_coverage;
    result.metrics.roe_latest = fin.roe;
    result.metrics.net_profit = fin.net_profit;
    result.metrics.net_profit_yoy = fin.net_profit_yoy;
    result.metrics.capital_adequacy = fin.capital_adequacy_ratio;
    result.metrics.net_interest_margin = fin.net_interest_margin;
    result.metrics.npl_ratio = fin.npl_ratio;
    result.metrics.provision_coverage = fin.provision_coverage;

    /* 分红历史缺失补默认 */
    var history = opts.history || { consecutive_years: 0, ever_cut: false,
                                    latest_year_amount: dividendTotal, history_mean_amount: null };
    if (!opts.history) result.notes.push('分红历史缺失，分红历史维度按 0 分计');

    /* Layer 2：分支评分（先算，供展示；红旗否决时也有维度分）*/
    var scored = _scoreByBranch(fin, history, payoutRatio, cfCoverage, cls.isBank, cls.isCyclical, cls.isDefensive);
    var dimScores = scored.dimScores, score = scored.score;
    var isFallback = false;
    // 银行走金融分支但专项全缺失时 _scoreByBranch 内部已降级通用；此处补记 note/branch
    if (cls.isBank && !(('capital_adequacy' in dimScores) || ('npl' in dimScores))) {
      result.notes.push('银行专项指标（资本充足率/净息差/不良率）缺失，按通用指标评估');
      result.branch = 'general-fallback';
      isFallback = true;
      // T7：银行 fallback 时屏蔽资产负债表维度（银行天然 90%+，通用阈值必踩坑）
      dimScores.balance_sheet = null;
      var reScored = _weightedScore(dimScores, SUS_WEIGHTS);
      score = reScored[0]; scored.missingRatio = reScored[1];
    }
    // T4：数据缺失惩罚——缺失权重 ≥ 30% 标低置信（score 已含缺失维度计 0 分）
    if (scored.missingRatio >= 0.30 && score != null) {
      result.notes.push('财务数据缺失较多（' + (scored.missingRatio * 100).toFixed(0) + '%），结论置信度偏低');
      result.metrics.missing_weight_ratio = scored.missingRatio;
    }
    Object.keys(dimScores).forEach(function (k) { if (dimScores[k] == null) dimScores[k] = 0; });
    result.dimension_scores = dimScores;
    result.metrics.consecutive_dividend_years = history.consecutive_years;
    result.metrics.ever_cut = history.ever_cut ? 1 : 0;

    /* Layer 1：致命红旗（维度分已算好，否决时仍展示）*/
    result.fatal_flags = checkFatalFlags(payoutRatio, fcfCoverage, fin.operating_cf, fin.net_profit, dividendTotal, cls.isBank);
    /* 银行/保险：资本充足率 < 10.5% 是监管约束分红的硬红线，单列致命否决 */
    if (cls.isBank && fin.capital_adequacy_ratio != null && fin.capital_adequacy_ratio < SUS_FATAL_BANK_CAR) {
      result.fatal_flags.push('资本充足率 ' + fin.capital_adequacy_ratio.toFixed(2) + '% < 10.5%，触及监管约束，分红受限');
    }
    if (result.fatal_flags.length) {
      result.score = 0.0;
      result.score_100 = 0.0;  /* 致命红旗 → 0 分（对齐 Python #20） */
      return result;
    }

    if (score == null) {
      result.notes.push('有效评分维度不足，结论仅供参考');
      result.score = null;
      result.verdict = '偏弱';
      return result;
    }
    result.score = Math.round(score * 1000) / 1000;
    /* 0-2 映射 0-100（×50，阈值1.5/1.0→75/50），对齐 Python _score_to_100。
     * #36: 基于已舍入的 result.score 计算（Python 为 round(score,3) 后 _score_to_100），
     * 用未舍入 score 会差 0.1（score=1.2346 → Python 61.8 / 旧 JS 61.7） */
    result.score_100 = Math.round(result.score * 50 * 10) / 10;

    /* Layer 3：情境红旗 → 降一档 */
    result.warning_flags = checkWarningFlags(fin, history, cls.isCyclical,
      opts.price_change_1y, opts.top10_holding, payoutRatio, debtDec, cfCoverage, cls.isBank);
    result.verdict = _verdictFromScore(score, result.warning_flags);
    return result;
  }

  /* ── 结论说明（对齐 Python explain_sustainability，双端逐字一致） ── */
  function _r1(v) { return Math.round(v * 10) / 10; }
  function _r2(v) { return Math.round(v * 100) / 100; }
  /* 小数→百分数 1 位小数 */
  function _pct1(v) { return _r1(v * 100).toFixed(1); }
  /* 净利润同比带符号 */
  function _yoyStr(v) {
    return (v >= 0 ? '+' : '') + _r1(v).toFixed(1) + '%';
  }

  var SUS_EXPLAIN_DIMS = ['cf_coverage', 'payout', 'profitability', 'balance_sheet', 'dividend_history', 'industry'];
  var SUS_EXPLAIN_DIMS_FIN = ['capital_adequacy', 'net_interest_margin', 'npl', 'provision'];

  function _weakDimText(k, s, m) {
    switch (k) {
      case 'cf_coverage':
        if (m.cf_coverage == null) return null;
        return '现金流覆盖 ' + _r2(m.cf_coverage).toFixed(2) + ' 倍' +
          (s === 0 ? '，分红花的钱超过真正赚到的现金，可能吃老本' : '，刚好够分红，余粮不多');
      case 'payout':
        if (m.payout_ratio == null) return null;
        return '股利支付率 ' + _pct1(m.payout_ratio) + '%' +
          (s === 0 ? '，利润几乎全拿去分红了' : '，分红比例偏高');
      case 'profitability':
        if (m.roe_latest == null) return null;
        var yoy = m.net_profit_yoy;
        var roe = m.roe_latest;
        /* 0 分可能来自 ROE 过低（<10%）、利润同比下滑、或两者兼有；文案按真实成因区分 */
        if (s === 0) {
          if (yoy != null && yoy < 0) {
            return '盈利稳定性：ROE ' + _r2(roe).toFixed(2) + '%、净利润同比 ' + _yoyStr(yoy) + '，盈利在下滑，分红难持续';
          }
          return '盈利稳定性：ROE ' + _r2(roe).toFixed(2) + '%（偏低）、净利润同比 ' + _yoyStr(yoy == null ? 0 : yoy) + '，盈利基础薄弱，分红承压';
        }
        return '盈利稳定性：ROE ' + _r2(roe).toFixed(2) + '%、净利润同比 ' + _yoyStr(yoy == null ? 0 : yoy) + '，盈利一般';
      case 'balance_sheet':
        if (m.debt_ratio == null) return null;
        return '资产负债率 ' + _pct1(m.debt_ratio) + '%' +
          (s === 0 ? '，负债偏高，财务压力大' : '，负债水平一般');
      case 'dividend_history':
        if (m.consecutive_dividend_years == null) return null;
        /* 0 分可能来自"近10年内曾削减"或"连续年数过短"，文案按原因区分（s===0 时） */
        if (s === 0) {
          if (m.ever_cut) {
            return '连续分红 ' + parseInt(m.consecutive_dividend_years) + ' 年，但近 10 年内曾削减分红，历史稳定性存疑';
          }
          return '连续分红仅 ' + parseInt(m.consecutive_dividend_years) + ' 年，历史较短';
        }
        return '连续分红 ' + parseInt(m.consecutive_dividend_years) + ' 年，尚不算长期稳定';
      case 'industry':
        if (s === 0) return '属强周期行业，盈利随景气波动大，高分红难年年保证';
        return null; // 中性行业不赘述
      case 'capital_adequacy':
        if (m.capital_adequacy == null) return null;
        return '资本充足率 ' + _r2(m.capital_adequacy).toFixed(2) + '%' +
          (s === 0 ? '，低于监管红线，分红受限' : '，一般');
      case 'net_interest_margin':
        if (m.net_interest_margin == null) return null;
        return '净息差 ' + _r2(m.net_interest_margin).toFixed(2) + '%' +
          (s === 0 ? '，盈利承压' : '，一般');
      case 'npl':
        if (m.npl_ratio == null) return null;
        return '不良贷款率 ' + _r2(m.npl_ratio).toFixed(2) + '%' +
          (s === 0 ? '，资产质量堪忧' : '，偏高');
      case 'provision':
        if (m.provision_coverage == null) return null;
        return '拨贷比 ' + _r2(m.provision_coverage).toFixed(2) + '%' +
          (s === 0 ? '，风险缓冲不足' : '，一般');
      default:
        return null;
    }
  }

  function _strongDimText(k, m) {
    switch (k) {
      case 'cf_coverage':
        if (m.cf_coverage == null) return null;
        return '现金流覆盖 ' + _r2(m.cf_coverage).toFixed(2) + ' 倍（充裕）';
      case 'payout':
        if (m.payout_ratio == null) return null;
        return '支付率 ' + _pct1(m.payout_ratio) + '%（健康）';
      case 'profitability':
        if (m.roe_latest == null) return null;
        return '盈利稳健（ROE ' + _r2(m.roe_latest).toFixed(2) + '%、净利润同比 ' + _yoyStr(m.net_profit_yoy == null ? 0 : m.net_profit_yoy) + '%）';
      case 'balance_sheet':
        if (m.debt_ratio == null) return null;
        return '资产负债率 ' + _pct1(m.debt_ratio) + '%（稳健）';
      case 'dividend_history':
        if (m.consecutive_dividend_years == null) return null;
        return '连续分红 ' + parseInt(m.consecutive_dividend_years) + ' 年（稳定）';
      case 'industry':
        return '属防御/成熟行业（盈利稳定）';
      case 'capital_adequacy':
        if (m.capital_adequacy == null) return null;
        return '资本充足率 ' + _r2(m.capital_adequacy).toFixed(2) + '%（充足）';
      case 'net_interest_margin':
        if (m.net_interest_margin == null) return null;
        return '净息差 ' + _r2(m.net_interest_margin).toFixed(2) + '%（健康）';
      case 'npl':
        if (m.npl_ratio == null) return null;
        return '不良贷款率 ' + _r2(m.npl_ratio).toFixed(2) + '%（很低）';
      case 'provision':
        if (m.provision_coverage == null) return null;
        return '拨贷比 ' + _r2(m.provision_coverage).toFixed(2) + '%（充足）';
      default:
        return null;
    }
  }

  /* 可持续性结论白话说明：首行结论+一句话总结，随后分条理由
   * （致命红旗 → 警示红旗 → 弱维度 → 优势项），末尾缺失数据说明。
   * 未触发 / 未评估时返回空数组。 */
  function explainSustainability(sus) {
    if (!sus || !sus.triggered || sus.verdict === '未评估') return [];
    var lines = [];
    var head;
    if (sus.verdict === '不可持续') {
      head = (sus.fatal_flags && sus.fatal_flags.length)
        ? '存在致命问题，当前分红水平大概率维持不下去'
        : '分红金额与盈利/现金流明显不匹配，长期难以为继';
    } else if (sus.verdict === '偏弱') {
      head = '分红有一定基础，但存在隐忧，长期分红能力可能打折扣';
    } else {
      head = (sus.branch === 'finance')
        ? '银行核心经营指标全部健康，分红能力扎实'
        : '盈利与现金流足以支撑当前分红';
    }
    lines.push('结论：' + sus.verdict + ' — ' + head);

    var n = 1;
    var m = sus.metrics || {};
    (sus.fatal_flags || []).slice(0, 3).forEach(function (f) { lines.push((n++) + '. ' + f); });

    if (!(sus.fatal_flags || []).length) {
      (sus.warning_flags || []).slice(0, 3).forEach(function (w) { lines.push((n++) + '. ' + w); });

      var order = (sus.branch === 'finance') ? SUS_EXPLAIN_DIMS_FIN : SUS_EXPLAIN_DIMS;
      var weak = [], strong = [];
      order.forEach(function (k) {
        var s = (sus.dimension_scores || {})[k];
        if (s == null) return;
        if (s <= 1) weak.push([s, k]); else strong.push(k);
      });
      weak.sort(function (a, b) { return a[0] - b[0]; });
      weak.slice(0, 3).forEach(function (wk) {
        var t = _weakDimText(wk[1], wk[0], m);
        if (t) lines.push((n++) + '. ' + t);
      });
      if (sus.verdict !== '不可持续') {
        var st = strong.slice(0, 2).map(function (k) { return _strongDimText(k, m); }).filter(Boolean);
        if (st.length) lines.push((n++) + '. 优势项：' + st.join('、'));
      }
    }
    if (sus.notes && sus.notes.length) lines.push('注：' + sus.notes.join('；'));
    return lines;
  }

  function round2(v) { return Math.round(v * 100) / 100; }

  /* Python str(float) 风格: 1.0 → "1.0", 2.332 → "2.332", 7.9 → "7.9" */
  function pyFloat(v) {
    if (Number.isInteger(v)) return v.toFixed(1);
    return String(v);
  }

  return {
    inferFiscalYear: inferFiscalYear,
    reportTime: reportTime,
    calculateDividendYield: calculateDividendYield,
    parseDividendRecords: parseDividendRecords,
    computeTtmDividend: computeTtmDividend,
    parseFinancials: parseFinancials,
    computeBasicPR: computeBasicPR,
    computeCorrectedPR: computeCorrectedPR,
    computePbPR: computePbPR,
    computeNFactor: computeNFactor,
    classifyValuation: classifyValuation,
    classifyIndustry: classifyIndustry,
    computePr: computePr,
    assessSustainability: assessSustainability,
    explainSustainability: explainSustainability,
    aggregateDividendHistory: _aggregateDividendHistory,
    round2: round2,
    CYCLICAL_INDUSTRIES: CYCLICAL_INDUSTRIES,
    TECH_INDUSTRIES: TECH_INDUSTRIES,
  };
}));
