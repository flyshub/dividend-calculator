"""
市赚率（PR）纯计算模块

所有函数均为纯函数：输入数据 → 输出结果，无网络依赖。
从 pr.py 提取，职责分离。
"""
from typing import Optional, Tuple

CYCLICAL_INDUSTRIES = {
    "煤炭", "钢铁", "有色金属", "石油", "化工", "航运", "建材",
    "水泥", "玻璃", "造纸", "养殖", "房地产", "工程机械", "船舶",
    "化肥", "农药", "化纤", "橡胶", "塑料",
    "证券", "券商", "保险",
}

TECH_INDUSTRIES = {
    "半导体", "软件", "互联网", "计算机", "通信", "电子",
    "芯片", "人工智能", "云计算", "大数据",
}

# 成长行业：高利润增速 + 高再投资（低分红）。修正市赚率对成长股不适用——
# 高成长需留存利润，分红率低导致 N 因子被压到 2.0、修正 PR 失真（丁宁原版定义）。
# 与 TECH 去重（半导体/芯片/人工智能等归科技）。
GROWTH_INDUSTRIES = {
    "新能源", "光伏", "太阳能", "锂电", "储能", "风电", "氢能", "新能源汽车",
    "军工", "国防", "机器人", "工业机器人", "智能驾驶", "卫星导航",
    "生物医药", "创新药", "CXO", "医疗器械", "医美",
    "新材料", "碳纤维", "复合材料",
    "算力", "数据中心", "大模型",
}


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
    """
    if payout_ratio is None:
        return None
    if payout_ratio <= 0:
        return 2.0
    raw = 0.50 / payout_ratio
    return max(1.0, min(2.0, raw))


def classify_valuation(pr: Optional[float]) -> str:
    """估值四档分类。

    阈值基于 PR 历史回测（2016-2024 沪深300，见 docs/BACKTEST_REPORT.md）：
    低估(≤0.5)与合理偏低(0.5-1)超额 +4%+，合理(1-3)接近中性，高估(>3)跑输 5%+。
    故 1.0~3.0 归合理，>3.0 才标高估；PR≤1 视为有超额的低估区间。
    """
    if pr is None:
        return "无法判定"
    if pr <= 0.5:
        return "低估"
    if pr <= 1.0:
        return "合理偏低"
    if pr <= 3.0:
        return "合理"
    return "高估"


def classify_industry(industry: str) -> Tuple[bool, bool, bool, str]:
    """根据行业标签判断周期/科技/成长属性，返回 (is_cyclical, is_tech, is_growth, warning)。

    优先级：周期 > 科技 > 成长（重叠时只报最优先的一类）。
    """
    is_cyclical = any(kw in industry for kw in CYCLICAL_INDUSTRIES)
    is_tech = any(kw in industry for kw in TECH_INDUSTRIES)
    is_growth = any(kw in industry for kw in GROWTH_INDUSTRIES)

    if is_cyclical:
        warning = "该股属于周期行业，修正市赚率仅供参考；建议优先参考PB-市赚率"
    elif is_tech:
        warning = "该股属于科技行业，修正市赚率可能不适用（科技股常以回购代替分红）"
    elif is_growth:
        warning = "该股属于成长行业，修正市赚率可能不适用（高成长需留存利润，分红率低导致N因子失真）"
    else:
        warning = ""

    return is_cyclical, is_tech, is_growth, warning
