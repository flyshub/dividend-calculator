"""scripts/init_screener.py 财务导入测试（评审 P1-2 / Phase 3）。

覆盖 _import_finance_from_backtest 的补算逻辑（全部临时库，不碰真实 data/）：
- roe_5y_median：最近 5 个年报（12-31）ROE 中位数（years.max()-4 窗口）
- is_cyclical：industry_cache.json → classify_industry；缓存缺失置 None 不阻塞
- 最新年报 ROE 作为 roe_latest / roe_period
"""
import json
import sqlite3

import pytest

from scripts.init_screener import _import_finance_from_backtest, _roe_5y_median
from src.screener_cache import ScreenerCache


def _make_backtest_db(path, rows):
    """rows: [(code, roe, report_date)]（与 backtest.db 的 roe 表列序一致）。"""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE roe (code TEXT NOT NULL, report_date TEXT NOT NULL, roe REAL)")
    conn.executemany(
        "INSERT INTO roe (code, report_date, roe) VALUES (?, ?, ?)",
        [(code, report_date, roe) for code, roe, report_date in rows])
    conn.commit()
    conn.close()


def _make_industry_cache(path, mapping):
    path.write_text(json.dumps(mapping), encoding="utf-8")


class TestRoe5yMedian:
    def test_median_of_last_5_years(self):
        # 10 个年报 → 窗口 2021-2025（max_year-4 起），中位数 = 中间值
        rows = [(f"{y}-12-31", float(y)) for y in range(2016, 2026)]
        assert _roe_5y_median(rows) == pytest.approx(2023.0)

    def test_fewer_than_5_years_uses_all(self):
        rows = [("2023-12-31", 10.0), ("2024-12-31", 20.0), ("2025-12-31", 30.0)]
        assert _roe_5y_median(rows) == pytest.approx(20.0)

    def test_empty_returns_none(self):
        assert _roe_5y_median([]) is None


class TestImportFinanceFromBacktest:
    def test_imports_median_and_cyclical(self, tmp_path):
        bt = tmp_path / "backtest.db"
        _make_backtest_db(bt, [
            # 周期股（房地产）：10 年 ROE，中位数窗口 2021-2025
            ("000002", 10.0, "2016-12-31"), ("000002", 11.0, "2017-12-31"),
            ("000002", 12.0, "2018-12-31"), ("000002", 13.0, "2019-12-31"),
            ("000002", 14.0, "2020-12-31"), ("000002", 15.0, "2021-12-31"),
            ("000002", 16.0, "2022-12-31"), ("000002", 17.0, "2023-12-31"),
            ("000002", 18.0, "2024-12-31"), ("000002", 19.0, "2025-12-31"),
            # 非周期股（电力）：3 年数据
            ("600900", 14.0, "2023-12-31"), ("600900", 15.0, "2024-12-31"),
            ("600900", 16.0, "2025-12-31"),
            # 行业缓存缺失 → is_cyclical None（不阻塞）
            ("600987", 12.0, "2025-12-31"),
        ])
        ind = tmp_path / "industry_cache.json"
        _make_industry_cache(ind, {"000002": "房地产-房地产开发-房地产开发",
                                   "600900": "公用事业-电力-火电"})
        cache = ScreenerCache(tmp_path / "screener.db")
        n = _import_finance_from_backtest(cache, backtest_db=bt, industry_cache_path=ind)
        assert n == 3

        cyc = cache.get_finance("000002")
        assert cyc is not None
        assert cyc.roe_latest == pytest.approx(19.0)          # 最新年报
        assert cyc.roe_period == "2025-12-31"
        assert cyc.roe_5y_median == pytest.approx(17.0)       # 2021-2025 中位数
        assert cyc.is_cyclical is True                        # 房地产 → 周期

        power = cache.get_finance("600900")
        assert power is not None
        assert power.roe_5y_median == pytest.approx(15.0)     # 3 年取全部
        assert power.is_cyclical is False                     # 电力 → 非周期

        missing = cache.get_finance("600987")
        assert missing is not None
        assert missing.is_cyclical is None                    # 缓存缺失 → None 不阻塞

    def test_missing_industry_cache_file(self, tmp_path):
        bt = tmp_path / "backtest.db"
        _make_backtest_db(bt, [("600900", 16.0, "2025-12-31")])
        cache = ScreenerCache(tmp_path / "screener.db")
        n = _import_finance_from_backtest(
            cache, backtest_db=bt, industry_cache_path=tmp_path / "nope.json")
        assert n == 1
        got = cache.get_finance("600900")
        assert got is not None
        assert got.is_cyclical is None

    def test_none_roe_skipped(self, tmp_path):
        bt = tmp_path / "backtest.db"
        _make_backtest_db(bt, [("600900", None, "2025-12-31")])
        cache = ScreenerCache(tmp_path / "screener.db")
        assert _import_finance_from_backtest(cache, backtest_db=bt) == 0