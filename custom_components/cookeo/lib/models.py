"""Data models for the Cookeo WiFi integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

#: Phases where ``progress`` is meaningful. Outside of these the
#: appliance stops emitting the field but the previous value would stay
#: in place forever — we explicitly hide it. The canonical values come
#: from ``vn.a$b`` in the Moulinex schema (no ``preheating``).
PROGRESS_PHASES = frozenset({"warming", "depressurization"})

#: Phases where ``operation_start`` is meaningful (= a live cooking /
#: keep-warm session is in progress). Outside of these we mask the
#: timestamp so HA doesn't display "running since 2 hours" once the
#: appliance is back to idle.
ACTIVE_PHASES = frozenset(
    {"cooking", "keep_warm", "warming", "depressurization", "paused", "delayed"}
)

#: Phases where ``currentOperation`` (program + target parameters) is
#: relevant. Includes ``ready`` (program picked but not started),
#: ``alert`` (something flagged mid-cycle), and ``done`` (so the user
#: can see *which* program just finished). Hidden in idle/menu/off.
PROGRAM_PHASES = ACTIVE_PHASES | frozenset({"ready", "alert", "done"})


@dataclass(slots=True)
class Tokens:
    """SEB OAuth tokens."""

    access_token: str
    id_token: str
    refresh_token: str | None = None
    #: Unix seconds (best-effort, parsed from ``issued_at`` or now())
    issued_at: int = 0
    #: Unix seconds, parsed from the id_token's ``exp`` claim.
    id_token_exp: int = 0


@dataclass(slots=True)
class AwsCredentials:
    """Temporary AWS credentials obtained from Cognito federation."""

    access_key: str
    secret_key: str
    session_token: str
    identity_id: str
    expiration: int  # unix seconds


@dataclass(slots=True)
class ApplianceInfo:
    """One paired Cookeo appliance as returned by ``/profiles/me``."""

    appliance_id: str      # e.g. "APPLIANCE_1090"
    name: str              # e.g. "Cookeo Touch Wifi"
    thing_type: str        # e.g. "COO001"
    thing_uuid: str        # e.g. "c3ec79e0-ef7d-11ef-…"
    functional_id: str     # e.g. "14692971" (DCP-side instance ID)
    rcu_asset_id: str      # Salesforce asset ID
    cmmf: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def thing_name(self) -> str:
        """AWS IoT thing name. Always ``{thingType}-{thingUuid}``."""
        return f"{self.thing_type}-{self.thing_uuid}"

    @property
    def cooking_status_topic(self) -> str:
        return f"{self.thing_type}/{self.thing_uuid}/cookingStatus"

    @property
    def alert_topic(self) -> str:
        return f"{self.thing_type}/{self.thing_uuid}/alert"

    @property
    def shadow_topic_prefix(self) -> str:
        return f"$aws/things/{self.thing_name}/shadow"


@dataclass(slots=True)
class CookeoProgram:
    """Cooking program (recipe / manual mode preset)."""

    key: str               # e.g. "PROGRAM_200064"
    name: str              # e.g. "Low Stew Cooking"


@dataclass(slots=True)
class CookeoNotification:
    """Notification embedded inside a ``cookingStatus`` event.

    The full schema (per ``vn.b`` in the Moulinex app):

    - ``code``: numeric ID (e.g. ``"30027"``)
    - ``severity``: ``"notif"`` / ``"warning"`` / ``"error"``
    - ``title``: **localized** title — the cloud renders it server-side
      according to the user's account locale
    - ``description``: **localized** description (same)
    - ``titleKey``: i18n key (e.g. ``"some_notification_key"``)
    - ``groupId`` / ``updateId``: opaque grouping identifiers
    - ``time_to_live``: TTL hint in seconds (presumed)
    """

    code: str
    severity: str
    time_to_live: int | None = None
    title: str | None = None
    description: str | None = None
    title_key: str | None = None
    group_id: str | None = None
    update_id: str | None = None


def _norm_status(raw: str | None) -> str | None:
    if raw is None:
        return None
    return raw.strip().lower().replace(" ", "_").replace("-", "_")


@dataclass(slots=True)
class CookeoStatus:
    """Decoded ``cookingStatus`` MQTT event.

    Fields are all optional because the appliance sometimes pushes
    partial updates (e.g. ``{currentStatus: "done", version: 10}``
    without ``mode`` or ``currentOperation``). Merge with the previous
    state on update.
    """

    #: Last observed cooking phase ("ready", "warming", "cooking",
    #: "keep warm", "done", "alert", "preheat", "depressuring", …).
    #: Treat as opaque and lowercase-snake-case it for HA enum sensor.
    current_status: str | None = None
    mode: str | None = None        # "manual" | "program" | "recipe" | "ingredient_cooking"
    progress: int | None = None    # 0-100 during warming/depressurization
    elapsed_time: int | None = None  # seconds during cooking/keep_warm
    #: Seconds until the appliance reaches the end of the current
    #: operation. The Cookeo's cloud already computes this, so we
    #: prefer it over ``operation_start + duration_target`` math.
    remaining_time: int | None = None
    program: CookeoProgram | None = None
    notification: CookeoNotification | None = None
    #: Free-text localized alert description (populated when
    #: ``currentStatus`` == ``"alert"``).
    alert_description: str | None = None
    #: Free-text error info (populated when something goes wrong).
    error: str | None = None
    #: Recipe ID + title when running a recipe (vs manual mode).
    recipe_id: str | None = None
    recipe_title: str | None = None
    recipe_step_index: int | None = None
    event_date: str | None = None  # ISO-8601 with TZ offset
    operation_start: str | None = None
    version: int | None = None
    #: Raw ``currentOperation.parameters`` list (kept as-is, e.g.
    #: ``[{"type": "DURATION", "value": "1200"}]``). Used to compute
    #: :attr:`duration_target` and :attr:`display_end_time_dt`.
    parameters: list[dict[str, Any]] | None = None

    def merge(self, other: "CookeoStatus") -> "CookeoStatus":
        """Return a new status with non-None fields from ``other`` overriding."""
        return CookeoStatus(
            current_status=other.current_status or self.current_status,
            mode=other.mode or self.mode,
            progress=other.progress if other.progress is not None else self.progress,
            elapsed_time=other.elapsed_time if other.elapsed_time is not None else self.elapsed_time,
            remaining_time=other.remaining_time if other.remaining_time is not None else self.remaining_time,
            program=other.program or self.program,
            notification=other.notification,  # always replace (transient)
            alert_description=other.alert_description or self.alert_description,
            error=other.error or self.error,
            recipe_id=other.recipe_id or self.recipe_id,
            recipe_title=other.recipe_title or self.recipe_title,
            recipe_step_index=other.recipe_step_index if other.recipe_step_index is not None else self.recipe_step_index,
            event_date=other.event_date or self.event_date,
            operation_start=other.operation_start or self.operation_start,
            version=other.version if other.version is not None else self.version,
            parameters=other.parameters if other.parameters is not None else self.parameters,
        )

    # --- Phase-gated derived values -----------------------------------
    # The appliance emits ``progress``/``elapsedTime`` only at phase
    # transitions (never as a live tick), and ``currentOperation`` info
    # sticks around even after the phase is done. We expose computed
    # properties that return ``None`` outside of the relevant phase so
    # HA sensors show "unavailable" rather than stale values.

    @property
    def normalized_status(self) -> str | None:
        return _norm_status(self.current_status)

    @property
    def display_progress(self) -> int | None:
        # ``None`` outside the live-progress phases → HA renders it as
        # "Inconnu", which reads better than a deceptive 0% when the
        # value is genuinely undetermined.
        if self.normalized_status not in PROGRESS_PHASES:
            return None
        return self.progress

    @property
    def display_remaining_time(self) -> int | None:
        """Remaining seconds.

        Visible across the full program lifecycle (ready → active →
        done) so users see "X minutes left" the moment they load a
        program with a duration, not only once it actually starts.
        """
        if self.normalized_status not in PROGRAM_PHASES:
            return None
        return self.remaining_time

    @property
    def display_target_temperature(self) -> int | None:
        """Target temperature — visible whenever a program with an
        explicit temperature is loaded (ready → active → done)."""
        if self.normalized_status not in PROGRAM_PHASES:
            return None
        return self.temperature_target

    @property
    def display_program_id(self) -> str | None:
        # Gate to relevant phases so a finished program doesn't keep
        # showing forever (the Cookeo doesn't re-emit a "no program"
        # event when it transitions out of ``done``).
        if self.normalized_status not in PROGRAM_PHASES:
            return None
        return self.program.key if self.program else None

    @property
    def display_recipe_title(self) -> str | None:
        """Recipe title — only meaningful while in recipe mode."""
        if self.mode != "recipe":
            return None
        if self.normalized_status not in PROGRAM_PHASES:
            return None
        return self.recipe_title

    @property
    def display_recipe_step(self) -> int | None:
        """Recipe step index — only meaningful while in recipe mode."""
        if self.mode != "recipe":
            return None
        if self.normalized_status not in PROGRAM_PHASES:
            return None
        return self.recipe_step_index

    @property
    def operation_start_dt(self) -> datetime | None:
        """Parse ``operationStart`` to a *naive* local datetime.

        The Cookeo firmware tags timestamps with a fixed ``+01`` offset
        regardless of DST (so in summer the absolute UTC instant is off
        by an hour). We trust the wall-clock portion (which matches the
        user's local time, since the device is in their kitchen) and
        drop the broken tz info — the HA sensor layer then attaches the
        user's configured timezone before exposing the value.

        Stays visible across the full program lifecycle so the "started
        at HH:MM" timestamp survives until the next program loads.
        """
        if self.normalized_status not in PROGRAM_PHASES:
            return None
        if not self.operation_start:
            return None
        try:
            parsed = datetime.fromisoformat(self.operation_start)
        except ValueError:
            return None
        return parsed.replace(tzinfo=None)

    def _param_int(self, kind: str) -> int | None:
        """Return the ``int(value)`` of the first parameter of given type.

        ``0`` is treated as "no value set" because the Cookeo reports
        zero for unset slots (e.g. keep-warm program has no duration).
        """
        if not self.parameters:
            return None
        for param in self.parameters:
            if not isinstance(param, dict) or param.get("type") != kind:
                continue
            try:
                value = int(param.get("value", 0))
            except (TypeError, ValueError):
                return None
            return value or None
        return None

    @property
    def duration_target(self) -> int | None:
        """Target cooking duration in seconds, or ``None``."""
        return self._param_int("DURATION")

    @property
    def temperature_target(self) -> int | None:
        """Target temperature in °C, or ``None``.

        Only meaningful for programs that take an explicit temperature
        (currently unobserved in capture but slot defined in the SDK).
        """
        return self._param_int("TEMPERATURE")

    @property
    def keep_warm_after(self) -> bool | None:
        """Whether keep-warm-after-cooking is enabled for this run."""
        v = self._param_int("KEEP_WARM")
        return None if v is None else bool(v)

    @property
    def display_end_time_dt(self) -> datetime | None:
        """Estimated end-of-cooking *naive* local datetime.

        ``operation_start_dt + duration_target`` — same naive-localtime
        convention as :attr:`operation_start_dt`. The HA sensor layer
        attaches the user's timezone.
        """
        start = self.operation_start_dt
        target = self.duration_target
        if start is None or target is None:
            return None
        from datetime import timedelta
        return start + timedelta(seconds=target)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "CookeoStatus":
        prog = None
        params = None
        op = payload.get("currentOperation") or {}
        if isinstance(op.get("program"), dict):
            prog = CookeoProgram(
                key=op["program"].get("key", ""),
                name=op["program"].get("name", ""),
            )
        if isinstance(op.get("parameters"), list):
            params = op["parameters"]

        notif = None
        nraw = payload.get("notification")
        if isinstance(nraw, dict):
            notif = CookeoNotification(
                code=str(nraw.get("code", "")),
                severity=str(nraw.get("severity", "")),
                time_to_live=nraw.get("timeToLive"),
                title=nraw.get("title"),
                description=nraw.get("description"),
                title_key=nraw.get("titleKey"),
                group_id=nraw.get("groupId"),
                update_id=nraw.get("updateId"),
            )

        recipe_id = recipe_title = None
        recipe_raw = payload.get("currentRecipe")
        if isinstance(recipe_raw, dict):
            recipe_id = recipe_raw.get("functionalId")
            recipe_title = recipe_raw.get("title")

        step_index = None
        step_raw = payload.get("currentStep")
        if isinstance(step_raw, dict):
            step_index = step_raw.get("stepIndex")

        return cls(
            current_status=payload.get("currentStatus"),
            mode=payload.get("mode"),
            progress=payload.get("progress"),
            elapsed_time=payload.get("elapsedTime"),
            remaining_time=payload.get("remainingTime"),
            program=prog,
            notification=notif,
            alert_description=payload.get("alertDescr"),
            error=payload.get("error"),
            recipe_id=recipe_id,
            recipe_title=recipe_title,
            recipe_step_index=step_index,
            event_date=payload.get("eventDate"),
            operation_start=payload.get("operationStart"),
            version=payload.get("version"),
            parameters=params,
        )


@dataclass(slots=True)
class CookeoFirmware:
    """Firmware version info from the AWS Shadow's reported state."""

    ui_version: str | None = None
    wifi_version: str | None = None
    update_priority: str | None = None  # "low" / "high"

    def merge(self, other: "CookeoFirmware") -> "CookeoFirmware":
        return CookeoFirmware(
            ui_version=other.ui_version or self.ui_version,
            wifi_version=other.wifi_version or self.wifi_version,
            update_priority=other.update_priority or self.update_priority,
        )


@dataclass(slots=True)
class CookeoShadow:
    """Decoded subset of the appliance shadow document.

    All fields default to ``None`` so that partial updates (which we
    receive on ``update/accepted`` etc.) can be merged into the cached
    state without clobbering known values with "unknown".
    """

    connected: bool | None = None
    last_connection: int | None = None     # ms epoch
    last_disconnection: int | None = None  # ms epoch
    synchronizing: bool | None = None
    firmware: CookeoFirmware = field(default_factory=CookeoFirmware)

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "CookeoShadow":
        """Parse the ``reported`` block from any shadow topic payload.

        Handles the three shapes we subscribe to:

        - ``get/accepted``: ``{state: {reported: {...}, desired: ...}, ...}``
        - ``update/accepted``: ``{state: {reported: <partial>, ...}, ...}``
        - ``update/documents``: ``{previous: {...}, current: {state: {reported: ...}}, ...}``

        Other shapes (``update/delta``, ``update/rejected``) return an
        empty shadow that is a no-op when merged.
        """
        reported = _extract_reported(payload)
        if reported is None:
            return cls()  # nothing parseable — caller merges, no-op

        status = reported.get("status") or {}
        fw_raw = reported.get("firmware") or {}
        files = fw_raw.get("files") or []
        fw = CookeoFirmware(update_priority=fw_raw.get("priority"))
        for f in files:
            if not isinstance(f, dict):
                continue
            if f.get("type") == "ui":
                fw.ui_version = f.get("version")
            elif f.get("type") == "wifi":
                fw.wifi_version = f.get("version")
        return cls(
            connected=status.get("connected"),
            last_connection=status.get("lastConnection"),
            last_disconnection=status.get("lastDisconnection"),
            synchronizing=status.get("synchronizing"),
            firmware=fw,
        )

    def merge(self, other: "CookeoShadow") -> "CookeoShadow":
        """Return a new shadow with non-None fields from ``other`` overriding."""
        return CookeoShadow(
            connected=other.connected if other.connected is not None else self.connected,
            last_connection=other.last_connection or self.last_connection,
            last_disconnection=other.last_disconnection or self.last_disconnection,
            synchronizing=other.synchronizing if other.synchronizing is not None else self.synchronizing,
            firmware=self.firmware.merge(other.firmware),
        )


def _extract_reported(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Pull the ``reported`` dict out of any of the shadow topic shapes."""
    if isinstance(payload.get("current"), dict):
        # update/documents wrapper
        payload = payload["current"]
    state = payload.get("state")
    if not isinstance(state, dict):
        return None
    reported = state.get("reported")
    return reported if isinstance(reported, dict) else None
