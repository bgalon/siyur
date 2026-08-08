"""T040 — ``POST /areas`` against the contract (`contracts/areas.md`).

Two tiers in one file, because the contract spans both:

- **Tier 1** — auth and the shapes that never reach storage. The app is given a session
  factory bound to an engine that points at a dead port; SQLAlchemy connects lazily, so a
  request that answers ``401``/``422`` *proves* it touched no database by not hanging.
- **Tier 2** (``@pytest.mark.integration``) — resolution, the ``ST_Within`` coverage count
  and the reuse/refresh signal over real PostGIS, seeded from the committed Rhodes fixtures
  through the real adapters. `tests/conftest.py` skips these cleanly with no database.

Nothing here reaches the network: Overture divisions and Nominatim are both injected
(`api/deps.py` seams), so a name lookup is deterministic and offline. Rhodes is a *fixture*,
never a default — every request in this file supplies its own delimitation (FR-001).

The harness at the top is shared with `test_api_sites.py` / `test_api_research.py`, the way
`test_pipeline.py` reuses the node tests' adapters: one place to change, not three.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape
from shapely.geometry import box, mapping
from shapely.geometry.base import BaseGeometry
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from api.app import create_app
from api.config import Settings
from commons.db import SRID, Area
from commons.merge import merge_records
from commons.models import SourceRef
from commons.repository import sites_within, upsert_sites
from planner.nodes.resolve_area import AreaCandidate
from tests.test_planner_research import OVERPASS_JSON, osm, overture

# ── the fixtures' own geography (tests/fixtures/README.md) ────────────────────

FIXTURES = Path(__file__).parent / "fixtures"
#: The Rhodes old-town area both committed fixtures were captured for (lon, lat).
AREA = box(28.216, 36.440, 28.232, 36.451)
BBOX = (28.216, 36.440, 28.232, 36.451)
#: Somewhere the fixtures say nothing about — the "empty area" control (SC-006).
# Sahara, ~15°N 0°E — genuinely empty of POIs, which is what this fixture is *for*,
# but on LAND. It was Null Island (0,0) until 2026-08-08, when T008 made area resolution
# derive `timezone`/`country_code` from the polygon and refuse an unresolvable frame.
# Null Island is open ocean in the Gulf of Guinea, so every test delimiting it began
# answering 422. The derivation changed, not the test's intent: "nothing here" still
# holds, and now the frame resolves. A deliberately-fictional coordinate must now also
# be a *possible* one.
NOWHERE_BBOX = (0.0, 15.0, 0.001, 15.001)
OBSERVED = date(2026, 8, 1)

_SESSION_SECRET = "test-session-secret-not-a-real-key"  # noqa: S105 (test fixture)
#: An engine that resolves but never connects — SQLAlchemy is lazy, so a Tier-1 request
#: that returns without hanging has demonstrably not gone near a database.
_DEAD_URL = "postgresql+psycopg://siyur:siyur@127.0.0.1:1/nonexistent"


# ── harness (shared with test_api_sites.py / test_api_research.py) ────────────


def api_settings() -> Settings:
    """Auth *configured* (so the mocked OIDC callback can mint a session), zero real creds."""
    return Settings(
        google_client_id="test-client-id.apps.googleusercontent.com",
        google_client_secret="test-client-secret",
        session_secret=_SESSION_SECRET,
        session_secret_is_ephemeral=False,
        oauth_redirect_uri="http://testserver/auth/callback",
        post_login_redirect="/",
        session_https_only=False,
    )


def build_app(engine: Engine | None = None, **state: Any) -> FastAPI:
    """The real app, with the `api/deps.py` seams pointed at test doubles."""
    app = create_app(api_settings())
    app.state.session_factory = sessionmaker(
        bind=engine if engine is not None else create_engine(_DEAD_URL)
    )
    for key, value in state.items():
        setattr(app.state, key, value)
    return app


def sign_in(app: FastAPI, client: TestClient, sub: str = "google-sub-a") -> TestClient:
    """Mint a session cookie through the real callback with a mocked token exchange.

    The auth path is exercised rather than bypassed — no ``dependency_overrides`` on
    ``require_user``, because that is exactly the dependency api/AGENTS.md protects.
    """
    app.state.oauth.google.authorize_access_token = AsyncMock(
        return_value={"userinfo": {"sub": sub, "email": f"{sub}@example.com"}}
    )
    assert client.get("/auth/callback?state=x&code=y", follow_redirects=False).status_code == 303
    return client


def signed_in_client(app: FastAPI, sub: str = "google-sub-a") -> TestClient:
    return sign_in(app, TestClient(app), sub)


def parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    """``event:``/``data:`` frames → ``(name, payload)`` pairs, in arrival order."""
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        name = ""
        payload: dict[str, Any] = {}
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                payload = json.loads(line.removeprefix("data: "))
        frames.append((name, payload))
    return frames


def seed_commons(session: Session, *, osm_only: bool = False) -> int:
    """Land the committed fixtures in the commons through the real adapters + merge.

    Returns the number of ``site`` rows written. ``osm_only`` keeps the Greek-named,
    ODbL-stamped subset — enough for the sites contract and far quicker than 225 upserts.

    Seeding the commons is **not** the same as researching an area, and since ADR-0018 the
    two are no longer conflated: this writes records, :func:`record_research` records that
    somebody looked. A test that wants ``covered=true`` needs both.
    """
    records = list(osm(node=OVERPASS_JSON).fetch(AREA))
    if not osm_only:
        records.extend(overture().fetch(AREA))
    results = list(merge_records(records))
    report = upsert_sites(session, results)
    session.commit()
    return report.created


def record_research(session: Session, polygon: BaseGeometry, sub: str = "google-sub-a") -> None:
    """A completed research pass over ``polygon`` for ``sub`` — the state ``covered`` reads."""
    session.add(
        Area(
            polygon=from_shape(polygon, srid=SRID),
            created_by=sub,
            researched_at=datetime.now(UTC),
        )
    )
    session.commit()


@dataclass
class FakeLookup:
    """A :class:`DivisionsLookup` / :class:`Geocoder` double — offline, deterministic."""

    candidates: tuple[AreaCandidate, ...] = ()
    #: Every name it was asked about, so a test can assert the fallback was (not) consulted.
    asked: list[str] = field(default_factory=list)

    def search(self, name: str) -> Sequence[AreaCandidate]:
        self.asked.append(name)
        return self.candidates


def candidate(name: str, confidence: float, *, at: float = 28.22) -> AreaCandidate:
    return AreaCandidate(
        name=name,
        polygon=box(at, 36.44, at + 0.01, 36.45),
        source=SourceRef(kind="overture", id=f"division:{name}", license="CDLA-Permissive-2.0"),
        confidence=confidence,
    )


# ── Tier 1: auth and validation, no database ─────────────────────────────────


def test_post_areas_is_401_unauthenticated() -> None:
    """The write path is auth-gated (ADR-0008) — and 401 lands before anything is parsed."""
    response = TestClient(build_app()).post("/areas", json={"bbox": BBOX})
    assert response.status_code == 401


def test_research_is_401_unauthenticated() -> None:
    response = TestClient(build_app()).post(f"/areas/{uuid4()}/research", json={})
    assert response.status_code == 401


def test_no_delimitation_at_all_is_422() -> None:
    app = build_app(divisions_lookup=FakeLookup(), geocoder=FakeLookup())
    response = signed_in_client(app).post("/areas", json={})
    assert response.status_code == 422
    assert "name" in response.json()["detail"]


@pytest.mark.parametrize(
    "bbox",
    [
        [28.232, 36.451, 28.216, 36.440],  # decreasing on both axes
        [28.216, 36.451, 28.232, 36.440],  # decreasing on latitude only
        [28.216, 36.440, 200.0, 36.451],  # longitude out of EPSG:4326 range
        [28.216, 36.440, 28.232, 96.0],  # latitude out of range
        [28.216, 36.440, 28.216, 36.451],  # zero width: encloses no space
    ],
)
def test_a_bbox_that_is_not_a_usable_area_is_422(bbox: list[float]) -> None:
    """Each ordinate is checked against **its own** axis, and both must increase.

    A *transposed* Rhodes bbox is deliberately not in this list: 28.2 and 36.4 are both
    legal latitudes **and** both legal longitudes, and the swapped pair still increases on
    both axes — so it is a geometrically valid box somewhere else on Earth and no validator
    can reject it. That failure mode is caught downstream, by the query returning nothing
    (`test_api_sites.py`), which is the only place it is visible.
    """
    app = build_app(divisions_lookup=FakeLookup(), geocoder=FakeLookup())
    assert signed_in_client(app).post("/areas", json={"bbox": bbox}).status_code == 422


def test_a_bbox_with_the_wrong_number_of_ordinates_is_422() -> None:
    app = build_app(divisions_lookup=FakeLookup(), geocoder=FakeLookup())
    client = signed_in_client(app)
    assert client.post("/areas", json={"bbox": [28.216, 36.440, 28.232]}).status_code == 422
    assert client.post("/areas", json={"bbox": ["a", "b", "c", "d"]}).status_code == 422


def test_an_unknown_field_in_the_body_is_refused() -> None:
    app = build_app(divisions_lookup=FakeLookup(), geocoder=FakeLookup())
    response = signed_in_client(app).post("/areas", json={"bbox": BBOX, "force": True})
    assert response.status_code == 422


# ── Tier 1: name resolution (both seams injected — no Overture S3, no Nominatim) ──


def test_an_ambiguous_name_is_404_with_the_candidates_to_choose_from() -> None:
    divisions = FakeLookup((candidate("Old Town", 0.6), candidate("Old Town", 0.6, at=28.30)))
    app = build_app(divisions_lookup=divisions, geocoder=FakeLookup())
    response = signed_in_client(app).post("/areas", json={"name": "Old Town"})

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert len(detail["candidates"]) == 2
    # Enough to disambiguate with, and each still carries where it came from.
    for option in detail["candidates"]:
        assert option["source"]["kind"] and option["source"]["license"]
        assert len(option["bbox"]) == 4


def test_an_unresolvable_name_is_404_and_the_geocoder_was_the_fallback() -> None:
    divisions, geocoder = FakeLookup(), FakeLookup()
    app = build_app(divisions_lookup=divisions, geocoder=geocoder)
    response = signed_in_client(app).post("/areas", json={"name": "Nowhere At All"})

    assert response.status_code == 404
    assert response.json()["detail"]["candidates"] == []
    # Overture divisions is authoritative; the geocoder is consulted only on its silence.
    assert divisions.asked == geocoder.asked == ["Nowhere At All"]


# ── Tier 2: coverage over real PostGIS ───────────────────────────────────────

integration = pytest.mark.integration


@pytest.fixture
def app(db_engine: Engine, db_session: Session) -> FastAPI:
    """The app bound to the migrated test database; ``db_session`` empties it first.

    ``research_adapters=()`` is not laziness: these are the *areas* tests, and an empty
    adapter list is the only way a research call here provably reaches neither Overture's
    cloud storage nor Overpass. The real fixtures drive `test_api_research.py`.
    """
    return build_app(engine=db_engine, research_adapters=(), divisions_lookup=FakeLookup())


@integration
def test_an_empty_commons_reports_no_coverage(app: FastAPI) -> None:
    response = signed_in_client(app).post("/areas", json={"bbox": BBOX})

    assert response.status_code == 200
    body = response.json()
    assert body["polygon"]["type"] == "Polygon"
    assert body["coverage"] == {
        "known_site_count": 0,
        "researched_fraction": 0.0,
        "covered": False,
        "stalest_observed_at": None,
        "refresh_available": False,
    }


@integration
def test_coverage_count_is_the_st_within_count(app: FastAPI, db_session: Session) -> None:
    """The contract's ``known_site_count`` is PostGIS's answer, not a re-derived one."""
    seed_commons(db_session, osm_only=True)
    record_research(db_session, AREA)
    expected = len(sites_within(db_session, AREA))
    assert expected > 0, "the fixture must seed sites, or this asserts nothing"

    body = signed_in_client(app).post("/areas", json={"bbox": BBOX}).json()

    assert body["coverage"]["known_site_count"] == expected
    assert body["coverage"]["covered"] is True
    assert body["coverage"]["researched_fraction"] == pytest.approx(1.0)
    # FR-006 / US2: a covered area always offers a refresh.
    assert body["coverage"]["refresh_available"] is True
    assert body["coverage"]["stalest_observed_at"] is not None


@integration
def test_reposting_a_covered_area_reports_covered_and_refresh_available(
    app: FastAPI, db_session: Session
) -> None:
    """Backs ``test_commons_reuse_dedupe`` (ADR-0008): the second ask reuses, not re-researches."""
    client = signed_in_client(app)
    first = client.post("/areas", json={"bbox": BBOX}).json()
    assert first["coverage"]["covered"] is False

    seed_commons(db_session, osm_only=True)
    record_research(db_session, AREA)

    second = client.post("/areas", json={"bbox": BBOX}).json()
    assert second["coverage"]["covered"] is True
    assert second["coverage"]["refresh_available"] is True
    # A second delimitation is a second area row — the commons behind it is shared.
    assert second["area_id"] != first["area_id"]


@integration
def test_the_enlarged_viewport_around_a_researched_area_is_not_covered(
    app: FastAPI, db_session: Session
) -> None:
    """The ADR-0018 regression, at the contract boundary this time.

    ``delimit.ts`` sends the map viewport, so "pan out one step" is a strictly larger bbox
    around the one that was researched. Under ``covered = known_site_count > 0`` the whole
    enlarged region came back covered — the client then shows existing data and a refresh
    affordance *instead of researching* (FR-006), for ground nobody has looked at, with
    nothing in the response to say so.
    """
    seed_commons(db_session, osm_only=True)
    record_research(db_session, AREA)
    minx, miny, maxx, maxy = AREA.bounds
    enlarged = [minx - 0.5, miny - 0.5, maxx + 0.5, maxy + 0.5]

    body = signed_in_client(app).post("/areas", json={"bbox": enlarged}).json()

    # The sites are genuinely in there — this is the state the old rule mistook for coverage.
    assert body["coverage"]["known_site_count"] > 0
    assert body["coverage"]["covered"] is False
    assert body["coverage"]["researched_fraction"] < 0.01


@integration
def test_a_completed_research_pass_is_what_makes_an_area_covered(
    app: FastAPI, db_session: Session
) -> None:
    """The other half of the fix: the endpoint records completion, so reuse has state to read.

    ``research_adapters=()`` means the pass finds nothing at all — and that is the sharper
    version of the claim. ``covered`` flips on the *pass*, not on the records: an area
    researched and found empty is covered (SC-006), which a site-count rule can never say.
    """
    client = signed_in_client(app)
    first = client.post("/areas", json={"bbox": NOWHERE_BBOX}).json()
    assert first["coverage"]["covered"] is False
    area_id = UUID(first["area_id"])
    delimited = db_session.get(Area, area_id)
    assert delimited is not None and delimited.researched_at is None, "delimited, not researched"

    assert client.post(f"/areas/{area_id}/research", json={}).status_code == 200

    db_session.expire_all()
    stamped = db_session.execute(select(Area.researched_at).where(Area.id == area_id)).scalar_one()
    assert stamped is not None, "a committed pass must record that it happened"
    second = client.post("/areas", json={"bbox": NOWHERE_BBOX}).json()
    assert second["coverage"]["known_site_count"] == 0, "the empty area really is empty"
    assert (second["coverage"]["covered"], second["coverage"]["refresh_available"]) == (True, True)


@integration
def test_a_drawn_polygon_is_resolved_and_a_degenerate_one_is_422(app: FastAPI) -> None:
    client = signed_in_client(app)
    drawn = client.post("/areas", json={"polygon": mapping(AREA)})
    assert drawn.status_code == 200
    assert drawn.json()["polygon"]["type"] == "Polygon"

    # A ring that encloses no space is "technically a geometry, useless as an area".
    flat = {"type": "Polygon", "coordinates": [[[28.2, 36.4], [28.3, 36.4], [28.2, 36.4]]]}
    assert client.post("/areas", json={"polygon": flat}).status_code == 422
    assert client.post("/areas", json={"polygon": {"type": "Point"}}).status_code == 422


@integration
def test_the_resolved_area_is_persisted_and_usable_by_its_owner(app: FastAPI) -> None:
    """The ``404``-on-unknown-id promise needs server-side state, so the row must be real."""
    client = signed_in_client(app)
    area_id = client.post("/areas", json={"bbox": NOWHERE_BBOX}).json()["area_id"]

    # A research pass over an *empty* area is the cheapest proof the row round-trips.
    streamed = client.post(f"/areas/{area_id}/research", json={"force_refresh": False})
    assert streamed.status_code == 200
    assert [name for name, _ in parse_sse(streamed.text)][-1] == "done"
