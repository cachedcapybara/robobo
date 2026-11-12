"""基础的 /start、/ping、/help 命令。"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from .. import __version__
from .common import reply_html

HELP_TEXT = """\
<b>robobo</b> —— Debian 信息查询机器人 <i>v{version}</i>

<b>可用命令</b>
• <code>/tracker 包名</code> —— 各发行版中的版本（tracker.debian.org）
• <code>/pkg 包名</code> —— 从 UDD 数据库查版本
• <code>/bug 编号</code> —— Debian BTS 缺陷详情
• <code>/ping</code> —— 探活
"""


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_html(update.effective_message, "Hi~ 发送 /help 看看我能做什么。")


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_html(update.effective_message, "I am still alive.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await reply_html(update.effective_message, HELP_TEXT.format(version=__version__))
