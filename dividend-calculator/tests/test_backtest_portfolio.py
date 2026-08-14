#!/usr/bin/env python3
"""T5 组合构建与绩效评估测试（issue #88）"""
import math
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from backtest_portfolio import (
    after_tax_dividend_contrib,
    portfolio_total_return,
    top_n_codes,
    run_portfolio,
    performance_metrics,
    max_drawdown,
    sharpe,
    sortino,
    downside_risk,
    calmar,
    win_rate,
    cum,
    annualized,
    load_benchmark,
)


class FakeLookup:
    """最小 lookup：price 用注入的序列，dividends 返回固定记录。"""

    def __init__(self, prices=None, dividends=None, trading_days=None):
        self._prices = prices or {}
        self._div = dividends or {}
        self.trading_days = trading_days  # T6 #111：交易日历建仓日

    def price(self, code, asof):
        series = self._prices.get(code, [])
        best = None
        for d, c in sorted(series):
            if d <= asof:
                best = c
        return best

    def dividends(self, code, asof):
        return self._div.get(code, [])


def _mk_div(ex_date, dps, report_date="2023-12-31", bonus=0.0, trans=0.0):
    return {
        "announce_date": ex_date,
        "report_date": report_date,
        "ex_dividend_date": ex_date,
        "cash_div_per_share": dps,
        "bonus_ratio": bonus,
        "trans_ratio": trans,
    }


def test_tax_tiers_by_holding_duration():
    """三档税率：>1年 0% / 1月-1年 10% / <1月 20%。"""
    build = date(2023, 1, 1)
    px_series = [(date(2023, 1, 1), 10.0), (date(2023, 6, 1), 10.0),
                 (date(2024, 2, 1), 10.0)]
    lookup = FakeLookup(
        prices={"x": px_series},
        dividends={
            "x": [
                _mk_div(date(2024, 2, 1), 1.0),   # 持有 >1年（397天）→ 0%
                _mk_div(date(2023, 6, 1), 1.0),   # 持有 151天 → 10%
                _mk_div(date(2023, 1, 15), 1.0),  # 持有 14天 → 20%
            ]
        },
    )
    # 单笔贡献 = dps × (1-tax) / px
    expect = 1.0 / 10.0 + 0.9 / 10.0 + 0.8 / 10.0
    got = after_tax_dividend_contrib(lookup, "x", build, date(2024, 3, 1))
    assert got == pytest.approx(expect, rel=1e-9)


def test_dividend_outside_window_ignored():
    """区间外分红不计入（除权日早于建仓日或晚于结算日）。"""
    build = date(2023, 1, 1)
    settle = date(2023, 12, 31)
    lookup = FakeLookup(
        prices={"x": [(date(2023, 1, 1), 10.0), (date(2023, 12, 31), 11.0)]},
        dividends={"x": [_mk_div(date(2022, 12, 1), 1.0),  # 早于建仓
                          _mk_div(date(2024, 6, 1), 1.0)]},  # 晚于结算
    )
    assert after_tax_dividend_contrib(lookup, "x", build, settle) == 0.0


def test_portfolio_total_return_split_factor():
    """T10 #115：组合层总收益也乘送转因子（双端口径一致，评审修复）。"""
    build = date(2023, 1, 2)
    settle = date(2023, 6, 30)
    # 10送10：建仓 10 元 → 结算 5.5 元（除权后），送转因子 2.0
    # 总收益 = 2 × 5.5/10 - 1 = +10%（若缺送转因子则 -45% 失真）
    lookup = FakeLookup(
        prices={"a": [(build, 10.0), (settle, 5.5)]},
        dividends={"a": [_mk_div(date(2023, 3, 1), 0.0, bonus=10.0)]},
    )
    got = portfolio_total_return(lookup, ["a"], build, settle, cost=0.0)
    assert got == pytest.approx(0.10, rel=1e-9)


