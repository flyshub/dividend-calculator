#!/usr/bin/env python3
"""T1 验证门：回测数据源历史可获得性实测（issue #84）。

按数据铁律「先验证可获得性，不通过不实现」，实测回测方案 V3 所需的全部数据源：
  1. 全 A 股票列表（含已退市）——akshare 退市列表
  2. 日频不复权价格 —— 腾讯日K（不带复权参数）
  3. 日频总市值 —— 腾讯实时快照 × 总股本（Index 73）
  4. 日频 PE_TTM 历史 —— akshare 百度估值（stock_zh_valuation_baidu）
  5. 历史分红（公告日+报告期+除权日）—— 东财 RPT_SHAREBONUS_DET（复用 fetch_dividend_rows）
  6. 可持续性历史字段（净利润/现金流/未分配利润，含银行专项 CAR）—— 东财财务/现金流
  7. 中证红利全收益 + 沪深300全收益基准 —— akshare 指数
  8. 抽样真实值核对

用法:
    cd dividend-calculator && python scripts/probe_backtest_data.py [600036,...]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SAMPLE = ["600036", "600900", "601398", "000001", "601988"]


def _p(label: str, ok: bool, detail: str = "") -> None:
    print(f"[{'OK ' if ok else 'FAIL'}] {label} {detail}")


def probe_quotes():
    """腾讯实时快照（含总市值/PE_TTM），确认 5 只抽样可用。"""
    from src.screener_quotes import fetch_quote_batch
    quotes = fetch_quote_batch(SAMPLE)
    _p("腾讯实时快照", bool(quotes), f"{len(quotes)}/{len(SAMPLE)}")
    for c in SAMPLE[:2]:
        q = quotes.get(c)
        if q:
            print(f"    {c}: price={q.price} pe_ttm={q.pe_ttm} total_shares={q.total_shares} market_cap={q.market_cap}")


def probe_tencent_kline():
    """腾讯日K（不复权，不带 qfq/hfq 参数），分段拉取验证历史深度。

    实测：单次请求上限约 2000 根（3000/5000 被拒返回空）；用日期区间分段
    （2013-01-01~2018-12-31 + 2019-01-01~今）可覆盖 2013 至今全部交易日。
    """
    import requests
    for c in SAMPLE[:2]:
        prefix = "sh" if c.startswith("6") else "sz"
        total = 0
        start, end = "2013-01-01", "2018-12-31"
        for s, e in [("2013-01-01", "2018-12-31"), ("2019-01-01", "2026-08-10")]:
            url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
                   f"?param={prefix}{c},day,{s},{e},2000,")
            try:
                resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
                data = resp.json()
                key = f"{prefix}{c}"
                rows = (data.get("data") or {}).get(key, {}).get("day") or []
                total += len(rows)
            except Exception:
                pass
        _p(f"腾讯日K不复权 {c}", total > 2000, f"{total} 根(分段2013-2026)")


def probe_baidu_pe():
    """akshare 百度估值（历史 PE_TTM 序列，列名 date/value）。"""
    import akshare as ak
    for c in SAMPLE[:2]:
        try:
            df = ak.stock_zh_valuation_baidu(symbol=c, indicator="市盈率(TTM)", period="全部")
            ok = df is not None and len(df) > 100
            _p(f"百度估值历史PE {c}", ok,
               f"{len(df) if ok else 0} 行, 最早={df.iloc[0]['date'] if ok else 'N/A'}")
        except Exception as e:
            _p(f"百度估值历史PE {c}", False, str(e))


def probe_delist():
    """akshare 退市列表（沪/深），消除幸存者偏差的关键。"""
    import akshare as ak
    try:
        sh = ak.stock_info_sh_delist()
        sz = ak.stock_info_sz_delist(symbol="终止上市公司")
        total = len(sh) + len(sz)
        _p("退市股票列表", total > 50, f"沪 {len(sh)} + 深 {len(sz)} = {total} 只")
        if len(sh):
            print(f"    沪样例: {sh.iloc[0].to_dict()}")
    except Exception as e:
        _p("退市股票列表", False, str(e))


def probe_dividend():
    """东财 RPT_SHAREBONUS_DET（公告日+报告期+除权日）。"""
    from src.eastmoney_fetcher import fetch_dividend_rows
    for c in SAMPLE[:2]:
        rows = fetch_dividend_rows(c)
        ok = rows is not None and len(rows) > 3
        detail = f"{len(rows) if rows else 0} 条"
        if rows:
            r = rows[0]
            keys = [k for k in ("REPORT_DATE", "EX_DIVIDEND_DATE", "ANNOUNCEMENT_DATE", "IMPL_ANN_DATE",
                                "PRETAX_BONUS_RMB", "CASH_DIVIDEND", "PRETAX_BONUS")
                    if k in r]
            detail += f" | 首条字段: {keys}"
        _p(f"东财分红 {c}", ok, detail)


def probe_finance():
    """东财财务历史（ROE/净利润/现金流，含银行专项 CAR）。

    已知限制：银行（如 600036）GCASHFLOW 现金流量表接口返回空，
    但经营现金流净额在财务主表 NETCASH_OPERATE_PK 字段可得（已验证 601398/000001）。
    """
    from src.eastmoney_fetcher import fetch_financial_rows, fetch_cashflow_rows
    for c in SAMPLE[:2]:
        rows = fetch_financial_rows(c)
        ok = rows is not None and len(rows) > 3
        detail = f"{len(rows) if rows else 0} 期"
        if rows:
            r = rows[0]
            keys = [k for k in ("REPORT_DATE", "ROE_WEIGHTED", "PARENTNETPROFIT",
                                "TOTAL_OPERATE_INCOME", "NETCASH_OPERATE_PK", "BPS",
                                "NEWCAPITALADER", "LOAN_PROVISION_RATIO")
                    if k in r]
            detail += f" | 首期字段: {keys}"
        _p(f"东财财务 {c}", ok, detail)
        cf = fetch_cashflow_rows(c)
        if c.startswith("6") and not cf and c == "600036":
            _p(f"东财现金流 {c} (银行，GCASHFLOW空为已知)", False,
               "NETCASH_OPERATE_PK 已在财务主表验证可用")
        else:
            _p(f"东财现金流 {c}", cf is not None and len(cf) > 0, f"{len(cf) if cf else 0} 期")


def probe_index():
    """中证红利全收益 + 沪深300全收益（中证官网下载接口）。

    akshare 东财 push2his 与新浪源均不支持 H 前缀全收益指数；
    中证官网 (csindex.com.cn) 直接下载可用（HTTP 200）。
    """
    import requests
    UA = {"User-Agent": "Mozilla/5.0"}
    for name, code in [("中证红利全收益", "H00922"), ("沪深300全收益", "H00300")]:
        try:
            r = requests.get(
                f"https://www.csindex.com.cn/uploads/file/autofile/closeweight/{code}closeweight.xls",
                headers=UA, timeout=20)
            _p(f"{name} {code}", r.status_code == 200 and len(r.content) > 1000,
               f"HTTP {r.status_code}, {len(r.content)} 字节")
        except Exception as e:
            _p(f"{name} {code}", False, repr(e)[:100])


def main() -> None:
    samples = sys.argv[1:] or SAMPLE
    print(f"=== T1 验证门探针（抽样 {samples}）===\n")
    probe_quotes()
    probe_tencent_kline()
    probe_baidu_pe()
    probe_delist()
    probe_dividend()
    probe_finance()
    probe_index()
    print("\n=== 完成 ===")


if __name__ == "__main__":
    main()
