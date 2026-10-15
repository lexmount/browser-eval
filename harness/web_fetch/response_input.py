"""Provider-neutral answer envelope. Never infer answers from logs or requests."""
import json
from urllib.parse import urljoin, urlsplit


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


def project_native_answer(payload, declaration):
    """Select declared native fields; never inspect references or URL meaning."""
    if declaration.get('schema_version') != 1:
        raise ValueError('Unsupported native answer projection')
    value = _projection_value(payload, declaration.get('content_pointer'))
    kind = declaration.get('content_format')
    if kind == 'text' and isinstance(value, str):
        content = value
    elif kind == 'json':
        content = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    else:
        raise ValueError('Native answer field differs from its declared type')
    fields = {}
    for name, projection in declaration.get('answer_fields', {}).items():
        if name not in {'links', 'images', 'videos', 'media', 'extras'}:
            raise ValueError('Unknown native answer metadata field')
        selected = _projection_value(payload, projection.get('pointer'))
        if projection.get('format') == 'html_attribute_urls':
            fields[name] = _attribute_record_urls(selected, projection['base_url'])
        elif projection.get('format') == 'identity':
            fields[name] = selected
        else:
            raise ValueError('Unknown native answer field projection')
    return content, fields


def require_native_answer_projection(content, metadata, raw_response):
    declaration = metadata.get('native_answer_projection')
    if declaration is None:
        return
    expected, fields = project_native_answer(json.loads(raw_response), declaration)
    if content != expected or any(metadata.get(key) != value for key, value in fields.items()):
        raise ValueError('Scored answer differs from its declared native field projection')
