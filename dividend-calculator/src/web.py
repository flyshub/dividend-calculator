"""本地 Web 页面和 JSON API。"""
import json
import logging
import sys
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.analysis import run_stock_analysis
from src.dividend import DividendResult, calculate_true_dividend_yield
from src.pr import PRResult
from src.sustainability_calculator import SustainabilityResult, explain_sustainability

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"


def serialize_pr_result(result: PRResult) -> dict:
    """序列化市赚率计算结果为 JSON"""
    return asdict(result)


def serialize_sustainability(result: SustainabilityResult) -> dict:
    """序列化可持续性评估结果为 JSON（字段名与 JS 端对齐）。"""
    return {
        "triggered": result.triggered,
        "verdict": result.verdict,
        "score": result.score,
        "fatal_flags": list(result.fatal_flags),
        "warning_flags": list(result.warning_flags),
        "dimension_scores": dict(result.dimension_scores),
        "metrics": {k: v for k, v in result.metrics.items()},
        "branch": result.branch,
        "notes": list(result.notes),
        "explanation": explain_sustainability(result),
    }


def serialize_result(result: DividendResult) -> dict:
    d = asdict(result)
    d["has_dividend"] = result.total_dividend > 0
    return d


class DividendRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_url = urlparse(self.path)

        if parsed_url.path == "/":
            self._send_file(INDEX_FILE, "text/html; charset=utf-8")
            return

        if parsed_url.path == "/health":
            self._send_json(200, {"ok": True})
            return

        if parsed_url.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return

        if parsed_url.path == "/api/calculate":
            self._handle_calculate(parsed_url.query)
            return

        if parsed_url.path == "/api/pr":
            self._handle_pr(parsed_url.query)
            return

        if parsed_url.path == "/api/historical-data":
            self._handle_historical_data(parsed_url.query)
            return

        self._send_json(404, {"success": False, "error": "页面不存在"})

    def do_OPTIONS(self):
        """处理 CORS 预检请求"""
        self.send_response(204)
        self._set_cors_headers()
        self.end_headers()

    def log_message(self, format, *args):
        logger.debug(format, *args)

    def _handle_calculate(self, query: str):
        params = parse_qs(query)
        stock_input = params.get("stock", [""])[0].strip()

        if not stock_input:
            self._send_json(400, {"success": False, "error": "请输入股票代码或股票名称"})
            return

        if len(stock_input) > 64:
            self._send_json(400, {"success": False, "error": "输入内容过长"})
            return

        try:
            result = calculate_true_dividend_yield(stock_input)
        except Exception:
            self._send_json(500, {"success": False, "error": "计算过程出错，请稍后重试"})
            return

        if result is None:
            # 提供更详细的错误提示
            error_msg = "无法获取股票数据"
            # 检查是否是代码格式问题
            if stock_input and not (stock_input.isdigit() and len(stock_input) == 6):
                error_msg = "请输入正确格式的6位股票代码，或准确的股票名称"
            else:
                error_msg = "无法获取数据。请检查股票代码是否正确，或网络连接是否正常（数据源可能暂时不可用）"

            self._send_json(
                404,
                {"success": False, "error": error_msg},
            )
            return

        self._send_json(200, {"success": True, "data": serialize_result(result)})

    def _handle_pr(self, query: str):
        """处理市赚率计算请求"""
        params = parse_qs(query)
        stock_input = params.get("stock", [""])[0].strip()

        if not stock_input:
            self._send_json(400, {"success": False, "error": "请输入股票代码"})
            return

        if len(stock_input) > 64:
            self._send_json(400, {"success": False, "error": "输入内容过长"})
            return

        try:
            analysis = run_stock_analysis(stock_input)
        except Exception:
            self._send_json(500, {"success": False, "error": "计算过程出错，请稍后重试"})
            return

        if analysis is None:
            self._send_json(404, {"success": False, "error": "无法获取股票数据，请检查股票代码"})
            return

        data = serialize_pr_result(analysis.pr_result)
        data["dividend_yield_before_tax"] = analysis.dividend_yield_before_tax
        data["sustainability"] = (
            serialize_sustainability(analysis.sustainability)
            if analysis.sustainability is not None else None
        )
        self._send_json(200, {"success": True, "data": data})

    def _handle_historical_data(self, query: str):
        from datetime import datetime
        from src.api import get_historical_data

        params = parse_qs(query)
        stock_input = params.get("stock", [""])[0].strip()

        if not stock_input:
            self._send_json(400, {"success": False, "error": "请输入股票代码"})
            return

        if len(stock_input) > 64:
            self._send_json(400, {"success": False, "error": "输入内容过长"})
            return

        try:
            data = get_historical_data(stock_input)
        except Exception:
            self._send_json(500, {"success": False, "error": "获取历史数据失败，请稍后重试"})
            return

        if data is None or not data.monthly_prices:
            self._send_json(404, {"success": False, "error": "无法获取历史数据，请检查股票代码"})
            return

        self._send_json(200, {
            "success": True,
            "data": {
                "stock_code": data.stock_code,
                "stock_name": data.stock_name,
                "data_date": datetime.now().strftime("%Y-%m-%d"),
                "monthly_prices": [
                    {"date": p.date, "close": round(p.close, 2)}
                    for p in data.monthly_prices
                ],
                "dividend_records": [
                    {
                        "ex_dividend_date": d.ex_dividend_date,
                        "dividend_per_10": d.dividend_per_10,
                        "report_time": d.report_time,
                    }
                    for d in data.dividend_records
                ],
            },
        })

    def _set_cors_headers(self):
        """设置 CORS 响应头"""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, status: int, body: dict):
        content = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self._set_cors_headers()
        self.end_headers()
        self.wfile.write(content)

    def _send_file(self, file_path: Path, content_type: str):
        if not file_path.exists():
            self._send_json(404, {"success": False, "error": "页面文件不存在"})
            return

        content = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main():
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    host = "127.0.0.1"
    port = 8000
    server = ThreadingHTTPServer((host, port), DividendRequestHandler)
    print("真实股息率计算器 Web 页面已启动:")
    print(f"http://{host}:{port}")
    print("按 Ctrl+C 退出")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
