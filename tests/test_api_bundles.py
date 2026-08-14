"""T047 — the `api/bundles.py` contract, over HTTP (`contracts/bundles.md`).

`test_compiler_pipeline.py` already pins the *generator's* frames offline, and
`test_compiler_storage.py` pins the store's `206`/`416` wire behaviour. What is pinned here is
everything the transport adds and could get wrong: the status-code matrix, the HITL gate as a
mechanical `409`, `size_bytes` readable before an artifact byte moves, range semantics through
the endpoint, and the two `404`s that must be **indistinguishable**.

Tier 2, against real PostGIS: every guarantee under test is a row's, and the claim/release
lifecycle is exactly the part a mocked session cannot speak for. The object store is an
in-memory double whose `get` delegates to `ByteRange.resolve` — the same primitive
`compiler/storage.py`'s own fallback path uses — so the range arithmetic under test is the real
one; the *wire* agreement with GCS is `test_compiler_storage.py`'s job, and the emulator case at
the bottom of this file re-checks the whole stack against it when one is running.

**Every negative case gets a positive control first.** The contract requires another user's
bundle to be `404` *byte-identical* to an unknown id, which means a privacy assertion written
carelessly compares two indistinguishable things and calls the result a proof — including when
the route does not exist at all. So each `404` test first asserts that the owner gets real
content at that exact URL, and only then that the stranger does not. Per FAIL-007 the same rule
applies to every guard here: each is shown to bite on deliberately bad input.

The compile seams (tiles, assets, walking network, style) are the committed fixtures, so no test
here reaches Overpass, Protomaps, the `pmtiles` binary or real GCS. Rhodes is the fixtures'
geography and never a default — the area is delimited per test (FR-001).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape
from shapely.geometry import box
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from api.bundles import BundleObjects, CompileSeams, bundle_id_for
from api.bundles import router as bundles_router
from commons.db import SRID, Area, Site, UserPlan, to_db_point
from commons.models import BundleManifestV1, SiteRecordV1
from commons.repository import (
    PlanRecord,
    approve_plan,
    claim_plan_for_compile,
    create_plan,
    load_plan,
    record_feasibility,
)
from compiler.pipeline import COMPILE_PHASES, MANIFEST_PATH
from compiler.storage import (
    DEFAULT_BUCKET,
    ENV_EMULATOR_HOST,
    BundleStore,
    ByteRange,
    ObjectNotFound,
    ObjectRead,
    ObjectStoreError,
    content_type_for,
    object_name,
)
from compiler.tiles import FixtureExtractor
from tests.test_api_areas import build_app, parse_sse, signed_in_client
from tests.test_compiler_pipeline import (
    BUILD,
    CONNECTED_LINES,
    PALACE,
    STREET,
    STYLE_BYTES,
    STYLE_PATH,
    _Assets,
    _itinerary,
    _Network,
    _palace,
    _street,
)

pytestmark = pytest.mark.integration

SUBJECT = "google-sub-a"
STRANGER = "google-sub-b"

#: Contains both fixture stops strictly (``ST_Within``), and nothing else. Generic box, stated
#: once: every bbox, distance and extent below is computed from the records by shapely.
AREA_POLYGON = box(28.220, 36.440, 28.230, 36.446)
FRAME = ("Europe/Athens", "GR")

#: Deliberately over 1 KiB, because the contract's range case is ``bytes=0-1023`` and an archive
#: smaller than that would clamp to the whole object and prove nothing about partial reads.
ARCHIVE_BYTES = b"PMTiles\x03" + bytes(range(256)) * 20


# ── the object store double ───────────────────────────────────────────────────────


@dataclass
class _MemoryStore:
    """A :class:`~api.bundles.BundleObjects` in a dict — writes, reads, and counts its reads.

    ``get`` resolves ranges through :meth:`~compiler.storage.ByteRange.resolve`, which is the
    same call the real store makes when an origin ignores ``Range``, so ``206``/``416`` here are
    decided by production arithmetic rather than by a second implementation of it. Keys are built
    with :func:`~compiler.storage.object_name`, so an unsafe path fails here exactly as it would
    against GCS.
    """

    objects: dict[str, bytes] = field(default_factory=dict)
    #: Every ``(bundle_id, path)`` read attempt — the evidence for "without touching the store".
    reads: list[tuple[str, str]] = field(default_factory=list)
    fail_on: str | None = None

    def put(self, bundle_id: str, path: str, data: bytes) -> object:
        if path == self.fail_on:
            raise RuntimeError(f"object store refused {path}")
        self.objects[object_name(bundle_id, path)] = data
        return None

    def delete_bundle(self, bundle_id: str) -> int:
        prefix = f"{bundle_id}/"
        removed = [name for name in self.objects if name.startswith(prefix)]
        for name in removed:
            del self.objects[name]
        return len(removed)

    def get(self, bundle_id: str, path: str, byte_range: ByteRange | None = None) -> ObjectRead:
        self.reads.append((bundle_id, path))
        data = self.objects.get(object_name(bundle_id, path))
        if data is None:
            raise ObjectNotFound(f"no such object: {bundle_id}/{path}")
        media_type = content_type_for(path)
        if byte_range is None:
            return ObjectRead(path, data, len(data), media_type, None)
        first, last = byte_range.resolve(len(data))  # raises RangeNotSatisfiable
        return ObjectRead(path, data[first : last + 1], len(data), media_type, (first, last))

    def paths(self, bundle_id: str) -> set[str]:
        prefix = f"{bundle_id}/"
        return {name.removeprefix(prefix) for name in self.objects if name.startswith(prefix)}


def test_the_real_store_satisfies_the_read_seam_this_module_declares() -> None:
    """The double is only honest if the production store fits the same Protocol.

    Structural, so nothing declares an inheritance the packages do not have: `compiler/` never
    imports `api/`.
    """
    assert isinstance(BundleStore(), BundleObjects)


# ── fixtures: an area, a plan, and the compile seams ──────────────────────────────


@pytest.fixture
def store() -> _MemoryStore:
    return _MemoryStore()


@pytest.fixture
def seams(tmp_path: Path) -> CompileSeams:
    """Every seam the compile would otherwise take from the network or a CLI."""
    planet = tmp_path / "planet.pmtiles"
    planet.write_bytes(ARCHIVE_BYTES)
    return CompileSeams(
        style_path=STYLE_PATH,
        style_bytes=STYLE_BYTES,
        walk_network=_Network(CONNECTED_LINES),
        build=BUILD,
        extractor=FixtureExtractor(archive=planet),
        assets=_Assets(),
    )


def build_bundles_app(**state: Any) -> FastAPI:
    """The real app with the bundles router mounted.

    `api/bundles.py` is a self-contained ``APIRouter``; its one-line ``include_router`` in
    `api/app.py` lands in a separate change, because that file is contested with another session
    whose PR is in flight. The guard makes this helper correct on both sides of that change —
    including the same router twice would shadow every route with a duplicate.
    """
    app = build_app(**state)
    if not any(getattr(route, "path", "").startswith("/bundles") for route in app.routes):
        app.include_router(bundles_router)
    return app


@pytest.fixture
def app(
    db_engine: Engine, db_session: Session, store: _MemoryStore, seams: CompileSeams
) -> FastAPI:
    return build_bundles_app(engine=db_engine, bundle_store=store, compile_seams=seams)


@pytest.fixture
def area(db_session: Session) -> Area:
    """A delimited, frame-carrying area — the clock the bundled day is read in (T040a)."""
    timezone, country_code = FRAME
    row = Area(
        polygon=from_shape(AREA_POLYGON, srid=SRID),
        created_by=SUBJECT,
        timezone=timezone,
        country_code=country_code,
    )
    db_session.add(row)
    db_session.flush()
    return row


@pytest.fixture
def plan(db_session: Session, area: Area) -> PlanRecord:
    """The day, in the pause: two commons records inside the area and a ``proposed`` plan."""
    for record in (_palace(), _street()):
        db_session.add(
            Site(
                id=record.id,
                geom=to_db_point(record.location.value),
                fields=record.model_dump(mode="json"),
                updated_at=record.updated_at,
            )
        )
    db_session.flush()
    itinerary = _itinerary(id=str(uuid4()), user_id=SUBJECT, area_id=str(area.id))
    created = create_plan(db_session, itinerary)
    checked = record_feasibility(
        db_session, created.id, user_id=SUBJECT, feasible=True, violations=[]
    )
    assert checked is not None
    db_session.commit()
    return checked


def approve(session: Session, plan_id: UUID) -> None:
    approve_plan(session, plan_id, user_id=SUBJECT, approved_by=SUBJECT)
    session.commit()


def compile_bundle(client: TestClient, plan_id: UUID) -> list[tuple[str, dict[str, Any]]]:
    response = client.post("/bundles", json={"plan_id": str(plan_id)})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


def frame_named(frames: list[tuple[str, dict[str, Any]]], event: str) -> dict[str, Any]:
    return next(payload for name, payload in frames if name == event)


def status_of(session: Session, plan_id: UUID) -> str:
    return session.execute(select(UserPlan.status).where(UserPlan.id == plan_id)).scalar_one()


@pytest.fixture
def compiled(
    app: FastAPI, db_session: Session, plan: PlanRecord
) -> tuple[TestClient, str, BundleManifestV1]:
    """One approved plan, compiled end to end through HTTP. The starting point for the reads."""
    approve(db_session, plan.id)
    client = signed_in_client(app, SUBJECT)
    frames = compile_bundle(client, plan.id)
    manifest = BundleManifestV1.model_validate(frame_named(frames, "manifest"))
    return client, bundle_id_for(plan.id), manifest


def artifact_paths(manifest: BundleManifestV1) -> dict[str, str]:
    """The manifest's path→hash table, restated so the endpoint's is *checked*, not reused."""
    tiles = manifest.tiles.pmtiles
    return {
        tiles.path: tiles.sha256,
        tiles.style.path: tiles.style.sha256,
        manifest.routing.walk_graph: manifest.routing.walk_graph_sha256,
        manifest.routing.legs: manifest.routing.legs_sha256,
        manifest.content.sites: manifest.content.sites_sha256,
        manifest.content.narrations: manifest.content.narrations_sha256,
        manifest.content.itinerary: manifest.content.itinerary_sha256,
        manifest.attribution.path: manifest.attribution.sha256,
    }


# ── auth: 401 before anything else ────────────────────────────────────────────────


def test_every_route_is_401_before_it_looks_at_anything(app: FastAPI, plan: PlanRecord) -> None:
    """Auth is the first dependency, so an anonymous caller never reaches storage or a row."""
    anonymous = TestClient(app)
    bundle_id = bundle_id_for(plan.id)

    assert anonymous.post("/bundles", json={"plan_id": str(plan.id)}).status_code == 401
    assert anonymous.get(f"/bundles/{bundle_id}/manifest").status_code == 401
    assert anonymous.get(f"/bundles/{bundle_id}/artifacts/content/sites.json").status_code == 401


# ── the HITL gate, made mechanical ────────────────────────────────────────────────


def test_compiling_an_unapproved_plan_is_409_and_nothing_moves(
    app: FastAPI, db_session: Session, store: _MemoryStore, plan: PlanRecord
) -> None:
    """FR-006 / SC-003: there is no flag, no override and no path that compiles a `proposed` plan.

    The refusal is the *claim* matching zero rows, so the assertions are on the row and the
    bucket as well as on the code: an endpoint that answered `409` after starting work would
    pass a status-code-only test.
    """
    client = signed_in_client(app, SUBJECT)
    response = client.post("/bundles", json={"plan_id": str(plan.id)})

    assert response.status_code == 409
    assert response.json()["detail"] == {"error": "plan_not_approved", "state": "proposed"}
    assert status_of(db_session, plan.id) == "proposed"
    assert store.objects == {}


def test_a_compile_already_running_is_409(
    app: FastAPI, db_session: Session, plan: PlanRecord
) -> None:
    """The idempotency guard. Unlike `api/areas.py`'s in-process set, this one is a row — so it
    holds across processes and across a restart, which is what a multi-minute compile needs."""
    approve(db_session, plan.id)
    assert claim_plan_for_compile(db_session, plan.id, user_id=SUBJECT) is not None
    db_session.commit()

    client = signed_in_client(app, SUBJECT)
    response = client.post("/bundles", json={"plan_id": str(plan.id)})

    assert response.status_code == 409
    assert response.json()["detail"] == {"error": "compile_in_progress", "state": "compiling"}


def test_compiling_an_unknown_or_foreign_plan_is_404_never_403(
    app: FastAPI, db_session: Session, plan: PlanRecord
) -> None:
    """Positive control first: the owner compiles this very plan id, then the stranger cannot."""
    approve(db_session, plan.id)
    owner = signed_in_client(app, SUBJECT)
    assert compile_bundle(owner, plan.id)  # the id is real and compilable — the control

    stranger = signed_in_client(app, STRANGER)
    foreign = stranger.post("/bundles", json={"plan_id": str(plan.id)})
    unknown = stranger.post("/bundles", json={"plan_id": str(uuid4())})

    assert foreign.status_code == unknown.status_code == 404
    assert foreign.content == unknown.content, (
        "a foreign plan is distinguishable from a missing one"
    )


# ── the stream ────────────────────────────────────────────────────────────────────


def test_an_approved_plan_streams_the_ordered_stages_a_manifest_and_a_done(
    app: FastAPI, db_session: Session, store: _MemoryStore, plan: PlanRecord
) -> None:
    """The §5.3 stage order survives SSE framing, and the row ends `compiled`."""
    approve(db_session, plan.id)
    client = signed_in_client(app, SUBJECT)
    frames = compile_bundle(client, plan.id)

    names = [name for name, _ in frames]
    assert names == ["status"] * len(COMPILE_PHASES) + ["manifest", "done"]
    assert tuple(p["phase"] for n, p in frames if n == "status") == COMPILE_PHASES
    assert "error" not in names

    done = frame_named(frames, "done")
    manifest = BundleManifestV1.model_validate(frame_named(frames, "manifest"))
    assert done["bundle_id"] == manifest.bundle_id == bundle_id_for(plan.id)
    assert done["size_bytes"] == manifest.size_bytes
    assert done["over_budget"] is False

    assert MANIFEST_PATH in store.paths(manifest.bundle_id)
    assert status_of(db_session, plan.id) == "compiled"


def test_the_frozen_day_carries_the_areas_clock_and_calendar(
    compiled: tuple[TestClient, str, BundleManifestV1],
) -> None:
    """T040a: offline, the device clock is the only clock, so the frame travels with the day."""
    client, bundle_id, manifest = compiled
    frozen = client.get(f"/bundles/{bundle_id}/artifacts/{manifest.content.itinerary}")

    assert frozen.status_code == 200
    day = frozen.json()
    assert (day["timezone"], day["country_code"]) == FRAME


def test_a_mid_stream_failure_is_in_band_and_publishes_nothing(
    app: FastAPI, db_session: Session, store: _MemoryStore, plan: PlanRecord
) -> None:
    """A failed publish leaves no readable bundle, and the claim is released rather than stuck.

    The store refuses the manifest — the last object written — which is the worst case: every
    other artifact is already in the bucket. The bundle must still be unreadable, and the plan
    must come to rest at `failed` (from which it can be re-armed) rather than at `compiling`.
    """
    store.fail_on = MANIFEST_PATH
    approve(db_session, plan.id)
    client = signed_in_client(app, SUBJECT)
    frames = compile_bundle(client, plan.id)

    error = frame_named(frames, "error")
    assert error["error"] == "RuntimeError"
    assert error["bundle_id"] == bundle_id_for(plan.id)
    # ADR-0030 A1: ids and a type name, never prose composed from a commons value.
    assert set(error) == {"bundle_id", "plan_id", "error"}
    assert not any(name == "manifest" for name, _ in frames)

    assert store.paths(bundle_id_for(plan.id)) == set(), "a failed compile left objects behind"
    assert status_of(db_session, plan.id) == "failed"
    assert client.get(f"/bundles/{bundle_id_for(plan.id)}/manifest").status_code == 404


def test_the_compile_is_refused_when_this_deployment_does_not_run_it_in_process(
    app: FastAPI, db_session: Session, plan: PlanRecord, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tech-design §5.3's flag, honoured by the transport: `503`, not a compile in a web worker."""
    monkeypatch.setenv("SIYUR_COMPILE_IN_PROCESS", "0")
    approve(db_session, plan.id)
    response = signed_in_client(app, SUBJECT).post("/bundles", json={"plan_id": str(plan.id)})

    assert response.status_code == 503
    assert response.json()["detail"] == {"error": "compile_not_in_process"}
    assert status_of(db_session, plan.id) == "approved", "the refusal claimed nothing"


