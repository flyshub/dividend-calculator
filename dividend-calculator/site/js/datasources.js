/* 数据源层 — 浏览器与 Node(verify) 共用。
 * 全部接口已验证支持 CORS（腾讯 qt/ifzq、东财 datacenter/emweb）。
 * 腾讯行情为 GBK 编码，需 arrayBuffer + TextDecoder('gbk') 解码。 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.DataSources = factory();
  }
}(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  var IS_NODE = typeof process !== 'undefined' && process.versions && process.versions.node;
  var G = typeof self !== 'undefined' ? self
    : (typeof window !== 'undefined' ? window
      : (typeof globalThis !== 'undefined' ? globalThis : {}));

  function gbkDecode(arrayBuffer) {
    return new TextDecoder('gbk').decode(arrayBuffer);
  }

  function jsonFetch(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status + ' ' + url);
      return r.json();
    });
  }

  /* ── 腾讯行情（qt.gtimg.cn，GBK）──
   * 字段索引与 tencent_quote.py 对齐:
   *   1=名称 3=价格 33=PE-TTM 46=PB 72=A股股本 73=总股本 */
  function fetchTencentQuote(stockCode) {
    var prefix = stockCode[0] === '6' ? 'sh' : 'sz';
    var url = 'https://qt.gtimg.cn/q=' + prefix + stockCode;
    return fetch(url).then(function (r) { return r.arrayBuffer(); }).then(function (buf) {
      var text = gbkDecode(buf);
      var m = /"([^"]*)"/.exec(text);
      if (!m) throw new Error('腾讯行情解析失败: ' + stockCode);
      var f = m[1].split('~');
      if (f.length < 4) throw new Error('腾讯行情字段不足: ' + stockCode);
      return {
        stock_code: stockCode,
        name: f[1] || null,
        price: safeFloat(f, 3),
        pe_ttm: safeFloat(f, 33),
        pb: safeFloat(f, 46),
        a_shares: safeFloat(f, 72),
        total_shares: safeFloat(f, 73),
      };
    });
  }

  /* ── 腾讯月度K线（web.ifzq.gtimg.cn，前复权）──
   * 返回 [{date:'YYYY-MM-DD', close, price}] 升序 */
  function fetchMonthlyPrices(stockCode, months) {
    months = months || 36;
    var prefix = stockCode[0] === '6' ? 'sh' : 'sz';
    var url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=' + prefix + stockCode +
      ',month,,,' + months + ',qfq';
    return jsonFetch(url).then(function (d) {
      var data = d.data || {};
      var key = prefix + stockCode;
      var node = data[key] || {};
      var rows = node.qfqmonth || node.month || [];
      return rows.map(function (r) {
        return { date: String(r[0]), close: Number(r[2]) };
      });
    });
  }

  /* ── 东财分红明细（RPT_SHAREBONUS_DET，每10股派息口径）── */
  function fetchDividendRecords(stockCode) {
    var url = 'https://datacenter-web.eastmoney.com/api/data/v1/get?sortColumns=REPORT_DATE&sortTypes=-1' +
      '&pageSize=100&pageNumber=1&reportName=RPT_SHAREBONUS_DET&columns=ALL' +
      '&filter=(SECURITY_CODE%3D%22' + stockCode + '%22)';
    return jsonFetch(url).then(function (d) {
      var result = d.result;
      return (result && result.data) || [];
    });
  }

  /* ── 东财财务（RPT_F10_FINANCE_MAINFINADATA，ROEJQ/PARENTNETPROFIT）── */
  function fetchFinancials(stockCode) {
    var market = stockCode[0] === '6' ? '.SH' : '.SZ';
    var secucode = stockCode + market;
    var url = 'https://datacenter.eastmoney.com/api/data/v1/get?sortColumns=REPORT_DATE&sortTypes=-1' +
      '&pageSize=100&pageNumber=1&reportName=RPT_F10_FINANCE_MAINFINADATA&columns=ALL' +
      '&filter=(SECUCODE%3D%22' + secucode + '%22)';
    return jsonFetch(url).then(function (d) {
      var result = d.result;
      return (result && result.data) || [];
    });
  }

  /* ── 东财现金流量表（RPT_F10_FINANCE_GCASHFLOW，含 CONSTRUCT_LONG_ASSET 资本开支）── */
  function fetchCashflow(stockCode) {
    var market = stockCode[0] === '6' ? '.SH' : '.SZ';
    var secucode = stockCode + market;
    var url = 'https://datacenter.eastmoney.com/api/data/v1/get?sortColumns=REPORT_DATE&sortTypes=-1' +
      '&pageSize=100&pageNumber=1&reportName=RPT_F10_FINANCE_GCASHFLOW&columns=ALL' +
      '&filter=(SECUCODE%3D%22' + secucode + '%22)';
    return jsonFetch(url).then(function (d) {
      var result = d.result;
      return (result && result.data) || [];
    });
  }

  /* ── 东财行业（datacenter RPT_F10_BASIC_ORGINFO，CORS ✓）
   * EM2016 为东财行业（如"公用事业-电力-水电"），INDUSTRYCSRC1 为证监会分类。
   * 优先 EM2016（含简洁行业词，利于关键字匹配）。 */
  function fetchIndustry(stockCode) {
    var market = stockCode[0] === '6' ? '.SH' : '.SZ';
    var url = 'https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_F10_BASIC_ORGINFO' +
      '&columns=ALL&filter=(SECUCODE%3D%22' + stockCode + market + '%22)';
    return jsonFetch(url).then(function (d) {
      var rows = (d.result && d.result.data) || [];
      if (!rows.length) throw new Error('行业数据为空: ' + stockCode);
      var row = rows[0];
      var industry = row.EM2016 || row.INDUSTRYCSRC1 || '';
      if (!industry) throw new Error('行业数据为空: ' + stockCode);
      return industry;
    });
  }

  /* ── 近1年股价涨跌幅（腾讯 fqkline 前复权日K，250根）──
   * 返回小数（如 -0.30 表示跌 30%），失败返回 null（评估降级继续） */
  function fetchPriceChange1y(stockCode) {
    var prefix = stockCode[0] === '6' ? 'sh'
      : (stockCode[0] === '8' || stockCode[0] === '4' || stockCode.slice(0, 2) === '92' ? 'bj' : 'sz');
    var url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=' + prefix + stockCode + ',day,,,250,qfq';
    return jsonFetch(url).then(function (d) {
      var data = d.data || {};
      var node = data[prefix + stockCode] || {};
      var rows = node.qfqday || node.day || [];
      if (!rows || rows.length < 2) return null;
      /* #40：实测请求 250 根返回 251 根，rows[0] 即约 1 年前的窗口起点，最后一行即最新。
       * 用首尾两行算一年涨跌幅——按索引 245 会取到仅 1 周前，把一年窗口算成周涨跌。 */
      var last = Number(rows[rows.length - 1][2]);
      var past = Number(rows[0][2]);
      if (!isFinite(last) || !isFinite(past) || past <= 0) return null;
      return (last - past) / past;
    }).catch(function () { return null; });
  }

  /* ── 前十大股东合计持股比例（东财 RPT_F10_EH_HOLDERS）──
   * HOLD_NUM_RATIO 为百分数（如 12.5 = 12.5%），求和后 /100 转小数（如 0.62）。
   * 对齐 Python sustainability_calculator: top10_holding 为小数（阈值 0.50，显示 ×100%）。
   * 数据缺失/全部为空值 → 返回 null（与 Python total==0 返回 None 一致，避免 0 参与集中度判分）。 */
  function fetchTop10Holding(stockCode) {
    var suffix = stockCode[0] === '6' ? 'SH'
      : (stockCode[0] === '8' || stockCode[0] === '4' || stockCode.slice(0, 2) === '92' ? 'BJ' : 'SZ');
    var url = 'https://datacenter.eastmoney.com/securities/api/data/v1/get?reportName=RPT_F10_EH_HOLDERS' +
      '&columns=ALL&filter=(SECUCODE%3D%22' + stockCode + '.' + suffix + '%22)' +
      '&pageNumber=1&pageSize=10&sortTypes=-1&sortColumns=END_DATE';
    return jsonFetch(url).then(function (d) {
      var rows = (d.result && d.result.data) || [];
      if (!rows.length) return null;
      var sum = 0;
      for (var i = 0; i < rows.length; i++) {
        var v = Number(rows[i].HOLD_NUM_RATIO);
        if (isFinite(v)) sum += v;
      }
      if (!(sum > 0)) return null;
      return sum / 100.0;
    }).catch(function () { return null; });
  }

  /* ── 股票名称→代码（smartbox.gtimg.cn）
   * 浏览器: script 标签 JSONP（读取全局 v_hint）
   * Node: 直接 fetch + 解析 \u 转义 */
  function lookupStockCodeByName(name) {
    var url = 'https://smartbox.gtimg.cn/s3/?q=' + encodeURIComponent(name) + '&t=all';
    if (IS_NODE) {
      return fetch(url).then(function (r) { return r.text(); }).then(parseSmartbox);
    }
    return new Promise(function (resolve, reject) {
      var prev = G.v_hint;
      var script = document.createElement('script');
      script.src = url;
      var timeout = setTimeout(function () {
        script.remove();
        G.v_hint = prev;
        reject(new Error('名称搜索超时: ' + name));
      }, 10000);
      script.onload = function () {
        clearTimeout(timeout);
        script.remove();
        var raw = G.v_hint;
        G.v_hint = prev;
        try {
          resolve(parseSmartbox('v_hint=' + JSON.stringify(raw)));
        } catch (e) { reject(e); }
      };
      script.onerror = function () {
        clearTimeout(timeout);
        script.remove();
        G.v_hint = prev;
        reject(new Error('名称搜索失败: ' + name));
      };
      document.head.appendChild(script);
    });
  }

  /* v_hint="sh~600900~\u957f\u6c5f\u7535\u529b~cjdl~GP-A" */
  function parseSmartbox(text) {
    var m = /"([^"]*)"/.exec(text);
    if (!m) throw new Error('smartbox 响应无法解析');
    var raw = m[1];
    var decoded = raw.replace(/\\u([0-9a-fA-F]{4})/g, function (_, h) {
      return String.fromCharCode(parseInt(h, 16));
    });
    var parts = decoded.split('~');
    if (parts.length < 3) throw new Error('smartbox 未找到匹配');
    return {
      market: parts[0],
      code: parts[1],
      name: parts[2],
    };
  }

  function safeFloat(fields, idx) {
    if (idx >= fields.length) return null;
    var v = Number(fields[idx]);
    return isFinite(v) && v > 0 ? v : null;
  }

  return {
    fetchTencentQuote: fetchTencentQuote,
    fetchMonthlyPrices: fetchMonthlyPrices,
    fetchDividendRecords: fetchDividendRecords,
    fetchFinancials: fetchFinancials,
    fetchCashflow: fetchCashflow,
    fetchIndustry: fetchIndustry,
    fetchPriceChange1y: fetchPriceChange1y,
    fetchTop10Holding: fetchTop10Holding,
    lookupStockCodeByName: lookupStockCodeByName,
    parseSmartbox: parseSmartbox,
    IS_NODE: IS_NODE,
  };
}));
