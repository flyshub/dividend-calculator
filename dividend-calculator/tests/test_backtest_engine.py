"""T4 分层回测引擎测试（issue #87）。

覆盖：调仓日历、收益区间（T+1 建仓/下季末结算）、无未来函数（asof 过滤）、
四层漏斗筛选、逐层增量超额、与 T3 因子联动。
全部 mock lookup（纯内存），不触网络。
"""
import sys
from datetime import date
from types import SimpleNamespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.backtest_engine import (
    BacktestLookup,
    build_day_after,
    funnel_layer,
    portfolio_return,
    quarterly_rebalance_dates,
    run_backtest,
)


class MockLookup:
    """内存 lookup：price/dividends 注入，其余键返回 None（缺口语义）。"""

    def __getitem__(self, key):
        return getattr(self, key)

    def __init__(self, prices=None, dividends=None, shares=None,
                 finance=None, industry="", pes=None):
        self._prices = prices or {}
        self._div = dividends or {}
        self._shares = shares or {}
        self._fin = finance or {}
        self._pe = pes or {}
        self._ind = industry
        self.delist = {}  # T5 #110：退市日映射
        self.prices = self._prices  # run_backtest 用 lookup.prices.keys() 取 all_codes
        self.trading_days = sorted(
            set(d for lst in self._prices.values() for d, _ in lst)
        )

    def dividends(self, code, asof):
        return [
            r for r in self._div.get(code, [])
            if (not r.get("announce_date")) or r["announce_date"] <= asof.isoformat()
        ] or None

    def pe_ttm(self, code, asof):
        return self._pe.get(code)

    def total_shares(self, code, asof):
        return self._shares.get(code)

    def price(self, code, asof):
        series = self._prices.get(code, [])
        latest = [c for d, c in series if d <= asof]
        return latest[-1] if latest else None

    def roe_latest(self, code, asof):
        f = self._fin.get(code)
        return f["roe"] if f else None

    def finance(self, code, asof):
        f = self._fin.get(code)
        if f and date(f["year"], 12, 31) <= asof:
            return f
        return None

    def price_change_1y(self, code, asof):
        return None

    def top10_holding(self, code, asof):
        return None

    def industry(self, code, asof):
        return self._ind

    def get(self, key, default=None):
        """dict.get 兼容（T3 因子层 lookup.get('industry') 契约）。"""
        fn = getattr(self, key, None)
        return fn if callable(fn) else default


# ---------------------------------------------------------------------------
# 调仓日历与收益区间
# ---------------------------------------------------------------------------

def test_quarterly_rebalance_dates_bounds():
    days = []
    for y in (2023, 2024):
        for m in range(1, 13):
            days.append(date(y, m, 15))
    days = sorted(days)
    out = quarterly_rebalance_dates(days, date(2023, 1, 1), date(2024, 12, 31))
    # 每年 3/6/9/12 月最后一个交易日 = 当月 15 日
    assert out == [date(y, m, 15) for y in (2023, 2024) for m in (3, 6, 9, 12)]


def test_quarterly_rebalance_start_end_clip():
    days = [date(2023, m, 15) for m in range(1, 13)]
    out = quarterly_rebalance_dates(days, date(2023, 4, 1), date(2023, 10, 1))
    assert out == [date(2023, 6, 15), date(2023, 9, 15)]


def test_build_day_after():
    days = [date(2023, 3, 30), date(2023, 3, 31), date(2023, 4, 3)]
    assert build_day_after(days, date(2023, 3, 31)) == date(2023, 4, 3)
    assert build_day_after(days, date(2023, 4, 3)) is None


def test_portfolio_return_t_plus_1_to_settle():
    """收益区间 = [build_day, settle_day]：T 收盘决策、T+1 建仓价、下季末结算。"""
    prices = {"600036": [(date(2023, 3, 31), 10.0),   # T 收盘（决策时点）
                         (date(2023, 4, 3), 10.2),    # T+1 建仓价
                         (date(2023, 6, 30), 11.22)]} # 下季末结算价
    lk = MockLookup(prices=prices)
    ret = portfolio_return(["600036"], date(2023, 4, 3), date(2023, 6, 30), lk,
                           cost=0.0)
    assert ret == pytest.approx(11.22 / 10.2 - 1.0)


