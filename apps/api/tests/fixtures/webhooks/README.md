# Webhook payload fixtures

Payloads for tests and for `scripts/replay_webhook.py`. The file name is
`<X-GitHub-Event>.<action>[.<variant>].json`; the replay script reads the event from the
part before the first dot.

**Where they come from.** Each file follows a real GitHub payload, taken from the examples
GitHub delivered and [octokit/webhooks](https://github.com/octokit/webhooks/tree/main/payload-examples/api.github.com)
published, and checked against GitHub's "Webhook events and payloads" docs on 2026-10-16.
They were then trimmed and **redacted**:

- people, repositories and ids are replaced by demo values (`demo-owner` 1001,
  `demo-contributor` 1002, repo `demo-owner/codelens-demo` 2002, installation 3003);
- commit messages, PR bodies and e-mail addresses are placeholders;
- long URL lists that GitHub sends (`*_url` templates) are mostly dropped. The handlers
  ignore unknown fields, so a full payload parses the same way.

To capture your own: in the GitHub App settings, open **Advanced → Recent Deliveries**,
copy a payload, replace any personal data the same way, and save it here.
