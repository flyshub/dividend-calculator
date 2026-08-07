"""
股息可持续性 — 数据获取层

全部走东方财富 datacenter HTTP 接口（与 site/js/datasources.js 同源），不依赖 mootdx：
  - 财务：RPT_F10_FINANCE_MAINFINADATA（columns=ALL，含现金流/负债/银行专项）
  - 分红明细：RPT_SHAREBONUS_DET（全历史除权记录）
  - 行业：RPT_F10_BASIC_ORGINFO（EM2016 东财行业）

设计理由：
  1. 东财 HTTP 全球可用，mootdx 通达信协议在部分环境受限；
  2. 与 JS 端同源，verify_js_vs_python.py 双端一致性校验天然对齐；
  3. 模块自洽：可持续性评估所需数据全部内聚取数，不耦合 pr.py/dividend.py 的 mootdx 路径。

字段名来自对 600900/600036 的实地验证，单位均为元、比率为百分数。严禁虚构数据。
"""
import dataclasses
import logging
import re
from typing import List, Optional, Tuple

import requests

from .datasource.base import DividendRecord
from .sustainability_calculator import (
    AnnualFinancial,
    CUT_WINDOW_YEARS,
    DividendHistory,
    SustainabilityResult,
    assess_sustainability,
)

logger = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 东财财务接口（host: datacenter.eastmoney.com）
_FINANCE_URL = (
    "https://datacenter.eastmoney.com/api/data/v1/get"
    "?sortColumns=REPORT_DATE&sortTypes=-1&pageSize=100&pageNumber=1"
    "&reportName=RPT_F10_FINANCE_MAINFINADATA&columns=ALL"
    '&filter=(SECUCODE%3D"{secucode}")'
)

# 东财分红明细接口（host: datacenter-web.eastmoney.com，与 JS 同源）
_DIVIDEND_URL = (
    "https://datacenter-web.eastmoney.com/api/data/v1/get"
    "?sortColumns=REPORT_DATE&sortTypes=-1&pageSize=100&pageNumber=1"
    "&reportName=RPT_SHAREBONUS_DET&columns=ALL"
    '&filter=(SECURITY_CODE%3D"{code}")'
)

# 东财行业接口
_INDUSTRY_URL = (
    "https://datacenter-web.eastmoney.com/api/data/v1/get"
    "?reportName=RPT_F10_BASIC_ORGINFO&columns=ALL"
    '&filter=(SECUCODE%3D"{secucode}")'
)

# 东财现金流量表接口（含真正的资本开支 CONSTRUCT_LONG_ASSET；MAINFINADATA 无此字段）
_CASHFLOW_URL = (
    "https://datacenter.eastmoney.com/api/data/v1/get"
    "?sortColumns=REPORT_DATE&sortTypes=-1&pageSize=100&pageNumber=1"
    "&reportName=RPT_F10_FINANCE_GCASHFLOW&columns=ALL"
    '&filter=(SECUCODE%3D"{secucode}")'
)

# 东财财务字段名 → AnnualFinancial 语义（实地验证 600036 招行/600887 伊利确认真实存在）
# 注意：东财字段命名有坑——NON_PERFORMING_LOAN 是"不良贷款余额"(元)非比率，
#       RISK_COVERAGE 恒为 None，DEBT_ASSET_RATIO/ADEQUACY_RATIO 不存在。
_FIELD_MAP = {
    "PARENTNETPROFIT": "net_profit",
    "PARENTNETPROFITTZ": "net_profit_yoy",
    "NETCASH_OPERATE_PK": "operating_cf",
    "NETCASH_INVEST_PK": "investing_cf",
    "TOTAL_ASSETS_PK": "total_assets",
    "LIABILITY": "total_liabilities",
    # debt_ratio 无直接字段，靠 AnnualFinancial.debt_ratio_decimal() 用 LIABILITY/TOTAL_ASSETS_PK 推算
    "INTEREST_DEBT_RATIO": "interest_debt_ratio",
    "INTEREST_COVERAGE_RATIO": "interest_coverage",
    "ROEJQ": "roe",
    "NEWCAPITALADER": "capital_adequacy_ratio",   # 总资本充足率（监管红线8%；非FIRST_ADEQUACY_RATIO一级口径）
    "NET_INTEREST_MARGIN": "net_interest_margin",
    "NONPERLOAN": "npl_ratio",                    # 不良贷款率（%；非NON_PERFORMING_LOAN余额）
    "LOAN_PROVISION_RATIO": "provision_coverage",  # 拨贷比（%；非RISK_COVERAGE恒空）
}


