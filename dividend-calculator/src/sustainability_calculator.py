"""
股息可持续性 — 纯评估器

所有函数均为纯函数：输入解析后的财务数据 → 输出 SustainabilityResult，无网络依赖。
数据源字段（东财 RPT_F10_FINANCE_MAINFINADATA，columns=ALL）已在 sustainability.py
解析为 AnnualFinancial 结构后喂入本模块。

分层级联判断模型：
  Layer 0  行业路由（银行/保险 → 金融分支，否则通用分支）
  Layer 1  致命红旗一票否决（→ 不可持续）
  Layer 2  通用分支六维加权评分 / 金融分支银行专项评分
  Layer 3  情境红旗（不否决，但降一档 + 列入理由）
  结论三档：可持续 / 偏弱 / 不可持续
"""
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .datasource.validation import check_net_profit, check_roe

# ---------------------------------------------------------------------------
# 可配置阈值与权重（集中在此，便于后续调参）
# ---------------------------------------------------------------------------

# 触发判断的股息率下限（税前，百分比）
THRESHOLD_YIELD = 4.0

# 三档分界（加权总分 0~2）
SCORE_SUSTAINABLE = 1.5   # ≥ 该值 → 可持续
SCORE_WEAK = 1.0          # ≥ 该值 → 偏弱；更低 → 不可持续

# Layer 1 致命红旗阈值
FATAL_CF_COVERAGE = 1.0        # 自由现金流覆盖 < 1.0x → 分红 > FCF（银行短路）
FATAL_BANK_CAR = 10.5          # 银行总资本充足率 < 10.5% → 监管约束分红，致命红线

# Layer 2 六维评分阈值（通用分支）
# 现金流覆盖（经营现金流/分红）
DIM_CF_COVERAGE = (1.0, 1.5)
# 股利支付率
DIM_PAYOUT = (0.60, 0.80)
# ROE（百分比）
DIM_ROE = (10.0, 15.0)
# 资产负债率（百分比）
DIM_DEBT_RATIO = (0.50, 0.70)
# 利息保障倍数（x）
DIM_INTEREST_COVERAGE = (3.0, 5.0)
# 连续分红年数
DIM_CONSECUTIVE_YEARS = (3, 10)
# 曾削减判定窗口（年）：仅考察最新财年往前 CUT_WINDOW_YEARS 年内的相邻年降幅。
# 10 年以上久远的波动（如行业早期调整）对当前分红可持续性无参考价值。
CUT_WINDOW_YEARS = 10

# 六维权重（合计 1.0）
WEIGHTS = {
    "cf_coverage": 0.25,
    "payout": 0.20,
    "profitability": 0.15,
    "balance_sheet": 0.15,
    "dividend_history": 0.15,
    "industry": 0.10,
}

# Layer 3 情境红旗阈值
WARN_PAYOUT_OVER_100 = 1.0        # 股利支付率 > 100%（成熟期股结构性偏高属健康信号，仅警示）
WARN_PRICE_DROP = -0.30        # 近1年股价跌幅 < -30%
WARN_SPECIAL_DIV_MULTIPLE = 2.0  # 当年分红 > 近3年均值 × 2.0（避免早期低基数拉偏误判稳定增长股）
WARN_HOLDING_CONCENTRATION = 0.50  # 前十大持股 > 50%
WARN_HIGH_PAYOUT = 0.80        # 高派息门槛（情境画像用）

# 银行/保险行业关键词
FINANCE_INDUSTRIES = ("银行", "保险")


# ---------------------------------------------------------------------------
# 输入数据结构（纯函数消费，与数据源字段名解耦）
# ---------------------------------------------------------------------------

@dataclass
class AnnualFinancial:
    """单年度财务数据（来自东财财务接口某一年的年报行，解析后）。

    所有金额单位：元；比率为小数（如 0.52 表示 52%）或百分比（ROE/增长率按东财原值，百分数）。
    缺失字段为 None。
    """
    year: int
    net_profit: Optional[float]            # 归母净利润（元）
    net_profit_yoy: Optional[float]        # 净利润同比（百分数，如 30.5 表示 +30.5%）
    operating_cf: Optional[float]          # 经营活动现金流净额（元）
    investing_cf: Optional[float]          # 投资活动现金流净额（元，通常为负；含金融投资，非纯 CAPEX）
    total_assets: Optional[float]          # 总资产（元）
    total_liabilities: Optional[float]     # 总负债（元）
    interest_debt_ratio: Optional[float]   # 有息负债率（百分数）
    interest_coverage: Optional[float]     # 利息保障倍数（x）
    roe: Optional[float]                   # ROE（百分数）
    # 银行/保险专项（普通股为 None）
    capital_adequacy_ratio: Optional[float]   # 资本充足率（百分数）
    net_interest_margin: Optional[float]      # 净息差（百分数）
    npl_ratio: Optional[float]                # 不良贷款率（百分数）
    provision_coverage: Optional[float]       # 拨贷比（LOAN_PROVISION_RATIO，百分数；东财无拨备覆盖率字段，用拨贷比近似）
    # 资本开支（购建固定资产/无形资产，元，正数）；来自现金流量表，缺失则 FCF 降级用 investing_cf
    capex: Optional[float] = None
    # 资产负债率（百分数）；东财无直接字段，靠 debt_ratio_decimal() 用 LIABILITY/TOTAL_ASSETS_PK 推算
    debt_ratio: Optional[float] = None

    def debt_ratio_decimal(self) -> Optional[float]:
        """资产负债率统一转为小数（0~1）。优先取 debt_ratio（东财百分数），缺失用总负债/总资产推算。"""
        if self.debt_ratio is not None:
            return self.debt_ratio / 100.0
        if self.total_assets and self.total_liabilities is not None and self.total_assets > 0:
            return self.total_liabilities / self.total_assets
        return None


