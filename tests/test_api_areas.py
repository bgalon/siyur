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
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
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
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker

from api.app import create_app
from api.config import Settings
from commons.db import SRID, Area
from commons.merge import merge_records
from commons.models import SourceRef
from commons.repository import LIST_LIMIT_MAX, list_areas, sites_within, upsert_sites
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


class ListRecordingSession:
    """A stand-in ``Session`` that records the ``SELECT`` a list read emits and returns none.

    The list endpoints' privacy boundary is a ``WHERE`` clause, and a ``WHERE`` clause is not
    observable in a response: a read that fetched every row and filtered them in Python would
    answer ``{"areas": []}`` for a second user *and would have read the first user's rows on
    the way*. `test_hitl_gate.py::test_every_plan_statement_filters_on_user_id` makes the same
    argument about the gate's transitions; this is that harness, shaped for a read that ends
    in ``.all()``.

    Shared with `test_api_plans.py` — one recorder, so both list reads are asserted the same
    way rather than each growing its own almost-identical double.
    """

    def __init__(self) -> None:
        self.statements: list[Any] = []

    def execute(self, statement: Any, *args: Any, **kwargs: Any) -> Any:
        self.statements.append(statement)
        return SimpleNamespace(all=lambda: [])

    def compiled(self, index: int = 0) -> Any:
        """The compiled statement — ``str()`` for the text, ``.params`` for bound values."""
        return self.statements[index].compile(dialect=postgresql.dialect())  # type: ignore[no-untyped-call]


@dataclass
class FakeLookup:
    """A :class:`DivisionsLookup` / :class:`Geocoder` double — offline, deterministic."""

    candidates: tuple[AreaCandidate, ...] = ()
    #: Every name it was asked about, so a test can assert the fallback was (not) consulted.
    asked: list[str] = field(default_factory=list)
    #: The `window=` each call carried (ADR-0036) — `None` when the caller sent none.
    windows: list[tuple[float, float, float, float] | None] = field(default_factory=list)

    def search(
        self, name: str, *, window: tuple[float, float, float, float] | None = None
    ) -> Sequence[AreaCandidate]:
        self.asked.append(name)
        self.windows.append(window)
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


def test_get_areas_is_401_unauthenticated() -> None:
    """The list is personal data, so it is behind the same door as the write path."""
    assert TestClient(build_app()).get("/areas").status_code == 401


def test_the_area_list_query_is_scoped_to_the_caller_and_bounded() -> None:
    """PRD §13 #4 asserted on the **SQL**, because a response cannot show a missing ``WHERE``.

    A list read is where an unscoped query stops being one leaked row and becomes the table:
    an implementation that selected every area and filtered in Python would return the same
    ``[]`` for a second user that a correct one does, having read everyone's rows to get
    there. So the scope is checked where it lives — and so is the ``LIMIT``, since an
    unbounded list is the other failure this read is not allowed to have.
    """
    session = ListRecordingSession()
    assert list_areas(session, created_by="google-sub-a") == ()  # type: ignore[arg-type]

    assert len(session.statements) == 1, "a list read that emits no SQL asserts nothing"
    compiled = session.compiled()
    assert "area.created_by = " in str(compiled), str(compiled)
    assert compiled.params["created_by_1"] == "google-sub-a", compiled.params
    assert "LIMIT" in str(compiled), str(compiled)
    assert "ORDER BY area.created_at DESC" in str(compiled), str(compiled)
    # The ring itself is never transferred: PostGIS returns four ordinates. A selected
    # geometry column compiles to `ST_AsEWKB(area.polygon)`, so its absence is the check —
    # `area.polygon` alone appears inside the `ST_XMin(...)` calls and proves nothing.
    assert "ST_AsEWKB" not in str(compiled), str(compiled)
    assert "ST_XMin(area.polygon)" in str(compiled), str(compiled)


def test_the_area_list_limit_is_capped_at_the_request_boundary() -> None:
    """Out of range is a ``422``, never a silently different page than the one asked for."""
    client = signed_in_client(build_app())
    assert client.get("/areas", params={"limit": 0}).status_code == 422
    assert client.get("/areas", params={"limit": -1}).status_code == 422
    assert client.get("/areas", params={"limit": LIST_LIMIT_MAX + 1}).status_code == 422
    assert client.get("/areas", params={"limit": "all"}).status_code == 422


def test_a_limit_inside_the_range_reaches_storage() -> None:
    """The control for the ``422``s above: an in-range limit is *not* refused at the boundary.

    It goes on to fail against the dead engine, which is what proves the refusals are the
    limit rule rather than the request being rejected wholesale.
    """
    client = signed_in_client(build_app())
    with pytest.raises(Exception, match="(?i)connect|operational"):
        client.get("/areas", params={"limit": LIST_LIMIT_MAX})


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


