"""merge_shard_rows.py 单测：合并语义 + 孤儿行清理（生产路径唯一入口）。

背景：CI 刷新只跑 `--shard`，而 fill_screener_data 的孤儿清理在非分片分支里，
于是生产环境从不清理。2026-11-15 review 把清理移到合并脚本，本文件锁定该行为。
"""
import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from src.screener_cache import (  # noqa: E402
    DividendSnapshot,
    ScreenerCache,
    StockListItem,
)

import merge_shard_rows  # noqa: E402


def _div(code, total=1e9):
    return {
        "code": code, "real_yield": 5.0, "ttm_yield": 5.0, "real_yield_year": "2025",
        "ttm_period": None, "total_dividend": total, "dividend_source": "test",
        "updated_at": str(date.today()),
    }


def _fin(code):
    return {
        "code": code, "roe_latest": 15.0, "roe_period": "2025",
        "net_profit_annual": 1e9, "payout_ratio": 0.5, "roe_5y_median": 15.0,
        "is_cyclical": None, "finance_source": "test", "updated_at": str(date.today()),
    }


@pytest.fixture
def cache(tmp_path):
    c = ScreenerCache(tmp_path / "s.db")
    c.upsert_stock_list([
        StockListItem(code="600900", name="长江电力", market="SH"),
        StockListItem(code="000651", name="格力电器", market="SZ"),
    ])
    c.upsert_dividend(DividendSnapshot(**_div("600900", total=1.0)))
    c.upsert_dividend(DividendSnapshot(**_div("999999", total=2.0)))  # 不在股票池 → 孤儿
    return c


def _run(tmp_path, cache, *extra, div_rows=None, fin_rows=None):
    shard = tmp_path / "shard-1.json"
    shard.write_text(json.dumps({
        "dividend": div_rows if div_rows is not None else [_div("600900", total=9.0)],
        "finance": fin_rows if fin_rows is not None else [_fin("600900")],
    }), encoding="utf-8")
    # 单测股票池只有 2 只，放开清理门槛（生产默认 1000）
    cache_min = ["--min-universe", "1"] if "--min-universe" not in extra else []
    argv = ["merge_shard_rows.py", str(shard), "--db", str(cache.db_path),
            *cache_min, *extra]
    monkey = pytest.MonkeyPatch()
    monkey.setattr(sys, "argv", argv)
    try:
        return merge_shard_rows.main()
    finally:
        monkey.undo()


class TestMerge:
    def test_upserts_rows_from_shards(self, tmp_path, cache):
        assert _run(tmp_path, cache) == 0
        assert cache.get_dividend("600900").total_dividend == 9.0
        assert cache.get_finance("600900").roe_latest == 15.0

    def test_missing_section_keeps_existing_rows(self, tmp_path, cache):
        """finance-only 分片（mode=finance）不得清掉已有股息行。"""
        _run(tmp_path, cache, div_rows=[])
        assert cache.get_dividend("600900") is not None
        assert cache.get_finance("600900") is not None


class TestOrphanPrune:
    def test_prune_removes_codes_missing_from_stock_list(self, tmp_path, cache):
        assert cache.get_dividend("999999") is not None
        _run(tmp_path, cache)
        assert cache.get_dividend("999999") is None
        assert cache.get_dividend("600900") is not None

    def test_prune_keeps_rows_when_stock_list_empty(self, tmp_path):
        """股票池为空时不动（防股票池刷新失败误删全市场快照）。"""
        c = ScreenerCache(tmp_path / "s.db")
        c.upsert_dividend(DividendSnapshot(**_div("999999")))
        _run(tmp_path, c, div_rows=[])
        assert c.get_dividend("999999") is not None

    def test_skip_prune_flag(self, tmp_path, cache):
        _run(tmp_path, cache, "--skip-prune")
        assert cache.get_dividend("999999") is not None
