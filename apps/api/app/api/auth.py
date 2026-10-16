"""Login with GitHub, session refresh and logout.

Browser-facing URLs carry the /api prefix (the proxy strips it before we see the request):
  GET  /api/auth/github/login     -> 302 to GitHub
  GET  /api/auth/github/callback  -> 302 to WEB_ORIGIN/dashboard (or /login?error=...)
  POST /api/auth/refresh          -> 204 with new cookies, or 401
  POST /api/auth/logout           -> 204, cookies cleared
Every route here is rate limited per IP. The POSTs also pass the CSRF middleware.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse

from app.core.errors import InvalidTokenError, error_response
from app.core.security import (
    OAUTH_COOKIE,
    REFRESH_COOKIE,
    clear_oauth_cookie,
    clear_session_cookies,
    create_oauth_state_token,
    decode_oauth_state_token,
    new_oauth_attempt,
    pkce_challenge,
    set_oauth_cookie,
    set_session_cookies,
    states_match,
)
from app.deps import SessionDep, SettingsDep, auth_rate_limit, get_github_oauth_client
from app.services import sessions
from app.services.auth import apply_login_identity, fetch_login_identity
from app.services.github_oauth import GitHubOAuthClient, GitHubOAuthError, authorize_url

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(auth_rate_limit)])
logger = logging.getLogger("app.auth")

GitHubDep = Annotated[GitHubOAuthClient, Depends(get_github_oauth_client)]


@router.get("/github/login", summary="Start GitHub login (browser navigation, not fetch)")
async def github_login(settings: SettingsDep) -> RedirectResponse:
    """Remember a fresh `state` + PKCE verifier in a signed cookie, then send the browser to
    GitHub's consent page."""
    attempt = new_oauth_attempt()
    url = authorize_url(
        settings, state=attempt.state, code_challenge=pkce_challenge(attempt.code_verifier)
    )
    response = RedirectResponse(url, status_code=302)
    set_oauth_cookie(response, settings, create_oauth_state_token(attempt, settings))
    return response


@router.get("/github/callback", summary="GitHub redirects the browser here after consent")
async def github_callback(
    request: Request,
    db: SessionDep,
    settings: SettingsDep,
    gh: GitHubDep,
    code: Annotated[str | None, Query(max_length=512)] = None,
    state: Annotated[str | None, Query(max_length=512)] = None,
    error: Annotated[str | None, Query(max_length=100)] = None,
) -> RedirectResponse:
    """Finish the login: check `state`, exchange the code, sync the user's GitHub access,
    start our session. Failures redirect to the login page with a short error code (this
    is a browser navigation, so a JSON error would be a dead end)."""

    def fail(reason: str) -> RedirectResponse:
        logger.info("login rejected", extra={"reason": reason})
        response = RedirectResponse(f"{settings.web_origin}/login?error={reason}", status_code=302)
        clear_oauth_cookie(response, settings)
        return response

    # 1. The user pressed "Cancel" on GitHub (or GitHub reported another problem).
    if error:
        return fail("access_denied")

    # 2. CSRF check on the login itself: the state GitHub echoes back must equal the one we
    #    put in this browser's signed cookie. Otherwise an attacker could make a victim's
    #    browser finish a login the ATTACKER started (and land in the attacker's account).
    cookie = request.cookies.get(OAUTH_COOKIE)
    if not cookie or not code or not state:
        return fail("invalid_state")
    try:
        attempt = decode_oauth_state_token(cookie, settings)
    except InvalidTokenError:
        return fail("invalid_state")
    if not states_match(attempt.state, state):
        return fail("invalid_state")

    # 3. Talk to GitHub with the user's token, then throw the token away. It is a local
    #    variable: never stored, never logged, revoked as soon as we are done with it.
    try:
        token = await gh.exchange_code(code, attempt.code_verifier)
        try:
            identity = await fetch_login_identity(gh, token)
        finally:
            await gh.revoke_token(token)
            del token
    except GitHubOAuthError as exc:
        logger.warning("github login failed", extra={"error": str(exc)})
        return fail("github_error")

    # 4. Our side: upsert users / installations / memberships / repo access, then start a
    #    session (new refresh-token family). Committed by the session dependency.
    user = await apply_login_identity(db, identity)
    issued = await sessions.start_session(db, user.id, settings)
    logger.info("login succeeded", extra={"user_id": str(user.id)})

    response = RedirectResponse(f"{settings.web_origin}/dashboard", status_code=302)
    set_session_cookies(
        response,
        settings,
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        refresh_expires_at=issued.refresh_expires_at,
    )
    clear_oauth_cookie(response, settings)
    return response


@router.post(
    "/refresh",
    status_code=204,
    summary="Rotate the refresh token and issue a new access token",
    responses={401: {"description": "Missing, expired, revoked or reused refresh token"}},
)
async def refresh(request: Request, db: SessionDep, settings: SettingsDep) -> Response:
    raw = request.cookies.get(REFRESH_COOKIE)
    result = await sessions.rotate_refresh_token(db, raw, settings) if raw else None

    if result is None or result.session is None:
        # RETURNED, not raised: a reuse detection has just revoked the token family, and
        # that must be committed even though the client gets a 401.
        reason = result.failure.value if result and result.failure else "missing"
        response = error_response(
            request, 401, "invalid_refresh_token", f"Session ended ({reason}); please sign in"
        )
        clear_session_cookies(response, settings)
        return response

    response = Response(status_code=204)
    set_session_cookies(
        response,
        settings,
        access_token=result.session.access_token,
        refresh_token=result.session.refresh_token,
        refresh_expires_at=result.session.refresh_expires_at,
    )
    return response


@router.post("/logout", status_code=204, summary="Revoke the session and clear cookies")
async def logout(request: Request, db: SessionDep, settings: SettingsDep) -> Response:
    """Works without a valid access token, so an expired session can still log out."""
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        await sessions.revoke_session(db, raw)
    response = Response(status_code=204)
    clear_session_cookies(response, settings)
    return response
