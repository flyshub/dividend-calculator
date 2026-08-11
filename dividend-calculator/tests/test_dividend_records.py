"""test_dividend_records.py — dividend_records.summarize_dividend_rows 单元测试（issue #93）

fixture 数据全部复用现有测试文件的真实值（数据铁律：严禁虚构）：
- test_regression_snapshot.py：长江电力 2025 年报 10派7.33（EX 2026-07-15）、预披露行
- test_sustainability_data.py：2025 中期 2.0 / 2024 年报 5.0 / 2020~2018 年报 7.0/7.0/6.8
- test_fiscal_year_crosscheck.py：3-31 报告期行（中期分配）
"""
from datetime import date

import pytest

from src.datasource.base import DividendRecord
from src.sustainability import parse_dividend_rows
from src.utils import compute_ttm_dividend
from src.dividend_records import DividendSummary, summarize_dividend_rows

pytestmark = pytest.mark.unit  # 无网络，CI 必跑

# 长江电力总股本（test_regression_snapshot.py 快照值）
CHANGJIANG_SHARES = 2.2741859116e10


def _fixture_rows() -> list:
    """真实分红行 fixture（数值全部来自现有测试文件，未新增任何数字）。"""
    return [
        # 长江电力 2025 年报 10派7.33（test_regression_snapshot.py test_sharebonus_det_field_mapping）
        {"REPORT_DATE": "2025-12-31", "PRETAX_BONUS_RMB": 7.33,
         "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2026-07-15"},
        # 2025 中期分配（test_sustainability_data.py test_parse_dividend_rows_distinguishes_annual）
        {"REPORT_DATE": "2025-06-30", "PRETAX_BONUS_RMB": 2.0,
         "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2025-12-01"},
        # 2024 年报（test_sustainability_data.py 同上）
        {"REPORT_DATE": "2024-12-31", "PRETAX_BONUS_RMB": 5.0,
         "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2025-07-01"},
        # 预披露 → 应过滤（test_regression_snapshot.py test_sharebonus_det_filters_preset）
        {"REPORT_DATE": "2025-03-31", "PRETAX_BONUS_RMB": 3.0,
         "ASSIGN_PROGRESS": "预披露", "EX_DIVIDEND_DATE": ""},
        # 历史年报（test_sustainability_data.py test_parse_dividend_rows_populates_plan_notice_date）
        {"REPORT_DATE": "2020-12-31", "PRETAX_BONUS_RMB": 7.0,
         "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2021-07-16",
         "PLAN_NOTICE_DATE": "2021-04-30"},
        {"REPORT_DATE": "2019-12-31", "PRETAX_BONUS_RMB": 7.0,
         "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2020-07-17",
         "PLAN_NOTICE_DATE": "2020-04-30"},
        {"REPORT_DATE": "2018-12-31", "PRETAX_BONUS_RMB": 6.8,
         "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2019-07-17"},
    ]


# ---------------------------------------------------------------------------
# 对账：与 sustainability.parse_dividend_rows 完全一致
# ---------------------------------------------------------------------------

def test_reconciles_with_parse_dividend_rows():
    """records/latest_year 与 parse_dividend_rows 输出逐字段一致（对账）。"""
    ref_records, ref_year = parse_dividend_rows(_fixture_rows())
    s = summarize_dividend_rows(_fixture_rows())

    assert s.latest_year == ref_year == "2025"
    assert len(s.records) == len(ref_records) == 6  # 预披露行被过滤
    assert {r.report_time for r in s.records} == {r.report_time for r in ref_records}
    by_time = {r.report_time: r for r in s.records}
    for ref in ref_records:
        got = by_time[ref.report_time]
        assert got.ex_dividend_date == ref.ex_dividend_date
        assert got.dividend_per_10 == pytest.approx(ref.dividend_per_10)
        assert got.plan_notice_date == ref.plan_notice_date


def test_records_sorted_by_ex_dividend_date():
    """records 按 ex_dividend_date 升序。"""
    dates = [r.ex_dividend_date for r in summarize_dividend_rows(_fixture_rows()).records]
    assert dates == sorted(dates)


def test_nan_guard_consistent_with_parse():
    """NaN/空值防护与 parse_dividend_rows 逐条一致（复用其解析，防未来分叉）。"""
    rows = [
        {"REPORT_DATE": "2025-12-31", "PRETAX_BONUS_RMB": float("nan"),
         "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2026-07-15"},
        {"REPORT_DATE": "2025-12-31", "PRETAX_BONUS_RMB": "",
         "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2026-07-15"},
        {"REPORT_DATE": "2025-12-31", "PRETAX_BONUS_RMB": 5.0,
         "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2026-07-15"},
    ]
    ref_records, ref_year = parse_dividend_rows(rows)
    s = summarize_dividend_rows(rows)
    assert len(s.records) == len(ref_records)
    assert s.latest_year == ref_year


