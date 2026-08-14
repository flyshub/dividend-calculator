#!/usr/bin/env python3
"""T5 组合构建与绩效评估（方案 V3 第 6/7 节，issue #88）

消费 T4 引擎（backtest_engine.run_backtest）的分层入选池，叠加：
- 税后分红再投资：总收益 = 价格收益（不复权）+ Σ(税后分红 于除权日按当日价格再买入)
- 三档税率按持仓时长判定（>1年 0% / 1月~1年 10% / <1月 20%）
- 入选池等权 + TopN（按真实股息率降序）对比
- 双边 0.3% 交易成本（季度调仓全换手，进出各 0.3%）
- 绩效指标：累计/年化收益、波动、最大回撤、夏普、卡玛、索提诺、胜率、换手
- 基准对比：中证红利全收益（H00922）为主、沪深300全收益（H00300）次

数据铁律：基准从 backtest.db 的 index_daily 表读取；分红用 T4 lookup 的
dividends()（公告日 ≤ asof 过滤，无未来函数）；税率口径与 src 三档一致。
"""

import json
import math
import sqlite3
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from backtest_engine import BacktestLookup, run_backtest, _d
from src.backtest_factors import ttm_dividend_yield

DB_PATH = "data/backtest.db"

# 三档税率（与 CLAUDE.md 口径一致）
TAX_GT_1Y = 0.0
TAX_1M_1Y = 0.10
TAX_LT_1M = 0.20

# 交易成本（双边，与方案 V3 一致）
COST = 0.003

# 基准指数（index_daily 表内代码）
BENCH_MAIN = "H00922"   # 中证红利全收益
BENCH_ALT = "H00300"    # 沪深300全收益


def after_tax_dividend_contrib(
    lookup, code: str, build_day: date, settle_day: date,
    tax_override: Optional[float] = None,
) -> float:
    """区间内每笔分红按持仓时长定税后净额，于除权日按当日价格再买入。

    返回 Σ(税后每股分红 / 除权日价格)，即分红复投对总收益的收益率贡献。
    无未来函数：只取公告日 ≤ settle_day 的记录。

    tax_override: 若非 None，所有分红按此单一税率计算（用于 hfq 无税上界对照，
    tax_override=0.0 即数学等价于 hfq 全收益）。

    T8 #113 已知限制：税率按 build_day → ex_date 的持仓时长定档，每期独立结算。
    跨期继承持仓时不重置建仓日，因此实际持仓 >1 年的分红可能被误判为 1月-1年档
    （10% 而非 0%），税拖累被略微高估。完整 FIFO 需跟踪每只股票的最早建仓日，
    属结构性改造（当前组合模型每期独立结算），#113 暂保留限制标注。
    """
    records = lookup.dividends(code, settle_day) or []
    # T12 #130 M-10：按除权日排序遍历，送转后现金分红按放大后股数计。
    # 例：先 10送10（股数×2）再每股派 d 元 → 实际现金 = 2×d（保守方向修正）。
    dated = []
    for rec in records:
        ex = rec.get("ex_dividend_date")
        dps = rec.get("cash_div_per_share")
        br = rec.get("bonus_ratio") or 0.0
        tr = rec.get("trans_ratio") or 0.0
        # T12 #130：dps=0 但有送转的记录也要处理（放大后续股数）
        if not ex or (not dps and not br and not tr):
            continue
        ex_date = ex if isinstance(ex, date) else date.fromisoformat(ex)
        dated.append((ex_date, dps or 0.0, rec))
    dated.sort(key=lambda x: x[0])
    shares = 1.0
    contrib = 0.0
    for ex_date, dps, rec in dated:
        if not (build_day <= ex_date <= settle_day):
            continue
        br = rec.get("bonus_ratio") or 0.0
        tr = rec.get("trans_ratio") or 0.0
        split = 1.0 + (br + tr) / 10.0
        px = lookup.price(code, ex_date)
        if not px:
            # 无除权日价：送转仍放大股数，现金分红跳过（保守）
            shares *= split
            continue
        if tax_override is not None:
            tax = tax_override
        else:
            hold_days = (ex_date - build_day).days
            if hold_days > 365:
                tax = TAX_GT_1Y
            elif hold_days >= 30:
                tax = TAX_1M_1Y
            else:
                tax = TAX_LT_1M
        # 当日除权记录：送转放大股数；现金分红按当日持股数发放
        if dps > 0:
            contrib += shares * dps * (1.0 - tax) / px
        shares *= split
    return contrib


