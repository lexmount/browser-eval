"""Browser reference capture must preserve scope and isolate case failures."""
import json
import os

import pytest

from harness.web_fetch import browser_observations as browser_capture
from harness.web_fetch.browser_observations import (
    capture_with_retries,
    chromium_host_rule,
    extract_pruned_selector_text,
    match_resource_targets,
    public_ip,
    resolve_public_host,
)
from harness.web_fetch.reference_lifecycle import resource_source_scope_identity


@pytest.mark.skipif(not os.getenv('BROWSER_EVAL_TEST_BROWSER_BINARY'), reason='explicit Chromium binary required')
def test_live_browser_audit_includes_visible_iframe_not_hidden_iframe():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            html=(b'<body>Child visible text<div style="display:none">Hidden child text</div></body>'
                  if self.path=='/child' else b'<body>Parent text<iframe src="/child"></iframe><iframe style="display:none" srcdoc="Hidden frame text"></iframe></body>')
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(html)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Page)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        case={'id':'frames','url':f'http://127.0.0.1:{server.server_port}/','task_kind':'body'}
        row=browser_capture.capture(case,10000,os.environ['BROWSER_EVAL_TEST_BROWSER_BINARY'],observation_scope='visible_page')
        assert 'Parent text' in row['content_text']
        assert 'Child visible text' in row['content_text']
        assert 'Hidden child text' not in row['content_text']
        assert 'Hidden frame text' not in row['content_text']
        assert len(row['visible_frames'])==1
    finally:
        server.shutdown();server.server_close();thread.join()


@pytest.mark.skipif(not os.getenv('BROWSER_EVAL_TEST_BROWSER_BINARY'), reason='explicit Chromium binary required')
def test_live_browser_whole_page_audit_keeps_visible_chrome_but_not_hidden_text():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path=='/start':
                self.send_response(302);self.send_header('Location','/page');self.end_headers();return
            html=b'''<html lang="en"><title>Audit fixture</title><body><nav>Images</nav>
            <main>Article content</main><footer>Terms</footer>
            <div style="display:none">Hidden answer</div>
            <script>setTimeout(()=>{const p=document.createElement('p');p.textContent='Delayed visible text';document.body.append(p)},100)</script>
            </body></html>'''
            self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(html)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Page)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        url=f'http://127.0.0.1:{server.server_port}/start'
        case={'id':'page-audit','url':url,'task_kind':'body'}
        binary=os.environ['BROWSER_EVAL_TEST_BROWSER_BINARY']
        audit=browser_capture.capture(case,10000,binary,observation_scope='visible_page')
        assert audit['content_text']=='Images\nArticle content\nTerms\n\nDelayed visible text'
        assert 'Hidden answer' not in audit['content_text']
        assert audit['final_url']==url.replace('/start','/page')
        assert audit['title']=='Audit fixture'
        assert audit['document_language']=='en'
        assert 'Hidden answer' in audit['outer_html']
        reference=browser_capture.capture(case,10000,binary)
        assert 'Article content' in reference['content_text']
        assert 'Images' not in reference['content_text']
        assert 'Terms' not in reference['content_text']
    finally:
        server.shutdown();server.server_close();thread.join()


def test_browser_audit_resume_is_input_bound_and_preserves_access_failures(tmp_path, monkeypatch):
    calls=[]
    def observe(case, *args, **kwargs):
        calls.append(case['id'])
        return {'case_id':case['id'],'status':'failure' if case['id']=='blocked' else 'success',
                'content_text':'Verify you are human' if case['id']=='blocked' else 'Article body'}
    monkeypatch.setattr(browser_capture,'capture_with_retries',observe)
    cases=[{'id':k,'url':'https://example.com/'+k} for k in ('article','blocked')]
    binary = tmp_path / 'browser'
    binary.write_bytes(b'synthetic browser identity')
    options=dict(timeout_ms=1000,attempts=1,observation_scope='visible_page',checkpoints=tmp_path,
                 browser_binary=str(binary))
    first=browser_capture.capture_batch(cases,**options)
    assert browser_capture.capture_batch(cases,**options)==first
    assert calls==['article','blocked']
    assert first['blocked']['status']=='failure'
    with pytest.raises(ValueError,match='inputs changed'):
        browser_capture.capture_batch(cases,**{**options,'timeout_ms':2000})
    cases[0]['url']='https://example.com/changed'
    with pytest.raises(ValueError,match='inputs changed'):
        browser_capture.capture_batch(cases,**options)
    saved=tmp_path/'blocked.json';data=json.loads(saved.read_text())
    data['observation']['status']='success';saved.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='observation changed'):
        browser_capture.capture_batch([cases[1]],**options)