def test_portfolio_total_return_split_factor_no_cost_no_div():
    """无送转时总收益 = 价格收益（向后兼容）。"""
    build = date(2023, 1, 2)
    settle = date(2023, 6, 30)
    lookup = FakeLookup(
        prices={"a": [(build, 10.0), (settle, 12.0)]},
        dividends={"a": []},
    )
    got = portfolio_total_return(lookup, ["a"], build, settle, cost=0.0)
    assert got == pytest.approx(0.20, rel=1e-9)


def test_portfolio_total_return_price_plus_div_minus_cost():
    """总收益 = 价格收益 × 分红复投 - 双边成本。"""
    build = date(2023, 1, 2)
    settle = date(2023, 6, 30)
    # 价格 10→11（+10%）；无分红；成本双边 0.6%
    lookup = FakeLookup(
        prices={"a": [(build, 10.0), (settle, 11.0)]},
        dividends={"a": []},
    )
    assert portfolio_total_return(lookup, ["a"], build, settle, cost=0.003) \
        == pytest.approx(0.1 - 0.006, rel=1e-9)

    # 有分红：10 元价格、1 元分红（税后 0.9，除权日价 10.5）→ 复投贡献 0.085714
    px2 = [(build, 10.0), (date(2023, 3, 1), 10.5), (settle, 11.0)]
    lookup2 = FakeLookup(
        prices={"a": px2},
        dividends={"a": [_mk_div(date(2023, 3, 1), 1.0)]},
    )
    contrib = after_tax_dividend_contrib(lookup2, "a", build, settle)
    expected = (1.1) * (1.0 + contrib) - 1.0 - 0.006
    assert portfolio_total_return(lookup2, ["a"], build, settle, cost=0.003) \
        == pytest.approx(expected, rel=1e-9)


def test_delisted_excluded():
    """无建仓价的股票剔除（从未上市/早已退市，无任何价格记录）。"""
    build = date(2023, 1, 2)
    settle = date(2023, 6, 30)
    lookup = FakeLookup(
        prices={
            "a": [(build, 10.0), (settle, 11.0)],
            "b": [],  # 无任何价格记录 → 剔除
        },
        dividends={"a": [], "b": []},
    )
    assert portfolio_total_return(lookup, ["a", "b"], build, settle) \
        == pytest.approx(0.1 - 0.006, rel=1e-9)


def test_top_n_selects_highest_yield():
    """TopN 按真实股息率（最新完整财年每股分红/价格）降序。"""
    T = date(2024, 3, 1)
    px = {"a": [(T, 10.0)], "b": [(T, 20.0)], "c": [(T, 5.0)]}
    div = {
        "a": [_mk_div(T, 0.1, "2023-12-31")],   # yield 1.0%
        "b": [_mk_div(T, 0.5, "2023-12-31")],   # yield 2.5%
        "c": [_mk_div(T, 0.4, "2023-12-31")],   # yield 8.0%
    }
    lookup = FakeLookup(prices=px, dividends=div)
    assert top_n_codes(lookup, ["a", "b", "c"], T, 2) == ["c", "b"]


def test_metrics_known_values():
    """绩效指标用已知序列验证。"""
    # 稳定 +10%/期 × 4 期
    rets = [0.1, 0.1, 0.1, 0.1]
    assert cum(rets) == pytest.approx(0.4641, rel=1e-9)
    assert max_drawdown(rets) == 0.0
    assert sharpe(rets) is None  # 零波动 → 无定义
    # 回撤：先涨后跌
    rets2 = [0.5, -0.5]
    assert max_drawdown(rets2) == pytest.approx(0.5, rel=1e-9)
    assert win_rate([0.1, -0.1, 0.1]) == pytest.approx(2 / 3, rel=1e-9)


