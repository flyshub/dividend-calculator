"""export_screener_json.py 单测（Pages 每日选股 JSON 导出）。

覆盖 CSV → JSON 转换逻辑，全部用 tmp_path 构造临时 CSV/输出目录，不碰仓库真实数据：
- parse_csv：11 字段映射、数字列转 float、非数字/空值健壮
- main：多日期聚合、同日多批次取最新、latest/history/按日文件生成、缺 CSV 报错

先例：tests/test_screener_cli.py（tmp_path 注入）、tests/test_backtest_pr.py（importlib 加载 scripts/）。
"""
import csv
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
CSV_HEADER = ["代码", "名称", "TTM股息率%", "真实股息率%", "估值区间", "市赚率PR",
              "行业", "可持续性", "ROE%", "总市值(亿)", "数据来源"]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "export_screener_json", SCRIPTS / "export_screener_json.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ex():
    return _load_module()


def _write_csv(path: Path, rows: list):
    """写一张 CSV（rows 为 dict 列表，按 CSV_HEADER 顺序输出）。"""
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADER)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def _sample_rows():
    """两只真实口径股票行（数字混合 int/float/字符串，验证转换）。"""
    return [
        {"代码": "600900", "名称": "长江电力", "TTM股息率%": "3.80", "真实股息率%": "3.80",
         "估值区间": "低估", "市赚率PR": "0.52", "行业": "公用事业-电力",
         "可持续性": "可持续", "ROE%": "16.00", "总市值(亿)": "6800.5", "数据来源": "mootdx"},
        {"代码": "000651", "名称": "格力电器", "TTM股息率%": "7.48", "真实股息率%": "7.48",
         "估值区间": "低估", "市赚率PR": "0.38", "行业": "家电-白色家电",
         "可持续性": "可持续", "ROE%": "20.30", "总市值(亿)": "2246", "数据来源": "akshare"},
    ]


class TestParseCsv:
    def test_fields_and_numeric_conversion(self, ex, tmp_path):
        p = tmp_path / "screener_20260809_144134.csv"
        _write_csv(p, _sample_rows())
        rows = ex.parse_csv(p)
        assert len(rows) == 2
        r = rows[0]
        # 11 字段映射齐全
        assert list(r.keys()) == CSV_HEADER
        # 数字列转 float
        assert isinstance(r["TTM股息率%"], float) and r["TTM股息率%"] == 3.80
        assert isinstance(r["ROE%"], float) and r["ROE%"] == 16.00
        assert isinstance(r["总市值(亿)"], float) and r["总市值(亿)"] == 6800.5
        # 非数字列保持字符串
        assert r["估值区间"] == "低估"
        assert r["数据来源"] == "mootdx"

    def test_non_numeric_value_keeps_string(self, ex, tmp_path):
        p = tmp_path / "screener_20260809_144134.csv"
        rows = _sample_rows()
        rows[0]["ROE%"] = "—"  # 缺数用占位符
        _write_csv(p, rows)
        r = ex.parse_csv(p)[0]
        assert r["ROE%"] == "—", "非数字值应保持字符串而非崩溃"

    def test_empty_cell_becomes_empty_string(self, ex, tmp_path):
        p = tmp_path / "screener_20260809_144134.csv"
        rows = _sample_rows()
        rows[0]["行业"] = ""
        _write_csv(p, rows)
        r = ex.parse_csv(p)[0]
        assert r["行业"] == ""
        assert r["代码"] == "600900"


class TestMain:
    def test_generates_three_files_with_history(self, ex, tmp_path, monkeypatch):
        csv_dir = tmp_path / "csv"
        site_dir = tmp_path / "site"
        csv_dir.mkdir()
        # 两天：2026-08-08（1 批）、2026-08-09（2 批，取较晚批次）
        _write_csv(csv_dir / "screener_20260808_100000.csv", _sample_rows()[:1])
        _write_csv(csv_dir / "screener_20260809_100000.csv", _sample_rows()[:1])
        _write_csv(csv_dir / "screener_20260809_150000.csv", _sample_rows())
        monkeypatch.setattr(ex, "CSV_DIR", csv_dir)
        monkeypatch.setattr(ex, "SITE_DIR", site_dir)

        assert ex.main() == 0

        # 3 类产物
        assert (site_dir / "latest.json").exists()
        assert (site_dir / "history.json").exists()
        assert (site_dir / "screener_2026-08-08.json").exists()
        assert (site_dir / "screener_2026-08-09.json").exists()

        # history 按日期升序
        history = json.loads((site_dir / "history.json").read_text(encoding="utf-8"))
        assert [h["date"] for h in history] == ["2026-08-08", "2026-08-09"]
        assert history[1]["count"] == 2

        # latest = 最新日期（2026-08-09）的最新批次（2 只）
        latest = json.loads((site_dir / "latest.json").read_text(encoding="utf-8"))
        assert len(latest) == 2
        # 同日多批次取最新：screener_2026-08-09.json 应含 2 只（非 1 只）
        day9 = json.loads((site_dir / "screener_2026-08-09.json").read_text(encoding="utf-8"))
        assert len(day9) == 2

    def test_no_csv_returns_error(self, ex, tmp_path, monkeypatch):
        csv_dir = tmp_path / "empty"
        csv_dir.mkdir()
        monkeypatch.setattr(ex, "CSV_DIR", csv_dir)
        monkeypatch.setattr(ex, "SITE_DIR", tmp_path / "site")
        assert ex.main() == 1

    def test_same_day_multiple_batches_takes_latest(self, ex, tmp_path, monkeypatch):
        """同一天 3 批：latest 内容与文件名排序最后的批次一致。"""
        csv_dir = tmp_path / "csv"
        csv_dir.mkdir()
        rows = _sample_rows()
        rows[0]["名称"] = "早批次"
        _write_csv(csv_dir / "screener_20260809_090000.csv", rows[:1])
        _write_csv(csv_dir / "screener_20260809_150000.csv", _sample_rows()[:1])
        _write_csv(csv_dir / "screener_20260809_170000.csv", rows)
        monkeypatch.setattr(ex, "CSV_DIR", csv_dir)
        monkeypatch.setattr(ex, "SITE_DIR", tmp_path / "site")

        assert ex.main() == 0
        latest = json.loads((tmp_path / "site" / "latest.json").read_text(encoding="utf-8"))
        assert len(latest) == 2, "latest 应取 17:00 批次（2 只）"
        # 最新批次第一只即 "早批次"（17:00 批次的 rows 内 600900 名称被改写）
        assert latest[0]["名称"] == "早批次"
        assert latest[1]["名称"] == "格力电器"
