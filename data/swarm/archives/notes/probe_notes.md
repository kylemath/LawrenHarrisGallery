# Archives swarm probe notes
- **Openverse API**: HTTP 200; holds=likely (name mentioned)
- **Internet Archive**: HTTP 200; holds=likely (name mentioned)
- **Europeana API (no key)**: HTTP 401; holds=unknown
- **DPLA API (no key)**: HTTP 403; holds=unknown
- **HathiTrust catalog**: HTTP 403; holds=unknown
- **LAC collection search**: HTTP 403; holds=unknown
- **Canadiana**: HTTP 200; holds=unknown
- **BAnQ numerique**: HTTP 200; holds=unknown
- **GAC search**: HTTP 200; holds=likely (name mentioned)
- **Commons P180**: HTTP error; holds=unknown

## Blockers
- Europeana / DPLA: API key required (401/403).
- LAC / HathiTrust / some portals: bot 403 or connection reset.
- IA `lawrenharrispain00harr` (1948 AGO catalog): rights © AGO — no plate scrape.
- Pinterest / Artsy / Artnet: skipped (login walls / not plainly scrapable).
- Main Commons `Category:Paintings by Lawren Harris` + Wikidata P170: owned by other swarm workers.
