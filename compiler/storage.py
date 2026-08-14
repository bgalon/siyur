"""Bundle object storage — put and read bundle artifacts, ranged reads first.

The bundle is a set of **objects, not rows** (`specs/002-plan-compile-offline/data-model.md`):
the manifest and every artifact it names live in GCS, and locally in `fake-gcs-server`
(`docker-compose.yml`, the `gcs` profile). This module is the only place the compiler and
the API touch that store.

**Ranged reads are the primary read path, not an extra.** `contracts/bundles.md` promises
`206`/`416` on every artifact so an interrupted download *resumes* rather than restarting —
the traveller is on a bad connection, and a 100 MB PMTiles that restarts on every drop never
finishes. So there is exactly one read primitive here, :meth:`BundleStore.get` with a
:class:`ByteRange`, and the API's whole-object response is that primitive iterated. One read
path means the resumable path is the exercised one, not a rarely-taken branch.

**No provider SDK.** `google-cloud-storage` is not a pinned dependency (ADR-0007 pins are
resolved-then-pinned with a publisher check), so this speaks the GCS **JSON API** over the
already-pinned `httpx`. That is a smaller surface than it sounds: upload is one `POST`,
download is one `GET ?alt=media`, and `Range` is plain HTTP, which is precisely why the
range contract survives the DU-06 OPFS transport swap unchanged (ADR-0002).

**Emulator vs. real GCS is one environment variable** — `STORAGE_EMULATOR_HOST`, the same
name the Google libraries honour, so switching to the SDK later is not a config change.
Unset means real GCS, which needs a bearer token; credentials are never read from `.env*`
or `secrets/`, so the token arrives through an injected ``token_provider`` (Application
Default Credentials would be a new dependency, and that is Ben's call, not this module's).

Nothing here is place-specific or bucket-specific: the bucket is a parameter with a dev-only
default, matching the compose file's ``${VAR:-default}`` posture.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Final
from urllib.parse import quote

import httpx

__all__ = [
    "DEFAULT_BUCKET",
    "DEFAULT_CONTENT_TYPE",
    "ENV_BUCKET",
    "ENV_EMULATOR_HOST",
    "ENV_PROJECT",
    "GCS_ENDPOINT",
    "BundleStore",
    "ByteRange",
    "ObjectMetadata",
    "ObjectNotFound",
    "ObjectRead",
    "ObjectStoreError",
    "RangeNotSatisfiable",
    "content_type_for",
    "object_name",
]

#: The switch. `http://localhost:4443` locally; unset in production, meaning real GCS.
ENV_EMULATOR_HOST: Final = "STORAGE_EMULATOR_HOST"
ENV_BUCKET: Final = "SIYUR_BUNDLE_BUCKET"
#: Only the emulator's bucket-create call needs a project; real buckets are Terraform's.
ENV_PROJECT: Final = "SIYUR_GCS_PROJECT"
GCS_ENDPOINT: Final = "https://storage.googleapis.com"
#: Dev-only default, product-named rather than place-named — a real deployment sets the var.
DEFAULT_BUCKET: Final = "siyur-bundles-dev"
DEFAULT_PROJECT: Final = "siyur-dev"
#: Generous: a per-area PMTiles extract is tens of MB and uploads in one request.
DEFAULT_TIMEOUT: Final = 120.0
USER_AGENT: Final = "siyur/0.0 (https://github.com/bgalon/siyur)"

DEFAULT_CONTENT_TYPE: Final = "application/octet-stream"
#: The bundle's artifact types (`docs/data/bundle-manifest.md`). The store does not care,
#: but the browser does: OPFS-cached JSON read back as `octet-stream` is a needless cast.
_CONTENT_TYPES: Final[Mapping[str, str]] = {
    ".geojson": "application/geo+json",
    ".json": "application/json",
    ".md": "text/markdown; charset=utf-8",
    ".pbf": "application/x-protobuf",
    ".png": "image/png",
    ".pmtiles": "application/vnd.pmtiles",
    # `glyphs/OFL.txt` — the vendored licence text a bundle ships to discharge OFL-1.1.
    # Without this it uploads as `application/octet-stream`, which no gate rejects and
    # nothing reads programmatically, but which offers a licence as a download rather
    # than as something a reader can open.
    ".txt": "text/plain; charset=utf-8",
}

#: A single byte-range spec: `bytes=0-1023`, `bytes=1024-`, or the suffix form `bytes=-512`.
_RANGE_SPEC: Final[re.Pattern[str]] = re.compile(r"\s*bytes\s*=\s*(?:(\d+)-(\d*)|-(\d+))\s*\Z")
_CONTENT_RANGE: Final[re.Pattern[str]] = re.compile(r"\s*bytes\s+(\d+)-(\d+)/(\d+|\*)\s*\Z")
_UNSATISFIED_RANGE: Final[re.Pattern[str]] = re.compile(r"\s*bytes\s+\*/(\d+)\s*\Z")
#: Path segments that would let an artifact address something other than itself.
_UNSAFE_SEGMENTS: Final[frozenset[str]] = frozenset({"", ".", ".."})


class ObjectStoreError(RuntimeError):
    """The object store could not answer — transport, auth, or an unexpected status."""


class ObjectNotFound(ObjectStoreError):
    """No such object. The API turns this into the contract's `404`."""


