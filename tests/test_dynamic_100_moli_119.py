import hashlib
import json
from pathlib import Path

import pytest

from harness.web_fetch import local_audit
from harness.web_fetch.reference_inputs import input_tree
from harness.web_fetch.report import local_audit_banner, provider_configuration
from harness.web_fetch.scoring import score_attempt
from harness.web_fetch.metrics import task_passed
from harness.web_fetch.scoring_regressions import load_scoring_regressions
from harness.web_fetch.stable_tasks import validate_acceptance_contract
from tests.conftest import read_frozen_dataset_bytes

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "evaluation/dynamic-content-100-moli-webfetch/dataset.json"
RECEIPT_PATH = ROOT / "research/dynamic-100-moli-119-local-audit.json"
COMPATIBILITY_PATH = ROOT / "research/dynamic-100-moli-119-risk-scorer-compatibility.json"
REVISION_PATH = ROOT / "research/moli-111-three-cohort-evaluation-revision.json"
PRE_CALL_PATH = ROOT / "research/dynamic-native-retrieval-contract-pre-call-revision.json"


def _authority_binding(path):
    path = Path(path)
    if path.parts[:2] == ("evaluation", "dynamic-content-100-moli-webfetch"):
        owner = ROOT / "evaluation/dynamic-content-100-moli-webfetch"
        tree = path.parts[2]
        relative = Path(*path.parts[3:])
    else:
        owner = ROOT / "evaluation"
        tree = path.parts[1]
        relative = Path(*path.parts[2:])
    manifest = json.loads((owner / "source-inputs.json").read_text())
    return owner, tree, relative, manifest["trees"][tree][relative.as_posix()]


def _dataset():
    return json.loads(read_frozen_dataset_bytes(DATASET_PATH).decode("utf-8"))


