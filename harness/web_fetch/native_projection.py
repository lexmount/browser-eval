"""Deterministic native answer selection, bound before a cohort starts.

Branches depend only on response structure and transport outcome. Keeping their
implementation in one owner lets acquisition and saved-evidence checks replay
the same policy rather than accepting any later declaration of allowed fields.
"""
import base64
import binascii
import hashlib
import json
import re
from functools import lru_cache
from urllib.parse import parse_qsl, quote, urljoin, urlsplit

from lexbench_webfetch.paths import validate_http_url
from pathlib import Path


def native_projection_source():
    return Path(__file__).read_bytes()


def native_projection_regressions():
    """Executable source-boundary controls owned by this acquisition layer."""
    document = json.loads((Path(__file__).parent / 'data/native-projection-regressions.json').read_text())
    rows = document.get('cases', [])
    indexed = {row['id']: row for row in rows}
    if document.get('schema_version') != 1 or not rows or len(indexed) != len(rows):
        raise ValueError('Invalid native projection regression registry')
    return indexed


def native_projection_policy(provider_type):
    digest = hashlib.sha256(native_projection_source()).hexdigest()
    return {'kind': 'native-answer-selection', 'version': 1,
            'provider_type': provider_type,
            'source_sha256': digest, 'source_path': 'native-answer-policies/'+digest+'.py'}


def _object(value):
    return value if isinstance(value, dict) else {}


