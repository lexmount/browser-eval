"""Discover maintained report owners and pinned examples before building."""

import json
from copy import deepcopy

from harness.distribution import repository_root

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
    registry = json.loads((root / "configs/report-templates.json").read_text())
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
    if not result.get("example"):
        raise ValueError("Report template needs a version-pinned example")
    if "metric_contract" in result:
        raise ValueError("A report track cannot replace the shared required metric contract")
    return {"track": track, **result, "metric_contract": metric_contract()}


def template_identity(track):
    """Record the maintained owner without borrowing an example's report inputs."""
    template = resolve_template(track)
    return {key: template[key] for key in ("track", "renderer", "entrypoint")}
