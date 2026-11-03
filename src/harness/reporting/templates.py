"""Discover maintained report owners and pinned examples before building."""
import json

from harness.distribution import repository_root
from harness.env_file import local_setting

ROOT = repository_root()


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
    if not (result.get("example_version") if result.get("example_env") else result.get("example")):
        raise ValueError("Report template needs a version-pinned example")
    return {"track": track, **result}


def discover_template(track, *, root=ROOT):
    """Add a private example link for a local reader; report manifests never carry it."""
    template = resolve_template(track, root=root)
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
