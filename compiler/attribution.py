"""T039 — regenerate ``ATTRIBUTION.md`` for one bundle, from that bundle's own stamps.

The compile order (tech-design §5.3) assembles this file **after** the quarantine filter has
run and the content is frozen, and **before** the manifest is written — because
``attribution.sha256`` hashes these bytes like any other artifact. It is legally obligated
content, not a decoration.

**Every credit is read from a stamp; none is composed here.** A ``SourceRef`` carries the
rendered attribution string its license requires, applied at the ingestion boundary by the
adapter that fetched the value. A value whose license *requires* attribution arriving without
one is an upstream bug, and :class:`AttributionRefused` says so — the alternative, a generic
credit line, names nobody and discharges nothing. The same refusal covers a
``bundleable=false`` value: its presence means the quarantine filter (T038) did not run, and no
wording can fix that.

**Two obligations, two mechanisms** (ADR-0024). Crediting each contributing article discharges
**BY**; declaring that the adapted prose is *itself* CC BY-SA discharges **SA**. The
declaration lives here as prose and in ``BundleManifestV1.textLicense`` as a machine-readable
field, so a reuser need not parse Markdown to learn the terms —
:func:`bundled_text_license` computes the manifest's value from the same stories this file
credits, so the two halves cannot drift apart.

**Per article *and* per revision.** ADR-0024 A1 credits the revision adapted, not the article
as it stands today; ``commons.sources.wikivoyage`` pins ``?oldid=<revid>`` into the
``SourceRef``, and this module carries that string through. Articles dedupe on ``source.id``
(``<lang>:<Page Title>``) so an article behind twenty stories is credited once — but two
*revisions* of one article are both named, rather than collapsed into whichever was seen first.

**Determinism is a hard requirement.** T040 hashes these bytes, so the same bundle renders
byte-identically however its inputs were ordered: credits group into license sections by
canonical SPDX id, sections follow a fixed order, and lines within a section are sorted.
Nothing here reads a clock, a dict's insertion order, or a set's iteration order.

**What this file does *not* discharge (ADR-0019).** Its revisit trigger 1 — "the first surface
that renders values with no interaction available at all" — is the offline bundle, and the ADR
states that a regenerated ``ATTRIBUTION.md`` is an **aggregate** credit which does not establish
per-value co-presence. That is still true here and is not something a generator can fix:
co-presence is a property of a *layout*, and this file is not the layout the traveller reads.
The material for it is already in the bundle — every frozen value keeps its own ``source``
stamp — and T051 owes the decision about how that stamp renders beside its value. Deliberately
nothing extra is emitted for the UI to consume: a parallel per-value credit map would be a copy
of the stamps that can disagree with them.
"""

from __future__ import annotations

import textwrap
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from pydantic import BaseModel

from commons.licenses import (
    BUNDLEABLE_LICENSES,
    SourceKind,
    bundleable,
    normalize_license,
    quarantine_reason,
)
from commons.models import RouteLegV1, SiteRecordV1, SourcedValue, SourceRef, Story, TileSourceV1

__all__ = [
    "ATTRIBUTION_PATH",
    "AttributionRefused",
    "BundleSources",
    "Notice",
    "attribution_bytes",
    "bundled_text_license",
    "render_attribution",
]

#: Where the rendered file sits inside the bundle — ``BundleManifestV1.attribution.path``.
#: Exported so the manifest writer names it once, here, rather than restating the string.
ATTRIBUTION_PATH: Final = "ATTRIBUTION.md"

#: Column the generated prose wraps at. Only prose: credit lines carry URLs and stay whole.
_WRAP_WIDTH: Final = 92


class AttributionRefused(ValueError):
    """A bundle asked for a credit that cannot be honestly written.

    Always an upstream bug — an unattributed value under an attribution-required license, or a
    ``bundleable=false`` value that the quarantine filter should have removed. Refusing is the
    only correct response: FR-012 refuses unstamped input rather than papering over it.
    """


