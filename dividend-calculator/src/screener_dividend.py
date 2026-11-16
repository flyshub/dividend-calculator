"""选股器真实股息率数据获取（spec #67，工单 #71）。

对候选池逐股计算真实股息率 + TTM 股息率并写 dividend_snapshot（缓存层）。
复用 calculate_true_dividend_yield（完整财年口径，支持注入 provider 便于测试）。
漏斗② 的判定与降级回退已收进 src.screening（ADR-0001），本模块只负责取数与缓存。
"""
import sys
from typing import Callable, List, Optional

from src.dividend import DividendResult, calculate_true_dividend_yield
from src.screener_cache import DividendSnapshot, ScreenerCache
from src.screener_fetch import (
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    FetchPolicy,
    fetch_each,
)

# 单批容错：连续 N 只取数失败视为数据源不可用（实现收在 screener_fetch）
MAX_CONSECUTIVE_FAILURES = DEFAULT_MAX_CONSECUTIVE_FAILURES

# calculate_true_dividend_yield 的 provider 类型
DivProvider = Optional[
    Callable[[str, str], Optional[DividendResult]]
]


def compute_dividend(
    code: str,
    *,
    calc_provider: Optional[Callable[[str], Optional[DividendResult]]] = None,
) -> Optional[DividendResult]:
    """单股真实股息率计算。calc_provider 注入便于测试（默认走真实数据源）。"""
    if calc_provider:
        return calc_provider(code)
    return calculate_true_dividend_yield(code)


def to_dividend_snapshot(result: DividendResult, source: str = "mootdx") -> DividendSnapshot:
    """DividendResult → 缓存快照（存分红总额，股息率筛选时按当日市值实时重算）。"""
    return DividendSnapshot(
        code=result.stock_code,
        real_yield=result.dividend_yield_before_tax,
        ttm_yield=result.dividend_yield_ttm_before_tax,
        real_yield_year=result.latest_year,
        ttm_period=result.ttm_period,
        total_dividend=result.total_dividend,
        ttm_dividend=getattr(result, 'ttm_dividend', None),
        dividend_source=result.dividend_source or source,
    )


def compute_dividends_for_candidates(
    codes: List[str],
    cache: ScreenerCache,
    *,
    calc_provider: Optional[Callable[[str], Optional[DividendResult]]] = None,
    fetch_timeout: float = 90.0,
    max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES,
) -> List[DividendSnapshot]:
    """对候选池逐股计算股息，写 dividend_snapshot，返回全部快照。

    逐股拉取受限流控制（RateLimiter），避免触发数据源限流。
    增量复用：已有且未过期的 dividend_snapshot 直接复用，不重拉。
    容错：单只失败（超时/数据源异常）记日志跳过，连续 N 只失败则中止本轮
    ——判据实现收在 src.screener_fetch（与财务/可持续性共用一份）。
    """
    from src.screener_rate_limit import batch_wait

    def cached(code: str) -> Optional[DividendSnapshot]:
        # 增量复用：缓存未过期且 total_dividend 完整则跳过重拉。
        # total_dividend 缺失（如旧 DB 迁移 ALTER TABLE 补列后未回填）→ 强制重拉，
        # 否则股息率实时化逻辑永远拿不到分红总额（#82）。
        existing = cache.get_dividend(code)
        if (
            existing is not None
            and not cache.is_dividend_stale(code)
            and existing.total_dividend is not None
        ):
            return existing
        return None

    def save(result: DividendResult) -> DividendSnapshot:
        snap = to_dividend_snapshot(result)
        cache.upsert_dividend(snap)
        return snap

    outcome = fetch_each(
        codes,
        lambda code: compute_dividend(code, calc_provider=calc_provider),
        label="股息",
        policy=FetchPolicy(
            timeout_s=fetch_timeout,
            max_consecutive_failures=max_consecutive_failures,
        ),
        cached=cached,
        is_empty=lambda result: result is None,
        save=save,
        before_request=batch_wait,  # 限流：控制请求间隔
    )
    return outcome.items
