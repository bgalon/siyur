# FAIL-001 — Source data script contamination (Hebrew address stored in Cyrillic)

- Date: 2026-07-24 · Severity: med
- Root-cause class: data-quality

## Symptom

In the discovery spike, the Overture place **"Cafelix"** (Old Jaffa) carried the address `Сгула 13` — the Hebrew street **סגולה** ("Sgula") rendered in **Cyrillic**. A naive pipeline would display this to a traveler as an authoritative address. Related: Overture `names.common` for the same area was largely null, and category taxonomies disagreed across sources (Overture `coffee_shop`/`bakery` vs OSM `cafe`).

## Trajectory excerpt

Discovery spike (`spike/run.py`), Overture places theme (release 2026-07-22.0), Jaffa bbox. Merged `SiteRecordV1` sample showed `address = "Сгула 13"` with `source.kind = overture`, `bundleable = true`.

## Root cause

Commercial POI sources (Overture places is Meta/Foursquare-derived) contain mis-scripted / mis-transliterated free-text fields. **We cannot trust a value's script or language from its source.** Rendering raw source strings assumes a data cleanliness that does not hold across scripts.

## Fix

- Treat source script/language as *claimed*, not authoritative. Add a normalization step: detect the script of free-text fields (name, address), and when it mismatches the site's locale, flag it (lower `confidence`) and prefer a same-locale alternative from another source or a transliteration.
- Surface cross-source disagreement as a `FieldConflict` (already captured by the merge) rather than silently picking one.
- Pulls a name/address transliteration sliver into M1 (recorded in `tech-design.md` §1.1 i18n findings).

## Regression eval added

**STUB (entry stays OPEN until filled):** `evals/golden/mis_scripted_address.json` — a fixture record with a Hebrew street written in Cyrillic; the structural/merge eval must assert the pipeline flags the script mismatch (lowers confidence / raises a conflict) rather than presenting it as authoritative. To be created when the eval harness lands at ramp-up (DU-00), then wired as a merge-blocking case. Owner: build agent. Tracked so DU-02/DU-03 (research + merge) cannot close without it.
