import hashlib
import json
from pathlib import Path

import pytest

import harness.trajectory_replay.diagnostic_closure as closure_module
from harness.trajectory_replay.diagnostic_closure import (
    CURRENT_FINDINGS,
    CURRENT_TASKS,
    LATEST_SOURCE_HEAD,
    LATEST_TASKS,
    _load_latest_index,
    _verified_digest,
    load_diagnostic_closure,
)
from harness.trajectory_replay.webarena_report import (
    _assets,
    _latest_moli_task_section,
    _write_diagnostic_closure,
)


def test_retrospective_retrieval_findings_do_not_claim_current_qualification() -> None:
    for task_id in (243, 313):
        chinese, english = CURRENT_FINDINGS[task_id][2:]
        assert "当前候选" in chinese
        assert "current candidate" in english
        assert "qualified output" not in english.lower()


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
        "latest_moli": {
            "source_head": LATEST_SOURCE_HEAD,
            "source_diff_sha256": "f" * 64,
            "binary_sha256": "a" * 64,
            "observations": [
                {
                    "task_id": 29,
                    "completed_actions": 9,
                    "action_count": 9,
                    "official_rule_score": 1.0,
                    "receipt_sha256": "c" * 64,
                    "completion_sha256": "d" * 64,
                    "scope_note": "current_source_retrieval",
                }
            ],
        },
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
    assert "Moli 1.1.7 定点诊断" in (tmp_path / "diagnostic/29.html").read_text(
        encoding="utf-8"
    )
    detail_json = json.loads((tmp_path / "data/diagnostic/29.json").read_text(encoding="utf-8"))
    assert detail_json["latest_moli"]["observation"]["official_rule_score"] == 1.0
    assert detail_json["latest_moli"]["source_head"] == LATEST_SOURCE_HEAD


def _latest_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    predecessor = _write(tmp_path / "v5.json", {"schema": "original36_diagnostic_closure_index/5"})
    binary = _write(tmp_path / "moli", {"binary": "fixture"})
    monkeypatch.setattr(closure_module, "LATEST_BINARY_SHA256", binary["sha256"])
    release = _write(
        tmp_path / "release.json",
        {
            "schema": "moli_latest_main_release_diagnostic/1",
            "source_head": LATEST_SOURCE_HEAD,
            "binary_sha256": binary["sha256"],
            "source_diff_sha256_before": "a" * 64,
            "source_diff_sha256_after": "a" * 64,
            "source_changed_during_check": False,
            "exit_code": 0,
        },
    )
    candidates = [
        {
            "task_id": task_id,
            **_write(
                tmp_path / f"{task_id}-candidate.json",
                {
                    "task_id": task_id,
                    "retrieval_contract": {
                        "schema": "lex_replay_current_run_retrieval/3",
                        "semantic_reference": {"kind": "reviewed_value_set"},
                    }
                    if task_id == 163 else {},
                },
            ),
        }
        for task_id in (29, 66, 163, 173)
    ]
    manifest = _write(
        tmp_path / "manifest.json",
        {
            "schema": "moli_latest_main_retrieval_four_diagnostic/1",
            "promotable": False,
            "source_head": LATEST_SOURCE_HEAD,
            "moli_binary_sha256": binary["sha256"],
            "task_ids": [29, 66, 163, 173],
            "candidate_bindings": candidates,
            "retry": False,
        },
    )
    archive = _write(tmp_path / "archive.json", {"archived": True})
    tasks = []
    for task_id in sorted(LATEST_TASKS):
        evaluation = _write(
            tmp_path / f"{task_id}-evaluation.json", {"task_id": task_id, "score": 1.0}
        )
        shared = _write(tmp_path / f"{task_id}-trace.json", {"task_id": task_id})
        evidence = {
            "trace.json": shared,
            "network.har": shared,
            "evaluation.json": evaluation,
            "candidate-input.json": shared,
        }
        evidence[
            "completion-observation.json" if task_id == 389 else "retrieval-acceptance.json"
        ] = shared
        status = (
            "diagnostic_mutation_completion_pass"
            if task_id == 389 else "diagnostic_current_retrieval_pass"
        )
        receipt = _write(
            tmp_path / f"{task_id}-receipt.json",
            {
                "task_id": task_id,
                "binary_sha256": binary["sha256"],
                "status": status,
                "official_score": 1.0,
                "completed_action_count": 7,
                "action_count": 7,
                "process_status": "pass",
                "recorded_response_used": False,
                "evidence": evidence,
            },
        )
        completion = _write(
            tmp_path / f"{task_id}-completion.json",
            {"task_id": task_id, "status": status, "official_score": 1.0},
        )
        tasks.append({"task_id": task_id, "receipt": receipt, "completion": completion})
    index = {
        "schema": "original36_latest_moli_diagnostic_index/6",
        "promotable": False,
        "formal_release": False,
        "cohort_certified": False,
        "official_scores_modified": False,
        "predecessor": predecessor,
        "source_receipt": release,
        "binary": binary,
        "archive": archive,
        "retrieval_manifest": manifest,
        "tasks": tasks,
    }
    monkeypatch.setattr(
        closure_module,
        "load_diagnostic_closure",
        lambda _path, old_failed_tasks: {
            "source_sha256": predecessor["sha256"],
            "entries": [],
            "original_failures": 36,
            "inherited_mixed_build": 27,
            "current_b96": 9,
        },
    )
    return index


