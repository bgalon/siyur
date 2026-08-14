"""The compile pipeline — the ordered §5.3 stages as one generator of the contract's frames.

T042. Everything upstream produces one artifact each; this module runs them in the order
`specs/002-plan-compile-offline/contracts/bundles.md` fixes — ``tiles → routes → quarantine
→ content → attribution → hash``, then ``manifest``, then ``done`` — and yields exactly the
frames that contract specifies, so ``api/bundles.py`` stays a thin SSE transport shim.

It mirrors :mod:`planner.pipeline` deliberately: same :class:`Event` shape, same
generator-the-caller-drives posture, same injected-persistence rule. Two pipelines, one
pattern.

**A generator, not a background job.** The caller drives it, so a client that disconnects
simply stops pulling and the compile stops with it — no orphaned extract, no half-written
bundle. (:data:`COMPILE_IN_PROCESS_ENV` is the M1 flag from tech-design §5.3: M1 runs this
in the API process, M2 moves it to a Cloud Run Job. The flag is read by the *transport*, not
here — this module is the same generator either way.)

**Storage is injected** (:class:`BundleObjectStore`), never imported. ``compiler/`` therefore
names no bucket and no SDK, exactly as ``planner/`` names no session, and Tier 1 drives the
whole pass against an in-memory fake. The plan row's ``approved → compiling → compiled``
transitions are the API's (``commons.repository.claim_plan_for_compile`` / ``finish_compile``)
and are deliberately *not* touched here: this pipeline knows about a bundle, not about a
transaction.

## The frame order is the contract's; the evaluation order is not, and cannot be

The contract emits ``tiles`` first and ``quarantine`` third. The quarantine **filter** runs
before either, because the tile stage's glyph selection is defined over *the bundled sites'
own labels* (T035a, `compiler/tiles.py`) — and a name that quarantine withholds is not a
bundled label. Selecting ranges from pre-quarantine names would vendor bytes for a script the
map never renders, and can fail the compile outright through
:class:`~compiler.tiles.GlyphCoverageError` for a script that exists only in a withheld name.

Nothing is lost by that: quarantine is a pure in-memory filter, and the invariant it carries
is *"every ``bundleable=false`` value is dropped **before anything is frozen or hashed**"*
(FR-011/SC-004), which the running order strengthens rather than weakens. What the ``tiles``
frame reports is unchanged; what the ``quarantine`` frame reports is unchanged. A refusal
(FR-012, unstamped input) therefore also happens before the first byte is written, which is
the behaviour the contract's "nothing is persisted" clause asks for at its cheapest.

## The legs are **frozen, not re-routed**

``routing/legs.json`` is the standalone freeze of the approved day's own legs
(``web/src/travel/index.ts`` says so in as many words: *"the standalone freeze of the same
objects"*). This pipeline therefore composes :func:`~compiler.routes.legs_bytes` over
``itinerary.legs`` and does **not** call :func:`~compiler.routes.compile_routes`, whose first
act is ``provider.route(waypoints)``.

Re-routing at compile time would be a machine editing an approved plan after approval: the
distances and durations the feasibility gate checked and the user approved (ADR-0023's
compare-and-set is over the itinerary hash) would be replaced by whatever the engine's data
says today, while ``content/itinerary.json`` still carries the approved ones — two artifacts,
two hashes, one day, disagreeing. It would also make a legitimate **one-stop day
uncompilable**, since routing needs a pair of waypoints and refuses fewer
(``commons.routing._require_pair``). The rest of stage four *is* composed:
:func:`~compiler.routes.build_walk_graph` nodes and prunes the recovery network and
:func:`~compiler.routes.require_connected` refuses a disconnected one.

**``connected: false`` fails the compile**, and the truthful frame is yielded *first*: the
operator sees why, and the traveller never receives a bundle whose recovery network is a set
of islands (plan.md risk 3).

## Publishing is one barrier: the manifest, last and alone

A mid-stream failure must leave **no partial bundle published** (`contracts/bundles.md`).
Two mechanisms, and both are needed:

* **Order.** Every artifact is written first and ``manifest.json`` last. The manifest *is* the
  index — "a path absent from the manifest is a ``404`` **by construction**" — so a bundle
  whose manifest never landed is not a half-bundle, it is an unreadable one: the travel UI
  cannot open it, and the API's manifest route answers ``404``.
* **Removal.** A failure anywhere in the publish still deletes the objects
  (:meth:`BundleObjectStore.delete_bundle`) before the error propagates, so a retry does not
  inherit a stale object at a path the new manifest also names. If the *deletion* fails too,
  the ordering above is the fallback and is why it exists rather than being an optimisation.

The error itself is **raised, not yielded**: the transport turns it into the in-band
``event: error`` frame, the way ``api/areas.py`` already does for the research stream. A
pipeline that yielded its own error frame would be a second implementation of that mapping.

## The frames carry counts, ids and enum values — never commons text (ADR-0030 A1)

A server-computed frame must not embed commons-derived text, so nothing here composes prose
from an OSM tag or any bundled value. What the frames *do* carry is: numbers; a bbox and a
zoom (computed by shapely in `compiler/tiles.py`, never by a model); ``withheld`` rows, whose
``site_id`` is our own UUID, whose ``field`` is a dotted **schema** path and whose ``reason``
is the closed :data:`~commons.models.WithheldReason` enum; and SPDX license ids read straight
off the generated ``ATTRIBUTION.md`` (:func:`_declared_licenses`) rather than re-derived from
the stamps — a second derivation can disagree with the artifact, and then the frame is a claim
about a file rather than a description of it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Literal, Protocol, runtime_checkable
from uuid import UUID

import rfc8785

from commons.frame import LocalFrame
from commons.licenses import BUNDLEABLE_LICENSES
from commons.models import BundleManifestV1, ItineraryV1, SiteRecordV1, WithheldValue
from commons.routing import Waypoint
from compiler.attribution import (
    ATTRIBUTION_PATH,
    BundleSources,
    Notice,
    attribution_bytes,
    bundled_text_license,
)
from compiler.manifest import Artifact, build_manifest, freeze_itinerary, manifest_json_bytes
from compiler.quarantine import quarantine_legs, quarantine_sites
from compiler.routes import (
    LEGS_PATH,
    WALK_GRAPH_PATH,
    WalkNetworkSource,
    build_walk_graph,
    legs_bytes,
    require_connected,
)
from compiler.tiles import (
    DEFAULT_ARCHIVE_NAME,
    MAXZOOM,
    AssetSource,
    ProtomapsBuild,
    TileExtractor,
    compile_tiles,
    itinerary_bbox,
)

__all__ = [
    "BUDGET_BYTES",
    "COMPILE_IN_PROCESS_ENV",
    "COMPILE_PHASES",
    "MANIFEST_PATH",
    "NARRATIONS_PATH",
    "SITES_PATH",
    "BundleObjectStore",
    "CompileError",
    "CompileRequest",
    "Event",
    "MissingArtifactError",
    "UnbundleableStopError",
    "UnknownSiteError",
    "in_process_compile_enabled",
    "run_compile",
]

_log = logging.getLogger(__name__)

EventName = Literal["status", "manifest", "done"]

#: The stage sequence `contracts/bundles.md` fixes, and the order the ``status`` frames come
#: in. Exported so the API's contract test and the trajectory eval assert the sequence against
#: one tuple rather than a list re-typed in each of them.
COMPILE_PHASES: Final[tuple[str, ...]] = (
    "tiles",
    "routes",
    "quarantine",
    "content",
    "attribution",
    "hash",
)

#: Bundle-relative paths this stage produces. The two ``content`` artifacts are named here
#: because this is the module that writes their bytes (`compiler/routes.py` does the same for
#: its two); the manifest records whatever path it is handed. The card's example layout is
#: what these are set to (`docs/data/bundle-manifest.md`).
SITES_PATH: Final = "content/sites.json"
NARRATIONS_PATH: Final = "content/narrations.json"
#: Where the manifest lands inside the bundle root — ``web/src/bundle/open.ts::MANIFEST_PATH``,
#: which is the reader that has to find it.
MANIFEST_PATH: Final = "manifest.json"

#: The ≤200 MB download budget (SC-007), in bytes, as `contracts/bundles.md` writes it in the
#: ``done`` frame. A budget, not a limit: an over-budget day still compiles and still reports
#: its size — the manifest has no over-budget field, so the verdict rides on that frame.
BUDGET_BYTES: Final = 200 * 1024 * 1024

#: The tech-design §5.3 flag: M1 compiles **in-process**, M2 moves the same generator to a
#: Cloud Run Job. Read by the transport before it starts a stream, so a deployment that has
#: turned the in-process path off answers honestly instead of compiling in a web worker.
COMPILE_IN_PROCESS_ENV: Final = "SIYUR_COMPILE_IN_PROCESS"

#: The strings :func:`in_process_compile_enabled` reads as true, matching ``api/config.py``'s
#: vocabulary so one deployment does not have two spellings of "on".
_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


class CompileError(RuntimeError):
    """The bundle cannot be produced. Never caught to continue — nothing is published."""


class UnknownSiteError(CompileError):
    """A stop references a record the compiler was not given; the day cannot be frozen."""


class UnbundleableStopError(CompileError):
    """Quarantine withheld a stop's whole site — its ``location`` may not be redistributed.

    Refused rather than compiled around, because the alternatives are both worse than a failed
    build: dropping the stop ships a *different day* from the approved one, and keeping it
    ships a timeline entry the traveller can tap and the bundle cannot draw, offline.
    """


class MissingArtifactError(CompileError):
    """A hashed artifact has no bytes in the staging tree, or its bytes changed after hashing.

    Both mean the same thing on the device: the manifest names a path whose recorded SHA-256
    belongs to something else, so the bundle fails its own launch-time integrity check with no
    way to diagnose it (FR-013/FR-020). Cheaper to refuse here.
    """


@dataclass(frozen=True, slots=True)
class Event:
    """One SSE frame: the ``event:`` name plus its JSON-serialisable ``data:`` payload.

    The same shape as :class:`planner.pipeline.Event` and deliberately **not imported from
    it**: ``compiler/`` does not depend on ``planner/`` (they are sibling packages behind one
    API), and a shared four-line dataclass is not worth a package edge that would let a
    planner import creep into a compile.
    """

    event: EventName
    data: Mapping[str, Any]


@runtime_checkable
class BundleObjectStore(Protocol):
    """Where a compiled bundle's objects go. :class:`compiler.storage.BundleStore` satisfies it.

    Two methods, because the compile needs exactly two things from a store: write an object,
    and remove everything under a bundle when the pass fails (see the module docstring on the
    publish barrier). Reading is the API's half of the contract and is not needed here.
    """

    def put(self, bundle_id: str, path: str, data: bytes) -> object: ...

    def delete_bundle(self, bundle_id: str) -> int: ...


@dataclass(frozen=True, slots=True)
class CompileRequest:
    """What is being compiled, and the frame the compiled day is read in.

    ``itinerary`` is the **approved** day, read off the ``user_plan`` row — this pipeline does
    not check that it was approved and must not be asked to: the gate is mechanical, and it is
    the API's ``claim_plan_for_compile`` (``approved → compiling``) that enforces it by
    claiming nothing when the plan is not approved (FR-006/SC-003).
    """

    #: Stable id of this bundle; also the object-store prefix every artifact lands under.
    bundle_id: str
    itinerary: ItineraryV1
    #: The area row's ``timezone``/``country_code``, frozen into ``content.itinerary`` so the
    #: day's wall-clock times mean something offline (T040a, `compiler/manifest.py`).
    frame: LocalFrame
    #: Archive stem — a name, never a place baked in (the tile stage's generic default).
    archive_name: str = DEFAULT_ARCHIVE_NAME
    #: The documented size lever (`compiler/tiles.py`): each zoom is roughly 2× the archive,
    #: and a missing zoom level degrades where a narrower glyph set disappears.
    maxzoom: int = MAXZOOM
    budget_bytes: int = BUDGET_BYTES


def in_process_compile_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Is the M1 in-process compile path enabled? Unset means **yes** (tech-design §5.3).

    Default-on because M1 has no other path: the Cloud Run Job is M2, so a default-off flag
    would mean an unset variable disables the only implementation there is. The flag exists so
    that a deployment can turn the in-process compile *off* the moment the job exists, without
    a code change and without the two racing.
    """
    raw = (os.environ if env is None else env).get(COMPILE_IN_PROCESS_ENV)
    return True if raw is None else raw.strip().lower() in _TRUTHY


