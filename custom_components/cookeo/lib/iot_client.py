"""AWS IoT MQTT connection to the appliances of an account."""

from collections.abc import Callable
from functools import partial
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
    """Streams the cooking status and shadow of the appliances of an account."""

    def __init__(
        self,
        on_status: Callable[[Appliance, CookeoStatus], None],
        on_shadow: Callable[[Appliance, CookeoShadow], None],
    ) -> None:
        """Initialize the client, callbacks run on the MQTT thread."""
        self._on_status = on_status
        self._on_shadow = on_shadow
        # AWS IoT needs a distinct client id for each connection of an identity.
        self._client_id = f"mqtt-{os.urandom(8).hex()}"
        self._connection: mqtt.Connection | None = None

    def connect(self, credentials: AwsCredentials, appliances: list[Appliance]) -> None:
        """Connect, subscribe to the appliances and request their state, blocking."""
        connection = mqtt_connection_builder.websockets_with_default_aws_signing(
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
        self._connection = connection
        try:
            connection.connect().result(timeout=15)
            for appliance in appliances:
                _subscribe(
                    connection, appliance, self._handle_status, self._handle_shadow
                )
        except Exception as err:
            raise CookeoError(f"MQTT connection failed: {err}") from err
        _LOGGER.debug("Connected to %s appliances", len(appliances))
        for appliance in appliances:
            self.request_status(appliance)

    def request_status(self, appliance: Appliance) -> None:
        """Ask the appliance to send its cooking status."""
        if self._connection is not None:
            self._connection.publish(
                appliance.status_request_topic,
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

    def reconnect(
        self, credentials: AwsCredentials, appliances: list[Appliance]
    ) -> None:
        """Reconnect with new AWS credentials and appliances, blocking."""
        self.disconnect()
        self.connect(credentials, appliances)

    def _handle_status(
        self, appliance: Appliance, payload: bytes, **kwargs: Any
    ) -> None:
        if (data := _decode(payload)) is not None:
            self._on_status(appliance, CookeoStatus.from_json(data))

    def _handle_shadow(
        self, appliance: Appliance, payload: bytes, **kwargs: Any
    ) -> None:
        if (data := _decode(payload)) is not None:
            self._on_shadow(appliance, CookeoShadow.from_json(data))

    def _on_interrupted(self, error: Exception, **kwargs: Any) -> None:
        _LOGGER.info("MQTT connection interrupted: %s", error)

    def _on_resumed(self, return_code: mqtt.ConnectReturnCode, **kwargs: Any) -> None:
        _LOGGER.info("MQTT connection resumed: %s", return_code)


def _subscribe(
    connection: mqtt.Connection,
    appliance: Appliance,
    on_status: Callable[..., None],
    on_shadow: Callable[..., None],
) -> None:
    """Subscribe to the topics of an appliance and request its shadow, blocking."""
    shadow = appliance.shadow_topic
    handlers = {
        f"{shadow}/get/accepted": partial(on_shadow, appliance),
        f"{shadow}/update/accepted": partial(on_shadow, appliance),
        f"{shadow}/update/documents": partial(on_shadow, appliance),
        appliance.cooking_status_topic: partial(on_status, appliance),
    }
    for topic, handler in handlers.items():
        connection.subscribe(topic, mqtt.QoS.AT_LEAST_ONCE, handler)[0].result(
            timeout=5
        )
    connection.publish(f"{shadow}/get", payload="", qos=mqtt.QoS.AT_LEAST_ONCE)[
        0
    ].result(timeout=5)


def _decode(payload: bytes) -> dict[str, Any] | None:
    """Decode a JSON MQTT payload."""
    try:
        data = json.loads(payload)
    except ValueError:
        _LOGGER.warning("Ignoring undecodable MQTT payload: %r", payload[:80])
        return None
    return data if isinstance(data, dict) else None
