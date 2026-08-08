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
    """DividendResult → 缓存快照（只取筛选需要的字段）。"""
    return DividendSnapshot(
        code=result.stock_code,
        real_yield=result.dividend_yield_before_tax,
        ttm_yield=result.dividend_yield_ttm_before_tax,
        real_yield_year=result.latest_year,
        ttm_period=result.ttm_period,
        dividend_source=result.dividend_source or source,
    )


def compute_dividends_for_candidates(
    codes: List[str],
    cache: ScreenerCache,
    *,
    calc_provider: Optional[Callable[[str], Optional[DividendResult]]] = None,
) -> List[DividendSnapshot]:
    """对候选池逐股计算股息，写 dividend_snapshot，返回全部快照。"""
    snapshots: List[DividendSnapshot] = []
    for code in codes:
        result = compute_dividend(code, calc_provider=calc_provider)
        if result is None:
            continue
        snap = to_dividend_snapshot(result)
        cache.upsert_dividend(snap)
        snapshots.append(snap)
    return snapshots


def screen_real_yield(
    snapshots: List[DividendSnapshot],
    min_real: float = 5.0,
    min_ttm: float = 5.0,
) -> List[DividendSnapshot]:
    """漏斗②：真实股息率 > min_real 且 TTM > min_ttm（两级都过）。"""
    return [
        s for s in snapshots
        if s.real_yield is not None and s.real_yield > min_real
        and s.ttm_yield is not None and s.ttm_yield > min_ttm
    ]