def test_dynamic_100_dataset_matches_the_frozen_local_audit(tmp_path):
    dataset = _dataset()
    cases = dataset["cases"]
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    compatibility = json.loads(COMPATIBILITY_PATH.read_text(encoding="utf-8"))

    assert len(cases) == receipt["scope"]["cases"] == 100
    assert len({case["id"] for case in cases}) == 100
    assert len({case["url"] for case in cases}) == 100
    from harness.web_fetch.models import EvalCase
    from harness.web_fetch.providers.headless import (
        HeadlessProvider,
        cli_command,
        output_mode_for_case,
    )
    from harness.web_fetch.providers.headless_worker import fetch as worker_fetch
    from harness.web_fetch.report_interfaces import browser_task_interface_matrix
    from harness.web_fetch.retrieval_hints import native_contract_for_case

    typed_cases = [EvalCase(**case) for case in cases]
    assert all(output_mode_for_case("lightpanda", "headless_markdown", case) == "markdown"
               for case in typed_cases)
    assert sum(bool((contract := native_contract_for_case(case))
                    and contract.include_subdocuments) for case in typed_cases) == 3
    matrix = browser_task_interface_matrix(
        {case["id"]: case for case in cases}, ["lightpanda"])
    dynamic = next(row for row in matrix["rows"] if row["track"] == "dynamic")
    assert dynamic["cells"]["lightpanda"][0]["mode"] == "markdown"
    assert dynamic["cells"]["lightpanda"][0]["count"] == 100
    assert "不传逐题选择器、数量、延迟或 iframe 参数" in matrix["capabilities"]["lightpanda"]
    revision = json.loads(REVISION_PATH.read_text(encoding="utf-8"))
    assert revision["provider_calls_for_revision"] == 0
    assert revision["dynamic_dataset"]["historical_sha256"] == receipt["bindings"]["dataset_sha256"]
    historical = read_frozen_dataset_bytes(DATASET_PATH).decode("utf-8")
    pre_call = json.loads(PRE_CALL_PATH.read_text())
    assert pre_call["provider_calls"] == 0
    for binding in ("dataset_manifest", "dataset_catalog", "contract_registry"):
        item = pre_call[binding]
        assert hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest() == item["sha256"]
    authoritative = pre_call["authoritative_430_input"]
    assert (
        authoritative["superseded_pre_call_dataset_sha256"]
        != authoritative["revised_pre_call_dataset_sha256"]
    )
    assert authoritative["case_count"] == 430
    assert authoritative["provider_calls"] == 0
    correction = pre_call["assembly_correction"]
    assert correction["revised_native_dataset_sha256"] == authoritative[
        "revised_pre_call_dataset_sha256"
    ]
    assert correction["provider_route_changed"] is False
    assert correction["acceptance_changed"] is False
    assert correction["provider_calls"] == 0
    delayed_correction = pre_call["delayed_completion_correction"]
    assert delayed_correction["case_id"] == "dynamic-delayed"
    assert delayed_correction["completion"] == {
        "selector": ".quote", "minimum_count": 10,
        "minimum_text_fragments": 3}
    assert delayed_correction["source_authority"] == {
        "path": "evaluation/sources/dynamic-delayed/render-audit.json",
        "sha256": "cb72f36b479827c25816f1df5e2448885c0935ab61c0ad130bc145a2d466cc93",
        "provider_output_used": False,
    }
    owner, tree, relative, bound = _authority_binding(
        delayed_correction["source_authority"]["path"])
    assert bound == delayed_correction["source_authority"]["sha256"]
    delayed_audit = json.loads((input_tree(owner / "dataset.json", tree) / relative).read_text())
    assert delayed_audit["completion"]["satisfied"] is True
    assert delayed_audit["completion"]["condition"] == (
        "dynamic-delayed: 10 nonempty target records with populated text fields; no interaction")
    rendered_coverage = delayed_audit["coverage"]["rendered"]
    assert rendered_coverage["hits"] == rendered_coverage["total"] == 30
    assert rendered_coverage["all_present"] is True
    assert delayed_correction["required_new_calls"] == {
        "moli_fixed": 1, "moli_pr919": 1, "lightpanda": 1,
        "obscura": 1, "chromium": 1, "kitesurf": 1,
        "physical_calls": 6}
    assert delayed_correction["deduplication"] == (
        "These six calls replace the already-declared dynamic-delayed calls in the "
        "method scopes below; they do not increase the 76-call physical total.")
    assert delayed_correction["provider_calls"] == 0
    assert pre_call["execution_effect"]["required_new_calls"] == {
        "moli_fixed": 39, "moli_pr919": 21, "lightpanda": 4,
        "obscura": 4, "chromium": 4, "kitesurf": 4,
        "physical_calls": 76}
    assert pre_call["execution_effect"]["interface_policy"] == {
        "lightpanda_dynamic": "official_native_markdown",
        "lightpanda_dynamic_case_count": 100,
        "lightpanda_iframe_behavior": (
            "three contracts preserve their named completion plan and add official frame flags inside "
            "the native Markdown JSON transport envelope; native Markdown omission remains a scored failure"),
        "non_chromium_body": "browser-native Markdown",
        "chromium_body": "rendered HTML converted by node-html-markdown 2.0.0",
    }
    from harness.web_fetch.task_outputs import require_native_task_routes

    by_id = {case.id: case for case in typed_cases}
    raw_by_id = {case["id"]: case for case in cases}
    fixture_binary = tmp_path / "lightpanda"
    fixture_binary.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'lightpanda fixture'; exit 0; fi\n"
        "printf '%s\\n' '{\"content\":\"# Browser-native Markdown\","
        "\"url\":\"https://example.com/final\",\"http_status\":200}'\n"
    )
    fixture_binary.chmod(0o755)
    provider = HeadlessProvider("lightpanda", {
        "type": "headless_markdown",
        "engine": "lightpanda",
        "binary": str(fixture_binary),
        "native_metadata": {"format": "lightpanda_json"},
    })
    for changed in pre_call["changed_cases"]:
        case = by_id[changed["case_id"]]
        contract = native_contract_for_case(case)
        plan = contract.completion_plan(45_000).worker_dict()
        command = cli_command(
            "lightpanda", "/lightpanda", case.url, 45, "markdown",
            retrieval_plan=plan,
            include_subdocuments=contract.include_subdocuments,
            native_metadata={"format": "lightpanda_json"},
        )
        assert command[command.index("--dump") + 1] == "markdown"
        assert command.count("--json") == 1
        assert command[command.index("--wait-until") + 1] == "load"
        assert "--wait-selector" in command or "--wait-script" in command
        if case.id == "dynamic-delayed":
            assert command[command.index("--wait-script") + 1] == plan["wait_value"]
            assert plan["minimum_text_fragments"] == 3
        if contract.include_subdocuments:
            assert command.count("--with-frames") == 1
            assert command[command.index("--load-resources") + 1] == "iframe"
        else:
            assert "--with-frames" not in command
            assert "--load-resources" not in command

        metadata = {
            "extractor": "lightpanda_native_markdown",
            "output_contract": "markdown",
        }
        if contract.include_subdocuments:
            metadata["subdocuments"] = {
                "requested": True,
                "support": "native_markdown_omits_child_document",
                "interface": "--load-resources iframe + --with-frames",
                "harness_content_assembly": False,
            }
        row = {"provider": "lightpanda", "case_id": case.id,
               "provider_metadata": metadata}
        require_native_task_routes([raw_by_id[case.id]], [row])
        if contract.include_subdocuments:
            invalid = {**metadata}
            invalid.pop("subdocuments")
        else:
            optional = {**metadata, "subdocuments": {
                "requested": True,
                "support": "native_markdown_omits_child_document",
                "interface": "--load-resources iframe + --with-frames",
                "harness_content_assembly": False,
                "scope": "dynamic_task_family",
            }}
            # Family-wide native frames are permitted, not required. They must
            # still disclose native support and cannot assemble child content.
            require_native_task_routes([raw_by_id[case.id]], [{**row, "provider_metadata": optional}])
            invalid = {**optional, "subdocuments": {
                **optional["subdocuments"], "harness_content_assembly": True}}
        with pytest.raises(ValueError, match="Native task route contract violated"):
            require_native_task_routes([raw_by_id[case.id]], [{**row,
                "provider_metadata": invalid}])

        declared = metadata if contract.include_subdocuments else optional
        for field, value in (("requested", False), ("support", "invented_support"),
                             ("interface", "external_frame_parser"), ("scope", "case_specific")):
            invalid = {**declared, "subdocuments": {**declared["subdocuments"], field: value}}
            with pytest.raises(ValueError, match="Native task route contract violated"):
                require_native_task_routes([raw_by_id[case.id]], [{**row, "provider_metadata": invalid}])

        # Exercise the disposable worker process itself.  A mocked worker used
        # to hide that the serialized request had lost the case identity.
        result = provider.fetch(case, 45)
        assert result.ok, result.error_message
        assert result.content == "# Browser-native Markdown"
        assert result.metadata["completion"]["selector"] == contract.selector
        if contract.include_subdocuments:
            assert result.metadata["subdocuments"]["interface"] == (
                "--load-resources iframe + --with-frames")

    frame_case = by_id["dynamic-iframe"]
    frame_contract = native_contract_for_case(frame_case)
    frame_request = {
        "engine": "lightpanda",
        "binary": str(fixture_binary),
        "url": frame_case.url,
        "timeout": 45,
        "output_mode": "markdown",
        "task_kind": "body",
        "track": "dynamic",
        "retrieval_plan": frame_contract.completion_plan(45_000).worker_dict(),
        "include_subdocuments": True,
        "retrieval_hints": frame_case.retrieval_hints,
        "native_metadata": {"format": "lightpanda_json"},
    }
    for bad_case_id in (None, "dynamic-delayed"):
        invalid_request = {**frame_request}
        if bad_case_id is not None:
            invalid_request["case_id"] = bad_case_id
        with pytest.raises(ValueError, match="case_id|belongs to another case"):
            worker_fetch(invalid_request)
    assert hashlib.sha256(historical.encode()).hexdigest() == pre_call["revised_dataset_sha256"]
    current_dataset = json.loads(historical)
    for row in pre_call["changed_cases"]:
        case = next(c for c in current_dataset["cases"] if c["id"] == row["case_id"])
        assert case.pop("retrieval_hints") == {
            "version": 3, "mode": "native", "contract": row["contract"]}
    historical = json.dumps(current_dataset, ensure_ascii=False, indent=2) + "\n"
    assert hashlib.sha256(historical.encode()).hexdigest() == pre_call["predecessor_dataset_sha256"]
    expected_hash=revision['dynamic_dataset']['revised_sha256']
    for followup in revision.get('dynamic_dataset_followups',[]):
        assert followup['predecessor_sha256']==expected_hash
        assert followup['provider_calls']==0
        assert followup['source_authority']['provider_output_used'] is False
        owner, tree, relative, bound = _authority_binding(followup['source_authority']['path'])
        assert bound == followup['source_authority']['sha256']
        if followup['case_id'] == 'dynamic-082-allocine-films':
            assert followup['provider_outputs_used'] is False
            effect = followup['acceptance_effect']
            assert effect == {
                'changed': True,
                'required_stable_anchor_count_before': 55,
                'required_stable_anchor_count_after': 54,
                'removed_required_anchor_id': 'phase1-stable-0e2130d4bdb9',
                'effect': ('An otherwise valid result no longer fails solely because TVActu '
                           'is absent; all 54 remaining anchors, the 57-record minimum, host, '
                           'entry-path and link rules remain required.'),
                'record_and_link_rules_changed': False,
            }
            current = next(c for c in cases if c['id'] == followup['case_id'])
            assert len(current['acceptance_contract']['stable_text_anchors']) == 54
            source = json.loads((input_tree(owner / 'dataset.json', tree) / relative).read_text())
            assert source['independent'] is True
            assert source['observation_method'] == followup['source_basis']['observation_method']
            assert source['checked_at'] == followup['source_basis']['checked_at']
            assert ('TVActu' in source['content_text']) is followup['source_basis'][
                'captured_text_contains_removed_anchor']
        expected_hash=followup['revised_sha256']
    assert hashlib.sha256(historical.encode()).hexdigest() == expected_hash
    assert hashlib.sha256(read_frozen_dataset_bytes(DATASET_PATH)).hexdigest() == pre_call["revised_dataset_sha256"]
    for followup in reversed(revision.get('dynamic_dataset_followups',[])):
        case=next(c for c in cases if c['id']==followup['case_id'])
        changes = followup.get('changes') or [{
            'field_path': followup['field_path'],
            'value': followup['value'],
        }]
        historical_dataset = json.loads(historical)
        historical_case = next(c for c in historical_dataset['cases'] if c['id']==followup['case_id'])
        for change in changes:
            current = case
            restored = historical_case
            for key in change['field_path'][:-1]:
                current = current[key]
                restored = restored[key]
            leaf = change['field_path'][-1]
            if change.get('operation') == 'remove_list_item':
                assert change['removed_value'] not in current[leaf]
                restored[leaf].insert(change['index'], change['removed_value'])
                continue
            assert current[leaf] == change['value']
            if 'previous_value' in change:
                restored[leaf] = change['previous_value']
            else:
                restored.pop(leaf)
        historical = json.dumps(historical_dataset, ensure_ascii=False, indent=2) + '\n'
        assert hashlib.sha256(historical.encode()).hexdigest()==followup['predecessor_sha256']
    controls = load_scoring_regressions()
    for change in revision["dynamic_dataset"]["changes"]:
        case = next(case for case in cases if case["id"] == change["case_id"])
        assert case[change["field"]] == change["value"]
        assert len(change["source_html_sha256"]) == 64
        value = json.dumps(change["value"])
        marker = f'      "url": "{case["url"]}",\n      "{change["field"]}": {value},\n'
        assert historical.count(marker) == 1
        historical = historical.replace(marker, f'      "url": "{case["url"]}",\n', 1)
    assert len(revision["dynamic_dataset"]["changes"]) == 9
    assert hashlib.sha256(historical.encode()).hexdigest() == receipt["bindings"]["dataset_sha256"]
    scorer_revision = revision["shared_scorer"]
    known_sources = {scorer_revision['revised_sha256']}
    for followup in revision.get('shared_scorer_followups', []):
        assert followup['predecessor_source_sha256'] == scorer_revision['revised_sha256']
        assert followup['provider_calls'] == 0
        assert set(scorer_revision['regression_ids']) <= set(followup['regression_ids'])
        scorer_revision = {**scorer_revision, **followup}
        known_sources.add(followup['revised_sha256'])
    for parallel in revision.get('shared_scorer_parallel_followups', []):
        assert parallel['predecessor_source_sha256'] in known_sources
        assert parallel['provider_calls'] == 0
        assert any(row.get('parallel_predecessor_source_sha256') == parallel['revised_sha256']
                   for row in revision['shared_scorer_followups'])
        assert set(parallel['regression_ids']) <= set(scorer_revision['regression_ids'])
    assert scorer_revision["main_baseline_sha256"] == compatibility["bindings"]["scoring_source_sha256"]
    successor = revision["current_shared_scorer_authority"]
    successor_path = ROOT / successor["path"]
    assert hashlib.sha256(successor_path.read_bytes()).hexdigest() == successor["sha256"]
    successor_receipt = json.loads(successor_path.read_text())
    assert successor_receipt["provider_calls"] == 0
    assert successor_receipt["scorer_binding"]["revised_scoring_source_sha256"] == (
        "8bc386c7c081b70701d635e4fc2ead8c22a7f9a6004ec42adcaf391f9d470f1b"
    )
    assembly_definition = json.loads(
        (ROOT / "evaluation/browser-native-430/dataset-assembly.json").read_text())
    correction_binding = assembly_definition["case_corrections"]
    correction_path = ROOT / correction_binding["path"]
    assert hashlib.sha256(correction_path.read_bytes()).hexdigest() == correction_binding["sha256"]
    current_corrections = json.loads(correction_path.read_text())
    current_scorer_bindings = {
        row["evidence"]["scoring_source_sha256"]
        for row in current_corrections["cases"]
        if row.get("evidence", {}).get("scoring_source_sha256")
    }
    # These reference-correction receipts record their original scorer, not
    # whatever unrelated scoring fixes are present in the current checkout.
    assert current_scorer_bindings == {
        "db507a6f0a90c8346aaa0fc7613505c8050c20427547cfccd94754548f1c8e9f"}
    current_regression_ids = {
        identifier
        for row in current_corrections["cases"]
        for identifier in row.get("evidence", {}).get("regression_ids", [])
    }
    assert current_regression_ids <= set(controls)
    for identifier in current_regression_ids:
        control = controls[identifier]
        metrics = score_attempt(control['case'], control['artifact'].encode(), ok=control['ok'],
                                metadata=control.get('metadata'), title=control.get('title'),
                                final_url=control.get('final_url'))
        assert task_passed(control['ok'], metrics, control['case'].get('task_kind','body')) is control['expected_task_pass']
        for key, expected in control.get('expected_metrics', {}).items():
            assert metrics.get(key) == expected

    assert set(successor_receipt["correction_provenance"]["regression_ids"]) <= set(controls)
    assert set(scorer_revision["regression_ids"]) <= set(controls)
    modes = {}
    for identifier in scorer_revision["regression_ids"]:
        row = controls[identifier]
        modes.setdefault(row["root_cause"], set()).add(row["failure_mode"])
    assert all(value == {"false-positive", "false-negative"} for value in modes.values())
    assert compatibility["provider_calls"] == 0
    assert compatibility["scope"] == {
        "cases": 100,
        "input_passes": 44,
        "output_passes": 45,
        "task_decision_changes": 1,
    }
    bindings = compatibility["bindings"]
    assert bindings["dataset_sha256"] == receipt["bindings"]["dataset_sha256"]
    assert bindings["input_results_sha256"] == receipt["bindings"]["results_sha256"]
    assert bindings["decision_digest_format"] == (
        "sorted_attempt_id_boolean_pairs_compact_json_v1"
    )
    assert bindings["input_task_decisions_sha256"] != bindings["output_task_decisions_sha256"]
    assert compatibility["task_decision_changes"] == [
        {
            "attempt_id": "moli-webfetch__replacement-pt-kabum-smartphones__r001",
            "before": False,
            "after": True,
            "reason": (
                "The saved Markdown uses &amp; for a browser-visible ampersand in the "
                "required text 'Celular & Smartphone'; decoding one character-reference "
                "layer corrects the false negative."
            ),
        }
    ]
    assert bindings["scoring_source_sha256"] == scorer_revision["main_baseline_sha256"]
    assert bindings["scoring_regressions_sha256"] != scorer_revision["scoring_regressions_sha256"]
    assert "Moli 1.1.9 local audit complete" in dataset["approval_status"]
    assert dataset["comparison"]["local_audit_receipt"] == RECEIPT_PATH.relative_to(ROOT).as_posix()


