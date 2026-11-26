"""选股器前瞻股息率 enrichment 测试（#157）。

接缝：make_forward_evaluator（编排层注入的评估器）——
输入 FunnelCandidate + 缓存快照，输出 ForwardYieldResult；不触网（预测取数可注入）。
分红/财务行一律来自可持续性快照，缺快照 → missing_inputs（不按需补拉）。
"""
import json

import pytest

from src.screening import FunnelCandidate
from src.screener_cache import (
    DividendSnapshot,
    FinanceSnapshot,
    QuoteSnapshot,
    ScreenerCache,
    SustainabilitySnapshot,
)
from src.screener_forward import make_forward_evaluator

pytestmark = pytest.mark.unit

SHARES = 1.0e9


def _div_rows():
    return [
        {"REPORT_DATE": "2025-12-31", "PRETAX_BONUS_RMB": 300.0, "ASSIGN_PROGRESS": "实施",
         "EX_DIVIDEND_DATE": "2026-06-20", "TOTAL_SHARES": SHARES},
        {"REPORT_DATE": "2024-12-31", "PRETAX_BONUS_RMB": 285.0, "ASSIGN_PROGRESS": "实施",
         "EX_DIVIDEND_DATE": "2025-06-20", "TOTAL_SHARES": SHARES},
        {"REPORT_DATE": "2023-12-31", "PRETAX_BONUS_RMB": 270.0, "ASSIGN_PROGRESS": "实施",
         "EX_DIVIDEND_DATE": "2024-06-20", "TOTAL_SHARES": SHARES},
    ]


def _fin_rows():
    return [
        {"REPORT_DATE": f"{y}-12-31", "ROEJQ": 15.0, "PARENTNETPROFIT": np_}
        for y, np_ in ((2025, 40e9), (2024, 38e9), (2023, 36e9))
    ]


def _forecast():
    """东财 RPT_WEB_RESPREDICT 同构一行：2026E EPS 66，覆盖 20 家。"""
    return [{
        "YEAR1": 2025, "YEAR_MARK1": "A", "EPS1": 40.0,
        "YEAR2": 2026, "YEAR_MARK2": "E", "EPS2": 66.0,
        "RATING_ORG_NUM": 20,
    }]


def _candidate(code="600900", price=1000.0):
    return FunnelCandidate(
        code=code,
        quote=QuoteSnapshot(code=code, name="长江电力", price=price, total_shares=SHARES,
                            market_cap=price * SHARES if price else None, source="腾讯"),
        dividend=DividendSnapshot(code=code, real_yield=3.0, ttm_yield=3.0,
                                  real_yield_year="2025", ttm_period="p",
                                  total_dividend=30e9, ttm_dividend=30e9,
                                  dividend_source="东财"),
        finance=FinanceSnapshot(code=code, roe_latest=15.0, roe_period="2025年报",
                                net_profit_annual=40e9, payout_ratio=0.75,
                                is_cyclical=False, finance_source="东财"),
    )


def _seed_snapshot(cache, code="600900"):
    cache.upsert_sustainability(SustainabilitySnapshot(
        code=code,
        financial_rows=json.dumps(_fin_rows()),
        dividend_rows=json.dumps(_div_rows()),
        industry="电力", source="东财"))


@pytest.fixture
def cache(tmp_path):
    return ScreenerCache(tmp_path / "s.db")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """兜底：任何按需补拉都视为缺陷（快照缺失应走 missing_inputs）。"""
    import src.forward_yield as fy

    def boom(*a, **kw):
        raise AssertionError("前瞻 enrichment 不得按需补拉分红/财务")

    monkeypatch.setattr(fy, "fetch_dividend_rows", boom)
    monkeypatch.setattr(fy, "fetch_financial_rows", boom)


class TestForwardEvaluator:
    def test_ok_uses_cached_rows_and_injected_forecast(self, cache):
        _seed_snapshot(cache)
        calls = []

        def fake_forecast(code):
            calls.append(code)
            return _forecast()

        ev = make_forward_evaluator(cache, forecast_fetcher=fake_forecast)
        r = ev(_candidate())

        assert r.forward_status == "ok"
        assert r.forward_year == 2026
        assert r.forward_yield_before_tax == pytest.approx(4.95)
        assert calls == ["600900"]

    def test_no_coverage(self, cache):
        _seed_snapshot(cache)
        ev = make_forward_evaluator(cache, forecast_fetcher=lambda code: [])
        r = ev(_candidate())
        assert r.forward_status == "no_coverage"
        assert r.forward_year == 2026
        assert r.forward_yield_before_tax is None

    def test_fetch_error(self, cache):
        _seed_snapshot(cache)
        ev = make_forward_evaluator(cache, forecast_fetcher=lambda code: None)
        r = ev(_candidate())
        assert r.forward_status == "fetch_error"
        assert r.forward_yield_before_tax is None

    def test_missing_snapshot_does_not_fetch(self, cache):
        """无可持续性快照 → missing_inputs，且不补拉、不请求预测。"""
        calls = []
        ev = make_forward_evaluator(
            cache, forecast_fetcher=lambda code: calls.append(code) or _forecast())
        r = ev(_candidate())
        assert r.forward_status == "missing_inputs"
        assert r.forward_year == 2026, "财年可由股息快照确定，仍保留"
        assert r.forward_yield_before_tax is None
        assert calls == []

    def test_missing_price(self, cache):
        _seed_snapshot(cache)
        ev = make_forward_evaluator(cache, forecast_fetcher=lambda code: _forecast())
        r = ev(_candidate(price=None))
        assert r.forward_status == "missing_inputs"
        assert r.forward_yield_before_tax is None
