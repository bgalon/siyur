# 0031 — Glyph and sprite artifacts carry their own integrity hash

- Status: proposed
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: Ben (2026-08-14, decision taken in session; ratification pending) · Date: 2026-08-14

## Context and Problem Statement

ADR-0025 ruling 2 made the bundle manifest's integrity story **per artifact**: one SHA-256 per path, no shared hashes, because "a hash spanning two files cannot name which one corrupted" (`contracts/bundles.md`). `contracts/bundles.md` enumerates the seven hashes that discharge it — tiles, walk graph, legs, sites, narrations, itinerary, attribution — and `integrity.manifest_sha256` seals the whole.

Implementing T035 revealed that the enumeration is **not** the set of files a bundle actually contains. `TileSourceV1` embeds two further artifact groups that ship inside the bundle and are read at render time:

- `style` — carried as `{path, sha256}`, hashed like everything else;
- `glyphs` — carried as `{path, license}`, with **no hash at all**;
- the sprite assets — carried **nowhere**. `TileSourceV1` has exactly one asset ref. The card mentions sprites only in prose ("`sprites` likewise"), and `compiler/tiles.py` writes `sprites/*` into the bundle with nothing in the manifest pointing at them.

So the vendored Noto glyph ranges have no integrity coverage, and the sprite sheet is not a manifest path at all. `compiler/tiles.py` can compute both digests — it writes every one of those files and already hashes each of them individually — and had nowhere in the schema to record the result.

The sprite finding is the more serious of the two, and it is not the gap T035b was written to close. An unhashed artifact is an integrity gap; an **unreferenced** one is an FR-021 gap — "everything the traveller depends on resolves to a manifest path", and the sheet the map draws its icons from does not. It was found only because implementing the hash required naming the field that would hold it.

**The failure this leaves open is silent and offline.** A corrupted or truncated glyph range is not a crash: MapLibre requests the range, gets bytes it cannot parse, draws no glyph, and reports no error. Every label on the map renders as nothing. The manifest still verifies, because `manifest_sha256` seals the *manifest*, and the manifest never claimed anything about those bytes. FR-020's launch-time integrity check — the mechanism whose entire purpose is "report an unusable bundle rather than render a partial day" — passes a bundle whose map is unreadable. The traveller is offline, so there is no reload that fixes it and no error to report.

This is the same class ADR-0025 gap 1 closed for the itinerary and `attribution.sha256` closed for the credits file: a thing the traveller depends on that is not covered by a manifest path's hash. It is being closed here rather than after a blank map is observed in the field, which is the only place it *can* be observed.

## Considered Options

- **A — Add `sha256` to the glyph and sprite refs as a required field.** Makes "one hash per artifact" true rather than nearly true. Uniform with `style`, which is the neighbouring field in the same model and already works this way.
- **B — Add `sha256` as an optional field.** Non-breaking, lands without touching any existing construction site. But a manifest that omits it still validates, so the gap is *closable* rather than closed — and the omission is invisible, which is the property that made this bug survive design review in the first place.
- **C — One directory-level digest per ref rather than one per file.** The ref becomes `{path, license, sha256}` where the digest is taken over the directory's `[[path, sha256], …]` listing, canonicalized. Catches mutation, addition, deletion and rename; order-independent.
- **C′ — One digest per individual glyph range and sprite asset**, i.e. the ref grows `files: tuple[ArtifactRef, ...]`. This is what ADR-0025 ruling 2's letter asks for.
- **D — Defer to a follow-up task, ship T035 without the recording half.** Keeps this change purely additive. Rejected: the digest is already computed, the schema is the only thing missing, and a known integrity hole tracked in a task list is still a known integrity hole shipped in a bundle.

## Decision Outcome

Chosen: **A — `sha256` is a required field on the glyph ref and the sprite ref**, matching `style`. The sprite ref is **created** by this ADR rather than amended, since none existed; `TileSourceV1` gains `sprites` alongside `glyphs`, both carrying `{path, license, sha256}`.

The version stays `V1` on ADR-0025's own precedent and for its own reason: `BundleManifestV1` has no persisted instances — `compiler/` reached first implementation in this slice — so this is a **correction to a specification before first use**, not a migration of a live contract. Required rather than optional is affordable precisely because nothing durable exists to break, and that window closes the moment a bundle is compiled and stored.

Required is also the only option that is self-enforcing. `commons/models.py` models are `extra="forbid"` and frozen; a required field means a construction site that forgets the hash fails validation at the moment it is written, in mypy and in the test suite. An optional field means it fails offline, on a phone, as a map with no labels.

**One digest per ref, over the directory listing — option C, not C′.** This is a deliberate departure from ADR-0025 ruling 2's letter, and the reason it does not violate its spirit is worth stating.

