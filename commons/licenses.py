"""License quarantine — the allowlist behind Constitution Article V.

Registry of record: [`DATA-LICENSES.md`](../DATA-LICENSES.md). Its "quarantine rule"
section is normative and this module is its executable form — **the allowlist is not
invented here, it is transcribed** (``tests/test_licenses.py`` re-parses the markdown
and fails on drift).

The rule, verbatim from the registry:

    A value may be stamped ``bundleable=true`` **only if** its ``source.license`` ∈
    {ODbL, CDLA-Permissive-2.0, CC0, CC-BY-4.0, CC-BY-SA-4.0, PD, OFL, LGPL-as-dependency}.
    ``open_web`` and ``review_provider`` sources are **always** ``bundleable=false``.

`docs/data/poi-site.md` and `specs/001-research-cited-sites/data-model.md` §5 sharpen
"only if" to an equivalence: ``bundleable=true`` ⟺ the license is allowlisted (and the
kind is not one of the never-bundleable ones). :func:`bundleable` is therefore a *total*
function of ``(kind, license)`` — the stamp is derived, never opinion. ``commons.models``
validates every ``SourcedValue`` against it, so a value cannot be author-stamped
``bundleable=True`` over a non-allowlisted license.

``SourceKind`` lives here (rather than in ``commons.models``) because the quarantine rule
keys on it, and ``models`` imports ``licenses`` — not the other way round.
"""

from __future__ import annotations

import re
from typing import Final, Literal

__all__ = [
    "BUNDLEABLE_LICENSES",
    "NEVER_BUNDLEABLE_KINDS",
    "SourceKind",
    "bundleable",
    "is_allowlisted",
    "normalize_license",
    "quarantine_reason",
]

#: The `SourceRef.kind` enum, verbatim from the schema card (`docs/data/poi-site.md`).
SourceKind = Literal[
    "overture",
    "osm",
    "wikivoyage",
    "wikipedia",
    "wikidata",
    "commons",
    "opening_hours_js",
    "review_provider",
    "open_web",
    "user",
]

#: Kinds that are **always** ``bundleable=false``, whatever license they claim.
#: (Review providers are proprietary; nothing fetched from the open web may be copied
#: into a bundle — DATA-LICENSES.md registry rows "Review providers" / "Open web".)
NEVER_BUNDLEABLE_KINDS: Final[frozenset[SourceKind]] = frozenset({"open_web", "review_provider"})

#: The allowlist, canonicalised to the SPDX spelling the schema card's example rows use.
#: One entry per registry family; the registry's shorthand spellings are accepted via
#: :data:`_LICENSE_ALIASES`. ``PD`` has no SPDX identifier — the registry names it "PD".
BUNDLEABLE_LICENSES: Final[frozenset[str]] = frozenset(
    {
        "ODbL-1.0",
        "CDLA-Permissive-2.0",
        # Added 2026-08-01 (ADR-0012). Overture places mixes licenses within one theme:
        # 33 of the 200 fixture rows are Foursquare-sourced Apache-2.0. Omitting it
        # dropped 16.5% of places from every bundle while allowlisting ODbL, which
        # carries share-alike and is strictly more restrictive. Apache-2.0 §4 adds one
        # obligation the pipeline must discharge: reproduce NOTICE-file contents.
        "Apache-2.0",
        # Added 2026-08-07 (ADR-0026). The first entry here added on consistency rather
        # than a measured loss: Apache-2.0 (above) is allowlisted, and MIT is strictly
        # more permissive than it — Apache-2.0 adds a patent grant, a modification
        # notice and NOTICE-file reproduction; MIT asks only that the copyright notice
        # and license text travel with the work. Quarantining the lighter license while
        # allowing the heavier one is indefensible. That single obligation is real,
        # though: the ATTRIBUTION pipeline reproduces it exactly as it does Apache-2.0's.
        "MIT",
        "CC0-1.0",
        "CC-BY-4.0",
        "CC-BY-SA-4.0",
        "PD",
        "OFL-1.1",
        "LGPL-3.0",
    }
)

# Registry shorthand ("ODbL", "CC0", "LGPL-as-dependency") → canonical SPDX id. The
# registry table and the schema card's example rows spell the same license two ways
# ("ODbL" vs "ODbL-1.0"), so canonicalising is required to honour *both* faithfully.
# Lookup is case-insensitive; anything not listed here is simply not allowlisted.
_LICENSE_ALIASES: Final[dict[str, str]] = {
    "odbl": "ODbL-1.0",
    "odbl-1.0": "ODbL-1.0",
    "cdla-permissive-2.0": "CDLA-Permissive-2.0",
    "apache-2.0": "Apache-2.0",
    "mit": "MIT",
    "apache2.0": "Apache-2.0",
    "apache-license-2.0": "Apache-2.0",
    "cc0": "CC0-1.0",
    "cc0-1.0": "CC0-1.0",
    "cc-by-4.0": "CC-BY-4.0",
    "cc-by-sa-4.0": "CC-BY-SA-4.0",
    "pd": "PD",
    "public-domain": "PD",
    "ofl": "OFL-1.1",
    "ofl-1.1": "OFL-1.1",
    "lgpl-as-dependency": "LGPL-3.0",
    "lgpl-3.0": "LGPL-3.0",
    "lgpl-3.0-only": "LGPL-3.0",
    "lgpl-3.0-or-later": "LGPL-3.0",
}

_WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")


def normalize_license(license_id: str) -> str | None:
    """Canonicalise a license stamp to its allowlist spelling, or ``None`` if unknown.

    ``None`` means "not on the allowlist" — it is never an error: a value may perfectly
    well be ``proprietary`` or ``user-owned``; it is simply not bundleable.
    """
    key = _WHITESPACE_RE.sub("", license_id).strip().lower()
    return _LICENSE_ALIASES.get(key)


def is_allowlisted(license_id: str) -> bool:
    """True iff this license may carry ``bundleable=true`` (license test only)."""
    canonical = normalize_license(license_id)
    return canonical is not None and canonical in BUNDLEABLE_LICENSES


def bundleable(kind: str, license_id: str) -> bool:
    """The quarantine rule as a total function: may this value enter an offline bundle?

    ``kind`` is typed ``str`` (not :data:`SourceKind`) deliberately — the guard must also
    hold for stamps arriving as raw strings from adapters, storage or an API payload.
    """
    if kind in NEVER_BUNDLEABLE_KINDS:
        return False
    return is_allowlisted(license_id)


def quarantine_reason(kind: str, license_id: str) -> str:
    """Human-readable *why* for the value :func:`bundleable` computed — used in errors."""
    if kind in NEVER_BUNDLEABLE_KINDS:
        return f"source.kind={kind!r} is never bundleable (DATA-LICENSES.md quarantine rule)"
    if is_allowlisted(license_id):
        canonical = normalize_license(license_id)
        return f"license {license_id!r} is allowlisted (as {canonical})"
    return (
        f"license {license_id!r} is not on the bundleable allowlist "
        f"{sorted(BUNDLEABLE_LICENSES)} (DATA-LICENSES.md quarantine rule)"
    )
