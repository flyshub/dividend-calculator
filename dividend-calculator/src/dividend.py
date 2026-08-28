"""
股息率计算逻辑 - 核心计算模块
完全使用真实数据，不虚构任何数据
"""
import logging
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple, List

from .datasource.base import StockInfo, DividendDetail
from .datasource.validation import check_dividend_yield
from .api import get_stock_info
from .tencent_quote import fetch_tencent_quote
from .datasource import get_data_source_manager
from .utils import get_stock_list_cache, compute_ttm_dividend
from .dividend_records import summarize_fhps_df, summarize_cninfo_df, summarize_dividend_rows

logger = logging.getLogger(__name__)


@dataclass
class DividendResult:
    """股息率计算结果"""
    stock_code: str
    stock_name: Optional[str]
    current_price: float
    total_shares: float
    total_market_cap: float
    total_dividend: float
    dividend_yield_before_tax: float
    dividend_yield_after_tax: float       # 持股1月~1年，扣税10%
    dividend_yield_after_tax_20: float    # 持股不足1月，扣税20%
    latest_year: Optional[str]
    dividend_details: List[DividendDetail]
    explanation: str
    warnings: List[str] = field(default_factory=list)  # 数据完整性软校验（审查 #4）
    dividend_source: str = ""  # 分红数据来源（mootdx / akshare fhps_detail_em / akshare cninfo，#16）
    # TTM 口径（近12个月实际派发，#19）：与主口径（最近完整财年）并行，纯增量
    ttm_dividend: Optional[float] = None            # TTM 现金分红总额（元）
    dividend_yield_ttm_before_tax: Optional[float] = None  # TTM 税前股息率（%）
    ttm_period: Optional[str] = None                # TTM 期间 "起-止"（YYYY-MM-DD）
    ttm_source: str = ""                            # TTM 数据来源（东财 / mootdx xdxr 兜底）


def calculate_dividend_yield(
    total_dividend: float,
    total_market_cap: float
) -> Tuple[float, float, float]:
    """
    核心计算逻辑：计算股息率

    Args:
        total_dividend: 近一年现金分红总额（元）
        total_market_cap: 当前总市值（元）

    Returns:
        (含税/持股1年以上免税%, 持股1月~1年扣税10%后%, 持股不足1月扣税20%后%)
    """
    if total_market_cap <= 0:
        return 0.0, 0.0, 0.0

    dividend_yield_before_tax = (total_dividend / total_market_cap) * 100
    dividend_yield_after_tax = dividend_yield_before_tax * 0.9
    dividend_yield_after_tax_20 = dividend_yield_before_tax * 0.8

    return dividend_yield_before_tax, dividend_yield_after_tax, dividend_yield_after_tax_20


def get_latest_full_year_dividend(
    stock_code: str, stock_info: StockInfo
) -> Tuple[float, Optional[str], List[DividendDetail], str, str]:
    """
    获取最近一个完整财年的现金分红总额和明细
    多数据源自动降级：akshare fhps_detail_em（对齐 JS 财年判定）→ akshare cninfo → mootdx

    优先级说明（修复 #600662）：akshare fhps_detail_em 按报告期月份判定财年
    （12月=年报，与 JS calculator.js 一致），且过滤「未实施」预案；mootdx xdxr
    只含已除权记录，会导致未除权的最新年报（如 600662 2025 年报除权日在未来）
    财年滞后一年。故 akshare 优先，mootdx 降级兜底。

    issue #97：①② 改走 dividend_records 各源 adapter（summarize_fhps_df /
    summarize_cninfo_df），解析口径与 TTM/走势图统一（财年判定单一实现
    sustainability.classify_fiscal_report）；旧 dividend._parse_fhps_detail 已删除（#100）。
    ③ mootdx 兜底保持 DataSourceManager 不变。
    """
    # 方式1: akshare fhps_detail_em（按报告期判定财年，对齐 JS，数据最全）
    try:
        import akshare as ak
        fhps_df = ak.stock_fhps_detail_em(symbol=stock_code)
        if not fhps_df.empty:
            summary = summarize_fhps_df(fhps_df)
            if summary.fiscal_total_per_10 > 0 and summary.latest_year:
                total_div, year, details, expl = _summary_to_dividend(summary, stock_info)
                logger.info("通过akshare fhps_detail_em获取分红数据成功: %s %s年", stock_code, year)
                return total_div, year, details, expl, "akshare fhps_detail_em"
    except Exception as e:
        logger.debug("akshare fhps_detail_em 获取分红失败: %s", e)

    # 方式2: akshare cninfo 分红数据（兜底）
    try:
        import akshare as ak
        dividend_df = ak.stock_dividend_cninfo(symbol=stock_code)
        if not dividend_df.empty:
            summary = summarize_cninfo_df(dividend_df)
            if summary.fiscal_total_per_10 > 0 and summary.latest_year:
                total_div, year, details, expl = _summary_to_dividend(summary, stock_info)
                logger.info("通过akshare cninfo获取分红数据成功: %s %s年", stock_code, year)
                return total_div, year, details, expl, "akshare cninfo"
    except Exception as e:
        logger.debug("akshare cninfo 获取分红失败: %s", e)

    # 方式3: 数据源管理器（mootdx，仅含已除权记录，降级兜底）
    try:
        manager = get_data_source_manager()
        total_div, year, details, expl = manager.get_latest_dividend(stock_code, stock_info)
        if total_div > 0:
            logger.info("通过数据源管理器获取分红数据成功: %s %s年", stock_code, year)
            return total_div, year, details, expl, "mootdx"
    except Exception as e:
        logger.debug("数据源管理器（mootdx）获取分红失败: %s", e)

    return 0.0, None, [], "所有数据源都无法获取分红数据", "无"


