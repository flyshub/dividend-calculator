import json
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.dividend import DividendDetail, DividendResult
from src.pr import PRResult
from src.web import DividendRequestHandler, serialize_result, serialize_pr_result, attach_tsr


def test_serialize_result_with_dividend_details():
    result = DividendResult(
        stock_code="600900",
        stock_name="长江电力",
        current_price=26.56,
        total_shares=22741859116.0,
        total_market_cap=604024497320.96,
        total_dividend=21445553126.388,
        dividend_yield_before_tax=3.550443,
        dividend_yield_after_tax=3.195399,
        dividend_yield_after_tax_20=2.840354,
        latest_year="2025",
        dividend_details=[DividendDetail("2025年报", 7.33)],
        explanation="测试说明",
    )

    data = serialize_result(result)

    assert data["stock_code"] == "600900"
    assert data["stock_name"] == "长江电力"
    assert data["has_dividend"] is True
    assert data["dividend_details"] == [
        {"report_time": "2025年报", "dividend_per_10": 7.33}
    ]


def test_serialize_result_without_dividend():
    result = DividendResult(
        stock_code="000000",
        stock_name=None,
        current_price=0.0,
        total_shares=0.0,
        total_market_cap=0.0,
        total_dividend=0.0,
        dividend_yield_before_tax=0.0,
        dividend_yield_after_tax=0.0,
        dividend_yield_after_tax_20=0.0,
        latest_year=None,
        dividend_details=[],
        explanation="无有效分红",
    )

    data = serialize_result(result)

    assert data["has_dividend"] is False
    assert data["latest_year"] is None
    assert data["dividend_details"] == []


def test_serialize_result_all_fields_present():
    result = DividendResult(
        stock_code="600036",
        stock_name="招商银行",
        current_price=35.0,
        total_shares=25_000_000_000.0,
        total_market_cap=875_000_000_000.0,
        total_dividend=35_000_000_000.0,
        dividend_yield_before_tax=4.0,
        dividend_yield_after_tax=3.6,
        dividend_yield_after_tax_20=3.2,
        latest_year="2024",
        dividend_details=[DividendDetail("2024年报", 1.738)],
        explanation="测试",
    )

    data = serialize_result(result)
    expected_keys = {
        "stock_code", "stock_name", "current_price", "total_shares",
        "total_market_cap", "total_dividend", "dividend_yield_before_tax",
        "dividend_yield_after_tax", "dividend_yield_after_tax_20",
        "latest_year", "has_dividend", "dividend_details", "explanation",
        "warnings", "dividend_source",
        "ttm_dividend", "dividend_yield_ttm_before_tax", "ttm_period", "ttm_source",
    }
    assert set(data.keys()) == expected_keys


def test_serialize_pr_result_basic():
    result = PRResult(
        stock_code="600900",
        stock_name="长江电力",
        pr_basic=1.5,
        pr_corrected=1.2,
        pr_pb=0.8,
        valuation_zone="合理偏低",
        pe_ttm=18.0,
        pb=3.5,
        roe_latest=15.9,
        roe_5y_median=14.5,
        net_profit_latest_period=30_000_000_000.0,
        net_profit_annual=28_000_000_000.0,
        dividend_total=21_000_000_000.0,
        payout_ratio=0.75,
        n_factor=0.85,
        industry="电力",
        is_cyclical=False,
        is_tech=False,
        is_growth=False,
        is_loss_stock=False,
        pr_warning="",
        pe_pb_source="tencent",
        finance_source="同花顺",
        industry_source="eastmoney",
        errors=[],
    )

    data = serialize_pr_result(result)

    assert data["stock_code"] == "600900"
    assert data["stock_name"] == "长江电力"
    assert data["pr_basic"] == 1.5
    assert data["pr_corrected"] == 1.2
    assert data["pr_pb"] == 0.8
    assert data["valuation_zone"] == "合理偏低"
    assert data["pe_ttm"] == 18.0
    assert data["pb"] == 3.5
    assert data["is_cyclical"] is False
    assert data["errors"] == []


# ---- attach_tsr（估值不变下的股东总回报率进详情页 payload）----

def test_attach_tsr_normal():
    data = {"roe_latest": 15.9, "roe_5y_median": 14.5, "is_cyclical": False,
            "pb": 3.5, "dividend_yield_before_tax": 5.0}
    # (15.9 − 5.0×3.5) + 5.0 = 3.4
    assert attach_tsr(data)["tsr"] == 3.4


def test_attach_tsr_cyclical_uses_median():
    data = {"roe_latest": 20.0, "roe_5y_median": 12.0, "is_cyclical": True,
            "pb": 2.0, "dividend_yield_before_tax": 5.0}
    # 周期股取中位数：(12.0 − 5.0×2.0) + 5.0 = 7.0
    assert attach_tsr(data)["tsr"] == 7.0


