"""
东方财富/腾讯 HTTP 取数层（共享模块，#43 L6）

从 sustainability.py 抽取的纯网络取数函数，供 sustainability.py / pr.py / api.py 共用。
本模块只做 HTTP 取数与最小字段提取，不含业务解析（解析留在各调用方）。

接口清单（均全球可用，字段名来自 600900/600036 实地验证，单位：元 / 百分数）：
  - 财务：RPT_F10_FINANCE_MAINFINADATA（datacenter，columns=ALL，含现金流/负债/银行专项）
  - 现金流量表：RPT_F10_FINANCE_GCASHFLOW（datacenter，含 CONSTRUCT_LONG_ASSET 资本开支）
  - 分红明细：RPT_SHAREBONUS_DET（datacenter-web，全历史除权记录）
  - 行业：RPT_F10_BASIC_ORGINFO（datacenter-web，EM2016 东财行业）
  - 前十大股东：RPT_F10_EH_HOLDERS（datacenter/securities，HOLD_NUM_RATIO 占比百分数）
  - 近1年涨跌幅：腾讯 fqkline 前复权日K（web.ifzq.gtimg.cn）

严禁虚构数据：数据源不可用即返回空/None，由调用方显式标注缺失。
"""
import logging
from typing import List, Optional

import requests

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

# 东财前十大股东接口（securities 子路径，返回结构同为 result.data）
_TOP10_URL = (
    "https://datacenter.eastmoney.com/securities/api/data/v1/get"
    "?reportName=RPT_F10_EH_HOLDERS&columns=ALL"
    '&filter=(SECUCODE%3D"{secucode}")&pageNumber=1&pageSize=10'
    "&sortTypes=-1&sortColumns=END_DATE"
)

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
    """6 开头上交所；8/4/92 开头北交所（4 旧代码，8/92 新代码，#42）；其余深交所。"""
    if stock_code.startswith("6"):
        return f"{stock_code}.SH"
    if stock_code.startswith(("8", "4", "92")):
        return f"{stock_code}.BJ"
    return f"{stock_code}.SZ"


def fetch_financial_rows(stock_code: str) -> List[dict]:
    """东财财务行。"""
    return _fetch_eastmoney_rows(_FINANCE_URL.format(secucode=_secucode(stock_code)),
                                 stock_code, "财务")


def fetch_cashflow_rows(stock_code: str) -> List[dict]:
    """东财现金流量表行（含 CONSTRUCT_LONG_ASSET 资本开支）。"""
    return _fetch_eastmoney_rows(_CASHFLOW_URL.format(secucode=_secucode(stock_code)),
                                 stock_code, "现金流量表")


def fetch_dividend_rows(stock_code: str) -> Optional[List[dict]]:
    """东财分红明细行。

    语义（#38 M5）：网络/HTTP 异常 → None（取数失败）；请求成功但无数据 → []
    （真无分红）。调用方据此区分「取数失败」与「真无分红」，避免把无分红公司
    误判为取数失败（丢失「0 年连续分红」负面结论）。
    """
    try:
        resp = requests.get(_DIVIDEND_URL.format(code=stock_code), headers=_UA, timeout=15)
        resp.raise_for_status()
        return (resp.json().get("result") or {}).get("data") or []
    except Exception as e:
        logger.warning("东财分红接口获取失败 %s: %s", stock_code, e)
        return None


def fetch_industry(stock_code: str) -> str:
    """东财行业字符串（EM2016 优先，降级 INDUSTRYCSRC1）。"""
    rows = _fetch_eastmoney_rows(_INDUSTRY_URL.format(secucode=_secucode(stock_code)),
                                 stock_code, "行业")
    if not rows:
        return ""
    return rows[0].get("EM2016") or rows[0].get("INDUSTRYCSRC1") or ""


def fetch_top10_holding(stock_code: str) -> Optional[float]:
    """前十大股东合计持股占比（小数，如 0.567 = 56.7%）：东财 RPT_F10_EH_HOLDERS。

    HOLD_NUM_RATIO 为占比百分数（实测招行约 18.06），sum 前 10 条后转小数。
    网络失败或数据缺失返回 None（不阻塞评估，#40 B1）。
    """
    rows = _fetch_eastmoney_rows(_TOP10_URL.format(secucode=_secucode(stock_code)),
                                 stock_code, "前十大股东")
    if not rows:
        return None
    total = 0.0
    for row in rows[:10]:
        v = row.get("HOLD_NUM_RATIO")
        if v is None:
            continue
        try:
            total += float(v)
        except (TypeError, ValueError):
            continue
    return total / 100.0 if total > 0 else None


def fetch_price_change_1y(stock_code: str) -> Optional[float]:
    """近1年股价变化率（小数，如 -0.3）：腾讯 fqkline 前复权日K。

    请求 250 根日K（实测返回 251 根），rows[0] 约 1 年前、rows[-1] 为最新：
    change = (last - past) / past。腾讯返回字符串，需 float() 转换。
    失败或 K 线不足返回 None（#40 B1）。
    """
    try:
        from .tencent_quote import fetch_kline_rows
        rows = fetch_kline_rows(stock_code, period="day", count=250)
        if not rows or len(rows) < 2:
            return None  # K 线不足，无窗口
        past_close = float(rows[0][2])   # 窗口起点（约 1 年前）收盘价
        last_close = float(rows[-1][2])  # 最新收盘价
        if last_close <= 0 or past_close <= 0:
            return None
        return (last_close - past_close) / past_close
    except Exception as e:
        logger.warning("腾讯K线近1年涨跌幅获取失败 %s: %s", stock_code, e)
        return None


__all__ = [
    "fetch_financial_rows",
    "fetch_cashflow_rows",
    "fetch_dividend_rows",
    "fetch_industry",
    "fetch_top10_holding",
    "fetch_price_change_1y",
]
