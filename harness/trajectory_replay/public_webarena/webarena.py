"""Import official WebArena human Playwright traces into the common protocol."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .suite import TRAJECTORY_SCHEMA

HUMAN_TRAJECTORY_FOLDER_ID = "1NrN_sawtYK2V_uHnmmS8ugmGIKUAsPgt"
HUMAN_TRAJECTORY_FOLDER_URL = "https://drive.google.com/drive/folders/" + HUMAN_TRAJECTORY_FOLDER_ID
_FILE_RE = re.compile(r"/(?P<id>[-\w]{25,})/view(?:\?|$)")
_TRACE_NAME_RE = re.compile(r"^(?P<task_id>\d+)\.trace\.zip$")
_ROLE_RE = re.compile(
    r'^internal:role=(?P<role>[\w-]+)(?:\[name="(?P<name>(?:[^"\\]|\\.)*)"(?P<flag>[is])\])?$'
)
_QUOTED_INTERNAL_RE = re.compile(
    r'^internal:(?P<kind>label|text)="(?P<value>(?:[^"\\]|\\.)*)"(?P<flag>[is])$'
)


@dataclass(frozen=True)
class DriveTraceFile:
    task_id: int
    name: str
    file_id: str

    @property
    def download_url(self) -> str:
        return f"https://drive.google.com/uc?id={self.file_id}&export=download"


class _FolderParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._href: str | None = None
        self._text: list[str] = []
        self.files: list[DriveTraceFile] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or self._href is None:
            return
        name = "".join(self._text).strip()
        file_match = _FILE_RE.search(self._href)
        name_match = _TRACE_NAME_RE.fullmatch(name)
        if file_match and name_match:
            self.files.append(
                DriveTraceFile(
                    task_id=int(name_match.group("task_id")),
                    name=name,
                    file_id=file_match.group("id"),
                )
            )
        self._href = None
        self._text = []


def parse_drive_folder_html(content: str, *, expected_count: int = 179) -> list[DriveTraceFile]:
    """Parse the official embedded folder view and fail closed on drift."""
    parser = _FolderParser()
    parser.feed(content)
    by_task = {item.task_id: item for item in parser.files}
    if len(by_task) != len(parser.files):
        raise ValueError("WebArena trace folder contains duplicate task IDs")
    if len(by_task) != expected_count:
        raise ValueError(
            f"WebArena trace folder must contain {expected_count} unique traces; "
            f"found {len(by_task)}"
        )
    return [by_task[task_id] for task_id in sorted(by_task)]


def _decode_quoted(value: str) -> str:
    escaped_controls = "".join(
        json.dumps(character)[1:-1] if ord(character) < 0x20 else character for character in value
    )
    return json.loads(f'"{escaped_controls}"')


def normalize_playwright_selector(selector: str) -> dict[str, Any]:
    """Convert simple internal selectors; preserve compound Playwright selectors exactly."""
    role = _ROLE_RE.fullmatch(selector)
    if role:
        target: dict[str, Any] = {"by": "role", "role": role.group("role")}
        if role.group("name") is not None:
            target["name"] = _decode_quoted(role.group("name"))
            target["exact"] = role.group("flag") == "s"
        return target
    quoted = _QUOTED_INTERNAL_RE.fullmatch(selector)
    if quoted:
        return {
            "by": quoted.group("kind"),
            "value": _decode_quoted(quoted.group("value")),
            "exact": quoted.group("flag") == "s",
        }
    if "internal:" in selector or ">>" in selector:
        return {"by": "playwright", "value": selector}
    return {"by": "css", "value": selector}


def _snapshot_input_value(value: Any, call_id: str) -> str | None:
    if isinstance(value, dict):
        if value.get("__playwright_target__") == call_id:
            recorded = value.get("__playwright_value_")
            if isinstance(recorded, str):
                return recorded
        for child in value.values():
            found = _snapshot_input_value(child, call_id)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _snapshot_input_value(child, call_id)
            if found is not None:
                return found
    return None


def _snapshot_target_inputs(value: Any) -> list[tuple[str, str]]:
    """Return visible input values whose last Playwright target identifies a locator call."""
    found: list[tuple[str, str]] = []
    if isinstance(value, list):
        if len(value) >= 2 and isinstance(value[0], str) and isinstance(value[1], dict):
            tag = value[0].upper()
            attributes = value[1]
            input_type = str(attributes.get("type") or "text").lower()
            call_id = attributes.get("__playwright_target__")
            recorded = attributes.get("__playwright_value_")
            if (
                tag in {"INPUT", "TEXTAREA"}
                and input_type not in {"hidden", "password", "checkbox", "radio", "submit"}
                and isinstance(call_id, str)
                and isinstance(recorded, str)
                and recorded
            ):
                found.append((call_id, recorded))
        for child in value:
            found.extend(_snapshot_target_inputs(child))
    elif isinstance(value, dict):
        for child in value.values():
            found.extend(_snapshot_target_inputs(child))
    return found


def _convert_before(
    record: dict[str, Any], *, recorded_input_value: str | None = None
) -> dict[str, Any] | None:
    api_name = str(record.get("apiName") or "")
    params = record.get("params") or {}
    mapping = {
        "frame.click": "click",
        "frame.dblclick": "double_click",
        "frame.fill": "fill",
        "frame.type": "fill",
        "frame.selectOption": "select",
        "frame.check": "check",
        "frame.uncheck": "uncheck",
        "frame.hover": "hover",
        "frame.press": "press",
        "page.goto": "navigate",
        "frame.goto": "navigate",
    }
    action = mapping.get(api_name)
    if action is None:
        return None
    result: dict[str, Any] = {
        "action": action,
        "source": {"api_name": api_name, "call_id": record.get("callId")},
    }
    if action == "press" and params.get("key") in {"Control+v", "Meta+v"}:
        action = "fill"
        result["action"] = action
        if recorded_input_value is None:
            result["source"]["unresolved_external_input"] = "clipboard"
        else:
            result["text"] = recorded_input_value
            result["source"]["recovered_from_input_snapshot"] = True
    if action == "navigate":
        result["url"] = params.get("url")
    elif action == "press":
        result["key"] = params.get("key")
        if params.get("selector"):
            result["target"] = normalize_playwright_selector(str(params["selector"]))
    else:
        selector = params.get("selector")
        if not isinstance(selector, str) or not selector:
            raise ValueError(f"{api_name} omitted selector")
        result["target"] = normalize_playwright_selector(selector)
        if action == "fill":
            if not result["source"].get("recovered_from_input_snapshot") and not result[
                "source"
            ].get("unresolved_external_input"):
                result["text"] = str(params.get("value", params.get("text", "")))
        elif action == "select":
            values = params.get("values") or []
            if not values or any(not isinstance(selected, dict) for selected in values):
                raise ValueError("selectOption values must be non-empty objects")
            selected_values = [selected.get("value", selected.get("label")) for selected in values]
            if any(value is None for value in selected_values):
                raise ValueError("selectOption value is missing")
            normalized_values = [str(value) for value in selected_values]
            result["value"] = (
                normalized_values[0] if len(normalized_values) == 1 else normalized_values
            )
    return result


def import_playwright_trace(path: str | Path, *, strict: bool = True) -> dict[str, Any]:
    """Read one Playwright trace ZIP without extracting untrusted archive members."""
    trace_path = Path(path)
    with zipfile.ZipFile(trace_path) as archive:
        names = [name for name in archive.namelist() if name.endswith("trace.trace")]
        if len(names) != 1:
            raise ValueError(f"trace ZIP must contain exactly one trace.trace; found {len(names)}")
        raw_lines = archive.read(names[0]).decode("utf-8").splitlines()

    context: dict[str, Any] | None = None
    actions: list[dict[str, Any]] = []
    unmapped: list[dict[str, Any]] = []
    after_by_call: dict[str, dict[str, Any]] = {}
    input_value_by_call: dict[str, str] = {}
    input_snapshot_by_call: dict[str, dict[str, Any]] = {}
    snapshot_by_name: dict[str, dict[str, Any]] = {}
    before_records: list[dict[str, Any]] = []
    page_ids: list[str] = []
    page_url_by_id: dict[str, str] = {}
    for line in raw_lines:
        record = json.loads(line)
        if record.get("type") == "context-options":
            context = record
        elif (
            record.get("type") == "event"
            and record.get("class") == "Page"
            and record.get("method") == "__create__"
            and record.get("guid")
        ):
            page_ids.append(str(record["guid"]))
        elif record.get("type") == "before":
            before_records.append(record)
        elif record.get("type") == "after" and record.get("callId"):
            after_by_call[str(record["callId"])] = record
        elif record.get("type") == "frame-snapshot":
            snapshot = record.get("snapshot") or {}
            snapshot_page_id = str(snapshot.get("pageId") or "")
            snapshot_url = str(snapshot.get("frameUrl") or "")
            if (
                snapshot_page_id
                and snapshot_url.startswith(("http://", "https://"))
                and snapshot_page_id not in page_url_by_id
            ):
                page_url_by_id[snapshot_page_id] = snapshot_url
            call_id = str(snapshot.get("callId") or "")
            snapshot_name = str(snapshot.get("snapshotName") or "")
            if snapshot_name:
                snapshot_by_name[snapshot_name] = snapshot
            if call_id and snapshot_name.startswith("input@"):
                value = _snapshot_input_value(snapshot.get("html"), call_id)
                if value is not None:
                    input_value_by_call[call_id] = value
                    input_snapshot_by_call[call_id] = snapshot

    target_by_call = {
        str(record.get("callId")): normalize_playwright_selector(str(record["params"]["selector"]))
        for record in before_records
        if isinstance((record.get("params") or {}).get("selector"), str)
        and (record.get("params") or {}).get("selector")
    }
    recovered_input_states: set[tuple[str, str, str, str]] = set()
    for record in before_records:
        call_id = str(record.get("callId") or "")
        before_snapshot = snapshot_by_name.get(str(record.get("beforeSnapshot") or "")) or {}
        recorded_input_value = input_value_by_call.get(call_id)
        if recorded_input_value is None:
            recorded_input_value = _snapshot_input_value(before_snapshot.get("html"), call_id)
        converted = _convert_before(
            record,
            recorded_input_value=recorded_input_value,
        )
        if converted is None:
            unmapped.append(
                {
                    "api_name": record.get("apiName"),
                    "call_id": record.get("callId"),
                }
            )
            continue
        after = after_by_call.get(str(record.get("callId")))
        page_id = str(record.get("pageId") or "")
        if page_id:
            if page_id not in page_ids:
                page_ids.append(page_id)
            converted["tab_index"] = page_ids.index(page_id)
        snapshots = [before_snapshot, input_snapshot_by_call.get(call_id) or {}]
        recovered_this_action: set[tuple[str, str]] = set()
        for snapshot in snapshots:
            for target_call_id, snapshot_value in _snapshot_target_inputs(snapshot.get("html")):
                target = target_by_call.get(target_call_id)
                if target is None or (target_call_id, snapshot_value) in recovered_this_action:
                    continue
                recovered_this_action.add((target_call_id, snapshot_value))
                input_state = (
                    page_id,
                    str(snapshot.get("frameUrl") or ""),
                    json.dumps(target, sort_keys=True, ensure_ascii=False),
                    snapshot_value,
                )
                if input_state in recovered_input_states:
                    continue
                if target_call_id == call_id and converted["action"] == "fill":
                    recovered_input_states.add(input_state)
                    continue
                recovered = {
                    "action": "fill",
                    "target": target,
                    "text": snapshot_value,
                    "source": {
                        "api_name": "synthetic.fill",
                        "call_id": target_call_id,
                        "recovered_before_call_id": call_id,
                        "recorded_status": "pass",
                        "recovered_manual_input_from_snapshot": True,
                    },
                }
                if "tab_index" in converted:
                    recovered["tab_index"] = converted["tab_index"]
                actions.append(recovered)
                recovered_input_states.add(input_state)
        converted["source"]["recorded_status"] = (
            "fail" if after is None or after.get("error") else "pass"
        )
        actions.append(converted)

    if strict and unmapped:
        names = sorted({str(item["api_name"]) for item in unmapped})
        raise ValueError(f"unsupported Playwright APIs in trace: {names}")
    options = (context or {}).get("options") or {}
    return {
        "schema": TRAJECTORY_SCHEMA,
        "provenance": {
            "kind": "webarena_official_human_playwright_trace",
            "source_file": trace_path.name,
            "source_sha256": hashlib.sha256(trace_path.read_bytes()).hexdigest(),
        },
        "recorded_context": {
            "browser_name": (context or {}).get("browserName"),
            "viewport": options.get("viewport"),
            "device_scale_factor": options.get("deviceScaleFactor"),
            "tab_count": len(page_ids),
        },
        "recorded_tab_urls": [page_url_by_id.get(page_id) for page_id in page_ids],
        "actions": actions,
        "unmapped_calls": unmapped,
    }


def build_trace_catalog(
    files: list[DriveTraceFile],
    *,
    original_tasks: list[dict[str, Any]],
    verified_tasks: list[dict[str, Any]],
    original_commit: str,
    verified_commit: str,
) -> dict[str, Any]:
    """Join all official trajectory IDs to both benchmark task definitions."""
    original = {int(task["task_id"]): task for task in original_tasks}
    verified = {int(task["task_id"]): task for task in verified_tasks}
    rows: list[dict[str, Any]] = []
    for item in files:
        if item.task_id not in original or item.task_id not in verified:
            raise ValueError(f"task {item.task_id} is missing from an upstream dataset")
        old = original[item.task_id]
        current = verified[item.task_id]
        rows.append(
            {
                "task_id": item.task_id,
                "file_name": item.name,
                "google_drive_file_id": item.file_id,
                "download_url": item.download_url,
                "intent_template_id": current["intent_template_id"],
                "sites": current["sites"],
                "start_urls": current.get("start_urls") or [],
                "original_intent": old["intent"],
                "verified_intent": current["intent"],
                "verified_revision": current["revision"],
                "verified_evaluators": current["eval"],
            }
        )
    return {
        "schema": "lex_webarena_human_trace_catalog/1",
        "count": len(rows),
        "source": {
            "folder_url": HUMAN_TRAJECTORY_FOLDER_URL,
            "webarena_commit": original_commit,
            "webarena_verified_commit": verified_commit,
        },
        "scope": {
            "all_recorded_trajectories_included": True,
            "upstream_reported_human_success_rate": 0.7824,
            "recorded_does_not_imply_successful_gold_path": True,
        },
        "traces": rows,
    }


def task_id_from_trace_path(path: str | Path) -> int:
    match = _TRACE_NAME_RE.fullmatch(Path(path).name)
    if not match:
        raise ValueError(f"invalid WebArena trace name: {Path(path).name}")
    return int(match.group("task_id"))


def is_google_drive_download_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.hostname == "drive.google.com"


def validate_trace_catalog(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    rows = catalog.get("traces")
    count = catalog.get("count")
    if not isinstance(rows, list) or not rows or type(count) is not int or count != len(rows):
        raise ValueError("trace catalog count must match its nonempty declared population")
    ids, names = set(), set()
    for row in rows:
        if not isinstance(row, dict) or type(row.get("task_id")) is not int:
            raise ValueError("trace catalog requires integer task IDs")
        name = row.get("file_name")
        if (
            not isinstance(name, str)
            or task_id_from_trace_path(name) != row["task_id"]
            or Path(name).name != name
        ):
            raise ValueError("trace catalog requires task-matched trace basenames")
        if row["task_id"] in ids or name in names:
            raise ValueError("trace catalog contains duplicate tasks or filenames")
        if not is_google_drive_download_url(str(row.get("download_url") or "")):
            raise ValueError("trace catalog requires official Google Drive download URLs")
        ids.add(row["task_id"])
        names.add(name)
    return rows


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


def main_fetch_traces(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("evaluation/browser-use-public/webarena-human-traces.json"),
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args(argv)
    configured_home = os.environ.get("BROWSER_EVAL_DATA_HOME")
    if args.output is None and not configured_home:
        parser.error("--output or BROWSER_EVAL_DATA_HOME is required")
    output = args.output or Path(str(configured_home)) / "evidence/current/webarena-human-traces"
    if args.jobs < 1 or args.jobs > 8:
        parser.error("--jobs must be between 1 and 8")
    catalog_bytes = args.catalog.read_bytes()
    catalog = json.loads(catalog_bytes)
    rows = validate_trace_catalog(catalog)
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
    if args.catalog.read_bytes() != catalog_bytes:
        raise ValueError("trace catalog changed during download")
    manifest = {
        "schema": "lex_webarena_trace_downloads/1",
        "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
        "count": len(results),
        "complete": len(results) == len(rows),
        "files": results,
    }
    (output / "downloads.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def main_audit_traces(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--catalog",
        type=Path,
        default=Path("evaluation/browser-use-public/webarena-human-traces.json"),
    )
    parser.add_argument("--trace-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    from harness.web_fetch.safety import unlinked_path, write_new_file

    output = unlinked_path(args.output)
    if output.exists():
        raise ValueError("output already exists")
    catalog_path = unlinked_path(args.catalog)
    catalog_bytes = catalog_path.read_bytes()
    catalog = json.loads(catalog_bytes)
    rows = validate_trace_catalog(catalog)
    bindings = {}

    missing: list[str] = []
    tasks: list[dict] = []
    action_counts: Counter[str] = Counter()
    target_counts: Counter[str] = Counter()
    unmapped_counts: Counter[str] = Counter()
    recovered_input_counts: Counter[str] = Counter()
    for row in rows:
        path = unlinked_path(args.trace_dir / row["file_name"])
        if not path.is_file():
            missing.append(row["file_name"])
            continue
        bindings[path] = _sha256(path)
        trace = import_playwright_trace(path, strict=False)
        actions = trace["actions"]
        action_counts.update(str(action["action"]) for action in actions)
        target_counts.update(
            str((action.get("target") or {}).get("by"))
            for action in actions
            if action.get("target")
        )
        unmapped_counts.update(str(call["api_name"]) for call in trace["unmapped_calls"])
        recovered_manual_inputs = sum(
            bool(action["source"].get("recovered_manual_input_from_snapshot")) for action in actions
        )
        recovered_clipboard_inputs = sum(
            bool(action["source"].get("recovered_from_input_snapshot")) for action in actions
        )
        recovered_input_counts["manual_typing"] += recovered_manual_inputs
        recovered_input_counts["clipboard_paste"] += recovered_clipboard_inputs
        tasks.append(
            {
                "task_id": row["task_id"],
                "action_count": len(actions),
                "unmapped_calls": trace["unmapped_calls"],
                "recorded_failed_actions": sum(
                    action["source"].get("recorded_status") == "fail" for action in actions
                ),
                "unresolved_external_inputs": sum(
                    bool(action["source"].get("unresolved_external_input")) for action in actions
                ),
                "recovered_manual_inputs": recovered_manual_inputs,
                "recovered_clipboard_inputs": recovered_clipboard_inputs,
            }
        )
    payload = {
        "schema": "lex_webarena_trace_audit/1",
        "expected_count": len(rows),
        "downloaded_count": len(tasks),
        "complete": not missing,
        "missing": missing,
        "action_counts": dict(sorted(action_counts.items())),
        "target_counts": dict(sorted(target_counts.items())),
        "unmapped_api_counts": dict(sorted(unmapped_counts.items())),
        "recovered_input_counts": dict(sorted(recovered_input_counts.items())),
        "zero_action_task_ids": [row["task_id"] for row in tasks if row["action_count"] == 0],
        "tasks": tasks,
    }
    if catalog_path.read_bytes() != catalog_bytes or any(
        _sha256(path) != digest for path, digest in bindings.items()
    ):
        raise ValueError("trace audit inputs changed")
    payload["catalog_sha256"] = hashlib.sha256(catalog_bytes).hexdigest()
    payload["trace_sha256"] = {path.name: digest for path, digest in bindings.items()}
    write_new_file(output, (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode())
    if missing:
        raise RuntimeError(f"trace download is incomplete: {len(missing)} files missing")
    if unmapped_counts:
        raise RuntimeError(f"unsupported Playwright APIs remain: {dict(unmapped_counts)}")
