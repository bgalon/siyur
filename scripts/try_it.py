#!/usr/bin/env python
"""Run the slice-001 data spine end to end, offline, against the committed fixtures.

    uv run python scripts/try_it.py

Everything below is real code on `main` — the Overture and OSM adapters, the
per-field merge, and the Greek transliteration — driven over the captured Rhodes
fixtures rather than the network. No API key, no database, no Docker required.

What this does NOT cover: the planner pipeline, the API endpoints, and the map
render, because `POST /areas`, the research SSE stream, and `GET /sites` are not
built yet (Spec 001 tasks T030, T032-T041). This script is the closest thing to
"see the product work" until those land.
"""

from __future__ import annotations

import pathlib
from datetime import date

import httpx
from shapely.geometry import box

from commons import merge as M
from commons import translit as T
from commons.sources.osm import OsmAdapter
from commons.sources.overture import OvertureAdapter

FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "tests" / "fixtures"
# The Rhodes medieval old town — the Spec 001 demo area. Nothing else is
# hardcoded to Rhodes; this is a user-supplied bbox everywhere in real code.
AREA = box(28.216, 36.440, 28.232, 36.451)
OBSERVED = date(2026, 7, 22)


def rule(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


def main() -> None:
    rule("1. Ingest — two sources, every value stamped at ingestion")

    overture = OvertureAdapter(
        parquet=str(FIXTURES / "overture_places_rhodes.parquet"), observed_at=OBSERVED
    ).fetch(AREA)

    # Serve the captured Overpass response instead of calling the live API.
    raw = (FIXTURES / "overpass_rhodes.json").read_text()
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=raw)))
    osm = OsmAdapter(client=client, backoff=0.0, observed_at=OBSERVED).fetch(AREA)

    print(f"  Overture : {len(overture.records):>3} records   degraded={overture.degraded}")
    print(f"  OSM      : {len(osm.records):>3} records   degraded={osm.degraded}")

    licenses: dict[str, int] = {}
    bundleable: dict[str, bool] = {}
    for record in overture.records:
        for value in (record.location, *record.names.values()):
            licenses[value.source.license] = licenses.get(value.source.license, 0) + 1
            bundleable[value.source.license] = value.bundleable
    print("\n  Per-record licensing (Overture mixes licenses *within* one theme):")
    for lic, count in sorted(licenses.items()):
        flag = "bundleable" if bundleable[lic] else "QUARANTINED"
        print(f"    {lic:<24} {count:>4} values   {flag}")

    rule("2. Merge — spatial + name join, no source lost")

    def names_of(record: object) -> dict[str, str]:
        return {k: v.value for k, v in record.names.items()}  # type: ignore[attr-defined]

    target = "Πύλη Ταρσανά"  # the fixture's deliberate cross-source anchor
    a = next(r for r in overture.records if target in names_of(r).values())
    b = next(r for r in osm.records if target in names_of(r).values())

    print(f"  Overture record : {names_of(a)}")
    print(f"  OSM record      : {names_of(b)}")
    print(f"  distance        : {M.distance_m(a.location.value, b.location.value):.2f} m")
    print(f"  thresholds      : eps={M.EPSILON_METERS} m, tau={M.TAU_NAME_SIMILARITY}")

    decision = M.decide_match(a, b)
    print(
        f"  decision        : matched={decision.matched} rule={decision.rule} "
        f"similarity={decision.name_similarity} language={decision.language}"
    )

    (merged,) = M.merge_records([a, b])
    print(f"  merged names    : {names_of(merged.record)}")
    print(f"  sources kept    : {sorted((s.kind, s.id) for s in merged.source_refs)}")

    print("\n  Distance alone must NEVER merge — the nearest differently-named neighbour:")
    others = [r for r in osm.records if r is not b]
    neighbour = min(others, key=lambda r: M.distance_m(b.location.value, r.location.value))
    gap = M.distance_m(b.location.value, neighbour.location.value)
    verdict = "not merged" if len(M.merge_records([b, neighbour])) == 2 else "MERGED"
    print(
        f"    '{list(names_of(b).values())[0]}' vs '{list(names_of(neighbour).values())[0]}'"
        f"  ({gap:.1f} m apart) -> {verdict}"
    )
    if gap > M.EPSILON_METERS:
        print(
            f"    note: {gap:.1f} m already exceeds eps={M.EPSILON_METERS} m, so this pair is "
            "rejected on distance.\n          The eps-boundary cases live in tests/test_merge.py."
        )

    print(
        f"\n  Whole corpus    : {len(overture.records) + len(osm.records)} raw -> "
        f"{len(M.merge_records(list(overture.records) + list(osm.records)))} merged"
    )

    rule("3. Transliteration — Greek display names become readable (ELOT 743)")

    for greek, why in [
        ("Ρολόι", "accent breaks the οι digraph"),
        ("Πύλη Ταρσανά", "the anchor above"),
        ("Ευαγγελισμός", "ευ before a vowel -> ev"),
        ("Ευτυχία", "ευ before an unvoiced consonant -> ef"),
        ("μπακάλικο", "word-initial μπ -> b"),
        ("Αϋπνία", "diaeresis breaks αυ"),
        ("ΡΟΔΟΣ", "all caps preserved"),
    ]:
        print(f"    {greek:<16} -> {T.transliterate_greek(greek):<22} ({why})")

    print("\n  FAIL-001 guard — source scripts are untrustworthy:")
    for candidate in ["Ρόδος", "Родос", "Ρόδος (Rhodes)"]:
        check = T.check_script(candidate, "el")
        note = "  <- Cyrillic in an 'el' field, refused" if check.status == "mismatch" else ""
        print(f"    {candidate!r:<20} declared 'el' -> {check.status}{note}")

    rule("Done — every value above carries a source, a license, and a bundleable flag")


if __name__ == "__main__":
    main()