@dataclass(frozen=True, slots=True)
class _Obligation:
    """What one license requires of a bundle that redistributes data under it.

    Transcribed from ``DATA-LICENSES.md`` — the registry is normative and this table is its
    rendering. ``credit_required`` is that registry's "In-app attribution required" column.
    """

    title: str
    note: str
    credit_required: bool


_COURTESY: Final = "No attribution is required. Credited as a courtesy."

#: One entry per allowlisted license (``commons.licenses.BUNDLEABLE_LICENSES``). A license the
#: registry allows into a bundle but this table does not describe would ship with no statement
#: of its terms, so the import-time check below refuses to let that happen quietly.
_OBLIGATIONS: Final[dict[str, _Obligation]] = {
    "ODbL-1.0": _Obligation(
        "Open Database License 1.0",
        "Attribution is required wherever this data is shown, including on every rendered map. "
        "A database derived from it carries the same terms.",
        credit_required=True,
    ),
    "CC-BY-SA-4.0": _Obligation(
        "Creative Commons Attribution-ShareAlike 4.0",
        "Each work is credited below, pinned to the revision adapted. Anything adapted from it "
        "is licensed under the same terms.",
        credit_required=True,
    ),
    "CC-BY-4.0": _Obligation(
        "Creative Commons Attribution 4.0", "Each work is credited below.", credit_required=True
    ),
    "CC0-1.0": _Obligation(
        "Creative Commons Zero 1.0",
        f"A public-domain dedication. {_COURTESY}",
        credit_required=False,
    ),
    "PD": _Obligation("Public domain", _COURTESY, credit_required=False),
    "CDLA-Permissive-2.0": _Obligation(
        "Community Data License Agreement – Permissive 2.0", _COURTESY, credit_required=False
    ),
    "Apache-2.0": _Obligation(
        "Apache License 2.0",
        "The copyright, patent and attribution notices are retained, a copy of the license "
        "ships with the bundle, and any NOTICE contents are reproduced under Notices below (§4).",
        credit_required=False,
    ),
    "MIT": _Obligation(
        "MIT License",
        "The copyright notice and the license text travel with the work.",
        credit_required=False,
    ),
    "OFL-1.1": _Obligation(
        "SIL Open Font License 1.1",
        "`OFL.txt` ships in the bundle beside the glyphs it covers.",
        credit_required=False,
    ),
    "LGPL-3.0": _Obligation(
        "GNU Lesser General Public License 3.0",
        "Used as an unmodified dependency; no copyleft term reaches the data in this bundle.",
        credit_required=False,
    ),
}

_UNDESCRIBED: Final[frozenset[str]] = frozenset(BUNDLEABLE_LICENSES - _OBLIGATIONS.keys())
if _UNDESCRIBED:  # pragma: no cover - fires only on a registry change, and must fire loudly
    raise ImportError(
        f"the bundleable allowlist gained {sorted(_UNDESCRIBED)} but compiler.attribution has "
        "no obligation text for it: a bundle would redistribute data under a license it never "
        "states the terms of. Add the entry to _OBLIGATIONS (DATA-LICENSES.md is normative)."
    )

#: How each source kind is named in a credit. Reading the name off the *stamped kind* is what
#: keeps a courtesy credit honest — the generator never guesses who a value came from. The map
#: is total over ``SourceKind`` even though the never-bundleable kinds are refused long before
#: a name is needed.
_SOURCE_NAMES: Final[dict[SourceKind, str]] = {
    "overture": "Overture Maps",
    "osm": "OpenStreetMap",
    "wikivoyage": "Wikivoyage",
    "wikipedia": "Wikipedia",
    "wikidata": "Wikidata",
    "commons": "Wikimedia Commons",
    "opening_hours_js": "Opening-hours evaluation",
    "review_provider": "Review providers",
    "open_web": "Open web",
    "user": "User notes",
}

#: Written unwrapped and wrapped at render time by :func:`_wrap`, so every paragraph in the
#: artifact has one width whoever wrote it.
_PREAMBLE_PARAGRAPHS: Final[tuple[str, ...]] = (
    "Every source in this bundle that carries a license obligation, credited from the stamp "
    "the value itself carries. This file is regenerated for each bundle by "
    "`compiler/attribution.py`; fix the generator, never this file.",
    "It is the bundle's **aggregate** credit and does not replace a per-value one: every value "
    "in the bundled site data keeps its own source and license stamp, and that stamp is what "
    "belongs beside the value wherever the value is rendered.",
)

