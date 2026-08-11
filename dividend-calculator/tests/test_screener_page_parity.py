"""选股结果页口径卡与 screening.py 默认值对账（code-review 修复）。

口径卡的阈值硬编码在 HTML 散文里，与 src/screening.py 的 DEFAULT_* 常量构成
双事实源（code-review Standards #1）。本测试锁定三件事（#98 单页收拢后
页面来源仅 site/，原双端逐字节一致校验已随 src/static/ 一并退役）：
  1. 口径卡列出的四漏斗阈值/区间与 screening.py 默认值一致（改默认值必须同步页面）；
  2. 口径卡确实存在（防误删）；
  3. 'PR ≤ 1.0' 边界与 classify_valuation 的 1.0 边界一致。
"""
from pathlib import Path

from src.pr_calculator import classify_valuation
from src.screening import (
    DEFAULT_MIN_REAL,
    DEFAULT_MIN_TTM,
    DEFAULT_PR_ZONE,
    DEFAULT_SUS_VERDICT,
)

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["site/screener.html"]


def _read(page: str) -> str:
    return (ROOT / page).read_text(encoding="utf-8")


def test_criteria_card_exists_on_page():
    for page in PAGES:
        assert "选股口径" in _read(page), f"{page} 缺少选股口径卡"


def test_yield_thresholds_match_screening_defaults():
    for page in PAGES:
        html = _read(page)
        assert f"TTM 股息率 &gt; {DEFAULT_MIN_TTM:.0f}%" in html, f"{page} TTM 阈值漂移"
        assert f"真实股息率 &gt; {DEFAULT_MIN_REAL:.0f}%" in html, f"{page} 真实股息率阈值漂移"


def test_pr_zone_and_sus_verdict_match_screening_defaults():
    for page in PAGES:
        html = _read(page)
        for zone in DEFAULT_PR_ZONE:
            assert zone in html, f"{page} 缺少市赚率区间 {zone}"
        for verdict in DEFAULT_SUS_VERDICT:
            assert verdict in html, f"{page} 缺少可持续性判定 {verdict}"


def test_pr_le_1_boundary_matches_classify_valuation():
    """口径卡 'PR ≤ 1.0' 与 classify_valuation 的 1.0 边界一致（防边界漂移）。"""
    for page in PAGES:
        assert "PR ≤ 1.0" in _read(page), f"{page} 缺少 PR ≤ 1.0 表述"
    # 语义对账：1.0 仍落在 {低估,合理偏低}（含边界），1.01 即离开
    assert classify_valuation(1.0) in DEFAULT_PR_ZONE
    assert classify_valuation(1.01) not in DEFAULT_PR_ZONE
