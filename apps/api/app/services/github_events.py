"""The parts of GitHub webhook payloads we read. Everything else is ignored.

Shapes checked on 2026-10-16 against GitHub's "Webhook events and payloads" page and the
real payload examples published in octokit/webhooks. Two facts the handlers rely on:
- `pull_request` and `push` payloads carry the full `repository` (with `default_branch`)
  and, for App webhooks, `installation: {id}` only;
- `installation` and `installation_repositories` payloads carry the full `installation`
  (with `account`) but list repositories as {id, name, full_name, private}: no
  default_branch.
"""

from pydantic import BaseModel, ConfigDict


class _Payload(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Account(_Payload):
    id: int
    login: str | None = None  # enterprise accounts have no login/type
    type: str | None = None  # "User" | "Organization"


class InstallationRef(_Payload):
    """What pull_request / push payloads say about the installation: just its id."""

    id: int


class InstallationInfo(_Payload):
    id: int
    account: Account


class RepositoryOwner(_Payload):
    login: str
    type: str  # "User" | "Organization"


class Repository(_Payload):
    """The full repository object of pull_request and push payloads."""

    id: int
    full_name: str
    private: bool
    default_branch: str
    owner: RepositoryOwner


class RepositorySummary(_Payload):
    """A repository as listed by installation events."""

    id: int
    full_name: str
    private: bool


class GitHubUser(_Payload):
    login: str
    type: str = "User"  # "User" | "Bot" | "Organization"


class GitRef(_Payload):
    ref: str
    sha: str


class PullRequest(_Payload):
    number: int
    title: str
    state: str  # "open" | "closed"
    draft: bool = False
    merged: bool = False
    user: GitHubUser
    head: GitRef


class PullRequestEvent(_Payload):
    action: str
    pull_request: PullRequest
    repository: Repository
    installation: InstallationRef


class PushEvent(_Payload):
    ref: str  # e.g. "refs/heads/main"
    after: str  # head commit after the push (all zeros when the branch was deleted)
    deleted: bool = False
    repository: Repository
    installation: InstallationRef


class InstallationEvent(_Payload):
    action: str
    installation: InstallationInfo
    repositories: list[RepositorySummary] = []


class InstallationRepositoriesEvent(_Payload):
    action: str
    installation: InstallationInfo
    repositories_added: list[RepositorySummary] = []
    repositories_removed: list[RepositorySummary] = []