# ── ADR-0036 · the search window reaches the divisions lookup ──────────────────────


def test_a_window_is_accepted_and_reaches_the_divisions_lookup() -> None:
    """The 73 s → 18 s lever, plumbed.

    `AreaRequestBody` is `extra="forbid"`, so before this field existed a client sending its
    viewport got a `422` rather than a fast answer. Asserted on the ambiguous-candidates
    `404` rather than a `200` so it stays a Tier-1 test: a `200` persists an `Area` and would
    drag a database into a question that is purely about request plumbing. A `422` here would
    mean the field was refused, which is precisely the regression being guarded.
    """
    divisions = FakeLookup((candidate("Old Town", 0.6), candidate("Old Town", 0.6, at=28.30)))
    app = build_app(divisions_lookup=divisions, geocoder=FakeLookup())

    response = signed_in_client(app).post(
        "/areas", json={"name": "Old Town", "window": [28.0, 36.0, 28.5, 36.5]}
    )

    assert response.status_code == 404  # ambiguous, not rejected
    assert divisions.windows == [(28.0, 36.0, 28.5, 36.5)]


def test_without_a_window_the_lookup_is_told_so_explicitly() -> None:
    """The unwindowed re-ask in ADR-0036 has to be distinguishable from the first pass."""
    divisions = FakeLookup((candidate("Old Town", 0.6), candidate("Old Town", 0.6, at=28.30)))
    app = build_app(divisions_lookup=divisions, geocoder=FakeLookup())

    signed_in_client(app).post("/areas", json={"name": "Old Town"})

    assert divisions.windows == [None]


def test_a_windowed_miss_is_a_plain_404_that_the_client_must_not_trust() -> None:
    """**The endpoint does not retry, and this is the assertion that keeps it that way.**

    A windowed `404` means "not in that box", not "no such area" — and only the caller can
    say "widening the search…" while it re-asks. If this endpoint ever grows a helpful
    server-side retry, ADR-0036's visible-state requirement dies silently and this fails.
    """
    divisions, geocoder = FakeLookup(), FakeLookup()
    app = build_app(divisions_lookup=divisions, geocoder=geocoder)

    response = signed_in_client(app).post(
        "/areas", json={"name": "Somewhere Else", "window": [28.0, 36.0, 28.5, 36.5]}
    )

    assert response.status_code == 404
    # Exactly one windowed divisions call. No second, unwindowed pass happened here.
    assert divisions.windows == [(28.0, 36.0, 28.5, 36.5)]
    # And the geocoder was not consulted: a windowed empty is "not in that box", not the
    # silence Nominatim exists to disambiguate. Were it consulted, an unwindowed hit would
    # turn this into a confident 200 and the client would never widen.
    assert geocoder.asked == []


@pytest.mark.parametrize(
    "window",
    [
        [28.5, 36.0, 28.0, 36.5],  # longitude decreases
        [28.0, 36.5, 28.5, 36.0],  # latitude decreases
        [28.0, 36.0, 28.0, 36.5],  # degenerate: zero width
        [28.0, 95.0, 28.5, 100.0],  # latitude out of range
        [28.0, 36.0, 28.5],  # not four ordinates
    ],
)
def test_a_malformed_window_is_422(window: list[float]) -> None:
    """Refused rather than dropped: a silently-ignored window is a silent 55 s regression."""
    app = build_app(divisions_lookup=FakeLookup(), geocoder=FakeLookup())
    response = signed_in_client(app).post("/areas", json={"name": "Old Town", "window": window})
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


def test_the_404_body_is_nested_under_detail_and_not_at_the_root() -> None:
    """FAIL-017: the client read the root, found nothing, and said nothing for 65 seconds.

    The server was always right and always tested — `test_an_ambiguous_name_is_404_…` has
    read `["detail"]["candidates"]` since it was written. What was missing was anything
    forcing the *client* to agree: `web/src/map/areas.ts` read `candidates` from the root,
    and `web/test/area-disambiguation.test.ts` sent a fixture that also put it at the root.
    Both halves shared one wrong assumption, so the suite was green while the feature had
    never once worked against a real response.

    This asserts the half the client got wrong, in the terms it got it wrong in: the payload
    is under `detail`, and there is **nothing** at the root to read. If somebody ever
    flattens this body, that is a breaking change for the client and this test is what says
    so — the TS fixture names this test for exactly that reason.
    """
    divisions = FakeLookup((candidate("Old Town", 0.6), candidate("Old Town", 0.6, at=28.30)))
    app = build_app(divisions_lookup=divisions, geocoder=FakeLookup())
    response = signed_in_client(app).post("/areas", json={"name": "Old Town"})

    assert response.status_code == 404
    body = response.json()

    # FastAPI serialises `HTTPException(detail=X)` as `{"detail": X}`. The client must read
    # one level down; a client reading the root sees neither key.
    assert "candidates" not in body, (
        "the payload moved to the root — this breaks web/src/map/areas.ts"
    )
    assert "message" not in body, "the payload moved to the root — this breaks web/src/map/areas.ts"
    assert set(body) == {"detail"}, f"unexpected top-level keys: {sorted(body)}"
    assert body["detail"]["candidates"], "candidates must survive inside `detail`"


