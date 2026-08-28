import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.datasource.base import MonthlyPrice, DividendRecord, HistoricalData


def test_monthly_price_creation():
    mp = MonthlyPrice(date="2024-06-30", close=25.50)
    assert mp.date == "2024-06-30"
    assert mp.close == 25.50
    assert mp.close_nominal is None  # 默认缺省（不复权请求失败的降级形态）


def test_monthly_price_dual_quotes():
    """走势图总额法双价格口径：前复权（画图）+ 不复权（算股息率）"""
    mp = MonthlyPrice(date="2021-01-29", close=2.185, close_nominal=19.17)
    assert mp.close == 2.185
    assert mp.close_nominal == 19.17


def test_dividend_record_creation():
    dr = DividendRecord(
        ex_dividend_date="2024-07-11",
        dividend_per_10=19.72,
        report_time="2023年度",
    )
    assert dr.ex_dividend_date == "2024-07-11"
    assert dr.dividend_per_10 == 19.72
    assert dr.total_shares is None      # cninfo/mootdx 路径无股本 → None（宁缺毋假）
    assert dr.transfer_per_10 is None


def test_dividend_record_total_shares_and_transfer():
    """登记股本（除权前口径）+ 送转比例：走势图股息率总额法的股本锚点"""
    dr = DividendRecord(
        ex_dividend_date="2020-06-15",
        dividend_per_10=10.0,
        report_time="2019年报",
        plan_notice_date="2020-04-28",
        total_shares=1.568e8,
        transfer_per_10=4.0,
    )
    assert dr.total_shares == 1.568e8
    assert dr.transfer_per_10 == 4.0


def test_historical_data_creation():
    prices = [
        MonthlyPrice(date="2024-01-31", close=22.00),
        MonthlyPrice(date="2024-02-29", close=23.50),
    ]
    dividends = [
        DividendRecord(
            ex_dividend_date="2024-01-15",
            dividend_per_10=5.0,
            report_time="2023年度",
        ),
    ]
    hd = HistoricalData(
        stock_code="600900",
        stock_name="长江电力",
        monthly_prices=prices,
        dividend_records=dividends,
    )
    assert hd.stock_code == "600900"
    assert hd.stock_name == "长江电力"
    assert len(hd.monthly_prices) == 2
    assert len(hd.dividend_records) == 1
    assert hd.total_shares_now is None  # quote 失败时缺省


def test_historical_data_name_none():
    """stock_name can be None when lookup fails"""
    hd = HistoricalData(
        stock_code="000000",
        stock_name=None,
        monthly_prices=[],
        dividend_records=[],
    )
    assert hd.stock_name is None
