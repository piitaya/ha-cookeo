"""Cookeo cloud protocol constants.

Non-sensitive identifiers shared with the SEB cloud backend (Salesforce
OAuth, AWS Cognito, AWS IoT).
"""

from __future__ import annotations

# --- SEB Salesforce OAuth ----------------------------------------------------

SF_BASE = "https://account.groupeseb.com"
SF_CLIENT_ID = (
    "3MVG99qusVZJwhskrTckKb5cJcadZj.gO2CSs265Vz0qQMGWsSZSBM5cTZ2nP9GXu04o7iOcczoNdXtJ5VWSZ"
)
#: Custom-scheme redirect URI. The browser will fail to open it, which
#: is exactly what we want — the user pastes the URL back to us.
SF_REDIRECT_URI = "gsmoduser://tokens"


# --- SEB platform / DCP API --------------------------------------------------

SEB_API_BASE = "https://sebplatform.api.groupe-seb.com"
SEB_IOT_BASE = "https://iot.api.groupe-seb.com"

#: API key for the Cookeo domain (``PRO_COO``) in the PROD environment.
COOKEO_API_KEY = "GtPU4am4rpf83Zptq4xahtJsEytbrvKP"

#: SEB domain key for Cookeo appliances.
SEB_DOMAIN = "PRO_COO"


# --- AWS IoT (PROD) ----------------------------------------------------------

AWS_REGION = "eu-west-1"
COGNITO_IDENTITY_POOL = "eu-west-1:f25c4598-48da-4fda-9c1a-27fe30bd2ffe"
#: Federated identity provider key for Cognito's setLogins(). Matches the
#: ``iss`` claim of the Salesforce id_token.
COGNITO_PROVIDER = "account.groupeseb.com"
MQTT_ENDPOINT = "a1p8u39dc9ign8-ats.iot.eu-west-1.amazonaws.com"


# --- App version ------------------------------------------------------------

#: Sent in the User-Agent for SEB API calls. Mirrors the official
#: mobile app so we stay under the same server-side code path — SEB's
#: API has no public spec and is more likely to keep working for
#: requests that look like the official client.
APP_VERSION = "22.0.0"
USER_AGENT = f"cookeat-android/{APP_VERSION}"
