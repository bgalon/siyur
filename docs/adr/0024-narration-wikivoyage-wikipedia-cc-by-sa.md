# 0024 — Narration comes from Wikivoyage + Wikipedia over the MediaWiki Action API, and share-alike lands on the bundled text

- Status: proposed
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: _pending_ · Date: 2026-08-07

## Context and Problem Statement

Spec 002 Q1 (resolved by Ben, 2026-08-07, option **A**) lands **story ingestion** in this slice: FR-023 requires that where an openly-licensed encyclopedic article exists for a place, the record carries a readable adapted account **with that article's own attribution**, and where none exists the place carries **no story and nothing is invented**. FR-024/SC-010 set the bar at **zero bundled stories without attribution**. `docs/data/poi-site.md` already types `SiteRecordV1.stories` as `[Story]` with a `SourceRef` whose "attribution required" — slice 001 deferred the fill to here.

Two things were left unstated and both are load-bearing for a slice whose whole point is a **bundle handed to a traveller offline**:

1. **Which source, reached how.** `Story` is typed but no adapter exists; `SourceRef.kind` already contains `wikivoyage` and `wikipedia`, but nothing says what fetches them or what a "per-article attribution" concretely is.
2. **What share-alike obliges once the prose is adapted and shipped.** This is the part that cannot be discovered later. `plan.md` Risk 4 names it directly: *"CC BY-SA share-alike is contagious."* A bundle is a distribution. If the obligation is understood only after bundles exist, the fix is retroactive and the compliance gap is already shipped.

PRD §7 fixed the **narration posture** as **(a) rich, CC BY-SA bundled text with per-article attribution** — a standing decision in `AGENTS.md`. **This ADR records the obligation that posture creates; it does not re-open the posture.**

## Considered Options

**Source and access:**
- **S1 — MediaWiki Action API over Wikivoyage + Wikipedia.** Free, no key, an honest descriptive User-Agent, results cached in the commons. Wikivoyage gives travel prose *and* structured listing templates; Wikipedia gives `list=geosearch` plus summary extracts. Both **CC BY-SA 4.0**, both already in `SourceRef.kind`.
- **S2 — Wikimedia Enterprise (Travel API).** Commercial, SLA-backed. The free APIs are ample at slice volume, and a paid dependency in the ingestion path is a cost the product does not need at M1.
- **S3 — Scrape rendered HTML.** Brittle against skin/parser changes, and it **discards the listing-template structure** — the structured prize is in the wikitext, not the rendering.
- **S4 — Open-web prose.** Always `bundleable=false` under `DATA-LICENSES.md`; a narration source that can never enter a bundle is not a narration source for this product.
- **S5 — Facts-only narration from Wikidata (CC0).** Zero share-alike obligation, and a thinner product — this is PRD §7 posture **(b)**, already rejected by the standing decision.

**Parsing:**
- **P1 — `mwparserfromhell` over the wikitext.** The stack reference §4 preference; the listing templates parse as templates rather than as guessed HTML.
- **P2 — Regex over wikitext.** Re-deriving a messy grammar; the standard way to produce silently wrong extractions.

**Attribution granularity:**
- **A1 — Per article, per `revid`.** The credit points at the exact revision adapted.
- **A2 — Per article only.** Simpler, but the credit then points at a moving target: the article changes and the recorded attribution no longer describes what was actually adapted.
- **A3 — Per contributor.** Wikimedia's own reuse guidance does not require it (a link to the article or its history is accepted), and it would mean fetching and bundling full contributor lists.

## Decision Outcome

Chosen: **S1 + P1 + A1** — the **MediaWiki Action API** over **Wikivoyage** (listing templates + prose) and **Wikipedia** (`list=geosearch` + summary extracts), parsed with **`mwparserfromhell`** (resolved-then-pinned per ADR-0007), attribution captured **per article and per revision** into the `SourceRef` **at ingestion**.

`commons/sources/wikivoyage.py` is a **source adapter exactly like the Overture and Overpass ones** (ADR-0009): it **stamps at the boundary**, so nothing above it is ever unstamped, and the quarantine filter above it needs no source-specific knowledge.

**The stamp, concretely:**

```jsonc
{ "kind": "wikivoyage" | "wikipedia",
  "id":   "<lang>:<Page Title>",
  "url":  "<canonical article URL>",
  "license": "CC-BY-SA-4.0",
  "attribution": "\"<Title>\", <Wikivoyage|Wikipedia>, <url> — authors via page history",
  "observed_at": "<revision timestamp>",
  "bundleable": true }
```

The **`revid`** is fetched with the extract and stored, so the credit points at the exact revision adapted. Wikimedia's reuse guidance accepts a link to the article or its history as author attribution; the revid is what makes that link **honest** rather than approximately true. Both `kind` values already exist in the `SourceRef.kind` enum (`docs/data/poi-site.md`) — **no schema change is required for the source stamp**.

### The share-alike obligation, stated plainly

**Adapted prose from a CC BY-SA 4.0 article is a derivative work. Therefore the bundled story text is itself CC BY-SA 4.0, and the bundle must say so.**

This is the sentence that must not be discovered after the fact. Three consequences follow from it and are the substance of this ADR:

