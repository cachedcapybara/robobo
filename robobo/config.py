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
    )
