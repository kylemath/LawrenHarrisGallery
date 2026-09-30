#!/usr/bin/env python3
"""Merge every scraper output into one deduplicated dataset.

Inputs : data/metadata.jsonl and data/swarm/*/metadata.jsonl
         (optional) data/swarm/index/works_index.jsonl for labelling unlabelled images
Outputs: data/final/dataset.jsonl, data/final/dataset.csv, data/final/report.txt
         Nothing is deleted or moved: images stay where they were downloaded;
         `local_path` of each merged entry points at the best (largest) copy.

Dedup: exact sha256, then perceptual dHash (Hamming <= --hamming, and similar
aspect ratio) so the same painting at different resolutions/crops collapses.

Usage: source .venv/bin/activate && python merge.py [--hamming 5]
"""
import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from PIL import Image

PROJECT = Path(__file__).resolve().parent
DATA = PROJECT / "data"
OUT = DATA / "final"

# lower = more trusted for metadata when records conflict
PRIORITY = ["canada_museums", "intl_museums", "wikiart", "wikidata", "commons",
            "commons_deep", "archives", "auctions", "web_search"]
FIELDS = ["title", "year", "date_raw", "collection", "inventory_no", "medium",
          "dimensions", "genre", "style", "description", "license", "wikidata_id"]


def load_all():
    recs = []
    files = [DATA / "metadata.jsonl"] + sorted((DATA / "swarm").glob("*/metadata.jsonl"))
    for f in files:
        if not f.exists():
            continue
        group = f.parent.name if f.parent.name != "data" else "main"
        for line in f.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                r["_group"] = group
                recs.append(r)
    return recs


def prio(r):
    g = r["_group"]
    return PRIORITY.index(g) if g in PRIORITY else len(PRIORITY)


def dhash(path, size=8):
    with Image.open(path) as im:
        im = im.convert("L").resize((size + 1, size), Image.LANCZOS)
        px = list(im.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            bits = (bits << 1) | (px[row * (size + 1) + col] > px[row * (size + 1) + col + 1])
    return bits


def find(groups_parent, x):
    while groups_parent[x] != x:
        groups_parent[x] = groups_parent[groups_parent[x]]
        x = groups_parent[x]
    return x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hamming", type=int, default=5)
    args = ap.parse_args()

    recs = load_all()
    with_img, no_img = [], []
    for r in recs:
        p = PROJECT / r["local_path"] if r.get("local_path") else None
        (with_img if p and p.exists() else no_img).append(r)
    print(f"{len(recs)} records; {len(with_img)} with image file, {len(no_img)} metadata-only")

    # perceptual hashes
    for r in with_img:
        try:
            r["_dh"] = dhash(PROJECT / r["local_path"])
            with Image.open(PROJECT / r["local_path"]) as im:
                r["width"], r["height"] = im.size
        except Exception as e:
            r["_dh"] = None
            r["notes"] = (r.get("notes") or "") + f" [unreadable image: {e}]"

    # union-find on exact + perceptual duplicates
    n = len(with_img)
    parent = list(range(n))
    by_sha = defaultdict(list)
    for i, r in enumerate(with_img):
        if r.get("sha256"):
            by_sha[r["sha256"]].append(i)
    for idxs in by_sha.values():
        for j in idxs[1:]:
            parent[find(parent, j)] = find(parent, idxs[0])
    for i in range(n):
        for j in range(i + 1, n):
            a, b = with_img[i], with_img[j]
            if a["_dh"] is None or b["_dh"] is None:
                continue
            ra, rb = a["width"] / a["height"], b["width"] / b["height"]
            if abs(ra - rb) / max(ra, rb) > 0.08:
                continue
            if bin(a["_dh"] ^ b["_dh"]).count("1") <= args.hamming:
                parent[find(parent, j)] = find(parent, i)
    clusters = defaultdict(list)
    for i in range(n):
        clusters[find(parent, i)].append(with_img[i])

    merged = []
    for members in clusters.values():
        # best image = most pixels; metadata donor order = trust priority, richer first
        best = max(members, key=lambda r: (r.get("width") or 0) * (r.get("height") or 0))
        donors = sorted(members, key=lambda r: (prio(r), -sum(bool(r.get(f)) for f in FIELDS)))
        out = {"key": best["key"], "local_path": best["local_path"], "image_url": best.get("image_url"),
               "sha256": best.get("sha256"), "width": best.get("width"), "height": best.get("height")}
        for f in FIELDS:
            out[f] = next((d[f] for d in donors if d.get(f)), None)
        out["confidence"] = ("high" if any(d.get("confidence") == "high" for d in members)
                             else members[0].get("confidence", "low"))
        out["sources"] = sorted({d.get("source") or d["_group"] for d in members})
        out["page_urls"] = sorted({d["page_url"] for d in members if d.get("page_url")})
        out["duplicates"] = sorted(d["local_path"] for d in members if d["local_path"] != best["local_path"])
        out["notes"] = " | ".join(sorted({d["notes"] for d in members if d.get("notes")})) or None
        out["label_status"] = "labelled" if out["title"] and out["year"] else (
            "title_only" if out["title"] else "unlabelled")
        merged.append(out)

    # metadata-only works (e.g. wikidata without image) kept separately
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "metadata_only.jsonl").open("w") as f:
        for r in no_img:
            r = {k: v for k, v in r.items() if not k.startswith("_")}
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # optional labelling by master index
    idx_file = DATA / "swarm" / "index" / "works_index.jsonl"
    if idx_file.exists():
        try:
            from rapidfuzz import fuzz, process
            index = [json.loads(l) for l in idx_file.read_text().splitlines() if l.strip()]
            titles = [w["title"] for w in index]
            for m in merged:
                if m["label_status"] == "labelled":
                    continue
                q = m["title"] or Path(m["local_path"]).stem.replace("-", " ")
                hit = process.extractOne(q, titles, scorer=fuzz.token_set_ratio)
                if hit and hit[1] >= 90:
                    w = index[hit[2]]
                    m["suggested_title"], m["suggested_year"] = w["title"], w.get("year")
                    m["suggested_collection"] = w.get("collection")
                    m["suggested_score"] = hit[1]
        except ImportError:
            print("rapidfuzz missing; skipped index labelling")

    merged.sort(key=lambda m: ((m["year"] or 9999), (m["title"] or "~")))
    with (OUT / "dataset.jsonl").open("w") as f:
        for m in merged:
            f.write(json.dumps(m, ensure_ascii=False) + "\n")
    cols = ["key", "title", "year", "date_raw", "collection", "inventory_no", "medium", "dimensions",
            "genre", "style", "label_status", "confidence", "local_path", "width", "height", "license",
            "sources", "page_urls", "suggested_title", "suggested_year", "suggested_score",
            "description", "notes"]
    with (OUT / "dataset.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for m in merged:
            row = dict(m)
            row["sources"] = ";".join(m["sources"])
            row["page_urls"] = ";".join(m["page_urls"])
            w.writerow(row)

    src = Counter(s for m in merged for s in m["sources"])
    lab = Counter(m["label_status"] for m in merged)
    rep = [f"unique images: {len(merged)} (from {len(with_img)} downloaded files)",
           f"metadata-only records: {len(no_img)}",
           f"label status: {dict(lab)}",
           f"confidence: {dict(Counter(m['confidence'] for m in merged))}",
           f"images per source (a merged image counts for each source): {dict(src)}"]
    (OUT / "report.txt").write_text("\n".join(rep) + "\n")
    print("\n".join(rep))


if __name__ == "__main__":
    main()
