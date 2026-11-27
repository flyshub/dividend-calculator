#!/usr/bin/env python3
"""选股结果数据质量检查。

补充 `screener_health.yml` 只判「最近一次成功运行是否超期」的盲区：
任务可能跑成功但数据塌了（漏斗 0 只、前瞻整列为空、seed 被清），
这类静默失效不会让 run 变红。本脚本读仓库内的 `site/screener/latest.json`
（不读 Pages，避免 CDN 缓存）判定结果是否可信。

判据（阈值取「明显异常」而非「有波动」）：
- 行数 < MIN_ROWS：选股结果异常偏少（漏斗或行情塌陷）
- 行数 ≥ MIN_ROWS_FOR_FORWARD 且无一行 `前瞻状态`：前瞻列缺失（契约未生效）
- 行数 ≥ MIN_ROWS_FOR_FORWARD 且 `前瞻状态` 全非 ok：前瞻整列为空
  （数据源故障且整批护栏触发，读者会以为全市场都不可估）
- 文件缺失 / 非 JSON 数组：单独一类

退出码：0 正常；1 有问题。`--json-out` 供 workflow 建 issue 使用。

用法:
    python scripts/check_screener_data_health.py
    python scripts/check_screener_data_health.py --path /tmp/latest.json --json-out /tmp/h.json
"""
import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = PROJECT_ROOT / "site" / "screener" / "latest.json"

MIN_ROWS = 5
MIN_ROWS_FOR_FORWARD = 10
FORWARD_STATUS_KEY = "前瞻状态"
FORWARD_OK = "ok"

TITLE_TOO_FEW = "🔔 选股结果异常偏少"
TITLE_NO_FORWARD = "🔔 前瞻股息率整列为空"
TITLE_UNREADABLE = "🔔 选股结果文件缺失或无法解析"


def _problem(title: str, detail: str) -> dict:
    return {"title": title, "detail": detail}


def check_rows(rows: list) -> list:
    """对已解析的 latest.json 行做质量判定 → 问题列表（空列表 = 正常）。"""
    problems = []
    if len(rows) < MIN_ROWS:
        problems.append(_problem(
            TITLE_TOO_FEW,
            "latest.json 只有 %d 行（阈值 %d 行），漏斗或行情数据可能塌陷。"
            % (len(rows), MIN_ROWS)))
        # 行数太少时不再判前瞻覆盖率：样本不足，避免一次故障报两条
        return problems

    if len(rows) < MIN_ROWS_FOR_FORWARD:
        return problems

    statuses = [r.get(FORWARD_STATUS_KEY) for r in rows if isinstance(r, dict)]
    if all(s is None for s in statuses):
        problems.append(_problem(
            TITLE_NO_FORWARD,
            "latest.json 全部 %d 行都没有 `%s` 字段，前瞻列契约未生效"
            "（导出脚本或页面版本可能回退）。" % (len(rows), FORWARD_STATUS_KEY)))
    elif not any(s == FORWARD_OK for s in statuses):
        counts = {}
        for s in statuses:
            counts[s] = counts.get(s, 0) + 1
        detail = "、".join("%s %d 只" % (k or "空", v) for k, v in sorted(
            counts.items(), key=lambda kv: -kv[1]))
        problems.append(_problem(
            TITLE_NO_FORWARD,
            "latest.json 共 %d 行，无一行可估（%s）。若为整批护栏触发，"
            "说明一致预期数据源故障，读者会看到整列「—」。" % (len(rows), detail)))
    return problems


def load_rows(path: Path) -> list:
    """读 latest.json → 行列表；缺失/损坏/非数组一律抛 ValueError。"""
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("latest.json 顶层不是数组")
    return data


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="选股结果数据质量检查")
    parser.add_argument("--path", default=str(DEFAULT_PATH), help="latest.json 路径")
    parser.add_argument("--json-out", default="", help="把问题列表写成 JSON（供 workflow 建 issue）")
    args = parser.parse_args(argv)

    path = Path(args.path)
    try:
        rows = load_rows(path)
        problems = check_rows(rows)
    except (OSError, ValueError) as exc:
        rows = []
        problems = [_problem(TITLE_UNREADABLE, "%s：%s" % (path, exc))]

    if problems:
        for p in problems:
            print("✗ %s\n  %s" % (p["title"], p["detail"]), flush=True)
    else:
        print("✓ 选股数据健康：%d 行，前瞻可估 %d 只" % (
            len(rows), sum(1 for r in rows if r.get(FORWARD_STATUS_KEY) == FORWARD_OK)),
            flush=True)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"problems": problems}, ensure_ascii=False), encoding="utf-8")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
