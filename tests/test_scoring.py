"""Shared scoring uses saved bytes only; all calls and references are synthetic."""
from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from harness.web_fetch.metrics import summarize, task_passed
from harness.web_fetch.models import EvalCase, ProviderResult
from harness.web_fetch.providers.cloudflare import CloudflareMarkdownProvider
from harness.web_fetch.providers.http_client import HttpResponse
from harness.web_fetch import runner
from harness.web_fetch.scoring import SCORING_SOURCE_SHA256, load_list_reference, score_attempt
from scripts import rescore_content


def case(**changes):
    return replace(EvalCase("page", "Page", "https://example.test/page", "test", []), **changes)


def stable_contract():
    return {"version": 1, "schema": "named_link", "minimum_records": 2,
            "hosts": ["example.test"], "entry_path": r"/item/\d+",
            "stable_text_anchors": [{"id": "heading", "alternatives": ["Catalog"]},
                                    {"id": "filter", "alternatives": ["Category filter"]}]}


def legacy_benchmark():
    return {"mode": "dynamic_list_structure", "hosts": ["example.test"],
            "entry_path": r"/item/\d+", "minimum_entries": 3,
            "verification_scope": "first_three_name_detail_pairs"}


LINKS = "[Alpha](/item/1)\n[Beta](/item/2)\n[Gamma](/item/3)"
REFERENCE = {"case_id": "legacy", "items": [
    {"url": f"https://example.test/item/{i}", "title": name}
    for i, name in enumerate(("Alpha", "Beta", "Gamma"), 1)]}