@dataclass
class DividendHistory:
    """历史分红聚合（来自分红明细按年聚合后）。"""
    consecutive_years: int                 # 连续分红年数（截至最新财年）
    ever_cut: bool                         # 可得历史内是否曾削减/中断
    latest_year_amount: Optional[float]    # 最新财年分红总额（元）
    history_mean_amount: Optional[float]   # 历史年均分红总额（元，不含最新年）
    history_3y_mean: Optional[float] = None  # 近3年（最新年前3年）年均分红（元）；突击分红判断用


@dataclass
class DerivedMetrics:
    """由 AnnualFinancial + 分红总额派生的衍生指标集合（避免多函数重复传同一组值）。"""
    payout_ratio: Optional[float]          # 股利支付率（小数）
    fcf: Optional[float]                   # 自由现金流（元）
    fcf_coverage: Optional[float]          # FCF 覆盖倍数
    cf_coverage: Optional[float]           # 经营现金流覆盖倍数
    debt_ratio_dec: Optional[float]        # 资产负债率（小数）

    @classmethod
    def from_inputs(cls, latest: AnnualFinancial, dividend_total: Optional[float]) -> "DerivedMetrics":
        payout_ratio = compute_payout_ratio(dividend_total, latest.net_profit)
        fcf = compute_free_cash_flow(latest.operating_cf, latest.investing_cf, latest.capex)
        return cls(
            payout_ratio=payout_ratio,
            fcf=fcf,
            fcf_coverage=compute_fcf_coverage(fcf, dividend_total),
            cf_coverage=compute_cf_coverage(latest.operating_cf, dividend_total),
            debt_ratio_dec=latest.debt_ratio_decimal(),
        )


@dataclass
class SustainabilityResult:
    """股息可持续性判断结果。"""
    triggered: bool                          # 是否因股息率 > 阈值触发判断
    verdict: str                             # 可持续 / 偏弱 / 不可持续 / 未评估
    score: Optional[float] = None            # 加权总分 0~2（红旗否决/未触发时为 None）
    fatal_flags: List[str] = field(default_factory=list)    # 致命红旗（带数值理由）
    warning_flags: List[str] = field(default_factory=list)  # 情境红旗（带数值理由）
    dimension_scores: Dict[str, int] = field(default_factory=dict)  # 各维度 0/1/2
    metrics: Dict[str, Optional[float]] = field(default_factory=dict)  # 支撑数据
    branch: str = "general"                  # general / finance
    notes: List[str] = field(default_factory=list)  # 缺失数据说明
    latest_annual_year: Optional[int] = None  # 最新年报财年（数据新鲜度判定用，#13）


# ---------------------------------------------------------------------------
# 衍生指标计算（纯函数）
# ---------------------------------------------------------------------------

def compute_free_cash_flow(operating_cf: Optional[float],
                           investing_cf: Optional[float],
                           capex: Optional[float] = None) -> Optional[float]:
    """自由现金流 FCF = 经营现金流净额 − 资本开支(CAPEX)。

    口径优先级：
      1. 有 CAPEX（来自现金流量表 CONSTRUCT_LONG_ASSET）→ FCF = CFO − CAPEX（正确口径，
         仅扣维持竞争力的必需投资，不含买理财等金融投资）
      2. 无 CAPEX 但有投资现金流净额 → 降级 FCF = CFO + investing_cf（investing_cf 为负数，
         含全部投资活动，会把买理财误算成投资，低估 FCF，仅作兜底）

    investing_cf 含金融投资/并购等非经营投资，会系统性低估 FCF（如伊利买理财导致
    FCF 被算成负值），故有 CAPEX 时务必用 CAPEX 口径。
    """
    if operating_cf is None:
        return None
    if capex is not None:
        return operating_cf - capex
    if investing_cf is not None:
        return operating_cf + investing_cf
    return None


def _safe_ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    """安全比率：分子或分母缺失、或分母 ≤ 0 时返回 None。"""
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator


def compute_cf_coverage(operating_cf: Optional[float],
                        dividend_total: Optional[float]) -> Optional[float]:
    """现金流覆盖倍数 = 经营现金流净额 / 分红总额。"""
    return _safe_ratio(operating_cf, dividend_total)


