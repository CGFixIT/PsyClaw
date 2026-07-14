# flake8: noqa: WPS202
# WPS202 (max-module-members=7) counts every config constant; splitting
# these into several tiny per-value modules would add indirection with no
# readability gain for a relay this small — see the "moderate split"
# decision recorded for this PR.
"""Environment-derived configuration and shared Twilio clients for the SMS relay."""
from __future__ import annotations

import os

from twilio.request_validator import RequestValidator
from twilio.rest import Client as TwilioClient

TWILIO_ACCOUNT_SID = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_FROM_NUMBER = os.environ["TWILIO_FROM_NUMBER"]

CYCLAW_QUERY_URL = os.environ.get("CYCLAW_QUERY_URL", "http://127.0.0.1:8787/query")

_raw_whitelist = os.environ.get("SMS_AUTH_WHITELIST", "")
SMS_AUTH_WHITELIST = frozenset(
    entry.strip() for entry in _raw_whitelist.split(",") if entry.strip()
)

# Default stays loopback for consistency with CyClaw's zero-trust posture
# (gate.py binds 127.0.0.1 only). Set SMS_RELAY_HOST=0.0.0.0 explicitly only
# when this relay must accept Twilio webhooks directly from the public
# internet; production deploys should front it with a reverse proxy / TLS
# terminator instead of exposing it on all interfaces directly.
SMS_RELAY_HOST = os.environ.get("SMS_RELAY_HOST", "127.0.0.1")
SMS_RELAY_PORT = int(os.environ.get("SMS_RELAY_PORT", "8788"))
SMS_DB_PATH = os.environ.get("SMS_DB_PATH", "sms_relay.db")
SMS_SESSION_TTL_SEC = int(os.environ.get("SMS_SESSION_TTL_SEC", "900"))
SMS_DEDUPE_TTL_SEC = int(os.environ.get("SMS_DEDUPE_TTL_SEC", "3600"))
SMS_SEGMENT_SIZE = max(int(os.environ.get("SMS_SEGMENT_SIZE", "1200")), 100)  # P2: floor guard

_raw_providers = os.environ.get("SMS_ALLOWED_ONLINE_PROVIDERS", "grok")
SMS_ALLOWED_ONLINE_PROVIDERS = tuple(
    provider.strip().lower() for provider in _raw_providers.split(",") if provider.strip()
)

ACK_TEXT = os.environ.get("SMS_ACK_TEXT", "Processing...")
CYCLAW_TIMEOUT_SEC = 180.0
ERROR_DETAIL_MAX_CHARS = 300
ERROR_SMS_MAX_CHARS = 120

validator = RequestValidator(TWILIO_AUTH_TOKEN)
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
