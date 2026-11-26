"""选股器前瞻股息率 enrichment（#157）。

漏斗③/④ 幸存行在编排层末尾逐只估算「下一财年前瞻股息率」：
- 分红明细 / 财务行取自可持续性缓存快照（`cached_snapshot_inputs`），零网络；
  快照缺失 → `missing_inputs`（不按需补拉，避免拖慢每日任务与掩盖数据缺口）
- 只对东财一致预期发一次请求（每只 1 次），取数失败 → `fetch_error`
- 现价 / 总股本取自行情快照；财年由分红快照的最新完整财年推导（L+1）

本模块不进漏斗判定、不排序：结果只随 CSV/JSON 输出，供选股页展示。
单只失败隔离与整批护栏见 #159（本阶段先保证正常路径与既有降级语义）。
"""
from typing import Callable, Optional

from src.forward_yield import (
    STATUS_FETCH_ERROR,
    STATUS_MISSING_INPUTS,
    STATUS_NO_COVERAGE,
    STATUS_NO_PAYOUT_HISTORY,
    STATUS_NO_POSITIVE_EPS,
    STATUS_NO_TARGET_YEAR,
    ForwardYieldResult,
    estimate_forward_yield,
    fetch_earnings_forecast,
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


def _next_year(latest_year) -> Optional[int]:
    """最新完整财年（"2025"）→ 下一财年（2026）；非法/缺失 → None。"""
    s = str(latest_year or "")[:4]
    return int(s) + 1 if s.isdigit() and len(s) == 4 else None


def make_forward_evaluator(
    cache: ScreenerCache,
    *,
    forecast_fetcher: Callable[[str], Optional[list]] = fetch_earnings_forecast,
) -> Callable[[FunnelCandidate], ForwardYieldResult]:
    """构造前瞻评估器（编排层注入；测试可替换预测取数）。"""

    def evaluate(candidate: FunnelCandidate) -> ForwardYieldResult:
        latest_year = candidate.dividend.real_yield_year if candidate.dividend else None
        inputs = cached_snapshot_inputs(cache, candidate.code) or {}
        dividend_rows = inputs.get("dividend_rows")
        financial_rows = inputs.get("financial_rows")
        if dividend_rows is None or financial_rows is None:
            return ForwardYieldResult(
                forward_status=STATUS_MISSING_INPUTS,
                forward_year=_next_year(latest_year),
                forward_note="缺少分红/财务快照，不做前瞻",
            )

        q = candidate.quote
        fin = candidate.finance
        trailing = candidate.real_yield_now
        if trailing is None and candidate.dividend is not None:
            trailing = candidate.dividend.real_yield
        res = estimate_forward_yield(
            stock_code=candidate.code,
            price=q.price if q else None,
            total_shares=q.total_shares if q else None,
            dividend_rows=dividend_rows,
            financial_rows=financial_rows,
            forecast_rows=None,
            is_cyclical=bool(fin.is_cyclical) if fin else False,
            trailing_yield=trailing,
            forecast_fetcher=forecast_fetcher,
        )
        if res.forward_year is None and res.forward_status in _YEAR_KNOWN_STATUSES:
            res.forward_year = _next_year(latest_year)
        return res

    return evaluate


__all__ = ["make_forward_evaluator"]
