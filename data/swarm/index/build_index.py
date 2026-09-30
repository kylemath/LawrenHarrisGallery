#!/usr/bin/env python3
"""Build deduplicated Lawren S. Harris works index from fetched raw sources."""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

DIR = Path(__file__).resolve().parent
RAW = DIR / "raw"
ROOT = DIR.parent.parent.parent  # LaurenHarris/

# Exclude son Lawren P. Harris signals
SON_HINTS = re.compile(
    r"lawren\s*p\.?\s*harris|lawren\s+phillips\s+harris|\(1910.?1994\)",
    re.I,
)

JUNK_TITLE = re.compile(
    r"^\s*$|"
    r"was born|october\s+23|ll\.?\s*d\.?|"
    r"harris to carr|hanging committee|ontario society of artists|"
    r"^(the )?(idea of north|higher states|canadian encyclopedia|essay on)|"
    r"international exhibition|"
    r"where the universe sings|"
    r"click here|read more|references|external link|edit section|"
    r"wikipedia|subscribe|newsletter|"
    r"^\(?\s*188[0-9]|"
    r"\bby lawren harris\b$",
    re.I,
)


def is_plausible_work_title(title: str) -> bool:
    t = norm_title(title)
    if len(t) < 3 or len(t) > 140:
        return False
    if SON_HINTS.search(t) or JUNK_TITLE.search(t):
        return False
    # too sentence-like
    if t.count(" ") > 14 and ("," in t or t.endswith(".")):
        return False
    if re.search(r"\b(was|were|born|died|lived|moved|became)\b", t, re.I):
        return False
    low = t.lower()
    if low in {"lawren harris", "lawren s. harris", "lawren stewart harris", "harris"}:
        return False
    return True


def norm_title(t: str) -> str:
    if not t:
        return ""
    s = t.strip()
    s = re.sub(r"\s+", " ", s)
    # strip catalog-number style suffixes: LSH 21, No. 7, #98 at end sometimes kept as title
    s = s.replace("\u2013", "-").replace("\u2014", "-").replace("\u2212", "-")
    s = s.replace("\u2018", "'").replace("\u2019", "'").replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+by\s+lawren\s+(?:s\.?\s+)?harris\b.*$", "", s, flags=re.I)
    # remove trailing parenthetical inventory-ish: (NGC 3708) etc
    s = re.sub(r"\s*\((?:NGC|AGO|acc\.?|inv\.?|cat\.?)[^)]*\)\s*$", "", s, flags=re.I)
    s = re.sub(r"\s*[-–—]\s*(?:oil on|canvas|board).*$", "", s, flags=re.I)
    s = s.strip(" .,-;:\"'")
    # trailing ~ from Commons filenames
    s = s.rstrip("~").strip()
    return s


