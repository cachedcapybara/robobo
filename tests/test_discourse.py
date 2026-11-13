"""Discourse 轮询服务的单元测试：用假客户端和假发送器，不联网。"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from robobo.services.discourse import (
    DiscourseNotifier,
    build_feed_url,
    build_topic_url,
    format_topic,
    parse_latest_payload,
)

BASE_URL = "https://forum.example.com"


def make_topic(topic_id: int, title: str = "Hello", slug: str | None = None, **extra: Any) -> dict:
    topic = {
        "id": topic_id,
        "title": title,
        "slug": slug if slug is not None else title.lower().replace(" ", "-"),
        "created_at": "2026-01-01T00:00:00.000Z",
    }
    topic.update(extra)
    return topic


def make_payload(*topics: dict) -> dict:
    return {"topic_list": {"topics": list(topics)}}


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    @property
    def is_error(self) -> bool:
        return self.status_code >= 400


class FakeClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.urls: list[str] = []

    async def get(self, url: str) -> FakeResponse:
        self.urls.append(url)
        return FakeResponse(json.dumps(self.payload))


class FakeSender:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[str] = []

    async def __call__(self, text: str) -> None:
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append(text)


def make_notifier(
    payload: dict,
    sender: FakeSender | None = None,
    *,
    category: str | None = None,
) -> tuple[DiscourseNotifier, FakeClient]:
    client = FakeClient(payload)
    notifier = DiscourseNotifier(
        client,
        base_url=BASE_URL,
        category=category,
        sender=sender or FakeSender(),
        interval=60,
    )
    return notifier, client


def test_build_feed_url_without_category() -> None:
    assert build_feed_url(BASE_URL) == f"{BASE_URL}/latest.json"


def test_build_feed_url_with_category() -> None:
    assert build_feed_url(BASE_URL + "/", "ask") == f"{BASE_URL}/c/ask/l/latest.json"


def test_build_topic_url() -> None:
    assert build_topic_url(BASE_URL, "hello", 42) == f"{BASE_URL}/t/hello/42"


def test_parse_latest_payload_filters_deleted_and_invisible() -> None:
    payload = make_payload(
        make_topic(1, title="visible"),
        make_topic(2, title="deleted", deleted_at="2026-01-02T00:00:00.000Z"),
        make_topic(3, title="invisible", visible=False),
        {"id": 4, "title": "", "slug": "x"},
    )

    topics = parse_latest_payload(payload, BASE_URL)

    assert [t.id for t in topics] == [1]
    assert topics[0].title == "visible"
    assert topics[0].url == f"{BASE_URL}/t/visible/1"


def test_parse_latest_payload_supports_category_feed() -> None:
    payload = make_payload(make_topic(7, title="Category topic"))

    topics = parse_latest_payload(payload, BASE_URL)

    assert [t.id for t in topics] == [7]


def test_format_topic_escapes_html() -> None:
    topic = parse_latest_payload(
        make_payload(make_topic(1, title="<script>alert(1)</script>", slug="evil")),
        BASE_URL,
    )[0]

    text = format_topic(topic)

    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert f"{BASE_URL}/t/evil/1" in text


def test_format_topic_prefix_outside_link_and_title_is_link() -> None:
    topic = parse_latest_payload(
        make_payload(make_topic(1, title="Hello", slug="hello")),
        BASE_URL,
    )[0]

    text = format_topic(topic)

    # [论坛新帖] 在 <a> 外面，标题本身是链接文字
    assert text == f'📢 [论坛新帖] <a href="{BASE_URL}/t/hello/1"><b>Hello</b></a>'


def test_first_poll_sets_baseline_without_notifying() -> None:
    async def run() -> None:
        sender = FakeSender()
        notifier, client = make_notifier(make_payload(make_topic(10), make_topic(11)), sender)

        sent = await notifier.poll_once()

        assert sent == 0
        assert sender.sent == []
        assert notifier.last_seen_id == 11
        assert client.urls == [f"{BASE_URL}/latest.json"]

    asyncio.run(run())


def test_only_new_topics_are_sent_in_order() -> None:
    async def run() -> None:
        payload = make_payload(make_topic(10), make_topic(11))
        sender = FakeSender()
        notifier, _ = make_notifier(payload, sender)
        await notifier.poll_once()

        payload["topic_list"]["topics"] = [
            make_topic(13, title="Third"),
            make_topic(12, title="Second"),
            make_topic(11),
            make_topic(10),
        ]
        sent = await notifier.poll_once()

        assert sent == 2
        assert len(sender.sent) == 2
        assert "Second" in sender.sent[0]
        assert "Third" in sender.sent[1]
        assert notifier.last_seen_id == 13

    asyncio.run(run())


def test_failed_send_keeps_watermark_for_retry() -> None:
    async def run() -> None:
        payload = make_payload(make_topic(10))
        sender = FakeSender(fail=True)
        notifier, _ = make_notifier(payload, sender)
        await notifier.poll_once()

        payload["topic_list"]["topics"] = [make_topic(11), make_topic(10)]
        assert await notifier.poll_once() == 0
        assert notifier.last_seen_id == 10

        sender.fail = False
        assert await notifier.poll_once() == 1
        assert notifier.last_seen_id == 11
        assert len(sender.sent) == 1

    asyncio.run(run())


def test_category_notifier_uses_category_feed() -> None:
    async def run() -> None:
        sender = FakeSender()
        notifier, client = make_notifier(make_payload(make_topic(10)), sender, category="ask")

        await notifier.poll_once()

        assert client.urls == [f"{BASE_URL}/c/ask/l/latest.json"]

    asyncio.run(run())


def test_start_and_stop() -> None:
    async def run() -> None:
        notifier, _ = make_notifier(make_payload(make_topic(10)))

        notifier.start()
        assert notifier.task is not None

        await notifier.stop()
        assert notifier.task is None

    asyncio.run(run())
