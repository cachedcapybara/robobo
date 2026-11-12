# robobo

一个查询 Debian 生态信息的小型 Telegram Bot，基于 **python-telegram-bot 22.x**（异步）+ **long polling**。

> 不需要域名、不需要公网 IP、不需要 TLS 证书——只要运行它的机器能出网访问 `api.telegram.org`。

## 功能

| 命令 | 说明 |
| --- | --- |
| `/start` | 欢迎语 |
| `/help` | 命令列表 |
| `/ping` | 探活，返回 `I am still alive.` |
| `/tracker <包名>` | 抓取 [tracker.debian.org](https://tracker.debian.org) 页面，列出 stable / testing / unstable 等各发行版版本 |
| `/pkg <包名>` | 从 [UDD](https://udd.debian.org/) 镜像查询版本（可关闭） |
| `/bug <编号>` | 查询 [Debian BTS](https://bugs.debian.org) 缺陷标题与链接 |

## 快速开始

需要 Python 3.10 及以上。

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env      # 填入 BOT_TOKEN
python -m robobo          # 或 python main.py
```

在 Telegram 里给 bot 发送 `/start` 即可。

## 配置

全部通过环境变量（`.env` 仅用于本地开发，已被 `.gitignore` 排除）：

| 变量 | 必填 | 说明 |
| --- | --- | --- |
| `BOT_TOKEN` | ✅ | 从 [@BotFather](https://t.me/BotFather) 获取 |
| `ALLOWED_USER_IDS` | — | 用户白名单，逗号/空格分隔；留空表示所有人可用 |
| `UDD_DSN` | — | UDD 连接串；留空则 `/pkg` 回复"未配置" |
| `TELEGRAM_PROXY` | — | 访问 `api.telegram.org` 需要代理时填写，支持 `http://` / `socks5://` |
| `LOG_LEVEL` | — | 默认 `INFO` |

> `socks5://` 代理需要在镜像/环境里额外安装 `httpx[socks]`。

## Docker 部署

```bash
cp .env.example .env    # 填 token
docker compose up -d --build
docker compose logs -f
```

或者不用 compose：

```bash
docker build -t robobo .
docker run -d --name robobo --restart unless-stopped --env-file .env robobo
```

镜像以非 root 用户运行，不暴露任何端口（long polling 只做出站连接）。

## systemd（不用 Docker）

```ini
[Unit]
Description=robobo telegram bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/robobo
EnvironmentFile=/opt/robobo/.env
ExecStart=/opt/robobo/.venv/bin/python -m robobo
Restart=always
RestartSec=5
User=robobo

[Install]
WantedBy=multi-user.target
```

## 为什么不需要域名？

Telegram 提供两种收消息的方式：

- **Webhook**：Telegram 主动 POST 到你的公网 HTTPS 地址 → 必须有域名 + 有效证书 + 开放端口。
- **Long polling**：bot 主动向 Telegram 拉取更新 → **只需要出站网络**。

本项目使用 long polling，所以可以跑在：

- 任意一台能上网的 VPS / 家用电脑 / 树莓派；
- NAT、防火墙后面的内网机器；
- 免费或低价的容器平台。

代价只有一个：同一个 token 同时只能有一个 polling 进程，否则会报 `409 Conflict`。

## 项目结构

```
robobo/
├── app.py            # Application 组装、handler 注册、错误处理
├── __main__.py       # 入口（python -m robobo）
├── config.py         # 环境变量 → Settings
├── http.py           # 共享 httpx 客户端 + 超时/重试
├── deps.py           # 从 bot_data 取共享依赖
├── handlers/         # Telegram 交互层（只做参数校验和文案）
│   ├── basic.py
│   ├── tracker.py
│   ├── pkg.py
│   ├── bug.py
│   └── common.py     # HTML 分块发送
└── services/         # 纯业务逻辑（可独立测试）
    ├── tracker.py
    ├── bts.py
    └── udd.py
tests/                # pytest，不联网
```

设计原则：**handler 不写解析逻辑，service 不碰 Telegram**，因此换框架（aiogram / grammY）时只需重写 `handlers/`。

## 开发

```bash
pip install -r requirements-dev.txt
pytest
ruff check .
ruff format .        # 可选
```

## 从旧版迁移

旧版是一个 135 行的单文件脚本（`python-telegram-bot==5.3.0` + webhook），已在 git 历史中。新版的主要变化：

- `Updater` → `Application` + `run_polling()`，删除域名/webhook 配置；
- 同步 `requests` → 异步 `httpx`，所有请求带超时和指数退避重试；
- `psycopg2` 每次新建连接 → `asyncpg` 连接池；
- `config.py`（提交了 token）→ 环境变量；
- legacy Markdown → HTML 并做 `html.escape`；
- 长消息自动分块；解析逻辑与 Telegram 解耦并有单元测试。
