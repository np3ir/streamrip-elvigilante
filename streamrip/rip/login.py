"""Explicit, secret-safe service authentication helpers."""

from __future__ import annotations

import hashlib

from ..client import DeezerClient, QobuzClient
from ..config import Config


async def authenticate_qobuz(
    config: Config,
    identity: str,
    credential: str,
    *,
    use_auth_token: bool,
) -> str:
    """Validate Qobuz credentials and persist only the resulting auth token."""

    session = config.session.qobuz
    session.use_auth_token = use_auth_token
    session.email_or_userid = identity.strip()
    session.password_or_token = (
        credential.strip()
        if use_auth_token
        else hashlib.md5(credential.encode("utf-8")).hexdigest()
    )

    client = QobuzClient(config)
    try:
        await client.login()
        assert client.user_id is not None
        assert client.user_auth_token is not None

        stored = config.file.qobuz
        stored.use_auth_token = True
        stored.email_or_userid = client.user_id
        stored.password_or_token = client.user_auth_token
        stored.app_id = session.app_id
        stored.secrets = list(session.secrets)
        config.file.set_modified()
        return client.user_id
    finally:
        client_session = getattr(client, "session", None)
        if client_session is not None and not client_session.closed:
            await client_session.close()


async def authenticate_deezer(config: Config, arl: str) -> str:
    """Validate an ARL before replacing the credential stored on disk."""

    config.session.deezer.arl = arl.strip()
    client = DeezerClient(config)
    try:
        await client.login()
        user = client.client.current_user
        stored = config.file.deezer
        stored.arl = config.session.deezer.arl
        config.file.set_modified()
        return str(user.get("name") or user.get("id") or "authenticated user")
    finally:
        client_session = getattr(client, "session", None)
        if client_session is not None and not client_session.closed:
            await client_session.close()


def logout_service(config: Config, service: str) -> None:
    """Remove private user credentials while preserving reusable app metadata."""

    if service == "qobuz":
        stored = config.file.qobuz
        stored.use_auth_token = True
        stored.email_or_userid = ""
        stored.password_or_token = ""
    elif service == "deezer":
        config.file.deezer.arl = ""
    else:
        raise ValueError(service)
    config.file.set_modified()


def configured_services(config: Config) -> dict[str, bool]:
    """Return credential presence without exposing credential values."""

    return {
        "qobuz": bool(
            config.file.qobuz.email_or_userid
            and config.file.qobuz.password_or_token
        ),
        "deezer": bool(config.file.deezer.arl),
        "tidal": bool(config.file.tidal.access_token),
    }