def _summary_to_dividend(
    summary, stock_info: StockInfo
) -> Tuple[float, Optional[str], List[DividendDetail], str]:
    """DividendSummary → (总分红金额, 财年, 分红明细, 说明)。

    换算：total_dividend = fiscal_total_per_10 / 10 × total_shares；
    明细只取最新完整财年的记录（含该财年中期分配）；explanation 文案沿用
    迁移前格式：fhps 链路中文逗号分隔、cninfo 链路 ASCII 逗号 + 空格分隔，
    与 JS parseDividendRecords 文案逐字一致（分红条目标签统一为 #37 M4 口径
    "YYYY年报"/"YYYY中期分配"）。
    """
    year = summary.latest_year
    total_per_10 = summary.fiscal_total_per_10
    # 明细/文案只含现金分红行：排除纯送转锚点行（per10=0，走势图股本锚点用）——
    # 与 JS parseDividendRecords 的 explanation 逐字一致（东财链 records 已含锚点行，
    # fhps/cninfo 链路靠前置过滤天然不含，此处统一兜底不依赖调用方）
    year_details = [
        DividendDetail(r.report_time, r.dividend_per_10)
        for r in summary.records
        if (r.report_time or "").startswith(year) and r.dividend_per_10 > 0
    ]
    dps = total_per_10 / 10.0
    total_shares = stock_info.total_shares
    total_dividend = dps * total_shares

    dividend_list = [
        f"{d.report_time}: 10派{d.dividend_per_10}元" for d in year_details
    ]
    if summary.source == "akshare cninfo":
        # cninfo 链路文案（ASCII 逗号 + 空格分隔，沿用迁移前格式）
        explanation = (
            f"{year}年度 {', '.join(dividend_list)}, "
            f"合计10派{total_per_10:.3f}元(每股{dps:.4f}元), "
            f"总股本{total_shares / 1e8:.2f}亿股, "
            f"总分红{total_dividend / 1e8:.2f}亿元"
        )
    else:
        # fhps 链路文案（中文逗号分隔，沿用迁移前格式，与 JS 逐字一致）
        explanation = (
            f"{year}年度 {'，'.join(dividend_list)}，"
            f"合计10派{total_per_10:.3f}元(每股{dps:.4f}元)，"
            f"总股本{total_shares / 1e8:.2f}亿股，"
            f"总分红{total_dividend / 1e8:.2f}亿元"
        )

    return total_dividend, year, year_details, explanation


def get_ttm_dividend(
    stock_code: str, stock_info: StockInfo
) -> Tuple[Optional[float], Optional[str], Optional[str], str]:
    """TTM 股息率口径（#19）：近 12 个月实际派发现金分红总额。

    主：东财分红明细（fetch_dividend_rows → dividend_records.summarize_dividend_rows，
    与主股息率/走势图同一解析口径）；mootdx xdxr 兜底（东财取数失败 → xdxr，
    复用 api._get_xdxr_records）。失败返回 (None, None, None, '无')，绝不抛出。

    Returns:
        (ttm_total_div, period, count_note, source)
    """
    try:
        from .eastmoney_fetcher import fetch_dividend_rows
        rows = fetch_dividend_rows(stock_code)
        if rows is None:
            # 网络/HTTP 取数失败（#38 M5 语义），不短路，落入 mootdx 兜底
            raise ConnectionError("东财分红接口取数失败")
        if not rows:
            # 请求成功但真无分红——与旧 _get_all_dividend_records 语义一致，不兜底
            return None, None, None, "无"
        summary = summarize_dividend_rows(rows, source="东财")
        records, source = summary.records, summary.source
    except Exception as e:
        logger.debug("TTM 东财获取失败 %s: %s", stock_code, e)
        from .api import _get_xdxr_records
        records, source = _get_xdxr_records(stock_code)

    if not records:
        return None, None, None, "无"
    try:
        ttm_total, start, end, count = compute_ttm_dividend(records, stock_info.total_shares)
    except Exception as e:
        logger.debug("TTM 计算失败 %s: %s", stock_code, e)
        return None, None, None, "无"
    if ttm_total is None:
        return None, None, None, "无"
    period = f"{start}~{end}" if start and end else None
    return ttm_total, period, f"{count}次派息", source


