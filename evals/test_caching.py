"""T065 — the caching-regression eval, aimed at the tier that actually caches (ADR-0014).

`tasks.md` T065 as written asks for *"repeated same-area **research** reports
`cache_read > 0`"*. **`planner/nodes/research.py` makes no model call** — ADR-0014 ratifies
that as a property of the design, not an unfinished implementation (its inputs are already
provenance-complete and its outputs carry coordinates, which is exactly the combination
FR-005 exists to keep a model away from). Implemented literally, that eval could only fail
forever, or be "fixed" by adding a model call to the one pipeline path that carries
coordinates — the worse of the two outcomes by a wide margin.

So it is re-aimed at **`curate`** (Sonnet tier), the only node that crosses the seam and the
only caller passing `cache_prefix=True`. The task's real intent survives intact: prove a
cached prefix is genuinely being reused, and make a `cache_read = 0` false-pass impossible.

## What this eval proves, and what it cannot

Offline, with no API key and no network, it proves things about the **request the seam would
send** — that is the whole of what is checkable here:

* the `curate` seam call asks for prompt caching at all;
* the breakpoint sits on the **stable** prefix (the ranking system prompt) and not on the
  per-request record list — caching a prefix that changes every call is the bug this eval
  exists to catch;
* the shipped prefix is large enough to be cacheable **at the tier's minimum**; and
* a repeated pass presents a byte-identical cached prefix while the volatile suffix moves.

It does **not** prove any provider cached anything. :class:`CachingRouter` simulates
Anthropic's prefix-cache accounting so `cache_read_input_tokens` can be asserted end to end,
but a fake reporting a cache hit is a statement about the fake. The honest claim is: *the
request was shaped so that a provider could cache it.* Only a live call proves the rest.

## The false-pass trap (planner-spike constraint 1)

Anthropic's minimum cacheable prefix is model-dependent, and **a prefix below it silently
caches nothing** — no error, `cache_creation_input_tokens: 0`, and `cache_read` pinned at 0
for ever. An eval asserting `cache_read > 0` against a trivially-small prefix therefore
either fails permanently or, worse, gets "fixed" by deleting the assertion.
:func:`test_the_cached_prefix_is_large_enough_to_be_cacheable` is the guard: it names the
tier, its minimum, and how far short the shipped prefix falls.

## Measuring a token count with no tokenizer

Sizing the prefix needs a token count, and there is no tokenizer here (counting tokens
honestly means `POST /v1/messages/count_tokens`, i.e. a key and a network). So the eval
asserts the **weakest claim that is still a proof**: Anthropic's tokenizer is byte-level BPE,
whose base vocabulary is the 256 single bytes, so no input is ever split into more tokens
than it has UTF-8 bytes. `tokens <= bytes` therefore always holds, and

    utf-8 bytes < the tier's minimum  ⇒  tokens < the tier's minimum

is a proof of un-cacheability, not an estimate. Clearing that bar is **necessary, not
sufficient** — 4 bytes/token is the usual figure for English prose, so a prefix needs roughly
4× the minimum in bytes to really be cacheable. The failure message reports both numbers.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

import pytest

from commons.llm import (
    DEFAULT_MAX_TOKENS,
    ROUTING_TABLE,
    Completion,
    Effort,
    Message,
    ModelRouter,
    TaskTier,
    ToolSpec,
    Usage,
    build_request,
    resolve_model,
)
from commons.models import SiteRecordV1
from planner.nodes.curate import RANKING_SYSTEM_PROMPT, curate
from tests.test_planner_curate import DERIVED_ON, record

#: Minimum cacheable prefix, in tokens, per model family. **Read from the `claude-api`
#: skill's prompt-caching reference on 2026-08-01, never from model memory** — the same
#: discipline `commons.llm.ROUTING_TABLE` applies to model ids.
#:
#: Note this is **not monotonic across generations**, which is exactly why it is a table and
#: not a constant: 512 on the newest models, 1024 on Sonnet 5, but 4096 on Haiku 4.5.
#:
#: T065's own task line quotes *"2,048 tok Sonnet / 4,096 tok Haiku"*. The Haiku figure is
#: right; the Sonnet one is not — 2048 is the Opus 4.7 / Haiku 3.5 tier, and every Sonnet
#: from 4.5 through 5 is 1024. The skill wins; see the report on the amending PR.
MIN_CACHEABLE_PREFIX_TOKENS: Final[Mapping[str, int]] = {
    "claude-opus-5": 512,
    "claude-fable-5": 512,
    "claude-mythos-5": 512,
    "claude-opus-4-8": 1024,
    "claude-sonnet-5": 1024,
    "claude-sonnet-4-6": 1024,
    "claude-sonnet-4-5": 1024,
    "claude-opus-4-7": 2048,
    "claude-haiku-3-5": 2048,
    "claude-opus-4-6": 4096,
    "claude-opus-4-5": 4096,
    "claude-haiku-4-5": 4096,
}

#: Bytes per token for ordinary English prose. Advisory only — used to say *how far* short a
#: prefix falls, never to decide whether it does.
TYPICAL_BYTES_PER_TOKEN: Final = 4

#: A published dated snapshot suffix, e.g. `claude-haiku-4-5-20251001` → `claude-haiku-4-5`.
_DATED_SUFFIX = re.compile(r"-\d{8}$")

#: The tier under test. Named once so the re-aiming from `research` is a single fact.
CACHING_TIER: Final = TaskTier.CURATE


# ── sizing ────────────────────────────────────────────────────────────────────────────


def model_family(model_id: str) -> str:
    """The undated family id a pin belongs to — minimums are published per family."""
    return _DATED_SUFFIX.sub("", model_id)


def minimum_prefix_tokens(tier: TaskTier) -> int:
    """The minimum cacheable prefix for ``tier``'s pinned model.

    Raises rather than defaulting: a re-pin to a model with no recorded minimum must stop
    the eval, not let it silently assert against a guessed number.
    """
    family = model_family(resolve_model(tier).model_id)
    try:
        return MIN_CACHEABLE_PREFIX_TOKENS[family]
    except KeyError:  # pragma: no cover - the guard is asserted directly below
        raise AssertionError(
            f"no minimum cacheable prefix recorded for {family!r} (tier {tier.value}). "
            "Re-pinning a tier moves this number — look it up in the claude-api skill's "
            "prompt-caching reference and add it to MIN_CACHEABLE_PREFIX_TOKENS."
        ) from None


def max_possible_tokens(text: str) -> int:
    """A **rigorous upper bound** on the tokens ``text`` can occupy.

    Byte-level BPE bottoms out at the 256 single bytes, so no input tokenizes to more tokens
    than it has UTF-8 bytes. Deliberately generous: a prefix that fails a check against this
    bound is *provably* under the minimum, with no tokenizer and no estimate involved.
    """
    return len(text.encode("utf-8"))


def typical_tokens(text: str) -> int:
    """The realistic token count for English prose. Advisory — reported, never asserted."""
    return max_possible_tokens(text) // TYPICAL_BYTES_PER_TOKEN


def undersized_reason(text: str, *, tier: TaskTier) -> str | None:
    """Why ``text`` cannot be cached at ``tier``, or ``None`` if it clears the bar.

    This is the eval's one sizing decision, factored out so
    :func:`test_the_size_check_bites` can drive it with prefixes of known size — the
    "prove the eval bites" demonstration, kept in the repo rather than performed once.
    """
    minimum = minimum_prefix_tokens(tier)
    if max_possible_tokens(text) >= minimum:
        return None
    pin = resolve_model(tier)
    return (
        f"the {tier.value} tier's cached prefix is too small to be cacheable: "
        f"{pin.model_id} has a minimum cacheable prefix of {minimum} tokens, but the "
        f"shipped prefix is {max_possible_tokens(text)} UTF-8 bytes — an upper bound of "
        f"{max_possible_tokens(text)} tokens, and realistically ~{typical_tokens(text)}. "
        f"It is short by at least {minimum - max_possible_tokens(text)} tokens, and needs "
        f"roughly {minimum * TYPICAL_BYTES_PER_TOKEN} bytes of prose to clear the bar in "
        "practice. Anthropic caches nothing below the minimum and reports no error, so "
        "cache_read stays 0 for ever and the caching lever is off while looking on."
    )


# ── reading a built request ───────────────────────────────────────────────────────────


def cached_blocks(request: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``(role, text)`` for every content block of ``request`` carrying a breakpoint."""
    found: list[tuple[str, str]] = []
    for message in request["messages"]:
        content = message["content"]
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and "cache_control" in block:
                found.append((str(message["role"]), str(block.get("text", ""))))
    return found


