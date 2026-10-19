"""SEB Salesforce OAuth helpers.

The official mobile app uses Salesforce Communities **implicit flow**
(``response_type=token id_token refresh_token``) with a custom-scheme
redirect URI (``gsmoduser://tokens``). :func:`login` submits the
account email and password to the same login page the app shows, then
follows the redirects until it reaches the ``gsmoduser://`` URL.

Token lifetimes observed in PROD:

- ``id_token``: ~2 minutes (very short — used for SEB-side API auth)
- ``access_token``: ~25 minutes (used for SEB-side API auth too)
- ``refresh_token``: long-lived until the user revokes the session

The refresh flow returns rotated ``access_token``, ``id_token``, and
sometimes a new ``refresh_token``.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import re
import secrets
import time
import urllib.parse
from typing import Any

import aiohttp
from yarl import URL

from .const import SF_BASE, SF_CLIENT_ID, SF_REDIRECT_URI, USER_AGENT
from .models import Tokens

_LOGGER = logging.getLogger(__name__)


class OAuthError(Exception):
    """Raised on OAuth failures (network, refused token, …)."""


class LoginError(OAuthError):
    """Raised when SEB refuses the email / password.

    ``reason`` is one of ``invalid_auth``, ``email_not_verified``,
    ``account_locked`` or ``password_reset_required``.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def build_authorize_url(
    profile_perimeter: str = "moulinex",
    market: str = "FR",
    lang: str = "fr",
    state: str | None = None,
    nonce: str | None = None,
) -> tuple[str, str]:
    """Return ``(auth_url, expected_state)`` to start the implicit flow.

    The Salesforce ``ContinuationForExpId`` page redirects (via JS) to
    ``/services/oauth2/authorize/expid_{perimeter}000{market}000{lang}000true``;
    we build the canonical URL directly to skip the JS hop.
    """
    state = state or secrets.token_urlsafe(16)
    nonce = nonce or secrets.token_urlsafe(16)
    expid = f"expid_{profile_perimeter.lower()}000{market.upper()}000{lang.lower()}000true"
    params = {
        "client_id": SF_CLIENT_ID,
        "redirect_uri": SF_REDIRECT_URI,
        "response_type": "token id_token refresh_token",
        "state": state,
        "nonce": nonce,
    }
    url = f"{SF_BASE}/services/oauth2/authorize/{expid}?{urllib.parse.urlencode(params)}"
    return url, state


_GSMODUSER_RE = re.compile(r"gsmoduser://tokens#[^\s'\"]+")


def parse_redirect(raw_url: str, expected_state: str | None = None) -> Tokens:
    """Extract tokens from a ``gsmoduser://tokens#…`` URL."""
    match = _GSMODUSER_RE.search(raw_url)
    url = match.group(0) if match else raw_url.strip()
    if "#" not in url:
        raise OAuthError("No gsmoduser://tokens#… URL found")
    fragment = url.split("#", 1)[1]
    fields = dict(urllib.parse.parse_qsl(fragment, keep_blank_values=True))
    if expected_state and fields.get("state") and fields["state"] != expected_state:
        raise OAuthError("OAuth state mismatch — refresh and try again.")
    missing = [k for k in ("access_token", "id_token") if k not in fields]
    if missing:
        raise OAuthError(f"Missing token fields in URL: {missing}")
    claims = _jwt_payload(fields["id_token"])
    return Tokens(
        access_token=fields["access_token"],
        id_token=fields["id_token"],
        refresh_token=fields.get("refresh_token"),
        issued_at=int(fields.get("issued_at", 0)) // 1000 or int(time.time()),
        id_token_exp=int(claims.get("exp", 0)),
    )


_FORM_RE = re.compile(r'<form id="([^"]+)"[^>]*\saction="([^"]+)"')
_A4J_ACTION_RE = r"{}=function\([^)]*\)\{{A4J\.AJAX\.Submit\('[^']+',null,\{{'similarityGroupingId':'([^']+)'"
_VIEW_STATE_RE = re.compile(r'name="(com\.salesforce\.visualforce\.\w+)" value="([^"]*)"')
_LOGIN_RESULT_RE = re.compile(r"afterLoginAttempt\((\w+),\s*(\w+),\s*(\w+)\)")
_JS_REDIRECT_RE = re.compile(r"window\.location\.replace\(([\"'])(.+?)\1\)")
#: Same order as the ``afterLoginAttempt(...)`` arguments.
_LOGIN_FAILURES = ("email_not_verified", "account_locked", "password_reset_required")
#: Post-login page that submits itself on load before redirecting.
_LOGIN_FLOW_ACTION = "finishLoginFlowAndRedirectJS"


