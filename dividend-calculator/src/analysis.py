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
from .forward_yield import ForwardYieldResult, estimate_forward_yield
from .pr import calculate_pr, PRResult
from .sustainability import assess_with_auto_fetch
from .eastmoney_fetcher import fetch_goodwill
from .risk_flags import goodwill_warning
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
    forward_yield: Optional[ForwardYieldResult] = None      # 下一财年前瞻股息率（模型估算，仅展示）


def attach_goodwill_warning(sustainability: SustainabilityResult, stock_code: str) -> None:
    """#26 商誉/净资产 > 50% 时给可持续性结果追加警示（就地修改，不改评分）。

    取不到（金融报表无此表 / 无商誉）即跳过：不补 0、不推断。
    """
    gw = fetch_goodwill(stock_code)
    if not gw:
        return
    flag = goodwill_warning(gw[0], gw[1])
    if flag:
        sustainability.metrics["goodwill_equity"] = gw[0] / gw[1]
        sustainability.warning_flags.append(flag)


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
    dividend_total, dividend_year, _, _, _ = get_latest_full_year_dividend(stock_code, stock_info)

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

    # #26 商誉/净资产警示：只在高股息触发可持续性评估时复核（单股路径）。
    # 取不到（金融报表无此表/无商誉）即跳过，不补 0、不推断；不改评分与判定。
    if sustainability is not None:
        try:
            attach_goodwill_warning(sustainability, stock_code)
        except Exception as e:
            logger.warning("商誉警示计算失败 %s: %s", stock_code, e)

    # 下一财年前瞻股息率（模型估算，仅展示；失败降级为不估，不影响主结果）
    forward_yield = None
    if dividend_year:
        try:
            forward_yield = estimate_forward_yield(
                stock_code=stock_code,
                price=stock_info.current_price,
                total_shares=stock_info.total_shares,
                is_cyclical=pr_result.is_cyclical,
                trailing_yield=dividend_yield,
            )
        except Exception as e:
            logger.warning("前瞻股息率计算失败 %s: %s", stock_code, e)

    return StockAnalysisResult(
        stock_info=stock_info,
        dividend_total=dividend_total,
        dividend_yield_before_tax=dividend_yield,
        pr_result=pr_result,
        sustainability=sustainability,
        forward_yield=forward_yield,
    )
