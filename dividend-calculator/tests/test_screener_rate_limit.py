"""选股器限流器测试（spec #67，用户要求）。

覆盖 RateLimiter 的间隔控制 + 线程安全 + 重置。
"""
import time

import pytest

from src.screener_rate_limit import RateLimiter, batch_wait


class TestRateLimiter:
    def test_first_wait_immediate(self):
        """首次 wait 不等待（无上一次时间戳）。"""
        rl = RateLimiter(interval=1.0)
        t0 = time.monotonic()
        rl.wait()
        assert time.monotonic() - t0 < 0.5

    def test_subsequent_wait_enforces_interval(self):
        """相邻请求间隔 >= interval。"""
        rl = RateLimiter(interval=0.3, jitter=0.0)
        rl.wait()  # 首次
        t0 = time.monotonic()
        rl.wait()  # 第二次应等待 ~0.3s
        assert time.monotonic() - t0 >= 0.25

    def test_zero_interval_no_wait(self):
        rl = RateLimiter(interval=0.0, jitter=0.0)
        rl.wait()
        t0 = time.monotonic()
        rl.wait()
        assert time.monotonic() - t0 < 0.1

    def test_reset_clears_timer(self):
        rl = RateLimiter(interval=1.0)
        rl.wait()
        rl.wait()  # 会等待
        rl.reset()
        t0 = time.monotonic()
        rl.wait()  # reset 后立即
        assert time.monotonic() - t0 < 0.5

    def test_negative_interval_clamped(self):
        rl = RateLimiter(interval=-1.0)
        assert rl.interval == 0.0

    def test_thread_safety_no_crash(self):
        """多线程并发 wait 不崩溃（内部有锁）。"""
        rl = RateLimiter(interval=0.01)
        import threading
        errors = []
        def worker():
            try:
                for _ in range(10):
                    rl.wait()
            except Exception as e:  # pragma: no cover
                errors.append(e)
        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors


class TestBatchWait:
    def test_uses_module_default(self):
        """batch_wait 使用模块级默认限流器（不崩溃）。"""
        batch_wait()
        batch_wait()
