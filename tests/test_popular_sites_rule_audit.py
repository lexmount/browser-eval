from pathlib import Path
import hashlib
import json
import unittest

from scripts.audit_popular_sites_rules_v2 import match_anchor, projected_answer
from harness.web_fetch.stable_tasks import evaluate_stable_task


ROOT = Path(__file__).resolve().parents[1]


class PopularSitesRuleAuditTests(unittest.TestCase):
    def test_image_metadata_and_document_title_are_diagnostic_only(self) -> None:
        round_ = {
            "round": "one",
            "state": "captured",
            "title": "Example Brand",
            "visible_text": "",
            "elements": [],
            "controls": [],
            "links": [{
                "url": "https://example.com/item/1",
                "text": "",
                "aria_label": "",
                "title": "",
                "image_alt": "Example Brand",
            }],
            "images": [{"alt": "Example Brand", "aria_label": "", "title": ""}],
        }
        anchor = {"id": "brand", "alternatives": ["Example Brand"]}
        observation = match_anchor(anchor, round_)
        self.assertFalse(observation["matched"])
        self.assertTrue(observation["title_only"])
        self.assertEqual(projected_answer(round_), "")

    def test_audit_scope_and_anchor_exception_policy_are_explicit(self) -> None:
        audit = json.loads((ROOT / "research/popular-sites-rules-audit-v2.json").read_text())
        dataset = json.loads((ROOT / "evaluation/popular-sites-dataset.json").read_text())
        cases = {case["id"]: case for case in dataset["cases"]}
        corrections = audit["corrections"]
        blockers = audit["blockers"]
        self.assertEqual(audit["reviewed_case_count"], 100)
        self.assertEqual(len(corrections), audit["summary"]["proposed_rule_corrections"])
        self.assertEqual(len(blockers), audit["summary"]["blocked_cases"])
        self.assertEqual(len({row["case_id"] for row in corrections}), len(corrections))
        self.assertEqual(len({row["case_id"] for row in blockers}), len(blockers))
        self.assertFalse(audit["policy"]["provider_outputs_used"])
        self.assertFalse(audit["policy"]["ocr_or_pixel_text_allowed_as_scoring_evidence"])
        self.assertEqual(audit["policy"]["page_identity_preferred_minimum_groups"], 3)
        self.assertEqual(audit["summary"]["page_identity_exceptions_under_three_after_proposed_corrections"], 0)
        self.assertEqual(
            {row["case_id"] for row in blockers},
            {row["case_id"] for row in audit["decision_required"]},
        )
        disposition = audit["case_disposition"]
        keep = set(disposition["keep_current_rule"])
        modify = set(disposition["modify_rule"])
        decision = set(disposition["decision_required"])
        approved = set(disposition.get("approved_contract_change", []))
        self.assertEqual(modify, {row["case_id"] for row in corrections})
        self.assertEqual(decision, {row["case_id"] for row in blockers})
        self.assertEqual(approved, {row["case_id"] for row in audit["approved_contract_changes"]})
        self.assertFalse(keep & modify)
        self.assertFalse(keep & decision)
        self.assertFalse(keep & approved)
        self.assertFalse(modify & approved)
        self.assertEqual(keep | modify | decision | approved, set(cases))
        self.assertEqual(disposition["page_identity_exceptions"], [])
        for row in corrections:
            groups = row.get("stable_text_groups")
            if groups is not None and cases[row["case_id"]]["acceptance_contract"]["schema"] == "page_identity":
                self.assertGreaterEqual(len(groups), 3, row["case_id"])

    def test_ordinary_browser_recheck_is_provider_free_and_three_rounds(self) -> None:
        evidence = json.loads(
            (ROOT / "research/popular-sites-ordinary-browser-recheck-2026-09-15.json").read_text()
        )
        self.assertFalse(evidence["provider_outputs_read"])
        self.assertEqual(evidence["provider_calls"], 0)
        cases = {row["case_id"]: row for row in evidence["cases"]}
        self.assertEqual(len(cases["challenge-01"]["rounds"]), 3)
        self.assertEqual(len(cases["challenge-09"]["rounds"]), 3)

    def test_publication_hold_state_matches_release_target(self) -> None:
        hold = json.loads((ROOT / "evaluation/publication-hold.json").read_text())
        profile = json.loads((ROOT / "evaluation/current-report.json").read_text())
        self.assertEqual(hold["dataset_id"], "popular-sites")
        if hold["active"]:
            self.assertIsNone(profile["artifact_hub_site"])
        else:
            self.assertEqual(profile["publication_state"], "measured_independently_reviewed")
            self.assertTrue(profile["artifact_hub_site"])

    def test_bounded_url_search_preserves_named_link_goals_and_gate(self) -> None:
        evidence = json.loads(
            (ROOT / "research/popular-sites-bounded-url-search-2026-09-15.json").read_text()
        )
        self.assertFalse(evidence["guardrails"]["provider_outputs_read"])
        self.assertEqual(evidence["guardrails"]["provider_calls"], 0)
        self.assertFalse(evidence["guardrails"]["formal_rules_changed"])
        searches = {row["case_id"]: row for row in evidence["existing_case_searches"]}
        self.assertEqual(set(searches), {"challenge-02", "challenge-04", "challenge-06"})
        self.assertIn("product names", searches["challenge-02"]["goal_preserved"])
        self.assertIn("question names", searches["challenge-04"]["goal_preserved"])
        self.assertIn("shop names", searches["challenge-06"]["goal_preserved"])
        self.assertEqual(len(evidence["dianping_replacement_candidates"]), 2)
        self.assertEqual(evidence["approval_state"]["huaban_boards"], "approved by user")
        self.assertEqual(evidence["approval_state"]["publication_hold"], "active")

    def test_freeze_receipt_declares_exact_rerun_before_calls(self) -> None:
        receipt = json.loads(
            (ROOT / "research/popular-sites-rules-v2-freeze-receipt.json").read_text()
        )
        scope = receipt["change_scope"]
        self.assertEqual(scope["changed_cases"], 24)
        self.assertEqual(len(scope["score_only_cases"]), 21)
        self.assertEqual(scope["provider_rerun_cases"], ["challenge-02", "challenge-04", "challenge-06"])
        self.assertEqual(len(scope["provider_rerun_methods"]), 8)
        self.assertEqual(scope["provider_rerun_call_count"], 24)
        self.assertFalse(receipt["guardrails"]["provider_rerun_authorized"])
        self.assertTrue(receipt["guardrails"]["publication_hold_active"])
        correction = json.loads(
            (ROOT / "research/popular-sites-rules-v2-post-freeze-correction-2026-09-15.json").read_text()
        )
        for key in ("dataset", "rules"):
            self.assertEqual(
                receipt["frozen"][f"{key}_sha256"],
                correction["predecessor"][f"{key}_sha256"],
            )
            path = ROOT / correction["corrected"][key]
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                correction["corrected"][f"{key}_sha256"],
            )
        for key in ("stage_contract", "final_contract"):
            path = ROOT / receipt["frozen"][key]
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(),
                receipt["frozen"][f"{key}_sha256"],
            )

    def test_wedoany_relative_detail_links_accept_both_observed_hosts(self) -> None:
        dataset = json.loads((ROOT / "evaluation/popular-sites-dataset.json").read_text())
        case = next(row for row in dataset["cases"] if row["id"] == "china-homepage-046-wedoany-com")
        evidence = json.loads(
            (ROOT / "evaluation/sources/china-homepage-046-wedoany-com/evidence.json").read_text()
        )
        self.assertEqual(case["url"], "https://wedoany.com/")
        self.assertEqual(evidence["observed_final_url"], "https://www.wedoany.com/")
        self.assertEqual(set(case["acceptance_contract"]["hosts"]), {"wedoany.com", "www.wedoany.com"})
        synthetic = (
            "维度网 工程分类 产品 选型总库\n"
            "[Alpha](/product/1.html)\n"
            "[Beta](/product/2.html)\n"
            "[Gamma](/product/3.html)"
        )
        result = evaluate_stable_task(
            synthetic, case["acceptance_contract"], case["url"]
        )
        self.assertTrue(result["stable_task_pass"], result)

    def test_superseded_builder_fails_closed_after_v2_freeze(self) -> None:
        source = (ROOT / "scripts/build_popular_sites_suite.py").read_text()
        self.assertIn("V2_FREEZE_RECEIPT.is_file()", source)
        self.assertIn("refusing to rebuild the superseded predecessor", source)


if __name__ == "__main__":
    unittest.main()
