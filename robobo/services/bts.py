"""Debian BTS（bugs.debian.org）页面抓取与解析。"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

from ..http import fetch_text

BUG_URL = "https://bugs.debian.org/{bug_id}"
_LEADING_ID = re.compile(r"^#\s*\d+\s*[.,:]?\s*")
_TITLE_PREFIX = re.compile(r"^(?:Debian Bug report logs?\s*[-:]\s*)", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class BugInfo:
    bug_id: str
    title: str


async def fetch_bug(client: httpx.AsyncClient, bug_id: str) -> BugInfo:
    markup = await fetch_text(client, BUG_URL.format(bug_id=bug_id))
    return parse_bug_page(markup, bug_id=bug_id)


def parse_bug_page(markup: str, bug_id: str) -> BugInfo:
    soup = BeautifulSoup(markup, "html.parser")

    title = ""
    heading = soup.find("h1")
    if heading is not None:
        title = heading.get_text(" ", strip=True)

    if title:
        title = _LEADING_ID.sub("", title).strip()

    if not title:
        tag = soup.find("title")
        if tag is not None:
            title = _TITLE_PREFIX.sub("", tag.get_text(" ", strip=True)).strip()

    return BugInfo(bug_id=bug_id, title=title or f"Bug #{bug_id}")


def format_bug(info: BugInfo) -> str:
    url = BUG_URL.format(bug_id=quote_id(info.bug_id))
    return (
        f"<b>#{html.escape(info.bug_id)} — {html.escape(info.title)}</b>\n"
        f'<a href="{html.escape(url, quote=True)}">{html.escape(url)}</a>'
    )


def quote_id(bug_id: str) -> str:
    # bug id 已在 handler 里校验为纯数字，这里只做防御性转义
    return "".join(ch for ch in bug_id if ch.isdigit())