def native_answer_projection(provider_type, case, payload, status_code=None, *, interface=None):
    """Return the declaration selected by the pre-call structural policy."""
    get = case.get if isinstance(case, dict) else lambda key, default=None: getattr(case, key, default)
    track, task_kind = get('track', 'web'), get('task_kind', 'body')
    payload = _object(payload)
    successful_http = type(status_code) is int and 200 <= status_code < 300
    if provider_type == 'brightdata_unlocker':
        return {'schema_version': 2, 'source_format': 'bytes',
                'content_format': 'utf8' if successful_http else 'empty'}
    if provider_type in {'browser_use_fetch', 'browserbase_fetch', 'deepseek_fetch', 'crawl4ai_cloud'}:
        field = ('body' if provider_type == 'browser_use_fetch' else
                 'markdown' if provider_type == 'crawl4ai_cloud' else 'content')
        value = payload.get(field)
        fmt = 'text' if isinstance(value, str) else 'empty'
        declaration = {'schema_version': 2, 'content_pointer': [field],
                       'content_format': fmt if successful_http else 'empty',
                       'optional_content': True, 'result_fields': {}}
        if provider_type == 'deepseek_fetch':
            declaration['result_fields']['title'] = {'pointer': ['title'], 'format': 'text_or_none'}
        if provider_type == 'browser_use_fetch':
            declaration['result_fields']['final_url'] = {'pointer': ['final_url'], 'format': 'text_or_none'}
        return declaration
    if provider_type in {'tavily_extract', 'exa_contents'}:
        results = payload.get('results')
        result = _object(results[0]) if isinstance(results, list) and results else {}
        field = 'raw_content' if provider_type == 'tavily_extract' else 'text'
        content = result.get(field) if isinstance(result.get(field), str) else ''
        ok = successful_http and bool(content)
        if provider_type == 'exa_contents':
            statuses = payload.get('statuses')
            status = _object(statuses[0]) if isinstance(statuses, list) and statuses else {}
            ok = ok and status.get('status') in (None, 'success')
        if not (ok or content or result.get('url')):
            return None
        declaration = {'schema_version': 2, 'content_pointer': ['results', 0, field],
                       'content_format': 'text', 'optional_content': True,
                       'result_fields': {'final_url': {'pointer': ['results', 0, 'url'],
                                                       'fallback': 'request_url'}}}
        if provider_type == 'tavily_extract':
            declaration['answer_fields'] = ({'images': {
                'pointer': ['results', 0, 'images'] if result.get('images') else ['images'],
                'format': 'identity', 'default': []}} if ok else {})
        else:
            if ok:
                declaration['result_fields']['title'] = {'pointer': ['results', 0, 'title']}
            declaration['answer_fields'] = ({
                'extras': {'pointer': ['results', 0, 'extras'], 'format': 'identity', 'default': {}},
                'links': {'pointer': ['results', 0, 'extras', 'richLinks'], 'format': 'named_links', 'default': []}}
                if ok else {})
        return declaration
    if provider_type == 'firecrawl':
        data = _object(payload.get('data'))
        metadata = _object(data.get('metadata'))
        file_route = task_kind == 'file'
        video_route = task_kind == 'resource' and track == 'video'
        api_ok = successful_http and payload.get('success') is True
        file_bytes = b''
        if file_route and api_ok and isinstance(data.get('rawBase64'), str):
            try:
                file_bytes = base64.b64decode(data['rawBase64'], validate=True)
            except ValueError:
                pass
        content = data.get('markdown') if isinstance(data.get('markdown'), str) else ''
        videos = [item for item in data.get('videos', []) if isinstance(item, (str, dict))] if isinstance(data.get('videos'), list) else []
        if isinstance(data.get('video'), str) and data['video']:
            videos.append(data['video'])
        ok = api_ok and (bool(file_bytes) if file_route else bool(content) or bool(videos and video_route))
        final_url = metadata.get('url')
        if not ok and not (isinstance(final_url, str) and final_url):
            return None
        fields = {} if file_route or not ok else {
            'links': {'pointer': ['data', 'links'], 'format': 'identity', 'default': []},
            'images': {'pointer': ['data', 'images'], 'format': 'identity', 'default': []}}
        if fields and video_route:
            fields['videos'] = {'pointer': ['data', 'videos'], 'format': 'video_values', 'default': [],
                                'legacy': {'pointer': ['data', 'video'], 'default': ''}}
        return {'schema_version': 2, 'content_pointer': ['data', 'markdown'],
                'content_format': 'empty' if file_route or not ok else 'text', 'optional_content': True,
                'result_fields': {'final_url': {'pointer': ['data', 'metadata', 'url'], 'format': 'text_or_none', 'nonempty': True},
                    **({'title': {'pointer': ['data', 'metadata', 'title'], 'format': 'text_or_none'}} if ok else {})},
                'answer_fields': fields,
                **({'files': {'pointer': ['data', 'rawBase64'], 'format': 'base64_file'}} if file_bytes else {})}
    if provider_type in {'cloudflare_markdown', 'cloudflare_content'}:
        success = payload.get('success') is True
        meta = _object(payload.get('meta'))
        resources = (interface == '/scrape' if interface is not None else
                     provider_type == 'cloudflare_markdown' and task_kind == 'resource' and track in {'image', 'video'})
        if resources:
            if not success and not meta:
                return None
            key = 'images' if track == 'image' else 'videos'
            return {'schema_version': 2, 'content_pointer': ['result'],
                    'content_format': 'json' if success else 'empty',
                    'result_fields': {'final_url': {'pointer': ['meta', 'finalUrl'], 'fallback': 'request_url'}},
                    'answer_fields': {key: {'pointer': ['result'], 'format': 'html_attribute_urls',
                                           'srcset_parser': SRCSET_PARSER, 'base_url': meta.get('finalUrl') or get('url')}} if success else {}}
        text = success and isinstance(payload.get('result', ''), str)
        if not text and not meta:
            return None
        return {'schema_version': 2, 'content_pointer': ['result'],
                'content_format': 'text' if text else 'empty', 'optional_content': True,
                'result_fields': {**({'title': {'pointer': ['meta', 'title']}} if provider_type == 'cloudflare_markdown' else {}),
                    'final_url': {'pointer': ['meta', 'finalUrl'], 'fallback': 'request_url'}}}
    if provider_type in {'subprocess_jsonl', 'cowagent_builtin'}:
        return {'schema_version': 2, 'content_pointer': ['content'],
                'content_format': 'text' if isinstance(payload.get('content'), str) else 'empty',
                'result_fields': {'title': {'pointer': ['title']},
                                  'final_url': {'pointer': ['final_url'], 'fallback': 'request_url'}},
                'answer_fields': {key: {'pointer': ['metadata', key], 'format': 'identity'}
                                  for key in ('links', 'images', 'videos', 'media', 'extras')
                                  if key in _object(payload.get('metadata'))},
                **({'files': {'pointer': ['files'], 'format': 'worker_files'}} if payload.get('files') else {})}
    if provider_type == 'moli_webfetch':
        from .providers.moli import _route_for_case
        from types import SimpleNamespace
        projected_case = SimpleNamespace(**case) if isinstance(case, dict) else case
        raw = _route_for_case(projected_case, native_default=True) in {'raw_file', 'native_document_bytes'}
        return {'schema_version': 2, 'source_format': 'bytes',
                'content_format': 'empty' if raw else 'utf8',
                **({'files': {'format': 'raw_bytes'}} if raw else
                   {'result_fields': {'final_url': {'format': 'fetch_log_final_url'}}})}
    if provider_type == 'fixture':
        return {'schema_version': 2, 'content_pointer': ['content'], 'content_format': 'text',
                'result_fields': {'title': {'pointer': ['title']}, 'final_url': {'pointer': ['final_url']}}}
    raise ValueError('Unsupported native answer selection policy: ' + str(provider_type))


