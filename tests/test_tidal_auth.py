import asyncio
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from streamrip.client.tidal import TidalClient


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
        self.headers = {}
        self.post_calls = 0

    def post(self, *_args, **_kwargs):
        self.post_calls += 1
        return ResponseContext()


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


@pytest.mark.asyncio
async def test_concurrent_401_does_not_refresh_token_twice():
    client = tidal_client(access_token="already-refreshed")

    await client._refresh_access_token(
        force=True,
        stale_access_token="old-token",
    )

    assert client.session.post_calls == 0