class RangeNotSatisfiable(ObjectStoreError):
    """The requested range lies outside the object; carries the size a `416` must report."""

    def __init__(self, total_size: int) -> None:
        super().__init__(f"range not satisfiable over {total_size} bytes")
        self.total_size = total_size


@dataclass(frozen=True, slots=True)
class ByteRange:
    """One HTTP byte range, in the wire form's own terms.

    The two fields *are* `Range: bytes=<first>-<last>`: both bounds **inclusive**, `last`
    absent meaning "to the end", and `first` absent meaning `last` is a **suffix length**
    (`bytes=-512` = the final 512 bytes). Keeping the wire grammar rather than a
    start/length pair removes the off-by-one that an inclusive/exclusive translation
    invites at exactly the point where a resumed download stitches two chunks together.
    """

    first: int | None
    last: int | None

    def __post_init__(self) -> None:
        if self.first is None:
            if self.last is None or self.last <= 0:
                raise ValueError("a suffix range needs a positive length")
        elif self.first < 0 or (self.last is not None and self.last < self.first):
            raise ValueError(f"invalid byte range: {self.first}-{self.last}")

    @classmethod
    def of(cls, first: int, last: int | None = None) -> ByteRange:
        """`bytes=first-last`, inclusive; `last=None` runs to the end of the object."""
        return cls(first=first, last=last)

    @classmethod
    def suffix(cls, length: int) -> ByteRange:
        """`bytes=-length` — the last `length` bytes, the form a resume-from-tail uses."""
        return cls(first=None, last=length)

    @classmethod
    def parse(cls, value: str | None) -> ByteRange | None:
        """A `Range` header → a range, or ``None`` meaning *serve the whole object*.

        ``None`` for absent, malformed **and** multi-range headers. That is not laziness:
        RFC 9110 §14.2 requires a recipient to *ignore* a Range header it cannot parse
        rather than reject the request, and GCS itself answers a multi-range request with
        the complete object. Returning ``None`` makes the caller do the conformant thing by
        default; unsatisfiability is a separate question, answered by :meth:`resolve`
        against the real size.
        """
        if not value:
            return None
        match = _RANGE_SPEC.match(value)
        if match is None:
            return None
        first, last, suffix = match.groups()
        if suffix is not None:
            length = int(suffix)
            return cls.suffix(length) if length > 0 else None
        try:
            return cls.of(int(first), int(last) if last else None)
        except ValueError:
            return None

    @property
    def header_value(self) -> str:
        if self.first is None:
            return f"bytes=-{self.last}"
        if self.last is None:
            return f"bytes={self.first}-"
        return f"bytes={self.first}-{self.last}"

    def resolve(self, total_size: int) -> tuple[int, int]:
        """Clamp against a known size → the inclusive `(first, last)` actually served.

        Raises :class:`RangeNotSatisfiable` — carrying `total_size`, which is what the
        `416` response's `Content-Range: bytes */<size>` must report.
        """
        if total_size <= 0:
            raise RangeNotSatisfiable(total_size)
        if self.first is None:
            length = min(self.last or 0, total_size)
            return total_size - length, total_size - 1
        if self.first >= total_size:
            raise RangeNotSatisfiable(total_size)
        last = total_size - 1 if self.last is None else min(self.last, total_size - 1)
        return self.first, last


