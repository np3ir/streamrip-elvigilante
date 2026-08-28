import os

import aiohttp
import pytest
from aiohttp import web

from streamrip.client.downloadable import TidalDownloadable


@pytest.mark.asyncio
async def test_segment_download_preserves_order_and_publishes_atomically(tmp_path):
    async def segment(request):
        number = int(request.match_info["number"])
        return web.Response(body=f"segment-{number}|".encode())

    app = web.Application()
    app.router.add_get("/{number}", segment)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    destination = tmp_path / "track.m4a"
    progress = []

    try:
        async with aiohttp.ClientSession() as session:
            downloadable = TidalDownloadable(
                session,
                url=None,
                urls=tuple(f"http://127.0.0.1:{port}/{number}" for number in range(12)),
                codec="mp4a.40.2",
                encryption_key=None,
                restrictions=(),
            )
            await downloadable.download(os.fspath(destination), progress.append)
    finally:
        await runner.cleanup()

    assert destination.read_bytes() == b"".join(
        f"segment-{number}|".encode() for number in range(12)
    )
    assert sum(progress) == destination.stat().st_size
    assert not (tmp_path / "track.m4a.part").exists()


@pytest.mark.asyncio
async def test_segment_failure_removes_partial_and_does_not_publish(tmp_path):
    async def segment(request):
        if request.match_info["number"] == "2":
            raise web.HTTPServiceUnavailable()
        return web.Response(body=b"segment")

    app = web.Application()
    app.router.add_get("/{number}", segment)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    destination = tmp_path / "track.m4a"

    try:
        async with aiohttp.ClientSession() as session:
            downloadable = TidalDownloadable(
                session,
                url=None,
                urls=tuple(f"http://127.0.0.1:{port}/{number}" for number in range(4)),
                codec="mp4a.40.2",
                encryption_key=None,
                restrictions=(),
            )
            with pytest.raises(aiohttp.ClientResponseError):
                await downloadable.download(os.fspath(destination), lambda _size: None)
    finally:
        await runner.cleanup()

    assert not destination.exists()
    assert not (tmp_path / "track.m4a.part").exists()