def run_compile(
    request: CompileRequest,
    *,
    sites: Sequence[SiteRecordV1],
    walk_network: WalkNetworkSource,
    store: BundleObjectStore,
    staging: Path,
    style: Artifact,
    build: ProtomapsBuild | None = None,
    extractor: TileExtractor | None = None,
    assets: AssetSource | None = None,
    notices: Sequence[Notice] = (),
    created_at: datetime | None = None,
) -> Iterator[Event]:
    """Compile one approved day into an offline bundle, yielding progress as it happens.

    Frames, in order (`contracts/bundles.md`): ``status`` ×6 in :data:`COMPILE_PHASES` order →
    ``manifest`` → ``done``. The stream is the stages as they happen, not a list assembled and
    replayed.

    **The bundle is published before the frame that claims it**, the way
    :func:`planner.pipeline.run_plan` writes its row before streaming it: the ``manifest``
    frame is emitted after every object — the manifest included — has reached the store, so
    the client is never handed a manifest for a bundle a failed write silently dropped.

    Args:
        sites: the candidate records, typically ``commons.repository.sites_within`` for the
            area. The bundle freezes exactly the records the day's **stops** reference, in
            first-visit order; the rest of the pool is not bundled, because the tiles, the
            walk graph and the content are all scoped to the day (FR-016) and an area-wide
            content dump would blow the download budget on places the day never visits.
        walk_network: the raw walking linework for the tile bbox — injected, so Tier 1 needs
            neither Overpass nor OSMnx (`compiler/routes.py`).
        staging: the filesystem directory the bundle is assembled in. Bundle-relative paths
            are mirrored under it, and every hashed file the tile stage wrote is read back
            from here at publish time.
        style: the base MapLibre style artifact, already written into ``staging`` at its
            bundle-relative path and hashed. Generated by the style stage, not here.
        build, extractor, assets: the tile stage's three seams. Unset means the live resolver,
            the ``pmtiles`` CLI and the Protomaps asset host — never a fixture.
        notices: verbatim NOTICE texts an Apache-2.0/MIT dependency ships, passed through to
            ``ATTRIBUTION.md`` (they live in the dependency, so nothing here can derive them).
        created_at: UTC; defaults to now. Explicit for reproducible builds and tests.

    Raises:
        QuarantineError: unstamped input (FR-012) or a route leg the licence registry refuses.
            Deliberately not caught: a value is withheld **or** refused, never both, and there
            is no ``unstamped`` member of the withheld enum to degrade it into.
        UnknownSiteError / UnbundleableStopError: the day cannot be represented by the records
            it was given. Both raise before anything is written.
        DisconnectedWalkGraphError: the pruned network cannot serve the day. The truthful
            ``connected: false`` frame is yielded first.
        TileError / RouteCompileError / ObjectStoreError: a stage failed. Whatever had been
            written is removed and no manifest is published (module docstring).
    """
    itinerary = request.itinerary
    # Stage five, run first — see the module docstring. Nothing is written until it has ruled.
    kept, withheld = _quarantined_day(itinerary, sites)
    legs = quarantine_legs(itinerary.legs)

    bbox = itinerary_bbox(itinerary, kept)
    tiles = compile_tiles(
        staging=staging,
        bbox=bbox,
        style=style,
        sites=kept,
        name=request.archive_name,
        maxzoom=request.maxzoom,
        build=build,
        extractor=extractor,
        assets=assets,
    )
    yield Event(
        "status",
        {
            "phase": "tiles",
            "bbox": list(bbox),
            "maxzoom": tiles.source.maxzoom,
            "bytes": tiles.size_bytes,
        },
    )

    graph = build_walk_graph(walk_network.walk_lines(bbox), anchors=_anchors(kept), clip_bbox=bbox)
    routes_frame: dict[str, Any] = {
        "phase": "routes",
        "legs": len(legs),
        "walk_graph_edges": graph.edge_count,
        "connected": graph.connected,
        # The evidence, not only the verdict. `WalkGraph` has carried these since T036, but
        # only `connected` reached the stream, so a compile that failed on connectivity told
        # an operator *that* it failed and nothing about why — and diagnosing it meant reading
        # a traceback or reproducing the fetch by hand. Counts and integers only, so this
        # stays inside ADR-0030 A1 (no commons-derived text in a server-computed frame).
        "component_count": graph.component_count,
        "anchor_components": list(graph.anchor_components),
        "dropped_edges": graph.dropped_edges,
    }
    # The verdict is streamed *before* it is enforced: an operator watching a compile fail
    # should be told which of the two facts failed it, not left to infer it from a traceback.
    yield Event("status", routes_frame)
    require_connected(graph, frame=routes_frame)

    legs_data = legs_bytes(legs)
    graph_data = graph.to_bytes()
    legs_artifact = Artifact.from_bytes(LEGS_PATH, legs_data)
    graph_artifact = Artifact.from_bytes(WALK_GRAPH_PATH, graph_data)

    yield Event(
        "status",
        {
            "phase": "quarantine",
            "values_dropped": len(withheld),
            "withheld": [_withheld_row(entry) for entry in withheld],
        },
    )

    sites_data = rfc8785.dumps({"sites": [_bundled_site(site) for site in kept]})
    narrations_data = rfc8785.dumps(_narrations(kept))
    frozen = freeze_itinerary(itinerary, request.frame)
    sites_artifact = Artifact.from_bytes(SITES_PATH, sites_data)
    narrations_artifact = Artifact.from_bytes(NARRATIONS_PATH, narrations_data)
    yield Event(
        "status",
        {
            "phase": "content",
            "sites": len(kept),
            "narrations": sum(len(site.stories) for site in kept),
        },
    )

    attribution_data = attribution_bytes(
        BundleSources(
            tiles=tiles.source,
            walk_graph_source=graph.source,
            legs=legs,
            sites=kept,
            notices=notices,
        )
    )
    attribution_artifact = Artifact.from_bytes(ATTRIBUTION_PATH, attribution_data)
    yield Event(
        "status", {"phase": "attribution", "licenses": _declared_licenses(attribution_data)}
    )

    manifest = build_manifest(
        bundle_id=request.bundle_id,
        itinerary=frozen,
        tiles=tiles.source,
        tiles_size_bytes=tiles.size_bytes,
        walk_graph=graph_artifact,
        legs=legs_artifact,
        sites=sites_artifact,
        narrations=narrations_artifact,
        attribution=attribution_artifact,
        text_license=bundled_text_license(kept),
        withheld=withheld,
        created_at=created_at,
    )
    yield Event("status", {"phase": "hash", "artifacts": len(_artifact_digests(manifest))})

    _publish(
        store,
        request.bundle_id,
        staging=staging,
        staged=tiles.artifacts,
        inline=(
            (graph_artifact, graph_data),
            (legs_artifact, legs_data),
            (sites_artifact, sites_data),
            (narrations_artifact, narrations_data),
            (frozen.artifact, frozen.data),
            (attribution_artifact, attribution_data),
        ),
        manifest_data=manifest_json_bytes(manifest),
    )

    yield Event("manifest", manifest.model_dump(mode="json"))
    yield Event(
        "done",
        {
            "bundle_id": manifest.bundle_id,
            "size_bytes": manifest.size_bytes,
            "budget_bytes": request.budget_bytes,
            "over_budget": manifest.size_bytes > request.budget_bytes,
        },
    )


