"""Deterministic native answer selection, bound before a cohort starts.

Branches depend only on response structure and transport outcome. Keeping their
implementation in one owner lets acquisition and saved-evidence checks replay
the same policy rather than accepting any later declaration of allowed fields.
"""
import base64
import hashlib
from pathlib import Path


def native_projection_source():
    return Path(__file__).read_bytes()


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
                                           'base_url': meta.get('finalUrl') or get('url')}} if success else {}}
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