def require_historical_inputs():
    try:
        input_tree(Path('evaluation/dataset.json'), 'sources')
    except FileNotFoundError:
        pytest.skip('requires externally installed historical reference inputs')


def test_dynamic_followup_reference_values_match_bound_external_source():
    require_historical_inputs()
    revision = json.loads(REVISION_PATH.read_text())
    for followup in revision.get('dynamic_dataset_followups', []):
        owner, tree, relative, _ = _authority_binding(followup['source_authority']['path'])
        dataset = DATASET_PATH if owner.name == "dynamic-content-100-moli-webfetch" else ROOT / "evaluation/dataset.json"
        authority = input_tree(dataset, tree) / relative
        assert hashlib.sha256(authority.read_bytes()).hexdigest() == followup['source_authority']['sha256']
        record = json.loads(authority.read_text())
        assert record.get('provider_output_used', False) is False
        if 'value' in followup:
            assert record['expected_final_url'] == followup['value']
        elif followup['case_id'] == 'dynamic-069-ja-nintendo-search':
            assert record['rendered_valid_record_counts'] == [10, 10, 10]
            assert record['minimum_records'] == 8
        else:
            assert record['case_id'] == followup['case_id']
            assert record['independent'] is True


def test_historical_scoring_controls_allow_only_new_tail_entries(tmp_path, monkeypatch):
    require_historical_inputs()
    dataset = _dataset()
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    registry = json.loads((ROOT / "evaluation/scoring-regressions.json").read_text())
    original_file = local_audit._repository_file
    replacement = tmp_path / "scoring-regressions.json"

    def repository_file(relative):
        return replacement if relative == "evaluation/scoring-regressions.json" else original_file(relative)

    monkeypatch.setattr(local_audit, "_repository_file", repository_file)
    changed = json.loads(json.dumps(registry))
    changed["cases"][0]["artifact"] += " altered"
    replacement.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="Scoring control contents changed"):
        local_audit._require_repository_bindings(dataset, receipt)

    changed = json.loads(json.dumps(registry))
    changed["cases"].insert(0, dict(changed["cases"][-1], id="interleaved-control"))
    replacement.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="Scoring control partition"):
        local_audit._require_repository_bindings(dataset, receipt)

    # Interleaving existing controls preserves both partition hashes, but
    # violates the historical ordered inventory just as inserting a new ID does.
    scoring = json.loads((ROOT / receipt['repository_evidence']['scoring_revision_path']).read_text())
    first_revision = scoring['revision_controls'][0]['id']
    changed = json.loads(json.dumps(registry))
    index = next(i for i, row in enumerate(changed['cases']) if row['id'] == first_revision)
    changed['cases'].insert(0, changed['cases'].pop(index))
    replacement.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="Scoring control partition"):
        local_audit._require_repository_bindings(dataset, receipt)