def title_key(t: str) -> str:
    s = norm_title(t).lower()
    s = re.sub(r"[^\w\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_year(raw: Any) -> tuple[Optional[int], Optional[str]]:
    if raw is None:
        return None, None
    if isinstance(raw, int):
        if 1885 < raw <= 1975:  # exclude birth year false positives as sole year
            return raw, str(raw)
        if raw == 1885:
            return None, None
        if 1880 <= raw <= 1975:
            return raw, str(raw)
        return None, None
    s = str(raw).strip()
    if not s or s in ("?", "n.d.", "nd", "unknown"):
        return None, None
    # ranges
    m = re.search(r"(19\d{2})\s*[-–—/]\s*(19\d{2}|\d{2})", s)
    if m:
        y1 = int(m.group(1))
        y2s = m.group(2)
        y2 = int(y2s) if len(y2s) == 4 else 1900 + int(y2s)
        return y1, f"{y1}-{y2}"
    m = re.search(r"\b(18\d{2}|19\d{2}|1970)\b", s)
    if m:
        y = int(m.group(1))
        if y == 1885:
            return None, s
        if 1880 <= y <= 1975:
            return y, s
    # ISO dates from wikidata
    m = re.match(r"^(\d{4})-", s)
    if m:
        y = int(m.group(1))
        if y == 1885:
            return None, s
        if 1880 <= y <= 1975:
            return y, s
    return None, s if s else None


def empty_work() -> dict:
    return {
        "title": "",
        "alt_titles": [],
        "year": None,
        "year_range": None,
        "medium": None,
        "dimensions": None,
        "collection": None,
        "inventory_no": None,
        "wikidata_id": None,
        "sources": [],
        "confidence": "low",
        "notes": None,
    }


def merge_into(bucket: dict, new: dict) -> None:
    if not bucket["title"] and new.get("title"):
        bucket["title"] = norm_title(new["title"])
    elif new.get("title"):
        nt = norm_title(new["title"])
        if nt and nt.lower() != bucket["title"].lower():
            if nt not in bucket["alt_titles"]:
                bucket["alt_titles"].append(nt)
    for at in new.get("alt_titles") or []:
        at = norm_title(at)
        if at and at.lower() != bucket["title"].lower() and at not in bucket["alt_titles"]:
            bucket["alt_titles"].append(at)
    # year: prefer non-null; tolerate +-1 by keeping existing if close
    ny, nr = new.get("year"), new.get("year_range")
    if bucket["year"] is None and ny is not None:
        bucket["year"] = ny
        bucket["year_range"] = nr
    elif ny is not None and bucket["year"] is not None:
        if abs(bucket["year"] - ny) <= 1:
            # keep earlier year as primary if range
            if nr and not bucket["year_range"]:
                bucket["year_range"] = nr
        # if differ by >1, keep alt note
        elif abs(bucket["year"] - ny) > 1:
            note = f"alt year {ny}"
            bucket["notes"] = (bucket["notes"] + "; " + note) if bucket["notes"] else note
    for field in ("medium", "dimensions", "collection", "inventory_no", "wikidata_id"):
        if not bucket.get(field) and new.get(field):
            bucket[field] = new[field]
    for s in new.get("sources") or []:
        if s and s not in bucket["sources"]:
            bucket["sources"].append(s)
    # confidence max
    order = {"low": 0, "medium": 1, "high": 2}
    if order.get(new.get("confidence", "low"), 0) > order.get(bucket.get("confidence", "low"), 0):
        bucket["confidence"] = new["confidence"]
    if new.get("notes"):
        bucket["notes"] = (
            (bucket["notes"] + "; " + new["notes"]) if bucket["notes"] else new["notes"]
        )


def find_bucket(index: dict, title: str, year: Optional[int]) -> Optional[str]:
    tk = title_key(title)
    if not tk:
        return None
    candidates = []
    for k, w in index.items():
        if title_key(w["title"]) == tk or tk in {title_key(a) for a in w["alt_titles"]}:
            candidates.append(k)
        elif title_key(w["title"]) and (
            title_key(w["title"]).startswith(tk) or tk.startswith(title_key(w["title"]))
        ):
            # only if lengths close
            if abs(len(title_key(w["title"])) - len(tk)) <= 8:
                candidates.append(k)
    if not candidates:
        return None
    if year is None:
        return candidates[0]
    best = None
    best_diff = 999
    for k in candidates:
        wy = index[k]["year"]
        if wy is None:
            if best is None:
                best = k
            continue
        d = abs(wy - year)
        if d <= 1 and d < best_diff:
            best, best_diff = k, d
    return best if best is not None else candidates[0]


def add_work(index: dict, work: dict) -> None:
    title = norm_title(work.get("title") or "")
    title = title.strip('"').strip("'").strip()
    if not is_plausible_work_title(title):
        return
    year = work.get("year")
    # drop impossible painting years (birth date leaks)
    if year is not None and (year < 1900 or year > 1970):
        # allow 1900s sketches; Harris started ~1908
        if year < 1905 or year > 1970:
            year = None
            work = {**work, "year": None}
    coll = work.get("collection")
    if coll and str(coll).lower() in {"flickr", "unknown", "private", "n/a"}:
        work = {**work, "collection": None if str(coll).lower() in {"flickr", "unknown", "n/a"} else coll}
        if str(coll).lower() == "private":
            work = {**work, "collection": "private collection"}
    key = find_bucket(index, title, year)
    if key is None:
        key = f"{title_key(title)}|{year or 'x'}"
        # avoid collision
        base = key
        n = 2
        while key in index:
            key = f"{base}#{n}"
            n += 1
        index[key] = empty_work()
        index[key]["title"] = title
        index[key]["year"] = year
        index[key]["year_range"] = work.get("year_range")
    merge_into(index[key], {**work, "title": title, "year": year})


def load_wikiart(index: dict) -> int:
    path = RAW / "wikiart_pages.json"
    n = 0
    if path.exists():
        pages = json.loads(path.read_text(encoding="utf-8"))
        for pdata in pages.values():
            for p in pdata.get("Paintings") or []:
                y, yr = parse_year(p.get("year"))
                url = "https://www.wikiart.org" + (p.get("paintingUrl") or "")
                add_work(
                    index,
                    {
                        "title": p.get("title"),
                        "year": y,
                        "year_range": yr,
                        "sources": [url],
                        "confidence": "high",
                    },
                )
                n += 1
    # HTML text-list: "- Title, 1926"
    html_path = RAW / "wikiart_text_list.html"
    if html_path.exists():
        html = html_path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(
            r"<li[^>]*>\s*<a[^>]*>\s*([^<]+?)\s*</a>\s*,\s*([^<]*?)\s*</li>",
            html,
            re.I,
        ):
            title, year_raw = m.group(1).strip(), m.group(2).strip()
            y, yr = parse_year(year_raw)
            add_work(
                index,
                {
                    "title": title,
                    "year": y,
                    "year_range": yr,
                    "sources": ["https://www.wikiart.org/en/lawren-harris/all-works/text-list"],
                    "confidence": "high",
                },
            )
            n += 1
        # fallback plain "- Title, year"
        for m in re.finditer(
            r"(?:^|>)\s*[-•]\s*([^,<]{3,120}?),\s*(c\.?\s*)?(\?|18\d{2}|19\d{2})",
            html,
            re.M,
        ):
            y, yr = parse_year(m.group(3))
            add_work(
                index,
                {
                    "title": m.group(1).strip(),
                    "year": y,
                    "year_range": yr,
                    "sources": ["https://www.wikiart.org/en/lawren-harris/all-works/text-list"],
                    "confidence": "high",
                },
            )
            n += 1
    # sibling swarm metadata
    for rel in (
        "data/swarm/wikiart/metadata.jsonl",
        "data/swarm/archives/metadata.jsonl",
        "data/swarm/web_search/metadata.jsonl",
    ):
        sib = ROOT / rel
        if not sib.exists():
            continue
        for line in sib.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            title = rec.get("title") or rec.get("work_title") or rec.get("name")
            if not title:
                continue
            y, yr = parse_year(rec.get("year") or rec.get("date_raw") or rec.get("date"))
            src = rec.get("page_url") or rec.get("source_url") or rec.get("url") or f"file:{rel}"
            add_work(
                index,
                {
                    "title": title,
                    "year": y,
                    "year_range": yr,
                    "medium": rec.get("medium"),
                    "dimensions": rec.get("dimensions"),
                    "collection": rec.get("collection"),
                    "sources": [src] if src else [],
                    "confidence": "high" if "wikiart" in rel else "medium",
                },
            )
            n += 1
    return n


def load_wikidata(index: dict) -> int:
    path = RAW / "wikidata_works.json"
    if not path.exists():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    n = 0
    rows = data.get("by_creator") or []
    if isinstance(rows, dict):
        return 0
    for b in rows:
        def val(k):
            return (b.get(k) or {}).get("value")

        item = val("item") or ""
        qid = item.rsplit("/", 1)[-1] if item else None
        label = val("itemLabel") or ""
        if SON_HINTS.search(label):
            continue
        y, yr = parse_year(val("inception"))
        h, w = val("height"), val("width")
        dims = None
        if h and w:
            try:
                dims = f"{float(h):g} x {float(w):g} cm"
            except Exception:
                dims = f"{h} x {w}"
        add_work(
            index,
            {
                "title": label,
                "year": y,
                "year_range": yr,
                "medium": val("mediumLabel"),
                "dimensions": dims,
                "collection": val("collectionLabel"),
                "inventory_no": val("inventory"),
                "wikidata_id": qid,
                "sources": [item] if item else ["https://www.wikidata.org/wiki/Q3106117"],
                "confidence": "high",
            },
        )
        n += 1
    return n


def extract_titles_from_html(html: str) -> list[tuple[str, Optional[int], Optional[str]]]:
    """Heuristic: italic/quoted titles near years; list items; figure captions."""
    found = []
    # <i>Title</i> (1926) or <em>
    for m in re.finditer(
        r"<(?:i|em|cite)[^>]*>\s*([^<]{3,120}?)\s*</(?:i|em|cite)>\s*[,\s(]*(?:c\.?\s*)?(18\d{2}|19\d{2})(?:\s*[-–—]\s*(18\d{2}|19\d{2}))?",
        html,
        re.I,
    ):
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        y = int(m.group(2))
        yr = f"{m.group(2)}-{m.group(3)}" if m.group(3) else m.group(2)
        found.append((title, y, yr))
    # list style: Title, 1926
    for m in re.finditer(
        r"(?:<li[^>]*>|>)\s*(?:<[^>]+>)*\s*([A-Z][^<>]{2,100}?),\s*(?:c\.?\s*)?(18\d{2}|19\d{2})",
        html,
    ):
        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        title = re.sub(r"\s+", " ", title)
        if "Harris" in title and len(title) < 20:
            continue
        found.append((title, int(m.group(2)), m.group(2)))
    return found


def load_html_sources(index: dict) -> int:
    n = 0
    for path in RAW.glob("*.html"):
        html = path.read_text(encoding="utf-8", errors="replace")
        if SON_HINTS.search(html[:2000]) and "Lawren S" not in html[:3000]:
            # still ok if page is about S. Harris primarily
            pass
        src_url = None
        if path.name.startswith("wikipedia_en"):
            src_url = "https://en.wikipedia.org/wiki/Lawren_Harris"
        elif path.name.startswith("wikipedia_fr"):
            src_url = "https://fr.wikipedia.org/wiki/Lawren_Harris"
        elif path.name.startswith("wikipedia_de"):
            src_url = "https://de.wikipedia.org/wiki/Lawren_Harris"
        elif path.name.startswith("wikipedia_es"):
            src_url = "https://es.wikipedia.org/wiki/Lawren_Harris"
        elif path.name.startswith("aci_"):
            src_url = "https://www.aci-iac.ca/art-books/lawren-harris/"
        elif path.name.startswith("tce_"):
            src_url = "https://www.thecanadianencyclopedia.ca/en/article/lawren-stewart-harris"
        elif path.name.startswith("ngc_"):
            src_url = "https://www.gallery.ca/collection/artist/lawren-s-harris"
        else:
            src_url = f"file:{path.name}"
        for title, y, yr in extract_titles_from_html(html):
            if SON_HINTS.search(title):
                continue
            # filter junk
            tl = title.lower()
            if any(
                x in tl
                for x in (
                    "click here",
                    "read more",
                    "group of seven",
                    "wikipedia",
                    "edit section",
                    "references",
                    "external link",
                )
            ):
                continue
            add_work(
                index,
                {
                    "title": title,
                    "year": y,
                    "year_range": yr,
                    "sources": [src_url],
                    "confidence": "medium" if "wikipedia" in path.name or "aci" in path.name else "low",
                },
            )
            n += 1
        # ACI key-works specific: look for artwork titles in headings
        for m in re.finditer(
            r"<h[1-4][^>]*>\s*(?:<[^>]+>)*\s*([^<]{3,100}?)\s*<",
            html,
            re.I,
        ):
            title = re.sub(r"\s+", " ", m.group(1)).strip()
            if re.search(r"\b(19\d{2})\b", title):
                y, yr = parse_year(title)
                title2 = re.sub(r",?\s*c?\.?\s*19\d{2}.*", "", title).strip()
                if title2:
                    add_work(
                        index,
                        {
                            "title": title2,
                            "year": y,
                            "year_range": yr,
                            "sources": [src_url],
                            "confidence": "medium",
                        },
                    )
                    n += 1
    return n


def load_commons(index: dict) -> int:
    path = RAW / "commons_files.json"
    n = 0
    if not path.exists():
        # also local commons filenames
        local = ROOT / "data" / "images" / "commons"
        if local.exists():
            for f in local.iterdir():
                n += _from_filename(index, f.name, f"local:{f.name}")
        return n
    data = json.loads(path.read_text(encoding="utf-8"))
    for entry in data.get("search") or []:
        title = entry.get("title") or ""
        # File:Foo.jpg
        fname = title.replace("File:", "")
        n += _from_filename(
            index, fname, f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}"
        )
    for entry in data.get("category") or []:
        title = entry.get("title") or ""
        fname = title.replace("File:", "")
        n += _from_filename(
            index, fname, f"https://commons.wikimedia.org/wiki/{title.replace(' ', '_')}"
        )
    local = ROOT / "data" / "images" / "commons"
    if local.exists():
        for f in local.iterdir():
            n += _from_filename(index, f.name, f"local:data/images/commons/{f.name}")
    return n


