"""sustainability.py 数据获取层单元测试（fixture 驱动，不打网络）。"""
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit  # 标记为本单元测试（区分 integration）

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from src.datasource.base import DividendRecord
from src.sustainability import (
    parse_financial_rows,
    parse_dividend_rows,
    select_latest_annual,
    aggregate_dividend_history,
    assess_for_stock,
)


# ---------------------------------------------------------------------------
# parse_financial_rows（东财字段 → AnnualFinancial）
# ---------------------------------------------------------------------------

def _make_finance_rows():
    """模拟东财 RPT_F10_FINANCE_MAINFINADATA 响应行（用验证过的真实字段名）。"""
    return [
        {
            "REPORT_DATE": "2025-12-31 00:00:00",
            "PARENTNETPROFIT": 34502809176.39,
            "PARENTNETPROFITTZ": 7.1,
            "NETCASH_OPERATE_PK": 60562925570.41,
            "NETCASH_INVEST_PK": -31264415237.5,
            "TOTAL_ASSETS_PK": 561990500889.54,
            "LIABILITY": 322172683239.63,
            "INTEREST_DEBT_RATIO": 51.5,
            "INTEREST_COVERAGE_RATIO": 6.37,
            "ROEJQ": 16.0,
            "NEWCAPITALADER": None,
            "NET_INTEREST_MARGIN": None,
            "NONPERLOAN": None,
            "LOAN_PROVISION_RATIO": None,
        },
        {
            "REPORT_DATE": "2024-12-31 00:00:00",
            "PARENTNETPROFIT": 325e8,
            "PARENTNETPROFITTZ": 5.0,
            "NETCASH_OPERATE_PK": 580e8,
            "NETCASH_INVEST_PK": -300e8,
            "TOTAL_ASSETS_PK": 5300e8,
            "LIABILITY": 280e8,
            "INTEREST_DEBT_RATIO": 50.0,
            "INTEREST_COVERAGE_RATIO": 7.0,
            "ROEJQ": 15.5,
        },
        {
            "REPORT_DATE": "2025-09-30 00:00:00",  # 季报（应被 select_latest_annual 跳过）
            "PARENTNETPROFIT": 28192874494.95,
            "NETCASH_OPERATE_PK": 42895214451.84,
            "ROEJQ": 13.0,
        },
    ]


def test_parse_financial_rows_basic():
    rows = _make_finance_rows()
    fins = parse_financial_rows(rows)
    # parse 只保留年报（12-31）行 → 2 条年报（季报 2025-09-30 被过滤）
    assert len(fins) == 2
    # 年份解析正确
    assert fins[0].year == 2025
    # 关键字段映射正确
    assert fins[0].net_profit == pytest.approx(34502809176.39)
    assert fins[0].operating_cf == pytest.approx(60562925570.41)
    assert fins[0].investing_cf == pytest.approx(-31264415237.5)
    # debt_ratio 无直接接口字段，靠 debt_ratio_decimal() 用 LIABILITY/TOTAL_ASSETS 推算
    assert fins[0].debt_ratio is None
    assert fins[0].debt_ratio_decimal() == pytest.approx(322172683239.63 / 561990500889.54, abs=0.01)
    assert fins[0].interest_coverage == 6.37
    assert fins[0].roe == 16.0


def test_parse_financial_rows_missing_fields_are_none():
    rows = [
        {"REPORT_DATE": "2025-12-31 00:00:00", "PARENTNETPROFIT": 100e8},
    ]
    fins = parse_financial_rows(rows)
    assert fins[0].net_profit == 1e10
    assert fins[0].operating_cf is None
    assert fins[0].capital_adequacy_ratio is None


def test_parse_financial_rows_empty_string_as_none():
    """空字符串视为缺失（避免 float('') 报错或 0 污染）。"""
    rows = [{"REPORT_DATE": "2025-12-31", "NETCASH_OPERATE_PK": "", "ROEJQ": None}]
    fins = parse_financial_rows(rows)
    assert fins[0].operating_cf is None
    assert fins[0].roe is None


def test_select_latest_annual_prefers_dividend_year():
    """select_latest_annual 应优先匹配分红所属财年（即使有更新的年报）。"""
    fins = parse_financial_rows(_make_finance_rows())  # 2025、2024 两份年报
    latest = select_latest_annual(fins, "2024")
    assert latest.year == 2024  # 匹配 target_year=2024，而非最新 2025


def test_select_latest_annual_empty():
    assert select_latest_annual([]) is None


# ---------------------------------------------------------------------------
# aggregate_dividend_history
# ---------------------------------------------------------------------------

