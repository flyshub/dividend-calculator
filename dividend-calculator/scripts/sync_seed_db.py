#!/usr/bin/env python3
"""把生产 Release 的 screener.db 同步到本地，供本地漏斗/复盘/排查使用。

背景：生产数据由 CI 每日/月频刷新后上传到 Release tag `screener-seed`，本地
`data/screener.db` 不会自动更新。本地库长期滞后（财务/可持续性可能停在几个月前）
会让"某只股票缺财务/被淘汰"的本地判断变成假象——排查前先同步。

用法：
    python scripts/sync_seed_db.py                 # 下载 + 校验 + 原子替换
    python scripts/sync_seed_db.py --dry-run       # 只下载校验，不动现有库
    python scripts/sync_seed_db.py --no-backup     # 不保留旧库备份

校验不通过（缺表/股票池过小/文件损坏）直接退出非 0，绝不覆盖现有库。
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DEST = ROOT / "data" / "screener.db"
RELEASE_TAG = "screener-seed"
ASSET_NAME = "screener.db"
RELEASE_API = "https://api.github.com/repos/flyshub/dividend-calculator/releases/tags/{tag}"

# 与 ScreenerCache.prune_orphan_rows 同一下限：低于它视为股票池写坏
MIN_UNIVERSE = 1000
REQUIRED_TABLES = (
    "stock_list",
    "quote_snapshot",
    "dividend_snapshot",
    "finance_snapshot",
    "sustainability_snapshot",
)
COVERAGE_TABLES = ("quote_snapshot", "dividend_snapshot", "finance_snapshot", "sustainability_snapshot")


@dataclass
class SeedStats:
    """本地可读的种子库校验结果。"""

    path: Path
    size_bytes: int
    universe: int
    tables: List[str]
    counts: Dict[str, int]
    latest_updated: Dict[str, Optional[str]]

    @property
    def ok(self) -> bool:
        # 与 CI 的 check_seed_guard 对齐：行情之外的库（只剩股票池）视为写坏
        return (self.universe >= MIN_UNIVERSE
                and self.counts.get("dividend_snapshot", 0) >= MIN_UNIVERSE
                and all(t in self.tables for t in REQUIRED_TABLES))


def asset_url(payload: dict, name: str = ASSET_NAME) -> str:
    """从 Release API 响应里取资产的下载地址；找不到抛 KeyError。"""
    for asset in payload.get("assets") or []:
        if asset.get("name") == name and asset.get("browser_download_url"):
            return asset["browser_download_url"]
    raise KeyError(f"Release 里没有 {name}")


def validate_seed(path: Path) -> SeedStats:
    """只读校验：完整性、表齐全、股票池规模、各表行数与最近更新时间。

    先跑 `PRAGMA quick_check`：下载被截断 1%~5% 时 sqlite 仍能读页面头和行数，
    只有尾部页损坏——不查完整性会把这种文件装到本地（后续报 disk image is malformed）。
    """
    path = Path(path)
    size = path.stat().st_size
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        integrity = con.execute("PRAGMA quick_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise sqlite3.DatabaseError(
                f"完整性校验失败: {integrity[0] if integrity else '无结果'}")
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        counts: Dict[str, int] = {}
        latest: Dict[str, Optional[str]] = {}
        for t in ("stock_list",) + COVERAGE_TABLES:
            if t not in tables:
                continue
            counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            if t in COVERAGE_TABLES:
                try:
                    latest[t] = con.execute(
                        f"SELECT MAX(updated_at) FROM {t}").fetchone()[0]
                except sqlite3.OperationalError:  # 旧库没有 updated_at 列
                    latest[t] = None
        universe = counts.get("stock_list", 0)
    finally:
        con.close()
    return SeedStats(path=path, size_bytes=size, universe=universe,
                     tables=tables, counts=counts, latest_updated=latest)


def format_report(stats: SeedStats) -> str:
    lines = [f"库文件: {stats.path}  ({stats.size_bytes / 1e6:.0f} MB)",
             f"股票池: {stats.universe} 只"]
    for t in COVERAGE_TABLES:
        n = stats.counts.get(t)
        if n is None:
            lines.append(f"  {t:26s} 缺失表")
            continue
        pct = (n / stats.universe * 100) if stats.universe else 0.0
        lines.append(f"  {t:26s} {n:>6d} 行 ({pct:5.1f}%)  最近 {stats.latest_updated.get(t) or '未知'}")
    missing = [t for t in REQUIRED_TABLES if t not in stats.tables]
    if missing:
        lines.append(f"缺表: {', '.join(missing)}")
    if stats.universe < MIN_UNIVERSE:
        lines.append(f"股票池 {stats.universe} < {MIN_UNIVERSE}，判定为写坏的库")
    return "\n".join(lines)


def download(url: str, dest: Path, timeout: int = 300, retries: int = 3) -> Path:
    """流式下载到 dest（调用方保证 dest 是临时路径）。

    186MB 走 GitHub 的 release-assets 重定向，实测偶发读超时，因此默认重试 3 次。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, stream=True, timeout=timeout) as resp:
                resp.raise_for_status()
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=1 << 20):
                        if chunk:
                            fh.write(chunk)
            return dest
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < retries:
                print(f"[sync] 下载中断（第 {attempt} 次）：{exc}，重试…", file=sys.stderr)
                time.sleep(2 * attempt)
    raise last_exc  # type: ignore[misc]