def native_field_value(payload, pointer):
    if not isinstance(pointer, list) or not pointer:
        raise ValueError('Native answer projection requires a nonempty field pointer')
    value = payload
    try:
        for key in pointer:
            if isinstance(value, dict) and isinstance(key, str):
                value = value[key]
            elif isinstance(value, list) and type(key) is int and key >= 0:
                value = value[key]
            else:
                raise ValueError('Native answer pointer is not a structural field selection')
    except (KeyError, IndexError) as error:
        raise ValueError('Native answer field is missing') from error
    return value


SRCSET_PARSER = 'html-candidates-v1'


@lru_cache(maxsize=1)
def _historical_srcset_parsers():
    registry = json.loads((Path(__file__).parent / 'data/native-projection-history.json').read_text())
    if registry.get('schema_version') != 2:
        raise ValueError('Unsupported native projection history schema')
    parsers = {source: entry['parser'] for source, entry in registry['scoring_sources'].items()}
    if any(parser not in {SRCSET_PARSER, 'legacy-comma-split'} for parser in parsers.values()):
        raise ValueError('Unsupported historical srcset parser')
    return parsers


def _srcset_urls(value, parser, historical_scoring_source):
    # Source identities bind the historical projection, never the resulting answer.
    historical = _historical_srcset_parsers().get(historical_scoring_source)
    if parser is not None:
        if parser != SRCSET_PARSER or historical not in {None, parser}:
            raise ValueError('Native srcset parser differs from its bound semantics')
    else:
        parser = historical
    if parser == SRCSET_PARSER:
        from lexbench_webfetch.resource_urls import srcset_urls
        return srcset_urls(value)
    if parser == 'legacy-comma-split':
        return [part.strip().split()[0] for part in value.split(',') if part.strip()]
    raise ValueError('Native srcset projection lacks a recognized parser binding')


def _attribute_record_urls(groups, base_url, *, parser=None, historical_scoring_source=None):
    """Project declared HTML URL attributes without classifying their targets."""
    urls = []
    for group in groups if isinstance(groups, list) else []:
        if not isinstance(group, dict):
            continue
        elements = group.get('results')
        for element in elements if isinstance(elements, list) else []:
            if not isinstance(element, dict):
                continue
            attributes = element.get('attributes')
            for attribute in attributes if isinstance(attributes, list) else []:
                if not isinstance(attribute, dict):
                    continue
                name, value = attribute.get('name'), attribute.get('value')
                if not isinstance(name, str) or not isinstance(value, str):
                    continue
                name = name.casefold()
                if name not in {'src', 'srcset', 'href', 'data-src', 'data-original'}:
                    continue
                values = (_srcset_urls(value, parser, historical_scoring_source)
                          if name == 'srcset' else [value])
                for raw in values:
                    try:
                        urls.append(urljoin(base_url, raw.strip()))
                    except ValueError:
                        # Retain malformed native values; the shared scorer,
                        # not a provider adapter, decides URL eligibility.
                        urls.append(raw)
    return sorted(set(urls))


