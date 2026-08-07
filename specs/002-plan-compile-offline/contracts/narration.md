# Contract — Narration (story ingestion into the commons)

**Not an HTTP surface.** This is an **ingestion contract**: the shape a `Story` must have to enter the commons, and the rules that refuse it if it does not. **Producer**: `commons/sources/wikivoyage.py` (MediaWiki Action API over Wikivoyage/Wikipedia) → `planner/nodes/narrate.py` (adapts the fetched prose over the model seam) → `SiteRecordV1.stories[]`. **Auth**: ingestion runs behind the same auth-gated write path as research (ADR-0008); the *stories* it writes are **shared commons**, not personal data. Schema: [`docs/data/poi-site.md`](../../../docs/data/poi-site.md) `Story` / `SourceRef` — the card wins. Registry of record: [`/DATA-LICENSES.md`](../../../DATA-LICENSES.md). Maps to **FR-023, FR-024, SC-010, US4** and **ADR-0024** (`proposed`).

## The shape entering the commons

```jsonc
{
  "text_by_lang": { "en": "The cobbled street once housed the …" },   // `en` canonical at M1; translations M3
  "source": {                                                        // a SourceRef: these five keys and NO others
    "kind": "wikivoyage",                                            // wikivoyage | wikipedia — nothing else at M1
    "id": "en:Rhodes",                                               // ALWAYS "<lang>:<Page Title>", never bare
    "url": "https://en.wikivoyage.org/wiki/Rhodes?oldid=4812301",    // credit link, pinned to the revid
    "license": "CC-BY-SA-4.0",
    "attribution": "\"Rhodes\", Wikivoyage, https://en.wikivoyage.org/wiki/Rhodes — authors via page history"
  },
  "observed_at": "2026-08-07"                                        // the revision timestamp — on Story, NOT SourceRef
  // `claims: [{span, SourceRef}]` — per-claim provenance — is M2+ and stays empty here.
}
```

**`id` is lang-qualified, always.** `tests/test_compiler_attribution.py` asserts every contributing article is credited
**exactly once** and dedupes on `source.id`; a bare `Rhodes` from one adapter and `en:Rhodes` from the other would
credit one article twice. **`SourceRef` carries no `observed_at` and no `bundleable`** — it is `extra="forbid"` with
exactly five fields, so both belong elsewhere: `observed_at` on the `Story`, and `bundleable` nowhere at all, because a
`Story` is not a `SourcedValue` and its bundleability is **derived** via `licenses.bundleable(kind, license)`.

**Refused at the boundary — not warned about, refused:**

- **No `source` ⇒ no story.** A `Story` without a `SourceRef` never reaches a row, the same refusal `commons/repository.py::CommonsWriteRefused` already applies to unstamped values (Constitution Article V; continuous with slice 001 FR-003).
- **`attribution` is mandatory for CC BY-SA.** The license *requires* credit, so a null `attribution` under `CC-BY-SA-4.0` is a refusal, not a nullable field. **Zero stories exist without attribution** (SC-010).
- **`id` and `url` identify one article.** A story is credited to the article it was adapted from; a story assembled from prose whose article cannot be named is refused.
- **`bundleable` is derived, never author-set.** `Story` carries no stamp fields of its own (see *Undetermined* below); its bundleability is computed from `source.license` + `source.kind` through the same equivalence as every other value (`commons/licenses.py::bundleable`) — `CC-BY-SA-4.0` is in the allowlist, `open_web` and `review_provider` never are.
- **No `kind: "user"` story.** A user-authored story is personal data and belongs in `user_note`, never in the commons record.

## Per-article credit and the share-alike obligation

- **One story per contributing article, each credited individually** (FR-024). Two articles about one place produce **two** `Story` entries with two `SourceRef`s — never one merged story with a merged credit line, which would make the credit untraceable.
- **CC BY-SA share-alike is contagious on the text.** Prose adapted from a CC BY-SA article is itself a derivative work and is **stamped and redistributed as `CC-BY-SA-4.0`**. Siyur accepts that obligation deliberately (PRD §7 posture (a), rich narration). It attaches to the **narration text only** — it does **not** infect app code, map styles, tiles, or the coordinates and names that are facts rather than expression (`DATA-LICENSES.md`).
- **The obligation is discharged mechanically**: `compiler/attribution.py` regenerates `ATTRIBUTION.md` per bundle with **one credit entry per contributing article** (title, URL, license), and the narration renders its own credit alongside the text — offline, from the bundle, with no network (see [`bundles.md`](./bundles.md)).
- **Quarantine applies unchanged.** Stories pass the same filter as every other value at compile: anything not `bundleable` is **removed**, and the place still appears with what survives, presented as *needing connectivity* rather than as an error or a blank (FR-011/FR-021).

## No article ⇒ no story, and nothing invented

- Where **no openly-licensed article is available for a place, the place carries no story**. `stories: []` is a valid, correct, expected outcome — not a gap to be filled (FR-023 / spec US4 scenario 3).
- The model's only permitted act is **adapting prose that is present in the fetched article**. It MUST NOT author a story for a place with no article, MUST NOT add a fact absent from the source article, and MUST NOT emit coordinates, distances, opening hours or times — those come from Overture/OSM, Valhalla and `commons/opening_hours.py`, never from narration (determinism discipline, AGENTS.md geo rules).
- A place whose article exists but yields nothing usable (a stub, a disambiguation page) is the **no-story** case, not a licence to synthesise one.
- Narration is **additive**: it never overwrites `names`, `location`, `categories`, `address` or `opening_hours` on the record it attaches to.

## Contract tests (T1 unit + deterministic eval + nightly judge)

- A `Story` with a null `source`, or with `CC-BY-SA-4.0` and a null `attribution`, is **refused** at the commons boundary (T1).
- A fixture site with two contributing articles ⇒ two `Story` entries, two `SourceRef`s, two credit lines in `ATTRIBUTION.md` (SC-010).
- A fixture place with **no** article ⇒ `stories: []` and **zero** generated text — the no-invention eval, mirroring slice 001's "zero fabricated places" (SC-006 lineage).
- A story stamped with a non-allowlisted license ⇒ dropped by the compile quarantine filter; the site still bundles, and the drop is recorded in `withheld[]` (`test_structural.py::test_no_unbundleable_in_bundle`).
- Prose quality (readability, faithfulness to the source article) is judged by an **LLM judge, nightly and non-blocking**, with a pinned judge model — narration is the first genuinely non-deterministic output in the product, so it is deliberately **not** a merge gate (Constitution Article II tiering; plan.md).

**Resolved since drafting (ADR-0025, 2026-08-07)**: (1) `Story` **keeps** its bare `SourceRef` and is not promoted to a `SourcedValue`; quarantine **derives** bundleability via `licenses.bundleable(kind, license)` (ruling 8). The rule is structural, not a `Story` special case — `RouteLegV1`, `ResolvedArea` and `AreaCandidate` are the same shape and derive the same way. `Story` gains **`observed_at`** as its staleness key. (2) The card's "M1 / ≥1 story" is restated as a **fill-set aspiration, not a validation rule** (ruling 11): a place with no openly-licensed article carries no story and is valid and complete.

**Still undetermined — flagged, not decided**: nothing in spec or cards bounds a story's **length** or defines "adapted" against verbatim copying. `prompts/narration.md` v1 owns that, and the share-alike stamp holds either way — but "adapted" doing no work would make the bundled text a straight copy, which CC BY-SA permits and the product should still not want.
