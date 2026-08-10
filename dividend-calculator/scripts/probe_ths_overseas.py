#!/usr/bin/env python3
"""THS（同花顺）接口海外可用性探针 —— 数据铁律「先验证」.

在 GitHub Actions（海外 runner）上实测 akshare stock_financial_abstract_ths
的成功率与耗时，评估「THS 升为主数据源」的可行性。

用法:
    python scripts/probe_ths_overseas.py                      # 默认 10 只样本
    python scripts/probe_ths_overseas.py --stocks 600036,600900 --repeat 1
"""
import argparse
import sys
import time

SAMPLE = [
    "600036", "600900", "600519", "601398", "000858",
    "600887", "601857", "000333", "600028", "002415",
]


def probe(stock_code: str) -> tuple:
    import akshare as ak
    t0 = time.time()
    df = ak.stock_financial_abstract_ths(symbol=stock_code, indicator="按年度")
    return time.time() - t0, len(df)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stocks", default=",".join(SAMPLE))
    parser.add_argument("--repeat", type=int, default=3, help="稳定性复测股票数")
    args = parser.parse_args()

    codes = [c.strip() for c in args.stocks.split(",") if c.strip()]
    ok, fail, total = 0, 0, 0.0
    print(f"THS 海外探针: {len(codes)} 只股票, 按年度, 单次调用", flush=True)
    for code in codes:
        try:
            sec, rows = probe(code)
            ok += 1
            total += sec
            print(f"  {code}: OK   {sec:.2f}s  rows={rows}", flush=True)
        except Exception as e:  # noqa: BLE001 —— 探针需捕获一切失败
            fail += 1
            print(f"  {code}: FAIL {type(e).__name__}: {str(e)[:100]}", flush=True)
        time.sleep(0.3)

    print(f"\n稳定性复测（前 {args.repeat} 只 × 3 次）:", flush=True)
    for code in codes[: args.repeat]:
        times = []
        for _ in range(3):
            try:
                sec, _ = probe(code)
                times.append(f"{sec:.2f}s")
            except Exception as e:  # noqa: BLE001
                times.append("FAIL")
            time.sleep(0.3)
        print(f"  {code}: {times}", flush=True)

    rate = ok / len(codes) * 100
    avg = total / ok if ok else 0
    print(f"\n结果: 成功 {ok}/{len(codes)} ({rate:.0f}%)  平均 {avg:.2f}s", flush=True)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
