"""组装 python-telegram-bot 的 Application。"""

from __future__ import annotations

import logging

from telegram import BotCommand, LinkPreviewOptions, Message, Update
from telegram.constants import ParseMode
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
from .services.discourse import DiscourseNotifier
from .services.security import SecurityNotifier
from .services.seen_store import SeenStore
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

    notifier = app.bot_data.pop(deps.DISCOURSE_NOTIFIER, None)
    if notifier is not None:
        await notifier.stop()

    security = app.bot_data.pop(deps.SECURITY_NOTIFIER, None)
    if security is not None:
        await security.stop()

    log.info("robobo stopped")


def _make_post_init(settings: Settings):
    async def post_init(app: Application) -> None:
        http = create_client(settings.proxy)
        app.bot_data[deps.HTTP_CLIENT] = http
        app.bot_data[deps.UDD_CLIENT] = UddClient(settings.udd_dsn)

        chat_id = settings.notify_chat_id

        async def send_notification(text: str) -> None:
            await app.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode=ParseMode.HTML,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )

        if settings.discourse_url and chat_id:
            notifier = DiscourseNotifier(
                http,
                base_url=settings.discourse_url,
                category=settings.discourse_category,
                interval=settings.discourse_poll_interval,
                sender=send_notification,
            )
            notifier.start()
            app.bot_data[deps.DISCOURSE_NOTIFIER] = notifier
            log.info(
                "Discourse 通知已启动：%s（每 %ss 轮询一次，推送到 %s）",
                settings.discourse_url,
                settings.discourse_poll_interval,
                chat_id,
            )
        elif settings.discourse_url:
            log.warning("已配置 DISCOURSE_URL 但缺少 NOTIFY_CHAT_ID，Discourse 通知不会启动")

        if settings.security_notify_enabled and chat_id:
            store = SeenStore.load(settings.security_state_file)
            security = SecurityNotifier(
                http,
                store=store,
                sender=send_notification,
                interval=settings.security_poll_interval,
                digest_threshold=settings.security_digest_threshold,
            )
            security.start()
            app.bot_data[deps.SECURITY_NOTIFIER] = security
            log.info(
                "安全公告通知已启动：每 %ss 轮询一次，推送到 %s（状态文件 %s）",
                settings.security_poll_interval,
                chat_id,
                settings.security_state_file,
            )
        elif settings.security_notify_enabled:
            log.warning(
                "已启用 SECURITY_NOTIFY_ENABLED 但缺少 NOTIFY_CHAT_ID，安全公告通知不会启动"
            )

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
