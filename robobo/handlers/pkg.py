"""``/pkg`` —— 从 UDD 查询各发行版版本。"""

from __future__ import annotations

import html
import logging

from telegram import Update
from telegram.ext import ContextTypes

from ..deps import udd_client
from ..services.udd import UddError, UddUnavailable
from .common import reply_html
from .tracker import PACKAGE_RE

log = logging.getLogger(__name__)

USAGE = "你要告诉我你要找什么噢～\n用法：<code>/pkg 包名</code>"


async def pkg_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    client = udd_client(context)
    if not client.configured:
        await reply_html(
            message,
            "这个实例没有配置 <code>UDD_DSN</code>，/pkg 暂时不可用。",
        )
        return

    try:
        versions = await client.versions(name)
    except UddUnavailable as exc:
        log.warning("UDD unavailable: %s", exc)
        await reply_html(message, "UDD 暂时不可用，稍后再试～")
        return
    except UddError as exc:
        log.warning("UDD query failed for %s: %s", name, exc)
        await reply_html(message, "查询 UDD 出错了，稍后再试～")
        return

    if not versions:
        await reply_html(message, f"没有找到 <code>{html.escape(name)}</code> 哦")
        return

    lines = [f"<b>Package:</b> <code>{html.escape(name)}</code>", ""]
    for item in versions:
        lines.append(
            f"• <b>{html.escape(item.release)}</b>: <code>{html.escape(item.version)}</code>"
        )
    await reply_html(message, "\n".join(lines))
