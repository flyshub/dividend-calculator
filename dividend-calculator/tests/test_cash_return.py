"""现金回报率（分红 + 注销类回购）单测。"""
import pytest

from src.cash_return import (
    STATUS_FETCH_ERROR,
    STATUS_MISSING_INPUTS,
    STATUS_NO_FISCAL_YEAR,
    STATUS_NO_REPURCHASE,
    STATUS_OK,
    aggregate_repurchase,
    classify_repurchase_purpose,
    compute_cash_return,
    estimate_cash_return,
)


def row(amount, purpose, finish="2025-06-30", start="2025-01-10", end="2025-07-01"):
    return {
        "REPURAMOUNT": amount,
        "REPUROBJECTIVE": purpose,
        "FINISHDATE": finish,
        "REPURSTARTDATE": start,
        "REPURENDDATE": end,
    }


# ── 用途分类 ──────────────────────────────────────────────

def test_classify_buyback_when_mentions_cancel():
    assert classify_repurchase_purpose("回购股份将用于注销并减少注册资本") == "buyback"


def test_classify_mixed_plan_counts_as_buyback():
    assert classify_repurchase_purpose("用于依法注销减少注册资本及股权激励计划") == "buyback"


def test_classify_incentive_only():
    assert classify_repurchase_purpose("用于股权激励计划及/或员工持股计划") == "incentive"


def test_classify_unknown_when_empty():
    assert classify_repurchase_purpose("") == "unknown"
    assert classify_repurchase_purpose(None) == "unknown"


# ── 按财年归集 ────────────────────────────────────────────

def test_aggregate_counts_only_cancelled_invoices_in_year():
    rows = [
        row(1e9, "用于注销", finish="2025-08-29"),        # 计入
        row(3e9, "股权激励", finish="2025-04-08"),        # 激励，不计
        row(2e9, "用于注销", finish="2026-05-27"),        # 次年完成，不计
    ]
    agg = aggregate_repurchase(rows, 2025)
    assert agg["amount"] == pytest.approx(1e9)
    assert agg["events"] == 1
    assert agg["incentive"] == pytest.approx(3e9)


def test_aggregate_ignores_unfinished_but_reports_in_progress():
    rows = [row(1.21e10, "用于注销", finish=None, start="2026-03-30", end="2027-03-30")]
    agg = aggregate_repurchase(rows, 2026)
    assert agg["amount"] == 0
    assert agg["in_progress"] == pytest.approx(1.21e10)


def test_aggregate_unfinished_outside_window_not_counted():
    rows = [row(5e8, "用于注销", finish=None, start="2022-01-01", end="2022-12-31")]
    agg = aggregate_repurchase(rows, 2026)
    assert agg["in_progress"] == 0


def test_aggregate_unclassified_amount():
    rows = [row(5e8, "", finish="2025-03-01")]
    agg = aggregate_repurchase(rows, 2025)
    assert agg["amount"] == 0
    assert agg["unclassified"] == pytest.approx(5e8)


def test_aggregate_zero_or_missing_amount_skipped():
    rows = [{"REPURAMOUNT": None, "REPUROBJECTIVE": "用于注销", "FINISHDATE": "2025-01-01"},
            {"REPURAMOUNT": 0, "REPUROBJECTIVE": "用于注销", "FINISHDATE": "2025-01-01"}]
    agg = aggregate_repurchase(rows, 2025)
    assert agg["amount"] == 0 and agg["events"] == 0


# ── 计算 ─────────────────────────────────────────────────

def test_compute_ok_splits_dividend_and_repurchase():
    rows = [row(6e9, "用于注销", finish="2025-08-29")]
    res = compute_cash_return(dividend_total=2e10, market_cap=1e12, repurchase_rows=rows, fiscal_year=2025)
    assert res.cash_return_status == STATUS_OK
    assert res.cash_return_dividend_yield == pytest.approx(2.0)
    assert res.cash_return_repurchase_yield == pytest.approx(0.6)
    assert res.cash_return_total_yield == pytest.approx(2.6)


