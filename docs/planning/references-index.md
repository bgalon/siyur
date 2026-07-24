# Master Reference Index — Siyur & the GeoAI Course
*v1.0 — 2026-07-24. Every link below was machine-verified on this date (see §6). PDF column points into `siyur-reference-library.zip` (`pdfs/`). Detailed per-source summaries live in the two methods docs; this index is the map, not the territory.*

## 1. The live document package (where everything is)

| Doc | What it is | Status |
|---|---|---|
| `project/00-project-definition.md` | One-page vision, two goals, dogfooding loop, success criteria | v1.0 |
| `project/02-prd-siyur.md` | Siyur PRD — scope, UX, architecture, evals, course-feed contract | **DRAFT awaiting Ben's approval** |
| `project/03-transition-plan.md` | How to move from these docs to the code project | v1.0 |
| `course/01-syllabus.md` | Full-day syllabus; each unit tied to a build artifact | v1.0 hypothesis |
| `methods/01-ramp-up-standards.md` | SOTA ramp-up standards (indexed, 7 sections, 28-step checklist) | v1.0, links verified |
| `methods/02-siyur-stack-reference.md` | SOTA stack reference (indexed, 7 components + arch/license/risks) | v1.0, links verified |
| `archive/` | Session-1 first two rounds: discussion paper, 6 scored ideas, old outline, **rated resource pack** (`archive/output/04-resource-pack.md`, ~85 rated items), 4 research notes | reference-only |

## 2. Research papers — PDF library (19 papers, all archived in `pdfs/`)

**Agentic/LLM foundations** (feed syllabus U3):

