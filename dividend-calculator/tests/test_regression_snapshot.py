"""数据快照回归测试套件（评审建议7 / #18）

冻结「固定输入 → 期望输出」的真实数据快照，防止数据源字段变更或解析
逻辑漂移导致静默错误。全部 @pytest.mark.unit，CI 的 -m 'not integration'
已覆盖，无网络。

五部分：
1. 股息率快照（长江电力 + 招商银行 A+H，DI 注入）
2. 财年分组快照（_parse_fhps_detail：年报/半年报/特殊月份归类）
3. PR 真实画像（茅台/银行/亏损股）
4. 可持续性快照（长江电力画像 → verdict）
5. 东财字段映射快照（字段名变更立即炸）
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.dividend import _parse_fhps_detail, calculate_true_dividend_yield
from src.utils import compute_ttm_dividend
from src.pr_calculator import (
    compute_basic_pr, compute_pb_pr, classify_valuation,
)
from src.datasource.base import StockInfo, DividendDetail
from src.sustainability import parse_dividend_rows, parse_financial_rows
from src.sustainability_calculator import assess_sustainability, AnnualFinancial, DividendHistory


# ---------------------------------------------------------------------------
# 1. 股息率快照（DI 注入，无网络）
# ---------------------------------------------------------------------------

class TestDividendYieldSnapshot:
    def _stock_info(self, code="600900", price=26.56, shares=2.2741859116e10):
        return StockInfo(stock_code=code, current_price=price, total_shares=shares)

    def test_changjiang_power_snapshot(self):
        """长江电力：10派7.33元(2025年报) × 227.4亿股 = 166.6亿分红。"""
        def fake_dividend(code, info):
            details = [DividendDetail(report_time="2025年报", dividend_per_10=7.33)]
            # 每股 0.733 × 227.4亿 = 166.7亿
            total = 0.733 * info.total_shares
            return total, "2025", details, "2025年度10派7.33元", "mock"

        result = calculate_true_dividend_yield(
            "600900",
            stock_info_provider=lambda code: self._stock_info(),
            dividend_provider=fake_dividend,
        )
        assert result is not None
        assert result.stock_code == "600900"
        assert result.current_price == 26.56
        # 总市值 = 26.56 × 227.4亿
        assert result.total_market_cap == pytest.approx(26.56 * 2.2741859116e10)
        # 总分红 = 0.733 × 227.4亿
        assert result.total_dividend == pytest.approx(0.733 * 2.2741859116e10)
        # 股息率 = 0.733/26.56 × 100 ≈ 2.76%
        assert result.dividend_yield_before_tax == pytest.approx(0.733 / 26.56 * 100, abs=0.01)

    def test_cmb_ah_snapshot(self):
        """招商银行（A+H）：总股本 252.2亿 > A股 206.3亿，用总股本。"""
        info = self._stock_info(code="600036", price=38.80, shares=2.522e10)  # 252.2亿

        def fake_dividend(code, si):
            details = [DividendDetail(report_time="2024年报", dividend_per_10=1.972)]
            return 0.1972 * si.total_shares, "2024", details, "2024年度10派1.972元", "mock"

        result = calculate_true_dividend_yield(
            "600036",
            stock_info_provider=lambda code: info,
            dividend_provider=fake_dividend,
        )
        assert result is not None
        # 用总股本（252.2亿）而非 A 股股本 —— A+H 正确口径
        assert result.total_shares == 2.522e10
        assert result.total_dividend == pytest.approx(0.1972 * 2.522e10)


# ---------------------------------------------------------------------------
# 2. 财年分组快照（_parse_fhps_detail）
# ---------------------------------------------------------------------------

class TestFiscalYearGroupingSnapshot:
    def _info(self):
        return StockInfo(stock_code="600900", current_price=26.56, total_shares=1e9)

    def test_annual_halfyear_grouping(self):
        """2024-12-31 年报 + 2025-06-30 半年报 → 目标财年选 2024（优先有年报）。"""
        df = pd.DataFrame([
            {"报告期": "2024-12-31", "方案进度": "实施", "现金分红-现金分红比例": 5.0},
            {"报告期": "2025-06-30", "方案进度": "实施", "现金分红-现金分红比例": 2.0},
        ])
        total, year, details, _ = _parse_fhps_detail(df, self._info())
        assert year == "2024"  # 优先选有年报的财年
        assert len(details) == 1
        assert details[0].report_time == "2024年报"
        assert details[0].dividend_per_10 == 5.0

    def test_special_month_grouped_as_annual(self):
        """特殊月份（3月）报告期分红 → 归年报（锁定当前行为，与 JS calculator.js:64 对齐）。"""
        df = pd.DataFrame([
            {"报告期": "2025-03-31", "方案进度": "实施", "现金分红-现金分红比例": 3.0},
        ])
        total, year, details, _ = _parse_fhps_detail(df, self._info())
        assert year == "2025"
        assert details[0].report_time == "2025年报"  # 特殊月份归年报（防御分支）

    def test_quarterly_accumulated_grouping(self):
        """Q1+中报+Q3 同财年累加：2024-03-31 + 2024-06-30 + 2024-09-30 归 2024 财年。"""
        df = pd.DataFrame([
            {"报告期": "2024-03-31", "方案进度": "实施", "现金分红-现金分红比例": 1.0},
            {"报告期": "2024-06-30", "方案进度": "实施", "现金分红-现金分红比例": 2.0},
            {"报告期": "2024-09-30", "方案进度": "实施", "现金分红-现金分红比例": 1.5},
        ])
        total, year, details, _ = _parse_fhps_detail(df, self._info())
        assert year == "2024"
        # 3条明细，同财年累加
        assert len(details) == 3
        assert sum(d.dividend_per_10 for d in details) == pytest.approx(4.5)


# ---------------------------------------------------------------------------
# 2b. TTM 股息率快照（#19）
# ---------------------------------------------------------------------------

class TestTtmSnapshot:
    def test_ttm_12month_window(self):
        """近12个月按除权日聚合：as_of 固定日期，窗口外分红不计入。"""
        from datetime import date
        from src.datasource.base import DividendRecord
        records = [
            # 窗口内（2025-08-01 ~ 2026-07-31）
            DividendRecord("2026-07-15", 5.0, "2025年报"),
            DividendRecord("2026-01-10", 2.0, "2025半年报"),
            # 窗口外（>365天前）
            DividendRecord("2025-05-01", 3.0, "2024年报"),
        ]
        ttm_total, start, end, count = compute_ttm_dividend(
            records, total_shares=1e9, as_of_date=date(2026, 7, 31)
        )
        # 每股 0.5 + 0.2 = 0.7 × 10亿 = 7亿
        assert ttm_total == pytest.approx(0.7 * 1e9)
        assert count == 2
        assert start == "2025-07-31"

    def test_ttm_no_records_returns_none(self):
        from datetime import date
        assert compute_ttm_dividend([], 1e9, as_of_date=date(2026, 7, 31))[0] is None


# ---------------------------------------------------------------------------
# 3. PR 真实画像
# ---------------------------------------------------------------------------

class TestPRSnapshot:
    def test_moutai_pe_roe(self):
        """贵州茅台画像：PE=30, ROE=30% → basic_pr = 30/30 = 1.0。"""
        assert compute_basic_pr(30.0, 30.0) == pytest.approx(1.0)

    def test_bank_pb_roe(self):
        """银行画像：PB=0.69, ROE=10.7% → pb_pr = round(PB/ROE², 2)。"""
        pb_pr = compute_pb_pr(0.69, 10.7)
        # compute_pb_pr 内部 round(...,2)：0.69/(0.107²)/100 = 0.6027 → 0.60
        assert pb_pr == pytest.approx(round(0.69 / 0.107**2 / 100, 2))

    def test_valuation_zones_snapshot(self):
        """估值四档边界：0.5/0.7/1.0。"""
        assert classify_valuation(0.5) == "低估"
        assert classify_valuation(0.7) == "合理偏低"
        assert classify_valuation(1.0) == "合理"
        assert classify_valuation(1.5) == "高估"


# ---------------------------------------------------------------------------
# 4. 可持续性快照（长江电力画像）
# ---------------------------------------------------------------------------

class TestSustainabilitySnapshot:
    def _healthy_financial(self) -> AnnualFinancial:
        return AnnualFinancial(
            year=2025, net_profit=345e8, net_profit_yoy=7.0,
            operating_cf=605e8, investing_cf=-312e8,
            total_assets=5620e8, total_liabilities=2918e8,
            debt_ratio=52.0, interest_debt_ratio=51.5,
            interest_coverage=6.37, roe=16.0,
            capital_adequacy_ratio=None, net_interest_margin=None,
            npl_ratio=None, provision_coverage=None,
        )

    def _healthy_history(self) -> DividendHistory:
        return DividendHistory(
            consecutive_years=15, ever_cut=False,
            latest_year_amount=214e8, history_mean_amount=200e8,
        )

    def test_changjiang_power_sustainable(self):
        """长江电力画像（高ROE/强现金流/长分红史/防御行业）→ 可持续。"""
        result = assess_sustainability(
            dividend_yield_before_tax=4.5,
            dividend_total=214e8,
            latest=self._healthy_financial(),
            history=self._healthy_history(),
            industry="公用事业-电力-水电",
        )
        assert result.triggered is True
        assert result.verdict == "可持续"
        assert result.score is not None and result.score >= 1.5
        # 0-2 映射 0-100：score≥1.5 → score_100≥75（#20）
        assert result.score_100 is not None
        assert result.score_100 >= 75

    def test_score_100_mapping(self):
        """score_100 = round(score×50, 1)：阈值 1.5/1.0 → 75/50（#20）。"""
        from src.sustainability_calculator import _score_to_100
        assert _score_to_100(1.5) == 75.0
        assert _score_to_100(1.0) == 50.0
        assert _score_to_100(0.5) == 25.0


