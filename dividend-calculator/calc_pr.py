#!/usr/bin/env python
"""市赚率（PR）计算 CLI 工具

用法:
    python calc_pr.py 600900          # 长江电力
    python calc_pr.py 600519          # 贵州茅台
    python calc_pr.py 600887          # 伊利股份

数据来源（多源自动降级）：
    PE-TTM / PB: 腾讯行情 → 东方财富
    ROE / 净利润: 同花顺财报 → 东方财富
    行业分类: 东方财富
    分红数据: 复用项目股息率链路
"""
import os
import sys

# Windows 控制台默认 GBK 编码无法输出 emoji，强制 stdout/stderr 用 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

# Ensure project root is on sys.path
_project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _project_root)

from src.analysis import run_stock_analysis
from src.pr import PRResult
from src.sustainability_calculator import SustainabilityResult, explain_sustainability


def _fmt_money(value):
    """格式化金额"""
    if value is None:
        return "N/A"
    yi = value / 1e8
    if yi >= 1:
        return f"{yi:.2f} 亿元"
    wan = value / 1e4
    return f"{wan:.2f} 万元"


def _fmt_pct(value):
    """格式化百分比"""
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def _fmt_pr(value):
    """格式化市赚率"""
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def _valuation_color(zone: str) -> str:
    """估值档位标记"""
    mapping = {
        "低估": " 🟢",
        "合理偏低": " 🟡",
        "合理": " 🔵",
        "高估": " 🔴",
        "无法判定": "",
    }
    return mapping.get(zone, "")


def print_result(result: PRResult):
    """格式化输出市赚率计算结果"""
    name = result.stock_name or result.stock_code
    print()
    print("=" * 60)
    print(f"  市赚率（PR）估值分析 - {name} ({result.stock_code})")
    print("=" * 60)

    # 亏损股特殊处理
    if result.is_loss_stock:
        print()
        print("  ⚠️  该股为亏损股，市赚率估值不适用")
        print()
        if result.errors:
            print("  数据采集日志:")
            for e in result.errors:
                print(f"    - {e}")
        return

    # === 市赚率核心结果 ===
    print()
    print("  ── 市赚率结果 ──")
    print(f"  基础市赚率:    {_fmt_pr(result.pr_basic)}")
    print(f"  修正市赚率:    {_fmt_pr(result.pr_corrected)}")
    print(f"  PB-市赚率:     {_fmt_pr(result.pr_pb)}")

    zone_label = result.valuation_zone + _valuation_color(result.valuation_zone)
    print(f"  估值档位:      {zone_label}")
    if result.pr_warning:
        print(f"  ⚠️  提示:       {result.pr_warning}")

    # === 中间计算数据 ===
    print()
    print("  ── 输入数据 ──")
    print(f"  PE-TTM:        {_fmt_pr(result.pe_ttm)}")
    print(f"  PB:            {_fmt_pr(result.pb)}")
    print(f"  ROE (最新年报): {_fmt_pct(result.roe_latest)}")
    print(f"  ROE (5年中位):  {_fmt_pct(result.roe_5y_median)}")
    print(f"  最新报告期净利润:{_fmt_money(result.net_profit_latest_period)}")
    print(f"  年报净利润:      {_fmt_money(result.net_profit_annual)}")
    print(f"  现金分红总额:    {_fmt_money(result.dividend_total)}")
    print(f"  股利支付率:     {_fmt_pct(result.payout_ratio * 100 if result.payout_ratio is not None else None)}")
    print(f"  修正系数 N:     {_fmt_pr(result.n_factor)}")
    print(f"  行业分类:       {result.industry}")

    # === 数据源 ===
    print()
    print("  ── 数据来源 ──")
    print(f"  PE/PB:    {result.pe_pb_source}")
    print(f"  财务数据:  {result.finance_source}")
    print(f"  行业分类:  {result.industry_source}")

    # === 错误日志 ===
    if result.errors:
        print()
        print("  ── 数据采集日志 ──")
        for e in result.errors:
            print(f"    - {e}")

    # === 公式说明 ===
    print()
    print("  ── 公式 ──")
    print("  基础PR  = PE / ROE / 100")
    print("  修正PR  = N × PE / ROE / 100")
    print("          N = 0.5 / 股利支付率, 区间 [1.0, 2.0]")
    print("  PB-PR   = PB / ROE² / 100")
    print()
    print("  估值四档: ≤0.5低估 | 0.5~0.7合理偏低 | 0.7~1.0合理 | >1.0高估")
    print()


def _verdict_emoji(verdict: str) -> str:
    return {"可持续": " 🟢", "偏弱": " 🟡", "不可持续": " 🔴", "未评估": ""}.get(verdict, "")