@dataclass(frozen=True, slots=True)
class ObjectMetadata:
    """What the store knows about one artifact — size first, since the manifest reports it.

    `md5Hash`/`crc32c` are deliberately absent: GCS's checksums are transport integrity,
    while the bundle's integrity contract is the SHA-256 the manifest carries per artifact
    (FR-013). Two hashes of different provenance sitting side by side invites checking the
    wrong one.
    """

    path: str
    size_bytes: int
    content_type: str
    generation: str | None = None


@dataclass(frozen=True, slots=True)
class ObjectRead:
    """Bytes plus the frame a `200`/`206` response needs: how much was served, of what."""

    path: str
    data: bytes
    total_size: int
    content_type: str
    #: Inclusive `(first, last)` when a range was satisfied; ``None`` for the whole object.
    served: tuple[int, int] | None = None

    @property
    def is_partial(self) -> bool:
        return self.served is not None

    @property
    def content_range(self) -> str | None:
        """The `Content-Range` value for a `206`, or ``None`` for a whole-object `200`."""
        if self.served is None:
            return None
        first, last = self.served
        return f"bytes {first}-{last}/{self.total_size}"


def content_type_for(path: str) -> str:
    """Guess from the artifact's extension; unknown is `application/octet-stream`."""
    return _CONTENT_TYPES.get(PurePosixPath(path).suffix.lower(), DEFAULT_CONTENT_TYPE)


def object_name(bundle_id: str, path: str) -> str:
    """A bundle-relative manifest path → the object's name, `<bundle_id>/<path>`.

    The manifest is the index, so *a path it does not name is a `404` by construction*
    (`contracts/bundles.md`). That only holds if a path cannot **address** anything outside
    its own bundle, so traversal (`..`), absolute paths, empty segments, backslashes and
    control characters are refused here — at the single point where an untrusted-looking
    string becomes an object name — rather than trusted because the manifest produced it.

    A trailing slash is refused too: `glyphs/` in a `TileSourceV1` names a **prefix**, not
    an object, and fetching it is a bug. Enumerate it with :meth:`BundleStore.list_paths`.
    """
    if not bundle_id or "/" in bundle_id or bundle_id in _UNSAFE_SEGMENTS:
        raise ValueError(f"invalid bundle id: {bundle_id!r}")
    if not path or path.startswith("/") or "\\" in path:
        raise ValueError(f"invalid artifact path: {path!r}")
    if any(segment in _UNSAFE_SEGMENTS for segment in path.split("/")):
        raise ValueError(f"invalid artifact path: {path!r}")
    if any(character < " " or character == "\x7f" for character in path):
        raise ValueError(f"invalid artifact path: {path!r}")
    return f"{bundle_id}/{path}"


