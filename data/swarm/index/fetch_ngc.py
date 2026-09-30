#!/usr/bin/env python3
"""Fetch National Gallery of Canada open search hits for Lawren Harris (HTML/API best-effort)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from http_util import get, save_json, save_text

DIR = Path(__file__).resolve().parent / "raw"

# NGC collection search — try public search page and a few known artwork pages
SEARCH_URLS = [
    "https://www.gallery.ca/collection/artist/lawren-s-harris",
    "https://www.gallery.ca/collection/search-the-collection?keys=Lawren%20Harris&type=artwork",
]


def main() -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    results = []
    for i, url in enumerate(SEARCH_URLS):
        out = DIR / f"ngc_page_{i}.html"
        if out.exists() and out.stat().st_size > 500:
            print(f"skip {out.name}")
            html = out.read_text(encoding="utf-8", errors="replace")
        else:
            try:
                print(f"fetch {url}")
                html = get(url, timeout=60)
                save_text(out, html)
            except Exception as e:
                print(f"  fail: {e}")
                results.append({"url": url, "error": str(e)})
                continue
        results.append({"url": url, "bytes": len(html), "path": str(out)})
    save_json(DIR / "ngc_fetch_meta.json", results)
    print("ngc done")


if __name__ == "__main__":
    main()
