"""SinaSource adapter 测试"""
from unittest.mock import patch, MagicMock

from src.datasource.sina_source import SinaSource, _get_price_from_sina
from src.utils import ensure_6digit as _ensure_6digit
from src.datasource.base import StockInfo


class TestSinaSource:
    """SinaSource 核心行为测试"""

    @patch('src.datasource.sina_source._get_price_from_sina')
    @patch('src.datasource.sina_source.TencentSource')
    def test_get_stock_info_success(self, mock_tencent_cls, mock_sina_price):
        """正常场景：新浪价格 + 腾讯总股本"""
        mock_sina_price.return_value = 26.56
        mock_tencent = MagicMock()
        mock_tencent.get_stock_info.return_value = StockInfo(
            stock_code="600900", current_price=26.56, total_shares=22700000000.0
        )
        mock_tencent_cls.return_value = mock_tencent

        source = SinaSource()
        info = source.get_stock_info("600900")
        assert info is not None
        assert info.stock_code == "600900"
        assert info.current_price == 26.56
        assert info.total_shares == 22700000000.0

    @patch('src.datasource.sina_source._get_price_from_sina')
    def test_get_stock_info_returns_none_on_sina_failure(self, mock_sina_price):
        """新浪行情获取价格失败时，返回 None"""
        mock_sina_price.return_value = None
        source = SinaSource()
        assert source.get_stock_info("600900") is None

    @patch('src.datasource.sina_source._get_price_from_sina')
    @patch('src.datasource.sina_source.TencentSource')
    def test_get_stock_info_returns_none_on_tencent_failure(self, mock_tencent_cls, mock_sina_price):
        """腾讯总股本获取失败时，返回 None"""
        mock_sina_price.return_value = 26.56
        mock_tencent = MagicMock()
        mock_tencent.get_stock_info.return_value = None
        mock_tencent_cls.return_value = mock_tencent

        source = SinaSource()
        assert source.get_stock_info("600900") is None

    def test_get_latest_dividend_returns_not_supported(self):
        """get_latest_dividend 始终返回"不支持"提示"""
        source = SinaSource()
        stock_info = StockInfo(stock_code="600900", current_price=26.56, total_shares=22700000000.0)
        total_div, year, details, explanation = source.get_latest_dividend("600900", stock_info)
        assert total_div == 0.0
        assert year is None
        assert details == []
        assert "不提供分红数据" in explanation

    def test_priority_between_tencent_and_mootdx(self):
        """优先级应在 tencent(3) 和 mootdx(5) 之间"""
        source = SinaSource()
        assert source.priority == 4


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


class TestGetPriceFromSina:
    """_get_price_from_sina 网络层测试"""

    @patch('src.datasource.sina_source.requests.get')
    def test_parse_sina_response_success(self, mock_get):
        """正常解析新浪返回数据"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = 'var hq_str_sh600900="长江电力,26.50,26.48,26.56,26.60,26.40,26.55,26.56,12345678,323456789.00,100,26.55,200,26.54,300,26.53,400,26.52,500,26.51,100,26.56,200,26.57,300,26.58,400,26.59,500,26.60,2025-06-13,15:00:00,00,0.38,0.15,26.60,26.40,26.56/12345678/323456789.00,2.34,45.67,26.60,26.40,1.47,2789.00,2800.00,26.56,30.00,26.48,26.56";'
        mock_get.return_value = mock_resp

        price = _get_price_from_sina("600900")
        assert price == 26.56

    @patch('src.datasource.sina_source.requests.get')
    def test_parse_sina_response_sz_prefix(self, mock_get):
        """深圳股票使用 sz 前缀"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = 'var hq_str_sz000001="平安银行,12.50,12.48,12.56,12.60,12.40,12.55,12.56,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,2025-06-13,15:00:00,00";'
        mock_get.return_value = mock_resp

        price = _get_price_from_sina("000001")
        assert price == 12.56

        # 验证 URL 使用了 sz 前缀
        call_args = mock_get.call_args
        assert "sz000001" in call_args[0][0]

    @patch('src.datasource.sina_source.requests.get')
    def test_returns_none_on_network_error(self, mock_get):
        """网络异常时返回 None"""
        mock_get.side_effect = Exception("Connection refused")
        price = _get_price_from_sina("600900")
        assert price is None

    @patch('src.datasource.sina_source.requests.get')
    def test_returns_none_on_empty_response(self, mock_get):
        """空响应返回 None"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = 'var hq_str_sh600900="";'
        mock_get.return_value = mock_resp
        price = _get_price_from_sina("600900")
        assert price is None

    @patch('src.datasource.sina_source.requests.get')
    def test_returns_none_on_zero_price(self, mock_get):
        """价格为0时返回 None"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = 'var hq_str_sh600900="长江电力,0.00,0.00,0.00,0.00,0.00";'
        mock_get.return_value = mock_resp
        price = _get_price_from_sina("600900")
        assert price is None
