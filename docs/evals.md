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

Every report starts with the sample size. The dataset (version `2d0ef7ebbf7a`) has **16
cases: 9 with planted issues (12 required, 1 optional) and 7 clean (6 with code, 1
documentation-only)**, split into:

- **dev:** 9 cases, 7 required issues;
- **test (held out):** 7 cases, 5 required issues.

With 7 issues, one hit more or less moves recall by 14.3 points; with 5, by 20 points. These
numbers can show that something is badly broken. They cannot show that one prompt is slightly
better than another.

## Runs: cost, rate limits, resuming

- Cases run one at a time, chunks one at a time, under a client-side requests-per-minute
  limit (`--rpm`, default 3). Groq lists 8K tokens/minute for `gpt-oss-120b` and a review
  call is about 2-3K tokens, so tokens, not requests, are the binding limit.
- Every finished case-run is appended to `evals/results/.partial/<run-id>.jsonl`
  (gitignored). If a run stops (rate limit after retries, outage, budget cap, Ctrl+C), resume
  it with `--resume <run-id>`. Finished cases are not re-run. Resuming refuses if the git
  commit, model, prompts, pipeline, dataset or matching configuration changed.
- `--max-cost-usd` (default 0.05) is checked before each case-run, so a run can exceed it by
  at most one case's cost.
- The **test split** runs only with `--include-test`, and the result records that it was
  included (see `evals/dataset/README.md`).

## Dataset provenance (read before quoting a number)

- **All 16 cases are synthetic**: small made-up modules turned into real `git diff` output.
- **7 cases** come from M5 (commit `4137554`). They were written together with the first
  security agent by the project author, with AI assistance, and checked by the M5 tests.
- **9 cases** were added in M13:
  - The diffs and draft labels were written by a model (Claude Opus 5.5 via Claude Code).
  - The labels were reviewed **in chat by a second model session** (Claude Sonnet 5.5).
  - The repository owner **accepted** them on the strength of that review on 2026-10-17.
  - **No human labelled them independently.** The ground truth is therefore one model's
    drafts checked by another model, so it can share both models' blind spots.
  - One draft, `insecure_default`, was dropped: its required and optional labels sat one
    line apart in the same category, which the ±2-line tolerance cannot tell apart.
- **The dev cases were visible while the labels were reviewed.** The dev numbers are
  in-sample in that sense.
- **`security_v1` was written in M5 by someone who had seen the 7 original cases.** Three of
  them (`clean_change`, `prompt_injection`, `unsafe_deserialization`) are in the test split.
  The split is fully held out for `security_v1` only on its 4 M13 cases:
  `clean_safe_download`, `clean_sha256_cache_key`, `missing_authorization` and
  `tls_verify_disabled`.
- `categories.json` gained aliases during the candidate review, **before any model-backed
  run**. Nothing in the dataset, the category table, the prompt or the thresholds was
  changed after the results below were seen.

**Correction.** The message of commit `7a64b7e` says the nine cases "were reviewed and
corrected by hand". That is inaccurate. The accurate provenance is the one above, and the
`labelled_by` field of each label file states it the same way. Git history is not
rewritten; this note is the correction.

## Experiment log

Real numbers only. Every number below is in a committed result file under `evals/results/`,
and `python -m evals.report --check` recomputes it from that file's stored findings.

### 1. Baseline: `security_v1`, single agent, no tools

Common setup for both runs:

| | |
|---|---|
| Model | Groq `openai/gpt-oss-120b` (reasoning effort low), default sampling |
| Pipeline | `security-single`, prompt `security_v1` |
| Review policy | confidence ≥ 0.6, at most 15 comments, 1 chunk at a time |
| Dataset | `seeded_prs` version `2d0ef7ebbf7a`, categories `85d2da1a5342` |
| Matching | file + line within ±2 + compatible category, maximum matching |
| Repeats | 3 per case |
| Code | dev at `42f517c`; test at `0091fbe`, which differs from `42f517c` only by the dev result files |

The headline is the **posted** stage (what a PR author would see). Intervals are 95% Wilson
intervals.

| | Dev | Held-out test |
|---|---|---|
| Date (UTC) | 2026-10-17 12:52–12:59 | 2026-10-17 13:04–13:11 |
| Result file | `20261017T125250Z-42f517c-security_v1.json` | `20261017T130459Z-0091fbe-security_v1.json` |
| Sample | 9 cases (5 with issues, 4 clean, 3 of them with code), 7 required issues, 0 optional; 27 case-runs | 7 cases (4 with issues, 3 clean, all with code), 5 required issues, 1 optional; 21 case-runs |
| TP / FP / FN | 21 / 1 / 0 | 13 / 2 / 2 |
| Precision | **95.5%** [60.0–99.7] | **86.7%** [43.2–98.2] |
| Recall | **100%** [64.6–100] | **86.7%** [43.2–98.2] |
| F1 | **97.7%** | **86.7%** |
| F1 per repeat | 100%, 93.3%, 100% | 80%, 80%, 100% |
| FP per clean case | 0.083 (1 over 12 clean case-runs); 0.111 with code (1 over 9) | 0 (0 over 9) |
| Severity agreement on hits | 100% | 92.3% (12 of 13) |
| Invalid-output rate | 0% (0 of 24 calls) | 0% (0 of 21 calls) |
| Chunks failed after retries | 0 of 24 | 0 of 21 |
| Cost per case (estimated, list price) | $0.000385 (total $0.010398) | $0.000426 (total $0.008953) |
| Latency per case | 15.7 s wall, 1.05 s model | 17.8 s wall, 1.72 s model |

