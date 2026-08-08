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

> **Amended 2026-08-08, before approval — three claims below were verified false against the
> installed 2.1.4 wheel.** The *decision* is unchanged and still right; three of the *reasons*
> given for it were wrong, and one is load-bearing for the entire fail-closed posture. Struck in
> place rather than deleted, per the ADR-0018 precedent. **Approving this ADR does not approve the
> struck claims.**

**It fails closed three ways:**

1. An expression the library refuses to parse is **never guessed** — the stop is marked **`hours_unknown`**, which **blocks a silent feasibility pass** and surfaces as a **named conflict** per FR-005. *(Holds.)*
2. ~~**`SH`-bearing expressions**, and any form the parser rejects, are **rejected loudly with the raw string shown** — never approximated, never smoothed over.~~ **✎ The library does not do this.** `"Mo-Su 10:00-18:00; SH off"` parses clean, `warnings == []`, `validate()` returns `True`, and it evaluates as though the `SH` clause were absent. **`commons/opening_hours.py` (T014) must detect the `SH` token itself** and force `hours_unknown`. The obvious implementation — `try: OpeningHours(...) except ParserError` — **never fires**, so T015's rejection table would pass against a wrapper that does nothing while a school-holiday closure evaluates as open in production.
3. **`PH` is trusted only where the area's country is in the embedded holiday database**; elsewhere PH-bearing rules degrade to **`hours_unknown`**, never to "open". *(Holds — but only under amendment A1.)* **Coverage is 122 countries**: probing all 676 two-letter codes, 554 raise `UnknownCountryError`. Absent include **`IL`**, `IN`, `TH`, `MY`, `PK`, `AE`, `SA`, `QA`, `JO`, `LB`, `IQ`, `IR`, `PS`, `ET`, `TZ`, `SN`, `CI`, `DZ` — most of the Middle East, South and Southeast Asia, and much of Africa; roughly 127 assigned codes in total. `UnknownCountryError` is raised **at construction**, before any evaluation, and for **every** expression rather than only PH-bearing ones — so the wrapper must route it to `hours_unknown` and **must never retry without a country** (see A1).

**Nothing is ever defaulted to open.** A documented library limit is carried through as-is: expressions evaluate as **closed before 1900 and after 9999**. And **the LLM never evaluates hours** (FR-004).

### Two wins and one loss, against the canonical implementation

- **Win 1 — no LGPL obligation.** MIT-or-Apache-2.0 keeps the evaluator out of the copyleft conversation entirely. `opening_hours.js`'s LGPL-3.0 is workable as an *unmodified* dependency, but that is a compliance posture we would have to hold, forever, inside `commons/`. This decision is a **reduction** in obligation.
- **Win 2 — no second language runtime in compile or CI.** A Node sidecar means a JS runtime and per-call IPC in a Python compile path, for a feature that must run inside a deterministic pytest tier. Prebuilt wheels also keep the Rust toolchain out of CI.
- **Loss — `SH` support.** Real, and paid rather than hidden: `opening_hours.js` has full PH **and SH** with locale context, and we do not. Every SH-bearing expression is rejected into `hours_unknown` — a worse plan-review experience on the (rare) stops that use it, in exchange for the two wins above. `opening_hours.js` is kept as the **conformance oracle**, not the runtime.

**Version discipline:** 2.1.4 (2026-07-07) is what was verified; the exact pin is **resolved-then-pinned at implementation** (ADR-0007), at which point two things are checked against the installed wheel — ~~whether any embedded **school-holiday** dataset exists (none was found)~~ *(see A3 — one exists)*, and **wheel coverage on the CI runner architecture** (PyPI advertises cp310–cp314 across manylinux / macOS-arm64; a source build would drag Rust into CI).

### Amendments (2026-08-08) — verified against the installed 2.1.4 wheel

**A1 — the mandatory call shape. This ADR's central claim is true only under it.** Every construction is:

