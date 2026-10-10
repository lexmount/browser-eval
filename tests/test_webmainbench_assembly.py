"""Assemble independently certified cohorts without fixed provider counts or copied work trees."""

import copy
import hashlib
import json

import pytest

from harness.web_fetch.webmainbench_assembly import assemble
from harness.web_fetch.webmainbench_certification import verify_shard
from harness.web_fetch.webmainbench_evidence import (
    digest,
    read_jsonl,
    read_method_binding,
)
from harness.web_fetch.webmainbench_scoring import CORE_METRICS, aggregate, verify_score


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def freeze_cohort(root, profile_path, source, scorer, *, retry=False):
    root.mkdir()
    profile = json.loads(profile_path.read_text())
    records = read_jsonl(source)
    ids = {row["track_id"] for row in records}
    measured, scored = [], []
    for provider in profile["providers"]:
        shard = root / provider
        (shard / "artifacts" / provider).mkdir(parents=True)
        rows, calls = [], []
        for index, record in enumerate(records):
            case = record["track_id"]
            attempts = 2 if retry and index == 0 else 1
            body = f"{root.name}: {provider} {case}"
            relative = f"artifacts/{provider}/{case}.md"
            (shard / relative).write_text(body)
            row = {
                "provider": provider,
                "track_id": case,
                "ok": True,
                "attempt_id": f"{provider}__{case}__r001",
                "artifact_path": relative,
                "output_sha256": digest(shard / relative),
                "physical_attempt_count": attempts,
                "elapsed_ms": 20,
                "total_elapsed_ms": 20 + 10 * (attempts - 1),
            }
            rows.append(row)
            for physical in range(1, attempts + 1):
                physical_path = f"artifacts/{provider}/{case}-p{physical:02d}.txt"
                (shard / physical_path).write_text(
                    body if physical == attempts else "first call failed"
                )
                calls.append(
                    {
                        "provider": provider,
                        "track_id": case,
                        "attempt_id": f"{provider}__{case}__p{physical:02d}",
                        "physical_artifact_path": physical_path,
                        "output_sha256": digest(shard / physical_path),
                    }
                )
        write_rows(shard / "results.jsonl", rows)
        write_rows(shard / "physical-attempts.jsonl", calls)
        write_json(
            shard / "dataset.json",
            {"case_count": len(records), "case_ids": sorted(ids)},
        )
        ca_path = shard / "fixture-ca.pem"
        ca_path.write_text(root.name + "-ca")
        write_json(
            shard / "execution.json",
            {
                "provider": provider,
                "case_count": len(records),
                "source_dataset_sha256": digest(source),
                "implementation_identity": {
                    "binary_sha256": profile.get("binary_sha256")
                },
                "fixture_ca_artifact": "fixture-ca.pem",
                "network_isolation_probe": {"fixture_ca_sha256": digest(ca_path)},
            },
        )
        metrics = {name: {"score": 0.5, "success": True} for name in CORE_METRICS}
        scored_rows = [{**row, "metrics": copy.deepcopy(metrics)} for row in rows]
        write_rows(shard / "scored-results.jsonl", scored_rows)
        write_json(
            shard / "score-summary.json",
            {
                "provider": provider,
                "case_count": len(records),
                "official_aggregate": aggregate(scored_rows),
                "scorer": {
                    "commit": profile["official_scorer_commit"],
                    "metric_config": profile["metric_config"],
                    "metric_source_sha256": {"metric.py": digest(scorer / "metric.py")},
                },
            },
        )
        measured.append(verify_shard(shard, provider, ids, profile))
        scored.append(verify_score(shard, provider, ids, scorer, profile))
    source_binding = {"source_sha256": digest(source)}
    write_json(
        root / "cohort-certificate.json",
        {
            "kind": "certified_webmainbench_545_full_v1",
            "profile_sha256": digest(profile_path),
            "preflight": source_binding,
            "postflight": source_binding,
            "providers": measured,
            "binary_source_commit": profile.get("source_commit"),
            "measured_binary_sha256": profile.get("binary_sha256"),
        },
    )
    write_json(
        root / "scoring-certificate.json",
        {
            "kind": "certified_webmainbench_scoring_v1",
            "providers": scored,
            "cohort_certificate_sha256": digest(root / "cohort-certificate.json"),
            "source_binding": source_binding,
            "scorer_source_sha256": {"metric.py": digest(scorer / "metric.py")},
        },
    )
    (root / "private-review.json").write_text("never export this unbound file")
    (root / "worker-queue").mkdir()
    (root / "worker-queue/log.txt").write_text("internal diagnostics")


