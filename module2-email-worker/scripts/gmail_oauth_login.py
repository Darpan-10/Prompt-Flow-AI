"""
One-time interactive Gmail OAuth2 login — for GMAIL_AUTH_MODE=oauth_personal.

Run this ONCE on a machine with a browser. It opens a Google consent
screen, you log in and approve read-only Gmail access, and the resulting
token (including a refresh token) is cached to disk. Every subsequent
run of the worker just loads and auto-refreshes that cached token —
fully headless, no browser needed again unless you delete the token file
or revoke access.

Usage:
    python scripts/gmail_oauth_login.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google_auth_oauthlib.flow import InstalledAppFlow
from app.config import settings
from app.services.gmail_auth import GMAIL_SCOPES


def main():
    client_secret_path = settings.gmail_oauth_client_secret_path
    if not os.path.exists(client_secret_path):
        print(f"Missing OAuth client secret file: {client_secret_path}")
        print()
        print("Get one (free, no Workspace admin needed):")
        print("  1. https://console.cloud.google.com -> create/select a project")
        print("  2. APIs & Services -> Enable APIs -> enable 'Gmail API'")
        print("  3. APIs & Services -> OAuth consent screen -> External ->")
        print("     fill in an app name + your email -> Save")
        print("     (it's fine to leave it in 'Testing' status for a demo —")
        print("     just add your own Gmail address under 'Test users')")
        print("  4. APIs & Services -> Credentials -> Create Credentials ->")
        print("     OAuth client ID -> Application type: Desktop app")
        print("  5. Download the JSON, save it as:")
        print(f"     {client_secret_path}")
        sys.exit(1)

    flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, GMAIL_SCOPES)
    # Opens your default browser, starts a local server to catch the
    # redirect — nothing to configure, run_local_server picks a free port.
    credentials = flow.run_local_server(port=0)

    with open(settings.gmail_oauth_token_path, "w") as f:
        f.write(credentials.to_json())

    print(f"✓ Token saved to {settings.gmail_oauth_token_path}")
    print("Set GMAIL_AUTH_MODE=oauth_personal in .env and you're done —")
    print("the worker will use this cached token from now on.")


if __name__ == "__main__":
    main()
