# FAIL-014 — An ADR marked `accepted` with a known open half, and nothing to force it shut

- Date: 2026-08-15 · Severity: medium (no corruption observed; an integrity guarantee the bundle
  displays and does not enforce)
- Root-cause class: **a consciously deferred gap with no mechanism that makes the deferral expire**

> **Read this first, in fairness to the decision.** ADR-0031 does **not** overlook the reader
> side. Its own header says so, in bold, at the top of the file:
>
> > *"Accepted while half-implemented, deliberately. The producing side is complete and verified;
> > `web/` records both digests without verifying them, so the bundle currently states integrity
> > coverage it does not enforce. … this ADR is not discharged until the launch-time check rejects
> > a mismatched glyph or sprite set."*
>
> So this entry is **not** "nobody noticed". It is the narrower and more useful failure: the gap
> was identified, written down precisely, and given **no owner, no ticket and no failing test** —
> and the ADR's status field says `accepted`, which is what every index, every reader and every
> future agent sees. The catalog entry exists because a known gap with no forcing function is
> indistinguishable, six weeks later, from one nobody spotted.

## Symptom

ADR-0031 — "glyph and sprite artifacts carry their own hash" — is `accepted`. The compiler
honours it. The schema card specifies it:

```jsonc
// docs/data/bundle-manifest.md:137-138
"glyphs":  { "path": "glyphs/",  "license": "OFL-1.1", "sha256": "3d5e…" },
"sprites": { "path": "sprites/", "license": "MIT",     "sha256": "0b71…" }
```

```python
# compiler/tiles.py:930-937 — directory digests, computed and recorded
glyphs_sha256: str    # directory_digest over :attr:`glyphs`; recorded at source.glyphs.sha256
sprites_sha256: str   # directory_digest over :attr:`sprites`; recorded at source.sprites.sha256
```

The client that is supposed to check them:

```ts
// web/src/bundle/types.ts:53-54
readonly style?: HashedArtifact                                        // { path, sha256 }
readonly glyphs?: { readonly path: string; readonly license?: string } // <- no sha256
                                                                       // <- no `sprites` at all
```

`bundle/manifest.ts` builds its typed view by reading known keys and dropping the rest. So both
directory digests are parsed out of existence, and **the launch-time integrity check silently
ignores them**. Note `style` immediately above, typed as `HashedArtifact` and verified correctly —
the right pattern is on the adjacent line.

## Why nothing caught it

- **The compiler's tests pass**, because the compiler is correct: it computes both digests and
  writes them where the card says.
- **The client's tests pass** (`web/test/bundle.test.ts`, 488 lines), because they assert the
  behaviour of the type as written. A field the type does not declare is a field no test misses.
- **The manifest digest still verifies.** `integrity.manifest_sha256` is taken over the **raw
  parsed JSON** (`manifest.ts:25-30`, deliberately, and correctly) — so the manifest as a whole
  hashes fine, including the two digests the client then discards. The one check that *does* run
  passes, which makes the absent checks look present.
- **The ADR's own warning is not executable.** It is a bold paragraph in a Markdown file with
  status `accepted`. `docs/adr/README.md` lists it beside genuinely discharged decisions; the
  ratification commit (`c9c7dac`, "ratify ADR-0031, 0032 and 0033") reads as completion. Nothing
  in CI, no `status: partially-implemented`, no linked issue, and no red test encodes "this is not
  done yet" — so the only carrier of that fact is prose that a reader has to open the file to
  find.

The result is the failure mode the audit report names: **a hash nothing verifies is a field, not
a guarantee.** Worse than absent, because the manifest *displays* integrity that is not enforced.

## Root cause

An integrity decision is only discharged by a **reader that refuses on mismatch**. Producing a
digest is bookkeeping; checking it is the control. ADR-0031 says exactly this and then ships the
bookkeeping half — a reasonable call, since the reader did not yet exist to put the check in
(`bundle/` is unreachable from `main.ts`, FAIL-012 context).

The failure is in what carried the remainder. **`accepted` is a binary status doing the work of a
tri-state.** A decision that is ratified-and-complete and one that is ratified-with-a-named-hole
are recorded identically, so the hole survives only as prose inside the file, and every summary
view of the project — the ADR index, the changelog, the ratification commit — reports it as done.

The seam makes it quieter still: `types.ts` being narrower than the schema card is not an error in
any tool this project runs. It type-checks. It lints. Its tests pass. Being narrower than the card
is simply an unremarked way of saying "we do not check this."

## Guardrail

**Derive the client's expectations from the schema card, so a field cannot be dropped silently.**

1. **A conformance test over the card's own example.** Parse the manifest example embedded in
   `docs/data/bundle-manifest.md` and assert that **every `sha256` appearing anywhere in it
   survives `parseManifest` and is reachable on the typed result.** This is the general form —
   it fails for glyphs and sprites today, and it will fail for the next artifact someone adds a
   digest to and forgets to read. Extracting the fenced JSON from the card keeps the card
   authoritative rather than duplicating it into a fixture (AGENTS.md: never guess a schema, read
   the card).

2. **The launch check verifies every digest it holds.** Assert that `verifyBundle` refuses a
   bundle whose `glyphs`/`sprites` directory digests do not match — a red test first, against a
   deliberately corrupted fixture, so the check is demonstrated to be load-bearing before it is
   allowed to pass.

3. **Type the two fields properly:** `glyphs?: HashedArtifact & { license?: string }` and add the
   missing `sprites` entry, matching `style` on the line above.

4. **An ADR that is knowingly part-implemented must not read as `accepted`.** This is the
   guardrail that generalises past this bundle. Add a status the index can show — e.g.
   `accepted (partially implemented)` — **required** whenever an ADR's Confirmation section names
   a check that does not yet exist, and a CI lint over `docs/adr/*.md` that fails when a file
   claims plain `accepted` while its own body says it is not discharged. ADR-0031 stated its
   remainder impeccably; what it could not do was make that statement visible from anywhere other
   than inside itself.

**This entry does not close until (1) and (2) are in CI**, and (2) has been observed failing on a
corrupted fixture.

**Sequencing note:** this work lands naturally with F-09 (wiring `bundle/` into `main.ts`), which
is the first time the client meets a **real** 5.2 MB bundle rather than the synthetic fixture
derived from the card. Expect other divergences there; each one is a finding about the fixture,
not a licence to change the bundle.

## What it cost, stated plainly

Nothing yet — the offline bundle surface is not reachable from the running app (`FAIL-012`
context; the client has never opened a real bundle). That is precisely why it is worth writing
down now: the cost of this defect is **entirely in the future**, and it is the kind that surfaces
as "the map renders blank glyphs on a phone in airplane mode with no diagnostic", six days from
the change that caused it, in the one situation where the user cannot refetch anything. FAIL-010
was six days of a Docker volume serving the wrong graph. This is the same story with the detector
switched off.

## Related

- **ADR-0031** — the decision whose reader half is still open. It named the gap correctly and in
  the right place; the lesson is for the ADR *status vocabulary*, not for the ADR's authors.
- **FAIL-010** — wrong data served silently for six days, invisible to every health signal.
- **FAIL-007** — the same client/server seam, on sprite URL absoluteness.
- `docs/design/ux-audit-2026-08-15.md` — finding UX-17, fix F-10.
</content>
