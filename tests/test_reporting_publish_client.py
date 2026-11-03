"""Each publisher keeps its own wire behaviour through the one shared Artifact Site client."""

import email.message
import hashlib
import io
import json
import stat
import urllib.request
import zipfile
from unittest import mock
from urllib.error import HTTPError

import pytest

from harness.browser_use import report_publish as browser_use
from harness.record_io import json_bytes
from harness.reporting import publication
from harness.reporting.publication import PublicationClient
from harness.trajectory_replay import webarena_report_publish as replay
from harness.web_fetch import report_publish as web_fetch
from harness.web_fetch import wcxb_publication as wcxb
from tests import test_wcxb_publication as wcxb_tests
from tests.test_webfetch_report_publish import Service
from tests.test_webfetch_report_publish import fixture as web_fetch_release

BASE = 'https://artifacts.example.invalid'
BEARER = ('Authorization', 'Bearer ahp_fixture')
JSON_BODY = ('Content-type', 'application/json')
FILE_BODY = ('Content-type', 'application/octet-stream')
wcxb_report = wcxb_tests.report


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setattr(publication, 'load_token', lambda *, base_url: 'ahp_fixture')

    class Wire(list):
        respond = staticmethod(lambda request: b'{}')

    calls = Wire()

    class Response(io.BytesIO):
        status = 200
        headers = email.message.Message()

    def urlopen(request, *, timeout):
        calls.append((request, timeout))
        return Response(calls.respond(request))

    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    return calls


def sent(wire, base=BASE):
    """Each physical request as the service would see it, in the order it was sent."""
    return [(request.get_method(), request.full_url.removeprefix(base), request.header_items(), timeout)
            for request, timeout in wire]


@pytest.mark.parametrize(('client', 'headers', 'post', 'put'), [
    (web_fetch.CLIENT,
     [BEARER, ('User-agent', 'WebFetch-Report-Publisher/1.0')], 240, 90),
    (web_fetch.RENAME_CLIENT,
     [BEARER], 30, 30),
    (browser_use.CLIENT,
     [BEARER, ('User-agent', 'Browser-Use-Report-Publisher/1.0')], 240, 240),
    (replay.CLIENT,
     [('User-agent', 'Trajectory-Replay-Artifact-Site-Publisher/1.0'), BEARER], 240, 1_200),
    (wcxb.CLIENT,
     [('User-agent', 'WCXB-Report-Publisher/1.0'), BEARER], 240, 240),
], ids=['web-fetch', 'web-fetch-rename', 'browser-use', 'replay', 'wcxb'])
def test_each_track_keeps_its_identity_header_order_and_timeouts(wire, client, headers, post, put):
    client.request(BASE, '/api/sites', b'{}', 'application/json', extra_headers={'Idempotency-Key': 'key'})
    client.request(BASE, '/api/uploads/one/files/a', b'x', method='PUT')
    (posted, posted_timeout), (uploaded, put_timeout) = wire
    assert posted.full_url == BASE + '/api/sites' and posted.get_method() == 'POST'
    assert posted.data == b'{}'
    assert posted.header_items() == [*headers, ('Content-type', 'application/json'), ('Idempotency-key', 'key')]
    assert uploaded.get_method() == 'PUT' and uploaded.header_items() == headers
    assert (posted_timeout, put_timeout) == (post, put)


def test_public_requests_never_load_a_credential(monkeypatch, wire):
    monkeypatch.setattr(publication, 'load_token', lambda **kwargs: pytest.fail('loaded a credential'))
    browser_use.PUBLIC_CLIENT.request(BASE, '/api/preview/site', authenticated=False,
                                      extra_headers={'X-Artifact-Share': 'share'})
    replay.CLIENT.request(BASE, '/v/share', authenticated=False)
    (preview, preview_timeout), (shared, shared_timeout) = wire
    assert preview.header_items() == [('User-agent', 'Browser-Use-Report-Publisher/1.0'),
                                      ('X-artifact-share', 'share')]
    assert shared.header_items() == [('User-agent', 'Trajectory-Replay-Artifact-Site-Publisher/1.0')]
    assert (preview_timeout, shared_timeout) == (60, 240)


