# `tests/fixtures/` — provenance

> **⚖️ These files are third-party data and are NOT covered by the repository's Apache-2.0
> code licence.** Each carries its own terms — ODbL-1.0 for the OSM-derived captures
> (Overpass, Valhalla), CC-BY-SA-4.0 for the Wikivoyage/Wikipedia capture, CDLA-Permissive-2.0
> for the Overture extracts. See [`/DATA-LICENSES.md`](../../DATA-LICENSES.md) for the
> per-source row and [`/LICENSING.md`](../../LICENSING.md) for why the repository is split
> three ways. **Copying a fixture into another project brings its licence with it**; the code
> licence does not override it. OSM-derived data requires "© OpenStreetMap contributors".

Committed fixtures back the T1/T2 source-adapter tests (ADR-0009) without ever hitting live
Overture/OSM/Wikimedia/Valhalla in CI (test-strategy.md "Test data & flake control"). Every
fixture below is **real data**, extracted live on the date noted — not synthetic. All of them
except the **Takayama** pair are for the Rhodes old-town bbox `[28.216, 36.440, 28.232, 36.451]`
(xmin, ymin, xmax, ymax / lon,lat, EPSG:4326) named in Spec 001; the Takayama pair is the second
area SC-005's genericity eval (T063) is measured over.

## `overture_places_rhodes.parquet`

- **Source**: Overture Maps **places** theme, release **`2026-07-22.0`** (latest GA release at
  extraction time; discovered via `ListBucketResult` on `s3://overturemaps-us-west-2/release/`).
- **Extracted**: 2026-08-01, via DuckDB `spatial` + `httpfs` reading the hosted cloud parquet
  directly (no bulk mirror — this *is* the ADR-0009 R1 ingestion pattern, replayed for a fixture).
- **Row count**: 200 (the `≤200` cap), **schema-faithful** — every column DuckDB resolved from the
  live theme (`id`, `geometry`, `categories`, `confidence`, `websites`, `emails`, `socials`,
  `phones`, `brand`, `addresses`, `names`, `sources`, `operating_status`, `basic_category`,
  `taxonomy`, `version`, `bbox`, `theme`, `type`) is preserved verbatim — nothing hand-edited.
- **Selection query** (reproducible — bump the release string to re-run):

  ```sql
  INSTALL spatial; INSTALL httpfs; LOAD spatial; LOAD httpfs;
  SET s3_region = 'us-west-2';

  COPY (
    WITH area AS (
      SELECT *
      FROM read_parquet(
        's3://overturemaps-us-west-2/release/2026-07-22.0/theme=places/type=place/*',
        filename=false, hive_partitioning=1
      )
      WHERE bbox.xmin BETWEEN 28.216 AND 28.232
        AND bbox.ymin BETWEEN 36.440 AND 36.451
    ),
    non_cdla_ids AS (      -- force in rows whose per-record license != the theme default
      SELECT DISTINCT area.id
      FROM area, UNNEST(sources) AS t(s)
      WHERE s.license != 'CDLA-Permissive-2.0'
    ),
    greek_ids AS (          -- force in rows with a Greek-script primary name
      SELECT id FROM area
      WHERE regexp_matches(names.primary, '[Ͱ-Ͽἀ-῿]')
      ORDER BY id LIMIT 60
    ),
    filler_ids AS (          -- top up to 200 with an arbitrary, deterministic slice
      SELECT id FROM area ORDER BY id LIMIT 140
    ),
    keep_ids AS (
      SELECT id FROM non_cdla_ids
      UNION SELECT id FROM greek_ids
      UNION SELECT id FROM filler_ids
    )
    SELECT area.* FROM area JOIN keep_ids USING (id)
    ORDER BY area.id LIMIT 200
  ) TO 'overture_places_rhodes.parquet' (FORMAT PARQUET);
  ```

