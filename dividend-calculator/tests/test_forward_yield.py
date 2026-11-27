"""test_forward_yield.py — 下一年度前瞻股息率纯函数与编排单元测试（无网络）

对齐 docs/FORWARD_YIELD.md：口径 = 下一财年一致预期 EPS × 近 3 年支付率中位数 ÷ 现价。
纯函数用例的输入是参数化的单元 fixture（非市场数据），与 site/js/calculator.test.js
的用例一一对应，两侧同输入同期望。
"""
import pytest

from src.forward_yield import (
    ForwardYieldResult,
    compute_forward_yield,
    dividend_totals_by_year,
    estimate_forward_yield,
    net_profit_by_year,
    parse_forecast_row,
)

pytestmark = pytest.mark.unit

SHARES = 1.0e9


def _forecast(eps_2026=66.0, coverage=20, mark="E", base=2025):
    """与东财 RPT_WEB_RESPREDICT 同构的一行（YEAR{i} 财年，YEAR_MARK{i} A/E）。"""
    return [{
        "YEAR1": base, "YEAR_MARK1": "A", "EPS1": 40.0,  # 40e9 / 1e9 股，与基准年净利一致
        "YEAR2": base + 1, "YEAR_MARK2": mark, "EPS2": eps_2026,
        "RATING_ORG_NUM": coverage,
    }]


def _inputs(**over):
    base = dict(
        price=1000.0,
        latest_year=2025,
        yearly_dividend_totals={2025: 30e9, 2024: 28.5e9, 2023: 27e9},
        net_profit_by_year_map={2025: 40e9, 2024: 38e9, 2023: 36e9, 2026: 44e9},
        forecast_rows=_forecast(),
        is_cyclical=False,
        trailing_yield=None,
        total_shares=SHARES,
    )
    base.update(over)
    return base


# ---- 主路径 ----

def test_normal_estimate():
    r = compute_forward_yield(**_inputs())
    assert r.forward_year == 2026
    assert r.forward_eps_kind == "E"
    assert r.forward_eps == pytest.approx(66.0)
    assert r.forward_payout_assumed == pytest.approx(0.75)
    assert r.forward_dps == pytest.approx(49.5)
    assert r.forward_yield_before_tax == pytest.approx(4.95)
    assert r.forward_coverage == 20
    assert r.forward_status == "ok"
    assert r.forward_warnings == []
    assert r.forward_note == "2026E EPS 66.00 × 支付率 75.0% ÷ 现价 1000.00"


def test_median_of_even_samples():
    """只有两年样本时中位数取中间两数均值（statistics.median 语义）。"""
    r = compute_forward_yield(**_inputs(
        yearly_dividend_totals={2025: 20e9, 2024: 40e9},
        net_profit_by_year_map={2025: 40e9, 2024: 40e9},
    ))
    assert r.forward_payout_samples == pytest.approx([0.5, 1.0])
    assert r.forward_payout_assumed == pytest.approx(0.75)
    assert r.forward_status == "ok"
    assert any("样本不连续" in w for w in r.forward_warnings)


def test_payout_above_100pct_capped():
    """五粮液 2025 情境：某年支付率 >100%，中位数仍在 100% 内则按实际值。"""
    r = compute_forward_yield(**_inputs(
        yearly_dividend_totals={2025: 60e9, 2024: 28e9, 2023: 24e9},
        net_profit_by_year_map={2025: 26.8e9, 2024: 40e9, 2023: 38e9},
    ))
    assert r.forward_payout_assumed == pytest.approx(0.70)
    assert any("最新财年支付率 223.9% > 100%" in w for w in r.forward_warnings)
    assert any("偏离超 30 个百分点" in w for w in r.forward_warnings)


def test_payout_median_capped_at_100pct():
    r = compute_forward_yield(**_inputs(
        yearly_dividend_totals={2025: 50e9, 2024: 45e9, 2023: 40e9},
        net_profit_by_year_map={2025: 10e9, 2024: 10e9, 2023: 10e9},
    ))
    assert r.forward_payout_assumed == 1.0
    assert any("截断为 100%" in w for w in r.forward_warnings)
    assert r.forward_yield_before_tax == pytest.approx(6.6)


