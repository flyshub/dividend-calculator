#!/usr/bin/env python3
"""T6 稳健性检验测试（issue #89）"""
import os
import sqlite3
import sys
from datetime import date

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from backtest_robustness import (
    filter_small_cap,
    filter_financial,
    random_start_offsets,
    load_names,
    run_variant,
    _FIN_KEYWORDS,
)
from backtest_engine import build_day_after


class FakeLookup:
    def __init__(self, prices=None, shares=None):
        self._prices = prices or {}
        self._shares = shares or {}

    def price(self, code, asof):
        best = None
        for d, c in sorted(self._prices.get(code, [])):
            if d <= asof:
                best = c
        return best

    def total_shares(self, code, asof):
        return self._shares.get(code)


def test_build_day_after_offset():
    """T+1 与 T+5 建仓日。"""
    days = [date(2023, 3, 31), date(2023, 4, 3), date(2023, 4, 4),
            date(2023, 4, 5), date(2023, 4, 6), date(2023, 4, 7)]
    assert build_day_after(days, date(2023, 3, 31)) == date(2023, 4, 3)
    assert build_day_after(days, date(2023, 3, 31), offset=5) == date(2023, 4, 7)
    # 超出交易日数 → None
    assert build_day_after(days, date(2023, 4, 7), offset=1) is None


def test_filter_small_cap():
    """市值 < 50 亿剔除；无价格/无股本也剔除。"""
    T = date(2024, 3, 1)
    lookup = FakeLookup(
        prices={"big": [(T, 20.0)], "small": [(T, 3.0)], "nopx": []},
        shares={"big": 30e8, "small": 10e8, "nopx": 100e8},  # big=600亿 small=30亿
    )
    assert filter_small_cap(lookup, ["big", "small", "nopx"], T) == ["big"]


def test_filter_financial():
    """名称含金融关键词剔除。"""
    names = {"600036": "招商银行", "601318": "中国平安保险",
             "600519": "贵州茅台", "600030": "中信证券"}
    assert filter_financial(["600036", "601318", "600519", "600030"], names) \
        == ["600519"]
    assert _FIN_KEYWORDS == ("银行", "证券", "保险", "信托", "金融")


def test_random_start_offsets_seeded():
    """固定种子可复现、起点在 2013 年前 4 个季度。"""
    a = random_start_offsets(4, seed=42)
    b = random_start_offsets(4, seed=42)
    assert a == b  # 可复现
    assert len(a) == 4
    # 起点必须是 2013 年的季度初（1/4/7/10 月）
    assert all(d.year == 2013 for d in a)
    assert all(d.month in (1, 4, 7, 10) for d in a)
    assert all(d.day == 1 for d in a)


def test_load_names():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE stock_list (code TEXT, name TEXT)")
    conn.execute("INSERT INTO stock_list VALUES ('600036', '招商银行')")
    assert load_names(conn) == {"600036": "招商银行"}
    # 无表时返回空 dict
    conn2 = sqlite3.connect(":memory:")
    assert load_names(conn2) == {}


def test_run_backtest_filter_fn_changes_results(monkeypatch):
    """filter_fn 接入 run_backtest 收益链路——过滤后结果应不同于未过滤。

    回归 T6 filter no-op bug：原实现 run_variant 在 run_backtest 之后过滤 pools，
    过滤根本不影响已算完的收益；修复后 filter_fn 在 portfolio_return 前应用，
    过滤应真实改变 cumulative_returns。
    """
    import backtest_engine

    # monkey-patch compute_all_factors + funnel_layer：让所有股票入 base 层，
    # 排除 T3 因子层契约依赖，专注测 filter_fn 在 run_backtest 内生效
    monkeypatch.setattr(backtest_engine, "compute_all_factors",
                        lambda code, T, lookup: {})
    monkeypatch.setattr(backtest_engine, "funnel_layer", lambda factors: 1)

    days = [date(2023, 3, 31), date(2023, 4, 3), date(2023, 6, 30), date(2023, 7, 3)]

    class TestLookup:
        def __init__(self):
            self.trading_days = days
            self._shares = {"KEEP": 30e8, "DROP": 5e8}  # DROP<50亿
            self.prices = {
                "KEEP": [(date(2023, 3, 31), 20.0), (date(2023, 4, 3), 20.0),
                          (date(2023, 6, 30), 22.0), (date(2023, 7, 3), 22.0)],
                "DROP": [(date(2023, 3, 31), 1.0), (date(2023, 4, 3), 1.0),
                          (date(2023, 6, 30), 1.0), (date(2023, 7, 3), 1.0)],
            }

        def price(self, code, asof):
            best = None
            for d, c in self.prices.get(code, []):
                if d <= asof:
                    best = c
            return best

        def total_shares(self, code, asof):
            return self._shares.get(code)

    lookup = TestLookup()

    # 未过滤：两只都在 base 池
    res_unfilt = backtest_engine.run_backtest(
        lookup, start=date(2023, 1, 1), end=date(2023, 12, 31))
    # 过滤：剔 DROP（市值 5 亿 < 50 亿）
    res_filt = backtest_engine.run_backtest(
        lookup, start=date(2023, 1, 1), end=date(2023, 12, 31),
        filter_fn=lambda codes, T: filter_small_cap(lookup, codes, T))

    # 过滤后 pools 应更小
    n_unfilt = sum(len(p) for p in res_unfilt["pools"]["base"])
    n_filt = sum(len(p) for p in res_filt["pools"]["base"])
    assert n_filt < n_unfilt, "filter_fn 未改变候选池（no-op 回归）"

    # full_over_base 键存在（report headline 用）
    assert "full_over_base" in res_unfilt["incremental_excess"]


def test_run_variant_filter_in_pipeline(monkeypatch):
    """run_variant 通过 filter_fn 接入 run_backtest，结果应不同于主回测。"""
    import backtest_engine
    monkeypatch.setattr(backtest_engine, "compute_all_factors",
                        lambda code, T, lookup: {})
    monkeypatch.setattr(backtest_engine, "funnel_layer", lambda factors: 1)

    days = [date(2023, 3, 31), date(2023, 4, 3), date(2023, 6, 30), date(2023, 7, 3)]

    class TestLookup:
        def __init__(self):
            self.trading_days = days
            self._shares = {"BIG": 30e8, "SMALL": 2e8}  # SMALL=4亿<50亿
            self.prices = {
                "BIG": [(date(2023, 3, 31), 20.0), (date(2023, 4, 3), 20.0),
                         (date(2023, 6, 30), 22.0), (date(2023, 7, 3), 22.0)],
                "SMALL": [(date(2023, 3, 31), 2.0), (date(2023, 4, 3), 2.0),
                           (date(2023, 6, 30), 2.0), (date(2023, 7, 3), 2.0)],
            }

        def price(self, code, asof):
            best = None
            for d, c in self.prices.get(code, []):
                if d <= asof:
                    best = c
            return best

        def total_shares(self, code, asof):
            return self._shares.get(code)

    lookup = TestLookup()

    main = run_variant(lookup, "主回测")
    filt = run_variant(
        lookup, "剔微盘",
        filter_fn=lambda codes, T: filter_small_cap(lookup, codes, T))

    # 过滤变体的 base 累计收益应不同于主回测（剔微盘真生效）
    assert main["cumulative_returns"]["base"] != filt["cumulative_returns"]["base"]
