"""T008 — the area's local frame: deterministic, offline, and refusing rather than guessing.

Tier 1. Every input is a fixed geometry and every answer comes from two committed/pinned
datasets, so there is nothing here that a network, a clock or a machine could change.

**What each assertion is defending against** is stated per test, because this slice has already
shipped tests that could not fail for the reason they existed. Three of the tests below were
constructed specifically so that a *plausible wrong implementation* fails them:

* :func:`test_a_polygon_whose_centroid_lands_in_the_other_country_still_resolves_by_area` fails a
  centroid lookup — the shape's centroid is outside the polygon *and* in the losing country.
* :func:`test_the_uncoded_rows_are_dropped_before_ranking_or_they_win` fails an implementation
  that keeps Natural Earth's ``-99`` rows: they outrank the real country on that bbox.
* :func:`test_metropolitan_france_needs_the_eh_column` fails an implementation reading ``ISO_A2``,
  where France is ``-99``.

Real places appear here as *fixtures*, never as behaviour: nothing in `commons/frame.py` knows a
name, a code or a bounding box, and `tests/test_resolve_area.py` keeps the genericity tripwire.
Rhodes is the project's rehearsed area; Gibraltar, Nicosia, Juba, Strasbourg/Kehl and the open
Pacific are unrehearsed, which is what makes them evidence (`tests/AGENTS.md`).
"""

from __future__ import annotations

import hashlib
import socket
import zipfile
from typing import Any

import geopandas as gpd
import pytest
from shapely import STRtree, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

import commons.frame as frame_module
from commons.frame import (
    COUNTRY_CODE_COLUMN,
    NATURAL_EARTH_BYTES,
    NATURAL_EARTH_LAYER,
    NATURAL_EARTH_ROWS,
    NATURAL_EARTH_SHA256,
    NATURAL_EARTH_VERSION,
    NATURAL_EARTH_ZIP,
    UNCODED,
    FrameUnresolved,
    LocalFrame,
    _country_index,
    _largest_intersection,
    _Weigher,
    _zone_index,
    holiday_countries,
    natural_earth_uri,
    resolve_country_code,
    resolve_frame,
    resolve_timezone,
)
from commons.geo import GeometryError
from planner.nodes.resolve_area import (
    AreaFrameUnresolved,
    AreaInvalid,
    AreaRequest,
    resolve_area,
)

# ── fixed inputs ──────────────────────────────────────────────────────────────
# `[minLon, minLat, maxLon, maxLat]` throughout — lon first, house order.

#: The rehearsed area: the same box `tests/test_api_areas.py` uses.
RHODES = (28.216, 36.440, 28.232, 36.451)
#: Straddles the Rhine, so it genuinely lies in two countries *and* two IANA zones.
STRASBOURG_KEHL = (7.70, 48.55, 7.82, 48.60)
#: `ISO_A2` is `-99` for France; `ISO_A2_EH` is `FR` (ADR-0029 rule 1).
METROPOLITAN_FRANCE = (2.30, 48.83, 2.38, 48.88)
#: Natural Earth's uncoded rows (N. Cyprus, the UN buffer zone) outrank `CY` here (rule 3).
NICOSIA = (33.34, 35.15, 33.39, 35.19)
#: 1:50m has no Gibraltar row at all, so this bbox resolves to `ES` there and `GI` here.
GIBRALTAR = (-5.36, 36.11, -5.33, 36.15)
#: Post-2011 South Sudan — the case the 2008-vintage alternatives get wrong (ADR-0029 B).
JUBA = (31.55, 4.83, 31.63, 4.88)
#: Open Pacific: a zone (`Etc/GMT+9`) covers it, no country does.
OPEN_OCEAN = (-140.0, -30.0, -139.9, -29.9)
#: 1:10m is too coarse for the BE/NL enclaves — a named, accepted limit (ADR-0029).
BAARLE = (4.92, 51.42, 4.95, 51.45)
#: Natural Earth's Vatican is a ~110 m token polygon, so a walkable bbox resolves to `IT`.
VATICAN = (12.44, 41.900, 12.46, 41.908)


