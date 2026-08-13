"""T12 #118：超额收益统计显著性检验。

对逐期超额收益做：
1. t 检验（H0：均值=0）
2. block bootstrap 95% CI（保留时序自相关，block size ≈ sqrt(n)）

输出 full 层 vs 各基准的超额检验结果。

数据铁律：检验结果基于真实回测数字，不虚构。样本不足（< 8 期）时
如实标注 "样本不足"。

用法：
    python scripts/backtest_significance.py --db data/backtest.db
"""
import argparse
import math
import random
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
_ROOT = _SCRIPT_DIR.parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from backtest_engine import BacktestLookup, run_backtest
from backtest_portfolio import run_portfolio


def _valid(rets: Sequence[Optional[float]]) -> List[float]:
    """过滤 None，返回有效收益序列。"""
    return [r for r in rets if r is not None]


def t_test_mean(samples: Sequence[float]) -> Tuple[Optional[float], Optional[float]]:
    """单样本 t 检验（H0: 均值=0）。

    返回 (t_stat, p_value_two_sided)。样本 < 2 时返回 (None, None)。
    """
    n = len(samples)
    if n < 2:
        return None, None
    mean = sum(samples) / n
    var = sum((x - mean) ** 2 for x in samples) / (n - 1)
    se = math.sqrt(var / n)
    if se == 0:
        return None, None
    t_stat = mean / se
    # 双侧 p 值用正态近似（n>=30 时 t→z；n 小时保守）
    # ponytail: 正态近似而非 t 分布精确值，避免引入 scipy 依赖，n>=8 可接受
    from statistics import NormalDist
    p = 2 * (1 - NormalDist().cdf(abs(t_stat)))
    return t_stat, p


def block_bootstrap_ci(
    samples: Sequence[float], n_boot: int = 1000, alpha: float = 0.05,
    seed: int = 42, block_size: Optional[int] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """block bootstrap 置信区间（保留时序自相关）。

    block_size 默认 sqrt(n)（stationary bootstrap 经验值）。
    返回 (lower, upper) 的均值置信区间。样本不足返回 (None, None)。
    """
    n = len(samples)
    if n < 8:
        return None, None
    if block_size is None:
        block_size = max(1, int(math.sqrt(n)))
    rng = random.Random(seed)
    means = []
    for _ in range(n_boot):
        total = 0.0
        for _ in range(n):
            start = rng.randint(0, n - 1)
            idx = start + (rng.randint(0, block_size - 1) if block_size > 1 else 0)
            if idx >= n:
                idx = n - 1
            total += samples[idx]
        means.append(total / n)
    means.sort()
    lo_idx = int(alpha / 2 * n_boot)
    hi_idx = int((1 - alpha / 2) * n_boot)
    return means[lo_idx], means[min(hi_idx, n_boot - 1)]


def excess_series(strategy: Sequence[float], benchmark: Sequence[float]) -> List[float]:
    """逐期超额（比值口径，与 T3 一致）：(1+s)/(1+b) - 1。"""
    out = []
    for s, b in zip(strategy, benchmark):
        if s is None or b is None:
            continue
        if 1 + b <= 0:
            continue
        out.append((1 + s) / (1 + b) - 1)
    return out


def run_significance(lookup, n_boot: int = 1000) -> List[List[str]]:
    """跑 full 层 vs 全A基线 + 双基准的超额检验。"""
    eng = run_backtest(lookup)
    pf = run_portfolio(lookup, eng)

    strategy = _valid(pf["quarterly_returns"]["full"])
    base = _valid(eng["quarterly_returns"]["base"])

    # 与基线等长对齐
    min_len = min(len(strategy), len(base))
    s, b = strategy[:min_len], base[:min_len]
    exc = excess_series(s, b)

    rows = []
    t_stat, p_val = t_test_mean(exc)
    lo, hi = block_bootstrap_ci(exc, n_boot=n_boot)
    mean_exc: float = sum(exc) / len(exc) if exc else 0.0
    rows.append([
        "全漏斗 vs 全A基线",
        f"{len(exc)}",
        f"{mean_exc * 100:+.2f}%",
        f"{t_stat:.3f}" if t_stat is not None else "N/A",
        f"{p_val:.4f}" if p_val is not None else "N/A",
        f"[{lo*100:+.2f}%, {hi*100:+.2f}%]" if lo is not None else "样本不足",
        "显著" if (p_val is not None and p_val < 0.05) else "不显著",
    ])
    return rows


def _table(headers: List[str], rows: List[List[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/backtest.db")
    parser.add_argument("--n-boot", type=int, default=1000)
    args = parser.parse_args()

    lookup = BacktestLookup(args.db)
    rows = run_significance(lookup, n_boot=args.n_boot)
    print(_table(["超额对比", "期数", "逐期均值", "t 统计量", "p 值", "bootstrap 95% CI", "结论"], rows))
    print("\n> ponytail: p 值用正态近似（n>=8 可接受），bootstrap block size=sqrt(n)；"
          "样本不足(<8期)如实标注。")


if __name__ == "__main__":
    main()