@dataclass(frozen=True, slots=True)
class BundleStore:
    """Put and read one bundle's artifacts against GCS or `fake-gcs-server`.

    ``client`` injects an ``httpx`` transport exactly as :class:`~commons.sources.osm
    .OsmAdapter` does, so Tier 1 drives the whole surface through ``httpx.MockTransport``
    with no container; Tier 2 points ``endpoint`` at the running emulator. When it is
    ``None`` a client is created per call and closed again.

    ``token_provider`` is called once per request for a real-GCS bearer token. It stays
    ``None`` locally — the emulator is unauthenticated — and no credential is ever read
    from a file here.
    """

    bucket: str = DEFAULT_BUCKET
    endpoint: str = GCS_ENDPOINT
    project: str = DEFAULT_PROJECT
    timeout: float = DEFAULT_TIMEOUT
    client: httpx.Client | None = None
    token_provider: Callable[[], str] | None = None

    @classmethod
    def from_env(
        cls,
        *,
        client: httpx.Client | None = None,
        token_provider: Callable[[], str] | None = None,
    ) -> BundleStore:
        """Read the endpoint and bucket from the process environment (never from a file)."""
        emulator = os.environ.get(ENV_EMULATOR_HOST, "").strip()
        return cls(
            bucket=os.environ.get(ENV_BUCKET, "").strip() or DEFAULT_BUCKET,
            endpoint=_normalise_endpoint(emulator) if emulator else GCS_ENDPOINT,
            project=os.environ.get(ENV_PROJECT, "").strip() or DEFAULT_PROJECT,
            client=client,
            token_provider=token_provider,
        )

    @property
    def is_emulated(self) -> bool:
        return self.endpoint != GCS_ENDPOINT

    def ensure_bucket(self) -> None:
        """Create the bucket if absent — emulator only, and a no-op when it exists.

        Refused against real GCS on purpose: production buckets carry retention, IAM and
        location policy and belong to Terraform, so app code that can conjure one hides a
        misconfigured `SIYUR_BUNDLE_BUCKET` behind a bucket that silently appears.
        """
        if not self.is_emulated:
            raise ObjectStoreError(
                "bucket creation is provisioning, not app code — Terraform owns it"
            )
        with self._session() as client:
            response = client.post(
                f"{self.endpoint}/storage/v1/b",
                params={"project": self.project},
                json={"name": self.bucket},
                headers=self._headers(),
            )
            if response.status_code == 409:  # already exists
                return
            _raise_for_status(response, self.bucket)

    def put(
        self, bundle_id: str, path: str, data: bytes, *, content_type: str | None = None
    ) -> ObjectMetadata:
        """Write one artifact; returns what the store now holds, size included."""
        name = object_name(bundle_id, path)
        media_type = content_type or content_type_for(path)
        with self._session() as client:
            response = client.post(
                f"{self.endpoint}/upload/storage/v1/b/{_segment(self.bucket)}/o",
                params={"uploadType": "media", "name": name},
                content=data,
                headers=self._headers({"Content-Type": media_type}),
            )
            _raise_for_status(response, name)
            return _metadata(path, response.json(), len(data), media_type)

    def stat(self, bundle_id: str, path: str) -> ObjectMetadata:
        """Size and type without moving the bytes — how `size_bytes` is known up front."""
        name = object_name(bundle_id, path)
        with self._session() as client:
            response = client.get(self._object_url(name), headers=self._headers())
            _raise_for_status(response, name)
            return _metadata(path, response.json(), 0, DEFAULT_CONTENT_TYPE)

    def get(self, bundle_id: str, path: str, byte_range: ByteRange | None = None) -> ObjectRead:
        """Read an artifact, whole or by range.

        `416` from the store becomes :class:`RangeNotSatisfiable` carrying the true size,
        which is exactly what the API's `416` must report back.
        """
        name = object_name(bundle_id, path)
        headers = {"Range": byte_range.header_value} if byte_range is not None else {}
        with self._session() as client:
            response = client.get(
                self._object_url(name), params={"alt": "media"}, headers=self._headers(headers)
            )
            if response.status_code == httpx.codes.REQUESTED_RANGE_NOT_SATISFIABLE:
                raise RangeNotSatisfiable(_unsatisfied_size(response.headers.get("Content-Range")))
            _raise_for_status(response, name)
            body = response.content
            media_type = response.headers.get("Content-Type") or content_type_for(path)
            if response.status_code == httpx.codes.PARTIAL_CONTENT:
                served, total = _parse_content_range(response.headers.get("Content-Range"), body)
                return ObjectRead(path, body, total, media_type, served)
            if byte_range is None:
                return ObjectRead(path, body, len(body), media_type, None)
            # `200` to a ranged request: something in the path ignored `Range` — an
            # intermediary, or GCS declining a form it does not serve. All the bytes are
            # here, so satisfy the range locally. The caller's contract (and the resumed
            # download's arithmetic) stays exactly right; only the bandwidth was wasted.
            first, last = byte_range.resolve(len(body))
            return ObjectRead(path, body[first : last + 1], len(body), media_type, (first, last))

    def list_paths(self, bundle_id: str) -> tuple[str, ...]:
        """Every artifact path this bundle actually holds, bundle-relative and sorted.

        The manifest is the index; this is the *territory*, and comparing the two is how
        "every manifest path resolves" (FR-021) is checked rather than assumed.
        """
        prefix = f"{bundle_id}/"
        params: dict[str, str] = {"prefix": prefix}
        found: list[str] = []
        with self._session() as client:
            while True:
                response = client.get(
                    f"{self.endpoint}/storage/v1/b/{_segment(self.bucket)}/o",
                    params=params,
                    headers=self._headers(),
                )
                _raise_for_status(response, prefix)
                payload = response.json()
                for item in payload.get("items") or ():
                    name = str(item.get("name", ""))
                    if name.startswith(prefix):
                        found.append(name[len(prefix) :])
                token = payload.get("nextPageToken")
                if not token:
                    return tuple(sorted(found))
                params = {"prefix": prefix, "pageToken": str(token)}

    def delete_bundle(self, bundle_id: str) -> int:
        """Remove every object under a bundle; returns how many went. Idempotent.

        A compile that fails mid-stream must leave **no partial bundle published**
        (`contracts/bundles.md`). Artifacts already written are the partial bundle, so the
        pipeline's failure path needs this — an unpublished manifest is not enough once the
        objects exist.
        """
        removed = 0
        with self._session() as client:
            for path in self.list_paths(bundle_id):
                response = client.delete(
                    self._object_url(object_name(bundle_id, path)), headers=self._headers()
                )
                if response.status_code == httpx.codes.NOT_FOUND:
                    continue  # raced with another deleter; the desired state still holds
                _raise_for_status(response, path)
                removed += 1
        return removed

    def _object_url(self, name: str) -> str:
        return f"{self.endpoint}/storage/v1/b/{_segment(self.bucket)}/o/{_segment(name)}"

    def _headers(self, extra: Mapping[str, str] | None = None) -> dict[str, str]:
        headers = {"User-Agent": USER_AGENT}
        if self.token_provider is not None:
            headers["Authorization"] = f"Bearer {self.token_provider()}"
        if extra:
            headers.update(extra)
        return headers

    @contextmanager
    def _session(self) -> Iterator[httpx.Client]:
        """The injected client, or a per-call one; either way transport errors come out typed."""
        client = self.client or httpx.Client(timeout=self.timeout)
        try:
            yield client
        except httpx.HTTPError as error:
            raise ObjectStoreError(f"{type(error).__name__}: {error}") from error
        except ValueError as error:  # a body that is not the JSON the API promises
            raise ObjectStoreError(f"malformed object-store response: {error}") from error
        finally:
            if self.client is None:
                client.close()


