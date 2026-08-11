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


# ---------------------------------------------------------------------------
# 各源 adapter（issue #97 主链路迁移）
# ---------------------------------------------------------------------------

def _fhps_df():
    """akshare stock_fhps_detail_em 列名 fixture（数值复用 test_regression_snapshot.py 真实值：
    2024年报 5.0 / 2025-06-30 中期 2.0 / 预披露 7.33 / 股东大会决议通过 8.0）。"""
    import pandas as pd
    return pd.DataFrame([
        {"报告期": "2024-12-31", "方案进度": "实施分配", "现金分红-现金分红比例": 5.0,
         "除权除息日": "2025-07-01", "预案公告日": "2025-04-30"},
        {"报告期": "2025-06-30", "方案进度": "实施分配", "现金分红-现金分红比例": 2.0,
         "除权除息日": "2025-12-01", "预案公告日": "2025-08-30"},
        # 预披露 / 未实施预案 → 应过滤（排除未落地预案，对齐 JS T5）
        {"报告期": "2025-12-31", "方案进度": "预披露", "现金分红-现金分红比例": 7.33,
         "除权除息日": "2026-07-15", "预案公告日": "2026-04-30"},
        {"报告期": "2025-12-31", "方案进度": "股东大会决议通过", "现金分红-现金分红比例": 8.0,
         "除权除息日": "", "预案公告日": ""},
    ])


def test_summarize_fhps_df():
    """fhps adapter：过滤预披露/未实施，财年/标签/除权日映射正确。"""
    from src.dividend_records import summarize_fhps_df
    s = summarize_fhps_df(_fhps_df(), as_of_date=date(2026, 7, 31))
    assert {r.report_time for r in s.records} == {"2024年报", "2025中期分配"}
    assert s.latest_year == "2024"  # 2025 仅有中期分配，无年报 → 非完整财年
    assert s.fiscal_total_per_10 == pytest.approx(5.0)
    by_time = {r.report_time: r for r in s.records}
    assert by_time["2024年报"].ex_dividend_date == "2025-07-01"
    assert by_time["2024年报"].plan_notice_date == "2025-04-30"
    assert s.ttm_total_per_10 == pytest.approx(2.0)  # 窗口内仅 2025-12-01（2025-07-01 在窗口外）
    assert s.source == "akshare fhps_detail_em"


def test_summarize_fhps_df_only_interim():
    """fhps 仅中期分配（无任何年报）→ latest_year=None、fiscal_total=0.0，
    与完整财年原则一致（无年报 → 不构成完整财年 → 主链路降级下源）。"""
    import pandas as pd
    from src.dividend_records import summarize_fhps_df
    df = pd.DataFrame([
        {"报告期": "2025-06-30", "方案进度": "实施分配", "现金分红-现金分红比例": 2.0,
         "除权除息日": "2025-12-01", "预案公告日": "2025-08-30"},
    ])
    s = summarize_fhps_df(df, as_of_date=date(2026, 7, 31))
    assert {r.report_time for r in s.records} == {"2025中期分配"}
    assert s.latest_year is None
    assert s.fiscal_total_per_10 == 0.0
    assert s.ttm_total_per_10 == pytest.approx(2.0)


def test_summarize_fhps_df_combined_keywords_filtered():
    """组合关键词「实施分配（未实施）」→ 过滤（T5 规则：含"实施"且不含"未实施"，
    双端同构；该组合是唯一能同时命中正反两词、易被简单 contains('实施') 误收的场景）。"""
    import pandas as pd
    from src.dividend_records import summarize_fhps_df
    df = pd.DataFrame([
        # 同时含"实施"与"未实施" → 必须过滤
        {"报告期": "2025-12-31", "方案进度": "实施分配（未实施）", "现金分红-现金分红比例": 5.0,
         "除权除息日": "2026-07-15", "预案公告日": "2026-04-30"},
        # 正常实施 → 保留（对照组，防过滤逻辑过宽）
        {"报告期": "2024-12-31", "方案进度": "实施分配", "现金分红-现金分红比例": 5.0,
         "除权除息日": "2025-07-01", "预案公告日": "2025-04-30"},
    ])
    s = summarize_fhps_df(df, as_of_date=date(2026, 7, 31))
    assert {r.report_time for r in s.records} == {"2024年报"}
    assert s.latest_year == "2024"
    assert s.fiscal_total_per_10 == pytest.approx(5.0)


def _cninfo_df():
    """akshare stock_dividend_cninfo 列名 fixture（600036 实地验证：2024年报 10派20 /
    2025半年报 10派10.13 / 2025年报 10派10.03；含无报告期特别分红 + 股改无派息行）。"""
    import pandas as pd
    return pd.DataFrame([
        {"报告时间": "2024年报", "实施方案分红说明": "10派20元(含税)",
         "派息比例": 20.0, "除权日": "2025-07-11"},
        {"报告时间": "2025半年报", "实施方案分红说明": "10派10.13元(含税)",
         "派息比例": 10.13, "除权日": "2026-01-16"},
        {"报告时间": "2025年报", "实施方案分红说明": "10派10.03元(含税)",
         "派息比例": 10.03, "除权日": "2026-07-10"},
        # 无报告期的特别分红（600036 实地：报告时间 NaN）→ 跳过
        {"报告时间": float("nan"), "实施方案分红说明": "10派1.8元(含税)",
         "派息比例": 1.8, "除权日": "2006-09-21"},
        # 股改分红无派息比例（600036 实地：派息比例 NaN）→ 跳过
        {"报告时间": "2005年报", "实施方案分红说明": "10转增0.8589股",
         "派息比例": float("nan"), "除权日": "2006-02-24"},
    ])


def test_summarize_cninfo_df():
    """cninfo adapter：文本报告时间 → 统一财年标签；半年报归中期分配。"""
    from src.dividend_records import summarize_cninfo_df
    s = summarize_cninfo_df(_cninfo_df(), as_of_date=date(2026, 7, 31))
    assert {r.report_time for r in s.records} == {"2024年报", "2025中期分配", "2025年报"}
    assert s.latest_year == "2025"
    assert s.fiscal_total_per_10 == pytest.approx(10.13 + 10.03)
    by_time = {r.report_time: r for r in s.records}
    assert by_time["2025年报"].ex_dividend_date == "2026-07-10"
    assert s.ttm_total_per_10 == pytest.approx(10.13 + 10.03)  # 窗口内 2026-01-16 + 2026-07-10
    assert s.source == "akshare cninfo"


def test_cninfo_quarterly_labeled_interim():
    """季度分红文本（600900 实地：2025三季报 10派2.1）→ 中期分配，非年报。"""
    import pandas as pd
    from src.dividend_records import summarize_cninfo_df
    df = pd.DataFrame([
        {"报告时间": "2025三季报", "实施方案分红说明": "10派2.1元(含税)",
         "派息比例": 2.1, "除权日": "2026-02-12"},
    ])
    s = summarize_cninfo_df(df, as_of_date=date(2026, 7, 31))
    assert {r.report_time for r in s.records} == {"2025中期分配"}
    assert s.latest_year is None
    assert s.fiscal_total_per_10 == 0.0
    assert s.ttm_total_per_10 == pytest.approx(2.1)
