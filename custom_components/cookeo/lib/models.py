"""Data models for the Cookeo library."""

from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timedelta
import re
from typing import Any, Self

STATUSES = (
    "stopped",
    "ready",
    "paused",
    "warming",
    "cooking",
    "depressurization",
    "keep_warm",
    "done",
    "delayed",
    "alert",
)
MODES = ("manual", "program", "recipe", "ingredient_cooking")

PROGRESS_STATUSES = frozenset({"warming", "depressurization"})
PROGRAM_STATUSES = frozenset(
    {
        "ready",
        "warming",
        "cooking",
        "depressurization",
        "paused",
        "delayed",
        "keep_warm",
        "alert",
        "done",
    }
)


def _merge[T](current: T, update: T) -> T:
    """Return ``current`` with the fields set in ``update`` applied."""
    return replace(
        current,
        **{
            f.name: value
            for f in fields(update)
            if (value := getattr(update, f.name)) not in (None, "")
        },
    )


@dataclass(slots=True)
class Tokens:
    """SEB OAuth tokens."""

    access_token: str
    id_token: str
    refresh_token: str


@dataclass(slots=True)
class AwsCredentials:
    """Temporary AWS credentials from Cognito."""

    access_key: str
    secret_key: str
    session_token: str
    expiration: int


@dataclass(slots=True)
class Appliance:
    """Cookeo linked to the SEB account."""

    name: str
    thing_type: str
    thing_uuid: str
    cmmf: str | None = None

    @property
    def thing_name(self) -> str:
        """Return the AWS IoT thing name."""
        return f"{self.thing_type}-{self.thing_uuid}"

    @property
    def cooking_status_topic(self) -> str:
        """Return the MQTT topic of the cooking status."""
        return f"{self.thing_type}/{self.thing_uuid}/cookingStatus"

    @property
    def status_request_topic(self) -> str:
        """Return the MQTT topic that makes the Cookeo send its cooking status."""
        return f"{self.thing_type}/{self.thing_uuid}/getCookingStatus"

    @property
    def alert_topic(self) -> str:
        """Return the MQTT topic of the alerts."""
        return f"{self.thing_type}/{self.thing_uuid}/alert"

    @property
    def shadow_topic(self) -> str:
        """Return the MQTT topic prefix of the AWS IoT shadow."""
        return f"$aws/things/{self.thing_name}/shadow"


@dataclass(slots=True)
class CookeoNotification:
    """Notification sent along a cooking status."""

    code: str
    severity: str
    time_to_live: int | None = None
    title: str | None = None
    description: str | None = None
    title_key: str | None = None


