"""安全公告服务的单元测试：用假客户端和假发送器，不联网。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from robobo.services.security import (
    DLA_FEED_URL,
    DLA_LIST_URL,
    DSA_FEED_URL,
    DSA_LIST_URL,
    MESSAGE_HEADER,
    SecurityAdvisory,
    SecurityNotifier,
    format_line,
    format_messages,
    normalize_date,
    parse_advisory_list,
    parse_rss_feed,
    tracker_url,
)
from robobo.services.seen_store import SeenStore
from robobo.text import SAFE_LIMIT

RSS_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" ?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel rdf:about="https://www.debian.org/security/dsa.rdf">
<title>Debian Security</title><link>x</link><dc:date>2026-11-19</dc:date>
<description>Debian Security Advisories</description>
<items><rdf:Seq><rdf:li rdf:resource="https://lists.debian.org/a"/></rdf:Seq></items>
</channel>
{items}
</rdf:RDF>
"""

DSA_ITEM = """<item rdf:about="https://lists.debian.org/a">
<title>DSA-6643-1 pam-mysql - security update</title>
<link>https://lists.debian.org/a</link>
<dc:date>2026-11-19</dc:date>
<description>Andrey discovered a problem. It is bad.&lt;p&gt;
&lt;a href="x"&gt;tracker&lt;/a&gt;</description>
</item>"""

DLA_ITEM = """<item rdf:about="https://lists.debian.org/b">
<title>DLA-4920-1 electrum - regression update</title>
<link>https://lists.debian.org/b</link>
<dc:date>2026-11-18</dc:date>
<description>&lt;a href="x"&gt;tracker&lt;/a&gt;</description>
</item>"""

RSS_BOTH = RSS_TEMPLATE.format(items=DSA_ITEM + DLA_ITEM)
RSS_DSA = RSS_TEMPLATE.format(items=DSA_ITEM)

LIST_TEXT = """[19 Nov 2026] DSA-6643-1 pam-mysql - security update
\t{CVE-2026-124749}
\t[trixie] - pam-mysql 0.8.2-2.1+deb13u1
[19 Nov 2026] DSA-6642-1 nginx - security update
\t{CVE-2026-113425 CVE-2026-118899}
\t[trixie] - nginx 1.26.3-3+deb13u11
[18 Nov 2026] DSA-6637-2 needrestart - regression update
\t[trixie] - needrestart 3.11-1.1~deb13u2
[18 Apr 2018] DSA-4205-1 jessie end-of-life
"""


class FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200, headers: dict | None = None) -> None:
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    @property
    def is_error(self) -> bool:
        return self.status_code >= 400


class FakeClient:
    def __init__(self, routes: dict[str, FakeResponse]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, dict | None]] = []

    async def get(self, url: str, headers: dict | None = None) -> FakeResponse:
        self.calls.append((url, headers))
        return self.routes.get(url, FakeResponse("not found", 404))


class FakeSender:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[str] = []

    async def __call__(self, text: str) -> None:
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append(text)


def make_notifier(
    tmp_path: Path,
    sender: FakeSender | None = None,
    routes: dict[str, FakeResponse] | None = None,
    *,
    store: SeenStore | None = None,
) -> tuple[SecurityNotifier, FakeClient, SeenStore]:
    state_file = str(tmp_path / "state" / "security.json")
    store = store if store is not None else SeenStore.load(state_file)
    store.path = state_file
    client = FakeClient(routes or {})
    notifier = SecurityNotifier(
        client,
        store=store,
        sender=sender or FakeSender(),
        interval=300,
    )
    return notifier, client, store


def make_advisory(**kwargs) -> SecurityAdvisory:
    defaults = {
        "id": "DSA-6643-1",
        "package": "pam-mysql",
        "title": "security update",
        "date": "2026-11-19",
        "source": "DSA",
    }
    defaults.update(kwargs)
    return SecurityAdvisory(**defaults)


# --- 解析 -------------------------------------------------------------------


def test_normalize_date() -> None:
    assert normalize_date("[19 Nov 2026]") == "2026-11-19"
    assert normalize_date("2026-11-19") == "2026-11-19"
    assert normalize_date("Thu, 19 Nov 2026 20:18:29 +0000") == "2026-11-19"
    assert normalize_date("garbage") == "garbage"


def test_parse_rss_feed() -> None:
    items = parse_rss_feed(RSS_BOTH)

    assert [item.id for item in items] == ["DSA-6643-1", "DLA-4920-1"]
    first = items[0]
    assert first.package == "pam-mysql"
    assert first.title == "security update"
    assert first.date == "2026-11-19"
    assert first.source == "DSA"
    assert items[1].title == "regression update"


def test_parse_rss_feed_skips_unparseable_titles() -> None:
    xml = RSS_BOTH.replace("DSA-6643-1 pam-mysql - security update", "not an advisory")
    assert [item.id for item in parse_rss_feed(xml)] == ["DLA-4920-1"]