def test_load_benchmark_aligns_to_rebalance():
    """基准对齐 rebalance_dates 计算季度收益。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE index_daily (code TEXT, date TEXT, close REAL)")
    conn.executemany(
        "INSERT INTO index_daily VALUES (?, ?, ?)",
        [
            ("H00922", "2023-01-02", 1000.0),
            ("H00922", "2023-03-31", 1100.0),
            ("H00922", "2023-06-30", 1210.0),
            ("H00922", "2023-09-29", 1000.0),
        ],
    )
    rebalance = [date(2022, 12, 31), date(2023, 3, 31), date(2023, 6, 30),
                 date(2023, 9, 29)]
    got = load_benchmark(conn, "H00922", rebalance)
    assert got[0] is None  # 起点前无基准数据
    assert got[1] == pytest.approx(1210 / 1100 - 1, rel=1e-9)
    assert got[2] == pytest.approx(1000 / 1210 - 1, rel=1e-9)
    assert got[3] is None  # 无下一期


def test_run_portfolio_turnover_tracks():
    """换手率 = 两期交集 / 并集。"""
    res = {
        "rebalance_dates": [date(2023, 3, 31), date(2023, 6, 30), date(2023, 9, 29)],
        "pools": {
            "full": [["a", "b"], ["b", "c"], ["c", "d"]],
            "base": [["a", "b"], ["b", "c"], ["c", "d"]],
            "l2": [["a", "b"], ["b", "c"], ["c", "d"]],
            "l3": [["a", "b"], ["b", "c"], ["c", "d"]],
            "l4": [["a", "b"], ["b", "c"], ["c", "d"]],
        },
    }
    # 所有股票价格齐全
    px = {}
    for d in [date(2023, 3, 31), date(2023, 4, 3), date(2023, 6, 30),
              date(2023, 7, 3), date(2023, 9, 29)]:
        for c in "abcd":
            px.setdefault(c, []).append((d, 10.0))
    lookup = FakeLookup(prices=px, dividends={c: [] for c in "abcd"})
    pf = run_portfolio(lookup, res, cost=0.0)
    # 第三期无下一 rebalance → None
    assert pf["quarterly_returns"]["full"][2] is None
    # 换手（T7 #112 真实换手率，0=零换手）：
    # 期1→期2 交集 {b} 并集 {a,b,c} → 保留 1/3，换手 2/3
    assert pf["turnover"]["full"][1] == pytest.approx(2 / 3, rel=1e-9)
    # 期0 无前值 → None
    assert pf["turnover"]["full"][0] is None


def test_run_portfolio_uses_trading_calendar_build_day():
    """T6 #111：建仓日用交易日历（替代探针股 hack），T+1 是周末则顺延。"""
    # rebalance 2023-06-30（周五），T+1=7/1 周六不在交易日历 → 应顺延到 7/3（周一）
    res = {
        "rebalance_dates": [date(2023, 6, 30), date(2023, 9, 29)],
        "pools": {"full": [["a"]], "base": [["a"]], "l2": [["a"]],
                  "l3": [["a"]], "l4": [["a"]]},
    }
    # 交易日历：6/30、7/3、9/29（7/1、7/2 不在）
    tdays = [date(2023, 6, 30), date(2023, 7, 3), date(2023, 9, 29)]
    # 价格：7/3 有价、7/1-7/2 无价
    px = {"a": [(date(2023, 6, 30), 10.0), (date(2023, 7, 3), 12.0),
                (date(2023, 9, 29), 11.0)]}
    lookup = FakeLookup(prices=px, dividends={"a": []}, trading_days=tdays)
    pf = run_portfolio(lookup, res, cost=0.0)
    # 建仓日用了 7/3：收益 = 11/12 - 1（建仓价 12、结算价 11）
    assert pf["quarterly_returns"]["full"][0] == pytest.approx(11 / 12 - 1, rel=1e-9)


def test_run_portfolio_fallback_no_trading_days():
    """T6 #111：无交易日历时回退 T+1 自然日（向后兼容）。"""
    res = {
        "rebalance_dates": [date(2023, 6, 30), date(2023, 9, 29)],
        "pools": {"full": [["a"]], "base": [["a"]], "l2": [["a"]],
                  "l3": [["a"]], "l4": [["a"]]},
    }
    px = {"a": [(date(2023, 6, 30), 10.0), (date(2023, 7, 1), 12.0),
                (date(2023, 9, 29), 11.0)]}
    lookup = FakeLookup(prices=px, dividends={"a": []})  # 无 trading_days
    pf = run_portfolio(lookup, res, cost=0.0)
    # 无交易日历 → bd = T+1 = 7/1，建仓价 12，结算价 11
    assert pf["quarterly_returns"]["full"][0] == pytest.approx(11 / 12 - 1, rel=1e-9)


