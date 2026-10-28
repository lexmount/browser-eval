"""Compile the audited access-restricted URL rules into an executable dataset.

The Markdown files remain the human authority.  This compiler is deliberately
strict about their hashes, order, stable-literal count, reference bindings and
the independently counted list groups.  Provider output is never read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from pathlib import Path

from lexbench_webfetch.body_coverage import compact_ngrams
from lexbench_webfetch.composite_tasks import validate_composite_acceptance
from .inputs import require_mutable_inputs, data_home, definition_path
from lexbench_webfetch.paths import contained_path
from lexbench_webfetch.stable_tasks import validate_acceptance_contract

RISK = definition_path("evaluation/risk-control")
COHORT = RISK / "qualification/cohort/cohort-manifest.json"
MANIFEST = RISK / "qualification/cohort/input-manifest.json"
RULE_FILES = (RISK / "first-30-rule-review.md", RISK / "remaining-70-rule-review.md")
LIST_CONTRACTS = RISK / "list-contracts.json"
BODY_CONTRACTS = RISK / "body-contracts.json"
EXPECTED_RULE_HASHES = {
    "first-30-rule-review.md": "6945a6aa7b0d8d5990d8abf5d24fc41b5a42a22c70940a7c996d8698c6e9bf81",
    "remaining-70-rule-review.md": (
        "301574dddcfddf6d30ed4d882cdd40b1ae859fdc95a0a02cf9868f55e3fd1079"
    ),
}
EXPECTED_COHORT_HASH = "aeec7ec26d24650896263574135a27fc4848f4d362bd38f0a5b5c167f82abc26"
EXPECTED_STABLE_LITERAL_COUNT = 1586
CHINESE_NUMBERS = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def assign_content_acceptance_roles(cases: list[dict]) -> None:
    """Default stable text to required; preserve explicit audited declarations."""
    for case in cases:
        contract = case["acceptance_contract"]
        role = contract.get("stable_text_role", "required")
        if role == "diagnostic" and not (
            case.get("body_requirements")
            or (
                case.get("composite_acceptance")
                and case["composite_acceptance"].get("groups")
            )
            or contract.get("page_patterns")
            or (type(contract.get("minimum_records")) is int
                and contract["minimum_records"] > 0)
        ):
            raise ValueError(
                "Diagnostic stable text requires body, composite, or page-pattern acceptance"
            )
        validate_acceptance_contract(contract)


def apply_content_acceptance_policy(acceptance: dict, *owners: dict | None) -> None:
    """Apply an explicit scope-completeness decision from an existing contract owner."""
    roles = {
        owner["stable_text_role"]
        for owner in owners
        if owner is not None and "stable_text_role" in owner
    }
    if len(roles) > 1:
        raise ValueError("Conflicting stable-text roles across content contracts")
    if roles:
        acceptance["stable_text_role"] = roles.pop()

    page_patterns = []
    for owner in owners:
        if owner is None:
            continue
        declared = owner.get("page_patterns", [])
        if not isinstance(declared, list) or any(
            not isinstance(pattern, str) or not pattern for pattern in declared
        ):
            raise ValueError("Invalid content-contract page patterns")
        page_patterns.extend(declared)
    if len(page_patterns) != len(set(page_patterns)):
        raise ValueError("Duplicate content-contract page patterns")
    if page_patterns:
        acceptance["page_patterns"] = page_patterns


def compact_key(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", value).casefold()
        if character.isalnum()
    )


def visible_text_key(value: str) -> str:
    """Normalize display formatting without joining punctuation-separated elements."""
    return re.sub(
        r"\s+", " ", unicodedata.normalize("NFKC", value).casefold().replace("\u00a0", " ")
    ).strip()


def parse_sections(rule_files=RULE_FILES) -> list[dict]:
    sections = []
    for path in rule_files:
        text = path.read_text()
        for raw in re.split(r"(?m)^## (?=\d+\. )", text)[1:]:
            lines = raw.strip().splitlines()
            match = re.fullmatch(r"(\d+)\. ([^｜]+)｜(.+)", lines[0])
            if not match:
                raise ValueError(f"Unparseable rule heading: {lines[0]}")
            fields = {}
            for line in lines:
                item = re.fullmatch(r"- \*\*(.+?)：\*\*\s*(.*)", line)
                if item:
                    fields[item[1]] = item[2]
            link = next(
                (
                    re.fullmatch(r"\[打开目标网页\]\((.+)\)", line)
                    for line in lines
                    if line.startswith("[打开目标网页]")
                ),
                None,
            )
            if not link:
                raise ValueError(f"Missing target link: {lines[0]}")
            stable_key = next((key for key in fields if key.startswith("稳定文字")), None)
            if not stable_key:
                raise ValueError(f"Missing stable text: {lines[0]}")
            stable_rule = fields[stable_key]
            stable = [
                [primary, *([english] if english else [])]
                for primary, english in re.findall(
                    r"`([^`]+)`(?:（英文界面为 `([^`]+)`）)?", stable_rule
                )
            ]
            stable = [
                alternatives
                for alternatives in stable
                for literal in alternatives[:1]
                if not re.search(rf"不把\s*`{re.escape(literal)}`\s*冻结", stable_rule)
                and not re.search(rf"`{re.escape(literal)}`[^。；]*不再冻结", stable_rule)
            ]
            sections.append(
                {
                    "index": int(match[1]),
                    "id": match[2],
                    "name": match[3],
                    "url": link[1],
                    "fields": fields,
                    "stable": stable,
                }
            )
    return sections


def evidence_roots(manifest_path=MANIFEST, *, evidence_home: Path | None = None) -> dict[str, Path]:
    manifest = json.loads(manifest_path.read_text())
    return {
        key: contained_path(evidence_home or data_home(), value, reject_symlinks=True)
        for key, value in manifest["evidence_roots"].items()
    }


def load_cohort(cohort_path=COHORT) -> tuple[dict, list[dict]]:
    cohort = json.loads(cohort_path.read_text())
    cases = cohort.get("cases")
    if (
        cohort.get("schema_version") != 1
        or cohort.get("kind") != "risk_control_cohort_manifest"
        or not isinstance(cases, list)
    ):
        raise ValueError("Unsupported cohort manifest")
    required = {
        "id",
        "url",
        "site_key",
        "target_scope",
        "target_language",
        "qualification_class",
        "reference_proof",
    }
    for case in cases:
        if not required <= set(case) <= required | {"observed_final_url"}:
            raise ValueError(f"Unexpected cohort fields: {case.get('id', '<unknown>')}")
        if set(case["reference_proof"]) != {"root", "path", "sha256"}:
            raise ValueError(f"Invalid reference proof: {case['id']}")
    expected_scope = {
        "case_count": len(cases),
        "site_count": len({case["site_key"] for case in cases}),
        "security_or_access_control_cases": sum(
            case["qualification_class"] == "security_or_access_control" for case in cases
        ),
        "login_only_cases": sum(case["qualification_class"] == "login_only" for case in cases),
        "qualification_reviewed_on": "2026-09-27",
        "claim_scope": "saved_url_route_and_observation_only",
    }
    if cohort.get("scope") != expected_scope or len(cases) != len({case["id"] for case in cases}):
        raise ValueError("Cohort scope or case identity mismatch")
    return cohort, cases


def proof(case: dict, roots: dict[str, Path]) -> tuple[str, dict]:
    binding = case["reference_proof"]
    path = contained_path(roots[binding["root"]], binding["path"], reject_symlinks=True)
    raw = path.read_bytes() if path.is_file() else None
    if raw is None or hashlib.sha256(raw).hexdigest() != binding["sha256"]:
        raise ValueError(f"Missing or changed reference proof: {case['id']}")
    return raw.decode("utf-8", errors="replace"), {
        "root": binding["root"],
        "path": binding["path"],
        "sha256": binding["sha256"],
    }


def region_count(body: str) -> int:
    if "单区" in body:
        return 1
    match = re.search(r"(?:分|划为|正文 )?([一两二三四五])(?:个连续(?:主题)?)?区", body)
    if match:
        return CHINESE_NUMBERS[match[1]]
    match = re.search(r"([1-5])\s*区", body)
    if match:
        return int(match[1])
    raise ValueError(f"Body region count is not explicit: {body}")


def body_reference(section: dict, source: str, contract: dict | None) -> dict | None:
    body = section["fields"].get("正文参考")
    if not body or body.startswith("不另设"):
        if contract is not None:
            raise ValueError(f"Unexpected body contract: {section['id']}")
        return None
    if contract is None:
        raise ValueError(f"Missing exact body contract: {section['id']}")
    count = region_count(body)
    region_specs = contract.get("regions")
    if not isinstance(region_specs, list) or len(region_specs) != count:
        raise ValueError(f"Body contract region count mismatch: {section['id']}")
    source_lines = source.splitlines()
    drop_exact = set(contract.get("drop_exact_lines", []))
    used_lines: set[int] = set()
    regions = []
    for region_spec in region_specs:
        if set(region_spec) == {"lines"}:
            segments = [region_spec]
        elif set(region_spec) == {"segments"} and isinstance(region_spec["segments"], list):
            segments = region_spec["segments"]
        else:
            raise ValueError(f"Invalid body region shape: {section['id']}")
        selected_lines = []
        for segment in segments:
            if (
                set(segment) != {"lines"}
                or not isinstance(segment["lines"], list)
                or len(segment["lines"]) != 2
            ):
                raise ValueError(f"Invalid body line segment: {section['id']}")
            start, end = segment["lines"]
            if (
                not isinstance(start, int)
                or not isinstance(end, int)
                or start < 1
                or end < start
                or end > len(source_lines)
            ):
                raise ValueError(f"Body line segment out of range: {section['id']} {start}-{end}")
            line_numbers = set(range(start, end + 1))
            if used_lines & line_numbers:
                raise ValueError(f"Body regions overlap: {section['id']} {start}-{end}")
            used_lines.update(line_numbers)
            selected_lines.extend(source_lines[start - 1 : end])
        selected_lines = [line for line in selected_lines if line.strip() not in drop_exact]
        regions.append("\n".join(selected_lines).strip())

    if len(regions) != count or any(not compact_ngrams(region, 6) for region in regions):
        raise ValueError(
            f"Invalid body regions: {section['id']} count={count} "
            f"actual={len(regions)} lengths={[len(value) for value in regions]}"
        )
    return {
        "version": 3,
        "unit": "region_compact_hexagram_recall",
        "minimum_coverage": 0.8,
        "minimum_floor": 0.6,
        "minimum_region_passes": max(1, count - 1),
        "regions": [
            {"id": f"body-region-{index + 1}", "text": text} for index, text in enumerate(regions)
        ],
    }


def build(output: Path, definition_dir: Path = RISK, *, evidence_home: Path | None = None) -> dict:
    require_mutable_inputs(output)
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    rule_files = tuple(
        contained_path(definition_dir, path.name, reject_symlinks=True) for path in RULE_FILES
    )
    cohort_path = contained_path(
        definition_dir, "qualification/cohort/cohort-manifest.json", reject_symlinks=True
    )
    manifest_path = contained_path(
        definition_dir, "qualification/cohort/input-manifest.json", reject_symlinks=True
    )
    list_contracts_path = contained_path(
        definition_dir, "list-contracts.json", reject_symlinks=True
    )
    body_contracts_path = contained_path(
        definition_dir, "body-contracts.json", reject_symlinks=True
    )
    for path in rule_files:
        if sha(path) != EXPECTED_RULE_HASHES[path.name]:
            raise ValueError(f"Audited rule hash changed: {path}")
    if sha(cohort_path) != EXPECTED_COHORT_HASH:
        raise ValueError("Cohort manifest hash changed")
    sections = parse_sections(rule_files)
    cohort, qualified = load_cohort(cohort_path)
    if [section["index"] for section in sections] != list(range(1, 101)) or [
        section["id"] for section in sections
    ] != [case["id"] for case in qualified]:
        raise ValueError("Rule/cohort order mismatch")
    stable_literal_count = sum(len(section["stable"]) for section in sections)
    if stable_literal_count != EXPECTED_STABLE_LITERAL_COUNT:
        raise ValueError(
            f"Expected exactly {EXPECTED_STABLE_LITERAL_COUNT} required stable literals"
        )

    roots = evidence_roots(manifest_path, evidence_home=evidence_home)
    list_source = json.loads(list_contracts_path.read_text())
    contracts = list_source["contracts"]
    body_source = json.loads(body_contracts_path.read_text())
    body_contracts = body_source["contracts"]
    expected_body_ids = {
        section["id"]
        for section in sections
        if section["fields"].get("正文参考", "").strip()
        and not section["fields"]["正文参考"].startswith("不另设")
    }
    if set(body_contracts) != expected_body_ids or len(body_contracts) != 79:
        missing = sorted(expected_body_ids - set(body_contracts))
        unexpected = sorted(set(body_contracts) - expected_body_ids)
        raise ValueError(f"Body contract scope mismatch: missing={missing} unexpected={unexpected}")
    groups = sum(len(value["groups"]) for value in contracts.values())
    quantity_kinds = {"named_link", "name_description", "table", "linked_table"}
    quantity_contracts = {
        identifier: value
        for identifier, value in contracts.items()
        if any(group["kind"] in quantity_kinds for group in value["groups"])
    }
    quantity_groups = sum(
        group["kind"] in quantity_kinds
        for value in quantity_contracts.values()
        for group in value["groups"]
    )
    # The replacement removes Kuaishou's two independently counted list groups
    # and adds a fixed full-body About page. Ibiblio's owner carries only its
    # explicit page-scope policy: the task does not require its changing news list.
    if (
        len(quantity_contracts) != 20
        or quantity_groups != 26
        or len(contracts) != 23
        or groups != 29
    ):
        raise ValueError("Expected 20 quantity cases/26 groups plus 3 field groups")
    cases = []
    body_count = 0
    for section, qualified_case in zip(sections, qualified, strict=True):
        source, binding = proof(qualified_case, roots)
        normalized_stable = [
            visible_text_key(alternatives[0]) for alternatives in section["stable"]
        ]
        if len(normalized_stable) != len(set(normalized_stable)):
            raise ValueError(f"Duplicate normalized stable literal: {section['id']}")
        normalized_source = visible_text_key(source)
        # The first literal comes from the frozen historical proof. Later
        # locale equivalents come from a separate browser observation and are
        # bound case-by-case in the language-rule revision receipt and tests.
        for alternatives in section["stable"]:
            literal = alternatives[0]
            if visible_text_key(literal) not in normalized_source:
                raise ValueError(
                    f"Stable literal missing from evidence: {section['id']}: {literal}"
                )
        anchors = [
            {"id": f"stable-{index + 1:02d}", "alternatives": alternatives}
            for index, alternatives in enumerate(section["stable"])
        ]
        acceptance = {
            "version": 1,
            "schema": "body_text",
            "minimum_records": 0,
            "stable_text_anchors": anchors,
        }
        apply_content_acceptance_policy(
            acceptance,
            body_contracts.get(section["id"]),
            contracts.get(section["id"]),
        )
        validate_acceptance_contract(acceptance)
        body = body_reference(section, source, body_contracts.get(section["id"]))
        body_count += bool(body)
        composite = None
        if section["id"] in contracts and contracts[section["id"]]["groups"]:
            composite = {"version": 1, "groups": contracts[section["id"]]["groups"]}
            validate_composite_acceptance(composite)
        final_url = qualified_case.get("observed_final_url") or section["url"]
        case = {
            "id": section["id"],
            "name": section["name"],
            "url": section["url"],
            "category": "risk_control",
            "track": "risk_control",
            "task_kind": "body",
            "description": qualified_case["target_scope"],
            "task_prompt": (
                "提取声明网址首次自动加载出的目标正文、列表和真实链接；"
                "不点击、不输入、不滚动、不翻页、不登录，也不使用图片文字。"
            ),
            "expected_any": [],
            "forbidden_any": [],
            "min_content_chars": 1,
            "ground_truth_main_content": "",
            "required_facts": [],
            "acceptance_contract": acceptance,
            "source_benchmark": {
                "risk_control": {
                    "navigation_contract": {
                        "requested_url": qualified_case["url"],
                        "observed_final_url": final_url,
                        "target_language": qualified_case["target_language"],
                    },
                    "rule_index": section["index"],
                    "reference_proof": binding,
                }
            },
        }
        # Keep independently observed anchors intact. Shared scoring makes
        # them diagnostic when body or composite requirements own acceptance.
        if body:
            case["body_requirements"] = body
        if composite:
            case["composite_acceptance"] = composite
        cases.append(case)
    assign_content_acceptance_roles(cases)
    dataset = {
        "schema_version": 1,
        "id": "risk-control-audited-100",
        "name": "曾观察到访问限制的100个网址",
        "tool": "web_fetch",
        "case_count": 100,
        "site_count": 74,
        "body_case_count": body_count,
        "quantity_case_count": len(quantity_contracts),
        "independent_quantity_group_count": quantity_groups,
        "list_or_field_case_count": sum(bool(owner["groups"]) for owner in contracts.values()),
        "composite_group_count": groups,
        "stable_literal_count": stable_literal_count,
        "observed_literal_count": stable_literal_count,
        "runs_per_case": 1,
        "timeout_ms": 45000,
        "workers": 1,
        "retry_policy": {"declared_retries": 0, "authoritative_response": "final_physical_call"},
        "interaction_policy": "initial_load_only_no_click_input_scroll_pagination_login_or_ocr",
        "qualification_scope": cohort["scope"],
        "source_bindings": {
            "cohort_manifest_sha256": sha(cohort_path),
            "first_30_rules_sha256": sha(rule_files[0]),
            "remaining_70_rules_sha256": sha(rule_files[1]),
            "list_contracts_sha256": sha(list_contracts_path),
            "body_contracts_sha256": sha(body_contracts_path),
        },
        "cases": cases,
    }
    output.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n")
    return dataset


def main(argv=None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--definition-dir", type=Path, default=RISK)
    parser.add_argument("--evidence-home", type=Path)
    args = parser.parse_args(argv)
    dataset = build(args.output, args.definition_dir, evidence_home=args.evidence_home)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": sha(args.output),
                "cases": len(dataset["cases"]),
                "body_cases": dataset["body_case_count"],
                "quantity_groups": dataset["independent_quantity_group_count"],
                "composite_groups": dataset["composite_group_count"],
                "stable_literals": dataset["stable_literal_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