def test_attach_tsr_missing_pb_returns_none():
    data = {"roe_latest": 15.9, "roe_5y_median": None, "is_cyclical": False,
            "pb": None, "dividend_yield_before_tax": 5.0}
    assert attach_tsr(data)["tsr"] is None


def test_attach_tsr_missing_yield_degrades_to_roe():
    """股息率缺失（无分红）按 0 参与 → TSR 退化为 ROE（对齐 JS 端 yields[0]=0）。"""
    data = {"roe_latest": 15.9, "roe_5y_median": None, "is_cyclical": False,
            "pb": 3.5, "dividend_yield_before_tax": None}
    assert attach_tsr(data)["tsr"] == 15.9


def test_serialize_pr_result_with_errors():
    result = PRResult(
        stock_code="600000",
        stock_name="浦发银行",
        pr_basic=0.5,
        pr_corrected=None,
        pr_pb=None,
        valuation_zone="低估",
        pe_ttm=5.0,
        pb=0.4,
        roe_latest=8.0,
        roe_5y_median=9.0,
        net_profit_latest_period=5_000_000_000.0,
        net_profit_annual=4_800_000_000.0,
        dividend_total=2_000_000_000.0,
        payout_ratio=0.4,
        n_factor=0.9,
        industry="银行",
        is_cyclical=True,
        is_tech=False,
        is_growth=False,
        is_loss_stock=False,
        pr_warning="周期股PR仅供参考",
        pe_pb_source="tencent",
        finance_source="eastmoney",
        industry_source="eastmoney",
        errors=["ROE_5Y取值失败，使用latest ROE"],
    )

    data = serialize_pr_result(result)

    assert data["pr_corrected"] is None
    assert data["pr_pb"] is None
    assert data["is_cyclical"] is True
    assert data["pr_warning"] == "周期股PR仅供参考"
    assert data["errors"] == ["ROE_5Y取值失败，使用latest ROE"]


def test_serialize_pr_result_all_fields():
    result = PRResult(
        stock_code="000858",
        stock_name="五粮液",
        pr_basic=1.0,
        pr_corrected=0.9,
        pr_pb=0.7,
        valuation_zone="合理偏低",
        pe_ttm=20.0,
        pb=6.0,
        roe_latest=25.0,
        roe_5y_median=22.0,
        net_profit_latest_period=30_000_000_000.0,
        net_profit_annual=28_000_000_000.0,
        dividend_total=15_000_000_000.0,
        payout_ratio=0.53,
        n_factor=0.95,
        industry="白酒",
        is_cyclical=False,
        is_tech=False,
        is_growth=False,
        is_loss_stock=False,
        pr_warning="",
        pe_pb_source="tencent",
        finance_source="同花顺",
        industry_source="eastmoney",
        errors=[],
    )

    data = serialize_pr_result(result)
    expected_keys = {
        "stock_code", "stock_name", "pr_basic", "pr_corrected", "pr_pb",
        "valuation_zone", "pe_ttm", "pb", "roe_latest", "roe_5y_median",
        "net_profit_latest_period", "net_profit_annual", "dividend_total",
        "payout_ratio", "n_factor", "industry", "is_cyclical", "is_tech",
        "is_growth", "is_loss_stock", "pr_warning", "pe_pb_source", "finance_source",
        "industry_source", "errors", "roe_period", "pr_judge", "roe_judge",
    }
    assert set(data.keys()) == expected_keys


# ---------------------------------------------------------------------------
# 静态路由：/screener.html 与 /screener/*.json（code-review 修复：本地 Web 版接入选股页）
# ---------------------------------------------------------------------------

