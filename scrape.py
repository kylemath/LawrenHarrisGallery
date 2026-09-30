#!/usr/bin/env python3
"""Scrape images + metadata for paintings by Lawren Harris (1885-1970).

Sources:
  wikiart   - JSON listing + per-painting page (title, year, gallery, medium, ...)
  commons   - Wikimedia Commons category tree + text search (extmetadata)
  wikidata  - structured works (title, inception, collection, inventory no.)

Usage:
  source .venv/bin/activate
  python scrape.py                       # all sources
  python scrape.py --sources commons     # one source
  python scrape.py --no-images           # metadata only

Output (in ./data):
  images/<source>/<slug>.<ext>
  metadata.jsonl   one record per image/work (resumable; merged by key)
  metadata.csv     flat export for spreadsheets
Re-running is safe: existing images/records are skipped.
"""
import argparse
import csv
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from bs4 import BeautifulSoup

PROJECT = Path(__file__).resolve().parent
ROOT = PROJECT / "data"
IMG_DIR = ROOT / "images"
META = ROOT / "metadata.jsonl"
CSV_OUT = ROOT / "metadata.csv"

UA = "LawrenHarrisScraper/0.1 (personal research; contact: local user)"
session = requests.Session()
session.headers.update({"User-Agent": UA})

WIKIDATA_ARTIST = "Q3106117"
COMMONS_ROOT_CATS = ["Category:Paintings by Lawren Harris"]
COMMONS_SEARCHES = ['"Lawren Harris" painting', '"Lawren S. Harris"', 'incategory:"Lawren Harris"']
IMG_EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
           "image/tiff": ".jpg", "image/gif": ".gif"}


# ----------------------------------------------------------------- helpers
def get(url, *, params=None, delay=1.0, retries=4, **kw):
    for i in range(retries):
        try:
            r = session.get(url, params=params, timeout=60, **kw)
            if r.status_code in (429, 503):
                time.sleep(5 * (i + 1))
                continue
            r.raise_for_status()
            time.sleep(delay)
            return r
        except requests.RequestException as e:
            if i == retries - 1:
                print(f"  ! failed {url}: {e}")
                return None
            time.sleep(2 * (i + 1))
    return None


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:120] or "untitled"


def strip_html(s):
    return BeautifulSoup(s or "", "html.parser").get_text(" ", strip=True)


def load_records():
    recs = {}
    if META.exists():
        for line in META.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                recs[r["key"]] = r
    return recs


def save_records(recs):
    ROOT.mkdir(parents=True, exist_ok=True)
    with META.open("w") as f:
        for r in recs.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    cols = ["key", "source", "title", "year", "date_raw", "collection", "inventory_no",
            "medium", "dimensions", "genre", "style", "description", "license",
            "page_url", "image_url", "local_path", "sha256", "width", "height",
            "wikidata_id", "confidence"]
    with CSV_OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in recs.values():
            w.writerow(r)


def download(rec, want_images=True):
    """Download rec['image_url'] -> rec['local_path'], fill sha256/size."""
    url = rec.get("image_url")
    if not url or not want_images:
        return
    if rec.get("local_path") and (PROJECT / rec["local_path"]).exists():
        return
    ext = Path(urlparse(url).path).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"
    d = IMG_DIR / rec["source"]
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slugify(rec['key'].split(':', 1)[-1])}{ext}"
    r = get(url, delay=0.5, stream=False)
    if r is None or not r.content:
        return
    ctype = r.headers.get("content-type", "")
    if not ctype.startswith("image/"):
        print(f"  ! not an image: {url} ({ctype})")
        return
    path.write_bytes(r.content)
    rec["local_path"] = str(path.relative_to(PROJECT))
    rec["sha256"] = hashlib.sha256(r.content).hexdigest()
    try:
        from PIL import Image
        with Image.open(path) as im:
            rec["width"], rec["height"] = im.size
    except Exception:
        pass


def year_from(s):
    m = re.search(r"\b(1[89]\d\d|19[0-7]\d)\b", s or "")
    return int(m.group(1)) if m else None


