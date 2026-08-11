"""腾讯行情/K线取数模块测试（ADR-0002）。

覆盖：单股/批量行情解析（字段索引、v_<code> 标签、指数过滤）、K 线取数，
全部 mock 会话响应，不碰真实 HTTP（真实请求见下方 integration 标记）。
"""

import pytest
from unittest.mock import patch

from src.tencent_quote import (
    TencentQuote,
    _is_index_code,
    _safe_float,
    _safe_str,
    fetch_kline_rows,
    fetch_tencent_quote,
    fetch_tencent_quote_batch,
)


# ---------------------------------------------------------------------------
# TencentQuote 不可变性
# ---------------------------------------------------------------------------

def test_tencent_quote_frozen():
    """TencentQuote 是不可变的（frozen dataclass）。"""
    q = TencentQuote(stock_code="600519", name="贵州茅台", price=1800.0)
    with pytest.raises(Exception):
        q.price = 1900.0  # type: ignore


def test_tencent_quote_defaults():
    """所有字段默认 None。"""
    q = TencentQuote(stock_code="000001")
    assert q.stock_code == "000001"
    assert q.name is None
    assert q.price is None
    assert q.pe_ttm is None
    assert q.pb is None
    assert q.total_shares is None
    assert q.a_shares is None


# ---------------------------------------------------------------------------
# _safe_float
# ---------------------------------------------------------------------------

def test_safe_float_valid():
    assert _safe_float(["", "0", "3.55"], 2) == 3.55


def test_safe_float_index_out_of_range():
    assert _safe_float(["a"], 5) is None


def test_safe_float_invalid_value():
    assert _safe_float(["abc"], 0) is None


def test_safe_float_zero_returns_none():
    """价格为 0 视为无效。"""
    assert _safe_float(["0.0"], 0) is None


def test_safe_float_negative_returns_none():
    """负数视为无效。"""
    assert _safe_float(["-5.0"], 0) is None


# ---------------------------------------------------------------------------
# _safe_str
# ---------------------------------------------------------------------------

def test_safe_str_valid():
    assert _safe_str(["", "贵州茅台"], 1) == "贵州茅台"


def test_safe_str_empty():
    assert _safe_str([""], 0) is None


def test_safe_str_index_out_of_range():
    assert _safe_str(["a"], 5) is None


def _batch_text(*items):
    """构造腾讯批量响应文本：v_sh600900="<fields>"。"""
    return ";".join(f'v_{tag}="{body}"' for tag, body in items)


def _fields(name="长江电力", price="27.75", pe="27.84", pb="3.26", total="24468217716"):
    # 构造 88 字段，关键位置填值，其余空
    f = [""] * 88
    f[1] = name
    f[3] = price
    f[33] = "99.99"  # 当日最高价（字段33），PE-TTM 在字段 39——验证正确取 39
    f[39] = pe       # PE-TTM（修复：字段 39，非 33）
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
        # 北交所（8开头）不是指数段
        assert _is_index_code("830001", "bj") is False