def _div_records():
    """2015~2025 连续 11 年分红，2023 年曾削减（用于测 ever_cut）。"""
    recs = []
    for y in range(2015, 2026):
        dp10 = 5.0  # 每年10派5元
        if y == 2023:
            dp10 = 2.0  # 削减（5→2，降幅60%）
        recs.append(DividendRecord(
            ex_dividend_date=f"{y}-07-01",
            dividend_per_10=dp10,
            report_time=f"{y}年报",
        ))
    return recs


def test_aggregate_consecutive_years():
    total_shares = 1e9  # 10亿股
    h = aggregate_dividend_history(_div_records(), "2025", total_shares)
    assert h.consecutive_years == 11  # 2015~2025 连续


def test_aggregate_detects_cut():
    total_shares = 1e9
    h = aggregate_dividend_history(_div_records(), "2025", total_shares)
    assert h.ever_cut is True  # 2023 削减（近 10 年窗口内）


def test_aggregate_cut_outside_10y_window_ignored():
    """10 年窗口之外的削减不计入 ever_cut（如伊利 2014 年波动不影响 2016~2025 连涨）。"""
    recs = []
    for y in range(2012, 2026):
        dp10 = 2.0 if y == 2014 else 5.0  # 2014 削减（5→2，降幅60%），但在窗口外
        recs.append(DividendRecord(f"{y}-07-01", dp10, f"{y}年报"))
    h = aggregate_dividend_history(recs, "2025", 1e9)
    assert h.consecutive_years == 14  # 2012~2025 连续
    assert h.ever_cut is False  # 窗口外削减不计入


def test_aggregate_cut_at_window_boundary_detected():
    """削减恰好落在窗口首年（起点年）应计入：2025 年窗口起点 2016，2015→2016 的削减算窗口内。"""
    recs = []
    for y in range(2014, 2026):
        dp10 = 2.0 if y == 2016 else 5.0  # 2016 削减（5→2），2015→2016 跨立窗口首年
        recs.append(DividendRecord(f"{y}-07-01", dp10, f"{y}年报"))
    h = aggregate_dividend_history(recs, "2025", 1e9)
    assert h.ever_cut is True


def test_aggregate_latest_and_mean():
    total_shares = 1e9
    h = aggregate_dividend_history(_div_records(), "2025", total_shares)
    # 最新年（2025）每10派5元 × 1亿股单位 = 5亿
    assert h.latest_year_amount == pytest.approx(5.0e8)
    # 历史均值（2015~2024 共10年：9年×5亿 + 2023年2亿）/ 10
    assert h.history_mean_amount == pytest.approx((9 * 5e8 + 2e8) / 10)


def test_aggregate_empty_records():
    h = aggregate_dividend_history([], "2025", 1e9)
    assert h.consecutive_years == 0
    assert h.latest_year_amount is None


def test_aggregate_consecutive_breaks():
    """中间断档 → 连续年数只算最近的连续段。"""
    recs = [
        DividendRecord("2025-07-01", 5.0, "2025年报"),
        DividendRecord("2024-07-01", 5.0, "2024年报"),
        # 2023 没分红（断档）
        DividendRecord("2021-07-01", 5.0, "2021年报"),
    ]
    h = aggregate_dividend_history(recs, "2025", 1e9)
    assert h.consecutive_years == 2  # 只有 2024-2025 连续


# ---------------------------------------------------------------------------
# parse_dividend_rows（东财分红明细 → DividendRecord + 最新财年）
# ---------------------------------------------------------------------------

def test_parse_dividend_rows_skips_preset():
    """跳过"预披露"方案。"""
    rows = [
        {"REPORT_DATE": "2025-12-31", "PRETAX_BONUS_RMB": 7.9, "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2026-07-15"},
        {"REPORT_DATE": "2025-03-31", "PRETAX_BONUS_RMB": 3.0, "ASSIGN_PROGRESS": "预披露", "EX_DIVIDEND_DATE": ""},
    ]
    records, year = parse_dividend_rows(rows)
    assert len(records) == 1
    assert records[0].dividend_per_10 == pytest.approx(7.9)
    assert year == "2025"


def test_parse_dividend_rows_distinguishes_annual():
    """年报(12月) vs 中期分配(6/9/3月)。"""
    rows = [
        {"REPORT_DATE": "2025-12-31", "PRETAX_BONUS_RMB": 5.0, "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2026-07-01"},
        {"REPORT_DATE": "2025-06-30", "PRETAX_BONUS_RMB": 2.0, "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2025-12-01"},
        {"REPORT_DATE": "2024-12-31", "PRETAX_BONUS_RMB": 5.0, "ASSIGN_PROGRESS": "实施", "EX_DIVIDEND_DATE": "2025-07-01"},
    ]
    records, year = parse_dividend_rows(rows)
    assert year == "2025"  # 最新有年报的财年
    labels = [r.report_time for r in records]
    assert "2025年报" in labels
    assert "2025中期分配" in labels


