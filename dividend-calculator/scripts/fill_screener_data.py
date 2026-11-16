#!/usr/bin/env python3
"""全 A 选股器数据补全（spec #67）。

逐股拉取全 A 的股息数据（dividend_snapshot），带进度汇报。
增量复用：已有且未过期的数据跳过（缓存复用 + 限流控制）。

单只取数约 4.9s（限流 0.8s + 东财/mootdx），全 A 一轮 ~7.5 小时，超出单次 CI 上限，
因此 CI 按 `--shard i/n` 分片并行：每片只处理自己那一段代码，跑完用 `--export-rows`
导出本片快照，再由 scripts/merge_shard_rows.py 合并回主库。

用法:
    python scripts/fill_screener_data.py --dividend     # 拉股息（默认）
    python scripts/fill_screener_data.py --finance      # 拉财务/PR（默认含）
    python scripts/fill_screener_data.py --limit 1000   # 调试：只拉前 1000
    python scripts/fill_screener_data.py --dividend --shard 3/8 --export-rows shard.json
    python scripts/fill_screener_data.py --dividend --max-runtime-minutes 95
"""
import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.screener_cache import ScreenerCache  # noqa: E402

BATCH_SIZE = 50


def _report(done: int, total: int, t0: float, tag: str):
    """进度汇报：每 50 只打印。"""
    if done % 50 == 0 or done == total:
        elapsed = time.time() - t0
        rate = done / elapsed if elapsed > 0 else 0
        eta = (total - done) / rate / 60 if rate > 0 else 0
        print(f"  [{tag}] {done}/{total} 只 ({done/total*100:.0f}%), "
              f"耗时 {elapsed/60:.1f} 分, 预计剩余 {eta:.0f} 分", flush=True)


def shard_codes(codes: List[str], index: int, total: int) -> List[str]:
    """把排序后的代码切成 total 段，返回第 index 段（1-based）。

    连续性保证：各片互不重叠且并集等于全量（i/n 切片，余数落在靠前的片）。
    """
    if total < 1 or not 1 <= index <= total:
        raise ValueError(f"非法分片 {index}/{total}")
    ordered = sorted(codes)
    n = len(ordered)
    start = (index - 1) * n // total
    end = index * n // total
    return ordered[start:end]


def export_rows(cache: ScreenerCache, codes: List[str], path: str, section: str):
    """把 codes 的股息/财务快照导出为 JSON（供 merge_shard_rows 合并回主库）。

    同一路径可被调用两次（先 dividend 后 finance），按 section 合并写回。
    只导出实际存在的行——被跳过/失败的行不出现，合并时主库原值保留。
    """
    p = Path(path)
    data = {}
    if p.exists():
        data = json.loads(p.read_text(encoding="utf-8"))
    if section == "dividend":
        data["dividend"] = [
            asdict(s) for s in (cache.get_dividend(c) for c in codes) if s is not None
        ]
    else:
        data["finance"] = [
            asdict(s) for s in (cache.get_finance(c) for c in codes) if s is not None
        ]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"导出 {section} {len(data.get(section, []))} 行 → {path}", flush=True)


def _fill(cache: ScreenerCache, codes: List[str], tag: str, fn, deadline: float):
    """按批执行逐股取数，打印进度；超过 deadline 提前收尾（已抓到的已落库）。

    返回 (done, truncated)；truncated=True 表示被时长上限截断，本轮并非完整结果
    （供 main 用非零退出码区分「跑完」与「跑了一部分」，否则 CI 无从判断）。
    """
    total = len(codes)
    t0 = time.time()
    done = 0
    truncated = False
    for i in range(0, total, BATCH_SIZE):
        if deadline and time.time() > deadline:
            print(f"  [{tag}] 达到本轮时长上限，已完成 {done}/{total} 只（余下留给下一轮）", flush=True)
            truncated = True
            break
        batch = codes[i:i + BATCH_SIZE]
        fn(batch, cache)
        done += len(batch)
        _report(done, total, t0, tag)
    print(f"✓ {tag}拉取完成: {done}/{total} 只, 耗时 {(time.time()-t0)/60:.1f} 分钟"
          + ("（截断）" if truncated else ""), flush=True)
    return done, truncated