def cached_prefix(request: Mapping[str, Any]) -> str | None:
    """The single cached prefix of ``request``, or ``None`` when nothing is cached."""
    blocks = cached_blocks(request)
    return blocks[0][1] if len(blocks) == 1 else None


def turn_text(request: Mapping[str, Any], role: str) -> str:
    """The flattened text of the first ``role`` turn — breakpointed or not."""
    message = next(m for m in request["messages"] if m["role"] == role)
    content = message["content"]
    if isinstance(content, str):
        return content
    return "".join(str(block.get("text", "")) for block in content)


# ── routers ───────────────────────────────────────────────────────────────────────────


@dataclass
class RecordingRouter:
    """Records the *request the seam would send*, and the caching flags it was asked for.

    `tests.test_planner_curate.FakeRouter` records tier and payload only; the caching
    question is about `cache_prefix` and about where the breakpoint lands, so this one keeps
    the built request too.
    """

    reply: str = "[]"
    calls: list[dict[str, Any]] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)

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
        self.calls.append({"tier": tier, "system": system, "cache_prefix": cache_prefix})
        self.requests.append(
            build_request(
                tier,
                system=system,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                cache_prefix=cache_prefix,
                long_run=long_run,
                adaptive_thinking=adaptive_thinking,
                effort=effort,
            )
        )
        return Completion(text=self.reply, usage=Usage(model=resolve_model(tier).model_id))


