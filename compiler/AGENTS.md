# AGENTS.md — `compiler/`

Nested override for work in this package; extends the root `AGENTS.md` (read that first).

**Scope:** the offline-bundle pipeline (`docs/design/tech-design.md` §5.3, ordered): `pmtiles extract`
(tight itinerary bbox + buffer from a Protomaps daily build, URL resolved at run time) → base MapLibre
style → Valhalla per-area build → route legs + pruned walk graph → **quarantine filter** → freeze
`content` → assemble `ATTRIBUTION.md` → SHA-256 each artifact → write `BundleManifestV1` → GCS. M1 runs
this in-process behind a flag; M2 moves it to a Cloud Run Job. Read the `bundle-manifest`, `route-leg`,
and `tile-source` schema cards in `docs/data/` before touching the manifest.

**Invariants enforced here:**
- **Airplane-mode guarantee:** everything the travel UI reads resolves to a path in the manifest;
  review links (M2) render as "needs connectivity," never errors.
- **Licence quarantine (§1.0, merge-blocking):** the filter drops **every** `bundleable=false` value;
  no bundle may contain one. `open_web` / `review_provider` are always `bundleable=false`.
- **Integrity:** content-address every artifact (SHA-256) and record `manifest_sha256` for the
  launch-time check; generated `ATTRIBUTION.md` (ODbL + CC-BY-SA credits) is per-bundle — fix the
  generator, never hand-edit the output.

**Status (DU-00):** package imports; the pipeline lands in M1 (DU-05).
