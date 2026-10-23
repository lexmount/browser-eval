"""Discover maintained report owners and pinned examples before building."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def resolve_template(track, *, root=ROOT):
    registry = json.loads((root / "configs/report-templates.json").read_text())
    if registry.get("schema_version") != 1:
        raise ValueError("Unsupported report template registry")
    if track not in registry["tracks"]:
        raise ValueError("Unregistered report track; add its maintained renderer, runnable example and release checks first")
    result = dict(registry["tracks"][track])
    for key in ("renderer", "entrypoint", "tests"):
        path = root / result[key]
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Report template has a missing or unsafe " + key)
    if not result.get("example"):
        raise ValueError("Report template needs a version-pinned example")
    return {"track": track, **result}


def template_identity(track):
    """Record the maintained owner without borrowing an example's report inputs."""
    template = resolve_template(track)
    return {key: template[key] for key in ("track", "renderer", "entrypoint")}
