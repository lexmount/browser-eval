"""Public evidence must not expose URL credentials or erase billable calls."""
import json
import tempfile
import unittest
from pathlib import Path

from harness.web_search.core import digest, request_cost
from harness.web_search.report import public_calls


class PublicCallScenarios(unittest.TestCase):
    def test_release_rejects_credentials_even_with_updated_file_hash(self):
        from harness.web_search.example import example
        from harness.web_search.report import check
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            example(root)
            output = root / "report"
            manifest_path = output / "manifest.json"
            manifest = json.loads(manifest_path.read_text())
            binding = next(x for x in manifest["bundle"] if x["path"].startswith("calls/"))
            path = output / binding["path"]
            value = json.loads(path.read_text())
            value["leaked_link"] = "https://reader:private-secret@example.org"
            path.write_text(json.dumps(value))
            binding["sha256"] = digest(path.read_bytes())
            manifest_path.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, "Public evidence contains URL credentials"):
                check(output)

    def test_billable_invalid_url_is_redacted_without_changing_outcome(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'calls').mkdir()
            raw = b'{"results":[{"url":"https://reader:private-secret@example.org/paper"}]}'
            path = root / 'calls/one.json'
            path.write_bytes(raw)
            evidence = {'path': 'calls/one.json', 'sha256': digest(raw)}
            method = {'id': 'search', 'price_per_1000': 5, 'billing': 'http-200-nonempty'}
            rows = [{'method': 'search', 'evidence': evidence, 'results': [], 'error': 'unsafe URL',
                     'cost': 0, 'elapsed_ms': 50, 'attempts': [{'http_status': 200, 'transport_exit': 0,
                     'cost': 0, 'evidence': evidence}]}]
            original = json.dumps(rows)
            output, files, redactions, changes = public_calls({'methods': [method]}, rows, root, {})
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(json.dumps(rows), original)
            self.assertNotIn(b'private-secret', files['calls/one.json'])
            self.assertIn(b'REDACTED:REDACTED@', files['calls/one.json'])
            self.assertEqual(output[0]['results'], [])
            self.assertEqual(output[0]['error'], 'unsafe URL')
            self.assertEqual(output[0]['elapsed_ms'], 50)
            self.assertEqual(output[0]['cost'], .005)
            self.assertEqual(redactions[0]['original_sha256'], digest(raw))
            self.assertEqual(changes[0]['original_cost'], 0)
            self.assertEqual(output[0]['evidence'], output[0]['attempts'][-1]['evidence'])

    def test_empty_error_unknown_and_nonempty_billing_are_distinct(self):
        method = {'price_per_1000': 5, 'billing': 'http-200-nonempty'}
        self.assertEqual(request_cost(method, 200, 0, {'results': []}), 0)
        self.assertEqual(request_cost(method, 402, 0, {'error': 'balance'}), 0)
        self.assertEqual(request_cost(method, 200, 0, {'results': [{'url': 'invalid'}]}), .005)
        self.assertIsNone(request_cost(method, 200, 0, {'unexpected': True}))
        self.assertIsNone(request_cost(method, 200, 1, {'results': [1]}))


def test_url_credentials_redact_delimiter_passwords_without_eating_adjacent_prose():
    from harness.web_search.report import _redact_url_userinfo
    for url in (b"https://user:pa;ss@host.example/x", b"https://user:a,b@host.example/x", b"https://user:pa'ss@host.example/x", b"https://user:pa(ss)@host.example/x"):
        assert _redact_url_userinfo(url) == b"https://REDACTED:REDACTED@host.example/x"
    for text in (b"https://a.example'bob@corp.example", b"https://a.example)bob@corp.example"):
        assert _redact_url_userinfo(text) == text
