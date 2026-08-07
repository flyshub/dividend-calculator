"""测试 MootdxSource 依赖注入（消除全局单例泄漏）"""
from unittest.mock import MagicMock, patch

import pytest

from src.datasource.mootdx_source import MootdxSource


def _make_mock_client(closed=False):
    """创建一个 mock mootdx client"""
    client = MagicMock()
    client.closed = closed
    return client


class TestMootdxClientInjection:
    """测试 client 注入与 _get_client 逻辑"""

    def test_injected_client_used_when_not_closed(self):
        """注入的 client 未关闭时，应优先使用它"""
        mock_client = _make_mock_client()
        source = MootdxSource(client=mock_client)
        assert source._get_client() is mock_client

    def test_fallback_to_singleton_when_injected_closed(self):
        """注入的 client 已关闭时，应回退到全局单例"""
        closed_client = _make_mock_client(closed=True)
        source = MootdxSource(client=closed_client)
        with patch("src.datasource.mootdx_source.get_quotes_client") as mock_singleton:
            mock_singleton.return_value = _make_mock_client()
            result = source._get_client()
            mock_singleton.assert_called_once()
            assert result is mock_singleton.return_value

    def test_fallback_to_singleton_when_no_injection(self):
        """未注入 client 时，应使用全局单例"""
        source = MootdxSource()
        with patch("src.datasource.mootdx_source.get_quotes_client") as mock_singleton:
            mock_singleton.return_value = _make_mock_client()
            result = source._get_client()
            mock_singleton.assert_called_once()
            assert result is mock_singleton.return_value

    def test_get_stock_info_uses_injected_client(self):
        """get_stock_info 应通过注入的 client 获取数据"""
        mock_client = _make_mock_client()
        source = MootdxSource(client=mock_client)

        # Mock quotes 返回价格
        import pandas as pd
        quotes_df = pd.DataFrame({"price": [15.0]})
        mock_client.quotes.return_value = quotes_df

        # Mock finance 返回总股本
        finance_df = pd.DataFrame({"zongguben": [100_0000_0000]})
        mock_client.finance.return_value = finance_df

        result = source.get_stock_info("600000")
        assert result is not None
        assert result.current_price == 15.0
        assert result.total_shares == 100_0000_0000
        mock_client.quotes.assert_called_once_with(symbol="600000")
        mock_client.finance.assert_called_once_with(symbol="600000")


class TestMootdxParseXdxr:
    """_parse_xdxr 浮点精度回归测试（审查 #10）"""

    def _make_stock_info(self):
        from src.datasource.base import StockInfo
        return StockInfo(stock_code="600900", current_price=26.56, total_shares=1e9)

    def test_fenhong_float_precision_2_09999(self):
        """10派2.1 但协议返回 2.0999999 → round 后精确为 2.1，格式化无尾数"""
        import pandas as pd
        source = MootdxSource(client=_make_mock_client())
        df = pd.DataFrame([{
            "category": 1, "year": 2025, "month": 7, "day": 10,  # 7月除权 → 2024年报
            "fenhong": 2.0999999,
        }])
        total, year, details, _ = source._parse_xdxr(df, self._make_stock_info())
        assert year == "2024"
        assert details[0].dividend_per_10 == 2.1  # round(,4) 后精确
        # total = 0.21元/股 × 10亿股 = 2.1亿（乘法浮点尾数用 approx）
        assert total == pytest.approx(0.21 * 1e9)

    def test_fenhong_multi_record_no_drift(self):
        """多条记录累加无浮点漂移（每笔先 round）"""
        import pandas as pd
        source = MootdxSource(client=_make_mock_client())
        df = pd.DataFrame([
            {"category": 1, "year": 2025, "month": 7, "day": 10, "fenhong": 1.04999999},
            {"category": 1, "year": 2025, "month": 7, "day": 20, "fenhong": 1.04999999},
        ])
        total, _, _, _ = source._parse_xdxr(df, self._make_stock_info())
        # 两笔各 round 为 1.05 → 累加 2.1 → 每股 0.21 × 10亿
        assert total == pytest.approx(0.21 * 1e9)

    def test_fenhong_zero_or_none_skipped(self):
        """fenhong <=0 或 None 跳过"""
        import pandas as pd
        source = MootdxSource(client=_make_mock_client())
        df = pd.DataFrame([
            {"category": 1, "year": 2025, "month": 7, "day": 10, "fenhong": 0},
            {"category": 1, "year": 2025, "month": 7, "day": 11, "fenhong": None},
            {"category": 1, "year": 2025, "month": 7, "day": 12, "fenhong": 2.0},
        ])
        total, year, details, _ = source._parse_xdxr(df, self._make_stock_info())
        assert len(details) == 1  # 只有第三笔保留
        assert details[0].dividend_per_10 == 2.0

    def test_non_category_ignored(self):
        """category != 1 的行过滤"""
        import pandas as pd
        source = MootdxSource(client=_make_mock_client())
        df = pd.DataFrame([
            {"category": 1, "year": 2025, "month": 7, "day": 10, "fenhong": 2.0},
            {"category": 2, "year": 2025, "month": 7, "day": 10, "fenhong": 5.0},  # 送转
        ])
        total, _, details, _ = source._parse_xdxr(df, self._make_stock_info())
        assert len(details) == 1
        assert details[0].dividend_per_10 == 2.0
