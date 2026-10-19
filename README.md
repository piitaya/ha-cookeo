# Cookeo, Home Assistant integration

Home Assistant integration for the **Moulinex Cookeo Touch WiFi** over
SEB's cloud API. No broker to run, no Bluetooth pairing.

## Status

Read-only. Tested against a Cookeo Touch WiFi (model `APPLIANCE_1090`,
firmware 5.0.54). Other cloud-connected Cookeo models share the same
backend (`PRO_COO` domain) and should work out of the box, but are
untested.

The non-cloud Cookeo Connect (Bluetooth-only) is not supported.

## Supported appliances

| Model | Tested |
| --- | --- |
| Cookeo Touch WiFi | ✓ |
| Cookeo Touch Pro WiFi | |
| Cookeo Touch Mini WiFi | |

Other SEB cloud robots (Companion, OptiGrill, CakeFactory, …) use the
same authentication stack but have different `cookingStatus` payloads
and are out of scope for this repo.

## Entities

Per Cookeo:

| Entity | Type | Description |
| --- | --- | --- |
| State | sensor (enum) | Cooking phase (`ready`, `warming`, `cooking`, `keep_warm`, `done`, …) |
| Mode | sensor (enum) | `manual`, `program`, `recipe`, `ingredient_cooking` |
| Program | sensor (enum) | Localised program name (pressure cooking, simmer, steam, …) |
| Progress | sensor (%) | Live percentage during warming / depressurisation |
| Remaining time | sensor (duration) | Cloud-computed countdown |
| Cooking started | sensor (timestamp) | When the current operation began |
| Estimated end | sensor (timestamp) | `started + duration` target |
| Target temperature | sensor (°C) | When a program defines an explicit temperature |
| Recipe | sensor (text) | Recipe title (when running a recipe) |
| Step | sensor (text) | Current step index inside the recipe |
| Notification | event | Fires on each appliance notification with localised title + description |

Diagnostic:

| Entity | Type | Description |
| --- | --- | --- |
| Connection | binary sensor | Cloud connectivity (online / offline) |

Firmware version is exposed as the device's `sw_version` on the device
page, not as a separate entity.

## Install

Recommended via HACS:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=piitaya&repository=ha-cookeo&category=integration)

Then restart Home Assistant and add the integration:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=cookeo)

Or manually: copy `custom_components/cookeo/` into your HA
`custom_components/`, restart, then **Settings → Devices & Services →
Add Integration → "Cookeo"**.

Follow the on-screen instructions (see "Authentication" below).

## Authentication

Setup asks for the brand / market of your Moulinex account, then the email and password you use in the Moulinex or Cookeo app. Home Assistant logs in once with them and keeps only the resulting refresh token, which it rotates automatically. The password is not stored.

If the refresh token gets revoked (password change, logged out everywhere), remove the integration and add it again.

## Limitations

- **Read-only.** The cloud API has no remote-control endpoints for the
  Cookeo line — every command path observed in the official mobile app
  is empty / no-op. Pressing buttons on the appliance is the only way
  to start, pause, or stop a program.
- **No initial state on power-on.** The Cookeo only publishes
  `cookingStatus` on phase transitions. If Home Assistant connects
  while the appliance is sitting idle, the live entities stay in
  "Inconnu" until you interact with the device.

## Development

A standalone debug script lives at `scripts/debug.py`. It uses the same
library code as the integration, drives it against your real account,
and prints a live Rich-powered TUI with state, shadow, and a diff log
of every field change. Read-only, no HA needed.

```bash
uv venv
uv pip install -r requirements_dev.txt
.venv/bin/python scripts/debug.py
```

On first run it logs in with `COOKEO_EMAIL` and `COOKEO_PASSWORD` from `.env` (or asks for them), then writes the refresh token to `.env` for the next runs.

## License

[MIT](LICENSE).

## Disclaimer

Unofficial integration, not affiliated with Moulinex, Tefal, or Groupe
SEB. The cloud API surface is undocumented and may change without
notice. Use at your own risk.
