"""Public report URL and exact release-target validation."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE = "https://artifact-hub.app.lexmount.com"


def publication_document(document: dict) -> dict:
    """Read flat or nested publication fields without changing their authority."""
    if 'artifact_hub_base_url' in document:
        return document
    publication = document.get('publication')
    if not isinstance(publication, dict):
        return document
    return {**document,
            'artifact_hub_base_url': publication.get('base_url'),
            'artifact_hub_site': publication.get('site_slug'),
            'artifact_hub_expected_version': publication.get('version_id')}


def validate_service_base(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("Publication service requires an explicit HTTPS base URL")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "\\" in value
        or any(ord(char) <= 32 or ord(char) == 127 for char in value)
    ):
        raise ValueError("Publication service requires an explicit HTTPS base URL")
    _ = parsed.port  # Reject malformed ports before loading a host-scoped credential.
    return value.rstrip("/")


def publication_base(
    profile_path: Path, requested_base: str | None = None, *, document: dict | None = None
) -> str:
    """Select the configured service without a deployment-specific default."""
    profile = document if document is not None else json.loads(profile_path.read_bytes())
    base = validate_service_base(profile.get("artifact_hub_base_url"))
    if requested_base is not None and validate_service_base(requested_base) != base:
        raise ValueError("Requested publication service differs from the bound profile")
    return base


def current_publication_target(
    profile_path=ROOT / "evaluation/current-report.json", *, require_bound=True
):
    profile = json.loads(Path(profile_path).read_text())
    base = profile.get("artifact_hub_base_url", DEFAULT_BASE)
    slug = profile.get("artifact_hub_site")
    if not isinstance(base, str) or not base.startswith("https://"):
        raise ValueError("Current report profile has an invalid Artifact Hub target")
    if not isinstance(slug, str) or not slug:
        if require_bound:
            raise ValueError("Current report profile has no bound Artifact Hub site")
        return base.rstrip("/"), None
    return base.rstrip("/"), slug


def require_publication_version(
    profile_path: Path, requested_version: str | None = None, *, document: dict | None = None
) -> str:
    """Require the profile-bound current site version for a compare-and-swap update."""
    profile = document if document is not None else json.loads(Path(profile_path).read_text())
    expected = profile.get("artifact_hub_expected_version")
    if not isinstance(expected, str) or not expected.startswith("ver_"):
        raise ValueError("Current report profile is missing a valid artifact_hub_expected_version")
    if requested_version is not None and requested_version != expected:
        raise ValueError(
            f"Requested publication version {requested_version!r}; expected {expected!r}"
        )
    return expected


def public_report_url(profile_path: Path | None = None) -> str:
    """Resolve links at render time; an installed offline example has no deployment."""
    override = os.environ.get("WEBFETCH_REPORT_PUBLIC_URL")
    if override:
        return override.rstrip("/")
    selected = profile_path or ROOT / "evaluation/current-report.json"
    if profile_path is None and not selected.is_file():
        return ""
    base, slug = current_publication_target(selected, require_bound=False)
    return f"{base}/api/preview/{slug}" if slug else ""


def require_publication_site(
    receipt: dict, profile_path: Path, base_url: str | None = None, *, document: dict | None = None
) -> str:
    """Refuse to publish the current report through another site's receipt."""
    profile = document if document is not None else json.loads(profile_path.read_text())
    expected = profile.get("artifact_hub_site")
    if not isinstance(expected, str) or not expected:
        raise ValueError("Current report profile is missing artifact_hub_site")
    if receipt.get("slug") != expected:
        raise ValueError(
            f"Publication receipt targets {receipt.get('slug')!r}; expected {expected!r}"
        )
    expected_base = profile.get("artifact_hub_base_url", DEFAULT_BASE).rstrip("/")
    receipt_base = (receipt.get("base_url") or "").rstrip("/")
    requested_base = (base_url or "").rstrip("/")
    if receipt_base and requested_base and receipt_base != requested_base:
        raise ValueError(
            f"Publication receipt targets {receipt_base!r}; requested {requested_base!r}"
        )
    actual_base = receipt_base or requested_base
    if actual_base != expected_base:
        raise ValueError(f"Publication receipt targets {actual_base!r}; expected {expected_base!r}")
    return expected
