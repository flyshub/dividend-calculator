"""选股数据质量检查测试（scripts/check_screener_data_health.py）。

接缝：check_rows(rows) 纯函数 + main(argv) 的退出码/JSON 产物。
先例：tests/test_check_seed_guard.py（脚本守卫的纯函数 + 退出码）。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_screener_data_health as health  # noqa: E402

pytestmark = pytest.mark.unit


def _rows(n, status="ok"):
    return [{"代码": "600%03d" % i, "前瞻状态": status} for i in range(n)]


class TestCheckRows:
    def test_healthy(self):
        assert health.check_rows(_rows(31)) == []

    def test_too_few_rows(self):
        (p,) = health.check_rows(_rows(3))
        assert p["title"] == health.TITLE_TOO_FEW
        assert "3 行" in p["detail"]

    def test_small_but_not_too_few_skips_forward_check(self):
        # 5-9 行：样本不足，不判前瞻覆盖率
        assert health.check_rows(_rows(7, status="fetch_error")) == []

    def test_forward_column_missing(self):
        rows = [{"代码": "600001"}, {"代码": "600002"}] * 6
        (p,) = health.check_rows(rows)
        assert p["title"] == health.TITLE_FORWARD_COLUMN
        assert "契约未生效" in p["detail"]

    def test_forward_all_empty(self):
        rows = _rows(9, status="fetch_error") + _rows(3, status="no_coverage")
        (p,) = health.check_rows(rows)
        assert p["title"] == health.TITLE_FORWARD_COLUMN
        assert "无一行可估" in p["detail"]

    def test_one_ok_is_enough(self):
        rows = _rows(20, status="no_coverage") + _rows(1, status="ok")
        assert health.check_rows(rows) == []


class TestMain:
    def test_unreadable_file_reports_and_exits_1(self, tmp_path, capsys):
        out = tmp_path / "h.json"
        code = health.main(["--path", str(tmp_path / "nope.json"), "--json-out", str(out)])
        assert code == 1
        problems = json.loads(out.read_text(encoding="utf-8"))["problems"]
        assert problems[0]["title"] == health.TITLE_UNREADABLE
        assert "✗" in capsys.readouterr().out

    def test_ok_writes_empty_problems(self, tmp_path):
        p = tmp_path / "latest.json"
        p.write_text(json.dumps(_rows(31)), encoding="utf-8")
        out = tmp_path / "h.json"
        assert health.main(["--path", str(p), "--json-out", str(out)]) == 0
        assert json.loads(out.read_text(encoding="utf-8"))["problems"] == []

    def test_problem_writes_json(self, tmp_path):
        p = tmp_path / "latest.json"
        p.write_text(json.dumps(_rows(2)), encoding="utf-8")
        out = tmp_path / "h.json"
        assert health.main(["--path", str(p), "--json-out", str(out)]) == 1
        assert json.loads(out.read_text(encoding="utf-8"))["problems"][0]["title"] \
            == health.TITLE_TOO_FEW
