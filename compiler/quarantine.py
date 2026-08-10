"""License quarantine — the last gate between the commons and an offline bundle.

Stage six of the compile order (`compiler/AGENTS.md`, tech-design §5.3): after the legs are
routed and **before `content` is frozen**, every value the licence registry refuses is removed
here, and every removal is written down. What this module drops is what
``BundleManifestV1.withheld`` records, and that record is the only thing the travel UI can
render "needs connectivity" from instead of a blank or an error — FR-011 (drop), FR-012
(refuse unstamped), FR-021 (record). Registry of record: `DATA-LICENSES.md` via
:mod:`commons.licenses`; the allowlist is never re-stated here.

## Bundleability is derived **structurally**, never per type

The one rule this module is built on (ADR-0025 ruling 8, and the `commons/models.py` module
docstring):

    Only a ``SourcedValue`` has a ``bundleable`` field to read. Everything else derives it
    from its bare ``SourceRef`` via ``licenses.bundleable(kind, license)``.

``Story`` is not the special case it looks like. **``RouteLegV1``, ``ResolvedArea`` and
``AreaCandidate`` are the same shape**, and anything added later that carries a bare
``SourceRef`` will be too. A filter written as ``derive if isinstance(v, Story) else
v.bundleable`` reaches the itinerary's legs, finds no attribute, and — with the likely
``getattr(v, "bundleable", False)`` repair — **drops every walking leg from every bundle**.
That bundle still compiles, hashes and passes every manifest-path check; the traveller's day
simply has no routes, silently, offline, with nothing to fall back to. So this module imports
neither ``Story`` nor any planner type and dispatches on **shape**: a name it cannot mention
is a name it cannot special-case. :func:`is_bundleable` is the whole of the decision, and
:func:`quarantine_legs` exists mostly to make "a leg is *kept*" a tested behaviour.

## Withheld or refused — never both

Two outcomes, and the difference is not a severity dial:

* **Withheld** — the value exists and its licence forbids redistribution. It is removed, and a
  :class:`~commons.models.WithheldValue` records ``site_id`` + the dotted **pre-removal** path
  + the reason. The compile succeeds; travel says "needs connectivity".
* **Refused** — :class:`QuarantineError`. The compile *fails*. FR-012's unstamped input is
  here, and deliberately **not** in the reason enum: there is no ``unstamped`` member
  (`docs/data/bundle-manifest.md`), because a placeholder in a downloadable manifest looks
  exactly like success, and turning a merge-blocking refusal into a "needs connectivity"
  affordance retires the invariant while every gate stays green.

``withheld[].reason`` is a **closed** enum (ADR-0025 A3) and this filter emits exactly one
member — see :data:`_LICENSE_FORBIDS` for why it is not sourced from
``licenses.quarantine_reason``.

## The walk, and what it refuses to guess

:func:`quarantine_record` walks a record's declared fields rather than a hand-written list of
field names, so a field added to ``SiteRecordV1`` later is quarantined the day it appears
instead of shipping unchecked until someone notices. Dispatch is by shape:

* JSON scalars and a ``SourceRef`` (the stamp itself, not a fact under it) pass through;
* mappings and sequences are filtered element-wise, the path taking the **input** index —
  survivors re-index, which is exactly why the card pins ``field`` as pre-removal;
* a ``SourcedValue`` is a **leaf**: its ``value`` is the fact its stamp covers, whatever the
  type (a ``str``, a ``Point``), and there is nothing under it to rule on separately;
* any other stamped structure derives, then is walked for refused children (a bundleable
  story with a non-bundleable claim keeps the story and loses the claim);
* anything else raises :class:`UnclassifiedStructureError`. A walk that shrugged at a
  structure it did not recognise would pass it into the bundle unchecked, which is the one
  failure mode this module exists to prevent.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Final
from uuid import UUID

from commons import licenses
from commons.models import (
    FieldConflict,
    ReviewSummary,
    RouteLegV1,
    SiteRecordV1,
    SourcedValue,
    SourceRef,
    StampedModel,
    WithheldReason,
    WithheldValue,
)

__all__ = [
    "QuarantineError",
    "QuarantineResult",
    "UnbundleableLegError",
    "UnclassifiedStructureError",
    "UnstampedValueError",
    "is_bundleable",
    "quarantine_legs",
    "quarantine_record",
    "quarantine_sites",
]

#: The only reason this filter emits: it removes what may not be redistributed, and nothing
#: else. Held as a constant so the string is written once, and **not** derived from
#: :func:`commons.licenses.quarantine_reason`, which returns prose naming the internal
#: allowlist — ``withheld`` ships inside a file the user downloads and opens, so the
#: vocabulary is closed at what the affordance needs (ADR-0025 A3). The enum's other member,
#: ``source_unavailable``, belongs to the freeze stage — a value that existed but could not be
#: written — and is not a licence verdict, so it is not this module's to emit.
_LICENSE_FORBIDS: Final[WithheldReason] = "license_forbids_redistribution"

#: Values that are facts in themselves and carry nothing under them to rule on. ``bool`` is an
#: ``int`` and ``datetime`` a ``date``; both are listed anyway, because the reader of a
#: quarantine allowlist should not have to know Python's numeric tower to check it.
_JSON_LEAF_TYPES: Final[tuple[type, ...]] = (str, bool, int, float, UUID, datetime, date, time)

#: Structures carrying **no ``SourceRef`` at all** whose *schema* pins their provenance to a
#: never-bundleable kind. ``ReviewSummary`` holds ``Rating``s from ``review_provider``s, which
#: :mod:`commons.licenses` refuses unconditionally, so there is a definite answer here and
#: nothing to derive it from — withheld, not refused. (`bundle-manifest.md`'s own example row
#: drops ``reviews`` and records it; FR-021's "needs connectivity" is what that entry feeds.)
_NEVER_BUNDLEABLE_STRUCTURES: Final[tuple[type[StampedModel], ...]] = (ReviewSummary,)

#: Structures with no provenance of their own that exist to hold stamped values. A
#: ``FieldConflict`` is bundleable exactly to the extent its candidates are: merge never
#: discards a source (§1.2), so a conflict routinely carries one allowlisted candidate beside
#: one that is not, and dropping the pair would throw away the surviving value too.
_STAMPLESS_CONTAINERS: Final[tuple[type[StampedModel], ...]] = (FieldConflict,)


class QuarantineError(Exception):
    """The compile cannot proceed. Refusal is the behaviour — never caught to continue."""


class UnstampedValueError(QuarantineError):
    """FR-012: input with no provenance is **refused**, never withheld and shipped."""


class UnbundleableLegError(QuarantineError):
    """A route leg the registry refuses — a removal ``withheld[]`` cannot express."""


class UnclassifiedStructureError(QuarantineError):
    """The walk met a structure it has no rule for; guessing would ship it unchecked."""


class _Dropped:
    """A "did not survive" marker — a distinct type, because ``None`` is a legitimate value."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<removed by quarantine>"