SCORE_ANSWER_FIELDS = frozenset({'links', 'images', 'videos', 'media', 'extras'})


def _selected(payload, spec):
    # Missing native fields may mean no answer, never a synthesized answer.
    _require_field_spec(spec, 'native field')
    if 'default' in spec and spec['default'] not in (None, '', [], {}):
        raise ValueError('Native answer defaults must be empty')
    try:
        return native_field_value(payload, spec['pointer'])
    except ValueError:
        if 'default' not in spec:
            raise
        return spec['default']


def _project_field(payload, spec, *, historical_scoring_source=None):
    value = _selected(payload, spec)
    kind = spec.get('format', 'identity')
    if kind == 'identity':
        return value
    if kind == 'html_attribute_urls':
        return _attribute_record_urls(value, spec['base_url'], parser=spec.get('srcset_parser'),
                                      historical_scoring_source=historical_scoring_source)
    if kind == 'named_links':
        return [{'text': item['anchor'], 'href': item['url']}
                for item in value if isinstance(item, dict)
                and isinstance(item.get('anchor'), str) and item['anchor'].strip()
                and isinstance(item.get('url'), str) and item['url'].strip()]
    if kind == 'video_values':
        values = [item for item in value if isinstance(item, (str, dict))] if isinstance(value, list) else []
        legacy = _selected(payload, spec['legacy'])
        return values + ([legacy] if isinstance(legacy, str) and legacy else [])
    raise ValueError('Unknown native answer field projection')


def require_native_projection_schema(declaration):
    """Keep historical answer scope separate from schema-2 result context."""
    if not isinstance(declaration, dict):
        raise ValueError('Native projection declaration must be an object')
    if type(declaration.get('schema_version')) is not int or declaration['schema_version'] not in {1, 2}:
        raise ValueError('Unsupported native answer projection')
    if declaration['schema_version'] == 1 and 'result_fields' in declaration:
        raise ValueError('Native result_fields require projection schema 2')
    for group, allowed in [('answer_fields', SCORE_ANSWER_FIELDS),
                           ('result_fields', {'title', 'final_url'})]:
        fields = declaration.get(group, {})
        if not isinstance(fields, dict):
            raise ValueError(group + ' must be an object')
        for name, spec in fields.items():
            field = group + '.' + str(name)
            if name not in allowed:
                raise ValueError('Unknown native projection field: ' + field)
            _require_field_spec(spec, field)
    if declaration.get('files') is not None:
        _require_field_spec(declaration['files'], 'files')


def _require_field_spec(spec, field):
    """Reject malformed declarations with a field location, before reading data."""
    if not isinstance(spec, dict):
        raise ValueError(field + ' must be an object')
    if 'srcset_parser' in spec and (spec.get('format') != 'html_attribute_urls'
                                    or spec['srcset_parser'] != SRCSET_PARSER):
        raise ValueError(field + '.srcset_parser is unsupported')
    if 'format' in spec and not isinstance(spec['format'], str):
        raise ValueError(field + '.format must be text')
    if 'pointer' not in spec and spec.get('format', 'identity') not in {
            'request_url', 'fetch_log_final_url', 'transport_response_url', 'raw_bytes'}:
        raise ValueError(field + '.pointer is required')
    if 'pointer' in spec:
        pointer = spec['pointer']
        if (not isinstance(pointer, list) or not pointer
                or any(not isinstance(key, str) and not (type(key) is int and key >= 0)
                       for key in pointer)):
            raise ValueError(field + '.pointer must be a nonempty list of names or nonnegative indexes')
    if 'legacy' in spec:
        _require_field_spec(spec['legacy'], field + '.legacy')