# --- the day, filtered --------------------------------------------------------------------


def _quarantined_day(
    itinerary: ItineraryV1, records: Sequence[SiteRecordV1]
) -> tuple[tuple[SiteRecordV1, ...], tuple[WithheldValue, ...]]:
    """The day's own records, post-quarantine, plus the removal log the manifest freezes.

    Deduplicated by ``site_id`` in first-visit order: a day that visits one place twice has
    two stops and one record (ADR-0025 ruling 4), and bundling the record twice would put two
    copies of every value behind one hash.
    """
    by_id = {record.id: record for record in records}
    visited = tuple(dict.fromkeys(stop.site_id for stop in itinerary.stops))
    missing = [site_id for site_id in visited if site_id not in by_id]
    if missing:
        raise UnknownSiteError(
            f"{len(missing)} stop(s) reference a site the compiler was not given "
            f"({_ids(missing)}); the bundle cannot freeze a day it has no record for"
        )

    result = quarantine_sites(by_id[site_id] for site_id in visited)
    kept_ids = {record.id for record in result.sites}
    refused = [site_id for site_id in visited if site_id not in kept_ids]
    if refused:
        raise UnbundleableStopError(
            f"{len(refused)} stop(s) sit at a site whose location may not be redistributed "
            f"({_ids(refused)}), so the day cannot be bundled as planned. Re-plan without "
            "them rather than shipping a day with an undrawable stop"
        )
    return result.sites, result.withheld


