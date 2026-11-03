"""Each publisher keeps its own wire behaviour through the one shared Artifact Site client."""

import email.message
import io
import json
import stat
import urllib.request
from urllib.error import HTTPError

import pytest

from harness.browser_use import report_publish as browser_use
from harness.reporting import publication
from harness.reporting.publication import PublicationClient
from harness.trajectory_replay import webarena_report_publish as replay
from harness.web_fetch import report_publish as web_fetch
from harness.web_fetch import wcxb_publication as wcxb

BASE = 'https://artifacts.example.invalid'
BEARER = ('Authorization', 'Bearer ahp_fixture')


@pytest.fixture
def wire(monkeypatch):
    monkeypatch.setattr(publication, 'load_token', lambda *, base_url: 'ahp_fixture')
    calls = []

    class Response(io.BytesIO):
        status = 200
        headers = email.message.Message()

    def urlopen(request, *, timeout):
        calls.append((request, timeout))
        return Response(b'{}')

    monkeypatch.setattr(urllib.request, 'urlopen', urlopen)
    return calls


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
