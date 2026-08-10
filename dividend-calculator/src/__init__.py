"""
真实股息率计算工具
"""
from src.datasource.base import StockInfo, DividendDetail
from src.api import get_stock_info
from src.dividend import calculate_true_dividend_yield, calculate_dividend_yield, DividendResult

__version__ = "0.1.0"
__all__ = [
    "StockInfo",
    "DividendDetail",
    "get_stock_info",
    "calculate_true_dividend_yield",
    "calculate_dividend_yield",
    "DividendResult",
]