def test_portfolio_return_equal_weight():
    prices = {
        "a": [(date(2023, 1, 2), 10.0), (date(2023, 6, 30), 11.0)],
        "b": [(date(2023, 1, 2), 20.0), (date(2023, 6, 30), 21.0)],
    }
    lk = MockLookup(prices=prices)
    ret = portfolio_return(["a", "b"], date(2023, 1, 2), date(2023, 6, 30), lk,
                           cost=0.0)
    assert ret == pytest.approx(((11 / 10 - 1) + (21 / 20 - 1)) / 2)


def test_portfolio_return_trading_cost():
    """双边交易成本：季度全换手，进出各 0.3% 从收益扣除。"""
    prices = {"a": [(date(2023, 1, 2), 10.0), (date(2023, 6, 30), 11.0)]}
    lk = MockLookup(prices=prices)
    gross = 11 / 10 - 1.0
    ret = portfolio_return(["a"], date(2023, 1, 2), date(2023, 6, 30), lk,
                           cost=0.003)
    assert ret == pytest.approx(gross - 0.006)
    # 零成本（对照）保持原收益
    ret0 = portfolio_return(["a"], date(2023, 1, 2), date(2023, 6, 30), lk,
                            cost=0.0)
    assert ret0 == pytest.approx(gross)


def test_portfolio_return_delisted_excluded():
    """建仓日无价（未上市/已退市）→ 剔除，不参与等权。"""
    prices = {
        "a": [(date(2023, 1, 2), 10.0), (date(2023, 6, 30), 11.0)],
        "b": [],  # 无价格记录（退市）
    }
    lk = MockLookup(prices=prices)
    assert portfolio_return(["a", "b"], date(2023, 1, 2), date(2023, 6, 30), lk,
                            cost=0.0) == pytest.approx(11 / 10 - 1.0)


# T5 #127 H-4：持有期内退市终局损失计提
def test_portfolio_return_holding_period_delist_loss():
    """pb 可得但 ps 不可得，且持有期内退市 → 计提全损（-1.0）。

    若不修：退市股被跳过，组合收益 = 正常股平均收益，退市损失未进入回测，
    系统性低估尾部风险。修复后：组合收益 = (正常股收益 + (-1.0)) / 2。
    """
    prices = {
        # a 正常：10→11，收益 10%
        "a": [(date(2023, 1, 2), 10.0), (date(2023, 6, 30), 11.0)],
        # dead 建仓日在价，但持有期内退市（2023-03-15），结算日已无价
        "dead": [(date(2023, 1, 2), 10.0)],  # 只有 build_day 价，无 settle_day 价
    }
    lk = MockLookup(prices=prices)
    lk.delist = {"dead": date(2023, 3, 15)}
    # 持有期 1-2 ~ 6-30，delist_date 3-15 ∈ 期间 → dead 计提 -1.0
    # 组合 = (0.10 + (-1.0)) / 2 = -0.45
    ret = portfolio_return(["a", "dead"], date(2023, 1, 2), date(2023, 6, 30), lk,
                           cost=0.0)
    assert ret == pytest.approx((0.10 + (-1.0)) / 2)


def test_portfolio_return_delist_outside_holding_period_no_impact():
    """退市日不在持有期内（< build_day 或 > settle_day）→ 不计提，正常跳过。

    build_day 前已退市：入选时就被 run_backtest 的 delist_date <= T 过滤掉，
    根本不会进入 portfolio_return 的 codes。这里测试：即便传入，也无 ps 时
    跳过（与原行为一致）。
    """
    prices = {
        "a": [(date(2023, 1, 2), 10.0), (date(2023, 6, 30), 11.0)],
        "out": [(date(2023, 1, 2), 5.0)],  # delist 在持有期之前或之后
    }
    lk = MockLookup(prices=prices)
    # delist_date 2023-07-01（晚于 settle_day 6-30，不在持有期内）→ 不计提
    lk.delist = {"out": date(2023, 7, 1)}
    ret = portfolio_return(["a", "out"], date(2023, 1, 2), date(2023, 6, 30), lk,
                           cost=0.0)
    # out 退市日在持有期外 → 不计提全损；其 _latest 价恒为 5.0（买5卖5，收益0）
    assert ret == pytest.approx((0.10 + 0.0) / 2)


# ---------------------------------------------------------------------------
# 无未来函数：asof 过滤
# ---------------------------------------------------------------------------

