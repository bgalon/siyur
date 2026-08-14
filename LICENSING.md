# Licensing

This repository carries **three different licences**, because it contains three different
kinds of thing. A single repo-wide licence would be a false claim: some of what is committed
here is third-party data that nobody involved in this project is entitled to relicense.

| What | Licence | File |
|---|---|---|
| **Code** — `commons/`, `planner/`, `compiler/`, `api/`, `web/`, `tests/`, `evals/`, `scripts/`, `.claude/`, configuration | **Apache-2.0** | [`LICENSE`](LICENSE) |
| **Documentation** — `docs/`, `specs/`, `prompts/`, `README.md`, `AGENTS.md` and this file | **CC BY 4.0** | [`LICENSE-docs`](LICENSE-docs) |
| **Committed data** — everything under `tests/fixtures/` and `data/` | **Per file, third-party** — see [`DATA-LICENSES.md`](DATA-LICENSES.md) | — |

## Why the split

**Code is Apache-2.0** rather than MIT for the explicit **patent grant**, and because the
project already reasons in Apache-2.0's terms: ADR-0012 put Apache-2.0 on the bundleable
allowlist, and `compiler/attribution.py` implements the §4 NOTICE reproduction mechanism.

**Documentation is CC BY, not CC BY-SA.** The docs are the teaching deliverable of a GeoAI
course case study, and the course repository consumes them. Share-alike would force every
derived slide, handout and lecture note to carry the same terms — a constraint on the
project's own downstream use, for no gain. Attribution without copyleft is the honest
requirement here.

Note the asymmetry with the *product's* narration posture, which **is** CC BY-SA (PRD §7):
narration adapts CC BY-SA source articles, so it inherits share-alike by obligation. That is
a different question from how this repository's own prose is offered.

## The data is not ours to relicense, and that is the important part

`tests/fixtures/` holds **real data captured live** from third-party sources, and `data/`
holds a committed boundary dataset. Neither is covered by the code or documentation licence
above. Each is governed by its own terms, recorded per row in
[`DATA-LICENSES.md`](DATA-LICENSES.md):

| Committed artifact | Source | Terms |
|---|---|---|
| `tests/fixtures/overpass_*.json` | OpenStreetMap via Overpass | **ODbL-1.0** — share-alike for derivative *databases*; © OpenStreetMap contributors |
| `tests/fixtures/valhalla_rhodes_*.json` | Valhalla over an OSM extract | **ODbL-1.0** Produced Work; © OpenStreetMap contributors |
| `tests/fixtures/wikivoyage_rhodes.json` | Wikivoyage / Wikipedia MediaWiki API | **CC-BY-SA-4.0** — per-article *and* per-revision attribution |
| `tests/fixtures/overture_places_*.parquet` | Overture Maps places theme | **CDLA-Permissive-2.0** |
| `data/ne_10m_admin_0_countries.zip` | Natural Earth 1:10m admin-0 | **Public domain** — no attribution required |
| `data/licenses/glyphs/OFL.txt` | `protomaps/basemaps-assets`, `fonts/OFL.txt` | **OFL-1.1**, verbatim — this *is* someone else's licence text; it is committed so `compiler/tiles.py` can ship it beside the Noto glyphs, as OFL §2 requires |
| `data/licenses/sprites/LICENSE.md` | `tangrams/icons`, `LICENSE.md` | **MIT** (© 2017 Mapzen), verbatim — shipped beside the sprite sheets for the same reason |

**If you reuse this repository, the data files carry their own obligations and they travel
with the bytes.** Copying an ODbL fixture into another project brings ODbL with it. The code
licence does not, and cannot, override that.

## Attribution, if you use this

Apache-2.0 §4 requires retaining the `LICENSE` file and any `NOTICE`. CC BY 4.0 requires
crediting the source of documentation you reuse. For the data, the per-source attribution
strings are in `DATA-LICENSES.md` and are the same ones the product renders — for OSM-derived
data that is **"© OpenStreetMap contributors"**, and it is not optional.

## Why this file exists at all

Licence compliance is treated here as an engineering practice rather than a legal afterthought
— every data value in the product carries `source + license + bundleable`, a quarantine filter
drops anything that may not be redistributed, and `ATTRIBUTION.md` is regenerated per bundle
rather than hand-maintained. A repository that enforces that on its own outputs and gets its
*own* licensing wrong would be an awkward teaching artifact.