def _from_filename(index: dict, fname: str, source: str) -> int:
    base = re.sub(r"\.(jpe?g|png|gif|webp|tif{1,2})$", "", fname, flags=re.I)
    base = base.replace("_", " ").replace("-", " ")
    if not re.search(r"lawren\s*harris", base, re.I):
        # still try if in harris commons folder naming
        if "harris" not in base.lower():
            return 0
    if SON_HINTS.search(base) or re.search(r"lawren\s*p", base, re.I):
        return 0
    # strip artist prefix
    t = re.sub(r"^.*?\blawren\s+(?:s\.?\s+)?harris\b\s*", "", base, flags=re.I)
    t = re.sub(r"\bby\s+lawren\s+harris\b.*", "", t, flags=re.I)
    t = re.sub(r"\bjpg\b|\bjpeg\b|\bpng\b", "", t, flags=re.I)
    # extract year
    y, yr = parse_year(t)
    # remove medium/size tails
    t = re.sub(
        r"\b(oil on|canvas|wood|pulp|board|paperboard|paper|panel)\b.*$",
        "",
        t,
        flags=re.I,
    )
    t = re.sub(r"\b(18\d{2}|19\d{2})(\s*[-–—]\s*(18\d{2}|19\d{2}))?\b", "", t)
    t = re.sub(r"\b\d{3,5}\b", "", t)  # accession fragments
    t = re.sub(r"\s+", " ", t).strip(" -_,.")
    if len(t) < 3:
        return 0
    add_work(
        index,
        {
            "title": t.title() if t.islower() else t,
            "year": y,
            "year_range": yr,
            "sources": [source],
            "confidence": "low",
            "notes": f"from filename: {fname}",
        },
    )
    return 1


