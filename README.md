# Cookeo for Home Assistant

Home Assistant integration for the Moulinex Cookeo multicookers connected to Wi-Fi. It goes through the same cloud as the Moulinex app, so the Cookeo must already be set up in that app.

## Supported appliances

| Model | Tested |
| --- | --- |
| Cookeo Touch WiFi | ✓ |
| Cookeo Touch Pro WiFi | |
| Cookeo Touch Mini WiFi | |

Only the Cookeo Touch WiFi (firmware 5.0.54) has been tested. The other Wi-Fi models use the same cloud and should work. The Bluetooth-only Cookeo Connect is not supported, and neither are other SEB appliances such as the Companion or the OptiGrill.

## Entities

Each Cookeo gets a device with these entities:

| Entity | Description |
| --- | --- |
| State | Cooking phase: ready, preheating, cooking, keep warm, done… |
| Mode | Manual, program, recipe or ingredient cooking |
| Program | Program in use, such as pressure cooking or simmer |
| Progress | Percentage while preheating or releasing pressure |
| Remaining time | Time left in the program |
| Cooking started | When the program started |
| Estimated end | When the program should end |
| Target temperature | Temperature set by the program, when it has one |
| Recipe | Title of the recipe in progress |
| Step | Current step of the recipe |
| Notification | Event fired for each notification of the Cookeo, with its title and description |
| Connection | Whether the Cookeo is connected to the cloud (diagnostic) |

The firmware version is shown on the device page.

## Installation

With HACS:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=piitaya&repository=ha-cookeo&category=integration)

Then restart Home Assistant and add the integration:

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=cookeo)

To install it manually, copy `custom_components/cookeo/` into the `custom_components` folder of your Home Assistant configuration, restart, then go to **Settings → Devices & services → Add integration** and search for Cookeo.

## Setup

Pick the region of your Moulinex account, then enter the email and password you use in the Moulinex app. Home Assistant logs in once and keeps only a login token, which it renews on its own. The password is not stored.

When the account has several Cookeo, setup asks which one to add. Run it again to add the others.

If the token stops working, after a password change for example, remove the integration and add it again.

## Limitations

The integration is read-only. The cloud offers no way to control the Cookeo, so programs can only be started or stopped on the appliance itself.

## Development

`scripts/debug.py` connects to your account without Home Assistant and shows the live state of the Cookeo in the terminal, with a log of every value that changes. Like the Moulinex app, the only thing it sends is a request for the current state.

```bash
uv venv
uv pip install -r requirements_dev.txt
.venv/bin/python scripts/debug.py
```

It logs in with `COOKEO_EMAIL` and `COOKEO_PASSWORD` from a `.env` file at the root of the repo, or asks for them, then saves the login token in `.env` for the next runs.

## License

[MIT](LICENSE)

## Disclaimer

This integration is not affiliated with Moulinex, Tefal or Groupe SEB. It relies on an undocumented cloud API that can change at any time.
