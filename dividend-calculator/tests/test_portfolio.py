"""组合股息分析（#31）：解析、折算、聚合。

测试用合成输入（结构性 fixture），不伪造任何市场数据结论。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.portfolio import (  # noqa: E402
    Holding,
    PortfolioPosition,
    build_position,
    parse_holdings,
    summarize,
)


# ---------------------------------------------------------------------------
# parse_holdings
# ---------------------------------------------------------------------------

def test_parse_holdings_basic_and_fullwidth_comma():
    hs = parse_holdings("600900:1000，601398:5000")
    assert hs == [Holding("600900", 1000.0), Holding("601398", 5000.0)]


@pytest.mark.parametrize("text", [
    "600900",            # 缺股数
    "600900:0",          # 非正股数
    "600900:-100",
    ":1000",             # 缺代码
    "600900:abc",        # 股数非数字
    "",                  # 空
    " , ",               # 只有分隔符
])
def test_parse_holdings_rejects_bad_input(text):
    with pytest.raises(ValueError):
        parse_holdings(text)


# ---------------------------------------------------------------------------
# build_position
# ---------------------------------------------------------------------------

def _analysis(price=10.0, shares=1e9, dividend=5e8, name="测试股",
              industry="制造业", verdict="偏弱", score=60.0,
              pr_warning="", sus_warnings=()):
    """构造一次 run_stock_analysis 的返回值形状。"""
    return SimpleNamespace(
        stock_info=SimpleNamespace(current_price=price, total_shares=shares),
        dividend_total=dividend,
        pr_result=SimpleNamespace(stock_name=name, industry=industry,
                                  pr_warning=pr_warning),
        sustainability=SimpleNamespace(triggered=True, verdict=verdict, score_100=score,
                                       warning_flags=list(sus_warnings)),
    )


def test_build_position_derives_per_share_and_income():
    # 每股 0.5 元 × 2000 股 = 1000 元；市值 10 × 2000 = 20000；股息率 5%
    pos = build_position("600000", 2000, _analysis())
    assert pos.dividend_per_share == pytest.approx(0.5)
    assert pos.annual_income == pytest.approx(1000)
    assert pos.market_value == pytest.approx(20000)
    assert pos.dividend_yield == pytest.approx(5.0)
    assert pos.verdict == "偏弱" and pos.error is None


def test_build_position_flags_no_dividend_without_blocking():
    pos = build_position("600001", 100, _analysis(dividend=0))
    assert pos.error == "最新完整财年无现金分红"
    assert pos.annual_income is None
    assert pos.market_value == pytest.approx(1000)  # 市值仍可知


def test_build_position_handles_failed_analysis():
    pos = build_position("600002", 100, None)
    assert pos.error is not None
    assert pos.market_value is None


def test_build_position_collects_warnings_from_pr_and_sustainability():
    pos = build_position("600003", 100, _analysis(pr_warning="周期行业",
                                                  sus_warnings=["支付率偏高"]))
    assert pos.warnings == ["周期行业", "支付率偏高"]


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------

def _pos(code, income, mv, industry="制造业", verdict="可持续", error=None):
    return PortfolioPosition(stock_code=code, shares=1, market_value=mv,
                             annual_income=income, industry=industry,
                             verdict=verdict, error=error)


def test_summarize_weighted_yield_and_tax_branches():
    s = summarize([
        _pos("A", 1000, 20000, "银行"),
        _pos("B", 500, 30000, "电力"),
    ])
    assert s.total_market_value == pytest.approx(50000)
    assert s.total_annual_income == pytest.approx(1500)
    assert s.weighted_yield == pytest.approx(3.0)
    assert s.after_tax_income_10 == pytest.approx(1350)
    assert s.after_tax_income_20 == pytest.approx(1200)


def test_summarize_excludes_failed_positions_from_denominator():
    """失败持仓不能把加权股息率压低：它不进分子也不进分母。"""
    s = summarize([
        _pos("A", 1000, 20000, "银行"),
        _pos("FAIL", None, None, error="分析失败"),
    ])
    assert s.weighted_yield == pytest.approx(5.0)
    assert s.errors == ["FAIL: 分析失败"]
    assert s.ok_count == 1


def test_summarize_industry_breakdown_sorted_with_share():
    s = summarize([
        _pos("A", 1000, 20000, "银行"),
        _pos("B", 300, 10000, "电力"),
    ])
    assert [row[0] for row in s.industry_breakdown] == ["银行", "电力"]
    assert s.industry_breakdown[0][2] == pytest.approx(1000 / 1300 * 100)


def test_summarize_unknown_industry_and_unassessed_verdict():
    s = summarize([_pos("A", 100, 1000, industry=None, verdict=None)])
    assert s.industry_breakdown[0][0] == "未知行业"
    assert s.verdict_summary == {"未评估": 1}


def test_summarize_empty_has_no_yield():
    s = summarize([])
    assert s.weighted_yield is None
    assert s.total_market_value == 0 and s.total_annual_income == 0


def test_summarize_counts_dividend_free_holding_in_market_value():
    """「无分红」是真实事实（0 元），不是缺数据：它必须进市值分母，稀释加权股息率。

    2026-11 review 修正：此前把所有带 error 的持仓都排除，导致含无分红股的组合
    市值偏小、加权股息率虚高。
    """
    s = summarize([
        _pos("A", 1000, 20000, "银行"),
        _pos("Z", None, 20000, "电力", verdict="未评估", error="最新完整财年无现金分红"),
    ])
    assert s.total_market_value == pytest.approx(40000)
    assert s.total_annual_income == pytest.approx(1000)
    assert s.weighted_yield == pytest.approx(2.5)      # 不是 5.0
    assert s.errors == ["Z: 最新完整财年无现金分红"]
    # 行业分布只统计有分红的持仓（0 元不占份额）
    assert [row[0] for row in s.industry_breakdown] == ["银行"]


def test_parse_holdings_accepts_space_separator_like_js():
    assert parse_holdings("600900 1000") == [Holding("600900", 1000.0)]
    with pytest.raises(ValueError):
        parse_holdings("600900 1000 2000")


# ---------------------------------------------------------------------------
# verify_js_vs_python 的适配层（portfolio 对账）
# ---------------------------------------------------------------------------

def test_compute_portfolio_python_adapter_matches_summarize():
    """适配层（扁平字段 → build_position 输入）不得引入口径偏差。"""
    from scripts.verify_js_vs_python import PORTFOLIO_SHARES, compute_portfolio_python

    py_by_code = {
        "600900": {
            "stock_name": "长江电力", "current_price": 30.0, "total_shares": 1e9,
            "total_dividend": 3e8, "industry": "公用事业-电力-水电",
            "pr_warning": "", "sustainability_verdict": "可持续",
        },
        "600001": {   # 无完整财年分红
            "stock_name": "无分红股", "current_price": 10.0, "total_shares": 2e9,
            "total_dividend": 0, "industry": "制造业",
            "pr_warning": None, "sustainability_verdict": None,
        },
    }
    p = compute_portfolio_python(py_by_code)

    shares = PORTFOLIO_SHARES
    mv = 30.0 * shares + 10.0 * shares
    income = (3e8 / 1e9) * shares
    assert p["total_market_value"] == pytest.approx(mv)
    assert p["total_annual_income"] == pytest.approx(income)
    assert p["weighted_yield"] == pytest.approx(income / mv * 100)
    assert p["industry"] == [["公用事业-电力-水电", income]]
    # 无分红股市值仍在分母、但收入 0；verdict 计「未评估」
    assert p["verdicts"] == {"可持续": 1, "未评估": 1}