# ----------------------------------------------------------------- wikiart
def scrape_wikiart(recs, want_images):
    print("[wikiart] listing works")
    base = "https://www.wikiart.org/en/lawren-harris/all-works/text-list"
    items = []
    page = 1
    while True:
        r = get(base, params={"json": 2, "page": page})
        if r is None:
            break
        paintings = r.json().get("Paintings") or []
        if not paintings:
            break
        items += paintings
        if len(paintings) < 10 and page > 1:
            break
        page += 1
        if page > 100:
            break
    # also the masonry listing, in case text-list is capped
    seen = {p["paintingUrl"] for p in items}
    page = 1
    while page < 50:
        r = get("https://www.wikiart.org/en/lawren-harris",
                params={"json": 2, "layout": "new", "page": page, "resultType": "masonry"})
        if r is None:
            break
        try:
            paintings = r.json().get("Paintings") or []
        except ValueError:
            break
        new = [p for p in paintings if p["paintingUrl"] not in seen]
        if not paintings:
            break
        for p in new:
            seen.add(p["paintingUrl"])
        items += new
        page += 1
    print(f"[wikiart] {len(items)} works listed")

    for n, p in enumerate(items, 1):
        slug = p["paintingUrl"].rstrip("/").split("/")[-1]
        key = f"wikiart:{slug}"
        rec = recs.get(key, {})
        rec.update({
            "key": key, "source": "wikiart", "title": p.get("title"),
            "year": year_from(p.get("year")), "date_raw": p.get("year"),
            "page_url": "https://www.wikiart.org" + p["paintingUrl"],
            "image_url": p.get("image"), "confidence": "high",
        })
        if "description" not in rec:  # fetch detail page once
            r = get(rec["page_url"], delay=1.0)
            if r is not None:
                soup = BeautifulSoup(r.text, "html.parser")
                info = {}
                for li in soup.select("li"):
                    s = li.find("s")
                    if s and s.get_text(strip=True).endswith(":"):
                        label = s.get_text(strip=True)[:-1].lower()
                        val = li.get_text(" ", strip=True)[len(s.get_text()):].strip()
                        info[label] = re.sub(r"\s+", " ", val)
                rec["medium"] = info.get("media") or info.get("material")
                rec["dimensions"] = info.get("dimensions")
                rec["collection"] = info.get("gallery") or info.get("collection")
                rec["genre"] = info.get("genre")
                rec["style"] = info.get("style")
                if info.get("date"):
                    rec["date_raw"] = info["date"]
                    rec["year"] = year_from(info["date"]) or rec["year"]
                d = soup.select_one('[itemprop="description"], .wiki-layout-painting-info p')
                rec["description"] = d.get_text(" ", strip=True) if d else ""
                rec["license"] = "see page (Harris d. 1970; public domain in Canada)"
        download(rec, want_images)
        recs[key] = rec
        if n % 10 == 0:
            print(f"[wikiart] {n}/{len(items)}")
            save_records(recs)


# ----------------------------------------------------------------- commons
COMMONS_API = "https://commons.wikimedia.org/w/api.php"


def commons_imageinfo(titles):
    out = []
    for i in range(0, len(titles), 40):
        chunk = titles[i:i + 40]
        r = get(COMMONS_API, params={
            "action": "query", "format": "json", "titles": "|".join(chunk),
            "prop": "imageinfo", "iiprop": "url|extmetadata|size|mime",
            "iiurlwidth": 1920}, delay=0.3)
        if r is None:
            continue
        for pg in r.json().get("query", {}).get("pages", {}).values():
            if "imageinfo" in pg:
                out.append((pg["title"], pg["imageinfo"][0]))
    return out


def commons_category_files(cat, seen_cats):
    files = []
    if cat in seen_cats:
        return files
    seen_cats.add(cat)
    cont = {}
    while True:
        r = get(COMMONS_API, params={"action": "query", "format": "json", "list": "categorymembers",
                                     "cmtitle": cat, "cmlimit": 500, "cmtype": "file|subcat", **cont},
                delay=0.3)
        if r is None:
            break
        j = r.json()
        for m in j["query"]["categorymembers"]:
            if m["title"].startswith("Category:"):
                files += commons_category_files(m["title"], seen_cats)
            else:
                files.append(m["title"])
        if "continue" not in j:
            break
        cont = j["continue"]
    return files


def commons_search_files(q):
    files, cont = [], {}
    while True:
        r = get(COMMONS_API, params={"action": "query", "format": "json", "list": "search",
                                     "srsearch": q, "srnamespace": 6, "srlimit": 50, **cont}, delay=0.3)
        if r is None:
            break
        j = r.json()
        files += [m["title"] for m in j["query"]["search"]]
        if "continue" not in j:
            break
        cont = j["continue"]
    return files


