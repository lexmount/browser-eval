import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_original36_latest_index.py"
SPEC = importlib.util.spec_from_file_location("original36_index", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_trace_scope_separates_original_actions_from_auxiliary_failure(tmp_path):
    trace = tmp_path / "trace.json"
    trace.write_text(
        json.dumps(
            [
                {"index": 1, "status": "success"},
                {"index": "1a", "status": "success"},
                {"index": 2, "status": "success"},
                {"index": 3, "status": "error"},
            ]
        )
    )
    assert MODULE.trace_completion(trace) == (3, 2, 1)
    assert MODULE.trace_completion(trace, True, 2) == (2, 2, 2)
    with pytest.raises(ValueError, match="original indexed actions"):
        MODULE.trace_completion(trace, True, 4)


def test_pre_action_readiness_is_not_a_qualified_browser_action(tmp_path):
    trace = tmp_path / "trace.json"
    trace.write_text(json.dumps([
        {"index": "before-1", "status": "success"},
        {"index": 1, "status": "success"},
        {"index": 2, "status": "success"},
    ]))
    assert MODULE.trace_completion(trace) == (2, 2, 1)
    trace.write_text(json.dumps([
        {"index": "before-1", "status": "success"},
        {"index": 1, "status": "success"},
        {"index": 1, "status": "success"},
    ]))
    with pytest.raises(ValueError, match="qualified indexed actions"):
        MODULE.trace_completion(trace)


def test_evidence_hash_and_symlink_fail_closed(tmp_path):
    source = tmp_path / "trace.json"
    source.write_text("[]")
    receipt = tmp_path / "receipt.json"
    receipt.write_text("{}")
    assert MODULE.evidence_path(receipt, "trace.json", MODULE.sha256(source)) == source
    with pytest.raises(ValueError, match="hash mismatch"):
        MODULE.evidence_path(receipt, "trace.json", "0" * 64)
    link = tmp_path / "linked.json"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symlink"):
        MODULE.ref(link)


def test_public_screenshot_requires_review_and_exact_png_bytes(tmp_path):
    png = tmp_path / "page.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"dummy")
    spec = {
        "path": str(png),
        "sha256": MODULE.sha256(png),
        "kind": "last_observed_page",
        "privacy_reviewed": True,
        "caption_zh": "最后观察页",
        "caption_en": "Last observed page",
    }
    assert MODULE.reviewed_screenshot(spec)["sha256"] == spec["sha256"]
    with pytest.raises(ValueError, match="privacy review"):
        MODULE.reviewed_screenshot({**spec, "privacy_reviewed": False})
    with pytest.raises(ValueError, match="hash mismatch"):
        MODULE.reviewed_screenshot({**spec, "sha256": "0" * 64})


def test_task243_menu_proof_requires_ready_unique_customers_submenu(tmp_path):
    path = tmp_path / "menu-target-proof.json"
    rows = [
        {
            "step": 11,
            "readiness_gate": "load+CSS+menu module+document click handler",
            "passed": True,
        },
        {"step": 11, "matched_count": 1, "matched_texts": ["CUSTOMERS"]},
        {"step": 12, "visible_match_count": 1},
    ]
    path.write_text(json.dumps(rows))
    assert MODULE.validate_task243_menu_proof(path, MODULE.sha256(path))["path"] == str(path)
    rows[2]["visible_match_count"] = 0
    path.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match="incomplete"):
        MODULE.validate_task243_menu_proof(path, MODULE.sha256(path))
    with pytest.raises(ValueError, match="hash mismatch"):
        MODULE.validate_task243_menu_proof(path, "0" * 64)


def test_cause_audit_requires_historical_trace_and_bound_receipts(tmp_path):
    trace = tmp_path / "historical-trace.json"
    trace.write_text("[]")
    receipt = tmp_path / "receipt.json"
    receipt.write_text('{"observation":"red"}')
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps({
        "task_id": 156,
        "historical_result": {"trace_sha256": MODULE.sha256(trace)},
        "evidence_receipt_sha256": MODULE.sha256(receipt),
        "reviewed_cause": {
            "category": "stale_layout_snapshot",
            "mechanism_zh": "布局过期",
            "mechanism_en": "Stale layout",
        },
    }))
    manifest = tmp_path / "audits.json"
    entry = {
        "task_id": 156,
        "audit": str(audit),
        "historical_trace": str(trace),
        "evidence_receipts": [str(receipt)],
        "fix": {"zh": "已复测", "en": "Retested"},
        "source_location": {"zh": "布局代码", "en": "Layout code"},
    }
    manifest.write_text(json.dumps({"schema": "original36_cause_audits/1", "audits": [entry]}))
    audits, binding = MODULE.load_cause_audits(manifest)
    assert audits[156]["cause"]["summary_en"] == "Stale layout"
    assert audits[156]["cause"]["category"] == "stale_layout_snapshot"
    assert audits[156]["cause"]["code_or_process"] == "stale_layout_snapshot"
    assert binding["sha256"] == MODULE.sha256(manifest)
    revised_audit = json.loads(audit.read_text())
    revised_audit["reviewed_cause"].update({
        "category": "moli_json_main_document_parsed_as_html",
        "code_or_process": "json_response_dispatched_to_html_parser",
    })
    audit.write_text(json.dumps(revised_audit))
    revised, _ = MODULE.load_cause_audits(manifest)
    assert revised[156]["cause"]["category"] == "moli_json_main_document_parsed_as_html"
    assert revised[156]["cause"]["code_or_process"] == "json_response_dispatched_to_html_parser"
    receipt.write_text('{"observation":"green"}')
    with pytest.raises(ValueError, match="unbound cause evidence"):
        MODULE.load_cause_audits(manifest)
    receipt.write_text('{"observation":"red"}')
    trace.write_text("[{}]")
    with pytest.raises(ValueError, match="historical trace differs"):
        MODULE.load_cause_audits(manifest)
