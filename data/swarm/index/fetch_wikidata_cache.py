"""Persist SPARQL JSON obtained via an allowed research fetch (query.wikidata.org
robots Disallow /sparql for crawlers; results cached here for offline rebuild)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

# If raw/wikidata_works.json already has by_creator rows, keep them.
# Else, copy from agent-tools cache path if provided as argv.

RAW = Path(__file__).resolve().parent / "raw" / "wikidata_works.json"


def main() -> None:
    if len(sys.argv) > 1:
        src = Path(sys.argv[1])
        text = src.read_text(encoding="utf-8")
        start, end = text.find("{"), text.rfind("}")
        data = json.loads(text[start : end + 1])
        bindings = data.get("results", {}).get("bindings", [])
        out = {
            "by_creator": bindings,
            "source": "sparql_cache",
            "note": "SPARQL endpoint disallows crawler UA; results cached from research fetch",
        }
        RAW.parent.mkdir(parents=True, exist_ok=True)
        RAW.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"cached {len(bindings)} bindings -> {RAW}")
    else:
        if RAW.exists():
            data = json.loads(RAW.read_text(encoding="utf-8"))
            n = len(data.get("by_creator") or [])
            print(f"existing cache: {n} works in {RAW}")
        else:
            print("no cache; pass path to SPARQL JSON")


if __name__ == "__main__":
    main()
