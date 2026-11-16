"""选股器 PR 数据获取（spec #67，工单 #72，code-review 修复）。

漏斗③ 的判定与 PR 计算已收进 src.screening（ADR-0001）；本模块保留
evaluate_pr_batch（纯缓存批量评估，供 scripts/fill_screener_data.py 预填财务用）。
"""
from typing import Callable, List, Optional

from src.pr import PRResult
from src.pr_calculator import judge_pr
from src.screener_cache import FinanceSnapshot, ScreenerCache

# 估值四档中选股器保留的两档（V2 回测证实 PR≤1 有超额）
PR_ZONE_KEEP = ("合理偏低", "低估")


def compute_pr(pe_ttm: Optional[float], fin: FinanceSnapshot) -> Optional[float]:
    """PR 口径对齐漏斗③ default_pr_evaluator：**基础 PR = PE_TTM / ROE**，
    与 V2 回测 L3 同口径（修正 PR 不进选股判据，理由见 screening.default_pr_evaluator）。
    周期股用 5 年 ROE 中位数（单年失真平滑），其余用最新年报 ROE。"""
    _roe, pr, _zone = judge_pr(pe_ttm, fin.roe_latest, fin.roe_5y_median, fin.is_cyclical)
    return pr


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
        # 判定与选股器/页面共用 judge_pr：档位不在此处另算，避免第二条判定路径
        roe_used, pr_val, zone = judge_pr(
            quote.pe_ttm, fin.roe_latest, fin.roe_5y_median, fin.is_cyclical)
        results.append({
            "code": code,
            "pr": pr_val,
            "valuation_zone": zone,
            "pass_pr": zone in pr_zone,
            "industry": None,  # 缓存无行业
            "roe_latest": fin.roe_latest,
            "roe_used": roe_used,  # 实际参与判定的 ROE（周期股为中位数）
        })
    return results
