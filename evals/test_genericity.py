"""Genericity eval — merge-blocking on SC-005 / FR-001 / validation rule 8 (T063).

*"Nothing is hardcoded to Rhodes"* is the claim the whole product positioning rests on, so
it is proven twice, from opposite directions:

**(a) Behavioural — the flow runs on two areas of genuinely different character.** The same
:func:`planner.pipeline.run_research` code path is driven over the committed Rhodes and
**Takayama** (高山, Gifu, Japan) fixture pairs, parametrised. The test bodies never branch
on which area they are looking at: whatever holds, holds for both, or the eval reddens.
Takayama is not a second Latin-script European town on purpose —
:data:`commons.translit.SUPPORTED_SOURCE_SCRIPTS` carries a transform for ``Grek`` and for
nothing else, so a Japanese area exercises the path that genericity actually *means* in
practice: **no transform exists for this script, so nothing is derived, and that is a
correct outcome rather than an error**. A Latin-script area would have proven far less —
it would have exercised the same "already Latin, nothing to do" branch Rhodes' own English
names already cover.

**(b) Static — an AST scan of ``commons``/``planner``/``api`` for place literals and
hardcoded coordinates.** Behavioural evidence only covers the areas someone thought to
test; the scan covers the code.

What the scan catches, and what it cannot — stated plainly, because a gate that oversells
itself is worse than one whose limits are known
-----------------------------------------------------------------------------------------

It **catches**:

* a coordinate-like literal — a 2- or 4-element tuple/list of numeric literals, at least
  one of them a float, whose slots are in longitude/latitude range (bounds read from
  :mod:`commons.geo`, never retyped). That is the shape a hardcoded point or bbox has.
* ``≥2`` numeric literals passed to a geometry constructor (``Point``, ``box``,
  ``Polygon``, :func:`~commons.geo.point_from_lonlat`, …).
* any of :data:`PLACE_WORDS` inside a **non-docstring** string literal.

It **cannot** catch:

* **A place name nobody put on the denylist.** Toponyms cannot be enumerated, so the
  string half is a *narrow denylist* of the specific words this project would plausibly
  hardcode — the two fixture areas and their countries — not a general proper-noun
  detector. A heuristic ("a capitalised string compared against a name field") was
  considered and rejected as too brittle to be honest: it would fire on ``"Point"``,
  ``"Overture"`` and every category value, and a scan that cries wolf gets disabled.
  This half is therefore a **regression guard on the areas we know about**, not a proof.
* **Place names in comments and docstrings.** Comments are not in the AST at all;
  docstrings are deliberately exempted, because documentation is not behaviour —
  :mod:`commons.merge` and :mod:`commons.translit` both discuss Rhodes *and* Takayama in
  their prose, correctly, and flagging that would train the reader to ignore the gate.
* **Integer-only coordinate pairs** (``(28, 36)``). Requiring one float is what keeps the
  numeric half from flagging every unrelated 2-tuple in the tree; the cost is that a
  degree-precision literal slips through.
* **A coordinate assembled at runtime** — from separate scalars, a config file, an
  environment variable or a database row. The scan reads source, not behaviour; half (a)
  is what covers that direction.

:func:`test_the_scan_bites_on_injected_place_literals_and_coordinates` and
:func:`test_the_scan_is_quiet_on_the_legitimate_literals_product_code_really_contains`
pin both edges permanently, so the scan cannot rot into a check that passes on everything.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import pytest
from shapely.geometry import box
from shapely.geometry.base import BaseGeometry

from commons.geo import MAX_LAT, MAX_LON
from commons.merge import iter_sourced_values
from commons.models import SiteRecordV1, SourcedValue
from commons.sources.base import SourceAdapter
from commons.sources.overture import OvertureAdapter
from commons.translit import LATIN_SCRIPT, SUPPORTED_SOURCE_SCRIPTS
from planner.pipeline import PHASES, Event
from tests.test_pipeline import drain, phases_of
from tests.test_planner_research import AREA, FIXTURES, OBSERVED, OVERPASS_JSON, osm

#: FR-005 — the only source kinds that may ever stamp a coordinate, for either area.
GEODATA_KINDS: Final[frozenset[str]] = frozenset({"overture", "osm"})

REPO_ROOT: Final[Path] = Path(__file__).resolve().parent.parent
#: The packages SC-005 is a claim about. `tests/`, `evals/`, `docs/` and the fixtures are
#: **not** scanned: "Rhodes" is legitimate — load-bearing, even — in all of them.
PRODUCT_PACKAGES: Final[tuple[str, ...]] = ("commons", "planner", "api")


# ── the two areas, and the one code path that serves both ─────────────────────────────


@dataclass(frozen=True, slots=True)
class Area:
    """A fixture pair. ``label`` reaches the test *ids* only — never a code path."""

    label: str
    polygon: BaseGeometry
    parquet: Path
    overpass: Any


def _overpass(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


#: Rhodes medieval old town — the demo area, reused verbatim from the pipeline tests.
RHODES: Final = Area(
    label="rhodes",
    polygon=AREA,
    parquet=FIXTURES / "overture_places_rhodes.parquet",
    overpass=OVERPASS_JSON,
)
#: Takayama (高山) old town, Gifu — same bbox extent, different hemisphere, different
#: script, and a script this build has **no** transliteration transform for.
TAKAYAMA: Final = Area(
    label="takayama",
    polygon=box(137.252, 36.135, 137.268, 36.146),
    parquet=FIXTURES / "overture_places_takayama.parquet",
    overpass=_overpass("overpass_takayama.json"),
)
AREAS: Final[tuple[Area, ...]] = (RHODES, TAKAYAMA)

_PASSES: dict[str, tuple[Event, ...]] = {}


def adapters_for(area: Area) -> list[SourceAdapter]:
    """The source set for *any* area — built from the request, with nothing to branch on."""
    return [
        OvertureAdapter(parquet=str(area.parquet), observed_at=OBSERVED),
        osm(node=area.overpass),
    ]


def research_pass(area: Area) -> tuple[Event, ...]:
    """One real research pass over ``area`` — offline, no model, no database, no network.

    Memoised per area so the parametrised evals below measure one pass each rather than
    re-running the whole pipeline for every assertion.
    """
    if area.label not in _PASSES:
        _PASSES[area.label] = tuple(drain(area=area.polygon, adapters=adapters_for(area)))
    return _PASSES[area.label]


def records_of(events: Sequence[Event]) -> tuple[SiteRecordV1, ...]:
    """The streamed sites, re-validated from the wire — what a client would actually hold."""
    return tuple(SiteRecordV1.model_validate(dict(e.data)) for e in events if e.event == "site")


def values_of(records: Sequence[SiteRecordV1]) -> list[tuple[str, SourcedValue[Any]]]:
    return [(field, value) for r in records for field, value in iter_sourced_values(r)]


def curate_frame(events: Sequence[Event]) -> dict[str, Any]:
    return dict(next(e for e in events if e.data.get("phase") == "curate").data)


# ── (a) the same flow, two areas, no place-specific code ──────────────────────────────


@pytest.mark.parametrize("area", AREAS, ids=[a.label for a in AREAS])
def test_the_same_flow_produces_cited_sites_on_every_area(area: Area) -> None:
    """SC-005: cited sites on the map for *both* areas, from one parametrised code path.

    Nothing below reads ``area.label``: the guarantees are identical, which is the whole
    claim. An area that produced nothing would make every later assertion vacuous, so
    ``N > 0`` is asserted first.
    """
    events = research_pass(area)
    records = records_of(events)
    assert records, f"{area.label}: the flow produced no records; this eval asserts nothing"

    # The contract's phases were all walked — the same ones, in the same order, for both.
    assert set(PHASES) <= set(phases_of(events)), f"{area.label}: a contract phase is missing"

    # SC-002 holds here too: provenance is a *rate* over the whole pass, and it is 1.0.
    values = values_of(records)
    assert len(values) > len(records), f"{area.label}: each record carries several values"
    unstamped = [f for f, v in values if v.source is None or not v.source.kind]
    assert not unstamped, f"{area.label}: {len(unstamped)} unstamped values, e.g. {unstamped[:5]}"

    # FR-005: every coordinate traces to authoritative geodata, in both hemispheres.
    locations = [v for f, v in values if f == "location"]
    assert len(locations) >= len(records), f"{area.label}: every record carries a location"
    foreign = {v.source.kind for v in locations if v.source.kind not in GEODATA_KINDS}
    assert not foreign, f"{area.label}: coordinates citing {sorted(foreign)}, not geodata"

    # Both sources answered for both areas — a fixture that silently covered only one
    # would make "the same flow" a much weaker statement than it looks.
    reports = [e for e in events if e.data.get("phase") == "research"]
    found = {str(e.data["source"]): e.data["found"] for e in reports}
    assert set(found) == GEODATA_KINDS, f"{area.label}: sources reported {sorted(found)}"
    assert all(n > 0 for n in found.values()), f"{area.label}: a source found nothing: {found}"


@pytest.mark.parametrize("area", AREAS, ids=[a.label for a in AREAS])
def test_every_streamed_site_reaches_the_client_on_every_area(area: Area) -> None:
    """The stream's own accounting agrees with itself for both areas (SC-006's other half:
    nothing is invented, and nothing merged is quietly dropped either)."""
    events = research_pass(area)
    summary = next(e for e in events if e.event == "summary")
    sites = [e for e in events if e.event == "site"]
    assert sites, f"{area.label}: no site frames"
    assert summary.data["sites"] == len(sites) == curate_frame(events)["merged"]
    assert summary.data["degraded_sources"] == [], f"{area.label}: {summary.data}"
    assert events[-1].event == "done"


def test_the_two_areas_are_genuinely_different_and_share_no_record() -> None:
    """The guard on the evidence itself: a relabelled Rhodes subset would satisfy every
    assertion above and prove nothing at all."""
    by_area = {a.label: records_of(research_pass(a)) for a in AREAS}
    ids = {label: {r.location.source.id for r in records} for label, records in by_area.items()}
    assert ids["rhodes"] and ids["takayama"]
    assert not ids["rhodes"] & ids["takayama"], "the two areas share source records"

    # …and they are genuinely elsewhere: the polygons do not touch, and the scripts differ.
    assert not RHODES.polygon.intersects(TAKAYAMA.polygon), "the two areas overlap"
    assert not RHODES.polygon.buffer(1.0).intersects(TAKAYAMA.polygon), "the areas are neighbours"


def test_an_area_whose_script_has_no_transform_derives_nothing_and_flags_nothing() -> None:
    """The heart of SC-005, and the reason the second area is Japanese rather than European.

    ``SUPPORTED_SOURCE_SCRIPTS`` covers ``Grek`` and nothing else. So the *correct* result
    for Takayama is **zero** derived ``*-Latn`` names and **zero** script-mismatch flags:
    no transform exists, therefore nothing is derived, and a silence that is right is not
    a failure. Rhodes is asserted in the same test as the control — if the transliteration
    stage were simply dead, Takayama's zero would be meaningless rather than meaningful.
    """
    assert set(SUPPORTED_SOURCE_SCRIPTS) == {"Grek"}, (
        "a new transform landed; this eval's premise (and its expected zero) must be re-derived"
    )

    greek = curate_frame(research_pass(RHODES))
    assert greek["derived_names"] > 0, "the control area must transliterate, or zero proves nothing"

    japanese = curate_frame(research_pass(TAKAYAMA))
    assert japanese["derived_names"] == 0, (
        "a Latin name was derived for a script no transform exists for — the pipeline "
        f"invented a rendering rather than staying silent: {japanese}"
    )
    assert japanese["flags"] == 0, (
        "'no transform available' was reported as a script mismatch; a missing transform "
        f"is not bad data and must raise no flag: {japanese}"
    )

    # …and the silence is a *choice*, not an empty stage: the names are all there, in their
    # own script, and none of them grew a derived Latin twin.
    records = records_of(research_pass(TAKAYAMA))
    tags = {tag for r in records for tag in r.names}
    assert tags, "no names at all — the assertion above would be vacuous"
    derived = sorted(tag for tag in tags if tag.endswith(f"-{LATIN_SCRIPT}"))
    assert not derived, f"derived Latin tags on an untransliterable area: {derived}"


# ── (b) the AST scan ──────────────────────────────────────────────────────────────────

#: Names whose numeric literal arguments are coordinates by construction.
GEOMETRY_CONSTRUCTORS: Final[frozenset[str]] = frozenset(
    {"Point", "box", "Polygon", "MultiPolygon", "LineString", "LinearRing", "point_from_lonlat"}
)
#: The narrow denylist — the specific words this project would plausibly hardcode. See the
#: module docstring for why this is a regression guard and not a proof. Compared casefolded
#: on both sides (``str.casefold`` maps Greek final sigma ς → σ, so the needle must too).
PLACE_WORDS: Final[tuple[str, ...]] = tuple(
    word.casefold()
    for word in (
        # Rhodes — the demo area, in the three spellings the fixtures actually carry.
        "Rhodes",
        "Rodos",
        "Ρόδος",
        "Greece",
        "Hellas",
        # Takayama — the genericity foil, likewise.
        "Takayama",
        "高山",
        "飛騨",
        "Japan",
    )
)


@dataclass(frozen=True, slots=True)
class Finding:
    """One place-specific literal the scan refuses."""

    path: str
    line: int
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line} [{self.rule}] {self.detail}"


def _literal_number(node: ast.expr) -> tuple[float, bool] | None:
    """``(value, was_written_as_a_float)`` for a numeric literal, unary minus included."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        inner = _literal_number(node.operand)
        if inner is None:
            return None
        value, is_float = inner
        return (-value if isinstance(node.op, ast.USub) else value), is_float
    if isinstance(node, ast.Constant) and not isinstance(node.value, bool):
        if isinstance(node.value, (int, float)):
            return float(node.value), isinstance(node.value, float)
    return None


