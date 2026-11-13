"""Discourse 新帖轮询与通知。

公开论坛的 ``/latest.json``（或分类 feed）可以匿名访问，因此这里采用**主动
轮询**：bot 定期出站抓取，发现新主题后推送到 Telegram。不需要入站端口、
不需要域名，与 long polling 架构完全兼容。
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from ..http import FetchError, fetch_text

log = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 60.0


@dataclass(frozen=True, slots=True)
class DiscourseTopic:
    id: int
    title: str
    slug: str
    url: str
    created_at: str = ""


def build_feed_url(base_url: str, category: str | None = None) -> str:
    base = base_url.rstrip("/")
    if category:
        return f"{base}/c/{category.strip('/')}/l/latest.json"
    return f"{base}/latest.json"


def build_topic_url(base_url: str, slug: str, topic_id: int) -> str:
    return f"{base_url.rstrip('/')}/t/{slug}/{topic_id}"


def parse_latest_payload(payload: dict[str, Any], base_url: str) -> list[DiscourseTopic]:
    """从 ``/latest.json`` 响应里提取可见且未删除的主题。"""
    topics: list[DiscourseTopic] = []
    for raw in payload.get("topic_list", {}).get("topics", []):
        topic_id = raw.get("id")
        title = raw.get("title")
        slug = raw.get("slug")
        if not isinstance(topic_id, int) or not title or not slug:
            continue
        if raw.get("deleted_at"):
            continue
        if raw.get("visible") is False:
            continue
        topics.append(
            DiscourseTopic(
                id=topic_id,
                title=title,
                slug=slug,
                url=build_topic_url(base_url, slug, topic_id),
                created_at=raw.get("created_at") or "",
            )
        )
    return topics


async def fetch_latest_topics(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    category: str | None = None,
) -> list[DiscourseTopic]:
    markup = await fetch_text(client, build_feed_url(base_url, category))
    try:
        payload = json.loads(markup)
    except json.JSONDecodeError as exc:
        raise FetchError(f"Discourse 返回了非 JSON 内容：{exc}") from exc
    return parse_latest_payload(payload, base_url)


def format_topic(topic: DiscourseTopic) -> str:
    """把主题格式化成 Telegram HTML 消息：标签 + 标题即链接。"""
    title = html.escape(topic.title)
    url = html.escape(topic.url, quote=True)
    return f'📢 [论坛新帖] <a href="{url}"><b>{title}</b></a>'


class DiscourseNotifier:
    """定时轮询 Discourse，把新主题推送到 Telegram。

    首次轮询只把当前最大 topic id 记为水位线，不补发历史主题。
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str,
        category: str | None,
        sender: Callable[[str], Awaitable[None]],
        interval: float = DEFAULT_POLL_INTERVAL,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._category = category
        self._sender = sender
        self._interval = interval
        self._last_seen_id: int | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def last_seen_id(self) -> int | None:
        return self._last_seen_id

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="discourse-notifier")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def _run(self) -> None:
        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("Discourse 轮询失败，将在下一轮重试", exc_info=True)
            await asyncio.sleep(self._interval)

    async def poll_once(self) -> int:
        """拉取一次并推送新主题，返回本次成功推送的数量。"""
        topics = await fetch_latest_topics(
            self._client,
            base_url=self._base_url,
            category=self._category,
        )

        if self._last_seen_id is None:
            self._last_seen_id = max((t.id for t in topics), default=None)
            log.info("Discourse 水位线初始化为 topic %s", self._last_seen_id)
            return 0

        new_topics = sorted(
            (t for t in topics if t.id > self._last_seen_id),
            key=lambda t: t.id,
        )
        sent = 0
        for topic in new_topics:
            try:
                await self._sender(format_topic(topic))
            except Exception:
                log.warning("推送 Discourse 主题 %s 失败", topic.id, exc_info=True)
                break
            self._last_seen_id = topic.id
            sent += 1
        return sent
