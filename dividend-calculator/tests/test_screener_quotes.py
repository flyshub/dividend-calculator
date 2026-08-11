"""选股器行情快照测试（spec #67，工单 #70）。

覆盖腾讯批量行情 → QuoteSnapshot 映射 + 行情候选池筛选，全部 mock 公开批量接口，
不碰真实 HTTP：
- fetch_all_quotes：调用 fetch_tencent_quote_batch、映射 market_cap/来源、缓存写入
- build_candidate_pool：行情可用性筛选

批量解析/字段索引/指数过滤测试已随 ADR-0002 迁移到 tests/test_tencent_quote.py。
先例：tests/test_tencent_quote.py（行情解析）、tests/test_screener_cache.py。
"""
from unittest.mock import patch

import pytest

from src.screener_cache import ScreenerCache
from src.screener_quotes import build_candidate_pool, fetch_all_quotes
from src.tencent_quote import TencentQuote


class TestFetchAllQuotes:
    @patch("src.screener_quotes.fetch_tencent_quote_batch")
    def test_maps_and_caches(self, mock_batch, tmp_path):
        q = TencentQuote(stock_code="600900", name="长江电力", price=27.75,
                         pe_ttm=27.84, pb=3.26, total_shares=24468217716, a_shares=23456789012)
        mock_batch.return_value = {"600900": q}
        cache = ScreenerCache(tmp_path / "s.db")
        snapshots = fetch_all_quotes(["600900"], cache=cache)
        assert len(snapshots) == 1
        s = snapshots[0]
        assert s.code == "600900"
        assert s.name == "长江电力"
        assert s.pe_ttm == pytest.approx(27.84)
        assert s.total_shares == 24468217716
        assert s.market_cap == pytest.approx(27.75 * 24468217716)
        assert s.source == "腾讯批量"
        # 缓存写入
        assert cache.get_quote("600900") is not None

    def test_empty_codes(self):
        assert fetch_all_quotes([]) == []


class TestBuildCandidatePool:
    def _q(self, price=10.0, shares=1e9):
        from src.screener_cache import QuoteSnapshot
        return QuoteSnapshot(code="600900", price=price, pe_ttm=10, pb=1,
                             total_shares=shares, market_cap=price * shares,
                             quote_time="", source="腾讯")

    def test_keeps_valid(self):
        pool = build_candidate_pool([self._q()])
        assert len(pool) == 1

    def test_drops_zero_price(self):
        pool = build_candidate_pool([self._q(price=0.0)])
        assert pool == []

    def test_drops_missing_shares(self):
        pool = build_candidate_pool([self._q(shares=0)])
        assert pool == []