def test_rendered_wikipedia_selector_preserves_main_article_text():
    html = """
    <div class="mw-parser-output">
      <section><h1>Artificial intelligence</h1>
      <p>Main article prose remains observable.</p></section>
      <div class="navbox">Related navigation</div>
    </div>
    """

    text = extract_pruned_selector_text(
        html,
        "https://en.wikipedia.org/w/index.php?title=Artificial_intelligence&oldid=1",
    )

    assert "Artificial intelligence" in text
    assert "Main article prose remains observable." in text


def test_rendered_wikipedia_selector_excludes_site_chrome():
    html = """
    <div class="mw-parser-output">
      <p>Main article prose</p>
      <script>script marker</script><style>style marker</style>
      <nav>nav marker</nav><footer>footer marker</footer>
      <div aria-hidden="true">aria hidden marker</div>
      <span class="mw-editsection">edit marker</span>
      <div class="vector-toc">toc marker</div>
      <aside class="sidebar">sidebar marker</aside>
      <aside class="sphinxsidebar">sphinx marker</aside>
      <div class="navbox-styles">navbox styles marker</div>
      <div class="navbox">navbox marker</div>
      <aside role="navigation">role navigation marker</aside>
    </div>
    """

    text = extract_pruned_selector_text(
        html,
        "https://en.wikipedia.org/w/index.php?title=Artificial_intelligence&oldid=1",
    )

    assert text == "Main article prose"


def test_selector_targeting_navigation_fails_closed():
    text = extract_pruned_selector_text(
        '<nav><p>Navigation only</p></nav>',
        'https://example.com/',
    )

    assert text == ''


def test_capture_retries_one_case_until_declared_completion(monkeypatch):
    calls = []

    def fake_capture(case, timeout_ms, browser_binary, resolved_ip):
        calls.append(case['id'])
        if len(calls) == 1:
            raise RuntimeError('temporary DNS failure')
        return {
            'case_id': case['id'],
            'status': 'success',
            'observer': {},
            'completion': {'satisfied': True},
        }

    monkeypatch.setattr(browser_capture, 'capture', fake_capture)
    monkeypatch.setattr(
        browser_capture, 'resolve_public_host',
        lambda url, explicit_ip: ('example.com', '93.184.216.34', 'system_dns_getaddrinfo'))
    row = capture_with_retries(
        {'id': 'retry-me', 'url': 'https://example.com/'}, 1000, 2, '/chrome', None)

    assert calls == ['retry-me', 'retry-me']
    assert row['status'] == 'success'
    assert row['attempt_count'] == 2
    assert row['attempt_errors'][0]['type'] == 'RuntimeError'
    assert row['observer']['host'] == 'example.com'
    assert row['observer']['resolved_ip'] == '93.184.216.34'
    assert row['observer']['resolution_method'] == 'system_dns_getaddrinfo'
    assert row['observer']['dns_override'] is True


def test_failed_resource_capture_retains_last_bounded_observation(monkeypatch):
    def incomplete(case, timeout_ms, browser_binary, resolved_ip):
        return {
            'case_id': case['id'], 'status': 'success', 'independent': True,
            'checked_at': '2026-09-11T00:00:00+00:00',
            'observer': {'browser': 'Chromium'},
            'resource_candidates': [{'tag': 'img', 'attribute': 'src',
                                     'url': 'https://example.com/other.png'}],
            'target_matches': [{'id': 'target', 'matched_urls': [], 'satisfied': False}],
            'completion': {'satisfied': False, 'elapsed_ms': 1000,
                           'timeout_ms': timeout_ms, 'condition': 'all targets'},
        }

    monkeypatch.setattr(browser_capture, 'capture', incomplete)
    monkeypatch.setattr(
        browser_capture, 'resolve_public_host',
        lambda url, explicit_ip: ('example.com', '93.184.216.34', 'system_dns_getaddrinfo'))

    row = capture_with_retries(
        {'id': 'resource', 'url': 'https://example.com/', 'task_kind': 'resource'},
        1000, 2, '/chrome', None)

    assert row['status'] == 'failure'
    assert row['resource_candidates'][0]['url'].endswith('/other.png')
    assert row['target_matches'][0]['satisfied'] is False
    assert row['completion']['elapsed_ms'] == 1000