- **Why this selection, not a plain `LIMIT 200`**: a naive first-200-rows slice of this bbox came
  back **100% `CDLA-Permissive-2.0`** and mostly Latin-script names — it would not exercise the two
  properties ADR-0009 explicitly calls out (per-record license variance; Greek-only names). The
  query above still samples real rows, just biased toward including examples of both, then fills
  the remainder arbitrarily (by `id` order, deterministic).
- **Verified variance in the committed file**:
  - Licenses (`UNNEST(sources)`, per-record — never the theme default):
    `CDLA-Permissive-2.0` (365 source-rows across the 200 places), `Apache-2.0` (33, all
    `dataset='Foursquare'`), `CC0-1.0` (2, `dataset='AllThePlaces'`) — the CDLA/Apache split this
    slice's `docs/data/poi-site.md` and `DATA-LICENSES.md` both call out as "Meta CDLA-Permissive-2.0
    vs Foursquare Apache-2.0 within one theme."
  - Greek-only `names.primary` (no Latin variant in `names.common`): 61 of 200 rows, e.g.
    `id=0425b08a-fed7-4c86-b172-9645558883f2` → `"Πύλη Ταρσανά"` (`basic_category=historic_site`,
    lon/lat `28.22800737, 36.44608099`) — this row is the deliberate cross-source anchor, see below.
- **Note for the implementing adapter** (`commons/sources/overture.py`, later task): this release's
  `id` column **is** the GERS id (Overture folded the separate `gers_id` concept into `id`) — there
  is no distinct `gers_id` column in the live schema. `docs/data/poi-site.md`'s `gers_id` field maps
  to this fixture's `id` column; flag this to whoever wires the adapter, don't silently invent a
  `gers_id` that isn't there.

## `overpass_rhodes.json`

- **Source**: live Overpass API (`https://overpass-api.de/api/interpreter`), **not** synthetic.
- **Fetched**: 2026-08-01, `timestamp_osm_base` in the response is `2026-08-01T11:03:35Z`.
- **Query** (single request, honest `User-Agent`, respects the ≤1 req/s / fair-use terms in
  `DATA-LICENSES.md`):

  ```
  [out:json][timeout:20];
  node["name"](36.440,28.216,36.451,28.232);
  out body 25;
  ```

- **Contents**: 25 real OSM nodes in the bbox — verbatim Overpass JSON (`version`, `generator`,
  `osm3s.copyright` = the required ODbL notice, `elements[]`).
- **Greek `name:el` tags present**: e.g. `id=126244310` (`"Ρόδος"`), `id=794491388`
  (`"Πύλη Ταρσανά"`), `id=1370730227` (`"Μεγάλο Συντριβάνι"`), and others.
- **Cross-source spatial match (for merge testing, ADR-0009 / merge ε=25m τ=0.6)**: Overpass node
  `id=794491388` (`"Πύλη Ταρσανά"` / `name:el="Πύλη Ταρσανά"` / `name:en="Gate of the Arsenal"`,
  `historic=city_gate`, lon/lat `28.2280031, 36.4460502`) sits **~3.4 m** (haversine) from the
  Overture places row `id=0425b08a-fed7-4c86-b172-9645558883f2` (`"Πύλη Ταρσανά"`,
  `basic_category=historic_site`, lon/lat `28.22800737, 36.44608099`) in
  `overture_places_rhodes.parquet` above — both real records of the same real gate, well inside the
  ε=25 m spatial threshold **and** passing the τ=0.6 same-language name-similarity gate (identical
  Greek string), so this pair is the intended fixture for `test_merge.py`'s join test.

## `overpass_504.txt`

- **Source**: live Overpass API, **real captured failure** — not a hand-written mock. The public
  `overpass-api.de` instance returned this body with HTTP status **504** on 2026-08-01 while under
  load (fetched in the same session as the fixtures above, before a lighter retry query succeeded).
