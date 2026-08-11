"""
分红明细统一解析模块（issue #93 扩展阶段 + issue #97 主链路迁移）。

单一入口 summarize_dividend_rows：接受东财分红明细行（RPT_SHAREBONUS_DET，
与 sustainability.parse_dividend_rows 相同的 row dict 列表），输出统一
DividendSummary：
  - records                全部分红记录（DividendRecord，按 ex_dividend_date 升序）
  - latest_year            最新完整财年（有年报的最新年份，如 "2025"）
  - fiscal_total_per_10    最新完整财年全部记录 10派合计（含该财年中期分配）
  - ttm_total_per_10       TTM（近12个月按除权日）10派合计
  - source                 数据来源标注（默认 "东财"）

issue #97 起本模块成为主股息率/TTM 链路的唯一解析口径，新增各源 adapter：
  - summarize_fhps_df   akshare stock_fhps_detail_em DataFrame → DividendSummary
  - summarize_cninfo_df akshare stock_dividend_cninfo DataFrame → DividendSummary
各 adapter 只做「来源列名 → 统一 row dict」格式转换与来源特有过滤，然后统一交给
sustainability.parse_dividend_rows 解析（财年判定、NaN 防护、label 生成均在其内），
不允许 adapter 各自实现财年判定。

财年判定规则（month == 12 → 年报，其余月份 → 中期分配，#37 M4）的**单一实现**：
位于 sustainability.classify_fiscal_report（parse_dividend_rows 与本模块各
adapter 均调用它；与 JS calculator.js parseDividendRecords 同口径）。本模块
不重复实现规则——records / latest_year 语义与 sustainability 完全一致
（含 T5 仅已实施、PRETAX_BONUS_RMB>0、REPORT_DATE 匹配 \\d{4}-\\d{2}、
EX_DIVIDEND_DATE/PLAN_NOTICE_DATE 取前10位、NaN/空防护），label 为
"YYYY年报"/"YYYY中期分配"（NOT "半年报"）。

金额口径：fiscal_total_per_10 / ttm_total_per_10 均为「10派合计」（每10股派息
金额之和，不含总股本）。换算为总金额（× total_shares / 10）留给消费方。
TTM 窗口与 utils.compute_ttm_dividend 一致：近365天（cutoff < ex_date <= as_of）。
"""
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import List, Optional

import pandas as pd

from .datasource.base import DividendRecord
from .sustainability import classify_fiscal_report, parse_dividend_rows

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


def _fiscal_total_per_10(records: List[DividendRecord], latest_year: Optional[str]) -> float:
    """最新完整财年全部记录 10派合计（含该财年中期分配）；无财年返回 0.0。"""
    total = 0.0
    if latest_year:
        for rec in records:
            m = _YEAR_RE.match(rec.report_time or "")
            if m and m.group(1) == latest_year:
                total += rec.dividend_per_10
    return total


def _summarize(records: List[DividendRecord], latest_year: Optional[str],
               source: str, as_of_date) -> DividendSummary:
    """records + 财年 → DividendSummary（汇总公共逻辑，各 adapter 共用）。"""
    return DividendSummary(
        records=sorted(records, key=lambda r: r.ex_dividend_date),
        latest_year=latest_year,
        fiscal_total_per_10=_fiscal_total_per_10(records, latest_year),
        ttm_total_per_10=_ttm_per_10(records, as_of_date),
        source=source,
    )


def summarize_dividend_rows(rows: Optional[List[dict]] = None, source: str = "东财",
                            as_of_date=None) -> DividendSummary:
    """东财分红明细行 → DividendSummary。

    解析语义与 sustainability.parse_dividend_rows 完全一致（直接复用该函数，
    财年判定规则单一实现见模块 docstring）；本函数在其上叠加
    latest_year 财年 10派合计 与 TTM 10派合计。rows=None（取数失败）按空处理。
    """
    records, latest_year = parse_dividend_rows(rows or [])  # None（取数失败）按空处理
    return _summarize(records, latest_year, source, as_of_date)


# ---------------------------------------------------------------------------
# 各源 adapter（issue #97）：仅做格式转换 → 统一交给 parse_dividend_rows
# ---------------------------------------------------------------------------

def _date_str(value) -> str:
    """日期值 → "YYYY-MM-DD"；None/NaN/NaT/空 → ""（与 parse_dividend_rows 取前10位同语义）。"""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        return ""
    return str(value)[:10]


