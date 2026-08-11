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

from backtest_engine import BacktestLookup, run_backtest

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
    lookup, code: str, build_day: date, settle_day: date
) -> float:
    """区间内每笔分红按持仓时长定税后净额，于除权日按当日价格再买入。

    返回 Σ(税后每股分红 / 除权日价格)，即分红复投对总收益的收益率贡献。
    无未来函数：只取公告日 ≤ settle_day 的记录。
    """
    records = lookup.dividends(code, settle_day) or []
    contrib = 0.0
    for rec in records:
        ex = rec.get("ex_dividend_date")
        dps = rec.get("cash_div_per_share")
        if not ex or not dps:
            continue
        ex_date = ex if isinstance(ex, date) else date.fromisoformat(ex)
        if not (build_day <= ex_date <= settle_day):
            continue
        px = lookup.price(code, ex_date)
        if not px:
            continue
        hold_days = (ex_date - build_day).days
        if hold_days > 365:
            tax = TAX_GT_1Y
        elif hold_days >= 30:
            tax = TAX_1M_1Y
        else:
            tax = TAX_LT_1M
        contrib += dps * (1.0 - tax) / px
    return contrib


def portfolio_total_return(
    lookup, codes: Sequence[str], build_day: date, settle_day: date,
    cost: float = COST,
) -> Optional[float]:
    """等权组合区间总收益（价格收益 + 税后分红复投 - 双边成本）。"""
    rets = []
    for code in codes:
        pb = lookup.price(code, build_day)
        ps = lookup.price(code, settle_day)
        if not pb or not ps:
            continue
        price_ret = ps / pb - 1.0
        div_contrib = after_tax_dividend_contrib(lookup, code, build_day, settle_day)
        rets.append((1.0 + price_ret) * (1.0 + div_contrib) - 1.0)
    if not rets:
        return None
    return sum(rets) / len(rets) - 2.0 * cost


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


def run_portfolio(
    lookup, engine_result: dict, top_n: Optional[int] = None,
    cost: float = COST,
) -> dict:
    """对每档每季度：等权（或 TopN）总收益 + 换手率。"""
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
        build = T + timedelta(days=1)  # T+1 建仓（无交易日历则用自然日，引擎已保证 T 为交易日）
        # 取 T 后最近有价格的交易日作为建仓日
        bd = build
        while lookup.price(pools["base"][i][0] if pools["base"][i] else "000001", bd) is None:
            bd += timedelta(days=1)
        for k in layers:
            codes = pools[k][i]
            sel = top_n_codes(lookup, codes, T, top_n) if top_n and codes else codes
            r = portfolio_total_return(lookup, sel, bd, settle, cost)
            quarterly[k].append(r)
            cur = set(sel)
            if prev_pool[k]:
                turnover[k].append(len(cur & prev_pool[k]) / max(len(cur | prev_pool[k]), 1))
            else:
                turnover[k].append(None)
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


def annualized(total: float, n_quarters: int) -> float:
    if n_quarters <= 0:
        return 0.0
    years = n_quarters / 4.0
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


def sharpe(rets: Sequence[Optional[float]], rf: float = 0.02) -> Optional[float]:
    vs = [r for r in rets if r is not None]
    if len(vs) < 2:
        return None
    mean = sum(vs) / len(vs)
    std = math.sqrt(sum((v - mean) ** 2 for v in vs) / (len(vs) - 1))
    if std == 0:
        return None
    return (mean - rf / 4.0) / std * math.sqrt(4.0)


def sortino(rets: Sequence[Optional[float]], rf: float = 0.02) -> Optional[float]:
    vs = [r for r in rets if r is not None]
    if len(vs) < 2:
        return None
    mean = sum(vs) / len(vs)
    downside = [v for v in vs if v < rf / 4.0]
    dstd = math.sqrt(sum((v - rf / 4.0) ** 2 for v in downside) / len(vs)) if downside else 0.0
    if dstd == 0:
        return None
    return (mean - rf / 4.0) / dstd * math.sqrt(4.0)


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


def performance_metrics(quarterly: Dict[str, List[Optional[float]]]) -> dict:
    out = {}
    for k, rets in quarterly.items():
        vs = [r for r in rets if r is not None]
        total = cum(rets)
        out[k] = {
            "cumulative": total,
            "annualized": annualized(total, len(vs)),
            "volatility": (lambda s: s)(
                math.sqrt(sum((r - sum(vs) / len(vs)) ** 2 for r in vs) / (len(vs) - 1))
                if len(vs) > 1 else 0.0
            ),
            "max_drawdown": max_drawdown(rets),
            "sharpe": sharpe(rets),
            "sortino": sortino(rets),
            "calmar": calmar(rets),
            "win_rate": win_rate(rets),
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