- **Shape**: Overpass's own `OSM3S Response` HTML error page (not a bare nginx page) — includes the
  ODbL notice paragraph and a `Dispatcher_Client::request_read_and_idx::timeout` runtime-error
  message ("The server is probably too busy to handle your request."). This is the realistic
  degradation shape `commons/sources/osm.py`'s adapter must catch and turn into a partial-result +
  `degraded=true` outcome (FR-012, ADR-0009's graceful-degradation confirmation test).

## `wikivoyage_rhodes.json` — the MediaWiki narration fixture (T006)

Backs `tests/test_sources_wikivoyage.py` (T033) and the `commons/sources/wikivoyage.py` adapter
(T031) without CI ever reaching Wikimedia. ADR-0024 is the authority for what the stamp must
carry; this file is the raw material it is derived from.

- **Source**: the live **MediaWiki Action API** (`action=query`, `formatversion=2`) over
  **`https://en.wikivoyage.org/w/api.php`** and **`https://en.wikipedia.org/w/api.php`** — the
  S1 access path ADR-0024 chose. No API key, unauthenticated, honest
  `User-Agent: siyur/0.0 (https://github.com/bgalon/siyur)`.
- **Captured**: 2026-08-08, `_capture.captured_utc = 2026-08-08T16:04:54Z`. **Five requests**,
  one second apart, **all HTTP 200** — no retries, no failures.
- **Size / records**: 53,968 bytes; **5 calls**, covering **1 Wikivoyage article** (with its full
  wikitext), **20 Wikipedia geosearch hits**, **4 Wikipedia articles with intro extracts**, and
  **2 titles that exist on neither wiki**.
- **Shape — an envelope of verbatim responses, not a reshaped payload.** Narration needs several
  requests (geosearch, then content, then the negative lookup), and a single API body cannot carry
  them, so the file is:

  ```jsonc
  { "_capture": { /* date, endpoints, UA, bbox, geosearch centre */ },
    "calls": [ { "name": …, "endpoint": …, "params": { /* exact query string */ },
                 "http_status": 200, "response": { /* the API's body, verbatim */ } } ] }
  ```

  Everything under `response` is exactly what the API returned — key order, `batchcomplete`,
  `limits`, page ordering and all. Nothing was renamed, filtered or hand-edited.

- **Reproducible** with `curl` + `python3` only (no WebFetch — see the note at the end of this
  section). Each call replays as, e.g.:

  ```bash
  curl -sS -G 'https://en.wikivoyage.org/w/api.php' \
    -H 'User-Agent: siyur/0.0 (https://github.com/bgalon/siyur)' \
    --data-urlencode 'action=query'      --data-urlencode 'format=json' \
    --data-urlencode 'formatversion=2'   --data-urlencode 'prop=revisions|info' \
    --data-urlencode 'rvprop=ids|timestamp|content' --data-urlencode 'rvslots=main' \
    --data-urlencode 'inprop=url'        --data-urlencode 'titles=Rhodes (city)'
  ```

  The other four calls are the same command with the `params` recorded in the file. The geosearch
  pair uses `list=geosearch&gscoord=36.4455|28.2240&gsradius=1000&gslimit=20` — `36.4455, 28.2240`
  is the **centroid of the Rhodes bbox** above and 1,000 m is the smallest radius that covers its
  ~940 m half-diagonal. Geosearch is a **circle, not a bbox**: 18 of the 20 Wikipedia hits fall
  inside the bbox, and two do not (`St. Francis of Assisi Cathedral, Rhodes` is south of it,
  `Colossus of Rhodes` north). The adapter must clip; the fixture deliberately keeps both
  out-of-bbox hits rather than trimming them to look tidy.

### The article carrying listing templates

**`Rhodes (city)`** on Wikivoyage (call `wikivoyage_page_rhodes_city`) — `pageid` 29573,
`revid` **5289050**, revision timestamp **2026-06-07T13:23:53Z**. Its **31,497 characters of raw
wikitext** sit at `response.query.pages[0].revisions[0].slots.main.content`, which is what
`rvprop=…|content` + `rvslots=main` buys: **wikitext, not rendered HTML**, because the structured
prize ADR-0024 S3 rejects scraping for is in the templates. Template counts in the committed text:
**`{{see}}` ×17, `{{sleep}}` ×12, `{{go}}` ×5, `{{drink}}` ×5, `{{eat}}` ×4, `{{buy}}` ×1,
`{{listing}}` ×1** — 45 listings for `mwparserfromhell` to parse at T031.

**Wikivoyage geosearch returns exactly one article here, Wikipedia twenty**, and that asymmetry is
the reason ADR-0024 names both wikis: Wikivoyage is **destination-level** (one article per town,
carrying the listings), Wikipedia is **POI-level** (`list=geosearch` + per-article extracts). A
test that expects a Wikivoyage article per POI is testing something the source does not do.

The four Wikipedia articles captured with `prop=extracts&exintro=1&explaintext=1` are
`Archaeological Museum of Rhodes` (885 chars), `Fortifications of Rhodes` (1,294),
`Panagia tou Kastrou` (537) and `Church of St John of the Collachium` (1,695) — **plain-text
intros**, already prose. `prop=coordinates` is included, so each carries a `coordinates[]` entry
(`lat`/`lon`, `globe: "earth"`, EPSG:4326) usable for matching a place to an article.

### The place with no article (FR-023)

**`Gate of the Arsenal`** / **`Πύλη Ταρσανά`** — deliberately the *same gate* that is already this
directory's cross-source anchor (Overpass node `794491388`, Overture
`0425b08a-fed7-4c86-b172-9645558883f2`), so the no-article case is a place the other fixtures
already know, not a made-up miss. Both name forms are **missing on both wikis**: they appear in
`wikipedia_extracts_batch` *and* in the dedicated `wikivoyage_no_article` call as

