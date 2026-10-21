"""Collection scenarios: missing sources, empty billing, and retained failures."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness.web_search.report import load_run
from harness.web_search.runner import run


class CollectionScenarios(unittest.TestCase):
    def test_resume_retains_complete_cases_and_recollects_whole_interrupted_case(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self.profile(root)
            profile = json.loads(p.read_text())
            profile["cases"].append(dict(profile["cases"][0], id="second-case"))
            profile["protocol"]["retry"] = True
            p.write_text(json.dumps(profile))
            calls = []
            recovered = False

            def http(url, body=None, header=None, timeout=20):
                receipt = dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error="")
                if body is None:
                    return receipt, b"reference " * 20
                calls.append(url)
                if not recovered and len(calls) > 2:
                    return dict(receipt, http_status=402), b'{"error":"Insufficient Balance"}'
                return receipt, b'{"results":[{"url":"https://example.org/a","title":"Fixture"}]}'

            with (
                patch("harness.web_search.runner.http_call", side_effect=http),
                patch("harness.web_search.runner.load_runtime_env"),
                patch.dict(os.environ, LEXMOUNT_SEARCH_API_KEY="test", DEEPSEEK_API_KEY="test", BOCHA_API_KEY="test"),
            ):
                with self.assertRaisesRegex(ValueError, "Provider account error"):
                    run(p, root / "run")
                original = (root / "run/results.jsonl").read_bytes()
                # A process can stop partway through the final row write.
                with (root / "run/results.jsonl").open("ab") as stream:
                    stream.write(b'{"id":"incomplete')
                recovered = True
                run(p, root / "run", resume=True)
            _, rows, receipt = load_run(root / "run")
            self.assertEqual(len(calls), 10)  # four interrupted calls, then both three-method cases
            self.assertEqual(len(rows), 6)
            self.assertTrue(receipt["certified"])
            self.assertEqual((root / "run/resume-1/previous-results.jsonl").read_bytes(),
                             original + b'{"id":"incomplete')
            self.assertEqual(len(list((root / "run/resume-1/calls").glob("*.json"))), 4)
            contract = json.loads((root / "run/resume-1/contract.json").read_text())
            self.assertEqual(
                contract["recollect_case_ids"], ["ds-price-zh", "second-case"]
            )
            archived = next((root / "run/resume-1/calls").glob("*.json"))
            archived.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                load_run(root / "run")

    def test_account_errors_stop_the_cohort_after_declared_retry(self):
        for status in (401, 402):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                p = self.profile(root)
                profile = json.loads(p.read_text())
                profile["protocol"]["retry"] = True
                p.write_text(json.dumps(profile))
                calls = []

                def http(url, body=None, header=None, timeout=20):
                    receipt = dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error="")
                    if body is None:
                        return receipt, b"reference " * 20
                    calls.append(url)
                    return dict(receipt, http_status=status), b'{"error":{"message":"account unavailable"}}'

                with (
                    patch("harness.web_search.runner.http_call", side_effect=http),
                    patch("harness.web_search.runner.load_runtime_env"),
                    patch.dict(os.environ, LEXMOUNT_SEARCH_API_KEY="test", DEEPSEEK_API_KEY="test", BOCHA_API_KEY="test"),
                ):
                    with self.assertRaisesRegex(ValueError, "Provider account error"):
                        run(p, root / "run")
                self.assertEqual(len(calls), 2)
                self.assertEqual(len(set(calls)), 1)  # no spending on the next participant
                rows = (root / "run/results.jsonl").read_text().splitlines()
                self.assertEqual(len(rows), 1)
                self.assertEqual(len(json.loads(rows[0])["attempts"]), 2)
                self.assertTrue((root / "run/interruption.json").exists())
                self.assertFalse((root / "run/receipt.json").exists())

    def profile(self, root):
        d = json.loads(Path("evaluation/web-search/current-report.json").read_text())
        d["cases"] = d["cases"][:1]
        d["protocol"].update(rounds=1, interval_s=0)
        d["protocol"].pop("reference_scope", None)
        d.pop("reference_checks", None)
        p = root / "input.json"
        p.write_text(json.dumps(d))
        return p

    def test_published_label_snapshot_requires_the_frozen_hash(self):
        from harness.web_search.runner import reference_available
        from harness.web_search.core import digest
        url = "https://example.org/pinned-labels.csv"
        raw = b"query,answer,url\n" * 20
        profile = {"reference_artifacts": {url: {"sha256": digest(raw)}}}
        receipt = {"http_status": 200, "transport_exit": 0}
        self.assertTrue(reference_available(profile, url, receipt, raw))
        self.assertFalse(reference_available(profile, url, receipt, raw + b"changed"))
        self.assertFalse(reference_available(profile, url, dict(receipt, transport_exit=28), raw))

    def test_failure_is_retained_after_the_single_declared_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self.profile(root)
            order = []

            def http(url, body=None, header=None, timeout=20):
                if body is None:
                    return dict(
                        http_status=200, elapsed_ms=1, transport_exit=0, transport_error=""
                    ), b"reference " * 20
                order.append(url)
                return dict(
                    http_status=503 if "lexmount" in url else 200,
                    elapsed_ms=20,
                    transport_exit=0,
                    transport_error="",
                ), (b'{"error":"Unavailable"}' if "lexmount" in url else b'{"results":[]}')

            with (
                patch("harness.web_search.runner.http_call", side_effect=http),
                patch("harness.web_search.runner.load_runtime_env"),
                patch.dict(os.environ, LEXMOUNT_SEARCH_API_KEY="test", DEEPSEEK_API_KEY="test", BOCHA_API_KEY="test"),
            ):
                run(p, root / "run")
            _, rows, receipt = load_run(root / "run")
            self.assertEqual(len(order), 4)
            self.assertEqual(len(rows), 3)
            self.assertEqual(len(rows[0]["attempts"]), 2)
            self.assertEqual(rows[1]["cost"], 0)
            self.assertTrue(rows[0]["error"])
            self.assertTrue(receipt["certified"])
            ref = next((root / "run/references").glob("*.bin"))
            ref.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                load_run(root / "run")

    def test_failed_reference_blocks_calls_before_spending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self.profile(root)
            with (
                patch(
                    "harness.web_search.runner.http_call",
                    return_value=(
                        dict(http_status=404, elapsed_ms=1, transport_exit=0, transport_error=""),
                        b"absent",
                    ),
                ) as call,
                patch("harness.web_search.runner.load_runtime_env"),
                patch.dict(os.environ, LEXMOUNT_SEARCH_API_KEY="test", DEEPSEEK_API_KEY="test", BOCHA_API_KEY="test"),
            ):
                with self.assertRaises(ValueError):
                    run(p, root / "run")
            self.assertEqual(call.call_count, 1)

    def test_failed_call_retries_once_and_only_final_response_is_scored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self.profile(root)
            profile = json.loads(p.read_text())
            profile["methods"] = [profile["methods"][1]]
            profile["protocol"]["retry"] = True
            p.write_text(json.dumps(profile))
            calls = []

            def http(url, body=None, header=None, timeout=20):
                if body is None:
                    return dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error=""), b"reference " * 20
                calls.append(url)
                if len(calls) == 1:
                    return dict(http_status=503, elapsed_ms=20, transport_exit=0, transport_error=""), b'{"error":"busy"}'
                return dict(http_status=200, elapsed_ms=30, transport_exit=0, transport_error=""), b'{"results":[{"url":"https://example.org/p"}]}'

            with (
                patch("harness.web_search.runner.http_call", side_effect=http),
                patch("harness.web_search.runner.load_runtime_env"),
                patch.dict(os.environ, DEEPSEEK_API_KEY="test"),
            ):
                run(p, root / "run")
            _, rows, _ = load_run(root / "run")
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(rows), 1)
            self.assertEqual(len(rows[0]["attempts"]), 2)
            self.assertEqual(rows[0]["results"][0]["url"], "https://example.org/p")
            self.assertGreaterEqual(rows[0]["elapsed_ms"], 50)
            first = root / "run" / rows[0]["attempts"][0]["evidence"]["path"]
            first.write_text("changed")
            with self.assertRaises(ValueError):
                load_run(root / "run")

    def test_valid_empty_response_is_a_result_not_an_api_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = self.profile(root)
            profile = json.loads(p.read_text())
            profile["methods"] = [profile["methods"][1]]
            profile["protocol"]["retry"] = True
            p.write_text(json.dumps(profile))
            calls = []

            def http(url, body=None, header=None, timeout=20):
                if body is None:
                    return dict(http_status=200, elapsed_ms=1, transport_exit=0, transport_error=""), b"reference " * 20
                calls.append(url)
                return dict(http_status=200, elapsed_ms=20, transport_exit=0, transport_error=""), b'{"results":[]}'

            with (
                patch("harness.web_search.runner.http_call", side_effect=http),
                patch("harness.web_search.runner.load_runtime_env"),
                patch.dict(os.environ, DEEPSEEK_API_KEY="test"),
            ):
                run(p, root / "run")
            _, rows, _ = load_run(root / "run")
            self.assertEqual(len(calls), 1)
            self.assertEqual(rows[0]["results"], [])


if __name__ == "__main__":
    unittest.main()


class ReferenceCaptureScenarios(unittest.TestCase):
    def test_identity_check_rejects_an_unrelated_http_200_page(self):
        from harness.web_search.runner import capture_references

        profile = {
            "protocol": {"reference_scope": "identity"},
            "cases": [{"anchors": ["https://example.org/paper"]}],
            "reference_checks": {
                "https://example.org/paper": {
                    "url": "https://example.org/paper",
                    "contains": ["Distinct paper title"],
                }
            },
        }
        receipt = dict(http_status=200, transport_exit=0)
        for raw, expected in [
            (b"login screen " * 30, False),
            (b"Distinct paper title " * 30, True),
        ]:
            with (
                tempfile.TemporaryDirectory() as tmp,
                patch("harness.web_search.runner.http_call", return_value=(receipt, raw)),
            ):
                refs = capture_references(profile, Path(tmp), "before")
                self.assertEqual(refs["https://example.org/paper"]["available"], expected)

    def test_page_chrome_changes_do_not_change_verified_identity(self):
        from harness.web_search.runner import reference_changes

        before = {"u": {"available": True, "sha256": "a"}}
        after = {"u": {"available": True, "sha256": "b"}}
        self.assertEqual(
            reference_changes({"protocol": {"reference_scope": "identity"}}, before, after), []
        )
        self.assertEqual(reference_changes({"protocol": {}}, before, after), ["u"])
        after["u"]["available"] = False
        self.assertEqual(
            reference_changes({"protocol": {"reference_scope": "identity"}}, before, after), ["u"]
        )

    def test_http_200_with_incomplete_transfer_is_not_qualified(self):
        from harness.web_search.runner import capture_references

        profile = {"protocol": {}, "cases": [{"anchors": ["https://example.org/"]}]}
        with tempfile.TemporaryDirectory() as tmp:
            receipt = dict(
                http_status=200, elapsed_ms=20000, transport_exit=28, transport_error="timeout"
            )
            with patch(
                "harness.web_search.runner.http_call", return_value=(receipt, b"partial " * 50)
            ):
                refs = capture_references(profile, Path(tmp), "before")
            self.assertFalse(refs["https://example.org/"]["available"])
