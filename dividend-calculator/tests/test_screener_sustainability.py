"""选股器可持续性数据获取测试（spec #67，工单 #73）。

覆盖可持续性评估 + 缓存 + 漏斗④ 评估回调（make_sustainability_evaluator），
全部注入 assessor，不碰网络。verdict 判定测试已随 ADR-0001 迁移到 test_screening.py。

先例：tests/test_sustainability_calculator.py（分层评估）、tests/test_screener_cache.py。
"""
from unittest.mock import patch

import pytest

from src.screener_cache import (
    DividendSnapshot,
    QuoteSnapshot,
    ScreenerCache,
    SustainabilitySnapshot,
)
from src.screener_sustainability import (
    SUS_VERDICT_KEEP,
    evaluate_sustainability,
    make_sustainability_evaluator,
    reset_sus_cache,
)
from src.screening import FunnelCandidate


_MISSING = object()


@pytest.fixture(autouse=True)
def _no_rate_limit():
    """mock 限流等待，测试不 sleep。"""
    with patch("src.screener_rate_limit.batch_wait"):
        yield


@pytest.fixture(autouse=True)
def clear_cache():
    reset_sus_cache()
    yield
    reset_sus_cache()


def _dividend(real=6.0):
    return DividendSnapshot(code="600900", real_yield=real, ttm_yield=6.5,
                            real_yield_year="2025", ttm_period="p", dividend_source="m")


def _quote():
    return QuoteSnapshot(code="600900", name="长江电力", price=10, pe_ttm=8.0, pb=1.0,
                         total_shares=1e9, market_cap=1e10, quote_time="", source="腾讯")


def _candidate(dividend=_MISSING):
    if dividend is _MISSING:
        dividend = _dividend()
    return FunnelCandidate(code="600900", quote=_quote(), dividend=dividend)


class _FakeResult:
    def __init__(self, verdict):
        self.verdict = verdict


class TestEvaluateSustainability:
    def test_sustainable_passes(self):
        r = evaluate_sustainability("600900", _dividend(),
                                    assessor=lambda c: _FakeResult("可持续"))
        assert r["pass_sus"] is True
        assert r["verdict"] == "可持续"

    def test_weak_passes(self):
        r = evaluate_sustainability("600900", _dividend(),
                                    assessor=lambda c: _FakeResult("偏弱"))
        assert r["pass_sus"] is True

    def test_unsustainable_rejected(self):
        r = evaluate_sustainability("600900", _dividend(),
                                    assessor=lambda c: _FakeResult("不可持续"))
        assert r["pass_sus"] is False

    def test_unassessed_rejected(self):
        r = evaluate_sustainability("600900", _dividend(),
                                    assessor=lambda c: _FakeResult("未评估"))
        assert r["pass_sus"] is False

    def test_missing_result_rejected(self):
        r = evaluate_sustainability("600900", _dividend(), assessor=lambda c: None)
        assert r["pass_sus"] is False
        assert r["verdict"] == "未评估"

    def test_cache_hit_injects_data_no_network(self, tmp_path):
        """缓存命中时注入预拉数据，不走 assess_with_auto_fetch 网络。"""
        from src.screener_cache import SustainabilitySnapshot
        cache = ScreenerCache(tmp_path / "s.db")
        cache.upsert_sustainability(SustainabilitySnapshot(
            code="600900", financial_rows='[{"a":1}]', cashflow_rows='[]',
            dividend_rows='[]', industry="电力", price_change_1y=0.1,
            top10_holding=0.2, source="东财预拉"))
        # 打补丁：缓存命中时调用的 assess_with_auto_fetch 应带注入参数
        with patch("src.sustainability.assess_with_auto_fetch") as mock_assess:
            mock_assess.return_value = _FakeResult("可持续")
            r = evaluate_sustainability("600900", _dividend(), cache=cache)
            # 缓存命中应触发注入路径（调 assess_with_auto_fetch 但带注入参数）
            assert mock_assess.called
            call_kwargs = mock_assess.call_args.kwargs
            assert call_kwargs["industry"] == "电力"  # 用缓存的行业
            assert call_kwargs["price_change_1y"] == 0.1
        assert r["pass_sus"] is True

    def test_cache_miss_falls_back_to_assessor(self, tmp_path):
        """缓存未命中时回退到 assessor。"""
        cache = ScreenerCache(tmp_path / "s.db")
        r = evaluate_sustainability("600900", _dividend(),
                                    assessor=lambda c: _FakeResult("可持续"),
                                    cache=cache)
        assert r["pass_sus"] is True

    def test_caches_verdict(self):
        calls = []
        def assessor(code):
            calls.append(code)
            return _FakeResult("可持续")
        evaluate_sustainability("600900", _dividend(), assessor=assessor)
        evaluate_sustainability("600900", _dividend(), assessor=assessor)
        # 第二次命中缓存，不重复调用 assessor
        assert len(calls) == 1

    def test_verdict_keep_constant(self):
        assert SUS_VERDICT_KEEP == ("可持续", "偏弱")


