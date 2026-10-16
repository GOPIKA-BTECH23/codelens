"""FakeLLM: a deterministic, scripted provider for tests (and for local runs without a key).

Each call takes the next item from the script:
  * a Pydantic model or a dict  -> returned as its JSON (structured) or text
  * a str                       -> returned as the raw model text, so a test can feed in
                                   malformed JSON and watch the retry logic
  * an Exception                -> raised, e.g. LLMRateLimited to test transient failures
When the script runs out, `fallback(schema)` supplies the text if set; otherwise the call
fails loudly, so a test never passes by accident on an unscripted call.

Every call is recorded in `calls` (the exact messages), which is how the prompt-injection
contract test checks what the model would have seen.

Usage is estimated from text length and priced as `price_as` (free by default). Tests that
check cost recording pass a real model's name, so the numbers are non-zero.
"""

import json
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel

from app.ai.llm.base import (
    LLMUsage,
    Message,
    StreamDone,
    StreamEvent,
    StructuredResult,
    TextDelta,
    TextResult,
    estimate_tokens,
    parse_structured,
)
from app.ai.llm.pricing import estimate_cost

FAKE_MODEL = "fake-llm"

ScriptItem = BaseModel | dict[str, Any] | str | Exception
Fallback = Callable[[type[BaseModel] | None], str]


@dataclass(frozen=True)
class FakeCall:
    kind: str  # "structured" | "text" | "stream"
    schema: type[BaseModel] | None
    messages: tuple[Message, ...]


class FakeScriptExhausted(AssertionError):
    """A call arrived that the test did not script."""


class FakeLLM:
    name = "fake"

    def __init__(
        self,
        script: Iterable[ScriptItem] = (),
        *,
        model: str = FAKE_MODEL,
        fallback: Fallback | None = None,
        price_as: str = FAKE_MODEL,
    ) -> None:
        self.model = model
        self._price_as = price_as
        self._script: deque[ScriptItem] = deque(script)
        self._fallback = fallback
        self.calls: list[FakeCall] = []

    def add(self, *items: ScriptItem) -> None:
        self._script.extend(items)

    # --- LLMProvider ---------------------------------------------------------------------------

    async def generate_structured[T: BaseModel](
        self, schema: type[T], messages: Sequence[Message], *, max_output_tokens: int = 4096
    ) -> StructuredResult[T]:
        raw = self._next("structured", schema, messages)
        usage = self._usage(messages, raw)
        return StructuredResult(parse_structured(schema, raw, usage), usage)

    async def generate_text(
        self, messages: Sequence[Message], *, max_output_tokens: int = 4096
    ) -> TextResult:
        raw = self._next("text", None, messages)
        return TextResult(raw, self._usage(messages, raw))

    async def stream_text(
        self, messages: Sequence[Message], *, max_output_tokens: int = 4096
    ) -> AsyncIterator[StreamEvent]:
        raw = self._next("stream", None, messages)
        for start in range(0, len(raw), 16):
            yield TextDelta(raw[start : start + 16])
        yield StreamDone(self._usage(messages, raw))

    def estimate_tokens(self, text: str) -> int:
        return estimate_tokens(text)

    async def aclose(self) -> None:
        return None

    # --- internals -----------------------------------------------------------------------------

    def _next(self, kind: str, schema: type[BaseModel] | None, messages: Sequence[Message]) -> str:
        self.calls.append(FakeCall(kind, schema, tuple(messages)))
        if self._script:
            item = self._script.popleft()
        elif self._fallback is not None:
            return self._fallback(schema)
        else:
            raise FakeScriptExhausted(f"FakeLLM got an unscripted {kind} call")
        if isinstance(item, Exception):
            raise item
        if isinstance(item, BaseModel):
            return item.model_dump_json()
        if isinstance(item, dict):
            return json.dumps(item)
        return item

    def _usage(self, messages: Sequence[Message], raw: str) -> LLMUsage:
        tokens_in = sum(estimate_tokens(m.content) for m in messages)
        tokens_out = estimate_tokens(raw)
        return LLMUsage(
            model=self.model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=0,
            cost_usd=estimate_cost(self._price_as, tokens_in, tokens_out, on=date.today()),
        )
