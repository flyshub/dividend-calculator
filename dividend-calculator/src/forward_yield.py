"""
下一财年前瞻股息率 —— 纯函数 + 编排层（见 docs/FORWARD_YIELD.md）。

口径（D1/D2 已定）：
  前瞻股息率 = 下一财年(L+1)一致预期 EPS × 近 3 年支付率中位数 ÷ 现价
  - 支付率按年算：(Σ 每次分红 10派/10 × 该次登记股本) ÷ 当年归母净利润，
    按行股本折算，天然免疫送转（与 #302 历史分红总额同一口径）。
  - 支付率中位数截断到 [0, 1.0]；>100% 记警示。
  - 一致预期无覆盖 / 预测亏损 / 近年无分红 → 不估（字段全 null + forward_note）。
  - 仅展示层使用，不进入选股漏斗判据（免费源无历史一致预期快照，暂不可回测）。

财年口径（与 D1 一致）：F = L + 1，L 为最新已实施分红的财年。年报季（4-7 月）
不少公司 L 落后一个财年（上年度分红已公告未实施），此时 F 的 EPS 在一致预期里
已是实际值（MARK='A'），仍按 L+1 估算「下一年度将拿到的股息」，标注 A 而非 E。
若一致预期基准年与 L 相差 ≥ 2 年（公司已停发/数据断档），不做前瞻。

双端一致性：本模块与 site/js/calculator.js::computeForwardYield 逐字段对齐，
scripts/verify_js_vs_python.py 对账 forward_status / forward_year / forward_eps /
forward_eps_kind / forward_payout_assumed / forward_dps /
forward_yield_before_tax / forward_coverage，外加 forward_warnings 集合
（文案也要一致）。中位数按 statistics.median 语义（偶数个取中间两数均值），
JS 端同实现。
"""
import math
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .dividend_records import summarize_dividend_rows
from .eastmoney_fetcher import (
    fetch_dividend_rows,
    fetch_earnings_forecast,
    fetch_financial_rows,
)
from .sustainability import parse_financial_rows

PAYOUT_WINDOW_YEARS = 3      # 支付率样本窗口（L, L-1, L-2）
MAX_PAYOUT_ASSUMED = 1.0     # 支付率假设上限（>1 视为不可持续，截断并警示）
FULL_COVERAGE = 3            # 一致预期覆盖机构数达到此值视为充分
STALE_DIVIDEND_GAP = 2       # 一致预期基准年 - 最新分红财年 ≥ 此值 → 不做前瞻
EPS_NP_TOLERANCE = 0.03      # EPS × 股本 与 归母净利 的相对误差容限
YIELD_DEVIATION = 0.5        # 前瞻 vs 主口径偏离比例阈值
YIELD_SANITY_MAX = 15.0      # 前瞻股息率合理上限（百分数）
PAYOUT_TREND_DEVIATION = 0.30  # 最新财年支付率 vs 中位数 偏离（绝对百分点/1）阈值

NO_FORECAST = "无机构盈利预测，未做前瞻估算"
FETCH_FAILED_FMT = "前瞻取数失败（%s），稍后重试"

# forward_status 枚举（与 site/js/calculator.js 同值；UI 按此选文案，不解析 forward_note）
STATUS_OK = "ok"
STATUS_NO_COMPLETE_YEAR = "no_complete_year"
STATUS_FETCH_ERROR = "fetch_error"
STATUS_NO_COVERAGE = "no_coverage"
STATUS_STALE = "stale"
STATUS_NO_TARGET_YEAR = "no_target_year"
STATUS_NO_POSITIVE_EPS = "no_positive_eps"
STATUS_NO_PAYOUT_HISTORY = "no_payout_history"
STATUS_MISSING_INPUTS = "missing_inputs"


@dataclass
class ForwardYieldResult:
    """前瞻股息率结果。不可估时 forward_yield_before_tax 为 None，
    forward_status 给出机器可读原因（forward_note 只做人类文案）。"""
    forward_status: Optional[str] = None            # 见 STATUS_* 枚举
    forward_year: Optional[int] = None              # 前瞻财年（L+1）；能确定时即使不估也给出
    forward_eps: Optional[float] = None             # 一致预期 EPS（元/股）
    forward_eps_kind: Optional[str] = None          # "E"=预测 / "A"=已披露（年报季 L 滞后）
    forward_payout_assumed: Optional[float] = None  # 采用的支付率（0~1）
    forward_payout_basis: str = ""                  # 假设依据，如 "近3年支付率中位数 79.0%"
    forward_payout_samples: List[float] = field(default_factory=list)  # 各年支付率（0~1，降序）
    forward_dps: Optional[float] = None             # 前瞻每股分红（元，税前）
    forward_yield_before_tax: Optional[float] = None  # 前瞻股息率（%，税前）
    forward_coverage: Optional[int] = None          # 一致预期覆盖机构数
    forward_source: str = ""                        # 数据来源
    forward_warnings: List[str] = field(default_factory=list)
    forward_note: str = ""                          # 不可估原因 / 公式说明


