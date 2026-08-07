#!/usr/bin/env python3
"""对比 JS 实现与 Python 实现的「计算逻辑」一致性。

关键设计：两套实现必须消费【相同原始数据】才能对比逻辑本身。
因此本脚本不调用 Python 的 mootdx/THS 管线（数据源不同），
而是与 JS 走同一批 HTTP 接口（腾讯行情 + 东财分红/财务/行业），
把相同 rows 分别喂给:
  - JS:  node site/js/verify_raw.js <fixture.json>
  - Python: 复用 src/dividend._parse_fhps_detail + src/pr_calculator 纯函数
然后逐字段对比。

用法:
    python scripts/verify_js_vs_python.py 600900 600987 ...
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent

TOLERANCE = 1e-9  # 相同输入 → 结果应完全一致（仅容忍浮点精度）
FIELDS_NUMERIC = [
    "current_price", "total_shares", "pe_ttm", "pb",
    "total_dividend", "dividend_yield_before_tax",
    "dividend_yield_after_tax_10", "dividend_yield_after_tax_20",
    "ttm_dividend", "dividend_yield_ttm_before_tax",
    "pr_basic", "pr_corrected", "pr_pb", "payout_ratio", "n_factor",
    "roe_latest", "roe_5y_median", "net_profit_latest_period", "net_profit_annual",
    "sustainability_triggered", "sustainability_score", "sustainability_score_100",
    # 衍生指标拍平（来自 sustainability.metrics）——防止双端在 FCF/coverage 公式上发散
    # 却恰好落入同一 verdict/score 档、bug 被掩盖
    "sustainability_cf_coverage", "sustainability_fcf_coverage",
    "sustainability_free_cash_flow", "sustainability_debt_ratio",
    "sustainability_interest_coverage", "sustainability_consecutive_years",
    "sustainability_payout_ratio",
]
FIELDS_STR = ["dividend_year", "valuation_zone", "pr_warning", "industry", "is_loss_stock", "explanation",
              "sustainability_verdict", "sustainability_explanation",
              "ttm_period", "ttm_source"]


# ---------------------------------------------------------------------------
# 原始数据获取（与 JS datasources.js 走相同接口）
# ---------------------------------------------------------------------------

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# GitHub Actions runner 位于海外，东财 datacenter 偶发限流超时（CLAUDE.md 已知坑）
# 统一 3 次退避重试 + 30s 读取超时，吸收瞬时网络抖动，避免 CI 假红
import requests.adapters
from urllib3.util.retry import Retry

_HTTP = requests.Session()
_HTTP.headers.update(UA)
_HTTP.mount(
    "https://",
    requests.adapters.HTTPAdapter(
        max_retries=Retry(total=3, connect=3, read=3, backoff_factor=1.0,
                          status_forcelist=[500, 502, 503, 504]),
    ),
)


def _get(url: str, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", (5, 30))
    return _HTTP.get(url, **kwargs)


def fetch_tencent_quote(code: str) -> dict:
    prefix = "sh" if code.startswith("6") else "sz"
    r = _get(f"https://qt.gtimg.cn/q={prefix}{code}")
    r.encoding = "gbk"
    import re
    m = re.search(r'"([^"]+)"', r.text)
    if not m:
        raise RuntimeError(f"腾讯行情解析失败: {code}")
    f = m.group(1).split("~")
    return {
        "stock_code": code,
        "name": f[1] or None,
        "price": _sf(f, 3),
        "pe_ttm": _sf(f, 33),
        "pb": _sf(f, 46),
        "a_shares": _sf(f, 72),
        "total_shares": _sf(f, 73),
    }


def _sf(fields, idx):
    try:
        v = float(fields[idx])
        return v if v > 0 else None
    except (ValueError, IndexError):
        return None


def fetch_dividend_rows(code: str) -> list:
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?sortColumns=REPORT_DATE&sortTypes=-1"
           f"&pageSize=100&pageNumber=1&reportName=RPT_SHAREBONUS_DET&columns=ALL"
           f'&filter=(SECURITY_CODE%3D%22{code}%22)')
    r = _get(url)
    d = r.json()
    return (d.get("result") or {}).get("data") or []


def fetch_financial_rows(code: str) -> list:
    market = ".SH" if code.startswith("6") else ".SZ"
    url = ("https://datacenter.eastmoney.com/api/data/v1/get?sortColumns=REPORT_DATE&sortTypes=-1"
           f"&pageSize=100&pageNumber=1&reportName=RPT_F10_FINANCE_MAINFINADATA&columns=ALL"
           f'&filter=(SECUCODE%3D%22{code}{market}%22)')
    r = _get(url)
    d = r.json()
    return (d.get("result") or {}).get("data") or []


def fetch_industry(code: str) -> str:
    market = ".SH" if code.startswith("6") else ".SZ"
    url = ("https://datacenter-web.eastmoney.com/api/data/v1/get?reportName=RPT_F10_BASIC_ORGINFO"
           f"&columns=ALL&filter=(SECUCODE%3D%22{code}{market}%22)")
    r = _get(url)
    d = r.json()
    rows = (d.get("result") or {}).get("data") or []
    if not rows:
        return "未知行业"
    return rows[0].get("EM2016") or rows[0].get("INDUSTRYCSRC1") or "未知行业"


def fetch_cashflow_rows(code: str) -> list:
    market = ".SH" if code.startswith("6") else ".SZ"
    url = ("https://datacenter.eastmoney.com/api/data/v1/get?sortColumns=REPORT_DATE&sortTypes=-1"
           f"&pageSize=100&pageNumber=1&reportName=RPT_F10_FINANCE_GCASHFLOW&columns=ALL"
           f'&filter=(SECUCODE%3D%22{code}{market}%22)')
    r = _get(url)
    d = r.json()
    return (d.get("result") or {}).get("data") or []


def fetch_raw(code: str) -> dict:
    return {
        "quote": fetch_tencent_quote(code),
        "dividend_rows": fetch_dividend_rows(code),
        "financial_rows": fetch_financial_rows(code),
        "cashflow_rows": fetch_cashflow_rows(code),
        "industry": fetch_industry(code),
    }


# ---------------------------------------------------------------------------
# Python 计算（复用项目纯函数，消费与 JS 相同的原始数据）
# ---------------------------------------------------------------------------

def compute_python(raw: dict) -> dict:
    import pandas as pd
    sys.path.insert(0, str(PROJECT_ROOT))
    from src.dividend import _parse_fhps_detail
    from src.dividend import DividendDetail  # noqa: F401
    from src.datasource.base import StockInfo
    from src.pr_calculator import (
        compute_basic_pr, compute_corrected_pr, compute_n_factor,
        compute_pb_pr, classify_valuation, classify_industry,
    )

    quote = raw["quote"]
    total_shares = quote["total_shares"] or quote["a_shares"] or 0
    stock_info = StockInfo(stock_code=quote["stock_code"], current_price=quote["price"], total_shares=total_shares)

    # 1. 分红：把东财行转成 _parse_fhps_detail 期望的 DataFrame
    df = pd.DataFrame([
        {
            "报告期": r["REPORT_DATE"],
            "现金分红-现金分红比例": r["PRETAX_BONUS_RMB"],
            "方案进度": r["ASSIGN_PROGRESS"],
        }
        for r in raw["dividend_rows"]
    ])
    total_div, year, details, expl = _parse_fhps_detail(df, stock_info)

    # 1b. TTM 口径（#19）：从东财行按除权日算近12个月派发，与 JS computeTtmDividend 同口径
    from src.utils import compute_ttm_dividend
    from src.datasource.base import DividendRecord as _DR
    ttm_records = []
    for r in raw["dividend_rows"]:
        progress = str(r.get("ASSIGN_PROGRESS") or "")
        if "实施" not in progress or "未实施" in progress:
            continue
        dp10 = r.get("PRETAX_BONUS_RMB")
        if dp10 is None or dp10 <= 0:
            continue
        ex = str(r.get("EX_DIVIDEND_DATE") or "")
        if not ex:
            continue
        ttm_records.append(_DR(ex_dividend_date=ex[:10], dividend_per_10=float(dp10), report_time=""))
    ttm_total, ttm_start, ttm_end, ttm_count = compute_ttm_dividend(ttm_records, total_shares)
    ttm_yield = (ttm_total / (quote["price"] * total_shares) * 100) if ttm_total is not None and quote["price"] > 0 else None
    ttm_period = f"{ttm_start}~{ttm_end}" if ttm_start else None

    # 2. 财务：从东财行计算 ROE / 净利润（与 JS parseFinancials 相同算法）
    fin = _parse_financials(raw["financial_rows"])

    # 3. 行业分类（与 JS classifyIndustry 同关键字集）
    _, _, warning = classify_industry(raw["industry"])

    # 4. 市赚率公式（与 JS computePr 相同核心）
    net_annual = fin["net_profit_annual"]
    is_loss = net_annual is not None and net_annual <= 0
    if is_loss:
        warning = warning + "；该股为亏损股，市赚率不适用" if warning else "该股为亏损股，市赚率不适用"

    payout = None
    n_factor = None
    if net_annual is not None and net_annual > 0 and total_div > 0:
        payout = total_div / net_annual
        n_factor = compute_n_factor(payout)

    pr_basic = pr_corrected = pr_pb = None
    zone = "无法判定"
    roe = fin["roe_latest"]
    if not is_loss and quote["pe_ttm"] is not None and roe is not None and roe > 0:
        pr_basic = compute_basic_pr(quote["pe_ttm"], roe)
        pr_corrected = compute_corrected_pr(quote["pe_ttm"], roe, n_factor)
        pr_pb = compute_pb_pr(quote["pb"], roe)
        zone = classify_valuation(pr_corrected if pr_corrected is not None else pr_basic)

    total_market_cap = quote["price"] * total_shares
    yld = (total_div / total_market_cap * 100) if total_market_cap > 0 else 0.0

    # 5. 股息可持续性（与 JS assessSustainability 相同算法，纯函数消费相同 raw）
    # JS 端 computeFromRaw 仅在 yields[0] > 4 时调用 assessSustainability，
    # 故 yield ≤ 阈值时 sustainability 为 None（镜像该门控）。
    from src.sustainability import parse_dividend_rows, assess_for_stock
    sus = None
    sus_triggered = None
    sus_verdict = None
    sus_score = None
    if yld > 4:  # 与 JS app.js 的 `yields[0] > 4` 门控一致
        div_records, em_year = parse_dividend_rows(raw["dividend_rows"])
        sus_year = year if year else em_year
        sus = assess_for_stock(
            stock_code=quote["stock_code"],
            total_shares=total_shares,
            dividend_total=total_div,
            dividend_yield_before_tax=yld,
            latest_dividend_year=sus_year,
            industry=raw["industry"],
            dividend_records=div_records,
            financial_rows=raw["financial_rows"],
            cashflow_rows=raw.get("cashflow_rows"),
        )
        sus_triggered = 1 if sus.triggered else 0
        sus_verdict = sus.verdict
        sus_score = sus.score

    result = {
        "current_price": quote["price"],
        "total_shares": total_shares,
        "pe_ttm": quote["pe_ttm"],
        "pb": quote["pb"],
        "dividend_year": year,
        "total_dividend": total_div,
        "dividend_yield_before_tax": yld,
        "dividend_yield_after_tax_10": yld * 0.9,
        "dividend_yield_after_tax_20": yld * 0.8,
        "explanation": expl,
        "ttm_dividend": ttm_total,
        "dividend_yield_ttm_before_tax": ttm_yield,
        "ttm_period": ttm_period,
        "ttm_source": "东财" if ttm_total is not None else "无",
        "pr_basic": pr_basic,
        "pr_corrected": pr_corrected,
        "pr_pb": pr_pb,
        "valuation_zone": zone,
        "pr_warning": warning,
        "payout_ratio": payout,
        "n_factor": n_factor,
        "roe_latest": fin["roe_latest"],
        "roe_5y_median": fin["roe_5y_median"],
        "net_profit_latest_period": fin["net_profit_latest_period"],
        "net_profit_annual": net_annual,
        "industry": raw["industry"],
        "is_loss_stock": is_loss,
        "sustainability_triggered": sus_triggered,
        "sustainability_verdict": sus_verdict,
        "sustainability_score": sus_score,
        "sustainability_score_100": sus.score_100 if sus else None,
    }
    # 衍生指标拍平（sus 可能为 None，对应字段也为 None，双端一致）
    sus_m = sus.metrics if sus else {}
    result["sustainability_cf_coverage"] = sus_m.get("cf_coverage")
    result["sustainability_fcf_coverage"] = sus_m.get("fcf_coverage")
    result["sustainability_free_cash_flow"] = sus_m.get("free_cash_flow")
    result["sustainability_debt_ratio"] = sus_m.get("debt_ratio")
    result["sustainability_interest_coverage"] = sus_m.get("interest_coverage")
    result["sustainability_consecutive_years"] = sus_m.get("consecutive_dividend_years")
    result["sustainability_payout_ratio"] = sus_m.get("payout_ratio")
    from src.sustainability_calculator import explain_sustainability
    result["sustainability_explanation"] = (
        "\n".join(explain_sustainability(sus)) if sus is not None else None
    )
    return result


def _parse_financials(rows: list) -> dict:
    """从东财财务行计算 ROE/净利润，算法与 JS parseFinancials 完全一致。"""
    annual = [
        {"year": int(r["REPORT_DATE"][:4]), "roe": float(r["ROEJQ"])}
        for r in rows
        if (r.get("REPORT_DATE") or "")[5:10] == "12-31" and _num(r.get("ROEJQ")) is not None
    ]
    annual.sort(key=lambda a: -a["year"])

    roe_latest = annual[0]["roe"] if annual else None
    roe_5y_median = None
    if annual:
        last5 = sorted([a["roe"] for a in annual[:5]])
        roe_5y_median = last5[len(last5) // 2]

    net_profit_annual = None
    if annual:
        for r in rows:
            if (r.get("REPORT_DATE") or "")[:10] == f"{annual[0]['year']}-12-31" and _num(r.get("PARENTNETPROFIT")) is not None:
                net_profit_annual = float(r["PARENTNETPROFIT"])
                break

    net_profit_latest_period = None
    dated = sorted(
        [{"date": (r.get("REPORT_DATE") or "")[:10], "np": float(r["PARENTNETPROFIT"])}
         for r in rows if len((r.get("REPORT_DATE") or "")[:10]) == 10 and _num(r.get("PARENTNETPROFIT")) is not None],
        key=lambda d: d["date"],
    )
    if dated:
        latest = dated[-1]
        latest_year = int(latest["date"][:4])
        md = latest["date"][5:]
        prev_year = prev_same = None
        for d in reversed(dated):
            if d["date"][5:] == "12-31" and d["date"][:4] != str(latest_year):
                prev_year = d["np"]
                break
        if md != "12-31":
            target = f"{latest_year - 1}-{md}"
            for d in reversed(dated):
                if d["date"] == target:
                    prev_same = d["np"]
                    break
            if prev_year is not None and prev_same is not None:
                net_profit_latest_period = latest["np"] + prev_year - prev_same
        else:
            net_profit_latest_period = latest["np"]

    return {
        "roe_latest": roe_latest,
        "roe_5y_median": roe_5y_median,
        "net_profit_latest_period": net_profit_latest_period,
        "net_profit_annual": net_profit_annual,
    }


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# JS 计算：写 fixture，跑 node site/js/verify_raw.js
# ---------------------------------------------------------------------------

JS_RUNNER = PROJECT_ROOT / "site" / "js" / "verify_raw.js"


def run_js(fixture_path: str) -> dict:
    # 捕获原始字节再 UTF-8 解码，避免 Windows 默认 GBK 损坏中文（行业/verdict 等）
    out = subprocess.run(
        ["node", str(JS_RUNNER), fixture_path],
        capture_output=True, check=True,
    )
    return json.loads(out.stdout.decode("utf-8"))


# ---------------------------------------------------------------------------
# 对比
# ---------------------------------------------------------------------------

def close(a, b):
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= TOLERANCE
    return a == b


def main():
    codes = sys.argv[1:] or ["600900", "600987", "600919", "600887", "600019"]
    print(f"对比股票: {codes}")

    fixture = {"stocks": {}}
    for code in codes:
        raw = fetch_raw(code)
        fixture["stocks"][code] = raw

        # 空数据假绿防护（审查 #6）：任一类数据为空即明确报错，避免双端
        # 消费同样的空数据静默通过
        if not raw["quote"] or raw["quote"].get("price") is None:
            print(f"[{code}] 行情数据为空，verify 结果不可信")
            return 2
        if not raw["dividend_rows"]:
            print(f"[{code}] 分红数据为空，verify 结果不可信")
            return 2
        if not raw["financial_rows"]:
            print(f"[{code}] 财务数据为空，verify 结果不可信")
            return 2

    # Windows 默认用 GBK 写文件会损坏中文，强制 UTF-8
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, dir="/tmp", encoding="utf-8") as f:
        json.dump(fixture, f, ensure_ascii=False)
        fixture_path = f.name

    js_all = run_js(fixture_path)
    ok = True
    for code in codes:
        js = js_all[code]
        py = compute_python(fixture["stocks"][code])
        diffs = []
        for k in FIELDS_NUMERIC + FIELDS_STR:
            a, b = js.get(k), py.get(k)
            if not close(a, b):
                diffs.append(f"  {k}: JS={a!r} PY={b!r}")
        if diffs:
            ok = False
            print(f"\n[{code}] {js.get('stock_name')} — DIFF")
            for d in diffs:
                print(d)
        else:
            print(f"\n[{code}] {js.get('stock_name')} — OK  (股息率 {round(js.get('dividend_yield_before_tax', 0) or 0, 4)}%)")

    print("\n" + ("✔ 全部字段一致" if ok else "✘ 存在差异，见上"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
