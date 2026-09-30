#!/usr/bin/env python3
"""Long-tail web image harvest for Lawren S. Harris (1885–1970) paintings.

Engines: Openverse API, Bing Images HTML, Yandex Images HTML, DuckDuckGo HTML
web results (page crawl for og:image), Wikimedia Commons search (supplemental).

Usage (from project root, with venv):
  source .venv/bin/activate
  python data/swarm/web_search/scrape_web.py
  python data/swarm/web_search/scrape_web.py --max-per-query 12 --engines openverse,bing

Output under data/swarm/web_search/:
  images/<domain-or-engine>/<slug>.<ext>
  metadata.jsonl, metadata.csv
Resumable: skips existing keys and sha256 duplicates.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html as htmllib
import json
import re
import time
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup

PROJECT = Path(__file__).resolve().parents[3]  # .../LaurenHarris
OUT = Path(__file__).resolve().parent
IMG_DIR = OUT / "images"
META = OUT / "metadata.jsonl"
CSV_OUT = OUT / "metadata.csv"
BLOCKERS = OUT / "blockers.log"
STATE = OUT / "state.json"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
UA_BOT = "LawrenHarrisScraper/0.1 (personal research; contact: local user)"

MIN_SIDE = 400
DELAY = 1.15  # >=1 req/sec per host

session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})

QUERIES = [
    # Title / famous works
    "Lawren Harris Mount Lefroy painting",
    "Lawren Harris Pic Island painting",
    "Lawren Harris Above Lake Superior",
    "Lawren Harris North Shore Lake Superior",
    "Lawren Harris Lake and Mountains",
    "Lawren Harris Mountain Forms",
    "Lawren Harris Isolation Peak",
    "Lawren Harris Maligne Lake",
    "Lawren Harris Icebergs Davis Strait",
    "Lawren Harris From the North Shore Lake Superior",
    "Lawren Harris Afternoon Sun North Shore",
    "Lawren Harris Bylot Island",
    "Lawren Harris Lake Superior Sketch",
    # Themes / periods
    "Lawren Harris Lake Superior painting",
    "Lawren Harris Algoma painting",
    "Lawren Harris Rocky Mountains Jasper",
    "Lawren Harris Baffin Island Arctic sketches",
    "Lawren Harris Nerke Greenland",
    "Lawren Harris Toronto Ward houses urban",
    "Lawren Harris abstract paintings Santa Fe",
    "Lawren Harris abstract Vancouver transcendental",
    "Lawren Harris oil sketch Group of Seven",
    "Lawren S. Harris painting Group of Seven",
    "Lawren Harris snow painting Canadian",
    "Lawren Harris tree painting Algoma",
    # Multilingual
    "Lawren Harris peinture Groupe des Sept",
    "Lawren Harris peinture Lac Supérieur",
    "Lawren Harris Gemälde Group of Seven",
    "Lawren Harris pintura Grupo de los Siete",
    "Lawren Harris montagna dipinto",
    # Broader catch nets
    "\"Lawren Harris\" painting private collection",
    "\"Lawren S. Harris\" oil on canvas",
    "\"Lawren Harris\" exhibition painting image",
    # Extra long-tail titles / variants (round 2)
    "Lawren Harris Pic Island Lake Superior",
    "Lawren Harris \"Lake Superior\" \"oil\"",
    "Lawren Harris \"Mountain Forms\" 1926",
    "Lawren Harris \"North Shore, Lake Superior\" 1926",
    "Lawren Harris \"Icebergs, Davis Strait\"",
    "Lawren Harris \"Abstraction\" Santa Fe",
    "Lawren Harris Toronto house painting",
    "Lawren Harris \"First Snow\" Algoma",
    "Lawren Harris \"Winter Landscape\"",
    "Lawren Harris \"Clouds, Lake Superior\"",
    "Lawren Harris \"Untitled\" mountain abstract",
]

# Curated long-tail pages (blogs / edu / multilang wiki) missed by museums
CURATED_PAGES = [
    "https://en.wikipedia.org/wiki/Lawren_Harris",
    "https://fr.wikipedia.org/wiki/Lawren_Harris",
    "https://de.wikipedia.org/wiki/Lawren_Harris",
    "https://es.wikipedia.org/wiki/Lawren_Harris",
    "https://www.thecanadianencyclopedia.ca/en/article/lawren-stewart-harris",
    "https://www.gallery.ca/collection/artist/lawren-s-harris",
    "https://www.aci-iac.ca/art-books/lawren-s-harris/",
    "https://www.aci-iac.ca/art-books/lawren-s-harris/key-works/",
    "https://www.heffel.com/Artist/A00075/HARRIS_LAWREN_STEWART",
    "https://cowleyabbott.ca/artists/79-lawren-stewart-harris",
    "https://www.artnet.com/artists/lawren-stewart-harris/",
    "https://groupofsevenart.com/lawren-harris/",
    "https://www.wikiart.org/en/lawren-harris",
    "https://www.mcmichael.com/collection/group-of-seven/lawren-s-harris/",
]

# Reject if title/caption/URL strongly suggests non-painting or wrong person
REJECT_PATTERNS = re.compile(
    r"(?i)("
    r"lawren\s*p\.?\s*harris|lauren\s+harris|"
    r"\bselfie\b|\bportrait\s+of\s+lawren\b|photo\s+of\s+harris|"
    r"book\s*cover|postage\s*stamp|\bstamp\b|merchandise|t[- ]?shirt|"
    r"mug\b|poster\s+mock|wallpaper\s+mock|phone\s+case|"
    r"\bcatalogue\s+cover\b|\bauction\s+catalog\s+cover\b|"
    r"stock\s+photo|gettyimages|shutterstock|"
    r"youtube\s+thumbnail|favicon|logo\b|"
    r"harris\s+with\s+|harris\s+and\s+|"
    r"\bphotograph\s+of\s+the\s+artist\b|"
    r"grade\s*\d|student\s+work|school\s+project|kids?\s+art|"
    r"welcome\s+to\s+.?group\s+of\s+seven.?country|"
    r"moments\s+of\s+algoma|"
    r"was\s+here\b|"
    r"before\s+after|periodic\s+table|lunch\s+box"
    r")"
)
KEEP_HINTS = re.compile(
    r"(?i)(painting|oil|canvas|sketch|drawing|watercolou?r|gouache|"
    r"artwork|tableau|peinture|gemälde|cuadro|dipinto|"
    r"mount\s+lefroy|lake\s+superior|algoma|pic\s+island|"
    r"mountain\s+forms|baffin|arctic|jasper|maligne|"
    r"group\s+of\s+seven|groupe\s+des\s+sept|"
    r"lawren\s+(s\.?\s+)?harris)"
)
YEAR_RE = re.compile(r"\b(18\d{2}|19[0-6]\d|1970)\b")
TITLE_YEAR_RE = re.compile(
    r"(?i)(?:lawren\s+(?:s\.?\s+)?harris[:\s,-]+)?(.+?)(?:,\s*|\s+)(18\d{2}|19[0-6]\d|1970)\b"
)

IMG_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/tiff": ".jpg",
}


def log_blocker(msg: str) -> None:
    with BLOCKERS.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    print(f"  [blocker] {msg}")


def get(url, *, params=None, delay=DELAY, retries=3, headers=None, stream=False, timeout=45):
    for i in range(retries):
        try:
            r = session.get(
                url, params=params, timeout=timeout, stream=stream,
                headers=headers or {},
            )
            if r.status_code in (429, 503):
                time.sleep(5 * (i + 1))
                continue
            if r.status_code in (403, 401, 451):
                log_blocker(f"{r.status_code} {url}")
                time.sleep(delay)
                return r
            if r.status_code == 202 and "duckduckgo" in url:
                # DDG challenge / soft block
                log_blocker(f"DDG soft-block 202 {url}")
                time.sleep(delay)
                return r
            r.raise_for_status()
            time.sleep(delay)
            return r
        except requests.RequestException as e:
            if i == retries - 1:
                print(f"  ! failed {url}: {e}")
                return None
            time.sleep(2 * (i + 1))
    return None


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:100] or "untitled"


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host or "unknown"
    except Exception:
        return "unknown"


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


def load_state():
    if STATE.exists():
        return json.loads(STATE.read_text())
    return {"done_queries": {}, "seen_image_urls": []}


def save_state(state):
    STATE.write_text(json.dumps(state, indent=2))


def clean_text(s: str | None) -> str:
    if not s:
        return ""
    s = htmllib.unescape(s)
    s = BeautifulSoup(s, "html.parser").get_text(" ", strip=True)
    s = re.sub(r"\s+", " ", s).strip()
    # Bing highlight chars
    s = s.replace("\ue000", "").replace("\ue001", "")
    return s


def year_from(s: str | None):
    m = YEAR_RE.search(s or "")
    return int(m.group(1)) if m else None


def guess_title(text: str | None):
    t = clean_text(text)
    if not t:
        return None, None
    m = TITLE_YEAR_RE.search(t)
    if m:
        title = m.group(1).strip(" -–—:|")
        # strip leading artist
        title = re.sub(r"(?i)^lawren\s+(?:s\.?\s+)?harris\s*[-–—:,]?\s*", "", title).strip()
        if len(title) > 3:
            return title[:200], int(m.group(2))
    # drop engine boilerplate
    t2 = re.sub(r"(?i)^lawren\s+(?:s\.?\s+)?harris\s*[-–—:,]?\s*", "", t).strip()
    t2 = re.split(r"\s[-|]\s| · | — ", t2)[0].strip()
    if len(t2) > 3 and len(t2) < 120:
        return t2, year_from(t)
    return None, year_from(t)


def should_reject(blob: str, url: str = "") -> str | None:
    text = f"{blob} {url}"
    if REJECT_PATTERNS.search(text):
        return "reject_pattern"
    # require some harris/painting signal unless openverse already tagged
    low = text.lower()
    if "harris" not in low and "group of seven" not in low and "groupe des sept" not in low:
        return "no_harris_signal"
    # reject obvious people-photo pages when no painting cue
    if re.search(r"(?i)\b(portrait|photo|photograph)\b", low) and not KEEP_HINTS.search(low):
        return "likely_photo"
    return None


def confidence_for(title, year, blob, source_engine):
    blob_l = (blob or "").lower()
    if title and year and "lawren" in blob_l and KEEP_HINTS.search(blob_l or ""):
        return "high"
    if title or "painting" in blob_l or "oil" in blob_l or source_engine == "openverse":
        return "medium"
    return "low"


def existing_hashes(recs):
    return {r["sha256"] for r in recs.values() if r.get("sha256")}


def download_image(image_url: str, engine: str, slug: str, recs, hashes) -> dict | None:
    """Download and return size/hash info, or None if skip."""
    if not image_url or not image_url.startswith("http"):
        return None
    # skip tiny bing/yandex thumbs by URL pattern when obvious
    if any(x in image_url for x in ("/thumb/", "_square", "favicon", "sprite")):
        return None

    ext = Path(urlparse(image_url).path).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"

    source_dir = slugify(engine)[:40]
    dest_dir = IMG_DIR / source_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"{slug}{ext}"
    # unique if collision
    n = 2
    while path.exists():
        path = dest_dir / f"{slug}-{n}{ext}"
        n += 1

    r = get(image_url, delay=max(0.8, DELAY), stream=False, headers={"Referer": "https://www.bing.com/"})
    if r is None or r.status_code != 200 or not r.content:
        return None
    ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
    if ctype and not ctype.startswith("image/"):
        print(f"  ! not image {ctype}: {image_url[:90]}")
        return None
    if ctype in IMG_EXT:
        new_ext = IMG_EXT[ctype]
        if path.suffix != new_ext:
            path = path.with_suffix(new_ext)

    data = r.content
    if len(data) < 8_000:
        return None  # too small file
    sha = hashlib.sha256(data).hexdigest()
    if sha in hashes:
        print(f"  = dup sha256 skip")
        return None

    try:
        from PIL import Image
        from io import BytesIO
        with Image.open(BytesIO(data)) as im:
            w, h = im.size
            if min(w, h) < MIN_SIDE:
                print(f"  ! too small {w}x{h}")
                return None
            # crude photo-of-person filter: very tall portrait photos often people
            # keep paintings which are often landscape or square-ish; don't hard reject
    except Exception as e:
        print(f"  ! bad image {e}")
        return None

    path.write_bytes(data)
    hashes.add(sha)
    rel = str(path.relative_to(PROJECT))
    return {"local_path": rel, "sha256": sha, "width": w, "height": h, "bytes": len(data)}


def add_candidate(
    recs, hashes, state, *,
    engine: str,
    image_url: str,
    page_url: str,
    title_hint: str | None,
    description: str | None,
    license_: str | None,
    query: str,
    collection: str | None = None,
    medium: str | None = None,
    extra_notes: str = "",
):
    image_url = (image_url or "").strip()
    if not image_url or image_url in state["seen_image_urls"]:
        return False
    blob = " | ".join(filter(None, [title_hint, description, page_url, image_url]))
    reason = should_reject(blob, image_url)
    if reason:
        print(f"  - skip ({reason}): {(title_hint or image_url)[:70]}")
        state["seen_image_urls"].append(image_url)
        return False

    title, year = guess_title(title_hint or description or "")
    if not title:
        title, year2 = guess_title(description or "")
        year = year or year2
    date_raw = None
    if year:
        date_raw = str(year)
    elif YEAR_RE.search(title_hint or ""):
        date_raw = YEAR_RE.search(title_hint).group(1)

    conf = confidence_for(title, year, blob, engine)
    # bump down if merchandise-looking hosts but painting shown
    host = domain_of(page_url or image_url)
    if any(x in host for x in ("fineartamerica", "redbubble", "society6", "pinterest")):
        conf = "low"
        extra_notes = (extra_notes + "; merchandise/print host — painting crop only").strip("; ")

    # Prefer image host domain for folder; engine as source field prefix
    src_label = host if host not in ("unknown", "tse1.mm.bing.net", "tse2.mm.bing.net") else engine
    if "bing.com" in host or "yandex" in host or "duckduckgo" in host:
        src_label = domain_of(image_url) if domain_of(image_url) != "unknown" else engine

    slug_base = slugify(title or Path(urlparse(image_url).path).stem or "untitled")
    key = f"web_search:{src_label}:{slug_base}:{hashlib.md5(image_url.encode()).hexdigest()[:8]}"
    if key in recs:
        state["seen_image_urls"].append(image_url)
        return False

    info = download_image(image_url, src_label, slug_base, recs, hashes)
    state["seen_image_urls"].append(image_url)
    if not info:
        return False

    notes = f"query={query!r}; engine={engine}"
    if extra_notes:
        notes += f"; {extra_notes}"

    rec = {
        "key": key,
        "source": src_label,
        "title": title,
        "year": year,
        "date_raw": date_raw,
        "collection": collection,
        "inventory_no": None,
        "medium": medium,
        "dimensions": None,
        "genre": None,
        "style": None,
        "description": clean_text(description)[:500] or None,
        "license": license_,
        "page_url": page_url,
        "image_url": image_url,
        "local_path": info["local_path"],
        "sha256": info["sha256"],
        "width": info["width"],
        "height": info["height"],
        "wikidata_id": None,
        "confidence": conf,
        "notes": notes,
    }
    recs[key] = rec
    print(f"  + kept [{conf}] {title or '(untitled)'} {info['width']}x{info['height']} <- {src_label}")
    return True


# ---------------- engines ----------------

def search_openverse(query: str, max_n: int):
    out = []
    page = 1
    while len(out) < max_n and page <= 5:
        r = get(
            "https://api.openverse.org/v1/images/",
            params={"q": query, "page_size": min(20, max_n), "page": page},
            delay=DELAY,
            headers={"User-Agent": UA_BOT, "Accept": "application/json"},
        )
        if r is None or r.status_code != 200:
            if r is not None:
                log_blocker(f"openverse {r.status_code} q={query}")
            break
        try:
            data = r.json()
        except ValueError:
            break
        results = data.get("results") or []
        if not results:
            break
        for x in results:
            out.append({
                "image_url": x.get("url"),
                "page_url": x.get("foreign_landing_url") or x.get("url"),
                "title_hint": x.get("title"),
                "description": x.get("title"),
                "license": x.get("license"),
                "collection": x.get("source"),
                "extra": f"creator={x.get('creator')}",
            })
            if len(out) >= max_n:
                break
        page += 1
        if not data.get("page_count") or page > (data.get("page_count") or 1):
            break
    return out


def search_bing(query: str, max_n: int):
    out = []
    first = 1
    while len(out) < max_n and first <= 80:
        r = get(
            "https://www.bing.com/images/search",
            params={"q": query, "form": "HDRSC2", "first": first, "count": 35},
            delay=DELAY,
        )
        if r is None or r.status_code != 200:
            if r is not None:
                log_blocker(f"bing {r.status_code} q={query}")
            break
        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.select("a.iusc")
        if not items:
            # sometimes blocked / empty
            if "captcha" in r.text.lower() or "challenge" in r.text.lower():
                log_blocker(f"bing captcha/challenge q={query}")
            break
        before = len(out)
        for a in items:
            raw = a.get("m")
            if not raw:
                continue
            try:
                d = json.loads(raw)
            except json.JSONDecodeError:
                continue
            murl = d.get("murl")
            if not murl:
                continue
            out.append({
                "image_url": murl,
                "page_url": d.get("purl") or d.get("surl") or murl,
                "title_hint": clean_text(d.get("t")),
                "description": clean_text(d.get("desc") or d.get("t")),
                "license": None,
                "collection": None,
                "extra": "",
            })
            if len(out) >= max_n:
                break
        if len(out) == before:
            break
        first += 35
    return out


def search_yandex(query: str, max_n: int):
    out = []
    r = get(
        "https://yandex.com/images/search",
        params={"text": query},
        delay=DELAY,
    )
    if r is None or r.status_code != 200:
        if r is not None:
            log_blocker(f"yandex {r.status_code} q={query}")
        return out
    text = r.text
    if "captcha" in text.lower() and "SmartCaptcha" in text:
        log_blocker(f"yandex captcha q={query}")
        return out
    # HTML-entity-encoded JSON fragments
    # origUrl&quot;:&quot;https://...&quot;
    pat = re.compile(
        r"origUrl&quot;:&quot;(https?://[^&]+?)&quot;.*?snippet&quot;:\{&quot;title&quot;:&quot;(.*?)&quot;",
        re.DOTALL,
    )
    for m in pat.finditer(text):
        url = htmllib.unescape(m.group(1).replace("\\/", "/"))
        title = clean_text(htmllib.unescape(m.group(2)))
        # try find page url nearby
        chunk = text[m.start(): m.start() + 800]
        um = re.search(r"&quot;url&quot;:&quot;(https?://[^&]+?)&quot;", chunk)
        page = htmllib.unescape(um.group(1).replace("\\/", "/")) if um else url
        out.append({
            "image_url": url,
            "page_url": page,
            "title_hint": title,
            "description": title,
            "license": None,
            "collection": None,
            "extra": "",
        })
        if len(out) >= max_n:
            break
    # fallback: plain origUrl if present
    if not out:
        for m in re.finditer(r'"origUrl"\s*:\s*"(https?://[^"]+)"', text):
            url = m.group(1).encode().decode("unicode_escape")
            out.append({
                "image_url": url,
                "page_url": url,
                "title_hint": query,
                "description": query,
                "license": None,
                "collection": None,
                "extra": "yandex_fallback",
            })
            if len(out) >= max_n:
                break
    return out


def search_ddg_web(query: str, max_n: int):
    """DuckDuckGo HTML web search -> follow result pages for og:image."""
    out = []
    r = get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        delay=DELAY,
        headers={"User-Agent": UA_BOT},
    )
    if r is None:
        return out
    if r.status_code != 200:
        log_blocker(f"ddg-html {r.status_code} q={query}")
        return out
    soup = BeautifulSoup(r.text, "html.parser")
    links = soup.select("a.result__a")
    if not links:
        # alternate selectors
        links = soup.select(".results a.result-link") or soup.select("a[href*='uddg=']")
    pages = []
    for a in links:
        href = a.get("href") or ""
        # DDG redirect
        m = re.search(r"uddg=([^&]+)", href)
        if m:
            href = unquote(m.group(1))
        if not href.startswith("http"):
            continue
        if any(x in href for x in ("duckduckgo.com", "youtube.com", "facebook.com")):
            continue
        pages.append((clean_text(a.get_text()), href))
        if len(pages) >= max_n:
            break

    for title, page_url in pages:
        pr = get(page_url, delay=DELAY)
        if pr is None or pr.status_code != 200:
            continue
        ctype = (pr.headers.get("content-type") or "").lower()
        if "html" not in ctype:
            continue
        psoup = BeautifulSoup(pr.text, "html.parser")
        og = psoup.find("meta", property="og:image") or psoup.find("meta", attrs={"name": "og:image"})
        img = og.get("content") if og else None
        if not img:
            # largest-ish img with harris in alt/src
            for im in psoup.select("img"):
                src = im.get("src") or im.get("data-src") or ""
                alt = im.get("alt") or ""
                if "harris" in (alt + src).lower() and src.startswith("http"):
                    img = src
                    title = title or alt
                    break
        if not img:
            continue
        img = urljoin(page_url, img)
        out.append({
            "image_url": img,
            "page_url": page_url,
            "title_hint": title or (psoup.title.get_text(strip=True) if psoup.title else None),
            "description": clean_text(
                (psoup.find("meta", property="og:description") or {}).get("content")
                if psoup.find("meta", property="og:description") else title
            ),
            "license": None,
            "collection": None,
            "extra": "ddg_web_ogimage",
        })
        if len(out) >= max_n:
            break
    return out


def search_commons(query: str, max_n: int):
    """Wikimedia Commons image search (may overlap main scrape; still useful long-tail)."""
    out = []
    r = get(
        "https://commons.wikimedia.org/w/api.php",
        params={
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": query,
            "gsrnamespace": 6,
            "gsrlimit": min(max_n, 20),
            "prop": "imageinfo|info",
            "iiprop": "url|size|extmetadata|mime",
            "iiurlwidth": 1200,
        },
        delay=DELAY,
        headers={"User-Agent": UA_BOT},
    )
    if r is None or r.status_code != 200:
        return out
    pages = (r.json().get("query") or {}).get("pages") or {}
    for p in pages.values():
        title = p.get("title", "").replace("File:", "")
        infos = p.get("imageinfo") or []
        if not infos:
            continue
        ii = infos[0]
        meta = ii.get("extmetadata") or {}
        desc = clean_text((meta.get("ImageDescription") or {}).get("value"))
        artist = clean_text((meta.get("Artist") or {}).get("value"))
        license_ = clean_text((meta.get("LicenseShortName") or {}).get("value"))
        out.append({
            "image_url": ii.get("url") or ii.get("thumburl"),
            "page_url": f"https://commons.wikimedia.org/wiki/{quote_plus(p.get('title','').replace(' ','_'))}",
            "title_hint": title,
            "description": desc or artist,
            "license": license_,
            "collection": "Wikimedia Commons",
            "extra": f"artist={artist}",
            "medium": None,
        })
    return out


def crawl_curated_pages(max_n: int = 40):
    """Pull candidate images from known long-tail / multilang pages."""
    out = []
    for page_url in CURATED_PAGES:
        if len(out) >= max_n:
            break
        print(f"  curated: {page_url}")
        r = get(page_url, delay=DELAY, headers={"User-Agent": UA_BOT})
        if r is None or r.status_code != 200:
            if r is not None:
                log_blocker(f"curated {r.status_code} {page_url}")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        page_title = clean_text(soup.title.get_text() if soup.title else "")
        # og:image
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            out.append({
                "image_url": urljoin(page_url, og["content"]),
                "page_url": page_url,
                "title_hint": page_title,
                "description": page_title,
                "license": None,
                "collection": None,
                "extra": "curated_ogimage",
            })
        # wikipedia / commons thumbs that look like paintings
        for im in soup.select("img"):
            src = im.get("src") or im.get("data-src") or im.get("data-file-src") or ""
            alt = clean_text(im.get("alt") or "")
            if not src:
                continue
            full = urljoin(page_url, src)
            # prefer larger wiki images
            if "/thumb/" in full:
                # try original: strip /thumb/ and trailing /NNpx-
                m = re.match(r"(https?://upload\.wikimedia\.org/wikipedia/[^/]+)/thumb/(.+)/[^/]+$", full)
                if m:
                    full = f"{m.group(1)}/{m.group(2)}"
            blob = f"{alt} {full} {page_title}"
            if "harris" not in blob.lower() and "painting" not in alt.lower():
                # still allow if on harris page and looks like artwork file
                if "lawren" not in page_url.lower() and "harris" not in page_url.lower():
                    continue
            if any(x in full.lower() for x in ("icon", "logo", "sprite", "wikimedia-button", "static/images")):
                continue
            if not re.search(r"\.(jpg|jpeg|png|webp)($|\?)", full, re.I) and "upload.wikimedia.org" not in full:
                continue
            out.append({
                "image_url": full,
                "page_url": page_url,
                "title_hint": alt or page_title,
                "description": alt or page_title,
                "license": None,
                "collection": None,
                "extra": "curated_page_img",
            })
            if len(out) >= max_n:
                break
    return out


ENGINES = {
    "openverse": search_openverse,
    "bing": search_bing,
    "yandex": search_yandex,
    "ddg": search_ddg_web,
    "commons": search_commons,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="openverse,bing,yandex,ddg,commons",
                    help="comma list")
    ap.add_argument("--max-per-query", type=int, default=10)
    ap.add_argument("--max-new", type=int, default=180,
                    help="stop after this many newly kept images")
    ap.add_argument("--queries", default="", help="optional | separated override")
    ap.add_argument("--curated", action="store_true", help="also crawl CURATED_PAGES")
    ap.add_argument("--curated-only", action="store_true")
    args = ap.parse_args()

    engines = [e.strip() for e in args.engines.split(",") if e.strip() in ENGINES]
    queries = [q.strip() for q in args.queries.split("|") if q.strip()] if args.queries else QUERIES

    recs = load_records()
    hashes = existing_hashes(recs)
    state = load_state()
    if len(state["seen_image_urls"]) > 20000:
        state["seen_image_urls"] = state["seen_image_urls"][-10000:]

    print(f"[web_search] {len(recs)} existing records, engines={engines}")
    kept_new = 0

    if args.curated or args.curated_only:
        print("\n[curated pages]")
        hits = crawl_curated_pages(max_n=60)
        print(f"  {len(hits)} candidates")
        for h in hits:
            if kept_new >= args.max_new:
                break
            ok = add_candidate(
                recs, hashes, state,
                engine="curated",
                image_url=h.get("image_url"),
                page_url=h.get("page_url"),
                title_hint=h.get("title_hint"),
                description=h.get("description"),
                license_=h.get("license"),
                query="curated_pages",
                collection=h.get("collection"),
                medium=h.get("medium"),
                extra_notes=h.get("extra") or "",
            )
            if ok:
                kept_new += 1
        save_records(recs)
        save_state(state)

    if args.curated_only:
        titled = sum(1 for r in recs.values() if r.get("title"))
        yeared = sum(1 for r in recs.values() if r.get("year"))
        print(f"\n[done] total={len(recs)} new_this_run={kept_new} with_title={titled} with_year={yeared}")
        return

    for qi, query in enumerate(queries, 1):
        if kept_new >= args.max_new:
            break
        for engine in engines:
            qkey = f"{engine}::{query}"
            if state["done_queries"].get(qkey):
                continue
            print(f"\n[{qi}/{len(queries)}] {engine}: {query}")
            try:
                hits = ENGINES[engine](query, args.max_per_query)
            except Exception as e:
                log_blocker(f"{engine} exception q={query}: {e}")
                hits = []
            print(f"  {len(hits)} candidates")
            for h in hits:
                if kept_new >= args.max_new:
                    break
                ok = add_candidate(
                    recs, hashes, state,
                    engine=engine,
                    image_url=h.get("image_url"),
                    page_url=h.get("page_url"),
                    title_hint=h.get("title_hint"),
                    description=h.get("description"),
                    license_=h.get("license"),
                    query=query,
                    collection=h.get("collection"),
                    medium=h.get("medium"),
                    extra_notes=h.get("extra") or "",
                )
                if ok:
                    kept_new += 1
                    if kept_new % 5 == 0:
                        save_records(recs)
                        save_state(state)
            state["done_queries"][qkey] = {
                "hits": len(hits),
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            save_state(state)
            save_records(recs)

    save_records(recs)
    save_state(state)

    titled = sum(1 for r in recs.values() if r.get("title"))
    yeared = sum(1 for r in recs.values() if r.get("year"))
    print(f"\n[done] total={len(recs)} new_this_run={kept_new} with_title={titled} with_year={yeared}")
    print(f"  meta={META}")
    print(f"  images={IMG_DIR}")


if __name__ == "__main__":
    main()