```jsonc
{ "ns": 0, "title": "Gate of the Arsenal", "missing": true, … }
```

— **no `pageid`, no `revisions`, no `extract`**. This is the FR-023 case: `stories: []`, nothing
invented.

Two traps the captured shape exposes, both of which would pass a careless test:

1. **A missing page still gets a URL.** `inprop=url` returns `fullurl`, `editurl` and
   `canonicalurl` for `Gate of the Arsenal` even though the article does not exist — they are the
   URLs the page *would* have. **A resolvable-looking URL is not evidence of an article**; key on
   `missing`, never on the presence of `canonicalurl`.
2. **Missing and present pages arrive in one array.** `wikipedia_extracts_batch` is a single
   `batchcomplete: true` response whose `query.pages` holds the two missing titles *and* the four
   real ones. The adapter must filter **per page**, not per response.

### Field mapping — ADR-0024's `SourceRef` vs. what the API actually returns

ADR-0024 names fields that the API returns under a different key, or does not return at all.
Recorded here rather than renamed in the data:

| ADR-0024 / `Story` field | Where it comes from in this fixture |
|---|---|
| `SourceRef.kind` (`wikivoyage`\|`wikipedia`) | **Not in the response.** It is which `endpoint` was called — `calls[].endpoint`. |
| `SourceRef.id` = `<lang>:<Page Title>` | **Composed**, no single key: `pages[].pagelanguage` (`"en"`) + `":"` + `pages[].title`. |
| `SourceRef.url` "pinned to the revid" | **Not returned pinned.** `inprop=url` gives the *unpinned* `pages[].canonicalurl`; the adapter builds the pinned form from `canonicalurl` + `revisions[0].revid` (`…?oldid=<revid>`). Do not expect an `oldid` URL anywhere in this file. |
| **`revid`** | `pages[].revisions[0].revid` (needs `rvprop=ids`) — e.g. `5289050` for `Rhodes (city)`. |
| `Story.observed_at` (revision timestamp) | `pages[].revisions[0].timestamp` (needs `rvprop=timestamp`) — `2026-06-07T13:23:53Z`. |
| `SourceRef.license` = `CC-BY-SA-4.0` | **Not in the response at all.** It is a property of the wiki, asserted by the adapter from `DATA-LICENSES.md`/ADR-0024 — never read from the payload. |

**`lastrevid` is a decoy.** `prop=info` also returns a page-level `pages[].lastrevid`, currently
equal to `revisions[0].revid` for every page here. It is the page's *current* revision, which
drifts from the revision whose content you actually fetched — attribution must use
`revisions[0].revid`, the one that goes with the text in hand.

