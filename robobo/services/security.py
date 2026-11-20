"""Debian 安全公告（DSA / DLA）轮询、去重与通知。

数据源：

- 主源：官方 RSS（``dsa-long`` / ``dla-long``，RDF）。只保留最近 30 条，
  但 bot 24h 在线、5 分钟轮询，两次轮询之间不可能积累 30 条。
- 启动核对：security-tracker 的完整列表（``data/DSA/list`` / ``data/DLA/list``），
  补发停机期间漏掉的公告。
- enrichment：每条新公告抓一次 tracker 页，取 CVE 与修复版本。

判重用「已见 ID 集合」而不是「最大 ID 水位线」：列表按日期倒序、同一天 ID
会乱序，用最大 ID 会漏。
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace

import httpx
from bs4 import BeautifulSoup

from ..http import FetchError, fetch_text, fetch_text_conditional
from ..text import split_message
from .seen_store import SeenStore

log = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 300.0
SEND_INTERVAL = 0.1

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

SUMMARY_LIMIT = 120

_RSS_NS = {
    "rss": "http://purl.org/rss/1.0/",
    "dc": "http://purl.org/dc/elements/1.1/",
}

_RSS_TITLE = re.compile(r"^(DSA|DLA)-(\d+)(?:-(\d+))?\s+(\S+)\s+-\s+(.*)$")
_RSS_TITLE_FALLBACK = re.compile(r"^(DSA|DLA)-(\d+)(?:-(\d+))?\s+(.*)$")

_LIST_HEADER = re.compile(
    r"^\[(\d{1,2} [A-Z][a-z]{2} \d{4})\]\s+(DSA|DLA)-(\d+)(?:-(\d+))?\s+(.*)$"
)
_LIST_SUITE = re.compile(r"^\[([^\]]+)\]\s+-\s+(.*)$")

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
    cves: tuple[str, ...] = ()
    suites: tuple[tuple[str, str], ...] = ()
    summary: str = ""


def tracker_url(advisory_id: str) -> str:
    return f"https://security-tracker.debian.org/tracker/{advisory_id}"


def advisory_url(advisory_id: str, date: str) -> str:
    year = date[:4]
    if len(year) != 4 or not year.isdigit():
        return ""
    if advisory_id.startswith("DLA"):
        return f"https://www.debian.org/lts/security/{year}/{advisory_id.lower()}"
    return f"https://www.debian.org/security/{year}/{advisory_id.lower()}"


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


def extract_summary(description: str, limit: int = SUMMARY_LIMIT) -> str:
    """从公告正文里取首句，去标签、折叠空白、截断。"""
    text = html.unescape(description)
    # 先去掉 <a>...</a>（短 feed 的 description 只有一个链接，不应变成摘要）
    text = re.sub(r"<a\b[^>]*>.*?</a>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    match = re.match(r"(.*?[.!?])(?:\s|$)", text)
    sentence = match.group(1) if match else text
    if len(sentence) > limit:
        sentence = sentence[:limit].rsplit(" ", 1)[0].rstrip() + "…"
    return sentence


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
        description = node.findtext("rss:description", default="", namespaces=_RSS_NS) or ""
        items.append(
            SecurityAdvisory(
                id=advisory_id,
                package=package,
                title=advisory_title,
                date=date,
                source=source,
                summary=extract_summary(description),
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


@dataclass(slots=True)
class _ListEntry:
    advisory_id: str
    package: str
    title: str
    date: str
    cves: list[str] = field(default_factory=list)
    suites: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def parse(cls, line: str) -> _ListEntry:
        match = _LIST_HEADER.match(line)
        if match is None:  # pragma: no cover - 调用方已过滤
            raise ValueError(f"无法解析列表条目：{line!r}")
        raw_date, source, number, revision, rest = match.groups()
        advisory_id = f"{source}-{number}" + (f"-{revision}" if revision else "")
        if " - " in rest:
            package, title = rest.split(" - ", 1)
        else:
            parts = rest.split(None, 1)
            package = parts[0] if parts else ""
            title = parts[1] if len(parts) > 1 else ""
        return cls(
            advisory_id=advisory_id,
            package=package.strip(),
            title=title.strip(),
            date=normalize_date(raw_date),
        )

    def add_body(self, line: str) -> None:
        body = line.strip()
        if body.startswith("{") and body.endswith("}"):
            self.cves.extend(body[1:-1].split())
            return
        match = _LIST_SUITE.match(body)
        if match:
            suite = match.group(1).strip()
            _package, _sep, version = match.group(2).rpartition(" ")
            if version:
                self.suites.append((suite, version))

    def finish(self) -> SecurityAdvisory:
        return SecurityAdvisory(
            id=self.advisory_id,
            package=self.package,
            title=self.title,
            date=self.date,
            source=self.advisory_id.split("-", 1)[0],
            cves=tuple(self.cves),
            suites=tuple(self.suites),
        )


def parse_advisory_list(markup: str) -> list[SecurityAdvisory]:
    """解析 security-tracker 的完整列表（启动核对/补发用）。"""
    items: list[SecurityAdvisory] = []
    current: _ListEntry | None = None
    for line in markup.splitlines():
        if line.startswith("[") and _LIST_HEADER.match(line):
            if current is not None:
                items.append(current.finish())
            current = _ListEntry.parse(line)
        elif current is not None and line.startswith("\t"):
            current.add_body(line)
    if current is not None:
        items.append(current.finish())
    return items


def parse_tracker_page(markup: str) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """从 tracker 页提取 CVE 列表和 ``(suite, 修复版本)`` 列表。"""
    soup = BeautifulSoup(markup, "html.parser")
    return _parse_references(soup), _parse_fixed_versions(soup)


def _parse_references(soup: BeautifulSoup) -> tuple[str, ...]:
    for row in soup.find_all("tr"):
        header = row.find("b")
        if header is None or header.get_text(strip=True) != "References":
            continue
        cves = []
        for anchor in row.find_all("a"):
            text = anchor.get_text(strip=True)
            if text.startswith("CVE-"):
                cves.append(text)
        return tuple(cves)
    return ()


def _parse_fixed_versions(soup: BeautifulSoup) -> tuple[tuple[str, str], ...]:
    for table in soup.find_all("table"):
        headers = [_normalise_header(th.get_text(" ", strip=True)) for th in table.find_all("th")]
        if "Fixed Version" not in headers:
            continue
        index = {name: position for position, name in enumerate(headers)}
        suites = _collect_fixed_rows(table, index)
        if suites:
            return tuple(suites)

    # 回退：”Vulnerable and fixed packages“ 表里 status=fixed 的行
    for table in soup.find_all("table"):
        headers = [_normalise_header(th.get_text(" ", strip=True)) for th in table.find_all("th")]
        if "Status" not in headers or "Version" not in headers:
            continue
        index = {name: position for position, name in enumerate(headers)}
        suites = []
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) <= max(index.values()):
                continue
            if cells[index["Status"]].get_text(strip=True).lower() != "fixed":
                continue
            release = cells[index["Release"]].get_text(" ", strip=True).replace(" (security)", "")
            version = cells[index["Version"]].get_text(" ", strip=True)
            if release and version:
                suites.append((release, version))
        if suites:
            return tuple(suites)
    return ()


def _collect_fixed_rows(table: BeautifulSoup, index: dict[str, int]) -> list[tuple[str, str]]:
    suites: list[tuple[str, str]] = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if len(cells) <= max(index.values()):
            continue
        release = cells[index["Release"]].get_text(" ", strip=True)
        version = cells[index["Fixed Version"]].get_text(" ", strip=True)
        if release and version:
            suites.append((release, version))
    return suites


def _normalise_header(text: str) -> str:
    return " ".join(text.split())


def format_advisory(adv: SecurityAdvisory) -> str:
    """把公告格式化成 Telegram HTML 卡片。"""
    lines = [
        f'🛡️ <a href="{_url(tracker_url(adv.id))}">{html.escape(adv.id)}</a>'
        f" · {_text(adv.package)}"
    ]

    if adv.suites:
        first_suite, first_version = adv.suites[0]
        lines.append(f"📅 {adv.date}  📦 {_text(first_suite)} {_text(first_version)}")
        for suite, version in adv.suites[1:3]:
            lines.append(f"📦 {_text(suite)} {_text(version)}")
        if len(adv.suites) > 3:
            lines.append(f"… 共 {len(adv.suites)} 个版本")
    else:
        lines.append(f"📅 {adv.date}")

    if adv.cves:
        if len(adv.cves) <= 3:
            lines.append("🐞 " + ", ".join(_text(cve) for cve in adv.cves))
        else:
            shown = ", ".join(_text(cve) for cve in adv.cves[:3])
            lines.append(f"🐞 {shown} … 共 {len(adv.cves)} 个")

    if adv.summary:
        lines.append("📝 " + _text(adv.summary))

    links = []
    full_text_url = advisory_url(adv.id, adv.date)
    if full_text_url:
        links.append(f'<a href="{_url(full_text_url)}">公告全文</a>')
    else:
        links.append(f'<a href="{_url(tracker_url(adv.id))}">Tracker</a>')
    if len(adv.cves) > 3:
        links.append(f'<a href="{_url(tracker_url(adv.id))}">全部 CVE</a>')
    lines.append("🔗 " + " · ".join(links))

    return "\n".join(lines)


def format_digest(items: list[SecurityAdvisory]) -> str:
    """把多条公告压成一份补发摘要。"""
    ordered = sorted(items, key=lambda adv: (adv.date, adv.id), reverse=True)
    dates = sorted({adv.date for adv in ordered}, reverse=True)
    if len(dates) > 1:
        header = f"📋 安全公告补发 · {dates[-1]} ~ {dates[0]} · 共 {len(ordered)} 条"
    elif dates:
        header = f"📋 安全公告补发 · {dates[0]} · 共 {len(ordered)} 条"
    else:
        header = f"📋 安全公告补发 · 共 {len(ordered)} 条"

    lines = [header]
    for adv in ordered:
        lines.append(
            f'• <a href="{_url(tracker_url(adv.id))}">{html.escape(adv.id)}</a>'
            f" {_text(adv.package)}"
        )
    return "\n".join(lines)


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
        digest_threshold: int = 5,
    ) -> None:
        self._client = client
        self._store = store
        self._sender = sender
        self._interval = interval
        self._digest_threshold = digest_threshold
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
            await self._send_digest(unseen)
            for item in unseen:
                self._store.mark(item.id, item.date)
            self._store.save()
            total += len(unseen)

        return total

    async def poll_once(self) -> int:
        """拉取一次 RSS 并推送新公告，返回本次成功推送的数量。"""
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

        if not new_items:
            return 0

        unique = _dedupe(new_items)
        if len(unique) > self._digest_threshold:
            await self._send_digest(unique)
            for item in unique:
                self._store.mark(item.id, item.date)
            self._store.save()
            return len(unique)

        sent = 0
        for item in unique:
            try:
                await self._sender(await self._build_card(item))
            except Exception:
                log.warning("推送安全公告 %s 失败", item.id, exc_info=True)
                break
            self._store.mark(item.id, item.date)
            self._store.save()
            sent += 1
            await asyncio.sleep(SEND_INTERVAL)
        return sent

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

    async def _build_card(self, adv: SecurityAdvisory) -> str:
        try:
            markup = await fetch_text(self._client, tracker_url(adv.id))
            cves, suites = parse_tracker_page(markup)
        except Exception:
            log.debug("获取 %s 的 tracker 页失败，使用 RSS 数据", adv.id, exc_info=True)
            return format_advisory(adv)
        if cves or suites:
            adv = replace(adv, cves=cves or adv.cves, suites=suites or adv.suites)
        return format_advisory(adv)

    async def _send_digest(self, items: list[SecurityAdvisory]) -> None:
        for chunk in split_message(format_digest(items)):
            await self._sender(chunk)
            await asyncio.sleep(SEND_INTERVAL)