def _fhps_report_date(value) -> Optional[str]:
    """fhps 报告期 → "YYYY-MM-DD"（parse_dividend_rows 只取年月，日补 01）；无法解析 → None。

    兼容 datetime.date/datetime/Timestamp 与 "YYYY-MM-DD" 字符串（_parse_fhps_detail 同语义）。
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        return None
    if isinstance(value, (datetime, date)):
        return f"{value.year:04d}-{value.month:02d}-{value.day:02d}"
    s = str(value).strip()
    m = re.match(r"(\d{4})-(\d{1,2})(?:-(\d{1,2}))?", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3) or 1):02d}"
    return None


def summarize_fhps_df(fhps_df=None, source: str = "akshare fhps_detail_em",
                      as_of_date=None) -> DividendSummary:
    """akshare stock_fhps_detail_em DataFrame → DividendSummary。

    过滤语义与旧 dividend._parse_fhps_detail 完全一致：现金分红比例 notna 且 > 0、
    方案进度含「实施」且不含「未实施/停止/否决/预披露」；报告期取年月后统一交给
    parse_dividend_rows 判定财年（classify_fiscal_report 单一实现，month==12 → 年报）。
    记录含除权除息日/预案公告日（可空串），供 TTM 与走势图使用。
    """
    if fhps_df is None or fhps_df.empty:
        return _summarize([], None, source, as_of_date)

    # 过滤语义与 dividend._parse_fhps_detail 逐条一致（排除一切未落地预案）
    valid = fhps_df[
        fhps_df['现金分红-现金分红比例'].notna()
        & (fhps_df['现金分红-现金分红比例'] > 0)
        & fhps_df['方案进度'].astype(str).str.contains('实施')
        & ~fhps_df['方案进度'].astype(str).str.contains('未实施')
        & ~fhps_df['方案进度'].astype(str).str.contains('停止')
        & ~fhps_df['方案进度'].astype(str).str.contains('否决')
        & ~fhps_df['方案进度'].astype(str).str.contains('预披露')
    ]

    rows = []
    for _, row in valid.iterrows():
        report_date = _fhps_report_date(row['报告期'])
        if report_date is None:
            continue
        rows.append({
            "REPORT_DATE": report_date,
            "PRETAX_BONUS_RMB": float(row['现金分红-现金分红比例']),
            # 已按 fhps 过滤语义过滤（含"实施"且排除未实施/停止/否决/预披露），
            # 置 "实施" 让 parse_dividend_rows 的 T5 检查直接通过，不二次变弱
            "ASSIGN_PROGRESS": "实施",
            "EX_DIVIDEND_DATE": _date_str(row.get('除权除息日')),
            "PLAN_NOTICE_DATE": _date_str(row.get('预案公告日')),
        })

    records, latest_year = parse_dividend_rows(rows)
    return _summarize(records, latest_year, source, as_of_date)


def _cninfo_report_date(text) -> Optional[str]:
    """cninfo 报告时间文本 → "YYYY-MM-DD"（供 parse_dividend_rows 单一财年判定）。

    实测格式（600036/600900 实地验证）："2024年报" / "2025半年报" / "2025三季报"；
    兼容日期格式（"2025-06-30"）。文本映射月份：年报 → 12，半年报/中报/季报/
    无法判定 → 6（非 12 → 中期分配，与旧 utils.is_annual_report 语义一致）。
    无年份 → None（跳过）。
    """
    if text is None:
        return None
    try:
        if pd.isna(text):
            return None
    except (TypeError, ValueError):
        return None
    s = str(text).strip()
    m = re.search(r"(\d{4})-(\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-01"
    m = re.search(r"(\d{4})", s)
    if not m:
        return None
    year = int(m.group(1))
    if "年报" in s and "半年报" not in s and "中报" not in s:
        month = 12
    else:
        month = 6  # 半年报/中报/季报 → 中期分配（NOT 年报）
    return f"{year:04d}-{month:02d}-01"


def summarize_cninfo_df(dividend_df=None, source: str = "akshare cninfo",
                        as_of_date=None) -> DividendSummary:
    """akshare stock_dividend_cninfo DataFrame → DividendSummary。

    语义与旧 utils.parse_dividend_df（report_col="报告时间"、scheme_col=
    "实施方案分红说明"、payout_col="派息比例"）一致：派息比例 > 0 才计入现金分红；
    报告时间取年份 + 月份判定统一走 parse_dividend_rows 单一实现（文本"年报"字样
    → 12 月，与旧 is_annual_report 语义对齐；"半年报/中报/三季报" → 中期分配）。
    记录含除权日（除权日列，可空串），供 TTM 使用。
    """
    if dividend_df is None or dividend_df.empty:
        return _summarize([], None, source, as_of_date)

    rows = []
    for _, row in dividend_df.iterrows():
        report_date = _cninfo_report_date(row.get("报告时间"))
        if report_date is None:
            continue
        try:
            dp10 = float(row.get("派息比例"))
        except (TypeError, ValueError):
            dp10 = float("nan")  # 与旧 parse_dividend_df 的 to_numeric(errors="coerce") 同语义
        if dp10 != dp10 or dp10 <= 0:  # NaN 防护（股改分红等无派息记录）
            continue
        rows.append({
            "REPORT_DATE": report_date,
            "PRETAX_BONUS_RMB": float(dp10),
            "ASSIGN_PROGRESS": "实施",  # cninfo 历史分红均为已实施方案
            "EX_DIVIDEND_DATE": _date_str(row.get("除权日")),
        })

    records, latest_year = parse_dividend_rows(rows)
    return _summarize(records, latest_year, source, as_of_date)


__all__ = [
    "DividendSummary",
    "summarize_dividend_rows",
    "summarize_fhps_df",
    "summarize_cninfo_df",
]
