"""The provider-independent parts of the LLM layer: pricing, the strict schema, the rate
limiter, output parsing, the FakeLLM and the factory's startup checks."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel, Field

from app.ai.llm.base import (
    LLMInvalidOutput,
    LLMRateLimited,
    LLMUsage,
    Message,
    StreamDone,
    TextDelta,
    parse_structured,
    split_system,
    total_usage,
)
from app.ai.llm.factory import FAKE_NO_REVIEW, LLMConfigError, build_llm, model_name
from app.ai.llm.fake import FakeLLM, FakeScriptExhausted
from app.ai.llm.groq import GroqProvider
from app.ai.llm.json_schema import provider_schema
from app.ai.llm.pricing import PRICING, UnknownModelPrice, estimate_cost, price_for
from app.ai.llm.rate_limit import RequestRateLimiter
from app.ai.schemas import AgentOutput, Finding
from tests.app_utils import make_settings

USAGE = LLMUsage("m", 1, 1, 1, Decimal(0))


# --- pricing ----------------------------------------------------------------------------------


def test_cost_is_computed_per_million_tokens() -> None:
    # gpt-oss-120b: $0.15 in, $0.60 out per 1M tokens.
    cost = estimate_cost("openai/gpt-oss-120b", 10_000, 2_000, on=date(2026, 10, 17))
    assert cost == Decimal("0.002700")  # 0.0015 + 0.0012


def test_announced_price_change_applies_from_its_date() -> None:
    before = estimate_cost("gemini-3.8-flash", 1_000_000, 0, on=date(2026, 12, 31))
    after = estimate_cost("gemini-3.8-flash", 1_000_000, 0, on=date(2027, 1, 1))
    assert (before, after) == (Decimal("0.75"), Decimal("1.50"))


def test_unknown_model_has_no_price() -> None:
    with pytest.raises(UnknownModelPrice, match="pricing.py"):
        price_for("someone/new-model", date(2026, 10, 17))


def test_default_models_are_priced() -> None:
    for provider in ("groq", "gemini", "fake"):
        assert model_name(make_settings(llm_provider=provider)) in PRICING


def test_usage_totals_add_up() -> None:
    a = LLMUsage("m", 10, 5, 100, Decimal("0.001"))
    b = LLMUsage("m", 20, 7, 50, Decimal("0.002"))
    totals = total_usage([a, b])
    assert (totals.calls, totals.tokens_in, totals.tokens_out) == (2, 30, 12)
    assert (totals.cost_usd, totals.latency_ms) == (Decimal("0.003"), 150)


# --- strict schema ----------------------------------------------------------------------------


def _walk(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        found.append(node)
        for value in node.values():
            found += _walk(value)
    elif isinstance(node, list):
        for value in node:
            found += _walk(value)
    return found


def test_provider_schema_is_strict_and_self_contained() -> None:
    schema = provider_schema(AgentOutput)
    nodes = _walk(schema)
    assert not any("$ref" in n or "$defs" in n for n in nodes)  # refs inlined
    for node in nodes:
        if node.get("type") == "object":
            # Groq strict mode: every property required, no extra properties.
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
        assert not {"minimum", "maximum", "maxLength", "title", "default"} & set(node)


def test_optional_fields_become_nullable_and_descriptions_survive() -> None:
    finding = provider_schema(AgentOutput)["properties"]["findings"]["items"]
    assert finding["properties"]["end_line"]["anyOf"] == [{"type": "integer"}, {"type": "null"}]
    severity = finding["properties"]["severity"]
    assert severity["enum"] == ["info", "low", "medium", "high", "critical"]
    assert severity["description"]  # kept when the $ref was inlined


class _Node(BaseModel):
    children: list["_Node"] = Field(default_factory=list)


def test_recursive_schemas_are_refused() -> None:
    with pytest.raises(ValueError, match="recursive"):
        provider_schema(_Node)


# --- parsing model output ---------------------------------------------------------------------


def test_parse_structured_reports_field_locations_not_content() -> None:
    raw = '{"change_summary": "x", "findings": [{"file": "a.py", "line": 0, "secret": "s3"}]}'
    with pytest.raises(LLMInvalidOutput) as exc_info:
        parse_structured(AgentOutput, raw, USAGE)
    problems = " ".join(exc_info.value.problems)
    assert "findings.0.line" in problems and "findings.0.severity" in problems
    assert "s3" not in problems and "a.py" not in problems
    assert exc_info.value.usage == USAGE  # tokens spent on a bad answer still count


@pytest.mark.parametrize("raw", [None, "", "   ", "not json", '{"findings": []'])
def test_parse_structured_rejects_empty_or_broken_json(raw: str | None) -> None:
    with pytest.raises(LLMInvalidOutput):
        parse_structured(AgentOutput, raw, USAGE)


def test_unknown_keys_from_the_model_are_ignored() -> None:
    raw = '{"change_summary": "x", "findings": [], "approve": true, "event": "APPROVE"}'
    assert parse_structured(AgentOutput, raw, USAGE).findings == []


def test_finding_normalises_category_and_collapses_one_line_ranges() -> None:
    f = Finding(
        file="a.py",
        line=3,
        end_line=3,
        severity="high",
        category="SQL_Injection",
        message="m",
        confidence=0.9,
        evidence="x",
    )
    assert (f.category, f.end_line) == ("sql-injection", None)


def test_split_system_joins_system_messages() -> None:
    system, rest = split_system(
        [Message("system", "a"), Message("user", "u"), Message("system", "b")]
    )
    assert system == "a\n\nb" and rest == [Message("user", "u")]


# --- rate limiter -----------------------------------------------------------------------------


async def test_rate_limiter_waits_for_the_window() -> None:
    now = [0.0]
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds

    limiter = RequestRateLimiter(2, clock=lambda: now[0], sleep=sleep)
    await limiter.acquire()
    now[0] = 10.0
    await limiter.acquire()
    await limiter.acquire()  # third call in the same minute: waits until the first expires
    assert slept == [50.0]
    assert now[0] == 60.0


# --- FakeLLM ----------------------------------------------------------------------------------


async def test_fake_llm_follows_its_script_and_records_calls() -> None:
    fake = FakeLLM(["not json", {"change_summary": "s", "findings": []}, LLMRateLimited("429")])
    messages = [Message("user", "hi")]
    with pytest.raises(LLMInvalidOutput):
        await fake.generate_structured(AgentOutput, messages)
    result = await fake.generate_structured(AgentOutput, messages)
    assert result.value.change_summary == "s"
    assert result.usage.cost_usd == 0 and result.usage.tokens_in > 0
    with pytest.raises(LLMRateLimited):
        await fake.generate_text(messages)
    with pytest.raises(FakeScriptExhausted):
        await fake.generate_text(messages)
    assert [c.kind for c in fake.calls] == ["structured", "structured", "text", "text"]


async def test_fake_llm_streams_text_then_usage() -> None:
    fake = FakeLLM(["hello streaming world, this is long enough for two chunks"])
    events = [e async for e in fake.stream_text([Message("user", "hi")])]
    assert isinstance(events[-1], StreamDone)
    assert "".join(e.text for e in events if isinstance(e, TextDelta)).startswith("hello")


# --- factory (worker startup) -----------------------------------------------------------------


def test_fake_provider_is_refused_in_production() -> None:
    with pytest.raises(LLMConfigError, match="development and tests only"):
        build_llm(make_settings(llm_provider="fake", environment="production"))


async def test_unscripted_dev_fake_says_no_review_happened() -> None:
    llm = build_llm(make_settings(llm_provider="fake", environment="development"))
    result = await llm.generate_structured(AgentOutput, [Message("user", "diff")])
    assert result.value.findings == []
    assert "no AI review was performed" in result.value.change_summary
    assert "no AI review" in FAKE_NO_REVIEW


@pytest.mark.parametrize(("provider", "variable"), [("groq", "GROQ"), ("gemini", "GEMINI")])
def test_chosen_provider_needs_its_key(provider: str, variable: str) -> None:
    with pytest.raises(LLMConfigError, match=f"{variable}_API_KEY"):
        build_llm(make_settings(llm_provider=provider))


def test_unpriced_model_stops_startup() -> None:
    settings = make_settings(llm_provider="groq", groq_api_key="k", llm_model="new/model")
    with pytest.raises(UnknownModelPrice):
        build_llm(settings)


async def test_groq_is_the_default_provider() -> None:
    settings = make_settings(llm_provider="groq", groq_api_key="gsk_test")
    llm = build_llm(settings)
    try:
        assert isinstance(llm, GroqProvider)
        assert llm.model == "openai/gpt-oss-120b"
    finally:
        await llm.aclose()
