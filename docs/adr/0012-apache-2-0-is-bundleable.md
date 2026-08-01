# 0012 — `Apache-2.0` joins the bundleable allowlist (Overture per-record licensing)

- Status: accepted
- Decision Maker(s): Ben
- drafted-by: claude-code · approved-by: Ben · Date: 2026-08-01

## Context and Problem Statement

`DATA-LICENSES.md`'s quarantine rule allowed `bundleable=true` **only if** the license was one of **{ODbL, CDLA-Permissive-2.0, CC0, CC-BY-4.0, CC-BY-SA-4.0, PD, OFL, LGPL-as-dependency}**. `Apache-2.0` was absent.

The same registry's Overture row, however, warns that **per-record licenses differ *within* one theme** ("some Foursquare rows Apache-2.0 — read the per-source stamp, not the theme") and marks Overture **✅ bundleable**. The registry contradicted itself: it told ingestion to honour a per-record license it then refused to allowlist.

Building slice 001 made the contradiction concrete rather than theoretical. The committed 200-row Rhodes Overture fixture (`tests/fixtures/overture_places_rhodes.parquet`) breaks down as:

| License | Rows | Share |
|---|---:|---:|
| CDLA-Permissive-2.0 | 165 | 82.5% |
| **Apache-2.0** (Foursquare-sourced) | **33** | **16.5%** |
| CC0-1.0 | 2 | 1.0% |

So **16.5% of real Overture places stamped `bundleable=False`** and would have been filtered out of every offline bundle at DU-05 — silently, because the quarantine filter is working as designed when it drops them.

Two sessions independently surfaced this and, correctly, refused to resolve it themselves: the data-spine session flagged it while transcribing the allowlist ("this may be a registry gap rather than intent"), and the source-adapter session stamped what the registry said and documented the outcome in a test rather than editing the allowlist to make its own numbers look better. Licensing is a Constitution Article V concern and the registry is the governing artifact, so neither could move it.

## Considered Options

1. **Add `Apache-2.0` to the allowlist** — treat it as bundle-safe, and discharge its §4 obligations through the existing attribution pipeline.
2. **Keep it excluded** — Apache-2.0-licensed values display in the online phase but never enter a bundle.
3. **Defer to DU-05** — decide when the compiler's quarantine filter is actually written.

## Decision Outcome

**Option 1.** `Apache-2.0` is added to the quarantine allowlist in `DATA-LICENSES.md` and to `BUNDLEABLE_LICENSES` in `commons/licenses.py`.

The decisive argument is **internal inconsistency**: `ODbL` is allowlisted despite carrying **share-alike**, which is a materially stronger obligation than anything Apache-2.0 imposes. Permitting the more restrictive license while refusing the more permissive one is not a defensible position — it is an omission, not a policy.

Apache-2.0's §4 obligations are concrete and already-solved shapes: **retain the copyright / patent / trademark / attribution notices, ship a copy of the license, and reproduce any NOTICE file contents**. The DU-05 ATTRIBUTION pipeline already renders per-value attribution for ODbL and CC-BY; NOTICE reproduction is the one genuinely new mechanic, and it becomes a DU-05 acceptance criterion.

Option 2 was rejected because it ships a product that silently loses 1 in 6 places offline — precisely the failure the airplane-mode promise (Article I) exists to prevent — and does so by omission rather than by a reasoned call. Option 3 was rejected because it defers the decision to the moment of maximum schedule pressure, with no more information than is available now: the fixture already quantifies the impact exactly.

**Not changed by this ADR:** the quarantine mechanism itself. `bundleable` is still derived from the registry, never author-set; `open_web` / `review_provider` are still *always* `bundleable=false` regardless of license; and the equivalence (`bundleable=true` ⟺ license ∈ allowlist) still holds in both directions. Only the allowlist's membership moved.

### Consequences

- Good: recovers 16.5% of Overture places for offline bundles; removes a self-contradiction from the governing registry; the adapter needed **no change** — it already read per-record licenses correctly.
- Good: the fixture's deliberate license variance now exercises three allowlisted licenses end-to-end, so the per-record-licensing path is covered by real data rather than a synthetic case.
- Cost / accepted: Apache-2.0 is a *software* license applied to data, so its §4 language maps imperfectly onto a tile/POI bundle. We discharge it literally (notices + license text + NOTICE) rather than reasoning about intent.
- **New DU-05 obligation:** the ATTRIBUTION pipeline must reproduce NOTICE-file contents, not just render an attribution string. This is the one mechanic Apache-2.0 adds beyond what ODbL/CC-BY already required.

### Confirmation

- **`tests/test_licenses.py::test_apache_2_0_is_bundleable`** — pins the decision across spellings (`Apache-2.0`, `apache-2.0`, `Apache-License-2.0`), and asserts the kind gate still wins (`open_web` + Apache-2.0 ⇒ `False`), so permissive licensing can never rescue a quarantined source.
- **`tests/test_licenses.py::test_allowlist_matches_the_registry_document`** — the existing drift tripwire re-parses the quarantine sentence out of `DATA-LICENSES.md` and asserts it equals `BUNDLEABLE_LICENSES`. It would have failed had the registry and the code been updated inconsistently.
- **`tests/test_sources_overture.py::test_every_per_record_license_in_the_fixture_is_bundleable`** — end-to-end over the real fixture: all three licenses Overture mixes into one theme now reach the bundle. This test previously asserted the opposite and carried the flag that surfaced the gap; its docstring records the reversal.
- **DU-05 gate:** `evals/test_structural.py::test_no_unbundleable_in_bundle` continues to hold, and the compile step must additionally emit NOTICE contents for Apache-2.0-sourced values.