def test_a_deployment_with_no_base_style_refuses_rather_than_compiling_a_blank_map(
    db_engine: Engine, db_session: Session, store: _MemoryStore, plan: PlanRecord
) -> None:
    """There is no style stage in the service yet; a compile without one is refused, not faked.

    And the claim it took is rolled back, so the plan stays approved and compilable the moment a
    style is configured — a refused precondition must not cost the user their approval.
    """
    app = build_bundles_app(engine=db_engine, bundle_store=store, compile_seams=CompileSeams())
    approve(db_session, plan.id)
    response = signed_in_client(app, SUBJECT).post("/bundles", json={"plan_id": str(plan.id)})

    assert response.status_code == 503
    assert response.json()["detail"] == {"error": "style_unavailable"}
    assert status_of(db_session, plan.id) == "approved"


def test_an_area_with_no_stored_frame_has_one_derived_and_written_back(
    app: FastAPI, db_session: Session, area: Area, plan: PlanRecord
) -> None:
    """`api/areas.py` stores the resolved polygon without the frame `resolve_area` computed.

    Rather than refuse every such compile, the frame is derived from the area's **own polygon**
    by the same deterministic offline lookup the migration backfilled with, and persisted. A
    stored frame always wins; this is the backfill, not a recomputation on read.
    """
    area.timezone = None
    area.country_code = None
    db_session.commit()

    approve(db_session, plan.id)
    frames = compile_bundle(signed_in_client(app, SUBJECT), plan.id)
    assert frame_named(frames, "done")["over_budget"] is False

    db_session.expire_all()
    refreshed = db_session.get(Area, area.id)
    assert refreshed is not None
    assert (refreshed.timezone, refreshed.country_code) == FRAME