def _anchors(sites: Sequence[SiteRecordV1]) -> tuple[Waypoint, ...]:
    """The stops the walk graph's connectivity is judged against — the day's own places.

    Coordinates are read off the records' stamped ``location``; nothing here computes or
    invents one (AGENTS.md geo rules).
    """
    return tuple(Waypoint(lon=site.location.value.x, lat=site.location.value.y) for site in sites)


def _bundled_site(site: SiteRecordV1) -> dict[str, Any]:
    """One record as ``content/sites.json`` carries it: the record **without its stories**.

    The card calls this artifact a "``SiteRecordV1`` subset", and the subset that matters is
    this one: the stories are frozen into ``content/narrations.json``, which is the artifact
    ``textLicense`` declares the terms of and ``content.narrations_sha256`` covers. Leaving
    them in both would ship every word of adapted CC BY-SA prose twice, under two hashes that
    can disagree — and the travel reader takes its narration from the narrations map anyway
    (``web/src/travel/index.ts``), so the second copy is bytes on a phone that nothing reads.
    """
    return site.model_copy(update={"stories": ()}).model_dump(mode="json")


def _narrations(sites: Sequence[SiteRecordV1]) -> dict[str, list[dict[str, Any]]]:
    """``content/narrations.json`` — ``{"<site_id>": [Story, …]}``, sites with no story absent.

    The map form of the two shapes ``web/src/travel/parse.ts::parseNarrations`` accepts, chosen
    because the reader's own model is a map keyed by ``site_id``: the row form would carry the
    same id one level down and let the two disagree. JCS sorts the keys, so the artifact's
    bytes are a function of the day and not of dict insertion order.
    """
    return {
        str(site.id): [story.model_dump(mode="json") for story in site.stories]
        for site in sites
        if site.stories
    }


