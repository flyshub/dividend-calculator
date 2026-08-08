"""选股器行情快照测试（spec #67，工单 #70）。

覆盖腾讯批量行情解析 + 行情候选池筛选，全部 mock 响应/注入，不碰真实 HTTP：
- fetch_quote_batch：v_<code> 标签解析、指数过滤、字段索引
- fetch_all_quotes：分批 + 缓存写入
- build_candidate_pool：行情可用性筛选

先例：tests/test_tencent_quote.py（行情解析）、tests/test_screener_cache.py。
"""
from unittest.mock import patch

import pytest

from src.screener_cache import ScreenerCache
from src.screener_quotes import (
    _is_index_code,
    build_candidate_pool,
    fetch_quote_batch,
    fetch_all_quotes,
)


def _batch_text(*items):
    """构造腾讯批量响应文本：v_sh600900="<fields>"。"""
    return ";".join(f'v_{tag}="{body}"' for tag, body in items)


def _fields(name="长江电力", price="27.75", pe="27.84", pb="3.26", total="24468217716"):
    # 构造 88 字段，关键位置填值，其余空
    f = [""] * 88
    f[1] = name
    f[3] = price
    f[33] = pe
    f[46] = pb
    f[72] = "23456789012"  # A股股本（仅A股，与总股本不同，验证 Index73 区分）
    f[73] = total         # 总股本（含A+H，Index73 铁律）
    return "~".join(f)


class TestIndexFilter:
    def test_equity_code_not_index(self):
        assert _is_index_code("600900", "sh") is False

    def test_sh_index_excluded(self):
        # 000001 在 sh 市场 = 上证指数
        assert _is_index_code("000001", "sh") is True

    def test_sh_equity_not_index(self):
        # 000001 在 sz 市场 = 平安银行（非指数）
        assert _is_index_code("000001", "sz") is False

    def test_sz_index_excluded(self):
        assert _is_index_code("399001", "sz") is True

    def test_bj_not_handled(self):
        # 北交所（8开头）不在本项目支持范围（mootdx 列表不含）
        assert _is_index_code("830001", "bj") is False


class TestFetchQuoteBatch:
    @patch("src.screener_quotes._SESSION.get")
    def test_parses_batch_response(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.encoding = "GBK"
        mock_get.return_value.text = _batch_text(
            ("sh600900", _fields()),
            ("sz000001", _fields(name="平安银行", price="12.5", pe="5.0", pb="0.8", total="19400000000")),
        )
        quotes = fetch_quote_batch(["600900", "000001"])
        assert len(quotes) == 2
        q = quotes["600900"]
        assert q.price == pytest.approx(27.75)
        assert q.pe_ttm == pytest.approx(27.84)
        assert q.total_shares == 24468217716
        assert q.market_cap == pytest.approx(27.75 * 24468217716)

    @patch("src.screener_quotes._SESSION.get")
    def test_index_codes_skipped(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.encoding = "GBK"
        mock_get.return_value.text = _batch_text(("sh600900", _fields()))
        # 传入含指数代码 → 请求前被过滤，只请求有效股
        quotes = fetch_quote_batch(["600900", "000001"])
        assert "600900" in quotes
        assert "000001" not in quotes  # 指数被过滤
        # 请求 URL 不含指数代码
        called_url = mock_get.call_args[0][0]
        assert "sh000001" not in called_url

    @patch("src.screener_quotes._SESSION.get")
    def test_invalid_code_silently_skipped(self, mock_get):
        # 退市/无效代码：响应无对应条目 → 不映射
        mock_get.return_value.status_code = 200
        mock_get.return_value.encoding = "GBK"
        mock_get.return_value.text = _batch_text(("sh600900", _fields()))
        quotes = fetch_quote_batch(["600900", "999999"])
        assert "999999" not in quotes

    @patch("src.screener_quotes._SESSION.get")
    def test_zero_price_skipped(self, mock_get):
        # 停牌股 price=0.00 → _safe_float 返回 None → 剔除
        mock_get.return_value.status_code = 200
        mock_get.return_value.encoding = "GBK"
        mock_get.return_value.text = _batch_text(
            ("sh600900", _fields()),
            ("sh601398", _fields(name="工商银行", price="0.00", pe="5.0", pb="0.6", total="356406257089")),
        )
        quotes = fetch_quote_batch(["600900", "601398"])
        assert "600900" in quotes
        assert "601398" not in quotes  # 停牌剔除

    @patch("src.screener_quotes._SESSION.get")
    def test_http_error_returns_empty(self, mock_get):
        mock_get.return_value.status_code = 500
        mock_get.return_value.raise_for_status.side_effect = Exception("500")
        assert fetch_quote_batch(["600900"]) == {}

    def test_empty_input(self):
        assert fetch_quote_batch([]) == {}


class TestFetchAllQuotes:
    @patch("src.screener_quotes.fetch_quote_batch")
    def test_batches_and_caches(self, mock_batch, tmp_path):
        # 900+ 只 → 分 2 批（800/批）
        from src.screener_cache import QuoteSnapshot
        codes = [f"{600000 + i}" for i in range(1000)]
        q1 = QuoteSnapshot(code="600000", price=10, pe_ttm=10, pb=1,
                           total_shares=1e9, market_cap=1e10, quote_time="", source="腾讯")
        q2 = QuoteSnapshot(code="601000", price=10, pe_ttm=10, pb=1,
                           total_shares=1e9, market_cap=1e10, quote_time="", source="腾讯")
        mock_batch.side_effect = [{"600000": q1}, {"601000": q2}]
        cache = ScreenerCache(tmp_path / "s.db")
        fetch_all_quotes(codes, cache=cache)
        # 分批调用（800/批 → 2 批）
        assert mock_batch.call_count == 2
        # 缓存写入
        assert cache.get_quote("600000") is not None

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
