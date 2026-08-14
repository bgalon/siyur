"""T043 — ``POST /bundles`` (SSE), ``GET /bundles/{id}/manifest`` and the artifact fetch.

Contract: `specs/002-plan-compile-offline/contracts/bundles.md`. Like `api/areas.py` this
module is transport: `compiler/pipeline.py` already yields exactly the frames the contract
specifies, so what lives here is the HTTP shape, the transaction boundary, and the five
decisions a bundle transport has to make honestly.

**1. The HITL gate is a claim, not a check.** ``POST /bundles`` never reads a status and then
starts work: it calls :func:`~commons.repository.claim_plan_for_compile`, a conditional
``UPDATE … WHERE status='approved' RETURNING``, and proceeds only if a row came back. A
``proposed`` plan is not refused by an ``if`` — it is *unclaimable* (FR-006 / SC-003), so a
future code path that forgets the gate compiles nothing. The ``409`` body is then built from a
**re-read** of the row, the same read-the-row discipline
:func:`~commons.repository.approve_plan` uses, because "the claim matched nothing" is one fact
and "why" is another. There is deliberately no ``SELECT … FOR UPDATE``: under READ COMMITTED
the conditional ``UPDATE`` re-evaluates its own predicate against the winner's committed
version, so the lock buys nothing and costs a round trip — and ``tests/test_hitl_gate.py`` pins
that the claim emits exactly one statement.

**2. The claim is committed before the stream starts, and released in a ``finally``.** A claim
held in an uncommitted transaction is invisible to the second request, which would then claim
the same plan and compile it twice. And a client that disconnects after the manifest is
published but before the ``done`` frame arrives must not leave a complete bundle behind a row
stuck at ``compiling`` — so the release runs in a ``finally``, and its outcome is decided by
whether the pipeline got as far as **publishing** (the ``manifest`` frame, which
`compiler/pipeline.py` emits only after every object including the manifest has reached the
store), never by whether the last frame reached the client.

**3. A bundle id is derived from its plan id, because there is no bundle table.** Bundle
objects are objects, not rows (`specs/002-plan-compile-offline/data-model.md`), and the format
of ``bundle_id`` is one of that document's own open gaps (G12). Ownership still has to be
answerable on a bare ``GET /bundles/{id}/manifest``, so the id **carries** the plan it was
compiled from — :func:`bundle_id_for` / :func:`plan_id_of` — and the scope check is the
ordinary row-scoped :func:`~commons.repository.load_plan`. One spelling only: an id that does
not round-trip through :func:`bundle_id_for` is not an id we minted and is a ``404``. The
consequence is deliberate and worth stating: **one plan has one bundle**, so recompiling an
edited day means a new plan row (which is what an edit already is) and recompiling *the same*
day overwrites its own objects.

**4. Another user's bundle is a ``404``, byte-identical to an unknown one.** Every negative
answer on all three routes is the same :data:`_NO_BUNDLE` detail — unknown id, unparsable id,
another subject's plan, a bundle whose manifest was never published, and a path the manifest
does not name. A ``403`` would confirm the bundle exists, and two ``404``s that differ by a
word do the same thing more slowly.

**5. The manifest is the index, not a hint.** The artifact route builds its path→hash table
from the manifest and answers ``404`` for anything absent from it **without touching the
store**. That is what makes "a path not in the manifest is a ``404`` by construction" a
property of the code rather than of the bucket's contents, and it is also what lets ``ETag``
be the artifact's manifest ``sha256`` — the hash is looked up, never computed over bytes we
just served.

Errors on the stream are **in-band** (``event: error``), the way `api/areas.py` does it: once
the ``200`` and the SSE headers are gone there is no status code left to fail with. Their
payload is narrower than the research stream's, though — type name and ids, never
``str(error)`` — because a compile's exceptions are raised from modules that handle commons
values, and ADR-0030 A1 forbids a server-computed response from embedding commons-derived
text. The detail goes to the log, where it is an operator's to read.
"""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Final, Protocol, runtime_checkable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from geoalchemy2.shape import to_shape
from pydantic import BaseModel, ConfigDict, ValidationError
from shapely.geometry.base import BaseGeometry
from sqlalchemy import update
from sqlalchemy.orm import Session
from starlette.responses import Response

