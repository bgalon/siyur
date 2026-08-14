"""T037 — the walk-graph tripwire: nodedness, connectivity, and the artifact's shape.

**Pre-armed, not reactive** (spec-002 plan Risk 3). Nothing has gone wrong yet; this exists
because when it does go wrong there is no signal. An un-noded walking network produces a file
that renders, hashes, carries the expected feature count and passes every downstream check,
and is a heap of disconnected islands. The person who finds out is the traveller, off-route,
offline, with nothing to fall back to.

**So the guardrail is proved to bite** (`tests/AGENTS.md`, FAIL-007): every check here is
driven against a deliberately broken graph as well as a good one. The headline case is
:func:`test_an_un_noded_grid_is_a_heap_of_islands_and_noding_makes_it_one` — a 9×10 street
grid whose ways cross without sharing a vertex comes to **16 components**, and the same
linework through :func:`~compiler.routes.node_lines` comes to **1**.

That the difference is the *traveller's* difference and not a bookkeeping one was measured
against the real client library rather than argued from the topology (2026-08-14,
``geojson-path-finder`` 2.1.0 driving ``web/src/travel/recovery.ts``'s own
``OffRouteRecovery`` over both files):

    un-noded  interior vertex → interior vertex   straight_line   473 m,  2 vertices
    noded     interior vertex → interior vertex   graph           658 m, 14 vertices
    un-noded  corner → corner (outer ring only)   graph           858 m,  3 vertices

The third line is why this needs a tripwire and not a smoke test: on the un-noded file the
*perimeter* still routes, because those ways happen to share endpoints at the corners. A
"does recovery work?" check written the obvious way passes on the broken artifact.

Tier 1: no network, no Valhalla container. Legs are replayed through
:class:`~commons.routing.FixtureProvider` — the same decoders the live provider uses — and
the network geometry is synthetic, so what is asserted about it is a property of the code
rather than of one recorded town.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from shapely import LineString

from commons import licenses
from commons.geo import GeometryError
from commons.routing import (
    ATTRIBUTION,
    LICENSE,
    FixtureProvider,
    PedestrianCosting,
    Waypoint,
)
from compiler.quarantine import quarantine_legs
from compiler.routes import (
    LEGS_PATH,
    WALK_GRAPH_PATH,
    Bbox,
    DisconnectedWalkGraphError,
    EmptyWalkNetworkError,
    OsmnxWalkNetwork,
    build_walk_graph,
    compile_routes,
    graph_components,
    legs_bytes,
    node_lines,
    noding_violations,
    require_connected,
    walk_graph_source,
)

#: The three ``break`` locations of the committed Valhalla capture
#: (``tests/fixtures/valhalla_rhodes_route.json``), in the capture's own order: the fixture
#: replays two legs and refuses any other waypoint count, so these are not interchangeable
#: with arbitrary points.
_WAYPOINTS: tuple[Waypoint, ...] = (
    Waypoint(28.228003, 36.44605),
    Waypoint(28.228275, 36.443635),
    Waypoint(28.225656, 36.44252),
)
#: The pace the capture was recorded at; ``FixtureProvider`` refuses any other.
_COSTING = PedestrianCosting(walking_speed_kmh=4.5)

#: Grid origin chosen to contain :data:`_WAYPOINTS` with room to spare. ~0.0005° ≈ 45 m, so
#: every point inside the grid is within ~25 m of a "street" — a plausible urban block size
#: and comfortably inside the default snap tolerance.
_GRID_ORIGIN = (28.2250, 36.4420)
_GRID_SPACING = 0.0005


def _grid(
    min_lon: float, min_lat: float, *, spacing: float = _GRID_SPACING, cols: int = 9, rows: int = 10
) -> list[LineString]:
    """A street grid as **un-noded** linework: full-length ways that cross without vertices.

    This is the realistic shape of the problem. Ways from OSM share node ids and arrive noded;
    ways assembled from more than one source, or hand-authored, or reconstructed, do not — and
    the two files look identical to everything except a path-finder.
    """
    max_lon, max_lat = min_lon + spacing * (cols - 1), min_lat + spacing * (rows - 1)
    horizontals = [
        LineString([(min_lon, min_lat + row * spacing), (max_lon, min_lat + row * spacing)])
        for row in range(rows)
    ]
    verticals = [
        LineString([(min_lon + col * spacing, min_lat), (min_lon + col * spacing, max_lat)])
        for col in range(cols)
    ]
    return horizontals + verticals


def _coordinates(graph_json: dict[str, Any]) -> list[list[list[float]]]:
    return [feature["geometry"]["coordinates"] for feature in graph_json["features"]]


# --- nodedness -----------------------------------------------------------------------------


def test_unary_union_splits_a_crossing_that_shares_no_vertex() -> None:
    """The noder's whole job: an X becomes four edges meeting at one node."""
    horizontal = LineString([(0.0, 0.0), (0.001, 0.0)])
    vertical = LineString([(0.0005, -0.0005), (0.0005, 0.0005)])

    edges = node_lines([horizontal, vertical])

    assert len(edges) == 4
    crossing = (0.0005, 0.0)
    assert all(crossing in (tuple(edge.coords[0]), tuple(edge.coords[-1])) for edge in edges)


