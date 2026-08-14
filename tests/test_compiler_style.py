"""Unit tests — `compiler/style.py` (T036, Spec 002).

Ground truth: `docs/data/tile-source.md` ("base MapLibre style JSON (no customization at
M1)", carried at `style/base.json` with a `sha256`) and `docs/data/bundle-manifest.md`.

Tier 1: **no network, no node, no container.** The layers are read from the committed
`data/basemap/protomaps-layers.json`, which is what a compile reads too — so these assert the
bytes a real bundle would carry, not a fixture standing in for them.

Four properties here are each a bug that passes every other gate in the pipeline, because all
four fail *silently* on the traveller's device, offline, under a manifest that verifies:

* **Absolute paths.** A style holding `https://…` or `/basemap/…` renders on the machine that
  compiled it and nowhere else — the bundle is read from OPFS, where that origin does not
  exist. The guard runs over the whole assembled document, not over the constants that built
  it, so an upstream release that inlines a hosted URL is caught too.
* **A fontstack the tiles stage never vendored.** MapLibre requests the missing glyph URL and
  draws nothing where text should be. Nothing raises, nothing logs anywhere a traveller can
  see. `test_the_style_only_names_fontstacks_the_tiles_stage_vendors` and its mutation are the
  whole of the protection.
* **An archive name that is not the one the tiles stage wrote.** Same shape: the manifest
  hashes match, the map is empty. Pinned here against `CompileRequest`'s own default.
* **A dependency bumped without regenerating.** `web/package.json` moves, the frozen layers do
  not, and the bundle ships a style built from a version the app no longer uses.

Every guard below is proved to bite by feeding it deliberately wrong input, per FAIL-005: a
test that stays green with its guard removed is not a test.
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import HTTPException, Request

from api.bundles import CompileSeams, _style_artifact, get_compile_seams
from compiler.pipeline import CompileRequest
from compiler.style import (
    BASEMAP_SOURCE_ID,
    DEFAULT_FLAVOR,
    GLYPHS_TEMPLATE,
    LAYERS_PATH,
    STYLE_PATH,
    STYLE_VERSION,
    BasemapLayers,
    StyleError,
    base_style_bytes,
    build_style,
    load_basemap_layers,
    referenced_fontstacks,
    style_bytes,
    unvendored_fontstacks,
)
from compiler.tiles import (
    ARCHIVE_DIR,
    DEFAULT_ARCHIVE_NAME,
    DEFAULT_FONTSTACKS,
    DEFAULT_SPRITE_ASSETS,
    GLYPHS_DIR,
    SPRITES_DIR,
    TILE_ATTRIBUTION,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_PACKAGE_JSON = REPO_ROOT / "web" / "package.json"
GENERATOR = REPO_ROOT / "scripts" / "generate-basemap-style.mjs"

#: The one script font upstream selects per-feature that `DEFAULT_FONTSTACKS` does not
#: vendor. Pinned, not tolerated: see `test_the_unvendored_fontstacks_are_exactly_the_known_gap`.
KNOWN_UNVENDORED: frozenset[str] = frozenset({"Noto Sans Devanagari Regular v1"})


def _strings(node: Any, where: str = "style") -> list[tuple[str, str]]:
    """Every string in the document, with the path it sits at — for the relativity scan."""
    if isinstance(node, dict):
        return [pair for key, value in node.items() for pair in _strings(value, f"{where}.{key}")]
    if isinstance(node, list):
        return [pair for i, item in enumerate(node) for pair in _strings(item, f"{where}[{i}]")]
    return [(where, node)] if isinstance(node, str) else []


# ── the document ──────────────────────────────────────────────────────────────────


def test_the_style_is_a_maplibre_v8_document() -> None:
    style = build_style()
    assert style["version"] == STYLE_VERSION == 8
    assert style["sources"], "a style with no source renders nothing"
    assert style["layers"], "a style with no layers renders nothing"
    assert BASEMAP_SOURCE_ID in style["sources"]
    # Every layer that draws (all but `background`) must name the declared source.
    drawn = {layer.get("source") for layer in style["layers"]} - {None}
    assert drawn == {BASEMAP_SOURCE_ID}


def test_the_style_lives_where_both_schema_cards_say_it_does() -> None:
    """`docs/data/tile-source.md` and `docs/data/bundle-manifest.md` both name this path."""
    assert STYLE_PATH == "style/base.json"


def test_the_style_bakes_in_no_viewport() -> None:
    """FR-001: a `center`/`zoom` in the base style would be the one place-specific value.

    The client frames the map from `TileSourceV1.bbox`, which is computed per day.
    """
    style = build_style()
    assert not {"center", "zoom", "bearing", "pitch"} & set(style)


def test_style_bytes_are_stable_and_parse_back_to_the_document() -> None:
    """The bytes are hashed into `TileSourceV1.style.sha256` and verified on device."""
    first, second = style_bytes(), style_bytes()
    assert first == second
    assert json.loads(first.decode("utf-8")) == build_style()


# ── every path is bundle-relative ─────────────────────────────────────────────────


def test_every_path_in_the_style_is_bundle_relative() -> None:
    """No `http://`, no `https://`, no leading `/` — anywhere in the emitted JSON."""
    for where, value in _strings(build_style()):
        assert "http://" not in value, f"{where} carries an absolute URL: {value!r}"
        assert "https://" not in value, f"{where} carries an absolute URL: {value!r}"
        assert not value.startswith("/"), f"{where} is an origin-relative path: {value!r}"


def test_an_absolute_url_anywhere_in_the_layers_refuses_to_compile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard reads the assembled document, so third-party layers are covered too.

    An upstream release that inlined a hosted sprite or source URL would otherwise ship a
    bundle that renders online and is blank in airplane mode.
    """
    frozen = load_basemap_layers()
    poisoned = BasemapLayers(
        package=frozen.package,
        declared_version=frozen.declared_version,
        resolved_version=frozen.resolved_version,
        source_id=frozen.source_id,
        lang=frozen.lang,
        flavors={
            DEFAULT_FLAVOR: (
                {
                    "id": "hosted",
                    "type": "background",
                    "metadata": {"src": "https://tiles.example"},
                },
            )
        },
    )
    monkeypatch.setattr("compiler.style.load_basemap_layers", lambda: poisoned)
    with pytest.raises(StyleError, match="absolute path"):
        build_style()


