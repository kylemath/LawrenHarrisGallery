#!/usr/bin/env python3
"""WikiArt-only scraper for Lawren S. Harris (improved copy of scrape.py).

Improvements vs root scrape.py:
  - Maps WikiArt "Location" -> collection (original looked for gallery/collection)
  - Parses only the painting info <article>, not the unrelated movie block
  - Cross-checks text-list, PaintingsByArtist, /all-works, and sitemap URLs
  - Prefers largest same-host image variant (!HalfHD / bare / !Large / !HD)
  - Always refreshes sparse metadata fields; keeps full schema + notes
  - Flags non-Lawren-S-Harris attributions in notes (does not delete)

Usage:
    cd /Users/fulkanjou/LaurenHarris && source .venv/bin/activate
    python -W ignore data/swarm/wikiart/scrape_wikiart_fixed.py
"""
import argparse
import csv
import hashlib
import html as html_lib
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

PROJECT = Path(__file__).resolve().parents[3]  # .../LaurenHarris
ROOT = Path(__file__).resolve().parent           # .../data/swarm/wikiart
IMG_DIR = ROOT / "images"
META = ROOT / "metadata.jsonl"
CSV_OUT = ROOT / "metadata.csv"

UA = "LawrenHarrisScraper/0.1 (personal research; contact: local user)"
session = requests.Session()
session.headers.update({"User-Agent": UA})

SCHEMA_KEYS = [
    "key", "source", "title", "year", "date_raw", "collection", "inventory_no",
    "medium", "dimensions", "genre", "style", "description", "license",
    "page_url", "image_url", "local_path", "sha256", "width", "height",
    "wikidata_id", "confidence", "notes",
]


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


def head(url, *, delay=0.4, retries=2):
    for i in range(retries):
        try:
            r = session.head(url, timeout=30, allow_redirects=True)
            time.sleep(delay)
            return r
        except requests.RequestException:
            if i == retries - 1:
                return None
            time.sleep(1.5 * (i + 1))
    return None


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:120] or "untitled"


def year_from(s):
    m = re.search(r"\b(1[89]\d\d|19[0-7]\d)\b", s or "")
    return int(m.group(1)) if m else None


def empty_record():
    return {k: None for k in SCHEMA_KEYS}


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
            out = empty_record()
            out.update({k: r.get(k) for k in SCHEMA_KEYS})
            f.write(json.dumps(out, ensure_ascii=False) + "\n")
    with CSV_OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SCHEMA_KEYS, extrasaction="ignore")
        w.writeheader()
        for r in recs.values():
            row = empty_record()
            row.update({k: r.get(k) for k in SCHEMA_KEYS})
            w.writerow(row)


def image_candidates(url):
    """Same-host WikiArt size variants; prefer larger when available."""
    if not url:
        return []
    m = re.match(
        r"(https://uploads\d*\.wikiart\.org/.+\.(?:jpg|jpeg|png|webp))(?:!\w+\.\w+)?$",
        url, re.I,
    )
    if not m:
        return [url]
    base = m.group(1)
    # bare / HalfHD / HD / Large — order tried then ranked by Content-Length
    return [base + s for s in ("!HalfHD.jpg", "", "!HD.jpg", "!Large.jpg")]


def best_image_url(url):
    best = None
    for u in image_candidates(url):
        r = head(u)
        if r is None or r.status_code != 200:
            continue
        ct = r.headers.get("content-type", "")
        if "image" not in ct:
            continue
        cl = int(r.headers.get("content-length") or 0)
        if best is None or cl > best[0]:
            best = (cl, u)
    return best[1] if best else url


def download(rec, want_images=True, force=False):
    url = rec.get("image_url")
    if not url or not want_images:
        return
    if (not force) and rec.get("local_path") and (PROJECT / rec["local_path"]).exists():
        return
    ext = Path(urlparse(url).path).suffix.lower()
    # WikiArt uses path.jpg!HalfHD.jpg — take last real image ext from base
    base_path = re.sub(r"!\w+\.\w+$", "", urlparse(url).path)
    ext = Path(base_path).suffix.lower() or ".jpg"
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
    # skip rewrite if not larger than existing
    if path.exists() and not force:
        if len(r.content) <= path.stat().st_size:
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


