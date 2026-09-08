import asyncio
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from streamrip.client.request_budget import RateLimitGuard
from streamrip.client.tidal import TidalClient, TidalTokenStore
from streamrip.exceptions import (
    AuthenticationError,
    TidalRateLimitError,
    TidalSessionRejectedError,
)


class FakeResponse:
    async def json(self):
        return {
            "access_token": "new-token",
            "refresh_token": "new-refresh",
            "expires_in": 7200,
        }


class ResponseContext:
    async def __aenter__(self):
        return FakeResponse()

    async def __aexit__(self, *_args):
        return None


class FakeSession:
    def __init__(self):
        self.closed = False
        self.headers = {}
        self.post_calls = 0
        self.post_kwargs = None

    def post(self, *_args, **_kwargs):
        self.post_calls += 1
        self.post_kwargs = _kwargs
        return ResponseContext()


class JsonResponseContext:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self):
        return self.payload


class JsonResponseSession:
    def __init__(self, payload):
        self.headers = {}
        self.payload = payload
        self.post_calls = 0
        self.closed = False

    def post(self, *_args, **_kwargs):
        self.post_calls += 1
        return JsonResponseContext(self.payload)


class RateLimitedResponse:
    status = 429

    def __init__(self):
        self.headers = {"Retry-After": "60"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class RateLimitedSession:
    def __init__(self):
        self.get_calls = 0

    def get(self, *_args, **_kwargs):
        self.get_calls += 1
        return RateLimitedResponse()


class ImmediateBudget:
    async def acquire(self):
        return None


def tidal_client(access_token="old-token"):
    client = object.__new__(TidalClient)
    client.auth_lock = asyncio.Lock()
    client.config = SimpleNamespace(
        access_token=access_token,
        refresh_token="old-refresh",
        token_expiry=time.time() + 7200,
        user_id="1",
        country_code="US",
    )
    client.refresh_token = "old-refresh"
    client.session = FakeSession()
    client.token_store = Mock()
    client.token_store.load.return_value = None
    client.oauth_client_id = "client-id"
    client.oauth_client_secret = "client-secret"
    client._refresh_blocked = False
    client.token_role = "primary"
    return client


@pytest.mark.asyncio
async def test_forced_refresh_ignores_future_expiry_after_401():
    client = tidal_client()

    await client._refresh_access_token(
        force=True,
        stale_access_token="old-token",
    )

    assert client.session.post_calls == 1
    assert client.config.access_token == "new-token"
    assert client.refresh_token == "new-refresh"
    assert client.session.headers["authorization"] == "Bearer new-token"
    assert client.session.post_kwargs["data"]["client_id"]
    assert "client_secret" not in client.session.post_kwargs["data"]
    assert "scope" not in client.session.post_kwargs["data"]
    assert client.session.post_kwargs["headers"]["Authorization"].startswith("Basic ")


@pytest.mark.asyncio
async def test_concurrent_401_does_not_refresh_token_twice():
    client = tidal_client(access_token="already-refreshed")

    await client._refresh_access_token(
        force=True,
        stale_access_token="old-token",
    )

    assert client.session.post_calls == 0


@pytest.mark.asyncio
async def test_rejected_session_stops_refresh_without_exposing_payload():
    client = tidal_client()
    client.session = JsonResponseSession(
        {
            "error": "abuse_detected",
            "error_description": "private provider detail",
            "status": 403,
            "sub_status": 12001,
        }
    )

    with pytest.raises(TidalSessionRejectedError) as raised:
        await client._refresh_access_token(force=True)

    message = str(raised.value)
    assert "saved OAuth session" in message
    assert "does not prove" in message
    assert "403/12001" in message
    assert "private provider detail" not in message
    assert client.session.post_calls == 1
    assert client.config.access_token == "old-token"


@pytest.mark.asyncio
async def test_preventive_refresh_rejection_keeps_unexpired_access_token():
    client = tidal_client()
    client.config.token_expiry = time.time() + 60
    client.session = JsonResponseSession(
        {
            "error": "abuse_detected",
            "status": 403,
            "sub_status": 12001,
        }
    )

    await client.login()

    assert client.logged_in is True
    assert client._refresh_blocked is True
    assert client.session.headers["authorization"] == "Bearer old-token"


def test_token_store_writes_atomically_with_client_identity(tmp_path):
    path = tmp_path / "token.json"
    store = TidalTokenStore(str(path))

    store.save("access", "refresh", 123.0, "user", "US", "client-id")

    saved = store.load()
    assert saved["client_id"] == "client-id"
    assert saved["refresh_token"] == "refresh"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.asyncio
async def test_tripped_rate_limit_guard_rejects_request_before_network():
    client = object.__new__(TidalClient)
    client.rate_limit_guard = RateLimitGuard(strike_limit=1)
    client.rate_limit_guard.note_rate_limited()

    with pytest.raises(TidalRateLimitError, match="already reached"):
        await client._api_request("tracks/1")


@pytest.mark.asyncio
async def test_429_that_reaches_limit_trips_before_retrying():
    client = object.__new__(TidalClient)
    client.rate_limit_guard = RateLimitGuard(strike_limit=1)
    client.request_budget = ImmediateBudget()
    client.semaphore = asyncio.Semaphore(1)
    client.session = RateLimitedSession()
    client.config = SimpleNamespace(country_code="US", access_token="token")
    client._rate_limit_delay = 0.0

    with pytest.raises(TidalRateLimitError, match="repeatedly returned HTTP 429"):
        await client._api_request("tracks/1")

    assert client.session.get_calls == 1
    assert client.rate_limit_guard.tripped is True


@pytest.mark.asyncio
async def test_lossless_fallback_shares_request_budget_and_rate_limit_guard(monkeypatch):
    primary = object.__new__(TidalClient)
    primary._lossless_fallback_client = None
    primary._lossless_fallback_checked = False
    primary.global_config = object()
    primary.request_budget = object()
    primary.rate_limit_guard = RateLimitGuard()

    fallback = object.__new__(TidalClient)

    async def login():
        fallback.logged_in = True

    fallback.login = login
    monkeypatch.setattr(
        TidalClient,
        "lossless_fallback",
        classmethod(lambda cls, config: fallback),
    )

    result = await primary._get_lossless_fallback_client()

    assert result is fallback
    assert fallback.request_budget is primary.request_budget
    assert fallback.rate_limit_guard is primary.rate_limit_guard


@pytest.mark.asyncio
async def test_device_authorization_returns_code_and_uri():
    client = object.__new__(TidalClient)

    async def post(_url, _data, _auth=None):
        return {
            "deviceCode": "device-code",
            "verificationUriComplete": "link.tidal.com/ABC",
        }

    client._api_post = post

    assert await client._get_device_code() == (
        "device-code",
        "link.tidal.com/ABC",
    )


@pytest.mark.asyncio
async def test_device_authorization_pending_and_success():
    client = object.__new__(TidalClient)
    responses = [
        {"status": 400, "sub_status": 1002},
        {
            "access_token": "access",
            "refresh_token": "refresh",
            "expires_in": 3600,
            "user": {"userId": 7, "countryCode": "US"},
        },
    ]

    async def post(_url, _data, _auth=None):
        return responses.pop(0)

    client._api_post = post

    assert await client._get_auth_status("code") == (2, {})
    status, info = await client._get_auth_status("code")
    assert status == 0
    assert info["user_id"] == 7
    assert info["country_code"] == "US"


@pytest.mark.asyncio
async def test_invalid_oauth_client_requests_reauthorization():
    client = tidal_client()

    class InvalidClientResponse(FakeResponse):
        async def json(self):
            return {"status": 401, "error": "invalid_client"}

    class InvalidClientContext(ResponseContext):
        async def __aenter__(self):
            return InvalidClientResponse()

    client.session.post = Mock(return_value=InvalidClientContext())

    with pytest.raises(AuthenticationError, match="authorized again"):
        await client._refresh_access_token(force=True)
