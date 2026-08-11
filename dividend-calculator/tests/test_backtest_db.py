"""T2 历史回测数据库：schema 与行映射纯函数测试（issue #85）。

全部离线（mock 数据），不依赖网络；沿用 test_backtest_pr.py 的
importlib 加载 scripts 脚本模式。真实数据抽样断言由
`python scripts/build_backtest_db.py --sample` 单独验证（需网络）。
"""
import importlib.util
import sqlite3
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load_module():
    spec = importlib.util.spec_from_file_location("build_backtest_db", SCRIPTS / "build_backtest_db.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _connect_mem(mod):
    conn = sqlite3.connect(":memory:")
    mod.create_schema(conn)
    return conn


class TestSchema:
    """表结构：字段与任务规格一致（含公告日字段）。"""

    def setup_method(self):
        self.mod = _load_module()
        self.conn = _connect_mem(self.mod)

    def _cols(self, table):
        return {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}

    def test_stock_list_columns(self):
        cols = self._cols("stock_list")
        assert {"code", "name", "list_date", "delist_date", "board"} <= cols

    def test_daily_price_columns(self):
        assert {"code", "date", "close"} == self._cols("daily_price")

    def test_daily_pe_columns(self):
        assert {"code", "date", "pe_ttm"} == self._cols("daily_pe")

    def test_dividend_history_columns_include_announce_date(self):
        """回测无未来函数约束要求公告日字段存在。"""
        cols = self._cols("dividend_history")
        assert {"code", "announce_date", "report_date", "ex_dividend_date",
                "cash_div_10shares"} <= cols
        assert "announce_date" in cols

    def test_finance_history_columns(self):
        cols = self._cols("finance_history")
        assert {"code", "report_date", "roe", "net_profit", "net_cash_operate",
                "bps", "newcapitalader", "loan_provision_ratio"} <= cols

    def test_index_daily_columns(self):
        assert {"code", "date", "close"} == self._cols("index_daily")

    def test_build_progress_resume(self):
        """断点续传：标记完成后再构建即跳过。"""
        assert not self.mod._is_done(self.conn, "daily_pe", "600036")
        self.mod._mark_done(self.conn, "daily_pe", "600036")
        assert self.mod._is_done(self.conn, "daily_pe", "600036")


class TestFinancialFyearFilter:
    """仅保留 12-31 完整财年（month==12 规则，与 _parse_fhps_detail 双端一致）。"""

    def setup_method(self):
        self.mod = _load_module()

    def test_only_december_kept(self):
        rows = [
            {"REPORT_DATE": "2025-12-31 00:00:00", "ROEJQ": 10.5,
             "PARENTNETPROFIT": 1000, "NETCASH_OPERATE_PK": 500,
             "BPS": 20.0, "NEWCAPITALADER": 12.0, "LOAN_PROVISION_RATIO": 3.5},
            {"REPORT_DATE": "2025-09-30 00:00:00", "ROEJQ": 8.0,
             "PARENTNETPROFIT": 700, "NETCASH_OPERATE_PK": 300,
             "BPS": 19.0, "NEWCAPITALADER": None, "LOAN_PROVISION_RATIO": None},
            {"REPORT_DATE": "2025-06-30 00:00:00", "ROEJQ": 5.0,
             "PARENTNETPROFIT": 400, "NETCASH_OPERATE_PK": 100,
             "BPS": 18.0, "NEWCAPITALADER": None, "LOAN_PROVISION_RATIO": None},
            {"REPORT_DATE": "2025-03-31 00:00:00", "ROEJQ": 2.0,
             "PARENTNETPROFIT": 150, "NETCASH_OPERATE_PK": -50,
             "BPS": 17.0, "NEWCAPITALADER": None, "LOAN_PROVISION_RATIO": None},
            {"REPORT_DATE": "2024-12-31 00:00:00", "ROEJQ": 11.0,
             "PARENTNETPROFIT": 900, "NETCASH_OPERATE_PK": 450,
             "BPS": 18.5, "NEWCAPITALADER": 12.5, "LOAN_PROVISION_RATIO": 3.2},
        ]
        out = self.mod.financial_rows_to_db("600036", rows)
        assert len(out) == 2, "应只保留 2 条 12-31 记录"
        # 保留源顺序（东财按 REPORT_DATE 降序返回）
        assert [r[1] for r in out] == ["2025-12-31", "2024-12-31"]

    def test_field_mapping_and_date_normalization(self):
        rows = [{"REPORT_DATE": "2025-12-31 00:00:00", "ROEJQ": 10.5,
                 "PARENTNETPROFIT": 1000, "NETCASH_OPERATE_PK": 500,
                 "BPS": 20.0, "NEWCAPITALADER": 12.0, "LOAN_PROVISION_RATIO": 3.5}]
        out = self.mod.financial_rows_to_db("600036", rows)[0]
        assert out == ("600036", "2025-12-31", 10.5, 1000, 500, 20.0, 12.0, 3.5)

    def test_roe_fallback_to_weighted_when_roejq_missing(self):
        """ROEJQ 为空时回退 ROE_WEIGHTED（东财该接口 ROE_WEIGHTED 实测全 None，双保险）。"""
        rows = [{"REPORT_DATE": "2025-12-31 00:00:00", "ROEJQ": None,
                 "ROE_WEIGHTED": 9.9, "PARENTNETPROFIT": 1, "NETCASH_OPERATE_PK": 1,
                 "BPS": 1.0, "NEWCAPITALADER": None, "LOAN_PROVISION_RATIO": None}]
        assert self.mod.financial_rows_to_db("600036", rows)[0][2] == 9.9

    def test_empty_rows_return_empty(self):
        assert self.mod.financial_rows_to_db("600036", []) == []


class TestDividendMapping:
    """分红行映射：公告日/报告期/除权日/每10股派息。"""

    def setup_method(self):
        self.mod = _load_module()

    def test_mapping_with_all_dates(self):
        rows = [{"NOTICE_DATE": "2025-04-10 00:00:00", "REPORT_DATE": "2024-12-31 00:00:00",
                 "EX_DIVIDEND_DATE": "2025-07-15 00:00:00", "PRETAX_BONUS_RMB": 8.2}]
        out = self.mod.dividend_rows_to_db("600900", rows)[0]
        assert out == ("600900", "2025-04-10", "2024-12-31", "2025-07-15", 8.2)

    def test_none_ex_dividend_kept_as_none(self):
        """未实施分红（除权日为 None）保留记录但不虚构日期。"""
        rows = [{"NOTICE_DATE": "2026-03-28 00:00:00", "REPORT_DATE": "2026-06-30 00:00:00",
                 "EX_DIVIDEND_DATE": None, "PRETAX_BONUS_RMB": None}]
        out = self.mod.dividend_rows_to_db("600036", rows)[0]
        assert out[3] is None and out[4] is None
        assert out[1] == "2026-03-28"

    def test_missing_report_date_skipped(self):
        rows = [{"NOTICE_DATE": None, "REPORT_DATE": None,
                 "EX_DIVIDEND_DATE": None, "PRETAX_BONUS_RMB": None}]
        assert self.mod.dividend_rows_to_db("600036", rows) == []


class TestKlineParse:
    """腾讯 fqkline 响应解析：不复权 day 键，close 取 index 2。"""

    def setup_method(self):
        self.mod = _load_module()

    def test_parse_close_and_prefix(self):
        resp = {"data": {"sh600036": {"day": [
            ["2013-01-04", "13.820", "13.530", "13.970", "13.500", "123"],
            ["2013-01-07", "13.500", "13.600", "13.700", "13.400", "456"],
        ]}}}
        out = self.mod.parse_kline("600036", resp)
        assert out == [("600036", "2013-01-04", 13.53), ("600036", "2013-01-07", 13.6)]

    def test_sz_prefix(self):
        resp = {"data": {"sz000001": {"day": [["2020-01-02", "1", "2", "3", "4", "5"]]}}}
        assert self.mod.parse_kline("000001", resp) == [("000001", "2020-01-02", 2.0)]

    def test_empty_or_missing_data(self):
        assert self.mod.parse_kline("600036", {}) == []
        assert self.mod.parse_kline("600036", {"data": None}) == []

    def test_bad_row_skipped(self):
        resp = {"data": {"sh600036": {"day": [["2013-01-04"], ["2013-01-07", "1", "2", "3"]]}}}
        assert self.mod.parse_kline("600036", resp) == [("600036", "2013-01-07", 2.0)]


class TestBoard:
    def setup_method(self):
        self.mod = _load_module()

    def test_board_rules(self):
        assert self.mod._board("600036") == "SH"
        assert self.mod._board("000001") == "SZ"
        assert self.mod._board("300750") == "SZ"
        assert self.mod._board("830799") == "BJ"
        assert self.mod._board("430047") == "BJ"
        assert self.mod._board("920000") == "BJ"
