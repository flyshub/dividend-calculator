"""测试 DataSourceManager 依赖注入（消除全局单例泄漏）"""
from unittest.mock import MagicMock

from src.datasource import DataSourceManager
from src.datasource.base import DataSource


class FakeSource(DataSource):
    """测试用假数据源"""

    def __init__(self, name: str, priority: int):
        self._name = name
        self._priority = priority

    @property
    def name(self) -> str:
        return self._name

    @property
    def priority(self) -> int:
        return self._priority

    def get_stock_info(self, stock_input):
        return None

    def get_latest_dividend(self, stock_code, stock_info):
        return 0.0, None, [], "fake"


class TestManagerInjection:
    """测试 sources 注入"""

    def test_default_sources_registered(self):
        """不传 sources 时，注册默认数据源"""
        mgr = DataSourceManager()
        names = mgr.get_source_names()
        assert "tencent" in names
        assert "sina" in names
        assert "mootdx" in names

    def test_injected_sources_replace_defaults(self):
        """传入 sources 时，跳过默认注册，只包含注入的数据源"""
        fake = FakeSource("FakeA", priority=10)
        mgr = DataSourceManager(sources=[fake])
        names = mgr.get_source_names()
        assert names == ["FakeA"]
        assert "tencent" not in names

    def test_injected_sources_sorted_by_priority(self):
        """传入的 sources 按 priority 排序"""
        high = FakeSource("High", priority=5)
        low = FakeSource("Low", priority=20)
        mgr = DataSourceManager(sources=[low, high])
        assert mgr.get_source_names() == ["High", "Low"]

    def test_stock_info_warnings_default_empty(self):
        """StockInfo 默认 warnings 为空列表（数据铁律#8：告警载体向后兼容）"""
        from src.datasource.base import StockInfo
        info = StockInfo(stock_code="600900", current_price=26.56, total_shares=2.27e10)
        assert info.warnings == []
        # 可追加告警且不影响其它字段
        info.warnings.append("测试告警")
        assert len(info.warnings) == 1
        assert info.stock_code == "600900"