def _numbers(nodes: Sequence[ast.expr]) -> list[tuple[float, bool]] | None:
    """Every element as a numeric literal, or ``None`` if any element is not one."""
    parsed = [_literal_number(node) for node in nodes]
    return None if any(item is None for item in parsed) else [i for i in parsed if i is not None]


def looks_like_coordinates(numbers: Sequence[tuple[float, bool]]) -> bool:
    """Is this run of numeric literals shaped like a point or a bbox?

    Two or four slots, alternating lon/lat, each inside **its own** axis' range (read from
    :mod:`commons.geo`, not retyped here), and at least one written as a float — that last
    condition is what stops the rule flagging every unrelated integer pair in the tree.
    All-zero is exempt: ``(0, 0)`` is an origin/placeholder idiom, not a delimited place.
    """
    if len(numbers) not in (2, 4) or not any(is_float for _, is_float in numbers):
        return False
    values = [value for value, _ in numbers]
    if all(value == 0.0 for value in values):
        return False
    return all(abs(v) <= MAX_LON for v in values[0::2]) and all(
        abs(v) <= MAX_LAT for v in values[1::2]
    )


def _docstring_ids(tree: ast.AST) -> set[int]:
    """Identities of the string nodes that are docstrings — documentation, not behaviour."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = node.body[0] if node.body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                found.add(id(first.value))
    return found


def scan_source(source: str, path: str) -> list[Finding]:
    """Every place-specific literal in one module's source (see the module docstring)."""
    tree = ast.parse(source, filename=path)
    docstrings = _docstring_ids(tree)
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List)):
            numbers = _numbers(node.elts)
            if numbers is not None and looks_like_coordinates(numbers):
                findings.append(
                    Finding(
                        path,
                        node.lineno,
                        "coordinate-literal",
                        f"{[v for v, _ in numbers]} is shaped like a hardcoded "
                        f"{'point' if len(numbers) == 2 else 'bbox'}",
                    )
                )
        elif isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else None
            name = name or (node.func.id if isinstance(node.func, ast.Name) else None)
            if name in GEOMETRY_CONSTRUCTORS:
                numbers = [n for n in (_literal_number(a) for a in node.args) if n is not None]
                if len(numbers) >= 2:
                    findings.append(
                        Finding(
                            path,
                            node.lineno,
                            "coordinate-literal",
                            f"{name}() built from literal coordinates {[v for v, _ in numbers]}",
                        )
                    )
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in docstrings:
                continue
            folded = node.value.casefold()
            hits = sorted({word for word in PLACE_WORDS if word in folded})
            if hits:
                findings.append(Finding(path, node.lineno, "place-literal", f"string names {hits}"))
    return findings


