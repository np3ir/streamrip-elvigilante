"""Explicit, secret-safe service authentication helpers."""

from __future__ import annotations

import hashlib
from typing import Any

from ..client import DeezerClient, QobuzClient
from ..config import Config


class BrowserLoginUnavailableError(RuntimeError):
    """Raised when the optional embedded-browser runtime is unavailable."""


class BrowserLoginCancelledError(RuntimeError):
    """Raised when the login window closes before an ARL cookie is available."""


def _extract_arl_cookie(cookies: Any) -> str | None:
    """Extract an ARL from pywebview cookie representations."""

    if isinstance(cookies, dict):
        value = cookies.get("arl")
        if hasattr(value, "value"):
            value = value.value
        return str(value).strip() if value else None

    for cookie in cookies or ():
        if hasattr(cookie, "items"):
            for name, morsel in cookie.items():
                if name == "arl" and getattr(morsel, "value", None):
                    return str(morsel.value).strip()
        name = getattr(cookie, "key", None) or getattr(cookie, "name", None)
        if name != "arl":
            continue
        value = getattr(cookie, "value", None)
        if value is None and isinstance(cookie, dict):
            value = cookie.get("value")
        if value:
            return str(value).strip()
    return None


def capture_deezer_arl(webview_module=None) -> str:
    """Open Deezer in an isolated WebView2 window and capture its ARL cookie."""

    if webview_module is None:
        try:
            import webview as webview_module
        except ImportError:
            raise BrowserLoginUnavailableError(
                "Install the 'browser-login' extra or use --arl."
            ) from None

    captured: list[str] = []
    window = webview_module.create_window(
        "Streamrip — Deezer login",
        "https://www.deezer.com/login",
        width=980,
        height=720,
    )

    def inspect_cookies(*_args):
        arl = _extract_arl_cookie(window.get_cookies())
        if arl:
            captured.append(arl)
            window.destroy()

    window.events.loaded += inspect_cookies
    webview_module.start(gui="edgechromium", private_mode=True)
    if not captured:
        raise BrowserLoginCancelledError(
            "Deezer login was cancelled before completion."
        )
    return captured[0]


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