def http_failure(*args, **kwargs):
    headers = email.message.Message()
    headers['Retry-After'] = '7'
    raise HTTPError(BASE, 429, 'busy', headers, io.BytesIO(b'{"error":"busy"}'))


@pytest.mark.parametrize('client', [browser_use.CLIENT, replay.CLIENT])
def test_status_decoding_publishers_receive_http_failures_as_values(monkeypatch, wire, client):
    monkeypatch.setattr(urllib.request, 'urlopen', http_failure)
    assert client.request(BASE, '/api/sites') == (429, b'{"error":"busy"}', {'Retry-After': '7'})


@pytest.mark.parametrize('client', [web_fetch.CLIENT, web_fetch.RENAME_CLIENT, wcxb.CLIENT,
                                    browser_use.PUBLIC_CLIENT])
def test_retrying_publishers_keep_the_raised_http_failure(monkeypatch, wire, client):
    monkeypatch.setattr(urllib.request, 'urlopen', http_failure)
    with pytest.raises(HTTPError) as failure:
        client.request(BASE, '/api/sites')
    assert failure.value.code == 429
    assert failure.value.headers['Retry-After'] == '7'
    failure.value.close()


def test_response_headers_are_returned_to_the_adapter(monkeypatch):
    monkeypatch.setattr(publication, 'load_token', lambda *, base_url: 'ahp_fixture')

    class Response(io.BytesIO):
        status = 201
        headers = {'X-Version': 'ver_one'}

    monkeypatch.setattr(urllib.request, 'urlopen', lambda request, *, timeout: Response(b'body'))
    response = PublicationClient('Agent/1.0').request(BASE, '/api/sites')
    assert (response.status, response.body, response.headers) == (201, b'body', {'X-Version': 'ver_one'})


# Boundaries come from the implementations these adapters replaced: a retried in-flight
# operation must keep replaying the same bytes.
@pytest.mark.parametrize(('build', 'boundary', 'filename'), [
    (lambda archive: browser_use.multipart_archive(archive=archive, title='标题'),
     '----browseruseabf65d3350ce72216025275027825d1d', 'browser-use.zip'),
    (lambda archive: replay.multipart_archive('标题', archive=archive),
     '----trajectoryreplayabf65d3350ce72216025275027825d1d', 'browser-trajectory-replay.zip'),
    (lambda archive: web_fetch.multipart(archive, '标题', 'digest'),
     '----webfetch54b5c315cbb7bcef4c210295f009cf95', 'webfetch.zip'),
], ids=['browser-use', 'replay', 'web-fetch'])
def test_multipart_body_is_exact(tmp_path, build, boundary, filename):
    archive = tmp_path / 'release.zip'
    archive.write_bytes(b'PK-bytes')
    body, content_type = build(archive)
    assert content_type == 'multipart/form-data; boundary=' + boundary
    assert body == (
        f'--{boundary}\r\nContent-Disposition: form-data; name="mode"\r\n\r\nzip\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="title"\r\n\r\n标题\r\n'
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        'Content-Type: application/zip\r\n\r\nPK-bytes\r\n'
        f'--{boundary}--\r\n'
    ).encode()


@pytest.mark.parametrize(('status', 'raw', 'message'), [
    (500, b' upstream broke ', 'publish failed with HTTP 500: upstream broke'),
    (502, b'', 'publish failed with HTTP 502: empty response'),
    (200, b'not json', 'publish returned HTTP 200 without a valid JSON response'),
    (200, b'[1]', 'publish returned a non-object JSON response'),
])
def test_response_json_keeps_the_failure_and_rejects_non_objects(status, raw, message):
    with pytest.raises(RuntimeError) as failure:
        publication.response_json(status, raw, operation='publish')
    assert str(failure.value) == message


def test_receipt_is_private_and_drops_service_credentials_at_every_depth(tmp_path):
    receipt = tmp_path / 'receipt.json'
    payload = publication.write_receipt(receipt, {
        'slug': 'site', 'editToken': 'secret', 'claimToken': 'secret',
        'last_update': {'token': 'secret', 'versions': [{'id': 'ver_one', 'token': 'secret'}]},
    })
    assert receipt.read_bytes() == payload
    assert json.loads(payload) == {'slug': 'site', 'last_update': {'versions': [{'id': 'ver_one'}]}}
    assert stat.S_IMODE(receipt.stat().st_mode) == 0o600


