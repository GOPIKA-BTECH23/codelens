"""Contract tests for the real providers, with the HTTP layer mocked by respx (no network).

They pin the request we send and how we read the answer:
  * Groq: request and response shapes were CAPTURED from one live call on 2026-10-17 (a toy
    schema, no code); the response below keeps that structure with our own content.
  * Gemini: the request shape was captured live the same day (POST /v1beta/interactions,
    `store: false`). A successful response could not be captured (the test key's project was
    denied access, HTTP 403), so the success body below is CONSTRUCTED from the
    google-genai 2.31.0 models (steps -> model_output -> text, usage.total_*_tokens) and the
    docs' REST example. The 403 body IS the captured one.
Both SDKs use httpx, which respx intercepts. SDK retries are turned off (max_retries=0) so
error tests make exactly one request.
"""

import json
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import httpx
import pytest
import respx

from app.ai.llm.base import (
    LLMInvalidOutput,
    LLMRateLimited,
    LLMRequestRejected,
    LLMUnavailable,
    Message,
)
from app.ai.llm.gemini import GeminiProvider
from app.ai.llm.groq import GroqProvider
from app.ai.llm.json_schema import provider_schema
from app.ai.llm.pricing import estimate_cost
from app.ai.llm.rate_limit import RequestRateLimiter
from app.ai.schemas import AgentOutput

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
SECRET_KEY = "key-Zq9SentinelValue"
MESSAGES = [Message("system", "be a reviewer"), Message("user", "the diff")]
ANSWER = {
    "change_summary": "Adds a query helper.",
    "findings": [
        {
            "file": "app/users.py",
            "line": 9,
            "end_line": None,
            "severity": "high",
            "category": "sql-injection",
            "message": "User input is concatenated into SQL.",
            "suggestion": None,
            "confidence": 0.93,
            "evidence": "query = ",
        }
    ],
}


@pytest.fixture
def mock() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        yield router


def _sent(route: respx.Route, index: int = -1) -> dict[str, Any]:
    body = json.loads(route.calls[index].request.content)
    assert isinstance(body, dict)
    return body


# --- Groq -------------------------------------------------------------------------------------