def _withheld_row(entry: WithheldValue) -> dict[str, Any]:
    """One ``withheld[]`` row, exactly as the contract writes it — ids and an enum, no prose."""
    return {"site_id": str(entry.site_id), "field": entry.field, "reason": entry.reason}


def _declared_licenses(rendered: bytes) -> list[str]:
    """The licences ``ATTRIBUTION.md`` actually declares, in the order that file declares them.

    Read off the generated artifact rather than re-derived from the bundle's stamps: a second
    derivation is a second implementation of ``compiler.attribution._credits``, and the day the
    two disagree the frame is reporting a bundle that does not exist. Matching is exact against
    the closed allowlist (:data:`~commons.licenses.BUNDLEABLE_LICENSES`) and the generator's own
    ``## <spdx> — <title>`` heading, so the prose sections (``Bundled text license``,
    ``Notices``) are skipped without a pattern that could catch arbitrary text.
    """
    found: list[str] = []
    for line in rendered.decode("utf-8").splitlines():
        if not line.startswith("## "):
            continue
        heading = line.removeprefix("## ").split(" — ", 1)[0]
        if heading in BUNDLEABLE_LICENSES and heading not in found:
            found.append(heading)
    return found


def _artifact_digests(manifest: BundleManifestV1) -> tuple[str, ...]:
    """The per-artifact hashes the ``hash`` frame counts — the card's **seven**, exactly.

    Named rather than counted from a constant, so the frame reports the manifest that was
    actually built. ``tiles.pmtiles.style.sha256`` is not among them and that is the card's
    own arithmetic: the style's hash lives *inside* the embedded ``TileSourceV1``, and the
    integrity list is the seven top-level artifact hashes a device re-checks path by path.
    """
    return (
        manifest.tiles.pmtiles.sha256,
        manifest.routing.walk_graph_sha256,
        manifest.routing.legs_sha256,
        manifest.content.sites_sha256,
        manifest.content.narrations_sha256,
        manifest.content.itinerary_sha256,
        manifest.attribution.sha256,
    )


