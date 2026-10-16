"""What agents produce. These models are the contract between the LLM and our code.

`AgentOutput` is the exact shape the model must return (it is turned into the provider's
JSON Schema by app/ai/llm/json_schema.py). The field descriptions are part of the prompt: the
model reads them. Validation here is the FIRST gate; review_poster then checks every finding
against the actual diff, because a schema-valid finding can still point at a line that does
not exist.

Unknown keys in the model's answer are ignored (`extra="ignore"`): a model talked into adding
`"approve": true` changes nothing, because no code ever reads such a field.
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.ai.llm.base import LLMUsage
from app.db.models.enums import Severity

__all__ = ["AgentOutput", "AgentResult", "Finding", "Severity"]


class Finding(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    file: str = Field(
        min_length=1,
        max_length=1000,
        description="Path of the changed file exactly as shown after 'File:' in the diff.",
    )
    line: int = Field(
        ge=1,
        description="New-file line number of the problem, taken from the number column of the "
        "diff. Only numbered lines can be cited.",
    )
    end_line: int | None = Field(
        default=None,
        ge=1,
        description="Last line if the problem spans several lines, otherwise null.",
    )
    severity: Severity = Field(description="How bad it is if exploited.")
    category: str = Field(
        min_length=1,
        max_length=50,
        description="Short kebab-case class of issue, e.g. sql-injection, hardcoded-secret.",
    )
    message: str = Field(
        min_length=1,
        max_length=2000,
        description="What is wrong and why it is exploitable, in one to three sentences.",
    )
    suggestion: str | None = Field(
        default=None,
        max_length=2000,
        description="How to fix it (short prose or a code snippet), or null.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Probability from 0.0 to 1.0 that this is a real, exploitable problem.",
    )
    evidence: str = Field(
        min_length=1,
        max_length=500,
        description="The offending code copied exactly from the cited line(s), without the "
        "line number and the +/- marker.",
    )

    @field_validator("category")
    @classmethod
    def _normalise_category(cls, value: str) -> str:
        return "-".join(value.lower().replace("_", " ").split())

    @model_validator(mode="after")
    def _end_not_before_start(self) -> "Finding":
        if self.end_line is not None and self.end_line < self.line:
            raise ValueError("end_line must not be before line")
        if self.end_line == self.line:
            self.end_line = None
        return self


class AgentOutput(BaseModel):
    """The model's whole answer for one diff chunk."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    change_summary: str = Field(
        max_length=600,
        description="One or two neutral sentences on what these changes do. No verdict.",
    )
    findings: list[Finding] = Field(
        max_length=50,
        description="Security problems found in the added or changed lines. Empty if none.",
    )


class AgentResult(BaseModel):
    """What an agent returns for a whole PR (all chunks). Built by our code, not the model."""

    findings: list[Finding]
    change_summaries: list[str]
    prompt_version: str
    model: str
    chunks_total: int
    chunks_failed: int
    usage: list[LLMUsage] = Field(default_factory=list)