**What this shows, plainly:**

- **Test F1 is 86.7%, against 97.7% on dev.**
- **The whole test shortfall comes from one case, `missing_authorization`.** Both test false
  positives and both misses are there; every other test case was scored perfectly in all
  three repeats.
  - The model reported the missing authorization check in **3 of 3** repeats.
  - It cited a line inside the ±2 tolerance of the label (lines 24–25, the lookup and the
    delete) in **only 1 of 3**: line 23, `def delete_project`.
  - In the other two it cited line 21, the `@app.route(..., methods=["DELETE"])` decorator,
    3 lines away. Under the rule each of those counts as a false positive *and* a miss.
  - The one hit had severity `medium` against a labelled minimum of `high`, which is the
    only severity disagreement.
- **The intervals overlap almost entirely** (recall [64.6–100] on dev vs [43.2–98.2] on
  test). This does **not** show that held-out performance is reliably lower than dev. It
  shows one weakness, localising a function-level problem to the labelled line, on one
  case.
- **The only dev error** was one `command-injection` finding on `clean_subprocess_args`
  (repeat 2, confidence 0.65, severity medium): a file name starting with `-` could be read
  as a `pdftoppm` option.
- **The test split is now spent for `security_v1`.** It will not be used to choose between
  prompts. A future prompt is chosen on dev, then run on test once to report the result.

### 2. Ablations inside each run

| Stage | Dev TP / FP / FN | Dev F1 | Test TP / FP / FN | Test F1 |
|---|---|---|---|---|
| raw (schema-valid model output) | 21 / 1 / 0 | 97.7% | 13 / 2 / 2 | 86.7% |
| anchored (points at changed code, evidence real) | 21 / 1 / 0 | 97.7% | 13 / 2 / 2 | 86.7% |
| posted (dedupe, confidence ≥ 0.6, cap) | 21 / 1 / 0 | 97.7% | 13 / 2 / 2 | 86.7% |
| posted, strict (±0 lines) | 21 / 1 / 0 | 97.7% | 12 / 3 / 3 | 80.0% |

**The ablation showed no effect on either split.** Every finding the model returned pointed
at a changed line with real evidence and had confidence ≥ 0.6, so diff validation and the
confidence filter removed nothing. That does not make them useless; these small, clean
diffs did not exercise them. The ±2 tolerance mattered once: the `missing_authorization` hit
on line 23 is a miss under the strict rule.

### Not run yet

| Experiment | Status |
|---|---|
| Multi-agent (security + style + test-gap, LangGraph) | not built yet |
| Multi-agent + analyzer grounding (Bandit/Ruff/Semgrep) | not built yet |

## Limits

- **Tiny, synthetic dataset.** 16 cases and 12 required issues. One hit moves recall by
  14-20 points, and the intervals are 35-55 points wide. The diffs are small single-file
  changes, far easier than real PRs.
- **The ground truth is not independent.** The 9 M13 labels are model-drafted and
  model-reviewed. The 7 M5 cases were written by the same author as the prompt. A label
  mistake both models share would not be caught.
- **Dev is in-sample, test is only partly held out.** The dev cases were visible during the
  label review. 3 of the 7 test cases were visible when `security_v1` was written.
- **One model, one provider, one prompt, default sampling.** Three repeats capture sampling
  noise, not variation across models or prompts.
- **The intervals assume independent issues.** Issues cluster within cases (two per case in
  some), so the true uncertainty is larger than the Wilson intervals show.
- **Line-level matching for function-level problems.** A missing check belongs to a whole
  function, but the label is one range. That is exactly what happened on
  `missing_authorization`.
- **Hand-made category table.** It is lenient where an alias exists and strict where none
  does.
- **No judgement of message quality.** A hit with a wrong explanation still counts.
- **Cost is an estimate at list price** (the free tier billed nothing). **Wall latency is
  mostly our own rate limiter** (3 requests a minute), not the model.

## What I would do next

1. **Multi-agent comparison through the same harness.** The harness calls a `ReviewAgents`
   pipeline, so a LangGraph fan-out (security + style + test-gap) can be scored on dev
   without changing the harness. Then compare it with this baseline using
   `python -m evals.compare` and look at per-agent precision.
2. **A bigger, hand-labelled set.**
   - Real fix commits from public projects with compatible licenses (for example
     CVE-fixing diffs), labelled independently by at least two people.
   - Report inter-annotator agreement, and aim for enough issues that the intervals are
     narrow enough to compare prompts.
   - Add multi-file PRs so chunking and validation are exercised.
3. **Function-level label ranges.** Let a label cover the whole function or declaration it is
   about (for example lines 21-28 for `delete_project`, or a symbol name), and match a
   finding that overlaps that range. Keep line-level labels for line-level bugs such as a
   hardcoded secret.