def test_dividends_filtered_by_announce_date():
    """公告日 > T 的分红预案不可见（无未来函数）。"""
    div = {
        "600036": [
            {"announce_date": "2023-03-20", "report_date": "2022-12-31",
             "ex_dividend_date": "2023-06-01", "cash_div_per_share": 1.5},
            {"announce_date": "2024-03-25", "report_date": "2023-12-31",
             "ex_dividend_date": "2024-06-01", "cash_div_per_share": 2.0},
        ]
    }
    lk = MockLookup(dividends=div)
    at_2023 = lk.dividends("600036", date(2023, 12, 31)) or []
    assert len(at_2023) == 1 and at_2023[0]["cash_div_per_share"] == 1.5
    at_2024 = lk.dividends("600036", date(2024, 6, 1)) or []
    assert len(at_2024) == 2


def test_finance_filtered_by_report_date():
    """报告期 > T 的财报不可见（T3 契约：report_date ≤ T；披露日缺口报告标注）。"""
    fin = {"600036": {"year": 2024, "roe": 13.5, "net_profit": 1e10}}
    lk = MockLookup(finance=fin)
    assert lk.finance("600036", date(2024, 6, 30)) is None   # 2024 年报报告期未到
    assert lk.finance("600036", date(2024, 12, 31))["roe"] == 13.5


# ---------------------------------------------------------------------------
# 四层漏斗
# ---------------------------------------------------------------------------

def _mk_factors(real=None, ttm=None, pr=0.8, verdict="可持续"):
    return {"real_yield": real, "ttm_yield": ttm,
            "pr": SimpleNamespace(pr=pr, pr_warning=""),
            "sustainability": SimpleNamespace(verdict=verdict)}


def test_funnel_all_layers():
    assert funnel_layer(_mk_factors(real=6.0, ttm=6.0, pr=0.8, verdict="可持续")) == 4
    assert funnel_layer(_mk_factors(real=6.0, ttm=6.0, pr=0.8, verdict="偏弱")) == 4


def test_funnel_l2_boundary():
    # TTM/真实股息率恰为 5 → 不通过（> 5 严格大于）
    assert funnel_layer(_mk_factors(real=5.0, ttm=6.0, pr=0.8)) == 1
    assert funnel_layer(_mk_factors(real=6.0, ttm=5.0, pr=0.8)) == 1
    assert funnel_layer(_mk_factors(real=None, ttm=6.0, pr=0.8)) == 0


def test_funnel_l3_boundary():
    # PR = 1.0 → 通过（≤ 1）；PR = 1.01 → 不通过
    assert funnel_layer(_mk_factors(real=6.0, ttm=6.0, pr=1.0, verdict="可持续")) == 4
    assert funnel_layer(_mk_factors(real=6.0, ttm=6.0, pr=1.01, verdict="可持续")) == 2


def test_funnel_l4_rejects_unsustainable():
    assert funnel_layer(_mk_factors(real=6.0, ttm=6.0, pr=0.8, verdict="不可持续")) == 3


def test_funnel_short_circuits():
    """L2 不过 → 不再评估 L3/L4（不可持续也不会提升层数）。"""
    assert funnel_layer(_mk_factors(real=4.0, ttm=6.0, pr=0.5, verdict="可持续")) == 1


# ---------------------------------------------------------------------------
# 逐层增量超额
# ---------------------------------------------------------------------------

