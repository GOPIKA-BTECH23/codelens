"""review_files: the GitHub-free review core shared by the worker and the eval harness."""

from collections.abc import Sequence

from app.ai.llm.base import LLMProvider, LLMUsage
from app.ai.llm.fake import FakeLLM
from app.ai.pipeline import default_pipeline
from app.ai.schemas import AgentResult, Finding
from app.services.diff import DiffChunk
from app.services.review_core import ReviewConfig, review_files
from tests.seeded import ideal_answer, load_case


async def test_single_security_agent_end_to_end_on_a_seeded_case() -> None:
    case = load_case("sql_injection")
    llm = FakeLLM([ideal_answer(case, confidence=0.9)])
    usage: list[LLMUsage] = []

    outcome = await review_files(
        case.files, default_pipeline(), llm, ReviewConfig(), usage_log=usage
    )

    assert outcome.has_code and outcome.chunks == 1
    assert [t.finding.line for t in outcome.selected] == [9, 14]
    assert {t.agent for t in outcome.raw} == {"security"}
    assert len(usage) == 1


async def test_no_reviewable_code_makes_no_llm_call() -> None:
    llm = FakeLLM([])  # any call would raise FakeScriptExhausted
    outcome = await review_files(
        load_case("docs_only").files, default_pipeline(), llm, ReviewConfig(), usage_log=[]
    )
    assert not outcome.has_code and outcome.results == [] and llm.calls == []


class TwoAgents:
    """A stand-in for a future multi-agent graph: the harness and worker only see the
    protocol, so this is all it takes to plug one in."""

    def __init__(self, by_agent: dict[str, list[Finding]]) -> None:
        self._by_agent = by_agent

    @property
    def name(self) -> str:
        return "two-agents"

    @property
    def prompt_versions(self) -> tuple[str, ...]:
        return ("a_v1", "b_v1")

    async def __call__(
        self,
        chunks: Sequence[DiffChunk],
        llm: LLMProvider,
        *,
        usage_log: list[LLMUsage],
        concurrency: int,
    ) -> list[AgentResult]:
        return [
            AgentResult(
                agent=agent,
                findings=findings,
                change_summaries=[],
                prompt_version=f"{agent}_v1",
                model=llm.model,
                chunks_total=len(chunks),
                chunks_failed=0,
            )
            for agent, findings in self._by_agent.items()
        ]


async def test_stages_keep_agent_tags_through_validation_and_selection() -> None:
    case = load_case("sql_injection")
    good, other = ideal_answer(case, confidence=0.9).findings
    low = other.model_copy(update={"confidence": 0.3})
    off_diff = good.model_copy(update={"line": 4, "evidence": "def get_user"})  # context line
    pipeline = TwoAgents({"a": [good, off_diff], "b": [low]})

    outcome = await review_files(case.files, pipeline, FakeLLM(), ReviewConfig(), usage_log=[])

    assert [(t.agent, t.finding.line) for t in outcome.raw] == [("a", 9), ("a", 4), ("b", 14)]
    assert [(t.agent, t.finding.line) for t in outcome.anchored] == [("a", 9), ("b", 14)]
    assert [(t.agent, t.finding.line) for t in outcome.selected] == [("a", 9)]
    assert outcome.counts.no_added_line == 1 and outcome.counts.low_confidence == 1
