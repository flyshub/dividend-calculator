"""选股漏斗 deep module 测试（spec #67，工单 #68；ADR-0001）。

覆盖漏斗级判定（run_funnel）：
- ① 行情可用 / ② 真实+TTM 双阈值（严格 >）/ ③ PR 估值区间 / ④ 可持续性 verdict
- 降级回退（#81/#82）：total/ttm_dividend 缺失 → 用快照旧值；统计进 FunnelResult
- default_pr_evaluator 纯计算；build_output_rows 11 列契约 + 排序
全部离线：注入假快照与假评估回调，不碰网络/缓存。
"""
import pytest

from src.screening import (
    DEFAULT_MIN_REAL,
    DEFAULT_MIN_TTM,
    DEFAULT_PR_ZONE,
    DEFAULT_SUS_VERDICT,
    FIELDS,
    FunnelCandidate,
    FunnelConfig,
    FunnelResult,
    PrValuation,
    build_output_rows,
    compute_real_yield,
    default_pr_evaluator,
    run_funnel,
)
from src.screener_cache import DividendSnapshot, FinanceSnapshot, QuoteSnapshot


_MISSING = object()


def _quote(code="600900", price=10.0, pe=8.0, market_cap=1e11, shares=1e10):
    return QuoteSnapshot(code=code, name="长江电力", price=price, pe_ttm=pe, pb=1.0,
                         total_shares=shares, market_cap=market_cap,
                         quote_time="", source="腾讯")


def _dividend(code="600900", real=6.0, ttm=6.5, total=_MISSING, ttm_total=_MISSING):
    # 默认按市值 1e11 反推总额：total = real/100 × 1e11（与 _quote 默认市值配套）
    if total is _MISSING:
        total = real / 100.0 * 1e11
    if ttm_total is _MISSING:
        ttm_total = ttm / 100.0 * 1e11
    return DividendSnapshot(code=code, real_yield=real, ttm_yield=ttm,
                            real_yield_year="2025", ttm_period="p",
                            total_dividend=total, ttm_dividend=ttm_total,
                            dividend_source="m")


def _finance(code="600900", roe=16.0):
    return FinanceSnapshot(code=code, roe_latest=roe, roe_period="2025年报",
                           net_profit_annual=1e9, payout_ratio=0.5, finance_source="东财")


def _candidate(code="600900", **kw):
    data = dict(quote=_quote(code=code), dividend=_dividend(code=code),
                finance=_finance(code=code))
    data.update(kw)
    return FunnelCandidate(code=code, **data)


class TestFunnel1Viability:
    def test_viable_passes(self):
        c = _candidate()
        result = run_funnel([c])
        assert result.stage_counts[0] == 1
        assert c.pass_viability is True

    def test_no_quote_rejected(self):
        assert run_funnel([_candidate(quote=None)]).stage_counts[0] == 0

    def test_zero_price_rejected(self):
        assert run_funnel([_candidate(quote=_quote(price=0))]).stage_counts[0] == 0

    def test_none_market_cap_rejected(self):
        assert run_funnel([_candidate(quote=_quote(market_cap=None))]).stage_counts[0] == 0

    def test_none_shares_rejected(self):
        assert run_funnel([_candidate(quote=_quote(shares=None))]).stage_counts[0] == 0


class TestFunnel2Yield:
    def test_passes_both_above(self):
        c = _candidate()
        result = run_funnel([c])
        assert result.stage_counts[1] == 1
        assert c.pass_yield is True

    def test_rejects_real_below(self):
        c = _candidate(dividend=_dividend(real=4.0, ttm=6.5))
        assert run_funnel([c]).stage_counts[1] == 0

    def test_rejects_ttm_below(self):
        c = _candidate(dividend=_dividend(real=6.0, ttm=4.0))
        assert run_funnel([c]).stage_counts[1] == 0

    def test_boundary_exact_rejected(self):
        # 严格 >：恰好 5.0 不过
        c = _candidate(dividend=_dividend(real=5.0, ttm=6.5))
        assert run_funnel([c]).stage_counts[1] == 0
        c2 = _candidate(dividend=_dividend(real=6.0, ttm=5.0))
        assert run_funnel([c2]).stage_counts[1] == 0

    def test_missing_stored_yield_rejected(self):
        c = _candidate(dividend=_dividend(real=None, ttm=6.5, total=None, ttm_total=None))
        assert run_funnel([c]).stage_counts[1] == 0

    def test_custom_threshold(self):
        c = _candidate(dividend=_dividend(real=4.5, ttm=6.0))
        result = run_funnel([c], FunnelConfig(min_real=4.0))
        assert result.stage_counts[1] == 1

    def test_mixed_pool(self):
        pool = [_candidate("600900"), _candidate("600987",
                dividend=_dividend("600987", real=3.0, ttm=4.0))]
        result = run_funnel(pool, evaluate_sustainability=lambda c: "可持续")
        assert result.stage_counts[1] == 1
        assert [c.code for c in result.candidates] == ["600900"]

    def test_recompute_overrides_stored(self):
        # 存储旧值仅 1%，但 total_dividend/市值 实时重算为 10% → 通过
        c = _candidate(dividend=_dividend(real=1.0, ttm=1.0, total=1e10, ttm_total=1.1e10))
        assert run_funnel([c]).stage_counts[1] == 1

    def test_no_dividend_snapshot_rejected(self):
        assert run_funnel([_candidate(dividend=None)]).stage_counts[1] == 0


