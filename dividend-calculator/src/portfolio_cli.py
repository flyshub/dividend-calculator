"""组合股息分析 CLI 渲染（#31）。

入口有两个：
    python -m src.main --portfolio 600900:1000,601398:5000     # 主 CLI 子模式
    python -m src.portfolio_cli 600900:1000 --json             # 独立运行

输出：逐只持仓的年股息收入、组合加权股息率、税前/税后合计、行业分布、可持续性概览。
"""
import argparse
import json
import sys
from dataclasses import asdict

from .portfolio import analyze_portfolio, parse_holdings


def _yi(value) -> str:
    """按量级选单位：<1万→元，<1亿→万元，否则亿元（个人持仓常在万元级）。"""
    if value is None:
        return "—"
    if value < 1e4:
        return f"{value:,.0f} 元"
    if value < 1e8:
        return f"{value / 1e4:,.2f} 万元"
    return f"{value / 1e8:.2f} 亿元"


def _pct(value) -> str:
    return "—" if value is None else f"{value:.2f}%"


def _money(value) -> str:
    return "—" if value is None else f"{value:,.0f} 元"


def render_text(summary) -> str:
    lines = []
    lines.append("=" * 64)
    lines.append("组合股息分析（税前；税率按持股期限另列）")
    lines.append("=" * 64)
    lines.append(f"{'代码':<8}{'名称':<10}{'持股':>10}{'每股分红':>10}{'年股息':>12}{'股息率':>8}")
    lines.append("-" * 64)
    for p in summary.positions:
        name = (p.stock_name or "—")[:8]
        dps = "—" if p.dividend_per_share is None else f"{p.dividend_per_share:.4f}"
        lines.append(f"{p.stock_code:<8}{name:<10}{p.shares:>10,.0f}{dps:>10}"
                     f"{_money(p.annual_income):>14}{_pct(p.dividend_yield):>9}")
        if p.error:
            lines.append(f"{'':<8}└─ 未计入：{p.error}")
    lines.append("-" * 64)
    lines.append(f"持仓市值合计：{_yi(summary.total_market_value)}")
    lines.append(f"年度股息合计（税前）　：{_money(summary.total_annual_income)}")
    lines.append(f"　持股 1 月~1 年（10%）：{_money(summary.after_tax_income_10)}")
    lines.append(f"　持股不足 1 月（20%）　：{_money(summary.after_tax_income_20)}")
    lines.append(f"组合股息率（市值加权）：{_pct(summary.weighted_yield)}")

    if summary.industry_breakdown:
        lines.append("")
        lines.append("行业分布（占年度股息）：")
        for name, amount, share in summary.industry_breakdown:
            lines.append(f"  {name:<16}{_money(amount):>14}  {share:5.1f}%")

    if summary.verdict_summary:
        lines.append("")
        lines.append("可持续性概览：" + "、".join(
            f"{k} {v} 只" for k, v in sorted(summary.verdict_summary.items(), key=lambda kv: -kv[1])))

    if summary.errors:
        lines.append("")
        lines.append("未计入的持仓（不影响其它持仓）：")
        for err in summary.errors:
            lines.append(f"  {err}")
    lines.append("=" * 64)
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="组合股息分析（#31）")
    parser.add_argument("holdings", help="持仓，如 600900:1000,601398:5000（代码:股数）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    try:
        holdings = parse_holdings(args.holdings)
    except ValueError as e:
        print(f"参数错误：{e}", file=sys.stderr)
        return 2

    summary = analyze_portfolio(holdings)
    if args.json:
        print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    else:
        print(render_text(summary))
    return 0 if summary.ok_count else 1


if __name__ == "__main__":
    sys.exit(main())