def _ids(values: Sequence[UUID]) -> str:
    return ", ".join(sorted(str(value) for value in values))


# --- publishing ---------------------------------------------------------------------------


def _publish(
    store: BundleObjectStore,
    bundle_id: str,
    *,
    staging: Path,
    staged: Sequence[Artifact],
    inline: Sequence[tuple[Artifact, bytes]],
    manifest_data: bytes,
) -> None:
    """Write every object, the manifest last — or write nothing that can be read.

    See the module docstring: the ordering is what makes a failed publish unreadable rather
    than partial, and the removal is what stops a retry inheriting a stale object at a path
    the new manifest also names.

    Each staged file is read whole before it is put, because the store's write primitive takes
    bytes (`compiler/storage.py`: upload is one ``POST``). That peaks at the size of the
    PMTiles archive — measured at ~1.3 MB for ADR-0021's extract, against a ≤200 MB budget in
    the worst case. A streaming upload is a change to the *store*, not to this loop, and is
    not worth making before a real archive makes it necessary.
    """
    try:
        for artifact in staged:
            store.put(bundle_id, artifact.path, _staged_bytes(staging, artifact))
        for artifact, data in inline:
            store.put(bundle_id, artifact.path, data)
        # Last, and after everything it indexes: publishing *is* this call.
        store.put(bundle_id, MANIFEST_PATH, manifest_data)
    except Exception:
        _unpublish(store, bundle_id)
        raise


