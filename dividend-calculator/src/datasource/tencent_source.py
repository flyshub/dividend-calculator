"""
腾讯行情数据源适配器

一次 HTTP 请求获取：价格 + 总股本 + PE_TTM + PB。
不提供分红数据（由 MootdxSource 提供）。
"""
import logging
from typing import Optional, List, Tuple

from ..tencent_quote import fetch_tencent_quote
from ..utils import ensure_6digit
from .base import StockInfo, DividendDetail, DataSource
from .mootdx_source import get_quotes_client

logger = logging.getLogger(__name__)


class TencentSource:
    """腾讯行情数据源

    通过 qt.gtimg.cn 获取实时价格和总股本，一次请求完成。
    PE_TTM/PB 可从同一请求获取，但当前 StockInfo 协议未包含这两个字段。
    """

    @property
    def name(self) -> str:
        return "tencent"

    @property
    def priority(self) -> int:
        return 3  # 高于 mootdx(5)，优先尝试

    def get_stock_info(self, stock_input: str) -> Optional[StockInfo]:
        """获取股票基本信息：当前价格 + 总股本"""
        stock_code = ensure_6digit(stock_input)
        if not stock_code:
            return None

        quote = fetch_tencent_quote(stock_code)
        if quote is None:
            logger.debug("tencent 无法获取 %s 行情", stock_code)
            return None

        price = quote.price
        if price is None or price <= 0:
            logger.debug("tencent %s 价格无效: %s", stock_code, price)
            return None

        total_shares, warnings = self._resolve_total_shares(stock_code, quote)

        return StockInfo(
            stock_code=stock_code,
            current_price=price,
            total_shares=total_shares,
            warnings=warnings,
        )

    def _resolve_total_shares(self, stock_code: str, quote) -> Tuple[Optional[float], List[str]]:
        """解析总股本（审查 #3）：A+H 股必须用总股本，禁止静默回退 A 股股本。

        优先级：
        1. 腾讯 Index 73（总股本，含 A+H）有值 → 直接用
        2. 缺失 → best-effort 用 mootdx 财务快照 zongguben（真总股本，含 H 股）
        3. mootdx 也不可用 → 回退 Index 72（A 股股本），强告警（无法排除 A+H）

        返回 (total_shares, warnings)。
        """
        warnings: List[str] = []

        # 分支 1：腾讯总股本在场（对纯 A 或 A+H 都正确）
        if quote.total_shares is not None and quote.total_shares > 0:
            return quote.total_shares, warnings

        # 分支 2：best-effort 用 mootdx 真总股本（含 H 股）
        try:
            client = get_quotes_client()
            df = client.finance(symbol=stock_code)
            if df is not None and len(df) > 0 and 'zongguben' in df.columns:
                shares = float(df['zongguben'].iloc[0])
                if shares > 0:
                    logger.info("tencent %s 总股本缺失，用 mootdx 总股本 %.0f", stock_code, shares)
                    return shares, warnings
        except Exception as e:
            logger.debug("mootdx 总股本获取失败 %s: %s", stock_code, e)

        # 分支 3：回退 A 股股本并强告警（无法排除 A+H）
        if quote.a_shares is not None and quote.a_shares > 0:
            warnings.append("总股本缺失，已回退 A 股股本，A+H 股可能低估")
            logger.warning("tencent %s 总股本缺失，回退 A 股股本", stock_code)
            return quote.a_shares, warnings

        return None, warnings

    def get_latest_dividend(
        self, stock_code: str, stock_info: StockInfo
    ) -> Tuple[float, Optional[str], List[DividendDetail], str]:
        """腾讯行情不提供分红数据，由 MootdxSource 负责"""
        return 0.0, None, [], "tencent_source不提供分红数据"


