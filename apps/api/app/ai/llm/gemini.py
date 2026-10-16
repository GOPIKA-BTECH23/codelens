"""Gemini provider, on the Interactions API (decision 2026-10-17).

Verified on 2026-10-17 against https://ai.google.dev/gemini-api/docs/interactions,
.../structured-output, .../interactions/tokens and the `google-genai==2.31.0` SDK:
  * Google recommends the Interactions API for new projects; `generateContent` is "legacy"
    (still supported). Requests go to POST /v1beta/interactions.
  * The Interactions API STORES every interaction by default (55 days paid tier, 1 day free
    tier). We send `store=False` on every call: PR code must not be kept by the provider
    (CLAUDE.md decision 9). A contract test asserts this on every request.
  * Structured output: `response_format={"type": "text", "mime_type": "application/json",
    "schema": ...}`; the answer arrives as `interaction.output_text`.
  * Usage: `interaction.usage.total_input_tokens`, `total_output_tokens` and
    `total_thought_tokens`. Thinking is billed as output, so tokens_out = output + thought.
  * Gemini 3.6+ ignores `temperature`; effort is set with `generation_config.thinking_level`.
  * Streaming: `step.delta` events carry text deltas; `interaction.completed` carries usage.
  * The SDK's Interactions error classes live in a PRIVATE module (see _errors below). That
    import is the one fragile spot; tests/unit/test_llm_providers.py fails loudly if a future
    SDK version moves it.
"""

from collections.abc import AsyncIterator, Sequence
from datetime import date
from typing import Any

from google import genai
from google.genai import types

# The Interactions API raises these, not google.genai.errors.*. Not re-exported publicly in
# 2.31.0; pinned SDK version + contract test.
from google.genai._gaos.lib import compat_errors as _errors
from pydantic import BaseModel

from app.ai.llm.base import (
    LLMRateLimited,
    LLMRequestRejected,
    LLMUnavailable,
    LLMUsage,
    Message,
    Stopwatch,
    StreamDone,
    StreamEvent,
    StructuredResult,
    TextDelta,
    TextResult,
    estimate_tokens,
    parse_structured,
    split_system,
)
from app.ai.llm.json_schema import provider_schema
from app.ai.llm.pricing import estimate_cost
from app.ai.llm.rate_limit import RequestRateLimiter

DEFAULT_MODEL = "gemini-3.8-flash"


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        limiter: RequestRateLimiter,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        thinking_level: str = "low",
    ) -> None:
        self.model = model
        self._limiter = limiter
        self._timeout = timeout_seconds
        self._thinking_level = thinking_level
        self._client = genai.Client(
            api_key=api_key,
            # Careful: HttpRetryOptions documents `attempts` as "including the original
            # request", but for the Interactions API SDK 2.31.0 passes it on as the number of
            # RETRIES (_gaos/google_genai.py, _translate_retry_config), and the client raises
            # 0 to 1. So Gemini retries max(max_retries, 1) times; a contract test pins it.
            http_options=types.HttpOptions(
                retry_options=types.HttpRetryOptions(attempts=max_retries)
            ),
        )

    async def generate_structured[T: BaseModel](
        self, schema: type[T], messages: Sequence[Message], *, max_output_tokens: int = 4096
    ) -> StructuredResult[T]:
        response_format = {
            "type": "text",
            "mime_type": "application/json",
            "schema": provider_schema(schema),
        }
        text, usage = await self._create(messages, max_output_tokens, response_format)
        return StructuredResult(parse_structured(schema, text, usage), usage)

    async def generate_text(
        self, messages: Sequence[Message], *, max_output_tokens: int = 4096
    ) -> TextResult:
        text, usage = await self._create(messages, max_output_tokens, None)
        return TextResult(text or "", usage)

    async def stream_text(
        self, messages: Sequence[Message], *, max_output_tokens: int = 4096
    ) -> AsyncIterator[StreamEvent]:
        await self._limiter.acquire()
        watch = Stopwatch()
        usage: Any = None
        try:
            stream: Any = await self._client.aio.interactions.create(
                **self._body(messages, max_output_tokens), stream=True, timeout=self._timeout
            )
            async for event in stream:
                kind = getattr(event, "event_type", None)
                if kind == "step.delta":
                    delta = getattr(event, "delta", None)
                    text = getattr(delta, "text", None)
                    if getattr(delta, "type", None) == "text" and isinstance(text, str) and text:
                        yield TextDelta(text)
                elif kind == "interaction.completed":
                    usage = getattr(getattr(event, "interaction", None), "usage", None)
        except _errors.APIError as exc:
            raise _translate(exc) from exc
        yield StreamDone(self._usage(usage, watch))

    def estimate_tokens(self, text: str) -> int:
        return estimate_tokens(text)

    async def aclose(self) -> None:
        await self._client.aio.aclose()

    # --- internals -----------------------------------------------------------------------------

    def _body(self, messages: Sequence[Message], max_output_tokens: int) -> dict[str, Any]:
        system, conversation = split_system(messages)
        body: dict[str, Any] = {
            "model": self.model,
            "input": _input(conversation),
            "store": False,  # never keep PR code on Google's side (see module docstring)
            "generation_config": {
                "max_output_tokens": max_output_tokens,
                "thinking_level": self._thinking_level,
            },
        }
        if system:
            body["system_instruction"] = system
        return body

    async def _create(
        self,
        messages: Sequence[Message],
        max_output_tokens: int,
        response_format: dict[str, Any] | None,
    ) -> tuple[str | None, LLMUsage]:
        await self._limiter.acquire()
        watch = Stopwatch()
        body = self._body(messages, max_output_tokens)
        if response_format is not None:
            body["response_format"] = response_format
        try:
            interaction: Any = await self._client.aio.interactions.create(
                **body, timeout=self._timeout
            )
        except _errors.APIError as exc:
            raise _translate(exc) from exc
        return getattr(interaction, "output_text", None), self._usage(
            getattr(interaction, "usage", None), watch
        )

    def _usage(self, usage: Any, watch: Stopwatch) -> LLMUsage:
        tokens_in = int(getattr(usage, "total_input_tokens", None) or 0)
        tokens_out = int(getattr(usage, "total_output_tokens", None) or 0) + int(
            getattr(usage, "total_thought_tokens", None) or 0
        )
        return LLMUsage(
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=watch.elapsed_ms(),
            cost_usd=estimate_cost(self.model, tokens_in, tokens_out, on=date.today()),
        )


def _input(conversation: Sequence[Message]) -> str | list[dict[str, Any]]:
    """One user message goes as plain text; a longer conversation as user/model steps."""
    if len(conversation) == 1 and conversation[0].role == "user":
        return conversation[0].content
    return [
        {
            "type": "user_input" if m.role == "user" else "model_output",
            "content": [{"type": "text", "text": m.content}],
        }
        for m in conversation
    ]


def _translate(exc: Exception) -> Exception:
    """Map SDK errors to ours. Status only: response bodies can echo parts of the request."""
    if isinstance(exc, _errors.RateLimitError):
        return LLMRateLimited("gemini: rate limited (HTTP 429)")
    if isinstance(exc, _errors.APIConnectionError):  # includes APITimeoutError
        return LLMUnavailable(f"gemini: {type(exc).__name__}")
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status < 500:
        return LLMRequestRejected(f"gemini: request rejected (HTTP {status})")
    return LLMUnavailable(f"gemini: HTTP {status}" if status else f"gemini: {type(exc).__name__}")