def _to_float(value) -> Optional[float]:
    """严格数值转换：空字符串/null/None 视为缺失（避免空串被解析为 0 污染）。"""
    if value is None:
        return None
    s = str(value).strip()
    if s == "" or s.lower() == "none":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# 财务解析（纯函数，可被 verify 复用）
# ---------------------------------------------------------------------------

def parse_financial_rows(rows: List[dict]) -> List[AnnualFinancial]:
    """解析东财财务行 → AnnualFinancial 列表，仅保留年报（12-31）行，按报告期降序。

    只保留年报行的原因：东财 MAINFINADATA 把年报与各季报累计值混在一起，
    若不滤掉季报，select_latest_annual 会取到季报累计净利润，与分红总额
    （某完整财年）错配，导致支付率/覆盖率虚高失真。
    """
    result: List[AnnualFinancial] = []
    for row in rows:
        date_str = str(row.get("REPORT_DATE") or "")[:10]  # YYYY-MM-DD
        if len(date_str) < 10 or date_str[5:10] != "12-31":
            continue  # 仅保留年报行
        year = int(date_str[:4])
        kwargs = {"year": year}
        for src_field, dst_field in _FIELD_MAP.items():
            kwargs[dst_field] = _to_float(row.get(src_field))
        result.append(AnnualFinancial(**kwargs))
    return result


def select_latest_annual(financials: List[AnnualFinancial],
                         target_year: Optional[str] = None) -> Optional[AnnualFinancial]:
    """选出年报行，优先匹配分红所属财年。

    parse_financial_rows 已只保留 12-31 年报行（过滤掉季报累计值），
    避免与分红总额（某完整财年）错配导致支付率虚高。

    target_year: 分红所属财年字符串（如 '2025'）。优先返回该年年报；无匹配则返回最新年报。
    """
    if target_year:
        for fin in financials:  # 已降序
            if str(fin.year) == str(target_year):
                return fin
    for fin in financials:
        if fin.net_profit is not None or fin.operating_cf is not None:
            return fin
    return financials[0] if financials else None


# ---------------------------------------------------------------------------
# 分红明细解析 → DividendRecord（对齐 JS parseDividendRecords 的口径）
# ---------------------------------------------------------------------------

def _is_implemented(progress: str) -> bool:
    """东财分红方案进度是否已实施落地（排除预案/预披露/批准/未实施等）。

    实际值含"实施分配"等。判定：包含"实施"且不含"未实施"。
    """
    return "实施" in progress and "未实施" not in progress


