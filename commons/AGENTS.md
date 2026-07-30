# AGENTS.md — `commons/`

Nested override for work in this package; extends the root `AGENTS.md` (read that first).

**Scope:** the shared **data spine** — the `SourcedValue` primitive and the versioned
schemas (`SiteRecordV1`, `ItineraryV1`, `BundleManifestV1`), PostGIS access, the per-field
merge, and the `commons/llm.py` **`ModelRouter` seam** (ADR-0004). Read `docs/design/tech-design.md`
§1–§2 and the `docs/data/*` schema cards before touching a model — never guess a schema.

**Invariants enforced here:**
- **Licence quarantine (§1.0):** every value carries a `SourceRef` + `bundleable` stamp; a value
  may be `bundleable=true` only for the allowlisted licences. Merge-blocking test.
- **Seam purity (ADR-0004):** no provider SDK (`anthropic` / `openai` / `litellm`) may be imported
  anywhere in `commons/` **above** `commons/llm.py`. The `tests/test_llm_seam.py` tripwire lands at
  DU-02 and enforces this through DU-04.
- **CRS discipline:** geometries are EPSG:4326 (lon, lat) unless a schema card says otherwise; spatial
  arithmetic runs in PostGIS/DuckDB/shapely — never let the LLM emit coordinates.
- **Merge is per-field, union-first:** never discard a source; losing values become `FieldConflict`s
  (ε = 25 m, τ = 0.6 same-language, name-signal required — spike-derived, §1.2).

**Status (DU-00):** package imports; the data model + seam land at DU-02+.
