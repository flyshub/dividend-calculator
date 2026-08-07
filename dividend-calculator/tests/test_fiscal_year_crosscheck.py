"""test_fiscal_year_crosscheck.py — 三套财年判定口径交叉校验（审查 #8）

两套语义：
- 除权月口径（utils.infer_fiscal_year）：财年归属，3-8月除权→上年年报
- 报告期月口径（sustainability.parse_dividend_rows / dividend._parse_fhps_detail）：
  报告期类型，12/3/4月→年报，6/9月→半年报

差异来自**输入信号不同**（除权日 vs 报告期），非规则本身。本文件固化
「正常场景一致」+「已知不一致场景」，防止未来被静默破坏。
"""
import pytest

from src.utils import infer_fiscal_year, FiscalYear
from src.sustainability import parse_dividend_rows
from src.dividend import _parse_fhps_detail


def _rows_for_report_period(report_date: str) -> list:
    """构造一条东财分红行（报告期口径用）"""
    return [{
        "REPORT_DATE": report_date,
        "PRETAX_BONUS_RMB": 5.0,
        "ASSIGN_PROGRESS": "实施",
        "EX_DIVIDEND_DATE": "",
    }]


def _report_period_label(rows) -> str:
    """用报告期口径推导 label（parse_dividend_rows）"""
    records, _ = parse_dividend_rows(rows)
    return records[0].report_time


# ── 正常场景：两套口径结论一致 ──────────────────────────────────────────

class TestConsistentScenarios:
    def test_july_exdiv_with_dec_report(self):
        """7月除权（→上年年报）× 报告期12-31（→上年年报）：一致"""
        fy = infer_fiscal_year(2026, 7)
        assert (fy.year, fy.is_annual) == (2025, True)
        assert _report_period_label(_rows_for_report_period("2025-12-31")) == "2025年报"

    def test_april_exdiv_with_mar_report(self):
        """4月除权（→上年年报）× 报告期3-31（→上年年报）：一致"""
        fy = infer_fiscal_year(2026, 4)
        assert (fy.year, fy.is_annual) == (2025, True)
        assert _report_period_label(_rows_for_report_period("2025-03-31")) == "2025年报"

    def test_oct_exdiv_with_sep_report(self):
        """10月除权（→当年中报）× 报告期6-30（→当年半年报）：语义一致（中报/半年报同义）"""
        fy = infer_fiscal_year(2026, 10)
        assert (fy.year, fy.is_annual) == (2026, False)
        assert _report_period_label(_rows_for_report_period("2026-06-30")) == "2026半年报"
        # 两套口径都标记为非年报
        assert fy.is_annual is False


# ── 已知不一致场景：固化差异，防静默破坏 ───────────────────────────────

class TestKnownDivergence:
    def test_cross_year_exdiv(self):
        """跨年除权：除权日 2026-01（→2025中报）vs 报告期 2025-12-31（→2025年报）

        结构性差异：mootdx 只给除权日、无报告期，两者语义本就不同。
        """
        fy = infer_fiscal_year(2026, 1)  # 1-2月 → 上年度中报
        assert (fy.year, fy.is_annual) == (2025, False)  # 2025中报
        assert _report_period_label(_rows_for_report_period("2025-12-31")) == "2025年报"
        # 断言两者差异（防未来被"统一"破坏）
        assert fy.is_annual != True  # 中报 vs 年报，is_annual 不同

    def test_q1_special_dividend(self):
        """Q1 特别分红：报告期 3-31（→年报）在 9-12 月除权（→当年中报）

        A 股少数公司 Q1 即分红，真实存在的差异场景。
        """
        assert _report_period_label(_rows_for_report_period("2025-03-31")) == "2025年报"
        fy = infer_fiscal_year(2025, 10)  # 若除权在 10 月 → 2025中报
        assert (fy.year, fy.is_annual) == (2025, False)

    def test_midyear_special_dividend(self):
        """年中特别分红：报告期 6-30（→半年报）在次年 4 月除权（→上年年报）"""
        assert _report_period_label(_rows_for_report_period("2025-06-30")) == "2025半年报"
        fy = infer_fiscal_year(2026, 4)  # 次年4月除权 → 2025年报
        assert (fy.year, fy.is_annual) == (2025, True)


# ── fhps_detail（akshare 报告期口径）与 parse_dividend_rows 一致 ─────────

class TestReportPeriodConsistency:
    def test_fhps_and_parse_rows_same_rule(self):
        """两条报告期链路（_parse_fhps_detail vs parse_dividend_rows）同规则"""
        import pandas as pd
        from src.datasource.base import StockInfo

        stock_info = StockInfo(stock_code="600900", current_price=26.56, total_shares=2.27e10)

        # 12-31 → 年报（akshare fhps 用 报告期 列）
        df_annual = pd.DataFrame([{
            "报告期": "2025-12-31", "方案进度": "实施",
            "现金分红-现金分红比例": 5.0,
        }])
        total, year, details, _ = _parse_fhps_detail(df_annual, stock_info)
        assert details[0].report_time == "2025年报"
        assert _report_period_label(_rows_for_report_period("2025-12-31")) == "2025年报"

        # 6-30 → 半年报
        df_half = pd.DataFrame([{
            "报告期": "2025-06-30", "方案进度": "实施",
            "现金分红-现金分红比例": 5.0,
        }])
        total2, year2, details2, _ = _parse_fhps_detail(df_half, stock_info)
        assert details2[0].report_time == "2025半年报"
        assert _report_period_label(_rows_for_report_period("2025-06-30")) == "2025半年报"
