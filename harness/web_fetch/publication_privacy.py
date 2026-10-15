"""Project public evidence and execution metadata without changing measurements."""
import hashlib
import html
import json
import re
from copy import deepcopy
from pathlib import PurePosixPath, PureWindowsPath
from urllib.parse import unquote_plus


def project_execution_manifest(manifest: dict) -> dict:
    """Omit host paths while retaining declared config and runtime identities.

    A basename identifies the original file, not a repository default. Hashes,
    including absent or unavailable hashes, remain exactly as recorded.
    """
    result = deepcopy(manifest)
    identity = result.get('runtime_identity_record')
    config_hash = result.get('provider_config_sha256')
    identity_hash = identity.get('provider_config_sha256') if isinstance(identity, dict) else None
    if config_hash is not None and identity_hash is not None and config_hash != identity_hash:
        raise ValueError('Conflicting provider configuration hashes in execution metadata')
    environment = result.get('environment')
    if isinstance(environment, dict):
        environment.pop('hostname', None)
    dataset = result.get('dataset')
    for container, field, marker in (
        (result, 'provider_config_source', 'provider_config_path_omitted'),
        (dataset, 'source', 'source_path_omitted'),
    ):
        source = container.get(field) if isinstance(container, dict) else None
        if not isinstance(source, str):
            continue
        posix, windows = PurePosixPath(source), PureWindowsPath(source)
        if posix.is_absolute() or windows.drive or '..' in windows.parts:
            container[field] = windows.name if windows.drive or '\\' in source else posix.name
            container[marker] = True
    projection = result.get('projection')
    if isinstance(projection, dict):
        projection.pop('source_manifest_path', None)
        projection['source_manifest_retained_in_local_run'] = True
        if projection.get('kind') == 'completed-case-from-live-preflight':
            result['dataset']['case_count'] = len(result['reference_checks']['cases'])
    return result


_DUPLICATED_RESULT_STREAMS = ('stderr', 'stdout', 'browser_stderr')


def project_redundant_result_streams(name: str, data: bytes):
    """Omit result-embedded streams when the public call log owns the same evidence.

    The measured result remains unchanged in the retained evidence tree.  The hosted
    ``results.jsonl`` keeps scores, verdicts, timings, hashes and the call-log binding;
    large diagnostic streams are loaded from that bound log instead of being shipped
    a second time inside every result row.
    """
    if name != 'results.jsonl':
        return data, None
    lines = data.splitlines(keepends=True)
    try:
        rows = [json.loads(line) if line.strip() else None for line in lines]
    except (ValueError, UnicodeError):
        return data, None
    counts = {key: 0 for key in _DUPLICATED_RESULT_STREAMS}
    omitted_bytes = 0
    output = []
    changed = False
    for raw, row in zip(lines, rows):
        row_changed = False
        metadata = row.get('provider_metadata') if isinstance(row, dict) else None
        retry = metadata.get('retry') if isinstance(metadata, dict) else None
        has_public_call_log = bool(
            isinstance(metadata, dict)
            and (
                metadata.get('call_log')
                or metadata.get('call_log_path')
                or (isinstance(retry, dict) and retry.get('attempts'))
            )
            or isinstance(row, dict)
            and (row.get('call_log') or row.get('call_log_path'))
        )
        if has_public_call_log:
            for key in _DUPLICATED_RESULT_STREAMS:
                if key in metadata:
                    value = metadata.pop(key)
                    omitted_bytes += len(str(value).encode('utf-8', errors='replace'))
                    counts[key] += 1
                    changed = True
                    row_changed = True
        if row_changed:
            ending = b'\r\n' if raw.endswith(b'\r\n') else b'\n' if raw.endswith(b'\n') else b''
            output.append(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode() + ending)
        else:
            output.append(raw)
    if not changed:
        return data, None
    projected = b''.join(output)
    return projected, {
        'path': name,
        'reason': 'diagnostic streams are served once from hash-bound public call logs',
        'omitted_fields': {key: value for key, value in counts.items() if value},
        'omitted_payload_bytes': omitted_bytes,
        'original_sha256': hashlib.sha256(data).hexdigest(),
        'published_sha256': hashlib.sha256(projected).hexdigest(),
        'original_bytes': len(data),
        'published_bytes': len(projected),
    }


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
PUBLIC_CORS_CREDENTIAL_POLICY = re.compile(
    rb'''(?i)["']?access[-_]control[-_]allow[-_]credentials["']?\s*[:=]\s*'''
    rb'''(?:%[si])?["'`]*(?:true|false)["'`]*'''
)


