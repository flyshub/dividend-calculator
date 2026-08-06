"""股息可持续性纯评估器单元测试（fixture 驱动，无网络）。"""
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit  # 标记为本单元测试（区分 integration）

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.sustainability_calculator import (
    AnnualFinancial,
    DividendHistory,
    SustainabilityResult,
    THRESHOLD_YIELD,
    compute_free_cash_flow,
    compute_cf_coverage,
    compute_fcf_coverage,
    compute_payout_ratio,
    check_fatal_flags,
    assess_sustainability,
)


# ---------------------------------------------------------------------------
# 衍生指标计算
# ---------------------------------------------------------------------------

def test_free_cash_flow():
    # 无 CAPEX → 降级用投资CF：经营CF 605亿 + 投资CF -312亿 = FCF 293亿
    assert compute_free_cash_flow(605e8, -312e8) == pytest.approx(293e8)


def test_free_cash_flow_with_capex():
    # 有 CAPEX → 用正确口径：经营CF − CAPEX（伊利画像：经营37.3亿 − CAPEX 5.55亿 = 31.8亿）
    assert compute_free_cash_flow(37.3e8, -64.7e8, 5.55e8) == pytest.approx(31.75e8)
    # CAPEX 优先级高于 investing_cf（不把买理财算进投资）
    assert compute_free_cash_flow(37.3e8, -64.7e8, 5.55e8) != pytest.approx(37.3e8 - 64.7e8)


def test_free_cash_flow_missing():
    assert compute_free_cash_flow(None, -312e8) is None
    assert compute_free_cash_flow(605e8, None) is None
    # operating_cf 缺失即使有 capex 也无法算
    assert compute_free_cash_flow(None, None, 5e8) is None


def test_cf_coverage():
    assert compute_cf_coverage(605e8, 200e8) == pytest.approx(3.025)


def test_cf_coverage_zero_dividend():
    assert compute_cf_coverage(605e8, 0) is None
    assert compute_cf_coverage(605e8, None) is None


def test_payout_ratio():
    assert compute_payout_ratio(50e8, 200e8) == pytest.approx(0.25)
    # 净利润为负/零 → 不计算（亏损股走致命红旗）
    assert compute_payout_ratio(50e8, 0) is None
    assert compute_payout_ratio(50e8, -10e8) is None


# ---------------------------------------------------------------------------
# 致命红旗
# ---------------------------------------------------------------------------

def test_fatal_payout_over_100():
    flags = check_fatal_flags(
        payout_ratio=1.2, fcf_coverage=2.0, operating_cf=100e8,
        net_profit=100e8, dividend_total=120e8,
    )
    assert any("股利支付率" in f and "> 100%" in f for f in flags)


def test_fatal_fcf_under_1x():
    flags = check_fatal_flags(
        payout_ratio=0.5, fcf_coverage=0.8, operating_cf=100e8,
        net_profit=100e8, dividend_total=120e8,
    )
    assert any("自由现金流覆盖" in f for f in flags)


def test_fatal_negative_cf_but_dividend():
    flags = check_fatal_flags(
        payout_ratio=None, fcf_coverage=None, operating_cf=-10e8,
        net_profit=50e8, dividend_total=5e8,
    )
    assert any("经营现金流为负" in f for f in flags)


def test_fatal_loss_but_dividend():
    flags = check_fatal_flags(
        payout_ratio=None, fcf_coverage=None, operating_cf=10e8,
        net_profit=-5e8, dividend_total=5e8,
    )
    assert any("净利润为负" in f for f in flags)


def test_no_fatal_flags_clean():
    flags = check_fatal_flags(
        payout_ratio=0.3, fcf_coverage=2.0, operating_cf=100e8,
        net_profit=100e8, dividend_total=30e8,
    )
    assert flags == []


# ---------------------------------------------------------------------------
# assess_sustainability 主流程 — 5 类边界
# ---------------------------------------------------------------------------

def _healthy_financial() -> AnnualFinancial:
    """健康可持续股（长江电力画像：高ROE/稳现金流/低负债）。"""
    return AnnualFinancial(
        year=2025,
        net_profit=345e8,
        net_profit_yoy=7.0,
        operating_cf=605e8,
        investing_cf=-312e8,
        total_assets=5620e8,
        total_liabilities=2918e8,
        debt_ratio=52.0,
        interest_debt_ratio=51.5,
        interest_coverage=6.37,
        roe=16.0,
        capital_adequacy_ratio=None,
        net_interest_margin=None,
        npl_ratio=None,
        provision_coverage=None,
    )


def _healthy_history() -> DividendHistory:
    return DividendHistory(
        consecutive_years=15, ever_cut=False,
        latest_year_amount=214e8, history_mean_amount=200e8,
    )


def test_healthy_stock_is_sustainable():
    """健康股（高ROE+强现金流+长分红史+防御行业）→ 可持续。"""
    result = assess_sustainability(
        dividend_yield_before_tax=4.5,
        dividend_total=214e8,
        latest=_healthy_financial(),
        history=_healthy_history(),
        industry="公用事业-电力-水电",
    )
    assert result.triggered is True
    assert result.verdict == "可持续"
    assert result.score is not None and result.score >= 1.5
    assert result.fatal_flags == []