@pytest.mark.parametrize('module', [browser_use, replay], ids=['browser-use', 'replay'])
def test_track_receipts_use_the_shared_writer(tmp_path, monkeypatch, module):
    monkeypatch.setattr(module, 'RECEIPT', tmp_path / 'receipt.json')
    module.save_receipt({'slug': 's', 'token': 't'})
    assert json.loads((tmp_path / 'receipt.json').read_bytes()) == {'slug': 's'}


def test_tree_digests_keep_each_tracks_frozen_serialization(tmp_path):
    # Attestations and visual receipts bind these digests; values come from the replaced copies.
    from harness.browser_use.report_package import tree_sha256 as browser_use_package
    from harness.trajectory_replay.webarena_report_package import tree_sha256 as replay_package

    (tmp_path / 'nested').mkdir()
    (tmp_path / 'index.html').write_bytes(b'home')
    (tmp_path / 'nested/a.html').write_bytes(b'detail')
    framed = 'ce9b74b2210a33c186bb7bfbaa40331dba1e69411d41b4728fca0651ce900d1d'
    unframed = '542ae38f7668451bfbabd2a058ec763c401dd0c265923dcc1cf7064e806f708e'
    assert browser_use.tree_sha256(tmp_path) == browser_use_package(tmp_path) == framed
    assert replay.tree_sha256(tmp_path) == replay_package(tmp_path) == unframed


# The adapters below run their real publish path; only urlopen is faked.
def test_browser_use_request_uses_the_authenticated_status_decoding_client(wire):
    agent = [BEARER, ('User-agent', 'Browser-Use-Report-Publisher/1.0')]
    browser_use.request('/api/sites', b'{}', 'application/json', base_url=BASE,
                        extra_headers={'Idempotency-Key': 'key'})
    browser_use.request('/api/sites/one', b'{}', 'application/json', method='PATCH', base_url=BASE)
    assert sent(wire) == [
        ('POST', '/api/sites', [*agent, JSON_BODY, ('Idempotency-key', 'key')], 240),
        ('PATCH', '/api/sites/one', [*agent, JSON_BODY], 240),
    ]
    assert [request.data for request, _ in wire] == [b'{}', b'{}']


def test_replay_request_forwards_identity_key_and_public_access(wire):
    agent = ('User-agent', 'Trajectory-Replay-Artifact-Site-Publisher/1.0')
    replay.request(BASE, '/api/sites', b'{}', 'application/json', extra_headers={'Idempotency-Key': 'key'})
    replay.request(BASE, '/api/uploads/one/files/a', b'x', 'application/octet-stream', method='PUT')
    replay.request(BASE, '/v/share', authenticated=False)
    replay.request(BASE, '/api/preview/site', authenticated=False, extra_headers={'X-Artifact-Share': 'share'})
    assert sent(wire) == [
        ('POST', '/api/sites', [agent, BEARER, JSON_BODY, ('Idempotency-key', 'key')], 240),
        ('PUT', '/api/uploads/one/files/a', [agent, BEARER, FILE_BODY], 1_200),
        ('GET', '/v/share', [agent], 240),
        ('GET', '/api/preview/site', [agent, ('X-artifact-share', 'share')], 240),
    ]


def serve(handler):
    def respond(request):
        status, raw = handler(request)
        assert status == 200, request.selector
        return raw
    return respond


def lifecycle_site(monkeypatch, tmp_path, owner, marker):
    """Point a track at a new release and answer every request of its create flow."""
    archive = tmp_path / 'archive.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('index.html', marker)
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'publication': {'base_url': BASE, 'title': 'Title'}}))
    attestation = tmp_path / 'attestation.json'
    attestation.write_text('{}')
    for name, value in (('ARCHIVE', archive), ('PROFILE', profile), ('ATTESTATION', attestation),
                        ('RECEIPT', tmp_path / 'receipt.json'),
                        ('BINDING_JOURNAL', tmp_path / 'binding.json'),
                        ('OPERATION_JOURNAL', tmp_path / 'operation.json')):
        monkeypatch.setattr(owner, name, value)
    monkeypatch.setattr(owner, 'validate_package', lambda: {})

    def respond(request):
        path = request.selector
        if path == '/api/sites':
            return b'{"slug":"site-1","versionId":"ver_new"}'
        if path.endswith('/shares'):
            return json.dumps({'url': BASE + '/v/token'}).encode()
        if path.endswith('/export'):
            return archive.read_bytes()
        if path.startswith('/api/preview/'):
            return marker.encode()
        if path == '/api/sites/site-1':
            return b'{"site":{"currentVersionId":"ver_new"}}'
        assert path == '/v/token'
        return b'<!doctype html>'

    return archive, respond