from api.areas import SSE_HEADERS, SSE_MEDIA_TYPE
from api.deps import SessionDep, SessionFactoryDep
from api.security import CurrentUser
from commons.db import Area
from commons.frame import FrameUnresolved, LocalFrame, resolve_frame
from commons.models import BundleManifestV1, SiteRecordV1
from commons.repository import (
    PlanRecord,
    claim_plan_for_compile,
    finish_compile,
    load_plan,
    reap_stale_compiles,
    sites_within,
)
from compiler.manifest import Artifact
from compiler.pipeline import (
    MANIFEST_PATH,
    CompileRequest,
    Event,
    in_process_compile_enabled,
    run_compile,
)
from compiler.routes import WalkNetworkSource
from compiler.storage import (
    BundleStore,
    ByteRange,
    ObjectNotFound,
    ObjectRead,
    ObjectStoreError,
    RangeNotSatisfiable,
)
from compiler.tiles import AssetSource, ProtomapsBuild, TileExtractor

__all__ = [
    "BUNDLE_ID_PREFIX",
    "COMPILE_SEAMS_STATE",
    "BundleObjects",
    "CompileRequestBody",
    "CompileSeams",
    "bundle_id_for",
    "plan_id_of",
    "router",
]

_log = logging.getLogger(__name__)

router = APIRouter(prefix="/bundles", tags=["bundles"])

#: Every ``404`` this module can produce, so that "not yours" and "does not exist" are the
#: **same bytes** and not merely the same status code (`contracts/bundles.md`).
_NO_BUNDLE: Final = "no bundle with that id for this user"

#: ``bundle_id`` = this prefix plus the plan's UUID hex. See decision 3 in the module docstring.
BUNDLE_ID_PREFIX: Final = "bnd_"

#: ``app.state`` key holding a :class:`CompileSeams`. Setting it replaces every default.
COMPILE_SEAMS_STATE: Final = "compile_seams"
#: ``app.state`` key holding the object store (anything satisfying :class:`BundleObjects`).
BUNDLE_STORE_STATE: Final = "bundle_store"


# ── the store seam ────────────────────────────────────────────────────────────────


@runtime_checkable
class BundleObjects(Protocol):
    """What this module needs from the object store: write, remove, and **read back**.

    :class:`compiler.storage.BundleStore` satisfies it structurally. ``put``/``delete_bundle``
    are `compiler/pipeline.py`'s :class:`~compiler.pipeline.BundleObjectStore`; ``get`` is the
    half that Protocol deliberately omits, because reading is the API's side of the contract —
    a compile never reads its own output back.
    """

    def put(self, bundle_id: str, path: str, data: bytes) -> object: ...

    def delete_bundle(self, bundle_id: str) -> int: ...

    def get(self, bundle_id: str, path: str, byte_range: ByteRange | None = None) -> ObjectRead: ...


@dataclass(frozen=True, slots=True)
class CompileSeams:
    """The compile's injectable inputs — everything `compiler/pipeline.py` will not build.

    Injected through ``app.state`` rather than FastAPI ``dependency_overrides`` for the reason
    `api/deps.py` gives: the same seams are needed *inside* a streaming generator, after the
    dependency scope has closed.

    The style is carried as **bytes plus a bundle-relative path** rather than as a hashed
    :class:`~compiler.manifest.Artifact`, because the pipeline reads every hashed artifact back
    out of the staging tree at publish time: the bytes have to be written into *this compile's*
    staging directory, and hashing them there is one line. There is no style stage in the
    service yet, so both are ``None`` by default and a deployment that has not supplied one
    answers ``503`` instead of compiling a bundle whose map cannot start.
    """

    #: Bundle-relative path of the base MapLibre style, e.g. ``style/base.json``.
    style_path: str | None = None
    style_bytes: bytes | None = None
    #: Where the walking linework comes from; ``None`` means the live OSMnx reader.
    walk_network: WalkNetworkSource | None = None
    #: The tile stage's three seams; ``None`` each means the live resolver, the ``pmtiles``
    #: CLI and the Protomaps asset host (`compiler/tiles.py`).
    build: ProtomapsBuild | None = None
    extractor: TileExtractor | None = None
    assets: AssetSource | None = None