# ── the manifest ──────────────────────────────────────────────────────────────────


def test_the_manifest_is_served_verbatim_and_reports_its_size_before_any_artifact_moves(
    compiled: tuple[TestClient, str, BundleManifestV1], store: _MemoryStore
) -> None:
    """FR-014 / SC-007: the client learns the download size from this one small read.

    Verbatim matters: a re-serialization would be a second producer of the bytes a client checks
    `integrity.manifest_sha256` against.
    """
    client, bundle_id, manifest = compiled
    response = client.get(f"/bundles/{bundle_id}/manifest")

    assert response.status_code == 200
    assert response.content == store.objects[object_name(bundle_id, MANIFEST_PATH)]
    body = response.json()
    assert body["size_bytes"] == manifest.size_bytes > 0
    assert BundleManifestV1.model_validate(body).verify_manifest_sha256()
    # The reads so far are the manifest only: not one artifact byte has moved.
    assert store.reads == [(bundle_id, MANIFEST_PATH)]


def test_another_users_manifest_is_404_byte_identical_to_an_unknown_bundle(
    app: FastAPI, compiled: tuple[TestClient, str, BundleManifestV1]
) -> None:
    """The privacy boundary, with the control that makes it a proof rather than a tautology."""
    owner, bundle_id, _ = compiled
    url = f"/bundles/{bundle_id}/manifest"
    mine = owner.get(url)
    assert mine.status_code == 200 and mine.content, "the control: this URL serves real content"

    stranger = signed_in_client(app, STRANGER)
    foreign = stranger.get(url)
    unknown = stranger.get(f"/bundles/{bundle_id_for(uuid4())}/manifest")

    assert foreign.status_code == unknown.status_code == 404
    assert foreign.content == unknown.content


