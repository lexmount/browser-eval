from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from harness.web_fetch.refresh_assembly import assemble, expected_refresh_pairs
from harness.web_fetch.speed_cohort import build_summary as build_speed_summary
from harness.web_fetch.reference_lifecycle import reference_revision_cases, valid_scoring_revision
from harness.web_fetch.scoring_regressions import build_correction_provenance

PROVIDERS = [
    "cloudflare-markdown",
    "cowagent-built-in",
    "crawl4ai",
    "deepseek-harness-built-in",
    "exa",
    "firecrawl",
    "langchain-webbase",
    "lexmount-cn",
    "lexmount-global",
    "moli-webfetch",
    "openclaw-built-in",
    "tavily",
]
RERUN_CASES = [
    "stable-wikipedia-ai-revision",
    "stable-browser-use-readme",
    "stable-openclaw-readme",
    "stable-nodejs-readme",
    "stable-wcag21-recommendation",
    "stable-xml10-recommendation",
    "stable-rfc-6455-websocket",
    "stable-rfc-8259-json",
    "document-03",
    "live-025-api-reference",
    "live-033-nasa-science",
    "live-041-python-programming-language",
]
REPLACEMENTS = {
    "english-encyclopedia": "stable-wikipedia-ai-revision",
    "live-011-navigation-menu": "stable-browser-use-readme",
    "live-018-navigation-menu": "stable-openclaw-readme",
    "live-020-navigation-menu": "stable-nodejs-readme",
    "live-023-guides": "stable-wcag21-recommendation",
    "lm-pnpm-install": "stable-rfc-6455-websocket",
    "lm-vite-guide": "stable-rfc-8259-json",
    "live-031-agent": "stable-xml10-recommendation",
}
SPEED_CASES = [
    "live-010-navigation-menu",
    "live-071-science",
    "live-022-until-september-10-get-pycharm-f",
    "live-040-documentation-overview",
    "lm-feishu-help",
    "lexmount-doc",
    "lm-node-intro",
    "live-046-file-api",
    "live-051-email-an-email-and-mime-handling",
    "live-052-objects-in-kubernetes",
    "live-026-getting-started",
    "live-027-sponsors",
    "live-025-api-reference",
    "stable-rfc-8259-json",
    "live-024-cascading-style-sheets-level-2-r",
    "live-061-version-control",
    "live-028-getting-started",
    "live-029-fetch",
    "live-032-api-reference",
    "live-033-nasa-science",
]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class AssembleWebfetchRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        required = list(dict.fromkeys(RERUN_CASES + SPEED_CASES + ["lm-rust-learn", "document-05"]))
        required.extend(f"filler-{index:03d}" for index in range(150 - len(required)))
        self.canonical_cases = [
            {
                "id": case_id,
                "name": case_id,
                "url": f"https://example.test/{case_id}",
                "track": "web",
                "task_kind": "body",
            }
            for case_id in required
        ]
        self.canonical = {
            "schema_version": 2,
            "id": "webfetch",
            "cases": self.canonical_cases,
            "coverage_plan": {"web": PROVIDERS},
            "comparison": {
                "case_replacements": REPLACEMENTS,
                "reference_binding_mode": "equivalent_reference_bytes_with_independent_batch_captures",
            },
        }
        self.canonical_path = self.root / "evaluation" / "dataset.json"
        self.canonical_path.parent.mkdir()
        self.write_json(self.canonical_path, self.canonical)
        (self.canonical_path.parent / "sources").mkdir()
        (self.canonical_path.parent / "list-references").mkdir()

        baseline_cases = []
        reverse = {new: old for old, new in REPLACEMENTS.items()}
        for case in self.canonical_cases:
            replacement = reverse.get(case["id"])
            baseline_cases.append(
                {
                    **case,
                    "id": replacement or case["id"],
                    "name": replacement or case["name"],
                    "url": f"https://old.example.test/{replacement}"
                    if replacement
                    else case["url"],
                }
            )
        self.baseline_dataset = {
            "schema_version": 1,
            "id": "webfetch",
            "cases": baseline_cases,
            "coverage_plan": {"web": PROVIDERS},
        }
        self.baseline = self.root / "baseline"
        baseline_rows = self.rows(PROVIDERS, [case["id"] for case in baseline_cases])
        self.write_bundle(self.baseline, self.baseline_dataset, baseline_rows)

        self.plan = {
            "schema_version": 2,
            "baseline": {
                "dataset_sha256": sha((self.baseline / "dataset.json").read_bytes()),
                "results_sha256": sha((self.baseline / "results.jsonl").read_bytes()),
                "logical_attempts": 1800,
            },
            "main_matrix": {
                "full_rerun_providers": ["crawl4ai", "langchain-webbase"],
                "full_rerun_reason_by_provider": {
                    provider: {"category": "provider_route", "reason": "changed route"}
                    for provider in ("crawl4ai", "langchain-webbase")
                },
                "matched_case_ids": list(RERUN_CASES),
                "matched_case_reason_groups": [{
                    "case_ids": list(RERUN_CASES),
                    "category": "input_contract", "reason": "changed shared input",
                }],
                "pair_replacements": [
                    {
                        "provider": "cowagent-built-in", "case_id": "lm-rust-learn",
                        "category": "provider_case_route", "reason": "changed route",
                    },
                    {
                        "provider": "cowagent-built-in", "case_id": "document-05",
                        "category": "provider_case_route", "reason": "changed route",
                    },
                ],
                "offline_rescore_cases": [{
                    "case_id": "filler-000", "category": "reference_only_correction",
                    "reason": "rescore immutable attempts", "attempts": 12,
                    "full_review": True,
                }],
                "planned_new_calls": 422,
                "final_logical_attempts": 1800,
            },
            "same_window_speed_cohort": {
                "required": False,
                "reuse_existing": True,
                "reason": "reuse the certified prior cohort",
                "dataset_sha256": "pending",
                "results_sha256": "pending",
                "execution_sha256": "pending",
                "speed_comparison_sha256": "pending",
                "auxiliary_sha256": {},
                "planned_new_calls": 0,
            },
            "total_planned_new_calls": 422,
        }
        self.plan_path = self.root / "evaluation" / "current-rerun.json"
        self.write_json(self.plan_path, self.plan)

        expected = expected_refresh_pairs(self.plan, self.canonical)
        self.main = self.root / "main-rerun"
        main_rows = self.rows_for_pairs(expected)
        self.write_bundle(self.main, self.canonical, main_rows)

        self.speed = self.root / "speed"
        speed_rows = self.rows(PROVIDERS, SPEED_CASES)
        speed_dataset = {
            **self.canonical,
            "cases": [case for case in self.canonical_cases if case["id"] in SPEED_CASES],
            "comparison": {"require_independent_result_review": True},
        }
        speed_case_order = [case["id"] for case in speed_dataset["cases"]]
        self.write_bundle(self.speed, speed_dataset, speed_rows)
        speed_batches = []
        for provider in PROVIDERS:
            attempt_ids = [f"{provider}__{case_id}__r001" for case_id in speed_case_order]
            speed_batches.append({
                "providers": [{"name": provider}],
                "started_at": "2026-09-10T00:00:00+00:00",
                "completed_at": "2026-09-10T00:00:03+00:00",
                "attempt_count": len(attempt_ids),
                "attempt_ids": attempt_ids,
            })
        aggregate = {
            "started_at": "2026-09-09T23:59:59+00:00",
            "completed_at": "2026-09-10T00:00:04+00:00",
            "providers": PROVIDERS,
            "case_ids": speed_case_order,
            "batch_count": len(speed_batches),
        }
        self.write_json(self.speed / "execution.json", {
            "schema_version": 1, "aggregate": aggregate, "batches": speed_batches,
        })
        review = {
            "schema_version": 1,
            "bindings": {
                "dataset_sha256": sha((self.speed / "dataset.json").read_bytes()),
                "results_sha256": sha((self.speed / "results.jsonl").read_bytes()),
            },
            "review_protocol": {"machine_judgments_withheld": True, "scope": "all"},
            "attempts": [
                {
                    "attempt_id": row["attempt_id"],
                    "machine_verdict": "pass",
                    "agent_verdict": "pass",
                    "final_verdict": "pass",
                }
                for row in speed_rows
            ],
        }
        self.write_json(self.speed / "result-review.json", review)
        speed_summary = build_speed_summary(speed_dataset, speed_rows, speed_batches, aggregate)
        speed_summary["bindings"] = {
            "dataset_sha256": sha((self.speed / "dataset.json").read_bytes()),
            "results_sha256": sha((self.speed / "results.jsonl").read_bytes()),
        }
        self.write_json(self.speed / "speed-comparison.json", speed_summary)
        self.plan["same_window_speed_cohort"].update({
            "dataset_sha256": sha((self.speed / "dataset.json").read_bytes()),
            "results_sha256": sha((self.speed / "results.jsonl").read_bytes()),
            "execution_sha256": sha((self.speed / "execution.json").read_bytes()),
            "speed_comparison_sha256": sha(
                (self.speed / "speed-comparison.json").read_bytes()
            ),
            "auxiliary_sha256": {
                "result-review.json": sha((self.speed / "result-review.json").read_bytes())
            },
        })
        self.write_json(self.plan_path, self.plan)

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def write_json(path: Path, value: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    @staticmethod
    def rows(providers: list[str], cases: list[str]) -> list[dict]:
        return AssembleWebfetchRefreshTests.rows_for_pairs(
            {(provider, case_id) for provider in providers for case_id in cases}
        )

    @staticmethod
    def rows_for_pairs(pairs: set[tuple[str, str]]) -> list[dict]:
        digest = sha(b"artifact")
        return [
            {
                "attempt_id": f"{provider}__{case_id}__r001",
                "provider": provider,
                "case_id": case_id,
                "run": 1,
                "run_id": "fixture",
                "started_at": "2026-09-10T00:00:01+00:00",
                "completed_at": "2026-09-10T00:00:02+00:00",
                "elapsed_ms": 1000,
                "artifact_path": "artifacts/shared.txt",
                "ok": True,
                "provider_metadata": {},
                "metrics": {"content_sha256": digest, "content_pass": True},
            }
            for provider, case_id in sorted(pairs)
        ]

    def write_bundle(self, root: Path, dataset: dict, rows: list[dict]) -> None:
        root.mkdir(parents=True)
        self.write_json(root / "dataset.json", dataset)
        (root / "results.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows), encoding="utf-8"
        )
        artifact = root / "artifacts" / "shared.txt"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"artifact")
        self.write_json(
            root / "execution.json",
            {
                "batches": [
                    {
                        "runtime_source_sha256": sha(b"fixture-runtime"),
                        "provider_config_sha256": sha(b"fixture-provider-config"),
                        "providers": [
                            {
                                "name": provider,
                                "implementation_identity": {
                                    "fixture_sha256": sha(provider.encode())
                                },
                            }
                            for provider in sorted({row["provider"] for row in rows})
                        ],
                        "parameters": {
                            "runs": 1,
                            "timeout": 45,
                            "delay": 0.5,
                            "provider_concurrency": 1,
                        },
                        "started_at": "2026-09-10T00:00:00+00:00",
                        "completed_at": "2026-09-10T00:00:03+00:00",
                        "attempt_count": len(rows),
                        "attempt_ids": [row["attempt_id"] for row in rows],
                    }
                ]
            },
        )

    def test_exact_refresh_scope_matches_the_current_union(self) -> None:
        pairs = expected_refresh_pairs(self.plan, self.canonical)
        self.assertEqual(len(pairs), 422)
        self.assertTrue({("crawl4ai", case["id"]) for case in self.canonical_cases} <= pairs)
        self.assertIn(("tavily", "document-03"), pairs)
        self.assertIn(("cowagent-built-in", "document-05"), pairs)

    def test_pair_only_refresh_scope_allows_empty_matched_case_lists(self) -> None:
        matrix = self.plan["main_matrix"]
        matrix["full_rerun_providers"] = []
        matrix["full_rerun_reason_by_provider"] = {}
        matrix["matched_case_ids"] = []
        matrix["matched_case_reason_groups"] = []
        matrix["planned_new_calls"] = len(matrix["pair_replacements"])
        self.plan["total_planned_new_calls"] = len(matrix["pair_replacements"])

        self.assertEqual(
            expected_refresh_pairs(self.plan, self.canonical),
            {
                (replacement["provider"], replacement["case_id"])
                for replacement in matrix["pair_replacements"]
            },
        )

    def test_matched_case_ids_and_reason_groups_must_be_empty_together(self) -> None:
        self.plan["main_matrix"]["matched_case_reason_groups"] = []
        with self.assertRaisesRegex(ValueError, "must both be empty or nonempty"):
            expected_refresh_pairs(self.plan, self.canonical)

        self.plan["main_matrix"]["matched_case_ids"] = []
        self.plan["main_matrix"]["matched_case_reason_groups"] = [{
            "case_ids": [RERUN_CASES[0]],
            "category": "input_contract",
            "reason": "changed shared input",
        }]
        with self.assertRaisesRegex(ValueError, "must both be empty or nonempty"):
            expected_refresh_pairs(self.plan, self.canonical)

    def test_refresh_scope_rejects_missing_reasons_and_bad_counts(self) -> None:
        self.plan["main_matrix"]["matched_case_reason_groups"][0]["reason"] = ""
        with self.assertRaisesRegex(ValueError, "empty matched case reason group"):
            expected_refresh_pairs(self.plan, self.canonical)
        self.plan["main_matrix"]["matched_case_reason_groups"][0]["reason"] = (
            "changed shared input"
        )
        self.plan["main_matrix"]["planned_new_calls"] -= 1
        with self.assertRaisesRegex(ValueError, "call count"):
            expected_refresh_pairs(self.plan, self.canonical)

    def test_refresh_scope_rejects_unexplained_matched_and_partial_offline_review(self) -> None:
        self.plan["main_matrix"]["matched_case_reason_groups"][0]["case_ids"].pop()
        with self.assertRaisesRegex(ValueError, "explain every matched case"):
            expected_refresh_pairs(self.plan, self.canonical)
        self.plan["main_matrix"]["matched_case_reason_groups"][0]["case_ids"] = list(
            RERUN_CASES
        )
        self.plan["main_matrix"]["offline_rescore_cases"][0]["attempts"] = 11
        with self.assertRaisesRegex(ValueError, "every method"):
            expected_refresh_pairs(self.plan, self.canonical)
        self.plan["main_matrix"]["offline_rescore_cases"][0]["attempts"] = 12
        self.plan["main_matrix"]["offline_rescore_cases"][0]["full_review"] = False
        with self.assertRaisesRegex(ValueError, "full review"):
            expected_refresh_pairs(self.plan, self.canonical)

    def test_refresh_scope_rejects_duplicate_or_overlapping_explicit_pairs(self) -> None:
        replacement = self.plan["main_matrix"]["pair_replacements"][0]
        self.plan["main_matrix"]["pair_replacements"].append(dict(replacement))
        with self.assertRaisesRegex(ValueError, "duplicate pair"):
            expected_refresh_pairs(self.plan, self.canonical)
        self.plan["main_matrix"]["pair_replacements"].pop()
        replacement["provider"] = "crawl4ai"
        with self.assertRaisesRegex(ValueError, "overlap broader"):
            expected_refresh_pairs(self.plan, self.canonical)

    def test_matched_and_full_scopes_overlap_once(self) -> None:
        pairs = expected_refresh_pairs(self.plan, self.canonical)
        broad_sum = (
            len(self.plan["main_matrix"]["full_rerun_providers"])
            * len(self.canonical_cases)
            + len(self.plan["main_matrix"]["matched_case_ids"]) * len(PROVIDERS)
            + len(self.plan["main_matrix"]["pair_replacements"])
        )
        overlap = (
            len(self.plan["main_matrix"]["full_rerun_providers"])
            * len(self.plan["main_matrix"]["matched_case_ids"])
        )
        self.assertEqual(len(pairs), broad_sum - overlap)

    def test_assembly_keeps_speed_results_out_of_main_matrix(self) -> None:
        output = self.root / "output"
        assemble(
            self.baseline,
            [self.main],
            self.speed,
            output,
            canonical_dataset=self.canonical_path,
            plan=self.plan_path,
        )
        main_rows = [
            json.loads(line) for line in (output / "results.jsonl").read_text().splitlines()
        ]
        speed_rows = [
            json.loads(line)
            for line in (output / "speed-cohort" / "results.jsonl").read_text().splitlines()
        ]
        self.assertEqual(len(main_rows), 1800)
        self.assertEqual(len(speed_rows), 240)
        execution = json.loads((output / "execution.json").read_text())
        self.assertEqual(sum(batch["attempt_count"] for batch in execution["batches"]), 1800)
        receipt = json.loads((output / "refresh-assembly.json").read_text())
        self.assertEqual(receipt["selected_rerun_attempts"], 422)
        self.assertEqual(receipt["inherited_attempts"], 1378)
        self.assertTrue(receipt["speed_cohort"]["excluded_from_main_results"])
        self.assertTrue(receipt["speed_cohort"]["reused_existing"])
        self.assertEqual(receipt["speed_cohort"]["new_calls"], 0)
        audit = json.loads((output / "selection-audit.json").read_text())
        self.assertEqual(len(audit), 422)
        overlap = next(
            row for row in audit
            if row["provider"] == "crawl4ai" and row["case_id"] == RERUN_CASES[0]
        )
        self.assertEqual(
            {scope["scope"] for scope in overlap["declared_scopes"]},
            {"full_rerun_provider", "matched_case"},
        )
        explicit = next(
            row for row in audit
            if row["provider"] == "cowagent-built-in" and row["case_id"] == "document-05"
        )
        self.assertEqual(explicit["declared_scopes"][0]["scope"], "pair_replacement")
        self.assertTrue(
            (output / "history/source-executions/000-baseline-execution.json").is_file()
        )
        self.assertFalse((output / "speed-cohort/workers").exists())

    def test_assembly_rebinds_selected_source_revision_provenance(self) -> None:
        dataset_hash = sha((self.main / "dataset.json").read_bytes())
        results_hash = sha((self.main / "results.jsonl").read_bytes())
        changed_case = RERUN_CASES[0]
        scoring = [{
            "schema_version": 1,
            "kind": "audited_scoring_rule_revision",
            "input_dataset_sha256": dataset_hash,
            "input_results_sha256": results_hash,
            "output_dataset_sha256": dataset_hash,
            "output_results_sha256": results_hash,
            "changed_cases": [{"case_id": changed_case, "fields": ["body_requirements"]}],
            "task_decision_changes": [{
                "attempt_id": f"crawl4ai__{changed_case}__r001",
                "before": False,
                "after": True,
            }],
            "provider_calls": 0,
            "correction_provenance": build_correction_provenance(
                "body-scope", "Full body completeness previously rejected valid complete output.",
                ["declared-truncation-cannot-pass-full-body-task"],
                [{"attempt_id": f"crawl4ai__{changed_case}__r001",
                  "before": False, "after": True}],
            ),
        }]
        references = [{
            "schema_version": 2,
            "kind": "audited_reference_correction",
            "input_dataset_sha256": dataset_hash,
            "input_results_sha256": results_hash,
            "output_dataset_sha256": dataset_hash,
            "output_results_sha256": results_hash,
            "changed_cases": [{
                "case_id": changed_case,
                "fields": ["ground_truth_main_content"],
                "old_reference_sha256": "a" * 64,
                "new_reference_sha256": "b" * 64,
            }],
            "task_decision_changes": [],
            "provider_calls": 0,
            "correction_provenance": build_correction_provenance(
                "reference-evidence", "Reference capture previously included unrelated page chrome.",
                ["reference-scope-excludes-site-chrome"], [],
            ),
        }]
        self.write_json(self.main / "scoring-revisions.json", scoring)
        self.write_json(self.main / "reference-revisions.json", references)
        output = self.root / "revision-output"
        assemble(self.baseline, [self.main], self.speed, output,
                 canonical_dataset=self.canonical_path, plan=self.plan_path)
        self.assertTrue(valid_scoring_revision(output))
        self.assertEqual(reference_revision_cases(output), {changed_case: {"a" * 64}})
        receipt = json.loads((output / "scoring-revisions.json").read_text())[0]
        source = receipt["source_receipts"][0]
        self.assertEqual(
            sha((output / source["receipt_path"]).read_bytes()), source["receipt_sha256"]
        )

    def test_assembly_rejects_invalid_selected_source_revision(self) -> None:
        self.write_json(self.main / "scoring-revisions.json", [{
            "kind": "audited_scoring_rule_revision",
            "input_dataset_sha256": "a" * 64,
            "input_results_sha256": "b" * 64,
            "output_dataset_sha256": "c" * 64,
            "output_results_sha256": "d" * 64,
            "changed_cases": [], "task_decision_changes": [], "provider_calls": 0,
        }])
        output = self.root / "invalid-revision-output"
        with self.assertRaisesRegex(ValueError, "invalid scoring revision binding"):
            assemble(self.baseline, [self.main], self.speed, output,
                     canonical_dataset=self.canonical_path, plan=self.plan_path)
        self.assertFalse(output.exists())

    def test_duplicate_rerun_pair_fails_without_creating_output(self) -> None:
        rows = [json.loads(line) for line in (self.main / "results.jsonl").read_text().splitlines()]
        rows.append(rows[0])
        (self.main / "results.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        execution = json.loads((self.main / "execution.json").read_text())
        execution["batches"][0]["attempt_ids"].append(rows[0]["attempt_id"])
        execution["batches"][0]["attempt_count"] += 1
        self.write_json(self.main / "execution.json", execution)
        output = self.root / "duplicate-output"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            assemble(
                self.baseline,
                [self.main],
                self.speed,
                output,
                canonical_dataset=self.canonical_path,
                plan=self.plan_path,
            )
        self.assertFalse(output.exists())

    def test_missing_rerun_pair_fails_without_creating_output(self) -> None:
        rows = [json.loads(line) for line in (self.main / "results.jsonl").read_text().splitlines()]
        missing = rows.pop()
        (self.main / "results.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        execution = json.loads((self.main / "execution.json").read_text())
        execution["batches"][0]["attempt_ids"].remove(missing["attempt_id"])
        execution["batches"][0]["attempt_count"] -= 1
        self.write_json(self.main / "execution.json", execution)

        output = self.root / "missing-output"
        with self.assertRaisesRegex(ValueError, "missing=1, extra=0"):
            assemble(
                self.baseline,
                [self.main],
                self.speed,
                output,
                canonical_dataset=self.canonical_path,
                plan=self.plan_path,
            )
        self.assertFalse(output.exists())

    def test_extra_rerun_pair_fails_without_creating_output(self) -> None:
        rows = [json.loads(line) for line in (self.main / "results.jsonl").read_text().splitlines()]
        expected = expected_refresh_pairs(self.plan, self.canonical)
        extra_pair = next(
            (provider, case["id"])
            for provider in PROVIDERS
            for case in self.canonical_cases
            if (provider, case["id"]) not in expected
        )
        extra = self.rows_for_pairs({extra_pair})[0]
        rows.append(extra)
        (self.main / "results.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        execution = json.loads((self.main / "execution.json").read_text())
        execution["batches"][0]["attempt_ids"].append(extra["attempt_id"])
        execution["batches"][0]["attempt_count"] += 1
        self.write_json(self.main / "execution.json", execution)

        output = self.root / "extra-output"
        with self.assertRaisesRegex(ValueError, "missing=0, extra=1"):
            assemble(
                self.baseline,
                [self.main],
                self.speed,
                output,
                canonical_dataset=self.canonical_path,
                plan=self.plan_path,
            )
        self.assertFalse(output.exists())

    def test_provider_shards_must_have_the_same_run_identity(self) -> None:
        rows = [json.loads(line) for line in (self.main / "results.jsonl").read_text().splitlines()]
        moved = next(row for row in rows if row["provider"] == "crawl4ai")
        rows.remove(moved)
        (self.main / "results.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        execution = json.loads((self.main / "execution.json").read_text())
        execution["batches"][0]["attempt_ids"].remove(moved["attempt_id"])
        execution["batches"][0]["attempt_count"] -= 1
        self.write_json(self.main / "execution.json", execution)

        shard = self.root / "second-main-rerun"
        self.write_bundle(shard, self.canonical, [moved])
        matching_output = self.root / "matching-provider-identity-output"
        assemble(
            self.baseline,
            [self.main, shard],
            self.speed,
            matching_output,
            canonical_dataset=self.canonical_path,
            plan=self.plan_path,
        )
        self.assertTrue((matching_output / "results.jsonl").is_file())

        shard_execution = json.loads((shard / "execution.json").read_text())
        shard_execution["batches"][0]["provider_config_sha256"] = sha(b"different-config")
        self.write_json(shard / "execution.json", shard_execution)

        output = self.root / "mixed-provider-identity-output"
        with self.assertRaisesRegex(
            ValueError, "inconsistent run identity.*provider_config_sha256"
        ):
            assemble(
                self.baseline,
                [self.main, shard],
                self.speed,
                output,
                canonical_dataset=self.canonical_path,
                plan=self.plan_path,
            )
        self.assertFalse(output.exists())

        shard_execution["batches"][0]["provider_config_source"] = "/repo/configs/providers.resources.json"
        self.write_json(shard / "execution.json", shard_execution)
        route_output = self.root / "different-declared-config-route-output"
        assemble(
            self.baseline,
            [self.main, shard],
            self.speed,
            route_output,
            canonical_dataset=self.canonical_path,
            plan=self.plan_path,
        )
        self.assertTrue((route_output / "results.jsonl").is_file())

    def test_main_rerun_rejects_explicit_account_capacity_failure(self) -> None:
        rows = [json.loads(line) for line in (self.main / "results.jsonl").read_text().splitlines()]
        rows[0].update(
            ok=False,
            error_type="account_capacity",
            error_message="private provider response must not appear in the rejection",
        )
        (self.main / "results.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        output = self.root / "account-capacity-output"
        with self.assertRaisesRegex(
            ValueError, "provider account-capacity failure.*replenish the account"
        ) as raised:
            assemble(
                self.baseline,
                [self.main],
                self.speed,
                output,
                canonical_dataset=self.canonical_path,
                plan=self.plan_path,
            )
        self.assertNotIn("private provider response", str(raised.exception))
        self.assertFalse(output.exists())

    def test_main_rerun_rejects_legacy_firecrawl_402_only(self) -> None:
        rows = [json.loads(line) for line in (self.main / "results.jsonl").read_text().splitlines()]
        firecrawl = next(row for row in rows if row["provider"] == "firecrawl")
        firecrawl.update(ok=False, status_code=402, error_type="api_error")
        (self.main / "results.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        with self.assertRaisesRegex(ValueError, "provider account-capacity failure"):
            assemble(
                self.baseline,
                [self.main],
                self.speed,
                self.root / "legacy-firecrawl-402-output",
                canonical_dataset=self.canonical_path,
                plan=self.plan_path,
            )

        firecrawl.update(ok=True, status_code=200, error_type=None)
        origin_402 = next(
            row
            for row in rows
            if row["provider"] == "crawl4ai"
        )
        origin_402.update(ok=False, status_code=402, error_type="crawl_error")
        (self.main / "results.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        assemble(
            self.baseline,
            [self.main],
            self.speed,
            self.root / "origin-402-output",
            canonical_dataset=self.canonical_path,
            plan=self.plan_path,
        )

    def test_equivalent_capture_mode_allows_only_observation_metadata_drift(self) -> None:
        canonical_case = self.canonical["cases"][0]
        canonical_case["source_verification"] = {
            "state": "valid",
            "checked_at": "2026-09-10T00:00:00+00:00",
            "http_status": 200,
            "reference_sha256": "a" * 64,
            "rendered_content_sha256": "e" * 64,
            "browser_observation_sha256": "f" * 64,
        }
        self.write_json(self.canonical_path, self.canonical)
        dataset = json.loads((self.main / "dataset.json").read_text())
        dataset["comparison"] = self.canonical["comparison"]
        dataset["cases"][0]["source_verification"] = {
            "state": "valid",
            "checked_at": "2026-09-10T00:10:00+00:00",
            "http_status": 200,
            "source_snapshot_sha256": "c" * 64,
            "extracted_reference_sha256": "d" * 64,
            "reference_sha256": "a" * 64,
            "rendered_content_sha256": "e" * 64,
            "browser_observation_sha256": "1" * 64,
        }
        self.write_json(self.main / "dataset.json", dataset)
        assemble(
            self.baseline,
            [self.main],
            self.speed,
            self.root / "timestamp-output",
            canonical_dataset=self.canonical_path,
            plan=self.plan_path,
        )

        dataset["cases"][0]["source_verification"]["reference_sha256"] = "b" * 64
        self.write_json(self.main / "dataset.json", dataset)
        with self.assertRaisesRegex(ValueError, "differs from canonical"):
            assemble(
                self.baseline,
                [self.main],
                self.speed,
                self.root / "changed-reference-output",
                canonical_dataset=self.canonical_path,
                plan=self.plan_path,
            )

    def test_bound_primary_symlinks_fail_before_output(self) -> None:
        for source in (self.baseline, self.main):
            with self.subTest(source=source.name):
                primary = source / 'artifacts/shared.txt'
                actual = source / 'same-bytes.txt'
                actual.write_bytes(primary.read_bytes())
                primary.unlink()
                primary.symlink_to(actual)
                output = self.root / ('linked-primary-' + source.name)
                try:
                    with self.assertRaisesRegex(ValueError, '[Ss]ymlink'):
                        assemble(self.baseline, [self.main], self.speed, output,
                                 canonical_dataset=self.canonical_path, plan=self.plan_path)
                    self.assertFalse(output.exists())
                finally:
                    primary.unlink()
                    primary.write_bytes(actual.read_bytes())

    def test_baseline_and_rerun_root_symlinks_fail_before_output(self) -> None:
        for source in (self.baseline, self.main):
            with self.subTest(source=source.name):
                linked = self.root / ('linked-' + source.name)
                linked.symlink_to(source, target_is_directory=True)
                output = self.root / ('linked-root-' + source.name)
                with self.assertRaisesRegex(ValueError, 'root must not be a symlink'):
                    assemble(linked if source == self.baseline else self.baseline,
                             [linked if source == self.main else self.main], self.speed, output,
                             canonical_dataset=self.canonical_path, plan=self.plan_path)
                self.assertFalse(output.exists())

    def test_speed_schema_and_symlinks_fail_before_output(self) -> None:
        comparison = json.loads((self.speed / "speed-comparison.json").read_text())
        comparison["rows"][0]["planned_attempts"] -= 1
        self.write_json(self.speed / "speed-comparison.json", comparison)
        with self.assertRaisesRegex(ValueError, "Reused speed cohort bytes"):
            assemble(self.baseline, [self.main], self.speed, self.root / "bad-speed",
                     canonical_dataset=self.canonical_path, plan=self.plan_path)
        self.assertFalse((self.root / "bad-speed").exists())

        comparison["rows"][0]["planned_attempts"] += 1
        self.write_json(self.speed / "speed-comparison.json", comparison)
        (self.speed / "workers").mkdir()
        (self.speed / "workers/outside").symlink_to(self.canonical_path)
        with self.assertRaisesRegex(ValueError, "symlink"):
            assemble(self.baseline, [self.main], self.speed, self.root / "symlink-speed",
                     canonical_dataset=self.canonical_path, plan=self.plan_path)
        self.assertFalse((self.root / "symlink-speed").exists())

        speed_link = self.root / "speed-link"
        speed_link.symlink_to(self.speed, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "root must not be a symlink"):
            assemble(self.baseline, [self.main], speed_link, self.root / "root-symlink-speed",
                     canonical_dataset=self.canonical_path, plan=self.plan_path)
        self.assertFalse((self.root / "root-symlink-speed").exists())

    def test_reused_speed_execution_mutation_fails_before_output(self) -> None:
        execution = json.loads((self.speed / "execution.json").read_text())
        execution["schema_version"] = 99
        self.write_json(self.speed / "execution.json", execution)
        with self.assertRaisesRegex(ValueError, "Reused speed cohort bytes"):
            assemble(self.baseline, [self.main], self.speed, self.root / "execution-schema-speed",
                     canonical_dataset=self.canonical_path, plan=self.plan_path)
        self.assertFalse((self.root / "execution-schema-speed").exists())

    def test_equivalent_capture_rejects_invalid_observation_metadata(self) -> None:
        dataset = json.loads((self.main / "dataset.json").read_text())
        dataset["comparison"] = self.canonical["comparison"]
        dataset["cases"][0]["source_verification"] = {
            "state": "valid",
            "checked_at": "2026-09-10T00:10:00+00:00",
            "http_status": 700,
            "reference_sha256": "a" * 64,
            "source_snapshot_sha256": "not-a-hash",
        }
        self.write_json(self.main / "dataset.json", dataset)
        with self.assertRaisesRegex(ValueError, "malformed capture HTTP status"):
            assemble(self.baseline, [self.main], self.speed, self.root / "bad-capture",
                     canonical_dataset=self.canonical_path, plan=self.plan_path)

        dataset["cases"][0]["source_verification"]["http_status"] = 403
        self.write_json(self.main / "dataset.json", dataset)
        with self.assertRaisesRegex(ValueError, "invalid capture hash"):
            assemble(self.baseline, [self.main], self.speed, self.root / "bad-capture-hash",
                     canonical_dataset=self.canonical_path, plan=self.plan_path)

    def test_equivalent_capture_accepts_unavailable_http_error_without_reference(self) -> None:
        case_id = self.canonical["cases"][0]["id"]
        unavailable = {
            "state": "unavailable",
            "checked_at": "2026-09-10T00:00:00+00:00",
            "http_status": 403,
            "reference_sha256": None,
        }
        self.canonical["cases"][0]["ground_truth_main_content"] = ""
        self.canonical["cases"][0]["source_verification"] = unavailable
        self.write_json(self.canonical_path, self.canonical)
        for root in (self.main, self.speed):
            dataset = json.loads((root / "dataset.json").read_text())
            case = next((row for row in dataset["cases"] if row["id"] == case_id), None)
            if case is not None:
                case["ground_truth_main_content"] = ""
                case["source_verification"] = {
                    **unavailable, "checked_at": "2026-09-10T00:10:00+00:00"
                }
                self.write_json(root / "dataset.json", dataset)
        assemble(self.baseline, [self.main], self.speed, self.root / "unavailable-output",
                 canonical_dataset=self.canonical_path, plan=self.plan_path)

    def test_equivalent_capture_accepts_valid_403_without_text_reference(self) -> None:
        case_id = self.canonical["cases"][0]["id"]
        verification = {
            "state": "valid", "checked_at": "2026-09-10T00:00:00+00:00",
            "http_status": 403, "reference_sha256": None,
            "note": "Independent HTTP plus browser verification",
        }
        self.canonical["cases"][0]["source_verification"] = verification
        self.write_json(self.canonical_path, self.canonical)
        dataset = json.loads((self.main / "dataset.json").read_text())
        case = next(row for row in dataset["cases"] if row["id"] == case_id)
        case["source_verification"] = {
            **verification, "checked_at": "2026-09-10T00:10:00+00:00"
        }
        self.write_json(self.main / "dataset.json", dataset)
        assemble(self.baseline, [self.main], self.speed, self.root / "valid-403-output",
                 canonical_dataset=self.canonical_path, plan=self.plan_path)

    def test_baseline_hash_mismatch_fails_closed(self) -> None:
        self.plan["baseline"]["results_sha256"] = "0" * 64
        self.write_json(self.plan_path, self.plan)
        output = self.root / "bad-baseline-output"
        with self.assertRaisesRegex(ValueError, "Baseline bytes"):
            assemble(
                self.baseline,
                [self.main],
                self.speed,
                output,
                canonical_dataset=self.canonical_path,
                plan=self.plan_path,
            )
        self.assertFalse(output.exists())

    def test_reused_speed_hash_mismatch_fails_closed(self) -> None:
        self.plan["same_window_speed_cohort"]["results_sha256"] = "0" * 64
        self.write_json(self.plan_path, self.plan)
        output = self.root / "bad-speed-binding"
        with self.assertRaisesRegex(ValueError, "Reused speed cohort bytes"):
            assemble(
                self.baseline, [self.main], self.speed, output,
                canonical_dataset=self.canonical_path, plan=self.plan_path,
            )
        self.assertFalse(output.exists())

    def test_reused_speed_bundle_allows_optional_review_and_keeps_revisions(self) -> None:
        (self.speed / "result-review.json").unlink()
        self.write_json(self.speed / "scoring-revisions.json", {"schema_version": 1})
        self.plan["same_window_speed_cohort"]["auxiliary_sha256"] = {
            "scoring-revisions.json": sha(
                (self.speed / "scoring-revisions.json").read_bytes()
            )
        }
        self.write_json(self.plan_path, self.plan)
        output = self.root / "speed-optional-review"
        assemble(
            self.baseline, [self.main], self.speed, output,
            canonical_dataset=self.canonical_path, plan=self.plan_path,
        )
        self.assertFalse((output / "speed-cohort/result-review.json").exists())
        self.assertTrue((output / "speed-cohort/scoring-revisions.json").is_file())

    def test_speed_dataset_cannot_smuggle_an_extra_main_case(self) -> None:
        dataset = json.loads((self.speed / "dataset.json").read_text())
        extra = next(case for case in self.canonical_cases if case["id"] not in SPEED_CASES)
        dataset["cases"].append(extra)
        self.write_json(self.speed / "dataset.json", dataset)
        output = self.root / "mixed-speed-output"
        with self.assertRaisesRegex(ValueError, "Reused speed cohort bytes"):
            assemble(
                self.baseline,
                [self.main],
                self.speed,
                output,
                canonical_dataset=self.canonical_path,
                plan=self.plan_path,
            )
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
