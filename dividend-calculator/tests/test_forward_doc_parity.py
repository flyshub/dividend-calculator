"""前瞻状态枚举：文档 ↔ 代码 ↔ 页面文案 的三方对账（复盘改进 #4）。

两次评审各抓到一次「文档承诺了代码没做的事」（`LEGACY_FIELDS_14_PRE_FORWARD`
命名、`前瞻状态` 声称供页面解释原因）。枚举类的漂移可以用集合对账拦下：

- `docs/FORWARD_YIELD.md` §5.1 状态表 == `forward_yield.STATUS_*` 常量集
- §6.1 悬停文案表的键 ∪ {ok} == 常量集（ok 有数值，不写原因）
- §6.1 悬停文案的文案 == `screener_render.js` 的 `FORWARD_REASONS` 映射

任一处多写、漏写、改措辞忘同步都会红。先例：scripts/policy_parity.py（关键词/阈值双端对账）。
"""
import re
from pathlib import Path

import pytest

import src.forward_yield as fy

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).resolve().parent.parent      # dividend-calculator/
DOC = PROJECT_ROOT.parent / "docs" / "FORWARD_YIELD.md"
JS = PROJECT_ROOT / "site" / "js" / "screener_render.js"

STATUS_SET = {v for k, v in vars(fy).items() if k.startswith("STATUS_") and isinstance(v, str)}


def _section(text: str, start: str, end: str) -> str:
    i = text.index(start)
    return text[i:text.index(end, i)]


def _table_rows(section: str) -> list:
    """markdown 表格 → 去掉表头与分隔行后的单元格列表。"""
    rows = []
    for line in section.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and set("".join(cells)) <= set("-: "):
            continue  # 分隔行
        if cells[0] in ("值", "`forward_status`"):
            continue  # 表头
        rows.append(cells)
    return rows


def _strip_ticks(s: str) -> str:
    return s.strip().strip("`")


@pytest.fixture(scope="module")
def doc_text():
    return DOC.read_text(encoding="utf-8")


class TestStatusTable:
    def test_five_one_lists_every_constant(self, doc_text):
        rows = _table_rows(_section(doc_text, "### 5.1", "\n---\n"))
        documented = {_strip_ticks(r[0]) for r in rows}
        assert documented == STATUS_SET, (
            "§5.1 状态表与 STATUS_* 常量不一致：缺 %s，多 %s"
            % (sorted(STATUS_SET - documented), sorted(documented - STATUS_SET)))


class TestHoverCopy:
    def test_table_covers_all_except_ok(self, doc_text):
        rows = _table_rows(_section(doc_text, "**不可估原因的悬停文案**", "\n---\n"))
        documented = {_strip_ticks(r[0]) for r in rows}
        assert documented | {"ok"} == STATUS_SET

    def test_copy_matches_js_mapping(self, doc_text):
        rows = _table_rows(_section(doc_text, "**不可估原因的悬停文案**", "\n---\n"))
        doc_copy = {_strip_ticks(r[0]): r[2] for r in rows}
        js_text = JS.read_text(encoding="utf-8")
        block = js_text[js_text.index("FORWARD_REASONS"):]
        block = block[:block.index("};")]
        js_copy = dict(re.findall(r"(\w+): '([^']*)'", block))
        assert doc_copy == js_copy, "文档悬停文案与 screener_render.js 的 FORWARD_REASONS 不一致"
