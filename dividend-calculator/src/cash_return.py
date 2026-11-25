"""现金回报率（现金分红 + 注销类回购，#29）。

与 `pr_calculator.compute_cash_return`（「估值不变下的股东总回报率」= ROE 口径）区分：
本条只看**已发生**的现金流出（分红总额 + 注销类回购金额）÷ 当前总市值，不假设
未来 ROE 与估值，回答「今年公司把多少市值以现金方式还给了股东」。

口径（2026-11 定案，双端镜像 `site/js/calculator.js::computeTSR`）：

1. **只计注销类回购**：`REPUROBJECTIVE` 含「注销」才计入。纯股权激励/员工持股回购
   是薪酬支出，不是股东回报——美的 2025 年 30 亿即属此类，计入会虚增回报。
   「注销 + 激励」混合计划按注销类计入（实际会注销一部分），并在 note 里标注。
2. **时间窗口 = 最新完整财年**：以回购完成日 `FINISHDATE` 落在该财年为准，与主卡
   「最新完整财年」同口径。未完成事件（FINISHDATE 为空）不计入，单独统计并提示。
3. **金额取已回购金额 `REPURAMOUNT`**，不做按日拆分（源数据本身就是事件累计口径）。
4. **总回报率 = (现金分红总额 + 回购金额) / 当前总市值**。分母与主卡同截面。

取数失败（None）时**不把回购当 0**：此时只给分红收益率，`cash_return_total_yield=None`，
状态 `fetch_error`（数据铁律：未知不等于零）。
"""
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from .eastmoney_fetcher import fetch_repurchase_rows

logger = logging.getLogger(__name__)

# 用途关键词：含「注销」视为股东回报；含激励词且不含注销视为薪酬支出
BUYBACK_KEYWORD = "注销"
INCENTIVE_KEYWORDS = ("股权激励", "员工持股")

STATUS_OK = "ok"
STATUS_NO_REPURCHASE = "no_repurchase"
STATUS_FETCH_ERROR = "fetch_error"
STATUS_MISSING_INPUTS = "missing_inputs"
STATUS_NO_FISCAL_YEAR = "no_fiscal_year"


@dataclass
class CashReturnResult:
    """现金回报率结果（扁平字段；JSON 由 asdict 直出，JS 端同名镜像）。"""

    cash_return_status: str = STATUS_MISSING_INPUTS
    cash_return_year: Optional[int] = None
    cash_return_dividend: Optional[float] = None          # 现金分红总额（元）
    cash_return_repurchase: Optional[float] = None        # 已计入的注销类回购（元）
    cash_return_repurchase_events: int = 0                # 计入的事件数
    cash_return_incentive_excluded: Optional[float] = None   # 激励类，未计入（元）
    cash_return_in_progress: Optional[float] = None  # 财年内未完成，未计入（元）
    cash_return_dividend_yield: Optional[float] = None    # %
    cash_return_repurchase_yield: Optional[float] = None  # %
    cash_return_total_yield: Optional[float] = None       # %；取数失败时为 None
    cash_return_note: str = ""


def classify_repurchase_purpose(text) -> str:
    """回购用途分类：'buyback'（含注销）/ 'incentive'（仅激励）/ 'unknown'。"""
    s = str(text or "")
    if BUYBACK_KEYWORD in s:
        return "buyback"
    if any(k in s for k in INCENTIVE_KEYWORDS):
        return "incentive"
    return "unknown"


