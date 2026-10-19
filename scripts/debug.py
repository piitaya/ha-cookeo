"""Live TUI inspector for a Cookeo over the SEB cloud.

Auth via refresh_token, pick appliance, then subscribe to the MQTT
topics. Renders a Rich TUI in place with the current status / shadow,
and logs field-level diffs in a change panel.

    python scripts/debug.py

Reads credentials from .env at the repo root. Without a refresh_token,
logs in with COOKEO_EMAIL / COOKEO_PASSWORD (asked for when missing)
and writes the refresh_token back into .env. Read-only. Ctrl+C to exit.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque

import aiohttp
from dotenv import load_dotenv
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.prompt import IntPrompt, Prompt
from rich.table import Table

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_ROOT / ".env"

# The library lives under custom_components/cookeo/lib/. We import it
# as a top-level ``lib`` package so we bypass the integration's
# __init__.py, which pulls in ``homeassistant`` at import time.
sys.path.insert(0, str(REPO_ROOT / "custom_components" / "cookeo"))

from lib import (  # noqa: E402
    ApplianceInfo,
    AwsCredentials,
    CookeoShadow,
    CookeoStatus,
    IotClient,
    Tokens,
    cognito,
    oauth,
    seb_api,
)
from lib.seb_api import appliances_from_profile  # noqa: E402


console = Console()


# ---------------------------------------------------------------- bootstrap


async def _bootstrap(brand: str, market: str) -> Tokens:
    """Log in with COOKEO_EMAIL / COOKEO_PASSWORD, asking for whichever is missing."""
    email = os.environ.get("COOKEO_EMAIL") or Prompt.ask("Moulinex account email")
    password = os.environ.get("COOKEO_PASSWORD") or Prompt.ask("Password", password=True)
    tokens = await oauth.login(email, password, profile_perimeter=brand, market=market)
    if not tokens.refresh_token:
        raise SystemExit("Login returned no refresh_token.")
    return tokens


def _persist_env(key: str, value: str) -> None:
    """Write or update a single key=value line in .env."""
    ENV_PATH.touch(exist_ok=True)
    lines = ENV_PATH.read_text().splitlines()
    out: list[str] = []
    seen = False
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            seen = True
        else:
            out.append(line)
    if not seen:
        out.append(f"{key}={value}")
    ENV_PATH.write_text("\n".join(out) + "\n")


# ----------------------------------------------------------- appliance pick


async def _pick_appliance(
    session: aiohttp.ClientSession, access_token: str, perimeter: str
) -> ApplianceInfo:
    profile = await seb_api.fetch_profile(session, access_token, perimeter)
    appliances = appliances_from_profile(profile)
    if not appliances:
        raise SystemExit("No Cookeo appliances found on this account.")
    if len(appliances) == 1:
        return appliances[0]
    console.print("\n[bold]Multiple appliances found — pick one:[/]")
    for i, a in enumerate(appliances, start=1):
        console.print(f"  {i}. {a.name} ({a.thing_uuid[:8]}…)")
    idx = IntPrompt.ask("Index", default=1)
    return appliances[idx - 1]


# ------------------------------------------------------------ UI rendering


CHANGE_LOG_MAX = 50


def _flatten_status(status: CookeoStatus) -> dict[str, Any]:
    """Pull a flat dict of (label → value) out of a CookeoStatus."""
    program_key = status.program.key if status.program else None
    return {
        "current_status": status.current_status,
        "normalized_status": status.normalized_status,
        "mode": status.mode,
        "progress": status.progress,
        "elapsed_time": status.elapsed_time,
        "remaining_time": status.remaining_time,
        "program_key": program_key,
        "operation_start": status.operation_start,
        "duration_target": status.duration_target,
        "temperature_target": status.temperature_target,
        "recipe_id": status.recipe_id,
        "recipe_title": status.recipe_title,
        "recipe_step_index": status.recipe_step_index,
        "event_date": status.event_date,
        "version": status.version,
        "alert_description": status.alert_description,
        "error": status.error,
        "notification_code": status.notification.code if status.notification else None,
    }


def _flatten_shadow(shadow: CookeoShadow) -> dict[str, Any]:
    return {
        "connected": shadow.connected,
        "last_connection": shadow.last_connection,
        "last_disconnection": shadow.last_disconnection,
        "synchronizing": shadow.synchronizing,
        "fw_ui": shadow.firmware.ui_version,
        "fw_wifi": shadow.firmware.wifi_version,
        "fw_update_priority": shadow.firmware.update_priority,
    }


def _make_table(title: str, fields: dict[str, Any]) -> Table:
    t = Table(title=title, show_header=False, expand=True)
    t.add_column("field", style="bold cyan", no_wrap=True)
    t.add_column("value")
    for k, v in fields.items():
        t.add_row(k, "—" if v is None else str(v))
    return t


def _render(
    appliance: ApplianceInfo,
    status: CookeoStatus,
    shadow: CookeoShadow,
    log: Deque[str],
) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=12),
    )
    layout["body"].split_row(Layout(name="status"), Layout(name="shadow"))
    layout["header"].update(
        Panel(
            f"[bold]{appliance.name}[/]   [dim]{appliance.thing_name}[/]",
            border_style="cyan",
        )
    )
    layout["status"].update(_make_table("cookingStatus", _flatten_status(status)))
    layout["shadow"].update(_make_table("shadow", _flatten_shadow(shadow)))
    layout["footer"].update(
        Panel("\n".join(list(log)[-10:]) or "(waiting for messages)", title="changes")
    )
    return layout


def _diff(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for k in sorted(set(old) | set(new)):
        if old.get(k) != new.get(k):
            out.append(f"{k}: {old.get(k)!r} → {new.get(k)!r}")
    return out


# -------------------------------------------------------------------- main


async def main() -> int:
    load_dotenv(ENV_PATH)
    brand = os.environ.get("COOKEO_BRAND", "moulinex")
    market = os.environ.get("COOKEO_MARKET", "FR")
    # ``api_perimeter`` is the Salesforce ``profilePerimeter`` (e.g.
    # ``MOULINEX_FR``) — derived from brand + market the same way the
    # mobile app does.
    api_perimeter = f"{brand.upper()}_{market.upper()}"
    refresh_token = os.environ.get("COOKEO_REFRESH_TOKEN")

    async with aiohttp.ClientSession() as session:
        if not refresh_token:
            console.print("[yellow]No COOKEO_REFRESH_TOKEN in .env — running bootstrap login.[/]")
            tokens = await _bootstrap(brand, market)
        else:
            tokens = await oauth.refresh(session, refresh_token)

        if tokens.refresh_token:
            _persist_env("COOKEO_REFRESH_TOKEN", tokens.refresh_token)

        appliance = await _pick_appliance(session, tokens.access_token, api_perimeter)
        console.print(f"[green]Selected:[/] {appliance.name} ({appliance.thing_uuid})")

        await seb_api.attach_policies(session, tokens.id_token, appliance.thing_type)
        creds: AwsCredentials = await asyncio.get_running_loop().run_in_executor(
            None, cognito.federate, tokens.id_token
        )

    loop = asyncio.get_running_loop()
    status = CookeoStatus()
    shadow = CookeoShadow()
    log: Deque[str] = deque(maxlen=CHANGE_LOG_MAX)

    def _ts() -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _on_status(new: CookeoStatus) -> None:
        nonlocal status
        before = _flatten_status(status)
        status = status.merge(new)
        for line in _diff(before, _flatten_status(status)):
            loop.call_soon_threadsafe(log.append, f"[{_ts()}] {line}")

    def _on_shadow(new: CookeoShadow) -> None:
        nonlocal shadow
        before = _flatten_shadow(shadow)
        shadow = shadow.merge(new)
        for line in _diff(before, _flatten_shadow(shadow)):
            loop.call_soon_threadsafe(log.append, f"[{_ts()}] shadow {line}")

    def _on_alert(alert: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(log.append, f"[{_ts()}] alert {alert!r}")

    client = IotClient(appliance)
    client.set_callbacks(on_status=_on_status, on_shadow=_on_shadow, on_alert=_on_alert)
    await loop.run_in_executor(None, client.connect, creds)
    console.print("[green]MQTT connected — entering live view (Ctrl+C to exit)[/]")
    await asyncio.sleep(0.5)

    try:
        with Live(_render(appliance, status, shadow, log), refresh_per_second=2, screen=True) as live:
            while True:
                live.update(_render(appliance, status, shadow, log))
                await asyncio.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        await loop.run_in_executor(None, client.disconnect)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        raise SystemExit(0)
