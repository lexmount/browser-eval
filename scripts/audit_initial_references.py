"""Reconstruct references from saved initial HTML, offline and without provider outputs.

Usage: python -B scripts/audit_initial_references.py --output research/initial-reference-audit.json
Without --output, emit JSON to stdout. Compact summaries go to stderr.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from importlib.metadata import version
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.reference_inputs import input_tree, read_dataset_bytes
from harness.web_fetch.reference_quality import clean_reference_text
from harness.web_fetch.safety import contained_path, safe_identifier
from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.source_capture import extract_html


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _score(case: dict, text: str) -> dict:
    measured = score_attempt(case, text.encode('utf-8'), ok=True)
    return {key: measured[key] for key in (
        "content_pass", "fact_hits", "fact_total", "fact_coverage", "word_f1",
    )}


def reconstruct_quotes_json(raw: bytes, case: dict) -> dict:
    """Decode the known Quotes var-data array; never evaluate JavaScript."""
    match = re.search(r"\bvar\s+data\s*=\s*", raw.decode("utf-8"))
    if match is None:
        raise ValueError("Quotes data array absent")
    data, _ = json.JSONDecoder().raw_decode(raw.decode("utf-8")[match.end():])
    if not isinstance(data, list) or not data:
        raise ValueError("Quotes data must be a nonempty array")
    text = clean_reference_text("\n".join(
        item["text"] + " by " + item["author"]["name"] + " Tags: " + " ".join(item["tags"])
        for item in data
    ))
    return {
        "state": "reconstructed", "method": "json.JSONDecoder.raw_decode(var data); no JS",
        "entries": len(data), "reconstructed_chars": len(text),
        "reconstructed_sha256": sha256(text.encode("utf-8")),
        "exact_reconstruction": text == case.get("ground_truth_main_content"),
        "metrics": _score(case, text), "js_executed": False,
        "conclusion": "Quote data is available in initial HTML; a must-execute-JS claim is unsupported.",
    }


def audit_case(case: dict, evaluation: Path, *, sources: Path | None = None) -> dict:
    # Normalize macOS' /var -> /private/var alias before containment and
    # publication-path checks so temporary evaluation roots stay comparable.
    evaluation = evaluation.resolve()
    identifier = safe_identifier(case["id"])
    sources = sources or input_tree(evaluation / "dataset.json", "sources")
    folder = contained_path(sources, identifier)
    candidates = [contained_path(folder, name) for name in (
        "current-source.html.txt", "initial.html.txt", "source.html.txt")]
    source = next((path for path in candidates if path.is_file()), None)
    evidence_candidates=[contained_path(folder,name) for name in (
        "current-capture.json","evidence.json")]
    evidence_path=next((path for path in evidence_candidates if path.is_file()),
                       evidence_candidates[-1])
    rendered_path = contained_path(folder, "rendered.txt")
    def public_path(path):
        if path is None:return None
        try:return path.relative_to(ROOT).as_posix()
        except ValueError:
            prefix = evaluation.relative_to(ROOT).as_posix() + "/" if evaluation.is_relative_to(ROOT) else ""
            return prefix + "sources/" + path.relative_to(sources).as_posix()
    reference = case.get("ground_truth_main_content")
    has_reference = isinstance(reference, str) and bool(reference.strip())
    verification = case.get("source_verification") or {}
    row = {
        "case_id": identifier, "name": case.get("name"), "track": case.get("track", "web"),
        "source_state": "present" if source else "absent",
        "source_path": public_path(source),
        "source_candidates": [public_path(path) for path in candidates],
        "source_sha256": None, "source_bytes": None,
        "source_evidence_path": public_path(evidence_path) if evidence_path.is_file() else None,
        "source_evidence_state": "present" if evidence_path.is_file() else "absent",
        "source_evidence_sha256": None, "recorded_source_sha256": None,
        "source_hash_matches_metadata": None,
        "reference_state": "present" if has_reference else "absent",
        "reference_sha256": sha256(reference.encode("utf-8")) if has_reference else None,
        "reference_chars": len(reference) if has_reference else None,
        "recorded_reference_sha256": verification.get("reference_sha256"),
        "reference_hash_matches_metadata": None,
        "recorded_reference_selector": verification.get("selector"),
        "recorded_reference_method": verification.get("method"),
        "rendered_snapshot_state": "present" if rendered_path.is_file() else "absent",
        "rendered_snapshot_path": public_path(rendered_path) if rendered_path.is_file() else None,
        "rendered_snapshot_sha256": None,
        "paired_capture_files_present": bool(source and rendered_path.is_file()),
        "extraction_state": "not_attempted", "extraction_error_type": None,
        "selector": None, "reconstructed_sha256": None, "reconstructed_chars": None,
        "exact_reconstruction": None, "errors": [],
        "js_requirement": "not_established_by_this_audit",
    }
    if has_reference and row["recorded_reference_sha256"]:
        row["reference_hash_matches_metadata"] = (
            row["reference_sha256"] == row["recorded_reference_sha256"])
    evidence = {}
    if evidence_path.is_file():
        try:
            evidence_raw = evidence_path.read_bytes()
            row["source_evidence_sha256"] = sha256(evidence_raw)
            evidence = json.loads(evidence_raw)
            row["recorded_source_sha256"] = evidence.get("initial_sha256") or evidence.get("sha256")
            row["recorded_source_method"] = evidence.get("source")
            row["recorded_source_checked_at"] = evidence.get("initial_checked_at") or evidence.get("checked_at")
        except Exception as error:
            row["errors"].append({"stage": "source_metadata", "type": type(error).__name__})
    if rendered_path.is_file():
        try:
            row["rendered_snapshot_sha256"] = sha256(rendered_path.read_bytes())
        except Exception as error:
            row["errors"].append({"stage": "rendered_snapshot", "type": type(error).__name__})
    if source is None:
        return row
    try:
        raw = source.read_bytes()
        row.update(source_sha256=sha256(raw), source_bytes=len(raw))
        if row["recorded_source_sha256"]:
            row["source_hash_matches_metadata"] = row["source_sha256"] == row["recorded_source_sha256"]
        # Use the original task URL, exactly as in the initial 98-reference audit.
        _, text, selector, _ = extract_html(raw, case["url"])
        text = clean_reference_text(text)
        row.update(extraction_state="extracted", selector=selector,
                   reconstructed_sha256=sha256(text.encode("utf-8")), reconstructed_chars=len(text),
                   exact_reconstruction=text == reference if has_reference else None)
        if has_reference:
            row["metrics"] = _score(case, text)
    except Exception as error:
        row.update(extraction_state="error", extraction_error_type=type(error).__name__)
        return row
    if identifier == "javascript-spa":
        try:
            row["quotes_json_reconstruction"] = reconstruct_quotes_json(raw, case)
        except Exception as error:
            row["quotes_json_reconstruction"] = {"state": "error", "error_type": type(error).__name__}
    return row


def summarize(rows: list[dict]) -> dict:
    return {
        "cases": len(rows),
        "initial_html_present": sum(row["source_state"] == "present" for row in rows),
        "exact_reconstructions": sum(row["exact_reconstruction"] is True for row in rows),
        "nonexact_case_ids": [row["case_id"] for row in rows if row["exact_reconstruction"] is False],
        "unknown_reconstruction_case_ids": [row["case_id"] for row in rows if row["exact_reconstruction"] is None],
        "absent_initial_html_case_ids": [row["case_id"] for row in rows if row["source_state"] == "absent"],
        "extraction_error_case_ids": [row["case_id"] for row in rows if row["extraction_state"] == "error"],
        "source_hash_mismatch_case_ids": [row["case_id"] for row in rows if row["source_hash_matches_metadata"] is False],
        "reference_hash_mismatch_case_ids": [row["case_id"] for row in rows if row["reference_hash_matches_metadata"] is False],
        "paired_capture_file_case_ids": [row["case_id"] for row in rows if row["paired_capture_files_present"]],
    }


def audit_dataset(dataset: Path) -> dict:
    dataset = dataset.resolve()
    raw = read_dataset_bytes(dataset)
    data = json.loads(raw)
    sources = input_tree(dataset, "sources")
    rows = [audit_case(case, dataset.parent, sources=sources) for case in data["cases"]]
    return {
        "schema_version": 1,
        "dataset_path": (dataset.relative_to(ROOT).as_posix()
                         if dataset.is_relative_to(ROOT) else dataset.name),
        "dataset_sha256": sha256(raw),
        "method": "extract_html(saved initial bytes, case URL), then clean_reference_text; exact Unicode comparison to current dataset reference",
        "js_executed": False, "network_used": False, "provider_outputs_used": False,
        "implementation_sha256": {name: sha256((ROOT / name).read_bytes()) for name in (
            "scripts/audit_initial_references.py", "harness/web_fetch/source_capture.py",
            "harness/web_fetch/reference_quality.py", "harness/web_fetch/metrics.py",
        )},
        "runtime": {"python": sys.version.split()[0], "beautifulsoup4": version("beautifulsoup4")},
        "summary": summarize(rows),
        "by_track": {track: summarize([row for row in rows if row["track"] == track])
                     for track in sorted({row["track"] for row in rows})},
        "limitations": [
            "Exact reconstruction demonstrates recoverability of the frozen reference from the saved response, not live-site behavior or computed DOM visibility.",
            "Missing initial HTML/reference has null comparison and hash, not false or zero coverage.",
            "Nonexact extraction does not prove content absence or JS necessity; embedded data or another HTTP request may suffice.",
            "Capture presence checks inspect only named files under each case source directory; no global claim that browser evidence never existed is made.",
            "Paired file presence and recorded methods are not independent verification of browser capture provenance or completion.",
        ],
        "cases": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=ROOT / "evaluation/dataset.json")
    parser.add_argument("--output", type=Path, help="Write generated JSON here; otherwise stdout")
    args = parser.parse_args()
    try:
        audit = audit_dataset(args.dataset)
        serialized = json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            output = args.output.resolve()
            # Never let an artifact overwrite the dataset or its source evidence.
            if output == args.dataset.resolve() or output.is_relative_to(input_tree(args.dataset, "sources")):
                raise ValueError("Output overlaps audit input")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(serialized, encoding="utf-8")
        else:
            print(serialized, end="")
    except Exception as error:
        print(f"Audit failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from None
    for track, summary in audit["by_track"].items():
        print(f"{track}: {summary['exact_reconstructions']}/{summary['cases']} exact; "
              f"nonexact={','.join(summary['nonexact_case_ids']) or 'none'}; "
              f"unknown={','.join(summary['unknown_reconstruction_case_ids']) or 'none'}", file=sys.stderr)
    for row in audit["cases"]:
        quotes = row.get("quotes_json_reconstruction", {})
        if quotes.get("state") == "reconstructed":
            print(f"{row['case_id']}: JSON-only {quotes['entries']} quotes; "
                  f"pass={quotes['metrics']['content_pass']}; F1={quotes['metrics']['word_f1']}; "
                  "does not establish must-execute-JS", file=sys.stderr)


if __name__ == "__main__":
    main()
