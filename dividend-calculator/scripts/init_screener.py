#!/usr/bin/env python3
"""选股器初始化/补全数据库（spec #67）。

从不同来源初始化全 A 股票列表到 screener.db，并用腾讯批量行情填充 quote_snapshot。

来源：
- --source all-a：akshare 全市场列表（~5400 只，全 A 含沪深京）
- --source backtest：backtest.db 历史成分股（588 只，沪深300+中证500）

增量合并：已存在的股票数据保留（不覆盖），仅补全缺失的。

用法:
    python scripts/init_screener.py --source all-a              # 全 A 补全
    python scripts/init_screener.py --source backtest           # 成分股初始化
    python scripts/init_screener.py --source all-a --with-finance  # 补全 + 导入 ROE
"""
import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.screener_cache import ScreenerCache, StockListItem  # noqa: E402
from src.screener_quotes import fetch_all_quotes  # noqa: E402


def load_codes_akshare() -> list:
    """akshare 全 A 列表（~5400 只，含沪深京）。"""
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        if df is None or df.empty or "code" not in df.columns:
            print("✘ akshare 列表为空", file=sys.stderr)
            return []
        codes = df["code"].astype(str).str.zfill(6).tolist()
        print(f"  akshare 全 A: {len(codes)} 只")
        return codes
    except Exception as e:
        print(f"✘ akshare 获取失败: {e}", file=sys.stderr)
        return []


def load_codes_from_backtest() -> list:
    """从 backtest.db 读历史成分股代码（588 只）。"""
    db = PROJECT_ROOT / "data" / "backtest.db"
    if not db.exists():
        print(f"✘ 缺少 {db}，请先运行回测脚本生成", file=sys.stderr)
        return []
    conn = sqlite3.connect(db)
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT code FROM constituents ORDER BY code").fetchall()]
    conn.close()
    return codes


def _existing_codes(cache: ScreenerCache) -> set:
    """screener.db 已有股票代码。"""
    with cache._conn() as conn:
        rows = conn.execute("SELECT code FROM stock_list").fetchall()
    return {r[0] for r in rows}


def _import_finance_from_backtest(cache) -> int:
    """从 backtest.db 批量导入最新 ROE 到 finance_snapshot。"""
    from src.screener_cache import FinanceSnapshot
    db = PROJECT_ROOT / "data" / "backtest.db"
    conn = sqlite3.connect(db)
    rows = conn.execute("""
        SELECT code, roe, report_date FROM roe r
        WHERE report_date = (SELECT MAX(report_date) FROM roe r2 WHERE r2.code = r.code)
        ORDER BY code
    """).fetchall()
    conn.close()
    n = 0
    for code, roe, period in rows:
        if roe is None:
            continue
        cache.upsert_finance(FinanceSnapshot(
            code=code, roe_latest=float(roe), roe_period=period,
            net_profit_annual=None, payout_ratio=None, finance_source="backtest.db",
        ))
        n += 1
    return n


def main():
    parser = argparse.ArgumentParser(description="选股器初始化/补全数据库")
    parser.add_argument("--source", choices=["all-a", "backtest"], default="all-a",
                        help="股票列表来源（all-a=akshare全市场 / backtest=成分股）")
    parser.add_argument("--limit", type=int, default=0, help="调试：只处理前 N 只")
    parser.add_argument("--with-finance", action="store_true",
                        help="从 backtest.db 批量导入 ROE 到 finance_snapshot")
    args = parser.parse_args()

    if args.source == "all-a":
        codes = load_codes_akshare()
    else:
        codes = load_codes_from_backtest()
    if not codes:
        print("✘ 无股票代码", file=sys.stderr)
        return 1
    if args.limit:
        codes = codes[:args.limit]

    cache = ScreenerCache()
    existing = _existing_codes(cache)

    # 1. 新增股票列表（增量：只加缺失的）
    new_codes = [c for c in codes if c not in existing]
    if new_codes:
        items = [
            StockListItem(code=c, name="", market=_market_of(c))
            for c in new_codes
        ]
        cache.upsert_stock_list(items)
        print(f"✓ stock_list 新增: {len(items)} 只（已有 {len(existing)}）")
    else:
        print(f"✓ stock_list 无新增（已有 {len(existing)} 只）")

    # 2. 腾讯批量行情 → quote_snapshot（只拉新增的，或全量刷新）
    fetch_codes = new_codes if new_codes else codes
    quotes = fetch_all_quotes(fetch_codes, cache=cache)
    print(f"✓ quote_snapshot 更新: {len(quotes)} 只（腾讯批量）")

    # 3. 从 backtest.db 批量导入 ROE（可选）
    if args.with_finance:
        n_fin = _import_finance_from_backtest(cache)
        print(f"✓ finance_snapshot: {n_fin} 只（backtest.db 导入 ROE）")

    # 4. 统计
    with cache._conn() as conn:
        n_list = conn.execute("SELECT COUNT(*) FROM stock_list").fetchone()[0]
        n_quote = conn.execute("SELECT COUNT(*) FROM quote_snapshot").fetchone()[0]
        n_fin = conn.execute("SELECT COUNT(*) FROM finance_snapshot").fetchone()[0]
    print(f"\n补全完成:")
    print(f"  stock_list: {n_list} 只")
    print(f"  quote_snapshot: {n_quote} 只")
    print(f"  finance_snapshot: {n_fin} 只")
    print(f"  数据库: {cache.db_path}")
    return 0


def _market_of(code: str) -> str:
    """推断市场前缀（sh/sz/bj）。"""
    if code.startswith("6"):
        return "sh"
    if code.startswith(("8", "4", "92")):
        return "bj"
    return "sz"


if __name__ == "__main__":
    sys.exit(main())
