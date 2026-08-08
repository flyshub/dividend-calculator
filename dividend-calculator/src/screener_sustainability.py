"""选股器股息可持续性（spec #67，工单 #73）。

四级漏斗的最后一层：对通过漏斗③的候选股评估股息可持续性，漏斗④ 筛 verdict ∈ {可持续, 偏弱}。

复用 assess_with_auto_fetch（现有分层级联模型：行业路由→致命红旗→六维评分→情境警示）。
verdict 存快照避免重复评估（选股器低频跑，但同股多次筛选不重拉）。

参数可注入 assessor 便于测试（不碰真实 HTTP）。
"""
from typing import Callable, List, Optional

from src.screener_cache import ScreenerCache, DividendSnapshot

# 漏斗④ 保留的 verdict（可持续 + 偏弱）
SUS_VERDICT_KEEP = ("可持续", "偏弱")

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
) -> dict:
    """单股可持续性评估。返回 {verdict, pass_sus}。

    assessor 注入便于测试；默认走 assess_with_auto_fetch（真实数据源）。
    """
    if code in _sus_cache:
        verdict = _sus_cache[code]
    else:
        if assessor:
            result = assessor(code)
        else:
            from src.sustainability import assess_with_auto_fetch
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
    return {
        "verdict": verdict,
        "pass_sus": verdict in SUS_VERDICT_KEEP,
    }


def evaluate_sustainability_batch(
    stocks: List[dict],
    cache: ScreenerCache,
    *,
    assessor: Optional[Callable[[str], object]] = None,
    sus_verdict: tuple = SUS_VERDICT_KEEP,
) -> List[dict]:
    """对候选池批量评估可持续性，返回 {code, verdict, pass_sus} 列表。

    sus_verdict 控制保留的 verdict（默认 可持续/偏弱）。
    """
    from src.screener_rate_limit import batch_wait
    results = []
    for s in stocks:
        batch_wait()  # 限流：控制请求间隔
        code = s["code"]
        dividend = s.get("dividend")
        if dividend is None:
            continue
        r = evaluate_sustainability(
            code, dividend, assessor=assessor,
            total_shares=s.get("total_shares", 1.0),
            dividend_total=s.get("dividend_total"),
            industry=s.get("industry"),
        )
        # 应用 sus_verdict 过滤（覆盖 SUS_VERDICT_KEEP 默认）
        r["pass_sus"] = r["verdict"] in sus_verdict
        results.append({"code": code, **r})
    return results


def screen_sustainability(evaluations: List[dict]) -> List[dict]:
    """漏斗④：保留 verdict ∈ {可持续, 偏弱} 的候选。"""
    return [e for e in evaluations if e["pass_sus"]]


def reset_sus_cache():
    """清空评估缓存（测试用）。"""
    _sus_cache.clear()
