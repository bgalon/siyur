# 0010 — Greek→Latin display-name transliteration sliver: deterministic, offline, provenance-inheriting

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: Ben · Date: 2026-07-31

## Context and Problem Statement

Spec 001 FR-008 (Q2=A, resolved by Ben 2026-07-31) requires that non-Latin (Greek) source place-names be presented **automatically, on the display-name field only, in a readable Latin/English form**, while the **original-script value and its attribution are preserved**. SC-004 sets the bar: **≥95%** of non-Latin display names get a Latin rendering, original preserved in **every** case. tech-design §1.1 flagged this as an accepted M1 "sliver" whose **exact extent is pinned in Spec 001 and formalized as an ADR** — and §6 lists "transliteration engine choice" as a deferred decision. Planning slice 001 forces it: (1) what performs the transliteration, and (2) how the derived value is provenance-stamped, given the schema card's `SourceRef.kind` enum has **no** "derived"/"transliteration" kind and the rule is "never guess the schema."

A second constraint: the discovery spike found **source scripts are untrustworthy** (a Hebrew address stored in Cyrillic — FAIL-001). Any transliteration step must not blindly trust the stored script, and **addresses are explicitly excluded** for this reason.

## Considered Options

**Engine:**
- **E1 — Deterministic rule-based transliteration (ICU `Greek-Latin` transform).** Fixed input → fixed output; offline; free; snapshot-testable to a known string; honors the determinism discipline (the LLM never does this).
- **E2 — LLM transliteration via the `ModelRouter` seam.** Handles context/edge cases, but non-deterministic, costs tokens, hard to gate at a fixed ≥95% with a deterministic test, and overkill for names (the LLM is reserved for *translation*/prose, an M3 concern).
- **E3 — ASCII-folding (`unidecode`-style).** Trivial, but lossy/inaccurate for Greek diacritics and polytonic forms.

**Provenance of the derived value:**
- **P1 — Inherit the upstream `SourceRef`.** The `el-Latn` value carries the same `source`/`license`/`attribution`/`bundleable` as the `el` value it was derived from (a produced work of the same source).
- **P2 — Invent a `derived`/`transliteration` `SourceRef.kind`.** First-class lineage, but a schema change (`SiteRecordV1` → V2) the card does not sanction, out of scope for the sliver.

## Decision Outcome

Chosen: **E1 (deterministic ICU Greek→Latin) + P1 (inherit upstream `SourceRef`)**, applied **only to the display-name field**, writing a new `names["<lang>-Latn"]` (e.g. `el-Latn`) `SourcedValue` and **never overwriting** the original-script key.

Drivers: a deterministic engine is the only one that makes SC-004's ≥95% bar **testable to a fixed expected output**, runs **offline/free**, and respects the rule that the LLM never performs this kind of arithmetic (it curates/writes prose, not coordinates or transliterations). Inheriting the upstream `SourceRef` keeps provenance **mechanical and correct** — a transliteration is a produced work of the source datum, so ODbL/CDLA attribution and the `bundleable` stamp carry through unchanged — **without** inventing a schema field the card forbids. Before deriving, the value's **script is validated against its declared BCP-47 language** (the FAIL-001 guard); a mismatch is normalised/flagged, never trusted. **Addresses are out of scope** (untrustworthy source scripts).

**Open sub-decision deferred to implementation (resolve-then-pin, ADR-0007 discipline):** the concrete package pin — PyICU (`icu.Transliterator`) vs a pure-python transliteration library — is chosen against what `uv` resolves and how heavy the C dependency is; the *approach* (deterministic, display-name-only, provenance-inheriting, script-validated) is what this ADR fixes. If a future slice needs first-class derived-value lineage (P2) or full multi-language translation, that is a `SiteRecordV2` / M3 decision with its own ADR — this sliver does not pre-empt it.

### Consequences

- Good: SC-004 is a deterministic, snapshot-gated test, not a judged one; no token cost; offline; original always preserved.
- Good: provenance stays correct and bundle-safe with zero schema change — the `el-Latn` value is as bundleable as its `el` parent.
- Good: the FAIL-001 script-validation guard is discharged with a regression test in this slice (Constitution IV).
- Bad / accepted cost: rule-based transliteration can be imperfect on names of foreign origin or polytonic Greek → the ≥95% bar (not 100%) absorbs this; the original script is always available as the fallback. A future language (Hebrew, M3) needs its own transform + guard — the *approach* ports, the *table* does not.
- Accepted: display-name only — addresses are deliberately not transliterated (FAIL-001), a scope limit, not a gap.

### Confirmation

- **`tests/test_translit.py`** (T1, deterministic): a fixed Greek fixture transliterates to the expected Latin string (snapshot); the original `el` value + attribution are unchanged; ≥95% coverage on the fixture set; a script-mismatch input (Cyrillic-in-`el`, the FAIL-001 shape) is flagged/normalised, not trusted.
- **Provenance-inheritance assertion** (in the same test): the `el-Latn` `SourcedValue.source` equals the `el` value's `SourceRef` (license/attribution/`bundleable` carried through), so `test_no_unbundleable_in_bundle` continues to hold on derived names.
- **FAIL-001 linkage**: this test is the regression eval that closes the FAIL-001 "source script untrustworthy" loop for the name path.
- **TODO (lands with DU-02/DU-03 implementation):** `commons/translit.py`, `tests/test_translit.py`, and the concrete transliteration package pin in `pyproject.toml`.
