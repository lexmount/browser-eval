from __future__ import annotations

import io
import urllib.parse
import zipfile

from harness.trajectory_replay.public_webarena.webarena import (
    _DriveConfirmationParser,
    _stream_download,
)


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


def test_declared_catalog_downloads_all_cached_traces_without_fixed_size(tmp_path, monkeypatch):
    import json

    from harness.trajectory_replay.public_webarena.webarena import main_fetch_traces

    rows = [
        {
            "task_id": task,
            "file_name": f"{task}.trace.zip",
            "download_url": f"https://drive.google.com/uc?id=synthetic-{task}",
        }
        for task in (1, 2)
    ]
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"count": len(rows), "traces": rows}))
    output = tmp_path / "traces"
    output.mkdir()
    for row in rows:
        (output / row["file_name"]).write_bytes(_trace_zip())
    monkeypatch.setattr(
        "urllib.request.build_opener",
        lambda *_: (_ for _ in ()).throw(AssertionError("cached traces must not trigger network")),
    )
    main_fetch_traces(["--catalog", str(catalog), "--output", str(output)])
    manifest = json.loads((output / "downloads.json").read_text())
    assert manifest["complete"] and manifest["count"] == 2
    assert {item["task_id"] for item in manifest["files"]} == {1, 2}
    assert all(item["status"] == "existing" for item in manifest["files"])


def test_invalid_catalog_rejected_before_creating_output(tmp_path):
    import json

    import pytest

    from harness.trajectory_replay.public_webarena.webarena import main_fetch_traces

    row = {
        "task_id": 1,
        "file_name": "../1.trace.zip",
        "download_url": "https://drive.google.com/uc?id=synthetic",
    }
    for rows, count in [
        ([row], 1),
        ([dict(row, file_name="1.trace.zip")] * 2, 2),
        ([dict(row, file_name="1.trace.zip")], 179),
    ]:
        catalog = tmp_path / "catalog.json"
        catalog.write_text(json.dumps({"count": count, "traces": rows}))
        output = tmp_path / "output"
        with pytest.raises(ValueError):
            main_fetch_traces(["--catalog", str(catalog), "--output", str(output)])
        assert not output.exists()


def test_catalog_drift_cannot_produce_download_manifest(tmp_path, monkeypatch):
    import json

    import pytest

    from harness.trajectory_replay.public_webarena import webarena

    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "count": 1,
                "traces": [
                    {
                        "task_id": 1,
                        "file_name": "1.trace.zip",
                        "download_url": "https://drive.google.com/uc?id=synthetic",
                    }
                ],
            }
        )
    )
    output = tmp_path / "traces"

    def download(row, destination):
        catalog.write_text("{}")
        return {"task_id": 1, "status": "downloaded"}

    monkeypatch.setattr(webarena, "_download", download)
    with pytest.raises(ValueError, match="catalog changed"):
        webarena.main_fetch_traces(["--catalog", str(catalog), "--output", str(output)])
    assert not (output / "downloads.json").exists()
