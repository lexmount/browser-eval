"""Public report URL shared by the article export and standalone packager."""
import json
import os
from pathlib import Path

HUB = (os.environ.get('WEBFETCH_REPORT_PUBLIC_URL') or
       'https://artifact-hub.dev.lexmount.net/api/preview/bWkRP157EtF6').rstrip('/')


def require_publication_site(receipt: dict, profile_path: Path) -> str:
    """Refuse to publish the current report through another site's receipt."""
    profile = json.loads(profile_path.read_text())
    expected = profile.get('artifact_hub_site')
    if not isinstance(expected, str) or not expected:
        raise ValueError('Current report profile is missing artifact_hub_site')
    if receipt.get('slug') != expected:
        raise ValueError(
            f'Publication receipt targets {receipt.get("slug")!r}; expected {expected!r}'
        )
    return expected
