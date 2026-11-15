#!/usr/bin/env python3
"""上传前守卫：seed 库的股息行数低于门槛就拒绝上传。

2026-11 事故：每日任务把只剩行情的库覆盖到 seed，次日漏斗② 180 只 → 0 只，
且亏空随备份滚动无法回退。守卫原先内联在 screener_daily.yml / screener_monthly.yml
两份 workflow 里（重复且无法测试），2026-11-15 review 抽出为脚本。

用法:
    python scripts/check_seed_guard.py                # 默认 data/screener.db，门槛 1000
    python scripts/check_seed_guard.py --db x.db --min 500

退出码：0 通过；1 行数不足（::error:: 供 Actions 标注）；2 库文件不存在。
只用 sqlite3 标准库，不 import src（无需安装依赖即可运行）。
"""
import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "screener.db"
MIN_DIVIDEND_ROWS = 1000


def count_dividend_rows(db_path: Path) -> int:
    """读股息快照行数；库或表缺失 → 0（视同空库，由调用方判定拒绝）。"""
    if not db_path.exists():
        return 0
    try:
        with sqlite3.connect(db_path) as conn:
            return conn.execute("SELECT count(*) FROM dividend_snapshot").fetchone()[0]
    except sqlite3.Error:
        return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="seed 上传守卫")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="目标库路径")
    parser.add_argument("--min", type=int, default=MIN_DIVIDEND_ROWS,
                        help=f"股息行数下限（默认 {MIN_DIVIDEND_ROWS}）")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    rows = count_dividend_rows(db_path)
    print(f"dividend_snapshot 行数: {rows}（门槛 {args.min}）", flush=True)
    if not db_path.exists():
        print(f"::error::库文件不存在: {db_path}", flush=True)
        return 2
    if rows < args.min:
        print(f"::error::dividend_snapshot 只有 {rows} 行（<{args.min}），"
              f"拒绝上传，避免污染 seed", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