def portfolio_total_return(
    lookup, codes: Sequence[str], build_day: date, settle_day: date,
    cost: float = COST, tax_override: Optional[float] = None,
    weights: Optional[Sequence[float]] = None,
    turnover_ratio: float = 1.0,
) -> Optional[float]:
    """组合区间总收益（价格收益 + 税后分红复投 - 按实际换手缩放的成本）。

    tax_override: 非 None 时所有分红按此税率计算（hfq 无税对照用 0.0）。
    weights: 个股权重序列（与 codes 等长，归一化后应用）；None = 等权。
    turnover_ratio: 本期实际换手比例（0=零换手继承上期持仓,1=全换手），
        T7 #112：成本按 turnover_ratio × 双边成本计提，避免 base 零换手被收满额成本。
    """
    rets = []
    wts = []
    for i, code in enumerate(codes):
        pb = lookup.price(code, build_day)
        ps = lookup.price(code, settle_day)
        if not pb or not ps:
            continue
        # T10 #115：累积持有期送转因子（与引擎 portfolio_return 同口径）
        split_factor = 1.0
        divs = getattr(lookup, "dividends", None)
        if divs:
            for rec in (divs(code, settle_day) or []):
                ex = rec.get("ex_dividend_date")
                if not ex:
                    continue
                ex_d = _d(ex) if isinstance(ex, str) else ex
                if build_day < ex_d <= settle_day:
                    br = rec.get("bonus_ratio") or 0.0
                    tr = rec.get("trans_ratio") or 0.0
                    if br or tr:
                        split_factor *= (1.0 + (br + tr) / 10.0)
        price_ret = split_factor * ps / pb - 1.0
        div_contrib = after_tax_dividend_contrib(
            lookup, code, build_day, settle_day, tax_override=tax_override)
        rets.append((1.0 + price_ret) * (1.0 + div_contrib) - 1.0)
        wts.append(weights[i] if weights is not None else 1.0)
    if not rets:
        return None
    s = sum(wts)
    wts = [w / s for w in wts] if s > 0 else [1.0 / len(wts)] * len(wts)
    scaled_cost = 2.0 * cost * turnover_ratio
    return sum(r * w for r, w in zip(rets, wts)) - scaled_cost


def top_n_codes(lookup, codes: Sequence[str], T: date, n: int) -> List[str]:
    """按真实股息率（完整财年分红/市值，T 时点）降序取前 n 只。"""
    scored = []
    for code in codes:
        div = lookup.dividends(code, T) or []
        px = lookup.price(code, T)
        if not px:
            continue
        fy_total = 0.0
        latest_fy = None
        for rec in div:
            rp = rec.get("report_date")
            if not rp:
                continue
            rp_s = rp if isinstance(rp, str) else rp.isoformat()
            if not rp_s.endswith("-12-31"):
                continue
            fy = rp_s[:4]
            if latest_fy is None or fy > latest_fy:
                latest_fy = fy
        if latest_fy is None:
            continue
        for rec in div:
            rp = rec.get("report_date")
            rp_s = rp if isinstance(rp, str) else rp.isoformat()
            if rp_s.startswith(latest_fy):
                fy_total += rec.get("cash_div_per_share") or 0.0
        scored.append((fy_total / px, code))
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:n]]


def _compute_weights(lookup, codes: Sequence[str], T: date,
                     weighting: str) -> Optional[List[float]]:
    """按加权方式计算个股权重序列。

    weighting: "equal" = None（让 portfolio_total_return 走等权分支）
               "cap" = 当日价格 × 总股本（total_shares=1.0 时退化为等价价格加权）
               "yield" = ttm 股息率（高分红股权重高）
    """
    if weighting == "equal":
        return None
    if weighting == "cap":
        return [(lookup.price(c, T) or 0.0) * (lookup.total_shares(c, T) or 0.0)
                for c in codes]
    if weighting == "yield":
        return [ttm_dividend_yield(c, T, lookup) or 0.0 for c in codes]
    return None


