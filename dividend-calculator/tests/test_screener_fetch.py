"""逐只取数失败隔离 module 的 interface 测试（src/screener_fetch.py）。

这些测试钉住的是三条取数路径共享的契约：缓存复用不请求、单只失败不中断、
连续失败中止、空响应重置计数、零写入护栏按规模生效。
"""
import pytest

from src.screener_fetch import (
    DEFAULT_MAX_CONSECUTIVE_FAILURES,
    FetchPolicy,
    effective_mass_failure_min,
    fetch_each,
    fetch_or_default,
)


def _policy(**kw):
    base = {"timeout_s": 1.0}
    base.update(kw)
    return FetchPolicy(**base)


class TestCacheReuse:
    def test_cached_rows_skip_request(self):
        calls = []
        outcome = fetch_each(
            ["000001", "000002"], lambda c: calls.append(c) or c,
            label="测试", policy=_policy(),
            cached=lambda c: {"cached": c} if c == "000001" else None,
        )
        assert calls == ["000002"]
        assert outcome.attempted == 1
        assert outcome.written == 1
        assert outcome.items == [{"cached": "000001"}, "000002"]

    def test_before_request_only_for_fetched_codes(self):
        waits = []
        fetch_each(
            ["a", "b", "c"], lambda c: c,
            label="测试", policy=_policy(),
            cached=lambda c: "hit" if c == "b" else None,
            before_request=lambda: waits.append(1),
        )
        assert len(waits) == 2


class TestFailureIsolation:
    def test_single_failure_does_not_abort(self):
        def fetch(code):
            if code == "bad":
                raise TimeoutError("boom")
            return code

        outcome = fetch_each(["bad", "ok"], fetch, label="测试", policy=_policy())
        assert outcome.items == ["ok"]
        assert outcome.failures == 1
        assert outcome.attempted == 2
        assert outcome.written == 1

    def test_consecutive_failures_abort_with_label(self):
        def fetch(code):
            raise TimeoutError("dead")

        with pytest.raises(RuntimeError, match="财务取数连续 3 只失败"):
            fetch_each(["a", "b", "c"], fetch, label="财务",
                       policy=_policy(max_consecutive_failures=3))

    def test_default_limit_is_30(self):
        assert DEFAULT_MAX_CONSECUTIVE_FAILURES == 30

    def test_empty_response_resets_counter(self):
        """空响应说明数据源活着，不能累积进连续失败。"""
        seq = {"a": "fail", "b": "fail", "c": "empty", "d": "fail", "e": "ok"}

        def fetch(code):
            kind = seq[code]
            if kind == "fail":
                raise TimeoutError("boom")
            if kind == "empty":
                return None
            return code

        outcome = fetch_each(list(seq), fetch, label="测试",
                             policy=_policy(max_consecutive_failures=3),
                             is_empty=lambda r: r is None)
        assert outcome.failures == 3
        assert outcome.items == ["e"]

    def test_empty_response_is_not_written(self):
        written = []
        outcome = fetch_each(["a"], lambda c: None, label="测试", policy=_policy(),
                             is_empty=lambda r: r is None, save=written.append)
        assert written == []
        assert outcome.written == 0


