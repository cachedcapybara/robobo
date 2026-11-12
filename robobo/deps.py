"""从 ``application.bot_data`` 里取共享依赖的辅助函数。

handlers 不直接访问全局变量，测试时可以注入假的客户端。
"""

from __future__ import annotations

import httpx
from telegram.ext import ContextTypes

from .services.udd import UddClient

HTTP_CLIENT = "http_client"
UDD_CLIENT = "udd_client"


def http_client(context: ContextTypes.DEFAULT_TYPE) -> httpx.AsyncClient:
    return context.application.bot_data[HTTP_CLIENT]


def udd_client(context: ContextTypes.DEFAULT_TYPE) -> UddClient:
    return context.application.bot_data[UDD_CLIENT]