def install(tmp: Path, dest: Path, *, backup: bool = True,
            keep_backups: int = 7) -> Optional[Path]:
    """替换目标库；backup=True 时先把旧库改名留档。返回备份路径。

    失败时回滚：旧库已改名而新库没落位（跨文件系统 os.replace 失败等）时
    把备份改回 dest，绝不留下"目标库不存在"的状态。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    backup_path: Optional[Path] = None
    if dest.exists() and backup:
        stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
        backup_path = dest.with_name(f"{dest.name}.bak-{stamp}")
        n = 1
        while backup_path.exists():  # 同一秒重复运行不覆盖前一份
            backup_path = dest.with_name(f"{dest.name}.bak-{stamp}-{n}")
            n += 1
        dest.replace(backup_path)
    try:
        os.replace(tmp, dest)
    except OSError as exc:
        if backup_path is not None and backup_path.exists() and not dest.exists():
            # 同一目录改名即可（不依赖刚失败的那条路径）
            backup_path.rename(dest)
        raise exc
    if backup_path is not None and keep_backups > 0:
        _prune_backups(dest, keep=keep_backups)
    return backup_path


def _prune_backups(dest: Path, keep: int = 7) -> List[Path]:
    """只保留最近 keep 份本地备份（按文件名里的时间戳排序）。"""
    backups = sorted(dest.parent.glob(f"{dest.name}.bak-*"))
    removed = backups[:-keep] if len(backups) > keep else []
    for path in removed:
        try:
            path.unlink()
        except OSError:
            pass
    return removed


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="同步生产 screener.db 到本地")
    parser.add_argument("--dest", default=str(DEFAULT_DEST), help="目标库路径")
    parser.add_argument("--tag", default=RELEASE_TAG, help="Release tag（默认 screener-seed）")
    parser.add_argument("--dry-run", action="store_true", help="只下载校验，不替换")
    parser.add_argument("--no-backup", action="store_true", help="不保留旧库备份")
    parser.add_argument("--timeout", type=int, default=300, help="单次下载读超时秒数")
    args = parser.parse_args(argv)

    dest = Path(args.dest).expanduser()
    try:
        resp = requests.get(RELEASE_API.format(tag=args.tag), timeout=30,
                            headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        url = asset_url(resp.json())
    except Exception as exc:  # noqa: BLE001 — 网络/接口变化都按失败处理
        print(f"[sync] 取 Release 资产地址失败: {exc}", file=sys.stderr)
        return 1

    # 暂存文件必须与目标库同一文件系统，os.replace 才是原子的（跨盘会失败且
    # 失败时旧库已被改名，等于把本地库弄丢）
    dest.parent.mkdir(parents=True, exist_ok=True)
    staged = dest.with_name(f".{dest.name}.download-{os.getpid()}")
    try:
        try:
            download(url, staged, timeout=args.timeout)
        except Exception as exc:  # noqa: BLE001
            print(f"[sync] 下载失败: {exc}", file=sys.stderr)
            return 1

        try:
            stats = validate_seed(staged)
        except sqlite3.DatabaseError as exc:
            print(f"[sync] 下载的文件不完整或不可读: {exc}", file=sys.stderr)
            return 1
        print(format_report(stats))
        if not stats.ok:
            print("[sync] 校验未通过，保留现有库不动。", file=sys.stderr)
            return 1

        if args.dry_run:
            print("[sync] --dry-run：校验通过，未替换。")
            return 0

        try:
            backup_path = install(staged, dest, backup=not args.no_backup)
        except OSError as exc:
            print(f"[sync] 替换失败（旧库已回滚）: {exc}", file=sys.stderr)
            return 1
        print(f"[sync] 已替换 {dest}"
              + (f"，旧库备份 {backup_path}" if backup_path else ""))
        return 0
    finally:
        if staged.exists():
            staged.unlink()


if __name__ == "__main__":
    sys.exit(main())
