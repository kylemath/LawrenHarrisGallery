#!/usr/bin/env python3
"""Commons deep gap-fill for Lawren S. Harris — lean, rate-limit-aware, resumable."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
IMG_DIR = HERE / "images" / "commons"
META = HERE / "metadata.jsonl"
CSV_OUT = HERE / "metadata.csv"
CANDIDATES = HERE / "candidates.json"
REJECTED = HERE / "rejected.json"
SHALLOW_IMG = PROJECT / "data" / "images" / "commons"
REPORT = HERE / "REPORT.md"

UA = (
    "LawrenHarrisCommonsDeep/1.0 (research dataset Lawren S. Harris paintings; "
    "personal archival; serial polite; contact: local swarm worker)"
)
API = "https://commons.wikimedia.org/w/api.php"
WDQS = "https://query.wikidata.org/sparql"
ARTIST = "Q3106117"
DELAY = 1.2  # elevated after 429s

IMG_MIME = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp",
            "image/tiff": ".jpg", "image/gif": ".gif"}

session = requests.Session()
session.headers.update({"User-Agent": UA, "Accept": "application/json"})


def log(*a):
    print(*a, flush=True)


def get(url, *, params=None, delay=DELAY, retries=6, **kw):
    for i in range(retries):
        try:
            r = session.get(url, params=params, timeout=90, **kw)
            if r.status_code in (429, 503):
                wait = min(120, 15 * (i + 1))
                log(f"  429/503 sleep {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            time.sleep(delay)
            return r
        except requests.RequestException as e:
            if i == retries - 1:
                log(f"  ! {e}")
                return None
            time.sleep(5 * (i + 1))
    return None


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:120] or "untitled"


def strip_html(s):
    return BeautifulSoup(s or "", "html.parser").get_text(" ", strip=True)


def year_from(s):
    m = re.search(r"\b(1[89]\d\d|19[0-7]\d)\b", s or "")
    return int(m.group(1)) if m else None


def save_json(p, o):
    p.write_text(json.dumps(o, ensure_ascii=False, indent=2))


def load_records():
    recs = {}
    if META.exists():
        for line in META.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                recs[r["key"]] = r
    return recs


def save_records(recs):
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    with META.open("w") as f:
        for r in recs.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    cols = ["key", "source", "title", "year", "date_raw", "collection", "inventory_no",
            "medium", "dimensions", "genre", "style", "description", "license",
            "page_url", "image_url", "local_path", "sha256", "width", "height",
            "wikidata_id", "confidence", "notes"]
    with CSV_OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in recs.values():
            w.writerow(r)


PHOTO_RE = re.compile(
    r"(?i)(at his studio|sitting at a table|in his vancouver|member of the group|"
    r"write up|hanging committee|leaders of the federation|arts and letters club|"
    r"group-of-seven-artists|group at the arts|lawren s\. harris, o\.s\.a|"
    r"lauren harris at his studio|^file:lawren harris\.jpe?g$)"
)
SON_RE = re.compile(r"(?i)lawren p\.?\s*harris")
PAINT_HINT = re.compile(
    r"(?i)(oil|canvas|sketch|painting|algoma|superior|lefroy|pic island|mountain|"
    r"arctic|baffin|iceberg|waterfall|houses|street|toronto|algonquin|"
    r"lake and mountains|isolation|maligne|decorative landscape|north shore|"
    r"robson|bylot|greenland|beaver|rapids|snow|woods|ward|abstract|"
    r"from the north|from the south|inlet|peaks|swamp)"
)


def is_img(t):
    low = t.lower()
    return low.startswith("file:") and any(
        low.endswith(e) for e in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp", ".gif")
    )


def looks_painting(title):
    if not is_img(title):
        return False
    if SON_RE.search(title) or PHOTO_RE.search(title):
        return False
    t = title.lower()
    if re.search(r"lawren\s+(stewart\s+|s\.?\s*)?harris", t):
        return bool(PAINT_HINT.search(t)) or not re.search(
            r"\b(photo|portrait|studio|sitting|table|club)\b", t
        )
    if re.search(r"\bharris\b.*(mount lefroy|pic island|lake superior|algoma|mountain forms)", t):
        return True
    return False


def cat_files(cat):
    out, cont = [], {}
    while True:
        r = get(API, params={
            "action": "query", "format": "json", "list": "categorymembers",
            "cmtitle": cat, "cmlimit": 500, "cmtype": "file", **cont,
        })
        if not r:
            break
        j = r.json()
        out += [m["title"] for m in j["query"]["categorymembers"]]
        if "continue" not in j:
            break
        cont = j["continue"]
    return out


def search_files(q, pages=2):
    out, cont = [], {}
    for _ in range(pages):
        r = get(API, params={
            "action": "query", "format": "json", "list": "search",
            "srsearch": q, "srnamespace": 6, "srlimit": 50, **cont,
        })
        if not r:
            break
        j = r.json()
        out += [m["title"] for m in j["query"]["search"]]
        if "continue" not in j:
            break
        cont = j["continue"]
    return out


def embeddedin(title):
    out, cont = [], {}
    while True:
        r = get(API, params={
            "action": "query", "format": "json", "list": "embeddedin",
            "eititle": title, "einamespace": 6, "eilimit": 500, **cont,
        })
        if not r:
            break
        j = r.json()
        out += [m["title"] for m in j.get("query", {}).get("embeddedin", [])]
        if "continue" not in j:
            break
        cont = j["continue"]
    return out


def discover():
    bag = {}

    def add(t, via):
        if not t.startswith("File:"):
            return
        bag.setdefault(t, [])
        if via not in bag[t]:
            bag[t].append(via)

    log("=== DISCOVER ===")
    # Wait if we were recently rate-limited
    time.sleep(5)

    shallow = cat_files("Category:Paintings by Lawren Harris")
    log(f"shallow paintings cat: {len(shallow)}")
    for t in shallow:
        add(t, "cat:Paintings by Lawren Harris")

    # Person category (mostly photos — keep for rejection accounting)
    person = cat_files("Category:Lawren Harris")
    log(f"cat Lawren Harris files: {len(person)}")
    for t in person:
        add(t, "cat:Lawren Harris")

    # Category search for Lawren Harris*
    r = get(API, params={
        "action": "query", "format": "json", "list": "search",
        "srsearch": "Lawren Harris", "srnamespace": 14, "srlimit": 40,
    })
    if r:
        for m in r.json()["query"]["search"]:
            cat = m["title"]
            log(f"  related cat: {cat}")
            if cat in ("Category:Lawren Harris", "Category:Paintings by Lawren Harris"):
                continue
            files = cat_files(cat)
            # filter: harris in name OR painting-related cat
            for f in files:
                if "harris" in f.lower() or "painting" in cat.lower():
                    add(f, f"cat:{cat}")

    # Targeted searches (filetype:bitmap cuts PDF noise)
    queries = [
        "haswbstatement:P170=Q3106117",
        "haswbstatement:P180=Q3106117",
        'filetype:bitmap intitle:"Lawren Harris"',
        'filetype:bitmap "Lawren Stewart Harris"',
        'filetype:bitmap "Lake and Mountains" Harris',
        'filetype:bitmap "Mountain Forms" Harris',
        'filetype:bitmap "Above Lake Superior"',
        'filetype:bitmap "Isolation Peak" Harris',
        'filetype:bitmap intitle:Harris intitle:Algoma',
        'filetype:bitmap intitle:Harris intitle:Lefroy',
        'filetype:bitmap "Pic Island" Harris',
        'filetype:bitmap "Decorative Landscape" Harris',
        'incategory:"Paintings by Group of Seven" Harris',
        'incategory:"Art Canada Institute" Harris',
    ]
    for q in queries:
        files = search_files(q, pages=2)
        kept = [t for t in files if looks_painting(t) or (
            is_img(t) and "harris" in t.lower() and not PHOTO_RE.search(t) and not SON_RE.search(t)
        )]
        log(f"search {q[:55]!r}: {len(files)}->{len(kept)}")
        for t in kept:
            add(t, f"search:{q[:50]}")

    # Creator backlinks
    emb = embeddedin("Creator:Lawren Harris")
    log(f"embeddedin Creator:Lawren Harris: {len(emb)}")
    for t in emb:
        add(t, "embeddedin:Creator:Lawren Harris")

    # Wikidata
    wd_rows = []
    rq = get(WDQS, params={
        "query": f"""
        SELECT ?item ?itemLabel ?inception ?image ?collectionLabel ?inv ?mediumLabel ?genreLabel ?commons WHERE {{
          ?item wdt:P170 wd:{ARTIST} .
          OPTIONAL {{ ?item wdt:P571 ?inception }}
          OPTIONAL {{ ?item wdt:P18 ?image }}
          OPTIONAL {{ ?item wdt:P195 ?collection }}
          OPTIONAL {{ ?item wdt:P217 ?inv }}
          OPTIONAL {{ ?item wdt:P186 ?medium }}
          OPTIONAL {{ ?item wdt:P136 ?genre }}
          OPTIONAL {{ ?item wdt:P373 ?commons }}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,fr,de". }}
        }}""",
        "format": "json",
    }, delay=1.5)
    if rq:
        for x in rq.json()["results"]["bindings"]:
            v = lambda k: x.get(k, {}).get("value")
            row = {
                "qid": v("item").rsplit("/", 1)[-1],
                "label": v("itemLabel"),
                "inception": v("inception"),
                "image": v("image"),
                "collection": v("collectionLabel"),
                "inv": v("inv"),
                "medium": v("mediumLabel"),
                "genre": v("genreLabel"),
                "commons_cat": v("commons"),
            }
            wd_rows.append(row)
            if row["image"]:
                fname = unquote(row["image"].rsplit("/", 1)[-1]).replace("_", " ")
                add("File:" + fname, f"wikidata:P18:{row['qid']}")
            if row["commons_cat"]:
                cat = row["commons_cat"] if row["commons_cat"].startswith("Category:") else "Category:" + row["commons_cat"]
                for f in cat_files(cat):
                    add(f, f"wikidata:P373:{row['qid']}")
    log(f"wikidata works: {len(wd_rows)}")

    # Wikipedia langs
    for lang in ["en", "fr", "de", "es", "it", "nl", "pt", "sv", "ja", "pl"]:
        api = f"https://{lang}.wikipedia.org/w/api.php"
        r = get(api, params={
            "action": "query", "format": "json", "list": "search",
            "srsearch": "Lawren Harris", "srlimit": 6,
        })
        if not r:
            continue
        pages = [m["title"] for m in r.json().get("query", {}).get("search", [])]
        if not pages:
            continue
        r2 = get(api, params={
            "action": "query", "format": "json", "titles": "|".join(pages[:8]),
            "prop": "images|pageimages", "imlimit": 50, "piprop": "name",
        })
        if not r2:
            continue
        n = 0
        for pg in r2.json().get("query", {}).get("pages", {}).values():
            for im in pg.get("images") or []:
                t = im["title"]
                if looks_painting(t) or (is_img(t) and "harris" in t.lower() and not PHOTO_RE.search(t)):
                    add(t, f"wikipedia:{lang}")
                    n += 1
            pi = pg.get("pageimage")
            if pi:
                ft = pi if str(pi).startswith("File:") else "File:" + pi
                if looks_painting(ft) or ("harris" in ft.lower() and not PHOTO_RE.search(ft)):
                    add(ft, f"wikipedia:{lang}:pi")
                    n += 1
        log(f"wikipedia/{lang}: {n} from {len(pages)} pages")

    data = {"titles": bag, "wikidata_works": wd_rows, "shallow_titles": shallow}
    save_json(CANDIDATES, data)
    log(f"TOTAL unique files: {len(bag)}")
    return data


def imageinfo(titles):
    out = []
    for i in range(0, len(titles), 40):
        chunk = titles[i:i + 40]
        r = get(API, params={
            "action": "query", "format": "json", "titles": "|".join(chunk),
            "prop": "imageinfo|categories",
            "iiprop": "url|extmetadata|size|mime",
            "iiurlwidth": 2560,
            "cllimit": 40,
        })
        if not r:
            continue
        for pg in r.json().get("query", {}).get("pages", {}).values():
            if "imageinfo" in pg:
                ii = pg["imageinfo"][0]
                ii["_categories"] = [c["title"] for c in pg.get("categories") or []]
                out.append((pg["title"], ii))
    return out


def classify(title, ii):
    em = ii.get("extmetadata", {})
    cats = ii.get("_categories", [])
    val = lambda k: strip_html(em.get(k, {}).get("value", ""))
    blob = " | ".join([title, val("Artist"), val("ImageDescription"), val("ObjectName"), " ".join(cats)])
    if SON_RE.search(blob):
        return "reject", "Lawren P. Harris"
    if PHOTO_RE.search(title) or re.search(r"(?i)\b(photograph of|portrait of lawren|at his studio)\b", blob):
        return "reject", "photograph of person"
    if not is_img(title):
        return "reject", "not image"
    in_paint = any("paintings by lawren harris" in c.lower() for c in cats)
    if in_paint:
        return "high", "Category:Paintings by Lawren Harris"
    if looks_painting(title) and re.search(r"(?i)lawren\s+(s\.?\s*|stewart\s+)?harris", blob):
        if re.search(r"(?i)(oil on|canvas|sketch)", blob):
            return "high", "filename+medium"
        return "medium", "Harris painting filename"
    if re.search(r"(?i)lawren\s+(s\.?\s*|stewart\s+)?harris", blob) and looks_painting(title):
        return "low", "needs visual"
    if looks_painting(title):
        return "low", "filename hint only"
    return "reject", "weak/non-painting"


def download_all(data):
    bag = data["titles"]
    shallow = set(data.get("shallow_titles") or [])
    wd_by = {}
    for row in data.get("wikidata_works") or []:
        if row.get("image"):
            fname = unquote(row["image"].rsplit("/", 1)[-1]).replace("_", " ")
            wd_by["File:" + fname] = row
    shallow_slugs = {p.stem for p in SHALLOW_IMG.iterdir()} if SHALLOW_IMG.exists() else set()

    # Prioritize painting-like
    titles = sorted(bag, key=lambda t: (0 if looks_painting(t) else 1, 0 if t in shallow else 0, t))
    # Deduplicate near-identical underscore variants by normalizing
    norm = {}
    for t in titles:
        k = t.replace("_", " ")
        norm.setdefault(k, t)
    titles = list(norm.values())

    log(f"=== DOWNLOAD imageinfo {len(titles)} ===")
    infos = imageinfo(titles)
    recs = load_records()
    rejected = []
    n_dl = 0

    for title, ii in infos:
        mime = ii.get("mime", "")
        if mime not in IMG_MIME:
            rejected.append({"title": title, "reason": f"mime {mime}"})
            continue
        conf, notes = classify(title, ii)
        via = bag.get(title) or bag.get(title.replace(" ", "_")) or []
        if conf == "reject":
            rejected.append({"title": title, "reason": notes, "via": via[:3]})
            continue

        em = ii.get("extmetadata", {})
        val = lambda k: strip_html(em.get(k, {}).get("value", ""))
        slug = slugify(title.replace("File:", ""))
        key = f"commons_deep:{slug}"
        if key in recs and recs[key].get("local_path") and (PROJECT / recs[key]["local_path"]).exists():
            log(f"  skip {slug}")
            continue

        url = ii["url"] if mime != "image/tiff" else (ii.get("thumburl") or ii["url"])
        date_raw = val("DateTimeOriginal") or val("DateTime")
        wd = wd_by.get(title) or wd_by.get(title.replace("_", " "))
        rec = {
            "key": key, "source": "commons_deep",
            "title": val("ObjectName") or title.replace("File:", "").rsplit(".", 1)[0],
            "year": year_from(date_raw) or year_from(title) or year_from(val("ImageDescription")),
            "date_raw": date_raw,
            "collection": None, "inventory_no": None, "medium": None, "dimensions": None,
            "genre": None, "style": None,
            "description": (val("ImageDescription") or "")[:2000],
            "license": val("LicenseShortName") or val("UsageTerms"),
            "page_url": ii.get("descriptionurl"), "image_url": url,
            "local_path": None, "sha256": None,
            "width": ii.get("width"), "height": ii.get("height"),
            "wikidata_id": wd["qid"] if wd else None,
            "confidence": conf,
            "notes": notes
            + (f"; via={','.join(via[:4])}" if via else "")
            + ("; in_shallow_cat" if title in shallow else "; NEW_vs_shallow_cat")
            + ("; slug_on_shallow_disk" if slug in shallow_slugs else ""),
        }
        if wd:
            rec["collection"] = wd.get("collection")
            rec["inventory_no"] = wd.get("inv")
            rec["medium"] = wd.get("medium")
            rec["genre"] = wd.get("genre")
            if wd.get("inception"):
                rec["date_raw"] = wd["inception"][:10]
                rec["year"] = year_from(wd["inception"]) or rec["year"]
            if wd.get("label"):
                rec["title"] = wd["label"]
        if not rec["collection"] and val("Credit"):
            rec["collection"] = val("Credit")[:200]
        m = re.search(r"(?i)(oil on (?:canvas|board|panel|wood|paperboard)[^.;,]{0,40})",
                      rec.get("description") or "")
        if m and not rec["medium"]:
            rec["medium"] = m.group(1)

        path = IMG_DIR / f"{slug}{IMG_MIME[mime]}"
        if path.exists() and path.stat().st_size > 1000:
            data_b = path.read_bytes()
        else:
            r = get(url)
            if not r or not r.content or len(r.content) < 800:
                rejected.append({"title": title, "reason": "download fail"})
                continue
            if not r.headers.get("content-type", "").startswith("image/"):
                rejected.append({"title": title, "reason": "bad content-type"})
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(r.content)
            data_b = r.content
            n_dl += 1
            log(f"  dl[{n_dl}] {path.name} {len(data_b)//1024}KB conf={conf}")

        rec["local_path"] = str(path.relative_to(PROJECT))
        rec["sha256"] = hashlib.sha256(data_b).hexdigest()
        try:
            from PIL import Image
            with Image.open(path) as im:
                rec["width"], rec["height"] = im.size
        except Exception:
            pass
        recs[key] = rec
        if n_dl % 3 == 0:
            save_records(recs)

    save_records(recs)
    save_json(REJECTED, rejected)
    log(f"DONE records={len(recs)} downloads={n_dl} rejected={len(rejected)}")
    return recs, rejected


def main():
    args = sys.argv[1:]
    if "--download-only" in args and CANDIDATES.exists():
        data = json.loads(CANDIDATES.read_text())
    else:
        data = discover()
        if "--discover-only" in args:
            return
    download_all(data)


if __name__ == "__main__":
    main()