def scrape_commons(recs, want_images):
    cat_titles = []
    seen_cats = set()
    for c in COMMONS_ROOT_CATS:
        cat_titles += commons_category_files(c, seen_cats)
    cat_titles = list(dict.fromkeys(cat_titles))
    print(f"[commons] {len(cat_titles)} files in painting categories")
    search_titles = []
    for q in COMMONS_SEARCHES:
        search_titles += commons_search_files(q)
    search_titles = [t for t in dict.fromkeys(search_titles) if t not in cat_titles]
    print(f"[commons] {len(search_titles)} extra files from search (lower confidence)")

    for group, conf in ((cat_titles, "high"), (search_titles, "low")):
        for title, ii in commons_imageinfo(group):
            mime = ii.get("mime", "")
            if mime not in IMG_EXT:
                continue
            em = ii.get("extmetadata", {})
            val = lambda k: strip_html(em.get(k, {}).get("value", ""))
            key = "commons:" + slugify(title.replace("File:", ""))
            rec = recs.get(key, {})
            url = ii["url"] if mime in ("image/jpeg", "image/png", "image/webp") else ii.get("thumburl", ii["url"])
            date_raw = val("DateTimeOriginal") or val("DateTime")
            rec.update({
                "key": key, "source": "commons",
                "title": val("ObjectName") or title.replace("File:", "").rsplit(".", 1)[0],
                "date_raw": date_raw, "year": year_from(date_raw) or year_from(title),
                "description": val("ImageDescription"),
                "license": val("LicenseShortName"),
                "page_url": ii.get("descriptionurl"), "image_url": url,
                "width": ii.get("width"), "height": ii.get("height"),
                "confidence": conf,
            })
            download(rec, want_images)
            recs[key] = rec
        save_records(recs)


# ---------------------------------------------------------------- wikidata
def scrape_wikidata(recs, want_images):
    q = f"""
    SELECT ?item ?itemLabel ?inception ?image ?collectionLabel ?inv ?mediumLabel ?genreLabel ?commons WHERE {{
      ?item wdt:P170 wd:{WIKIDATA_ARTIST} .
      OPTIONAL {{?item wdt:P571 ?inception}} OPTIONAL {{?item wdt:P18 ?image}}
      OPTIONAL {{?item wdt:P195 ?collection}} OPTIONAL {{?item wdt:P217 ?inv}}
      OPTIONAL {{?item wdt:P186 ?medium}} OPTIONAL {{?item wdt:P136 ?genre}}
      OPTIONAL {{?item wdt:P373 ?commons}}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }}"""
    r = get("https://query.wikidata.org/sparql", params={"query": q, "format": "json"})
    if r is None:
        return
    rows = r.json()["results"]["bindings"]
    print(f"[wikidata] {len(rows)} rows")
    for x in rows:
        v = lambda k: x.get(k, {}).get("value")
        qid = v("item").rsplit("/", 1)[-1]
        key = f"wikidata:{qid}"
        rec = recs.get(key, {})
        img = v("image")
        rec.update({
            "key": key, "source": "wikidata", "wikidata_id": qid,
            "title": v("itemLabel"), "date_raw": (v("inception") or "")[:10],
            "year": year_from(v("inception")), "collection": v("collectionLabel"),
            "inventory_no": v("inv"), "medium": v("mediumLabel"), "genre": v("genreLabel"),
            "page_url": f"https://www.wikidata.org/wiki/{qid}",
            "image_url": ("https://commons.wikimedia.org/wiki/Special:FilePath/"
                          + unquote(img.rsplit("/", 1)[-1]) + "?width=1920") if img else None,
            "confidence": "high",
        })
        download(rec, want_images)
        recs[key] = rec
    save_records(recs)


# -------------------------------------------------------------------- main
SOURCES = {"wikiart": scrape_wikiart, "commons": scrape_commons, "wikidata": scrape_wikidata}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", nargs="+", choices=SOURCES, default=list(SOURCES))
    ap.add_argument("--no-images", action="store_true", help="metadata only")
    ap.add_argument("--out", type=Path, default=None,
                    help="output dir (default ./data); use to isolate parallel runs")
    args = ap.parse_args()
    if args.out:
        global ROOT, IMG_DIR, META, CSV_OUT
        ROOT = args.out.resolve()
        IMG_DIR, META, CSV_OUT = ROOT / "images", ROOT / "metadata.jsonl", ROOT / "metadata.csv"
    ROOT.mkdir(parents=True, exist_ok=True)
    recs = load_records()
    for s in args.sources:
        try:
            SOURCES[s](recs, not args.no_images)
        finally:
            save_records(recs)
    print(f"done: {len(recs)} records, "
          f"{sum(1 for r in recs.values() if r.get('local_path'))} images -> {ROOT}")


if __name__ == "__main__":
    main()
