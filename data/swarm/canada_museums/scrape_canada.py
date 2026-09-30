#!/usr/bin/env python3
"""Scrape Lawren S. Harris paintings from Canadian museum online collections.

Primary sources (HTTP-accessible without Cloudflare):
  - McMichael Canadian Art Collection eMuseum (collections.mcmichael.com)
  - Art Gallery of Greater Victoria eMuseum (aggv.ca/emuseum)
  - Wikidata structured works held by Canadian institutions (+ Commons images)
  - University of Calgary / Glenbow digital collections (when hits found)
  - Art Museum at U of T / Canadian Encyclopedia (opportunistic)

National Gallery of Canada / AGO / WAG / LAC often behind Cloudflare — see
scrape_ngc_browser.py notes / REPORT blockers. Resumable via metadata.jsonl.

Usage:
  source /Users/fulkanjou/LaurenHarris/.venv/bin/activate
  python scrape_canada.py
  python scrape_canada.py --no-images
  python scrape_canada.py --sources mcmichael,aggv,wikidata
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup

PROJECT = Path(__file__).resolve().parents[3]  # LaurenHarris/
OUT = Path(__file__).resolve().parent
IMG_DIR = OUT / "images"
META = OUT / "metadata.jsonl"
CSV_OUT = OUT / "metadata.csv"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
session = requests.Session()
session.headers.update({
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-CA,en;q=0.9",
})

IMG_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/tiff": ".jpg",
}

# Exclude son's work / photos of Harris when title strongly suggests it
PHOTO_TITLE_RE = re.compile(
    r"\b(portrait of|photograph|photo of|after a photograph|"
    r"harris sketching|harris and )\b",
    re.I,
)
SON_RE = re.compile(r"Lawren\s+P\.?\s+Harris", re.I)


# ----------------------------------------------------------------- helpers
def get(url, *, params=None, delay=1.1, retries=4, stream=False, **kw):
    for i in range(retries):
        try:
            r = session.get(url, params=params, timeout=60, stream=stream, **kw)
            if r.status_code in (429, 503):
                time.sleep(5 * (i + 1))
                continue
            if r.status_code == 403:
                print(f"  ! 403 {url}")
                return None
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
    rec = {
        "key": None, "source": None, "title": None, "year": None, "date_raw": None,
        "collection": None, "inventory_no": None, "medium": None, "dimensions": None,
        "genre": None, "style": None, "description": None, "license": None,
        "page_url": None, "image_url": None, "local_path": None, "sha256": None,
        "width": None, "height": None, "wikidata_id": None, "confidence": "medium",
        "notes": None,
    }
    rec.update(kw)
    return rec


def download(rec, want_images=True, institution_dir=None):
    url = rec.get("image_url")
    if not url or not want_images:
        return
    if rec.get("local_path") and (PROJECT / rec["local_path"]).exists():
        return
    ext = Path(urlparse(url).path).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"
    folder = institution_dir or slugify(rec["source"])
    d = IMG_DIR / folder
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{slugify(rec['key'].split(':', 1)[-1])}{ext}"
    r = get(url, delay=1.0)
    if r is None or not r.content:
        return
    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    if not ctype.startswith("image/"):
        print(f"  ! not an image: {url} ({ctype})")
        return
    if ctype in IMG_EXT:
        path = path.with_suffix(IMG_EXT[ctype])
    path.write_bytes(r.content)
    rec["local_path"] = str(path.relative_to(PROJECT))
    rec["sha256"] = hashlib.sha256(r.content).hexdigest()
    try:
        from PIL import Image
        with Image.open(path) as im:
            rec["width"], rec["height"] = im.size
    except Exception:
        pass


def emuseum_field(soup, class_substr):
    for el in soup.select(".detailField"):
        classes = " ".join(el.get("class") or [])
        if class_substr in classes:
            # value often in .detailFieldValue or last text after label
            val = el.select_one(".detailFieldValue")
            if val:
                return val.get_text(" ", strip=True)
            texts = list(el.stripped_strings)
            if len(texts) >= 2:
                return " ".join(texts[1:])
            return el.get_text(" ", strip=True)
    return None


def emuseum_best_image(soup, base):
    candidates = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if "/internal/media/dispatcher/" in src and "/logo" not in src:
            # prefer /full
            full = re.sub(r"/(preview|thumbnail|medium|large)$", "/full", src)
            if not full.endswith("/full"):
                if re.search(r"/dispatcher/\d+$", full):
                    full = full + "/full"
                elif "/dispatcher/" in full and not full.rstrip("/").endswith(
                    ("full", "preview", "thumbnail")
                ):
                    full = full.rstrip("/") + "/full"
            candidates.append(urljoin(base, full))
    # unique, prefer ones ending in /full
    seen = []
    for c in candidates:
        if c not in seen:
            seen.append(c)
    fulls = [c for c in seen if c.rstrip("/").endswith("/full")]
    return (fulls or seen or [None])[0]


def emuseum_list_objects(list_url, base, max_pages=30):
    """Paginate an eMuseum objects listing; return {id: {id,slug,title,path}}."""
    items = {}
    url = list_url
    seen_page_urls = set()
    for page in range(max_pages):
        # normalize page url for loop detection (drop jsessionid)
        norm = re.sub(r";jsessionid=[^/?#]+", "", url)
        norm = re.sub(r"[?&]sid=[^&]+", "", norm)
        if norm in seen_page_urls:
            print("  pagination loop detected, stopping")
            break
        seen_page_urls.add(norm)
        print(f"  list page {page + 1}: {url[:110]}")
        r = get(url, delay=1.1)
        if r is None:
            break
        soup = BeautifulSoup(r.content, "html.parser")
        before = len(items)
        for a in soup.find_all("a", href=True):
            m = re.search(r"/objects/(\d+)/([^?;]+)", a["href"])
            if not m:
                continue
            oid, slug = m.group(1), m.group(2)
            title = a.get_text(" ", strip=True)
            if not title or title.startswith("Image Not"):
                continue
            path = a["href"].split(";")[0].split("?")[0]
            path = re.sub(r";jsessionid=[^/]*", "", path)
            if oid not in items:
                items[oid] = {"id": oid, "slug": slug, "title": title, "path": path}
        added = len(items) - before
        nxt = None
        for a in soup.find_all("a", href=True):
            t = a.get_text(strip=True)
            if t in ("Next", "Next Page"):
                nxt = urljoin(base, a["href"])
                break
        print(f"  +{added} new (total {len(items)}), next={bool(nxt)}")
        if not nxt or added == 0:
            break
        url = nxt
    return items


ART_MEDIUM_RE = re.compile(
    r"\b(oil|graphite|charcoal|pencil|ink|watercolou?r|gouache|"
    r"tempera|pastel|crayon|canvas|panel|board|paper|drawing|"
    r"sketch|acrylic|mixed media|lithograph|etching|print)\b",
    re.I,
)
NON_ART_RE = re.compile(
    r"\b(paintbox|drawing box|easel|artist'?s tools|palette knife|"
    r"copy print|copy negative|photograph|gelatin|silver print|"
    r"fonds|archive)\b",
    re.I,
)
ARTIST_ROLE_RE = re.compile(
    r"(?:^|\b)(?:Artist|Maker|Primary Maker|Painter|Draughtsman|Draftsman)\b",
    re.I,
)


def parse_emuseum_object(page_url, soup, *, source_key, source_name, collection_name):
    title = emuseum_field(soup, "titleField") or (
        soup.title.string.split("–")[0].strip() if soup.title and soup.title.string else None
    )
    # Full people field text (may include role labels)
    people_el = None
    for el in soup.select(".detailField.peopleField, .detailField"):
        classes = " ".join(el.get("class") or [])
        if "peopleField" in classes:
            people_el = el
            break
    artist = emuseum_field(soup, "peopleField") or ""
    people_text = people_el.get_text(" ", strip=True) if people_el else artist
    date_raw = emuseum_field(soup, "displayDateField") or emuseum_field(soup, "dateField")
    medium = emuseum_field(soup, "mediumField")
    support = emuseum_field(soup, "paperSupportField")
    if support and medium:
        medium = f"{medium} on {support}" if " on " not in medium.lower() else medium
    dims = emuseum_field(soup, "dimensionsField")
    inv = emuseum_field(soup, "invnoField")
    desc = emuseum_field(soup, "descriptionField")
    credit = emuseum_field(soup, "creditlineField")
    classification = emuseum_field(soup, "classificationField") or ""
    department = emuseum_field(soup, "departmentField") or ""

    notes = []
    confidence = "high"

    # Hard skip: son's work
    if SON_RE.search(people_text) and "Lawren S" not in people_text:
        return None

    # Require Artist/Maker role for Lawren S. Harris (not Owner / Sitter / Subject)
    is_artist = bool(
        re.search(
            r"(?:Artist|Maker|Primary Maker|Painter|Draughtsman|Draftsman)\s*[:|]?\s*"
            r"Lawren\s+S\.?\s*Harris",
            people_text,
            re.I,
        )
    ) or (
        ARTIST_ROLE_RE.search(people_text)
        and re.search(r"Lawren\s+S\.?\s*Harris", people_text, re.I)
        and not re.search(r"\b(Owner|Sitter|Subject|Photographer)\b.*Lawren\s+S", people_text, re.I)
    )
    # eMuseum often shows: "Artist Lawren S. Harris 1885 - 1970"
    if re.search(r"^Artist\s+Lawren\s+S\.?\s*Harris", people_text, re.I):
        is_artist = True
    if re.search(r"\b(Owner|Sitter|Subject)\b", people_text, re.I) and not re.search(
        r"\bArtist\b", people_text, re.I
    ):
        is_artist = False

    if not is_artist:
        # Skip non-art attributions entirely (tools owned by Harris, photos of him, etc.)
        return None

    if SON_RE.search(people_text):
        return None

    if department and re.search(r"archive", department, re.I):
        return None
    if classification and re.search(r"\b(fonds|archive|photograph)\b", classification, re.I):
        return None
    if NON_ART_RE.search(title or "") or NON_ART_RE.search(medium or ""):
        return None
    if medium and not ART_MEDIUM_RE.search(medium):
        # keep but flag
        confidence = "low"
        notes.append(f"unusual medium for painting/drawing: {medium}")
    if PHOTO_TITLE_RE.search(title or ""):
        confidence = "low"
        notes.append("title may be photo/portrait of Harris rather than a work by him")

    img = emuseum_best_image(soup, page_url)
    oid = re.search(r"/objects/(\d+)/", page_url)
    slug = slugify(f"{oid.group(1) if oid else ''}-{title}")
    key = f"{source_key}:{slug}"

    if credit:
        notes.append(f"credit: {credit}")

    return blank_rec(
        key=key,
        source=source_name,
        title=title,
        year=year_from(date_raw),
        date_raw=date_raw,
        collection=collection_name,
        inventory_no=inv,
        medium=medium,
        dimensions=dims,
        genre=None,
        style=None,
        description=desc,
        license="Check institution terms; Harris died 1970 — PD in Canada for works",
        page_url=page_url.split(";")[0].split("?")[0],
        image_url=img,
        confidence=confidence,
        notes="; ".join(notes) if notes else None,
    )


# ----------------------------------------------------------------- McMichael
def scrape_mcmichael(recs, want_images):
    print("[mcmichael] Lawren S. Harris objects")
    base = "https://collections.mcmichael.com"
    # Prefer primaryMaker filter (129) over people related (131)
    list_url = (
        f"{base}/search/Lawren%20Harris/objects"
        f"?filter=primaryMaker%3ALawren%20S.%20Harris"
    )
    items = emuseum_list_objects(list_url, base)
    print(f"  listed {len(items)} objects")
    for i, it in enumerate(items.values(), 1):
        path = it["path"]
        if not path.startswith("http"):
            path = urljoin(base, path)
        key_guess = f"mcmichael:{slugify(it['id'] + '-' + it['title'])}"
        if key_guess in recs and recs[key_guess].get("local_path"):
            # still allow metadata refresh skip if complete
            if (PROJECT / recs[key_guess]["local_path"]).exists() or not want_images:
                print(f"  [{i}/{len(items)}] skip {it['title'][:50]}")
                continue
            # if have record without image, retry download below via existing key
        print(f"  [{i}/{len(items)}] {it['title'][:60]}")
        r = get(path, delay=1.1)
        if r is None:
            continue
        soup = BeautifulSoup(r.content, "html.parser")
        rec = parse_emuseum_object(
            r.url,
            soup,
            source_key="mcmichael",
            source_name="mcmichael",
            collection_name="McMichael Canadian Art Collection",
        )
        if rec is None:
            print("    skip (not Artist / non-painting)")
            continue
        # Prefer stable key by object id
        oid = it["id"]
        rec["key"] = f"mcmichael:{slugify(oid + '-' + (rec['title'] or it['title']))}"
        if rec["key"] in recs and recs[rec["key"]].get("sha256"):
            # merge missing fields only
            old = recs[rec["key"]]
            for k, v in rec.items():
                if v and not old.get(k):
                    old[k] = v
            rec = old
        download(rec, want_images, institution_dir="mcmichael")
        recs[rec["key"]] = rec
        if i % 10 == 0:
            save_records(recs)
    save_records(recs)


# ----------------------------------------------------------------- AGGV
def scrape_aggv(recs, want_images):
    print("[aggv] Lawren S. Harris (people 476051)")
    base = "https://aggv.ca"
    list_url = f"{base}/emuseum/search/Harris?filter=people%3A476051"
    items = emuseum_list_objects(list_url, base)
    print(f"  listed {len(items)} objects")
    for i, it in enumerate(items.values(), 1):
        path = it["path"]
        if not path.startswith("http"):
            # ensure /emuseum prefix
            if not path.startswith("/emuseum"):
                path = "/emuseum" + path if path.startswith("/objects") else path
            path = urljoin(base, path)
        print(f"  [{i}/{len(items)}] {it['title'][:60]}")
        r = get(path, delay=1.1)
        if r is None:
            continue
        soup = BeautifulSoup(r.content, "html.parser")
        rec = parse_emuseum_object(
            r.url,
            soup,
            source_key="aggv",
            source_name="aggv",
            collection_name="Art Gallery of Greater Victoria",
        )
        if rec is None:
            continue
        rec["key"] = f"aggv:{slugify(it['id'] + '-' + (rec['title'] or it['title']))}"
        if rec["key"] in recs and recs[rec["key"]].get("sha256"):
            old = recs[rec["key"]]
            for k, v in rec.items():
                if v and not old.get(k):
                    old[k] = v
            rec = old
        download(rec, want_images, institution_dir="aggv")
        recs[rec["key"]] = rec
    save_records(recs)


# ----------------------------------------------------------------- Wikidata (Canadian holdings)
CA_COLLECTIONS = re.compile(
    r"National Gallery of Canada|McMichael|Art Gallery of Ontario|"
    r"Montreal Museum|Musée des beaux-arts de Montréal|"
    r"Musée national des beaux-arts|Art Gallery of Hamilton|"
    r"Winnipeg Art Gallery|Vancouver Art Gallery|"
    r"Art Gallery of Greater Victoria|Hart House|"
    r"Glenbow|Library and Archives Canada|University of Toronto",
    re.I,
)


def scrape_wikidata_ca(recs, want_images):
    print("[wikidata] Canadian-collection works by Lawren S. Harris")
    query = """
    SELECT ?item ?itemLabel ?inv ?collection ?collectionLabel ?inception ?image
           ?mediumLabel ?dims WHERE {
      ?item wdt:P170 wd:Q3106117 .
      OPTIONAL { ?item wdt:P217 ?inv }
      OPTIONAL { ?item wdt:P195 ?collection }
      OPTIONAL { ?item wdt:P571 ?inception }
      OPTIONAL { ?item wdt:P18 ?image }
      OPTIONAL { ?item wdt:P186 ?medium . }
      OPTIONAL { ?item wdt:P2048 ?h . ?item wdt:P2049 ?w .
                 BIND(CONCAT(STR(?h), " x ", STR(?w)) AS ?dims) }
      SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
    }
    """
    r = get(
        "https://query.wikidata.org/sparql",
        params={"query": query, "format": "json"},
        delay=1.2,
        headers={"Accept": "application/sparql-results+json", "User-Agent": UA},
    )
    if r is None:
        return
    binds = r.json()["results"]["bindings"]
    seen = set()
    n = 0
    for b in binds:
        qid = b["item"]["value"].rsplit("/", 1)[-1]
        col = b.get("collectionLabel", {}).get("value", "")
        if not CA_COLLECTIONS.search(col):
            continue
        # dedupe by qid+inv
        inv = b.get("inv", {}).get("value")
        dedupe = f"{qid}:{inv}"
        if dedupe in seen:
            continue
        seen.add(dedupe)
        title = b["itemLabel"]["value"]
        if title == qid:
            title = None
        inception = b.get("inception", {}).get("value", "")
        date_raw = inception[:10] if inception else None
        img = b.get("image", {}).get("value")
        if img and "Special:FilePath/" in img:
            # request original via commons
            fname = unquote(img.split("Special:FilePath/")[-1])
            img = f"https://commons.wikimedia.org/wiki/Special:FilePath/{fname}?width=2000"
        key = f"wikidata-ca:{slugify(qid + '-' + (title or inv or 'work'))}"
        conf = "high" if img else "medium"
        notes = "Wikidata P170=Lawren Harris; Canadian collection"
        if not img:
            notes += "; no P18 image on Wikidata"
        # Institution slug for folder
        inst = "wikidata_ca"
        if "National Gallery" in col:
            inst = "national_gallery_canada"
        elif "McMichael" in col:
            inst = "mcmichael"
        elif "Ontario" in col:
            inst = "ago"
        elif "Québec" in col or "Quebec" in col or "beaux-arts du Qu" in col:
            inst = "mnbaq"
        elif "Montreal" in col or "Montréal" in col:
            inst = "mbam"

        rec = blank_rec(
            key=key,
            source=inst,
            title=title,
            year=year_from(date_raw),
            date_raw=date_raw,
            collection=col,
            inventory_no=inv,
            medium=b.get("mediumLabel", {}).get("value"),
            dimensions=b.get("dims", {}).get("value"),
            license="Public domain in Canada (artist d.1970); image may have separate rights",
            page_url=f"https://www.wikidata.org/wiki/{qid}",
            image_url=img,
            wikidata_id=qid,
            confidence=conf,
            notes=notes,
        )
        if key in recs and recs[key].get("sha256"):
            continue
        print(f"  {qid} {title} @ {col[:40]}")
        download(rec, want_images, institution_dir=inst)
        recs[key] = rec
        n += 1
    print(f"  added/updated {n} Canadian-collection Wikidata works")
    save_records(recs)


# ----------------------------------------------------------------- UCalgary / Glenbow digital
def scrape_ucalgary(recs, want_images):
    print("[ucalgary] digital collections search")
    url = "https://digitalcollections.ucalgary.ca/search"
    r = get(url, params={"query": "Lawren Harris"}, delay=1.2)
    if r is None:
        print("  blocked or failed")
        return
    soup = BeautifulSoup(r.content, "html.parser")
    # look for item links
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(" ", strip=True)
        if not text:
            continue
        if "/asset/" in href or "/digital/" in href or "/Detail/" in href:
            if re.search(r"harris", text + href, re.I):
                links.append((urljoin(r.url, href), text))
    # also any result cards mentioning Harris paintings
    text = soup.get_text(" ", strip=True)
    print(f"  page len={len(r.content)} harris mentions={len(re.findall('Harris', text))} links={len(links)}")
    for page_url, title in links[:40]:
        if SON_RE.search(title):
            continue
        key = f"ucalgary:{slugify(title)}"
        if key in recs:
            continue
        rec = blank_rec(
            key=key,
            source="glenbow_ucalgary",
            title=title,
            collection="Glenbow Library and Archives / U Calgary Digital Collections",
            page_url=page_url,
            confidence="low",
            notes="Listed from digitalcollections.ucalgary.ca search; verify attribution on page",
            license="Check UCalgary / Glenbow terms",
        )
        # fetch page for image
        pr = get(page_url, delay=1.1)
        if pr:
            ps = BeautifulSoup(pr.content, "html.parser")
            for meta in ps.find_all("meta"):
                if meta.get("property") == "og:image":
                    rec["image_url"] = meta.get("content")
            # crude field scrape
            body = ps.get_text("\n", strip=True)
            if "Lawren P" in body and "Lawren S" not in body:
                continue
            m = re.search(r"(?:Date|Date created|Creation date)\s*[:\n]\s*([^\n]{0,40})", body, re.I)
            if m:
                rec["date_raw"] = m.group(1).strip()
                rec["year"] = year_from(rec["date_raw"])
            if re.search(r"Lawren S\.?\s*Harris", body):
                rec["confidence"] = "medium"
                rec["notes"] = (rec["notes"] or "") + "; page mentions Lawren S. Harris"
            download(rec, want_images, institution_dir="glenbow_ucalgary")
        recs[key] = rec
        print(f"  + {title[:60]}")
    save_records(recs)


# ----------------------------------------------------------------- Canadian Encyclopedia (reference images)
def scrape_canadian_encyclopedia(recs, want_images):
    print("[canadian_encyclopedia] article images")
    url = "https://www.thecanadianencyclopedia.ca/en/article/lawren-stewart-harris"
    r = get(url, delay=1.2)
    if r is None:
        return
    soup = BeautifulSoup(r.content, "html.parser")
    # figures / images with captions
    for i, fig in enumerate(soup.select("figure, .media, .image")):
        img = fig.find("img")
        if not img:
            continue
        src = img.get("src") or img.get("data-src") or ""
        alt = img.get("alt") or ""
        cap = fig.find("figcaption")
        caption = cap.get_text(" ", strip=True) if cap else alt
        if not re.search(r"harris|painting|oil|canvas|lake|mountain", (caption + alt + src), re.I):
            continue
        if re.search(r"portrait of lawren|photograph of", caption + alt, re.I):
            conf = "low"
            notes = "Likely photo of Harris, not a painting"
        else:
            conf = "medium"
            notes = "From Canadian Encyclopedia article; verify artist attribution in caption"
        if "Lawren P" in caption:
            continue
        abs_src = urljoin(url, src)
        key = f"canadian_encyclopedia:{slugify(caption or alt or str(i))}"
        if key in recs:
            continue
        rec = blank_rec(
            key=key,
            source="canadian_encyclopedia",
            title=caption.split(",")[0][:120] if caption else alt,
            date_raw=None,
            year=year_from(caption),
            collection="The Canadian Encyclopedia",
            description=caption,
            page_url=url,
            image_url=abs_src,
            confidence=conf,
            notes=notes,
            license="Check Historica Canada / image credit on page",
        )
        download(rec, want_images, institution_dir="canadian_encyclopedia")
        recs[key] = rec
        print(f"  + {rec['title'][:60]}")
    save_records(recs)


# ----------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--sources",
        default="mcmichael,aggv,wikidata,ucalgary,encyclopedia",
        help="comma list: mcmichael,aggv,wikidata,ucalgary,encyclopedia",
    )
    ap.add_argument("--no-images", action="store_true")
    args = ap.parse_args()
    want_images = not args.no_images
    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    recs = load_records()
    print(f"loaded {len(recs)} existing records -> {OUT}")

    if "mcmichael" in sources:
        scrape_mcmichael(recs, want_images)
    if "aggv" in sources:
        scrape_aggv(recs, want_images)
    if "wikidata" in sources:
        scrape_wikidata_ca(recs, want_images)
    if "ucalgary" in sources:
        scrape_ucalgary(recs, want_images)
    if "encyclopedia" in sources:
        scrape_canadian_encyclopedia(recs, want_images)

    save_records(recs)
    # summary
    by_src = {}
    with_img = 0
    for r in recs.values():
        by_src[r["source"]] = by_src.get(r["source"], 0) + 1
        if r.get("local_path"):
            with_img += 1
    print("DONE", len(recs), "records;", with_img, "with local images")
    for k, v in sorted(by_src.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
