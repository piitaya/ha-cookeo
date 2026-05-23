"""AWS Cognito federation for SEB Salesforce-issued ID tokens.

The Cognito Identity Pool is configured with SEB Salesforce as a
federated OpenID Connect provider (the provider key is the issuer of
the id_token, i.e. ``account.groupeseb.com``). Exchanging the SEB
id_token for AWS temp credentials gives us a principal we can use to:

- attach the IoT policy that owns the appliance's MQTT topics
- connect to AWS IoT MQTT over WebSockets with SigV4

We use ``boto3.client("cognito-identity")`` directly. The boto3 client
is synchronous; the integration runs it inside an executor.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from .const import AWS_REGION, COGNITO_IDENTITY_POOL, COGNITO_PROVIDER
from .models import AwsCredentials

if TYPE_CHECKING:  # pragma: no cover
    from botocore.client import BaseClient


class CognitoError(Exception):
    """Raised when Cognito refuses the federation request."""


def federate(id_token: str, client: "BaseClient | None" = None) -> AwsCredentials:
    """Federate an id_token with Cognito and return AWS temp credentials.

    Blocking call (boto3 is synchronous). The caller is expected to run
    this in an executor when used from an async context.
    """
    import boto3  # imported lazily so the integration doesn't pay the
                  # ~150ms boto3 import unless the user has a WiFi unit
    from botocore.exceptions import BotoCoreError, ClientError

    client = client or boto3.client("cognito-identity", region_name=AWS_REGION)
    logins = {COGNITO_PROVIDER: id_token}
    try:
        ident = client.get_id(
            IdentityPoolId=COGNITO_IDENTITY_POOL, Logins=logins
        )
        creds = client.get_credentials_for_identity(
            IdentityId=ident["IdentityId"], Logins=logins
        )["Credentials"]
    except (ClientError, BotoCoreError) as err:
        raise CognitoError(str(err)) from err

    return AwsCredentials(
        access_key=creds["AccessKeyId"],
        secret_key=creds["SecretKey"],
        session_token=creds["SessionToken"],
        identity_id=ident["IdentityId"],
        expiration=int(creds["Expiration"].timestamp()),
    )


def is_expired(creds: AwsCredentials, leeway_seconds: int = 120) -> bool:
    """Return True when temp credentials are about to expire."""
    return creds.expiration - leeway_seconds < int(time.time())