def parse_dividend_rows(rows: List[dict]) -> Tuple[List[DividendRecord], Optional[str]]:
    """解析东财分红明细行 → DividendRecord 列表 + 最新有年报的财年字符串。

    纯函数，与 JS calculator.parseDividendRecords 同口径：
      - 仅保留已实施分红（_is_implemented，T5）
      - 仅保留 PRETAX_BONUS_RMB > 0 的现金分红
      - report_time 取报告期年份 + '年报'/'半年报'
    返回: (records, latest_year_str)
    """
    yearly: dict = {}  # {year: {'total': dp10合计, 'has_annual': bool}}
    records: List[DividendRecord] = []
    date_re = re.compile(r"(\d{4})-(\d{2})")

    for row in rows:
        progress = str(row.get("ASSIGN_PROGRESS") or "")
        # T5：仅保留已实施分红（含"实施"但排除"未实施"/"预案"/"预披露"/"批准"等未落地状态）
        if not _is_implemented(progress):
            continue
        dp10 = _to_float(row.get("PRETAX_BONUS_RMB"))
        if dp10 is None or dp10 <= 0:
            continue
        report_date = str(row.get("REPORT_DATE") or "")
        m = date_re.match(report_date)
        if not m:
            continue
        year = int(m.group(1))
        month = int(m.group(2))
        # 与 JS 一致：12/3/4月为年报，6/9月为半年报
        is_annual = month not in (6, 9)
        label = f"{year}年报" if is_annual else f"{year}半年报"

        ex_date = str(row.get("EX_DIVIDEND_DATE") or "")[:10]
        records.append(DividendRecord(
            ex_dividend_date=ex_date,
            dividend_per_10=dp10,
            report_time=label,
        ))

        if year not in yearly:
            yearly[year] = {"total": 0.0, "has_annual": False}
        yearly[year]["total"] += dp10
        yearly[year]["has_annual"] = yearly[year]["has_annual"] or is_annual

    # 最新有年报的财年（降序找第一个 has_annual）
    latest_year = None
    for y in sorted(yearly.keys(), reverse=True):
        if yearly[y]["has_annual"]:
            latest_year = str(y)
            break

    return records, latest_year


# ---------------------------------------------------------------------------
# 历史聚合（纯函数）
# ---------------------------------------------------------------------------

def aggregate_dividend_history(records: List[DividendRecord],
                               latest_year: Optional[str],
                               total_shares: float) -> DividendHistory:
    """聚合分红记录 → DividendHistory（连续年数 / 是否曾削减 / 均值）。

    连续年数：从最新年向前连续递减计数，遇中断即停。
    曾削减：历史任意年分红额 < 前一年 ×0.7 视为明显削减。
    """
    if not records:
        return DividendHistory(consecutive_years=0, ever_cut=False,
                               latest_year_amount=None, history_mean_amount=None)

    # 按财年聚合分红总额（元）
    year_amount: dict = {}
    for rec in records:
        ym = re.match(r"(\d{4})", rec.report_time or "")
        if not ym:
            continue
        year = ym.group(1)
        amount = rec.dividend_per_10 / 10.0 * total_shares
        year_amount[year] = year_amount.get(year, 0.0) + amount

    if not year_amount:
        return DividendHistory(consecutive_years=0, ever_cut=False,
                               latest_year_amount=None, history_mean_amount=None)

    years_sorted = sorted(year_amount.keys(), reverse=True)
    target_year = latest_year if (latest_year and latest_year in year_amount) else years_sorted[0]

    # 连续年数：从 target_year 向前逐年递减
    consecutive = 0
    try:
        y = int(target_year)
        while str(y) in year_amount:
            consecutive += 1
            y -= 1
    except ValueError:
        pass

    history_years = [yy for yy in years_sorted if yy != target_year]
    history_mean = None
    if history_years:
        history_mean = sum(year_amount[yy] for yy in history_years) / len(history_years)

    # 近3年均值（target_year 之前最近的3年）——突击分红判断用，避免早期低基数拉低全历史均值
    # 导致稳定增长股被误判为"突兀"（如伊利逐年提升分红，全历史均值偏低）
    try:
        tgt_int = int(target_year)
        recent3 = [yy for yy in years_sorted if yy != target_year and int(yy) < tgt_int][:3]
    except ValueError:
        recent3 = history_years[:3]
    history_3y_mean = None
    if recent3:
        history_3y_mean = sum(year_amount[yy] for yy in recent3) / len(recent3)

    ever_cut = False
    # 近 CUT_WINDOW_YEARS 年窗口（含最新财年）内相邻年分红降幅 > 30% 视为曾削减。
    # 窗口之外的久远波动（如行业早期调整）对当前分红可持续性无参考价值，
    # 避免连年提升分红的股票（如伊利 2016~2025 逐年递增）被早期低基数误判。
    window_start = int(target_year) - (CUT_WINDOW_YEARS - 1)
    asc = sorted(year_amount.keys())
    for i in range(1, len(asc)):
        prev_y, cur_y = asc[i - 1], asc[i]
        if int(cur_y) < window_start:
            continue  # 仅检查窗口内相邻年
        prev, cur = year_amount[prev_y], year_amount[cur_y]
        if prev > 0 and cur < prev * 0.7:
            ever_cut = True
            break

    return DividendHistory(
        consecutive_years=consecutive,
        ever_cut=ever_cut,
        latest_year_amount=year_amount.get(target_year),
        history_mean_amount=history_mean,
        history_3y_mean=history_3y_mean,
    )


