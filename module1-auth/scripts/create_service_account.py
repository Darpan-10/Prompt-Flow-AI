"""
Provision a local M2M service account (non-production only — see
app/services/cognito.py::verify_m2m_client).

Usage:
    python scripts/create_service_account.py <client_id> <client_name>
    # prompts for a secret, or generates one if you press Enter
"""
import asyncio
import getpass
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncpg
from app.config import settings
from app.services.service_accounts import hash_secret


async def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/create_service_account.py <client_id> <client_name>")
        sys.exit(1)

    client_id, client_name = sys.argv[1], sys.argv[2]

    secret = getpass.getpass(f"Secret for '{client_id}' (Enter to auto-generate): ")
    if not secret:
        secret = secrets.token_urlsafe(32)
        print(f"Generated secret: {secret}")
        print("Save this now — it isn't stored anywhere retrievable.")

    secret_hash = hash_secret(secret)

    db_url = settings.database_url
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute(
            """
            INSERT INTO service_accounts (client_id, client_name, client_secret_hash)
            VALUES ($1, $2, $3)
            ON CONFLICT (client_id) DO UPDATE SET client_secret_hash = $3, is_active = true
            """,
            client_id, client_name, secret_hash,
        )
    finally:
        await conn.close()

    print(f"✓ Service account '{client_id}' ready.")


if __name__ == "__main__":
    asyncio.run(main())