def project_native_answer(payload, declaration, *, historical_scoring_source=None):
    """Select declared native fields; never inspect references or URL meaning."""
    require_native_projection_schema(declaration)
    kind = declaration.get('content_format')
    if declaration.get('source_format') == 'bytes':
        if declaration.get('answer_fields'):
            raise ValueError('Native byte projection cannot declare structured answer fields')
        if not isinstance(payload, bytes):
            raise ValueError('Native byte projection requires bytes')
        if kind == 'utf8':
            try:
                return payload.decode('utf-8'), {}
            except UnicodeDecodeError:
                # Invalid text remains raw evidence, never repaired answer text.
                return '', {}
        if kind == 'empty':
            return '', {}
        raise ValueError('Unknown native byte projection')
    if kind == 'empty':
        content = ''
    else:
        value = _selected(payload, {'pointer': declaration.get('content_pointer'),
                                  **({'default': ''} if declaration.get('optional_content') else {})})
        if kind == 'text' and isinstance(value, str):
            content = value
        elif kind == 'json':
            content = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
        else:
            raise ValueError('Native answer field differs from its declared type')
    fields = {}
    for name, spec in declaration.get('answer_fields', {}).items():
        if name not in SCORE_ANSWER_FIELDS:
            raise ValueError('Unknown native answer metadata field')
        fields[name] = _project_field(payload, spec, historical_scoring_source=historical_scoring_source)
    return content, fields


def project_native_files(payload, declaration):
    """Decode native delivered bytes; filenames and logs cannot supply answers."""
    spec = declaration.get('files')
    if spec is None:
        return []
    if not isinstance(spec, dict):
        raise ValueError('Native file projection must be an object')
    kind = spec.get('format')
    if kind not in {'raw_bytes', 'base64_file', 'worker_files'}:
        raise ValueError('Unknown native file projection')
    if kind == 'raw_bytes':
        if not isinstance(payload, bytes):
            raise ValueError('Native raw file projection requires bytes')
        return [payload] if payload else []
    value = _selected(payload, spec)
    if kind == 'base64_file':
        values = [value] if value else []
        if not isinstance(value, str):
            raise ValueError('Native base64 file projection requires text')
    else:
        if not isinstance(value, list) or any(
            not isinstance(item, dict) or not isinstance(item.get('base64'), str)
            for item in value
        ):
            raise ValueError('Native worker files require objects with base64 text')
        values = [item['base64'] for item in value]
    try:
        return [base64.b64decode(item, validate=True) for item in values]
    except (binascii.Error, ValueError) as error:
        raise ValueError('Native file projection contains invalid base64') from error


def native_fetch_diagnostics(stderr: bytes, requested_url: str) -> tuple[int | None, bool]:
    """Read target HTTP events only through the captured document's lifetime.

    A pending-navigation snapshot can precede renderer shutdown and a late
    replacement commit. A unique page-turn/capture/close sequence binds that
    cutoff; without it, later commits keep legacy ownership ambiguous.
    """
    prefix = rb"(?:^|\n)(?:\d{4}-\d{2}-\d{2}T[^\s]+\s+)?"
    captures = list(re.finditer(prefix + rb"DEBUG capturing page state while a location navigation is pending\b[^\r\n]*", stderr))
    if len(captures) == 1:
        capture = captures[0]
        turns = list(re.finditer(prefix + rb"DEBUG completed one ordinary Page turn\b[^\r\n]*\bpage_id=(\d+)\b", stderr[:capture.start()]))
        if turns and len({row.group(1) for row in turns}) == 1:
            page = turns[-1].group(1)
            closes = list(re.finditer(prefix + rb"DEBUG closing renderer page handle page_id=" + re.escape(page) + rb" requested_execution_interrupt=true\b", stderr[capture.end():]))
            if len(closes) == 1:
                stderr = stderr[:capture.start()]
    rows = list(re.finditer(prefix + rb"DEBUG fetch request complete method=(?:GET|POST) url=(?P<url>\S+) final_url=\S+ status=(?P<status>\d{3})\b[^\r\n]*", stderr))
    matches = [row for row in rows if row.group('url') == requested_url.encode('utf-8')]
    status = None
    if len(matches) == 1:
        row = matches[0]
        value = int(row.group('status'))
        if not 300 <= value < 400 and not any(marker in stderr[row.end():] for marker in (
                b'committed main navigation before replacement realm bootstrap',
                b'detached committed main WindowProxy for replacement context')):
            status = value
    waits = re.finditer(prefix + rb"WARN fetch readiness wait timed out; returning best-effort page page_id=\d+ url=(?P<url>\S+) final_url=\S+ wait_until=(?:DomStable|NetworkIdle) timeout_ms=[1-9]\d* remaining_ms=\d+ error=[^\r\n]+", stderr)
    return status, any(row.group('url') == requested_url.encode('utf-8') for row in waits)