class TestFetchQuoteBatch:
    @patch("src.tencent_quote._SESSION.get")
    def test_parses_batch_response(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.encoding = "GBK"
        mock_get.return_value.text = _batch_text(
            ("sh600900", _fields()),
            ("sz000001", _fields(name="平安银行", price="12.5", pe="5.0", pb="0.8", total="19400000000")),
        )
        quotes = fetch_tencent_quote_batch(["600900", "000001"])
        assert len(quotes) == 2
        q = quotes["600900"]
        assert q.price == pytest.approx(27.75)
        assert q.pe_ttm == pytest.approx(27.84)
        assert q.total_shares == 24468217716
        assert q.a_shares == 23456789012
        assert quotes["000001"].name == "平安银行"

    @patch("src.tencent_quote._SESSION.get")
    def test_index_codes_skipped(self, mock_get):
        mock_get.return_value.status_code = 200
        mock_get.return_value.encoding = "GBK"
        mock_get.return_value.text = _batch_text(("sh600900", _fields()))
        # 传入含指数代码 → 请求前被过滤，只请求有效股
        quotes = fetch_tencent_quote_batch(["600900", "000001"])
        assert "600900" in quotes
        assert "000001" not in quotes  # 指数被过滤
        called_url = mock_get.call_args[0][0]
        assert "sh000001" not in called_url

    @patch("src.tencent_quote._SESSION.get")
    def test_invalid_code_silently_skipped(self, mock_get):
        # 退市/无效代码：响应无对应条目 → 不映射
        mock_get.return_value.status_code = 200
        mock_get.return_value.encoding = "GBK"
        mock_get.return_value.text = _batch_text(("sh600900", _fields()))
        quotes = fetch_tencent_quote_batch(["600900", "999999"])
        assert "999999" not in quotes

    @patch("src.tencent_quote._SESSION.get")
    def test_zero_price_skipped(self, mock_get):
        # 停牌股 price=0.00 → _safe_float 返回 None → 剔除
        mock_get.return_value.status_code = 200
        mock_get.return_value.encoding = "GBK"
        mock_get.return_value.text = _batch_text(
            ("sh600900", _fields()),
            ("sh601398", _fields(name="工商银行", price="0.00", pe="5.0", pb="0.6", total="356406257089")),
        )
        quotes = fetch_tencent_quote_batch(["600900", "601398"])
        assert "600900" in quotes
        assert "601398" not in quotes  # 停牌剔除

    @patch("src.tencent_quote._SESSION.get")
    def test_http_error_returns_empty(self, mock_get):
        mock_get.return_value.status_code = 500
        mock_get.return_value.raise_for_status.side_effect = Exception("500")
        assert fetch_tencent_quote_batch(["600900"]) == {}

    @patch("src.tencent_quote._SESSION.get")
    def test_multi_batch_splits_at_800(self, mock_get):
        # 900+ 只 → 按 ≤800 拆 2 批，逐批独立请求
        codes = [f"{600000 + i}" for i in range(1000)]
        mock_get.return_value.status_code = 200
        mock_get.return_value.encoding = "GBK"
        mock_get.return_value.text = ""
        assert fetch_tencent_quote_batch(codes) == {}
        urls = [call.args[0] for call in mock_get.call_args_list]
        assert len(urls) == 2
        assert len(urls[0].split(",")) == 800
        assert len(urls[1].split(",")) == 200
        assert "sh600000" in urls[0]
        assert "sh600799" in urls[0]
        assert "sh600800" in urls[1]
        assert "sh600999" in urls[1]

    def test_empty_input(self):
        assert fetch_tencent_quote_batch([]) == {}


class _FakeKlineResp:
    """模拟 requests.Response（json 由调用方注入）。"""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class TestFetchKline:
    @patch("src.tencent_quote._SESSION.get")
    def test_month_rows(self, mock_get):
        rows = [["2026-01-31", "10", "10.5"], ["2026-02-28", "11", "11.2"]]
        mock_get.return_value = _FakeKlineResp({"data": {"sh600900": {"qfqmonth": rows}}})
        assert fetch_kline_rows("600900", period="month", count=120) == rows
        url = mock_get.call_args[0][0]
        assert "sh600900,month,,,120,qfq" in url

    @patch("src.tencent_quote._SESSION.get")
    def test_day_rows(self, mock_get):
        rows = [["2026-08-07", "42.5", "42.424"]]
        mock_get.return_value = _FakeKlineResp({"data": {"sz000001": {"qfqday": rows}}})
        assert fetch_kline_rows("000001", period="day", count=250) == rows

    @patch("src.tencent_quote._SESSION.get")
    def test_bj_prefix(self, mock_get):
        """北交所代码 → bj 前缀（6→sh，8/4/92→bj，其余→sz）。"""
        seen = {}

        def fake_get(url, **kwargs):
            seen["key"] = url.split("param=")[1].split(",")[0]
            return _FakeKlineResp({"data": {seen["key"]: {"qfqday": [["a", "10", "10"], ["b", "20", "20"]]}}})

        mock_get.side_effect = fake_get
        assert fetch_kline_rows("830799", period="day", count=250) is not None
        assert seen["key"] == "bj830799"

    @patch("src.tencent_quote._SESSION.get")
    def test_no_rows_returns_empty(self, mock_get):
        mock_get.return_value = _FakeKlineResp({"data": {"sh600900": {}}})
        assert fetch_kline_rows("600900") == []

    @patch("src.tencent_quote._SESSION.get")
    def test_http_error_returns_none(self, mock_get):
        mock_get.return_value.raise_for_status.side_effect = Exception("500")
        assert fetch_kline_rows("600900") is None


# ---------------------------------------------------------------------------
# fetch_tencent_quote — 集成测试（需要网络，标记为 integration）
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_fetch_known_stock():
    """获取贵州茅台行情，验证关键字段非空。"""
    q = fetch_tencent_quote("600519")
    assert q is not None
    assert q.stock_code == "600519"
    assert q.name is not None
    assert q.price is not None
    assert q.price > 0


@pytest.mark.integration
def test_fetch_known_stock_sz():
    """获取深市股票行情。"""
    q = fetch_tencent_quote("000001")
    assert q is not None
    assert q.stock_code == "000001"
    assert q.price is not None


@pytest.mark.integration
def test_fetch_invalid_stock():
    """无效代码返回 None。"""
    q = fetch_tencent_quote("999999")
    # 腾讯行情对不存在代码可能返回空字段或空响应
    # 无论如何不应抛异常
    assert q is None or isinstance(q, TencentQuote)