def compute_fcf_coverage(fcf: Optional[float],
                         dividend_total: Optional[float]) -> Optional[float]:
    """自由现金流覆盖倍数 = FCF / 分红总额。"""
    return _safe_ratio(fcf, dividend_total)


def compute_payout_ratio(dividend_total: Optional[float],
                         net_profit: Optional[float]) -> Optional[float]:
    """股利支付率 = 分红总额 / 净利润（小数）。无分红或净利润 ≤ 0 不计算。"""
    # 分子 dividend_total=0 时应返回 None（无分红），故单独守卫而非直接用 _safe_ratio
    if not dividend_total:
        return None
    return _safe_ratio(dividend_total, net_profit)


# ---------------------------------------------------------------------------
# Layer 1：致命红旗（一票否决）
# ---------------------------------------------------------------------------

def check_fatal_flags(payout_ratio: Optional[float],
                      fcf_coverage: Optional[float],
                      operating_cf: Optional[float],
                      net_profit: Optional[float],
                      dividend_total: Optional[float],
                      is_bank: bool = False) -> List[str]:
    """返回致命红旗理由列表（空则无致命问题）。

    银行/保险（is_bank=True）短路现金流类红旗：
      银行经营CF含存贷款净变动、扩张期为负属常态；FCF 概念对银行不适用
      （其"投资"是放贷而非固定资产）。故仅保留"净利润<0却分红"。
    """
    flags: List[str] = []
    has_div = bool(dividend_total and dividend_total > 0)

    # 1. 股利支付率 > 100% —— 移至 Layer 3 情境红旗（成熟期股结构性>100%属健康，见 T2）
    # （此处不再判否决）

    if not is_bank:
        # 2. 自由现金流覆盖 < 1.0x（分红 > FCF）
        if fcf_coverage is not None and fcf_coverage < FATAL_CF_COVERAGE:
            flags.append(
                f"自由现金流覆盖 {fcf_coverage:.2f}x < 1.0x，分红金额超过自由现金流"
            )

        # 3. 经营现金流为负却分红
        if has_div and operating_cf is not None and operating_cf < 0:
            flags.append("经营现金流为负却仍派发现金分红")

    # 4. 净利润为负仍分红（所有行业适用，含银行）
    if has_div and net_profit is not None and net_profit < 0:
        flags.append("净利润为负（亏损）却仍派发现金分红")

    return flags


# ---------------------------------------------------------------------------
# Layer 2：维度评分
# ---------------------------------------------------------------------------

def _score_band(value: Optional[float], low: float, high: float) -> Optional[int]:
    """三档评分：value < low → 0，[low, high) → 1，≥ high → 2。缺失 → None。"""
    if value is None:
        return None
    if value < low:
        return 0
    if value < high:
        return 1
    return 2


def _score_inverted(value: Optional[float], low: float, high: float) -> Optional[int]:
    """反向三档（值越低越好，如支付率/负债率）：> high → 0，(low, high] → 1，≤ low → 2。"""
    if value is None:
        return None
    if value > high:
        return 0
    if value > low:
        return 1
    return 2


def score_dimensions(latest: AnnualFinancial,
                     history: DividendHistory,
                     payout_ratio: Optional[float],
                     cf_coverage: Optional[float],
                     is_cyclical: bool,
                     is_defensive: bool) -> Dict[str, Optional[int]]:
    """六维评分，返回 {维度名: 0/1/2}。缺失维度为 None，不计入加权（见 _weighted_score）。"""
    scores: Dict[str, Optional[int]] = {}

    # 1. 现金流覆盖
    scores["cf_coverage"] = _score_band(cf_coverage, *DIM_CF_COVERAGE) if cf_coverage is not None else None

    # 2. 股利支付率（反向：越低越好）
    scores["payout"] = _score_inverted(payout_ratio, *DIM_PAYOUT) if payout_ratio is not None else None

    # 3. 盈利稳定性：ROE 水平为主，利润下滑扣分
    profit_score = None
    if latest.roe is not None:
        if latest.roe < DIM_ROE[0]:
            profit_score = 0
        elif latest.roe < DIM_ROE[1]:
            profit_score = 1
        else:
            profit_score = 2
        # 净利润同比为负且幅度大，降一档
        if latest.net_profit_yoy is not None and latest.net_profit_yoy < 0 and profit_score > 0:
            profit_score -= 1
    scores["profitability"] = profit_score

    # 4. 资产负债表：负债率 + 利息覆盖（取两者较低档）
    debt_dec = latest.debt_ratio_decimal()
    debt_score = _score_inverted(debt_dec, *DIM_DEBT_RATIO) if debt_dec is not None else None
    int_score = _score_band(latest.interest_coverage, *DIM_INTEREST_COVERAGE) if latest.interest_coverage is not None else None
    bs_candidates = [s for s in (debt_score, int_score) if s is not None]
    scores["balance_sheet"] = min(bs_candidates) if bs_candidates else None

    # 5. 分红历史
    if history.ever_cut:
        scores["dividend_history"] = 0
    elif history.consecutive_years >= DIM_CONSECUTIVE_YEARS[1]:
        scores["dividend_history"] = 2
    elif history.consecutive_years >= DIM_CONSECUTIVE_YEARS[0]:
        scores["dividend_history"] = 1
    else:
        scores["dividend_history"] = 0

    # 6. 行业属性
    if is_cyclical:
        scores["industry"] = 0
    elif is_defensive:
        scores["industry"] = 2
    else:
        scores["industry"] = 1

    return scores


