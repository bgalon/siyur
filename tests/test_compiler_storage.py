"""Tier 1 (+ one Tier 2) for `compiler/storage.py` — a spec-002 gap, not a spec task.

T044/T045/T046 cover quarantine, manifest and attribution; T047 covers the *API's* range
contract. Nothing in Spec 002 tests this 500-line module directly, so it is exercised here:
every corner driven through `httpx.MockTransport` (no container, no network) plus one
`@pytest.mark.integration` case against the real `fake-gcs-server` emulator on `:4443`
(`docker compose --profile compile up -d gcs`), which is what actually proves the
`ByteRange` grammar agrees with what GCS answers rather than with itself.

The fake server used below (`FakeGcs`) deliberately does **not** reuse `ByteRange` for its
own range-slicing — see its docstring — so the Tier-1 wiring tests don't validate the module
under test against itself.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import unquote
from uuid import uuid4

import httpx
import pytest

from compiler.storage import (
    DEFAULT_BUCKET,
    ENV_BUCKET,
    ENV_EMULATOR_HOST,
    ENV_PROJECT,
    GCS_ENDPOINT,
    BundleStore,
    ByteRange,
    ObjectNotFound,
    ObjectStoreError,
    RangeNotSatisfiable,
    content_type_for,
    object_name,
)

Handler = Callable[[httpx.Request], httpx.Response]


def client_for(handler: Handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def store_for(handler: Handler, **kwargs: Any) -> BundleStore:
    """A store wired to a mock transport, pointed at a fake (non-real-GCS) endpoint."""
    kwargs.setdefault("endpoint", "http://emulator.test")
    return BundleStore(client=client_for(handler), **kwargs)


def refuses_any_request(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"no request should have been sent, got {request.method} {request.url}")


# ── ByteRange: the wire grammar, in its own terms ───────────────────────────────────────────


def test_of_is_inclusive_and_open_ended_when_last_is_omitted() -> None:
    closed = ByteRange.of(0, 1023)
    assert (closed.first, closed.last) == (0, 1023)
    assert closed.header_value == "bytes=0-1023"

    open_ended = ByteRange.of(1024)
    assert (open_ended.first, open_ended.last) == (1024, None)
    assert open_ended.header_value == "bytes=1024-"


def test_suffix_is_the_final_n_bytes_form() -> None:
    tail = ByteRange.suffix(512)
    assert (tail.first, tail.last) == (None, 512)
    assert tail.header_value == "bytes=-512"


@pytest.mark.parametrize(
    ("first", "last"),
    [(-1, None), (5, 2)],
)
def test_construction_rejects_a_negative_or_inverted_range(first: int, last: int | None) -> None:
    with pytest.raises(ValueError, match="invalid byte range"):
        ByteRange(first=first, last=last)


@pytest.mark.parametrize("length", [0, -1])
def test_a_suffix_range_needs_a_positive_length(length: int) -> None:
    with pytest.raises(ValueError, match="positive length"):
        ByteRange(first=None, last=length)


# ── ByteRange.resolve: clamping against the real size ───────────────────────────────────────


def test_resolve_a_closed_range_within_bounds() -> None:
    assert ByteRange.of(0, 4).resolve(12) == (0, 4)


def test_resolve_clamps_last_to_the_final_byte() -> None:
    """`bytes=6-999` over a 12-byte object serves through byte 11, not past it."""
    assert ByteRange.of(6, 999).resolve(12) == (6, 11)


def test_resolve_an_open_ended_range_runs_to_the_last_byte() -> None:
    assert ByteRange.of(6).resolve(12) == (6, 11)


def test_resolve_a_suffix_within_bounds() -> None:
    """`bytes=-5` over 12 bytes is the last 5: `(7, 11)`."""
    assert ByteRange.suffix(5).resolve(12) == (7, 11)


def test_resolve_a_suffix_longer_than_the_object_clamps_to_the_whole_object() -> None:
    assert ByteRange.suffix(999).resolve(12) == (0, 11)


def test_resolve_raises_when_first_is_at_or_past_the_end() -> None:
    with pytest.raises(RangeNotSatisfiable) as excinfo:
        ByteRange.of(12).resolve(12)
    assert excinfo.value.total_size == 12


def test_resolve_raises_on_an_empty_or_negative_size_object() -> None:
    with pytest.raises(RangeNotSatisfiable) as excinfo:
        ByteRange.of(0).resolve(0)
    assert excinfo.value.total_size == 0


def test_range_not_satisfiable_carries_the_size_a_416_must_report() -> None:
    """The `416` response needs `Content-Range: bytes */<size>` — this is that size."""
    error = RangeNotSatisfiable(12)
    assert error.total_size == 12
    assert "12" in str(error)


# ── ByteRange.parse: None for absent, malformed AND multi-range (RFC 9110 §14.2) ───────────


@pytest.mark.parametrize("value", [None, ""])
def test_parse_returns_none_for_an_absent_header(value: str | None) -> None:
    assert ByteRange.parse(value) is None


@pytest.mark.parametrize(
    "value",
    [
        "not a range",
        "bytes=",
        "bytes=abc-def",
        "byte=0-10",  # wrong unit keyword
        "bytes=10-5",  # last before first — caught via the ValueError branch
    ],
)
def test_parse_returns_none_for_a_malformed_header(value: str) -> None:
    assert ByteRange.parse(value) is None


def test_parse_returns_none_for_a_multi_range_header() -> None:
    """GCS answers a multi-range request with the complete object; `None` is what makes the
    caller do that by default rather than reject the request outright."""
    assert ByteRange.parse("bytes=0-50,100-150") is None


def test_parse_a_zero_length_suffix_is_treated_as_absent() -> None:
    assert ByteRange.parse("bytes=-0") is None


def test_parse_a_closed_range() -> None:
    assert ByteRange.parse("bytes=0-1023") == ByteRange.of(0, 1023)


def test_parse_an_open_ended_range() -> None:
    assert ByteRange.parse("bytes=1024-") == ByteRange.of(1024)


def test_parse_a_suffix_range() -> None:
    assert ByteRange.parse("bytes=-512") == ByteRange.suffix(512)


def test_parse_tolerates_surrounding_and_around_the_equals_sign_whitespace() -> None:
    """Tolerated around `bytes` and `=` (the grammar's own slack); *not* around the `-` inside
    the range spec itself — `"0 - 10"` is not a valid `Range` value and correctly parses to
    `None` (the next test pins that the leniency has a limit)."""
    assert ByteRange.parse("  bytes = 0-10  ") == ByteRange.of(0, 10)


def test_parse_does_not_tolerate_whitespace_around_the_hyphen() -> None:
    assert ByteRange.parse("bytes=0 - 10") is None


# ── content_type_for ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tiles/rhodes.pmtiles", "application/vnd.pmtiles"),
        ("content/sites.json", "application/json"),
        ("routing/walk_graph.geojson", "application/geo+json"),
        ("ATTRIBUTION.md", "text/markdown; charset=utf-8"),
        ("tiles/glyphs/0-255.pbf", "application/x-protobuf"),
        ("preview.PNG", "image/png"),  # case-insensitive suffix match
        ("readme", "application/octet-stream"),  # unknown → the safe default
    ],
)
def test_content_type_for_maps_known_extensions_and_falls_back(path: str, expected: str) -> None:
    assert content_type_for(path) == expected


# ── object_name: the single point an untrusted-looking path becomes an object name ─────────


def test_object_name_joins_bundle_and_path() -> None:
    assert object_name("bnd_01J", "tiles/rhodes.pmtiles") == "bnd_01J/tiles/rhodes.pmtiles"


@pytest.mark.parametrize("bundle_id", ["", ".", "..", "a/b"])
def test_object_name_rejects_an_unsafe_bundle_id(bundle_id: str) -> None:
    with pytest.raises(ValueError, match="invalid bundle id"):
        object_name(bundle_id, "tiles/rhodes.pmtiles")


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/absolute/path.json",
        "back\\slash.json",
        "../escape.json",
        "tiles/../../other_bundle/secret.json",
        "tiles//double_slash.json",
        "tiles/./current.json",
        "tiles/\x00null.json",
        "trailing/slash/",
    ],
)
def test_object_name_refuses_traversal_and_other_unsafe_paths(path: str) -> None:
    """The one security-shaped surface here: a manifest path must not address anything
    outside its own bundle's prefix."""
    with pytest.raises(ValueError, match="invalid artifact path"):
        object_name("bnd_01J", path)


def test_object_name_traversal_cannot_reach_another_bundles_objects() -> None:
    """Concretely: a path built to escape `bnd_a`'s prefix and land inside `bnd_b`'s must be
    refused rather than resolved to `bnd_b`'s object."""
    with pytest.raises(ValueError):
        object_name("bnd_a", "../bnd_b/secret.json")


# ── emulator vs. real GCS: STORAGE_EMULATOR_HOST is the only switch ────────────────────────


def test_default_endpoint_is_real_gcs_and_not_emulated() -> None:
    store = BundleStore()
    assert store.endpoint == GCS_ENDPOINT
    assert store.is_emulated is False


def test_an_explicit_emulator_endpoint_is_emulated() -> None:
    store = BundleStore(endpoint="http://localhost:4443")
    assert store.is_emulated is True


def test_from_env_defaults_to_real_gcs_when_the_var_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(ENV_EMULATOR_HOST, raising=False)
    monkeypatch.delenv(ENV_BUCKET, raising=False)
    monkeypatch.delenv(ENV_PROJECT, raising=False)
    store = BundleStore.from_env()
    assert store.endpoint == GCS_ENDPOINT
    assert store.is_emulated is False
    assert store.bucket == DEFAULT_BUCKET


def test_from_env_switches_to_the_emulator_from_a_bare_host_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`STORAGE_EMULATOR_HOST` is written both ways in practice — bare `host:port` gets an
    `http://` prefix; a full URL is used as given (see the next test)."""
    monkeypatch.setenv(ENV_EMULATOR_HOST, "localhost:4443")
    store = BundleStore.from_env()
    assert store.endpoint == "http://localhost:4443"
    assert store.is_emulated is True


def test_from_env_accepts_a_full_url_and_strips_a_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_EMULATOR_HOST, "https://emulator.internal:9000/")
    store = BundleStore.from_env()
    assert store.endpoint == "https://emulator.internal:9000"


def test_from_env_reads_bucket_and_project_with_whitespace_stripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_BUCKET, "  my-bucket  ")
    monkeypatch.setenv(ENV_PROJECT, "  my-project  ")
    monkeypatch.delenv(ENV_EMULATOR_HOST, raising=False)
    store = BundleStore.from_env()
    assert store.bucket == "my-bucket"
    assert store.project == "my-project"


def test_ensure_bucket_refuses_against_real_gcs_without_sending_a_request() -> None:
    """Bucket creation is provisioning, not app code — Terraform owns real buckets. This must
    be refused *before* any request is sent, which the injected transport enforces."""
    store = BundleStore(client=client_for(refuses_any_request))  # default endpoint: real GCS
    with pytest.raises(ObjectStoreError, match="Terraform owns it"):
        store.ensure_bucket()


def test_ensure_bucket_creates_it_on_the_emulator() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"kind": "storage#bucket", "name": "siyur-bundles-dev"})

    store = store_for(handler)
    store.ensure_bucket()
    assert len(calls) == 1
    assert calls[0].method == "POST"


def test_ensure_bucket_treats_already_exists_as_a_no_op() -> None:
    store = store_for(lambda request: httpx.Response(409))
    store.ensure_bucket()  # must not raise


def test_ensure_bucket_raises_on_an_unexpected_status() -> None:
    store = store_for(lambda request: httpx.Response(500))
    with pytest.raises(ObjectStoreError):
        store.ensure_bucket()


# ── FakeGcs: a minimal in-memory GCS JSON API for the wiring tests below ───────────────────


_TEST_RANGE = re.compile(r"bytes=(\d*)-(\d*)")


def _test_double_slice(data: bytes, header: str | None) -> tuple[bytes, tuple[int, int]] | None:
    """The fake server's *own* range slicer — independent of `ByteRange`/`resolve` on purpose.

    If this reused the module under test to decide what bytes to serve, a wiring test built
    on it could not tell "the store asked for the right range" from "the store and the fake
    server share the same bug". Real-emulator agreement is checked separately, in the
    `@pytest.mark.integration` case at the bottom of this file.
    """
    if not header:
        return None
    match = _TEST_RANGE.fullmatch(header.strip())
    if match is None:
        return None
    first_s, last_s = match.groups()
    total = len(data)
    if first_s == "":
        length = int(last_s)
        first, last = max(total - length, 0), total - 1
    else:
        first = int(first_s)
        last = min(int(last_s), total - 1) if last_s else total - 1
    if total == 0 or first >= total or first > last:
        return None
    return data[first : last + 1], (first, last)


class FakeGcs:
    """Enough of the GCS JSON API to drive `BundleStore` end to end, in memory."""

    def __init__(self, *, page_size: int = 2) -> None:
        self.objects: dict[str, bytes] = {}
        self.bucket_exists = False
        self.page_size = page_size
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/storage/v1/b":
            return self._create_bucket(request)
        if request.method == "POST" and path.startswith("/upload/storage/v1/b/"):
            return self._upload(request)
        if request.method == "GET" and path.endswith("/o"):
            return self._list(request)
        if request.method == "GET" and "/o/" in path and request.url.params.get("alt") == "media":
            return self._get_media(request)
        if request.method == "GET" and "/o/" in path:
            return self._stat(request)
        if request.method == "DELETE" and "/o/" in path:
            return self._delete(request)
        raise AssertionError(f"unhandled fake-GCS request: {request.method} {path}")

    def _name(self, request: httpx.Request) -> str:
        encoded = request.url.path.rsplit("/o/", 1)[1]
        return unquote(encoded)

    def _create_bucket(self, request: httpx.Request) -> httpx.Response:
        if self.bucket_exists:
            return httpx.Response(409)
        self.bucket_exists = True
        body = json.loads(request.content)
        return httpx.Response(200, json={"kind": "storage#bucket", "name": body["name"]})

    def _upload(self, request: httpx.Request) -> httpx.Response:
        name = request.url.params["name"]
        data = request.content
        self.objects[name] = data
        content_type = request.headers.get("Content-Type", "application/octet-stream")
        return httpx.Response(
            200,
            json={
                "kind": "storage#object",
                "name": name,
                "size": str(len(data)),
                "contentType": content_type,
                "generation": "1786299329631012",
            },
        )

    def _stat(self, request: httpx.Request) -> httpx.Response:
        name = self._name(request)
        data = self.objects.get(name)
        if data is None:
            return httpx.Response(404, text="Not Found")
        return httpx.Response(
            200,
            json={
                "kind": "storage#object",
                "name": name,
                "size": str(len(data)),
                "contentType": "application/octet-stream",
                "generation": "1",
            },
        )

    def _get_media(self, request: httpx.Request) -> httpx.Response:
        name = self._name(request)
        data = self.objects.get(name)
        if data is None:
            return httpx.Response(404, text="Not Found")
        range_header = request.headers.get("Range")
        if range_header is None:
            return httpx.Response(
                200, content=data, headers={"Content-Type": "application/octet-stream"}
            )
        sliced = _test_double_slice(data, range_header)
        if sliced is None:
            return httpx.Response(416, headers={"Content-Range": f"bytes */{len(data)}"})
        body, (first, last) = sliced
        return httpx.Response(
            206,
            content=body,
            headers={
                "Content-Range": f"bytes {first}-{last}/{len(data)}",
                "Content-Type": "application/octet-stream",
            },
        )

    def _list(self, request: httpx.Request) -> httpx.Response:
        prefix = request.url.params.get("prefix", "")
        page_token = request.url.params.get("pageToken")
        names = sorted(n for n in self.objects if n.startswith(prefix))
        start = int(page_token) if page_token else 0
        page = names[start : start + self.page_size]
        payload: dict[str, Any] = {
            "kind": "storage#objects",
            "items": [{"name": n} for n in page],
        }
        next_start = start + self.page_size
        if next_start < len(names):
            payload["nextPageToken"] = str(next_start)
        return httpx.Response(200, json=payload)

    def _delete(self, request: httpx.Request) -> httpx.Response:
        name = self._name(request)
        if name not in self.objects:
            return httpx.Response(404, text="Not Found")
        del self.objects[name]
        return httpx.Response(200, json={})


@pytest.fixture
def fake_gcs() -> FakeGcs:
    return FakeGcs()


def store_over(fake: FakeGcs, **kwargs: Any) -> BundleStore:
    return store_for(fake.handler, **kwargs)


# ── put / stat / get: the roundtrip ─────────────────────────────────────────────────────────


def test_put_infers_content_type_from_the_path_extension(fake_gcs: FakeGcs) -> None:
    store = store_over(fake_gcs)
    metadata = store.put("bnd_1", "content/sites.json", b'{"a":1}')
    assert metadata.content_type == "application/json"
    assert metadata.size_bytes == len(b'{"a":1}')
    assert fake_gcs.objects["bnd_1/content/sites.json"] == b'{"a":1}'


def test_put_honours_an_explicit_content_type_override(fake_gcs: FakeGcs) -> None:
    store = store_over(fake_gcs)
    metadata = store.put("bnd_1", "weird.bin", b"\x00\x01", content_type="application/x-custom")
    assert metadata.content_type == "application/x-custom"


def test_stat_reports_size_and_type_without_moving_bytes(fake_gcs: FakeGcs) -> None:
    store = store_over(fake_gcs)
    store.put("bnd_1", "content/sites.json", b"0123456789")
    metadata = store.stat("bnd_1", "content/sites.json")
    assert metadata.size_bytes == 10
    assert metadata.generation == "1"


def test_stat_on_a_missing_object_raises_object_not_found(fake_gcs: FakeGcs) -> None:
    store = store_over(fake_gcs)
    with pytest.raises(ObjectNotFound):
        store.stat("bnd_1", "content/sites.json")


def test_get_a_missing_object_raises_object_not_found_not_object_store_error(
    fake_gcs: FakeGcs,
) -> None:
    """`ObjectNotFound` is distinct from other store failures — the API turns it into `404`,
    other failures into a `5xx`, so the two must not collapse into one exception type."""
    store = store_over(fake_gcs)
    with pytest.raises(ObjectNotFound):
        store.get("bnd_1", "missing.json")
    assert not issubclass(ObjectStoreError, ObjectNotFound)
    assert issubclass(ObjectNotFound, ObjectStoreError)


def test_get_the_whole_object_when_no_range_is_requested(fake_gcs: FakeGcs) -> None:
    store = store_over(fake_gcs)
    store.put("bnd_1", "a.bin", b"0123456789ab")
    read = store.get("bnd_1", "a.bin")
    assert read.data == b"0123456789ab"
    assert read.total_size == 12
    assert read.served is None
    assert read.is_partial is False
    assert read.content_range is None


def test_get_a_closed_range_returns_a_206_with_content_range(fake_gcs: FakeGcs) -> None:
    store = store_over(fake_gcs)
    store.put("bnd_1", "a.bin", b"0123456789ab")
    read = store.get("bnd_1", "a.bin", ByteRange.of(0, 4))
    assert read.data == b"01234"
    assert read.served == (0, 4)
    assert read.is_partial is True
    assert read.content_range == "bytes 0-4/12"


def test_get_an_out_of_range_request_raises_range_not_satisfiable_with_the_true_size(
    fake_gcs: FakeGcs,
) -> None:
    store = store_over(fake_gcs)
    store.put("bnd_1", "a.bin", b"0123456789ab")
    with pytest.raises(RangeNotSatisfiable) as excinfo:
        store.get("bnd_1", "a.bin", ByteRange.of(100, 200))
    assert excinfo.value.total_size == 12


def test_get_a_416_without_content_range_yields_a_negative_one_size_rather_than_raising() -> None:
    """`_unsatisfied_size` falls back to `-1` when the store doesn't say — this must not raise
    a second, unrelated error while parsing the failure it is already reporting. A 416 with no
    `Content-Range` header at all is a strict test double, not the real emulator's behaviour
    (which always includes it; see the Tier-2 case)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(416)

    store = store_for(handler)
    with pytest.raises(RangeNotSatisfiable) as excinfo:
        store.get("bnd_1", "a.bin", ByteRange.of(0, 10))
    assert excinfo.value.total_size == -1


def test_get_a_200_to_a_ranged_request_satisfies_the_range_locally(fake_gcs: FakeGcs) -> None:
    """An intermediary (or a store declining the form) can answer `200` to a ranged request.
    All the bytes are present, so the range is satisfied client-side rather than failing the
    read — the caller's contract stays exactly right; only bandwidth was wasted."""
    data = b"0123456789ab"

    def ignores_range(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Range") == "bytes=2-5"  # the request was still sent
        return httpx.Response(
            200, content=data, headers={"Content-Type": "application/octet-stream"}
        )

    store = store_for(ignores_range)
    read = store.get("bnd_1", "a.bin", ByteRange.of(2, 5))
    assert read.data == b"2345"
    assert read.served == (2, 5)
    assert read.total_size == 12
    assert read.is_partial is True


def test_get_a_206_with_a_malformed_content_range_falls_back_to_describing_what_arrived() -> None:
    """A `206` without a parseable `Content-Range` still carries real bytes; the fallback
    describes what arrived rather than failing an otherwise-successful read."""
    body = b"56789a"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(206, content=body, headers={"Content-Range": "not-a-content-range"})

    store = store_for(handler)
    read = store.get("bnd_1", "a.bin", ByteRange.of(5, 10))
    assert read.data == body
    assert read.served == (0, len(body) - 1)
    assert read.total_size == len(body)


def test_a_path_that_would_escape_its_bundle_is_refused_before_any_request() -> None:
    store = BundleStore(client=client_for(refuses_any_request), endpoint="http://emulator.test")
    with pytest.raises(ValueError, match="invalid artifact path"):
        store.get("bnd_a", "../bnd_b/secret.json")


# ── list_paths: pagination across nextPageToken, and the territory vs. the manifest ────────


def test_list_paths_walks_every_page_and_strips_the_bundle_prefix(fake_gcs: FakeGcs) -> None:
    store = store_over(fake_gcs)
    fake_gcs.page_size = 2  # force pagination: 5 objects over pages of 2
    for index in range(5):
        fake_gcs.objects[f"bnd_1/artifact_{index}.json"] = b"x"
    # An object under a different bundle must never appear in `bnd_1`'s listing.
    fake_gcs.objects["bnd_2/other.json"] = b"y"

    paths = store.list_paths("bnd_1")

    assert paths == tuple(sorted(f"artifact_{index}.json" for index in range(5)))
    # Three pages were needed for 5 items at page_size=2.
    list_requests = [
        r for r in fake_gcs.requests if r.method == "GET" and r.url.path.endswith("/o")
    ]
    assert len(list_requests) == 3


def test_list_paths_defensively_filters_an_item_outside_the_requested_prefix() -> None:
    """Real GCS's `prefix` listing would never return this, but the code re-checks anyway —
    pin that the check is load-bearing, not decorative."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {"name": "bnd_1/a.json"},
                    {"name": "bnd_OTHER/leaked.json"},  # would leak across bundles if not filtered
                ]
            },
        )

    store = store_for(handler)
    assert store.list_paths("bnd_1") == ("a.json",)


def test_list_paths_on_an_empty_bundle_is_an_empty_tuple(fake_gcs: FakeGcs) -> None:
    store = store_over(fake_gcs)
    assert store.list_paths("bnd_empty") == ()


# ── delete_bundle: idempotent, races with another deleter tolerated ────────────────────────


def test_delete_bundle_removes_every_object_and_counts_them(fake_gcs: FakeGcs) -> None:
    store = store_over(fake_gcs)
    fake_gcs.objects["bnd_1/a.json"] = b"x"
    fake_gcs.objects["bnd_1/b.json"] = b"y"

    removed = store.delete_bundle("bnd_1")

    assert removed == 2
    assert fake_gcs.objects == {}


def test_delete_bundle_tolerates_an_object_already_gone() -> None:
    """A concurrent deleter that already removed one object must not fail the call — the
    desired end state (nothing left) still holds."""
    names = ["bnd_1/a.json", "bnd_1/b.json"]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"items": [{"name": n} for n in names]})
        if request.method == "DELETE":
            if "a.json" in str(request.url):
                return httpx.Response(404)  # raced away already
            return httpx.Response(200, json={})
        raise AssertionError(request)

    store = store_for(handler)
    assert store.delete_bundle("bnd_1") == 1  # only the one that was actually still there


def test_delete_bundle_on_an_empty_bundle_removes_nothing(fake_gcs: FakeGcs) -> None:
    store = store_over(fake_gcs)
    assert store.delete_bundle("bnd_empty") == 0


# ── client lifecycle: an injected client is borrowed, a created one is closed ──────────────


def test_an_injected_client_is_never_closed_by_the_store(monkeypatch: pytest.MonkeyPatch) -> None:
    client = client_for(lambda request: httpx.Response(200, json={"items": []}))

    def forbidden_close() -> None:
        pytest.fail("a caller-supplied client must not be closed by BundleStore")

    monkeypatch.setattr(client, "close", forbidden_close)
    store = BundleStore(client=client, endpoint="http://emulator.test")

    store.list_paths("bnd_1")

    assert client.is_closed is False


def test_a_created_client_is_closed_after_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """When ``client=None``, `_session()` makes one per call and must close it again —
    verified by swapping in a tracked subclass rather than hitting real DNS/network."""
    closed: list[bool] = []

    class TrackedClient(httpx.Client):
        def close(self) -> None:
            closed.append(True)
            super().close()

    def factory(**kwargs: Any) -> httpx.Client:
        empty_list = httpx.Response(200, json={"items": []})
        return TrackedClient(transport=httpx.MockTransport(lambda r: empty_list))

    # Patches the one `httpx` module object both this test and `compiler.storage` import —
    # `storage.py`'s own `import httpx` is not a re-exported attribute (mypy strict), so this
    # goes through the shared module rather than reaching into `compiler.storage.httpx`.
    monkeypatch.setattr(httpx, "Client", factory)

    store = BundleStore(endpoint="http://emulator.test")  # client=None: created per call
    store.list_paths("bnd_1")

    assert closed == [True]


# ── Tier 2: the real `fake-gcs-server` emulator, drives the actual wire behaviour ──────────

_EMULATOR_HOST = "localhost:4443"


@pytest.fixture(scope="module")
def gcs_store() -> Iterator[BundleStore]:
    """A `BundleStore` over the real emulator, or a clean skip if it isn't running.

    Mirrors `tests/conftest.py`'s Postgres fixture in spirit (probe, then skip with the
    start-it hint) but stays local to this file, since `conftest.py` belongs to another
    owner in this session and the emulator here is compose-managed, not testcontainers-spun.
    """
    host = os.environ.get(ENV_EMULATOR_HOST, "").strip() or _EMULATOR_HOST
    endpoint = (host if "://" in host else f"http://{host}").rstrip("/")
    hint = (
        f"no fake-gcs-server reachable at {endpoint}. Start one with "
        "`docker compose --profile compile up -d gcs`."
    )
    probe = httpx.Client(timeout=2.0)
    try:
        response = probe.get(f"{endpoint}/storage/v1/b/{DEFAULT_BUCKET}")
    except httpx.HTTPError as error:
        probe.close()
        pytest.skip(f"{hint} ({type(error).__name__}: {error})")
    probe.close()
    if response.status_code not in (200, 404):
        pytest.skip(f"{hint} (HTTP {response.status_code} on the probe request)")

    bundle_store = BundleStore(endpoint=endpoint)
    bundle_store.ensure_bucket()
    yield bundle_store


@pytest.mark.integration
def test_ranged_reads_against_the_real_emulator_match_the_documented_206_416_contract(
    gcs_store: BundleStore,
) -> None:
    """Verified by hand against the running container before this test was written:
    `bytes=0-4` -> `bytes 0-4/12`; `bytes=6-` -> `bytes 6-11/12`; `bytes=-5` -> `bytes
    7-11/12`; an out-of-range request -> `416` with `Content-Range: bytes */12`."""
    bundle_id = f"test-{uuid4().hex[:8]}"
    path = "probe/object.bin"
    data = b"0123456789ab"  # 12 bytes
    try:
        gcs_store.put(bundle_id, path, data)

        whole = gcs_store.get(bundle_id, path)
        assert whole.data == data
        assert whole.total_size == 12
        assert whole.served is None

        head = gcs_store.get(bundle_id, path, ByteRange.of(0, 4))
        assert head.data == data[0:5]
        assert head.served == (0, 4)
        assert head.content_range == "bytes 0-4/12"

        open_ended = gcs_store.get(bundle_id, path, ByteRange.of(6))
        assert open_ended.data == data[6:]
        assert open_ended.content_range == "bytes 6-11/12"

        suffix = gcs_store.get(bundle_id, path, ByteRange.suffix(5))
        assert suffix.data == data[7:]
        assert suffix.content_range == "bytes 7-11/12"

        with pytest.raises(RangeNotSatisfiable) as excinfo:
            gcs_store.get(bundle_id, path, ByteRange.of(100, 200))
        assert excinfo.value.total_size == 12
    finally:
        gcs_store.delete_bundle(bundle_id)


@pytest.mark.integration
def test_list_and_delete_bundle_against_the_real_emulator(gcs_store: BundleStore) -> None:
    bundle_id = f"test-{uuid4().hex[:8]}"
    try:
        gcs_store.put(bundle_id, "a.json", b"{}")
        gcs_store.put(bundle_id, "sub/b.json", b"{}")

        assert gcs_store.list_paths(bundle_id) == ("a.json", "sub/b.json")
        removed = gcs_store.delete_bundle(bundle_id)
        assert removed == 2
        assert gcs_store.list_paths(bundle_id) == ()
    finally:
        gcs_store.delete_bundle(bundle_id)  # no-op if the above already succeeded
