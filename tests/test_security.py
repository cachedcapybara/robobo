"""安全公告服务的单元测试：用假客户端和假发送器，不联网。"""

from __future__ import annotations

import asyncio
from pathlib import Path

from robobo.services.security import (
    DLA_FEED_URL,
    DLA_LIST_URL,
    DSA_FEED_URL,
    DSA_LIST_URL,
    SecurityAdvisory,
    SecurityNotifier,
    advisory_url,
    extract_summary,
    format_advisory,
    format_digest,
    normalize_date,
    parse_advisory_list,
    parse_rss_feed,
    parse_tracker_page,
    tracker_url,
)
from robobo.services.seen_store import SeenStore

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
<title>DLA-4920-1 electrum - security update</title>
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

TRACKER_HTML = """<html><body><table>
<tr><td><b>Name</b></td><td>DSA-6643-1</td></tr>
<tr><td><b>References</b></td>
<td><a href="/tracker/CVE-2026-124749">CVE-2026-124749</a>,
<a href="/tracker/CVE-2026-113425">CVE-2026-113425</a></td></tr>
</table>
<table><tr><th>Source Package</th><th>Release</th><th>Version</th><th>Status</th></tr>
<tr><td><a href="/tracker/source-package/pam-mysql">pam-mysql</a></td><td>trixie</td>
<td><span class="red">0.8.2-2.1</span></td><td><span class="red">vulnerable</span></td></tr>
<tr><td></td><td>trixie (security)</td><td>0.8.2-2.1+deb13u1</td><td>fixed</td></tr>
</table>
<table><tr><th>Package</th><th>Type</th><th>Release</th><th>Fixed Version</th>
<th>Urgency</th><th>Origin</th><th>Debian Bugs</th></tr>
<tr><td>pam-mysql</td><td>source</td><td>trixie</td><td>0.8.2-2.1+deb13u1</td>
<td></td><td></td><td></td></tr>
</table></body></html>
"""