def product_modules(root: Path = REPO_ROOT) -> list[Path]:
    """Every ``.py`` file of the product packages — and deliberately nothing else."""

    def walk() -> Iterator[Path]:
        for package in PRODUCT_PACKAGES:
            yield from sorted((root / package).rglob("*.py"))

    return [path for path in walk() if "__pycache__" not in path.parts]


def scan_product_code(root: Path = REPO_ROOT) -> list[Finding]:
    return [
        finding
        for path in product_modules(root)
        for finding in scan_source(path.read_text(encoding="utf-8"), str(path.relative_to(root)))
    ]


def test_product_code_hardcodes_no_place_and_no_coordinate() -> None:
    """Validation rule 8 / SC-005, statically: nothing in the shipped packages knows where
    it is. The demo area is a *test fixture*, never a default."""
    modules = product_modules()
    assert len(modules) >= 10, (
        f"the scan found only {len(modules)} product modules — the walk has broken and this "
        "eval would pass vacuously"
    )
    findings = scan_product_code()
    assert not findings, "place-specific literals in product code:\n" + "\n".join(
        f"  {finding}" for finding in findings
    )


def test_the_scan_covers_the_product_packages_and_not_the_fixtures() -> None:
    """Scope is load-bearing in both directions: missing ``api/`` would leave a hole, and
    scanning ``tests/`` would flag the fixtures the other half of this eval depends on."""
    scanned = {path.relative_to(REPO_ROOT).parts[0] for path in product_modules()}
    assert scanned == set(PRODUCT_PACKAGES), f"scanned packages drifted: {sorted(scanned)}"
    assert not any(part in {"tests", "evals"} for p in product_modules() for part in p.parts)


