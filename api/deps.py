"""Shared FastAPI dependencies — the database session and the injectable seams.

Three things every endpoint below `api/areas.py` and `api/sites.py` needs, in one place:

**1. A session, created lazily.** The app MUST boot and serve ``/healthz`` with no
credentials and no database (`api/config.py`), so the engine is built on first *use*, not at
import or startup. A missing ``SIYUR_DATABASE_URL`` therefore surfaces as a ``503`` on the
endpoints that need storage rather than a crash at boot. The URL is read from the process
environment only — never a file, never ``.env*`` (AGENTS.md).

**2. Injectable research seams.** The real :class:`~commons.sources.overture.OvertureAdapter`
and :class:`~commons.sources.osm.OsmAdapter` are the defaults, but every one of them can be
replaced through ``app.state`` so component tests drive the committed fixtures with **no
network** — no Overture cloud storage, no Overpass, no Nominatim, no model. Injection is by
app state rather than by FastAPI ``dependency_overrides`` because the same seams are needed
inside a streaming generator, after the dependency scope has already closed.

**3. Ordering that lets validation answer before storage is touched.** FastAPI solves
dependencies in signature order, so a route that declares ``user`` → ``bbox`` → ``session``
answers ``401`` before ``422`` and ``422`` before it ever builds an engine. That is what
makes the auth/validation contract testable at Tier 1, with no database in sight.

``sessionmaker`` and ``create_engine`` are both lazy — neither opens a connection until a
statement runs — so a request that fails validation never reaches PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session, sessionmaker

from commons.db import create_session_factory
from commons.llm import ModelRouter
from commons.sources.base import SourceAdapter
from commons.sources.osm import OsmAdapter
from commons.sources.overture import OvertureAdapter
from planner.nodes.resolve_area import DivisionsLookup, Geocoder

__all__ = [
    "ADAPTERS_STATE",
    "DIVISIONS_STATE",
    "GEOCODER_STATE",
    "ROUTER_STATE",
    "SESSION_FACTORY_STATE",
    "AdaptersDep",
    "DivisionsDep",
    "GeocoderDep",
    "RouterDep",
    "SessionDep",
    "SessionFactoryDep",
    "get_adapters",
    "get_divisions",
    "get_geocoder",
    "get_router",
    "get_session",
    "get_session_factory",
]

#: ``app.state`` keys. Setting any of them replaces the corresponding default.
SESSION_FACTORY_STATE = "session_factory"
ADAPTERS_STATE = "research_adapters"
DIVISIONS_STATE = "divisions_lookup"
GEOCODER_STATE = "geocoder"
ROUTER_STATE = "model_router"


def get_session_factory(request: Request) -> sessionmaker[Session]:
    """The app's session factory, built on first use and cached on ``app.state``.

    ``503`` — not a crash — when ``SIYUR_DATABASE_URL`` is unset: the API is expected to
    boot without a database and say so per-endpoint.
    """
    factory: sessionmaker[Session] | None = getattr(request.app.state, SESSION_FACTORY_STATE, None)
    if factory is not None:
        return factory
    try:
        factory = create_session_factory()
    except RuntimeError as error:  # SIYUR_DATABASE_URL is not set
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(error)
        ) from error
    setattr(request.app.state, SESSION_FACTORY_STATE, factory)
    return factory


SessionFactoryDep = Annotated["sessionmaker[Session]", Depends(get_session_factory)]


def get_session(factory: SessionFactoryDep) -> Iterator[Session]:
    """A request-scoped session. **Not** usable from a streaming body.

    FastAPI closes a ``yield`` dependency once the route function returns, which for a
    :class:`~fastapi.responses.StreamingResponse` is *before* the body is produced. The SSE
    endpoint therefore takes :data:`SessionFactoryDep` and owns its session explicitly.
    """
    with factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]


def get_adapters(request: Request) -> Sequence[SourceAdapter]:
    """The research sources: the real adapters, unless ``app.state`` names others.

    The defaults are constructed per request — :class:`~commons.sources.osm.OsmAdapter`
    opens (and closes) its own HTTP client per fetch, so sharing one across requests would
    outlive its bounded timeout config.
    """
    adapters: Sequence[SourceAdapter] | None = getattr(request.app.state, ADAPTERS_STATE, None)
    if adapters is not None:
        return adapters
    return (OvertureAdapter(), OsmAdapter())


AdaptersDep = Annotated[Sequence[SourceAdapter], Depends(get_adapters)]


def get_divisions(request: Request) -> DivisionsLookup | None:
    """Name → area lookup. ``None`` lets ``resolve_area`` build the real Overture reader."""
    lookup: DivisionsLookup | None = getattr(request.app.state, DIVISIONS_STATE, None)
    return lookup


DivisionsDep = Annotated[DivisionsLookup | None, Depends(get_divisions)]


def get_geocoder(request: Request) -> Geocoder | None:
    """Disambiguation fallback. ``None`` lets ``resolve_area`` build the real Nominatim one."""
    geocoder: Geocoder | None = getattr(request.app.state, GEOCODER_STATE, None)
    return geocoder


GeocoderDep = Annotated[Geocoder | None, Depends(get_geocoder)]


def get_router(request: Request) -> ModelRouter | None:
    """The model seam for curation ranking. ``None`` ⇒ deterministic, offline, no API key.

    Ranking is the *only* thing the model may influence (`planner/nodes/curate.py`), so an
    absent router costs ordering and nothing else — never a record, never a coordinate.
    """
    router: ModelRouter | None = getattr(request.app.state, ROUTER_STATE, None)
    return router


RouterDep = Annotated[ModelRouter | None, Depends(get_router)]