def score_finance_branch(latest: AnnualFinancial) -> Dict[str, int]:
    """金融分支：银行专项评分（资本充足率/净息差/不良率/拨备）。

    各项有值才计分；全部缺失返回空 dict（由调用方降级）。
    """
    scores: Dict[str, int] = {}
    # 资本充足率（>10.5 满意/8~10.5 一般/<8 危险，监管红线 8%）
    if latest.capital_adequacy_ratio is not None:
        car = latest.capital_adequacy_ratio
        scores["capital_adequacy"] = 2 if car >= 12 else (1 if car >= 10.5 else 0)
    # 净息差（>1.8 健康/1.4~1.8 警戒/<1.4 危险，2026 行业冰点约 1.4）
    if latest.net_interest_margin is not None:
        nim = latest.net_interest_margin
        scores["net_interest_margin"] = 2 if nim >= 1.8 else (1 if nim >= 1.4 else 0)
    # 不良率（<1 健康/1~2 警戒/>2 危险）
    if latest.npl_ratio is not None:
        npl = latest.npl_ratio
        scores["npl"] = 2 if npl < 1.0 else (1 if npl < 2.0 else 0)
    # 拨贷比（LOAN_PROVISION_RATIO，监管要求1.5-2.5%；东财无拨备覆盖率字段，用拨贷比近似）
    # ≥2.5 健康 / 2.0-2.5 警戒 / <2.0 危险
    if latest.provision_coverage is not None:
        pc = latest.provision_coverage
        scores["provision"] = 2 if pc >= 2.5 else (1 if pc >= 2.0 else 0)
    return scores


def _weighted_score(scores: Dict[str, Optional[int]], weights: Dict[str, float]) -> Tuple[Optional[float], float]:
    """按权重加权。缺失维度按 0 分计入（不再归一化分摊——避免数据稀疏虚高得分，T4）。

    返回 (score, missing_weight_ratio)：
      - score = ∑(分数×权重) / ∑(全部权重)，缺失维度贡献 0 分
      - missing_weight_ratio = 缺失维度权重和 / 总权重
    全部维度缺失返回 (None, 1.0)。
    """
    total_w = sum(weights.get(k, 0) for k in scores)
    if total_w <= 0:
        return None, 1.0
    missing_w = sum(weights.get(k, 0) for k, v in scores.items() if v is None)
    weighted = sum((scores[k] or 0) * weights.get(k, 0) for k in scores)
    return weighted / total_w, missing_w / total_w


# ---------------------------------------------------------------------------
# Layer 3：情境红旗（不否决，降一档）
# ---------------------------------------------------------------------------

def check_warning_flags(latest: AnnualFinancial,
                        history: DividendHistory,
                        is_cyclical: bool,
                        price_change_1y: Optional[float],
                        top10_holding: Optional[float],
                        payout_ratio: Optional[float],
                        debt_ratio_dec: Optional[float],
                        cf_coverage: Optional[float],
                        is_bank: bool = False) -> List[str]:
    """返回情境红旗理由列表。"""
    flags: List[str] = []

    # 股利支付率 > 100%（成熟期股结构性偏高属健康信号，单年不否决，仅警示）
    if payout_ratio is not None and payout_ratio > WARN_PAYOUT_OVER_100:
        flags.append(
            f"股利支付率 {payout_ratio*100:.1f}% > 100%，分红超过当年净利润"
            f"（成熟期/高折旧股常见，关注是否动用留存收益）"
        )

    # 被动高股息：股价近1年跌幅 > 30%
    if price_change_1y is not None and price_change_1y < WARN_PRICE_DROP:
        flags.append(
            f"近1年股价跌幅 {price_change_1y*100:.1f}%，高股息率可能源于股价下跌（分母效应）"
        )

    # 特别/突击分红：当年分红远超近3年均值（T3：用近3年而非全历史，避免稳定增长股被误伤）
    history_baseline = history.history_3y_mean if history.history_3y_mean is not None else history.history_mean_amount
    if (history.latest_year_amount and history_baseline
            and history_baseline > 0
            and history.latest_year_amount > history_baseline * WARN_SPECIAL_DIV_MULTIPLE):
        flags.append(
            f"最新财年分红 {history.latest_year_amount/1e8:.2f}亿元 "
            f"远超近3年均值 {history_baseline/1e8:.2f}亿元，疑似特别/突击分红"
        )

    # 一股独大 + 高派息（需大股东数据）
    if (top10_holding is not None and top10_holding > WARN_HOLDING_CONCENTRATION
            and payout_ratio is not None and payout_ratio > WARN_HIGH_PAYOUT):
        flags.append(
            f"前十大持股 {top10_holding*100:.1f}% 且支付率 {payout_ratio*100:.1f}%，"
            f"疑似向大股东输血式分红"
        )

    # 周期股顶部信号：强周期 + 利润已拐头 + 高派息
    if (is_cyclical and latest.net_profit_yoy is not None and latest.net_profit_yoy < 0
            and payout_ratio is not None and payout_ratio > WARN_HIGH_PAYOUT):
        flags.append(
            f"属周期行业且净利润同比 {latest.net_profit_yoy:.1f}% 已拐头，"
            f"支付率仍 {payout_ratio*100:.1f}%，警惕周期顶点高分红陷阱"
        )

    # 证监会红线画像：高负债 + 弱现金流 + 高分红（银行跳过——负债率对银行无意义，T7）
    if not is_bank:
        weak_cf = cf_coverage is not None and cf_coverage < DIM_CF_COVERAGE[1]
        high_debt = debt_ratio_dec is not None and debt_ratio_dec > DIM_DEBT_RATIO[1]
        high_payout = payout_ratio is not None and payout_ratio > WARN_HIGH_PAYOUT
        if weak_cf and high_debt and high_payout:
            flags.append("高负债 + 弱现金流覆盖 + 高派息，符合监管重点关注的'透支式分红'画像")

    return flags


