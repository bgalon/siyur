"""The base MapLibre style stage — every path in it bundle-relative (T036).

Second stage of the compile order (`compiler/AGENTS.md`, tech-design §5.3) and the one
the tiles stage takes as an input: :func:`compiler.tiles.compile_tiles` hashes the style
artifact into ``TileSourceV1.style`` but deliberately does not build it. Ground truth is
[`docs/data/tile-source.md`](../docs/data/tile-source.md) ("base MapLibre style JSON, no
customization at M1") and [`docs/data/bundle-manifest.md`](../docs/data/bundle-manifest.md),
which fixes the bundle path at ``style/base.json``.

## The layers are frozen upstream output, not a Python transcription

``@protomaps/basemaps`` is the style's source, the same package
``web/src/map/basemap.ts`` calls for the dev map. Its ``layers()`` output is ~65 KB of
paint and filter expressions per flavour and changes with every release, so porting it
into Python would be a second source of truth that drifts *silently* — the map still
renders, just not the same map. Instead ``scripts/generate-basemap-style.mjs`` freezes it
into :data:`LAYERS_PATH` as committed JSON, and this module reads that file. The frozen
file records the ``@protomaps/basemaps`` version it came from and
``tests/test_compiler_style.py`` pins that against ``web/package.json``, because bumping
the dependency without regenerating is exactly the drift this arrangement invites.

Fix the generator, never hand-edit ``data/basemap/`` (AGENTS.md).

## Every path is bundle-relative, and that is load-bearing

The style ships **inside** the bundle and is read offline from OPFS, where the origin it
was compiled under does not exist and may never have. So it stores relative paths —

* ``sources.<id>.url`` → ``pmtiles://tiles/<archive>.pmtiles``
* ``glyphs``           → ``glyphs/{fontstack}/{range}.pbf``
* ``sprite``           → ``sprites/<flavour>``

— and the travel client rewrites them against its own OPFS/blob origin at load (DU-06).
:func:`_refuse_absolute_paths` enforces it on the emitted document rather than trusting
the constants, because an absolute URL is not a rendering bug on the developer's machine;
it is a bundle that renders on the machine that compiled it and nowhere else.

**This is why the style's ``sprite`` is relative even though MapLibre rejects a relative
one.** MapLibre throws ``Invalid sprite URL "…", must be absolute`` during ``_loadSprite``
— that is FAIL-007, and ``web/src/map/basemap.ts`` documents it. That constraint applies
at *render* time, after the client has rewritten the path against a real origin. It is not
a reason to bake an absolute URL in here: at compile time there is no origin to bake, and
one baked anyway would make the bundle non-portable between devices and hosts. If a future
reader "fixes" the sprite path to an absolute URL because MapLibre complained, they will
have moved the failure from a place where it is caught to a place where it is not.

``{fontstack}`` and ``{range}`` are **MapLibre's own template tokens** and must survive
into the JSON literally — hence :data:`GLYPHS_TEMPLATE` is a plain constant and never a
format string with those names substituted away.

## Nothing here names a place, or a font the tiles stage did not write

There is no ``center``, ``zoom`` or ``bearing`` in the emitted style: a viewport baked
into the base style would be the one genuinely place-specific value a generic compiler
could emit (FR-001), and the client already frames the map from
``TileSourceV1.bbox``.

The fontstack check is the other half. A style naming a fontstack
:data:`compiler.tiles.DEFAULT_FONTSTACKS` does not vendor is a map whose labels are
simply absent — MapLibre requests the missing glyph URL, fails, and draws nothing where
text should be, reporting the failure to a console nobody is holding in airplane mode. So
:func:`build_style` **raises** when a layer names an unvendored stack unconditionally.

Upstream also selects a fontstack *conditionally*, per feature: a ``["case", ["==",
["get", "script"], "Devanagari"], …]`` expression picks ``Noto Sans Devanagari Regular
v1`` for Devanagari-script labels and ``Noto Sans Regular`` for the rest. That stack is
**not** in ``DEFAULT_FONTSTACKS``, so a Devanagari-script area (FR-001 says any area)
currently compiles a bundle whose Devanagari labels have no glyphs. Refusing to compile
would be wrong — every non-Devanagari area is unaffected, and the fallback branch of the
same expression is vendored — so this module reports the gap
(:func:`unvendored_fontstacks`, logged once per compile) instead of hiding it, and
``tests/test_compiler_style.py`` pins the exact set so a new script font arriving upstream
is visible rather than silent. Closing it is a ``compiler/tiles.py`` change (that module
owns ``DEFAULT_FONTSTACKS`` and the vendoring loop), not a style one.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

import rfc8785

from compiler.tiles import (
    ARCHIVE_DIR,
    DEFAULT_ARCHIVE_NAME,
    DEFAULT_FONTSTACKS,
    DEFAULT_SPRITE_ASSETS,
    GLYPHS_DIR,
    SPRITES_DIR,
    TILE_ATTRIBUTION,
)

__all__ = [
    "BASEMAP_SOURCE_ID",
    "DEFAULT_FLAVOR",
    "GLYPHS_TEMPLATE",
    "LAYERS_PATH",
    "STYLE_DIR",
    "STYLE_PATH",
    "STYLE_VERSION",
    "BasemapLayers",
    "StyleError",
    "base_style_bytes",
    "build_style",
    "load_basemap_layers",
    "referenced_fontstacks",
    "style_bytes",
    "unvendored_fontstacks",
]

_log = logging.getLogger(__name__)


# --------------------------------------------------------------------------------------
# Constants — the card's paths and the generator's output, nothing place-specific
# --------------------------------------------------------------------------------------

#: Bundle-relative home of the style. ``docs/data/bundle-manifest.md`` and every example
#: row in ``docs/data/tile-source.md`` name ``style/base.json`` exactly.
STYLE_DIR: Final = "style"
STYLE_PATH: Final = f"{STYLE_DIR}/base.json"

#: MapLibre style spec version. The only one MapLibre GL JS 5.x accepts.
STYLE_VERSION: Final = 8

#: Frozen upstream layers — written by ``scripts/generate-basemap-style.mjs``.
LAYERS_PATH: Final = (
    Path(__file__).resolve().parents[1] / "data" / "basemap" / "protomaps-layers.json"
)

#: Style-internal id of the vector source, baked into every frozen layer's ``source``.
#: Must equal the generator's ``SOURCE_ID``; :func:`load_basemap_layers` checks it rather
#: than assuming, so a generator edit cannot leave the layers pointing at a source the
#: style does not declare — which renders as an empty map with no error.
BASEMAP_SOURCE_ID: Final = "protomaps"

#: Matches the app's own dark surface (``web/src/map/basemap.ts`` defaults the same way).
DEFAULT_FLAVOR: Final = "dark"

#: ``{fontstack}`` and ``{range}`` are MapLibre's tokens, substituted by MapLibre at
#: request time. They are literal text here — never a Python format placeholder.
GLYPHS_TEMPLATE: Final = f"{GLYPHS_DIR}/{{fontstack}}/{{range}}.pbf"

#: What ``_refuse_absolute_paths`` rejects anywhere in the emitted document.
_ABSOLUTE_PREFIXES: Final = ("http://", "https://", "//", "/")


class StyleError(Exception):
    """The style stage cannot produce a usable style. Never caught to continue."""


# --------------------------------------------------------------------------------------
# The frozen layer set
# --------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BasemapLayers:
    """``data/basemap/protomaps-layers.json``, parsed — the generator's output as data."""

    package: str
    #: The version range ``web/package.json`` declares, e.g. ``~5.7.2``. This is what the
    #: drift test compares, so it can run without ``node_modules``.
    declared_version: str
    #: The build actually installed when the file was generated, e.g. ``5.7.2``.
    resolved_version: str
    source_id: str
    #: BCP-47 primary subtag the label expressions were generated for. The Protomaps
    #: schema falls back to a feature's local ``name``, so this is a presentation default
    #: and not a claim about where the bundle is.
    lang: str
    flavors: Mapping[str, tuple[Mapping[str, Any], ...]]

    def layers(self, flavor: str) -> tuple[Mapping[str, Any], ...]:
        try:
            return self.flavors[flavor]
        except KeyError:
            raise StyleError(
                f"no frozen layers for flavour {flavor!r}; "
                f"{LAYERS_PATH.name} has {', '.join(sorted(self.flavors))}. Add it to the "
                "generator's FLAVORS and regenerate — never hand-edit the output"
            ) from None