def main_document_url_from_fetch_log(stderr: str | bytes, requested_url: str) -> str | None:
    """Replay the effective main-document URL from the same call's debug log."""
    pattern = re.compile(
        rb"fetch request complete method=(?:GET|POST) url=(?P<url>\S+) "
        rb"final_url=(?P<final_url>\S+) status=(?P<status>\d{3})\b"
    )

    diagnostic = stderr.encode("utf-8") if isinstance(stderr, str) else stderr
    navigation_pattern = re.compile(
        rb"following pending location navigation asynchronously[^\n]* url=(?P<url>\S+)"
    )

    def field(row: re.Match[bytes], name: str) -> str:
        return row.group(name).decode("utf-8", "replace")

    def identity(value: str) -> tuple | None:
        try:
            validate_http_url(value)
            parts = urlsplit(value)
            return (
                parts.scheme.casefold(),
                (parts.hostname or "").casefold(),
                parts.port if parts.port is not None else (443 if parts.scheme.casefold() == "https" else 80),
                re.sub(r"%[0-9a-fA-F]{2}", lambda match: match[0].upper(),
                       quote(parts.path, safe="/%:@!$&'()*+,;=")).rstrip("/") or "/",
                sorted(parse_qsl(parts.query, keep_blank_values=True)),
            )
        except ValueError:
            return None

    requested_identity = identity(requested_url)
    if requested_identity is None:
        return None
    rows = list(pattern.finditer(diagnostic))

    # Legacy completion records do not identify redirect hops or request owners.
    # Never infer the document from GET ordering or challenge query parameters.
    row = next((candidate for candidate in rows
                if identity(field(candidate, "url")) == requested_identity), None)
    if row is None or 300 <= int(field(row, "status")) < 400:
        return None
    navigations = list(navigation_pattern.finditer(diagnostic))
    for index, navigation in enumerate(navigations):
        boundary = navigations[index + 1].start() if index + 1 < len(navigations) else len(diagnostic)
        committed = diagnostic.find(
            b"committed main navigation before replacement realm bootstrap",
            navigation.end(), boundary,
        )
        if committed < 0:
            continue
        navigation_identity = identity(field(navigation, "url"))
        if navigation_identity is None:
            return None
        matches = [candidate for candidate in rows
                   if navigation.end() <= candidate.start() < boundary
                   and identity(field(candidate, "url")) == navigation_identity]
        if len(matches) != 1 or 300 <= int(field(matches[0], "status")) < 400:
            return None
        row = matches[0]
    candidate = field(row, "final_url")
    if identity(candidate) is None:
        return None
    try:
        validate_http_url(candidate)
    except ValueError:
        return None
    return candidate