# ---------------------------------------------------------------------------
# 编排：assess_sustainability
# ---------------------------------------------------------------------------

# 防御性行业（现金流稳定，高分红可持续性更强）
DEFENSIVE_INDUSTRIES = (
    "公用事业", "电力", "水务", "燃气", "高速公路", "铁路", "港口", "机场",
    "食品饮料", "白酒", "乳品", "家电", "医药", "超市", "运营商", "电信",
)

CYCLICAL_INDUSTRIES = (
    "煤炭", "钢铁", "有色金属", "石油", "化工", "航运", "建材",
    "水泥", "玻璃", "造纸", "养殖", "房地产", "工程机械", "船舶",
    "化肥", "农药", "化纤", "橡胶", "塑料",
)


def _classify_industry(industry: str) -> Tuple[bool, bool, bool]:
    """返回 (is_bank, is_cyclical, is_defensive)。"""
    is_bank = any(kw in industry for kw in FINANCE_INDUSTRIES)
    is_cyclical = any(kw in industry for kw in CYCLICAL_INDUSTRIES)
    is_defensive = any(kw in industry for kw in DEFENSIVE_INDUSTRIES)
    return is_bank, is_cyclical, is_defensive


def _score_by_branch(latest: AnnualFinancial, history: DividendHistory,
                     metrics: DerivedMetrics, is_bank: bool, is_cyclical: bool,
                     is_defensive: bool) -> Tuple[Dict[str, Optional[int]], Optional[float], float]:
    """Layer 2 分支评分：银行走金融专项（等权平均），否则通用六维加权。

    返回 (dimension_scores, score, missing_weight_ratio)。
    金融分支只计入有值的专项项，missing_ratio=0；通用分支按 _weighted_score 缺失惩罚。
    """
    if is_bank:
        dim_scores = score_finance_branch(latest)
        if dim_scores:
            score = sum(dim_scores.values()) / len(dim_scores)
            return dim_scores, score, 0.0
        # 银行专项全缺失 → 降级（调用方负责记 note/branch）
        dim_scores = score_dimensions(latest, history, metrics.payout_ratio, metrics.cf_coverage,
                                      is_cyclical, is_defensive)
        score, missing = _weighted_score(dim_scores, WEIGHTS)
        return dim_scores, score, missing
    dim_scores = score_dimensions(latest, history, metrics.payout_ratio, metrics.cf_coverage,
                                  is_cyclical, is_defensive)
    score, missing = _weighted_score(dim_scores, WEIGHTS)
    return dim_scores, score, missing


def _verdict_from_score(score: Optional[float], warning_flags: List[str]) -> str:
    """由加权总分映射三档结论；有情境红旗则降一档。score 为 None 时判偏弱。"""
    if score is None:
        verdict = "偏弱"
    elif score >= SCORE_SUSTAINABLE:
        verdict = "可持续"
    elif score >= SCORE_WEAK:
        verdict = "偏弱"
    else:
        verdict = "不可持续"
    if warning_flags:
        if verdict == "可持续":
            verdict = "偏弱"
        elif verdict == "偏弱":
            verdict = "不可持续"
    return verdict


