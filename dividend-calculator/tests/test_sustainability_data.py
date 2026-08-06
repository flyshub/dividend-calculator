"""sustainability.py 数据获取层单元测试（fixture 驱动，不打网络）。"""
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit  # 标记为本单元测试（区分 integration）

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.datasource.base import DividendRecord
from src.sustainability import (
    parse_financial_rows,
    parse_dividend_rows,
    select_latest_annual,
    aggregate_dividend_history,
    assess_for_stock,
)


# ---------------------------------------------------------------------------
# parse_financial_rows（东财字段 → AnnualFinancial）
# ---------------------------------------------------------------------------

def _make_finance_rows():
    """模拟东财 RPT_F10_FINANCE_MAINFINADATA 响应行（用验证过的真实字段名）。"""
    return [
        {
            "REPORT_DATE": "2025-12-31 00:00:00",
            "PARENTNETPROFIT": 34502809176.39,
            "PARENTNETPROFITTZ": 7.1,
            "NETCASH_OPERATE_PK": 60562925570.41,
            "NETCASH_INVEST_PK": -31264415237.5,
            "TOTAL_ASSETS_PK": 561990500889.54,
            "LIABILITY": 322172683239.63,
            "DEBT_ASSET_RATIO": 52.0,
            "INTEREST_DEBT_RATIO": 51.5,
            "INTEREST_COVERAGE_RATIO": 6.37,
            "ROEJQ": 16.0,
            "FIRST_ADEQUACY_RATIO": None,
            "NET_INTEREST_MARGIN": None,
            "NON_PERFORMING_LOAN": None,
            "RISK_COVERAGE": None,
        },
        {
            "REPORT_DATE": "2024-12-31 00:00:00",
            "PARENTNETPROFIT": 325e8,
            "PARENTNETPROFITTZ": 5.0,
            "NETCASH_OPERATE_PK": 580e8,
            "NETCASH_INVEST_PK": -300e8,
            "TOTAL_ASSETS_PK": 5300e8,
            "LIABILITY": 280e8,
            "DEBT_ASSET_RATIO": 52.8,
            "INTEREST_DEBT_RATIO": 50.0,
            "INTEREST_COVERAGE_RATIO": 7.0,
            "ROEJQ": 15.5,
        },
        {
            "REPORT_DATE": "2025-09-30 00:00:00",  # 季报（应被 select_latest_annual 跳过）
            "PARENTNETPROFIT": 28192874494.95,
            "NETCASH_OPERATE_PK": 42895214451.84,
            "ROEJQ": 13.0,
        },
    ]


def test_parse_financial_rows_basic():
    rows = _make_finance_rows()
    fins = parse_financial_rows(rows)
    # parse 只保留年报（12-31）行 → 2 条年报（季报 2025-09-30 被过滤）
    assert len(fins) == 2
    # 年份解析正确
    assert fins[0].year == 2025
    # 关键字段映射正确
    assert fins[0].net_profit == pytest.approx(34502809176.39)
    assert fins[0].operating_cf == pytest.approx(60562925570.41)
    assert fins[0].investing_cf == pytest.approx(-31264415237.5)
    assert fins[0].debt_ratio == 52.0
    assert fins[0].interest_coverage == 6.37
    assert fins[0].roe == 16.0


def test_parse_financial_rows_missing_fields_are_none():
    rows = [
        {"REPORT_DATE": "2025-12-31 00:00:00", "PARENTNETPROFIT": 100e8},
    ]
    fins = parse_financial_rows(rows)
    assert fins[0].net_profit == 1e10
    assert fins[0].operating_cf is None
    assert fins[0].capital_adequacy_ratio is None


def test_parse_financial_rows_empty_string_as_none():
    """空字符串视为缺失（避免 float('') 报错或 0 污染）。"""
    rows = [{"REPORT_DATE": "2025-12-31", "NETCASH_OPERATE_PK": "", "ROEJQ": None}]
    fins = parse_financial_rows(rows)
    assert fins[0].operating_cf is None
    assert fins[0].roe is None


def test_select_latest_annual_prefers_dividend_year():
    """select_latest_annual 应优先匹配分红所属财年（即使有更新的年报）。"""
    fins = parse_financial_rows(_make_finance_rows())  # 2025、2024 两份年报
    latest = select_latest_annual(fins, "2024")
    assert latest.year == 2024  # 匹配 target_year=2024，而非最新 2025


def test_select_latest_annual_empty():
    assert select_latest_annual([]) is None


# ---------------------------------------------------------------------------
# aggregate_dividend_history
# ---------------------------------------------------------------------------

def _div_records():
    """2015~2025 连续 11 年分红，2023 年曾削减（用于测 ever_cut）。"""
    recs = []
    for y in range(2015, 2026):
        dp10 = 5.0  # 每年10派5元
        if y == 2023:
            dp10 = 2.0  # 削减（5→2，降幅60%）
        recs.append(DividendRecord(
            ex_dividend_date=f"{y}-07-01",
            dividend_per_10=dp10,
            report_time=f"{y}年报",
        ))
    return recs


def test_aggregate_consecutive_years():
    total_shares = 1e9  # 10亿股
    h = aggregate_dividend_history(_div_records(), "2025", total_shares)
    assert h.consecutive_years == 11  # 2015~2025 连续


