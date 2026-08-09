"""选股器缓存快照测试（spec #67，工单 #69）。

覆盖 screener_cache 的 4 表 schema + 读写 + 增量过期判定，全部用临时库（tmp_path），
不碰网络：
- stock_list / quote_snapshot / dividend_snapshot / finance_snapshot 建表
- upsert 单股/批量、按 code 读
- 过期判定（行情每日、股息/财务低频）
- updated_at + *_source 标注

先例：tests/test_backtest_pr.py（SQLite 注入）、tests/test_manager_injection.py。
"""
import datetime

import pytest

from src.screener_cache import (
    ScreenerCache,
    QuoteSnapshot,
    DividendSnapshot,
    FinanceSnapshot,
    SustainabilitySnapshot,
)


@pytest.fixture
def cache(tmp_path):
    return ScreenerCache(tmp_path / "screener_test.db")


class TestSchema:
    def test_tables_created(self, cache):
        tables = cache.tables()
        assert "stock_list" in tables
        assert "quote_snapshot" in tables
        assert "dividend_snapshot" in tables
        assert "finance_snapshot" in tables

    def test_independent_db(self, tmp_path):
        # 缓存独立于 backtest.db（data/screener.db 路径可配）
        c = ScreenerCache(tmp_path / "custom.db")
        assert c.db_path.name == "custom.db"


class TestQuoteUpsert:
    def test_upsert_and_read(self, cache):
        q = QuoteSnapshot(code="600900", price=27.75, pe_ttm=27.84, pb=3.26,
                          total_shares=24468217716, market_cap=678890000000,
                          quote_time="2026-08-08", source="腾讯")
        cache.upsert_quote(q)
        got = cache.get_quote("600900")
        assert got is not None
        assert got.pe_ttm == pytest.approx(27.84)
        assert got.total_shares == 24468217716

    def test_upsert_overwrites(self, cache):
        cache.upsert_quote(QuoteSnapshot(code="600900", price=10, pe_ttm=1, pb=1,
                                         total_shares=1, market_cap=1,
                                         quote_time="2026-08-08", source="腾讯"))
        cache.upsert_quote(QuoteSnapshot(code="600900", price=20, pe_ttm=2, pb=2,
                                         total_shares=2, market_cap=2,
                                         quote_time="2026-08-09", source="腾讯"))
        got = cache.get_quote("600900")
        assert got.price == 20

    def test_batch_upsert(self, cache):
        quotes = [
            QuoteSnapshot(code="600900", price=10, pe_ttm=1, pb=1,
                          total_shares=1, market_cap=1, quote_time="t", source="s"),
            QuoteSnapshot(code="600987", price=10, pe_ttm=1, pb=1,
                          total_shares=1, market_cap=1, quote_time="t", source="s"),
        ]
        cache.upsert_quotes(quotes)
        assert cache.get_quote("600900") is not None
        assert cache.get_quote("600987") is not None

    def test_missing_code_returns_none(self, cache):
        assert cache.get_quote("999999") is None


class TestDividendSnapshot:
    def test_upsert_and_read(self, cache):
        d = DividendSnapshot(code="600900", real_yield=5.2, ttm_yield=5.5,
                             real_yield_year="2025", ttm_period="2025-07~2026-06",
                             dividend_source="mootdx")
        cache.upsert_dividend(d)
        got = cache.get_dividend("600900")
        assert got.real_yield == pytest.approx(5.2)
        assert got.real_yield_year == "2025"


class TestFinanceSnapshot:
    def test_upsert_and_read(self, cache):
        f = FinanceSnapshot(code="600900", roe_latest=16.4, roe_period="2025年报",
                            net_profit_annual=1e10, payout_ratio=0.58,
                            finance_source="东财")
        cache.upsert_finance(f)
        got = cache.get_finance("600900")
        assert got.roe_latest == pytest.approx(16.4)
        assert got.payout_ratio == pytest.approx(0.58)


class TestSustainabilitySnapshot:
    def test_upsert_and_read(self, cache):
        s = SustainabilitySnapshot(
            code="600900", financial_rows='[{"x":1}]', cashflow_rows='[]',
            dividend_rows='[]', industry="电力", price_change_1y=0.1,
            top10_holding=0.2, source="东财")
        cache.upsert_sustainability(s)
        got = cache.get_sustainability("600900")
        assert got is not None
        assert got.industry == "电力"
        assert got.price_change_1y == pytest.approx(0.1)

    def test_missing_code_returns_none(self, cache):
        assert cache.get_sustainability("999999") is None


class TestStaleness:
    def _ts(self, days_ago):
        return (datetime.date.today() - datetime.timedelta(days=days_ago)).isoformat()

    def test_quote_stale_after_1_day(self, cache):
        # 行情每日刷新：>1 天算 stale
        cache.upsert_quote(QuoteSnapshot(code="600900", price=10, pe_ttm=1, pb=1,
                                         total_shares=1, market_cap=1,
                                         quote_time="t", source="s",
                                         updated_at=self._ts(2)))
        assert cache.is_quote_stale("600900", max_age_days=1) is True

    def test_quote_fresh_within_day(self, cache):
        cache.upsert_quote(QuoteSnapshot(code="600900", price=10, pe_ttm=1, pb=1,
                                         total_shares=1, market_cap=1,
                                         quote_time="t", source="s",
                                         updated_at=self._ts(0)))
        assert cache.is_quote_stale("600900", max_age_days=1) is False

    def test_dividend_stale_after_long(self, cache):
        # 股息/财务低频（年报季）：30 天不 stale
        cache.upsert_dividend(DividendSnapshot(code="600900", real_yield=5.2, ttm_yield=5.5,
                                               real_yield_year="2025", ttm_period="p",
                                               dividend_source="mootdx",
                                               updated_at=self._ts(7)))
        assert cache.is_dividend_stale("600900", max_age_days=30) is False
        assert cache.is_dividend_stale("600900", max_age_days=3) is True

    def test_missing_code_stale(self, cache):
        assert cache.is_quote_stale("999999", max_age_days=1) is True


class TestSourceTracking:
    def test_source_stored(self, cache):
        cache.upsert_quote(QuoteSnapshot(code="600900", price=10, pe_ttm=1, pb=1,
                                         total_shares=1, market_cap=1,
                                         quote_time="t", source="腾讯"))
        got = cache.get_quote("600900")
        assert got.source == "腾讯"  # 数据铁律：来源标注