def test_browser_use_publish_keeps_authenticated_and_public_share_requests_apart(tmp_path, monkeypatch, wire):
    archive, wire.respond = lifecycle_site(monkeypatch, tmp_path, browser_use, browser_use.PUBLIC_REPORT_MARKER)
    browser_use.main(create=True)
    agent = ('User-agent', 'Browser-Use-Report-Publisher/1.0')
    private = [BEARER, agent]
    upload_type = browser_use.multipart_archive(archive=archive, title='Title')[1]
    assert sent(wire) == [
        ('POST', '/api/sites', [*private, ('Content-type', upload_type), ('Idempotency-key', mock.ANY)], 240),
        ('GET', '/api/sites/site-1', private, 240),
        ('PATCH', '/api/sites/site-1', [*private, JSON_BODY], 240),
        ('POST', '/api/sites/site-1/shares', [*private, JSON_BODY], 240),
        ('GET', '/api/sites/site-1/export', private, 240),
        ('GET', '/v/token', [agent], 60),
        ('GET', '/api/preview/site-1', [agent, ('X-artifact-share', 'token')], 60),
        ('GET', '/api/sites/site-1', private, 240),
    ]
    assert wire[0][0].get_header('Idempotency-key').startswith('browser-use-')


def test_replay_publish_keeps_authenticated_and_public_share_requests_apart(tmp_path, monkeypatch, wire):
    archive, wire.respond = lifecycle_site(monkeypatch, tmp_path, replay, replay.PUBLIC_REPORT_MARKERS[0])
    replay.main(create=True)
    agent = ('User-agent', 'Trajectory-Replay-Artifact-Site-Publisher/1.0')
    private = [agent, BEARER]
    upload_type = replay.multipart_archive('Title', archive=archive)[1]
    assert sent(wire) == [
        ('POST', '/api/sites', [*private, ('Content-type', upload_type), ('Idempotency-key', mock.ANY)], 240),
        ('POST', '/api/sites/site-1/shares', [*private, JSON_BODY], 240),
        ('GET', '/api/sites/site-1', private, 240),
        ('GET', '/api/sites/site-1/export', private, 240),
        ('GET', '/v/token', [agent], 240),
        ('GET', '/api/preview/site-1', [agent, ('X-artifact-share', 'token')], 240),
        ('GET', '/api/sites/site-1', private, 240),
    ]


def behind_wire(service):
    """Serve urlopen requests from the Web Fetch publication fake."""
    def handler(request):
        key = request.get_header('Idempotency-key')
        return service(request.selector, request.data, request.get_header('Content-type'),
                       method=getattr(request, 'method', None), extra_headers={'Idempotency-Key': key} if key else None)
    return serve(handler)


WEB_FETCH = [BEARER, ('User-agent', 'WebFetch-Report-Publisher/1.0')]
WEB_FETCH_BASE = 'https://reports.example'
WEB_FETCH_SHARE = ('POST', '/api/sites/site-one/shares', [*WEB_FETCH, JSON_BODY], 240)


def test_web_fetch_single_request_publish_keeps_its_wire_contract(tmp_path, monkeypatch, wire):
    config = web_fetch_release(tmp_path)
    monkeypatch.setattr(web_fetch, 'STAGED_UPLOAD_THRESHOLD', 10**9)
    wire.respond = behind_wire(Service())
    web_fetch.publish(config)
    receipt = json.loads(config.receipt.read_bytes())
    upload_type = web_fetch.multipart(config.archive, config.title, receipt['archive_sha256'])[1]
    assert sent(wire, WEB_FETCH_BASE) == [
        ('POST', '/api/sites/site-one/versions?expected_version=ver_old',
         [*WEB_FETCH, ('Content-type', upload_type), ('Idempotency-key', receipt['operation_key'])], 240),
        WEB_FETCH_SHARE,
    ]


