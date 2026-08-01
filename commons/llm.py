"""The ``ModelRouter`` seam — the one module allowed to touch a provider SDK.

**ADR-0004** (layered planner over a provider-routable model seam) makes every model
call go through a narrow, capability-oriented seam so provider/model choice is
configuration rather than code. Two rules follow, and both are mechanically enforced:

1. **Seam purity.** No module in ``commons/`` (above this file), ``planner/``, ``api/``
   or ``compiler/`` may import ``anthropic`` / ``openai`` / ``litellm``. The tripwire is
   ``tests/test_llm_seam.py`` (AST scan, not grep). Nothing provider-shaped crosses the
   seam: callers pass :class:`TaskTier` + :class:`Message` and get a :class:`Completion`.
2. **Pinned models.** Constitution Article VII — the app model is pinned to a dated
   snapshot, never a floating alias. :class:`ModelPin` refuses to be constructed with an
   undated id unless the pin records *why* (see ``undated_reason`` below).

Per-task routing (ADR-0004 (c), the biggest cost lever) is the whole point of the tier
enum: ``research`` → Haiku 4.5, ``curate`` → Sonnet 5, ``plan`` → Opus 5.

**Planner-spike constraint 2** (``spike/planner_spike/FINDINGS.md``, ratified by Ben
2026-07-25): adaptive thinking and ``output_config.effort`` are 4.6+ features and Haiku
4.5 returns **HTTP 400** on either. The research tier stays on Haiku 4.5 with those
parameters *gated off* — :data:`SUPPORTS_ADAPTIVE_EFFORT` decides, and
:func:`build_request` **strips** them for a non-supporting tier rather than raising, so a
caller may always ask for its preferred capabilities and let the seam degrade.

Layering note: PydanticAI is the *orchestration* layer that sits **above** this seam (in
``planner/``, a later task) — it is deliberately not imported here, because a
pydantic-ai type in the seam's signatures would be exactly the kind of leak rule 1
forbids. LiteLLM is the routing implementation *inside* the seam. The ADR-0004 M1
Anthropic-native adapter (direct ``anthropic`` SDK, for server-side compaction /
context-editing / task budgets) is **deferred**: the ``anthropic`` dependency is not in
``pyproject.toml`` yet, so ``long_run`` currently degrades to a no-op. Prompt caching and
per-task routing — the two levers the spike actually measured — work over LiteLLM today.

Credentials are never read here: LiteLLM resolves ``ANTHROPIC_API_KEY`` from the process
environment at call time. This module never reads ``.env*`` or ``secrets/``.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol, runtime_checkable

_log = logging.getLogger(__name__)

# Non-streaming default; large enough not to truncate a research/curate turn mid-thought.
DEFAULT_MAX_TOKENS = 16_000
DEFAULT_TIMEOUT_S = 120.0

Effort = Literal["low", "medium", "high", "xhigh"]
Role = Literal["user", "assistant"]

# A published dated snapshot ends in `-YYYYMMDD` (e.g. `claude-haiku-4-5-20251001`).
_DATED_SNAPSHOT = re.compile(r"-\d{8}$")


class TaskTier(StrEnum):
    """What a call is *for*. Drives model choice — never named a model at a call site."""

    RESEARCH = "research"  # high volume, structured extraction  -> cheap model
    CURATE = "curate"  # merge ranking + prose               -> mid model
    PLAN = "plan"  # hard reasoning                      -> strong model


@dataclass(frozen=True, slots=True)
class ModelPin:
    """One pinned model, with the provenance Article VII asks for.

    ``model_id`` is the exact string the provider accepts. Constructing a pin whose id is
    not a dated snapshot requires an explicit ``undated_reason`` — the mechanical hook
    that stops a floating alias sliding in unnoticed (Constitution Article VI: rules
    become checks, not vigilance).
    """

    provider: str
    model_id: str
    pinned_on: str  # ISO date the pin was taken / last re-verified
    source: str  # where the id was verified — never from model memory
    undated_reason: str | None = None

    def __post_init__(self) -> None:
        if self.is_dated_snapshot and self.undated_reason is not None:
            raise ValueError(f"{self.model_id!r} is a dated snapshot; drop the undated_reason.")
        if not self.is_dated_snapshot and not self.undated_reason:
            raise ValueError(
                f"{self.model_id!r} is not a dated snapshot (Constitution Article VII). "
                "Pin a dated snapshot, or record undated_reason explaining why the "
                "provider publishes none."
            )

    @property
    def is_dated_snapshot(self) -> bool:
        return _DATED_SNAPSHOT.search(self.model_id) is not None

    @property
    def ref(self) -> str:
        """Provider-qualified id as LiteLLM routes it, e.g. ``anthropic/claude-...``."""
        return f"{self.provider}/{self.model_id}"


# --- The routing table -----------------------------------------------------
#
# Model ids verified against the `claude-api` skill's model catalog (2026-08-01),
# never written from model memory. Anthropic publishes a dated snapshot id for
# Haiku 4.5 only; for Sonnet 5 / Opus 5 the short id *is* the sole published
# identifier and appending a date returns 404 — so those pins carry the reason the
# Article VII dated form is unavailable rather than inventing one.
ROUTING_TABLE: Mapping[TaskTier, ModelPin] = {
    TaskTier.RESEARCH: ModelPin(
        provider="anthropic",
        model_id="claude-haiku-4-5-20251001",
        pinned_on="2026-08-01",
        source="claude-api skill model catalog (Haiku 4.5 full id)",
    ),
    TaskTier.CURATE: ModelPin(
        provider="anthropic",
        model_id="claude-sonnet-5",
        pinned_on="2026-08-01",
        source="claude-api skill model catalog (Sonnet 5)",
        undated_reason=(
            "Anthropic publishes no dated snapshot id for Sonnet 5; the short id is the "
            "complete published identifier and a date suffix 404s. Re-pin to the dated "
            "form as soon as one is published."
        ),
    ),
    TaskTier.PLAN: ModelPin(
        provider="anthropic",
        model_id="claude-opus-5",
        pinned_on="2026-08-01",
        source="claude-api skill model catalog (Opus 5)",
        undated_reason=(
            "Anthropic publishes no dated snapshot id for Opus 5; the short id is the "
            "complete published identifier and a date suffix 404s. Re-pin to the dated "
            "form as soon as one is published."
        ),
    ),
}

# Planner-spike constraint 2: adaptive thinking + `output_config.effort` are 4.6+ only.
# Haiku 4.5 400s on both, so it is absent here and `build_request` strips them for it.
# Keyed by `model_id` (not tier) so re-pointing a tier at another model carries the
# capability answer with it.
SUPPORTS_ADAPTIVE_EFFORT: frozenset[str] = frozenset(
    {
        ROUTING_TABLE[TaskTier.CURATE].model_id,
        ROUTING_TABLE[TaskTier.PLAN].model_id,
    }
)

# Default depth per tier, applied only where the tier supports effort at all.
EFFORT_BY_TIER: Mapping[TaskTier, Effort] = {
    TaskTier.RESEARCH: "low",
    TaskTier.CURATE: "medium",
    TaskTier.PLAN: "high",
}


# --- Provider-neutral DTOs (the only vocabulary that crosses the seam) -----


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    input_schema: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    args: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Usage:
    """Token counters, incl. the cache fields the ADR-0004 caching eval asserts on."""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    model: str = ""


@dataclass(frozen=True, slots=True)
class Completion:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: Usage = field(default_factory=Usage)


@runtime_checkable
class ModelRouter(Protocol):
    """The seam. Implementations pick the provider; callers never name a model."""

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
    ) -> Completion: ...


# --- Routing + capability gating ------------------------------------------


def resolve_model(tier: TaskTier) -> ModelPin:
    """The pinned model for a task tier."""
    return ROUTING_TABLE[tier]


def supports_adaptive_effort(tier: TaskTier) -> bool:
    """Whether this tier's pinned model accepts adaptive thinking / effort."""
    return ROUTING_TABLE[tier].model_id in SUPPORTS_ADAPTIVE_EFFORT


