"""A 股特色风险标记（#26）。

两条规则，双端镜像（JS 在 `site/js/calculator.js::isST` / `goodwillWarning`）：

1. **ST / *ST**：名称含 `ST` 即视为退市风险警示股。名称来自腾讯行情，零新数据源。
2. **商誉占净资产 > 50%**：并购商誉存在减值风险，可能一次性侵蚀净资产与分红能力。
   数据来自东财 `RPT_F10_FINANCE_GBALANCE` 的 `GOODWILL` / `TOTAL_EQUITY`
   （2026-11 实测存在；银行等金融报表无此行，取不到即跳过，不编造）。

这两条都是**警示**，不改任何评分、档位或漏斗判据。
"""

from typing import Optional

# 商誉/净资产 阈值：>50% 触发警示
GOODWILL_EQUITY_WARN = 0.50


def is_st(stock_name: Optional[str]) -> bool:
    """名称是否带 ST / *ST（退市风险警示）。名称缺失时返回 False（不猜）。"""
    if not stock_name:
        return False
    return "ST" in stock_name.upper()


def st_warning(stock_name: Optional[str]) -> Optional[str]:
    """ST 警示文案；非 ST 或名称缺失返回 None。"""
    if not is_st(stock_name):
        return None
    return "ST 股票（退市风险警示）：盈利与分红政策可能已恶化，历史分红不可直接外推"


def goodwill_warning(goodwill: Optional[float],
                     equity: Optional[float]) -> Optional[str]:
    """商誉/净资产 > 50% 的警示文案；数据缺失或未超阈值返回 None。

    equity 用所有者权益合计（东财 TOTAL_EQUITY），与商誉同表同报告期，口径自洽。
    """
    if goodwill is None or equity is None or equity <= 0 or goodwill <= 0:
        return None
    ratio = goodwill / equity
    if ratio <= GOODWILL_EQUITY_WARN:
        return None
    return (f"商誉占净资产 {ratio * 100:.0f}%（>{GOODWILL_EQUITY_WARN * 100:.0f}%）："
            "商誉减值会直接侵蚀净资产与分红能力")