_TEXT_LICENSE_HEADING: Final = "## Bundled text license"

_NO_TEXT_PARAGRAPH: Final = (
    "This bundle carries no narration text, so it declares no text license — `textLicense` is "
    "`null` in its manifest."
)


@dataclass(frozen=True, slots=True)
class Notice:
    """A NOTICE or copyright text reproduced **verbatim**, as Apache-2.0 §4 and MIT require.

    Not derivable from a :class:`~commons.models.SourceRef` — the text lives in the dependency
    that ships it — so the pipeline stage that collected it passes it through. Summarising or
    reflowing a notice is not reproducing it, hence the verbatim fenced block in the output.
    """

    #: What the notice covers, in the reuser's terms (a dependency or dataset name).
    subject: str
    #: SPDX id of the license imposing the notice.
    license: str
    #: The notice exactly as its source ships it.
    text: str


@dataclass(frozen=True, slots=True)
class BundleSources:
    """Everything one bundle's credits are read from — stamps, never prose.

    ``tiles`` and ``walk_graph_source`` are required because a bundle always has both, and both
    are OSM Produced Works whose ODbL credit is not optional. The walk graph is a GeoJSON file
    rather than a model, so it has nowhere of its own to carry a stamp: the stage that builds it
    passes the :class:`~commons.models.SourceRef` it was built under.
    """

    tiles: TileSourceV1
    walk_graph_source: SourceRef
    legs: Sequence[RouteLegV1] = ()
    #: Post-quarantine records. Their stories are credited individually; every other stamped
    #: value on them is credited in aggregate, per source and license.
    sites: Sequence[SiteRecordV1] = ()
    notices: Sequence[Notice] = ()


def render_attribution(bundle: BundleSources) -> str:
    """The full ``ATTRIBUTION.md`` for this bundle, as text."""
    entries = _credits(bundle)
    preamble = "\n\n".join(["# Attribution", *(_wrap(text) for text in _PREAMBLE_PARAGRAPHS)])
    sections = [preamble, _text_license_section(bundle.sites)]
    for license_id in _section_order(entries):
        obligation = _OBLIGATIONS[license_id]
        body = "\n".join(sorted(entries[license_id]))
        note = _wrap(obligation.note)
        sections.append(f"## {license_id} — {obligation.title}\n\n{note}\n\n{body}")
    sections.extend(_notice_sections(bundle.notices))
    return "\n\n".join(sections) + "\n"


def attribution_bytes(bundle: BundleSources) -> bytes:
    """The artifact exactly as it is written and hashed — UTF-8, LF, one trailing newline.

    The manifest hashes the *bytes* of this file, so the encoding is decided here rather than
    left to whoever writes it out; two callers encoding differently would produce two hashes
    for one bundle.
    """
    return render_attribution(bundle).encode("utf-8")


def bundled_text_license(sites: Iterable[SiteRecordV1]) -> str | None:
    """``BundleManifestV1.textLicense`` — the license of this bundle's narration text.

    ``None`` when no story is bundled; otherwise the license the stories are actually stamped
    with (``CC-BY-SA-4.0`` for every M1 source). Derived rather than hardcoded so that adding a
    narration source under different terms changes the declaration instead of silently shipping
    text under a license the manifest misstates; several distinct licenses render as an SPDX
    ``AND`` expression, which is the honest reading of a mixed-text bundle.
    """
    found = {_story_license(story) for site in sites for story in site.stories}
    return " AND ".join(sorted(found)) if found else None


# --- credits ---------------------------------------------------------------------------


def _credits(bundle: BundleSources) -> dict[str, list[str]]:
    """Every credit line this bundle owes, grouped by canonical license id."""
    entries: dict[str, list[str]] = {}
    _add_artifacts(entries, bundle)
    _add_place_data(entries, bundle.sites)
    _add_articles(entries, bundle.sites)
    return entries


