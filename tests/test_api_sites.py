"""T040 — ``GET /sites?bbox=…`` against the contract (`contracts/sites.md`).

The consumer of this endpoint is `web/src/map/sites.ts`, which was built against the same
contract with ``fetch`` mocked. So the assertions here are deliberately the *client's*
questions: does every value carry a stamp it can render a chip from, is the ODbL credit in
``attribution[]``, does a Greek record arrive with both ``el`` and ``el-Latn``.

Tier 1 covers auth and ``bbox`` parsing with no database at all (the session factory points
at a dead port — see `test_api_areas.py`); Tier 2 runs the real PostGIS ``&&`` filter over
the committed Overpass fixture, seeded through the real adapter, the real merge and the real
transliteration, so what is queried is what a research pass would actually have written.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely import Point
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from commons.db import Site, to_db_point
from commons.repository import sites_in_bbox, upsert_sites
from planner.nodes.curate import curate
from tests.test_api_areas import AREA, OBSERVED, build_app, signed_in_client
from tests.test_planner_research import OVERPASS_JSON, osm

#: The query-string form the client sends (`web/src/map/sites.ts` ``formatBbox``).
BBOX_QUERY = "28.216,36.440,28.232,36.451"
BBOX = (28.216, 36.440, 28.232, 36.451)

integration = pytest.mark.integration


# ── Tier 1: auth and bbox parsing, no database ───────────────────────────────


def test_sites_is_401_unauthenticated() -> None:
    """World-readable *to a signed-in user* — 'signed-in' is the whole access rule."""
    response = TestClient(build_app()).get(f"/sites?bbox={BBOX_QUERY}")
    assert response.status_code == 401


@pytest.mark.parametrize(
    "query",
    [
        "",  # the parameter is required
        "?bbox=",
        "?bbox=28.216,36.440,28.232",  # three ordinates
        "?bbox=28.216,36.440,28.232,36.451,1",  # five
        "?bbox=a,b,c,d",
        "?bbox=36.440,28.216,36.451,281.0",  # longitude out of range
        "?bbox=28.216,96.0,28.232,97.0",  # latitude out of range
    ],
)
def test_a_malformed_bbox_is_422_before_storage_is_touched(query: str) -> None:
    response = signed_in_client(build_app()).get(f"/sites{query}")
    assert response.status_code == 422


# ── Tier 2: the real PostGIS filter over the committed fixture ───────────────


@pytest.fixture
def app(db_engine: Engine, db_session: Session) -> FastAPI:
    return build_app(engine=db_engine)


@pytest.fixture
def seeded(db_session: Session) -> int:
    """The Overpass fixture through the real adapter → merge → transliterate → commons.

    Curated rather than merged-only, because ``el-Latn`` is derived at curate time (T059)
    and the FR-008 display-name contract is about what the *endpoint* returns.
    """
    curated = curate(list(osm(node=OVERPASS_JSON).fetch(AREA)), observed_at=OBSERVED)
    report = upsert_sites(db_session, curated.results)
    db_session.commit()
    assert report.created > 0
    return report.created


def _get_sites(app: FastAPI, bbox: str = BBOX_QUERY) -> dict[str, Any]:
    response = signed_in_client(app).get(f"/sites?bbox={bbox}")
    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    return body


def _values(site: dict[str, Any]) -> list[dict[str, Any]]:
    """Every ``SourcedValue``-shaped object anywhere in a returned record."""
    found: list[dict[str, Any]] = []

    def walk(payload: Any) -> None:
        if isinstance(payload, dict):
            if "value" in payload and "source" in payload:
                found.append(payload)
            for nested in payload.values():
                walk(nested)
        elif isinstance(payload, list):
            for item in payload:
                walk(item)

    walk(site)
    return found


@integration
def test_bbox_filtering_matches_the_repository_query(
    app: FastAPI, db_session: Session, seeded: int
) -> None:
    expected = sites_in_bbox(db_session, BBOX)
    body = _get_sites(app)

    assert len(body["sites"]) == len(expected) == seeded
    assert [site["id"] for site in body["sites"]] == [str(record.id) for record in expected]


@integration
def test_a_lat_first_bbox_returns_nothing_rather_than_the_wrong_hemisphere(
    app: FastAPI, seeded: int
) -> None:
    """Rhodes' ordinates are both legal latitudes: only the query result exposes a swap."""
    assert _get_sites(app, "36.440,28.216,36.451,28.232")["sites"] == []


@integration
def test_every_value_of_every_returned_site_carries_a_source(app: FastAPI, seeded: int) -> None:
    """FR-003 / SC-002 — 100%, which is the only passing score for a provenance invariant."""
    body = _get_sites(app)
    assert body["sites"]

    for site in body["sites"]:
        values = _values(site)
        assert values, "a record with no sourced value is not a record"
        for value in values:
            assert value["source"] is not None
            assert value["source"]["kind"] and value["source"]["license"]
            assert value["bundleable"] is not None


@integration
def test_a_synthetic_unstamped_row_is_never_returned(
    app: FastAPI, db_session: Session, seeded: int
) -> None:
    """The DB boundary refuses a half-record — it is not repaired, and not half-rendered."""
    db_session.add(
        Site(
            id=uuid4(),
            gers_id=None,
            geom=to_db_point(Point(28.224, 36.444)),
            # A bare name with no stamp: exactly what must never reach a marker.
            fields={"names": {"en": {"value": "Unstamped Place"}}, "location": None},
            updated_at=datetime.now(UTC),
        )
    )
    db_session.commit()

    body = _get_sites(app)
    assert len(body["sites"]) == seeded, "the unstamped row must not be returned"
    assert "Unstamped Place" not in str(body)


@integration
def test_an_osm_derived_site_credits_openstreetmap(app: FastAPI, seeded: int) -> None:
    """FR-004 / SC-002 — the exact string comes from the stamp, never from the client."""
    body = _get_sites(app)
    assert "© OpenStreetMap contributors" in body["attribution"]
    kinds = {value["source"]["kind"] for site in body["sites"] for value in _values(site)}
    assert kinds == {"osm"}


@integration
def test_attribution_is_the_union_of_what_the_stamps_carry(app: FastAPI, seeded: int) -> None:
    body = _get_sites(app)
    stamped = {
        value["source"]["attribution"]
        for site in body["sites"]
        for value in _values(site)
        if value["source"].get("attribution")
    }
    assert set(body["attribution"]) == stamped
    assert body["attribution"] == sorted(set(body["attribution"]))


@integration
def test_a_greek_named_site_returns_both_el_and_el_latn_with_the_original_intact(
    app: FastAPI, seeded: int
) -> None:
    """FR-008 / US3 — the Latin form is *added*; the source script is never overwritten."""
    greek = [site for site in _get_sites(app)["sites"] if "el" in site["names"]]
    assert greek, "the Overpass fixture must carry Greek names, or this asserts nothing"

    transliterated = [site for site in greek if "el-Latn" in site["names"]]
    assert transliterated, "no `el-Latn` display name was derived"

    for site in transliterated:
        original, latin = site["names"]["el"], site["names"]["el-Latn"]
        assert original["value"] != latin["value"]
        assert not original["value"].isascii(), "the original must still be in Greek script"
        assert latin["value"].isascii()
        # A derived value inherits its parent's stamp — it is not invented, so it is cited.
        assert latin["source"] == original["source"]


@integration
def test_an_empty_viewport_is_an_empty_answer_not_an_error(app: FastAPI, seeded: int) -> None:
    body = _get_sites(app, "0.0,0.0,0.001,0.001")
    assert body == {"sites": [], "attribution": []}
