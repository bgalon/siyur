"""Unit tests — the script guard + Greek→Latin display-name sliver (T060 of Spec 001).

Four things are on trial here, in the order ADR-0010 puts them:

1. **The FAIL-001 guard runs first.** A value whose script contradicts its language tag is
   *flagged and never transliterated* — the Hebrew-address-stored-in-Cyrillic shape. This
   test file is the regression eval that closes FAIL-001 for the name path.
2. **The transform is the rule table, not a letter map** — digraphs (``γγ``/``μπ``/``ντ``/
   ``γκ``), context-sensitive ``αυ``/``ευ``/``ηυ`` voicing, final sigma, accents,
   diaeresis-broken digraphs, casing, polytonic input.
3. **Provenance is inherited, never invented** — the ``el-Latn`` value carries the ``el``
   value's ``SourceRef``, license, attribution and ``bundleable`` stamp; and the original
   ``el`` value survives **unchanged in every case** (FR-008 / SC-004).
4. **Determinism + SC-004 coverage** — same input, same output; ≥95% of non-Latin display
   names get a Latin rendering.

Inputs are built inline (no ``tests/fixtures/``): the expected Latin strings *are* the
specification, so they belong next to the case that asserts them.
"""

from __future__ import annotations

from datetime import date

import pytest
from shapely import Point

from commons.geo import Wgs84Point
from commons.models import SiteRecordV1, SourcedValue, SourceRef
from commons.translit import (
    BASE_CONFIDENCE,
    ScriptMismatchError,
    check_script,
    derive_latin_name,
    derive_latin_names,
    latin_tag,
    transliterate_greek,
    transliteration_confidence,
)

OSM = SourceRef(
    kind="osm",
    id="node/123456",
    license="ODbL-1.0",
    attribution="© OpenStreetMap contributors",
)
#: `open_web` is never bundleable whatever its license — the derived name must inherit
#: that quarantine, not launder it.
OPEN_WEB = SourceRef(
    kind="open_web",
    id="https://example.invalid/place",
    license="CC-BY-4.0",
    attribution="Example",
)
OBSERVED = date(2026, 7, 22)
DERIVED_ON = date(2026, 8, 1)


def _name(text: str, source: SourceRef = OSM, confidence: float = 0.8) -> SourcedValue[str]:
    return SourcedValue[str].stamp(
        value=text,
        source=source,
        confidence=confidence,
        observed_at=OBSERVED,
    )


# --------------------------------------------------------------------------------------
# 2. The ELOT 743 / UN-GEGN rule table
# --------------------------------------------------------------------------------------

