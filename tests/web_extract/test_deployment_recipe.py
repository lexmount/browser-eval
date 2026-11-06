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
        assert "@sha256:" in component["image"]
    assert len(model["volumes"]) == 3
    firecrawl = services["firecrawl-api"]
    assert firecrawl["entrypoint"] == ["node", "/opt/browser-eval/firecrawl_backend.mjs"]
    mount = next(v for v in firecrawl["volumes"] if v["target"].endswith("firecrawl_backend.mjs"))
    assert mount["read_only"]
    assert Path(mount["source"]).is_file()
    assert api["depends_on"]["firecrawl-api"]["condition"] == "service_healthy"


@pytest.mark.parametrize("missing", REQUIRED)
def test_recipe_rejects_missing_account_or_private_service_credentials(missing):
    result = resolve(missing=missing)
    assert result.returncode != 0
    assert missing in result.stderr
