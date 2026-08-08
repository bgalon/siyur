# 0029 — Offline country resolution (Natural Earth) and a holiday coverage oracle (`holidays`)

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: Ben · Date: 2026-08-08 · accepted: 2026-08-08

## Context and Problem Statement

ADR-0025 ruling 2 gave `area` a **`country_code`** (ISO 3166-1 alpha-2) derived deterministically from the polygon at resolve time, and `docs/data/area.md` makes it a **required M1 field** with **no default and no fallback** — "a plan whose frame is a guess is worse than a plan that was refused."

Nothing pins how to derive it. `timezonefinder~=8.2` (T001) resolves the *timezone* half and **not** the country half; verified by execution, it exposes zone boundary polygons via `get_geometry()` and no country data at all. So T008 cannot be implemented as specified, and T008 gates all of Phase 2.

**Why the country code is load-bearing rather than metadata.** `opening-hours-py` resolves `PH` — public holidays — against a *country's* calendar. An OSM rule of the form `Tu-Su 08:00-20:00; Mo off; PH off` cannot be evaluated on a given date without one. Feasibility (FR-005, T018) requires every stop to fall inside its opening window, so a wrong or missing country produces a plan whose stops are open on paper and closed in fact — discovered by the traveller, **offline, with no network to check and no way to replan**. That is the failure SC-002 exists to prevent, and the offline bundle removes every escape hatch from it.

A second gap sits behind the first. `opening-hours-py`'s embedded holiday database covers **122 countries**; 554 of 676 two-letter codes raise `UnknownCountryError`. Absent are **`IL`**, `IN`, `TH`, `AE`, `SA`, `JO`, `ET`, `DZ` and roughly 120 more — most of the Middle East, South and Southeast Asia, and much of Africa. For a product whose first requirement is *any area*, "we resolve the country correctly and then cannot use it" is only half a fix.

Both halves are settled here because they are one question — *can this area's opening hours be trusted on this date* — and answering it in two ADRs would let the two answers drift.

## Considered Options

### Q1 — polygon → ISO 3166-1 alpha-2, offline

- **A — commit Natural Earth 1:10m admin-0 and read it with the already-pinned geopandas/shapely (chosen).** No new dependency.
- **B — `geopip`.** MIT, offline, bundles polygons.
- **C — `reverse_geocoder` / `reverse-geocode`.** Offline point→country.
- **D — `geodatasets` / `cartopy`.** Maintained, standard.
- **E — `country-boundaries`, the Rust crate already inside the `opening-hours-py` wheel.** Zero new bytes.
- **F — Natural Earth 1:50m** instead of 1:10m. 800 KB against 4.9 MB.

### Q2 — public holidays

- **G — keep `opening-hours-py`'s embedded database as the sole evaluator (status quo).**
- **H — replace it with `holidays` (python-holidays) and evaluate `PH` ourselves.**
- **I — keep `opening-hours-py` as the evaluator and add `holidays` as a coverage gate + cross-check oracle (chosen).**
- **J — `workalendar`.**
- **K — rewrite `PH` into concrete date selectors** in the expression before evaluating.

## Decision Outcome

**Q1: option A.** Commit `ne_10m_admin_0_countries.zip` (Natural Earth v5.1.1, **4,930,492 bytes**, 258 rows, EPSG:4326) and read it in place — geopandas opens the zip directly, so there is no unzip step and the committed bytes stay checksum-comparable against the upstream download:

```python
gpd.read_file("zip://data/ne_10m_admin_0_countries.zip!ne_10m_admin_0_countries.shp")
```

**Public domain**, per Natural Earth's terms: *"All versions … are in the public domain. No permission is needed to use Natural Earth. Crediting the authors is unnecessary."* The cleanest licence in the registry — unlike every ODbL alternative it carries **no attribution obligation at all**. Performance is a non-issue: `read_file` 0.03 s, spatial index 0.001 s, 100 `intersects` queries 0.015 s. The zip beats every re-encoding measured (FlatGeobuf 8.90 MB, GPKG 9.13 MB, GeoJSON 24.4 MB).

**Q2: option I.** `opening-hours-py` stays the single evaluator — one grammar, one engine, no second interpretation of `opening_hours` to keep in sync. `holidays~=0.102` (MIT, 251 alpha-2 countries, 342 contributors, ~monthly releases) is pinned for the two things it is strictly better at:

