#!/usr/bin/env python3
"""Fetch Lawren Harris works from WikiArt text-list JSON (resumable)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from http_util import get, load_json, save_json

RAW = Path(__file__).resolve().parent / "raw" / "wikiart_pages.json"
BASE = "https://www.wikiart.org/en/lawren-harris/all-works/text-list"


def main() -> None:
    pages = load_json(RAW) if RAW.exists() else {}
    page = 1
    while True:
        key = str(page)
        if key in pages and pages[key].get("Paintings") is not None:
            data = pages[key]
        else:
            url = f"{BASE}?json=2&page={page}"
            print(f"fetch wikiart page {page}")
            data = get(url, as_json=True)
            pages[key] = data
            save_json(RAW, pages)
        paintings = data.get("Paintings") or []
        total = data.get("AllPaintingsCount") or 0
        print(f"  page {page}: {len(paintings)} items (total reported {total})")
        if not paintings:
            break
        # continue while we may have more (API sometimes returns < PageSize)
        got = sum(len((p.get("Paintings") or [])) for p in pages.values())
        if got >= total or len(paintings) == 0:
            # still try one more page if got < total
            if got >= total:
                break
        page += 1
        if page > 10:
            break
    # also cache HTML text-list (often fuller title list)
    html_path = Path(__file__).resolve().parent / "raw" / "wikiart_text_list.html"
    if not html_path.exists():
        try:
            print("fetch wikiart HTML text-list")
            from http_util import save_text
            html = get("https://www.wikiart.org/en/lawren-harris/all-works/text-list", timeout=60)
            save_text(html_path, html)
        except Exception as e:
            print(f"  html fail: {e}")
    n = sum(len((p.get("Paintings") or [])) for p in pages.values())
    print(f"wikiart: {n} painting entries across {len(pages)} pages -> {RAW}")


if __name__ == "__main__":
    main()
