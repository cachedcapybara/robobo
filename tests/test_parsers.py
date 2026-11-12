"""解析逻辑的单元测试：只喂 HTML 字符串，不联网。"""

from __future__ import annotations

from robobo.services.bts import parse_bug_page
from robobo.services.tracker import format_package, parse_package_page

TRACKER_HTML = """
<html><body>
  <h1>hello</h1>
  <div class="panel panel-default">
    <div class="panel-heading"><h4>versions</h4></div>
    <div class="panel-body">
      <ul>
        <li><b>stable</b>: <a href="/pkg/hello">2.10-3</a></li>
        <li><b>testing</b>: <a href="/pkg/hello">2.10-3</a></li>
        <li><b>unstable</b>: 2.10-4</li>
      </ul>
    </div>
  </div>
</body></html>
"""


def test_parse_tracker_versions() -> None:
    info = parse_package_page(TRACKER_HTML, fallback_name="hello")

    assert info.name == "hello"
    assert [(v.suite, v.version) for v in info.versions] == [
        ("stable", "2.10-3"),
        ("testing", "2.10-3"),
        ("unstable", "2.10-4"),
    ]


def test_parse_tracker_without_versions_panel() -> None:
    info = parse_package_page("<html><body><h1>gone</h1></body></html>", "gone")

    assert info.name == "gone"
    assert info.versions == ()


def test_parse_tracker_falls_back_to_requested_name() -> None:
    info = parse_package_page("<html><body></body></html>", "libc6")

    assert info.name == "libc6"


def test_format_package_escapes_html() -> None:
    info = parse_package_page(
        "<html><body><h1>&lt;script&gt;alert(1)&lt;/script&gt;</h1></body></html>",
        "evil",
    )
    text = format_package(info)

    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_format_package_mentions_structure_change() -> None:
    text = format_package(parse_package_page("<html></html>", "foo"))

    assert "页面结构可能变了" in text


def test_parse_bug_page_strips_number_prefix() -> None:
    info = parse_bug_page(
        "<html><body><h1>#861234. hello: buffer overflow</h1></body></html>",
        bug_id="861234",
    )

    assert info.bug_id == "861234"
    assert info.title == "hello: buffer overflow"


def test_parse_bug_page_without_dot() -> None:
    info = parse_bug_page("<html><body><h1>#123 hello: crash</h1></body></html>", bug_id="123")

    assert info.title == "hello: crash"


def test_parse_bug_page_falls_back_to_title_tag() -> None:
    info = parse_bug_page(
        "<html><head><title>Debian Bug report logs - weird bug</title></head><body></body></html>",
        bug_id="42",
    )

    assert info.title == "weird bug"


def test_parse_bug_page_without_any_title() -> None:
    info = parse_bug_page("<html><body></body></html>", bug_id="7")

    assert info.title == "Bug #7"
