"""Telegram 消息相关的工具：HTML 转义后的分块发送。"""

from __future__ import annotations

import logging

from telegram import LinkPreviewOptions, Message
from telegram.constants import ParseMode

from ..text import SAFE_LIMIT, split_message

__all__ = ["SAFE_LIMIT", "reply_html", "split_message"]

log = logging.getLogger(__name__)


async def reply_html(message: Message | None, text: str) -> None:
    """以 HTML 模式回复，自动分块并关闭链接预览。"""
    if message is None:
        return

    for chunk in split_message(text):
        await message.reply_text(
            chunk,
            parse_mode=ParseMode.HTML,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            do_quote=False,
        )
