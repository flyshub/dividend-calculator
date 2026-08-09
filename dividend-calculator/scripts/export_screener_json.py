#!/usr/bin/env python3
"""选股器每日结果 → GitHub Pages JSON（spec: Pages 展示每日选股）。

将 data/screener/screener_*.csv 转换为 site/screener/ 下的 JSON：
- latest.json：最新一日的股票列表（11 字段，数字转 float）
- history.json：所有历史日期索引 [{date, file}]（按日期聚合，同日多批次取最新）
- screener_<date>.json：每个历史日期的独立文件（供历史切换读取）

用法:
    python scripts/export_screener_json.py
"""
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CSV_DIR = PROJECT_ROOT / "data" / "screener"
SITE_DIR = PROJECT_ROOT / "site" / "screener"

# CSV 11 列 → JSON 字段（数字列转 float）
FIELDS = ["代码", "名称", "TTM股息率%", "真实股息率%", "估值区间", "市赚率PR",
          "行业", "可持续性", "ROE%", "总市值(亿)", "数据来源"]
NUMERIC = {"TTM股息率%", "真实股息率%", "市赚率PR", "ROE%", "总市值(亿)"}


def parse_csv(path: Path) -> list:
    """解析一个 CSV 文件 → list[dict]（数字转 float）。"""
    rows = []
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            item = {}
            for field in FIELDS:
                val = row.get(field, "").strip()
                if field in NUMERIC and val:
                    try:
                        item[field] = float(val)
                    except ValueError:
                        item[field] = val
                else:
                    item[field] = val
            rows.append(item)
    return rows


def main():
    if not CSV_DIR.exists():
        print(f"✘ 缺少 {CSV_DIR}", file=sys.stderr)
        return 1

    # 收集所有 CSV，按日期聚合（同日多批次取最新）
    csvs = sorted(CSV_DIR.glob("screener_*.csv"))
    if not csvs:
        print("✘ 无 CSV 结果", file=sys.stderr)
        return 1

    by_date = defaultdict(list)
    for c in csvs:
        # 文件名 screener_YYYYMMDD_HHMMSS.csv → 日期 YYYY-MM-DD
        stem = c.stem  # screener_20260809_144134
        parts = stem.split("_")
        if len(parts) >= 2:
            date = parts[1]
            if len(date) == 8:
                by_date[f"{date[:4]}-{date[4:6]}-{date[6:8]}"].append(c)

    if not by_date:
        print("✘ 无法解析日期", file=sys.stderr)
        return 1

    # 生成 site/screener/
    SITE_DIR.mkdir(parents=True, exist_ok=True)

    # 每个日期 → screener_<date>.json（取该日最新 CSV）
    history = []
    for date in sorted(by_date.keys()):
        files = sorted(by_date[date])
        latest = files[-1]  # 该日最后批次
        rows = parse_csv(latest)
        (SITE_DIR / f"screener_{date}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        history.append({"date": date, "file": f"screener_{date}.json", "count": len(rows)})

    # latest.json = 最近日期
    latest_date = history[-1]["date"]
    latest_rows = parse_csv(sorted(by_date[latest_date])[-1])
    (SITE_DIR / "latest.json").write_text(
        json.dumps(latest_rows, ensure_ascii=False, indent=2), encoding="utf-8")

    # history.json
    (SITE_DIR / "history.json").write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✓ 导出 {len(history)} 个日期到 {SITE_DIR}")
    print(f"  latest: {latest_date}（{len(latest_rows)} 只）")
    for h in history:
        print(f"  {h['date']}: {h['count']} 只")
    return 0


if __name__ == "__main__":
    sys.exit(main())
