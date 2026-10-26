"""Credential redaction must preserve legitimate URL content and final-call identity."""

import json

import pytest

from harness.web_search.core import digest
from harness.web_search.report import _redact_url_userinfo, public_calls


@pytest.mark.parametrize(
    "url",
    [
        b"https://example.org?email=reader@example.net",
        b"https://example.org#reader@example.net",
        b"https://example.org/path/reader@example.net",
        b"https://example.org?next=/reader:secret@example.net",
        b"https://example.org:443?recipient=a@b.test",
        b"https://example.org/path\\nreader@example.net",
    ],
)
def test_at_sign_outside_authority_is_not_a_credential(url):
    raw = b'{"url":"' + url + b'", "text":"raw\\r\\nbytes"}\r\n'
    assert _redact_url_userinfo(raw) == raw


@pytest.mark.parametrize(
    "url,expected",
    [
        (
            b"https://reader:secret@example.org?email=a@b.test",
            b"https://REDACTED:REDACTED@example.org?email=a@b.test",
        ),
        (
            b"https://reader@example.org#user@fragment.test",
            b"https://REDACTED:REDACTED@example.org#user@fragment.test",
        ),
        (
            b"HTTPS://reader:secret@[::1]:443/path@name",
            b"HTTPS://REDACTED:REDACTED@[::1]:443/path@name",
        ),
        (
            b"https://reader%40mail.test:secret@example.org/path",
            b"https://REDACTED:REDACTED@example.org/path",
        ),
    ],
)
def test_only_authority_userinfo_changes_and_redaction_is_idempotent(url, expected):
    raw = b'{"url":"' + url + b'"}\r\n\xff'
    projected = _redact_url_userinfo(raw)
    assert projected == b'{"url":"' + expected + b'"}\r\n\xff'
    assert _redact_url_userinfo(projected) == projected


def test_public_projection_rejects_wrong_final_attempt_before_reading_files(tmp_path):
    binding = {"path": "missing-final.json", "sha256": "wrong"}
    rows = [
        {"method": "a", "evidence": binding, "attempts": [{"evidence": {"path": "different.json"}}]}
    ]
    with pytest.raises(ValueError, match="Final response evidence differs"):
        public_calls({"methods": [{"id": "a"}]}, rows, tmp_path, {})


def test_release_accepts_query_at_sign_without_rewriting_evidence(tmp_path):
    from harness.web_search.example import example
    from harness.web_search.report import check

    example(tmp_path)
    output = tmp_path / "report"
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    binding = next(x for x in manifest["bundle"] if x["path"].startswith("calls/"))
    path = output / binding["path"]
    payload = json.loads(path.read_text())
    payload["related_url"] = "https://example.org?recipient=reader@example.net"
    raw = json.dumps(payload).encode()
    path.write_bytes(raw)
    binding["sha256"] = digest(raw)
    manifest_path.write_text(json.dumps(manifest))
    check(output)
    assert path.read_bytes() == raw