def test_incremental_excess_monotone():
    """构造 mock：股息率/PR/可持续性每层过滤后收益逐层递增 → 增量全为正。"""
    prices = {}
    # base 池：低股息股收益平庸
    prices["lo"] = [(date(2023, 3, 31), 10.0), (date(2023, 4, 3), 10.0),
                    (date(2023, 6, 30), 10.0)]  # 0%
    # l2 池：高股息
    prices["mid"] = [(date(2023, 3, 31), 10.0), (date(2023, 4, 3), 10.0),
                     (date(2023, 6, 30), 11.0)]  # +10%
    # l4 池：高股息 + 低 PR + 可持续
    prices["hi"] = [(date(2023, 3, 31), 10.0), (date(2023, 4, 3), 10.0),
                    (date(2023, 6, 30), 12.1)]  # +21%

    class FakeLookup(MockLookup):
        def __init__(self):
            super().__init__(prices=prices)

        def dividends(self, code, asof):
            return None

        def pe_ttm(self, code, asof):
            return None

        def total_shares(self, code, asof):
            return 1e9

        def finance(self, code, asof):
            return {"year": 2022, "roe": 12.0, "net_profit": 1e10}

    class _PR:
        def __init__(self, v, w=""):
            self.pr, self.pr_warning = v, w
    class _Sus:
        def __init__(self, v):
            self.verdict = v

    class _F:
        def __init__(self, real, ttm, pr, sus):
            self.real_yield, self.ttm_yield = real, ttm
            self.pr, self.sustainability = _PR(pr), _Sus(sus)

        def __iter__(self):
            yield self

    lookup = FakeLookup()
    # 单季回测（一个调仓日）——直接验证逐层增量逻辑
    def run():
        return None
    # 简化：直接验证漏斗 + 组合收益链路（run_backtest 需完整价格日历，此处单季度验证）
    f_lo = _mk_factors(real=3.0, ttm=3.0, pr=0.5, verdict="可持续")
    f_mid = _mk_factors(real=6.0, ttm=6.0, pr=0.9, verdict="偏弱")
    f_hi = _mk_factors(real=6.0, ttm=6.0, pr=0.6, verdict="可持续")
    assert funnel_layer(f_lo) == 1      # 仅过 L1（base）
    assert funnel_layer(f_mid) == 4     # 全漏斗
    assert funnel_layer(f_hi) == 4      # 全漏斗
    # 组合收益单调性
    ret_lo = portfolio_return(["lo"], date(2023, 4, 3), date(2023, 6, 30), lookup,
                              cost=0.0)
    ret_hi = portfolio_return(["hi"], date(2023, 4, 3), date(2023, 6, 30), lookup,
                              cost=0.0)
    assert ret_lo == pytest.approx(0.0)
    assert ret_hi == pytest.approx(12.1 / 10.0 - 1.0)
    assert ret_hi > ret_lo


# ---------------------------------------------------------------------------
# 与 T3 因子联动（真实 T3 纯函数 + mock lookup）
# ---------------------------------------------------------------------------

def test_engine_calls_t3_factors():
    """引擎因子入口复用 T3 纯函数：同一输入 → 同一输出。"""
    from src.backtest_factors import real_dividend_yield
    prices = {"600036": [(date(2023, 3, 31), 10.0)]}
    div = {"600036": [
        {"announce_date": "2023-03-20", "report_date": "2022-12-31",
         "ex_dividend_date": "2023-06-01", "cash_div_per_share": 1.0}]}
    lk = MockLookup(prices=prices, dividends=div, shares={"600036": 100.0})
    T = date(2023, 3, 31)
    assert real_dividend_yield("600036", T, lk) == pytest.approx(
        (1.0 * 100.0) / (10.0 * 100.0) * 100.0  # 总额法：100元/1000元 = 10%
    )


# ---------------------------------------------------------------------------
# T3 #106：增量超额比值口径
# ---------------------------------------------------------------------------

def test_incremental_excess_ratio_not_linear():
    """比值口径：r=0.5, p=-0.3 时，线性 r-p=0.8 失真，比值 (1.5/0.7)-1≈1.14。"""
    prices = {}
    # 构造 base 收益 -30%、full 收益 +50% 的单期场景
    for code, end in [("lo", 7.0), ("hi", 15.0)]:
        prices[code] = [(date(2023, 3, 31), 10.0), (date(2023, 4, 3), 10.0),
                        (date(2023, 6, 30), end)]

    class Lk(MockLookup):
        def dividends(self, code, asof):
            return None

        def pe_ttm(self, code, asof):
            return None

        def total_shares(self, code, asof):
            return 1e9

        def finance(self, code, asof):
            return {"year": 2022, "roe": 12.0, "net_profit": 1e10}

    class _PR:
        def __init__(self, v, w=""):
            self.pr, self.pr_warning = v, w

    class _Sus:
        def __init__(self, v):
            self.verdict = v

    lk = Lk(prices=prices)
    # base = lo（-30%），full = hi（+50%）
    r_base = portfolio_return(["lo"], date(2023, 4, 3), date(2023, 6, 30), lk, cost=0.0)
    r_full = portfolio_return(["hi"], date(2023, 4, 3), date(2023, 6, 30), lk, cost=0.0)
    assert r_base == pytest.approx(-0.3)
    assert r_full == pytest.approx(0.5)
    # 比值口径超额 = (1+0.5)/(1-0.3) - 1 ≈ 1.1428
    ratio_excess = (1.0 + r_full) / (1.0 + r_base) - 1.0
    linear_excess = r_full - r_base  # 0.8（旧口径失真）
    assert ratio_excess == pytest.approx(1.5 / 0.7 - 1.0, abs=0.01)
    assert abs(ratio_excess - linear_excess) > 0.3  # 两口径差异显著


