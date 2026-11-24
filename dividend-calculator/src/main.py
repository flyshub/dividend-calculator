"""
真实股息率计算工具 - 主入口
完全使用真实数据，不虚构任何数据
"""
import io
import logging
import sys
from pathlib import Path

import click

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# 配置日志
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

from src.dividend import calculate_true_dividend_yield


def ensure_utf8_stdout():
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')


@click.command()
@click.argument('stock_input', type=str, required=False)
@click.option('--portfolio', 'portfolio_text', default=None,
              help='组合模式：持仓「代码:股数」逗号分隔，如 600900:1000,601398:5000')
def dividend(stock_input, portfolio_text):
    """
    计算股票的真实股息率

    STOCK_INPUT: 股票代码（如600887）或名称（如伊利股份）

    加 --portfolio 时改为组合股息分析（#31），STOCK_INPUT 可省略。
    """
    ensure_utf8_stdout()

    if portfolio_text:
        from src.portfolio import analyze_portfolio, parse_holdings
        from src.portfolio_cli import render_text

        try:
            holdings = parse_holdings(portfolio_text)
        except ValueError as e:
            raise click.ClickException(f"参数错误：{e}")
        click.echo(render_text(analyze_portfolio(holdings)))
        return

    if not stock_input:
        raise click.ClickException("请提供股票代码，或使用 --portfolio 持仓列表")
    click.echo("=" * 60)
    click.echo(f"真实股息率计算: {stock_input}")
    click.echo("=" * 60)

    result = calculate_true_dividend_yield(stock_input)

    if result is None:
        raise click.ClickException("计算失败，请检查股票代码或网络连接")

    click.echo("\n" + "=" * 60)
    click.echo("计算结果")
    click.echo("=" * 60)

    if result.stock_name:
        click.echo(f"股票名称: {result.stock_name}")
    click.echo(f"股票代码: {result.stock_code}")

    click.echo()

    if result.total_dividend <= 0:
        raise click.ClickException(f"无有效分红数据: {result.explanation}")

    click.echo(f"最新分红年度: {result.latest_year}")
    click.echo(f"当前股价: {result.current_price:.2f} 元")
    click.echo(f"总股本: {result.total_shares/100000000:.2f} 亿股")
    click.echo(f"总市值: {result.total_market_cap/100000000:.2f} 亿元")
    click.echo(f"近一年现金分红总额: {result.total_dividend/100000000:.2f} 亿元")

    if result.dividend_details and len(result.dividend_details) > 1:
        click.echo()
        click.echo("分红明细:")
        for detail in result.dividend_details:
            click.echo(f"  {detail.report_time}: 10派{detail.dividend_per_10}元")

    click.echo()
    click.echo(f"真实股息率(含税): {result.dividend_yield_before_tax:.2f}%")
    click.echo(f"真实股息率(扣税10%后): {result.dividend_yield_after_tax:.2f}%")
    click.echo(f"真实股息率(扣税20%后): {result.dividend_yield_after_tax_20:.2f}%")

    click.echo()
    click.echo("计算说明:")
    click.echo(f"  {result.explanation}")

    click.echo()
    click.echo("=" * 60)


if __name__ == "__main__":
    dividend()
