"""选股器行情快照（spec #67，工单 #70）。

四级漏斗的第一层：全 A 股票列表 → 腾讯批量行情 → quote_snapshot。

腾讯批量主通道（研究 #63 结论）：
- qt.gtimg.cn/q= 单次最多 900 只（推荐 800），实测无限流
- 批量响应与单股同构（v_<code> 标签关联，非请求顺序）
- 字段索引与 tencent_quote.py 一致（PE=33、PB=46、总股本=73）
- 无效/退市代码静默跳过 → 必须按 v_<code> 建 dict
- 指数代码（sh000xxx/sz399xxx）污染字段 → 排除
"""
import re
from typing import Dict, List, Optional

import requests

from src.screener_cache import QuoteSnapshot, ScreenerCache
from src.tencent_quote import _FIELD_NAME, _FIELD_PE_TTM, _FIELD_PB, \
    _FIELD_PRICE, _FIELD_TOTAL_SHARES, _safe_float, _safe_str

_BATCH_SIZE = 800  # 研究 #63：900 上限，800 安全裕量
_QUOTE_URL = "https://qt.gtimg.cn/q="
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": _UA})


def _prefix(code: str) -> str:
    return "sh" if code.startswith("6") else "sz"


def _is_index_code(code: str, market: str) -> bool:
    """排除指数段：sh000xxx（上证指数）、sz399xxx（深证指数）。

    需 market 前缀区分：000001 是平安银行（sz）非上证指数（sh）。
    """
    if market == "sh":
        return code.startswith("000")
    if market == "sz":
        return code.startswith("399")
    return False


def _market(code: str) -> str:
    return "sh" if code.startswith("6") else "sz"


def fetch_quote_batch(codes: List[str]) -> Dict[str, QuoteSnapshot]:
    """腾讯批量拉取行情（一批）。返回 {code: QuoteSnapshot}，按 v_<code> 标签映射。"""
    if not codes:
        return {}
    # 过滤指数/无效代码（需 market 前缀判断）
    valid = [c for c in codes if not _is_index_code(c, _market(c))]
    if not valid:
        return {}

    url = _QUOTE_URL + ",".join(f"{_prefix(c)}{c}" for c in valid)
    try:
        resp = _SESSION.get(url, timeout=(5, 30))
        resp.raise_for_status()
        resp.encoding = "GBK"  # 腾讯行情 GBK 编码
    except Exception:
        return {}

    # 按 v_<code>="<fields>" 提取每条
    result: Dict[str, QuoteSnapshot] = {}
    for m in re.finditer(r'v_(\w+)="([^"]*)"', resp.text):
        tag = m.group(1)   # 如 sh600900
        body = m.group(2)
        code = tag[-6:]
        fields = body.split("~")
        if len(fields) < 4:
            continue
        price = _safe_float(fields, _FIELD_PRICE)
        total_shares = _safe_float(fields, _FIELD_TOTAL_SHARES)
        if price is None or total_shares is None:
            continue
        result[code] = QuoteSnapshot(
            code=code,
            name=_safe_str(fields, _FIELD_NAME),
            price=price,
            pe_ttm=_safe_float(fields, _FIELD_PE_TTM),
            pb=_safe_float(fields, _FIELD_PB),
            total_shares=total_shares,
            market_cap=price * total_shares,
            quote_time="",
            source="腾讯批量",
        )
    return result


def fetch_all_quotes(codes: List[str], cache: Optional[ScreenerCache] = None) -> List[QuoteSnapshot]:
    """全量拉取行情（分批），写入 cache，返回全部 QuoteSnapshot。"""
    all_quotes: List[QuoteSnapshot] = []
    valid = [c for c in codes if not _is_index_code(c, _market(c))]
    for i in range(0, len(valid), _BATCH_SIZE):
        batch = valid[i:i + _BATCH_SIZE]
        quotes = fetch_quote_batch(batch)
        all_quotes.extend(quotes.values())
    if cache is not None:
        cache.upsert_quotes(all_quotes)
    return all_quotes


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
