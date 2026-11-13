"""UDD 客户端测试：用假连接池，不连数据库。"""

from __future__ import annotations

import asyncio

import pytest

from robobo.services.udd import PackageVersion, UddClient, UddUnavailable


class FakePool:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, query: str, *args: object) -> list[dict[str, str]]:
        self.queries.append((query, args))
        return self.rows

    async def close(self) -> None:
        return None


def test_unconfigured_client_raises() -> None:
    client = UddClient(None)

    assert client.configured is False
    with pytest.raises(UddUnavailable):
        asyncio.run(client.versions("hello"))


def test_versions_are_parameterized_and_mapped() -> None:
    client = UddClient("postgresql://example/udd")
    pool = FakePool([{"release": "bookworm", "version": "2.10-3"}])
    client._pool = pool  # type: ignore[assignment]

    rows = asyncio.run(client.versions("libc6"))

    assert rows == [PackageVersion(release="bookworm", version="2.10-3")]
    query, args = pool.queries[0]
    assert "$1" in query
    assert args == ("libc6",)
    # 按 Debian 官方发行顺序（releases.sort）排序，而不是字母序
    assert "JOIN releases" in query
    assert "r.sort" in query
