#!/usr/bin/env python3
"""Scrape Lawren S. Harris (1885–1970) paintings from auction houses / dealers.

Only Lawren Stewart Harris (Group of Seven) — NOT Lawren P. Harris (1910–1994).

Usage:
  source /Users/fulkanjou/LaurenHarris/.venv/bin/activate
  python scrape_auctions.py
  python scrape_auctions.py --no-images
  python scrape_auctions.py --sources heffel,christies,sothebys

Output (this folder):
  images/<house>/<slug>.<ext>
  metadata.jsonl
  metadata.csv
  blockers.json   per-site reachability notes
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]  # LaurenHarris/
IMG_DIR = HERE / "images"
META = HERE / "metadata.jsonl"
CSV_OUT = HERE / "metadata.csv"
BLOCKERS = HERE / "blockers.json"

UA = "LawrenHarrisScraper/0.1 (personal research; contact: local user)"
session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "en-CA,en;q=0.9"})

# Reject son / wrong artist
REJECT_ARTIST = re.compile(
    r"Lawren\s+P\.?\s*Harris|Lawren\s+Phillips\s+Harris|Harris,\s*Lawren\s+P",
    re.I,
)
ACCEPT_ARTIST = re.compile(
    r"Lawren\s+(Stewart\s+)?Harris|Harris,\s*Lawren(\s+Stewart)?|"
    r"Lawren\s+S\.?\s*Harris",
    re.I,
)
# Birth/death window for S. Harris when dates present
DATES_S = re.compile(r"1885\s*[-–]\s*1970")

DELAY = 1.1  # >=1 req/sec per host


def get(url, *, params=None, delay=DELAY, retries=3, timeout=25, **kw):
    for i in range(retries):
        try:
            r = session.get(url, params=params, timeout=timeout, **kw)
            if r.status_code in (429, 503):
                time.sleep(5 * (i + 1))
                continue
            time.sleep(delay)
            return r
        except requests.RequestException as e:
            if i == retries - 1:
                print(f"  ! failed {url}: {e}")
                return None
            time.sleep(1.5 * (i + 1))
    return None


def best_image_url_from_html(html: str) -> str | None:
    """Pick largest publicly linked image URL from page HTML."""
    jpgs = re.findall(r'https?://[^"\'\s>]+\.(?:jpg|jpeg|png|webp)', html or "", re.I)
    if not jpgs:
        return None
    best, best_score = None, -1
    for u in jpgs:
        score = 0
        m = re.search(r"resize/(\d+)x(\d+)", u)
        if m:
            score = int(m.group(1)) * int(m.group(2))
        if "s3.amazonaws.com" in u or "brightspot-migration" in u:
            score += 10
        if score > best_score:
            best_score, best = score, u
    # Prefer raw S3 original when CDN wraps it
    if best and "url=" in best:
        from urllib.parse import parse_qs, urlparse, unquote
        raw = parse_qs(urlparse(best).query).get("url", [None])[0]
        if raw:
            return unquote(raw)
    return best


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:120] or "untitled"


def year_from(s) -> int | None:
    m = re.search(r"\b(1[89]\d\d|19[0-7]\d)\b", s or "")
    return int(m.group(1)) if m else None


def is_lawren_s(text: str) -> bool:
    """Return True if text attributes the work to Lawren S. Harris, not P."""
    if not text:
        return False
    if REJECT_ARTIST.search(text) and not DATES_S.search(text):
        # explicit son name without 1885-1970 → reject
        if re.search(r"Lawren\s+P|Phillips\s+Harris|1910\s*[-–]\s*1994", text, re.I):
            return False
    if DATES_S.search(text):
        return True
    if ACCEPT_ARTIST.search(text) and not re.search(
        r"Lawren\s+P\.?\s*Harris|1910\s*[-–]\s*1994", text, re.I
    ):
        return True
    return False


def load_records():
    recs = {}
    if META.exists():
        for line in META.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                recs[r["key"]] = r
    return recs


def save_records(recs):
    HERE.mkdir(parents=True, exist_ok=True)
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


def prefer_hires(url: str) -> str:
    """Bump Heffel / known CDN paths to largest publicly served size if possible."""
    if not url:
        return url
    # Heffel ArtIndex/Lots 350 → try without size or common alts later at download
    return url


def download(rec, want_images=True):
    url = rec.get("image_url")
    if not url or not want_images:
        return False
    if rec.get("local_path") and (PROJECT / rec["local_path"]).exists():
        return True
    # Try candidate URLs (hires variants)
    candidates = [prefer_hires(url)]
    if "/Images/ArtIndex/350/" in url:
        base = url.split("/Images/ArtIndex/350/")[-1]
        stem = base.rsplit(".", 1)[0]
        for size in ("600", "800", "1000", "1200"):
            candidates.append(f"https://www.heffel.com/Images/ArtIndex/{size}/{base}")
            candidates.append(f"https://www.heffel.com/Images/Lots/{size}/{stem}-01.jpg")
        candidates.append(f"https://www.heffel.com/Images/Lots/350/{stem}-01.jpg")
    if "/Images/Lots/350/" in url:
        base = url.split("/Images/Lots/350/")[-1]
        for size in ("600", "800", "1000", "1200"):
            candidates.append(url.replace("/Lots/350/", f"/Lots/{size}/"))

    best = None
    best_bytes = b""
    for cand in candidates:
        r = get(cand, delay=0.6, stream=False)
        if r is None or r.status_code != 200:
            continue
        ctype = r.headers.get("content-type", "")
        if not ctype.startswith("image/") or not r.content:
            continue
        if len(r.content) > len(best_bytes):
            best, best_bytes = cand, r.content
        # Prefer larger; stop early if we got something decent
        if len(best_bytes) > 200_000:
            break

    if not best or not best_bytes:
        return False

    ext = Path(urlparse(best).path).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"
    house = slugify(rec["source"])
    d = IMG_DIR / house
    d.mkdir(parents=True, exist_ok=True)
    slug = slugify(rec["key"].split(":", 1)[-1])
    path = d / f"{slug}{ext}"
    path.write_bytes(best_bytes)
    rec["image_url"] = best
    rec["local_path"] = str(path.relative_to(PROJECT))
    rec["sha256"] = hashlib.sha256(best_bytes).hexdigest()
    try:
        from PIL import Image
        with Image.open(path) as im:
            rec["width"], rec["height"] = im.size
    except Exception:
        pass
    return True


def blank_rec(**kw):
    base = {
        "key": None, "source": None, "title": None, "year": None, "date_raw": None,
        "collection": None, "inventory_no": None, "medium": None, "dimensions": None,
        "genre": None, "style": None, "description": None, "license": None,
        "page_url": None, "image_url": None, "local_path": None, "sha256": None,
        "width": None, "height": None, "wikidata_id": None, "confidence": "medium",
        "notes": None,
    }
    base.update(kw)
    return base


# ----------------------------------------------------------------- Heffel
def scrape_heffel(recs, want_images, report):
    print("[heffel] artist page + exceptional results")
    artist_url = "https://www.heffel.com/Artist/5B5B58/Lawren%20Stewart%20Harris/"
    found = 0
    downloaded = 0

    r = get(artist_url)
    if r is None or r.status_code != 200:
        report["heffel"] = {
            "reachable": False,
            "lots_found": 0,
            "images_downloaded": 0,
            "blocker": f"artist page HTTP {getattr(r,'status_code',None)}",
        }
        return
    soup = BeautifulSoup(r.text, "html.parser")
    # Alt: "Mountain Forms by Lawren Stewart Harris sold for $11,210,000"
    for im in soup.find_all("img"):
        src = im.get("src") or ""
        alt = im.get("alt") or ""
        if "/ArtIndex/" not in src and "/Lots/" not in src:
            continue
        if not is_lawren_s(alt) and "Lawren Stewart Harris" not in alt:
            continue
        m = re.match(
            r"(.+?)\s+by\s+Lawren\s+Stewart\s+Harris\s+sold\s+for\s+(\$?[\d,]+)",
            alt, re.I,
        )
        title = m.group(1).strip() if m else (alt.split(" by ")[0].strip() or None)
        sold = m.group(2) if m else None
        # inventory from filename e.g. A16F-E14319-001.jpg
        inv = Path(urlparse(src).path).stem
        key = f"heffel:{slugify(inv)}"
        img_url = urljoin("https://www.heffel.com", src)
        notes = {
            "sale_price": sold,
            "image_alt": alt,
            "license_note": "Auction-house photograph; Heffel catalogue imagery — personal research use; check Heffel terms for redistribution",
            "artist_verified": "Lawren Stewart Harris 1885-1970 (artist page)",
        }
        rec = recs.get(key, blank_rec())
        rec.update({
            "key": key, "source": "heffel", "title": title,
            "year": rec.get("year"), "date_raw": rec.get("date_raw"),
            "collection": "private collection / Heffel sale",
            "inventory_no": inv, "page_url": artist_url,
            "image_url": img_url,
            "license": "auction photo — Heffel terms; painting PD in Canada (artist d.1970)",
            "confidence": "high",
            "genre": "painting",
            "notes": json.dumps(notes, ensure_ascii=False),
            "wikidata_id": None,
        })
        recs[key] = rec
        found += 1
        if download(rec, want_images):
            downloaded += 1
        print(f"  + {title} [{inv}]")

    # Exceptional results — structured rows
    r2 = get("https://www.heffel.com/Auction/Exceptional_Results_E")
    if r2 is not None and r2.status_code == 200:
        soup2 = BeautifulSoup(r2.text, "html.parser")
        for row in soup2.select("div.row.divRowsSpace"):
            tile = row.select_one(".artist-template-tile")
            img = row.select_one("img")
            if not tile or not img:
                continue
            text = tile.get_text("\n", strip=True)
            if not is_lawren_s(text):
                continue
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            # Lawren Stewart Harris / 1885 - 1970 Canadian / TITLE / dims / medium / Estimate... / Sale of...
            title = None
            dims = None
            medium = None
            sale = None
            sold_for = None
            estimate = None
            for i, ln in enumerate(lines):
                if re.search(r"Lawren\s+Stewart\s+Harris", ln, re.I):
                    # next non-date line is title
                    for j in range(i + 1, len(lines)):
                        if re.match(r"1885", lines[j]):
                            continue
                        if lines[j].lower().startswith("estimate"):
                            break
                        if re.search(r"\d+\s*x\s*\d+", lines[j], re.I):
                            dims = lines[j].split("\n")[0]
                            # medium may be in same line after br — already flattened
                            continue
                        if lines[j].lower().startswith("oil") or lines[j].lower().startswith("graphite") \
                                or "on canvas" in lines[j].lower() or "on board" in lines[j].lower():
                            medium = lines[j]
                            continue
                        if lines[j].lower().startswith("sale of"):
                            sale = lines[j]
                            continue
                        if title is None and not lines[j].lower().startswith("sold"):
                            title = lines[j]
                        break
                    break
            # re-parse more carefully from HTML
            bold_divs = tile.select("div.font-bold")
            for bd in bold_divs:
                t = bd.get_text(" ", strip=True)
                if t and "Lawren" not in t and not t.startswith("$") and "Harris" not in t:
                    title = t
            loc = tile.select("div.preview-location-text-space")
            for el in loc:
                t = el.get_text(" ", strip=True)
                if re.search(r"\d+\s*x\s*\d+", t):
                    # "60 x 70 in, 152.4 x 177.8 cm oil on canvas"
                    parts = re.split(r"\s{2,}|\n", t)
                    dims_m = re.match(
                        r"(.+?(?:cm|in)[^o]*)\s*(oil .+|graphite .+|acrylic .+|watercolou?r .+)?",
                        t, re.I,
                    )
                    if "cm" in t or " in" in t:
                        # split medium
                        mm = re.search(
                            r"(oil on \w+|graphite on \w+|acrylic on \w+|watercolou?r on \w+|pencil)",
                            t, re.I,
                        )
                        if mm:
                            medium = mm.group(1)
                            dims = t[: mm.start()].strip(" ,;")
                        else:
                            dims = t
                if t.lower().startswith("estimate"):
                    estimate = t
                    sm = re.search(r"Sold for:\s*(\$[\d,]+)", t, re.I)
                    if sm:
                        sold_for = sm.group(1)
            for el in tile.select("div.text"):
                t = el.get_text(" ", strip=True)
                if t.lower().startswith("sale of"):
                    sale = t
            # sold for in span
            span = tile.select_one("span.font-bold")
            if span and span.get_text(strip=True).startswith("$"):
                sold_for = span.get_text(strip=True)

            src = img.get("src") or ""
            inv = Path(urlparse(src).path).stem.replace("-01", "")
            key = f"heffel:{slugify(inv)}"
            img_url = urljoin("https://www.heffel.com", src)
            notes = {
                "estimate": estimate,
                "sale_price": sold_for,
                "sale": sale,
                "license_note": "Heffel auction photograph; personal research; see Heffel catalogue terms",
                "artist_verified": "Lawren Stewart Harris 1885-1970",
                "source_page": "Exceptional_Results_E",
            }
            rec = recs.get(key, blank_rec())
            # Prefer richer fields
            rec.update({
                "key": key, "source": "heffel",
                "title": title or rec.get("title"),
                "collection": sale or rec.get("collection") or "private collection / Heffel sale",
                "inventory_no": inv,
                "medium": medium or rec.get("medium"),
                "dimensions": dims or rec.get("dimensions"),
                "page_url": "https://www.heffel.com/Auction/Exceptional_Results_E",
                "image_url": img_url or rec.get("image_url"),
                "license": "auction photo — Heffel terms; painting PD in Canada (artist d.1970)",
                "confidence": "high",
                "genre": "painting",
                "notes": json.dumps(notes, ensure_ascii=False),
            })
            # merge if we already had artist-page entry — keep better image if Lots version
            if key not in recs or "/Lots/" in src:
                recs[key] = rec
            else:
                # update missing fields
                old = recs[key]
                for f in ("medium", "dimensions", "title"):
                    if rec.get(f) and not old.get(f):
                        old[f] = rec[f]
                old["notes"] = rec["notes"]
                old["collection"] = rec["collection"]
                recs[key] = old
            if key not in [x for x in []]:  # count unique
                pass
            found += 1
            if download(recs[key], want_images):
                downloaded += 1
            print(f"  + exceptional {title} [{inv}]")

    # Top 100 paintings list (may include Harris)
    for top_url in (
        "https://www.heffel.com/ArtIndex/Top100Paintings_E",
        "https://www.heffel.com/ArtIndex/Top100_E",
    ):
        rt = get(top_url)
        if rt is None or rt.status_code != 200 or "Harris" not in rt.text:
            continue
        print(f"[heffel] parsing {top_url}")
        soup_t = BeautifulSoup(rt.text, "html.parser")
        for row in soup_t.select("div.row, tr, .artist-template-tile"):
            t = row.get_text(" ", strip=True)
            if not is_lawren_s(t):
                continue
            img = row.find("img")
            if not img:
                parent = row.find_parent("div", class_=re.compile(r"row"))
                if parent:
                    img = parent.find("img")
                    row = parent
            if not img:
                continue
            src = img.get("src") or ""
            if not src:
                continue
            inv = Path(urlparse(src).path).stem.replace("-01", "")
            key = f"heffel:{slugify(inv)}"
            if key in recs and recs[key].get("local_path"):
                continue
            title_m = re.search(
                r"Lawren\s+Stewart\s+Harris.*?1970\s*(?:Canadian)?\s*(.+?)(?:\d+\s*x\s*\d+|Estimate|Sold)",
                t, re.I | re.S,
            )
            title = title_m.group(1).strip() if title_m else img.get("alt")
            rec = blank_rec(
                key=key, source="heffel", title=title,
                collection="Heffel Top 100 / private collection",
                inventory_no=inv,
                page_url=top_url,
                image_url=urljoin("https://www.heffel.com", src),
                license="auction photo — Heffel terms; painting PD in Canada (artist d.1970)",
                confidence="high",
                genre="painting",
                notes=json.dumps({"source_page": top_url, "artist_verified": "Lawren Stewart Harris 1885-1970"}),
            )
            recs[key] = rec
            found += 1
            if download(rec, want_images):
                downloaded += 1

    n_keys = sum(1 for k in recs if k.startswith("heffel:"))
    n_img = sum(
        1 for k, v in recs.items()
        if k.startswith("heffel:") and v.get("local_path") and (PROJECT / v["local_path"]).exists()
    )
    report["heffel"] = {
        "reachable": True,
        "lots_found": n_keys,
        "images_downloaded": n_img,
        "blocker": None,
        "notes": "robots.txt 404; scraped public artist + exceptional-results pages",
    }
    print(f"[heffel] done keys={n_keys} images={n_img}")


# ----------------------------------------------------------------- Christie's
CHRISTIES_LOTS = [
    "https://www.christies.com/en/lot/lot-6397193",  # Mountain Sketch LXIII
    "https://www.christies.com/en/lot/lot-6397194",  # From Berg Lake, Morning
]


def scrape_christies(recs, want_images, report):
    print("[christies] known Lawren Stewart Harris lots")
    found = 0
    blocked = False
    for url in CHRISTIES_LOTS:
        r = get(url, timeout=15, retries=1)
        if r is None:
            blocked = True
            continue
        if r.status_code in (403, 401) or "captcha" in r.text.lower()[:3000] \
                or "Just a moment" in r.text[:2000]:
            blocked = True
            print(f"  ! blocked {url} ({r.status_code})")
            continue
        if r.status_code != 200:
            print(f"  ! HTTP {r.status_code} {url}")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(" ", strip=True)
        if not is_lawren_s(text):
            print(f"  ! attribution fail {url}")
            continue
        # title
        h1 = soup.find("h1")
        title = h1.get_text(" ", strip=True) if h1 else None
        if title and "LAWREN" in title.upper():
            # often "LAWREN STEWART HARRIS (1885-1970), Title"
            m = re.search(r"\)[,\s]+(.+)$", title)
            if m:
                title = m.group(1).strip()
        og = soup.find("meta", property="og:image")
        img_url = og["content"] if og and og.get("content") else None
        if not img_url or not str(img_url).strip():
            img_url = best_image_url_from_html(r.text)
        lot_id = url.rstrip("/").split("/")[-1]
        # dimensions / medium from text
        medium = None
        dims = None
        mm = re.search(r"(oil on \w+(?: board| canvas| panel)?)", text, re.I)
        if mm:
            medium = mm.group(1)
        dm = re.search(r"(\d[\d\s./]*\s*x\s*\d[\d\s./]*\s*in\.?\s*\([^)]+\))", text, re.I)
        if dm:
            dims = dm.group(1)
        year = None
        ym = re.search(r"(?:Painted|circa)\s*(?:circa\s*)?(\d{4})", text, re.I)
        if ym:
            year = int(ym.group(1))
        notes = {
            "artist_verified": "LAWREN STEWART HARRIS (1885-1970)",
            "license_note": "Christie's lot photograph; personal research; see Christie's terms",
            "page_title": (soup.title.string if soup.title else None),
        }
        key = f"christies:{lot_id}"
        rec = blank_rec(
            key=key, source="christies", title=title, year=year,
            date_raw=str(year) if year else None,
            collection="private collection / Christie's sale",
            inventory_no=lot_id, medium=medium, dimensions=dims,
            page_url=url, image_url=img_url,
            license="auction photo — Christie's terms; painting PD in Canada (artist d.1970)",
            confidence="high", genre="painting",
            notes=json.dumps(notes, ensure_ascii=False),
        )
        recs[key] = rec
        found += 1
        if download(rec, want_images):
            print(f"  + downloaded {title}")
        else:
            print(f"  ~ meta only {title} img={bool(img_url)}")

    n_keys = sum(1 for k in recs if k.startswith("christies:"))
    n_img = sum(
        1 for k, v in recs.items()
        if k.startswith("christies:") and v.get("local_path") and (PROJECT / v["local_path"]).exists()
    )
    report["christies"] = {
        "reachable": not blocked or n_keys > 0,
        "lots_found": n_keys,
        "images_downloaded": n_img,
        "blocker": "captcha/403 on some lots" if blocked else None,
    }


# ----------------------------------------------------------------- Sotheby's
SOTHEBYS_LOTS = [
    "https://www.sothebys.com/en/auctions/ecatalogue/2012/canadian-art-t00140/lot.93.html",
    "https://www.sothebys.com/en/auctions/ecatalogue/2009/important-canadian-art-t00135/lot.35.html",
    "https://www.sothebys.com/en/auctions/ecatalogue/2012/null-t00141/lot.82.html",
]


def scrape_sothebys(recs, want_images, report):
    print("[sothebys] known Lawren Stewart Harris lots")
    blocked = False
    for url in SOTHEBYS_LOTS:
        r = get(url)
        if r is None:
            blocked = True
            continue
        if r.status_code in (403, 401, 503) or "captcha" in r.text.lower()[:4000] \
                or "Access Denied" in r.text[:2000] or "Just a moment" in r.text[:2000]:
            blocked = True
            print(f"  ! blocked {url} ({r.status_code})")
            continue
        if r.status_code != 200:
            print(f"  ! HTTP {r.status_code} {url}")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text("\n", strip=True)
        if not is_lawren_s(text):
            print(f"  ! attribution fail {url}")
            continue
        # Title often in description list
        title = None
        for li in soup.select("li"):
            t = li.get_text(" ", strip=True)
            # second item often work title
            if re.search(r"Mountain|Lake|Arctic|Sketch|House|Island|Algoma", t) and len(t) < 120:
                if "Harris" not in t:
                    title = t
                    break
        if not title:
            # fallback: look for italic / h1 siblings
            for sel in ("h1", "h2", ".lot-name", "[data-test=lotTitle]"):
                el = soup.select_one(sel)
                if el:
                    title = el.get_text(" ", strip=True)
                    break
        og = soup.find("meta", property="og:image")
        img_url = og["content"] if og and og.get("content") else None
        if not img_url or not str(img_url).strip():
            img_url = best_image_url_from_html(r.text)
        lot_m = re.search(r"lot\.(\d+)", url)
        lot_id = f"lot-{lot_m.group(1)}" if lot_m else slugify(urlparse(url).path)
        medium = None
        dims = None
        mm = re.search(r"(oil on \w+)", text, re.I)
        if mm:
            medium = mm.group(1)
        dm = re.search(r"(\d[\d.]*\s*by\s*\d[\d.]*\s*cm)", text, re.I)
        if dm:
            dims = dm.group(1)
        # Prefer catalogue title from description list items
        desc_items = []
        for li in soup.select("li"):
            t = li.get_text(" ", strip=True)
            if t and len(t) < 200:
                desc_items.append(t)
        # Typical: artist, title, signed..., medium, dims
        for t in desc_items:
            if re.search(r"Mountain|Lake|Arctic|Sketch|House|Island|Algoma|Superior|Ward|Pine", t) \
                    and "Harris" not in t and "Estimate" not in t and not t.lower().startswith("oil"):
                title = t
                break
        notes = {
            "artist_verified": "Lawren Stewart Harris 1885-1970",
            "license_note": "Sotheby's lot photograph; personal research; see Sotheby's terms",
            "page_url": url,
        }
        key = f"sothebys:{lot_id}"
        rec = blank_rec(
            key=key, source="sothebys", title=title,
            collection="private collection / Sotheby's sale",
            inventory_no=lot_id, medium=medium, dimensions=dims,
            page_url=url, image_url=img_url,
            license="auction photo — Sotheby's terms; painting PD in Canada (artist d.1970)",
            confidence="high" if title else "medium",
            genre="painting",
            notes=json.dumps(notes, ensure_ascii=False),
        )
        recs[key] = rec
        ok = download(rec, want_images)
        print(f"  {'+' if ok else '~'} {title} img={bool(img_url)}")

    n_keys = sum(1 for k in recs if k.startswith("sothebys:"))
    n_img = sum(
        1 for k, v in recs.items()
        if k.startswith("sothebys:") and v.get("local_path") and (PROJECT / v["local_path"]).exists()
    )
    report["sothebys"] = {
        "reachable": n_keys > 0 and not blocked,
        "lots_found": n_keys,
        "images_downloaded": n_img,
        "blocker": "bot protection / redirect" if blocked else None,
    }


# ----------------------------------------------------------------- Cowley / others (record blockers)
def probe_blocked_sites(report):
    probes = {
        "cowley_abbott": "https://cowleyabbott.ca/artwork/AW46150",
        "mayberry": "https://www.mayberryfineart.com/artists/lawren-harris",
        "waddingtons": "https://www.waddingtons.ca/?s=Lawren+Harris",
        "bonhams": "https://www.bonhams.com/search/?q=%22Lawren+Stewart+Harris%22",
        "invaluable": "https://www.invaluable.com/artist/harris-lawren-stewart-abc/",
        "mutualart": "https://www.mutualart.com/Artist/Lawren-Harris/...",
    }
    # fixed mutualart
    probes["mutualart"] = "https://www.mutualart.com/Artist/Lawren-Stewart-Harris/8C0B5F6E0E0E0E0E"

    for name, url in probes.items():
        if name in report:
            continue
        r = get(url, delay=1.2)
        if r is None:
            report[name] = {
                "reachable": False, "lots_found": 0, "images_downloaded": 0,
                "blocker": "request failed / timeout",
            }
            continue
        body = r.text[:4000]
        cf = ("Just a moment" in body or "cf-browser-verification" in body
              or "Attention Required" in body or r.status_code == 403)
        if cf:
            report[name] = {
                "reachable": False, "lots_found": 0, "images_downloaded": 0,
                "blocker": f"Cloudflare/bot protection HTTP {r.status_code} — skipped (no bypass)",
                "page_url": url,
            }
            print(f"[{name}] blocked HTTP {r.status_code}")
            continue
        # light parse for waddingtons / bonhams
        soup = BeautifulSoup(r.text, "html.parser")
        harris_links = []
        for a in soup.find_all("a", href=True):
            t = a.get_text(" ", strip=True)
            href = a["href"]
            if is_lawren_s(t) or (is_lawren_s(href) and "harris" in href.lower()):
                harris_links.append((urljoin(url, href), t[:80]))
        report[name] = {
            "reachable": True,
            "lots_found": len(harris_links),
            "images_downloaded": 0,
            "blocker": None if harris_links else "reachable but no clear Lawren S. Harris lot links in search HTML (JS-rendered?)",
            "sample_links": harris_links[:5],
            "page_url": url,
        }
        print(f"[{name}] reachable links={len(harris_links)}")


def scrape_cowley_seed_list(recs, want_images, report):
    """Try known Cowley Abbott artwork URLs; skip if Cloudflare."""
    urls = [
        "https://www.cowleyabbott.ca/artwork/AW46150",
        "https://cowleyabbott.ca/artwork/AW45383",
        "https://cowleyabbott.ca/artwork/AW49929",
        "https://www.cowleyabbott.ca/artwork/AW43109",
    ]
    got = 0
    blocked = False
    for url in urls:
        r = get(url)
        if r is None or r.status_code == 403 or "Just a moment" in (r.text[:2000] if r else ""):
            blocked = True
            print(f"  ! cowley blocked {url}")
            continue
        if r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        text = soup.get_text(" ", strip=True)
        if not is_lawren_s(text):
            continue
        h1 = soup.find("h1")
        title = h1.get_text(" ", strip=True) if h1 else None
        og = soup.find("meta", property="og:image")
        img_url = og["content"] if og and og.get("content") else None
        aw = url.rstrip("/").split("/")[-1]
        key = f"cowley_abbott:{aw.lower()}"
        rec = blank_rec(
            key=key, source="cowley_abbott", title=title,
            collection="private collection / Cowley Abbott",
            inventory_no=aw, page_url=url, image_url=img_url,
            license="auction photo — Cowley Abbott terms; painting PD in Canada (artist d.1970)",
            confidence="high", genre="painting",
            notes=json.dumps({
                "artist_verified": "Lawren Stewart Harris",
                "license_note": "Cowley Abbott lot photo; personal research",
            }),
        )
        # medium/dims
        mm = re.search(r"(oil on \w+|pencil|graphite)", text, re.I)
        if mm:
            rec["medium"] = mm.group(1)
        dm = re.search(r"(\d[\d.]*\s*[×x]\s*\d[\d.]*\s*in)", text, re.I)
        if dm:
            rec["dimensions"] = dm.group(1)
        recs[key] = rec
        download(rec, want_images)
        got += 1
        print(f"  + cowley {title}")

    n_keys = sum(1 for k in recs if k.startswith("cowley_abbott:"))
    n_img = sum(
        1 for k, v in recs.items()
        if k.startswith("cowley_abbott:") and v.get("local_path") and (PROJECT / v["local_path"]).exists()
    )
    report["cowley_abbott"] = {
        "reachable": not blocked,
        "lots_found": n_keys,
        "images_downloaded": n_img,
        "blocker": "Cloudflare challenge — skipped (no bypass)" if blocked and n_keys == 0 else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--sources", default="heffel,christies,sothebys,cowley,probes",
                    help="comma list: heffel,christies,sothebys,cowley,probes")
    args = ap.parse_args()
    want_images = not args.no_images
    sources = {s.strip() for s in args.sources.split(",") if s.strip()}

    recs = load_records()
    report = {}
    if BLOCKERS.exists():
        try:
            report = json.loads(BLOCKERS.read_text())
        except json.JSONDecodeError:
            report = {}

    if "heffel" in sources:
        scrape_heffel(recs, want_images, report)
        save_records(recs)
    if "christies" in sources:
        scrape_christies(recs, want_images, report)
        save_records(recs)
    if "sothebys" in sources:
        scrape_sothebys(recs, want_images, report)
        save_records(recs)
    if "cowley" in sources:
        scrape_cowley_seed_list(recs, want_images, report)
        save_records(recs)
    if "probes" in sources:
        probe_blocked_sites(report)

    save_records(recs)
    BLOCKERS.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nWrote {META} ({len(recs)} records), {CSV_OUT}, {BLOCKERS}")


if __name__ == "__main__":
    main()
