"""Live view of a Cookeo over the SEB cloud.

Reads COOKEO_REFRESH_TOKEN from the .env at the repo root, or logs in with
COOKEO_EMAIL and COOKEO_PASSWORD (asked for when missing) and stores the
refresh token there. Read-only. Ctrl+C to exit.

    python scripts/debug.py
"""

import asyncio
from collections import deque
import contextlib
from datetime import datetime
import os
from pathlib import Path
import sys
from typing import Any

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
CHANGE_LOG_SIZE = 50

# Import the library on its own, the integration package needs Home Assistant.
sys.path.insert(0, str(REPO_ROOT / "custom_components" / "cookeo"))

from lib import (  # noqa: E402
    Appliance,
    CookeoShadow,
    CookeoStatus,
    IotClient,
    Tokens,
    cognito,
    oauth,
    seb_api,
)

console = Console()


async def _login(brand: str, market: str) -> Tokens:
    """Log in with the credentials of the .env, asking for the missing ones."""
    email = os.environ.get("COOKEO_EMAIL") or Prompt.ask("Moulinex account email")
    password = os.environ.get("COOKEO_PASSWORD") or Prompt.ask(
        "Password", password=True
    )
    return await oauth.login(email, password, brand=brand, market=market)


def _store_env(key: str, value: str) -> None:
    """Write or replace one ``key=value`` line of the .env."""
    ENV_PATH.touch(exist_ok=True)
    lines = [
        line
        for line in ENV_PATH.read_text().splitlines()
        if not line.startswith(f"{key}=")
    ]
    ENV_PATH.write_text("\n".join([*lines, f"{key}={value}"]) + "\n")


async def _pick_appliance(
    session: aiohttp.ClientSession, access_token: str, perimeter: str
) -> Appliance:
    """Return the Cookeo of the account, asking which one when there are several."""
    profile = await seb_api.fetch_profile(session, access_token, perimeter)
    appliances = seb_api.appliances_from_profile(profile)
    if not appliances:
        raise SystemExit("No Cookeo on this account.")
    if len(appliances) == 1:
        return appliances[0]
    for index, appliance in enumerate(appliances, start=1):
        console.print(f"  {index}. {appliance.name} ({appliance.thing_uuid[:8]})")
    return appliances[IntPrompt.ask("Cookeo", default=1) - 1]


def _status_fields(status: CookeoStatus) -> dict[str, Any]:
    """Return the cooking status fields to display."""
    return {
        "current_status": status.current_status,
        "normalized_status": status.normalized_status,
        "mode": status.mode,
        "progress": status.progress,
        "elapsed_time": status.elapsed_time,
        "remaining_time": status.remaining_time,
        "program_key": status.program_key,
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


def _shadow_fields(shadow: CookeoShadow) -> dict[str, Any]:
    """Return the shadow fields to display."""
    return {
        "connected": shadow.connected,
        "last_connection": shadow.last_connection,
        "last_disconnection": shadow.last_disconnection,
        "synchronizing": shadow.synchronizing,
        "fw_ui": shadow.firmware.ui_version,
        "fw_wifi": shadow.firmware.wifi_version,
        "fw_update_priority": shadow.firmware.update_priority,
    }


def _table(title: str, fields: dict[str, Any]) -> Table:
    """Render fields as a two-column table."""
    table = Table(title=title, show_header=False, expand=True)
    table.add_column("field", style="bold cyan", no_wrap=True)
    table.add_column("value")
    for key, value in fields.items():
        table.add_row(key, "-" if value is None else str(value))
    return table


def _render(
    appliance: Appliance,
    status: CookeoStatus,
    shadow: CookeoShadow,
    changes: deque[str],
) -> Layout:
    """Render the live view."""
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
    layout["status"].update(_table("cookingStatus", _status_fields(status)))
    layout["shadow"].update(_table("shadow", _shadow_fields(shadow)))
    layout["footer"].update(
        Panel(
            "\n".join(list(changes)[-10:]) or "(waiting for messages)", title="changes"
        )
    )
    return layout


def _diff(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Return one line per field that changed."""
    return [
        f"{key}: {old.get(key)!r} → {new.get(key)!r}"
        for key in sorted(old.keys() | new.keys())
        if old.get(key) != new.get(key)
    ]


async def main() -> None:
    """Log in, connect to the Cookeo and show its live state."""
    load_dotenv(ENV_PATH)
    brand = os.environ.get("COOKEO_BRAND", "moulinex")
    market = os.environ.get("COOKEO_MARKET", "FR")
    refresh_token = os.environ.get("COOKEO_REFRESH_TOKEN")

    async with aiohttp.ClientSession() as session:
        if refresh_token:
            tokens = await oauth.refresh(session, refresh_token)
        else:
            tokens = await _login(brand, market)
        _store_env("COOKEO_REFRESH_TOKEN", tokens.refresh_token)
        appliance = await _pick_appliance(
            session, tokens.access_token, f"{brand.upper()}_{market.upper()}"
        )
        console.print(f"[green]Selected:[/] {appliance.name} ({appliance.thing_uuid})")
        await seb_api.attach_policies(session, tokens.id_token, appliance.thing_type)

    loop = asyncio.get_running_loop()
    credentials = await loop.run_in_executor(None, cognito.federate, tokens.id_token)
    status = CookeoStatus()
    shadow = CookeoShadow()
    changes: deque[str] = deque(maxlen=CHANGE_LOG_SIZE)

    def log(line: str) -> None:
        loop.call_soon_threadsafe(changes.append, f"[{datetime.now():%H:%M:%S}] {line}")

    def on_status(update: CookeoStatus) -> None:
        nonlocal status
        before = _status_fields(status)
        status = status.merge(update)
        for line in _diff(before, _status_fields(status)):
            log(line)

    def on_shadow(update: CookeoShadow) -> None:
        nonlocal shadow
        before = _shadow_fields(shadow)
        shadow = shadow.merge(update)
        for line in _diff(before, _shadow_fields(shadow)):
            log(f"shadow {line}")

    client = IotClient(
        appliance,
        on_status=on_status,
        on_shadow=on_shadow,
        on_alert=lambda alert: log(f"alert {alert!r}"),
    )
    await loop.run_in_executor(None, client.connect, credentials)
    try:
        with Live(
            _render(appliance, status, shadow, changes),
            refresh_per_second=2,
            screen=True,
        ) as live:
            while True:
                live.update(_render(appliance, status, shadow, changes))
                await asyncio.sleep(0.5)
    finally:
        await loop.run_in_executor(None, client.disconnect)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