def test_latest_five_are_separate_and_all_named_evidence_is_verified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = _latest_fixture(tmp_path, monkeypatch)
    model = _load_latest_index(index, "c" * 64, old_failed_tasks=set())
    latest = model["latest_moli"]
    assert model["original_failures"] == 36
    assert len(latest["observations"]) == 5
    assert latest["formal_cohort"] is False
    assert latest["source_diff_sha256"] == "a" * 64
    assert next(row for row in latest["observations"] if row["task_id"] == 163)[
        "scope_note"
    ] == "predeclared_shared_v3_correction"
    assert next(row for row in latest["observations"] if row["task_id"] == 389)[
        "scope_note"
    ] == "read_only_completion_observation"
    assert str(tmp_path) not in json.dumps(model)
    (tmp_path / "29-trace.json").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        _load_latest_index(index, "c" * 64, old_failed_tasks=set())


def test_latest_five_rejects_promotion_and_wrong_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    index = _latest_fixture(tmp_path, monkeypatch)
    index["cohort_certified"] = True
    with pytest.raises(ValueError, match="cannot claim certification"):
        _load_latest_index(index, "c" * 64, old_failed_tasks=set())
    index["cohort_certified"] = False
    index["tasks"] = index["tasks"][:-1]
    with pytest.raises(ValueError, match="exactly five"):
        _load_latest_index(index, "c" * 64, old_failed_tasks=set())


def test_latest_diagnostic_page_keeps_later_receipt_separate() -> None:
    latest = {
        "source_head": LATEST_SOURCE_HEAD,
        "source_diff_sha256": "a" * 64,
        "binary_sha256": "b" * 64,
        "observations": [
            {
                "task_id": 389,
                "completed_actions": 10,
                "action_count": 10,
                "official_rule_score": 1.0,
                "receipt_sha256": "c" * 64,
                "completion_sha256": "d" * 64,
                "scope_note": "read_only_completion_observation",
            }
        ],
    }
    zh = _latest_moli_task_section("zh", latest, 389)
    en = _latest_moli_task_section("en", latest, 389)
    assert "Moli 1.1.7 定点诊断" in zh
    assert "只读完成观察" in zh
    assert "does not replace the historical score" in en
    assert LATEST_SOURCE_HEAD in zh and "c" * 64 in en
    assert _latest_moli_task_section("zh", latest, 29) == ""
