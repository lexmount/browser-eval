"""Native and diagnostic provider modes keep their distinct public behavior."""
import json
from unittest.mock import patch

import pytest

from lexbench_webfetch.models import EvalCase
from harness.web_fetch.providers.base import build_provider
from harness.web_fetch.providers.http_client import HttpResponse


@pytest.mark.parametrize('provider_type', ['cloudflare_markdown', 'cloudflare_content'])
@pytest.mark.parametrize('native', [False, True])
def test_cloudflare_mode_controls_only_declared_service_deadlines(provider_type, native):
    provider = build_provider('cloudflare', {'type': provider_type,
        'uniform_native_parameters': native, 'api_token': 'fixture', 'account_id': 'fixture'})
    case = EvalCase('a', 'A', 'https://example.invalid/', 'fixture', [],
                    source_benchmark={'input_media_kind': 'html_page'})
    response = HttpResponse(200, case.url, {}, json.dumps({
        'success': True, 'result': 'same response', 'meta': {'finalUrl': case.url}}).encode())
    with patch('harness.web_fetch.providers.cloudflare.request', return_value=response) as transport:
        result = provider.fetch(case, 17)
    body = transport.call_args.kwargs['json_body']
    assert result.ok and result.content == 'same response'
    assert body['url'] == case.url
    assert body['gotoOptions']['waitUntil'] == 'networkidle2'
    if native:
        assert 'timeout' not in body['gotoOptions'] and 'actionTimeout' not in body
        assert result.metadata['service_timeout_ms'] is None
    else:
        assert body['gotoOptions']['timeout'] == body['actionTimeout']
        assert result.metadata['service_timeout_ms'] == body['actionTimeout']


def test_native_cloudflare_cannot_enter_diagnostic_document_conversion():
    provider = build_provider('cloudflare', {'type': 'cloudflare_markdown',
        'uniform_native_parameters': True, 'api_token': 'fixture', 'account_id': 'fixture'})
    case = EvalCase('pdf', 'PDF', 'https://example.invalid/paper.pdf', 'fixture', [],
                    track='document', source_benchmark={'input_media_kind': 'direct_pdf'})
    response = HttpResponse(200, case.url, {}, b'{"success":true,"result":"text"}')
    with patch('harness.web_fetch.providers.cloudflare._get_document',
               side_effect=AssertionError('native mode entered document conversion')), \
         patch('harness.web_fetch.providers.cloudflare.request', return_value=response) as transport:
        result = provider.fetch(case, 17)
    assert result.ok
    assert transport.call_args.args[0].endswith('/markdown')


@pytest.mark.parametrize('provider_type', ['cloudflare_markdown', 'cloudflare_content', 'moli_webfetch'])
@pytest.mark.parametrize('native', [False, True])
def test_initialized_mode_cannot_be_changed_before_transport(provider_type, native):
    provider = build_provider('method', {'type': provider_type, 'uniform_native_parameters': native})
    provider.config['uniform_native_parameters'] = not native
    case = EvalCase('a', 'A', 'https://example.invalid/', 'fixture', [],
                    source_benchmark={'input_media_kind': 'html_page'})
    with pytest.raises(ValueError, match='mode'):
        provider.fetch(case, 17)
