"""Provider-neutral answer envelope. Never infer answers from logs or requests."""
import json
import re
from urllib.parse import parse_qsl, quote, urljoin, urlsplit

from .safety import validate_http_url


def answer_input(artifact, metadata=None, *, ok=True, status_code=None):
    text = artifact.decode('utf-8', errors='replace')
    metadata = metadata or {}
    basis = 'adapter_status'
    # Some adapters persist an explicit API envelope when extraction is empty.
    # Recognize the envelope by its fields, never by a provider's name.
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        payload = None
    if (status_code == 200 and isinstance(payload, dict)
            and payload.get('success') is True and payload.get('result') == ''):
        ok, text, basis = True, '', 'http_200_success_envelope_empty_result'
    links = []
    # Only explicit name/destination pairs are answer evidence. Bare URLs,
    # request echoes, arbitrary nested metadata and diagnostic logs are not.
    returned = metadata.get('links', [])
    if isinstance(returned, dict):
        returned = [item for key in ('internal', 'external') for item in
                    (returned.get(key) if isinstance(returned.get(key), list) else [])]
    for item in returned if isinstance(returned, list) else []:
        if not isinstance(item, dict):
            continue
        label, target = item.get('text') or item.get('title'), item.get('href') or item.get('url')
        if isinstance(label, str) and isinstance(target, str) and label.strip():
            links.append({'title': label, 'url': target})
    return text, links, bool(ok), basis


def apply_request_status(record):
    """Persist one normalized status, retaining the original adapter fields."""
    m = record['metrics']
    if m.get('request_status_basis') == 'http_200_success_envelope_empty_result':
        record.setdefault('raw_request_status', {k: record.get(k) for k in
                          ('ok', 'error_type', 'error_message')})
        record.update(ok=True, error_type=None, error_message=None)
        record['provider_metadata']['empty_content'] = True


def safe_named_links(links, base_url):
    result = []
    for item in links or []:
        try:
            url = urljoin(base_url, item['url'])
            parts = urlsplit(url)
        except (ValueError, KeyError, TypeError):
            continue
        if parts.scheme in {'http', 'https'} and parts.hostname and not parts.username and not parts.password:
            result.append((url, item['title']))
    return result


def _projection_value(payload, pointer):
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


def _attribute_record_urls(groups, base_url):
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
                values = ([part.strip().split()[0] for part in value.split(',') if part.strip()]
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
    try:
        return _projection_value(payload, spec['pointer'])
    except ValueError:
        if 'default' not in spec:
            raise
        return spec['default']


def _project_field(payload, spec):
    value = _selected(payload, spec)
    kind = spec.get('format', 'identity')
    if kind == 'identity':
        return value
    if kind == 'html_attribute_urls':
        return _attribute_record_urls(value, spec['base_url'])
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


def project_native_answer(payload, declaration):
    """Select declared native fields; never inspect references or URL meaning."""
    if declaration.get('schema_version') not in {1, 2}:
        raise ValueError('Unsupported native answer projection')
    kind = declaration.get('content_format')
    if declaration.get('source_format') == 'bytes':
        if declaration.get('answer_fields'):
            raise ValueError('Native byte projection cannot declare structured answer fields')
        if not isinstance(payload, bytes):
            raise ValueError('Native byte projection requires bytes')
        if kind == 'utf8':
            return payload.decode('utf-8'), {}
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
        fields[name] = _project_field(payload, spec)
    return content, fields


def project_native_files(payload, declaration):
    """Decode native delivered bytes; filenames and logs cannot supply answers."""
    import base64
    spec = declaration.get('files')
    if spec is None:
        return []
    value = _selected(payload, spec) if spec.get('format') != 'raw_bytes' else payload
    if spec['format'] == 'raw_bytes' and isinstance(payload, bytes):
        return [payload] if payload else []
    if spec['format'] == 'base64_file':
        return [base64.b64decode(value, validate=True)] if value else []
    if spec['format'] == 'worker_files':
        return [base64.b64decode(item['base64'], validate=True) for item in value]
    raise ValueError('Unknown native file projection')


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
            value = _selected(payload, {**spec, 'default': None})
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
                                     request_url=None, diagnostic=None, transport_receipt=None):
    """Reject every undeclared score-bearing input, then check exact provenance."""
    declaration = metadata.get('native_answer_projection')
    present = {key for key in SCORE_ANSWER_FIELDS if metadata.get(key)}
    if declaration is None:
        if (present or files or title is not None
                or (final_url is not None and final_url != request_url)
                or (raw_response is not None and content)):
            raise ValueError('Scored native answer has undeclared answer fields')
        return
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
            required = 'original_bytes' if declaration.get('files') else 'stdout'
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
    expected, fields = project_native_answer(payload, declaration)
    if content != expected or any(metadata.get(key) != value for key, value in fields.items()):
        raise ValueError('Scored answer differs from its declared native field projection')
    if [item['data'] for item in files] != project_native_files(payload, declaration):
        raise ValueError('Scored native files differ from their declared byte projection')


