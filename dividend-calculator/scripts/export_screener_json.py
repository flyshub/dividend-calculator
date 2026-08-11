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
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 11 列契约单点化（ADR-0001）：与 src.screening.FIELDS 同源，防止选股器输出列漂移
from src.screening import FIELDS  # noqa: E402

CSV_DIR = PROJECT_ROOT / "data" / "screener"
SITE_DIR = PROJECT_ROOT / "site" / "screener"
STATIC_SITE_DIR = PROJECT_ROOT / "src" / "static" / "screener"

# 保留策略：仅保留最近 RETENTION_DAYS 天的 CSV / 按日 JSON（history.json 由重扫天然截断）。
# 90 天覆盖一个完整财报季；git 历史可回滚更早数据。--retention-days 可覆盖。
RETENTION_DAYS = 90

# CSV 11 列 → JSON 字段（数字列转 float）。列定义来自 src.screening.FIELDS，
# （screener_daily.yml 每日跑本脚本时，export 会校验 CSV 表头与 FIELDS 一致，防漂移）。
NUMERIC = {"TTM股息率%", "真实股息率%", "市赚率PR", "ROE%", "总市值(亿)"}


def _csv_date(path: Path):
    """从文件名 screener_YYYYMMDD_HHMMSS.csv 提取日期 'YYYY-MM-DD'；无法解析返回 None。"""
    parts = path.stem.split("_")
    if len(parts) >= 2 and len(parts[1]) == 8:
        ymd = parts[1]
        return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"
    return None


def collect_csvs(cutoff_date: str) -> list:
    """收集保留期内的 CSV，物理删除过期的（保留策略：git 可恢复，见设计文档）。

    cutoff_date: 'YYYY-MM-DD'，文件日期 < cutoff 视为过期删除。解析失败的文件跳过不删
    （宁留不误删，数据铁律）。
    """
    csvs = []
    for c in sorted(CSV_DIR.glob("screener_*.csv")):
        date_str = _csv_date(c)
        if date_str is None:
            print(f"⚠ 无法解析日期，跳过: {c.name}", file=sys.stderr)
            continue
        if date_str < cutoff_date:
            c.unlink()
            print(f"  prune CSV: {c.name}", file=sys.stderr)
        else:
            csvs.append(c)
    return csvs


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

    # 清理孤儿按日 JSON（不在本批 history 的旧文件，防 Pages 目录残留不一致）
    kept = {h["file"] for h in history}
    for f in out_dir.glob("screener_*.json"):
        if f.name not in kept:
            f.unlink()
            print(f"  prune JSON: {f.name}", file=sys.stderr)
    return history


def main(argv: list = None):
    import argparse
    parser = argparse.ArgumentParser(description="选股器 CSV → Pages JSON（含保留期清理）")
    parser.add_argument("--retention-days", type=int, default=RETENTION_DAYS,
                        help="保留最近 N 天的 CSV/按日 JSON（默认 90）")
    # argv=None 时用 sys.argv[1:]（CLI）；测试传 [] 避免读到 pytest 参数
    args = parser.parse_args([] if argv is None else argv)

    if not CSV_DIR.exists():
        print(f"✘ 缺少 {CSV_DIR}", file=sys.stderr)
        return 1

    # 收集保留期内 CSV（物理删除过期的），按日期聚合
    cutoff = (date.today() - timedelta(days=args.retention_days)).isoformat()
    csvs = collect_csvs(cutoff)
    if not csvs:
        print("✘ 无 CSV 结果（保留期内无数据）", file=sys.stderr)
        return 1

    by_date = defaultdict(list)
    for c in csvs:
        date_str = _csv_date(c)
        if date_str is not None:
            by_date[date_str].append(c)

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
    sys.exit(main(sys.argv[1:]))