def _num(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN → None


def _year_of(value) -> Optional[int]:
    s = str(value or "")[:4]
    return int(s) if s.isdigit() else None


def aggregate_repurchase(rows: List[dict], fiscal_year: int) -> dict:
    """按财年归集回购：完成日在该财年、且用途为注销类才计入。

    返回 {amount, events, incentive, in_progress, unclassified}，单位元。
    未完成（FINISHDATE 为空）单独计入 in_progress，绝不按比例推算。
    """
    amount = incentive = in_progress = unclassified = 0.0
    events = 0
    mixed = 0
    for row in rows or []:
        amt = _num(row.get("REPURAMOUNT")) or 0.0
        if amt <= 0:
            continue
        kind = classify_repurchase_purpose(row.get("REPUROBJECTIVE"))
        finished_year = _year_of(row.get("FINISHDATE"))
        if finished_year is None:
            # 未完成：只在跨财年进行中的事件上提示，不参与计算
            start_year = _year_of(row.get("REPURSTARTDATE")) or _year_of(row.get("DIM_DATE"))
            end_year = _year_of(row.get("REPURENDDATE"))
            if start_year is not None and start_year <= fiscal_year and (
                    end_year is None or end_year >= fiscal_year):
                in_progress += amt
            continue
        if finished_year != fiscal_year:
            continue
        if kind == "buyback":
            amount += amt
            events += 1
            if any(k in str(row.get("REPUROBJECTIVE") or "") for k in INCENTIVE_KEYWORDS):
                mixed += 1   # 「注销 + 激励」混合计划：按注销计入，提示口径
        elif kind == "incentive":
            incentive += amt
        else:
            unclassified += amt
    return {
        "amount": amount,
        "events": events,
        "incentive": incentive,
        "in_progress": in_progress,
        "unclassified": unclassified,
        "mixed": mixed,
    }


def compute_cash_return(dividend_total: Optional[float], market_cap: Optional[float],
                repurchase_rows: Optional[List[dict]], fiscal_year: Optional[int],
                fetch_error: bool = False) -> CashReturnResult:
    """纯函数：由（分红总额、市值、回购行、财年）算股东总回报。"""
    # 财年可能是字符串（页面/verify 传的是 "2025"），统一转 int 再比较
    if fiscal_year is not None and not isinstance(fiscal_year, int):
        try:
            fiscal_year = int(str(fiscal_year).strip())
        except (TypeError, ValueError):
            fiscal_year = None
    res = CashReturnResult(cash_return_year=fiscal_year)
    if not fiscal_year:
        res.cash_return_status = STATUS_NO_FISCAL_YEAR
        res.cash_return_note = "无完整财年，不做股东总回报"
        return res
    if not market_cap or market_cap <= 0:
        res.cash_return_status = STATUS_MISSING_INPUTS
        res.cash_return_note = "缺少总市值，不做股东总回报"
        return res

    div = dividend_total if (dividend_total or 0) > 0 else 0.0
    res.cash_return_dividend = div
    res.cash_return_dividend_yield = div / market_cap * 100

    if fetch_error:
        res.cash_return_status = STATUS_FETCH_ERROR
        res.cash_return_repurchase = None
        res.cash_return_note = "回购取数失败，只显示分红收益率（不把未知当 0）"
        return res

    agg = aggregate_repurchase(repurchase_rows or [], fiscal_year)
    res.cash_return_repurchase = agg["amount"]
    res.cash_return_repurchase_events = agg["events"]
    res.cash_return_incentive_excluded = agg["incentive"] or None
    res.cash_return_in_progress = agg["in_progress"] or None
    res.cash_return_repurchase_yield = agg["amount"] / market_cap * 100
    res.cash_return_total_yield = (div + agg["amount"]) / market_cap * 100

    notes = []
    if not repurchase_rows:
        res.cash_return_status = STATUS_NO_REPURCHASE
        notes.append("该股无回购记录")
    else:
        res.cash_return_status = STATUS_OK
        if agg["events"] == 0:
            notes.append("该财年无完成的注销类回购")
    if agg["incentive"] > 0:
        notes.append(
            f"激励类回购 {agg['incentive'] / 1e8:.2f} 亿元未计入（属薪酬支出）")
    if agg["in_progress"] > 0:
        notes.append(
            f"进行中回购 {agg['in_progress'] / 1e8:.2f} 亿元未完成，未计入")
    if agg["unclassified"] > 0:
        notes.append(
            f"用途未标注的 {agg['unclassified'] / 1e8:.2f} 亿元未计入")
    if agg["mixed"] > 0:
        notes.append(f"含 {agg['mixed']} 笔「注销+激励」混合计划，按注销类计入")
    notes.append("按回购完成日归属财年，未按日拆分")
    res.cash_return_note = "；".join(notes)
    return res


def estimate_cash_return(stock_code: str, dividend_total: Optional[float],
                 market_cap: Optional[float], fiscal_year: Optional[int]) -> CashReturnResult:
    """编排入口：取数（区分失败/无记录）后计算。异常不外抛，降级为 fetch_error。"""
    if not fiscal_year or not market_cap or market_cap <= 0:
        return compute_cash_return(dividend_total, market_cap, [], fiscal_year)
    try:
        rows = fetch_repurchase_rows(stock_code)
    except Exception as e:  # 防御：fetch_* 已内部兜底，这里只防未预期异常
        logger.warning("回购取数异常 %s: %s", stock_code, e)
        rows = None
    if rows is None:
        return compute_cash_return(dividend_total, market_cap, None, fiscal_year, fetch_error=True)
    return compute_cash_return(dividend_total, market_cap, rows, fiscal_year)