def get_bundle_store(request: Request) -> BundleObjects:
    """The bundle object store: whatever ``app.state`` names, else one built from the env.

    ``BundleStore.from_env`` reads ``STORAGE_EMULATOR_HOST``/``SIYUR_BUNDLE_BUCKET`` from the
    process environment only — never a file, never ``.env*`` (AGENTS.md).
    """
    store: BundleObjects | None = getattr(request.app.state, BUNDLE_STORE_STATE, None)
    return store if store is not None else BundleStore.from_env()


def get_compile_seams(request: Request) -> CompileSeams:
    seams: CompileSeams | None = getattr(request.app.state, COMPILE_SEAMS_STATE, None)
    return seams if seams is not None else CompileSeams()


StoreDep = Annotated[BundleObjects, Depends(get_bundle_store)]
SeamsDep = Annotated[CompileSeams, Depends(get_compile_seams)]


# ── ids ───────────────────────────────────────────────────────────────────────────


def bundle_id_for(plan_id: UUID) -> str:
    """The bundle id of a plan's compile — also the object-store prefix its artifacts sit under."""
    return f"{BUNDLE_ID_PREFIX}{plan_id.hex}"


def plan_id_of(bundle_id: str) -> UUID | None:
    """The plan a bundle id names, or ``None`` when this is not an id we minted.

    Strict on purpose: ``UUID()`` accepts a dashed, braced or ``urn:``-prefixed spelling of the
    same value, and every accepted spelling would be a second URL for one bundle whose object
    prefix does not exist. Only the exact output of :func:`bundle_id_for` round-trips.
    """
    if not bundle_id.startswith(BUNDLE_ID_PREFIX):
        return None
    raw = bundle_id.removeprefix(BUNDLE_ID_PREFIX)
    try:
        plan_id = UUID(hex=raw)
    except ValueError:
        return None
    return plan_id if plan_id.hex == raw else None


# ── request/response bodies ───────────────────────────────────────────────────────


class CompileRequestBody(BaseModel):
    """``POST /bundles`` — ``{"plan_id": "…"}``, the whole request (`contracts/bundles.md`)."""

    model_config = ConfigDict(extra="forbid")

    plan_id: UUID


# ── helpers ───────────────────────────────────────────────────────────────────────


def _sse(event: str, data: Mapping[str, Any]) -> str:
    """One SSE frame. ``json.dumps`` escapes newlines, so ``data:`` can never be split."""
    payload = json.dumps(dict(data), ensure_ascii=False, separators=(",", ":"), default=str)
    return f"event: {event}\ndata: {payload}\n\n"


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NO_BUNDLE)


def _owned_plan(session: Session, bundle_id: str, subject: str) -> PlanRecord:
    """The plan a bundle id names, **scoped to its owner**, or the one ``404``.

    Three distinguishable situations — not an id we mint, no such plan, someone else's plan —
    deliberately collapse into one indistinguishable answer.
    """
    plan_id = plan_id_of(bundle_id)
    if plan_id is None:
        raise _not_found()
    plan = load_plan(session, plan_id, user_id=subject)
    if plan is None:
        raise _not_found()
    return plan


def _manifest_bytes(store: BundleObjects, bundle_id: str) -> bytes:
    """The published manifest's own bytes, or a ``404``.

    A bundle whose compile failed has no ``manifest.json`` — the pipeline writes it last and
    removes the objects on failure — so "no manifest object" *is* "no bundle", and it is the
    same ``404`` as an unknown id.
    """
    try:
        return store.get(bundle_id, MANIFEST_PATH).data
    except ObjectNotFound as error:
        raise _not_found() from error
    except ObjectStoreError as error:
        _log.exception("object store could not answer for bundle %s", bundle_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"error": "store_unavailable"}
        ) from error


def _artifact_index(manifest: BundleManifestV1) -> dict[str, str]:
    """Every fetchable path the manifest names → the ``sha256`` it records for it.

    The card's **seven** artifact hashes plus the base style, which is a single hashed file
    inside the embedded :class:`~commons.models.TileSourceV1` and which the map cannot start
    without.

    ``tiles.pmtiles.glyphs``/``sprites`` are **not** here, and that is the honest reading of the
    schema rather than an omission: both are ``GlyphsRef``s whose ``path`` is a directory
    *prefix* and whose ``sha256`` covers a listing, not a byte stream
    (:func:`compiler.tiles.directory_digest`). There is no per-file hash to answer an ``ETag``
    with, so serving one file at a time from under that prefix would mean either a wrong ``ETag``
    or an ``ETag``-less artifact on the one endpoint whose whole point is resumable, verifiable
    reads. Today nothing asks: the web client fetches exactly the seven
    (``web/src/bundle/download.ts``) and renders glyphs from its own static ``/basemap/`` path.
    Making the glyph set addressable is a **card** change (a per-file listing artifact), not a
    decision to take quietly in a transport.
    """
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


