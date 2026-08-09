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

from src.screener_cache import (
    DividendSnapshot,
    FinanceSnapshot,
    QuoteSnapshot,
    ScreenerCache,
)
from src.screener_dividend import screen_real_yield
from src.screener_pr import evaluate_pr_batch, screen_pr
from src.screener_sustainability import (
    evaluate_sustainability_batch,
    screen_sustainability,
)


def _load_stock_list(cache: ScreenerCache) -> List[str]:
    """全 A 股票列表（代码）。

    优先读 screener.db 的 stock_list 表（已初始化），mootdx 仅作后备。
    """
    # 1. 优先：screener.db stock_list（初始化脚本已写入）
    with cache._conn() as conn:
        rows = conn.execute("SELECT code FROM stock_list ORDER BY code").fetchall()
    if rows:
        return [r[0] for r in rows]
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
) -> List[dict]:
    """四级漏斗主流程。返回通过全部层的股票 dict 列表。

    各层数据源可被测试注入（通过模块级替换），此处为生产编排。
    """
    codes = _load_stock_list(cache)
    if limit:
        codes = codes[:limit]

    # 批量读缓存（性能优化：一次读全表，替代逐股查询）
    all_quotes = cache.get_all_quotes()
    all_dividends = cache.get_all_dividends()

    # 漏斗① 行情快照（腾讯批量；refresh_quotes=False 时读缓存）
    from src.screener_quotes import build_candidate_pool, fetch_all_quotes
    if refresh_quotes:
        quotes = fetch_all_quotes(codes, cache=cache)
    else:
        quotes = [all_quotes[c] for c in codes if c in all_quotes]
    base_pool = build_candidate_pool(quotes)
    print(f"漏斗① 行情可用: {len(base_pool)} 只", file=sys.stderr)

    # 漏斗② 真实股息率（仅候选池，从批量缓存读；结合当日市值实时重算）
    base_codes = [q.code for q in base_pool]
    div_snaps = [all_dividends[c] for c in base_codes if c in all_dividends]
    market_caps = {q.code: q.market_cap for q in base_pool if q.market_cap}
    real_pool = screen_real_yield(div_snaps, market_caps=market_caps, min_real=min_real, min_ttm=min_ttm)
    print(f"漏斗② 真实股息率>{min_real}%: {len(real_pool)} 只", file=sys.stderr)

    # 漏斗③ PR 估值（纯缓存，仅候选池；性能优化：不调网络）
    pr_eval = evaluate_pr_batch(real_pool_codes(real_pool), cache, pr_zone=pr_zone)
    # 附加 dividend / industry / total_shares / dividend_total 供漏斗④
    by_div = {s.code: s for s in div_snaps}
    for ev in pr_eval:
        ev["dividend"] = by_div.get(ev["code"])
        quote = all_quotes.get(ev["code"])
        if quote is not None:
            ev["total_shares"] = quote.total_shares
        # dividend_total = 真实股息率(%) × 市值 / 100（可持续性评估核心输入）
        div = by_div.get(ev["code"])
        if div is not None and div.real_yield is not None:
            ev["dividend_total"] = div.real_yield / 100.0 * (quote.market_cap if quote and quote.market_cap else 0)
    pr_pool = screen_pr(pr_eval)
    print(f"漏斗③ PR {pr_zone}: {len(pr_pool)} 只", file=sys.stderr)

    # 漏斗④ 可持续性（仅候选池）
    sus_eval = evaluate_sustainability_batch(pr_pool, cache, sus_verdict=sus_verdict)
    final = screen_sustainability(sus_eval)
    print(f"漏斗④ 可持续性 {sus_verdict}: {len(final)} 只", file=sys.stderr)

    return final


def real_pool_codes(real_pool: List) -> List[str]:
    """从漏斗② 结果取代码列表。"""
    return [s.code for s in real_pool]


def _build_output_rows(cache: ScreenerCache, final: List[dict]) -> List[dict]:
    """汇总最终结果行（代码/名称/三指标/估值/可持续性/行业/辅助字段）。

    复用漏斗③ 已算的 ev（pr/valuation_zone）+ 批量读缓存补辅助字段；
    行业从 sustainability_snapshot 取（纯缓存路径无行业，用预拉数据补）。
    """
    all_quotes = cache.get_all_quotes()
    all_dividends = cache.get_all_dividends()
    all_finance = cache.get_all_finance()
    all_sus = cache.get_all_sustainability()
    rows = []
    for ev in final:
        code = ev["code"]
        quote = all_quotes.get(code)
        dividend = all_dividends.get(code)
        finance = all_finance.get(code)
        sus = all_sus.get(code)
        industry = ev.get("industry") or (sus.industry if sus else "") or ""
        # 实时股息率 = 分红总额 / 当日市值（每日随市值变化，非月频旧值）
        from src.screener_dividend import compute_real_yield
        market_cap = quote.market_cap if quote else None
        real_yield_now = compute_real_yield(dividend.total_dividend if dividend else None, market_cap)
        ttm_yield_now = compute_real_yield(dividend.ttm_dividend if dividend else None, market_cap)
        rows.append({
            "代码": code,
            "名称": quote.name if quote else "",
            "TTM股息率%": round(ttm_yield_now, 2) if ttm_yield_now else "",
            "真实股息率%": round(real_yield_now, 2) if real_yield_now else "",
            "估值区间": ev.get("valuation_zone", ""),
            "市赚率PR": ev.get("pr", ""),
            "行业": industry,
            "可持续性": ev.get("verdict", ""),
            "ROE%": finance.roe_latest if finance else "",
            "总市值(亿)": round(market_cap / 1e8, 2) if market_cap else "",
            "数据来源": (dividend.dividend_source if dividend else "") + " / " + (quote.source if quote else "腾讯"),
        })
    # 按真实股息率降序
    rows.sort(key=lambda r: r["真实股息率%"] if isinstance(r["真实股息率%"], float) else -1, reverse=True)
    return rows


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
            Path(output).write_text("代码,名称,TTM股息率%,真实股息率%,估值区间,市赚率PR,可持续性,ROE%,总市值(亿),数据来源\n", encoding="utf-8")
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
    final = run_screener(
        cache,
        min_ttm=args.min_ttm_yield,
        min_real=args.min_real_yield,
        pr_zone=tuple(args.pr_zone),
        sus_verdict=tuple(args.sus_verdict),
        refresh_quotes=args.refresh in ("quotes", "all"),
        limit=args.limit,
    )
    rows = _build_output_rows(cache, final)
    write_csv(rows, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
