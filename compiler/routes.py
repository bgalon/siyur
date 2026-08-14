"""T036 — stage four of the compile order: the day's legs, and the graph to recover onto.

Two artifacts leave this module, both Produced Works of OSM and both hashed by T040:

* ``routing/legs.json`` — the precomputed Valhalla walking legs for the planned day. These
  carry the experience: offline there is no engine to ask, so the day's own route has to be
  in the bundle already. Routing itself happens behind the :mod:`commons.routing` seam
  (ADR-0020); nothing here names an engine or opens a socket.
* ``routing/walk_graph.geojson`` — the **pruned walking network**, for the traveller who
  leaves the plan. ``web/src/travel/recovery.ts`` walks it with ``geojson-path-finder``
  (ISC) on-device; it is approximate by design (no turn restrictions, naive costing), and a
  straight line is the labelled last resort when the graph cannot answer.

## Why noding is the whole point of this module

A walking network is a pile of ``LineString``\\ s. If two of them cross without sharing a
vertex at the crossing — which is the *normal* state of linework assembled from more than one
source, or clipped, or hand-authored — then no path-finder can turn there. The file still
looks right: it renders, it hashes, it has the expected number of features, and every check
downstream passes. What it has actually become is a set of disconnected islands, and the
first person to find out is the traveller, off-route, with no connectivity and no way to
recover. So the network is **noded** here (:func:`node_lines`, GEOS ``unary_union``), the
result is **checked** for nodedness (:func:`noding_violations`) and for **connectivity**
(:func:`graph_components`), and a graph that fails either **fails the compile**
(:func:`require_connected`) rather than shipping.

The connectivity question is asked against the day's own stops, not in the abstract: the
guarantee that matters is *every stop is on the network and reachable from every other one*.
Components holding no stop are islands — they are dropped (that is the "pruned" in pruned
walking network) and the count of what was dropped is reported rather than swallowed.

## What this module refuses to do

**No snap-rounding.** Nothing here moves a coordinate to close a gap. GEOS would happily
snap-round the linework together (``shapely.set_precision``), and for a network whose ways
genuinely share OSM node ids there is nothing to close; where a gap is real, closing it
silently invents a crossing that does not exist on the ground — a footbridge where there is
a fence. A real gap is reported as a disconnection instead, which is a fact about the area,
not a defect to paper over.

**No coordinates from a model, ever** (AGENTS.md geo rules). Geometry comes from OSM through
the injected :class:`WalkNetworkSource` and from Valhalla through the routing seam; every
number here is measured by shapely or by :func:`_metres_between`.

## Licensing

Both artifacts are Produced Works derived from OSM, so both carry ``ODbL-1.0`` and render
"© OpenStreetMap contributors". The stamp is minted through
:func:`commons.routing.routing_source`, which refuses any stamp that would not *derive* to
``bundleable=True`` — the flag is never author-set (`docs/data/route-leg.md`). The walk graph
is a GeoJSON file with nowhere to hang a model field, so its :class:`SourceRef` travels two
ways: as a foreign member on the ``FeatureCollection`` (RFC 7946 §6.1 permits it; the client
parser ignores keys it does not model) so the extracted file is self-describing, and as
:attr:`WalkGraph.source`, which is what ``compiler/attribution.py`` credits it from.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol, runtime_checkable

import rfc8785
from shapely import LineString, Point, STRtree, clip_by_rect, shortest_line, unary_union

from commons.geo import validate_lat, validate_lon
from commons.models import RouteLegV1, SourceRef
from commons.routing import PedestrianCosting, RoutingProvider, Waypoint, routing_source
from compiler.manifest import Artifact

__all__ = [
    "DEFAULT_SNAP_TOLERANCE_M",
    "LEGS_PATH",
    "NODE_PRECISION_DP",
    "WALK_GRAPH_PATH",
    "WALK_GRAPH_PRODUCER",
    "WALK_GRAPH_PROFILE",
    "Bbox",
    "CompiledRoutes",
    "DisconnectedWalkGraphError",
    "EmptyWalkNetworkError",
    "OsmnxWalkNetwork",
    "RouteCompileError",
    "UnnodedWalkGraphError",
    "WalkGraph",
    "WalkNetworkSource",
    "build_walk_graph",
    "compile_routes",
    "graph_components",
    "legs_bytes",
    "node_lines",
    "noding_violations",
    "require_connected",
    "walk_graph_source",
]

#: Bundle-relative paths of the two artifacts, as `docs/data/bundle-manifest.md` writes them.
#: Named here because this is the stage that produces the bytes; the manifest records whatever
#: path it is handed, so the strings live in one place rather than in both modules.
WALK_GRAPH_PATH: Final = "routing/walk_graph.geojson"
LEGS_PATH: Final = "routing/legs.json"

#: The walk graph's ``SourceRef.id`` is ``<producer>:<profile>``, the same convention
#: `route-leg.md` fixes for a leg — the *data* is OSM (``kind="osm"``, ODbL) and the ``id``
#: names the machinery that produced this particular artifact. Valhalla did not build this
#: graph, so stamping it ``valhalla:pedestrian`` would be a provenance claim about machinery
#: that never ran, which the card refuses in exactly those words.
WALK_GRAPH_PRODUCER: Final = "siyur"
WALK_GRAPH_PROFILE: Final = "walk-graph"

#: Decimal places a node coordinate is quantized to before edges are matched at it. 1e-9° is
#: ~0.1 mm — far below any positional meaning in OSM, and far above the last-bit differences
#: that would otherwise split one junction into two nodes and report a phantom disconnection.
NODE_PRECISION_DP: Final = 9

#: How far a stop may sit from the pruned network and still count as standing on it. A stop's
#: coordinate is a POI centroid — the middle of a building, a courtyard, a headland — so it is
#: routinely tens of metres from the nearest footway, and a tight tolerance would fail compiles
#: for correct data. A stop *further* than this is not a tolerance question: nothing on the
#: bundled network reaches it, and recovery there would be a straight line across whatever lies
#: between. Both directions are wrong to guess at, which is why this is a parameter.
DEFAULT_SNAP_TOLERANCE_M: Final = 100.0

#: Metres per degree of latitude. Used only to size a candidate query box in degrees; every
#: distance that matters is measured with :func:`_metres_between`, never with this.
_METRES_PER_DEGREE: Final = 111_320.0

#: Mean Earth radius — **the same constant ``web/src/travel/recovery.ts`` uses**, so a distance
#: measured here at compile time and one measured on the device agree instead of differing by a
#: fraction of a percent that looks like a routing bug.
_EARTH_RADIUS_M: Final = 6_371_008.8

#: Violations are reported to make a failure diagnosable, not to enumerate a broken network;
#: a fully un-noded graph would otherwise produce a message with one line per crossing.
_MAX_REPORTED_VIOLATIONS: Final = 8

#: ``(min_lon, min_lat, max_lon, max_lat)`` in EPSG:4326 — the ordering
#: ``TileSourceV1.bbox`` and ``commons.repository.sites_in_bbox`` already use. A plain tuple
#: rather than a class, so no two modules can disagree about whose bbox type is canonical.
type Bbox = tuple[float, float, float, float]

#: A node key: a quantized ``(lon, lat)`` pair. Internal, but named so the union-find and the
#: noding check are visibly keyed on the same thing.
type _Node = tuple[float, float]


class RouteCompileError(RuntimeError):
    """The routing stage cannot produce a shippable pair of artifacts."""


class EmptyWalkNetworkError(RouteCompileError):
    """No usable walking linework survived clipping — there is nothing to recover onto.

    Refused rather than shipped empty: an empty ``FeatureCollection`` is parsed by the client
    as "no graph", which silently demotes every recovery to a straight line for the whole day.
    """


class UnnodedWalkGraphError(RouteCompileError):
    """The emitted network is not topologically noded — see the module docstring."""


class DisconnectedWalkGraphError(RouteCompileError):
    """The day's stops are not all reachable from one another over the bundled network.

    Carries :attr:`frame` so a caller streaming the compile can emit the contract's truthful
    ``{"phase": "routes", …, "connected": false}`` status frame *before* failing — the traveller
    should never receive this bundle, and the operator should never have to guess why.
    """

    def __init__(self, message: str, frame: dict[str, Any]) -> None:
        super().__init__(message)
        self.frame = frame


@runtime_checkable
class WalkNetworkSource(Protocol):
    """Where the raw walking linework comes from. One method, so a test double is three lines.

    Injected rather than imported so :func:`compile_routes` itself never touches the network:
    the seam is what keeps Tier 1 offline, and what keeps Siyur's walking network genuinely
    swappable (an Overpass query, a committed extract, a PostGIS view) without this module
    learning about any of them.
    """

    def walk_lines(self, bbox: Bbox) -> Sequence[LineString]: ...


def walk_graph_source(
    producer: str = WALK_GRAPH_PRODUCER, profile: str = WALK_GRAPH_PROFILE
) -> SourceRef:
    """The ODbL stamp the walk graph is built under (see :data:`WALK_GRAPH_PRODUCER`).

    Minted through :func:`commons.routing.routing_source` rather than assembled here, because
    that function is where the "this stamp must derive to ``bundleable=True``" check lives.
    Two places minting an ODbL stamp is one place too many for the check to be trusted.
    """
    return routing_source(producer, profile)


# --- the network: clip, node, check ------------------------------------------------------


def node_lines(
    lines: Iterable[LineString], *, clip_bbox: Bbox | None = None
) -> tuple[LineString, ...]:
    """Clip, **node** and deterministically order the walking linework.

    ``unary_union`` is the noder: GEOS splits every input line at every intersection with
    another, then merges runs that meet at a degree-2 node, so the output linework meets only
    at shared endpoints — which is precisely the invariant ``geojson-path-finder`` needs to
    build a traversable topology. (Shapely 2.x: ``unary_union``, never 1.x ``cascaded_union``.)

    Clipping happens **before** the union so the noder only sees linework that will ship. Note
    what clipping costs: a way that leaves the box and comes back is cut into two pieces that
    no longer connect through the outside. Fetch with a buffer, and prefer a source that keeps
    boundary-crossing edges whole (:class:`OsmnxWalkNetwork` passes ``truncate_by_edge``).

    The result is sorted by coordinates. GEOS is deterministic for a given input *order*, and
    the input order is whatever an upstream iteration produced — sorting makes the artifact's
    bytes, and therefore its hash, a function of the network alone.
    """
    parts: list[LineString] = []
    for line in lines:
        if not isinstance(line, LineString):
            raise RouteCompileError(
                f"the walking network is built from LineStrings; got {type(line).__name__}"
            )
        # Range-checked here rather than trusted: a lat/lon-swapped network produces a graph
        # that nodes and connects perfectly and sits in the wrong hemisphere, and the only
        # symptom is that every recovery is a straight line (nothing is ever near the walker).
        # Any z is tolerated on the way in and dropped on the way out (:meth:`feature_collection`)
        # — the bundle's geometry is 2-D, and an elevation nobody reads is bytes on a phone.
        for vertex in line.coords:
            validate_lon(vertex[0])
            validate_lat(vertex[1])
        parts.extend(_line_parts(clip_by_rect(line, *clip_bbox) if clip_bbox else line))

    if not parts:
        raise EmptyWalkNetworkError(
            "no walking linework survived clipping"
            + (f" to {clip_bbox}" if clip_bbox else "")
            + " — the bundle would ship a graph with no edges, demoting every off-route "
            "recovery to a straight line with nothing to say so"
        )

    edges = _line_parts(unary_union(parts))
    return tuple(sorted(edges, key=lambda edge: tuple(edge.coords)))


def graph_components(edges: Sequence[LineString]) -> tuple[tuple[int, ...], ...]:
    """Group edge indices into connected components — edges sharing a node are one component.

    Endpoints alone are enough **because the input is noded**: a noded edge has no junction in
    its interior, so two edges are adjacent exactly when an endpoint of one equals an endpoint
    of the other. Run this on un-noded linework and it reports what an un-noded graph actually
    is — a heap of islands — which is what makes it a usable tripwire rather than a formality.

    Components come back ordered by their lowest edge index, so the grouping is stable.
    """
    parent: dict[_Node, _Node] = {}

    def find(node: _Node) -> _Node:
        root = parent.setdefault(node, node)
        while root != parent[root]:
            root = parent[root]
        while parent[node] != root:  # path compression, iterative: a long way is deep enough
            parent[node], node = root, parent[node]
        return root

    def union(left: _Node, right: _Node) -> None:
        parent[find(left)] = find(right)

    for edge in edges:
        union(*_endpoints(edge))

    groups: dict[_Node, list[int]] = {}
    for index, edge in enumerate(edges):
        groups.setdefault(find(_endpoints(edge)[0]), []).append(index)
    return tuple(tuple(members) for members in sorted(groups.values(), key=lambda m: m[0]))


def noding_violations(
    edges: Sequence[LineString], *, limit: int = _MAX_REPORTED_VIOLATIONS
) -> tuple[str, ...]:
    """Every place two edges meet somewhere other than at a shared endpoint. Empty is good.

    An **independent** check on the emitted network, deliberately not sharing code with the
    noder: :func:`node_lines` claims the property, this proves it. Two shapes of violation, and
    both make a path-finder silently refuse a turn a traveller can see with their own eyes:

    * an intersection at a point that is not an endpoint of *both* edges (a crossing, or a T
      where one way ends on another's interior);
    * an intersection with length — two edges running along each other.

    ``STRtree`` (shapely 2.x) keeps this near-linear on real networks by only comparing
    candidates whose envelopes meet; it is run on every compile because a graph that is not
    noded must not ship, and the cost is small beside routing the day.
    """
    tree = STRtree(list(edges))
    problems: list[str] = []
    for i, edge in enumerate(edges):
        ends_i = set(_endpoints(edge))
        for raw in tree.query(edge, predicate="intersects"):
            j = int(raw)
            if j <= i:
                continue
            ends_j = set(_endpoints(edges[j]))
            for part in _line_parts(edge.intersection(edges[j]), keep_points=True):
                if part.geom_type != "Point":
                    problems.append(
                        f"edges {i} and {j} overlap along a {part.geom_type} instead of "
                        "meeting at a node"
                    )
                    break
                node = _quantize(part.x, part.y)
                if node in ends_i and node in ends_j:
                    continue
                problems.append(
                    f"edges {i} and {j} cross at ({part.x:.7f}, {part.y:.7f}), which is not an "
                    "endpoint of both — a path-finder cannot turn there"
                )
            if len(problems) >= limit:
                return tuple(problems[:limit])
    return tuple(problems)


@dataclass(frozen=True, slots=True)
class WalkGraph:
    """The pruned walking network, and the honest verdict on it.

    ``connected`` is the shippability question: **every anchor sits on the network and every
    anchor can reach every other one**. When it is ``True``, ``edges`` is exactly the one
    component that answers it and the islands have been dropped. When it is ``False`` nothing
    is dropped — ``edges`` is the whole noded network, kept for diagnosis, and
    :func:`require_connected` refuses to let it into a bundle.
    """

    edges: tuple[LineString, ...]
    source: SourceRef
    connected: bool
    #: Components in the noded network **before** pruning — ``1`` means the area's walking
    #: network came out whole.
    component_count: int
    #: Edges in components holding no anchor, removed when ``connected``. A large number beside
    #: a small ``edges`` is the signature of linework that arrived in pieces.
    dropped_edges: int
    #: ``(anchor index, metres to the nearest edge)`` for anchors that snapped to nothing
    #: within the tolerance — the specific reason a graph is unshippable, not a bare False.
    unreachable_anchors: tuple[tuple[int, float], ...] = ()
    #: Component index (into the pre-pruning grouping) each anchor landed in; more than one
    #: distinct value means the day's stops are on networks that do not join up.
    anchor_components: tuple[int, ...] = ()

    @property
    def edge_count(self) -> int:
        return len(self.edges)

    def feature_collection(self) -> dict[str, Any]:
        """The GeoJSON ``web/src/travel/recovery.ts::parseWalkGraph`` reads.

        A ``FeatureCollection`` of 2-D ``LineString`` features in EPSG:4326 with empty
        ``properties`` — the client discards properties and ``geojson-path-finder`` weights
        edges by length, so anything put there would ship bytes nothing reads. ``source`` is a
        foreign member (RFC 7946 §6.1): the parser ignores unmodelled keys, and an extracted
        bundle should carry its own licence rather than depend on a second file.
        """
        return {
            "type": "FeatureCollection",
            "source": self.source.model_dump(mode="json"),
            "features": [
                {
                    "type": "Feature",
                    # ``[x, y]`` explicitly: a z ordinate that survived from the input would
                    # ship three-element positions, which RFC 7946 allows and the client's
                    # parser silently keeps — bytes on a phone that nothing ever reads.
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[vertex[0], vertex[1]] for vertex in coords],
                    },
                    "properties": {},
                }
                for coords in (tuple(edge.coords) for edge in self.edges)
            ],
        }

    def to_bytes(self) -> bytes:
        """The artifact's exact bytes. JCS, for the reason :func:`legs_bytes` gives."""
        return rfc8785.dumps(self.feature_collection())


def build_walk_graph(
    lines: Iterable[LineString],
    *,
    anchors: Sequence[Waypoint] = (),
    clip_bbox: Bbox | None = None,
    snap_tolerance_m: float = DEFAULT_SNAP_TOLERANCE_M,
    source: SourceRef | None = None,
) -> WalkGraph:
    """Node the linework, rule on its connectivity, and prune to the part the day needs.

    ``anchors`` are the day's stops. Each is snapped to its nearest edge (within
    ``snap_tolerance_m``, measured great-circle, not in degrees); the graph is connected iff
    every anchor snapped **and** all of them landed in one component. With no anchors the
    question degenerates to "is the network one piece", which is the right reading of an
    unanchored network but a much weaker guarantee — pass the stops.

    Returns the verdict rather than raising it: the caller streams the status frame and *then*
    fails (:func:`require_connected`), and a diagnostic can hold a disconnected graph without
    catching an exception to see it.
    """
    edges = node_lines(lines, clip_bbox=clip_bbox)
    violations = noding_violations(edges)
    if violations:
        raise UnnodedWalkGraphError(
            "the walking network is not topologically noded after unary_union, which should "
            "be impossible — every remaining crossing is a turn the traveller can see and "
            "geojson-path-finder cannot take: " + "; ".join(violations)
        )

    components = graph_components(edges)
    component_of = {index: number for number, members in enumerate(components) for index in members}
    tree = STRtree(list(edges))

    unreachable: list[tuple[int, float]] = []
    reachable: list[Mapping[int, float]] = []
    for position, anchor in enumerate(anchors):
        point = Point(anchor.lon, anchor.lat)
        options = _reachable_components(tree, edges, component_of, point, snap_tolerance_m)
        if not options:
            # Nothing within tolerance at all: report the true nearest so the message says how
            # far off it was rather than merely that it failed.
            _, metres = _nearest_edge(tree, edges, point)
            unreachable.append((position, metres))
        else:
            reachable.append(options)

    sizes = [len(members) for members in components]
    serving = None if unreachable else _serving_component(reachable, sizes)

    if serving is not None:
        landed = [serving] * len(reachable)
    else:
        # Diagnostic only — the compile is failing, and "which component was each stop
        # nearest" is what makes the failure readable. `min` on distance, so it names the
        # component the stop would have snapped to under the old nearest-edge rule.
        landed = [min(options, key=lambda c: options[c]) for options in reachable]

    connected = not unreachable and (serving is not None if reachable else len(components) == 1)

    kept = edges
    if connected and landed:
        # Every island is by definition anchor-free, so pruning cannot remove a stop's
        # neighbourhood — that case is a *disconnection*, and it has already failed above.
        kept = tuple(edges[index] for index in components[landed[0]])

    return WalkGraph(
        edges=kept,
        source=source if source is not None else walk_graph_source(),
        connected=connected,
        component_count=len(components),
        dropped_edges=len(edges) - len(kept),
        unreachable_anchors=tuple(unreachable),
        anchor_components=tuple(landed),
    )


def require_connected(graph: WalkGraph, *, frame: dict[str, Any] | None = None) -> WalkGraph:
    """Return ``graph`` if it is shippable; otherwise raise :class:`DisconnectedWalkGraphError`.

    ``connected: false`` **fails the compile**. The alternative — shipping the islands and
    letting the client fall back — trades a build failure someone can fix for a traveller
    standing in a street being told to walk through a building, offline, at the one moment the
    product exists for.
    """
    if graph.connected:
        return graph
    reasons: list[str] = []
    if graph.unreachable_anchors:
        reasons.append(
            "stops further than the snap tolerance from any edge: "
            + ", ".join(f"#{index} ({metres:.0f} m)" for index, metres in graph.unreachable_anchors)
        )
    if len(set(graph.anchor_components)) > 1:
        reasons.append(
            f"stops landed on {len(set(graph.anchor_components))} networks that do not join up "
            f"(components {sorted(set(graph.anchor_components))})"
        )
    if not reasons:
        reasons.append(f"the network came out in {graph.component_count} disconnected pieces")
    raise DisconnectedWalkGraphError(
        "the pruned walking network cannot serve this day — " + "; ".join(reasons),
        frame=frame if frame is not None else _stage_frame(0, graph),
    )


# --- the legs -----------------------------------------------------------------------------


def legs_bytes(legs: Sequence[RouteLegV1]) -> bytes:
    """``routing/legs.json`` exactly as it is written and hashed.

    ``{"legs": [...]}`` — ``web/src/travel/parse.ts::parseLegs`` accepts that or a bare array;
    the wrapped form leaves room for a sibling key (Plan B/C legs at M2) without changing the
    document's type out from under a reader.

    Serialized with JCS like every other generated artifact, so recompiling an unchanged day
    produces byte-identical output and an unchanged ``routing.legs_sha256`` means the legs did
    not change — rather than that the serializer happened to agree with itself today.
    """
    return rfc8785.dumps({"legs": [leg.model_dump(mode="json") for leg in legs]})


@dataclass(frozen=True, slots=True)
class CompiledRoutes:
    """What stage four hands the rest of the pipeline: two artifacts, their bytes, their hashes.

    The bytes travel *with* the :class:`~compiler.manifest.Artifact` that describes them, as
    :class:`~compiler.manifest.FrozenItinerary` does, because a hash beside bytes it was not
    taken over is the one failure the launch-time integrity check cannot explain.
    """

    legs: tuple[RouteLegV1, ...]
    legs_data: bytes
    legs_artifact: Artifact
    walk_graph: WalkGraph
    walk_graph_data: bytes
    walk_graph_artifact: Artifact

    @property
    def stage_frame(self) -> dict[str, Any]:
        """The ``routes`` status frame of `specs/002…/contracts/bundles.md`."""
        return _stage_frame(len(self.legs), self.walk_graph)


def compile_routes(
    *,
    waypoints: Sequence[Waypoint],
    provider: RoutingProvider,
    costing: PedestrianCosting,
    walk_lines: Iterable[LineString],
    anchors: Sequence[Waypoint] | None = None,
    clip_bbox: Bbox | None = None,
    snap_tolerance_m: float = DEFAULT_SNAP_TOLERANCE_M,
    graph_source: SourceRef | None = None,
    legs_path: str = LEGS_PATH,
    walk_graph_path: str = WALK_GRAPH_PATH,
) -> CompiledRoutes:
    """Route the day and build the network to recover onto — stage four, whole.

    ``waypoints`` are the stops in visiting order; leg *i* joins stop *i* to stop *i+1*, which
    is the seam's contract and the reason ``RouteLegV1.from_stop`` is a position rather than an
    id. ``anchors`` defaults to those same waypoints: the network has to serve the day it is
    compiled for, so that is what its connectivity is judged against.

    The legs are **not** quarantined here. Quarantine is stage five and runs over the whole
    bundle at once (`compiler/quarantine.py`); doing it twice would put two modules in charge
    of one refusal. The walk graph is not a model and never reaches that filter, which is why
    its stamp is minted through the deriving path instead (:func:`walk_graph_source`).

    Raises :class:`DisconnectedWalkGraphError` when the network cannot serve the day. The
    exception carries the truthful ``connected: false`` status frame, so a streaming caller can
    report the stage before the compile fails.
    """
    legs = tuple(provider.route(waypoints, costing=costing))
    graph = build_walk_graph(
        walk_lines,
        anchors=waypoints if anchors is None else anchors,
        clip_bbox=clip_bbox,
        snap_tolerance_m=snap_tolerance_m,
        source=graph_source,
    )
    require_connected(graph, frame=_stage_frame(len(legs), graph))

    legs_data = legs_bytes(legs)
    graph_data = graph.to_bytes()
    return CompiledRoutes(
        legs=legs,
        legs_data=legs_data,
        legs_artifact=Artifact.from_bytes(legs_path, legs_data),
        walk_graph=graph,
        walk_graph_data=graph_data,
        walk_graph_artifact=Artifact.from_bytes(walk_graph_path, graph_data),
    )


# --- the one networked implementation of the seam ------------------------------------------


@dataclass(frozen=True, slots=True)
class OsmnxWalkNetwork:
    """The walking network for a bbox, from OSM via OSMnx 2.x. **Fetches over the network.**

    Compile-time only, and never reached from Tier 1 — the import is local to the call so that
    importing this module costs nothing and a test that constructs the class but does not call
    it cannot accidentally pull in an Overpass-capable stack.

    Two argument choices carry the weight, both about connectivity:

    * ``retain_all=True``. OSMnx's default keeps only the largest weakly-connected component,
      *silently*. That is a decision about which part of the network matters, taken before
      anyone has looked at where the day's stops are — a small coastal or island area can put
      every stop on a component OSMnx would discard. Connectivity is judged here, against the
      stops (:func:`build_walk_graph`), and the pruning follows that judgement.
    * ``truncate_by_edge=True``. Keeps ways that cross the bbox boundary whole instead of
      cutting them at it, so the network does not fray into stubs at the edge of the box.

    OSMnx 2.x API only: ``graph_from_bbox(bbox=(left, bottom, right, top))`` — the 2.0 rewrite
    changed both the signature and the tuple order (1.x took ``north, south, east, west`` as
    separate arguments) — and ``ox.convert.graph_to_gdfs`` for the edge geometry, since 1.x's
    ``ox.utils_graph`` module no longer exists.
    """

    network_type: str = "walk"
    #: OSMnx's topology simplification: drop interstitial degree-2 nodes, keeping the geometry.
    #: Harmless for path-finding and a large reduction in the bundled file.
    simplify: bool = True

    def walk_lines(self, bbox: Bbox) -> tuple[LineString, ...]:
        import osmnx as ox

        min_lon, min_lat, max_lon, max_lat = bbox
        graph = ox.graph_from_bbox(
            (min_lon, min_lat, max_lon, max_lat),
            network_type=self.network_type,
            simplify=self.simplify,
            retain_all=True,
            truncate_by_edge=True,
        )
        edges = ox.convert.graph_to_gdfs(graph, nodes=False)
        return tuple(geom for geom in edges.geometry if isinstance(geom, LineString))


# --- internals -----------------------------------------------------------------------------


def _stage_frame(legs: int, graph: WalkGraph) -> dict[str, Any]:
    return {
        "phase": "routes",
        "legs": legs,
        "walk_graph_edges": graph.edge_count,
        "connected": graph.connected,
    }


def _line_parts(geometry: Any, *, keep_points: bool = False) -> list[Any]:
    """Flatten a geometry to its usable line parts (and, for the noding check, its points).

    Zero-length lines are dropped: they contribute no edge, and a "line" whose two vertices are
    identical becomes a node with no way out of it in the path-finder's topology.
    """
    parts = getattr(geometry, "geoms", None)
    if parts is not None:
        return [part for member in parts for part in _line_parts(member, keep_points=keep_points)]
    if geometry.is_empty:
        return []
    if geometry.geom_type == "LineString":
        return [geometry] if geometry.length > 0 else []
    if keep_points and geometry.geom_type == "Point":
        return [geometry]
    return []


def _quantize(lon: float, lat: float) -> _Node:
    return (round(lon, NODE_PRECISION_DP), round(lat, NODE_PRECISION_DP))


def _endpoints(edge: LineString) -> tuple[_Node, _Node]:
    coords = edge.coords
    return _quantize(*coords[0][:2]), _quantize(*coords[-1][:2])


def _reachable_components(
    tree: STRtree,
    edges: Sequence[LineString],
    component_of: Mapping[int, int],
    point: Point,
    tolerance_m: float,
) -> dict[int, float]:
    """Every component this point can snap to within ``tolerance_m``, and how far each is.

    The **nearest** edge is not the right question, and asking it was a real bug (ADR-0034).
    A stop half a metre from an isolated twenty-metre courtyard path, with the street that
    actually serves the day five metres away, snapped to the courtyard — a one-edge component
    — and the day was declared unshippable. Valhalla routed the same five legs without
    difficulty, because it was never confused about which network the traveller walks on.

    So the snap is a *set* of options and the choice is made once, globally, in
    :func:`build_walk_graph`: pick a component that serves **every** stop.

    The candidate radius is converted from metres to degrees using the longitude scale at this
    latitude, which is the tighter of the two axes, so the degree-space query over-covers in
    latitude and never under-covers. Exact haversine then filters it back. Doing this in raw
    degrees would shrink the tolerance by ``cos(latitude)`` — 25 % at 40° — which is the same
    genericity trap :func:`_nearest_edge` documents.
    """
    scale = max(math.cos(math.radians(point.y)), 0.01)
    radius_deg = tolerance_m / (_METRES_PER_DEGREE * scale)

    within: dict[int, float] = {}
    for index in tree.query(point.buffer(radius_deg)):
        edge = edges[int(index)]
        link = shortest_line(edge, point)
        (near_lon, near_lat), _ = tuple(link.coords)
        metres = _metres_between(near_lon, near_lat, point.x, point.y)
        if metres > tolerance_m:
            continue  # inside the degree box, outside the true circle
        component = component_of[int(index)]
        if metres < within.get(component, math.inf):
            within[component] = metres
    return within


def _serving_component(
    reachable: Sequence[Mapping[int, float]], sizes: Sequence[int]
) -> int | None:
    """The one component that serves every stop, or ``None`` if no single component does.

    Chosen by **total snap distance**, so the day walks on the network its stops actually sit
    on rather than whichever component happens to sort first. Ties break on the larger
    component and then the lower index, so the choice is deterministic and a rerun of the same
    compile produces the same bundle — which per-artifact hashing requires.
    """
    if not reachable:
        return None
    shared = set(reachable[0])
    for candidate in reachable[1:]:
        shared &= set(candidate)
    if not shared:
        return None
    return min(
        shared,
        key=lambda component: (
            sum(distances[component] for distances in reachable),
            -sizes[component],
            component,
        ),
    )


def _nearest_edge(tree: STRtree, edges: Sequence[LineString], point: Point) -> tuple[int, float]:
    """``(index, metres)`` of the edge nearest ``point``. The distance is great-circle.

    A degree-space distance would be wrong by ``1/cos(latitude)`` in longitude — 25% at 40°,
    which turns a documented 100 m tolerance into an undocumented 75 m one that varies with
    where in the world the area is. Genericity is not only about place names.
    """
    found = tree.query_nearest(point)
    index = int(found[0])
    link = shortest_line(edges[index], point)
    (near_lon, near_lat), _ = tuple(link.coords)
    return index, _metres_between(near_lon, near_lat, point.x, point.y)


def _metres_between(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """Haversine metres, mirroring ``haversineMetres`` in ``web/src/travel/recovery.ts``."""
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    h = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return 2 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))
