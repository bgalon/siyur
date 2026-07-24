# Project Definition — Siyur & the GeoAI Course
### One page. The what, the why, and the loop that connects them.

*v1.0 — 2026-07-24. Owner: Ben Galon. Status: for discussion. Supersedes the archived session-1 package (see `archive/`).*

## Vision

One project, two products. **Siyur** is a tour-day map studio: the user picks any city, converses with an embedded LLM to plan a day that matches their preferences, and receives a beautiful, personal, *dynamic* map compiled into a self-contained offline bundle — plan online, travel with zero connectivity and zero LLM. **The course** is a full-day workshop teaching AI-first projects in the geospatial/spatio-temporal domain — and its material is not written; it is *harvested* from the documented reality of building Siyur.

## The two goals (and how Siyur serves both)

**Goal 1 — build, manage, and maintain a dev or analytics project with AI code agents** (general + geo). Siyur is built by Ben with Claude Code (GitHub, cloud + local sessions) under the highest current standards — spec-anchored development, evals from day one, deterministic verification, maintenance designed in. The *process* is the Goal-1 curriculum, kept geo-specific throughout: stale-geo-API tripwires, CRS discipline, schema cards, data-license registry, spatio-temporal feasibility checks — never generic project management.

**Goal 2 — build a geo/spatio-temporal application that embeds an LLM in the user's workflow.** Siyur *is* the Goal-2 curriculum: the LLM sits at the planning and design moments (conversational curation, cartographic Flavor deltas, HITL approval gates) and produces durable artifacts the travel phase consumes without it. The online/offline boundary makes the embedding lesson physical: the LLM does its job and leaves.

## The dogfooding loop (the meta-workflow)

```
 Ben + agent BUILD Siyur ──► agent DOCUMENTS as it works ──► docs DISTILL into course units
        ▲                    (course-feed contract:                    │
        │                     ADRs · devlog · failure catalog ·        │
        └── course rehearsals  prompts+eval history · changelog)  ◄────┘
            feed fixes back
```

The **course-feed contract** (defined in the PRD, §course-feed) makes "the agent documents everything" concrete: five artifact types, each auto- or agent-produced during the build, each mapped to a named syllabus unit. Nothing is written twice.

## Ground rules

**Stack policy:** open source preferred end to end (PMTiles/MapLibre, Valhalla, DuckDB, LangGraph, Overture/OSM/Wikivoyage); hosted APIs only where they genuinely serve (the LLM API, ORS in dev mode); every license obligation tracked in a registry and honored in-product (ODbL attribution on every map, CC BY-SA posture decided consciously, per-file image licenses captured at compile).
**Standards:** the build follows `methods/01-ramp-up-standards.md` (28-step ramp-up checklist, AGENTS.md + Spec Kit constitution, pytest+DeepEval+agentevals harness, MADR 4.0 ADRs, SAST-gated CI). The technical stack follows `methods/02-siyur-stack-reference.md` (pinned versions, license register, open risks).
**Format:** the course is a full-day workshop; the syllabus (in `course/`) declares per unit which build artifact feeds it.

## Success criteria

The project succeeds when: (1) Siyur's MVP passes its airplane-mode eval — a full tour day planned for a never-before-tested city renders, narrates, and re-routes entirely offline; (2) the repo's documentation trail is complete enough that the course's exhibits are extracted, not authored — every syllabus unit has its promised artifact; (3) a participant can clone the repo and reproduce both the product and the process.

## What this is not

Not a generic AI-coding course (every unit must earn its geo/temporal specificity); not a GeoAI deep-learning course (we orchestrate models, we don't train them); not a production-scale deployment course (laptop-scale, methods-first); not vibe coding (specs, evals, and review gates are the point).

## Immediate next steps

1. Ben reviews **`project/02-prd-siyur.md`** and resolves its open decisions (notably the CC BY-SA narration posture). 2. On PRD approval, Claude produces the **ramp-up prompt** — the bootstrap instruction for the build session that initializes the repo per the 28-step checklist with the course-feed contract wired in. 3. Build begins; the course syllabus gets validated against real artifacts as they accumulate.
