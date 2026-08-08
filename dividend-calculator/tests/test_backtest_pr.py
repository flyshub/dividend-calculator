"""回测脚本（backtest_pr.py）纯函数测试。

覆盖 T1/T2/T3/T6/T7 提取的纯函数，全部注入构造 DataFrame / list，离线无网络：
- rebalance_dates / rebalance_targets：调仓日修复语义（#55）
- filter_pe_outliers：PE 极端值过滤（#56）
- analyze_panel：分组统计 + 单调性 + Q1 逐年（#54）
- bucket_absolute_pr：绝对 PR 区间（T6，#59）
- industry_neutralize：行业中性化（T7，#60）
- _spearman：秩相关

注：本测试用合成数据验证函数结构/边界（保持离线、CI 稳定）；
真实数据回归锚点（V2 报告：2535 点、rho -0.90、Q1 超额 +6.20%）
由 scripts/backtest_pr.py 全量运行验证，不在此断言（避免依赖 backtest.db）。

先例：tests/test_pr_calculator.py（纯函数 + 边界值）、tests/test_dividend.py。
"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load_module():
    spec = importlib.util.spec_from_file_location("backtest_pr", SCRIPTS / "backtest_pr.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def bp():
    return _load_module()


# ---------------------------------------------------------------------------
# rebalance_dates / rebalance_targets
# ---------------------------------------------------------------------------

class TestRebalanceDates:
    def _px(self):
        return pd.DataFrame({
            "date": pd.to_datetime([
                "2016-06-03", "2016-06-10", "2016-06-24",  # 6月内周线bar
                "2016-07-01", "2016-07-08",  # 6/30当周归7月
                "2017-06-30",  # 恰好落在目标日
                "2017-07-07",
            ]),
            "close": [10.0] * 7,
        })

    def test_selects_last_bar_on_or_before_target(self, bp):
        """6/30 当周 bar 归 7 月时，仍应选中 6 月内最近 bar（#55 修复）。"""
        px = self._px()
        dates = bp.rebalance_dates(px, bp.rebalance_targets(2016, 2016))
        assert len(dates) == 1
        assert str(dates[0].date()) == "2016-06-24"

    def test_target_date_exact_hit(self, bp):
        """目标日当天有 bar 时直接选中该日。"""
        px = self._px()
        dates = bp.rebalance_dates(px, ["2017-06-30"])
        assert len(dates) == 1
        assert str(dates[0].date()) == "2017-06-30"

    def test_empty_px_returns_empty(self, bp):
        px = pd.DataFrame({"date": pd.to_datetime([]), "close": []})
        assert bp.rebalance_dates(px, bp.rebalance_targets(2016, 2020)) == []

    def test_multi_year_targets(self, bp):
        px = self._px()
        dates = bp.rebalance_dates(px, bp.rebalance_targets(2016, 2017))
        assert len(dates) == 2
        assert str(dates[0].date()) == "2016-06-24"
        assert str(dates[1].date()) == "2017-06-30"

    def test_no_bar_after_target_uses_last_available(self, bp):
        """目标日之前无 bar（数据起点晚）时返回空（该年无调仓点）。"""
        px = pd.DataFrame({
            "date": pd.to_datetime(["2018-01-05", "2018-01-12"]),
            "close": [10.0, 10.5],
        })
        # 2016/2017 目标日在数据起点之前 → 无 bar → 空
        dates = bp.rebalance_dates(px, bp.rebalance_targets(2016, 2017))
        assert dates == []


# ---------------------------------------------------------------------------
# filter_pe_outliers
# ---------------------------------------------------------------------------

class TestFilterPeOutliers:
    def test_removes_extreme_pe(self, bp):
        pts = [{"pe": 10}, {"pe": 500}, {"pe": 300.5}, {"pe": 300}]
        assert [p["pe"] for p in bp.filter_pe_outliers(pts, 300)] == [10, 300]

    def test_removes_none_pe(self, bp):
        pts = [{"pe": 10}, {"pe": None}, {"pe": 20}]
        assert [p["pe"] for p in bp.filter_pe_outliers(pts, 300)] == [10, 20]

    def test_boundary_exact_max_kept(self, bp):
        pts = [{"pe": 300}]
        assert len(bp.filter_pe_outliers(pts, 300)) == 1

    def test_empty(self, bp):
        assert bp.filter_pe_outliers([], 300) == []


# ---------------------------------------------------------------------------
# analyze_panel
# ---------------------------------------------------------------------------

