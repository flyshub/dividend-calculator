"""腾讯行情/K线取数模块（ADR-0002）。

统一封装 qt.gtimg.cn（行情）与 web.ifzq.gtimg.cn（fqkline K线）：
- 单股/批量行情解析（~ 分隔 wire 格式、字段索引、v_<code> 标签）
- 前复权 K 线原始行取数（月K/日K 等，供走势图与涨跌幅计算）

所有调用方通过公开函数获取数据，不直接碰字段索引与线格式：
fetch_tencent_quote / fetch_tencent_quote_batch / fetch_kline_rows
"""
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# 腾讯行情接口字段索引常量（不暴露给外部；改字段务必同步单股/批量两处使用点）
_FIELD_NAME = 1          # 股票名称
_FIELD_PRICE = 3         # 最新价格
_FIELD_PE_TTM = 39       # 市盈率（TTM）——字段 39（33 是当日最高价，勿混）
_FIELD_PB = 46           # 市净率
_FIELD_A_SHARES = 72     # A股股本（仅A股）
_FIELD_TOTAL_SHARES = 73 # 总股本（含A+H等全部股份）

_QUOTE_URL = "https://qt.gtimg.cn/q="
_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_BATCH_SIZE = 800        # 研究 #63：900 上限，800 安全裕量
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

_SESSION = requests.Session()
_SESSION.headers.update({"User-Agent": _UA})


@dataclass(frozen=True)
class TencentQuote:
    """腾讯行情返回的一支股票的全部可用字段。

    所有字段均可为 None——接口不保证每个字段都有值。
    """

    stock_code: str
    name: Optional[str] = None
    price: Optional[float] = None
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    total_shares: Optional[float] = None    # 总股本（优先，含A+H）
    a_shares: Optional[float] = None        # A股股本（total_shares 不可用时回退）


def _market_prefix(code: str) -> str:
    """市场前缀：6→sh，8/4/92→bj（北交所），其余→sz。"""
    if code.startswith("6"):
        return "sh"
    if code.startswith(("8", "4", "92")):
        return "bj"
    return "sz"


def _is_index_code(code: str, market: str) -> bool:
    """排除指数段：sh000xxx（上证指数）、sz399xxx（深证指数）。

    需 market 前缀区分：000001 是平安银行（sz）非上证指数（sh）。
    """
    if market == "sh":
        return code.startswith("000")
    if market == "sz":
        return code.startswith("399")
    return False


def _parse_quote_fields(stock_code: str, fields: list) -> TencentQuote:
    """把 ~ 分隔的腾讯行情字段解析为 TencentQuote（单股/批量共用）。"""
    return TencentQuote(
        stock_code=stock_code,
        name=_safe_str(fields, _FIELD_NAME),
        price=_safe_float(fields, _FIELD_PRICE),
        pe_ttm=_safe_float(fields, _FIELD_PE_TTM),
        pb=_safe_float(fields, _FIELD_PB),
        total_shares=_safe_float(fields, _FIELD_TOTAL_SHARES),
        a_shares=_safe_float(fields, _FIELD_A_SHARES),
    )


def fetch_tencent_quote(stock_code: str, timeout: int = 10) -> Optional[TencentQuote]:
    """从腾讯行情接口获取一支股票的全部可用数据。

    仅发一次 HTTP 请求，解析全部字段后返回不可变的 TencentQuote。
    调用方按需取用具体字段，无需关心 ~ 分隔的线格式。

    Args:
        stock_code: 6位数字代码
        timeout: HTTP 请求超时秒数

    Returns:
        TencentQuote 或 None（网络错误/解析失败时）
    """
    try:
        url = f"{_QUOTE_URL}{_market_prefix(stock_code)}{stock_code}"
        resp = _SESSION.get(url, timeout=timeout)
        resp.raise_for_status()

        match = re.search(r'"([^"]+)"', resp.text)
        if not match:
            logger.debug("腾讯行情解析失败 %s: 未找到引号内容", stock_code)
            return None

        fields = match.group(1).split("~")
        if len(fields) < 4:
            logger.debug("腾讯行情字段不足 %s: 仅 %d 个字段", stock_code, len(fields))
            return None

        return _parse_quote_fields(stock_code, fields)
    except Exception as e:
        logger.debug("腾讯行情获取失败 %s: %s", stock_code, e)
        return None


def fetch_tencent_quote_batch(
    codes: List[str],
    batch_size: int = _BATCH_SIZE,
) -> Dict[str, TencentQuote]:
    """腾讯批量行情（qt.gtimg.cn/q=），按 v_<code> 标签映射，返回 {code: TencentQuote}。

    语义与既有批量实现一致：
    - 指数代码（sh000xxx / sz399xxx）与无效/退市代码静默跳过
    - price 或 total_shares 无效（<=0/缺失）的条目剔除（停牌股）
    - 单批最多 batch_size 只（接口上限 900，默认 800 安全裕量）
    单个批次请求失败返回空结果（不抛，不影响其他批次）。
    """
    valid = [c for c in codes if not _is_index_code(c, _market_prefix(c))]
    if not valid:
        return {}
    result: Dict[str, TencentQuote] = {}
    for i in range(0, len(valid), batch_size):
        batch = valid[i:i + batch_size]
        url = _QUOTE_URL + ",".join(f"{_market_prefix(c)}{c}" for c in batch)
        try:
            resp = _SESSION.get(url, timeout=(5, 30))
            resp.raise_for_status()
            resp.encoding = "GBK"  # 腾讯行情 GBK 编码
        except Exception:
            logger.debug("腾讯批量行情请求失败，跳过 %d 只", len(batch))
            continue

        for m in re.finditer(r'v_(\w+)="([^"]*)"', resp.text):
            tag = m.group(1)   # 如 sh600900
            code = tag[-6:]
            fields = m.group(2).split("~")
            if len(fields) < 4:
                continue
            quote = _parse_quote_fields(code, fields)
            if quote.price is not None and quote.total_shares is not None:
                result[code] = quote
    return result


def fetch_kline_rows(
    stock_code: str,
    period: str = "month",
    count: int = 120,
    fq: str = "qfq",
) -> Optional[List[list]]:
    """腾讯 fqkline 前复权 K 线原始行（每行 [date, open, close, ...]）。

    取数语义与既有实现一致：data[<prefix><code>][f"qfq{period}"]（qfqmonth/qfqday/qfqweek）。
    请求失败返回 None；请求成功但无数据返回 []。
    """
    try:
        prefix = _market_prefix(stock_code)
        url = f"{_KLINE_URL}?param={prefix}{stock_code},{period},,,{count},{fq}"
        resp = _SESSION.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        key = f"{prefix}{stock_code}"
        rows = (data.get("data") or {}).get(key, {}).get(f"qfq{period}") or []
        return rows
    except Exception as e:
        logger.debug("腾讯K线获取失败 %s: %s", stock_code, e)
        return None


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _safe_float(fields: list[str], idx: int) -> Optional[float]:
    """安全读取字段并转为 float，字段不存在或无效时返回 None。"""
    if idx >= len(fields):
        return None
    try:
        val = float(fields[idx])
        return val if val > 0 else None
    except (ValueError, TypeError):
        return None


def _safe_str(fields: list[str], idx: int) -> Optional[str]:
    """安全读取字段并返回非空字符串，字段不存在或为空时返回 None。"""
    if idx >= len(fields):
        return None
    val = fields[idx]
    return val if val else None
