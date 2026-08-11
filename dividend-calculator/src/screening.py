"""选股漏斗 — 选股判定 deep module（spec #67，工单 #68；ADR-0001）。

四级判定 + 降级回退 + 输出整形，语义集中于此：
  ① 行情可用（quote 的价格/总股本/市值均为正）
  ② 真实股息率 > min_real 且 TTM 股息率 > min_ttm（实时重算；
     缺 total/ttm_dividend 时回退快照旧值，仅缺失回退、非 0，#81）
  ③ 市赚率估值区间 ∈ pr_zone（PR = PE_TTM / ROE，纯计算）
  ④ 可持续性 verdict ∈ sus_verdict（评估由数据获取层注入）

本 module 不碰网络、不碰缓存：数据获取（拉行情/股息/财务/可持续性）
由编排层注入 evaluate_pr / evaluate_sustainability 两个回调；测试用假回调离线验证。
"""
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

from src.pr_calculator import classify_valuation, compute_basic_pr
from src.screener_cache import DividendSnapshot, FinanceSnapshot, QuoteSnapshot

# 默认阈值（与 spec #67 / 页面口径卡一致，page-parity 测试引用）
DEFAULT_MIN_TTM = 5.0
DEFAULT_MIN_REAL = 5.0
DEFAULT_PR_ZONE = ("合理偏低", "低估")
DEFAULT_SUS_VERDICT = ("可持续", "偏弱")

# CSV 11 列契约（export_screener_json.py 与选股页列定义共用；改动需三处同步）
FIELDS = ["代码", "名称", "TTM股息率%", "真实股息率%", "估值区间", "市赚率PR",
          "行业", "可持续性", "ROE%", "总市值(亿)", "数据来源"]


@dataclass(frozen=True)
class FunnelConfig:
    """选股漏斗阈值配置（CLI 参数直接映射）。"""
    min_ttm: float = DEFAULT_MIN_TTM
    min_real: float = DEFAULT_MIN_REAL
    pr_zone: Sequence[str] = DEFAULT_PR_ZONE
    sus_verdict: Sequence[str] = DEFAULT_SUS_VERDICT


@dataclass
class FunnelCandidate:
    """单股候选行：跨漏斗各阶段传递的 typed 对象。"""
    code: str
    quote: Optional[QuoteSnapshot] = None
    dividend: Optional[DividendSnapshot] = None
    finance: Optional[FinanceSnapshot] = None
    # 评估结果（漏斗内部计算 + 注入评估器填充）
    pr: Optional[float] = None
    valuation_zone: str = ""
    industry: str = ""
    verdict: str = ""
    # 实时股息率（漏斗② 计算，含降级回退）
    real_yield_now: Optional[float] = None
    ttm_yield_now: Optional[float] = None
    used_fallback: bool = False
    # 四级通过标记
    pass_viability: bool = False
    pass_yield: bool = False
    pass_pr: bool = False
    pass_sus: bool = False

    @property
    def passed(self) -> bool:
        return self.pass_viability and self.pass_yield and self.pass_pr and self.pass_sus


@dataclass
class FunnelResult:
    """一次选股漏斗的产出。"""
    stage_counts: List[int]            # [①, ②, ③, ④] 各层通过数
    candidates: List[FunnelCandidate]  # 通过全部层的候选
    fallback_count: int = 0            # 漏斗② 缺 total/ttm_dividend 触发回退的股票数
    fallback_passed: int = 0           # 其中最终入选数


@dataclass(frozen=True)
class PrValuation:
    """PR 估值结果（evaluate_pr 回调的产出）。"""
    pr: Optional[float]
    valuation_zone: str
    industry: str = ""


def compute_real_yield(total_dividend: Optional[float], market_cap: Optional[float]) -> Optional[float]:
    """真实股息率 = 分红总额 / 当前总市值 × 100（实时，随市值每日变化）。"""
    if total_dividend is None or market_cap is None or market_cap <= 0:
        return None
    return (total_dividend / market_cap) * 100


def default_pr_evaluator(candidate: FunnelCandidate) -> PrValuation:
    """默认 PR 评估（纯计算）：PR = PE_TTM / ROE_latest + 估值四档分类。

    缺 PE 或 ROE → 无法判定（不调网络，与既有纯缓存路径一致）。
    """
    quote, fin = candidate.quote, candidate.finance
    if quote is None or quote.pe_ttm is None or fin is None or fin.roe_latest is None:
        return PrValuation(pr=None, valuation_zone="无法判定")
    pr_val = compute_basic_pr(quote.pe_ttm, fin.roe_latest)
    return PrValuation(pr=pr_val, valuation_zone=classify_valuation(pr_val))


