"""选股器限流控制（spec #67，code-review + 用户要求）。

逐股拉取数据（股息/财务/PR/可持续性）时，统一控制请求频率，避免触发
数据源限流（东财海外 IP 限流、mootdx 连接不稳定等）。

用法：
    rl = RateLimiter(interval=0.8)   # 每请求间隔 0.8s
    for code in codes:
        rl.wait()                    # 进入下一请求前等待
        result = fetch(code)

参考：backtest_pr.py 的 UA_SLEEP=0.8（东财限流实测经验值）。
"""
import threading
import time
from typing import Optional


class RateLimiter:
    """基于时间戳的速率限制：保证相邻请求间隔 >= interval 秒。"""

    def __init__(self, interval: float = 0.8, jitter: float = 0.0):
        """
        Args:
            interval: 相邻请求最小间隔（秒）。默认 0.8（东财限流经验值）。
            jitter: 额外随机抖动（秒），避免所有请求同时发出。
        """
        self.interval = max(0.0, interval)
        self.jitter = max(0.0, jitter)
        self._last = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        """阻塞直到可以发出下一个请求。线程安全。"""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            need = self.interval + (self.jitter * (0.5 + _rand01()))
            if self._last and elapsed < need:
                time.sleep(need - elapsed)
            self._last = time.monotonic()

    def reset(self) -> None:
        """重置计时（测试用）。"""
        with self._lock:
            self._last = 0.0


def _rand01() -> float:
    """[0,1) 随机数（避免 import random 顶部开销）。"""
    import random
    return random.random()


# 模块级默认限流器（供批量拉取共享）
DEFAULT_RATE_LIMITER = RateLimiter(interval=0.8)


def batch_wait(limiter: Optional[RateLimiter] = None) -> None:
    """批量循环中的等待点。默认用模块级限流器。"""
    (limiter or DEFAULT_RATE_LIMITER).wait()
