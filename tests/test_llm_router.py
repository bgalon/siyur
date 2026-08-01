"""Routing-table + capability-gate unit tests (T021).

Fully offline: no API key, no network. The provider boundary
(:func:`commons.llm._litellm_completion`) is replaced by a recording stub, so these
assert on the *request the seam would send* — which is precisely what the two
planner-spike constraints are about.

Regression eval (ADR-0004 Confirmation (a)): "the seam must not send adaptive/effort to
a rejecting model" — :func:`test_research_tier_strips_adaptive_and_effort` and
:func:`test_router_never_sends_adaptive_or_effort_to_research` are that guardrail.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import pytest

from commons.llm import (
    DEFAULT_MAX_TOKENS,
    EFFORT_BY_TIER,
    ROUTING_TABLE,
    SUPPORTS_ADAPTIVE_EFFORT,
    Completion,
    LiteLLMRouter,
    Message,
    ModelPin,
    ModelRouter,
    TaskTier,
    ToolSpec,
    build_request,
    resolve_model,
    supports_adaptive_effort,
)

# The pinned ids this slice ships. Written out longhand rather than derived from the
# table, so a silent re-pin fails here and has to be a deliberate, reviewed edit.
EXPECTED_MODEL_ID = {
    TaskTier.RESEARCH: "claude-haiku-4-5-20251001",
    TaskTier.CURATE: "claude-sonnet-5",
    TaskTier.PLAN: "claude-opus-5",
}


# --- Recording stub for the provider boundary ------------------------------


@dataclass
class _Function:
    name: str
    arguments: str


@dataclass
class _Call:
    id: str
    function: _Function


@dataclass
class _Msg:
    content: str | None = None
    tool_calls: list[_Call] = field(default_factory=list)


@dataclass
class _Choice:
    message: _Msg


@dataclass
class _Details:
    cached_tokens: int = 0
    cache_creation_tokens: int = 0


@dataclass
class _Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    prompt_tokens_details: _Details = field(default_factory=_Details)


@dataclass
class _Response:
    choices: list[_Choice]
    usage: _Usage = field(default_factory=_Usage)


class _Recorder:
    """Stands in for ``litellm.completion``; records kwargs, returns a canned reply."""

    def __init__(self, response: _Response | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response or _Response(choices=[_Choice(_Msg(content="ok"))])

    def __call__(self, **kwargs: Any) -> _Response:
        self.calls.append(kwargs)
        return self._response

    @property
    def last(self) -> dict[str, Any]:
        assert self.calls, "provider boundary was never called"
        return self.calls[-1]


@pytest.fixture(autouse=True)
def _no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing here may depend on a key: these tests are T1, offline."""
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    assert "ANTHROPIC_API_KEY" not in os.environ


# --- The routing table (Article VII: pinned, never floating) ---------------


@pytest.mark.parametrize("tier", list(TaskTier))
def test_routing_table_resolves_the_pinned_model(tier: TaskTier) -> None:
    pin = resolve_model(tier)
    assert pin.model_id == EXPECTED_MODEL_ID[tier]
    assert pin.provider == "anthropic"
    assert pin.ref == f"anthropic/{EXPECTED_MODEL_ID[tier]}"


def test_every_tier_is_routed() -> None:
    assert set(ROUTING_TABLE) == set(TaskTier)
    assert set(EFFORT_BY_TIER) == set(TaskTier)


@pytest.mark.parametrize("tier", list(TaskTier))
def test_every_pin_is_dated_or_says_why_not(tier: TaskTier) -> None:
    """Article VII: a dated snapshot, or an explicit recorded reason there is none."""
    pin = resolve_model(tier)
    assert pin.is_dated_snapshot or pin.undated_reason
    assert pin.pinned_on and pin.source


def test_research_tier_uses_the_dated_haiku_snapshot() -> None:
    """Haiku 4.5 does publish a dated id, so the pin must not be the bare alias."""
    pin = resolve_model(TaskTier.RESEARCH)
    assert pin.is_dated_snapshot
    assert pin.model_id != "claude-haiku-4-5"


def test_undated_pin_without_a_reason_is_rejected() -> None:
    with pytest.raises(ValueError, match="Article VII"):
        ModelPin(
            provider="anthropic",
            model_id="claude-opus-5",
            pinned_on="2026-08-01",
            source="test",
        )


def test_dated_pin_with_a_redundant_reason_is_rejected() -> None:
    with pytest.raises(ValueError, match="dated snapshot"):
        ModelPin(
            provider="anthropic",
            model_id="claude-haiku-4-5-20251001",
            pinned_on="2026-08-01",
            source="test",
            undated_reason="not needed",
        )


# --- The capability gate (planner-spike constraint 2) ----------------------


def test_haiku_is_outside_the_adaptive_effort_set() -> None:
    assert EXPECTED_MODEL_ID[TaskTier.RESEARCH] not in SUPPORTS_ADAPTIVE_EFFORT
    assert EXPECTED_MODEL_ID[TaskTier.CURATE] in SUPPORTS_ADAPTIVE_EFFORT
    assert EXPECTED_MODEL_ID[TaskTier.PLAN] in SUPPORTS_ADAPTIVE_EFFORT


def test_supports_adaptive_effort_by_tier() -> None:
    assert not supports_adaptive_effort(TaskTier.RESEARCH)
    assert supports_adaptive_effort(TaskTier.CURATE)
    assert supports_adaptive_effort(TaskTier.PLAN)