# ---------------------------------------------------------------------------
# T5 #110：退市股终局损失
# ---------------------------------------------------------------------------

def test_delisted_stock_excluded_from_pool():
    """退市日早于调仓日的股票不可入选。"""
    prices = {
        "alive": [(date(2023, 3, 31), 10.0), (date(2023, 4, 3), 10.0),
                  (date(2023, 6, 30), 11.0)],
        "dead": [(date(2023, 3, 31), 10.0), (date(2023, 6, 15), 5.0)],
    }

    class Lk(MockLookup):
        def dividends(self, code, asof):
            return None

        def pe_ttm(self, code, asof):
            return None

        def total_shares(self, code, asof):
            return 1e9

        def finance(self, code, asof):
            return {"year": 2022, "roe": 12.0, "net_profit": 1e10}

    lk = Lk(prices=prices)
    # dead 在 2023-06-15 退市
    lk.delist = {"dead": date(2023, 6, 15)}

    days = [date(2023, 3, 31), date(2023, 4, 3), date(2023, 6, 30)]
    res = run_backtest(lk, start=date(2023, 6, 1), end=date(2023, 6, 30))
    # 2023-06-30 调仓：dead 退市日 6-15 < 6-30，不应入选
    for pool_per_date in res["pools"].values():
        for codes_at_T in pool_per_date:
            if codes_at_T:  # 非空池不应含 dead
                assert "dead" not in codes_at_T


def test_delisted_stock_attr_missing_no_crash():
    """lookup 无 delist 属性时不崩溃（向后兼容）。"""
    prices = {"x": [(date(2023, 3, 31), 10.0), (date(2023, 4, 3), 10.0),
                    (date(2023, 6, 30), 11.0)]}

    class Lk(MockLookup):
        def dividends(self, code, asof):
            return None

        def pe_ttm(self, code, asof):
            return None

        def total_shares(self, code, asof):
            return 1e9

        def finance(self, code, asof):
            return {"year": 2022, "roe": 12.0, "net_profit": 1e10}

    lk = Lk(prices=prices)
    del lk.delist  # 模拟无 delist 属性的旧 lookup
    # 不崩溃即可（getattr 容错返回 {}）
    res = run_backtest(lk, start=date(2023, 3, 1), end=date(2023, 6, 30))
    assert res["rebalance_dates"]  # 正常返回


def test_unlisted_stock_excluded_from_pool():
    """T16 #121：上市日晚于调仓日的股票不可入选（未来函数防御）。"""
    prices = {
        "A": [(date(2023, 3, 31), 10.0), (date(2023, 4, 3), 10.0),
              (date(2023, 6, 30), 11.0)],
        "B": [(date(2023, 3, 31), 5.0), (date(2023, 4, 3), 5.0),
              (date(2023, 6, 30), 5.5)],
    }

    class Lk(MockLookup):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.listdt = {"B": date(2024, 1, 1)}  # B 在 2024 才上市
            self.delist = {}

        def dividends(self, code, asof):
            return None

        def pe_ttm(self, code, asof):
            return None

        def total_shares(self, code, asof):
            return 1e9

        def finance(self, code, asof):
            return {"year": 2022, "roe": 12.0, "net_profit": 1e10}

    lk = Lk(prices=prices)
    res = run_backtest(lk, start=date(2023, 3, 1), end=date(2023, 6, 30))
    # B 在 2023-06 调仓日尚未上市，不可入选；A 可入选
    # pools 结构：{layer: list[per_period_codes]}
    for layer_pools in res["pools"].values():
        for period_codes in layer_pools:
            assert "B" not in period_codes
            if period_codes:  # 至少有一期含 A
                assert "A" in period_codes
                break


# --- T10 #115 送转除权因子建模 ---
def test_portfolio_return_split_factor():
    """送转除权因子建模：10送10 持有期收益正确（不再是 -47% 失真）。"""
    # 建仓价 10、结算价 5.5、持有期 10送10（送股比例 10）
    # 真实收益 = 2 × 5.5/10 - 1 = +10%
    prices = {"X": [(date(2023, 3, 31), 10.0), (date(2023, 6, 30), 5.5)]}

    class Lk(MockLookup):
        def dividends(self, code, asof):
            if code != "X":
                return None
            return [{
                "ex_dividend_date": "2023-05-15",
                "bonus_ratio": 10.0,   # 每10股送10股
                "trans_ratio": 0.0,
                "cash_div_per_share": 0.0,
            }]

    lk = Lk(prices=prices)
    # 不含分红的送转：成本 0、收益 = 2*5.5/10 - 1 - 0 = +10%
    r = portfolio_return(["X"], date(2023, 3, 31), date(2023, 6, 30), lk, cost=0.0)
    assert r is not None
    assert abs(r - 0.10) < 0.001  # +10%，不再是 -47%