| Paper | One-line summary | PDF |
|---|---|---|
| [GPT-3 few-shot](https://arxiv.org/abs/2005.14165) (Brown+ 2020) | In-context examples steer models without training — examples are implicit specs | `gpt3-language-models-few-shot-learners-*` |
| [Chain-of-Thought](https://arxiv.org/abs/2201.11903) (Wei+ 2022) | Room-to-think improves reasoning — but not recall, and not spatial math | `chain-of-thought-prompting-wei-*` |
| [ReAct](https://arxiv.org/abs/2210.03629) (Yao+ 2022) | Interleave reasoning with tool actions; grounding in observations cuts hallucination | `react-reasoning-acting-yao-*` |
| [Reflexion](https://arxiv.org/abs/2303.11366) (Shinn+ 2023) | Verbal self-correction works — when an external signal (test failure) feeds it | `reflexion-verbal-rl-shinn-*` |
| [Plan-and-Solve](https://arxiv.org/abs/2305.04091) (Wang+ 2023) | Plan-then-execute beats step-at-a-time on long tasks | `plan-and-solve-prompting-wang-*` |
| [LLM-as-Judge / MT-Bench](https://arxiv.org/abs/2306.05685) (Zheng+ 2023) | Judges reach ~80% human agreement — with a documented bias catalog | `llm-as-judge-mtbench-zheng-*` |
| [Coding-agent misalignment, 20k sessions](https://arxiv.org/html/2605.29442v1) (2026) | Large-scale taxonomy of how coding agents fail their users — feeds the failure catalog | `coding-agents-misalignment-20k-sessions-*` |

**Geo/spatio-temporal evidence base** (the "semantic-not-metric geography" receipts; feed U0/U3):

| Paper | One-line summary | PDF |
|---|---|---|
| [GPT4GEO](https://arxiv.org/abs/2306.00020) (Roberts+ 2023) | What GPT-4 knows without tools: good coarse priors, caricature coastlines | `gpt4geo-roberts-*` |
| [Are LLMs Geospatially Knowledgeable?](https://arxiv.org/abs/2310.13002) (Bhandari+ 2023) | Geo knowledge is real but uneven — a prior, not a data source | `llms-geospatially-knowledgeable-bhandari-*` |
| [GeoLLM](https://arxiv.org/abs/2310.06213) (Manvi+ ICLR'24) | OSM-grounded prompts unlock ~70% gains — grounding beats recall | `geollm-geospatial-knowledge-manvi-*` |
| [STBench](https://arxiv.org/abs/2406.19065) (Li+ 2024) | LLMs comprehend spatio-temporal questions but fail accurate computation | `stbench-spatiotemporal-li-*` |
| [Test of Time](https://arxiv.org/abs/2406.09170) (Fatemi+ ICLR'25) | Temporal reasoning is brittle — resolve time in code, not in the model | `test-of-time-temporal-reasoning-fatemi-*` |
| [LLM-Mob](https://arxiv.org/abs/2308.15197) (Wang+ 2023) | The viable *semantic* role over trajectories (prompt-decomposed mobility) | `llm-mob-mobility-wang-*` |
| [GPSBench](https://arxiv.org/html/2602.16105v1) (2026) | Raw coordinate handling still weak in current models — the "still true" citation | `gpsbench-coordinates-*` |

**Autonomous GIS / LLM+GIS systems** (feed U3/U4):

| Paper | One-line summary | PDF |
|---|---|---|
| [Autonomous GIS / LLM-Geo](https://arxiv.org/abs/2305.06453) (Li & Ning 2023) | The manifesto: LLM decomposes, generates code; GIS engines compute | `autonomous-gis-llm-geo-li-ning-*` |
| [GeoGPT](https://arxiv.org/abs/2307.07930) (Zhang+ 2023) | "Foundational + professional": LLM plans, mature GIS tools execute | `geogpt-zhang-*` |
| [LLM-Find](https://arxiv.org/abs/2407.21024) (2024) | Per-source "handbooks" for data retrieval — the skills pattern, independently invented | `llm-find-geospatial-data-retrieval-*` |
| [GIS Copilot](https://arxiv.org/abs/2411.03205) (2024-25) | Autonomy collapses on multi-step workflow design — why Siyur keeps HITL gates | `gis-copilot-spatial-analysis-agent-*` |
| [GeoSQL-Eval](https://arxiv.org/abs/2509.25264) (Hou+ 2025) | 14k-task NL→PostGIS benchmark; CRS/function selection are the failure hotspots | `geosql-eval-postgis-nl2sql-hou-*` |

## 3. Standards, policies & specs (snapshots in `pdfs/` where marked 📄)

| Reference | Why it matters to Siyur | Snapshot |
|---|---|---|
| [MADR 4.0](https://adr.github.io/madr/) — ADR standard | The course-feed contract's decision-record format | 📄 `madr-4.0-adr-standard.pdf` |
| [Nominatim usage policy](https://operations.osmfoundation.org/policies/nominatim/) | Hard 1 req/s; shapes the geocoding fallback design | 📄 `nominatim-usage-policy.pdf` |
| [OSMF ODbL attribution guidelines](https://osmfoundation.org/wiki/Licence/Attribution_Guidelines) | Defines the attribution Siyur must render on every map | 📄 `osmf-odbl-attribution-guidelines.pdf` |
| [Overture attribution](https://docs.overturemaps.org/attribution/) | Per-theme licenses: places CDLA-P (bundle-safe), rest ODbL | 📄 `overture-attribution-licensing.pdf` |
| [Wikivoyage reuse policy](https://en.wikivoyage.org/wiki/Wikivoyage:How_to_re-use_Wikivoyage_guides) | The CC BY-SA share-alike terms behind PRD open decision #1 | 📄 `wikivoyage-reuse-policy-cc-by-sa.pdf` |
| [PMTiles spec v3](https://github.com/protomaps/PMTiles/blob/main/spec/v3/spec.md) | The bundle's tile format | 📄 `pmtiles-spec-v3.md` (raw spec) |
| [Anthropic: Effective Context Engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) | The context-as-budget canon behind methods/01 | 📄 `anthropic-effective-context-engineering.pdf` |
| [Anthropic: Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) | Workflows-vs-agents, pattern vocabulary, ACI | 📄 `anthropic-building-effective-agents.pdf` |
| [REUSE v3.3](https://reuse.software/spec-3.3/) · [ODI data-licensing guide](https://theodi.org/insights/guides/reusers-guide-to-open-data-licensing/) | Code + data licensing practice | link only |
| [OpenRouteService restrictions](https://openrouteservice.org/restrictions/) · [OSRM demo policy](https://github.com/Project-OSRM/osrm-backend/wiki/Api-usage-policy) | Dev-mode API terms | link only (ORS page resisted rendering) |

*Note: snapshots are simplified-formatting captures (sandbox limitation); each carries a banner pointing to the canonical URL. Living docs (framework docs, GitHub repos) are deliberately NOT snapshotted — verified links are the right artifact for those, and a repo task in M0 can archive fuller local copies from Ben's machine if wanted.*

## 4. Tooling & component references (living docs — verified links, no snapshots)

The full component table with pinned versions and licenses is **`methods/02` §A** (20 rows: MapLibre 5.19.x, PMTiles v3/`pmtiles` v4, Protomaps builds + basemaps flavors, Planetiler 0.10.2, Workbox 7, maplibre-offline-pmtiles 2.1.1, Overture 2026-07-22.0, opening_hours.js 3.9, Valhalla, geojson-path-finder 2, LangGraph 1.x, DuckDB 1.3+…). The process-tooling equivalents are **`methods/01`** per section: Spec Kit 0.13, DeepEval + agentevals, MADR 4.0, git-cliff, disler hooks repo, claude-code-action, Semgrep/gitleaks/pip-audit; geo pins `shapely~=2.1`, `h3~=4.5`, `osmnx~=2.1`, `geopandas~=1.1`.

## 5. Practitioner & community layer

The rated catalog (~85 items with quality/popularity stars: Anthropic engineering posts, Chroma context-rot, Spec Kit ecosystem, DuckDB-geo posts, MapScaping, Matt Forrest, r/gis threads, data sources…) lives at **`archive/output/04-resource-pack.md`** — still current as of 2026-07-23; refresh popularity figures near the workshop date.

## 6. Link-verification report

Method: every URL in `methods/01` + `methods/02` (99 unique, template URLs excluded) fetched and title-checked on **2026-07-24** by two verification agents; a handful of fetch-gated domains cross-checked by direct request.

Result: **95 OK · 2 moved (fixed in docs) · 2 blocked-unverifiable · 0 broken.** Fixes applied: `blog.langchain.com/...langgraph-1dot0` → `www.langchain.com/blog/langchain-langgraph-1dot0`; Wikimedia `/wiki/Travel` portal → `www.mediawiki.org/wiki/Wikimedia_APIs`; BusinessWire press mirror (bot-gated) → Veracode's primary report page. Blocked-but-believed-fine: `github.com/anthropics/claude-code-action` (proxy-gated here; it is Anthropic's official action). Operational note: the Overpass kumi.systems mirror is now operated by Private.coffee — link works, operator changed.

## 7. Maintenance policy for this library

Re-verify links + pinned versions at each build milestone and before the workshop (Overture releases monthly and embeds versions in paths; MapLibre v6 ESM transition is in progress; Protomaps build URLs are explicitly unstable; promptfoo's post-acquisition roadmap bears watching). The build repo should add a `docs/references.md` that imports from this index — and from M0 onward, new references discovered during the build get added *there* first, synced here at milestones.
