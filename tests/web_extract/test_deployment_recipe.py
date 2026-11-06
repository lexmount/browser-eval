"""An operator can resolve the complete stack without exposing private backends."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy/web-extract/compose.yaml"
REQUIRED = (
    "EXTRACTION_API_KEY", "EXTRACTION_MODEL_API_KEY", "SNAPSHOT_ORIGINS",
    "WEB_EXTRACT_POSTGRES_PASSWORD", "WEB_EXTRACT_QUEUE_PASSWORD",
)


def resolve(*, missing=None):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is required for deployment recipe validation")
    env = {**os.environ, **{name: "synthetic" for name in REQUIRED}, "WEB_EXTRACT_PORT": "18086"}
    env["SNAPSHOT_ORIGINS"] = "https://snapshot.example"
    if missing:
        env.pop(missing)
    return subprocess.run(
        ["docker", "compose", "--env-file", os.devnull, "-f", str(COMPOSE),
         "config", "--format", "json"],
        env=env, capture_output=True, text=True, timeout=30, check=False,
    )


def test_recipe_resolves_one_authenticated_frontend_and_five_private_dependencies():
    result = resolve()
    assert result.returncode == 0, result.stderr
    model = json.loads(result.stdout)
    services = model["services"]
    assert len(services) == 6
    api = services["native-extraction"]
    assert api["ports"][0]["host_ip"] == "127.0.0.1"
    assert str(api["ports"][0]["published"]) == "18086"
    assert api["environment"]["EXTRACTION_API_KEY"] == "synthetic"
    assert api["environment"]["FIRECRAWL_BACKEND_URL"] == "http://firecrawl-api:3002"
    for name, component in services.items():
        if name == "native-extraction":
            continue
        assert not component.get("ports"), name
        if name not in ("firecrawl-api", "firecrawl-browser"):
            assert "@sha256:" in component["image"]
    queue = services["firecrawl-rabbitmq"]
    assert "platform" not in queue
    assert queue["hostname"] == "firecrawl-rabbitmq"
    assert queue["healthcheck"]["test"][-1] == "check_running"
    assert len(model["volumes"]) == 3
    firecrawl = services["firecrawl-api"]
    build = firecrawl["build"]
    recipe = (Path(build["context"]) / build["dockerfile"]).read_text(encoding="utf-8")
    assert "@sha256:" in recipe
    assert "firecrawl_backend.mjs" in recipe
    browser = services["firecrawl-browser"]
    browser_build = browser["build"]
    browser_recipe = (Path(browser_build["context"]) / browser_build["dockerfile"]).read_text()
    assert "FROM ghcr.io/firecrawl/playwright-service@sha256:" in browser_recipe
    assert "FROM ghcr.io/firecrawl/firecrawl@sha256:" in recipe
    assert "firecrawl_backend.mjs" in browser_recipe
    assert not browser_build.get("args")
    assert browser["environment"]["FIRECRAWL_SERVICE_KIND"] == "browser"
    assert firecrawl["environment"]["FIRECRAWL_BROWSER_IDENTITY_URL"].endswith(":3020/identity")
    assert firecrawl["depends_on"]["firecrawl-browser"]["condition"] == "service_healthy"
    # A remote Docker daemon does not share the checkout's host filesystem.
    assert not any(v["type"] == "bind" for s in services.values() for v in s.get("volumes", []))
    assert api["depends_on"]["firecrawl-api"]["condition"] == "service_healthy"


@pytest.mark.parametrize("missing", REQUIRED)
def test_recipe_rejects_missing_account_or_private_service_credentials(missing):
    result = resolve(missing=missing)
    assert result.returncode != 0
    assert missing in result.stderr
