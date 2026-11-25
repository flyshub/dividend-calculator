"""全局测试夹具（#21）。

两处模块级单例（`datasource._data_source_manager`、`mootdx_source._quotes_client`）
会在用例之间泄漏状态与连接。这里在每条用例前后各 reset 一次，保证隔离。

行为说明：reset 只是把单例置 None，下次 `get_*()` 惰性重建——生产路径不受影响。
"""
import pytest


@pytest.fixture(autouse=True)
def _reset_global_singletons():
    """每个用例前后清空数据源管理器与 mootdx 客户端单例。"""
    from src.datasource import reset_data_source_manager
    from src.datasource.mootdx_source import reset_quotes_client

    reset_data_source_manager()
    reset_quotes_client()
    yield
    reset_data_source_manager()
    reset_quotes_client()
