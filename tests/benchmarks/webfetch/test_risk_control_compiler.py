"""Compile an independent synthetic cohort without historical pages or provider calls."""

import hashlib
import json

import pytest

from lexbench_webfetch import risk_control_dataset as compiler


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


@pytest.fixture
def definitions(tmp_path, monkeypatch):
    root = tmp_path / "definitions"
    root.mkdir()
    data = tmp_path / "data"
    proofs = data / "source-inputs"
    proofs.mkdir(parents=True)
    monkeypatch.setenv("BROWSER_EVAL_DATA_HOME", str(data))
    sections, cases, bodies, lists = [], [], {}, {}
    for index in range(100):
        identifier = f"fixture-{index:03d}"
        anchors = [f"Independent anchor {index:03d}-{item:02d}" for item in range(16)]
        raw = (
            "; ".join(anchors) + "\n" + f"Independent source body paragraph {index:03d}.\n"
        ).encode()
        (proofs / f"{identifier}.txt").write_bytes(raw)
        url = f"https://example.test/{identifier}"
        section = (
            f"## {index + 1}. {identifier}｜Synthetic task {index}\n"
            f"[打开目标网页]({url})\n"
            "- **稳定文字：** " + "、".join(f"`{anchor}`" for anchor in anchors) + "\n"
        )
        if index < 79:
            section += "- **正文参考：** 单区\n"
            bodies[identifier] = {"regions": [{"lines": [2, 2]}]}
            if index == 0:
                bodies[identifier]["stable_text_role"] = "diagnostic"
            elif index == 1:
                bodies[identifier].update(
                    {
                        "stable_text_role": "diagnostic",
                        "page_patterns": [r"^Independent anchor 001-00$"],
                    }
                )
        sections.append(section)
        cases.append(
            {
                "id": identifier,
                "url": url,
                "site_key": f"site-{index % 74}",
                "target_scope": "The independent source page",
                "target_language": "en",
                "qualification_class": "security_or_access_control",
                "reference_proof": {
                    "root": "fixture",
                    "path": f"{identifier}.txt",
                    "sha256": digest(raw),
                },
            }
        )
        if index < 20:
            lists[identifier] = {
                "groups": [
                    {
                        "id": f"entries-{group}",
                        "kind": "named_link",
                        "minimum_records": 1,
                        "hosts": ["example.test"],
                        "entry_path": "/item/[0-9]+",
                    }
                    for group in range(7 if index == 0 else 1)
                ]
            }
        elif index == 20:
            lists[identifier] = {
                "stable_text_role": "diagnostic",
                "page_patterns": [r"^Independent source body paragraph 020\.$"],
                "groups": [],
            }
        elif index < 23:
            lists[identifier] = {
                "groups": [
                    {"id": "literal", "kind": "literals", "required_literals": ["Independent"]}
                ]
                + (
                    [
                        {
                            "id": "second-field-scope",
                            "kind": "literals",
                            "required_literals": ["source"],
                        }
                    ]
                    if index == 21
                    else []
                )
            }
    rules = [root / name for name in ("first-30-rule-review.md", "remaining-70-rule-review.md")]
    rules[0].write_text("\n".join(sections[:30]))
    rules[1].write_text("\n".join(sections[30:]))
    scope = {
        "case_count": 100,
        "site_count": 74,
        "security_or_access_control_cases": 100,
        "login_only_cases": 0,
        "qualification_reviewed_on": "2026-09-27",
        "claim_scope": "saved_url_route_and_observation_only",
    }
    cohort = root / "qualification/cohort/cohort-manifest.json"
    write_json(
        cohort,
        {
            "schema_version": 1,
            "kind": "risk_control_cohort_manifest",
            "cases": cases,
            "scope": scope,
        },
    )
    write_json(
        root / "qualification/cohort/input-manifest.json",
        {"evidence_roots": {"fixture": "source-inputs"}},
    )
    write_json(root / "body-contracts.json", {"contracts": bodies})
    write_json(root / "list-contracts.json", {"contracts": lists})
    monkeypatch.setattr(
        compiler, "EXPECTED_RULE_HASHES", {path.name: digest(path.read_bytes()) for path in rules}
    )
    monkeypatch.setattr(compiler, "EXPECTED_COHORT_HASH", digest(cohort.read_bytes()))
    monkeypatch.setattr(compiler, "EXPECTED_STABLE_LITERAL_COUNT", 1600)
    return root, proofs


