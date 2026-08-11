"""选股器行情快照（spec #67，工单 #70）。

四级漏斗的第一层：全 A 股票列表 → 腾讯批量行情 → quote_snapshot。

腾讯行情的 wire 格式、字段索引、指数过滤与批量 HTTP 已收进 src.tencent_quote
（ADR-0002），本模块只负责：调用公开批量接口 → 映射 QuoteSnapshot → 写缓存 → 候选池筛选。
"""
from typing import List, Optional

from src.screener_cache import QuoteSnapshot, ScreenerCache
from src.tencent_quote import TencentQuote, fetch_tencent_quote_batch


def _to_snapshot(quote: TencentQuote) -> QuoteSnapshot:
    """TencentQuote → QuoteSnapshot（补 market_cap 与来源标注）。"""
    market_cap = None
    if quote.price is not None and quote.total_shares is not None:
        market_cap = quote.price * quote.total_shares
    return QuoteSnapshot(
        code=quote.stock_code,
        name=quote.name,
        price=quote.price,
        pe_ttm=quote.pe_ttm,
        pb=quote.pb,
        total_shares=quote.total_shares,
        market_cap=market_cap,
        quote_time="",
        source="腾讯批量",
    )


def fetch_all_quotes(codes: List[str], cache: Optional[ScreenerCache] = None) -> List[QuoteSnapshot]:
    """全量拉取行情（批量接口内部处理指数过滤/分批/解析），写入 cache，返回全部 QuoteSnapshot。"""
    quotes = fetch_tencent_quote_batch(codes)
    snapshots = [_to_snapshot(q) for q in quotes.values()]
    if cache is not None:
        cache.upsert_quotes(snapshots)
    return snapshots


def build_candidate_pool(quotes: List[QuoteSnapshot]) -> List[QuoteSnapshot]:
    """漏斗①（基础）：行情可用性筛选——有价格、总股本、市值可算的股票。

    注：TTM 股息率 >5% 的数值判定需股息数据（T4 dividend_snapshot），
    本层先剔除无行情/停牌/无市值，产出基础候选池。
    """
    return [
        q for q in quotes
        if q.price is not None and q.price > 0
        and q.total_shares is not None and q.total_shares > 0
        and q.market_cap is not None and q.market_cap > 0
    ]
