"""
公共工具模块 - 统一股票代码标准化、分红解析等公共逻辑
"""
import re
import logging
from dataclasses import dataclass
from typing import Optional, List, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


def ensure_6digit(stock_input: str) -> Optional[str]:
    """确保输入是6位数字代码，支持 SH.600987 / 600987.SH / 600987 等格式"""
    code = str(stock_input).strip()
    if '.' in code:
        parts = code.split('.')
        for part in parts:
            if part.isdigit() and len(part) == 6:
                return part
        return None
    if code.isdigit() and len(code) == 6:
        return code
    return None


@dataclass(frozen=True, slots=True)
class FiscalYear:
    """财年推断结果"""
    year: int
    is_annual: bool

    @property
    def report_time(self) -> str:
        """返回 'YYYY年报' 或 'YYYY中报' 格式"""
        label = "年报" if self.is_annual else "中报"
        return f"{self.year}{label}"


def infer_fiscal_year(year: int, month: int) -> FiscalYear:
    """根据除权除息日期推断财年和报告类型

    规则（CLAUDE.md 原则）：
      - 3-8月除权 → 上年度年报（3/4/5/6/7/8月都是年报分红）
      - 9-12月除权 → 当年中报
      - 1-2月除权 → 上年度中报
    """
    if 3 <= month <= 8:
        return FiscalYear(year=year - 1, is_annual=True)
    elif month >= 9:
        return FiscalYear(year=year, is_annual=False)
    else:
        return FiscalYear(year=year - 1, is_annual=False)


# ── 股票列表缓存 ──────────────────────────────────────────────
_stock_list_cache = None


def get_stock_list_cache():
    """获取 A 股列表缓存，懒加载

    注意：需要下载大量数据，可能较慢。
    如果加载失败，返回 None，不影响核心功能。
    股票名称→代码优先使用腾讯智能搜索接口（lookup_stock_code_by_name）。
    """
    global _stock_list_cache
    if _stock_list_cache is None:
        try:
            from .datasource.mootdx_source import get_quotes_client
            client = get_quotes_client()
            # 使用 mootdx 获取全市场股票列表
            import pandas as pd
            df = client.stocks(market=1)  # 上海
            df_sz = client.stocks(market=0)  # 深圳
            if df_sz is not None and not df_sz.empty:
                df = pd.concat([df, df_sz], ignore_index=True)
            if df is not None and not df.empty:
                _stock_list_cache = df
                logger.debug("A股列表缓存已加载，共 %d 条", len(_stock_list_cache))
        except Exception as e:
            logger.warning("加载A股列表缓存失败: %s", e)
    return _stock_list_cache


def lookup_stock_code_by_name(stock_name: str) -> Optional[str]:
    """通过股票名称查询代码（使用腾讯智能搜索接口，快速）

    腾讯搜索接口返回格式：v_hint="sh~600987~航民股份~hmgf~GP-A"
    """
    try:
        import requests
        url = "https://smartbox.gtimg.cn/s3/?q={}&t=all".format(stock_name)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        resp = requests.get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            text = resp.text
            # 格式：v_hint="sh~600987~航民股份~hmgf~GP-A"
            import re
            match = re.search(r'"(sh|sz)~(\d{6})~(.+?)~', text)
            if match:
                code = match.group(2)
                name = match.group(3)
                logger.debug("腾讯搜索: %s -> %s (%s)", stock_name, code, name)
                return code
    except Exception as e:
        logger.debug("腾讯搜索查询失败 %s: %s", stock_name, e)
    return None


def normalize_stock_code(stock_input: str) -> str:
    """
    标准化股票代码，支持股票名称转代码

    Args:
        stock_input: 6位股票代码或精确股票名称

    Returns:
        6位股票代码
    """
    stock_input = str(stock_input).strip()

    if stock_input.isdigit() and len(stock_input) == 6:
        return stock_input

    # 优先使用腾讯搜索（快速，不依赖东方财富）
    code = lookup_stock_code_by_name(stock_input)
    if code is not None:
        return code

    # 回退到 akshare 缓存（较慢，需要下载A股列表）
    cache = get_stock_list_cache()
    if cache is not None:
        match = cache[cache["name"] == stock_input]
        if not match.empty:
            code = str(match.iloc[0]["code"])
            logger.debug("名称 %s -> 代码 %s (akshare缓存)", stock_input, code)
            return code

    logger.warning("无法将输入 %r 解析为股票代码", stock_input)
    return stock_input


def normalize_to_baostock_code(stock_code: str) -> Tuple[Optional[str], str]:
    """
    将6位股票代码转换为 baostock 格式

    Returns:
        (baostock_code, original_6digit_code)
    """
    if "." in stock_code:
        parts = stock_code.split(".")
        if len(parts) == 2:
            return stock_code, parts[1]

    if len(stock_code) == 6 and stock_code.isdigit():
        prefix = "sh" if stock_code.startswith("6") else "sz"
        return f"{prefix}.{stock_code}", stock_code

    return None, stock_code


def compute_ttm_dividend(
    records: List["DividendRecord"],
    total_shares: float,
    as_of_date=None,
) -> Tuple[Optional[float], Optional[str], Optional[str], int]:
    """TTM 股息率口径（#19）：近 12 个月（按除权除息日）实际派发现金分红总额。

    Args:
        records: 分红记录列表（含 ex_dividend_date YYYY-MM-DD）
        total_shares: 总股本（元股）
        as_of_date: 计算基准日（默认今天）；用于测试注入固定日期

    Returns:
        (ttm_total_div, period_start, period_end, count)
        - ttm_total_div: TTM 现金分红总额（元）；无记录返回 None
        - period_start/end: TTM 期间（YYYY-MM-DD）
        - count: 参与计算的派息次数
    """
    from datetime import date, timedelta
    from .datasource.base import DividendRecord  # noqa: F401 类型引用

    as_of = as_of_date or date.today()
    cutoff = as_of - timedelta(days=365)

    total_per_10 = 0.0
    count = 0
    for rec in records:
        ex_date = getattr(rec, "ex_dividend_date", None)
        if not ex_date:
            continue
        # 纯送转锚点行（per10=0，走势图股本锚点用）不算派息，count 不虚增
        if not (float(rec.dividend_per_10) > 0):
            continue
        try:
            d = date.fromisoformat(str(ex_date)[:10])
        except ValueError:
            continue
        if cutoff < d <= as_of:
            total_per_10 += float(rec.dividend_per_10)
            count += 1

    if count == 0:
        return None, None, None, 0

    ttm_total = total_per_10 / 10.0 * total_shares
    return ttm_total, cutoff.isoformat(), as_of.isoformat(), count
