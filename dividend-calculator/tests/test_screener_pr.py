"""选股器 PR 估值测试（spec #67，工单 #72，code-review 修复）。

覆盖 evaluate_stock_full（复用 calculate_pr）+ 漏斗③ 筛选，
全部注入 pr_provider，不碰网络。

先例：tests/test_pr_calculator.py（classify_valuation 边界）、tests/test_screener_cache.py。
"""
from unittest.mock import patch

import pytest

from src.pr import PRResult
from src.screener_cache import ScreenerCache
from src.screener_pr import (
    PR_ZONE_KEEP,
    evaluate_pr_batch,
    evaluate_stock_full,
    screen_pr,
)


@pytest.fixture(autouse=True)
def _no_rate_limit():
    """mock 限流等待，测试不 sleep。"""
    with patch("src.screener_rate_limit.batch_wait"):
        yield


def _pr(code="600900", pr_basic=0.5, zone="低估", industry="电力", roe=16.0):
    return PRResult(
        stock_code=code, stock_name="长江电力",
        pr_basic=pr_basic, pr_corrected=None, pr_pb=None,
        valuation_zone=zone, pe_ttm=8.0, pb=1.0,
        roe_latest=roe, roe_5y_median=roe,
        net_profit_latest_period=1e10, net_profit_annual=1e10,
        dividend_total=None, payout_ratio=0.5, n_factor=1.0,
        industry=industry, is_cyclical=False, is_tech=False, is_growth=False,
        is_loss_stock=False, pr_warning="", pe_pb_source="腾讯", finance_source="东财",
        industry_source="东财", errors=[],
    )


class TestEvaluateStockFull:
    def test_writes_finance_snapshot(self, tmp_path):
        cache = ScreenerCache(tmp_path / "s.db")
        r = evaluate_stock_full("600900", cache, pr_provider=lambda c: _pr())
        assert r["pass_pr"] is True
        assert r["valuation_zone"] == "低估"
        assert r["industry"] == "电力"
        # finance_snapshot 已写（code-review 修复：漏斗③ 不再空）
        fin = cache.get_finance("600900")
        assert fin is not None
        assert fin.roe_latest == pytest.approx(16.0)

    def test_rejects_reasonable(self, tmp_path):
        cache = ScreenerCache(tmp_path / "s.db")
        r = evaluate_stock_full("600900", cache,
                                pr_provider=lambda c: _pr(zone="合理"))
        assert r["pass_pr"] is False

    def test_none_provider_result(self, tmp_path):
        cache = ScreenerCache(tmp_path / "s.db")
        r = evaluate_stock_full("600900", cache, pr_provider=lambda c: None)
        assert r["pass_pr"] is False
        assert r["valuation_zone"] == "无法判定"

    def test_zone_keep(self):
        assert PR_ZONE_KEEP == ("合理偏低", "低估")


class TestEvaluatePrBatch:
    def test_batch_and_dividend_totals(self, tmp_path):
        cache = ScreenerCache(tmp_path / "s.db")
        calls = []
        def provider(code):
            calls.append(code)
            return _pr(code=code, zone="合理偏低")
        results = evaluate_pr_batch(
            ["600900", "600987"], cache, pr_provider=provider,
            dividend_totals={"600900": 1e9, "600987": 2e9})
        assert len(results) == 2
        assert len(calls) == 2
        assert all(r["pass_pr"] for r in results)

    def test_empty(self, tmp_path):
        cache = ScreenerCache(tmp_path / "s.db")
        assert evaluate_pr_batch([], cache) == []


class TestScreenPr:
    def test_keeps_low_and_reasonable_low(self):
        evals = [
            {"code": "a", "pass_pr": True, "valuation_zone": "低估"},
            {"code": "b", "pass_pr": True, "valuation_zone": "合理偏低"},
        ]
        assert len(screen_pr(evals)) == 2

    def test_filters_others(self):
        evals = [
            {"code": "a", "pass_pr": True, "valuation_zone": "低估"},
            {"code": "c", "pass_pr": False, "valuation_zone": "高估"},
        ]
        assert len(screen_pr(evals)) == 1

    def test_empty(self):
        assert screen_pr([]) == []
