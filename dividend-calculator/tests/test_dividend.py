import sys
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.dividend import calculate_dividend_yield, calculate_true_dividend_yield
from src.datasource.base import StockInfo, DividendDetail


def test_calculate_dividend_yield():
    before_tax, after_tax, after_tax_20 = calculate_dividend_yield(214.46e8, 6040.24e8)

    assert before_tax == pytest.approx(3.55, abs=0.01)
    assert after_tax == pytest.approx(3.20, abs=0.01)
    assert after_tax_20 == pytest.approx(2.84, abs=0.01)


def test_calculate_dividend_yield_with_zero_market_cap():
    assert calculate_dividend_yield(100, 0) == (0.0, 0.0, 0.0)


def test_dividend_result_with_sample_data():
    """测试股息率计算使用样本数据"""
    # 使用长江电力的样本数据进行测试
    total_shares = 22741859116.0
    current_price = 26.56
    total_market_cap = current_price * total_shares
    total_dividend = 21445553126.388

    before_tax, after_tax, after_tax_20 = calculate_dividend_yield(total_dividend, total_market_cap)

    assert before_tax == pytest.approx(3.55, abs=0.01)
    assert after_tax == pytest.approx(3.20, abs=0.01)
    assert after_tax_20 == pytest.approx(2.84, abs=0.01)


# ---------------------------------------------------------------------------
# 依赖注入接缝测试 — 无需网络即可验证完整编排路径
# ---------------------------------------------------------------------------

def _fake_stock_info(code: str) -> StockInfo:
    """Fake 股票信息提供器。"""
    return StockInfo(
        stock_code="600900",
        current_price=25.0,
        total_shares=10_000_000.0,  # 1000万股
    )


def _fake_dividend(code: str, info: StockInfo):
    """Fake 分红数据提供器：10派1.25元 × 1000万股 = 125万分红。"""
    details = [DividendDetail(report_time="20251231", dividend_per_10=1.25)]
    return 1_250_000.0, "2025", details, "2025年度10派1.25元", "mock"


def test_di_seam_full_pipeline():
    """通过注入 fake provider，无需网络即可验证完整计算流水线。"""
    result = calculate_true_dividend_yield(
        "600900",
        stock_info_provider=_fake_stock_info,
        dividend_provider=_fake_dividend,
    )

    assert result is not None
    assert result.stock_code == "600900"
    assert result.current_price == 25.0
    assert result.total_shares == 10_000_000.0
    # 总市值 = 25.0 × 1000万股 = 2.5亿
    assert result.total_market_cap == 250_000_000.0
    # 总分红 = 125万
    assert result.total_dividend == 1_250_000.0
    # 股息率 = 125万 / 25000万 × 100 = 0.5%
    assert result.dividend_yield_before_tax == 0.5
    assert result.dividend_yield_after_tax == 0.45    # × 0.9
    assert result.dividend_yield_after_tax_20 == 0.4   # × 0.8
    assert result.latest_year == "2025"
    assert len(result.dividend_details) == 1
    assert "2025年度10派1.25元" in result.explanation


@pytest.mark.integration
def test_di_seam_defaults_still_work():
    """不传 provider 时，函数正常执行（使用真实数据源）。"""
    result = calculate_true_dividend_yield("600900")
    # 可能返回 None（网络不可用），也可能返回结果——都不应抛异常
    if result is not None:
        assert result.stock_code == "600900"
        assert result.current_price > 0
        assert result.total_shares > 0
    # 无论网络通不通，函数不应崩溃
    assert True


def test_di_seam_invalid_stock():
    """注入返回 None 的 provider，模拟数据不可用。"""
    result = calculate_true_dividend_yield(
        "999999",
        stock_info_provider=lambda code: None,
        dividend_provider=_fake_dividend,
    )
    assert result is None