def test_aggregate_detects_cut():
    total_shares = 1e9
    h = aggregate_dividend_history(_div_records(), "2025", total_shares)
    assert h.ever_cut is True  # 2023 削减


def test_aggregate_latest_and_mean():
    total_shares = 1e9
    h = aggregate_dividend_history(_div_records(), "2025", total_shares)
    # 最新年（2025）每10派5元 × 1亿股单位 = 5亿
    assert h.latest_year_amount == pytest.approx(5.0e8)
    # 历史均值（2015~2024 共10年：9年×5亿 + 2023年2亿）/ 10
    assert h.history_mean_amount == pytest.approx((9 * 5e8 + 2e8) / 10)


def test_aggregate_empty_records():
    h = aggregate_dividend_history([], "2025", 1e9)
    assert h.consecutive_years == 0
    assert h.latest_year_amount is None


def test_aggregate_consecutive_breaks():
    """中间断档 → 连续年数只算最近的连续段。"""
    recs = [
        DividendRecord("2025-07-01", 5.0, "2025年报"),
        DividendRecord("2024-07-01", 5.0, "2024年报"),
        # 2023 没分红（断档）
        DividendRecord("2021-07-01", 5.0, "2021年报"),
    ]
    h = aggregate_dividend_history(recs, "2025", 1e9)
    assert h.consecutive_years == 2  # 只有 2024-2025 连续


# ---------------------------------------------------------------------------
# parse_dividend_rows（东财分红明细 → DividendRecord + 最新财年）
# ---------------------------------------------------------------------------

def test_parse_dividend_rows_skips_preset():
    """跳过"预披露"方案。"""
    rows = [
        {"REPORT_DATE": "2025-12-31", "PRETAX_BONUS_RMB": 7.9, "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2026-07-15"},
        {"REPORT_DATE": "2025-03-31", "PRETAX_BONUS_RMB": 3.0, "ASSIGN_PROGRESS": "预披露", "EX_DIVIDEND_DATE": ""},
    ]
    records, year = parse_dividend_rows(rows)
    assert len(records) == 1
    assert records[0].dividend_per_10 == pytest.approx(7.9)
    assert year == "2025"


def test_parse_dividend_rows_distinguishes_annual():
    """年报(12月) vs 半年报(6/9月)。"""
    rows = [
        {"REPORT_DATE": "2025-12-31", "PRETAX_BONUS_RMB": 5.0, "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2026-07-01"},
        {"REPORT_DATE": "2025-06-30", "PRETAX_BONUS_RMB": 2.0, "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2025-12-01"},
        {"REPORT_DATE": "2024-12-31", "PRETAX_BONUS_RMB": 5.0, "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2025-07-01"},
    ]
    records, year = parse_dividend_rows(rows)
    assert year == "2025"  # 最新有年报的财年
    labels = [r.report_time for r in records]
    assert "2025年报" in labels
    assert "2025半年报" in labels


def test_parse_dividend_rows_empty():
    records, year = parse_dividend_rows([])
    assert records == []
    assert year is None




def test_assess_for_stock_healthy_sustainable():
    rows = _make_finance_rows()
    # 连续 11 年稳定分红（无削减），匹配健康可持续画像
    records = [
        DividendRecord(f"{y}-07-01", 5.0, f"{y}年报") for y in range(2015, 2026)
    ]
    result = assess_for_stock(
        stock_code="600900",
        total_shares=2.4468e10,
        dividend_total=214e8,
        dividend_yield_before_tax=4.5,
        latest_dividend_year="2025",
        industry="公用事业-电力-水电",
        dividend_records=records,
        financial_rows=rows,
    )
    assert result.triggered is True
    assert result.verdict == "可持续"
    assert result.fatal_flags == []


def test_assess_for_stock_below_threshold():
    result = assess_for_stock(
        stock_code="600900",
        total_shares=1e9,
        dividend_total=10e8,
        dividend_yield_before_tax=3.0,
        latest_dividend_year="2025",
        industry="公用事业",
        dividend_records=[],
        financial_rows=[],
    )
    assert result.triggered is False


def test_assess_for_stock_bank_finance_branch():
    """银行股 → 金融分支（资本充足率等专项有效）。"""
    bank_rows = [{
        "REPORT_DATE": "2025-12-31 00:00:00",
        "PARENTNETPROFIT": 150181000000,
        "NETCASH_OPERATE_PK": 451457000000,
        "NETCASH_INVEST_PK": None,
        "TOTAL_ASSETS_PK": 12e12,
        "LIABILITY": 11e12,
        "DEBT_ASSET_RATIO": 91.5,
        "ROEJQ": 14.0,
        "ADEQUACY_RATIO": 16.5,
        "NET_INTEREST_MARGIN": 1.87,
        "NON_PERFORMING_LOAN": 0.95,
        "RISK_COVERAGE": 200.0,
    }]
    result = assess_for_stock(
        stock_code="600036",
        total_shares=2.5e11,
        dividend_total=350e8,
        dividend_yield_before_tax=5.0,
        latest_dividend_year="2025",
        industry="银行",
        dividend_records=[
            DividendRecord(f"{y}-07-01", 5.0, f"{y}年报") for y in range(2015, 2026)
        ],
        financial_rows=bank_rows,
    )
    assert result.branch == "finance"
    assert "capital_adequacy" in result.dimension_scores
    assert result.score is not None and result.score >= 1.5