@pytest.fixture
def evidence(tmp_path):
    source = tmp_path / "source.jsonl"
    write_rows(
        source,
        [
            {
                "track_id": f"case-{i}",
                "html": f"<p>input {i}</p>",
                "groundtruth_content": f"input {i}",
                "url": f"https://example.invalid/{i}",
                "meta": {},
            }
            for i in range(545)
        ],
    )
    scorer = tmp_path / "scorer"
    scorer.mkdir()
    (scorer / "metric.py").write_text("# synthetic pinned scorer source\n")
    profile = {
        "dataset_id": "synthetic-full",
        "case_count": 545,
        "source_dataset_sha256": digest(source),
        "case_ids_sha256": hashlib.sha256(b"synthetic cases").hexdigest(),
        "providers": ["engine-a", "engine-b"],
        "official_scorer_commit": "synthetic",
        "metric_config": {"use_llm": False},
    }
    profile_path = tmp_path / "baseline.json"
    write_json(profile_path, profile)
    baseline = tmp_path / "baseline"
    freeze_cohort(baseline, profile_path, source, scorer)
    return tmp_path, baseline, source, scorer, profile_path


def variants(evidence, count):
    directory, _, source, scorer, baseline_profile = evidence
    profile = json.loads(baseline_profile.read_text())
    entries, cohorts = [], {}
    for index in range(count):
        provider = f"variant-{index}"
        profile_path = directory / f"{provider}.json"
        write_json(
            profile_path,
            {
                **profile,
                "providers": ["engine-a"],
                "binary_sha256": f"binary-{index}",
                "source_commit": f"commit-{index}",
            },
        )
        cohorts[provider] = directory / provider
        freeze_cohort(cohorts[provider], profile_path, source, scorer, retry=True)
        entries.append(
            {"provider": provider, "profile": profile_path.name, "label": provider}
        )
    registry = directory / "variants.json"
    write_json(registry, {"variants": entries})
    return registry, cohorts


@pytest.mark.parametrize("count", [0, 1, 3])
def test_complete_assembly_follows_registry_and_retains_all_physical_calls(
    evidence, count
):
    directory, baseline, source, scorer, profile = evidence
    registry, cohorts = variants(evidence, count) if count else (None, {})
    output = directory / "package"
    before = {
        path: digest(path)
        for root in (baseline, *cohorts.values())
        for path in root.rglob("*")
        if path.is_file()
    }
    assemble(
        baseline,
        output,
        source,
        profile,
        registry_path=registry,
        cohorts=cohorts,
        scorer=scorer,
    )
    rows, calls = (
        read_jsonl(output / "results.jsonl"),
        read_jsonl(output / "physical-attempts.jsonl"),
    )
    assert len(rows) == 545 * (2 + count)
    assert len(calls) == len(rows) + count
    assert {row["provider"] for row in rows} == {"engine-a", "engine-b", *cohorts}
    for row in rows:
        assert digest(output / row["artifact_path"]) == row["output_sha256"]
    for row in calls:
        assert digest(output / row["physical_artifact_path"]) == row["output_sha256"]
    assert (
        output / "fixture-certificates/engine-a-ca.pem"
    ).read_text() == "baseline-ca"
    assert not list(output.rglob("private-review.json"))
    assert not list(output.rglob("worker-queue"))
    assert not list(output.glob("six-method*"))
    assert all(digest(path) == expected for path, expected in before.items())
    if count:
        name, binding = read_method_binding(output)
        assert name == "method-binding.json"
        assert binding["logical_results"] == len(rows)
        assert binding["physical_calls"] == len(calls)
        assert binding["results_sha256"] == digest(output / "results.jsonl")
        assert [item["provider"] for item in binding["variants"]] == list(cohorts)
        for provider in cohorts:
            row = next(
                row
                for row in rows
                if row["provider"] == provider and row["case_id"] == "case-0"
            )
            assert row["elapsed_ms"] == 20 and row["total_elapsed_ms"] == 30
            assert (
                output / f"fixture-certificates/{provider}-ca.pem"
            ).read_text() == provider + "-ca"


