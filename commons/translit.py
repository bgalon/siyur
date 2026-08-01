"""Script validation + deterministic Greek→Latin display-name transliteration (ADR-0010).

Two jobs, in this order — the guard **always** runs before the transform:

**1. The script-validation guard (FAIL-001, T056).** Source scripts are *claimed*, not
authoritative: the discovery spike found the Hebrew street ``סגולה`` stored as the Cyrillic
``Сгула`` in an Overture address. So before anything is derived, :func:`check_script`
asserts the value's actual script (by Unicode codepoint range) against the script its
BCP-47 tag declares. A mismatch is **flagged and never trusted** — never silently
transliterated, and never repaired by guesswork. Addresses are out of scope for
transliteration entirely (ADR-0010), but :func:`check_script` is deliberately generic so
the same guard can be pointed at any free-text field (and at the FAIL-001 regression eval).

**2. The transform (T057) + provenance inheritance (T058).** ``el`` → ``el-Latn``:
a pure, offline, dependency-free rule table — same input, same output, forever. The LLM
**never** performs this (Constitution: the model curates prose, it does not do arithmetic,
coordinates, or transliteration). The derived value inherits the source value's
:class:`~commons.models.SourceRef` verbatim (a transliteration is a *produced work* of the
source datum), so license, attribution and the ``bundleable`` stamp carry through
unchanged — no invented ``SourceRef.kind``, no schema change. **The original-script value
is never overwritten** (FR-008 / SC-004; data-model §3, §5 rule 6).

Romanization standard
---------------------

Implemented: **ELOT 743 / UN-GEGN (1987) romanization of Greek** — the *transcription*
variant (the same system ISO 843 calls "type 1"; it is the basis of the Greek passport
tables). It is a rule table, not a letter map: digraphs, context-sensitive voicing and
accent/diaeresis-driven digraph breaking are what make it correct.

===========  ==============================================  ====================
Input        Rule                                            Example
===========  ==============================================  ====================
``γγ γξ γχ`` ``ng`` / ``nx`` / ``nch``                       Άγγελος → Angelos
``μπ``       ``b`` word-initially, ``mp`` elsewhere          Μπενάκη → Benaki
``ντ``       ``d`` word-initially, ``nt`` elsewhere          Ντίνος → Dinos
``γκ``       ``g`` word-initially, ``ng`` elsewhere          Άγκυρα → Angyra
``αυ ευ ηυ`` ``av/ev/iv`` before a voiced sound,             Παύλος → Pavlos
             ``af/ef/if`` before an unvoiced one or at       Ευχαριστώ → Efcharisto
             the end of the word
``αι ει οι`` ``ai`` / ``ei`` / ``oi``                        Πειραιάς → Peiraias
``ου υι``    ``ou`` / ``yi``                                 Μεγάλου → Megalou
``ς``        final sigma ⇒ ``s`` like ``σ``                   Ρόδος → Rodos
accents      dropped from the output, but an accent on       Ρολόι → Roloi
             the **first** vowel breaks the digraph
diaeresis    on the **second** vowel breaks the digraph      προϋπολογισμός →
                                                             proypologismos
===========  ==============================================  ====================

Casing is decided per word (a maximal run of Greek letters): all-uppercase in ⇒
all-uppercase out (``ΡΟΔΟΣ`` → ``RODOS``), leading capital ⇒ leading capital
(``Θήρα`` → ``Thira``), else lowercase. Non-Greek characters pass through untouched.

Two documented judgement calls, both deterministic and both visible here rather than
buried in a dependency:

* ``μπ / ντ / γκ`` use the **transcription** readings (``b / d / g`` word-initially).
  The reversible ISO 843 "type 2" transliteration would emit ``mp / nt / gk``
  everywhere; these are traveler-facing *display* names, so the readable variant wins.
* Polytonic breathings, the iota subscript and the perispomeni are treated exactly like
  the monotonic tonos: dropped from the output, and they break a following digraph.

Engine deviation from ADR-0010 (recorded for ratification)
----------------------------------------------------------

ADR-0010 names the **ICU ``Greek-Latin`` transform** as the engine. PyICU cannot be used
here: PyPI ships it as an sdist only, and building it needs system ``pkg-config`` +
``libicu-dev``, which the CI runners lack and which cannot be added (``.github/workflows``
is review-gated). ASCII-folding libraries (``anyascii``/``Unidecode``/``transliterate``)
are *not* a substitute — they are ADR-0010's rejected option E3 by another name, and carry
GPL risk. This module therefore keeps every property ADR-0010 actually decided on —
deterministic, offline, rule-based, reproducible, display-name-only, provenance-inheriting,
script-validated, **never the LLM** — and changes only the engine: an in-repo rule table
with **zero dependencies**. The ADR needs an amendment ratifying the engine swap; that is
Ben's call, not this module's.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import Final, Literal

from commons.models import SourcedValue

__all__ = [
    "BASE_CONFIDENCE",
    "LATIN_SCRIPT",
    "SUPPORTED_SOURCE_SCRIPTS",
    "ScriptCheck",
    "ScriptFlag",
    "ScriptMismatchError",
    "ScriptStatus",
    "TransliterationResult",
    "check_script",
    "derive_latin_name",
    "derive_latin_names",
    "detect_script",
    "expected_scripts",
    "latin_tag",
    "script_counts",
    "script_of",
    "transliterate_greek",
    "transliteration_confidence",
]

# --------------------------------------------------------------------------------------
# Script detection — ISO 15924 codes, so they line up with BCP-47 script subtags
# --------------------------------------------------------------------------------------

LATIN_SCRIPT: Final[str] = "Latn"

#: ``(first, last, ISO 15924)`` codepoint ranges. Only characters whose Unicode general
#: category starts with ``L`` are classified, so punctuation/digits/marks inside these
#: blocks are ignored rather than mis-attributed.
_SCRIPT_RANGES: Final[tuple[tuple[int, int, str], ...]] = (
    (0x0041, 0x005A, LATIN_SCRIPT),
    (0x0061, 0x007A, LATIN_SCRIPT),
    (0x00C0, 0x024F, LATIN_SCRIPT),
    (0x1E00, 0x1EFF, LATIN_SCRIPT),
    (0x0370, 0x03FF, "Grek"),
    (0x1F00, 0x1FFF, "Grek"),
    (0x0400, 0x052F, "Cyrl"),
    (0x2DE0, 0x2DFF, "Cyrl"),
    (0x0530, 0x058F, "Armn"),
    (0x0590, 0x05FF, "Hebr"),
    (0x0600, 0x06FF, "Arab"),
    (0x0750, 0x077F, "Arab"),
    (0x08A0, 0x08FF, "Arab"),
    (0x0780, 0x07BF, "Thaa"),
    (0x0900, 0x097F, "Deva"),
    (0x0980, 0x09FF, "Beng"),
    (0x0A00, 0x0A7F, "Guru"),
    (0x0A80, 0x0AFF, "Gujr"),
    (0x0B00, 0x0B7F, "Orya"),
    (0x0B80, 0x0BFF, "Taml"),
    (0x0C00, 0x0C7F, "Telu"),
    (0x0C80, 0x0CFF, "Knda"),
    (0x0D00, 0x0D7F, "Mlym"),
    (0x0D80, 0x0DFF, "Sinh"),
    (0x0E00, 0x0E7F, "Thai"),
    (0x0E80, 0x0EFF, "Laoo"),
    (0x0F00, 0x0FFF, "Tibt"),
    (0x1000, 0x109F, "Mymr"),
    (0x10A0, 0x10FF, "Geor"),
    (0x1C90, 0x1CBF, "Geor"),
    (0x1200, 0x137F, "Ethi"),
    (0x1780, 0x17FF, "Khmr"),
    (0x1100, 0x11FF, "Hang"),
    (0xAC00, 0xD7AF, "Hang"),
    (0x3040, 0x309F, "Hira"),
    (0x30A0, 0x30FF, "Kana"),
    (0x3400, 0x4DBF, "Hani"),
    (0x4E00, 0x9FFF, "Hani"),
    (0xF900, 0xFAFF, "Hani"),
)

#: Scripts a language may legitimately be written in when its BCP-47 tag carries **no**
#: explicit script subtag. Absent from this table ⇒ we make no claim (``status="unknown"``)
#: rather than guessing — the guard never invents a verdict it cannot justify.
_DEFAULT_SCRIPTS: Final[Mapping[str, frozenset[str]]] = {
    "el": frozenset({"Grek"}),
    "he": frozenset({"Hebr"}),
    "yi": frozenset({"Hebr"}),
    "ru": frozenset({"Cyrl"}),
    "uk": frozenset({"Cyrl"}),
    "be": frozenset({"Cyrl"}),
    "bg": frozenset({"Cyrl"}),
    "mk": frozenset({"Cyrl"}),
    "sr": frozenset({"Cyrl", LATIN_SCRIPT}),
    "ar": frozenset({"Arab"}),
    "fa": frozenset({"Arab"}),
    "ur": frozenset({"Arab"}),
    "hy": frozenset({"Armn"}),
    "ka": frozenset({"Geor"}),
    "am": frozenset({"Ethi"}),
    "ti": frozenset({"Ethi"}),
    "hi": frozenset({"Deva"}),
    "mr": frozenset({"Deva"}),
    "ne": frozenset({"Deva"}),
    "bn": frozenset({"Beng"}),
    "pa": frozenset({"Guru"}),
    "gu": frozenset({"Gujr"}),
    "or": frozenset({"Orya"}),
    "ta": frozenset({"Taml"}),
    "te": frozenset({"Telu"}),
    "kn": frozenset({"Knda"}),
    "ml": frozenset({"Mlym"}),
    "si": frozenset({"Sinh"}),
    "th": frozenset({"Thai"}),
    "lo": frozenset({"Laoo"}),
    "bo": frozenset({"Tibt"}),
    "my": frozenset({"Mymr"}),
    "km": frozenset({"Khmr"}),
    "dv": frozenset({"Thaa"}),
    "ko": frozenset({"Hang"}),
    "zh": frozenset({"Hani"}),
    "ja": frozenset({"Hira", "Kana", "Hani"}),
    **{
        code: frozenset({LATIN_SCRIPT})
        for code in (
            "en fr de es it pt nl sv no da fi is pl cs sk hu ro hr sl et lv lt tr id ms "
            "vi sw af ca gl eu cy ga sq az uz tl mt"
        ).split()
    },
}

ScriptStatus = Literal["match", "mismatch", "unknown", "empty"]


def script_of(char: str) -> str | None:
    """ISO 15924 code for a single character, or ``None`` if it is not a classified letter.

    Non-letters (digits, punctuation, spaces, combining marks) are ``None`` by design: they
    are script-neutral and must not sway the verdict of :func:`check_script`.
    """
    if not unicodedata.category(char).startswith("L"):
        return None
    code = ord(char)
    for first, last, script in _SCRIPT_RANGES:
        if first <= code <= last:
            return script
    return None


def script_counts(text: str) -> dict[str, int]:
    """Letter counts per ISO 15924 script, e.g. ``{"Grek": 5, "Latn": 6}``."""
    counts: dict[str, int] = {}
    for char in unicodedata.normalize("NFC", text):
        script = script_of(char)
        if script is not None:
            counts[script] = counts.get(script, 0) + 1
    return counts


def detect_script(text: str) -> str | None:
    """The dominant script of ``text``, or ``None`` if it carries no classified letter.

    Ties break alphabetically so the answer is a pure function of the input.
    """
    counts = script_counts(text)
    if not counts:
        return None
    return max(sorted(counts), key=lambda script: counts[script])


def _parse_tag(tag: str) -> tuple[str, str | None, str | None]:
    """Split a BCP-47 subtag into ``(language, script, region)`` (see ``models.Bcp47Tag``)."""
    parts = tag.split("-")
    language = parts[0].lower()
    script: str | None = None
    region: str | None = None
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            script = part.title()
        else:
            region = part.upper()
    return language, script, region


def expected_scripts(tag: str) -> frozenset[str]:
    """Scripts a value tagged ``tag`` may legitimately be written in.

    An explicit script subtag always wins (``el-Latn`` ⇒ ``{"Latn"}``); otherwise the
    language's conventional script(s). Empty ⇒ unknown language, no claim is made.
    """
    language, script, _ = _parse_tag(tag)
    if script is not None:
        return frozenset({script})
    return _DEFAULT_SCRIPTS.get(language, frozenset())


@dataclass(frozen=True, slots=True)
class ScriptCheck:
    """The verdict of the FAIL-001 guard for one value.

    ``status`` is deliberately four-valued: ``"unknown"`` (no expectation on record) and
    ``"empty"`` (nothing to judge) are *not* passes, so a caller cannot read a missing
    expectation as an endorsement.
    """

    tag: str
    text: str
    expected: frozenset[str]
    detected: str | None
    #: ``((script, letter_count), ...)``, sorted — evidence behind the verdict.
    counts: tuple[tuple[str, int], ...]
    status: ScriptStatus
    reason: str

    @property
    def ok(self) -> bool:
        """True only for a positive match — the sole state in which deriving is allowed."""
        return self.status == "match"

    @property
    def mixed(self) -> bool:
        """True when more than one script is present (a Latin word inside a Greek name)."""
        return len(self.counts) > 1


class ScriptMismatchError(ValueError):
    """A value's actual script contradicts its declared language tag (FAIL-001).

    Raised instead of returning a best-effort transliteration: the mismatch means the
    *input* is untrustworthy, and transliterating it would launder bad data into a
    confident-looking display name.
    """

    def __init__(self, check: ScriptCheck) -> None:
        super().__init__(check.reason)
        self.check = check


def check_script(text: str, tag: str) -> ScriptCheck:
    """Assert that ``text`` is actually written in the script ``tag`` declares (T056).

    The verdict is **not** "dominant script == declared script": real display names mix
    scripts benignly (``"Ρόδος (Rhodes)"``, a Latin brand name in a Greek field), and a
    Latin gloss is exactly what this module produces anyway. The rule is therefore:

    * the declared script must be **present** — its total absence is the FAIL-001 shape
      (a Hebrew address that contains no Hebrew at all, only Cyrillic); and
    * no **other non-Latin** script may match or outnumber it — a competing foreign script
      means the value has been substituted, not annotated.

    Generic on purpose: names are the only thing this slice *derives* from, but the same
    guard is what an address or any other free-text field is screened with (ADR-0010:
    addresses are validated, never transliterated).
    """
    expected = expected_scripts(tag)
    counts = script_counts(text)
    evidence = tuple(sorted(counts.items()))
    detected = detect_script(text)
    declared_letters = sum(count for script, count in evidence if script in expected)
    rivals = {
        script: count
        for script, count in evidence
        if script not in expected and script != LATIN_SCRIPT
    }
    strongest_rival = max(sorted(rivals), key=lambda script: rivals[script]) if rivals else None

    if detected is None:
        status: ScriptStatus = "empty"
        reason = f"{tag!r} value carries no classified letters; nothing to validate"
    elif not expected:
        status = "unknown"
        reason = (
            f"no expected script on record for language tag {tag!r} "
            f"(detected {detected}); the guard makes no claim"
        )
    elif declared_letters == 0:
        status = "mismatch"
        reason = (
            f"script mismatch (FAIL-001): {tag!r} declares "
            f"{'/'.join(sorted(expected))} but the value contains none of it — it is "
            f"written in {detected} ({evidence}) — flagged, not trusted"
        )
    elif strongest_rival is not None and rivals[strongest_rival] >= declared_letters:
        status = "mismatch"
        reason = (
            f"script mismatch (FAIL-001): {tag!r} declares "
            f"{'/'.join(sorted(expected))} but {strongest_rival} letters match or "
            f"outnumber them ({evidence}) — flagged, not trusted"
        )
    else:
        status = "match"
        reason = f"{tag!r} value is written in {'/'.join(sorted(expected))}, as declared"
    return ScriptCheck(
        tag=tag,
        text=text,
        expected=expected,
        detected=detected,
        counts=evidence,
        status=status,
        reason=reason,
    )


# --------------------------------------------------------------------------------------
# The Greek→Latin rule table (ELOT 743 / UN-GEGN) — see the module docstring
# --------------------------------------------------------------------------------------

#: Combining marks that count as an accent: monotonic tonos/oxia, varia, perispomeni,
#: the polytonic breathings and the iota subscript. All are dropped from the output; all
#: break a digraph when they sit on its **first** vowel.
_ACCENT_MARKS: Final[frozenset[str]] = frozenset(
    {
        "\u0300",  # varia / grave
        "\u0301",  # tonos / oxia — the monotonic accent
        "\u0302",  # circumflex
        "\u0303",  # tilde
        "\u0304",  # macron
        "\u0306",  # breve
        "\u0313",  # psili (smooth breathing)
        "\u0314",  # dasia (rough breathing)
        "\u0342",  # perispomeni
        "\u0345",  # ypogegrammeni (iota subscript)
    }
)
#: Diaeresis — the *other* digraph breaker, and the only mark that is not an accent.
_DIAERESIS: Final[str] = "\u0308"

#: Presentation variants folded onto their normal letter before any rule is applied.
_VARIANT_FOLD: Final[Mapping[str, str]] = {
    "ϐ": "β",  # ϐ curled beta
    "ϑ": "θ",  # ϑ script theta
    "ϰ": "κ",  # ϰ script kappa
    "ϱ": "ρ",  # ϱ tailed rho
    "ϲ": "σ",  # ϲ lunate sigma
    "ς": "σ",  # final sigma — same sound, same Latin letter
}

_SINGLE: Final[Mapping[str, str]] = {
    "α": "a",
    "β": "v",
    "γ": "g",
    "δ": "d",
    "ε": "e",
    "ζ": "z",
    "η": "i",
    "θ": "th",
    "ι": "i",
    "κ": "k",
    "λ": "l",
    "μ": "m",
    "ν": "n",
    "ξ": "x",
    "ο": "o",
    "π": "p",
    "ρ": "r",
    "σ": "s",
    "τ": "t",
    "υ": "y",
    "φ": "f",
    "χ": "ch",
    "ψ": "ps",
    "ω": "o",
}

#: ``γ`` before a velar/``γ`` nasalises — the one cluster rule with no positional variant.
_GAMMA_CLUSTERS: Final[Mapping[tuple[str, str], str]] = {
    ("γ", "γ"): "ng",
    ("γ", "ξ"): "nx",
    ("γ", "χ"): "nch",
}

#: Voiced-stop digraphs: ``(word-initial, elsewhere)``.
_STOP_DIGRAPHS: Final[Mapping[tuple[str, str], tuple[str, str]]] = {
    ("μ", "π"): ("b", "mp"),
    ("ν", "τ"): ("d", "nt"),
    ("γ", "κ"): ("g", "ng"),
}

_VOWEL_DIGRAPHS: Final[Mapping[tuple[str, str], str]] = {
    ("α", "ι"): "ai",
    ("ε", "ι"): "ei",
    ("ο", "ι"): "oi",
    ("ο", "υ"): "ou",
    ("υ", "ι"): "yi",
}

#: ``αυ``/``ευ``/``ηυ`` — the ``υ`` becomes ``v`` or ``f`` depending on what follows.
_UPSILON_HEADS: Final[Mapping[str, str]] = {"α": "a", "ε": "e", "η": "i"}

#: A following vowel or voiced consonant voices the ``υ`` (``av``); anything else — an
#: unvoiced consonant or the end of the word — devoices it (``af``).
_VOICING_FOLLOWERS: Final[frozenset[str]] = frozenset(
    "αεηιουω" + "βγδζλμνρ",
)

_GREEK_LETTERS: Final[frozenset[str]] = frozenset(_SINGLE)


@dataclass(frozen=True, slots=True)
class _Letter:
    """One Greek letter, stripped to what the rules actually key on."""

    base: str
    upper: bool
    accented: bool
    diaeresis: bool


def _parse_letter(char: str) -> _Letter | None:
    """Decompose ``char`` into base letter + marks; ``None`` if it is not Greek."""
    decomposed = unicodedata.normalize("NFD", char)
    base = decomposed[0]
    marks = decomposed[1:]
    lowered = base.lower()
    lowered = _VARIANT_FOLD.get(lowered, lowered)
    if lowered not in _GREEK_LETTERS:
        return None
    return _Letter(
        base=lowered,
        upper=base.isupper(),
        accented=any(mark in _ACCENT_MARKS for mark in marks),
        diaeresis=_DIAERESIS in marks,
    )


def _forms_digraph(first: _Letter, second: _Letter) -> bool:
    """Greek orthography's two digraph breakers: an accent on the first vowel
    (``πλάι``), or a diaeresis on the second (``προϋ-``)."""
    return not first.accented and not second.diaeresis


def _transliterate_word(word: Sequence[_Letter]) -> str:
    """Apply the rule table to one word (a maximal run of Greek letters), lowercase."""
    out: list[str] = []
    index = 0
    length = len(word)
    while index < length:
        current = word[index]
        following = word[index + 1] if index + 1 < length else None
        if following is not None:
            pair = (current.base, following.base)
            cluster = _GAMMA_CLUSTERS.get(pair)
            if cluster is not None:
                out.append(cluster)
                index += 2
                continue
            stop = _STOP_DIGRAPHS.get(pair)
            if stop is not None:
                out.append(stop[0] if index == 0 else stop[1])
                index += 2
                continue
            if _forms_digraph(current, following):
                head = _UPSILON_HEADS.get(current.base)
                if following.base == "υ" and head is not None:
                    after = word[index + 2] if index + 2 < length else None
                    voiced = after is not None and after.base in _VOICING_FOLLOWERS
                    out.append(head + ("v" if voiced else "f"))
                    index += 2
                    continue
                vowels = _VOWEL_DIGRAPHS.get(pair)
                if vowels is not None:
                    out.append(vowels)
                    index += 2
                    continue
        out.append(_SINGLE[current.base])
        index += 1
    return "".join(out)


def _apply_case(word: Sequence[_Letter], latin: str) -> str:
    """Casing is a per-word decision — see the module docstring."""
    if all(letter.upper for letter in word):
        return latin.upper()
    if word[0].upper:
        return latin[:1].upper() + latin[1:]
    return latin


def transliterate_greek(text: str) -> str:
    """Romanize Greek text per ELOT 743 / UN-GEGN (T057) — pure, offline, deterministic.

    Non-Greek characters (spaces, punctuation, digits, already-Latin words) pass through
    unchanged, so ``"Ρόδος (Rhodes)"`` → ``"Rodos (Rhodes)"``.
    """
    out: list[str] = []
    word: list[_Letter] = []

    def flush() -> None:
        if word:
            out.append(_apply_case(word, _transliterate_word(word)))
            word.clear()

    for char in unicodedata.normalize("NFC", text):
        letter = _parse_letter(char)
        if letter is not None:
            word.append(letter)
            continue
        # A combining mark that NFC could not recompose still belongs to the letter before
        # it — attaching it keeps the word (and its digraph rules) intact.
        if word and unicodedata.category(char) == "Mn":
            word[-1] = replace(
                word[-1],
                accented=word[-1].accented or char in _ACCENT_MARKS,
                diaeresis=word[-1].diaeresis or char == _DIAERESIS,
            )
            continue
        flush()
        out.append(char)
    flush()
    return "".join(out)


#: Source scripts this module can romanize, ISO 15924 → transform. Greek is the whole of
#: ADR-0010's M1 sliver; Hebrew (M3) needs its own table, not a new approach.
SUPPORTED_SOURCE_SCRIPTS: Final[Mapping[str, Callable[[str], str]]] = {
    "Grek": transliterate_greek,
}

#: Ceiling on a rule-based transliteration's confidence. Not 1.0 on purpose: ELOT 743 is
#: exact on Greek orthography but cannot know that a name is of foreign origin
#: (``Μπενάκη`` → ``Benaki``, but a borrowed spelling may want another rendering).
#: SC-004 accordingly sets a ≥95% coverage bar, not a correctness-by-fiat one.
BASE_CONFIDENCE: Final[float] = 0.9


def transliteration_confidence(latin: str) -> float:
    """Certainty of a produced rendering: :data:`BASE_CONFIDENCE`, scaled by how much of
    the result actually came out as Latin letters (leftover foreign script ⇒ less sure)."""
    letters = [char for char in latin if char.isalpha()]
    if not letters:
        return 0.0
    latin_share = sum(1 for char in letters if script_of(char) == LATIN_SCRIPT) / len(letters)
    return round(BASE_CONFIDENCE * latin_share, 2)


def latin_tag(tag: str) -> str:
    """The BCP-47 key the derived name lands under: ``el`` → ``el-Latn`` (data-model §3)."""
    language, _, region = _parse_tag(tag)
    parts = [language, LATIN_SCRIPT] + ([region] if region else [])
    return "-".join(parts)


def _today() -> date:
    return datetime.now(UTC).date()


def derive_latin_name(
    entry: SourcedValue[str],
    tag: str,
    *,
    observed_at: date | None = None,
) -> SourcedValue[str] | None:
    """Derive the ``*-Latn`` twin of one source-script display name (T057 + T058).

    Returns ``None`` when there is nothing to derive (the tag's script is already Latin, or
    no transform exists for it). Raises :class:`ScriptMismatchError` when the guard fails —
    a mismatch is a flag, never a silent transliteration (T056 / FAIL-001).

    Provenance is **inherited, not invented**: the returned value carries the *same*
    :class:`~commons.models.SourceRef` object as ``entry``, so license, attribution and —
    via :meth:`SourcedValue.stamp`, which re-derives it from that license — ``bundleable``
    are identical to the parent's. ``confidence`` is the transliteration's certainty and
    ``observed_at`` is the derivation date (defaults to today, injectable for reproducible
    tests). The caller's original ``entry`` is untouched; models are frozen.
    """
    expected = expected_scripts(tag)
    transforms = [
        (script, SUPPORTED_SOURCE_SCRIPTS[script])
        for script in sorted(expected)
        if script in SUPPORTED_SOURCE_SCRIPTS
    ]
    if len(transforms) != 1:
        return None

    check = check_script(entry.value, tag)
    if not check.ok:
        raise ScriptMismatchError(check)

    latin = transforms[0][1](entry.value)
    if not latin.strip():
        return None
    return SourcedValue[str].stamp(
        value=latin,
        source=entry.source,
        confidence=transliteration_confidence(latin),
        observed_at=observed_at if observed_at is not None else _today(),
    )


@dataclass(frozen=True, slots=True)
class ScriptFlag:
    """A value whose script contradicts its tag — surfaced for the caller to act on.

    The caller (curate, T059) decides the consequence — lower the confidence, raise a
    :class:`~commons.models.FieldConflict` — but it cannot *miss* the problem, and the
    flagged value is never transliterated.
    """

    tag: str
    value: str
    check: ScriptCheck

    @property
    def reason(self) -> str:
        return self.check.reason


@dataclass(frozen=True, slots=True)
class TransliterationResult:
    """Outcome of :func:`derive_latin_names`."""

    #: The originals **verbatim** plus any derived ``*-Latn`` entries. Never an overwrite.
    names: Mapping[str, SourcedValue[str]]
    #: Tags that were newly added, in insertion order.
    derived: tuple[str, ...]
    #: Script-mismatch flags (FAIL-001) for *every* entry checked, derivable or not.
    flags: tuple[ScriptFlag, ...]


def derive_latin_names(
    names: Mapping[str, SourcedValue[str]],
    *,
    observed_at: date | None = None,
) -> TransliterationResult:
    """Screen every display name, then add the ``*-Latn`` twins (the entry point for T059).

    Guarantees, in order:

    1. Every entry is script-checked; each mismatch becomes a :class:`ScriptFlag`.
    2. A flagged or non-derivable entry produces no derived value.
    3. Originals are returned **unchanged** — same object, same attribution, same stamp —
       and an existing ``*-Latn`` key (one a source supplied itself) is never overwritten.
    """
    result: dict[str, SourcedValue[str]] = dict(names)
    derived: list[str] = []
    flags: list[ScriptFlag] = []

    for tag in sorted(names):
        entry = names[tag]
        check = check_script(entry.value, tag)
        if check.status == "mismatch":
            flags.append(ScriptFlag(tag=tag, value=entry.value, check=check))
            continue
        if not check.ok:
            # "unknown" (no expectation on record) / "empty" (no letters) are not passes:
            # the guard could not confirm the script, so nothing is derived from it.
            continue
        target = latin_tag(tag)
        if target in names or target in result:
            continue
        value = derive_latin_name(entry, tag, observed_at=observed_at)
        if value is None:
            continue
        result[target] = value
        derived.append(target)

    return TransliterationResult(names=result, derived=tuple(derived), flags=tuple(flags))
