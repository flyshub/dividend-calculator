"""选股器缓存快照（spec #67，工单 #69）。

管理 data/screener.db（独立于 backtest.db），4 张表：
- stock_list：全 A 股票列表（低频刷新）
- quote_snapshot：行情快照（每日刷新，腾讯批量）
- dividend_snapshot：股息快照（年报季刷新）
- finance_snapshot：财务快照（年报季刷新）

每表含 updated_at + *_source（数据铁律：字段可溯源）。
增量规则：行情每日、股息/财务低频——通过 max_age_days 判定 stale。
PR 不建表（由 quote.pe_ttm + finance.roe_latest 派生，筛选时实时算）。
"""
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent  # 项目根（dividend-calculator/）

@dataclass(frozen=True)
class QuoteSnapshot:
    code: str
    name: Optional[str] = None           # 股票名称（辅助字段）
    price: Optional[float] = None
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    total_shares: Optional[float] = None      # 总股本 Index73（A+H 铁律）
    market_cap: Optional[float] = None        # price * total_shares
    quote_time: str = ""                       # 快照时点
    source: str = ""                           # 数据来源（铁律）
    updated_at: str = field(default_factory=lambda: date.today().isoformat())


@dataclass(frozen=True)
class DividendSnapshot:
    code: str
    real_yield: Optional[float]        # 真实股息率（拉取时旧值；筛选用 total_dividend/当日市值 实时重算）
    ttm_yield: Optional[float]         # TTM 股息率（同上）
    real_yield_year: Optional[str]     # 对应财年
    ttm_period: Optional[str]          # TTM 期间
    total_dividend: Optional[float] = None   # 最近完整财年分红总额（元，月频不变）
    ttm_dividend: Optional[float] = None     # TTM 分红总额（元，月频不变）
    dividend_source: str = ""          # 分红数据来源（铁律）
    updated_at: str = field(default_factory=lambda: date.today().isoformat())


@dataclass(frozen=True)
class FinanceSnapshot:
    code: str
    roe_latest: Optional[float]        # 最新年报 ROE
    roe_period: Optional[str]          # 报告期
    net_profit_annual: Optional[float]
    payout_ratio: Optional[float]
    finance_source: str = ""           # 财务数据来源（铁律）
    updated_at: str = field(default_factory=lambda: date.today().isoformat())


@dataclass(frozen=True)
class SustainabilitySnapshot:
    """可持续性评估的预缓存基础数据（提速用，避免逐股重复拉网络）。

    存储 assess_with_auto_fetch 所需的原始数据（JSON 序列化），评估时注入复用。
    """
    code: str
    financial_rows: Optional[str] = None   # JSON: 东财财务行
    cashflow_rows: Optional[str] = None    # JSON: 东财现金流行
    dividend_rows: Optional[str] = None    # JSON: 东财分红行
    industry: Optional[str] = None         # 行业分类
    price_change_1y: Optional[float] = None  # 1 年涨跌
    top10_holding: Optional[float] = None  # 前十大股东占比
    source: str = ""                       # 数据来源（铁律）
    updated_at: str = field(default_factory=lambda: date.today().isoformat())


@dataclass(frozen=True)
class StockListItem:
    code: str
    name: str
    market: str                        # sh / sz
    updated_at: str = field(default_factory=lambda: date.today().isoformat())


