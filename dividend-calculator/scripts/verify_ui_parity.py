#!/usr/bin/env python3
"""比对双端 UI（site/index.html vs src/static/index.html）的结构一致性。

UI 渐进优化（to-spec 批准）的主要测试接缝：双端是同一套页面的两个实现
（GitHub Pages 静态版 + 本地 Web 服务版），结构必须保持同步，否则视觉/功能漂移。

本脚本比对：
  1. DOM id 集合（两端必须完全一致）
  2. result-card 卡片骨架（卡片标题文案）

用法:
    python scripts/verify_ui_parity.py
"""
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SITE_HTML = PROJECT_ROOT / "site" / "index.html"
STATIC_HTML = PROJECT_ROOT / "src" / "static" / "index.html"

ID_RE = re.compile(r'id="([^"]+)"')
CARD_RE = re.compile(r'<div class="label">([^<]+)</div>')


def collect_ids(html: str) -> set:
    return set(ID_RE.findall(html))


def collect_card_titles(html: str) -> list:
    return CARD_RE.findall(html)


def main() -> int:
    ok = True

    for label, path in [("site", SITE_HTML), ("static", STATIC_HTML)]:
        if not path.exists():
            print(f"✘ 缺失文件: {path}")
            return 1

    site_html = SITE_HTML.read_text(encoding="utf-8")
    static_html = STATIC_HTML.read_text(encoding="utf-8")

    site_ids = collect_ids(site_html)
    static_ids = collect_ids(static_html)

    only_site = site_ids - static_ids
    only_static = static_ids - site_ids

    if only_site or only_static:
        ok = False
        print(f"✘ id 集合不一致: site={len(site_ids)} static={len(static_ids)}")
        if only_site:
            print(f"  仅 site 有: {sorted(only_site)}")
        if only_static:
            print(f"  仅 static 有: {sorted(only_static)}")
    else:
        print(f"✓ id 集合一致 ({len(site_ids)} 个)")

    site_cards = collect_card_titles(site_html)
    static_cards = collect_card_titles(static_html)

    if site_cards != static_cards:
        ok = False
        print(f"✘ result-card 标题不一致: site={len(site_cards)} static={len(static_cards)}")
        for a, b in zip(site_cards, static_cards):
            if a != b:
                print(f"  site={a!r} static={b!r}")
    else:
        print(f"✓ result-card 标题一致 ({len(site_cards)} 个)")

    print("\n" + ("✔ 双端 UI 结构一致" if ok else "✘ 存在结构差异，见上"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