**Fair use / do not re-run**: five unauthenticated requests, spaced one second apart. Wikimedia's
API is free and generous, but this is a one-time hand-curated capture, not something CI or a timer
may repeat. Re-running returns live current revisions and **will not reproduce the revids above**.

**Captured with `curl` + `python3`, not WebFetch** — AGENTS.md forbids WebFetch for this kind of
metadata capture because its summariser has twice misreported a field it also returned verbatim.
Provenance fixtures need the bytes, not a description of them.

## The Valhalla pair — `valhalla_rhodes_route.json` + `valhalla_rhodes_matrix.json` (T005)

The recorded responses `commons/routing.py`'s `FixtureProvider` replays, so **Tier 1 never builds a
graph and never opens a socket** (ADR-0020). Both were captured from a **real container**, from a
**real Rhodes walking network** — nothing here is hand-written.

- **Engine**: `ghcr.io/valhalla/valhalla-scripted:3.8.3@sha256:24ef7955899dececb94e26c6dfb89d64fabfae875f980432694b0261eb6c251b`
  — the same digest `docker-compose.yml` pins. `/status` confirmed `"version": "3.8.3"`.
- **Captured**: 2026-08-08, two `POST`s, the two endpoints ADR-0020 allows and no others —
  **`/route`** and **`/sources_to_targets`**, both **pedestrian** costing.
- **Graph**: Geofabrik **`europe/greece-latest.osm.pbf`**, 338,880,000 bytes, `Last-Modified:
  Fri, 07 Aug 2026 03:23:41 GMT`, **md5 `d1627ab0fff8f4513ee89e6a48cfcc04`** — matching Geofabrik's
  own published `.md5`, verified against the file inside the container. Greece is the **smallest
  Geofabrik extract that contains Rhodes**; Geofabrik publishes no Dodecanese or per-island
  sub-extract. Tile build took **~2 minutes** on this PBF.
- **Sizes**: `valhalla_rhodes_route.json` **15,622 bytes** (1 trip, **2 legs**);
  `valhalla_rhodes_matrix.json` **1,525 bytes** (a **3×3** matrix, 9 cells).

### The Andorra trap, and how this capture avoided it

The shared `siyur_siyur_valhalla_tiles` volume **holds an Andorra graph** from an earlier
verification run, and Valhalla's `use_tiles_ignore_pbf` means **repointing `tile_urls` at another
area does not rebuild** — it silently keeps serving the old graph. A fixture captured that way
would be Andorra routes labelled Rhodes, and every test built on it would pass against fiction.

This capture used a **separate container on a fresh, empty named volume**, so the stale graph was
neither read nor destroyed:

```bash
docker volume create siyur_t005_rhodes_tiles
docker run -d --name siyur-valhalla-t005 -p 8102:8002 \
  -v siyur_t005_rhodes_tiles:/custom_files \
  -e tile_urls='https://download.geofabrik.de/europe/greece-latest.osm.pbf' \
  -e force_rebuild=True -e use_tiles_ignore_pbf=False \
  -e build_elevation=False -e build_admins=False -e build_time_zones=False \
  -e server_threads=6 \
  ghcr.io/valhalla/valhalla-scripted:3.8.3@sha256:24ef7955899dececb94e26c6dfb89d64fabfae875f980432694b0261eb6c251b

# Readiness is /status answering, never a sleep — the port binds only after the graph is built.
until curl -sf -m 5 http://localhost:8102/status >/dev/null; do sleep 10; done
```

**The negative control, run before the capture**: the *pre-existing* container on `:8002` was given
the identical Rhodes `/route` body and answered

```json
{"error_code":171,"error":"No suitable edges near location","status_code":400}
```

— no walkable edge anywhere near Rhodes, i.e. demonstrably not a Rhodes graph. The new container
answers the same request with a route. That pair of results is the proof, not the label on the
volume.

### The requests

