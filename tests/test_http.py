"""HTTP 条件请求的单元测试。"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from robobo.http import fetch_text_conditional


class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200, headers: dict | None = None) -> None:
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    @property
    def is_error(self) -> bool:
        return self.status_code >= 400


class FakeClient:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict | None]] = []

    async def get(self, url: str, headers: dict | None = None) -> FakeResponse:
        self.calls.append((url, headers))
        return self.responses.pop(0)


def test_returns_text_and_etag() -> None:
    async def run() -> None:
        client = FakeClient([FakeResponse("hello", headers={"etag": '"v2"'})])

        text, etag = await fetch_text_conditional(client, "https://example.test")

        assert text == "hello"
        assert etag == '"v2"'
        assert client.calls == [("https://example.test", None)]

    asyncio.run(run())


def test_sends_if_none_match_and_handles_304() -> None:
    async def run() -> None:
        client = FakeClient([FakeResponse("", 304, headers={"etag": '"v1"'})])

        text, etag = await fetch_text_conditional(
            client, "https://example.test", etag='"v1"'
        )

        assert text is None
        assert etag == '"v1"'
        assert client.calls == [("https://example.test", {"If-None-Match": '"v1"'})]

    asyncio.run(run())


def test_retries_on_server_error() -> None:
    async def run() -> None:
        client = FakeClient(
            [FakeResponse("boom", 503), FakeResponse("ok", headers={"etag": '"v3"'})]
        )

        text, etag = await fetch_text_conditional(
            client, "https://example.test", attempts=2, backoff=0
        )

        assert text == "ok"
        assert etag == '"v3"'
        assert len(client.calls) == 2

    asyncio.run(run())


def test_raises_on_persistent_failure() -> None:
    async def run() -> None:
        client = FakeClient([FakeResponse("boom", 500), FakeResponse("boom", 500)])

        with pytest.raises(Exception, match="请求失败"):
            await fetch_text_conditional(
                client, "https://example.test", attempts=2, backoff=0
            )

    asyncio.run(run())


def test_network_error_is_retried() -> None:
    async def run() -> None:
        class FlakyClient:
            def __init__(self) -> None:
                self.calls = 0

            async def get(self, url: str, headers: dict | None = None) -> FakeResponse:
                self.calls += 1
                if self.calls == 1:
                    raise httpx.ConnectError("down")
                return FakeResponse("ok")

        client = FlakyClient()
        text, _etag = await fetch_text_conditional(
            client, "https://example.test", attempts=2, backoff=0
        )

        assert text == "ok"
        assert client.calls == 2

    asyncio.run(run())
