# 0026 — MIT joins the bundleable allowlist, on consistency rather than need

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code (Opus 5) · approved-by: Ben · Date: 2026-08-07 · accepted: 2026-08-11

## Context and Problem Statement

The bundleable allowlist is the executable form of Constitution Article V: a value may be stamped `bundleable=true` **only if** its `source.license` is on it, and no bundle may contain a `bundleable=false` value. It is merge-blocking (`evals/test_structural.py::test_no_unbundleable_in_bundle`), it is **transcribed** from `DATA-LICENSES.md` rather than invented in code (`tests/test_licenses.py::test_allowlist_matches_the_registry_document` fails on drift), and until now it has grown exactly once, on evidence: **ADR-0012** added Apache-2.0 after measuring that omitting it silently dropped **16.5%** of Overture places from every bundle.

`MIT` was never on it. That was not an oversight — the list inherited tech-design §1.0's original set, and **no data source has ever carried MIT**. Every MIT thing in the registry is a *code dependency*: the Valhalla routing engine (ADR-0020) and the `opening-hours-py` bindings (ADR-0022). Neither stamps a value. Valhalla's *output* is stamped ODbL, because routing over OSM produces a Produced Work of OSM, not an MIT work. `DATA-LICENSES.md` said so explicitly, and `tests/test_licenses.py` pinned MIT in its `NOT_ALLOWLISTED` fixture.

So the allowlist was correct on its own terms and incoherent as a policy, and the incoherence only became visible when ADR-0022 chose a dual-licensed **MIT OR Apache-2.0** dependency and the drafting had to elect the Apache arm to avoid asserting something false.

## Considered Options

- **A — Add MIT.** Restores coherence: the allowlist stops containing a strictly more-encumbered license while excluding a strictly less-encumbered one.
- **B — Leave it out until a data source needs it.** Preserves the "grow on demonstrated need" discipline that ADR-0012 set, and keeps a security-relevant gate at its minimum surface. But it defends an ordering nobody can justify: an MIT-stamped value would be quarantined out of a bundle that carries Apache-2.0 beside it.
- **C — Add MIT *and* BSD-3-Clause together**, since the same argument covers both (BSD-3-Clause is in the registry today for the PMTiles reader and the Protomaps build pipeline). Rejected as scope creep in the same breath as the fix: BSD-3-Clause has no requester and no data source, and adding two on one argument makes the next addition easier than it should be.

## Decision Outcome

Chosen: **A — `MIT` joins the allowlist.**

The argument is **consistency, not need**, and this ADR says so rather than manufacturing a use case. **Apache-2.0 is allowlisted; MIT is strictly more permissive than Apache-2.0.** Apache-2.0 adds a patent grant (§3), a state-changes notice (§4b) and the NOTICE-file reproduction obligation (§4d). MIT adds none of them: its single condition is that the copyright notice and permission notice travel with the work. A policy that bundles the heavier license and refuses the lighter one is not a conservative policy — it is an arbitrary one, and the arbitrariness would have surfaced as a silent quarantine of a perfectly bundleable value.

**This is the first allowlist entry with no data source behind it**, which is a real departure from ADR-0012's precedent, and it is recorded as such so the next addition is argued rather than assumed. The bar this ADR sets is *"strictly more permissive than something already allowed"* — not *"seems fine"*.

**MIT is permissive, not public domain.** Its one obligation is real, and the DU-05 ATTRIBUTION pipeline discharges it exactly as it already discharges Apache-2.0's: reproduce the copyright line and ship the license text. "No obligation" would be the wrong summary and is the way this decision could go wrong in practice.

**What changes:** the quarantine-rule sentence in `DATA-LICENSES.md` (normative), `BUNDLEABLE_LICENSES` and the alias table in `commons/licenses.py` (transcribed), and `tests/test_licenses.py` — where MIT moves out of the `NOT_ALLOWLISTED` fixture and the "unknown license normalises to `None`" test re-points at `AGPL-3.0`, since it was using MIT as its example of an unknown license and would otherwise have quietly stopped testing what it claims.

**What does not change:** `BSD-3-Clause` stays off the list (option C), `LGPL-3.0` stays on it as the generic as-a-dependency arm, `open_web` and `review_provider` remain always-`bundleable=false` whatever license they claim, and no existing stamp changes meaning — nothing in the commons is stamped MIT today, so this decision quarantines nothing differently until something is.

### Consequences

- Good: the allowlist can be defended as a rule rather than as a list. The ordering "Apache-2.0 yes, MIT no" is gone.
- Good: a future MIT-licensed data source (an openly-licensed POI set, an MIT-licensed glyph or sprite set) is not silently dropped from every bundle the way Apache-2.0 places were before ADR-0012.
- Bad / accepted: **the surface of a security-relevant, merge-blocking gate grew without a measured need.** That is the cost of the consistency argument and it should not become routine — the bar above ("strictly more permissive than something already allowed") is the guard.
- Bad / accepted: **one more license whose notice obligation the ATTRIBUTION pipeline must actually discharge.** Forgetting it is an unrendered legal obligation, indistinguishable from success at compile time.
- Neutral: no data or stamp changes today. The decision is entirely forward-looking, which is also why it is cheap to reverse before anything is stamped MIT.

### Confirmation

- **`tests/test_licenses.py`** — the existing drift tripwire `test_allowlist_matches_the_registry_document` re-parses the quarantine-rule sentence out of `DATA-LICENSES.md` and fails if `commons/licenses.py` disagrees, so the registry stays normative and this ADR cannot drift from either. `MIT` now round-trips through `normalize_license` and `bundleable`, and the parametrised `NOT_ALLOWLISTED` cases still hold for `BSD-3-Clause`, `AGPL-3.0`, the NC/ND CC variants, `proprietary` and `user-owned`.
- **`evals/test_structural.py::test_no_unbundleable_in_bundle`** — unchanged and still merge-blocking; it is the assertion this allowlist exists to serve.
- **TODO (lands with DU-05):** the ATTRIBUTION pipeline reproduces the MIT copyright + permission notice for any MIT-stamped bundled value, asserted in `tests/test_compiler_attribution.py` alongside the ODbL, CC-BY-SA and Apache-2.0 NOTICE cases. **Until that assertion exists, MIT is allowlisted but its obligation is undischarged** — the same gap Apache-2.0 carried between ADR-0012 and DU-05.
