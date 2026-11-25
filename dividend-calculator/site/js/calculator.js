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

  /* 行业关键词镜像 src/industry_policy.py（浏览器读不到 .py，只能保留副本）。
   * 改动必须双端同步：scripts/policy_parity.py::check_industry_keywords 会在
   * verify_js_vs_python.py / CI 里拦住漂移；词条一律子串匹配，用能唯一指认行业的专名。 */
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

  var GROWTH_INDUSTRIES = [
    '新能源', '光伏', '太阳能', '锂电', '储能', '风电', '氢能', '新能源汽车',
    '军工', '国防', '机器人', '工业机器人', '智能驾驶', '卫星导航',
    '生物医药', '创新药', 'CXO', '医疗器械', '医美',
    '新材料', '碳纤维', '复合材料',
    '算力', '数据中心', '大模型',
  ];

  function reportTime(year, isAnnual) {
    return year + (isAnnual ? '年报' : '中报');
  }

  function calculateDividendYield(totalDividend, totalMarketCap) {
    if (totalMarketCap <= 0) return [0.0, 0.0, 0.0];
    var before = (totalDividend / totalMarketCap) * 100;
    return [before, before * 0.9, before * 0.8];
  }

  /* ── 分红解析（对齐 Python dividend_records.summarize_dividend_rows，输入为东财
   * RPT_SHAREBONUS_DET 行）──
   * 行字段: REPORT_DATE (YYYY-MM-DD ...), PRETAX_BONUS_RMB (每10股派息), EX_DIVIDEND_DATE
   * 财年判定单一实现（Python sustainability.classify_fiscal_report）：仅12月=年报。
   * 返回: { totalDividend, year, details:[{report_time, dividend_per_10, ex_dividend_date}], explanation } */
  /* T5「已实施」判定单一实现（对齐 Python sustainability._is_implemented）：
   * 含「实施」且不含「未实施」（排除预案/预披露/批准等未落地状态）。
   * parseDividendRecords / computeTtmDividend / toChartDividendRecords 共用。 */
  function isImplemented(progress) {
    var p = String(progress || '');
    return p.indexOf('实施') !== -1 && p.indexOf('未实施') === -1;
  }

  /* 东财分红行 → 走势图记录（走势图数据链路映射单一实现，与 Python
   * parse_dividend_rows 纯送转口径一致：现金行进分子，纯送转行保留作股本锚点） */
  function toChartDividendRecords(rows) {
    return (rows || []).filter(function (r) {
      return r.EX_DIVIDEND_DATE && isImplemented(r.ASSIGN_PROGRESS) &&
        (Number(r.PRETAX_BONUS_RMB) > 0 || Number(r.IT_RATIO) > 0);
    }).map(function (r) {
      var y = r.REPORT_DATE ? r.REPORT_DATE.slice(0, 4) : '';
      var m = r.REPORT_DATE ? r.REPORT_DATE.slice(5, 7) : '';
      var isAnnual = (m === '12'); /* 仅 12 月报告期为年报（classify_fiscal_report 同口径） */
      return {
        ex_dividend_date: r.EX_DIVIDEND_DATE.slice(0, 10),
        dividend_per_10: Number(r.PRETAX_BONUS_RMB) > 0 ? r.PRETAX_BONUS_RMB : 0,
        report_time: y + (isAnnual ? '年报' : '中期分配'),
        /* 预案公告日：财年股息生效起点（走势图按此归因，非除权日）；无则空串回退除权日 */
        plan_notice_date: r.PLAN_NOTICE_DATE ? r.PLAN_NOTICE_DATE.slice(0, 10) : '',
        /* 登记股本（除权前）+ 送转比例：走势图总额法的股本锚点（无则该段股息率 null） */
        total_shares: r.TOTAL_SHARES > 0 ? Number(r.TOTAL_SHARES) : null,
        transfer_per_10: r.IT_RATIO != null ? Number(r.IT_RATIO) : null,
      };
    });
  }

  function parseDividendRecords(rows, totalShares) {
    var yearly = {};
    var reportDate = /^(\d{4})-(\d{2})/;

    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      /* T5：仅保留已实施分红（含'实施'且不含'未实施'，排除预案/预披露/批准等）*/
      if (!isImplemented(row.ASSIGN_PROGRESS)) continue;
      var dp10 = Number(row.PRETAX_BONUS_RMB);
      if (!(dp10 > 0)) continue;
      var m = reportDate.exec(row.REPORT_DATE || '');
      if (!m) continue;
      var y = parseInt(m[1], 10);
      var month = parseInt(m[2], 10);
      /* 与 Python classify_fiscal_report 一致: 仅12月为年报（完整财年），3/6/9月为中期分配 */
      var isAnnual = (month === 12);
      var label = isAnnual ? (y + '年报') : (y + '中期分配');

      if (!yearly[y]) yearly[y] = { hasAnnual: false, details: [] };
      yearly[y].hasAnnual = yearly[y].hasAnnual || isAnnual;
      /* P1-1: 每笔分红携带各自行股本（历史股本），0/NaN 视为无效存 null */
      var ts = row.TOTAL_SHARES ? Number(row.TOTAL_SHARES) : null;
      yearly[y].details.push({
        report_time: label, dividend_per_10: dp10,
        ex_dividend_date: String(row.EX_DIVIDEND_DATE || '').slice(0, 10),
        total_shares: (ts > 0) ? ts : null,
      });
    }

    var years = Object.keys(yearly).map(Number).sort(function (a, b) { return b - a; });
    if (!years.length) {
      return { totalDividend: 0, year: null, details: [], explanation: '无有效分红数据', sustainabilityHistory: null, yearlyDividendTotals: {} };
    }

    var target = null;
    for (var j = 0; j < years.length; j++) {
      if (yearly[years[j]].hasAnnual) { target = yearly[years[j]]; target.year = years[j]; break; }
    }
    /* 无年报（无完整财年）→ year=null、总额0，不退回最新有数据年份（对齐 Python
     * dividend_records：latest_year=None → fiscal_total=0） */
    if (!target) {
      return { totalDividend: 0, year: null, details: [], explanation: '无有效分红数据', sustainabilityHistory: null, yearlyDividendTotals: {} };
    }

    /* 明细按除权日升序（空串在前），与 Python summary.records 排序一致（#99） */
    target.details.sort(function (a, b) {
      return (a.ex_dividend_date || '').localeCompare(b.ex_dividend_date || '');
    });

    var totalPer10 = target.details.reduce(function (s, d) { return s + (Number(d.dividend_per_10) || 0); }, 0);
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
      /* 前瞻股息率用：{财年: 现金分红总额(元)}，与 Python dividend_totals_by_year 同口径 */
      yearlyDividendTotals: susHistory.year_amounts || {},
    };
  }

  /* ── 走势图月度股息率（总额法）──
   * 对齐主链路架构决策「总额法 > 每股法」：每股法（每股分红 ÷ 前复权价）在高送转公司
   * 口径错配——分子是除权时点名义每股分红（旧股本口径），分母是按当前股本回溯调整的
   * 前复权价（嘉友国际 603871 2021/01 曾因此显示 45.77%，真实约 3.7%）。
   * 总额法：
   *   分子 = 财年分红总额 Σ(dividend_per_10/10 × 该次登记股本 total_shares)
   *   分母 = 该月名义收盘价 close_nominal × 该月股本
   * 登记股本语义（东财 TOTAL_SHARES = 股权登记日股本 = 该次除权**前**股本）：
   *   S(t) = 下一次除权记录的登记股本（t 在两次除权之间）
   *   t 早于首笔除权 → 首笔登记股本；t 晚于全部除权 → totalSharesNow（当前总股本，
   *   与主卡自洽）回退 末条登记股本 × 末条送转因子（transfer_per_10）
   *   两次除权之间的增发不反映在锚点内（数据可得性极限，误差远小于口径错配）
   * monthlyPrices: [{date, close(前复权,画图用), close_nominal(不复权,算股息率)}]
   * dividendRecords: [{ex_dividend_date, dividend_per_10, report_time,
   *                    plan_notice_date, total_shares, transfer_per_10}]
   * 财年归因保留原语义：availDate（预案公告日回退除权日）≤ 月末的最新年报财年。
   * 数据缺失（total_shares / close_nominal 无）→ yield = null（宁缺毋假，数据铁律）。
   * 返回 [{date, price, yield, fiscalYear}] 升序 */
  function computeDividendYields(monthlyPrices, dividendRecords, totalSharesNow) {
    if (!monthlyPrices.length) return [];

    /* 1. 按财年分组：分红总额 + 年报标记 + 生效起点 */
    var fiscalYears = {};
    var exEvents = []; /* 除权事件：[{exDate, sharesBefore(登记股本), transfer(10送转X)}] */
    for (var i = 0; i < dividendRecords.length; i++) {
      var rec = dividendRecords[i];
      if (!rec.ex_dividend_date) continue;

      /* 除权事件（现金分红或纯送转均构成股本口径切换点）：纯送转行
       *（dividend_per_10=0/无）不进分子，但必须进锚点链——否则「上笔分红
       * 除权 → 纯送转除权」之间月份的股本锚点错到送转后，股息率静默偏差 */
      if (rec.total_shares > 0) {
        exEvents.push({
          exDate: rec.ex_dividend_date,
          sharesBefore: rec.total_shares,
          transfer: Number(rec.transfer_per_10) > 0 ? Number(rec.transfer_per_10) : 0,
        });
      }
      if (!(rec.dividend_per_10 > 0)) continue;
      var yearMatch = /\d{4}/.exec(rec.report_time || '');
      if (!yearMatch) continue;
      var year = parseInt(yearMatch[0], 10);

      if (!fiscalYears[year]) {
        fiscalYears[year] = { totalAmount: 0, complete: false, availDate: '' };
      }
      /* 该次分红总额：每股分红 × 该次登记股本；股本缺失 → NaN（该财年整体作缺） */
      var dps = rec.dividend_per_10 / 10.0;
      var shares = rec.total_shares > 0 ? rec.total_shares : NaN;
      fiscalYears[year].totalAmount += dps * shares;
      if (rec.report_time.indexOf('年报') !== -1 && rec.report_time.indexOf('半年报') === -1) {
        fiscalYears[year].complete = true;
      }
      /* 财年股息生效起点：预案公告日（该财年股息已公布），回退除权日（mootdx 兜底无预案字段） */
      var effDate = rec.plan_notice_date || rec.ex_dividend_date;
      if (effDate > fiscalYears[year].availDate) {
        fiscalYears[year].availDate = effDate;
      }
    }
    exEvents.sort(function (a, b) {
      return a.exDate < b.exDate ? -1 : (a.exDate > b.exDate ? 1 : 0);
    });

    /* 2. 完整财年列表（有年报），按年份升序 */
    var completeYears = [];
    for (var y in fiscalYears) {
      if (fiscalYears[y].complete) {
        completeYears.push({
          year: parseInt(y, 10),
          totalAmount: fiscalYears[y].totalAmount,
          availDate: fiscalYears[y].availDate,
        });
      }
    }
    completeYears.sort(function (a, b) { return a.year - b.year; });

    /* 3. 该月股本：下一次除权前的登记股本（锚点） */
    function sharesAt(dateStr) {
      var last = null;
      for (var a = 0; a < exEvents.length; a++) {
        var ev = exEvents[a];
        if (ev.exDate > dateStr) {
          return ev.sharesBefore; /* t 在此除权之前 → 登记股本即 t 时点股本 */
        }
        last = ev;
      }
      /* t 晚于全部除权：当前总股本（与主卡自洽）→ 回退 末条登记股本 × 末条送转因子 */
      if (totalSharesNow > 0) return totalSharesNow;
      if (last && last.sharesBefore) {
        return last.sharesBefore * (10 + last.transfer) / 10;
      }
      return null;
    }

    /* 4. 逐月计算：分子财年总额 ÷ 分母当月市值 */
    return monthlyPrices.map(function (month) {
      var selected = null;
      for (var k = 0; k < completeYears.length; k++) {
        if (completeYears[k].availDate <= month.date) {
          selected = completeYears[k];
        }
      }

      var yld = null;
      var shares = sharesAt(month.date);
      var nominal = Number(month.close_nominal);
      if (selected && selected.totalAmount > 0 && shares && nominal > 0) {
        yld = round2(selected.totalAmount / (nominal * shares) * 100);
      }

      return {
        date: month.date,
        price: month.close,
        yield: yld,
        fiscalYear: selected ? selected.year : null,
      };
    });
  }

  /* 走势图除权除息标记。
   * x 必须落在月K线的类目轴上（月末日期），除权日是月中日 → 定位到「除权日所在
   * 或之后的第一个月末」。旧实现用 ex_dividend_date 当 coord.x，该类目不在轴上，
   * 标记无法定位；窗口外（早于首月 / 晚于末月）的事件也无法画，直接跳过。
   * 纯送转行（dp=0, transfer>0）同样构成除权事件，文案不写「10派0元」。
   * 返回 [{name, coord:[月末日期, 前复权收盘价]}] */
  function buildExDivMarkers(monthlyPrices, dividendRecords) {
    if (!monthlyPrices || !monthlyPrices.length || !dividendRecords) return [];
    var first = monthlyPrices[0].date;
    var last = monthlyPrices[monthlyPrices.length - 1].date;
    var out = [];
    for (var i = 0; i < dividendRecords.length; i++) {
      var rec = dividendRecords[i];
      var ex = rec.ex_dividend_date;
      if (!ex) continue;
      var dp = Number(rec.dividend_per_10) || 0;
      var tr = Number(rec.transfer_per_10) || 0;
      if (!(dp > 0) && !(tr > 0)) continue;
      if (ex < first || ex > last) continue; /* 窗口外：无对应月份，画不出 */
      var idx = -1;
      for (var k = 0; k < monthlyPrices.length; k++) {
        if (monthlyPrices[k].date >= ex) { idx = k; break; }
      }
      if (idx < 0) continue;
      var bits = [];
      if (dp > 0) bits.push('10派' + dp + '元');
      if (tr > 0) bits.push('10送转' + tr);
      out.push({
        name: (rec.report_time || '') + ': ' + bits.join(''),
        coord: [monthlyPrices[idx].date, monthlyPrices[idx].close],
      });
    }
    return out;
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
      if (!isImplemented(row.ASSIGN_PROGRESS)) continue;
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

  /* ── #26 A 股特色风险：ST 与商誉（镜像 src/risk_flags.py）──
   * 两者都是警示，不改评分、档位或漏斗判据。 */
  var GOODWILL_EQUITY_WARN = 0.50;

  function isST(stockName) {
    return !!stockName && String(stockName).toUpperCase().indexOf('ST') !== -1;
  }

  function stWarning(stockName) {
    if (!isST(stockName)) return null;
    return 'ST 股票（退市风险警示）：盈利与分红政策可能已恶化，历史分红不可直接外推';
  }

  /* goodwill / equity > 50% 时返回警示文案；缺数据返回 null（不补 0、不推断） */
  function goodwillWarning(goodwill, equity) {
    if (goodwill == null || equity == null || !(equity > 0) || !(goodwill > 0)) return null;
    var ratio = goodwill / equity;
    if (ratio <= GOODWILL_EQUITY_WARN) return null;
    return '商誉占净资产 ' + Math.round(ratio * 100) + '%（>' +
      Math.round(GOODWILL_EQUITY_WARN * 100) + '%）：商誉减值会直接侵蚀净资产与分红能力';
  }

  /* 按财年聚合 → {consecutive_years, ever_cut, latest_year_amount, history_mean_amount} */
  /* ── 股息 CAGR / 趋势（#30，对齐 sustainability_calculator.compute_dividend_cagr）──
   * 窗口：从最新财年向前取连续年份，遇缺年/零分红即停，最多 5 年；不足 3 年不估。
   * 返回 {cagr, trend, years, note}，cagr 为小数（0.084 = 8.4%/年）。 */
  var DIV_CAGR_MAX_YEARS = 5;
  var DIV_CAGR_MIN_YEARS = 3;
  var DIV_CAGR_GROWTH = 0.05;
  var DIV_CAGR_DECLINE = -0.05;

  function computeDividendCagr(yearAmount, completeYears, annualAmount, midYears) {
    var amounts = {};
    Object.keys(yearAmount || {}).forEach(function (k) {
      var y = parseInt(String(k).slice(0, 4), 10);
      var v = yearAmount[k];
      if (!isNaN(y) && v != null) amounts[y] = Number(v);
    });
    var years = Object.keys(amounts).map(Number);
    if (!years.length) return { cagr: null, trend: null, years: 0, note: null };
    /* completeYears：有年报记录的财年（当年只有中期分配时分红不完整，不能当窗口端点） */
    var complete = null;
    if (completeYears) {
      complete = {};
      Object.keys(completeYears).forEach(function (k) {
        var y = parseInt(String(k).slice(0, 4), 10);
        if (!isNaN(y)) complete[y] = true;
      });
      years = years.filter(function (y) { return complete[y]; });
      if (!years.length) return { cagr: null, trend: null, years: 0, note: '无完整财年分红数据，不估增长率' };
    }
    var latest = Math.max.apply(null, years);
    if (!(amounts[latest] > 0)) {
      return { cagr: null, trend: null, years: 1, note: '最新财年无现金分红，不估增长率' };
    }
    var chain = [];
    var y = latest;
    while (amounts[y] != null && amounts[y] > 0 && (!complete || complete[y])
           && chain.length < DIV_CAGR_MAX_YEARS) {
      chain.push(amounts[y]); y -= 1;
    }
    if (chain.length < DIV_CAGR_MIN_YEARS) {
      return { cagr: null, trend: null, years: chain.length,
               note: '连续分红年数不足 ' + DIV_CAGR_MIN_YEARS + ' 年（' + chain.length + ' 年），不估增长率' };
    }
    var start = chain[chain.length - 1], end = chain[0];
    var cagr = Math.pow(end / start, 1 / (chain.length - 1)) - 1;
    var trend = cagr > DIV_CAGR_GROWTH ? '增长' : (cagr < DIV_CAGR_DECLINE ? '萎缩' : '平稳');
    var notes = [];
    var others = chain.slice(1).sort(function (a, b) { return a - b; });
    if (others.length) {
      var baseline = others[Math.floor(others.length / 2)];
      var peak = Math.max.apply(null, chain);
      if (baseline > 0 && peak > baseline * SUS_WARN.special_div) {
        notes.push('窗口内某年分红远超其余年份（周期高峰或特别分红），增长率会被放大');
      }
    }
    /* 分红次数变化（某年起加中期分配）会让年度现金口径跳变，给出年报口径对照。
     * 镜像 Python compute_dividend_cagr：伊利 2025 起加中期分配 → 总额 +9.2%/年，
     * 但仅年报口径为 -1.9%/年。 */
    var windowYears = [];
    for (var i = 0; i < chain.length; i++) windowYears.push(latest - i);
    var cagrAnnual = null;
    if (annualAmount) {
      var annChain = windowYears.map(function (y) { return annualAmount[y]; });
      if (annChain.every(function (a) { return a != null && a > 0; })) {
        cagrAnnual = Math.pow(annChain[0] / annChain[annChain.length - 1], 1 / (annChain.length - 1)) - 1;
      }
    }
    var midSet = null;
    if (midYears) {
      midSet = {};
      midYears.forEach(function (y) { midSet[parseInt(String(y).slice(0, 4), 10)] = true; });
    }
    /* 恢复性增长：窗口起点前一年无分红（亏损停派/IPO 当年），3 年期增速会显得很高。
     * 镜像 Python：只提示，不改数值。 */
    var priorYear = latest - chain.length;
    if (!amounts[priorYear]) {
      notes.push('窗口起点 ' + windowYears[windowYears.length - 1] + ' 之前一年（' + priorYear +
        '）无现金分红记录（亏损停派或上市较晚），起点基数可能偏低，读作长期趋势需谨慎');
    }

    if (midSet && cagrAnnual != null) {
      var flags = {};
      windowYears.forEach(function (y) { flags[midSet[y] ? 1 : 0] = true; });
      if (Object.keys(flags).length > 1) {
        notes.push('窗口内分红次数不一致（某年起增加中期分配），增速含分红政策变化；' +
          '仅按年报口径为 ' + (cagrAnnual > 0 ? '+' : '') + (cagrAnnual * 100).toFixed(1) + '%/年');
      }
    }
    return { cagr: cagr, trend: trend, years: chain.length,
             note: notes.length ? notes.join('；') : null, cagr_annual: cagrAnnual };
  }

  function _aggregateDividendHistory(yearly, targetYear, totalShares) {
    /* P1-1: 每笔分红用各自行股本（历史股本）折算，缺失/无效回退参数股本 —— 与 Python 逐记录一致 */
    function recShares(d) {
      return (d.total_shares != null && d.total_shares > 0) ? d.total_shares : totalShares;
    }
    var yearAmount = {};
    Object.keys(yearly).forEach(function (y) {
      var sum = 0;
      (yearly[y].details || []).forEach(function (d) {
        sum += (Number(d.dividend_per_10) || 0) / 10.0 * recShares(d);
      });
      yearAmount[y] = sum;
    });
    /* 仅年报记录参与 ever_cut（#39）：yearly[y].details 含 {report_time, dividend_per_10, total_shares}，
     * report_time 为 'YYYY年报'/'YYYY中期分配'（注意 '中期分配' 不含 '年报' 子串，双重判断防 '半年报'）。
     * 半年报混入会掩盖相邻年降幅（如 2025 年报 8 元 + 半年报 3 元被当作 11 元）。 */
    var annualAmount = {};
    /* 完整财年集合：该年至少有一条年报记录（Python annual_amount 的 key 语义）。
     * 不能只看 annualAmount[y] 是否存在——那会给"只有中期分配"的当年填 0，
     * 使其被当成完整财年（#30：中远海控/宝钢 的 CAGR 曾因此算成暴跌）。 */
    var completeYears = {};
    var midYears = {};
    Object.keys(yearly).forEach(function (y) {
      var sum = 0;
      var hasAnnual = false;
      (yearly[y].details || []).forEach(function (d) {
        var t = String(d.report_time || '');
        if (t.indexOf('年报') !== -1 && t.indexOf('半年报') === -1) {
          hasAnnual = true;
          sum += (Number(d.dividend_per_10) || 0) / 10.0 * recShares(d);
        } else if ((Number(d.dividend_per_10) || 0) > 0) {
          /* 中期分配（当年非年报记录）——只用于识别「分红次数变化」 */
          midYears[y] = 1;
        }
      });
      annualAmount[y] = sum;
      if (hasAnnual) completeYears[y] = 1;
    });
    var yearsSorted = Object.keys(yearAmount).map(Number).sort(function (a, b) { return b - a; });
    if (!yearsSorted.length) {
      return { consecutive_years: 0, ever_cut: false, latest_year_amount: null, history_mean_amount: null,
               year_amounts: {}, cagr: null, trend: null, cagr_years: 0, cagr_note: null };
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
    var growth = computeDividendCagr(yearAmount, completeYears, annualAmount, Object.keys(midYears));
    return {
      consecutive_years: consecutive, ever_cut: everCut,
      cagr: growth.cagr, trend: growth.trend,
      cagr_years: growth.years, cagr_note: growth.note,
      cagr_annual: growth.cagr_annual,
      /* #35: 用 baseYear（最新有年报年，含 fallback，与 Python target_year 语义一致）而非最大年份 */
      latest_year_amount: yearAmount[String(baseYear)] || null,
      history_mean_amount: historyMean,
      history_3y_mean: history3yMean,
      year_amounts: yearAmount,
    };
  }

  /* ── 财务数据（对齐 pr.py _get_financial，输入为东财 RPT_F10_FINANCE_MAINFINADATA 行）
   * PARENTNETPROFIT 为累计值(YTD)，TTM 用最近报告期补齐上年同期。
   * 可持续性字段（NETCASH_OPERATE_PK/NETCASH_INVEST_PK/LIABILITY/TOTAL_ASSETS_PK/
   * DEBT_ASSET_RATIO/INTEREST_DEBT_RATIO/INTEREST_COVERAGE_RATIO/银行专项）在 columns=ALL 已含。 */
  function parseFinancials(rows) {
    if (!rows.length) return { roeLatest: null, roe5yMedian: null, roePeriod: null, netProfitTtm: null, netProfitAnnual: null, netProfitByYear: {}, sustainabilityFin: null };

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

    /* 前瞻股息率用：{财年: 归母净利润(元)}，仅 12-31 年报行（与 Python net_profit_by_year 同口径） */
    var netProfitByYear = {};
    for (var n = 0; n < rows.length; n++) {
      var npDate = String(rows[n].REPORT_DATE || '').slice(0, 10);
      if (npDate.length !== 10 || npDate.slice(5, 10) !== '12-31') continue;
      var npVal = toNum(rows[n].PARENTNETPROFIT);
      if (isFinite(npVal)) netProfitByYear[parseInt(npDate.slice(0, 4), 10)] = npVal;
    }

    return {
      roeLatest: roeLatest, roe5yMedian: roe5yMedian,
      roePeriod: annual.length ? annual[0].year : null,
      netProfitTtm: netProfitTtm, netProfitAnnual: netProfitAnnual,
      netProfitByYear: netProfitByYear,
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

  /* ── 下一财年前瞻股息率（对齐 src/forward_yield.py，见 docs/FORWARD_YIELD.md）──
   * 前瞻 = 下一财年 L+1 一致预期 EPS × 近 3 年支付率中位数 ÷ 现价。
   * 支付率按年：Σ(每次 10派/10 × 该次登记股本) ÷ 当年归母净利（免疫送转）。
   * 不可估（无预测/预测亏损/近年无分红）→ 字段全 null + forward_note。
   * 仅展示，不进入选股判据（免费源无历史一致预期快照，暂不可回测）。 */
  var FWD_PAYOUT_WINDOW = 3, FWD_MAX_PAYOUT = 1.0, FWD_TREND_DEVIATION = 0.30;
  var FWD_FULL_COVERAGE = 3, FWD_STALE_GAP = 2, FWD_YIELD_DEVIATION = 0.5;
  var FWD_YIELD_MAX = 15.0, FWD_EPS_NP_TOLERANCE = 0.03;

  function _fwdNum(v) {
    if (v == null || String(v).trim() === '') return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }

  function _fwdMedian(values) {
    /* Python statistics.median 语义：偶数个取中间两数均值 */
    var s = values.slice().sort(function (a, b) { return a - b; });
    var mid = Math.floor(s.length / 2);
    if (s.length % 2) return s[mid];
    return (s[mid - 1] + s[mid]) / 2;
  }

  /* 东财一致预期行 → {base_year, eps_by_year, mark_by_year, coverage}；无可用行返回 null。
   * 实地验证：YEAR{i} 为财年，YEAR_MARK{i} 为标记（A 已披露 / E 预测）。 */
  function _parseForecastRow(rows) {
    if (!rows || !rows.length) return null;
    for (var r = 0; r < rows.length; r++) {
      var row = rows[r];
      var epsByYear = {}, markByYear = {}, firstYear = null;
      for (var i = 1; i <= 4; i++) {
        var year = _fwdNum(row['YEAR' + i]);
        if (year == null) continue;
        year = Math.trunc(year);
        if (firstYear == null) firstYear = year;
        var mark = String(row['YEAR_MARK' + i] || '').trim().toUpperCase();
        if (mark) markByYear[year] = mark;
        var eps = _fwdNum(row['EPS' + i]);
        if (eps != null) epsByYear[year] = eps;
      }
      if (!Object.keys(epsByYear).length) continue;
      var baseYear = null;
      Object.keys(markByYear).forEach(function (y) {
        if (markByYear[y] === 'A' && epsByYear[y] != null) {
          var yi = parseInt(y, 10);
          if (baseYear == null || yi > baseYear) baseYear = yi;
        }
      });
      if (baseYear == null) baseYear = firstYear != null ? firstYear : Math.min.apply(null, Object.keys(epsByYear).map(Number));
      var cov = _fwdNum(row.RATING_ORG_NUM);
      return {
        base_year: baseYear, eps_by_year: epsByYear, mark_by_year: markByYear,
        coverage: cov == null ? null : Math.trunc(cov),
      };
    }
    return null;
  }

  /* ── 现金回报率（现金分红 + 注销类回购，#29）──
   * 与 computeFromRaw 里的 tsr（ROE 口径「估值不变下的股东总回报率」）无关：
   * 本条只看**已发生**的现金流出 =（现金分红总额 + 注销类回购金额）÷ 当前总市值，
   * 不假设未来 ROE 与估值。对齐 Python src/cash_return.py，字段同为 snake_case。
   * 口径：只计用途含「注销」的回购；以回购完成日归属财年；取数失败不当 0。 */
  var CR_BUYBACK_KEYWORD = '注销';
  var CR_INCENTIVE_KEYWORDS = ['股权激励', '员工持股'];

  function classifyRepurchasePurpose(text) {
    var s = (text == null) ? '' : String(text);
    if (s.indexOf(CR_BUYBACK_KEYWORD) >= 0) return 'buyback';
    for (var i = 0; i < CR_INCENTIVE_KEYWORDS.length; i++) {
      if (s.indexOf(CR_INCENTIVE_KEYWORDS[i]) >= 0) return 'incentive';
    }
    return 'unknown';
  }

  function _crNum(v) {
    if (v == null) return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }

  function _crYear(v) {
    var s = (v == null ? '' : String(v)).slice(0, 4);
    return /^\d{4}$/.test(s) ? Number(s) : null;
  }

  function aggregateRepurchase(rows, fiscalYear) {
    var amount = 0, incentive = 0, inProgress = 0, unclassified = 0, events = 0, mixed = 0;
    rows = rows || [];
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i] || {};
      var amt = _crNum(row.REPURAMOUNT) || 0;
      if (amt <= 0) continue;
      var kind = classifyRepurchasePurpose(row.REPUROBJECTIVE);
      var finished = _crYear(row.FINISHDATE);
      if (finished == null) {
        var startYear = _crYear(row.REPURSTARTDATE) || _crYear(row.DIM_DATE);
        var endYear = _crYear(row.REPURENDDATE);
        if (startYear != null && startYear <= fiscalYear && (endYear == null || endYear >= fiscalYear)) {
          inProgress += amt;
        }
        continue;
      }
      if (finished !== fiscalYear) continue;
      if (kind === 'buyback') {
        amount += amt;
        events += 1;
        var purpose = String(row.REPUROBJECTIVE || '');
        for (var k = 0; k < CR_INCENTIVE_KEYWORDS.length; k++) {
          if (purpose.indexOf(CR_INCENTIVE_KEYWORDS[k]) >= 0) { mixed += 1; break; }
        }
      } else if (kind === 'incentive') {
        incentive += amt;
      } else {
        unclassified += amt;
      }
    }
    return { amount: amount, events: events, incentive: incentive,
             in_progress: inProgress, unclassified: unclassified, mixed: mixed };
  }

  function computeCashReturn(input) {
    input = input || {};
    var fiscalYear = _crNum(input.fiscalYear);
    var marketCap = _crNum(input.marketCap);
    var res = {
      cash_return_status: 'missing_inputs', cash_return_year: fiscalYear,
      cash_return_dividend: null, cash_return_repurchase: null,
      cash_return_repurchase_events: 0, cash_return_incentive_excluded: null,
      cash_return_in_progress: null, cash_return_dividend_yield: null,
      cash_return_repurchase_yield: null, cash_return_total_yield: null,
      cash_return_note: '',
    };
    if (fiscalYear == null) {
      res.cash_return_status = 'no_fiscal_year';
      res.cash_return_note = '无完整财年，不做股东总回报';
      return res;
    }
    if (marketCap == null || marketCap <= 0) {
      res.cash_return_status = 'missing_inputs';
      res.cash_return_note = '缺少总市值，不做股东总回报';
      return res;
    }

    var div = (_crNum(input.dividendTotal) || 0) > 0 ? _crNum(input.dividendTotal) : 0;
    res.cash_return_dividend = div;
    res.cash_return_dividend_yield = div / marketCap * 100;

    if (input.fetchError) {
      res.cash_return_status = 'fetch_error';
      res.cash_return_note = '回购取数失败，只显示分红收益率（不把未知当 0）';
      return res;
    }

    var agg = aggregateRepurchase(input.repurchaseRows || [], fiscalYear);
    res.cash_return_repurchase = agg.amount;
    res.cash_return_repurchase_events = agg.events;
    res.cash_return_incentive_excluded = agg.incentive || null;
    res.cash_return_in_progress = agg.in_progress || null;
    res.cash_return_repurchase_yield = agg.amount / marketCap * 100;
    res.cash_return_total_yield = (div + agg.amount) / marketCap * 100;

    var notes = [];
    if (!input.repurchaseRows || !input.repurchaseRows.length) {
      res.cash_return_status = 'no_repurchase';
      notes.push('该股无回购记录');
    } else {
      res.cash_return_status = 'ok';
      if (agg.events === 0) notes.push('该财年无完成的注销类回购');
    }
    if (agg.incentive > 0) notes.push('激励类回购 ' + (agg.incentive / 1e8).toFixed(2) + ' 亿元未计入（属薪酬支出）');
    if (agg.in_progress > 0) notes.push('进行中回购 ' + (agg.in_progress / 1e8).toFixed(2) + ' 亿元未完成，未计入');
    if (agg.unclassified > 0) notes.push('用途未标注的 ' + (agg.unclassified / 1e8).toFixed(2) + ' 亿元未计入');
    if (agg.mixed > 0) notes.push('含 ' + agg.mixed + ' 笔「注销+激励」混合计划，按注销类计入');
    notes.push('按回购完成日归属财年，未按日拆分');
    res.cash_return_note = notes.join('；');
    return res;
  }

  function computeForwardYield(input) {
    var price = input.price, totalShares = input.totalShares;
    var isCyclical = !!input.isCyclical;
    var trailingYield = input.trailingYield;
    var res = {
      forward_status: null, forward_year: null, forward_eps: null, forward_eps_kind: null,
      forward_payout_assumed: null, forward_payout_basis: '',
      forward_payout_samples: [], forward_dps: null,
      forward_yield_before_tax: null, forward_coverage: null,
      forward_source: '', forward_warnings: [], forward_note: '',
    };

    var latest = _fwdNum(input.latestYear);
    if (latest == null) { res.forward_status = 'no_complete_year'; res.forward_note = '无完整财年分红，不做前瞻'; return res; }
    latest = Math.trunc(latest);
    /* 契约：目标财年可确定时就给出，UI 才能显示「FY2026E 无预测」 */
    res.forward_year = latest + 1;

    if (input.forecast_rows == null) {
      res.forward_status = 'fetch_error';
      res.forward_note = '前瞻取数失败（盈利预测），稍后重试';
      return res;
    }
    var forecast = _parseForecastRow(input.forecast_rows);
    if (!forecast) { res.forward_status = 'no_coverage'; res.forward_note = '无机构盈利预测，未做前瞻估算'; return res; }

    res.forward_coverage = forecast.coverage;
    res.forward_source = '东财一致预期';
    /* 股本口径守卫：用基准年（MARK='A'）的 EPS 校验 EPS×股本 ≈ 归母净利 */
    Array.prototype.push.apply(res.forward_warnings,
      _fwdEpsShareWarnings(forecast, input.netProfitByYear || {}, totalShares));

    if (forecast.base_year - latest >= FWD_STALE_GAP) {
      res.forward_status = 'stale';
      res.forward_year = null;  /* latest+1 已是过去年份，不能当前瞻财年 */
      res.forward_note = '近年无分红（一致预期已到 ' + forecast.base_year + ' 年，最新分红财年 ' +
        latest + '），不做前瞻';
      return res;
    }

    var forwardYear = res.forward_year;
    var eps = forecast.eps_by_year[forwardYear];
    if (eps == null) {
      res.forward_status = 'no_target_year';
      res.forward_note = '无 ' + forwardYear + ' 年盈利预测';
      return res;
    }
    res.forward_eps = eps;
    res.forward_eps_kind = forecast.mark_by_year[forwardYear] === 'A' ? 'A' : 'E';
    if (eps <= 0) {
      res.forward_eps = null;
      res.forward_eps_kind = null;
      res.forward_status = 'no_positive_eps';
      res.forward_note = '机构预测 ' + forwardYear + ' 年亏损，不估股息率';
      return res;
    }

    var yearly = input.yearlyDividendTotals || {};
    var npByYear = input.netProfitByYear || {};
    var samples = [];
    for (var y = latest; y > latest - FWD_PAYOUT_WINDOW; y--) {
      var np = npByYear[y], div = yearly[y];
      if (np == null || np <= 0 || div == null || div <= 0) continue;
      samples.push(div / np);
    }
    res.forward_payout_samples = samples;
    if (!samples.length) {
      res.forward_status = 'no_payout_history';
      res.forward_note = '无可用分红历史，不做前瞻';
      return res;
    }

    var rawMedian = _fwdMedian(samples);
    var assumed = Math.min(Math.max(rawMedian, 0), FWD_MAX_PAYOUT);
    res.forward_payout_assumed = assumed;
    res.forward_payout_basis = '近3年支付率中位数 ' + (rawMedian * 100).toFixed(1) + '%';

    if (samples.length < FWD_PAYOUT_WINDOW) {
      res.forward_warnings.push('支付率样本不连续（最近 ' + FWD_PAYOUT_WINDOW +
        ' 个财年仅 ' + samples.length + ' 年可用）');
    }
    var trend = Math.abs(samples[0] - rawMedian) > FWD_TREND_DEVIATION;
    if (samples[0] > FWD_MAX_PAYOUT && trend) {
      res.forward_warnings.push('最新财年支付率 ' + (samples[0] * 100).toFixed(1) +
        '% > 100%（超出当年净利），与近3年中位数 ' + (rawMedian * 100).toFixed(1) +
        '% 偏离超 ' + Math.round(FWD_TREND_DEVIATION * 100) + ' 个百分点，分红政策可能变化');
    } else if (samples[0] > FWD_MAX_PAYOUT) {
      res.forward_warnings.push('最新财年支付率 ' + (samples[0] * 100).toFixed(1) + '% > 100%（超出当年净利）');
    } else if (trend) {
      res.forward_warnings.push('最新财年支付率 ' + (samples[0] * 100).toFixed(1) +
        '% 与近3年中位数 ' + (rawMedian * 100).toFixed(1) + '% 偏离超 ' +
        Math.round(FWD_TREND_DEVIATION * 100) + ' 个百分点，分红政策可能变化');
    }

    if (rawMedian > FWD_MAX_PAYOUT) {
      res.forward_warnings.push('近3年支付率中位数 ' + (rawMedian * 100).toFixed(1) + '% > 100%，假设截断为 100%');
    }
    if (res.forward_coverage != null && res.forward_coverage < FWD_FULL_COVERAGE) {
      res.forward_warnings.push('一致预期仅 ' + res.forward_coverage + ' 家机构覆盖（样本少）');
    }
    if (isCyclical) res.forward_warnings.push('周期行业，盈利预测波动大，前瞻仅供参考');

    /* 现价门必须在这批警示之后：Python compute_forward_yield 同序，
     * 否则缺现价时两端 forward_warnings 集合不同，verify 对账会分叉 */
    if (price == null || !(price > 0)) {
      res.forward_status = 'missing_inputs';
      res.forward_note = '缺少现价，不做前瞻';
      return res;
    }

    res.forward_dps = eps * assumed;
    res.forward_yield_before_tax = res.forward_dps / price * 100.0;
    res.forward_status = 'ok';
    res.forward_note = forwardYear + res.forward_eps_kind + ' EPS ' + eps.toFixed(2) +
      ' × 支付率 ' + (assumed * 100).toFixed(1) + '% ÷ 现价 ' + price.toFixed(2);
    if (trailingYield && trailingYield > 0 &&
        (res.forward_yield_before_tax > trailingYield * (1 + FWD_YIELD_DEVIATION) ||
         res.forward_yield_before_tax < trailingYield * (1 - FWD_YIELD_DEVIATION))) {
      res.forward_warnings.push('前瞻 ' + res.forward_yield_before_tax.toFixed(2) + '% 与主口径 ' +
        trailingYield.toFixed(2) + '% 偏离超 50%，请核对');
    }
    if (res.forward_yield_before_tax > FWD_YIELD_MAX) {
      res.forward_warnings.push('前瞻股息率 ' + res.forward_yield_before_tax.toFixed(2) + '% 超合理上限，疑似数据异常');
    }
    return res;
  }

  /* 用一致预期基准年的已披露 EPS 校验股本口径：EPS × 当前股本 应 ≈ 基准年归母净利。
   * 一致预期 EPS 是当前股本口径；基准年之后有送转/增发时两者会偏离 → 提示（不阻断）。 */
  function _fwdEpsShareWarnings(forecast, npByYear, totalShares) {
    if (!totalShares || !(totalShares > 0)) return [];
    var base = forecast.base_year;
    if (forecast.mark_by_year[base] !== 'A') return [];
    var eps = forecast.eps_by_year[base];
    var npActual = npByYear ? npByYear[base] : null;
    if (eps == null || npActual == null || !(npActual > 0)) return [];
    var implied = eps * totalShares;
    if (Math.abs(implied - npActual) / npActual <= FWD_EPS_NP_TOLERANCE) return [];
    return ['一致预期基准年(' + base + ') EPS ' + eps.toFixed(2) + ' × 股本 与归母净利偏差 ' +
      (Math.abs(implied / npActual - 1) * 100).toFixed(0) + '%，股本口径可能不一致'];
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

  // N 因子（股利支付率修正系数），与 Python compute_n_factor 逐字一致。
  // N = 50% / 股利支付率；payout ≥ 50% → N=1.0，≤ 25% → N=2.0，之间按比例，区间 [1.0, 2.0]。
  // 出处与依据（数据铁律：如实标注，不编造理论）：市赚率为雪球用户 ericwarn丁宁（丁宁）
  // 发明的估值参数；修正市赚率是作者自认的「经验公式」（原话「没什么道理可讲」），
  // 非理论推导，无学术/券商研报出处。50% 基准取贵州茅台多年平均股利支付率
  // （作者以茅台为「赚真钱」估值锚，支付率低于 50% 视为「赚假钱」，N 相应放大）。
  function computeNFactor(payoutRatio) {
    if (payoutRatio == null) return null;
    if (payoutRatio <= 0) return 2.0;
    var raw = 0.50 / payoutRatio;
    return Math.max(1.0, Math.min(2.0, raw));
  }

  function judgePR(peTtm, roeLatest, roe5yMedian, isCyclical) {
    // 选股判定口径：基础 PR = PE_TTM / ROE（与 V2 回测 L3 同口径），
    // 修正 PR 只展示不进判据。与 Python pr_calculator.judge_pr 逐字一致。
    // ROE 取值复用 selectRoe（周期股用 5 年中位数，与 TSR/PB-PR 同一规则）。
    var roe = selectRoe(roeLatest, roe5yMedian, isCyclical);
    if (peTtm == null || roe == null || roe <= 0) {
      return { roe: roe, pr: null, zone: '无法判定' };
    }
    var pr = computeBasicPR(peTtm, roe);
    return { roe: roe, pr: pr, zone: classifyValuation(pr) };
  }

  function classifyValuation(pr) {
    // 阈值基于 PR 历史回测（2016-2024 沪深300，见 docs/BACKTEST_REPORT.md）：
    // 低估(≤0.5)与合理偏低(0.5-1)超额 +4%+，合理(1-3)接近中性，高估(>3)跑输 5%+。
    // 与 Python classify_valuation 逐字一致。
    if (pr == null) return '无法判定';
    if (pr <= 0.5) return '低估';
    if (pr <= 1.0) return '合理偏低';
    if (pr <= 3.0) return '合理';
    return '高估';
  }

  function classifyIndustry(industry) {
    var isCyclical = false, isTech = false, isGrowth = false;
    for (var i = 0; i < CYCLICAL_INDUSTRIES.length; i++) {
      if (industry.indexOf(CYCLICAL_INDUSTRIES[i]) !== -1) { isCyclical = true; break; }
    }
    for (var j = 0; j < TECH_INDUSTRIES.length; j++) {
      if (industry.indexOf(TECH_INDUSTRIES[j]) !== -1) { isTech = true; break; }
    }
    for (var k = 0; k < GROWTH_INDUSTRIES.length; k++) {
      if (industry.indexOf(GROWTH_INDUSTRIES[k]) !== -1) { isGrowth = true; break; }
    }
    var warning = '';
    if (isCyclical) {
      warning = '该股属于周期行业，修正市赚率仅供参考；建议优先参考PB-市赚率';
    } else if (isTech) {
      warning = '该股属于科技行业，修正市赚率可能不适用（科技股常以回购代替分红）';
    } else if (isGrowth) {
      warning = '该股属于成长行业，修正市赚率可能不适用（高成长需留存利润，分红率低导致N因子失真）';
    }
    return { isCyclical: isCyclical, isTech: isTech, isGrowth: isGrowth, warning: warning };
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
    var prJudge = null, roeJudge = null;
    var valuationZone = '无法判定';
    if (!isLossStock && pe != null && roe != null && roe > 0) {
      // 周期股 PB-市赚率用 5 年 ROE 中位数（对齐 Python：周期股单年 ROE 失真）
      var roeForPb = (input.is_cyclical && input.roe_5y_median != null) ? input.roe_5y_median : roe;
      prBasic = computeBasicPR(pe, roe);
      prCorrected = computeCorrectedPR(pe, roe, nFactor);
      prPb = computePbPR(pb, roeForPb);
      // 判定档位与选股器漏斗③ 同口径（基础 PR + 周期股 5 年中位数 ROE），
      // 修正 PR 仅展示（阈值 0.5/1.0/3.0 是在基础 PR 上标定的）
      var judged = judgePR(pe, roe, input.roe_5y_median, !!input.is_cyclical);
      prJudge = judged.pr;
      roeJudge = judged.roe;
      valuationZone = judged.zone;
    }

    return {
      pr_basic: prBasic,
      pr_corrected: prCorrected,
      pr_pb: prPb,
      valuation_zone: valuationZone,
      pr_judge: prJudge,
      roe_judge: roeJudge,
      roe_period: roePeriod != null ? String(roePeriod) + '年报' : null,
      payout_ratio: payoutRatio,
      n_factor: nFactor,
      is_loss_stock: isLossStock,
    };
  }

  /* ROE 取值规则（对齐 Python compute_tsr 内部逻辑）：周期股取 5 年中位数，缺失回退最新年报。
   * 导出供页面渲染复用，防止 ROE 选择规则在计算端与展示端漂移 */
  function selectRoe(roeLatest, roe5yMedian, isCyclical) {
    return (isCyclical && roe5yMedian != null) ? roe5yMedian : roeLatest;
  }

  /* 估值不变下的股东总回报率（对齐 Python pr_calculator.compute_tsr，双端一致） */
  function computeTsr(roeLatest, roe5yMedian, isCyclical, realYieldPct, pb) {
    var roe = selectRoe(roeLatest, roe5yMedian, isCyclical);
    if (roe == null || realYieldPct == null || pb == null || pb <= 0) return null;
    return Math.round(((roe - realYieldPct * pb) + realYieldPct) * 100) / 100;
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
    '公用事业', '电力行业', '水务', '燃气', '高速公路', '铁路', '港口', '机场',
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

    /* #30 股息增长率：仅展示，不参与评分（所有分支统一在此挂载，含未触发/致命红旗早退） */
    result.dividend_cagr = (history.cagr != null) ? history.cagr : null;
    result.dividend_trend = history.trend || null;
    result.dividend_cagr_years = history.cagr_years || 0;
    result.dividend_cagr_note = history.cagr_note || null;
    result.dividend_cagr_annual = history.cagr_annual != null ? history.cagr_annual : null;

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
    reportTime: reportTime,
    calculateDividendYield: calculateDividendYield,
    isImplemented: isImplemented,
    toChartDividendRecords: toChartDividendRecords,
    parseDividendRecords: parseDividendRecords,
    computeDividendYields: computeDividendYields,
    buildExDivMarkers: buildExDivMarkers,
    computeTtmDividend: computeTtmDividend,
    parseFinancials: parseFinancials,
    computeForwardYield: computeForwardYield,
    computeCashReturn: computeCashReturn,
    aggregateRepurchase: aggregateRepurchase,
    classifyRepurchasePurpose: classifyRepurchasePurpose,
    computeBasicPR: computeBasicPR,
    computeCorrectedPR: computeCorrectedPR,
    computePbPR: computePbPR,
    computeNFactor: computeNFactor,
    computeTsr: computeTsr,
    selectRoe: selectRoe,
    judgePR: judgePR,
    classifyValuation: classifyValuation,
    classifyIndustry: classifyIndustry,
    computePr: computePr,
    isST: isST,
    stWarning: stWarning,
    goodwillWarning: goodwillWarning,
    assessSustainability: assessSustainability,
    explainSustainability: explainSustainability,
    aggregateDividendHistory: _aggregateDividendHistory,
    computeDividendCagr: computeDividendCagr,
    round2: round2,
    CYCLICAL_INDUSTRIES: CYCLICAL_INDUSTRIES,
    TECH_INDUSTRIES: TECH_INDUSTRIES,
    GROWTH_INDUSTRIES: GROWTH_INDUSTRIES,
    DEFENSIVE_INDUSTRIES: DEFENSIVE_INDUSTRIES,
    FINANCE_INDUSTRIES: FINANCE_INDUSTRIES,
  };
}));
