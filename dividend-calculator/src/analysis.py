"""股票综合分析流水线模块。

封装 get_stock_info → get_latest_full_year_dividend → calculate_pr → assess_sustainability
四步编排，CLI 和 Web 各自成为薄适配器。
"""

import logging
from dataclasses import dataclass
from typing import Optional

from .api import get_stock_info
from .datasource.base import StockInfo
from .dividend import get_latest_full_year_dividend
from .pr import calculate_pr, PRResult
from .sustainability import assess_with_auto_fetch
from .sustainability_calculator import SustainabilityResult, THRESHOLD_YIELD

logger = logging.getLogger(__name__)


@dataclass
class StockAnalysisResult:
    """股票综合分析的完整结果。"""
    stock_info: StockInfo
    dividend_total: float
    dividend_yield_before_tax: Optional[float]   # 税前股息率（百分数），无有效分红为 None
    pr_result: PRResult
    sustainability: Optional[SustainabilityResult] = None  # 仅股息率 > 阈值时评估，否则 None


def run_stock_analysis(stock_input: str) -> Optional[StockAnalysisResult]:
    """执行股票综合分析流水线。

    依次获取股票基本信息、分红数据、市赚率，
    当税前股息率 > 可持续性阈值时追加可持续性评估，
    三步中任一步失败即返回 None。

    CLI 和 Web 路径共用一个编排入口，
    将来加第三个入口（定时任务/通知）零成本。
    """
    stock_info = get_stock_info(stock_input)
    if stock_info is None:
        logger.error("无法获取股票信息: %s", stock_input)
        return None

    stock_code = stock_info.stock_code
    dividend_total, dividend_year, _, _ = get_latest_full_year_dividend(stock_code, stock_info)

    pr_result = calculate_pr(
        stock_code=stock_code,
        dividend_total=dividend_total if dividend_total > 0 else None,
        stock_info=stock_info,
    )

    # 税前股息率
    dividend_yield = None
    market_cap = stock_info.current_price * stock_info.total_shares
    if market_cap > 0 and dividend_total > 0:
        dividend_yield = dividend_total / market_cap * 100

    # 可持续性评估：仅高股息率触发（避免低股息股浪费网络请求）
    sustainability = None
    if dividend_yield is not None and dividend_yield > THRESHOLD_YIELD:
        try:
            sustainability = assess_with_auto_fetch(
                stock_code=stock_code,
                total_shares=stock_info.total_shares,
                dividend_total=dividend_total,
                dividend_yield_before_tax=dividend_yield,
                latest_dividend_year=dividend_year,
                industry=pr_result.industry,
            )
        except Exception as e:
            logger.warning("可持续性评估失败 %s: %s", stock_code, e)

    return StockAnalysisResult(
        stock_info=stock_info,
        dividend_total=dividend_total,
        dividend_yield_before_tax=dividend_yield,
        pr_result=pr_result,
        sustainability=sustainability,
    )
