"""A new user selects a real HTTP adapter and obtains inspectable local evidence."""
import functools
import json
import subprocess
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from harness.reporting.links import check
from harness.web_fetch.metrics import task_passed

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("complete", [True, False])
@pytest.mark.parametrize("method_name", ["http", "moli"])
def test_local_quickstart_runs_and_rebuilds_without_provider_calls(tmp_path, complete, method_name):
    site = tmp_path / "site"
    site.mkdir()
    page = (ROOT / "examples/web_fetch/site/index.html").read_text()
    if not complete:
        page = page[:page.index("  <p>Local examples")]+"</main></html>"
    (site / "index.html").write_text(page)
    handler = functools.partial(
        SimpleHTTPRequestHandler, directory=str(site)
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    dataset = json.loads((ROOT / "examples/web_fetch/dataset.json").read_text())
    for case in dataset["cases"]:
        case["url"] = f"http://127.0.0.1:{server.server_port}/"
    definition = tmp_path / "dataset.json"
    definition.write_text(json.dumps(dataset))
    # Configuration keys are caller labels, not version or implementation selectors.
    config = tmp_path / "providers.json"
    config.write_text(json.dumps({"providers": {method_name: {"type": "http"}}}))
    if method_name == "http":
        config = ROOT / "configs/providers.quickstart.json"
    run = tmp_path / "evidence"
    command = [sys.executable, "-m", "harness.web_fetch"]
    selection = ["--dataset", str(definition), "--provider-config",
                 str(config), "--providers", method_name]
    try:
        calls = [
            (["validate", *selection], 0),
            (["run", *selection, "--output", str(run), "--delay", "0",
              "--fail-on-failure"], 0 if complete else 2),
        ]
        for arguments, expected_code in calls:
            result = subprocess.run(command + arguments, capture_output=True, text=True, timeout=30)
            assert result.returncode == expected_code, result.stdout + result.stderr
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
    records = [json.loads(line) for line in (run / "results.jsonl").read_text().splitlines()]
    assert len(records) == len(dataset["cases"])
    assert all(row["ok"] and row["provider"] == method_name for row in records)
    assert all(task_passed(row["ok"], row["metrics"], "body") == complete for row in records)
    original = (run / "results.jsonl").read_bytes()
    result = subprocess.run(command + ["report", str(run)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (run / "results.jsonl").read_bytes() == original
    assert (run / "report/index.html").exists()
    for path in [run / "report/index.html", run / "report/analysis/index.html",
                 run / "report/providers" / f"{method_name}.html"]:
        assert "Moli 1.1.5" not in path.read_text()
    check(run / "report")
