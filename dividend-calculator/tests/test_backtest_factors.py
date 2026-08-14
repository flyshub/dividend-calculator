"""T3 因子计算层（src/backtest_factors.py）纯函数测试。

覆盖（离线注入快照，无网络）：
- 口径一致性：与现网 src 逐字段一致 —— dividend_records.summarize_dividend_rows（完整财年/TTM）、
  pr_calculator.compute_basic_pr / classify_industry（市赚率）、
  sustainability_calculator.assess_sustainability（可持续性）
- 无未来函数：分红公告日边界 / 财报报告期边界，asof 前后因子值必须不同
- 银行 vs 非银行：银行走金融专项分支（低 ROE 不判弱）、非银行 ROE<10% 计 0 分
- 边界：无分红 / 无 PE / 无 ROE / 缺价格 / 缺财务快照

先例：tests/test_pr_calculator.py、tests/test_sustainability_calculator.py（纯函数 + 边界值）。
"""
import sys
from datetime import date
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.backtest_factors import (
    PRFactor,
    real_dividend_yield,
    ttm_dividend_yield,
    pr,
    sustainability,
)
from src.datasource.base import DividendRecord
from src.dividend_records import summarize_dividend_rows
from src.pr_calculator import classify_industry, compute_basic_pr
from src.sustainability_calculator import (
    AnnualFinancial,
    DividendHistory,
    assess_sustainability,
)
from src.utils import compute_ttm_dividend

SHARES = 10e8          # 10 亿股
PRICE = 10.0           # 股价 10 元 → 总市值 100 亿元


def _rec(announce, report, ex=None, dps=0.1):
    """lookup 分红记录 dict（每股现金分红，元）。"""
    return {
        "announce_date": announce,
        "report_date": report,
        "ex_dividend_date": ex if ex is not None else announce,
        "cash_div_per_share": dps,
    }


def _to_af(fin):
    """财务快照 dict → AnnualFinancial（与 backtest_factors 内部转换同构）。"""
    return AnnualFinancial(**{f: fin.get(f) for f in AnnualFinancial.__dataclass_fields__})


def _lookup(records, *, pe=10.0, roe=15.0, shares=SHARES, price=PRICE,
            finance=None, industry="", price_change_1y=0.0,
            top10_holding=0.3, asof_filter=True):
    """构造可注入 lookup。asof_filter=False 时 dividends 不过滤公告日
    （用于验证因子内部的无未来函数过滤）。"""
    def dividends(code, T):
        recs = list(records)
        if asof_filter:
            t = T.isoformat()[:10]
            recs = [r for r in recs if str(r["announce_date"])[:10] <= t]
        return recs

    def roe_lookup(code, T):
        return roe(code, T) if callable(roe) else roe

    def finance_lookup(code, T):
        return finance(code, T) if callable(finance) else finance

    return {
        "dividends": dividends,
        "pe_ttm": lambda code, T: pe,
        "total_shares": lambda code, T: shares,
        "price": lambda code, T: price,
        "roe_latest": roe_lookup,
        "finance": finance_lookup,
        "price_change_1y": lambda code, T: price_change_1y,
        "top10_holding": lambda code, T: top10_holding,
        "industry": lambda code, T: industry,
    }


def _bank_finance(**overrides):
    """银行财务快照（资本充足率等专项齐全，低 ROE）。"""
    fin = dict(
        year=2024, net_profit=120e8, net_profit_yoy=3.0,
        operating_cf=None, investing_cf=None,
        total_assets=30000e8, total_liabilities=27500e8,
        interest_debt_ratio=None, interest_coverage=None,
        roe=8.0,  # 低 ROE——银行不走通用六维，不应被判弱
        capital_adequacy_ratio=13.5, net_interest_margin=2.0,
        npl_ratio=0.8, provision_coverage=2.6,
    )
    fin.update(overrides)
    return fin


