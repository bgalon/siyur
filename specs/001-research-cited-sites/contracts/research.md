# Contract — Research (trigger a pass, stream progress)

**Service**: `api/areas.py` → drives `planner/pipeline.py` (`resolve_area → research → curate → persist`). **Auth**: bearer token required; **write path is auth-gated** (ADR-0008). **Transport**: Server-Sent Events (SSE) so the user sees progress and partial results (FR-012). Maps to **FR-002, FR-003, FR-007, FR-009, FR-011, FR-012, US1, US3**.

## POST /areas/{area_id}/research — trigger research (SSE)

Runs the planner pipeline over the resolved polygon, ingesting **Overture + OSM** into stamped `SiteRecordV1`s, merging/deduping into the **shared commons**. `force_refresh=true` re-runs over a covered area (US2).

**Request**:
```jsonc
{ "force_refresh": false }   // false: no-op with a reuse hint if already covered; true: re-run + merge
```

**Response**: `200` `text/event-stream`. Event sequence (trajectory `superset` over `resolve_area → research → curate`):
```
event: status   data: {"phase":"resolve_area","msg":"polygon ready"}
event: status   data: {"phase":"research","source":"overture","found":31}
event: status   data: {"phase":"research","source":"osm","found":18,"degraded":false}
event: status   data: {"phase":"curate","merged":39,"conflicts":4}
event: site     data: { /* a persisted SiteRecordV1 (subset), every value a SourcedValue */ }
…
event: summary  data: {"sites":39,"new":39,"reused":0,"conflicts":4,
                       "sources":{"overture":31,"osm":18},"degraded_sources":[]}
event: done     data: {"area_id":"…"}
```

**Invariants asserted on the stream / persisted output**:
- **Every value on every emitted `site` is a `SourcedValue`** with a real `source` + `license` + `bundleable` stamp; **no unstamped value is ever emitted** (FR-003 / SC-002). Anything the model tried to assert without a source is rejected, not streamed.
- **Locations come from Overture/OSM only** — never model-emitted or model-computed (FR-005).
- **Records are written directly to the shared commons**, deduped by the merge rules; **no source ref is lost**, disagreements become `FieldConflict`s (FR-007 / FR-009).
- **Degraded sources** (e.g. Overpass 504) ⇒ partial results with `degraded:true` + a `degraded_sources` list in `summary` — never a hang or silent-incomplete (FR-012 / edge case).
- **Empty area** ⇒ `summary.sites=0` with an explicit "nothing found", **zero fabricated places** (SC-006 / FR-002).
- **Non-Latin names** ⇒ emitted `site.names` carry the source-script value **and** a derived `*-Latn` display name, original preserved (FR-008 / US3).

**Errors**: `401` unauthenticated · `404` unknown `area_id` · `409` research already running for this area (idempotency guard).

## Contract tests (T2 component + deterministic eval)

- Authenticated research over the Overture **fixture** yields ≥1 persisted `SiteRecordV1`, all values stamped; a value with a missing source is refused (T1 + component).
- A **different** session reads the just-written record from the shared commons via `GET /sites` (backs `test_commons_write_shared`, ADR-0008).
- `force_refresh=true` over a covered area creates **no duplicate rows** (backs `test_commons_reuse_dedupe`).
- Trajectory eval: the emitted `phase` sequence is a `superset` of `resolve_area → research → curate` (mocked LLM, no API key).
- A record whose `location` is absent from source geodata is **not** synthesised by the model (geometry-provenance eval).
