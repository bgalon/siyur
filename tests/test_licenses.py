"""Unit tests — the license quarantine (`commons/licenses.py`), T012 of Spec 001.

The invariant (DATA-LICENSES.md "quarantine rule"; data-model §5.2; Constitution
Article V): `bundleable=true` ⟺ `source.license` is allowlisted, and `open_web` /
`review_provider` are **always** false regardless of license. The last test in the
allowlist section re-parses DATA-LICENSES.md so the registry and the code cannot drift
apart silently.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from commons.licenses import (
    BUNDLEABLE_LICENSES,
    NEVER_BUNDLEABLE_KINDS,
    bundleable,
    is_allowlisted,
    normalize_license,
)
from commons.models import SourcedValue, SourceRef

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_LICENSES = REPO_ROOT / "DATA-LICENSES.md"

# Licenses that appear in the registry (or in the wild) but are **not** on the data
# allowlist. The NC/ND Creative Commons variants are the live ones — Wikimedia Commons
# images carry per-file licenses and the registry excludes NC/ND explicitly, so a
# per-file stamp must never be assumed bundleable from its CC family alone.
#
# Apache-2.0 was on this list until 2026-08-01 and is now allowlisted (ADR-0012): it is
# permissive, and omitting it quarantined 16.5% of Overture places while ODbL — which
# carries share-alike — was allowed. Pinned by the named test below.
#
# MIT left this list on 2026-08-07 (ADR-0026) on a consistency argument rather than a
# measured loss: MIT is strictly more permissive than the Apache-2.0 already allowed
# above, so quarantining it while bundling Apache-2.0 could not be defended. BSD-3-Clause
# stays here deliberately — it is equally permissive and equally defensible to add, but
# nothing has needed it, and the allowlist grows by decision, not by sympathy.
NOT_ALLOWLISTED = [
    "proprietary",
    "user-owned",
    "BSD-3-Clause",
    "AGPL-3.0",
    "CC-BY-NC-4.0",
    "CC-BY-ND-4.0",
    "all-rights-reserved",
    "",
]

ALWAYS_BUNDLEABLE_KINDS = [
    "overture",
    "osm",
    "wikivoyage",
    "wikipedia",
    "wikidata",
    "commons",
    "opening_hours_js",
    "user",
]


# --- allowlist ⟺ bundleable ------------------------------------------------------


@pytest.mark.parametrize("license_id", sorted(BUNDLEABLE_LICENSES))
def test_allowlisted_license_is_bundleable(license_id: str) -> None:
    assert is_allowlisted(license_id)
    assert bundleable("osm", license_id) is True


@pytest.mark.parametrize("license_id", NOT_ALLOWLISTED)
def test_non_allowlisted_license_is_not_bundleable(license_id: str) -> None:
    assert is_allowlisted(license_id) is False
    assert bundleable("overture", license_id) is False


@pytest.mark.parametrize("spelling", ["Apache-2.0", "apache-2.0", "Apache-License-2.0"])
def test_apache_2_0_is_bundleable(spelling: str) -> None:
    """ADR-0012: Overture's Foursquare rows are Apache-2.0 and must reach the bundle.

    Regression guard for the registry gap this closed — 33 of the 200 committed Overture
    fixture rows carry Apache-2.0, so leaving it off the allowlist silently dropped
    16.5% of places from every offline bundle while ODbL, which carries share-alike and
    is strictly more restrictive, was allowed.
    """
    assert normalize_license(spelling) == "Apache-2.0"
    assert is_allowlisted(spelling) is True
    assert bundleable("overture", spelling) is True
    # The kind gate still wins: permissive licensing never rescues a quarantined source.
    assert bundleable("open_web", spelling) is False


@pytest.mark.parametrize(
    ("spelling", "canonical"),
    [
        ("ODbL", "ODbL-1.0"),
        ("ODbL-1.0", "ODbL-1.0"),
        ("odbl-1.0", "ODbL-1.0"),
        ("CC0", "CC0-1.0"),
        ("OFL", "OFL-1.1"),
        ("LGPL-as-dependency", "LGPL-3.0"),
        ("PD", "PD"),
    ],
)
def test_registry_shorthand_normalises_to_the_spdx_spelling(spelling: str, canonical: str) -> None:
    """The registry writes "ODbL"; the schema card's example rows write "ODbL-1.0"."""
    assert normalize_license(spelling) == canonical
    assert canonical in BUNDLEABLE_LICENSES


