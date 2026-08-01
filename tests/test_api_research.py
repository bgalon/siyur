"""T041 — the ``POST /areas/{area_id}/research`` SSE stream (`contracts/research.md`).

`test_pipeline.py` already pins the *generator's* frame contract offline. What is pinned
here is everything the transport adds and could get wrong: that the frames survive SSE
framing, that the records genuinely land in PostGIS inside one transaction, that a covered
area is reused rather than re-researched (T050), and that a pass another user ran is
readable from the shared commons (ADR-0008).

Every adapter is injected from the committed fixtures, so no test in this file touches
Overture's cloud storage or Overpass; the model seam stays ``None``, so none touches an API
key either. Rhodes is the fixtures' geography, never a default — each test delimits its own
area (FR-001).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from api.areas import _claim, _release
from commons.db import Site
from commons.models import SiteRecordV1
from planner.pipeline import PHASES
from tests.test_api_areas import (
    BBOX,
    NOWHERE_BBOX,
    build_app,
    parse_sse,
    signed_in_client,
)
from tests.test_planner_research import OSM_NODES, OVERPASS_JSON, OVERTURE_ROWS, osm, overture

pytestmark = pytest.mark.integration

Frames = list[tuple[str, dict[str, Any]]]


@pytest.fixture
def app(db_engine: Engine, db_session: Session) -> FastAPI:
    """The real app over the migrated test database, with the fixture adapters injected."""
    return build_app(engine=db_engine, research_adapters=(overture(), osm(node=OVERPASS_JSON)))


def delimit(client: TestClient, bbox: tuple[float, float, float, float] = BBOX) -> str:
    response = client.post("/areas", json={"bbox": list(bbox)})
    assert response.status_code == 200
    area_id: str = response.json()["area_id"]
    return area_id


def research(client: TestClient, area_id: str, *, force_refresh: bool = False) -> Frames:
    response = client.post(f"/areas/{area_id}/research", json={"force_refresh": force_refresh})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


def named(frames: Frames, event: str) -> list[dict[str, Any]]:
    return [payload for name, payload in frames if name == event]


def sourced_values(payload: Any) -> list[dict[str, Any]]:
    """Every ``SourcedValue``-shaped dict anywhere in a streamed frame."""
    found: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        if "value" in payload and "source" in payload:
            found.append(payload)
        for nested in payload.values():
            found.extend(sourced_values(nested))
    elif isinstance(payload, list):
        for item in payload:
            found.extend(sourced_values(item))
    return found


def site_count(session: Session) -> int:
    total: int = session.execute(select(func.count()).select_from(Site)).scalar_one()
    return total


@pytest.fixture
def researched(app: FastAPI) -> Frames:
    """One full pass over the fixture area, driven end to end through HTTP."""
    client = signed_in_client(app)
    return research(client, delimit(client))


# ── the stream contract, over the wire ───────────────────────────────────────


def test_the_phase_sequence_survives_sse_framing(researched: Frames) -> None:
    phases = [payload["phase"] for name, payload in researched if name == "status"]
    assert set(PHASES) <= set(phases)
    ordered = [phase for phase in phases if phase in PHASES]
    assert ordered[0] == "resolve_area" and ordered[-1] == "curate"
    assert [name for name, _ in researched][-2:] == ["summary", "done"]
    assert "error" not in [name for name, _ in researched]


def test_each_source_reports_what_it_found(researched: Frames) -> None:
    reported = {
        payload["source"]: payload["found"]
        for name, payload in researched
        if name == "status" and payload.get("phase") == "research"
    }
    assert reported == {"overture": OVERTURE_ROWS, "osm": OSM_NODES}


def test_every_streamed_value_carries_a_source_and_the_records_are_schema_valid(
    researched: Frames,
) -> None:
    """FR-003 / SC-002 across the transport, not just inside the generator."""
    sites = named(researched, "site")
    assert sites
    for site in sites:
        assert SiteRecordV1.model_validate(site).schema_ver == "SiteRecordV1"
        for value in sourced_values(site):
            assert value["source"]["kind"] and value["source"]["license"]
            assert value["bundleable"] is not None
        # FR-005: a coordinate is never model-emitted, so it always cites geodata.
        assert site["location"]["source"]["kind"] in {"overture", "osm"}


def test_the_pass_is_committed_and_the_summary_counts_it(
    app: FastAPI, db_session: Session, researched: Frames
) -> None:
    (summary,) = named(researched, "summary")
    assert summary["sites"] == len(named(researched, "site")) > 0
    assert summary["new"] > 0 and summary["reused"] == 0
    assert summary["degraded_sources"] == []
    # The API owns the transaction boundary: the repository only flushed.
    assert site_count(db_session) == summary["new"]


def test_done_names_the_area_it_ran_for(app: FastAPI) -> None:
    client = signed_in_client(app)
    area_id = delimit(client)
    (done,) = named(research(client, area_id), "done")
    assert done["area_id"] == area_id


# ── the honest-failure cases ─────────────────────────────────────────────────


def test_an_empty_area_reports_zero_and_fabricates_nothing(
    app: FastAPI, db_session: Session
) -> None:
    """SC-006 — 'nothing found' is a real answer; an invented place would not be."""
    client = signed_in_client(app)
    frames = research(client, delimit(client, NOWHERE_BBOX))

    (summary,) = named(frames, "summary")
    assert summary["sites"] == 0
    assert named(frames, "site") == []
    assert [name for name, _ in frames][-1] == "done"
    assert site_count(db_session) == 0


def test_a_degraded_source_is_reported_and_the_others_survive(
    db_engine: Engine, db_session: Session
) -> None:
    """FR-012 — a partial answer is a legitimate answer; a silent one is not."""
    app = build_app(
        engine=db_engine,
        research_adapters=(overture(), osm(node=OVERPASS_JSON, way=504, relation=504)),
    )
    client = signed_in_client(app)
    frames = research(client, delimit(client))

    (summary,) = named(frames, "summary")
    assert "osm" in summary["degraded_sources"]
    assert summary["sources"]["overture"] == OVERTURE_ROWS
    (degraded,) = [
        payload for name, payload in frames if name == "status" and payload.get("source") == "osm"
    ]
    assert degraded["degraded"] is True and "504" in degraded["reason"]
    # The pass still committed what it did find.
    assert site_count(db_session) > 0


# ── the error contract ───────────────────────────────────────────────────────


def test_an_unknown_area_id_is_404(app: FastAPI) -> None:
    response = signed_in_client(app).post(f"/areas/{uuid4()}/research", json={})
    assert response.status_code == 404


def test_another_users_area_is_404_not_a_borrowed_polygon(app: FastAPI) -> None:
    """Row-scoping is the privacy boundary: someone else's id is indistinguishable from none."""
    area_id = delimit(signed_in_client(app, sub="google-sub-owner"))
    intruder = signed_in_client(app, sub="google-sub-intruder")
    assert intruder.post(f"/areas/{area_id}/research", json={}).status_code == 404


