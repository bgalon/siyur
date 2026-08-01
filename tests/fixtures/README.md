# `tests/fixtures/` — provenance

Committed fixtures back the T1/T2 source-adapter tests (ADR-0009) without ever hitting live
Overture/OSM in CI (test-strategy.md "Test data & flake control"). Both fixtures below are
**real data**, extracted live on the date noted — not synthetic — for the Rhodes old-town bbox
`[28.216, 36.440, 28.232, 36.451]` (xmin, ymin, xmax, ymax / lon,lat, EPSG:4326) named in Spec 001.

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

## Reproducing

Both live extractions used only the pinned `duckdb~=1.3` (`spatial`/`httpfs` extensions, installed
on demand) and a plain `curl` POST to Overpass — no extra tooling. Re-running the Overture query
with a newer `release=` string will return a different (larger, more current) row set; re-running
the Overpass query will return live current data and will **not** reproduce the exact 504 above
(that was a live, transient server-load failure, captured opportunistically). Do not re-run either
extraction in CI or in a loop — these are one-time, hand-curated commits per test-strategy.md's
"never hit live Overture/OSM/Anthropic in CI."