def test_the_scan_bites_on_injected_place_literals_and_coordinates() -> None:
    """A gate that cannot go red is not a gate. Each snippet is the real mistake it names.

    Parsed as source rather than by mutating a module on disk, so the proof is committed
    and runs on every CI pass instead of being a one-off someone did by hand once.
    """
    biting = {
        "point": "HOME = (28.2247, 36.4443)\n",
        "bbox": "DEMO_AREA = [28.216, 36.440, 28.232, 36.451]\n",
        "bbox-elsewhere": "DEMO_AREA = (137.252, 36.135, 137.268, 36.146)\n",
        "constructor": "from shapely import box\nAREA = box(28.216, 36.440, 28.232, 36.451)\n",
        "negative": "SOMEWHERE = (-71.0589, 42.3601)\n",
        "place-name": 'def resolve(name: str) -> str:\n    return name or "Rhodes"\n',
        "place-name-jp": 'DEFAULT = {"area": "Takayama"}\n',
        "place-name-greek": 'FALLBACK = "Ρόδος"\n',
        "place-in-fstring": 'def f(x: str) -> str:\n    return f"{x} in Greece"\n',
    }
    quiet = [name for name, source in biting.items() if not scan_source(source, name)]
    assert not quiet, f"the scan stayed silent on genuinely place-specific code: {quiet}"


