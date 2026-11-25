"""#28 可持续性评估阈值可配置（Python 侧）。

覆盖：阈值透传（纯评估器 / 编排层）、低于阈值不取数、轻量结果文案含阈值数字、
CLI 参数解析。全部无网络（数据行注入空列表）。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

import src.analysis as analysis_module
from calc_pr import parse_args
from src.sustainability import assess_for_stock, assess_with_auto_fetch
from src.sustainability_calculator import (
    THRESHOLD_YIELD,
    SustainabilityResult,
    assess_sustainability,
)


def _assess(yield_pct=3.5, **kwargs):
    return assess_sustainability(
        dividend_yield_before_tax=yield_pct,
        dividend_total=1e8,
        latest=None,
        history=None,
        industry="公用事业",
        current_year=2025,
        **kwargs,
    )


def _for_stock(yield_pct, **kwargs):
    # 行数据显式注入空列表 → 不触发任何 HTTP 取数
    return assess_for_stock(
        stock_code="600000",
        total_shares=1e9,
        dividend_total=1e8,
        dividend_yield_before_tax=yield_pct,
        latest_dividend_year="2024",
        industry="公用事业",
        dividend_records=[],
        financial_rows=[],
        cashflow_rows=[],
        **kwargs,
    )


class TestDefaultUnchanged:
    def test_default_threshold_is_4(self):
        assert THRESHOLD_YIELD == 4.0

    def test_below_default_threshold_not_triggered_with_numbers(self):
        r = _assess(yield_pct=3.5)
        assert r.triggered is False
        assert r.verdict == "未评估"
        assert "3.50%" in r.notes[0]
        assert "4.0%" in r.notes[0]

    def test_exactly_at_threshold_not_triggered(self):
        """边界与改动前一致：`<= 阈值` 不评估。"""
        assert _assess(yield_pct=4.0).triggered is False
        assert _assess(yield_pct=4.01).triggered is True


class TestThresholdPassthrough:
    def test_assess_sustainability_accepts_threshold(self):
        assert _assess(yield_pct=3.5, threshold=3.0).triggered is True
        assert _assess(yield_pct=3.5, threshold=3.6).triggered is False

    def test_assess_for_stock_accepts_threshold(self):
        assert _for_stock(3.5).triggered is False
        assert _for_stock(3.5, threshold=3.0).triggered is True

    def test_assess_with_auto_fetch_accepts_threshold(self):
        kwargs = dict(
            stock_code="600000",
            total_shares=1e9,
            dividend_total=1e8,
            dividend_yield_before_tax=3.5,
            latest_dividend_year="2024",
            industry="公用事业",
            dividend_rows=[],
            financial_rows=[],
            cashflow_rows=[],
            price_change_1y=0.0,
            top10_holding=0.0,
        )
        assert assess_with_auto_fetch(**kwargs).triggered is False
        assert assess_with_auto_fetch(**kwargs, threshold=3.0).triggered is True


@pytest.fixture
def analysis_env(monkeypatch):
    """把 run_stock_analysis 的外部依赖全部打桩，返回 assess/goodwill 调用记录。"""
    from src.datasource.base import StockInfo

    info = StockInfo(stock_code="600000", current_price=10.0, total_shares=1e9)
    monkeypatch.setattr(analysis_module, "get_stock_info", lambda x: info)
    # 3.5e8 / (10 × 1e9) = 3.5%
    monkeypatch.setattr(analysis_module, "get_latest_full_year_dividend",
                        lambda code, info: (3.5e8, "2024", None, None, None))
    monkeypatch.setattr(analysis_module, "calculate_pr",
                        lambda **kw: SimpleNamespace(industry="公用事业", is_cyclical=False))
    monkeypatch.setattr(analysis_module, "estimate_forward_yield", lambda **kw: None)
    monkeypatch.setattr(analysis_module, "estimate_cash_return", lambda **kw: None)

    calls = {"assess": [], "goodwill": []}
    monkeypatch.setattr(analysis_module, "fetch_goodwill",
                        lambda code: calls["goodwill"].append(code))

    def fake_assess(**kw):
        calls["assess"].append(kw)
        return SustainabilityResult(triggered=True, verdict="可持续", score=1.6)

    monkeypatch.setattr(analysis_module, "assess_with_auto_fetch", fake_assess)
    return calls


class TestRunStockAnalysis:
    def test_below_threshold_returns_lightweight_result_without_fetch(self, analysis_env):
        res = analysis_module.run_stock_analysis("600000")
        assert analysis_env["assess"] == []           # 未调用 assess_with_auto_fetch
        assert analysis_env["goodwill"] == []         # 也未发起商誉二次取数
        sus = res.sustainability
        assert sus is not None and sus.triggered is False
        assert sus.verdict == "未评估"
        assert "3.50%" in sus.notes[0] and "4.0%" in sus.notes[0]

    def test_threshold_passed_through(self, analysis_env):
        res = analysis_module.run_stock_analysis("600000", sus_threshold=3.0)
        assert res.sustainability.triggered is True
        assert analysis_env["assess"] and analysis_env["assess"][0]["threshold"] == 3.0

    def test_default_threshold_passed_through(self, analysis_env, monkeypatch):
        # 5% 股息率才会在默认 4.0 阈值下触发评估
        monkeypatch.setattr(analysis_module, "get_latest_full_year_dividend",
                            lambda code, info: (5e8, "2024", None, None, None))
        analysis_module.run_stock_analysis("600000", sus_threshold=None)
        assert analysis_env["assess"]
        assert analysis_env["assess"][0]["threshold"] == THRESHOLD_YIELD


class TestCliArgs:
    def test_default_threshold(self):
        args = parse_args(["600900"])
        assert args.stock == "600900"
        assert args.threshold == THRESHOLD_YIELD

    def test_threshold_override(self):
        args = parse_args(["603288", "--threshold", "2.5"])
        assert args.stock == "603288"
        assert args.threshold == 2.5
