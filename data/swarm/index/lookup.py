#!/usr/bin/env python3
"""Fuzzy-match titles or filenames against the Lawren S. Harris works index."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

from rapidfuzz import fuzz, process

INDEX_PATH = Path(__file__).resolve().parent / "works_index.jsonl"

_cache: Optional[list[dict]] = None
_choices: Optional[dict[str, dict]] = None


def _norm_query(s: str) -> str:
    s = s.strip()
    # filename -> words
    if "/" in s or "\\" in s:
        s = Path(s).name
    s = re.sub(r"\.(jpe?g|png|gif|webp|tif{1,2})$", "", s, flags=re.I)
    s = s.replace("_", " ").replace("-", " ")
    s = re.sub(r"\blawren\s+(?:s\.?\s+)?harris\b", " ", s, flags=re.I)
    s = re.sub(r"\b(oil on|canvas|wood|board|paperboard|jpg|jpeg)\b", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _extract_year(s: str) -> Optional[int]:
    m = re.search(r"\b(18\d{2}|19\d{2}|1970)\b", s)
    if m:
        y = int(m.group(1))
        if 1880 <= y <= 1975:
            return y
    return None


def load_index(path: Path = INDEX_PATH) -> list[dict]:
    global _cache, _choices
    if _cache is not None and path == INDEX_PATH:
        return _cache
    works = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                works.append(json.loads(line))
    if path == INDEX_PATH:
        _cache = works
        _choices = None
    return works


def _choice_map(works: list[dict]) -> dict[str, dict]:
    m: dict[str, dict] = {}
    for w in works:
        m[w["title"]] = w
        for alt in w.get("alt_titles") or []:
            m[alt] = w
    return m


def match(
    title_or_filename: str,
    year: Optional[int] = None,
    *,
    limit: int = 5,
    score_cutoff: float = 55.0,
    index_path: Optional[Path] = None,
) -> list[dict[str, Any]]:
    """
    Fuzzy-match a title or image filename to indexed Lawren S. Harris works.

    Returns list of dicts: {score, work, matched_as} sorted by score desc.
    Optional year (+-1) boosts / filters candidates.
    """
    path = index_path or INDEX_PATH
    works = load_index(path)
    choices = _choice_map(works)
    q = _norm_query(title_or_filename)
    q_year = year if year is not None else _extract_year(title_or_filename)

    # strip year from query for better title match
    q_title = re.sub(r"\b(18\d{2}|19\d{2})\b", " ", q)
    q_title = re.sub(r"\s+", " ", q_title).strip() or q

    hits = process.extract(
        q_title,
        list(choices.keys()),
        scorer=fuzz.WRatio,
        limit=max(limit * 4, 20),
        score_cutoff=score_cutoff,
    )
    results = []
    seen = set()
    for matched_as, score, _ in hits:
        work = choices[matched_as]
        key = (work["title"], work.get("year"))
        if key in seen:
            continue
        seen.add(key)
        adj = float(score)
        if q_year is not None and work.get("year") is not None:
            diff = abs(int(work["year"]) - int(q_year))
            if diff == 0:
                adj = min(100.0, adj + 8)
            elif diff == 1:
                adj = min(100.0, adj + 3)
            elif diff > 5:
                adj -= 15
        results.append({"score": round(adj, 2), "matched_as": matched_as, "work": work})
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:limit]


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Match a title/filename to Lawren S. Harris works index")
    p.add_argument("query", help="Title string or image filename")
    p.add_argument("--year", type=int, default=None)
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--cutoff", type=float, default=55.0)
    args = p.parse_args(argv)
    for r in match(args.query, year=args.year, limit=args.limit, score_cutoff=args.cutoff):
        w = r["work"]
        print(
            f"{r['score']:6.1f}  {w.get('year') or '?'}  {w['title']}"
            f"  [{w.get('collection') or ''}]  via={r['matched_as']!r}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
