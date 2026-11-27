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
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.screener_cache import ScreenerCache  # noqa: E402
from src.sustainability import prefetch_and_cache  # noqa: E402

# 最近一次选股结果（仓库内，非 Pages）——漏斗候选的来源
DEFAULT_FUNNEL_JSON = PROJECT_ROOT / "site" / "screener" / "latest.json"


def load_funnel_codes(path: Path = DEFAULT_FUNNEL_JSON) -> list:
    """最近一次漏斗候选代码；文件缺失/损坏 → 空列表（不阻断预拉）。"""
    try:
        rows = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    return [str(r["代码"]) for r in rows if isinstance(r, dict) and r.get("代码")]


def load_candidates(cache: ScreenerCache, all_a: bool = False,
                    funnel_json: Path = DEFAULT_FUNNEL_JSON) -> list:
    """待预拉的候选代码。

    默认 = 库内高股息（real>5 且 ttm>5）∪ 最近一次漏斗候选：
    漏斗② 用实时行情，股价上涨新进漏斗的股票在库内旧值可能不到阈值
    （#160 的 600211 即如此），只按库内值筛会永远漏掉它们，每日只能按需补拉。
    文件缺失时退化为纯阈值集合。all_a 则全部有股息数据的。
    """
    if all_a:
        codes = cache.get_dividend_codes(require_real_yield=True)
    else:
        codes = set(cache.get_dividend_codes(real_yield_min=5.0, ttm_yield_min=5.0))
        codes |= set(load_funnel_codes(funnel_json))
        codes = sorted(codes)
    return codes


def prefetch_one(code: str, cache: ScreenerCache):
    """预拉单只 6 类数据并写缓存。

    内部走 sustainability.prefetch_and_cache（限流 + S2 完整性检查：financial/
    cashflow 同时为空视为拉取失败，不写缓存，返回 None）。
    """
    return prefetch_and_cache(cache, code)


def main():
    parser = argparse.ArgumentParser(description="预拉可持续性评估基础数据")
    parser.add_argument("--limit", type=int, default=0, help="调试：只处理前 N 只")
    parser.add_argument("--all-a", action="store_true", help="拉全部有股息数据的（默认仅高股息候选）")
    parser.add_argument("--funnel-json", default=str(DEFAULT_FUNNEL_JSON),
                        help="最近一次选股结果 JSON（并入候选）")
    args = parser.parse_args()

    cache = ScreenerCache()
    codes = load_candidates(cache, all_a=args.all_a, funnel_json=Path(args.funnel_json))
    if args.limit:
        codes = codes[:args.limit]
    total = len(codes)
    print(f"预拉可持续性基础数据: {total} 只（限流）", flush=True)

    t0 = time.time()
    failures = 0
    for i, code in enumerate(codes, 1):
        try:
            prefetch_one(code, cache)
        except Exception as exc:  # noqa: BLE001 — 单只失败不影响整批
            failures += 1
            print(f"  [{i}/{total}] {code} 预拉失败: {exc}", flush=True)
        if i % 10 == 0 or i == total:
            elapsed = time.time() - t0
            rate = i / elapsed if elapsed > 0 else 0
            eta = (total - i) / rate / 60 if rate > 0 else 0
            print(f"  [{i}/{total}] 耗时 {elapsed/60:.1f} 分, 预计剩余 {eta:.0f} 分", flush=True)
    print(f"✓ 预拉完成: {total} 只（失败 {failures}）, 耗时 {(time.time()-t0)/60:.1f} 分钟", flush=True)
    return 0


if __name__ == "__main__":
    from src.cli_exit import hard_exit

    hard_exit(main())