def test_compute_no_repurchase_still_reports_dividend():
    res = compute_cash_return(2e10, 1e12, [], 2025)
    assert res.cash_return_status == STATUS_NO_REPURCHASE
    assert res.cash_return_repurchase == 0
    assert res.cash_return_total_yield == pytest.approx(2.0)
    assert "无回购记录" in res.cash_return_note


def test_compute_fetch_error_does_not_treat_unknown_as_zero():
    res = compute_cash_return(2e10, 1e12, None, 2025, fetch_error=True)
    assert res.cash_return_status == STATUS_FETCH_ERROR
    assert res.cash_return_repurchase is None
    assert res.cash_return_total_yield is None          # 不能把未知当 0
    assert res.cash_return_dividend_yield == pytest.approx(2.0)


def test_compute_missing_market_cap():
    res = compute_cash_return(2e10, 0, [], 2025)
    assert res.cash_return_status == STATUS_MISSING_INPUTS
    assert res.cash_return_total_yield is None


def test_compute_without_fiscal_year():
    res = compute_cash_return(0, 1e12, [], None)
    assert res.cash_return_status == STATUS_NO_FISCAL_YEAR


def test_compute_notes_mention_excluded_amounts():
    rows = [row(3e9, "股权激励", finish="2025-04-08"),
            row(1.21e10, "用于注销", finish=None, start="2025-03-30", end="2027-03-30")]
    res = compute_cash_return(0, 1e12, rows, 2025)
    assert "激励类回购 30.00 亿元未计入" in res.cash_return_note
    assert "进行中回购 121.00 亿元未完成" in res.cash_return_note


def test_dividend_zero_keeps_repurchase_yield():
    rows = [row(1e10, "用于注销", finish="2025-05-05")]
    res = compute_cash_return(0, 1e12, rows, 2025)
    assert res.cash_return_dividend_yield == 0
    assert res.cash_return_total_yield == pytest.approx(1.0)


# ── 编排（取数语义）──────────────────────────────────────

def test_estimate_fetch_error_returns_fetch_error(monkeypatch):
    monkeypatch.setattr("src.cash_return.fetch_repurchase_rows", lambda code: None)
    res = estimate_cash_return("600519", 2e10, 1e12, 2025)
    assert res.cash_return_status == STATUS_FETCH_ERROR
    assert res.cash_return_total_yield is None


def test_estimate_no_records_returns_no_repurchase(monkeypatch):
    monkeypatch.setattr("src.cash_return.fetch_repurchase_rows", lambda code: [])
    res = estimate_cash_return("600036", 2e10, 1e12, 2025)
    assert res.cash_return_status == STATUS_NO_REPURCHASE
    assert res.cash_return_total_yield == pytest.approx(2.0)


def test_estimate_exception_degrades_to_fetch_error(monkeypatch):
    def boom(code):
        raise RuntimeError("network")
    monkeypatch.setattr("src.cash_return.fetch_repurchase_rows", boom)
    res = estimate_cash_return("600519", 2e10, 1e12, 2025)
    assert res.cash_return_status == STATUS_FETCH_ERROR


def test_estimate_skips_fetch_without_fiscal_year(monkeypatch):
    called = []
    monkeypatch.setattr("src.cash_return.fetch_repurchase_rows", lambda c: called.append(c) or [])
    res = estimate_cash_return("600519", 0, 1e12, None)
    assert res.cash_return_status == STATUS_NO_FISCAL_YEAR
    assert called == []


def test_compute_notes_mixed_plan():
    rows = [row(1e10, "用于注销减少注册资本及股权激励计划", finish="2025-06-01")]
    res = compute_cash_return(0, 1e12, rows, 2025)
    assert res.cash_return_repurchase == pytest.approx(1e10)
    assert "混合计划" in res.cash_return_note


def test_compute_accepts_string_fiscal_year():
    """页面/verify 传的是字符串财年，必须与 int 一致（曾因 '2025' <= 2025 抛 TypeError）。"""
    rows = [row(6e9, "用于注销", finish="2025-08-29")]
    res = compute_cash_return(2e10, 1e12, rows, "2025")
    assert res.cash_return_status == STATUS_OK
    assert res.cash_return_repurchase == pytest.approx(6e9)
