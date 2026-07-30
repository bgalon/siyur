"""Geo stale-API tripwire (DU-00 unit d).

The dominant geo-specific agent failure mode is emitting *v-old* API idioms that
models still carry from training data (AGENTS.md "Geo APIs" table;
methods-ramp-up-standards §6). All four core libs crossed breaking majors:
Shapely 2.x, h3-py 4.x (full rename), OSMnx 2.x, GeoPandas 1.x.

This test exercises every geo entrypoint on its **current** API and asserts the
removed v-old names are gone, so any stale call fails CI immediately — before it
can reach product code. It is offline (no network): no ``graph_from_place`` etc.

Pins under test (see pyproject.toml): shapely~=2.1 · h3~=4.5 · osmnx~=2.1 ·
geopandas~=1.1 · pyproj~=3.7.
"""

import geopandas as gpd
import h3
import osmnx as ox
import pyproj
import shapely
from shapely import Point, unary_union


def _major_minor(version: str) -> tuple[int, int]:
    major, minor = version.split(".")[:2]
    return int(major), int(minor)


# --- Shapely 2.1 -----------------------------------------------------------
def test_shapely_pin_and_current_api() -> None:
    assert _major_minor(shapely.__version__) == (2, 1)
    # 2.x: vectorized `unary_union` + `.geom_type` (NOT 1.x `cascaded_union`/`.type`)
    merged = unary_union([Point(0, 0), Point(1, 1)])
    assert merged.geom_type == "MultiPoint"


def test_shapely_vold_idioms_removed() -> None:
    # 1.x `cascaded_union` was removed in Shapely 2.0
    assert not hasattr(shapely, "cascaded_union")
    # geometries are immutable in 2.x — no mutable coord assignment
    assert not hasattr(Point(0, 0), "coords_writable")


# --- h3-py 4.5 (entire API renamed in v4) ----------------------------------
def test_h3_pin_and_current_api() -> None:
    assert _major_minor(h3.__version__) == (4, 5)
    # v4 names: latlng_to_cell / cell_to_latlng / grid_disk
    cell = h3.latlng_to_cell(32.05, 34.75, 9)
    lat, _lng = h3.cell_to_latlng(cell)
    assert abs(lat - 32.05) < 0.01
    assert len(h3.grid_disk(cell, 1)) == 7  # center + 6 neighbours


def test_h3_v3_names_removed() -> None:
    # the v3→v4 rename is the classic trap: geo_to_h3 / h3_to_geo / k_ring are gone
    for stale in ("geo_to_h3", "h3_to_geo", "k_ring", "h3_to_geo_boundary"):
        assert not hasattr(h3, stale), f"stale h3 v3 name present: {stale}"


# --- OSMnx 2.1 (2.0 was a breaking rewrite) --------------------------------
def test_osmnx_pin_and_no_v1_paths() -> None:
    assert _major_minor(ox.__version__) == (2, 1)
    # 1.x `ox.utils_graph` module was removed in the 2.0 rewrite
    assert not hasattr(ox, "utils_graph")
    # a 2.x namespace that must exist (offline attribute check, no download)
    assert hasattr(ox, "graph")


# --- GeoPandas 1.1 ---------------------------------------------------------
def test_geopandas_pin_and_current_api() -> None:
    assert _major_minor(gpd.__version__) == (1, 1)
    # 1.x: `union_all()` replaces the deprecated `.unary_union` property on frames
    gs = gpd.GeoSeries([Point(0, 0), Point(1, 1)], crs="EPSG:4326")
    assert gs.union_all().geom_type == "MultiPoint"


# --- pyproj 3.7 (CRS discipline: EPSG:4326 lon/lat unless a card says else) --
def test_pyproj_transform_4326_to_3857() -> None:
    assert _major_minor(pyproj.__version__) == (3, 7)
    # always_xy=True → (lon, lat) order, matching the schema-card CRS convention
    transformer = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    x, y = transformer.transform(34.75, 32.05)  # Tel Aviv-ish (lon, lat)
    assert 3_800_000 < x < 3_900_000
    assert 3_700_000 < y < 3_800_000
