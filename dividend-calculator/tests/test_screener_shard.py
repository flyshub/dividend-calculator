"""分片刷新链路单测：代码切片、快照导出、合并回库、过期覆盖统计。

背景（2026-11）：全 A 股息刷新实测 4.9s/只 ≈ 7.5 小时，单次 CI 跑不完，
改为 8 片并行 + 导出合并。这里保证「切片不重不漏」和「合并只覆盖导出的行」。
"""
import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from src.screener_cache import (
    ScreenerCache,
    DividendSnapshot,
    FinanceSnapshot,
)

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fill = _load("fill_screener_data")
merge = _load("merge_shard_rows")


def _codes(n):
    return [f"{600000 + i}" for i in range(n)]


class TestShardCodes:
    def test_partition_is_disjoint_and_complete(self):
        codes = _codes(5559)
        seen = []
        for i in range(1, 9):
            seen.extend(fill.shard_codes(codes, i, 8))
        assert sorted(seen) == sorted(codes), "各片并集必须等于全量"
        assert len(seen) == len(set(seen)), "各片不得重叠"

    def test_sizes_balanced(self):
        codes = _codes(5559)
        sizes = [len(fill.shard_codes(codes, i, 8)) for i in range(1, 9)]
        assert max(sizes) - min(sizes) <= 1
        assert sum(sizes) == 5559

    def test_single_shard_is_identity(self):
        codes = _codes(10)
        assert fill.shard_codes(codes, 1, 1) == sorted(codes)

    def test_invalid_index_rejected(self):
        with pytest.raises(ValueError):
            fill.shard_codes(_codes(5), 0, 3)
        with pytest.raises(ValueError):
            fill.shard_codes(_codes(5), 4, 3)

    def test_empty_universe(self):
        assert fill.shard_codes([], 2, 8) == []


class TestExportAndMerge:
    def test_round_trip_only_covers_exported_codes(self, tmp_path):
        """合并只覆盖导出文件里出现的 code，未导出的主库原值保留。"""
        # 分片库：只有本片的 600001 有新鲜数据
        shard_db = tmp_path / "shard.db"
        sc = ScreenerCache(shard_db)
        sc.upsert_dividend(DividendSnapshot(code="600001", real_yield=6.1, ttm_yield=6.4,
                                            real_yield_year="2025", ttm_period="p",
                                            total_dividend=1e9, ttm_dividend=1.1e9,
                                            dividend_source="mootdx", updated_at="2026-11-14"))
        sc.upsert_finance(FinanceSnapshot(code="600001", roe_latest=18.0, roe_period="2025",
                                          net_profit_annual=2e9, payout_ratio=0.5,
                                          finance_source="ths", updated_at="2026-11-14"))
        out = tmp_path / "shard.json"
        fill.export_rows(sc, ["600001", "600002"], str(out), "dividend")  # 600002 不存在
        fill.export_rows(sc, ["600001", "600002"], str(out), "finance")
        data = json.loads(out.read_text(encoding="utf-8"))
        assert [r["code"] for r in data["dividend"]] == ["600001"]
        assert [r["code"] for r in data["finance"]] == ["600001"]

        # 主库：600001 是旧值，600002 只存在于主库
        main_db = tmp_path / "main.db"
        mc = ScreenerCache(main_db)
        mc.upsert_dividend(DividendSnapshot(code="600001", real_yield=1.0, ttm_yield=1.0,
                                            real_yield_year="2024", ttm_period="p",
                                            dividend_source="old", updated_at="2026-08-10"))
        mc.upsert_dividend(DividendSnapshot(code="600002", real_yield=5.0, ttm_yield=5.0,
                                            real_yield_year="2024", ttm_period="p",
                                            dividend_source="old", updated_at="2026-08-10"))

        div, fin = merge._load([str(out)])
        for row in div.values():
            mc.upsert_dividend(DividendSnapshot(**row))
        for row in fin.values():
            mc.upsert_finance(FinanceSnapshot(**row))

        assert mc.get_dividend("600001").real_yield == 6.1        # 被覆盖
        assert mc.get_dividend("600001").updated_at == "2026-11-14"
        assert mc.get_dividend("600002").real_yield == 5.0        # 未导出 → 原值保留
        assert mc.get_finance("600001").roe_latest == 18.0

    def test_duplicate_code_keeps_newest(self, tmp_path):
        a = tmp_path / "a.json"
        b = tmp_path / "b.json"
        a.write_text(json.dumps({"dividend": [{"code": "600001", "real_yield": 1.0,
                                               "ttm_yield": 1.0, "real_yield_year": "2024",
                                               "ttm_period": "p", "total_dividend": None,
                                               "ttm_dividend": None, "dividend_source": "a",
                                               "updated_at": "2026-08-01"}]}), encoding="utf-8")
        b.write_text(json.dumps({"dividend": [{"code": "600001", "real_yield": 2.0,
                                               "ttm_yield": 2.0, "real_yield_year": "2024",
                                               "ttm_period": "p", "total_dividend": None,
                                               "ttm_dividend": None, "dividend_source": "b",
                                               "updated_at": "2026-11-14"}]}), encoding="utf-8")
        div, _ = merge._load([str(a), str(b)])
        assert div["600001"]["real_yield"] == 2.0


class TestStaleCounts:
    def test_counts_and_threshold_semantics(self, tmp_path):
        cache = ScreenerCache(tmp_path / "s.db")
        old = (date.today() - timedelta(days=40)).isoformat()
        fresh = date.today().isoformat()
        cache.upsert_dividend(DividendSnapshot(code="600001", real_yield=5.0, ttm_yield=5.0,
                                              real_yield_year="2024", ttm_period="p",
                                              dividend_source="x", updated_at=old))
        cache.upsert_dividend(DividendSnapshot(code="600002", real_yield=5.0, ttm_yield=5.0,
                                              real_yield_year="2025", ttm_period="p",
                                              dividend_source="x", updated_at=fresh))
        counts = cache.stale_counts(max_age_days=30)
        assert counts["dividend_snapshot"] == (2, 1)
        assert counts["finance_snapshot"] == (0, 0)
