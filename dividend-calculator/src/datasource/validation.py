"""数据完整性校验层 — sanity bound 纯函数（审查 #4）。

数据铁律：任何涉及数据的功能必须先验证数据的真实准确性。
本模块提供「明显异常值」软校验：越界只追加 warning，不否决结果——
本工具是估值参考，真实极端值（小盘高 ROE、近零利润高 PE）比缺值更值得展示。
`<=0` 硬界（价格/股本/每股分红）由各数据源已有 `>0` 检查处理，不在此层。
"""
from typing import List, Optional

from .base import StockInfo

# ---------------------------------------------------------------------------
# Sanity bound 常量（软界）
# ---------------------------------------------------------------------------

# 价格（元）：A 股历史最高长期为茅台 ~2500 元，10000 有 4 倍余量
PRICE_LO, PRICE_HI = 0.0, 10000.0
# 总股本（股）：工行约 3.56e11 股为最大，1e13 是 28 倍余量
SHARES_LO, SHARES_HI = 0.0, 1e13
# 股息率（%）：>100% 意味分红>总市值，现实中不可能；真实极值 ~20-30%
YIELD_LO, YIELD_HI = 0.0, 100.0
# 股利支付率：>10 意味净利润不足分红 1/10，几乎必为分红财年与净利财年错配
PAYOUT_LO, PAYOUT_HI = 0.0, 10.0
# PE(TTM)：负 PE 无意义（已过滤为 None）；>10000 意味 EPS 趋 0
PE_LO, PE_HI = 0.0, 10000.0
# PB：负 PB 无意义；>100 意味市价远超净资产（极端泡沫）
PB_LO, PB_HI = 0.0, 100.0
# ROE（%）：净资产过小可致 >100%（罕见），±100 覆盖 99.9% 真实场景
ROE_LO, ROE_HI = -100.0, 100.0
# 净利润（元）：工行 ~3.6e11；负值合法（亏损股）
NET_PROFIT_LO, NET_PROFIT_HI = -1e13, 1e13


def _check(value: Optional[float], label: str, lo: float, hi: float) -> Optional[str]:
    """单值软校验：越界返回 warning 文案，None/缺失或界内返回 None。"""
    if value is None:
        return None
    if value <= lo or value >= hi:
        return f"{label} {value:.4g} 超出合理区间 ({lo:g}, {hi:g})，数据可能异常"
    return None


def check_stock_info(info: StockInfo) -> List[str]:
    """校验 StockInfo 的价格与总股本，返回越界 warning 列表。"""
    warnings: List[str] = []
    if info.current_price is not None:
        w = _check(info.current_price, "当前股价", PRICE_LO, PRICE_HI)
        if w:
            warnings.append(w)
    if info.total_shares is not None:
        w = _check(info.total_shares, "总股本", SHARES_LO, SHARES_HI)
        if w:
            warnings.append(w)
    return warnings


def check_dividend_yield(yield_before_tax: Optional[float]) -> Optional[str]:
    """校验股息率（百分数）。"""
    return _check(yield_before_tax, "股息率", YIELD_LO, YIELD_HI)


def check_payout_ratio(payout: Optional[float]) -> Optional[str]:
    """校验股利支付率（小数）。"""
    return _check(payout, "股利支付率", PAYOUT_LO, PAYOUT_HI)


def check_pe(pe: Optional[float]) -> Optional[str]:
    """校验 PE(TTM)。"""
    return _check(pe, "PE(TTM)", PE_LO, PE_HI)


def check_pb(pb: Optional[float]) -> Optional[str]:
    """校验 PB。"""
    return _check(pb, "PB", PB_LO, PB_HI)


def check_roe(roe: Optional[float]) -> Optional[str]:
    """校验 ROE（百分数，可负）。"""
    return _check(roe, "ROE", ROE_LO, ROE_HI)


def check_net_profit(np: Optional[float]) -> Optional[str]:
    """校验净利润（元，可负）。"""
    return _check(np, "净利润", NET_PROFIT_LO, NET_PROFIT_HI)
