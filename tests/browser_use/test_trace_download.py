from __future__ import annotations

import io
import urllib.parse
import zipfile

from scripts.fetch_webarena_traces import _DriveConfirmationParser, _stream_download


class _Response(io.BytesIO):
    def __init__(self, body: bytes, content_type: str) -> None:
        super().__init__(body)
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


class _Opener:
    def __init__(self, responses) -> None:
        self.responses = iter(responses)
        self.urls = []

    def open(self, request, timeout):
        self.urls.append(request.full_url)
        return next(self.responses)


def _trace_zip() -> bytes:
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("trace.trace", "{}\n")
    return target.getvalue()


def test_drive_confirmation_parser_reads_only_download_form():
    parser = _DriveConfirmationParser()
    parser.feed(
        '<form id="other"><input name="ignored" value="1"></form>'
        '<form id="download-form" action="https://drive.usercontent.google.com/download">'
        '<input name="id" value="file-id"><input name="confirm" value="t"></form>'
    )

    assert parser.action == "https://drive.usercontent.google.com/download"
    assert parser.fields == {"id": "file-id", "confirm": "t"}


def test_stream_download_follows_large_file_confirmation(monkeypatch, tmp_path):
    confirmation = (
        b'<form id="download-form" action="https://drive.usercontent.google.com/download">'
        b'<input name="id" value="file-id"><input name="confirm" value="t">'
        b'<input name="uuid" value="attempt-id"></form>'
    )
    opener = _Opener(
        [
            _Response(confirmation, "text/html; charset=utf-8"),
            _Response(_trace_zip(), "application/octet-stream"),
        ]
    )
    monkeypatch.setattr("urllib.request.build_opener", lambda *_args: opener)
    output = tmp_path / "trace.zip.part"

    _stream_download("https://drive.google.com/uc?id=file-id", output)

    assert zipfile.is_zipfile(output)
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(opener.urls[1]).query)
    assert query == {"id": ["file-id"], "confirm": ["t"], "uuid": ["attempt-id"]}
