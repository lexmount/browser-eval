"""Browser reference capture must preserve scope and isolate case failures."""
import json
import sys
from types import SimpleNamespace

import pytest

from scripts import capture_headless_browser_observations as browser_capture
from scripts.capture_headless_browser_observations import (
    capture,
    evaluate_popular_source_contract,
    capture_popular_contract,
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
        case, '<html><body><h1>Original heading</h1><nav>Pricing</nav></body></html>',
        'Original heading\nPricing', [])
    stale = evaluate_popular_source_contract(
        case, '<html><body><h1>New heading</h1><nav>Pricing</nav></body></html>',
        'New heading\nPricing', [])
    hidden = evaluate_popular_source_contract(
        case, '<html><body><h1>New heading</h1><script>Original heading</script>'
              '<nav>Pricing</nav></body></html>', 'New heading\nPricing', [])
    assert fresh['pass'] is True
    assert stale['missing_anchor_ids'] == ['heading']
    assert hidden['missing_anchor_ids'] == ['heading']


def test_popular_source_contract_does_not_accept_hidden_old_navigation():
    case = {
        'url': 'https://example.com/',
        'acceptance_contract': {
            'version': 1, 'schema': 'body_text', 'minimum_records': 0,
            'stable_text_anchors': [
                {'id': 'current', 'alternatives': ['News']},
                {'id': 'old-nav', 'alternatives': ['Home']},
            ],
        },
    }
    result = evaluate_popular_source_contract(
        case,
        '<html><head><style>.offscreen{display:none}</style></head>'
        '<body><nav>News</nav><div class="offscreen">Home</div></body></html>',
        'News', [],
    )

    assert result['raw_html_pass'] is True
    assert result['pass'] is False
    assert result['missing_anchor_ids'] == ['old-nav']


def test_popular_source_contract_rejects_hidden_records_behind_security_gate():
    case = {
        'url': 'https://example.com/',
        'acceptance_contract': {
            'version': 1, 'schema': 'named_link', 'minimum_records': 1,
            'hosts': ['example.com'], 'entry_path': r'/item/[0-9]+',
            'stable_text_anchors': [
                {'id': 'nav', 'alternatives': ['Discover'], 'match_scope': 'non_record_lines'}],
        },
    }
    raw = '<html><body><nav>Discover</nav><a href="/item/1">First item</a></body></html>'
    fresh = evaluate_popular_source_contract(
        case, raw, 'Discover\nFirst item',
        [{'name': 'First item', 'url': 'https://example.com/item/1'}])
    gated = evaluate_popular_source_contract(
        case, raw, '安全限制\nIP存在风险，请切换可靠网络环境后重试', [])
    assert fresh['pass'] is True
    assert gated['pass'] is False
    assert gated['raw_html_pass'] is True
    assert 'too_few_complete_records' in gated['errors']


def test_popular_capture_waits_for_late_visible_content(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(browser_capture.time, 'monotonic', lambda: clock[0])

    class Locator:
        def __init__(self, page):
            self.page = page

        @property
        def first(self):
            return self

        def inner_text(self, *, timeout):
            return self.page.text

        def evaluate_all(self, expression):
            return []

    class Page:
        url = 'https://example.com/'
        text = 'Loading'

        def content(self):
            return f'<html><body>{self.text}</body></html>'

        def locator(self, selector):
            return Locator(self)

        def wait_for_timeout(self, milliseconds):
            clock[0] += milliseconds / 1000
            self.text = 'Ready'

    case = {
        'url': Page.url,
        'acceptance_contract': {
            'version': 1, 'schema': 'body_text', 'minimum_records': 0,
            'stable_text_anchors': [{'id': 'ready', 'alternatives': ['Ready']}],
        },
    }
    content, source_hash, contract = capture_popular_contract(Page(), case, 1000, 0.0)

    assert content == 'Ready'
    assert contract['pass'] is True
    assert clock[0] > 0
    assert len(source_hash) == 64


def test_popular_capture_retains_last_visible_failure_at_deadline(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(browser_capture.time, 'monotonic', lambda: clock[0])

    class Locator:
        @property
        def first(self):
            return self

        def inner_text(self, *, timeout):
            return 'Loading'

        def evaluate_all(self, expression):
            return []

    class Page:
        url = 'https://example.com/'

        def content(self):
            return '<html><body>Loading</body></html>'

        def locator(self, selector):
            return Locator()

        def wait_for_timeout(self, milliseconds):
            clock[0] += milliseconds / 1000

    case = {
        'url': Page.url,
        'acceptance_contract': {
            'version': 1, 'schema': 'body_text', 'minimum_records': 0,
            'stable_text_anchors': [{'id': 'ready', 'alternatives': ['Ready']}],
        },
    }
    content, _, contract = capture_popular_contract(Page(), case, 500, 0.0)

    assert content == 'Loading'
    assert contract['pass'] is False
    assert contract['missing_anchor_ids'] == ['ready']
    assert clock[0] == 0.5


def test_popular_capture_uses_primary_body_when_microfrontend_adds_another(monkeypatch):
    monkeypatch.setattr(browser_capture.time, 'monotonic', lambda: 0.0)

    class PrimaryBody:
        def inner_text(self, *, timeout):
            return 'Ready'

    class AmbiguousBodies:
        first = PrimaryBody()

        def inner_text(self, *, timeout):
            raise AssertionError('unscoped body locator is ambiguous')

    class VisibleLinks:
        def evaluate_all(self, expression):
            return []

    class Page:
        url = 'https://example.com/'

        def content(self):
            return '<html><body>Ready</body></html>'

        def locator(self, selector):
            return AmbiguousBodies() if selector == 'body' else VisibleLinks()

    case = {
        'url': Page.url,
        'acceptance_contract': {
            'version': 1, 'schema': 'body_text', 'minimum_records': 0,
            'stable_text_anchors': [{'id': 'ready', 'alternatives': ['Ready']}],
        },
    }

    content, _, contract = capture_popular_contract(Page(), case, 1000, 0.0)
    assert content == 'Ready'
    assert contract['pass'] is True


def test_popular_observation_retains_observed_redirect_and_title(monkeypatch):
    class Locator:
        @property
        def first(self):
            return self

        def wait_for(self, **kwargs):
            return None

        def count(self):
            return 1

        def inner_text(self, **kwargs):
            return 'Ready'

        def evaluate_all(self, expression):
            return []

    class Page:
        url = 'https://www.example.com/locale'

        def goto(self, *args, **kwargs):
            return None

        def locator(self, selector):
            return Locator()

        def content(self):
            return '<html><body>Ready</body></html>'

        def title(self):
            return 'Localized homepage'

    class Browser:
        def new_page(self):
            return Page()

        def close(self):
            return None

    class Context:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=lambda **kwargs: Browser()))

        def __exit__(self, *args):
            return False

    monkeypatch.setitem(sys.modules, 'playwright.sync_api',
                        SimpleNamespace(sync_playwright=Context))
    case = {
        'id': 'popular-redirect', 'url': 'https://example.com/',
        'source_benchmark': {'stable_annotation': {'registry': 'popular-sites'}},
        'acceptance_contract': {
            'version': 1, 'schema': 'body_text', 'minimum_records': 0,
            'stable_text_anchors': [{'id': 'ready', 'alternatives': ['Ready']}],
        },
    }

    row = capture(case, 1000, '/chrome', '93.184.216.34')
    assert row['status'] == 'success'
    assert row['completion']['satisfied'] is True
    assert row['final_url'] == 'https://www.example.com/locale'
    assert row['title'] == 'Localized homepage'


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