@pytest.mark.parametrize("bundle_id", ["bnd_not-a-uuid", "whatever", "", "bnd_"])
def test_an_id_we_did_not_mint_is_404(app: FastAPI, plan: PlanRecord, bundle_id: str) -> None:
    """One spelling per bundle: a dashed, braced or `urn:`-prefixed id names no bundle here."""
    client = signed_in_client(app, SUBJECT)
    dashed = f"bnd_{plan.id!s}"  # the same plan, spelled the way `UUID()` would also accept

    assert client.get(f"/bundles/{bundle_id}/manifest").status_code == 404
    assert client.get(f"/bundles/{dashed}/manifest").status_code == 404


# ── the artifacts ─────────────────────────────────────────────────────────────────


def test_every_path_the_manifest_names_resolves_to_its_own_hashed_bytes(
    compiled: tuple[TestClient, str, BundleManifestV1],
) -> None:
    """FR-021's airplane-mode invariant, checked path by path: the index is complete and true."""
    client, bundle_id, manifest = compiled
    index = artifact_paths(manifest)
    assert len(index) == 8, "the card's seven artifact hashes plus the base style"

    for path, digest in index.items():
        response = client.get(f"/bundles/{bundle_id}/artifacts/{path}")
        assert response.status_code == 200, f"{path}: {response.text}"
        assert hashlib.sha256(response.content).hexdigest() == digest, path
        assert response.headers["etag"].strip('"') == digest, path
        assert response.headers["accept-ranges"] == "bytes"
        assert int(response.headers["content-length"]) == len(response.content)