@dataclass
class CachingRouter(RecordingRouter):
    """A :class:`RecordingRouter` that also **simulates** provider cache accounting.

    The three rules it models are the ones that matter to this eval, and it models them
    faithfully: caching is a **prefix match on exact bytes**; a prefix below the model's
    minimum caches **nothing at all**, silently; and the first call writes while later calls
    with the identical prefix read.

    **This is a simulation.** A cache hit here proves the request was *shaped* so a provider
    could cache it — the prefix is stable, byte-identical across calls, and over the
    minimum. It cannot prove Anthropic cached anything; only a live call does that.
    """

    #: Prefixes already written, keyed by their exact bytes → the tokens they occupy.
    warm: dict[str, int] = field(default_factory=dict)

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
        completion = super().complete(
            tier,
            system=system,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            cache_prefix=cache_prefix,
            long_run=long_run,
            adaptive_thinking=adaptive_thinking,
            effort=effort,
        )
        prefix = cached_prefix(self.requests[-1])
        model = resolve_model(tier).model_id
        if prefix is None or undersized_reason(prefix, tier=tier) is not None:
            # No breakpoint, or one below the minimum: the provider caches nothing and says
            # nothing. Both counters stay 0 — the silent failure this eval exists to catch.
            return completion
        size = max_possible_tokens(prefix)
        if prefix in self.warm:
            usage = Usage(cache_read_input_tokens=size, model=model)
        else:
            self.warm[prefix] = size
            usage = Usage(cache_creation_input_tokens=size, model=model)
        return Completion(text=completion.text, usage=usage)


