"""check_seed_guard.py 单测：上传守卫的三种结局。

守卫是「空库覆盖 seed」事故（2026-11）的唯一闸门，原先内联在 workflow 里没有测试。
"""
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from src.screener_cache import DividendSnapshot, ScreenerCache  # noqa: E402

import check_seed_guard  # noqa: E402


def _seed(tmp_path, n: int):
    cache = ScreenerCache(tmp_path / "s.db")
    for i in range(n):
        cache.upsert_dividend(DividendSnapshot(
            code=f"{600000 + i}", real_yield=5.0, ttm_yield=5.0,
            real_yield_year="2025", ttm_period=None, total_dividend=1e9,
            dividend_source="test"))
    return cache.db_path


class TestCountRows:
    def test_counts_existing_rows(self, tmp_path):
        db = _seed(tmp_path, 3)
        assert check_seed_guard.count_dividend_rows(db) == 3

    def test_missing_file_is_zero(self, tmp_path):
        assert check_seed_guard.count_dividend_rows(tmp_path / "nope.db") == 0

    def test_missing_table_is_zero(self, tmp_path):
        import sqlite3
        db = tmp_path / "empty.db"
        sqlite3.connect(db).execute("CREATE TABLE other (x int)")
        assert check_seed_guard.count_dividend_rows(db) == 0


class TestMain:
    def test_passes_above_threshold(self, tmp_path, capsys):
        db = _seed(tmp_path, 5)
        assert check_seed_guard.main(["--db", str(db), "--min", "5"]) == 0
        assert "::error::" not in capsys.readouterr().out

    def test_rejects_below_threshold(self, tmp_path, capsys):
        db = _seed(tmp_path, 4)
        assert check_seed_guard.main(["--db", str(db), "--min", "5"]) == 1
        out = capsys.readouterr().out
        assert "::error::" in out and "拒绝上传" in out

    def test_missing_db_exits_2(self, tmp_path, capsys):
        assert check_seed_guard.main(["--db", str(tmp_path / "nope.db")]) == 2
        assert "库文件不存在" in capsys.readouterr().out

    def test_default_threshold_is_1000(self, tmp_path):
        db = _seed(tmp_path, 999)
        assert check_seed_guard.main(["--db", str(db)]) == 1
