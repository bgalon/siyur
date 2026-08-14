"""Unit tests — `compiler/manifest.py` (T045, Spec 002).

Ground truth: `docs/data/bundle-manifest.md`, `commons/models.py` (`BundleManifestV1`,
`canonical_payload`, `seal`), and `compiler/manifest.py`'s own docstring, which names the
hazard this module exists to prevent — restated here because it is the reason most of these
assertions exist:

**The seal covers WHICH KEYS EXIST, not only their values.** An M1 manifest carries
`"schematic": null`. Serializing with `exclude_none=True`, or any hand-tidied
re-serialization, drops that key — and changes the digest the device recomputes from what
it downloaded. The bundle then reports itself corrupt, **offline, with no other symptom**.
`manifest_json_bytes()` is the one sanctioned path; several tests below exist only to make a
future "tidier" alternative fail loudly instead of shipping that.

Also pinned, because these are exactly where a hand-rolled "canonical JSON" silently
diverges from RFC 8785 (JCS) — the same two divergences `docs/data/bundle-manifest.md` and
ADR-0025 A5 document: the `©` every ODbL attribution carries must survive raw (not
`\\u00a9`-escaped), and an integral bbox bound must render as `28`, not `28.0`.

**Cross-language agreement is the real contract**, not an aspiration: `compiler/manifest.py`
writes with `rfc8785`, and `web/src/bundle/manifest.ts` (T053) verifies with the pinned npm
`canonicalize`. The tests under "cross-language agreement" below build the *actual*
`manifest.ts` to plain ESM with `vite` (library mode, `web/node_modules` as the toolchain —
no reimplementation of its algorithm in Python) and drive it from Node, asserting
byte-for-byte equality against the Python writer. That build needs `node` + a `pnpm -C web
install`, which Tier-1 CI does not guarantee is present *before* this suite runs (`ci.yml`
job 2 runs `pytest tests/` ahead of the web toolchain setup) — so those tests **skip**, not
fail, exactly like the Tier-2 `postgis_url` fixture skips when no database is reachable, when
the toolchain is not on this machine. Wherever it is present (as it was when this file was
written), the assertion is a real, executed byte comparison — never a claim taken on faith.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import rfc8785
from pydantic import ValidationError

from commons.frame import LocalFrame
from commons.models import BundleManifestV1, ItineraryV1, TileSourceV1, WithheldValue
from compiler.manifest import (
    FRAME_KEYS,
    ITINERARY_PATH,
    Artifact,
    build_manifest,
    canonical_manifest_bytes,
    freeze_itinerary,
    manifest_json_bytes,
    read_frozen_itinerary,
)

ITINERARY_ID = UUID("7be20000-0000-4000-8000-000000000042")
AREA_ID = UUID("3a110000-0000-4000-8000-000000000001")
USER_ID = "google-oauth2|1103"
FRAME = LocalFrame(timezone="Europe/Athens", country_code="GR")

WALK_GRAPH_BYTES = b"walk-graph-geojson-bytes"
LEGS_BYTES = b"legs-json-bytes"
SITES_BYTES = b"sites-json-bytes"
NARRATIONS_BYTES = b"narrations-json-bytes"
ATTRIBUTION_BYTES = b"# ATTRIBUTION\n\n\xc2\xa9 OpenStreetMap contributors\n"


def _digest(seed: str) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()


def _itinerary(**overrides: Any) -> ItineraryV1:
    """A minimal, valid `ItineraryV1` — no stops/legs; `ItineraryV1` does not require any
    (ADR-0025 ruling 11), and this module's job is the freeze/hash/seal, not the day's shape.
    """
    payload: dict[str, Any] = {
        "id": str(ITINERARY_ID),
        "user_id": USER_ID,
        "area_id": str(AREA_ID),
        "date": "2026-07-25",
        "lang": "en",
        "schema_ver": "ItineraryV1",
        "budgets": {"walking_m": 0.0, "hours": 0.0},
    }
    payload.update(overrides)
    return ItineraryV1.model_validate(payload)


def _tile_source(**overrides: Any) -> TileSourceV1:
    """`tile-source.md` example row 1 — the whole object `build_manifest` embeds verbatim."""
    payload: dict[str, Any] = {
        "path": "tiles/rhodes.pmtiles",
        "format": "pmtiles",
        "schema_ver": "TileSourceV1",
        "sha256": _digest("pmtiles"),
        "bbox": [28.216, 36.440, 28.232, 36.451],
        "minzoom": 0,
        "maxzoom": 15,
        "build_source": "protomaps-daily",
        "build_date": "2026-07-24",
        "tile_license": "ODbL-1.0",
        "attribution": "© OpenStreetMap contributors",
        "style": {"path": "style/base.json", "sha256": _digest("style")},
        "glyphs": {"path": "glyphs/", "license": "OFL-1.1", "sha256": _digest("glyphs")},
        "sprites": {"path": "sprites/", "license": "MIT", "sha256": _digest("sprites")},
    }
    payload.update(overrides)
    return TileSourceV1.model_validate(payload)


def _artifacts() -> dict[str, Artifact]:
    """The five artifacts `build_manifest` hashes itself (tiles carries its own; the frozen
    itinerary is supplied separately, since its id must come from the same object as its
    bytes — see `_built_manifest`)."""
    return {
        "walk_graph": Artifact.from_bytes("routing/walk_graph.geojson", WALK_GRAPH_BYTES),
        "legs": Artifact.from_bytes("routing/legs.json", LEGS_BYTES),
        "sites": Artifact.from_bytes("content/sites.json", SITES_BYTES),
        "narrations": Artifact.from_bytes("content/narrations.json", NARRATIONS_BYTES),
        "attribution": Artifact.from_bytes("ATTRIBUTION.md", ATTRIBUTION_BYTES),
    }


def _built_manifest(**overrides: Any) -> BundleManifestV1:
    itinerary = overrides.pop("itinerary", None)
    if itinerary is None:
        itinerary = freeze_itinerary(_itinerary(), FRAME)
    fields: dict[str, Any] = {
        "bundle_id": "bnd_01J",
        "itinerary": itinerary,
        "tiles": _tile_source(),
        "tiles_size_bytes": 1_048_576,
        "text_license": "CC-BY-SA-4.0",
        **_artifacts(),
    }
    fields.update(overrides)
    return build_manifest(**fields)


# --- Artifact: one hash per artifact, over raw bytes -------------------------------


def test_artifact_from_bytes_hashes_the_given_bytes() -> None:
    data = b"pmtiles archive bytes"
    artifact = Artifact.from_bytes("tiles/rhodes.pmtiles", data)
    assert artifact.sha256 == hashlib.sha256(data).hexdigest()
    assert artifact.size_bytes == len(data)
    assert artifact.path == "tiles/rhodes.pmtiles"


def test_artifact_from_file_matches_from_bytes_across_a_chunk_boundary(tmp_path: Path) -> None:
    """`_HASH_CHUNK` is 1 MiB; a boundary-crossing size exercises more than one `read()`."""
    data = os.urandom(3 * (1 << 20) + 17)
    source = tmp_path / "archive.bin"
    source.write_bytes(data)
    from_file = Artifact.from_file("tiles/area.pmtiles", source)
    assert from_file.sha256 == hashlib.sha256(data).hexdigest()
    assert from_file.size_bytes == len(data)
    assert from_file == Artifact.from_bytes("tiles/area.pmtiles", data)


# --- T040a: the local frame travels with the day, top-level, alongside `date` ------


def test_frame_keys_are_exactly_timezone_and_country_code() -> None:
    assert FRAME_KEYS == ("timezone", "country_code")


def test_default_itinerary_path_matches_the_card() -> None:
    frozen = freeze_itinerary(_itinerary(), FRAME)
    assert frozen.artifact.path == ITINERARY_PATH == "content/itinerary.json"


def test_freeze_itinerary_accepts_a_custom_path() -> None:
    frozen = freeze_itinerary(_itinerary(), FRAME, path="content/other.json")
    assert frozen.artifact.path == "content/other.json"


def test_freeze_itinerary_writes_the_frame_as_top_level_siblings_of_date() -> None:
    frozen = freeze_itinerary(_itinerary(), FRAME)
    payload = json.loads(frozen.data)
    assert payload["timezone"] == FRAME.timezone
    assert payload["country_code"] == FRAME.country_code
    assert "date" in payload
    # Siblings, not a nested frame object — the card's rule ("never nest under a field
    # ItineraryV1 does not have") is what makes "alongside date" the right place to look.
    assert "frame" not in payload


def test_frozen_itinerary_artifact_hash_matches_its_bytes() -> None:
    frozen = freeze_itinerary(_itinerary(), FRAME)
    assert frozen.artifact.sha256 == hashlib.sha256(frozen.data).hexdigest()
    assert frozen.artifact.size_bytes == len(frozen.data)
    assert frozen.itinerary_id == ITINERARY_ID


def test_frozen_itinerary_is_deliberately_not_a_valid_itinerary_payload() -> None:
    """The consequence stated in the module docstring: `StampedModel` is `extra='forbid'`,
    so a reader that tries `ItineraryV1.model_validate` straight on the artifact — the
    obvious thing to try — must fail, not silently succeed with the frame keys stripped."""
    frozen = freeze_itinerary(_itinerary(), FRAME)
    with pytest.raises(ValidationError):
        ItineraryV1.model_validate(json.loads(frozen.data))


def test_read_frozen_itinerary_round_trips() -> None:
    original = _itinerary()
    frozen = freeze_itinerary(original, FRAME)
    itinerary, frame = read_frozen_itinerary(frozen.data)
    assert itinerary == original
    assert frame == FRAME


def test_read_frozen_itinerary_refuses_a_missing_frame() -> None:
    bare = rfc8785.dumps(_itinerary().model_dump(mode="json"))
    with pytest.raises(ValueError, match="local frame"):
        read_frozen_itinerary(bare)


def test_read_frozen_itinerary_refuses_a_partial_frame() -> None:
    payload = _itinerary().model_dump(mode="json")
    payload["timezone"] = "Europe/Athens"  # country_code deliberately left out
    with pytest.raises(ValueError, match="country_code"):
        read_frozen_itinerary(rfc8785.dumps(payload))


def test_read_frozen_itinerary_refuses_a_non_object_payload() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        read_frozen_itinerary(b"[1, 2, 3]")


def test_freeze_itinerary_refuses_a_schema_that_already_carries_a_frame_key() -> None:
    """The collision guard can't be reached through a real `ItineraryV1` today — the model
    has no `timezone`/`country_code` field and is `extra='forbid'`, so pydantic itself would
    refuse the payload before `freeze_itinerary` ever saw it. Duck-typing the minimal surface
    `freeze_itinerary` reads (`.id`, `.model_dump`) is the only way to exercise the guard, and
    is deliberate: it pins the guard against the day the schema *does* grow one of these
    fields, which is exactly when the check stops being unreachable.
    """

    class _DriftedItinerary:
        id = ITINERARY_ID

        def model_dump(self, mode: str = "json") -> dict[str, Any]:
            return {"date": "2026-07-25", "timezone": "already-here"}

    with pytest.raises(ValueError, match="timezone"):
        freeze_itinerary(_DriftedItinerary(), FRAME)  # type: ignore[arg-type]


# --- build_manifest: per-artifact hashes, totals, linkage, refusals ----------------


def test_every_artifact_hash_matches_its_actual_bytes() -> None:
    itinerary = freeze_itinerary(_itinerary(), FRAME)
    manifest = _built_manifest(itinerary=itinerary)

    assert manifest.routing.walk_graph_sha256 == hashlib.sha256(WALK_GRAPH_BYTES).hexdigest()
    assert manifest.routing.legs_sha256 == hashlib.sha256(LEGS_BYTES).hexdigest()
    assert manifest.content.sites_sha256 == hashlib.sha256(SITES_BYTES).hexdigest()
    assert manifest.content.narrations_sha256 == hashlib.sha256(NARRATIONS_BYTES).hexdigest()
    assert manifest.attribution.sha256 == hashlib.sha256(ATTRIBUTION_BYTES).hexdigest()
    assert manifest.content.itinerary_sha256 == hashlib.sha256(itinerary.data).hexdigest()
    assert manifest.content.itinerary == itinerary.artifact.path


def test_build_manifest_totals_size_bytes_excluding_the_manifest_itself() -> None:
    itinerary = freeze_itinerary(_itinerary(), FRAME)
    manifest = _built_manifest(itinerary=itinerary, tiles_size_bytes=1_048_576)
    expected = (
        1_048_576
        + len(WALK_GRAPH_BYTES)
        + len(LEGS_BYTES)
        + len(SITES_BYTES)
        + len(NARRATIONS_BYTES)
        + len(ATTRIBUTION_BYTES)
        + len(itinerary.data)
    )
    assert manifest.size_bytes == expected


def test_build_manifest_refuses_two_artifacts_at_one_bundle_path() -> None:
    with pytest.raises(ValueError, match="share a bundle path"):
        _built_manifest(
            sites=Artifact.from_bytes("content/shared.json", SITES_BYTES),
            narrations=Artifact.from_bytes("content/shared.json", NARRATIONS_BYTES),
        )


def test_the_sprites_path_is_inside_the_uniqueness_check_too() -> None:
    """A *new* bundle path is a new way for two artifacts to claim one path, so `sprites`
    joins the refusal rather than being trusted to differ from `glyphs` (ADR-0031)."""
    collided = _tile_source(
        sprites={"path": "glyphs/", "license": "MIT", "sha256": _digest("sprites")}
    )
    with pytest.raises(ValueError, match="share a bundle path"):
        _built_manifest(tiles=collided)


def test_build_manifest_itinerary_id_comes_from_the_frozen_itinerary() -> None:
    """`FrozenItinerary` carries both the id and the bytes precisely so they cannot name
    different days — passing the id separately would let them disagree silently."""
    other = _itinerary(id=str(uuid4()))
    frozen = freeze_itinerary(other, FRAME)
    manifest = _built_manifest(itinerary=frozen)
    assert manifest.itinerary_id == frozen.itinerary_id == other.id


def test_build_manifest_created_at_defaults_to_now_utc() -> None:
    before = datetime.now(UTC)
    manifest = _built_manifest()
    after = datetime.now(UTC)
    assert before <= manifest.created_at <= after


def test_build_manifest_created_at_explicit_is_used_verbatim() -> None:
    stamped = datetime(2026, 7, 25, 12, 30, tzinfo=UTC)
    manifest = _built_manifest(created_at=stamped)
    assert manifest.created_at == stamped


def test_build_manifest_withheld_defaults_to_empty_meaning_nothing_withheld() -> None:
    assert _built_manifest().withheld == ()


def test_build_manifest_records_withheld_values_verbatim() -> None:
    withheld = (
        WithheldValue(site_id=uuid4(), field="reviews", reason="license_forbids_redistribution"),
    )
    manifest = _built_manifest(withheld=withheld)
    assert manifest.withheld == withheld


def test_build_manifest_text_license_is_recorded_as_given() -> None:
    assert _built_manifest(text_license="CC-BY-SA-4.0").textLicense == "CC-BY-SA-4.0"
    assert _built_manifest(text_license=None).textLicense is None


def test_build_manifest_seals_a_self_verifying_manifest() -> None:
    assert _built_manifest().verify_manifest_sha256() is True


# --- a mutated artifact is detected -------------------------------------------------


def test_a_swapped_artifact_changes_the_sealed_digest() -> None:
    baseline = _built_manifest()
    swapped = _built_manifest(
        sites=Artifact.from_bytes("content/sites.json", b"different-sites-bytes")
    )
    assert swapped.content.sites_sha256 != baseline.content.sites_sha256
    assert swapped.integrity.manifest_sha256 != baseline.integrity.manifest_sha256


def test_a_manifest_tampered_after_sealing_fails_its_own_check() -> None:
    """FR-020, exercised through this module's own seal rather than a hand-built manifest."""
    manifest = _built_manifest()
    tampered = manifest.model_copy(
        update={"content": manifest.content.model_copy(update={"sites_sha256": _digest("evil")})}
    )
    assert tampered.verify_manifest_sha256() is False


