# 0022 — Opening-hours evaluator: `opening-hours-py`, a documented subset, and `hours_unknown` for everything else

- Status: proposed
- Decision Maker(s): Ben
- drafted-by: claude-code (Opus 5) · approved-by: _pending_ · Date: 2026-08-07

## Context and Problem Statement

Spec 002 FR-004 requires feasibility — including **"every stop inside its place's opening window"** — to be computed by deterministic schedule machinery and **never asserted by the model**; FR-005 requires an infeasible plan to name its specific violations before approval. That needs a real evaluator for OSM's `opening_hours` grammar, callable from Python, inside a deterministic pytest tier with no network.

The canonical implementation is **`opening_hours.js`** (v3.9, active, the reference) — and it is **JavaScript, LGPL-3.0**. Both facts cost something in a Python compile path. This was the **one genuinely open unknown** in Spec 002's Phase 0 (`research.md` §R3); the candidate table there was verified against PyPI and GitHub on **2026-08-07**.

There is also no cross-implementation conformance suite published by anyone, and the grammar is messy enough that no implementation covers it completely. So the honest question is not "which evaluator is complete" but **"which evaluator, and what happens to everything it cannot answer."**

## Considered Options

| Candidate | License | Maintenance | PH / SH |
|---|---|---|---|
| **`opening-hours-py`** — Python bindings over the Rust crate `opening-hours` (`remi-dupre/opening-hours-rs`) | **MIT OR Apache-2.0** — both allowlisted (MIT added by ADR-0026) | **2.1.4, released 2026-07-07** — active | **PH yes** (embedded nager.Date holiday DB, country-scoped); **SH not documented as supported** |
| `opening_hours.js` v3.9 (canonical) | **LGPL-3.0** | active; the reference implementation | **PH and SH**, with `nominatim_object` locale |
| `pyopening_hours` — bridges to `opening_hours.js` | **GPL-3.0** — not in the bundleable allowlist, and copyleft over `commons/` | **dead: PyPI 0.3.0, last upload 2015-10-09** | via the JS lib |
| `osm-humanized-opening-hours` / `osm-opening-hours-humanized` | AGPL / varies | README asks for new maintainers | **not an evaluator** — it renders human-readable descriptions |
| ⚠️ `opening-hours` (`anthill/Python_OpeningHours`) | **UNKNOWN** | abandoned at v0.1.1 | — |

Also considered and rejected: embedding `opening_hours.js` via a **JS runtime** (Node subprocess / QuickJS binding) — a whole second runtime in every compile and CI job, plus LGPL unmodified-dependency discipline, to buy SH support the demo day does not need; a **hand-rolled subset parser** — re-deriving a messy grammar is exactly how silent wrong answers happen; **Overture's operating-status field** as the evaluator — coverage is still uneven (stack reference §4), so it is a cross-check signal, not a source of truth; and **the LLM** — forbidden by FR-004.

## Decision Outcome

Chosen: **`opening-hours-py`**, wrapped behind a narrow `commons/opening_hours.py` API and called with **the area's timezone and country code** (PH resolution and sun events silently misfire without locale context). It is the single deterministic evaluator; `state()`, `next_change()` and the lazy interval iterator are exactly the primitives feasibility and the timeline need.

### The honest scope statement

**M1 supports the subset `opening-hours-py` parses, plus PH where the country is covered by the embedded holiday database. Everything else is a named infeasibility, not a guess.** Partial support that fails closed is the posture; a silent wrong answer is the failure SC-002 exists to prevent.

**It fails closed three ways:**

1. An expression the library refuses to parse is **never guessed** — the stop is marked **`hours_unknown`**, which **blocks a silent feasibility pass** and surfaces as a **named conflict** per FR-005.
2. **`SH`-bearing expressions**, and any form the parser rejects, are **rejected loudly with the raw string shown** — never approximated, never smoothed over.
3. **`PH` is trusted only where the area's country is in the embedded holiday database**; elsewhere PH-bearing rules degrade to **`hours_unknown`**, never to "open".

**Nothing is ever defaulted to open.** A documented library limit is carried through as-is: expressions evaluate as **closed before 1900 and after 9999**. And **the LLM never evaluates hours** (FR-004).

### Two wins and one loss, against the canonical implementation

