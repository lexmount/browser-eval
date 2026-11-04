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
from harness.record_io import file_sha256, json_bytes
from harness.reporting import publication, publish
from harness.reporting.publication import PublicationClient
from harness.trajectory_replay import webarena_report_publish as replay
from harness.trajectory_replay.public_webarena import publication as public_webarena
from harness.web_fetch import report_publish as web_fetch
from harness.web_fetch import wcxb_publication as wcxb
from harness.web_search import publication as web_search
from tests import test_wcxb_publication as wcxb_tests
from tests.test_webfetch_report_publish import Service
from tests.test_webfetch_report_publish import fixture as web_fetch_release
from tests.trajectory_replay.public_webarena.test_trajectory_release import (
    publication_fixture as public_webarena_release,
)
from tests.web_search.test_publication import publication_files as web_search_release

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
        reply = calls.respond(request)
        status, raw = reply if isinstance(reply, tuple) else (200, reply)
        response = Response(raw)
        response.status = status
        return response

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
    (web_search.CLIENT,
     [BEARER, ('User-agent', 'Web-Search-Report-Publisher/1.0')], 240, 90),
    (public_webarena.CLIENT,
     [BEARER, ('User-agent', 'Public-WebArena-Report-Publisher/1.0')], 240, 90),
], ids=['web-fetch', 'web-fetch-rename', 'browser-use', 'replay', 'wcxb', 'web-search', 'public-webarena'])
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
    (lambda archive: publish.release_multipart(archive, '标题', 'digest', 'webfetch'),
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


BOUND_TRACKS = pytest.mark.parametrize(('owner', 'marker'), [
    (browser_use, browser_use.PUBLIC_REPORT_MARKER),
    (replay, replay.PUBLIC_REPORT_MARKERS[0]),
], ids=['browser-use', 'replay'])


def lifecycle_site(monkeypatch, tmp_path, owner, marker, base=BASE, *, uploaded=None, active='ver_new'):
    """Point a track at a new release and answer every request of its publish flow.

    An upload answers with ``uploaded`` and makes ``ver_new`` the active remote version.
    """
    archive = tmp_path / 'archive.zip'
    with zipfile.ZipFile(archive, 'w') as bundle:
        bundle.writestr('index.html', marker)
    profile = tmp_path / 'profile.json'
    profile.write_text(json.dumps({'publication': {'base_url': base, 'title': 'Title'}}), encoding='utf-8')
    attestation = tmp_path / 'attestation.json'
    attestation.write_text('{}', encoding='utf-8')
    for name, value in (('ARCHIVE', archive), ('PROFILE', profile), ('ATTESTATION', attestation),
                        ('RECEIPT', tmp_path / 'receipt.json'),
                        ('BINDING_JOURNAL', tmp_path / 'binding.json'),
                        ('OPERATION_JOURNAL', tmp_path / 'operation.json')):
        monkeypatch.setattr(owner, name, value)
    monkeypatch.setattr(owner, 'validate_package', lambda: {})

    def respond(request):
        nonlocal active
        path = request.selector
        if path == '/api/sites' or '/versions?' in path:
            active = 'ver_new'
            return json.dumps({'slug': 'site-1', 'versionId': 'ver_new', **(uploaded or {})}).encode()
        if path.endswith('/shares'):
            return json.dumps({'url': base + '/v/token'}).encode()
        if path.endswith('/export'):
            return archive.read_bytes()
        if path.startswith('/api/preview/'):
            return marker.encode()
        if path == '/api/sites/site-1':
            return json.dumps({'site': {'currentVersionId': active}}).encode()
        assert path.startswith('/v/')
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
        ('POST', '/api/sites/site-1/shares', [*private, JSON_BODY], 240),
        ('GET', '/api/sites/site-1', private, 240),
        ('PATCH', '/api/sites/site-1', [*private, JSON_BODY], 240),
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


@BOUND_TRACKS
def test_bound_track_binds_its_own_target_and_only_asks_for_that_hosts_token(
        tmp_path, monkeypatch, wire, owner, marker):
    base = 'https://own-target.example.invalid'
    _, wire.respond = lifecycle_site(monkeypatch, tmp_path, owner, marker, base=base)
    tokens = []
    monkeypatch.setattr(publication, 'load_token', lambda *, base_url: tokens.append(base_url) or 'ahp_fixture')
    owner.main(create=True)
    assert all(request.full_url.startswith(base + '/') for request, _ in wire)
    assert set(tokens) == {base}
    bound = json.loads(owner.PROFILE.read_bytes())['publication']
    assert (bound['base_url'], bound['site_slug'], bound['expected_version']) == (base, 'site-1', 'ver_new')


@BOUND_TRACKS
def test_receipt_of_another_site_stops_a_bound_track_before_any_request(
        tmp_path, monkeypatch, wire, owner, marker):
    lifecycle_site(monkeypatch, tmp_path, owner, marker)
    owner.PROFILE.write_text(json.dumps({'publication': {
        'base_url': BASE, 'site_slug': 'site-1', 'expected_version': 'ver_old',
    }}), encoding='utf-8')
    owner.RECEIPT.write_text(json.dumps({'slug': 'another-report'}), encoding='utf-8')
    with pytest.raises(ValueError, match='differs from the bound'):
        owner.main(create=False)
    assert not wire


SERVICE_RESPONSE = {
    'editToken': 'edit-secret', 'claimToken': 'claim-secret', 'createdAt': 'kept',
    'metadata': {'editToken': 'edit-secret', 'owner': 'kept'},
}


def assert_no_service_token_and_response_kept(*raw_receipts):
    for raw in raw_receipts:
        assert b'secret' not in raw and b'Token' not in raw
        receipt = json.loads(raw)
        assert receipt['createdAt'] == 'kept' and receipt['metadata'] == {'owner': 'kept'}


@BOUND_TRACKS
def test_bound_create_keeps_tokens_out_of_the_receipt_in_every_state(tmp_path, monkeypatch, wire, owner, marker):
    _, site = lifecycle_site(monkeypatch, tmp_path, owner, marker, uploaded=SERVICE_RESPONSE)
    states = []

    def respond(request):
        if request.selector.endswith('/shares'):
            states.append(owner.RECEIPT.read_bytes())
        return site(request)

    bind = owner.bind_publication_files

    def observed_bind(*args, **kwargs):
        states.append(owner.RECEIPT.read_bytes())
        return bind(*args, **kwargs)

    wire.respond = respond
    monkeypatch.setattr(owner, 'bind_publication_files', observed_bind)
    owner.main(create=True)
    states.append(owner.RECEIPT.read_bytes())
    pending, binding, final = map(json.loads, states)
    assert pending['verified'] is False and 'phase' not in pending
    assert binding['verified'] is False and binding['phase'] == 'binding'
    assert final['verified'] is True and 'phase' not in final
    assert_no_service_token_and_response_kept(*states)


VERSION_FIELDS = {browser_use: ('versionId', 'version'), replay: ('version', 'versionId')}


def bound_profile(owner, **fields):
    owner.PROFILE.write_text(json.dumps({'publication': {
        'base_url': BASE, 'site_slug': 'site-1', 'expected_version': 'ver_old', **fields,
    }}), encoding='utf-8')


def saved_receipt(owner, archive, **fields):
    owner.RECEIPT.write_text(json.dumps({
        'base_url': BASE, 'slug': 'site-1', 'archive_sha256': file_sha256(archive), **fields,
    }), encoding='utf-8')


def requested(wire, method):
    return [request.selector for request, _ in wire if request.get_method() == method]


def bound(owner):
    return json.loads(owner.PROFILE.read_bytes())['publication']


def receipt_of(owner):
    return json.loads(owner.RECEIPT.read_bytes())


@BOUND_TRACKS
def test_bound_update_settles_one_version_key_from_the_lookup_and_keeps_response_fields(
        tmp_path, monkeypatch, wire, owner, marker):
    archive, wire.respond = lifecycle_site(
        monkeypatch, tmp_path, owner, marker, active='ver_old',
        uploaded={**SERVICE_RESPONSE, 'versionId': 'ver_from_response', 'version_id': 'ver_from_response',
                  'currentVersionId': 'ver_from_response'},
    )
    bound_profile(owner, share_url=BASE + '/v/token')
    saved_receipt(owner, archive, verified=True, phase='binding', version='ver_stale', versionId='ver_stale',
                  version_id='ver_stale', currentVersionId='ver_stale')
    owner.main(create=False)
    current, stale = VERSION_FIELDS[owner]
    final = receipt_of(owner)
    assert final[current] == 'ver_new' and bound(owner)['expected_version'] == 'ver_new'
    assert not {stale, 'version_id', 'currentVersionId', 'phase'} & set(final) and final['verified'] is True
    assert_no_service_token_and_response_kept(owner.RECEIPT.read_bytes())
    assert requested(wire, 'POST') == [
        '/api/sites/site-1/versions?expected_version=ver_old']


@BOUND_TRACKS
def test_interrupted_binding_of_a_create_reuses_the_share_url_in_its_receipt(
        tmp_path, monkeypatch, wire, owner, marker):
    archive, wire.respond = lifecycle_site(monkeypatch, tmp_path, owner, marker)
    current, stale = VERSION_FIELDS[owner]
    saved_receipt(owner, archive, verified=False, phase='binding', share_url=BASE + '/v/saved',
                  createdAt='kept', **{current: 'ver_stale', stale: 'ver_stale'})
    owner.main(create=False)
    assert requested(wire, 'POST') == []
    assert '/v/saved' in requested(wire, 'GET')
    assert bound(owner)['share_url'] == BASE + '/v/saved'
    final = receipt_of(owner)
    assert final['share_url'] == BASE + '/v/saved' and final['createdAt'] == 'kept'
    assert final[current] == 'ver_new' and stale not in final and 'phase' not in final


@BOUND_TRACKS
def test_pending_create_receipt_without_a_verified_field_resumes_when_it_matches(
        tmp_path, monkeypatch, wire, owner, marker):
    archive, wire.respond = lifecycle_site(monkeypatch, tmp_path, owner, marker)
    saved_receipt(owner, archive)
    owner.main(create=False)
    assert requested(wire, 'POST') == ['/api/sites/site-1/shares']
    assert bound(owner)['site_slug'] == 'site-1'


def assert_release_stayed_unbound(owner):
    assert 'site_slug' not in bound(owner)
    assert receipt_of(owner)['verified'] is False


@BOUND_TRACKS
@pytest.mark.parametrize('reply', [
    {'url': BASE.replace('https://', 'http://') + '/v/token'}, {},
], ids=['plain-http', 'no-url'])
def test_share_creation_without_an_https_url_stops_before_the_share_is_used(
        tmp_path, monkeypatch, wire, owner, marker, reply):
    _, site = lifecycle_site(monkeypatch, tmp_path, owner, marker)
    wire.respond = lambda request: (
        json.dumps(reply).encode() if request.selector.endswith('/shares') else site(request))
    with pytest.raises(RuntimeError, match='omitted public URL'):
        owner.main(create=True)
    assert not [path for path in requested(wire, 'GET') if path.startswith('/v/')]
    assert_release_stayed_unbound(owner)


def test_title_update_failure_stops_browser_use_before_it_verifies_the_release(tmp_path, monkeypatch, wire):
    _, site = lifecycle_site(monkeypatch, tmp_path, browser_use, browser_use.PUBLIC_REPORT_MARKER)

    def respond(request):
        if request.get_method() == 'PATCH':
            raise HTTPError(request.full_url, 500, 'broken', email.message.Message(), io.BytesIO(b'broken'))
        return site(request)

    wire.respond = respond
    with pytest.raises(RuntimeError, match='site title update failed with HTTP 500: broken'):
        browser_use.main(create=True)
    assert not [path for path in requested(wire, 'GET') if path.endswith('/export')]
    assert_release_stayed_unbound(browser_use)


@pytest.mark.parametrize(('owner', 'marker', 'status'), [
    (browser_use, browser_use.PUBLIC_REPORT_MARKER, 202),
    (replay, replay.PUBLIC_REPORT_MARKERS[0], 202),
    (replay, replay.PUBLIC_REPORT_MARKERS[0], 404),
], ids=['browser-use-202', 'replay-202', 'replay-404'])
def test_public_share_that_is_not_ok_stops_before_the_release_is_bound(
        tmp_path, monkeypatch, wire, owner, marker, status):
    _, site = lifecycle_site(monkeypatch, tmp_path, owner, marker)

    def respond(request):
        if not request.selector.startswith('/v/'):
            return site(request)
        if status >= 400:
            raise HTTPError(request.full_url, status, 'gone', email.message.Message(), io.BytesIO(b'gone'))
        return status, b'<!doctype html>'

    wire.respond = respond
    with pytest.raises(RuntimeError, match=f'public report returned HTTP {status}'):
        owner.main(create=True)
    assert not [path for path in requested(wire, 'GET') if path.startswith('/api/preview/')]
    assert_release_stayed_unbound(owner)


@BOUND_TRACKS
def test_large_archive_on_an_unbound_profile_still_requires_create(tmp_path, monkeypatch, wire, owner, marker):
    monkeypatch.setattr(publish, 'STAGED_UPLOAD_THRESHOLD', 0)
    lifecycle_site(monkeypatch, tmp_path, owner, marker)
    with pytest.raises(ValueError, match='no bound site'):
        owner.main(create=False)
    assert not wire
    assert not owner.RECEIPT.exists()


@BOUND_TRACKS
@pytest.mark.parametrize('mismatch', [{'archive_sha256': 'other'}, {'base_url': 'https://other.example.invalid'}])
def test_pending_create_receipt_without_a_verified_field_stops_on_a_mismatch(
        tmp_path, monkeypatch, wire, owner, marker, mismatch):
    archive, _ = lifecycle_site(monkeypatch, tmp_path, owner, marker)
    saved_receipt(owner, archive, **mismatch)
    with pytest.raises(ValueError, match='differs from the current release'):
        owner.main(create=True)
    assert not wire


@BOUND_TRACKS
def test_verified_receipt_is_not_a_pending_create(tmp_path, monkeypatch, wire, owner, marker):
    archive, wire.respond = lifecycle_site(monkeypatch, tmp_path, owner, marker)
    saved_receipt(owner, archive, slug='finished-site', verified=True)
    with pytest.raises(ValueError, match='no bound site'):
        owner.main(create=False)
    assert not wire
    owner.main(create=True)
    assert requested(wire, 'POST') == ['/api/sites', '/api/sites/site-1/shares']
    assert bound(owner)['site_slug'] == 'site-1'


# An interrupted release resumes from what an earlier run saved, so these literals are the
# persisted format and stay as the code before the shared owner wrote them.
OPERATIONS = {
    browser_use: ('lex_browser_use_artifact_operation/1', 'browser-use-'),
    replay: ('lex_webarena_replay_artifact_operation/1', 'trajectory-replay-'),
}
UPDATE_IDENTITY = {'title': 'Title', 'kind': 'version', 'slug': 'site-1', 'expected_version': 'ver_old'}
CREATE_IDENTITY = {'title': 'Title', 'kind': 'create', 'slug': '', 'expected_version': ''}


def bound_release(monkeypatch, tmp_path, owner, marker):
    archive, site = lifecycle_site(monkeypatch, tmp_path, owner, marker, active='ver_old')
    bound_profile(owner, share_url=BASE + '/v/token', title='Title')
    saved_receipt(owner, archive, verified=True)
    return archive, site


@BOUND_TRACKS
@pytest.mark.parametrize(('create', 'identity', 'endpoint'), [
    (False, UPDATE_IDENTITY, '/api/sites/site-1/versions?expected_version=ver_old'),
    (True, CREATE_IDENTITY, '/api/sites'),
], ids=['update', 'create'])
def test_bound_journal_keeps_its_persisted_identity(
        tmp_path, monkeypatch, wire, owner, marker, create, identity, endpoint):
    if create:
        archive, site = lifecycle_site(monkeypatch, tmp_path, owner, marker)
    else:
        archive, site = bound_release(monkeypatch, tmp_path, owner, marker)
    journals = []

    def respond(request):
        if request.selector == endpoint:
            journals.append(json.loads(owner.OPERATION_JOURNAL.read_bytes()))
        return site(request)

    wire.respond = respond
    owner.main(create=create)
    schema, prefix = OPERATIONS[owner]
    upload = next(request for request, _ in wire if request.selector == endpoint)
    key = upload.get_header('Idempotency-key')
    assert key.startswith(prefix)
    assert journals == [{'schema': schema, 'base_url': 'https://artifacts.example.invalid',
                         'archive_sha256': file_sha256(archive), **identity, 'key': key}]
    assert not owner.OPERATION_JOURNAL.exists()


def saved_update_journal(owner, archive, **fields):
    schema, prefix = OPERATIONS[owner]
    key = prefix + '0123456789abcdef'
    owner.OPERATION_JOURNAL.write_text(json.dumps({
        'schema': schema, 'base_url': 'https://artifacts.example.invalid',
        'archive_sha256': file_sha256(archive), 'title': 'Title', 'kind': 'version', 'slug': 'site-1',
        'expected_version': 'ver_old', 'key': key, **fields,
    }), encoding='utf-8')
    return key


@BOUND_TRACKS
def test_saved_update_journal_replays_its_key_when_the_service_has_no_result(
        tmp_path, monkeypatch, wire, owner, marker):
    archive, site = bound_release(monkeypatch, tmp_path, owner, marker)
    key = saved_update_journal(owner, archive)

    def respond(request):
        if request.selector.startswith('/api/operations/'):
            raise HTTPError(request.full_url, 404, 'missing', email.message.Message(), io.BytesIO(b'{}'))
        return site(request)

    wire.respond = respond
    owner.main(create=False)
    assert '/api/operations/' + key in requested(wire, 'GET')
    uploads = [request for request, _ in wire if '/versions?' in request.selector]
    assert [request.get_header('Idempotency-key') for request in uploads] == [key]
    assert bound(owner)['expected_version'] == 'ver_new'
    assert not owner.OPERATION_JOURNAL.exists()


@BOUND_TRACKS
def test_saved_update_journal_recovers_a_completed_operation_without_another_upload(
        tmp_path, monkeypatch, wire, owner, marker):
    archive, site = bound_release(monkeypatch, tmp_path, owner, marker)
    key = saved_update_journal(owner, archive)
    recovered = False

    def respond(request):
        nonlocal recovered
        if request.selector == '/api/operations/' + key:
            recovered = True
            return json.dumps({'status': 'completed', 'httpStatus': 200, 'result': {
                'slug': 'site-1', 'versionId': 'ver_new', 'createdAt': 'recovered'}}).encode()
        if recovered and request.selector == '/api/sites/site-1':
            return b'{"site":{"currentVersionId":"ver_new"}}'
        return site(request)

    wire.respond = respond
    owner.main(create=False)
    assert requested(wire, 'POST') == []
    assert bound(owner)['expected_version'] == 'ver_new'
    assert receipt_of(owner)['createdAt'] == 'recovered'


@BOUND_TRACKS
@pytest.mark.parametrize('other', [
    {'expected_version': 'ver_other'}, {'slug': 'another-site'}, {'kind': 'create'},
], ids=['expected-version', 'slug', 'kind'])
def test_saved_journal_of_another_update_stops_before_it_uploads(
        tmp_path, monkeypatch, wire, owner, marker, other):
    archive, wire.respond = bound_release(monkeypatch, tmp_path, owner, marker)
    saved_update_journal(owner, archive, **other)
    before = owner.OPERATION_JOURNAL.read_bytes()
    with pytest.raises(ValueError, match='differs from this release'):
        owner.main(create=False)
    assert requested(wire, 'POST') == []
    assert owner.OPERATION_JOURNAL.read_bytes() == before


def test_replay_staged_update_keys_derive_from_its_persisted_identity(tmp_path, monkeypatch, wire):
    monkeypatch.setattr(publish, 'STAGED_UPLOAD_THRESHOLD', 0)
    archive, site = bound_release(monkeypatch, tmp_path, replay, replay.PUBLIC_REPORT_MARKERS[0])
    committed = False

    def respond(request):
        nonlocal committed
        if request.selector.startswith('/api/uploads?'):
            return b'{"versionId":"stage_one"}'
        if request.get_method() == 'PUT':
            return b'{}'
        if '/commit' in request.selector:
            committed = True
            return b'{"slug":"site-1","versionId":"ver_new"}'
        if committed and request.selector == '/api/sites/site-1':
            return b'{"site":{"currentVersionId":"ver_new"}}'
        return site(request)

    wire.respond = respond
    replay.main(create=False)
    identity = {'schema': 'lex_webarena_replay_artifact_operation/1', 'base_url': 'https://artifacts.example.invalid',
                'archive_sha256': file_sha256(archive), **UPDATE_IDENTITY}
    seed = json.dumps(identity, sort_keys=True, separators=(',', ':')) + '|'
    paths = ['/api/uploads?expected_version=ver_old', '/api/uploads/stage_one/commit?expected_version=ver_old']
    assert [(request.selector, request.get_header('Idempotency-key'))
            for request, _ in wire if request.get_method() == 'POST'] == [
        (path, 'trajectory-staged-' + hashlib.sha256((seed + path).encode()).hexdigest()) for path in paths
    ]
    assert not replay.OPERATION_JOURNAL.exists()


@BOUND_TRACKS
@pytest.mark.parametrize('journal_matches', [True, False], ids=['own-journal', 'other-journal'])
def test_resumed_create_settles_only_its_own_operation_journal(
        tmp_path, monkeypatch, wire, owner, marker, journal_matches):
    archive, wire.respond = lifecycle_site(monkeypatch, tmp_path, owner, marker)
    saved_receipt(owner, archive, verified=False)
    schema, prefix = OPERATIONS[owner]
    publication.PublicationOperation(owner.OPERATION_JOURNAL, prefix).pending(
        publish.operation_identity(schema, base=BASE, archive_sha256=file_sha256(archive),
                                   title='Title' if journal_matches else 'Another release'),
        allow_create=True,
    )
    if journal_matches:
        owner.main(create=False)
        assert requested(wire, 'POST') == ['/api/sites/site-1/shares']
        assert not owner.OPERATION_JOURNAL.exists()
    else:
        with pytest.raises(ValueError, match='differs from this release'):
            owner.main(create=False)
        assert not wire
        assert owner.OPERATION_JOURNAL.exists()


@pytest.mark.parametrize(('owner', 'marker', 'has_receipt'), [
    (browser_use, browser_use.PUBLIC_REPORT_MARKER, True),
    (replay, replay.PUBLIC_REPORT_MARKERS[0], True),
    (replay, replay.PUBLIC_REPORT_MARKERS[0], False),
], ids=['browser-use', 'replay', 'replay-no-receipt'])
def test_bound_profile_with_no_share_url_creates_one_and_binds_it(
        tmp_path, monkeypatch, wire, owner, marker, has_receipt):
    archive, wire.respond = lifecycle_site(monkeypatch, tmp_path, owner, marker, active='ver_old')
    bound_profile(owner)
    if has_receipt:
        saved_receipt(owner, archive, verified=True)
    owner.main(create=False)
    assert requested(wire, 'POST') == ['/api/sites/site-1/versions?expected_version=ver_old',
                                       '/api/sites/site-1/shares']
    assert bound(owner)['share_url'] == BASE + '/v/token'
    assert receipt_of(owner)['share_url'] == BASE + '/v/token'


@BOUND_TRACKS
@pytest.mark.parametrize(('in_profile', 'chosen'), [
    ('/v/profile', '/v/profile'),
    (None, '/v/receipt'),
], ids=['profile-wins', 'receipt-fallback'])
def test_bound_track_prefers_the_profile_share_url_over_its_receipt(
        tmp_path, monkeypatch, wire, owner, marker, in_profile, chosen):
    archive, wire.respond = lifecycle_site(monkeypatch, tmp_path, owner, marker, active='ver_old')
    bound_profile(owner, **({'share_url': BASE + in_profile} if in_profile else {}))
    saved_receipt(owner, archive, verified=True, share_url=BASE + '/v/receipt')
    owner.main(create=False)
    assert requested(wire, 'POST') == ['/api/sites/site-1/versions?expected_version=ver_old']
    fetched = {path for path in requested(wire, 'GET') if path.startswith('/v/')}
    assert fetched == {chosen}
    assert bound(owner)['share_url'] == BASE + chosen
    assert receipt_of(owner)['share_url'] == BASE + chosen


@BOUND_TRACKS
@pytest.mark.parametrize('deployed', ['same', 'different'])
def test_remote_version_moved_resumes_only_when_the_export_proves_the_same_bytes(
        tmp_path, monkeypatch, wire, owner, marker, deployed):
    archive, site = lifecycle_site(monkeypatch, tmp_path, owner, marker)
    bound_profile(owner, share_url=BASE + '/v/token')
    saved_receipt(owner, archive, verified=True)
    other = tmp_path / 'other.zip'
    with zipfile.ZipFile(other, 'w') as bundle:
        bundle.writestr('index.html', 'another release')

    def respond(request):
        if deployed == 'different' and request.selector.endswith('/export'):
            return other.read_bytes()
        return site(request)

    wire.respond = respond
    before = owner.PROFILE.read_bytes()
    if deployed == 'different':
        with pytest.raises(RuntimeError, match='differs from package'):
            owner.main(create=False)
        assert owner.PROFILE.read_bytes() == before
        # The first export stops the run before it touches the site.
        assert requested(wire, 'GET') == ['/api/sites/site-1', '/api/sites/site-1/export']
        assert requested(wire, 'PATCH') == []
    else:
        owner.main(create=False)
        assert bound(owner)['expected_version'] == 'ver_new'
    assert requested(wire, 'POST') == []


def test_browser_use_requires_its_receipt_and_replay_stages_large_archives(tmp_path, monkeypatch, wire):
    monkeypatch.setattr(publish, 'STAGED_UPLOAD_THRESHOLD', 0)
    bound = {'publication': {'base_url': BASE, 'site_slug': 'site-1', 'expected_version': 'ver_new'}}
    (tmp_path / 'b').mkdir()
    lifecycle_site(monkeypatch, tmp_path / 'b', browser_use, browser_use.PUBLIC_REPORT_MARKER)
    browser_use.PROFILE.write_text(json.dumps(bound), encoding='utf-8')
    with pytest.raises(ValueError, match='receipt is missing'):
        browser_use.main(create=False)
    assert not wire

    (tmp_path / 'r').mkdir()
    _, site = lifecycle_site(monkeypatch, tmp_path / 'r', replay, replay.PUBLIC_REPORT_MARKERS[0])

    def respond(request):
        if request.selector == '/api/uploads':
            return b'{"versionId":"stage_one"}'
        if request.get_method() == 'PUT':
            return b'{}'
        if '/commit' in request.selector:
            return b'{"slug":"site-1","versionId":"ver_new"}'
        return site(request)

    wire.respond = respond
    replay.main(create=True)
    posts = [(request.selector, request.get_header('Idempotency-key'))
             for request, _ in wire if request.get_method() == 'POST']
    assert not any(path == '/api/sites' for path, _ in posts)
    assert [path for path, _ in posts[:2]] == ['/api/uploads', '/api/uploads/stage_one/commit']
    assert posts[1][1].startswith('trajectory-staged-')


class ReceiptSite(Service):
    """The Web Fetch publication fake, plus the sharing state a private site asks for."""

    def __call__(self, path, data=None, content_type=None, *, method=None, extra_headers=None):
        if path.endswith('/sharing'):
            return 200, b'{"visibility":"private"}'
        return super().__call__(path, data, content_type, method=method, extra_headers=extra_headers)


def publish_web_search(tmp_path, monkeypatch):
    report, work = web_search_release(tmp_path, monkeypatch)
    monkeypatch.setattr(web_search.subprocess, 'check_output', lambda *args, **kwargs: b'fixture-commit')
    web_search.publish(report, work)
    return work / 'report.zip', 'Search API evaluation'


def publish_public_webarena(tmp_path, monkeypatch):
    _, profile, staged, archive, proof, receipt, journal = public_webarena_release(tmp_path)
    public_webarena.publish_current(profile, staged, archive, proof, receipt, journal)
    return archive, 'WebArena 轨迹重放'


@pytest.mark.parametrize(('run', 'agent', 'namespace'), [
    (publish_web_search, 'Web-Search-Report-Publisher/1.0', 'search'),
    (publish_public_webarena, 'Public-WebArena-Report-Publisher/1.0', 'public-webarena'),
], ids=['web-search', 'public-webarena'])
def test_receipt_tracks_send_their_own_identity_and_keep_replayable_upload_bytes(
        tmp_path, monkeypatch, wire, run, agent, namespace):
    monkeypatch.delenv('ARTIFACT_SITE_BASE_URL', raising=False)
    monkeypatch.setattr(publish, 'STAGED_UPLOAD_THRESHOLD', 10**9)
    wire.respond = behind_wire(ReceiptSite())
    archive, title = run(tmp_path, monkeypatch)
    upload = wire[0][0]
    assert '/versions?' in upload.selector
    assert {request.get_header('User-agent') for request, _ in wire} == {agent}
    assert upload.get_header('Idempotency-key').startswith(namespace + '-')
    # A journal from before these tracks had their own adapter must replay byte-identically.
    body, content_type = publish.release_multipart(archive, title, file_sha256(archive), 'webfetch')
    assert upload.data == body
    assert upload.get_header('Content-type') == content_type
    assert b'filename="webfetch.zip"' in upload.data


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
    monkeypatch.setattr(publish, 'STAGED_UPLOAD_THRESHOLD', 10**9)
    wire.respond = behind_wire(Service())
    web_fetch.publish(config)
    receipt = json.loads(config.receipt.read_bytes())
    upload_type = publish.release_multipart(config.archive, config.title, receipt['archive_sha256'], 'webfetch')[1]
    assert sent(wire, WEB_FETCH_BASE) == [
        ('POST', '/api/sites/site-one/versions?expected_version=ver_old',
         [*WEB_FETCH, ('Content-type', upload_type), ('Idempotency-key', receipt['operation_key'])], 240),
        WEB_FETCH_SHARE,
    ]


def test_web_fetch_staged_publish_keeps_its_wire_contract(tmp_path, monkeypatch, wire):
    config = web_fetch_release(tmp_path)
    monkeypatch.setattr(publish, 'STAGED_UPLOAD_THRESHOLD', 0)
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
    receipt.write_text(json.dumps({'slug': 'site-one', 'base_url': BASE}), encoding='utf-8')
    wire.respond = lambda request: b'{"slug":"site-one","title":"after"}'
    web_fetch.rename_main([str(receipt), '--title', 'after'])
    assert sent(wire) == [('PATCH', '/api/sites/site-one', [BEARER, JSON_BODY], 30)]
    assert wire[0][0].data == b'{"title": "after"}'