#: ``(Greek, expected Latin)`` — the snapshot ADR-0010 asks for. Also the SC-004 corpus.
ELOT_743_CASES: tuple[tuple[str, str], ...] = (
    # plain letters, final sigma, accents dropped
    ("Ρόδος", "Rodos"),
    ("Λίνδος", "Lindos"),
    ("Σάμος", "Samos"),
    ("Κως", "Kos"),
    ("Αθήνα", "Athina"),
    ("Θεσσαλονίκη", "Thessaloniki"),
    ("Ακρόπολη", "Akropoli"),
    ("Πλάκα", "Plaka"),
    ("Μοναστηράκι", "Monastiraki"),
    ("Σαντορίνη", "Santorini"),
    ("Μύκονος", "Mykonos"),
    ("Κέρκυρα", "Kerkyra"),
    ("Ζάκυνθος", "Zakynthos"),
    ("Δελφοί", "Delfoi"),
    ("Ολυμπία", "Olympia"),
    ("Μετέωρα", "Meteora"),
    ("Κνωσός", "Knosos"),
    # multi-letter singles: θ→th, χ→ch, ψ→ps, ξ→x, υ→y
    ("Θήρα", "Thira"),
    ("Χανιά", "Chania"),
    ("Ψαρά", "Psara"),
    ("Ύδρα", "Ydra"),
    ("Σύμη", "Symi"),
    # γ-clusters: γγ→ng, γξ→nx, γχ→nch
    ("Άγγελος", "Angelos"),
    ("σφίγγα", "sfinga"),
    ("έλεγχος", "elenchos"),
    ("Σφίγξ", "Sfinx"),
    # voiced stops: word-initial b/d/g, medial mp/nt/ng
    ("Μπενάκη", "Benaki"),
    ("λάμπα", "lampa"),
    ("Ντίνος", "Dinos"),
    ("πέντε", "pente"),
    ("Γκίκας", "Gikas"),
    ("Άγκυρα", "Angyra"),
    # αυ/ευ voicing by context
    ("Παύλος", "Pavlos"),  # before λ (voiced) → av
    ("Ναύπλιο", "Nafplio"),  # before π (unvoiced) → af
    ("Επίδαυρος", "Epidavros"),  # before ρ (voiced) → av
    ("Ευαγγελισμός", "Evangelismos"),  # before a vowel → ev
    ("Ευχαριστώ", "Efcharisto"),  # before χ (unvoiced) → ef
    # vowel digraphs: αι, ει, οι, ου, υι
    ("Πειραιάς", "Peiraias"),
    ("Φαιστός", "Faistos"),
    ("Ερεχθείο", "Erechtheio"),
    ("Παλαιά Πόλη", "Palaia Poli"),
    ("Σούνιο", "Sounio"),
    ("υιός", "yios"),
    # digraph breakers: accent on the first vowel, diaeresis on the second
    ("Ρολόι", "Roloi"),
    ("προϋπολογισμός", "proypologismos"),  # not "prou-": ϋ breaks ου
    ("Αϋπνία", "Aypnia"),  # not "Af-": ϋ breaks αυ
    ("Μωϋσής", "Moysis"),
    # casing is a per-word decision
    ("ΡΟΔΟΣ", "RODOS"),
    ("ΑΘΗΝΑ", "ATHINA"),
    ("ΘΗΡΑ", "THIRA"),
    ("ΧΑΝΙΑ", "CHANIA"),
    ("ΜΠΑΜΠΗΣ", "BAMPIS"),
    # multi-word + non-Greek passthrough
    ("Παλάτι του Μεγάλου Μαγίστρου", "Palati tou Megalou Magistrou"),
    ("Οδός Ιπποτών", "Odos Ippoton"),
    ("Ρόδος (Rhodes)", "Rodos (Rhodes)"),
    ("Καστελλόριζο", "Kastellorizo"),
    ("Λυκαβηττός", "Lykavittos"),
    # polytonic input: breathings/perispomeni are dropped like the tonos
    ("Ἀθῆναι", "Athinai"),
    ("Θησεῖον", "Thiseion"),
)


@pytest.mark.parametrize(("greek", "expected"), ELOT_743_CASES)
def test_elot_743_rule_table(greek: str, expected: str) -> None:
    """The transform is a fixed function to a known string (ADR-0010 "Confirmation")."""
    assert transliterate_greek(greek) == expected


@pytest.mark.parametrize(
    ("greek", "expected"),
    [
        ("ηυ", "if"),  # end of word → unvoiced
        ("ηυα", "iva"),  # before a vowel → voiced
        ("αυ", "af"),
        ("αυβ", "avv"),  # before β (voiced)
        ("αυτ", "aft"),  # before τ (unvoiced)
        ("ευ", "ef"),
        ("ευρ", "evr"),
    ],
)
def test_upsilon_digraph_voicing_is_contextual(greek: str, expected: str) -> None:
    """``αυ``/``ευ``/``ηυ`` are the context rule, not a letter pair — end-of-word devoices."""
    assert transliterate_greek(greek) == expected


def test_final_sigma_and_medial_sigma_agree() -> None:
    assert transliterate_greek("σοφός") == "sofos"
    assert transliterate_greek("ΣΟΦΟΣ") == "SOFOS"


def test_non_greek_text_passes_through_untouched() -> None:
    assert transliterate_greek("Palace of the Grand Master") == "Palace of the Grand Master"
    assert transliterate_greek("28.2247, 36.4443") == "28.2247, 36.4443"
    assert transliterate_greek("") == ""


@pytest.mark.parametrize("greek", [pair[0] for pair in ELOT_743_CASES])
def test_transliteration_is_deterministic(greek: str) -> None:
    """Same input → same output, every time: SC-004 is snapshot-gated, not judged."""
    first = transliterate_greek(greek)
    assert all(transliterate_greek(greek) == first for _ in range(5))


