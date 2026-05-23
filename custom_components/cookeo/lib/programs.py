"""Cookeo program ID → semantic key mapping.

The Cookeo firmware emits its program names in English (e.g.
``"Low Stew Cooking"``); the official mobile app overrides that with a
localised string keyed on the program's ``PROGRAM_<id>`` value. We do
the same here: map every known ID to a semantic snake_case key, then
let HA's translation system render it in the user's locale.
"""

from __future__ import annotations

#: PROGRAM_<id> → semantic snake_case key (used as HA enum sensor option
#: AND as the translation key in ``translations/<lang>.json``).
PROGRAM_KEYS: dict[str, str] = {
    # Pressure cooking
    "PROGRAM_200066": "cooking_low_pressure",
    "PROGRAM_200067": "cooking_high_pressure",
    "PROGRAM_200068": "cooking_ultra_high_pressure",
    "PROGRAM_200156": "cooking_high_pressure_classic",
    "PROGRAM_200157": "cooking_low_pressure_classic",
    "PROGRAM_200163": "cooking_high_pressure_mini",
    "PROGRAM_200164": "cooking_low_pressure_mini",
    # Browning ("dorage")
    "PROGRAM_200063": "browning_high",
    "PROGRAM_200143": "browning_low",
    "PROGRAM_200144": "browning_medium",
    "PROGRAM_200159": "browning",
    "PROGRAM_200166": "browning_mini",
    # Simmering ("mijotage")
    "PROGRAM_200062": "simmering_medium",
    "PROGRAM_200064": "simmering_low",
    "PROGRAM_200142": "simmering_high",
    "PROGRAM_200160": "simmering",
    "PROGRAM_200167": "simmering_mini",
    # Steam
    "PROGRAM_200145": "steam_low",
    "PROGRAM_200146": "steam_high",
    # Slow cooking
    "PROGRAM_200158": "slow_cooking",
    "PROGRAM_200165": "slow_cooking_mini",
    # Keep warm
    "PROGRAM_200065": "keep_warm_iot",
    "PROGRAM_200161": "keep_warm",
    "PROGRAM_200168": "keep_warm_mini",
    # Reheat
    "PROGRAM_REHEAT": "reheat",
}

#: Every semantic key we know about, plus the ``"unknown"`` fallback
#: used when the firmware ships a ``PROGRAM_<id>`` we haven't yet
#: catalogued. No ``"none"`` sentinel — when there is no program at
#: all the sensor reads as Unavailable, in line with the rest of the
#: gated sensors.
PROGRAM_OPTIONS: tuple[str, ...] = (
    tuple(sorted(set(PROGRAM_KEYS.values()))) + ("unknown",)
)


def program_to_key(program_id: str | None) -> str | None:
    """Translate a ``PROGRAM_<id>`` to its semantic key.

    Returns ``None`` when no program is loaded (so the HA sensor
    becomes Unavailable), or ``"unknown"`` when there *is* a program
    but its ID isn't in our mapping. The raw firmware ID surfaces in
    debug logs; PRs welcome to add the mapping.
    """
    if not program_id:
        return None
    return PROGRAM_KEYS.get(program_id, "unknown")