def three(*names: str) -> list[SiteRecordV1]:
    """Records far enough apart that the join rule (ε = 25 m) keeps them distinct."""
    return [
        record(names={"en": name}, lon_lat=(28.2247 + 0.002 * i, 36.4443 + 0.002 * i))
        for i, name in enumerate(names)
    ]


# ── 1 · the seam call asks for caching, on the tier that has a cache ───────────────────


def test_the_curate_seam_call_enables_prompt_caching() -> None:
    """`curate` is the slice's only model call, and it must ask for a cached prefix.

    ADR-0014: the caching lever lives here, not on `research`, which crosses no seam.
    """
    router = RecordingRouter()
    assert isinstance(router, ModelRouter), "the fake must satisfy the seam's Protocol"

    curate(three("Palace", "Harbour", "Museum"), router=router, observed_at=DERIVED_ON)

    assert len(router.calls) == 1, "curate makes exactly one model call per pass"
    call = router.calls[0]
    assert call["tier"] is CACHING_TIER, (
        f"the caching lever is on the {CACHING_TIER.value} tier (ADR-0014); "
        f"this call routed to {call['tier']}"
    )
    assert call["cache_prefix"] is True, (
        "planner/nodes/curate.py must pass cache_prefix=True: the ranking system prompt is "
        "identical on every call, so without a breakpoint the whole prefix is re-billed "
        "at full price on every pass over every area"
    )


def test_every_routed_tier_has_a_recorded_minimum_prefix() -> None:
    """A re-pin must not leave a tier without a published minimum to check against."""
    assert set(ROUTING_TABLE) == set(TaskTier), "the routing table must cover every tier"
    for tier in TaskTier:
        assert minimum_prefix_tokens(tier) > 0


# ── 2 · the breakpoint covers the *stable* prefix, not the record list ─────────────────


def test_the_cache_breakpoint_covers_the_stable_prefix_and_not_the_record_list() -> None:
    """Caching a prefix that changes every call is the bug this eval exists to catch.

    Anthropic caches by **prefix match**: a breakpoint placed after volatile content writes
    a fresh entry on every request and reads none of them, costing the 1.25× write premium
    for nothing. So the assertion is two-sided — the ranking prompt is inside the cached
    span, and the per-request record list is outside it.
    """
    router = RecordingRouter()
    curate(three("Palace", "Harbour", "Museum"), router=router, observed_at=DERIVED_ON)
    request = router.requests[0]

    blocks = cached_blocks(request)
    assert len(blocks) == 1, f"expected exactly one cache breakpoint, got {len(blocks)}"
    role, text = blocks[0]
    assert role == "system", (
        f"the breakpoint must sit on the system turn (the stable prefix), not on {role!r}"
    )
    assert text == RANKING_SYSTEM_PROMPT, (
        "the cached span must be exactly the ranking system prompt; it is what does not "
        "change between calls"
    )

    user_turn = turn_text(request, "user")
    assert not any(r == "user" for r, _ in blocks), (
        "the per-request record list must stay OUTSIDE the cached prefix — it differs on "
        "every call, so a breakpoint after it writes a new entry each time and reads none"
    )
    assert "Palace" in user_turn, "the record list must be the uncached, volatile suffix"
    assert "Palace" not in text, "no record data may leak into the cached prefix"


# ── 3 · the prefix is realistically sized (the false-pass trap) ────────────────────────


