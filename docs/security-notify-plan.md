# Debian 安全公告（DSA / DLA）自动通知 — 方案与实现记录

状态：**已实现**（2026-11-20）

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
| 长内容 | 一条公告一张卡片；CVE 折叠；摘要取正文首句；补发用摘要 |
| 套件过滤 | 不按套件过滤 |

## 2. 数据源实测

| 来源 | URL | 完整性 | 条件请求 |
| --- | --- | --- | --- |
| DSA RSS | `https://www.debian.org/security/dsa-long` | 最近 30 条，含正文 | ETag / Last-Modified |
| DLA RSS | `https://www.debian.org/lts/security/dla-long` | 最近 30 条，含正文 | 同上 |
| Tracker DSA 列表 | `https://salsa.debian.org/security-tracker-team/security-tracker/-/raw/master/data/DSA/list` | 2002 至今 6716 条（1.25 MB） | ETag → 304 |
| Tracker DLA 列表 | `.../data/DLA/list` | 2014 至今 5113 条（990 KB） | 同上 |
| Tracker 页 | `https://security-tracker.debian.org/tracker/<ID>` | CVE + 修复版本 | — |

要点：

- 常见猜测路径 `security/dsa.rss` / `dla.rss` / `security/dsa/` 都是 404；真实 RSS 不带扩展名，且是 RDF（RSS 1.0）。
- 列表条目：`[19 Nov 2026] DSA-6643-1 pam-mysql - security update`，随后 `\t{CVE...}`、`\t[suite] - pkg version`；无空行分隔。
- 单条公告 CVE 数典型 1–7，`linux` 的 DSA 可达 **1311** 个；近期 suite 最多 2，历史最多 20。
- 2006 年的旧条目没有修订号（`DSA-1209`），解析已兼容。

## 3. 实现

| 文件 | 作用 |
| --- | --- |
| `robobo/services/security.py` | 解析 RSS / 完整列表 / tracker 页；格式化卡片与摘要；`SecurityNotifier` 轮询 |
| `robobo/services/seen_store.py` | 已见 ID + ETag 的原子持久化 |
| `robobo/http.py` | 新增 `fetch_text_conditional`（`If-None-Match` / 304） |
| `robobo/text.py` | `split_message` 从 handlers 移到此处（service 不依赖 Telegram） |
| `robobo/config.py` | `SECURITY_*` 配置与解析 |
| `robobo/app.py` / `deps.py` | 装配与生命周期 |

### 3.1 轮询与判重

1. 启动：`reconcile()` 拉取完整列表。
   - 无状态文件 → 全部标记已见（基线），不发历史。
   - 有状态文件 → 未见条目发一条摘要，标记已见。
2. 每 5 分钟：`poll_once()` 拉取两个 RSS（带 ETag，未变 304）。
   - 未见 ID → 抓 tracker 页补 CVE/版本 → 发卡。
   - 单条发送失败 → 不标记，下轮重试。
   - 单次超过 `SECURITY_DIGEST_THRESHOLD` → 发摘要。
3. 每成功一条立即原子落盘（崩溃只影响剩余条目）。

### 3.2 消息格式

单条卡片：

```
🛡️ <a href="tracker">DSA-6643-1</a> · pam-mysql
📅 2026-11-19  📦 trixie 0.8.2-2.1+deb13u1
🐞 CVE-2026-124749
📝 Andrey Vasilevsky discovered that pam-mysql …
🔗 公告全文
```

- CVE ≤ 3 全列；> 3 列前 3 + `… 共 N 个` + 「全部 CVE」链接。
- 摘要取正文首句，≤ 120 字符。
- 多套件每行一个 `📦`；超过 3 个折叠为 `… 共 N 个版本`。
- 无 CVE / 无摘要时省略对应行。

补发摘要：

```
📋 安全公告补发 · 2026-11-18 ~ 2026-11-19 · 共 3 条
• <a href="tracker">DSA-6643-1</a> pam-mysql
…
```

## 4. 配置

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SECURITY_NOTIFY_ENABLED` | `false` | 总开关 |
| `SECURITY_POLL_INTERVAL` | `300` | 秒，最小 60 |
| `SECURITY_STATE_FILE` | `state/security.json` | 状态文件 |
| `SECURITY_DIGEST_THRESHOLD` | `5` | 超过则发摘要 |
| `NOTIFY_CHAT_ID` | — | 复用，启用时必填 |

## 5. 部署

- Docker 挂持久卷 `-v robobo-state:/app/state`（compose 已内置 named volume）。
- 未挂卷时每次重启重新基线，会漏掉重启期间的公告。

## 6. 测试

`tests/test_security.py`、`tests/test_http.py`、`tests/test_utils.py`：

- RDF / 完整列表 / tracker 页解析（含旧格式、无修订号、无 CVE、多套件）。
- 摘要提取与截断、日期归一化。
- 卡片/摘要格式化（折叠、转义、链接）。
- `SeenStore` 往返、`.bak` 回退。
- Notifier：基线、补发、富卡片、去重、ETag/304、失败重试、摘要阈值。
- HTTP 条件请求：200/304/重试/网络错误。

验证：`ruff check` 通过，`pytest` 全部通过；并用真实数据端到端渲染核对。
