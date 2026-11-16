"""
市赚率（PR）纯计算模块

所有函数均为纯函数：输入数据 → 输出结果，无网络依赖。
从 pr.py 提取，职责分离。
"""
from typing import Optional, Tuple

# 行业关键词与判定来自 src/industry_policy.py（唯一来源，issue #149）。
# 此处保留同名导入，兼容既有 `from src.pr_calculator import CYCLICAL_INDUSTRIES`。
from src.industry_policy import (  # noqa: E402  （常量区后置导入，保持文件原结构）
    CYCLICAL_INDUSTRIES,
    GROWTH_INDUSTRIES,
    TECH_INDUSTRIES,
    classify_pr_industry,
)


def compute_basic_pr(pe_ttm: Optional[float], roe: Optional[float]) -> Optional[float]:
    """基础市赚率 = PE_TTM / ROE（百分比值直接除）"""
    if pe_ttm is None or roe is None or roe <= 0:
        return None
    return round(pe_ttm / roe, 2)


def compute_corrected_pr(pe_ttm: Optional[float], roe: Optional[float], n_factor: Optional[float]) -> Optional[float]:
    """修正市赚率 = N × PE_TTM / ROE"""
    if pe_ttm is None or roe is None or roe <= 0 or n_factor is None:
        return None
    return round(n_factor * pe_ttm / roe, 2)


def compute_pb_pr(pb: Optional[float], roe: Optional[float]) -> Optional[float]:
    """PB-市赚率 = PB / ROE²（百分比转小数后平方）× 100"""
    if pb is None or roe is None or roe <= 0:
        return None
    roe_decimal = roe / 100.0
    return round(pb / (roe_decimal * roe_decimal) / 100.0, 2)


def compute_n_factor(payout_ratio: Optional[float]) -> Optional[float]:
    """
    股利支付率修正系数 N
    N = 50% / 股利支付率
    边界：payout ≥ 50% → N = 1.0
          payout ≤ 25% → N = 2.0

    出处与依据（数据铁律：如实标注，不编造理论）：
    - 市赚率为雪球用户 ericwarn丁宁（丁宁）发明的估值参数；修正市赚率是作者自认的
      「经验公式」（原话「没什么道理可讲」），非理论推导，无学术/券商研报出处。
    - 50% 基准取贵州茅台多年平均股利支付率（作者以茅台为「赚真钱」估值锚，
      支付率低于 50% 视为「赚假钱」，N 相应放大）。
    """
    if payout_ratio is None:
        return None
    if payout_ratio <= 0:
        return 2.0
    raw = 0.50 / payout_ratio
    return max(1.0, min(2.0, raw))


def compute_tsr(roe_latest: Optional[float], roe_5y_median: Optional[float],
                is_cyclical: bool, real_yield_pct: Optional[float],
                pb: Optional[float]) -> Optional[float]:
    """估值不变下的股东总回报率 = (ROE − 股息率×PB) + 股息率（百分比）。

    假设 PB 一年内不变：ROE − 股息率×PB 为留存收益推动的每股净资产增速
    （此时即价格回报），加股息率为现金分红回报。股息率取真实股息率
    （税前、完整财年总额法）；ROE 周期股取 5 年中位数（对齐 PB-市赚率与
    漏斗③ 先例），中位数缺失回退 roe_latest。ROE 为负（亏损股）照算，
    警示由展示层负责；PB ≤ 0 视为数据异常返回 None。
    """
    roe = select_judge_roe(roe_latest, roe_5y_median, is_cyclical)
    if roe is None or real_yield_pct is None or pb is None or pb <= 0:
        return None
    return round((roe - real_yield_pct * pb) + real_yield_pct, 2)


# 估值四档阈值（回测标定：docs/BACKTEST_REPORT_V3.md）。页面文案与回测脚本
# 引用这里的数字，不再各自硬编码。
PR_UNDERVALUED_MAX = 0.5    # ≤0.5 低估
PR_FAIR_LOW_MAX = 1.0       # 0.5–1.0 合理偏低（回测 L3 的入选线）
PR_FAIR_MAX = 3.0           # 1.0–3.0 合理，>3.0 高估


def classify_valuation(pr: Optional[float]) -> str:
    """估值四档分类。

    阈值基于 PR 历史回测（2016-2024 沪深300，见 docs/BACKTEST_REPORT_V3.md）：
    低估(≤0.5)与合理偏低(0.5-1)超额 +4%+，合理(1-3)接近中性，高估(>3)跑输 5%+。
    故 1.0~3.0 归合理，>3.0 才标高估；PR≤1 视为有超额的低估区间。
    """
    if pr is None:
        return "无法判定"
    if pr <= PR_UNDERVALUED_MAX:
        return "低估"
    if pr <= PR_FAIR_LOW_MAX:
        return "合理偏低"
    if pr <= PR_FAIR_MAX:
        return "合理"
    return "高估"


def classify_industry(industry: str) -> Tuple[bool, bool, bool, str]:
    """根据行业标签判断周期/科技/成长属性，返回 (is_cyclical, is_tech, is_growth, warning)。

    优先级：周期 > 科技 > 成长（重叠时只报最优先的一类）。
    """
    is_cyclical, is_tech, is_growth = classify_pr_industry(industry)

    if is_cyclical:
        warning = "该股属于周期行业，修正市赚率仅供参考；建议优先参考PB-市赚率"
    elif is_tech:
        warning = "该股属于科技行业，修正市赚率可能不适用（科技股常以回购代替分红）"
    elif is_growth:
        warning = "该股属于成长行业，修正市赚率可能不适用（高成长需留存利润，分红率低导致N因子失真）"
    else:
        warning = ""

    return is_cyclical, is_tech, is_growth, warning


# ---------------------------------------------------------------------------
# 判定口径（页面档位 与 选股器漏斗③ 共用，唯一真相）
# ---------------------------------------------------------------------------

def select_judge_roe(roe_latest: Optional[float], roe_5y_median: Optional[float],
                     is_cyclical: bool) -> Optional[float]:
    """判定口径 ROE：周期股用 5 年 ROE 中位数（单年失真平滑），否则最新年报 ROE。"""
    if is_cyclical and roe_5y_median is not None:
        return roe_5y_median
    return roe_latest


def judge_pr(pe_ttm: Optional[float], roe_latest: Optional[float],
             roe_5y_median: Optional[float] = None,
             is_cyclical: bool = False) -> Tuple[Optional[float], Optional[float], str]:
    """选股判定口径 → (判定用 ROE, 基础 PR, 估值档位)。

    基础 PR = PE_TTM / ROE，与 V2 回测 L3（docs/BACKTEST_METHODOLOGY.md §3.5
    「L3: 基础 PR ≤ 1.0」）同口径；0.5/1.0/3.0 的档位阈值也是在这上面标定的。
    修正 PR（N×PE/ROE）不进判据：N 是市赚率作者自认的经验公式，低支付率会把 PR
    放大到 2 倍，与阈值口径不匹配（2026-11 银行/保险整批跌出漏斗③即因此）。

    页面档位（pr.py / site/js）与选股器（screening.py / screener_pr.py）都调本函数，
    保证「页面说低估」等价于「选股器收下」。
    """
    roe = select_judge_roe(roe_latest, roe_5y_median, is_cyclical)
    if pe_ttm is None or roe is None or roe <= 0:
        return roe, None, "无法判定"
    pr = compute_basic_pr(pe_ttm, roe)
    return roe, pr, classify_valuation(pr)