async def login(
    email: str,
    password: str,
    profile_perimeter: str = "moulinex",
    market: str = "FR",
    lang: str = "fr",
) -> Tokens:
    """Log in with the SEB account email and password.

    Uses a dedicated HTTP session so the Salesforce session cookies
    never end up in a shared cookie jar.
    """
    auth_url, state = build_authorize_url(profile_perimeter, market, lang)
    async with aiohttp.ClientSession(
        headers={"User-Agent": USER_AGENT},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as web:
        async with web.get(auth_url) as resp:
            login_url = resp.url
            page = await resp.text()
        location, body = await _a4j_submit(
            web, login_url, page, "loginAF", {"userEmail": email, "password": password}
        )
        if location is None:
            result = _LOGIN_RESULT_RE.search(body)
            if result is None:
                raise OAuthError("Unexpected response from the SEB login page")
            raise LoginError(
                next(
                    (r for r, flag in zip(_LOGIN_FAILURES, result.groups()) if flag == "true"),
                    "invalid_auth",
                )
            )
        redirect = await _follow_to_app(web, login_url.join(URL(location, encoded=True)))
    return parse_redirect(redirect, state)


async def _a4j_submit(
    web: aiohttp.ClientSession,
    page_url: URL,
    page: str,
    action: str,
    params: dict[str, str] | None = None,
) -> tuple[str | None, str]:
    """Replay a Visualforce ``A4J.AJAX.Submit`` call, return ``(redirect, body)``."""
    form = _FORM_RE.search(page)
    action_match = re.search(_A4J_ACTION_RE.format(re.escape(action)), page)
    if form is None or action_match is None:
        raise OAuthError(f"SEB page not recognised (no {action})")
    form_id, action_id = form.group(1), action_match.group(1)
    data = {
        "AJAXREQUEST": "_viewRoot",
        form_id: form_id,
        **{k: html.unescape(v) for k, v in _VIEW_STATE_RE.findall(page)},
        **(params or {}),
        action_id: action_id,
    }
    post_url = page_url.join(URL(html.unescape(form.group(2)), encoded=True))
    async with web.post(post_url, data=data, allow_redirects=False) as resp:
        _LOGGER.debug("SEB login submit: %s %s%s", resp.status, post_url.host, post_url.path)
        return resp.headers.get("Location"), await resp.text()


async def _follow_to_app(web: aiohttp.ClientSession, url: URL) -> str:
    for _ in range(10):
        async with web.get(url, allow_redirects=False) as resp:
            _LOGGER.debug("SEB login redirect: %s %s%s", resp.status, url.host, url.path)
            location = resp.headers.get("Location")
            body = await resp.text()
        if location is None and (script := _JS_REDIRECT_RE.search(body)):
            location = script.group(2)
        if location is None and _LOGIN_FLOW_ACTION in body:
            location, _ = await _a4j_submit(web, url, body, _LOGIN_FLOW_ACTION)
        if location is None:
            break
        if location.startswith(SF_REDIRECT_URI):
            return location
        url = url.join(URL(location, encoded=True))
    raise OAuthError("Login accepted but SEB never redirected to the app")


async def refresh(session: aiohttp.ClientSession, refresh_token: str) -> Tokens:
    """Exchange a refresh token for a fresh set of tokens.

    Salesforce ``grant_type=refresh_token`` does not always return a new
    ``refresh_token`` — keep the old one if the response omits it.
    """
    async with session.post(
        f"{SF_BASE}/services/oauth2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": SF_CLIENT_ID,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        body = await resp.text()
        if resp.status != 200:
            raise OAuthError(f"refresh failed ({resp.status}): {body[:200]}")
        data = json.loads(body)
    claims = _jwt_payload(data["id_token"])
    return Tokens(
        access_token=data["access_token"],
        id_token=data["id_token"],
        refresh_token=data.get("refresh_token") or refresh_token,
        issued_at=int(data.get("issued_at", 0)) // 1000 or int(time.time()),
        id_token_exp=int(claims.get("exp", 0)),
    )


async def revoke(session: aiohttp.ClientSession, token: str) -> None:
    """Best-effort revocation of an OAuth token. Used on integration unload."""
    try:
        await session.post(
            f"{SF_BASE}/services/oauth2/revoke",
            data={"token": token},
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=10),
        )
    except aiohttp.ClientError:
        pass


def _jwt_payload(jwt: str) -> dict[str, Any]:
    """Decode the JWT payload (no signature check — we just want the claims)."""
    try:
        payload = jwt.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except (IndexError, ValueError, json.JSONDecodeError):
        return {}