Costing options are **explicit per ADR-0020** — `walking_speed` comes from the user's pace band
(3.5–5.0 km/h) and Valhalla's brisk **5.1 km/h default is never taken implicitly**:

```jsonc
// POST /route
{"locations": [{"lat": 36.4460502, "lon": 28.2280031},
               {"lat": 36.4436351, "lon": 28.2282751},
               {"lat": 36.4425208, "lon": 28.2256561}],
 "costing": "pedestrian",
 "costing_options": {"pedestrian": {"walking_speed": 4.5, "walkway_factor": 0.9}},
 "units": "kilometers"}

// POST /sources_to_targets — same three points as both sources and targets (the N×N
// feasibility matrix of FR-004), same costing block, same units.
```

The three stops are **real named OSM nodes already committed in `overpass_rhodes.json`**, so the
route is traceable end to end rather than to arbitrary coordinates: `794491388` **Πύλη Ταρσανά /
Gate of the Arsenal** (also this directory's cross-source anchor), `1370730227` **Μεγάλο
Συντριβάνι**, `948338313` **Olympos**.

### What the committed responses contain, and how it was verified

- **Two legs, 26 and 33 shape vertices** — far past T017's `≥3` bar. Trip summary **0.698 km /
  559.826 s**; leg summaries **0.328 km / 262.437 s** and **0.370 km / 297.388 s**.
- **The geometry is in Rhodes.** Decoded extent **lon 28.225660 – 28.228521, lat 36.442569 –
  36.446027**: every one of the 59 vertices lies inside the Rhodes bbox
  `[28.216, 36.440, 28.232, 36.451]`, and **zero** lie anywhere near Andorra la Vella
  (lon 1.4–1.8, lat 42.4–42.7). `trip.summary`'s own `min_lon/min_lat/max_lon/max_lat` agree.
- **`shape` is polyline6, not GeoJSON.** `trip.legs[].shape` is Valhalla's **default** encoding — a
  Google-polyline string at 1e-6 precision — and it is committed **as returned**. It was tempting
  to ask for `shape_format=geojson` so a test could read coordinates directly; that would have been
  reshaping the fixture into what the adapter wishes for. `commons/routing.py` decodes it, and
  T017's "valid EPSG:4326 `LineString`" assertion runs on the decoded result. Note the axis order
  flip: the polyline encodes **lat, lon** pairs, the `LineString` must be **(lon, lat)**.
- **Pace actually took effect**: leg 0 is `0.328 km / 262.437 s` = **4.499 km/h**, i.e. the
  requested 4.5 — not the 5.1 default. This is worth asserting; a costing block silently ignored is
  invisible in the geometry and moves every feasibility verdict.
- **Units**: `trip.units` and the matrix's `units` both echo `"kilometers"`; leg `length` is **km**
  and `time` is **seconds** (fractional in `/route`, integer in the matrix).
- **The matrix is real, and the asymmetry proves it.** `algorithm: "timedistancematrix"`, diagonal
  exactly `0.0 / 0`, and the off-diagonal pairs **do not match**: stop 0→2 is `0.629 km / 504 s`
  while 2→0 is `0.654 km / 525 s`. A one-way / stepped old-town network gives different distances
  in each direction — a fabricated or haversine table would be perfectly symmetric. That check
  costs one line and catches a synthetic fixture instantly.
- **The two responses echo locations differently**, which will otherwise look like a bug: `/route`'s
  `trip.locations` echoes the **requested** points (`36.44605, 28.228003`), while the matrix's
  `sources`/`targets` echo the **network-snapped** ones (`36.446027, 28.228047`). The snapped point
  is where the leg shape actually starts.
- **Licensing**: Valhalla is MIT, but the graph is built from OSM, so every geometry, distance and
  duration in these two files is a **Produced Work of ODbL data** — it carries **ODbL** and renders
  **"© OpenStreetMap contributors"** (ADR-0020, `DATA-LICENSES.md`). Routing output is not "ours"
  because our container computed it.

**Cleanup / re-running**: the capture container and `siyur_t005_rhodes_tiles` volume are throwaway
and were removed afterwards — recreate them with the block above. Re-running against a newer
`greece-latest.osm.pbf` will return slightly different distances and durations, so do **not** wire
this capture into CI; `tests/test_compiler_routes.py` under `-m integration` is the intended place
to check the fixtures have not drifted from the engine.

## The Takayama pair — the second area (SC-005 / T063)

`evals/test_genericity.py` proves the "generic any-area, nothing hardcoded per place" claim by
running the **same** `run_research` code path over Rhodes *and* a second area. These two files
are that second area: **Takayama (高山) old town, Gifu, Japan**, bbox
`[137.252, 36.135, 137.268, 36.146]` (xmin, ymin, xmax, ymax / lon,lat, EPSG:4326) — deliberately
the **same extent** as the Rhodes bbox (0.016° × 0.011°), so the two passes differ in *where* they
are and in nothing else.

**Why Japan, and not a second European town.** `commons/translit.py`'s
`SUPPORTED_SOURCE_SCRIPTS` carries a transform for `Grek` and for nothing else. A second
Latin-script area would only have re-run the "already Latin, nothing to derive" branch that
Rhodes' own English names already cover. A Japanese area instead exercises the path genericity
actually *means*: **no transform exists for this script, so nothing is derived, and that is a
correct outcome rather than an error or a silent failure.** (`commons/merge.py`'s docstring
already names Takayama as the genericity contrast for its ε-in-metres argument — this fixture
makes that contrast executable.)

### `overture_places_takayama.parquet`

- **Source**: Overture Maps **places** theme, release **`2026-07-22.0`** — the same release the
  Rhodes fixture pins and `commons/sources/overture.py`'s `DEFAULT_RELEASE` names.
- **Extracted**: 2026-08-01, DuckDB `spatial` + `httpfs` over the hosted cloud parquet, exactly
  as the Rhodes fixture was (ADR-0009 R1 ingestion pattern, replayed for a fixture).
- **Row count**: 200 (the `≤200` cap) selected from the **1,095** rows the live theme has in this
  bbox; schema-faithful — the same 19 columns as the Rhodes fixture, nothing hand-edited.
- **Selection query** (reproducible — bump the release string to re-run; the same shape as the
  Rhodes query, with the Greek-script clause swapped for a Japanese-script one):

  ```sql
  INSTALL spatial; INSTALL httpfs; LOAD spatial; LOAD httpfs;
  SET s3_region = 'us-west-2';

  COPY (
    WITH area AS (
      SELECT *
      FROM read_parquet(
        's3://overturemaps-us-west-2/release/2026-07-22.0/theme=places/type=place/*',
        filename=false, hive_partitioning=1
      )
      WHERE bbox.xmin BETWEEN 137.252 AND 137.268
        AND bbox.ymin BETWEEN 36.135 AND 36.146
    ),
    non_cdla_ids AS (      -- force in rows whose per-record license != the theme default
      SELECT DISTINCT area.id
      FROM area, UNNEST(sources) AS t(s)
      WHERE s.license != 'CDLA-Permissive-2.0'
    ),
    japanese_ids AS (       -- force in rows with a Hiragana/Katakana/Han primary name
      SELECT id FROM area
      WHERE regexp_matches(names.primary, '[ぁ-ゟ゠-ヿ一-鿿]')
      ORDER BY id LIMIT 60
    ),
    filler_ids AS (          -- top up to 200 with an arbitrary, deterministic slice
      SELECT id FROM area ORDER BY id LIMIT 140
    ),
    keep_ids AS (
      SELECT id FROM non_cdla_ids
      UNION SELECT id FROM japanese_ids
      UNION SELECT id FROM filler_ids
    )
    SELECT area.* FROM area JOIN keep_ids USING (id)
    ORDER BY area.id LIMIT 200
  ) TO 'overture_places_takayama.parquet' (FORMAT PARQUET);
  ```

- **Verified per-record license spread in the committed file** (`UNNEST(sources)`, per record —
  never the theme default), the ADR-0012 / `DATA-LICENSES.md` variance reproduced independently
  in a second part of the world:
  - by **source row**: `CDLA-Permissive-2.0` 325 (`dataset='Overture'` 200, `'meta'` 122,
    `'Microsoft'` 3), `Apache-2.0` 55 (all `dataset='Foursquare'`), `CC0-1.0` 20 (all
    `dataset='AllThePlaces'`).
  - by **distinct place**: all 200 carry at least one `CDLA-Permissive-2.0` source; 55 also carry
    an `Apache-2.0` source; 20 also carry a `CC0-1.0` one. Apache-2.0 is allowlisted as of
    ADR-0012, so these stamp `bundleable=true` — the eval reads that from `commons.licenses`
    rather than asserting it here.
- **Names**: 170 of the 200 rows have a Hiragana/Katakana/Han `names.primary`. `names.common` is
  **empty on all 200 rows** (this release publishes no tagged alternates for this area), so the
  adapter keys every Overture name `und` per its untagged-`primary` rule — which is itself part
  of what the genericity eval observes: an `und` name carries no language claim, so the script
  guard makes none either.

### `overpass_takayama.json`

- **Source**: live Overpass API (`https://overpass-api.de/api/interpreter`), **not** synthetic.
- **Fetched**: 2026-08-01, `timestamp_osm_base` in the response is `2026-08-01T18:27:31Z`.
- **Query** (single request per attempt, the adapter's own honest `User-Agent`
  `siyur/0.0 (https://github.com/bgalon/siyur)`, same shape as the Rhodes query):

  ```
  [out:json][timeout:20];
  node["name"](36.135,137.252,36.146,137.268);
  out body 25;
  ```

- **Fair use**: the first attempt returned **HTTP 504** — the same transient overload
  `overpass_504.txt` captured. It was **not** retried in a loop: one further request was sent
  after a **120 s** backoff and returned 200. Two requests total. Do not re-run this in CI or on
  a timer (`DATA-LICENSES.md` "API terms").
- **Contents**: 25 real OSM nodes — verbatim Overpass JSON (`version`, `generator`,
  `osm3s.copyright` = the required ODbL notice, `elements[]`).
- **Name tags present**: `name` ×25, `name:en` ×10, `name:ja` ×5, `name:ja-Hira` ×2, `name:es` ×2,
  `name:pt` ×2, `name:ja_rm` ×1. The `name:ja_rm` tag is **not** BCP-47, so
  `commons/sources/osm.py` drops it and counts it — the ingest reports
  `dropped={'unusable_language_tag': 1}`, which is the honest outcome, not a loss.
- **What the pipeline actually does with this pair** (the numbers the eval asserts against):
  200 Overture + 25 OSM records merge to **224** (one intra-OSM join: nodes `1416000484` and
  `1420999628`, both the Takayama Red Cross Hospital, 5.09 m apart (`commons.merge.distance_m`,
  well inside ε=25 m) — one `location` conflict
  parked in the ledger), **0** derived `*-Latn` names and **0** script-mismatch flags. The zero
  is the point: `Grek` is the only transform that exists, so `ja` / `ja-Hira` / `und` names are
  carried verbatim and nothing is invented for them.

## Reproducing

The Overture/Overpass extractions used only the pinned `duckdb~=1.3` (`spatial`/`httpfs`
extensions, installed on demand) and a plain `curl` POST to Overpass; the MediaWiki and Valhalla
captures used `curl` + `python3` plus, for Valhalla, the digest-pinned container above — no extra
tooling in either case, and **no WebFetch** (AGENTS.md: its summariser has misreported fields it
also returned verbatim, and a provenance fixture needs the bytes). Re-running the Overture query
with a newer `release=` string will return a different (larger, more current) row set; re-running
the Overpass query will return live current data and will **not** reproduce the exact 504 above
(that was a live, transient server-load failure, captured opportunistically). Do not re-run either
extraction in CI or in a loop — these are one-time, hand-curated commits per test-strategy.md's
"never hit live Overture/OSM/Anthropic in CI."
