#!/usr/bin/env python3
"""预拉取可持续性评估基础数据（spec #67，提速优化）。

对高股息候选（通过漏斗②）批量预拉 6 类数据到 sustainability_snapshot：
financial_rows / cashflow_rows / dividend_rows / industry / price_change_1y / top10_holding。
评估时命中缓存则零网络（秒级），未命中按需补拉。

预取 + 限流 + 写缓存已收进 src.sustainability.prefetch_and_cache（#95）：
本脚本只负责候选清单与 CLI，不接触快照 JSON 列。

用法:
    python scripts/prefetch_sustainability.py               # 预拉 172 只高股息候选
    python scripts/prefetch_sustainability.py --limit 20    # 调试
    python scripts/prefetch_sustainability.py --all-a       # 拉全部有股息数据的
"""
import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.screener_cache import ScreenerCache  # noqa: E402
from src.sustainability import prefetch_and_cache  # noqa: E402


def load_candidates(cache: ScreenerCache, all_a: bool = False) -> list:
    """待预拉的候选代码。默认高股息候选（real>5 且 ttm>5），all_a 则全部。"""
    if all_a:
        codes = cache.get_dividend_codes(require_real_yield=True)
    else:
        codes = cache.get_dividend_codes(real_yield_min=5.0, ttm_yield_min=5.0)
    return codes


def prefetch_one(code: str, cache: ScreenerCache):
    """预拉单只 6 类数据并写缓存。

    内部走 sustainability.prefetch_and_cache（限流 + S2 完整性检查：financial/
    cashflow 同时为空视为拉取失败，标记 source=东财预拉(失败) 不写缓存，返回 None）。
    """
    return prefetch_and_cache(cache, code)


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
        prefetch_one(code, cache)
        if i % 10 == 0 or i == total:
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / rate / 60 if rate > 0 else 0
            print(f"  [{i}/{total}] 耗时 {elapsed/60:.1f} 分, 预计剩余 {eta:.0f} 分", flush=True)
    print(f"✓ 预拉完成: {total} 只, 耗时 {(time.time()-t0)/60:.1f} 分钟", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