class ScreenerCache:
    """data/screener.db 的读写 + 增量过期判定。"""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS stock_list (
        code TEXT PRIMARY KEY, name TEXT, market TEXT, updated_at TEXT);

    CREATE TABLE IF NOT EXISTS quote_snapshot (
        code TEXT PRIMARY KEY, name TEXT, price REAL, pe_ttm REAL, pb REAL,
        total_shares REAL, market_cap REAL, quote_time TEXT, source TEXT, updated_at TEXT);

    CREATE TABLE IF NOT EXISTS dividend_snapshot (
        code TEXT PRIMARY KEY, real_yield REAL, ttm_yield REAL,
        real_yield_year TEXT, ttm_period TEXT, total_dividend REAL, ttm_dividend REAL,
        dividend_source TEXT, updated_at TEXT);

    CREATE TABLE IF NOT EXISTS finance_snapshot (
        code TEXT PRIMARY KEY, roe_latest REAL, roe_period TEXT,
        net_profit_annual REAL, payout_ratio REAL, finance_source TEXT, updated_at TEXT);

    CREATE TABLE IF NOT EXISTS sustainability_snapshot (
        code TEXT PRIMARY KEY, financial_rows TEXT, cashflow_rows TEXT,
        dividend_rows TEXT, industry TEXT, price_change_1y REAL,
        top10_holding REAL, source TEXT, updated_at TEXT);
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else PROJECT_ROOT / "data" / "screener.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(self._SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection):
        """增量迁移：为旧 DB 补新列（ALTER TABLE ADD COLUMN）。

        修复股息率实时化后，旧 dividend_snapshot 缺 total_dividend/ttm_dividend 列，
        其他机器/CI 用旧 DB 会报 no such column。此处幂等补齐。
        """
        cols = {r[1] for r in conn.execute("PRAGMA table_info(dividend_snapshot)").fetchall()}
        if "total_dividend" not in cols:
            conn.execute("ALTER TABLE dividend_snapshot ADD COLUMN total_dividend REAL")
        if "ttm_dividend" not in cols:
            conn.execute("ALTER TABLE dividend_snapshot ADD COLUMN ttm_dividend REAL")
        conn.commit()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def tables(self) -> List[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        return [r[0] for r in rows]

    # ---- stock_list ----

    def upsert_stock_list(self, items: List[StockListItem]):
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO stock_list (code, name, market, updated_at) "
                "VALUES (?, ?, ?, ?)",
                [(i.code, i.name, i.market, i.updated_at) for i in items],
            )

    # ---- quote_snapshot ----

    def upsert_quote(self, q: QuoteSnapshot):
        self.upsert_quotes([q])

    def upsert_quotes(self, quotes: List[QuoteSnapshot]):
        with self._conn() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO quote_snapshot "
                "(code, name, price, pe_ttm, pb, total_shares, market_cap, quote_time, source, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(q.code, q.name, q.price, q.pe_ttm, q.pb, q.total_shares, q.market_cap,
                  q.quote_time, q.source, q.updated_at) for q in quotes],
            )

    def get_quote(self, code: str) -> Optional[QuoteSnapshot]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT code, name, price, pe_ttm, pb, total_shares, market_cap, quote_time, source, updated_at "
                "FROM quote_snapshot WHERE code=?", (code,)
            ).fetchone()
        return QuoteSnapshot(*row) if row else None

    def is_quote_stale(self, code: str, max_age_days: int = 1) -> bool:
        """行情按 max_age_days 判定过期（默认每日刷新）。"""
        return self._is_stale("quote_snapshot", code, max_age_days)

    # ---- dividend_snapshot ----

    def upsert_dividend(self, d: DividendSnapshot):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO dividend_snapshot "
                "(code, real_yield, ttm_yield, real_yield_year, ttm_period, total_dividend, ttm_dividend, dividend_source, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (d.code, d.real_yield, d.ttm_yield, d.real_yield_year, d.ttm_period,
                 d.total_dividend, d.ttm_dividend, d.dividend_source, d.updated_at),
            )

    def get_dividend(self, code: str) -> Optional[DividendSnapshot]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT code, real_yield, ttm_yield, real_yield_year, ttm_period, total_dividend, ttm_dividend, dividend_source, updated_at "
                "FROM dividend_snapshot WHERE code=?", (code,)
            ).fetchone()
        return DividendSnapshot(*row) if row else None

    def is_dividend_stale(self, code: str, max_age_days: int = 30) -> bool:
        """股息按 max_age_days 判定过期（默认低频/年报季）。"""
        return self._is_stale("dividend_snapshot", code, max_age_days)

    # ---- finance_snapshot ----

    def upsert_finance(self, f: FinanceSnapshot):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO finance_snapshot "
                "(code, roe_latest, roe_period, net_profit_annual, payout_ratio, finance_source, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f.code, f.roe_latest, f.roe_period, f.net_profit_annual,
                 f.payout_ratio, f.finance_source, f.updated_at),
            )

    def get_finance(self, code: str) -> Optional[FinanceSnapshot]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT code, roe_latest, roe_period, net_profit_annual, payout_ratio, finance_source, updated_at "
                "FROM finance_snapshot WHERE code=?", (code,)
            ).fetchone()
        return FinanceSnapshot(*row) if row else None

    def is_finance_stale(self, code: str, max_age_days: int = 30) -> bool:
        return self._is_stale("finance_snapshot", code, max_age_days)

    # ---- 批量读（性能优化：一次读全表，替代逐股查询）----

    def get_all_quotes(self) -> Dict[str, QuoteSnapshot]:
        """一次读全表行情 → {code: QuoteSnapshot}。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT code, name, price, pe_ttm, pb, total_shares, market_cap, quote_time, source, updated_at "
                "FROM quote_snapshot").fetchall()
        return {r[0]: QuoteSnapshot(*r) for r in rows}

    def get_all_dividends(self) -> Dict[str, DividendSnapshot]:
        """一次读全表股息 → {code: DividendSnapshot}。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT code, real_yield, ttm_yield, real_yield_year, ttm_period, total_dividend, ttm_dividend, dividend_source, updated_at "
                "FROM dividend_snapshot").fetchall()
        return {r[0]: DividendSnapshot(*r) for r in rows}

    def get_all_finance(self) -> Dict[str, FinanceSnapshot]:
        """一次读全表财务 → {code: FinanceSnapshot}。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT code, roe_latest, roe_period, net_profit_annual, payout_ratio, finance_source, updated_at "
                "FROM finance_snapshot").fetchall()
        return {r[0]: FinanceSnapshot(*r) for r in rows}

    def get_all_sustainability(self) -> Dict[str, SustainabilitySnapshot]:
        """一次读全表可持续性快照 → {code: SustainabilitySnapshot}。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT code, financial_rows, cashflow_rows, dividend_rows, industry, "
                "price_change_1y, top10_holding, source, updated_at "
                "FROM sustainability_snapshot").fetchall()
        return {r[0]: SustainabilitySnapshot(*r) for r in rows}

    # ---- sustainability_snapshot ----

    def upsert_sustainability(self, s: SustainabilitySnapshot):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sustainability_snapshot "
                "(code, financial_rows, cashflow_rows, dividend_rows, industry, "
                " price_change_1y, top10_holding, source, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (s.code, s.financial_rows, s.cashflow_rows, s.dividend_rows, s.industry,
                 s.price_change_1y, s.top10_holding, s.source, s.updated_at),
            )

    def get_sustainability(self, code: str) -> Optional[SustainabilitySnapshot]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT code, financial_rows, cashflow_rows, dividend_rows, industry, "
                "price_change_1y, top10_holding, source, updated_at "
                "FROM sustainability_snapshot WHERE code=?", (code,)
            ).fetchone()
        return SustainabilitySnapshot(*row) if row else None

    def is_sustainability_stale(self, code: str, max_age_days: int = 30) -> bool:
        """可持续性预缓存按 max_age_days 判定过期（默认 30 天，覆盖财报季）。"""
        return self._is_stale("sustainability_snapshot", code, max_age_days)

    # ---- helpers ----

    def _is_stale(self, table: str, code: str, max_age_days: int) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT updated_at FROM {table} WHERE code=?", (code,)
            ).fetchone()
        if not row or not row[0]:
            return True
        try:
            updated = datetime.fromisoformat(row[0]).date()
        except ValueError:
            return True
        return (date.today() - updated).days > max_age_days