def parse_painting_page(html_text):
    """Extract metadata from a WikiArt painting page."""
    soup = BeautifulSoup(html_text, "html.parser")
    info = {}
    # Prefer the first article inside artist-info (painting), not the movie promo
    root = soup.select_one(".wiki-layout-artist-info > article") or soup
    for li in root.select("li"):
        s_el = li.find("s")
        if not s_el:
            continue
        label_raw = s_el.get_text(strip=True)
        if not label_raw.endswith(":"):
            continue
        label = label_raw[:-1].lower()
        if label in ("share", "directed by", "written by", "produced by"):
            continue
        full = li.get_text(" ", strip=True)
        val = full[len(label_raw):].strip() if full.startswith(label_raw) else full
        val = re.sub(r"\s+", " ", val).strip(" ,")
        if val:
            info[label] = val

    # paintingJson bootstrap (HTML-entity JSON)
    pj = {}
    m = re.search(
        r'ng-init="paintingJson\s*=\s*(\{.*?\})"\s',
        html_text, re.S,
    )
    if m:
        try:
            raw = html_lib.unescape(m.group(1))
            pj = json.loads(raw)
        except Exception:
            pj = {}

    creator = ""
    c = soup.select_one('[itemprop="creator"] [itemprop="name"], [itemprop="creator"]')
    if c:
        creator = c.get_text(" ", strip=True)
    d = soup.select_one('[itemprop="description"], .wiki-layout-painting-info p')
    desc = d.get_text(" ", strip=True) if d else ""
    og = soup.select_one('meta[property="og:image"]')
    og_image = og.get("content") if og else None
    return info, pj, creator, desc, og_image


def list_text_list():
    items, seen = [], set()
    page = 1
    while page <= 100:
        r = get("https://www.wikiart.org/en/lawren-harris/all-works/text-list",
                params={"json": 2, "page": page})
        if r is None:
            break
        paintings = r.json().get("Paintings") or []
        if not paintings:
            break
        for p in paintings:
            u = p.get("paintingUrl")
            if u and u not in seen:
                seen.add(u)
                items.append(p)
        page += 1
    return items


def list_all_works():
    items, seen = [], set()
    page = 1
    while page <= 100:
        r = get("https://www.wikiart.org/en/lawren-harris/all-works",
                params={"json": 2, "page": page})
        if r is None:
            break
        paintings = r.json().get("Paintings") or []
        if not paintings:
            break
        for p in paintings:
            u = p.get("paintingUrl")
            if u and u not in seen:
                seen.add(u)
                items.append(p)
        page += 1
    return items


def list_paintings_by_artist():
    r = get("https://www.wikiart.org/en/App/Painting/PaintingsByArtist",
            params={"artistUrl": "lawren-harris", "json": 2})
    if r is None:
        return []
    try:
        data = r.json()
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def list_sitemap_urls():
    found = set()
    idx = get("https://www.wikiart.org/sitemap/sitemap_index.xml", delay=1.1)
    if idx is None:
        return found
    smaps = re.findall(r"<loc>(.*?)</loc>", idx.text)
    paint = [u for u in smaps if "paintings-" in u]
    for url in paint:
        r = get(url, delay=1.2)
        if r is None:
            continue
        for loc in re.findall(
            r"<loc>(https://www\.wikiart\.org/en/lawren-harris/[^<]+)</loc>", r.text
        ):
            # skip non-work listing pages
            slug = loc.rstrip("/").split("/")[-1]
            if slug in ("all-works", "lawren-harris"):
                continue
            found.add(loc)
    return found