# ---------------------------------------------------------------------------
# 网络层（单独隔离）
# ---------------------------------------------------------------------------

def _fetch_eastmoney_rows(url: str, stock_code: str, label: str) -> List[dict]:
    """东财 datacenter GET 统一封装：请求 → 取 result.data → 失败 warning 返空。"""
    try:
        resp = requests.get(url, headers=_UA, timeout=15)
        resp.raise_for_status()
        return (resp.json().get("result") or {}).get("data") or []
    except Exception as e:
        logger.warning("东财%s接口获取失败 %s: %s", label, stock_code, e)
        return []


def _secucode(stock_code: str) -> str:
    """6 开头上交所，否则深交所。"""
    market = ".SH" if stock_code.startswith("6") else ".SZ"
    return f"{stock_code}{market}"


def fetch_financial_rows(stock_code: str) -> List[dict]:
    """东财财务行。"""
    return _fetch_eastmoney_rows(_FINANCE_URL.format(secucode=_secucode(stock_code)),
                                 stock_code, "财务")


def fetch_dividend_rows(stock_code: str) -> List[dict]:
    """东财分红明细行。"""
    return _fetch_eastmoney_rows(_DIVIDEND_URL.format(code=stock_code), stock_code, "分红")


def fetch_industry(stock_code: str) -> str:
    """东财行业字符串（EM2016 优先，降级 INDUSTRYCSRC1）。"""
    rows = _fetch_eastmoney_rows(_INDUSTRY_URL.format(secucode=_secucode(stock_code)),
                                 stock_code, "行业")
    if not rows:
        return ""
    return rows[0].get("EM2016") or rows[0].get("INDUSTRYCSRC1") or ""


def fetch_cashflow_rows(stock_code: str) -> List[dict]:
    """东财现金流量表行（含 CONSTRUCT_LONG_ASSET 资本开支）。"""
    return _fetch_eastmoney_rows(_CASHFLOW_URL.format(secucode=_secucode(stock_code)),
                                 stock_code, "现金流量表")


def merge_capex(financials: List[AnnualFinancial],
                cashflow_rows: List[dict]) -> List[AnnualFinancial]:
    """把现金流量表资本开支(CONSTRUCT_LONG_ASSET)按年合并，返回带 capex 的新列表（不改原对象）。

    东财现金流量表 CONSTRUCT_LONG_ASSET 为正数（购建固定资产/无形资产支付的现金）。
    仅取年报(12-31)行匹配年份；无匹配年份的行原样保留（capex 保持 None）。
    """
    # 按年聚合年报 CAPEX
    capex_by_year: dict = {}
    for row in cashflow_rows:
        date_str = str(row.get("REPORT_DATE") or "")[:10]
        if len(date_str) < 10 or date_str[5:10] != "12-31":
            continue
        year = int(date_str[:4])
        val = _to_float(row.get("CONSTRUCT_LONG_ASSET"))
        if val is None:
            continue
        capex_by_year[year] = capex_by_year.get(year, 0.0) + val
    # 不修改原对象：用 replace 生成带 capex 的新副本
    return [
        dataclasses.replace(fin, capex=capex_by_year[fin.year]) if fin.year in capex_by_year else fin
        for fin in financials
    ]