def test_complete_independent_definitions_compile_to_an_executable_dataset(definitions, tmp_path):
    root, _ = definitions
    output = tmp_path / "dataset.json"
    dataset = compiler.build(output, root)
    assert json.loads(output.read_bytes()) == dataset
    assert dataset["case_count"] == len(dataset["cases"]) == 100
    assert dataset["body_case_count"] == 79
    assert dataset["stable_literal_count"] == 1600
    assert dataset["independent_quantity_group_count"] == 26
    assert dataset["list_or_field_case_count"] == 22
    assert dataset["composite_group_count"] == 29
    assert dataset["source_bindings"]["body_contracts_sha256"] == digest(
        (root / "body-contracts.json").read_bytes()
    )
    assert dataset["cases"][0]["body_requirements"]["regions"][0]["text"] == (
        "Independent source body paragraph 000."
    )
    roles = [
        case["acceptance_contract"].get("stable_text_role", "required")
        for case in dataset["cases"]
    ]
    assert roles.count("diagnostic") == 3
    assert roles.count("required") == 97
    assert dataset["cases"][1]["acceptance_contract"]["page_patterns"] == [
        r"^Independent anchor 001-00$"
    ]
    assert dataset["cases"][20]["acceptance_contract"]["stable_text_role"] == "diagnostic"
    assert "composite_acceptance" not in dataset["cases"][20]


def test_current_scope_owners_declare_only_proven_complete_contracts():
    bodies = json.loads(compiler.BODY_CONTRACTS.read_text())["contracts"]
    lists = json.loads(compiler.LIST_CONTRACTS.read_text())["contracts"]
    assert bodies["risk-diverse-042"]["stable_text_role"] == "diagnostic"
    assert bodies["risk-diverse-011"]["stable_text_role"] == "required"
    assert lists["risk-diverse-192"]["stable_text_role"] == "diagnostic"
    assert len(lists["risk-diverse-192"]["page_patterns"]) == 1


def test_current_rules_keep_responsive_page_chrome_out_of_body_acceptance():
    removed = {
        "risk-diverse-038": {"Recent changes"},
        "risk-diverse-117": {"Search"},
        "risk-diverse-225": {"Resources"},
        "risk-diverse-241": {"Cart", "Home"},
        "risk-diverse-268": {"Main Site", "Recent Changes", "Site Index", "Categories"},
        "risk-diverse-201": {"Main page", "Table of contents", "Getting involved"},
        "risk-diverse-209": {"Search"},
        "risk-diverse-237": {"Discord"},
    }
    sections = {row["id"]: row for row in compiler.parse_sections()}
    for case_id, labels in removed.items():
        stable = {value for alternatives in sections[case_id]["stable"] for value in alternatives}
        assert labels.isdisjoint(stable), case_id
    assert sum(len(row["stable"]) for row in sections.values()) == (
        compiler.EXPECTED_STABLE_LITERAL_COUNT
    )


def test_changed_independent_proof_cannot_produce_a_dataset(definitions, tmp_path):
    root, proofs = definitions
    (proofs / "fixture-099.txt").write_text("changed source")
    output = tmp_path / "dataset.json"
    with pytest.raises(ValueError, match="Missing or changed reference proof"):
        compiler.build(output, root)
    assert not output.exists()


def test_linked_independent_proof_is_rejected_even_when_its_bytes_match(definitions, tmp_path):
    root, proofs = definitions
    original = proofs / "fixture-099.txt"
    target = tmp_path / "outside-proof.txt"
    original.rename(target)
    original.symlink_to(target)
    output = tmp_path / "dataset.json"
    with pytest.raises(ValueError, match="[Ss]ymlink"):
        compiler.build(output, root)
    assert not output.exists()


def test_reference_roots_cannot_escape_the_declared_data_home(definitions):
    root, _ = definitions
    manifest = root / "qualification/cohort/input-manifest.json"
    write_json(manifest, {"evidence_roots": {"fixture": "../outside"}})
    with pytest.raises(ValueError, match="relative"):
        compiler.evidence_roots(manifest)


@pytest.mark.parametrize("linked", [False, True])
def test_compiler_refuses_to_overwrite_existing_or_linked_output(tmp_path, linked):
    original = tmp_path / "keep.json"
    original.write_bytes(b"original user data")
    output = tmp_path / "output.json" if linked else original
    if linked:
        output.symlink_to(original)
    with pytest.raises(FileExistsError):
        compiler.build(output, tmp_path / "missing-definitions")
    assert original.read_bytes() == b"original user data"