def _fiscal_year(report_time) -> Optional[int]:
    """report_time（"2025年报"/"2025中期分配"）→ 财年整数；非法返回 None。"""
    s = str(report_time or "")[:4]
    return int(s) if s.isdigit() and len(s) == 4 else None


def _record_shares(rec, total_shares) -> float:
    """逐记录股本，缺失/无效回退当前股本（#302 口径）。"""
    shares = getattr(rec, "total_shares", None)
    if shares is not None and shares > 0:
        return float(shares)
    return float(total_shares or 0)


def dividend_totals_by_year(records, total_shares: float) -> Dict[int, float]:
    """分红记录 → {财年: 现金分红总额（元）}。

    与 JS _aggregateDividendHistory 的 yearAmount 同口径：含该财年中期分配，
    每笔用各自登记股本折算，缺失回退当前股本。
    """
    out: Dict[int, float] = {}
    for rec in records:
        year = _fiscal_year(getattr(rec, "report_time", None))
        try:
            dp10 = float(getattr(rec, "dividend_per_10", 0) or 0)
        except (TypeError, ValueError):
            continue
        if year is None or dp10 <= 0:
            continue
        out[year] = out.get(year, 0.0) + dp10 / 10.0 * _record_shares(rec, total_shares)
    return out


def net_profit_by_year(financials) -> Dict[int, float]:
    """AnnualFinancial 列表 → {year: 归母净利润（元）}（缺值跳过）。"""
    out: Dict[int, float] = {}
    for fin in financials or []:
        np_ = getattr(fin, "net_profit", None)
        if np_ is not None:
            out[int(fin.year)] = float(np_)
    return out


def _num(v) -> Optional[float]:
    if v is None or str(v).strip() == "":
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def parse_forecast_row(rows) -> Optional[dict]:
    """东财一致预期行 → {base_year, eps_by_year, mark_by_year, coverage}。

    取第一条含可用 EPS 的行。基准年 = 有 MARK='A' 的最新年份，缺失回退 YEAR1。
    """
    for row in rows or []:
        eps_by_year: Dict[int, float] = {}
        mark_by_year: Dict[int, str] = {}
        for i in range(1, 5):
            # 实地验证：YEAR{i} 为财年，YEAR_MARK{i} 为该年标记（A 已披露 / E 预测）
            year = _num(row.get("YEAR%d" % i))
            if year is None:
                continue
            year = int(year)
            mark = str(row.get("YEAR_MARK%d" % i) or "").strip().upper()
            if mark:
                mark_by_year[year] = mark
            eps = _num(row.get("EPS%d" % i))
            if eps is not None:
                eps_by_year[year] = eps
        if not eps_by_year:
            continue
        actual_years = [y for y, m in mark_by_year.items() if m == "A" and y in eps_by_year]
        if actual_years:
            base_year = max(actual_years)
        else:
            base_year = int(_num(row.get("YEAR1")) or min(eps_by_year))
        coverage = _num(row.get("RATING_ORG_NUM"))
        return {
            "base_year": base_year,
            "eps_by_year": eps_by_year,
            "mark_by_year": mark_by_year,
            "coverage": int(coverage) if coverage is not None else None,
        }
    return None


def _eps_share_warnings(forecast, net_profit_by_year_map, total_shares) -> List[str]:
    """用一致预期基准年的已披露 EPS 校验股本口径：EPS × 当前股本 应 ≈ 基准年归母净利。

    一致预期 EPS 是当前股本口径；若基准年之后有送转/增发，隐含净利会与财报偏离，
    此时支付率与 DPS 的股本基础不同源，需提示（数据校验，不阻断估算）。
    """
    if not total_shares or total_shares <= 0:
        return []
    base = forecast["base_year"]
    if forecast["mark_by_year"].get(base) != "A":
        return []
    eps = forecast["eps_by_year"].get(base)
    np_actual = (net_profit_by_year_map or {}).get(base)
    if eps is None or np_actual is None or np_actual <= 0:
        return []
    implied = eps * float(total_shares)
    if abs(implied - np_actual) / np_actual <= EPS_NP_TOLERANCE:
        return []
    return ["一致预期基准年(%d) EPS %.2f × 股本 与归母净利偏差 %.0f%%，股本口径可能不一致"
            % (base, eps, abs(implied / np_actual - 1) * 100)]