- **Win 1 — no LGPL obligation.** MIT-or-Apache-2.0 keeps the evaluator out of the copyleft conversation entirely. `opening_hours.js`'s LGPL-3.0 is workable as an *unmodified* dependency, but that is a compliance posture we would have to hold, forever, inside `commons/`. This decision is a **reduction** in obligation.
- **Win 2 — no second language runtime in compile or CI.** A Node sidecar means a JS runtime and per-call IPC in a Python compile path, for a feature that must run inside a deterministic pytest tier. Prebuilt wheels also keep the Rust toolchain out of CI.
- **Loss — `SH` support.** Real, and paid rather than hidden: `opening_hours.js` has full PH **and SH** with locale context, and we do not. Every SH-bearing expression is rejected into `hours_unknown` — a worse plan-review experience on the (rare) stops that use it, in exchange for the two wins above. `opening_hours.js` is kept as the **conformance oracle**, not the runtime.

**Version discipline:** 2.1.4 (2026-07-07) is what was verified; the exact pin is **resolved-then-pinned at implementation** (ADR-0007), at which point two things are checked against the installed wheel — whether any embedded **school-holiday** dataset exists (none was found), and **wheel coverage on the CI runner architecture** (PyPI advertises cp310–cp314 across manylinux / macOS-arm64; a source build would drag Rust into CI).

### Consequences

- Good: the opening-window half of feasibility is deterministic, offline, permissively licensed, and runs in Tier 1 with no sidecar; `DATA-LICENSES.md` loses an LGPL row rather than gaining an obligation.
- Good: the failure mode is **legible**. A stop with unevaluable hours surfaces as a named conflict the user can resolve; nobody is shown a confidently wrong "open".
- **⚠️ Named supply-chain hazard — the two names are one hyphen apart.** `pip install opening-hours` resolves to an **abandoned v0.1.1 with an UNKNOWN license** (`anthill/Python_OpeningHours`) — **not** the Rust bindings this ADR chooses. The correct distribution is **`opening-hours-py`** (PyPI metadata name `opening_hours_py`), imported as `from opening_hours import OpeningHours`. This is precisely what the **job-6 slopsquatting gate** exists to catch, and it gets an explicit case there. Nobody following this ADR may be able to install the wrong package.
- **`SourceKind.opening_hours_js` is live code, not prose — keep it, and redefine it.** It is a `SourceKind` value in `commons/licenses.py` **and it carries a merge trust weight in `commons/merge.py`**, so renaming it would be a `SiteRecordV2` concern and would churn stored data. **The value stays as-is; its meaning changes**: from this ADR onward `opening_hours_js` means **"deterministic opening-hours evaluation"**, whatever engine backs it. The enum is not stale; it is generic.
- **Registry and schema-card debt (owned by another agent; recorded here so it is not lost):** `DATA-LICENSES.md` registers `opening_hours.js` / LGPL-3.0 as the feasibility evaluator, and `docs/data/poi-site.md` + `docs/data/itinerary.md` name it in prose. On acceptance those become **`opening-hours-py` / MIT OR Apache-2.0** and the LGPL row disappears. **Applied 2026-08-07 in the same change as this ADR** — the registry and both cards now name `opening-hours-py`, so no card/ADR disagreement remains open.
- Bad / accepted cost: **"matches the canonical evaluator" is a claim nobody can make** — neither project publishes a cross-implementation comparison. The differential run below is the only evidence we will have, which is why the conformance table must be built from **real OSM tags**, not synthetic strings.

### Confirmation

- **`tests/test_opening_hours.py`** (Tier 1): a table of **real OSM `opening_hours` strings from the Rhodes fixture** with expected open/closed at **fixed instants under a frozen clock**, no network.
- **An explicit rejection table**, in the same test: `SH`-bearing and unparseable strings **raise and surface as `hours_unknown`**, never defaulting open; the raw string is preserved in the surfaced conflict; the exception is caught at the wrapper and never escapes it.
- **`evals/test_structural.py`** gains the **SC-002** assertion that **no itinerary is approvable with an unresolved hours conflict** (merge-blocking).
- **Job-6 supply-chain gate**: an explicit case that the dependency is `opening-hours-py`, not the abandoned `opening-hours` v0.1.1 (UNKNOWN licence).
- **A one-off Tier 2 differential run** against `opening_hours.js` in a Node container sizes the divergence from the canonical evaluator and is recorded **as evidence in this ADR** — it is not run per PR.
- **TODO (lands with DU-04):** `commons/opening_hours.py`, `tests/test_opening_hours.py`, the harvested Rhodes tag fixtures, the differential-run evidence, and the exact `pyproject.toml` pin with the wheel/SH verifications above.
