"""CLI 收尾 hard_exit 的契约（issue #150）。

三个入口原本各自内联 flush + os._exit。这里钉住两件事：
顺序（先 flush 再 _exit，否则缓冲丢失）与退出码透传（0 完整 / 1 异常 /
2 部分完成，CI 依赖它区分「截断」与「失败」）。
"""
from unittest.mock import MagicMock, patch

from src.cli_exit import hard_exit


def _ordered_mocks():
    """按调用顺序记录：stdout.flush / stderr.flush / _exit(code)。"""
    calls = []
    stdout = MagicMock()
    stdout.flush.side_effect = lambda: calls.append("stdout")
    stderr = MagicMock()
    stderr.flush.side_effect = lambda: calls.append("stderr")
    exit_mock = MagicMock()
    exit_mock.side_effect = lambda code: calls.append(("exit", code))
    return stdout, stderr, exit_mock, calls


def test_flushes_then_exits_in_order():
    stdout, stderr, exit_mock, calls = _ordered_mocks()
    with patch("sys.stdout", stdout), patch("sys.stderr", stderr), \
            patch("os._exit", exit_mock):
        hard_exit(0)
    assert calls == ["stdout", "stderr", ("exit", 0)]


def test_passes_exit_code_through():
    _, _, exit_mock, _ = _ordered_mocks()
    with patch("os._exit", exit_mock), patch("sys.stdout"), patch("sys.stderr"):
        hard_exit(2)
    exit_mock.assert_called_once_with(2)


def test_default_code_is_zero():
    _, _, exit_mock, _ = _ordered_mocks()
    with patch("os._exit", exit_mock), patch("sys.stdout"), patch("sys.stderr"):
        hard_exit()
    exit_mock.assert_called_once_with(0)


def test_entry_points_use_hard_exit_not_os_exit():
    """三个入口不得再内联 os._exit（漏 flush 或漏调用的回归防线）。"""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    for rel in ("src/screener.py", "scripts/fill_screener_data.py",
                "scripts/prefetch_sustainability.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "hard_exit(main())" in text, f"{rel} 未使用 hard_exit"
        assert "os._exit" not in text, f"{rel} 仍内联 os._exit"