def test_the_nodedness_check_fires_on_the_crossing_it_exists_to_catch() -> None:
    """The negative control. Un-noded in, violation out; noded in, silence.

    A check that only ever runs against good input is not evidence about bad input.
    """
    horizontal = LineString([(0.0, 0.0), (0.001, 0.0)])
    vertical = LineString([(0.0005, -0.0005), (0.0005, 0.0005)])

    violations = noding_violations([horizontal, vertical])

    assert violations, "the checker did not notice two ways crossing with no shared node"
    assert "cross at" in violations[0]
    assert noding_violations(node_lines([horizontal, vertical])) == ()


def test_the_nodedness_check_fires_on_a_collinear_overlap() -> None:
    """Two ways running along each other — the second shape of un-noded linework."""
    violations = noding_violations(
        [LineString([(0.0, 0.0), (0.002, 0.0)]), LineString([(0.001, 0.0), (0.003, 0.0)])]
    )

    assert violations and "overlap" in violations[0]


def test_the_nodedness_check_fires_on_a_t_junction_ending_mid_edge() -> None:
    """A side street ending on another way's *interior* vertex is still not a node."""
    through = LineString([(0.0, 0.0), (0.001, 0.0), (0.002, 0.0)])
    side = LineString([(0.001, 0.0), (0.001, 0.001)])

    assert noding_violations([through, side])
    assert noding_violations(node_lines([through, side])) == ()


def test_an_un_noded_grid_is_a_heap_of_islands_and_noding_makes_it_one() -> None:
    """The headline: 16 components before, 1 after, from the same linework.

    The 16 is worth reading. It is not 19 (one per way) because the four perimeter ways *do*
    share endpoints at the grid corners — which is exactly why the failure is silent: a
    recovery test that happens to route along the perimeter succeeds on the broken file.
    """
    lines = _grid(*_GRID_ORIGIN)

    assert len(graph_components(lines)) == 16
    assert len(graph_components(node_lines(lines))) == 1


# --- connectivity, judged against the day's own stops ---------------------------------------


def test_a_real_gap_between_two_networks_is_reported_not_bridged() -> None:
    """Two networks, a stop on each, nothing joining them: ``connected`` is False.

    And nothing is moved to make it True. Snap-rounding the two halves together would invent
    a crossing that does not exist on the ground; the disconnection is a fact about the area.
    """
    near, far = _grid(*_GRID_ORIGIN, cols=4, rows=4), _grid(28.2400, 36.4420, cols=4, rows=4)

    graph = build_walk_graph(
        near + far,
        anchors=(Waypoint(28.2255, 36.4425), Waypoint(28.2405, 36.4425)),
    )

    assert graph.connected is False
    assert graph.component_count == 2
    assert len(set(graph.anchor_components)) == 2
    # Nothing pruned, nothing bridged: the whole noded network is kept for diagnosis.
    assert graph.dropped_edges == 0
    assert graph.edge_count == len(node_lines(near + far))


def test_a_stop_beyond_the_snap_tolerance_is_named_with_its_distance() -> None:
    """An unreachable stop is a specific, actionable failure — not a bare ``False``."""
    graph = build_walk_graph(
        _grid(*_GRID_ORIGIN, cols=4, rows=4),
        anchors=(Waypoint(28.2255, 36.4425), Waypoint(28.2255, 36.4500)),
        snap_tolerance_m=100.0,
    )

    assert graph.connected is False
    assert [index for index, _ in graph.unreachable_anchors] == [1]
    ((_, metres),) = graph.unreachable_anchors
    assert metres == pytest.approx(723, abs=20)  # 0.0065° of latitude north of the grid