@lru_cache(maxsize=1)
def load_basemap_layers(path: Path = LAYERS_PATH) -> BasemapLayers:
    """Read and validate the generator's output. Cached — it is committed, not dynamic."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise StyleError(
            f"the frozen basemap layers are missing from {path}. They are committed repo "
            "data, so this is a broken checkout or an unbuilt one: run "
            "`pnpm -C web install && node scripts/generate-basemap-style.mjs`"
        ) from exc
    except ValueError as exc:
        raise StyleError(f"the frozen basemap layers at {path} are not valid JSON: {exc}") from exc

    try:
        package = raw["package"]
        flavors = {flavor: tuple(layers) for flavor, layers in sorted(raw["flavors"].items())}
        loaded = BasemapLayers(
            package=package["name"],
            declared_version=package["declared"],
            resolved_version=package["resolved"],
            source_id=raw["source_id"],
            lang=raw["lang"],
            flavors=flavors,
        )
    except (KeyError, TypeError, AttributeError) as exc:
        raise StyleError(f"the frozen basemap layers at {path} are malformed: {exc}") from exc

    if loaded.source_id != BASEMAP_SOURCE_ID:
        raise StyleError(
            f"the frozen layers name source {loaded.source_id!r} but this module declares "
            f"{BASEMAP_SOURCE_ID!r}. A style whose layers point at a source it does not "
            "declare renders as an empty map and reports nothing"
        )
    if not loaded.flavors or not all(loaded.flavors.values()):
        raise StyleError(f"the frozen basemap layers at {path} contain an empty flavour")
    return loaded


# --------------------------------------------------------------------------------------
# Fontstacks — what the layers ask for versus what the tiles stage writes
# --------------------------------------------------------------------------------------


def _collect_fontstacks(node: Any, direct: set[str], conditional: set[str]) -> None:
    """Walk a layer for ``text-font`` values, sorting them into the two kinds.

    A plain ``["Noto Sans Italic"]`` is **direct**: MapLibre asks for that stack on every
    feature the layer draws. Anything else is a MapLibre expression, and the stacks inside
    it are whatever its ``["literal", [...]]`` branches name — reached only by the features
    that select that branch, which is why they are counted separately rather than raising.
    """
    if isinstance(node, Mapping):
        for key, value in node.items():
            if key == "text-font":
                if isinstance(value, list) and all(isinstance(item, str) for item in value):
                    direct.update(value)
                else:
                    _collect_literal_strings(value, conditional)
            else:
                _collect_fontstacks(value, direct, conditional)
    elif isinstance(node, list):
        for item in node:
            _collect_fontstacks(item, direct, conditional)


def _collect_literal_strings(node: Any, into: set[str]) -> None:
    """Every string inside a ``["literal", [...]]`` branch of an expression.

    Only ``literal`` payloads count: the operators and property names an expression is
    built from (``"case"``, ``"get"``, ``"script"``, ``"Devanagari"``) are strings too, and
    treating them as fontstacks would report nonsense.
    """
    if isinstance(node, list):
        if len(node) == 2 and node[0] == "literal" and isinstance(node[1], list):
            into.update(item for item in node[1] if isinstance(item, str))
            return
        for item in node:
            _collect_literal_strings(item, into)


def referenced_fontstacks(
    layers: Iterable[Mapping[str, Any]],
) -> tuple[frozenset[str], frozenset[str]]:
    """``(direct, conditional)`` — the fontstacks a layer set names, by how it names them."""
    direct: set[str] = set()
    conditional: set[str] = set()
    for layer in layers:
        _collect_fontstacks(layer, direct, conditional)
    return frozenset(direct), frozenset(conditional - direct)


def unvendored_fontstacks(
    *,
    flavor: str = DEFAULT_FLAVOR,
    fontstacks: Sequence[str] = DEFAULT_FONTSTACKS,
) -> frozenset[str]:
    """Conditionally-referenced stacks the tiles stage does not vendor (module docstring).

    Non-empty today — upstream's per-script font selection names a Devanagari stack that
    ``DEFAULT_FONTSTACKS`` does not include. Reported rather than raised, and pinned by a
    test, so the gap is visible without blocking every area that is not affected by it.
    """
    _, conditional = referenced_fontstacks(load_basemap_layers().layers(flavor))
    return frozenset(conditional - set(fontstacks))


def _refuse_unvendored_fontstacks(
    layers: Iterable[Mapping[str, Any]], fontstacks: Sequence[str]
) -> None:
    direct, conditional = referenced_fontstacks(layers)
    available = set(fontstacks)
    missing = sorted(direct - available)
    if missing:
        raise StyleError(
            f"the style names fontstack(s) {', '.join(missing)} that the tiles stage does "
            f"not vendor (it writes {', '.join(fontstacks)}). Every label drawn with them "
            "would render as nothing at all, offline, with the failure reported only to a "
            "console — so this refuses to compile instead"
        )
    unvendored = sorted(conditional - available)
    if unvendored:
        _log.warning(
            "style references %d unvendored fontstack(s) in per-script expressions: %s — "
            "labels selecting those branches will render blank (see compiler/style.py)",
            len(unvendored),
            ", ".join(unvendored),
        )


# --------------------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------------------


def _refuse_absolute_paths(style: Mapping[str, Any]) -> None:
    """No absolute URL may reach the bundle — the reason is in the module docstring.

    Checked on the assembled document, not on the constants that built it, because the
    layers are third-party data: an upstream release that starts inlining a hosted sprite
    or a remote source URL would otherwise ship a style that only renders online.
    """

    def walk(node: Any, where: str) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                walk(value, f"{where}.{key}")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{where}[{index}]")
        elif isinstance(node, str) and node.startswith(_ABSOLUTE_PREFIXES):
            raise StyleError(
                f"{where} is the absolute path {node!r}. Every path in a bundled style is "
                "relative to the bundle root: the travel client reads it from OPFS, where "
                "the origin it was compiled under does not exist"
            )

    walk(style, "style")


def build_style(
    *,
    archive_name: str = DEFAULT_ARCHIVE_NAME,
    flavor: str = DEFAULT_FLAVOR,
    fontstacks: Sequence[str] = DEFAULT_FONTSTACKS,
    sprite_assets: Sequence[str] = DEFAULT_SPRITE_ASSETS,
) -> dict[str, Any]:
    """The bundle's ``style/base.json`` as a document — all paths bundle-relative.

    Args:
        archive_name: the tiles stage's archive stem, so the source url is the archive
            that stage actually wrote (``tiles/<archive_name>.pmtiles``). Both default to
            :data:`~compiler.tiles.DEFAULT_ARCHIVE_NAME`; a caller that overrides one and
            not the other gets a style pointing at an archive that is not in the bundle.
        flavor: which frozen flavour to render, and which sprite sheet to name.
        fontstacks / sprite_assets: what the tiles stage vendors. Parameters rather than
            direct constant reads so the guards below can be exercised against a
            deliberately wrong set.
    """
    frozen = load_basemap_layers()
    layers = frozen.layers(flavor)
    _refuse_unvendored_fontstacks(layers, fontstacks)

    # MapLibre appends `.json`/`.png` (and the `@2x` variants) to the sprite base, so the
    # flavour is only usable if the tiles stage vendored sheets under that exact stem.
    required = {f"{flavor}.json", f"{flavor}.png"}
    if not required <= set(sprite_assets):
        raise StyleError(
            f"the tiles stage vendors no sprite sheet for flavour {flavor!r} "
            f"(it writes {', '.join(sprite_assets)}); the style would name a sprite that "
            "is not in the bundle and every icon would be missing"
        )

    style: dict[str, Any] = {
        "version": STYLE_VERSION,
        "name": f"siyur-basemap-{flavor}",
        # Relative, and deliberately so — see the FAIL-007 note in the module docstring.
        "glyphs": GLYPHS_TEMPLATE,
        "sprite": f"{SPRITES_DIR}/{flavor}",
        "sources": {
            frozen.source_id: {
                "type": "vector",
                "url": f"pmtiles://{ARCHIVE_DIR}/{archive_name}.pmtiles",
                # MapLibre's own attribution accounting, kept in step with the credit the
                # app renders and with ATTRIBUTION.md — the three must not disagree about
                # what the basemap owes (ODbL).
                "attribution": TILE_ATTRIBUTION,
            }
        },
        # Deep-copied out of the cached frozen set: `load_basemap_layers` is memoized, so a
        # shallow copy would hand every subsequent caller aliases into one shared nested
        # structure — and a single mutation would silently change every later bundle in the
        # process, not just this one.
        "layers": [deepcopy(dict(layer)) for layer in layers],
        # Provenance for a reader of the bundle, who has the style but not this repo.
        "metadata": {
            "siyur:generator": f"{frozen.package}@{frozen.resolved_version}",
            "siyur:flavor": flavor,
            "siyur:lang": frozen.lang,
        },
    }
    _refuse_absolute_paths(style)
    return style


def style_bytes(**kwargs: Any) -> bytes:
    """:func:`build_style`, serialized to the exact bytes the bundle carries.

    RFC 8785 (JCS) rather than ``json.dumps``: the style is hashed into
    ``TileSourceV1.style.sha256`` and the traveller's device verifies that hash against
    these bytes, so the serialization has to be byte-stable across processes and Python
    versions — and JCS is already the repo's one canonicalization (ADR-0025 A5), so this
    is not a second answer to a settled question. It also leaves the ``©`` in the
    attribution as UTF-8 rather than escaping it, which ``json.dumps`` would not.
    """
    return rfc8785.dumps(build_style(**kwargs))


@lru_cache(maxsize=1)
def base_style_bytes() -> bytes:
    """The default style's bytes, built once per process.

    What `api/bundles.py` hands to the compile seam. Cached because it is the same
    document for every bundle at M1 ("no customization", `docs/data/tile-source.md`) and
    re-reading 130 KB of frozen layers per request buys nothing.
    """
    return style_bytes()
