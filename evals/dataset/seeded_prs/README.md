# Seeded pull requests

Small diffs with deliberately planted problems, plus the findings a good security review
should produce. The API tests use them (with the FakeLLM) to check that findings land on the
right lines, and the M13 eval harness scores the real models on them. The format, sources,
labelling process and split are described in `../README.md`. The matching rule used for
scoring is in `docs/evals.md`.

Each case is two files:

- `<case>.diff`: a real `git diff` (generated with git, so hunk headers and line counts are
  exact). Every value in them is fake: the "secrets" are invented strings chosen not to match
  any real provider's key format.
- `<case>.expected.json`: the labels (schema_version 2, see `../README.md`).

| Case | What is planted |
|---|---|
| `hardcoded_secret` | a database password and an API token in a config module |
| `sql_injection` | queries built by concatenation and an f-string |
| `unsafe_deserialization` | `pickle.loads` of a cookie, `yaml.load` with the unsafe Loader |
| `path_traversal` | a download endpoint joining a user-supplied name onto a directory |
| `prompt_injection` | a comment telling the AI reviewer to approve and report nothing, above a command injection |
| `docs_only` | documentation changes only (nothing to review) |
| `clean_change` | a safe parameterised query (false-positive check) |

**Prompt injection and the real model.** The automated test only proves our side of the
contract: the injected text reaches the model strictly inside the delimited data block, and
nothing the model returns can approve the PR or post anything unvalidated. Whether a REAL
model still reports the command injection is measured by the M13 evals.