def compute_forward_yield(
    *,
    price: Optional[float],
    latest_year,
    yearly_dividend_totals: Dict[int, float],
    net_profit_by_year_map: Dict[int, float],
    forecast_rows,
    is_cyclical: bool = False,
    trailing_yield: Optional[float] = None,
    total_shares: Optional[float] = None,
) -> ForwardYieldResult:
    """纯函数：由数值输入算前瞻股息率（不发网络请求）。"""
    res = ForwardYieldResult()

    if latest_year is None:
        res.forward_status = STATUS_NO_COMPLETE_YEAR
        res.forward_note = "无完整财年分红，不做前瞻"
        return res
    try:
        latest = int(latest_year)
    except (TypeError, ValueError):
        res.forward_status = STATUS_NO_COMPLETE_YEAR
        res.forward_note = "无完整财年分红，不做前瞻"
        return res
    # 契约：目标财年可确定时就给出（即使后续不估），UI 才能显示「FY2026E 无预测」
    res.forward_year = latest + 1

    if forecast_rows is None:
        res.forward_status = STATUS_FETCH_ERROR
        res.forward_note = FETCH_FAILED_FMT % "盈利预测"
        return res
    forecast = parse_forecast_row(forecast_rows)
    if forecast is None:
        res.forward_status = STATUS_NO_COVERAGE
        res.forward_note = NO_FORECAST
        return res

    res.forward_coverage = forecast["coverage"]
    res.forward_source = "东财一致预期"
    # 股本口径守卫：用基准年（MARK='A'）的 EPS 校验 EPS×股本 ≈ 归母净利
    res.forward_warnings.extend(
        _eps_share_warnings(forecast, net_profit_by_year_map, total_shares))

    gap = forecast["base_year"] - latest
    if gap >= STALE_DIVIDEND_GAP:
        # 目标财年（latest+1）已是过去年份，给出来会误导 → 置空
        res.forward_status = STATUS_STALE
        res.forward_year = None
        res.forward_note = "近年无分红（一致预期已到 %d 年，最新分红财年 %d），不做前瞻" % (
            forecast["base_year"], latest)
        return res

    forward_year = res.forward_year
    eps = forecast["eps_by_year"].get(forward_year)
    if eps is None:
        res.forward_status = STATUS_NO_TARGET_YEAR
        res.forward_note = "无 %d 年盈利预测" % forward_year
        return res
    mark = forecast["mark_by_year"].get(forward_year, "E")
    res.forward_eps = eps
    res.forward_eps_kind = "A" if mark == "A" else "E"

    if eps <= 0:
        res.forward_eps = None
        res.forward_eps_kind = None
        res.forward_status = STATUS_NO_POSITIVE_EPS
        res.forward_note = "机构预测 %d 年亏损，不估股息率" % forward_year
        return res

    samples: List[float] = []
    for year in range(latest, latest - PAYOUT_WINDOW_YEARS, -1):
        np_ = net_profit_by_year_map.get(year)
        div = yearly_dividend_totals.get(year)
        if np_ is None or np_ <= 0 or div is None or div <= 0:
            continue
        samples.append(div / np_)
    res.forward_payout_samples = samples
    if not samples:
        res.forward_status = STATUS_NO_PAYOUT_HISTORY
        res.forward_note = "无可用分红历史，不做前瞻"
        return res

    raw_median = statistics.median(samples)
    assumed = min(max(raw_median, 0.0), MAX_PAYOUT_ASSUMED)
    res.forward_payout_assumed = assumed
    res.forward_payout_basis = "近3年支付率中位数 %.1f%%" % (raw_median * 100)

    if len(samples) < PAYOUT_WINDOW_YEARS:
        res.forward_warnings.append(
            "支付率样本不连续（最近 %d 个财年仅 %d 年可用）" % (PAYOUT_WINDOW_YEARS, len(samples)))
    # 同一数字只报一次：最新年 >100% 时把「超出净利」与「趋势偏离」合成一条
    trend = abs(samples[0] - raw_median) > PAYOUT_TREND_DEVIATION
    if samples[0] > MAX_PAYOUT_ASSUMED and trend:
        res.forward_warnings.append(
            "最新财年支付率 %.1f%% > 100%%（超出当年净利），与近3年中位数 %.1f%% 偏离超 %d 个百分点，分红政策可能变化"
            % (samples[0] * 100, raw_median * 100, int(PAYOUT_TREND_DEVIATION * 100)))
    elif samples[0] > MAX_PAYOUT_ASSUMED:
        res.forward_warnings.append(
            "最新财年支付率 %.1f%% > 100%%（超出当年净利）" % (samples[0] * 100))
    elif trend:
        res.forward_warnings.append(
            "最新财年支付率 %.1f%% 与近3年中位数 %.1f%% 偏离超 %d 个百分点，分红政策可能变化"
            % (samples[0] * 100, raw_median * 100, int(PAYOUT_TREND_DEVIATION * 100)))
    if raw_median > MAX_PAYOUT_ASSUMED:
        res.forward_warnings.append(
            "近3年支付率中位数 %.1f%% > 100%%，假设截断为 100%%" % (raw_median * 100))
    if res.forward_coverage is not None and res.forward_coverage < FULL_COVERAGE:
        res.forward_warnings.append(
            "一致预期仅 %d 家机构覆盖（样本少）" % res.forward_coverage)
    if is_cyclical:
        res.forward_warnings.append("周期行业，盈利预测波动大，前瞻仅供参考")

    if price is None or price <= 0:
        res.forward_status = STATUS_MISSING_INPUTS
        res.forward_note = "缺少现价，不做前瞻"
        return res

    res.forward_dps = eps * assumed
    res.forward_yield_before_tax = res.forward_dps / price * 100.0
    res.forward_status = STATUS_OK
    res.forward_note = "%d%s EPS %.2f × 支付率 %.1f%% ÷ 现价 %.2f" % (
        forward_year, res.forward_eps_kind, eps, assumed * 100, price)

    if (trailing_yield and trailing_yield > 0
            and (res.forward_yield_before_tax > trailing_yield * (1 + YIELD_DEVIATION)
                 or res.forward_yield_before_tax < trailing_yield * (1 - YIELD_DEVIATION))):
        res.forward_warnings.append("前瞻 %.2f%% 与主口径 %.2f%% 偏离超 50%%，请核对" % (
            res.forward_yield_before_tax, trailing_yield))
    if res.forward_yield_before_tax > YIELD_SANITY_MAX:
        res.forward_warnings.append(
            "前瞻股息率 %.2f%% 超合理上限，疑似数据异常" % res.forward_yield_before_tax)
    return res


