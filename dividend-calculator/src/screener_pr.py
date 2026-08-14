"""选股器 PR 数据获取（spec #67，工单 #72，code-review 修复）。

漏斗③ 的判定与 PR 计算已收进 src.screening（ADR-0001）；本模块保留
evaluate_pr_batch（纯缓存批量评估，供 scripts/fill_screener_data.py 预填财务用）。
"""
from typing import Callable, List, Optional

from src.pr import PRResult
from src.pr_calculator import compute_basic_pr, compute_corrected_pr, compute_n_factor
from src.screener_cache import FinanceSnapshot, ScreenerCache

# 估值四档中选股器保留的两档（V2 回测证实 PR≤1 有超额）
PR_ZONE_KEEP = ("合理偏低", "低估")


def _select_roe(fin: FinanceSnapshot) -> Optional[float]:
    """周期股用 5 年 ROE 中位数（抗单年失真），其余用最新年报 ROE —— 与漏斗③ 一致。"""
    if fin.is_cyclical and fin.roe_5y_median is not None:
        return fin.roe_5y_median
    return fin.roe_latest


def compute_pr(pe_ttm: Optional[float], fin: FinanceSnapshot) -> Optional[float]:
    """PR 口径对齐漏斗③ default_pr_evaluator（P1-2 统一，评审修复）：
    周期股用 5 年 ROE 中位数（单年失真平滑）；优先修正 PR = N×PE_TTM/ROE
    （N=0.5/股利支付率，截断[1,2]），payout_ratio 缺失回退基础 PR。"""
    roe = _select_roe(fin)
    if roe is None:
        return None
    n = compute_n_factor(fin.payout_ratio)
    return compute_corrected_pr(pe_ttm, roe, n) if n is not None else compute_basic_pr(pe_ttm, roe)


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
        pr_val = compute_pr(quote.pe_ttm, fin)
        zone = classify_valuation(pr_val)
        results.append({
            "code": code,
            "pr": pr_val,
            "valuation_zone": zone,
            "pass_pr": zone in pr_zone,
            "industry": None,  # 缓存无行业
            "roe_latest": fin.roe_latest,
            "roe_used": _select_roe(fin),  # 实际参与 PR 计算的 ROE（周期股为中位数）
        })
    return results