def test_system_dns_selects_a_public_address_and_records_method(monkeypatch):
    monkeypatch.setattr(browser_capture.socket, 'getaddrinfo', lambda *args, **kwargs: [
        (browser_capture.socket.AF_INET, browser_capture.socket.SOCK_STREAM, 0, '',
         ('127.0.0.1', 0)),
        (browser_capture.socket.AF_INET, browser_capture.socket.SOCK_STREAM, 0, '',
         ('93.184.216.34', 0)),
    ])

    host, address, method = resolve_public_host('https://docs.example.com/guide')

    assert host == 'docs.example.com'
    assert address == '93.184.216.34'
    assert method == 'system_dns_getaddrinfo'
    assert chromium_host_rule(host, address) == 'MAP docs.example.com 93.184.216.34'


@pytest.mark.parametrize('address', [
    '127.0.0.1', '10.1.2.3', '192.168.1.2', '169.254.1.2', '::1', 'not-an-ip',
])
def test_explicit_dns_override_rejects_private_or_invalid_addresses(address):
    with pytest.raises(ValueError):
        public_ip(address)


def test_explicit_dns_override_maps_only_the_current_case_host():
    host, address, method = resolve_public_host(
        'https://docs.tavily.com/documentation', '8.8.8.8')

    assert (host, address, method) == (
        'docs.tavily.com', '8.8.8.8', 'explicit_cli_override')
    assert chromium_host_rule(host, address) == 'MAP docs.tavily.com 8.8.8.8'
    assert 'quotes.toscrape.com' not in chromium_host_rule(host, address)


def test_resource_observation_matches_every_declared_target_and_cdn_variants():
    case = {
        'id': 'resource', 'url': 'https://example.test/page',
        'track': 'image', 'task_kind': 'resource',
        'expected_resources': [
            {'id': 'one', 'urls': ['https://images.unsplash.com/photo-one']},
            {'id': 'two', 'urls': [], 'url_patterns': [r'^https://cdn\.example\.test/two(?:\?|$)']},
        ],
        'resource_output_contract': {'unexpected': 'allow'},
    }
    candidates = [
        {'tag': 'img', 'attribute': 'currentSrc',
         'url': 'https://images.unsplash.com/photo-one?w=1200'},
        {'tag': 'a', 'attribute': 'href', 'url': 'https://cdn.example.test/two?download=1'},
    ]

    matches = match_resource_targets(case, candidates)

    assert [row['satisfied'] for row in matches] == [True, True]
    assert resource_source_scope_identity(case) == {
        'case_id': 'resource', 'url': 'https://example.test/page',
        'track': 'image', 'task_kind': 'resource',
        'expected_resources': case['expected_resources'],
        'resource_output_contract': {'unexpected': 'allow'},
    }


def test_resource_observation_fails_closed_when_one_target_is_missing():
    case = {
        'id': 'resource', 'url': 'https://example.test/page',
        'track': 'image', 'task_kind': 'resource',
        'expected_resources': [
            {'id': 'one', 'urls': ['https://example.test/one.png']},
            {'id': 'two', 'urls': ['https://example.test/two.png']},
        ],
    }

    matches = match_resource_targets(case, [
        {'tag': 'img', 'attribute': 'src', 'url': 'https://example.test/one.png'},
    ])

    assert [row['satisfied'] for row in matches] == [True, False]


def test_batch_writes_every_case_before_failing_closed(tmp_path, monkeypatch):
    dataset = tmp_path / 'dataset.json'
    output = tmp_path / 'observations.json'
    dataset.write_text(json.dumps({'cases': [
        {'id': 'unreachable', 'url': 'https://example.invalid/'},
        {'id': 'reachable', 'url': 'https://example.com/'},
    ]}))

    def fake_capture(case, timeout_ms, attempts, browser_binary, resolved_ip):
        succeeded = case['id'] == 'reachable'
        return {
            'case_id': case['id'],
            'status': 'success' if succeeded else 'failure',
            'completion': {'satisfied': succeeded},
        }

    monkeypatch.setattr(browser_capture, 'capture_with_retries', fake_capture)
    monkeypatch.setattr('sys.argv', [
        'capture_headless_browser_observations.py',
        '--dataset', str(dataset), '--output', str(output),
        '--case-ids', 'unreachable,reachable', '--attempts', '2',
    ])

    with pytest.raises(SystemExit) as raised:
        browser_capture.main()

    assert raised.value.code == 1
    rows = json.loads(output.read_text())
    assert set(rows) == {'unreachable', 'reachable'}
    assert rows['unreachable']['status'] == 'failure'
    assert rows['reachable']['status'] == 'success'