def test_derivation_is_deterministic_end_to_end() -> None:
    names = {"el": _name("Παλάτι του Μεγάλου Μαγίστρου")}
    runs = [derive_latin_names(names, observed_at=DERIVED_ON) for _ in range(3)]
    assert all(run.names["el-Latn"] == runs[0].names["el-Latn"] for run in runs)


# --------------------------------------------------------------------------------------
# 1. The FAIL-001 script-validation guard
# --------------------------------------------------------------------------------------


def test_fail001_cyrillic_in_a_hebrew_field_is_flagged() -> None:
    """The literal FAIL-001 datum: `Сгула 13` — a Hebrew street written in Cyrillic."""
    check = check_script("Сгула 13", "he")
    assert check.status == "mismatch"
    assert not check.ok
    assert check.detected == "Cyrl"
    assert "FAIL-001" in check.reason


def test_fail001_mismatch_is_never_transliterated() -> None:
    """Flagged, not "best-effort" — a mismatch means the *input* cannot be trusted."""
    entry = _name("Сгула 13")
    with pytest.raises(ScriptMismatchError) as excinfo:
        derive_latin_name(entry, "el", observed_at=DERIVED_ON)
    assert excinfo.value.check.status == "mismatch"


def test_mismatched_entry_is_flagged_and_yields_no_derived_value() -> None:
    names = {"el": _name("Сгула 13"), "en": _name("Sgula 13")}
    result = derive_latin_names(names, observed_at=DERIVED_ON)

    assert result.derived == ()
    assert [flag.tag for flag in result.flags] == ["el"]
    assert result.flags[0].value == "Сгула 13"
    assert "FAIL-001" in result.flags[0].reason
    # …and the untrustworthy original is still there, untouched, for a human to see.
    assert result.names["el"] is names["el"]


def test_guard_accepts_a_latin_gloss_inside_a_greek_name() -> None:
    """Mixed Latin is benign annotation, not substitution — it must not false-positive."""
    check = check_script("Ρόδος (Rhodes)", "el")
    assert check.ok
    assert check.mixed


def test_guard_flags_an_all_latin_value_under_a_greek_tag() -> None:
    assert check_script("Rodos", "el").status == "mismatch"


def test_guard_makes_no_claim_for_an_unknown_language() -> None:
    """An "unknown" verdict is not a pass — nothing is derived from an unvouched script."""
    check = check_script("Ρόδος", "qq")
    assert check.status == "unknown"
    assert not check.ok
    assert derive_latin_names({"qq": _name("Ρόδος")}, observed_at=DERIVED_ON).derived == ()


def test_guard_treats_a_letterless_value_as_empty_not_a_pass() -> None:
    check = check_script("13", "el")
    assert check.status == "empty"
    assert not check.ok


def test_guard_screens_any_field_not_just_names() -> None:
    """Addresses are never transliterated (ADR-0010) but are still screened by the guard."""
    assert check_script("סגולה 13", "he").ok
    assert check_script("Сгула 13", "he").status == "mismatch"


# --------------------------------------------------------------------------------------
# 3. Provenance inheritance + the original is never overwritten
# --------------------------------------------------------------------------------------


def test_derived_name_inherits_the_source_ref_verbatim() -> None:
    original = _name("Ρόδος")
    derived = derive_latin_name(original, "el", observed_at=DERIVED_ON)

    assert derived is not None
    assert derived.value == "Rodos"
    # Inherited, not invented: same SourceRef object → same kind/id/license/attribution.
    assert derived.source is original.source
    assert derived.source.license == "ODbL-1.0"
    assert derived.source.attribution == "© OpenStreetMap contributors"
    assert derived.bundleable is original.bundleable is True
    # …and the two fields that are genuinely the derivation's own.
    assert derived.observed_at == DERIVED_ON
    assert 0.0 < derived.confidence <= BASE_CONFIDENCE


def test_derived_name_inherits_an_unbundleable_stamp_too() -> None:
    """A produced work is no more bundleable than its parent — no laundering."""
    original = _name("Ρόδος", source=OPEN_WEB)
    derived = derive_latin_name(original, "el", observed_at=DERIVED_ON)

    assert derived is not None
    assert original.bundleable is False
    assert derived.bundleable is False
    assert derived.source is OPEN_WEB