def test_the_source_url_is_the_archive_the_tiles_stage_actually_writes() -> None:
    source = build_style()["sources"][BASEMAP_SOURCE_ID]
    assert source["url"] == f"pmtiles://{ARCHIVE_DIR}/{DEFAULT_ARCHIVE_NAME}.pmtiles"
    assert source["type"] == "vector"
    # MapLibre's own attribution accounting must agree with the credit the app renders.
    assert source["attribution"] == TILE_ATTRIBUTION
    assert build_style(archive_name="kyoto")["sources"][BASEMAP_SOURCE_ID]["url"] == (
        f"pmtiles://{ARCHIVE_DIR}/kyoto.pmtiles"
    )


def test_the_default_archive_name_matches_the_one_a_compile_requests() -> None:
    """`api/bundles.py` never passes `archive_name`, so these two defaults must agree.

    If they drift, the style names an archive the bundle does not contain: an empty map under
    a manifest whose hashes all verify.
    """
    # Read off the dataclass field, not the class attribute: `CompileRequest` uses `slots`,
    # where the class attribute is the slot descriptor rather than the default value.
    requested = {field.name: field.default for field in fields(CompileRequest)}["archive_name"]
    assert requested == DEFAULT_ARCHIVE_NAME
    assert build_style()["sources"][BASEMAP_SOURCE_ID]["url"].endswith(f"/{requested}.pmtiles")


def test_the_maplibre_template_tokens_survive_python_literally() -> None:
    """`{fontstack}`/`{range}` are MapLibre's, substituted by MapLibre — not by us."""
    assert GLYPHS_TEMPLATE == f"{GLYPHS_DIR}/{{fontstack}}/{{range}}.pbf"
    style = build_style()
    assert style["glyphs"] == GLYPHS_TEMPLATE
    raw = style_bytes().decode("utf-8")
    assert "glyphs/{fontstack}/{range}.pbf" in raw


# ── fontstacks and sprites: what the tiles stage actually vendored ────────────────


def test_the_style_only_names_fontstacks_the_tiles_stage_vendors() -> None:
    direct, _ = referenced_fontstacks(build_style()["layers"])
    assert direct <= set(DEFAULT_FONTSTACKS)
    assert direct, "a basemap that names no fontstack at all draws no labels"


def test_a_fontstack_the_tiles_stage_does_not_vendor_refuses_to_compile() -> None:
    """The mutation of the guard above: pretend the stage vendors only one weight."""
    with pytest.raises(StyleError, match="does not vendor"):
        build_style(fontstacks=("Noto Sans Regular",))


def test_the_unvendored_fontstacks_are_exactly_the_known_gap() -> None:
    """Upstream picks a Devanagari font per-feature; `DEFAULT_FONTSTACKS` has no such stack.

    Recorded rather than refused (see `compiler/style.py`): it costs labels only in a
    Devanagari-script area, and the fallback branch of the same expression *is* vendored. This
    pins the set so a second script font arriving upstream reddens here instead of shipping a
    silently blank label layer. Closing it is a `compiler/tiles.py` change.
    """
    assert unvendored_fontstacks() == KNOWN_UNVENDORED


def test_the_sprite_names_a_sheet_the_tiles_stage_vendors() -> None:
    style = build_style()
    assert style["sprite"] == f"{SPRITES_DIR}/{DEFAULT_FLAVOR}"
    # MapLibre appends the extensions itself, so the stem must match vendored sheets.
    assert f"{DEFAULT_FLAVOR}.json" in DEFAULT_SPRITE_ASSETS
    assert f"{DEFAULT_FLAVOR}.png" in DEFAULT_SPRITE_ASSETS