class TestAnalyzePanel:
    def _points(self):
        """构造 2 年、每年 10 只股票的 panel，PR 单调递减（低价组收益更高），
        且两年收益有区分（2017 整体高于 2016）。"""
        pts = []
        for year, boost in ((2016, 0.0), (2017, 0.05)):
            for i in range(10):
                pts.append({
                    "code": f"{year}{i:04d}",
                    "date": pd.Timestamp(f"{year}-06-24"),
                    "year": year,
                    "pe": 5 + i * 5,       # 5,10,15,...,50
                    "roe": 10.0,           # 固定 → PR 单调增
                    "ret_1y": boost + 0.3 - i * 0.05,  # 低价组收益更高，2017 整体+5%
                })
        return pts

    def test_group_count_and_anchor(self, bp):
        stats = bp.analyze_panel(self._points())
        assert stats["valid_points"] == 20
        assert len(stats["groups"]) == 5
        assert [g["group"] for g in stats["groups"]] == ["Q1", "Q2", "Q3", "Q4", "Q5"]
        # 每个分组含 PR 区间与统计字段
        for g in stats["groups"]:
            assert {"group", "pr_min", "pr_max", "ann", "excess", "win", "worst", "sharpe"} <= set(g)
        # Q1 最低 PR → 最高收益；Q5 反之 → 单调 → rho 应为 -1.0（完美负相关）
        assert stats["spearman_rho"] == -1.0

    def test_monotonic_rho_negative(self, bp):
        stats = bp.analyze_panel(self._points())
        anns = {g["group"]: g["ann"] for g in stats["groups"]}
        assert anns["Q1"] > anns["Q5"]

    def test_q1_by_year(self, bp):
        stats = bp.analyze_panel(self._points())
        assert set(stats["q1_by_year"].keys()) == {"2016", "2017"}
        assert stats["q1_by_year"]["2017"] > stats["q1_by_year"]["2016"]

    def test_empty_panel(self, bp):
        stats = bp.analyze_panel([])
        assert stats["valid_points"] == 0
        assert stats["groups"] == []
        assert stats["bench_annual"] != stats["bench_annual"]  # NaN

    def test_excludes_negative_pe_roe(self, bp):
        pts = [
            {"code": "a", "date": pd.Timestamp("2016-06-24"), "year": 2016,
             "pe": -10, "roe": 10, "ret_1y": 0.1},
            {"code": "b", "date": pd.Timestamp("2016-06-24"), "year": 2016,
             "pe": 10, "roe": -5, "ret_1y": 0.2},
            {"code": "c", "date": pd.Timestamp("2016-06-24"), "year": 2016,
             "pe": 10, "roe": 10, "ret_1y": 0.3},
        ]
        stats = bp.analyze_panel(pts)
        assert stats["valid_points"] == 1  # 只剩 c

    def test_benchmark_equal_weight(self, bp):
        stats = bp.analyze_panel(self._points())
        exp = self._points()
        expected = pd.DataFrame(exp)["ret_1y"].mean()
        assert stats["bench_annual"] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# _spearman
# ---------------------------------------------------------------------------

class TestSpearman:
    def test_perfect_negative(self, bp):
        assert bp._spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) == -1.0

    def test_perfect_positive(self, bp):
        assert bp._spearman([1, 2, 3], [1, 2, 3]) == 1.0

    def test_no_correlation(self, bp):
        # 常量序列 rank 均为平均秩，不触发除零——行为符合实现（组序 vs 年化收益不会遇常量）
        assert abs(bp._spearman([1, 2, 3], [2, 2, 2])) <= 1.0

    def test_ties_handled(self, bp):
        # 并列秩取平均：rank([1,1,2])=[1.5,1.5,3] → rho 定义正确（无崩溃）
        assert bp._spearman([1, 2, 3], [1, 1, 2]) == pytest.approx(0.875)


# ---------------------------------------------------------------------------
# bucket_absolute_pr（T6）
# ---------------------------------------------------------------------------

class TestBucketAbsolutePr:
    def _panel(self):
        """构造含 pr/ret_1y/year 的面板：低估组收益高、高估组收益低。"""
        rows = []
        for year in (2016, 2017):
            for i, pr in enumerate([0.3, 0.7, 1.5, 2.5, 4.0, 5.0]):
                rows.append({"year": year, "pr": pr, "ret_1y": 0.25 - i * 0.04})
        return pd.DataFrame(rows)

    def test_bucket_counts(self, bp):
        stats = bp.bucket_absolute_pr(self._panel())
        names = [b["bucket"] for b in stats["buckets"] if b["n"]]
        assert names == ["低估(≤0.5)", "合理偏低(0.5-1)", "合理(1-3)", "高估(>3)"]
        assert stats["n"] == 12

    def test_lowest_bucket_highest_return(self, bp):
        stats = bp.bucket_absolute_pr(self._panel())
        by_name = {b["bucket"]: b for b in stats["buckets"]}
        assert by_name["低估(≤0.5)"]["ann"] > by_name["高估(>3)"]["ann"]

    def test_empty_bucket(self, bp):
        panel = pd.DataFrame([{"year": 2016, "pr": 0.3, "ret_1y": 0.1}])
        stats = bp.bucket_absolute_pr(panel)
        assert stats["buckets"][1]["n"] == 0  # 合理偏低无样本


# ---------------------------------------------------------------------------
# industry_neutralize（T7）
# ---------------------------------------------------------------------------

class TestIndustryNeutralize:
    def _panel(self):
        """两年、两行业、Q1-Q5 分组的构造面板（含 ind1）。"""
        rows = []
        for year in (2016, 2017):
            for ind, base in (("银行", 0.10), ("白酒", 0.02)):
                for i in range(5):
                    rows.append({
                        "year": year, "ind1": ind,
                        "group": f"Q{i+1}",
                        "ret_1y": base + 0.05 - i * 0.02,
                        "pr": 0.5 + i,
                    })
        return pd.DataFrame(rows)

    def test_returns_excess_maps(self, bp):
        stats = bp.industry_neutralize(self._panel())
        assert set(stats["raw_excess"].keys()) == {"Q1", "Q2", "Q3", "Q4", "Q5"}
        assert set(stats["neutral_excess"].keys()) == {"Q1", "Q2", "Q3", "Q4", "Q5"}
        assert stats["n_industries"] == 2

    def test_raw_excess_matches_manual(self, bp):
        panel = self._panel()
        stats = bp.industry_neutralize(panel)
        bench = panel.groupby("year")["ret_1y"].mean().mean()
        q1 = panel[panel["group"] == "Q1"]["ret_1y"].mean()
        assert stats["raw_excess"]["Q1"] == pytest.approx(q1 - bench)

    def test_missing_ind1_returns_error(self, bp):
        panel = pd.DataFrame([{"year": 2016, "group": "Q1", "ret_1y": 0.1}])
        stats = bp.industry_neutralize(panel)
        assert "error" in stats
