"""Debian Package Tracker（tracker.debian.org）页面抓取与解析。"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from bs4 import BeautifulSoup
from bs4.element import PageElement, Tag

from ..http import fetch_text

TRACKER_URL = "https://tracker.debian.org/pkg/{name}"
_DIGIT = re.compile(r"\d")
_VERSIONS_HINT = re.compile(r"versions", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class VersionEntry:
    suite: str
    version: str


@dataclass(frozen=True, slots=True)
class PackageInfo:
    name: str
    versions: tuple[VersionEntry, ...] = ()


def package_url(name: str) -> str:
    return TRACKER_URL.format(name=quote(name, safe=""))


async def fetch_package(client: httpx.AsyncClient, name: str) -> PackageInfo:
    markup = await fetch_text(client, package_url(name))
    return parse_package_page(markup, fallback_name=name)


def parse_package_page(markup: str, fallback_name: str) -> PackageInfo:
    """从 Tracker 页面里取出包名和各发行版的版本号。

    页面改版时这里只会返回空版本列表，不会抛异常——调用方负责给出提示。
    """
    soup = BeautifulSoup(markup, "html.parser")

    heading = soup.find("h1")
    name = heading.get_text(" ", strip=True) if heading else ""

    return PackageInfo(
        name=name or fallback_name,
        versions=tuple(_parse_versions(soup)),
    )


def _parse_versions(soup: BeautifulSoup) -> list[VersionEntry]:
    hint = soup.find(string=_VERSIONS_HINT)
    if hint is None:
        return []

    panel = _find_versions_panel(hint)
    if panel is None:
        return []

    entries: list[VersionEntry] = []
    for item in panel.find_all("li"):
        label = item.find("b")
        suite = label.get_text(" ", strip=True) if label else ""
        version = _extract_version(item, suite)
        if version:
            entries.append(VersionEntry(suite=suite or "?", version=version))
    return entries


def _find_versions_panel(hint: PageElement) -> Tag | None:
    parent = hint.find_parent(class_="panel-heading")
    if parent is not None:
        return parent.find_next_sibling(class_="panel-body")
    # 有些页面没有 panel-heading，直接找最近的 panel-body
    return hint.find_parent(class_="panel-body")


def _extract_version(item: Tag, suite: str) -> str:
    link = item.find("a")
    if link is not None:
        linked = link.get_text(" ", strip=True)
        if _DIGIT.search(linked):
            return linked

    text = item.get_text(" ", strip=True)
    if suite:
        text = text.replace(suite, "", 1)
    return text.strip(" :")


def format_package(info: PackageInfo) -> str:
    url = package_url(info.name)
    lines = [
        f'<b>Package:</b> <a href="{html.escape(url, quote=True)}">{html.escape(info.name)}</a>'
    ]

    if not info.versions:
        lines.append("")
        lines.append("没解析到版本信息，tracker.debian.org 的页面结构可能变了。")
        return "\n".join(lines)

    lines.append("")
    for entry in info.versions:
        lines.append(
            f"• <b>{html.escape(entry.suite)}</b>: <code>{html.escape(entry.version)}</code>"
        )
    return "\n".join(lines)
