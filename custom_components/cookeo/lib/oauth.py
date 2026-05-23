"""SEB Salesforce OAuth helpers.

The official mobile app uses Salesforce Communities **implicit flow**
(``response_type=token id_token refresh_token``) with a custom-scheme
redirect URI (``gsmoduser://tokens``). For a headless / HA setting we
cannot intercept the custom scheme, so the config flow asks the user to
paste the redirect URL back.

Token lifetimes observed in PROD:

- ``id_token``: ~2 minutes (very short — used for SEB-side API auth)
- ``access_token``: ~25 minutes (used for SEB-side API auth too)
- ``refresh_token``: long-lived until the user revokes the session

The refresh flow returns rotated ``access_token``, ``id_token``, and
sometimes a new ``refresh_token``.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import time
import urllib.parse
from typing import Any

import aiohttp

from .const import SF_BASE, SF_CLIENT_ID, SF_REDIRECT_URI, USER_AGENT
from .models import Tokens


class OAuthError(Exception):
    """Raised on OAuth failures (network, refused token, …)."""


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
    """Extract tokens from a ``gsmoduser://tokens#…`` URL.

    Accepts both a raw URL (from the address bar) and the browser
    console line wrapping it (``Failed to launch 'gsmoduser://…' because
    …``) — the regex pulls out just the URL.
    """
    match = _GSMODUSER_RE.search(raw_url)
    url = match.group(0) if match else raw_url.strip()
    if "#" not in url:
        raise OAuthError(
            "No gsmoduser://tokens#… URL found — paste either the full URL "
            "from the address bar or the line shown in the browser's JS console."
        )
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
