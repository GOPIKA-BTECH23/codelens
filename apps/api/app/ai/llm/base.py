"""The provider interface every LLM call goes through (CLAUDE.md section 10).

Agents never import a vendor SDK. They call an `LLMProvider`, and every call returns an
`LLMUsage`: model, tokens in/out, latency and the ESTIMATED cost at list price. That one rule
is what lets us record cost per run, enforce the daily cap, and swap Groq for Gemini (or the
FakeLLM in tests) with an environment variable.

Errors are translated into the four classes below so callers can decide what to do without
knowing which vendor raised them:
    LLMRateLimited, LLMUnavailable -> transient: the job queue retries later
    LLMRequestRejected             -> permanent: bad key, denied project, bad request
    LLMInvalidOutput               -> the model answered, but not with valid structured output
"""

import time
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import BaseModel, ValidationError

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True)
class LLMUsage:
    """What one call consumed. `tokens_out` includes reasoning/thinking tokens: providers bill
    them as output, so leaving them out would under-report cost."""

    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: int
    cost_usd: Decimal  # estimated at list price (decision 8), never a real bill


@dataclass(frozen=True)
class UsageTotals:
    calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    latency_ms: int  # sum of call latencies (calls may overlap, so this is not wall time)


def total_usage(usages: Sequence[LLMUsage]) -> UsageTotals:
    return UsageTotals(
        calls=len(usages),
        tokens_in=sum(u.tokens_in for u in usages),
        tokens_out=sum(u.tokens_out for u in usages),
        cost_usd=sum((u.cost_usd for u in usages), Decimal(0)),
        latency_ms=sum(u.latency_ms for u in usages),
    )


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    value: T
    usage: LLMUsage


@dataclass(frozen=True)
class TextResult:
    text: str
    usage: LLMUsage


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class StreamDone:
    usage: LLMUsage


StreamEvent = TextDelta | StreamDone


# --- errors ------------------------------------------------------------------------------------


class LLMError(Exception):
    """Base class. Messages are safe to log: they never contain prompts, code or keys."""


class LLMRateLimited(LLMError):
    """The provider said "slow down" (HTTP 429) and its own retries did not get through."""


class LLMUnavailable(LLMError):
    """Network error, timeout or 5xx after the SDK's retries."""


class LLMRequestRejected(LLMError):
    """A 4xx other than 429: wrong key, project denied access, unknown model, bad request.
    Retrying the same request cannot help."""


class LLMInvalidOutput(LLMError):
    """The model answered, but the answer is not valid JSON for the schema (or was cut off).

    Carries the usage, because those tokens were spent (and billed) all the same. `problems`
    lists field locations only, never the model's text (it may quote source code).
    """

    def __init__(self, message: str, *, usage: LLMUsage | None, problems: Sequence[str] = ()):
        super().__init__(message)
        self.usage = usage
        self.problems = tuple(problems)


# --- the interface -----------------------------------------------------------------------------


class LLMProvider(Protocol):
    """What agents depend on. Implementations: GroqProvider, GeminiProvider, FakeLLM."""

    name: str
    model: str

    async def generate_structured[T: BaseModel](
        self, schema: type[T], messages: Sequence[Message], *, max_output_tokens: int = 4096
    ) -> StructuredResult[T]:
        """Ask for JSON matching `schema` and return it validated by Pydantic.

        Raises LLMInvalidOutput if the answer does not validate (the caller decides whether
        to retry), or one of the transport errors above.
        """
        ...

    async def generate_text(
        self, messages: Sequence[Message], *, max_output_tokens: int = 4096
    ) -> TextResult: ...

    def stream_text(
        self, messages: Sequence[Message], *, max_output_tokens: int = 4096
    ) -> AsyncIterator[StreamEvent]:
        """Yield TextDelta chunks, then exactly one StreamDone with the usage."""
        ...

    def estimate_tokens(self, text: str) -> int:
        """A cheap LOCAL estimate, used only to size diff chunks before calling the model.
        Recorded usage always comes from the provider's own response."""
        ...

    async def aclose(self) -> None: ...


# --- helpers shared by the implementations -----------------------------------------------------


def estimate_tokens(text: str) -> int:
    """~4 characters per token, the usual rule of thumb for English and code with BPE
    tokenizers. Deliberately rough and slightly pessimistic; it only sizes chunks."""
    return len(text) // 4 + 1


class Stopwatch:
    """Milliseconds since construction, on the monotonic clock."""

    def __init__(self) -> None:
        self._start = time.monotonic()

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._start) * 1000)


def parse_structured[T: BaseModel](schema: type[T], raw: str | None, usage: LLMUsage) -> T:
    """Validate the model's raw text against `schema`, or raise LLMInvalidOutput."""
    if not raw or not raw.strip():
        raise LLMInvalidOutput("the model returned no text", usage=usage)
    try:
        return schema.model_validate_json(raw)
    except ValidationError as exc:
        problems = [
            f"{'.'.join(map(str, err['loc'])) or '<root>'}: {err['type']}" for err in exc.errors()
        ]
        raise LLMInvalidOutput(
            f"output did not match {schema.__name__} ({len(problems)} problem(s))",
            usage=usage,
            problems=problems[:10],
        ) from None


def split_system(messages: Sequence[Message]) -> tuple[str | None, list[Message]]:
    """System messages joined into one instruction, plus the remaining conversation."""
    system = [m.content for m in messages if m.role == "system"]
    rest = [m for m in messages if m.role != "system"]
    return ("\n\n".join(system) if system else None), rest
