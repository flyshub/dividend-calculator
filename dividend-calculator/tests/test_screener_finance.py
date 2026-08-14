"""选股器财务拉取测试（issue #122）。

覆盖 compute_finance_for_candidates（逐股拉财务写 finance_snapshot）：
mock 财务/行业 provider，不碰网络；验证字段写入、增量复用、payout_ratio 边界。

先例：tests/test_screener_pr.py（mock 限流）、tests/test_screener_cache.py。
"""
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from src.screener_cache import DividendSnapshot, FinanceSnapshot, ScreenerCache
from src.screener_finance import compute_finance_for_candidates


@pytest.fixture(autouse=True)
def _no_rate_limit():
    """mock 限流等待，测试不 sleep。"""
    with patch("src.screener_rate_limit.batch_wait"):
        yield


def _fin(roe=16.0, roe5y=15.0, np_annual=1e9, src="mootdx F10", period=2025):
    """构造与 pr._get_financial 同构的 7 元组返回值。"""
    return (roe, roe5y, None, np_annual, src, [], period)


def _div_snapshot(code="600900", total=2e9) -> DividendSnapshot:
    return DividendSnapshot(
        code=code, real_yield=5.0, ttm_yield=None, real_yield_year="2025",
        ttm_period=None, total_dividend=total, dividend_source="test")


class TestComputeFinanceForCandidates:
    def test_upserts_full_fields(self, tmp_path):
        cache = ScreenerCache(tmp_path / "s.db")
        cache.upsert_dividend(_div_snapshot())
        snaps = compute_finance_for_candidates(
            ["600900"], cache,
            financial_provider=lambda c: _fin(),
            industry_provider=lambda c: ("证券", "mootdx"))
        assert len(snaps) == 1
        fin = cache.get_finance("600900")
        assert fin.roe_latest == 16.0
        assert fin.roe_5y_median == 15.0
        assert fin.net_profit_annual == 1e9
        assert fin.roe_period == "2025"
        assert fin.payout_ratio == 2.0          # 2e9 / 1e9
        assert fin.is_cyclical is True          # 证券 → 周期股
        assert fin.finance_source == "mootdx F10"

    def test_fresh_skips_fetch(self, tmp_path):
        """updated_at 在 fresh_days 内 → 跳过重拉（provider 不被调用）。"""
        cache = ScreenerCache(tmp_path / "s.db")
        cache.upsert_finance(FinanceSnapshot(
            code="600900", roe_latest=16.0, roe_period="2025",
            net_profit_annual=1e9, payout_ratio=0.5,
            roe_5y_median=15.0, is_cyclical=False, finance_source="test"))
        called = []
        snaps = compute_finance_for_candidates(
            ["600900"], cache, fresh_days=7,
            financial_provider=lambda c: called.append(c) or _fin())
        assert called == []                     # 未重拉
        assert snaps[0].roe_latest == 16.0      # 复用缓存行

    def test_stale_refetches(self, tmp_path):
        """updated_at 超过 fresh_days → 重拉并更新。"""
        cache = ScreenerCache(tmp_path / "s.db")
        old = (date.today() - timedelta(days=30)).isoformat()
        cache.upsert_finance(FinanceSnapshot(
            code="600900", roe_latest=16.0, roe_period="2025",
            net_profit_annual=1e9, payout_ratio=0.5,
            roe_5y_median=15.0, is_cyclical=False, finance_source="test",
            updated_at=old))
        called = []
        compute_finance_for_candidates(
            ["600900"], cache, fresh_days=7,
            financial_provider=lambda c: called.append(c) or _fin(roe=18.0),
            industry_provider=lambda c: ("电力", "mootdx"))
        assert called == ["600900"]             # 已重拉
        assert cache.get_finance("600900").roe_latest == 18.0

    def test_fresh_but_roe_missing_refetches(self, tmp_path):
        """updated_at 新鲜但 roe_latest 缺失（如 backtest.db 导入行）→ 强制重拉（#82 教训）。"""
        cache = ScreenerCache(tmp_path / "s.db")
        cache.upsert_finance(FinanceSnapshot(
            code="600900", roe_latest=None, roe_period=None,
            net_profit_annual=None, payout_ratio=None,
            roe_5y_median=None, is_cyclical=None, finance_source="backtest"))
        called = []
        compute_finance_for_candidates(
            ["600900"], cache, fresh_days=7,
            financial_provider=lambda c: called.append(c) or _fin(),
            industry_provider=lambda c: ("电力", "mootdx"))
        assert called == ["600900"]             # 已重拉
        assert cache.get_finance("600900").roe_latest == 16.0

    def test_roe_missing_skips_write(self, tmp_path):
        """数据不可得（roe None）→ 跳过不写（数据铁律：不写假数据）。"""
        cache = ScreenerCache(tmp_path / "s.db")
        snaps = compute_finance_for_candidates(
            ["600900"], cache,
            financial_provider=lambda c: _fin(roe=None))
        assert snaps == []
        assert cache.get_finance("600900") is None

    def test_payout_none_without_dividend(self, tmp_path):
        """无 dividend_snapshot → payout_ratio None（漏斗回退基础 PR）。"""
        cache = ScreenerCache(tmp_path / "s.db")
        compute_finance_for_candidates(
            ["600900"], cache,
            financial_provider=lambda c: _fin(),
            industry_provider=lambda c: ("电力", "mootdx"))
        assert cache.get_finance("600900").payout_ratio is None

    def test_payout_none_when_profit_missing(self, tmp_path):
        """净利润缺失/≤0 → payout_ratio None。"""
        cache = ScreenerCache(tmp_path / "s.db")
        cache.upsert_dividend(_div_snapshot())
        compute_finance_for_candidates(
            ["600900"], cache,
            financial_provider=lambda c: _fin(np_annual=0.0),
            industry_provider=lambda c: ("电力", "mootdx"))
        assert cache.get_finance("600900").payout_ratio is None

    def test_rate_limited_per_fetch(self, tmp_path):
        """每个实际拉取前调用 batch_wait（限流 0.8s/只）。"""
        cache = ScreenerCache(tmp_path / "s.db")
        with patch("src.screener_rate_limit.batch_wait") as bw:
            compute_finance_for_candidates(
                ["600900", "600987"], cache,
                financial_provider=lambda c: _fin(),
                industry_provider=lambda c: ("电力", "mootdx"))
            assert bw.call_count == 2

    def test_empty_codes(self, tmp_path):
        cache = ScreenerCache(tmp_path / "s.db")
        assert compute_finance_for_candidates([], cache) == []