def test_the_snap_tolerance_is_measured_in_metres_not_degrees() -> None:
    """0.001° of longitude is 56 m at 60°N and 111 m at the equator. Degrees are not a unit.

    A tolerance applied in degree space silently becomes a different tolerance in every area —
    a genericity failure that no place-name scan can see. The anchor here is 0.001° east of a
    north-south way at 60°N: 55.7 m great-circle, 111.3 m if someone multiplied degrees by a
    flat 111 320.
    """
    way = LineString([(0.0, 59.99), (0.0, 60.01)])
    anchor = Waypoint(0.001, 60.0)

    assert build_walk_graph([way], anchors=(anchor,), snap_tolerance_m=60.0).connected is True
    assert build_walk_graph([way], anchors=(anchor,), snap_tolerance_m=50.0).connected is False


def test_islands_with_no_stop_are_pruned_and_counted() -> None:
    """ "Pruned walking network": components the day never touches are dropped, and reported."""
    served = _grid(*_GRID_ORIGIN, cols=4, rows=4)
    island = [LineString([(28.2400, 36.4420), (28.2405, 36.4420)])]

    graph = build_walk_graph(served + island, anchors=(Waypoint(28.2255, 36.4425),))

    assert graph.connected is True
    assert graph.component_count == 2
    assert graph.dropped_edges == 1
    assert graph.edge_count == len(node_lines(served))
    assert all(coords[0][0] < 28.23 for coords in _coordinates(graph.feature_collection()))


def test_require_connected_refuses_and_carries_the_stage_frame() -> None:
    """``connected: false`` fails the compile — and says so in the contract's own frame."""
    graph = build_walk_graph(
        _grid(*_GRID_ORIGIN, cols=4, rows=4) + _grid(28.2400, 36.4420, cols=4, rows=4),
        anchors=(Waypoint(28.2255, 36.4425), Waypoint(28.2405, 36.4425)),
    )

    with pytest.raises(DisconnectedWalkGraphError) as error:
        require_connected(graph)

    assert "do not join up" in str(error.value)
    assert error.value.frame["phase"] == "routes"
    assert error.value.frame["connected"] is False


# --- the artifact -------------------------------------------------------------------------


def test_the_emitted_geojson_is_the_shape_the_client_parser_accepts() -> None:
    """Every condition ``web/src/travel/recovery.ts::parseWalkGraph`` narrows on.

    That parser drops whatever it cannot use and returns ``null`` for an empty result, so a
    graph it silently thins is a graph the traveller partly does not have. Asserted here
    against the conditions in that file; the artifact was also run through the parser itself
    and through ``geojson-path-finder`` 2.1.0 (module docstring).
    """
    graph = build_walk_graph(_grid(*_GRID_ORIGIN), anchors=_WAYPOINTS)
    payload = json.loads(graph.to_bytes())

    assert payload["type"] == "FeatureCollection"
    assert len(payload["features"]) == graph.edge_count > 0
    for feature in payload["features"]:
        assert feature["type"] == "Feature"
        assert feature["geometry"]["type"] == "LineString"
        assert feature["properties"] == {}
        coordinates = feature["geometry"]["coordinates"]
        assert len(coordinates) >= 2
        for pair in coordinates:
            assert len(pair) == 2
            lon, lat = pair
            assert isinstance(lon, float) and isinstance(lat, float)
            assert math.isfinite(lon) and math.isfinite(lat)
            assert -180 <= lon <= 180 and -90 <= lat <= 90


def test_the_walk_graph_carries_the_derived_odbl_stamp() -> None:
    """OSM-derived, so ODbL — and bundleable **derived**, never author-set."""
    source = walk_graph_source()

    assert (source.kind, source.license, source.attribution) == ("osm", LICENSE, ATTRIBUTION)
    assert source.id == "siyur:walk-graph"  # the producer, not an engine that never ran
    assert licenses.bundleable(source.kind, source.license) is True

    payload = json.loads(build_walk_graph(_grid(*_GRID_ORIGIN), anchors=_WAYPOINTS).to_bytes())
    # A foreign member (RFC 7946 §6.1): the file states its own licence, and the client parser
    # ignores keys it does not model — verified by running parseWalkGraph over these bytes.
    assert payload["source"]["license"] == LICENSE
    assert payload["source"]["attribution"] == ATTRIBUTION