def build_request(
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
    timeout: float = DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Assemble the provider call for ``tier``. Pure — no I/O, no credentials.

    Capability requests are *aspirational*: ``adaptive_thinking`` / ``effort`` are applied
    only when the tier's pinned model supports them and **silently stripped otherwise**
    (planner-spike constraint 2 — Haiku 4.5 returns a 400 on either). Stripping rather
    than raising is deliberate: the orchestration layer asks for what it wants once, and
    the seam degrades per model.
    """
    pin = resolve_model(tier)
    request: dict[str, Any] = {
        "model": pin.ref,
        "messages": [_system_message(system, cache_prefix=cache_prefix)]
        + [{"role": m.role, "content": m.content} for m in messages],
        "max_tokens": max_tokens,
        "timeout": timeout,
    }
    if tools:
        request["tools"] = [_tool_param(t) for t in tools]

    wanted_effort: Effort = effort if effort is not None else EFFORT_BY_TIER[tier]
    if supports_adaptive_effort(tier):
        if adaptive_thinking:
            # 4.6+ shape. `budget_tokens` is removed on these models and 400s.
            request["thinking"] = {"type": "adaptive"}
        # LiteLLM maps `reasoning_effort` onto Anthropic's `output_config.effort`.
        request["reasoning_effort"] = wanted_effort
    elif adaptive_thinking or effort is not None:
        _log.debug(
            "tier=%s model=%s does not support adaptive thinking / effort; "
            "stripped thinking=%s effort=%s (planner-spike constraint 2)",
            tier.value,
            pin.model_id,
            adaptive_thinking,
            wanted_effort,
        )

    if long_run:
        # ADR-0004 (b): server-side compaction is an Anthropic-native adapter feature,
        # deferred with the `anthropic` SDK dependency. Degrade, never fail.
        _log.debug("long_run requested but unsupported by the LiteLLM adapter; ignored")

    return request


def _system_message(system: str, *, cache_prefix: bool) -> dict[str, Any]:
    """System turn, with a cache breakpoint on the stable prefix when asked.

    Caching has a **minimum-prefix precondition** (planner-spike constraint 1): the
    breakpoint silently no-ops below the model's minimum cacheable prefix, so a small
    prefix reads as "caching broken" when nothing was cacheable. Callers keep the system
    prefix realistically sized; the caching eval asserts against such a prefix.
    """
    if not cache_prefix:
        return {"role": "system", "content": system}
    return {
        "role": "system",
        "content": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
    }


def _tool_param(tool: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": dict(tool.input_schema),
        },
    }


# --- The LiteLLM-backed router --------------------------------------------

CompletionFn = Callable[..., Any]


def _litellm_completion(**kwargs: Any) -> Any:
    """The provider boundary — the single call that leaves the process.

    LiteLLM is imported lazily so that importing the seam (which the whole data spine
    does, transitively) costs nothing, and so unit tests can drive :class:`LiteLLMRouter`
    with a stub boundary and no provider package, API key, or network.
    """
    import litellm

    return litellm.completion(**kwargs)


class LiteLLMRouter:
    """ADR-0004's routing implementation: one LiteLLM call per tier-routed request."""

    def __init__(self, completion: CompletionFn | None = None) -> None:
        self._completion: CompletionFn = completion or _litellm_completion

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
        request = build_request(
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
        return _parse_response(self._completion(**request), model=resolve_model(tier).model_id)


def _parse_response(raw: Any, *, model: str) -> Completion:
    """Flatten a provider response into the seam's DTOs. No provider type escapes."""
    choices = getattr(raw, "choices", None) or []
    message = getattr(choices[0], "message", None) if choices else None
    text = (getattr(message, "content", None) or "") if message is not None else ""

    calls: list[ToolCall] = []
    for call in getattr(message, "tool_calls", None) or []:
        fn = getattr(call, "function", None)
        calls.append(
            ToolCall(
                id=str(getattr(call, "id", "")),
                name=str(getattr(fn, "name", "")),
                args=_decode_args(getattr(fn, "arguments", None)),
            )
        )

    return Completion(text=text, tool_calls=tuple(calls), usage=_parse_usage(raw, model=model))


def _parse_usage(raw: Any, *, model: str) -> Usage:
    usage = getattr(raw, "usage", None)
    details = getattr(usage, "prompt_tokens_details", None)
    return Usage(
        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        cache_read_input_tokens=int(getattr(details, "cached_tokens", 0) or 0),
        cache_creation_input_tokens=int(getattr(details, "cache_creation_tokens", 0) or 0),
        model=model,
    )


def _decode_args(arguments: Any) -> Mapping[str, Any]:
    """Tool arguments arrive as a JSON string; never raw-string-match them."""
    if isinstance(arguments, Mapping):
        return dict(arguments)
    if isinstance(arguments, str) and arguments:
        try:
            decoded: Any = json.loads(arguments)
        except json.JSONDecodeError:
            _log.warning("undecodable tool-call arguments; returning empty args")
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}