```python
OpeningHours(expr, timezone=tz, country=cc, auto_country=False, auto_timezone=False)
```

Both `auto_*` flags **default to `True`**, and with `auto_country=True` an uncovered country is **silently swallowed**: `OpeningHours("Mo-Su 00:00-24:00; PH off", coords=(32.08, 34.78))` — Tel Aviv — constructs fine, `warnings == []`, and `PH` is simply never applied. Same for Delhi, Juba, and open ocean at `(0.0, -140.0)`: no error, no warning. Separately, **`country=None` with a `PH` clause evaluates `OPEN`** — so "omit the country on failure" is the one repair the wrapper must never make.

"**Nothing is ever defaulted to open**" is therefore a property of *how we call the library*, not of the library. That makes it a **ruling**, not a style note, and **T014 owes a test asserting both flags are off**.

**A2 — regional public holidays are a silent hole, closed by an oracle rather than by the library.** With `country="DE"`, 2026-01-06 Epiphany, 08-15 Assumption and 10-31 Reformation all return `state=open, is_unknown()=False` — although all three are present in the wheel's own `holidays_public.regional.txt` (733 DE rows). No subdivision code is accepted: `DE-BY`, `US-CA`, `ES-CT`, `GB-SCT` all raise `UnknownCountryError`. **The data ships and is unreachable**, and upstream's 1.4.0 changelog claim that regional holidays surface as unknown does not hold from Python in 2.1.4.

**Mitigation (ADR-0029):** `holidays~=0.102` is pinned as a **coverage gate and cross-check oracle** — it is subdivision-aware where this library is not. Disagreement between the two → `hours_unknown`.

**A3 — an embedded school-holiday dataset does exist**, contrary to Version discipline above: `holidays_school.global.txt`, covering **5 entities (DK, GL, IE, MX, NL)**. Partial, never applied, and unwarned — which is a **worse** failure mode than absent, and is the second reason A1's token scan is required rather than optional.

**A4 — the wheel ships ODbL data, and our licence position depends on A1.** Parsing the CycloneDX SBOM the wheel carries at `opening_hours_py-2.1.4.dist-info/sboms/`: of 144 components exactly one is share-alike — **`tzf-dist 0.0.2026-b-fix1`, ODbL-1.0** (timezone-boundary polygons behind `auto_timezone`) — alongside `country-boundaries 1.2.0` (Apache-2.0, OSM-derived, behind `auto_country`). Nothing else is copyleft; no GPL/AGPL/LGPL anywhere. **Under A1 we read neither dataset**, so the `DATA-LICENSES.md` row stays a code-dependency row. Recorded because the flag defaults are `True`: a future caller who omits them changes the project's licence position without noticing, which is exactly the class of thing the registry exists to make mechanical.

**Confirmation owed by T014/T015**, in addition to the frozen-clock table already specified: a test that both `auto_*` flags are passed `False`; a test that an `SH`-bearing expression yields `hours_unknown` **via the token scan**, not via a `ParserError` that never fires; and a test that an uncovered country (e.g. `IL`) yields `hours_unknown` rather than an evaluated answer.

### Amendments (2026-08-07/08, second session — verified by execution)

*Numbered A7+ to sit after the 2026-08-08 block above; two sessions amended this ADR in parallel and both sets are kept.*

