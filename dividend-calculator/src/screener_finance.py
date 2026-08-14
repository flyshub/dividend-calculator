"""选股器财务数据获取（issue #122）。

对候选池逐股拉取财务（ROE/净利润/支付率/周期标记）写 finance_snapshot（缓存层）。
复用 pr.py::_get_financial（mootdx F10 → akshare 同花顺降级链）与 _get_industry。
漏斗③ 的判定与 PR 计算已收进 src.screening（ADR-0001），本模块只负责取数与缓存。
"""
from typing import Callable, List, Optional, Tuple

from src.screener_cache import FinanceSnapshot, ScreenerCache

# 与 pr._get_financial 同构的 provider 返回类型：
# (roe_latest, roe_5y_median, net_profit_latest_period, net_profit_annual, src, errors, roe_period)
FinancialProvider = Callable[[str], Tuple[
    Optional[float], Optional[float], Optional[float], Optional[float],
    str, List[str], Optional[int],
]]
IndustryProvider = Callable[[str], Tuple[str, str]]


def _payout_ratio(cache: ScreenerCache, code: str,
                  net_profit_annual: Optional[float]) -> Optional[float]:
    """股利支付率 = 最新财年分红总额 / 最新年报净利润。

    净利润缺失/≤0 或分红总额缺失 → None（漏斗回退基础 PR，不写假值）。
    """
    if net_profit_annual is None or net_profit_annual <= 0:
        return None
    div = cache.get_dividend(code)
    total = div.total_dividend if div else None
    if total is None or total <= 0:
        return None
    return total / net_profit_annual


def compute_finance_for_candidates(
    codes: List[str],
    cache: ScreenerCache,
    *,
    fresh_days: int = 7,
    financial_provider: Optional[FinancialProvider] = None,
    industry_provider: Optional[IndustryProvider] = None,
) -> List[FinanceSnapshot]:
    """对候选池逐股拉财务，写 finance_snapshot，返回全部快照。

    增量复用：finance_snapshot 在 fresh_days 内 → 跳过重拉（月频管线 ~30 天跑一次，
    7 天窗口保证月内重跑不重复拉取）。
    限流：batch_wait()（0.8s/只），对齐 fill_dividends。
    """
    from src.pr import _get_financial, _get_industry
    from src.pr_calculator import classify_industry
    from src.screener_rate_limit import batch_wait

    fin_provider = financial_provider or _get_financial
    ind_provider = industry_provider or _get_industry
    snapshots: List[FinanceSnapshot] = []
    for code in codes:
        existing = cache.get_finance(code)
        # 增量复用：缓存未过期且 ROE 完整则跳过重拉。
        # roe_latest 缺失（如 init_screener 从 backtest.db 导入的行无 ROE）→ 强制重拉，
        # 否则该行永远拿不到财务数据（镜像 dividend 路径 #82 教训）。
        if (
            existing is not None
            and not cache.is_finance_stale(code, max_age_days=fresh_days)
            and existing.roe_latest is not None
        ):
            snapshots.append(existing)
            continue
        batch_wait()  # 限流：控制请求间隔
        roe_latest, roe_5y_median, _np_latest, np_annual, src, _errors, roe_period = fin_provider(code)
        if roe_latest is None:
            continue  # 数据不可得 → 不写假数据（数据铁律）
        industry, _ind_src = ind_provider(code)
        is_cyclical = classify_industry(industry)[0] if industry else None
        snap = FinanceSnapshot(
            code=code, roe_latest=roe_latest,
            roe_period=str(roe_period) if roe_period is not None else None,
            net_profit_annual=np_annual, payout_ratio=_payout_ratio(cache, code, np_annual),
            roe_5y_median=roe_5y_median, is_cyclical=is_cyclical,
            finance_source=src,
        )
        cache.upsert_finance(snap)
        snapshots.append(snap)
    return snapshots