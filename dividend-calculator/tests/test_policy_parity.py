"""双端 policy 一致性校验的测试（issues #149 / #151）。"""
from pathlib import Path

from scripts.policy_parity import (
    check_industry_keywords,
    check_pr_cutoffs,
    python_industry_sets,
)

ROOT = Path(__file__).resolve().parent.parent
CALCULATOR_JS = (ROOT / "site" / "js" / "calculator.js").read_text(encoding="utf-8")


class TestIndustryKeywords:
    def test_real_files_are_consistent(self):
        """仓库现状必须一致（这条会拦住未来任何一侧的单边新增）。"""
        assert check_industry_keywords(CALCULATOR_JS) == []

    def test_detects_keyword_only_in_python(self):
        js = CALCULATOR_JS.replace("'电力行业'", "'电力'")
        problems = check_industry_keywords(js)
        assert problems and "DEFENSIVE_INDUSTRIES" in problems[0]
        assert "电力" in problems[0]

    def test_detects_keyword_only_in_js(self):
        js = CALCULATOR_JS.replace(
            "var TECH_INDUSTRIES = [", "var TECH_INDUSTRIES = ['量子计算',")
        problems = check_industry_keywords(js)
        assert any("量子计算" in p for p in problems)

    def test_missing_array_reported_not_raised(self):
        js = CALCULATOR_JS.replace("var GROWTH_INDUSTRIES = [", "var GROWTH_X = [")
        problems = check_industry_keywords(js)
        assert any("GROWTH_INDUSTRIES" in p and "缺少" in p for p in problems)

    def test_python_sets_have_deliberate_special_names(self):
        """#42 L3 的专名规则：不能退回泛词，否则「电力设备」会被误判防御。"""
        sets = python_industry_sets()
        assert "电力行业" in sets["DEFENSIVE_INDUSTRIES"]
        assert "电力" not in sets["DEFENSIVE_INDUSTRIES"]


class TestPrCutoffs:
    def test_consistent_when_absent_is_reported(self):
        """calculator.js 里还没有切点声明时应报缺失（Phase 2 落在 site/js/policy.js）。"""
        problems = check_pr_cutoffs("var PR_ZONE_CUTOFFS = {};")
        assert len(problems) == 3

    def test_detects_value_drift(self):
        js = "var PR_ZONE_CUTOFFS = { undervalued: 0.6, fair_low: 1.0, fair_max: 3.0 };"
        problems = check_pr_cutoffs(js)
        assert len(problems) == 1 and "undervalued" in problems[0]
