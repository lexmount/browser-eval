"""Project supplemental raw HTML for publication without changing scored evidence."""
import hashlib
import re
from pathlib import PurePosixPath
from urllib.parse import unquote_plus


GOOGLE_KEY = re.compile(rb'AIzaSy[A-Za-z0-9_-]{20,}')
OTHER_SECRET = re.compile(
    rb'(?<![A-Za-z0-9_-])(?:ahp_[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|'
    rb'sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{20,}|'
    rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)'
)
PUBLIC_AUTH_EXAMPLE = re.compile(
    rb'(?i)(?:proxy-)?authorization\s*:\s*basic\s+aGVsbG86d29ybGQ='
)
PUBLIC_AUTH_PLACEHOLDER = re.compile(
    rb'(?i)(?:proxy-)?authorization\s*[:=]\s*'
    rb'(?:[A-Za-z][A-Za-z0-9_-]*\s+)?(?:<[^>\r\n]+>|\[[^\]\r\n]+\])'
)
AUTH_SENSITIVE_VALUE = re.compile(
    rb'(?im)((?:proxy[-_]?authorization|authorization)[ \t]*[:=][ \t]*)'
    rb'(?![A-Za-z][A-Za-z0-9_-]*[ \t]+(?:<|\[)|(?:<|\[))'
    rb'(?:[A-Za-z][A-Za-z0-9_-]*[ \t]+)?[^\s,;]+'
)
SENSITIVE_KEY = (
    rb'(?:(?:[a-z0-9]+[-_])*(?:proxy[-_]?authorization|authorization|(?:set[-_])?cookie|'
    rb'(?:access|refresh|id)[-_]?token|x[-_]?auth[-_]?token|private[-_]?token|token|'
    rb'(?:aws[-_])?secret[-_]?access[-_]?key|client[-_]?secret|secret|credentials?|'
    rb'x[-_]?api[-_]?key|api[-_]?key|password|passwd))'
)
EMBEDDED_URL = re.compile(r"(?i)\bhttps?://[^\s<>\"'\x00]+")
QUERY_SENSITIVE_KEY = re.compile(
    r'(?i)(?:' + SENSITIVE_KEY.decode('ascii') +
    r'|(?:[a-z0-9]+[-_])*(?:signature|sig|signed|auth|key)|'
    r'apiKey|accessToken|refreshToken|idToken)\Z'
)
JSON_SENSITIVE_VALUE = re.compile(
    rb'(?i)(["\']' + SENSITIVE_KEY + rb'["\']\s*:\s*["\'])(?!<|\[)'
    rb'((?:\\.|[^"\'\r\n])*)(["\'])'
)
LINE_SENSITIVE_VALUE = re.compile(
    rb'(?im)(^|[ \t])(' + SENSITIVE_KEY + rb'[ \t]*[:=])'
    rb'(?![ \t]*(?:<|\[))[ \t]*[^\s,;]+'
)
LOCAL_USER_PATH = re.compile(
    rb'(?<![A-Za-z0-9])(?:/(?:Users|home)/[^/\s"\'<>]+/|/root/)'
)

REDACTIONS = (
    (AUTH_SENSITIVE_VALUE, 'sensitive_field', rb'\1[REDACTED]'),
    (JSON_SENSITIVE_VALUE, 'sensitive_field', rb'\1[REDACTED]\3'),
    (LINE_SENSITIVE_VALUE, 'sensitive_field', rb'\1\2 [REDACTED]'),
)


def redact_url_queries(text):
    """Redact sensitive parameters in URLs embedded in diagnostic prose."""
    replacements = 0

    def redact_url(match):
        nonlocal replacements
        url = match.group()
        query = url.partition('?')[2].partition('#')[0]
        if not query:
            return url
        parts = []
        for item in query.split('&'):
            key, separator, value = item.partition('=')
            # HTML attributes can spell a separator as &amp;.
            logical_key = key.removeprefix('amp;')
            decoded_key = unquote_plus(logical_key).casefold()
            decoded_value = unquote_plus(value)
            sensitive = bool(QUERY_SENSITIVE_KEY.fullmatch(decoded_key))
            placeholder = (decoded_value.startswith('<') and decoded_value.endswith('>')
                           or decoded_value == '[REDACTED]')
            if separator and sensitive and value and not placeholder:
                item = key + '=[REDACTED]'
                replacements += 1
            parts.append(item)
        return url.replace('?' + query, '?' + '&'.join(parts), 1)

    return EMBEDDED_URL.sub(redact_url, text), replacements


def auth_matches(data):
    """Return bounded sensitive-field matches, independent of auth scheme."""
    if (PUBLIC_AUTH_EXAMPLE.fullmatch(data.strip())
            or PUBLIC_AUTH_PLACEHOLDER.fullmatch(data.strip())):
        return []
    matches = sorted(
        [*AUTH_SENSITIVE_VALUE.finditer(data), *JSON_SENSITIVE_VALUE.finditer(data),
         *LINE_SENSITIVE_VALUE.finditer(data)],
        key=lambda match: (match.start(), -match.end()),
    )
    distinct = []
    for match in matches:
        if distinct and match.start() < distinct[-1].end():
            continue
        distinct.append(match)
    return distinct


def publication_risk(data):
    """Return a public-bundle risk class without exposing the matched value."""
    if GOOGLE_KEY.search(data) or OTHER_SECRET.search(data):
        return 'credential-shaped value'
    if auth_matches(data):
        return 'sensitive field value'
    if redact_url_queries(data.decode('utf-8', errors='surrogateescape'))[1]:
        return 'sensitive URL query value'
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
    sensitive_matches = auth_matches(data)
    local_matches = list(LOCAL_USER_PATH.finditer(data))
    url_projected, url_replacements = redact_url_queries(
        data.decode('utf-8', errors='surrogateescape'))
    if not google_matches and not sensitive_matches and not local_matches and not url_replacements:
        return data, None
    projected = GOOGLE_KEY.sub(b'[REDACTED_GOOGLE_API_KEY]',
                               url_projected.encode('utf-8', errors='surrogateescape'))
    for pattern, _, replacement in REDACTIONS:
        projected = pattern.sub(replacement, projected)
    projected = LOCAL_USER_PATH.sub(b'/[REDACTED_USER]/', projected)
    categories = []
    kinds = []
    if google_matches or sensitive_matches or url_replacements:
        categories.append('credential-shaped strings in public source or measured content')
        kinds.append('credential')
    if local_matches:
        categories.append('machine-local user paths')
        kinds.append('local_path')
    return projected, {
        'path': name,
        'kinds': kinds,
        'reason': '; '.join(categories),
        'replacements': len(google_matches) + len(sensitive_matches) + len(local_matches)
                        + url_replacements,
        'original_sha256': hashlib.sha256(data).hexdigest(),
        'published_sha256': hashlib.sha256(projected).hexdigest(),
        'original_bytes': len(data),
        'published_bytes': len(projected),
    }