def test_annual_report_season_uses_actual_eps():
    """年报季 L 滞后一年：F=L+1 的一致预期已是实际值，标记 A。"""
    r = compute_forward_yield(**_inputs(
        latest_year=2024,
        yearly_dividend_totals={2024: 30e9, 2023: 28e9, 2022: 26e9},
        net_profit_by_year_map={2024: 40e9, 2023: 38e9, 2022: 36e9, 2025: 44e9},
        forecast_rows=_forecast(base=2025),
        total_shares=SHARES,
    ))
    assert r.forward_year == 2025
    assert r.forward_eps_kind == "A"


# ---- 不可估分支 ----

def test_no_complete_year():
    r = compute_forward_yield(**_inputs(latest_year=None))
    assert r.forward_year is None and r.forward_yield_before_tax is None
    assert r.forward_status == "no_complete_year"


def test_no_forecast_coverage():
    r = compute_forward_yield(**_inputs(forecast_rows=[]))
    assert r.forward_yield_before_tax is None
    assert r.forward_status == "no_coverage"
    assert r.forward_year == 2026  # 目标财年可确定，UI 才能显示「FY2026E 无预测」
    assert r.forward_note == "无机构盈利预测，未做前瞻估算"


def test_forecast_fetch_failed():
    r = compute_forward_yield(**_inputs(forecast_rows=None))
    assert r.forward_yield_before_tax is None
    assert r.forward_status == "fetch_error"
    assert r.forward_note == "前瞻取数失败（盈利预测），稍后重试"


def test_forecast_loss():
    r = compute_forward_yield(**_inputs(forecast_rows=_forecast(eps_2026=-1.09)))
    assert r.forward_year == 2026 and r.forward_eps is None
    assert r.forward_status == "no_positive_eps"
    assert r.forward_note == "机构预测 2026 年亏损，不估股息率"


def test_forecast_missing_target_year():
    rows = _forecast()
    rows[0]["YEAR2"] = 2027
    r = compute_forward_yield(**_inputs(forecast_rows=rows))
    assert r.forward_year == 2026
    assert r.forward_status == "no_target_year"
    assert r.forward_note == "无 2026 年盈利预测"


def test_stale_dividend_history():
    """隆基情境：停发后一致预期基准年领先分红财年 ≥2 年。"""
    r = compute_forward_yield(**_inputs(
        latest_year=2023,
        yearly_dividend_totals={2023: 10e9},
        net_profit_by_year_map={2023: 10e9},
        forecast_rows=_forecast(base=2025, mark="A"),
    ))
    assert r.forward_yield_before_tax is None
    assert r.forward_status == "stale"
    assert r.forward_year is None  # latest+1 已是过去年份，不能当前瞻财年
    assert "近年无分红" in r.forward_note


def test_eps_share_mismatch_warning_survives_early_return():
    """股本口径校验用基准年 A 值，且在不估（无分红史）时仍给出。"""
    r = compute_forward_yield(**_inputs(
        yearly_dividend_totals={},
        net_profit_by_year_map={2025: 40e9, 2024: 38e9, 2023: 36e9},
        total_shares=1.5e9,  # 40 × 1.5e9 = 60e9 vs 40e9 → 50%
    ))
    assert r.forward_status == "no_payout_history"
    assert any("股本口径可能不一致" in w for w in r.forward_warnings)


def test_payout_trend_deviation_warning():
    """最新财年支付率与中位数偏离 >30pct → 分红政策可能变化。"""
    r = compute_forward_yield(**_inputs(
        yearly_dividend_totals={2025: 16e9, 2024: 30e9, 2023: 30e9},
        net_profit_by_year_map={2025: 40e9, 2024: 40e9, 2023: 40e9},
    ))
    # 样本 0.4 / 0.75 / 0.75 → 中位数 0.75，最新 0.4 偏离 35pct
    assert r.forward_payout_assumed == pytest.approx(0.75)
    assert any("分红政策可能变化" in w for w in r.forward_warnings)
    assert r.forward_status == "ok"


def test_no_profit_history():
    r = compute_forward_yield(**_inputs(net_profit_by_year_map={}))
    assert r.forward_yield_before_tax is None
    assert r.forward_status == "no_payout_history"
    assert r.forward_note == "无可用分红历史，不做前瞻"