def test_the_artifact_is_byte_identical_whatever_order_the_lines_arrive_in() -> None:
    """An unchanged ``walk_graph_sha256`` must mean the network did not change.

    Upstream iteration order is not part of the network. If it reached the bytes, every
    recompile would produce a new hash and a new download for an identical graph.
    """
    lines = _grid(*_GRID_ORIGIN)
    forward = build_walk_graph(lines, anchors=_WAYPOINTS).to_bytes()
    reversed_ = build_walk_graph(list(reversed(lines)), anchors=_WAYPOINTS).to_bytes()

    assert forward == reversed_


# --- input discipline ------------------------------------------------------------------------


def test_a_swapped_lat_lon_network_is_refused() -> None:
    """(lat, lon) linework nodes and connects perfectly, in the wrong place entirely.

    Caught the only way it can be caught without a second opinion about where the area is:
    the latitude slot goes out of range. That means the swap is detected for an area east of
    90° or west of −90° and *not* for one inside that band — the same limit
    :mod:`commons.geo` states about itself, inherited rather than re-solved here.
    """
    east_of_90 = [(36.00, 137.00), (36.01, 137.00)]  # a (lat, lon) pair fed in as (lon, lat)

    with pytest.raises(GeometryError):
        build_walk_graph([LineString(east_of_90)])


def test_a_network_that_clips_away_to_nothing_is_refused() -> None:
    """An empty graph parses as "no graph" and demotes every recovery, silently."""
    with pytest.raises(EmptyWalkNetworkError):
        build_walk_graph([])

    with pytest.raises(EmptyWalkNetworkError):
        build_walk_graph(_grid(*_GRID_ORIGIN), clip_bbox=(0.0, 0.0, 0.1, 0.1))


def test_a_z_ordinate_is_tolerated_on_the_way_in_and_dropped_on_the_way_out() -> None:
    """3-D linework routes the same and would otherwise ship an elevation nothing reads."""
    graph = build_walk_graph(
        [LineString([(0.0, 60.0, 12.0), (0.0, 60.001, 14.0)])], anchors=(Waypoint(0.0, 60.0),)
    )

    assert graph.connected is True
    assert _coordinates(graph.feature_collection()) == [[[0.0, 60.0], [0.0, 60.001]]]


def test_clipping_keeps_only_the_linework_inside_the_bbox() -> None:
    """The bbox is the bundle's, so the graph is cut to it before it is noded."""
    box: Bbox = (28.2250, 36.4420, 28.2260, 36.4430)

    graph = build_walk_graph(
        _grid(*_GRID_ORIGIN), anchors=(Waypoint(28.2255, 36.4425),), clip_bbox=box
    )

    assert graph.connected is True
    for coordinates in _coordinates(graph.feature_collection()):
        for lon, lat in coordinates:
            assert box[0] <= lon <= box[2]
            assert box[1] <= lat <= box[3]