def test_dynamic_100_contracts_are_valid_and_do_not_configure_provider_waits():
    registered = json.loads((ROOT / "evaluation/native-retrieval-contracts.json").read_text())["contracts"]
    for case in _dataset()["cases"]:
        validate_acceptance_contract(case["acceptance_contract"])
        serialized = json.dumps(case, ensure_ascii=False).lower()
        assert "wait_for_selector" not in serialized
        assert "wait_after_load_ms" not in serialized
        assert "extraction_script" not in serialized
        hints = case.get("retrieval_hints")
        if hints and "contract" in hints:
            assert hints["contract"] in registered
            assert registered[hints["contract"]]["case_id"] == case["id"]


def test_native_moli_report_describes_the_frozen_uniform_call():
    record = {
        "provider_type": "moli_webfetch",
        "provider_metadata": {
            "dump": "markdown",
            "route": "dynamic_native_markdown",
            "wait_until": "done",
            "wait": {"strategy": "native_default_completion"},
        },
    }
    text = provider_configuration("moli-webfetch", [record] * 100, {})
    assert "默认 done" in text
    assert "除目标 URL 外，没有逐例等待元素" in text


def test_local_audit_banner_has_no_pull_request_number():
    report_source = (ROOT / "harness/web_fetch/report.py").read_text(encoding="utf-8")
    translations = (ROOT / "harness/web_fetch/report_i18n.py").read_text(encoding="utf-8")
    assert "PR #30" not in report_source
    assert "PR #30" not in translations


