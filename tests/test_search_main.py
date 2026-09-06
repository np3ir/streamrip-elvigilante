import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from streamrip.rip.main import Main


def qobuz_track_page():
    return {
        "tracks": {
            "items": [
                {
                    "id": "q1",
                    "title": "Canción",
                    "performer": {"name": "Artista"},
                    "release_date": "2026-01-01",
                }
            ]
        }
    }


def search_main():
    main = Main.__new__(Main)
    main.config = SimpleNamespace(
        session=SimpleNamespace(cli=SimpleNamespace(max_search_results=25))
    )
    client = AsyncMock()
    client.search.return_value = [qobuz_track_page()]
    main.get_logged_in_client = AsyncMock(return_value=client)
    main.add_by_id = AsyncMock()
    main.add_all_by_id = AsyncMock()
    return main, client


@pytest.mark.asyncio
async def test_search_output_file_writes_importable_unicode_json(tmp_path):
    main, client = search_main()
    output = tmp_path / "results.json"

    await main.search_output_file("qobuz", "track", "Canción", str(output), 7)

    client.search.assert_awaited_once_with("track", "Canción", limit=7)
    assert json.loads(output.read_text(encoding="utf-8")) == [
        {
            "source": "qobuz",
            "media_type": "track",
            "id": "q1",
            "desc": "Canción by Artista",
        }
    ]


@pytest.mark.asyncio
async def test_search_take_first_queues_normalized_result():
    main, client = search_main()

    await main.search_take_first("qobuz", "track", "query")

    client.search.assert_awaited_once_with("track", "query", limit=1)
    main.add_by_id.assert_awaited_once_with("qobuz", "track", "q1")


@pytest.mark.asyncio
async def test_windows_interactive_search_queues_selected_result():
    main, client = search_main()
    selected = None

    def choose(results, **_kwargs):
        nonlocal selected
        selected = results[0]
        return [(selected, 0)]

    with patch("streamrip.rip.main.platform.system", return_value="Windows"), patch(
        "pick.pick", side_effect=choose
    ):
        await main.search_interactive("qobuz", "track", "query")

    client.search.assert_awaited_once_with("track", "query", limit=25)
    main.add_all_by_id.assert_awaited_once_with([("qobuz", "track", "q1")])


@pytest.mark.asyncio
async def test_empty_search_does_not_queue_or_write(tmp_path):
    main, client = search_main()
    client.search.return_value = []
    output = tmp_path / "results.json"

    await main.search_output_file("qobuz", "track", "missing", str(output), 5)
    await main.search_take_first("qobuz", "track", "missing")

    assert not output.exists()
    main.add_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_all_by_id_logs_in_once_per_source():
    main = Main.__new__(Main)
    qobuz = object()
    deezer = object()

    async def client_for(source):
        return {"qobuz": qobuz, "deezer": deezer}[source]

    main.get_logged_in_client = AsyncMock(side_effect=client_for)
    main._queue_by_id = AsyncMock()

    await main.add_all_by_id(
        [
            ("qobuz", "track", "q1"),
            ("qobuz", "album", "q2"),
            ("deezer", "track", "d1"),
        ]
    )

    assert main.get_logged_in_client.await_count == 2
    main._queue_by_id.assert_any_await(qobuz, "track", "q1")
    main._queue_by_id.assert_any_await(qobuz, "album", "q2")
    main._queue_by_id.assert_any_await(deezer, "track", "d1")
