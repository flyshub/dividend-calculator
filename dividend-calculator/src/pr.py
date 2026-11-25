"""
市赚率（PR）计算模块

公式体系（完整版，PE 与 ROE 均取百分数值直接相除）：
  基础PR  = PE_TTM / ROE
  修正PR  = N × PE_TTM / ROE      （N 基于股利支付率修正）
  PB-PR   = PB / ROE² × 100       （周期股参考；ROE 为百分数）

数据来源（多源降级）：
  PE-TTM / PB: 腾讯行情 [主] → akshare 同花顺 EPS 推算 → 东方财富 push2 [备]（f164=PE-TTM、f167=PB，实测对照腾讯吻合）
  ROE / 净利润: 东方财富 datacenter(RPT_F10_FINANCE_MAINFINADATA) [主] → mootdx F10 → akshare 同花顺 [降级]
  行业分类:     mootdx F10 → 东方财富 datacenter(RPT_F10_BASIC_ORGINFO) → 降级为 "未知行业"
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Optional, List

from .datasource.validation import check_pe, check_pb, check_roe, check_net_profit, check_payout_ratio

from .pr_calculator import (
    compute_basic_pr,
    compute_corrected_pr,
    compute_pb_pr,
    compute_n_factor,
    classify_valuation,
    classify_industry,
    judge_pr,
    select_judge_roe,
)
from .risk_flags import st_warning

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class PRResult:
    """市赚率计算结果"""
    stock_code: str
    stock_name: Optional[str]

    # 市赚率三值
    pr_basic: Optional[float]           # 基础PR = PE_TTM / ROE
    pr_corrected: Optional[float]        # 修正PR = N × PE_TTM / ROE
    pr_pb: Optional[float]               # PB-PR = PB / ROE² × 100

    # 估值档位：判定口径 = 基础 PR（周期股用 5 年 ROE 中位数），与选股器漏斗③ 共用
    # pr_calculator.judge_pr。修正 PR 仅页面参考，不进判据。
    valuation_zone: str                   # 低估 / 合理偏低 / 合理 / 高估

    # 中间计算数据
    pe_ttm: Optional[float]              # 市盈率 TTM
    pb: Optional[float]                   # 市净率
    roe_latest: Optional[float]           # 最新年报 ROE（百分比，如 15.9）
    roe_5y_median: Optional[float]        # 5年 ROE 中位数（百分比）
    net_profit_latest_period: Optional[float]  # 最新报告期累计净利润（元），非 TTM；仅展示
    net_profit_annual: Optional[float]    # 最新年报净利润（元）
    dividend_total: Optional[float]       # 最新财年现金分红总额（元）
    payout_ratio: Optional[float]         # 股利支付率（0~1）
    n_factor: Optional[float]             # 修正系数 N

    # 行业相关
    industry: str                         # 行业分类
    is_cyclical: bool                     # 是否周期行业
    is_tech: bool                         # 是否科技行业
    is_growth: bool                       # 是否成长行业
    is_loss_stock: bool                   # 是否亏损股
    pr_warning: str                       # 市赚率适用性提示

    # 数据来源追踪
    pe_pb_source: str                     # PE/PB 数据来源
    finance_source: str                   # 财务数据来源
    industry_source: str                  # 行业数据来源
    errors: List[str] = field(default_factory=list)  # 采集过程中的非致命错误
    # 口径标注：PE-TTM 与 ROE 时间窗口差异（评审 P0-2，见 DATA_RELIABILITY.md §3）
    roe_period: Optional[str] = None      # ROE 对应报告期，如 '2025年报'（PE 为 TTM 含最新季报）
    pr_judge: Optional[float] = None      # 判定用 PR（= 基础 PR，ROE 见 roe_judge）
    roe_judge: Optional[float] = None     # 判定用 ROE（周期股为 5 年中位数）


# ---------------------------------------------------------------------------
# 数据层 re-export（实现在 pr_data.py，#24 纯移动）
# ---------------------------------------------------------------------------
# 私有名一并 re-export，保持 tests/外部调用点兼容
from .pr_data import (
    _amount_to_float,
    _get_financial,
    _get_financial_em,
    _get_financial_ths,
    _get_industry,
    _get_pe_pb,
    _get_pe_pb_eastmoney,
    _get_pe_pb_tencent,
    _pct_to_float,
)

def calculate_pr(
    stock_code: str,
    stock_name: Optional[str] = None,
    dividend_total: Optional[float] = None,
    stock_info: Optional["StockInfo"] = None,  # noqa: F821  # forward ref
) -> PRResult:
    """
    计算市赚率（完整体系）

    Args:
        stock_code: 6位股票代码
        stock_name: 股票名称（可选，内部也可从腾讯获取）
        dividend_total: 最新财年现金分红总额（元），由调用方从股息率链路传入
        stock_info: 股票基本信息（可选，仅用于日志）

    Returns:
        PRResult 对象
    """
    all_errors: List[str] = []

    # 1. 获取 PE-TTM 和 PB
    pe_ttm, pb, tencent_name, pe_pb_src, errs = _get_pe_pb(stock_code)
    all_errors.extend(errs)
    if stock_name is None and tencent_name:
        stock_name = tencent_name

    # 2. 获取 ROE 和净利润
    roe_latest, roe_5y_median, net_profit_latest_period, net_profit_annual, fin_src, errs, roe_period_year = _get_financial(stock_code)
    all_errors.extend(errs)

    # 3. 获取行业分类
    industry, ind_src = _get_industry(stock_code)

    # 4. 分类行业属性
    is_cyclical, is_tech, is_growth, pr_warning = classify_industry(industry)

    # 4.5 ST / *ST 退市风险警示（#26）：只警告，不改 PR 判定
    st_flag = st_warning(stock_name)

    # 5. 判断亏损股
    is_loss_stock = False
    if net_profit_annual is not None and net_profit_annual <= 0:
        is_loss_stock = True
        pr_warning = pr_warning + "；该股为亏损股，市赚率不适用" if pr_warning else "该股为亏损股，市赚率不适用"

    # 6. 计算股利支付率 & 修正系数 N
    payout_ratio = None
    n_factor = None
    if net_profit_annual is not None and net_profit_annual > 0 and dividend_total is not None:
        payout_ratio = dividend_total / net_profit_annual
        n_factor = compute_n_factor(payout_ratio)

    # 7. 计算三个 PR 值
    pr_basic = None
    pr_corrected = None
    pr_pb = None
    pr_judge = None
    roe_judge = None
    valuation_zone = "无法判定"

    roe_for_calc = roe_latest  # 主用最新年报 ROE

    # 周期股 PB-市赚率用 5 年 ROE 中位数（对齐市赚率定义：周期股单年 ROE 失真，
    # 用多年 ROE 平滑；丁宁原版用均值，本项目用中位数抗单年极端值）
    roe_for_pb = select_judge_roe(roe_for_calc, roe_5y_median, is_cyclical)

    if not is_loss_stock and pe_ttm is not None and roe_for_calc is not None and roe_for_calc > 0:
        pr_basic = compute_basic_pr(pe_ttm, roe_for_calc)
        pr_corrected = compute_corrected_pr(pe_ttm, roe_for_calc, n_factor)
        pr_pb = compute_pb_pr(pb, roe_for_pb)

        # 估值档位：与选股器漏斗③ 共用 judge_pr（基础 PR + 周期股 5 年中位数 ROE）。
        # 修正 PR 只展示，不参与判定——阈值 0.5/1.0/3.0 是在基础 PR 上标定的。
        roe_judge, pr_judge, valuation_zone = judge_pr(
            pe_ttm, roe_latest, roe_5y_median, is_cyclical)

    if st_flag:
        pr_warning = pr_warning + "；" + st_flag if pr_warning else st_flag

    # 数据完整性软校验（审查 #4）：越界只追加 errors，不否决结果
    all_errors.extend(_check_pr_fields(pe_ttm, pb, roe_latest, net_profit_annual, payout_ratio))

    return PRResult(
        stock_code=stock_code,
        stock_name=stock_name,
        pr_basic=pr_basic,
        pr_corrected=pr_corrected,
        pr_pb=pr_pb,
        valuation_zone=valuation_zone,
        pr_judge=pr_judge,
        roe_judge=roe_judge,
        pe_ttm=pe_ttm,
        pb=pb,
        roe_latest=roe_latest,
        roe_period=f"{roe_period_year}年报" if roe_period_year else None,
        roe_5y_median=roe_5y_median,
        net_profit_latest_period=net_profit_latest_period,
        net_profit_annual=net_profit_annual,
        dividend_total=dividend_total,
        payout_ratio=payout_ratio,
        n_factor=n_factor,
        industry=industry,
        is_cyclical=is_cyclical,
        is_tech=is_tech,
        is_growth=is_growth,
        is_loss_stock=is_loss_stock,
        pr_warning=pr_warning,
        pe_pb_source=pe_pb_src,
        finance_source=fin_src,
        industry_source=ind_src,
        errors=all_errors,
    )


def _check_pr_fields(
    pe_ttm: Optional[float],
    pb: Optional[float],
    roe_latest: Optional[float],
    net_profit_annual: Optional[float],
    payout_ratio: Optional[float],
) -> List[str]:
    """市赚率相关字段数据完整性软校验（审查 #4）。越界追加 warning，不否决。"""
    warnings: List[str] = []
    for w in (
        check_pe(pe_ttm),
        check_pb(pb),
        check_roe(roe_latest),
        check_net_profit(net_profit_annual),
        check_payout_ratio(payout_ratio),
    ):
        if w:
            warnings.append(w)
    return warnings
