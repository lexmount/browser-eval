#!/usr/bin/env python3
"""Apply the approved React/MUI policy and replace the ineligible Steam case.

The transformation is deliberately hash-gated.  It can create a provisional
dataset from the independently captured Blueprint body, or a final dataset
from two immutable, scope-bound browser observations of the declared ``main``
region.  No provider output is read by this script.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness.web_fetch.body_coverage import body_requirements
from harness.web_fetch.reference_lifecycle import scope_identity


EXPECTED_INPUT_SHA256 = "d8019897025bae95e9b6e5db9988d68f4052780d3b57fc933bcf9fb14c385cbc"
EXPECTED_QUALIFICATION_SHA256 = "fd5612377f64889ffe049fc009fa4387a45efc29230a5d6cb1cce61ad568bf6d"
EXPECTED_RENDERED_BODY_SHA256 = "fc0c566c956d51f1d36fc3fe1dae07c436a45e716752c8e3b6c17b47053b24f7"
REPLACEABLE_OUTPUT_SHA256 = "a89f52df7ec0b31da0364c7b5f2b315ed9631745abb10a5e5d39ab4c445ea2e1"
REPLACEABLE_RECEIPT_SHA256 = "191fe03e559da9be7610060c3190665cbc4a5ad355d3c0a929984ddfd2e2393c"
BLUEPRINT_ID = "dynamic-105-blueprint-docs"
BLUEPRINT_URL = "https://blueprintjs.com/docs/"
SCOPE = "Blueprint documentation main content after initial automatic load"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def read_hash_gated(path: Path, expected: str) -> bytes:
    raw = path.read_bytes()
    actual = digest(raw)
    if actual != expected:
        raise ValueError(f"hash mismatch for {path}: {actual} != {expected}")
    return raw


def qualification(path: Path) -> dict:
    payload = json.loads(read_hash_gated(path, EXPECTED_QUALIFICATION_SHA256))
    rows = payload.get("observations") or []
    if len(rows) != 1 or rows[0].get("id") != "candidate-262-blueprintjs-com":
        raise ValueError("qualification must contain exactly the Blueprint candidate")
    row = rows[0]
    if row.get("eligible") is not True:
        raise ValueError("Blueprint candidate did not pass the independent qualification gate")
    for label in ("capture_a", "capture_b"):
        capture = row.get(label) or {}
        if (capture.get("status") != 200 or capture.get("final_url") != BLUEPRINT_URL
                or capture.get("completion", {}).get("satisfied") is not True
                or capture.get("initial_sha256") != "39b78bdb58c2e27a7e972e9f5feab8654e6c1cd00e6df168f150a7c903434fae"
                or capture.get("rendered_sha256") != EXPECTED_RENDERED_BODY_SHA256):
            raise ValueError(f"invalid Blueprint qualification {label}")
    return row


def provisional_reference(path: Path) -> str:
    body = read_hash_gated(path, EXPECTED_RENDERED_BODY_SHA256).decode()
    marker = "Edit this page\nBlueprint\nBlueprint is a React-based UI toolkit for the web."
    position = body.find(marker)
    if position < 0:
        raise ValueError("Blueprint main-content marker is absent")
    return body[position:].strip()


def blueprint_case(reference: str, qualification_row: dict, checked_at: str) -> dict:
    reference_hash = digest(reference.encode())
    captures = {}
    for source_key, target_key in (("capture_a", "capture_a"), ("capture_b", "capture_b")):
        source = qualification_row[source_key]
        captures[target_key] = {
            "status": source["status"],
            "final_url": source["final_url"],
            "document_lang": source.get("document_lang"),
            "initial_sha256": source["initial_sha256"],
            "target_sha256": reference_hash,
            "target_chars": len(reference),
            "target_record_count": 0,
        }
    return {
        "id": BLUEPRINT_ID,
        "name": "Blueprint · 客户端渲染入门正文",
        "url": BLUEPRINT_URL,
        "category": "dynamic_content",
        "track": "dynamic",
        "task_kind": "body",
        "expected_any": [],
        "description": SCOPE,
        "task_prompt": (
            "提取 Blueprint 文档首页首次自动加载后 main 区域的完整正文；不得点击、输入、"
            "搜索、滚动或翻页。排除顶栏、侧栏、页脚、广告、Cookie 和登录提示。"
        ),
        "retrieval_hints": {
            "version": 2,
            "wait_until": "domcontentloaded",
            "completion": {
                "selector": "main",
                "minimum_count": 1,
                "timeout_ms": 45000,
                "delay_ms": 1000,
            },
            "extraction": {
                "selector": "main",
                "maximum_count": 1,
                "exclude_selectors": ["nav", "header", "footer"],
                "fallback": "body",
            },
        },
        "forbidden_any": [
            "Access denied", "403 Forbidden", "Verify you are human",
            "Checking your browser", "CAPTCHA", "Just a moment",
            # Stable toolbar text independently observed outside ``main``.
            # The task explicitly excludes the top bar and sidebar.
            "Dark theme",
        ],
        # Fixed before the two scope-bound main observations.  Do not derive
        # this value from a later reference refresh or the task identity would
        # silently change between qualification and measurement.
        "min_content_chars": 959,
        "ground_truth_main_content": reference,
        "source_verification": {
            "rule_scope": SCOPE,
            "target_selectors": ["main"],
            "exclude_selectors": ["nav", "header", "footer", "script", "style"],
            "record_selectors": [],
            "interaction_boundary": (
                "initial automatic load only; no login, click, input, scroll, search, "
                "pagination, or consent action"
            ),
            "completion": {
                "condition": "main visible, declared anchors present, and stable",
                "timeout_ms": 45000,
                "stable_samples": 4,
                "minimum_chars": 1200,
                "minimum_records": 0,
                "loading_must_end": True,
            },
            "initial_classification": ["initial_missing", "initial_missing"],
            **captures,
            "provider_output_used_for_rule": False,
            "rule_family": "body",
            "rule_profile": "fixed_document_body",
            "state": "valid",
            "checked_at": checked_at,
            "method": "Independent visible browser DOM; complete declared scope",
            "reference_sha256": reference_hash,
            "selector": "main",
            "reference_scope": SCOPE,
        },
        "required_facts": [
            {"id": "identity", "alternatives": ["Blueprint is a React-based UI toolkit for the web."]},
            {"id": "purpose", "alternatives": ["complex data-dense interfaces for desktop applications"]},
            {"id": "install", "alternatives": ["pnpm add @blueprintjs/core react react-dom"]},
            {"id": "import", "alternatives": ["Import React components from the appropriate package."]},
        ],
        "fact_coverage_threshold": 1.0,
        "body_requirements": body_requirements(reference),
        "source_benchmark": {
            "capture_scope": SCOPE,
            "render_evidence": {
                "scope": SCOPE,
                "rendered_truncated": False,
                "completion": {
                    "condition": "declared main content was visible and stable in independent captures",
                    "timeout_ms": 45000,
                    "elapsed_ms": max(
                        qualification_row["capture_a"]["elapsed_ms"],
                        qualification_row["capture_b"]["elapsed_ms"],
                    ),
                    "satisfied": True,
                    "checked_at": checked_at,
                },
            },
        },
    }


def load_observation(path: Path, expected_scope: dict) -> tuple[dict, bytes]:
    raw = path.read_bytes()
    batch = json.loads(raw)
    if set(batch) != {BLUEPRINT_ID}:
        raise ValueError(f"browser observation scope is not {BLUEPRINT_ID}: {path}")
    row = batch[BLUEPRINT_ID]
    if (row.get("case_id") != BLUEPRINT_ID or row.get("status") != "success"
            or row.get("completion", {}).get("satisfied") is not True
            or row.get("scope_identity") != expected_scope):
        raise ValueError(f"invalid or differently scoped browser observation: {path}")
    return row, raw


def transform(args: argparse.Namespace) -> tuple[dict, dict]:
    source_raw = read_hash_gated(args.input, EXPECTED_INPUT_SHA256)
    dataset = json.loads(source_raw)
    qrow = qualification(args.qualification)
    provisional = provisional_reference(args.rendered_body)
    checked_at = qrow["capture_b"].get("checked_at") or json.loads(
        args.qualification.read_text())["checked_at"]
    replacement = blueprint_case(provisional, qrow, checked_at)

    obs_hashes: list[str] = []
    if bool(args.browser_observation_a) != bool(args.browser_observation_b):
        raise ValueError("provide both browser observations or neither")
    if args.browser_observation_a:
        # These two captures predate the audited purity-rule correction and
        # are bound to the otherwise identical qualification contract.
        observation_contract = deepcopy(replacement)
        observation_contract["forbidden_any"].remove("Dark theme")
        expected_scope = scope_identity(observation_contract)
        first, first_raw = load_observation(args.browser_observation_a, expected_scope)
        second, second_raw = load_observation(args.browser_observation_b, expected_scope)
        if first["content_text"] != second["content_text"]:
            raise ValueError("independent Blueprint main-content observations differ")
        reference = first["content_text"]
        checked_at = max(first["checked_at"], second["checked_at"])
        replacement = blueprint_case(reference, qrow, checked_at)
        reference_hash = digest(reference.encode())
        for key, row in (("capture_a", first), ("capture_b", second)):
            replacement["source_verification"][key].update(
                target_sha256=reference_hash,
                target_chars=len(reference),
                browser_observation_sha256=digest(
                    first_raw if key == "capture_a" else second_raw),
            )
        replacement["source_verification"]["browser_observation_sha256"] = digest(first_raw)
        replacement["source_verification"]["second_browser_observation_sha256"] = digest(second_raw)
        obs_hashes = [digest(first_raw), digest(second_raw)]

    cases = deepcopy(dataset["cases"])
    react = next(case for case in cases if case["id"] == "dynamic-react-cart")
    if react.get("forbidden_any", []).count("Add to cart") != 1:
        raise ValueError("React Cart exclusion is not in the expected pre-adjudication state")
    react["forbidden_any"].remove("Add to cart")
    steam_index = next(i for i, case in enumerate(cases)
                       if case["id"] == "dynamic-105-steam-city-search")
    cases[steam_index] = replacement
    if len(cases) != 100 or len({case["id"] for case in cases}) != 100:
        raise ValueError("replacement did not retain 100 unique task IDs")
    if len({case["url"] for case in cases}) != 100:
        raise ValueError("replacement did not retain 100 unique URLs")
    dataset["cases"] = cases
    output_raw = json_bytes(dataset)
    receipt = {
        "schema_version": 1,
        "kind": "approved_dynamic_100_policy_and_case_replacement",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_dataset_sha256": digest(source_raw),
        "output_dataset_sha256": digest(output_raw),
        "qualification_summary_sha256": EXPECTED_QUALIFICATION_SHA256,
        "qualification_rendered_body_sha256": EXPECTED_RENDERED_BODY_SHA256,
        "browser_observation_sha256": obs_hashes,
        "provider_output_used_to_define_rules": False,
        "provider_calls_made_by_this_transformation": 0,
        "approved_adjudications": [
            {
                "case_id": "dynamic-react-cart",
                "decision": "pass when all 16 target records are complete even if each retains its attached Add to cart control",
                "change": "remove Add to cart from forbidden_any",
            },
            {
                "case_id": "dynamic-104-mui-data-grid",
                "decision": "remain fail because output crosses into a separately named Pro tier and Cookie surface",
                "change": "none",
            },
            {
                "old_case_id": "dynamic-105-steam-city-search",
                "new_case_id": BLUEPRINT_ID,
                "decision": "remove Steam because its complete target was already present in the initial HTTP response",
                "old_url": "https://store.steampowered.com/search/?term=city",
                "new_url": BLUEPRINT_URL,
                "stratum_change": "real_public volatile linked-product list -> stable_demo client-rendered document",
            },
            {
                "case_id": BLUEPRINT_ID,
                "decision": "fail when the returned answer includes the independently observed Dark theme toolbar outside main",
                "change": "add Dark theme to forbidden_any to enforce the predeclared top-bar/sidebar exclusion",
                "rule_revision_timing": "after measurement, prompted by the independent audit",
                "rule_evidence": "independent no-interaction browser accessibility observation",
            },
        ],
    }
    return dataset, receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt-output", type=Path, required=True)
    parser.add_argument("--qualification", type=Path, required=True)
    parser.add_argument("--rendered-body", type=Path, required=True)
    parser.add_argument("--browser-observation-a", type=Path)
    parser.add_argument("--browser-observation-b", type=Path)
    parser.add_argument("--replace-existing", action="store_true")
    args = parser.parse_args()
    if args.replace_existing:
        expected = ((args.output, REPLACEABLE_OUTPUT_SHA256),
                    (args.receipt_output, REPLACEABLE_RECEIPT_SHA256))
        for path, expected_hash in expected:
            if not path.is_file() or digest(path.read_bytes()) != expected_hash:
                parser.error(f"refusing to replace unexpected existing file: {path}")
    elif args.receipt_output.exists():
        parser.error("receipt output already exists")
    elif args.output.exists() and args.output.resolve() != args.input.resolve():
        parser.error("output already exists")
    try:
        dataset, receipt = transform(args)
    except (OSError, KeyError, StopIteration, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    output_raw = json_bytes(dataset)
    args.output.write_bytes(output_raw)
    args.receipt_output.write_bytes(json_bytes(receipt))
    print(json.dumps({
        "dataset_sha256": digest(output_raw),
        "receipt_sha256": digest(args.receipt_output.read_bytes()),
        "case_count": len(dataset["cases"]),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
