"""TencentSource adapter 测试"""
import pytest
from unittest.mock import patch

from src.datasource.tencent_source import TencentSource
from src.utils import ensure_6digit as _ensure_6digit
from src.datasource.base import StockInfo
from src.tencent_quote import TencentQuote


class TestTencentSource:
    """TencentSource 核心行为测试"""

    @patch('src.datasource.tencent_source.fetch_tencent_quote')
    def test_get_stock_info_success(self, mock_fetch):
        """正常场景：返回 StockInfo"""
        mock_fetch.return_value = TencentQuote(
            stock_code="600987",
            name="航民股份",
            price=7.89,
            pe_ttm=12.5,
            pb=1.8,
            total_shares=1015560000.0,
            a_shares=1015560000.0,
        )
        source = TencentSource()
        info = source.get_stock_info("600987")
        assert info is not None
        assert info.stock_code == "600987"
        assert info.current_price == 7.89
        assert info.total_shares == 1015560000.0

    @patch('src.datasource.tencent_source.fetch_tencent_quote')
    @patch('src.datasource.tencent_source.get_quotes_client')
    def test_get_stock_info_fallback_to_a_shares(self, mock_client, mock_fetch):
        """total_shares 为空、mootdx 也失败时回退到 a_shares 并带 warning"""
        mock_fetch.return_value = TencentQuote(
            stock_code="000001",
            name="平安银行",
            price=12.50,
            total_shares=None,
            a_shares=1940591819.0,
        )
        mock_client.return_value.finance.side_effect = Exception("mootdx 不可用")
        source = TencentSource()
        info = source.get_stock_info("000001")
        assert info is not None
        assert info.total_shares == 1940591819.0
        assert any("回退 A 股股本" in w for w in info.warnings)

    @patch('src.datasource.tencent_source.fetch_tencent_quote')
    @patch('src.datasource.tencent_source.get_quotes_client')
    def test_get_stock_info_uses_mootdx_shares_when_total_missing(self, mock_client, mock_fetch):
        """total_shares 为空、mootdx 可用时用 mootdx 真总股本（含 H 股），无 warning"""
        mock_fetch.return_value = TencentQuote(
            stock_code="601919",
            name="中远海控",
            price=12.50,
            total_shares=None,
            a_shares=1.0e9,  # 若回退会用 A 股股本（错）
        )
        import pandas as pd
        mock_fetch2 = mock_client.return_value.finance
        mock_fetch2.return_value = pd.DataFrame([{"zongguben": 1.6e10}])  # 含 H 股真总股本
        source = TencentSource()
        info = source.get_stock_info("601919")
        assert info is not None
        assert info.total_shares == 1.6e10  # 用的是 mootdx 真总股本
        assert info.warnings == []  # 值正确，无告警

    @patch('src.datasource.tencent_source.fetch_tencent_quote')
    def test_get_stock_info_total_shares_present_no_mootdx_call(self, mock_fetch):
        """total_shares 有值时直接用，不触发 mootdx 降级"""
        mock_fetch.return_value = TencentQuote(
            stock_code="600900",
            name="长江电力",
            price=26.56,
            total_shares=2.27e10,
            a_shares=2.27e10,
        )
        source = TencentSource()
        info = source.get_stock_info("600900")
        assert info is not None
        assert info.total_shares == 2.27e10
        assert info.warnings == []

    @patch('src.datasource.tencent_source.fetch_tencent_quote')
    def test_get_stock_info_returns_none_on_fetch_failure(self, mock_fetch):
        """fetch_tencent_quote 返回 None 时，get_stock_info 也返回 None"""
        mock_fetch.return_value = None
        source = TencentSource()
        assert source.get_stock_info("600987") is None

    @patch('src.datasource.tencent_source.fetch_tencent_quote')
    def test_get_stock_info_returns_none_on_zero_price(self, mock_fetch):
        """价格为0时，get_stock_info 返回 None"""
        mock_fetch.return_value = TencentQuote(
            stock_code="600987",
            name="航民股份",
            price=0.0,
            pe_ttm=0.0,
            pb=0.0,
            total_shares=1015560000.0,
            a_shares=1015560000.0,
        )
        source = TencentSource()
        assert source.get_stock_info("600987") is None

    @patch('src.datasource.tencent_source.fetch_tencent_quote')
    def test_get_stock_info_returns_none_on_negative_price(self, mock_fetch):
        """价格为负时，get_stock_info 返回 None"""
        mock_fetch.return_value = TencentQuote(
            stock_code="600987",
            name="航民股份",
            price=-1.5,
            pe_ttm=0.0,
            pb=0.0,
            total_shares=1015560000.0,
            a_shares=1015560000.0,
        )
        source = TencentSource()
        assert source.get_stock_info("600987") is None

    def test_get_latest_dividend_returns_not_supported(self):
        """get_latest_dividend 始终返回"不支持"提示"""
        source = TencentSource()
        stock_info = StockInfo(stock_code="600987", current_price=7.89, total_shares=1e9)
        total_div, year, details, explanation = source.get_latest_dividend("600987", stock_info)
        assert total_div == 0.0
        assert year is None
        assert details == []
        assert "不提供分红数据" in explanation


class TestEnsure6Digit:
    """_ensure_6digit 输入校验"""

    def test_valid_code(self):
        assert _ensure_6digit("600987") == "600987"

    def test_with_dot_returns_numeric_part(self):
        assert _ensure_6digit("600987.SH") == "600987"

    def test_with_prefix_returns_none(self):
        assert _ensure_6digit("sh600987") is None

    def test_invalid_length(self):
        assert _ensure_6digit("60098") is None

    def test_non_numeric(self):
        assert _ensure_6digit("abc") is None
