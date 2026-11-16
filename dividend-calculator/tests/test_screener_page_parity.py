"""选股结果页口径卡与 screening.py 默认值对账（code-review 修复）。

口径卡的阈值硬编码在 HTML 散文里，与 src/screening.py 的 DEFAULT_* 常量构成
双事实源（code-review Standards #1）。本测试锁定三件事（#98 单页收拢后
页面来源仅 site/，原双端逐字节一致校验已随 src/static/ 一并退役）：
  1. 口径卡列出的四漏斗阈值/区间与 screening.py 默认值一致（改默认值必须同步页面）；
  2. 口径卡确实存在（防误删）；
  3. 'PR ≤ 1.0' 这条边界来自 site/js/policy.js（不再写死在 HTML 散文里），
     且 policy.js 的切点由 scripts/policy_parity.py 与 pr_calculator 对账。
"""
from pathlib import Path

from src.pr_calculator import classify_valuation, judge_pr
from src.screening import (
    DEFAULT_MIN_REAL,
    DEFAULT_MIN_TTM,
    DEFAULT_PR_ZONE,
    DEFAULT_SUS_VERDICT,
)

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["site/screener.html"]


def _read(page: str) -> str:
    return (ROOT / page).read_text(encoding="utf-8")


def test_criteria_card_exists_on_page():
    for page in PAGES:
        assert "选股口径" in _read(page), f"{page} 缺少选股口径卡"


def test_yield_thresholds_match_screening_defaults():
    for page in PAGES:
        html = _read(page)
        assert f"TTM 股息率 &gt; {DEFAULT_MIN_TTM:.0f}%" in html, f"{page} TTM 阈值漂移"
        assert f"真实股息率 &gt; {DEFAULT_MIN_REAL:.0f}%" in html, f"{page} 真实股息率阈值漂移"


def test_pr_zone_and_sus_verdict_match_screening_defaults():
    for page in PAGES:
        html = _read(page)
        for zone in DEFAULT_PR_ZONE:
            assert zone in html, f"{page} 缺少市赚率区间 {zone}"
        for verdict in DEFAULT_SUS_VERDICT:
            assert verdict in html, f"{page} 缺少可持续性判定 {verdict}"


def test_pr_le_1_boundary_comes_from_policy_module():
    """PR≤1 的边界改由 policy.js 提供（issue #151），HTML 只留占位。

    双端数值一致性由 scripts/policy_parity.check_pr_cutoffs 负责；
    这里守的是「页面确实用占位、不再写死整句阈值」。
    """
    for page in PAGES:
        html = _read(page)
        assert 'data-policy="fair_low"' in html, f"{page} 未使用 policy.js 占位"
        assert "PR ≤ 1.0（" not in html, f"{page} 仍写死阈值整句"
    assert "PR_ZONE_CUTOFFS" in _read("site/js/policy.js")
    # 语义对账：1.0 仍落在 {低估,合理偏低}（含边界），1.01 即离开
    assert classify_valuation(1.0) in DEFAULT_PR_ZONE
    assert classify_valuation(1.01) not in DEFAULT_PR_ZONE


def test_page_zone_and_screener_share_judge_pr():
    """页面档位与选股器漏斗③ 同口径：都只能来自 pr_calculator.judge_pr。

    2026-11 事故：页面按修正 PR 判档（平安 0.97）、选股器按基础 PR 判档（0.75），
    同一只股票两个结论。本测试锁定「单页 index.html 明示判定口径」+ 数值一致性。
    """
    html = _read("site/index.html")
    assert "判定用" in html, "index.html 缺少判定口径展示"
    assert "不进选股判据" in html, "index.html 未标注修正 PR 仅参考"

    # 页面与选股器共用同一函数：用筛选器同参数复算，档位必须一致
    from src.screener_cache import FinanceSnapshot, QuoteSnapshot
    from src.screening import FunnelCandidate, default_pr_evaluator
    from src.screener_pr import compute_pr

    quote = QuoteSnapshot(code="601318", name="中国平安", price=50.0, pe_ttm=9.88, pb=1.0,
                          total_shares=1e9, market_cap=5e10, quote_time="", source="腾讯")
    fin = FinanceSnapshot(code="601318", roe_latest=14.0, roe_period="2025年报",
                          roe_5y_median=13.2, is_cyclical=True, net_profit_annual=1e9,
                          payout_ratio=0.363, finance_source="东财")
    page_zone = judge_pr(quote.pe_ttm, fin.roe_latest, fin.roe_5y_median, fin.is_cyclical)[2]
    screener_zone = default_pr_evaluator(FunnelCandidate(code="601318", quote=quote,
                                                         finance=fin)).valuation_zone
    assert page_zone == screener_zone == "合理偏低"
    assert compute_pr(quote.pe_ttm, fin) == 0.75
