# Candidate cases (MODEL-DRAFTED, not scored)

**Everything in this directory was drafted by a model (Claude Opus 5.5 via Claude Code) on
2026-10-17. The labels are drafts (`"label_status": "draft"`) and have not been reviewed by a
human.** The harness never scores this directory: `run_evals.py` reads only `seeded_prs/`.

The diffs are real `git diff` output, generated from before/after files in throwaway repos,
so hunk headers and line numbers are exact. The label line numbers were located by searching
the after-file for the offending code, not counted by hand. Every "secret", URL and name in
them is invented.

| Case | Planted | Required label (draft) |
|---|---|---|
| `weak_password_hash` | unsalted MD5 password hashing | `weak-crypto` ≥ medium |
| `eval_user_input` | `eval()` of a query parameter | `code-injection` ≥ high |
| `tls_verify_disabled` | `verify=False` on a request carrying a bearer token | `insecure-config` ≥ medium |
| `missing_authorization` | delete endpoint without the owner check its neighbour has | `missing-authz` ≥ high (lines 24–25) |
| `insecure_default` | `APP_DEBUG` defaults to on (optional: binds 0.0.0.0) | `insecure-config` ≥ medium |
| `clean_safe_yaml` | `yaml.safe_load` + key allow-list | none (clean) |
| `clean_subprocess_args` | `subprocess.run` with an argument list, no shell | none (clean) |
| `clean_sha256_cache_key` | SHA-256 for cache keys / ETags | none (clean) |
| `clean_safe_download` | download with `resolve()` + `is_relative_to` check | none (clean) |
| `clean_env_secret` | token read from the environment, fail fast | none (clean) |

Things worth a second look while reviewing:

- `insecure_default`: the required label (line 6) and the optional one (line 7) share a
  category and are 1 line apart. With the ±2 tolerance, a finding about line 7 counts as a
  hit on line 6. Decide whether that is acceptable, or drop or move the optional label.
- Severities are the *minimum* a good review would give. Check they are not too strict.
- The clean cases are deliberately near-misses of the planted ones. If you think a careful
  reviewer *would* reasonably comment on one (e.g. `subprocess` without `shell=False`
  spelled out), add it as an optional label instead of leaving the case strictly clean.

## Reviewing and moving a case

For each case you accept:

1. Read the `.diff`, then correct `<case>.expected.json` by hand: the lines, categories
   (family names from `evals/categories.json`), `min_severity`, notes, optional labels and
   `clean`. Then set:
   - `"label_status": "accepted"`
   - `"source": "synthetic"`
   - `"labelled_by": "drafted by Claude (2026-10-17); reviewed and corrected by <you> (<date>)"`
2. Check the candidates still validate:

   ```bash
   uv run --project apps/api python -m evals.validate_dataset --candidates
   ```

3. Move the case:

   ```bash
   git mv evals/dataset/candidates/<case>.diff evals/dataset/candidates/<case>.expected.json evals/dataset/seeded_prs/
   ```

4. Add `<case>` to `dev` or `test` in `evals/dataset/splits.json` (the split is re-proposed
   once the cases are in).
5. Validate the scored dataset and run the eval tests:

   ```bash
   uv run --project apps/api python -m evals.validate_dataset
   cd apps/api && uv run pytest tests/evals -q
   ```

Step 5 fails if the case is not in a split, still says `draft`, or a label points at a line
that was not added.
