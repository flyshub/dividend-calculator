"""#21 单例 reset 钩子与 conftest 隔离。"""
import src.datasource as ds
from src.datasource.mootdx_source import get_quotes_client, reset_quotes_client


def test_reset_data_source_manager_rebuilds_lazily():
    first = ds.get_data_source_manager()
    assert ds._data_source_manager is first
    ds.reset_data_source_manager()
    assert ds._data_source_manager is None
    second = ds.get_data_source_manager()
    assert second is not first          # 惰性重建，不是同一个实例


def test_set_data_source_manager_injects_stand_in():
    sentinel = object()
    ds.set_data_source_manager(sentinel)
    assert ds.get_data_source_manager() is sentinel
    ds.reset_data_source_manager()


def test_conftest_resets_between_tests():
    """前一个用例留下的单例必须已被 autouse fixture 清掉。"""
    assert ds._data_source_manager is None


def test_reset_quotes_client_sets_none(monkeypatch):
    import src.datasource.mootdx_source as m
    monkeypatch.setattr(m, "_quotes_client", "fake", raising=False)
    reset_quotes_client()
    assert m._quotes_client is None