def test_portfolio_metrics_end_to_end():
    """run_portfolio → performance_metrics 全链。"""
    res = {
        "rebalance_dates": [date(2023, 3, 31), date(2023, 6, 30), date(2023, 9, 29)],
        "pools": {
            "full": [["a"], ["a"], ["a"]],
            "base": [["a"], ["a"], ["a"]],
            "l2": [["a"], ["a"], ["a"]],
            "l3": [["a"], ["a"], ["a"]],
            "l4": [["a"], ["a"], ["a"]],
        },
    }
    px = {"a": [(date(2023, 3, 31), 10.0), (date(2023, 4, 3), 10.0),
                (date(2023, 6, 30), 11.0), (date(2023, 7, 3), 11.0),
                (date(2023, 9, 29), 12.1)]}
    lookup = FakeLookup(prices=px, dividends={"a": []})
    pf = run_portfolio(lookup, res, cost=0.0)
    m = performance_metrics(pf["quarterly_returns"])["full"]
    # 3 期，2 个有效收益（第3期 None）：10% + 10%
    assert m["cumulative"] == pytest.approx(1.1 * 1.1 - 1, rel=1e-9)
    assert m["max_drawdown"] == 0.0
    assert m["win_rate"] == 1.0


# ---- T4 #109: 频率年化按实际期长 ----

def test_annualized_quarterly_default():
    """季度调仓（默认 periods_per_year=4）：12 期（3 年）累计 30% → 年化约 9.14%。"""
    ann = annualized(0.30, 12)
    assert ann == pytest.approx((1.30) ** (1 / 3) - 1, rel=1e-9)


def test_annualized_monthly_periods_per_year_12():
    """月调仓 36 期（3 年）累计 30%，periods_per_year=12 → 与季度 12 期 3 年同年化。"""
    ann_m = annualized(0.30, 36, periods_per_year=12)
    ann_q = annualized(0.30, 12, periods_per_year=4)
    assert ann_m == pytest.approx(ann_q, rel=1e-9)


def test_annualized_semiannual_periods_per_year_2():
    """半年调仓 6 期（3 年）累计 30%，periods_per_year=2 → 同 3 年年化。"""
    ann_s = annualized(0.30, 6, periods_per_year=2)
    assert ann_s == pytest.approx((1.30) ** (1 / 3) - 1, rel=1e-9)


def test_annualized_freq_bug_regression():
    """T4 bug 回归：月调仓 36 期累计 30%，旧代码 n/4=9 年严重低估，
    新代码按 12 期/年 = 3 年正确年化。"""
    bad = (1.30) ** (1 / 9) - 1   # 旧：36/4=9 年
    good = annualized(0.30, 36, periods_per_year=12)
    assert good > bad  # 新 > 旧（旧严重低估年化）