def test_the_osmnx_seam_calls_the_2_x_api_with_the_2_x_bbox_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OSMnx 2.0 changed both the signature and the tuple order. Pin the call, offline.

    1.x took ``north, south, east, west`` as separate arguments; 2.x takes one
    ``(left, bottom, right, top)`` tuple. A stale call still *runs* — it just fetches a
    different, usually empty, part of the world — so this asserts the arguments rather than
    the result. ``osmnx`` is stubbed into ``sys.modules``: the seam's import is function-local
    precisely so a Tier-1 test can do this without a socket.
    """
    calls: dict[str, Any] = {}
    edge = LineString([(28.2250, 36.4420), (28.2260, 36.4420)])

    def graph_from_bbox(bbox: Any, **kwargs: Any) -> str:
        calls["bbox"], calls["kwargs"] = bbox, kwargs
        return "graph"

    monkeypatch.setitem(
        sys.modules,
        "osmnx",
        SimpleNamespace(
            graph_from_bbox=graph_from_bbox,
            convert=SimpleNamespace(
                graph_to_gdfs=lambda graph, **kwargs: SimpleNamespace(geometry=[edge, None])
            ),
        ),
    )

    lines = OsmnxWalkNetwork().walk_lines((28.2250, 36.4420, 28.2290, 36.4465))

    assert calls["bbox"] == (28.2250, 36.4420, 28.2290, 36.4465)  # left, bottom, right, top
    assert calls["kwargs"]["network_type"] == "walk"
    # Both connectivity-critical, and both non-default — see the class docstring.
    assert calls["kwargs"]["retain_all"] is True
    assert calls["kwargs"]["truncate_by_edge"] is True
    assert lines == (edge,)


# --- the whole stage ---------------------------------------------------------------------


def test_compile_routes_produces_both_artifacts_and_the_contract_stage_frame() -> None:
    """Two artifacts, two hashes over the bytes that ship, and the frame `bundles.md` names."""
    compiled = compile_routes(
        waypoints=_WAYPOINTS,
        provider=FixtureProvider.from_dir(),
        costing=_COSTING,
        walk_lines=_grid(*_GRID_ORIGIN),
    )

    assert compiled.stage_frame == {
        "phase": "routes",
        "legs": 2,
        "walk_graph_edges": compiled.walk_graph.edge_count,
        "connected": True,
    }
    assert compiled.legs_artifact.path == LEGS_PATH
    assert compiled.walk_graph_artifact.path == WALK_GRAPH_PATH
    for artifact, data in (
        (compiled.legs_artifact, compiled.legs_data),
        (compiled.walk_graph_artifact, compiled.walk_graph_data),
    ):
        assert artifact.sha256 == hashlib.sha256(data).hexdigest()
        assert artifact.size_bytes == len(data)


def test_the_legs_survive_quarantine_carrying_the_produced_work_stamp() -> None:
    """A leg is a bare-``SourceRef`` shape: quarantine *derives*, and the answer is "keep".

    The failure this pins is the one `route-leg.md` spells out — a filter that reads a
    ``bundleable`` attribute off a leg finds nothing and drops every walking leg from every
    bundle, past every hash and path check, leaving a day with no routes.
    """
    compiled = compile_routes(
        waypoints=_WAYPOINTS,
        provider=FixtureProvider.from_dir(),
        costing=_COSTING,
        walk_lines=_grid(*_GRID_ORIGIN),
    )

    assert quarantine_legs(compiled.legs) == compiled.legs
    for index, leg in enumerate(compiled.legs):
        assert (leg.id, leg.from_stop, leg.to_stop) == (f"leg-{index}", index, index + 1)
        assert (leg.source.kind, leg.source.id) == ("osm", "valhalla:pedestrian")
        assert (leg.source.license, leg.source.attribution) == (LICENSE, ATTRIBUTION)


def test_the_legs_artifact_is_the_document_the_client_leg_parser_reads() -> None:
    """``routing/legs.json`` — ``{"legs": [...]}``, GeoJSON geometry, metres and seconds."""
    compiled = compile_routes(
        waypoints=_WAYPOINTS,
        provider=FixtureProvider.from_dir(),
        costing=_COSTING,
        walk_lines=_grid(*_GRID_ORIGIN),
    )
    payload = json.loads(compiled.legs_data)

    assert list(payload) == ["legs"]
    for leg in payload["legs"]:
        assert leg["mode"] == "walk"
        assert leg["geometry"]["type"] == "LineString"
        assert len(leg["geometry"]["coordinates"]) >= 2
        assert isinstance(leg["duration_s"], int) and leg["distance_m"] > 0
        assert leg["source"]["license"] == LICENSE
    assert compiled.legs_data == legs_bytes(compiled.legs)


def test_a_disconnected_network_fails_the_compile_with_the_truthful_frame() -> None:
    """The compile stops. The frame it stops with reports the legs it did route, and False.

    Shipping the islands instead would trade a build failure someone can fix for a traveller
    being told to walk through a building, offline, at the one moment the product exists for.
    """
    stranded = _WAYPOINTS + ()  # the capture's stops, one of which the network will not reach

    with pytest.raises(DisconnectedWalkGraphError) as error:
        compile_routes(
            waypoints=stranded,
            provider=FixtureProvider.from_dir(),
            costing=_COSTING,
            # A grid that covers only the first stop's corner of the area.
            walk_lines=_grid(*_GRID_ORIGIN, cols=3, rows=3),
        )

    assert error.value.frame == {
        "phase": "routes",
        "legs": 2,
        "walk_graph_edges": error.value.frame["walk_graph_edges"],
        "connected": False,
    }
    assert "snap tolerance" in str(error.value)


# --- the client itself: the actual web/src/travel/recovery.ts, driven from Node -----------
#
# Same mechanism `tests/test_compiler_manifest.py` uses for the TypeScript manifest verifier,
# and for the same reason: an assertion about what a *client* parses is worth exactly as much
# as the client that was run. `geojson-path-finder` is bundled into the build rather than left
# external — it is a CJS package, and Node's ESM loader hands back a namespace object where the
# browser build gets the constructor, so bundling is what makes the library under test the same
# one the app ships.

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
VITE_BIN = WEB_DIR / "node_modules" / ".bin" / "vite"
RECOVERY_TS = WEB_DIR / "src" / "travel" / "recovery.ts"
PATH_FINDER_PKG = WEB_DIR / "node_modules" / "geojson-path-finder"


def _node_toolchain_available() -> bool:
    return (
        shutil.which("node") is not None
        and VITE_BIN.exists()
        and PATH_FINDER_PKG.exists()
        and RECOVERY_TS.exists()
    )


@pytest.fixture(scope="session")
def recovery_module(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the *actual* ``web/src/travel/recovery.ts`` to plain ESM, once, for Node to drive.

    Skips — never fails — when the web toolchain is absent, exactly as the manifest verifier's
    fixture does: the Tier-1 Python lane has no ``web/node_modules``.
    """
    if not _node_toolchain_available():
        pytest.skip(
            "node/vite/web-node_modules not available — cannot drive the actual client "
            "recovery module from this Tier-1 test; run `pnpm -C web install` to enable it"
        )
    build_dir = tmp_path_factory.mktemp("ts_recovery_build")
    config = build_dir / "vite.lib.config.mjs"
    config.write_text(
        "import { resolve } from 'node:path'\n"
        "export default {\n"
        f"  root: {json.dumps(str(WEB_DIR))},\n"
        "  resolve: { alias: { 'geojson-path-finder': "
        f"{json.dumps(str(PATH_FINDER_PKG))} }} }},\n"
        "  build: {\n"
        f"    outDir: resolve({json.dumps(str(build_dir))}, 'dist'),\n"
        "    emptyOutDir: true,\n"
        "    lib: {\n"
        f"      entry: {json.dumps(str(RECOVERY_TS))},\n"
        "      formats: ['es'],\n"
        "      fileName: () => 'recovery.mjs',\n"
        "    },\n"
        "    minify: false,\n"
        "  },\n"
        "}\n"
    )
    result = subprocess.run(
        [str(VITE_BIN), "build", "--config", str(config)],
        cwd=build_dir,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"vite build of recovery.ts failed:\n{result.stdout}\n{result.stderr}")
    return build_dir / "dist" / "recovery.mjs"


