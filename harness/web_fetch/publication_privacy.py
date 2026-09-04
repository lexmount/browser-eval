"""Project supplemental raw HTML for publication without changing scored evidence."""
import hashlib
import re
from pathlib import PurePosixPath


GOOGLE_KEY = re.compile(rb'AIzaSy[A-Za-z0-9_-]{20,}')
OTHER_SECRET = re.compile(
    rb'(?<![A-Za-z0-9_-])(?:ahp_[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|'
    rb'sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)'
)


def project_file(name, data):
    """Redact only auxiliary HTML. Any match elsewhere requires human review."""
    path = PurePosixPath(name)
    if any(part.startswith('.') for part in path.parts):
        raise ValueError('Private/hidden file is not publishable: ' + name)
    if OTHER_SECRET.search(data):
        raise ValueError('Potential credential in publication file: ' + name)
    matches = list(GOOGLE_KEY.finditer(data))
    if not matches:
        return data, None
    if path.parts[0] not in {'artifacts', 'sources'} or not name.endswith('.html.txt'):
        raise ValueError('Potential credential outside supplemental HTML: ' + name)
    projected = GOOGLE_KEY.sub(b'[REDACTED_GOOGLE_API_KEY]', data)
    return projected, {
        'path': name,
        'reason': 'Google API-key-shaped strings in third-party raw HTML; validity not tested',
        'replacements': len(matches),
        'original_sha256': hashlib.sha256(data).hexdigest(),
        'published_sha256': hashlib.sha256(projected).hexdigest(),
        'original_bytes': len(data),
        'published_bytes': len(projected),
    }