class TestScreenerRoutes:
    @pytest.fixture(autouse=True)
    def _server(self):
        """临时起服于随机端口，测完即关。"""
        server = ThreadingHTTPServer(("127.0.0.1", 0), DividendRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        yield f"http://127.0.0.1:{port}"
        server.shutdown()
        server.server_close()

    def test_serve_screener_html(self, _server):
        with urllib.request.urlopen(f"{_server}/screener.html") as r:
            assert r.status == 200
            assert r.headers["Content-Type"].startswith("text/html")
            body = r.read().decode("utf-8")
            assert "每日选股结果" in body
            assert "js/screener_render.js" in body, "页面应引用渲染模块"

    def test_serve_screener_render_js(self, _server):
        """本地 Web 版依赖的 JS 必须可加载（否则页面白屏）。"""
        with urllib.request.urlopen(f"{_server}/js/screener_render.js") as r:
            assert r.status == 200
            assert r.headers["Content-Type"].startswith("application/javascript")
            body = r.read().decode("utf-8")
            assert "rowHtml" in body and "fmtNum" in body

    def test_serve_screener_latest_json(self, _server):
        with urllib.request.urlopen(f"{_server}/screener/latest.json") as r:
            assert r.status == 200
            assert r.headers["Content-Type"].startswith("application/json")
            data = json.loads(r.read().decode("utf-8"))
            # 允许合法空结果（某日无股通过全部漏斗）：非空时首行须含 11 字段
            assert isinstance(data, list)
            if data:
                assert "代码" in data[0]

    def test_serve_screener_history_json(self, _server):
        with urllib.request.urlopen(f"{_server}/screener/history.json") as r:
            assert r.status == 200
            history = json.loads(r.read().decode("utf-8"))
            assert isinstance(history, list)
            assert history[0]["date"] and history[0]["file"]

    def test_index_still_serves(self, _server):
        """回归：原首页路由不受影响。"""
        with urllib.request.urlopen(f"{_server}/") as r:
            assert r.status == 200
            assert "真实股息率计算器" in r.read().decode("utf-8")


# ---------------------------------------------------------------------------
# /api/pr 前瞻股息率字段（docs/FORWARD_YIELD.md）：键名与页面约定固化
# ---------------------------------------------------------------------------

class TestForwardYieldRoute:
    @pytest.fixture(autouse=True)
    def _server(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), DividendRequestHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{server.server_address[1]}"
        server.shutdown()
        server.server_close()

    def _fake_analysis(self, forward, cash_return=None):
        from types import SimpleNamespace
        return SimpleNamespace(
            pr_result=None,
            dividend_yield_before_tax=3.44,
            sustainability=None,
            forward_yield=forward,
            cash_return=cash_return,
        )

    def test_pr_payload_includes_forward(self, _server, monkeypatch):
        import src.web as web_module
        from src.forward_yield import ForwardYieldResult

        fwd = ForwardYieldResult(
            forward_status="ok",
            forward_year=2026, forward_eps=66.8, forward_eps_kind="E",
            forward_payout_assumed=0.75, forward_dps=50.1,
            forward_yield_before_tax=3.44, forward_coverage=43,
            forward_note="2026E EPS 66.80 × 支付率 75.0% ÷ 现价 1457.70",
        )
        monkeypatch.setattr(web_module, "run_stock_analysis", lambda code: self._fake_analysis(fwd))
        monkeypatch.setattr(web_module, "serialize_pr_result", lambda r: {"stock_code": "600519"})

        with urllib.request.urlopen(f"{_server}/api/pr?stock=600519") as r:
            body = json.loads(r.read().decode("utf-8"))

        assert body["success"] is True
        forward = body["data"]["forward"]
        assert forward["forward_status"] == "ok"
        assert forward["forward_year"] == 2026
        assert forward["forward_eps_kind"] == "E"
        assert forward["forward_yield_before_tax"] == 3.44
        assert forward["forward_coverage"] == 43

    def test_pr_payload_includes_cash_return(self, _server, monkeypatch):
        import src.web as web_module
        from src.cash_return import CashReturnResult

        cr = CashReturnResult(
            cash_return_status="ok", cash_return_year=2025,
            cash_return_dividend=2e10, cash_return_repurchase=6e9,
            cash_return_repurchase_events=1,
            cash_return_dividend_yield=2.0, cash_return_repurchase_yield=0.6,
            cash_return_total_yield=2.6, cash_return_note="按回购完成日归属财年，未按日拆分",
        )
        monkeypatch.setattr(web_module, "run_stock_analysis", lambda code: self._fake_analysis(None, cr))
        monkeypatch.setattr(web_module, "serialize_pr_result", lambda r: {"stock_code": "600519"})

        with urllib.request.urlopen(f"{_server}/api/pr?stock=600519") as r:
            body = json.loads(r.read().decode("utf-8"))

        data = body["data"]["cash_return"]
        assert data["cash_return_status"] == "ok"
        assert data["cash_return_year"] == 2025
        assert data["cash_return_repurchase"] == 6e9
        assert data["cash_return_total_yield"] == 2.6

    def test_pr_payload_cash_return_null_without_fiscal_year(self, _server, monkeypatch):
        import src.web as web_module

        monkeypatch.setattr(web_module, "run_stock_analysis", lambda code: self._fake_analysis(None))
        monkeypatch.setattr(web_module, "serialize_pr_result", lambda r: {"stock_code": "000000"})

        with urllib.request.urlopen(f"{_server}/api/pr?stock=000000") as r:
            body = json.loads(r.read().decode("utf-8"))

        assert body["data"]["cash_return"] is None

    def test_pr_payload_forward_null_when_not_estimated(self, _server, monkeypatch):
        import src.web as web_module

        monkeypatch.setattr(web_module, "run_stock_analysis", lambda code: self._fake_analysis(None))
        monkeypatch.setattr(web_module, "serialize_pr_result", lambda r: {"stock_code": "000000"})

        with urllib.request.urlopen(f"{_server}/api/pr?stock=000000") as r:
            body = json.loads(r.read().decode("utf-8"))

        assert body["data"]["forward"] is None
