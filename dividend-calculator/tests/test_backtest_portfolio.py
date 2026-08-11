#!/usr/bin/env python3
"""T5 组合构建与绩效评估测试（issue #88）"""
import math
import os
import sqlite3
import sys
from datetime import date

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
    calmar,
    win_rate,
    cum,
    load_benchmark,
)


class FakeLookup:
    """最小 lookup：price 用注入的序列，dividends 返回固定记录。"""

    def __init__(self, prices=None, dividends=None):
        self._prices = prices or {}
        self._div = dividends or {}

    def price(self, code, asof):
        series = self._prices.get(code, [])
        best = None
        for d, c in sorted(series):
            if d <= asof:
                best = c
        return best

    def dividends(self, code, asof):
        return self._div.get(code, [])


def _mk_div(ex_date, dps, report_date="2023-12-31"):
    return {
        "announce_date": ex_date,
        "report_date": report_date,
        "ex_dividend_date": ex_date,
        "cash_div_per_share": dps,
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
    # 换手：期1→期2 交集 {b} 并集 {a,b,c} → 1/3
    assert pf["turnover"]["full"][1] == pytest.approx(1 / 3, rel=1e-9)
    # 期0 无前值 → None
    assert pf["turnover"]["full"][0] is None


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
