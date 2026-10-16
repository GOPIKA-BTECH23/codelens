"""Groq provider (the default, decision 2026-10-17).

Verified on 2026-10-17 against https://console.groq.com/docs/structured-outputs, the models
page, the `groq==1.7.0` SDK and one live call:
  * POST /openai/v1/chat/completions (OpenAI-compatible).
  * `response_format={"type": "json_schema", "json_schema": {"name", "strict": True,
    "schema"}}`: STRICT mode uses constrained decoding, so the answer always matches the
    schema (supported on openai/gpt-oss-20b and -120b). We still validate with Pydantic.
  * Usage: `usage.prompt_tokens` and `usage.completion_tokens`. The live call showed
    completion_tokens already INCLUDES reasoning tokens (133 + 53 = total 186, of which 25
    reasoning), so completion_tokens is the full billable output.
  * Streaming: the final chunk carries usage in `x_groq.usage` (SDK 1.7.0 has no
    `stream_options` parameter).
  * The SDK retries 429/5xx/connection errors itself (`max_retries`), then raises
    RateLimitError / APIConnectionError / APIStatusError, which we translate.
"""

from collections.abc import AsyncIterator, Sequence
from datetime import date
from typing import Any

import groq
from groq import AsyncGroq
from pydantic import BaseModel

from app.ai.llm.base import (
    LLMInvalidOutput,
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
)
from app.ai.llm.json_schema import provider_schema
from app.ai.llm.pricing import estimate_cost
from app.ai.llm.rate_limit import RequestRateLimiter

DEFAULT_MODEL = "openai/gpt-oss-120b"


class GroqProvider:
    name = "groq"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        limiter: RequestRateLimiter,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        reasoning_effort: str = "low",  # gpt-oss: low | medium | high. Low keeps reviews fast.
    ) -> None:
        self.model = model
        self._limiter = limiter
        self._reasoning_effort = reasoning_effort
        self._client = AsyncGroq(api_key=api_key, timeout=timeout_seconds, max_retries=max_retries)

    async def generate_structured[T: BaseModel](
        self, schema: type[T], messages: Sequence[Message], *, max_output_tokens: int = 4096
    ) -> StructuredResult[T]:
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "strict": True,
                "schema": provider_schema(schema),
            },
        }
        text, usage, finish_reason = await self._complete(
            messages, max_output_tokens, response_format=response_format
        )
        if finish_reason == "length":
            raise LLMInvalidOutput("output was cut off at max_output_tokens", usage=usage)
        return StructuredResult(parse_structured(schema, text, usage), usage)

    async def generate_text(
        self, messages: Sequence[Message], *, max_output_tokens: int = 4096
    ) -> TextResult:
        text, usage, _ = await self._complete(messages, max_output_tokens)
        return TextResult(text or "", usage)

    async def stream_text(
        self, messages: Sequence[Message], *, max_output_tokens: int = 4096
    ) -> AsyncIterator[StreamEvent]:
        await self._limiter.acquire()
        watch = Stopwatch()
        tokens_in = tokens_out = 0
        try:
            stream = await self._client.chat.completions.create(
                **self._request(messages, max_output_tokens), stream=True
            )
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    yield TextDelta(chunk.choices[0].delta.content)
                usage = (chunk.x_groq.usage if chunk.x_groq else None) or chunk.usage
                if usage is not None:
                    tokens_in, tokens_out = usage.prompt_tokens, usage.completion_tokens
        except groq.APIError as exc:
            raise _translate(exc) from exc
        yield StreamDone(self._usage(tokens_in, tokens_out, watch))

    def estimate_tokens(self, text: str) -> int:
        return estimate_tokens(text)

    async def aclose(self) -> None:
        await self._client.close()

    # --- internals -----------------------------------------------------------------------------

    def _request(self, messages: Sequence[Message], max_output_tokens: int) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_completion_tokens": max_output_tokens,
            "reasoning_effort": self._reasoning_effort,
        }

    async def _complete(
        self,
        messages: Sequence[Message],
        max_output_tokens: int,
        *,
        response_format: dict[str, Any] | None = None,
    ) -> tuple[str | None, LLMUsage, str | None]:
        await self._limiter.acquire()
        watch = Stopwatch()
        extra: dict[str, Any] = {"response_format": response_format} if response_format else {}
        try:
            response = await self._client.chat.completions.create(
                **self._request(messages, max_output_tokens), **extra
            )
        except groq.BadRequestError as exc:
            if _is_schema_failure(exc):
                # Best-effort mode only ("json_validate_failed"); strict mode should never
                # produce it, but if it does, the model's output was the problem.
                raise LLMInvalidOutput(
                    "provider rejected the output as not matching the schema",
                    usage=self._usage(0, 0, watch),
                ) from exc
            raise _translate(exc) from exc
        except groq.APIError as exc:
            raise _translate(exc) from exc
        usage = self._usage(
            response.usage.prompt_tokens if response.usage else 0,
            response.usage.completion_tokens if response.usage else 0,
            watch,
        )
        choice = response.choices[0] if response.choices else None
        if choice is None:
            return None, usage, None
        return choice.message.content, usage, choice.finish_reason

    def _usage(self, tokens_in: int, tokens_out: int, watch: Stopwatch) -> LLMUsage:
        return LLMUsage(
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=watch.elapsed_ms(),
            cost_usd=estimate_cost(self.model, tokens_in, tokens_out, on=date.today()),
        )


def _is_schema_failure(exc: groq.BadRequestError) -> bool:
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error", body)
    return isinstance(error, dict) and error.get("code") == "json_validate_failed"


def _translate(exc: groq.APIError) -> Exception:
    """Map SDK errors to ours. The message names the status only: response bodies can echo
    parts of the request."""
    if isinstance(exc, groq.RateLimitError):
        return LLMRateLimited("groq: rate limited (HTTP 429)")
    if isinstance(exc, groq.APIConnectionError):  # includes APITimeoutError
        return LLMUnavailable(f"groq: {type(exc).__name__}")
    if isinstance(exc, groq.APIStatusError):
        if exc.status_code >= 500:
            return LLMUnavailable(f"groq: HTTP {exc.status_code}")
        return LLMRequestRejected(f"groq: request rejected (HTTP {exc.status_code})")
    return LLMUnavailable(f"groq: {type(exc).__name__}")
