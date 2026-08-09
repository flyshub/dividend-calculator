#!/usr/bin/env python3
"""预拉取可持续性评估基础数据（spec #67，提速优化）。

对高股息候选（通过漏斗②）批量预拉 6 类数据到 sustainability_snapshot：
financial_rows / cashflow_rows / dividend_rows / industry / price_change_1y / top10_holding。
评估时命中缓存则零网络（秒级），未命中按需补拉。

用法:
    python scripts/prefetch_sustainability.py               # 预拉 172 只高股息候选
    python scripts/prefetch_sustainability.py --limit 20    # 调试
    python scripts/prefetch_sustainability.py --all-a       # 拉全部有股息数据的
"""
import argparse
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.eastmoney_fetcher import (  # noqa: E402
    fetch_cashflow_rows,
    fetch_dividend_rows,
    fetch_financial_rows,
    fetch_industry,
    fetch_price_change_1y,
    fetch_top10_holding,
)
from src.screener_cache import ScreenerCache, SustainabilitySnapshot  # noqa: E402
from src.screener_rate_limit import batch_wait  # noqa: E402


def load_candidates(cache: ScreenerCache, all_a: bool = False) -> list:
    """待预拉的候选代码。默认高股息候选（real>5 且 ttm>5），all_a 则全部。"""
    with cache._conn() as conn:
        if all_a:
            codes = [r[0] for r in conn.execute(
                "SELECT code FROM dividend_snapshot WHERE real_yield IS NOT NULL ORDER BY code").fetchall()]
        else:
            codes = [r[0] for r in conn.execute(
                "SELECT code FROM dividend_snapshot WHERE real_yield>5 AND ttm_yield>5 ORDER BY code").fetchall()]
    return codes


def prefetch_one(code: str) -> SustainabilitySnapshot:
    """预拉单只 6 类数据。

    数据完整性检查：financial/cashflow 为空数组视为拉取失败（正常公司必有财报），
    不写缓存（S2 修复：避免空数据投毒导致假阴性 verdict）。
    """
    batch_wait()  # 限流
    financial = fetch_financial_rows(code)
    cashflow = fetch_cashflow_rows(code)
    dividend = fetch_dividend_rows(code)
    industry = fetch_industry(code)
    price_change = fetch_price_change_1y(code)
    top10 = fetch_top10_holding(code)
    # S2：财务/现金流同时为空 → 拉取失败（正常公司必有财报），标记不缓存
    if (financial is not None and len(financial) == 0
            and cashflow is not None and len(cashflow) == 0):
        return SustainabilitySnapshot(code=code, source="东财预拉(失败)")
    return SustainabilitySnapshot(
        code=code,
        financial_rows=json.dumps(financial, ensure_ascii=False) if financial is not None else None,
        cashflow_rows=json.dumps(cashflow, ensure_ascii=False) if cashflow is not None else None,
        dividend_rows=json.dumps(dividend, ensure_ascii=False) if dividend is not None else None,
        industry=industry,
        price_change_1y=price_change,
        top10_holding=top10,
        source="东财预拉",
    )


def main():
    parser = argparse.ArgumentParser(description="预拉可持续性评估基础数据")
    parser.add_argument("--limit", type=int, default=0, help="调试：只处理前 N 只")
    parser.add_argument("--all-a", action="store_true", help="拉全部有股息数据的（默认仅高股息候选）")
    args = parser.parse_args()

    cache = ScreenerCache()
    codes = load_candidates(cache, all_a=args.all_a)
    if args.limit:
        codes = codes[:args.limit]
    total = len(codes)
    print(f"预拉可持续性基础数据: {total} 只（限流）", flush=True)

    t0 = time.time()
    for i, code in enumerate(codes, 1):
        snap = prefetch_one(code)
        if snap.source != "东财预拉(失败)":
            cache.upsert_sustainability(snap)
        if i % 10 == 0 or i == total:
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / rate / 60 if rate > 0 else 0
            print(f"  [{i}/{total}] 耗时 {elapsed/60:.1f} 分, 预计剩余 {eta:.0f} 分", flush=True)
    print(f"✓ 预拉完成: {total} 只, 耗时 {(time.time()-t0)/60:.1f} 分钟", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
