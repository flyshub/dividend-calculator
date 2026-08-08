"""选股器 PR 估值（spec #67，工单 #72，code-review 修复）。

四级漏斗的第三层：对通过漏斗②的候选池计算 PR，估值分类，漏斗③ 筛「合理偏低+低估」。

复用 pr.calculate_pr（完整 PRResult：含 valuation_zone / industry / roe_latest）——
不重复实现 compute_pr（code-review：与 pr_calculator.compute_basic_pr 重复）。
结果写 finance_snapshot（ROE 等），供后续筛选复用。

calculate_pr 网络密集，测试注入 assessor 包装。
"""
from typing import Callable, List, Optional

from src.pr import PRResult, calculate_pr
from src.screener_cache import FinanceSnapshot, ScreenerCache

# 估值四档中选股器保留的两档（V2 回测证实 PR≤1 有超额）
PR_ZONE_KEEP = ("合理偏低", "低估")


def evaluate_stock_full(
    code: str,
    cache: ScreenerCache,
    *,
    dividend_total: Optional[float] = None,
    pr_provider: Optional[Callable[[str], Optional[PRResult]]] = None,
) -> dict:
    """单股完整 PR 评估：调 calculate_pr 拿 valuation_zone/industry/roe_latest，写 finance_snapshot。

    pr_provider 注入便于测试（默认走 calculate_pr 真实数据源）。
    返回 {code, pr, valuation_zone, pass_pr, industry, roe_latest}。
    """
    result = pr_provider(code) if pr_provider else calculate_pr(code, dividend_total=dividend_total)
    if result is None:
        return {"code": code, "pr": None, "valuation_zone": "无法判定",
                "pass_pr": False, "industry": None, "roe_latest": None}
    zone = result.valuation_zone
    # 写 finance_snapshot（ROE 等，供后续复用）
    cache.upsert_finance(FinanceSnapshot(
        code=code,
        roe_latest=result.roe_latest,
        roe_period=result.roe_period,
        net_profit_annual=result.net_profit_annual,
        payout_ratio=result.payout_ratio,
        finance_source=result.finance_source,
    ))
    return {
        "code": code,
        "pr": result.pr_basic,
        "valuation_zone": zone,
        "pass_pr": zone in PR_ZONE_KEEP,
        "industry": result.industry,
        "roe_latest": result.roe_latest,
    }


def evaluate_pr_batch(
    codes: List[str],
    cache: ScreenerCache,
    *,
    pr_zone: tuple = PR_ZONE_KEEP,
    pr_provider: Optional[Callable[[str], Optional[PRResult]]] = None,
    dividend_totals: Optional[dict] = None,
) -> List[dict]:
    """候选池批量 PR 评估。pr_zone 控制保留的估值区间。"""
    results = []
    for code in codes:
        dt = (dividend_totals or {}).get(code)
        results.append(evaluate_stock_full(
            code, cache, dividend_total=dt, pr_provider=pr_provider))
    # 应用 pr_zone 过滤（覆盖 PR_ZONE_KEEP 默认）
    for r in results:
        r["pass_pr"] = r["valuation_zone"] in pr_zone
    return results


def screen_pr(evaluations: List[dict]) -> List[dict]:
    """漏斗③：保留估值 ∈ {合理偏低, 低估} 的候选。"""
    return [e for e in evaluations if e["pass_pr"]]
