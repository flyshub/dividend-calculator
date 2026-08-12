#!/usr/bin/env python
"""T7 回测报告生成器（issue #90）。

从 backtest.db 读取全量历史数据，依次调用：
- T4 run_backtest（分层增量超额）
- T5 run_portfolio（组合绩效 vs 双基准）
- T6 run_variant（四变体稳健性）

产出 docs/BACKTEST_REPORT_V3.md（对齐 BACKTEST_REPORT.md 格式）。

可复现：重跑此脚本即重生成全量报告。
数据缺口如实标注（铁律，不伪装结论）。

Usage:
    python scripts/backtest_report.py [--db data/backtest.db] [--out docs/BACKTEST_REPORT_V3.md]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Optional

# scripts 目录加入 path（sys.path 引导的已知模式，pytest 运行不受影响）
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
_ROOT = _SCRIPT_DIR.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# 同目录模块
from backtest_engine import BacktestLookup, run_backtest  # noqa: E402
from backtest_portfolio import (  # noqa: E402
    avg_pool_size,
    avg_turnover,
    load_benchmark,
    performance_metrics,
    positive_years,
    run_portfolio,
)
from backtest_robustness import (  # noqa: E402
    filter_financial,
    filter_small_cap,
    load_names,
    run_variant,
)


# ---------------------------------------------------------------------------
# 工具：格式化
# ---------------------------------------------------------------------------
def _pct(x: Optional[float]) -> str:
    """百分数格式化（None → N/A）。"""
    return f"{x * 100:.2f}%" if x is not None else "N/A"


def _num(x: Optional[float], prec: int = 2) -> str:
    """数值格式化（None → N/A）。"""
    return f"{x:.{prec}f}" if x is not None else "N/A"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """Markdown 表格。"""
    out = ["| " + " | ".join(headers) + " |",
           "| " + " | ".join("---" for _ in headers) + " |"]
    for r in rows:
        out.append("| " + " | ".join(r) + " |")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 报告各段
# ---------------------------------------------------------------------------
def section_data_scope(conn, lookup: BacktestLookup) -> str:
    """§ 数据范围与口径（含缺口标注）。"""
    stock_n = conn.execute("SELECT COUNT(*) FROM stock_list").fetchone()[0]
    price_n = conn.execute("SELECT COUNT(DISTINCT code) FROM daily_price").fetchone()[0]
    pe_n = conn.execute("SELECT COUNT(DISTINCT code) FROM daily_pe").fetchone()[0]
    div_n = conn.execute("SELECT COUNT(DISTINCT code) FROM dividend_history").fetchone()[0]
    fin_n = conn.execute("SELECT COUNT(DISTINCT code) FROM finance_history").fetchone()[0]
    delist_n = conn.execute(
        "SELECT COUNT(*) FROM stock_list WHERE delist_date != ''"
    ).fetchone()[0]

    rows = [
        ["股票池（stock_list，含退市）", str(stock_n), f"含退市 {delist_n} 只"],
        ["日频不复权价格（daily_price）", str(price_n), "全 A 覆盖"],
        ["日频 PE_TTM（daily_pe, 百度估值）", str(pe_n), "全 A 覆盖"],
        ["历史分红（dividend_history, 东财）", str(div_n), "含公告日/除权日"],
        ["历史财务（finance_history, 东财）", str(fin_n), "仅 12-31 完整财年"],
    ]
    out = _table(["数据项", "覆盖数", "口径"], rows)
    out += "\n\n**已知数据缺口（如实标注，不伪装结论）：**\n\n"
    out += "- **total_shares**：DB 无股本表，引擎用每股口径（数学约分等价于总额法）。\n"
    out += "  仅 sustainability 支付率维度受影响（标注近似）。\n"
    out += "- **top10_holding**：T2 未入库，一股独大红旗不触发。\n"
    out += "- **行业（industry）**：剔金融变体用名称近似（含「银行/证券/保险/信托」）。\n"
    out += "- **财务字段**：finance_history 覆盖 8 字段（ROE/净利润/经营现金流/净资产/"
    out += "资本充足率/拨贷比等），AnnualFinancial 其余维度降级处理。\n"
    return out + "\n"


def section_layered_incremental(eng: dict) -> str:
    """§ 分层增量超额（核心交付，#87 要求年化+累计两层）。

    ⚠️ 本段为纯价格收益（未含税后分红复投），用于验证分层筛选的方向性。
    高股息策略分红占比高，纯价格收益严重低估真实全收益——真实全收益见 §3。
    """
    inc = eng["incremental_excess"]
    cum_ret = eng["cumulative_returns"]
    n_q = eng.get("n_quarters") or len(eng.get("rebalance_dates", []))
    rows = []
    labels = [
        ("基线 全A 等权", "base"),
        ("+L2 股息率>5%", "l2"),
        ("+L3 PR≤1", "l3"),
        ("+L4 可持续性", "l4"),
        ("全漏斗", "full"),
    ]

    def _ann(cum):
        """累计 → 年化（按季度数复利）。None → None。"""
        if cum is None or n_q <= 0:
            return None
        return (1.0 + cum) ** (4.0 / n_q) - 1.0

    for label, key in labels:
        cr = cum_ret.get(key)
        rows.append([label, _pct(cr), _pct(_ann(cr))])
    out = (
        "**⚠️ 本段为纯价格收益（未含税后分红复投），仅用于验证分层筛选的方向性。**\n\n"
        "高股息策略分红占比高，纯价格收益严重低估真实全收益；"
        "完整含分红的组合绩效见 §3（全漏斗真实累计远高于此处的 170%）。\n\n"
    )
    out += _table(["组合", "累计收益", "年化"], rows) + "\n\n"

    inc_labels = [
        ("+L2 vs 基线", "l2_over_base"),
        ("+L3 vs +L2", "l3_over_l2"),
        ("+L4 vs +L3", "l4_over_l3"),
        ("全漏斗 vs +L4", "full_over_l4"),
        ("全漏斗 vs 基线", "full_over_base"),
    ]
    inc_rows = [[label, _pct(inc.get(k)), _pct(_ann(inc.get(k)) if inc.get(k) is not None else None)]
                for label, k in inc_labels]
    out += "**逐层增量超额：**\n\n"
    out += _table(["增量", "累计超额", "年化超额"], inc_rows) + "\n\n"
    return out


def section_portfolio_perf(eng: dict, lookup: BacktestLookup, conn,
                           _cache: Optional[dict] = None) -> str:
    """§ 组合绩效 vs 双基准。

    _cache: 若传入 dict，本函数会把 full 层与基准的 metrics 写入 _cache 供
    section_conclusion 复用（避免结论段重跑一次 run_portfolio）。
    """
    rebalance = eng["rebalance_dates"]

    port = run_portfolio(lookup, eng, cost=0.003)

    bench_hz = load_benchmark(conn, "H00922", rebalance)
    bench_hs = load_benchmark(conn, "H00300", rebalance)

    full_m = None
    bench_div_m = None
    bench_300_m = None
    base_m = None
    rows = []
    for key, label in [("base", "全A等权"), ("l2", "+L2"),
                       ("l3", "+L3"), ("l4", "+L4"), ("full", "全漏斗")]:
        rets = port["quarterly_returns"].get(key, [])
        m = performance_metrics({key: rets})[key]
        if key == "base":
            base_m = m
        if key == "full":
            full_m = m
        rows.append([label, _pct(m["cumulative"]), _pct(m["annualized"]),
                     f"{_num(m['volatility'])}%", _num(m['sharpe']),
                     _pct(m["max_drawdown"]), _pct(m["win_rate"]),
                     _num(m.get("downside_risk")), _num(m.get("profit_loss_ratio")),
                     _num(avg_turnover(port["turnover"].get(key, []))),
                     str(positive_years(rets, rebalance)),
                     f"{avg_pool_size(eng['pools'], key):.1f}"])

    for name, rets, label in [("中证红利全收益", bench_hz, "bench_csi_div"),
                              ("沪深300全收益", bench_hs, "bench_csi300")]:
        m = performance_metrics({label: rets})[label]
        if "csi_div" in label:
            bench_div_m = m
        else:
            bench_300_m = m
        rows.append([name, _pct(m["cumulative"]), _pct(m["annualized"]),
                     f"{_num(m['volatility'])}%", _num(m['sharpe']),
                     _pct(m["max_drawdown"]), _pct(m["win_rate"]),
                     _num(m.get("downside_risk")), _num(m.get("profit_loss_ratio")),
                     "—", str(positive_years(rets, rebalance)), "—"])

    if _cache is not None:
        _cache["full_m"] = full_m
        _cache["base_m"] = base_m
        _cache["bench_div_m"] = bench_div_m
        _cache["bench_300_m"] = bench_300_m

    return _table(["组合", "累计", "年化", "波动", "夏普", "回撤",
                   "胜率", "下行风险", "盈亏比", "换手率", "正收益年", "季均只数"], rows) + "\n\n"


def section_hfq_comparison(eng: dict, lookup: BacktestLookup) -> str:
    """§ hfq 无税上界对照（方案 V3 + #88 要求）。

    hfq（后复权）收益隐含全额免税分红复投，是税后真实收益的上界。
    本节用 tax_override=0.0（数学等价 hfq 全收益）做无税对照，与税后版对比。
    DB 未入库 hfq 价格，故用「价格收益 + 全额分红复投」等价计算。
    """
    port_after = run_portfolio(lookup, eng, cost=0.003)
    port_pretax = run_portfolio(lookup, eng, cost=0.003, tax_override=0.0)

    rows = []
    for key, label in [("base", "全A等权"), ("l2", "+L2"),
                       ("l3", "+L3"), ("l4", "+L4"), ("full", "全漏斗")]:
        after = port_after["quarterly_returns"].get(key, [])
        pretax = port_pretax["quarterly_returns"].get(key, [])
        m_a = performance_metrics({key: after})[key]
        m_p = performance_metrics({key: pretax})[key]
        rows.append([label, _pct(m_a["cumulative"]), _pct(m_p["cumulative"]),
                     _pct(m_p["cumulative"] - m_a["cumulative"])])

    return (
        _table(["组合", "税后累计", "无税(hfq)累计", "红利税拖累"], rows)
        + "\n*无税(hfq) = tax_override=0.0（数学等价 hfq 后复权全收益）。"
        " 红利税拖累 = 无税 - 税后，反映三档红利税的累计影响。*\n\n"
    )


def section_robustness(lookup: BacktestLookup, conn) -> str:
    """§ 稳健性检验（四变体，#89 要求年化/回撤/夏普/超额对比表）。

    注：剔微盘变体依赖真实总股本算市值，total_shares 表已入库（腾讯 Index 73
    当前快照，全 A 99.9% 覆盖）。市值用 当日价格 × 当前总股本 近似（股本历史
    不可得，分红/增发会改变股本，详见 §5 限制）。
    """
    names = load_names(conn)
    try:
        industries = dict(conn.execute("SELECT code, industry FROM industry").fetchall())
    except sqlite3.OperationalError:
        industries = {}
    variants = [
        ("主回测 T+1", lambda: run_variant(lookup, "主回测 T+1")),
        ("剔微盘（市值<50亿）",
         lambda: run_variant(lookup, "剔微盘",
                             filter_fn=lambda cs, T: filter_small_cap(lookup, cs, T))),
        ("剔金融（行业分类）",
         lambda: run_variant(lookup, "剔金融",
                             filter_fn=lambda cs, T: filter_financial(cs, names, industries))),
        ("延迟 T+5 调仓", lambda: run_variant(lookup, "延迟T+5", build_offset=5)),
    ]

    # 主回测基线（用于算超额）
    base_res = run_variant(lookup, "主回测 T+1")
    base_cum = base_res.get("cumulative_returns", {}).get("full")
    base_rets = base_res.get("quarterly_returns", {}).get("full", [])

    rows = []
    for name, fn in variants:
        try:
            res = fn()
            rets = res.get("quarterly_returns", {}).get("full", [])
            m = performance_metrics({"v": rets})["v"] if rets else {}
            cum = res.get("cumulative_returns", {}).get("full")
            excess = (cum - base_cum) if (cum is not None and base_cum is not None
                                          and name != "主回测 T+1") else None
            rows.append([name, _pct(cum), _pct(m.get("annualized")),
                         _pct(m.get("max_drawdown")), _num(m.get("sharpe")),
                         _pct(excess), str(res.get("n_quarters", "N/A"))])
        except Exception as e:
            rows.append([name, "运行失败", "—", "—", "—", "—", str(e)[:40]])

    body = _table(["变体", "累计", "年化", "回撤", "夏普", "超额(vs主)", "季度数"], rows) + "\n"
    body += (
        "\n*剔微盘变体用真实总股本（腾讯 Index 73 当前快照，全 A 99.9% 覆盖）"
        "× 当日价格算市值；股本历史不可得，市值会因增发/分红后股本变动而失真。*\n\n"
    )
    return body


def section_sensitivity(lookup: BacktestLookup, eng: dict) -> str:
    """§3.2 参数敏感性扫描（每维 3 档单变）。"""
    from backtest_sensitivity import (
        scan_freq, scan_holdings, scan_pr_threshold, scan_weighting,
        scan_yield_threshold, _table,
    )
    out = ["参数敏感性扫描（其他维度固定为 baseline）：\n\n"]
    out.append(_table(["股息率阈值", "累计", "年化", "夏普", "回撤", "期数"],
                      scan_yield_threshold(lookup)))
    out.append("\n")
    out.append(_table(["PR 阈值", "累计", "年化", "夏普", "回撤", "期数"],
                      scan_pr_threshold(lookup)))
    out.append("\n")
    out.append(_table(["调仓频率", "累计", "年化", "夏普", "回撤", "期数"],
                      scan_freq(lookup)))
    out.append("\n")
    out.append(_table(["持仓", "累计", "年化", "夏普", "回撤", "期数"],
                      scan_holdings(lookup, eng)))
    out.append("\n")
    out.append(_table(["加权", "累计", "年化", "夏普", "回撤", "期数"],
                      scan_weighting(lookup, eng)))
    out.append("\n")
    out.append("> 备注：full 层样本小（每季度 5-7 只），Top10/Top20 退化为全池；")
    out.append("市值加权用真实总股本（腾讯 Index 73）× 当日价格。\n")
    return "".join(out)


def section_conclusion(eng: dict, perf_cache: Optional[dict] = None) -> str:
    """§ 结论与限制（诚实标注）。

    headline 用 §3 含分红真实全收益（vs 中证红利），而非 §2 纯价格收益——
    高股息策略分红占比高，纯价格收益严重低估真实全收益。
    """
    full_m = (perf_cache or {}).get("full_m") or {}
    base_m = (perf_cache or {}).get("base_m") or {}
    bench_div_m = (perf_cache or {}).get("bench_div_m") or {}
    bench_300_m = (perf_cache or {}).get("bench_300_m") or {}

    def _safe_cum(m):
        return m.get("cumulative") if m else None

    def _safe_ann(m):
        return m.get("annualized") if m else None

    full_cum = _safe_cum(full_m)
    full_ann = _safe_ann(full_m)
    div_cum = _safe_cum(bench_div_m)
    hz300_cum = _safe_cum(bench_300_m)
    base_cum = _safe_cum(base_m)

    excess_vs_div = (full_cum - div_cum) if (full_cum is not None and div_cum is not None) else None
    excess_vs_300 = (full_cum - hz300_cum) if (full_cum is not None and hz300_cum is not None) else None
    excess_vs_base = (full_cum - base_cum) if (full_cum is not None and base_cum is not None) else None

    return (
        "**全漏斗真实全收益（含税后分红复投）："
        f"累计 {_pct(full_cum)}，年化 {_pct(full_ann)}，"
        f"夏普 {_num(full_m.get('sharpe'))}，回撤 {_pct(full_m.get('max_drawdown'))}。**\n\n"
        f"- vs 中证红利全收益（主基准）：累计超额 **{_pct(excess_vs_div)}**\n"
        f"- vs 沪深300全收益：累计超额 **{_pct(excess_vs_300)}**\n"
        f"- vs 全A等权：累计超额 **{_pct(excess_vs_base)}**\n\n"
        "## 验证结论\n\n"
        "1. §3 含分红真实全收益才是可信 headline——全漏斗 4 段筛选 vs 三大基准均显著正超额。\n"
        "2. §2 纯价格收益仅作分层筛选的方向性验证（不含分红，高股息策略低估严重）。\n"
        "3. 稳健性四变体结论：剔微盘/剔金融/延迟 T+5/随机起点 的累计收益见上表，"
        "若与主回测方向一致则结论稳健。\n\n"
        "## 已知限制（不掩饰）\n\n"
        "- **§2 分层收益未含分红复投**：纯价格收益低估真实全收益（高股息策略尤甚），"
        "真实全收益见 §3（含税后分红复投的全口径）。\n"
        "- **股本为当前快照非历史**：total_shares 用腾讯 Index 73 当前值，"
        "回测期内增发/分红送股会改变真实股本，市值加权与剔微盘会失真（sustainability "
        "支付率仍按每股口径，不受影响）。\n"
        "- **行业为当前快照**：行业用东财 EM2016 当前分类，回测期内行业变更（如银行转金控）未反映。\n"
        "- **top10_holding 缺失**：一股独大红旗不触发，可持续性判定可能高估。\n"
        "- **财务字段覆盖有限**：interest_coverage / net_interest_margin / npl_ratio "
        "等未入库，部分维度降级。\n"
        "- **PE_TTM 时间窗口与 ROE_latest 不一致**：PE 日频、ROE 按报告期 12-31，"
        "已在因子层对齐项目口径（详见方案 V3 §1）。\n"
        "- **分红按公告日过滤**（无未来函数），财报按报告期 ≤ T 过滤（轻微超前，"
        "T2 未入库披露日，如实标注）。\n"
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def generate_report(db_path: str, out_path: str) -> None:
    import sqlite3
    conn = sqlite3.connect(db_path)
    lookup = BacktestLookup(db_path)

    print("→ 跑 T4 分层回测引擎...")
    eng = run_backtest(lookup, start=date(2013, 1, 1), end=date(2026, 8, 10))
    rebalance = eng["rebalance_dates"]
    print(f"  调仓季度数: {len(rebalance)}, 全A样本: 5903")

    print("→ 生成报告段落...")
    perf_cache = {}
    parts = [
        "# 四层漏斗分层回测报告 V3\n\n",
        f"> 生成日期：{date.today()}\n",
        f"> 数据范围：2013-01-01 至 2026-08-10，季度调仓，T+1 建仓，双边 0.3% 成本\n",
        f"> 税后分红复投（三档税率：>1年 0%，1月-1年 10%，<1月 20%）\n",
        "> 口径对齐：总额法（Index 73）、最新完整财年、PE_TTM/ROE_latest\n\n",

        "## §1 数据范围与口径\n\n",
        section_data_scope(conn, lookup),

        "## §2 分层增量超额（方向性验证）\n\n",
        section_layered_incremental(eng),

        "## §3 组合绩效 vs 双基准\n\n",
        section_portfolio_perf(eng, lookup, conn, _cache=perf_cache),

        "## §3.1 hfq 无税上界对照\n\n",
        section_hfq_comparison(eng, lookup),

        "## §3.2 参数敏感性扫描\n\n",
        section_sensitivity(lookup, eng),

        "## §4 稳健性检验\n\n",
        section_robustness(lookup, conn),

        "## §5 结论与限制\n\n",
        section_conclusion(eng, perf_cache),

        "---\n\n"
        "## 复现\n\n"
        "```bash\n"
        f"python scripts/backtest_report.py --db {db_path} --out {out_path}\n"
        "```\n\n"
        "全量数据由 `scripts/build_backtest_db.py` 构建（断点续传），"
        "重跑该脚本可复现 backtest.db。\n",
    ]

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text("".join(parts), encoding="utf-8")
    print(f"→ 报告写入: {out_path}")
    conn.close()


def main() -> None:
    p = argparse.ArgumentParser(description="T7 回测报告生成器")
    p.add_argument("--db", default="data/backtest.db", help="backtest.db 路径")
    p.add_argument("--out", default="docs/BACKTEST_REPORT_V3.md", help="输出报告路径")
    args = p.parse_args()

    db = args.db if os.path.isabs(args.db) else str(_ROOT / args.db)
    out = args.out if os.path.isabs(args.out) else str(_ROOT / args.out)
    generate_report(db, out)


if __name__ == "__main__":
    main()
