"""选股器筛选纯函数测试（spec #67，工单 #68）。

覆盖 screen_stock 四级漏斗判定，全部注入构造 dict，离线无网络：
- 漏斗① TTM 股息率 > 阈值
- 漏斗② 真实股息率 > 阈值
- 漏斗③ PR 估值 ∈ {合理偏低, 低估}
- 漏斗④ 可持续性 verdict ∈ {可持续, 偏弱}
- 边界：恰等于阈值、PR 恰 0.5/1.0、verdict 各档

先例：tests/test_pr_calculator.py（纯函数 + 边界值）。
"""
from src.screening import screen_stock


def _stock(**over):
    """构造单股数据 dict（默认通过全部漏斗）。"""
    data = {
        "ttm_yield": 6.0,
        "real_yield": 6.0,
        "pr": 0.8,
        "valuation_zone": "合理偏低",
        "sus_verdict": "可持续",
    }
    data.update(over)
    return data


class TestFunnel1Ttm:
    def test_passes_above_threshold(self):
        assert screen_stock(_stock(ttm_yield=6.0)).pass_ttm is True

    def test_rejects_below_threshold(self):
        r = screen_stock(_stock(ttm_yield=4.0))
        assert r.pass_ttm is False
        assert r.passed is False

    def test_boundary_exact_threshold(self):
        assert screen_stock(_stock(ttm_yield=5.0)).pass_ttm is False  # 严格 >

    def test_custom_threshold(self):
        r = screen_stock(_stock(ttm_yield=4.5), min_ttm=4.0)
        assert r.pass_ttm is True

    def test_missing_ttm_rejected(self):
        r = screen_stock(_stock(ttm_yield=None))
        assert r.pass_ttm is False


class TestFunnel2Real:
    def test_passes_above_threshold(self):
        assert screen_stock(_stock(real_yield=6.0)).pass_real is True

    def test_rejects_below_threshold(self):
        r = screen_stock(_stock(real_yield=4.0))
        assert r.pass_real is False

    def test_boundary_exact_threshold(self):
        assert screen_stock(_stock(real_yield=5.0)).pass_real is False

    def test_missing_real_rejected(self):
        r = screen_stock(_stock(real_yield=None))
        assert r.pass_real is False


class TestFunnel3Pr:
    def test_passes_low_valuation(self):
        assert screen_stock(_stock(valuation_zone="低估")).pass_pr is True

    def test_passes_reasonable_low(self):
        assert screen_stock(_stock(valuation_zone="合理偏低")).pass_pr is True

    def test_rejects_reasonable(self):
        r = screen_stock(_stock(valuation_zone="合理"))
        assert r.pass_pr is False
        assert r.passed is False

    def test_rejects_overvalued(self):
        assert screen_stock(_stock(valuation_zone="高估")).pass_pr is False

    def test_rejects_unknown(self):
        assert screen_stock(_stock(valuation_zone="无法判定")).pass_pr is False

    def test_pr_boundary_classified_by_valuation(self):
        # PR 0.5 → 低估（通过），PR 1.0 → 合理偏低（通过），PR 1.5 → 合理（拒绝）
        # 注意：screen_stock 用传入的 valuation_zone，不是自己算 PR 分类
        # （PR 计算由 T5 完成，这里只验证筛选对 valuation_zone 的判定）
        assert screen_stock(_stock(pr=0.5, valuation_zone="低估")).pass_pr is True
        assert screen_stock(_stock(pr=1.0, valuation_zone="合理偏低")).pass_pr is True
        assert screen_stock(_stock(pr=1.5, valuation_zone="合理")).pass_pr is False


class TestFunnel4Sustainability:
    def test_passes_sustainable(self):
        assert screen_stock(_stock(sus_verdict="可持续")).pass_sus is True

    def test_passes_weak(self):
        assert screen_stock(_stock(sus_verdict="偏弱")).pass_sus is True

    def test_rejects_unsustainable(self):
        r = screen_stock(_stock(sus_verdict="不可持续"))
        assert r.pass_sus is False
        assert r.passed is False

    def test_rejects_unassessed(self):
        assert screen_stock(_stock(sus_verdict="未评估")).pass_sus is False

    def test_missing_verdict_rejected(self):
        assert screen_stock(_stock(sus_verdict=None)).pass_sus is False

    def test_custom_verdict_whitelist(self):
        r = screen_stock(_stock(sus_verdict="偏弱"), sus_verdict=["偏弱"])
        assert r.pass_sus is True


class TestOverall:
    def test_all_pass(self):
        r = screen_stock(_stock())
        assert r.passed is True
        assert r.zone == "合理偏低"

    def test_any_fail_means_rejected(self):
        assert screen_stock(_stock(real_yield=3.0)).passed is False
        assert screen_stock(_stock(valuation_zone="高估")).passed is False
        assert screen_stock(_stock(sus_verdict="不可持续")).passed is False

    def test_reason_records_failed_funnel(self):
        r = screen_stock(_stock(real_yield=3.0))
        assert "真实股息率" in r.reason

    def test_zone_from_valuation(self):
        assert screen_stock(_stock(valuation_zone="低估")).zone == "低估"

    def test_missing_all_rejected(self):
        r = screen_stock(_stock(ttm_yield=None, real_yield=None,
                                valuation_zone=None, sus_verdict=None))
        assert r.passed is False
