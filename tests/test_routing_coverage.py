"""FAIL-010's guardrail — a live router must cover the area our fixtures use.

`siyur-valhalla-1` spent days reporting ``Up (healthy)`` while serving the upstream image's
default **Andorra** demo extract. `/status` answered ``200`` with a populated
``tileset_last_modified``; every health signal in the stack was green; and every route in the
fixtures' own area returned ``171 No suitable edges``.

Nothing caught it because **nothing in the suite routes over a live router at all** —
`test_routing.py` exercises `select_provider` against literal env dicts, and every other
routing test replays the committed captures through `FixtureProvider`. That is the correct
default (ADR-0020: CI must not pay for a graph build per PR), and it is exactly why the live
service could be wrong indefinitely.

So this module asserts the proposition the health checks do not:

    "the service is running"  ≠  "the service can route where we are going"

**Opt-in on purpose.** It skips unless `SIYUR_ROUTING_PROVIDER` names a live provider, so it
cannot redden CI and cannot force a contributor to build a regional extract. A guardrail that
made every PR depend on a 340 MB download would be traded away within a week; one that fires
for whoever actually points at a live router is the one that survives.

**The probe is derived, never written down.** Coordinates come from the committed capture's
own ``trip.locations``, found by glob rather than by name — the same discipline PR #94 had to
retrofit onto `commons/routing.py` after a hardcoded fixture filename tripped
`evals/test_genericity.py`. A literal pair here would be a place literal in the tree *and*
would silently stop testing the right region the moment the fixtures moved.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from commons.routing import (
    FIXTURE_ROUTE_GLOB,
    PROVIDER_ENV,
    PedestrianCosting,
    RoutingError,
    Waypoint,
    select_provider,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests/fixtures"

#: Providers that reach a real service. `fixture` replays captures and proves nothing here.
LIVE_PROVIDERS = frozenset({"valhalla", "ors"})

pytestmark = pytest.mark.integration


def _configured_provider() -> str:
    return os.environ.get(PROVIDER_ENV, "").strip().lower()


def _probe_waypoints() -> tuple[Waypoint, Waypoint]:
    """Two waypoints from the committed capture — the area the rest of the suite believes in.

    Glob, not a filename: naming one area's capture is what made a second area's capture
    unusable without editing product code, which is the coupling SC-009 exists to prevent.
    """
    captures = sorted(FIXTURE_DIR.glob(FIXTURE_ROUTE_GLOB))
    if len(captures) != 1:  # pragma: no cover — a repo-shape problem, not a routing one
        pytest.fail(
            f"expected exactly one {FIXTURE_ROUTE_GLOB} capture in {FIXTURE_DIR}, "
            f"found {[c.name for c in captures]}. This guard derives its probe from that "
            f"capture; with 0 or 2 it cannot know which area to assert coverage of."
        )
    locations = json.loads(captures[0].read_text(encoding="utf-8"))["trip"]["locations"]
    first, second = locations[0], locations[1]
    return (
        Waypoint(lon=float(first["lon"]), lat=float(first["lat"])),
        Waypoint(lon=float(second["lon"]), lat=float(second["lat"])),
    )


@pytest.mark.skipif(
    _configured_provider() not in LIVE_PROVIDERS,
    reason=f"{PROVIDER_ENV} does not name a live provider; nothing to check coverage of",
)
def test_the_live_router_covers_the_fixture_area() -> None:
    """Route a real leg where the fixtures live, and blame *coverage* when it fails.

    The failure message is the point. A bare ``171 No suitable edges`` sends a reader to
    look for a bug in their waypoints or their costing; it took two coordinate pairs and a
    comparison to work out that the *graph* was of somewhere else entirely. Naming the
    likely cause turns a mystery into a one-line fix.
    """
    start, end = _probe_waypoints()
    provider = select_provider()

    try:
        legs = provider.route((start, end), costing=PedestrianCosting(walking_speed_kmh=4.5))
    except RoutingError as error:  # pragma: no cover — only on a misconfigured graph
        pytest.fail(
            f"the live router refused a leg inside the fixtures' own area "
            f"({start.lat:.4f},{start.lon:.4f} -> {end.lat:.4f},{end.lon:.4f}): {error}\n"
            f"\n"
            f"This is almost certainly COVERAGE, not a defect in the request: the service "
            f"answers and reports healthy, but its graph is of somewhere else. Check what "
            f"`SIYUR_VALHALLA_PBF_URL` was set to when the tiles were built — an empty value "
            f"makes the upstream image build its own Andorra demo extract (FAIL-010). "
            f"Rebuilding needs `SIYUR_VALHALLA_FORCE_REBUILD=True` *and* the stale .osm.pbf "
            f"removed from the tiles volume, because the image reuses a PBF already on disk "
            f"and will cheerfully rebuild the wrong region."
        )

    assert legs, "the router returned no legs for a routable pair"
    leg = legs[0]
    assert leg.distance_m > 0, "a leg between two distinct waypoints cannot be 0 m"
    assert leg.duration_s > 0, "a leg between two distinct waypoints cannot take 0 s"
    # FR-003: a two-point line is a straight line pretending to be a route. This is the
    # assertion that separates "the graph answered" from "the graph has streets here" — a
    # graph with no local edges cannot produce an intermediate vertex.
    assert len(leg.geometry.coords) >= 3, (
        f"the leg has {len(leg.geometry.coords)} vertices; a real walking route follows "
        f"the network and has intermediate points"
    )
