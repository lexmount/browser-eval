"""Project public evidence and execution metadata without changing measurements."""
import hashlib
from harness.record_io import file_sha256
import html
import json
import re
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath, PureWindowsPath
from threading import Lock
from urllib.parse import unquote_plus


@dataclass(frozen=True)
class EvidenceBinding:
    """Validated identity of bound bytes; parsing never rewrites saved JSON."""

    path: str
    sha256: str

    @classmethod
    def from_mapping(cls, value, *, field='evidence_binding'):
        if not isinstance(value, dict):
            raise ValueError(field + ' must be an object')
        path = value.get('path')
        if not isinstance(path, str) or not path.strip():
            raise ValueError(field + '.path must be a nonempty string')
        digest = value.get('sha256')
        if not isinstance(digest, str) or re.fullmatch('[0-9a-f]{64}', digest) is None:
            raise ValueError(field + '.sha256 must be a lowercase SHA-256 digest')
        return cls(path=path, sha256=digest)


_STAGED_EVIDENCE = ContextVar('webfetch_staged_evidence', default=None)


@contextmanager
def staged_evidence_reads(source, resolve):
    """Expose a proposed write set to ordinary binding checks within this context."""
    token = _STAGED_EVIDENCE.set((Path(source).resolve(), resolve))
    try:
        yield
    finally:
        _STAGED_EVIDENCE.reset(token)


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
    rb'((?:\\.|[^"\'\r\n])+)(["\'])'
)
LINE_SENSITIVE_VALUE = re.compile(
    rb'(?im)(^|[ \t])(' + SENSITIVE_KEY + rb'[ \t]*[:=])'
    rb'(?![ \t]*(?:<|\[))[ \t]*[^\s,;]+'
)
ENTITY_SENSITIVE_VALUE = re.compile(
    rb'(?i)((&quot;|&#0*34;|&#x0*22;|&apos;|&#0*39;|&#x0*27;)'
    + SENSITIVE_KEY + rb'\2\s*:\s*\2)(?!<|\[)([^\r\n]*?)(\2)'
)
# Reader-facing excerpts can stop inside a quoted header value. Bound such a
# value by the current line/text node rather than waiting for a later HTML quote.
JSON_SENSITIVE_TAIL = re.compile(
    rb'(?im)(["\']' + SENSITIVE_KEY + rb'["\']\s*:\s*["\'])(?!<|\[)'
    rb'((?:\\.|[^"\'\r\n<])*)(?=<|$)'
)
ENTITY_SENSITIVE_TAIL = re.compile(
    rb'(?im)((&quot;|&#0*34;|&#x0*22;|&apos;|&#0*39;|&#x0*27;)'
    + SENSITIVE_KEY + rb'\2\s*:\s*\2)(?!<|\[|&lt;|&#0*60;|&#x0*3c;)'
    rb'((?:(?!\2)[^\r\n<])*)(?=<|$)'
)
LOCAL_USER_PATH = re.compile(
    rb'(?<![A-Za-z0-9])(?:/(?:Users|home)/[^/\s"\'<>]+/|/root/)'
)

REDACTIONS = (
    (ENTITY_SENSITIVE_TAIL, 'sensitive_field', rb'\1[REDACTED]'),
    (JSON_SENSITIVE_TAIL, 'sensitive_field', rb'\1[REDACTED]'),
    (ENTITY_SENSITIVE_VALUE, 'sensitive_field', rb'\1[REDACTED]\4'),
    (AUTH_SENSITIVE_VALUE, 'sensitive_field', rb'\1[REDACTED]'),
    (JSON_SENSITIVE_VALUE, 'sensitive_field', rb'\1[REDACTED]\3'),
    (LINE_SENSITIVE_VALUE, 'sensitive_field', rb'\1\2 [REDACTED]'),
)


_URL_ENTITY = re.compile(r'&(?:\#[xX][0-9A-Fa-f]+;?|\#[0-9]+;?|[A-Za-z][A-Za-z0-9]+;)')


