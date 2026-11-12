"""``/tracker`` —— 查询 Debian Package Tracker。"""

from __future__ import annotations

import html
import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from ..deps import http_client
from ..http import FetchError, NotFound
from ..services.tracker import fetch_package, format_package
from .common import reply_html

log = logging.getLogger(__name__)

PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]{0,99}$")

USAGE = "你要告诉我你要找什么噢～\n用法：<code>/tracker 包名</code>"


async def tracker_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message

    args = context.args or []
    if not args:
        await reply_html(message, USAGE)
        return

    raw = args[0].strip()
    name = raw.lower()
    if not PACKAGE_RE.fullmatch(name):
        await reply_html(
            message,
            f"这不是合法的 Debian 包名：<code>{html.escape(raw)}</code>",
        )
        return

    try:
        info = await fetch_package(http_client(context), name)
    except NotFound:
        await reply_html(message, f"好像木有找到 <code>{html.escape(name)}</code> 这个包")
        return
    except FetchError as exc:
        log.warning("tracker lookup failed for %s: %s", name, exc)
        await reply_html(message, "tracker.debian.org 暂时连不上，稍后再试～")
        return

    await reply_html(message, format_package(info))