class TestFunnel2Fallback:
    """#81/#82 降级语义：缺 total/ttm_dividend → 回退快照旧值，统计进 FunnelResult。"""

    def test_missing_total_dividend_falls_back(self):
        c = _candidate(dividend=_dividend(real=6.0, ttm=6.5, total=None, ttm_total=None))
        result = run_funnel([c])
        assert result.stage_counts[1] == 1
        assert c.used_fallback is True
        assert result.fallback_count == 1
        assert result.fallback_passed == 1

    def test_missing_ttm_only_falls_back_both(self):
        # 仅 ttm_dividend 缺失 → real 与 ttm 一起回退（保持同源）
        c = _candidate(dividend=_dividend(real=6.0, ttm=6.5, total=1e10, ttm_total=None))
        result = run_funnel([c])
        assert result.stage_counts[1] == 1
        assert c.used_fallback is True
        assert result.fallback_count == 1

    def test_fallback_still_enforces_threshold(self):
        # 回退后旧值仍低于阈值 → 不过；统计保留
        c = _candidate(dividend=_dividend(real=3.0, ttm=6.5, total=None, ttm_total=None))
        result = run_funnel([c])
        assert result.stage_counts[1] == 0
        assert result.fallback_count == 1
        assert result.fallback_passed == 0

    def test_no_fallback_silent(self):
        result = run_funnel([_candidate()])
        assert result.fallback_count == 0
        assert result.fallback_passed == 0


class TestFunnel3Pr:
    def test_default_evaluator_passes_low_valuation(self):
        c = _candidate()
        result = run_funnel([c])  # PE 8 / ROE 16 = 0.5 → 低估
        assert result.stage_counts[2] == 1
        assert c.pr == pytest.approx(0.5)
        assert c.valuation_zone == "低估"
        assert c.pass_pr is True

    def test_default_evaluator_rejects_overvalued(self):
        c = _candidate(finance=_finance(roe=2.0))  # PR 4.0 → 高估
        assert run_funnel([c]).stage_counts[2] == 0

    def test_missing_roe_unknown_zone_rejected(self):
        c = _candidate(finance=None)
        result = run_funnel([c])
        assert result.stage_counts[2] == 0

    def test_injected_evaluator_used(self):
        c = _candidate()
        result = run_funnel(
            [c],
            evaluate_pr=lambda cand: PrValuation(pr=0.3, valuation_zone="低估", industry="电力"))
        assert result.stage_counts[2] == 1
        assert c.industry == "电力"

    def test_custom_pr_zone(self):
        c = _candidate(finance=_finance(roe=4.0))  # PR 2.0 → 合理
        result = run_funnel([c], FunnelConfig(pr_zone=("合理",)))
        assert result.stage_counts[2] == 1


class TestFunnel4Sustainability:
    def _run(self, verdict="可持续", **cfg):
        c = _candidate()
        return run_funnel(
            [c],
            FunnelConfig(**cfg),
            evaluate_sustainability=lambda cand: verdict)

    def test_sustainable_passes(self):
        result = self._run("可持续")
        assert result.stage_counts[3] == 1
        assert result.candidates[0].verdict == "可持续"

    def test_weak_passes(self):
        assert self._run("偏弱").stage_counts[3] == 1

    def test_unsustainable_rejected(self):
        assert self._run("不可持续").stage_counts[3] == 0

    def test_unassessed_rejected(self):
        assert self._run("未评估").stage_counts[3] == 0

    def test_default_evaluator_rejects(self):
        # 默认 evaluate_sustainability 返回空串 → 不通过
        assert run_funnel([_candidate()]).stage_counts[3] == 0

    def test_custom_verdict_whitelist(self):
        assert self._run("偏弱", sus_verdict=("偏弱",)).stage_counts[3] == 1

    def test_evaluator_only_called_for_stage3_passers(self):
        calls = []
        bad = _candidate("600987", dividend=_dividend("600987", real=3.0, ttm=4.0))
        result = run_funnel(
            [_candidate("600900"), bad],
            evaluate_sustainability=lambda cand: calls.append(cand.code) or "可持续")
        assert calls == ["600900"], "漏斗② 未通过的候选不应进入可持续性评估"
        assert len(result.candidates) == 1


