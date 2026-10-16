"""Send a saved webhook payload to a running API, signed exactly the way GitHub signs it.

Usage (from apps/api, with the API running):
    uv run python -m scripts.replay_webhook tests/fixtures/webhooks/pull_request.opened.json
    uv run python -m scripts.replay_webhook FILE --delivery-id demo-1   # run twice: duplicate
    uv run python -m scripts.replay_webhook FILE --bad-signature        # expect 401

The event name comes from the file name (`pull_request.opened.json` -> `pull_request`)
unless --event is given. The secret is GITHUB_WEBHOOK_SECRET from the environment or the
repo-root .env. The file's bytes are sent unchanged: the signature covers exactly them.
"""

import argparse
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values

from app.core.webhook_signature import SIGNATURE_HEADER, compute_signature

ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
DEFAULT_URL = "http://localhost:8000/webhooks/github"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def build_request(
    body: bytes, *, secret: str, event: str, delivery_id: str, bad_signature: bool = False
) -> dict[str, str]:
    """The headers GitHub would send with `body`."""
    signature = compute_signature(secret, body)
    if bad_signature:
        signature = compute_signature(secret + "-wrong", body)
    return {
        "Content-Type": "application/json",
        "User-Agent": "GitHub-Hookshot/replay",
        "X-GitHub-Event": event,
        "X-GitHub-Delivery": delivery_id,
        SIGNATURE_HEADER: signature,
    }


def event_from_filename(path: Path) -> str:
    return path.name.split(".", 1)[0]


def webhook_secret() -> str | None:
    return os.environ.get("GITHUB_WEBHOOK_SECRET") or dotenv_values(ROOT_ENV_FILE).get(
        "GITHUB_WEBHOOK_SECRET"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("payload", type=Path, help="JSON payload file")
    parser.add_argument("--event", help="X-GitHub-Event (default: from the file name)")
    parser.add_argument("--delivery-id", help="X-GitHub-Delivery (default: a new UUID)")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"default: {DEFAULT_URL}")
    parser.add_argument("--bad-signature", action="store_true", help="sign with a wrong secret")
    parser.add_argument(
        "--allow-remote", action="store_true", help="allow a URL that is not localhost"
    )
    args = parser.parse_args(argv)

    if urlsplit(args.url).hostname not in LOCAL_HOSTS and not args.allow_remote:
        print(f"refusing to send to {args.url}: not localhost (pass --allow-remote)")
        return 2
    secret = webhook_secret()
    if not secret:
        print("GITHUB_WEBHOOK_SECRET is not set (environment or repo-root .env)")
        return 2

    body = args.payload.read_bytes()
    headers = build_request(
        body,
        secret=secret,
        event=args.event or event_from_filename(args.payload),
        delivery_id=args.delivery_id or str(uuid.uuid4()),
        bad_signature=args.bad_signature,
    )
    response = httpx.post(args.url, content=body, headers=headers, timeout=10.0)
    print(f"{headers['X-GitHub-Event']} delivery {headers['X-GitHub-Delivery']}")
    print(f"HTTP {response.status_code} {response.text}")
    return 0 if response.is_success else 1


if __name__ == "__main__":
    sys.exit(main())