- **Coverage gate.** If the raw expression contains a `PH` or `SH` token and the area's `country_code` is not among the 122, the stop is **`hours_unknown`** — a named FR-005 conflict. This turns ~127 countries from *silently unevaluated* into *honestly refused*.
- **Cross-check oracle.** Where the country *is* covered, evaluate with `opening-hours-py` and independently ask `holidays.country_holidays(cc, subdiv=…)` whether `ItineraryV1.date` is a holiday. Disagreement → `hours_unknown`. **This closes ADR-0022 A2 for free**: the German regional-holiday case the evaluator silently passes is caught by the oracle, using the subdivision support the evaluator lacks.

Both are pure, offline, deterministic and Tier-1. Combined footprint 12.4 MB + 6.6 MB.

### Why the alternatives lose

**B — `geopip` is disqualified twice, and the second one is fatal.** Its bundled TM_WORLD_BORDERS is from **2008**, predating South Sudan; run against real inputs it returns `Juba → SD` and `Pristina → RS`. And its 1.1 MB simplified globe **omits Rhodes entirely** — it returns `None` for this project's own fixture area. A dataset that cannot find the area we test on is not a candidate.

**C — LGPL, and point-only.** `reverse_geocoder` (last release 2016-09-15) and `reverse-geocode` are nearest-populated-place lookups over GeoNames cities. No boundary geometry, so the largest-intersection rule is unimplementable, and nearest-city is wrong precisely at borders — the case the rule exists for.

**D — they download at first use.** Both fetch shapefiles on demand, which breaks the no-network requirement for CI reproducibility and contradicts the offline guarantee.

**E — no Python API.** `country-boundaries` is inside the wheel but unreachable: the full instance surface is `intervals, is_closed, is_open, is_unknown, next_change, normalize, state, warnings`. It is reachable only through `auto_country`, which **ADR-0022 A1 forbids** because it silently fails open.

**F — 1:50m is a false economy.** It is 800 KB and **loses Vatican City and Gibraltar entirely**: a Vatican bbox resolves to `IT`, a Gibraltar bbox to `ES`. For a product whose unit of work is a **walkable day tour**, silently resolving one of the most-walked tour areas on earth to the wrong country — and therefore the wrong holiday calendar — is exactly the class of error this ADR exists to prevent. The extra 4.1 MB is bought with that.

**G — status quo leaves ~127 countries failing open**, including `IL`. Not tenable for an any-area product.

**H — replacing the evaluator re-derives a grammar ADR-0022 explicitly refused to re-derive.** `holidays` answers "is this date a holiday"; it does not parse `opening_hours`. Evaluating `PH` ourselves means owning the interaction between holiday rules and the rest of the expression, which is where silent wrong answers live.

**J — `workalendar` is dormant.** No release since **2023-01-01**, no commit since 2024-04, and five runtime dependencies.

**K — expression rewriting breaks on the common form.** Specialising `PH` into a date selector works standalone (`"Mo-Su 10:00-18:00; 2026 Apr 22 off"` evaluates correctly), but the compound form `"Mo-Sa,2026 Apr 22 10:00-18:00"` is a `ParserError` — so the very ordinary `"Mo-Sa,PH 10:00-18:00"` has no rewrite target. Partial grammar surgery, rejected on the same grounds as H.

### Three implementation rules, normative — `area.md` does not yet state them

These are not tips. Each was verified against the real dataset and each produces a wrong `country_code` if missed.

1. **Read `ISO_A2_EH`, never `ISO_A2`.** `ISO_A2` is `-99` for **22 rows, including France and Norway** — a lookup on it returns `-99` for metropolitan France. `ISO_A2_EH` fixes France, Norway and Kosovo (`XK`), leaving 13 `-99` rows, all disputed or uninhabited. South Sudan (`SS`) is present.
2. **`ISO_A2_EH` is not unique — group by code and sum the intersection areas before taking the maximum.** Duplicates: `AU`×4, `FR`×2, `KZ`×2, `BR`×2. Ranking *rows* instead of *countries* compares fragments, and picks the wrong winner for any area near an overseas département or an Australian external territory.
3. **Drop `-99` rows before ranking — they win otherwise.** Verified on a Nicosia bbox: N. Cyprus `0.00121` and the UN buffer zone `0.00083` both beat `CY` at `0.00036`. If dropping them empties the candidate set, that is a **hard failure at resolve time** — the same class as open ocean, never a fallback.

Sanity checks pass under these rules: Rhodes → `GR`; Juba → `SS`; a Kehl/Strasbourg bbox straddling the Rhine → `FR` `0.00413` > `DE` `0.00137`, the largest-intersection rule working as intended.

**Known limit, named rather than discovered:** 1:10m is too coarse for the Baarle-Hertog BE/NL enclaves — that bbox returns `NL` only. Accepted.

