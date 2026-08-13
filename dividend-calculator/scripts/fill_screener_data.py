#!/usr/bin/env python3
"""全 A 选股器数据补全（spec #67）。

逐股拉取全 A 的股息数据（dividend_snapshot），带进度汇报。
增量复用：已有且未过期的数据跳过（缓存复用 + 限流控制）。

用法:
    python scripts/fill_screener_data.py --dividend     # 拉股息（默认）
    python scripts/fill_screener_data.py --finance      # 拉财务/PR（默认含）
    python scripts/fill_screener_data.py --limit 1000   # 调试：只拉前 1000
"""
import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.screener_cache import ScreenerCache  # noqa: E402


def _report(done: int, total: int, t0: float, tag: str):
    """进度汇报：每 50 只打印。"""
    if done % 50 == 0 or done == total:
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / rate / 60 if rate > 0 else 0
        print(f"  [{tag}] {done}/{total} 只 ({done/total*100:.0f}%), "
              f"耗时 {elapsed/60:.1f} 分, 预计剩余 {eta:.0f} 分", flush=True)


def fill_dividends(cache: ScreenerCache, limit: int = 0):
    """全 A 逐股拉股息（带进度汇报）。"""
    from src.screener_dividend import compute_dividends_for_candidates
    codes = cache.get_stock_codes()
    if limit:
        codes = codes[:limit]
    total = len(codes)
    print(f"拉取股息: {total} 只（限流 0.8s/只，预计 ~{total*0.8/60:.0f} 分钟）", flush=True)
    t0 = time.time()
    # 分批处理，每批 50 只（便于进度汇报）
    batch_size = 50
    done = 0
    for i in range(0, total, batch_size):
        batch = codes[i:i + batch_size]
        compute_dividends_for_candidates(batch, cache)
        done += len(batch)
        _report(done, total, t0, "股息")
    print(f"✓ 股息拉取完成: {done} 只, 耗时 {(time.time()-t0)/60:.1f} 分钟", flush=True)


def fill_finance(cache: ScreenerCache, limit: int = 0):
    """全 A 逐股拉财务/PR（带进度汇报）。"""
    from src.screener_finance import compute_finance_for_candidates
    # 只对股息>5% 的候选（漏斗② 之后）评估 PR——但此处补全财务，拉全有股息率的
    codes = cache.get_dividend_codes(real_yield_min=0.0)
    if limit:
        codes = codes[:limit]
    total = len(codes)
    print(f"拉取财务: {total} 只（限流 0.8s/只，预计 ~{total*0.8/60:.0f} 分钟）", flush=True)
    t0 = time.time()
    batch_size = 50
    done = 0
    for i in range(0, total, batch_size):
        batch = codes[i:i + batch_size]
        compute_finance_for_candidates(batch, cache)
        done += len(batch)
        _report(done, total, t0, "财务")
    print(f"✓ 财务拉取完成: {done} 只, 耗时 {(time.time()-t0)/60:.1f} 分钟", flush=True)


def main():
    parser = argparse.ArgumentParser(description="全 A 选股器数据补全")
    parser.add_argument("--dividend", action="store_true", help="拉股息（默认）")
    parser.add_argument("--finance", action="store_true", help="拉财务/PR")
    parser.add_argument("--limit", type=int, default=0, help="调试：只处理前 N 只")
    args = parser.parse_args()

    cache = ScreenerCache()
    if args.finance:
        fill_finance(cache, args.limit)
    else:
        fill_dividends(cache, args.limit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
