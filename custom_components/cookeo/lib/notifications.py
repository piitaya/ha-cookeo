"""Cookeo notification code → semantic key mapping.

The Cookeo's firmware emits cooking-related notifications as numeric
codes (``"30027"``, etc.) inside the ``cookingStatus`` payload. There
is no bundled translation table for these; the SEB cloud appears to
render notification titles/descriptions server-side. This file is the
home-grown equivalent: each known code maps to a semantic snake_case
key, then HA's translation system localises that key.

**This table is intentionally incomplete.** When you encounter an
unknown code:

1. Note the value of ``trigger.event.data.code`` (or the entity log).
2. Cross-reference with what your Cookeo was doing at that moment
   (e.g. "appeared when I opened the lid mid-cooking").
3. Add an entry below and a matching translation in
   ``translations/<lang>.json`` under
   ``entity.event.notification.state.<key>``.

PRs welcome.
"""

from __future__ import annotations

#: ``notification.code`` (string) → semantic snake_case key.
NOTIFICATION_KEYS: dict[str, str] = {
    # Observed in our captures but unconfirmed:
    # "30027": "check_tank",         # appeared briefly during a manual
                                     # warm-up sequence — meaning TBD
}

#: Every semantic key we know about, used as the ``event_types`` list
#: of the notification event entity (HA requires an exhaustive list).
NOTIFICATION_KEY_VALUES: tuple[str, ...] = tuple(sorted(set(NOTIFICATION_KEYS.values()))) + ("unknown",)


def code_to_key(code: str | None) -> str:
    """Translate a raw notification code to its semantic key.

    Returns ``"unknown"`` for codes we haven't catalogued yet — the
    raw code is still attached to the event ``data`` payload so users
    can build their own automations against it.
    """
    if not code:
        return "unknown"
    return NOTIFICATION_KEYS.get(code, "unknown")
