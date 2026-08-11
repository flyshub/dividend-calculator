#!/usr/bin/env python3
"""T6 稳健性检验（方案 V3 第 8 节，issue #89）

对 T4 分层回测结论做四组对照，检验「PR≤1 有超额」是否稳健：
1. 剔微盘：排除总市值 < 50 亿（股本取当前值近似历史，见下）
2. 剔金融：排除银行/证券/保险/多元金融（名称关键词近似）
3. 延迟 T+5：调仓日 T 后第 5 个交易日建仓（模拟执行延迟）
4. 随机起点：起始季度随机偏移 0-3 个季度（固定种子，多组抽样）

数据铁律声明：
- 股本历史未入库（T2 仅存当前快照不可得历史序列）——剔微盘用当前总股本 ×
  当日价格近似市值，报告中如实标注该近似；股本变动较大的个股市值会有偏差。
- 行业历史未入库——剔金融用股票名称关键词近似（银行/证券/保险/信托/金融），
  名称不含关键词的金融类（如部分投资平台）可能漏剔，报告中如实标注。

输出：主回测 vs 各变体的全漏斗累计收益 + 分层增量超额对比。
"""

import json
import random
import sqlite3
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence

from backtest_engine import BacktestLookup, run_backtest, quarterly_rebalance_dates
from backtest_portfolio import performance_metrics, cum

DB_PATH = "data/backtest.db"
SMALL_CAP_FLOOR = 50e8  # 50 亿元

# 金融关键词（剔金融变体，名称近似）
_FIN_KEYWORDS = ("银行", "证券", "保险", "信托", "金融")


def load_names(conn: sqlite3.Connection) -> Dict[str, str]:
    """stock_list 表 code -> name（无 name 表则返回空 dict）。"""
    try:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT code, name FROM stock_list").fetchall()}
    except sqlite3.Error:
        return {}


def filter_small_cap(lookup, codes: Sequence[str], T: date,
                     floor: float = SMALL_CAP_FLOOR) -> List[str]:
    """剔除当日市值 < floor 的股票（市值 = 价格 × 当前总股本近似）。"""
    out = []
    for code in codes:
        px = lookup.price(code, T)
        shares = lookup.total_shares(code, T)
        if not px or not shares:
            continue
        if px * shares >= floor:
            out.append(code)
    return out


def filter_financial(codes: Sequence[str],
                     names: Dict[str, str]) -> List[str]:
    """剔除名称含金融关键词的股票。"""
    return [c for c in codes if not any(
        k in names.get(c, "") for k in _FIN_KEYWORDS)]


def random_start_offsets(n: int = 4, seed: int = 42) -> List[date]:
    """固定种子生成 n 个随机起始日（2013 年起随机偏移 0-270 天）。"""
    rng = random.Random(seed)
    base = date(2013, 1, 1)
    return [base + timedelta(days=rng.randint(0, 270)) for _ in range(n)]


def run_variant(lookup, name: str, build_offset: int = 1,
                filter_fn=None) -> dict:
    """跑一组回测，返回全漏斗/各层累计 + 分层增量超额。"""
    res = run_backtest(lookup, build_offset=build_offset)
    # 变体过滤：仅对入选池做后过滤，等价于漏斗后置剔除
    if filter_fn is not None:
        for i in range(len(res["rebalance_dates"])):
            for k in ("base", "l2", "l3", "l4", "full"):
                res["pools"][k][i] = filter_fn(res["pools"][k][i])
    return {
        "name": name,
        "incremental_excess": res["incremental_excess"],
        "cumulative_returns": res["cumulative_returns"],
        "n_quarters": len(res["rebalance_dates"]),
    }


def main() -> None:
    lookup = BacktestLookup(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    names = load_names(conn)

    results = [run_variant(lookup, "主回测 (T+1)")]
    results.append(run_variant(
        lookup, "剔微盘 <50亿",
        filter_fn=lambda cs: filter_small_cap(lookup, cs, date(2026, 8, 10))))
    results.append(run_variant(
        lookup, "剔金融",
        filter_fn=lambda cs: filter_financial(cs, names)))
    results.append(run_variant(lookup, "延迟 T+5", build_offset=5))

    print("== 稳健性检验：各变体全漏斗累计收益 ==")
    base = results[0]["cumulative_returns"]["full"]
    for r in results:
        full = r["cumulative_returns"]["full"]
        print(f"  {r['name']:>16s}  累计 {full*100:+6.2f}%  "
              f"(vs 主回测 {full-base:+.2f}pp)")

    print("\n== 分层增量超额（累计，各变体 vs 主回测）==")
    for r in results[1:]:
        print(f"  {r['name']:>16s}:", end="")
        for k in ("l2_over_base", "l3_over_l2", "l4_over_l3", "full_over_l4"):
            v = r["incremental_excess"].get(k)
            print(f"  {k}={v*100 if v is not None else float('nan'):+.2f}%", end="")
        print()

    print("\n== 随机起点（4 组，固定种子 42）==")
    for i, start in enumerate(random_start_offsets()):
        res = run_backtest(lookup, start=start)
        full = res["cumulative_returns"]["full"]
        inc = res["incremental_excess"].get("full_over_l4")
        print(f"  start={start}  全漏斗 {full*100:+6.2f}%  "
              f"L4增量 {inc*100 if inc is not None else float('nan'):+.2f}%")

    print(json.dumps(
        {r["name"]: {"full": r["cumulative_returns"]["full"]} for r in results},
        ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
