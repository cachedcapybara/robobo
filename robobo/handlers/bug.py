"""``/bug`` —— 查询 Debian Bug Tracking System。"""

from __future__ import annotations

import logging
import re

from telegram import Update
from telegram.ext import ContextTypes

from ..deps import http_client
from ..http import FetchError, NotFound
from ..services.bts import fetch_bug, format_bug
from .common import reply_html

log = logging.getLogger(__name__)

BUG_RE = re.compile(r"^\d{1,9}$")

USAGE = "用法：<code>/bug 编号</code>"


async def bug_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message

    args = context.args or []
    if not args:
        await reply_html(message, USAGE)
        return

    raw = args[0].strip()
    if not BUG_RE.fullmatch(raw):
        await reply_html(message, "不要用奇怪的东西调戏我啊喂～")
        return

    try:
        info = await fetch_bug(http_client(context), raw)
    except NotFound:
        await reply_html(message, "好像木有这个 bug")
        return
    except FetchError as exc:
        log.warning("BTS lookup failed for #%s: %s", raw, exc)
        await reply_html(message, "bugs.debian.org 暂时连不上，稍后再试～")
        return

    await reply_html(message, format_bug(info))
