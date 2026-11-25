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
  - 盈利预测：RPT_WEB_RESPREDICT（datacenter-web，券商一致预期 EPS1..4 + MARK1..4）
  - 回购明细：RPTA_WEB_GETHGLIST_NEW（datacenter-web，已回购金额 + 回购用途）

严禁虚构数据：数据源不可用即返回空/None，由调用方显式标注缺失。
"""
import json
import logging
from pathlib import Path
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

# 东财资产负债表（商誉 GOODWILL / 所有者权益合计 TOTAL_EQUITY；#26）
_BALANCE_URL = (
    "https://datacenter-web.eastmoney.com/api/data/v1/get"
    "?sortColumns=REPORT_DATE&sortTypes=-1&pageSize=2&pageNumber=1"
    "&reportName=RPT_F10_FINANCE_GBALANCE&columns=ALL"
    '&filter=(SECUCODE%3D"{secucode}")'
)

# 东财前十大股东接口（securities 子路径，返回结构同为 result.data）
_TOP10_URL = (
    "https://datacenter.eastmoney.com/securities/api/data/v1/get"
    "?reportName=RPT_F10_EH_HOLDERS&columns=ALL"
    '&filter=(SECUCODE%3D"{secucode}")&pageNumber=1&pageSize=10'
    "&sortTypes=-1&sortColumns=END_DATE"
)

# 东财盈利预测一致预期（datacenter-web，与页面直连同源；无覆盖时 result.data = null）
_FORECAST_URL = (
    "https://datacenter-web.eastmoney.com/api/data/v1/get"
    "?reportName=RPT_WEB_RESPREDICT&columns=ALL"
    '&filter=(SECURITY_CODE%3D"{code}")&pageNumber=1&pageSize=5'
)

# 东财回购明细（datacenter-web，页面直连同源；无回购记录时 code=9201、result.data=null）
_REPURCHASE_URL = (
    "https://datacenter-web.eastmoney.com/api/data/v1/get"
    "?reportName=RPTA_WEB_GETHGLIST_NEW&columns=ALL&source=WEB"
    '&filter=(DIM_SCODE%3D"{code}")&pageNumber=1&pageSize=50'
    "&sortColumns=DIM_DATE&sortTypes=-1"
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


def fetch_goodwill(stock_code: str) -> Optional[tuple]:
    """东财资产负债表里的 (商誉, 所有者权益合计)，单位元；无数据返回 None。

    2026-11 实测：RPT_F10_FINANCE_GBALANCE 有 GOODWILL / TOTAL_EQUITY 字段；
    银行等金融报表不返回该表（result.data 为空），此时返回 None，调用方跳过警示，
    不补 0、不推断（数据铁律）。茅台等无商誉公司 GOODWILL 为 null，同样返回 None。
    """
    rows = _fetch_eastmoney_rows(_BALANCE_URL.format(secucode=_secucode(stock_code)),
                                 stock_code, "资产负债表")
    for row in rows:
        goodwill = row.get("GOODWILL")
        equity = row.get("TOTAL_EQUITY")
        if goodwill is not None and equity is not None:
            try:
                return float(goodwill), float(equity)
            except (TypeError, ValueError):
                return None
    return None


def fetch_dividend_rows(stock_code: str) -> Optional[List[dict]]:
    """东财分红明细行（出口应用已知错误行修正表 site/data/dividend_fixes.json）。

    语义（#38 M5）：网络/HTTP 异常 → None（取数失败）；请求成功但无数据 → []
    （真无分红）。调用方据此区分「取数失败」与「真无分红」，避免把无分红公司
    误判为取数失败（丢失「0 年连续分红」负面结论）。
    """
    try:
        resp = requests.get(_DIVIDEND_URL.format(code=stock_code), headers=_UA, timeout=15)
        resp.raise_for_status()
        rows = (resp.json().get("result") or {}).get("data") or []
        return _apply_dividend_fixes(rows, stock_code)
    except Exception as e:
        logger.warning("东财分红接口获取失败 %s: %s", stock_code, e)
        return None


# 东财分红明细个别行存在数值错误（2026-08 抽样 30 股 579 行年报记录发现 1 行：
# 长江电力 600900 2015 年度东财 10派1.2946，官方公告与巨潮/同花顺/新浪均为 10派4）。
# 修正表仅录入经多源官方验证的个案（巨潮在 A+H 股上自身有错，不能自动仲裁，
# 见 docs/DATA_RELIABILITY.md）。键：code + REPORT_DATE + EX_DIVIDEND_DATE 三者同匹配。
_FIXES_CACHE: Optional[dict] = None


def _load_dividend_fixes() -> dict:
    """加载修正表（进程内缓存）。缺失/损坏 → 空表（降级为原始数据，不阻断）。"""
    global _FIXES_CACHE
    if _FIXES_CACHE is None:
        path = Path(__file__).resolve().parent.parent / "site" / "data" / "dividend_fixes.json"
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            _FIXES_CACHE = {
                (f["code"], str(f["report_date"])[:10], str(f["ex_dividend_date"])[:10]): f
                for f in data.get("fixes", [])
            }
        except Exception as e:
            logger.warning("分红修正表加载失败（降级为未修正）: %s", e)
            _FIXES_CACHE = {}
    return _FIXES_CACHE


def _apply_dividend_fixes(rows: List[dict], stock_code: str) -> List[dict]:
    """对东财分红行应用修正表：命中行替换 PRETAX_BONUS_RMB 并记日志（透明可追溯）。"""
    fixes = _load_dividend_fixes()
    if not fixes:
        return rows
    for row in rows:
        rd = str(row.get("REPORT_DATE") or "")[:10]
        ex = str(row.get("EX_DIVIDEND_DATE") or "")[:10]
        fix = fixes.get((stock_code, rd, ex))
        if fix is not None:
            logger.warning(
                "分红修正 %s %s：东财 %.4f → %.4f（%s）",
                stock_code, rd, row.get("PRETAX_BONUS_RMB") or 0,
                fix["corrected_per_10"], fix.get("verified_by", ""),
            )
            row["PRETAX_BONUS_RMB"] = fix["corrected_per_10"]
    return rows


FORECAST_NO_DATA_CODE = 9201  # 东财「返回数据为空」＝真·无覆盖，不是接口故障


def _datacenter_rows_from_payload(payload) -> Optional[List[dict]]:
    """东财 datacenter 响应三分类：行列表 / []（真无数据）/ None（取数失败）。

    东财对「本来就没有数据」和「服务端错误」都返回 HTTP 200 + success=false，只能靠
    code 区分：9201 = 返回数据为空（真实结论，如无一致预期、无回购记录）；其他非 0
    code（如 9501 参数错误）是接口故障，必须报成取数失败，否则 UI 会把故障说成
    「无数据」——那会把取数问题伪装成公司的真实结论（数据铁律 1/2）。
    """
    if not payload:
        return None
    data = (payload.get("result") or {}).get("data")
    if data:
        return data
    if payload.get("success") is True:
        return []
    if payload.get("code") == FORECAST_NO_DATA_CODE:
        return []
    return None


def _forecast_rows_from_payload(payload) -> Optional[List[dict]]:
    """一致预期行（语义同 _datacenter_rows_from_payload，保留旧名给既有调用方）。"""
    return _datacenter_rows_from_payload(payload)


def fetch_repurchase_rows(stock_code: str) -> Optional[List[dict]]:
    """东财回购明细行（RPTA_WEB_GETHGLIST_NEW，#29）。

    语义同 fetch_dividend_rows：网络/HTTP/接口故障 → None（取数失败）；请求成功但
    该公司无回购记录 → []（真无回购，600036/601398 实测 code=9201）。

    字段（2026-11 实测）：DIM_SCODE 代码、DIM_DATE 公告日、REPURAMOUNT 已回购金额（元）、
    JEXX/JESX 计划金额下限/上限、REPUROBJECTIVE 回购用途（区分「注销」与「股权激励」）、
    REPURPROGRESS 实施进度、REPURSTARTDATE/REPURENDDATE 实施区间、FINISHDATE 完成日。
    """
    try:
        resp = requests.get(_REPURCHASE_URL.format(code=stock_code), headers=_UA, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        logger.warning("东财回购接口获取失败 %s: %s", stock_code, e)
        return None
    rows = _datacenter_rows_from_payload(payload)
    if rows is None:
        logger.warning("东财回购接口异常 %s: code=%s %s", stock_code,
                       payload.get("code"), str(payload.get("message"))[:40])
    return rows


def fetch_earnings_forecast(stock_code: str) -> Optional[List[dict]]:
    """东财券商一致预期行（RPT_WEB_RESPREDICT）。

    语义与 fetch_dividend_rows 对齐（#38 M5）：网络/HTTP/接口故障 → None（取数失败）；
    请求成功但无覆盖 → []。字段：EPS1..4 为各年一致预期每股收益，YEAR1..4 为对应
    财年，YEAR_MARK1..4 为 A（已披露）/E（预测），RATING_ORG_NUM 为覆盖机构数。
    接口不提供分红预测——支付率需另行推算。
    """
    try:
        resp = requests.get(_FORECAST_URL.format(code=stock_code), headers=_UA, timeout=15)
        resp.raise_for_status()
        payload = resp.json()
    except Exception as e:
        logger.warning("东财盈利预测接口获取失败 %s: %s", stock_code, e)
        return None
    rows = _forecast_rows_from_payload(payload)
    if rows is None:
        logger.warning("东财盈利预测接口异常 %s: code=%s %s", stock_code,
                       payload.get("code"), str(payload.get("message"))[:40])
    return rows


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
    "fetch_earnings_forecast",
    "fetch_industry",
    "fetch_top10_holding",
    "fetch_price_change_1y",
]
