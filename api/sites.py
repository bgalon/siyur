"""T039 — ``GET /sites?bbox=…``: the cited commons for a map viewport.

Contract: `specs/001-research-cited-sites/contracts/sites.md`. The consumer is
`web/src/map/sites.ts`, which was written against that contract with ``fetch`` mocked — so
the body this module emits is the interface, not an implementation detail.

Three properties, in the order they bite:

**Auth, then validation, then storage.** FastAPI solves dependencies in signature order, so
an unauthenticated request is ``401`` before the ``bbox`` is even parsed, and a malformed
``bbox`` is ``422`` before an engine is built. The commons is world-readable **to any
signed-in user** (api/AGENTS.md), so there is deliberately *no* per-user scoping on
``site*`` — that would be a bug here, not a safeguard.

**Nothing unstamped is returned.** :func:`commons.repository.sites_in_bbox` already drops a
stored row that cannot re-validate into a fully stamped
:class:`~commons.models.SiteRecordV1`; this module hands the surviving records straight to
``model_dump(mode="json")`` and re-serialises **nothing** by hand. There is no loose
dict-building path through which an unstamped value could re-enter the response.

**Attribution is derived, never composed.** ``attribution[]`` is
:func:`commons.repository.attribution_for` — the union of what the stamps themselves carry.
The API invents no credit string, exactly as the client renders none of its own.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from api.deps import SessionDep
from api.security import CurrentUser
from commons.geo import GeometryError, validate_lat, validate_lon
from commons.repository import attribution_for, sites_in_bbox

__all__ = ["SitesResponse", "parse_bbox", "router"]

router = APIRouter(tags=["sites"])

_BBOX_FORMAT = "bbox must be `minLon,minLat,maxLon,maxLat` in EPSG:4326 (lon first)"


class SitesResponse(BaseModel):
    """The `contracts/sites.md` ``200`` body.

    ``sites`` carries whole ``SiteRecordV1`` JSON documents rather than a re-declared
    pydantic shape: the schema is owned by :mod:`commons.models`, and restating it here
    would be a second place for it to drift — and a place for a value to lose its stamp.
    """

    sites: list[dict[str, Any]]
    #: Union of required attribution strings across every returned value.
    attribution: list[str]


def parse_bbox(
    bbox: Annotated[
        str,
        Query(
            description="minLon,minLat,maxLon,maxLat in EPSG:4326",
            examples=["28.216,36.440,28.232,36.451"],
        ),
    ],
) -> tuple[float, float, float, float]:
    """``"minLon,minLat,maxLon,maxLat"`` → four floats, each checked against **its own** axis.

    A dependency rather than inline parsing so that a malformed ``bbox`` answers ``422``
    before the session dependency runs — the endpoint's validation contract is then
    testable with no database at all.

    Each ordinate is validated per axis (`commons/geo.py`), so a lat-first bbox with
    ``|lon| > 90`` is refused here instead of quietly querying the wrong hemisphere.
    ``minLon > maxLon`` is *not* refused: a world-wrapped MapLibre viewport is a legitimate
    request, and PostGIS answers it with an empty envelope rather than wrong rows.
    """
    parts = bbox.split(",")
    if len(parts) != 4:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{_BBOX_FORMAT}; got {len(parts)} value(s)",
        )
    try:
        min_lon, min_lat, max_lon, max_lat = (float(part) for part in parts)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{_BBOX_FORMAT}; {error}",
        ) from error
    try:
        return (
            validate_lon(min_lon),
            validate_lat(min_lat),
            validate_lon(max_lon),
            validate_lat(max_lat),
        )
    except GeometryError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from error


BboxDep = Annotated["tuple[float, float, float, float]", Depends(parse_bbox)]


@router.get("/sites", response_model=SitesResponse, summary="Cited commons records in a bbox")
def list_sites(user: CurrentUser, bbox: BboxDep, session: SessionDep) -> SitesResponse:
    """The stamped records whose location falls in ``bbox``, plus the credits they require.

    Read-only: no research is triggered, nothing is written. ``user`` is required and then
    deliberately unused — being signed in is the whole access rule for the commons.
    """
    records = sites_in_bbox(session, bbox)
    return SitesResponse(
        sites=[record.model_dump(mode="json") for record in records],
        attribution=list(attribution_for(records)),
    )