def test_an_unresolvable_name_is_404_and_the_geocoder_was_the_fallback() -> None:
    divisions, geocoder = FakeLookup(), FakeLookup()
    app = build_app(divisions_lookup=divisions, geocoder=geocoder)
    response = signed_in_client(app).post("/areas", json={"name": "Nowhere At All"})

    assert response.status_code == 404
    assert response.json()["detail"]["candidates"] == []
    # Overture divisions is authoritative; the geocoder is consulted only on its silence.
    assert divisions.asked == geocoder.asked == ["Nowhere At All"]


#: The `404` body, captured from this endpoint and doubled verbatim by
#: `web/test/area-disambiguation.test.ts`. Generated, never hand-edited — regenerate with
#: `SIYUR_UPDATE_WIRE_CAPTURES=1 uv run pytest tests/test_api_areas.py -k wire_capture`.
WIRE_CAPTURE = Path(__file__).resolve().parents[1] / "web/test/fixtures/area-404-wire.json"


def test_the_wire_capture_the_web_suite_doubles_is_the_body_this_endpoint_sends() -> None:
    """The one assertion that would have caught FAIL-017.

    `web/test/area-disambiguation.test.ts` cannot reach this app, so it doubles the `404`. A
    double is a guardrail only while something proves it matches the original: that suite built
    `{message, candidates}` un-nested, passed all 265 lines of itself, and the chooser had never
    once worked against the real API — the client read `candidates` from a root that never
    carried it.

    So the double *is* this response, byte for byte. A change to `_unresolved_detail`, or to
    FastAPI's `detail` nesting, now fails here in Tier 1 rather than silently un-fixing the
    client behind a green web suite.
    """
    divisions = FakeLookup((candidate("Old Town", 0.6), candidate("Old Town", 0.6, at=28.30)))
    app = build_app(divisions_lookup=divisions, geocoder=FakeLookup())
    response = signed_in_client(app).post("/areas", json={"name": "Old Town"})
    assert response.status_code == 404

    body = response.json()
    if os.environ.get("SIYUR_UPDATE_WIRE_CAPTURES"):
        WIRE_CAPTURE.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n")

    assert body == json.loads(WIRE_CAPTURE.read_text()), (
        "web/test/fixtures/area-404-wire.json no longer matches this endpoint. Regenerate it "
        "with SIYUR_UPDATE_WIRE_CAPTURES=1 and fix whatever on the web side read the old shape."
    )
    # The nesting, named — this is the fact the web client was wrong about.
    assert body["detail"]["candidates"], "a capture with no candidates is not worth doubling"
    assert "candidates" not in body, "candidates live under `detail`, never at the root"


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
def test_a_created_area_persists_its_local_frame(app: FastAPI, db_session: Session) -> None:
    """``POST /areas`` must write ``timezone`` and ``country_code``, not just the polygon.

    This regression exists because dropping them **failed nowhere near here and looked
    correct the whole way down**. `resolve_area` computes the frame and `0005_user_plan`
    backfilled every pre-existing row, so only areas created *through the API* carried a
    NULL frame — and the symptom surfaced three modules away:

    ``feasibility._check_hours`` refuses to substitute UTC for an unresolved timezone (by
    design — a confident answer in the wrong frame is worse than a refusal), so every stop
    returned ``hours_unknown``; that made every plan ``feasible=False``; and ``approve_plan``
    has ``feasible IS TRUE`` in its predicate, so **every approve over every API-created
    area answered 409 infeasible**. Every one of those refusals is individually correct,
    which is exactly why nothing failed and no test went red.

    Asserted on the **row**, not on the response: the response body carries no frame, so a
    check on it could pass with the columns still NULL.
    """
    response = signed_in_client(app).post("/areas", json={"bbox": BBOX})
    assert response.status_code == 200

    area = db_session.get(Area, UUID(response.json()["area_id"]))
    assert area is not None
    assert area.timezone is not None, "the area was created without a timezone"
    assert area.country_code is not None, "the area was created without a country code"
    # Shape, not identity — the point is genericity, and pinning a specific zone here would
    # put a place literal in a test whose subject is "any area resolves its own frame".
    assert "/" in area.timezone, f"not an IANA zone id: {area.timezone!r}"
    assert re.fullmatch(r"[A-Z]{2}", area.country_code), area.country_code


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


