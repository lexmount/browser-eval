"""Exact release-target validation against the bound publication profile."""

import json
from pathlib import Path
from urllib.parse import urlsplit


def publication_document(document: dict) -> dict:
    """Read flat or nested publication fields without changing their authority.

    ``artifact_hub_*`` are the stored keys of hash-bound profiles, not a current
    product name: renaming them would change the profile hashes bound by certificates.
    """
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


def require_publication_version(
    profile_path: Path, requested_version: str | None = None, *, document: dict | None = None
) -> str:
    """Require the profile-bound current site version for a compare-and-swap update."""
    profile = document if document is not None else json.loads(Path(profile_path).read_text(encoding="utf-8"))
    expected = profile.get("artifact_hub_expected_version")
    if not isinstance(expected, str) or not expected.startswith("ver_"):
        raise ValueError("Current report profile is missing a valid artifact_hub_expected_version")
    if requested_version is not None and requested_version != expected:
        raise ValueError(
            f"Requested publication version {requested_version!r}; expected {expected!r}"
        )
    return expected


def require_publication_site(
    receipt: dict, profile_path: Path, base_url: str | None = None, *, document: dict | None = None
) -> str:
    """Refuse to publish the current report through another site's receipt."""
    profile = document if document is not None else json.loads(profile_path.read_text(encoding="utf-8"))
    expected = profile.get("artifact_hub_site")
    if not isinstance(expected, str) or not expected:
        raise ValueError("Current report profile is missing artifact_hub_site")
    if receipt.get("slug") != expected:
        raise ValueError(
            f"Publication receipt targets {receipt.get('slug')!r}; expected {expected!r}"
        )
    expected_base = validate_service_base(profile.get("artifact_hub_base_url"))
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