def _parsed_manifest(data: bytes, bundle_id: str) -> BundleManifestV1:
    """The published manifest as a model — a ``500`` when it does not validate.

    Not a ``404``: the bundle exists, and answering "no such bundle" for a bundle whose manifest
    we published and cannot read would hide a defect of ours behind a client-shaped answer.
    """
    try:
        return BundleManifestV1.model_validate(json.loads(data))
    except (ValidationError, ValueError) as error:
        _log.exception("published manifest of bundle %s does not validate", bundle_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "manifest_invalid"},
        ) from error


def _refuse(state: str, error: str) -> HTTPException:
    """One ``409`` body: an error code and the row state, both closed vocabularies.

    Counts, ids and enum values only — never prose composed from a commons value (ADR-0030 A1).
    ``state`` is :data:`~commons.db.PlanStatus`, which `contracts/plans.md` already exposes
    verbatim, so nothing here is a second spelling of the lifecycle.
    """
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT, detail={"error": error, "state": state}
    )


def _claim(session: Session, plan_id: UUID, subject: str) -> PlanRecord:
    """Take the compile claim, or raise the contract's ``404``/``409``.

    The claim runs first and the explanation second: a zero-row claim is re-read and *branched
    on the row's actual state*, never inferred from which predicate failed.
    """
    claimed = claim_plan_for_compile(session, plan_id, user_id=subject)
    if claimed is not None:
        return claimed
    plan = load_plan(session, plan_id, user_id=subject)
    if plan is None:
        raise _not_found()
    if plan.status == "compiling":
        # The idempotency guard, and unlike `api/areas.py`'s in-process set this one is a row:
        # it holds across processes and across restarts.
        raise _refuse(plan.status, "compile_in_progress")
    raise _refuse(plan.status, "plan_not_approved")


def _load_area(session: Session, area_id: UUID, subject: str) -> Area:
    """The plan's area, scoped to the caller — the polygon and the local frame come from here."""
    area = session.get(Area, area_id)
    if area is None or area.created_by != subject:
        raise _not_found()
    return area


def _frame_of(session: Session, area: Area) -> LocalFrame:
    """The area's local frame, derived and **written back** when the row does not carry one.

    ``area.timezone``/``country_code`` are what make the bundled wall-clock times mean anything
    offline (ADR-0025 ruling 2, T040a). They are nullable for **legacy rows**: the ones that
    predate the columns, plus the ones written between the columns landing and ``create_area``
    learning to persist what :func:`~planner.nodes.resolve_area.resolve_area` had already
    computed (fixed in #110). ``0005_user_plan`` backfilled only the rows that existed when it
    ran, so those two cohorts still reach here with NULLs. Rather than refuse every such
    compile, the frame is derived from the row's **own polygon** by the same deterministic,
    offline lookup the migration backfilled with, and persisted so the next compile of the same
    area reads a stored value rather than re-deriving one.

    **This is a legacy-row backfill, not a live workaround** — ``api/areas.py`` persists the
    frame at creation today, and a reader should not infer from this function that it does not.
    The path stays because a row written before that fix is indistinguishable from one written
    after it, except by being NULL here.

    Deriving is not "recomputing on a read": a stored frame always wins, exactly so that a day
    stays readable in the frame it was planned in even after the boundary data changes.

    An area with no frame at all (open water, a CRS mistake) is a ``409``: a bundle whose times
    are a guess is worse than a compile that was refused (`commons/frame.py`).
    """
    if area.timezone and area.country_code:
        return LocalFrame(timezone=area.timezone, country_code=area.country_code)
    polygon: BaseGeometry = to_shape(area.polygon)
    try:
        frame = resolve_frame(polygon)
    except (FrameUnresolved, ValueError) as error:
        raise _refuse("approved", "area_frame_unresolved") from error
    session.execute(
        update(Area)
        .where(Area.id == area.id, Area.created_by == area.created_by)
        .values(timezone=frame.timezone, country_code=frame.country_code)
    )
    return frame