def _general_finance(**overrides):
    """通用财务快照（长江电力画像：高 ROE/稳现金流/低负债）。"""
    fin = dict(
        year=2024, net_profit=345e8, net_profit_yoy=7.0,
        operating_cf=605e8, investing_cf=-312e8,
        total_assets=5620e8, total_liabilities=2918e8,
        interest_debt_ratio=51.5, interest_coverage=6.37,
        roe=16.0,
        capital_adequacy_ratio=None, net_interest_margin=None,
        npl_ratio=None, provision_coverage=None,
    )
    fin.update(overrides)
    return fin


# ---------------------------------------------------------------------------
# 口径一致性：real_dividend_yield vs dividend_records.summarize_dividend_rows
# ---------------------------------------------------------------------------

class TestRealDividendYieldParity:
    RECORDS = [
        # 2023 财年（中期分配 + 年报）
        _rec("2023-06-20", "2023-06-30", dps=0.05),
        _rec("2024-04-28", "2023-12-31", dps=0.24),
        # 2024 财年（中期分配 + 年报）
        _rec("2024-09-20", "2024-06-30", dps=0.06),
        _rec("2025-04-28", "2024-12-31", dps=0.28),
    ]

    def test_matches_summarize_dividend_rows_field_by_field(self):
        """同一输入快照：backtest 因子与现网 summarize_dividend_rows 结果逐字段一致。"""
        T = date(2025, 5, 15)
        lookup = _lookup(self.RECORDS)
        fy_yield = real_dividend_yield("600000", T, lookup)

        # 现网口径：报告期 12-31 决定完整财年；2024 财年 = 中期分配 0.06 + 年报 0.28
        rows = [
            {"REPORT_DATE": r["report_date"],
             "PRETAX_BONUS_RMB": r["cash_div_per_share"] * 10,
             "ASSIGN_PROGRESS": "实施",
             "EX_DIVIDEND_DATE": r["ex_dividend_date"],
             "PLAN_NOTICE_DATE": r["announce_date"]}
            for r in self.RECORDS
        ]
        summary = summarize_dividend_rows(rows, as_of_date=T)
        market_cap = PRICE * SHARES
        expected_yield = summary.fiscal_total_per_10 * SHARES / 10 / market_cap * 100

        assert fy_yield == pytest.approx(expected_yield)
        assert fy_yield == pytest.approx(3.4)  # (0.06+0.28)*10e8 / 100e8 = 3.4%
        assert summary.latest_year == "2024"

    def test_interim_only_year_not_full_fiscal_year(self):
        """#37 M4：仅有中期分配（无 12-31 年报）的财年不构成完整财年，
        最新完整财年应回退到上一个有年报的年份。"""
        records = [
            _rec("2024-09-20", "2024-06-30", dps=0.06),   # 2024 只有中期分配
            _rec("2024-04-28", "2023-12-31", dps=0.24),   # 2023 有年报
        ]
        T = date(2024, 12, 31)
        fy = real_dividend_yield("600000", T, _lookup(records))
        # 完整财年 = 2023：0.24*10e8 / 100e8 = 2.4%
        assert fy == pytest.approx(2.4)

    def test_no_dividend_returns_zero(self):
        T = date(2025, 5, 15)
        assert real_dividend_yield("600000", T, _lookup([])) == 0.0

    def test_missing_price_or_shares_returns_none(self):
        T = date(2025, 5, 15)
        assert real_dividend_yield("600000", T, _lookup(self.RECORDS, price=None)) is None
        assert real_dividend_yield("600000", T, _lookup(self.RECORDS, shares=None)) is None


# ---------------------------------------------------------------------------
# 口径一致性：ttm_dividend_yield vs utils.compute_ttm_dividend
# ---------------------------------------------------------------------------