def test_web_fetch_staged_publish_keeps_its_wire_contract(tmp_path, monkeypatch, wire):
    config = web_fetch_release(tmp_path)
    monkeypatch.setattr(web_fetch, 'STAGED_UPLOAD_THRESHOLD', 0)
    wire.respond = behind_wire(Service())
    web_fetch.publish(config)
    key = json.loads(config.receipt.read_bytes())['operation_key']
    lookup = ('GET', '/api/sites/site-one', WEB_FETCH, 240)
    with zipfile.ZipFile(config.archive) as bundle:
        files = sorted(set(bundle.namelist()) - {'index.html'})
    assert files
    put = [('PUT', '/api/uploads/ver_staging/files/' + name, [*WEB_FETCH, FILE_BODY], 90)
           for name in ('index.html', *files)]
    requests = sent(wire, WEB_FETCH_BASE)
    # The root page is staged first; the remaining files race through the thread pool.
    requests[3:len(put) + 2] = sorted(requests[3:len(put) + 2])
    assert requests == [
        lookup,
        ('POST', '/api/uploads?expected_version=ver_old',
         [*WEB_FETCH, JSON_BODY, ('Idempotency-key', key + ':start')], 240),
        *put,
        lookup,
        ('POST', '/api/uploads/ver_staging/commit?expected_version=ver_old',
         [*WEB_FETCH, JSON_BODY, ('Idempotency-key', key)], 240),
        WEB_FETCH_SHARE,
    ]


def test_wcxb_publish_keeps_its_wire_contract_and_a_public_share_check(wcxb_report, wire):
    kwargs, profile_path, _ = wcxb_report
    site = wcxb_tests.Site()

    def handler(request):
        return site(request.selector, request.data, request.get_header('Content-type'),
                    auth=request.has_header('Authorization'), method=getattr(request, 'method', None),
                    headers={name.title(): value for name, value in request.header_items()})

    wire.respond = serve(handler)
    wcxb.publish(**kwargs, profile_path=profile_path)
    agent = ('User-agent', 'WCXB-Report-Publisher/1.0')
    private = [agent, BEARER]
    journal = json.loads(next(kwargs['work'].glob('wcxb-update-*.json')).read_bytes())
    operation = hashlib.sha256(json_bytes(
        {name: journal[name] for name in ('base_url', 'slug', 'archive_sha256')})).hexdigest()[:32]
    lookup = ('GET', '/api/sites/wcxb-site', private, 240)
    assert sent(wire) == [
        lookup,
        lookup,
        ('POST', '/api/uploads?expected_version=ver_old',
         [*private, JSON_BODY, ('Idempotency-key', 'wcxb-start-' + operation)], 240),
        ('PUT', '/api/uploads/stage_one/files/index.html', [*private, FILE_BODY], 240),
        ('PUT', '/api/uploads/stage_one/files/' + wcxb.INVENTORY, [*private, FILE_BODY], 240),
        lookup,
        ('POST', '/api/uploads/stage_one/commit?expected_version=ver_old',
         [*private, JSON_BODY, ('Idempotency-key', 'wcxb-commit-' + operation)], 240),
        lookup,
        ('GET', '/api/sites/wcxb-site/export', private, 240),
        ('GET', '/v/public-token', [agent], 240),
        ('PATCH', '/api/sites/wcxb-site', [*private, JSON_BODY], 240),
    ]


def test_rename_keeps_its_identity_free_request_and_short_timeout(tmp_path, monkeypatch, wire):
    monkeypatch.delenv('ARTIFACT_SITE_BASE_URL', raising=False)
    receipt = tmp_path / 'receipt.json'
    receipt.write_text(json.dumps({'slug': 'site-one', 'base_url': BASE}))
    wire.respond = lambda request: b'{"slug":"site-one","title":"after"}'
    web_fetch.rename_main([str(receipt), '--title', 'after'])
    assert sent(wire) == [('PATCH', '/api/sites/site-one', [BEARER, JSON_BODY], 30)]
    assert wire[0][0].data == b'{"title": "after"}'