def verify_saved_native_answer(root, record):
    """Validate newly declared saved answers at aggregation/release boundaries.

    Older calls with no declaration need a separately recorded historical audit;
    their missing acquisition declarations must never be invented in place.
    """
    from .publication_privacy import read_evidence_binding
    from .safety import contained_path
    metadata = record.get('provider_metadata', {})
    declaration = metadata.get('native_answer_projection')
    if not declaration:
        if metadata.get('native_answer_required'):
            raise ValueError('Scored native answer has no projection declaration')
        return
    answer = read_evidence_binding(root, {'path': record['artifact_path'],
        'sha256': record['metrics']['content_sha256']}).read_bytes()
    # Deadlines discard answers while preserving late raw evidence.
    if not record['ok'] and record.get('error_type') == 'timeout' and not answer:
        if any(metadata.get(key) for key in SCORE_ANSWER_FIELDS) or metadata.get('file_evaluation', {}).get('files'):
            raise ValueError('Discarded late native answer retains score-bearing fields')
        return
    raw_path = metadata.get('raw_stdout_path') if declaration.get('source_format') == 'bytes' else metadata.get('raw_response_path')
    raw_hash = metadata.get('raw_stdout_sha256') if declaration.get('source_format') == 'bytes' else metadata.get('raw_response_sha256')
    if not raw_path or not raw_hash:
        raise ValueError('Declared native answer lacks its retained raw response')
    raw = read_evidence_binding(root, {'path': raw_path, 'sha256': raw_hash}).read_bytes()
    files = [{'data': contained_path(root, item['artifact_path'], reject_symlinks=True).read_bytes()}
             for item in metadata.get('file_evaluation', {}).get('files', [])]
    diagnostic = None
    if any(spec.get('format') == 'fetch_log_final_url'
           for spec in declaration.get('result_fields', {}).values()):
        diagnostic = read_evidence_binding(root, {'path': metadata['diagnostic_log_path'],
            'sha256': metadata['diagnostic_log_sha256']}).read_bytes()
    require_native_answer_projection(answer.decode('utf-8'), metadata, raw, files=files,
        title=record.get('title') if declaration.get('schema_version') == 2 or 'result_fields' in declaration else None,
        final_url=record.get('final_url') if declaration.get('schema_version') == 2 or 'result_fields' in declaration else None,
        request_url=record.get('url'),
        diagnostic=diagnostic, transport_receipt=metadata.get('source_transport_receipt'))
    if files or 'file_evaluation' in metadata:
        from .call_boundary import evaluate_returned_files
        source_verification = {}
        dataset_path = root/'dataset.json'
        if dataset_path.is_file():
            cases = json.loads(dataset_path.read_bytes()).get('cases', [])
            case = next((case for case in cases if case.get('id') == record.get('case_id')), None)
            if case is None:
                raise ValueError('Native file result lacks its frozen case')
            source_verification = case.get('source_verification') or {}
        expected = evaluate_returned_files(files, source_verification)
        saved = metadata.get('file_evaluation', {})
        compared = {**saved, 'files': [{k: v for k, v in item.items() if k != 'artifact_path'}
                                     for item in saved.get('files', [])]}
        if compared != expected:
            raise ValueError('Native file evaluation differs from its retained bytes')
