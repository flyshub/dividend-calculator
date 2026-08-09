"""选股器 CLI/编排测试（spec #67，工单 #74）。

覆盖四级漏斗编排 + CSV 输出，全部注入/替换模块级函数，不碰网络：
- run_screener：各层串联、漏斗递减
- _build_output_rows：字段汇总 + 排序
- write_csv：stdout 与文件两种模式

先例：tests/test_screener_*.py（各层）、tests/test_web.py（CLI 冒烟）。
"""
import io
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from src.screener import _build_output_rows, run_screener, write_csv
from src.screener_cache import (
    DividendSnapshot,
    FinanceSnapshot,
    QuoteSnapshot,
    ScreenerCache,
)


def _seed_cache(cache):
    """注入 3 只股票：2 只通过全部层，1 只卡在漏斗②。"""
    for code, name in [("600900", "长江电力"), ("600987", "航民股份"), ("600919", "江苏银行")]:
        cache.upsert_quote(QuoteSnapshot(
            code=code, name=name, price=10, pe_ttm=8.0, pb=1.0,
            total_shares=1e9, market_cap=1e10, quote_time="", source="腾讯"))
        cache.upsert_finance(FinanceSnapshot(
            code=code, roe_latest=16.0, roe_period="2025年报",
            net_profit_annual=1e9, payout_ratio=0.5, finance_source="东财"))
    # 600900/600987 高股息过漏斗②；600919 低股息被拒
    # total_dividend 按 real_yield 反推（市值 1e10）：total = real/100 × 1e10
    for code, real in [("600900", 6.0), ("600987", 6.5), ("600919", 2.0)]:
        cache.upsert_dividend(DividendSnapshot(
            code=code, real_yield=real, ttm_yield=real + 0.5,
            real_yield_year="2025", ttm_period="p",
            total_dividend=real / 100.0 * 1e10, ttm_dividend=(real + 0.5) / 100.0 * 1e10,
            dividend_source="mootdx"))


class TestRunScreener:
    @patch("src.screener._load_stock_list", return_value=["600900", "600987", "600919"])
    @patch("src.screener_quotes.fetch_all_quotes")
    @patch("src.screener.evaluate_pr_batch")
    def test_four_funnel_pipeline(self, mock_pr, mock_fetch, mock_list, tmp_path):
        cache = ScreenerCache(tmp_path / "s.db")
        _seed_cache(cache)

        def fake_fetch(codes, cache=None):
            return [cache.get_quote(c) for c in codes if cache.get_quote(c) is not None]

        mock_fetch.side_effect = fake_fetch
        # 股息从缓存读（run_screener 用批量缓存，_seed_cache 已预填）
        # PR 评估注入：600900/600987 通过（低估），其余拒绝
        def fake_pr(codes, cache, **kw):
            return [{"code": c, "pr": 0.5, "valuation_zone": "低估",
                     "pass_pr": c in ("600900", "600987"),
                     "industry": "电力", "roe_latest": 16.0} for c in codes]
        mock_pr.side_effect = fake_pr
        # 可持续性评估注入：全通过
        with patch("src.screener.evaluate_sustainability_batch") as mock_sus:
            mock_sus.side_effect = lambda stocks, cache, **kw: [
                {"code": s["code"], "verdict": "可持续", "pass_sus": True} for s in stocks
            ]
            final = run_screener(cache, min_ttm=5.0, min_real=5.0)
            assert mock_sus.called, "evaluate_sustainability_batch 应被调用"
        # 漏斗② 拒掉 600919（低股息），应剩 2 只
        codes = {f["code"] for f in final}
        assert "600900" in codes, f"应含 600900, 实际 {codes}"
        assert "600987" in codes
        assert "600919" not in codes

    def test_limit_caps_codes(self, tmp_path):
        cache = ScreenerCache(tmp_path / "s.db")
        with patch("src.screener._load_stock_list", return_value=[f"{600000+i}" for i in range(50)]):
            # limit=10 → 只扫前 10
            with patch("src.screener_quotes.fetch_all_quotes", return_value=[]) as m:
                run_screener(cache, limit=10)
                # 传入 fetch_all_quotes 的 codes 应被裁剪到 10
                called_codes = m.call_args[0][0]
                assert len(called_codes) == 10


class TestBuildOutputRows:
    def test_rows_sorted_by_real_yield(self, tmp_path):
        cache = ScreenerCache(tmp_path / "s.db")
        _seed_cache(cache)
        final = [
            {"code": "600900", "verdict": "可持续"},
            {"code": "600987", "verdict": "可持续"},
        ]
        rows = _build_output_rows(cache, final)
        # 按真实股息率降序：600987(6.5) > 600900(6.0)
        assert rows[0]["真实股息率%"] == 6.5
        assert rows[1]["真实股息率%"] == 6.0
        assert rows[0]["名称"] == "航民股份"

    def test_fields_present(self, tmp_path):
        cache = ScreenerCache(tmp_path / "s.db")
        _seed_cache(cache)
        rows = _build_output_rows(cache, [{
            "code": "600900", "verdict": "可持续",
            "valuation_zone": "低估", "pr": 0.5, "industry": "电力",
        }])
        r = rows[0]
        assert r["估值区间"] == "低估"
        assert r["可持续性"] == "可持续"
        assert r["行业"] == "电力"
        assert r["数据来源"]


class TestWriteCsv:
    def test_writes_stdout(self):
        rows = [{"代码": "600900", "名称": "长江电力", "真实股息率%": 6.0}]
        buf = io.StringIO()
        with redirect_stdout(buf):
            write_csv(rows, "-")
        out = buf.getvalue()
        assert "代码,名称,真实股息率%" in out
        assert "600900" in out

    def test_writes_file(self, tmp_path):
        f = tmp_path / "out.csv"
        rows = [{"代码": "600900", "名称": "长江电力", "真实股息率%": 6.0}]
        write_csv(rows, str(f))
        assert f.exists()
        assert "600900" in f.read_text(encoding="utf-8")

    def test_empty_writes_header(self, tmp_path):
        f = tmp_path / "out.csv"
        write_csv([], str(f))
        assert f.exists()
        assert "代码" in f.read_text(encoding="utf-8")