class TestTtmDividendYieldParity:
    def _records(self):
        return [
            # ex 日 = T-400（窗口外）
            _rec("2023-04-10", "2022-12-31", ex="2023-05-20", dps=0.10),
            # ex 日 = T-365 边界（开区间起点，严格 > cutoff 才计入 → 排除）
            _rec("2023-06-01", "2022-12-31", ex="2023-06-30", dps=0.12),
            # 窗口内
            _rec("2023-08-01", "2022-12-31", ex="2023-09-15", dps=0.14),
            _rec("2024-05-01", "2023-12-31", ex="2024-06-01", dps=0.16),
            # ex 日 = T（闭区间终点 → 计入）
            _rec("2024-06-20", "2023-12-31", ex="2024-06-30", dps=0.18),
        ]

    def test_matches_compute_ttm_dividend(self):
        T = date(2024, 6, 30)
        records = self._records()
        fy_yield = ttm_dividend_yield("600000", T, _lookup(records))

        # 现网：compute_ttm_dividend（按除权日窗口 (T-365, T]）
        df_recs = [
            DividendRecord(ex_dividend_date=r["ex_dividend_date"],
                           dividend_per_10=r["cash_div_per_share"] * 10,
                           report_time=str(r["report_date"]))
            for r in records
        ]
        ttm_total, _, _, count = compute_ttm_dividend(df_recs, SHARES, as_of_date=T)
        assert count == 3  # 0.14/0.16/0.18 计入；T-400 与 T-365 边界排除
        assert ttm_total is not None
        expected = ttm_total / (PRICE * SHARES) * 100

        assert fy_yield == pytest.approx(expected)
        # (0.14+0.16+0.18)*10e8 / 100e8 = 4.8%
        assert fy_yield == pytest.approx(4.8)

    def test_no_payout_in_window_returns_none(self):
        T = date(2024, 6, 30)
        records = [self._records()[0]]  # 仅 T-400 的一笔
        assert ttm_dividend_yield("600000", T, _lookup(records)) is None

    def test_missing_price_returns_none(self):
        T = date(2024, 6, 30)
        assert ttm_dividend_yield("600000", T, _lookup(self._records(), price=None)) is None


# ---------------------------------------------------------------------------
# 口径一致性：pr vs pr_calculator.compute_basic_pr / classify_industry
# ---------------------------------------------------------------------------

class TestPrParity:
    def test_matches_compute_basic_pr(self):
        T = date(2025, 5, 15)
        factor = pr("600000", T, _lookup([], pe=10.0, roe=15.9))
        assert factor.pr == compute_basic_pr(10.0, 15.9)
        assert factor.pr == pytest.approx(round(10.0 / 15.9, 2))
        assert factor.pe_ttm == 10.0
        assert factor.roe_latest == 15.9

    def test_cyclical_warning_preserved(self):
        T = date(2025, 5, 15)
        factor = pr("600000", T, _lookup([], industry="煤炭"))
        is_cyc, is_tech, is_growth, warning = classify_industry("煤炭")
        assert factor.is_cyclical == is_cyc
        assert factor.is_tech == is_tech
        assert factor.is_growth == is_growth
        assert factor.pr_warning == warning
        assert factor.pr_warning  # 周期股应有警示文案

    def test_no_industry_defaults_no_warning(self):
        T = date(2025, 5, 15)
        lookup = _lookup([])
        lookup.pop("industry")  # industry 为可选键
        factor = pr("600000", T, lookup)
        assert factor.pr_warning == ""

    def test_missing_pe_or_roe_returns_none(self):
        T = date(2025, 5, 15)
        assert pr("600000", T, _lookup([], pe=None)).pr is None
        assert pr("600000", T, _lookup([], roe=None)).pr is None
        assert pr("600000", T, _lookup([], roe=0.0)).pr is None
        assert pr("600000", T, _lookup([], roe=-3.0)).pr is None


# ---------------------------------------------------------------------------
# 口径一致性：sustainability vs assess_sustainability（同输入同输出）
# ---------------------------------------------------------------------------