1. **The bundle carries a license statement for its narration text, not merely a credit line.** `ATTRIBUTION.md` gains a block — `Narration text: CC BY-SA 4.0 — adapted from:` — listing **every** contributing article by title and URL, and the manifest carries `textLicense: "CC-BY-SA-4.0"`. Attribution without the license statement discharges BY and not SA.
2. **The obligation is viral over the text only.** It reaches the adapted story prose and anything derived from it. It **does not** reach the application code, the MapLibre style, the PMTiles basemap, the walk graph, the route legs, or the itinerary data — those are separate works with their own licenses (MIT/BSD/ODbL/user-owned), assembled alongside the text in one archive rather than merged into it. A bundle is a **collection**; share-alike travels with the CC BY-SA component, not with the container. Stating this bound explicitly is as important as stating the obligation, because the failure mode of an unbounded reading is abandoning bundled narration entirely — which would silently revert PRD §7 to posture (b).
3. **Discharge is mechanical, not editorial.** `compiler/attribution.py` regenerates `ATTRIBUTION.md` per bundle from the `SourceRef`s actually present in the frozen content. Nobody maintains a list by hand; a story that reaches the bundle without a resolvable article credit is a **test failure**, not a review miss.

**The rest of the quarantine rules apply unchanged.** A story with no `SourceRef` is refused by the same filter as any other unstamped value (FR-012) — `CC-BY-SA-4.0` is in the bundleable allowlist, so an *unattributed* story would otherwise bundle silently, which is exactly the hole SC-010 exists to close. A place with **no available article carries no story and nothing is invented** (FR-023). **Wikidata (CC0) remains the preferred machine-*facts* source and is not a narration source** — its value is facts without a share-alike obligation, and mixing the two roles would put CC0 facts and CC BY-SA prose in one undifferentiated blob.

**Scope / non-goals**: this fixes the **ingestion** source, its access method, its stamp, and the share-alike discharge. The narration **generator** with per-claim provenance is **M2** (spec Q1 = A: `Story.claims` stays empty in this slice). Translation and multi-language narration are M3. Adding a third openly-licensed narration source later is one adapter behind the same boundary, not a re-decision — but a source under a *different* license would need its own row in `DATA-LICENSES.md` and its own attribution block.

### Consequences

- Good: FR-023/FR-024/SC-010 are enforceable rather than aspirational — every bundled story's credit is derived from a stamp applied at ingestion, and the generated `ATTRIBUTION.md` is a function of the bundle's own content.
- Good: no schema change for the source stamp — `wikivoyage` and `wikipedia` are already `SourceKind` values, so the adapter composes with the existing quarantine filter untouched.
- Good: the licence obligation is **smaller than it looks and now written down** — text-only, discharged by a generated file, with the bound on virality stated so a later reader does not over-apply it to code or tiles.
- Good: per-`revid` attribution makes staleness answerable: the article moved on, and the bundle can say which revision it adapted.
- Bad / accepted cost: adapted prose is a derivative, so **Siyur cannot relicense its narration text** — no proprietary or CC-BY-only distribution of the story text, ever, without regenerating it from a non-share-alike source. That is a permanent product constraint accepted as the price of PRD §7 posture (a).
- Bad / accepted cost: `ATTRIBUTION.md` grows with the number of contributing articles, and the manifest gains a `textLicense` field — a small addition to `BundleManifestV1` that must be reconciled with the amendments in **ADR-0025**.
- Bad / accepted cost: a live third-party dependency in the ingestion path. Wikimedia's API is reliable and we cache into the commons, but ingestion must degrade like the Overpass adapter does (ADR-0009) — partial results, never a hang, and never a fabricated story to fill the gap.
- Accepted: `Story` is not a `SourcedValue` and carries no `bundleable` stamp of its own; bundleability is **derived** from `licenses.bundleable(kind, license)`. That asymmetry is real and is ruled on in **ADR-0025** (gap G4), not here.
- Accepted: `SH`-free, `claims`-free, English-only at M1. Narration prose is also the first genuinely non-deterministic output in the product, so its *quality* gate is a nightly LLM judge (Article II tiering), while its *attribution* gate is deterministic and merge-blocking.

### Confirmation

- **`evals/test_structural.py`** (deterministic, merge-blocking): every bundled `Story` has a non-null `SourceRef` with `license == "CC-BY-SA-4.0"` and a resolvable article URL — **SC-010 = zero stories without attribution**. The existing `test_no_unbundleable_in_bundle` quarantine invariant is extended to see story text, so an unstamped story is dropped rather than shipped.
- **`tests/test_compiler_attribution.py`**: the generated `ATTRIBUTION.md` names **every** contributing article **exactly once**, and **declares the text license** (`CC BY-SA 4.0`) — the SA half of the obligation, asserted separately from the BY half so a regression that drops the license line cannot pass on credits alone.
- **`tests/test_sources_wikivoyage.py`**: a committed MediaWiki API fixture (no network in CI, per test-strategy) yields a `Story` whose `SourceRef` carries the article title, canonical URL, `revid`-derived `observed_at`, and the rendered credit; a page with no article yields **no story**, not an empty-string one (FR-023).
- **TODO (lands with DU-04.5):** `commons/sources/wikivoyage.py`, `planner/nodes/narrate.py`, `prompts/narration.md` v1, `compiler/attribution.py`'s narration block, the committed MediaWiki fixture, and the `mwparserfromhell` pin.