def assess_sustainability(*,
                          dividend_yield_before_tax: Optional[float],
                          dividend_total: Optional[float],
                          latest: Optional[AnnualFinancial],
                          history: Optional[DividendHistory],
                          industry: str = "",
                          price_change_1y: Optional[float] = None,
                          top10_holding: Optional[float] = None,
                          roe_series: Optional[List[float]] = None,
                          current_year: Optional[int] = None) -> SustainabilityResult:
    """股息可持续性主评估入口（纯函数）。

    Args:
        dividend_yield_before_tax: 税前股息率（百分数）
        dividend_total: 最新财年现金分红总额（元）
        latest: 最新年报的 AnnualFinancial（可缺失）
        history: 历史分红聚合 DividendHistory（可缺失）
        industry: 行业字符串
        price_change_1y: 近1年股价涨跌幅（小数，如 -0.3）
        top10_holding: 前十大股东合计持股比例（小数，如 0.6）
        roe_series: 多年 ROE 序列（百分数，最新在前），用于稳定性判断
        current_year: 当前年份，用于数据新鲜度判定（#13）；None 时不判 stale
    """
    # 未达触发阈值 → 不评估
    if dividend_yield_before_tax is None or dividend_yield_before_tax <= THRESHOLD_YIELD:
        return SustainabilityResult(triggered=False, verdict="未评估", score=None,
                                    notes=["股息率未超过阈值，未做可持续性评估"])

    result = SustainabilityResult(triggered=True, verdict="不可持续")

    # 行业路由
    is_bank, is_cyclical, is_defensive = _classify_industry(industry)
    result.branch = "finance" if is_bank else "general"
    result.metrics["is_bank"] = 1.0 if is_bank else 0.0
    result.metrics["is_cyclical"] = 1.0 if is_cyclical else 0.0

    # 数据缺失：latest 缺失则降级评估
    if latest is None:
        result.notes.append("财务数据缺失，无法评估可持续性")
        result.fatal_flags.append("缺少财务数据，无法判断分红是否可持续")
        return result

    # 数据新鲜度判定（#13）：标注而非改判
    result.latest_annual_year = latest.year
    if current_year is not None:
        stale_note = _staleness_note(latest.year, current_year)
        if stale_note:
            result.notes.append(stale_note)

    # 衍生指标（封装为 DerivedMetrics，避免在各 check 函数间重复传同一组值）
    metrics = DerivedMetrics.from_inputs(latest, dividend_total)
    result.metrics.update({
        "payout_ratio": metrics.payout_ratio,
        "operating_cf": latest.operating_cf,
        "capex": latest.capex,
        "free_cash_flow": metrics.fcf,
        "fcf_coverage": metrics.fcf_coverage,
        "cf_coverage": metrics.cf_coverage,
        "debt_ratio": latest.debt_ratio_decimal() if latest.debt_ratio is None else latest.debt_ratio,
        "interest_coverage": latest.interest_coverage,
        "roe_latest": latest.roe,
        "net_profit": latest.net_profit,
        "net_profit_yoy": latest.net_profit_yoy,
        "capital_adequacy": latest.capital_adequacy_ratio,
        "net_interest_margin": latest.net_interest_margin,
        "npl_ratio": latest.npl_ratio,
        "provision_coverage": latest.provision_coverage,
    })

    # 分红历史缺失补默认
    if history is None:
        history = DividendHistory(consecutive_years=0, ever_cut=False,
                                  latest_year_amount=dividend_total, history_mean_amount=None)
        result.notes.append("分红历史缺失，分红历史维度按 0 分计")

    # 数据完整性软校验（审查 #4）：越界只追加 notes，不否决结果
    for w in (
        check_net_profit(latest.net_profit),
        check_roe(latest.roe),
    ):
        if w:
            result.notes.append(w)

    # Layer 2：分支评分（先算，供展示；红旗否决时也有维度分）
    dim_scores, score, missing_ratio = _score_by_branch(latest, history, metrics, is_bank, is_cyclical, is_defensive)
    is_fallback = False
    # 银行走金融分支但专项全缺失时 _score_by_branch 内部已降级通用；此处补记 note/branch
    if is_bank and not any(k in dim_scores for k in ("capital_adequacy", "npl")):
        result.notes.append("银行专项指标（资本充足率/净息差/不良率）缺失，按通用指标评估")
        result.branch = "general-fallback"
        is_fallback = True
        # T7：银行 fallback 时屏蔽资产负债表维度（银行天然 90%+，通用阈值必踩坑）
        dim_scores["balance_sheet"] = None
        score, missing_ratio = _weighted_score(dim_scores, WEIGHTS)
    # T4：数据缺失惩罚——缺失权重 ≥ 30% 标低置信（score 已含缺失维度计 0 分）
    if missing_ratio >= 0.30 and score is not None:
        result.notes.append(f"财务数据缺失较多（{missing_ratio*100:.0f}%），结论置信度偏低")
        result.metrics["missing_weight_ratio"] = missing_ratio
    result.dimension_scores = {k: (v if v is not None else 0) for k, v in dim_scores.items()}
    result.metrics["consecutive_dividend_years"] = float(history.consecutive_years)
    result.metrics["ever_cut"] = 1.0 if history.ever_cut else 0.0

    # Layer 1：致命红旗（维度分已算好，否决时仍展示）
    result.fatal_flags = check_fatal_flags(
        metrics.payout_ratio, metrics.fcf_coverage, latest.operating_cf, latest.net_profit, dividend_total,
        is_bank=is_bank,
    )
    # 银行/保险：资本充足率 < 10.5% 是监管约束分红的硬红线，单列致命否决
    if is_bank and latest.capital_adequacy_ratio is not None and latest.capital_adequacy_ratio < FATAL_BANK_CAR:
        result.fatal_flags.append(
            f"资本充足率 {latest.capital_adequacy_ratio:.2f}% < {FATAL_BANK_CAR}%，触及监管约束，分红受限"
        )
    if result.fatal_flags:
        result.score = 0.0
        return result

    if score is None:
        result.notes.append("有效评分维度不足，结论仅供参考")
        result.score = None
        result.verdict = "偏弱"
        return result

    result.score = round(score, 3)

    # Layer 3：情境红旗 → 降一档（银行跳过证监会画像，T7）
    result.warning_flags = check_warning_flags(
        latest, history, is_cyclical, price_change_1y, top10_holding,
        metrics.payout_ratio, metrics.debt_ratio_dec, metrics.cf_coverage, is_bank=is_bank
    )
    result.verdict = _verdict_from_score(score, result.warning_flags)
    return result