def _public_cors_policy(data):
    """A CORS permission Boolean (including ABNF) is not a credential value."""
    decoded = html.unescape(data.decode('utf-8', errors='surrogateescape'))
    return bool(PUBLIC_CORS_CREDENTIAL_POLICY.fullmatch(decoded.strip().encode(
        'utf-8', errors='surrogateescape')))


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
EMBEDDED_URL = re.compile(r"(?i)\bhttps?://[^\s<>\"'\\\x00]+")
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
ENTITY_SENSITIVE_VALUE = re.compile(
    rb'(?i)((&quot;|&#0*34;|&#x0*22;|&apos;|&#0*39;|&#x0*27;)'
    + SENSITIVE_KEY + rb'\2\s*:\s*\2)(?!<|\[)([^\r\n]*?)(\2)'
)
LOCAL_USER_PATH = re.compile(
    rb'(?<![A-Za-z0-9])(?:/(?:Users|home)/[^/\s"\'<>]+/|/root/)'
)

REDACTIONS = (
    (ENTITY_SENSITIVE_VALUE, 'sensitive_field', rb'\1[REDACTED]\4'),
    (AUTH_SENSITIVE_VALUE, 'sensitive_field', rb'\1[REDACTED]'),
    (JSON_SENSITIVE_VALUE, 'sensitive_field', rb'\1[REDACTED]\3'),
    (LINE_SENSITIVE_VALUE, 'sensitive_field', rb'\1\2 [REDACTED]'),
)


def _redact_plain_url_queries(text):
    """Redact sensitive parameters in URLs embedded in diagnostic prose."""
    replacements = 0

    def redact_url(match):
        nonlocal replacements
        original = match.group()
        decoded = original
        while True:
            unescaped = html.unescape(decoded)
            if unescaped == decoded:
                break
            decoded = unescaped
        bounded = EMBEDDED_URL.match(decoded)
        if bounded is None:
            return original
        url = bounded.group()
        suffix = decoded[len(url):]
        query = url.partition('?')[2].partition('#')[0]
        if not query:
            return original
        prior_replacements = replacements
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
        if replacements == prior_replacements:
            return original
        projected = url.replace('?' + query, '?' + '&'.join(parts), 1) + suffix
        return html.escape(projected, quote=True) if decoded != original else projected

    return EMBEDDED_URL.sub(redact_url, text), replacements


_JSON_STRING = re.compile(r'"(?:\\.|[^"\\\x00-\x1f])*"')


def redact_url_queries(text):
    """Decode JSON string escapes before checking URLs; preserve clean bytes."""
    parts = []
    cursor = 0
    replacements = 0
    for match in _JSON_STRING.finditer(text):
        prefix, count = _redact_plain_url_queries(text[cursor:match.start()])
        parts.append(prefix)
        replacements += count
        raw = match.group()
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            projected, count = _redact_plain_url_queries(raw)
        else:
            projected, count = _redact_plain_url_queries(decoded)
            projected = json.dumps(projected, ensure_ascii=True) if count else raw
        parts.append(projected)
        replacements += count
        cursor = match.end()
    suffix, count = _redact_plain_url_queries(text[cursor:])
    parts.append(suffix)
    return ''.join(parts), replacements + count


def auth_matches(data):
    """Return bounded sensitive-field matches, independent of auth scheme."""
    if (PUBLIC_AUTH_EXAMPLE.fullmatch(data.strip())
            or PUBLIC_AUTH_PLACEHOLDER.fullmatch(data.strip())):
        return []
    matches = sorted(
        [*ENTITY_SENSITIVE_VALUE.finditer(data), *AUTH_SENSITIVE_VALUE.finditer(data), *JSON_SENSITIVE_VALUE.finditer(data),
         *LINE_SENSITIVE_VALUE.finditer(data)],
        key=lambda match: (match.start(), -match.end()),
    )
    distinct = []
    for match in matches:
        if _public_cors_policy(match.group()):
            continue
        if distinct and match.start() < distinct[-1].end():
            continue
        distinct.append(match)
    return distinct


def publication_risk(data):
    """Return a public-bundle risk class without exposing the matched value."""
    decoded=html.unescape(data.decode('utf-8',errors='surrogateescape')).encode('utf-8',errors='surrogateescape')
    if GOOGLE_KEY.search(decoded) or OTHER_SECRET.search(decoded):
        return 'credential-shaped value'
    if auth_matches(decoded):
        return 'sensitive field value'
    if redact_url_queries(decoded.decode('utf-8', errors='surrogateescape'))[1]:
        return 'sensitive URL query value'
    if LOCAL_USER_PATH.search(decoded):
        return 'machine-local user path'
    return None


