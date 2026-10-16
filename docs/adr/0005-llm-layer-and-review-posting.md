# ADR 0005: LLM layer, validating model output, and posting reviews (M5)

Status: accepted, 2026-10-17

## Context

M5 turns the stub `review_pr` into a real review: an LLM reads the diff and the worker posts comments on the PR. Three things make this harder than an ordinary API call:

- **Model output is untrusted.** It can be malformed, it can point at lines that do not exist, and the diff it reads was written by whoever opened the PR (prompt injection).
- **GitHub is unforgiving.** One inline comment on a line outside the diff makes the whole review fail with 422.
- **Delivery is at-least-once** (ADR 0004). A job can run twice, so posting must not happen twice.

## Decisions

### 1. One provider interface, three implementations

Agents call `LLMProvider` (`app/ai/llm/base.py`) and never a vendor SDK. Every call returns `LLMUsage` (model, tokens in/out, latency, estimated cost), and vendor errors become four types: `LLMRateLimited` and `LLMUnavailable` (transient: the job queue retries), `LLMRequestRejected` (permanent: bad key, denied project), and `LLMInvalidOutput` (the agent retries).

| | Groq (default) | Gemini (alternative) | FakeLLM |
|---|---|---|---|
| Model | `openai/gpt-oss-120b` | `gemini-3.8-flash` | `fake-llm` |
| API | OpenAI-compatible chat completions | Interactions API (`/v1beta/interactions`) | in memory, scripted |
| Structured output | `json_schema` with `strict: true` (constrained decoding) | `response_format` with a JSON schema | parses the scripted text |
| Usage | `prompt_tokens`, `completion_tokens` (includes reasoning) | `total_input_tokens`, `total_output_tokens` + `total_thought_tokens` | estimated from text length |

Both model IDs were confirmed in the providers' live model lists on 2026-10-17 before being pinned.

**Gemini uses the Interactions API with `store=False`.** Google recommends it for new projects and calls `generateContent` legacy. It also *stores every interaction by default* (55 days paid, 1 day free), so every request sends `store: false`, and a contract test asserts it for structured, text and streaming calls. Two quirks are pinned by tests: the Interactions error classes live in a private SDK module, and its `HttpRetryOptions.attempts` behaves as a retry count with a minimum of 1.

**One strict JSON Schema for both providers.** Pydantic's schema is cleaned (`$ref`s inlined, every field required with nullable optionals, `additionalProperties: false`, value constraints removed). Pydantic then re-validates every answer against the full model, constraints included.

**Cost is an estimate at list price** from a dated table (`pricing.py`) that supports announced price changes (`effective_from`). An unpriced model stops the worker at startup instead of recording $0.

### 2. Model output passes three gates before reaching GitHub

1. **Schema** (`AgentOutput`, Pydantic). Invalid → the agent retries up to 2 times, telling the model *which fields* were wrong (never echoing its output), then marks that chunk failed and continues with the others.
2. **The diff** (`review_poster.validate_findings`). A finding is kept only if its file is in the PR, its lines are RIGHT-side lines of one hunk (GitHub's rule), the range contains an added line (we review the change, not old code), and its `evidence` appears on the cited lines. Otherwise it is **dropped and counted** in the summary, never moved to a "nearby" line, where it would point at the wrong code.
3. **Policy** (`select_findings`). Dedupe by (file, line, category), confidence threshold, rank by severity then confidence, cap the comment count.

The evidence check strips the diff rendering prefix (`"11 + "`) that models copy along with the code. That was found with the real model: before the fix, 14 of 20 correct findings were dropped. Stripping only shortens the evidence, so it cannot make invented code match.

### 3. Prompt-injection defences are structural, not just instructions

- The diff sits between marker lines that contain a **random per-call token**, so text in the PR cannot fake the end of the data block. The system prompt says the block is data and lists typical injection phrases to ignore.
- The model's power is limited by code, not by asking nicely: the review event is hard-coded to `COMMENT` (never `APPROVE`), unknown keys in its answer are ignored, every finding must match the diff, and `@mentions` in model text are defused so it cannot ping people.
- What tests can and cannot prove: the FakeLLM contract tests show a "fooled" model still cannot approve, post unvalidated comments or ping anyone. Whether a real model still *reports* the vulnerability is behaviour, checked manually (and by M13 evals).

### 4. Posting exactly once

The review body starts with an invisible `<!-- codelens-run:<run id> -->` marker. Before calling the model, the pipeline lists the PR's reviews. If one carries this run's marker, the previous attempt died after posting, so it only finishes the bookkeeping. Findings are saved *before* posting, so the stored rows always match what GitHub shows. Right before posting it re-checks that the run is still `running` (decision 16).

### 5. Usage is added, never overwritten

Every LLM call's usage is appended to a log the moment it returns and ADDED to the run's totals (`runs.add_usage`), even when the job then fails and is retried. Overwriting would hide real spend from the daily cost cap.

### 6. PRs with nothing to review

If the noise filter leaves no code (docs, lockfiles, generated or binary files only), the model is not called. `REVIEW_NO_CODE_BEHAVIOR=skip` (default) marks the run `skipped`; `comment` posts a short summary.

## Consequences

- Swapping or adding a provider touches one file plus the price table.
- A finding that is real but cites the wrong line is lost (and counted). We accept that rather than risk posting comments on the wrong code; the counts make the loss visible.
- The worker now needs `GITHUB_APP_PRIVATE_KEY` and the chosen LLM key, and refuses to start without them (also in in-API mode).
- The Gemini path depends on a private SDK import; upgrading `google-genai` must re-run the contract tests.
