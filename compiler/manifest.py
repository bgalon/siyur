"""Manifest assembly — one hash per artifact, and the one canonicalization the seal is over.

T040/T040a: the last two stages of the compile order (`compiler/AGENTS.md`, tech-design
§5.3). Everything upstream has produced bytes; this module records **where** those bytes
live inside the bundle, **what** each one hashes to, and seals the whole into a
:class:`~commons.models.BundleManifestV1` a device can re-check offline. Field-level ground
truth is [`docs/data/bundle-manifest.md`](../docs/data/bundle-manifest.md); where anything
here reads differently, the card wins.

## What this module deliberately does NOT implement: the canonicalizer

``integrity.manifest_sha256`` is **RFC 8785 (JCS)** over the manifest with the ``integrity``
key **removed** — not nulled, not emptied — and both halves of that already exist, in two
places that must agree byte-for-byte:

* **Python, the writer**: :meth:`BundleManifestV1.canonical_payload` deletes the key and
  :meth:`BundleManifestV1.seal` digests the result through the pinned ``rfc8785``
  (Trail of Bits, Apache-2.0, ``~=0.1.4`` — a 0.x minor bump changes every bundle's hash);
* **TypeScript, the launch-time verifier** (T053): ``web/src/bundle/manifest.ts`` deletes
  the same key and canonicalizes through the pinned ``canonicalize`` npm package.

A third implementation here is exactly the divergence the named-standard rule exists to
prevent, so :func:`build_manifest` calls ``seal`` and nothing below hand-rolls a serializer.
Measured on the ``©``-plus-integral-bbox shape **every** real manifest carries:

    json.dumps(sort_keys=True)  {"attribution":"\\u00a9 OpenStreetMap …","bbox":[28.0,36.0,…]}
    rfc8785.dumps               {"attribution":"© OpenStreetMap …","bbox":[28,36,…]}

Both divergences are in that one line: ``json.dumps`` defaults to ``ensure_ascii=True`` and
escapes the ``©`` of the ODbL attribution (fires on every bundle), and it renders ``28.0``
where ECMAScript ``Number::toString`` renders ``28`` (fires only when a bbox bound lands on
a whole degree — intermittent, so it reads like corruption rather than like a serialization
bug). JCS pins string escaping and number formatting as well as key order; "sorted keys,
UTF-8, no whitespace" pins neither. A manifest that gets this wrong fails its own launch
check **offline, on the traveller's device, with no way to diagnose it**.

That the two sides actually agree is a measured fact, not an inference from the RFC
(2026-08-09): a manifest carrying the ODbL ``©`` and ``bbox: [28.0, 36.0, 28.5, 36.5]``
canonicalizes to the same 1,607 bytes and the same digest ``698bc6ed…`` under ``rfc8785``
and under ``canonicalize@3.0.0`` driven through ``manifest.ts``'s own copy-delete-
canonicalize path. Re-measure it, do not re-reason about it, if either pin moves.

## One hash per artifact, over raw bytes

Every path in the manifest has exactly one SHA-256 beside it, lowercase hex, taken over the
artifact's **raw bytes** — a hash spanning two files could not say which one corrupted.
:class:`Artifact` is that pairing, and :meth:`Artifact.from_file` streams so the PMTiles
archive (tens to hundreds of MB) is hashed without being held in memory.

## The frozen itinerary is a *different document* from the approved one

``content.itinerary_sha256`` hashes the bytes of :attr:`FrozenItinerary.data`; the approval
hash on the ``user_plan`` row is :meth:`ItineraryV1.canonical_sha256`, over the itinerary
alone. The two digests differ **by construction** — the frozen artifact carries the local
frame the plan row does not — and neither is a check on the other. Comparing them (or
"fixing" one to match) would break either the approval compare-and-set (ADR-0023) or the
bundle's own integrity check.

## T040a — the local frame travels with the day, or the day means nothing

Every bundled time is **area-local wall clock**, and what turns ``10:00`` into an instant is
``ItineraryV1.date`` plus the *area row's* ``timezone``/``country_code``. A database row is
not a bundle artifact. Offline the travel UI can read only the device clock — on a phone
still set to home time that is hours off, and the UI highlights the wrong stop with nothing
to detect the error against. So :func:`freeze_itinerary` copies both values into the frozen
artifact, top-level, **alongside** ``date``: same fix ADR-0025 ruling 1 applied to the
itinerary itself, one level down.

They are written as sibling keys rather than a nested object because the card's rule is that
``ItineraryV1`` *never restates* the frame — so there is no field for them to nest under, and
"alongside `date`" is where a reader looking for the day's frame will look. The consequence
is stated rather than discovered: **the artifact is not a valid ``ItineraryV1`` payload.**
``StampedModel`` is ``extra="forbid"``, so ``ItineraryV1.model_validate(json.loads(data))``
*fails* on it. Read it back with :func:`read_frozen_itinerary`, which separates the two
halves. The TypeScript reader (``web/src/travel/parse.ts``) ignores keys it does not model,
so the artifact narrows there unchanged.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final
from uuid import UUID

import rfc8785

from commons.frame import LocalFrame
from commons.models import (
    ArtifactRef,
    BundleContent,
    BundleManifestV1,
    BundleRouting,
    BundleTiles,
    ItineraryV1,
    TileSourceV1,
    WithheldValue,
)

__all__ = [
    "FRAME_KEYS",
    "ITINERARY_PATH",
    "Artifact",
    "FrozenItinerary",
    "build_manifest",
    "canonical_manifest_bytes",
    "freeze_itinerary",
    "manifest_json_bytes",
    "read_frozen_itinerary",
]

#: Read size for :meth:`Artifact.from_file`. 1 MiB keeps a 200 MB PMTiles archive off the
#: heap while staying far above the syscall-per-byte end of the tradeoff.
_HASH_CHUNK: Final[int] = 1 << 20

#: The two keys T040a adds to the frozen itinerary artifact — the area's local frame, copied
#: from the ``area`` row at compile time. Exported so a reader can strip exactly these and no
#: others; they are the *only* keys in the artifact that are not ``ItineraryV1`` fields.
FRAME_KEYS: Final[tuple[str, str]] = ("timezone", "country_code")

#: Default bundle-relative path of the frozen itinerary (the card's example layout). A
#: default, not a constraint: the manifest records whatever path the artifact was given.
ITINERARY_PATH: Final[str] = "content/itinerary.json"


@dataclass(frozen=True, slots=True)
class Artifact:
    """One bundled file as the manifest records it: where it is, what it hashes to, how big.

    ``sha256`` is lowercase hex over the file's raw bytes — lowercase because
    :data:`~commons.models.Sha256Hex` refuses anything else, the casing being part of the
    contract with a TypeScript verifier that compares these as plain strings.
    ``hashlib.hexdigest()`` is lowercase by definition, so nothing here normalizes it.
    """

    #: Bundle-relative path; validated as a :data:`~commons.models.BundlePath` when the
    #: manifest is built, so an absolute or ``..``-bearing path fails there, not silently.
    path: str
    sha256: str
    #: Byte length of the artifact — summed into ``BundleManifestV1.size_bytes``, which FR-014
    #: reports *before* the download starts.
    size_bytes: int

    @classmethod
    def from_bytes(cls, path: str, data: bytes) -> Artifact:
        """Hash an in-memory artifact (every JSON/Markdown artifact the compiler generates)."""
        return cls(path=path, sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data))

    @classmethod
    def from_file(cls, path: str, source: Path) -> Artifact:
        """Hash an artifact on disk **without reading it into memory**.

        For the PMTiles archive, which is the one bundled artifact big enough for the
        difference to matter (the ≤200 MB budget is almost entirely tiles). ``source`` is a
        filesystem path; ``path`` is where the artifact will sit *inside the bundle*, and the
        two are unrelated on purpose — nothing about our staging layout should leak into the
        manifest a device reads.
        """
        digest = hashlib.sha256()
        size = 0
        with source.open("rb") as handle:
            while chunk := handle.read(_HASH_CHUNK):
                digest.update(chunk)
                size += len(chunk)
        return cls(path=path, sha256=digest.hexdigest(), size_bytes=size)


@dataclass(frozen=True, slots=True)
class FrozenItinerary:
    """The day, serialized for the bundle: the bytes to write, and how the manifest names them.

    Carries ``itinerary_id`` so :func:`build_manifest` reads the id off the same object it
    hashes. Passing the id separately would let ``itinerary_id`` and ``content.itinerary``
    name different days, and nothing downstream re-checks that pairing.
    """

    itinerary_id: UUID
    #: Exactly the bytes to write at ``artifact.path`` — hashing anything else breaks the
    #: check this whole module exists to make possible.
    data: bytes
    artifact: Artifact


def freeze_itinerary(
    itinerary: ItineraryV1, frame: LocalFrame, *, path: str = ITINERARY_PATH
) -> FrozenItinerary:
    """Freeze the planned day into the bundle's ``content.itinerary`` artifact (T040/T040a).

    The document is the itinerary's JSON dump plus the area's :data:`FRAME_KEYS` — see the
    module docstring for why the frame has to travel with the day and why it sits top-level.

    Serialized with **JCS**, the same canonicalization the manifest seal uses, though nothing
    requires it of an artifact: it makes recompiling an unchanged day byte-identical, so an
    unchanged ``content.itinerary_sha256`` means "the day did not change" rather than "the
    day did not change *and* the serializer felt the same way about dict order today".
    """
    payload = itinerary.model_dump(mode="json")

    # A frame key already present would mean `ItineraryV1` grew a field this function then
    # silently overwrote — the card says the itinerary never restates the frame, so if that
    # ever changes it is a schema decision, not something to paper over here.
    collisions = [key for key in FRAME_KEYS if key in payload]
    if collisions:
        raise ValueError(
            f"ItineraryV1 now carries {', '.join(collisions)} itself; the frozen artifact "
            "would overwrite it. Reconcile with docs/data/itinerary.md before compiling."
        )
    payload["timezone"] = frame.timezone
    payload["country_code"] = frame.country_code

    data = rfc8785.dumps(payload)
    return FrozenItinerary(
        itinerary_id=itinerary.id, data=data, artifact=Artifact.from_bytes(path, data)
    )


def read_frozen_itinerary(data: bytes) -> tuple[ItineraryV1, LocalFrame]:
    """Read a frozen artifact back into the day and the frame it is read in.

    The inverse of :func:`freeze_itinerary`, and the only correct way to validate the artifact
    in Python: the frame keys must come off *before* ``ItineraryV1.model_validate``, which is
    ``extra="forbid"`` and would otherwise refuse the compiler's own output.

    Raises ``ValueError`` if the frame is absent or not a pair of non-empty strings. Absent is
    refused rather than defaulted, for the reason :class:`~commons.frame.FrameUnresolved`
    exists: a day whose wall clock is unframed cannot be scheduled *or* checked, and the
    device has no second source to fall back on.
    """
    payload: Any = json.loads(data)
    if not isinstance(payload, dict):
        raise ValueError(f"a frozen itinerary is a JSON object, got {type(payload).__name__}")

    frame_values = {key: payload.pop(key, None) for key in FRAME_KEYS}
    missing = [
        key for key, value in frame_values.items() if not isinstance(value, str) or not value
    ]
    if missing:
        raise ValueError(
            f"frozen itinerary is missing its local frame ({', '.join(missing)}); every "
            "bundled time is area-local wall clock and is unreadable without it (T040a)"
        )

    return ItineraryV1.model_validate(payload), LocalFrame(
        timezone=str(frame_values["timezone"]), country_code=str(frame_values["country_code"])
    )


def canonical_manifest_bytes(manifest: BundleManifestV1) -> bytes:
    """The exact bytes ``integrity.manifest_sha256`` is taken over — JCS, ``integrity`` gone.

    A two-call convenience over the model's own :meth:`BundleManifestV1.canonical_payload`
    (which is what removes the key) and the pinned ``rfc8785`` — *not* a second canonicalizer.
    It exists because commons exposes the digest but not the bytes, and the bytes are what a
    cross-language comparison against ``web/src/bundle/manifest.ts`` has to be made over.
    """
    return rfc8785.dumps(manifest.canonical_payload())


def manifest_json_bytes(manifest: BundleManifestV1) -> bytes:
    """The bytes to **ship** as ``manifest.json`` — the sealed manifest, nothing dropped.

    Use this rather than a local ``json.dumps``, because the seal covers *which keys exist*
    and a writer is one keyword away from breaking it. ``model_dump(mode="json")`` emits
    ``"schematic": null`` for an M1 bundle (the M2 slot, unfilled), and the digest was taken
    over a payload that had it; a writer using ``exclude_none=True`` — or hand-building a
    "tidier" document — ships a manifest whose own ``manifest_sha256`` no longer matches, and
    the verifier reports the bundle corrupt. Measured: dropping that one null key changes the
    digest completely, and the only symptom is an unusable bundle offline.

    Serialized with JCS as well. The verifier re-canonicalizes whatever it parses, so key
    *order* on the wire is not load-bearing; determinism is worth having anyway, and it means
    the shipped bytes differ from :func:`canonical_manifest_bytes` in exactly one respect —
    the ``integrity`` object is present.
    """
    return rfc8785.dumps(manifest.model_dump(mode="json"))


def build_manifest(
    *,
    bundle_id: str,
    itinerary: FrozenItinerary,
    tiles: TileSourceV1,
    tiles_size_bytes: int,
    walk_graph: Artifact,
    legs: Artifact,
    sites: Artifact,
    narrations: Artifact,
    attribution: Artifact,
    text_license: str | None,
    withheld: Sequence[WithheldValue] = (),
    created_at: datetime | None = None,
) -> BundleManifestV1:
    """Assemble and **seal** the manifest — the compile pipeline's final stage.

    Every argument is already-produced output of an earlier stage; this function invents no
    content. It records the seven per-artifact hashes, totals ``size_bytes``, and seals, so
    the returned manifest's ``integrity.manifest_sha256`` already covers everything below it.

    Args:
        bundle_id: stable id of this compiled bundle (format is the caller's; G12).
        itinerary: :func:`freeze_itinerary`'s output — supplies both ``itinerary_id`` and the
            ``content.itinerary`` artifact, so the two cannot disagree.
        tiles: the **whole** ``TileSourceV1`` from T035, embedded as the card requires (not a
            four-field summary of it); it already carries the archive's own ``sha256``.
        tiles_size_bytes: every byte the tiles stage wrote — the PMTiles archive plus the
            style JSON and glyph directory the ``TileSourceV1`` points at. Those files belong
            to T035/T035b and this module never sees them, so their size is passed in rather
            than guessed: a ``size_bytes`` missing the tiles would under-report the download
            by roughly the whole bundle.
        text_license: ``"CC-BY-SA-4.0"`` when any story is bundled, ``None`` when
            ``content.narrations`` is empty (ADR-0024) — the share-alike declaration, written
            to the card's camelCase ``textLicense``. The narration stage decides it; the
            manifest only records it.
        withheld: what quarantine (T038) removed, with its closed-enum reason. Empty means
            nothing was withheld — never "we did not look".
        created_at: UTC; defaults to now. Explicit only for reproducible builds and tests.

    ``size_bytes`` deliberately excludes the manifest itself: its size is unknown until it is
    sealed (sealing writes a 64-char digest into it), and against a tile archive the few
    hundred bytes are noise. It is a download estimate, not an audit.
    """
    # Two artifacts at one path is not a failure the launch check can explain: the second
    # write wins in the archive, its recorded hash then belongs to bytes that are gone, and
    # the bundle fails verification pointing at the wrong file. Cheap to refuse here.
    _refuse_duplicate_paths(
        tiles.path,
        tiles.style.path,
        tiles.glyphs.path,
        walk_graph.path,
        legs.path,
        sites.path,
        narrations.path,
        itinerary.artifact.path,
        attribution.path,
    )

    size_bytes = tiles_size_bytes + sum(
        artifact.size_bytes
        for artifact in (
            walk_graph,
            legs,
            sites,
            narrations,
            itinerary.artifact,
            attribution,
        )
    )

    fields: dict[str, Any] = {
        "bundle_id": bundle_id,
        "itinerary_id": itinerary.itinerary_id,
        "size_bytes": size_bytes,
        "tiles": BundleTiles(pmtiles=tiles),
        "routing": BundleRouting(
            walk_graph=walk_graph.path,
            walk_graph_sha256=walk_graph.sha256,
            legs=legs.path,
            legs_sha256=legs.sha256,
        ),
        "content": BundleContent(
            sites=sites.path,
            sites_sha256=sites.sha256,
            narrations=narrations.path,
            narrations_sha256=narrations.sha256,
            itinerary=itinerary.artifact.path,
            itinerary_sha256=itinerary.artifact.sha256,
        ),
        "attribution": ArtifactRef(path=attribution.path, sha256=attribution.sha256),
        "textLicense": text_license,
        "withheld": tuple(withheld),
    }
    # Absent rather than `None`: the model's default factory is what supplies "now", and
    # passing None would fail validation instead of falling back to it.
    if created_at is not None:
        fields["created_at"] = created_at

    return BundleManifestV1.seal(**fields)


def _refuse_duplicate_paths(*paths: str) -> None:
    """Refuse two artifacts claiming one bundle path (see :func:`build_manifest`)."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for path in paths:
        if path in seen:
            duplicates.add(path)
        seen.add(path)
    if duplicates:
        raise ValueError(
            f"two artifacts share a bundle path: {', '.join(sorted(duplicates))}. One path "
            "holds one artifact and one hash; the second write would replace the first."
        )
