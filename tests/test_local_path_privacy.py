"""Pin each public surface's redaction; expected text is the pre-consolidation output."""
import re
from pathlib import Path

import pytest

from harness.browser_use.publication import sanitize_text
from harness.reporting import privacy
from harness.trajectory_replay.public_webarena.publication import public_projection
from harness.web_fetch.runner import _public_text
from harness.web_fetch.wcxb_scoring import public_error
from harness.web_fetch.webmainbench_assembly import public_value
from harness.web_search.comparison import _preview

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = 'open /Users/kim/app/log.txt:12 then /tmp/run/a "/opt/tool/bin" https://example.com/home/page'
MATRIX = {
    "TRAJECTORY_REPLAY": 'open [local path removed] then [local path removed] "[local path removed]" '
                         "https://example.com/home/page",
    "BROWSER_USE": 'open <LOCAL_PATH> then <LOCAL_PATH> "<LOCAL_PATH>" https://example.com<LOCAL_PATH>',
    "PUBLIC_WEBARENA": 'open /[local-user]/app/log.txt:12 then /tmp/run/a "/opt/tool/bin" '
                       "https://example.com/[local-user]",
    "LEXBENCH": 'open <local path> then <local path> "/opt/tool/bin" https://example.com<local path>',
    "WEB_SEARCH_PREVIEW": 'open [source path] then /tmp/run/a "/opt/tool/bin" https://example.com[source path]',
    "WEBMAINBENCH": 'open /redacted-home/app/log.txt:12 then /redacted-temp "/opt/tool/bin" '
                    "https://example.com/redacted-home",
    "WCXB_ERROR": 'open [local path]:12 then /tmp/run/a "/opt/tool/bin" https://example.com[local path]',
    "WEB_FETCH_DIAGNOSTIC": 'open [local-path] then [local-path] "[local-path]" https://example.com/home/page',
}


@pytest.mark.parametrize(("name", "expected"), MATRIX.items())
def test_surface_output_is_unchanged(name, expected):
    assert getattr(privacy, name).redact(SAMPLE) == expected


def test_surfaces_keep_their_disagreements():
    # Merging any two rules would change published bytes, so this control fails
    # if a later cleanup quietly routes two surfaces through one rule.
    assert len(set(MATRIX.values())) == len(MATRIX)


def test_public_entry_points_apply_their_surface():
    assert sanitize_text("at /Users/kim/x") == "at <LOCAL_PATH>"
    assert public_projection({"a": ["/home/kim/x"]}) == {"a": ["/[local-user]/x"]}
    assert public_value({"a": ["/home/kim/x", "/private/tmp/y"]}) == {"a": ["/redacted-home/x", "/redacted-temp"]}
    assert public_error("/private/var/x: no") == "[local path]: no"
    assert public_error(None) is None
    assert _public_text(r"read /etc/hosts and C:\Users\kim\x") == "read [local-path] and [local-path]"
    assert _preview(r"File C:\Users\kim\a.py and /home/k/b.py", 320) == "File [source path] and [source path]"


def test_only_trajectory_replay_keeps_matching_web_url_paths():
    url = "https://example.com/tmp/page"
    assert privacy.TRAJECTORY_REPLAY.redact(url) == url
    assert not privacy.TRAJECTORY_REPLAY.found(url)
    assert privacy.TRAJECTORY_REPLAY.found(url + " /tmp/page")
    assert privacy.TRAJECTORY_REPLAY.redact(url + " /tmp/page") == url + " [local path removed]"
    assert privacy.BROWSER_USE.redact(url) == "https://example.com<LOCAL_PATH>"


def test_web_fetch_byte_views_are_counted():
    raw = b'{"a": "/Users/kim/app/x", "b": "/root/y", "c": "x/home/kim/z", "d": "/tmp/w"}'
    segment, count = privacy.WEB_FETCH_USER_SEGMENT.subn(raw)
    assert count == 2
    assert segment == b'{"a": "/[REDACTED_USER]/app/x", "b": "/[REDACTED_USER]/y", "c": "x/home/kim/z", "d": "/tmp/w"}'
    retained, count = privacy.WEB_FETCH_RETAINED_DIAGNOSTIC.subn(raw)
    assert count == 2
    assert retained == b'{"a": "[HOST_PATH_OMITTED]", "b": "[HOST_PATH_OMITTED]", "c": "x/home/kim/z", "d": "/tmp/w"}'
    assert not privacy.WEB_FETCH_USER_SEGMENT.found(b"/tmp/w")


@pytest.mark.parametrize(
    ("text", "hit"),
    [("see /Users/kim/x", True), ("(/private/tmp/x)", True), ("a\n/home/k/y", True),
     ("x/home/k/y", False), ("/tmp/plain", False), ("/private/etc/x", False)],
)
def test_anomaly_review_detector(text, hit):
    assert bool(privacy.ANOMALY_REVIEW_LOCAL_PATH.search(text)) is hit


def test_release_check_fragments():
    assert re.search(privacy.BROWSER_USE_CHECK_SOURCE, "/opt/x")
    assert not re.search(privacy.BROWSER_USE_CHECK_SOURCE, "/root/x")
    assert re.search(privacy.LEXBENCH_CHECK_SOURCE, "/work/")
    assert privacy.FIX_LOG_LOCAL_ROOT.search("/root/")


# A root directly followed by a regex construct, or a root inside an alternation.
_ROOTS = "Users|home|root|tmp|private|var|opt"
PATH_RULE = re.compile(rf"\b(?:{_ROOTS})/(?:\[|\||\\|\.[*+]|\()|\b(?:{_ROOTS})\||\|(?:{_ROOTS})\b(?!\[)")


def test_only_the_privacy_module_defines_path_rules():
    # Plain substring guards such as `"/Users/" in html` are release checks, not rules.
    owner = ROOT / "src/harness/reporting/privacy.py"
    sources = [*(ROOT / "src/harness").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]
    found = [str(path.relative_to(ROOT)) for path in sources
             if path != owner and PATH_RULE.search(path.read_text("utf-8"))]
    assert found == []


@pytest.mark.parametrize(
    "rule",
    [
        r're.sub(r"/Users/[^/]+", "x", value)',
        r're.sub(r"/home/[^/]+", "x", value)',
        r're.compile(r"/tmp/[^\s]+")',
        r're.compile(r"/root/[^\s]+")',
        r're.compile(r"/private/tmp/\S+")',
        r're.compile(r"/home/\w+")',
        r're.compile(r"/home/.+")',
        r're.compile(r"/(?:var/)?tmp/[^\s]+")',
        r're.compile(r"/(?:Users|home)/")',
        r're.compile(r"/(?:home|Users)/")',
        r're.compile(r"/(?:work|Users)/")',
        r're.compile(r"/(?:work|home)/")',
        r're.compile(rb"/(?:root|home)/")',
        r're.compile(rb"/(?:work|root)/")',
        r're.compile(r"/(?:tmp|work)/")',
        r're.compile(r"(?:/Users/|/home/)\S+")',
    ],
)
def test_path_rule_guard_recognizes_a_copied_rule(rule):
    assert PATH_RULE.search(rule)


@pytest.mark.parametrize(
    "line",
    [
        'assert "/Users/" not in html',
        'path = Path("/tmp/plain")',
        'pattern = re.compile(r"chrome|firefox")',
        'pattern = re.compile(r"users|accounts")',
        r're.compile(r"x[-_]?auth[-_]?token|private[-_]?token|token")',
    ],
)
def test_path_rule_guard_ignores_unrelated_text(line):
    assert not PATH_RULE.search(line)