def test_a_flavour_with_no_vendored_sprite_sheet_refuses_to_compile() -> None:
    # Flavour named explicitly rather than taken from `DEFAULT_FLAVOR`: this must exercise the
    # guard whichever flavour is the default, not accidentally agree with it.
    with pytest.raises(StyleError, match="no sprite sheet"):
        build_style(flavor="dark", sprite_assets=("light.json", "light.png"))


def test_the_sprite_path_is_relative_even_though_maplibre_wants_it_absolute() -> None:
    """FAIL-007 applies at *render* time, in the client, after the load-time rewrite.

    Baking an absolute URL here is not possible (there is no origin at compile time) and
    would make the bundle non-portable if it were. This pins the reasoning against a
    well-meaning "fix".
    """
    assert not build_style()["sprite"].startswith(("http", "/"))


# ── the frozen layers and their generator ─────────────────────────────────────────


def test_the_frozen_layers_record_the_version_web_packagejson_declares() -> None:
    """Bumping `@protomaps/basemaps` without regenerating is the drift this catches."""
    declared = json.loads(WEB_PACKAGE_JSON.read_text(encoding="utf-8"))["dependencies"]
    frozen = load_basemap_layers()
    assert frozen.package == "@protomaps/basemaps"
    assert frozen.declared_version == declared["@protomaps/basemaps"]
    # `declared` is a `~` range, so the build the layers came from must share major.minor.
    major_minor = ".".join(frozen.declared_version.lstrip("~^=").split(".")[:2])
    assert frozen.resolved_version.startswith(f"{major_minor}.")


def test_the_frozen_file_says_it_is_generated_and_names_its_generator() -> None:
    """AGENTS.md: fix the generator, never hand-edit the output — said in the output."""
    assert GENERATOR.is_file()
    raw = json.loads(LAYERS_PATH.read_text(encoding="utf-8"))
    assert "scripts/generate-basemap-style.mjs" in raw["_generated"]
    assert "DO NOT EDIT" in raw["_generated"]


def test_a_flavour_the_generator_did_not_freeze_is_refused() -> None:
    with pytest.raises(StyleError, match="no frozen layers"):
        build_style(flavor="chartreuse")


def test_frozen_layers_that_are_absent_raise_rather_than_compile_a_bare_map(
    tmp_path: Path,
) -> None:
    with pytest.raises(StyleError, match="missing"):
        load_basemap_layers(tmp_path / "not-generated.json")


def test_frozen_layers_naming_a_different_source_are_refused(tmp_path: Path) -> None:
    """Layers pointing at a source the style does not declare render as an empty map."""
    path = tmp_path / "drifted.json"
    raw = json.loads(LAYERS_PATH.read_text(encoding="utf-8"))
    raw["source_id"] = "somethingelse"
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(StyleError, match="source"):
        load_basemap_layers(path)


# ── the API seam ──────────────────────────────────────────────────────────────────


def _request(**state: Any) -> Request:
    """A stand-in carrying only what `get_compile_seams` reads: `request.app.state`."""
    return cast(Request, SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**state))))


def test_a_deployment_that_injects_nothing_still_gets_a_real_style() -> None:
    """The point of the whole stage: `POST /bundles` stops answering 503 by default."""
    seams = get_compile_seams(_request())
    assert seams.style_path == STYLE_PATH
    assert seams.style_bytes == base_style_bytes()
    assert seams.style_bytes is not None
    assert json.loads(seams.style_bytes)["version"] == STYLE_VERSION


def test_injected_seams_win_over_the_default_style() -> None:
    injected = CompileSeams(style_path="style/base.json", style_bytes=b'{"version":8}')
    assert get_compile_seams(_request(compile_seams=injected)) is injected


def test_a_checkout_that_cannot_build_a_style_degrades_to_the_503_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken build must refuse, not 500 — and must not ship a bundle with a dead map."""

    def broken() -> bytes:
        raise StyleError("the frozen basemap layers are missing")

    monkeypatch.setattr("api.bundles.base_style_bytes", broken)
    seams = get_compile_seams(_request())
    assert seams.style_path is None
    assert seams.style_bytes is None


def test_a_cleared_style_seam_still_answers_503(tmp_path: Path) -> None:
    """The refusal stays reachable. The end-to-end path is `tests/test_api_bundles.py`.

    This is the unit under it, so the Tier-1 suite alone proves a compile with no style is
    refused rather than compiling a bundle whose map cannot start.
    """
    with pytest.raises(HTTPException) as caught:
        _style_artifact(CompileSeams(), tmp_path)
    assert caught.value.status_code == 503
    # Starlette types `detail` as `str`; FastAPI's contract bodies are dicts (`_refuse`).
    detail: Any = caught.value.detail
    assert detail == {"error": "style_unavailable"}


def test_the_default_style_is_written_and_hashed_where_the_pipeline_expects_it(
    tmp_path: Path,
) -> None:
    """`_style_artifact` writes the seam's bytes into staging; the pipeline re-reads them."""
    artifact = _style_artifact(get_compile_seams(_request()), tmp_path)
    assert artifact.path == STYLE_PATH
    written = (tmp_path / STYLE_PATH).read_bytes()
    assert written == base_style_bytes()
    assert artifact.size_bytes == len(written)
