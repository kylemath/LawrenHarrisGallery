#!/usr/bin/env python3
"""Helpers + offline merger for NGC records collected via browser.

National Gallery of Canada blocks plain curl (Cloudflare). Use the
cursor-ide-browser MCP to open search pages and artwork pages, then
paste collected JSON into ngc_links.json / run merge from metadata.

This script can also merge a JSONL dump produced by the browser harvest
(ngc_harvest.jsonl) into metadata.jsonl.
"""
from __future__ import annotations

import base64
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
IMG_DIR = OUT / "images" / "national_gallery_canada"
META = OUT / "metadata.jsonl"
CSV_OUT = OUT / "metadata.csv"

# Known search URL (artist facet). Pages are 0-indexed in Drupal (?page=0..8).
NGC_SEARCH = (
    "https://www.gallery.ca/collection/search-the-collection"
    "?f%5B0%5D=field_reference_artist%253Atitle%3ALawren%20S.%20Harris"
)


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:120] or "untitled"


def year_from(s):
    m = re.search(r"\b(1[89]\d\d|19[0-7]\d)\b", s or "")
    return int(m.group(1)) if m else None


def load_records():
    recs = {}
    if META.exists():
        for line in META.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                recs[r["key"]] = r
    return recs


def save_records(recs):
    with META.open("w") as f:
        for r in recs.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    cols = [
        "key", "source", "title", "year", "date_raw", "collection", "inventory_no",
        "medium", "dimensions", "genre", "style", "description", "license",
        "page_url", "image_url", "local_path", "sha256", "width", "height",
        "wikidata_id", "confidence", "notes",
    ]
    with CSV_OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in recs.values():
            w.writerow(r)


def parse_ngc_text(text):
    """Parse tombstone fields from artwork page innerText."""
    def field(label):
        m = re.search(rf"{label}\s*\n\s*([^\n]+)", text)
        return m.group(1).strip() if m else None

    title = field("Title")
    date_raw = field("Date")
    medium = field("Materials") or field("Medium")
    dims = field("Dimensions")
    inv = field("Accession number")
    credit = field("Credit line")
    artist = field("Artist")
    desc = None
    # description is often the paragraph before Artist
    m = re.search(r"Category:.*?\n(.+?)\nArtist\n", text, re.S)
    if m:
        desc = re.sub(r"\s+", " ", m.group(1)).strip()
    return {
        "title": title,
        "date_raw": date_raw,
        "year": year_from(date_raw),
        "medium": medium,
        "dimensions": dims,
        "inventory_no": inv,
        "description": desc,
        "artist": artist,
        "credit": credit,
    }


def merge_harvest(path: Path):
    """Merge ngc_harvest.jsonl lines: {page_url, text, image_url, image_b64?}."""
    recs = load_records()
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    n = 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        page_url = item["page_url"]
        fields = parse_ngc_text(item.get("text") or "")
        if fields.get("artist") and "Lawren P" in fields["artist"] and "Lawren S" not in fields["artist"]:
            continue
        if fields.get("artist") and "Lawren S" not in (fields["artist"] or "") and "Lawren Harris" not in (fields["artist"] or ""):
            continue
        slug = slugify(page_url.rstrip("/").split("/")[-1] + "-" + (fields.get("title") or ""))
        key = f"ngc:{slug}"
        img_url = item.get("image_url")
        # prefer unsized files URL
        if img_url and "/styles/" in img_url:
            m = re.search(r"/styles/[^/]+/public/(.+?)(?:\?|$)", img_url)
            if m:
                img_url = f"https://www.gallery.ca/sites/default/files/{m.group(1)}"
        rec = {
            "key": key,
            "source": "national_gallery_canada",
            "title": fields.get("title"),
            "year": fields.get("year"),
            "date_raw": fields.get("date_raw"),
            "collection": "National Gallery of Canada",
            "inventory_no": fields.get("inventory_no"),
            "medium": fields.get("medium"),
            "dimensions": fields.get("dimensions"),
            "genre": None,
            "style": None,
            "description": fields.get("description"),
            "license": "NGC image reproduction terms; Harris d.1970 PD in Canada for the work",
            "page_url": page_url,
            "image_url": img_url,
            "local_path": None,
            "sha256": None,
            "width": None,
            "height": None,
            "wikidata_id": None,
            "confidence": "high",
            "notes": f"credit: {fields.get('credit')}" if fields.get("credit") else "Harvested via browser (Cloudflare)",
        }
        b64 = item.get("image_b64")
        if b64:
            data = base64.b64decode(b64)
            ext = ".jpg"
            out = IMG_DIR / f"{slug}{ext}"
            out.write_bytes(data)
            rec["local_path"] = str(out.relative_to(PROJECT))
            rec["sha256"] = hashlib.sha256(data).hexdigest()
            try:
                from PIL import Image
                import io
                with Image.open(io.BytesIO(data)) as im:
                    rec["width"], rec["height"] = im.size
            except Exception:
                pass
        recs[key] = rec
        n += 1
        print("merged", key)
    save_records(recs)
    print("merged", n, "NGC records; total", len(recs))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "merge":
        merge_harvest(Path(sys.argv[2] if len(sys.argv) > 2 else OUT / "ngc_harvest.jsonl"))
    else:
        print("NGC_SEARCH=", NGC_SEARCH)
        print("Usage: python scrape_ngc_browser.py merge [ngc_harvest.jsonl]")