def area(bounds: tuple[float, float, float, float]) -> BaseGeometry:
    return box(*bounds)


# ── the committed dataset is the one ADR-0029 chose ───────────────────────────


def test_the_committed_boundary_file_is_the_one_the_adr_pinned() -> None:
    """Merge-blocking fixture check: an accidental or silent dataset swap fails here.

    The digest is the whole point of committing the *zip* rather than an unpacked shapefile —
    these bytes are comparable against the upstream download, and a re-encoding (FlatGeobuf,
    GPKG, GeoJSON) would not be. 4.93 MB of binary in the repo is ADR-0029's accepted cost and
    this assertion is what buys it back.
    """
    payload = NATURAL_EARTH_ZIP.read_bytes()
    assert len(payload) == NATURAL_EARTH_BYTES
    assert hashlib.sha256(payload).hexdigest() == NATURAL_EARTH_SHA256
    with zipfile.ZipFile(NATURAL_EARTH_ZIP) as archive:
        version = archive.read("ne_10m_admin_0_countries.VERSION.txt").decode().strip()
    assert version == NATURAL_EARTH_VERSION


def test_the_layer_reads_in_place_at_the_expected_shape() -> None:
    """258 rows, EPSG:4326, and the `ISO_A2_EH` column the rule reads — read *from the zip*."""
    frame = gpd.read_file(natural_earth_uri())
    assert len(frame) == NATURAL_EARTH_ROWS
    assert frame.crs is not None
    assert frame.crs.to_string() == "EPSG:4326"
    assert COUNTRY_CODE_COLUMN in frame.columns
    assert NATURAL_EARTH_LAYER in natural_earth_uri()


def test_the_index_drops_the_uncoded_rows_and_keeps_the_duplicated_codes() -> None:
    """The two dataset facts the ranking rules exist for, asserted rather than assumed.

    If Natural Earth ever stopped duplicating codes, ADR-0029 rule 2 would be dead weight and
    this test says so; if it stopped carrying `-99`, rule 3 would be. Both are load-bearing
    today, so both are pinned.
    """
    keys = _country_index().keys
    assert UNCODED not in keys
    assert len(keys) == NATURAL_EARTH_ROWS - 13  # 13 uncoded rows dropped
    assert keys.count("AU") == 4  # rule 2: one country, four rows
    assert len(set(keys)) < len(keys)


# ── the rule itself ───────────────────────────────────────────────────────────


def test_ties_break_on_the_lexicographically_smallest_key() -> None:
    """`max()` over items would return the *largest* key on a tie — the opposite of the rule."""
    assert _largest_intersection({"ZZ": 1.0, "AA": 1.0, "MM": 1.0}) == "AA"
    assert _largest_intersection({"AA": 1.0, "ZZ": 2.0}) == "ZZ"
    assert _largest_intersection({}) is None


def test_fragments_of_one_key_are_summed_not_ranked_against_each_other() -> None:
    """ADR-0029 rule 2, tested on the mechanism rather than hoping the dataset shows it.

    Two fragments of ``AA`` (0.6 + 0.6) beat one ``BB`` (1.0) only if they are added. An
    implementation that assigned instead of accumulating — ``weights[key] = overlap`` — picks
    ``BB`` and passes every whole-dataset assertion in this file, because no real bbox in it
    straddles two fragments of one country and a second country at once.
    """
    polygons = (box(0, 0, 0.6, 1), box(1, 0, 1.6, 1), box(2, 0, 3, 1))
    weigher = _Weigher(keys=("AA", "AA", "BB"), tree=STRtree(polygons), load=polygons.__getitem__)
    weights = weigher.weigh(box(-1, -1, 4, 2))
    assert weights == pytest.approx({"AA": 1.2, "BB": 1.0})
    assert _largest_intersection(weights) == "AA"


