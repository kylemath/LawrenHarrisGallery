#!/usr/bin/env python3
"""Fetch Wikipedia article HTML for Lawren Harris (multi-lang) + Commons API filenames."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from http_util import get, save_json, save_text

DIR = Path(__file__).resolve().parent / "raw"
WIKIS = {
    "en": "https://en.wikipedia.org/wiki/Lawren_Harris",
    "fr": "https://fr.wikipedia.org/wiki/Lawren_Harris",
    "de": "https://de.wikipedia.org/wiki/Lawren_Harris",
    "es": "https://es.wikipedia.org/wiki/Lawren_Harris",
}
API = "https://commons.wikimedia.org/w/api.php"


def fetch_wikipedia() -> None:
    for lang, url in WIKIS.items():
        out = DIR / f"wikipedia_{lang}.html"
        if out.exists() and out.stat().st_size > 1000:
            print(f"skip wiki {lang} (cached)")
            continue
        try:
            print(f"fetch wiki {lang}")
            html = get(url, timeout=60)
            save_text(out, html)
        except Exception as e:
            print(f"  fail {lang}: {e}")


def fetch_commons() -> None:
    """Use category HTML only — /w/api.php is Disallow'd by commons robots.txt."""
    out = DIR / "commons_files.json"
    if out.exists():
        print(f"commons json exists ({out.stat().st_size}b); HTML fetch may refresh via fetch_wikidata")
    cats = [
        "https://commons.wikimedia.org/wiki/Category:Paintings_by_Lawren_Harris",
        "https://commons.wikimedia.org/wiki/Category:Lawren_Harris",
    ]
    files = []
    for url in cats:
        name = url.split(":")[-1].replace(" ", "_")
        path = DIR / f"commons_cat_{name}.html"
        try:
            if path.exists() and path.stat().st_size > 500:
                html = path.read_text(encoding="utf-8", errors="replace")
                print(f"skip {path.name}")
            else:
                print(f"fetch {url}")
                html = get(url, timeout=60)
                save_text(path, html)
            import re
            found = re.findall(r"href=\"/wiki/(File:[^\"]+)\"", html)
            files.extend(found)
            print(f"  +{len(found)} files")
        except Exception as e:
            print(f"  fail: {e}")
    # merge if prior
    prev = {}
    if out.exists():
        try:
            prev = json.loads(out.read_text(encoding="utf-8"))
        except Exception:
            prev = {}
    cat = [{"title": t} for t in dict.fromkeys(files)]
    if prev.get("category"):
        seen = {c.get("title") for c in cat}
        for c in prev["category"]:
            if c.get("title") not in seen:
                cat.append(c)
    save_json(out, {"search": prev.get("search") or [], "category": cat, "category_html_files": files})
    print(f"commons: {len(cat)} category files -> {out}")


def fetch_aci() -> None:
    urls = [
        "https://www.aci-iac.ca/art-books/lawren-harris/",
        "https://www.aci-iac.ca/art-books/lawren-harris/key-works/",
        "https://www.aci-iac.ca/art-books/lawren-harris/biography/",
        "https://www.thecanadianencyclopedia.ca/en/article/lawren-stewart-harris",
    ]
    for url in urls:
        name = url.rstrip("/").split("/")[-1] or "index"
        host = "aci" if "aci-iac" in url else "tce"
        out = DIR / f"{host}_{name}.html"
        if out.exists() and out.stat().st_size > 500:
            print(f"skip {out.name}")
            continue
        try:
            print(f"fetch {url}")
            html = get(url, timeout=60)
            save_text(out, html)
        except Exception as e:
            print(f"  fail: {e}")


def main() -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    fetch_wikipedia()
    fetch_commons()
    fetch_aci()


if __name__ == "__main__":
    main()