def test_missing_price():
    r = compute_forward_yield(**_inputs(price=None))
    assert r.forward_yield_before_tax is None
    assert r.forward_status == "missing_inputs"
    assert r.forward_note == "缺少现价，不做前瞻"


# ---- 警示 ----

def test_cyclical_warning():
    r = compute_forward_yield(**_inputs(is_cyclical=True))
    assert any("周期行业" in w for w in r.forward_warnings)


def test_low_coverage_warning():
    r = compute_forward_yield(**_inputs(forecast_rows=_forecast(coverage=2)))
    assert any("仅 2 家机构覆盖" in w for w in r.forward_warnings)


def test_deviation_from_trailing_warning():
    r = compute_forward_yield(**_inputs(trailing_yield=10.0))
    assert any("偏离超 50%" in w for w in r.forward_warnings)


def test_yield_sanity_upper_bound():
    r = compute_forward_yield(**_inputs(
        price=100.0,
        yearly_dividend_totals={2025: 50e9, 2024: 45e9, 2023: 40e9},
        net_profit_by_year_map={2025: 10e9, 2024: 10e9, 2023: 10e9},
    ))
    assert any("超合理上限" in w for w in r.forward_warnings)


def test_eps_share_mismatch_warning():
    """A 类 EPS × 股本 与归母净利偏差 >3% → 股本口径警示。"""
    r = compute_forward_yield(**_inputs(
        latest_year=2024,
        yearly_dividend_totals={2024: 30e9, 2023: 28e9, 2022: 26e9},
        net_profit_by_year_map={2024: 40e9, 2023: 38e9, 2022: 36e9, 2025: 44e9},
        forecast_rows=_forecast(base=2025),
        total_shares=1.2e9,
    ))
    assert any("股本口径可能不一致" in w for w in r.forward_warnings)


# ---- 组装层（分年总额 / 净利映射 / 编排） ----

def test_dividend_totals_use_per_record_shares():
    """每笔分红按各自登记股本折算（#302），缺失回退当前股本。"""
    from src.datasource.base import DividendRecord
    recs = [
        DividendRecord(report_time="2025年报", dividend_per_10=10.0, ex_dividend_date="2026-07-01",
                       total_shares=1.5e9),
        DividendRecord(report_time="2025中期分配", dividend_per_10=2.0, ex_dividend_date="2025-12-01",
                       total_shares=None),
    ]
    totals = dividend_totals_by_year(recs, SHARES)
    assert totals[2025] == pytest.approx(10.0 / 10 * 1.5e9 + 2.0 / 10 * SHARES)


def test_net_profit_by_year_skips_missing():
    class Fin:
        def __init__(self, year, np_):
            self.year, self.net_profit = year, np_

    m = net_profit_by_year([Fin(2025, 40e9), Fin(2024, None)])
    assert m == {2025: 40e9}


def test_parse_forecast_row_picks_latest_actual_base():
    rows = [{
        "YEAR1": 2024, "YEAR_MARK1": "A", "EPS1": 1.0,
        "YEAR2": 2025, "YEAR_MARK2": "A", "EPS2": 1.1,
        "YEAR3": 2026, "YEAR_MARK3": "E", "EPS3": 1.2,
        "RATING_ORG_NUM": 5,
    }]
    f = parse_forecast_row(rows)
    assert f["base_year"] == 2025
    assert f["eps_by_year"] == {2024: 1.0, 2025: 1.1, 2026: 1.2}
    assert f["coverage"] == 5


def _div_rows():
    return [
        {"REPORT_DATE": "2025-12-31", "PRETAX_BONUS_RMB": 300.0, "ASSIGN_PROGRESS": "实施",
         "EX_DIVIDEND_DATE": "2026-06-20", "TOTAL_SHARES": SHARES},
        {"REPORT_DATE": "2024-12-31", "PRETAX_BONUS_RMB": 285.0, "ASSIGN_PROGRESS": "实施",
         "EX_DIVIDEND_DATE": "2025-06-20", "TOTAL_SHARES": SHARES},
        {"REPORT_DATE": "2023-12-31", "PRETAX_BONUS_RMB": 270.0, "ASSIGN_PROGRESS": "实施",
         "EX_DIVIDEND_DATE": "2024-06-20", "TOTAL_SHARES": SHARES},
    ]


