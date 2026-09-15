"""Public report URL and exact release-target validation."""
import json
import os
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
DEFAULT_BASE='https://artifact-hub.app.lexmount.com'


def current_publication_target(profile_path=ROOT/'evaluation/current-report.json'):
    profile=json.loads(Path(profile_path).read_text())
    base=profile.get('artifact_hub_base_url',DEFAULT_BASE)
    slug=profile.get('artifact_hub_site')
    if not isinstance(base,str) or not base.startswith('https://') or not isinstance(slug,str) or not slug:
        raise ValueError('Current report profile has an invalid Artifact Hub target')
    return base.rstrip('/'),slug


def require_publication_version(profile_path: Path, requested_version: str | None = None) -> str:
    """Return the profile-bound remote version, rejecting missing or conflicting input."""
    profile = json.loads(Path(profile_path).read_text())
    expected = profile.get('artifact_hub_expected_version')
    if not isinstance(expected, str) or not expected.startswith('ver_'):
        raise ValueError(
            'Current report profile is missing a valid artifact_hub_expected_version'
        )
    if requested_version is not None and requested_version != expected:
        raise ValueError(
            f'Requested publication version {requested_version!r}; expected {expected!r}'
        )
    return expected


_BASE,_SLUG=current_publication_target()
HUB = (os.environ.get('WEBFETCH_REPORT_PUBLIC_URL') or
       f'{_BASE}/api/preview/{_SLUG}').rstrip('/')


def require_publication_site(receipt: dict, profile_path: Path, base_url: str | None=None) -> str:
    """Refuse to publish the current report through another site's receipt."""
    profile = json.loads(profile_path.read_text())
    expected = profile.get('artifact_hub_site')
    if not isinstance(expected, str) or not expected:
        raise ValueError('Current report profile is missing artifact_hub_site')
    if receipt.get('slug') != expected:
        raise ValueError(
            f'Publication receipt targets {receipt.get("slug")!r}; expected {expected!r}'
        )
    expected_base=profile.get('artifact_hub_base_url',DEFAULT_BASE).rstrip('/')
    receipt_base=(receipt.get('base_url') or '').rstrip('/')
    requested_base=(base_url or '').rstrip('/')
    if receipt_base and requested_base and receipt_base != requested_base:
        raise ValueError(f'Publication receipt targets {receipt_base!r}; requested {requested_base!r}')
    actual_base=receipt_base or requested_base
    if actual_base != expected_base:
        raise ValueError(f'Publication receipt targets {actual_base!r}; expected {expected_base!r}')
    return expected