def _dimension_label(key: str) -> str:
    return {
        "cf_coverage": "现金流覆盖",
        "payout": "股利支付率",
        "profitability": "盈利稳定性",
        "balance_sheet": "资产负债表",
        "dividend_history": "分红历史",
        "industry": "行业属性",
        "capital_adequacy": "资本充足率",
        "net_interest_margin": "净息差",
        "npl": "不良贷款率",
        "provision": "拨备覆盖率",
    }.get(key, key)


def _format_metrics_lines(metrics: dict) -> list:
    """把支撑数据指标格式化为可打印行列表（仅输出有值的指标）。"""
    lines = []
    specs = [
        ("payout_ratio", "股利支付率", lambda v: f"{v*100:.1f}%"),
        ("cf_coverage", "现金流覆盖", lambda v: f"{v:.2f}x"),
        ("fcf_coverage", "自由现金流覆盖", lambda v: f"{v:.2f}x"),
        ("debt_ratio", "资产负债率", lambda v: f"{v*100:.1f}%"),
        ("interest_coverage", "利息保障倍数", lambda v: f"{v:.2f}x"),
        ("roe_latest", "ROE(最新)", lambda v: f"{v:.2f}%"),
        ("net_profit_yoy", "净利润同比", lambda v: f"{v:.1f}%"),
        ("consecutive_dividend_years", "连续分红年数", lambda v: f"{int(v)} 年"),
    ]
    for key, label, fmt in specs:
        v = metrics.get(key)
        if v is not None:
            lines.append(f"  {label:<14}: {fmt(v)}")
    return lines


def print_sustainability(yield_pct, result: SustainabilityResult):
    """格式化输出股息可持续性评估结果。"""
    print("=" * 60)
    print(f"  股息可持续性分析 - 税前股息率 {yield_pct:.2f}%")
    print("=" * 60)

    if not result.triggered:
        print(f"  股息率未超过阈值，未做可持续性评估（{'; '.join(result.notes) or ''}）")
        print()
        return

    verdict_label = result.verdict + _verdict_emoji(result.verdict)
    score_str = f"综合评分 {result.score:.2f}/2.0" if result.score is not None else "评分不足"
    branch_str = "（银行/保险专项）" if result.branch == "finance" else ""
    print(f"  结论: {verdict_label}   {score_str}{branch_str}")

    explanation = explain_sustainability(result)
    if explanation:
        print()
        print("  ── 结论说明 ──")
        for line in explanation:
            print(f"    {line}")

    if result.dimension_scores:
        print()
        print("  ── 各维度评分 ──")
        for k, v in result.dimension_scores.items():
            print(f"  {_dimension_label(k):　<6}: {v} 分")

    metrics_lines = _format_metrics_lines(result.metrics)
    if metrics_lines:
        print()
        print("  ── 支撑数据 ──")
        for line in metrics_lines:
            print(line)

    if result.fatal_flags:
        print()
        print("  ── 🔴 致命红旗（不可持续）──")
        for f in result.fatal_flags:
            print(f"    - {f}")

    if result.warning_flags:
        print()
        print("  ── 🟠 情境警示 ──")
        for w in result.warning_flags:
            print(f"    - {w}")

    print()
    print("  评分区间: ≥1.5可持续 | 1.0~1.5偏弱 | <1.0不可持续（情境警示降一档）")
    print()


def main():
    if len(sys.argv) < 2:
        print("用法: python calc_pr.py <股票代码>")
        print("示例: python calc_pr.py 600900")
        sys.exit(1)

    stock_input = sys.argv[1].strip()

    # 执行综合分析流水线
    print(f"\n正在获取 {stock_input} 的股票数据...")
    analysis = run_stock_analysis(stock_input)
    if analysis is None:
        print(f"错误: 无法获取股票 {stock_input} 的数据，请检查代码是否正确")
        sys.exit(1)

    stock_info = analysis.stock_info
    print(f"  已获取: {stock_info.stock_code} 价格={stock_info.current_price:.2f} 总股本={stock_info.total_shares/1e8:.2f}亿")

    if analysis.dividend_total > 0:
        print(f"  已获取: 现金分红总额 {analysis.dividend_total/1e8:.2f}亿元")
    else:
        print("  未获取到有效分红数据")

    print("正在计算市赚率...")
    result = analysis.pr_result

    # 输出
    print_result(result)

    # 股息可持续性（仅高股息率触发时输出）
    if analysis.sustainability is not None and analysis.sustainability.triggered:
        print_sustainability(analysis.dividend_yield_before_tax, analysis.sustainability)


if __name__ == "__main__":
    main()