- **A7 — `SH` does not raise, so "reject loudly" cannot be a `try/except`.** `OpeningHours("SH off")` constructs cleanly and `.state()` returns `CLOSED`; only genuinely malformed input raises `ParserError`. A `try/except ParserError` wrapper would evaluate `SH` rules as ordinary ones — wrong answers, no exception — while passing every row of its own rejection table. The wrapper must **detect the `SH` token itself**. `OpeningHours(...).warnings` is an attribute (not a method) and is **empty** for `SH`, so it is not an alternative.
- **A8 — a missing country degrades to "open", which is the dangerous direction.** `Mo-Fr 09:00-17:00; PH off` on Greek Independence Day (Wed 2026-03-25) returns **`open` without a country** and `closed` with `country="GR"`. The fail-closed posture therefore has to cover the *input* as well as the expression: **a `PH`-bearing expression with no `country_code` yields `hours_unknown`, never a verdict.** This also promotes `area.country_code` from a completeness gap to a **correctness blocker** — `timezonefinder` resolves no country, so until one is wired every `PH` rule in the commons would evaluate wrongly.
- **A9 — `.state()` takes a `datetime`, not a string.** A string raises `TypeError`, which reads like a parse failure and would be swallowed by a broad `except`. (This one cost me a wrong verification: my first check passed strings and concluded `SH` *did* raise, from all four expressions including the valid ones.)

  **SUPERSEDED IN PART on 2026-08-08.** The `auto_country=False` half stands. The
  `auto_timezone=True` half is **withdrawn**: this amendment argued the flag on for solar
  accuracy, not having seen the wheel's SBOM. **A6 above is decisive** — the flag reads
  `tzf-dist` (**ODbL-1.0**), the one share-alike component among 144, so enabling it would
  give the project a share-alike obligation in exchange for a selector M1 barely uses. Both
  flags are pinned **off**, and the solar cost measured here is paid instead by refusing sun
  expressions outright (A11). The measurement below is retained because it is what makes that
  cost explicit rather than invisible.

- **A10 — the library infers a country from coordinates unless told not to, and the obvious hardening of its sibling flag is wrong.** Two constructor flags, measured on 2026-08-08 rather than assumed:

  | | |
  |---|---|
  | `auto_country` | defaults to inferring a country **from `coords`**. With coords supplied, `Mo-Fr 09:00-17:00; PH off` on Greek Independence Day returns `closed` — a correct-looking verdict from a country **nobody supplied or vetted**. Without coords the flag is inert. |
  | `auto_timezone` | gates whether `coords` are used for **solar** computation at all. |

  The hazard is not that it fails open — it is that it **quietly succeeds**, which would *bypass* A2's rule that a `PH`-bearing expression with no `country_code` yields `hours_unknown`. The guard would never fire. So the wrapper pins **`auto_country=False`** explicitly at every construction site, even where no coords are passed today, so that adding coords later cannot silently re-enable inference.

  **`auto_timezone` is pinned `True`, and the instinct to "make them consistent" is a defect.** Measured at Rhodes on the summer solstice with coords and an explicit timezone supplied in both runs:

  ```
  auto_timezone=True    06:30 = open     19:45 = open      ← the real sun
  auto_timezone=False   06:30 = closed   19:45 = closed    ← a generic 07:00–19:00 day,
  no coords             06:30 = closed   19:45 = closed      byte-identical to passing no coords
  ```

  Setting it `False` makes coordinates inert for solar work and silently degrades every sun-event expression to a generic day — while looking like hardening. It does **not** override the caller's timezone (Rhodes coords with `America/New_York` yields the same instant, differently expressed). *This amendment exists because the coordinator directed `auto_timezone=False` and the implementer refused it with the measurement above. The measurement belongs in the ADR so the directive cannot be re-issued.*

- **A11 — sun events are refused OUTRIGHT, and coordinates do not rescue them.** `sunrise-sunset` with no `coords` falls back to a generic day, so at **Reykjavik in winter** it reports **open** at 08:00, 09:00, 16:00, 17:00 and 18:00 while the sun is down. With `auto_timezone` pinned off (A6/A10) coordinates are inert for solar work, so a sun expression cannot be answered correctly *at all*: **any sun-bearing expression yields `hours_unknown`**, with or without a location. Accepting one with coordinates would return the generic window dressed as the sun. Together with A2 this makes the rule general — *any* expression whose evaluation depends on context the caller did not supply is `hours_unknown`, not a guess.

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