@dataclass(slots=True)
class CookeoStatus:
    """Cooking status, filled in by partial MQTT updates."""

    current_status: str | None = None
    mode: str | None = None
    progress: int | None = None
    elapsed_time: int | None = None
    remaining_time: int | None = None
    program_key: str | None = None
    notification: CookeoNotification | None = None
    alert_description: str | None = None
    error: str | None = None
    recipe_id: str | None = None
    recipe_title: str | None = None
    recipe_step_index: int | None = None
    event_date: str | None = None
    operation_start: str | None = None
    version: int | None = None
    parameters: list[dict[str, Any]] | None = None

    def merge(self, update: Self) -> Self:
        """Return this status updated with the fields set in ``update``."""
        return replace(_merge(self, update), notification=update.notification)

    @property
    def normalized_status(self) -> str | None:
        """Return the status in snake case, ``keep warm`` becomes ``keep_warm``."""
        if self.current_status is None:
            return None
        return self.current_status.strip().lower().replace(" ", "_").replace("-", "_")

    @property
    def normalized_mode(self) -> str | None:
        """Return the mode in snake case, ``ingredientCooking`` becomes ``ingredient_cooking``."""
        if self.mode is None:
            return None
        return re.sub(r"(?<!^)(?=[A-Z])", "_", self.mode.strip()).lower()

    @property
    def has_program(self) -> bool:
        """Return True when a program is loaded, running or just finished."""
        return self.normalized_status in PROGRAM_STATUSES

    @property
    def in_recipe(self) -> bool:
        """Return True when a recipe is loaded, running or just finished."""
        return self.has_program and self.normalized_mode == "recipe"

    @property
    def display_progress(self) -> int | None:
        """Return the progress while warming up or releasing pressure."""
        if self.normalized_status not in PROGRESS_STATUSES:
            return None
        return self.progress

    @property
    def display_remaining_time(self) -> int | None:
        """Return the remaining time of the program in seconds."""
        return self.remaining_time if self.has_program else None

    @property
    def display_target_temperature(self) -> int | None:
        """Return the target temperature of the program in °C."""
        return self.temperature_target if self.has_program else None

    @property
    def display_program_key(self) -> str | None:
        """Return the key of the program."""
        return self.program_key if self.has_program else None

    @property
    def display_recipe_title(self) -> str | None:
        """Return the title of the recipe."""
        return self.recipe_title if self.in_recipe else None

    @property
    def display_recipe_step(self) -> int | None:
        """Return the current step of the recipe."""
        return self.recipe_step_index if self.in_recipe else None

    @property
    def display_operation_start(self) -> datetime | None:
        """Return when the program started, as a naive local time."""
        if not self.has_program or not self.operation_start:
            return None
        try:
            started = datetime.fromisoformat(self.operation_start)
        except ValueError:
            return None
        # The firmware always sends +01:00, even in summer: keep the wall clock only.
        return started.replace(tzinfo=None)

    @property
    def display_end_time(self) -> datetime | None:
        """Return when the program ends, as a naive local time."""
        start = self.display_operation_start
        duration = self.duration_target
        if start is None or duration is None:
            return None
        return start + timedelta(seconds=duration)

    @property
    def duration_target(self) -> int | None:
        """Return the target duration of the program in seconds."""
        return self._parameter("DURATION")

    @property
    def temperature_target(self) -> int | None:
        """Return the target temperature of the program in °C."""
        return self._parameter("TEMPERATURE")

    def _parameter(self, kind: str) -> int | None:
        """Return a program parameter, the Cookeo sends 0 for unset ones."""
        for parameter in self.parameters or []:
            if not isinstance(parameter, dict) or parameter.get("type") != kind:
                continue
            try:
                return int(parameter.get("value", 0)) or None
            except (TypeError, ValueError):
                return None
        return None

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Self:
        """Parse a ``cookingStatus`` MQTT message."""
        operation = payload.get("currentOperation") or {}
        program = operation.get("program")
        parameters = operation.get("parameters")
        notification = payload.get("notification")
        recipe = payload.get("currentRecipe")
        step = payload.get("currentStep")
        return cls(
            current_status=payload.get("currentStatus"),
            mode=payload.get("mode"),
            progress=payload.get("progress"),
            elapsed_time=payload.get("elapsedTime"),
            remaining_time=payload.get("remainingTime"),
            program_key=program.get("key") if isinstance(program, dict) else None,
            notification=(
                CookeoNotification(
                    code=str(notification.get("code", "")),
                    severity=str(notification.get("severity", "")),
                    time_to_live=notification.get("timeToLive"),
                    title=notification.get("title"),
                    description=notification.get("description"),
                    title_key=notification.get("titleKey"),
                )
                if isinstance(notification, dict)
                else None
            ),
            alert_description=payload.get("alertDescr"),
            error=payload.get("error"),
            recipe_id=recipe.get("functionalId") if isinstance(recipe, dict) else None,
            recipe_title=recipe.get("title") if isinstance(recipe, dict) else None,
            recipe_step_index=step.get("stepIndex") if isinstance(step, dict) else None,
            event_date=payload.get("eventDate"),
            operation_start=payload.get("operationStart"),
            version=payload.get("version"),
            parameters=parameters if isinstance(parameters, list) else None,
        )


@dataclass(slots=True)
class CookeoFirmware:
    """Firmware versions reported in the shadow."""

    ui_version: str | None = None
    wifi_version: str | None = None
    update_priority: str | None = None


@dataclass(slots=True)
class CookeoShadow:
    """Reported state of the AWS IoT shadow, filled in by partial updates."""

    connected: bool | None = None
    last_connection: int | None = None
    last_disconnection: int | None = None
    synchronizing: bool | None = None
    firmware: CookeoFirmware = field(default_factory=CookeoFirmware)

    def merge(self, update: Self) -> Self:
        """Return this shadow updated with the fields set in ``update``."""
        return replace(
            _merge(self, update), firmware=_merge(self.firmware, update.firmware)
        )

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> Self:
        """Parse a shadow ``get``, ``update`` or ``documents`` MQTT message."""
        if isinstance(payload.get("current"), dict):
            payload = payload["current"]
        state = payload.get("state")
        reported = state.get("reported") if isinstance(state, dict) else None
        if not isinstance(reported, dict):
            return cls()

        status = reported.get("status") or {}
        firmware = reported.get("firmware") or {}
        versions = {
            file.get("type"): file.get("version")
            for file in firmware.get("files") or []
            if isinstance(file, dict)
        }
        return cls(
            connected=status.get("connected"),
            last_connection=status.get("lastConnection"),
            last_disconnection=status.get("lastDisconnection"),
            synchronizing=status.get("synchronizing"),
            firmware=CookeoFirmware(
                ui_version=versions.get("ui"),
                wifi_version=versions.get("wifi"),
                update_priority=firmware.get("priority"),
            ),
        )