def project_native_result_fields(payload, declaration, *, request_url=None,
                                 diagnostic=None, transport_receipt=None):
    """Replay scored title/address from native fields or same-call evidence."""
    require_native_projection_schema(declaration)
    fields = {'title': None, 'final_url': None}
    for name, spec in declaration.get('result_fields', {}).items():
        if name not in fields:
            raise ValueError('Unknown native result field')
        kind = spec.get('format', 'identity')
        if kind == 'request_url':
            if name != 'final_url' or request_url is None:
                raise ValueError('Native URL fallback requires its declared request URL')
            value = request_url
        elif kind == 'fetch_log_final_url':
            if name != 'final_url' or diagnostic is None or request_url is None:
                raise ValueError('Native final URL lacks its same-call diagnostic evidence')
            value = main_document_url_from_fetch_log(diagnostic, request_url)
        elif kind == 'transport_response_url':
            if name != 'final_url' or not transport_receipt:
                raise ValueError('Native final URL lacks its source transport receipt')
            value = transport_receipt['response_url']
        else:
            # Schema 2 historically treats result context as optional. Preserve
            # missing-field replay, but never overwrite an explicit default.
            value = _selected(payload, {'default': None, **spec})
            if kind == 'text_or_none':
                value = value if isinstance(value, str) else None
            elif kind != 'identity':
                raise ValueError('Unknown native result field projection')
            if spec.get('nonempty') and not value:
                value = None
            if not value and spec.get('fallback') == 'request_url':
                if name != 'final_url' or request_url is None:
                    raise ValueError('Native URL fallback requires its declared request URL')
                value = request_url
        fields[name] = value
    return fields


def require_native_answer_projection(content, metadata, raw_response, *, files=(), title=None, final_url=None,
                                     request_url=None, diagnostic=None, transport_receipt=None,
                                     historical_scoring_source=None):
    """Reject every undeclared score-bearing input, then check exact provenance."""
    declaration = metadata.get('native_answer_projection')
    present = {key for key in SCORE_ANSWER_FIELDS if metadata.get(key)}
    if declaration is None:
        if (present or files or title is not None
                or (final_url is not None and final_url != request_url)
                or (raw_response is not None and content)):
            raise ValueError('Scored native answer has undeclared answer fields')
        return
    require_native_projection_schema(declaration)
    if raw_response is None:
        raise ValueError('Declared native answer lacks its retained raw response')
    if present - set(declaration.get('answer_fields', {})):
        raise ValueError('Scored native answer has undeclared answer fields')
    allowed = metadata.get('native_route_receipt', {}).get('answer_fields')
    if allowed is not None:
        def check_source(spec):
            pointer = spec.get('pointer')
            if pointer:
                name = '.'.join(str(key) for key in pointer if not isinstance(key, int))
                allowed_names = [field.replace('[]', '') for field in allowed]
                if not any(name == field or name.startswith(field+'.') for field in allowed_names):
                    raise ValueError('Native projection reads outside its frozen answer fields')
            if 'legacy' in spec:
                check_source(spec['legacy'])
        if declaration.get('source_format') == 'bytes':
            required = ('original_bytes' if declaration.get('files') else
                        'response_body' if 'response_body' in allowed
                        else 'stdout')
            if required not in allowed:
                raise ValueError('Native byte projection differs from its frozen answer fields')
        else:
            if declaration.get('content_format') != 'empty':
                check_source({'pointer': declaration.get('content_pointer')})
            for spec in declaration.get('answer_fields', {}).values():
                check_source(spec)
            if declaration.get('files'):
                check_source(declaration['files'])
        for spec in declaration.get('result_fields', {}).values():
            check_source(spec)
            if spec.get('format') == 'fetch_log_final_url' and 'diagnostic.main_document_url' not in allowed:
                raise ValueError('Native final URL conversion is outside its frozen sources')
    payload = raw_response if declaration.get('source_format') == 'bytes' else json.loads(raw_response)
    projected_result = project_native_result_fields(payload, declaration, request_url=request_url,
        diagnostic=diagnostic, transport_receipt=transport_receipt)
    if projected_result != {'title': title, 'final_url': final_url}:
        raise ValueError('Scored title or final URL differs from its declared native projection')
    if declaration.get('schema_version') == 2:
        for spec in declaration.get('answer_fields', {}).values():
            if spec.get('format') == 'html_attribute_urls' and spec.get('base_url') != (final_url or request_url):
                raise ValueError('Native resource base URL differs from its verified result URL')
    expected, fields = project_native_answer(payload, declaration, historical_scoring_source=historical_scoring_source)
    if content != expected or any(metadata.get(key) != value for key, value in fields.items()):
        raise ValueError('Scored answer differs from its declared native field projection')
    if [item['data'] for item in files] != project_native_files(payload, declaration):
        raise ValueError('Scored native files differ from their declared byte projection')
