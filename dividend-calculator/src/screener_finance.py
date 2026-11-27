"""选股器财务数据获取（issue #122）。

对候选池逐股拉取财务（ROE/净利润/支付率/周期标记）写 finance_snapshot（缓存层）。
复用 pr.py::_get_financial（东财 F10 → mootdx → 同花顺降级链：CI 上只有东财可用，
TDX 7709 被封、*.10jqka 对机房 IP 403、akshare 已移除该同花顺接口）与 _get_industry。
漏斗③ 的判定与 PR 计算已收进 src.screening（ADR-0001），本模块只负责取数与缓存。
"""
import sys
from typing import Callable, List, Optional, Tuple

from src.screener_cache import FinanceSnapshot, ScreenerCache
from src.screener_fetch import (  # noqa: F401 — effective_mass_failure_min 对外再导出
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    MIN_MASS_FAILURE_MIN,
    FetchPolicy,
    effective_mass_failure_min,
    fetch_each,
)

# 单批容错：连续 N 只取数失败视为数据源不可用（实现收在 screener_fetch）
MAX_CONSECUTIVE_FAILURES = DEFAULT_MAX_CONSECUTIVE_FAILURES

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
    fetch_timeout: float = 120.0,
    max_consecutive_failures: int = MAX_CONSECUTIVE_FAILURES,
    mass_failure_min: int = 200,
) -> List[FinanceSnapshot]:
    """对候选池逐股拉财务，写 finance_snapshot，返回全部快照。

    增量复用：finance_snapshot 在 fresh_days 内 → 跳过重拉（月频管线 ~30 天跑一次，
    7 天窗口保证月内重跑不重复拉取）。
    限流：batch_wait()（0.8s/只），对齐 fill_dividends。
    容错：单只失败（超时/数据源异常）记日志跳过，连续 N 只失败则中止本轮；
    尝试达到 effective_mass_failure_min 只仍零写入（正常覆盖 ~97%）也判定数据源
    失效而中止——门槛按本轮代码数缩放，分片小批次同样生效。
    判据实现收在 src.screener_fetch（与股息/可持续性共用一份）。
    """
    from src.pr import _get_financial, _get_industry
    from src.pr_calculator import classify_industry
    from src.screener_rate_limit import batch_wait, call_with_timeout

    fin_provider = financial_provider or _get_financial
    ind_provider = industry_provider or _get_industry

    def cached(code: str) -> Optional[FinanceSnapshot]:
        # 增量复用：缓存未过期且 ROE 完整则跳过重拉。
        # roe_latest 缺失（如 init_screener 从 backtest.db 导入的行无 ROE）→ 强制重拉，
        # 否则该行永远拿不到财务数据（镜像 dividend 路径 #82 教训）。
        existing = cache.get_finance(code)
        if (
            existing is not None
            and not cache.is_finance_stale(code, max_age_days=fresh_days)
            and existing.roe_latest is not None
        ):
            return existing
        return None

    def fetch(code: str) -> Tuple[str, tuple, str]:
        """取一只：代码 + 财务元组 + 行业串（财务为空时不取行业）。"""
        fin = fin_provider(code)
        if fin[0] is None:  # ROE 缺失 → 数据不可得，交给 is_empty
            return code, fin, ""
        try:
            industry, _ind_src = call_with_timeout(
                lambda: ind_provider(code), fetch_timeout, what=f"{code} 行业",
            )
        except Exception as exc:  # noqa: BLE001 — 行业缺失不影响财务快照
            print(f"  [财务] {code} 行业取数失败: {exc}", file=sys.stderr)
            industry = ""
        return code, fin, industry

    def save(result: Tuple[str, tuple, str]) -> FinanceSnapshot:
        code, fin, industry = result
        roe_latest, roe_5y_median, _np_latest, np_annual, src, _errors, roe_period = fin
        is_cyclical = classify_industry(industry)[0] if industry else None
        snap = FinanceSnapshot(
            code=code, roe_latest=roe_latest,
            roe_period=str(roe_period) if roe_period is not None else None,
            net_profit_annual=np_annual, payout_ratio=_payout_ratio(cache, code, np_annual),
            roe_5y_median=roe_5y_median, is_cyclical=is_cyclical,
            finance_source=src,
        )
        cache.upsert_finance(snap)
        return snap

    outcome = fetch_each(
        codes,
        fetch,
        label="财务",
        policy=FetchPolicy(
            timeout_s=fetch_timeout,
            max_consecutive_failures=max_consecutive_failures,
            mass_failure_min=mass_failure_min,
        ),
        cached=cached,
        is_empty=lambda result: result[1][0] is None,
        save=save,
        before_request=batch_wait,  # 限流：控制请求间隔
    )
    return outcome.items