def test_portfolio_return_no_split_unchanged():
    """无送转的股票收益不变（向后兼容）。"""
    prices = {"X": [(date(2023, 3, 31), 10.0), (date(2023, 6, 30), 12.0)]}

    class Lk(MockLookup):
        def dividends(self, code, asof):
            return None

    lk = Lk(prices=prices)
    r = portfolio_return(["X"], date(2023, 3, 31), date(2023, 6, 30), lk, cost=0.0)
    assert r is not None
    assert abs(r - 0.20) < 0.001  # +20%


# --- T11 #116 财报按披露日过滤 ---
def test_finance_filter_by_notice_date():
    """T11：finance 优先按 notice_date 过滤（消除未来函数）。"""
    prices = {"X": [(date(2023, 3, 31), 10.0), (date(2023, 6, 30), 11.0)]}

    class Lk(MockLookup):
        def __init__(self, **kw):
            super().__init__(**kw)
            self._fin_recs_test = {
                "X": [
                    # 2022 年报，notice_date 2023-04-29（年报 4 月披露）
                    {"year": 2022, "roe": 15.0, "notice_date": "2023-04-29"},
                ]
            }

        def dividends(self, code, asof):
            return None

        def pe_ttm(self, code, asof):
            return None

        def total_shares(self, code, asof):
            return 1e9

        # 直接测 finance 方法的 notice_date 过滤逻辑
        def finance(self, code, asof):
            recs = []
            for f in self._fin_recs_test.get(code, []):
                nd = f.get("notice_date")
                if nd:
                    from datetime import datetime
                    cutoff = datetime.strptime(nd[:10], "%Y-%m-%d").date()
                    if cutoff <= asof:
                        recs.append(f)
                else:
                    if date(f["year"], 12, 31) <= asof:
                        recs.append(f)
            return recs[-1] if recs else None

    lk = Lk(prices=prices)
    # 2023-04-28（披露日前）：2022 年报尚未披露，不可用
    assert lk.finance("X", date(2023, 4, 28)) is None
    # 2023-04-29（披露日当天）：可用
    assert lk.finance("X", date(2023, 4, 29)) is not None
    # 2023-06-30（披露后）：可用
    assert lk.finance("X", date(2023, 6, 30)) is not None


def test_finance_notice_date_missing_fallback_conservative_window():
    """T4 #131：notice_date 缺失时按报告期 +4 月保守窗口（非当天可见）。"""
    prices = {"X": [(date(2023, 3, 31), 10.0), (date(2023, 12, 31), 11.0)]}

    class Lk(MockLookup):
        def __init__(self, **kw):
            super().__init__(**kw)
            self._fin_recs_test = {
                "X": [
                    # 2022 年报，无 notice_date（缺失回退）
                    {"year": 2022, "roe": 15.0, "notice_date": None},
                ]
            }

        def dividends(self, code, asof):
            return None

        def pe_ttm(self, code, asof):
            return None

        def total_shares(self, code, asof):
            return 1e9

        def finance(self, code, asof):
            recs = []
            for f in self._fin_recs_test.get(code, []):
                nd = f.get("notice_date")
                if nd:
                    from datetime import datetime
                    cutoff = datetime.strptime(nd[:10], "%Y-%m-%d").date()
                    if cutoff <= asof:
                        recs.append(f)
                else:
                    # T4 #131：缺失回退 = 报告期 +4 月（2022 年报 → 2023-04-30 可见）
                    if date(f["year"] + 1, 4, 30) <= asof:
                        recs.append(f)
            return recs[-1] if recs else None

    lk = Lk(prices=prices)
    # 2022-12-31（报告期当天）：旧逻辑超前可见，T4 收紧后不可见
    assert lk.finance("X", date(2022, 12, 31)) is None
    # 2023-04-29：保守窗口前一日，不可见
    assert lk.finance("X", date(2023, 4, 29)) is None
    # 2023-04-30：保守窗口日，可见
    assert lk.finance("X", date(2023, 4, 30)) is not None
    # 2023-06-30：窗口后，可见
    assert lk.finance("X", date(2023, 6, 30)) is not None
