"""CLI 入口的收尾：flush 缓冲后硬退出。

为什么要硬退出：mootdx / tdxpy 会留下非 daemon 心跳线程，脚本的 main() 返回后
解释器仍要等它们，进程挂住不退出——2026-11 事故里每日任务输出完结果后一直挂到
6 小时上限才被杀，故障表现成「超时」，掩盖了真正的取数问题。

为什么收在一处：三个入口（screener / fill_screener_data / prefetch_sustainability）
此前各写一遍 flush + os._exit，新增入口漏掉就是同一个事故重演，而且症状离根因很远。

注意 `os._exit` 跳过 atexit 与缓冲刷新，所以这里必须先 flush——顺序由
tests/test_cli_exit.py 钉住。
"""
import os
import sys


def hard_exit(code: int = 0) -> None:
    """刷新标准输出/错误后立即终止进程（不返回）。

    code 透传给进程退出码：0 完整、1 异常、2 部分完成（见各入口的约定）。
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
