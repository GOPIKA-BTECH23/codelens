"""Turn agent findings into ONE GitHub review, safely.

Model output is untrusted. A schema-valid finding can still name a file that is not in the
PR, a line that is not in the diff (GitHub would reject the whole review with 422), or code
that is not there at all (a hallucination). So, in order:

  1. validate_findings: keep a finding only if
       * its file is one of the reviewed files,
       * `line` (and `end_line`) are RIGHT-side lines shown in the same hunk,
       * the cited range contains at least one ADDED line (we review the change, not old
         code), and
       * every line of its `evidence` appears in the cited lines (whitespace-insensitive).
     Anything else is dropped and counted; the summary reports the counts (CLAUDE.md
     decision 2026-10-17: drop, never move a comment to a "nearby" line).
  2. select_findings: remove duplicates (same file, line and category), apply the confidence
     threshold, rank by severity then confidence, and cap the number of comments.
  3. build_review: one review, event COMMENT (never APPROVE or REQUEST_CHANGES, whatever the
     model says), inline comments plus a summary that says it is AI-generated.
  4. post_review: post it once. The summary carries a hidden marker with the run ID; before
     posting we look for a review with that marker, so a retried job never posts twice.

Text from the model is also defused before it reaches GitHub: @mentions are broken (a model
must not be able to ping people) and lengths are capped.
"""

import re
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from app.ai.schemas import Finding, Severity
from app.services.diff import FileDiff, SkippedFile
from app.services.github_client import GitHubClient, ReviewComment, ReviewRequest

SEVERITY_RANK = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.INFO: 0,
}
_MENTION = re.compile(r"@(?=[A-Za-z0-9])")
# What render_file puts before the code ("    11 + "). Models often copy it into `evidence`
# along with the code; we strip it before matching. Stripping only shortens the evidence, so
# it can never make invented code match.
_RENDER_PREFIX = re.compile(r"^\s*\d+ [+\- ] ")
_DIFF_MARKER = re.compile(r"^[+-](?=\s)")
_MAX_SKIPPED_LISTED = 20
_MAX_SUMMARY_CHARS = 1500


def run_marker(run_id: uuid.UUID) -> str:
    """Invisible in the rendered review (an HTML comment), unique per run."""
    return f"<!-- codelens-run:{run_id} -->"


# --- 1. validation against the diff -------------------------------------------------------------


@dataclass
class DropCounts:
    unknown_file: int = 0
    line_not_in_diff: int = 0
    no_added_line: int = 0
    evidence_mismatch: int = 0
    duplicate: int = 0
    low_confidence: int = 0
    over_cap: int = 0

    @property
    def not_anchored(self) -> int:
        """Findings that did not point at real changed code."""
        return (
            self.unknown_file + self.line_not_in_diff + self.no_added_line + self.evidence_mismatch
        )


def _normalise(text: str) -> str:
    return " ".join(text.split())


def _evidence_line(text: str) -> str:
    return _normalise(_DIFF_MARKER.sub("", _RENDER_PREFIX.sub("", text)))


def evidence_matches(finding: Finding, file: FileDiff) -> bool:
    """Every non-blank line of the evidence occurs in the text of the cited lines."""
    right = file.right_text()
    last = finding.end_line or finding.line
    cited = _normalise(" ".join(right.get(n, "") for n in range(finding.line, last + 1)))
    wanted = [_evidence_line(part) for part in finding.evidence.splitlines() if part.strip()]
    return bool(wanted) and all(part and part in cited for part in wanted)


def validate_findings(
    findings: Iterable[Finding], files: Sequence[FileDiff], counts: DropCounts
) -> list[Finding]:
    """Keep findings that point at real changed code (see module docstring)."""
    by_path = {f.path: f for f in files}
    valid = []
    for finding in findings:
        file = by_path.get(finding.file)
        if file is None:
            counts.unknown_file += 1
            continue
        hunk = file.hunk_of(finding.line)
        if hunk is None:
            counts.line_not_in_diff += 1
            continue
        if finding.end_line is not None and finding.end_line not in hunk.right_lines:
            # A range must stay inside one hunk (GitHub's rule for multi-line comments).
            counts.line_not_in_diff += 1
            continue
        last = finding.end_line or finding.line
        if not any(n in file.added_lines for n in range(finding.line, last + 1)):
            counts.no_added_line += 1
            continue
        if not evidence_matches(finding, file):
            counts.evidence_mismatch += 1
            continue
        valid.append(finding)
    return valid


# --- 2. dedupe, threshold, rank, cap ------------------------------------------------------------


def select_findings(
    findings: Iterable[Finding], *, threshold: float, max_comments: int, counts: DropCounts
) -> list[Finding]:
    best: dict[tuple[str, int, str], Finding] = {}
    for finding in findings:
        key = (finding.file, finding.line, finding.category)
        if key in best:
            counts.duplicate += 1
            if finding.confidence <= best[key].confidence:
                continue
        best[key] = finding
    confident = [f for f in best.values() if f.confidence >= threshold]
    counts.low_confidence += len(best) - len(confident)
    ranked = sorted(
        confident,
        key=lambda f: (-SEVERITY_RANK[f.severity], -f.confidence, f.file, f.line),
    )
    counts.over_cap += max(0, len(ranked) - max_comments)
    return ranked[:max_comments]


# --- 3. rendering -------------------------------------------------------------------------------


