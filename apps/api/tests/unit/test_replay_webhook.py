"""The replay script signs what it sends, and refuses non-local targets by default."""

from pathlib import Path

import httpx
import pytest
import respx

from app.core.webhook_signature import verify_signature
from scripts import replay_webhook

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "webhooks" / "pull_request.opened.json"
SECRET = "replay-test-secret-0123456789"


def test_event_comes_from_the_file_name() -> None:
    assert replay_webhook.event_from_filename(FIXTURE) == "pull_request"
    assert replay_webhook.event_from_filename(Path("installation_repositories.added.json")) == (
        "installation_repositories"
    )


def test_sends_the_file_bytes_with_a_valid_signature(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    route = respx_mock.post(replay_webhook.DEFAULT_URL).respond(202, json={"status": "queued"})

    assert replay_webhook.main([str(FIXTURE), "--delivery-id", "d-1"]) == 0

    request: httpx.Request = route.calls.last.request
    assert request.content == FIXTURE.read_bytes()
    assert request.headers["x-github-event"] == "pull_request"
    assert request.headers["x-github-delivery"] == "d-1"
    assert verify_signature(SECRET, request.content, request.headers["x-hub-signature-256"])


def test_bad_signature_option_really_is_bad(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    route = respx_mock.post(replay_webhook.DEFAULT_URL).respond(401)
    assert replay_webhook.main([str(FIXTURE), "--bad-signature"]) == 1
    request = route.calls.last.request
    assert not verify_signature(SECRET, request.content, request.headers["x-hub-signature-256"])


def test_refuses_a_remote_url_unless_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    assert (
        replay_webhook.main([str(FIXTURE), "--url", "https://codelens.example/webhooks/github"])
        == 2
    )