Ruling 2 rejected group hashes because "a hash spanning two files cannot name which one corrupted". That rationale is *diagnostic*, and it is load-bearing where the answer changes what happens next: `content/sites.json` and `content/narrations.json` corrupting are different events with different remedies. Here it is true and **not actionable**. The traveller is offline. They cannot refetch one glyph range, and there is no partial-recovery path in which knowing *which* of 342 files moved changes the outcome — the bundle is unusable either way, and FR-020's required response ("report an unusable bundle") is identical. Against that, C′ adds several hundred entries to the manifest for a CJK area and lengthens the JCS seal, for a distinction nobody can act on.

The directory digest is taken over the canonicalized `[[path, sha256], …]` listing, so it detects mutation, addition, deletion **and** rename, and is order-independent. What ruling 2 actually protects — that a corrupted artifact is detected rather than silently rendered — is fully preserved. The unit of integrity here is the glyph set, because the glyph set is the unit of use.

*(Recorded because the drafted version of this ADR specified C′ and the implementation shipped C. The implementation was right; this text moved.)*

**The card is amended in the same change as the model.** `docs/data/tile-source.md` is field-level ground truth and `AGENTS.md` says never guess a schema, read the card. A model that has moved ahead of its card reintroduces the ambiguity the card exists to remove — and `GlyphsRef`'s own docstring said "Do not add one ahead of the card", which is the rule being honoured here rather than an obstacle being worked around.

## Consequences

- Every bundled artifact now has an integrity hash, so FR-013 and FR-020 are satisfiable for the whole bundle rather than for most of it. A corrupted glyph set is **detected at launch** and reported as an unusable bundle instead of rendering as a map with no labels.
- `docs/data/tile-source.md` and `commons/models.py` are amended together; the amendment is dated in the card in the style ADR-0025's amendments established.
- Any construction of a glyph or sprite ref must now supply a digest. Every such site is inside `compiler/tiles.py` and its tests at the time of writing, so the blast radius is one module.
- A test asserts the hash **changes when a glyph file's bytes change**. A recorded digest that nothing verifies is not integrity, and this ADR would otherwise have added a field rather than a guarantee.
- **Splitting the ref exposed a live licensing defect, which this change also fixes.** Glyphs are OFL-1.1 and the sprite sheets are **MIT**, but with only one ref to read from, `compiler/attribution.py` emitted a single credit — "Map glyphs and sprites" — under `glyphs.license`. Every bundle therefore credited MIT-licensed assets under OFL-1.1 and asserted OFL's "don't sell fonts standalone" restriction over them. `DATA-LICENSES.md` carries the same conflation in its "Noto glyphs / sprites" row. Attributing a work under a licence that is not its own is a compliance failure in the artifact the traveller downloads, and AGENTS.md makes licence compliance an engineering practice rather than a documentation one. Now that `sprites.license` exists the credit becomes two rows and the registry row splits in two. *(The bug predates this ADR; it was undiscoverable while one field stood for two licences.)*
- `docs/data/bundle-manifest.md`'s two worked examples embed a whole `TileSourceV1` and become schema-invalid under this change; both are amended in the same commit. A wrong example in an authoritative card is worse than a silence, because a transcriber copies the example.
- `compiler/manifest.py`'s duplicate-path refusal is extended to the new `tiles.sprites.path`. A new bundle path that escapes the uniqueness check is a way for two artifacts to claim one path and for one of them to be silently unreachable.
- **The client does not yet verify these hashes, and until it does the manifest claims coverage it does not have.** `web/src/bundle/types.ts` types `glyphs` as `{path, license?}` and `web/src/bundle/manifest.ts` drops unknown keys, so the launch-time integrity check ignores both new digests. Nothing breaks and no typecheck fails — which is the problem: FR-020's guarantee is recorded in the bundle and not enforced on the device. **This ADR is not fully discharged until `web/` verifies both digests at launch**, tracked as follow-up work in `web/`'s lane. Recording a hash nothing checks is a field, not a guarantee, and is arguably worse than the honest gap it replaced.
- This does not revisit ADR-0021 (Protomaps / `pmtiles extract`) or the glyph licence position (OFL-1.1, registered in `DATA-LICENSES.md`). Only the integrity coverage and the sprite reference change.

## Confirmation

Satisfied on the producing side when `docs/data/tile-source.md` shows `sha256` on both refs with a dated amendment note, `commons/models.py` requires it, a compiled bundle's manifest carries one directory digest per ref, and a test proves that mutating **or deleting** a vendored file produces a different digest — computed over the bytes actually written into the bundle, not over a source file that happens to be copied there. A hash of the wrong bytes verifies cleanly and protects nothing.

**Fully discharged** only when the launch-time check in `web/` rejects a bundle whose glyph or sprite bytes do not match the recorded digest. Until then the producing side is correct and the guarantee is unenforced, and this ADR should be read as half-implemented rather than done.