def defuse(text: str, limit: int) -> str:
    """Model text made safe to post: no @mentions (they would notify people), capped length."""
    text = _MENTION.sub("@​", text.strip())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def render_comment(finding: Finding) -> str:
    parts = [
        f"**{finding.severity.value.capitalize()} · {finding.category}** "
        f"(confidence {finding.confidence:.2f})",
        "",
        defuse(finding.message, 2000),
    ]
    if finding.suggestion:
        suggestion = defuse(finding.suggestion, 2000).replace("```", "'''")
        parts += ["", "**Suggested fix:**", "```", suggestion, "```"]
    parts += ["", "<sub>CodeLens security review · AI-generated, may be wrong</sub>"]
    return "\n".join(parts)


def to_comment(finding: Finding) -> ReviewComment:
    if finding.end_line is None:
        return ReviewComment(path=finding.file, line=finding.line, body=render_comment(finding))
    return ReviewComment(
        path=finding.file,
        start_line=finding.line,
        start_side="RIGHT",
        line=finding.end_line,
        body=render_comment(finding),
    )


@dataclass
class ReviewSummary:
    """Everything the summary reports (also what the worker logs)."""

    files_reviewed: int
    chunks_total: int = 0
    chunks_failed: int = 0
    change_summaries: list[str] = field(default_factory=list)
    skipped: list[SkippedFile] = field(default_factory=list)
    doc_files: int = 0
    counts: DropCounts = field(default_factory=DropCounts)
    model: str | None = None
    prompt_version: str | None = None


def _severity_line(findings: Sequence[Finding]) -> str:
    if not findings:
        return "No security issues found in the reviewed changes."
    by_severity = Counter(f.severity for f in findings)
    parts = [
        f"{by_severity[s]} {s.value}" for s in sorted(by_severity, key=lambda s: -SEVERITY_RANK[s])
    ]
    noun = "comment" if len(findings) == 1 else "comments"
    return f"{len(findings)} inline {noun}: {', '.join(parts)}."


def summary_notes(s: ReviewSummary) -> list[str]:
    """The "Review notes" bullets: what was reviewed, what was dropped and why."""
    notes = [f"Reviewed {s.files_reviewed} file(s) in {s.chunks_total} part(s)."]
    if s.chunks_failed:
        notes.append(
            f"{s.chunks_failed} of {s.chunks_total} part(s) could not be reviewed "
            "(the model's answer was invalid after retries)."
        )
    c = s.counts
    if c.not_anchored:
        notes.append(
            f"{c.not_anchored} finding(s) were dropped because they did not point at a changed "
            "line of this diff or quoted code that is not there."
        )
    if c.low_confidence:
        notes.append(f"{c.low_confidence} lower-confidence finding(s) are not shown.")
    if c.over_cap:
        notes.append(f"{c.over_cap} more finding(s) are not shown (comment limit reached).")
    if s.doc_files:
        notes.append(f"{s.doc_files} documentation file(s) were not security-reviewed.")
    if s.skipped:
        listed = ", ".join(
            f"`{defuse(sk.path, 200)}` ({sk.reason})" for sk in s.skipped[:_MAX_SKIPPED_LISTED]
        )
        more = len(s.skipped) - _MAX_SKIPPED_LISTED
        notes.append(f"Skipped: {listed}" + (f" and {more} more." if more > 0 else "."))
    return notes


def stored_summary(s: ReviewSummary) -> dict[str, object]:
    """What the dashboard shows above the diff (review_runs.summary). Plain text, no markdown:
    the web app renders it as text."""
    what = defuse(" ".join(s.change_summaries), _MAX_SUMMARY_CHARS) if s.change_summaries else None
    return {"what_changed": what, "notes": summary_notes(s)}


def render_summary(run_id: uuid.UUID, findings: Sequence[Finding], s: ReviewSummary) -> str:
    lines = [run_marker(run_id), "## CodeLens security review", ""]
    if s.change_summaries:
        what = defuse(" ".join(s.change_summaries), _MAX_SUMMARY_CHARS)
        lines += [f"**What changed:** {what}", ""]
    lines += [f"**Findings:** {_severity_line(findings)}", ""]

    notes = summary_notes(s)
    lines += ["<details><summary>Review notes</summary>", ""]
    lines += [f"- {note}" for note in notes]
    lines += ["", "</details>", ""]

    by = f" (model `{s.model}`, prompt `{s.prompt_version}`)" if s.model else ""
    lines += [
        "---",
        f"_AI-generated by CodeLens{by}. It can be wrong and can miss issues: treat it as a "
        "second pair of eyes, not a verdict._",
    ]
    return "\n".join(lines)


def build_review(
    run_id: uuid.UUID, head_sha: str, findings: Sequence[Finding], summary: ReviewSummary
) -> ReviewRequest:
    return ReviewRequest(
        commit_id=head_sha,
        body=render_summary(run_id, findings, summary),
        event="COMMENT",  # fixed: the model can never approve or block a PR
        comments=[to_comment(f) for f in findings],
    )


# --- 4. posting ---------------------------------------------------------------------------------


async def find_posted_review(
    gh: GitHubClient, repo: str, number: int, run_id: uuid.UUID
) -> int | None:
    """The id of a review this run already posted, if any (looked up by the hidden marker)."""
    marker = run_marker(run_id)
    for review in await gh.list_reviews(repo, number):
        if review.body and marker in review.body:
            return review.id
    return None


async def post_review(
    gh: GitHubClient, repo: str, number: int, run_id: uuid.UUID, review: ReviewRequest
) -> int:
    """Post `review` unless this run already did; returns the review id either way."""
    existing = await find_posted_review(gh, repo, number, run_id)
    if existing is not None:
        return existing
    return await gh.create_review(repo, number, review)