TRACKER_STATUS_ONLY_HTML = """<html><body>
<table><tr><th>Source Package</th><th>Release</th><th>Version</th><th>Status</th></tr>
<tr><td>pam-mysql</td><td>trixie</td><td>0.8.2-2.1</td><td>vulnerable</td></tr>
<tr><td></td><td>trixie (security)</td><td>0.8.2-2.1+deb13u1</td><td>fixed</td></tr>
</table></body></html>
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
    digest_threshold: int = 5,
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
        digest_threshold=digest_threshold,
    )
    return notifier, client, store


# --- 解析 -------------------------------------------------------------------


def test_normalize_date() -> None:
    assert normalize_date("[19 Nov 2026]") == "2026-11-19"
    assert normalize_date("2026-11-19") == "2026-11-19"
    assert normalize_date("Thu, 19 Nov 2026 20:18:29 +0000") == "2026-11-19"
    assert normalize_date("garbage") == "garbage"


def test_extract_summary_strips_tags_and_takes_first_sentence() -> None:
    summary = extract_summary(
        "Andrey discovered a problem. It is bad.&lt;p&gt;&lt;a href=\"x\"&gt;tracker&lt;/a&gt;"
    )
    assert summary == "Andrey discovered a problem."


def test_extract_summary_truncates_long_sentence() -> None:
    summary = extract_summary("A" * 200 + ". More.")
    assert len(summary) <= 121
    assert summary.endswith("…")


def test_extract_summary_empty() -> None:
    assert extract_summary("") == ""
    assert extract_summary("&lt;a href=\"x\"&gt;tracker&lt;/a&gt;") == ""


def test_parse_rss_feed() -> None:
    items = parse_rss_feed(RSS_BOTH)

    assert [item.id for item in items] == ["DSA-6643-1", "DLA-4920-1"]
    first = items[0]
    assert first.package == "pam-mysql"
    assert first.title == "security update"
    assert first.date == "2026-11-19"
    assert first.source == "DSA"
    assert first.summary == "Andrey discovered a problem."
    assert items[1].summary == ""


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
    assert first.date == "2026-11-19"
    assert first.cves == ("CVE-2026-124749",)
    assert first.suites == (("trixie", "0.8.2-2.1+deb13u1"),)
    assert items[1].cves == ("CVE-2026-113425", "CVE-2026-118899")
    assert items[2].cves == ()
    # 旧格式（无 " - " 分隔）不能崩
    assert items[3].package == "jessie"
    assert items[3].date == "2018-04-18"


def test_parse_advisory_list_without_revision() -> None:
    items = parse_advisory_list("[12 Nov 2006] DSA-1209 trac - several\n")

    assert [item.id for item in items] == ["DSA-1209"]
    assert items[0].date == "2006-11-12"


def test_parse_tracker_page() -> None:
    cves, suites = parse_tracker_page(TRACKER_HTML)

    assert cves == ("CVE-2026-124749", "CVE-2026-113425")
    assert suites == (("trixie", "0.8.2-2.1+deb13u1"),)


def test_parse_tracker_page_fallback_to_status_table() -> None:
    cves, suites = parse_tracker_page(TRACKER_STATUS_ONLY_HTML)

    assert cves == ()
    assert suites == (("trixie", "0.8.2-2.1+deb13u1"),)


# --- 格式化 -----------------------------------------------------------------


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


def test_advisory_url() -> None:
    assert (
        advisory_url("DSA-6643-1", "2026-11-19")
        == "https://www.debian.org/security/2026/dsa-6643-1"
    )
    assert (
        advisory_url("DLA-4920-1", "2026-11-19")
        == "https://www.debian.org/lts/security/2026/dla-4920-1"
    )
    assert advisory_url("DSA-6643-1", "") == ""


def test_format_advisory_full() -> None:
    adv = make_advisory(
        cves=("CVE-2026-124749",),
        suites=(("trixie", "0.8.2-2.1+deb13u1"),),
        summary="Andrey discovered a problem.",
    )
    lines = format_advisory(adv).split("\n")

    assert lines[0] == (
        '🛡️ <a href="https://security-tracker.debian.org/tracker/DSA-6643-1">DSA-6643-1</a>'
        " · pam-mysql"
    )
    assert lines[1] == "📅 2026-11-19  📦 trixie 0.8.2-2.1+deb13u1"
    assert lines[2] == "🐞 CVE-2026-124749"
    assert lines[3] == "📝 Andrey discovered a problem."
    assert lines[4] == (
        '🔗 <a href="https://www.debian.org/security/2026/dsa-6643-1">公告全文</a>'
    )


def test_format_advisory_folds_long_cve_list() -> None:
    adv = make_advisory(cves=("CVE-1", "CVE-2", "CVE-3", "CVE-4"))
    lines = format_advisory(adv).split("\n")

    assert "🐞 CVE-1, CVE-2, CVE-3 … 共 4 个" in lines
    assert "全部 CVE" in lines[-1]
    assert tracker_url("DSA-6643-1") in lines[-1]


def test_format_advisory_multi_suite() -> None:
    adv = make_advisory(
        suites=(("bookworm", "1"), ("trixie", "2"), ("bullseye", "3"), ("sid", "4"))
    )
    lines = format_advisory(adv).split("\n")

    assert lines[1] == "📅 2026-11-19  📦 bookworm 1"
    assert lines[2] == "📦 trixie 2"
    assert lines[3] == "📦 bullseye 3"
    assert lines[4] == "… 共 4 个版本"


def test_format_advisory_escapes_html() -> None:
    adv = make_advisory(package="<script>", summary="<b>bad</b>")
    text = format_advisory(adv)

    assert "<script>" not in text
    assert "&lt;script&gt;" in text
    assert "<b>bad</b>" not in text


def test_format_advisory_minimal() -> None:
    lines = format_advisory(make_advisory()).split("\n")

    assert len(lines) == 3
    assert lines[1] == "📅 2026-11-19"
    assert lines[2].startswith("🔗 ")


def test_format_digest() -> None:
    items = [
        make_advisory(id="DSA-6643-1", package="pam-mysql", date="2026-11-19"),
        make_advisory(id="DSA-6642-1", package="nginx", date="2026-11-19"),
        make_advisory(id="DSA-6637-2", package="needrestart", date="2026-11-18"),
    ]
    lines = format_digest(items).split("\n")

    assert lines[0] == "📋 安全公告补发 · 2026-11-18 ~ 2026-11-19 · 共 3 条"
    assert len(lines) == 4
    assert "DSA-6643-1" in lines[1]
    assert "DSA-6637-2" in lines[3]


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


def test_reconcile_backfills_unseen_as_digest(tmp_path: Path) -> None:
    async def run() -> None:
        store = SeenStore(path="x", seen={"DSA-6643-1": "2026-11-19"})
        routes = {DSA_LIST_URL: FakeResponse(LIST_TEXT), DLA_LIST_URL: FakeResponse("")}
        sender = FakeSender()
        notifier, _client, store = make_notifier(tmp_path, sender, routes, store=store)

        sent = await notifier.reconcile()

        assert sent == 3  # DSA-6642-1、DSA-6637-2、DSA-4205-1
        assert len(sender.sent) == 1
        assert "安全公告补发" in sender.sent[0]
        assert "DSA-6642-1" in sender.sent[0]
        assert "DSA-6643-1" not in sender.sent[0]

    asyncio.run(run())


def test_poll_sends_rich_card_with_tracker_data(tmp_path: Path) -> None:
    async def run() -> None:
        routes = {
            DSA_FEED_URL: FakeResponse(RSS_DSA),
            DLA_FEED_URL: FakeResponse(RSS_TEMPLATE.format(items="")),
            tracker_url("DSA-6643-1"): FakeResponse(TRACKER_HTML),
        }
        sender = FakeSender()
        notifier, _client, store = make_notifier(tmp_path, sender, routes)

        sent = await notifier.poll_once()

        assert sent == 1
        card = sender.sent[0]
        assert "DSA-6643-1" in card
        assert "CVE-2026-124749" in card
        assert "trixie 0.8.2-2.1+deb13u1" in card
        assert "Andrey discovered a problem." in card
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
            tracker_url("DSA-6643-1"): FakeResponse(TRACKER_HTML),
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


def test_poll_send_failure_keeps_item_unseen(tmp_path: Path) -> None:
    async def run() -> None:
        routes = {
            DSA_FEED_URL: FakeResponse(RSS_DSA),
            DLA_FEED_URL: FakeResponse(RSS_TEMPLATE.format(items="")),
            tracker_url("DSA-6643-1"): FakeResponse(TRACKER_HTML),
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


def test_poll_uses_digest_above_threshold(tmp_path: Path) -> None:
    async def run() -> None:
        routes = {
            DSA_FEED_URL: FakeResponse(RSS_BOTH),
            DLA_FEED_URL: FakeResponse(RSS_TEMPLATE.format(items="")),
            tracker_url("DSA-6643-1"): FakeResponse(TRACKER_HTML),
        }
        sender = FakeSender()
        notifier, _client, store = make_notifier(
            tmp_path, sender, routes, digest_threshold=1
        )

        sent = await notifier.poll_once()

        assert sent == 2  # DSA-6643-1 与 DLA-4920-1
        assert len(sender.sent) == 1
        assert "安全公告补发" in sender.sent[0]
        assert store.has("DSA-6643-1")
        assert store.has("DLA-4920-1")

    asyncio.run(run())


def test_poll_skips_malformed_feed(tmp_path: Path) -> None:
    async def run() -> None:
        routes = {
            DSA_FEED_URL: FakeResponse("<not xml"),
            DLA_FEED_URL: FakeResponse(RSS_TEMPLATE.format(items=DLA_ITEM)),
        }
        sender = FakeSender()
        notifier, _client, _store = make_notifier(tmp_path, sender, routes)

        sent = await notifier.poll_once()

        assert sent == 1
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