def test_parse_advisory_list() -> None:
    items = parse_advisory_list(LIST_TEXT)

    assert [item.id for item in items] == [
        "DSA-6643-1",
        "DSA-6642-1",
        "DSA-6637-2",
        "DSA-4205-1",
    ]
    first = items[0]
    assert first.package == "pam-mysql"
    assert first.title == "security update"
    assert first.date == "2026-11-19"
    assert items[2].title == "regression update"
    # 旧格式（无 " - " 分隔）不能崩
    assert items[3].package == "jessie"
    assert items[3].date == "2018-04-18"


def test_parse_advisory_list_without_revision() -> None:
    items = parse_advisory_list("[12 Nov 2006] DSA-1209 trac - several\n")

    assert [item.id for item in items] == ["DSA-1209"]
    assert items[0].date == "2006-11-12"


# --- 格式化 -----------------------------------------------------------------


def test_format_line() -> None:
    adv = make_advisory()
    assert format_line(adv) == (
        '<a href="https://security-tracker.debian.org/tracker/DSA-6643-1">DSA-6643-1</a>'
        " · pam-mysql · security update"
    )


def test_format_line_escapes_html() -> None:
    line = format_line(make_advisory(package="<script>", title="<b>bad</b>"))

    assert "<script>" not in line
    assert "&lt;script&gt;" in line
    assert "<b>bad</b>" not in line


def test_format_line_without_type() -> None:
    line = format_line(make_advisory(title=""))
    assert line.endswith(" · pam-mysql")


def test_format_messages_merges_into_one() -> None:
    items = [
        make_advisory(id="DSA-6643-1", package="pam-mysql", date="2026-11-19"),
        make_advisory(id="DLA-4920-1", package="electrum", title="regression update"),
    ]

    messages = format_messages(items)

    assert len(messages) == 1
    lines = messages[0].split("\n")
    assert lines[0] == MESSAGE_HEADER
    assert len(lines) == 3
    assert "DSA-6643-1" in lines[1]
    assert "security update" in lines[1]
    assert "regression update" in lines[2]


def test_format_messages_orders_newest_first() -> None:
    items = [
        make_advisory(id="DSA-0001-1", package="old", date="2026-11-18"),
        make_advisory(id="DSA-0002-1", package="new", date="2026-11-19"),
    ]

    lines = format_messages(items)[0].split("\n")

    assert "DSA-0002-1" in lines[1]
    assert "DSA-0001-1" in lines[2]


def test_format_messages_chunks_long_batches() -> None:
    items = [
        make_advisory(id=f"DSA-{i:04d}-1", package="p" * 80, date="2026-11-19")
        for i in range(200)
    ]

    messages = format_messages(items)

    assert len(messages) > 1
    assert all(message.startswith(MESSAGE_HEADER) for message in messages)
    assert all(len(message) <= SAFE_LIMIT for message in messages)
    joined = "\n".join(messages)
    for item in items:
        assert item.id in joined


def test_format_messages_empty() -> None:
    assert format_messages([]) == []


# --- 状态 -------------------------------------------------------------------


def test_seen_store_roundtrip(tmp_path: Path) -> None:
    path = str(tmp_path / "state" / "security.json")
    store = SeenStore.load(path)
    store.mark("DSA-1-1", "2026-11-19")
    store.set_etag("feed:DSA", '"abc"')
    store.save()

    loaded = SeenStore.load(path)
    assert loaded.has("DSA-1-1")
    assert loaded.get_etag("feed:DSA") == '"abc"'
    assert loaded.seen["DSA-1-1"] == "2026-11-19"
    assert Path(f"{path}.bak").exists()


def test_seen_store_missing_file_is_empty(tmp_path: Path) -> None:
    store = SeenStore.load(str(tmp_path / "nope.json"))
    assert store.empty
    assert not store.has("DSA-1-1")


def test_seen_store_falls_back_to_backup(tmp_path: Path) -> None:
    path = tmp_path / "security.json"
    path.write_text("{ not json", encoding="utf-8")
    Path(f"{path}.bak").write_text(
        '{"version":1,"seen":{"DSA-9-9":"2026-01-01"}}', encoding="utf-8"
    )

    store = SeenStore.load(str(path))

    assert store.has("DSA-9-9")


# --- Notifier ---------------------------------------------------------------


def test_reconcile_baselines_without_notifying(tmp_path: Path) -> None:
    async def run() -> None:
        routes = {
            DSA_LIST_URL: FakeResponse(LIST_TEXT),
            DLA_LIST_URL: FakeResponse(
                "[19 Nov 2026] DLA-4920-1 electrum - security update\n"
            ),
        }
        sender = FakeSender()
        notifier, _client, store = make_notifier(tmp_path, sender, routes)

        sent = await notifier.reconcile()

        assert sent == 0
        assert sender.sent == []
        assert store.has("DSA-6643-1")
        assert store.has("DLA-4920-1")

    asyncio.run(run())


