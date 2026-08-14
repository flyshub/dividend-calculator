#!/usr/bin/env python3
"""T2 历史回测数据库构建（issue #85）：全 A 四层漏斗回测数据管线（方案 V3）。

产出 data/backtest.db，6 张表：
  stock_list       全 A（含退市，消除幸存者偏差）
  daily_price      日频不复权收盘价（腾讯 fqkline 分段拉取，2013-2026 两段）
  daily_pe         日频 PE_TTM（akshare 百度估值，列名 date/value）
  dividend_history 历史分红（东财 RPT_SHAREBONUS_DET，含公告日，回测无未来函数约束）
  finance_history  历史财务（东财 MAINFINADATA，仅保留 12-31 完整财年，month==12 规则）
  index_daily      基准指数（中证全收益 H00922/H00300）
  build_progress   断点续传标记（按 表×code 记录已完成，空结果也标记，避免重复请求）

数据铁律：数据源不可用即记缺失（0 行 + 标记完成），绝不编造、推算补缺。
网络请求统一 3 次退避重试 + 30s 读取超时；批量拉取每请求间隔 0.15s 限速友好。

口径说明：
  - PRETAX_BONUS_RMB 为「每 10 股派息」（与 site/js/calculator.js 一致），
    字段名 cash_div_10shares 如实反映单位，未按每股折算（口径准确优先）。
  - ROE 字段取 ROEJQ（加权净资产收益率）：实测 ROE_WEIGHTED 在该东财接口全为 None
    （死字段），ROEJQ 有值且与公开年报核对一致（招行 2024 加权 ROE 14.49%）。
  - 沪市退市列表只提供「暂停上市日期」（无正式退市日期字段），
    delist_date 存该值：暂停上市日 = 最后交易日，回测以此为退市边界更准确。
  - 现存 A 股列表（stock_info_a_code_name）无上市日期字段 → list_date 留 NULL。
  - 中证官网 closeweight xls 下载路径已失效（返回 SPA HTML），
    改用 akshare stock_zh_index_hist_csindex（同一中证官方数据源，含全收益指数）。

用法:
    cd dividend-calculator
    python scripts/build_backtest_db.py --sample          # 抽样 5 只快速验证
    python scripts/build_backtest_db.py                   # 全 A 全量
    python scripts/build_backtest_db.py --table daily_pe  # 只构建单表（可断点续传）
    python scripts/build_backtest_db.py --codes 600519,000858  # 自定义代码
"""
import argparse
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import re

import requests
import requests.adapters
from urllib3.util.retry import Retry

from src.eastmoney_fetcher import fetch_dividend_rows, fetch_financial_rows

_FIELD_TOTAL_SHARES = 73  # 腾讯行情字段下标（总股本，含 A+H，A+H 必须用此）

DB_PATH = PROJECT_ROOT / "data" / "backtest.db"

SAMPLE_CODES = ["600036", "600900", "601398", "000001", "601988"]
INDEX_CODES = ["H00922", "H00300"]

_KLINE_URL = "https://ifzq.gtimg.cn/appstock/app/fqkline/get"
# 腾讯日K单次最多约 2000 根（实测 3000/5000 被拒），2013-2026 分两段
_KLINE_SEGMENTS = [("2013-01-01", "2018-12-31"), ("2019-01-01", None)]  # None=今天
_RATE_LIMIT_SLEEP = 0.15  # 批量拉取限速友好（0.1-0.3s）

# GitHub Actions runner 位于海外，东财/腾讯接口偶发限流超时（CLAUDE.md 已知坑）
_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_HTTP = requests.Session()
_HTTP.headers.update(_UA)
_HTTP.mount(
    "https://",
    requests.adapters.HTTPAdapter(
        max_retries=Retry(total=3, connect=3, read=3, backoff_factor=1.0,
                          status_forcelist=[429, 500, 502, 503, 504]),
    ),
)