class TestMassFailureGuard:
    def test_zero_write_aborts_at_scaled_threshold(self):
        with pytest.raises(RuntimeError, match="无一成功写入"):
            fetch_each(["a", "b", "c", "d"], lambda c: None, label="财务",
                       policy=_policy(mass_failure_min=4),
                       is_empty=lambda r: r is None)

    def test_guard_not_triggered_when_any_written(self):
        outcome = fetch_each(
            ["a", "b", "c", "d"],
            lambda c: "ok" if c == "d" else None,
            label="财务", policy=_policy(mass_failure_min=4),
            is_empty=lambda r: r is None,
        )
        assert outcome.written == 1

    def test_guard_disabled_by_default(self):
        outcome = fetch_each(["a", "b"], lambda c: None, label="股息",
                             policy=_policy(), is_empty=lambda r: r is None)
        assert outcome.written == 0

    def test_guard_counts_attempts_not_cache_hits(self):
        """缓存命中的行不算「已尝试」——旧行完好，不该触发中止。"""
        outcome = fetch_each(
            ["a", "b", "c", "d"],
            lambda c: None,
            label="财务", policy=_policy(mass_failure_min=4),
            cached=lambda c: "cached" if c in ("a", "b") else None,
            is_empty=lambda r: r is None,
        )
        assert outcome.attempted == 2
        assert len(outcome.items) == 2

    def test_threshold_scales_with_batch_size(self):
        assert effective_mass_failure_min(5559) == 200
        assert effective_mass_failure_min(86) == 43
        assert effective_mass_failure_min(10) == 20
        assert effective_mass_failure_min(0) == 200
        assert effective_mass_failure_min(5559, configured=50) == 50


class TestSaveContract:
    def test_save_returns_stored_row(self):
        stored = []

        def save(result):
            row = {"row": result}
            stored.append(row)
            return row

        outcome = fetch_each(["a"], lambda c: c, label="测试", policy=_policy(), save=save)
        assert stored == [{"row": "a"}]
        assert outcome.items == [{"row": "a"}]


class TestFetchOrDefault:
    def test_returns_value_on_success(self):
        assert fetch_or_default("a", lambda: 7, label="可持续性",
                                timeout_s=1.0, default="未评估") == 7

    def test_returns_default_on_exception(self):
        def boom():
            raise TimeoutError("dead")

        assert fetch_or_default("a", boom, label="可持续性",
                                timeout_s=1.0, default="未评估") == "未评估"


class TestPolicySingleSource:
    """漏斗阈值只有一份定义（src/screening），其余位置引用它。"""

    def test_screener_pr_alias_points_to_screening(self):
        import src.screening as sc
        import src.screener_pr as spr
        assert spr.PR_ZONE_KEEP is sc.DEFAULT_PR_ZONE

    def test_sustainability_alias_points_to_screening(self):
        import src.screening as sc
        import src.screener_sustainability as ss
        assert ss.SUS_VERDICT_KEEP is sc.DEFAULT_SUS_VERDICT

    def test_backtest_engine_uses_screening_thresholds(self):
        import src.screening as sc
        import scripts.backtest_engine as be
        from src.pr_calculator import PR_FAIR_LOW_MAX
        assert be.TTM_YIELD_THRESHOLD == sc.DEFAULT_MIN_TTM
        assert be.REAL_YIELD_THRESHOLD == sc.DEFAULT_MIN_REAL
        assert be.PR_THRESHOLD == PR_FAIR_LOW_MAX == 1.0
        assert be.SUSTAINABLE_VERDICTS == sc.DEFAULT_SUS_VERDICT

    def test_classify_valuation_uses_named_cutoffs(self):
        from src.pr_calculator import (
            PR_FAIR_LOW_MAX, PR_FAIR_MAX, PR_UNDERVALUED_MAX, classify_valuation,
        )
        assert classify_valuation(PR_UNDERVALUED_MAX) == "低估"
        assert classify_valuation(PR_FAIR_LOW_MAX) == "合理偏低"
        assert classify_valuation(PR_FAIR_MAX) == "合理"
        assert classify_valuation(PR_FAIR_MAX + 0.01) == "高估"

    def test_screener_cli_defaults_match_screening(self):
        import inspect
        import src.screening as sc
        from src.screener import run_screener
        sig = inspect.signature(run_screener)
        assert sig.parameters["min_ttm"].default == sc.DEFAULT_MIN_TTM
        assert sig.parameters["pr_zone"].default == sc.DEFAULT_PR_ZONE
        assert sig.parameters["sus_verdict"].default == sc.DEFAULT_SUS_VERDICT