def test_local_audit_banner_depends_on_dataset_publication_state():
    assert local_audit_banner({}) == ""
    assert local_audit_banner({"comparison": {"publication_state": "release"}}) == ""
    assert "本地审核版" in local_audit_banner(_dataset())


def test_dynamic_100_repository_evidence_and_scoring_revision_are_exact():
    require_historical_inputs()
    dataset = _dataset()
    receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
    local_audit._require_repository_bindings(dataset, receipt)


def test_source_evidence_inventory_detects_omissions_extras_changes_and_symlinks(
        tmp_path, monkeypatch):
    root = tmp_path / "repo"
    source = root / "evaluation/sources/case-1/reference.txt"
    source.parent.mkdir(parents=True)
    source.write_text("frozen\n", encoding="utf-8")
    monkeypatch.setattr(local_audit, "ROOT", root)
    dataset = {"cases": [{"id": "case-1"}]}

    expected = local_audit._source_evidence_inventory(dataset)
    source.unlink()
    assert local_audit._source_evidence_inventory(dataset) != expected
    source.write_text("changed\n", encoding="utf-8")
    assert local_audit._source_evidence_inventory(dataset) != expected
    source.write_text("frozen\n", encoding="utf-8")
    (source.parent / "extra.txt").write_text("extra\n", encoding="utf-8")
    assert local_audit._source_evidence_inventory(dataset) != expected

    linked = root / "evaluation/sources/case-2"
    linked.symlink_to(source.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        local_audit._source_evidence_inventory({"cases": [{"id": "case-2"}]})