def _get_stock_name(stock_code: str) -> Optional[str]:
    """获取股票名称（优先使用腾讯行情，快速）"""
    # 方式1: 腾讯行情（快速，不依赖东方财富）
    quote = fetch_tencent_quote(stock_code, timeout=5)
    if quote is not None and quote.name is not None:
        return quote.name

    # 方式2: akshare 缓存（较慢）
    try:
        cache = get_stock_list_cache()
        if cache is not None:
            match = cache[cache["code"] == stock_code]
            if not match.empty:
                return str(match.iloc[0]["name"])
    except Exception as e:
        logger.debug("akshare缓存获取股票名称失败 %s: %s", stock_code, e)
    return None


def calculate_true_dividend_yield(
    stock_input: str,
    *,
    stock_info_provider: Optional[Callable[[str], Optional[StockInfo]]] = None,
    dividend_provider: Optional[
        Callable[[str, StockInfo], Tuple[float, Optional[str], List[DividendDetail], str, str]]
    ] = None,
    ttm_dividend_provider: Optional[
        Callable[[str, StockInfo], Tuple[Optional[float], Optional[str], Optional[str], str]]
    ] = None,
) -> Optional[DividendResult]:
    """
    计算真实股息率。

    Args:
        stock_input: 股票代码或名称
        stock_info_provider: 股票信息获取函数（不传则使用默认 get_stock_info）
        dividend_provider: 分红数据获取函数（不传则使用默认 DataSourceManager）
            签名为 (stock_code, stock_info) → (total_div, year, details, explanation, source)
        ttm_dividend_provider: TTM 分红获取函数（#19，不传则使用默认 get_ttm_dividend）
            签名为 (stock_code, stock_info) → (ttm_total, period, count_note, source)
    """
    _stock_info = stock_info_provider or get_stock_info
    _dividend = dividend_provider or get_latest_full_year_dividend
    _ttm_dividend = ttm_dividend_provider or get_ttm_dividend

    try:
        stock_info = _stock_info(stock_input)
        if stock_info is None:
            logger.error("无法获取股票信息: %s", stock_input)
            return None

        stock_code = stock_info.stock_code
        total_market_cap = stock_info.current_price * stock_info.total_shares

        total_dividend, latest_year, dividend_details, dividend_explanation, dividend_source = (
            _dividend(stock_code, stock_info)
        )

        if total_dividend <= 0:
            return DividendResult(
                stock_code=stock_code,
                stock_name=None,
                current_price=stock_info.current_price,
                total_shares=stock_info.total_shares,
                total_market_cap=total_market_cap,
                total_dividend=0.0,
                dividend_yield_before_tax=0.0,
                dividend_yield_after_tax=0.0,
                dividend_yield_after_tax_20=0.0,
                latest_year=None,
                dividend_details=[],
                explanation=f"无有效分红: {dividend_explanation}",
                warnings=list(stock_info.warnings),
            )

        dividend_yield_before_tax, dividend_yield_after_tax, dividend_yield_after_tax_20 = calculate_dividend_yield(
            total_dividend, total_market_cap
        )

        stock_name = _get_stock_name(stock_code)

        explanation = (
            f"核心公式: 近一年现金分红总额({total_dividend / 1e8:.2f}亿元) "
            f"/ 当前总市值({total_market_cap / 1e8:.2f}亿元) "
            f"= {dividend_yield_before_tax:.2f}%(持股1年以上免税) | "
            f"{dividend_yield_after_tax:.2f}%(持股1月~1年扣税10%) | "
            f"{dividend_yield_after_tax_20:.2f}%(持股不足1月扣税20%). "
            f"{dividend_explanation}. "
            f"当前股价{stock_info.current_price:.2f}元, 总股本{stock_info.total_shares / 1e8:.2f}亿股."
        )

        # TTM 口径（#19）：并行计算，失败静默置 None，不拖垮主口径
        _ttm_total, _ttm_period, _ttm_count, _ttm_source = _ttm_dividend(stock_code, stock_info)
        _ttm_yield = None
        if _ttm_total is not None and total_market_cap > 0:
            _ttm_yield = (_ttm_total / total_market_cap) * 100

        return DividendResult(
            stock_code=stock_code,
            stock_name=stock_name,
            current_price=stock_info.current_price,
            total_shares=stock_info.total_shares,
            total_market_cap=total_market_cap,
            total_dividend=total_dividend,
            dividend_yield_before_tax=dividend_yield_before_tax,
            dividend_yield_after_tax=dividend_yield_after_tax,
            dividend_yield_after_tax_20=dividend_yield_after_tax_20,
            latest_year=latest_year,
            dividend_details=dividend_details,
            explanation=explanation,
            warnings=list(stock_info.warnings) + _yield_warnings(dividend_yield_before_tax),
            dividend_source=dividend_source,
            ttm_dividend=_ttm_total,
            dividend_yield_ttm_before_tax=_ttm_yield,
            ttm_period=_ttm_period,
            ttm_source=_ttm_source,
        )

    except Exception as e:
        logger.error("计算真实股息率异常 %s: %s", stock_input, e)
        return None


def _yield_warnings(yield_before_tax: Optional[float]) -> List[str]:
    """股息率越界软校验（审查 #4），返回 warning 列表。"""
    w = check_dividend_yield(yield_before_tax)
    return [w] if w else []
