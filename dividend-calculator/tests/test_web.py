import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.dividend import DividendDetail, DividendResult
from src.pr import PRResult
from src.web import serialize_result, serialize_pr_result


def test_serialize_result_with_dividend_details():
    result = DividendResult(
        stock_code="600900",
        stock_name="长江电力",
        current_price=26.56,
        total_shares=22741859116.0,
        total_market_cap=604024497320.96,
        total_dividend=21445553126.388,
        dividend_yield_before_tax=3.550443,
        dividend_yield_after_tax=3.195399,
        dividend_yield_after_tax_20=2.840354,
        latest_year="2025",
        dividend_details=[DividendDetail("2025年报", 7.33)],
        explanation="测试说明",
    )

    data = serialize_result(result)

    assert data["stock_code"] == "600900"
    assert data["stock_name"] == "长江电力"
    assert data["has_dividend"] is True
    assert data["dividend_details"] == [
        {"report_time": "2025年报", "dividend_per_10": 7.33}
    ]


def test_serialize_result_without_dividend():
    result = DividendResult(
        stock_code="000000",
        stock_name=None,
        current_price=0.0,
        total_shares=0.0,
        total_market_cap=0.0,
        total_dividend=0.0,
        dividend_yield_before_tax=0.0,
        dividend_yield_after_tax=0.0,
        dividend_yield_after_tax_20=0.0,
        latest_year=None,
        dividend_details=[],
        explanation="无有效分红",
    )

    data = serialize_result(result)

    assert data["has_dividend"] is False
    assert data["latest_year"] is None
    assert data["dividend_details"] == []


def test_serialize_result_all_fields_present():
    result = DividendResult(
        stock_code="600036",
        stock_name="招商银行",
        current_price=35.0,
        total_shares=25_000_000_000.0,
        total_market_cap=875_000_000_000.0,
        total_dividend=35_000_000_000.0,
        dividend_yield_before_tax=4.0,
        dividend_yield_after_tax=3.6,
        dividend_yield_after_tax_20=3.2,
        latest_year="2024",
        dividend_details=[DividendDetail("2024年报", 1.738)],
        explanation="测试",
    )

    data = serialize_result(result)
    expected_keys = {
        "stock_code", "stock_name", "current_price", "total_shares",
        "total_market_cap", "total_dividend", "dividend_yield_before_tax",
        "dividend_yield_after_tax", "dividend_yield_after_tax_20",
        "latest_year", "has_dividend", "dividend_details", "explanation",
        "warnings", "dividend_source",
        "ttm_dividend", "dividend_yield_ttm_before_tax", "ttm_period", "ttm_source",
    }
    assert set(data.keys()) == expected_keys


def test_serialize_pr_result_basic():
    result = PRResult(
        stock_code="600900",
        stock_name="长江电力",
        pr_basic=1.5,
        pr_corrected=1.2,
        pr_pb=0.8,
        valuation_zone="合理偏低",
        pe_ttm=18.0,
        pb=3.5,
        roe_latest=15.9,
        roe_5y_median=14.5,
        net_profit_latest_period=30_000_000_000.0,
        net_profit_annual=28_000_000_000.0,
        dividend_total=21_000_000_000.0,
        payout_ratio=0.75,
        n_factor=0.85,
        industry="电力",
        is_cyclical=False,
        is_tech=False,
        is_loss_stock=False,
        pr_warning="",
        pe_pb_source="tencent",
        finance_source="同花顺",
        industry_source="eastmoney",
        errors=[],
    )

    data = serialize_pr_result(result)

    assert data["stock_code"] == "600900"
    assert data["stock_name"] == "长江电力"
    assert data["pr_basic"] == 1.5
    assert data["pr_corrected"] == 1.2
    assert data["pr_pb"] == 0.8
    assert data["valuation_zone"] == "合理偏低"
    assert data["pe_ttm"] == 18.0
    assert data["pb"] == 3.5
    assert data["is_cyclical"] is False
    assert data["errors"] == []


def test_serialize_pr_result_with_errors():
    result = PRResult(
        stock_code="600000",
        stock_name="浦发银行",
        pr_basic=0.5,
        pr_corrected=None,
        pr_pb=None,
        valuation_zone="低估",
        pe_ttm=5.0,
        pb=0.4,
        roe_latest=8.0,
        roe_5y_median=9.0,
        net_profit_latest_period=5_000_000_000.0,
        net_profit_annual=4_800_000_000.0,
        dividend_total=2_000_000_000.0,
        payout_ratio=0.4,
        n_factor=0.9,
        industry="银行",
        is_cyclical=True,
        is_tech=False,
        is_loss_stock=False,
        pr_warning="周期股PR仅供参考",
        pe_pb_source="tencent",
        finance_source="eastmoney",
        industry_source="eastmoney",
        errors=["ROE_5Y取值失败，使用latest ROE"],
    )

    data = serialize_pr_result(result)

    assert data["pr_corrected"] is None
    assert data["pr_pb"] is None
    assert data["is_cyclical"] is True
    assert data["pr_warning"] == "周期股PR仅供参考"
    assert data["errors"] == ["ROE_5Y取值失败，使用latest ROE"]


def test_serialize_pr_result_all_fields():
    result = PRResult(
        stock_code="000858",
        stock_name="五粮液",
        pr_basic=1.0,
        pr_corrected=0.9,
        pr_pb=0.7,
        valuation_zone="合理偏低",
        pe_ttm=20.0,
        pb=6.0,
        roe_latest=25.0,
        roe_5y_median=22.0,
        net_profit_latest_period=30_000_000_000.0,
        net_profit_annual=28_000_000_000.0,
        dividend_total=15_000_000_000.0,
        payout_ratio=0.53,
        n_factor=0.95,
        industry="白酒",
        is_cyclical=False,
        is_tech=False,
        is_loss_stock=False,
        pr_warning="",
        pe_pb_source="tencent",
        finance_source="同花顺",
        industry_source="eastmoney",
        errors=[],
    )

    data = serialize_pr_result(result)
    expected_keys = {
        "stock_code", "stock_name", "pr_basic", "pr_corrected", "pr_pb",
        "valuation_zone", "pe_ttm", "pb", "roe_latest", "roe_5y_median",
        "net_profit_latest_period", "net_profit_annual", "dividend_total",
        "payout_ratio", "n_factor", "industry", "is_cyclical", "is_tech",
        "is_loss_stock", "pr_warning", "pe_pb_source", "finance_source",
        "industry_source", "errors", "roe_period",
    }
    assert set(data.keys()) == expected_keys