# ---------------------------------------------------------------------------
# 编排：assess_for_stock
# ---------------------------------------------------------------------------

def assess_for_stock(*,
                     stock_code: str,
                     total_shares: float,
                     dividend_total: Optional[float],
                     dividend_yield_before_tax: Optional[float],
                     latest_dividend_year: Optional[str],
                     industry: str,
                     dividend_records: List[DividendRecord],
                     financial_rows: Optional[List[dict]] = None,
                     cashflow_rows: Optional[List[dict]] = None,
                     price_change_1y: Optional[float] = None,
                     top10_holding: Optional[float] = None) -> SustainabilityResult:
    """可持续性评估编排入口：取数据 → 喂纯评估器。

    dividend_records / industry / financial_rows / cashflow_rows 可外部注入（verify 复用），
    未注入时现场走东财 HTTP 取数。
    """
    if dividend_yield_before_tax is None or dividend_yield_before_tax <= 0:
        return SustainabilityResult(triggered=False, verdict="未评估", score=None,
                                    notes=["无股息率数据，未评估"])

    # 财务数据
    if financial_rows is None:
        financial_rows = fetch_financial_rows(stock_code)
    financials = parse_financial_rows(financial_rows)
    # 资本开支（现金流量表，修正 FCF 口径）——merge_capex 返回新列表，不改原对象
    if cashflow_rows is None:
        cashflow_rows = fetch_cashflow_rows(stock_code)
    financials = merge_capex(financials, cashflow_rows)
    latest = select_latest_annual(financials, latest_dividend_year)

    # 分红历史
    history = aggregate_dividend_history(dividend_records, latest_dividend_year, total_shares)

    return assess_sustainability(
        dividend_yield_before_tax=dividend_yield_before_tax,
        dividend_total=dividend_total,
        latest=latest,
        history=history,
        industry=industry,
        price_change_1y=price_change_1y,
        top10_holding=top10_holding,
    )


def assess_with_auto_fetch(stock_code: str,
                          total_shares: float,
                          dividend_total: Optional[float],
                          dividend_yield_before_tax: Optional[float],
                          latest_dividend_year: Optional[str],
                          industry: Optional[str] = None,
                          dividend_rows: Optional[List[dict]] = None,
                          financial_rows: Optional[List[dict]] = None) -> SustainabilityResult:
    """全自取数版编排：财务/分红/行业全走东财，不依赖外部传入 mootdx 数据。

    供 analysis.py 调用 —— 可持续性模块自洽，无需 pr.py 的 mootdx 行业、
    也无需 dividend.py 的 mootdx 分红记录。
    """
    if not industry or industry in ("未知行业", "无", ""):
        # 上游（pr.py 走 mootdx）行业不可用时，走东财重取，保证银行/周期判定准确
        industry = fetch_industry(stock_code)
    if dividend_rows is None:
        dividend_rows = fetch_dividend_rows(stock_code)
    records, em_latest_year = parse_dividend_rows(dividend_rows)
    # 分红财年：优先用外部传入（来自股息率口径），否则用东财分红明细推断
    div_year = latest_dividend_year or em_latest_year

    return assess_for_stock(
        stock_code=stock_code,
        total_shares=total_shares,
        dividend_total=dividend_total,
        dividend_yield_before_tax=dividend_yield_before_tax,
        latest_dividend_year=div_year,
        industry=industry,
        dividend_records=records,
        financial_rows=financial_rows,
    )


__all__ = [
    "parse_financial_rows",
    "parse_dividend_rows",
    "select_latest_annual",
    "aggregate_dividend_history",
    "fetch_financial_rows",
    "fetch_cashflow_rows",
    "merge_capex",
    "fetch_dividend_rows",
    "fetch_industry",
    "assess_for_stock",
    "assess_with_auto_fetch",
]