SCHEMA = {
    "stock_list": """
        CREATE TABLE IF NOT EXISTS stock_list (
            code        TEXT PRIMARY KEY,
            name        TEXT,
            list_date   TEXT,   -- 现存 A 股来源无此字段，留 NULL（不虚构）
            delist_date TEXT,   -- 沪市为暂停上市日期（无正式退市字段）
            board       TEXT CHECK (board IN ('SH','SZ','BJ'))
        )""",
    "daily_price": """
        CREATE TABLE IF NOT EXISTS daily_price (
            code  TEXT NOT NULL,
            date  TEXT NOT NULL,
            close REAL,
            PRIMARY KEY (code, date)
        )""",
    "daily_pe": """
        CREATE TABLE IF NOT EXISTS daily_pe (
            code   TEXT NOT NULL,
            date   TEXT NOT NULL,
            pe_ttm REAL,
            PRIMARY KEY (code, date)
        )""",
    "dividend_history": """
        CREATE TABLE IF NOT EXISTS dividend_history (
            code              TEXT NOT NULL,
            announce_date     TEXT,   -- NOTICE_DATE 公告日（无未来函数约束的关键）
            report_date       TEXT,   -- REPORT_DATE 报告期（12-31 完整财年 / 中期）
            ex_dividend_date  TEXT,   -- EX_DIVIDEND_DATE 除权除息日
            cash_div_10shares REAL,   -- PRETAX_BONUS_RMB 每10股派息
            bonus_ratio       REAL,   -- BONUS_RATIO 每10股送股数（送股，除权因子）
            trans_ratio       REAL,   -- TRAN_ADD_RATIO 每10股转增股数（转增，除权因子）
            PRIMARY KEY (code, report_date)
        )""",
    "finance_history": """
        CREATE TABLE IF NOT EXISTS finance_history (
            code                TEXT NOT NULL,
            report_date         TEXT NOT NULL,  -- 仅 12-31 完整财年（month==12 规则）
            roe                 REAL,   -- ROEJQ 加权净资产收益率（ROE_WEIGHTED 为该接口死字段，实测全 None）
            net_profit          REAL,   -- PARENTNETPROFIT 归母净利润
            net_cash_operate    REAL,   -- NETCASH_OPERATE_PK 经营现金流净额（银行替代字段）
            bps                 REAL,   -- BPS 每股净资产
            newcapitalader      REAL,   -- NEWCAPITALADER 资本充足率（银行专项）
            loan_provision_ratio REAL,  -- LOAN_PROVISION_RATIO 拨备覆盖率（银行专项）
            notice_date         TEXT,   -- NOTICE_DATE 实际披露日（消除财报未来函数的关键，T2 #107）
            PRIMARY KEY (code, report_date)
        )""",
    "index_daily": """
        CREATE TABLE IF NOT EXISTS index_daily (
            code  TEXT NOT NULL,
            date  TEXT NOT NULL,
            close REAL,
            PRIMARY KEY (code, date)
        )""",
    "build_progress": """
        CREATE TABLE IF NOT EXISTS build_progress (
            table_name TEXT NOT NULL,
            code       TEXT NOT NULL,
            built_at   TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (table_name, code)
        )""",
    "total_shares": """
        CREATE TABLE IF NOT EXISTS total_shares (
            code         TEXT PRIMARY KEY,
            total_shares REAL NOT NULL,
            asof         TEXT NOT NULL DEFAULT (datetime('now'))  -- 拉取日期（当前快照，无历史）
        )""",
    "industry": """
        CREATE TABLE IF NOT EXISTS industry (
            code     TEXT PRIMARY KEY,
            industry TEXT NOT NULL,   -- EM2016 优先 / INDUSTRYCSRC1 降级
            asof     TEXT NOT NULL DEFAULT (datetime('now'))
        )""",
}

# 各表独立构建函数（可单独跑、断点续传）
BUILDERS = {}


# ---------------------------------------------------------------------------
# 通用
# ---------------------------------------------------------------------------

