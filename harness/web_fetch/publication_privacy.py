"""Project supplemental raw HTML for publication without changing scored evidence."""
import hashlib
import re
from pathlib import PurePosixPath


GOOGLE_KEY = re.compile(rb'AIzaSy[A-Za-z0-9_-]{20,}')
OTHER_SECRET = re.compile(
    rb'(?<![A-Za-z0-9_-])(?:ahp_[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|'
    rb'sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{20,}|'
    rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)'
)
AUTH_VALUE = re.compile(rb'(?i)authorization\s*:\s*(?:bearer|basic)\s+(?!<|\[)[A-Za-z0-9._~+/=-]{12,}')
PUBLIC_AUTH_EXAMPLE = re.compile(rb'(?i)(?:proxy-)?authorization\s*:\s*basic\s+aGVsbG86d29ybGQ=')
LOCAL_USER_PATH = re.compile(rb'(?<![A-Za-z0-9])/(?:Users|home)/[^/\s"\'<>]+/')


def auth_matches(data):
    """Ignore only the RFC's published hello:world Basic-auth example."""
    scrubbed = PUBLIC_AUTH_EXAMPLE.sub(b'Authorization: [PUBLIC_RFC_EXAMPLE]', data)
    return list(AUTH_VALUE.finditer(scrubbed))


def publication_risk(data):
    """Return a public-bundle risk class without exposing the matched value."""
    if GOOGLE_KEY.search(data) or OTHER_SECRET.search(data):
        return 'credential-shaped value'
    if auth_matches(data):
        return 'authorization header value'
    if LOCAL_USER_PATH.search(data):
        return 'machine-local user path'
    return None


def project_file(name, data):
    """Redact only auxiliary HTML. Any match elsewhere requires human review."""
    path = PurePosixPath(name)
    if 'private_artifacts' in path.parts:
        raise ValueError('Private evidence is not publishable: ' + name)
    if any(part.startswith('.') for part in path.parts):
        raise ValueError('Private/hidden file is not publishable: ' + name)
    if OTHER_SECRET.search(data):
        raise ValueError('Potential credential-shaped value in publication file: ' + name)
    google_matches = list(GOOGLE_KEY.finditer(data))
    authorization_matches = auth_matches(data)
    local_matches = list(LOCAL_USER_PATH.finditer(data))
    if not google_matches and not authorization_matches and not local_matches:
        return data, None
    projected = GOOGLE_KEY.sub(b'[REDACTED_GOOGLE_API_KEY]', data)
    projected = AUTH_VALUE.sub(b'Authorization: [REDACTED_EXAMPLE_CREDENTIAL]', projected)
    projected = LOCAL_USER_PATH.sub(b'/[REDACTED_USER]/', projected)
    categories = []
    kinds = []
    if google_matches or authorization_matches:
        categories.append('credential-shaped strings in public source or measured content')
        kinds.append('credential')
    if local_matches:
        categories.append('machine-local user paths')
        kinds.append('local_path')
    return projected, {
        'path': name,
        'kinds': kinds,
        'reason': '; '.join(categories),
        'replacements': len(google_matches) + len(authorization_matches) + len(local_matches),
        'original_sha256': hashlib.sha256(data).hexdigest(),
        'published_sha256': hashlib.sha256(projected).hexdigest(),
        'original_bytes': len(data),
        'published_bytes': len(projected),
    }