def test_a_range_request_answers_206_with_exactly_the_bytes_asked_for(
    compiled: tuple[TestClient, str, BundleManifestV1],
) -> None:
    """`Range: bytes=0-1023` ⇒ `206`, 1024 bytes, `Content-Range`, and the manifest's `sha256`.

    The `ETag` is the **whole artifact's** hash even on a partial read — that is what lets a
    resumed download recognise it is stitching chunks of the same object together.
    """
    client, bundle_id, manifest = compiled
    path = manifest.tiles.pmtiles.path
    response = client.get(
        f"/bundles/{bundle_id}/artifacts/{path}", headers={"Range": "bytes=0-1023"}
    )

    assert response.status_code == 206
    assert len(response.content) == 1024
    assert int(response.headers["content-length"]) == 1024
    assert response.headers["content-range"] == f"bytes 0-1023/{len(ARCHIVE_BYTES)}"
    assert response.headers["etag"].strip('"') == manifest.tiles.pmtiles.sha256
    assert response.headers["accept-ranges"] == "bytes"
    # The resumed remainder stitches back onto it, byte for byte.
    rest = client.get(f"/bundles/{bundle_id}/artifacts/{path}", headers={"Range": "bytes=1024-"})
    assert rest.status_code == 206
    assert response.content + rest.content == ARCHIVE_BYTES