def project_file(name, data):
    """Project public copies and bind their hashes to retained original evidence."""
    path = PurePosixPath(name)
    if 'private_artifacts' in path.parts:
        raise ValueError('Private evidence is not publishable: ' + name)
    if any(part.startswith('.') for part in path.parts):
        raise ValueError('Private/hidden file is not publishable: ' + name)
    if OTHER_SECRET.search(data):
        raise ValueError('Potential credential-shaped value in publication file: ' + name)
    if name.endswith(('.json', '.jsonl')):
        structured = _project_json(name, data)
        if structured is not None:
            return structured
    original = data
    inline_replacements = 0
    inline_kinds = set()
    if name.endswith(('.html', '.html.txt')):
        def project_pre(match):
            nonlocal inline_replacements
            body = html.unescape(match[2].decode('utf-8', errors='surrogateescape'))
            projected, receipt = project_file(name + '.inline.json', body.encode('utf-8', errors='surrogateescape'))
            if receipt is None:
                return match[0]
            inline_replacements += receipt['replacements']
            inline_kinds.update(receipt['kinds'])
            safe = html.escape(projected.decode('utf-8', errors='surrogateescape'), quote=False)
            return match[1] + safe.encode('utf-8', errors='surrogateescape') + match[3]
        data = re.sub(rb'(<pre\b[^>]*>)(.*?)(</pre>)', project_pre, data, flags=re.I | re.S)
    if OTHER_SECRET.search(data):
        raise ValueError('Potential credential-shaped value in publication file: ' + name)
    google_matches = list(GOOGLE_KEY.finditer(data))
    sensitive_matches = auth_matches(data)
    local_matches = list(LOCAL_USER_PATH.finditer(data))
    url_projected, url_replacements = redact_url_queries(
        data.decode('utf-8', errors='surrogateescape'))
    if not google_matches and not sensitive_matches and not local_matches and not url_replacements and not inline_replacements:
        return data, None
    projected = GOOGLE_KEY.sub(b'[REDACTED_GOOGLE_API_KEY]',
                               url_projected.encode('utf-8', errors='surrogateescape'))
    for pattern, _, replacement in REDACTIONS:
        projected = pattern.sub(lambda match: match.group() if _public_cors_policy(
            match.group()) else match.expand(replacement), projected)
    projected = LOCAL_USER_PATH.sub(b'/[REDACTED_USER]/', projected)
    categories = []
    kinds = []
    if google_matches or sensitive_matches or url_replacements or "credential" in inline_kinds:
        categories.append('credential-shaped strings in public source or measured content')
        kinds.append('credential')
    if local_matches or "local_path" in inline_kinds:
        categories.append('machine-local user paths')
        kinds.append('local_path')
    return projected, {
        'path': name,
        'kinds': kinds,
        'reason': '; '.join(categories),
        'replacements': len(google_matches) + len(sensitive_matches) + len(local_matches)
                        + url_replacements + inline_replacements,
        'original_sha256': hashlib.sha256(original).hexdigest(),
        'published_sha256': hashlib.sha256(projected).hexdigest(),
        'original_bytes': len(original),
        'published_bytes': len(projected),
    }


def _project_json(name, data):
    """Project decoded values so header text cannot consume JSON delimiters."""
    lines = data.splitlines(keepends=True) if name.endswith('.jsonl') else [data]
    try:
        documents = [json.loads(line) if line.strip() else None for line in lines]
    except (ValueError, UnicodeError):
        return None
    replacements = 0
    kinds = set()

    def project(value):
        nonlocal replacements
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                if (isinstance(child, str) and child
                        and re.fullmatch(SENSITIVE_KEY, key.encode(), re.I)
                        and not child.startswith(('<', '['))
                        and not PUBLIC_AUTH_PLACEHOLDER.fullmatch((key+': '+child).encode())):
                    result[key] = '[REDACTED]'
                    replacements += 1
                    kinds.add('credential')
                else:
                    result[key] = project(child)
            return result
        if isinstance(value, list):
            return [project(child) for child in value]
        if isinstance(value, str):
            raw, receipt = project_file(name+'.value.txt', value.encode())
            if receipt:
                replacements += receipt['replacements']
                kinds.update(receipt['kinds'])
            return raw.decode()
        return value

    output = []
    for raw, document in zip(lines, documents):
        revised = project(document)
        if revised == document:
            output.append(raw)
        else:
            ending = b'\r\n' if raw.endswith(b'\r\n') else b'\n' if raw.endswith(b'\n') else b''
            output.append(json.dumps(revised, ensure_ascii=True).encode()+ending)
    projected = b''.join(output)
    if projected == data:
        return data, None
    return projected, {'path': name, 'kinds': sorted(kinds),
        'reason': 'sensitive values in decoded JSON; JSON structure and non-sensitive fields retained',
        'replacements': replacements,
        'original_sha256': hashlib.sha256(data).hexdigest(),
        'published_sha256': hashlib.sha256(projected).hexdigest(),
        'original_bytes': len(data), 'published_bytes': len(projected)}
