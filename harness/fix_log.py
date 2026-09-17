"""Validate the shared, hash-bound browser fix-log evidence model."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from PIL import Image

EMPTY_FIX_LOG = {
    "schema": "lex_browser_use_fix_log/1",
    "records": [],
    "artifacts": [],
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_fix_log(
    profile: dict[str, Any], *, sanitize_value: Callable[[Any], Any]
) -> dict[str, Any]:
    """Read and validate an optional public fix manifest.

    The manifest can point to local source artifacts, but every reader-facing
    value must already survive the caller's publication sanitizer unchanged.
    Raw traces, HAR files, logs, and arbitrary attachment types are rejected.
    """

    binding = profile.get("fix_log")
    if not binding:
        return {**EMPTY_FIX_LOG, "records": [], "artifacts": []}
    source = Path(binding["source_path"])
    if source.is_symlink() or not source.is_file() or _sha256(source) != binding["source_sha256"]:
        raise ValueError("fix log is not hash-bound")
    model = _read_json(source)
    if model.get("schema") != EMPTY_FIX_LOG["schema"]:
        raise ValueError("unsupported fix log schema")
    if set(model) != {"schema", "records", "artifacts"}:
        raise ValueError("fix log contains unsupported fields")

    def digest(value: Any) -> None:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("fix log requires an exact SHA-256")

    def localized(value: Any) -> None:
        if (
            not isinstance(value, dict)
            or set(value) != {"zh", "en"}
            or not all(isinstance(text, str) and text.strip() for text in value.values())
        ):
            raise ValueError("fix log prose requires both languages")

    def public_text(value: Any) -> None:
        text = json.dumps(value, ensure_ascii=False)
        if sanitize_value(value) != value or re.search(r"/(?:Users|home|private|tmp|var)/", text):
            raise ValueError("fix log contains private data or local paths")
        if re.search(
            r'"(?:authorization|cookie|set-cookie|password|access_token|api_key)"\s*:',
            text,
            re.I,
        ):
            raise ValueError("fix log receipt contains credential fields")

    artifacts: dict[str, dict[str, Any]] = {}
    for artifact in model.get("artifacts", []):
        artifact_id = artifact["id"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", artifact_id) or artifact_id in artifacts:
            raise ValueError("invalid or duplicate fix artifact ID")
        path = Path(artifact["path"])
        digest(artifact["sha256"])
        if (
            artifact.get("public_safe") is not True
            or path.is_symlink()
            or any(parent.is_symlink() for parent in path.parents)
            or not path.is_file()
            or _sha256(path) != artifact["sha256"]
        ):
            raise ValueError("fix artifact is unsafe, missing or not hash-bound")
        kind = artifact.get("kind")
        if kind == "image":
            with Image.open(path) as image:
                if image.format not in {"PNG", "JPEG"}:
                    raise ValueError("fix images must be measured PNG/JPEG captures")
                artifact["extension"] = ".png" if image.format == "PNG" else ".jpg"
                image.verify()
            provenance = artifact["provenance"]
            if (
                provenance.get("observation") not in {"last_observed_page", "exact_error_frame"}
                or not provenance.get("capture_method")
                or not provenance.get("run_id")
            ):
                raise ValueError("fix image requires observation provenance")
            localized(provenance.get("timing_note"))
            if provenance["observation"] == "exact_error_frame":
                localized(provenance.get("error_time_basis"))
            public_text(provenance)
        elif kind == "receipt":
            if path.stat().st_size > 1_000_000:
                raise ValueError("fix receipt exceeds compact public evidence limit")
            receipt = _read_json(path)
            if receipt.get("schema") not in {
                "lex_browser_use_fix_test_receipt/1",
                "lex_browser_use_fix_comparison_receipt/1",
                "lex_browser_use_fix_certification_receipt/1",
            }:
                raise ValueError("fix artifacts require allowlisted public receipt schemas")
            allowed = {
                "schema",
                "run_id",
                "method",
                "version",
                "source_base",
                "source_diff_sha256",
                "binary_sha256",
                "input_sha256",
                "trajectory_sha256",
                "task_contract_sha256",
                "action_count",
                "command",
                "result",
                "measurements",
                "source_diff_status",
                "status",
                "scope",
                "task_ids",
                "comparison_receipts_sha256",
            }
            if set(receipt) - allowed:
                raise ValueError(
                    "fix receipt contains unsupported fields; raw logs are not public receipts"
                )
            public_text(receipt)
        else:
            raise ValueError("unsupported fix artifact kind")
        artifacts[artifact_id] = artifact
        public_text({key: value for key, value in artifact.items() if key != "path"})

    def reference(identifier: str, kind: str) -> None:
        if identifier not in artifacts or artifacts[identifier]["kind"] != kind:
            raise ValueError("fix record references missing or incompatible evidence")

    ids: set[str] = set()
    for record in model.get("records", []):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", record["id"]) or record["id"] in ids:
            raise ValueError("invalid or duplicate fix record ID")
        ids.add(record["id"])
        public_text(record)
        for key in ("title", "symptom", "root_cause", "principle", "limitations"):
            localized(record.get(key))
        if record.get("scope") not in {"diagnostic", "formal"}:
            raise ValueError("fix record must distinguish diagnostic and formal scope")
        basis = record.get("comparison_basis")
        if basis is None:
            basis = {"kind": "identical_inputs"}
            record["comparison_basis"] = basis
        if not isinstance(basis, dict):
            raise ValueError("fix comparison basis must be an object")
        basis_kind = basis.get("kind")
        if basis_kind == "identical_inputs":
            if set(basis) != {"kind"}:
                raise ValueError("identical-input comparison basis has unsupported fields")
            digest(record.get("input_sha256"))
        elif basis_kind == "same_task_contract":
            if set(basis) != {"kind", "task_contract_sha256"}:
                raise ValueError("same-task comparison requires one task contract binding")
            digest(basis.get("task_contract_sha256"))
            if "input_sha256" in record:
                raise ValueError("revised trajectories cannot claim one shared input")
        else:
            raise ValueError("unsupported fix comparison basis")
        if record["scope"] == "formal":
            reference(record.get("certification"), "receipt")
            certification = _read_json(Path(artifacts[record["certification"]]["path"]))
            certification_key = (
                "input_sha256" if basis_kind == "identical_inputs" else "task_contract_sha256"
            )
            certification_value = (
                record["input_sha256"]
                if basis_kind == "identical_inputs"
                else basis["task_contract_sha256"]
            )
            comparison_receipts = certification.get("comparison_receipts_sha256")
            if not isinstance(comparison_receipts, dict) or set(comparison_receipts) != {
                "before",
                "after",
            }:
                raise ValueError("formal fix evidence must bind both comparison receipts")
            for receipt_sha256 in comparison_receipts.values():
                digest(receipt_sha256)
            expected_comparison_receipts = {}
            for side in ("before", "after"):
                comparison_id = record.get(side, {}).get("receipt")
                reference(comparison_id, "receipt")
                expected_comparison_receipts[side] = artifacts[comparison_id]["sha256"]
            if (
                certification.get("schema") != "lex_browser_use_fix_certification_receipt/1"
                or certification.get("status") != "certified"
                or certification.get(certification_key) != certification_value
                or comparison_receipts != expected_comparison_receipts
            ):
                raise ValueError("formal fix evidence requires a matching certification receipt")
        if not record.get("code_paths") or not record.get("tests"):
            raise ValueError("fix record requires code locations and tests")
        for code in record["code_paths"]:
            if not code.get("repository") or not code.get("symbol"):
                raise ValueError("fix code location is incomplete")
            code_path = PurePosixPath(code["path"])
            if code_path.is_absolute() or ".." in code_path.parts:
                raise ValueError("fix code paths must be repository-relative")
        for side in ("before", "after"):
            run = record[side]
            for key in ("method", "version", "source_base", "run_id"):
                if not isinstance(run.get(key), str) or not run[key].strip():
                    raise ValueError("fix comparison identity is incomplete")
            digest(run.get("binary_sha256"))
            if run.get("source_diff_sha256") is None:
                if (
                    record["scope"] != "diagnostic"
                    or run.get("source_diff_status") != "not_recorded"
                ):
                    raise ValueError(
                        "missing source diff hash requires explicit not_recorded status"
                    )
            else:
                digest(run["source_diff_sha256"])
            localized(run.get("observation"))
            reference(run.get("receipt"), "receipt")
            receipt = _read_json(Path(artifacts[run["receipt"]]["path"]))
            if basis_kind == "identical_inputs":
                if any(key in run for key in ("input_sha256", "trajectory_sha256", "action_count")):
                    raise ValueError("identical-input comparisons use the shared record input")
                basis_matches_receipt = receipt.get("input_sha256") == record["input_sha256"]
            else:
                digest(run.get("input_sha256"))
                digest(run.get("trajectory_sha256"))
                if (
                    not isinstance(run.get("action_count"), int)
                    or isinstance(run.get("action_count"), bool)
                    or run["action_count"] < 1
                ):
                    raise ValueError("revised trajectory requires a positive action count")
                basis_matches_receipt = all(
                    receipt.get(key) == value
                    for key, value in (
                        ("task_contract_sha256", basis["task_contract_sha256"]),
                        ("input_sha256", run["input_sha256"]),
                        ("trajectory_sha256", run["trajectory_sha256"]),
                        ("action_count", run["action_count"]),
                    )
                )
            if (
                receipt.get("schema") != "lex_browser_use_fix_comparison_receipt/1"
                or any(
                    receipt.get(key) != run[key]
                    for key in (
                        "method",
                        "version",
                        "source_base",
                        "source_diff_sha256",
                        "binary_sha256",
                        "run_id",
                    )
                )
                or not basis_matches_receipt
            ):
                raise ValueError("fix comparison claims do not match the bound receipt")
            if (
                run.get("source_diff_sha256") is None
                and receipt.get("source_diff_status") != "not_recorded"
            ):
                raise ValueError("missing source diff provenance must match the receipt")
            if run.get("image"):
                reference(run["image"], "image")
        if basis_kind == "same_task_contract":
            before = record["before"]
            after = record["after"]
            if (
                before["input_sha256"] == after["input_sha256"]
                or before["trajectory_sha256"] == after["trajectory_sha256"]
            ):
                raise ValueError("same-task comparison must bind distinct revised trajectories")
        for test in record["tests"]:
            if not test.get("command") or not test.get("result"):
                raise ValueError("fix test requires exact command and recorded result")
            reference(test.get("receipt"), "receipt")
            receipt = _read_json(Path(artifacts[test["receipt"]]["path"]))
            if (
                receipt.get("schema") != "lex_browser_use_fix_test_receipt/1"
                or receipt.get("command") != test["command"]
                or receipt.get("result") != test["result"]
            ):
                raise ValueError("fix test claims do not match the bound receipt")
        if record.get("pr_url") and not re.fullmatch(r"https://[^\s<>\"']+", record["pr_url"]):
            raise ValueError("fix PR must use an HTTPS URL")
    model["source_sha256"] = binding["source_sha256"]
    return model