def fill_dividends(cache: ScreenerCache, codes: List[str], deadline: float = 0.0):
    """逐股拉股息（带进度汇报）。"""
    from src.screener_dividend import compute_dividends_for_candidates
    print(f"拉取股息: {len(codes)} 只（实测 ~4.9s/只）", flush=True)
    return _fill(cache, codes, "股息", compute_dividends_for_candidates, deadline)[1]


def fill_finance(cache: ScreenerCache, codes: List[str], deadline: float = 0.0):
    """逐股拉财务/PR（带进度汇报）。只对有股息数据的代码（漏斗② 之前补全）。"""
    from src.screener_finance import compute_finance_for_candidates
    div_codes = set(cache.get_dividend_codes(real_yield_min=0.0))
    codes = [c for c in codes if c in div_codes]
    print(f"拉取财务: {len(codes)} 只（实测 ~4s/只）", flush=True)
    truncated = _fill(cache, codes, "财务", compute_finance_for_candidates, deadline)[1]
    # 取数全部返回 None 时不会有异常、也不会写行——静默冻结（2026-11：CI 上
    # 同花顺/akshare 取数失效，690 只跑完后 finance_snapshot 一行未更新）。这里显式告警。
    # 「新鲜」由缓存层定义（fresh_codes），脚本不再自己比较 updated_at 字符串。
    fresh = len(cache.fresh_codes("finance_snapshot", codes, max_age_days=0))
    if codes and fresh == 0:
        print(f"::warning::财务取数 {len(codes)} 只无一写入新行（数据源不可用？finance_snapshot 未更新）",
              flush=True)
    return truncated


def main(argv=None):
    """argv=None 时读 sys.argv（单测可注入）。"""
    parser = argparse.ArgumentParser(description="全 A 选股器数据补全")
    parser.add_argument("--dividend", action="store_true", help="拉股息（默认）")
    parser.add_argument("--finance", action="store_true", help="拉财务/PR")
    parser.add_argument("--limit", type=int, default=0, help="调试：只处理前 N 只")
    parser.add_argument("--shard", default="", help="分片：i/n（1-based，如 3/8）")
    parser.add_argument("--export-rows", default="",
                        help="把本片股息/财务快照导出 JSON（供 merge_shard_rows 合并）")
    parser.add_argument("--max-runtime-minutes", type=float, default=0.0,
                        help="本轮时长上限（分钟）；到点收尾并导出，避免被 CI 杀在半路")
    args = parser.parse_args(argv)

    cache = ScreenerCache()
    codes = cache.get_stock_codes()
    if args.shard:
        try:
            index, total = (int(x) for x in args.shard.split("/"))
        except ValueError:
            parser.error("--shard 需要 i/n 形式，如 3/8")
        codes = shard_codes(codes, index, total)
        print(f"分片 {index}/{total}: {len(codes)} 只", flush=True)
    if args.limit:
        codes = codes[:args.limit]

    deadline = (time.time() + args.max_runtime_minutes * 60) if args.max_runtime_minutes else 0.0
    section = "finance" if args.finance else "dividend"
    # 导出放在 finally：财务步因数据源整体失效抛错时，同一分片已刷好的股息数据
    # 仍要交给 merge（否则该片股息刷新白跑，且没有任何提示）。
    try:
        if args.finance:
            truncated = fill_finance(cache, codes, deadline)
        else:
            truncated = fill_dividends(cache, codes, deadline)
    finally:
        if args.export_rows:
            export_rows(cache, codes, args.export_rows, section)

    # 全量刷新成功后才清理孤儿行（股票池已剔除的快照）；分片/调试/被截断的轮次不碰
    # （截断时股票池可能不完整或本轮的剔除尚未生效，清理留到下一轮完整运行）。
    if not args.shard and not args.limit and not truncated:
        pruned = cache.prune_orphan_rows()
        if pruned:
            print(f"清理 {pruned} 条孤儿快照行（股票池已剔除）", flush=True)

    if truncated:
        # 退出码 2 = 部分完成（已落库的部分有效，下一轮从缺口续跑）。
        # 与 0（完整）和 1（异常）区分开，CI 才能标注「本轮被截断」而不是静默成功。
        print(f"::warning::{section} 刷新被时长上限截断，本轮部分完成（余下留待下一轮）",
              flush=True)
        return 2
    return 0


if __name__ == "__main__":
    code = main()
    # 显式硬退出：mootdx/tdxpy 心跳线程会拖住进程（见 src/screener.py 同款说明）
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