def _drive_client_recovery(
    module_path: Path, tmp_path: Path, graph_json: dict[str, Any], name: str
) -> dict[str, Any]:
    """Parse ``graph_json`` with the client's own parser and route across it with its own class."""
    graph_path = tmp_path / f"{name}.geojson"
    graph_path.write_text(json.dumps(graph_json))
    script = tmp_path / f"drive_{name}.mjs"
    script.write_text(
        f"import {{ parseWalkGraph, OffRouteRecovery }} from "
        f"{json.dumps(module_path.resolve().as_uri())}\n"
        "import { readFileSync } from 'node:fs'\n"
        f"const parsed = parseWalkGraph(JSON.parse(readFileSync({json.dumps(str(graph_path))},"
        " 'utf-8')))\n"
        "const route = new OffRouteRecovery(parsed).route(\n"
        "  [28.2255, 36.4425], [28.2285, 36.446])\n"
        "process.stdout.write(JSON.stringify({\n"
        "  features: parsed === null ? 0 : parsed.features.length,\n"
        "  method: route.method, distance_m: route.distance_m,\n"
        "  vertices: route.coordinates.length }))\n"
    )
    result = subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=60, check=False
    )
    if result.returncode != 0:
        pytest.fail(f"node recovery check failed:\n{result.stdout}\n{result.stderr}")
    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


def test_the_real_client_routes_over_the_emitted_graph_and_not_over_an_un_noded_one(
    recovery_module: Path, tmp_path: Path
) -> None:
    """The claim this whole module rests on, checked against the library that has to honour it.

    Both files are the same 9×10 grid; only one of them went through :func:`node_lines`. The
    interior pair routed here is where a traveller actually is — not the perimeter, which
    routes on both files and is what makes the failure silent.
    """
    lines = _grid(*_GRID_ORIGIN)
    noded = build_walk_graph(lines, anchors=_WAYPOINTS).feature_collection()
    un_noded = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [list(xy) for xy in line.coords]},
                "properties": {},
            }
            for line in lines
        ],
    }

    good = _drive_client_recovery(recovery_module, tmp_path, noded, "noded")
    bad = _drive_client_recovery(recovery_module, tmp_path, un_noded, "un_noded")

    # Nothing the compiler emitted was thinned away by the client's parser.
    assert good["features"] == len(noded["features"])
    assert good["method"] == "graph"
    assert good["vertices"] > 2
    # …and the same linework un-noded gives the traveller a straight line through the blocks.
    assert bad["method"] == "straight_line"