@pytest.mark.parametrize(
    "damage",
    [
        "missing_cohort",
        "changed_output",
        "wrong_profile",
        "missing_call",
        "wrong_source",
        "wrong_scorer",
    ],
)
def test_invalid_evidence_cannot_leave_a_publishable_partial_package(evidence, damage):
    directory, baseline, source, scorer, profile = evidence
    registry, cohorts = variants(evidence, 1)
    candidate = cohorts["variant-0"]
    if damage == "missing_cohort":
        cohorts.clear()
    elif damage == "changed_output":
        (candidate / "engine-a/artifacts/engine-a/case-0.md").write_text("changed")
    elif damage == "wrong_profile":
        variant_profile = directory / "variant-0.json"
        value = json.loads(variant_profile.read_text())
        value["source_commit"] = "different"
        write_json(variant_profile, value)
    elif damage == "missing_call":
        path = candidate / "engine-a/physical-attempts.jsonl"
        write_rows(path, read_jsonl(path)[1:])
    elif damage == "wrong_source":
        source.write_text(source.read_text() + " ")
    else:
        (scorer / "metric.py").write_text("changed scorer")
    output = directory / "package"
    with pytest.raises(ValueError):
        assemble(
            baseline,
            output,
            source,
            profile,
            registry_path=registry,
            cohorts=cohorts,
            scorer=scorer,
        )
    assert not output.exists()
    assert not list(directory.glob(".webmainbench-assemble-*"))


@pytest.mark.parametrize(
    "name,version,kind",
    [
        ("method-binding.json", 2, "webmainbench_method_cohort_binding"),
        (
            "seven-method-binding.json",
            1,
            "webmainbench_seven_method_full_scope_binding",
        ),
    ],
)
def test_historical_and_current_binding_readers_preserve_frozen_bytes(
    tmp_path, name, version, kind
):
    path = tmp_path / name
    payload = {"schema_version": version, "kind": kind, "variants": []}
    write_json(path, payload)
    before = path.read_bytes()
    assert read_method_binding(tmp_path) == (name, payload)
    assert path.read_bytes() == before


def test_binding_reader_rejects_stale_ambiguous_or_unsupported_formats(tmp_path):
    with pytest.raises(ValueError, match="Exactly one"):
        read_method_binding(tmp_path)
    current = tmp_path / "method-binding.json"
    write_json(
        current, {"schema_version": 99, "kind": "webmainbench_method_cohort_binding"}
    )
    with pytest.raises(ValueError, match="Unsupported"):
        read_method_binding(tmp_path)
    write_json(tmp_path / "seven-method-binding.json", {})
    with pytest.raises(ValueError, match="Exactly one"):
        read_method_binding(tmp_path)


def test_input_changed_after_materialization_cannot_commit_package(
    evidence, monkeypatch
):
    from harness.web_fetch import webmainbench_assembly as assembly

    directory, baseline, source, scorer, profile = evidence
    original = assembly._assemble_cohort

    def collect_then_change(*args, **kwargs):
        original(*args, **kwargs)
        source.write_text(source.read_text() + " ")

    monkeypatch.setattr(assembly, "_assemble_cohort", collect_then_change)
    output = directory / "package"
    with pytest.raises(ValueError, match="changed during aggregation"):
        assemble(baseline, output, source, profile, scorer=scorer)
    assert not output.exists()
    assert not list(directory.glob(".webmainbench-assemble-*"))