def run_portfolio(
    lookup, engine_result: dict, top_n: Optional[int] = None,
    cost: float = COST, tax_override: Optional[float] = None,
    weighting: str = "equal",
) -> dict:
    """对每档每季度：等权（或 TopN/加权）总收益 + 换手率。

    tax_override: 非 None 时所有分红按此税率计算（hfq 无税上界对照用 0.0）。
    weighting: "equal"|"cap"|"yield"（市值加权需真实 total_shares，
        当前 1.0 近似下退化为价格加权，会在报告标注近似）。
    """
    rebalance = engine_result["rebalance_dates"]
    pools = engine_result["pools"]
    layers = ("base", "l2", "l3", "l4", "full")

    quarterly = {k: [] for k in layers}
    turnover = {k: [] for k in layers}
    prev_pool = {k: set() for k in layers}

    for i, T in enumerate(rebalance):
        settle = rebalance[i + 1] if i + 1 < len(rebalance) else None
        if settle is None:
            for k in layers:
                quarterly[k].append(None)
                turnover[k].append(None)
            continue
        build = T + timedelta(days=1)  # T+1 建仓
        # T6 #111：用交易日历找 T 后第一个交易日（替代探针股 hack）
        trading_days = getattr(lookup, "trading_days", None)
        if trading_days:
            future = [d for d in trading_days if d > T]
            bd = future[0] if future else build
        else:
            bd = build
        for k in layers:
            codes = pools[k][i]
            sel = top_n_codes(lookup, codes, T, top_n) if top_n and codes else codes
            wts = _compute_weights(lookup, sel, T, weighting) if sel else None
            # T7 #112：实际换手比例（0=零换手继承,1=全换手），成本按此缩放
            cur = set(sel)
            if prev_pool[k]:
                union = max(len(cur | prev_pool[k]), 1)
                held = len(cur & prev_pool[k])
                # 单边换手率 = (新买入数 + 卖出数) / 2 / 池大小；近似 = 1 - 交集/并集
                actual_turnover = 1.0 - held / union
            else:
                actual_turnover = 1.0  # 首期全建仓
            r = portfolio_total_return(lookup, sel, bd, settle, cost,
                                       tax_override=tax_override, weights=wts,
                                       turnover_ratio=actual_turnover)
            quarterly[k].append(r)
            turnover[k].append(actual_turnover if prev_pool[k] else None)
            prev_pool[k] = cur

    return {
        "rebalance_dates": rebalance,
        "quarterly_returns": quarterly,
        "turnover": turnover,
    }


def cum(rets: Sequence[Optional[float]]) -> float:
    acc = 1.0
    for r in rets:
        if r is None:
            continue
        acc *= (1.0 + r)
    return acc - 1.0


def annualized(total: float, n_periods: int, periods_per_year: int = 4) -> float:
    """年化收益。periods_per_year=4（季度）/12（月）/2（半年）。

    T4 #109：频率扫描须按实际期长年化，否则月调仓被高估 n/4 年数、半年被低估。
    """
    if n_periods <= 0:
        return 0.0
    years = n_periods / periods_per_year
    return (1.0 + total) ** (1.0 / years) - 1.0


def max_drawdown(rets: Sequence[Optional[float]]) -> float:
    peak = 1.0
    nav = 1.0
    mdd = 0.0
    for r in rets:
        if r is None:
            continue
        nav *= (1.0 + r)
        peak = max(peak, nav)
        mdd = max(mdd, (peak - nav) / peak)
    return mdd