def test_parse_dividend_rows_empty():
    records, year = parse_dividend_rows([])
    assert records == []
    assert year is None




def test_assess_for_stock_healthy_sustainable():
    rows = _make_finance_rows()
    # 连续 11 年稳定分红（无削减），匹配健康可持续画像
    records = [
        DividendRecord(f"{y}-07-01", 5.0, f"{y}年报") for y in range(2015, 2026)
    ]
    result = assess_for_stock(
        stock_code="600900",
        total_shares=2.4468e10,
        dividend_total=214e8,
        dividend_yield_before_tax=4.5,
        latest_dividend_year="2025",
        industry="公用事业-电力-水电",
        dividend_records=records,
        financial_rows=rows,
    )
    assert result.triggered is True
    assert result.verdict == "可持续"
    assert result.fatal_flags == []


def test_assess_for_stock_below_threshold():
    result = assess_for_stock(
        stock_code="600900",
        total_shares=1e9,
        dividend_total=10e8,
        dividend_yield_before_tax=3.0,
        latest_dividend_year="2025",
        industry="公用事业",
        dividend_records=[],
        financial_rows=[],
    )
    assert result.triggered is False


def test_assess_for_stock_bank_finance_branch():
    """银行股 → 金融分支（资本充足率等专项有效）。"""
    bank_rows = [{
        "REPORT_DATE": "2025-12-31 00:00:00",
        "PARENTNETPROFIT": 150181000000,
        "NETCASH_OPERATE_PK": 451457000000,
        "NETCASH_INVEST_PK": None,
        "TOTAL_ASSETS_PK": 12e12,
        "LIABILITY": 11e12,
        "ROEJQ": 14.0,
        "NEWCAPITALADER": 16.5,
        "NET_INTEREST_MARGIN": 1.87,
        "NONPERLOAN": 0.95,
        "LOAN_PROVISION_RATIO": 200.0,
    }]
    result = assess_for_stock(
        stock_code="600036",
        total_shares=2.5e11,
        dividend_total=350e8,
        dividend_yield_before_tax=5.0,
        latest_dividend_year="2025",
        industry="银行",
        dividend_records=[
            DividendRecord(f"{y}-07-01", 5.0, f"{y}年报") for y in range(2015, 2026)
        ],
        financial_rows=bank_rows,
    )
    assert result.branch == "finance"
    assert "capital_adequacy" in result.dimension_scores
    assert result.score is not None and result.score >= 1.5


# ---------------------------------------------------------------------------
# #39 M6：ever_cut 仅年报口径比较（中期分配不参与削减判定）
# ---------------------------------------------------------------------------

def test_ever_cut_ignores_interim_dividends():
    """年报10 → 年报8+中期3：ever_cut 仅比较年报，8 ≥ 10×0.7 → 不判削减（#39）。"""
    recs = [
        DividendRecord("2024-07-01", 10.0, "2024年报"),
        DividendRecord("2025-07-01", 8.0, "2025年报"),
        DividendRecord("2025-12-01", 3.0, "2025中期分配"),
    ]
    h = aggregate_dividend_history(recs, "2025", 1e9)
    assert h.ever_cut is False
    # 中期分配仍计入当年总额（year_amount 保持现状）
    assert h.latest_year_amount == pytest.approx(11.0e8)


def test_ever_cut_detects_annual_cut():
    """年报10 → 年报6：仅年报比较，6 < 10×0.7 → 判削减（#39）。"""
    recs = [
        DividendRecord("2024-07-01", 10.0, "2024年报"),
        DividendRecord("2025-07-01", 6.0, "2025年报"),
    ]
    h = aggregate_dividend_history(recs, "2025", 1e9)
    assert h.ever_cut is True


def test_ever_cut_mixed_annual_and_interim():
    """年报10+中期3 → 年报6：年度总额含中期仍可能削减，但比较用年报口径。"""
    recs = [
        DividendRecord("2024-07-01", 10.0, "2024年报"),
        DividendRecord("2024-12-01", 3.0, "2024中期分配"),
        DividendRecord("2025-07-01", 6.0, "2025年报"),
    ]
    h = aggregate_dividend_history(recs, "2025", 1e9)
    assert h.ever_cut is True  # 年报 6 < 10×0.7


