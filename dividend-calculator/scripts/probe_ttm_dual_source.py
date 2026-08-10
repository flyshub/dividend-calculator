#!/usr/bin/env python3
"""双源 TTM 分红对比探针（issue #76 验证门）。

对比两条分红链路在「滚动 365 天（按除权除息日）」窗口下的 TTM 分红结果：
- 东财 RPT_SHAREBONUS_DET（fetch_dividend_rows + parse_dividend_rows，报告期口径，
  issue #77 后为走势图/TTM 主源）
- mootdx xdxr（原主源，category==1 现金分红，fenhong 浮点 round(4)，现为兜底）

用法:
    cd dividend-calculator && python scripts/probe_ttm_dual_source.py [600036,600900,...]
"""
import sys

SAMPLE = ["600036", "600900", "601398", "601857", "000333"]


def records_mootdx(stock_code):
    """mootdx xdxr 链路（显式 server，避免默认构造在本沙箱挂起）"""
    from mootdx.quotes import Quotes
    from src.utils import infer_fiscal_year
    from src.datasource.base import DividendRecord

    client = Quotes.factory(market='std', server=('110.41.147.114', 7709), timeout=8)
    df = client.xdxr(symbol=stock_code)
    if df is None or df.empty:
        return []
    df = df[df["category"] == 1]
    results = []
    for _, row in df.iterrows():
        try:
            y, m, d = int(row["year"]), int(row["month"]), int(row["day"])
        except (ValueError, KeyError):
            continue
        fenhong = round(float(row.get("fenhong", 0) or 0), 4)
        if fenhong <= 0:
            continue
        results.append(DividendRecord(
            ex_dividend_date=f"{y:04d}-{m:02d}-{d:02d}",
            dividend_per_10=fenhong,
            report_time=infer_fiscal_year(y, m).report_time,
        ))
    return results


def records_eastmoney(stock_code):
    """复用东财链路（api.py 降级块 / sustainability 同源）"""
    from src.eastmoney_fetcher import fetch_dividend_rows
    from src.sustainability import parse_dividend_rows

    rows = fetch_dividend_rows(stock_code)
    if not rows:
        return []
    records, _ = parse_dividend_rows(rows)
    return records


def ttm_window(records, as_of="2026-08-10"):
    """滚动 365 天窗口（按除权除息日），返回 (总额每10股, 次数, [(ex_date, per_10)])"""
    from datetime import date, timedelta

    as_of = date.fromisoformat(as_of)
    cutoff = as_of - timedelta(days=365)
    hits = []
    for rec in records:
        d = date.fromisoformat(str(rec.ex_dividend_date)[:10])
        if cutoff < d <= as_of:
            hits.append((str(rec.ex_dividend_date), round(float(rec.dividend_per_10), 4)))
    total = round(sum(x[1] for x in hits), 4)
    return total, len(hits), hits


def main() -> int:
    codes = [c.strip() for c in (sys.argv[1].split(",") if len(sys.argv) > 1 else []) if c.strip()]
    if not codes:
        codes = SAMPLE

    print(f"双源 TTM 分红对比（滚动 365 天, 基准 2026-08-10, 每10股）\n")
    mismatches = 0
    for code in codes:
        try:
            moo = records_mootdx(code)
        except Exception as e:
            moo = f"FAIL {type(e).__name__}: {str(e)[:80]}"
        try:
            em = records_eastmoney(code)
        except Exception as e:
            em = f"FAIL {type(e).__name__}: {str(e)[:80]}"

        if isinstance(moo, str) or isinstance(em, str):
            print(f"{code}: mootdx={moo if isinstance(moo, str) else 'OK'} 东财={em if isinstance(em, str) else 'OK'}")
            mismatches += 1
            continue

        moo_total, moo_n, moo_hits = ttm_window(moo)
        em_total, em_n, em_hits = ttm_window(em)

        same = moo_total == em_total and moo_n == em_n and sorted(moo_hits) == sorted(em_hits)
        if not same:
            mismatches += 1
        print(f"{code}: {'一致 ✅' if same else '差异 ⚠️'}")
        print(f"  mootdx: 总额={moo_total} 次数={moo_n}  明细={moo_hits}")
        print(f"  东财:   总额={em_total} 次数={em_n}  明细={em_hits}")
        if moo_hits != em_hits:
            only_moo = set(moo_hits) - set(em_hits)
            only_em = set(em_hits) - set(moo_hits)
            if only_moo:
                print(f"  仅 mootdx 有: {sorted(only_moo)}")
            if only_em:
                print(f"  仅东财有: {sorted(only_em)}")
        print()

    print(f"结论: {5 - mismatches}/{len(codes)} 一致")
    return 0 if mismatches == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
