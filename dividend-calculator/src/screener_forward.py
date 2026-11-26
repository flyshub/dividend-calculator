"""选股器前瞻股息率 enrichment（#157）。

漏斗③/④ 幸存行在编排层末尾逐只估算「下一财年前瞻股息率」：
- 分红明细 / 财务行优先取可持续性缓存快照（`cached_snapshot_inputs`），命中零网络；
  未命中（如当日行情新进漏斗、快照还没预拉）按需补拉这两类数据，与主页面同源同口径
- 东财一致预期每只 1 次请求；任一取数失败 → `fetch_error`
- 现价 / 总股本取自行情快照；财年由分红快照的最新完整财年推导（L+1）

本模块不进漏斗判定、不排序：结果只随 CSV/JSON 输出，供选股页展示。
单只失败隔离与整批护栏见 #159。
"""
from typing import Callable, Optional

from src.forward_yield import (
    STATUS_FETCH_ERROR,
    STATUS_MISSING_INPUTS,
    STATUS_NO_COVERAGE,
    STATUS_NO_PAYOUT_HISTORY,
    STATUS_NO_POSITIVE_EPS,
    STATUS_NO_TARGET_YEAR,
    STATUS_OK,
    STATUS_STALE,
    ForwardYieldResult,
    estimate_forward_yield,
    fetch_dividend_rows,
    fetch_earnings_forecast,
    fetch_financial_rows,
)
from src.screening import FunnelCandidate
from src.screener_cache import ScreenerCache
from src.sustainability import cached_snapshot_inputs

# 这些状态在 docs/FORWARD_YIELD.md §5.1 里仍带目标财年（L+1）——
# estimate 的早退分支只给状态不给财年，编排层按股息快照补回，保证 CSV/悬停一致
_YEAR_KNOWN_STATUSES = {
    STATUS_FETCH_ERROR, STATUS_NO_COVERAGE, STATUS_NO_TARGET_YEAR,
    STATUS_NO_POSITIVE_EPS, STATUS_NO_PAYOUT_HISTORY,
}

# 真正发起过一致预期请求的状态（护栏分母）：ok + 上面这些早退状态 + stale。
# 注意 stale 的判定发生在 compute_forward_yield 内部，而一致预期取数在它之前已完成，
# 所以 stale 行确实发过请求，必须计入分母（否则失败率被放大）。
# missing_inputs / no_complete_year 在取数前就返回，不计入。
_REQUEST_STATUSES = _YEAR_KNOWN_STATUSES | {STATUS_OK, STATUS_STALE}


def _next_year(latest_year) -> Optional[int]:
    """最新完整财年（"2025"）→ 下一财年（2026）；非法/缺失 → None。"""
    s = str(latest_year or "")[:4]
    return int(s) + 1 if s.isdigit() and len(s) == 4 else None


# 整批护栏（#159）：实际发起一致预期请求的行 ≥10 且失败率 >50% → 判定数据源故障。
# 阈值与「连续 30 只失败中止」同一思路：小样本的偶发失败逐行标注即可，不整列清空。
FORWARD_GUARD_MIN_ATTEMPTS = 10
FORWARD_GUARD_FAILURE_RATIO = 0.5


def safe_evaluate(
    evaluator: Callable[[FunnelCandidate], ForwardYieldResult],
    candidate: FunnelCandidate,
) -> ForwardYieldResult:
    """单只隔离：评估器抛异常（如快照 JSON 损坏）→ fetch_error，不中断整批选股。"""
    try:
        return evaluator(candidate)
    except Exception as exc:  # noqa: BLE001 — 单只隔离是数据源不可靠时的设计目标
        year = _next_year(candidate.dividend.real_yield_year if candidate.dividend else None)
        return ForwardYieldResult(
            forward_status=STATUS_FETCH_ERROR,
            forward_year=year,
            forward_note="前瞻取数异常: %s" % exc,
        )


def apply_forward_guard(candidates: list) -> dict:
    """整批护栏：失败率超阈值 → 清空本批全部前瞻结果（三列留空），返回统计。

    分母只计真正发起过请求的状态（`_REQUEST_STATUSES`）：快照缺失、
    无完整财年不发请求，不计入；stale 的判定在取数之后，计入。
    市场事实（no_coverage / no_positive_eps / no_target_year / no_payout_history）
    计分母但不计失败。
    """
    attempted = 0
    failed = 0
    for c in candidates:
        status = c.forward.forward_status if c.forward else None
        if status in _REQUEST_STATUSES:
            attempted += 1
            if status == STATUS_FETCH_ERROR:
                failed += 1
    tripped = (attempted >= FORWARD_GUARD_MIN_ATTEMPTS
               and failed > attempted * FORWARD_GUARD_FAILURE_RATIO)
    if tripped:
        for c in candidates:
            c.forward = None
    return {"attempted": attempted, "failed": failed, "tripped": tripped}


def make_forward_evaluator(
    cache: ScreenerCache,
    *,
    forecast_fetcher: Callable[[str], Optional[list]] = fetch_earnings_forecast,
    dividend_fetcher: Callable[[str], Optional[list]] = fetch_dividend_rows,
    financial_fetcher: Callable[[str], Optional[list]] = fetch_financial_rows,
) -> Callable[[FunnelCandidate], ForwardYieldResult]:
    """构造前瞻评估器（编排层注入；测试可替换三类取数）。

    分红/财务行命中可持续性快照时零网络；未命中时由 estimate_forward_yield
    按需补拉（与主页面同一取数路径与口径），取数失败降级为 fetch_error。
    """

    def evaluate(candidate: FunnelCandidate) -> ForwardYieldResult:
        latest_year = candidate.dividend.real_yield_year if candidate.dividend else None
        inputs = cached_snapshot_inputs(cache, candidate.code) or {}

        q = candidate.quote
        fin = candidate.finance
        trailing = candidate.real_yield_now
        if trailing is None and candidate.dividend is not None:
            trailing = candidate.dividend.real_yield
        res = estimate_forward_yield(
            stock_code=candidate.code,
            price=q.price if q else None,
            total_shares=q.total_shares if q else None,
            dividend_rows=inputs.get("dividend_rows"),
            financial_rows=inputs.get("financial_rows"),
            forecast_rows=None,
            is_cyclical=bool(fin.is_cyclical) if fin else False,
            trailing_yield=trailing,
            dividend_fetcher=dividend_fetcher,
            financial_fetcher=financial_fetcher,
            forecast_fetcher=forecast_fetcher,
        )
        if res.forward_year is None and res.forward_status in _YEAR_KNOWN_STATUSES:
            res.forward_year = _next_year(latest_year)
        return res

    return evaluate


__all__ = [
    "FORWARD_GUARD_MIN_ATTEMPTS",
    "FORWARD_GUARD_FAILURE_RATIO",
    "apply_forward_guard",
    "make_forward_evaluator",
    "safe_evaluate",
]