def run_funnel(
    universe: List[FunnelCandidate],
    config: FunnelConfig = FunnelConfig(),
    *,
    evaluate_pr: Callable[[FunnelCandidate], PrValuation] = default_pr_evaluator,
    evaluate_sustainability: Callable[[FunnelCandidate], str] = lambda c: "",
) -> FunnelResult:
    """四级漏斗主流程（判定 + 降级 + 分层过滤）。

    判定口径与既有实现逐项一致：
      ① 行情可用：quote.price / total_shares / market_cap 均为正
      ② real_yield_now > min_real 且 ttm_yield_now > min_ttm（严格 >）；
         total/ttm_dividend 缺失时回退快照旧值（仅缺失回退，非 0）
      ③ valuation_zone ∈ pr_zone（由 evaluate_pr 计算）
      ④ verdict ∈ sus_verdict（由 evaluate_sustainability 计算）
    """
    stage_counts = [0, 0, 0, 0]
    fallback_count = 0
    fallback_passed = 0
    final_candidates: List[FunnelCandidate] = []

    for c in universe:
        # ① 行情可用
        q = c.quote
        if not (q is not None and q.price is not None and q.price > 0
                and q.total_shares is not None and q.total_shares > 0
                and q.market_cap is not None and q.market_cap > 0):
            continue
        c.pass_viability = True
        stage_counts[0] += 1

        # ② 实时股息率 + 降级回退（#81/#82 语义原样保留）
        d = c.dividend
        if d is not None:
            real = compute_real_yield(d.total_dividend, q.market_cap)
            ttm = compute_real_yield(d.ttm_dividend, q.market_cap)
            if real is None or ttm is None:
                # total/ttm_dividend 缺失（NULL，旧 DB 迁移未回填）→ 回退存储旧值
                c.used_fallback = True
                real, ttm = d.real_yield, d.ttm_yield
                fallback_count += 1
            c.real_yield_now, c.ttm_yield_now = real, ttm
        else:
            # 无股息快照 → 无收益率，按不通过处理
            c.real_yield_now, c.ttm_yield_now = None, None
        if not (c.real_yield_now is not None and c.real_yield_now > config.min_real
                and c.ttm_yield_now is not None and c.ttm_yield_now > config.min_ttm):
            continue
        c.pass_yield = True
        if c.used_fallback:
            fallback_passed += 1
        stage_counts[1] += 1

        # ③ PR 估值（默认纯计算；可注入带数据获取的评估器）
        val = evaluate_pr(c)
        c.pr, c.valuation_zone = val.pr, val.valuation_zone
        if val.industry:
            c.industry = val.industry
        if c.valuation_zone not in config.pr_zone:
            continue
        c.pass_pr = True
        stage_counts[2] += 1

        # ④ 可持续性（评估由数据获取层注入）
        c.verdict = evaluate_sustainability(c)
        if c.verdict not in config.sus_verdict:
            continue
        c.pass_sus = True
        stage_counts[3] += 1
        final_candidates.append(c)

    return FunnelResult(stage_counts=stage_counts, candidates=final_candidates,
                        fallback_count=fallback_count, fallback_passed=fallback_passed)


def build_output_rows(result: FunnelResult) -> List[dict]:
    """FunnelResult → 11 列 CSV 行（与 FIELDS 契约一致），按真实股息率降序。

    与既有输出口径一致：实时股息率 = 分红总额 / 当日市值（缺失显示空串，
    不使用漏斗② 的回退旧值——CSV 如实标注缺失）。
    """
    rows = []
    for c in result.candidates:
        q, d, fin = c.quote, c.dividend, c.finance
        market_cap = q.market_cap if q else None
        real_yield_now = compute_real_yield(d.total_dividend if d else None, market_cap)
        ttm_yield_now = compute_real_yield(d.ttm_dividend if d else None, market_cap)
        rows.append({
            "代码": c.code,
            "名称": q.name if q else "",
            "TTM股息率%": round(ttm_yield_now, 2) if ttm_yield_now else "",
            "真实股息率%": round(real_yield_now, 2) if real_yield_now else "",
            "估值区间": c.valuation_zone,
            "市赚率PR": c.pr if c.pr is not None else "",
            "行业": c.industry,
            "可持续性": c.verdict,
            "ROE%": fin.roe_latest if fin else "",
            "总市值(亿)": round(market_cap / 1e8, 2) if market_cap else "",
            "数据来源": (d.dividend_source if d else "") + " / " + (q.source if q else "腾讯"),
        })
    rows.sort(key=lambda r: r["真实股息率%"] if isinstance(r["真实股息率%"], float) else -1, reverse=True)
    return rows
