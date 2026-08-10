"""_get_all_dividend_records 主备降级回归测试（issue #77 审查修复）。

核心不变量（#38 M5 语义）：
- 东财取数失败（None）→ 必须落入 mootdx 兜底，不能短路返回空
- 东财真无分红（[]）→ 直接返回空，不走兜底（避免无分红公司误判为取数失败）
- 返回值 (records, source) 必须如实标注实际数据来源
"""
from unittest.mock import patch

from src.api import _get_all_dividend_records
from src.datasource.base import DividendRecord


def _mk_xdxr_df():
    """构造一份包含 category==1 现金分红记录的 xdxr DataFrame"""
    import pandas as pd
    return pd.DataFrame([
        {"category": 1, "year": 2026, "month": 7, "day": 10, "fenhong": 10.03},
        {"category": 2, "year": 2026, "month": 1, "day": 5, "fenhong": 5.0},  # 非现金，应过滤
    ])


class TestEastmoneyPrimary:
    # 注：_get_all_dividend_records 内部为函数级 import（from .eastmoney_fetcher import ...），
    # 因此 patch 目标必须是源模块，而非 src.api 上的名字。
    FETCH = "src.eastmoney_fetcher.fetch_dividend_rows"
    PARSE = "src.sustainability.parse_dividend_rows"
    CLIENT = "src.datasource.mootdx_source.get_quotes_client"

    def test_eastmoney_success_returns_records_and_source(self):
        rows = [{"除权除息日": "2026-07-10", "派息": 100.3, "报告期": "2025年报"}]
        with patch(self.FETCH, return_value=rows) as fetch, \
             patch(self.PARSE) as parse:
            parse.return_value = ([DividendRecord("2026-07-10", 10.03, "2025年报")], None)
            records, source = _get_all_dividend_records("600036")
        assert source == "东财"
        assert len(records) == 1
        fetch.assert_called_once_with("600036")

    def test_eastmoney_none_falls_back_to_mootdx(self):
        """东财取数失败（None）→ 必须走 mootdx 兜底"""
        with patch(self.FETCH, return_value=None), \
             patch(self.CLIENT) as client:
            client.return_value.xdxr.return_value = _mk_xdxr_df()
            records, source = _get_all_dividend_records("600036")
        assert source == "mootdx xdxr"
        assert len(records) == 1
        assert records[0].dividend_per_10 == 10.03  # 非现金 category==2 已过滤

    def test_eastmoney_empty_returns_empty_without_fallback(self):
        """东财请求成功但真无分红（[]）→ 短路返回，不触发 mootdx"""
        with patch(self.FETCH, return_value=[]) as fetch, \
             patch(self.CLIENT) as client:
            records, source = _get_all_dividend_records("600036")
        assert records == []
        assert source == "东财"
        client.assert_not_called()

    def test_both_fail_returns_empty_and_no_source(self):
        with patch(self.FETCH, return_value=None), \
             patch(self.CLIENT) as client:
            client.return_value.xdxr.side_effect = RuntimeError("mootdx 不可用")
            records, source = _get_all_dividend_records("600036")
        assert records == []
        assert source == "无"
