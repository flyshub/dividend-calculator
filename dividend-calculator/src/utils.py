"""
公共工具模块 - 统一股票代码标准化、分红解析等公共逻辑
"""
import re
import logging
from dataclasses import dataclass
from typing import Optional, List, Tuple

import pandas as pd

from .datasource.base import StockInfo, DividendDetail

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


# ── 分红解析公共逻辑 ──────────────────────────────────────────

def extract_report_year(text) -> Optional[int]:
    """从报告时间文本中提取年份"""
    if pd.isna(text):
        return None
    text = str(text)
    for i in range(len(text) - 3):
        if text[i:i + 4].isdigit():
            return int(text[i:i + 4])
    return None


def is_annual_report(text) -> bool:
    """判断是否为年报（排除半年报/中报）"""
    if pd.isna(text):
        return False
    text = str(text)
    return "年报" in text and "半年报" not in text and "中报" not in text


def extract_dividend_per_10(text) -> float:
    """
    从分红方案说明中提取每10股派息金额

    支持格式：
    - "10派2.5元"
    - "10送2派1.5元"  -> 取派的部分
    - "派1.5元"
    """
    if pd.isna(text):
        return 0.0
    text = str(text)
    # 优先匹配 "10派X" 格式
    match = re.search(r"10派[^0-9]*([0-9.]+)", text)
    if match:
        return float(match.group(1))
    # 回退：匹配 "派X" 格式
    match = re.search(r"派[^0-9]*([0-9.]+)", text)
    if match:
        return float(match.group(1))
    return 0.0


def parse_dividend_df(
    dividend_df: pd.DataFrame,
    stock_info: StockInfo,
    report_col: str = "报告时间",
    scheme_col: str = "分红方案说明",
    payout_col: Optional[str] = None,
) -> Tuple[float, Optional[str], List[DividendDetail], str]:
    """
    解析分红 DataFrame 的公共逻辑

    统一处理：提取年份、判断年报、汇总同年度分红、计算总额

    Args:
        dividend_df: 原始分红数据
        stock_info: 股票基本信息（用于计算总分红）
        report_col: 报告时间列名
        scheme_col: 分红方案说明列名
        payout_col: 直接派息比例列名（可选，优先使用）

    Returns:
        (总分红金额, 年份, 分红明细, 说明)
    """
    if dividend_df.empty:
        return 0.0, None, [], "无分红数据"

    # 提取年份
    dividend_df = dividend_df.copy()
    dividend_df["report_year"] = dividend_df[report_col].apply(extract_report_year)
    dividend_df["is_annual"] = dividend_df[report_col].apply(is_annual_report)

    # 提取每10股分红金额
    if payout_col and payout_col in dividend_df.columns:
        # 优先使用直接的派息比例列（数值型，最准确）
        dividend_df["dividend_per_10"] = pd.to_numeric(
            dividend_df[payout_col], errors="coerce"
        ).fillna(0.0)
    elif scheme_col and scheme_col in dividend_df.columns:
        # 从分红方案说明文本中解析
        dividend_df["dividend_per_10"] = dividend_df[scheme_col].apply(
            extract_dividend_per_10
        )
    else:
        return 0.0, None, [], "无法提取分红金额：缺少派息比例列和分红方案说明列"

    # 过滤无效记录
    dividend_df = dividend_df.dropna(subset=["report_year", "dividend_per_10"])
    dividend_df = dividend_df[dividend_df["dividend_per_10"] > 0.0].copy()

    if dividend_df.empty:
        return 0.0, None, [], "无有效分红数据"

    # 按年份分组，优先选择有年报的最近年度
    yearly_totals = []
    for year in sorted(dividend_df["report_year"].unique(), reverse=True):
        year_records = dividend_df[dividend_df["report_year"] == year]
        total_for_year = year_records["dividend_per_10"].sum()
        has_annual = year_records["is_annual"].any()
        yearly_totals.append((year, total_for_year, year_records, has_annual))

    if not yearly_totals:
        return 0.0, None, [], "无有效分红数据"

    # 选择最近的完整年度（必须有年报）
    target_year = None
    total_dividend_per_10 = 0.0
    target_dividends = None
    for year, total, records, has_annual in yearly_totals:
        if has_annual:
            target_year = year
            total_dividend_per_10 = total
            target_dividends = records
            break

    # 如果没有找到有年报的年份，回退到最近年份
    if target_year is None:
        target_year, total_dividend_per_10, target_dividends, _ = yearly_totals[0]
        logger.warning("未找到有年报的年份，回退到最近年份: %s", target_year)

    if target_dividends.empty:
        return 0.0, None, [], f"{target_year}年无分红数据"

    # 构建分红明细
    dividend_details = []
    total_dividend_per_10 = 0.0
    for _, row in target_dividends.iterrows():
        dp10 = float(row["dividend_per_10"])
        total_dividend_per_10 += dp10
        dividend_details.append(
            DividendDetail(str(row[report_col]), dp10)
        )

    # 计算总分红
    dps = total_dividend_per_10 / 10.0
    total_shares = stock_info.total_shares
    total_dividend = dps * total_shares

    dividend_list = [
        f"{d.report_time}: 10派{d.dividend_per_10}元" for d in dividend_details
    ]
    explanation = (
        f"{target_year}年度 {', '.join(dividend_list)}, "
        f"合计10派{total_dividend_per_10:.3f}元(每股{dps:.4f}元), "
        f"总股本{total_shares / 1e8:.2f}亿股, "
        f"总分红{total_dividend / 1e8:.2f}亿元"
    )

    return total_dividend, str(int(target_year)), dividend_details, explanation


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
