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


def test_xdxr_records_transfer_extraction(monkeypatch):
    """mootdx xdxr 兜底链路的送转比例提取：字段映射实地验证（数据铁律）——
    songzhuangu 正确透传为 transfer_per_10；无送转行/NaN 行不炸、正确降级。"""
    import pandas as pd
    from unittest.mock import MagicMock

    df = pd.DataFrame([
        # 正常：10派2.1 + 10送转4
        {"year": 2021, "month": 6, "day": 15, "category": 1,
         "fenhong": 2.1, "songzhuangu": 4.0, "peigu": 0},
        # 无送转（0）→ transfer_per_10=None
        {"year": 2022, "month": 6, "day": 9, "category": 1,
         "fenhong": 5.0, "songzhuangu": 0.0, "peigu": 0},
        # songzhuangu 缺失（列 NaN）→ 不炸，transfer_per_10=None
        {"year": 2023, "month": 6, "day": 21, "category": 1,
         "fenhong": 5.0, "songzhuangu": float("nan"), "peigu": 0},
        # fenhong NaN → 整行跳过（#34 M1 NaN 防护）
        {"year": 2024, "month": 6, "day": 18, "category": 1,
         "fenhong": float("nan"), "songzhuangu": 4.0, "peigu": 0},
        # 非除权类目（category != 1）→ 跳过
        {"year": 2025, "month": 1, "day": 10, "category": 7,
         "fenhong": 1.0, "songzhuangu": 0.0, "peigu": 0},
    ])
    mock_client = MagicMock()
    mock_client.xdxr.return_value = df
    monkeypatch.setattr("src.datasource.mootdx_source.get_quotes_client", lambda: mock_client)

    from src.api import _get_xdxr_records
    records, source = _get_xdxr_records("603871")

    assert source == "mootdx xdxr"
    by_ex = {r.ex_dividend_date: r for r in records}
    assert set(by_ex) == {"2021-06-15", "2022-06-09", "2023-06-21"}
    assert by_ex["2021-06-15"].dividend_per_10 == 2.1
    assert by_ex["2021-06-15"].transfer_per_10 == 4.0
    assert by_ex["2022-06-09"].transfer_per_10 is None
    assert by_ex["2023-06-21"].transfer_per_10 is None
    assert by_ex["2023-06-21"].dividend_per_10 == 5.0