_DROPPED: Final = _Dropped()


@dataclass(frozen=True, slots=True)
class QuarantineResult:
    """Post-quarantine sites plus the removal log the manifest freezes into ``withheld``.

    ``sites`` is what ``content.sites`` — and, through ``SiteRecordV1.stories``,
    ``content.narrations`` — is frozen from: nothing reachable in it is ``bundleable=false``.
    A site absent from it altogether is one whose ``location`` was refused
    (:func:`quarantine_record`).
    """

    sites: tuple[SiteRecordV1, ...]
    withheld: tuple[WithheldValue, ...]


def is_bundleable(value: object) -> bool:
    """May this structure enter an offline bundle? Total, structural, no type dispatch.

    A :class:`~commons.models.SourcedValue` carries a stamp whose equivalence with the
    registry ``commons.models`` already validates, so the stamp is read. **Everything else
    carries a bare ``SourceRef`` and the answer is derived from it** — a story, a route leg, a
    resolved area, a claim, and whatever is added next.

    Raises :class:`UnstampedValueError` when there is no ``SourceRef`` to read or derive from.
    """
    if isinstance(value, SourcedValue):
        return value.bundleable
    source = _stamp_of(value)
    return licenses.bundleable(source.kind, source.license)


def quarantine_record(record: SiteRecordV1) -> QuarantineResult:
    """Filter one site record. ``sites`` holds it, or is empty if the whole site is withheld.

    ``location`` is checked first and alone: it is the record's one **required**
    ``SourcedValue``, so a refused location cannot be *removed* — there would be no record
    left to bundle. The site is withheld under that single path and nothing else about it is
    reported, because nothing else about it ships either.
    """
    if not is_bundleable(record.location):
        return QuarantineResult(sites=(), withheld=(_withhold(record.id, "location"),))
    withheld: list[WithheldValue] = []
    return QuarantineResult(
        sites=(_filter_fields(record, "", record.id, withheld),), withheld=tuple(withheld)
    )


