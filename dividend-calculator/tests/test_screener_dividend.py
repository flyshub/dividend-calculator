"""选股器真实股息率测试（spec #67，工单 #71）。

覆盖 dividend 快照转换 + 候选池批量计算 + 漏斗② 筛选，
全部注入 provider / 构造快照，不碰真实网络。

先例：tests/test_dividend.py（股息计算）、tests/test_screener_cache.py。
"""
import pytest

from src.dividend import DividendResult
from src.screener_cache import ScreenerCache, DividendSnapshot
from src.screener_dividend import (
    compute_dividends_for_candidates,
    screen_real_yield,
    to_dividend_snapshot,
)


def _result(code="600900", real=6.0, ttm=6.5, year="2025", period="2025-07~2026-06"):
    return DividendResult(
        stock_code=code, stock_name="长江电力", current_price=27.75,
        total_shares=2.4e10, total_market_cap=6.7e11, total_dividend=4e10,
        dividend_yield_before_tax=real, dividend_yield_after_tax=real * 0.9,
        dividend_yield_after_tax_20=real * 0.8, latest_year=year,
        dividend_details=[], explanation="", dividend_source="mootdx",
        dividend_yield_ttm_before_tax=ttm, ttm_period=period, ttm_source="mootdx xdxr",
    )


class TestToSnapshot:
    def test_maps_fields(self):
        snap = to_dividend_snapshot(_result())
        assert snap.real_yield == pytest.approx(6.0)
        assert snap.ttm_yield == pytest.approx(6.5)
        assert snap.real_yield_year == "2025"
        assert snap.ttm_period == "2025-07~2026-06"

    def test_source_default(self):
        snap = to_dividend_snapshot(_result())
        assert snap.dividend_source == "mootdx"


class TestComputeDividends:
    def test_injects_provider(self, tmp_path):
        cache = ScreenerCache(tmp_path / "s.db")
        calls = []

        def fake_calc(code):
            calls.append(code)
            return _result(code=code)

        snaps = compute_dividends_for_candidates(
            ["600900", "600987"], cache, calc_provider=fake_calc)
        assert len(snaps) == 2
        assert calls == ["600900", "600987"]
        # 缓存已写
        assert cache.get_dividend("600900").real_yield == pytest.approx(6.0)

    def test_none_result_skipped(self, tmp_path):
        cache = ScreenerCache(tmp_path / "s.db")
        snaps = compute_dividends_for_candidates(
            ["600900"], cache, calc_provider=lambda c: None)
        assert snaps == []

    def test_empty_codes(self, tmp_path):
        cache = ScreenerCache(tmp_path / "s.db")
        assert compute_dividends_for_candidates([], cache) == []


class TestScreenRealYield:
    def _snap(self, real=6.0, ttm=6.5):
        return DividendSnapshot(code="600900", real_yield=real, ttm_yield=ttm,
                                real_yield_year="2025", ttm_period="p", dividend_source="m")

    def test_passes_both_above(self):
        assert screen_real_yield([self._snap(real=6.0, ttm=6.5)]) == [self._snap()]

    def test_rejects_real_below(self):
        assert screen_real_yield([self._snap(real=4.0, ttm=6.5)]) == []

    def test_rejects_ttm_below(self):
        assert screen_real_yield([self._snap(real=6.0, ttm=4.0)]) == []

    def test_boundary_exact(self):
        assert screen_real_yield([self._snap(real=5.0, ttm=6.5)]) == []  # 严格 >
        assert screen_real_yield([self._snap(real=6.0, ttm=5.0)]) == []  # 严格 >

    def test_missing_yield_rejected(self):
        snap = DividendSnapshot(code="600900", real_yield=None, ttm_yield=6.5,
                                real_yield_year="2025", ttm_period="p", dividend_source="m")
        assert screen_real_yield([snap]) == []

    def test_custom_threshold(self):
        assert len(screen_real_yield([self._snap(real=4.5, ttm=6.0)], min_real=4.0)) == 1

    def test_mixed_pool(self):
        pool = [self._snap(real=6.0, ttm=6.5), self._snap(real=3.0, ttm=4.0)]
        assert len(screen_real_yield(pool)) == 1
