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
from datetime import date
from typing import Dict, List, Optional, Sequence

from backtest_engine import BacktestLookup, run_backtest, quarterly_rebalance_dates
from backtest_portfolio import performance_metrics, cum, run_portfolio
from backtest_significance import block_bootstrap_ci

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
                     names: Dict[str, str],
                     industries: Optional[Dict[str, str]] = None) -> List[str]:
    """剔除金融股：优先用 industry 表真实分类，回退到名称近似。

    industry 表（东财 F10）以"金融-..."开头 = 金融行业（银行/证券/保险/信托/其他非银）。
    缺失时回退到名称包含 _FIN_KEYWORDS 的近似判定。
    """
    out = []
    for c in codes:
        ind = (industries or {}).get(c, "")
        if ind.startswith("金融"):
            continue
        if not ind and any(k in names.get(c, "") for k in _FIN_KEYWORDS):
            continue
        out.append(c)
    return out


def random_start_offsets(n: int = 4, seed: int = 42) -> List[date]:
    """固定种子生成 n 个随机起始季（2013Q1 起偏移 0-3 季度）。

    spec：起始季平移——天数偏移不改变 (y,m) 季度末过滤后的调仓序列，
    必须按季度偏移才能真正改变起点。固定种子保证可复现。
    """
    rng = random.Random(seed)
    base = date(2013, 1, 1)
    out: List[date] = []
    for _ in range(n):
        q_offset = rng.randint(0, 3)  # 0-3 季度偏移
        year = base.year + (base.month - 1 + q_offset * 3) // 12
        month = (base.month - 1 + q_offset * 3) % 12 + 1
        out.append(date(year, month, 1))
    return out


def run_variant(lookup, name: str, build_offset: int = 1,
                filter_fn=None) -> dict:
    """跑一组回测，返回全漏斗/各层累计 + 分层增量超额。

    filter_fn 通过 run_backtest 的 filter_fn 参数接入收益计算链路——
    在每季度 portfolio_return 之前逐期过滤候选池（市值/行业过滤必须用当季价格，
    避免未来函数）。若不接入 run_backtest 而仅后置过滤 pools，则过滤是 no-op
    （收益已按未过滤池算完，过滤不影响结果）。
    """
    res = run_backtest(lookup, build_offset=build_offset, filter_fn=filter_fn)
    # T9 #134：full 层含分红（portfolio_total_return）口径——headline 稳健性
    pf = run_portfolio(lookup, res)
    pf_full = pf["quarterly_returns"]["full"]
    return {
        "name": name,
        "incremental_excess": res["incremental_excess"],
        "cumulative_returns": res["cumulative_returns"],
        "quarterly_returns": res["quarterly_returns"],
        "n_quarters": len(res["rebalance_dates"]),
        "pf_full_cum": cum(pf_full),
        "pf_full_quarterly": pf_full,
    }


def main() -> None:
    lookup = BacktestLookup(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    names = load_names(conn)
    # T11 #135：与报告 4.1 入口统一——加载 industry 表（剔金融用真实分类）
    try:
        industries = dict(conn.execute("SELECT code, industry FROM industry").fetchall())
    except sqlite3.OperationalError:
        industries = {}

    results = [run_variant(lookup, "主回测 (T+1)")]
    results.append(run_variant(
        lookup, "剔微盘 <50亿",
        filter_fn=lambda cs, T: filter_small_cap(lookup, cs, T)))
    results.append(run_variant(
        lookup, "剔金融",
        filter_fn=lambda cs, T: filter_financial(cs, names, industries)))
    results.append(run_variant(lookup, "延迟 T+5", build_offset=5))

    print("== 稳健性检验：各变体全漏斗累计收益 ==")
    base = results[0]["cumulative_returns"]["full"]
    for r in results:
        full = r["cumulative_returns"]["full"]
        pf_full = r.get("pf_full_cum")
        pf_str = f"  含分红 {pf_full*100:+6.2f}%" if pf_full is not None else ""
        print(f"  {r['name']:>16s}  纯价格 {full*100:+6.2f}%  "
              f"(vs 主回测 {full-base:+.2f}pp){pf_str}")

    print("\n== 分层增量超额（累计，各变体 vs 主回测）==")
    for r in results[1:]:
        print(f"  {r['name']:>16s}:", end="")
        for k in ("l2_over_base", "l3_over_l2", "l4_over_l3"):
            v = r["incremental_excess"].get(k)
            print(f"  {k}={v*100 if v is not None else float('nan'):+.2f}%", end="")
        print()

    print("\n== 随机起点（4 组，固定种子 42）==")
    for i, start in enumerate(random_start_offsets()):
        res = run_backtest(lookup, start=start)
        full = res["cumulative_returns"]["full"]
        inc = res["incremental_excess"].get("full_over_base")
        print(f"  start={start}  全漏斗 {full*100:+6.2f}%  "
              f"vs base {inc*100 if inc is not None else float('nan'):+.2f}%")

    # T9 #134：半年调仓最优（14.19%）仅 25 期样本 + 事后扫描——补 bootstrap 95% CI
    print("\n== 半年调仓 bootstrap 95% CI（block，T9 #134）==")
    half_res = run_backtest(lookup, freq="semiannual")
    half_pf = run_portfolio(lookup, half_res)
    half_q = [r for r in half_pf["quarterly_returns"]["full"] if r is not None]
    ci = block_bootstrap_ci(half_q)
    lo, hi = ci
    if lo is not None and hi is not None:
        mean_r = sum(half_q) / len(half_q) if half_q else 0.0
        print(f"  半年调仓逐期收益 mean={mean_r*100:+.2f}%  "
              f"95% CI [{lo*100:+.2f}%, {hi*100:+.2f}%]  n={len(half_q)}")
        if lo > 0:
            print("  CI 下界 > 0：正收益结论在 block bootstrap 下成立")
        else:
            print("  CI 含 0：正收益结论未达统计显著，只能作为提示而非结论")
    else:
        print("  样本不足，无法计算 CI")

    print(json.dumps(
        {r["name"]: {"full": r["cumulative_returns"]["full"]} for r in results},
        ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
