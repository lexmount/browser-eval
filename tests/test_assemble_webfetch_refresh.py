from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.assemble_webfetch_refresh import assemble, expected_refresh_pairs

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
            "schema_version": 1,
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
            "schema_version": 1,
            "baseline": {
                "dataset_sha256": sha((self.baseline / "dataset.json").read_bytes()),
                "results_sha256": sha((self.baseline / "results.jsonl").read_bytes()),
                "logical_attempts": 1800,
            },
            "main_matrix": {
                "rerun_case_ids": RERUN_CASES,
                "rerun_all_providers_for_each_case": True,
                "full_rerun_providers": ["crawl4ai", "langchain-webbase"],
                "deadline_replacement_attempt_ids": [
                    "cowagent-built-in__lm-rust-learn__r001",
                    "cowagent-built-in__document-05__r001",
                ],
                "planned_new_calls": 422,
                "final_logical_attempts": 1800,
            },
            "same_window_speed_cohort": {
                "required": True,
                "case_ids": SPEED_CASES,
                "provider_count": 12,
                "planned_new_calls": 240,
                "require_independent_result_review": True,
            },
            "total_planned_new_calls": 662,
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
        self.write_bundle(self.speed, speed_dataset, speed_rows)
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
        self.assertEqual(len(json.loads((output / "selection-audit.json").read_text())), 422)
        self.assertTrue(
            (output / "history/source-executions/000-baseline-execution.json").is_file()
        )

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

    def test_equivalent_capture_mode_allows_only_timestamp_drift(self) -> None:
        canonical_case = self.canonical["cases"][0]
        canonical_case["source_verification"] = {
            "checked_at": "2026-09-10T00:00:00+00:00",
            "reference_sha256": "a" * 64,
        }
        self.write_json(self.canonical_path, self.canonical)
        dataset = json.loads((self.main / "dataset.json").read_text())
        dataset["comparison"] = self.canonical["comparison"]
        dataset["cases"][0]["source_verification"] = {
            "checked_at": "2026-09-10T00:10:00+00:00",
            "reference_sha256": "a" * 64,
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

    def test_speed_dataset_cannot_smuggle_an_extra_main_case(self) -> None:
        dataset = json.loads((self.speed / "dataset.json").read_text())
        extra = next(case for case in self.canonical_cases if case["id"] not in SPEED_CASES)
        dataset["cases"].append(extra)
        self.write_json(self.speed / "dataset.json", dataset)
        output = self.root / "mixed-speed-output"
        with self.assertRaisesRegex(ValueError, "exact sampled case inventory"):
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