class TestSustainabilityParity:
    RECORDS = [
        _rec("2021-04-20", "2020-12-31", dps=0.30),
        _rec("2022-04-20", "2021-12-31", dps=0.32),
        _rec("2023-04-20", "2022-12-31", dps=0.34),
        _rec("2024-04-20", "2023-12-31", dps=0.36),
        _rec("2025-04-28", "2024-12-31", dps=0.50),   # 连续 5 年，无削减
    ]

    def test_matches_assess_sustainability_field_by_field(self):
        """同一输入快照：因子输出与现网 assess_sustainability 逐字段一致。"""
        T = date(2025, 5, 15)
        lookup = _lookup(self.RECORDS, price=PRICE,
                         finance=_general_finance(), industry="公用事业-电力-水电",
                         price_change_1y=0.05, top10_holding=0.3)
        result = sustainability("600000", T, lookup)

        # 现网：手工拼装同一组输入直接调 assess_sustainability
        dividend_total = 0.50 * SHARES  # 最新完整财年 2024
        expected = assess_sustainability(
            dividend_yield_before_tax=dividend_total / (PRICE * SHARES) * 100,
            dividend_total=dividend_total,
            latest=_to_af(_general_finance()),
            history=DividendHistory(
                consecutive_years=5, ever_cut=False,
                latest_year_amount=0.50 * SHARES,
                history_mean_amount=sum([0.30, 0.32, 0.34, 0.36]) / 4 * SHARES,
                history_3y_mean=sum([0.32, 0.34, 0.36]) / 3 * SHARES,
            ),
            industry="公用事业-电力-水电",
            price_change_1y=0.05, top10_holding=0.3,
            current_year=2025,
        )

        for attr in ("triggered", "verdict", "score", "branch",
                     "fatal_flags", "warning_flags", "dimension_scores",
                     "latest_annual_year", "score_100"):
            assert getattr(result, attr) == getattr(expected, attr), attr
        assert result.triggered is True
        assert result.verdict == "可持续"

    def test_below_threshold_not_triggered(self):
        """股息率 ≤4% → 未评估（与现网 THRESHOLD_YIELD 一致）。"""
        records = [_rec("2025-04-28", "2024-12-31", dps=0.30)]  # 3% < 4%
        T = date(2025, 5, 15)
        result = sustainability("600000", T,
                                _lookup(records, finance=_general_finance(),
                                        industry="公用事业"))
        assert result.triggered is False
        assert result.verdict == "未评估"


# ---------------------------------------------------------------------------
# 无未来函数：公告日 / 报告期边界
# ---------------------------------------------------------------------------

class TestNoFutureFunction:
    def test_announce_date_boundary_changes_result(self):
        """分红公告日 T0：asof 在公告前 → 该分红不可见；公告后 → 可见。"""
        records = [
            _rec("2024-04-28", "2023-12-31", dps=0.30),  # 2023 年报
            _rec("2025-05-15", "2024-12-31", dps=0.50),  # 2024 年报，公告日 05-15
        ]
        # 注入不过滤公告日的原始记录，验证因子内部过滤（无未来函数防线）
        lookup = _lookup(records, asof_filter=False)

        before = real_dividend_yield("600000", date(2025, 5, 10), lookup)
        after = real_dividend_yield("600000", date(2025, 5, 20), lookup)
        assert before == pytest.approx(3.0)   # 完整财年 = 2023：0.30*10e8/100e8
        assert after == pytest.approx(5.0)    # 完整财年 = 2024：0.50*10e8/100e8
        assert before != after

    def test_report_period_boundary_changes_result(self):
        """财报报告期边界：asof 在新年报披露前用旧 ROE/财务，披露后用新。"""
        def roe_by_T(code, T):
            return 15.0 if T < date(2025, 5, 15) else 20.0

        def fin_by_T(code, T):
            return _general_finance(year=2023, roe=15.0, net_profit=300e8) \
                if T < date(2025, 5, 15) \
                else _general_finance(year=2024, roe=20.0, net_profit=400e8)

        records = [_rec("2025-04-28", "2024-12-31", dps=0.50)]
        lookup = _lookup(records, roe=roe_by_T, finance=fin_by_T,
                         industry="公用事业", price_change_1y=0.05)

        pr_before = pr("600000", date(2025, 5, 10), lookup)
        pr_after = pr("600000", date(2025, 5, 20), lookup)
        assert pr_before.pr == pytest.approx(round(10.0 / 15.0, 2))
        assert pr_after.pr == pytest.approx(round(10.0 / 20.0, 2))
        assert pr_before.pr != pr_after.pr

        sus_before = sustainability("600000", date(2025, 5, 10), lookup)
        sus_after = sustainability("600000", date(2025, 5, 20), lookup)
        assert sus_before.latest_annual_year == 2023
        assert sus_after.latest_annual_year == 2024
        assert sus_before.metrics["roe_latest"] != sus_after.metrics["roe_latest"]