### Consequences

- Good: `country_code` becomes derivable, so **T008 unblocks and with it all of Phase 2**.
- Good: the country dataset is **public domain** — no attribution obligation, no share-alike, the least encumbered entry in `DATA-LICENSES.md`.
- Good: **no new dependency for Q1.** geopandas and shapely are already pinned; the artifact is a committed file.
- Good: the oracle closes ADR-0022 A2 (regional holidays) without a second evaluator.
- Good: **the fail-closed posture becomes true for every country**, not just the covered 122 — outside them, PH/SH-bearing stops are refused rather than silently evaluated.
- Bad / accepted cost: **4.93 MB of binary in the repo.** Mitigated by a fixture test asserting its `sha256` and row count, which makes an accidental swap merge-blocking.
- Bad / accepted cost: **two holiday sources to keep aligned.** The cross-check turns a divergence into `hours_unknown` rather than a wrong answer, so drift degrades gracefully — but a wide divergence would make many stops unknown, and that is a signal to investigate rather than to relax the check.
- Bad / accepted cost: `opening-hours-py`'s holiday table is **bundled in the wheel with a hard 2076 ceiling** and refreshed only by an upstream release, which upstream does by hand. `holidays` is rule-based with no horizon, which is part of why it is the oracle.
- Accepted: an already-compiled bundle does **not** change when holiday data does, and that is correct — the PH question was answered at compile time and frozen into the plan the user approved. A self-contained bundle silently re-deciding feasibility offline would be worse than a stale one. **Recompile is the only remedy**, and compile has connectivity. This argues for an evaluator/holiday-data **provenance stamp in `BundleManifestV1`** so a recompile can be diffed; recorded as owed, not decided here.

### The update mechanism (the operational question this ADR must answer)

**Holidays** — no scheduled job, no fetch, no runtime hook:

```bash
uv lock --upgrade-package holidays --upgrade-package opening-hours-py
uv sync --locked
uv run pytest tests/test_opening_hours.py -q   # the frozen-clock table is the regression net
```

Cadence: `holidays` monthly is comfortable, **annually is the floor** — it ships the following year's dates well ahead and computes any year on demand regardless. `opening-hours-py` follows upstream. A dependabot/renovate PR is sufficient; the frozen-clock table is what makes an upgrade safe to merge.

**Country borders** — re-download the zip, compare `sha256`, commit the new bytes, run the resolver tests. Natural Earth releases are rare (v5.1.1 files are dated 2022-05-09 and remain current), so an **annual checksum check is ample**.

### Confirmation

- **`tests/test_area_frame.py`** (owed, T008) — Rhodes → `GR`, Juba → `SS`, the Rhine-straddling bbox → `FR` by largest intersection, the Nicosia bbox → `CY` with `-99` rows dropped, and a polygon over open ocean → **hard failure, not a default**.
- **A dataset fixture test** — the committed zip's `sha256` and row count (258), so an accidental or silent swap is merge-blocking.
- **`tests/test_opening_hours.py`** (T015) — the coverage gate: a `PH`-bearing expression in an **uncovered** country (`IL`) yields `hours_unknown`, never an evaluated answer; and the cross-check: the German regional-holiday case yields `hours_unknown` rather than `open`.
- **`DATA-LICENSES.md`** — a row for Natural Earth, following the `timezonefinder` pattern (a resolve-time server-side dependency whose only retained output is a scalar), and noting that public domain carries no obligation at all. Closes the "deliberately NOT registered" paragraph left by the previous pass.
- **Owed, not discharged here:** the evaluator/holiday-data provenance stamp in `BundleManifestV1`.

### Related

- **ADR-0022** — the evaluator, amended the same day. A1 (`auto_country=False`) is a **precondition** for this ADR's gate: with the default `True` the library resolves a country itself and swallows the uncovered case, so the gate would never fire.
- **ADR-0025 ruling 2** — the largest-intersection rule this dataset implements.
- **AGENTS.md geo-API table** — owed row: **GeoPandas 1.x removed `gpd.datasets`**. Verified on the repo venv, `gpd.datasets.get_path('naturalearth_lowres')` raises `AttributeError`. Models emit it constantly, and it belongs alongside `.unary_union` in the stale-API traps.
- **Method standard** — owed: **do not use WebFetch for PyPI metadata.** It reported `opening-hours-py` as "0.11.1" while returning `info.version: 2.1.4` in the same response, twice on the same package across two sessions. Read the JSON fields directly; the lockfile hashes are the authority.
