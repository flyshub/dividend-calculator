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
    StockListItem,
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


class TestStockList:
    def test_get_stock_codes_ordered(self, cache):
        cache.upsert_stock_list([
            StockListItem(code="600987", name="航民股份", market="sh"),
            StockListItem(code="600900", name="长江电力", market="sh"),
        ])
        assert cache.get_stock_codes() == ["600900", "600987"]

    def test_get_stock_codes_empty(self, cache):
        assert cache.get_stock_codes() == []


class TestDividendCodes:
    def _seed(self, cache):
        rows = [
            ("600900", 6.0, 6.5),
            ("600987", 7.0, 7.5),
            ("600919", 4.0, 4.5),
            ("600887", None, 6.0),   # real_yield NULL
            ("600036", 0.0, 0.0),    # real_yield 0（不 >0）
        ]
        for code, real, ttm in rows:
            cache.upsert_dividend(DividendSnapshot(
                code=code, real_yield=real, ttm_yield=ttm,
                real_yield_year="2025", ttm_period="p", dividend_source="m"))

    def test_all_codes_ordered(self, cache):
        self._seed(cache)
        assert cache.get_dividend_codes() == ["600036", "600887", "600900", "600919", "600987"]

    def test_require_real_yield(self, cache):
        self._seed(cache)
        codes = cache.get_dividend_codes(require_real_yield=True)
        assert "600887" not in codes  # real_yield NULL 排除
        assert "600036" in codes      # real_yield 0 保留（仅 IS NOT NULL）

    def test_real_yield_min_strict(self, cache):
        self._seed(cache)
        codes = cache.get_dividend_codes(real_yield_min=5.0, ttm_yield_min=5.0)
        assert codes == ["600900", "600987"]  # 严格 >，600919 卡在 4.x，600036 为 0

    def test_real_yield_min_zero_excludes_null_and_zero(self, cache):
        self._seed(cache)
        codes = cache.get_dividend_codes(real_yield_min=0.0)
        assert codes == ["600900", "600919", "600987"]  # >0：排除 NULL 与 0


class TestStats:
    def test_counts_after_upserts(self, cache):
        cache.upsert_stock_list([StockListItem(code="600900", name="长江电力", market="sh")])
        cache.upsert_quote(QuoteSnapshot(code="600900", price=10, pe_ttm=8.0, pb=1.0,
                                         total_shares=1e9, market_cap=1e10,
                                         quote_time="", source="腾讯"))
        stats = cache.stats()
        assert stats["stock_list"] == 1
        assert stats["quote_snapshot"] == 1
        assert stats["dividend_snapshot"] == 0
        assert stats["finance_snapshot"] == 0
        assert stats["sustainability_snapshot"] == 0


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

    def test_upsert_and_read_roe_5y_median_cyclical(self, cache):
        # 周期股 PR 字段（评审 P1-2 / Phase 3）：roe_5y_median + is_cyclical 往返
        f = FinanceSnapshot(code="600900", roe_latest=16.4, roe_period="2025年报",
                            net_profit_annual=1e10, payout_ratio=0.58,
                            roe_5y_median=12.3, is_cyclical=True,
                            finance_source="东财")
        cache.upsert_finance(f)
        got = cache.get_finance("600900")
        assert got is not None
        assert got.roe_5y_median == pytest.approx(12.3)
        assert got.is_cyclical is True  # SQLite INTEGER → bool 还原

    def test_upsert_and_read_cyclical_false(self, cache):
        f = FinanceSnapshot(code="600900", roe_latest=16.4, roe_period="2025年报",
                            net_profit_annual=1e10, payout_ratio=0.58,
                            roe_5y_median=12.3, is_cyclical=False,
                            finance_source="东财")
        cache.upsert_finance(f)
        got = cache.get_finance("600900")
        assert got is not None
        assert got.is_cyclical is False

    def test_get_all_finance_roundtrip(self, cache):
        cache.upsert_finance(FinanceSnapshot(
            code="600900", roe_latest=16.4, roe_period="2025年报",
            net_profit_annual=1e10, payout_ratio=0.58,
            roe_5y_median=12.3, is_cyclical=True, finance_source="东财"))
        all_fin = cache.get_all_finance()
        assert all_fin["600900"].roe_5y_median == pytest.approx(12.3)
        assert all_fin["600900"].is_cyclical is True


