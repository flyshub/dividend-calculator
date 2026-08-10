"""
API层 — 薄 facade，委托给 DataSourceManager 和 mootdx 适配器

get_stock_info: 委托给 DataSourceManager（自动降级）
get_historical_data: 月度价格 + 分红记录聚合（尚未提取到 adapter）
"""
import logging
import math
from typing import Optional, Tuple

import requests

from .datasource.base import StockInfo, MonthlyPrice, DividendRecord, HistoricalData
from .datasource import get_data_source_manager
from .datasource.validation import check_stock_info
from .tencent_quote import fetch_tencent_quote
from .utils import normalize_stock_code, infer_fiscal_year

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────
# 股票信息获取（委托 DataSourceManager）
# ────────────────────────────────────────────────────────────────

def get_stock_info(stock_input: str) -> Optional[StockInfo]:
    """
    获取股票基本信息，委托 DataSourceManager 自动降级

    DataSourceManager 按优先级尝试：腾讯 → 新浪 → mootdx
    """
    stock_code = normalize_stock_code(stock_input)
    if not (stock_code.isdigit() and len(stock_code) == 6):
        logger.error("无效的股票代码: %s (原始输入: %s)", stock_code, stock_input)
        return None

    try:
        manager = get_data_source_manager()
        info = manager.get_stock_info(stock_code)
        if info is not None:
            logger.info("通过数据源管理器获取 %s 成功", stock_code)
            # 数据完整性软校验（审查 #4）：越界只追加 warning，不否决
            info.warnings.extend(check_stock_info(info))
            return info
    except Exception as e:
        logger.warning("数据源管理器获取 %s 失败: %s", stock_code, e)

    logger.error("所有数据源均无法获取 %s 的完整信息", stock_code)
    return None


# ────────────────────────────────────────────────────────────────
# 月度K线数据（走势图用，尚未提取到 adapter）
# ────────────────────────────────────────────────────────────────

def _get_monthly_prices_mootdx(stock_code: str) -> list:
    """通过 mootdx 通达信协议获取月度收盘价（前复权），全球可用"""
    try:
        from .datasource.mootdx_source import get_quotes_client
        client = get_quotes_client()
        df = client.bars(symbol=stock_code, frequency=6, offset=120)
        if df is None or df.empty:
            return []

        results = []
        for _, row in df.iterrows():
            try:
                date_str = str(row.get('datetime', ''))[:10]
                close = float(row['close'])
                if close <= 0:
                    continue
                results.append(MonthlyPrice(date=date_str, close=close))
            except (ValueError, KeyError, TypeError):
                continue

        results.sort(key=lambda r: r.date)
        logger.debug("mootdx 获取月度价格 %s: %d 条", stock_code, len(results))
        return results
    except Exception as e:
        logger.warning("mootdx 获取月度价格失败 %s: %s", stock_code, e)
        return []


def _get_monthly_prices_tencent(stock_code: str) -> list:
    """通过腾讯 K 线接口获取月度收盘价（前复权），不依赖东方财富，全球可用"""
    try:
        prefix = "sh" if stock_code.startswith("6") else "sz"
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        params = {"param": f"{prefix}{stock_code},month,,,120,qfq"}
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://gu.qq.com/",
        }
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        if resp.status_code != 200:
            logger.warning("腾讯K线接口返回非200: %s", resp.status_code)
            return []

        data = resp.json()
        if data.get("code") != 0:
            logger.warning("腾讯K线接口返回错误: %s", data.get("msg", ""))
            return []

        key = f"{prefix}{stock_code}"
        stock_data = data.get("data", {}).get(key, {})
        rows = stock_data.get("qfqmonth", [])
        if not rows:
            logger.warning("腾讯K线接口无 qfqmonth 数据: %s", stock_code)
            return []

        results = []
        for row in rows:
            try:
                results.append(MonthlyPrice(date=row[0], close=float(row[2])))
            except (ValueError, IndexError, TypeError):
                continue

        logger.debug("腾讯K线获取月度价格 %s: %d 条", stock_code, len(results))
        return results
    except Exception as e:
        logger.warning("腾讯K线获取月度价格失败 %s: %s", stock_code, e)
        return []


def _get_monthly_prices(stock_code: str) -> list:
    """获取近120个月（10年）月度收盘价（前复权），多数据源自动降级"""
    results = _get_monthly_prices_tencent(stock_code)
    if results:
        return results

    results = _get_monthly_prices_mootdx(stock_code)
    if results:
        return results

    logger.warning("所有数据源均无法获取 %s 的月度价格", stock_code)
    return []