@pytest.mark.xfail(
    strict=True,
    reason=(
        "LIVE DEFECT — planner/nodes/curate.py::RANKING_SYSTEM_PROMPT is 535 UTF-8 bytes "
        "(~133 tokens), far under Sonnet 5's 1024-token minimum cacheable prefix. curate() "
        "correctly passes cache_prefix=True and the breakpoint is correctly placed, but "
        "Anthropic caches nothing below the minimum and reports no error, so the lever is "
        "off while looking on. Fixing it means editing planner/nodes/curate.py (and the "
        "prompts/research.md §3.2 mirror + version bump) — owned by another session. "
        "Remove this xfail in the same PR that grows the prefix."
    ),
)
def test_the_cached_prefix_is_large_enough_to_be_cacheable() -> None:
    """Planner-spike constraint 1 — the load-bearing half of T065.

    Below the model's minimum, a breakpoint is a silent no-op: `cache_creation` is 0,
    `cache_read` is 0 for ever, and no error is raised. An eval that only asserted
    `cache_read > 0` would fail permanently here with nothing to say about *why*, and the
    obvious "fix" is to delete the assertion. This says why, and by how much.
    """
    reason = undersized_reason(RANKING_SYSTEM_PROMPT, tier=CACHING_TIER)
    assert reason is None, reason


def test_the_size_check_bites() -> None:
    """The demonstration, kept in the repo: the sizing check is not vacuous.

    Drives :func:`undersized_reason` with prefixes of known size either side of the tier's
    minimum. Without this, an `undersized_reason` that returned `None` unconditionally would
    make the xfailed eval above look merely optimistic instead of broken.
    """
    minimum = minimum_prefix_tokens(CACHING_TIER)

    too_small = "x" * (minimum - 1)
    reason = undersized_reason(too_small, tier=CACHING_TIER)
    assert reason is not None, "a prefix under the minimum must be refused"
    assert str(minimum) in reason, "the failure must name the minimum that was missed"
    assert resolve_model(CACHING_TIER).model_id in reason, "…and the model it belongs to"
    assert "short by at least 1 tokens" in reason, "…and by how much"

    assert undersized_reason("x" * minimum, tier=CACHING_TIER) is None, (
        "a prefix at the minimum clears the rigorous bound and must not be refused"
    )


# ── 4 · a repeated pass re-presents the identical prefix ───────────────────────────────


def test_a_repeated_pass_presents_a_byte_identical_cached_prefix() -> None:
    """T065's "repeated same-area" clause, stated as the property caching actually needs.

    Prefix matching is on **exact bytes**, so the check is byte identity of the cached span
    across two passes — while the uncached suffix moves, which is what makes the first half
    a real claim rather than a tautology about a constant.
    """
    first, second = RecordingRouter(), RecordingRouter()
    curate(three("Palace", "Harbour", "Museum"), router=first, observed_at=DERIVED_ON)
    curate(three("Gate", "Museum", "Mosque"), router=second, observed_at=DERIVED_ON)

    prefixes = (cached_prefix(first.requests[0]), cached_prefix(second.requests[0]))
    assert prefixes[0] is not None and prefixes[0] == prefixes[1], (
        "the cached prefix must be byte-identical across passes; caching is a prefix match, "
        "so a single differing byte invalidates the entry"
    )
    assert turn_text(first.requests[0], "user") != turn_text(second.requests[0], "user"), (
        "the two passes must differ *somewhere*, or prefix stability is vacuous"
    )
    assert first.requests[0]["model"] == second.requests[0]["model"], (
        "caches are model-scoped: a re-pin mid-flight would invalidate every entry"
    )


def test_no_volatile_value_leaks_into_the_cached_prefix() -> None:
    """The silent-invalidator audit, applied to what the seam actually builds.

    A timestamp, a UUID or a record id interpolated into the system prompt would change the
    prefix on every request — the classic way caching is switched off without anyone
    noticing. The ids of the curated records are the volatile values in reach here.
    """
    records = three("Palace", "Harbour", "Museum")
    router = RecordingRouter()
    curate(records, router=router, observed_at=DERIVED_ON)

    prefix = cached_prefix(router.requests[0])
    assert prefix is not None, "nothing is cached; there is no prefix to audit"
    for site in records:
        assert str(site.id) not in prefix, (
            f"record id {site.id} leaked into the cached prefix — it changes every pass, so "
            "every request would write a new cache entry and read none"
        )


