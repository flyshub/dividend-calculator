"""
全 A 四层漏斗回测 — T3 因子计算层（纯函数，零网络请求）

把现网四层漏斗口径历史化为纯函数：输入 = T 日快照数据 + 可注入 lookup
（T4 用真实 DB 实现，测试注入合成数据），输出 = 因子值。与现网 src 计算
逻辑逐字段一致（数据铁律 #3），保证回测在历史调仓日无未来函数地复算。

无未来函数铁律（每个因子内部用 asof=T 过滤，注释标明时间窗口）：
  - 分红记录：只使用 announce_date ≤ T（函数内二次过滤；lookup 已按同条件预过滤）
  - 完整财年分红（real_dividend_yield）：
      取 report_date 12-31 的最新财年，该财年内全部报告期（含中期分配）的
      现金分红合计 —— 对齐 dividend._parse_fhps_detail（#37 M4：仅 12 月
      报告期构成完整财年，其余月份派息计入同一财年总额）
  - TTM 分红（ttm_dividend_yield）：
      按 ex_dividend_date 落在 (T-365, T] 的实际派发 —— 对齐
      utils.compute_ttm_dividend（#19 口径，起点开区间、终点闭区间）
  - 财报（pr / sustainability）：只使用 report_date ≤ T 的 12-31 年报
      （lookup['roe_latest'] / lookup['finance'] 已按此过滤）
  - 行情：PE / 价格 / 总股本取 T 当日或之前最近快照（lookup 按 asof 过滤）

lookup 注入契约（T4 用真实 DB 数据实现；测试可注入）：
  'dividends':       code, asof -> [{announce_date, report_date, ex_dividend_date,
                                     cash_div_per_share}, ...]   # 每股现金分红（元）
  'pe_ttm':          code, asof -> float | None     # T 当日或之前最近 PE_TTM
  'total_shares':    code, asof -> float | None     # 总股本（Index 73；A+H 必须用总股本）
  'price':           code, asof -> float | None     # T 当日或之前最近股价
  'roe_latest':      code, asof -> float | None     # 报告期12-31 ≤ asof 最新 ROE（百分数）
  'finance':         code, asof -> dict | None      # 报告期12-31 ≤ asof 最新财年财务快照
                                                    # （键名 = AnnualFinancial 字段名）
  'price_change_1y': code, asof -> float | None     # 近1年涨跌幅（小数，如 -0.3）
  'top10_holding':   code, asof -> float | None     # 前十大股东持股比例（小数，如 0.6）
  'industry':        code, asof -> str              # 行业（可选键；pr 周期警示 / sustainability 路由用）

注：'industry' 为契约扩展键（任务契约未列，但 pr 周期股警示与 sustainability
银行/周期/防御路由必需），T4 实现时从 DB 行业快照提供；缺省按空字符串处理。
"""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional

from .pr_calculator import classify_industry, compute_basic_pr
from .sustainability_calculator import (
    AnnualFinancial,
    CUT_WINDOW_YEARS,
    DividendHistory,
    assess_sustainability,
)


@dataclass
class FullYearDividend:
    """最新完整财年现金分红（对齐 dividend._parse_fhps_detail 返回值的前半部分）。"""
    total_dividend: float        # 现金分红总额（元）= 每股合计 × 总股本
    latest_year: Optional[str]   # 财年（如 '2024'）；无有效记录为 None
    dps: float                   # 该财年每股现金分红合计（元）


@dataclass
class PRFactor:
    """市赚率因子（对齐 pr.calculate_pr 核心口径 + 周期股警示）。"""
    pr: Optional[float]          # 基础市赚率 = PE_TTM / ROE（round 2 位；缺失/ROE≤0 为 None）
    pe_ttm: Optional[float]      # T 当日 PE_TTM
    roe_latest: Optional[float]  # 报告期 12-31 ≤ T 最新 ROE（百分数）
    is_cyclical: bool            # 周期行业警示（对齐 pr_calculator.classify_industry）
    is_tech: bool
    is_growth: bool
    pr_warning: str              # 修正市赚率适用性提示


def _dstr(d) -> str:
    """T 归一化为 YYYY-MM-DD 字符串（announce_date 比较用）。"""
    return d.isoformat()[:10]


def _industry(code, T, lookup) -> str:
    fn = lookup.get("industry")
    return fn(code, T) if fn else ""