# ---------------------------------------------------------------------------
# 结论说明（对齐 JS explainSustainability，双端逐字一致）
# ---------------------------------------------------------------------------

def _r1(v: float) -> float:
    """1 位小数 half-up（对齐 JS Math.round(v*10)/10）"""
    return math.floor(v * 10 + 0.5) / 10


def _staleness_note(latest_annual_year: int, current_year: int) -> Optional[str]:
    """数据新鲜度判定（#13）：最新年报财年比当前年早 1 年以上 → 陈旧。

    A 股年报法定披露截止次年 4 月 30 日：当前 2026 年时正常最新年报 = 2025
    （2026-04 前披露）；若停在 2024 或更早 → 超 18 个月未更新，标注时效有限。
    仅标注不改判（陈旧可能是公司真实状态，静默改结论违反数据铁律）。
    """
    if latest_annual_year < current_year - 1:
        return (f"财务数据截至 {latest_annual_year} 年报，已超过 18 个月未更新，"
                "结论时效性有限")
    return None


def _r2(v: float) -> float:
    """2 位小数 half-up（对齐 JS Math.round(v*100)/100）"""
    return math.floor(v * 100 + 0.5) / 100


def _pct1(v: float) -> str:
    """小数 → 百分数 1 位小数"""
    return f"{_r1(v * 100):.1f}"


def _yoy_str(v: float) -> str:
    """净利润同比带符号"""
    return ("+" if v >= 0 else "") + f"{_r1(v):.1f}" + "%"


SUS_EXPLAIN_DIMS = ["cf_coverage", "payout", "profitability", "balance_sheet",
                    "dividend_history", "industry"]
SUS_EXPLAIN_DIMS_FIN = ["capital_adequacy", "net_interest_margin", "npl", "provision"]


def _weak_dim_text(k: str, s: int, m: dict) -> Optional[str]:
    if k == "cf_coverage":
        if m.get("cf_coverage") is None:
            return None
        return (f"现金流覆盖 {_r2(m['cf_coverage']):.2f} 倍" +
                ("，分红花的钱超过真正赚到的现金，可能吃老本" if s == 0 else "，刚好够分红，余粮不多"))
    if k == "payout":
        if m.get("payout_ratio") is None:
            return None
        return (f"股利支付率 {_pct1(m['payout_ratio'])}%" +
                ("，利润几乎全拿去分红了" if s == 0 else "，分红比例偏高"))
    if k == "profitability":
        if m.get("roe_latest") is None:
            return None
        yoy = m.get("net_profit_yoy")
        return (f"盈利稳定性：ROE {_r2(m['roe_latest']):.2f}%、净利润同比 {_yoy_str(0 if yoy is None else yoy)}" +
                ("，盈利在下滑，分红难持续" if s == 0 else "，盈利一般"))
    if k == "balance_sheet":
        if m.get("debt_ratio") is None:
            return None
        return (f"资产负债率 {_pct1(m['debt_ratio'])}%" +
                ("，负债偏高，财务压力大" if s == 0 else "，负债水平一般"))
    if k == "dividend_history":
        if m.get("consecutive_dividend_years") is None:
            return None
        # 0 分可能来自"近10年内曾削减"或"连续年数过短"，文案按原因区分（s==0 时）
        if s == 0:
            if m.get("ever_cut"):
                return (f"连续分红 {int(m['consecutive_dividend_years'])} 年，但近 10 年内曾削减分红，"
                        "历史稳定性存疑")
            return f"连续分红仅 {int(m['consecutive_dividend_years'])} 年，历史较短"
        return f"连续分红 {int(m['consecutive_dividend_years'])} 年，尚不算长期稳定"
    if k == "industry":
        if s == 0:
            return "属强周期行业，盈利随景气波动大，高分红难年年保证"
        return None
    if k == "capital_adequacy":
        if m.get("capital_adequacy") is None:
            return None
        return (f"资本充足率 {_r2(m['capital_adequacy']):.2f}%" +
                ("，低于监管红线，分红受限" if s == 0 else "，一般"))
    if k == "net_interest_margin":
        if m.get("net_interest_margin") is None:
            return None
        return (f"净息差 {_r2(m['net_interest_margin']):.2f}%" +
                ("，盈利承压" if s == 0 else "，一般"))
    if k == "npl":
        if m.get("npl_ratio") is None:
            return None
        return (f"不良贷款率 {_r2(m['npl_ratio']):.2f}%" +
                ("，资产质量堪忧" if s == 0 else "，偏高"))
    if k == "provision":
        if m.get("provision_coverage") is None:
            return None
        return (f"拨贷比 {_r2(m['provision_coverage']):.2f}%" +
                ("，风险缓冲不足" if s == 0 else "，一般"))
    return None


