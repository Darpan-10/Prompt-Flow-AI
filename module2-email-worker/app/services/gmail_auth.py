"""
Gmail Authentication — two modes.

1. Service Account + Domain-Wide Delegation (settings.gmail_auth_mode ==
   "service_account", the default): headless, no browser ever, but
   requires a Google Workspace admin console to grant delegation — a
   personal @gmail.com account has no such console and can NEVER use
   this mode, regardless of permissions on the account itself.

   SETUP REQUIRED (one-time, by a Workspace admin):
     1. GCP: Create Service Account → generate JSON key
     2. GCP: Enable Gmail API
     3. Google Workspace Admin Console:
        → Security → API Controls → Domain-wide Delegation
        → Add Client ID of the service account
        → Scope: https://www.googleapis.com/auth/gmail.readonly
     4. Store JSON key in AWS Secrets Manager
     5. Set GMAIL_DELEGATED_USER = papers@srmap.edu.in in .env

2. OAuth2 "Desktop app" (settings.gmail_auth_mode == "oauth_personal"):
   works with any Gmail account, personal or Workspace, no admin needed.
   One-time interactive browser consent (scripts/gmail_oauth_login.py)
   caches a refresh token to disk; every run after that is fully
   headless, using the cached token and refreshing it automatically.
"""

import json
import logging
import base64
import os
from typing import List, Optional, Generator
from email import message_from_bytes
from email.message import Message

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as OAuthCredentials
from google.auth.transport.requests import Request as GoogleAuthRequest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from app.config import settings

logger = logging.getLogger(__name__)

# Gmail API scopes — readonly is sufficient for ingestion
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _get_credentials_service_account() -> service_account.Credentials:
    """
    Load service account credentials from env (JSON string).
    In production this JSON string comes from AWS Secrets Manager.
    """
    if not settings.google_service_account_json:
        raise RuntimeError(
            "GOOGLE_SERVICE_ACCOUNT_JSON is not set. "
            "Store the service account JSON in AWS Secrets Manager "
            "and inject it as an environment variable."
        )

    sa_info = json.loads(settings.google_service_account_json)

    credentials = service_account.Credentials.from_service_account_info(
        sa_info,
        scopes=GMAIL_SCOPES,
    )

    # Delegate to the target mailbox (domain-wide delegation)
    delegated = credentials.with_subject(settings.gmail_delegated_user)
    return delegated


def _get_credentials_oauth_personal() -> OAuthCredentials:
    """
    Load a cached OAuth2 token (written by scripts/gmail_oauth_login.py)
    and refresh it if expired. Never opens a browser itself — this is
    the headless path meant to run unattended; the interactive consent
    only happens once, via the login script.
    """
    token_path = settings.gmail_oauth_token_path
    if not os.path.exists(token_path):
        raise RuntimeError(
            f"No cached OAuth token at '{token_path}'. Run "
            f"'python scripts/gmail_oauth_login.py' once to authorize "
            f"this app against your Gmail account interactively."
        )

    credentials = OAuthCredentials.from_authorized_user_file(token_path, GMAIL_SCOPES)

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(GoogleAuthRequest())
        # Persist the refreshed access token so the next restart doesn't
        # need to refresh again immediately.
        with open(token_path, "w") as f:
            f.write(credentials.to_json())

    return credentials


def _get_credentials():
    if settings.gmail_auth_mode == "oauth_personal":
        return _get_credentials_oauth_personal()
    return _get_credentials_service_account()


def build_gmail_service():
    """Build and return authenticated Gmail API service client."""
    credentials = _get_credentials()
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    if settings.gmail_auth_mode == "oauth_personal":
        logger.info("Gmail API service built via OAuth2 (personal account mode)")
    else:
        logger.info(
            "Gmail API service built for delegated user: %s",
            settings.gmail_delegated_user,
        )
    return service


def fetch_unread_messages(service) -> List[dict]:
    """
    Fetch all UNREAD messages from the delegated mailbox.
    Returns list of raw message dicts.
    """
    messages = []
    try:
        result = (
            service.users()
            .messages()
            .list(userId="me", q="is:unread", maxResults=50)
            .execute()
        )

        msg_refs = result.get("messages", [])
        logger.info("Found %d unread messages", len(msg_refs))

        for ref in msg_refs:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=ref["id"], format="raw")
                .execute()
            )
            messages.append(msg)

    except HttpError as e:
        logger.error("Gmail API error fetching messages: %s", str(e))
        raise

    return messages


def mark_message_read(service, message_id: str) -> None:
    """Mark a message as read after successful processing."""
    try:
        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
        logger.info("Marked message %s as read", message_id)
    except HttpError as e:
        logger.warning("Failed to mark message %s as read: %s", message_id, str(e))


def decode_raw_message(raw_msg: dict) -> Message:
    """Decode base64url-encoded raw Gmail message into Python email.Message."""
    raw_bytes = base64.urlsafe_b64decode(raw_msg["raw"].encode("utf-8"))
    return message_from_bytes(raw_bytes)
