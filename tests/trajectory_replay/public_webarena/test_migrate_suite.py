from __future__ import annotations

from scripts.migrate_browser_use_suite import migrate


def test_migration_preserves_semantics_and_emits_common_layers():
    legacy = {
        "schema": "lex_browser_use_tasks/3",
        "tasks": [
            {
                "id": "one",
                "title": "One",
                "difficulty": "simple",
                "tags": ["curated"],
                "tool_calls": [
                    {
                        "action": "navigate",
                        "url": "https://example.com",
                        "save_as": "page",
                    }
                ],
                "assertions": [{"output": "page", "equals": "Example"}],
                "ground_truth": {
                    "oracle": "deterministic_exact",
                    "expected_outputs": {"page": "Example"},
                    "expected_actions": ["navigate"],
                },
                "source": {"url": "https://example.com/source"},
            }
        ],
    }

    migrated, contract_sha256 = migrate(legacy)

    task = migrated["tasks"][0]
    assert migrated["schema"] == "lex_browser_use_tasks/4"
    assert contract_sha256
    assert "tool_calls" not in task
    assert task["trajectory"]["actions"] == legacy["tasks"][0]["tool_calls"]
    assert task["evaluator"]["kind"] == "exact_outputs"
    assert task["environment"] == {"kind": "public_web"}
