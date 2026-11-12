"""配置与消息工具的单元测试。"""

from __future__ import annotations

import pytest

from robobo.config import ConfigError, parse_user_ids
from robobo.handlers.common import SAFE_LIMIT, split_message


def test_parse_user_ids_with_mixed_separators() -> None:
    assert parse_user_ids("1, 2 3") == frozenset({1, 2, 3})


def test_parse_user_ids_empty() -> None:
    assert parse_user_ids(None) == frozenset()
    assert parse_user_ids("   ") == frozenset()


def test_parse_user_ids_rejects_garbage() -> None:
    with pytest.raises(ConfigError):
        parse_user_ids("1, abc")


def test_split_message_keeps_short_text_intact() -> None:
    assert split_message("hello") == ["hello"]


def test_split_message_respects_limit() -> None:
    text = "\n".join(f"line {i}" for i in range(2000))

    chunks = split_message(text)

    assert len(chunks) > 1
    assert all(len(chunk) <= SAFE_LIMIT for chunk in chunks)
    assert "\n".join(chunks) == text


def test_split_message_hard_splits_long_line() -> None:
    text = "x" * (SAFE_LIMIT * 2 + 10)

    chunks = split_message(text)

    assert all(len(chunk) <= SAFE_LIMIT for chunk in chunks)
    assert "".join(chunks) == text
