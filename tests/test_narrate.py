"""T032 tests — the ``narrate`` node. Tier 1: a fake router, no API key, no network.

Pins the guarantees ``planner/nodes/narrate.py`` exists to make structural, not merely
prompted (module docstring, ``prompts/narration.md`` §3.2/§3.4/§3.5):

* **FR-023 is structural.** No article text ⇒ the router is never called — not merely
  "no story came back". Every no-call assertion below is on a spy's call count.
* **Attribution is copied, never read from the reply.** A forged ``source`` key is
  discarded with the whole reply; a good reply's :class:`~commons.models.Story` carries
  the article's own :class:`~commons.models.SourceRef` **by identity**.
* **Every post-check (§3.4) falls towards no story, never towards a patched one.** Shape,
  length (boundary-tested, never trimmed), verbatim-run, coordinate.
* ``text: null`` is success, not refusal (§2) — the two are asserted apart.
* ``router=None`` is fully deterministic and offline (module docstring).
* A router exception degrades to a refusal, never propagates (FR-012).
* An unspaced-script account is counted by character, not by whitespace token (SC-009) —
  a regression to ``str.split()`` must fail loudly here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pytest
from shapely import Point

from commons.geo import Wgs84Point
from commons.llm import (
    DEFAULT_MAX_TOKENS,
    Completion,
    Effort,
    Message,
    TaskTier,
    ToolSpec,
    Usage,
)
from commons.models import SiteRecordV1, SourcedValue, SourceRef, Story
from planner.nodes.narrate import (
    MAX_VERBATIM_RUN,
    MAX_WORDS,
    MIN_WORDS,
    narrate,
    narrate_article,
)

OBSERVED = date(2026, 8, 1)

ARTICLE_SOURCE = SourceRef(
    kind="wikivoyage",
    id="en:Rhodes Palace",
    license="CC-BY-SA-4.0",
    attribution="Wikivoyage contributors, CC BY-SA 4.0",
)
SITE_SOURCE = SourceRef(
    kind="osm", id="node/1", license="ODbL-1.0", attribution="© OpenStreetMap contributors"
)

ARTICLE_TEXT = (
    "The palace at the head of the street was built in the fourteenth century by the "
    "knights and served as the seat of the order. An explosion in the nineteenth century "
    "destroyed much of the structure, and it was rebuilt in the twentieth century by a "
    "different administration intending it as a residence for a visiting head of state."
)
ARTICLE_TEXT_ZH = (
    "该建筑由骑士团于十四世纪建造用作宫殿并成为总部所在地十九世纪的一场爆炸摧毁了大部分"
    "结构此后由另一政权在二十世纪重建作为来访元首的行馆保存至今仍可参观"
)


def words(n: int, prefix: str = "tok") -> str:
    """``n`` space-separated ASCII tokens — a synthetic account that never overlaps
    ``ARTICLE_TEXT`` verbatim and never carries a coordinate."""
    return " ".join(f"{prefix}{i}" for i in range(n))


GOOD_ACCOUNT = words(60)


def article(
    *,
    text_by_lang: Mapping[str, str] | None = None,
    source: SourceRef = ARTICLE_SOURCE,
    observed_at: date = OBSERVED,
) -> Story:
    """The adapter's story-shaped carrier of one fetched article (narrate_article's input)."""
    return Story(
        text_by_lang=dict(text_by_lang) if text_by_lang is not None else {"en": ARTICLE_TEXT},
        source=source,
        observed_at=observed_at,
    )


def record(
    *,
    names: Mapping[str, str] | None = None,
    stories: tuple[Story, ...] = (),
) -> SiteRecordV1:
    """A minimal valid slice-001 record carrying ``stories`` for the top-level ``narrate``."""

    def stamp(value: object) -> SourcedValue[Any]:
        return SourcedValue[Any].stamp(
            value=value, source=SITE_SOURCE, confidence=0.8, observed_at=OBSERVED
        )

    filled_names = dict(names) if names is not None else {"en": "Test Place"}
    return SiteRecordV1(
        names={tag: stamp(value) for tag, value in filled_names.items()},
        location=SourcedValue[Wgs84Point].stamp(
            value=Point(28.2247, 36.4443), source=SITE_SOURCE, confidence=0.8, observed_at=OBSERVED
        ),
        stories=stories,
    )


@dataclass
class FakeRouter:
    """A local :class:`commons.llm.ModelRouter` — no provider, no key, no network."""

    reply: str = ""
    raises: Exception | None = None
    calls: list[tuple[TaskTier, str]] = field(default_factory=list)

    def complete(
        self,
        tier: TaskTier,
        *,
        system: str,
        messages: Sequence[Message],
        tools: Sequence[ToolSpec] = (),
        max_tokens: int = DEFAULT_MAX_TOKENS,
        cache_prefix: bool = False,
        long_run: bool = False,
        adaptive_thinking: bool = True,
        effort: Effort | None = None,
    ) -> Completion:
        self.calls.append((tier, messages[-1].content))
        if self.raises is not None:
            raise self.raises
        return Completion(text=self.reply, usage=Usage(model="fake"))


def reply(text: object) -> str:
    return json.dumps({"text": text}, ensure_ascii=False)


# ── FR-023: no article text ⇒ the router is never touched ──────────────────────────────


@pytest.mark.parametrize(
    "text_by_lang",
    [{}, {"en": ""}, {"en": "   "}],
    ids=["no-entries", "empty-string", "whitespace-only"],
)
def test_no_article_text_means_no_call_and_no_story(text_by_lang: dict[str, str]) -> None:
    router = FakeRouter(reply=reply(GOOD_ACCOUNT))
    result = narrate_article(
        article(text_by_lang=text_by_lang), names=["Test Place"], router=router
    )
    assert result.story is None
    assert result.refusal is None, "FR-023: not asking is not a refusal"
    assert router.calls == [], "the model must never be called when there is no article to adapt"


# ── attribution: copied by identity, never read from the reply ─────────────────────────


def test_a_good_reply_keeps_the_articles_source_ref_by_identity() -> None:
    art = article()
    router = FakeRouter(reply=reply(GOOD_ACCOUNT))
    result = narrate_article(art, names=["Test Place"], router=router)
    assert result.story is not None
    assert result.story.source is art.source, "attribution is copied, never rebuilt from the reply"
    assert result.story.observed_at == art.observed_at
    assert result.story.text_by_lang == {"en": GOOD_ACCOUNT}
    assert result.refusal is None


def test_a_forged_source_key_in_the_reply_never_reaches_a_story() -> None:
    forged_source = {"kind": "wikipedia", "id": "en:Somewhere Else", "license": "CC0-1.0"}
    router = FakeRouter(reply=json.dumps({"text": GOOD_ACCOUNT, "source": forged_source}))
    result = narrate_article(article(), names=["Test Place"], router=router)
    assert result.story is None, "the reply is discarded whole; a forged credit is unreachable"
    assert result.refusal is not None and "source" in result.refusal


# ── §3.4 shape checks: malformed whole, never salvaged ──────────────────────────────────


@pytest.mark.parametrize(
    ("bad_reply", "expect_substring"),
    [
        pytest.param("not json at all", "not JSON", id="not-json"),
        pytest.param("```json\n" + reply(GOOD_ACCOUNT) + "\n```", "not JSON", id="json-fenced"),
        pytest.param(json.dumps({"text": GOOD_ACCOUNT, "note": "nope"}), "note", id="extra-key"),
        pytest.param(json.dumps({"text": 123}), "int", id="non-str-value"),
    ],
)
def test_a_malformed_reply_is_refused_whole_and_costs_the_place_its_story(
    bad_reply: str, expect_substring: str
) -> None:
    router = FakeRouter(reply=bad_reply)
    result = narrate_article(article(), names=["Test Place"], router=router)
    assert result.story is None
    assert result.refusal is not None and expect_substring in result.refusal
    assert len(router.calls) == 1, "unlike the no-article case, the model was genuinely asked"


def test_text_null_is_a_success_not_a_refusal() -> None:
    router = FakeRouter(reply=reply(None))
    result = narrate_article(article(), names=["Test Place"], router=router)
    assert result.story is None
    assert result.refusal is None, "declining is success (§2); it must not inflate the refusal rate"
    assert len(router.calls) == 1, "the model was genuinely asked and genuinely declined"


# ── §3.5 length: boundary-tested, dropped, never trimmed ────────────────────────────────


def test_min_words_boundary_is_kept() -> None:
    account = words(MIN_WORDS)
    result = narrate_article(
        article(), names=["Test Place"], router=FakeRouter(reply=reply(account))
    )
    assert result.story is not None
    assert result.story.text_by_lang["en"] == account


def test_one_word_below_the_floor_is_dropped() -> None:
    account = words(MIN_WORDS - 1)
    result = narrate_article(
        article(), names=["Test Place"], router=FakeRouter(reply=reply(account))
    )
    assert result.story is None
    assert result.refusal is not None and "word" in result.refusal


def test_max_words_boundary_is_kept() -> None:
    account = words(MAX_WORDS)
    result = narrate_article(
        article(), names=["Test Place"], router=FakeRouter(reply=reply(account))
    )
    assert result.story is not None


def test_one_word_above_the_ceiling_is_dropped() -> None:
    account = words(MAX_WORDS + 1)
    result = narrate_article(
        article(), names=["Test Place"], router=FakeRouter(reply=reply(account))
    )
    assert result.story is None


def test_an_overlong_reply_is_dropped_whole_never_trimmed() -> None:
    account = words(300)
    result = narrate_article(
        article(), names=["Test Place"], router=FakeRouter(reply=reply(account))
    )
    assert result.story is None, "an over-long account is dropped whole, never truncated to fit"


# ── §3.5 adapted-not-copied: the longest shared verbatim run ────────────────────────────


def test_a_verbatim_run_over_the_limit_is_dropped() -> None:
    shared = words(MAX_VERBATIM_RUN + 1, prefix="shared")  # 21 tokens > the 20-token limit
    filler_article = (
        ARTICLE_TEXT
        + " "
        + shared
        + " and more prose that is otherwise unrelated to the shared phrase above."
    )
    account = shared + " " + words(MIN_WORDS - (MAX_VERBATIM_RUN + 1), prefix="own")
    result = narrate_article(
        article(text_by_lang={"en": filler_article}),
        names=["Test Place"],
        router=FakeRouter(reply=reply(account)),
    )
    assert result.story is None
    assert result.refusal is not None
    assert "verbatim" in result.refusal and "shared0" in result.refusal


def test_a_verbatim_run_at_the_limit_is_kept() -> None:
    shared = words(MAX_VERBATIM_RUN, prefix="shared")  # exactly the 20-token limit
    filler_article = (
        ARTICLE_TEXT + " " + shared + " and further unrelated prose padding the article out today."
    )
    account = shared + " " + words(MIN_WORDS - MAX_VERBATIM_RUN, prefix="own")
    result = narrate_article(
        article(text_by_lang={"en": filler_article}),
        names=["Test Place"],
        router=FakeRouter(reply=reply(account)),
    )
    assert result.story is not None, "sharing exactly the limit is adaptation, not copying"


# ── FR-005: no coordinate ever survives into an account ─────────────────────────────────


def test_a_coordinate_pair_is_dropped() -> None:
    account = words(50) + " 36.4443, 28.2247"
    result = narrate_article(
        article(), names=["Test Place"], router=FakeRouter(reply=reply(account))
    )
    assert result.story is None
    assert result.refusal is not None
    assert "coordinate" in result.refusal and "36.4443" in result.refusal


# ── router=None: fully deterministic, offline ────────────────────────────────────────────


def test_router_none_makes_no_call_and_produces_no_story() -> None:
    result = narrate_article(article(), names=["Test Place"], router=None)
    assert result.story is None
    assert result.refusal is None


def test_router_none_over_a_record_set_is_fully_deterministic_and_offline() -> None:
    records = [
        record(stories=(article(),)),
        record(names={"en": "Second Place"}, stories=(article(),)),
    ]
    result = narrate(records, router=None)
    assert result.narrated == 0
    assert result.rejected == ()
    assert all(rec.stories == () for rec in result.records)


# ── FR-012: a router exception degrades to a refusal, never propagates ─────────────────


def test_a_router_exception_degrades_to_a_refusal_not_a_propagated_error() -> None:
    router = FakeRouter(raises=TimeoutError("the narration call timed out"))
    result = narrate_article(article(), names=["Test Place"], router=router)
    assert result.story is None
    assert result.refusal is not None and "TimeoutError" in result.refusal


# ── SC-009: an unspaced-script account is counted by character ─────────────────────────


def test_an_unspaced_script_account_is_counted_by_character_not_whitespace() -> None:
    # 45 CJK characters, no whitespace anywhere: str.split() reads this as one "word" and
    # would wrongly floor it out under MIN_WORDS. The per-character count must accept it,
    # or narration silently stops working for every area written in such a script.
    account = "字" * 45
    result = narrate_article(
        article(text_by_lang={"zh": ARTICLE_TEXT_ZH}),
        names=["Test Place"],
        router=FakeRouter(reply=reply(account)),
    )
    assert result.story is not None, "an unspaced-script account must not be floored by token count"
    assert result.story.text_by_lang["zh"] == account


# ── the ``narrate`` wrapper: replacement, counting, rejection labelling ─────────────────


def test_narrate_replaces_the_article_with_the_adapted_story_and_counts_it() -> None:
    good = record(stories=(article(),))
    router = FakeRouter(reply=reply(GOOD_ACCOUNT))
    result = narrate([good], router=router)
    assert result.narrated == 1
    assert result.rejected == ()
    narrated_story = result.records[0].stories[0]
    assert narrated_story.text_by_lang == {"en": GOOD_ACCOUNT}
    assert narrated_story.source is good.stories[0].source


def test_narrate_records_a_rejection_labeled_with_the_place_and_the_article() -> None:
    bad = record(names={"en": "Parking P3"}, stories=(article(),))
    router = FakeRouter(reply="not json")
    result = narrate([bad], router=router)
    assert result.narrated == 0
    assert len(result.rejected) == 1
    assert "Parking P3" in result.rejected[0]
    assert ARTICLE_SOURCE.id in result.rejected[0]