class TestPrefetchAndCache:
    """sustainability.prefetch_and_cache：预取 + 限流 + S2 完整性检查 + 写缓存。"""

    def _patch_fetchers(self, monkeypatch, **overrides):
        import src.sustainability as sus
        defaults = {
            "fetch_financial_rows": lambda c: [{"REPORT_DATE": "2025-12-31"}],
            "fetch_cashflow_rows": lambda c: [{"REPORT_DATE": "2025-12-31"}],
            "fetch_dividend_rows": lambda c: [{"ASSIGN_PROGRESS": "实施"}],
            "fetch_industry": lambda c: "电力",
            "fetch_price_change_1y": lambda c: 0.1,
            "fetch_top10_holding": lambda c: 0.2,
        }
        defaults.update(overrides)
        for name, fn in defaults.items():
            monkeypatch.setattr(sus, name, fn)
        monkeypatch.setattr(sus, "batch_wait", lambda: None)  # 不 sleep
        return sus

    def test_prefetch_writes_snapshot(self, tmp_path, monkeypatch):
        """预取 6 类数据 → 写缓存，source=东财预拉；行数据以 JSON 字符串存列。"""
        import json
        sus = self._patch_fetchers(monkeypatch)
        cache = ScreenerCache(tmp_path / "s.db")
        snap = sus.prefetch_and_cache(cache, "600900")
        assert snap is not None and snap.source == "东财预拉"
        assert snap.industry == "电力"
        got = cache.get_sustainability("600900")
        assert got is not None
        # 序列化契约：列存 JSON 字符串（prefetch 侧 _dict_to_snapshot 序列化）
        assert json.loads(got.financial_rows)[0]["REPORT_DATE"] == "2025-12-31"
        assert json.loads(got.dividend_rows)[0]["ASSIGN_PROGRESS"] == "实施"

    def test_s2_failure_not_cached(self, tmp_path, monkeypatch):
        """S2：financial/cashflow 同时为空 → 判为拉取失败，返回 None 且不写缓存。"""
        sus = self._patch_fetchers(monkeypatch, fetch_financial_rows=lambda c: [],
                                   fetch_cashflow_rows=lambda c: [])
        cache = ScreenerCache(tmp_path / "s.db")
        assert sus.prefetch_and_cache(cache, "600900") is None
        assert cache.get_sustainability("600900") is None

    def test_fetch_failure_none_not_flagged(self, tmp_path, monkeypatch):
        """取数失败（None）≠ 空数组：financial/cashflow 为 None 不触发 S2（与既有语义一致）。"""
        sus = self._patch_fetchers(monkeypatch, fetch_financial_rows=lambda c: None,
                                   fetch_cashflow_rows=lambda c: None,
                                   fetch_dividend_rows=lambda c: None,
                                   fetch_industry=lambda c: None)
        cache = ScreenerCache(tmp_path / "s.db")
        snap = sus.prefetch_and_cache(cache, "600900")
        assert snap is not None
        assert cache.get_sustainability("600900").financial_rows is None  # 取数失败不序列化


