"""
市赚率（PR）计算模块

公式体系（完整版）：
  基础PR  = PE / ROE / 100
  修正PR  = N × PE / ROE / 100   （N 基于股利支付率修正）
  PB-PR   = PB / ROE² / 100       （周期股参考）

数据来源（多源降级）：
  PE-TTM / PB: 腾讯行情 [主] → 东方财富 push2 [备]
  ROE / 净利润: 同花顺财报 [主] → 东方财富 push2 [备]
  行业分类:     mootdx F10 → 东方财富 datacenter(RPT_F10_BASIC_ORGINFO) → 降级为 "未知行业"
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

import requests

from .datasource.validation import check_pe, check_pb, check_roe, check_net_profit, check_payout_ratio

from .tencent_quote import fetch_tencent_quote
from .pr_calculator import (
    compute_basic_pr,
    compute_corrected_pr,
    compute_pb_pr,
    compute_n_factor,
    classify_valuation,
    classify_industry,
)
from .eastmoney_fetcher import fetch_industry as fetch_eastmoney_industry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class PRResult:
    """市赚率计算结果"""
    stock_code: str
    stock_name: Optional[str]

    # 市赚率三值
    pr_basic: Optional[float]           # 基础PR = PE / ROE / 100
    pr_corrected: Optional[float]        # 修正PR = N × PE / ROE / 100
    pr_pb: Optional[float]               # PB-PR = PB / ROE² / 100

    # 估值档位（基于基础PR或修正PR，取两者较低值）
    valuation_zone: str                   # 低估 / 合理偏低 / 合理 / 高估

    # 中间计算数据
    pe_ttm: Optional[float]              # 市盈率 TTM
    pb: Optional[float]                   # 市净率
    roe_latest: Optional[float]           # 最新年报 ROE（百分比，如 15.9）
    roe_5y_median: Optional[float]        # 5年 ROE 中位数（百分比）
    net_profit_latest_period: Optional[float]  # 最新报告期累计净利润（元），非 TTM；仅展示
    net_profit_annual: Optional[float]    # 最新年报净利润（元）
    dividend_total: Optional[float]       # 最新财年现金分红总额（元）
    payout_ratio: Optional[float]         # 股利支付率（0~1）
    n_factor: Optional[float]             # 修正系数 N

    # 行业相关
    industry: str                         # 行业分类
    is_cyclical: bool                     # 是否周期行业
    is_tech: bool                         # 是否科技行业
    is_loss_stock: bool                   # 是否亏损股
    pr_warning: str                       # 市赚率适用性提示

    # 数据来源追踪
    pe_pb_source: str                     # PE/PB 数据来源
    finance_source: str                   # 财务数据来源
    industry_source: str                  # 行业数据来源
    errors: List[str] = field(default_factory=list)  # 采集过程中的非致命错误


# ---------------------------------------------------------------------------
# 行业映射
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# 数据获取 — PE / PB
# ---------------------------------------------------------------------------

def _get_pe_pb_tencent(stock_code: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
    """从腾讯行情获取 PE-TTM、PB 和股票名称（通过统一的 TencentQuote 模块）。"""
    quote = fetch_tencent_quote(stock_code)
    if quote is None:
        return None, None, None
    logger.debug("腾讯行情 PE/PB %s: PE=%s PB=%s", stock_code, quote.pe_ttm, quote.pb)
    return quote.pe_ttm, quote.pb, quote.name


def _get_pe_pb_eastmoney(stock_code: str) -> Tuple[Optional[float], Optional[float]]:
    """从东方财富 push2 接口获取 PE 和 PB（备选）"""
    try:
        market = "1" if stock_code.startswith("6") else "0"
        url = (
            f"https://push2.eastmoney.com/api/qt/stock/get"
            f"?secid={market}.{stock_code}&fields=f9,f167"
        )
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return None, None

        data = resp.json()
        d = data.get("data")
        if not d:
            return None, None

        pe_ttm = None
        try:
            val = float(d.get("f9", 0))
            if val > 0:
                pe_ttm = val
        except (ValueError, TypeError):
            pass

        pb = None
        try:
            val = float(d.get("f167", 0))
            if val > 0:
                pb = val
        except (ValueError, TypeError):
            pass

        logger.debug("东方财富 PE/PB %s: PE=%s PB=%s", stock_code, pe_ttm, pb)
        return pe_ttm, pb

    except Exception as e:
        logger.warning("东方财富获取PE/PB失败 %s: %s", stock_code, e)
        return None, None


def _get_pe_pb(stock_code: str) -> Tuple[Optional[float], Optional[float], Optional[str], str, List[str]]:
    """
    获取 PE-TTM 和 PB，多源自动降级
    返回: (pe_ttm, pb, stock_name, source_label, errors)
    """
    errors: List[str] = []

    # 主：腾讯行情
    pe, pb, name = _get_pe_pb_tencent(stock_code)
    if pe is not None and pb is not None:
        return pe, pb, name, "腾讯行情", errors

    # 若PE缺失但PB有，尝试用 akshare 同花顺 EPS 推算 PE
    if pe is None and pb is not None:
        try:
            import akshare as ak
            df = ak.stock_financial_abstract_ths(symbol=stock_code, indicator="按年度")
            if not df.empty:
                latest = df.iloc[-1]
                eps_str = str(latest.get("基本每股收益", ""))
                if eps_str:
                    eps = float(eps_str)
                    if eps > 0:
                        # 从腾讯行情取价格（已有 name 和 pb）
                        from .tencent_quote import fetch_tencent_quote
                        quote = fetch_tencent_quote(stock_code)
                        if quote is not None and quote.price is not None:
                            pe = round(quote.price / eps, 2)
                            logger.info("从同花顺EPS推算PE %s: price=%.2f EPS=%.4f PE=%.2f",
                                        stock_code, quote.price, eps, pe)
                            return pe, pb, name, "腾讯+同花顺(EPS推算)", errors
        except Exception as e:
            errors.append(f"同花顺EPS推算PE失败: {e}")

    if pe is not None or pb is not None:
        errors.append("腾讯行情仅返回部分PE/PB数据")
        return pe, pb, name, "腾讯行情(部分)", errors

    errors.append("腾讯行情PE/PB获取失败")
    # 备：东方财富
    pe, pb = _get_pe_pb_eastmoney(stock_code)
    if pe is not None or pb is not None:
        return pe, pb, name, "东方财富", errors

    errors.append("东方财富PE/PB获取失败")
    return None, None, name, "无", errors


# ---------------------------------------------------------------------------
# 数据获取 — ROE / 净利润（mootdx F10 为主，akshare 同花顺为备）
# ---------------------------------------------------------------------------

def _pct_to_float(value) -> float:
    """将百分比字符串转为浮点数，如 '15.90%' -> 15.9"""
    s = str(value)
    return float(s.replace("%", "").strip())


def _amount_to_float(value) -> float:
    """将金额字符串转为元，如 '345.03亿' -> 34503000000.0"""
    s = str(value).replace(",", "").strip()
    multiplier = 1.0
    if "亿" in s:
        multiplier = 1e8
        s = s.replace("亿", "")
    elif "万" in s:
        multiplier = 1e4
        s = s.replace("万", "")
    return float(s) * multiplier


def _get_financial_ths(stock_code: str) -> Tuple[
    Optional[float], Optional[float], Optional[float], Optional[float], str, List[str],
]:
    """从 akshare/同花顺财报获取 ROE 和净利润（mootdx 不可用时的备选）"""
    errors: List[str] = []
    try:
        import akshare as ak

        # 年度数据
        df_annual = ak.stock_financial_abstract_ths(symbol=stock_code, indicator="按年度")
        if df_annual.empty:
            errors.append("同花顺年度财报数据为空")
            return None, None, None, None, "无", errors

        latest_annual = df_annual.iloc[-1]
        roe_latest = _pct_to_float(latest_annual["净资产收益率"])
        net_profit_annual = _amount_to_float(latest_annual["净利润"])

        # 5年 ROE 中位数
        years = df_annual["报告期"].astype(int).values
        last5_mask = years >= (years.max() - 4)
        roe_5y_vals = [
            _pct_to_float(v) for v in df_annual.loc[last5_mask, "净资产收益率"]
        ]
        roe_5y_median = float(sorted(roe_5y_vals)[len(roe_5y_vals) // 2]) if roe_5y_vals else None

        # 最新报告期累计净利润（非 TTM；仅展示）：对齐 JS 真 TTM 算法
        # TTM = 最新累计 + 上年全年 − 上年同期（最新为年报时 TTM=全年）
        net_profit_latest_period = None
        try:
            df_q = ak.stock_financial_abstract_ths(symbol=stock_code, indicator="按报告期")
            if not df_q.empty and "报告期" in df_q.columns:
                dated = []
                for _, row in df_q.iterrows():
                    rp = str(row["报告期"]).strip()
                    if len(rp) == 10 and rp[4] == "-" and rp[7] == "-":
                        dated.append((rp, _amount_to_float(row["净利润"])))
                dated.sort(key=lambda x: x[0])
                if len(dated) >= 2:
                    latest_date, latest_np = dated[-1]
                    latest_year = int(latest_date[:4])
                    latest_md = latest_date[5:]
                    prev_year = None
                    prev_same = None
                    for d, np in reversed(dated[:-1]):
                        if d[5:] == "12-31" and d[:4] != str(latest_year):
                            prev_year = np
                            break
                    if latest_md != "12-31":
                        target_prev = f"{latest_year - 1}-{latest_md}"
                        for d, np in dated[:-1]:
                            if d == target_prev:
                                prev_same = np
                                break
                        if prev_year is not None and prev_same is not None:
                            net_profit_latest_period = latest_np + prev_year - prev_same
                        else:
                            net_profit_latest_period = None  # 数据不全不猜，置 None
                    else:
                        net_profit_latest_period = latest_np  # 最新为年报 → 全年
        except Exception as e:
            errors.append(f"同花顺季度财报获取失败: {e}")

        logger.debug(
            "同花顺财报 %s: ROE最新=%.2f%% ROE5Y中位=%.2f%% 最新报告期净利润=%.2f亿 年报净利润=%.2f亿",
            stock_code,
            roe_latest,
            roe_5y_median or -1,
            (net_profit_latest_period or 0) / 1e8,
            net_profit_annual / 1e8,
        )
        return roe_latest, roe_5y_median, net_profit_latest_period, net_profit_annual, "同花顺（akshare）", errors

    except Exception as e:
        errors.append(f"同花顺财报获取失败: {e}")
        return None, None, None, None, "无", errors


def _get_financial(stock_code: str) -> Tuple[
    Optional[float], Optional[float], Optional[float], Optional[float], str, List[str],
]:
    """获取 ROE 和净利润，mootdx F10 优先，akshare/同花顺备用"""
    errors: List[str] = []

    # 主：mootdx F10
    try:
        from .datasource.mootdx_source import MootdxSource, get_quotes_client
        source = MootdxSource()

        # ROE 历史（多年年报数据）
        roe_history = source.get_roe_history(stock_code)
        roe_latest = None
        roe_5y_median = None
        if roe_history:
            years = sorted(roe_history.keys(), reverse=True)
            if years:
                roe_latest = roe_history[years[0]]
            last5 = sorted(roe_history.items(), reverse=True)[:5]
            if last5:
                vals = sorted([v for _, v in last5])
                roe_5y_median = float(vals[len(vals) // 2])

        # 净利润（年报）
        np_history = source.get_net_profit_annual(stock_code)
        net_profit_annual = None
        if np_history:
            np_years = sorted(np_history.keys(), reverse=True)
            if np_years:
                net_profit_annual = np_history[np_years[0]]

        # 最新报告期累计净利润（非 TTM；仅展示）：finance() 是最新报告期快照，
        # 无历史期次（无上年同期），无法构造真 TTM（审查 #5）
        net_profit_latest_period = None
        try:
            client = get_quotes_client()
            fin = client.finance(symbol=stock_code)
            if fin is not None and len(fin) > 0 and 'jinglirun' in fin.columns:
                net_profit_latest_period = float(fin['jinglirun'].iloc[0])
        except Exception as e:
            errors.append(f"finance 快照获取失败: {e}")

        if roe_latest is not None:
            logger.debug(
                "mootdx F10 财报 %s: ROE最新=%.2f%% ROE5Y中位=%.2f%% 最新报告期净利润=%.2f亿 年报净利润=%.2f亿",
                stock_code,
                roe_latest,
                roe_5y_median or -1,
                (net_profit_latest_period or 0) / 1e8,
                (net_profit_annual or 0) / 1e8,
            )
            return roe_latest, roe_5y_median, net_profit_latest_period, net_profit_annual, "mootdx F10", errors

        errors.append("mootdx F10 未获取到有效 ROE 数据")
    except Exception as e:
        errors.append(f"mootdx F10 财报获取失败: {e}")

    # 备：akshare/同花顺
    logger.info("mootdx 财报不可用，回退到 akshare/同花顺")
    roe, roe5y, np_ttm, np_annual, src, errs2 = _get_financial_ths(stock_code)
    errors.extend(errs2)
    return roe, roe5y, np_ttm, np_annual, src, errors


# ---------------------------------------------------------------------------
# 数据获取 — 行业分类
# ---------------------------------------------------------------------------

def _get_industry(stock_code: str) -> Tuple[str, str]:
    """获取行业分类：mootdx F10 优先，东方财富 datacenter 备用（与 JS 端同源）"""
    # 主：mootdx F10 行业分析
    try:
        from .datasource.mootdx_source import MootdxSource
        source = MootdxSource()
        industry = source.get_industry(stock_code)
        if industry and industry != "未知行业":
            logger.debug("mootdx F10 行业 %s: %s", stock_code, industry)
            return industry, "mootdx F10"
    except Exception as e:
        logger.debug("mootdx F10 行业获取失败 %s: %s", stock_code, e)

    # 备：东方财富 datacenter RPT_F10_BASIC_ORGINFO（与 sustainability/JS 端同源，全球可用）
    try:
        industry = fetch_eastmoney_industry(stock_code)
        if industry:
            logger.debug("东方财富行业 %s: %s", stock_code, industry)
            return industry, "东方财富"
    except Exception as e:
        logger.debug("东方财富行业获取失败 %s: %s", stock_code, e)

    return "未知行业", "无"


# ---------------------------------------------------------------------------
# 核心计算
# ---------------------------------------------------------------------------


def calculate_pr(
    stock_code: str,
    stock_name: Optional[str] = None,
    dividend_total: Optional[float] = None,
    stock_info: Optional["StockInfo"] = None,  # noqa: F821  # forward ref
) -> PRResult:
    """
    计算市赚率（完整体系）

    Args:
        stock_code: 6位股票代码
        stock_name: 股票名称（可选，内部也可从腾讯获取）
        dividend_total: 最新财年现金分红总额（元），由调用方从股息率链路传入
        stock_info: 股票基本信息（可选，仅用于日志）

    Returns:
        PRResult 对象
    """
    all_errors: List[str] = []

    # 1. 获取 PE-TTM 和 PB
    pe_ttm, pb, tencent_name, pe_pb_src, errs = _get_pe_pb(stock_code)
    all_errors.extend(errs)
    if stock_name is None and tencent_name:
        stock_name = tencent_name

    # 2. 获取 ROE 和净利润
    roe_latest, roe_5y_median, net_profit_latest_period, net_profit_annual, fin_src, errs = _get_financial(stock_code)
    all_errors.extend(errs)

    # 3. 获取行业分类
    industry, ind_src = _get_industry(stock_code)

    # 4. 分类行业属性
    is_cyclical, is_tech, pr_warning = classify_industry(industry)

    # 5. 判断亏损股
    is_loss_stock = False
    if net_profit_annual is not None and net_profit_annual <= 0:
        is_loss_stock = True
        pr_warning = pr_warning + "；该股为亏损股，市赚率不适用" if pr_warning else "该股为亏损股，市赚率不适用"

    # 6. 计算股利支付率 & 修正系数 N
    payout_ratio = None
    n_factor = None
    if net_profit_annual is not None and net_profit_annual > 0 and dividend_total is not None:
        payout_ratio = dividend_total / net_profit_annual
        n_factor = compute_n_factor(payout_ratio)

    # 7. 计算三个 PR 值
    pr_basic = None
    pr_corrected = None
    pr_pb = None
    valuation_zone = "无法判定"

    roe_for_calc = roe_latest  # 主用最新年报 ROE

    if not is_loss_stock and pe_ttm is not None and roe_for_calc is not None and roe_for_calc > 0:
        pr_basic = compute_basic_pr(pe_ttm, roe_for_calc)
        pr_corrected = compute_corrected_pr(pe_ttm, roe_for_calc, n_factor)
        pr_pb = compute_pb_pr(pb, roe_for_calc)

        # 估值档位：优先用修正PR（如有），否则用基础PR
        judge_pr = pr_corrected if pr_corrected is not None else pr_basic
        valuation_zone = classify_valuation(judge_pr)

    # 数据完整性软校验（审查 #4）：越界只追加 errors，不否决结果
    all_errors.extend(_check_pr_fields(pe_ttm, pb, roe_latest, net_profit_annual, payout_ratio))

    return PRResult(
        stock_code=stock_code,
        stock_name=stock_name,
        pr_basic=pr_basic,
        pr_corrected=pr_corrected,
        pr_pb=pr_pb,
        valuation_zone=valuation_zone,
        pe_ttm=pe_ttm,
        pb=pb,
        roe_latest=roe_latest,
        roe_5y_median=roe_5y_median,
        net_profit_latest_period=net_profit_latest_period,
        net_profit_annual=net_profit_annual,
        dividend_total=dividend_total,
        payout_ratio=payout_ratio,
        n_factor=n_factor,
        industry=industry,
        is_cyclical=is_cyclical,
        is_tech=is_tech,
        is_loss_stock=is_loss_stock,
        pr_warning=pr_warning,
        pe_pb_source=pe_pb_src,
        finance_source=fin_src,
        industry_source=ind_src,
        errors=all_errors,
    )


def _check_pr_fields(
    pe_ttm: Optional[float],
    pb: Optional[float],
    roe_latest: Optional[float],
    net_profit_annual: Optional[float],
    payout_ratio: Optional[float],
) -> List[str]:
    """市赚率相关字段数据完整性软校验（审查 #4）。越界追加 warning，不否决。"""
    warnings: List[str] = []
    for w in (
        check_pe(pe_ttm),
        check_pb(pb),
        check_roe(roe_latest),
        check_net_profit(net_profit_annual),
        check_payout_ratio(payout_ratio),
    ):
        if w:
            warnings.append(w)
    return warnings
