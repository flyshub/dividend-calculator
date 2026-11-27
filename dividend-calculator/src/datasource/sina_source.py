"""
新浪行情数据源适配器

价格从新浪行情获取，总股本从腾讯行情获取。
作为 TencentSource 的备用降级路径。
"""
import logging
import re
from typing import Optional, List, Tuple

import requests

from .base import StockInfo, DividendDetail
from .tencent_source import TencentSource
from ..utils import ensure_6digit

logger = logging.getLogger(__name__)


class SinaSource:
    """新浪行情数据源

    价格来自 hq.sinajs.cn（新浪行情接口），总股本来自腾讯行情。
    用于腾讯行情价格获取失败时的降级场景。
    """

    def __init__(self, tencent_source=None):
        self._tencent_source = tencent_source

    @property
    def name(self) -> str:
        return "sina"

    @property
    def priority(self) -> int:
        return 4  # 低于 tencent(3)，高于 mootdx(5)

    def get_stock_info(self, stock_input: str) -> Optional[StockInfo]:
        """获取股票基本信息：新浪价格 + 腾讯总股本"""
        stock_code = ensure_6digit(stock_input)
        if not stock_code:
            return None

        price = _get_price_from_sina(stock_code)
        if price is None:
            return None

        total_shares = self._get_total_shares_from_tencent(stock_code)
        if total_shares is None:
            logger.debug("sina 无法从腾讯获取 %s 总股本", stock_code)
            return None

        return StockInfo(
            stock_code=stock_code,
            current_price=price,
            total_shares=total_shares,
        )

    def get_latest_dividend(
        self, stock_code: str, stock_info: StockInfo
    ) -> Tuple[float, Optional[str], List[DividendDetail], str]:
        """新浪行情不提供分红数据，由 MootdxSource 负责"""
        return 0.0, None, [], "sina_source不提供分红数据"

    def _get_total_shares_from_tencent(self, stock_code: str) -> Optional[float]:
        """从腾讯行情获取总股本"""
        try:
            tencent = self._tencent_source or TencentSource()
            info = tencent.get_stock_info(stock_code)
            if info is not None:
                return info.total_shares
        except Exception as e:
            logger.debug("sina 通过腾讯获取总股本失败 %s: %s", stock_code, e)
        return None


def _get_price_from_sina(stock_code: str) -> Optional[float]:
    """从新浪行情获取最新价格"""
    try:
        prefix = "sh" if stock_code.startswith("6") else "sz"
        url = "https://hq.sinajs.cn/list={}{}".format(prefix, stock_code)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://finance.sina.com.cn",
        }
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            match = re.search(r'"([^"]+)"', resp.text)
            if match:
                fields = match.group(1).split(",")
                if len(fields) >= 4:
                    price = float(fields[3])
                    if price > 0:
                        logger.debug("新浪行情获取价格 %s: %.2f", stock_code, price)
                        return price
    except Exception as e:
        logger.debug("新浪行情获取价格失败 %s: %s", stock_code, e)
    return None


