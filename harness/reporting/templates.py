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
    for key in ("renderer", "entrypoint", "profile", "tests", *(
        key for key in result if key.endswith('_profile')
    )):
        path = root / result[key]
        if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("Report template has a missing or unsafe " + key)
    if not result.get("example"):
        profile = json.loads((root / result["profile"]).read_text())
        publication = profile.get("publication", {})
        host = publication.get("host") or publication.get("base_url")
        slug = publication.get("slug") or publication.get("site_slug")
        version = publication.get("version_id") or publication.get("expected_version")
        if not all((host, slug, version)):
            raise ValueError("Report template needs a version-pinned example")
        result["example"] = f"{host}/s/{slug}?version={version}"
    return {"track": track, **result}


def template_identity(track):
    """Record the maintained owner without borrowing an example's report inputs."""
    template = resolve_template(track)
    return {key: template[key] for key in ("track", "renderer", "entrypoint")}
