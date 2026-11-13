"""Telegram 消息相关的工具：HTML 转义后的分块发送。"""

from __future__ import annotations

import logging

from telegram import LinkPreviewOptions, Message
from telegram.constants import ParseMode

log = logging.getLogger(__name__)

# Telegram 上限 4096，留出余量给标签
SAFE_LIMIT = 3800


def split_message(text: str, limit: int = SAFE_LIMIT) -> list[str]:
    """按行切分长消息，避免超过 Telegram 的 4096 字符限制。

    只在换行处切分，因此每一块内部的 HTML 标签仍然是配对的。
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    size = 0

    for line in text.split("\n"):
        # 单行仍然过长时硬切（正常业务里不会出现）
        while len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current, size = [], 0
            chunks.append(line[:limit])
            line = line[limit:]

        line_size = len(line) + 1
        if current and size + line_size > limit:
            chunks.append("\n".join(current))
            current, size = [], 0

        current.append(line)
        size += line_size

    if current:
        chunks.append("\n".join(current))

    return [chunk for chunk in chunks if chunk]


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
