"""Thin async client for the SEB DCP / IoT REST APIs.

Covers just what the integration needs:

- ``profiles/me`` to discover the user's paired appliances (the rest of
  the API is irrelevant to us — we don't push recipes).
- ``iot.api.groupe-seb.com/entity/v1/update`` to ask the SEB backend to
  attach the AWS IoT policies to our newly-federated Cognito identity.
"""

from __future__ import annotations

from typing import Any

import aiohttp

from .const import (
    COOKEO_API_KEY,
    SEB_API_BASE,
    SEB_DOMAIN,
    SEB_IOT_BASE,
    USER_AGENT,
)
from .models import ApplianceInfo


class SebApiError(Exception):
    """SEB REST API returned a non-2xx response."""


def _headers(access_token: str, profile_perimeter: str) -> dict[str, str]:
    return {
        "ApiKey": COOKEO_API_KEY,
        "Authorization": f"Bearer {access_token}",
        "Grant-Type": "RCUToken",
        "DCP-Context": profile_perimeter,
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }


async def fetch_profile(
    session: aiohttp.ClientSession,
    access_token: str,
    profile_perimeter: str,
) -> dict[str, Any]:
    """Get the user profile (incl. household.products list)."""
    async with session.get(
        f"{SEB_API_BASE}/common-api/profiles/me",
        headers=_headers(access_token, profile_perimeter),
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        body = await resp.text()
        if resp.status != 200:
            raise SebApiError(f"profiles/me failed ({resp.status}): {body[:200]}")
        import json

        return json.loads(body)


def appliances_from_profile(profile: dict[str, Any]) -> list[ApplianceInfo]:
    """Extract Cookeo appliances from a profile JSON."""
    out: list[ApplianceInfo] = []
    products = ((profile.get("household") or {}).get("products")) or []
    for p in products:
        if SEB_DOMAIN not in (p.get("domains") or []):
            continue
        iot = p.get("iot") or {}
        out.append(
            ApplianceInfo(
                appliance_id=p.get("appliance", ""),
                name=p.get("name", ""),
                thing_type=iot.get("thingType", ""),
                thing_uuid=iot.get("functionalId", ""),
                functional_id=(p.get("identifier") or {}).get("functionalId", ""),
                rcu_asset_id=p.get("rcuAssetId", ""),
                cmmf=str(p.get("cmmf")) if p.get("cmmf") is not None else None,
                raw=p,
            )
        )
    return out


async def attach_policies(
    session: aiohttp.ClientSession,
    id_token: str,
    thing_type: str,
) -> dict[str, Any]:
    """Ask the SEB IoT backend to attach the AWS IoT policy to our identity.

    The endpoint is keyed by the Cognito identity (which SEB derives from
    ``id_token``) and the ``thingType`` (e.g. ``COO001``). Calling it
    twice is harmless — SEB returns the same policy name.
    """
    async with session.post(
        f"{SEB_IOT_BASE}/entity/v1/update",
        json={"openIdToken": id_token, "thingType": thing_type},
        headers={
            "ApiKey": COOKEO_API_KEY,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        body = await resp.text()
        if resp.status != 200:
            raise SebApiError(f"attach_policies failed ({resp.status}): {body[:200]}")
        import json

        return json.loads(body)
