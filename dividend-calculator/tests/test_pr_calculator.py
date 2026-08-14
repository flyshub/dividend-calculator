"""
pr_calculator 纯计算模块测试
"""
from unittest.mock import patch

from src.pr import _get_industry, _get_pe_pb_eastmoney
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
        assert classify_valuation(1.5) == "合理"

    def test_overvalued(self):
        assert classify_valuation(4.0) == "高估"

    def test_none(self):
        assert classify_valuation(None) == "无法判定"

    def test_boundary_05(self):
        assert classify_valuation(0.5) == "低估"

    def test_boundary_10(self):
        assert classify_valuation(1.0) == "合理偏低"

    def test_boundary_30(self):
        assert classify_valuation(3.0) == "合理"


# ---- classify_industry ----

class TestClassifyIndustry:
    def test_cyclical(self):
        c, t, g, w = classify_industry("煤炭开采")
        assert c is True and t is False and g is False
        assert "周期行业" in w

    def test_tech(self):
        c, t, g, w = classify_industry("半导体设备")
        assert c is False and t is True and g is False
        assert "科技行业" in w

    def test_growth(self):
        c, t, g, w = classify_industry("光伏设备")
        assert c is False and t is False and g is True
        assert "成长行业" in w

    def test_growth_solar_real_label(self):
        # 东财真实标签「电气设备-电源设备-太阳能」（隆基/福莱特/阳光电源）
        c, t, g, w = classify_industry("电气设备-电源设备-太阳能")
        assert g is True
        assert "成长行业" in w

    def test_growth_ai_adjacent(self):
        # AI 应用行业标签可能是算力/数据中心（不在 TECH 而在 GROWTH）
        c, t, g, w = classify_industry("数据中心")
        assert g is True
        assert "成长行业" in w

    def test_priority_cyclical_over_growth(self):
        # 化工新材料：成长（新材料）+ 周期（化工）重叠 → 周期优先（周期用中位数 ROE 是计算级处理）
        c, t, g, w = classify_industry("化工新材料")
        assert c is True and g is True
        assert "周期行业" in w
        assert "成长行业" not in w

    def test_priority_tech_over_growth(self):
        # 半导体新材料：成长（新材料）+ 科技（半导体）重叠 → 科技优先
        c, t, g, w = classify_industry("半导体新材料")
        assert t is True and g is True
        assert "科技行业" in w
        assert "成长行业" not in w

    def test_normal(self):
        c, t, g, w = classify_industry("食品饮料")
        assert c is False and t is False and g is False
        assert w == ""

    def test_empty(self):
        c, t, g, w = classify_industry("")
        assert c is False and t is False and g is False


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


# ---- calculate_pr 周期股 PB-市赚率用 5 年 ROE 中位数（#25 补充）----

class TestCalculatePrCyclicalPBRoe:
    """周期股 PB-市赚率用 5 年 ROE 中位数；非周期股用最新年报 ROE。"""

    def _run(self, industry, roe_latest, roe_5y_median):
        from src.pr import calculate_pr
        with patch("src.pr._get_pe_pb", return_value=(10.0, 4.0, "测试股", "tencent", [])), \
             patch("src.pr._get_financial",
                   return_value=(roe_latest, roe_5y_median, 1e9, 1e9, "mock", [], 2025)), \
             patch("src.pr._get_industry", return_value=(industry, "mock")), \
             patch("src.pr._check_pr_fields", return_value=[]):
            return calculate_pr("600000", stock_name="测试股", dividend_total=5e8)

    def test_cyclical_uses_median(self):
        # 煤炭（周期）: ROE最新=5, 5年中位=10 → PB-PR 用 10 → 4/(0.1²)/100 = 4.0
        r = self._run("煤炭", 5.0, 10.0)
        assert r.pr_pb == 4.0

    def test_non_cyclical_uses_latest(self):
        # 白酒（非周期）: ROE最新=5 → PB-PR 用 5 → 4/(0.05²)/100 = 16.0
        r = self._run("白酒", 5.0, 10.0)
        assert r.pr_pb == 16.0

    def test_cyclical_no_median_falls_back(self):
        # 周期股但无 5 年中位数 → 回退最新 ROE
        r = self._run("煤炭", 5.0, None)
        assert r.pr_pb == 16.0


# ---- _get_pe_pb_eastmoney push2 字段解析（P2-1 实测：f164=PE-TTM、f167=PB、fltt=2）----

class TestGetPePbEastmoney:
    """push2 备选源字段解析：f164=PE-TTM、f167=PB（fltt=2 非缩放），f9/f115 弃用。"""

    def _mock_get(self, payload):
        class FakeResp:
            status_code = 200

            def json(self):
                return payload

        return patch("src.pr.requests.get", return_value=FakeResp())

    def test_parses_f164_and_f167(self):
        # 实测 600900 返回形状（fltt=2）：f164=19.15 与腾讯 f39 吻合，f167=3.31 与腾讯 f46 吻合
        with self._mock_get({"data": {"f164": 19.15, "f167": 3.31}}):
            pe, pb = _get_pe_pb_eastmoney("600900")
        assert pe == 19.15
        assert pb == 3.31

    def test_requests_fltt2_and_f164(self):
        with patch("src.pr.requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {"data": {"f164": 6.49, "f167": 0.88}}
            _get_pe_pb_eastmoney("600036")
        url = mock_get.call_args[0][0]
        assert "fields=f164,f167" in url
        assert "fltt=2" in url
        assert "secid=1.600036" in url

    def test_zero_fields_return_none(self):
        # f164/f167 为 0（数据缺失）→ 返回 None，不编造
        with self._mock_get({"data": {"f164": 0, "f167": 0}}):
            pe, pb = _get_pe_pb_eastmoney("600900")
        assert (pe, pb) == (None, None)

    def test_no_data_returns_none(self):
        with self._mock_get({"data": None}):
            pe, pb = _get_pe_pb_eastmoney("600900")
        assert (pe, pb) == (None, None)