def merge_listings():
    """Union of listing endpoints keyed by paintingUrl path."""
    by_url = {}
    text = list_text_list()
    print(f"[wikiart] text-list: {len(text)}")
    for p in text:
        by_url[p["paintingUrl"]] = p

    allw = list_all_works()
    print(f"[wikiart] all-works: {len(allw)}")
    for p in allw:
        u = p["paintingUrl"]
        if u not in by_url:
            by_url[u] = p
            print(f"  + from all-works: {u}")
        else:
            # prefer larger declared width or non-!Large image when useful
            cur = by_url[u]
            if (p.get("width") or 0) > (cur.get("width") or 0):
                by_url[u] = {**cur, **{k: p[k] for k in p if p.get(k) is not None}}

    pba = list_paintings_by_artist()
    print(f"[wikiart] PaintingsByArtist: {len(pba)}")
    # PaintingsByArtist lacks paintingUrl; match by title+year / image basename
    index = {}
    for u, p in by_url.items():
        key = (p.get("title"), str(p.get("year") or p.get("completitionYear") or ""))
        index[key] = u
        bn = Path(urlparse(p.get("image") or "").path).name
        index[("img", bn)] = u

    for p in pba:
        title = p.get("title")
        year = str(p.get("yearAsString") or p.get("completitionYear") or "")
        img = p.get("image") or ""
        bn = Path(urlparse(re.sub(r"!\w+\.\w+$", "", img)).path).name
        u = index.get((title, year)) or index.get(("img", bn))
        if u:
            cur = by_url[u]
            # keep a candidate high-res from !Large listing if bare missing
            if img and (not cur.get("image") or "!Large" in img):
                cur.setdefault("_alt_image", img)
            if (p.get("width") or 0) > (cur.get("width") or 0):
                cur["width"] = p.get("width")
                cur["height"] = p.get("height")
        else:
            print(f"  ! unmatched PaintingsByArtist: {title} ({year})")

    sm = list_sitemap_urls()
    print(f"[wikiart] sitemap lawren-harris work URLs: {len(sm)}")
    listed_full = {"https://www.wikiart.org" + u for u in by_url}
    for loc in sorted(sm - listed_full):
        path = loc.replace("https://www.wikiart.org", "")
        print(f"  + from sitemap (no listing json): {path}")
        by_url[path] = {
            "paintingUrl": path,
            "title": path.rstrip("/").split("/")[-1].replace("-", " "),
            "image": None,
            "year": None,
            "artistName": "Lawren Harris",
        }

    only_listed = listed_full - sm
    if only_listed:
        print(f"[wikiart] listed but not in sitemap: {len(only_listed)}")
    return list(by_url.values())


def artist_notes(creator, artist_name, title):
    notes = []
    blob = f"{creator or ''} {artist_name or ''} {title or ''}".lower()
    # Son is Lawren P. Harris / Lawren Phillips Harris — exclude if attributed
    if re.search(r"lawren\s+p\.?\s+harris|lawren\s+phillips\s+harris", blob):
        notes.append("FLAG: attributed to Lawren P. Harris (son), not Lawren S. Harris")
    if creator and "harris" in creator.lower() and "lawren" not in creator.lower():
        notes.append(f"FLAG: unexpected creator '{creator}'")
    if creator and "lawren" in creator.lower() and "harris" not in creator.lower():
        notes.append(f"FLAG: unexpected creator '{creator}'")
    return "; ".join(notes) if notes else None


def needs_detail_refresh(rec):
    if not rec.get("description") and rec.get("description") != "":
        return True
    # refresh if core fields sparse (Location was previously missed)
    if rec.get("collection") is None and rec.get("_detail_v", 0) < 2:
        return True
    return rec.get("_detail_v", 0) < 2


