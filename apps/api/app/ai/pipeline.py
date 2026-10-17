"""What "the review pipeline" is, as far as its callers (the worker, the eval harness) know.

A pipeline takes diff chunks and returns one AgentResult per agent that ran. Today there is
one implementation, the single security agent. The LangGraph fan-out (security + style +
test-gap, later milestone) will implement the same protocol, so the worker and the eval
harness can run it without changing: they only ever call `pipeline(chunks, llm, ...)`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from app.ai.agents.security import PROMPT_NAME, PROMPT_VERSION, security_agent
from app.ai.llm.base import LLMProvider, LLMUsage
from app.ai.prompts import Prompt, load_prompt
from app.ai.schemas import AgentResult
from app.services.diff import DiffChunk


class ReviewAgents(Protocol):
    @property
    def name(self) -> str:
        """Stable identifier recorded with eval results, e.g. `security-single`."""
        ...

    @property
    def prompt_versions(self) -> tuple[str, ...]:
        """Every prompt the pipeline uses, e.g. `("security_v1",)`."""
        ...

    async def __call__(
        self,
        chunks: Sequence[DiffChunk],
        llm: LLMProvider,
        *,
        usage_log: list[LLMUsage],
        concurrency: int,
    ) -> list[AgentResult]:
        """Review the chunks. Appends every call's usage to `usage_log` as it happens (so the
        caller can record cost even if this raises) and raises the provider's transient
        errors."""
        ...


@dataclass(frozen=True)
class SingleSecurityAgent:
    """The MVP pipeline: the security agent alone, no tools."""

    prompt: Prompt

    @property
    def name(self) -> str:
        return "security-single"

    @property
    def prompt_versions(self) -> tuple[str, ...]:
        return (self.prompt.id,)

    async def __call__(
        self,
        chunks: Sequence[DiffChunk],
        llm: LLMProvider,
        *,
        usage_log: list[LLMUsage],
        concurrency: int,
    ) -> list[AgentResult]:
        result = await security_agent(
            chunks, llm, usage_log=usage_log, concurrency=concurrency, prompt=self.prompt
        )
        return [result]


def default_pipeline() -> SingleSecurityAgent:
    return SingleSecurityAgent(load_prompt(PROMPT_NAME, PROMPT_VERSION))