# ────────────────────────────────────────────────────────────────
# 分红记录（走势图用，尚未提取到 adapter）
# ────────────────────────────────────────────────────────────────

def _get_all_dividend_records(stock_code: str) -> Tuple[list, str]:
    """获取全部分红记录（含除权除息日），按除权日升序。

    返回 (records, source)，source 为实际数据来源（"东财" / "mootdx xdxr" / "无"），
    供调用方如实标注（数据铁律：来源可追溯）。

    主：东财分红明细（RPT_SHAREBONUS_DET，报告期口径，与 site/js datasources.js 同源，
    财年按报告期判定 month==12）；mootdx xdxr 兜底（仅含已除权记录，fenhong 浮点 round(4)）。
    issue #77：TTM 主源切换后东财升主。

    注意：此链路服务于走势图数据链路（historical-data）与 TTM 股息率，复用 sustainability 模块的
    东财取数能力，本身不属于股息可持续性功能的 spec 范围，属独立的健壮性增强。
    """
    # 主：东财分红明细（与 site/js datasources.js 同源，浏览器可直连）
    try:
        from .eastmoney_fetcher import fetch_dividend_rows
        from .sustainability import parse_dividend_rows
        rows = fetch_dividend_rows(stock_code)
        if rows is None:
            # 网络/HTTP 取数失败（#38 M5 语义），不短路，落入 mootdx 兜底
            raise ConnectionError("东财分红接口取数失败")
        if not rows:
            # 请求成功但真无分红——直接返回，无需兜底（避免把无分红公司误判为取数失败）
            return [], "东财"
        records, _ = parse_dividend_rows(rows)
        records.sort(key=lambda r: r.ex_dividend_date or "")
        logger.debug("通过东财获取分红记录 %s: %d 条", stock_code, len(records))
        return records, "东财"
    except Exception as e:
        logger.warning("东财获取分红记录失败 %s: %s", stock_code, e)

    # 兜底：mootdx xdxr（通达信协议，仅含已除权记录）
    try:
        from .datasource.mootdx_source import get_quotes_client
        client = get_quotes_client()
        xdxr_df = client.xdxr(symbol=stock_code)
        if xdxr_df is not None and not xdxr_df.empty:
            xdxr_df = xdxr_df[xdxr_df['category'] == 1]
            if not xdxr_df.empty:
                results = []
                for _, row in xdxr_df.iterrows():
                    try:
                        y, m, d = int(row['year']), int(row['month']), int(row['day'])
                    except (ValueError, KeyError):
                        continue

                    fenhong = round(float(row.get('fenhong', 0) or 0), 4)
                    # NaN 防护（#34 M1）：NaN <= 0 为 False 会穿透导致 json.dumps 输出非法 JSON
                    if math.isnan(fenhong) or fenhong <= 0:
                        continue

                    ex_div_date = f"{y:04d}-{m:02d}-{d:02d}"
                    result = infer_fiscal_year(y, m)
                    report_time = result.report_time
                    results.append(DividendRecord(
                        ex_dividend_date=ex_div_date,
                        dividend_per_10=fenhong,
                        report_time=report_time,
                    ))

                if results:
                    results.sort(key=lambda r: r.ex_dividend_date)
                    logger.debug("mootdx 兜底获取分红记录 %s: %d 条", stock_code, len(results))
                    return results, "mootdx xdxr"
    except Exception as e:
        logger.warning("mootdx 获取分红记录失败 %s: %s", stock_code, e)

    return [], "无"


# ────────────────────────────────────────────────────────────────
# 走势图数据聚合
# ────────────────────────────────────────────────────────────────

def get_historical_data(stock_input: str) -> Optional[HistoricalData]:
    """获取历史走势数据（月度收盘价 + 分红记录）"""
    stock_code = normalize_stock_code(stock_input)
    if not (stock_code.isdigit() and len(stock_code) == 6):
        logger.error("无效的股票代码: %s", stock_code)
        return None

    quote = fetch_tencent_quote(stock_code, timeout=5)
    stock_name = quote.name if quote else None

    monthly_prices = _get_monthly_prices(stock_code)
    dividend_records, _ = _get_all_dividend_records(stock_code)

    if not monthly_prices:
        logger.warning("无法获取 %s 的月度价格数据", stock_code)
        return None

    return HistoricalData(
        stock_code=stock_code,
        stock_name=stock_name,
        monthly_prices=monthly_prices,
        dividend_records=dividend_records,
    )
