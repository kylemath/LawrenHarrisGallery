#!/usr/bin/env python3
"""Scan data/ and write docs/catalog.js for the gallery.

Checklist = cleaned works from data/swarm/index/works_index.jsonl
(Wikidata + WikiArt + Commons titles), with biography and exhibition
sentences removed. Images are everything under data/**/*.jpg|png|webp.
Re-run after scrapers add files:  python build_docs.py
"""
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "docs" / "catalog.js"
INDEX = DATA / "swarm" / "index" / "works_index.jsonl"

JUNK = re.compile(
    r"\b(born|died|exhibition|retrospective|biography|formation of|"
    r"hanging committee|logic of ecstasy|wikipedia|retrieved|"
    r"was a |member of|christmas|http|www\.|society of artists|"
    r"painter.s progress|art canada|group of seven was)\b|"
    r"in snow\)|ft\) canada",
    re.I,
)
GENERIC = re.compile(
    r"^(untitled|abstraction|abstract|shoreline|winter morning|lake superior)$"
)
MEDIUM_TAIL = re.compile(
    r"\b(oil(on)?|on canvas|on board|on wood|wood pulp|paperboard|"
    r"jpg|jpeg|png|canvas)\b.*$",
    re.I,
)


def norm(s):
    s = unicodedata.normalize("NFKD", s or "")
    s = s.lower().replace("'", "").replace("'", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    s = re.sub(r"\b(lawren|harris|stewart|the|a|an|of|by)\b", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def clean_title(t):
    t = re.sub(r"\s+", " ", t or "")
    t = re.sub(r"[\"“”'`]+", "", t).strip(" ~")
    t = re.sub(r"\s*[-–|]\s*Lawren.*$", "", t, flags=re.I)
    t = re.sub(r"\s+by Lawren.*$", "", t, flags=re.I)
    t = re.sub(r"\s+oil\s*on.*$", "", t, flags=re.I)
    t = re.sub(r"\s+oilon.*$", "", t, flags=re.I)
    t = MEDIUM_TAIL.sub("", t).strip(" -–,~")
    t = re.sub(r"\s+\(c\.?$", "", t).strip()
    # Auction / aggregator decoration: "Lawren Stewart Harris <Title> (1922) MutualArt".
    t = re.sub(r"^(Lawren|Lauren)\s+(Stewart\s+|Phillips\s+|S\.\s*)?Harris\s+(?=\S)", "", t, flags=re.I)
    t = re.sub(r"\s+(MutualArt|Artwork performance.*)$", "", t, flags=re.I)
    t = re.sub(r"\s*\((?:ca\.?|c\.)\s*$", "", t, flags=re.I)
    t = re.sub(r"\s*~\s*\d{4}$", "", t)
    t = re.sub(r"\s*\((?:ca\.?\s*)?\d{4}(?:-\d{2,4})?\)$", "", t)
    t = re.sub(r"(?<![,\d])\s+\d{4}-\d{4}$", "", t).strip(" ,-")
    # Pinterest board suffixes that leak into scraped titles.
    t = re.sub(r"[\s,]*(Canadian art,\s*)?Group of seven (paintings|artists)[\s,]*", " ", t, flags=re.I).strip(" ,-")
    t = re.sub(r"\s+Fine Art$", "", t).strip()
    return t


def year_in(s):
    years = [int(y) for y in re.findall(r"\b(19[0-6]\d|190[4-9]|1970)\b", s or "")]
    years = [y for y in years if 1904 <= y <= 1970]
    return (years[0], years[1] if len(years) > 1 and years[1] != years[0] else None) if years else (None, None)


def is_work(r):
    t = clean_title(r.get("title") or "")
    if not t or len(t) < 3 or len(t) > 90:
        return False
    if JUNK.search(t) or JUNK.search(r.get("title") or ""):
        return False
    if re.match(r"^(on|in) [a-z]", t, re.I):
        return False
    if re.match(r"^(lawren|lauren|emily)\b", t, re.I):
        return False
    if re.search(r"\bLawren\b", t) and len(t.split()) > 4:
        return False
    if re.search(r"\b(yearbook|four decades|group of painters|contemporaries|urban scenes|north by west|winston)\b", t, re.I):
        return False
    if re.fullmatch(r"Q\d+", t):
        return False
    if re.search(r"studio|o\.s\.a|%C3|musee d|musée d", t, re.I):
        return False
    if re.fullmatch(r"(January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}", t):
        return False
    if len(t.split()) > 10 and not r.get("collection"):
        return False
    y = r.get("year")
    if y is not None and not (1904 <= int(y) <= 1970):
        return False
    if t.endswith(".") or t.count(",") > 2:
        return False
    conf = r.get("confidence") or "medium"
    srcs = " ".join(r.get("sources") or []).lower()
    named = any(k in srcs for k in ("wikidata", "wikiart", "commons.wikimedia", "mcmichael", "gallery.ca"))
    if conf == "low" and not named and not r.get("collection"):
        return False
    if GENERIC.match(norm(t)) and not y:
        return False
    return True


def load_works():
    if not INDEX.exists():
        return []
    raw = [json.loads(l) for l in INDEX.read_text().splitlines() if l.strip()]
    kept = []
    for r in raw:
        if not is_work(r):
            continue
        title = clean_title(r["title"])
        year = r.get("year")
        year = int(year) if year else None
        kept.append({
            "title": title,
            "norm": norm(title),
            "year": year,
            "collection": r.get("collection") or None,
            "inventory_no": r.get("inventory_no") or None,
            "sources": [u for u in (r.get("sources") or []) if isinstance(u, str) and u.startswith("http")][:4],
            "medium": r.get("medium") or None,
        })
    # merge same title + year; fold undated into a single dated twin
    by_key = {}
    for w in kept:
        key = (w["norm"], w["year"], norm(w["collection"] or ""))
        prev = by_key.get(key)
        if prev:
            if not prev["collection"] and w["collection"]:
                prev["collection"] = w["collection"]
            prev["sources"] = list(dict.fromkeys(prev["sources"] + w["sources"]))[:4]
            if not prev["inventory_no"]:
                prev["inventory_no"] = w["inventory_no"]
        else:
            by_key[key] = w
    works = list(by_key.values())
    dated = {}
    for w in works:
        if w["year"]:
            dated.setdefault(w["norm"], []).append(w)
    merged = []
    for w in works:
        twins = dated.get(w["norm"]) or []
        if w["year"] is None and len(twins) == 1 and not w["collection"]:
            twins[0]["sources"] = list(dict.fromkeys(twins[0]["sources"] + w["sources"]))[:4]
            continue
        merged.append(w)
    merged.sort(key=lambda w: ((w["year"] or 9999), w["title"]))
    for i, w in enumerate(merged, 1):
        w["id"] = f"w{i:04d}"
    return merged


def load_meta():
    by_path = {}
    for f in DATA.rglob("metadata.jsonl"):
        for line in f.read_text(errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            lp = r.get("local_path")
            if not lp:
                continue
            key = str(Path(lp).as_posix()).lstrip("./")
            if key.startswith("data/"):
                key = key[5:]
            by_path[key] = r
    return by_path


def source_of(rel: Path):
    parts = rel.parts
    if "mcmichael" in parts:
        return "McMichael"
    if "heffel" in parts:
        return "Heffel"
    if "wikiart" in parts:
        return "WikiArt"
    if "flickr-com" in parts:
        return "Flickr"
    if "commons" in parts or "commons_deep" in parts:
        return "Commons"
    if "auctions" in parts:
        return "Auction"
    if "canada_museums" in parts or "intl_museums" in parts:
        return "Museum"
    if "archives" in parts:
        return "Archive"
    if "web_search" in parts:
        dom = next((p for p in parts if p.endswith("-com") or p.endswith("-org")), "Web")
        return dom.replace("-", ".")
    return "Folder"


def rank(src):
    order = ["McMichael", "Museum", "WikiArt", "Commons", "Archive", "Heffel", "Auction"]
    return order.index(src) if src in order else 50


def title_from_name(name):
    stem = Path(name).stem
    stem = re.sub(r"-(jpg|jpeg|png|webp)$", "", stem, flags=re.I)
    stem = re.sub(r"^(lawren-s?-?harris-|decorative-landscape-by-lawren-harris-)", "", stem)
    y, _ = year_in(stem.replace("-", " "))
    stem = re.sub(r"-?\b(19[0-6]\d|1970)\b.*$", "", stem)
    stem = re.sub(r"-\d{3,}$", "", stem)
    title = clean_title(stem.replace("-", " "))
    return title, y


def collect_images(meta):
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    images = []
    for p in DATA.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        if p.stat().st_size < 8_000:
            continue
        rel = p.relative_to(DATA)
        try:
            with Image.open(p) as im:
                w, h = im.size
        except Exception:
            continue
        if w < 80 or h < 80:
            continue
        m = meta.get(rel.as_posix(), {})
        title = clean_title(m.get("title") or "")
        year = m.get("year")
        year = int(year) if isinstance(year, int) or (isinstance(year, str) and str(year).isdigit()) else None
        if PHOTO.search(title) or PHOTO.search(p.name) or re.search(r"winston", p.name, re.I):
            continue
        if not title or title.lower() in {"untitled", "image", "img"}:
            guessed, gy = title_from_name(p.name)
            title = title or guessed
            year = year or gy
        else:
            _, gy = title_from_name(p.name)
            if year is None:
                year = gy
        y2 = None
        if year is None:
            year, y2 = year_in(p.name.replace("-", " "))
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        images.append({
            "src": "../data/" + rel.as_posix(),
            "title": title or None,
            "year": year,
            "year_end": y2,
            "w": w,
            "h": h,
            "aspect": round(w / h, 4),
            "source": source_of(rel),
            "page": m.get("page_url"),
            "collection": m.get("collection"),
            "medium": m.get("medium"),
            "sha": digest,
            "pixels": w * h,
            "rank": rank(source_of(rel)),
        })
    return images


def title_score(a, b):
    if not a or not b:
        return 0
    score = fuzz.token_sort_ratio(a, b)
    shorter, longer = sorted((len(a), len(b)))
    if longer and shorter / longer < 0.55:
        score -= 18
    return score


# Photos of Harris himself. "Portrait of <sitter>" is a genuine Harris work
# (e.g. Portrait of Thoreau MacDonald), so only portraits OF Harris are skipped.
# "Group of Seven artists" is a Pinterest board suffix, not evidence of a photo.
PHOTO = re.compile(
    r"\b(at his studio|o\.s\.a|hanging committee"
    r"|portrait of (lawren|lauren|l\.?\s?s\.?\s)\s?(stewart\s)?(s\.?\s)?harris"
    r"|(lawren|lauren) (s\.? )?harris (in|at|with|portrait|photograph))\b",
    re.I,
)


def match(images, works):
    for img in images:
        it = norm(img["title"] or "")
        iy = img["year"]
        generic = bool(GENERIC.match(it))
        cands = []
        for w in works:
            if generic or GENERIC.match(w["norm"]):
                if iy and w["year"] and iy == w["year"] and it == w["norm"]:
                    cands.append((100, w))
                continue
            sc = title_score(it, w["norm"])
            wy = w["year"]
            if iy and wy:
                d = abs(iy - wy)
                if d > 1 or (d == 1 and sc < 99) or (d == 0 and sc < 88):
                    continue
            elif sc < 96:
                continue
            cands.append((sc, w))
        cands.sort(key=lambda c: -c[0])
        best = None
        if cands:
            top = cands[0][0]
            near = [w for sc, w in cands if sc >= top - 2]
            years = {w["year"] for w in near}
            # Undated file, several canvases share the title: don't guess.
            if iy is None and len(years) > 1:
                best = None
            else:
                best = cands[0][1]
        img["work_id"] = best["id"] if best else None
        img["match"] = cands[0][0] if best else 0


def add_folder_works(images, works):
    """Images with a real title that matched nothing become extra checklist rows."""
    buckets = {}
    for im in images:
        if im["work_id"]:
            continue
        title = clean_title(im["title"] or "")
        n = norm(title)
        if not n or PHOTO.search(title):
            continue
        if JUNK.search(title) or re.match(r"^(lawren|lauren)\b", title, re.I):
            continue
        if re.search(r"[\u0400-\u04ff]|exhibition|for sale|^\d+$|^the \d{4}s", title, re.I):
            continue
        if GENERIC.match(n) or len(n) < 6:
            # "Untitled", "Abstract", "Abstraction": real museum items. Keep them as
            # their own rows, told apart by medium, but only when a source gave
            # us a medium, year or collection to go on.
            if not (im.get("medium") or im["year"] or im.get("collection") or re.search(r"wikiart|museum|mcmichael|gallery", im["source"] or "", re.I)):
                continue
            medium = (im.get("medium") or "").strip()
            title = f"{title} ({medium})" if medium else title
            n = norm(title)
            key = (n, im["year"], im["sha"] if not im["year"] else "")
        else:
            key = (n, im["year"], "")
        bucket = buckets.setdefault(key, {"title": title, "members": []})
        bucket["members"].append(im)
    n0 = len(works)
    for (n, year, _), bucket in sorted(buckets.items(), key=lambda kv: ((kv[0][1] or 9999), kv[0][0])):
        title, members = bucket["title"], bucket["members"]
        w = {
            "id": f"e{n0 + 1:04d}",
            "title": title, "norm": n, "year": year,
            "collection": next((m["collection"] for m in members if m.get("collection")), None),
            "inventory_no": None, "sources": [], "medium": None,
            "extra": True,
        }
        n0 += 1
        for m in members:
            m["work_id"] = w["id"]
        works.append(w)
    return works


def choose_primary(images):
    groups = {}
    for img in images:
        key = img["work_id"] or ("sha:" + img["sha"])
        groups.setdefault(key, []).append(img)
    for members in groups.values():
        members.sort(key=lambda im: (-im["pixels"], im["rank"]))
        for i, im in enumerate(members):
            im["primary"] = i == 0
            im["copies"] = len(members)


def main():
    works = load_works()
    images = collect_images(load_meta())
    match(images, works)
    works = add_folder_works(images, works)
    choose_primary(images)
    held = {}
    for im in images:
        if im["work_id"] and im["primary"]:
            held.setdefault(im["work_id"], []).append(im["src"])
    out_works = []
    for w in works:
        ids = held.get(w["id"], [])
        out_works.append({
            "id": w["id"], "title": w["title"], "year": w["year"],
            "collection": w["collection"], "inventory_no": w["inventory_no"],
            "sources": w["sources"], "medium": w["medium"],
            "status": "have" if ids else "missing",
            "extra": bool(w.get("extra")),
            "images": ids[:6],
        })
    out_images = []
    for i, im in enumerate(sorted(images, key=lambda x: (x["year"] or 9999, x["title"] or "")), 1):
        out_images.append({
            "id": f"i{i:04d}", "src": im["src"], "title": im["title"], "year": im["year"],
            "w": im["w"], "h": im["h"], "aspect": im["aspect"], "source": im["source"],
            "page": im["page"], "collection": im["collection"], "medium": im["medium"],
            "work_id": im["work_id"], "primary": im["primary"], "copies": im["copies"],
            "sha": im["sha"][:12],
        })
    have = sum(1 for w in out_works if w["status"] == "have")
    unidentified = sum(1 for im in out_images if im["primary"] and not im["work_id"])
    catalog = {
        "unidentified": unidentified,
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "index_name": "Wikidata + WikiArt + Commons, cleaned",
        "note": (
            "No complete catalogue raisonné of Lawren Harris is published in these sources. "
            "This checklist is the located works (museum records, WikiArt, Commons titles) "
            "after biography lines and exhibition titles were removed. An empty box means no "
            "image in this folder matched that work — not that the painting does not exist. "
            "Harris's oil sketches in private collections are mostly absent from the index."
        ),
        "works": out_works,
        "images": out_images,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("window.CATALOG = " + json.dumps(catalog, ensure_ascii=False) + ";\n")
    print(f"works {len(out_works)}  have {have}  missing {len(out_works) - have}")
    print(f"images {len(out_images)}  primary {sum(1 for i in out_images if i['primary'])}  unidentified {unidentified}")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