def _url_entity_surface(raw, *, _bound=True):
    """Decode URL entities while retaining each character's original span."""
    # Encoded HTML delimiters end the URL. Do not build a per-character map
    # for unrelated minified HTML that the caller will discard from the URL.
    if _bound:
        for match in _URL_ENTITY.finditer(raw):
            if any(char in '\"\'<>\\\x00' or char.isspace()
                   for char in html.unescape(match.group())):
                raw = raw[:match.end()]
                break
            if html.unescape(match.group()) == '&':
                window = raw[match.start():match.end()+128]
                decoded,spans = _url_entity_surface(window,_bound=False)
                stop = next((i for i,char in enumerate(decoded)
                             if char in '\"\'<>\\\x00' or char.isspace()),None)
                if stop is not None:
                    raw = raw[:match.start()+spans[stop][2]]
                    break
    units = [(char, index, index+1) for index, char in enumerate(raw)]
    for _ in range(8):
        text = ''.join(unit[0] for unit in units)
        matches = [m for m in _URL_ENTITY.finditer(text) if html.unescape(m.group()) != m.group()]
        if not matches:
            return text, units
        result, cursor = [], 0
        for match in matches:
            result.extend(units[cursor:match.start()])
            start, end = units[match.start()][1], units[match.end()-1][2]
            result.extend((char, start, end) for char in html.unescape(match.group()))
            cursor = match.end()
        result.extend(units[cursor:])
        units = result
    raise ValueError('Excessively nested URL entities require explicit privacy review')


def _redact_plain_url_queries(text):
    """Redact actual query values, preserving URL/Markdown/entity syntax."""
    replacements, cursor, output = 0, 0, []
    while match := EMBEDDED_URL.search(text, cursor):
        start, end = match.span()
        markdown = bool(re.search(r'\]\([ \t]*$', text[max(0, start-100):start]))
        if markdown:
            depth = 0
            for index in range(start, end):
                if text[index] == '(':
                    depth += 1
                elif text[index] == ')':
                    if depth == 0:
                        end = index
                        break
                    depth -= 1
        raw = text[start:end]
        suffix = ''
        if not markdown:
            for opening, closing in (('(', ')'), ('[', ']'), ('{', '}')):
                while raw.endswith(closing) and raw.count(closing) > raw.count(opening):
                    raw, suffix = raw[:-1], closing + suffix
        if _URL_ENTITY.search(raw):
            url, units = _url_entity_surface(raw)
            stop = next((i for i, ch in enumerate(url) if ch in '\"\'<>\\\x00' or ch.isspace()), len(url))
            if stop < len(url):
                # An encoded HTML attribute delimiter is not query content.
                used = units[stop][1]
                raw, end, suffix = raw[:used], start+used, ''
                url, units = url[:stop], units[:stop]
        else:
            url, units = raw, None
        query_start = url.find('?')
        changes = []
        if query_start >= 0:
            query_end = url.find('#', query_start)
            if query_end < 0:
                query_end = len(url)
            position = query_start+1
            for item in url[position:query_end].split('&'):
                key, separator, value = item.partition('=')
                logical_key = key
                while logical_key.startswith('amp;'):
                    logical_key = logical_key[4:]
                decoded_key, decoded_value = unquote_plus(logical_key).casefold(), unquote_plus(value)
                sensitive = bool(QUERY_SENSITIVE_KEY.fullmatch(decoded_key))
                placeholder = (decoded_value.startswith('<') and decoded_value.endswith('>')
                               or decoded_value == '[REDACTED]')
                if separator and sensitive and value and not placeholder:
                    left, right = position+len(key)+1, position+len(item)
                    changes.append((units[left][1] if units else left,
                                    units[right-1][2] if units else right))
                    replacements += 1
                position += len(item)+1
        for left, right in reversed(changes):
            raw = raw[:left]+'[REDACTED]'+raw[right:]
        output.extend((text[cursor:start], raw+suffix))
        cursor = end
    output.append(text[cursor:])
    return ''.join(output), replacements


_JSON_STRING = re.compile(r'"(?:\\.|[^"\\\x00-\x1f])*"')


def _json_string_spans(text):
    """Match the JSON-string grammar without retrying long invalid suffixes.

    An unterminated escaped string can make an unanchored regex rescan the same
    suffix at every quote. Backward states remember the next valid closing
    quote; forward selection preserves ordinary finditer's nonoverlap rule.
    """
    if '"' not in text:
        return
    if len(text) <= 4096:
        yield from (match.span() for match in _JSON_STRING.finditer(text))
        return
    candidates = []
    next_end = after_next_end = None
    # Ordinary runs leave the backward state unchanged. Let the regex engine
    # skip them in bounded windows instead of visiting every byte in Python.
    def positions():
        special = re.compile(r'["\\\x00-\x1f]')
        for end in range(len(text), 0, -65536):
            for match in reversed(list(special.finditer(text, max(0, end-65536), end))):
                yield match.start()

    following = len(text)
    for index in positions():
        if following > index + 1:
            after_next_end = next_end
        following = index
        char = text[index]
        if char == '"':
            if next_end is not None:
                candidates.append((index, next_end+1))
            end = index
        elif char == '\\':
            end = (after_next_end if index+1 < len(text) and text[index+1] != '\n'
                   else None)
        elif ord(char) < 32:
            end = None
        else:
            end = next_end
        after_next_end, next_end = next_end, end
    cursor = 0
    for start, end in reversed(candidates):
        if start >= cursor:
            yield start, end
            cursor = end


