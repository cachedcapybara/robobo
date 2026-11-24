"""Debian 安全公告（DSA / DLA）轮询、去重与通知。

数据源：

- 主源：官方 RSS（``dsa-long`` / ``dla-long``，RDF）。只保留最近 30 条，
  但 bot 24h 在线、5 分钟轮询，两次轮询之间不可能积累 30 条。
- 启动核对：security-tracker 的完整列表（``data/DSA/list`` / ``data/DLA/list``），
  补发停机期间漏掉的公告。

判重用「已见 ID 集合」而不是「最大 ID 水位线」：列表按日期倒序、同一天 ID
会乱序，用最大 ID 会漏。

消息格式：一条消息一条标题 + 每行一条公告（编号带 tracker 链接、包名、类型）。
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from ..http import FetchError, fetch_text_conditional
from ..text import SAFE_LIMIT
from .seen_store import SeenStore

log = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 300.0
SEND_INTERVAL = 0.1

MESSAGE_HEADER = "🛡️ Security"

DSA_FEED_URL = "https://www.debian.org/security/dsa-long"
DLA_FEED_URL = "https://www.debian.org/lts/security/dla-long"
DSA_LIST_URL = (
    "https://salsa.debian.org/security-tracker-team/security-tracker/-/raw/master/data/DSA/list"
)
DLA_LIST_URL = (
    "https://salsa.debian.org/security-tracker-team/security-tracker/-/raw/master/data/DLA/list"
)

_FEEDS = (("DSA", DSA_FEED_URL), ("DLA", DLA_FEED_URL))
_LISTS = (("DSA", DSA_LIST_URL), ("DLA", DLA_LIST_URL))

_RSS_NS = {
    "rss": "http://purl.org/rss/1.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

_RSS_TITLE = re.compile(r"^(DSA|DLA)-(\d+)(?:-(\d+))?\s+(\S+)\s+-\s+(.*)$")
_RSS_TITLE_FALLBACK = re.compile(r"^(DSA|DLA)-(\d+)(?:-(\d+))?\s+(.*)$")

_LIST_HEADER = re.compile(
    r"^\[(\d{1,2} [A-Z][a-z]{2} \d{4})\]\s+(DSA|DLA)-(\d+)(?:-(\d+))?\s+(.*)$"
)

_MONTHS = {
    "Jan": "01",
    "Feb": "02",
    "Mar": "03",
    "Apr": "04",
    "May": "05",
    "Jun": "06",
    "Jul": "07",
    "Aug": "08",
    "Sep": "09",
    "Oct": "10",
    "Nov": "11",
    "Dec": "12",
}


@dataclass(frozen=True, slots=True)
class SecurityAdvisory:
    id: str
    package: str
    title: str
    date: str
    source: str


def tracker_url(advisory_id: str) -> str:
    return f"https://security-tracker.debian.org/tracker/{advisory_id}"


def normalize_date(raw: str) -> str:
    """把 ``[19 Nov 2026]`` 或 ``2026-11-19`` 统一成 ``2026-11-19``。"""
    text = raw.strip()
    match = re.search(r"(\d{1,2}) ([A-Z][a-z]{2}) (\d{4})", text)
    if match:
        day, month, year = match.groups()
        month_num = _MONTHS.get(month)
        if month_num:
            return f"{year}-{month_num}-{int(day):02d}"
    match = re.match(r"(\d{4}-\d{2}-\d{2})", text)
    if match:
        return match.group(1)
    return text


def parse_rss_feed(markup: str) -> list[SecurityAdvisory]:
    """解析 RDF（RSS 1.0）feed。"""
    try:
        root = ET.fromstring(markup)
    except ET.ParseError as exc:
        raise FetchError(f"安全公告 RSS 解析失败：{exc}") from exc

    items: list[SecurityAdvisory] = []
    for node in root.findall("rss:item", _RSS_NS):
        title = (node.findtext("rss:title", default="", namespaces=_RSS_NS) or "").strip()
        parsed = _parse_title(title)
        if parsed is None:
            continue
        source, advisory_id, package, advisory_title = parsed
        date = normalize_date(node.findtext("dc:date", default="", namespaces=_RSS_NS) or "")
        items.append(
            SecurityAdvisory(
                id=advisory_id,
                package=package,
                title=advisory_title,
                date=date,
                source=source,
            )
        )
    return items


def _parse_title(title: str) -> tuple[str, str, str, str] | None:
    match = _RSS_TITLE.match(title)
    if match:
        source, number, revision, package, advisory_title = match.groups()
        advisory_id = f"{source}-{number}" + (f"-{revision}" if revision else "")
        return source, advisory_id, package, advisory_title.strip()
    match = _RSS_TITLE_FALLBACK.match(title)
    if match:
        source, number, revision, rest = match.groups()
        advisory_id = f"{source}-{number}" + (f"-{revision}" if revision else "")
        parts = rest.strip().split(None, 1)
        package = parts[0] if parts else ""
        advisory_title = parts[1] if len(parts) > 1 else ""
        return source, advisory_id, package, advisory_title
    return None


def parse_advisory_list(markup: str) -> list[SecurityAdvisory]:
    """解析 security-tracker 的完整列表（启动核对/补发用）。

    只取每条的首行：``[日期] 编号 包名 - 类型``。
    """
    items: list[SecurityAdvisory] = []
    for line in markup.splitlines():
        match = _LIST_HEADER.match(line)
        if match is None:
            continue
        raw_date, source, number, revision, rest = match.groups()
        advisory_id = f"{source}-{number}" + (f"-{revision}" if revision else "")
        if " - " in rest:
            package, title = rest.split(" - ", 1)
        else:
            parts = rest.split(None, 1)
            package = parts[0] if parts else ""
            title = parts[1] if len(parts) > 1 else ""
        items.append(
            SecurityAdvisory(
                id=advisory_id,
                package=package.strip(),
                title=title.strip(),
                date=normalize_date(raw_date),
                source=source,
            )
        )
    return items


def format_line(adv: SecurityAdvisory) -> str:
    """一行：``编号(链接) · 包名 · 类型``。"""
    link = f'<a href="{_url(tracker_url(adv.id))}">{html.escape(adv.id)}</a>'
    segments = [link, _text(adv.package), _text(adv.title)]
    return " · ".join(segment for segment in segments if segment)


def format_messages(items: list[SecurityAdvisory]) -> list[str]:
    """把若干公告合并成一条（必要时多条）消息，每条以 ``🛡️ Security`` 开头。"""
    ordered = sorted(items, key=lambda adv: (adv.date, adv.id), reverse=True)
    messages: list[str] = []
    current: list[str] = []
    size = len(MESSAGE_HEADER)

    for line in (format_line(adv) for adv in ordered):
        if current and size + 1 + len(line) > SAFE_LIMIT:
            messages.append("\n".join([MESSAGE_HEADER, *current]))
            current, size = [], len(MESSAGE_HEADER)
        current.append(line)
        size += 1 + len(line)

    if current:
        messages.append("\n".join([MESSAGE_HEADER, *current]))
    return messages


def _text(value: str) -> str:
    return html.escape(value)


def _url(value: str) -> str:
    return html.escape(value, quote=True)


def _dedupe(items: list[SecurityAdvisory]) -> list[SecurityAdvisory]:
    seen: set[str] = set()
    result: list[SecurityAdvisory] = []
    for adv in sorted(items, key=lambda item: (item.date, item.id)):
        if adv.id in seen:
            continue
        seen.add(adv.id)
        result.append(adv)
    return result


class SecurityNotifier:
    """定时轮询安全公告并推送到 Telegram。

    首次运行时用完整列表建立基线（不补发历史）；之后只推送新公告。
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        store: SeenStore,
        sender: Callable[[str], Awaitable[None]],
        interval: float = DEFAULT_POLL_INTERVAL,
    ) -> None:
        self._client = client
        self._store = store
        self._sender = sender
        self._interval = interval
        self._task: asyncio.Task[None] | None = None

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="security-notifier")

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
        try:
            await self.reconcile()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("安全公告启动核对失败", exc_info=True)

        while True:
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("安全公告轮询失败，将在下一轮重试", exc_info=True)
            await asyncio.sleep(self._interval)

    async def reconcile(self) -> int:
        """启动时用完整列表核对，返回补发的条数。"""
        first_run = self._store.empty
        total = 0

        for kind, url in _LISTS:
            try:
                text, _ = await self._fetch_list(kind, url)
                if text is None:
                    continue
                items = parse_advisory_list(text)
            except FetchError:
                log.warning("拉取/解析 %s 完整列表失败，跳过", kind, exc_info=True)
                continue

            if first_run:
                for item in items:
                    self._store.mark(item.id, item.date)
                self._store.save()
                log.info("安全公告基线建立：%s %d 条", kind, len(items))
                continue

            unseen = [item for item in items if not self._store.has(item.id)]
            if not unseen:
                continue
            await self._send_items(unseen)
            for item in unseen:
                self._store.mark(item.id, item.date)
            self._store.save()
            total += len(unseen)

        return total

    async def poll_once(self) -> int:
        """拉取一次 RSS 并推送新公告，返回本次新公告的数量。"""
        new_items: list[SecurityAdvisory] = []
        for kind, url in _FEEDS:
            try:
                text, _ = await self._fetch_feed(kind, url)
                if text is None:
                    continue
                new_items.extend(
                    item for item in parse_rss_feed(text) if not self._store.has(item.id)
                )
            except FetchError:
                log.warning("拉取/解析 %s RSS 失败，跳过", kind, exc_info=True)
                continue

        unique = _dedupe(new_items)
        if not unique:
            return 0

        try:
            await self._send_items(unique)
        except Exception:
            log.warning("推送安全公告失败，将在下一轮重试", exc_info=True)
            return 0

        for item in unique:
            self._store.mark(item.id, item.date)
        self._store.save()
        return len(unique)

    async def _fetch_feed(self, kind: str, url: str) -> tuple[str | None, str | None]:
        text, etag = await fetch_text_conditional(
            self._client, url, etag=self._store.get_etag(f"feed:{kind}")
        )
        if text is not None:
            self._store.set_etag(f"feed:{kind}", etag)
        return text, etag

    async def _fetch_list(self, kind: str, url: str) -> tuple[str | None, str | None]:
        text, etag = await fetch_text_conditional(
            self._client, url, etag=self._store.get_etag(f"list:{kind}")
        )
        if text is not None:
            self._store.set_etag(f"list:{kind}", etag)
        return text, etag

    async def _send_items(self, items: list[SecurityAdvisory]) -> None:
        for message in format_messages(items):
            await self._sender(message)
            await asyncio.sleep(SEND_INTERVAL)