def test_ever_cut_legacy_halfyear_label_not_annual():
    """旧 label「半年报」含「年报」子串：遗留数据不应被算进年报口径（#39 复审）。"""
    recs = [
        DividendRecord("2024-07-01", 10.0, "2024年报"),
        DividendRecord("2025-07-01", 8.0, "2025年报"),
        DividendRecord("2025-12-01", 3.0, "2025半年报"),  # 旧格式遗留数据
    ]
    h = aggregate_dividend_history(recs, "2025", 1e9)
    assert h.ever_cut is False  # 8 ≥ 10×0.7，半年报不参与削减比较
    # 总额仍含半年报（year_amount 保持现状）
    assert h.latest_year_amount == pytest.approx(11.0e8)


# ---------------------------------------------------------------------------
# #38 M5：分红历史取数失败 → history=None + 显式 note
# ---------------------------------------------------------------------------

def test_assess_for_stock_dividend_fetch_failed_note():
    """网络取数路径分红记录为空 → 强制 history=None + 显式失败 note（非静默 0 年）。"""
    rows = _make_finance_rows()
    result = assess_for_stock(
        stock_code="600900",
        total_shares=1e9,
        dividend_total=10e8,
        dividend_yield_before_tax=5.0,
        latest_dividend_year="2025",
        industry="公用事业",
        dividend_records=[],
        dividend_fetch_failed=True,
        financial_rows=rows,
    )
    assert result.triggered is True
    assert any("分红历史数据获取失败" in n for n in result.notes)


def test_assess_for_stock_empty_records_no_failure_note():
    """注入空 records 且未标记失败（纯函数路径）→ 不追加失败 note。"""
    rows = _make_finance_rows()
    result = assess_for_stock(
        stock_code="600900",
        total_shares=1e9,
        dividend_total=10e8,
        dividend_yield_before_tax=5.0,
        latest_dividend_year="2025",
        industry="公用事业",
        dividend_records=[],
        financial_rows=rows,
    )
    assert not any("分红历史数据获取失败" in n for n in result.notes)


# ---------------------------------------------------------------------------
# #42 L3：行业关键词专名匹配（电力设备 ≠ 电力行业，化学制药 ≠ 化工）
# ---------------------------------------------------------------------------

def test_industry_proper_names_no_false_match():
    from src.sustainability_calculator import _classify_industry

    # 电力设备（东财 EM2016 行业名）不应被"电力"误判为防御
    is_bank, is_cyclical, is_defensive = _classify_industry("电力设备")
    assert is_defensive is False
    # 电力行业（mootdx/证监会行业名）应判防御
    _, _, is_defensive = _classify_industry("电力行业")
    assert is_defensive is True
    # 化学制药（含"化工"子串）不应被"化工"误判为周期
    _, is_cyclical, _ = _classify_industry("化学制药")
    assert is_cyclical is False


# ---------------------------------------------------------------------------
# #40 B1 边界：fetch_price_change_1y / fetch_top10_holding / _secucode
# ---------------------------------------------------------------------------

class _FakeResp:
    """模拟 requests.Response（json 由调用方注入）。"""
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_fetch_price_change_1y_window_calculation(monkeypatch):
    """#40：用 rows[0]（窗口起点，约 1 年前）与 rows[-1]（最新）算 1 年变化率。"""
    import src.eastmoney_fetcher as emf

    rows = [
        ["2025-08-07", "42.5", "42.424"],   # 窗口起点收盘 42.424
        ["2026-01-05", "45.0", "45.0"],
        ["2026-08-07", "38.2", "38.80"],    # 最新收盘 38.80（腾讯返回字符串）
    ]
    payload = {"data": {"sh600036": {"qfqday": rows}}}

    def fake_get(url, **kwargs):
        assert "sh600036" in url
        return _FakeResp(payload)

    monkeypatch.setattr(emf.requests, "get", fake_get)
    result = emf.fetch_price_change_1y("600036")
    assert result == pytest.approx((38.80 - 42.424) / 42.424)


def test_fetch_price_change_1y_too_few_rows(monkeypatch):
    """K 线不足 2 根 → None（无窗口可算）。"""
    import src.eastmoney_fetcher as emf

    payload = {"data": {"sh600036": {"qfqday": [["2026-08-07", "10", "10"]]}}}
    monkeypatch.setattr(emf.requests, "get", lambda *a, **k: _FakeResp(payload))
    assert emf.fetch_price_change_1y("600036") is None