def test_new_certified_variant_replaces_only_its_method_and_preserves_previous_package(
    evidence,
):
    directory, baseline, source, scorer, profile = evidence
    registry, cohorts = variants(evidence, 2)
    previous = directory / "previous"
    assemble(
        baseline,
        previous,
        source,
        profile,
        registry_path=registry,
        cohorts=cohorts,
        scorer=scorer,
    )
    frozen = {path: digest(path) for path in previous.rglob("*") if path.is_file()}
    previous_rows = read_jsonl(previous / "results.jsonl")
    replacement_profile = directory / "variant-0-v2.json"
    value = json.loads((directory / "variant-0.json").read_text())
    value.update(binary_sha256="replacement-binary", source_commit="replacement-commit")
    write_json(replacement_profile, value)
    replacement = directory / "replacement"
    freeze_cohort(replacement, replacement_profile, source, scorer, retry=True)
    plan = json.loads(registry.read_text())
    plan["variants"][0]["profile"] = replacement_profile.name
    replacement_registry = directory / "replacement-registry.json"
    write_json(replacement_registry, plan)
    output = directory / "replacement-package"
    assemble(
        baseline,
        output,
        source,
        profile,
        registry_path=replacement_registry,
        cohorts={**cohorts, "variant-0": replacement},
        scorer=scorer,
    )
    rows = read_jsonl(output / "results.jsonl")
    assert [row for row in rows if row["provider"] != "variant-0"] == [
        row for row in previous_rows if row["provider"] != "variant-0"
    ]
    assert (
        next(row for row in rows if row["provider"] == "variant-0")["output_sha256"]
        != next(row for row in previous_rows if row["provider"] == "variant-0")[
            "output_sha256"
        ]
    )
    assert all(digest(path) == expected for path, expected in frozen.items())


def test_correction_fixture_ca_is_bound_and_cannot_change_after_copy(evidence, monkeypatch):
    from harness.web_fetch import webmainbench_assembly as assembly

    directory, baseline, source, scorer, profile = evidence
    shard = baseline / 'engine-a'
    ca = shard / 'correction-fixture-ca.pem'
    ca.write_text('frozen correction CA')
    execution = json.loads((shard / 'execution.json').read_bytes())
    execution['correction_runs'] = [{'fixture_ca_sha256': digest(ca)}]
    write_json(shard / 'execution.json', execution)
    certificate = json.loads((baseline / 'cohort-certificate.json').read_bytes())
    certificate['providers'][0] = verify_shard(shard, 'engine-a',
                                              {row['track_id'] for row in read_jsonl(source)},
                                              json.loads(profile.read_bytes()))
    write_json(baseline / 'cohort-certificate.json', certificate)
    scored = json.loads((baseline / 'scoring-certificate.json').read_bytes())
    scored['cohort_certificate_sha256'] = digest(baseline / 'cohort-certificate.json')
    write_json(baseline / 'scoring-certificate.json', scored)
    output = directory / 'complete-correction-package'
    assemble(baseline, output, source, profile, scorer=scorer)
    assert (output / 'fixture-certificates/engine-a-correction-ca.pem').read_bytes() == ca.read_bytes()
    original = assembly._assemble_cohort

    def collect_then_change(*args, **kwargs):
        original(*args, **kwargs)
        ca.write_text('changed after copy')

    monkeypatch.setattr(assembly, '_assemble_cohort', collect_then_change)
    refused = directory / 'changed-correction-package'
    with pytest.raises(ValueError, match='changed during aggregation'):
        assemble(baseline, refused, source, profile, scorer=scorer)
    assert not refused.exists()