def test_unknown_license_normalises_to_none_rather_than_raising() -> None:
    # Was "MIT" until 2026-08-07; MIT is allowlisted as of ADR-0026, so this needs a
    # license that is genuinely absent from the alias table to still test what it says.
    assert normalize_license("AGPL-3.0") is None


def test_allowlist_matches_the_registry_document() -> None:
    """Drift tripwire: the allowlist is transcribed from DATA-LICENSES.md, not invented."""
    text = DATA_LICENSES.read_text(encoding="utf-8")
    rule = re.search(r"bundleable=true`\s+\*\*only if\*\*.*?\{([^}]+)\}", text, re.S)
    assert rule is not None, "DATA-LICENSES.md quarantine rule sentence not found"
    documented = {name.strip() for name in rule.group(1).split(",")}
    normalised = {normalize_license(name) for name in documented}
    assert None not in normalised, f"unrecognised license in the registry: {documented}"
    assert normalised == set(BUNDLEABLE_LICENSES)


# --- kind overrides license ------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(NEVER_BUNDLEABLE_KINDS))
@pytest.mark.parametrize("license_id", sorted(BUNDLEABLE_LICENSES))
def test_open_web_and_review_provider_are_never_bundleable(kind: str, license_id: str) -> None:
    """Even a CC0 stamp cannot make an open-web or review-provider value bundleable."""
    assert bundleable(kind, license_id) is False


@pytest.mark.parametrize("kind", ALWAYS_BUNDLEABLE_KINDS)
def test_other_kinds_defer_to_the_license(kind: str) -> None:
    assert bundleable(kind, "CC0-1.0") is True
    assert bundleable(kind, "proprietary") is False


def test_user_data_is_not_bundleable() -> None:
    """`user-owned` is not a bundleable license — personal data never enters a bundle."""
    assert bundleable("user", "user-owned") is False


# --- the stamp cannot be author-set against the rule -----------------------------


def _sourced(kind: str, license_id: str, *, bundleable_flag: bool) -> SourcedValue[str]:
    return SourcedValue[str].model_validate(
        {
            "value": "Palace of the Grand Master",
            "source": {"kind": kind, "id": "x", "license": license_id},
            "bundleable": bundleable_flag,
            "confidence": 0.8,
            "observed_at": date(2026, 7, 22),
        }
    )


def test_bundleable_true_over_a_non_allowlisted_license_is_refused() -> None:
    with pytest.raises(ValidationError, match="quarantine"):
        _sourced("open_web", "all-rights-reserved", bundleable_flag=True)
    with pytest.raises(ValidationError, match="not on the bundleable allowlist"):
        _sourced("overture", "CC-BY-NC-4.0", bundleable_flag=True)


def test_bundleable_true_over_a_never_bundleable_kind_is_refused() -> None:
    with pytest.raises(ValidationError, match="never bundleable"):
        _sourced("review_provider", "CC0-1.0", bundleable_flag=True)


def test_bundleable_false_over_an_allowlisted_license_is_refused() -> None:
    """The rule is an equivalence, so an under-stamped value is a bug too."""
    with pytest.raises(ValidationError, match="quarantine"):
        _sourced("osm", "ODbL-1.0", bundleable_flag=False)


@pytest.mark.parametrize(
    ("kind", "license_id", "expected"),
    [
        ("overture", "CDLA-Permissive-2.0", True),
        ("osm", "ODbL-1.0", True),
        ("wikidata", "CC0-1.0", True),
        ("wikivoyage", "CC-BY-SA-4.0", True),
        ("open_web", "CC0-1.0", False),
        ("review_provider", "proprietary", False),
        ("user", "user-owned", False),
    ],
)
def test_stamp_derives_the_flag_from_the_registry(
    kind: str, license_id: str, *, expected: bool
) -> None:
    """Ingestion never decides bundleability — `SourcedValue.stamp` reads the rule."""
    value = SourcedValue[str].stamp(
        value="Palace of the Grand Master",
        source=SourceRef.model_validate({"kind": kind, "id": "x", "license": license_id}),
        confidence=0.8,
        observed_at=date(2026, 7, 22),
    )
    assert value.bundleable is expected
    assert value.bundleable is bundleable(kind, license_id)