def test_an_unsatisfiable_range_is_416_and_says_how_big_the_object_really_is(
    compiled: tuple[TestClient, str, BundleManifestV1],
) -> None:
    """`416` with `bytes */<size>` is what lets a client correct an offset instead of guessing."""
    client, bundle_id, manifest = compiled
    path = manifest.tiles.pmtiles.path
    response = client.get(
        f"/bundles/{bundle_id}/artifacts/{path}", headers={"Range": "bytes=999999-1000000"}
    )

    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(ARCHIVE_BYTES)}"
    assert response.headers["accept-ranges"] == "bytes"


def test_a_malformed_range_header_is_ignored_and_the_whole_object_is_served(
    compiled: tuple[TestClient, str, BundleManifestV1],
) -> None:
    """RFC 9110 §14.2: a `Range` a recipient cannot parse is ignored, not rejected."""
    client, bundle_id, manifest = compiled
    response = client.get(
        f"/bundles/{bundle_id}/artifacts/{manifest.tiles.pmtiles.path}",
        headers={"Range": "furlongs=0-3"},
    )

    assert response.status_code == 200
    assert response.content == ARCHIVE_BYTES


def test_a_path_the_manifest_does_not_name_is_404_even_when_the_object_exists(
    compiled: tuple[TestClient, str, BundleManifestV1], store: _MemoryStore
) -> None:
    """ "The manifest is the index, not a hint" — asserted against an object that is really there.

    The glyph files are the honest case: the compile wrote them, the store holds them, and the
    manifest names only their *directory prefix* with a listing digest. A route that stat-ed the
    store would serve them with an `ETag` it had no hash for. The positive control is the line
    above it: the same client, the same bundle, a path that **is** in the manifest.
    """
    client, bundle_id, manifest = compiled
    control = client.get(f"/bundles/{bundle_id}/artifacts/{manifest.content.sites}")
    assert control.status_code == 200 and control.content

    present = sorted(path for path in store.paths(bundle_id) if path.startswith("glyphs/"))
    assert present, "the fixture compile is meant to vendor glyphs — nothing is being proved here"
    store.reads.clear()

    unknown_bundle = client.get(f"/bundles/{bundle_id_for(uuid4())}/artifacts/whatever.json")
    for path in (present[0], "content/not-an-artifact.json", MANIFEST_PATH):
        response = client.get(f"/bundles/{bundle_id}/artifacts/{path}")
        assert response.status_code == 404, path
        # …and indistinguishable from an unknown bundle, so the refusal reveals nothing about
        # what the bucket happens to hold.
        assert response.content == unknown_bundle.content, path

    # Only the manifest was read: no path check ever reached for the object it was refusing.
    assert {path for _, path in store.reads} == {MANIFEST_PATH}


