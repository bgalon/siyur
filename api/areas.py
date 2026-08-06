"""T037/T038/T050 — ``POST /areas`` and the ``POST /areas/{area_id}/research`` SSE stream.

Contracts: `specs/001-research-cited-sites/contracts/areas.md` and `contracts/research.md`.
This module is deliberately thin: `planner/pipeline.py` already yields exactly the frames
the research contract specifies, so what lives here is transport, storage and the three
decisions a transport shim has to make honestly.

**1. Areas are durable rows, not process memory.** ``POST /areas`` writes a
:class:`~commons.db.Area`; the research endpoint reads it back. That is what lets ``404 on
an unknown area_id`` be true across restarts and across processes instead of only until the
next deploy. Areas are **per-user**: an area is a personal delimitation (the *sites* a pass
finds are shared commons, "where I asked about" is not), so every read filters on
``created_by = user.sub`` and someone else's id is a ``404``, never someone else's polygon.
The row also records ``researched_at`` once a pass **commits**, which is what turns
``coverage.covered`` from "we hold a site in here somewhere" into "this was researched"
(ADR-0018). That read stays per-user, so a second user re-researches ground the first already
covered — see :func:`~commons.repository.coverage` for why that scope was not widened here.

**2. The API owns the transaction boundary.** :func:`commons.repository.upsert_sites`
flushes and does not commit, so this module commits **after the stream completes** and rolls
back otherwise. A client that disconnects mid-stream, an adapter that explodes after the
write, a refused user-sourced value — each leaves the commons exactly as it was, never a
half-written area.

**3. The ``409`` guard is process-local.** ``_RUNNING`` is a set in this process's memory,
guarded by a lock. Two concurrent passes over one area *in the same process* collide and
the second gets ``409``; two passes in two processes (a scaled-out deployment) do **not**
see each other. That is stated rather than dressed up: making it true across processes needs
a database-backed claim (a lock row, or a ``research_started_at`` column with a staleness
timeout), which is a schema decision worth an ADR rather than a silent addition here. The
underlying write stays safe either way — :func:`~commons.repository.upsert_sites` upserts,
so a doubled pass enriches the same rows rather than forking them (T054).

The polygon fed to the pipeline comes from the **stored** row, so ``resolve_area`` genuinely
re-runs and re-validates the geometry rather than being faked past.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from geoalchemy2.shape import from_shape, to_shape
from pydantic import BaseModel, ConfigDict
from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from starlette.responses import Response

from api.deps import (
    AdaptersDep,
    DivisionsDep,
    GeocoderDep,
    RouterDep,
    SessionDep,
    SessionFactoryDep,
)
from api.security import CurrentUser
from commons.db import Area
from commons.merge import MergeResult
from commons.repository import Coverage, UpsertReport, coverage, upsert_sites
from planner.nodes.resolve_area import (
    AreaCandidate,
    AreaInvalid,
    AreaNotResolved,
    AreaRequest,
    resolve_area,
)
from planner.pipeline import Event, run_research

__all__ = ["AreaRequestBody", "AreaResponse", "CoverageResponse", "ResearchRequestBody", "router"]

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/areas", tags=["areas"])

#: SSE media type, plus the headers that stop a proxy from buffering the stream away.
SSE_MEDIA_TYPE = "text/event-stream"
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

#: Areas with a research pass in flight **in this process** — see the module docstring.
_RUNNING: set[UUID] = set()
_RUNNING_LOCK = threading.Lock()


# ── request/response bodies ────────────────────────────────────────────────────────


class AreaRequestBody(BaseModel):
    """``POST /areas`` — one of ``name`` / ``bbox`` / ``polygon`` (`contracts/areas.md`)."""

    model_config = ConfigDict(extra="forbid")

    #: Free text, resolved via Overture divisions (+ Nominatim disambiguation).
    name: str | None = None
    #: ``[minLon, minLat, maxLon, maxLat]``, EPSG:4326 — **lon first**.
    bbox: tuple[float, float, float, float] | None = None
    #: GeoJSON ``Polygon``/``MultiPolygon``, EPSG:4326.
    polygon: dict[str, Any] | None = None


class CoverageResponse(BaseModel):
    """What is already known about the area — drives reuse/refresh (US2/FR-006).

    ``covered`` means *"this area has been researched"* (see
    :func:`~commons.repository.coverage`), and ``researched_fraction`` is the evidence for it,
    so a client can tell a genuinely-covered area from one that merely contains some sites.
    """

    known_site_count: int
    #: ``0.0``–``1.0``: how much of the requested polygon has actually been researched.
    researched_fraction: float
    covered: bool
    stalest_observed_at: date | None
    refresh_available: bool


class AreaResponse(BaseModel):
    """The `contracts/areas.md` ``200`` body."""

    area_id: UUID
    #: The **resolved** geometry as GeoJSON, EPSG:4326.
    polygon: dict[str, Any]
    coverage: CoverageResponse


class ResearchRequestBody(BaseModel):
    """``POST /areas/{area_id}/research`` — ``force_refresh`` is the whole request (T050)."""

    model_config = ConfigDict(extra="forbid")

    force_refresh: bool = False


# ── helpers ───────────────────────────────────────────────────────────────────────


def _sse(event: str, data: Mapping[str, Any]) -> str:
    """One SSE frame. ``json.dumps`` escapes newlines, so ``data:`` can never be split."""
    payload = json.dumps(dict(data), ensure_ascii=False, separators=(",", ":"), default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _candidate(candidate: AreaCandidate) -> dict[str, Any]:
    """One disambiguation option: enough to choose, without shipping a whole ring.

    The candidate's **bounds** rather than its geometry — a division polygon can be tens of
    thousands of vertices, and the client re-delimits by name or bbox anyway.
    """
    min_lon, min_lat, max_lon, max_lat = candidate.polygon.bounds
    return {
        "name": candidate.name,
        "confidence": candidate.confidence,
        "bbox": [min_lon, min_lat, max_lon, max_lat],
        "source": candidate.source.model_dump(mode="json"),
    }


def _unresolved_detail(error: AreaNotResolved) -> dict[str, Any]:
    """``404`` body: why, plus the candidates the user should pick between."""
    return {"message": str(error), "candidates": [_candidate(c) for c in error.candidates]}


def _coverage_body(found: Coverage) -> CoverageResponse:
    return CoverageResponse(
        known_site_count=found.known_site_count,
        researched_fraction=found.researched_fraction,
        covered=found.covered,
        stalest_observed_at=found.stalest_observed_at,
        refresh_available=found.refresh_available,
    )


def _mark_researched(session: Session, area_id: UUID, subject: str) -> None:
    """Record that a pass over this area **completed** — the state ``covered`` reads.

    Scoped to ``created_by`` like every other read/write of this table, even though
    :func:`_load_area` already proved ownership: the scope filter is the invariant, not a
    check someone remembered to do once upstream.

    A pass that finished with degraded sources still counts. The area *was* looked at, and
    what each source did or did not answer is reported in-band by the ``status``/``summary``
    frames (FR-012); folding that into a boolean here would silently re-research a whole area
    because one adapter was slow.
    """
    session.execute(
        update(Area)
        .where(Area.id == area_id, Area.created_by == subject)
        .values(researched_at=datetime.now(UTC))
    )


def _load_area(session: Session, area_id: UUID, subject: str) -> Area:
    """The caller's area, or ``404``.

    Scoped to ``created_by``: another user's area id is indistinguishable from a
    non-existent one, which is the point (api/AGENTS.md — a missing scope is a data leak).
    """
    area = session.execute(
        select(Area).where(Area.id == area_id, Area.created_by == subject)
    ).scalar_one_or_none()
    if area is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"no area {area_id} for this user"
        )
    return area


def _claim(area_id: UUID) -> bool:
    """Take the in-process research claim for ``area_id``; ``False`` if one is held."""
    with _RUNNING_LOCK:
        if area_id in _RUNNING:
            return False
        _RUNNING.add(area_id)
        return True


def _release(area_id: UUID) -> None:
    with _RUNNING_LOCK:
        _RUNNING.discard(area_id)


def _persist_into(session: Session) -> Any:
    """A :class:`~planner.pipeline.Persist` bound to ``session`` — flush only, no commit."""

    def persist(results: Sequence[MergeResult]) -> UpsertReport:
        return upsert_sites(session, results)

    return persist


def _reuse_frames(area_id: UUID, found: Coverage) -> Iterator[str]:
    """T050 — ``force_refresh=false`` over a covered area: a reuse hint, not a pass.

    Still SSE, because the client opened an event stream and a JSON body would break it.
    No adapter is called, nothing is written, and ``summary.sites`` is ``0`` because this
    pass researched nothing — what the area already holds is reported as ``reused``.
    """
    yield _sse(
        "status",
        {
            "phase": "reuse",
            "covered": True,
            "known_site_count": found.known_site_count,
            "stalest_observed_at": found.stalest_observed_at,
            "refresh_available": found.refresh_available,
            "msg": (
                "area already covered — read it with GET /sites?bbox=…, or re-POST with "
                "force_refresh=true to research it again"
            ),
        },
    )
    yield _sse(
        "summary",
        {
            "sites": 0,
            "new": 0,
            "reused": found.known_site_count,
            "conflicts": 0,
            "sources": {},
            "degraded_sources": [],
            "reuse": True,
        },
    )
    yield _sse("done", {"area_id": str(area_id)})


def _research_frames(
    session: Session, area_id: UUID, subject: str, first: Event, rest: Iterator[Event]
) -> Iterator[str]:
    """Stream the pipeline, then commit — the transaction boundary the repository leaves open.

    The commit happens only after the last frame is produced. Any failure (including a
    client disconnect, which arrives as ``GeneratorExit``) falls through to ``close()``,
    which rolls the whole pass back: never a half-written area.

    ``researched_at`` is stamped **inside that same transaction**, so "this area has been
    researched" and "the records it found" become true together or not at all. A pass that
    dies mid-stream leaves the area unresearched and the next ask runs it again.
    """
    try:
        yield _sse(first.event, first.data)
        for event in rest:
            yield _sse(event.event, event.data)
        _mark_researched(session, area_id, subject)
        session.commit()
    except Exception as error:  # noqa: BLE001 — the status code is long gone; say so in-band
        session.rollback()
        _log.exception("research pass failed mid-stream for area %s", area_id)
        # Not in the contract's frame list, because the contract lists the *success*
        # sequence. A stream that dies silently would be worse than an honest error frame.
        yield _sse(
            "error",
            {"area_id": str(area_id), "error": type(error).__name__, "detail": str(error)},
        )
    finally:
        session.close()
        _release(area_id)


# ── endpoints ─────────────────────────────────────────────────────────────────────


@router.post("", response_model=AreaResponse, summary="Delimit, resolve and check coverage")
def create_area(
    user: CurrentUser,
    session: SessionDep,
    divisions: DivisionsDep,
    geocoder: GeocoderDep,
    body: AreaRequestBody,
) -> AreaResponse:
    """T037 — resolve the user's delimitation to one polygon and report commons coverage.

    Genericity (FR-001): ``name``/``bbox``/``polygon`` are pure user input and nothing in
    this path branches on a place. The resolved polygon is persisted so the research
    endpoint has something to be a ``404`` about.
    """
    try:
        resolved = resolve_area(
            AreaRequest(name=body.name, bbox=body.bbox, polygon=body.polygon),
            divisions=divisions,
            geocoder=geocoder,
        )
    except AreaInvalid as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except AreaNotResolved as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_unresolved_detail(error)
        ) from error

    area = Area(
        polygon=from_shape(resolved.polygon, srid=4326),
        name=body.name,
        created_by=user.sub,
    )
    session.add(area)
    session.flush()
    area_id = area.id
    # The row just flushed has no `researched_at`, so it cannot report itself as covered.
    found = coverage(session, resolved.polygon, researched_by=user.sub)
    session.commit()

    return AreaResponse(
        area_id=area_id, polygon=dict(mapping(resolved.polygon)), coverage=_coverage_body(found)
    )


@router.post("/{area_id}/research", summary="Run a research pass over the area (SSE)")
def research_area(
    user: CurrentUser,
    factory: SessionFactoryDep,
    adapters: AdaptersDep,
    model_router: RouterDep,
    area_id: UUID,
    body: ResearchRequestBody | None = None,
) -> Response:
    """T038/T050 — drive the pipeline and stream its frames as ``text/event-stream``.

    Resolution failures raise from the **first** ``next()``, before a byte is sent, so they
    become a real ``422``/``404`` rather than a ``200`` stream whose first frame is an
    apology. Everything after that point streams, and the pass commits or rolls back whole.
    """
    force_refresh = body is not None and body.force_refresh

    with factory() as session:
        area = _load_area(session, area_id, user.sub)
        polygon: BaseGeometry = to_shape(area.polygon)
        known = coverage(session, polygon, researched_by=user.sub)

    # T050: a covered area is reused by default; a refresh is opt-in (FR-006 / US2).
    if known.covered and not force_refresh:
        return StreamingResponse(
            _reuse_frames(area_id, known), media_type=SSE_MEDIA_TYPE, headers=SSE_HEADERS
        )

    if not _claim(area_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"a research pass is already running for area {area_id}",
        )

    session = factory()
    try:
        stream = run_research(
            # From the stored geometry, so `resolve_area` really runs and really validates.
            AreaRequest(polygon=dict(mapping(polygon))),
            adapters=adapters,
            persist=_persist_into(session),
            router=model_router,
            area_id=area_id,
        )
        first = next(stream)
    except AreaInvalid as error:
        session.close()
        _release(area_id)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error
    except AreaNotResolved as error:
        session.close()
        _release(area_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_unresolved_detail(error)
        ) from error
    except BaseException:
        session.close()
        _release(area_id)
        raise

    return StreamingResponse(
        _research_frames(session, area_id, user.sub, first, stream),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )
