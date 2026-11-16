"""月度价格多源降级（腾讯 → 东财 push2his → mootdx）。

2026-11 实测：腾讯 fqkline 会因限频返回 WAF 页面（waf.tencent.com/501page），
普通 requests 拿到 HTML 而非 JSON → 该源整体失效，走势图无数据。东财
push2his/klt=103 同口径（fqt=1 前复权 / fqt=0 不复权）可作退路。
"""
from unittest.mock import MagicMock, patch

from src.api import _get_monthly_prices
from src.monthly_prices import (
    DEFAULT_PROVIDERS,
    PriceSource,
    _fetch_eastmoney,
    fetch_monthly_prices,
)


def _resp(klines):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"rc": 0, "data": {"klines": klines}}
    return r


def _router(qfq, nominal):
    """按 URL 里的 fqt 参数返回不同数据。"""
    def _get(url, **kwargs):
        return _resp(qfq if url.endswith("fqt=1") else nominal)
    return _get


class TestEastmoneyMonthlyPrices:
    def test_dual_price_basis(self):
        with patch("requests.get", side_effect=_router(
            ["2026-10-30,53.80", "2026-11-28,55.10"],
            ["2026-10-30,62.40", "2026-11-28,55.10"],
        )):
            rows = _fetch_eastmoney("601318")
        assert [r.date for r in rows] == ["2026-10-30", "2026-11-28"]
        assert [r.close for r in rows] == [53.80, 55.10]        # 前复权
        assert [r.close_nominal for r in rows] == [62.40, 55.10]  # 不复权

    def test_truncates_to_count_keep_latest(self):
        klines = [f"20{10 + i}-01-31,{10 + i}.0" for i in range(20)]
        with patch("requests.get", side_effect=_router(klines, klines)):
            rows = _fetch_eastmoney("601318", count=5)
        assert len(rows) == 5
        assert rows[-1].date == "2029-01-31"      # 升序取最近 5 根

    def test_nominal_failure_keeps_price_line(self):
        def _get(url, **kwargs):
            if url.endswith("fqt=1"):
                return _resp(["2026-10-30,53.80"])
            raise RuntimeError("timeout")
        with patch("requests.get", side_effect=_get):
            rows = _fetch_eastmoney("601318")
        assert len(rows) == 1
        assert rows[0].close == 53.80
        assert rows[0].close_nominal is None      # 宁缺毋假

    def test_empty_payload_returns_empty(self):
        with patch("requests.get", side_effect=_router([], [])):
            assert _fetch_eastmoney("601318") == []

    def test_http_error_returns_empty(self):
        r = MagicMock()
        r.status_code = 502
        with patch("requests.get", return_value=r):
            assert _fetch_eastmoney("601318") == []

    def test_dirty_rows_skipped(self):
        with patch("requests.get", side_effect=_router(
            ["2026-09-30,", "bad-line", "2026-11-28,55.10", "2026-10-30,0"],
            [],
        )):
            rows = _fetch_eastmoney("601318")
        assert [r.date for r in rows] == ["2026-11-28"]  # 空价/坏行/非正价都丢


class TestFallbackChain:
    """降级顺序由 DEFAULT_PROVIDERS 声明，测试直接替换这张表。"""

    def _providers(self, *specs):
        return tuple(PriceSource(name, fn) for name, fn in specs)

    def test_default_order_is_tencent_eastmoney_mootdx(self):
        assert [p.name for p in DEFAULT_PROVIDERS] == ["腾讯", "东财", "mootdx"]

    def test_first_source_with_data_wins(self):
        calls = []

        def t(code):
            calls.append("t")
            return ["t"]

        def e(code):
            calls.append("e")
            return ["e"]

        rows, source = fetch_monthly_prices("601318", self._providers(("腾讯", t), ("东财", e)))
        assert rows == ["t"] and source == "腾讯"
        assert calls == ["t"]

    def test_falls_back_when_source_empty(self):
        rows, source = fetch_monthly_prices(
            "601318",
            self._providers(("腾讯", lambda c: []), ("东财", lambda c: ["e"]), ("mootdx", lambda c: ["m"])))
        assert rows == ["e"] and source == "东财"

    def test_empty_when_all_fail(self):
        rows, source = fetch_monthly_prices(
            "601318", self._providers(("腾讯", lambda c: []), ("东财", lambda c: [])))
        assert rows == [] and source == ""

    def test_api_delegates_and_returns_rows(self):
        with patch("src.monthly_prices.DEFAULT_PROVIDERS",
                   self._providers(("腾讯", lambda c: ["t"]))):
            assert _get_monthly_prices("601318") == ["t"]
