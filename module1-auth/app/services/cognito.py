"""
AWS Cognito integration via Authlib.

Handles:
- OAuth 2.0 Authorization Code + PKCE flow
- M2M Client Credentials flow
- Token exchange
"""
import httpx
import base64
from typing import Optional
from app.config import settings
from app.services.service_accounts import verify_secret
from app import state


def get_cognito_base_url() -> str:
    return f"https://{settings.cognito_domain}"


def get_oidc_metadata_url() -> str:
    return (
        f"https://cognito-idp.{settings.cognito_region}.amazonaws.com/"
        f"{settings.cognito_user_pool_id}/.well-known/openid-configuration"
    )


async def exchange_code_for_tokens(code: str, redirect_uri: str) -> dict:
    """Exchange authorization code for Cognito tokens."""
    token_url = f"{get_cognito_base_url()}/oauth2/token"
    credentials = base64.b64encode(
        f"{settings.cognito_client_id}:{settings.cognito_client_secret}".encode()
    ).decode()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            token_url,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        resp.raise_for_status()
        return resp.json()


async def get_user_info(access_token: str) -> dict:
    """Fetch user info from Cognito /oauth2/userInfo."""
    userinfo_url = f"{get_cognito_base_url()}/oauth2/userInfo"
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            userinfo_url,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()


async def revoke_token(refresh_token: str) -> bool:
    """Revoke a refresh token at Cognito."""
    revoke_url = f"{get_cognito_base_url()}/oauth2/revoke"
    credentials = base64.b64encode(
        f"{settings.cognito_client_id}:{settings.cognito_client_secret}".encode()
    ).decode()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            revoke_url,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"token": refresh_token},
        )
        return resp.status_code == 200


async def verify_m2m_client(client_id: str, client_secret: str) -> bool:
    """
    Validate M2M client credentials.

    Production: Cognito client_credentials grant, as before.

    Non-production: checks the local service_accounts table instead.
    This table existed in schema.sql with no code ever reading it and
    no column to even store a validatable secret — meaning M2M auth
    (and everything that depends on it, like Module 3's directory
    lookups) could never actually succeed without live AWS Cognito
    configured, even for local Docker testing. Real credential
    validation still happens either way; this just chooses where.
    """
    if settings.app_env != "production":
        return await _verify_local_service_account(client_id, client_secret)

    token_url = f"{get_cognito_base_url()}/oauth2/token"
    credentials = base64.b64encode(
        f"{client_id}:{client_secret}".encode()
    ).decode()

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            token_url,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "client_credentials",
                "scope": "queue.consume db.write.internal",
            },
        )
        return resp.status_code == 200


async def _verify_local_service_account(client_id: str, client_secret: str) -> bool:
    async with state.db_pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT client_secret_hash FROM service_accounts WHERE client_id = $1 AND is_active = true",
            client_id,
        )
    if row is None or not row["client_secret_hash"]:
        return False
    return verify_secret(client_secret, row["client_secret_hash"])
