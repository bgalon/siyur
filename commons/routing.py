"""The routing seam — the one module in Siyur that speaks to a routing engine (ADR-0020).

Same discipline as :mod:`commons.llm`'s ``ModelRouter``, applied to a different vendor:
callers pass neutral DTOs (:class:`Waypoint`, :class:`PedestrianCosting`) and receive
stamped :class:`RouteLegV1`\\ s and a :class:`TravelMatrix`. **Nothing above this module
knows which engine is live**, and no HTTP call to a routing host may appear outside this
file — swapping engines, or replaying a recording, is a construction-time choice.

Three implementations, selected by ``SIYUR_ROUTING_PROVIDER``:

* :class:`ValhallaProvider` — the engine of record. Exactly the two endpoints ADR-0020
  allows: ``POST /route`` (leg geometry + duration) and ``POST /sources_to_targets`` (the
  N×N feasibility matrix of FR-004). Valhalla's *optimized ordering* is deliberately not
  used: the model proposes an order and deterministic machinery validates it.
* :class:`OpenRouteServiceProvider` — the zero-infra dev fallback, so an early demo needs
  no docker. **Written to the documented API shape and exercised only against a
  hand-written mock**; unlike the Valhalla path there is no captured live response behind
  it, so treat its response parsing as unproven until someone runs it with a real key.
* :class:`FixtureProvider` — replays the committed captures in ``tests/fixtures/`` through
  *the same decoders the live providers use*, so Tier 1 never builds a graph and never
  opens a socket.

**The pace is never defaulted.** :class:`PedestrianCosting` has no default
``walking_speed_kmh``; the caller's pace preference (3.5–5.0 km/h) is required. Valhalla's
own default is 5.1 km/h — brisk for a sightseeing day, deliberately *outside* the band we
accept, and it moves feasibility verdicts more than the choice of engine does. A silently
defaulted pace is a wrong answer, not a cosmetic one, which is why the band is enforced
here rather than trusted at the call site.

**Every leg is a Produced Work of OSM.** Valhalla is MIT, but its graph is built from OSM,
so leg geometry, distance and duration carry **ODbL** and render "© OpenStreetMap
contributors" (`docs/data/route-leg.md`, DATA-LICENSES.md). There is no ``SourceKind`` for
a routing engine and none is being added: the engine is named inside ``SourceRef.id`` as
``<engine>:<costing>``. ``RouteLegV1`` has **no ``bundleable`` field** — quarantine is
*derived* via :func:`commons.licenses.bundleable`, and :func:`routing_source` refuses to
mint a stamp that would not derive to ``True``.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

import httpx
from shapely import LineString

from commons import licenses
from commons.geo import validate_lat, validate_lon
from commons.models import RouteLegV1, SourceRef

__all__ = [
    "ATTRIBUTION",
    "DEFAULT_FIXTURE_DIR",
    "DEFAULT_ORS_ENDPOINT",
    "DEFAULT_VALHALLA_ENDPOINT",
    "FIXTURE_DIR_ENV",
    "LICENSE",
    "MAX_WALKING_SPEED_KMH",
    "MIN_WALKING_SPEED_KMH",
    "ORS_API_KEY_ENV",
    "PROVIDER_ENV",
    "VALHALLA_DEFAULT_WALKING_SPEED_KMH",
    "VALHALLA_URL_ENV",
    "FixtureProvider",
    "NoRouteFound",
    "OpenRouteServiceProvider",
    "PedestrianCosting",
    "RoutingError",
    "RoutingProvider",
    "RoutingUnavailable",
    "TravelMatrix",
    "ValhallaProvider",
    "Waypoint",
    "decode_polyline",
    "routing_source",
    "select_provider",
]

_log = logging.getLogger(__name__)

LICENSE: Final = "ODbL-1.0"
ATTRIBUTION: Final = "© OpenStreetMap contributors"
USER_AGENT: Final = "siyur/0.0 (https://github.com/bgalon/siyur)"

#: The compose service (`docker-compose.yml`, profile ``compile``), overridable per env.
DEFAULT_VALHALLA_ENDPOINT: Final = "http://localhost:8002"
DEFAULT_ORS_ENDPOINT: Final = "https://api.openrouteservice.org"

PROVIDER_ENV: Final = "SIYUR_ROUTING_PROVIDER"
VALHALLA_URL_ENV: Final = "SIYUR_VALHALLA_URL"
ORS_API_KEY_ENV: Final = "SIYUR_ORS_API_KEY"
FIXTURE_DIR_ENV: Final = "SIYUR_ROUTING_FIXTURE_DIR"
#: Unset ⇒ Valhalla, **never the fixture**. A fixture default would hand every area on
#: earth the recorded Rhodes geometry and fail no check at all; a missing container fails
#: loudly on the first request. Loud beats plausible.
DEFAULT_PROVIDER: Final = "valhalla"

#: The product's pace band (PRD pace preference; ADR-0020). Valhalla itself accepts
#: 0.5–25 km/h — this is narrower on purpose, because a pace outside the band is far more
#: likely a units mistake or an un-tracked default than a real walking speed.
MIN_WALKING_SPEED_KMH: Final = 3.5
MAX_WALKING_SPEED_KMH: Final = 5.0
#: Recorded so it can be *excluded*, never applied: Valhalla's implicit pedestrian speed.
VALHALLA_DEFAULT_WALKING_SPEED_KMH: Final = 5.1

_KMH_TO_MPS: Final = 1 / 3.6
#: Valhalla echoes the `units` it routed in; we send kilometres but read the echo, so a
#: server default or a capture recorded in miles converts correctly instead of silently
#: shrinking every distance by 1.6.
_METRES_PER_UNIT: Final[Mapping[str, float]] = {"kilometers": 1000.0, "miles": 1609.344}
#: Valhalla error codes that mean "no walking path for this request", as opposed to "the
#: engine is broken". 171 is the Andorra-trap signature: a graph that does not cover the
#: area answers `/status` happily and then reports no suitable edges (fixtures/README.md).
_NO_ROUTE_CODES: Final[frozenset[int]] = frozenset({170, 171, 442})

#: The committed captures :class:`FixtureProvider` replays. Product code pointing at
#: ``tests/`` is deliberate and confined to this one default: a fixture provider is a test
#: double, and putting the path anywhere else would make it look production-shaped.
DEFAULT_FIXTURE_DIR: Final = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
FIXTURE_ROUTE_FILE: Final = "valhalla_rhodes_route.json"
FIXTURE_MATRIX_FILE: Final = "valhalla_rhodes_matrix.json"
#: The pace the committed capture was recorded at (fixtures/README.md: 0.328 km / 262.437 s
#: = 4.499 km/h, i.e. the requested 4.5 and not the 5.1 default).
FIXTURE_COSTING_KMH: Final = 4.5


class RoutingError(RuntimeError):
    """The engine answered, but not with a usable route."""


class RoutingUnavailable(RoutingError):
    """The engine could not be reached at all (transport error, timeout, 5xx)."""


class NoRouteFound(RoutingError):
    """The engine reports no walking path — a plan-level fact, named, never straight-lined.

    FR-003 forbids presenting a straight line as a route, so this is raised rather than
    filled in: the caller drops the unroutable stop (T021) or surfaces the failure.
    """


@dataclass(frozen=True, slots=True)
class Waypoint:
    """One EPSG:4326 point to route through. **Longitude first**, as everywhere else."""

    lon: float
    lat: float

    def __post_init__(self) -> None:
        # Range-checked at the seam: a lat/lon swap that survives to the engine comes back
        # as "no suitable edges" and reads as a routing bug rather than a caller bug.
        validate_lon(self.lon)
        validate_lat(self.lat)


@dataclass(frozen=True, slots=True)
class PedestrianCosting:
    """Explicit pedestrian costing. ``walking_speed_kmh`` has **no default, by design**.

    ADR-0020 fixes the costing block as
    ``{"pedestrian": {"walking_speed": <pace>, "walkway_factor": 0.9}}``. Adding a default
    pace here would restore exactly the failure the ADR names — every feasibility verdict
    computed at somebody else's idea of a walking speed — so the omission is load-bearing
    and ``tests/test_routing.py`` asserts it mechanically.
    """

    walking_speed_kmh: float
    walkway_factor: float = 0.9

    def __post_init__(self) -> None:
        if not MIN_WALKING_SPEED_KMH <= self.walking_speed_kmh <= MAX_WALKING_SPEED_KMH:
            raise ValueError(
                f"walking_speed_kmh={self.walking_speed_kmh} is outside the pace band "
                f"[{MIN_WALKING_SPEED_KMH}, {MAX_WALKING_SPEED_KMH}] km/h (ADR-0020). "
                f"Valhalla's implicit default is {VALHALLA_DEFAULT_WALKING_SPEED_KMH} and "
                "is refused here rather than accepted silently."
            )

    @property
    def metres_per_second(self) -> float:
        return self.walking_speed_kmh * _KMH_TO_MPS

    def as_valhalla(self) -> dict[str, dict[str, float]]:
        return {
            "pedestrian": {
                "walking_speed": self.walking_speed_kmh,
                "walkway_factor": self.walkway_factor,
            }
        }


@dataclass(frozen=True, slots=True)
class TravelMatrix:
    """The N×N walking matrix FR-004's feasibility check runs on. Metres and seconds.

    Cells are total functions: an unreachable pair raises :class:`NoRouteFound` at parse
    time naming the two indices, rather than arriving here as ``None`` for someone to write
    ``or 0`` over — a zero-second hop between two stops is a feasible-looking lie.
    """

    durations_s: tuple[tuple[int, ...], ...]
    distances_m: tuple[tuple[float, ...], ...]

    @property
    def size(self) -> int:
        return len(self.durations_s)

    def duration_s(self, source: int, target: int) -> int:
        return self.durations_s[source][target]

    def distance_m(self, source: int, target: int) -> float:
        return self.distances_m[source][target]


@runtime_checkable
class RoutingProvider(Protocol):
    """The seam. Implementations pick the engine; callers never name one.

    ``route`` returns **one leg per consecutive pair** of waypoints: leg *i* is
    ``id="leg-<i>"``, ``from_stop=i``, ``to_stop=i+1``. That mapping is exact rather than
    presumptuous because ``ItineraryV1`` validates ``stops[i].order == i`` — waypoint
    position *is* stop order. A caller wanting other ids re-labels with
    ``leg.model_copy(update={"id": ...})``, which re-validates.
    """

    def route(
        self, waypoints: Sequence[Waypoint], *, costing: PedestrianCosting
    ) -> tuple[RouteLegV1, ...]: ...

    def matrix(
        self, waypoints: Sequence[Waypoint], *, costing: PedestrianCosting
    ) -> TravelMatrix: ...


def routing_source(engine: str = "valhalla", profile: str = "pedestrian") -> SourceRef:
    """The produced-work stamp every leg carries — and a check that it still derives.

    ``kind="osm"`` because the *data* the leg derives from is OSM; the engine lives in
    ``id`` as ``<engine>:<costing>``. The guard is not decoration: this stamp is the only
    thing that makes a leg bundleable, and it is derived (`commons.licenses.bundleable`),
    never author-set, so a future edit to :data:`LICENSE` must fail here rather than
    quietly drop every walking leg from every bundle.
    """
    if not licenses.bundleable("osm", LICENSE):
        raise RoutingError(
            f"the routing stamp would not derive to bundleable=True: "
            f"{licenses.quarantine_reason('osm', LICENSE)}"
        )
    return SourceRef(
        kind="osm", id=f"{engine}:{profile}", url=None, license=LICENSE, attribution=ATTRIBUTION
    )


# --- decoding (shared by the live providers and the recorded replay) --------


def decode_polyline(encoded: str, *, precision: float = 1e6) -> list[tuple[float, float]]:
    """Google-polyline → ``[(lon, lat), …]``. Valhalla's default ``shape`` is polyline6.

    **The encoding is (lat, lon) and the output is (lon, lat)** — the axis flip is here,
    once, rather than at three call sites. ``precision`` is 1e6 for Valhalla (polyline6);
    the classic Google/OSRM encoding is 1e5.
    """
    index = lat = lon = 0
    out: list[tuple[float, float]] = []
    while index < len(encoded):
        deltas: list[int] = []
        for _ in range(2):
            shift = result = 0
            while True:
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            deltas.append(~(result >> 1) if result & 1 else result >> 1)
        lat += deltas[0]
        lon += deltas[1]
        out.append((lon / precision, lat / precision))
    return out


def _linestring(vertices: Sequence[tuple[float, float]]) -> LineString:
    """Vertices → a validated EPSG:4326 ``LineString``, coordinates untouched.

    A repeated final vertex (Valhalla emits a zero-delta pair at the end of some legs) is
    kept verbatim: the geometry is what the engine returned, and "tidying" a recorded shape
    is how a fixture stops being evidence.
    """
    for lon, lat in vertices:
        validate_lon(lon)
        validate_lat(lat)
    if len(vertices) < 2:
        raise RoutingError(f"a leg geometry needs at least 2 vertices, got {len(vertices)}")
    return LineString(vertices)


def _metres(length: float, units: str) -> float:
    factor = _METRES_PER_UNIT.get(units)
    if factor is None:
        raise RoutingError(f"unknown distance units {units!r} from the routing engine")
    return length * factor


def _leg(
    index: int, geometry: LineString, distance_m: float, duration_s: int, source: SourceRef
) -> RouteLegV1:
    return RouteLegV1(
        id=f"leg-{index}",
        from_stop=index,
        to_stop=index + 1,
        geometry=geometry,
        distance_m=distance_m,
        duration_s=duration_s,
        source=source,
    )


def _legs_from_valhalla_trip(
    payload: Mapping[str, Any], *, engine: str = "valhalla"
) -> tuple[RouteLegV1, ...]:
    """A ``/route`` response body → stamped legs. Shared by the live client and the replay."""
    trip = payload.get("trip")
    if not isinstance(trip, Mapping):
        raise RoutingError("valhalla /route response carries no trip")
    units = str(trip.get("units", "kilometers"))
    source = routing_source(engine)
    legs: list[RouteLegV1] = []
    for index, leg in enumerate(trip.get("legs") or ()):
        summary = leg.get("summary") or {}
        legs.append(
            _leg(
                index,
                _linestring(decode_polyline(str(leg["shape"]))),
                _metres(float(summary["length"]), units),
                # Valhalla reports fractional seconds; the schema card says integer.
                round(float(summary["time"])),
                source,
            )
        )
    if not legs:
        raise NoRouteFound("valhalla /route returned a trip with no legs")
    return tuple(legs)


def _matrix_from_valhalla(payload: Mapping[str, Any]) -> TravelMatrix:
    """A ``/sources_to_targets`` response body → a :class:`TravelMatrix` in m and s."""
    rows = payload.get("sources_to_targets")
    if not isinstance(rows, Sequence) or not rows:
        raise RoutingError("valhalla /sources_to_targets response carries no matrix")
    units = str(payload.get("units", "kilometers"))
    durations: list[tuple[int, ...]] = []
    distances: list[tuple[float, ...]] = []
    for i, row in enumerate(rows):
        for j, cell in enumerate(row):
            if cell.get("time") is None or cell.get("distance") is None:
                raise NoRouteFound(f"no pedestrian route from waypoint {i} to waypoint {j}")
        durations.append(tuple(round(float(cell["time"])) for cell in row))
        distances.append(tuple(_metres(float(cell["distance"]), units) for cell in row))
    return TravelMatrix(durations_s=tuple(durations), distances_m=tuple(distances))


def _require_pair(waypoints: Sequence[Waypoint]) -> None:
    if len(waypoints) < 2:
        raise ValueError(f"routing needs at least 2 waypoints, got {len(waypoints)}")


@contextmanager
def _borrowed(client: httpx.Client | None, timeout: float) -> Iterator[httpx.Client]:
    """Yield an injected ``client`` untouched, or an owned one that is closed on the way out."""
    if client is not None:
        yield client
        return
    owned = httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT})
    try:
        yield owned
    finally:
        owned.close()


def _post_json(
    client: httpx.Client,
    url: str,
    payload: Mapping[str, Any],
    *,
    engine: str,
    timeout: float,
    headers: Mapping[str, str] | None = None,
) -> tuple[int, Any]:
    """One bounded POST. Transport failures become :class:`RoutingUnavailable`, narrowly.

    ``headers`` go on the **request**, not on the client, which is not a style preference:
    a caller-injected ``httpx.Client`` (Tier 2, or a mock transport) is yielded untouched by
    :func:`_borrowed`, so credentials attached at client construction silently vanish for
    exactly the callers most likely to need them. The ORS key test caught this.

    No retry loop, unlike ``commons/sources/osm.py``: Overpass is a contended public
    instance where a 504 is routine, whereas Valhalla is our own container inside compile —
    a failure there is a real failure and retrying it just delays the report.
    """
    try:
        response = client.post(url, json=dict(payload), timeout=timeout, headers=headers)
    except httpx.TimeoutException as error:
        raise RoutingUnavailable(f"{engine} timed out after {timeout}s") from error
    except httpx.HTTPError as error:
        raise RoutingUnavailable(
            f"{engine} unreachable: {type(error).__name__}: {error}"
        ) from error
    if response.status_code >= 500:
        raise RoutingUnavailable(f"{engine} returned HTTP {response.status_code}")
    try:
        return response.status_code, response.json()
    except ValueError as error:
        raise RoutingError(
            f"{engine} returned a non-JSON body (HTTP {response.status_code})"
        ) from error


@dataclass(frozen=True, slots=True)
class ValhallaProvider:
    """The engine of record: self-hosted Valhalla, pedestrian costing (ADR-0020).

    ``client`` lets a caller (or a Tier-2 test) inject an ``httpx`` transport; when it is
    ``None`` a client is created per request with the bounded timeout and closed again.
    """

    endpoint: str = DEFAULT_VALHALLA_ENDPOINT
    #: A tile-warm container answers a walking route in well under a second; the ceiling is
    #: there so a wedged engine fails the compile instead of holding it open.
    timeout: float = 60.0
    client: httpx.Client | None = None

    def route(
        self, waypoints: Sequence[Waypoint], *, costing: PedestrianCosting
    ) -> tuple[RouteLegV1, ...]:
        _require_pair(waypoints)
        body = self._post("/route", {"locations": _latlon(waypoints), **self._costing(costing)})
        legs = _legs_from_valhalla_trip(body)
        if len(legs) != len(waypoints) - 1:
            raise RoutingError(
                f"valhalla returned {len(legs)} legs for {len(waypoints)} waypoints; "
                "leg i must join stop i to stop i+1"
            )
        return legs

    def matrix(self, waypoints: Sequence[Waypoint], *, costing: PedestrianCosting) -> TravelMatrix:
        _require_pair(waypoints)
        locations = _latlon(waypoints)
        body = self._post(
            "/sources_to_targets",
            {"sources": locations, "targets": locations, **self._costing(costing)},
        )
        return _matrix_from_valhalla(body)

    def _costing(self, costing: PedestrianCosting) -> dict[str, Any]:
        # Sent on *every* request, never omitted: an absent costing_options block is
        # invisible in the geometry and silently reprices the whole day at 5.1 km/h.
        return {
            "costing": "pedestrian",
            "costing_options": costing.as_valhalla(),
            "units": "kilometers",
        }

    def _post(self, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        with _borrowed(self.client, self.timeout) as client:
            status, body = _post_json(
                client,
                f"{self.endpoint.rstrip('/')}{path}",
                payload,
                engine="valhalla",
                timeout=self.timeout,
            )
        if not isinstance(body, dict):
            raise RoutingError(f"valhalla {path} returned {type(body).__name__}, not an object")
        if status >= 400 or "error" in body:
            code = body.get("error_code")
            message = body.get("error", f"HTTP {status}")
            if isinstance(code, int) and code in _NO_ROUTE_CODES:
                raise NoRouteFound(f"valhalla error {code}: {message}")
            raise RoutingError(f"valhalla {path} failed (error_code={code}): {message}")
        return body


def _latlon(waypoints: Sequence[Waypoint]) -> list[dict[str, float]]:
    """Valhalla takes ``{"lat": …, "lon": …}`` objects — named keys, so no axis ambiguity."""
    return [{"lat": w.lat, "lon": w.lon} for w in waypoints]


@dataclass(frozen=True, slots=True)
class OpenRouteServiceProvider:
    """The zero-infra dev fallback — a free key instead of a docker container (ADR-0020).

    Two deliberate differences from Valhalla, both visible rather than hidden:

    1. **ORS exposes no walking-speed parameter.** Honouring the caller's pace therefore
       means deriving each duration from the *routed distance* and the requested pace, and
       discarding ORS's own duration. That keeps the seam's promise — the pace always took
       effect — at the cost of ORS's slope-aware timing, which is one more reason this is
       the fallback and not the engine of record.
    2. **Legs name their own engine** (``openrouteservice:foot-walking``). The card fixes
       ``valhalla:pedestrian`` for M1 because Valhalla is the M1 engine; stamping an ORS
       leg with Valhalla's id would be a provenance claim about machinery that never ran.
    """

    api_key: str
    endpoint: str = DEFAULT_ORS_ENDPOINT
    profile: str = "foot-walking"
    timeout: float = 60.0
    client: httpx.Client | None = None

    def route(
        self, waypoints: Sequence[Waypoint], *, costing: PedestrianCosting
    ) -> tuple[RouteLegV1, ...]:
        _require_pair(waypoints)
        body = self._post(
            f"/v2/directions/{self.profile}/geojson", {"coordinates": _lonlat(waypoints)}
        )
        features = body.get("features") or []
        if not features:
            raise NoRouteFound("openrouteservice returned no route feature")
        coordinates = features[0]["geometry"]["coordinates"]
        properties = features[0]["properties"]
        segments = properties.get("segments") or []
        cuts = properties.get("way_points") or [0, len(coordinates) - 1]
        source = routing_source("openrouteservice", self.profile)
        legs: list[RouteLegV1] = []
        for index, segment in enumerate(segments):
            distance_m = float(segment["distance"])  # ORS returns metres when units=m
            legs.append(
                _leg(
                    index,
                    _linestring(
                        [
                            (float(x), float(y))
                            for x, y in coordinates[cuts[index] : cuts[index + 1] + 1]
                        ]
                    ),
                    distance_m,
                    round(distance_m / costing.metres_per_second),
                    source,
                )
            )
        if not legs:
            raise NoRouteFound("openrouteservice returned a route with no segments")
        return tuple(legs)

    def matrix(self, waypoints: Sequence[Waypoint], *, costing: PedestrianCosting) -> TravelMatrix:
        _require_pair(waypoints)
        body = self._post(
            f"/v2/matrix/{self.profile}",
            {"locations": _lonlat(waypoints), "metrics": ["distance"], "units": "m"},
        )
        rows = body.get("distances")
        if not rows:
            raise RoutingError("openrouteservice matrix response carries no distances")
        distances: list[tuple[float, ...]] = []
        durations: list[tuple[int, ...]] = []
        for i, row in enumerate(rows):
            for j, cell in enumerate(row):
                if cell is None:
                    raise NoRouteFound(f"no pedestrian route from waypoint {i} to waypoint {j}")
            distances.append(tuple(float(cell) for cell in row))
            durations.append(tuple(round(float(cell) / costing.metres_per_second) for cell in row))
        return TravelMatrix(durations_s=tuple(durations), distances_m=tuple(distances))

    def _post(self, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        with _borrowed(self.client, self.timeout) as client:
            status, body = _post_json(
                client,
                f"{self.endpoint.rstrip('/')}{path}",
                payload,
                engine="openrouteservice",
                timeout=self.timeout,
                headers={"Authorization": self.api_key, "User-Agent": USER_AGENT},
            )
        if not isinstance(body, dict):
            raise RoutingError(f"openrouteservice {path} returned {type(body).__name__}")
        if status >= 400 or "error" in body:
            raise RoutingError(f"openrouteservice {path} failed: {body.get('error', status)}")
        return body


def _lonlat(waypoints: Sequence[Waypoint]) -> list[list[float]]:
    """ORS takes bare ``[lon, lat]`` pairs — positional, so the axis order is the contract."""
    return [[w.lon, w.lat] for w in waypoints]


@dataclass(frozen=True, slots=True)
class FixtureProvider:
    """Replays the committed Valhalla captures — Tier 1's provider, provably offline.

    It decodes through :func:`_legs_from_valhalla_trip` / :func:`_matrix_from_valhalla`, the
    same functions :class:`ValhallaProvider` uses, so replaying exercises the real decoder
    rather than a second, agreeable one.

    A recording answers exactly one question. Asking it for a different number of waypoints,
    or for a pace it was not captured at, **raises** instead of replaying: the committed
    capture is a Rhodes walk at 4.5 km/h, and quietly returning those numbers for four stops
    in another town is the fixture-shaped version of the Andorra trap (fixtures/README.md).
    Providers are a protocol — a test needing another shape records another capture or
    writes its own double.
    """

    route_response: Mapping[str, Any]
    matrix_response: Mapping[str, Any]
    captured_costing: PedestrianCosting = PedestrianCosting(walking_speed_kmh=FIXTURE_COSTING_KMH)

    @classmethod
    def from_dir(cls, directory: Path | str = DEFAULT_FIXTURE_DIR) -> FixtureProvider:
        base = Path(directory)
        return cls(
            route_response=json.loads((base / FIXTURE_ROUTE_FILE).read_text(encoding="utf-8")),
            matrix_response=json.loads((base / FIXTURE_MATRIX_FILE).read_text(encoding="utf-8")),
        )

    def route(
        self, waypoints: Sequence[Waypoint], *, costing: PedestrianCosting
    ) -> tuple[RouteLegV1, ...]:
        _require_pair(waypoints)
        self._check(costing)
        legs = _legs_from_valhalla_trip(self.route_response)
        if len(legs) != len(waypoints) - 1:
            raise RoutingError(
                f"the recorded capture has {len(legs)} legs; {len(waypoints)} waypoints would "
                f"need {len(waypoints) - 1}. A fixture replays what was captured."
            )
        return legs

    def matrix(self, waypoints: Sequence[Waypoint], *, costing: PedestrianCosting) -> TravelMatrix:
        _require_pair(waypoints)
        self._check(costing)
        matrix = _matrix_from_valhalla(self.matrix_response)
        if matrix.size != len(waypoints):
            raise RoutingError(
                f"the recorded matrix is {matrix.size}×{matrix.size}; {len(waypoints)} "
                "waypoints were asked for. A fixture replays what was captured."
            )
        return matrix

    def _check(self, costing: PedestrianCosting) -> None:
        if costing.walking_speed_kmh != self.captured_costing.walking_speed_kmh:
            raise RoutingError(
                f"this capture was recorded at {self.captured_costing.walking_speed_kmh} km/h; "
                f"{costing.walking_speed_kmh} km/h was requested. Replaying it would report "
                "durations no engine ever computed for that pace."
            )


def select_provider(env: Mapping[str, str] | None = None) -> RoutingProvider:
    """Construct the provider ``SIYUR_ROUTING_PROVIDER`` names (``env`` for testability).

    Unset means Valhalla — see :data:`DEFAULT_PROVIDER` for why the fixture is never the
    implicit answer. An unknown name is a ``ValueError``: a typo must not fall back to
    anything, least of all to a recording.
    """
    environment = os.environ if env is None else env
    name = environment.get(PROVIDER_ENV, DEFAULT_PROVIDER).strip().lower()
    if name == "valhalla":
        return ValhallaProvider(
            endpoint=environment.get(VALHALLA_URL_ENV, DEFAULT_VALHALLA_ENDPOINT)
        )
    if name in ("ors", "openrouteservice"):
        key = environment.get(ORS_API_KEY_ENV)
        if not key:
            raise ValueError(f"{PROVIDER_ENV}={name} needs {ORS_API_KEY_ENV} in the environment")
        return OpenRouteServiceProvider(api_key=key)
    if name == "fixture":
        _log.warning("routing provider is the recorded fixture — replayed, not routed")
        return FixtureProvider.from_dir(environment.get(FIXTURE_DIR_ENV, DEFAULT_FIXTURE_DIR))
    raise ValueError(
        f"unknown {PROVIDER_ENV}={name!r}; expected one of valhalla, ors, fixture (ADR-0020)"
    )
