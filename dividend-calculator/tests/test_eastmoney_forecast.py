"""test_eastmoney_forecast.py — 一致预期取数的三分类（行 / 无覆盖 / 取数失败）

东财对「无机构覆盖」与「服务端错误」都回 HTTP 200 + success=false，
只能靠 code 区分（9201 = 返回数据为空）。把故障误判成无覆盖，
会让页面把"取数失败"显示成"无预测"，契约 forward_status 也随之失真。
"""
import pytest

from src.eastmoney_fetcher import _forecast_rows_from_payload

pytestmark = pytest.mark.unit

ROW = {"YEAR1": 2025, "YEAR_MARK1": "A", "EPS1": 40.0, "RATING_ORG_NUM": 20}


def test_success_with_data_returns_rows():
    payload = {"success": True, "code": 0, "result": {"data": [ROW]}}
    assert _forecast_rows_from_payload(payload) == [ROW]


def test_success_without_data_is_no_coverage():
    payload = {"success": True, "code": 0, "result": None}
    assert _forecast_rows_from_payload(payload) == []


def test_code_9201_is_no_coverage():
    """实测：300441 鲍斯股份 → success=false, code=9201「返回数据为空」。"""
    payload = {"success": False, "code": 9201, "message": "返回数据为空", "result": None}
    assert _forecast_rows_from_payload(payload) == []


def test_other_error_code_is_fetch_failure():
    """实测：参数错误 → success=false, code=9501，必须报成取数失败。"""
    payload = {"success": False, "code": 9501,
               "message": "参数预处理错误", "result": None}
    assert _forecast_rows_from_payload(payload) is None


def test_empty_payload_is_fetch_failure():
    assert _forecast_rows_from_payload(None) is None
    assert _forecast_rows_from_payload({}) is None


def test_data_wins_even_if_success_flag_missing():
    """字段缺失但确实带数据时按有数据处理，避免因上游多加字段而丢行。"""
    assert _forecast_rows_from_payload({"result": {"data": [ROW]}}) == [ROW]
