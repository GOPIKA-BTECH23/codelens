# M13: The evaluation harness

## What was built

- **A GitHub-free review core** (`app/services/review_core.py`). `review_files(files,
  pipeline, llm, config)` does filter → chunk → agents → validate against the diff →
  dedupe/threshold/cap. The worker and the eval harness both call it, so the evals measure
  the production code, not a copy.
  - The `pipeline` argument is a `ReviewAgents` protocol. Today the only implementation is
    `SingleSecurityAgent`; a future LangGraph multi-agent graph plugs in without touching the
    harness.
- **Invalid-output counters** on every agent result (`calls`, `invalid_outputs`).
- **A labelled dataset** in `evals/dataset/`: 16 synthetic cases, 12 required issues, a
  validator, a README with provenance, and an approved dev/test split (9 dev, 7 test).
- **A matching rule and metrics** (`evals/harness/`):
  - precision, recall and F1 pooled over all case-runs, with 95% Wilson intervals;
  - false positives per clean case;
  - three pipeline stages (raw / anchored / posted) and a strict ±0 row;
  - per-category, per-agent, per-split and per-case breakdowns;
  - invalid-output rate, cost and latency.
- **A resumable, cost-capped runner** (`python -m evals.run_evals`) plus self-contained result
  files, markdown reports, a trend table and a run-vs-run diff (`evals.compare`).
- **A manual-only GitHub Actions workflow** (`evals.yml`) with a cost cap and an uploaded
  report.
- **Two recorded baselines** for `security_v1` on Groq `gpt-oss-120b`, both in
  `docs/evals.md`: dev F1 97.7%, held-out test F1 86.7%.

## The flow, in plain words

1. `run_evals` loads the dataset and checks it is valid. It picks the split (test only with
   `--include-test`), and writes a header line to a checkpoint file: git sha, model, prompt
   versions, dataset hash, matching settings.
2. For each repeat, for each case: parse the case's `.diff` into `FileDiff`s and call
   `review_files` with the real provider. One case and one chunk at a time, at most 3
   requests a minute.
3. Each finished case-run is appended to the checkpoint. It holds every finding with the
   furthest stage it reached, plus tokens, estimated cost, model time and wall time.
4. A rate limit or outage gets two backoff retries, then the run stops with a `--resume`
   command. The budget cap is checked before each case.
5. At the end, the matching rule pairs findings with labels and the metrics are computed.
   The result file stores the findings, a snapshot of the labels and of `categories.json`,
   and the metrics.
6. `python -m evals.report --check` (also run by a test) recomputes every committed result
   from its own findings and snapshots. It fails if any stored number, report or the trend
   table has drifted.

## Why evals matter for LLM apps

A normal function is tested with fixed inputs and exact expected outputs. An LLM's output is
probabilistic, changes when the provider updates a model, and changes in hard-to-predict ways
when you edit a prompt. Unit tests with the FakeLLM prove *our* code handles any answer
correctly. They say nothing about whether the *model's* answers are any good.

An eval answers "how good is the review, in numbers", on a fixed dataset, so you can tell
whether a prompt or model change helped. Without it, prompt work is guesswork: you fix the
example in front of you and silently break three others.

It also keeps you honest in interviews. Instead of "it finds SQL injection", you can say
"95.5% precision and 100% recall on 9 dev cases; 86.7% on 7 held-out cases; here are the
intervals and the one case it struggled with".

## Precision vs recall here

- **Precision** = of the comments we post, how many point at a real labelled problem. A
  false positive is a wrong comment on someone's PR. Too many and developers learn to ignore
  the bot, which is the death of a review tool.
- **Recall** = of the real problems, how many we report. A miss is a vulnerability that gets
  merged while the bot looked at it. That is worse per incident but invisible day to day.
- **They trade off.** The confidence threshold (0.6) and the comment cap push towards
  precision; a looser prompt pushes towards recall. That is why the report shows the `raw`
  stage (before filtering) next to `posted` (what is shown): the gap between them is what the
  filter costs or saves.
  - In our runs the gap was zero on both splits: every finding was anchored and above 0.6.
  - So we cannot yet say whether the filter helps. These diffs did not exercise it.
- **False positives per clean case** answers a separate question precision cannot: "on a PR
  with nothing wrong, does the bot stay quiet?" Precision is undefined when nothing is
  reported, so the clean cases need their own measure.

## Overfitting prompts, and how the held-out split helps

If you change a prompt, run the evals, and keep the change when the number goes up, the
prompt slowly learns the dataset. The phrasing ends up tuned to *these* 16 diffs, not to
security review in general, so the number becomes optimistic. With a small dataset this
happens fast: one lucky hit moves recall by 14 points.

The held-out **test** split protects against that:

- Prompt decisions are made on **dev** only.
- **Test** runs only with an explicit `--include-test`, the result file records that it
  did, and it is run **once** per decided change, to report, never to choose.

Once you look at test numbers and react to them, the split is "spent". It now measures your
choices, not the model, which is why `docs/evals.md` says the test split is spent for
`security_v1`.

