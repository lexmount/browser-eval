"""Download official WebArena human traces into an external evidence directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.cookiejar import CookieJar
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


def _valid_trace(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            return sum(name.endswith("trace.trace") for name in archive.namelist()) == 1
    except zipfile.BadZipFile:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _DriveConfirmationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.action: str | None = None
        self.fields: dict[str, str] = {}
        self._in_download_form = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form" and values.get("id") == "download-form":
            self._in_download_form = True
            self.action = values.get("action")
        elif tag == "input" and self._in_download_form:
            name = values.get("name")
            value = values.get("value")
            if name and value is not None:
                self.fields[name] = value

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._in_download_form = False


def _stream_download(url: str, partial: Path) -> None:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 browser-eval WebArena trace fetcher"},
    )
    with opener.open(request, timeout=180) as response:
        content_type = str(response.headers.get("Content-Type") or "").lower()
        if "text/html" not in content_type:
            with partial.open("wb") as target:
                shutil.copyfileobj(response, target, length=1024 * 1024)
            return
        content = response.read(64 * 1024).decode("utf-8", errors="replace")
        parser = _DriveConfirmationParser()
        parser.feed(content)
        if not parser.action or not parser.fields:
            raise ValueError("Google Drive returned HTML without a download confirmation form")
        confirmation_url = parser.action + "?" + urllib.parse.urlencode(parser.fields)
    confirmation = urllib.request.Request(
        confirmation_url,
        headers={"User-Agent": "Mozilla/5.0 browser-eval WebArena trace fetcher"},
    )
    with opener.open(confirmation, timeout=180) as response, partial.open("wb") as target:
        shutil.copyfileobj(response, target, length=1024 * 1024)


def _download(row: dict[str, Any], output: Path, *, max_attempts: int = 4) -> dict[str, Any]:
    destination = output / str(row["file_name"])
    if _valid_trace(destination):
        return {
            "task_id": row["task_id"],
            "file_name": destination.name,
            "bytes": destination.stat().st_size,
            "sha256": _sha256(destination),
            "status": "existing",
        }
    partial = destination.with_suffix(destination.suffix + ".part")
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        partial.unlink(missing_ok=True)
        try:
            _stream_download(str(row["download_url"]), partial)
            if not _valid_trace(partial):
                raise ValueError("response was not a Playwright trace ZIP")
            break
        except Exception as exc:
            last_error = exc
            partial.unlink(missing_ok=True)
            if attempt == max_attempts:
                raise RuntimeError(
                    f"failed to download {destination.name} after {max_attempts} attempts"
                ) from last_error
            time.sleep(2 ** (attempt - 1))
    partial.replace(destination)
    return {
        "task_id": row["task_id"],
        "file_name": destination.name,
        "bytes": destination.stat().st_size,
        "sha256": _sha256(destination),
        "status": "downloaded",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("evaluation/browser-use-public/webarena-human-traces.json"),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    configured_home = os.environ.get("BROWSER_EVAL_DATA_HOME")
    if args.output is None and not configured_home:
        parser.error("--output or BROWSER_EVAL_DATA_HOME is required")
    output = args.output or Path(str(configured_home)) / "evidence/current/webarena-human-traces"
    if args.jobs < 1 or args.jobs > 8:
        parser.error("--jobs must be between 1 and 8")
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    rows = catalog.get("traces") or []
    if catalog.get("count") != 179 or len(rows) != 179:
        raise ValueError("catalog must contain exactly 179 WebArena traces")
    output.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        pending = {executor.submit(_download, row, output): row for row in rows}
        for future in as_completed(pending):
            row = pending[future]
            result = future.result()
            results.append(result)
            print(
                json.dumps(
                    {
                        "completed": len(results),
                        "total": len(rows),
                        "task_id": row["task_id"],
                        "status": result["status"],
                    }
                ),
                flush=True,
            )
    results.sort(key=lambda item: int(item["task_id"]))
    manifest = {
        "schema": "lex_webarena_trace_downloads/1",
        "catalog_sha256": hashlib.sha256(args.catalog.read_bytes()).hexdigest(),
        "count": len(results),
        "complete": len(results) == 179,
        "files": results,
    }
    (output / "downloads.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