def _style_artifact(seams: CompileSeams, staging: Path) -> Artifact:
    """Write the base style into this compile's staging tree and hash it there.

    The pipeline reads every hashed artifact back out of ``staging`` at publish time, so the
    bytes must be on disk under their bundle-relative path before the compile starts —
    including the style, which the tile stage hashes but does not write.
    """
    if not seams.style_path or seams.style_bytes is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "style_unavailable"},
        )
    target = staging / seams.style_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(seams.style_bytes)
    return Artifact.from_bytes(seams.style_path, seams.style_bytes)


def _compile_frames(
    session: Session,
    staging: tempfile.TemporaryDirectory[str],
    stream: Iterator[Event],
    *,
    plan_id: UUID,
    subject: str,
    bundle_id: str,
) -> Iterator[str]:
    """Stream the compile, then release the claim — whatever happened, exactly once.

    ``published`` is the release's discriminator, and it is read off the **``manifest`` frame**
    rather than off the ``done`` frame: `compiler/pipeline.py` publishes every object, manifest
    last, *before* yielding ``manifest``, so by the time that frame exists the bundle is
    readable. A client that drops the connection between it and ``done`` arrives here as a
    ``GeneratorExit`` — not an ``Exception``, so the ``except`` below does not see it — and the
    ``finally`` still records ``compiled``, because a published bundle whose row says ``failed``
    is a bundle nobody can compile again and nobody can throw away.
    """
    published = False
    try:
        for event in stream:
            published = published or event.event == "manifest"
            yield _sse(event.event, event.data)
    except Exception as error:  # noqa: BLE001 — the status code is long gone; say so in-band
        _log.exception("compile failed mid-stream for plan %s (bundle %s)", plan_id, bundle_id)
        # Type name and ids only. A compile's exceptions are raised from modules that handle
        # commons values, and a server-computed response must not carry commons-derived text
        # (ADR-0030 A1); `str(error)` is one refactor away from doing exactly that.
        yield _sse(
            "error",
            {"bundle_id": bundle_id, "plan_id": str(plan_id), "error": type(error).__name__},
        )
    finally:
        try:
            finish_compile(
                session,
                plan_id,
                user_id=subject,
                outcome="compiled" if published else "failed",
            )
            session.commit()
        except Exception:  # noqa: BLE001 — nothing is left to tell the client with
            session.rollback()
            _log.exception("could not release the compile claim on plan %s", plan_id)
        finally:
            session.close()
            staging.cleanup()


# ── endpoints ─────────────────────────────────────────────────────────────────────


@router.post("", summary="Compile an approved plan into an offline bundle (SSE)")
def compile_bundle(
    user: CurrentUser,
    factory: SessionFactoryDep,
    store: StoreDep,
    seams: SeamsDep,
    body: CompileRequestBody,
) -> Response:
    """T043 — drive `compiler/pipeline.py` and stream its frames as ``text/event-stream``.

    Everything that can be decided before a byte is sent is decided here and becomes a real
    status code: ``401`` (the dependency order puts ``user`` first), ``503`` when this
    deployment does not run the in-process compile or has no base style, ``404`` for an unknown
    or foreign plan, ``409`` for a plan that is not approved or is already compiling. Everything
    after that streams, and a mid-stream failure is an in-band ``event: error``.
    """
    if not in_process_compile_enabled():
        # tech-design §5.3: M2 moves this generator to a Cloud Run Job. A deployment that has
        # turned the in-process path off says so rather than compiling in the web process.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "compile_not_in_process"},
        )

    plan_id = body.plan_id
    bundle_id = bundle_id_for(plan_id)
    session = factory()
    staging = tempfile.TemporaryDirectory(prefix="siyur-compile-")
    try:
        # A compile whose process died leaves `compiling` with no timeout and no reaper, and
        # that row would refuse every retry for ever. Scoped to this plan and this subject:
        # a request path must never rewrite another subject's row. It does not unblock *this*
        # request (a reaped row is `failed`, still unclaimable) — re-arming a failed compile is
        # a decision someone makes, which is the point.
        reap_stale_compiles(session, plan_id=plan_id, user_id=user.sub)
        session.commit()

        claimed = _claim(session, plan_id, user.sub)
        area = _load_area(session, claimed.area_id, user.sub)
        frame = _frame_of(session, area)
        sites: Sequence[SiteRecordV1] = sites_within(session, to_shape(area.polygon))
        style = _style_artifact(seams, Path(staging.name))
        # The claim becomes visible here: until it commits, a second request would claim the
        # same plan and compile it twice.
        session.commit()
    except BaseException:
        session.rollback()
        session.close()
        staging.cleanup()
        raise

    stream = run_compile(
        CompileRequest(bundle_id=bundle_id, itinerary=claimed.itinerary, frame=frame),
        sites=sites,
        walk_network=seams.walk_network or _live_walk_network(),
        store=store,
        staging=Path(staging.name),
        style=style,
        build=seams.build,
        extractor=seams.extractor,
        assets=seams.assets,
    )
    return StreamingResponse(
        _compile_frames(
            session, staging, stream, plan_id=plan_id, subject=user.sub, bundle_id=bundle_id
        ),
        media_type=SSE_MEDIA_TYPE,
        headers=SSE_HEADERS,
    )


