"""#26 A 股特色风险标记：ST 与商誉警示。

纯函数 + 取数解析；不改评分、档位或漏斗判据。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.risk_flags import (  # noqa: E402
    GOODWILL_EQUITY_WARN,
    goodwill_warning,
    is_st,
    st_warning,
)


@pytest.mark.parametrize("name,expected", [
    ("*ST宏图", True),
    ("ST 航通", True),
    ("st航通", True),
    ("中远海控", False),
    ("贵州茅台", False),
    ("", False),
    (None, False),
])
def test_is_st(name, expected):
    assert is_st(name) is expected


def test_st_warning_text_and_none():
    assert "退市风险警示" in st_warning("*ST宏图")
    assert st_warning("贵州茅台") is None
    assert st_warning(None) is None


def test_goodwill_below_threshold_no_warning():
    # 美的集团 2026Q3：商誉 311.5 亿 / 所有者权益 2364 亿 ≈ 13.2%
    assert goodwill_warning(31_146_557_000, 236_404_503_000) is None


def test_goodwill_above_threshold_warns_with_ratio():
    msg = goodwill_warning(600, 1000)
    assert msg is not None
    assert "60%" in msg and "50%" in msg
    assert GOODWILL_EQUITY_WARN == 0.50


@pytest.mark.parametrize("goodwill,equity", [
    (None, 1000), (100, None), (100, 0), (100, -5), (0, 1000),
])
def test_goodwill_missing_or_invalid_no_warning(goodwill, equity):
    """缺数据/非正净资产：不补 0、不推断（数据铁律）。"""
    assert goodwill_warning(goodwill, equity) is None


def test_goodwill_exactly_threshold_no_warning():
    assert goodwill_warning(500, 1000) is None
    assert goodwill_warning(501, 1000) is not None


# ---------------------------------------------------------------------------
# fetch_goodwill 解析（不联网：替换 _fetch_eastmoney_rows）
# ---------------------------------------------------------------------------

def test_fetch_goodwill_picks_first_row_with_both_fields(monkeypatch):
    from src import eastmoney_fetcher as ef

    rows = [
        {"REPORT_DATE": "2026-09-30", "GOODWILL": None, "TOTAL_EQUITY": 1000.0},
        {"REPORT_DATE": "2026-06-30", "GOODWILL": 600.0, "TOTAL_EQUITY": 1000.0},
    ]
    monkeypatch.setattr(ef, "_fetch_eastmoney_rows", lambda *a, **k: rows)
    assert ef.fetch_goodwill("600519") == (600.0, 1000.0)


def test_fetch_goodwill_returns_none_when_report_absent(monkeypatch):
    from src import eastmoney_fetcher as ef

    monkeypatch.setattr(ef, "_fetch_eastmoney_rows", lambda *a, **k: [])
    assert ef.fetch_goodwill("601398") is None


# ---------------------------------------------------------------------------
# analysis.attach_goodwill_warning：警示挂载（就地修改，不改评分）
# ---------------------------------------------------------------------------

def _sus():
    from src.sustainability_calculator import SustainabilityResult
    return SustainabilityResult(triggered=True, verdict="偏弱", score=1.2,
                                warning_flags=["既有警示"], metrics={"payout": 0.5})


def test_attach_goodwill_warning_adds_flag_and_metric(monkeypatch):
    from src import analysis

    monkeypatch.setattr(analysis, "fetch_goodwill", lambda code: (600.0, 1000.0))
    s = _sus()
    analysis.attach_goodwill_warning(s, "600754")
    assert s.verdict == "偏弱" and s.score == 1.2          # 判定与评分不变
    assert s.warning_flags[0] == "既有警示"                  # 追加而非覆盖
    assert "商誉占净资产 60%" in s.warning_flags[-1]
    assert s.metrics["goodwill_equity"] == 0.6


def test_attach_goodwill_warning_no_data_is_silent(monkeypatch):
    from src import analysis

    monkeypatch.setattr(analysis, "fetch_goodwill", lambda code: None)
    s = _sus()
    analysis.attach_goodwill_warning(s, "601398")
    assert s.warning_flags == ["既有警示"]
    assert "goodwill_equity" not in s.metrics


def test_attach_goodwill_warning_below_threshold_is_silent(monkeypatch):
    from src import analysis

    monkeypatch.setattr(analysis, "fetch_goodwill", lambda code: (100.0, 1000.0))
    s = _sus()
    analysis.attach_goodwill_warning(s, "600519")
    assert s.warning_flags == ["既有警示"]