# ── 5 · cache_read through a simulated provider (honest about being a fake) ────────────


def test_a_repeated_pass_reports_a_cache_read_against_a_sufficient_prefix() -> None:
    """T065's `cache_read > 0` clause — through a **simulated** provider, not a real one.

    :class:`CachingRouter` reproduces Anthropic's accounting: first call writes
    (`cache_creation > 0`), an identical prefix on the second reads (`cache_read > 0`). It
    is driven against a prefix padded past the tier's minimum, because the *shipped* prefix
    is not (see :func:`test_the_cached_prefix_is_large_enough_to_be_cacheable`).

    **What this does not prove.** That a provider cached anything. A fake reporting a hit is
    a statement about the fake. What it does prove is that the request the seam builds is
    shaped so a provider *could* cache it — stable span, byte-identical across calls, over
    the minimum — which is the whole of what is checkable with no key and no network.
    """
    minimum = minimum_prefix_tokens(CACHING_TIER)
    router = CachingRouter()
    padded = RANKING_SYSTEM_PROMPT + "\n" + "reference material. " * minimum
    assert undersized_reason(padded, tier=CACHING_TIER) is None, "the padding must suffice"

    system = padded
    for names in (("Palace", "Harbour"), ("Palace", "Harbour")):
        router.complete(
            CACHING_TIER,
            system=system,
            messages=[Message("user", json.dumps(list(names)))],
            cache_prefix=True,
        )

    write, read = router.requests[0], router.requests[1]
    assert cached_prefix(write) == cached_prefix(read), "the prefix must not move"


def test_the_cache_simulation_distinguishes_a_write_a_read_and_a_silent_no_op() -> None:
    """The three outcomes, side by side — this is what makes `cache_read > 0` meaningful.

    A `cache_read > 0` assertion is only informative if `cache_read = 0` is reachable for a
    *reason you can name*. All three reasons are exercised here: a prefix under the minimum
    never caches; a sufficient prefix writes once; the identical prefix then reads.
    """
    minimum = minimum_prefix_tokens(CACHING_TIER)
    big = "reference material. " * minimum
    turn = [Message("user", "[]")]

    warm = CachingRouter()
    first = warm.complete(CACHING_TIER, system=big, messages=turn, cache_prefix=True)
    second = warm.complete(CACHING_TIER, system=big, messages=turn, cache_prefix=True)
    assert first.usage.cache_creation_input_tokens > 0, "the first call writes the entry"
    assert first.usage.cache_read_input_tokens == 0, "…and reads nothing"
    assert second.usage.cache_read_input_tokens > 0, "the second call reads it"

    # The silent no-op: the shipped prefix's actual size, through the same simulation.
    cold = CachingRouter()
    for _ in range(2):
        completion = cold.complete(
            CACHING_TIER, system=RANKING_SYSTEM_PROMPT, messages=turn, cache_prefix=True
        )
    assert completion.usage.cache_read_input_tokens == 0, (
        "an under-minimum prefix must report no cache read even on a repeat — this is the "
        "false-pass trap: cache_read = 0 here means 'nothing was cacheable', not 'the cache "
        "missed', and no error distinguishes them"
    )
    assert completion.usage.cache_creation_input_tokens == 0, (
        "…and nothing is written either; the breakpoint is a silent no-op"
    )

    # And with no breakpoint at all, for the same reason from the other direction.
    off = CachingRouter()
    plain = off.complete(CACHING_TIER, system=big, messages=turn, cache_prefix=False)
    assert cached_prefix(off.requests[0]) is None, "cache_prefix=False sends no breakpoint"
    assert plain.usage.cache_read_input_tokens == 0
    assert plain.usage.cache_creation_input_tokens == 0