def test_a_touching_neighbour_contributes_nothing() -> None:
    """A zero-area overlap is not evidence. Counting it would let a tie-break invent an answer."""
    polygons = (box(0, 0, 1, 1), box(1, 0, 2, 1))
    weigher = _Weigher(keys=("AA", "BB"), tree=STRtree(polygons), load=polygons.__getitem__)
    # This query area touches BB along the x=1 line and overlaps AA properly.
    assert weigher.weigh(box(0.5, 0, 1.0, 1)) == pytest.approx({"AA": 0.5})


# ── the frame, on real geometry ───────────────────────────────────────────────


def test_the_rehearsed_area_resolves_to_its_own_clock_and_calendar() -> None:
    frame = resolve_frame(area(RHODES))
    assert frame == LocalFrame(timezone="Europe/Athens", country_code="GR")


@pytest.mark.parametrize(
    ("bounds", "expected"),
    [
        (JUBA, "SS"),  # post-2011; a 2008-vintage boundary set answers `SD`
        (GIBRALTAR, "GI"),
        (NICOSIA, "CY"),
        (METROPOLITAN_FRANCE, "FR"),
    ],
)
def test_unrehearsed_areas_resolve_to_the_right_country(
    bounds: tuple[float, float, float, float], expected: str
) -> None:
    """SC-009 genericity: four areas nothing in this project was built around."""
    assert resolve_country_code(area(bounds)) == expected


def test_metropolitan_france_needs_the_eh_column() -> None:
    """ADR-0029 rule 1, made able to fail: on `ISO_A2` this bbox resolves to `-99`.

    The assertion is not just "FR" — it is that the *dataset* would answer `-99` on the other
    column, so the test would catch the swap rather than merely agreeing with the right answer.
    """
    frame = gpd.read_file(natural_earth_uri())
    france = frame.loc[frame["NAME"] == "France"]
    assert set(france["ISO_A2"]) == {UNCODED}
    assert set(france[COUNTRY_CODE_COLUMN]) == {"FR"}
    assert resolve_country_code(area(METROPOLITAN_FRANCE)) == "FR"


def test_the_uncoded_rows_are_dropped_before_ranking_or_they_win() -> None:
    """ADR-0029 rule 3, made able to fail: on this bbox `-99` outranks the real country.

    N. Cyprus (0.00121) and the UN buffer zone (0.00083) both beat `CY` (0.00036). An
    implementation that ranked first and filtered afterwards would return the sentinel — or,
    filtering the winner only, would return the *second* place rather than the largest real
    country. Both are caught by asserting the uncoded polygons genuinely dominate here.
    """
    everything = gpd.read_file(natural_earth_uri())
    query = area(NICOSIA)
    uncoded = everything.loc[everything[COUNTRY_CODE_COLUMN] == UNCODED]
    uncoded_overlap = max(geom.intersection(query).area for geom in uncoded.geometry)
    cyprus = everything.loc[everything[COUNTRY_CODE_COLUMN] == "CY"]
    cyprus_overlap = sum(geom.intersection(query).area for geom in cyprus.geometry)
    assert uncoded_overlap > cyprus_overlap  # the trap is real on this input
    assert resolve_country_code(query) == "CY"


def test_the_committed_scale_is_what_makes_gibraltar_resolvable() -> None:
    """Why 1:10m and not the 800 KB 1:50m: at 1:50m there is no Gibraltar row to find.

    Asserted on the committed file rather than by downloading the alternative (Tier 1 is
    offline): `GI` exists here, it wins on a Gibraltar bbox, and Spain — the answer 1:50m
    gives — is a genuine competitor on that same bbox rather than absent.
    """
    weights = _country_index().weigh(area(GIBRALTAR))
    assert weights["GI"] > weights["ES"] > 0.0
    assert resolve_frame(area(GIBRALTAR)) == LocalFrame(
        timezone="Europe/Gibraltar", country_code="GI"
    )


