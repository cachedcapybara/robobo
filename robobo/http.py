"""共享的 HTTP 客户端与带重试的抓取工具。

所有出站请求都必须有超时，这是避免一个慢站点拖死整个 bot 的关键。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Final

import httpx

log = logging.getLogger(__name__)

USER_AGENT: Final = "robobo/0.2 (+https://github.com/cachedcapybara/robobo)"
RETRY_STATUS: Final = frozenset({429, 500, 502, 503, 504})


class FetchError(RuntimeError):
    """抓取失败（网络错误、超时或服务端错误）。"""


class NotFound(FetchError):
    """目标页面返回 404。"""


def create_client(proxy: str | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(10.0, connect=5.0),
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
        proxy=proxy,
    )


async def fetch_text(
    client: httpx.AsyncClient,
    url: str,
    *,
    attempts: int = 3,
    backoff: float = 0.5,
) -> str:
    """GET 一个页面并返回文本，对网络错误和 5xx/429 做指数退避重试。"""
    last_error = "unknown error"

    for attempt in range(attempts):
        try:
            response = await client.get(url)
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code == 404:
                raise NotFound(url)
            if response.status_code not in RETRY_STATUS:
                if response.is_error:
                    raise FetchError(f"{url} 返回 HTTP {response.status_code}")
                return response.text
            last_error = f"HTTP {response.status_code}"

        if attempt + 1 < attempts:
            await asyncio.sleep(backoff * (2**attempt))

    raise FetchError(f"{url} 请求失败（{last_error}）")


async def fetch_text_conditional(
    client: httpx.AsyncClient,
    url: str,
    *,
    etag: str | None = None,
    attempts: int = 3,
    backoff: float = 0.5,
) -> tuple[str | None, str | None]:
    """GET 一个页面，带 ``If-None-Match`` 做条件请求。

    返回 ``(text, etag)``：内容未变时 ``text`` 为 ``None``（HTTP 304），
    ``etag`` 为响应携带的新 ETag（无则回传传入值）。
    """
    headers = {"If-None-Match": etag} if etag else None
    last_error = "unknown error"

    for attempt in range(attempts):
        try:
            if headers:
                response = await client.get(url, headers=headers)
            else:
                response = await client.get(url)
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            response_etag = _response_etag(response) or etag
            if response.status_code == 304:
                return None, response_etag
            if response.status_code == 404:
                raise NotFound(url)
            if response.status_code not in RETRY_STATUS:
                if response.is_error:
                    raise FetchError(f"{url} 返回 HTTP {response.status_code}")
                return response.text, response_etag
            last_error = f"HTTP {response.status_code}"

        if attempt + 1 < attempts:
            await asyncio.sleep(backoff * (2**attempt))

    raise FetchError(f"{url} 请求失败（{last_error}）")


def _response_etag(response: httpx.Response) -> str | None:
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    return headers.get("etag")
