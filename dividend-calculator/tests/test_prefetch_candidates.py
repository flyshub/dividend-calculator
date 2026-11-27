"""预拉候选清单测试（scripts/prefetch_sustainability.load_candidates）。

接缝：load_funnel_codes(path) 纯函数 + load_candidates(cache, funnel_json) 合并逻辑。
背景 #160：漏斗② 用实时行情，股价涨进漏斗的股票库内旧值可能不到阈值，
预拉名单必须并入最近一次漏斗候选，否则它们永远没有快照、每日只能按需补拉。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import prefetch_sustainability as pf  # noqa: E402
from src.screener_cache import DividendSnapshot, ScreenerCache  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture
def cache(tmp_path):
    c = ScreenerCache(tmp_path / "s.db")
    for code, real, ttm in (("600001", 6.0, 6.0),   # 入阈值集合
                            ("600002", 4.0, 4.0),   # 不入（#160 形态）
                            ("600003", 6.0, 6.0)):  # 入阈值集合
        c.upsert_dividend(DividendSnapshot(
            code=code, real_yield=real, ttm_yield=ttm, real_yield_year="2025",
            ttm_period="p", total_dividend=1e9, ttm_dividend=1e9, dividend_source="东财"))
    return c


def _write_funnel(path, codes):
    path.write_text(json.dumps([{"代码": c} for c in codes]), encoding="utf-8")


class TestLoadFunnelCodes:
    def test_reads_codes(self, tmp_path):
        p = tmp_path / "latest.json"
        _write_funnel(p, ["600002", "600900"])
        assert pf.load_funnel_codes(p) == ["600002", "600900"]

    def test_missing_file_is_empty(self, tmp_path):
        assert pf.load_funnel_codes(tmp_path / "nope.json") == []

    def test_broken_json_is_empty(self, tmp_path):
        p = tmp_path / "latest.json"
        p.write_text("{not json", encoding="utf-8")
        assert pf.load_funnel_codes(p) == []

    def test_non_list_is_empty(self, tmp_path):
        p = tmp_path / "latest.json"
        p.write_text('{"rows": []}', encoding="utf-8")
        assert pf.load_funnel_codes(p) == []


class TestLoadCandidates:
    def test_unions_threshold_and_funnel(self, cache, tmp_path):
        p = tmp_path / "latest.json"
        _write_funnel(p, ["600002", "600003"])  # 600002 不在阈值集合、600003 重复
        assert pf.load_candidates(cache, funnel_json=p) == ["600001", "600002", "600003"]

    def test_missing_funnel_json_falls_back_to_thresholds(self, cache, tmp_path):
        assert pf.load_candidates(cache, funnel_json=tmp_path / "nope.json") == \
            ["600001", "600003"]

    def test_all_a_ignores_funnel(self, cache, tmp_path):
        p = tmp_path / "latest.json"
        _write_funnel(p, ["600002"])
        assert pf.load_candidates(cache, all_a=True, funnel_json=p) == \
            ["600001", "600002", "600003"]
