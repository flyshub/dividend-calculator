"""选股器 PR 数据获取测试（spec #67，工单 #72，code-review 修复）。

覆盖 evaluate_pr_batch（纯缓存批量评估，供 scripts/fill_screener_data.py 预填）。
漏斗③ 判定与 PR 计算测试已随 ADR-0001 迁移到 test_screening.py。

先例：tests/test_pr_calculator.py（classify_valuation 边界）、tests/test_screener_cache.py。
"""
from unittest.mock import patch

import pytest

from src.screener_cache import ScreenerCache
from src.screener_pr import (
    PR_ZONE_KEEP,
    evaluate_pr_batch,
)


@pytest.fixture(autouse=True)
def _no_rate_limit():
    """mock 限流等待，测试不 sleep。"""
    with patch("src.screener_rate_limit.batch_wait"):
        yield


class TestEvaluatePrBatch:
    def test_batch_and_dividend_totals(self, tmp_path):
        from src.screener_cache import FinanceSnapshot, QuoteSnapshot
        cache = ScreenerCache(tmp_path / "s.db")
        # 纯缓存路径：需预填 quote_snapshot + finance_snapshot（ROE）
        for code in ["600900", "600987"]:
            cache.upsert_quote(QuoteSnapshot(code=code, name="x", price=10, pe_ttm=8.0,
                                             pb=1.0, total_shares=1e9, market_cap=1e10,
                                             quote_time="", source="腾讯"))
            cache.upsert_finance(FinanceSnapshot(code=code, roe_latest=16.0, roe_period="2025年报",
                                                 net_profit_annual=1e9, payout_ratio=0.5,
                                                 finance_source="东财"))
        results = evaluate_pr_batch(
            ["600900", "600987"], cache,
            dividend_totals={"600900": 1e9, "600987": 2e9})
        assert len(results) == 2
        assert all(r["pass_pr"] for r in results)
        # PE 8 / ROE 16 = PR 0.5 → 低估
        assert results[0]["valuation_zone"] == "低估"

    def test_skips_missing_roe(self, tmp_path):
        """缺 ROE 的股票被跳过（不调网络）。"""
        from src.screener_cache import QuoteSnapshot
        cache = ScreenerCache(tmp_path / "s.db")
        # 只有 quote，无 finance（缺 ROE）
        cache.upsert_quote(QuoteSnapshot(code="600900", name="x", price=10, pe_ttm=8.0,
                                         pb=1.0, total_shares=1e9, market_cap=1e10,
                                         quote_time="", source="腾讯"))
        results = evaluate_pr_batch(["600900"], cache)
        assert results == []  # 缺 ROE 跳过

    def test_uses_corrected_pr(self, tmp_path):
        """口径对齐漏斗③：payout_ratio=0.25 → N=2 → 修正 PR=1.0（合理偏低），非基础 PR=0.5（低估）。"""
        from src.screener_cache import FinanceSnapshot, QuoteSnapshot
        cache = ScreenerCache(tmp_path / "s.db")
        cache.upsert_quote(QuoteSnapshot(code="600900", name="x", price=10, pe_ttm=8.0,
                                         pb=1.0, total_shares=1e9, market_cap=1e10,
                                         quote_time="", source="腾讯"))
        cache.upsert_finance(FinanceSnapshot(code="600900", roe_latest=16.0, roe_period="2025年报",
                                             net_profit_annual=1e9, payout_ratio=0.25,
                                             finance_source="东财"))
        results = evaluate_pr_batch(["600900"], cache)
        assert results[0]["pr"] == 1.0
        assert results[0]["valuation_zone"] == "合理偏低"  # 修正 PR 生效：0.5(低估) → 1.0(合理偏低)
        assert results[0]["roe_used"] == 16.0  # 非周期股用 roe_latest

    def test_cyclical_uses_roe_5y_median(self, tmp_path):
        """周期股用 5 年 ROE 中位数（对齐 pr.py:460）：中位数 10 → PR=0.8，而非 roe_latest 16 → 0.5。"""
        from src.screener_cache import FinanceSnapshot, QuoteSnapshot
        cache = ScreenerCache(tmp_path / "s.db")
        cache.upsert_quote(QuoteSnapshot(code="600900", name="x", price=10, pe_ttm=8.0,
                                         pb=1.0, total_shares=1e9, market_cap=1e10,
                                         quote_time="", source="腾讯"))
        cache.upsert_finance(FinanceSnapshot(code="600900", roe_latest=16.0, roe_period="2025年报",
                                             net_profit_annual=1e9, payout_ratio=0.5,
                                             roe_5y_median=10.0, is_cyclical=True,
                                             finance_source="东财"))
        results = evaluate_pr_batch(["600900"], cache)
        assert results[0]["pr"] == 0.8  # 8/10，非 8/16
        assert results[0]["roe_used"] == 10.0  # 周期股用中位数
        assert results[0]["roe_latest"] == 16.0  # 原始字段保留

    def test_empty(self, tmp_path):
        cache = ScreenerCache(tmp_path / "s.db")
        assert evaluate_pr_batch([], cache) == []


def test_zone_keep():
    assert PR_ZONE_KEEP == ("合理偏低", "低估")
