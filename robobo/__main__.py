"""入口：``python -m robobo``。"""

from __future__ import annotations

import logging
import sys

from telegram import Update
from telegram.error import InvalidToken

from . import __version__
from .app import build_application
from .config import ConfigError, load_settings

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"

log = logging.getLogger("robobo")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format=LOG_FORMAT,
    )

    app = build_application(settings)
    log.info("Starting robobo %s in long-polling mode", __version__)

    try:
        # 不需要域名、证书或公网 IP，只要机器能出网访问 api.telegram.org
        app.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
    except InvalidToken:
        log.error("BOT_TOKEN 无效，请向 @BotFather 确认后重试")
        return 2
    except KeyboardInterrupt:  # pragma: no cover - 交互式停止
        pass

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
