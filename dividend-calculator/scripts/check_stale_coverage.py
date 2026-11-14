#!/usr/bin/env python3
"""统计快照过期覆盖，决定 CI 是否需要跑一轮刷新。

分片刷新是幂等的：刷新过的行变成「新鲜」，下一轮自动跳过。因此 6 小时一次的
定时任务只需要在确实存在过期行时才启动 8 个分片 job，否则提前退出（~2 分钟）。

退出码恒为 0；用 --github-output 写 need_refresh=1|0 供 workflow 判断。
阈值：股息或财务的过期行 > max(200, 总数 5%) 才算需要刷新（少量新上市/退市代码
不触发全量）。
"""
import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.screener_cache import ScreenerCache  # noqa: E402

# 触发刷新的最小过期行数（低于此值视为正常波动）
MIN_STALE = 200
STALE_RATIO = 0.05


def main():
    parser = argparse.ArgumentParser(description="检查快照过期覆盖")
    parser.add_argument("--max-age-days", type=int, default=30)
    parser.add_argument("--github-output", action="store_true",
                        help="把 need_refresh 写入 $GITHUB_OUTPUT")
    args = parser.parse_args()

    cache = ScreenerCache()
    counts = cache.stale_counts(max_age_days=args.max_age_days)
    need = 0
    for table, (total, stale) in counts.items():
        threshold = max(MIN_STALE, int(total * STALE_RATIO))
        flag = "需要刷新" if stale > threshold else "OK"
        print(f"{table}: 过期 {stale}/{total}（阈值 {threshold}）→ {flag}", flush=True)
        if table in ("dividend_snapshot", "finance_snapshot") and stale > threshold:
            need = 1
    print(f"need_refresh={need}", flush=True)

    if args.github_output:
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"need_refresh={need}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
