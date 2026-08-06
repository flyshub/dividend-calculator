"""
pr_calculator 纯计算模块测试
"""
from unittest.mock import patch

from src.pr import _get_industry
from src.pr_calculator import (
    compute_basic_pr,
    compute_corrected_pr,
    compute_pb_pr,
    compute_n_factor,
    classify_valuation,
    classify_industry,
)


# ---- compute_basic_pr ----

class TestComputeBasicPR:
    def test_normal(self):
        assert compute_basic_pr(10, 15.0) == round(10 / 15.0, 2)

    def test_none_pe(self):
        assert compute_basic_pr(None, 15.0) is None

    def test_none_roe(self):
        assert compute_basic_pr(10, None) is None

    def test_zero_roe(self):
        assert compute_basic_pr(10, 0.0) is None

    def test_negative_roe(self):
        assert compute_basic_pr(10, -5.0) is None


# ---- compute_corrected_pr ----

class TestComputeCorrectedPR:
    def test_normal(self):
        assert compute_corrected_pr(10, 15.0, 1.5) == round(1.5 * 10 / 15.0, 2)

    def test_none_factor(self):
        assert compute_corrected_pr(10, 15.0, None) is None

    def test_none_pe(self):
        assert compute_corrected_pr(None, 15.0, 1.0) is None

    def test_zero_roe(self):
        assert compute_corrected_pr(10, 0.0, 1.0) is None


# ---- compute_pb_pr ----

class TestComputePBPR:
    def test_normal(self):
        # PB=2, ROE=15% → 2 / (0.15²) / 100
        expected = round(2 / (0.15 ** 2) / 100, 2)
        assert compute_pb_pr(2.0, 15.0) == expected

    def test_none_pb(self):
        assert compute_pb_pr(None, 15.0) is None

    def test_none_roe(self):
        assert compute_pb_pr(2.0, None) is None

    def test_zero_roe(self):
        assert compute_pb_pr(2.0, 0.0) is None


# ---- compute_n_factor ----

class TestComputeNFactor:
    def test_none(self):
        assert compute_n_factor(None) is None

    def test_zero(self):
        assert compute_n_factor(0.0) == 2.0

    def test_negative(self):
        assert compute_n_factor(-0.1) == 2.0

    def test_high_payout_clamps_to_1(self):
        assert compute_n_factor(0.60) == 1.0

    def test_low_payout_clamps_to_2(self):
        assert compute_n_factor(0.20) == 2.0

    def test_mid_payout(self):
        # 50% / 40% = 1.25
        assert compute_n_factor(0.40) == 1.25

    def test_exact_boundary_50(self):
        assert compute_n_factor(0.50) == 1.0

    def test_exact_boundary_25(self):
        assert compute_n_factor(0.25) == 2.0


# ---- classify_valuation ----

class TestClassifyValuation:
    def test_undervalued(self):
        assert classify_valuation(0.3) == "低估"

    def test_fair_low(self):
        assert classify_valuation(0.6) == "合理偏低"

    def test_fair(self):
        assert classify_valuation(0.85) == "合理"

    def test_overvalued(self):
        assert classify_valuation(1.5) == "高估"

    def test_none(self):
        assert classify_valuation(None) == "无法判定"

    def test_boundary_05(self):
        assert classify_valuation(0.5) == "低估"

    def test_boundary_07(self):
        assert classify_valuation(0.7) == "合理偏低"

    def test_boundary_10(self):
        assert classify_valuation(1.0) == "合理"


# ---- classify_industry ----

class TestClassifyIndustry:
    def test_cyclical(self):
        c, t, w = classify_industry("煤炭开采")
        assert c is True and t is False
        assert "周期行业" in w

    def test_tech(self):
        c, t, w = classify_industry("半导体设备")
        assert c is False and t is True
        assert "科技行业" in w

    def test_normal(self):
        c, t, w = classify_industry("食品饮料")
        assert c is False and t is False
        assert w == ""

    def test_empty(self):
        c, t, w = classify_industry("")
        assert c is False and t is False


# ---- _get_industry 降级链（pr.py 数据层）----

class TestGetIndustryFallback:
    """行业获取降级链：mootdx F10 → 东财 datacenter(RPT_F10_BASIC_ORGINFO) → 未知行业"""

    def test_mootdx_fail_falls_back_to_eastmoney_datacenter(self):
        with patch("src.datasource.mootdx_source.MootdxSource", side_effect=Exception("mootdx down")), \
             patch("src.pr.fetch_eastmoney_industry", return_value="公用事业-电力-水电") as mock_em:
            industry, source = _get_industry("600900")
        assert industry == "公用事业-电力-水电"
        assert source == "东方财富"
        mock_em.assert_called_once_with("600900")

    def test_both_fail_returns_unknown(self):
        with patch("src.datasource.mootdx_source.MootdxSource", side_effect=Exception("mootdx down")), \
             patch("src.pr.fetch_eastmoney_industry", return_value=""):
            industry, source = _get_industry("600900")
        assert (industry, source) == ("未知行业", "无")

    def test_mootdx_ok_skips_eastmoney(self):
        class FakeSource:
            def get_industry(self, code):
                return "电力"

        with patch("src.datasource.mootdx_source.MootdxSource", return_value=FakeSource()), \
             patch("src.pr.fetch_eastmoney_industry") as mock_em:
            industry, source = _get_industry("600900")
        assert industry == "电力"
        assert source == "mootdx F10"
        mock_em.assert_not_called()