def _get(url: str, **kwargs) -> requests.Response:
    """统一带重试会话的 GET（3 次退避 + 30s 读取超时）。"""
    kwargs.setdefault("timeout", (5, 30))
    return _HTTP.get(url, **kwargs)


def _pace() -> None:
    """批量拉取限速友好：每请求间隔 0.15s。"""
    time.sleep(_RATE_LIMIT_SLEEP)


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    for ddl in SCHEMA.values():
        conn.execute(ddl)
    _migrate(conn)
    conn.commit()


# 历史库演进：旧表缺新列时 ALTER ADD COLUMN（CREATE TABLE IF NOT EXISTS 不加列）
_MIGRATIONS = {
    "dividend_history": [
        ("bonus_ratio", "REAL"),
        ("trans_ratio", "REAL"),
    ],
    "finance_history": [
        ("notice_date", "TEXT"),
    ],
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in _MIGRATIONS.items():
        existing = {
            r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for name, typ in cols:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
                print(f"迁移：{table} 新增列 {name}")


def _mark_done(conn: sqlite3.Connection, table: str, code: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO build_progress (table_name, code) VALUES (?, ?)",
        (table, code),
    )
    conn.commit()


def _is_done(conn: sqlite3.Connection, table: str, code: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM build_progress WHERE table_name=? AND code=?", (table, code)
    ).fetchone()
    return row is not None


def _all_codes(conn: sqlite3.Connection) -> list:
    return [r[0] for r in conn.execute("SELECT code FROM stock_list ORDER BY code")]


def _count(conn: sqlite3.Connection, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _board(code: str) -> str:
    """6 开头沪市；8/4/92 开头北交所（4 旧代码、8/92 新代码，#42）；其余深市。"""
    if code.startswith("6"):
        return "SH"
    if code.startswith(("8", "4", "92")):
        return "BJ"
    return "SZ"


def _d10(s: str) -> str:
    """'2025-12-31 00:00:00' → '2025-12-31'；空值/None → None。"""
    if not s:
        return None
    return str(s)[:10]


# ---------------------------------------------------------------------------
# 行映射纯函数（供测试离线验证）
# ---------------------------------------------------------------------------

def financial_rows_to_db(code: str, rows: list) -> list:
    """东财财务行 → finance_history 元组；仅保留 12-31 完整财年（month==12 规则）。

    ROE 取 ROEJQ（加权净资产收益率）：实测 ROE_WEIGHTED 在该接口全为 None（死字段，
    0/100 非空），ROEJQ 有值且与年报核对一致（招行 2024 加权 ROE 14.49%）。
    rows 为空 → 返回 []（取数失败或真无数据，均记缺失，不虚构）。
    """
    out = []
    for r in rows:
        report = _d10(r.get("REPORT_DATE"))
        if not report or not report.endswith("-12-31"):
            continue  # 3/6/9 月为中期分配，不构成完整财年
        out.append((
            code, report,
            r.get("ROEJQ") if r.get("ROEJQ") is not None else r.get("ROE_WEIGHTED"),
            r.get("PARENTNETPROFIT"),
            r.get("NETCASH_OPERATE_PK"), r.get("BPS"),
            r.get("NEWCAPITALADER"), r.get("LOAN_PROVISION_RATIO"),
            _d10(r.get("NOTICE_DATE")),
        ))
    return out


def dividend_rows_to_db(code: str, rows: list) -> list:
    """东财分红行 → dividend_history 元组（含公告日，无未来函数约束）。

    送转字段：BONUS_RATIO=每10股送股数、BONUS_IT_RATIO=每10股转增股数
    （T1 #108，除权因子建模用；实测 600519 2014 年报 BONUS_RATIO=1 = 10送1）。
    """
    out = []
    for r in rows:
        report = _d10(r.get("REPORT_DATE"))
        if not report:
            continue
        out.append((
            code,
            _d10(r.get("NOTICE_DATE")),
            report,
            _d10(r.get("EX_DIVIDEND_DATE")),
            r.get("PRETAX_BONUS_RMB"),
            r.get("BONUS_RATIO"),
            r.get("BONUS_IT_RATIO"),
        ))
    return out


def parse_kline(code: str, resp_json: dict) -> list:
    """腾讯 fqkline 响应 → daily_price 元组。不复权（param 空复权参数 → day 键）。

    行格式: [date, open, close, high, low, ...]，close 取 index 2。
    """
    prefix = _tencent_prefix(code)
    rows = ((resp_json.get("data") or {}).get(f"{prefix}{code}") or {}).get("day") or []
    out = []
    for row in rows:
        try:
            out.append((code, str(row[0]), float(row[2])))
        except (IndexError, TypeError, ValueError):
            continue  # 坏行跳过，不虚构
    return out


def _tencent_prefix(code: str) -> str:
    if code.startswith("6"):
        return "sh"
    if code.startswith(("8", "4", "92")):
        return "bj"
    return "sz"


# ---------------------------------------------------------------------------
# 各表构建函数
# ---------------------------------------------------------------------------

def build_stock_list(conn: sqlite3.Connection) -> None:
    """全 A 股票列表（含退市）：现存 + 沪退市 + 深退市（消除幸存者偏差）。"""
    import akshare as ak
    if _count(conn, "stock_list") > 0:
        print("stock_list 已构建，跳过")
        return
    merged = {}
    for _, r in ak.stock_info_a_code_name().iterrows():
        merged[str(r["code"])] = {
            "name": r["name"], "list_date": None, "delist_date": None,
            "board": _board(str(r["code"])),
        }
    n_delist = 0
    for _, r in ak.stock_info_sh_delist().iterrows():
        code = str(r["公司代码"])
        merged[code] = {
            "name": r["公司简称"], "list_date": _d10(r["上市日期"]),
            "delist_date": _d10(r["暂停上市日期"]),  # 沪无正式退市日期字段，暂停上市日≈最后交易日
            "board": "SH",
        }
        n_delist += 1
    for _, r in ak.stock_info_sz_delist().iterrows():
        code = str(r["证券代码"])
        merged[code] = {
            "name": r["证券简称"], "list_date": _d10(r["上市日期"]),
            "delist_date": _d10(r["终止上市日期"]), "board": "SZ",
        }
        n_delist += 1
    conn.executemany(
        "INSERT OR REPLACE INTO stock_list (code, name, list_date, delist_date, board)"
        " VALUES (:code, :name, :list_date, :delist_date, :board)",
        [{"code": c, **v} for c, v in merged.items()],
    )
    conn.commit()
    print(f"stock_list: {len(merged)} 只（现存 {len(merged) - n_delist} + 退市 {n_delist}）")


def build_daily_price(conn: sqlite3.Connection, codes: list) -> None:
    """日频不复权收盘价：腾讯 fqkline 分段拉取（2013-2018 / 2019-今）。"""
    n = 0
    for i, code in enumerate(codes):
        if _is_done(conn, "daily_price", code):
            continue
        rows = []
        failed = False
        for start, end in _KLINE_SEGMENTS:
            if end is None:
                end = date.today().isoformat()
            url = f"{_KLINE_URL}?param={_tencent_prefix(code)}{code},day,{start},{end},2000,"
            try:
                rows += parse_kline(code, _get(url).json())
            except Exception as e:
                print(f"  [warn] {code} 日K拉取失败: {e}")
                failed = True
                break
            _pace()
        if failed:
            # 拉取异常不标记完成，下次重试（断点续传不跳过失败项）
            continue
        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO daily_price (code, date, close) VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()
            n += len(rows)
        _mark_done(conn, "daily_price", code)
        if (i + 1) % 50 == 0:
            print(f"  daily_price 进度 {i + 1}/{len(codes)}")
    print(f"daily_price: {n} 行写入")


def build_daily_pe(conn: sqlite3.Connection, codes: list) -> None:
    """日频 PE_TTM 历史：akshare 百度估值（列名 date/value，全历史）。"""
    import akshare as ak
    if hasattr(ak, "set_verbose"):
        ak.set_verbose(False)
    n = 0
    for i, code in enumerate(codes):
        if _is_done(conn, "daily_pe", code):
            continue
        try:
            df = ak.stock_zh_valuation_baidu(symbol=code, indicator="市盈率(TTM)", period="全部")
            rows = [(code, str(r["date"]), float(r["value"])) for _, r in df.iterrows()]
        except Exception as e:
            print(f"  [warn] {code} 百度估值拉取失败: {e}")
            rows = []
        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO daily_pe (code, date, pe_ttm) VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()
            n += len(rows)
        _mark_done(conn, "daily_pe", code)
        _pace()
        if (i + 1) % 50 == 0:
            print(f"  daily_pe 进度 {i + 1}/{len(codes)}")
    print(f"daily_pe: {n} 行写入")


def build_dividend(conn: sqlite3.Connection, codes: list) -> None:
    """历史分红：东财 RPT_SHAREBONUS_DET（复用 fetch_dividend_rows）。

    None=取数失败（不标记完成，下次续传重试）；[]=真无分红（标记完成）。
    """
    n = 0
    for i, code in enumerate(codes):
        if _is_done(conn, "dividend_history", code):
            continue
        try:
            rows = fetch_dividend_rows(code)
        except Exception as e:
            print(f"  [warn] {code} 分红拉取异常: {e}")
            continue
        if rows is None:
            print(f"  [warn] {code} 分红取数失败，跳过（下次重试）")
            continue
        db_rows = dividend_rows_to_db(code, rows)
        if db_rows:
            conn.executemany(
                "INSERT OR REPLACE INTO dividend_history (code, announce_date, report_date,"
                " ex_dividend_date, cash_div_10shares, bonus_ratio, trans_ratio)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                db_rows,
            )
            conn.commit()
            n += len(db_rows)
        _mark_done(conn, "dividend_history", code)
        _pace()
        if (i + 1) % 50 == 0:
            print(f"  dividend_history 进度 {i + 1}/{len(codes)}")
    print(f"dividend_history: {n} 行写入")


def build_finance(conn: sqlite3.Connection, codes: list) -> None:
    """历史财务：东财 MAINFINADATA（仅保留 12-31 完整财年）。"""
    n = 0
    for i, code in enumerate(codes):
        if _is_done(conn, "finance_history", code):
            continue
        try:
            rows = fetch_financial_rows(code)
        except Exception as e:
            print(f"  [warn] {code} 财务拉取异常: {e}")
            continue
        db_rows = financial_rows_to_db(code, rows)
        if db_rows:
            conn.executemany(
                "INSERT OR REPLACE INTO finance_history (code, report_date, notice_date,"
                " roe, net_profit, net_cash_operate, bps, newcapitalader,"
                " loan_provision_ratio) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                db_rows,
            )
            conn.commit()
            n += len(db_rows)
        _mark_done(conn, "finance_history", code)
        _pace()
        if (i + 1) % 50 == 0:
            print(f"  finance_history 进度 {i + 1}/{len(codes)}")
    print(f"finance_history: {n} 行写入")


def build_index_daily(conn: sqlite3.Connection, codes: list = None) -> None:
    """基准指数：中证全收益 H00922/H00300（akshare 中证官方接口）。

    注：中证官网 closeweight xls 路径已失效（返回 SPA HTML），
    stock_zh_index_hist_csindex 走同一中证官方数据源（T2 实测 H00922 3304 行）。
    """
    import akshare as ak
    if hasattr(ak, "set_verbose"):
        ak.set_verbose(False)
    today = date.today().strftime("%Y%m%d")
    for code in INDEX_CODES:
        if _is_done(conn, "index_daily", code):
            continue
        try:
            df = ak.stock_zh_index_hist_csindex(symbol=code, start_date="20130101", end_date=today)
            rows = [(code, str(r["日期"]), float(r["收盘"])) for _, r in df.iterrows()]
        except Exception as e:
            print(f"  [warn] 指数 {code} 拉取失败: {e}")
            rows = []
        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO index_daily (code, date, close) VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()
        _mark_done(conn, "index_daily", code)
        _pace()
    print(f"index_daily: 已写入 {INDEX_CODES}")


_BATCH_QUOTES_URL = "https://qt.gtimg.cn/q="


def build_total_shares(conn: sqlite3.Connection, codes: list) -> None:
    """总股本（腾讯 Index 73，含 A+H）—— 当前快照（无历史，单值）。

    批量接口 50 只/批，单批 ~0.5s，5903 只约 60s（含限速）。失败批次中的 code
    不标记完成，下次重试（断点续传不跳过失败项）。
    """
    pending = [c for c in codes if not _is_done(conn, "total_shares", c)]
    print(f"total_shares: 待拉 {len(pending)}/{len(codes)}")
    BATCH = 50
    n_ok = 0
    for i in range(0, len(pending), BATCH):
        batch = pending[i:i + BATCH]
        try:
            qs = ",".join(f"{_tencent_prefix(c)}{c}" for c in batch)
            text = _get(f"{_BATCH_QUOTES_URL}{qs}").text
        except Exception as e:
            print(f"  [warn] 批 {i//BATCH+1} 失败: {e}")
            continue
        rows = []
        for c in batch:
            prefix = _tencent_prefix(c)
            # 实际响应：v_sh600036="...~"（每个 code 由 v_<prefix><code>="..." 标识）
            m = re.search(rf'v_{prefix}{c}="([^"]+)"', text)
            if not m or len(m.group(1).split("~")) <= _FIELD_TOTAL_SHARES:
                continue
            fields = m.group(1).split("~")
            try:
                ts = float(fields[_FIELD_TOTAL_SHARES])
            except (ValueError, TypeError):
                continue
            if ts > 0:
                rows.append((c, ts))
                _mark_done(conn, "total_shares", c)
        if rows:
            conn.executemany(
                "INSERT OR REPLACE INTO total_shares (code, total_shares) VALUES (?, ?)",
                rows,
            )
            conn.commit()
            n_ok += len(rows)
        if (i // BATCH + 1) % 10 == 0:
            print(f"  total_shares 批 {i//BATCH+1}/{(len(pending)+BATCH-1)//BATCH}, ok={n_ok}")
        _pace()
    print(f"total_shares: {n_ok} 只写入")


def build_industry(conn: sqlite3.Connection, codes: list) -> None:
    """行业（东财 EM2016 优先 / INDUSTRYCSRC1 降级）—— 当前快照（单值）。

    单只串行 + _pace 限速（东节数据源限流）；5903 只约 25 分钟。
    失败 code 不标记完成。
    """
    from src.eastmoney_fetcher import fetch_industry
    n_ok = 0
    pending = [c for c in codes if not _is_done(conn, "industry", c)]
    print(f"industry: 待拉 {len(pending)}/{len(codes)}")
    for i, code in enumerate(pending):
        try:
            ind = fetch_industry(code)
        except Exception as e:
            print(f"  [warn] {code} industry 拉取失败: {e}")
            continue
        if ind:
            conn.execute(
                "INSERT OR REPLACE INTO industry (code, industry) VALUES (?, ?)",
                (code, ind),
            )
            conn.commit()
            _mark_done(conn, "industry", code)
            n_ok += 1
        if (i + 1) % 100 == 0:
            print(f"  industry 进度 {i+1}/{len(pending)}, ok={n_ok}")
        _pace()
    print(f"industry: {n_ok} 只写入")


BUILDERS.update({
    "daily_price": build_daily_price,
    "daily_pe": build_daily_pe,
    "dividend_history": build_dividend,
    "finance_history": build_finance,
    "index_daily": build_index_daily,
    "total_shares": build_total_shares,
    "industry": build_industry,
})


# ---------------------------------------------------------------------------
# 抽样验证断言（--sample 模式）
# ---------------------------------------------------------------------------

def _sample_assertions(conn: sqlite3.Connection) -> None:
    """抽样 5 只构建后的数据合理性断言（数据铁律：真实数据可验证）。"""
    print("\n=== 抽样断言 ===")

    # 600036 分红：≥ 20 条且最早除权日合理（2000-2010 区间）
    rows = conn.execute(
        "SELECT ex_dividend_date FROM dividend_history WHERE code='600036'"
        " AND ex_dividend_date IS NOT NULL ORDER BY report_date"
    ).fetchall()
    n_div = len(rows)
    assert n_div >= 20, f"600036 分红记录 {n_div} < 20"
    first_ex = rows[0][0]
    assert first_ex and "2000" <= first_ex[:4] <= "2010", f"最早除权日 {first_ex} 不合理"
    print(f"[ASSERT] 600036 分红 >= 20: {n_div} 条 | 最早除权日 {first_ex} ✓")

    # 600036 百度估值 ≥ 600 行
    n_pe = conn.execute("SELECT COUNT(*) FROM daily_pe WHERE code='600036'").fetchone()[0]
    assert n_pe >= 600, f"600036 百度估值 {n_pe} < 600"
    print(f"[ASSERT] 600036 百度估值 >= 600: {n_pe} 行 ✓")

    # 日K分段后最早日期 ≤ 2013-01-10
    first = conn.execute(
        "SELECT MIN(date) FROM daily_price WHERE code='600036'"
    ).fetchone()[0]
    assert first and first <= "2013-01-10", f"最早日K {first} > 2013-01-10"
    print(f"[ASSERT] 600036 日K最早 {first} <= 2013-01-10 ✓")

    # 财务仅 12-31 完整财年
    bad = conn.execute(
        "SELECT COUNT(*) FROM finance_history WHERE substr(report_date, 6) != '12-31'"
    ).fetchone()[0]
    assert bad == 0, f"finance_history 存在 {bad} 条非 12-31 记录"
    print(f"[ASSERT] finance_history 全部 12-31 完整财年（违规 {bad} 条）✓")

    print("=== 抽样断言全部通过 ===\n")


# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="构建历史回测数据库 data/backtest.db")
    parser.add_argument("--sample", action="store_true",
                        help=f"抽样模式：只构建 {SAMPLE_CODES}（快速验证）+ 全量 stock_list/index_daily")
    parser.add_argument("--table", choices=list(SCHEMA),
                        help="只构建指定表（可断点续传）")
    parser.add_argument("--codes", help="自定义构建代码（逗号分隔，替代抽样/全量）")
    args = parser.parse_args()

    conn = _connect()
    create_schema(conn)

    tables = [args.table] if args.table else list(SCHEMA)

    # stock_list 是其余各表的代码来源，独立构建（3 个批量接口，成本极低）
    if _count(conn, "stock_list") == 0:
        build_stock_list(conn)

    if args.codes:
        codes = args.codes.split(",")
    elif args.sample:
        codes = SAMPLE_CODES
    else:
        codes = _all_codes(conn)

    for t in tables:
        if t in ("stock_list", "build_progress"):
            continue
        print(f"== 构建 {t}（{len(codes)} 只）==")
        BUILDERS[t](conn, codes)

    print("\n=== 各表行数 ===")
    for t in ("stock_list", "daily_price", "daily_pe", "dividend_history",
              "finance_history", "index_daily"):
        print(f"  {t}: {_count(conn, t)}")

    if args.sample:
        _sample_assertions(conn)
    conn.close()


if __name__ == "__main__":
    main()