def sharpe(rets: Sequence[Optional[float]], rf: float = 0.03,
           periods_per_year: int = 4) -> Optional[float]:
    """夏普比率。periods_per_year=4（季度）/12（月）/2（半年）。

    rf=3% 近似中国 10 年期国债 2013-2026 区间均值（2.5%-4.5%，约 3.2%）。
    T8 #113：旧 rf=2% 偏低，高估夏普；改 3% 更贴近无风险利率实际水平。
    """
    vs = [r for r in rets if r is not None]
    if len(vs) < 2:
        return None
    mean = sum(vs) / len(vs)
    std = math.sqrt(sum((v - mean) ** 2 for v in vs) / (len(vs) - 1))
    if std == 0:
        return None
    return (mean - rf / periods_per_year) / std * math.sqrt(periods_per_year)


def sortino(rets: Sequence[Optional[float]], rf: float = 0.03,
            periods_per_year: int = 4) -> Optional[float]:
    vs = [r for r in rets if r is not None]
    if len(vs) < 2:
        return None
    mean = sum(vs) / len(vs)
    target = rf / periods_per_year
    downside = [v for v in vs if v < target]
    dstd = math.sqrt(sum((v - target) ** 2 for v in downside) / len(vs)) if downside else 0.0
    if dstd == 0:
        return None
    return (mean - target) / dstd * math.sqrt(periods_per_year)


def calmar(rets: Sequence[Optional[float]]) -> Optional[float]:
    mdd = max_drawdown(rets)
    if mdd == 0:
        return None
    total = cum(rets)
    n = len([r for r in rets if r is not None])
    return annualized(total, n) / mdd


def win_rate(rets: Sequence[Optional[float]]) -> Optional[float]:
    vs = [r for r in rets if r is not None]
    if not vs:
        return None
    return sum(1 for r in vs if r > 0) / len(vs)


def downside_risk(rets: Sequence[Optional[float]], rf: float = 0.03,
                  periods_per_year: int = 4) -> Optional[float]:
    """下行风险（仅对负偏离求标准差，年化）。"""
    vs = [r for r in rets if r is not None]
    if not vs:
        return None
    target = rf / periods_per_year
    downside = [(v - target) for v in vs if v < target]
    if not downside:
        return 0.0
    var = sum(d * d for d in downside) / len(vs)
    return math.sqrt(var) * math.sqrt(periods_per_year)


def profit_loss_ratio(rets: Sequence[Optional[float]]) -> Optional[float]:
    """盈亏比 = 平均盈利期收益 / 平均亏损期损失（绝对值）。"""
    vs = [r for r in rets if r is not None]
    gains = [r for r in vs if r > 0]
    losses = [-r for r in vs if r < 0]  # 取正
    if not gains or not losses:
        return None
    avg_gain = sum(gains) / len(gains)
    avg_loss = sum(losses) / len(losses)
    return avg_gain / avg_loss if avg_loss > 0 else None


def positive_years(rets: Sequence[Optional[float]],
                   rebalance_dates: Sequence[date]) -> int:
    """年度正收益年数（按日历年聚合季度收益，年收益 >0 即记）。"""
    by_year: Dict[int, float] = {}
    for r, d in zip(rets, rebalance_dates):
        if r is None:
            continue
        by_year.setdefault(d.year, 1.0)
        by_year[d.year] *= (1.0 + r)
    return sum(1 for v in by_year.values() if v > 1.0)


def avg_pool_size(pools: Dict[str, List[List[str]]], layer: str) -> float:
    """季均入选只数（layer 层）。"""
    sizes = [len(codes) for codes in pools.get(layer, []) if codes is not None]
    return sum(sizes) / len(sizes) if sizes else 0.0


def avg_turnover(turnover_series: Sequence[Optional[float]]) -> Optional[float]:
    """平均换手率（跳过首期 None）。"""
    vs = [t for t in turnover_series if t is not None]
    return sum(vs) / len(vs) if vs else None


