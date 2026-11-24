# Debian 安全公告（DSA / DLA）自动通知 — 方案与实现记录

状态：**已实现**（最近更新 2026-11-24）

## 1. 需求与决策

- 定期检查 Debian Security 的 DSA 与 DLA 通知，自动发送到 Telegram 群。
- 目标：**不遗漏**；内容适合即时消息阅读。

| 决策点 | 结论 |
| --- | --- |
| 主源 | 官方 RSS `security/dsa-long`、`lts/security/dla-long`（RDF，30 条窗口） |
| 为何不用完整列表做主源 | 30 条窗口在 5 分钟轮询下不会溢出；RSS 是官方源、格式稳定 |
| 停机兜底 | **首次启动**拉取完整 `DSA/list` / `DLA/list` 核对，补发漏掉的 |
| 判重 | 已见 **ID 集合**（列表按日期倒序，ID 会乱序，不能用最大 ID 水位线） |
| 状态 | 文件 `state/security.json`，原子写 + `.bak`，用户手动挂持久卷 |
| 消息格式 | 只留一个 emoji：`🛡️ Security` 标题 + 每行 `编号 · 包名 · 类型`，多条合并成一条消息 |
| enrichment | **不做**每条抓 tracker 页（新格式不需要 CVE/版本/摘要），只保留必要请求 |
| 套件过滤 | 不按套件过滤 |

## 2. 数据源实测

| 来源 | URL | 完整性 | 条件请求 |
| --- | --- | --- | --- |
| DSA RSS | `https://www.debian.org/security/dsa-long` | 最近 30 条，含正文 | ETag / Last-Modified |
| DLA RSS | `https://www.debian.org/lts/security/dla-long` | 最近 30 条，含正文 | 同上 |
| Tracker DSA 列表 | `https://salsa.debian.org/security-tracker-team/security-tracker/-/raw/master/data/DSA/list` | 2002 至今 6716 条（1.25 MB） | ETag → 304 |
| Tracker DLA 列表 | `.../data/DLA/list` | 2014 至今 5113 条（990 KB） | 同上 |
| Tracker 页 | `https://security-tracker.debian.org/tracker/<ID>` | 编号链接目标 | — |

要点：

- 常见猜测路径 `security/dsa.rss` / `dla.rss` / `security/dsa/` 都是 404；真实 RSS 不带扩展名，且是 RDF（RSS 1.0）。
- 列表条目：`[19 Nov 2026] DSA-6643-1 pam-mysql - security update`。**尾部的 `security update` / `regression update` 等就是类型**，无独立字段。
- 类型分布（2024+）：DSA 只有 `security update`(1063) 与 `regression update`(24)；DLA 还有 `new timezone database`、`database update`、`bugfix update`、`CA certificates update`、`new upstream version` 等。
- 2006 年的旧条目没有修订号（`DSA-1209`），解析已兼容。

## 3. 实现

| 文件 | 作用 |
| --- | --- |
| `robobo/services/security.py` | 解析 RSS / 完整列表；格式化合并消息；`SecurityNotifier` 轮询 |
| `robobo/services/seen_store.py` | 已见 ID + ETag 的原子持久化 |
| `robobo/http.py` | 新增 `fetch_text_conditional`（`If-None-Match` / 304） |
| `robobo/text.py` | `split_message` / `SAFE_LIMIT`（service 不依赖 Telegram） |
| `robobo/config.py` | `SECURITY_*` 配置与解析 |
| `robobo/app.py` / `deps.py` | 装配与生命周期 |

### 3.1 轮询与判重

1. 启动：`reconcile()` 拉取完整列表。
   - 无状态文件 → 全部标记已见（基线），不发历史。
   - 有状态文件 → 未见条目合并成一条消息补发，标记已见。
2. 每 5 分钟：`poll_once()` 拉取两个 RSS（带 ETag，未变 304）。
   - 未见 ID 合并成一条消息发送。
   - 发送失败 → 不标记，下轮重试。
3. 发送成功后原子落盘（崩溃只影响剩余条目）。

### 3.2 消息格式

```
🛡️ Security
<a href="tracker">DSA-6654-1</a> · mediawiki · security update
<a href="tracker">DLA-4932-1</a> · chromium · security update
```

- 只保留一个 emoji；编号带 tracker 页链接；中间为包名，末尾为类型。
- 同一批次（含补发）合并成一条消息；超过 Telegram 4096 字符时按行分块，每块重复 `🛡️ Security` 头。

## 4. 配置

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SECURITY_NOTIFY_ENABLED` | `false` | 总开关 |
| `SECURITY_POLL_INTERVAL` | `300` | 秒，最小 60 |
| `SECURITY_STATE_FILE` | `state/security.json` | 状态文件 |
| `NOTIFY_CHAT_ID` | — | 复用，启用时必填 |

## 5. 部署

- Docker 挂持久卷 `-v robobo-state:/app/state`（compose 已内置 named volume；镜像预建并 chown 该目录）。
- 未挂卷时每次重启重新基线，会漏掉重启期间的公告。

## 6. 测试

`tests/test_security.py`、`tests/test_http.py`、`tests/test_utils.py`：

- RDF / 完整列表解析（含旧格式、无修订号、类型字段）。
- 日期归一化。
- 行格式化与转义；合并消息、排序、超长按行分块。
- `SeenStore` 往返、`.bak` 回退。
- Notifier：基线、补发、合并消息、去重、ETag/304、失败重试、畸形 feed 跳过。
- HTTP 条件请求：200/304/重试/网络错误。

验证：`ruff check` 通过，`pytest` 全部通过；并用真实数据端到端渲染核对。
