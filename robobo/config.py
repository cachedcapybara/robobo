"""环境变量 → Settings。

``.env`` 仅用于本地开发（已被 .gitignore 排除）；生产环境使用真实环境变量。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


class ConfigError(ValueError):
    """配置缺失或格式错误。"""


@dataclass(frozen=True, slots=True)
class Settings:
    token: str
    log_level: str = "INFO"
    proxy: str | None = None
    udd_dsn: str | None = None
    allowed_user_ids: frozenset[int] = field(default_factory=frozenset)
    discourse_url: str | None = None
    discourse_category: str | None = None
    discourse_poll_interval: float = 60.0
    notify_chat_id: str | None = None
    security_notify_enabled: bool = False
    security_poll_interval: float = 300.0
    security_state_file: str = "state/security.json"
    security_digest_threshold: int = 5


def parse_user_ids(raw: str | None) -> frozenset[int]:
    """解析 ``ALLOWED_USER_IDS``：逗号或空格分隔的 Telegram 用户 ID。"""
    if raw is None or not raw.strip():
        return frozenset()

    ids: set[int] = set()
    for part in raw.replace(",", " ").split():
        if not part.isdigit():
            raise ConfigError(f"ALLOWED_USER_IDS 含非法值：{part!r}（只接受数字 ID）")
        ids.add(int(part))
    return frozenset(ids)


def parse_poll_interval(
    raw: str | None,
    default: float = 60.0,
    *,
    name: str = "DISCOURSE_POLL_INTERVAL",
    minimum: float = 0.0,
) -> float:
    """解析轮询间隔（秒），留空时用默认值。"""
    if raw is None or not raw.strip():
        return default

    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是数字：{raw!r}") from exc
    if value <= minimum:
        limit = int(minimum) if minimum == int(minimum) else minimum
        raise ConfigError(f"{name} 必须大于 {limit}")
    return value


def parse_bool(raw: str | None, default: bool = False, *, name: str = "BOOL") -> bool:
    """解析布尔环境变量：true/false/yes/no/on/off/1/0。"""
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} 必须是 true/false：{raw!r}")


def parse_positive_int(raw: str | None, default: int, *, name: str) -> int:
    """解析正整数环境变量。"""
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} 必须是整数：{raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{name} 必须大于 0")
    return value


def load_settings() -> Settings:
    """从环境变量读取配置；存在 ``.env`` 时先加载（不覆盖已有环境变量）。"""
    load_dotenv()

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise ConfigError("缺少必填环境变量 BOT_TOKEN（可从 @BotFather 获取）")

    return Settings(
        token=token,
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        proxy=os.getenv("TELEGRAM_PROXY", "").strip() or None,
        udd_dsn=os.getenv("UDD_DSN", "").strip() or None,
        allowed_user_ids=parse_user_ids(os.getenv("ALLOWED_USER_IDS")),
        discourse_url=os.getenv("DISCOURSE_URL", "").strip() or None,
        discourse_category=os.getenv("DISCOURSE_CATEGORY", "").strip() or None,
        discourse_poll_interval=parse_poll_interval(os.getenv("DISCOURSE_POLL_INTERVAL")),
        notify_chat_id=os.getenv("NOTIFY_CHAT_ID", "").strip() or None,
        security_notify_enabled=parse_bool(
            os.getenv("SECURITY_NOTIFY_ENABLED"), name="SECURITY_NOTIFY_ENABLED"
        ),
        security_poll_interval=parse_poll_interval(
            os.getenv("SECURITY_POLL_INTERVAL"),
            default=300.0,
            name="SECURITY_POLL_INTERVAL",
            minimum=60.0,
        ),
        security_state_file=os.getenv("SECURITY_STATE_FILE", "").strip()
        or "state/security.json",
        security_digest_threshold=parse_positive_int(
            os.getenv("SECURITY_DIGEST_THRESHOLD"),
            default=5,
            name="SECURITY_DIGEST_THRESHOLD",
        ),
    )