class TestAssessFromCache:
    """sustainability.assess_from_cache：读缓存 → 内部反序列化 → 评估。"""

    def test_cache_hit_injects_deserialized_rows(self, tmp_path):
        """命中缓存 → 内部反序列化注入 assess_with_auto_fetch（调用方不接触 JSON）。"""
        import src.sustainability as sus
        cache = ScreenerCache(tmp_path / "s.db")
        cache.upsert_sustainability(SustainabilitySnapshot(
            code="600900", financial_rows='[{"a":1}]', cashflow_rows='[]',
            dividend_rows='[]', industry="电力", price_change_1y=0.1,
            top10_holding=0.2, source="东财预拉"))
        with patch("src.sustainability.assess_with_auto_fetch") as mock_assess:
            mock_assess.return_value = _FakeResult("可持续")
            r = sus.assess_from_cache(cache, "600900", total_shares=1e9,
                                      dividend_total=None, dividend_yield_before_tax=6.0,
                                      latest_dividend_year="2025", industry="其他")
            assert r.verdict == "可持续"
            kw = mock_assess.call_args.kwargs
            assert kw["industry"] == "电力"             # 快照行业优先
            assert kw["financial_rows"] == [{"a": 1}]   # 反序列化为列表
            assert kw["price_change_1y"] == 0.1
            assert kw["dividend_fetch_failed"] is False

    def test_cache_miss_auto_fetch(self, tmp_path):
        """未命中缓存 → 按需取数（不注入快照数据，限流由调用方处理）。"""
        import src.sustainability as sus
        cache = ScreenerCache(tmp_path / "s.db")
        with patch("src.sustainability.assess_with_auto_fetch") as mock_assess:
            mock_assess.return_value = _FakeResult("可持续")
            r = sus.assess_from_cache(cache, "600900", total_shares=1e9,
                                      dividend_total=None, dividend_yield_before_tax=6.0,
                                      latest_dividend_year="2025", industry="电力")
            assert r.verdict == "可持续"
            kw = mock_assess.call_args.kwargs
            assert kw.get("financial_rows") is None     # 未注入，走现场取数
            assert kw["industry"] == "电力"
            assert kw.get("dividend_fetch_failed") is False


class TestMakeEvaluator:
    def test_returns_verdict_via_assessor(self):
        ev = make_sustainability_evaluator(None, assessor=lambda c: _FakeResult("可持续"))
        assert ev(_candidate()) == "可持续"

    def test_missing_dividend_returns_unassessed(self):
        ev = make_sustainability_evaluator(None, assessor=lambda c: _FakeResult("可持续"))
        assert ev(_candidate(dividend=None)) == "未评估"

    def test_fills_industry_from_snapshot(self, tmp_path):
        """纯缓存路径无行业：快照行业补进候选，供输出行使用（不走网络评估）。"""
        from unittest.mock import patch as _patch
        cache = ScreenerCache(tmp_path / "s.db")
        cache.upsert_sustainability(SustainabilitySnapshot(
            code="600900", financial_rows=None, cashflow_rows=None, dividend_rows=None,
            industry="电力", price_change_1y=None, top10_holding=None, source="预拉"))
        ev = make_sustainability_evaluator(cache, assessor=lambda c: _FakeResult("可持续"))
        c = _candidate()
        with _patch("src.sustainability.assess_with_auto_fetch") as mock_assess:
            mock_assess.return_value = _FakeResult("可持续")
            assert ev(c) == "可持续"
            # 缓存命中走注入路径：行业来自快照
            assert mock_assess.call_args.kwargs["industry"] == "电力"
        assert c.industry == "电力"
