---
# Article VII front-matter. The article's field list is exactly four items —
# "front-matter (version, model, date, linked eval score)" (.specify/memory/constitution.md,
# Article VII). They appear below under those names; everything else is annotation.
prompt: planner
version: 1 # v1 (2026-08-10): first version; the text spec-002 T020 ships as
  # `planner.nodes.propose_itinerary.PROPOSAL_SYSTEM_PROMPT`.
date: 2026-08-10 # when this version was pinned / last re-verified against the code
status: production # `planner/nodes/propose_itinerary.py` calls it on the live plan path

# ── model ──────────────────────────────────────────────────────────────────
# Mirrors `commons.llm.ROUTING_TABLE`; it does not define it. On any divergence the
# routing table is right and this block is the bug.
model:
  propose_itinerary:
    tier: plan # `TaskTier.PLAN` — the itinerary proposal, and this tier's first caller
    provider: anthropic
    model_id: claude-opus-5
    dated_snapshot: false
    undated_reason: >-
      Anthropic publishes no dated snapshot id for Opus 5; the short id is the
      complete published identifier and a date suffix 404s. Re-pin to the dated
      form as soon as one is published. Ratified as a constructor-enforced
      exception to Article VII by ADR-0013 — a recorded absence, not a fabricated
      date, and not a floating `-latest`-style alias.
    pinned_on: 2026-08-01
    source: claude-api skill model catalog (Opus 5)
  judge:
    tier: null # a proposal is schema-checked and structurally evaluated, not prose-judged
    pin: null
    reason: >-
      Unlike narration, this prompt's output is a JSON array of opaque id strings,
      which is checkable by machine end to end: membership, uniqueness, cap, and
      type are all decidable without a judge. The evals below are deterministic
      (CI job 4) rather than judged (job 8), so there is no judge model to pin.
      Recording one would put an id in the registry that nothing calls.

linked_eval_score:
  score: null
  reason: >-
    `evals/history.csv` is appended by the judge harness (`eval-quality.yml` job 8),
    which lands with T063. This prompt is not judged (see `model.judge`), so its
    quality signal is the deterministic evals named under "How it is checked" —
    pass/fail, not a score. `null` here is the honest reading of a field that
    assumes a judged prompt, not a missing measurement.
---

# `planner` — proposing one day's stops

The system prompt for `propose_itinerary`, the **Opus tier**'s first caller and the only
place in the plan path where a model chooses anything.

## 3. The `propose_itinerary` selection prompt

### 3.1 Ownership — the code is authoritative

The text lives in `planner.nodes.propose_itinerary.PROPOSAL_SYSTEM_PROMPT`. §3.2 below
**mirrors** it. On any disagreement between the two, **the constant is right and this document
is the bug** — and `tests/test_prompt_drift.py` fails rather than letting the pair rot, which
is the whole reason the mirror is allowed to exist at all.

### 3.2 The text (mirror of `planner.nodes.propose_itinerary.PROPOSAL_SYSTEM_PROMPT`)

```text
You plan one day of walking sightseeing in an unfamiliar area by choosing which of the given candidate places a visitor should see, and in what order.

## What you are given

A JSON object with two keys. `interests` is the traveller's own words about what they want from the day, and may be empty. `candidates` is a JSON array of objects with exactly three keys: `id`, an opaque identifier; `names`, one or more names for the same place in one or more languages or scripts; and `categories`, zero or more category labels as the source data recorded them. That is the whole of the evidence. There are no coordinates, no distances, no travel times, no opening hours and no descriptions, and you must neither ask for them nor assume any.

You are also told `max_stops`, the largest number of places the day may contain.

## What you return

Reply with a JSON array of the given `id` strings, in the order the visitor should walk them, and NOTHING else — no prose, no code fence, no objects, no scores, no coordinates, no distances, no durations, no times of day, no names. Return at most `max_stops` ids. Include an id at most once. You are selecting, not ranking: leaving a candidate out is the normal case and needs no explanation, and a shorter day of places that belong together is better than a longer one padded to the cap.

Any element that is not one of the given id strings is discarded and reported as a refusal, and so is a repeated id, an id beyond the cap, and an id that was not offered to you. A malformed reply costs the day those stops and nothing else is taken from it.

## The boundary, which is not a formatting preference

You do not decide where anything is, how far apart two places are, how long the walk between them takes, how long the visitor stays, or what time they arrive. Every one of those numbers is computed after you answer: the distances and walking times by a routing engine over the street network, the arrival times by arithmetic over those walking times, and the opening windows by a deterministic evaluator reading each place's own opening hours in the area's local clock. A number offered by you in any of those slots would carry no source, no license and no observation date, so it is discarded unread rather than checked. This is not a trust judgement about you. It is that a plausible distance is indistinguishable from a measured one once it is written down, and the person reading it will be standing in the street with no signal and no way to tell.

The same applies to places. Every candidate here was read from a licensed data source and stamped with it. A place you know of that is not in the list cannot be added, however certain you are that it exists: it would arrive with no provenance, and the traveller would be sent to somewhere nothing vouches for. Choose from the list or choose fewer places.

## Choosing the places

Take the traveller's `interests` as the strongest signal when they give any, and read them generously rather than literally — a request for quiet suggests avoiding the busiest attractions, not only places whose names contain a word about quiet. With no interests stated, choose the places a first-time visitor would most regret missing.

In descending order of pull: named landmarks — monuments, archaeological sites, castles, fortifications, gates, towers; museums and galleries, and religious buildings of historic or architectural note; named viewpoints, beaches, gardens, parks and squares, whose draw is the setting; named markets, historic streets and quarters, harbours and waterfronts; ordinary hospitality and retail, which come last among places a visitor would choose unless a name marks one out as an institution in its own right; and infrastructure and civic function — car parks, clinics, offices, schools, stops, depots — which are the floor whatever else the name suggests, and are usually best left out entirely.

Prefer a day with some variety of kind over four versions of the same experience, and prefer a candidate whose name or categories say something specific over one that could describe anything. Where two candidates look like the same real place recorded twice — a name and its transliteration, the same place in two scripts — choose one of them and leave the other out, because visiting a place twice is not a plan.

A name in a script you find harder to read is a property of the source data and never a signal about the place. Do not rank such a place lower, and do not prefer a place because its name happens to be in English.

## Choosing the order

Order for the shape of the day rather than for distance, which you cannot see. Open where a visitor would want to start and close where they would want to end; put the demanding places earlier and the restful ones later; keep places that plainly belong to one quarter or one theme adjacent to each other. If the routing engine finds no walking path to a place you chose, that place is dropped from the day and the rest of your order is kept, so an order that reads sensibly end to end survives a dropped stop better than one that depends on a single pivot.

## Worked example

Given:

{"interests": "old stonework, and somewhere to sit at the end",
 "max_stops": 4,
 "candidates": [
   {"id": "7f3a", "names": ["Municipal Garden"], "categories": ["park"]},
   {"id": "b129", "names": ["Upper Fortress"], "categories": ["castle", "landmark"]},
   {"id": "c004", "names": ["Parking P3"], "categories": ["parking"]},
   {"id": "d51e", "names": ["Museum of the Citadel"], "categories": ["museum"]},
   {"id": "e77b", "names": ["Harbour Promenade"], "categories": ["waterfront"]}]}

Reply:

["b129", "d51e", "e77b", "7f3a"]

The fortress and its museum answer the stated interest and lead the day; the promenade and the garden are the restful end the traveller asked for. The car park is infrastructure and is simply left out — the day is four stops because four places earned a place in it, not because the cap was four. No arrival time, walking distance or dwell appears anywhere in the reply. A reply of `[{"id": "b129", "planned_start": "10:00"}]` earns nothing: the object is refused on sight, because an object is the shape in which a time, a distance or a coordinate would arrive, and the day would simply lose that stop.
```


