"""SEB cloud REST API."""

from typing import Any

import aiohttp

from .const import COOKEO_API_KEY, SEB_API_BASE, SEB_DOMAIN, SEB_IOT_BASE, USER_AGENT
from .exceptions import CookeoError
from .models import Appliance

_TIMEOUT = aiohttp.ClientTimeout(total=15)


async def fetch_profile(
    session: aiohttp.ClientSession, access_token: str, profile_perimeter: str
) -> dict[str, Any]:
    """Fetch the profile of the account, including its appliances."""
    async with session.get(
        f"{SEB_API_BASE}/common-api/profiles/me",
        headers={
            "ApiKey": COOKEO_API_KEY,
            "Authorization": f"Bearer {access_token}",
            "Grant-Type": "RCUToken",
            "DCP-Context": profile_perimeter,
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
        timeout=_TIMEOUT,
    ) as resp:
        return await _json(resp)


def appliances_from_profile(profile: dict[str, Any]) -> list[Appliance]:
    """Return the Cookeo appliances of a profile."""
    return [
        Appliance(
            name=product.get("name") or "Cookeo",
            thing_type=product["iot"]["thingType"],
            thing_uuid=product["iot"]["functionalId"],
            cmmf=str(product["cmmf"]) if product.get("cmmf") is not None else None,
        )
        for product in (profile.get("household") or {}).get("products") or []
        if SEB_DOMAIN in (product.get("domains") or []) and product.get("iot")
    ]


async def attach_policies(
    session: aiohttp.ClientSession, id_token: str, thing_type: str
) -> None:
    """Give the Cognito identity of the account access to the MQTT topics."""
    async with session.post(
        f"{SEB_IOT_BASE}/entity/v1/update",
        json={"openIdToken": id_token, "thingType": thing_type},
        headers={"ApiKey": COOKEO_API_KEY, "User-Agent": USER_AGENT},
        timeout=_TIMEOUT,
    ) as resp:
        await _json(resp)


async def _json(resp: aiohttp.ClientResponse) -> dict[str, Any]:
    """Return the JSON body of a successful response."""
    if resp.status != 200:
        raise CookeoError(
            f"{resp.method} {resp.url.path} failed ({resp.status}): "
            f"{(await resp.text())[:200]}"
        )
    return await resp.json(content_type=None)