# ---------------------------------------------------------------------------
# 财年判定（单一实现位于 sustainability.parse_dividend_rows，此处断言 label）
# ---------------------------------------------------------------------------

def test_fiscal_year_labels():
    """12月报告期→"YYYY年报"；3/6月→"YYYY中期分配"（NOT "半年报"）。"""
    labels = {r.report_time for r in summarize_dividend_rows(_fixture_rows()).records}
    assert "2025年报" in labels
    assert "2025中期分配" in labels
    assert "2024年报" in labels
    assert not any("半年报" in l for l in labels)


def test_latest_year_prefers_annual():
    """最新有年报的财年：2025 有年报，latest_year="2025"（即使 2024 也存在）。"""
    assert summarize_dividend_rows(_fixture_rows()).latest_year == "2025"


# ---------------------------------------------------------------------------
# 财年 10派合计 / TTM 10派合计
# ---------------------------------------------------------------------------

def test_fiscal_total_per_10():
    """最新完整财年（2025）全部记录：年报 7.33 + 中期 2.0 = 9.33。"""
    s = summarize_dividend_rows(_fixture_rows())
    assert s.fiscal_total_per_10 == pytest.approx(9.33)


def test_ttm_matches_compute_ttm_dividend():
    """TTM 10派合计与 compute_ttm_dividend 反推口径一致（total×10/shares）。"""
    as_of = date(2026, 7, 31)
    s = summarize_dividend_rows(_fixture_rows(), as_of_date=as_of)
    ref_records, _ = parse_dividend_rows(_fixture_rows())
    ttm_total, _, _, count = compute_ttm_dividend(ref_records, CHANGJIANG_SHARES, as_of_date=as_of)

    assert count == 2  # 窗口内：2026-07-15 + 2025-12-01（2025-07-01 在窗口外）
    assert ttm_total is not None
    # total = total_per_10 / 10 * total_shares → total_per_10 = total * 10 / total_shares
    assert s.ttm_total_per_10 == pytest.approx(ttm_total * 10 / CHANGJIANG_SHARES)
    assert s.ttm_total_per_10 == pytest.approx(9.33)


def test_ttm_window_boundary():
    """窗口边界：cutoff < ex_date <= as_of（2025-07-01 在窗口外，不计入）。"""
    s = summarize_dividend_rows(_fixture_rows(), as_of_date=date(2026, 7, 31))
    ex_dates = {r.ex_dividend_date for r in parse_dividend_rows(_fixture_rows())[0]}
    assert "2025-07-01" in ex_dates  # 存在但被窗口排除
    assert s.ttm_total_per_10 == pytest.approx(9.33)


# ---------------------------------------------------------------------------
# 边界
# ---------------------------------------------------------------------------

def test_empty_and_none_input():
    """空列表 / None（取数失败）→ 全空汇总，不抛。"""
    for rows in ([], None):
        s = summarize_dividend_rows(rows)
        assert s.records == []
        assert s.latest_year is None
        assert s.fiscal_total_per_10 == 0.0
        assert s.ttm_total_per_10 == 0.0
        assert s.source == "东财"


def test_all_interim_no_latest_year():
    """全中期分配 → latest_year=None、fiscal_total=0.0；TTM 正常聚合。"""
    rows = [
        # test_fiscal_year_crosscheck.py 报告期 3-31 行（实施）
        {"REPORT_DATE": "2025-03-31", "PRETAX_BONUS_RMB": 3.0,
         "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": ""},
        # test_sustainability_data.py 中期行
        {"REPORT_DATE": "2025-06-30", "PRETAX_BONUS_RMB": 2.0,
         "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2025-12-01"},
    ]
    s = summarize_dividend_rows(rows, as_of_date=date(2026, 7, 31))
    assert len(s.records) == 2
    assert {r.report_time for r in s.records} == {"2025中期分配"}
    assert s.latest_year is None
    assert s.fiscal_total_per_10 == 0.0
    assert s.ttm_total_per_10 == pytest.approx(2.0)  # 空 EX 日期的 3.0 行不进 TTM


def test_source_label():
    """source 标注默认 "东财"，可覆盖。"""
    assert summarize_dividend_rows(_fixture_rows()).source == "东财"
    assert summarize_dividend_rows(_fixture_rows(), source="mootdx xdxr").source == "mootdx xdxr"
