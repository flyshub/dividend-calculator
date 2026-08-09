#!/usr/bin/env python3
"""选股器每日结果 → GitHub Pages JSON（spec: Pages 展示每日选股）。

将 data/screener/screener_*.csv 转换为 site/screener/ 下的 JSON：
- latest.json：最新一日的股票列表（11 字段，数字转 float）
- history.json：所有历史日期索引 [{date, file}]（按日期聚合，同日多批次取最新）
- screener_<date>.json：每个历史日期的独立文件（供历史切换读取）

同一份产物同时写入 site/screener/（GitHub Pages）与 src/static/screener/
（本地 Web 服务版，src/web.py 只 serve src/static/），保证双端一致。

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
STATIC_SITE_DIR = PROJECT_ROOT / "src" / "static" / "screener"

# CSV 11 列 → JSON 字段（数字列转 float）。两处均与 site/screener.html 的列定义同步
# （screener_daily.yml 每日跑本脚本时，export 会校验 CSV 表头与 FIELDS 一致，防漂移）。
FIELDS = ["代码", "名称", "TTM股息率%", "真实股息率%", "估值区间", "市赚率PR",
          "行业", "可持续性", "ROE%", "总市值(亿)", "数据来源"]
NUMERIC = {"TTM股息率%", "真实股息率%", "市赚率PR", "ROE%", "总市值(亿)"}


def parse_csv(path: Path) -> list:
    """解析一个 CSV 文件 → list[dict]（数字转 float）。

    校验表头与 FIELDS 一致：缺列/换列说明选股器输出变更，需同步脚本与页面列定义，
    此时直接报错（数据铁律：口径不准宁可失败不输出错误数据）。
    """
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        if header != FIELDS:
            missing = [c for c in FIELDS if c not in header]
            extra = [c for c in header if c not in FIELDS]
            raise ValueError(
                f"CSV 表头与 FIELDS 不一致: {path.name}\n"
                f"  期望 {len(FIELDS)} 列，实际 {len(header)} 列\n"
                f"  缺: {missing or '无'}\n  多: {extra or '无'}")
        rows = []
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


def write_json_files(out_dir: Path, by_date: dict) -> list:
    """写 latest/history/按日 JSON 到指定目录，返回 history 列表。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    history = []
    for date in sorted(by_date.keys()):
        files = sorted(by_date[date])
        latest = files[-1]  # 该日最后批次
        rows = parse_csv(latest)
        (out_dir / f"screener_{date}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        history.append({"date": date, "file": f"screener_{date}.json", "count": len(rows)})

    # latest.json = 最近日期
    latest_date = history[-1]["date"]
    latest_rows = parse_csv(sorted(by_date[latest_date])[-1])
    (out_dir / "latest.json").write_text(
        json.dumps(latest_rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # history.json
    (out_dir / "history.json").write_text(
        json.dumps(history, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return history


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

    # 两个输出目录写同一份产物（GitHub Pages + 本地 Web 版）
    history = write_json_files(SITE_DIR, by_date)
    history_static = write_json_files(STATIC_SITE_DIR, by_date)

    if history != history_static:
        print(f"✘ 双目录输出不一致: {SITE_DIR} vs {STATIC_SITE_DIR}", file=sys.stderr)
        return 1

    print(f"✓ 导出 {len(history)} 个日期到 {SITE_DIR} + {STATIC_SITE_DIR}")
    print(f"  latest: {history[-1]['date']}（{history[-1]['count']} 只）")
    for h in history:
        print(f"  {h['date']}: {h['count']} 只")
    return 0


if __name__ == "__main__":
    sys.exit(main())