def test_a_pass_already_running_for_the_area_is_409(app: FastAPI) -> None:
    """The idempotency guard, taken directly — the stream is synchronous, so the claim is not."""
    client = signed_in_client(app)
    area_id = delimit(client)

    assert _claim(UUID(area_id)) is True
    try:
        response = client.post(f"/areas/{area_id}/research", json={"force_refresh": True})
        assert response.status_code == 409
    finally:
        _release(UUID(area_id))

    # …and the claim is released once the pass ends, so the next one runs.
    assert research(client, area_id, force_refresh=True)


def test_the_claim_is_released_after_a_pass_completes(app: FastAPI) -> None:
    client = signed_in_client(app)
    area_id = delimit(client)
    research(client, area_id)
    assert _claim(UUID(area_id)) is True, "the finished pass never released its claim"
    _release(UUID(area_id))


# ── T050: reuse vs refresh (US2) ─────────────────────────────────────────────


def test_force_refresh_false_over_a_covered_area_is_a_no_op_reuse_hint(
    app: FastAPI, db_session: Session
) -> None:
    client = signed_in_client(app)
    area_id = delimit(client)
    first = research(client, area_id)
    written = site_count(db_session)
    assert written == named(first, "summary")[0]["new"]

    frames = research(client, area_id, force_refresh=False)

    (status_frame,) = [p for name, p in frames if name == "status"]
    assert status_frame["phase"] == "reuse"
    assert status_frame["known_site_count"] == written
    assert status_frame["refresh_available"] is True
    (summary,) = named(frames, "summary")
    assert (summary["sites"], summary["new"], summary["reuse"]) == (0, 0, True)
    assert summary["reused"] == written
    # A no-op researched nothing: no source was called and no row moved.
    assert named(frames, "site") == []
    assert site_count(db_session) == written


def test_force_refresh_true_re_runs_and_creates_no_duplicate_rows(
    app: FastAPI, db_session: Session
) -> None:
    """Backs ``test_commons_reuse_dedupe`` (T054): a refresh enriches, it never forks."""
    client = signed_in_client(app)
    area_id = delimit(client)
    research(client, area_id)
    before = site_count(db_session)
    assert before > 0

    frames = research(client, area_id, force_refresh=True)

    (summary,) = named(frames, "summary")
    assert summary["new"] == 0, "every record joined the row it already belonged to"
    assert summary["reused"] > 0
    assert site_count(db_session) == before, "a refresh must enrich, never fork"


# ── ADR-0008: the commons is shared, the area is not ─────────────────────────


def test_a_record_written_by_one_user_is_readable_by_another(app: FastAPI) -> None:
    """``test_commons_write_shared`` — the write is auth-gated, the read is world-readable."""
    author = signed_in_client(app, sub="google-sub-author")
    frames = research(author, delimit(author))
    written = {site["id"] for site in named(frames, "site")}
    assert written

    reader = signed_in_client(app, sub="google-sub-reader")
    response = reader.get("/sites?bbox=28.216,36.440,28.232,36.451")

    assert response.status_code == 200
    body = response.json()
    assert written <= {site["id"] for site in body["sites"]}
    assert "© OpenStreetMap contributors" in body["attribution"]
