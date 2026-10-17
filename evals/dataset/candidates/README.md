# Candidate cases (model-drafted, not scored)

New eval cases wait here until a human has reviewed every label. The harness never scores
this directory: `run_evals.py` reads only `seeded_prs/`. The folder is empty between batches
(`.gitkeep` keeps it tracked), and `validate_dataset --candidates` accepts an empty folder.

## History

**Batch 1 (drafted 2026-10-17 by Claude Opus 5.5 via Claude Code; reviewed by the
repository owner).** Ten cases were drafted: five flawed and five clean near-misses.

- **Accepted (9), labels corrected by hand, now in `seeded_prs/`:**
  - flawed: `weak_password_hash`, `eval_user_input`, `tls_verify_disabled`,
    `missing_authorization`;
  - clean: `clean_safe_yaml`, `clean_subprocess_args`, `clean_sha256_cache_key`,
    `clean_safe_download`, `clean_env_secret`.
- **Dropped (1): `insecure_default`.** Its required label (debug defaulting to on, line 6)
  and its optional label (binding to 0.0.0.0, line 7) were one line apart in the same
  category (`insecure-config`). With the ±2-line matching tolerance, a finding about one
  would count as finding the other, so the case could not measure what it claimed to.

## Drafting a new batch

- Generate each diff with git from before/after files in a throwaway repository, so hunk
  headers and line numbers are exact.
- Locate label lines from the code, not by counting.
- Mark every label file `"label_status": "draft"` and set `labelled_by` to say who drafted
  it.
- Keep two labels of the same category further apart than the matching tolerance (see
  `insecure_default` above), or merge them into one label with a line range.

## Reviewing and moving a case

For each case you accept:

1. Read the `.diff`, then correct `<case>.expected.json` by hand: the lines, categories
   (family names from `evals/categories.json`), `min_severity`, notes, optional labels and
   `clean`. Then set:
   - `"label_status": "accepted"`
   - `"source": "synthetic"`
   - `"labelled_by": "drafted by <model> (<date>); reviewed and corrected by <you> (<date>)"`
2. Check the candidates still validate:

   ```bash
   uv run --project apps/api python -m evals.validate_dataset --candidates
   ```

3. Move the case:

   ```bash
   git mv evals/dataset/candidates/<case>.diff evals/dataset/candidates/<case>.expected.json evals/dataset/seeded_prs/
   ```

4. Add `<case>` to `dev` or `test` in `evals/dataset/splits.json`. A change to the split is
   proposed and approved before it is committed.
5. Validate the scored dataset and run the eval tests:

   ```bash
   uv run --project apps/api python -m evals.validate_dataset
   cd apps/api && uv run pytest tests/evals -q
   ```

Step 5 fails if the case is not in a split, still says `draft`, or a label points at a line
that was not added.

Adding or changing a case changes the dataset version recorded in every result. Results
from different versions are not directly comparable (`compare.py` warns about this).