def _add_artifacts(entries: dict[str, list[str]], bundle: BundleSources) -> None:
    """The whole-file artifacts: basemap, glyphs, sprites, walking legs, walking network."""
    tiles = bundle.tiles
    _add(
        entries,
        subject=(
            f"Offline basemap tiles (`{tiles.path}`, {tiles.build_source} build "
            f"{tiles.build_date.isoformat()})"
        ),
        license_id=tiles.tile_license,
        attribution=tiles.attribution,
    )
    # Two credits, because they are two works under two licenses: the Noto glyphs are OFL-1.1
    # and the sprite sheets MIT (`compiler/tiles.py`). One line naming both under
    # `glyphs.license` credited the sprites under OFL — asserting its "no standalone sale"
    # restriction over MIT assets, in the one file that discharges the bundle's obligations.
    # Each line reads its own ref's stamp, like every other credit here: the conflation was
    # invisible precisely because one of the two licenses was never read from anywhere.
    for name, asset in (("glyphs", tiles.glyphs), ("sprites", tiles.sprites)):
        _add(
            entries,
            subject=f"Map {name} (`{asset.path}`)",
            license_id=asset.license,
            attribution=None,
        )
    # `tiles.style` is deliberately absent: an ArtifactRef carries a path and a hash and no
    # license stamp at all, and the base style is our own generated file. Crediting it would
    # mean inventing a stamp, which is the one thing this module must not do.
    _add_ref(
        entries,
        subject=f"Pruned walking network (`{bundle.walk_graph_source.id}`)",
        ref=bundle.walk_graph_source,
    )
    for ref, count in sorted(_grouped(leg.source for leg in bundle.legs), key=_ref_sort_key):
        plural = "" if count == 1 else "s"
        _add_ref(entries, subject=f"Walking route legs (`{ref.id}`, {count} leg{plural})", ref=ref)


def _add_place_data(entries: dict[str, list[str]], sites: Sequence[SiteRecordV1]) -> None:
    """Bundled site values, credited per source and license rather than one line per value.

    **Grouped on ``(kind, license, attribution)`` and deliberately not on the whole stamp.** A
    stamp's ``id`` is per *record* — a GERS id, an ``osm`` element id — so keying on it would
    emit one credit line per place: a dense area's file would be hundreds of identical
    "Overture Maps, 3 values" lines, and the obligations at the top of each section would be
    unreadable underneath them. What a credit must name is the source and its terms, both of
    which are constant across those records. The count is what keeps the aggregate checkable
    against the content it covers.
    """
    counts: dict[tuple[SourceKind, str, str], int] = {}
    for site in sites:
        for value in _stamped_values(site):
            ref = value.source
            # Canonicalised into the key so a registry alias ("ODbL") and its SPDX spelling
            # ("ODbL-1.0") are one credit rather than two lines saying the same thing.
            license_id = _canonical(
                ref.license, subject=f"a value from {_SOURCE_NAMES[ref.kind]}", kind=ref.kind
            )
            key = (ref.kind, license_id, ref.attribution or "")
            counts[key] = counts.get(key, 0) + 1
    for (kind, license_id, attribution), count in sorted(counts.items()):
        plural = "" if count == 1 else "s"
        _add(
            entries,
            subject=f"{_SOURCE_NAMES[kind]}, {count} bundled place value{plural}",
            license_id=license_id,
            attribution=attribution or None,
            kind=kind,
        )


def _add_articles(entries: dict[str, list[str]], sites: Sequence[SiteRecordV1]) -> None:
    """One credit per contributing article, naming every revision adapted from it.

    Deduped on ``source.id`` (ADR-0024's ``<lang>:<Page Title>``) so an article behind twenty
    stories is credited once. Two *revisions* of one article are a different case: collapsing
    them would leave the file claiming the bundle adapted a revision it partly did not, so the
    extra revisions are named under the same credit.
    """
    revisions: dict[tuple[str, str], dict[str, str]] = {}
    for site in sites:
        for story in site.stories:
            ref = story.source
            license_id = _story_license(story)
            credit = _credit_for(ref, license_id, subject=f"the article `{ref.id}`")
            # Keyed by the pinned revision URL so one revision credited twice is one entry;
            # falls back to the credit itself for a stamp that carries no URL.
            revisions.setdefault((license_id, ref.id), {})[ref.url or credit] = credit
    for (license_id, _article_id), adapted in revisions.items():
        primary, *extra = sorted(adapted)
        lines = [f"- {adapted[primary]}"]
        lines.extend(f"  - also adapted from {key}" for key in extra)
        entries.setdefault(license_id, []).append("\n".join(lines))


