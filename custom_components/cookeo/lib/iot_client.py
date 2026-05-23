"""AWS IoT MQTT client tailored for the Cookeo Touch WiFi.

Uses ``awscrt`` / ``awsiotsdk`` over WebSockets with SigV4 (mandatory for
Cognito-federated identities — TLS-cert auth is reserved for the
appliance itself). Messages arrive on awscrt's internal threads; the
client marshals them through a user-supplied callable that is expected
to be thread-safe (in HA: ``hass.add_job`` or ``loop.call_soon_threadsafe``).

The class lifecycle is:

1. :meth:`connect` — wire up MQTT, subscribe to the relevant topics,
   trigger an initial shadow GET.
2. Messages flow through ``on_status`` / ``on_shadow`` / ``on_alert``.
3. :meth:`refresh_credentials` — call when AWS temp creds are close to
   expiring; reconnects with rotated creds.
4. :meth:`disconnect` — clean shutdown on unload.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from typing import Any

from .const import AWS_REGION, MQTT_ENDPOINT
from .models import ApplianceInfo, AwsCredentials, CookeoShadow, CookeoStatus

_LOGGER = logging.getLogger(__name__)

StatusCallback = Callable[[CookeoStatus], None]
ShadowCallback = Callable[[CookeoShadow], None]
AlertCallback = Callable[[dict[str, Any]], None]


class IotClient:
    """One MQTT connection per appliance.

    Multiple Cookeos under the same account could share a connection, but
    keeping one client per appliance keeps the code small and matches
    HA's one-coordinator-per-device pattern.
    """

    def __init__(
        self,
        appliance: ApplianceInfo,
        client_id_suffix: str | None = None,
    ) -> None:
        self._appliance = appliance
        # Mirror the kind of client_id the official mobile app uses: a
        # stable per-device suffix, no integration-specific branding.
        # AWS IoT lets multiple sessions share an identity but requires
        # unique client_ids; pad with a short random tail.
        suffix = client_id_suffix or os.urandom(2).hex()
        self._client_id = f"mqtt-{appliance.thing_name}-{suffix}"
        self._conn: Any = None
        self._status_cb: StatusCallback | None = None
        self._shadow_cb: ShadowCallback | None = None
        self._alert_cb: AlertCallback | None = None

    @property
    def appliance(self) -> ApplianceInfo:
        return self._appliance

    @property
    def is_connected(self) -> bool:
        return self._conn is not None

    def set_callbacks(
        self,
        on_status: StatusCallback,
        on_shadow: ShadowCallback,
        on_alert: AlertCallback,
    ) -> None:
        self._status_cb = on_status
        self._shadow_cb = on_shadow
        self._alert_cb = on_alert

    # ------------------------------------------------------------------

    def connect(self, creds: AwsCredentials) -> None:
        """Synchronously connect + subscribe + request initial shadow.

        Blocking — call from an executor.
        """
        from awscrt import auth as crt_auth, io as crt_io, mqtt
        from awsiot import mqtt_connection_builder

        provider = crt_auth.AwsCredentialsProvider.new_static(
            access_key_id=creds.access_key,
            secret_access_key=creds.secret_key,
            session_token=creds.session_token,
        )
        event_loop = crt_io.EventLoopGroup(1)
        resolver = crt_io.DefaultHostResolver(event_loop)
        bootstrap = crt_io.ClientBootstrap(event_loop, resolver)

        self._conn = mqtt_connection_builder.websockets_with_default_aws_signing(
            endpoint=MQTT_ENDPOINT,
            client_bootstrap=bootstrap,
            region=AWS_REGION,
            credentials_provider=provider,
            client_id=self._client_id,
            clean_session=True,
            keep_alive_secs=30,
            on_connection_interrupted=self._on_interrupted,
            on_connection_resumed=self._on_resumed,
        )
        _LOGGER.debug("connecting MQTT thing=%s", self._appliance.thing_name)
        self._conn.connect().result(timeout=15)
        _LOGGER.info("MQTT connected as %s", self._appliance.thing_name)

        # Topics covered by the IoT policy attached to our Cognito identity.
        topics = {
            f"{self._appliance.shadow_topic_prefix}/get/accepted": self._on_shadow_get,
            f"{self._appliance.shadow_topic_prefix}/get/rejected": self._on_shadow_rejected,
            f"{self._appliance.shadow_topic_prefix}/update/accepted": self._on_shadow_update,
            f"{self._appliance.shadow_topic_prefix}/update/delta": self._on_shadow_update,
            f"{self._appliance.shadow_topic_prefix}/update/documents": self._on_shadow_update,
            self._appliance.cooking_status_topic: self._on_cooking_status,
            self._appliance.alert_topic: self._on_alert_message,
        }
        for topic, handler in topics.items():
            self._conn.subscribe(topic, mqtt.QoS.AT_LEAST_ONCE, handler)[0].result(
                timeout=5
            )
            _LOGGER.debug("MQTT sub %s", topic)

        # Trigger a shadow GET so the consumer has firmware/connectivity
        # info available right away.
        self._conn.publish(
            f"{self._appliance.shadow_topic_prefix}/get",
            payload="",
            qos=mqtt.QoS.AT_LEAST_ONCE,
        )[0].result(timeout=5)

    def disconnect(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.disconnect().result(timeout=5)
        except Exception:  # noqa: BLE001
            _LOGGER.debug("MQTT disconnect raised; ignoring", exc_info=True)
        self._conn = None

    def refresh_credentials(self, creds: AwsCredentials) -> None:
        """Reconnect with new AWS temp creds (creds rotate every hour)."""
        self.disconnect()
        self.connect(creds)

    # ------------------------------------------------------- Topic handlers

    # awscrt invokes message handlers with all keyword arguments
    # (topic, payload, dup, qos, retain, …). We accept **kw and pluck
    # what we need — the signature is finicky, so don't switch back to
    # positional params.

    def _on_cooking_status(self, **kw: Any) -> None:
        payload: bytes = kw["payload"]
        cb = self._status_cb
        if cb is None:
            return
        try:
            data = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            _LOGGER.warning("cookingStatus: undecodable payload %s", payload[:80])
            return
        try:
            cb(CookeoStatus.from_json(data))
        except Exception:  # noqa: BLE001
            _LOGGER.exception("cookingStatus callback failed")

    def _on_shadow_get(self, **kw: Any) -> None:
        self._dispatch_shadow(kw["payload"])

    def _on_shadow_rejected(self, **kw: Any) -> None:
        # Most common reason: shadow doesn't exist yet (device never online).
        payload: bytes = kw["payload"]
        _LOGGER.info("shadow get rejected: %s", payload[:120])

    def _on_shadow_update(self, **kw: Any) -> None:
        self._dispatch_shadow(kw["payload"])

    def _dispatch_shadow(self, payload: bytes) -> None:
        cb = self._shadow_cb
        if cb is None:
            return
        try:
            data = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        # update/documents wraps things in {"previous": …, "current": …}.
        if isinstance(data.get("current"), dict):
            data = data["current"]
        try:
            cb(CookeoShadow.from_json(data))
        except Exception:  # noqa: BLE001
            _LOGGER.exception("shadow callback failed")

    def _on_alert_message(self, **kw: Any) -> None:
        payload: bytes = kw["payload"]
        cb = self._alert_cb
        if cb is None:
            return
        try:
            data = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return
        try:
            cb(data)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("alert callback failed")

    # ------------------------------------------------------ Conn lifecycle

    def _on_interrupted(self, **kwargs: Any) -> None:
        _LOGGER.info("MQTT interrupted: %s", kwargs.get("error"))

    def _on_resumed(self, **kwargs: Any) -> None:
        _LOGGER.info("MQTT resumed: %s", kwargs.get("return_code"))