class TestMigration:
    """旧库缺列 → 打开时自动 ALTER TABLE 补齐（评审 P1-2：146MB 存量 screener.db 兼容）。"""

    def _old_schema_db(self, tmp_path):
        """手工建旧版 finance_snapshot（无 roe_5y_median/is_cyclical 列）+ 旧 dividend 表。"""
        import sqlite3
        db = tmp_path / "old.db"
        conn = sqlite3.connect(db)
        conn.executescript("""
            CREATE TABLE finance_snapshot (
                code TEXT PRIMARY KEY, roe_latest REAL, roe_period TEXT,
                net_profit_annual REAL, payout_ratio REAL, finance_source TEXT, updated_at TEXT);
            CREATE TABLE dividend_snapshot (
                code TEXT PRIMARY KEY, real_yield REAL, ttm_yield REAL,
                real_yield_year TEXT, ttm_period TEXT, dividend_source TEXT, updated_at TEXT);
        """)
        conn.execute(
            "INSERT INTO finance_snapshot VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("600900", 16.4, "2025年报", 1e10, 0.58, "东财", "2026-08-10"))
        conn.commit()
        conn.close()
        return db

    def test_old_db_migrated_and_readable(self, tmp_path):
        db = self._old_schema_db(tmp_path)
        cache = ScreenerCache(db)  # 打开即迁移
        got = cache.get_finance("600900")
        assert got is not None
        assert got.roe_latest == pytest.approx(16.4)
        assert got.roe_5y_median is None      # 旧行新列 → NULL
        assert got.is_cyclical is None

    def test_old_db_upsert_with_new_fields_after_migration(self, tmp_path):
        db = self._old_schema_db(tmp_path)
        cache = ScreenerCache(db)
        cache.upsert_finance(FinanceSnapshot(
            code="600987", roe_latest=15.0, roe_period="2025年报",
            net_profit_annual=1e9, payout_ratio=0.4,
            roe_5y_median=11.0, is_cyclical=True, finance_source="backtest.db"))
        got = cache.get_finance("600987")
        assert got is not None
        assert got.roe_5y_median == pytest.approx(11.0)
        assert got.is_cyclical is True

    def test_migration_idempotent(self, tmp_path):
        db = self._old_schema_db(tmp_path)
        ScreenerCache(db)          # 第一次迁移
        cache = ScreenerCache(db)  # 第二次打开不报错（幂等）
        assert cache.get_finance("600900") is not None


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

    def test_prune_stale_rows(self, cache):
        """prune_stale_rows 删超期（>90 天）+ NULL updated_at 行，保留近期行；quote 不受影响。"""
        # 超期（100 天前）：dividend 与 finance
        cache.upsert_dividend(DividendSnapshot(code="600900", real_yield=5.2, ttm_yield=5.5,
                                               real_yield_year="2025", ttm_period="p",
                                               dividend_source="mootdx",
                                               updated_at=self._ts(100)))
        cache.upsert_finance(FinanceSnapshot(code="600900", roe_latest=15, roe_period="2025",
                                             net_profit_annual=1e9, payout_ratio=0.5,
                                             finance_source="ths", updated_at=self._ts(100)))
        # NULL updated_at：sustainability
        cache.upsert_sustainability(SustainabilitySnapshot(code="600887", financial_rows="[]",
                                                           cashflow_rows="[]", dividend_rows="[]",
                                                           industry="x", price_change_1y=0,
                                                           top10_holding=0, source="s",
                                                           updated_at=None))
        # 近期（10 天前）：dividend 保留
        cache.upsert_dividend(DividendSnapshot(code="600887", real_yield=5.2, ttm_yield=5.5,
                                               real_yield_year="2025", ttm_period="p",
                                               dividend_source="mootdx",
                                               updated_at=self._ts(10)))
        # quote_snapshot 超期但不应被清理（每日全量覆盖，不做时间裁剪）
        cache.upsert_quote(QuoteSnapshot(code="600900", price=10, pe_ttm=1, pb=1,
                                         total_shares=1, market_cap=1,
                                         quote_time="t", source="s",
                                         updated_at=self._ts(100)))

        pruned = cache.prune_stale_rows(max_age_days=90)
        assert pruned == 3  # 600900-dividend + 600900-finance + 600887-sustainability(NULL)

        # 删除后：600900 dividend/finance 不存在，600887 dividend 保留
        assert cache.is_dividend_stale("600900", max_age_days=90) is True
        assert cache.is_dividend_stale("600887", max_age_days=90) is False
        # quote_snapshot 保留（虽超期）
        assert cache.is_quote_stale("600900", max_age_days=90) is True


class TestSourceTracking:
    def test_source_stored(self, cache):
        cache.upsert_quote(QuoteSnapshot(code="600900", price=10, pe_ttm=1, pb=1,
                                         total_shares=1, market_cap=1,
                                         quote_time="t", source="腾讯"))
        got = cache.get_quote("600900")
        assert got.source == "腾讯"  # 数据铁律：来源标注