def _latest_full_year_dividend(code, T, lookup) -> FullYearDividend:
    """最新完整财年现金分红总额（口径对齐 dividend._parse_fhps_detail）。

    时间窗口：只使用 announce_date ≤ T 的分红；完整财年 = 报告期 12-31 的
    最新财年，该财年内全部报告期（含中期分配）合计。无有效记录 → 0.0/None。
    """
    records = lookup["dividends"](code, T) or []
    total_shares = lookup["total_shares"](code, T)
    if total_shares is None or total_shares <= 0:
        return FullYearDividend(0.0, None, 0.0)

    t_str = _dstr(T)
    # yearly[year] = [该年每股现金分红合计, 是否有 12-31 年报]（与现网同构）
    yearly: Dict[int, List] = {}
    for rec in records:
        announce = str(rec.get("announce_date") or "")[:10]
        if len(announce) == 10 and announce > t_str:
            continue  # 无未来函数：公告日 > T 的预案不可见
        rp = str(rec.get("report_date") or "")[:10]
        if len(rp) < 10:
            continue  # 对齐现网：报告期缺失跳过
        dps = float(rec.get("cash_div_per_share") or 0)
        if dps <= 0:
            continue  # 对齐现网：dp10 NaN/<=0 过滤
        try:
            y, m = int(rp[:4]), int(rp[5:7])
        except ValueError:
            continue
        bucket = yearly.setdefault(y, [0.0, False])
        bucket[0] += dps
        bucket[1] = bucket[1] or (m == 12)

    if not yearly:
        return FullYearDividend(0.0, None, 0.0)

    # 选最新完整财年：优先有 12-31 年报的；否则最新有数据的（对齐现网降级）
    target = None
    for y in sorted(yearly, reverse=True):
        if yearly[y][1]:
            target = y
            break
    if target is None:
        target = max(yearly)

    total_dps = yearly[target][0]
    return FullYearDividend(total_dps * total_shares, str(target), total_dps)


def real_dividend_yield(code, T, lookup) -> Optional[float]:
    """真实股息率（总额法，%）—— 四层漏斗第 1 层。

    口径对齐 dividend.calculate_dividend_yield / _parse_fhps_detail：
    最新完整财年现金分红总额 / T 日总市值 × 100。
    时间窗口：公告日 ≤ T 的最新完整财年（report_date 12-31）。
    无分红记录 → 0.0（对齐现网"无有效分红"返回 0 收益率）；
    price / 总股本缺失 → None（无法计算总市值，对齐现网 get_stock_info 失败路径）。
    """
    shares = lookup["total_shares"](code, T)
    price = lookup["price"](code, T)
    if shares is None or price is None:
        return None
    fy = _latest_full_year_dividend(code, T, lookup)
    market_cap = price * shares
    if market_cap <= 0:
        return 0.0  # 对齐 calculate_dividend_yield：总市值 ≤ 0 → 0.0
    return fy.total_dividend / market_cap * 100


def ttm_dividend_yield(code, T, lookup) -> Optional[float]:
    """TTM 股息率（%）—— 四层漏斗第 2 层。

    口径对齐 utils.compute_ttm_dividend（#19）：近 12 个月按除权除息日实际
    派发现金分红总额 / T 日总市值 × 100。
    时间窗口：ex_dividend_date ∈ (T-365, T]（起点开区间、终点闭区间），
    且 announce_date ≤ T（无未来函数）；窗口内无派息 → None（对齐现网
    _ttm_total None → _ttm_yield None）。
    """
    shares = lookup["total_shares"](code, T)
    price = lookup["price"](code, T)
    if shares is None or price is None:
        return None
    records = lookup["dividends"](code, T) or []
    cutoff = T - timedelta(days=365)
    t_str = _dstr(T)

    total_dps = 0.0
    count = 0
    for rec in records:
        announce = str(rec.get("announce_date") or "")[:10]
        if len(announce) == 10 and announce > t_str:
            continue  # 无未来函数
        ex = str(rec.get("ex_dividend_date") or "")[:10]
        if len(ex) < 10:
            continue  # 对齐现网：ex_dividend_date 缺失跳过
        try:
            d = date.fromisoformat(ex)
        except ValueError:
            continue
        if cutoff < d <= T:
            total_dps += float(rec.get("cash_div_per_share") or 0)
            count += 1

    if count == 0:
        return None
    market_cap = price * shares
    if market_cap <= 0:
        return None  # 对齐现网：总市值 > 0 才出 yield
    return (total_dps * shares) / market_cap * 100


def pr(code, T, lookup) -> PRFactor:
    """市赚率 —— 四层漏斗第 3 层。

    口径对齐 pr_calculator.compute_basic_pr：PR = PE_TTM / ROE（ROE 为百分号前
    数字直接除，无 ×100），round(pe/roe, 2)；ROE 缺失或 ≤ 0 → None。
    周期股警示规则保留（classify_industry：周期 > 科技 > 成长，重叠时只报最优先一类）。
    时间窗口：PE_TTM 取 T 当日快照；ROE 取报告期 12-31 ≤ T 最新年报（lookup 过滤）。
    """
    pe_ttm = lookup["pe_ttm"](code, T)
    roe_latest = lookup["roe_latest"](code, T)
    basic = compute_basic_pr(pe_ttm, roe_latest)
    is_cyclical, is_tech, is_growth, warning = classify_industry(_industry(code, T, lookup))
    return PRFactor(pr=basic, pe_ttm=pe_ttm, roe_latest=roe_latest,
                    is_cyclical=is_cyclical, is_tech=is_tech,
                    is_growth=is_growth, pr_warning=warning)


