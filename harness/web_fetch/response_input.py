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