Our run shows why the split is worth having. Test F1 (86.7%) came out below dev (97.7%).
All of the gap comes from one case, and the intervals overlap, so it is not proof of a real
drop. But if dev had been the only number, nobody would have looked at `missing_authorization`.

**Honest caveats:**

- 3 of the 7 test cases were visible when `security_v1` was written in M5.
- The 9 newer labels were model-drafted and model-reviewed, not independently human-labelled.

## Why these decisions

- **One review core for worker and evals.** An eval of a re-implementation measures the
  re-implementation. Sharing the function means a change in production shows up in the evals.
- **The pipeline as a parameter.** The multi-agent graph can be compared against this
  baseline through the same harness and the same metrics, so the comparison is fair.
- **A maximum matching instead of greedy pairing.** Greedy "closest issue first" can let one
  finding take the issue a second finding needed, under-counting hits. Augmenting paths
  find the largest set of pairs, independent of order.
- **Duplicates count as false positives.** A second comment on the same bug is noise for the
  PR author.
- **Optional labels are neutral.** Some remarks are fair but not required, such as flagging
  the prompt-injection comment itself. Punishing them would teach the prompt to stay silent.
- **Pooled (micro) metrics with Wilson intervals.** With one or two issues per case,
  per-case averages swing wildly. Wilson behaves well near 0% and 100% and with small n,
  where the normal approximation breaks (it can give intervals above 100%).
- **Self-contained result files** (findings, label and category snapshots). Numbers stay
  reproducible after the dataset changes. "Every number in the docs comes from a committed
  file" is enforced by a test, not by trust.
- **Resumable, low concurrency, cost cap.** Free tiers limit tokens per minute, so the run is
  slow on purpose and must survive a 429 without paying for finished cases again.
- **Dirty-tree refusal.** A result whose git sha does not describe the code that produced it
  cannot be reproduced.
- **Manual-only workflow.** Every run calls a real API, so nothing should spend quota on a
  timer.

## Five likely interview questions

1. **How do you know your LLM reviewer works?**
   I run it on a labelled dataset and match its findings to the labels with a documented
   rule. I report precision, recall, F1 with 95% Wilson intervals, false positives on clean
   PRs, invalid-output rate, cost and latency. On 9 dev cases it scored 97.7% F1; on 7
   held-out cases 86.7%. I always give the sample size, because 5-7 issues per split gives
   wide intervals.
2. **Why is the test score lower than dev?**
   All of the gap is one case, a missing ownership check. The model found the problem in 3
   of 3 repeats but cited the route decorator, 3 lines from the label, in 2 of them. That is
   partly a model localisation weakness and partly my line-level matching for a
   function-level problem. The intervals overlap, so I would not claim held-out performance
   is reliably lower.
3. **What does your matching rule count as a hit, and what are its weaknesses?**
   Same file, line within ±2, compatible category from a small alias table, as a maximum
   matching. Weaknesses:
   - the tolerance is a judgement call (the report shows a strict row);
   - unknown category names count as misses;
   - correct but unlabelled findings count as false positives;
   - message quality is not judged.
4. **How do you avoid overfitting your prompt to the eval set?**
   A held-out test split that only runs with an explicit flag, is recorded in the result,
   and is used once to report, never to choose. I also say where it is weaker: 3 test cases
   were seen when the prompt was written, and the labels were not independently
   human-labelled.
5. **Your confidence filter had no effect. Why keep it?**
   On these small, clean diffs the model never returned a low-confidence or unanchored
   finding, so the data could not exercise the filter. That is a property of the dataset,
   not proof the filter is useless. The next step is real PRs, where hallucinated lines and
   guesses are common, and the `raw` vs `posted` stages will show whether it earns its place.

## Things to try breaking

- Edit a number in a committed `evals/results/*.json` and run `python -m evals.report
  --check`. It should name the file whose stored metrics differ from a recomputation.
- Move a label in a `seeded_prs/*.expected.json` onto an unchanged context line and run the
  validator. Then put a case in both splits.
- Start a smoke run, press Ctrl+C after a few cases, and `--resume` it. Then try to resume
  with `--tolerance 0` and watch it refuse.
- Run `--split test` without `--include-test`.
- Make an uncommitted change in `apps/api/` and try a real run (it refuses), then with
  `--allow-dirty` (it goes to the gitignored `smoke/` folder).
- In `tests/evals/test_matching.py`, replace the maximum matching with "closest issue first"
  and see which test fails.
- Run `python -m evals.compare` on the two committed results and read the warnings. The
  case selections differ (dev vs test), so it should say the numbers are not directly
  comparable.

## Quiz (answer in your own words)

1. Two findings in the same file both have category `sql-injection`: one at line 10 with
   confidence 0.9, one at line 11 with confidence 0.7. There is one labelled issue at line
   9. What are TP, FP and FN, which finding is the hit, and why?
2. Why does the report show false positives per clean case separately from precision? What
   would precision say about a run that reported nothing on a clean PR?
3. After seeing the test result you want to edit `security_v1` so it cites the line of the
   missing check instead of the decorator. What exactly are you allowed to do with the dev
   and test splits from here, and why?
