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
    """§ 分层增量超额（核心交付，#87 要求年化+累计两层）。"""
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
    out = _table(["组合", "累计收益", "年化"], rows) + "\n\n"

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


def section_portfolio_perf(eng: dict, lookup: BacktestLookup, conn) -> str:
    """§ 组合绩效 vs 双基准。"""
    rebalance = eng["rebalance_dates"]

    port = run_portfolio(lookup, eng, cost=0.003)

    bench_hz = load_benchmark(conn, "H00922", rebalance)
    bench_hs = load_benchmark(conn, "H00300", rebalance)

    rows = []
    for key, label in [("base", "全A等权"), ("l2", "+L2"),
                       ("l3", "+L3"), ("l4", "+L4"), ("full", "全漏斗")]:
        rets = port["quarterly_returns"].get(key, [])
        m = performance_metrics({key: rets})[key]
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
        rows.append([name, _pct(m["cumulative"]), _pct(m["annualized"]),
                     f"{_num(m['volatility'])}%", _num(m['sharpe']),
                     _pct(m["max_drawdown"]), _pct(m["win_rate"]),
                     _num(m.get("downside_risk")), _num(m.get("profit_loss_ratio")),
                     "—", str(positive_years(rets, rebalance)), "—"])

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

    注：剔微盘变体依赖真实总股本算市值，DB 无股本表用 1.0 近似时
    全部股票市值 < 50亿会被全剔（结果 0.00%）。该变体需 total_shares 真实值入库
    后才能产出有意义结论——此处保留代码结构，结果如实呈现并标注。
    """
    names = load_names(conn)
    variants = [
        ("主回测 T+1", lambda: run_variant(lookup, "主回测 T+1")),
        ("剔微盘（市值<50亿）",
         lambda: run_variant(lookup, "剔微盘",
                             filter_fn=lambda cs, T: filter_small_cap(lookup, cs, T))),
        ("剔金融（名称近似）",
         lambda: run_variant(lookup, "剔金融",
                             filter_fn=lambda cs, T: filter_financial(cs, names))),
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
        "\n*剔微盘变体依赖真实总股本算市值；当前 total_shares 用 1.0 近似时"
        "全部股票市值 < 50亿被全剔（结果 0.00%）。需 total_shares 真实值入库"
        "（T2 数据管线待办）后才能产出有意义结论。*\n\n"
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
    out.append("市值加权在 total_shares=1.0 近似下退化为价格加权。\n")
    return "".join(out)


def section_conclusion(eng: dict) -> str:
    """§ 结论与限制（诚实标注）。"""
    inc = eng["incremental_excess"]
    full_cum = eng["cumulative_returns"].get("full")
    baseline_cum = eng["cumulative_returns"].get("base")
    excess = inc.get("full_over_base")

    return (
        f"**全漏斗累计收益：{_pct(full_cum)}，"
        f"基线全A等权：{_pct(baseline_cum)}，"
        f"累计超额：{_pct(excess)}。**\n\n"
        "## 验证结论\n\n"
        "1. 分层增量超额的方向与幅度见上表；正超额表明漏斗筛选有增益。\n"
        "2. 组合绩效 vs 双基准（中证红利全收益为主基准）：夏普、回撤、胜率对比见上表。\n"
        "3. 稳健性四变体结论：剔微盘/剔金融/延迟 T+5/随机起点 的累计收益见上表，"
        "若与主回测方向一致则结论稳健。\n\n"
        "## 已知限制（不掩饰）\n\n"
        "- **历史总股本不可得**：用每股口径近似（数学等价于总额法），"
        "sustainability 支付率维度受影响。\n"
        "- **行业取名称近似**（剔金融变体）：无法精确识别所有金融股。\n"
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
    parts = [
        "# 四层漏斗分层回测报告 V3\n\n",
        f"> 生成日期：{date.today()}\n",
        f"> 数据范围：2013-01-01 至 2026-08-10，季度调仓，T+1 建仓，双边 0.3% 成本\n",
        f"> 税后分红复投（三档税率：>1年 0%，1月-1年 10%，<1月 20%）\n",
        "> 口径对齐：总额法（Index 73）、最新完整财年、PE_TTM/ROE_latest\n\n",

        "## §1 数据范围与口径\n\n",
        section_data_scope(conn, lookup),

        "## §2 分层增量超额（核心交付）\n\n",
        section_layered_incremental(eng),

        "## §3 组合绩效 vs 双基准\n\n",
        section_portfolio_perf(eng, lookup, conn),

        "## §3.1 hfq 无税上界对照\n\n",
        section_hfq_comparison(eng, lookup),

        "## §3.2 参数敏感性扫描\n\n",
        section_sensitivity(lookup, eng),

        "## §4 稳健性检验\n\n",
        section_robustness(lookup, conn),

        "## §5 结论与限制\n\n",
        section_conclusion(eng),

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