def test_reconcile_backfills_unseen(tmp_path: Path) -> None:
    async def run() -> None:
        store = SeenStore(path="x", seen={"DSA-6643-1": "2026-11-19"})
        routes = {DSA_LIST_URL: FakeResponse(LIST_TEXT), DLA_LIST_URL: FakeResponse("")}
        sender = FakeSender()
        notifier, _client, store = make_notifier(tmp_path, sender, routes, store=store)

        sent = await notifier.reconcile()

        assert sent == 3  # DSA-6642-1、DSA-6637-2、DSA-4205-1
        assert len(sender.sent) == 1
        assert sender.sent[0].startswith(MESSAGE_HEADER)
        assert "DSA-6642-1" in sender.sent[0]
        assert "DSA-6643-1" not in sender.sent[0]

    asyncio.run(run())


def test_poll_sends_merged_message(tmp_path: Path) -> None:
    async def run() -> None:
        routes = {
            DSA_FEED_URL: FakeResponse(RSS_DSA),
            DLA_FEED_URL: FakeResponse(RSS_TEMPLATE.format(items="")),
        }
        sender = FakeSender()
        notifier, _client, store = make_notifier(tmp_path, sender, routes)

        count = await notifier.poll_once()

        assert count == 1
        message = sender.sent[0]
        assert message.split("\n")[0] == MESSAGE_HEADER
        assert "DSA-6643-1" in message
        assert "pam-mysql" in message
        assert "security update" in message
        assert tracker_url("DSA-6643-1") in message
        assert store.has("DSA-6643-1")

    asyncio.run(run())


def test_poll_dedupes_seen_items(tmp_path: Path) -> None:
    async def run() -> None:
        store = SeenStore(path="x", seen={"DSA-6643-1": "2026-11-19"})
        routes = {
            DSA_FEED_URL: FakeResponse(RSS_DSA),
            DLA_FEED_URL: FakeResponse(RSS_TEMPLATE.format(items="")),
        }
        sender = FakeSender()
        notifier, _client, _store = make_notifier(tmp_path, sender, routes, store=store)

        assert await notifier.poll_once() == 0
        assert sender.sent == []

    asyncio.run(run())


def test_poll_uses_etag_and_handles_304(tmp_path: Path) -> None:
    async def run() -> None:
        routes = {
            DSA_FEED_URL: FakeResponse(RSS_DSA, headers={"etag": '"v1"'}),
            DLA_FEED_URL: FakeResponse(RSS_TEMPLATE.format(items="")),
        }
        sender = FakeSender()
        notifier, client, store = make_notifier(tmp_path, sender, routes)

        await notifier.poll_once()
        assert store.get_etag("feed:DSA") == '"v1"'

        client.routes[DSA_FEED_URL] = FakeResponse("", 304, headers={"etag": '"v1"'})
        assert await notifier.poll_once() == 0
        dsa_calls = [headers for url, headers in client.calls if url == DSA_FEED_URL]
        assert dsa_calls[-1] == {"If-None-Match": '"v1"'}

    asyncio.run(run())


def test_poll_send_failure_keeps_items_unseen(tmp_path: Path) -> None:
    async def run() -> None:
        routes = {
            DSA_FEED_URL: FakeResponse(RSS_DSA),
            DLA_FEED_URL: FakeResponse(RSS_TEMPLATE.format(items="")),
        }
        sender = FakeSender(fail=True)
        notifier, _client, store = make_notifier(tmp_path, sender, routes)

        assert await notifier.poll_once() == 0
        assert not store.has("DSA-6643-1")

        sender.fail = False
        assert await notifier.poll_once() == 1
        assert store.has("DSA-6643-1")
        assert len(sender.sent) == 1

    asyncio.run(run())


def test_poll_skips_malformed_feed(tmp_path: Path) -> None:
    async def run() -> None:
        routes = {
            DSA_FEED_URL: FakeResponse("<not xml"),
            DLA_FEED_URL: FakeResponse(RSS_TEMPLATE.format(items=DLA_ITEM)),
        }
        sender = FakeSender()
        notifier, _client, _store = make_notifier(tmp_path, sender, routes)

        count = await notifier.poll_once()

        assert count == 1
        assert "DLA-4920-1" in sender.sent[0]

    asyncio.run(run())


def test_start_and_stop(tmp_path: Path) -> None:
    async def run() -> None:
        notifier, _client, _store = make_notifier(tmp_path)
        notifier.start()
        assert notifier.task is not None
        await notifier.stop()
        assert notifier.task is None

    asyncio.run(run())
