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
    """固定种子可复现、起点不早于 2013-01-01、不重复。"""
    a = random_start_offsets(4, seed=42)
    b = random_start_offsets(4, seed=42)
    assert a == b
    assert len(set(a)) == 4
    assert all(d >= date(2013, 1, 1) for d in a)
    assert all(d <= date(2013, 9, 29) for d in a)  # 2013-01-01 + 270 天


def test_load_names():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE stock_list (code TEXT, name TEXT)")
    conn.execute("INSERT INTO stock_list VALUES ('600036', '招商银行')")
    assert load_names(conn) == {"600036": "招商银行"}
    # 无表时返回空 dict
    conn2 = sqlite3.connect(":memory:")
    assert load_names(conn2) == {}