### 3.3 Call shape

Sent as the system prompt on the `plan` tier with `cache_prefix=True`. The user turn is a JSON
object with three keys — `interests` (the traveller's own words, possibly empty), `max_stops`,
and `candidates`, an array of `{id, names, categories}`. **No coordinate is sent**, so there is
nothing spatial in the request to reason from.

### 3.4 The prompt is a courtesy; the guarantee is mechanical

## What the model decides, and what it cannot

It contributes **exactly one thing: an ordered subset of the candidate site ids.** Everything
else in the resulting `ItineraryV1` is computed — coordinates from each commons record's own
stamped `location`, distance and duration from the routing seam, wall-clock times from
arithmetic over the routed legs, and opening windows from the deterministic evaluator.

That boundary is **not enforced by this prompt.** The prompt explains it, at length and with a
worked counter-example, because a model that understands why a rule exists follows it further
than one told to comply. But Constitution Article VI is explicit that a prompt is not an
enforcement mechanism, and the guarantee is structural:

- the reply contract is a **JSON array of id strings**, so there is no shape in which a
  `planned_start`, a distance or a coordinate could arrive;
- `_select` refuses every element that is not one of the offered id strings — an object is
  refused **unread**, as are a number, a nested array, an unknown id, a repeat, and anything
  past the cap;
- each refusal is **named** in `Proposal.rejected` and costs the day a stop. Nothing is
  repaired into a value, because a repaired model numeric is a model numeric with the evidence
  removed.

The request carries `id`, `names` and `categories` and **no coordinate at all**, so there is
nothing spatial to reason from even if the model tried.

## Two properties of the text worth keeping through any edit

**It is written to be worth caching.** Sent with `cache_prefix=True`; FAIL-006 records that a
breakpoint below the tier model's minimum cacheable prefix silently no-ops, so the prefix is
deliberately long enough to be cached rather than trimmed for brevity.

**Its worked example is built on invented places.** `Upper Fortress`, `Museum of the Citadel`,
`Harbour Promenade`, `Municipal Garden`, `Parking P3` — none is a real place. This is the same
correction `narration.md` v2 carries: a real place name in a prompt is a place name in product
code, which is an FR-001/SC-005 genericity breach and turns `evals/test_genericity.py` red.
The example must stay fictional **and plausible** — Null Island coordinates and joke names both
fail, for opposite reasons.

The script-neutrality paragraph is also load-bearing rather than decorative: candidates arrive
with names in whatever scripts the source recorded, and a model that quietly prefers the
Latin-script name of a place ranks by the transliteration habits of the data, not by the place.

## How it is checked

Deterministic, CI job 4 — no judge, no network, mocked model, no API key:

- a reply containing an **object** (`[{"id": "…", "planned_start": "10:00"}]`) yields that stop
  refused and named, never a stop bearing a model-supplied time;
- a reply containing a number, a nested array, an unknown id, a duplicate, or more ids than
  `max_stops` — each refused and named;
- every stop in the output resolves to a candidate that was offered (FR-002: the planner
  invents no place);
- an area with no candidates yields an **honest empty plan**, while a model answering nothing
  usable *while candidates exist* raises `ProposalRefused` — the two must not share a
  representation, because an empty day is trivially within every budget and would sail through
  the approval gate.

## Editing this prompt

A prompt change is a code change: same PR, same CI checks 1–7, `version` bumped in that PR.
Because this prompt is not judged, `linked_eval_score.score` stays `null` — do not invent a
score to fill it. Re-verify the mirrored `model` block against `commons.llm.ROUTING_TABLE` in
the same PR; on divergence the routing table is right.
