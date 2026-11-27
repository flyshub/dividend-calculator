"""两条 Python 前瞻路径的对账测试（复盘改进 #1）。

背景：主页（`analysis` → `estimate_forward_yield`，按需取数）与选股页
（`screener_forward.make_forward_evaluator`，优先读缓存快照）是同一口径的两条
路径。2026-11 的 #160 事故：同一只股票主页算出 5.39%，选股页因缺快照判
`missing_inputs` 显示「—」，而所有单测都是绿的——因为没有测试同时跑这两条路径。

本测试用同一份输入分别走两条路径，断言 (status, year, yield) 完全一致，
覆盖「快照命中」「快照缺失（按需补拉）」「无覆盖」「取数失败」四种形态。
离线：三类取数全部注入，不触网。
"""
import json

import pytest

from src.forward_yield import STATUS_OK, estimate_forward_yield
from src.screener_cache import (
    DividendSnapshot,
    FinanceSnapshot,
    QuoteSnapshot,
    ScreenerCache,
    SustainabilitySnapshot,
)
from src.screener_forward import make_forward_evaluator
from src.screening import FunnelCandidate

pytestmark = pytest.mark.unit

SHARES = 1.0e9
PRICE = 1000.0


def _div_rows():
    return [
        {"REPORT_DATE": f"{y}-12-31", "PRETAX_BONUS_RMB": amt, "ASSIGN_PROGRESS": "实施",
         "EX_DIVIDEND_DATE": f"{y + 1}-06-20", "TOTAL_SHARES": SHARES}
        for y, amt in ((2025, 300.0), (2024, 285.0), (2023, 270.0))
    ]


def _fin_rows():
    return [{"REPORT_DATE": f"{y}-12-31", "ROEJQ": 15.0, "PARENTNETPROFIT": np_}
            for y, np_ in ((2025, 40e9), (2024, 38e9), (2023, 36e9))]


def _forecast_rows():
    return [{"YEAR1": 2025, "YEAR_MARK1": "A", "EPS1": 40.0,
             "YEAR2": 2026, "YEAR_MARK2": "E", "EPS2": 66.0, "RATING_ORG_NUM": 20}]


def _main_path(forecast_rows, forecast_fetcher=None):
    """主页路径：直接调用编排函数（输入全部给定，等价于页面一次请求内的计算）。"""
    kwargs = {"forecast_fetcher": forecast_fetcher} if forecast_fetcher else {}
    r = estimate_forward_yield(
        stock_code="600900", price=PRICE, total_shares=SHARES,
        dividend_rows=_div_rows(), financial_rows=_fin_rows(),
        forecast_rows=forecast_rows, trailing_yield=3.0, **kwargs)
    return (r.forward_status, r.forward_year, r.forward_yield_before_tax)


def _candidate(code="600900"):
    return FunnelCandidate(
        code=code,
        quote=QuoteSnapshot(code=code, name="长江电力", price=PRICE, total_shares=SHARES,
                            market_cap=PRICE * SHARES, source="腾讯"),
        dividend=DividendSnapshot(code=code, real_yield=3.0, ttm_yield=3.0,
                                  real_yield_year="2025", ttm_period="p",
                                  total_dividend=30e9, ttm_dividend=30e9,
                                  dividend_source="东财"),
        finance=FinanceSnapshot(code=code, roe_latest=15.0, roe_period="2025年报",
                                net_profit_annual=40e9, payout_ratio=0.75,
                                is_cyclical=False, finance_source="东财"),
    )


def _screener_path(tmp_path, *, seed_snapshot, forecast_rows):
    """选股页路径：经缓存快照 + 注入取数。"""
    cache = ScreenerCache(tmp_path / "s.db")
    if seed_snapshot:
        cache.upsert_sustainability(SustainabilitySnapshot(
            code="600900", financial_rows=json.dumps(_fin_rows()),
            dividend_rows=json.dumps(_div_rows()), industry="电力", source="东财"))
    ev = make_forward_evaluator(
        cache,
        forecast_fetcher=lambda code: forecast_rows,
        dividend_fetcher=lambda code: _div_rows(),
        financial_fetcher=lambda code: _fin_rows())
    r = ev(_candidate())
    return (r.forward_status, r.forward_year, r.forward_yield_before_tax)


class TestForwardParity:
    """两条路径对同一输入的结论必须一致（口径漂移即红）。"""

    def test_snapshot_hit(self, tmp_path):
        main = _main_path(_forecast_rows())
        screen = _screener_path(tmp_path, seed_snapshot=True,
                                forecast_rows=_forecast_rows())
        assert main == screen
        assert main[0] == STATUS_OK

    def test_snapshot_miss_fetches_on_demand(self, tmp_path):
        """#160 回归：缺快照时两条路径仍须一致（选股页按需补拉，不判缺失）。"""
        main = _main_path(_forecast_rows())
        screen = _screener_path(tmp_path, seed_snapshot=False,
                                forecast_rows=_forecast_rows())
        assert main == screen, "缺快照不得让选股页与主页给出不同结论"
        assert main[0] == STATUS_OK

    def test_no_coverage(self, tmp_path):
        main = _main_path([])
        screen = _screener_path(tmp_path, seed_snapshot=True, forecast_rows=[])
        assert main == screen
        assert main[0] == "no_coverage"

    def test_forecast_fetch_error(self, tmp_path):
        main = _main_path(None, forecast_fetcher=lambda code: None)
        screen = _screener_path(tmp_path, seed_snapshot=True, forecast_rows=None)
        assert main == screen
        assert main[0] == "fetch_error"
