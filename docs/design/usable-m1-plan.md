# Making M1 usable — a plan organised by journey, not by layer

**Status:** proposed · **Date:** 2026-08-15 · **Author:** claude-code · **For:** Ben

## Why this document exists

Nine deliverable units are merged, every one green in CI. The UX audit (`ux-audit-2026-08-14.md`,
PR #125) found the product **not usable on a phone**. Ben's follow-up is sharper and this
document answers it:

> *"the issue is not just UI, the functionality is also not complete — try to run flows like
> make a plan, research an area, tour an existing plan, and edit a plan on the go, to see it
> all makes sense."*

He is right, and the audit understated it. The audit measured **screens**. Walking the
**journeys** shows that two of the four cannot be completed *at any width, by anyone*, because
the endpoints do not exist.

**The organising principle here is: sequence by journey completion, not by layer.** Every
phase below ends with a journey a human can finish on a phone. Nine units of correct machinery
that no one can use is the failure mode this plan exists to stop repeating.

---

## The API surface, complete

Twelve endpoints. This is everything:

```
POST   /areas                          create + coverage
POST   /areas/{id}/research            SSE
GET    /sites?bbox=                    the commons for a viewport
POST   /plans                          SSE propose
GET    /plans/{id}                     one plan, by id you must already have
POST   /plans/{id}/approve             the HITL gate
POST   /bundles                        SSE compile
GET    /bundles/{id}/manifest
GET    /bundles/{id}/artifacts/{path}  range-friendly
GET    /auth/login · /auth/callback · POST /auth/logout
```

**What is missing is not a detail.** There is no `GET /plans`, no `GET /areas`, no
`GET /bundles`. **Nothing can be listed.** Every id exists only in the response that created
it: close the tab and the plan is unreachable forever. The app has no memory of what you made.

And there is **no edit route at all** — `commons/repository.py:1125::supersede_plan` is
implemented, tested, and carries the `failed → approved` re-arm and the stale-`compiling`
reaper, and **no HTTP path reaches any of it.**

---

## The four journeys

### 1. Research an area — *completable, badly*

Functionally works: delimit → research (SSE, ~40 s) → `GET /sites` → markers.

Broken by the audit's findings, in order of severity:
- **ODbL attribution occluded at every width, including 1440.** This is a **licence
  obligation**, not a UX nit — the one finding that is a compliance failure rather than a
  quality one.
- `Use this view` (the 0.18 s path) is occluded by the plan panel in **every scroll state** at
  375/390 px, so the only reachable route in is the search pill: **61.6 s** to a `404` whose 20
  disambiguation candidates `map/areas.ts:85` discards into a `console.warn`.
- Coverage card reads *"No cited places here yet"* while the server reports
  `known_site_count: 958`.
- Map gets **154 px (18.3%)** of a 390×844 screen; **0 of 957 markers visible**.
- No progress indication across a 40 s stream; the `degraded` path (Overpass times out
  regularly) is not surfaced.

### 2. Plan a day — *completable, then lost*

Functionally works end to end: propose (real model, Opus tier) → 5 Valhalla legs → feasibility
→ **approve**. Verified on the running stack.

But: **you cannot get back to it.** No `GET /plans`, no history, no list. The plan exists in
the database and is unreachable from the UI the moment the response scrolls away.

Plus the audit's emblematic defect: an element carrying `data-plan-state="proposing"` while
rendering **"No day has been proposed yet."** *The attribute a test asserts is right; the
sentence a human reads is the opposite.* That single line explains how nine units passed CI
while the product did not work.

### 3. Tour an existing plan — **NOT COMPLETABLE**

Three independent blockers, any one of which is fatal:
1. **No `GET /plans`** — you cannot find the plan to tour.
2. **No bundle download in the UI** — `POST /bundles` compiles a real 5.2 MB bundle today, and
   nothing in the app calls it.
3. **`web/src/travel/` (6 modules) and `web/src/bundle/` (10 modules) are not imported by
   `main.ts`.** Sixteen modules, built and tested, unreachable.

Also missing: the **OPFS URL rewriter**. The bundle deliberately ships bundle-relative paths
(`glyphs/{fontstack}/{range}.pbf`, `pmtiles://tiles/area.pmtiles`) because an offline artifact
cannot know its own origin; the client must rewrite them at load. That rewriter does not exist.

### 4. Edit a plan on the go — **NOT COMPLETABLE**

**No endpoint exists.** The repository layer is complete and unreachable.

This is the journey with the most machinery already built and the least of it exposed: the
state machine (`proposing → proposed → approved | superseded`), compare-and-set on the
itinerary hash, `EDITABLE_STATUSES`, revision chaining via `superseded_by`, the re-arm and the
reaper — all tested, all invisible.

---

## The plan

Four phases. **Each ends with a journey a person can complete on a phone.** Sizes are rough
and deliberately coarse.

### Phase A — the app remembers what you made *(unblocks journeys 2 and 3)*

The cheapest large win in the codebase, and everything downstream needs it.

- **`GET /plans`** — the caller's plans, newest first, user-scoped like every other read
  (`404`-not-`403`, indistinguishable from missing). Include `state`, `date`, stop count,
  `area_id`.
- **`GET /areas`** — same, so an area can be revisited without re-delimiting.
- **A "your plans" surface** in the web app: list → tap → the existing plan panel, which
  already renders a plan by id.
- Fix `map/areas.ts:85` to **use** the 20 disambiguation candidates the `404` already carries
  instead of dropping them into a `console.warn`.

*Ends with:* make a plan, close the tab, come back, find it, open it.

### Phase B — it is usable with a thumb *(fixes journeys 1 and 2)*

Straight from the audit's findings, hardest-blocking first.

- **Un-occlude the ODbL attribution at every width.** Licence obligation; do this first.
- **Un-occlude `Use this view`** — the 0.18 s delimit path, currently unreachable in every
  scroll state.
- **Tap targets to ≥44 px** (11 of 12 controls fail) and **body text to ≥14 px** at 375–430 px
  (`plan.css` ships 11.5–12.5 px).
- **Give the map its screen back** — 154 px / 18.3% with 0 of 957 markers visible is not a map.
- **Empty, loading and error states that tell the truth** — the `data-plan-state="proposing"`
  / "No day has been proposed yet" contradiction, the coverage card claiming zero against a
  reported 958, no progress across a 40 s stream, no surfacing of `degraded`.
- **Sign-in that is not a console paste.** Currently the only way in is pasting
  `document.cookie` into devtools. Either wire the Google SSO flow that already exists at
  `/auth/login`, or ship an explicit dev sign-in screen — but a product whose front door is
  devtools has no front door.

*Ends with:* research an area and plan a day, on a phone, with a thumb.

### Phase C — you can tour what you planned *(completes journey 3, and M1's gate)*

- **Wire `travel/` and `bundle/` into `main.ts`.** Sixteen modules, already tested.
- **The OPFS URL rewriter** — bundle-relative → OPFS URLs at load. Note MapLibre requires
  `style.sprite` **absolute** (FAIL-007): the absolute form is produced *by the rewriter at
  render time*, never baked into the bundle, which could not be portable if it were.
- **A download → compile → open flow** in the UI: approve → compile (SSE, six stages) →
  download to OPFS → open offline.
- **Close ADR-0031's missing half.** The manifest records `sha256` for `glyphs/` and
  `sprites/`; `web/src/bundle/types.ts` types glyphs as `{path, license?}` and `manifest.ts`
  drops unknown keys, so the launch check **silently ignores both digests**. A hash nothing
  verifies is a field, not a guarantee — and ADR-0031 is `accepted` while half-implemented
  precisely on this.
- **T056** — grow the airplane-mode e2e into the real gate. It now has a real bundle to gate
  on for the first time.

*Ends with:* tour a real day offline, with the network off. **This is M1's definition of done.**

### Phase D — you can change your mind *(completes journey 4)*

- **`POST /plans/{id}/edit`** (or `PUT`) reaching `supersede_plan`: writes a new revision,
  clears nothing, chains `superseded_by`, re-runs feasibility.
- **The client half**: an edit affordance on an approved plan, and honest handling of
  `409 plan_superseded` — the error already carries `supersededBy`, which is the actionable
  part and is currently thrown away.
- **A re-compile path**, since an edited plan supersedes and needs a new bundle. Note
  ADR-0033: `bundle_id = bnd_<plan_id.hex>`, one plan → one bundle, so a superseded plan's
  successor gets its own id for free.

*Ends with:* change a plan mid-trip and re-approve it.

---

## What "good enough" means, stated so it can be checked

Per journey, on a **390 × 844** viewport, one-handed:

1. **Research** — delimit in ≤3 taps, progress visible throughout, attribution never occluded,
   markers visible on the map.
2. **Plan** — request → review → approve without leaving the thumb zone; every control ≥44 px;
   every state message true.
3. **Tour** — find a past plan, download it, open it **with the network off**, see the day.
4. **Edit** — change an approved plan and re-approve, with the supersede chain visible.

No journey is "done" while any step needs devtools, a remembered UUID, or a desktop.

---

## Sequencing, and the one thing I would not do

**A before B.** Phase A is small and unblocks two journeys; doing CSS first would polish
screens you still cannot navigate to.

**C before D.** Touring is M1's release gate; editing is not.

**What I would not do:** fix the audit's 17 findings in severity order as a single pass. That
optimises for closing findings rather than completing journeys, and it is how we got here — a
backlog of individually-correct work that never added up to something usable. Every phase above
is defined by *a journey that starts working*, and should be verified the way the bundle was:
**by a person doing it, on a phone, not by a green suite.**

## Open questions for Ben

1. **Sign-in for M1** — real Google SSO, or an explicit dev sign-in screen? SSO needs a Google
   project and a redirect URI; the dev screen is hours. This gates every journey.
2. **Is Phase D in M1?** "Edit on the go" is in the PRD's promise, but touring is the release
   gate. D could be M2 without weakening M1's claim.
3. **RTL** — deferred, agreed. The delivery plan says **M3**, Ben said **M2**; the audit found
   **zero physical direction properties against 35 logical ones**, so the debt is not
   accumulating either way. Worth settling the number when convenient.