def quarantine_sites(records: Iterable[SiteRecordV1]) -> QuarantineResult:
    """:func:`quarantine_record` over a bundle's records, concatenating both halves."""
    sites: list[SiteRecordV1] = []
    withheld: list[WithheldValue] = []
    for record in records:
        result = quarantine_record(record)
        sites.extend(result.sites)
        withheld.extend(result.withheld)
    return QuarantineResult(sites=tuple(sites), withheld=tuple(withheld))


def quarantine_legs(legs: Iterable[RouteLegV1]) -> tuple[RouteLegV1, ...]:
    """The legs, **unchanged** — or a refusal. Nothing here is ever silently dropped.

    A leg is one of the bare-``SourceRef`` shapes, so :func:`is_bundleable` derives; at M1
    every leg carries the `route-leg.md` stamp (``kind="osm"``, ``license="ODbL-1.0"``,
    because routing over OSM data is a Produced Work of OSM) and is therefore **kept**. That
    "kept" is why this function exists at all: it is the behaviour a per-type filter silently
    inverts, and it now has somewhere to be tested.

    A leg the registry does refuse raises instead of dropping. Every ``withheld`` entry needs
    a ``site_id`` and a leg has none, so a dropped leg is the one removal the manifest cannot
    express: the traveller would get a gap in the day with nothing to render "needs
    connectivity" from (FR-021). It is also unreachable through the M1 stamp — getting here
    means the routing provider's licence changed, which is a human's decision and not
    something a compile should absorb.
    """
    checked = tuple(legs)
    refused = [leg for leg in checked if not is_bundleable(leg)]
    if refused:
        raise UnbundleableLegError(
            f"{len(refused)} route leg(s) may not be redistributed — "
            f"{', '.join(sorted(leg.id for leg in refused))}: "
            f"{licenses.quarantine_reason(refused[0].source.kind, refused[0].source.license)}. "
            "Legs are not withheld: withheld[] is keyed by site_id, so a dropped leg is a "
            "silent gap in the traveller's day"
        )
    return checked


def _stamp_of(value: object) -> SourceRef:
    """The bare :class:`~commons.models.SourceRef` on ``value``, or an FR-012 refusal.

    Read as an attribute — every stamped structure in the spine is a pydantic model — or as a
    ``"source"`` key, for a payload arriving from storage or an API body. The stamp must
    already **be** a validated ``SourceRef``: a raw mapping is *claimed* provenance, and
    validating one here would mint at compile time the stamp FR-012 exists to demand at
    ingestion.
    """
    source = value.get("source") if isinstance(value, Mapping) else getattr(value, "source", None)
    if isinstance(source, SourceRef):
        return source
    raise UnstampedValueError(
        f"{type(value).__name__} reached the quarantine filter carrying no validated "
        "SourceRef, so it has no provenance and cannot be bundled. FR-012 refuses unstamped "
        "input: the compile fails here rather than continuing, because WithheldReason has no "
        "'unstamped' member (docs/data/bundle-manifest.md) and a placeholder in a downloaded "
        "manifest is indistinguishable from success"
    )


