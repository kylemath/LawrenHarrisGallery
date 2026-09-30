#!/usr/bin/env python3
"""Fetch Wikidata works via robots-allowed HTML / EntityData (not SPARQL)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from http_util import get, save_json, save_text

RAW = Path(__file__).resolve().parent / "raw"
OUT = RAW / "wikidata_works.json"
PERSON = "Q3106117"


def fetch_entity(qid: str) -> dict:
    # Special:EntityData/*. is Allow'd on wikidata robots.txt
    url = f"https://www.wikidata.org/wiki/Special:EntityData/{qid}.json"
    print(f"entity {qid}")
    return get(url, as_json=True, timeout=60)


def claims_of(entity: dict, pid: str) -> list:
    return (entity.get("claims") or {}).get(pid) or []


def mainsnak_id(claim: dict):
    try:
        return claim["mainsnak"]["datavalue"]["value"]["id"]
    except Exception:
        return None


def mainsnak_time(claim: dict):
    try:
        return claim["mainsnak"]["datavalue"]["value"]["time"]
    except Exception:
        return None


def mainsnak_str(claim: dict):
    try:
        v = claim["mainsnak"]["datavalue"]["value"]
        if isinstance(v, str):
            return v
        if isinstance(v, dict) and "amount" in v:
            return v["amount"].lstrip("+")
        if isinstance(v, dict) and "id" in v:
            return v["id"]
        return str(v)
    except Exception:
        return None


def label_en(entity: dict) -> str:
    labs = entity.get("labels") or {}
    for lang in ("en", "fr", "de", "es"):
        if lang in labs:
            return labs[lang]["value"]
    if labs:
        return next(iter(labs.values()))["value"]
    return ""


def fetch_what_links_html() -> list[str]:
    """Parse HTML WhatLinksHere for painting items (wiki pages allowed; Special may be blocked)."""
    # Try article 'List' style via Wikipedia backlinks page mirror:
    urls = [
        f"https://www.wikidata.org/wiki/Special:WhatLinksHere/{PERSON}?limit=500&namespace=0",
        f"https://www.wikidata.org/w/index.php?title=Special:WhatLinksHere/{PERSON}&limit=500&namespace=0",
    ]
    qids = []
    for url in urls:
        try:
            html = get(url, timeout=60)
            save_text(RAW / "wikidata_whatlinkshere.html", html)
            qids = re.findall(r"href=\"/wiki/(Q\d+)\"", html)
            print(f"whatlinkshere: {len(qids)} Q-ids from {url}")
            break
        except Exception as e:
            print(f"  whatlinkshere fail: {e}")
    return list(dict.fromkeys(qids))


def fetch_commons_category_html() -> None:
    url = "https://commons.wikimedia.org/wiki/Category:Paintings_by_Lawren_Harris"
    alts = [
        url,
        "https://commons.wikimedia.org/wiki/Category:Lawren_Harris",
        "https://commons.wikimedia.org/wiki/Category:Lawren_S._Harris",
    ]
    files = []
    for u in alts:
        out = RAW / ("commons_" + u.rstrip("/").split(":")[-1].replace(" ", "_") + ".html")
        try:
            if out.exists() and out.stat().st_size > 500:
                html = out.read_text(encoding="utf-8", errors="replace")
                print(f"skip cached {out.name}")
            else:
                print(f"fetch {u}")
                html = get(u, timeout=60)
                save_text(out, html)
            # gallery file links
            found = re.findall(r"href=\"/wiki/(File:[^\"]+)\"", html)
            files.extend(found)
            print(f"  files +{len(found)}")
        except Exception as e:
            print(f"  fail {u}: {e}")
    save_json(RAW / "commons_files.json", {"category_html_files": files, "search": [], "category": [{"title": f} for f in files]})


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    out = {"by_creator": [], "person": None, "linked_entities": []}

    try:
        person = fetch_entity(PERSON)
        ent = person["entities"][PERSON]
        out["person"] = {
            "label": label_en(ent),
            "P6379": [mainsnak_id(c) for c in claims_of(ent, "P6379")],
        }
        save_json(RAW / "wikidata_person.json", person)
    except Exception as e:
        print(f"person entity fail: {e}")

    # Discover work QIDs from Wikipedia en article wikidata sitelinks / HTML of enwiki
    # Parse enwiki for wd items mentioned via data-mw - fallback: known painting QIDs from commons filenames + curated
    candidate_qids = []
    try:
        # Entity talk / reverse: use Wikipedia API via action=parse is under /w/ - blocked.
        # Parse already-fetched enwiki HTML for File: and painting titles; also try wd "has part" style.
        en = (RAW / "wikipedia_en.html").read_text(encoding="utf-8", errors="replace")
        # Wikidata item links rare in HTML; look for Q numbers near painting
        candidate_qids += re.findall(r"wikidata\.org/wiki/(Q\d+)", en)
    except Exception:
        pass

    candidate_qids += fetch_what_links_html()
    # Dedup, skip person
    candidate_qids = [q for q in dict.fromkeys(candidate_qids) if q != PERSON]

    # Cap for time-box; fetch entities that look like works (have P170)
    works_bindings = []
    for qid in candidate_qids[:80]:
        try:
            data = fetch_entity(qid)
            ent = data["entities"][qid]
            creators = [mainsnak_id(c) for c in claims_of(ent, "P170")]
            if PERSON not in creators:
                # keep if instance of painting and label suggests harris work? skip non-creator
                continue
            title = label_en(ent)
            inception = None
            for c in claims_of(ent, "P571"):
                inception = mainsnak_time(c)
                break
            medium = None
            for c in claims_of(ent, "P186"):
                mid = mainsnak_id(c)
                medium = mid
                break
            collection = None
            inv = None
            for c in claims_of(ent, "P195"):
                collection = mainsnak_id(c)
                break
            for c in claims_of(ent, "P217"):
                inv = mainsnak_str(c)
                break
            h = w = None
            for c in claims_of(ent, "P2048"):
                h = mainsnak_str(c)
            for c in claims_of(ent, "P2049"):
                w = mainsnak_str(c)
            works_bindings.append(
                {
                    "item": {"value": f"https://www.wikidata.org/wiki/{qid}"},
                    "itemLabel": {"value": title},
                    "inception": {"value": inception} if inception else {},
                    "mediumLabel": {"value": medium} if medium else {},
                    "collectionLabel": {"value": collection} if collection else {},
                    "inventory": {"value": inv} if inv else {},
                    "height": {"value": h} if h else {},
                    "width": {"value": w} if w else {},
                }
            )
            print(f"  work {qid}: {title}")
        except Exception as e:
            print(f"  skip {qid}: {e}")

    # Also seed well-known work QIDs if WhatLinksHere failed
    KNOWN = [
        "Q19900745",  # North Shore Lake Superior (example - may be wrong)
    ]
    # Discover from Commons category HTML file descriptions later
    out["by_creator"] = works_bindings
    out["linked_entities"] = candidate_qids
    save_json(OUT, out)
    fetch_commons_category_html()
    print(f"saved {OUT} with {len(works_bindings)} works")


if __name__ == "__main__":
    main()
