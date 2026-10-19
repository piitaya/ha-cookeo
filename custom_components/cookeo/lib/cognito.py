"""AWS Cognito federation of SEB accounts."""

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from .const import AWS_REGION, COGNITO_IDENTITY_POOL, COGNITO_PROVIDER
from .exceptions import CookeoError
from .models import AwsCredentials


def federate(id_token: str) -> AwsCredentials:
    """Exchange an SEB id token for temporary AWS credentials, blocking."""
    client = boto3.client("cognito-identity", region_name=AWS_REGION)
    logins = {COGNITO_PROVIDER: id_token}
    try:
        identity = client.get_id(IdentityPoolId=COGNITO_IDENTITY_POOL, Logins=logins)
        credentials = client.get_credentials_for_identity(
            IdentityId=identity["IdentityId"], Logins=logins
        )["Credentials"]
    except (BotoCoreError, ClientError) as err:
        raise CookeoError(f"Cognito federation failed: {err}") from err
    return AwsCredentials(
        access_key=credentials["AccessKeyId"],
        secret_key=credentials["SecretKey"],
        session_token=credentials["SessionToken"],
        expiration=int(credentials["Expiration"].timestamp()),
    )
