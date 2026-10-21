"""Local rendering, input identities and focused release-review receipts."""

from __future__ import annotations

import hashlib
import http.server
import json
import math
import os
import shutil
import tempfile
import threading
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit

from harness.web_fetch.safety import reject_evidence_symlinks
from lexbench_webfetch.paths import contained_path


def browser_executable() -> str:
    configured = os.environ.get("BROWSER_USE_VISUAL_BROWSER") or os.environ.get("CHROMIUM_BINARY")
    if configured:
        if not Path(configured).is_file():
            raise ValueError("Configured visual QA browser is missing: " + configured)
        return configured
    for candidate in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    raise ValueError("visual QA requires Chrome/Chromium; set BROWSER_USE_VISUAL_BROWSER")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_sha256(root: Path, *, framed_paths: bool) -> str:
    """Preserve each track's frozen tree-digest serialization."""
    reject_evidence_symlinks(root)
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix().encode()
        if framed_paths:
            digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(file_sha256(path)))
    return digest.hexdigest()


def checker_sources(source: Path) -> dict[str, str]:
    return {path.name: file_sha256(path) for path in (source, Path(__file__))}


def state_matrix(routes: Sequence[str], viewports: Sequence[tuple[int, int]]) -> set[tuple]:
    if not routes or len(routes) != len(set(routes)):
        raise ValueError("visual QA requires nonempty unique routes")
    return {(route, width, height, "light") for route in routes for width, height in viewports}


def validate_routes(report: Path, routes: Sequence[str]) -> None:
    state_matrix(routes, ((1, 1),))
    for route in routes:
        parsed = urlsplit(route)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise ValueError("visual routes must stay inside the report")
        target = contained_path(report, unquote(parsed.path), reject_symlinks=True)
        if not target.is_file():
            raise ValueError("visual QA route is missing: " + route)


def layout_passes(
    state: dict, *, empty_lists: Sequence[str], false_flags: Sequence[str] = ()
) -> bool:
    value = state.get("overflow")
    return (
        type(value) in (int, float)
        and math.isfinite(value)
        and -2 <= value <= 2
        and all(state.get(key) == [] for key in empty_lists)
        and all(state.get(key) is False for key in false_flags)
    )


def receipt_is_current(
    receipt: Path,
    *,
    schema: str,
    report_sha256: str,
    source: Path,
    routes: Sequence[str],
    viewports: Sequence[tuple[int, int]],
    state_passes: Callable[[dict], bool],
    browser: Callable[[], str] = browser_executable,
) -> bool:
    try:
        if receipt.is_symlink():
            return False
        value = json.loads(receipt.read_text())
        expected = state_matrix(routes, viewports)
        states = value.get("states")
        if (
            value.get("schema") != schema
            or value.get("report_tree_sha256") != report_sha256
            or value.get("checker_sources") != checker_sources(source)
            or value.get("failures") != []
            or not isinstance(states, list)
            or type(value.get("states_checked")) is not int
            or value["states_checked"] != len(expected)
            or type(value.get("route_classes")) is not int
            or value["route_classes"] != len(routes)
            or len(states) != len(expected)
        ):
            return False
        actual = set()
        for state in states:
            if not isinstance(state, dict) or not state_passes(state):
                return False
            if type(state.get("width")) is not int or type(state.get("height")) is not int:
                return False
            actual.add((state.get("route"), state["width"], state["height"], state.get("theme")))
        if actual != expected:
            return False
        executable = browser()
        return value.get("browser_executable") == executable and value.get(
            "browser_sha256"
        ) == file_sha256(Path(executable))
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        return False


def prepare_check(report: Path, receipt: Path) -> None:
    reject_evidence_symlinks(report)
    if receipt.is_symlink() or receipt.resolve().is_relative_to(report.resolve()):
        raise ValueError("visual receipt must be unlinked and outside the report")


def write_receipt(receipt: Path, value: dict) -> None:
    """Install a complete receipt atomically; failures retain the previous bytes."""
    if receipt.is_symlink():
        raise ValueError("visual receipt must not be a symlink")
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=receipt.parent, prefix="." + receipt.name, delete=False
    ) as stream:
        staging = Path(stream.name)
        try:
            stream.write((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            staging.unlink(missing_ok=True)
            raise
    try:
        if receipt.is_symlink():
            raise ValueError("visual receipt changed to a symlink")
        staging.replace(receipt)
    finally:
        staging.unlink(missing_ok=True)


def settle_theme(page) -> None:
    """Wait for finite CSS transitions before measuring a selected theme."""
    page.evaluate("""() => Promise.all(document.getAnimations()
        .filter(animation => animation.effect?.getTiming().iterations !== Infinity)
        .map(animation => animation.finished.catch(() => {})))""")


def local_page(browser, origin: str, **options):
    """Render frozen local bytes; optional remote enhancements use their fallback."""
    page = browser.new_page(**options)
    expected = urlsplit(origin)

    def dispatch(route):
        requested = urlsplit(route.request.url)
        if (requested.scheme, requested.netloc) == (expected.scheme, expected.netloc):
            route.continue_()
        else:
            route.abort()

    page.route("**/*", dispatch)
    return page


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return


@contextmanager
def local_report_server(report: Path) -> Iterator[str]:
    def handler(*args, **kwargs):
        return QuietHandler(*args, directory=str(report), **kwargs)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    try:
        thread.start()
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        if thread.is_alive():
            server.shutdown()
            thread.join(timeout=5)
        server.server_close()