def test_research_tier_strips_adaptive_and_effort() -> None:
    """Haiku 4.5 400s on either — the seam strips, and does not raise."""
    request = build_request(
        TaskTier.RESEARCH,
        system="s",
        messages=[Message("user", "hi")],
        adaptive_thinking=True,
        effort="high",
    )
    assert "thinking" not in request
    assert "reasoning_effort" not in request
    assert request["model"] == "anthropic/claude-haiku-4-5-20251001"


@pytest.mark.parametrize("tier", [TaskTier.CURATE, TaskTier.PLAN])
def test_supporting_tiers_send_adaptive_thinking_and_default_effort(tier: TaskTier) -> None:
    request = build_request(tier, system="s", messages=[Message("user", "hi")])
    # 4.6+ shape: adaptive, never the removed `budget_tokens`.
    assert request["thinking"] == {"type": "adaptive"}
    assert "budget_tokens" not in request.get("thinking", {})
    assert request["reasoning_effort"] == EFFORT_BY_TIER[tier]


def test_explicit_effort_is_honoured_on_a_supporting_tier() -> None:
    request = build_request(
        TaskTier.CURATE, system="s", messages=[Message("user", "hi")], effort="xhigh"
    )
    assert request["reasoning_effort"] == "xhigh"


def test_adaptive_thinking_can_be_disabled_on_a_supporting_tier() -> None:
    request = build_request(
        TaskTier.PLAN,
        system="s",
        messages=[Message("user", "hi")],
        adaptive_thinking=False,
    )
    assert "thinking" not in request
    assert request["reasoning_effort"] == EFFORT_BY_TIER[TaskTier.PLAN]


def test_long_run_degrades_instead_of_leaking_a_parameter() -> None:
    """Server-side compaction is deferred with the Anthropic-native adapter."""
    request = build_request(
        TaskTier.PLAN, system="s", messages=[Message("user", "hi")], long_run=True
    )
    assert "context_management" not in request
    assert "long_run" not in request


# --- Request shaping -------------------------------------------------------


def test_system_prefix_carries_a_cache_breakpoint_only_when_asked() -> None:
    plain = build_request(TaskTier.CURATE, system="S", messages=[Message("user", "hi")])
    assert plain["messages"][0] == {"role": "system", "content": "S"}

    cached = build_request(
        TaskTier.CURATE, system="S", messages=[Message("user", "hi")], cache_prefix=True
    )
    assert cached["messages"][0]["content"] == [
        {"type": "text", "text": "S", "cache_control": {"type": "ephemeral"}}
    ]


def test_messages_and_tools_are_translated() -> None:
    request = build_request(
        TaskTier.CURATE,
        system="S",
        messages=[Message("user", "a"), Message("assistant", "b")],
        tools=[ToolSpec("emit", "emit a site", {"type": "object", "properties": {}})],
    )
    assert [m["role"] for m in request["messages"]] == ["system", "user", "assistant"]
    assert request["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "emit",
                "description": "emit a site",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    assert request["max_tokens"] == DEFAULT_MAX_TOKENS


def test_no_tools_key_when_no_tools() -> None:
    request = build_request(TaskTier.PLAN, system="S", messages=[Message("user", "a")])
    assert "tools" not in request


# --- The router over a stubbed boundary ------------------------------------


def test_router_satisfies_the_seam_protocol() -> None:
    assert isinstance(LiteLLMRouter(_Recorder()), ModelRouter)


def test_router_never_sends_adaptive_or_effort_to_research() -> None:
    recorder = _Recorder()
    LiteLLMRouter(recorder).complete(
        TaskTier.RESEARCH,
        system="S",
        messages=[Message("user", "hi")],
        effort="high",
    )
    assert recorder.last["model"] == "anthropic/claude-haiku-4-5-20251001"
    assert "thinking" not in recorder.last
    assert "reasoning_effort" not in recorder.last


def test_router_returns_a_provider_neutral_completion() -> None:
    response = _Response(
        choices=[
            _Choice(
                _Msg(
                    content="hello",
                    tool_calls=[_Call("call_1", _Function("emit", '{"name": "Acropolis"}'))],
                )
            )
        ],
        usage=_Usage(
            prompt_tokens=120,
            completion_tokens=7,
            prompt_tokens_details=_Details(cached_tokens=14_993, cache_creation_tokens=3),
        ),
    )
    result = LiteLLMRouter(_Recorder(response)).complete(
        TaskTier.CURATE, system="S", messages=[Message("user", "hi")]
    )
    assert isinstance(result, Completion)
    assert result.text == "hello"
    assert result.tool_calls[0].name == "emit"
    assert result.tool_calls[0].args == {"name": "Acropolis"}
    assert result.usage.input_tokens == 120
    assert result.usage.output_tokens == 7
    # The lever the ADR-0004 caching eval asserts on.
    assert result.usage.cache_read_input_tokens == 14_993
    assert result.usage.cache_creation_input_tokens == 3
    assert result.usage.model == "claude-sonnet-5"


def test_router_tolerates_an_empty_response() -> None:
    result = LiteLLMRouter(_Recorder(_Response(choices=[]))).complete(
        TaskTier.PLAN, system="S", messages=[Message("user", "hi")]
    )
    assert result.text == ""
    assert result.tool_calls == ()


def test_malformed_tool_arguments_do_not_crash_the_seam() -> None:
    response = _Response(
        choices=[_Choice(_Msg(content=None, tool_calls=[_Call("c", _Function("emit", "{"))]))]
    )
    result = LiteLLMRouter(_Recorder(response)).complete(
        TaskTier.CURATE, system="S", messages=[Message("user", "hi")]
    )
    assert result.text == ""
    assert result.tool_calls[0].args == {}
