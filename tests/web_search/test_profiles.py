"""Current selections preserve old inputs and bind formal publication to evidence."""

import json
import tempfile
import unittest
from pathlib import Path

from harness.web_search.core import digest
from harness.web_search.profiles import load_profile_bytes, publication_authority


class CurrentProfileScenarios(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        profile = {
            "schema": "web-search/1", "title": "Frozen", "publication": {"expected_version": "historical"},
            "protocol": {"top_k": 5, "rounds": 1, "score_round": 0, "concurrency": 1, "server_timeout_ms": 1000},
            "methods": [{"id": mid, "adapter": adapter, "endpoint": "https://example.org/" + mid}
                        for mid, adapter in [("lexmount", "lexmount"), ("base", "deepseek"), ("body", "deepseek"), ("summary", "bocha")]],
            "cases": [{"id": "topic", "query": "Topic", "family": "web"}],
        }
        self.historical = self.root / "historical.json"
        self.historical.write_text(json.dumps(profile, indent=3) + "\n")
        self.raw = self.historical.read_bytes()
        self.binding = {"path": "historical.json", "sha256": digest(self.raw)}
        self.entry = self.root / "current.json"
        self.selection = {"schema": "web-search-current/1", "title": "Current", "profile": self.binding,
                          "methods": ["lexmount", "body", "summary"], "supplementary_profiles": [],
                          "publication_target": "target.json"}
        self.entry.write_text(json.dumps(self.selection))
        self.target = {"base_url": "https://reports.example.org", "slug": "search", "versionId": "live-version",
                       "url": "https://reports.example.org/s/search", "methods": self.selection["methods"],
                       "bindings": {"primary_profile": self.binding, "primary_receipt": {"path": "evidence/receipt.json", "sha256": "receipt-hash"}}}
        self.save_target()
        self.manifest = {"profile": {"methods": [{"id": mid} for mid in self.selection["methods"]]},
                         "receipt": {"separate_collections": [{"cohort": "primary", "profile_sha256": self.binding["sha256"], "receipt_sha256": "receipt-hash"}]}}

    def save_target(self):
        (self.root / "target.json").write_text(json.dumps(self.target))

    def test_current_collection_freezes_a_new_three_method_identity(self):
        raw = load_profile_bytes(self.entry, root=self.root)
        profile = json.loads(raw)
        self.assertEqual([m["id"] for m in profile["methods"]], ["lexmount", "body", "summary"])
        self.assertEqual(profile["cases"], json.loads(self.raw)["cases"])
        self.assertNotEqual(digest(raw), digest(self.raw))
        self.assertEqual(self.historical.read_bytes(), self.raw)
        self.assertEqual(load_profile_bytes(self.historical, root=self.root), self.raw)
        self.assertEqual(profile["publication"], {"authority": "target.json"})

    def test_formal_release_accepts_a_new_run_of_the_resolved_current_profile(self):
        self.manifest["receipt"]["separate_collections"][0]["profile_sha256"] = digest(load_profile_bytes(self.entry, root=self.root))
        self.assertEqual(publication_authority(self.manifest, self.entry, root=self.root)["slug"], "search")

    def test_current_selection_rejects_changed_historical_input(self):
        self.historical.write_bytes(self.raw + b" ")
        with self.assertRaisesRegex(ValueError, "binding changed"):
            load_profile_bytes(self.entry, root=self.root)

    def test_current_selection_rejects_unknown_or_duplicate_methods(self):
        for ids in [["missing"], ["lexmount", "lexmount"]]:
            with self.subTest(ids=ids):
                self.entry.write_text(json.dumps(dict(self.selection, methods=ids)))
                with self.assertRaisesRegex(ValueError, "method selection"):
                    load_profile_bytes(self.entry, root=self.root)

    def test_current_selection_rejects_traversal_and_symlink_inputs(self):
        for path in ["../historical.json", str(self.historical)]:
            with self.subTest(path=path):
                self.entry.write_text(json.dumps(dict(self.selection, profile=dict(self.binding, path=path))))
                with self.assertRaises(ValueError):
                    load_profile_bytes(self.entry, root=self.root)
        (self.root / "alias.json").symlink_to(self.historical)
        self.entry.write_text(json.dumps(dict(self.selection, profile=dict(self.binding, path="alias.json"))))
        with self.assertRaisesRegex(ValueError, "symlink"):
            load_profile_bytes(self.entry, root=self.root)

    def test_formal_release_uses_the_live_target_and_exact_selection(self):
        actual = publication_authority(self.manifest, self.entry, root=self.root)
        self.assertEqual(actual["versionId"], "live-version")
        self.manifest["profile"]["methods"].append({"id": "base"})
        with self.assertRaisesRegex(ValueError, "participants"):
            publication_authority(self.manifest, self.entry, root=self.root)

    def test_formal_release_rejects_different_cohort_profile_or_receipt(self):
        for key in ["profile_sha256", "receipt_sha256"]:
            with self.subTest(key=key):
                manifest = json.loads(json.dumps(self.manifest))
                manifest["receipt"]["separate_collections"][0][key] = "different"
                with self.assertRaisesRegex(ValueError, "different collection"):
                    publication_authority(manifest, self.entry, root=self.root)
        self.manifest["receipt"]["separate_collections"].append(dict(self.manifest["receipt"]["separate_collections"][0]))
        with self.assertRaisesRegex(ValueError, "cohorts"):
            publication_authority(self.manifest, self.entry, root=self.root)

    def test_formal_release_rejects_wrong_host_url_or_target_binding(self):
        for change in [{"base_url": "http://reports.example.org"}, {"url": "https://other.example.org/s/search"}, {"versionId": ""}]:
            with self.subTest(change=change):
                original = dict(self.target)
                self.target.update(change)
                self.save_target()
                with self.assertRaises(ValueError):
                    publication_authority(self.manifest, self.entry, root=self.root)
                self.target = original
        self.target["bindings"]["primary_profile"] = dict(self.binding, path="another.json")
        self.save_target()
        with self.assertRaisesRegex(ValueError, "target profile"):
            publication_authority(self.manifest, self.entry, root=self.root)


    def test_full_review_requirement_cannot_be_dropped_at_formal_release(self):
        self.selection['independent_result_review_scope'] = 'all'
        self.entry.write_text(json.dumps(self.selection))
        resolved = json.loads(load_profile_bytes(self.entry, root=self.root))
        self.assertEqual(resolved['independent_result_review_scope'], 'all')
        with self.assertRaisesRegex(ValueError, 'complete independent'):
            publication_authority(self.manifest, self.entry, root=self.root)
        self.manifest['profile']['independent_result_review_scope'] = 'all'
        self.manifest['independent_review_sha256'] = 'bound-review'
        self.manifest['report_assessment'] = {'independent_review_scope': 'all'}
        self.assertEqual(publication_authority(self.manifest, self.entry, root=self.root)['slug'], 'search')