# ---------------------------------------------------------------------------
# 5. 东财字段映射快照（字段名变更立即炸）
# ---------------------------------------------------------------------------

class TestEastmoneyFieldMappingSnapshot:
    def test_mainfinadata_field_mapping(self):
        """RPT_F10_FINANCE_MAINFINADATA 字段 → AnnualFinancial 映射（东财改字段名立即炸）。"""
        rows = [{
            "REPORT_DATE": "2025-12-31",
            "PARENTNETPROFIT": 345e8,          # 净利润
            "PARENTNETPROFITTZ": 7.0,          # 净利润同比
            "NETCASH_OPERATE_PK": 605e8,       # 经营现金流
            "NETCASH_INVEST_PK": -312e8,       # 投资现金流
            "TOTAL_ASSETS_PK": 5620e8,         # 总资产
            "LIABILITY": 2918e8,               # 总负债
            "INTEREST_DEBT_RATIO": 51.5,       # 有息负债率
            "INTEREST_COVERAGE_RATIO": 6.37,   # 利息保障
            "ROEJQ": 16.0,                     # 加权ROE
            "NEWCAPITALADER": None,            # 资本充足率（非银行无）
            "NET_INTEREST_MARGIN": None,
            "NONPERLOAN": None,
            "LOAN_PROVISION_RATIO": None,
        }]
        financials = parse_financial_rows(rows)
        assert len(financials) == 1
        fin = financials[0]
        assert fin.year == 2025
        assert fin.net_profit == pytest.approx(345e8)
        assert fin.net_profit_yoy == pytest.approx(7.0)
        assert fin.operating_cf == pytest.approx(605e8)
        assert fin.investing_cf == pytest.approx(-312e8)
        assert fin.total_assets == pytest.approx(5620e8)
        assert fin.total_liabilities == pytest.approx(2918e8)
        assert fin.roe == pytest.approx(16.0)

    def test_sharebonus_det_field_mapping(self):
        """RPT_SHAREBONUS_DET 字段 → DividendRecord 映射。"""
        rows = [{
            "REPORT_DATE": "2025-12-31",
            "PRETAX_BONUS_RMB": 7.33,
            "ASSIGN_PROGRESS": "实施",
            "EX_DIVIDEND_DATE": "2026-07-15",
        }]
        records, latest_year = parse_dividend_rows(rows)
        assert len(records) == 1
        assert records[0].report_time == "2025年报"
        assert records[0].dividend_per_10 == pytest.approx(7.33)
        assert latest_year == "2025"

    def test_sharebonus_det_filters_preset(self):
        """预披露方案被过滤（T5：仅保留已实施）。"""
        rows = [
            {"REPORT_DATE": "2025-12-31", "PRETAX_BONUS_RMB": 7.33,
             "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2026-07-15"},
            {"REPORT_DATE": "2025-12-31", "PRETAX_BONUS_RMB": 5.0,
             "ASSIGN_PROGRESS": "预披露", "EX_DIVIDEND_DATE": ""},
        ]
        records, _ = parse_dividend_rows(rows)
        assert len(records) == 1  # 只有已实施的
        assert records[0].dividend_per_10 == pytest.approx(7.33)
