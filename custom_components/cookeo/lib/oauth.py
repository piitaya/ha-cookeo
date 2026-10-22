"""SEB account login and token refresh."""

import html
import logging
import re
import secrets
import urllib.parse

import aiohttp
from yarl import URL

from .const import SF_BASE, SF_CLIENT_ID, SF_REDIRECT_URI, USER_AGENT
from .exceptions import CookeoError, LoginError
from .models import Tokens

_LOGGER = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=15)

_FORM_RE = re.compile(r'<form id="([^"]+)"[^>]*\saction="([^"]+)"')
_A4J_ACTION_RE = r"{}=function\([^)]*\)\{{A4J\.AJAX\.Submit\('[^']+',null,\{{'similarityGroupingId':'([^']+)'"
_VIEW_STATE_RE = re.compile(
    r'name="(com\.salesforce\.visualforce\.\w+)" value="([^"]*)"'
)
_LOGIN_RESULT_RE = re.compile(r"afterLoginAttempt\((\w+),\s*(\w+),\s*(\w+)\)")
_JS_REDIRECT_RE = re.compile(r"window\.location\.replace\(([\"'])(.+?)\1\)")
_REDIRECT_URI_RE = re.compile(r"gsmoduser://tokens#[^\s'\"]+")
# Same order as the arguments of afterLoginAttempt().
_LOGIN_FAILURES = ("email_not_verified", "account_locked", "password_reset_required")
_LOGIN_FLOW_ACTION = "finishLoginFlowAndRedirectJS"


async def login(
    email: str,
    password: str,
    brand: str = "moulinex",
    market: str = "FR",
    language: str = "fr",
) -> Tokens:
    """Log in with the SEB account email and password."""
    state = secrets.token_urlsafe(16)
    expid = f"expid_{brand}000{market}000{language}000true"
    authorize_url = URL(f"{SF_BASE}/services/oauth2/authorize/{expid}").with_query(
        client_id=SF_CLIENT_ID,
        redirect_uri=SF_REDIRECT_URI,
        response_type="token id_token refresh_token",
        state=state,
        nonce=secrets.token_urlsafe(16),
    )
    # SEB disables the password grant, so replay the login page of the mobile app.
    # Own session so the Salesforce cookies stay out of the shared cookie jar.
    async with aiohttp.ClientSession(
        headers={"User-Agent": USER_AGENT}, timeout=_TIMEOUT
    ) as web:
        async with web.get(authorize_url) as resp:
            login_url = resp.url
            page = await resp.text()
        location, body = await _a4j_submit(
            web, login_url, page, "loginAF", {"userEmail": email, "password": password}
        )
        if location is None:
            if (result := _LOGIN_RESULT_RE.search(body)) is None:
                raise CookeoError("Unexpected response from the SEB login page")
            raise LoginError(
                next(
                    (
                        reason
                        for reason, flag in zip(
                            _LOGIN_FAILURES, result.groups(), strict=True
                        )
                        if flag == "true"
                    ),
                    "invalid_auth",
                )
            )
        redirect = await _follow_to_app(
            web, login_url.join(URL(location, encoded=True))
        )
    return _parse_redirect(redirect, state)


async def refresh(session: aiohttp.ClientSession, refresh_token: str) -> Tokens:
    """Get new tokens from a refresh token."""
    async with session.post(
        f"{SF_BASE}/services/oauth2/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": SF_CLIENT_ID,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=_TIMEOUT,
    ) as resp:
        if resp.status != 200:
            raise CookeoError(
                f"Token refresh failed ({resp.status}): {(await resp.text())[:200]}"
            )
        data = await resp.json(content_type=None)
    return Tokens(
        access_token=data["access_token"],
        id_token=data["id_token"],
        refresh_token=data.get("refresh_token", refresh_token),
    )


async def _a4j_submit(
    web: aiohttp.ClientSession,
    page_url: URL,
    page: str,
    action: str,
    params: dict[str, str] | None = None,
) -> tuple[str | None, str]:
    """Replay a Visualforce ``A4J.AJAX.Submit`` call, return the redirect and body."""
    form = _FORM_RE.search(page)
    action_match = re.search(_A4J_ACTION_RE.format(re.escape(action)), page)
    if form is None or action_match is None:
        raise CookeoError(f"SEB page not recognised (no {action})")
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
        _LOGGER.debug(
            "Login submit: %s %s%s", resp.status, post_url.host, post_url.path
        )
        return resp.headers.get("Location"), await resp.text()


async def _follow_to_app(web: aiohttp.ClientSession, url: URL) -> str:
    """Follow the HTTP and JavaScript redirects up to the app redirect URI."""
    for _ in range(10):
        async with web.get(url, allow_redirects=False) as resp:
            _LOGGER.debug("Login redirect: %s %s%s", resp.status, url.host, url.path)
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
    raise CookeoError("Login accepted but SEB never redirected to the app")


def _parse_redirect(redirect: str, state: str) -> Tokens:
    """Extract the tokens from the ``gsmoduser://tokens#...`` redirect."""
    if (match := _REDIRECT_URI_RE.search(redirect)) is None:
        raise CookeoError("No tokens in the SEB redirect")
    fields = dict(urllib.parse.parse_qsl(match.group(0).split("#", 1)[1]))
    if fields.get("state") != state:
        raise CookeoError("OAuth state mismatch")
    try:
        return Tokens(
            access_token=fields["access_token"],
            id_token=fields["id_token"],
            refresh_token=fields["refresh_token"],
        )
    except KeyError as err:
        raise CookeoError(f"Missing {err} in the SEB redirect") from err
