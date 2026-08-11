"""选股器真实股息率数据获取测试（spec #67，工单 #71）。

覆盖 dividend 快照转换 + 候选池批量计算（取数与缓存），
全部注入 provider / 构造快照，不碰网络。batch_wait 限流已 mock，测试不 sleep。

漏斗② 判定与降级回退测试已随 ADR-0001 迁移到 test_screening.py。
先例：tests/test_dividend.py（股息计算）、tests/test_screener_cache.py。
"""
from unittest.mock import patch

import pytest

from src.dividend import DividendResult
from src.screener_cache import ScreenerCache, DividendSnapshot
from src.screener_dividend import (
    compute_dividends_for_candidates,
    to_dividend_snapshot,
)


@pytest.fixture(autouse=True)
def _no_rate_limit():
    """mock 限流等待，测试不 sleep。"""
    with patch("src.screener_rate_limit.batch_wait"):
        yield


def _result(code="600900", real=6.0, ttm=6.5, year="2025", period="2025-07~2026-06"):
    return DividendResult(
        stock_code=code, stock_name="长江电力", current_price=27.75,
        total_shares=2.4e10, total_market_cap=6.7e11, total_dividend=4e10,
        dividend_yield_before_tax=real, dividend_yield_after_tax=real * 0.9,
        dividend_yield_after_tax_20=real * 0.8, latest_year=year,
        dividend_details=[], explanation="", dividend_source="mootdx",
        dividend_yield_ttm_before_tax=ttm, ttm_period=period, ttm_source="东财",
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

    def test_total_dividend_missing_force_refresh(self, tmp_path):
        """缓存有 total_dividend=None 的快照（旧 DB 迁移未回填）→ 即使未过期也强制重拉（#82）。

        否则 fill_screener_data --dividend 的增量复用会跳过，total_dividend 永远 NULL。
        """
        cache = ScreenerCache(tmp_path / "s.db")
        # 预置一个未过期但 total_dividend=None 的快照（模拟旧 DB）
        stale_snap = DividendSnapshot(
            code="600900", real_yield=6.0, ttm_yield=6.5,
            total_dividend=None, ttm_dividend=None,
            real_yield_year="2025", ttm_period="p", dividend_source="m")
        cache.upsert_dividend(stale_snap)

        calls = []

        def fake_calc(code):
            calls.append(code)
            return _result(code=code)  # total_dividend=4e10

        snaps = compute_dividends_for_candidates(
            ["600900"], cache, calc_provider=fake_calc)
        # 应重拉（provider 被调用），而非复用缓存
        assert calls == ["600900"]
        # 重拉后 total_dividend 有值
        assert cache.get_dividend("600900").total_dividend == pytest.approx(4e10)

    def test_total_dividend_present_reuses(self, tmp_path):
        """缓存 total_dividend 有值且未过期 → 增量复用，不重拉。"""
        cache = ScreenerCache(tmp_path / "s.db")
        fresh_snap = DividendSnapshot(
            code="600900", real_yield=6.0, ttm_yield=6.5,
            total_dividend=4e10, ttm_dividend=4.4e10,
            real_yield_year="2025", ttm_period="p", dividend_source="m")
        cache.upsert_dividend(fresh_snap)

        calls = []
        snaps = compute_dividends_for_candidates(
            ["600900"], cache, calc_provider=lambda c: calls.append(c) or _result(code=c))
        assert calls == [], "total_dividend 有值应复用缓存不重拉"
        assert len(snaps) == 1
        assert snaps[0].total_dividend == pytest.approx(4e10)