def groq_response(content: str, *, finish_reason: str = "stop") -> dict[str, Any]:
    """Shape captured from a live call (2026-10-17)."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1792180317,
        "model": "openai/gpt-oss-120b",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content, "reasoning": "..."},
                "logprobs": None,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": 1200,
            "completion_tokens": 300,
            "total_tokens": 1500,
            "completion_tokens_details": {"reasoning_tokens": 120},
        },
        "system_fingerprint": "fp_test",
        "x_groq": {"id": "req_test"},
        "service_tier": "on_demand",
    }


@pytest.fixture
async def groq() -> Any:
    provider = GroqProvider(
        api_key=SECRET_KEY,
        model="openai/gpt-oss-120b",
        limiter=RequestRateLimiter(1000),
        max_retries=0,
    )
    yield provider
    await provider.aclose()


async def test_groq_sends_a_strict_json_schema_and_reads_usage(
    mock: respx.MockRouter, groq: GroqProvider
) -> None:
    route = mock.post(GROQ_URL).respond(200, json=groq_response(json.dumps(ANSWER)))

    result = await groq.generate_structured(AgentOutput, MESSAGES, max_output_tokens=777)

    body = _sent(route)
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["messages"] == [
        {"role": "system", "content": "be a reviewer"},
        {"role": "user", "content": "the diff"},
    ]
    assert body["max_completion_tokens"] == 777
    assert body["reasoning_effort"] == "low"
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "AgentOutput",
            "strict": True,
            "schema": provider_schema(AgentOutput),
        },
    }
    assert route.calls[0].request.headers["authorization"] == f"Bearer {SECRET_KEY}"
    assert result.value.findings[0].category == "sql-injection"
    # completion_tokens already includes the reasoning tokens (verified live): no double count.
    assert (result.usage.tokens_in, result.usage.tokens_out) == (1200, 300)
    # 1200 * 0.15 / 1M + 300 * 0.60 / 1M
    assert result.usage.cost_usd == Decimal("0.000360")
    assert result.usage.model == "openai/gpt-oss-120b"


async def test_groq_truncated_output_is_invalid_output(
    mock: respx.MockRouter, groq: GroqProvider
) -> None:
    mock.post(GROQ_URL).respond(
        200, json=groq_response('{"change_summary": "x", "fin', finish_reason="length")
    )
    with pytest.raises(LLMInvalidOutput, match="cut off") as exc_info:
        await groq.generate_structured(AgentOutput, MESSAGES)
    assert exc_info.value.usage is not None and exc_info.value.usage.tokens_out == 300


async def test_groq_schema_validation_failure_is_invalid_output(
    mock: respx.MockRouter, groq: GroqProvider
) -> None:
    mock.post(GROQ_URL).respond(
        400,
        json={
            "error": {
                "message": "Generated JSON does not match the expected schema.",
                "type": "invalid_request_error",
                "code": "json_validate_failed",
            }
        },
    )
    with pytest.raises(LLMInvalidOutput):
        await groq.generate_structured(AgentOutput, MESSAGES)


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (429, LLMRateLimited),
        (401, LLMRequestRejected),
        (403, LLMRequestRejected),
        (503, LLMUnavailable),
    ],
)
async def test_groq_http_errors_are_translated_without_leaking(
    mock: respx.MockRouter, groq: GroqProvider, status: int, error: type[Exception]
) -> None:
    route = mock.post(GROQ_URL).respond(status, json={"error": {"message": f"echo {SECRET_KEY}"}})
    with pytest.raises(error) as exc_info:
        await groq.generate_structured(AgentOutput, MESSAGES)
    assert SECRET_KEY not in str(exc_info.value)
    assert route.call_count == 1  # max_retries=0: the SDK did not retry


async def test_groq_network_failure_is_unavailable(
    mock: respx.MockRouter, groq: GroqProvider
) -> None:
    mock.post(GROQ_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(LLMUnavailable):
        await groq.generate_text(MESSAGES)


# --- Gemini -----------------------------------------------------------------------------------


def gemini_response(text: str) -> dict[str, Any]:
    """CONSTRUCTED from the SDK models (see module docstring), not captured."""
    return {
        "id": "int_test",
        "status": "completed",
        "model": "gemini-3.8-flash",
        "steps": [{"type": "model_output", "content": [{"type": "text", "text": text}]}],
        "usage": {
            "total_input_tokens": 1000,
            "total_output_tokens": 200,
            "total_thought_tokens": 50,
            "total_tokens": 1250,
        },
    }


@pytest.fixture
async def gemini() -> Any:
    provider = GeminiProvider(
        api_key=SECRET_KEY,
        model="gemini-3.8-flash",
        limiter=RequestRateLimiter(1000),
        max_retries=0,
    )
    yield provider
    await provider.aclose()


async def test_gemini_never_stores_and_sends_the_schema(
    mock: respx.MockRouter, gemini: GeminiProvider
) -> None:
    route = mock.post(GEMINI_URL).respond(200, json=gemini_response(json.dumps(ANSWER)))

    result = await gemini.generate_structured(AgentOutput, MESSAGES, max_output_tokens=777)

    body = _sent(route)
    assert body["store"] is False  # PR code must not be kept by the provider
    assert body["model"] == "gemini-3.8-flash"
    assert body["system_instruction"] == "be a reviewer"
    assert body["input"] == "the diff"
    assert body["generation_config"] == {"max_output_tokens": 777, "thinking_level": "low"}
    assert body["response_format"] == {
        "type": "text",
        "mime_type": "application/json",
        "schema": provider_schema(AgentOutput),
    }
    assert route.calls[0].request.headers["x-goog-api-key"] == SECRET_KEY
    assert result.value.findings[0].line == 9
    # Thinking tokens are billed as output.
    assert (result.usage.tokens_in, result.usage.tokens_out) == (1000, 250)
    assert result.usage.cost_usd == estimate("gemini-3.8-flash", 1000, 250)


def estimate(model: str, tokens_in: int, tokens_out: int) -> Decimal:
    return estimate_cost(model, tokens_in, tokens_out, on=date.today())


async def test_gemini_text_and_multi_turn_input_also_never_store(
    mock: respx.MockRouter, gemini: GeminiProvider
) -> None:
    route = mock.post(GEMINI_URL).respond(200, json=gemini_response("plain answer"))
    result = await gemini.generate_text(
        [Message("user", "q1"), Message("assistant", "a1"), Message("user", "q2")]
    )
    body = _sent(route)
    assert body["store"] is False
    assert "response_format" not in body
    assert [step["type"] for step in body["input"]] == ["user_input", "model_output", "user_input"]
    assert result.text == "plain answer"


async def test_gemini_stream_request_also_never_stores(
    mock: respx.MockRouter, gemini: GeminiProvider
) -> None:
    # The SSE success format is not captured, so this only pins the request: answer 403 and
    # look at what was sent.
    route = mock.post(GEMINI_URL).respond(
        403, json={"error": {"message": "denied", "code": "permission_denied"}}
    )
    with pytest.raises(LLMRequestRejected):
        async for _ in gemini.stream_text(MESSAGES):
            pass
    body = _sent(route)
    assert body["store"] is False and body["stream"] is True


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (429, {"error": {"message": "slow down", "code": "resource_exhausted"}}, LLMRateLimited),
        # Captured live on 2026-10-17 (the key's project was denied access).
        (
            403,
            {
                "error": {
                    "message": "Your project has been denied access. Please contact support.",
                    "code": "permission_denied",
                }
            },
            LLMRequestRejected,
        ),
        (500, {"error": {"message": "internal", "code": "internal"}}, LLMUnavailable),
    ],
)
async def test_gemini_http_errors_are_translated(
    mock: respx.MockRouter,
    gemini: GeminiProvider,
    status: int,
    body: dict[str, Any],
    error: type[Exception],
) -> None:
    route = mock.post(GEMINI_URL).respond(status, json=body)
    with pytest.raises(error) as exc_info:
        await gemini.generate_structured(AgentOutput, MESSAGES)
    assert SECRET_KEY not in str(exc_info.value)
    # SDK 2.31.0 quirk (see gemini.py): the Interactions client always retries at least once
    # on 429/5xx, even with max_retries=0. A 403 is never retried.
    assert route.call_count == (1 if status == 403 else 2)


def test_interactions_error_classes_are_where_we_import_them_from() -> None:
    """gemini.py imports the Interactions API's errors from a PRIVATE SDK module. If an SDK
    upgrade moves them, this fails here instead of letting errors slip past our handlers."""
    from google.genai._gaos.lib import compat_errors

    for name in ("APIError", "RateLimitError", "APIConnectionError", "APIStatusError"):
        assert isinstance(getattr(compat_errors, name), type)
    assert issubclass(compat_errors.RateLimitError, compat_errors.APIError)
    assert issubclass(compat_errors.APIConnectionError, compat_errors.APIError)