def test_another_users_artifact_is_404_byte_identical_to_an_unknown_bundle(
    app: FastAPI, compiled: tuple[TestClient, str, BundleManifestV1]
) -> None:
    """Same boundary as the manifest route, same control, on the route that moves the bytes."""
    owner, bundle_id, manifest = compiled
    url = f"/bundles/{bundle_id}/artifacts/{manifest.content.sites}"
    mine = owner.get(url)
    assert mine.status_code == 200 and mine.content, "the control: this URL serves real content"

    stranger = signed_in_client(app, STRANGER)
    foreign = stranger.get(url)
    unknown = stranger.get(f"/bundles/{bundle_id_for(uuid4())}/artifacts/{manifest.content.sites}")

    assert foreign.status_code == unknown.status_code == 404
    assert foreign.content == unknown.content


def test_a_bundle_whose_compile_never_published_is_404_on_both_read_routes(
    app: FastAPI, db_session: Session, plan: PlanRecord
) -> None:
    """An approved-but-never-compiled plan has an id and no bundle. The plan is not the bundle."""
    approve(db_session, plan.id)
    client = signed_in_client(app, SUBJECT)
    bundle_id = bundle_id_for(plan.id)

    assert client.get(f"/bundles/{bundle_id}/manifest").status_code == 404
    assert client.get(f"/bundles/{bundle_id}/artifacts/content/sites.json").status_code == 404


def test_a_store_that_cannot_answer_is_503_and_not_a_404(
    compiled: tuple[TestClient, str, BundleManifestV1], store: _MemoryStore
) -> None:
    """A broken bucket is *our* failure, and saying "no such bundle" would misattribute it.

    The distinction is the difference between a client that retries and a client that deletes a
    bundle it believes is gone — which, offline, is the traveller's whole day.
    """
    client, bundle_id, manifest = compiled

    def refuse(*args: Any, **kwargs: Any) -> ObjectRead:
        raise ObjectStoreError("the bucket is unreachable")

    store.get = refuse  # type: ignore[method-assign]

    assert client.get(f"/bundles/{bundle_id}/manifest").status_code == 503
    artifact = client.get(f"/bundles/{bundle_id}/artifacts/{manifest.content.sites}")
    assert artifact.status_code == 503
    assert artifact.json()["detail"] == {"error": "store_unavailable"}


def test_a_published_manifest_that_does_not_validate_is_500_and_not_a_404(
    compiled: tuple[TestClient, str, BundleManifestV1], store: _MemoryStore
) -> None:
    """Same reasoning one level in: a manifest we published and cannot read is a defect of ours."""
    client, bundle_id, manifest = compiled
    store.objects[object_name(bundle_id, MANIFEST_PATH)] = b'{"schema_ver": "BundleManifestV1"}'

    response = client.get(f"/bundles/{bundle_id}/artifacts/{manifest.content.sites}")

    assert response.status_code == 500
    assert response.json()["detail"] == {"error": "manifest_invalid"}


# ── the same contract, against the real emulator ──────────────────────────────────


