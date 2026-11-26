"""选股器前瞻股息率 enrichment 测试（#157）。

接缝：make_forward_evaluator（编排层注入的评估器）——
输入 FunnelCandidate + 缓存快照，输出 ForwardYieldResult；不触网（预测取数可注入）。
分红/财务行一律来自可持续性快照，缺快照 → missing_inputs（不按需补拉）。
"""
import json
from types import SimpleNamespace

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


def _boom(*a, **kw):
    raise AssertionError("快照命中时不得按需补拉分红/财务")


def _make_eval(cache, **kw):
    """默认断言「不补拉」；需要补拉的用例显式传入 fetcher。"""
    kw.setdefault("dividend_fetcher", _boom)
    kw.setdefault("financial_fetcher", _boom)
    return make_forward_evaluator(cache, **kw)


class TestForwardEvaluator:
    def test_ok_uses_cached_rows_and_injected_forecast(self, cache):
        _seed_snapshot(cache)
        calls = []

        def fake_forecast(code):
            calls.append(code)
            return _forecast()

        ev = _make_eval(cache, forecast_fetcher=fake_forecast)
        r = ev(_candidate())

        assert r.forward_status == "ok"
        assert r.forward_year == 2026
        assert r.forward_yield_before_tax == pytest.approx(4.95)
        assert calls == ["600900"]

    def test_no_coverage(self, cache):
        _seed_snapshot(cache)
        ev = _make_eval(cache, forecast_fetcher=lambda code: [])
        r = ev(_candidate())
        assert r.forward_status == "no_coverage"
        assert r.forward_year == 2026
        assert r.forward_yield_before_tax is None

    def test_fetch_error(self, cache):
        _seed_snapshot(cache)
        ev = _make_eval(cache, forecast_fetcher=lambda code: None)
        r = ev(_candidate())
        assert r.forward_status == "fetch_error"
        assert r.forward_yield_before_tax is None

    def test_snapshot_miss_fetches_on_demand(self, cache):
        """无可持续性快照（当日新进漏斗的高股息股）→ 按需补拉分红/财务，照常估算。

        回归 #160：600211 快照未预拉（库内 real_yield 4.48 < 5 未入选预拉名单），
        但当日行情新进漏斗、主页面能算 → 选股页不能因缺快照就判「缺失」。
        """
        div_calls, fin_calls = [], []

        def fake_div(code):
            div_calls.append(code)
            return _div_rows()

        def fake_fin(code):
            fin_calls.append(code)
            return _fin_rows()

        ev = make_forward_evaluator(
            cache, forecast_fetcher=lambda code: _forecast(),
            dividend_fetcher=fake_div, financial_fetcher=fake_fin)
        r = ev(_candidate())

        assert r.forward_status == "ok"
        assert r.forward_year == 2026
        assert r.forward_yield_before_tax == pytest.approx(4.95)
        assert div_calls == ["600900"] and fin_calls == ["600900"]

    def test_snapshot_miss_and_dividend_fetch_fails(self, cache):
        """按需补拉失败 → fetch_error（带财年），不发预测请求。"""
        forecast_calls = []
        ev = make_forward_evaluator(
            cache,
            forecast_fetcher=lambda code: forecast_calls.append(code) or _forecast(),
            dividend_fetcher=lambda code: None,
            financial_fetcher=_boom)
        r = ev(_candidate())
        assert r.forward_status == "fetch_error"
        assert r.forward_year == 2026
        assert forecast_calls == []

    def test_missing_price(self, cache):
        _seed_snapshot(cache)
        ev = _make_eval(cache, forecast_fetcher=lambda code: _forecast())
        r = ev(_candidate(price=None))
        assert r.forward_status == "missing_inputs"
        assert r.forward_yield_before_tax is None


class TestBatchGuard:
    """#159 整批护栏：失败率 > 50%（且发起请求 ≥10 行）→ 判定数据源故障，整列留空。"""

    def _cand(self, code, status):
        c = _candidate(code)
        c.forward = SimpleNamespace(
            forward_status=status, forward_year=2026,
            forward_yield_before_tax=5.0 if status == "ok" else None)
        return c

    def _cands(self, statuses):
        return [self._cand(f"{600000 + i}", s) for i, s in enumerate(statuses)]

    def test_trips_above_half_failure(self):
        from src.screener_forward import apply_forward_guard
        cands = self._cands(["fetch_error"] * 7 + ["ok"] * 5)
        outcome = apply_forward_guard(cands)
        assert outcome == {"attempted": 12, "failed": 7, "tripped": True}
        assert all(c.forward is None for c in cands), "整列留空（含财年）"

    def test_exactly_half_does_not_trip(self):
        from src.screener_forward import apply_forward_guard
        cands = self._cands(["fetch_error"] * 6 + ["ok"] * 6)
        outcome = apply_forward_guard(cands)
        assert outcome["tripped"] is False
        assert all(c.forward is not None for c in cands)

    def test_small_batch_never_trips(self):
        """发起请求 <10 行：样本不足，逐行标注即可，不整列清空。"""
        from src.screener_forward import apply_forward_guard
        cands = self._cands(["fetch_error"] * 5)
        assert apply_forward_guard(cands)["tripped"] is False
        assert all(c.forward is not None for c in cands)

    def test_market_facts_not_counted_as_failures(self):
        from src.screener_forward import apply_forward_guard
        cands = self._cands(["no_coverage"] * 7 + ["ok"] * 5)
        outcome = apply_forward_guard(cands)
        assert outcome == {"attempted": 12, "failed": 0, "tripped": False}

    def test_missing_inputs_not_counted_as_attempted(self):
        """快照缺失不发起请求，不计入分母（否则整批缺快照会被误判为数据源故障）。"""
        from src.screener_forward import apply_forward_guard
        cands = self._cands(["missing_inputs"] * 7 + ["ok"] * 5)
        outcome = apply_forward_guard(cands)
        assert outcome == {"attempted": 5, "failed": 0, "tripped": False}

    def test_stale_counted_as_attempted(self):
        """stale 的判定在一致预期取数之后，请求已发出 → 计入分母。

        回归：若把 stale 排除，6 失败 / 6 stale 会被算成 100% 失败而误清整列。
        """
        from src.screener_forward import apply_forward_guard
        cands = self._cands(["fetch_error"] * 6 + ["stale"] * 6)
        outcome = apply_forward_guard(cands)
        assert outcome == {"attempted": 12, "failed": 6, "tripped": False}