def _fin_rows():
    return [
        {"REPORT_DATE": f"{y}-12-31", "ROEJQ": 15.0, "PARENTNETPROFIT": np_}
        for y, np_ in ((2025, 40e9), (2024, 38e9), (2023, 36e9))
    ]


def test_estimate_forward_yield_end_to_end():
    r = estimate_forward_yield(
        stock_code="000000", price=1000.0, total_shares=SHARES,
        dividend_rows=_div_rows(), financial_rows=_fin_rows(), forecast_rows=_forecast(),
    )
    assert r.forward_yield_before_tax == pytest.approx(4.95)


def test_estimate_without_shares_still_estimates():
    """股本只服务 EPS×股本 口径守卫，缺失时不该阻断估算（页面 quote 无股本也会算）。"""
    r = estimate_forward_yield(
        stock_code="000000", price=1000.0, total_shares=None,
        dividend_rows=_div_rows(), financial_rows=_fin_rows(), forecast_rows=_forecast(),
    )
    assert r.forward_status == "ok"
    assert r.forward_yield_before_tax == pytest.approx(4.95)
    assert not [w for w in r.forward_warnings if "股本口径可能不一致" in w]


def test_missing_price_keeps_context_warnings():
    """缺现价时也要先攒覆盖/周期警示：与 JS computeForwardYield 同序，verify 对账要求集合一致。"""
    r = compute_forward_yield(
        price=None,
        latest_year=2025,
        yearly_dividend_totals={2025: 30e9, 2024: 28.5e9, 2023: 27e9},
        net_profit_by_year_map={2025: 40e9, 2024: 38e9, 2023: 36e9},
        forecast_rows=[{
            "YEAR1": 2025, "YEAR_MARK1": "A", "EPS1": 40.0,
            "YEAR2": 2026, "YEAR_MARK2": "E", "EPS2": 66.0,
            "RATING_ORG_NUM": 1,
        }],
        is_cyclical=True,
        total_shares=1e9,
    )
    assert r.forward_status == "missing_inputs"
    assert r.forward_warnings == [
        "一致预期仅 1 家机构覆盖（样本少）",
        "周期行业，盈利预测波动大，前瞻仅供参考",
    ]
    assert r.forward_yield_before_tax is None


def test_estimate_forward_yield_missing_inputs_fail_soft():
    r = estimate_forward_yield(
        stock_code="000000", price=None, total_shares=SHARES,
        dividend_rows=_div_rows(), financial_rows=_fin_rows(), forecast_rows=_forecast())
    assert r.forward_note == "缺少现价，不做前瞻"
    # 契约 §5.1：缺现价时未取分红，财年不可确定 → 空（不猜 L+1）
    assert r.forward_year is None

    r = estimate_forward_yield(
        stock_code="000000", price=10.0, total_shares=SHARES,
        dividend_rows=None, financial_rows=_fin_rows(), forecast_rows=_forecast(),
        dividend_fetcher=lambda code: None,
    )
    assert r.forward_note == "前瞻取数失败（分红明细），稍后重试"
    assert r.forward_year is None, "分红明细失败时财年未知"


def test_estimate_forward_yield_keeps_year_when_known_on_fetch_error():
    """契约 §5.1：财年已知（分红明细到手）时，财务/预测失败仍给 L+1。"""
    r = estimate_forward_yield(
        stock_code="000000", price=10.0, total_shares=SHARES,
        dividend_rows=_div_rows(), forecast_rows=_forecast(),
        financial_fetcher=lambda code: None,
    )
    assert r.forward_status == "fetch_error"
    assert r.forward_year == 2026

    assert estimate_forward_yield(
        stock_code="000000", price=10.0, total_shares=SHARES,
        dividend_rows=_div_rows(), financial_rows=None, forecast_rows=_forecast(),
        financial_fetcher=lambda code: None,
    ).forward_note == "前瞻取数失败（财务数据），稍后重试"

    r = estimate_forward_yield(
        stock_code="000000", price=10.0, total_shares=SHARES,
        dividend_rows=_div_rows(), financial_rows=_fin_rows(), forecast_rows=None,
        forecast_fetcher=lambda code: None,
    )
    assert r.forward_status == "fetch_error"
    assert r.forward_note == "前瞻取数失败（盈利预测），稍后重试"
    assert isinstance(r, ForwardYieldResult)