def _add(
    entries: dict[str, list[str]],
    *,
    subject: str,
    license_id: str,
    attribution: str | None,
    kind: SourceKind | None = None,
) -> None:
    canonical = _canonical(license_id, subject=subject, kind=kind)
    credit = attribution.strip() if attribution else None
    if credit is None and _OBLIGATIONS[canonical].credit_required:
        raise AttributionRefused(
            f"{subject} is stamped {canonical}, which requires attribution, but its stamp "
            "carries none. The credit is read from the value's SourceRef and never composed "
            "here, so this is a bug in the adapter that produced the value."
        )
    line = f"- {subject}" if credit is None else f"- {subject} — {credit}"
    entries.setdefault(canonical, []).append(line)


def _add_ref(entries: dict[str, list[str]], *, subject: str, ref: SourceRef) -> None:
    _add(
        entries,
        subject=subject,
        license_id=ref.license,
        attribution=ref.attribution,
        kind=ref.kind,
    )


def _credit_for(ref: SourceRef, license_id: str, *, subject: str) -> str:
    """The rendered credit for one stamp: its own attribution, or a courtesy naming its source."""
    if ref.attribution and ref.attribution.strip():
        return ref.attribution.strip()
    if _OBLIGATIONS[license_id].credit_required:
        raise AttributionRefused(
            f"{subject} is stamped {license_id}, which requires attribution, but its stamp "
            "carries none — see ADR-0024: the credit is captured at ingestion, not written here."
        )
    return _SOURCE_NAMES[ref.kind]


def _canonical(license_id: str, *, subject: str, kind: SourceKind | None) -> str:
    """The allowlist spelling of a license that reached a bundle, or a refusal.

    Two different failures, and both mean the same thing — quarantine did not do its job — so
    both stop the compile rather than being written into the file as a caveat.
    """
    if kind is not None and not bundleable(kind, license_id):
        raise AttributionRefused(
            f"{subject} reached ATTRIBUTION.md but is not bundleable: "
            f"{quarantine_reason(kind, license_id)}. The quarantine filter drops these before "
            "the content is frozen; a credit cannot make a value redistributable."
        )
    canonical = normalize_license(license_id)
    if canonical is None or canonical not in BUNDLEABLE_LICENSES:
        raise AttributionRefused(
            f"{subject} is stamped {license_id!r}, which is not on the bundleable allowlist "
            "(DATA-LICENSES.md quarantine rule), so it may not be in a bundle at all."
        )
    return canonical


def _story_license(story: Story) -> str:
    """A story's canonical license — derived, because a ``Story`` carries no ``bundleable``."""
    return _canonical(
        story.source.license,
        subject=f"the story adapted from `{story.source.id}`",
        kind=story.source.kind,
    )


def _section_order(entries: Mapping[str, list[str]]) -> list[str]:
    """ODbL first, then alphabetical.

    ODbL is pulled to the front because it is the one credit the OSMF Produced-Work guidelines
    and Constitution Article V require on every map and credits screen; the rest need only be
    present, and alphabetical is an order no future license can argue with.
    """
    rest = sorted(license_id for license_id in entries if license_id != "ODbL-1.0")
    return (["ODbL-1.0"] if "ODbL-1.0" in entries else []) + rest