def test_report_section1_annualization_calendar_span():
    """T10 #129 回归：报告 §3.1 年化按调仓日历跨度，不再固定 4.0/n_q。

    月频主回测 36 期（3 年日历跨度）累计 30%：旧口径 4.0/36≈9 年 → 1.22%
    失真；日历跨度口径 = (1.30)^(1/3)−1 ≈ 9.14%。
    """
    from datetime import date
    # 模拟 eng 输出的 rebalance_dates：36 个月度调仓日，跨 3 年
    rds = [date(2020 + m // 12, m % 12 + 1, 1) for m in range(36)]
    years = (max(rds) - min(rds)).days / 365.25
    ann = (1.30) ** (1.0 / years) - 1
    bad = (1.30) ** (4.0 / 36) - 1
    assert ann == pytest.approx((1.30) ** (1.0 / years) - 1, abs=1e-9)
    assert ann > bad * 2  # 旧口径低估一半以上


def test_sharpe_periods_per_year_scales():
    """sharpe 按 periods_per_year 缩放 rf 与 sqrt(ppy)。"""
    rets = [0.02, 0.01, -0.01, 0.03]
    s_q = sharpe(rets, periods_per_year=4)
    s_m = sharpe(rets, periods_per_year=12)
    # 不同频率下夏普不同（并非相等），证明参数生效
    assert s_q != pytest.approx(s_m, rel=1e-3)


def test_downside_risk_periods_per_year_scales():
    rets = [0.02, -0.01, -0.02, 0.03]
    d_q = downside_risk(rets, periods_per_year=4)
    d_m = downside_risk(rets, periods_per_year=12)
    assert d_q is not None and d_m is not None
    assert d_m > d_q  # 月度年化（sqrt(12)）> 季度年化（sqrt(4)）


# --- T12 #118 显著性检验 ---
def test_t_test_mean_significance():
    """t 检验：显著正收益序列 p<0.05。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from backtest_significance import t_test_mean
    t, p = t_test_mean([1.0] * 20)  # 恒正、零方差（1.0 精确可表示，跨 Python 版本稳定）→ se=0 → None
    assert t is None  # 零方差退化
    t, p = t_test_mean([0.01, 0.02, 0.03, 0.04, 0.05] * 4)
    assert t is not None and p is not None
    assert p < 0.05  # 显著正


def test_block_bootstrap_ci_covers_true_mean():
    """bootstrap CI 覆盖真实均值。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from backtest_significance import block_bootstrap_ci
    samples = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08] * 4
    lo, hi = block_bootstrap_ci(samples, n_boot=500, seed=42)
    assert lo is not None and hi is not None
    mean = sum(samples) / len(samples)
    assert lo <= mean <= hi  # CI 应覆盖真实均值


def test_excess_series_ratio_caliber():
    """超额用比值口径 (1+s)/(1+b)-1，与 T3 一致。"""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from backtest_significance import excess_series
    exc = excess_series([0.10], [0.05])
    assert exc == [pytest.approx(1.10 / 1.05 - 1, rel=1e-9)]


def test_split_then_cash_dividend_doubled():
    """T12 #130 M-10：先 10送10 再现金分红 → 股数×2，现金按 2×dps 计。"""
    build = date(2023, 1, 1)
    settle = date(2023, 12, 31)
    lookup = FakeLookup(
        prices={"x": [(date(2023, 1, 1), 10.0), (date(2023, 12, 31), 10.0)]},
        dividends={"x": [
            # 3-1：10送10（无现金）→ 股数×2
            _mk_div(date(2023, 3, 1), 0.0, bonus=10.0),
            # 7-1：每股现金 1.0 → 实际现金 = 2股 × 1.0 = 2.0
            _mk_div(date(2023, 7, 1), 1.0),
        ]},
    )
    # 税：持有 181 天 → 10%。贡献 = 2 × 1.0 × 0.9 / 10 = 0.18
    got = after_tax_dividend_contrib(lookup, "x", build, settle)
    assert got == pytest.approx(2.0 * 0.9 / 10.0, rel=1e-9)


def test_cash_dividend_before_split_not_doubled():
    """对照：现金分红在送转之前 → 按原股数 1×dps 计（不翻倍）。"""
    build = date(2023, 1, 1)
    settle = date(2023, 12, 31)
    lookup = FakeLookup(
        prices={"x": [(date(2023, 1, 1), 10.0), (date(2023, 12, 31), 10.0)]},
        dividends={"x": [
            # 3-1：先现金分红 1.0（1股 × 1.0）
            _mk_div(date(2023, 3, 1), 1.0),
            # 7-1：后 10送10（无现金）
            _mk_div(date(2023, 7, 1), 0.0, bonus=10.0),
        ]},
    )
    got = after_tax_dividend_contrib(lookup, "x", build, settle)
    assert got == pytest.approx(1.0 * 0.9 / 10.0, rel=1e-9)
