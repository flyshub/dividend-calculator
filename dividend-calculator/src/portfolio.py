"""组合股息分析（#31）：持仓 → 年度股息现金流 / 加权股息率 / 行业分布 / 可持续性概览。

与 #27（组合回测）区分：这里只做「按当前持仓，一年能收多少分红」的汇总，
不涉及回测、调仓或择时。

口径：
- 每股分红 = 最新完整财年现金分红总额 ÷ 当前总股本（与单股主卡同源，总额法）。
- 年股息收入 = 每股分红 × 持股数（税前）。
- 组合股息率 = Σ年收入 ÷ Σ持仓市值（按市值加权）；市值缺价的持仓不进分母，
  单列在 errors 里，避免「漏算一只就当它 0 股息」把加权股息率压低。
- 单只失败（取数失败/无分红）不阻塞其它持仓。

仅计算与展示，不改任何判定口径。
"""
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .analysis import run_stock_analysis

logger = logging.getLogger(__name__)


@dataclass
class Holding:
    """一笔持仓：股票代码 + 持股数（股）。"""
    stock_code: str
    shares: float


@dataclass
class PortfolioPosition:
    """单只持仓的分析结果。任一字段缺失都表示数据不可得，不补默认值。"""
    stock_code: str
    shares: float
    stock_name: Optional[str] = None
    price: Optional[float] = None
    market_value: Optional[float] = None
    dividend_per_share: Optional[float] = None
    annual_income: Optional[float] = None
    dividend_yield: Optional[float] = None      # % 税前
    industry: Optional[str] = None
    verdict: Optional[str] = None               # 可持续 / 偏弱 / 不可持续 / 未评估
    score_100: Optional[float] = None
    warnings: List[str] = field(default_factory=list)
    error: Optional[str] = None                 # 该持仓失败原因；不影响其它持仓


@dataclass
class PortfolioSummary:
    positions: List[PortfolioPosition]
    total_market_value: float
    total_annual_income: float
    weighted_yield: Optional[float]             # % 税前
    after_tax_income_10: float                  # 持股 1 月~1 年
    after_tax_income_20: float                  # 持股不足 1 月
    industry_breakdown: List[Tuple[str, float, float]]  # (行业, 年收入, 占组合年收入比例)
    verdict_summary: Dict[str, int]             # 结论 → 只数
    errors: List[str] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for p in self.positions if p.error is None)


def parse_holdings(text: str) -> List[Holding]:
    """解析 `600900:1000,601398:5000`。空项/非法股数抛 ValueError（不静默丢弃）。"""
    holdings: List[Holding] = []
    for chunk in (text or "").replace("，", ",").split(","):
        item = chunk.strip()
        if not item:
            continue
        if ":" in item:
            code, _, shares_text = item.partition(":")
        else:
            parts = item.split()
            if len(parts) != 2:
                raise ValueError(f"持仓格式应为 代码:股数（或「代码 股数」），收到 {item!r}")
            code, shares_text = parts
        code = code.strip()
        if not code:
            raise ValueError(f"持仓缺少股票代码：{item!r}")
        try:
            shares = float(shares_text.strip())
        except ValueError:
            raise ValueError(f"股数不是数字：{item!r}") from None
        if shares <= 0:
            raise ValueError(f"股数必须为正：{item!r}")
        holdings.append(Holding(stock_code=code, shares=shares))
    if not holdings:
        raise ValueError("持仓为空")
    return holdings


def build_position(stock_code: str, shares: float, analysis) -> PortfolioPosition:
    """把一次 run_stock_analysis 的结果折算成持仓行（纯函数，便于测试）。"""
    pos = PortfolioPosition(stock_code=stock_code, shares=shares)
    if analysis is None:
        pos.error = "分析失败（取数失败或代码不存在）"
        return pos

    info = analysis.stock_info
    dividend = analysis.dividend_total
    pos.price = info.current_price if info else None
    if pos.price and pos.price > 0:
        pos.market_value = pos.price * shares

    # 每股分红 = 分红总额 ÷ 总股本（总额法，与单股主卡同口径）
    if info and info.total_shares and info.total_shares > 0 and dividend:
        pos.dividend_per_share = dividend / info.total_shares
        pos.annual_income = pos.dividend_per_share * shares
        if pos.market_value and pos.market_value > 0:
            pos.dividend_yield = pos.annual_income / pos.market_value * 100

    pr = analysis.pr_result
    if pr is not None:
        pos.stock_name = pr.stock_name
        pos.industry = pr.industry
        if pr.pr_warning:
            pos.warnings.append(pr.pr_warning)

    sus = analysis.sustainability
    # #28：低于阈值时 analysis 返回轻量「未评估」结果，持仓表只记录真正评估过的结论
    if sus is not None and sus.triggered:
        pos.verdict = sus.verdict
        pos.score_100 = sus.score_100
        pos.warnings.extend(sus.warning_flags)

    if not pos.dividend_per_share:
        pos.error = "最新完整财年无现金分红"
    return pos


def summarize(positions: Sequence[PortfolioPosition]) -> PortfolioSummary:
    """聚合持仓行（纯函数）。

    口径（2026-11 review 修正）：
    - **市值**包含所有持股，包括「无分红」的持仓——你确实持有它们，
      把它们排除会让市值偏小、加权股息率虚高。
    - **收入**只累加有分红数据的持仓；无分红 = 真实 0 元，靠市值进分母实现稀释。
    - **取数失败**的持仓 market_value / annual_income 均为 None，自然不进任何合计。
    """
    errors = [f"{p.stock_code}: {p.error}" for p in positions if p.error]

    market_value = sum(p.market_value for p in positions if p.market_value)
    income = sum(p.annual_income for p in positions if p.annual_income)

    weighted_yield = (income / market_value * 100) if market_value > 0 else None

    by_industry: Dict[str, float] = {}
    for p in positions:
        if p.annual_income:
            key = p.industry or "未知行业"
            by_industry[key] = by_industry.get(key, 0.0) + p.annual_income
    industry_breakdown = sorted(
        ((name, amount, (amount / income * 100) if income > 0 else 0.0)
         for name, amount in by_industry.items()),
        key=lambda row: row[1], reverse=True)

    verdict_summary: Dict[str, int] = {}
    for p in positions:
        if p.market_value is None:      # 取数失败：连市值都不可知，不计入概览
            continue
        key = p.verdict or "未评估"
        verdict_summary[key] = verdict_summary.get(key, 0) + 1

    return PortfolioSummary(
        positions=list(positions),
        total_market_value=market_value,
        total_annual_income=income,
        weighted_yield=weighted_yield,
        after_tax_income_10=income * 0.9,
        after_tax_income_20=income * 0.8,
        industry_breakdown=industry_breakdown,
        verdict_summary=verdict_summary,
        errors=errors,
    )


def analyze_portfolio(holdings: Sequence[Holding]) -> PortfolioSummary:
    """逐只跑单股分析并聚合；单只异常只记 error，不中断整组。"""
    positions: List[PortfolioPosition] = []
    for holding in holdings:
        try:
            analysis = run_stock_analysis(holding.stock_code)
        except Exception as e:  # 单只异常降级（网络/接口/解析）
            logger.warning("组合分析单只失败 %s: %s", holding.stock_code, e)
            analysis = None
        positions.append(build_position(holding.stock_code, holding.shares, analysis))
    return summarize(positions)
