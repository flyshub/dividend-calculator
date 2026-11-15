#!/usr/bin/env python3
"""把分片刷新的快照合并回主库（screener.db）。

CI 分片刷新流程：
    各片并行跑 fill_screener_data.py --shard i/n --export-rows shard.json
    → 本脚本把各片 JSON 按 code 覆盖写入主库

只覆盖 JSON 中出现的行；某片失败/未处理的代码，主库原值保留（宁旧不缺）。
各片代码区间互不重叠，若出现重复 code（同片重跑导出），保留 updated_at 最新的一行。
"""
import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.screener_cache import (  # noqa: E402
    MIN_UNIVERSE_FOR_PRUNE,
    ScreenerCache,
    DividendSnapshot,
    FinanceSnapshot,
)


def _load(paths):
    """读入全部 JSON，按 code 去重并保留 updated_at 最新的一行。"""
    div, fin = {}, {}
    for p in paths:
        data = json.loads(Path(p).read_text(encoding="utf-8"))
        for row in data.get("dividend", []):
            code = row["code"]
            if code not in div or (row.get("updated_at") or "") >= (div[code].get("updated_at") or ""):
                div[code] = row
        for row in data.get("finance", []):
            code = row["code"]
            if code not in fin or (row.get("updated_at") or "") >= (fin[code].get("updated_at") or ""):
                fin[code] = row
    return div, fin


def main():
    parser = argparse.ArgumentParser(description="合并分片快照到 screener.db")
    parser.add_argument("files", nargs="+", help="分片导出的 JSON 文件")
    parser.add_argument("--db", default="", help="目标库路径（默认 data/screener.db）")
    parser.add_argument("--skip-prune", action="store_true",
                        help="跳过孤儿行清理（调试/需要保留旧快照时使用）")
    parser.add_argument("--min-universe", type=int, default=MIN_UNIVERSE_FOR_PRUNE,
                        help=f"清理要求的最小股票池规模（默认 {MIN_UNIVERSE_FOR_PRUNE}）")
    args = parser.parse_args()

    div, fin = _load(args.files)
    cache = ScreenerCache(Path(args.db) if args.db else None)
    for row in div.values():
        cache.upsert_dividend(DividendSnapshot(**row))
    for row in fin.values():
        cache.upsert_finance(FinanceSnapshot(**row))
    print(f"合并完成: dividend {len(div)} 行, finance {len(fin)} 行（来源 {len(args.files)} 个分片）",
          flush=True)

    # 孤儿行清理放在这里：CI 的分片刷新永远不满足「整轮成功」条件（--shard 模式跳过
    # prune），清理只能挂在合并之后——此时股票池是完整的，且刚刷完一轮。
    # 只按「代码是否还在 stock_list」裁剪，stock_list 为空时不动（防误判全市场退市）。
    if not args.skip_prune:
        pruned = cache.prune_orphan_rows(min_universe=args.min_universe)
        print(f"清理孤儿行: {pruned} 条（stock_list 已剔除的代码）", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