# ── straddling: the case the rule exists for ──────────────────────────────────


def test_a_border_straddling_area_resolves_to_the_majority_side_on_both_halves() -> None:
    """One area, one clock, one calendar — and the two halves must agree.

    The check that matters is the *coherence*: this bbox lies in FR/DE and in
    Europe/Paris/Europe/Berlin, and resolving the two halves by different rules would hand the
    planner a French country code against a German clock. The losing side is asserted to be
    present and non-zero, so the test would still be meaningful if the bbox ever drifted off
    the border — at which point it fails rather than silently becoming a single-country test.
    """
    query = area(STRASBOURG_KEHL)
    countries = _country_index().weigh(query)
    zones = _zone_index().weigh(query)
    assert countries["DE"] > 0.0 and countries["FR"] > countries["DE"]
    assert zones["Europe/Berlin"] > 0.0 and zones["Europe/Paris"] > zones["Europe/Berlin"]
    assert resolve_frame(query) == LocalFrame(timezone="Europe/Paris", country_code="FR")


def test_the_same_polygon_resolves_identically_on_every_call() -> None:
    """Determinism, asserted on repeats *and* on the float sums, not just the winner.

    Sums are accumulated in sorted index order because float addition is not associative; if
    that ordering were dropped the winner would almost always still be right and the weights
    would wobble in the last bits. Comparing the dicts exactly is what would notice.
    """
    query = area(STRASBOURG_KEHL)
    first, second = resolve_frame(query), resolve_frame(query)
    assert first == second
    assert _country_index().weigh(query) == _country_index().weigh(query)
    assert _zone_index().weigh(query) == _zone_index().weigh(query)


def test_a_polygon_whose_centroid_lands_in_the_other_country_still_resolves_by_area() -> None:
    """`area.md`: **largest intersection, never a centroid** — with a shape that separates them.

    A blob west of the Rhine with two long thin arms reaching east. The arms carry little area
    but a long lever, so the centroid is dragged across the border: it lands **outside the
    polygon entirely** (the ordinary case for a concave or multi-part area, which is why the
    card names it) and **inside the losing country**. A centroid lookup answers `DE`; the rule
    answers `FR`. Both facts are asserted, so this test cannot pass by coincidence.
    """
    shape = unary_union(
        [
            box(7.60, 48.55, 7.78, 48.65),  # the blob, in France
            box(7.78, 48.556, 8.60, 48.564),  # a thin arm reaching into Germany
            box(7.78, 48.636, 8.60, 48.644),  # and another
        ]
    )
    centroid = shape.centroid
    assert not shape.contains(centroid)
    probe = 0.0005
    at_centroid = box(
        centroid.x - probe, centroid.y - probe, centroid.x + probe, centroid.y + probe
    )
    assert resolve_country_code(at_centroid) == "DE"
    weights = _country_index().weigh(shape)
    assert weights["FR"] > weights["DE"] > 0.0
    assert resolve_frame(shape) == LocalFrame(timezone="Europe/Paris", country_code="FR")


# ── refusal ───────────────────────────────────────────────────────────────────


def test_open_ocean_is_refused_rather_than_defaulted() -> None:
    """The refusal, and *which half* refuses — which is the part a default would hide.

    The timezone dataset tiles the oceans, so open water answers a clock quite happily. If the
    frame ever came back for this polygon it would be because the country half had been given
    a fallback, and the assertion on `Etc/GMT+9` is what stops the test being satisfied by a
    total failure instead.
    """
    ocean = area(OPEN_OCEAN)
    assert resolve_timezone(ocean) == "Etc/GMT+9"
    assert _country_index().weigh(ocean) == {}
    with pytest.raises(FrameUnresolved):
        resolve_country_code(ocean)
    with pytest.raises(FrameUnresolved):
        resolve_frame(ocean)