def test_fetch_price_change_1y_bj_prefix(monkeypatch):
    """北交所代码 → bj 前缀（#40 复审：6→sh，8/4/92→bj，其余→sz）。"""
    import src.eastmoney_fetcher as emf

    seen = {}

    def fake_get(url, **kwargs):
        seen["key"] = url.split("param=")[1].split(",")[0]
        payload = {"data": {seen["key"]: {"qfqday": [["a", "10", "10"], ["b", "20", "20"]]}}}
        return _FakeResp(payload)

    monkeypatch.setattr(emf.requests, "get", fake_get)
    assert emf.fetch_price_change_1y("830799") == pytest.approx(1.0)
    assert seen["key"] == "bj830799"


def test_fetch_top10_holding_empty_data(monkeypatch):
    """前十大股东数据为空 → None（不返回 0，避免参与集中度判分）。"""
    import src.eastmoney_fetcher as emf

    payload = {"result": {"data": []}}
    monkeypatch.setattr(emf.requests, "get", lambda *a, **k: _FakeResp(payload))
    assert emf.fetch_top10_holding("600036") is None


def test_fetch_top10_holding_all_ratios_missing(monkeypatch):
    """HOLD_NUM_RATIO 全缺失 → None（total==0 不返回 0）。"""
    import src.eastmoney_fetcher as emf

    payload = {"result": {"data": [{"END_DATE": "2026-06-30"}, {"END_DATE": "2026-03-31"}]}}
    monkeypatch.setattr(emf.requests, "get", lambda *a, **k: _FakeResp(payload))
    assert emf.fetch_top10_holding("600036") is None


def test_secucode_bj_mapping():
    """北交所（8/4/92 开头）→ .BJ；6 → .SH；其余 → .SZ。"""
    from src.eastmoney_fetcher import _secucode

    assert _secucode("830799") == "830799.BJ"
    assert _secucode("920099") == "920099.BJ"
    assert _secucode("600036") == "600036.SH"
    assert _secucode("000001") == "000001.SZ"


# ---------------------------------------------------------------------------
# #38 M5：fetch_dividend_rows None/[] 语义 + assess_with_auto_fetch 接线
# ---------------------------------------------------------------------------

def test_fetch_dividend_rows_failure_returns_none(monkeypatch):
    """网络异常 → None（取数失败），区别于真无分红的 []。"""
    import requests
    import src.eastmoney_fetcher as emf

    def boom(*a, **k):
        raise requests.ConnectionError("network down")

    monkeypatch.setattr(emf.requests, "get", boom)
    assert emf.fetch_dividend_rows("600036") is None


def test_fetch_dividend_rows_empty_data_returns_list(monkeypatch):
    """请求成功但无分红数据 → []（真无分红，不判失败）。"""
    import src.eastmoney_fetcher as emf

    payload = {"result": {"data": []}}
    monkeypatch.setattr(emf.requests, "get", lambda *a, **k: _FakeResp(payload))
    assert emf.fetch_dividend_rows("600036") == []


def test_assess_with_auto_fetch_empty_rows_no_failure_note(monkeypatch):
    """注入 dividend_rows=[]（真无分红）→ 不置失败标记、不加失败 note。"""
    from src import sustainability as sus

    monkeypatch.setattr(sus, "fetch_price_change_1y", lambda code: None)
    monkeypatch.setattr(sus, "fetch_top10_holding", lambda code: None)
    result = sus.assess_with_auto_fetch(
        stock_code="600900",
        total_shares=1e9,
        dividend_total=10e8,
        dividend_yield_before_tax=5.0,
        latest_dividend_year="2025",
        industry="公用事业",
        dividend_rows=[],          # 真无分红
        financial_rows=_make_finance_rows(),
    )
    assert result.triggered is True
    assert not any("分红历史数据获取失败" in n for n in result.notes)


def test_assess_with_auto_fetch_failure_marks_note(monkeypatch):
    """fetch_dividend_rows 返回 None（网络失败）→ 置失败标记 + note + history 缺失。"""
    from src import sustainability as sus

    monkeypatch.setattr(sus, "fetch_price_change_1y", lambda code: None)
    monkeypatch.setattr(sus, "fetch_top10_holding", lambda code: None)
    monkeypatch.setattr(sus, "fetch_dividend_rows", lambda code: None)  # 网络失败
    result = sus.assess_with_auto_fetch(
        stock_code="600900",
        total_shares=1e9,
        dividend_total=10e8,
        dividend_yield_before_tax=5.0,
        latest_dividend_year="2025",
        industry="公用事业",
        financial_rows=_make_finance_rows(),
    )
    assert result.triggered is True
    assert any("分红历史数据获取失败" in n for n in result.notes)
