"""纯文本工具：长消息按行分块。

放在这里而不是 ``handlers/``，是为了让 ``services/`` 也能用，而不必导入
Telegram 相关模块（保持 service 不碰 Telegram 的分层约定）。
"""

from __future__ import annotations

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
