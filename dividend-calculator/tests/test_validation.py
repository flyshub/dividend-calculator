"""test_validation.py — sanity bound 校验层测试（审查 #4）"""
import pytest

from src.datasource.base import StockInfo
from src.datasource import validation


class TestCheckStockInfo:
    def test_normal_values_no_warning(self):
        info = StockInfo(stock_code="600900", current_price=26.56, total_shares=2.27e10)
        assert validation.check_stock_info(info) == []

    def test_price_over_upper_bound(self):
        info = StockInfo(stock_code="600900", current_price=99999.0, total_shares=2.27e10)
        w = validation.check_stock_info(info)
        assert len(w) == 1
        assert "当前股价" in w[0]

    def test_shares_over_upper_bound(self):
        info = StockInfo(stock_code="600900", current_price=26.56, total_shares=1e14)
        w = validation.check_stock_info(info)
        assert len(w) == 1
        assert "总股本" in w[0]

    def test_both_over_bound(self):
        info = StockInfo(stock_code="600900", current_price=99999.0, total_shares=1e14)
        assert len(validation.check_stock_info(info)) == 2


class TestScalarChecks:
    @pytest.mark.parametrize("fn", [
        validation.check_dividend_yield,
        validation.check_payout_ratio,
        validation.check_pe,
    ])
    def test_none_passthrough(self, fn):
        assert fn(None) is None

    def test_yield_normal(self):
        assert validation.check_dividend_yield(5.3) is None

    def test_yield_over_100(self):
        assert validation.check_dividend_yield(150.0) is not None

    def test_payout_over_10(self):
        assert validation.check_payout_ratio(12.0) is not None

    def test_payout_normal_above_1(self):
        # 支付率 >1（成熟期股）合法，不在软界内
        assert validation.check_payout_ratio(1.5) is None

    def test_pe_high(self):
        assert validation.check_pe(20000.0) is not None

    def test_pb_high(self):
        assert validation.check_pb(150.0) is not None

    def test_pb_normal(self):
        assert validation.check_pb(3.2) is None

    def test_roe_negative_ok(self):
        # 亏损股 ROE 为负合法
        assert validation.check_roe(-15.0) is None

    def test_roe_extreme(self):
        assert validation.check_roe(150.0) is not None

    def test_net_profit_negative_ok(self):
        assert validation.check_net_profit(-1e9) is None

    def test_net_profit_extreme(self):
        assert validation.check_net_profit(1e14) is not None

    def test_roe_upper_ok(self):
        # ROE=100 是边界（>=hi 触发，但 99.9 正常）
        assert validation.check_roe(99.9) is None
        assert validation.check_roe(100.0) is not None
