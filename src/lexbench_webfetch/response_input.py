"""Provider-neutral answer envelope. Never infer answers from logs or requests."""
import json
import hashlib
import re
from urllib.parse import urljoin, urlsplit, urlunsplit


PAGE_INFORMATION_FIELDS = frozenset({
    'title', 'author', 'description', 'publish_time', 'published_at', 'subtitle', 'lead',
    'source', 'editor', 'site_name', 'copyright', 'statement', 'categories',
    'tags', 'headings', 'section_headings', 'paragraphs', 'table_of_contents', 'breadcrumbs',
})


def page_information_texts(information):
    """Read declared extracted page fields, never recursively mine metadata.

    These independent values can supply unscoped information checks, not body
    coverage, length, records, or ownership of a requested page region.
    """
    if information is None:
        return []
    if not isinstance(information, dict) or set(information) - PAGE_INFORMATION_FIELDS:
        raise ValueError('Invalid extracted page information fields')
    texts = []
    for field, value in information.items():
        if value is None:
            continue
        values = value if isinstance(value, list) else [value]
        if any(not isinstance(text, str) for text in values):
            raise ValueError('Extracted page information must contain text values')
        if field != 'section_headings':
            texts.extend(text for text in values if text.strip())
    return texts


def unscoped_page_information(contract):
    """Flat fields cannot prove a visible control, record, or excluded region."""
    contract = contract or {}
    return (contract.get('schema') in {None, 'body_text', 'page_identity'}
            and contract.get('content_scope') != 'target_content'
            and not any(contract.get(key) for key in ('entry_scope', 'table_scope', 'text_exclusions')))


def join_answer_url(base_url: str, reference: str) -> str:
    """Resolve answer URLs while retaining literal semicolon path data."""
    joined = urljoin(base_url, reference)
    if not base_url or not reference:
        return joined
    base, target = urlsplit(base_url), urlsplit(reference)
    if ';' not in base.path and ';' not in target.path:
        return joined
    # urlparse splits path params before urljoin removes dot segments. Protect
    # literal delimiters so '.;v' stays a filename rather than becoming '.'.
    def protected(parts):
        return urlunsplit(parts._replace(path=parts.path.replace('%', '%25').replace(';', '%3B')))
    resolved = urlsplit(urljoin(protected(base), protected(target)))
    # One pass restores only introduced layers; an original '%3B' was protected
    # as '%253B' and therefore stays encoded rather than turning into ';'.
    path = re.sub(r'%(?:25|3B)', lambda match: '%' if match[0] == '%25' else ';', resolved.path)
    return urlunsplit(resolved._replace(path=path))


def answer_input(artifact, metadata=None, *, ok=True, status_code=None):
    text = artifact.decode('utf-8', errors='replace')
    metadata = metadata or {}
    basis = 'adapter_status'
    # Only adapter-declared, hash-bound raw API bytes may be interpreted as an
    # envelope. Identically shaped JSON can be the actual requested document.
    projection = metadata.get('native_answer_projection')
    raw_envelope = (isinstance(projection, dict) and projection.get('schema_version') == 2
                    and projection.get('content_pointer') == ['result']
                    and projection.get('content_format') == 'text'
                    and metadata.get('raw_response_sha256') == hashlib.sha256(artifact).hexdigest())
    payload = None
    if raw_envelope:
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            pass
    if (status_code == 200 and isinstance(payload, dict)
            and payload.get('success') is True and payload.get('result') == ''):
        text, basis = '', 'http_200_success_envelope_empty_result'
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
    """Annotate declared empty API content without changing adapter status."""
    m = record['metrics']
    if m.get('request_status_basis') == 'http_200_success_envelope_empty_result':
        record.setdefault('provider_metadata', {})['empty_content'] = True


def safe_named_links(links, base_url):
    result = []
    for item in links or []:
        try:
            url = join_answer_url(base_url, item['url'])
            parts = urlsplit(url)
        except (ValueError, KeyError, TypeError):
            continue
        if parts.scheme in {'http', 'https'} and parts.hostname and not parts.username and not parts.password:
            result.append((url, item['title']))
    return result


def answer_format(metadata=None):
    """Read the persisted answer representation, never the origin HTTP MIME."""
    metadata = metadata or {}
    if 'answer_format' not in metadata:
        return None
    value = metadata['answer_format']
    if not isinstance(value, str) or value not in {'plain_text', 'html', 'markdown'}:
        raise ValueError('answer_format must be plain_text, html, or markdown')
    return value