@router.get("/{bundle_id}/manifest", summary="The bundle manifest — read before downloading")
def read_manifest(
    user: CurrentUser, session: SessionDep, store: StoreDep, bundle_id: str
) -> Response:
    """The published ``BundleManifestV1``, **verbatim** (`docs/data/bundle-manifest.md`).

    Served as the stored bytes rather than a re-serialization: ``size_bytes`` is read from here
    *before* the first artifact byte moves (FR-014 / SC-007), and a client that verifies
    ``integrity.manifest_sha256`` should be checking the bytes we published, not the bytes a
    second serializer produced from them.
    """
    _owned_plan(session, bundle_id, user.sub)
    return Response(content=_manifest_bytes(store, bundle_id), media_type="application/json")


@router.get("/{bundle_id}/artifacts/{path:path}", summary="One bundled artifact, range-friendly")
def read_artifact(
    request: Request,
    user: CurrentUser,
    session: SessionDep,
    store: StoreDep,
    bundle_id: str,
    path: str,
) -> Response:
    """One artifact the manifest names — ``200``/``206``/``416``, ``ETag`` from the manifest.

    ``Accept-Ranges: bytes`` on every answer and ``Content-Length`` exact, so an interrupted
    download resumes instead of restarting (`contracts/bundles.md`); the identical contract is
    what DU-06's OPFS transport swap satisfies locally, unchanged (ADR-0002).
    """
    _owned_plan(session, bundle_id, user.sub)
    manifest = _parsed_manifest(_manifest_bytes(store, bundle_id), bundle_id)
    digest = _artifact_index(manifest).get(path)
    if digest is None:
        # By construction, and without asking the store: the manifest is the index.
        raise _not_found()

    byte_range = ByteRange.parse(request.headers.get("Range"))
    try:
        read = store.get(bundle_id, path, byte_range)
    except ObjectNotFound as error:
        raise _not_found() from error
    except RangeNotSatisfiable as error:
        headers = {"Accept-Ranges": "bytes", "ETag": f'"{digest}"'}
        if error.total_size >= 0:
            # `bytes */<size>` is what a resuming client needs to correct its offset; the store
            # reports -1 only when the origin declined to say, and inventing a size would be
            # worse than omitting the header.
            headers["Content-Range"] = f"bytes */{error.total_size}"
        raise HTTPException(
            status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
            detail={"error": "range_not_satisfiable"},
            headers=headers,
        ) from error
    except ObjectStoreError as error:
        _log.exception("object store could not answer for %s/%s", bundle_id, path)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail={"error": "store_unavailable"}
        ) from error

    # Quoted per RFC 9110 §8.8.3 — an unquoted entity-tag is not a valid one. The digest inside
    # is the manifest's, byte for byte.
    headers = {"Accept-Ranges": "bytes", "ETag": f'"{digest}"'}
    if read.content_range is not None:
        headers["Content-Range"] = read.content_range
    return Response(
        content=read.data,
        status_code=status.HTTP_206_PARTIAL_CONTENT if read.is_partial else status.HTTP_200_OK,
        media_type=read.content_type,
        headers=headers,
    )


def _live_walk_network() -> WalkNetworkSource:
    """The networked walking-network reader. Constructed per compile, never at import.

    Local so that importing this module costs nothing and a test that never compiles cannot
    pull in an Overpass-capable stack (`compiler/routes.py` makes the same choice one level
    down, for the same reason).
    """
    from compiler.routes import OsmnxWalkNetwork

    return OsmnxWalkNetwork()
