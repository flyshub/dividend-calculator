"""选股器 PR 估值（spec #67，工单 #72，code-review 修复）。

四级漏斗的第三层：对通过漏斗②的候选池计算 PR，估值分类，漏斗③ 筛「合理偏低+低估」。

复用 pr.calculate_pr（完整 PRResult：含 valuation_zone / industry / roe_latest）——
不重复实现 compute_pr（code-review：与 pr_calculator.compute_basic_pr 重复）。
结果写 finance_snapshot（ROE 等），供后续筛选复用。

calculate_pr 网络密集，测试注入 assessor 包装。
"""
from typing import Callable, List, Optional

from src.pr import PRResult, calculate_pr
from src.pr_calculator import compute_basic_pr
from src.screener_cache import FinanceSnapshot, ScreenerCache

# 估值四档中选股器保留的两档（V2 回测证实 PR≤1 有超额）
PR_ZONE_KEEP = ("合理偏低", "低估")


def compute_pr(pe_ttm: Optional[float], roe_latest: Optional[float]) -> Optional[float]:
    """PR = PE_TTM / ROE_latest（复用 pr_calculator.compute_basic_pr）。"""
    return compute_basic_pr(pe_ttm, roe_latest)


def evaluate_stock_full(
    code: str,
    cache: ScreenerCache,
    *,
    dividend_total: Optional[float] = None,
    pr_provider: Optional[Callable[[str], Optional[PRResult]]] = None,
) -> dict:
    """单股完整 PR 评估：调 calculate_pr 拿 valuation_zone/industry/roe_latest，写 finance_snapshot。

    pr_provider 注入便于测试（默认走 calculate_pr 真实数据源）。
    缓存优先：finance_snapshot（ROE）+ quote_snapshot（PE）均有效且未过期时，
    用缓存算估值，避免调网络 calculate_pr。
    返回 {code, pr, valuation_zone, pass_pr, industry, roe_latest}。
    """
    # 缓存优先：ROE + PE 都有则用缓存估值（省网络请求）
    fin = cache.get_finance(code)
    quote = cache.get_quote(code)
    if (fin is not None and fin.roe_latest is not None
            and quote is not None and quote.pe_ttm is not None):
        from src.pr_calculator import classify_valuation
        pr_val = compute_pr(quote.pe_ttm, fin.roe_latest)
        zone = classify_valuation(pr_val)
        return {
            "code": code,
            "pr": pr_val,
            "valuation_zone": zone,
            "pass_pr": zone in PR_ZONE_KEEP,
            "industry": None,  # 缓存无行业，后续从 calculate_pr 补
            "roe_latest": fin.roe_latest,
        }
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
    """候选池批量 PR 评估（纯缓存优先，性能优化）。

    pr_zone 控制保留的估值区间。
    只对缓存完整（ROE+PE 都有）的股票用缓存估值；缺 ROE 直接跳过（不调网络
    calculate_pr），避免少数缺数据股票拖垮整个阶段。
    """
    # 批量读缓存
    all_quotes = cache.get_all_quotes()
    all_finance = cache.get_all_finance()
    results = []
    for code in codes:
        quote = all_quotes.get(code)
        fin = all_finance.get(code)
        if (quote is None or quote.pe_ttm is None
                or fin is None or fin.roe_latest is None):
            # 缺 ROE/PE → 跳过（不调网络）
            continue
        from src.pr_calculator import classify_valuation
        pr_val = compute_pr(quote.pe_ttm, fin.roe_latest)
        zone = classify_valuation(pr_val)
        results.append({
            "code": code,
            "pr": pr_val,
            "valuation_zone": zone,
            "pass_pr": zone in pr_zone,
            "industry": None,  # 缓存无行业
            "roe_latest": fin.roe_latest,
        })
    return results


def screen_pr(evaluations: List[dict]) -> List[dict]:
    """漏斗③：保留估值 ∈ {合理偏低, 低估} 的候选。"""
    return [e for e in evaluations if e["pass_pr"]]
