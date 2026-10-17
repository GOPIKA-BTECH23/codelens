# CodeLens eval dataset

The pull requests the eval harness (`evals/run_evals.py`) scores the review pipeline on.

| Directory / file | What it is | Scored? |
|---|---|---|
| `seeded_prs/` | accepted cases: a diff plus human-accepted labels | yes |
| `splits.json` | which accepted case is in `dev` and which in `test` | |
| `candidates/` | model-drafted cases waiting for human review | **no** |

Validate before every commit that touches the dataset:

```bash
uv run --project apps/api python -m evals.validate_dataset               # seeded_prs/ + splits
uv run --project apps/api python -m evals.validate_dataset --candidates  # candidates/
```

## Size

7 accepted cases: 5 with planted issues (8 required issues, 1 optional) and 2 clean (one with
code, one documentation-only). That is tiny. Every report prints the sample size, and
`docs/evals.md` explains what such small numbers can and cannot tell you.

## Sources and licenses

- **All cases are synthetic.** The diffs are small, made-up modules written for this repo and
  turned into real `git diff` output (so hunk headers and line counts are exact). No code was
  copied from other projects, so there is no third-party license to track. The cases are
  covered by this repository's MIT license (`/LICENSE`).
- Every "secret" in them is invented and chosen not to match a real provider's key format.
- The 7 accepted cases were added in M5 (commit `4137554`) together with the first security
  agent. The project is built with an AI coding assistant, so treat them as author-curated,
  AI-assisted cases. `labelled_by` in each label file records this.
- Cases in `candidates/` were drafted by a model (Claude, via Claude Code) in M13. Their
  labels are drafts (`"label_status": "draft"`) until a human has reviewed every one.

## How labels are made

1. Write the "before" and "after" versions of a small file, plant (or deliberately avoid) a
   problem, and generate the diff with git.
2. For each problem a competent security reviewer would report, add an entry to
   `expected_findings`:
   - `file` and `line`: the **added** line where the problem is (the anchor). `end_line` is
     optional, for a problem that spans several lines.
   - `category`: a family name from `evals/categories.json`.
   - `min_severity`: the lowest severity a good review would give it. Severity is reported
     as "severity agreement" and is **not** part of the match.
   - `note`: one line on why it is a problem.
3. Problems a reviewer *may* reasonably report, without being wrong either way, go in
   `optional_findings`. Matching one is neutral: neither a hit nor a false positive.
4. `clean: true` means a good review reports nothing (`expected_findings` is empty).
   `has_code_to_review: false` means the production noise filter drops every file (for
   example, docs only), so the model is never called.
5. A human reviews every label, sets `"label_status": "accepted"` and updates `labelled_by`.
   Only then does the case move to `seeded_prs/` and get a split.

The validator enforces the mechanical parts: every labelled line is an added line of that
file, ranges stay inside one hunk, categories exist, `clean` and `has_code_to_review` agree
with the labels and with the noise filter, and every accepted case is in exactly one split.
It cannot check whether a label is *right*. Only the human review does that.

## File format (`<case>.expected.json`, schema_version 2)

```json
{
  "schema_version": 2,
  "case": "sql_injection",
  "description": "What the PR does and what is planted.",
  "source": "synthetic",
  "label_status": "accepted",
  "labelled_by": "who wrote and who reviewed the labels",
  "pr": {"title": "Add user lookup and search helpers", "body": ""},
  "clean": false,
  "has_code_to_review": true,
  "expected_findings": [
    {"file": "app/users.py", "line": 9, "category": "sql-injection", "min_severity": "high"}
  ],
  "optional_findings": []
}
```

`pr` metadata is recorded for realism and for future agents. The security agent reads only
the diff today.

## The held-out split

`splits.json` puts every accepted case in `dev` or `test`:

- `dev` may be looked at while changing a prompt or the pipeline.
- `test` is held out. `run_evals.py` runs it only with `--include-test`, the result file
  records that it did, and a prompt change is never chosen because of its test numbers.
  Test numbers are reported once a change has been decided on dev.

**Honest caveat:** `security_v1` was written by someone who had seen all 7 original cases, so
for that prompt the split is held out only from M13 onwards. The split is `provisional` until
the candidate cases have been reviewed and added; it will be re-proposed then.

## Changing the dataset

Any change to a case file or `splits.json` changes the dataset version (a content hash
recorded in every result). `compare.py` warns when two results used different versions,
because their numbers are then not directly comparable.
