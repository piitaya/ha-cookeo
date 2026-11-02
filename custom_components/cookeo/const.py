"""Constants for the Cookeo integration."""

from datetime import timedelta
import logging

DOMAIN = "cookeo"

LOGGER = logging.getLogger(__package__)

MANUFACTURER = "Moulinex"

CONF_PROFILE_PERIMETER = "profile_perimeter"
CONF_REGION = "region"
CONF_TOKENS = "tokens"

CREDENTIALS_RENEWAL_MARGIN = 300
RETRY_INTERVAL = timedelta(minutes=1)
