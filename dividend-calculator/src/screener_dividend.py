"""选股器真实股息率（spec #67，工单 #71）。

四级漏斗的第二层：对通过漏斗①的候选池逐股计算真实股息率 + TTM 股息率，
写 dividend_snapshot，漏斗② 筛 真实股息率 > 阈值。

复用 calculate_true_dividend_yield（完整财年口径，支持注入 provider 便于测试）。
TTM 股息率（近12个月）并存快照，供漏斗① 完整判定（TTM >5%）。
"""
from typing import Callable, Dict, List, Optional

from src.dividend import DividendResult, calculate_true_dividend_yield
from src.screener_cache import DividendSnapshot, ScreenerCache

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
) -> List[DividendSnapshot]:
    """对候选池逐股计算股息，写 dividend_snapshot，返回全部快照。

    逐股拉取受限流控制（RateLimiter），避免触发数据源限流。
    增量复用：已有且未过期的 dividend_snapshot 直接复用，不重拉。
    """
    from src.screener_rate_limit import batch_wait
    snapshots: List[DividendSnapshot] = []
    for code in codes:
        # 增量复用：缓存未过期则跳过重拉
        existing = cache.get_dividend(code)
        if existing is not None and not cache.is_dividend_stale(code):
            snapshots.append(existing)
            continue
        batch_wait()  # 限流：控制请求间隔
        result = compute_dividend(code, calc_provider=calc_provider)
        if result is None:
            continue
        snap = to_dividend_snapshot(result)
        cache.upsert_dividend(snap)
        snapshots.append(snap)
    return snapshots


def compute_real_yield(total_dividend: Optional[float], market_cap: Optional[float]) -> Optional[float]:
    """真实股息率 = 分红总额 / 当前总市值 × 100（实时，随市值每日变化）。"""
    if total_dividend is None or market_cap is None or market_cap <= 0:
        return None
    return (total_dividend / market_cap) * 100


def screen_real_yield(
    snapshots: List[DividendSnapshot],
    market_caps: Optional[Dict[str, float]] = None,
    min_real: float = 5.0,
    min_ttm: float = 5.0,
) -> List[DividendSnapshot]:
    """漏斗②：真实股息率 > min_real 且 TTM > min_ttm（两级都过）。

    market_caps: {code: 当日市值}。提供时实时重算股息率（分红总额/当日市值），
    否则用存储的 real_yield（月频拉取时的旧值）。
    """
    result = []
    for s in snapshots:
        if market_caps and s.code in market_caps:
            real = compute_real_yield(s.total_dividend, market_caps[s.code])
            ttm = compute_real_yield(s.ttm_dividend, market_caps[s.code])
        else:
            real, ttm = s.real_yield, s.ttm_yield
        if real is not None and real > min_real and ttm is not None and ttm > min_ttm:
            result.append(s)
    return result