def _normalise_endpoint(host: str) -> str:
    """`STORAGE_EMULATOR_HOST` is written both ways — `localhost:4443` and a full URL."""
    endpoint = host if "://" in host else f"http://{host}"
    return endpoint.rstrip("/")


def _segment(value: str) -> str:
    """Percent-encode a whole path segment: an object name's `/` must become `%2F`."""
    return quote(value, safe="")


def _metadata(path: str, payload: Any, fallback_size: int, fallback_type: str) -> ObjectMetadata:
    body: Mapping[str, Any] = payload if isinstance(payload, Mapping) else {}
    raw_size = body.get("size")
    try:
        size = int(raw_size) if raw_size is not None else fallback_size
    except (TypeError, ValueError):
        size = fallback_size
    generation = body.get("generation")
    return ObjectMetadata(
        path=path,
        size_bytes=size,
        content_type=str(body.get("contentType") or fallback_type),
        generation=str(generation) if generation is not None else None,
    )


def _parse_content_range(header: str | None, body: bytes) -> tuple[tuple[int, int], int]:
    """`bytes 0-4/12` → `((0, 4), 12)`.

    A `206` without a parseable `Content-Range` is malformed, but the bytes in hand are
    still real: fall back to describing what arrived rather than failing the read, since a
    resumed download only needs to know how far it got.
    """
    match = _CONTENT_RANGE.match(header or "")
    if match is None:
        return (0, max(len(body) - 1, 0)), len(body)
    first, last, total = int(match.group(1)), int(match.group(2)), match.group(3)
    return (first, last), int(total) if total != "*" else last + 1


def _unsatisfied_size(header: str | None) -> int:
    """The size out of a `416`'s `bytes */<size>`; `-1` when the store did not say."""
    match = _UNSATISFIED_RANGE.match(header or "")
    return int(match.group(1)) if match else -1


def _raise_for_status(response: httpx.Response, subject: str) -> None:
    if response.is_success:
        return
    if response.status_code == httpx.codes.NOT_FOUND:
        raise ObjectNotFound(f"no such object: {subject}")
    if response.status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
        raise ObjectStoreError(
            f"object store refused {subject} (HTTP {response.status_code}) — real GCS needs a "
            f"token_provider, or set {ENV_EMULATOR_HOST} to use the local emulator"
        )
    raise ObjectStoreError(f"object store error on {subject}: HTTP {response.status_code}")