def _withhold(site_id: UUID, field: str) -> WithheldValue:
    """One removal, recorded. ``field`` is the **pre-removal** dotted path (card, `withheld`)."""
    return WithheldValue(site_id=site_id, field=field, reason=_LICENSE_FORBIDS)


def _filter(value: object, path: str, site_id: UUID, withheld: list[WithheldValue]) -> Any:
    """``value`` with every refused structure removed, or :data:`_DROPPED` if it is the value.

    Shape-driven, per the module docstring. Container indices are taken from the **input**
    position: survivors re-index, and the manifest's paths address the record as it stood
    before quarantine ran.
    """
    if value is None or isinstance(value, _JSON_LEAF_TYPES):
        return value
    # The stamp is provenance *about* a fact, never a fact under it — there is nothing here to
    # quarantine, and descending would ask a SourceRef for its own SourceRef.
    if isinstance(value, SourceRef):
        return value
    if isinstance(value, Mapping):
        kept_items = {
            key: item
            for key, raw in value.items()
            if (item := _filter(raw, f"{path}.{key}", site_id, withheld)) is not _DROPPED
        }
        return kept_items
    if isinstance(value, (tuple, list)):
        return tuple(
            item
            for index, raw in enumerate(value)
            if (item := _filter(raw, f"{path}[{index}]", site_id, withheld)) is not _DROPPED
        )
    if isinstance(value, StampedModel):
        return _filter_structure(value, path, site_id, withheld)
    raise UnclassifiedStructureError(
        f"quarantine has no rule for {type(value).__name__} at {path!r}. Rather than pass an "
        "unrecognised structure into the bundle unchecked, the compile stops: classify it in "
        "compiler/quarantine.py — stamped (it carries a SourceRef), never bundleable, a "
        "stampless container, or a leaf"
    )


def _filter_structure(
    value: StampedModel, path: str, site_id: UUID, withheld: list[WithheldValue]
) -> Any:
    """Rule on one stamped structure, then on its children. See the module docstring."""
    if isinstance(value, _NEVER_BUNDLEABLE_STRUCTURES):
        withheld.append(_withhold(site_id, path))
        return _DROPPED
    if isinstance(value, SourcedValue):
        # A leaf: `value.value` is the fact the stamp covers, of any type at all.
        if not value.bundleable:
            withheld.append(_withhold(site_id, path))
            return _DROPPED
        return value
    if "source" in type(value).model_fields:
        # Story, Claim, and every bare-SourceRef structure after them — derived, not read.
        if not is_bundleable(value):
            withheld.append(_withhold(site_id, path))
            return _DROPPED
    elif not isinstance(value, _STAMPLESS_CONTAINERS):
        raise UnclassifiedStructureError(
            f"{type(value).__name__} at {path!r} carries no SourceRef and is not a declared "
            "container or never-bundleable structure, so quarantine cannot rule on it. "
            "Classify it in compiler/quarantine.py rather than letting it into the bundle"
        )
    # Surviving the licence test is not the end of it: a bundleable story may carry a claim
    # sourced from somewhere the registry refuses, and a conflict its losing candidate.
    return _filter_fields(value, path, site_id, withheld)


def _filter_fields[M: StampedModel](
    model: M, path: str, site_id: UUID, withheld: list[WithheldValue]
) -> M:
    """Filter every declared field of ``model``, rebuilding it only if something changed.

    Fields are read off the model class, not a list maintained here, so a new fact-bearing
    field on ``SiteRecordV1`` is quarantined the day it is declared. ``model_copy`` on a
    :class:`~commons.models.StampedModel` re-validates, which is also the backstop for the
    impossible case: a dropped **required** field cannot be rebuilt and fails loudly here.
    """
    updates: dict[str, Any] = {}
    for name in type(model).model_fields:
        if name == "source":
            continue  # handled by _filter_structure as the stamp, not walked as a field
        current = getattr(model, name)
        kept = _filter(current, f"{path}.{name}" if path else name, site_id, withheld)
        if kept is _DROPPED:
            updates[name] = None
        elif kept != current:
            updates[name] = kept
    return model.model_copy(update=updates) if updates else model
