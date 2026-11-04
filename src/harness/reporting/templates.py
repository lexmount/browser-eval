"""Discover maintained report owners and pinned examples before building."""

import json
from copy import deepcopy

from harness.distribution import repository_root
from harness.env_file import local_setting

ROOT = repository_root()

# Every registered track inherits these dimensions, including future tracks.
METRIC_CONTRACT = {
    "required": {
        "latency": {
            "unit": "seconds",
            "summaries": ["mean", "p50", "p95"],
            "population": "every participant; declare sample and missing counts",
            "collection": "measure failures as well as successes; retain retry cost separately",
        },
        "cost": {
            "scope": "every participant",
            "pricing": "Source/date/unit/commitment; use USD/1,000 requests where supported.",
            "usage": "Native receipts; distinguish estimates, actual charges and free allocations.",
        },
    },
    "contextual": {
        "cpu": "Decide before calls; measure local or controlled compute, else explain omission.",
        "memory": "Decide before calls; measure local or controlled memory, else explain omission.",
    },
    "unavailable": "Never substitute zero; retain the affected count and an evidenced reason.",
}


def metric_contract():
    """Return runtime requirements without requiring checkout-only authoring files."""
    return deepcopy(METRIC_CONTRACT)


def resolve_template(track, *, root=ROOT):
    registry = json.loads((root / "configs/report-templates.json").read_text(encoding="utf-8"))
    if registry.get("schema_version") != 1:
        raise ValueError("Unsupported report template registry")
    if track not in registry["tracks"]:
        raise ValueError(
            "Unregistered report track; add its maintained renderer, "
            "runnable example and release checks first"
        )
    result = dict(registry["tracks"][track])
    for key in ("renderer", "entrypoint", "tests"):
        path = root / result[key]
        exists = path.is_file() or (key == "tests" and path.is_dir())
        if not exists or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Report template has a missing or unsafe " + key)
    if not (result.get("example_version") if result.get("example_env") else result.get("example")):
        raise ValueError("Report template needs a version-pinned example")
    if "metric_contract" in result:
        raise ValueError("A report track cannot replace the shared required metric contract")
    return {"track": track, **result}


def discover_template(track, *, root=ROOT):
    """Add a private example link for a local reader; report manifests never carry it."""
    template = {**resolve_template(track, root=root), "metric_contract": metric_contract()}
    name = template.get("example_env")
    if not name:
        return template
    # An optional link must not stop discovery, so an unusable file means it is unset.
    try:
        example = local_setting(name, root / ".env", follow_links=True).strip()
    except ValueError:
        example = ""
    if example and f"version={template['example_version']}" not in example:
        raise ValueError(f"{name} does not pin the registered example version")
    return {**template, "example": example or None}


def template_identity(track):
    """Record the maintained owner without borrowing an example's report inputs."""
    template = resolve_template(track)
    return {key: template[key] for key in ("track", "renderer", "entrypoint")}


def check_template_identity(report, track):
    """Bind the renderer: a moved entry point leaves reports built before the move valid."""
    if not report.get("profile_sha256"):
        return
    template, maintained = report.get("template") or {}, template_identity(track)
    if any(template.get(key) != maintained[key] for key in ("track", "renderer")):
        raise ValueError(f"report bypasses the maintained {track} template")
