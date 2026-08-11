"""选股器 CLI（spec #67，工单 #74）。

`python -m src.screener` —— 四级漏斗选全 A 高股息低估值可持续股，输出 CSV。

漏斗：全 A → ①TTM>5% → ②真实>5% → ③PR合理偏低/低估 → ④可持续/偏弱
数据：缓存快照 + 增量刷新（data/screener.db）。

用法示例：
    python -m src.screener                          # 默认阈值，输出到 stdout
    python -m src.screener --output out.csv         # 指定输出文件
    python -m src.screener --refresh quotes         # 只刷新行情快照
    python -m src.screener --min-ttm-yield 4        # 自定义 TTM 阈值
    python -m src.screener --limit 100              # 调试：只扫前 100 只
"""
import argparse
import csv
import sys
from pathlib import Path
from typing import List

from src.screening import (
    FIELDS,
    FunnelCandidate,
    FunnelConfig,
    FunnelResult,
    build_output_rows,
    run_funnel,
)
from src.screener_cache import ScreenerCache


def _load_stock_list(cache: ScreenerCache) -> List[str]:
    """全 A 股票列表（代码）。

    优先读 screener.db 的 stock_list 表（已初始化），mootdx 仅作后备。
    """
    # 1. 优先：screener.db stock_list（初始化脚本已写入）
    codes = cache.get_stock_codes()
    if codes:
        return codes
    # 2. 后备：mootdx 全列表
    try:
        from src.utils import get_stock_list_cache
        df = get_stock_list_cache()
        if df is None or df.empty:
            return []
        if "code" in df.columns:
            return df["code"].astype(str).str[-6:].tolist()
        if "symbol" in df.columns:
            return df["symbol"].astype(str).str[-6:].tolist()
        return []
    except Exception as e:
        print(f"获取股票列表失败: {e}", file=sys.stderr)
        return []


def run_screener(
    cache: ScreenerCache,
    *,
    min_ttm: float = 5.0,
    min_real: float = 5.0,
    pr_zone: List[str] = ("合理偏低", "低估"),
    sus_verdict: List[str] = ("可持续", "偏弱"),
    refresh_quotes: bool = True,
    limit: int = 0,
) -> FunnelResult:
    """四级漏斗主流程（编排 + 数据获取），判定语义集中在 src.screening.run_funnel。

    数据获取：股票列表 → 行情快照（腾讯批量）→ 缓存股息/财务快照；
    判定、降级回退、输出整形由选股漏斗 module 完成（ADR-0001）。
    """
    codes = _load_stock_list(cache)
    if limit:
        codes = codes[:limit]

    # 批量读缓存（一次读全表，替代逐股查询）
    all_quotes = cache.get_all_quotes()
    all_dividends = cache.get_all_dividends()
    all_finance = cache.get_all_finance()

    # 行情快照（腾讯批量；refresh_quotes=False 时读缓存）
    from src.screener_quotes import fetch_all_quotes
    if refresh_quotes:
        quotes = fetch_all_quotes(codes, cache=cache)
    else:
        quotes = [all_quotes[c] for c in codes if c in all_quotes]

    universe = [
        FunnelCandidate(code=q.code, quote=q,
                        dividend=all_dividends.get(q.code),
                        finance=all_finance.get(q.code))
        for q in quotes
    ]

    # 漏斗④ 可持续性评估（数据获取层注入：限流 + 缓存复用）
    from src.screener_sustainability import make_sustainability_evaluator
    sus_evaluator = make_sustainability_evaluator(cache)

    result = run_funnel(
        universe,
        FunnelConfig(min_ttm=min_ttm, min_real=min_real,
                     pr_zone=tuple(pr_zone), sus_verdict=tuple(sus_verdict)),
        evaluate_sustainability=sus_evaluator,
    )

    n1, n2, n3, n4 = result.stage_counts
    print(f"漏斗① 行情可用: {n1} 只", file=sys.stderr)
    print(f"漏斗② 真实股息率>{min_real}%: {n2} 只", file=sys.stderr)
    print(f"漏斗③ PR {pr_zone}: {n3} 只", file=sys.stderr)
    print(f"漏斗④ 可持续性 {sus_verdict}: {n4} 只", file=sys.stderr)
    if result.fallback_count:
        print(f"漏斗② 降级: {result.fallback_count} 只缺 total/ttm_dividend 用存储旧值，"
              f"其中 {result.fallback_passed} 只入选", file=sys.stderr)
    return result


# 默认 CSV 导出目录（data/screener/，自动创建）
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "screener"


def write_csv(rows: List[dict], output: str):
    """写 CSV。

    output 为空 → 自动导出到 data/screener/screener_<时间戳>.csv（新建目录）。
    output 为路径 → 写入指定路径。
    output 为 '-' → stdout。
    """
    # 自动导出：data/screener/ 目录 + 时间戳文件名
    if output == "":
        import datetime
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        DEFAULT_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output = str(DEFAULT_OUTPUT_DIR / f"screener_{ts}.csv")

    if not rows:
        print("无符合条件的股票", file=sys.stderr)
        if output != "-":
            # 空结果也写完整表头（11 列，含「行业」，与 FIELDS 契约一致，
            # 否则 export_screener_json.py 的表头校验会拦截）
            Path(output).write_text(",".join(FIELDS) + "\n", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    if output != "-":
        with open(output, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(rows)
        print(f"已输出 {len(rows)} 只到 {output}", file=sys.stderr)
    else:
        w = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="全 A 股息率+市赚率+可持续性选股器")
    parser.add_argument("--output", default="", help="CSV 输出路径（默认 data/screener/screener_<时间戳>.csv；'-'=stdout）")
    parser.add_argument("--min-ttm-yield", type=float, default=5.0, help="TTM 股息率阈值（默认 5）")
    parser.add_argument("--min-real-yield", type=float, default=5.0, help="真实股息率阈值（默认 5）")
    parser.add_argument("--pr-zone", nargs="+", default=["合理偏低", "低估"],
                        help="保留的估值区间（默认 合理偏低 低估）")
    parser.add_argument("--sus-verdict", nargs="+", default=["可持续", "偏弱"],
                        help="保留的可持续性结论（默认 可持续 偏弱）")
    parser.add_argument("--refresh", choices=["quotes", "dividend", "all", "none"],
                        default="quotes", help="增量刷新范围（默认仅行情）")
    parser.add_argument("--limit", type=int, default=0, help="调试：只扫前 N 只")
    args = parser.parse_args()

    cache = ScreenerCache()
    result = run_screener(
        cache,
        min_ttm=args.min_ttm_yield,
        min_real=args.min_real_yield,
        pr_zone=tuple(args.pr_zone),
        sus_verdict=tuple(args.sus_verdict),
        refresh_quotes=args.refresh in ("quotes", "all"),
        limit=args.limit,
    )
    rows = build_output_rows(result)
    write_csv(rows, args.output)

    # 数据保留策略：清理超 90 天未刷新的快照行（幂等，无 stale 行则 0 删除）
    pruned = cache.prune_stale_rows()
    if pruned:
        print(f"  清理 {pruned} 条超期快照行（保留 {90} 天）", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
