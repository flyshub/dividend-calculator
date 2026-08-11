"""T7 报告生成器测试（issue #90）。"""
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
for p in (_ROOT, _SCRIPTS):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

from backtest_report import _pct, _num, _table  # noqa: E402


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def test_pct_none():
    assert _pct(None) == "N/A"


def test_pct_value():
    assert _pct(0.1234) == "12.34%"


def test_num_none():
    assert _num(None) == "N/A"


def test_num_value():
    assert _num(1.5) == "1.50"
    assert _num(1.5, prec=3) == "1.500"


def test_table_basic():
    out = _table(["A", "B"], [["1", "2"]])
    assert "| A | B |" in out
    assert "| --- | --- |" in out
    assert "| 1 | 2 |" in out


# ---------------------------------------------------------------------------
# 报告生成（端到端，用内存 DB + Mock lookup）
# ---------------------------------------------------------------------------
def _make_db(db_path: str):
    """建最小可用的 backtest.db（schema 对齐实际 DB）。"""
    src = _ROOT / "data" / "backtest.db"
    c = sqlite3.connect(db_path)
    if src.exists():
        # 从实际 DB 复制 schema（DDL only），保证列名一致
        src_conn = sqlite3.connect(str(src))
        for t in ("stock_list", "daily_price", "daily_pe", "dividend_history",
                  "finance_history", "index_daily", "build_progress"):
            ddl = src_conn.execute(
                f"SELECT sql FROM sqlite_master WHERE name='{t}'"
            ).fetchone()
            if ddl:
                c.execute(ddl[0])
        src_conn.close()
    else:
        # 无实际 DB 时手动建（CI 兜底）
        c.execute("CREATE TABLE stock_list (code TEXT, name TEXT, list_date TEXT, delist_date TEXT, board TEXT)")
        c.execute("CREATE TABLE daily_price (code TEXT, date TEXT, close REAL)")
        c.execute("CREATE TABLE daily_pe (code TEXT, date TEXT, pe_ttm REAL)")
        c.execute("CREATE TABLE dividend_history (code TEXT, announce_date TEXT, report_date TEXT, ex_dividend_date TEXT, cash_div_10shares REAL)")
        c.execute("CREATE TABLE finance_history (code TEXT, report_date TEXT, roe REAL, net_profit REAL, net_cash_operate REAL, bps REAL, newcapitalader REAL, loan_provision_ratio REAL)")
        c.execute("CREATE TABLE index_daily (code TEXT, date TEXT, close REAL)")
        c.execute("CREATE TABLE build_progress (table_name TEXT, code TEXT, UNIQUE(table_name, code))")

    codes = ["600036", "601398"]
    for code in codes:
        c.execute("INSERT INTO stock_list VALUES (?,?,?,?,?)",
                  (code, f"测试{code}", "2010-01-01", "", "SH"))
        for y in range(2013, 2026):
            for m in (3, 6, 9, 12):
                d = f"{y}-{m:02d}-15"
                c.execute("INSERT INTO daily_price VALUES (?,?,?)", (code, d, 10.0))
                c.execute("INSERT INTO daily_pe VALUES (?,?,?)", (code, d, 5.0))
        c.execute("INSERT INTO finance_history VALUES (?,?,?,?,?,?,?,?)",
                  (code, "2023-12-31", 15.0, 1e9, 5e8, 10.0, None, None))
        c.execute("INSERT INTO dividend_history VALUES (?,?,?,?,?)",
                  (code, "2023-06-01", "2022-12-31", "2023-07-01", 5.0))

    # 指数数据（季度末）
    for idx_code in ("H00922", "H00300"):
        for y in range(2013, 2026):
            for m in (3, 6, 9, 12):
                c.execute("INSERT INTO index_daily VALUES (?,?,?)",
                          (idx_code, f"{y}-{m:02d}-15", 1000.0))
    c.commit()
    c.close()


def test_generate_report_end_to_end(tmp_path):
    """端到端：建 DB → 生成报告 → 报告含核心段落。"""
    from backtest_report import generate_report
    db = str(tmp_path / "test.db")
    out = str(tmp_path / "report.md")
    _make_db(db)

    generate_report(db, out)

    content = Path(out).read_text(encoding="utf-8")
    # 核心段落标题
    assert "§1 数据范围与口径" in content
    assert "§2 分层增量超额" in content
    assert "§3 组合绩效 vs 双基准" in content
    assert "§4 稳健性检验" in content
    assert "§5 结论与限制" in content
    # 数据缺口标注（铁律）
    assert "已知数据缺口" in content or "已知限制" in content
    # 复现命令
    assert "复现" in content
    # 可重复运行（幂等）
    generate_report(db, out)
    assert Path(out).exists()


def test_report_handles_empty_db(tmp_path):
    """空 DB（无数据）→ 报告仍生成（N/A），不崩溃。"""
    from backtest_report import generate_report
    db = str(tmp_path / "empty.db")
    out = str(tmp_path / "report.md")
    _make_db(db)
    # 清空所有数据
    c = sqlite3.connect(db)
    for t in ("daily_price", "daily_pe", "dividend_history", "finance_history", "index_daily"):
        c.execute(f"DELETE FROM {t}")
    c.commit()
    c.close()

    generate_report(db, out)  # 不应抛异常
    content = Path(out).read_text(encoding="utf-8")
    assert "N/A" in content or "0.00%" in content
