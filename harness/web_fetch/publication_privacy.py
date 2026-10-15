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
    """Redact only auxiliary HTML. Any match elsewhere requires human review."""
    path = PurePosixPath(name)
    if 'private_artifacts' in path.parts:
        raise ValueError('Private evidence is not publishable: ' + name)
    if any(part.startswith('.') for part in path.parts):
        raise ValueError('Private/hidden file is not publishable: ' + name)
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
        projected = pattern.sub(replacement, projected)
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


def _host_metadata_projection_path(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or any(p.startswith('.') for p in path.parts):
        return False
    metadata = (path.suffix in {'.json', '.jsonl'}
                and path.parts[0] not in {'artifacts', 'sources', 'logs', 'references'})
    auxiliary_log = path.parts[0] == 'logs' and path.name.endswith(('.debug.txt', '.debug.html'))
    return metadata or auxiliary_log


def project_host_metadata_files(files, host_prefixes, *, host_names=()):
    """Project host metadata/auxiliary logs and explicitly rebind dependencies.

    Inputs and measured payloads are immutable. Returned receipts distinguish
    original file identities from the public copies; public hashes never claim
    to be original call hashes. Only known whole-file digest references change.
    """
    if not host_prefixes or any(not p.startswith('/') or p == '/' for p in host_prefixes):
        raise ValueError('Explicit non-root host prefixes are required')
    patterns = [re.compile(re.escape(p.rstrip('/')) + r"(?=/|$)[^\s\"'<>\x00]*")
                for p in sorted(set(host_prefixes), key=len, reverse=True)]
    json_names = {name for name in files if name.endswith(('.json', '.jsonl'))}
    parsed = {}
    for name in json_names:
        text = files[name].decode('utf-8')
        parsed[name] = ([json.loads(line) for line in text.splitlines() if line.strip()]
                        if name.endswith('.jsonl') else json.loads(text))
    by_hash = {hashlib.sha256(raw).hexdigest(): name for name, raw in files.items()}
    projected, published_hashes, changed_fields, visiting = {}, {}, {}, set()

    def project(name):
        if name in projected:
            return projected[name]
        if name in visiting:
            raise ValueError('Cyclic publication metadata dependencies')
        visiting.add(name)
        raw = files[name]
        fields = []
        def value(node, path=''):
            if isinstance(node, dict):
                if 'hostname' in node and path.endswith('.environment'):
                    fields.append(path + '.hostname')
                return {key: value(item, path + '.' + key) for key, item in node.items()
                        if not (key == 'hostname' and path.endswith('.environment'))}
            if isinstance(node, list):
                return [value(item, path + '[]') for item in node]
            if not isinstance(node, str):
                return node
            if node in by_hash:
                dependency = by_hash[node]
                if dependency != name:
                    project(dependency)
                    return published_hashes[dependency]
            result = node
            for pattern in patterns:
                result = pattern.sub('[HOST_PATH_OMITTED]', result)
            for host in host_names:
                if not host:
                    raise ValueError('Host names must be explicit nonempty identities')
                result = result.replace(host, '[HOSTNAME_OMITTED]')
            if result != node:
                fields.append(path)
            return result
        if name in parsed:
            result = value(parsed[name])
            if result != parsed[name]:
                raw = ((''.join(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n'
                                for row in result)) if name.endswith('.jsonl')
                       else json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode()
        elif host_names:
            raw = value(raw.decode('utf-8'), 'auxiliary_log').encode('utf-8')
        if raw != files[name] and not _host_metadata_projection_path(name):
            raise ValueError('Host projection must not alter scored or response payloads')
        projected[name] = raw
        published_hashes[name] = hashlib.sha256(raw).hexdigest()
        changed_fields[name] = sorted(set(fields))
        visiting.remove(name)
        return raw

    for name in files:
        project(name)
    receipt = {'kind': 'host_metadata_publication_projection',
               'provider_calls_added': 0,
               'meaning': 'Original identities are retained below; published hashes identify redacted copies only.',
               'files': [{'path': name, 'original_sha256': hashlib.sha256(files[name]).hexdigest(),
                          'published_sha256': hashlib.sha256(projected[name]).hexdigest(),
                          'original_bytes': len(files[name]), 'published_bytes': len(projected[name]),
                          'redacted_fields': changed_fields[name]}
                         for name in sorted(files) if files[name] != projected[name]]}
    return projected, receipt


def require_host_metadata_projection(source, binding):
    """Validate public metadata hashes without certifying unavailable originals."""
    from .local_revision import read_bound
    receipt = json.loads(read_bound(source, binding).read_text())
    rows = receipt.get('files', [])
    names = [item['path'] for item in rows]
    if (receipt.get('kind') != 'host_metadata_publication_projection'
            or receipt.get('provider_calls_added') != 0 or len(names) != len(set(names))):
        raise ValueError('Invalid host metadata projection receipt')
    for item in rows:
        if (not _host_metadata_projection_path(item['path'])
                or not re.fullmatch('[0-9a-f]{64}', item.get('original_sha256', ''))
                or item['original_sha256'] == item.get('published_sha256')):
            raise ValueError('Projection must preserve scored payloads and original identities')
        public = read_bound(source, {'path': item['path'], 'sha256': item['published_sha256']})
        if public.stat().st_size != item['published_bytes']:
            raise ValueError('Published metadata size differs from projection receipt')
    return receipt


def project_evidence_view(name, data, *, role, host_prefixes=(), host_names=()):
    """Create a separately identified public view after scoring.

    The caller retains original bytes privately. This function never changes a
    result, score, original binding or source file. Its receipt identifies both
    versions; verifying the public hash does not verify unavailable originals.
    JSON envelopes are traversed structurally so escaped paths are redacted
    without corrupting strings or rewriting numeric measurements.
    """
    path = PurePosixPath(name)
    if (path.is_absolute() or not path.parts or path.parts[0] != 'public-evidence'
            or '..' in path.parts or any(part.startswith('.') for part in path.parts)):
        raise ValueError('Public evidence views require a separate safe public-evidence path')
    if role not in {'scored_answer', 'native_response'}:
        raise ValueError('Unknown public evidence role')
    if any(not p.startswith('/') or p == '/' for p in host_prefixes):
        raise ValueError('Public evidence host prefixes must be explicit non-root paths')
    if any(not host for host in host_names):
        raise ValueError('Public evidence host names must be explicit nonempty identities')
    counts = {}
    patterns = [re.compile(re.escape(p.rstrip('/')) + r'''(?=/|$)[^\s"'<>\\\x00]*''')
                for p in sorted(set(host_prefixes), key=len, reverse=True)]
    # User-home paths in retained diagnostics need a complete replacement, not
    # just a username replacement that would disclose the remaining directories.
    local = re.compile(LOCAL_USER_PATH.pattern + rb'''[^\s"'<>\\\x00]*''')

    def text_view(value):
        before = value
        for pattern in patterns:
            value, count = pattern.subn('[HOST_PATH_OMITTED]', value)
            counts['local_path'] = counts.get('local_path', 0) + count
        for host in host_names:
            count = value.count(host)
            value = value.replace(host, '[HOSTNAME_OMITTED]')
            counts['hostname'] = counts.get('hostname', 0) + count
        raw = value.encode('utf-8')
        raw, count = local.subn(b'[HOST_PATH_OMITTED]', raw)
        counts['local_path'] = counts.get('local_path', 0) + count
        raw, count = OTHER_SECRET.subn(b'[REDACTED_CREDENTIAL]', raw)
        counts['credential_shape'] = counts.get('credential_shape', 0) + count
        projected, receipt = project_file(name, raw)
        if receipt:
            counts['sensitive_values'] = counts.get('sensitive_values', 0) + receipt['replacements']
        result = projected.decode('utf-8')
        return result if result != before else before

    def node_view(value):
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                public_key = text_view(key)
                if public_key in result:
                    raise ValueError('Public view would merge distinct native fields')
                result[public_key] = node_view(item)
            return result
        if isinstance(value, list):
            return [node_view(item) for item in value]
        return text_view(value) if isinstance(value, str) else value

    text = data.decode('utf-8')
    try:
        native = json.loads(text)
    except ValueError:
        projected = text_view(text).encode('utf-8')
        encoding = 'utf-8-text'
    else:
        public = node_view(native)
        projected = (json.dumps(public, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
                     if public != native else data)
        encoding = 'json'
    risk = publication_risk(projected)
    if risk:
        raise ValueError('Public evidence still has a publication risk: '+risk)
    receipt = {'kind': 'post_score_public_evidence_view', 'role': role, 'path': name,
               'encoding': encoding, 'provider_calls_added': 0,
               'original_sha256': hashlib.sha256(data).hexdigest(),
               'original_bytes': len(data),
               'published_sha256': hashlib.sha256(projected).hexdigest(),
               'published_bytes': len(projected),
               'redactions': {key: count for key, count in counts.items() if count},
               'original_retained_privately': True,
               'meaning': 'Public-view bytes are separately identified; original scoring bytes and measurements are unchanged. The public view cannot reconstruct omitted private values.'}
    return projected, receipt


def require_public_evidence_view(source, binding):
    """Verify a declared public view, without claiming original bytes are public."""
    from .local_revision import read_bound
    receipt = json.loads(read_bound(source, binding).read_text())
    name = receipt.get('path', '')
    path = PurePosixPath(name)
    redactions = receipt.get('redactions')
    if (receipt.get('kind') != 'post_score_public_evidence_view'
            or receipt.get('role') not in {'scored_answer', 'native_response'}
            or receipt.get('encoding') not in {'json', 'utf-8-text'}
            or receipt.get('provider_calls_added') != 0
            or receipt.get('original_retained_privately') is not True
            or path.is_absolute() or not path.parts or path.parts[0] != 'public-evidence'
            or '..' in path.parts or any(p.startswith('.') for p in path.parts)
            or not isinstance(redactions, dict)
            or any(key not in {'local_path', 'hostname', 'credential_shape', 'sensitive_values'}
                   or type(count) is not int or count < 1 for key, count in redactions.items())):
        raise ValueError('Invalid separately identified public evidence view')
    for label in ('original', 'published'):
        if (not re.fullmatch('[0-9a-f]{64}', str(receipt.get(label+'_sha256', '')))
                or type(receipt.get(label+'_bytes')) is not int or receipt[label+'_bytes'] < 0):
            raise ValueError('Invalid original/public evidence identity')
    same = receipt['original_sha256'] == receipt['published_sha256']
    if (same and (redactions or receipt['original_bytes'] != receipt['published_bytes'])
            or not same and not redactions):
        raise ValueError('Public view identity does not match its declared redactions')
    public = read_bound(source, {'path': name, 'sha256': receipt['published_sha256']})
    raw = public.read_bytes()
    if len(raw) != receipt['published_bytes'] or publication_risk(raw):
        raise ValueError('Public evidence view bytes or privacy state differ')
    if receipt['encoding'] == 'json':
        json.loads(raw)
    else:
        raw.decode('utf-8')
    return receipt
