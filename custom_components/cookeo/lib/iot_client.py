"""AWS IoT MQTT connection to a Cookeo."""

from collections.abc import Callable
import json
import logging
import os
from typing import Any

from awscrt import auth, mqtt
from awsiot import mqtt_connection_builder

from .const import AWS_REGION, MQTT_ENDPOINT
from .exceptions import CookeoError
from .models import Appliance, AwsCredentials, CookeoShadow, CookeoStatus

_LOGGER = logging.getLogger(__name__)


class IotClient:
    """Streams the cooking status and shadow of one Cookeo."""

    def __init__(
        self,
        appliance: Appliance,
        on_status: Callable[[CookeoStatus], None],
        on_shadow: Callable[[CookeoShadow], None],
        on_alert: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """Initialize the client, callbacks run on the MQTT thread."""
        self._appliance = appliance
        self._on_status = on_status
        self._on_shadow = on_shadow
        self._on_alert = on_alert
        # AWS IoT needs a distinct client id for each connection of an identity.
        self._client_id = f"mqtt-{appliance.thing_name}-{os.urandom(2).hex()}"
        self._connection: mqtt.Connection | None = None

    def connect(self, credentials: AwsCredentials) -> None:
        """Connect, subscribe and request the shadow, blocking."""
        self._connection = mqtt_connection_builder.websockets_with_default_aws_signing(
            endpoint=MQTT_ENDPOINT,
            region=AWS_REGION,
            credentials_provider=auth.AwsCredentialsProvider.new_static(
                access_key_id=credentials.access_key,
                secret_access_key=credentials.secret_key,
                session_token=credentials.session_token,
            ),
            client_id=self._client_id,
            clean_session=True,
            keep_alive_secs=30,
            on_connection_interrupted=self._on_interrupted,
            on_connection_resumed=self._on_resumed,
        )
        shadow = self._appliance.shadow_topic
        handlers = {
            f"{shadow}/get/accepted": self._handle_shadow,
            f"{shadow}/update/accepted": self._handle_shadow,
            f"{shadow}/update/documents": self._handle_shadow,
            self._appliance.cooking_status_topic: self._handle_status,
        }
        if self._on_alert is not None:
            handlers[self._appliance.alert_topic] = self._handle_alert
        try:
            self._connection.connect().result(timeout=15)
            for topic, handler in handlers.items():
                self._connection.subscribe(topic, mqtt.QoS.AT_LEAST_ONCE, handler)[
                    0
                ].result(timeout=5)
            self._connection.publish(
                f"{shadow}/get", payload="", qos=mqtt.QoS.AT_LEAST_ONCE
            )[0].result(timeout=5)
        except Exception as err:
            raise CookeoError(f"MQTT connection failed: {err}") from err
        _LOGGER.debug("Connected to %s", self._appliance.thing_name)
        self.request_status()

    def request_status(self) -> None:
        """Ask the Cookeo to send its cooking status."""
        if self._connection is not None:
            self._connection.publish(
                self._appliance.status_request_topic,
                payload="{}",
                qos=mqtt.QoS.AT_LEAST_ONCE,
            )

    def disconnect(self) -> None:
        """Disconnect, blocking."""
        if self._connection is None:
            return
        try:
            self._connection.disconnect().result(timeout=5)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("MQTT disconnect failed", exc_info=True)
        self._connection = None

    def refresh_credentials(self, credentials: AwsCredentials) -> None:
        """Reconnect with new AWS credentials, blocking."""
        self.disconnect()
        self.connect(credentials)

    def _handle_status(self, payload: bytes, **kwargs: Any) -> None:
        if (data := _decode(payload)) is not None:
            self._on_status(CookeoStatus.from_json(data))

    def _handle_shadow(self, payload: bytes, **kwargs: Any) -> None:
        if (data := _decode(payload)) is not None:
            self._on_shadow(CookeoShadow.from_json(data))

    def _handle_alert(self, payload: bytes, **kwargs: Any) -> None:
        if self._on_alert is not None and (data := _decode(payload)) is not None:
            self._on_alert(data)

    def _on_interrupted(self, error: Exception, **kwargs: Any) -> None:
        _LOGGER.info("MQTT connection interrupted: %s", error)

    def _on_resumed(self, return_code: mqtt.ConnectReturnCode, **kwargs: Any) -> None:
        _LOGGER.info("MQTT connection resumed: %s", return_code)


def _decode(payload: bytes) -> dict[str, Any] | None:
    """Decode a JSON MQTT payload."""
    try:
        data = json.loads(payload)
    except ValueError:
        _LOGGER.warning("Ignoring undecodable MQTT payload: %r", payload[:80])
        return None
    return data if isinstance(data, dict) else None
