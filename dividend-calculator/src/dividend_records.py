"""
分红明细统一解析模块（issue #93 扩展阶段：只新增，不替换）。

单一入口 summarize_dividend_rows：接受东财分红明细行（RPT_SHAREBONUS_DET，
与 sustainability.parse_dividend_rows 相同的 row dict 列表），输出统一
DividendSummary：
  - records                全部分红记录（DividendRecord，按 ex_dividend_date 升序）
  - latest_year            最新完整财年（有年报的最新年份，如 "2025"）
  - fiscal_total_per_10    最新完整财年全部记录 10派合计（含该财年中期分配）
  - ttm_total_per_10       TTM（近12个月按除权日）10派合计
  - source                 数据来源标注（默认 "东财"）

财年判定规则（month == 12 → 年报，其余月份 → 中期分配，#37 M4）的**单一实现**：
位于 sustainability.parse_dividend_rows（与 JS calculator.js parseDividendRecords
同口径）。本模块直接 import 复用该解析函数，不重复实现规则——records /
latest_year 语义与 sustainability 完全一致（含 T5 仅已实施、PRETAX_BONUS_RMB>0、
REPORT_DATE 匹配 \\d{4}-\\d{2}、EX_DIVIDEND_DATE/PLAN_NOTICE_DATE 取前10位、
NaN/空防护），label 为 "YYYY年报"/"YYYY中期分配"（NOT "半年报"）。

金额口径：fiscal_total_per_10 / ttm_total_per_10 均为「10派合计」（每10股派息
金额之和，不含总股本）。换算为总金额（× total_shares / 10）留给消费方。
TTM 窗口与 utils.compute_ttm_dividend 一致：近365天（cutoff < ex_date <= as_of）。
"""
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import List, Optional

from .datasource.base import DividendRecord
from .sustainability import parse_dividend_rows

_YEAR_RE = re.compile(r"(\d{4})")


@dataclass
class DividendSummary:
    """分红明细统一汇总（见模块 docstring 字段说明）。"""
    records: List[DividendRecord]   # 全部分红记录（按 ex_dividend_date 升序）
    latest_year: Optional[str]      # 最新完整财年（有年报的最新年份，如 "2025"）
    fiscal_total_per_10: float      # 最新完整财年全部记录 10派合计
    ttm_total_per_10: float         # TTM（近12个月按除权日）10派合计
    source: str                     # 数据来源标注，如 "东财"


def _ttm_per_10(records: List[DividendRecord], as_of_date) -> float:
    """近365天（按除权日）10派合计 —— 与 utils.compute_ttm_dividend 同窗口语义。

    仅返回 10派合计（不含总股本）；窗口内无记录返回 0.0。
    """
    as_of = as_of_date or date.today()
    cutoff = as_of - timedelta(days=365)
    total = 0.0
    for rec in records:
        ex_date = getattr(rec, "ex_dividend_date", None)
        if not ex_date:
            continue
        try:
            d = date.fromisoformat(str(ex_date)[:10])
        except ValueError:
            continue
        if cutoff < d <= as_of:
            total += float(rec.dividend_per_10)
    return total


def summarize_dividend_rows(rows: Optional[List[dict]] = None, source: str = "东财",
                            as_of_date=None) -> DividendSummary:
    """东财分红明细行 → DividendSummary。

    解析语义与 sustainability.parse_dividend_rows 完全一致（直接复用该函数，
    财年判定规则单一实现见模块 docstring）；本函数在其上叠加
    latest_year 财年 10派合计 与 TTM 10派合计。rows=None（取数失败）按空处理。
    """
    records, latest_year = parse_dividend_rows(rows or [])  # None（取数失败）按空处理

    fiscal_total = 0.0
    if latest_year:
        for rec in records:
            m = _YEAR_RE.match(rec.report_time or "")
            if m and m.group(1) == latest_year:
                fiscal_total += rec.dividend_per_10

    return DividendSummary(
        records=sorted(records, key=lambda r: r.ex_dividend_date),
        latest_year=latest_year,
        fiscal_total_per_10=fiscal_total,
        ttm_total_per_10=_ttm_per_10(records, as_of_date),
        source=source,
    )


__all__ = ["DividendSummary", "summarize_dividend_rows"]
