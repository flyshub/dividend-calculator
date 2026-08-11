"""T6 参数敏感性扫描：股息率阈值/PR 阈值/调仓频率/持仓/加权 单变扫描。

每维 3 档，其他维度固定为 baseline，输出全漏斗累计/年化/夏普/回撤/超额对比表。
单变扫描（不组合爆炸）：每张表 N=3 行，可读性强、定位敏感维度。

数据铁律：所有数据来自 backtest.db，无虚构；total_shares 缺失时市值加权
退化为价格加权（已在报告与代码标注近似）。

用法：
    python scripts/backtest_sensitivity.py --db data/backtest.db
"""
import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Dict, List

_SCRIPT_DIR = Path(__file__).resolve().parent
_ROOT = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backtest_engine import BacktestLookup, run_backtest
from backtest_portfolio import (annualized, cum, max_drawdown, performance_metrics,
                                run_portfolio, sharpe)


def _row(label: str, rets: List[float]) -> List[str]:
    n = len([r for r in rets if r is not None])
    total = cum(rets)
    return [
        label,
        f"{total*100:+.2f}%",
        f"{annualized(total, n)*100:+.2f}%",
        f"{(sharpe(rets) or 0):.2f}",
        f"{max_drawdown(rets)*100:.2f}%",
        f"{n}",
    ]


def scan_yield_threshold(lookup) -> List[List[str]]:
    """股息率阈值 4%/5%/6% 单变扫描（PR/频率/加权固定 baseline）。"""
    rows = []
    for thr in (4.0, 5.0, 6.0):
        res = run_backtest(lookup, yield_thr=thr, real_yield_thr=thr)
        rows.append(_row(f"股息率>{thr:.0f}%", res["quarterly_returns"]["full"]))
    return rows


def scan_pr_threshold(lookup) -> List[List[str]]:
    """PR 阈值 0.8/1.0/1.2 单变扫描。"""
    rows = []
    for thr in (0.8, 1.0, 1.2):
        res = run_backtest(lookup, pr_thr=thr)
        rows.append(_row(f"PR≤{thr}", res["quarterly_returns"]["full"]))
    return rows


def scan_freq(lookup) -> List[List[str]]:
    """调仓频率 月/季/半年 单变扫描。"""
    rows = []
    for freq, label in (("monthly", "月调仓"), ("quarterly", "季调仓"), ("semiannual", "半年调仓")):
        res = run_backtest(lookup, freq=freq)
        rows.append(_row(label, res["quarterly_returns"]["full"]))
    return rows


def scan_holdings(lookup, engine_result) -> List[List[str]]:
    """持仓 全池/Top20/Top10 单变扫描。"""
    rows = []
    for top_n, label in ((None, "全池"), (20, "Top20"), (10, "Top10")):
        pf = run_portfolio(lookup, engine_result, top_n=top_n)
        rows.append(_row(label, pf["quarterly_returns"]["full"]))
    return rows


def scan_weighting(lookup, engine_result) -> List[List[str]]:
    """加权 等权/市值/股息率 单变扫描。"""
    rows = []
    for w, label in (("equal", "等权"), ("cap", "市值加权"), ("yield", "股息率加权")):
        pf = run_portfolio(lookup, engine_result, weighting=w)
        rows.append(_row(label, pf["quarterly_returns"]["full"]))
    return rows


def _table(headers: List[str], rows: List[List[str]]) -> str:
    out = "| " + " | ".join(headers) + " |\n"
    out += "| " + " | ".join("---" for _ in headers) + " |\n"
    for r in rows:
        out += "| " + " | ".join(r) + " |\n"
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/backtest.db")
    args = parser.parse_args()

    lookup = BacktestLookup(args.db)
    base = run_backtest(lookup)

    print(_table(["股息率阈值", "累计", "年化", "夏普", "回撤", "期数"],
                 scan_yield_threshold(lookup)))
    print(_table(["PR 阈值", "累计", "年化", "夏普", "回撤", "期数"],
                 scan_pr_threshold(lookup)))
    print(_table(["调仓频率", "累计", "年化", "夏普", "回撤", "期数"],
                 scan_freq(lookup)))
    print(_table(["持仓", "累计", "年化", "夏普", "回撤", "期数"],
                 scan_holdings(lookup, base)))
    print(_table(["加权", "累计", "年化", "夏普", "回撤", "期数"],
                 scan_weighting(lookup, base)))


if __name__ == "__main__":
    main()