class ScoringTests(unittest.TestCase):
    def test_source_title_is_answer_evidence_without_inflating_body_length(self):
        c=case(required_facts=[{'id':'title','alternatives':['Exact page title']},
                              {'id':'body','alternatives':['Body fact']}],
               min_content_chars=20)
        measured=score_attempt(c,b'Body fact',ok=True,title='Exact page title')
        self.assertEqual(measured['fact_coverage'],1)
        self.assertEqual(measured['content_chars'],len('Body fact'))
        self.assertEqual(measured['title_chars'],len('Exact page title'))
        self.assertFalse(measured['length_pass'])
        self.assertFalse(task_passed(True,measured))

    def test_resource_markdown_parser_handles_balanced_multiline_links(self):
        c = case(task_kind='resource', expected_resources=[{
            'id': 'guide', 'urls': ['/files/guide(v2).pdf?download=1'],
        }])
        content = ('[Download **guide**\nnow]\n'
                   '(/files/guide(v2).pdf?download=1 "PDF copy")')
        measured = score_attempt(c, content.encode(), ok=True)
        self.assertTrue(measured['resource_pass'])
        self.assertIn('https://example.test/files/guide(v2).pdf?download=1',
                      measured['resource_candidate_urls'])

    def test_cloudflare_empty_success_is_request_success_but_not_task_success(self):
        with patch("harness.web_fetch.providers.cloudflare.request", return_value=HttpResponse(
                200, "https://example.test/api", {}, b'{"success":true,"result":""}')):
            result = CloudflareMarkdownProvider("cloudflare-markdown", {}).fetch(case(), 1)
        self.assertTrue(result.ok)
        self.assertIsNone(result.error_type)
        self.assertEqual(result.content, "")
        self.assertTrue(result.metadata["empty_content"])
        measured = score_attempt(case(required_facts=[{"alternatives": ["Alpha"]}]),
                                 result.content.encode(), ok=result.ok, metadata=result.metadata)
        self.assertFalse(task_passed(result.ok, measured))
        self.assertEqual(measured["content_check_coverage"], 0)

    def test_dataclass_and_dict_inputs_produce_same_metrics_without_mutation(self):
        c = case(required_facts=[{"id": "a", "alternatives": ["Alpha"]}])
        mapping = asdict(c)
        previous = {"deadline_overrun": True, "reference_sha256": "original",
                    "raw_file_pass": True, "file_evidence": {"sha256": "file-hash"}}
        metadata = {"images": [{"src": "/image.png"}]}
        original = deepcopy((mapping, previous, metadata))
        first = score_attempt(c, b"Alpha\r\nBeta", ok=True, previous_metrics=previous, metadata=metadata)
        self.assertEqual(first, score_attempt(mapping, b"Alpha\r\nBeta", ok=True,
                                             previous_metrics=previous, metadata=metadata))
        self.assertEqual((mapping, previous, metadata), original)
        self.assertEqual(first["content_sha256"], hashlib.sha256(b"Alpha\r\nBeta").hexdigest())
        self.assertEqual(first["content_chars"], len("Alpha\r\nBeta"))
        for key in previous:
            self.assertEqual(first[key], previous[key])

    def test_text_coverage_is_declared_groups_not_alternatives_or_word_f1(self):
        c = case(required_facts=[{"id": "a", "alternatives": ["Alpha", "Another Alpha"]},
                                {"id": "b", "alternatives": ["Beta"]}],
                 ground_truth_main_content="Alpha noise")
        measured = score_attempt(c, b"Alpha noise", ok=True)
        self.assertEqual((measured["content_check_hits"], measured["content_check_total"],
                          measured["content_check_coverage"]), (1, 2, .5))
        self.assertFalse(task_passed(True, measured))
        self.assertEqual(measured["word_f1"], 1)

    def test_stable_coverage_uses_anchors_not_old_facts_and_survives_structure_failure(self):
        c = case(acceptance_contract=stable_contract(), task_kind="structure",
                 required_facts=[{"id": "old", "alternatives": ["Old price"]}])
        measured = score_attempt(c, b"Catalog\nCategory filter", ok=True)
        self.assertFalse(measured["stable_structure_pass"])
        self.assertFalse(task_passed(True, measured))
        self.assertEqual(measured["fact_coverage"], 0)
        self.assertEqual((measured["content_check_hits"], measured["content_check_total"],
                          measured["content_check_coverage"]), (2, 2, 1))
        self.assertIsNone(measured["word_f1"])
        partial = score_attempt(c, ("Catalog\n" + LINKS).encode(), ok=True)
        self.assertTrue(partial["stable_structure_pass"])
        self.assertEqual(partial["content_check_coverage"], .5)

    def test_second_declared_truncation_notice_overrides_stable_body_contract(self):
        notice = "[Content truncated: showing 10 of 20 lines]"
        measured = score_attempt(
            case(acceptance_contract=stable_contract()),
            ("Catalog\nCategory filter\n" + LINKS + "\n" + notice).encode(),
            ok=True,
        )
        self.assertEqual(measured["declared_truncation_hits"], [notice])
        self.assertFalse(measured["content_pass"])
        self.assertFalse(measured["stable_content_pass"])
        self.assertFalse(measured["stable_task_pass"])
        self.assertIn("declared_truncation", measured["stable_task_errors"])
        self.assertFalse(task_passed(True, measured))

    def test_minimum_body_length_remains_a_gate_for_stable_contracts(self):
        body_contract = {
            "version": 1,
            "schema": "body_text",
            "minimum_records": 0,
            "stable_text_anchors": [
                {"id": "identity", "alternatives": ["Stable page identity"]},
            ],
        }
        short = score_attempt(
            case(acceptance_contract=body_contract, min_content_chars=80),
            b"Stable page identity",
            ok=True,
        )
        self.assertFalse(short["length_pass"])
        self.assertFalse(short["stable_content_pass"])
        self.assertFalse(short["stable_task_pass"])
        self.assertIn("content_too_short", short["stable_task_errors"])
        self.assertFalse(task_passed(True, short))

        complete = score_attempt(
            case(acceptance_contract=body_contract, min_content_chars=80),
            ("Stable page identity\n" + "Useful independently captured body text. " * 2).encode(),
            ok=True,
        )
        self.assertTrue(complete["length_pass"])
        self.assertTrue(complete["stable_task_pass"])
        self.assertTrue(task_passed(True, complete))

    def test_declared_truncation_notice_does_not_override_non_body_contracts(self):
        notice = "[Content truncated: showing 10 of 20 lines]"
        structure = score_attempt(
            case(task_kind="structure", acceptance_contract=stable_contract()),
            ("Catalog\nCategory filter\n" + LINKS + "\n" + notice).encode(),
            ok=True,
        )
        self.assertEqual(structure["declared_truncation_hits"], [notice])
        self.assertTrue(structure["stable_task_pass"])
        self.assertTrue(task_passed(True, structure, "structure"))

        resource = score_attempt(
            case(task_kind="resource", content_score_applicable=False,
                 expected_resources=[{"id": "image", "urls": ["/image.png"]}]),
            (notice + "\n![Image](/image.png)").encode(),
            ok=True,
        )
        self.assertEqual(resource["declared_truncation_hits"], [notice])
        self.assertTrue(resource["resource_pass"])
        self.assertTrue(task_passed(True, resource, "resource"))

    def test_exact_declared_body_regions_override_truncation_notice(self):
        notice = "[Content truncated: showing 10 of 20 lines]"
        requirements = {
            "version": 1,
            "unit": "region_token_recall",
            "minimum_coverage": 0.5,
            "regions": [{"id": "body", "text": "alpha bravo charlie delta"}],
        }
        complete = score_attempt(
            case(body_requirements=requirements,
                 ground_truth_main_content="alpha bravo charlie delta"),
            ("alpha bravo charlie delta\n" + notice).encode(), ok=True,
        )
        self.assertEqual(complete["body_region_results"][0]["coverage"], 1.0)
        self.assertTrue(complete["content_pass"])
        self.assertTrue(task_passed(True, complete))

        partial = score_attempt(
            case(body_requirements=requirements,
                 ground_truth_main_content="alpha bravo charlie delta"),
            ("alpha bravo charlie\n" + notice).encode(), ok=True,
        )
        self.assertTrue(partial["body_coverage_pass"])
        self.assertLess(partial["body_region_results"][0]["coverage"], 1.0)
        self.assertFalse(partial["content_pass"])
        self.assertFalse(task_passed(True, partial))

    def test_body_reference_can_exempt_its_standalone_captcha_heading_only(self):
        reference = "Accessibility guidance\nCAPTCHA\nProvide an equivalent text alternative."
        legitimate = score_attempt(
            case(ground_truth_main_content=reference,
                 required_facts=[{"id": "guidance", "alternatives": [
                     "Provide an equivalent text alternative"
                 ]}]),
            reference.encode(), ok=True,
        )
        self.assertEqual(legitimate["blocked_page_hits"], [])
        self.assertEqual(legitimate["blocked_page_reference_exemptions"], ["CAPTCHA"])
        self.assertTrue(task_passed(True, legitimate))

        challenge = score_attempt(
            case(ground_truth_main_content="Accessibility guidance",
                 required_facts=[]),
            b"CAPTCHA", ok=True,
        )
        self.assertEqual(challenge["blocked_page_hits"], ["CAPTCHA"])
        self.assertEqual(challenge["blocked_page_reference_exemptions"], [])
        self.assertFalse(task_passed(True, challenge))

        stable = score_attempt(
            case(ground_truth_main_content=reference,
                 acceptance_contract=stable_contract()),
            ("CAPTCHA\nCatalog\nCategory filter\n" + LINKS).encode(), ok=True,
        )
        self.assertEqual(stable["blocked_page_hits"], ["CAPTCHA"])
        self.assertEqual(stable["blocked_page_reference_exemptions"], [])
        self.assertFalse(task_passed(True, stable))

    def test_quote_fields_reject_lost_tag_boundaries(self):
        quote = "“The world as we have created it is a process of our thinking.”"
        contract = {
            "version": 1, "schema": "quote", "minimum_records": 1,
            "stable_text_anchors": [{"id": "quote-1", "alternatives": [
                quote + "\nby Albert Einstein\nTags: change deep-thoughts thinking world"]}],
        }
        facts = [
            {"id": "entry-1-quote", "alternatives": [quote]},
            {"id": "entry-1-author", "alternatives": ["Albert Einstein"]},
            {"id": "entry-1-tags", "alternatives": ["change deep-thoughts thinking world"]},
        ]
        output = (quote + "byAlbert EinsteinTags:changedeep-thoughtsthinkingworld"
                  "\n\n##### Loading...\n\nQuotes by: GoodReads.com").encode()
        measured = score_attempt(case(acceptance_contract=contract, required_facts=facts), output, ok=True)
        self.assertFalse(task_passed(True, measured))
        self.assertEqual(measured["fact_coverage"], 0)
        self.assertEqual(measured["stable_anchor_coverage"], 0)
        self.assertTrue(measured["stable_structure_pass"])
        self.assertFalse(measured["stable_content_pass"])

    def test_no_declared_groups_is_unmeasured_even_with_markers_or_old_facts(self):
        rules = stable_contract()
        rules["stable_text_anchors"] = []
        for c in (case(expected_any=["Alpha"]),
                  case(acceptance_contract=rules, required_facts=[{"alternatives": ["Alpha"]}])):
            for ok in (False, True):
                measured = score_attempt(c, LINKS.encode(), ok=ok)
                self.assertEqual(measured["content_check_hits"], 0)
                self.assertEqual(measured["content_check_total"], 0)
                self.assertIsNone(measured["content_check_coverage"])

    def test_failed_requests_zero_configured_coverage_but_keep_check_evidence(self):
        for c, text, check_key in (
            (case(required_facts=[{"alternatives": ["Alpha"]}], ground_truth_main_content="Alpha"),
             "Alpha", "fact_results"),
            (case(acceptance_contract=stable_contract()), "Catalog\nCategory filter\n" + LINKS,
             "stable_anchor_results"),
        ):
            measured = score_attempt(c, text.encode(), ok=False)
            self.assertEqual(measured["content_check_hits"], 0)
            self.assertGreater(measured["content_check_total"], 0)
            self.assertEqual(measured["content_check_coverage"], 0)
            self.assertTrue(all(c["pass"] for c in measured[check_key]))
            self.assertFalse(task_passed(False, measured))
            self.assertEqual(measured["word_f1"], 0 if c.ground_truth_main_content else None)

    def test_resource_metadata_recomputed_and_request_eligibility_applied(self):
        c = case(task_kind="resource", content_score_applicable=False,
                 expected_resources=[{"id": "image", "urls": ["/image.png"]}])
        for ok in (True, False):
            measured = score_attempt(c, b"", ok=ok, metadata={"images": [{"src": "/image.png"}]})
            self.assertEqual(task_passed(ok, measured), ok)
            self.assertEqual(measured["resource_pass"], ok)
            self.assertFalse(measured["content_pass"])

    def test_target_only_image_contract_is_part_of_shared_task_decision(self):
        c = case(
            task_kind="resource", content_score_applicable=False,
            expected_resources=[{"id": "image", "urls": ["/image.png"]}],
            resource_output_contract={
                "candidate_scope": "image_answer_fields", "unexpected": "reject",
            },
        )
        measured = score_attempt(
            c, b"", ok=True,
            metadata={"images": ["/image.png", "/recommended.png"],
                      "links": ["/ordinary-page"]},
        )
        self.assertEqual(measured["resource_recall"], 1.0)
        self.assertEqual(measured["resource_precision"], 0.5)
        self.assertEqual(measured["unexpected_resource_urls"],
                         ["https://example.test/recommended.png"])
        self.assertFalse(measured["resource_pass"])
        self.assertFalse(task_passed(True, measured))
        expected_contract = hashlib.sha256(json.dumps({
            "expected_resources": c.expected_resources,
            "resource_output_contract": c.resource_output_contract,
        }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(measured["resource_acceptance_contract_sha256"], expected_contract)

    def test_old_stable_and_list_decisions_cannot_survive_mode_change(self):
        old = {"stable_task_pass": True, "stable_anchor_results": [],
               "acceptance_contract_sha256": "old", "content_decision_basis": "old",
               "resource_acceptance_contract_sha256": "old",
               "list_task_pass": True, "list_checks": [], "structure_pass": True,
               "confirmed_entries": 3, "verification_note": "old", "raw_file_pass": True}
        measured = score_attempt(case(expected_any=["Missing"]), b"Other", ok=True, previous_metrics=old)
        self.assertFalse(task_passed(True, measured))
        for key in old.keys() - {"raw_file_pass"}:
            self.assertNotIn(key, measured)
        self.assertTrue(measured["raw_file_pass"])

    def test_legacy_list_verification_is_shared_not_just_shape(self):
        c = case(task_kind="structure", source_benchmark=legacy_benchmark())
        with self.assertRaisesRegex(ValueError, "independent list reference"):
            score_attempt(c, LINKS.encode(), ok=True)
        measured = score_attempt(c, LINKS.encode(), ok=True, list_reference=REFERENCE)
        self.assertTrue(task_passed(True, measured))
        measured = score_attempt(c, LINKS.replace("Alpha", "Different").encode(), ok=True,
                                 list_reference=REFERENCE)
        self.assertTrue(measured["structure_pass"])
        self.assertFalse(task_passed(True, measured))
        stable = replace(c, acceptance_contract=stable_contract())
        self.assertIsNone(load_list_reference(stable, Path("/does-not-exist")))
        measured = score_attempt(stable, ("Catalog\nCategory filter\n" + LINKS).encode(), ok=True)
        self.assertTrue(task_passed(True, measured))
        self.assertNotIn("list_task_pass", measured)

    def test_summary_task_predicate_controls_rate_failures_and_latency(self):
        rows = []
        examples = [
            ("body", {"content_pass": True}, True),
            ("resource", {"content_pass": False, "resource_pass": True}, True),
            ("file", {"content_pass": False, "raw_file_pass": True}, True),
            ("structure", {"content_pass": True, "list_task_pass": False}, False),
            ("body", {"content_pass": True, "stable_task_pass": False}, False),
            ("file", {"content_pass": True}, False),
        ]
        for i, (kind, fields, passed) in enumerate(examples):
            row = {"provider": "p", "provider_type": "fixture", "case_id": str(i),
                   "case_name": str(i), "category": "test", "ok": True, "elapsed_ms": 10 if passed else 900,
                   "metrics": {"task_kind": kind, "marker_recall": 1, "content_chars": 10, **fields}}
            self.assertEqual(task_passed(True, row["metrics"]), passed)
            rows.append(row)
        rows.append({**rows[0], "case_id": "failed", "ok": False, "error_type": "timeout"})
        result = summarize(rows)
        provider = result["providers"]["p"]
        self.assertEqual(provider["task_pass_rate"], round(3 / 6, 4))
        self.assertEqual(provider['task_unmeasured_attempts'], 1)
        self.assertNotIn("content_pass_rate", provider)
        self.assertNotIn("conditional_content_pass_rate", provider)
        for field in ("mean_content_ms", "p50_ms", "p95_ms"):
            self.assertEqual(provider[field], 10)
        self.assertEqual(provider["failure_types"], {"content_validation_failure": 2, "timeout": 1})
        for i, (_, _, passed) in enumerate(examples):
            self.assertEqual(result["cases"][str(i)]["task_pass_rate"], None if i == 5 else float(passed))
        # Explicit case context remains authoritative over stored summary hints.
        self.assertFalse(task_passed(True, rows[1]["metrics"], "body"))

    def test_source_hash_includes_shared_helper(self):
        from harness.web_fetch import scoring
        root = Path(scoring.__file__).parent
        expected = hashlib.sha256(b"\0".join((root / name).read_bytes() for name in (
            "scoring.py", "metrics.py", "stable_tasks.py", "dynamic_lists.py",
            "list_verification.py", "reference_quality.py", "dynamic_entry_matching.py",
            "response_input.py", "body_coverage.py", "structured_tables.py"))).hexdigest()
        self.assertEqual(SCORING_SOURCE_SHA256, expected)
        self.assertEqual(score_attempt(case(), b"Text", ok=True)["scoring_source_sha256"], expected)


class ScoringPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / "run"
        self.cases = [
            case(id="web", required_facts=[{"alternatives": ["Alpha"]}], ground_truth_main_content="Alpha"),
            case(id="dynamic", track="dynamic", acceptance_contract=stable_contract()),
            case(id="challenge", track="challenge", task_kind="structure", acceptance_contract=stable_contract()),
            case(id="document", track="document", required_facts=[{"alternatives": ["Missing"]}],
                 ground_truth_main_content="Actual document"),
            case(id="resource", task_kind="resource", content_score_applicable=False,
                 expected_resources=[{"urls": ["/image.png"]}]),
            case(id="file", task_kind="file"),
            case(id="failed", ground_truth_main_content="Alpha", required_facts=[{"alternatives": ["Alpha"]}]),
            case(id="legacy", task_kind="structure", source_benchmark=legacy_benchmark()),
            case(id="unverified", task_kind="structure", source_benchmark=legacy_benchmark()),
        ]
        self.dataset = {"id": "test", "tool": "web_fetch", "cases": [asdict(c) for c in self.cases]}
        self.path = self.root / "dataset.json"
        self.path.write_text(json.dumps(self.dataset))
        refs = self.root / "list-references"
        refs.mkdir()
        for name in ("legacy", "unverified"):
            (refs / f"{name}.json").write_text(json.dumps({**REFERENCE, "case_id": name}))
        texts = {"web": "Alpha\r\n", "dynamic": "Catalog\nCategory filter\n" + LINKS,
                 "challenge": "Catalog\n" + LINKS, "document": "Actual document",
                 "resource": "", "file": "This is not verified file bytes", "failed": "Alpha",
                 "legacy": LINKS, "unverified": LINKS.replace("Alpha", "Different")}
        self.provider = Mock()
        self.provider.name, self.provider.type, self.provider.min_interval_seconds = "p", "fixture", 0
        self.provider.validate.return_value = []
        self.provider.fetch.side_effect = lambda c, timeout: ProviderResult(
            ok=c.id != "failed", content=texts[c.id], status_code=200 if c.id != "failed" else 502,
            error_type="synthetic_failure" if c.id == "failed" else None,
            metadata={"images": [{"src": "/image.png"}], "request_id": "original-" + c.id})

    def live(self):
        with redirect_stdout(io.StringIO()):
            return runner.run_evaluation(output_dir=self.run, dataset_meta=self.dataset, dataset_path=self.path,
                cases=self.cases, providers=[self.provider], provider_config_path=self.root / "providers.json",
                runs=1, timeout=1, delay=0)

    def prepare_script_references(self):
        refs = self.run / "list-references"
        refs.mkdir()
        for name in ("legacy", "unverified"):
            (refs / f"{name}.json").write_bytes((self.root / "list-references" / f"{name}.json").read_bytes())

    def test_live_and_both_rescores_share_scores_without_changing_call_evidence(self):
        with patch.object(runner, "score_attempt", wraps=score_attempt) as shared:
            original, summary = self.live()
            rescored, resummary = runner.rescore_evaluation(run_dir=self.run, dataset_meta=self.dataset,
                                                           dataset_path=self.path, cases=self.cases)
        self.assertEqual(shared.call_count, len(self.cases) * 2)
        self.assertEqual(rescored, original)
        self.assertEqual(resummary, summary)
        self.prepare_script_references()
        manifest = (self.run / "manifest.json").read_bytes()
        artifacts = {r["artifact_path"]: (self.run / r["artifact_path"]).read_bytes() for r in original}
        self.provider.fetch.side_effect = AssertionError("Rescoring must not call providers")
        with patch.object(rescore_content, "score_attempt", wraps=score_attempt) as shared:
            changes = rescore_content.rescore(self.run)
        self.assertEqual(shared.call_count, len(self.cases))
        self.assertEqual(changes, [])
        current = [json.loads(line) for line in (self.run / "results.jsonl").read_text().splitlines()]
        self.assertEqual(summarize(current), summary)
        for before, after in zip(original, current):
            self.assertEqual(before["metrics"], after["metrics"])
            for key in before:
                self.assertEqual(before[key], after[key], key)
            self.assertEqual((self.run / after["artifact_path"]).read_bytes(), artifacts[after["artifact_path"]])
        self.assertEqual((self.run / "manifest.json").read_bytes(), manifest)
        self.assertEqual(self.provider.fetch.call_count, len(self.cases))
        by_id = {r["case_id"]: r for r in current}
        self.assertTrue(task_passed(True, by_id["resource"]["metrics"]))
        self.assertFalse(task_passed(True, by_id["file"]["metrics"]))
        self.assertFalse(task_passed(True, by_id["unverified"]["metrics"]))
        self.assertEqual(by_id["document"]["metrics"]["word_f1"], 1)
        self.assertIsNone(by_id["challenge"]["metrics"]["word_f1"])
        self.assertEqual(by_id["challenge"]["metrics"]["content_check_coverage"], .5)
        self.assertEqual(by_id["failed"]["metrics"]["content_check_coverage"], 0)

    def test_script_selection_preserves_unselected_records_and_original_run_id(self):
        records, _ = self.live()
        records[0]["run_id"] = "original-call-identity"
        (self.run / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
        self.dataset["cases"][0]["required_facts"] = [{"alternatives": ["Missing"]}]
        (self.run / "dataset.json").write_text(json.dumps(self.dataset))
        changes = rescore_content.rescore(self.run, case_ids={"web"})
        updated = [json.loads(line) for line in (self.run / "results.jsonl").read_text().splitlines()]
        self.assertEqual(updated[1:], records[1:])
        self.assertEqual(updated[0]["run_id"], "original-call-identity")
        self.assertEqual(updated[0]["metrics"]["content_check_coverage"], 0)
        self.assertEqual(changes, [{"attempt_id": records[0]["attempt_id"], "before": True, "after": False}])

    def test_each_rescore_updates_audit_without_erasing_existing_scoring_or_call_identity(self):
        records, _ = self.live()
        preserved = {"rule_sha256": "original-rule", "previous_task_pass": False,
                     "word_metric_scope": "captured_sample_diagnostic", "custom": {"keep": True}}
        records[0]["scoring"] = {**preserved, "mode": "previous-mode", "scored_at": "old-time",
                                  "source_sha256": "old-source"}
        path = self.run / "results.jsonl"
        path.write_text("".join(json.dumps(r) + "\n" for r in records))
        original = deepcopy(records[0])
        original_manifest = (self.run / "manifest.json").read_bytes()
        original_dataset = (self.run / "dataset.json").read_bytes()
        for second in (1, 2):
            before_bytes = path.read_bytes()
            now = datetime(2030, 1, 1, 0, 0, second, tzinfo=timezone.utc)
            with patch.object(rescore_content, "datetime") as clock:
                clock.now.return_value = now
                self.assertEqual(rescore_content.rescore(self.run, case_ids={"web"}), [])
            updated = [json.loads(line) for line in path.read_text().splitlines()]
            row = updated[0]
            self.assertEqual(updated[1:], records[1:])
            for key in original.keys() - {"scoring"}:
                self.assertEqual(row[key], original[key], key)
            audit = row["scoring"]
            for key, value in preserved.items():
                self.assertEqual(audit[key], value)
            self.assertEqual(audit["mode"], "offline_rescore")
            self.assertEqual(audit["scored_at"], now.isoformat())
            self.assertEqual(audit["request_status_basis"], "shared_normalization_with_original_adapter_status_retained")
            self.assertEqual(audit["input_results_sha256"], hashlib.sha256(before_bytes).hexdigest())
            self.assertEqual(audit["dataset_sha256"], hashlib.sha256(original_dataset).hexdigest())
            expected_source_hash = hashlib.sha256(b'\0'.join([
                Path(rescore_content.__file__).read_bytes(), SCORING_SOURCE_SHA256.encode('ascii'),
                (rescore_content.ROOT/'harness/web_fetch/failure_diagnosis.py').read_bytes(),
                (rescore_content.ROOT/'harness/web_fetch/report.py').read_bytes(),
            ])).hexdigest()
            self.assertEqual(audit["source_sha256"], expected_source_hash)
        self.assertEqual((self.run / "manifest.json").read_bytes(), original_manifest)
        self.assertEqual((self.run / "dataset.json").read_bytes(), original_dataset)

    def test_historical_empty_response_remains_original_in_live_and_both_rescores(self):
        self.provider.name = "cloudflare-markdown"
        raw = '{"success":true,"result":""}'
        self.provider.fetch.side_effect = lambda c, timeout: ProviderResult(
            ok=False, content=raw, status_code=200, error_type="api_error", error_message="Historical error",
            metadata={"request_id": "original-id"})
        original, summary = self.live()
        rescored, new_summary = runner.rescore_evaluation(run_dir=self.run, dataset_meta=self.dataset,
                                                        dataset_path=self.path, cases=self.cases)
        self.assertEqual(rescored, original)
        self.assertEqual(summary, new_summary)
        rescore_content.rescore(self.run, case_ids={"web"})
        row = json.loads((self.run / "results.jsonl").read_text().splitlines()[0])
        for key in original[0]:
            self.assertEqual(row[key], original[0][key], key)
        self.assertTrue(row["ok"])
        self.assertFalse(row['raw_request_status']['ok'])
        self.assertEqual(row['raw_request_status']['error_message'], 'Historical error')
        self.assertEqual(row["scoring"]["request_status_basis"], "shared_normalization_with_original_adapter_status_retained")
        self.assertIsNone(row['error_type'])

    def test_script_rebuilds_diagnosis_from_saved_nested_error_payload(self):
        self.provider.fetch.side_effect = lambda c, timeout: ProviderResult(
            ok=False, status_code=502, error_type="api_error", content=json.dumps({
                "error": {"upstream_status_code": 403, "message": "Source denied this request"}}))
        self.live()
        rescore_content.rescore(self.run, case_ids={"web"})
        row = json.loads((self.run / "results.jsonl").read_text().splitlines()[0])
        self.assertEqual(row["diagnosis"]["category"], "源站拒绝该次访问")
        self.assertEqual(row["failure_category"], row["diagnosis"]["category"])
        self.assertFalse(row["ok"])
        self.assertEqual(row["status_code"], 502)

    def test_empty_selection_does_not_rewrite_records_or_add_audit(self):
        self.live()
        path = self.run / "results.jsonl"
        before = path.read_bytes()
        self.assertEqual(rescore_content.rescore(self.run, case_ids=set()), [])
        self.assertEqual(path.read_bytes(), before)

    def test_concurrent_results_change_is_not_overwritten(self):
        self.live()
        path = self.run / "results.jsonl"
        concurrent = path.read_bytes() + b"\n"

        def concurrent_score(*args, **kwargs):
            path.write_bytes(concurrent)
            return score_attempt(*args, **kwargs)

        with patch.object(rescore_content, "score_attempt", side_effect=concurrent_score):
            with self.assertRaisesRegex(ValueError, "inputs changed"):
                rescore_content.rescore(self.run, case_ids={"web"})
        self.assertEqual(path.read_bytes(), concurrent)

    def test_both_rescores_retain_external_file_evidence_and_original_deadline(self):
        records, _ = self.live()
        original = next(r for r in records if r["case_id"] == "file")
        original["metrics"].update(raw_file_pass=True, raw_file_sha256="independently-verified-file",
                                   deadline_overrun=True)
        (self.run / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
        rescored, summary = runner.rescore_evaluation(run_dir=self.run, dataset_meta=self.dataset,
                                                     dataset_path=self.path, cases=self.cases)
        file_row = next(r for r in rescored if r["case_id"] == "file")
        self.assertEqual(file_row, original)
        self.assertEqual(summary["cases"]["file"]["task_pass_rate"], 0)
        rescore_content.rescore(self.run, case_ids={"file"})
        script_rows = [json.loads(line) for line in (self.run / "results.jsonl").read_text().splitlines()]
        file_row = next(r for r in script_rows if r["case_id"] == "file")
        self.assertEqual(file_row["metrics"], original["metrics"])
        self.assertEqual(summarize(script_rows)["cases"]["file"]["task_pass_rate"], 0)

    def test_both_rescores_reject_modified_output_without_writing_results(self):
        records, _ = self.live()
        path = self.run / "results.jsonl"
        before = path.read_bytes()
        (self.run / records[0]["artifact_path"]).write_text("Tampered")
        for rescore in (
            lambda: runner.rescore_evaluation(run_dir=self.run, dataset_meta=self.dataset,
                                              dataset_path=self.path, cases=self.cases),
            lambda: rescore_content.rescore(self.run, case_ids={"web"}),
        ):
            with self.assertRaisesRegex(ValueError, "integrity|Public call log output binding changed"):
                rescore()
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
