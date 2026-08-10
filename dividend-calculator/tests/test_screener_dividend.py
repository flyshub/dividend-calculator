"""选股器真实股息率测试（spec #67，工单 #71）。

覆盖 dividend 快照转换 + 候选池批量计算 + 漏斗② 筛选，
全部注入 provider / 构造快照，不碰网络。batch_wait 限流已 mock，测试不 sleep。

先例：tests/test_dividend.py（股息计算）、tests/test_screener_cache.py。
"""
from unittest.mock import patch

import pytest

from src.dividend import DividendResult
from src.screener_cache import ScreenerCache, DividendSnapshot
from src.screener_dividend import (
    compute_dividends_for_candidates,
    screen_real_yield,
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

    # ---- market_caps 传入（实时重算） + total_dividend 缺失降级（#81） ----

    def test_market_caps_recompute_uses_total_dividend(self):
        """market_caps 传入且 total_dividend 有值 → 用分红总额/市值实时重算。"""
        snap = DividendSnapshot(code="600900", real_yield=1.0, ttm_yield=1.0,
                                total_dividend=1e10, ttm_dividend=1.1e10,
                                real_yield_year="2025", ttm_period="p", dividend_source="m")
        # 市值 1000亿 → 真实股息率 10% / 11%，都 >5% → 通过（尽管 real_yield 旧值仅 1%）
        assert screen_real_yield([snap], market_caps={"600900": 1e11}) == [snap]

    def test_market_caps_missing_total_dividend_falls_back(self):
        """total_dividend 缺失（NULL，旧 DB 迁移未回填）→ 回退到 real_yield 旧值，而非静默 0 只。"""
        snap = DividendSnapshot(code="600900", real_yield=6.0, ttm_yield=6.5,
                                total_dividend=None, ttm_dividend=None,
                                real_yield_year="2025", ttm_period="p", dividend_source="m")
        assert screen_real_yield([snap], market_caps={"600900": 1e11}) == [snap]

    def test_market_caps_missing_ttm_dividend_falls_back(self):
        """仅 ttm_dividend 缺失 → 回退到存储旧值（含 real 与 ttm 一起回退，保持同源）。"""
        snap = DividendSnapshot(code="600900", real_yield=6.0, ttm_yield=6.5,
                                total_dividend=1e10, ttm_dividend=None,
                                real_yield_year="2025", ttm_period="p", dividend_source="m")
        assert screen_real_yield([snap], market_caps={"600900": 1e11}) == [snap]

    def test_market_caps_fallback_still_enforces_threshold(self):
        """回退后仍按阈值筛选：real_yield 旧值低于阈值则不过。"""
        snap = DividendSnapshot(code="600900", real_yield=3.0, ttm_yield=6.5,
                                total_dividend=None, ttm_dividend=None,
                                real_yield_year="2025", ttm_period="p", dividend_source="m")
        assert screen_real_yield([snap], market_caps={"600900": 1e11}) == []


class TestComputeRealYield:
    def test_basic(self):
        from src.screener_dividend import compute_real_yield
        # 分红总额 100亿 / 市值 1000亿 = 10%
        assert compute_real_yield(1e10, 1e11) == pytest.approx(10.0)

    def test_market_cap_change_affects_yield(self):
        from src.screener_dividend import compute_real_yield
        # 同一分红，市值涨 → 股息率降（每日实时）
        assert compute_real_yield(1e10, 1e11) == pytest.approx(10.0)
        assert compute_real_yield(1e10, 2e11) == pytest.approx(5.0)

    def test_none_inputs(self):
        from src.screener_dividend import compute_real_yield
        assert compute_real_yield(None, 1e11) is None
        assert compute_real_yield(1e10, None) is None
        assert compute_real_yield(1e10, 0) is None


class TestScreenRealYieldRealtime:
    def _snap(self, total=1e10, ttm_total=1.05e10):
        return DividendSnapshot(code="600900", real_yield=10.0, ttm_yield=10.5,
                                real_yield_year="2025", ttm_period="p",
                                total_dividend=total, ttm_dividend=ttm_total,
                                dividend_source="m")

    def test_market_caps_recompute_overrides_stored(self):
        # 提供 market_caps → 实时重算（市值 2e11 → 股息率 5%）
        from src.screener_dividend import screen_real_yield
        pool = screen_real_yield([self._snap()], market_caps={"600900": 2e11})
        # 重算后 5% 不达 5.5 阈值（原存储 10%），应被筛掉
        assert pool == []

    def test_market_caps_lower_keeps(self):
        from src.screener_dividend import screen_real_yield
        # 市值 1e11 → 股息率 10%，达阈值
        pool = screen_real_yield([self._snap()], market_caps={"600900": 1e11}, min_real=5.0, min_ttm=5.0)
        assert len(pool) == 1

    def test_no_market_caps_falls_back_stored(self):
        from src.screener_dividend import screen_real_yield
        # 无 market_caps → 用存储 real_yield（10%）
        pool = screen_real_yield([self._snap()], min_real=5.0, min_ttm=5.0)
        assert len(pool) == 1
