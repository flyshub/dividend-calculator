#!/usr/bin/env python3
"""选股器初始化数据库（spec #67）。

从 backtest.db 导入历史成分股（588 只，沪深300+中证500 历史成分）到 screener.db，
并用腾讯批量行情填充 quote_snapshot。

步骤：
1. 从 backtest.db 读成分股代码
2. 写 screener.db stock_list（低频）
3. 腾讯批量行情（800/批）→ quote_snapshot
4. 输出统计

用法:
    python scripts/init_screener.py                 # 初始化
    python scripts/init_screener.py --source backtest  # 从 backtest.db（默认）
"""
import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.screener_cache import QuoteSnapshot, ScreenerCache, StockListItem  # noqa: E402
from src.screener_quotes import fetch_all_quotes  # noqa: E402


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


def _import_finance_from_backtest(cache) -> int:
    """从 backtest.db 批量导入最新 ROE 到 finance_snapshot。"""
    from src.screener_cache import FinanceSnapshot
    db = PROJECT_ROOT / "data" / "backtest.db"
    conn = sqlite3.connect(db)
    # 每只股票最新年报 ROE
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
            code=code,
            roe_latest=float(roe),
            roe_period=period,
            net_profit_annual=None,
            payout_ratio=None,
            finance_source="backtest.db",
        ))
        n += 1
    return n


def main():
    parser = argparse.ArgumentParser(description="选股器初始化数据库")
    parser.add_argument("--source", choices=["backtest"], default="backtest",
                        help="成分股来源（当前仅 backtest）")
    parser.add_argument("--limit", type=int, default=0, help="调试：只初始化前 N 只")
    parser.add_argument("--with-finance", action="store_true",
                        help="从 backtest.db 批量导入 ROE 到 finance_snapshot（避免逐股拉财务）")
    args = parser.parse_args()

    codes = load_codes_from_backtest()
    if not codes:
        print("✘ 无成分股代码", file=sys.stderr)
        return 1
    if args.limit:
        codes = codes[:args.limit]
    print(f"成分股代码: {len(codes)} 只")

    cache = ScreenerCache()

    # 1. stock_list（marker 推断市场前缀）
    items = [
        StockListItem(code=c, name="", market=("sh" if c.startswith("6") else "sz"))
        for c in codes
    ]
    cache.upsert_stock_list(items)
    print(f"✓ stock_list: {len(items)} 只")

    # 2. 腾讯批量行情 → quote_snapshot
    quotes = fetch_all_quotes(codes, cache=cache)
    print(f"✓ quote_snapshot: {len(quotes)} 只（腾讯批量）")

    # 3. 从 backtest.db 批量导入 ROE → finance_snapshot（可选加速）
    if args.with_finance:
        n_fin = _import_finance_from_backtest(cache)
        print(f"✓ finance_snapshot: {n_fin} 只（backtest.db 导入 ROE）")

    # 4. 统计
    with cache._conn() as conn:
        n_list = conn.execute("SELECT COUNT(*) FROM stock_list").fetchone()[0]
        n_quote = conn.execute("SELECT COUNT(*) FROM quote_snapshot").fetchone()[0]
    print(f"\n初始化完成:")
    print(f"  stock_list: {n_list} 只")
    print(f"  quote_snapshot: {n_quote} 只")
    print(f"  数据库: {cache.db_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