def _unpublish(store: BundleObjectStore, bundle_id: str) -> None:
    """Remove a failed compile's objects. A failure here is logged, never raised.

    Raising would replace the error that actually failed the compile with the error that
    failed the cleanup, and the operator would debug the wrong one. The bundle is unpublished
    either way — no manifest was written, and a path the manifest does not name is a ``404``
    by construction.
    """
    try:
        removed = store.delete_bundle(bundle_id)
    except Exception:
        _log.exception(
            "could not remove the objects of failed bundle %s; no manifest was published, so "
            "the bundle is unreadable, but the objects are orphaned until they are swept",
            bundle_id,
        )
        return
    if removed:
        _log.info("removed %d object(s) of failed bundle %s", removed, bundle_id)


def _staged_bytes(staging: Path, artifact: Artifact) -> bytes:
    """Read a hashed artifact back out of the staging tree, refusing anything else.

    The tile stage writes its files to disk and hands back their hashes; this is where those
    bytes are picked up again. Both failure modes are the same failure on the device — a
    manifest naming a path whose SHA-256 belongs to other bytes — so both raise here rather
    than shipping a bundle that fails its own launch check offline.
    """
    source = staging / artifact.path
    if not source.is_file():
        raise MissingArtifactError(
            f"the manifest records {artifact.path!r} but the staging tree has no file at "
            f"{source}. Every hashed artifact must be written under the staging root at its "
            "bundle-relative path — including the base style, which the tile stage hashes but "
            "does not write"
        )
    data = source.read_bytes()
    if len(data) != artifact.size_bytes:
        raise MissingArtifactError(
            f"{artifact.path!r} is {len(data)} bytes on disk but was hashed at "
            f"{artifact.size_bytes}: it changed after the digest was taken, so the bundle "
            "would fail its own integrity check offline"
        )
    return data