def test_below_threshold_not_triggered():
    """股息率 ≤4% 不触发评估。"""
    result = assess_sustainability(
        dividend_yield_before_tax=3.5,
        dividend_total=214e8,
        latest=_healthy_financial(),
        history=_healthy_history(),
        industry="公用事业",
    )
    assert result.triggered is False
    assert result.verdict == "未评估"


def test_loss_stock_dividend_unsustainable():
    """亏损却分红 → 致命红旗 → 不可持续。"""
    fin = _healthy_financial()
    fin.net_profit = -10e8       # 亏损
    fin.net_profit_yoy = -120.0
    result = assess_sustainability(
        dividend_yield_before_tax=5.0,
        dividend_total=214e8,
        latest=fin,
        history=_healthy_history(),
        industry="煤炭",
    )
    assert result.verdict == "不可持续"
    assert any("净利润为负" in f for f in result.fatal_flags)
    assert result.score == 0.0


def test_payout_over_100_unsustainable():
    """支付率>100% → 致命红旗。"""
    fin = _healthy_financial()
    fin.net_profit = 100e8       # 净利润 100亿，分红 214亿 → 支付率 214%
    result = assess_sustainability(
        dividend_yield_before_tax=5.0,
        dividend_total=214e8,
        latest=fin,
        history=_healthy_history(),
        industry="煤炭",
    )
    assert result.verdict == "不可持续"
    assert any("股利支付率" in f and "> 100%" in f for f in result.fatal_flags)


def test_cyclical_top_triggers_warning():
    """周期股 + 利润拐头 + 高支付率 → 触发情境红旗并降档。"""
    fin = _healthy_financial()
    fin.net_profit = 100e8
    fin.net_profit_yoy = -25.0   # 利润同比下滑
    fin.roe = 18.0
    fin.interest_coverage = 8.0
    result = assess_sustainability(
        dividend_yield_before_tax=6.0,
        dividend_total=90e8,      # 支付率 90%（>80%，但 <100% 不致命）
        latest=fin,
        history=_healthy_history(),
        industry="煤炭",
    )
    assert result.triggered is True
    assert any("周期" in w for w in result.warning_flags)


def test_bank_uses_finance_branch():
    """银行股 → 走金融分支，看资本充足率等专项。"""
    bank_fin = AnnualFinancial(
        year=2025,
        net_profit=1500e8,
        net_profit_yoy=1.5,
        operating_cf=4514e8,
        investing_cf=None,
        total_assets=12e12,
        total_liabilities=11e12,
        debt_ratio=87.8,
        interest_debt_ratio=None,
        interest_coverage=None,
        roe=14.0,
        capital_adequacy_ratio=16.5,   # 资本充足 16.5%（健康）
        net_interest_margin=1.87,      # 净息差 1.87%
        npl_ratio=0.95,                # 不良 0.95%
        provision_coverage=200.0,      # 拨备 200%
    )
    result = assess_sustainability(
        dividend_yield_before_tax=5.0,
        dividend_total=350e8,
        latest=bank_fin,
        history=DividendHistory(consecutive_years=12, ever_cut=False,
                                latest_year_amount=350e8, history_mean_amount=340e8),
        industry="银行",
    )
    assert result.branch == "finance"
    # 银行专项全健康 → 高分
    assert result.score is not None and result.score >= 1.5
    assert "capital_adequacy" in result.dimension_scores


def test_bank_missing_specialty_falls_back():
    """银行但专项数据缺失 → 降级通用分支 + 标注。"""
    bank_fin = AnnualFinancial(
        year=2025,
        net_profit=100e8,
        net_profit_yoy=5.0,
        operating_cf=200e8,
        investing_cf=-50e8,
        total_assets=5000e8,
        total_liabilities=4400e8,
        debt_ratio=88.0,
        interest_debt_ratio=None,
        interest_coverage=None,
        roe=12.0,
        capital_adequacy_ratio=None,   # 专项全缺
        net_interest_margin=None,
        npl_ratio=None,
        provision_coverage=None,
    )
    result = assess_sustainability(
        dividend_yield_before_tax=5.0,
        dividend_total=30e8,
        latest=bank_fin,
        history=_healthy_history(),
        industry="银行",
    )
    assert result.branch == "general-fallback"
    assert any("银行专项" in n for n in result.notes)


def test_missing_financial_data():
    """财务数据完全缺失 → 不可持续 + 致命红旗说明。"""
    result = assess_sustainability(
        dividend_yield_before_tax=5.0,
        dividend_total=214e8,
        latest=None,
        history=None,
        industry="公用事业",
    )
    assert result.verdict == "不可持续"
    assert any("缺少财务数据" in f for f in result.fatal_flags)


def test_weak_cf_coverage_lowers_score():
    """经营现金流覆盖偏低 → 偏弱档。"""
    fin = _healthy_financial()
    fin.operating_cf = 100e8       # 经营CF仅100亿，分红214亿 → 覆盖0.47x
    fin.investing_cf = 0           # FCF=100亿，覆盖0.47x（致命）
    result = assess_sustainability(
        dividend_yield_before_tax=5.0,
        dividend_total=214e8,
        latest=fin,
        history=_healthy_history(),
        industry="公用事业",
    )
    # FCF覆盖<1x → 致命红旗
    assert result.verdict == "不可持续"
    assert any("自由现金流覆盖" in f for f in result.fatal_flags)
