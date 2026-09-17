import hashlib
import json
from pathlib import Path

import pytest

from harness.trajectory_replay.diagnostic_closure import (
    CURRENT_FINDINGS,
    CURRENT_TASKS,
    _verified_digest,
    load_diagnostic_closure,
)


def test_retrospective_retrieval_findings_do_not_claim_current_qualification() -> None:
    for task_id in (243, 313):
        chinese, english = CURRENT_FINDINGS[task_id][2:]
        assert "当前候选" in chinese
        assert "current candidate" in english
        assert "qualified output" not in english.lower()
from harness.trajectory_replay.webarena_report import _assets, _write_diagnostic_closure


def _write(path: Path, value: object) -> dict[str, str]:
    path.write_text(json.dumps(value), encoding="utf-8")
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def test_diagnostic_evidence_requires_matching_regular_file(tmp_path: Path) -> None:
    ref = _write(tmp_path / "receipt.json", {"status": "pass"})
    assert _verified_digest(ref) == ref["sha256"]
    (tmp_path / "receipt.json").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        _verified_digest(ref)
    link = tmp_path / "link.json"
    link.symlink_to(tmp_path / "receipt.json")
    with pytest.raises(ValueError, match="symlinked"):
        _verified_digest({"path": str(link), "sha256": ref["sha256"]})


def test_diagnostic_index_rejects_symlinked_parent(tmp_path: Path) -> None:
    source = tmp_path / "index.json"
    source.write_text("{}", encoding="utf-8")
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="missing or symlinked"):
        load_diagnostic_closure(linked_parent / source.name, old_failed_tasks=set())


def test_diagnostic_closure_is_bounded_and_public_safe(tmp_path: Path) -> None:
    receipt = _write(tmp_path / "receipt.json", {"local_path": str(tmp_path), "status": "pass"})
    ids = sorted(CURRENT_TASKS | set(range(1000, 1027)))
    prior = {
        "tasks": [
            {
                "task_id": task_id,
                "original_status": "fail",
                "history_receipt_versions": [receipt],
                "b96f_diagnostic_closure": {
                    "completed_pair": receipt,
                    "independent_audit": receipt,
                    "physical_results": [
                        {
                            "browser": browser,
                            "receipt": receipt["path"],
                            "receipt_sha256": receipt["sha256"],
                            "binary_sha256": "a" * 64,
                            "raw_status": "diagnostic_current_retrieval_pass",
                        }
                        for browser in ("chromium", "moli")
                    ],
                },
            }
            for task_id in ids
        ]
    }
    prior_ref = _write(tmp_path / "prior.json", prior)
    index = {
        "schema": "original36_diagnostic_closure_index/5",
        "promotable": False,
        "formal_release": False,
        "cohort_certified": False,
        "official_scores_modified": False,
        "original_task_count": 36,
        "diagnostically_closed_count": 36,
        "inherited_old_binary_closed_count": 27,
        "current_b96_diagnostically_closed_task_ids": sorted(CURRENT_TASKS),
        "predecessor": prior_ref,
        "tasks": [
            {
                "task_id": task_id,
                "task_type": "RETRIEVE",
                "b96_current_task_validation": task_id in CURRENT_TASKS,
                "closure_evidence": [receipt],
                "diagnosis": {"category": "observer", "code_or_process": "shared observer"},
                "raw_results": [
                    {
                        "browser": browser,
                        "receipt": receipt,
                        "binary_sha256": "a" * 64,
                        "status": "diagnostic_current_retrieval_pass",
                    }
                    for browser in ("chromium", "moli")
                ],
                "downstream_results": [
                    {"browser": browser, "audit": receipt, "accepted": True}
                    for browser in ("chromium", "moli")
                ],
            }
            for task_id in ids
        ],
    }
    path = tmp_path / "index.json"
    _write(path, index)
    model = load_diagnostic_closure(path, old_failed_tasks=set(ids))
    assert len(model["entries"]) == 36
    assert (model["current_b96"], model["inherited_mixed_build"]) == (9, 27)
    assert str(tmp_path) not in json.dumps(model)
    assert model["official_scores_changed"] is False
    with pytest.raises(ValueError, match="does not match"):
        load_diagnostic_closure(path, old_failed_tasks={1})


def test_diagnostic_pages_keep_old_verdict_and_bilingual_routes(tmp_path: Path) -> None:
    _assets(tmp_path)
    (tmp_path / "en").mkdir()
    for lang_root in (tmp_path, tmp_path / "en"):
        for landing in ("index.html", "fixes.html"):
            (lang_root / landing).write_text("<html><main>existing</main></html>", encoding="utf-8")
    model = {
        "entries": [
            {
                "task_id": 108,
                "task_type": "RETRIEVE",
                "generation": "current_b96",
                "finding": ("日期筛选", "Date filter", "中文原因", "English finding"),
                "evidence_sha256": ["a" * 64],
                "observations": [
                    {
                        "browser": "moli",
                        "binary_sha256": "a" * 64,
                        "receipt_sha256": "b" * 64,
                        "audit_sha256": "c" * 64,
                        "status": "diagnostic_current_retrieval_incomplete",
                        "completed_actions": 10,
                        "action_count": 10,
                        "official_rule_score": None,
                        "downstream_accepted": True,
                    }
                ],
            },
            {
                "task_id": 29,
                "task_type": "RETRIEVE",
                "generation": "inherited_mixed_build",
                "finding": None,
                "evidence_sha256": ["b" * 64],
                "observations": [],
            },
            {
                "task_id": 313,
                "task_type": "RETRIEVE",
                "generation": "current_b96",
                "finding": CURRENT_FINDINGS[313],
                "owning_process": "retrieval acceptance",
                "owning_process_zh": "取数验收",
                "evidence_sha256": ["c" * 64],
                "observations": [
                    {
                        "browser": "moli",
                        "binary_sha256": "d" * 64,
                        "receipt_sha256": "e" * 64,
                        "status": "diagnostic_current_retrieval_incomplete",
                        "completed_actions": 7,
                        "action_count": 7,
                        "official_rule_score": None,
                        "downstream_accepted": True,
                    }
                ],
            },
        ],
    }
    _write_diagnostic_closure(
        tmp_path,
        model,
        moli_result_keys={108: "moli-1.1.3-108", 29: "moli-1.1.3-29", 313: "moli-1.1.3-313"},
    )
    zh = (tmp_path / "diagnostics.html").read_text(encoding="utf-8")
    en = (tmp_path / "en/diagnostics.html").read_text(encoding="utf-8")
    assert "Chromium 90/90、Moli 54/90" in zh
    assert "Chromium 90/90 and Moli 54/90" in en
    assert 'href="diagnostic/108.html"' in zh and 'href="diagnostic/108.html"' in en
    assert 'href="../result/moli-1.1.3-108.html"' in (tmp_path / "diagnostic/108.html").read_text(
        encoding="utf-8"
    )
    assert "no new code-level cause" in (tmp_path / "en/diagnostic/29.html").read_text(
        encoding="utf-8"
    )
    assert "早期有限范围复核接受；当前资格未通过" in (
        tmp_path / "diagnostic/313.html"
    ).read_text(encoding="utf-8")
    assert "Earlier limited-scope audit accepted; current qualification failed" in (
        tmp_path / "en/diagnostic/313.html"
    ).read_text(encoding="utf-8")
    assert "diagnostics.html" in (tmp_path / "index.html").read_text(encoding="utf-8")