def test_the_scan_is_quiet_on_the_legitimate_literals_product_code_really_contains() -> None:
    """…and the other edge: these are all real shapes from the tree above. A scan that
    flagged them would be turned off within a week, and would then protect nothing."""
    quiet = {
        "axis-bounds": "MIN_LON = -180.0\nMAX_LAT = 90.0\n",
        "confidence-band": "STRONG = 0.8\nPLAUSIBLE = 0.4\nFALLBACK = 0.5\n",
        "srid": "CRS = 'EPSG:4326'\nSRID = 4326\n",
        "integer-pair": "RETRIES = (1, 2)\nVERSION = (0, 6)\n",
        "computed-point": "def p(lon: object, lat: object) -> object:\n"
        "    return Point(validate_lon(lon), validate_lat(lat))\n",
        "geometry-from-names": "def a(w: float, s: float, e: float, n: float) -> object:\n"
        "    return box(w, s, e, n)\n",
        "docstring-prose": '"""Rhodes is the demo area and Takayama the genericity foil."""\n',
        "function-docstring": 'def f() -> None:\n    """Ρόδος → Rodos, per ELOT 743."""\n',
        "origin": "ORIGIN = (0.0, 0.0)\n",
        "script-table": "FOLD = {'ς': 'σ'}\nRANGES = ((0x0370, 0x03FF, 'Grek'),)\n",
    }
    noisy = {name: scan_source(source, name) for name, source in quiet.items()}
    assert not any(noisy.values()), "false positives — the scan would get disabled:\n" + "\n".join(
        f"  {finding}" for findings in noisy.values() for finding in findings
    )