def _text_license_section(sites: Sequence[SiteRecordV1]) -> str:
    """The SA half of ADR-0024: adapted prose is a derivative, so the bundle declares its terms."""
    declared = bundled_text_license(sites)
    if declared is None:
        return f"{_TEXT_LICENSE_HEADING}\n\n{_wrap(_NO_TEXT_PARAGRAPH)}"
    declaration = _wrap(
        "The narration text in this bundle is adapted from the openly-licensed articles "
        f"credited below and is therefore itself licensed **{declared}**. The manifest declares "
        "the same license machine-readably in `textLicense`: crediting the articles discharges "
        "attribution, and this declaration discharges share-alike."
    )
    # ADR-0024's bound on virality, stated in the artifact itself. Its absence is what makes a
    # reuser read share-alike as reaching the whole archive — and the failure mode of that
    # reading is dropping bundled narration altogether, i.e. silently reverting PRD §7 (a).
    bound = _wrap(
        "The obligation reaches the narration text and anything derived from it. It does not "
        "reach the application, the map style, the basemap tiles, the walking network, the "
        "route legs or the itinerary — those are separate works under their own licenses, "
        "assembled alongside the text rather than merged into it."
    )
    return f"{_TEXT_LICENSE_HEADING}\n\n{declaration}\n\n{bound}"


def _notice_sections(notices: Sequence[Notice]) -> list[str]:
    """Verbatim NOTICE reproduction (Apache-2.0 §4, MIT) — empty when nothing ships one."""
    if not notices:
        return []
    sections = ["## Notices\n\nReproduced verbatim, as the licenses below require."]
    for notice in sorted(notices, key=lambda item: (item.subject, item.license, item.text)):
        fence = _fence_for(notice.text)
        # The notice's own trailing newline is kept if it has one and supplied if it does not —
        # a fence needs the closing row on its own line, and adding a second newline would put
        # a blank line inside text that is supposed to be reproduced exactly.
        body = notice.text if notice.text.endswith("\n") else notice.text + "\n"
        sections.append(f"### {notice.subject} — {notice.license}\n\n{fence}\n{body}{fence}")
    return sections


def _wrap(paragraph: str) -> str:
    """Hard-wrap generated prose so the artifact reads in a terminal as well as a renderer.

    Deterministic (``textwrap`` is pure Python and locale-independent) and applied only to
    prose: a credit line carries URLs and must never be broken across lines.
    """
    return textwrap.fill(paragraph, width=_WRAP_WIDTH, break_long_words=False)


def _fence_for(text: str) -> str:
    """A code fence longer than any backtick run in ``text`` — a notice must survive intact."""
    longest = 0
    run = 0
    for character in text:
        run = run + 1 if character == "`" else 0
        longest = max(longest, run)
    return "`" * max(3, longest + 1)


def _grouped(refs: Iterable[SourceRef]) -> list[tuple[SourceRef, int]]:
    """Distinct stamps and how many values carry each — order-independent by construction."""
    counts: dict[SourceRef, int] = {}
    for ref in refs:
        counts[ref] = counts.get(ref, 0) + 1
    return list(counts.items())


def _ref_sort_key(item: tuple[SourceRef, int]) -> tuple[str, str, str, str]:
    ref = item[0]
    return (ref.kind, ref.license, ref.id, ref.attribution or "")


def _stamped_values(node: object) -> Iterator[SourcedValue[Any]]:
    """Every :class:`~commons.models.SourcedValue` inside a record, however it is nested.

    Walked structurally rather than field by field: a ``SiteRecordV2`` that adds a stamped
    field would otherwise ship its values uncredited, and nothing would notice. Recursion stops
    at a ``SourcedValue`` — its ``value`` is a scalar or a geometry, never another stamp.
    """
    if isinstance(node, SourcedValue):
        if not node.bundleable:
            raise AttributionRefused(
                f"a value stamped {node.source.kind}/{node.source.license} with "
                "bundleable=false is in the frozen content: the quarantine filter must drop it "
                "and record it in the manifest's withheld[] before ATTRIBUTION.md is assembled."
            )
        yield node
    elif isinstance(node, BaseModel):
        for name in type(node).model_fields:
            yield from _stamped_values(getattr(node, name))
    elif isinstance(node, Mapping):
        for item in node.values():
            yield from _stamped_values(item)
    elif isinstance(node, (tuple, list)):
        for item in node:
            yield from _stamped_values(item)
