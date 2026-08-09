"""腾讯行情数据解析模块。

统一解析 qt.gtimg.cn 返回的 ~ 分隔格式，隐藏 HTTP 请求和字段索引细节。
所有调用方通过 fetch_tencent_quote() 获取一次解析好的 TencentQuote，
避免各处各自发请求、各自维护字段索引。
"""

import logging
import re
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# 腾讯行情接口字段索引常量（方便查阅，不暴露给外部）
_FIELD_NAME = 1          # 股票名称
_FIELD_PRICE = 3         # 最新价格
_FIELD_PE_TTM = 39       # 市盈率（TTM）——字段 39（33 是当日最高价，勿混）
_FIELD_PB = 46           # 市净率
_FIELD_A_SHARES = 72     # A股股本（仅A股）
_FIELD_TOTAL_SHARES = 73 # 总股本（含A+H等全部股份）


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
        prefix = "sh" if stock_code.startswith("6") else "sz"
        url = f"https://qt.gtimg.cn/q={prefix}{stock_code}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        resp = requests.get(url, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            logger.debug("腾讯行情请求失败 %s: HTTP %s", stock_code, resp.status_code)
            return None

        match = re.search(r'"([^"]+)"', resp.text)
        if not match:
            logger.debug("腾讯行情解析失败 %s: 未找到引号内容", stock_code)
            return None

        fields = match.group(1).split("~")
        if len(fields) < 4:
            logger.debug("腾讯行情字段不足 %s: 仅 %d 个字段", stock_code, len(fields))
            return None

        return TencentQuote(
            stock_code=stock_code,
            name=_safe_str(fields, _FIELD_NAME),
            price=_safe_float(fields, _FIELD_PRICE),
            pe_ttm=_safe_float(fields, _FIELD_PE_TTM),
            pb=_safe_float(fields, _FIELD_PB),
            total_shares=_safe_float(fields, _FIELD_TOTAL_SHARES),
            a_shares=_safe_float(fields, _FIELD_A_SHARES),
        )
    except Exception as e:
        logger.debug("腾讯行情获取失败 %s: %s", stock_code, e)
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