def redact_url_queries(text):
    """Decode JSON string escapes before checking URLs; preserve clean bytes."""
    parts = []
    cursor = 0
    replacements = 0
    for start, end in _json_string_spans(text):
        prefix, count = _redact_plain_url_queries(text[cursor:start])
        parts.append(prefix)
        replacements += count
        raw = text[start:end]
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            projected, count = _redact_plain_url_queries(raw)
        else:
            projected, count = _redact_plain_url_queries(decoded)
            projected = json.dumps(projected, ensure_ascii=True) if count else raw
        parts.append(projected)
        replacements += count
        cursor = end
    suffix, count = _redact_plain_url_queries(text[cursor:])
    parts.append(suffix)
    return ''.join(parts), replacements + count


def auth_matches(data):
    """Return bounded sensitive-field matches, independent of auth scheme."""
    if (PUBLIC_AUTH_EXAMPLE.fullmatch(data.strip())
            or PUBLIC_AUTH_PLACEHOLDER.fullmatch(data.strip())):
        return []
    matches = sorted(
        [*ENTITY_SENSITIVE_TAIL.finditer(data), *JSON_SENSITIVE_TAIL.finditer(data), *ENTITY_SENSITIVE_VALUE.finditer(data), *AUTH_SENSITIVE_VALUE.finditer(data), *JSON_SENSITIVE_VALUE.finditer(data),
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


_large_plain_risks = OrderedDict()


def _plain_publication_risk(data):
    # Cache immutable bytes only. Long evidence retains only a SHA-256 decision,
    # while short keys/values retain at most 8192 * 1024 bytes in the LRU.
    data = bytes(data)
    if len(data) <= 1024:
        return _cached_plain_publication_risk(data)
    key = (len(data),hashlib.sha256(data).digest())
    try:
        result = _large_plain_risks[key]
    except KeyError:
        result = _scan_plain_publication_risk(data)
        _large_plain_risks[key] = result
        while len(_large_plain_risks) > 32768:
            _large_plain_risks.popitem(last=False)
    else:
        try:
            _large_plain_risks.move_to_end(key)
        except KeyError:
            pass  # Concurrent eviction does not invalidate the computed result.
    return result


@lru_cache(maxsize=8192)
def _cached_plain_publication_risk(data):
    return _scan_plain_publication_risk(data)


def _scan_plain_publication_risk(data):
    """Inspect actual text, excluding serialization delimiters."""
    decoded=html.unescape(data.decode('utf-8',errors='surrogateescape')).encode('utf-8',errors='surrogateescape')
    if GOOGLE_KEY.search(decoded) or OTHER_SECRET.search(decoded):
        return 'credential-shaped value'
    if auth_matches(decoded):
        return 'sensitive field value'
    # Bare query parameters such as &timestamp must not be interpreted as the
    # semicolon-less HTML entity &times. Decode explicit named entities and
    # numeric references for URL inspection while preserving raw separators.
    text = data.decode('utf-8', errors='surrogateescape')
    url_surface = re.sub(r'&(?:\#[xX][0-9A-Fa-f]+;?|\#[0-9]+;?|[A-Za-z][A-Za-z0-9]+;)',
                         lambda match: html.unescape(match.group()), text)
    if redact_url_queries(url_surface)[1]:
        return 'sensitive URL query value'
    if LOCAL_USER_PATH.search(decoded):
        return 'machine-local user path'
    return None



def _secret_placeholder(value):
    return bool((value.startswith('<') and value.endswith('>'))
                or (value.startswith('[') and value.endswith(']'))
                or PUBLIC_AUTH_PLACEHOLDER.fullmatch(('Authorization: '+value).encode()))


def _scan_publication_risk(data):
    """Inspect JSON values structurally, otherwise inspect the actual text.

    A serialized quote after a string such as ``Authorization:`` is not an
    authorization value. Nonempty sensitive fields and real header values still
    fail closed, including values hidden by JSON string escapes.
    """
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return _plain_publication_risk(data)

    def inspect(value, *, secret=False):
        if isinstance(value, dict):
            for key, item in value.items():
                risk = _plain_publication_risk(key.encode('utf-8'))
                if risk:
                    return risk
                sensitive = secret or bool(re.fullmatch(SENSITIVE_KEY.decode('ascii'), key, re.I))
                risk = inspect(item, secret=sensitive)
                if risk:
                    return risk
        elif isinstance(value, list):
            for item in value:
                risk = inspect(item, secret=secret)
                if risk:
                    return risk
        elif isinstance(value, str):
            if secret and value and not _secret_placeholder(value):
                return 'sensitive field value'
            return _plain_publication_risk(value.encode('utf-8'))
        return None

    return inspect(payload)


_publication_risks = OrderedDict()
_publication_risks_lock = Lock()


def publication_risk(data):
    """Cache decisions by actual bytes, retaining no payloads or file metadata."""
    data = bytes(data)
    key = (len(data), hashlib.sha256(data).digest())
    # Hold the lock through a miss so concurrent identical inputs scan once.
    # File readers still read and verify their bytes before reaching this owner.
    with _publication_risks_lock:
        if key in _publication_risks:
            _publication_risks.move_to_end(key)
            return _publication_risks[key]
        result = _scan_publication_risk(data)
        _publication_risks[key] = result
        if len(_publication_risks) > 16384:
            _publication_risks.popitem(last=False)
        return result


def project_file(name, data):
    """Project public copies and bind their hashes to retained original evidence."""
    path = PurePosixPath(name)
    if 'private_artifacts' in path.parts:
        raise ValueError('Private evidence is not publishable: ' + name)
    if any(part.startswith('.') for part in path.parts):
        raise ValueError('Private/hidden file is not publishable: ' + name)
    if OTHER_SECRET.search(data):
        raise ValueError('Potential credential-shaped value in publication file: ' + name)
    if name.endswith(('.json', '.jsonl')) or data.lstrip().startswith((b'{',b'[',b'"')):
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


def _host_metadata_projection_path(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or any(p.startswith('.') for p in path.parts):
        return False
    metadata = (path.suffix in {'.json', '.jsonl'}
                and path.parts[0] not in {'artifacts', 'sources', 'logs', 'references'})
    auxiliary_log = path.parts[0] == 'logs' and path.name.endswith(('.debug.txt', '.debug.html'))
    return metadata or auxiliary_log


def project_host_metadata_files(files, host_prefixes, *, host_names=(), public_excerpt_views=False, output_store=None):
    """Project host metadata/auxiliary logs and explicitly rebind dependencies.

    Inputs and measured payloads are immutable. Returned receipts distinguish
    original file identities from the public copies; public hashes never claim
    to be original call hashes. Only known whole-file digest references change.
    """
    if not host_prefixes or any(not p.startswith('/') or p == '/' for p in host_prefixes):
        raise ValueError('Explicit non-root host prefixes are required')
    patterns = [re.compile(re.escape(p.rstrip('/')) + r"(?=/|$)[^\s\"'<>\\\x00]*")
                for p in sorted(set(host_prefixes), key=len, reverse=True)]
    json_names = {name for name in files if name.endswith(('.json', '.jsonl'))}
    original_hashes = {name: hashlib.sha256(files[name]).hexdigest() for name in files}
    by_hash = {digest: name for name, digest in original_hashes.items()}
    # Prewarm hash dependencies before parsing a large parent. Disk-backed
    # mappings can therefore project multi-GB histories one document at a time.
    dependencies = {}
    for name in json_names:
        hashes = {match.group(1).decode('ascii') for match in
                  re.finditer(rb'"([0-9a-f]{64})"(?!\s*:)', files[name])}
        dependencies[name] = {by_hash[digest] for digest in hashes
                              if digest in by_hash and by_hash[digest] != name}
    projected = output_store if output_store is not None else {}
    published_hashes, changed_fields, visiting = {}, {}, set()
    original_sizes, published_sizes, changed_names = {}, {}, set()

    def project(name):
        if name in published_hashes:
            return
        if name in visiting:
            raise ValueError('Cyclic publication metadata dependencies')
        visiting.add(name)
        for dependency in sorted(dependencies.get(name, set())):
            project(dependency)
        original = raw = files[name]
        original_sizes[name] = len(original)
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
            review_excerpt = (path.endswith(('.agent_evidence', '.effective_agent_evidence'))
                    or ('.layers.' in path
                        and path.endswith(('.evidence', '.probes[].text'))))
            if public_excerpt_views and (review_excerpt or path.endswith('.samples[].text')
                    or path.endswith('.agent_evidence_quotes[]') or path.endswith('.evidence_quotes[]')
                    or ('.metrics.' in path and path.endswith('.context'))):
                public, _ = project_evidence_view('public-evidence/excerpt.txt', result.encode('utf-8'),
                    role='scored_answer', host_prefixes=host_prefixes, host_names=host_names,
                    content_format='auto' if review_excerpt else 'text')
                result = public.decode('utf-8')
            if result != node:
                fields.append(path)
            return result
        if name in json_names:
            text = raw.decode('utf-8')
            parsed = ([json.loads(line) for line in text.splitlines() if line.strip()]
                      if name.endswith('.jsonl') else json.loads(text))
            result = value(parsed)
            if result != parsed:
                raw = ((''.join(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n'
                                for row in result)) if name.endswith('.jsonl')
                       else json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode()
        elif host_names:
            raw = value(raw.decode('utf-8'), 'auxiliary_log').encode('utf-8')
        if raw != original and not _host_metadata_projection_path(name):
            raise ValueError('Host projection must not alter scored or response payloads')
        if raw != original:
            changed_names.add(name)
        projected[name] = raw
        published_sizes[name] = len(raw)
        published_hashes[name] = hashlib.sha256(raw).hexdigest()
        changed_fields[name] = sorted(set(fields))
        visiting.remove(name)

    for name in files:
        project(name)
    receipt = {'kind': 'host_metadata_publication_projection',
               'provider_calls_added': 0,
               'meaning': 'Original identities are retained below; published hashes identify redacted copies only.',
               'files': [{'path': name, 'original_sha256': original_hashes[name],
                          'published_sha256': published_hashes[name],
                          'original_bytes': original_sizes[name], 'published_bytes': published_sizes[name],
                          'redacted_fields': changed_fields[name]}
                         for name in sorted(changed_names)]}
    return projected, receipt


def require_host_metadata_projection(source, binding):
    """Validate public metadata hashes without certifying unavailable originals."""
    receipt = json.loads(read_evidence_binding(source, binding).read_text())
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
        public = read_evidence_binding(source, {'path': item['path'], 'sha256': item['published_sha256']})
        if public.stat().st_size != item['published_bytes']:
            raise ValueError('Published metadata size differs from projection receipt')
    return receipt


def project_evidence_view(name, data, *, role, host_prefixes=(), host_names=(), content_format="auto"):
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
    if content_format not in {'auto', 'text', 'json'}:
        raise ValueError('Unknown public evidence content format')
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
                if (re.fullmatch(SENSITIVE_KEY.decode('ascii'), key, re.I)
                        and isinstance(item, str) and item
                        and not ((item.startswith('<') and item.endswith('>'))
                                 or (item.startswith('[') and item.endswith(']')))):
                    result[public_key] = '[REDACTED]'
                    counts['sensitive_values'] = counts.get('sensitive_values', 0) + 1
                else:
                    result[public_key] = node_view(item)
            return result
        if isinstance(value, list):
            return [node_view(item) for item in value]
        return text_view(value) if isinstance(value, str) else value

    text = data.decode('utf-8')
    if content_format == 'text':
        projected = text_view(text).encode('utf-8')
        encoding = 'utf-8-text'
    else:
        try:
            native = json.loads(text)
        except ValueError:
            if content_format == 'json':
                raise ValueError('Declared JSON public evidence is not JSON')
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
    receipt = json.loads(read_evidence_binding(source, binding).read_text())
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
    public = read_evidence_binding(source, {'path': name, 'sha256': receipt['published_sha256']})
    raw = public.read_bytes()
    if len(raw) != receipt['published_bytes'] or publication_risk(raw):
        raise ValueError('Public evidence view bytes or privacy state differ')
    if receipt['encoding'] == 'json':
        json.loads(raw)
    else:
        raw.decode('utf-8')
    return receipt


@lru_cache(maxsize=4)
def _public_view_inventory_index(raw):
    # Cache parsing by exact file bytes, never by a path or timestamp. Every
    # caller still reads and checks the inventory's declared SHA-256.
    index = json.loads(raw)
    if (index.get('kind') != 'post_score_public_evidence_view_inventory'
            or index.get('provider_calls_added') != 0):
        raise ValueError('Invalid public evidence view inventory')
    entries = {}
    for entry in index.get('files', []):
        key = entry.get('original_path'), entry.get('original_sha256')
        if key in entries:
            raise ValueError('Ambiguous public evidence view identity')
        entries[key] = entry
    return entries


def bound_public_view(source, binding):
    """Resolve only a view explicitly bound by this snapshot's public profile.

    A returned view verifies public bytes plus the recorded original identity;
    it does not claim that omitted original bytes have been reconstructed.
    """
    from pathlib import Path
    from lexbench_webfetch.paths import contained_path
    source = Path(source)
    profile_path = contained_path(source, 'presentation-binding.json', reject_symlinks=True)
    if not profile_path.is_file():
        return None
    profile = json.loads(profile_path.read_text())
    declared = profile.get('publication_evidence_views')
    if not declared:
        return None
    index_path = contained_path(source, declared['path'], reject_symlinks=True)
    raw = index_path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != declared.get('sha256'):
        raise ValueError('Public evidence view inventory hash changed')
    entries = _public_view_inventory_index(raw)
    entry = entries.get((binding.get('path'), binding.get('sha256')))
    if entry is None:
        return None
    receipt = require_public_evidence_view(source, entry['view_receipt'])
    original_root = PurePosixPath(binding['path']).parts[0]
    expected_root = {'scored_answer': 'artifacts', 'native_response': 'private_artifacts'}[receipt['role']]
    if original_root != expected_root:
        raise ValueError('Public payload views cannot replace metadata or source authority')
    if receipt['original_sha256'] != binding['sha256']:
        raise ValueError('Public view does not identify the requested original')
    return contained_path(source, receipt['path'], reject_symlinks=True), receipt


def read_evidence_binding(source, binding, *, allow_public_view=True):
    """Verify bound bytes, optionally accepting an explicitly disclosed public view.

    Scoring and exact-byte authorities require originals; presentation checks may
    accept their separately verified public projection. Both use the same path,
    staged-write and hash checks.
    """
    from lexbench_webfetch.paths import contained_path
    identity = EvidenceBinding.from_mapping(binding)
    path = contained_path(source, identity.path, reject_symlinks=True)
    staged = _STAGED_EVIDENCE.get()
    if staged is not None and Path(source).resolve() == staged[0]:
        proposed = staged[1](path)
        if proposed != path:
            if not proposed.is_file() or file_sha256(proposed) != identity.sha256:
                raise ValueError('Staged revision evidence binding changed: ' + identity.path)
            return proposed
    if path.is_file() and file_sha256(path) == identity.sha256:
        return path
    public = bound_public_view(source, binding) if allow_public_view else None
    if public is None:
        raise ValueError('Local revision evidence binding changed: ' + identity.path)
    return public[0]


def original_evidence_size(source, binding):
    """Return verified original size, or its explicitly recorded private size."""
    path = read_evidence_binding(source, binding)
    if file_sha256(path) == binding['sha256']:
        return path.stat().st_size
    public = bound_public_view(source, binding)
    if public is None:
        raise ValueError('Public evidence lacks its original-size disclosure')
    return public[1]['original_bytes']


def evidence_excerpt_matches(source, binding, sample, text):
    """Validate exact original offsets, or a disclosed redacted-view excerpt.

    Original coordinates remain recorded. A redacted public view can verify the
    visible excerpt's presence, not its former private-byte/character position.
    """
    start, end = sample.get('start'), sample.get('end')
    if type(start) is not int or type(end) is not int or not 0 <= start < end:
        return False
    excerpt = sample.get('text')
    if not isinstance(excerpt, str) or not excerpt:
        return False
    if end <= len(text) and text[start:end] == excerpt:
        return True
    public = bound_public_view(source, binding)
    return bool(public and public[1]['redactions'] and excerpt in text)


def require_public_evidence_view_inventory(source, binding, records):
    """Require complete current-answer/native-response view coverage at release."""
    from lexbench_webfetch.paths import contained_path
    if (source/'private_artifacts').exists():
        raise ValueError('Public evidence snapshot must not contain private artifact trees')
    index = json.loads(read_evidence_binding(source, binding).read_text())
    if (index.get('kind') != 'post_score_public_evidence_view_inventory'
            or index.get('provider_calls_added') != 0):
        raise ValueError('Invalid public evidence view inventory')
    entries = index.get('files', [])
    wanted = {(row['artifact_path'], row['metrics']['content_sha256']) for row in records}
    for row in records:
        metadata = row.get('provider_metadata', {})
        if metadata.get('raw_response_path'):
            wanted.add((metadata['raw_response_path'], metadata['raw_response_sha256']))
    identities = set()
    for entry in entries:
        identity = entry.get('original_path'), entry.get('original_sha256')
        if identity in identities:
            raise ValueError('Public evidence view inventory repeats an original')
        identities.add(identity)
        receipt = require_public_evidence_view(source, entry['view_receipt'])
        root = PurePosixPath(identity[0]).parts
        expected_root = {'scored_answer': 'artifacts', 'native_response': 'private_artifacts'}[receipt['role']]
        if (not root or root[0] != expected_root or '..' in root
                or receipt['original_sha256'] != identity[1]):
            raise ValueError('Public evidence view inventory changes an original authority')
        if identity in wanted and receipt['role'] == 'scored_answer':
            alias = contained_path(source, identity[0], reject_symlinks=True)
            if (not alias.is_file()
                    or file_sha256(alias) != receipt['published_sha256']):
                raise ValueError('Displayed answer differs from its declared public view')
    if not wanted <= identities:
        raise ValueError('Public evidence view inventory omits current evidence')
    return index


def public_render_views(source, records):
    """Read bound display annotations; never rewrite saved result records."""
    profile_path = source/'presentation-binding.json'
    if not profile_path.is_file():
        return {}
    profile = json.loads(profile_path.read_text())
    declared = profile.get('publication_evidence_views')
    if not declared:
        return {}
    index = json.loads(read_evidence_binding(source, declared).read_text())
    entries = {(e['original_path'], e['original_sha256']): e for e in index['files']}
    result = {}
    for row in records:
        metadata = row.get('provider_metadata', {})
        wanted = {'scored_answer': (row['artifact_path'], row['metrics']['content_sha256'])}
        if metadata.get('raw_response_path'):
            wanted['native_response'] = (metadata['raw_response_path'], metadata['raw_response_sha256'])
        views = {}
        for role, identity in wanted.items():
            if identity not in entries:
                raise ValueError('Public rendering omits a declared evidence view')
            binding = entries[identity]['view_receipt']
            receipt = json.loads(read_evidence_binding(source, binding).read_text())
            if receipt['role'] != role or receipt['original_sha256'] != identity[1]:
                raise ValueError('Public rendering changed its original evidence identity')
            views[role] = {key: receipt[key] for key in ('path', 'original_sha256', 'published_sha256',
                                                       'original_bytes', 'published_bytes', 'redactions')}
            views[role]['receipt'] = binding['path']
        result[row['attempt_id']] = views
    return result


def _project_json(name, data):
    """Project decoded values so header text cannot consume JSON delimiters."""
    lines = data.splitlines(keepends=True) if name.endswith('.jsonl') else [data]
    try:
        documents = [json.loads(line) if line.strip() else None for line in lines]
    except (ValueError, UnicodeError):
        return None
    replacements = 0
    kinds = set()

    def project(value, *, secret=False):
        nonlocal replacements
        if isinstance(value, dict):
            return {key:project(child,secret=secret or bool(re.fullmatch(SENSITIVE_KEY,key.encode(),re.I)))
                    for key,child in value.items()}
        if isinstance(value, list):
            return [project(child,secret=secret) for child in value]
        if isinstance(value, str):
            if secret and value and not _secret_placeholder(value):
                replacements += 1
                kinds.add('credential')
                return '[REDACTED]'
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


def publication_file_risk(name, data):
    """Scan JSONL records independently; malformed records keep the full scan.

    Valid JSON strings cannot cross a physical record newline. Keeping that
    boundary avoids promoting a whole large matrix to one wide Unicode string.
    """
    if not name.endswith('.jsonl'):
        return publication_risk(data)
    import io
    for line in io.BytesIO(data):
        if not line.strip():
            continue
        try:
            json.loads(line)
        except (ValueError,UnicodeError,RecursionError):
            return publication_risk(data)
        risk = publication_risk(line)
        if risk:
            return risk
    return None