def _strong_dim_text(k: str, m: dict) -> Optional[str]:
    if k == "cf_coverage":
        if m.get("cf_coverage") is None:
            return None
        return f"现金流覆盖 {_r2(m['cf_coverage']):.2f} 倍（充裕）"
    if k == "payout":
        if m.get("payout_ratio") is None:
            return None
        return f"支付率 {_pct1(m['payout_ratio'])}%（健康）"
    if k == "profitability":
        if m.get("roe_latest") is None:
            return None
        yoy = m.get("net_profit_yoy")
        return (f"盈利稳健（ROE {_r2(m['roe_latest']):.2f}%、"
                f"净利润同比 {_yoy_str(0 if yoy is None else yoy)}%）")
    if k == "balance_sheet":
        if m.get("debt_ratio") is None:
            return None
        return f"资产负债率 {_pct1(m['debt_ratio'])}%（稳健）"
    if k == "dividend_history":
        if m.get("consecutive_dividend_years") is None:
            return None
        return f"连续分红 {int(m['consecutive_dividend_years'])} 年（稳定）"
    if k == "industry":
        return "属防御/成熟行业（盈利稳定）"
    if k == "capital_adequacy":
        if m.get("capital_adequacy") is None:
            return None
        return f"资本充足率 {_r2(m['capital_adequacy']):.2f}%（充足）"
    if k == "net_interest_margin":
        if m.get("net_interest_margin") is None:
            return None
        return f"净息差 {_r2(m['net_interest_margin']):.2f}%（健康）"
    if k == "npl":
        if m.get("npl_ratio") is None:
            return None
        return f"不良贷款率 {_r2(m['npl_ratio']):.2f}%（很低）"
    if k == "provision":
        if m.get("provision_coverage") is None:
            return None
        return f"拨贷比 {_r2(m['provision_coverage']):.2f}%（充足）"
    return None


def explain_sustainability(result: "SustainabilityResult") -> List[str]:
    """可持续性结论白话说明：首行结论+一句话总结，随后分条理由
    （致命红旗 → 警示红旗 → 弱维度 → 优势项），末尾缺失数据说明。
    未触发 / 未评估时返回空列表。"""
    if not result.triggered or result.verdict == "未评估":
        return []

    lines: List[str] = []
    if result.verdict == "不可持续":
        head = ("存在致命问题，当前分红水平大概率维持不下去"
                if result.fatal_flags else "分红金额与盈利/现金流明显不匹配，长期难以为继")
    elif result.verdict == "偏弱":
        head = "分红有一定基础，但存在隐忧，长期分红能力可能打折扣"
    else:
        head = ("银行核心经营指标全部健康，分红能力扎实"
                if result.branch == "finance" else "盈利与现金流足以支撑当前分红")
    lines.append(f"结论：{result.verdict} — {head}")

    n = 1
    m = result.metrics or {}
    for f in (result.fatal_flags or [])[:3]:
        lines.append(f"{n}. {f}")
        n += 1

    if not (result.fatal_flags or []):
        for w in (result.warning_flags or [])[:3]:
            lines.append(f"{n}. {w}")
            n += 1

        order = SUS_EXPLAIN_DIMS_FIN if result.branch == "finance" else SUS_EXPLAIN_DIMS
        weak: List[Tuple[int, str]] = []
        strong: List[str] = []
        for k in order:
            s = (result.dimension_scores or {}).get(k)
            if s is None:
                continue
            if s <= 1:
                weak.append((s, k))
            else:
                strong.append(k)
        weak.sort(key=lambda x: x[0])
        for s, k in weak[:3]:
            t = _weak_dim_text(k, s, m)
            if t:
                lines.append(f"{n}. {t}")
                n += 1
        if result.verdict != "不可持续":
            st = [_strong_dim_text(k, m) for k in strong[:2]]
            st = [t for t in st if t]
            if st:
                lines.append(f"{n}. 优势项：{'、'.join(st)}")
                n += 1
    if result.notes:
        lines.append("注：" + "；".join(result.notes))
    return lines