def performance_metrics(quarterly: Dict[str, List[Optional[float]]],
                        rebalance_dates: Optional[Sequence[date]] = None) -> dict:
    """绩效指标。rebalance_dates 提供时年化按日历跨度（T9 #114），

    空仓期计入年化分母（不再因漏斗空仓被排除而高估年化）。
    无 rebalance_dates 时回退 n_periods/ppy（向后兼容）。
    """
    # T9 #114：日历跨度年数（首→末调仓日）
    years_calendar = None
    if rebalance_dates and len(rebalance_dates) >= 2:
        span_days = (rebalance_dates[-1] - rebalance_dates[0]).days
        years_calendar = span_days / 365.25 if span_days > 0 else None

    out = {}
    for k, rets in quarterly.items():
        vs = [r for r in rets if r is not None]
        total = cum(rets)
        if years_calendar is not None and years_calendar > 0:
            ann = (1.0 + total) ** (1.0 / years_calendar) - 1.0
        else:
            ann = annualized(total, len(vs))
        out[k] = {
            "cumulative": total,
            "annualized": ann,
            "volatility": (lambda s: s)(
                math.sqrt(sum((r - sum(vs) / len(vs)) ** 2 for r in vs) / (len(vs) - 1))
                if len(vs) > 1 else 0.0
            ),
            "max_drawdown": max_drawdown(rets),
            "sharpe": sharpe(rets),
            "sortino": sortino(rets),
            "calmar": calmar(rets),
            "win_rate": win_rate(rets),
            "downside_risk": downside_risk(rets),
            "profit_loss_ratio": profit_loss_ratio(rets),
        }
    return out


def load_benchmark(conn: sqlite3.Connection, code: str,
                   rebalance_dates: Sequence[date]) -> List[Optional[float]]:
    """基准指数季度收益（对齐 rebalance_dates）。"""
    rows = conn.execute(
        "SELECT date, close FROM index_daily WHERE code=? ORDER BY date", (code,)
    ).fetchall()
    if not rows:
        return [None] * len(rebalance_dates)
    series = {date.fromisoformat(d): c for d, c in rows}
    out = []
    for i, T in enumerate(rebalance_dates):
        if i + 1 >= len(rebalance_dates):
            out.append(None)
            break
        p0 = _latest_on_or_before(series, T)
        p1 = _latest_on_or_before(series, rebalance_dates[i + 1])
        out.append(p1 / p0 - 1.0 if p0 and p1 else None)
    return out


def _latest_on_or_before(series: Dict[date, float], d: date) -> Optional[float]:
    best = None
    for dd, c in series.items():
        if dd <= d and (best is None or dd > best[0]):
            best = (dd, c)
    return best[1] if best else None


def main() -> None:
    lookup = BacktestLookup(DB_PATH)
    res = run_backtest(lookup)
    for top_n in (None, 10, 20):
        pf = run_portfolio(lookup, res, top_n=top_n)
        metrics = performance_metrics(pf["quarterly_returns"])
        label = "等权" if top_n is None else f"Top{top_n}"
        print(f"\n== 组合：{label}（税后分红复投 + 双边成本 {COST*100:.1f}%）==")
        for k in ("base", "l2", "l3", "l4", "full"):
            m = metrics[k]
            print(
                f"  {k:>4s} 累计{m['cumulative']*100:+6.2f}% "
                f"年化{m['annualized']*100:+5.2f}% 波动{m['volatility']*100:5.2f}% "
                f"回撤{m['max_drawdown']*100:5.2f}% 夏普{m['sharpe'] if m['sharpe'] is not None else float('nan'):5.2f} "
                f"卡玛{m['calmar'] if m['calmar'] is not None else float('nan'):5.2f} "
                f"胜率{m['win_rate']*100 if m['win_rate'] is not None else float('nan'):5.1f}%"
            )
    conn = sqlite3.connect(DB_PATH)
    bench_main = load_benchmark(conn, BENCH_MAIN, res["rebalance_dates"])
    bench_alt = load_benchmark(conn, BENCH_ALT, res["rebalance_dates"])
    print(f"\n== 基准对比（同期累计）==")
    print(f"  中证红利全收益 H00922: {cum(bench_main)*100:+.2f}%")
    print(f"  沪深300全收益 H00300: {cum(bench_alt)*100:+.2f}%")
    full_ret = performance_metrics(res["quarterly_returns"])["full"]
    bm = cum(bench_main)
    print(f"\n  全漏斗 vs 中证红利全收益超额: {(full_ret['cumulative'] - bm)*100:+.2f}%")


if __name__ == "__main__":
    main()