# ---------------------------------------------------------------------------
# 银行 vs 非银行分支
# ---------------------------------------------------------------------------

class TestBankVsNonBank:
    RECORDS = [_rec("2025-04-28", "2024-12-31", dps=0.60)]  # 6% > 4% 触发

    def test_bank_uses_finance_branch_low_roe_not_penalized(self):
        """银行走金融专项：低 ROE（8%）不参与通用六维评分，不计 0 分。"""
        T = date(2025, 5, 15)
        result = sustainability("600000", T, _lookup(
            self.RECORDS, finance=_bank_finance(), industry="银行",
        ))
        assert result.branch == "finance"
        assert "profitability" not in result.dimension_scores
        assert "capital_adequacy" in result.dimension_scores
        # 专项全值 → 等权均值：四项全 2 分
        assert result.score == pytest.approx(2.0)

    def test_bank_low_car_is_fatal(self):
        """银行资本充足率 < 10.5% → 致命红旗（监管红线）。"""
        T = date(2025, 5, 15)
        result = sustainability("600000", T, _lookup(
            self.RECORDS, finance=_bank_finance(capital_adequacy_ratio=10.0),
            industry="银行",
        ))
        assert result.verdict == "不可持续"
        assert any("资本充足率" in f for f in result.fatal_flags)
        assert result.score == 0.0

    def test_nonbank_roe_below_10_scores_zero(self):
        """非银行：ROE < 10% → 盈利维度计 0 分。"""
        T = date(2025, 5, 15)
        result = sustainability("600000", T, _lookup(
            self.RECORDS,
            finance=_general_finance(roe=8.0, net_profit=200e8),
            industry="煤炭",
        ))
        assert result.branch == "general"
        assert result.dimension_scores["profitability"] == 0


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------

class TestEdges:
    def test_no_dividend_records(self):
        T = date(2025, 5, 15)
        lookup = _lookup([], finance=_general_finance(), industry="公用事业")
        assert real_dividend_yield("600000", T, lookup) == 0.0
        assert ttm_dividend_yield("600000", T, lookup) is None
        result = sustainability("600000", T, lookup)
        assert result.triggered is False
        assert result.verdict == "未评估"

    def test_no_finance_snapshot(self):
        """财务快照缺失 → 现网降级路径：致命红旗 + 不可持续。"""
        T = date(2025, 5, 15)
        result = sustainability("600000", T, _lookup(
            [_rec("2025-04-28", "2024-12-31", dps=0.60)],
            finance=None, industry="公用事业",
        ))
        assert result.verdict == "不可持续"
        assert any("缺少财务数据" in f for f in result.fatal_flags)

    def test_ttm_window_boundary_strict(self):
        """TTM 窗口 (T-365, T]：起点严格开区间、终点闭区间。"""
        T = date(2024, 6, 30)
        records = [
            _rec("2023-06-01", "2022-12-31", ex="2023-06-30", dps=0.10),  # == T-365 → 排除
            _rec("2024-06-20", "2023-12-31", ex="2024-06-30", dps=0.20),  # == T → 计入
        ]
        assert ttm_dividend_yield("600000", T, _lookup(records)) == pytest.approx(2.0)
