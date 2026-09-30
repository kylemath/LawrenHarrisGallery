# Canadian museums harvest — Lawren S. Harris

Output root: `/Users/fulkanjou/LaurenHarris/data/swarm/canada_museums/`

Scripts: `scrape_canada.py`, `scrape_ngc_browser.py`, `ngc_links.json`

## Per institution (final)

| Institution | Works found | Images downloaded | Metadata completeness | Blockers |
|---|---:|---:|---|---|
| **McMichael** (`collections.mcmichael.com`) | **111** paintings/drawings (129 primaryMaker hits; tools/archives/owner-objects/silkscreens skipped) | **111** | High: title, year (~90%), medium, dims, accession on all | eMuseum facet mixes Owner artefacts; filtered to Artist + art media. `/full` ≈600px |
| **AGGV** (`aggv.ca/emuseum`, people 476051) | **15** | **15** | High | None |
| **National Gallery of Canada** | **93** browser pages + **49** Wikidata overlays | **2** (`Greenland Mountains`, `Return from Church`) | High tombstone (meta description + accession) | Cloudflare blocks curl; most NGC pages publish **no image file** online |
| **AGO** (Wikidata/Commons) | **1** Miners' Houses, Glace Bay (69/122) | 0 (Commons 429 during window; URL recorded) | High | AGO site Cloudflare; Commons rate-limit |
| **MNBAQ** (Wikidata) | **2** | 0 | Inventory only | No P18 images |
| ACI / MBAM / VAG / AGH / Hart House / Glenbow / LAC / WAG | 0 in window | 0 | — | ACI book not found; others 403/404/weak search |

## Exact paths

- `/Users/fulkanjou/LaurenHarris/data/swarm/canada_museums/metadata.jsonl` (~271 lines)
- `/Users/fulkanjou/LaurenHarris/data/swarm/canada_museums/metadata.csv`
- `/Users/fulkanjou/LaurenHarris/data/swarm/canada_museums/images/mcmichael/` (111)
- `/Users/fulkanjou/LaurenHarris/data/swarm/canada_museums/images/aggv/` (15)
- `/Users/fulkanjou/LaurenHarris/data/swarm/canada_museums/images/national_gallery_canada/` (2)
- `/Users/fulkanjou/LaurenHarris/data/swarm/canada_museums/images/ago/`
- Report: `/Users/fulkanjou/LaurenHarris/data/swarm/canada_museums/REPORT.md`

## Resume

```bash
source /Users/fulkanjou/LaurenHarris/.venv/bin/activate
cd /Users/fulkanjou/LaurenHarris/data/swarm/canada_museums
python scrape_canada.py
```
