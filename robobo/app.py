"""组装 python-telegram-bot 的 Application。"""

from __future__ import annotations

import logging

from telegram import BotCommand, Message, Update
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)
from telegram.ext.filters import MessageFilter

from . import deps
from .config import Settings
from .handlers import basic, bug, pkg, tracker
from .http import create_client
from .services.udd import UddClient

log = logging.getLogger(__name__)

COMMANDS = [
    BotCommand("start", "开始使用"),
    BotCommand("help", "查看命令列表"),
    BotCommand("tracker", "查询 Debian Tracker 版本"),
    BotCommand("pkg", "从 UDD 查询版本"),
    BotCommand("bug", "查询 Debian Bug"),
    BotCommand("ping", "探活"),
]


class AllowedUsers(MessageFilter):
    """只放行白名单里的用户；``ALLOWED_USER_IDS`` 为空时不会注册该过滤器。"""

    def __init__(self, user_ids: frozenset[int]) -> None:
        super().__init__()
        self._ids = frozenset(user_ids)

    def filter(self, message: Message) -> bool:
        user = getattr(message, "from_user", None)
        return bool(user and user.id in self._ids)


async def _post_shutdown(app: Application) -> None:
    http = app.bot_data.pop(deps.HTTP_CLIENT, None)
    if http is not None:
        await http.aclose()

    udd = app.bot_data.pop(deps.UDD_CLIENT, None)
    if udd is not None:
        await udd.close()

    log.info("robobo stopped")


def _make_post_init(settings: Settings):
    async def post_init(app: Application) -> None:
        app.bot_data[deps.HTTP_CLIENT] = create_client(settings.proxy)
        app.bot_data[deps.UDD_CLIENT] = UddClient(settings.udd_dsn)

        try:
            await app.bot.set_my_commands(COMMANDS)
        except Exception:
            log.warning("set_my_commands failed", exc_info=True)

    return post_init


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Unhandled exception while processing an update", exc_info=context.error)

    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text("出了点问题，稍后再试～")
        except Exception:
            log.debug("Failed to send error notice", exc_info=True)


def build_application(settings: Settings) -> Application:
    builder = (
        ApplicationBuilder()
        .token(settings.token)
        .concurrent_updates(True)
        .rate_limiter(AIORateLimiter())
        .post_init(_make_post_init(settings))
        .post_shutdown(_post_shutdown)
    )

    if settings.proxy:
        builder = builder.proxy(settings.proxy).get_updates_proxy(settings.proxy)

    app = builder.build()

    access: MessageFilter | None = None
    if settings.allowed_user_ids:
        access = AllowedUsers(settings.allowed_user_ids)
        log.info("Access restricted to %d user(s)", len(settings.allowed_user_ids))
    else:
        log.warning("ALLOWED_USER_IDS 为空：任何找到这个 bot 的人都能使用，生产环境建议配置白名单")

    app.add_handler(CommandHandler("start", basic.start, filters=access))
    app.add_handler(CommandHandler("ping", basic.ping, filters=access))
    app.add_handler(CommandHandler("help", basic.help_command, filters=access))
    app.add_handler(CommandHandler("tracker", tracker.tracker_command, filters=access))
    app.add_handler(CommandHandler("pkg", pkg.pkg_command, filters=access))
    app.add_handler(CommandHandler("bug", bug.bug_command, filters=access))
    app.add_error_handler(on_error)

    return app