def scrape_wikiart(recs, want_images):
    print("[wikiart] listing works (multi-endpoint)")
    items = merge_listings()
    print(f"[wikiart] {len(items)} unique works after merge")

    for n, p in enumerate(items, 1):
        slug = p["paintingUrl"].rstrip("/").split("/")[-1]
        key = f"wikiart:{slug}"
        rec = empty_record()
        rec.update(recs.get(key, {}))

        listed_image = p.get("image") or rec.get("image_url")
        alt = p.get("_alt_image")
        # start from listed; upgrade below
        image_url = listed_image or alt

        rec.update({
            "key": key,
            "source": "wikiart",
            "title": p.get("title") or rec.get("title"),
            "year": year_from(str(p.get("year") or p.get("completitionYear") or "")) or rec.get("year"),
            "date_raw": p.get("year") or p.get("yearAsString") or rec.get("date_raw"),
            "page_url": "https://www.wikiart.org" + p["paintingUrl"],
            "confidence": "high",
            "license": rec.get("license")
            or "see page (Harris d. 1970; public domain in Canada)",
        })

        if needs_detail_refresh(rec):
            r = get(rec["page_url"], delay=1.0)
            if r is not None:
                info, pj, creator, desc, og_image = parse_painting_page(r.text)
                rec["medium"] = info.get("media") or info.get("material") or rec.get("medium")
                rec["dimensions"] = info.get("dimensions") or rec.get("dimensions")
                # WikiArt uses Location: for gallery/collection
                rec["collection"] = (
                    info.get("location")
                    or info.get("gallery")
                    or info.get("collection")
                    or rec.get("collection")
                )
                rec["genre"] = info.get("genre") or rec.get("genre")
                rec["style"] = info.get("style") or rec.get("style")
                if info.get("date"):
                    rec["date_raw"] = info["date"]
                    rec["year"] = year_from(info["date"]) or rec["year"]
                if desc:
                    rec["description"] = desc
                elif rec.get("description") is None:
                    rec["description"] = ""
                note = artist_notes(creator, p.get("artistName") or pj.get("artistName"), rec.get("title"))
                if note:
                    rec["notes"] = note
                elif rec.get("notes") is None:
                    rec["notes"] = None
                # prefer og / paintingJson image as additional candidate
                for cand in (og_image, pj.get("image"), alt, listed_image):
                    if cand:
                        image_url = cand
                        break
                rec["_detail_v"] = 2

        # highest-res same-host variant
        if image_url:
            upgraded = best_image_url(image_url)
            prev = rec.get("image_url")
            rec["image_url"] = upgraded
            force = bool(prev and upgraded and upgraded != prev)
            # also force if local missing
            local = rec.get("local_path")
            if local and not (PROJECT / local).exists():
                force = True
            if not local:
                force = True
            # if upgraded URL differs, re-download when larger
            if force or not local:
                # temporarily allow overwrite if URL changed
                if force and local and (PROJECT / local).exists():
                    # download() compares sizes when force=False; set force path
                    old = PROJECT / local
                    r = get(upgraded, delay=0.5)
                    if r is not None and r.content and r.headers.get("content-type", "").startswith("image/"):
                        if len(r.content) > old.stat().st_size:
                            old.write_bytes(r.content)
                            rec["sha256"] = hashlib.sha256(r.content).hexdigest()
                            try:
                                from PIL import Image
                                with Image.open(old) as im:
                                    rec["width"], rec["height"] = im.size
                            except Exception:
                                pass
                            rec["local_path"] = local
                        elif not rec.get("sha256"):
                            download(rec, want_images)
                else:
                    download(rec, want_images, force=False)
            else:
                download(rec, want_images, force=False)

        # drop internal keys before save
        rec.pop("_alt_image", None)
        recs[key] = rec
        if n % 10 == 0 or n == len(items):
            print(f"[wikiart] {n}/{len(items)}")
            save_records(recs)

    save_records(recs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    global ROOT, IMG_DIR, META, CSV_OUT
    if args.out:
        ROOT = args.out.resolve()
        IMG_DIR, META, CSV_OUT = ROOT / "images", ROOT / "metadata.jsonl", ROOT / "metadata.csv"
    ROOT.mkdir(parents=True, exist_ok=True)
    recs = load_records()
    try:
        scrape_wikiart(recs, not args.no_images)
    finally:
        save_records(recs)
    print(
        f"done: {len(recs)} records, "
        f"{sum(1 for r in recs.values() if r.get('local_path'))} images -> {ROOT}"
    )


if __name__ == "__main__":
    main()