@pytest.fixture
def gcs_store() -> Iterator[BundleStore]:
    """The compose-managed `fake-gcs-server`, or a skip (`docker compose --profile compile up`).

    A skip rather than a testcontainer: this file's other dependency (PostGIS) is already
    session-managed by `tests/conftest.py`, and CI job 3 does not run the GCS service yet.
    """
    host = os.environ.get(ENV_EMULATOR_HOST, "").strip() or "localhost:4443"
    endpoint = (host if "://" in host else f"http://{host}").rstrip("/")
    probe = httpx.Client(timeout=2.0)
    try:
        response = probe.get(f"{endpoint}/storage/v1/b/{DEFAULT_BUCKET}")
    except httpx.HTTPError as error:
        pytest.skip(f"no fake-gcs-server at {endpoint} ({type(error).__name__}: {error})")
    finally:
        probe.close()
    if response.status_code not in (200, 404):
        pytest.skip(f"no fake-gcs-server at {endpoint} (HTTP {response.status_code})")
    store = BundleStore(endpoint=endpoint)
    store.ensure_bucket()
    yield store


def test_the_whole_contract_holds_against_the_real_object_store(
    db_engine: Engine,
    db_session: Session,
    seams: CompileSeams,
    plan: PlanRecord,
    gcs_store: BundleStore,
) -> None:
    """End to end over HTTP and real GCS-protocol range reads — no double anywhere in the path.

    The in-memory store proves the endpoint's arithmetic; this proves the endpoint's arithmetic
    is not the only thing standing between a resumed download and a corrupt file.
    """
    app = build_bundles_app(engine=db_engine, bundle_store=gcs_store, compile_seams=seams)
    approve(db_session, plan.id)
    client = signed_in_client(app, SUBJECT)
    bundle_id = bundle_id_for(plan.id)
    try:
        frames = compile_bundle(client, plan.id)
        manifest = BundleManifestV1.model_validate(frame_named(frames, "manifest"))

        served = client.get(f"/bundles/{bundle_id}/manifest")
        assert served.status_code == 200
        assert json.loads(served.content)["bundle_id"] == bundle_id

        for path, digest in artifact_paths(manifest).items():
            whole = client.get(f"/bundles/{bundle_id}/artifacts/{path}")
            assert whole.status_code == 200, path
            assert hashlib.sha256(whole.content).hexdigest() == digest, path

        head = client.get(
            f"/bundles/{bundle_id}/artifacts/{manifest.tiles.pmtiles.path}",
            headers={"Range": "bytes=0-1023"},
        )
        assert head.status_code == 206
        assert len(head.content) == 1024
        assert head.headers["etag"].strip('"') == manifest.tiles.pmtiles.sha256
    finally:
        gcs_store.delete_bundle(bundle_id)


def test_the_bundled_records_are_the_days_own_stops(
    compiled: tuple[TestClient, str, BundleManifestV1],
) -> None:
    """The bundle freezes what the day visits — not the whole area's pool (FR-016)."""
    client, bundle_id, manifest = compiled
    sites = client.get(f"/bundles/{bundle_id}/artifacts/{manifest.content.sites}").json()["sites"]

    assert {UUID(site["id"]) for site in sites} == {_palace().id, _street().id}
    for site in sites:
        assert SiteRecordV1.model_validate(site).schema_ver == "SiteRecordV1"


def test_a_compiled_plan_is_left_readable_and_its_approval_intact(
    db_session: Session, compiled: tuple[TestClient, str, BundleManifestV1], plan: PlanRecord
) -> None:
    """The claim is released exactly once, and the approval that authorised it is still there."""
    stored = load_plan(db_session, plan.id, user_id=SUBJECT)
    assert stored is not None
    assert stored.status == "compiled"
    assert stored.approved_at is not None and stored.approved_by == SUBJECT


def test_the_bundled_geometry_lies_inside_the_extracted_bbox(
    compiled: tuple[TestClient, str, BundleManifestV1],
) -> None:
    """CRS discipline over the wire: EPSG:4326, lon first, and the day inside its own tiles."""
    _, _, manifest = compiled
    min_lon, min_lat, max_lon, max_lat = manifest.tiles.pmtiles.bbox

    assert min_lon < PALACE[0] < STREET[0] < max_lon
    assert min_lat < PALACE[1] < STREET[1] < max_lat
