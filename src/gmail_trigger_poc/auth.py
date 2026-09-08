"""Gmail OAuth — Google's current recommended pattern (JSON token, not pickle).

get_gmail_service():
  - token.json valid            -> load silently, no browser
  - token.json expired + refresh -> refresh silently
  - missing / refresh failed     -> open browser once for consent, then save
"""

from __future__ import annotations

import logging
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from gmail_trigger_poc.config import Settings, get_settings

logger = logging.getLogger(__name__)


def load_credentials(settings: Settings | None = None) -> Credentials:
    settings = settings or get_settings()
    creds: Credentials | None = None

    if os.path.exists(settings.token_file):
        logger.debug("loading cached token from %s", settings.token_file)
        creds = Credentials.from_authorized_user_file(settings.token_file, settings.scopes)
    else:
        logger.debug("no token file at %s", settings.token_file)

    if creds and creds.valid:
        logger.debug("cached token is valid; scopes=%s", settings.scopes)
        return creds

    if creds and creds.expired and creds.refresh_token:
        logger.info("access token expired -> refreshing silently")
        creds.refresh(Request())
        logger.debug("token refreshed OK")
    else:
        if not os.path.exists(settings.credentials_file):
            logger.error("OAuth client secret missing: %s", settings.credentials_file)
            raise FileNotFoundError(
                f"OAuth client secret not found: {settings.credentials_file}. "
                "Download it from Google Cloud Console -> APIs & Services -> "
                "Credentials -> OAuth client ID (Desktop app)."
            )
        logger.info(
            "no valid token -> starting OAuth consent flow "
            "(a browser window will open; port=%s)",
            settings.oauth_local_server_port or "auto",
        )
        flow = InstalledAppFlow.from_client_secrets_file(
            settings.credentials_file, settings.scopes
        )
        creds = flow.run_local_server(port=settings.oauth_local_server_port)
        logger.info("OAuth consent complete")

    with open(settings.token_file, "w", encoding="utf-8") as f:
        f.write(creds.to_json())
    logger.info("wrote credentials to %s", settings.token_file)

    return creds


def get_gmail_service(settings: Settings | None = None):
    """Return an authorized Gmail API service client."""
    settings = settings or get_settings()
    logger.debug("building Gmail service for user_id=%s", settings.user_id)
    creds = load_credentials(settings)
    service = build("gmail", "v1", credentials=creds)
    logger.debug("Gmail service ready")
    return service