# ── Tier 2: GET /areas — the app remembers where you asked about ─────────────


def seed_areas(session: Session, sub: str, count: int, *, name: str | None = None) -> list[UUID]:
    """``count`` areas for ``sub`` with **explicitly staggered** ``created_at``, oldest first.

    Stated rather than left to the insert clock: "newest first" is only testable against an
    order the test itself fixed, and two rows written in one transaction can land in the same
    microsecond.
    """
    base = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)
    written: list[UUID] = []
    for index in range(count):
        area = Area(
            polygon=from_shape(box(28.0 + index, 36.0, 28.5 + index, 36.5), srid=SRID),
            name=None if name is None else f"{name} {index}",
            created_by=sub,
            created_at=base + timedelta(minutes=index),
        )
        session.add(area)
        session.flush()
        written.append(area.id)
    session.commit()
    return written


@integration
def test_the_area_list_is_the_callers_own_newest_first(app: FastAPI, db_session: Session) -> None:
    """Phase A: an ``area_id`` that only exists in one response is an area you cannot revisit."""
    oldest, middle, newest = seed_areas(db_session, "google-sub-a", 3, name="Somewhere")

    body = signed_in_client(app).get("/areas").json()

    assert [UUID(entry["area_id"]) for entry in body["areas"]] == [newest, middle, oldest]
    first = body["areas"][0]
    assert first["name"] == "Somewhere 2"
    # lon first, and the bbox is the row's own extent — computed by PostGIS, not by the client.
    assert first["bbox"] == [
        pytest.approx(30.0),
        pytest.approx(36.0),
        pytest.approx(30.5),
        pytest.approx(36.5),
    ]
    assert datetime.fromisoformat(first["created_at"]).tzinfo is not None
    assert first["researched_at"] is None, "delimited, never researched"
    assert set(first) == {"area_id", "name", "bbox", "created_at", "researched_at"}


@integration
def test_an_area_the_list_names_can_be_opened_and_researched(
    app: FastAPI, db_session: Session
) -> None:
    """The journey the list exists for: come back, find the area, use its id — and see it move.

    ``researched_at`` is the field that distinguishes an abandoned delimitation from one with
    a commons behind it, so it is asserted **after** a real pass rather than only as a null.
    """
    client = signed_in_client(app)
    area_id = client.post("/areas", json={"bbox": NOWHERE_BBOX}).json()["area_id"]

    listed = client.get("/areas").json()["areas"]
    assert [entry["area_id"] for entry in listed] == [area_id]
    assert listed[0]["researched_at"] is None

    assert client.post(f"/areas/{area_id}/research", json={}).status_code == 200

    after = client.get("/areas").json()["areas"][0]
    assert after["researched_at"] is not None, "a committed pass shows up in the list"


@integration
def test_a_second_user_never_sees_the_first_users_areas(app: FastAPI, db_session: Session) -> None:
    """The privacy boundary, over the wire — and the list must be *empty*, not merely a 404.

    Paired with ``test_the_area_list_query_is_scoped_to_the_caller_and_bounded``, which
    asserts the filter is in the ``WHERE`` rather than applied after the read.
    """
    seed_areas(db_session, "google-sub-a", 2)
    mine = seed_areas(db_session, "google-sub-intruder", 1)

    intruder = signed_in_client(app, sub="google-sub-intruder")
    body = intruder.get("/areas").json()

    assert [UUID(entry["area_id"]) for entry in body["areas"]] == mine


@integration
def test_a_user_with_no_areas_gets_an_empty_list_and_a_200(app: FastAPI) -> None:
    """Empty is a success. A ``404`` here would make a first run indistinguishable from a fault."""
    response = signed_in_client(app).get("/areas")
    assert response.status_code == 200
    assert response.json() == {"areas": []}


@integration
def test_the_area_list_returns_at_most_the_limit_and_takes_the_newest(
    app: FastAPI, db_session: Session
) -> None:
    ids = seed_areas(db_session, "google-sub-a", 4)
    client = signed_in_client(app)

    listed = client.get("/areas", params={"limit": 2}).json()["areas"]

    assert [UUID(entry["area_id"]) for entry in listed] == list(reversed(ids))[:2]
    assert len(client.get("/areas").json()["areas"]) == 4, "the default is not the cap"


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
