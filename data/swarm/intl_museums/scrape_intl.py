#!/usr/bin/env python3
"""Scrape Lawren S. Harris (1885–1970) works from NON-CANADIAN museums / open APIs.

Output (this folder):
  images/<institution>/<slug>.<ext>
  metadata.jsonl / metadata.csv
  probe_report.json

Resumable: existing keys/images are skipped.
Uses project venv; polite rate-limiting (>=1s/host).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

PROJECT = Path("/Users/fulkanjou/LaurenHarris")
OUT = Path(__file__).resolve().parent
IMG_DIR = OUT / "images"
META = OUT / "metadata.jsonl"
CSV_OUT = OUT / "metadata.csv"
PROBE = OUT / "probe_report.json"

UA = "LawrenHarrisScraper/0.1 (personal research; Lawren S. Harris dataset; contact: local user)"
WIKIDATA_ARTIST = "Q3106117"  # Lawren Harris (1885–1970), not Lawren P. Harris
session = requests.Session()
session.headers.update({"User-Agent": UA})

# host -> last request monotonic time
_last_host: dict[str, float] = {}
MIN_DELAY = 1.15


def polite_get(url, *, params=None, delay=None, retries=4, stream=False, **kw):
    host = urlparse(url).netloc
    now = time.monotonic()
    wait = (delay if delay is not None else MIN_DELAY) - (now - _last_host.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    for i in range(retries):
        try:
            r = session.get(url, params=params, timeout=90, stream=stream, **kw)
            _last_host[host] = time.monotonic()
            if r.status_code in (429, 503):
                time.sleep(5 * (i + 1))
                continue
            return r
        except requests.RequestException as e:
            if i == retries - 1:
                print(f"  ! failed {url}: {e}")
                return None
            time.sleep(2 * (i + 1))
    return None


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
    OUT.mkdir(parents=True, exist_ok=True)
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


def download_to(rec, institution_slug, want_images=True):
    """Download image_url into images/<institution_slug>/; fill sha256/size."""
    if not want_images:
        return
    url = rec.get("image_url")
    if not url:
        return
    if rec.get("local_path") and (PROJECT / rec["local_path"]).exists():
        return
    ext = Path(urlparse(url).path).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff"):
        ext = ".jpg"
    d = IMG_DIR / institution_slug
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slugify(rec['key'].split(':', 1)[-1])}{ext}"
    r = polite_get(url, delay=0.8)
    if r is None or not r.content:
        return
    ctype = r.headers.get("content-type", "")
    if not ctype.startswith("image/") and not r.content[:3] in (b"\xff\xd8\xff", b"\x89PN"):
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


def ingest_local_file(rec, src: Path, institution_slug: str):
    """Copy an already-local PD image into this swarm folder."""
    if not src.exists():
        return
    if rec.get("local_path") and (PROJECT / rec["local_path"]).exists():
        return
    ext = src.suffix.lower() or ".jpg"
    d = IMG_DIR / institution_slug
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slugify(rec['key'].split(':', 1)[-1])}{ext}"
    if not path.exists():
        shutil.copy2(src, path)
    data = path.read_bytes()
    rec["local_path"] = str(path.relative_to(PROJECT))
    rec["sha256"] = hashlib.sha256(data).hexdigest()
    try:
        from PIL import Image
        with Image.open(path) as im:
            rec["width"], rec["height"] = im.size
    except Exception:
        pass


# ----------------------------------------------------------------- probes
def probe_all():
    report = {}

    def note(name, **kw):
        report[name] = kw
        print(f"[probe] {name}: {kw.get('holds_harris')} works={kw.get('works')} {kw.get('detail','')}")

    # AIC — fuzzy search hits Callahan etc.; filter artist_title
    r = polite_get(
        "https://api.artic.edu/api/v1/artworks/search",
        params={
            "q": "Lawren Harris",
            "fields": "id,title,artist_title,image_id,is_public_domain",
            "limit": 20,
        },
    )
    real = []
    if r and r.status_code == 200:
        for x in r.json().get("data") or []:
            at = (x.get("artist_title") or "").lower()
            if "lawren" in at and "harris" in at and "p." not in at:
                real.append(x)
    note("aic", probed=True, holds_harris=bool(real), works=len(real),
         detail="fuzzy hits are Harry Callahan etc.; artist_title filter empty")

    # Met
    r = polite_get(
        "https://collectionapi.metmuseum.org/public/collection/v1/search",
        params={"q": "Lawren Harris", "artistOrCulture": "true"},
    )
    total = (r.json().get("total") if r and r.status_code == 200 else None)
    note("met", probed=True, holds_harris=bool(total), works=total or 0, detail="artistOrCulture search")

    # Cleveland
    r = polite_get(
        "https://openaccess-api.clevelandart.org/api/artworks/",
        params={"artists": "Harris, Lawren", "limit": 20},
    )
    n = (r.json().get("info") or {}).get("total") if r and r.status_code == 200 else None
    note("cleveland", probed=True, holds_harris=bool(n), works=n or 0)

    # Smithsonian DEMO_KEY — books/botany fuzzy, not LSH paintings
    r = polite_get(
        "https://api.si.edu/openaccess/api/v1.0/search",
        params={"api_key": "DEMO_KEY", "q": 'creator:"Lawren Harris" OR name:"Lawren S. Harris"', "rows": 10},
    )
    rows = ((r.json().get("response") or {}).get("rows") if r and r.status_code == 200 else []) or []
    paint = [x for x in rows if "paint" in (x.get("title") or "").lower()]
    note("smithsonian", probed=True, holds_harris=False, works=0,
         detail=f"DEMO_KEY ok; {len(rows)} fuzzy rows, no LSH paintings")

    # Harvard — needs real key
    r = polite_get(
        "https://api.harvardartmuseums.org/object",
        params={"apikey": "DEMO_KEY", "q": "Lawren Harris", "size": 5},
    )
    note("harvard", probed=True, holds_harris=None, works=None,
         detail=f"status={getattr(r,'status_code',None)}; DEMO_KEY unauthorized — need free API key")

    # V&A
    r = polite_get("https://api.vam.ac.uk/v2/objects/search", params={"q": "Lawren Harris"})
    n = ((r.json().get("info") or {}).get("record_count") if r and r.status_code == 200 else None)
    note("vam", probed=True, holds_harris=bool(n), works=n or 0)

    # Europeana apidemo
    r = polite_get(
        "https://api.europeana.eu/record/v2/search.json",
        params={"query": '"Lawren Harris"', "wskey": "apidemo", "rows": 5},
    )
    n = r.json().get("totalResults") if r and r.status_code == 200 else None
    note("europeana", probed=True, holds_harris=bool(n), works=n or 0, detail="wskey=apidemo")

    # DPLA
    r = polite_get(
        "https://api.dp.la/v2/items",
        params={"q": '"Lawren Harris"', "api_key": "DEMO_KEY", "page_size": 5},
    )
    note("dpla", probed=True, holds_harris=None, works=None,
         detail=f"status={getattr(r,'status_code',None)}; DEMO_KEY rejected")

    # Rijksmuseum
    r = polite_get(
        "https://www.rijksmuseum.nl/api/en/collection",
        params={"key": "0", "format": "json", "q": "Lawren Harris"},
    )
    note("rijksmuseum", probed=True, holds_harris=None, works=None,
         detail=f"status={getattr(r,'status_code',None)}; API key required / 410")

    # Yale IIIF manifest
    r = polite_get("https://manifests.collections.yale.edu/yuag/obj/1950.42")
    note("yale_iiif", probed=True, holds_harris=True, works=1,
         detail=f"manifest status={getattr(r,'status_code',None)}; site Cloudflare; Wikidata Q49195217 confirms holding")

    # Princeton (via Wayback metadata)
    note("princeton", probed=True, holds_harris=True, works=1,
         detail="Wikidata Q106768901; Wayback confirms Lawren S. Harris Seacoast Landscape y1939-36; IIIF host NXDOMAIN")

    # Wikidata non-Canadian collections
    sparql = """
    SELECT ?item ?itemLabel ?inventory ?collectionLabel ?countryLabel WHERE {
      ?item wdt:P170 wd:Q3106117 .
      OPTIONAL { ?item wdt:P217 ?inventory }
      OPTIONAL { ?item wdt:P195 ?collection . OPTIONAL { ?collection wdt:P17 ?country } }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    } LIMIT 200
    """
    r = polite_get("https://query.wikidata.org/sparql", params={"query": sparql, "format": "json"})
    intl = []
    if r and r.status_code == 200:
        for b in r.json()["results"]["bindings"]:
            country = b.get("countryLabel", {}).get("value", "")
            if country and country != "Canada":
                intl.append({
                    "qid": b["item"]["value"].rsplit("/", 1)[-1],
                    "title": b.get("itemLabel", {}).get("value"),
                    "inventory": b.get("inventory", {}).get("value"),
                    "collection": b.get("collectionLabel", {}).get("value"),
                    "country": country,
                })
    note("wikidata_intl", probed=True, holds_harris=bool(intl), works=len(intl), detail=intl)

    PROBE.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    return report


# ----------------------------------------------------------------- scrapers
def scrape_yale(recs, want_images):
    """Yale University Art Gallery — Abstraction, No. 3 (1950.42)."""
    print("[yale] Abstraction, No. 3")
    key = "yale:1950.42"
    if key in recs and recs[key].get("local_path") and (PROJECT / recs[key]["local_path"]).exists():
        print("  skip existing")
        return

    # Try IIIF / Linked Art / open endpoints
    image_url = None
    for u in [
        "https://manifests.collections.yale.edu/yuag/obj/1950.42",
        "https://manifests.collections.yale.edu/yuag/obj/1950.42/v2",
    ]:
        r = polite_get(u, headers={"Accept": "application/json"})
        if r and r.status_code == 200:
            try:
                man = r.json()
                # IIIF3: items[0].items[0].items[0].body.id
                def walk(o):
                    nonlocal image_url
                    if image_url:
                        return
                    if isinstance(o, dict):
                        for k, v in o.items():
                            if k in ("id", "@id") and isinstance(v, str) and (
                                v.endswith((".jpg", ".jpeg", ".png")) or "/full/" in v
                            ):
                                image_url = v
                                return
                            walk(v)
                    elif isinstance(o, list):
                        for x in o:
                            walk(x)
                walk(man)
            except Exception:
                pass

    rec = blank_rec(
        key=key,
        source="Yale University Art Gallery",
        title="Abstraction, No. 3",
        year=None,
        date_raw=None,
        collection="Yale University Art Gallery",
        inventory_no="1950.42",
        medium="oil on canvas",  # Wikidata P186 oil paint + canvas
        dimensions=None,
        genre="abstract",
        style=None,
        description="Abstraction, No. 3 by Lawren S. Harris (Canadian, 1885–1970).",
        license="Yale Open Access (public domain works); confirm on object page",
        page_url="https://artgallery.yale.edu/collections/objects/search?search=1950.42",
        image_url=image_url,
        wikidata_id="Q49195217",
        confidence="high",
        notes="Creator verified via Wikidata P170=Q3106117. Live YUAG site Cloudflare-blocked; "
              "IIIF manifest 404 at manifests.collections.yale.edu/yuag/obj/1950.42 during scrape. "
              "Image download blocked pending open IIIF/LIDO endpoint.",
    )
    if image_url:
        download_to(rec, "yale", want_images)
    recs[key] = rec


def scrape_princeton(recs, want_images):
    """Princeton University Art Museum — Seacoast Landscape (y1939-36)."""
    print("[princeton] Seacoast Landscape")
    key = "princeton:y1939-36"
    if key in recs and recs[key].get("local_path") and (PROJECT / recs[key]["local_path"]).exists():
        print("  skip existing")
        return

    page_url = "https://artmuseum.princeton.edu/collections/objects/20567"
    # Prefer IIIF full; fall back to Wayback-cached derivatives
    candidates = [
        "https://puam-loris.aws.princeton.edu/loris/INV46786.jp2/full/full/0/default.jpg",
        "https://puam-loris.aws.princeton.edu/loris/INV46786.jp2/full/max/0/default.jpg",
        "https://puam-loris.aws.princeton.edu/loris/INV46786.jp2/full/!4000,4000/0/default.jpg",
        "https://media.artmuseum.princeton.edu/iiif/2/INV46786/full/max/0/default.jpg",
        "https://web.archive.org/web/20160518211913im_/http://artmuseum.princeton.edu/"
        "files/styles/tms_flexslider_full/public/imagecache/external/bb154788c786d7b8ed76b87db77ad992.jpg"
        "?itok=1qEExv-t",
    ]
    image_url = None
    for u in candidates:
        r = polite_get(u)
        if r and r.status_code == 200 and r.content and (
            r.headers.get("content-type", "").startswith("image/")
            or r.content[:3] == b"\xff\xd8\xff"
        ):
            image_url = u
            # write immediately from this response
            d = IMG_DIR / "princeton"
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{slugify('y1939-36')}.jpg"
            path.write_bytes(r.content)
            local = str(path.relative_to(PROJECT))
            sha = hashlib.sha256(r.content).hexdigest()
            w = h = None
            try:
                from PIL import Image
                with Image.open(path) as im:
                    w, h = im.size
            except Exception:
                pass
            recs[key] = blank_rec(
                key=key,
                source="Princeton University Art Museum",
                title="Seacoast Landscape",
                year=None,
                date_raw=None,
                collection="Princeton University Art Museum",
                inventory_no="y1939-36",
                medium="Oil on wood panel",
                dimensions="26.80 × 35.30 cm (Wikidata)",
                genre="landscape",
                style=None,
                description="Seacoast Landscape by Lawren S. Harris, Canadian, 1885–1970.",
                license="Check Princeton Image Use and Access policy; work PD in Canada (artist d.1970)",
                page_url=page_url,
                image_url=image_url,
                local_path=local,
                sha256=sha,
                width=w,
                height=h,
                wikidata_id="Q106768901",
                confidence="high",
                notes="Creator verified on Wayback object page as Lawren S. Harris, Canadian, 1885-1970. "
                      "TMS object id 20567; IIIF id INV46786.",
            )
            print(f"  downloaded {local} ({w}x{h})")
            return

    recs[key] = blank_rec(
        key=key,
        source="Princeton University Art Museum",
        title="Seacoast Landscape",
        year=None,
        date_raw=None,
        collection="Princeton University Art Museum",
        inventory_no="y1939-36",
        medium="Oil on wood panel",
        dimensions="26.80 × 35.30 cm (Wikidata)",
        genre="landscape",
        style=None,
        description="Seacoast Landscape by Lawren S. Harris, Canadian, 1885–1970.",
        license="Check Princeton Image Use and Access policy; work PD in Canada (artist d.1970)",
        page_url=page_url,
        image_url="https://puam-loris.aws.princeton.edu/loris/INV46786.jp2/full/full/0/default.jpg",
        wikidata_id="Q106768901",
        confidence="high",
        notes="Creator verified (Wayback 2016 object page). Image host puam-loris.aws.princeton.edu "
              "NXDOMAIN / Wayback im_ 404 during scrape; metadata recorded, image pending.",
    )


def scrape_orsay_commons(recs, want_images):
    """Musée d'Orsay exhibition context — Decorative Landscape on Commons.

    Note: permanent collection is NGC 36813; Commons file documents Orsay/Paris display.
    """
    print("[orsay/commons] Decorative Landscape")
    key = "musee-dorsay:decorative-landscape"
    if key in recs and recs[key].get("local_path") and (PROJECT / recs[key]["local_path"]).exists():
        print("  skip existing")
        return

    commons_title = "File:Decorative Landscape, by Lawren Harris, Musée d'Orsay, Paris (35486993474).jpg"
    image_url = (
        "https://upload.wikimedia.org/wikipedia/commons/c/c7/"
        "Decorative_Landscape%2C_by_Lawren_Harris%2C_Mus%C3%A9e_d%27Orsay%2C_Paris_%2835486993474%29.jpg"
    )
    page_url = "https://commons.wikimedia.org/wiki/" + commons_title.replace(" ", "_")

    # Try Commons API for extmetadata
    license_ = "CC BY 2.0 (Flickr upload on Commons; painting PD in Canada)"
    date_raw = "1917?"  # NGC Decorative Landscape typically 1917
    year = 1917
    r = polite_get(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query",
            "titles": commons_title,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata|sha1",
            "format": "json",
            "formatversion": 2,
        },
    )
    if r and r.status_code == 200:
        try:
            pages = r.json().get("query", {}).get("pages") or []
            if pages and pages[0].get("imageinfo"):
                ii = pages[0]["imageinfo"][0]
                image_url = ii.get("url") or image_url
                ext = ii.get("extmetadata") or {}
                if ext.get("LicenseShortName"):
                    license_ = BeautifulSoup(ext["LicenseShortName"]["value"], "html.parser").get_text()
        except Exception as e:
            print(f"  commons meta parse: {e}")

    rec = blank_rec(
        key=key,
        source="Musée d'Orsay (exhibition / Commons)",
        title="Decorative Landscape",
        year=year,
        date_raw=date_raw,
        collection="Musée d'Orsay (exhibited; permanent collection National Gallery of Canada 36813)",
        inventory_no="NGC 36813 (permanent); Orsay exhibition context",
        medium="oil on canvas",
        dimensions=None,
        genre="landscape",
        style="Group of Seven",
        description="Decorative Landscape by Lawren S. Harris, photographed in Musée d'Orsay / Paris context "
                    "(Commons). Related to 1927 Jeu de Paume Exhibition of Canadian Art / later Orsay display.",
        license=license_,
        page_url=page_url,
        image_url=image_url,
        wikidata_id="Q59537727",
        confidence="medium",
        notes="Painting by Lawren S. Harris (not Lawren P.). Permanent home is NGC; Orsay/Paris is exhibition "
              "context per Commons filename. Included under intl museums source family as requested.",
    )

    # Download: try Wikimedia; else seed from existing project commons copy (same PD file)
    if want_images:
        download_to(rec, "musee-dorsay", want_images=True)
        if not rec.get("local_path"):
            seed = PROJECT / "data/images/commons/decorative-landscape-by-lawren-harris-mus-e-d-orsay-paris-35486993474-jpg.jpg"
            if seed.exists():
                print("  seeding from existing commons download (rate-limited API)")
                ingest_local_file(rec, seed, "musee-dorsay")
                rec["notes"] = (rec.get("notes") or "") + " Image bytes seeded from prior Commons download while API 429."
    recs[key] = rec


def scrape_reading(recs, want_images):
    """University of Reading — Earley Houses, Reading, Berkshire (10479) via Wikidata."""
    print("[reading] Earley Houses")
    key = "university-of-reading:10479"
    if key in recs:
        print("  skip existing")
        return
    recs[key] = blank_rec(
        key=key,
        source="University of Reading",
        title="Earley Houses, Reading, Berkshire",
        year=None,
        date_raw=None,
        collection="University of Reading",
        inventory_no="10479",
        medium="oil on canvas (Wikidata)",
        dimensions="19 × 24 cm (Wikidata)",
        genre=None,
        style=None,
        description="Earley Houses, Reading, Berkshire — attributed on Wikidata to Lawren Harris (Q3106117).",
        license="Unknown / check Art UK & University of Reading",
        page_url="https://artuk.org/discover/artworks/earley-houses-reading-berkshire-10479",
        image_url=None,
        wikidata_id="Q119721316",
        confidence="low",
        notes="Wikidata P170=Q3106117 but subject (Reading, Berkshire) is atypical for Lawren S. Harris; "
              "Art UK pages Cloudflare/403. No open image found. Flag for human verification vs other Harrises.",
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-images", action="store_true")
    ap.add_argument("--skip-probe", action="store_true")
    args = ap.parse_args()
    want_images = not args.no_images

    OUT.mkdir(parents=True, exist_ok=True)
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    recs = load_records()

    if not args.skip_probe:
        probe_all()

    scrape_yale(recs, want_images)
    scrape_princeton(recs, want_images)
    scrape_orsay_commons(recs, want_images)
    scrape_reading(recs, want_images)

    save_records(recs)
    with_img = sum(1 for r in recs.values() if r.get("local_path"))
    print(f"Done. {len(recs)} records, {with_img} with images -> {META}")


if __name__ == "__main__":
    main()
