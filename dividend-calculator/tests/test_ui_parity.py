"""双端 UI 结构一致性测试（#46）。

verify_ui_parity.py 的检测行为固化：现状通过；任一端口径漂移必须报错。
"""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
SPEC = SCRIPTS / "verify_ui_parity.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_ui_parity", SPEC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestCollectIds:
    def test_extracts_ids(self):
        mod = _load_module()
        html = '<div id="a"></div><div id="b"></div>'
        assert mod.collect_ids(html) == {"a", "b"}

    def test_empty(self):
        mod = _load_module()
        assert mod.collect_ids("") == set()


class TestCollectCardTitles:
    def test_extracts_labels(self):
        mod = _load_module()
        html = '<div class="label">估值区间</div>'
        assert mod.collect_card_titles(html) == ["估值区间"]

    def test_ignores_other_classes(self):
        mod = _load_module()
        html = '<div class="value">1.5</div>'
        assert mod.collect_card_titles(html) == []


@pytest.mark.parametrize("path", ["site/index.html", "src/static/index.html"])
def test_parity_files_exist(path):
    root = Path(__file__).resolve().parent.parent
    assert (root / path).exists(), f"缺失 {path}"


def test_current_parity_passes():
    """现状：双端 id 与卡片标题完全一致，脚本应通过。"""
    root = Path(__file__).resolve().parent.parent
    mod = _load_module()
    site = (root / "site" / "index.html").read_text(encoding="utf-8")
    static = (root / "src" / "static" / "index.html").read_text(encoding="utf-8")
    assert mod.collect_ids(site) == mod.collect_ids(static)
    assert mod.collect_card_titles(site) == mod.collect_card_titles(static)


def test_detects_drift_in_ids(tmp_path):
    """一端 id 漂移必须被检测。"""
    root = Path(__file__).resolve().parent.parent
    mod = _load_module()
    site = (root / "site" / "index.html").read_text(encoding="utf-8")
    static = (root / "src" / "static" / "index.html").read_text(encoding="utf-8")
    drifted = static.replace("pr-valuation", "pr-valuation-drifted", 1)
    assert mod.collect_ids(site) != mod.collect_ids(drifted)


def test_detects_drift_in_card_titles(tmp_path):
    """一端卡片标题漂移必须被检测。"""
    root = Path(__file__).resolve().parent.parent
    mod = _load_module()
    site = (root / "site" / "index.html").read_text(encoding="utf-8")
    static = (root / "src" / "static" / "index.html").read_text(encoding="utf-8")
    drifted = static.replace("估值区间", "估值区间漂移", 1)
    assert mod.collect_card_titles(site) != mod.collect_card_titles(drifted)
