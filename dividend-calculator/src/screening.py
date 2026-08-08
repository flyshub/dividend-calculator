"""选股器纯筛选逻辑（spec #67，工单 #68）。

四级漏斗的判定核心——输入单股数据 dict，输出各漏斗通过与否 + 估值/可持续性分类。
纯函数，无网络依赖，离线可测。

漏斗（阈值来自 CLI 参数，默认值见下）：
  ① TTM 股息率 > min_ttm（默认 5）
  ② 真实股息率 > min_real（默认 5）
  ③ PR 估值 ∈ pr_zone（默认 {合理偏低, 低估}，V2 回测证实 PR≤1 有超额）
  ④ 可持续性 verdict ∈ sus_verdict（默认 {可持续, 偏弱}）

PR 计算与估值分类由 T5 完成（复用 pr_calculator.classify_valuation），
本模块只接收 valuation_zone 做筛选判定。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 默认阈值（与 spec #67 一致）
DEFAULT_MIN_TTM = 5.0
DEFAULT_MIN_REAL = 5.0
DEFAULT_PR_ZONE = ("合理偏低", "低估")
DEFAULT_SUS_VERDICT = ("可持续", "偏弱")


@dataclass(frozen=True)
class ScreenResult:
    """单股筛选结果。passed = 四层漏斗全部通过。"""
    pass_ttm: bool = False
    pass_real: bool = False
    pass_pr: bool = False
    pass_sus: bool = False
    passed: bool = False
    zone: str = ""          # 估值分类（来自 valuation_zone）
    sus_verdict: str = ""   # 可持续性结论（来自 sus_verdict）
    reason: str = ""        # 未通过时的首个失败漏斗说明


def _above(value: Optional[float], threshold: float) -> bool:
    """严格大于阈值；None 或非正数不通过。"""
    return value is not None and value > threshold


def screen_stock(
    s: Dict,
    min_ttm: float = DEFAULT_MIN_TTM,
    min_real: float = DEFAULT_MIN_REAL,
    pr_zone: List[str] = list(DEFAULT_PR_ZONE),
    sus_verdict: List[str] = list(DEFAULT_SUS_VERDICT),
) -> ScreenResult:
    """四级漏斗判定。

    输入 s：{ttm_yield, real_yield, valuation_zone, sus_verdict, ...}
    输出 ScreenResult：各漏斗通过与否 + passed + 未通过原因。
    """
    ttm = s.get("ttm_yield")
    real = s.get("real_yield")
    zone = s.get("valuation_zone") or ""
    sus = s.get("sus_verdict") or ""

    pass_ttm = _above(ttm, min_ttm)
    pass_real = _above(real, min_real)
    pass_pr = zone in pr_zone
    pass_sus = sus in sus_verdict
    passed = pass_ttm and pass_real and pass_pr and pass_sus

    # 未通过原因（首个失败漏斗）
    reason = ""
    if not passed:
        if not pass_ttm:
            reason = f"TTM 股息率 {ttm if ttm is not None else '无'}% 未达 {min_ttm}%"
        elif not pass_real:
            reason = f"真实股息率 {real if real is not None else '无'}% 未达 {min_real}%"
        elif not pass_pr:
            reason = f"PR 估值「{zone}」不在 {pr_zone} 内"
        elif not pass_sus:
            reason = f"可持续性「{sus}」不在 {sus_verdict} 内"

    return ScreenResult(
        pass_ttm=pass_ttm,
        pass_real=pass_real,
        pass_pr=pass_pr,
        pass_sus=pass_sus,
        passed=passed,
        zone=zone,
        sus_verdict=sus,
        reason=reason,
    )