class TestStageCounts:
    def test_full_pipeline_counts(self):
        pool = [
            _candidate("600900"),                       # 全过
            _candidate("600987"),                       # 全过
            _candidate("600919",
                       dividend=_dividend("600919", real=2.0, ttm=2.5)),  # 卡②
        ]
        result = run_funnel(pool, evaluate_sustainability=lambda c: "可持续")
        assert result.stage_counts == [3, 2, 2, 2]
        assert [c.code for c in result.candidates] == ["600900", "600987"]

    def test_empty_universe(self):
        result = run_funnel([])
        assert result.stage_counts == [0, 0, 0, 0]
        assert result.candidates == []


class TestComputeRealYield:
    def test_basic(self):
        # 分红总额 100亿 / 市值 1000亿 = 10%
        assert compute_real_yield(1e10, 1e11) == pytest.approx(10.0)

    def test_market_cap_change_affects_yield(self):
        assert compute_real_yield(1e10, 2e11) == pytest.approx(5.0)

    def test_none_inputs(self):
        assert compute_real_yield(None, 1e11) is None
        assert compute_real_yield(1e10, None) is None
        assert compute_real_yield(1e10, 0) is None


class TestDefaultPrEvaluator:
    def test_computes_basic_pr(self):
        val = default_pr_evaluator(_candidate())
        assert val.pr == pytest.approx(0.5)
        assert val.valuation_zone == "低估"

    def test_missing_roe_unknown(self):
        val = default_pr_evaluator(_candidate(finance=None))
        assert val.pr is None
        assert val.valuation_zone == "无法判定"

    def test_missing_pe_unknown(self):
        val = default_pr_evaluator(_candidate(quote=_quote(pe=None)))
        assert val.pr is None
        assert val.valuation_zone == "无法判定"


class TestBuildOutputRows:
    def test_rows_sorted_by_real_yield(self):
        result = FunnelResult(
            stage_counts=[2, 2, 2, 2],
            candidates=[
                _candidate("600900", dividend=_dividend("600900", real=6.0, ttm=6.5)),
                _candidate("600987", dividend=_dividend("600987", real=6.5, ttm=7.0)),
            ],
        )
        rows = build_output_rows(result)
        assert [r["代码"] for r in rows] == ["600987", "600900"]
        assert rows[0]["真实股息率%"] == 6.5
        assert rows[1]["真实股息率%"] == 6.0

    def test_fields_present_and_contract(self):
        c = _candidate()
        c.valuation_zone = "低估"
        c.pr = 0.5
        c.verdict = "可持续"
        c.industry = "电力"
        result = FunnelResult(stage_counts=[1, 1, 1, 1], candidates=[c])
        rows = build_output_rows(result)
        r = rows[0]
        assert list(r.keys()) == FIELDS
        assert r["估值区间"] == "低估"
        assert r["市赚率PR"] == 0.5
        assert r["可持续性"] == "可持续"
        assert r["行业"] == "电力"
        assert r["数据来源"] == "m / 腾讯"

    def test_fallback_candidate_shows_blank_yield(self):
        """输出行如实标注缺失：回退通过但 total_dividend 缺失 → 股息率列空串。"""
        c = _candidate(dividend=_dividend(real=6.0, ttm=6.5, total=None, ttm_total=None))
        c.valuation_zone = "低估"
        c.verdict = "可持续"
        result = FunnelResult(stage_counts=[1, 1, 1, 1], candidates=[c])
        r = build_output_rows(result)[0]
        assert r["真实股息率%"] == ""
        assert r["TTM股息率%"] == ""

    def test_empty_result(self):
        assert build_output_rows(FunnelResult(stage_counts=[0, 0, 0, 0], candidates=[])) == []


class TestDefaults:
    def test_default_thresholds(self):
        assert DEFAULT_MIN_TTM == 5.0
        assert DEFAULT_MIN_REAL == 5.0
        assert DEFAULT_PR_ZONE == ("合理偏低", "低估")
        assert DEFAULT_SUS_VERDICT == ("可持续", "偏弱")

    def test_fields_contract_11_columns(self):
        assert len(FIELDS) == 11
        assert FIELDS[0] == "代码"
        assert "行业" in FIELDS
