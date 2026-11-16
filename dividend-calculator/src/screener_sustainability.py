"""选股器股息可持续性数据获取（spec #67，工单 #73）。

漏斗④ 的判定已收进 src.screening（ADR-0001）；本模块保留评估与缓存
（复用 assess_with_auto_fetch 分层级联模型），并向选股漏斗提供单股评估回调。

参数可注入 assessor 便于测试（不碰真实 HTTP）。
"""
from typing import Callable, Optional

import sys

from src.screener_rate_limit import batch_wait
from src.screener_cache import ScreenerCache, DividendSnapshot
from src.screener_fetch import DEFAULT_MAX_CONSECUTIVE_FAILURES, fetch_or_default
from src.screening import FunnelCandidate

# 漏斗④ 保留的 verdict（可持续 + 偏弱）
SUS_VERDICT_KEEP = ("可持续", "偏弱")

# 单股可持续性取数超时（秒）：数据源无响应时降级为「未评估」，不拖死每日任务
SUS_FETCH_TIMEOUT = 120.0

# 可持续性评估结果缓存（内存，跨股票）
_sus_cache: dict = {}


def evaluate_sustainability(
    code: str,
    dividend: DividendSnapshot,
    *,
    assessor: Optional[Callable[[str], object]] = None,
    total_shares: float = 1.0,
    dividend_total: Optional[float] = None,
    industry: Optional[str] = None,
    cache: Optional[ScreenerCache] = None,
) -> dict:
    """单股可持续性评估。返回 {verdict, pass_sus}。

    assessor 注入便于测试；默认走 assess_with_auto_fetch（真实数据源）。
    缓存加速：sustainability_snapshot 命中（未过期）时注入预拉数据，零网络；
    未命中时实时评估并写缓存（按需补拉）。
    """
    if code in _sus_cache and cache is not None:
        # 内存缓存命中：仅当 DB 缓存也有效时才复用（否则强制重评估，避免过期 verdict）
        db_snap = cache.get_sustainability(code)
        if db_snap is not None and not cache.is_sustainability_stale(code):
            verdict = _sus_cache[code]
        else:
            _sus_cache.pop(code, None)
            verdict = _assess_and_cache(code, dividend, assessor, total_shares,
                                        dividend_total, industry, cache)
    elif code in _sus_cache:
        # 无 DB 缓存场景（测试）：直接复用内存缓存
        verdict = _sus_cache[code]
    else:
        verdict = _assess_and_cache(code, dividend, assessor, total_shares,
                                    dividend_total, industry, cache)
    return {
        "verdict": verdict,
        "pass_sus": verdict in SUS_VERDICT_KEEP,
    }


def _assess_and_cache(code, dividend, assessor, total_shares, dividend_total,
                      industry, cache):
    """评估单股可持续性，写回缓存（S1 按需补拉）。返回 verdict。

    缓存命中时走 sustainability.assess_from_cache（内部反序列化并注入预拉数据，
    调用方不接触快照 JSON 列，见 sustainability.py #95 序列化契约）；
    未命中且注入 assessor 时用 assessor（测试钩子），否则按需补拉。
    """
    from src.sustainability import assess_from_cache, assess_with_auto_fetch
    cached_ok = (cache is not None
                 and cache.get_sustainability(code) is not None
                 and not cache.is_sustainability_stale(code))
    if cached_ok:
        result = assess_from_cache(
            cache, code,
            total_shares=total_shares,
            dividend_total=dividend_total,
            dividend_yield_before_tax=dividend.real_yield,
            latest_dividend_year=dividend.real_yield_year,
            industry=industry,
        )
    elif assessor:
        result = assessor(code)
    else:
        result = assess_with_auto_fetch(
            stock_code=code,
            total_shares=total_shares,
            dividend_total=dividend_total,
            dividend_yield_before_tax=dividend.real_yield,
            latest_dividend_year=dividend.real_yield_year,
            industry=industry,
        )
    verdict = getattr(result, "verdict", "未评估")
    _sus_cache[code] = verdict
    return verdict


def make_sustainability_evaluator(
    cache: ScreenerCache,
    *,
    assessor: Optional[Callable[[str], object]] = None,
    max_consecutive_failures: int = DEFAULT_MAX_CONSECUTIVE_FAILURES,
) -> Callable[[FunnelCandidate], str]:
    """构造漏斗④ 的可持续性评估回调（数据获取层）：限流 + 缓存复用 + 评估。

    返回单股 verdict 字符串。缓存命中（未过期）时跳过限流等待（零网络）；
    未命中且未注入 assessor 时按数据源限流等待（与既有批量评估一致）。
    同时把快照行业补进候选（纯缓存路径无行业，供输出行使用）。

    容错与另两条取数路径同源（src.screener_fetch）：单只失败降级为「未评估」，
    连续 N 只超时/异常则中止本轮——此前这里没有计数器，数据源整体失效时
    整轮都在「未评估」上空转。
    """
    consecutive_failures = 0

    def evaluate(candidate: FunnelCandidate) -> str:
        nonlocal consecutive_failures
        code = candidate.code
        dividend = candidate.dividend
        if dividend is None:
            return "未评估"
        snap = cache.get_sustainability(code) if cache else None
        if snap is not None and snap.industry and not candidate.industry:
            candidate.industry = snap.industry
        cached_ok = snap is not None and not cache.is_sustainability_stale(code)
        if not cached_ok and assessor is None:
            batch_wait()
        verdict = fetch_or_default(
            code,
            lambda: evaluate_sustainability(
                code, dividend, assessor=assessor,
                total_shares=candidate.quote.total_shares if candidate.quote else 1.0,
                dividend_total=dividend.total_dividend,
                industry=candidate.industry,
                cache=cache,
            )["verdict"],
            label="可持续性",
            timeout_s=SUS_FETCH_TIMEOUT,
            default="未评估",
        )
        if verdict == "未评估" and not cached_ok:
            consecutive_failures += 1
            if consecutive_failures >= max_consecutive_failures:
                raise RuntimeError(
                    f"可持续性取数连续 {consecutive_failures} 只无结果，"
                    "判定数据源不可用，中止本轮"
                )
        else:
            consecutive_failures = 0
        return verdict

    return evaluate


def reset_sus_cache():
    """清空评估缓存（测试用）。"""
    _sus_cache.clear()