def _to_annual_financial(snapshot: Optional[dict]) -> Optional[AnnualFinancial]:
    """财务快照 dict → AnnualFinancial（键名 = AnnualFinancial 字段名）。

    快照为报告期 12-31 ≤ T 的最新财年行（lookup 保证）；缺失或无财年标记 → None
    （对齐现网 sustainability.assess_for_stock 的 latest=None 降级路径）。
    """
    if not snapshot:
        return None
    year = snapshot.get("year")
    if year is None:
        return None
    kwargs = {f: snapshot.get(f) for f in AnnualFinancial.__dataclass_fields__}
    kwargs["year"] = int(year)
    return AnnualFinancial(**kwargs)


def _dividend_history(code, T, lookup, latest_year, total_shares) -> DividendHistory:
    """分红记录 → DividendHistory（口径对齐 sustainability.aggregate_dividend_history）。

    时间窗口：只使用 announce_date ≤ T 的分红记录。
    全部记录（含中期分配）参与总额/连续年数；仅年报（report_date 12-31）参与
    ever_cut 相邻年削减比较（#39 M6：避免中期分配 vs 上年年报误判削减）。
    """
    if not total_shares:
        return DividendHistory(consecutive_years=0, ever_cut=False,
                               latest_year_amount=None, history_mean_amount=None)
    records = lookup["dividends"](code, T) or []
    t_str = _dstr(T)

    year_amount: Dict[str, float] = {}
    annual_amount: Dict[str, float] = {}
    for rec in records:
        announce = str(rec.get("announce_date") or "")[:10]
        if len(announce) == 10 and announce > t_str:
            continue  # 无未来函数
        rp = str(rec.get("report_date") or "")[:10]
        if len(rp) < 10:
            continue
        y = rp[:4]
        amount = float(rec.get("cash_div_per_share") or 0) * total_shares
        year_amount[y] = year_amount.get(y, 0.0) + amount
        if rp[5:7] == "12":  # 仅年报（12-31）参与削减比较，对齐现网 label 判定
            annual_amount[y] = annual_amount.get(y, 0.0) + amount

    if not year_amount:
        return DividendHistory(consecutive_years=0, ever_cut=False,
                               latest_year_amount=None, history_mean_amount=None)

    years_sorted = sorted(year_amount, reverse=True)
    target_year = latest_year if (latest_year and latest_year in year_amount) else years_sorted[0]

    # 连续年数：从 target_year 向前逐年递减，遇中断即停
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

    # 近3年均值（target_year 之前最近 3 年）—— 突击分红判断用
    history_3y_mean = None
    try:
        tgt_int = int(target_year)
        recent3 = [yy for yy in years_sorted if yy != target_year and int(yy) < tgt_int][:3]
    except ValueError:
        recent3 = history_years[:3]
    if recent3:
        history_3y_mean = sum(year_amount[yy] for yy in recent3) / len(recent3)

    # 曾削减：近 CUT_WINDOW_YEARS 年窗口内相邻年报降幅 > 30%（只比年报口径）
    ever_cut = False
    window_start = int(target_year) - (CUT_WINDOW_YEARS - 1)
    asc = sorted(annual_amount)
    for i in range(1, len(asc)):
        prev_y, cur_y = asc[i - 1], asc[i]
        if int(cur_y) < window_start:
            continue
        prev, cur = annual_amount[prev_y], annual_amount[cur_y]
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


def sustainability(code, T, lookup):
    """股息可持续性 —— 四层漏斗第 4 层。

    口径对齐 sustainability_calculator.assess_sustainability：六维判据 + 银行专项
    （CAR / 净息差 / 不良率 / 拨贷比）+ 情境红旗（被动高股息 price_change_1y、
    突击分红、一股独大 top10_holding）。返回现网同构的 SustainabilityResult
    （verdict: 可持续 / 偏弱 / 不可持续；股息率 ≤ 4% → 未评估）。
    时间窗口：分红总额取公告日 ≤ T 的最新完整财年；财务快照 / price_change_1y /
    top10_holding 均取报告期或时点 ≤ T（lookup 按 asof 过滤）；current_year = T.year
    （数据新鲜度标注用，与现网 #13 一致）。
    """
    shares = lookup["total_shares"](code, T)
    price = lookup["price"](code, T)
    fy = _latest_full_year_dividend(code, T, lookup)
    dividend_total = fy.total_dividend

    yield_before_tax = None
    if shares and price and shares > 0 and price > 0:
        yield_before_tax = dividend_total / (price * shares) * 100

    return assess_sustainability(
        dividend_yield_before_tax=yield_before_tax,
        dividend_total=dividend_total,
        latest=_to_annual_financial(lookup["finance"](code, T)),
        history=_dividend_history(code, T, lookup, fy.latest_year, shares),
        industry=_industry(code, T, lookup),
        price_change_1y=lookup["price_change_1y"](code, T),
        top10_holding=lookup["top10_holding"](code, T),
        current_year=T.year,
    )
