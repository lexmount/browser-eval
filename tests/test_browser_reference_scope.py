"""Browser reference capture must preserve scope and isolate case failures."""
import json

import pytest

from scripts import capture_headless_browser_observations as browser_capture
from scripts.capture_headless_browser_observations import (
    evaluate_popular_source_contract,
    capture_with_retries,
    chromium_host_rule,
    extract_pruned_selector_text,
    match_resource_targets,
    public_ip,
    resolve_public_host,
)
from harness.web_fetch.reference_lifecycle import resource_source_scope_identity


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


def test_popular_source_contract_rejects_stale_heading_without_weakening_task():
    case = {
        'url': 'https://example.com/',
        'acceptance_contract': {
            'version': 1, 'schema': 'body_text', 'minimum_records': 0,
            'stable_text_anchors': [
                {'id': 'heading', 'alternatives': ['Original heading']},
                {'id': 'nav', 'alternatives': ['Pricing']},
            ],
        },
    }
    fresh = evaluate_popular_source_contract(
        case, '<html><body><h1>Original heading</h1><nav>Pricing</nav></body></html>')
    stale = evaluate_popular_source_contract(
        case, '<html><body><h1>New heading</h1><nav>Pricing</nav></body></html>')
    hidden = evaluate_popular_source_contract(
        case, '<html><body><h1>New heading</h1><script>Original heading</script>'
              '<nav>Pricing</nav></body></html>')
    assert fresh['pass'] is True
    assert stale['missing_anchor_ids'] == ['heading']
    assert hidden['missing_anchor_ids'] == ['heading']


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
