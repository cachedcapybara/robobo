"""配置与消息工具的单元测试。"""

from __future__ import annotations

import asyncio
from unittest.mock import create_autospec

import pytest
from telegram import Message
from telegram.constants import ParseMode

from robobo.config import ConfigError, load_settings, parse_poll_interval, parse_user_ids
from robobo.handlers.common import SAFE_LIMIT, reply_html, split_message


def test_parse_user_ids_with_mixed_separators() -> None:
    assert parse_user_ids("1, 2 3") == frozenset({1, 2, 3})


def test_parse_user_ids_empty() -> None:
    assert parse_user_ids(None) == frozenset()
    assert parse_user_ids("   ") == frozenset()


def test_parse_user_ids_rejects_garbage() -> None:
    with pytest.raises(ConfigError):
        parse_user_ids("1, abc")


def test_parse_poll_interval_defaults() -> None:
    assert parse_poll_interval(None) == 60.0
    assert parse_poll_interval("   ") == 60.0
    assert parse_poll_interval("30") == 30.0


def test_parse_poll_interval_rejects_invalid() -> None:
    with pytest.raises(ConfigError):
        parse_poll_interval("abc")
    with pytest.raises(ConfigError):
        parse_poll_interval("0")


def test_load_settings_requires_bot_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setattr("robobo.config.load_dotenv", lambda: None)

    with pytest.raises(ConfigError):
        load_settings()


def test_load_settings_parses_discourse_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("robobo.config.load_dotenv", lambda: None)
    monkeypatch.setenv("BOT_TOKEN", "123:abc")
    monkeypatch.setenv("DISCOURSE_URL", "https://forum.example.com")
    monkeypatch.setenv("DISCOURSE_CATEGORY", "ask")
    monkeypatch.setenv("DISCOURSE_POLL_INTERVAL", "30")
    monkeypatch.setenv("NOTIFY_CHAT_ID", "-1001234567890")

    settings = load_settings()

    assert settings.discourse_url == "https://forum.example.com"
    assert settings.discourse_category == "ask"
    assert settings.discourse_poll_interval == 30.0
    assert settings.notify_chat_id == "-1001234567890"


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


def test_reply_html_uses_ptb22_reply_text_arguments() -> None:
    """PTB 22 移除了 reply_text 的 quote 参数，只能用 do_quote。"""

    async def run() -> None:
        message = create_autospec(Message, instance=True)

        await reply_html(message, "hello")

        message.reply_text.assert_awaited_once()
        _, kwargs = message.reply_text.await_args
        assert "quote" not in kwargs
        assert kwargs["parse_mode"] == ParseMode.HTML
        assert kwargs["link_preview_options"] is not None

    asyncio.run(run())