# Curated / catalogue seeds (WikiArt list, ACI key works, Idea of North / Higher States / NGC staples)
# tuple: title, year, medium, dims, collection, source
CURATED = [
    ("A Row of Houses, Wellington Street (Street Painting I)", 1910, "oil on canvas", None, "Art Gallery of Ontario", "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Winter Sunrise", 1913, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("The Corner Store", 1912, "oil on canvas", None, None, "https://en.wikipedia.org/wiki/Lawren_Harris"),
    ("Buildings on the Hill", 1910, None, None, None, "catalogue: Lawren Harris Painter's Progress"),
    ("Houses on Wellington Street", 1910, None, None, None, "catalogue: Lawren Harris Painter's Progress"),
    ("In the Ward, Toronto", 1919, None, None, None, "https://en.wikipedia.org/wiki/Lawren_Harris"),
    ("Autumn Batchewana", 1918, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Winter Landscape with Pink House", 1918, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Woods, Algoma", 1918, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Algoma Hill", 1920, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Algoma Country", 1920, "oil on canvas", None, None, "https://commons.wikimedia.org/wiki/Category:Lawren_Harris"),
    ("Algoma Sketch XXXI", 1920, "oil on wood", None, None, "https://commons.wikimedia.org/wiki/Category:Lawren_Harris"),
    ("Algoma Sketch Autumn", 1920, "oil on wood", None, None, "https://commons.wikimedia.org/wiki/Category:Lawren_Harris"),
    ("Algoma Sketch LXXXIV", 1922, "oil on paperboard", None, None, "https://commons.wikimedia.org/wiki/Category:Lawren_Harris"),
    ("Decorative Landscape", 1917, "oil on canvas", None, "Musée d'Orsay", "https://commons.wikimedia.org/wiki/Category:Lawren_Harris"),
    ("Return from Church", 1919, None, None, "National Gallery of Canada", "https://www.gallery.ca/"),
    ("Elevator Court, Halifax", 1921, "oil on canvas", None, None, "https://en.wikipedia.org/wiki/Lawren_Harris"),
    ("Hurdy Gurdy", 1921, None, None, None, "https://en.wikipedia.org/wiki/Lawren_Harris"),
    ("Winter in the Ward", 1920, None, None, None, "https://en.wikipedia.org/wiki/Lawren_Harris"),
    ("Above Lake Superior", 1922, "oil on canvas", None, "Art Gallery of Ontario", "https://www.aci-iac.ca/art-books/lawren-harris/"),
    ("Ice House, Coldwell, Lake Superior", 1923, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Afternoon Sun, Lake Superior", 1924, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Maligne Lake, Jasper Park", 1924, "oil on canvas", None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Pic Island, Lake Superior", 1924, "oil on canvas", None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Pine Tree and Red House, Winter City", 1924, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Red House", 1925, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Miners' Houses, Glace Bay", 1925, "oil on canvas", None, None, "https://en.wikipedia.org/wiki/Lawren_Harris"),
    ("Mountain Forms", 1926, "oil on canvas", None, None, "https://www.wikiart.org/en/lawren-harris/mountain-forms-1926"),
    ("Aftermath of Storm - Lake Superior Sketch XXXIV", 1926, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("North Shore, Lake Superior", 1926, "oil on canvas", "102.2 x 128.3 cm", "National Gallery of Canada", "https://www.gallery.ca/collection/artwork/north-shore-lake-superior-0"),
    ("From the North Shore, Lake Superior", 1927, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Lake and Mountains", 1928, "oil on canvas", None, "Art Gallery of Ontario", "https://www.aci-iac.ca/art-books/lawren-harris/"),
    ("Lake Superior", 1928, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Mountains and Lake", 1929, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Isolation Peak, Rocky Mountains", 1930, "oil on canvas", None, "Hart House, University of Toronto", "https://www.aci-iac.ca/art-books/lawren-harris/"),
    ("Mount Lefroy", 1930, "oil on canvas", None, "McMichael Canadian Art Collection", "https://www.aci-iac.ca/art-books/lawren-harris/"),
    ("Mount Thule, Bylot Island", 1930, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Nerke, Greenland", 1930, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Icebergs, Davis Strait", 1930, "oil on canvas", None, "McMichael Canadian Art Collection", "https://www.aci-iac.ca/art-books/lawren-harris/"),
    ("Bylot Island", 1930, "oil on canvas", None, None, "https://en.wikipedia.org/wiki/Lawren_Harris"),
    ("Greenland Mountains", 1930, None, None, "National Gallery of Canada", "https://www.gallery.ca/"),
    ("2 A.M., Buchanan Bay, Ellesmere Island", 1930, "oil on wood pulp board", None, None, "https://commons.wikimedia.org/wiki/Category:Lawren_Harris"),
    ("Baffin Island", 1931, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Winter Comes from the Arctic to the Temperate Zone", 1935, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Abstract Painting #98", 1938, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Hanover Abstract", 1938, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Abstract No. 7", 1939, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Abstract Painting #20", 1942, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("LSH 21", 1942, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Intimations", 1943, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Sketch Painted in Santa Fe, New Mexico", 1944, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("LSH 134", 1950, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Nature Rhythms", 1950, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Untitled", 1951, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Abstraction 30", 1955, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("LSH 83", 1957, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Calligraph Forming", 1958, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("The Spirit of Remote Hills", 1958, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Abstraction", 1964, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Abstract", None, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Shoreline", None, None, None, None, "https://www.wikiart.org/en/lawren-harris/all-works/text-list"),
    ("Untitled (Lake Superior)", 1923, "oil on board", None, None, "catalogue: The Idea of North (Hammer Museum 2016)"),
    ("Mount Robson", 1929, None, None, None, "catalogue: The Idea of North (Hammer Museum 2016)"),
    ("Lake Superior Sketch XXXVII", 1926, None, None, None, "catalogue: Lawren Harris Higher States"),
    ("Afternoon, Lake Superior", 1924, None, None, None, "catalogue: Lawren Harris Nature and Abstraction"),
    ("Snow II", 1915, None, None, None, "https://en.wikipedia.org/wiki/Lawren_Harris"),
    ("Toronto Street, Winter Morning", 1920, None, None, None, "https://en.wikipedia.org/wiki/Lawren_Harris"),
    ("Black Court, Halifax", 1921, None, None, None, "https://en.wikipedia.org/wiki/Lawren_Harris"),
    ("First Snow, North Shore of Lake Superior", 1923, None, None, "Vancouver Art Gallery", "https://www.aci-iac.ca/art-books/lawren-harris/"),
    ("Northern Lake", 1926, None, None, None, "catalogue: Lawren Harris A Painter's Progress"),
    ("Clouds, Lake Superior", 1923, None, None, None, "catalogue: The Idea of North"),
    ("Islands, Lake Superior", 1924, None, None, None, "catalogue: The Idea of North"),
    ("Mountain and Glacier", 1930, None, None, None, "catalogue: The Idea of North"),
    ("Arctic Tent Camp", 1930, None, None, None, "catalogue: The Idea of North"),
    ("South Shore, Baffin Island", 1931, None, None, None, "https://en.wikipedia.org/wiki/Lawren_Harris"),
    ("The Old Stump", 1926, None, None, None, "https://en.wikipedia.org/wiki/Lawren_Harris"),
    ("Bylot Island I", 1930, None, None, None, "https://en.wikipedia.org/wiki/Lawren_Harris"),
    ("Winter Landscape", 1915, None, None, None, "https://en.wikipedia.org/wiki/Lawren_Harris"),
    ("The Turning Earth", None, None, None, None, "catalogue: Higher States"),
    ("Church at Yuquot Village", 1929, None, None, None, "https://en.wikipedia.org/wiki/Lawren_Harris"),
    ("Study 'Lighthouse, Father Point'", 1929, None, None, "National Gallery of Canada", "https://www.gallery.ca/magazine/your-collection/higher-states-lawren-s-harris-and-his-north-american-contemporaries"),
    ("Abstract Sketch", 1936, None, None, "National Gallery of Canada", "https://www.gallery.ca/magazine/your-collection/higher-states-lawren-s-harris-and-his-north-american-contemporaries"),
    ("White Triangle", 1939, None, None, "National Gallery of Canada", "https://www.gallery.ca/magazine/your-collection/higher-states-lawren-s-harris-and-his-north-american-contemporaries"),
    ("The Earth, The Sun and the Moon", None, None, None, None, "catalogue: Higher States"),
    ("Abstraction 119", None, None, None, None, "catalogue: Higher States"),
    ("Pine Tree and Red House, Winter, City Painting II", 1924, None, None, None, "https://en.wikipedia.org/wiki/Lawren_Harris"),
]


def load_curated(index: dict) -> int:
    n = 0
    for title, year, medium, dims, coll, src in CURATED:
        add_work(
            index,
            {
                "title": title,
                "year": year,
                "year_range": str(year),
                "medium": medium,
                "dimensions": dims,
                "collection": coll,
                "sources": [src],
                "confidence": "high",
                "notes": "curated seed / cross-ref",
            },
        )
        n += 1
    return n


def main() -> None:
    index: dict[str, dict] = {}
    counts = {
        "wikiart": load_wikiart(index),
        "wikidata": load_wikidata(index),
        "html": load_html_sources(index),
        "commons": load_commons(index),
        "curated": load_curated(index),
    }
    works = list(index.values())
    # clean alt_titles / finalize
    for w in works:
        w["title"] = norm_title(w["title"])
        w["alt_titles"] = sorted(set(norm_title(a) for a in w["alt_titles"] if norm_title(a)))
        w["sources"] = sorted(set(s for s in w["sources"] if s))
        if not w.get("year_range") and w.get("year"):
            w["year_range"] = str(w["year"])

    works.sort(key=lambda w: (w["year"] is None, w["year"] or 0, w["title"].lower()))

    jsonl = DIR / "works_index.jsonl"
    with jsonl.open("w", encoding="utf-8") as f:
        for w in works:
            f.write(json.dumps(w, ensure_ascii=False) + "\n")

    csv_path = DIR / "works_index.csv"
    fields = [
        "title",
        "alt_titles",
        "year",
        "year_range",
        "medium",
        "dimensions",
        "collection",
        "inventory_no",
        "wikidata_id",
        "sources",
        "confidence",
        "notes",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for w in works:
            row = dict(w)
            row["alt_titles"] = "|".join(w["alt_titles"])
            row["sources"] = "|".join(w["sources"])
            writer.writerow(row)

    with_year = sum(1 for w in works if w["year"] is not None)
    with_coll = sum(1 for w in works if w.get("collection"))
    src_counter: Counter = Counter()
    for w in works:
        for s in w["sources"]:
            if "wikiart" in s:
                src_counter["wikiart"] += 1
            elif "wikidata" in s:
                src_counter["wikidata"] += 1
            elif "wikipedia" in s:
                src_counter["wikipedia"] += 1
            elif "aci-iac" in s:
                src_counter["aci"] += 1
            elif "commons" in s or s.startswith("local:"):
                src_counter["commons"] += 1
            elif "gallery.ca" in s:
                src_counter["ngc"] += 1
            elif "canadianencyclopedia" in s:
                src_counter["tce"] += 1
            else:
                src_counter["other"] += 1

    report = {
        "distinct_works": len(works),
        "pct_with_year": round(100.0 * with_year / max(len(works), 1), 1),
        "pct_with_collection": round(100.0 * with_coll / max(len(works), 1), 1),
        "raw_ingest_counts": counts,
        "source_hit_counts": dict(src_counter.most_common()),
        "paths": {"jsonl": str(jsonl), "csv": str(csv_path)},
    }
    (DIR / "build_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
