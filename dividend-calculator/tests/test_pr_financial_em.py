"""pr._get_financial_em 单测（东财 F10 财务口径 + 降级链）。

背景（2026-11 事故）：mootdx F10 走 TDX 7709 TCP，GitHub Actions 出网被封；
akshare 1.20.41 移除 stock_financial_abstract_ths，同花顺对机房 IP 返回 403。
两条旧路径在 CI 上同时失效 → 财务快照静默冻结在 2026-08-08。
本文件锁定东财路径的口径与降级行为，防止再次静默退化。
"""
from unittest.mock import patch


from src.pr import _get_financial, _get_financial_em


def _row(date: str, roe, np_, report_type: str = "") -> dict:
    return {
        "REPORT_DATE": f"{date} 00:00:00",
        "REPORT_TYPE": report_type,
        "ROEJQ": roe,
        "PARENTNETPROFIT": np_,
    }


ROWS = [
    _row("2026-09-30", 11.66, 26343828523.43, "三季报"),   # 最新报告期（季报，仅用于 latest_period）
    _row("2026-06-30", 6.55, 14756000000.0, "中报"),
    _row("2025-12-31", 15.90, 34502809176.39, "年报"),
    _row("2024-12-31", 15.71, 32496000000.0, "年报"),
    _row("2023-12-31", 13.52, 27039000000.0, "年报"),
    _row("2022-12-31", 9.32, 21303000000.0, "年报"),
    _row("2021-12-31", 14.92, 26268000000.0, "年报"),
    _row("2020-12-31", 16.71, 26298000000.0, "年报"),
]


class TestGetFinancialEm:
    def test_roe_from_annual_rows_not_quarterly(self):
        """ROE 取年报行（不含季报累计），否则年报 ROE 会被季报 11.66 顶替。"""
        with patch("src.eastmoney_fetcher.fetch_financial_rows", return_value=ROWS):
            roe, roe5y, np_cur, np_annual, src, errors, year = _get_financial_em("600900")
        assert roe == 15.90
        assert year == 2025
        assert src == "东方财富"
        assert errors == []

    def test_five_year_median_of_annual_roe(self):
        """5 年中位数取最近 5 个年报（15.90/15.71/13.52/9.32/14.92 → 14.92）。"""
        with patch("src.eastmoney_fetcher.fetch_financial_rows", return_value=ROWS):
            _, roe5y, _, _, _, _, _ = _get_financial_em("600900")
        assert roe5y == 14.92

    def test_latest_period_net_profit_from_most_recent_report(self):
        """最新报告期净利润取 rows[0]（季报累计），与年报净利润分开。"""
        with patch("src.eastmoney_fetcher.fetch_financial_rows", return_value=ROWS):
            _, _, np_cur, np_annual, _, _, _ = _get_financial_em("600900")
        assert np_cur == 26343828523.43
        assert np_annual == 34502809176.39

    def test_empty_rows_returns_none_with_error(self):
        with patch("src.eastmoney_fetcher.fetch_financial_rows", return_value=[]):
            roe, _, _, _, src, errors, year = _get_financial_em("600900")
        assert roe is None and src == "无" and year is None
        assert "东财财务数据为空" in errors

    def test_no_annual_rows_returns_none_with_error(self):
        """只有季报（新股/次新股）→ 没有年报行可算 ROE 中位数，报错而非猜。"""
        quarterly_only = [r for r in ROWS if r["REPORT_DATE"][5:10] != "12-31"]
        with patch("src.eastmoney_fetcher.fetch_financial_rows", return_value=quarterly_only):
            roe, _, _, _, src, errors, _ = _get_financial_em("600900")
        assert roe is None and src == "无"
        assert "东财财务无年报行" in errors

    def test_median_skips_none_roe(self):
        rows = [
            _row("2025-12-31", 20.0, 1.0),
            _row("2024-12-31", None, 1.0),
            _row("2023-12-31", 10.0, 1.0),
        ]
        with patch("src.eastmoney_fetcher.fetch_financial_rows", return_value=rows):
            _, roe5y, _, _, _, _, _ = _get_financial_em("600900")
        assert roe5y == 15.0  # median(20, 10)


class TestFinancialFallbackChain:
    def test_em_preferred_over_mootdx(self):
        """东财可用时不碰 mootdx（否则 CI 上每只股票白等 TCP 超时）。"""
        with patch("src.eastmoney_fetcher.fetch_financial_rows", return_value=ROWS), \
             patch("src.pr_data._get_financial_ths") as ths, \
             patch("src.datasource.mootdx_source.MootdxSource") as mootdx:
            out = _get_financial("600900")
        assert out[4] == "东方财富"
        mootdx.assert_not_called()
        ths.assert_not_called()

    def test_falls_back_to_mootdx_when_em_empty(self):
        with patch("src.eastmoney_fetcher.fetch_financial_rows", return_value=[]), \
             patch("src.datasource.mootdx_source.MootdxSource",
                   side_effect=Exception("tdx blocked")), \
             patch("src.pr_data._get_financial_ths",
                   return_value=(None, None, None, None, "无", ["同花顺不可用"], None)):
            roe, _, _, _, src, errors, _ = _get_financial("600900")
        assert roe is None and src == "无"
        # 两条路径的失败原因都保留，便于排查（不再静默）
        assert any("东财" in e for e in errors)
        assert any("mootdx" in e for e in errors)


class TestThsRemovedApi:
    def test_missing_akshare_function_reports_explicit_error(self):
        """akshare 新版移除接口时给出明确原因，而不是「数据为空」的误导信息。"""
        import akshare as ak

        fake_ak = type("FakeAk", (), {"__version__": "1.20.63"})
        with patch.dict("sys.modules", {"akshare": fake_ak}):
            from src.pr import _get_financial_ths
            roe, _, _, _, src, errors, _ = _get_financial_ths("600900")
        assert roe is None and src == "无"
        assert any("已移除" in e for e in errors)
        assert getattr(ak, "__version__", None)  # 真实 akshare 仍可导入（未污染）