# --- canonicalization: JCS, `integrity` removed, key order irrelevant --------------


def test_canonical_manifest_bytes_is_exactly_the_models_own_canonicalization() -> None:
    """A two-call convenience over `rfc8785` + `canonical_payload`, not a second
    canonicalizer — pinned so a future edit can't quietly hand-roll a variant here."""
    manifest = _built_manifest()
    assert canonical_manifest_bytes(manifest) == rfc8785.dumps(manifest.canonical_payload())


def test_integrity_is_absent_from_the_canonical_bytes_not_null_or_empty() -> None:
    manifest = _built_manifest()
    assert b'"integrity"' not in canonical_manifest_bytes(manifest)
    # ...and present in the document actually shipped as manifest.json.
    assert b'"integrity":{"manifest_sha256":"' in manifest_json_bytes(manifest)


def test_canonical_bytes_carry_the_odbl_symbol_raw_not_escaped() -> None:
    canonical = canonical_manifest_bytes(_built_manifest())
    assert "©".encode() in canonical
    assert b"\\u00a9" not in canonical


def test_canonical_bytes_render_an_integral_bbox_bound_without_a_trailing_zero() -> None:
    manifest = _built_manifest(tiles=_tile_source(bbox=[28.0, 36.0, 28.5, 36.5]))
    canonical = canonical_manifest_bytes(manifest)
    assert b'"bbox":[28,36,28.5,36.5]' in canonical
    assert b"28.0" not in canonical