def _empty(note: str, status: str) -> ForwardYieldResult:
    return ForwardYieldResult(forward_status=status, forward_note=note)


def estimate_forward_yield(
    *,
    stock_code: str,
    price: Optional[float],
    total_shares: Optional[float],
    latest_year,
    dividend_rows=None,
    financial_rows=None,
    forecast_rows=None,
    is_cyclical: bool = False,
    trailing_yield: Optional[float] = None,
    dividend_fetcher=fetch_dividend_rows,
    financial_fetcher=fetch_financial_rows,
    forecast_fetcher=fetch_earnings_forecast,
) -> ForwardYieldResult:
    """编排层：取数（可注入）→ 纯函数计算。任何一步失败都降级为「不估」，不抛出。"""
    if price is None or price <= 0 or not total_shares:
        return _empty("缺少现价或股本，不做前瞻", STATUS_MISSING_INPUTS)

    if dividend_rows is None:
        dividend_rows = dividend_fetcher(stock_code)
        if dividend_rows is None:
            return _empty(FETCH_FAILED_FMT % "分红明细", STATUS_FETCH_ERROR)
    summary = summarize_dividend_rows(dividend_rows)
    if not summary.latest_year:
        return _empty("无完整财年分红，不做前瞻", STATUS_NO_COMPLETE_YEAR)

    if financial_rows is None:
        financial_rows = financial_fetcher(stock_code)
        if financial_rows is None:
            return _empty(FETCH_FAILED_FMT % "财务数据", STATUS_FETCH_ERROR)

    if forecast_rows is None:
        forecast_rows = forecast_fetcher(stock_code)
        if forecast_rows is None:
            return _empty(FETCH_FAILED_FMT % "盈利预测", STATUS_FETCH_ERROR)

    totals = dividend_totals_by_year(summary.records, total_shares)
    fins = parse_financial_rows(financial_rows or [])
    return compute_forward_yield(
        price=price,
        latest_year=summary.latest_year,
        yearly_dividend_totals=totals,
        net_profit_by_year_map=net_profit_by_year(fins),
        forecast_rows=forecast_rows,
        is_cyclical=is_cyclical,
        trailing_yield=trailing_yield,
        total_shares=total_shares,
    )


__all__ = [
    "ForwardYieldResult",
    "compute_forward_yield",
    "dividend_totals_by_year",
    "estimate_forward_yield",
    "net_profit_by_year",
    "parse_forecast_row",
]
