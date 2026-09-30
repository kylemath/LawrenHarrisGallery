#!/usr/bin/env python3
"""Scrape Lawren S. Harris painting images from archives / aggregators / social commons.

Sources (beyond main Commons painting category + Wikidata P170 covered elsewhere):
  openverse   - Openverse API (Flickr etc. CC-licensed photos of paintings)
  gac         - Google Arts & Culture asset search
  commons_x   - Commons extras: structured-data, related cats, wiki lang editions
  archive     - Internet Archive item metadata (PD/open texts only for plates)
  flickr_feed - Flickr public tag feeds (fallback)

Usage:
  source /Users/fulkanjou/LaurenHarris/.venv/bin/activate
  python scrape_archives.py
  python scrape_archives.py --sources openverse gac --no-images
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html as htmlmod
import json
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlparse, quote

import requests
from bs4 import BeautifulSoup

PROJECT = Path(__file__).resolve().parents[3]  # LaurenHarris/
ROOT = Path(__file__).resolve().parent
IMG_DIR = ROOT / "images"
META = ROOT / "metadata.jsonl"
CSV_OUT = ROOT / "metadata.csv"
NOTES = ROOT / "notes" / "probe_notes.md"
PHOTOS_NOTES = ROOT / "notes" / "interesting_photos.md"

UA = "LawrenHarrisScraper/0.1 (archives swarm; personal research; contact: local user)"
session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "en"})

WIKIDATA_ARTIST = "Q3106117"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
IMG_EXT = {
    "image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
    "image/tiff": ".jpg", "image/gif": ".gif",
}

# Titles / patterns that are photos OF Harris, not paintings
PHOTO_OF_ARTIST = re.compile(
    r"\b(sitting|portrait|studio|photograph|photo of|member of the group|"
    r"arts and letters|write up|uncle jack|was here|meets the|"
    r"rafael|grade \d|group of seven drawings|after lawren)\b",
    re.I,
)
NOT_PAINTING = re.compile(
    r"\b(museum london|studio building|gallery exterior|shoppers|"
    r"canary district|humber bay|portlands|peace tower|vogue|"
    r"ralph lauren|cigarette|tom thomson|bradley.?the.?taking)\b",
    re.I,
)
# Known work-title fragments (not just the artist's name)
KNOWN_WORK = re.compile(
    r"(north shore of lake superior|above lake superior|mount(ain)?s?\s+(lefroy|robson)|"
    r"lake and mountains|return from church|light.?house|"
    r"ice house|hurdy.?gurdy|northern lake|riven earth|"
    r"in the ward|snow fantasy|abstraction|painting no\.?\s*\d|"
    r"ellesmere|muskoka|sentinels|country north of lake|"
    r"houses?,?\s*chestnut|mountain sketch|moraine lake|"
    r"lake superior sketch|algoma sketch|northern painting|"
    r"from the north shore|maligne lake|isolation peak|"
    r"pic island|coldwell|bylot|baffin)",
    re.I,
)
PAINTING_WORDS = re.compile(
    r"\b(oil|canvas|painting|paintings|sketch|sketches|watercolou?r|"
    r"drawing|panel|board|gouache|tempera)\b",
    re.I,
)
ARTIST_NAME = re.compile(r"lawren\s+(s\.?\s+)?harris|harris,\s*lawren", re.I)
SON_HINT = re.compile(r"lawren\s+p\.?\s*harris|harris,\s*lawren\s+p", re.I)


def get(url, *, params=None, delay=1.1, retries=4, **kw):
    for i in range(retries):
        try:
            r = session.get(url, params=params, timeout=60, **kw)
            if r.status_code in (429, 503):
                time.sleep(5 * (i + 1))
                continue
            if r.status_code >= 400:
                if i == retries - 1:
                    print(f"  ! HTTP {r.status_code} {url}")
                    return r
                time.sleep(2 * (i + 1))
                continue
            time.sleep(delay)
            return r
        except requests.RequestException as e:
            if i == retries - 1:
                print(f"  ! failed {url}: {e}")
                return None
            time.sleep(2 * (i + 1))
    return None


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:120] or "untitled"


def strip_html(s):
    return BeautifulSoup(s or "", "html.parser").get_text(" ", strip=True)


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
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "notes").mkdir(parents=True, exist_ok=True)
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


def download(rec, want_images=True):
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
    r = get(url, delay=0.8, stream=False)
    if r is None or r.status_code >= 400 or not r.content:
        return
    ctype = r.headers.get("content-type", "")
    if not ctype.startswith("image/") and "octet-stream" not in ctype:
        # GAC googleusercontent sometimes omits image/ but is still image
        if not (r.content[:3] == b"\xff\xd8\xff" or r.content[:8] == b"\x89PNG\r\n\x1a\n"):
            print(f"  ! not an image: {url[:80]} ({ctype})")
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


def looks_like_harris_painting(title, description="", creator="", extra="", *, trust_extra=False):
    """Strict: need artist attribution AND (known work title OR painting medium words).

    `extra` (e.g. Flickr tags / Commons cats) is only used for artist/work hints when
    trust_extra=True; otherwise tags like 'painting' alone cannot qualify a hit.
    """
    core = " ".join([title or "", description or "", creator or ""])
    blob = core + ((" " + (extra or "")) if trust_extra else "")
    if SON_HINT.search(core + " " + (extra or "")):
        return False, "son Lawren P. Harris"
    if PHOTO_OF_ARTIST.search(core) and not (KNOWN_WORK.search(core) and PAINTING_WORDS.search(core)):
        return False, "photo of artist / non-painting"
    if NOT_PAINTING.search(core + " " + (extra or "")):
        return False, "not a Harris painting"
    # Creator field is Lawren Harris (museum/GAC style) — accept
    if re.search(r"^lawren\s+(s\.?\s+)?harris\b", (creator or "").strip(), re.I):
        return True, "creator is Lawren Harris"
    has_artist = bool(ARTIST_NAME.search(core))
    has_work = bool(KNOWN_WORK.search(core))
    has_paint = bool(PAINTING_WORDS.search(core))
    if has_artist and has_work:
        return True, "artist + known work title"
    if has_artist and has_paint:
        return True, "artist + painting medium words"
    if has_work and has_paint and re.search(r"\bharris\b", core, re.I):
        return True, "known work + painting + Harris"
    if trust_extra and re.search(r"Category:Paintings by Lawren Harris", extra or "", re.I):
        return True, "in Paintings by Lawren Harris category"
    # Commons category alone with Harris artist in cats
    if trust_extra and has_artist and re.search(r"Category:.*Paintings", extra or "", re.I):
        return True, "artist + paintings category"
    return False, "insufficient evidence"


# ----------------------------------------------------------------- openverse
def scrape_openverse(recs, want_images, photo_notes):
    print("[openverse] searching")
    results = []
    page = 1
    while page <= 8:
        r = get("https://api.openverse.org/v1/images/", params={
            "q": '"Lawren Harris"', "page_size": 20, "page": page,
        })
        if r is None or r.status_code >= 400:
            break
        try:
            j = r.json()
        except ValueError:
            break
        batch = j.get("results") or []
        if not batch:
            break
        results.extend(batch)
        if page >= int(j.get("page_count") or 1):
            break
        page += 1
    print(f"[openverse] {len(results)} hits")

    kept = 0
    for x in results:
        title = x.get("title") or ""
        creator = x.get("creator") or ""
        desc = x.get("description") or ""
        tags = " ".join(
            (t.get("name") if isinstance(t, dict) else str(t))
            for t in (x.get("tags") or [])
        )
        ok, reason = looks_like_harris_painting(title, desc, creator, tags, trust_extra=False)
        page_url = x.get("foreign_landing_url") or x.get("url")
        img = x.get("url")
        # prefer larger flickr sizes when possible
        if img and "_b.jpg" in img:
            img = img.replace("_b.jpg", "_o.jpg")  # may 404; download falls back handled below
        lic = x.get("license")
        if lic and x.get("license_version"):
            lic = f"CC {lic} {x.get('license_version')}"
        elif lic:
            lic = f"CC {lic}"

        if not ok:
            if re.search(r"harris", title, re.I) and PHOTO_OF_ARTIST.search(title + " " + desc):
                photo_notes.append(f"- {title} | {page_url} | {reason}")
            continue

        oid = x.get("id") or slugify(title)
        key = f"openverse:{oid}"
        conf = "medium"
        if re.search(r"\b(oil|canvas|painting)\b", title + " " + desc, re.I):
            conf = "high"
        if "?" in title or "maybe" in title.lower():
            conf = "low"

        rec = recs.get(key, blank_rec())
        rec.update(blank_rec(
            key=key, source="openverse", title=title, year=year_from(title) or year_from(desc),
            date_raw=None, description=(desc or "")[:500] or None,
            license=lic, page_url=page_url, image_url=x.get("url"),  # use known-good size
            collection=x.get("source"), confidence=conf,
            notes=f"Openverse/{x.get('source')}; {reason}; creator={creator!r}",
        ))
        # try original flickr size; if fail download uses image_url
        if x.get("url"):
            big = x["url"].replace("_b.jpg", "_b.jpg")  # keep _b as reliable
            rec["image_url"] = big
        download(rec, want_images)
        if not rec.get("local_path") and x.get("url"):
            rec["image_url"] = x["url"]
            download(rec, want_images)
        recs[key] = rec
        kept += 1
    print(f"[openverse] kept {kept}")
    save_records(recs)


# --------------------------------------------------------- Google Arts & Culture
def _gac_parse_asset(url, html):
    def meta(prop):
        m = re.search(
            rf'<meta[^>]+property="{re.escape(prop)}"[^>]+content="([^"]+)"', html, re.I
        )
        if not m:
            m = re.search(
                rf'<meta[^>]+content="([^"]+)"[^>]+property="{re.escape(prop)}"', html, re.I
            )
        return htmlmod.unescape(m.group(1)) if m else None

    title = meta("og:title") or ""
    title = re.sub(r"\s*-\s*Google Arts.*$", "", title).strip()
    title = re.sub(r"\s*-\s*Lawren S\. Harris.*$", "", title).strip() or title
    img = meta("og:image")
    # bump size if googleusercontent
    if img and "googleusercontent.com" in img:
        # strip size suffixes; request larger
        img = re.sub(r"=s\d+.*$", "=s2000", img)
        if "=" not in img.split("/")[-1]:
            img = img + "=s2000"

    def field(name):
        # AF_initDataCallback blobs embed [["value"]]
        m = re.search(
            rf'"{re.escape(name)}"\s*,\s*\[\s*\[\s*"([^"]+)"', html
        )
        return htmlmod.unescape(m.group(1)) if m else None

    creator = field("Creator") or field("Painter")
    date_raw = field("Date Created")
    medium = field("Medium")
    dims = field("Physical Dimensions")
    rights = field("Rights")
    form = field("Art Form")
    return {
        "title": title, "image_url": img, "creator": creator, "date_raw": date_raw,
        "medium": medium, "dimensions": dims, "license": rights, "genre": form,
    }


def scrape_gac(recs, want_images):
    print("[gac] searching Google Arts & Culture")
    search_urls = [
        "https://artsandculture.google.com/search?q=Lawren%20S.%20Harris",
        "https://artsandculture.google.com/search/asset?q=Lawren%20Harris",
    ]
    asset_paths = []
    for u in search_urls:
        r = get(u, delay=1.2)
        if r is None or r.status_code >= 400:
            continue
        found = re.findall(r'(/asset/[a-z0-9\-%]+/[A-Za-z0-9_\-]+)', r.text, re.I)
        asset_paths.extend(found)
    # unique, prefer ones that mention harris/lawren in path
    seen = set()
    ordered = []
    for p in asset_paths:
        if p in seen:
            continue
        seen.add(p)
        ordered.append(p)
    harrisish = [p for p in ordered if re.search(r"harris|lawren", p, re.I)]
    # also keep paths we'll verify by creator field
    candidates = harrisish + [p for p in ordered if p not in harrisish][:30]
    print(f"[gac] {len(candidates)} candidate assets ({len(harrisish)} name-matched)")

    kept = 0
    for path in candidates:
        page = "https://artsandculture.google.com" + path
        slug = slugify(path.strip("/").replace("/", "-"))
        key = f"gac:{slug}"
        if key in recs and recs[key].get("local_path") and want_images:
            kept += 1
            continue
        r = get(page, delay=1.15)
        if r is None or r.status_code >= 400:
            continue
        meta = _gac_parse_asset(page, r.text)
        creator = meta.get("creator") or ""
        title = meta.get("title") or path
        ok, reason = looks_like_harris_painting(title, "", creator, path)
        # stricter: creator must be Lawren S. Harris when available
        if creator and not re.search(r"lawren\s+s\.?\s*harris|lawren\s+harris", creator, re.I):
            continue
        if not creator and not re.search(r"harris", path + title, re.I):
            continue
        if SON_HINT.search(creator + title):
            continue
        conf = "high" if re.search(r"lawren\s+s\.?\s*harris", creator, re.I) else "medium"
        if meta.get("license") and "©" in (meta.get("license") or "") and "Public Domain" not in (meta.get("license") or ""):
            conf = "medium"
            note_lic = f"GAC rights={meta.get('license')!r} (Harris d.1970 PD Canada; museum may assert ©)"
        else:
            note_lic = f"GAC rights={meta.get('license')!r}"

        rec = recs.get(key, blank_rec())
        rec.update(blank_rec(
            key=key, source="gac", title=title, year=year_from(meta.get("date_raw")),
            date_raw=meta.get("date_raw"), medium=meta.get("medium"),
            dimensions=meta.get("dimensions"), genre=meta.get("genre"),
            license=meta.get("license"), page_url=page, image_url=meta.get("image_url"),
            confidence=conf, notes=f"{reason}; {note_lic}",
            collection="Google Arts & Culture partner museum",
        ))
        download(rec, want_images)
        recs[key] = rec
        kept += 1
        if kept % 5 == 0:
            save_records(recs)
            print(f"[gac] {kept} saved")
    print(f"[gac] kept {kept}")
    save_records(recs)


# --------------------------------------------------------- Commons extras + wiki
def commons_imageinfo(titles):
    out = []
    for i in range(0, len(titles), 20):
        chunk = titles[i:i + 20]
        print(f"  [commons_x] imageinfo {i+1}-{i+len(chunk)}/{len(titles)}")
        r = get(COMMONS_API, params={
            "action": "query", "format": "json", "titles": "|".join(chunk),
            "prop": "imageinfo|categories",
            "iiprop": "url|extmetadata|size|mime",
            "iiurlwidth": 1920, "cllimit": 30,
        }, delay=0.8, retries=3)
        if r is None or r.status_code >= 400:
            print(f"  ! imageinfo batch failed ({getattr(r,'status_code',None)})")
            continue
        try:
            pages = r.json().get("query", {}).get("pages", {})
        except ValueError:
            print("  ! imageinfo bad json")
            continue
        for pg in pages.values():
            if "imageinfo" in pg:
                cats = [c["title"] for c in pg.get("categories", [])]
                out.append((pg["title"], pg["imageinfo"][0], cats))
    return out


def commons_search(q, limit=50):
    files, cont = [], {}
    while len(files) < limit:
        r = get(COMMONS_API, params={
            "action": "query", "format": "json", "list": "search",
            "srsearch": q, "srnamespace": 6, "srlimit": min(50, limit - len(files)),
            **cont,
        }, delay=0.6)
        if r is None or r.status_code >= 400:
            break
        try:
            j = r.json()
        except ValueError:
            break
        files += [m["title"] for m in j.get("query", {}).get("search", [])]
        if "continue" not in j:
            break
        cont = j["continue"]
    return files


def scrape_commons_x(recs, want_images, photo_notes):
    print("[commons_x] structured data + related searches + wiki editions")
    titles = []
    queries = [
        "haswbstatement:P170=Q3106117",
        "haswbstatement:P180=Q3106117",
        'incategory:"Lawren Harris"',
        '"Lawren Harris" painting OR oil OR canvas -incategory:"Paintings by Lawren Harris"',
        '"Lawren Stewart Harris"',
        'File:"Lawren" Harris Lake Mountains',
        'incategory:"Group of Seven" Harris',
    ]
    for q in queries:
        titles += commons_search(q, limit=40)

    # Wikipedia sitelinks for Harris + Group of Seven
    wiki_titles = set()
    for qid, label in ((WIKIDATA_ARTIST, "harris"), ("Q152140", "group7")):
        r = get(f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json", delay=1.0)
        if r is None or r.status_code >= 400:
            continue
        try:
            sitelinks = r.json()["entities"][qid]["sitelinks"]
        except Exception:
            continue
        # Prefer major language editions first (time-box); still cover many editions
            prefer = ["en", "fr", "de", "es", "it", "pt", "nl", "ja", "zh", "ru",
                      "sv", "pl", "uk", "cs", "fi", "no", "da", "ca", "ro", "hu"]
            sites_sorted = sorted(
                sitelinks.items(),
                key=lambda kv: (prefer.index(kv[0][:-4]) if kv[0][:-4] in prefer else 99, kv[0]),
            )
            for site, info in sites_sorted[:25]:
                if not site.endswith("wiki") or site.startswith("commons"):
                    continue
                lang = site[:-4]
                if lang in ("species", "meta", "simple"):
                    continue
                page_title = info["title"]
                api = f"https://{lang}.wikipedia.org/w/api.php"
                rr = get(api, params={
                    "action": "query", "titles": page_title, "prop": "images",
                    "imlimit": 50, "format": "json",
                }, delay=0.45)
                if rr is None or rr.status_code >= 400:
                    continue
                try:
                    pages = rr.json().get("query", {}).get("pages", {})
                except ValueError:
                    continue
                for p in pages.values():
                    for im in p.get("images", []):
                        t = im["title"]
                        if not t.lower().endswith((".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp")):
                            continue
                        # Only keep wiki images that look Harris-related by filename
                        if re.search(r"harris|lefroy|lake and mountains|north shore|hurdy", t, re.I):
                            wiki_titles.add(t)
            print(f"[commons_x] wiki images via {label}: {len(wiki_titles)} (langs capped, name-filtered)")

    titles = list(dict.fromkeys(titles + list(wiki_titles)))
    print(f"[commons_x] {len(titles)} candidate files")

    PAINT_CAT = "Category:Paintings by Lawren Harris"
    kept = 0
    for title, ii, cats in commons_imageinfo(titles):
        mime = ii.get("mime", "")
        if mime not in IMG_EXT:
            continue
        em = ii.get("extmetadata", {})
        val = lambda k: strip_html(em.get(k, {}).get("value", ""))
        obj = val("ObjectName") or title.replace("File:", "").rsplit(".", 1)[0]
        desc = val("ImageDescription")
        artist = val("Artist")
        license_ = val("LicenseShortName")
        date_raw = val("DateTimeOriginal") or val("DateTime")
        in_main_paint_cat = PAINT_CAT in cats

        # Skip photos of the artist into separate notes
        blob = f"{obj} {desc} {artist} {' '.join(cats)}"
        if PHOTO_OF_ARTIST.search(blob) or re.search(
            r"Category:.*photographs|portrait photographs", " ".join(cats), re.I
        ):
            if "harris" in blob.lower():
                photo_notes.append(f"- commons {title} | {ii.get('descriptionurl')}")
            continue

        ok, reason = looks_like_harris_painting(obj, desc, artist, " ".join(cats), trust_extra=True)
        # Also accept if depicts Harris (P180) or clearly a painting file naming
        if not ok and re.search(r"Lawren Stewart Harris|oil on canvas", title + desc, re.I):
            ok, reason = True, "filename/desc indicates Harris painting"
        if not ok:
            continue
        if SON_HINT.search(blob):
            continue

        # Prefer items NOT already in the main painting category (other worker covers those)
        # but still keep medium/high confidence extras; mark note if overlapping
        key = "commons_x:" + slugify(title.replace("File:", ""))
        conf = "high" if ("Paintings by Lawren Harris" in cats or "oil" in blob.lower()) else "medium"
        if in_main_paint_cat:
            conf = "high"
            note = f"also in {PAINT_CAT}; {reason}"
        else:
            note = f"NOT in main painting category; {reason}; cats={cats[:6]}"

        url = ii["url"] if mime in ("image/jpeg", "image/png", "image/webp") else ii.get("thumburl", ii["url"])
        rec = recs.get(key, blank_rec())
        rec.update(blank_rec(
            key=key, source="commons_x", title=obj, date_raw=date_raw,
            year=year_from(date_raw) or year_from(title) or year_from(desc),
            description=desc[:800] if desc else None, license=license_,
            page_url=ii.get("descriptionurl"), image_url=url,
            width=ii.get("width"), height=ii.get("height"),
            confidence=conf, notes=note,
        ))
        download(rec, want_images)
        recs[key] = rec
        kept += 1
    print(f"[commons_x] kept {kept}")
    save_records(recs)


# ----------------------------------------------------------------- Internet Archive
def scrape_archive(recs, want_images):
    print("[archive] Internet Archive search")
    queries = [
        'title:"Lawren Harris"',
        '"Lawren Harris" paintings 1910-1948',
        '"Lawren Harris" AND mediatype:image',
        'creator:"Lawren Harris" OR creator:"Lawren S. Harris"',
    ]
    docs = []
    seen = set()
    for q in queries:
        r = get("https://archive.org/advancedsearch.php", params={
            "q": q,
            "fl[]": ["identifier", "title", "year", "mediatype", "licenseurl",
                     "creator", "rights", "description", "publicdate"],
            "rows": 50, "page": 1, "output": "json",
        })
        if r is None or r.status_code >= 400:
            continue
        try:
            for d in r.json()["response"]["docs"]:
                ident = d.get("identifier")
                if ident and ident not in seen:
                    seen.add(ident)
                    docs.append(d)
        except Exception:
            continue
    print(f"[archive] {len(docs)} items")

    open_lic = re.compile(r"publicdomain|cc0|creativecommons\.org/(publicdomain|licenses/(by|by-sa))", re.I)
    kept = 0
    for d in docs:
        ident = d["identifier"]
        # fetch full metadata for rights
        r = get(f"https://archive.org/metadata/{ident}", delay=1.1)
        meta = {}
        files = []
        if r is not None and r.status_code < 400:
            try:
                j = r.json()
                meta = j.get("metadata") or {}
                files = j.get("files") or []
            except ValueError:
                pass
        rights = " ".join(filter(None, [
            meta.get("rights") or d.get("rights"),
            meta.get("licenseurl") or d.get("licenseurl"),
            str(meta.get("possible-copyright-status") or ""),
        ]))
        title = meta.get("title") or d.get("title")
        year = year_from(str(meta.get("year") or d.get("year") or ""))
        page_url = f"https://archive.org/details/{ident}"
        is_open = bool(open_lic.search(rights)) or (
            (year and year < 1929 and (meta.get("mediatype") or d.get("mediatype")) == "texts"
             and not re.search(r"©|copyright", rights, re.I))
        )
        # Image mediatype with open license: download largest jpeg
        mt = meta.get("mediatype") or d.get("mediatype")
        image_url = None
        if mt == "image" and is_open:
            jpgs = [f for f in files if str(f.get("name", "")).lower().endswith((".jpg", ".jpeg", ".png"))
                    and not str(f.get("name", "")).startswith("__")]
            jpgs.sort(key=lambda f: int(f.get("size") or 0), reverse=True)
            if jpgs:
                image_url = f"https://archive.org/download/{ident}/{quote(jpgs[0]['name'])}"

        # Do NOT extract plates from AGO-copyrighted 1948 catalog
        note = f"IA {ident}; rights={rights[:200]!r}; open={is_open}"
        if re.search(r"Art Gallery of Ontario|Reproduced with permission", rights, re.I):
            note += "; SKIP plate extract (museum permission copyright)"
            image_url = None
            conf = "low"
        elif image_url:
            conf = "medium"
        else:
            conf = "low"
            note += "; metadata-only (no openly-licensed standalone image)"

        key = f"archive:{ident}"
        rec = recs.get(key, blank_rec())
        rec.update(blank_rec(
            key=key, source="archive", title=title, year=year,
            date_raw=str(meta.get("year") or d.get("year") or "") or None,
            description=(meta.get("description") or d.get("description") or ""),
            license=rights[:300] if rights else None,
            page_url=page_url, image_url=image_url, collection="Internet Archive",
            confidence=conf, notes=note,
            medium="catalog/text scan" if mt == "texts" else mt,
        ))
        if isinstance(rec.get("description"), list):
            rec["description"] = " ".join(rec["description"])[:500]
        elif rec.get("description"):
            rec["description"] = str(rec["description"])[:500]
        download(rec, want_images)
        recs[key] = rec
        kept += 1
    print(f"[archive] recorded {kept}")
    save_records(recs)


# ----------------------------------------------------------------- Flickr feed
def scrape_flickr_feed(recs, want_images, photo_notes):
    print("[flickr_feed] public tag feed")
    r = get("https://www.flickr.com/services/feeds/photos_public.gne", params={
        "tags": "LawrenHarris", "format": "json", "nojsoncallback": 1,
    })
    if r is None or r.status_code >= 400:
        print("[flickr_feed] blocked or empty")
        return
    try:
        j = r.json()
    except ValueError:
        print("[flickr_feed] bad json")
        return
    items = j.get("items") or []
    print(f"[flickr_feed] {len(items)} items")
    kept = 0
    for it in items:
        title = it.get("title") or ""
        desc = strip_html(it.get("description") or "")
        ok, reason = looks_like_harris_painting(title, desc, it.get("author", ""), "")
        media = (it.get("media") or {}).get("m", "")
        # upscale _m to _b
        img = media.replace("_m.jpg", "_b.jpg") if media else None
        page_url = it.get("link")
        if not ok:
            if "harris" in title.lower():
                photo_notes.append(f"- flickr {title} | {page_url}")
            continue
        slug = slugify(title + "-" + (page_url or "")[-12:])
        key = f"flickr_feed:{slug}"
        rec = recs.get(key, blank_rec())
        rec.update(blank_rec(
            key=key, source="flickr_feed", title=title, year=year_from(title),
            description=desc[:400], license="see Flickr page (tag feed)",
            page_url=page_url, image_url=img, confidence="low",
            notes=f"Flickr public feed; {reason}",
        ))
        download(rec, want_images)
        recs[key] = rec
        kept += 1
    print(f"[flickr_feed] kept {kept}")
    save_records(recs)


# ----------------------------------------------------------------- probe stubs
def probe_other_sources():
    """Record blockers for sources we cannot scrape without keys / that block bots."""
    lines = ["# Archives swarm probe notes\n"]
    probes = []

    def try_one(name, url, params=None):
        r = get(url, params=params, delay=1.2, retries=2)
        status = r.status_code if r is not None else "error"
        holds = "unknown"
        snippet = ""
        if r is not None and r.status_code == 200:
            snippet = r.text[:200].replace("\n", " ")
            if re.search(r"lawren\s+harris", r.text, re.I):
                holds = "likely (name mentioned)"
        probes.append((name, status, holds, snippet[:120]))
        lines.append(f"- **{name}**: HTTP {status}; holds={holds}\n")

    try_one("Openverse API", "https://api.openverse.org/v1/images/", {"q": "Lawren Harris", "page_size": 1})
    try_one("Internet Archive", "https://archive.org/advancedsearch.php",
            {"q": "Lawren Harris", "rows": 1, "output": "json"})
    try_one("Europeana API (no key)", "https://api.europeana.eu/record/v2/search.json",
            {"query": "Lawren Harris", "rows": 1})
    try_one("DPLA API (no key)", "https://api.dp.la/v2/items", {"q": "Lawren Harris", "page_size": 1})
    try_one("HathiTrust catalog", "https://catalog.hathitrust.org/Search/Home",
            {"lookfor": "Lawren Harris", "ft": "ft"})
    try_one("LAC collection search", "https://recherche-collection-search.bac-lac.gc.ca/eng/home/result",
            {"q": "Lawren Harris"})
    try_one("Canadiana", "https://www.canadiana.ca/search", {"q": "\"Lawren Harris\""})
    try_one("BAnQ numerique", "https://numerique.banq.qc.ca/", None)
    try_one("GAC search", "https://artsandculture.google.com/search?q=Lawren%20S.%20Harris")
    try_one("Commons P180", COMMONS_API, {
        "action": "query", "list": "search", "srsearch": "haswbstatement:P180=Q3106117",
        "srnamespace": 6, "format": "json",
    })

    lines.append("\n## Blockers\n")
    lines.append("- Europeana / DPLA: API key required (401/403).\n")
    lines.append("- LAC / HathiTrust / some portals: bot 403 or connection reset.\n")
    lines.append("- IA `lawrenharrispain00harr` (1948 AGO catalog): rights © AGO — no plate scrape.\n")
    lines.append("- Pinterest / Artsy / Artnet: skipped (login walls / not plainly scrapable).\n")
    lines.append("- Main Commons `Category:Paintings by Lawren Harris` + Wikidata P170: owned by other swarm workers.\n")
    NOTES.write_text("".join(lines))
    print(f"[probe] wrote {NOTES}")


SOURCES = {
    "openverse": lambda recs, img, photos: scrape_openverse(recs, img, photos),
    "gac": lambda recs, img, photos: scrape_gac(recs, img),
    "commons_x": lambda recs, img, photos: scrape_commons_x(recs, img, photos),
    "archive": lambda recs, img, photos: scrape_archive(recs, img),
    "flickr_feed": lambda recs, img, photos: scrape_flickr_feed(recs, img, photos),
    "probe": lambda recs, img, photos: probe_other_sources(),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", nargs="+", choices=list(SOURCES),
                    default=["probe", "openverse", "gac", "commons_x", "archive", "flickr_feed"])
    ap.add_argument("--no-images", action="store_true")
    args = ap.parse_args()
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "notes").mkdir(parents=True, exist_ok=True)
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    recs = load_records()
    photo_notes = ["# Interesting photos OF Harris / non-painting (excluded from dataset)\n"]
    want = not args.no_images
    for s in args.sources:
        try:
            SOURCES[s](recs, want, photo_notes)
        except Exception as e:
            print(f"[{s}] ERROR: {e}")
            import traceback
            traceback.print_exc()
        finally:
            save_records(recs)
    PHOTOS_NOTES.write_text("\n".join(photo_notes) + "\n")
    n_img = sum(1 for r in recs.values() if r.get("local_path"))
    print(f"done: {len(recs)} records, {n_img} images -> {ROOT}")


if __name__ == "__main__":
    main()