def test_no_derived_source_kind_is_invented() -> None:
    """ADR-0010 P1, not P2: the schema card's `SourceRef.kind` enum is untouched."""
    derived = derive_latin_name(_name("Ρόδος"), "el", observed_at=DERIVED_ON)
    assert derived is not None
    assert derived.source.kind == "osm"


@pytest.mark.parametrize(("greek", "expected"), ELOT_743_CASES)
def test_original_value_and_attribution_survive_every_case(greek: str, expected: str) -> None:
    """SC-004's "original preserved in **every** case", asserted case by case."""
    names = {"el": _name(greek)}
    result = derive_latin_names(names, observed_at=DERIVED_ON)

    assert result.names["el"] is names["el"]
    assert result.names["el"].value == greek
    assert result.names["el"].source.attribution == "© OpenStreetMap contributors"
    assert result.names["el"].confidence == 0.8
    assert result.names["el"].observed_at == OBSERVED
    assert result.names["el-Latn"].value == expected


def test_existing_latin_name_from_a_source_is_never_overwritten() -> None:
    supplied = _name("Rhodes")
    names = {"el": _name("Ρόδος"), "el-Latn": supplied}
    result = derive_latin_names(names, observed_at=DERIVED_ON)

    assert result.derived == ()
    assert result.names["el-Latn"] is supplied


def test_latin_script_and_unsupported_scripts_derive_nothing() -> None:
    names = {
        "en": _name("Palace of the Grand Master"),
        "el-Latn": _name("Rodos"),
        "he": _name("רודוס"),  # Hebrew is an M3 table, not this sliver
    }
    result = derive_latin_names(names, observed_at=DERIVED_ON)
    assert result.derived == ()
    assert result.flags == ()
    assert dict(result.names) == names


@pytest.mark.parametrize(
    ("tag", "expected"),
    [("el", "el-Latn"), ("el-GR", "el-Latn-GR"), ("he", "he-Latn"), ("ja", "ja-Latn")],
)
def test_latin_tag_is_the_bcp47_key(tag: str, expected: str) -> None:
    assert latin_tag(tag) == expected


def test_derived_key_validates_as_a_site_record_name() -> None:
    """The derived key must survive `Bcp47Tag` — the model is the arbiter, not this module."""
    result = derive_latin_names({"el": _name("Ρόδος")}, observed_at=DERIVED_ON)
    record = SiteRecordV1(
        names=dict(result.names),
        location=SourcedValue[Wgs84Point].stamp(
            value=Point(28.2247, 36.4443),
            source=OSM,
            confidence=0.9,
            observed_at=OBSERVED,
        ),
    )
    assert record.names["el-Latn"].value == "Rodos"
    assert record.names["el"].value == "Ρόδος"


def test_confidence_drops_when_letters_survive_untransliterated() -> None:
    """Certainty is measured, not asserted: leftover foreign letters lower it."""
    assert transliteration_confidence("Rodos") == BASE_CONFIDENCE
    assert transliteration_confidence("Rodoс") < BASE_CONFIDENCE
    assert transliteration_confidence("13") == 0.0


# --------------------------------------------------------------------------------------
# 4. SC-004 — ≥95% of non-Latin display names get a Latin rendering
# --------------------------------------------------------------------------------------


def test_sc004_coverage_of_non_latin_display_names() -> None:
    """≥95% coverage, measured over the corpus **plus** a FAIL-001 mis-scripted name.

    The flagged name deliberately counts *against* coverage: the bar has to be clearable
    while still refusing to transliterate untrustworthy input.
    """
    corpus = [greek for greek, _ in ELOT_743_CASES] + ["Сгула 13"]
    rendered = 0
    for greek in corpus:
        result = derive_latin_names({"el": _name(greek)}, observed_at=DERIVED_ON)
        latin = result.names.get("el-Latn")
        assert result.names["el"].value == greek  # original preserved in every case
        if latin is not None and latin.value.strip():
            rendered += 1

    coverage = rendered / len(corpus)
    assert coverage >= 0.95, f"SC-004: only {coverage:.1%} of non-Latin names got a rendering"
    assert rendered == len(corpus) - 1  # exactly one refusal, and it is the flagged one