def test_a_gap_in_the_zone_data_refuses_rather_than_falling_back_to_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one refusal no real geometry can reach, so the gap is injected instead.

    The pinned zone set tiles the entire globe — the poles resolve `Antarctica/*`, the open
    Pacific `Etc/GMT+9`, the antimeridian `Etc/GMT±12` — so `resolve_timezone` never actually
    runs out of candidates today. That is precisely why this test exists: without it a `return
    "UTC"` slipped into that branch passes the whole file (verified by mutation, 2026-08-08),
    and a silent UTC fallback is the failure `area.md` names by hand. An empty index is a
    stand-in for a future dataset with a hole in it, and the assertion is that the hole is
    reported rather than papered over.
    """
    empty = _Weigher(keys=(), tree=STRtree([]), load=lambda _index: box(0, 0, 1, 1))
    monkeypatch.setattr(frame_module, "_zone_index", lambda: empty)
    with pytest.raises(FrameUnresolved):
        resolve_timezone(area(RHODES))
    with pytest.raises(FrameUnresolved):
        resolve_frame(area(RHODES))


@pytest.mark.parametrize(
    "geometry",
    [
        box(28.216, 36.440, 28.232, 36.451).exterior,  # a ring: no area
        box(28.216, 36.440, 28.232, 36.451).centroid,  # a point: no area
        box(28.216, 36.440, 28.216, 36.451),  # zero width
    ],
)
def test_a_geometry_with_no_area_is_refused_before_it_can_tie_everything(
    geometry: BaseGeometry,
) -> None:
    """Every candidate would score 0.0, every candidate would tie, and the tie-break would win.

    That is the dangerous shape: a confident alphabetically-first country drawn from a
    degenerate input. It is a `GeometryError` and not a `FrameUnresolved` because the caller
    passed something that is not an area — a different fact from "no country covers this".
    """
    with pytest.raises(GeometryError):
        resolve_frame(geometry)


def test_a_polygon_outside_epsg_4326_is_refused_as_a_crs_mistake() -> None:
    """Web Mercator metres intersect nothing; "no country" would read as open ocean."""
    with pytest.raises(GeometryError):
        resolve_frame(box(3_140_000.0, 4_360_000.0, 3_150_000.0, 4_370_000.0))


# ── the accepted coarseness, named rather than discovered ─────────────────────


def test_the_named_limits_of_a_1_to_10m_boundary_set() -> None:
    """Two areas this dataset gets *wrong*, pinned so the wrongness is visible and stable.

    Baarle-Hertog is ADR-0029's own named limit: 1:10m has no BE enclaves there, so the bbox
    is all `NL`. The Vatican is a limit the ADR did **not** name, and it is worth recording
    because the ADR cites a Vatican bbox as a reason to prefer 1:10m: the row does exist here
    (1:50m has one too, contrary to the ADR's wording), but it is a ~110 m token square rather
    than the real 0.44 km² state, so any walkable delimitation of Vatican City is mostly
    Italy and resolves to `IT` — under **both** scales. Gibraltar, not the Vatican, is what
    distinguishes them. The frame stays *coherent* (`IT` with `Europe/Rome`), which is the
    property that keeps a plan honest; it is simply the coarser of the two right answers.
    """
    assert resolve_country_code(area(BAARLE)) == "NL"
    assert resolve_frame(area(VATICAN)) == LocalFrame(timezone="Europe/Rome", country_code="IT")

    everything = gpd.read_file(natural_earth_uri())
    vatican = everything.loc[everything[COUNTRY_CODE_COLUMN] == "VA"].geometry.iloc[0]
    min_lon, min_lat, max_lon, max_lat = vatican.bounds
    assert max_lon - min_lon < 0.002 and max_lat - min_lat < 0.002  # a token, not a state


# ── offline, always ───────────────────────────────────────────────────────────


def test_nothing_here_opens_a_socket(monkeypatch: pytest.MonkeyPatch) -> None:
    """The no-network guarantee, enforced rather than described.

    Caches are cleared first so this exercises the real file reads — the boundary zip and the
    timezone binaries — and not a warm process. An area resolve that phoned a timezone API
    would be neither reproducible in CI nor available to a compile.
    """

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("commons.frame opened a socket; it must be entirely offline")

    _country_index.cache_clear()
    _zone_index.cache_clear()
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    assert resolve_frame(area(RHODES)) == LocalFrame(timezone="Europe/Athens", country_code="GR")


# ── holiday coverage — exposed here, gated at T018 ────────────────────────────


def test_holiday_coverage_is_alpha_2_only() -> None:
    """`list_supported_countries()` also returns alpha-3 aliases; `area.country_code` is alpha-2.

    A membership test that accepted `GRC` would report coverage for a value this project never
    produces — so the filter is asserted from both sides.
    """
    covered = holiday_countries()
    assert "GR" in covered
    assert "GRC" not in covered
    assert all(len(code) == 2 and code.isupper() for code in covered)
    assert len(covered) == 251


def test_every_resolvable_country_has_an_oracle_calendar_today() -> None:
    """The honest state of the coverage flag: with these two pins it never refuses.

    Natural Earth's 239 coded countries are a strict subset of the 251 `holidays` covers, so
    `has_holiday_calendar` is `True` for every frame this resolver can produce. Recorded as an
    assertion rather than as a comment because it is the thing that would *change*: the day a
    boundary update or a `holidays` release breaks the subset relation, T018's cross-check
    acquires a case it has never seen, and this test is where that shows up.
    """
    frame = gpd.read_file(natural_earth_uri(), columns=[COUNTRY_CODE_COLUMN])
    resolvable = {code for code in frame[COUNTRY_CODE_COLUMN] if code != UNCODED}
    assert len(resolvable) == 239
    assert resolvable <= holiday_countries()
    assert resolve_frame(area(RHODES)).has_holiday_calendar is True


# ── the wiring into resolve_area (the node is where derivation happens) ───────
# These live here rather than in `tests/test_resolve_area.py` because they are about the
# frame, not about resolution policy — and because that module's fixtures are deliberately
# fictional places, which a derivation from real boundary data cannot use.


def test_a_user_drawn_bbox_carries_a_derived_frame() -> None:
    """`area.md` example 3: a delimitation says *where*, never *which clock*."""
    resolved = resolve_area(AreaRequest(bbox=RHODES))
    assert resolved.timezone == "Europe/Athens"
    assert resolved.country_code == "GR"
    assert resolved.source.kind == "user"  # still the user's own data, still not bundleable


def test_a_name_resolved_area_takes_its_frame_from_the_winning_polygon() -> None:
    """Not from the name: a division called anything says nothing about the clock it keeps."""
    from tests.test_resolve_area import FakeLookup, candidate

    winner = candidate("anything at all", area(RHODES))
    resolved = resolve_area(
        AreaRequest(name="anything at all"), divisions=FakeLookup(results=(winner,))
    )
    assert (resolved.timezone, resolved.country_code) == ("Europe/Athens", "GR")
    assert resolved.candidates == (winner,)


def test_an_area_with_no_frame_is_a_422_not_a_500() -> None:
    """The refusal has to reach the user as *unprocessable*, not as a crash or a fallback.

    `AreaFrameUnresolved` subclasses `AreaInvalid` precisely so `api/areas.py`'s existing
    mapping covers it without a new branch; asserting both types is what pins that.
    """
    with pytest.raises(AreaFrameUnresolved) as caught:
        resolve_area(AreaRequest(bbox=OPEN_OCEAN))
    assert isinstance(caught.value, AreaInvalid)
