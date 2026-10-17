# Evaluating the review pipeline

How CodeLens measures review quality, what the numbers mean, and the log of every recorded
experiment. **Every number in this file comes from a result file committed under
`evals/results/`, and `evals/report.py` recomputes it from that file's stored findings.**

## One command

```bash
# from the repo root; reads GROQ_API_KEY / GEMINI_API_KEY from the root .env
uv run --project apps/api python -m evals.run_evals --provider groq --split dev
```

This reviews every case in the split with the production review code
(`app/services/review_core.py`, the same function the worker calls), scores the findings and
writes three files:

- `evals/results/<UTC timestamp>-<git sha>-<prompt versions>.json` holds the raw findings of
  every case-run, usage, configuration, dataset version and the computed metrics.
- The same name with `.md` is the human-readable report.
- `evals/results/TREND.md` is one row per recorded run (fake-provider runs are left out).

Compare two runs, for example two prompt versions:

```bash
uv run --project apps/api python -m evals.compare evals/results/A.json evals/results/B.json
```

A smoke run needs no key and no money. It uses the FakeLLM, which reviews nothing, so its
recall is 0 by design, and writes to the gitignored `evals/results/smoke/`:

```bash
uv run --project apps/api python -m evals.run_evals --provider fake --split all --include-test
```

## Matching rule

A reported finding **matches** a labelled issue when all three hold:

1. **Same file**: the paths are exactly equal.
2. **Close enough**: the finding's lines `[line, end_line]` are within **2 lines** of the
   issue's lines `[line, end_line]`. Overlapping ranges have distance 0.
3. **Compatible category**: the finding's category is the label's family name, one of its
   aliases, or an umbrella term that covers it (`evals/categories.json`, for example `sqli`
   and `injection` both match `sql-injection`).

Pairing:

- Each issue is found at most once, and each finding finds at most one issue. The harness
  takes a **maximum matching** (augmenting paths). A greedy "closest first" pairing can
  under-count when one finding sits between two issues.
- An unpaired finding that matches an already-found issue is a **duplicate**. It counts as a
  **false positive**, because a second comment on the same problem is noise for the author.
- A finding that matches an **optional** label (something reasonable to report, not
  required) is neutral: it counts neither as a hit nor as a false positive.
- Any other unpaired finding is a false positive. An unpaired issue is a false negative.
- Severity is **not** part of the match. It is reported as *severity agreement*: the share of
  hits whose severity is at least the label's `min_severity`.

Every report also shows the headline stage at **tolerance 0** ("strict"), so you can see how
much the tolerance contributes.

### Limitations of the rule (read before quoting a number)

- **The tolerance is a judgement call.** Two lines lets a finding cite `conn.execute(query)`
  instead of the `query = ... + username` line above it. That is the same bug, but it also
  means a finding about *another* problem two lines away, with a compatible category, would
  count as a hit. The strict row bounds this effect.
- **The category table is hand-made and small.** A real hit under a name the table does not
  know (say `sql-concatenation`) counts as both a false positive and a miss. Reports list
  every unknown category so such cases are visible. Adding aliases makes matching more
  lenient, so each one is reviewed by hand.
- **Labels are one person's view.** A finding that is correct but unlabelled counts as a
  false positive. Optional labels soften this only for the cases someone thought of.
- **It does not judge the message.** A finding on the right line with the right category but
  a wrong or useless explanation still counts as a hit. Message quality would need human
  ratings or a calibrated LLM judge. Neither is built yet.
- **File-level or PR-level problems** (for example "no authentication on this whole router")
  can only be labelled at a line. The model may cite a different line for them.

## Metrics

All counts are **pooled over every case-run** (micro-average). With one or two issues per
case, per-case averages would swing wildly.

| Metric | Definition |
|---|---|
| precision | TP / (TP + FP). Undefined (shown as n/a) when nothing was reported |
| recall | TP / (TP + FN) |
| F1 | 2TP / (2TP + FP + FN) |
| 95% interval | Wilson score interval. With repeats it uses the per-repeat denominator: repeats average out the model's randomness, but the dataset still has only this many issues |
| FP per clean case | false positives on clean cases ÷ clean case-runs, also shown for clean cases *with code* (a docs-only PR never reaches the model, so it scores 0 by construction) |
| severity agreement | share of hits with severity ≥ the label's `min_severity` |
| invalid-output rate | model answers that failed schema validation ÷ model calls that returned |
| chunk failure rate | diff chunks still invalid after all retries ÷ chunks |
| cost per case | **estimated** at list price (`app/ai/llm/pricing.py`). The free tiers bill nothing |
| latency per case | model time (sum of call latencies) and wall time (includes client-side rate-limit waits) |

Findings are scored at three **stages** of the pipeline:

- `raw`: everything the model returned (schema-valid).
- `anchored`: what survived `validate_findings` (it points at a changed line of the diff and
  its evidence is really there).
- `posted`: what would be posted (after dedupe, the confidence threshold of 0.6, the severity
  threshold and the comment cap). **This is the headline**, because it is what a PR author
  sees.

Comparing `raw` with `posted` in one run is the confidence-filter ablation, and it costs no
extra model calls.

Per-category, per-agent, per-split and per-case breakdowns are in every report.

## Sample size

Every report starts with the sample size. Today's dataset is **7 cases: 5 with planted issues
(8 required, 1 optional) and 2 clean, one of them with code**. With 8 issues, one more or
one fewer hit moves recall by 12.5 points, and the 95% interval for 8 of 8 still reaches
down to 68%. These numbers can show that something is badly broken. They cannot show that
one prompt is slightly better than another.

## Runs: cost, rate limits, resuming

- Cases run one at a time, chunks one at a time, under a client-side requests-per-minute
  limit (`--rpm`, default 10), because of free-tier rate limits.
- Every finished case-run is appended to `evals/results/.partial/<run-id>.jsonl`
  (gitignored). If a run stops (rate limit after retries, outage, budget cap, Ctrl+C), resume
  it with `--resume <run-id>`. Finished cases are not re-run. Resuming refuses if the git
  commit, model, prompts, pipeline, dataset or matching configuration changed.
- `--max-cost-usd` (default 0.05) is checked before each case-run, so a run can exceed it by
  at most one case's cost.
- The **test split** runs only with `--include-test`, and the result records that it was
  included (see `evals/dataset/README.md`).

## Experiment log

Real numbers only, each with its model, date, dataset version and result file.

| # | Experiment | Status |
|---|---|---|
| 1 | Baseline: `security_v1`, single agent, no tools (raw / anchored / posted stages) | not run yet |
| 2 | Multi-agent (security + style + test-gap, LangGraph) | not built yet |
| 3 | Multi-agent + analyzer grounding (Bandit/Ruff/Semgrep) | not built yet |
| 4 | + confidence filter | covered by the `raw` vs `posted` stages of each run |