def test_the_digest_is_stable_under_key_reordering() -> None:
    """What the TypeScript verifier needs: it rebuilds the object from parsed JSON, whose key
    order is whatever the parser produced."""
    manifest = _built_manifest()
    payload = manifest.canonical_payload()
    shuffled = dict(reversed(list(payload.items())))
    assert list(shuffled) != list(payload)
    reordered_digest = hashlib.sha256(rfc8785.dumps(shuffled)).hexdigest()
    assert reordered_digest == manifest.computed_manifest_sha256()


def test_manifest_json_bytes_is_not_the_hand_rolled_sorted_keys_alternative() -> None:
    """ADR-0025 A5's documented defect, run against this module's own writer."""
    manifest = _built_manifest(tiles=_tile_source(bbox=[28.0, 36.0, 28.5, 36.5]))
    shipped = manifest_json_bytes(manifest)

    hand_rolled = json.dumps(
        manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()
    assert hand_rolled != shipped
    assert b"\\u00a9" in hand_rolled and b"\\u00a9" not in shipped
    assert b'"bbox":[28.0,' in hand_rolled and b'"bbox":[28,' in shipped


def test_manifest_json_bytes_is_the_sanctioned_path_exclude_none_breaks_the_launch_check() -> None:
    """The hazard `compiler/manifest.py`'s module docstring names as the reason it exists:
    an M1 manifest carries `"schematic": null`, and `exclude_none=True` — or any hand-tidied
    re-serialization — silently drops that key. The device then downloads a document whose
    own recomputed digest disagrees with the `integrity.manifest_sha256` shipped inside it,
    and reports the bundle corrupt with no other symptom.
    """
    manifest = _built_manifest()
    shipped = manifest_json_bytes(manifest)
    assert BundleManifestV1.model_validate(json.loads(shipped)) == manifest
    assert b'"schematic":null' in shipped

    tidied = rfc8785.dumps(manifest.model_dump(mode="json", exclude_none=True))
    assert b'"schematic"' not in tidied, "exclude_none=True silently dropped the null key"

    reparsed_tidied = json.loads(tidied)
    digest_a_device_would_recompute = hashlib.sha256(
        rfc8785.dumps({k: v for k, v in reparsed_tidied.items() if k != "integrity"})
    ).hexdigest()
    assert digest_a_device_would_recompute != manifest.integrity.manifest_sha256, (
        "a tidied manifest.json recomputes to a different digest than the one shipped inside "
        "it, so the launch-time check reports every bundle corrupt, offline, for no reason a "
        "traveller can diagnose — exactly the failure manifest_json_bytes() exists to prevent"
    )


# --- cross-language agreement: the actual web/src/bundle/manifest.ts, driven from Node ---

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
VITE_BIN = WEB_DIR / "node_modules" / ".bin" / "vite"
MANIFEST_TS = WEB_DIR / "src" / "bundle" / "manifest.ts"
CANONICALIZE_PKG = WEB_DIR / "node_modules" / "canonicalize"


def _node_toolchain_available() -> bool:
    return (
        shutil.which("node") is not None
        and VITE_BIN.exists()
        and CANONICALIZE_PKG.exists()
        and MANIFEST_TS.exists()
    )


@pytest.fixture(scope="session")
def ts_manifest_module(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the *actual* `web/src/bundle/manifest.ts` to plain ESM (`vite`, library mode,
    types stripped, nothing reimplemented) so its exported `canonicalManifestBytes` /
    `manifestDigest` / `verifyManifestDigest` can be driven from Node and compared
    byte-for-byte against the Python writer. `canonicalize` (the one runtime dependency the
    module has) is kept external and wired in via a `node_modules` symlink, so the digest
    algorithm under test is exactly the pinned npm package, not a Python stand-in for it.

    Session-scoped: this is a fixed input to every test below, built once. Skips — never
    fails — when `node`/`vite`/`web/node_modules` are not present (see module docstring).
    """
    if not _node_toolchain_available():
        pytest.skip(
            "node/vite/web-node_modules not available — cannot build/drive the actual "
            "TypeScript verifier from this Tier-1 test (see module docstring); run "
            "`pnpm -C web install` to enable these cases"
        )
    build_dir = tmp_path_factory.mktemp("ts_manifest_build")
    config = build_dir / "vite.lib.config.mjs"
    config.write_text(
        "import { resolve } from 'node:path'\n"
        "export default {\n"
        f"  root: {json.dumps(str(WEB_DIR))},\n"
        "  build: {\n"
        f"    outDir: resolve({json.dumps(str(build_dir))}, 'dist'),\n"
        "    emptyOutDir: true,\n"
        "    lib: {\n"
        f"      entry: {json.dumps(str(MANIFEST_TS))},\n"
        "      formats: ['es'],\n"
        "      fileName: () => 'manifest.mjs',\n"
        "    },\n"
        "    rollupOptions: { external: ['canonicalize'] },\n"
        "    minify: false,\n"
        "  },\n"
        "}\n"
    )
    result = subprocess.run(
        [str(VITE_BIN), "build", "--config", str(config)],
        cwd=build_dir,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"vite build of manifest.ts failed:\n{result.stdout}\n{result.stderr}")

    node_modules = build_dir / "node_modules"
    node_modules.mkdir(exist_ok=True)
    canonicalize_link = node_modules / "canonicalize"
    if not canonicalize_link.exists():
        canonicalize_link.symlink_to(CANONICALIZE_PKG)
    return build_dir / "dist" / "manifest.mjs"


def _run_ts_manifest_check(
    module_path: Path, tmp_path: Path, raw_manifest: dict[str, Any]
) -> dict[str, Any]:
    """Run the built `manifest.mjs`'s own `canonicalManifestBytes`/`manifestDigest`/
    `verifyManifestDigest` over `raw_manifest`, and return what it computed.

    `module_path` is imported by an absolute `file://` URL, which Node resolves regardless of
    where this driver script lives — `canonicalize` (the module's one bare import) resolves
    relative to *`manifest.mjs`'s own* location, not this script's, so the two can be in
    unrelated directories without breaking module resolution.
    """
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps(raw_manifest))
    script = tmp_path / "check.mjs"
    script.write_text(
        f"import {{ canonicalManifestBytes, manifestDigest, verifyManifestDigest }} "
        f"from {json.dumps(module_path.resolve().as_uri())}\n"
        "import { readFileSync } from 'node:fs'\n"
        f"const raw = JSON.parse(readFileSync({json.dumps(str(payload_path))}, 'utf-8'))\n"
        "const canonical = Buffer.from(canonicalManifestBytes(raw)).toString('utf-8')\n"
        "const digest = await manifestDigest(raw)\n"
        "const verified = await verifyManifestDigest(raw)\n"
        "process.stdout.write(JSON.stringify({ canonical, digest, verified }))\n"
    )
    result = subprocess.run(
        ["node", str(script)], capture_output=True, text=True, timeout=30, check=False
    )
    if result.returncode != 0:
        pytest.fail(f"node manifest check failed:\n{result.stdout}\n{result.stderr}")
    parsed: dict[str, Any] = json.loads(result.stdout)
    return parsed


def test_ts_verifier_canonicalizes_identically_to_the_python_writer(
    ts_manifest_module: Path, tmp_path: Path
) -> None:
    """The real cross-language check: the shipped `manifest.json` bytes, parsed exactly as a
    device would parse them, re-canonicalized by the *actual* TypeScript module — asserted
    byte-for-byte and digest-for-digest against the Python side, over a payload carrying both
    documented divergences (`©` and an integral bbox bound) at once."""
    manifest = _built_manifest(tiles=_tile_source(bbox=[28.0, 36.0, 28.5, 36.5]))
    raw = json.loads(manifest_json_bytes(manifest))

    result = _run_ts_manifest_check(ts_manifest_module, tmp_path, raw)

    assert result["canonical"].encode("utf-8") == canonical_manifest_bytes(manifest)
    assert result["digest"] == manifest.computed_manifest_sha256()
    assert result["verified"] is True


def test_ts_verifier_and_python_agree_a_tampered_manifest_fails(
    ts_manifest_module: Path, tmp_path: Path
) -> None:
    manifest = _built_manifest()
    raw = json.loads(manifest_json_bytes(manifest))
    tampered = {**raw, "size_bytes": raw["size_bytes"] + 1}

    py_tampered = BundleManifestV1.model_validate(tampered)
    result = _run_ts_manifest_check(ts_manifest_module, tmp_path, tampered)

    assert py_tampered.verify_manifest_sha256() is False
    assert result["verified"] is False
